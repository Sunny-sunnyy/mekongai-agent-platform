import fitz
import pytesseract
from PIL import Image
import cv2
import numpy as np
import re
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pdf2image import convert_from_path
import logging
import multiprocessing
import os

# Import tương đối: chép riêng thư mục pdf/ sang dự án khác vẫn chạy (README_pdf.md, "Dùng Loader ở dự án khác")
from . import docling_order, page_vision
from .figure_caption import add_figure_caption_pairs
from . import chart_crop, scan_conflicts

# Thứ tự đọc trang có chữ sẵn theo khối bố cục của pymupdf-layout: chấm mù 19 trang tiếng Việt thắng thứ tự cũ 11–1; bảng nằm đúng khối thay vì dồn
# cuối trang (CHANGELOG: CH-20261016-A18). Tắt: PDF_LAYOUT_ORDER=false.
# Import pymupdf.layout làm find_tables() mặc định chỉ trả bảng ở vùng layout gọi là "table" (6 trang có bảng: 11 → 4) ⇒ mọi chỗ gọi dùng use_layout=False.
_layout_module = None
# Mô hình bố cục (ONNX) mặc định dùng MỌI nhân cho mỗi phiên, mà lượt 1 chạy tới 6 tiến trình con ⇒ tranh CPU. 1 luồng / phiên: luận án 208 trang
# 60,9 ⇒ 22,4 s, chữ ra giống hệt
LAYOUT_ONNX_THREADS = 1
# Trong một khối: phần tử có đáy lệch ≤ 3 pt so với phần tử đầu hàng là cùng hàng
LAYOUT_ROW_TOLERANCE_PT = 3.0


def _import_layout_module():
    """Nạp pymupdf.layout với phiên ONNX giới hạn LAYOUT_ONNX_THREADS luồng. Thư viện tạo cả 3 phiên ngay lúc import
    (kiểm 16/10) ⇒ chỉ bọc onnxruntime.InferenceSession trong lúc import rồi trả lại, chỗ khác dùng ONNX không bị ảnh hưởng."""
    import onnxruntime as ort
    original = ort.InferenceSession

    def limited_session(path, sess_options=None, **kwargs):
        options = sess_options or ort.SessionOptions()
        options.intra_op_num_threads = LAYOUT_ONNX_THREADS
        options.inter_op_num_threads = 1
        return original(path, sess_options=options, **kwargs)

    ort.InferenceSession = limited_session
    try:
        import pymupdf.layout as layout_module
    finally:
        ort.InferenceSession = original
    return layout_module


def layout_order_enabled():
    return os.getenv("PDF_LAYOUT_ORDER", "true").strip().lower() not in ("0", "false", "no", "off")


def is_large_page(page):
    """Trang khổ lớn (≥ LARGE_PAGE_MIN_A4_AREAS lần A4: bản đồ, bản vẽ A2–A0). Mô hình bố cục chạy cực chậm theo khổ trang (bản đồ A0: 442 s / trang,
    trang A4 nhiều nét chỉ 0,5–1,4 s) ⇒ không chạy bố cục; trang được gửi luna như trang hình (CHANGELOG: CH-20261017-A39)."""
    return abs(page.rect) >= LARGE_PAGE_MIN_A4_AREAS * A4_AREA_PT


def layout_blocks(page):
    """Khối bố cục của trang theo thứ tự đọc [(Rect, loại khối)]; [] khi tắt, thiếu thư viện hoặc lỗi (giữ thứ tự cũ).
    Nạp model ONNX lần đầu gọi trong mỗi tiến trình (~1 s), không nạp lúc import để tiến trình không có trang chữ sẵn khỏi tốn RAM."""
    global _layout_module
    if not layout_order_enabled() or _layout_module is False or is_large_page(page):
        return []
    try:
        if _layout_module is None:
            _layout_module = _import_layout_module()
    except ImportError as e:
        logger.warning(f"pymupdf-layout not available, keeping native reading order: {e}")
        _layout_module = False
        return []
    try:
        page.get_layout()
        return [(fitz.Rect(b[:4]), b[4]) for b in page.layout_information or []]
    except Exception as e:
        logger.warning(f"Layout analysis failed on page {page.number + 1}: {e}")
        return []


# Tesseract trong image build với OpenMP: mỗi tiến trình tự mở nhiều luồng; OCR_WORKERS tiến trình song song sẽ dùng
# vượt xa số nhân (góp phần làm máy treo 14/10). Tiến trình con pytesseract kế thừa biến này.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

# Cấu hình logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Quyết định cách trích xuất theo TỪNG TRANG từ tín hiệu rẻ của chính trang (khung "PDF - 5 bước", bước 1–2).
# Ngưỡng đặt từ tín hiệu L1 trên 16 PDF (14/10): xem tài liệu kỹ thuật
SPARSE_PAGE_MAX_CHARS = 100      # ít ký tự native hơn mức này: text layer không đủ dùng
VECTOR_TEXT_MIN_DRAWINGS = 50    # trang ít chữ, không ảnh nhưng nhiều đoạn path: chữ vẽ bằng vector (menu, bìa hướng dẫn sử dụng)
# Trang NHIỀU chữ thật vẫn có thể có dòng chữ vẽ bằng nét (catalog / menu thiết kế xuất chữ thành đường viền glyph): quét 3.603 trang ⇒ ~37 trang mất chữ thật
# (thông số, bảo hành, "chưa gồm VAT", lưu ý dị ứng) vì trang là lớp native, không được gửi luna. Một "dòng chữ nét" = path tô kín ≥ 30 đoạn, cao ≤ 60 pt,
# dài ≥ 2 × cao (biểu tượng gần vuông bị loại), không đè chữ của lớp chữ (CHANGELOG: CH-20261020-A5).
VECTOR_TEXT_LINE_MIN_ITEMS = 30
VECTOR_TEXT_LINE_MAX_HEIGHT = 60
# Biểu đồ VẼ BẰNG NÉT mà bố cục không ra khối "picture" (báo cáo xuất từ Excel / PowerPoint, lưới 2×2 / 2×3): trang chữ sẵn, ảnh < 3 % ⇒ trước đây không gửi
# luna ⇒ không đọc số, không cắt ảnh (6 / 21 trang biểu đồ thử). Một cụm nét `cluster_drawings` là biểu đồ khi: ≥ 1 % trang, ≥ 4 nhãn số quanh cụm, ≥ 8 nét,
# và có dáng biểu đồ (≥ 2 nét cong / xiên, hoặc 1 đường gấp khúc ≥ 6 đoạn xiên, hoặc ≥ 3 cột tô màu ≥ 3 pt chung một mép, dài ngắn khác nhau ≥ 1,5 ×).
# Bỏ trước: nét vô hình (Word tô trắng sau mỗi dòng chữ) và nét trong bảng thật. Đo: 6 / 6 trang bị bỏ được bắt; kho 3.951 trang khác chỉ thêm 3 trang luna;
# ~0,07 s / trang. Ngưỡng chọn sau khi thấy số ⇒ đã kiểm trên PDF mới (CHANGELOG: CH-20261020-A8).
VECTOR_CHART_MIN_AREA = 0.01
VECTOR_CHART_MIN_NUMBERS = 4
VECTOR_CHART_MIN_PATHS = 8
VECTOR_CHART_NUMBER_MARGIN = 0.03    # nhãn trục nằm sát ngoài vùng vẽ: mở rộng cụm 3 % trang
VECTOR_CHART_IN_TABLE = 0.8          # nét nằm ≥ 80 % trong bảng thật ⇒ là kẻ bảng
VECTOR_CHART_MIN_CURVED = 2
VECTOR_CHART_MIN_POLYLINE = 6
VECTOR_CHART_MIN_BAR_SIDE = 3.0      # đường kẻ bảng vẽ bằng hình chữ nhật đen mảnh không phải cột
VECTOR_CHART_BAR_ALIGN = 1.5
VECTOR_CHART_BAR_VARY = 1.5
VECTOR_CHART_CLUSTER_TOLERANCE = 3   # pt: nét cách nhau ≤ 3 pt gom chung một cụm (một biểu đồ)
# số, %, số âm / trong ngoặc, nhãn quý / năm: "12,5%", "(82)", "-2,5%", "2.000", "1Q26", "Q1/2019", "4/22"
CHART_NUMBER_LABEL = re.compile(r"^[-+(]?\d[\d.,]*%?\)?$|^\d?Q\d([/'-]?\d{2,4})?$|^\dQ\d{2}$|^\d{1,2}/\d{2,4}$")
HIDDEN_LAYER_MIN_RATIO = 0.5     # tỉ lệ ký tự vô hình (texttrace type 3): lớp OCR ẩn của bản scan
# Lớp OCR ẩn đủ dấu thì giữ, không OCR lại — Tesseract đọc lại một lớp tốt làm kém đi (số hiệu, %). Tỉ lệ chữ cái có dấu: lớp mất dấu ≤ 0,122,
# lớp tốt ≥ 0,240, chữ native tiếng Việt 0,23–0,30. Trang ít chữ cái thì tỉ lệ không tin được ⇒ vẫn OCR lại (CHANGELOG: CH-20261015-A10).
GOOD_HIDDEN_LAYER_MIN_DIACRITIC_RATIO = 0.18
GOOD_HIDDEN_LAYER_MIN_LETTERS = 200
VIETNAMESE_DIACRITIC_LETTERS = set("àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ")
# Ký tự trông giống chữ Việt mà khác mã, làm tìm từ khoá / embedding không khớp: font một số PDF Word ánh xạ "ư"/"Ư" sang "ƣ"/"Ƣ" (U+01A3/U+01A2 — một
# luận án 4.518 lần) (CHANGELOG: CH-20261015-A12); "Ð/ð" (U+00D0/U+00F0, chữ Eth) thay "Đ/đ" — tìm "ĐÈN" không ra "ÐÈN" (CHANGELOG: CH-20261015-A16)
# Một bảng cặp ký tự cho mọi chỗ sửa: dict cho ô bảng (DataFrame.replace — 16/10: trước chỉ có "ƣ", ô bảng còn "Ð"), bản maketrans cho str.translate
TABLE_CHAR_FIXES = {chr(0x00D0): "Đ", chr(0x00F0): "đ", "\u01a3": "ư", "\u01a2": "Ư"}
VIETNAMESE_CHAR_FIXES = str.maketrans(TABLE_CHAR_FIXES)
# Lớp chữ có sẵn nhưng hỏng (lớp OCR cũ chất lượng kém hiển thị như chữ thường: "Chiéc", "n6'i tié'ng") — trước đây Loader tin là native.
# Tín hiệu: trong các từ có dấu, tỉ lệ từ KHÔNG phải âm tiết tiếng Việt hợp lệ (phụ âm đầu + vần theo chính tả). Khảo sát 2.223 trang: file hỏng 0,37–1,00;
# mọi trang khác ≤ 0,105, cao nhất 0,21 (từ tiếng Pháp) (CHANGELOG: CH-20261015-A16).
GARBLED_MIN_MARKED_WORDS = 10
GARBLED_MIN_INVALID_RATIO = 0.3
# Lớp chữ lỗi mã font MỘT PHẦN — font ánh xạ vài chữ Việt sang chữ Kirin ("đҥi" = đại, "dөc" = dọc; 1–30 từ / trang, dưới ngưỡng tỉ lệ ở trên). Chữ Kirin
# chen GIỮA từ Latin thì chữ in thật không có (~140 file thử: chỉ 1 file); chữ Hy Lạp trong ký hiệu ("µm", "cosα") là chữ thật — không tính (CHANGELOG: CH-20261017-A33)
GARBLED_MIN_MIXED_SCRIPT_WORDS = 1
# Trang khổ lớn = ≥ 4 lần diện tích A4 (595 × 842 pt): bản đồ, bản vẽ A0–A2 (CHANGELOG: CH-20261017-A39)
A4_AREA_PT = 595 * 842
LARGE_PAGE_MIN_A4_AREAS = 4
# Lớp chữ MẤT ký tự — font không nhúng, mã WinAnsi: chữ Việt ngoài bảng mã thành khoảng trắng ("Đườ   Đỗ N ư" = "Đường Đỗ Như") ⇒ nhiều mẩu chữ chỉ 1 chữ cái.
# ~3.500 trang native: file lỗi trung vị 0,52; trang khác p99,9 0,29 — 3 trang ≥ 0,3 đều là trang toán (biến x, y), đã gửi luna vì có công thức (CHANGELOG: CH-20261017-A38)
LOST_GLYPH_MIN_LONE_RATIO = 0.3
LOST_GLYPH_MIN_TOKENS = 30
# Trang có công thức ⇒ luna: font toán của LaTeX / Word Equation / MathType, ký hiệu toán Unicode (has_math_formula) (CHANGELOG: CH-20261017-A37)
MATH_FONT_PATTERN = re.compile(r"CMMI|CMSY|CMEX|MSAM|MSBM|Cambria ?Math|STIX|Fourier-Math|Euclid|MT ?Extra|MathType|MTSY|MTMI|LatinModernMath|XITS|Asana|(?:^|[+,-])Symbol", re.I)
MATH_SYMBOLS = set("∑∫∮√∏∂∇")
BULLET_CHARS = set("\uf0b7\uf0a7\uf0d8\uf076\uf0fc\uf0a8\uf06e\uf0e0\uf0de\uf0f0\uf0d7\uf071•·◦▪■□●○➢➤✓✔")
FORMULA_MIN_MATH_FONT_CHARS = 3
VIETNAMESE_ONSETS = ("ngh", "ng", "nh", "ch", "gh", "gi", "kh", "ph", "qu", "th", "tr",
                     "b", "c", "d", "đ", "g", "h", "k", "l", "m", "n", "p", "q", "r", "s", "t", "v", "x", "")
# "q" riêng: "quỳnh" = "q" + vần "uynh", "quýt" = "q" + "uyt" (thiếu thì tên "Quỳnh" bị tính sai)
VIETNAMESE_RHYMES = frozenset("""a ac ach ai am an ang anh ao ap at au ay ă ăc ăm ăn ăng ăp ăt â âc âm ân âng âp ât âu ây
e ec em en eng eo ep et ê êch êm ên ênh êp êt êu i ia ich iêc iêm iên iêng iêp iêt iêu im in inh ip it iu
o oa oac oach oai oam oan oang oanh oao oap oat oay oăc oăm oăn oăng oăt oc oe oen oeo oet oi om on ong oo ooc oong op ot
ô ôc ôi ôm ôn ông ôp ôt ơ ơi ơm ơn ơp ơt u ua uân uâng uât uây uc uê uêch uênh uơ ui um un ung uôc uôi uôm uôn uông uôt
up ut uy uya uych uyên uyêt uyn uynh uyp uyt uyu ư ưa ưc ưi ưng ươc ươi ươm ươn ương ươp ươt ươu ưt ưu y yêm yên yêt yêu""".split())
_TONE_MARKS = {chr(0x0300), chr(0x0301), chr(0x0303), chr(0x0309), chr(0x0323)}  # dấu huyền, sắc, ngã, hỏi, nặng (dạng NFD)
# find_tables nhận nhầm văn xuôi / biểu đồ là bảng (Word kẻ một đoạn path dưới mỗi dòng chữ) ⇒ bảng giả làm dời chỗ, lặp chữ. Đo 692 bảng / 38 PDF:
# bảng giả văn xuôi < 0,5 hàng có ≥ 2 ô có chữ và ~1 đoạn kẻ / hàng; bảng thật thưa ô vẫn nhiều đường kẻ (5–14 đoạn / hàng). Ô cắt ngang từ ("H | ọ và tên",
# "3 | 000K") cũng là bảng giả (CHANGELOG: CH-20261015-A13).
FALSE_TABLE_MAX_MULTI_CELL_ROWS = 0.5
FALSE_TABLE_MAX_SEGMENTS_PER_ROW = 2
FALSE_TABLE_MIN_CUT_WORD_RATIO = 0.3
# "Bảng" gần như toàn ô trống: hộp slide kẻ viền, lưới biểu đồ, sơ đồ khối, khung bìa. 997 bảng / ~90 file: 43 bảng ≤ 5 ô có chữ và ≥ 70 % ô trống, không
# bảng nào mang cấu trúc hàng – cột có ích; bảng thật thưa ô vẫn nhiều ô chữ (bảng đơn giá: 46–101 ô, 84–90 % trống) (CHANGELOG: CH-20261017-A32)
FALSE_TABLE_MAX_FILLED_CELLS = 5
FALSE_TABLE_MIN_EMPTY_RATIO = 0.7
BAD_CHAR_MAX_RATIO = 0.1         # tỉ lệ U+FFFD: chữ native lỗi mã hóa
SPACE_GAP_RATIO = 0.2           # hở giữa 2 ký tự > 0,2 × cỡ chữ thì là dấu cách (thử 0,1–0,25 cho cùng kết quả) (CHANGELOG: CH-20261014-A6)
OCR_DPI = 300
# Trang scan bị xoay: chữ OCR có dưới 0,3 âm tiết tiếng Việt hợp lệ trên mỗi mẩu chữ (tách theo khoảng trắng, kể cả mẩu rác) ⇒ nghi trang xoay, chạy Tesseract
# dò hướng. Đo: trang xoay 0,05–0,23; trang thẳng nhiều chữ ≥ 0,44. Độ tin cậy hướng của Tesseract KHÔNG dùng làm ngưỡng được (lần sai tới 7,4, lần đúng thấp
# nhất 0,4) ⇒ nhận ảnh đã xoay chỉ khi số âm tiết hợp lệ ≥ 1,5 lần ảnh gốc và ≥ 20 (26 / 26 xoay đúng, 0 xoay sai). Dò hướng trên ảnh ~150 dpi
# (CHANGELOG: CH-20261016-A20)
OCR_PSM3_MIN_WORDS = 25         # psm 3 ra ít hơn số từ này ⇒ đọc lại psm 6 (bảng in ngang psm 3 từng ra rỗng) (CHANGELOG: CH-20261016-A24)
OCR_ROTATION_CHECK_RATIO = 0.3
ROTATED_MIN_GAIN = 1.5
ROTATED_MIN_SYLLABLES = 20
ROTATED_MIN_NUMBERS = 20        # bảng toàn số: bản xoay cần ≥ 20 chuỗi ≥ 3 chữ số (cùng mức "tốt hơn rõ" như âm tiết) (CHANGELOG: CH-20261017-A25)
# Trang nằm ngang ⇒ đa số hộp từ (≥ 3 ký tự) cao > 1,5 × rộng; trang < 15 từ thì tỉ lệ không tin được (đo 126 trang) (CHANGELOG: CH-20261017-A26)
SIDEWAYS_TALL_FACTOR = 1.5
SIDEWAYS_MIN_TALL_RATIO = 0.5
SIDEWAYS_MIN_WORDS = 15
SIDEWAYS_MIN_KEEP = 0.5
# Trang scan luna đã đọc mà chữ OCR trông như rác (dưới OCR_ROTATION_CHECK_RATIO âm tiết) ⇒ bỏ từng DÒNG OCR có dưới 60 % mẩu "trông như từ" (word_like_ratio),
# giữ dòng còn lại (bỏ trọn chữ máy thì mất số thật luna không chép: hotline, giá trên trang catalog). Chữ đã lưu 22/10: rác trang ảnh −84 % (36 trang) / −95 %
# (12 trang); 2 / 23 trang scan văn bản bị xét, dòng bỏ chỉ là số máy đọc sai của hàng luna chép đủ. Ngưỡng chọn SAU KHI xem mẫu (50 %: −76 %) (CHANGELOG: CH-20261023-B2)
OCR_JUNK_LINE_MIN_WORD_LIKE = 0.6
# Số worker = một nửa số luồng CPU, tối đa 6 (User: không dùng hết máy). 150 trang scan, 12 luồng: 1 / 2 / 4 / 6 worker = 219 / 128 / 77 / 60 s;
# Tesseract chiếm ~88 % thời gian mỗi trang (CHANGELOG: CH-20261015-A11)
PAGE_WORKERS = max(1, min(6, (os.cpu_count() or 2) // 2))
OCR_WORKERS = PAGE_WORKERS
# Lượt 1 (chữ native + bảng) của file lớn chia batch trang cho nhiều TIẾN TRÌNH (PyMuPDF không dùng chung được giữa các luồng).
# Đo: catalog 80 trang 46,8 ⇒ 20,0 s, luận án 208 trang 24,2 ⇒ 9,4 s (4 tiến trình), kết quả giống hệt từng trang (CHANGELOG: CH-20261015-A11).
# File nhỏ chạy trong tiến trình hiện tại: khởi động tiến trình con tốn hơn phần tiết kiệm.
FIRST_PASS_BATCH_PAGES = 20
PARALLEL_FIRST_PASS_MIN_PAGES = 40

_WORD = re.compile(r"\w+", re.UNICODE)
_DIGIT_RUN = re.compile(r"\d{3,}")


def gray_image_for_ocr(image):
    """Ảnh xám cho Tesseract. Bỏ nhị phân Otsu + lọc median cũ — Tesseract tự nhị phân hóa bên trong; 16 trang có đáp án: số đúng 70 ⇒ 84 %,
    dấu đúng 89 ⇒ 93 % (CHANGELOG: CH-20261016-A24)."""
    img_array = np.array(image)
    if img_array.ndim == 3:
        return Image.fromarray(cv2.cvtColor(img_array[..., :3], cv2.COLOR_RGB2GRAY))
    return image


def to_tesseract_lang(language):
    """Đổi mã ngôn ngữ của API ('vie+eng', 'vi', 'en'...) sang mã Tesseract.
    Tiếng Việt dùng traineddata 'vie' (gồm cả chữ Latin); không rơi về 'eng' làm mất dấu."""
    lang = (language or '').lower()
    if 'vie' in lang or lang.startswith('vi'):
        return 'vie'
    if 'chi' in lang or lang.startswith('zh'):
        return 'chi_sim'
    return 'eng'


class PageText:
    """Phần tử chữ của một trang, cùng dạng phần tử unstructured mà node_structured đọc (.text, .metadata.page_number)."""

    def __init__(self, text, page_num):
        self.text = text
        self.metadata = SimpleNamespace(page_number=page_num)


def _words(text):
    return _WORD.findall(unicodedata.normalize("NFC", text or "").lower())


def page_signals(page):
    """Tín hiệu L1 của một trang: chỉ đọc cấu trúc PDF, không render."""
    text = page.get_text()
    chars = len(text.strip())
    invisible = 0
    if chars:
        invisible = sum(len(span["chars"]) for span in page.get_texttrace() if span.get("type") == 3)
    area = abs(page.rect) or 1.0
    images = [fitz.Rect(info["bbox"]) & page.rect for info in page.get_image_info()]
    images = [rect for rect in images if not rect.is_empty]
    image_cover = min(1.0, sum(abs(rect) for rect in images) / area)
    # get_drawings đắt ở trang nhiều path: chỉ cần khi trang ít chữ và không có ảnh.
    # Đếm ĐOẠN path (items), không đếm drawing: MuPDF gom path cùng kiểu thành một drawing (bìa chữ vector: 40 drawing
    # nhưng 4.282 đoạn; trang chỉ có đường kẻ: 3–5 đoạn)
    drawings = sum(len(d["items"]) for d in page.get_drawings()) if chars < SPARSE_PAGE_MAX_CHARS and not images else None
    return {
        "chars": chars,
        "invisible_ratio": invisible / chars if chars else 0.0,
        "bad_char_ratio": text.count("�") / chars if chars else 0.0,
        "image_rects": images,
        "image_cover": image_cover,
        "drawings": drawings,
    }


def classify_page(signals):
    """Lớp trang -> phương pháp: 'empty' | 'hidden_ocr' | 'ocr' | 'native'."""
    chars = signals["chars"]
    if chars == 0 and not signals["image_rects"] and not signals["drawings"]:
        return "empty"
    if chars and signals["invisible_ratio"] >= HIDDEN_LAYER_MIN_RATIO:
        # Lớp ẩn mất dấu (tỉ lệ chữ có dấu 7–10 % so với 25–29 % của Tesseract) ⇒ OCR lại;
        # lớp đủ dấu thì extract_pages giữ nguyên (is_good_hidden_layer)
        return "hidden_ocr"
    if chars < SPARSE_PAGE_MAX_CHARS and (signals["image_rects"] or (signals["drawings"] or 0) >= VECTOR_TEXT_MIN_DRAWINGS):
        return "ocr"
    if signals["bad_char_ratio"] > BAD_CHAR_MAX_RATIO:
        return "ocr"
    return "native"


def native_text(page, skip_boxes=(), cell_words=None):
    """Chữ native của trang (các dòng của native_lines ghép bằng xuống dòng)."""
    return "\n".join(text for _, text in native_lines(page, skip_boxes, cell_words))


def native_lines(page, skip_boxes=(), cell_words=None):
    """Các dòng chữ native của trang [(Rect dòng, chữ)], ghép từ toạ độ từng ký tự. Dòng có tâm trong skip_boxes (vùng bảng) chỉ bị bỏ khi MỌI từ của
    dòng còn trong ô bảng (cell_words: Counter, tính cả số lần, trừ dần): find_tables nhận nhầm biểu đồ/sơ đồ là bảng
    mà ô không đủ chữ (từng mất số trục, năm, nhãn) (CHANGELOG: CH-20261014-A7).
    get_text() của MuPDF bỏ mất dấu cách sau chữ có dấu ở PDF LaTeX tiếng Việt ("ngữcảnh"; độ chính xác từ 0,97 ⇒ 0,77) dù khoảng hở bằng một dấu cách
    ⇒ tự chèn dấu cách khi hở > SPACE_GAP_RATIO × cỡ chữ (CHANGELOG: CH-20261014-A6). Không dùng sort=True: mất khoảng trắng, và thứ tự mặc định
    đọc hết cột này mới sang cột kia. Lấy cả chữ bị clip (bỏ cờ TEXT_MEDIABOX_CLIP, cũng là cờ clip path): chữ tràn ô sơ đồ Word là nội dung thật;
    đổi lại nhãn trục có bản bị clip thì lặp số — người vận hành: thà lặp còn hơn mất chữ (CHANGELOG: CH-20261014-A8)."""
    lines, seen = [], set()
    flags = fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_MEDIABOX_CLIP
    for block in page.get_text("rawdict", flags=flags)["blocks"]:
        for line in block.get("lines", []):
            bbox = fitz.Rect(line["bbox"])
            center = fitz.Point((bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2)
            in_table = any(center in box for box in skip_boxes)
            out, prev_x1, prev_c = [], None, " "
            for span in line["spans"]:
                for ch in span["chars"]:
                    # Font không có bảng ToUnicode cho dấu cách: MuPDF trả ký tự điều khiển (PDF in bằng "Microsoft Print to PDF": "\x03"
                    # giữa mọi từ; pdftotext đổi thành dấu cách) ⇒ coi là dấu cách (CHANGELOG: CH-20261014-A8)
                    c = " " if ch["c"] < " " and ch["c"] not in "\t\n\r" else ch["c"]
                    if prev_x1 is not None and c != " " and prev_c != " " \
                            and ch["bbox"][0] - prev_x1 > SPACE_GAP_RATIO * span["size"]:
                        out.append(" ")
                    out.append(c)
                    prev_x1, prev_c = ch["bbox"][2], c
            text = "".join(out).translate(VIETNAMESE_CHAR_FIXES)
            # Cùng chữ vẽ chồng đúng một chỗ (catalog xuất từ Illustrator vẽ mỗi dòng 3 lần cùng toạ độ) chỉ lấy một lần (CHANGELOG: CH-20261015-A10)
            key = (text, tuple(round(v, 1) for v in line["bbox"]))
            if key in seen:
                continue
            seen.add(key)
            if in_table and cell_words is not None:
                line_words = Counter(_words(text))
                if not line_words - cell_words:
                    cell_words.subtract(line_words)
                    continue
            lines.append((bbox, text))
    return lines


def is_false_table(page, table, rows, page_words):
    """Bảng find_tables tìm được nhưng không phải bảng. Bỏ bảng thì chữ vùng đó ở lại phần chữ thường, đúng thứ tự trang.
    - Ô cắt ngang từ: nhiều từ trong ô không có nguyên vẹn trong chữ của trang ("H | ọ và tên", "3 | 000K").
    - Mỗi hàng chỉ một ô có chữ và hầu như không có đường kẻ: văn xuôi / biểu đồ (quốc hiệu, trang hai cột, luận án) (CHANGELOG: CH-20261015-A13).
    - Gần như toàn ô trống: ≤ 5 ô có chữ và ≥ 70 % ô trống — hộp slide, lưới biểu đồ, sơ đồ khối (CHANGELOG: CH-20261017-A32)."""
    filled = [str(c) for row in rows for c in row if c and str(c).strip()]
    cells = sum(len(row) for row in rows)
    if len(filled) <= FALSE_TABLE_MAX_FILLED_CELLS and cells and 1 - len(filled) / cells >= FALSE_TABLE_MIN_EMPTY_RATIO:
        return True
    cell_words = [w for c in filled for w in _words(c)]
    if cell_words and sum(w not in page_words for w in cell_words) >= FALSE_TABLE_MIN_CUT_WORD_RATIO * len(cell_words):
        return True
    multi_cell_rows = sum(sum(1 for c in row if c and str(c).strip()) >= 2 for row in rows)
    if multi_cell_rows >= FALSE_TABLE_MAX_MULTI_CELL_ROWS * len(rows):
        return False
    # get_drawings đắt ở trang nhiều path: chỉ đếm khi bảng đã "mỗi hàng một ô"
    return path_segments_in_box(page, fitz.Rect(table.bbox)) <= FALSE_TABLE_MAX_SEGMENTS_PER_ROW * len(rows)


def path_segments_in_box(page, box):
    """Số đoạn thẳng / hình chữ nhật của path chạm vùng box (đường kẻ bảng; Word vẽ viền ô bằng hình chữ nhật mảnh)."""
    count = 0
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                rect = fitz.Rect(item[1], item[2])
            elif item[0] == "re":
                rect = fitz.Rect(item[1])
            else:
                continue
            # Đường ngang/dọc có diện tích 0: nới 1 pt để intersects() thấy
            if (rect.normalize() + (-1, -1, 1, 1)).intersects(box):
                count += 1
    return count


def vector_text_lines(page):
    """Khung [x0, y0, x1, y1] (làm tròn) các "dòng chữ vẽ bằng nét" của trang có chữ sẵn (xem VECTOR_TEXT_LINE_*). page_vision dùng để gửi trang cho luna đọc phần chữ này;
    khung lặp ở nhiều trang (logo, chân trang thiết kế) được bỏ ở đó. get_cdrawings (bản C) rẻ: ~400 trang thường 3 s."""
    words = [fitz.Rect(w[:4]) for w in page.get_text("words")]
    lines = []
    for d in page.get_cdrawings():
        if d.get("fill") is None or len(d.get("items", [])) < VECTOR_TEXT_LINE_MIN_ITEMS:
            continue
        rect = fitz.Rect(d["rect"])
        if not (0 < rect.height <= VECTOR_TEXT_LINE_MAX_HEIGHT and rect.width >= 2 * rect.height) or any(rect.intersects(w) for w in words):
            continue
        lines.append([round(v) for v in rect])
    return lines


def _is_white(rgb):
    return rgb is not None and all(v >= 0.95 for v in rgb)


def _slanted_segments(drawing):
    return sum(1 for item in drawing["items"] if item[0] == "l" and abs(item[1].x - item[2].x) > 1 and abs(item[1].y - item[2].y) > 1)


def _has_bars(rects):
    """≥ 3 hình chữ nhật chung một mép thẳng hàng mà chiều kia khác nhau rõ (cột dọc / thanh ngang, kể cả cột âm)."""
    for edge, size in ((lambda r: r.y1, lambda r: r.height), (lambda r: r.y0, lambda r: r.height),
                       (lambda r: r.x0, lambda r: r.width), (lambda r: r.x1, lambda r: r.width)):
        for anchor in rects:
            sizes = [size(r) for r in rects if abs(edge(r) - edge(anchor)) <= VECTOR_CHART_BAR_ALIGN]
            if len(sizes) >= 3 and max(sizes) >= VECTOR_CHART_BAR_VARY * min(sizes):
                return True
    return False


def _chart_shape(drawings):
    curved = sum(1 for d in drawings if any(item[0] == "c" for item in d["items"]) or _slanted_segments(d))
    if curved >= VECTOR_CHART_MIN_CURVED or any(_slanted_segments(d) >= VECTOR_CHART_MIN_POLYLINE for d in drawings):
        return True
    bars = [d["rect"] for d in drawings if d.get("fill") is not None and not _is_white(d["fill"])
            and d["rect"].width >= VECTOR_CHART_MIN_BAR_SIDE and d["rect"].height >= VECTOR_CHART_MIN_BAR_SIDE]
    return _has_bars(bars)


def has_vector_chart(page):
    """Trang chữ sẵn có biểu đồ vẽ bằng nét (xem VECTOR_CHART_*) ⇒ page_vision gửi luna như trang hình. Chỉ gọi khi bố cục không ra khối "picture"."""
    numbers = [fitz.Rect(w[:4]) for w in page.get_text("words") if CHART_NUMBER_LABEL.match(w[4])]
    if len(numbers) < VECTOR_CHART_MIN_NUMBERS:
        return False  # không đủ nhãn số thì không cụm nào đạt — bỏ qua bước tìm bảng / gom nét (tốn nhất)
    drawings = [d for d in page.get_drawings() if d.get("color") is not None or (d.get("fill") is not None and not _is_white(d["fill"]))]
    if not drawings:
        return False
    words = set(_words(page.get_text())) | set(_words(native_text(page)))
    tables = []
    for table in page.find_tables(use_layout=False).tables:
        if not (table.row_count and table.col_count):
            continue
        try:
            if not is_false_table(page, table, table.extract(), words):
                tables.append(fitz.Rect(table.bbox))
        except Exception:
            continue  # bảng lỗi: native_elements cũng bỏ qua
    drawings = [d for d in drawings if not any(abs(d["rect"] & t) >= VECTOR_CHART_IN_TABLE * abs(d["rect"]) for t in tables)
                and not (abs(d["rect"]) == 0 and any(d["rect"] in t for t in tables))]
    if not drawings:
        return False
    page_area = abs(page.rect) or 1.0
    margin_x, margin_y = VECTOR_CHART_NUMBER_MARGIN * page.rect.width, VECTOR_CHART_NUMBER_MARGIN * page.rect.height
    for cluster in page.cluster_drawings(drawings=drawings, x_tolerance=VECTOR_CHART_CLUSTER_TOLERANCE, y_tolerance=VECTOR_CHART_CLUSTER_TOLERANCE):
        cluster = cluster & page.rect
        if abs(cluster) < VECTOR_CHART_MIN_AREA * page_area:
            continue
        inside = [d for d in drawings if d["rect"] in cluster]
        wide = cluster + (-margin_x, -margin_y, margin_x, margin_y)
        if (len(inside) >= VECTOR_CHART_MIN_PATHS and sum(1 for w in numbers if (w.tl + w.br) / 2 in wide) >= VECTOR_CHART_MIN_NUMBERS
                and _chart_shape(inside)):
            return True
    return False


def safe_has_vector_chart(page, page_num):
    """has_vector_chart chỉ thêm trang gửi luna: lỗi đo (bảng / nét hỏng) không được làm mất chữ trang ⇒ coi như không có biểu đồ."""
    try:
        return has_vector_chart(page)
    except Exception as e:
        logger.warning(f"Vector chart check failed on page {page_num}: {e}")
        return False


def computed_layout(page):
    """Khối bố cục native_elements VỪA tính trên chính đối tượng page này (page.get_layout() ghi vào page.layout_information), dạng (x0, y0, x1, y1, loại).
    Chỉ đọc lại, không chạy lại mô hình; [] khi native_elements không chạy bố cục (tắt / thiếu thư viện / trang khổ lớn / trang không có chữ, bảng)."""
    return getattr(page, "layout_information", None) or []


def picture_blocks(page):
    """Khối "picture" (biểu đồ / sơ đồ / bản đồ / ảnh) trong computed_layout."""
    return [b for b in computed_layout(page) if "picture" in str(b[4]).lower()]


def largest_picture_ratio(page):
    """Tỉ lệ diện tích trang của khối "picture" lớn nhất (biểu đồ / sơ đồ / bản đồ ⇒ luna); 0 khi không có khối bố cục."""
    area = abs(page.rect) or 1.0
    return max((abs(fitz.Rect(b[:4]) & page.rect) / area for b in picture_blocks(page)), default=0.0)


def native_elements(page, page_num, blocks=None):
    """Chữ native ngoài vùng bảng + bảng (DataFrame). Chữ trong bảng không lặp lại ở phần chữ thường.
    blocks: khối bố cục [(Rect, loại)] đã có sẵn (khối Docling cho trang nhiều cột); None ⇒ khối pymupdf-layout."""
    elements = []
    tables = []
    try:
        tables = [t for t in page.find_tables(use_layout=False).tables if t.row_count and t.col_count]
    except Exception as e:
        logger.warning(f"Table detection failed on page {page_num}: {e}")

    # Chỉ bảng lấy được cả chữ ô lẫn DataFrame mới được bỏ chữ vùng của nó; bảng lỗi giữ chữ trong phần chữ thường
    # (trước đây extract() lỗi làm mất cả trang, to_pandas() lỗi làm mất chữ của bảng) (CHANGELOG: CH-20261014-B1)
    cells, boxes, frames = Counter(), [], []
    # Từ của trang theo cả MuPDF lẫn native_text: PDF LaTeX tiếng Việt mất dấu cách ở get_text() ("ngữcảnh"), ô bảng thì không
    page_words = set(_words(page.get_text())) | set(_words(native_text(page))) if tables else set()
    for idx, table in enumerate(tables):
        try:
            rows = table.extract()
            if is_false_table(page, table, rows, page_words):
                continue
            cell_text = " ".join(str(c) for row in rows for c in row if c).translate(VIETNAMESE_CHAR_FIXES)
            # Ô nhiều dòng: DataFrame.to_html (node_structured) in "\n" thành 2 ký tự dính vào chữ sau ("\n91,76" -> "n91");
            # ký tự điều khiển khác (dấu cách không ToUnicode, "\x1b") cũng thành dấu cách. Ô bảng cũng sửa "ƣ" / "Ð"
            # Tên cột thật chép TRƯỚC to_pandas: hàm này tự đặt "Col0" cho ô tiêu đề trống và "1-VND" khi tên trùng (sửa ngay trong header.names) —
            # chữ không có trong PDF, thành đoạn rác "col0 2025 vnd 2024 vnd" lặp ở mọi trang bảng kéo dài (116 / 1.893 đoạn ở bộ thử) (CHANGELOG: CH-20261020-A8)
            header_names = [str(name or "") for name in table.header.names]
            df = table.to_pandas().fillna("").replace({r"[\x00-\x1f]": " ", **TABLE_CHAR_FIXES}, regex=True)
            df.columns = [re.sub(r"[\x00-\x1f]", " ", name).translate(VIETNAMESE_CHAR_FIXES) for name in header_names]
        except Exception as e:
            logger.warning(f"Table {idx} on page {page_num} could not be converted: {e}")
            continue
        cells.update(_words(cell_text))
        boxes.append(fitz.Rect(table.bbox))
        frames.append({"page": page_num, "data": df, "type": "table", "table_index": idx})

    lines = native_lines(page, boxes, cells)
    if blocks is None:
        blocks = layout_blocks(page) if lines or frames else []
    if blocks:
        items = [("line", rect, text) for rect, text in lines] + [("table", box, frame) for box, frame in zip(boxes, frames)]
        try:
            return group_page_elements(order_by_layout(items, [rect for rect, _ in blocks]), page_num)
        except Exception as e:
            # Sắp thứ tự chỉ đổi thứ tự: lỗi ở đây không được làm mất chữ trang ⇒ thứ tự cũ
            logger.warning(f"Layout ordering failed on page {page_num}: {e}")
    text = "\n".join(t for _, t in lines)
    if text.strip():
        elements.append(PageText(text.strip(), page_num))
    return elements + frames


def layout_block_index(kind, rect, block_rects):
    """Khối chứa phần tử: dòng chữ theo tâm dòng; bảng (hoặc dòng có tâm ngoài mọi khối) theo diện tích chồng lấn lớn nhất."""
    if kind == "line":
        center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        for i, block in enumerate(block_rects):
            if center in block:
                return i
    best, best_area = None, 0.0
    for i, block in enumerate(block_rects):
        area = abs(rect & block)
        if area > best_area:
            best, best_area = i, area
    return best


def order_by_layout(items, block_rects):
    """Sắp phần tử (dòng, bảng) theo thứ tự khối layout; trong một khối xếp theo hàng (trên xuống, trái sang phải); phần tử không
    thuộc khối nào lấy khối + toạ độ của phần tử đứng trước nó (đứng ngay sau nó). Chấm mù 19 trang: xếp theo hàng thắng giữ thứ tự cũ 6–2,
    bảng đúng chỗ 17 / 18 ↔ 14 / 18; thua ở khối nhiều ô cạnh nhau (dòng thời gian) (CHANGELOG: CH-20261016-A18)."""
    ranks, keys, last_rank, last_key = [], [], -1, (0.0, 0.0)
    for kind, rect, _ in items:
        rank = layout_block_index(kind, rect, block_rects)
        # khoá hàng: đáy dòng chữ, đỉnh bảng (như C đo)
        key = (rect.y1 if kind == "line" else rect.y0, rect.x0)
        if rank is None:
            rank, key = last_rank, last_key
        last_rank, last_key = rank, key
        ranks.append(rank)
        keys.append(key)
    ordered = []
    for rank in sorted(set(ranks)):
        group = sorted((i for i in range(len(items)) if ranks[i] == rank), key=lambda i: (keys[i], i))
        rows = []
        for i in group:
            if rows and keys[i][0] - keys[rows[-1][0]][0] <= LAYOUT_ROW_TOLERANCE_PT:
                rows[-1].append(i)
            else:
                rows.append([i])
        ordered += [items[i] for row in rows for i in sorted(row, key=lambda i: (keys[i][1], i))]
    return ordered


def group_page_elements(items, page_num):
    """Dòng liền nhau gộp thành một PageText, bảng giữ nguyên vị trí giữa các đoạn chữ (node_structured ghép theo đúng thứ tự)."""
    elements, buffer = [], []
    for kind, _, value in items:
        if kind == "line":
            buffer.append(value)
            continue
        if "\n".join(buffer).strip():
            elements.append(PageText("\n".join(buffer).strip(), page_num))
        buffer = []
        elements.append(value)
    if "\n".join(buffer).strip():
        elements.append(PageText("\n".join(buffer).strip(), page_num))
    return elements


def ocr_page(file_path, page_num, tesseract_lang):
    """Render một trang bằng poppler (pdf2image) rồi OCR; trả (chữ, số giây, góc đã xoay ảnh). Chạy được trong luồng phụ.
    Đo 14/10 trên 2 menu, cùng 300 dpi + cùng tiền xử lý: render poppler đọc đúng món+giá 23/54 và 26/50,
    PyMuPDF get_pixmap chỉ 18/54 và 21/50 => giữ poppler như Loader cũ."""
    t0 = time.perf_counter()
    image = convert_from_path(file_path, dpi=OCR_DPI, first_page=page_num, last_page=page_num)[0]
    text, rotation = ocr_with_rotation(image, tesseract_lang)
    return text, time.perf_counter() - t0, rotation


def vietnamese_syllables(text):
    """(số mẩu chữ tách theo khoảng trắng, số từ là âm tiết tiếng Việt hợp lệ)."""
    words = [w for w in _words(text) if w.isalpha() and len(w) >= 2]
    return len(text.split()), sum(is_vietnamese_syllable(w) for w in words)


def detect_rotation(image):
    """Góc (0/90/180/270, theo chiều kim đồng hồ) Tesseract OSD đề xuất xoay cho thẳng; 0 khi ít chữ / lỗi. Chạy trên ảnh ~150 dpi cho nhanh."""
    small = image.resize((max(1, image.width // 2), max(1, image.height // 2)))
    try:
        return pytesseract.image_to_osd(small, config="--psm 0", output_type=pytesseract.Output.DICT)["rotate"]
    except pytesseract.TesseractError:
        return 0  # "Too few characters" ở trang ít chữ / ảnh


def ocr_with_rotation(image, tesseract_lang):
    """OCR; chữ ra trông như rác (dưới OCR_ROTATION_CHECK_RATIO âm tiết hợp lệ / mẩu chữ) thì dò hướng trang và xoay ảnh lại trước khi OCR (CHANGELOG:
    CH-20261016-A20). Trang thẳng không dò ⇒ không tốn thêm. Chỉ nhận bản xoay khi đọc ra nhiều âm tiết hợp lệ hơn rõ ⇒ dò nhầm hướng chỉ tốn thời gian,
    không làm hỏng chữ. Giới hạn User chấp nhận: psm 3 đọc được cả bảng đang nằm ngang (âm tiết 0,6–0,75) ⇒ không dò ⇒ bảng sai cấu trúc; nâng ngưỡng (0,8)
    thì dò thừa 65 / 192 trang thẳng và lật nhầm trang ảnh ⇒ bỏ; trang nằm ngang được bắt riêng bằng hộp từ đứng (sideways_rotation) (CHANGELOG: CH-20261016-A24)."""
    text, tall_ratio = ocr_image_and_tall_ratio(image, tesseract_lang)
    tokens, valid = vietnamese_syllables(text)
    # Trang quay 90° (bảng in ngang bị scan nằm): psm 3 vẫn đọc ra chữ hợp lệ nên luật "trông như rác" không bắt ⇒ dùng tỉ lệ hộp từ đứng (sideways_rotation)
    numbers = len(_DIGIT_RUN.findall(text))
    if tall_ratio >= SIDEWAYS_MIN_TALL_RATIO:
        turned = sideways_rotation(image, tesseract_lang, valid + numbers)
        if turned:
            return turned
    if tokens and valid / tokens >= OCR_ROTATION_CHECK_RATIO:
        return text, 0
    rotation = detect_rotation(image)
    if not rotation:
        return text, 0
    rotated_text = ocr_image(image.rotate(-rotation, expand=True), tesseract_lang)  # PIL quay ngược chiều kim đồng hồ khi góc dương
    _, rotated_valid = vietnamese_syllables(rotated_text)
    if rotated_valid >= ROTATED_MIN_SYLLABLES and rotated_valid >= ROTATED_MIN_GAIN * valid:
        return rotated_text, rotation
    # Bảng toàn số lộn ngược: bản xoay đúng chỉ ra số nên ít âm tiết hơn bản rác ⇒ cũng nhận khi chuỗi ≥ 3 chữ số tăng rõ. 72 trang đối chứng (menu có giá,
    # bảng số thẳng mà dò hướng báo nhầm 180°): 0 lật nhầm (CHANGELOG: CH-20261017-A25)
    rotated_numbers = len(_DIGIT_RUN.findall(rotated_text))
    if rotated_numbers >= ROTATED_MIN_NUMBERS and rotated_numbers >= ROTATED_MIN_GAIN * numbers:
        return rotated_text, rotation
    return text, 0


def sideways_rotation(image, tesseract_lang, base_score):
    """Trang quay 90° (bảng in ngang bị scan nằm): psm 3 vẫn đọc ra chữ hợp lệ nên luật "trông như rác" không bắt; dấu hiệu là đa số hộp từ đứng (126 trang
    đã biết hướng: trang 90° / 270° ≥ 0,78, trang thẳng / 180° ≤ 0,46). Hình học chỉ cho biết trang NẰM NGANG, không cho biết xoay về phía nào, và dò hướng
    của Tesseract báo ngược chiều ở scan nét mảnh ⇒ đọc thử CẢ HAI hướng, chỉ nhận hướng đọc ra (âm tiết + số) nhiều hơn rõ hướng kia, hộp từ hết đứng,
    không kém hẳn bản nằm ngang. Không chắc ⇒ None (giữ như cũ) (CHANGELOG: CH-20261017-A26)."""
    tries = []
    for angle in (90, 270):
        text, tall = ocr_image_and_tall_ratio(image.rotate(-angle, expand=True), tesseract_lang)
        score = vietnamese_syllables(text)[1] + len(_DIGIT_RUN.findall(text))
        tries.append((score, angle, text, tall))
    (best, angle, text, tall), (other, *_) = sorted(tries, key=lambda t: t[0], reverse=True)
    if tall < SIDEWAYS_MIN_TALL_RATIO and best >= max(ROTATED_MIN_SYLLABLES, SIDEWAYS_MIN_KEEP * base_score) and best >= ROTATED_MIN_GAIN * other:
        return text, angle
    return None


def ocr_image(image, tesseract_lang):
    return ocr_image_and_tall_ratio(image, tesseract_lang)[0]


def ocr_image_and_tall_ratio(image, tesseract_lang):
    """(chữ, tỉ lệ hộp từ đứng). psm 3 (Tesseract tự chia vùng): bảng chữ nhỏ dày đặc số đúng 43 / 70 ⇒ 83 / 94 % so psm 6 cũ. psm 3 có trang ra gần như rỗng
    (bảng in ngang: 0 / 47 số) ⇒ ra < OCR_PSM3_MIN_WORDS từ thì đọc lại psm 6, giữ bản nhiều từ hơn (CHANGELOG: CH-20261016-A24).
    Tỉ lệ hộp từ đứng lấy từ cùng lượt psm 3 — 0 khi quá ít từ để tin."""
    gray = gray_image_for_ocr(image)
    text, tall_ratio = _tesseract_psm3_text_and_tall_ratio(gray, tesseract_lang)
    if len(text.split()) < OCR_PSM3_MIN_WORDS:
        fallback = _tesseract_text(gray, tesseract_lang, 6)
        if len(fallback.split()) > len(text.split()):
            text = fallback
    return text, tall_ratio


def _tesseract_text(image, tesseract_lang, psm):
    # Tesseract vie cũng ra "Ð" thay "Đ" ("NLÐ" = "NLĐ"; 129 chỗ ở chữ OCR của 39 file) ⇒ sửa cả chữ OCR
    return pytesseract.image_to_string(image, lang=tesseract_lang, config=f'--psm {psm}').strip().translate(VIETNAMESE_CHAR_FIXES)


def _tesseract_psm3_text_and_tall_ratio(image, tesseract_lang):
    """Một lần chạy Tesseract ra cả chữ (txt) lẫn hộp từ (tsv) — psm 3 là mặc định của Tesseract nên chữ giống hệt image_to_string(--psm 3)."""
    txt, tsv = pytesseract.run_and_get_multiple_output(image, extensions=["txt", "tsv"], lang=tesseract_lang)
    words = [row.split("\t") for row in tsv.splitlines()[1:]]
    # cột tsv: ... 6 left, 7 top, 8 width, 9 height, 10 conf, 11 text; chỉ xét từ ≥ 3 ký tự (dấu chấm, chữ lẻ có hộp vuông bất kỳ)
    boxes = [(int(w[8]), int(w[9])) for w in words if len(w) >= 12 and len(w[11].strip()) >= 3]
    tall = sum(h > SIDEWAYS_TALL_FACTOR * wd for wd, h in boxes)
    ratio = tall / len(boxes) if len(boxes) >= SIDEWAYS_MIN_WORDS else 0.0
    return txt.strip().translate(VIETNAMESE_CHAR_FIXES), ratio


def novel_ocr_lines(ocr_text, known_text):
    """Dòng OCR có ít nhất một nửa số từ chưa có trong chữ đã có của trang (tránh lặp chữ native đè lên ảnh)."""
    known = set(_words(known_text))
    keep = []
    for line in ocr_text.splitlines():
        words = _words(line)
        if words and sum(w not in known for w in words) * 2 >= len(words):
            keep.append(line.strip())
    return "\n".join(keep)


def is_good_hidden_layer(layer_text):
    """Lớp OCR ẩn có đủ dấu tiếng Việt để dùng thẳng (không OCR lại). Tesseract OCR lại một lớp tốt làm kém đi:
    "342/2016/TT-BTC" ⇒ "143 3016 TT-BTC", "50%" ⇒ "509%" (CHANGELOG: CH-20261015-A10)."""
    letters = [c for c in unicodedata.normalize("NFC", layer_text) if c.isalpha()]
    if len(letters) < GOOD_HIDDEN_LAYER_MIN_LETTERS:
        return False
    # Chỉ đếm chữ Việt có dấu: chữ Latin-1 lạ của bảng mã cũ TCVN3/VNI ("Th«ng", "ViÖt") không được tính là lớp tốt
    with_diacritics = sum(c.lower() in VIETNAMESE_DIACRITIC_LETTERS for c in letters)
    return with_diacritics / len(letters) >= GOOD_HIDDEN_LAYER_MIN_DIACRITIC_RATIO


def is_vietnamese_syllable(word):
    """Từ có đúng cấu trúc âm tiết tiếng Việt: tối đa một dấu thanh, phụ âm đầu + vần trong danh sách (không dùng từ điển)."""
    decomposed = unicodedata.normalize("NFD", word.lower())
    if sum(c in _TONE_MARKS for c in decomposed) > 1:
        return False
    base = unicodedata.normalize("NFC", "".join(c for c in decomposed if c not in _TONE_MARKS))
    return any(base.startswith(onset) and base[len(onset):] in VIETNAMESE_RHYMES for onset in VIETNAMESE_ONSETS)


def is_garbled_text_layer(text):
    """Lớp chữ có dấu tiếng Việt nhưng phần lớn từ có dấu không phải âm tiết hợp lệ, có từ trộn chữ Latin với chữ Kirin, hoặc mất ký tự (nhiều chữ cái đứng lẻ)
    — xem GARBLED_*, LOST_GLYPH_* (CHANGELOG: CH-20261015-A16, CH-20261017-A33, CH-20261017-A38).
    Bỏ chữ viết tắt in hoa ngắn ("ĐS", "GDĐH")."""
    if sum(is_latin_cyrillic_word(w) for w in _words(text)) >= GARBLED_MIN_MIXED_SCRIPT_WORDS:
        return True
    tokens = text.split()
    if len(tokens) >= LOST_GLYPH_MIN_TOKENS and sum(len(t) == 1 and t.isalpha() for t in tokens) >= LOST_GLYPH_MIN_LONE_RATIO * len(tokens):
        return True
    marked = [w for w in _words(text) if len(w) >= 2 and w.isalpha() and any(c.lower() in VIETNAMESE_DIACRITIC_LETTERS for c in w)
              and not (w.isupper() and len(w) <= 5 and sum(c.lower() in "aeiouyăâêôơư" for c in w) <= 1)]
    if len(marked) < GARBLED_MIN_MARKED_WORDS:
        return False
    return sum(not is_vietnamese_syllable(w) for w in marked) / len(marked) >= GARBLED_MIN_INVALID_RATIO


def has_math_formula(page):
    """Trang chữ sẵn có công thức: ≥ FORMULA_MIN_MATH_FONT_CHARS ký tự font toán (không tính dấu đầu dòng 1 ký tự của font Symbol / Wingdings) hoặc ≥ 1 ký hiệu
    toán Unicode (∑ ∫ √ ∂ ∇, chữ toán U+1D400–1D7FF). File chưa thấy: bài báo 4 / 4, giáo trình 75 / 79 + 208 / 213 trang công thức, nhầm 0 / ~18 trang xem tay.
    Sót: công thức gõ bằng font chữ thường (CHANGELOG: CH-20261017-A37)."""
    math_chars = 0
    # Chỉ chữ (TEXTFLAGS_TEXT): "dict" mặc định kèm byte ảnh ⇒ trang ảnh lớn chậm tới 0,88 s; kết quả giống hệt 208 / 208 trang
    for block in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                text = span["text"].strip()
                if not text:
                    continue
                if any(c in MATH_SYMBOLS or 0x1D400 <= ord(c) <= 0x1D7FF for c in text):
                    return True
                if MATH_FONT_PATTERN.search(span["font"]) and not (len(text) == 1 and text in BULLET_CHARS):
                    math_chars += len(text.replace(" ", ""))
    return math_chars >= FORMULA_MIN_MATH_FONT_CHARS


def page_has_math_formula(page, page_num):
    """has_math_formula nhưng lỗi chỉ làm mất cờ, không làm mất chữ trang (để chung try với native_elements thì lỗi ⇒ elements = [])."""
    try:
        return has_math_formula(page)
    except Exception as e:
        logger.warning(f"Formula check failed on page {page_num}: {e}")
        return False


def is_latin_cyrillic_word(word):
    """Từ có cả chữ cái Latin lẫn chữ cái Kirin ("đҥi") — dấu hiệu font ánh xạ sai, không phải chữ tiếng Nga thật (từ tiếng Nga không chứa chữ Latin)."""
    letters = [unicodedata.name(c, "") for c in word if c.isalpha()]
    return any(n.startswith("LATIN") for n in letters) and any(n.startswith("CYRILLIC") for n in letters)


def elements_text(elements):
    """Chữ thường + chữ ô bảng của trang (xét lớp chữ hỏng phải tính cả bảng: catalog chữ hỏng nằm gần hết trong bảng)."""
    parts = []
    for e in elements:
        if isinstance(e, PageText):
            parts.append(e.text)
        elif isinstance(e, dict) and e.get("type") == "table":
            parts.append(" ".join(str(v) for v in e["data"].values.ravel()))
    return "\n".join(parts)


def layer_number_lines(layer_text, ocr_text):
    """Dòng của lớp OCR ẩn chứa số (≥ 2 chữ số) mà chữ OCR lại không có. Lớp mất dấu vẫn có thể đúng số: bảng
    BCTC lớp ẩn "37,307,000,821", Tesseract ra "37307000821", "3/42930352" ⇒ giữ dòng số của lớp ẩn (CHANGELOG: CH-20261015-A10)."""
    ocr_numbers = set(scan_conflicts.NUMBER.findall(ocr_text))
    keep = []
    for line in layer_text.splitlines():
        numbers = [n for n in scan_conflicts.NUMBER.findall(line) if sum(c.isdigit() for c in n) >= 2]
        if any(n not in ocr_numbers for n in numbers):
            keep.append(line.strip())
    return "\n".join(keep)


def first_pass_page(doc, idx):
    """Lượt 1 cho một trang: tín hiệu, phân lớp, chữ native/bảng. Trả (elements, page_status, cần OCR?, chữ lớp OCR ẩn)."""
    page_num = idx + 1
    t0 = time.perf_counter()
    elements, method, chars, flags, needs_ocr, layer, picture_ratio, picture_boxes, vector_lines = [], "error", 0, [], False, "", 0.0, [], []
    side_pairs = 0
    try:
        page = doc.load_page(idx)
        signals = page_signals(page)
        method, chars = classify_page(signals), signals["chars"]
        # Trang native không OCR vùng ảnh: đo 14/10, Tesseract trên ảnh chụp/bảng-ảnh chủ yếu ra ký tự rác
        # (advanced_fashion, pwc) => chữ trong ảnh của trang có chữ để bước 2 (VLM)
        if method == "native":
            elements = native_elements(page, page_num)
            # Ba tín hiệu dưới đọc khối bố cục native_elements vừa tính (computed_layout), không chạy lại mô hình
            picture_ratio = largest_picture_ratio(page)
            # Khung khối "picture" để lượt 3 cắt ảnh biểu đồ (chart_crop) — lượt 1 chạy trong tiến trình con, không giữ được đối tượng page
            picture_boxes = [[round(v, 1) for v in b[:4]] for b in picture_blocks(page)]
            if not is_large_page(page):
                vector_lines = vector_text_lines(page)
            # Dấu hiệu trang nhiều cột
            side_pairs = docling_order.side_by_side_pairs([(fitz.Rect(b[:4]), b[4]) for b in computed_layout(page)])
            if is_garbled_text_layer(elements_text(elements)):
                # Lớp chữ hỏng: giữ chữ hỏng (không mất gì nếu báo nhầm), đi đường lớp ocr: Tesseract thêm dòng mới + luna (lượt 3) đọc lại ảnh trang
                method, flags, needs_ocr = "ocr", ["garbled_text_layer"], True
            elif is_large_page(page):
                # Bản đồ / bản vẽ khổ lớn — không có bố cục ⇒ không có khối "picture"; gửi luna như trang hình
                flags = ["large_page"]
            elif page_has_math_formula(page, page_num):
                # Chữ công thức vỡ cấu trúc (phân số, chỉ số thành chữ liền) ⇒ lượt 3 gửi luna (page_vision.select_pages)
                flags = ["math_formula"]
            elif picture_ratio < page_vision.VLM_MIN_PICTURE_RATIO and safe_has_vector_chart(page, page_num):
                # 20/10 tối: biểu đồ vẽ bằng nét bố cục không nhận ra ⇒ lượt 3 gửi luna như trang hình
                flags = ["vector_chart"]
        elif method == "ocr":
            if chars:
                # Chữ native ít ỏi (tiêu đề, chân trang) vẫn đúng: giữ, OCR chỉ thêm dòng mới
                elements = [PageText(native_text(page).strip(), page_num)]
            needs_ocr = True
        elif method == "hidden_ocr":
            layer = native_text(page).strip()
            if is_good_hidden_layer(layer):
                elements, flags, layer = [PageText(layer, page_num)], ["ocr_layer_kept"], ""
            else:
                needs_ocr = True
    except Exception as e:
        # Một trang hỏng không làm cả file mất chữ
        logger.warning(f"Page {page_num} extraction failed: {e}")
        elements, flags = [], ["page_error"]
    status = {"type": "page_status", "page": page_num, "method": method, "native_chars": chars, "ocr_chars": 0,
              "seconds": time.perf_counter() - t0, "flags": flags, "picture_ratio": round(picture_ratio, 3), "picture_boxes": picture_boxes,
              "vector_text_lines": vector_lines, "layout_side_pairs": side_pairs}
    return elements, status, needs_ocr, layer


def first_pass_range(file_path, first, last):
    """Lượt 1 cho trang first..last (chỉ số 0, gồm cả last); chạy được trong tiến trình con (tự mở file)."""
    with fitz.open(file_path) as doc:
        return [first_pass_page(doc, idx) for idx in range(first, last + 1)]


def first_pass(file_path, page_count):
    """Lượt 1 cho cả file, theo thứ tự trang. File lớn: batch FIRST_PASS_BATCH_PAGES trang cho PAGE_WORKERS tiến trình;
    forkserver để không fork tiến trình server đang có nhiều luồng.
    File nhỏ cũng chạy trong MỘT tiến trình con: mô hình bố cục (pymupdf-layout `get_layout`) làm sập tiến trình (segfault) ở một số trang, try / except
    không bắt được ⇒ trước đây file < 40 trang làm chết luôn tiến trình app. Tiến trình con chết ⇒ đọc lại cả file ở tiến trình chính, tắt bố cục
    (first_pass_without_layout) (CHANGELOG: CH-20261017-A35)."""
    if page_count == 0:
        return []
    if page_count < PARALLEL_FIRST_PASS_MIN_PAGES:
        starts, ends, workers = [0], [page_count - 1], 1
    else:
        starts = list(range(0, page_count, FIRST_PASS_BATCH_PAGES))
        ends = [min(start + FIRST_PASS_BATCH_PAGES, page_count) - 1 for start in starts]
        workers = PAGE_WORKERS
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("forkserver")) as executor:
            return [page for batch in executor.map(first_pass_range, [file_path] * len(starts), starts, ends) for page in batch]
    except BrokenProcessPool:
        logger.warning("First pass worker crashed (layout model); re-reading the file without layout ordering")
        return first_pass_without_layout(file_path, page_count)


def first_pass_without_layout(file_path, page_count):
    """Lượt 1 ở tiến trình chính, không gọi mô hình bố cục: chữ, bảng như cũ; mất thứ tự theo khối và nhận trang có hình (khối "picture") — cờ layout_skipped_crash.
    Tiến trình chính không dùng bố cục ở chỗ nào khác (lượt 1 luôn chạy ở tiến trình con) ⇒ tắt hẳn bằng _layout_module = False."""
    global _layout_module
    _layout_module = False
    pages = first_pass_range(file_path, 0, page_count - 1)
    for _, status, _, _ in pages:
        status["flags"].append("layout_skipped_crash")
    return pages


def apply_docling_order(file_path, raw_elements, statuses):
    """Lượt 1b: trang chữ sẵn nhiều cột (≥ MIN_SIDE_PAIRS cặp khối cạnh nhau) đọc lại theo khối + thứ tự khối của Docling; chữ, bảng vẫn
    của native_elements (CHANGELOG: CH-20261018-A40). Docling chạy trong MỘT tiến trình con cho cả file (nạp mô hình ~4 s một lần; sập không làm chết app).
    Lỗi / thiếu thư viện / trang Docling không trả khối ⇒ giữ thứ tự pymupdf-layout của lượt 1 (chỉ đổi thứ tự, không được mất chữ).
    Chặn: Docling trả đúng MỘT khối "picture" phủ cả trang (trang infographic) ⇒ giữ pymupdf-layout."""
    pages = [idx for idx, status in enumerate(statuses)
             if status["method"] == "native" and status.get("layout_side_pairs", 0) >= docling_order.MIN_SIDE_PAIRS]
    if not pages or not docling_order.docling_order_enabled():
        return
    t0 = time.perf_counter()
    try:
        with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("forkserver")) as executor:
            blocks = executor.submit(docling_order.page_blocks, file_path, [idx + 1 for idx in pages]).result()
    except Exception as e:
        logger.warning(f"Docling reading order skipped: {type(e).__name__}: {e}")
        return
    used = 0
    with fitz.open(file_path) as doc:
        for idx in pages:
            rows = blocks.get(idx + 1)
            if not rows or (len(rows) == 1 and rows[0][4] == "picture"):
                continue
            try:
                raw_elements[idx] = native_elements(doc[idx], idx + 1, [(fitz.Rect(row[:4]), row[4]) for row in rows])
            except Exception as e:
                logger.warning(f"Docling ordering failed on page {idx + 1}: {e}")
                continue
            statuses[idx]["flags"].append("docling_order")
            used += 1
    seconds = time.perf_counter() - t0
    for idx in pages:
        statuses[idx]["seconds"] += seconds / len(pages)
    logger.info(f"Docling reading order: {used}/{len(pages)} pages, {seconds:.1f} s")


def add_ocr_text(elements, status, text, hidden_layer, page_num):
    """Lượt 2, một trang: ghi chất lượng chữ OCR vào status, thêm các dòng OCR chưa có trong chữ trang (kèm số của lớp OCR ẩn cũ — layer_number_lines).
    OCR không ra gì ⇒ cờ ocr_empty; trang lớp OCR ẩn thì dùng lại lớp đó, không để mất chữ."""
    # Chất lượng chữ OCR (cùng tín hiệu dò trang xoay) — lượt 3 dùng để bỏ chữ rác ở trang trắng (CHANGELOG: CH-20261016-A22)
    tokens, valid = vietnamese_syllables(text)
    status["ocr_tokens"] = tokens
    status["ocr_syllable_ratio"] = round(valid / tokens, 3) if tokens else 0.0
    known = "\n".join(e.text for e in elements if isinstance(e, PageText))
    # Lớp chữ hỏng không đáng tin ⇒ không lọc dòng OCR theo nó: lớp lỗi mã font một phần đủ từ đúng để mọi dòng OCR bị coi là trùng
    # (cờ ocr_empty, chữ máy chỉ còn bản có chữ Kirin) dù Tesseract đọc được 357 / 432 từ
    garbled = "garbled_text_layer" in status["flags"]
    new_text = novel_ocr_lines(text, known) if known and not garbled else text
    if new_text:
        elements.append(PageText(new_text, page_num))
        status["ocr_chars"] += len(new_text)
        layer_numbers = layer_number_lines(hidden_layer, new_text)
        if layer_numbers:
            elements.append(PageText(layer_numbers, page_num))
            status["flags"].append("layer_numbers_added")
    else:
        if "ocr_error" not in status["flags"]:
            status["flags"].append("ocr_empty")
        if status["method"] == "hidden_ocr":
            # OCR lỗi hoặc không ra gì thì dùng lại lớp chữ ẩn, không để mất chữ
            elements.append(PageText(hidden_layer, page_num))


def drop_conflicting_ocr_numbers(elements, vlm_text, trusted_text=""):
    """Trang scan lưu cả chữ Tesseract lẫn chữ luna ⇒ cùng một con số có hai giá trị, LLM phải chọn. Xoá khỏi chữ máy của trang (PageText,
    trước khối luna) những con số mâu thuẫn với luna, giữ mọi chữ khác. Đo trên 230 trang 2 bản đã lưu: luna đúng 7 / 7 cặp phân xử được, ý câu hỏi luna 60 / 69
    ↔ Tesseract 39 / 69; bỏ hẳn bản Tesseract thì mất chữ thật luna sót (tiêu đề, ngày) ⇒ chỉ bỏ con số. Số có trong `trusted_text` (chữ thật sẵn có của trang scan:
    tiêu đề, chân trang) giữ nguyên. Trả số con số đã xoá. Luật so: scan_conflicts.py (CHANGELOG: CH-20261020-A4)."""
    machine_text = "\n".join(e.text for e in elements if isinstance(e, PageText))
    bad = scan_conflicts.conflicting_ocr_numbers(machine_text, vlm_text) - scan_conflicts.numbers_in(trusted_text)
    if not bad:
        return 0
    for e in elements:
        if isinstance(e, PageText):
            e.text = scan_conflicts.remove_numbers(e.text, bad)
    return len(bad)


def drop_conflicting_ocr_dates(elements, vlm_text, trusted_text=""):
    """Như drop_conflicting_ocr_numbers nhưng cho ngày tháng (User: hai bản đọc khác nhau thì chọn luna — Tesseract "2/5/2012" ↔ luna "29/5/2012"; luật số
    không bắt vì ngày / tháng là số ngắn) (CHANGELOG: CH-20261021-A11). Xoá cả ngày đọc lệch khỏi chữ máy của trang, giữ mọi chữ khác; ngày có trong `trusted_text` giữ nguyên. Trả số ngày đã xoá."""
    machine_text = "\n".join(e.text for e in elements if isinstance(e, PageText))
    bad = scan_conflicts.conflicting_ocr_dates(machine_text, vlm_text) - scan_conflicts.dates(trusted_text)
    if not bad:
        return 0
    for e in elements:
        if isinstance(e, PageText):
            e.text = scan_conflicts.remove_dates(e.text, bad)
    return len(bad)


def vlm_page_text(reading, page_num, known_text):
    """Khối chữ VLM của một trang: tiêu đề nguồn + mô tả + phần hình ảnh + dòng chữ VLM chưa có trong chữ trang (tránh lặp chữ
    native/OCR). Trang VLM báo trống/trang trí, hoặc không có gì mới, trả "" (quyết định đã chốt 14/10: bỏ trang trống/trang trí)."""
    if reading.get("is_blank_or_decorative"):
        return ""
    text_lines = "\n".join(line for line in reading.get("text_lines", []) if line.strip())
    new_lines = novel_ocr_lines(text_lines, known_text) if known_text else text_lines
    visuals = [f"- {v['kind']}: {v['description_vi'].strip()}" for v in reading.get("visuals", []) if v.get("description_vi", "").strip()]
    description = reading.get("description_vi", "").strip()
    if not (description or visuals or new_lines):
        return ""
    parts = [f"[Mô tả ảnh trang {page_num} — do mô hình đọc ảnh]"]
    parts += [description] if description else []
    parts += visuals
    parts += [new_lines] if new_lines else []
    return "\n".join(parts)


_VOWELS = set("aeiouy") | VIETNAMESE_DIACRITIC_LETTERS
_NUMBER_TOKEN = re.compile(r"\d+([.,/]\d+)*%?")
_TOKEN_EDGE = ".,;:!?()[]\"'“”‘’«»-–—…*"


def is_word_like(token):
    """Mẩu chữ trông như từ thật — không cần từ điển, dùng cho cả tiếng Việt lẫn tiếng Anh: số ("12.448", "1/7", "50%"), hoặc chỉ gồm chữ cái,
    ≥ 2 ký tự, có nguyên âm, viết thường / IN HOA / Hoa đầu. Mẩu rác Tesseract đọc từ ảnh chụp ("“..x2", "Ẻ", "ằïằ-—^") không qua."""
    token = token.strip(_TOKEN_EDGE)
    if _NUMBER_TOKEN.fullmatch(token):
        return True
    if len(token) < 2 or not token.isalpha():
        return False
    if not any(c in _VOWELS for c in unicodedata.normalize("NFC", token.lower())):
        return False
    return token.islower() or token.isupper() or (token[0].isupper() and token[1:].islower())


_SEPARATOR_TOKEN = re.compile(r"[|_\-–—=:]+")


def drop_ocr_junk_lines(text):
    """Bỏ các dòng có dưới OCR_JUNK_LINE_MIN_WORD_LIKE mẩu trông như từ; trả (chữ còn lại, số dòng đã bỏ). Mẩu chỉ là dấu ngăn cột ("|", "—")
    không đếm: hàng bảng scan nhiều "|" vẫn là chữ thật. Dòng chỉ có dấu ngăn thì bỏ."""
    kept, dropped = [], 0
    for line in text.splitlines():
        tokens = [t for t in line.split() if not _SEPARATOR_TOKEN.fullmatch(t)]
        if tokens and sum(is_word_like(t) for t in tokens) >= OCR_JUNK_LINE_MIN_WORD_LIKE * len(tokens):
            kept.append(line)
        elif line.strip():
            dropped += 1
    return "\n".join(kept), dropped


def drop_junk_ocr_elements(elements, native_text, status):
    """drop_ocr_junk_lines trên các PageText chữ OCR của trang (chữ native giữ nguyên); phần tử hết chữ thì bỏ. Trả (phần tử mới, số dòng đã bỏ)."""
    kept, dropped = [], 0
    for e in elements:
        if isinstance(e, PageText) and e.text not in native_text:
            text, lines = drop_ocr_junk_lines(e.text)
            dropped += lines
            status["ocr_chars"] -= len(e.text) - len(text)
            if not text:
                continue
            e.text = text
        kept.append(e)
    return kept, dropped


def is_blank_scan_noise(reading, status):
    """VLM báo trang trống / chỉ trang trí, trang không có lớp chữ (ocr) và chữ OCR trông như rác (cùng ngưỡng dò trang xoay)."""
    return (bool(reading.get("is_blank_or_decorative")) and status.get("method") == "ocr"
            and status.get("ocr_syllable_ratio", 1.0) < OCR_ROTATION_CHECK_RATIO)


def add_vlm_text(file_path, raw_elements, statuses, native_texts, vlm_client=None, on_usage=None):
    """Lượt 3: chọn trang (page_vision.select_pages), gọi VLM, thêm một PageText mỗi trang. Lỗi provider ⇒ cờ vlm_error, chữ cũ giữ nguyên.
    Dòng VLM chỉ được lọc trùng với chữ NATIVE của trang (native_texts, lấy sau lượt 1), không với chữ OCR: Tesseract đọc gần đúng
    ("Jumbo Wagyvu Tomahawk (2.2kg)") đủ từ để dòng đúng của VLM bị coi là trùng và mất (menu: món + giá 38 / 54 khi lọc theo cả OCR).
    vlm_client / on_usage: xem page_vision.describe_pages (None ⇒ client của app)."""
    with fitz.open(file_path) as doc:
        selected, over_cap = page_vision.select_pages(doc, statuses)
    for idx in over_cap:
        statuses[idx]["flags"].append("vlm_skipped_cap")
    # Trang OCR đã xoay ⇒ ảnh gửi luna cũng xoay theo cùng góc
    rotations = {idx: int(f.split("_")[1]) for idx in selected for f in statuses[idx]["flags"] if f.startswith("rotated_")}
    for idx, reading, seconds in page_vision.describe_pages(file_path, selected, rotations, vlm_client, on_usage):
        statuses[idx]["seconds"] += seconds
        if isinstance(reading, Exception):
            statuses[idx]["flags"].append("vlm_error")
            continue
        raw_elements[idx] = add_page_reading(file_path, idx, reading, raw_elements[idx], statuses[idx], native_texts.get(idx, ""), idx in rotations)
    if selected or over_cap:
        counts = Counter(f for idx in selected for f in statuses[idx]["flags"] if f.startswith("vlm_"))
        logger.info(f"VLM pages: {len(selected)} sent ({dict(counts)}), {len(over_cap)} over cap ({page_vision.PROMPT_VERSION})")


def add_page_reading(file_path, idx, reading, elements, status, native_text, rotated):
    """Lượt 3, một trang luna đọc được: thêm khối chữ luna + ảnh từng hình; trang scan bỏ số / ngày máy đọc lệch luna; trang scan trắng bỏ chữ OCR rác.
    native_text: chữ native của trang sau lượt 1; rotated: ảnh trang đã xoay khi OCR. Trả list phần tử mới của trang."""
    # Không lọc dòng luna theo chữ native khi chữ native không đáng tin: lớp chữ hỏng (số rời của lớp hỏng từng làm mất 2 hàng bảng đúng của luna);
    # lớp OCR ẩn được giữ (sai dấu vài từ mỗi dòng nên dòng đúng dấu của luna bị coi là trùng — 22 dòng chỉ giữ 1) ⇒ chấp nhận lặp chữ (CHANGELOG:
    # CH-20261017-A30); trang công thức (dòng công thức của luna trùng ký hiệu rời ⇒ mất 83 / 101 dòng) (CHANGELOG: CH-20261017-A37)
    untrusted = {"garbled_text_layer", "ocr_layer_kept", "math_formula"} & set(status["flags"])
    known_text = "" if untrusted else native_text
    text = vlm_page_text(reading, idx + 1, known_text)
    if text and status["method"] in ("ocr", "hidden_ocr"):
        # Trang scan: chữ máy (Tesseract / lớp OCR ẩn) và chữ luna cùng lưu ⇒ bỏ con số máy đọc khác luna (gọi trước khi thêm khối luna)
        # Lớp `ocr`: ít chữ thật sẵn có (tiêu đề, chân trang) đúng ⇒ không xoá số của nó; lớp hỏng / lớp OCR ẩn thì không tin
        trusted = native_text if status["method"] == "ocr" and not untrusted else ""
        dropped = drop_conflicting_ocr_numbers(elements, text, trusted)
        if dropped:
            status["flags"].append(f"ocr_numbers_dropped_{dropped}")
        dropped_dates = drop_conflicting_ocr_dates(elements, text, trusted)
        if dropped_dates:
            status["flags"].append(f"ocr_dates_dropped_{dropped_dates}")
        if status["method"] == "ocr" and status.get("ocr_syllable_ratio", 1.0) < OCR_ROTATION_CHECK_RATIO:
            # Trang ảnh / menu toàn ảnh: Tesseract đọc ảnh chụp ra rác, luna đã đọc trang ⇒ bỏ dòng OCR rác, giữ dòng trông như chữ thật (CHANGELOG: CH-20261023-B2)
            elements, junk_lines = drop_junk_ocr_elements(elements, native_text, status)
            if junk_lines:
                status["flags"].append(f"ocr_junk_lines_dropped_{junk_lines}")
    if text:
        elements.append(PageText(text, idx + 1))
        status["flags"].append("vlm_described")
        if not rotated:
            # Ảnh từng biểu đồ / sơ đồ / ảnh chụp ⇒ "hình" như cặp ảnh – chú thích (app hiện lại ảnh khi hỏi). Trang đã xoay: khung luna theo ảnh xoay ⇒ bỏ qua
            figures = chart_crop.crop_figures(file_path, idx, reading, status.get("picture_boxes", []))
            elements.extend(figures)
            if figures:
                status["flags"].append(f"figure_images_{len(figures)}")
    else:
        status["flags"].append("vlm_empty")
        if is_blank_scan_noise(reading, status):
            # Trang trắng của bản scan (mặt sau, vết mờ): luna báo trống, chữ OCR trông như rác ⇒ bỏ chữ OCR, giữ chữ native nếu có
            # (một quyết định scan: 6 / 26 trang trắng lưu 3–50 mẩu rác, tỉ lệ âm tiết 0,00–0,29) (CHANGELOG: CH-20261016-A22)
            elements = [e for e in elements if not (isinstance(e, PageText) and e.text not in native_text)]
            status["ocr_chars"] = 0
            status["flags"].append("ocr_dropped_blank")
    return elements


def extract_pages(file_path, language, use_vlm=None, vlm_client=None, on_usage=None):
    """Trích xuất từng trang theo lớp của chính trang đó (lượt 1, 1b, 2, 3).
    Trả về raw_elements: list theo trang; mỗi trang có các PageText/dict table và một dict type=page_status.
    use_vlm: chạy lượt 3 (luna); None ⇒ theo PDF_VLM_ENABLED (page_vision.vlm_enabled). vlm_client / on_usage: xem page_vision.describe_pages."""
    tesseract_lang = to_tesseract_lang(language)
    raw_elements, statuses, ocr_pages = [], [], []
    hidden_layers = {}  # idx -> chữ lớp OCR ẩn của trang sẽ OCR lại (để giữ số của lớp cũ)
    with fitz.open(file_path) as doc:
        page_count = doc.page_count
    # Lượt 1: tín hiệu, phân lớp, chữ native; ghi lại các trang cần OCR
    for idx, (elements, status, needs_ocr, layer) in enumerate(first_pass(file_path, page_count)):
        raw_elements.append(elements)
        statuses.append(status)
        if needs_ocr:
            ocr_pages.append(idx)
        if layer:
            hidden_layers[idx] = layer
    # Lượt 1b: trang chữ sẵn nhiều cột lấy thứ tự khối của Docling
    apply_docling_order(file_path, raw_elements, statuses)
    # Chữ native của từng trang (trước OCR) — để lọc dòng VLM trùng ở lượt 3
    native_texts = {idx: "\n".join(e.text for e in elements if isinstance(e, PageText)) for idx, elements in enumerate(raw_elements)}
    # Lượt 2: OCR song song (render poppler + Tesseract đều là tiến trình riêng); mỗi luồng giữ một ảnh trang
    with ThreadPoolExecutor(max_workers=OCR_WORKERS) as executor:
        futures = [executor.submit(ocr_page, file_path, idx + 1, tesseract_lang) for idx in ocr_pages]
        for idx, future in zip(ocr_pages, futures):
            try:
                text, seconds, rotation = future.result()
                statuses[idx]["seconds"] += seconds
                if rotation:
                    statuses[idx]["flags"].append(f"rotated_{rotation}")
            except Exception as e:
                logger.warning(f"OCR failed for page {idx + 1}: {e}")
                statuses[idx]["flags"].append("ocr_error")
                text = ""
            add_ocr_text(raw_elements[idx], statuses[idx], text, hidden_layers.get(idx, ""), idx + 1)
    # Lượt 3 (ảnh giai đoạn 1): VLM đọc trang không có lớp chữ / trang có ảnh lớn — chỉ THÊM chữ, không thay chữ đã có
    if page_vision.vlm_enabled() if use_vlm is None else use_vlm:
        try:
            add_vlm_text(file_path, raw_elements, statuses, native_texts, vlm_client, on_usage)
        except Exception as e:
            # Lượt VLM chỉ thêm chữ: lỗi ở chọn trang / tạo client không được làm mất chữ đã có của file
            logger.warning(f"VLM pass skipped: {type(e).__name__}")
            # Cờ cho mọi trang để metadata chunk cho biết file này thiếu phần đọc ảnh (trước đây chỉ có log)
            for status in statuses:
                status["flags"].append("vlm_pass_error")

    for elements, status in zip(raw_elements, statuses):
        status["seconds"] = round(status["seconds"], 3)
        if status["method"] == "empty":
            status["flags"].append("empty_page")
        elif not any((isinstance(e, dict) and e.get("type") == "table") or getattr(e, "text", "") for e in elements):
            status["flags"].append("no_text")
        elements.append(status)
    counts = {}
    for status in statuses:
        counts[status["method"]] = counts.get(status["method"], 0) + 1
    logger.info(f"Page methods: {counts}")
    return raw_elements


def load_pdf_pages(file_path, language="vie+eng", vlm_client=None, on_usage=None):
    """Loader PDF dùng riêng (ngoài app): đọc file, KHÔNG xoá file, lỗi ném ra cho nơi gọi. Trả list theo trang; mỗi trang: PageText, dict table
    (DataFrame), dict figure_caption, đúng một dict page_status, rồi ảnh hình luna (README_pdf.md, "Đầu ra").
    language: mã kiểu app ("vie+eng", "vie", "en", …) ⇒ ngôn ngữ Tesseract. vlm_client: client OpenAI (Responses API, model page_vision.VLM_MODEL) để luna
    đọc trang khó; on_usage(dict) nhận số token mỗi lượt luna; vlm_client None ⇒ không gọi luna (bỏ PDF_VLM_ENABLED)."""
    return _load_pages(file_path, language, vlm_client is not None, vlm_client, on_usage)


def _load_pages(file_path, language, use_vlm=None, vlm_client=None, on_usage=None):
    """Phần chung của load_pdf_pages và process_pdf: các lượt đọc trang (extract_pages) + cặp ảnh–chú thích."""
    raw_elements = extract_pages(file_path, language, use_vlm, vlm_client, on_usage)
    # Cặp ảnh–chú thích (hình học): thêm dict type=figure_caption, không có khóa text nên không lặp vào chữ trang
    add_figure_caption_pairs(file_path, raw_elements)
    # Ảnh chụp luna (page-v7) trùng chỗ cặp hình học ở trên ⇒ bỏ bản luna, một ảnh không thành hai "hình"
    raw_elements = [chart_crop.drop_duplicate_photos(elements) for elements in raw_elements]
    # Chú thích ảnh đọc chữ riêng (figure_caption._page_lines), không qua native_text ⇒ sửa "ƣ" / "Ð" ở đây
    for elements in raw_elements:
        for e in elements:
            if isinstance(e, dict) and e.get("type") == "figure_caption":
                e["caption_lines"] = [line.translate(VIETNAMESE_CHAR_FIXES) for line in e["caption_lines"]]
    return raw_elements


def process_pdf(folder_path, file_path, language, export_base64=False):
    """Điểm vào của app (upload_files): như load_pdf_pages nhưng luna của app theo PDF_VLM_ENABLED (page_vision.app_client), lỗi ⇒ [] (tải lên báo
    thất bại) và luôn XOÁ file đầu vào. Trả (raw_elements, page_image_base64_list) — list thứ hai là chuỗi rỗng mỗi trang (app không lưu ảnh trang;
    node_structured cần cùng độ dài). folder_path, export_base64 không dùng (nhánh xuất ảnh trang đã bỏ 22/10), giữ cho chỗ gọi cũ."""
    try:
        with fitz.open(file_path) as doc:
            page_image_base64_list = [""] * doc.page_count
    except Exception as e:
        logger.error(f"PDF splitting failed: {e}")
        page_image_base64_list = []

    raw_elements = []
    try:
        raw_elements = _load_pages(file_path, language)
        if len(page_image_base64_list) != len(raw_elements):
            page_image_base64_list = [""] * len(raw_elements)
    except Exception as e:
        logger.error(f"PDF processing failed: {e}")
        raw_elements = []

    try:
        remove_file(Path(file_path))
    except Exception as e:
        logger.warning(f"File cleanup failed: {e}")

    return raw_elements, page_image_base64_list


def remove_file(file_path_rm):
    try:
        # Đảm bảo file_path_rm là Path object
        if isinstance(file_path_rm, str):
            file_path_rm = Path(file_path_rm)

        if file_path_rm.exists():
            file_path_rm.unlink()
            print("File removed successfully.")
    except PermissionError as e:
        print("File remove error: ", e)
        pass
    except Exception as e:
        print(f"File remove error: {e}")
        pass
