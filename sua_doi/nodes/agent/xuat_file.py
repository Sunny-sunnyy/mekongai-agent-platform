"""[nhóm kỹ thuật, tối 05/11/2026 — User dặn chiều 04/11: "khi tạo file pdf, pptx, thì cần nghiên cứu thêm các skills để tạo file cho đẹp"]
Công cụ xuất file ĐẸP cho khối Agent: make_pptx bản 2, make_docx bản 2, make_pdf (mới). Bản cũ: chay_thu/sua_doi/nodes/agent/native_tools.py.

Nguyên tắc giữ từ bản cũ: AI chỉ đưa NỘI DUNG (chữ, số, tên bộ màu, tên kiểu trang) qua tham số; CODE dựng file. Không có code do AI viết chạy trên máy.
Mới — theo skill document-skills (pptx / pdf / docx, bản ca45fc496a34), tóm tắt ở chay_thu/GHI_CHU_FILE_DEP.md:
  - bộ màu theo chủ đề, một màu chính (60–70 %), một màu nhấn; AI chỉ chọn TÊN bộ trong danh sách cho phép (không đưa mã màu, không đưa đường dẫn);
  - đổi bố cục giữa các trang (7 kiểu trang); mỗi trang có một yếu tố hình (vòng tròn đánh số, thẻ, trục thời gian, bảng);
  - không sọc màu / gạch chân tiêu đề (skill coi là dấu hiệu "slide AI làm"); lề ≥ 0,5 inch; phông an toàn (Cambria tiêu đề + Calibri thân);
  - CODE đo độ dài chữ (bằng số đo phông DejaVu — rộng hơn Calibri ⇒ ước dư, an toàn): quá dài thì giảm cỡ (thân ≥ 12pt, tiêu đề ≥ 20pt),
    vẫn không vừa thì TÁCH TRANG; một mục không vừa nổi một trang ở cỡ nhỏ nhất ⇒ trả lỗi rõ, KHÔNG cắt chữ lặng lẽ;
  - PDF nhúng phông DejaVu (có đủ dấu tiếng Việt); chữ của AI được ESCAPE trước khi vào reportlab (thẻ <img src=…> của reportlab đọc được file trên máy).
Giữ đúng hình tham số + kiểu trả về của bản cũ; tham số mới đều có mặc định; mọi lỗi trả {"ok": False, "error": …}, không ném lỗi.
Khi A gắn vào: thêm "tool_make_pdf" vào TOOLS và _GHI_FILE (trần "ghi một lần / lượt" bằng _mot_lan) của native_tools.py.
"""

import asyncio
import functools
import hashlib
import inspect
import io
import json
import logging
import math
import os
import re
import stat as _stat
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))
EXPORT_DIR = Path(os.environ.get("NATIVE_TOOLS_EXPORT_DIR", "/srv/xuat_file"))
# [ghi chú] dòng nguồn in ở bìa / phụ đề / thuộc tính file. Mặc định GIỮ NGUYÊN chữ cũ (khối Agent: AI soạn nội dung);
# va_xuat_ket_qua/dung_file.py đặt lại trước khi gọi vì số ở đó do CODE lấy từ bảng thưởng của lõi KPI, không qua AI.
NHAN_NGUON = "nội dung do AI soạn, file do code ghi"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")          # CỐ ĐỊNH trong code — không tham số nào của AI chạm tới đường phông
FONT_FILES = {"sans": "DejaVuSans.ttf", "sans_b": "DejaVuSans-Bold.ttf", "serif": "DejaVuSerif.ttf", "serif_b": "DejaVuSerif-Bold.ttf"}

# Trần (giữ trần bản cũ ở chỗ bản cũ có; thêm trần ở chỗ bản cũ chưa có)
MAX_SLIDES_IN = 30            # như bản cũ
MAX_ITEMS_PER_SLIDE = 20      # bản cũ 10 — nới vì giờ code tự tách trang
MAX_ITEM_CHARS = 600          # bản cũ 300 — nới vì giờ code tự giảm cỡ / tách trang
MAX_SLIDES_OUT = 60           # sau khi tách trang
MAX_TITLE_CHARS = 300         # bản cũ cắt lặng lẽ ở 200 ⇒ giờ báo lỗi khi quá 300, không cắt
MAX_DOAN, MAX_DOAN_CHARS = 200, 5000   # như bản cũ (make_docx)
MAX_COLS, MAX_ROWS = 30, 1000          # như make_xlsx; PDF riêng: tối đa MAX_PDF_COLS cột
MAX_PDF_COLS = 15
MAX_PDF_PAGES = 60
MAX_PDF_CELL = 1500
MIN_BODY_PT, MIN_TITLE_PT = 12, 20     # A yêu cầu 05/11: thân ≥ 12pt, tiêu đề ≥ 20pt (slide); PDF / Word: thân 12pt, chữ trong bảng ≥ 10pt (ghi BAO_CAO)

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ------------------------------------------------------------------ bộ màu: AI chỉ chọn TÊN; tên lạ ⇒ bộ mặc định + cảnh báo
# dam: màu chính (nền trang bìa, chữ tiêu đề, vòng tròn có số trắng) — phải đậm; nhat: nền thẻ; nhan: số lớn / điểm nhấn trên nền sáng.
BO_MAU = {
    "than_chi":  {"mo_ta": "trung tính — công nghệ, pháp lý, nội bộ",            "dam": "36454F", "nhat": "F2F2F2", "nhan": "C2410C"},
    "dem_xanh":  {"mo_ta": "điều hành, tài chính, ngân hàng",                     "dam": "1E2761", "nhat": "EAF0FD", "nhan": "3557C4"},
    "rung_reu":  {"mo_ta": "nông nghiệp, môi trường, thực phẩm sạch",             "dam": "2C5F2D", "nhat": "EEF5E4", "nhan": "5E8C1F"},
    "san_ho":    {"mo_ta": "marketing, bán lẻ, sản phẩm trẻ",                     "dam": "2F3C7E", "nhat": "FFF0F0", "nhan": "D9363E"},
    "dat_nung":  {"mo_ta": "ẩm thực, du lịch, thủ công",                          "dam": "8A3B2E", "nhat": "F6ECE8", "nhan": "B85042"},
    "dai_duong": {"mo_ta": "xuất nhập khẩu, logistics, thuỷ sản",                 "dam": "0B3C5D", "nhat": "E6F1F6", "nhan": "1C7293"},
    "xanh_ngoc": {"mo_ta": "y tế, bảo hiểm, chăm sóc khách hàng",                 "dam": "01606B", "nhat": "E3F4F2", "nhan": "008A7A"},
    "dau_kem":   {"mo_ta": "thời trang, mỹ phẩm, làm đẹp",                        "dam": "6D2E46", "nhat": "F6EEF0", "nhan": "A26769"},
    "anh_dao":   {"mo_ta": "chính sách, thuế quan, cảnh báo rủi ro",              "dam": "990011", "nhat": "FCF1F0", "nhan": "2F3C7E"},
}
# [ghi chú] 10 chủ đề lấy MÀU từ skill theme-factory của Anthropic (Apache License 2.0 — tài liệu kỹ thuật
# skills/theme-factory/LICENSE.txt, themes/*.md). Chọn bằng code (tương phản WCAG): "dam" = màu có tương phản ≥ 4,5 với chữ trắng (ô tiêu đề),
# "nhat" = màu sáng nhất (nền sọc, chữ 222222 ≥ 8), "nhan" = màu nhấn ≥ 3 với nền trắng. PHÔNG KHÔNG lấy theo chủ đề (FreeSans / FreeSerif không có
# trên máy văn phòng ⇒ bị thay phông, dễ vỡ dấu) — giữ phông đã kiểm tiếng Việt của từng loại file.
BO_MAU.update({
    "arctic_frost":      {"mo_ta": "lạnh, rõ ràng — y tế, công nghệ sạch (theme-factory)",            "dam": "4A6FA5", "nhat": "FAFAFA", "nhan": "4A6FA5"},
    "botanical_garden":  {"mo_ta": "tươi, tự nhiên — thực phẩm, làm vườn, sự kiện (theme-factory)",   "dam": "4A7C59", "nhat": "F5F3ED", "nhan": "B7472A"},
    "desert_rose":       {"mo_ta": "ấm, mềm — thời trang, làm đẹp, nội thất (theme-factory)",         "dam": "5D2E46", "nhat": "E8D5C4", "nhan": "B87D6D"},
    "forest_canopy":     {"mo_ta": "vững, trầm — môi trường, bền vững, ngoài trời (theme-factory)",   "dam": "2D4A2B", "nhat": "FAF9F6", "nhan": "7D8471"},
    "golden_hour":       {"mo_ta": "ấm, mùa thu — nhà hàng, thủ công, khách sạn (theme-factory)",     "dam": "4A403A", "nhat": "D4B896", "nhan": "C1666B"},
    "midnight_galaxy":   {"mo_ta": "sâu, trang trọng — giải trí, game, sang trọng (theme-factory)",   "dam": "2B1E3E", "nhat": "E6E6FA", "nhan": "4A4E8F"},
    "modern_minimalist": {"mo_ta": "tối giản, xám — công nghệ, kiến trúc, nội bộ (theme-factory)",     "dam": "36454F", "nhat": "FFFFFF", "nhan": "708090"},
    "ocean_depths":      {"mo_ta": "chuyên nghiệp, điềm tĩnh — tài chính, tư vấn (theme-factory)",    "dam": "1A2332", "nhat": "F1FAEE", "nhan": "2D8B8B"},
    "sunset_boulevard":  {"mo_ta": "rực, năng động — marketing, sự kiện (theme-factory)",              "dam": "264653", "nhat": "E9C46A", "nhan": "E76F51"},
    "tech_innovation":   {"mo_ta": "sáng, hiện đại — khởi nghiệp, công nghệ (theme-factory)",          "dam": "0066FF", "nhat": "FFFFFF", "nhan": "1E1E1E"},
})
MAC_DINH_MAU = "than_chi"
CHU, CHU_PHU, VIEN = "222222", "666666", "BFBFBF"

KIEU_TRANG = ("bia", "gach_dau_dong", "hai_cot", "so_lon", "so_sanh", "dong_thoi_gian", "bang")


def _chon_mau(ten, canh_bao: list) -> tuple[str, dict]:
    """Tra TÊN trong từ điển cố định. Không bao giờ ghép tên vào đường dẫn / không đọc file theo tên."""
    k = ten.strip().lower() if isinstance(ten, str) else ""
    if k in BO_MAU:
        return k, BO_MAU[k]
    if ten not in (None, ""):
        canh_bao.append(f"chu_de_mau '{str(ten)[:40]}' không có trong danh sách ⇒ dùng '{MAC_DINH_MAU}'.")
    return MAC_DINH_MAU, BO_MAU[MAC_DINH_MAU]


def _safe_name(name: str, ext: str) -> Path:
    """Chỉ giữ chữ không dấu / số / gạch, thêm giờ + [C bản 4, 09/11] 6 ký tự ngẫu nhiên (2 lượt cùng giây cùng tên không còn trùng — D ghi nhận
    khi chấm v4); luôn nằm trong EXPORT_DIR (chặn ../)."""
    import secrets
    base = re.sub(r"[^0-9A-Za-z_-]+", "_", (name or "").strip())[:60].strip("_") or "file"
    p = (EXPORT_DIR / f"{datetime.now(VN_TZ).strftime('%Y%m%d_%H%M%S')}_{base}_{secrets.token_hex(3)}{ext}").resolve()
    if p.parent != EXPORT_DIR.resolve():
        raise ValueError("tên file không hợp lệ")
    return p


def _doc(p: Path) -> bytes:
    """[C bản 5, D 58.D m1] đọc file KHÔNG theo symlink (O_NOFOLLOW): p là symlink ⇒ OSError (ELOOP), không đọc lặng lẽ file đích nằm chỗ khác."""
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        return f.read()


def _sha(p: Path) -> str:
    return hashlib.sha256(_doc(p)).hexdigest()[:16]


def _tam(p: Path) -> Path:
    """[C bản 4] file tạm ẨN cùng thư mục (tên bắt đầu ".") — ghi xong mới gắn tên thật; người đọc thư mục không thấy file dở."""
    import secrets
    return p.with_name(f".{p.name}.{secrets.token_hex(4)}.tam")


_KHUON_TAM = re.compile(r"^\..+\.[0-9a-f]{8}\.tam$")    # đúng khuôn tên _tam() đặt — chỉ xoá file mang tên này
TAM_CU_GIAY = 3600                                       # file tạm sống vài giây ⇒ cũ hơn 1 giờ chắc chắn là sót (không phải lượt đang chạy)


def _don_tam_cu(thu_muc: Path, tuoi_giay: float = TAM_CU_GIAY) -> int:
    """[C bản 5, D 58.D m2] xoá file tạm ẩn còn SÓT: tiến trình bị giết cứng (SIGKILL / OOM) giữa lúc dựng ⇒ `finally` của _ghi_qua_tam không chạy.
    Chỉ xoá tên đúng khuôn _tam(), cũ hơn tuoi_giay (lượt khác đang ghi song song không bị xoá nhầm), không phải thư mục; không theo symlink
    (os.unlink xoá chính liên kết, không xoá đích). Lỗi đọc thư mục / xoá ⇒ bỏ qua (dọn rác không được làm hỏng lượt ghi). Trả số file đã xoá."""
    n = 0
    try:
        bay_gio = time.time()
        with os.scandir(thu_muc) as ds:
            for e in ds:
                if not _KHUON_TAM.match(e.name):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                    if bay_gio - st.st_mtime > tuoi_giay and not _stat.S_ISDIR(st.st_mode):
                        os.unlink(e.path)
                        n += 1
                except OSError:
                    pass
    except OSError:
        pass
    if n:
        logger.warning("[xuat_file] đã dọn %d file tạm sót (cũ hơn %ds) trong %s", n, int(tuoi_giay), thu_muc)
    return n


def _ghi_qua_tam(p: Path, dung) -> object:
    """[C bản 4] dung ghi file vào file tạm ẩn ⇒ gắn tên thật KHÔNG ĐÈ bằng os.link (đích đã có ⇒ FileExistsError, khác os.rename /
    save() đè lặng lẽ). File tạm LUÔN bị xoá trước khi hàm trả về — kể cả khi dựng file lỗi giữa chừng (B nhắc 09/11: không để file tạm nằm lại).
    [C bản 5, D 58.D m1] làm chặt:
      - file tạm tạo bằng os.open(O_CREAT | O_EXCL | O_NOFOLLOW): tên tạm đã có (kể cả là symlink) ⇒ FileExistsError, không bao giờ ghi theo symlink;
      - dung(f) nhận ĐỐI TƯỢNG FILE đã mở (không nhận đường dẫn) — openpyxl / python-pptx / python-docx save() đều nhận file;
      - os.link(follow_symlinks=False), rồi os.lstat(p) phải là FILE THƯỜNG cùng (st_dev, st_ino) với file tạm đã mở; khác ⇒ xoá p, báo lỗi;
      - quyền 0o644 (không 0o600): hộp ab-* chạy root, người trên WSL (uid 1000) phải đọc được file xuất — giống save() bản 4 (umask 022);
      - trước khi ghi: dọn file tạm sót (m2, _don_tam_cu)."""
    _don_tam_cu(p.parent)
    t = _tam(p)
    fd = os.open(t, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "wb") as f:
            kq = dung(f)
            f.flush()
            st = os.fstat(f.fileno())
        os.link(t, p, follow_symlinks=False)
        sp = os.lstat(p)
        if not _stat.S_ISREG(sp.st_mode) or (sp.st_dev, sp.st_ino) != (st.st_dev, st.st_ino):
            os.unlink(p)                       # p do chính os.link vừa tạo (link không đè) ⇒ xoá được, không đụng file của ai khác
            raise OSError("file vừa gắn tên không phải file tạm đã ghi (bị thay giữa chừng) — đã xoá, không trả file")
        return kq
    finally:
        t.unlink(missing_ok=True)


def _sach(x, canh_bao: list | None = None) -> str:
    """Chữ vào ⇒ chuỗi NFC, bỏ ký tự điều khiển (lxml / PDF từ chối, PostgreSQL từ chối NUL — bước 23). Ghi cảnh báo nếu có bỏ."""
    s = "" if x is None else str(x)
    s = unicodedata.normalize("NFC", s)
    s2 = _CTRL.sub("", s)
    if canh_bao is not None and len(s2) != len(s):
        canh_bao.append(f"bỏ {len(s) - len(s2)} ký tự điều khiển (không hiện được).")
    return s2


def _gio() -> str:
    return datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y")


# ------------------------------------------------------------------ đo chữ (dùng số đo phông DejaVu của reportlab)
_REG: dict = {}


def _font(kind: str) -> str:
    """Đăng ký phông DejaVu với reportlab (một lần) — đường phông cố định. kind ∈ FONT_FILES."""
    if kind not in _REG:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        name = "DV_" + kind
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / FONT_FILES[kind])))
        _REG[kind] = name
    return _REG[kind]


def _w(text: str, pt: float, bold=False, serif=False) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    return stringWidth(text, _font(("serif" if serif else "sans") + ("_b" if bold else "")), pt)


def _wrap(text: str, width_pt: float, pt: float, bold=False, serif=False) -> list[str]:
    """Ngắt dòng theo từ; từ dài hơn cả khung thì ngắt theo ký tự (không mất chữ)."""
    out = []
    for para in (text or "").split("\n"):
        line = ""
        for word in para.split(" "):
            cand = word if not line else line + " " + word
            if _w(cand, pt, bold, serif) <= width_pt:
                line = cand
                continue
            if line:
                out.append(line)
            while _w(word, pt, bold, serif) > width_pt and len(word) > 1:      # từ quá dài: cắt dần theo ký tự sang dòng sau
                k = len(word)
                while k > 1 and _w(word[:k], pt, bold, serif) > width_pt:
                    k -= 1
                out.append(word[:k])
                word = word[k:]
            line = word
        out.append(line)
    return out


def _h(text: str, width_in: float, pt: float, bold=False, serif=False, lead=1.2) -> float:
    """Chiều cao (inch) khi đặt chữ vào khung rộng width_in."""
    return len(_wrap(text, width_in * 72, pt, bold, serif)) * pt * lead / 72


# ================================================================== PPTX
SW, SH, LE = 13.333, 7.5, 0.6          # khổ 16:9 rộng; lề 0,6 inch (skill: ≥ 0,5)
CW = SW - 2 * LE                       # bề rộng vùng nội dung
PAD = 0.05                             # lề trong khung chữ (đặt rõ, không để mặc định)
F_TIEU_DE, F_THAN = "Cambria", "Calibri"
COVER_W = 8.6                          # bề rộng chữ trang bìa (hình tròn trang trí nằm bên phải, không chồng chữ)


class _KhongVua(Exception):
    """Một mục không vừa nổi một trang ở cỡ chữ nhỏ nhất ⇒ báo lỗi rõ thay vì cắt chữ."""


def _title_fit(title: str, width_in: float, start=36, max_lines=2) -> tuple[int, float]:
    for pt in range(start, MIN_TITLE_PT - 1, -2):
        n = len(_wrap(title, (width_in - 2 * PAD) * 72, pt, True, True))
        if n <= max_lines:
            return pt, n * pt * 1.2 / 72 + 2 * PAD
    n = len(_wrap(title, (width_in - 2 * PAD) * 72, MIN_TITLE_PT, True, True))
    if n > 6:
        raise _KhongVua("tiêu đề trang quá dài")
    return MIN_TITLE_PT, n * MIN_TITLE_PT * 1.2 / 72 + 2 * PAD


# "7 năm", "khoảng 3 tháng", "18 tháng", "2 quý" — độ dài thuần (không kèm năm / ngày cụ thể)
_KHOANG = re.compile(r"\s*(?:khoảng|gần|hơn|~)?\s*\d+(?:[.,]\d+)?\s*(?:năm|tháng|tuần|ngày|giờ|quý)\s*", re.I)
# 07/11 lần 3 (8f): AI ghi LỜI CAM KẾT CÁCH LÀM vào file ("Không bổ sung giả định hoặc số liệu ngoài dữ liệu được cung cấp.").
# Không tự xoá (câu có thể là nội dung thật, vd chú thích nguồn) ⇒ chỉ BÁO; mô tả công cụ dặn nói câu đó trong chat.
_CAM_KET = re.compile(r"không\s+(?:bổ\s+sung|thêm|đưa\s+thêm|tự\s+thêm)\b.{0,40}(?:số\s+liệu|giả\s+định|dữ\s+liệu|thông\s+tin|con\s+số)"
                      r"|(?:do|mà)\s+người\s+dùng\s+(?:đã\s+)?(?:cung\s+cấp|đưa)|ngoài\s+(?:phạm\s+vi\s+)?dữ\s+liệu\s+(?:được\s+)?(?:cung\s+cấp|đưa)", re.I)


def _bao_cam_ket(chu: list[str], canh_bao: list) -> None:
    for x in chu:
        if isinstance(x, str) and _CAM_KET.search(x):
            canh_bao.append(f"câu '{x.strip()[:60]}' giống LỜI CAM KẾT cách làm (dành cho câu trả lời chat), không phải nội dung cho người đọc file "
                            "— nên bỏ khỏi file.")


def _ghi_tham_so(fn):
    """07/11 (đã giao việc 9): mỗi lần gọi make_pdf / make_pptx / make_docx ghi THAM SỐ AI GỬI + canh_bao + kết quả vào
    <EXPORT_DIR>/thamso/<tên file>.thamso.json (lỗi: <giờ>_<ten_file>.loi.thamso.json) ⇒ lần sau biết AI tự chọn khuôn hay công cụ sửa giúp.
    Thư mục CON thamso/: thư mục xuất chỉ chứa file thật cho người dùng (kiem.py K4: gọi lỗi thì KHÔNG có file mới trong thư mục xuất).
    Các công cụ này không nhận khoá / mã khách ⇒ tham số chỉ là nội dung. Ghi hỏng thì chỉ log, KHÔNG làm hỏng công cụ.
    functools.wraps giữ tên / docstring / chữ ký ⇒ mô tả + khuôn tham số AI thấy không đổi (kiểm bằng kiem_mo_ta)."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    async def boc(*args, **kwargs):
        r = await fn(*args, **kwargs)
        try:
            ts = sig.bind_partial(*args, **kwargs).arguments
            ten = (r or {}).get("file") if isinstance(r, dict) else None
            thu_muc = EXPORT_DIR / "thamso"
            ten_loi = _safe_name(str(ts.get("ten_file", "")), ".loi.thamso.json").name
            p = thu_muc / (f"{ten}.thamso.json" if ten else ten_loi)
            ban = {"cong_cu": fn.__name__, "luc": datetime.now(VN_TZ).isoformat(timespec="seconds"), "tham_so_ai_gui": ts,
                   "ok": isinstance(r, dict) and r.get("ok"), "canh_bao": (r or {}).get("canh_bao") if isinstance(r, dict) else None,
                   "ket_qua": {k: v for k, v in r.items() if k != "canh_bao"} if isinstance(r, dict) else str(r)[:300]}
            chuoi = json.dumps(ban, ensure_ascii=False, indent=1, default=str)
            if len(chuoi) > 2_000_000:
                chuoi = json.dumps({**ban, "tham_so_ai_gui": f"(bỏ — {len(chuoi)} ký tự)"}, ensure_ascii=False, default=str)
            thu_muc.mkdir(parents=True, exist_ok=True)
            p.write_text(chuoi, encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.warning("không ghi được .thamso.json cho %s", fn.__name__, exc_info=True)
        return r
    return boc


def _sua_khuon(idx: int, kt: str, items: list[str], canh_bao: list) -> list[str]:
    """Lượt AI thật 06/11 (BAO_CAO.md mục 8): AI nhớ sai KHUÔN CHỮ của kiểu trang ⇒ code tự sửa chỗ chắc chắn, báo chỗ không chắc (không để AI canh).
    - so_lon "nhãn | số" (đảo) ⇒ đổi thành "số | nhãn" khi bên trái không có chữ số mà bên phải có;
    - so_lon THIẾU "|" (lượt AI thật lần 2, 06/11: "MFN trung bình: 12%") ⇒ tách ở ": " cuối; một bên là MỘT con số ⇒ đổi "số | nhãn"
      + báo; không thì giữ nguyên + báo. Mọi mục so_lon thiếu "|" đều báo;
    - hai_cot / so_sanh thiếu "---" ⇒ báo (khi dựng file, _doi_kieu đã đổi trang đó sang gạch đầu dòng trước khi gọi hàm này);
    - dấu "|" ở kiểu không dùng "|" ⇒ báo (chữ giữ nguyên, không mất)."""
    co_so = re.compile(r"\d")
    # "là một con số" = ngắn, chỉ số + đơn vị (12%, 4,8%, 696.000₫, 3 tỷ, x2…). Bên phải là câu có số ("Lộ trình về 0% trong 7 năm") thì KHÔNG đổi
    # (đổi sẽ đưa cả câu lên chữ to) — chỉ báo.
    # D kiểm 06/11 (bộ giữ kín mới): "con số" quá hẹp ⇒ mở rộng VỪA ĐỦ: dấu trước (~ ≈ < > + − ±, x, $ €), tối đa 2 đơn vị ("tỷ đồng",
    # "triệu USD"), "/năm"…, "+" sau ("10+"), "x" sau ("1,5x"). Vẫn là MỘT con số ngắn — câu có số không khớp.
    don_vi = r"(?:%|₫|đ|đồng|vnd|vnđ|usd|\$|€|eur|k|m|b|tr|tỷ|triệu|nghìn|ngàn|năm|tháng|tuần|ngày|giờ|phút|lần|x|điểm|người|khách|đơn|sp)"
    la_so = re.compile(rf"(?:[~≈<>+\-–−±]|x)?\s*[$€]?\s*\d[\d.,\s]*(?:\s*{don_vi}){{0,2}}(?:\s*/\s*(?:năm|tháng|tuần|ngày|giờ))?\s*\+?", re.I)
    if kt == "so_lon":
        out = []
        for it in items:
            if "|" not in it:
                # 06/11 lần 2: sửa "|" xong thì AI đổi sang ":" ⇒ bắt theo Ý ("mục số lớn phải có một con số"), không theo dấu của lần lỗi trước.
                # Chỉ tách ở ": " (có khoảng trắng sau) ⇒ giờ "10:30", tỉ số "1:2" không bị tách.
                m = re.match(r"^(.*\S)\s*:\s+(.+)$", it.strip())
                a, b = (m.group(1).strip(), m.group(2).strip()) if m else ("", "")
                if m and la_so.fullmatch(b) and not la_so.fullmatch(a):
                    out.append(f"{b} | {a}")
                    canh_bao.append(f"trang {idx + 1} (so_lon): mục '{it[:40]}' thiếu '|' (viết 'nhãn: số') ⇒ đã đổi thành 'số | nhãn'.")
                elif m and la_so.fullmatch(a):
                    out.append(f"{a} | {b}")
                    canh_bao.append(f"trang {idx + 1} (so_lon): mục '{it[:40]}' thiếu '|' (viết 'số: nhãn') ⇒ đã đổi thành 'số | nhãn'.")
                else:
                    out.append(it)
                    canh_bao.append(f"trang {idx + 1} (so_lon): mục '{it[:40]}' thiếu '|' và không tách được thành MỘT con số + nhãn ⇒ "
                                    "cả mục hiện thành chữ to; khuôn là 'số | nhãn'.")
                continue
            a, b = _split_pipe(it)
            # đổi khi BÊN PHẢI là một con số mà BÊN TRÁI không phải (kể cả trái có năm: "Năm 2024 | 4,8%" — lỗi thật 06/11)
            if b and la_so.fullmatch(b.strip()) and not la_so.fullmatch(a.strip()):
                out.append(f"{b} | {a}")
                canh_bao.append(f"trang {idx + 1} (so_lon): mục '{it[:40]}' đặt nhãn trước số ⇒ đã đổi thành 'số | nhãn'.")
            else:
                if b and not la_so.fullmatch(a.strip()) and not co_so.match(a.strip()):
                    canh_bao.append(f"trang {idx + 1} (so_lon): mục '{it[:40]}' bên trái không phải con số — khuôn là 'số | nhãn'.")
                out.append(it)
        return out
    if kt == "dong_thoi_gian":
        # 07/11 lượt AI thật lần 3 (BAO_CAO.md 8f): "2020 | …", "7 năm | …", "2024 | …" ⇒ "7 năm" là ĐỘ DÀI, đặt giữa hai mốc dễ đọc thành
        # "7 năm trước 2024". Code KHÔNG tự đổi (không biết mốc đúng là năm nào) ⇒ chỉ báo.
        for it in items:
            moc = _split_pipe(it)[0]
            if _KHOANG.fullmatch(moc):
                canh_bao.append(f"trang {idx + 1} (dong_thoi_gian): mốc '{moc[:30]}' là một KHOẢNG thời gian, không phải thời điểm — "
                                "ghi mốc thời điểm (vd năm kết thúc) hoặc đưa khoảng vào phần mô tả.")
    if kt in ("hai_cot", "so_sanh"):
        items = ["---" if re.fullmatch(r"\s*-{3,}\s*", x) else x for x in items]     # D 06/11: "  ---  ", "----" vẫn là dòng ngăn
    if kt in ("hai_cot", "so_sanh") and items and "---" not in items:
        canh_bao.append(f"trang {idx + 1} ({kt}): không có mục '---' ngăn hai cột; nên thêm '---'.")
    if kt not in ("so_lon", "bang", "dong_thoi_gian") and any("|" in x for x in items):
        canh_bao.append(f"trang {idx + 1} ({kt}): có dấu '|' — dấu này chỉ dùng cho so_lon / dong_thoi_gian / bang ⇒ sẽ hiện nguyên trên trang.")
    return items


def _doi_kieu(idx: int, kt: str, items: list[str], canh_bao: list) -> str:
    """hai_cot / so_sanh thiếu "---" ⇒ dựng thành gạch đầu dòng (đã duyệt 06/11, BAO_CAO.md 8d đề xuất b).
    Vì sao: không có dòng ngăn thì code không biết hai bên là gì; chia đôi theo số mục ra trang lệch (3 rủi ro ⇒ 2 ↔ 1, lượt AI thật 06/11 cả 2 lần).
    Tách riêng khỏi _sua_khuon để không đổi tham số / kiểu trả về của hàm đó."""
    if kt in ("hai_cot", "so_sanh") and items and not any(re.fullmatch(r"\s*-{3,}\s*", x) for x in items):
        canh_bao.append(f"trang {idx + 1} ({kt}): không có mục '---' ngăn hai cột ⇒ đã dựng thành gạch đầu dòng (không chia đôi lệch).")
        return "gach_dau_dong"
    return kt


def _tach_gach(dn: list[str], canh_bao: list) -> list[str]:
    """Lượt AI thật 06/11: AI gộp nhiều gạch đầu dòng vào MỘT phần tử nối bằng " - " ⇒ tách ra.
    D kiểm 06/11: tách nhầm một câu thường ⇒ CHỈ TÁCH KHI CHẮC: phần tử là gạch đầu dòng ("- " / "• " / "* " ở đầu), có ≥ 2 dấu nối (- – —)
    đứng giữa khoảng trắng, và MỌI phần đều có dạng "Nhãn: mô tả" (đúng dạng AI gộp 06/11). Còn lại mà vẫn có ≥ 2 dấu nối ⇒ CHỈ báo, không tách."""
    out = []
    noi = r"\s+[-–—]\s+"
    nhan = re.compile(r"[^\W\d_][^:\n]{1,60}:\s+\S")
    for x in dn:
        s = x.strip()
        la_gach = bool(re.match(r"^[-*•]\s+", s))
        than = re.sub(r"^[-*•]\s+", "", s)
        parts = [p.strip() for p in re.split(noi, than)]
        if len(parts) >= 3 and la_gach and all(nhan.match(p) and p[:1].isupper() for p in parts):
            out += ["- " + p for p in parts]
            canh_bao.append(f"đoạn '{s[:40]}' gộp {len(parts)} gạch đầu dòng bằng dấu nối ⇒ đã tách thành {len(parts)} gạch.")
        else:
            if len(parts) >= 3:
                canh_bao.append(f"đoạn '{s[:40]}' có {len(parts) - 1} dấu nối — nếu là nhiều ý thì mỗi ý nên là MỘT phần tử (không tự tách).")
            out.append(x)
    return out


def _split_pipe(s: str, n: int = 2) -> list[str]:
    parts = [x.strip() for x in s.split("|")]
    if len(parts) < n:
        return parts + [""] * (n - len(parts))
    return parts[: n - 1] + [" | ".join(parts[n - 1:])]       # thừa "|" ⇒ gộp vào phần cuối, không mất chữ


# ---- lập trang: mỗi hàm nhận (tiêu đề, các mục) ⇒ danh sách trang đã đo cỡ; không vừa ⇒ tách
def _content_top(title: str) -> tuple[int, float, float]:
    tpt, th = _title_fit(title, CW - 0.55)
    top = 0.45 + max(th, 0.5) + 0.3
    return tpt, th, top


BOTTOM = SH - 0.75                    # chừa chỗ số trang


def _plan_list(title, items, row_h_fn, sizes):
    """Chung cho các kiểu dạng danh sách: tìm cỡ lớn nhất cho cả nhóm vừa; không được thì ở cỡ nhỏ nhất lấy phần đầu vừa, phần còn lại sang trang sau."""
    tpt, th, top = _content_top(title)
    avail = BOTTOM - top
    pages, rest = [], list(items)
    while rest:
        chunk = rest
        for pt in sizes:
            if row_h_fn(chunk, pt) <= avail:
                pages.append({"items": chunk, "pt": pt})
                rest = rest[len(chunk):]
                break
        else:
            pt = sizes[-1]
            k = len(chunk)
            while k > 0 and row_h_fn(chunk[:k], pt) > avail:
                k -= 1
            if k == 0:
                raise _KhongVua("một mục quá dài, không vừa một trang kể cả ở cỡ chữ nhỏ nhất")
            pages.append({"items": chunk[:k], "pt": pt})
            rest = rest[k:]
    for p in pages:
        p.update(tpt=tpt, th=th, top=top)
    return pages


def _rows_h(chunk, pt, text_w=CW - 0.75):
    return sum(max(_h(x, text_w - 2 * PAD, pt) + 2 * PAD, 0.5) for x in chunk) + 0.22 * (len(chunk) - 1)


def _plan_bullets(title, items):
    return _plan_list(title, items, _rows_h, list(range(20, MIN_BODY_PT - 1, -1)))


def _cols_from(items):
    if "---" in items:
        i = items.index("---")
        L, R = items[:i], items[i + 1:]
        hl, hr = (L[0] if L else ""), (R[0] if R else "")
        return hl, L[1:], hr, R[1:]
    half = (len(items) + 1) // 2
    return "", items[:half], "", items[half:]


def _card_h(header, body, pt, w):
    inner = w - 0.7                   # thẻ chừa 0,3 inch mỗi bên + lề trong khung chữ
    h = 0.3 + (_h(header, inner, 20, True, True) + 0.15 if header else 0)
    h += sum(_h(x, inner - 0.3, pt) + 0.08 for x in body) + 0.3
    return h


def _plan_two(title, items):
    tpt, th, top = _content_top(title)
    avail = BOTTOM - top
    w = (CW - 0.4) / 2
    hl, L, hr, R = _cols_from(items)

    def fits(Lc, Rc):
        for pt in range(18, MIN_BODY_PT - 1, -1):
            if max(_card_h(hl, Lc, pt, w), _card_h(hr, Rc, pt, w)) <= avail:
                return pt
        return None

    out, todo = [], [(L, R)]
    while todo:
        Lc, Rc = todo.pop(0)
        pt = fits(Lc, Rc)
        if pt:
            out.append({"hl": hl, "L": Lc, "hr": hr, "R": Rc, "pt": pt, "tpt": tpt, "th": th, "top": top, "w": w})
            continue
        if len(Lc) <= 1 and len(Rc) <= 1:
            raise _KhongVua("một ô của trang hai cột quá dài")
        a, b = (len(Lc) + 1) // 2, (len(Rc) + 1) // 2
        todo[0:0] = [(Lc[:a], Rc[:b]), (Lc[a:], Rc[b:])]
    return out


def _plan_stats(title, items):
    tpt, th, top = _content_top(title)
    avail = BOTTOM - top
    pairs = [_split_pipe(x) for x in items]
    pages, i = [], 0
    while i < len(pairs):
        rem = len(pairs) - i
        for per in sorted({min(6, rem), *[x for x in (4, 3, 2, 1) if x <= rem]}, reverse=True):
            chunk = pairs[i:i + per]
            cols = 3 if len(chunk) in (3, 5, 6) else (2 if len(chunk) in (2, 4) else 1)
            rows = (len(chunk) + cols - 1) // cols
            cw = (CW - 0.4 * (cols - 1)) / cols
            ch = (avail - 0.3 * (rows - 1)) / rows
            plan = []
            ok = True
            iw = cw - 0.5 - 2 * PAD            # bề rộng chữ thật trong khung (khung rộng cw - 0.5, lề trong PAD mỗi bên)
            for v, lab in chunk:
                vpt = 60
                while vpt > 24 and _w(v, vpt, True) > iw * 72:
                    vpt -= 2
                vh = _h(v, iw, vpt, True)
                lpt = 16
                while lpt > MIN_BODY_PT and vh + 0.15 + _h(lab, iw, lpt) + 0.6 > ch:
                    lpt -= 1
                if vh + 0.15 + _h(lab, iw, lpt) + 0.6 > ch:
                    ok = False
                    break
                plan.append((v, lab, vpt, lpt))
            if ok:
                pages.append({"cells": plan, "cols": cols, "rows": rows, "cw": cw, "ch": ch, "tpt": tpt, "th": th, "top": top})
                i += len(chunk)
                break
        else:
            raise _KhongVua("một ô số lớn quá dài")
    return pages


def _plan_timeline(title, items):
    tpt, th, top = _content_top(title)
    avail = BOTTOM - top
    steps = [_split_pipe(x) if "|" in x else ["", x] for x in items]
    pages, i = [], 0
    while i < len(steps):
        rem = len(steps) - i
        for per in sorted({min(5, rem), *[x for x in (4, 3, 2, 1) if x <= rem]}, reverse=True):
            chunk = steps[i:i + per]
            sw = CW / len(chunk)
            ok, plan = True, []
            iw = sw - 0.3 - 2 * PAD
            for m, d in chunk:
                mpt = 18
                while mpt > 14 and _h(m, iw, mpt, True) > 0.9:
                    mpt -= 1
                dpt = 16
                while dpt > MIN_BODY_PT and _h(d, iw, dpt) > avail - 2.0:
                    dpt -= 1
                if _h(m, iw, mpt, True) > 0.9 or _h(d, iw, dpt) > avail - 2.0:
                    ok = False
                    break
                plan.append((m, d, mpt, dpt))
            if ok:
                pages.append({"steps": plan, "sw": sw, "tpt": tpt, "th": th, "top": top, "start": i})
                i += len(chunk)
                break
        else:
            raise _KhongVua("một bước của dòng thời gian quá dài")
    return pages


def _col_widths(rows, total, pt, pad):
    """Bề rộng cột (inch): mỗi cột ít nhất bằng TỪ dài nhất của nó ở cỡ pt + lề ô pad (không bẻ giữa chữ như "Nă/m", "202/4"),
    phần dư chia theo độ dài chữ. Tổng mức tối thiểu vượt khung (quá nhiều cột) ⇒ co đều — chữ vẫn đủ, chỉ ngắt dòng nhiều hơn."""
    n = len(rows[0])
    cap = total * 0.3                                       # từ cực dài (đường link) không được chiếm cả bảng — để nó tự ngắt
    minw = [min(cap, max(_w(wd, pt, i == 0) for i, r in enumerate(rows) for wd in (r[j].split() or [""])) / 72 + pad) for j in range(n)]
    s = sum(minw)
    if s >= total:
        return [x * total / s for x in minw]
    lens = [max(max(len(r[j]) for r in rows), 4) for j in range(n)]
    want = [max(total * L / sum(lens) - m, 0) for L, m in zip(lens, minw)]     # cột chữ dài "muốn" thêm bao nhiêu
    extra, tw = total - s, sum(want)
    return [m + (extra * x / tw if tw else extra / n) for m, x in zip(minw, want)]


def _plan_table(title, items, canh_bao):
    tpt, th, top = _content_top(title)
    avail = BOTTOM - top
    head = [x.strip() for x in items[0].split("|")]
    n = len(head)
    rows = []
    for k, r in enumerate(items[1:]):
        cells = [x.strip() for x in r.split("|")]
        if len(cells) > n:
            cells = cells[: n - 1] + [" | ".join(cells[n - 1:])]
            canh_bao.append(f"bảng '{title[:30]}': dòng {k + 1} thừa ô ⇒ gộp vào ô cuối.")
        rows.append(cells + [""] * (n - len(cells)))
    widths = _col_widths([head] + rows, CW, 20, 0.3)         # đo ở cỡ LỚN nhất có thể dùng (20pt)

    def rh(r, pt, bold=False):
        return max(_h(c, w - 0.2, pt, bold) for c, w in zip(r, widths)) + 0.2

    pages, rest = [], rows
    while True:
        for pt in range(20, MIN_BODY_PT - 1, -1):
            if rh(head, pt, True) + sum(rh(r, pt) for r in rest) <= avail:
                pages.append({"head": head, "rows": rest, "pt": pt, "widths": widths, "tpt": tpt, "th": th, "top": top})
                rest = []
                break
        else:
            pt = MIN_BODY_PT
            k, used = 0, rh(head, pt, True)
            while k < len(rest) and used + rh(rest[k], pt) <= avail:
                used += rh(rest[k], pt)
                k += 1
            if k == 0:
                raise _KhongVua("một dòng bảng quá dài, không vừa một trang ở cỡ 12pt")
            pages.append({"head": head, "rows": rest[:k], "pt": pt, "widths": widths, "tpt": tpt, "th": th, "top": top})
            rest = rest[k:]
        if not rest:
            return pages


def _plan_cover(title, items):
    iw = COVER_W - 2 * PAD
    tpt = 44
    while tpt > MIN_TITLE_PT and _h(title, iw, tpt, True, True) > 3.0:
        tpt -= 2
    th = _h(title, iw, tpt, True, True)
    sub = "\n".join(items)
    spt = 20
    while spt > MIN_BODY_PT and th + 0.4 + _h(sub, iw, spt) + 0.1 * len(items) > 5.3:
        spt -= 1
    if th + 0.4 + _h(sub, iw, spt) + 0.1 * len(items) > 5.3:
        raise _KhongVua("trang bìa quá nhiều chữ")
    return [{"tpt": tpt, "th": th, "sub": sub, "spt": spt}]


# ---- vẽ
def _mix(a: str, b: str, t: float) -> str:
    """Pha màu a với b theo tỉ lệ t (0 = a, 1 = b) — tông dịu cùng họ màu, không thêm màu mới."""
    x, y = [int(a[i:i + 2], 16) for i in (0, 2, 4)], [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round(p + (q - p) * t):02X}" for p, q in zip(x, y))


def _rgb(h):
    from pptx.dml.color import RGBColor
    return RGBColor.from_string(h)


def _in(x):
    from pptx.util import Emu
    return Emu(int(x * 914400))


def _shape(slide, kind, x, y, w, h, fill, name):
    from pptx.enum.shapes import MSO_SHAPE
    s = slide.shapes.add_shape({"rect": MSO_SHAPE.RECTANGLE, "round": MSO_SHAPE.ROUNDED_RECTANGLE, "oval": MSO_SHAPE.OVAL}[kind],
                               _in(x), _in(y), _in(w), _in(h))
    s.name = name
    s.fill.solid()
    s.fill.fore_color.rgb = _rgb(fill)
    s.line.fill.background()
    try:
        s.shadow.inherit = False           # bỏ bóng mặc định của theme
    except AttributeError:
        pass
    if kind == "round":
        s.adjustments[0] = 0.08
    return s


def _bullet(p, color):
    """Gạch đầu dòng THẬT của PowerPoint (a:buChar) — skill: không gõ ký tự • vào chữ."""
    from pptx.oxml.ns import qn
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(int(0.25 * 914400)))
    pPr.set("indent", str(int(-0.25 * 914400)))
    for tag in ("a:buClr", "a:buFont", "a:buChar"):
        for old in pPr.findall(qn(tag)):
            pPr.remove(old)
    clr = pPr.makeelement(qn("a:buClr"), {})
    srgb = clr.makeelement(qn("a:srgbClr"), {"val": color})
    clr.append(srgb)
    pPr.append(clr)
    pPr.append(pPr.makeelement(qn("a:buFont"), {"typeface": "Arial"}))
    pPr.append(pPr.makeelement(qn("a:buChar"), {"char": "•"}))


def _text(slide, x, y, w, h, paras, name, anchor="top", shape=None):
    """paras: list of dict(text, pt, bold, color, font, align, bullet, after). Gán chữ theo run (skill: text_frame.text làm mất định dạng)."""
    from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
    from pptx.util import Pt
    tb = shape if shape is not None else slide.shapes.add_textbox(_in(x), _in(y), _in(w), _in(h))
    if shape is None:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = _in(PAD)
    tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}[anchor]
    for i, d in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}[d.get("align", "left")]
        if d.get("after") is not None:
            p.space_after = Pt(d["after"])
        if d.get("bullet"):
            _bullet(p, d["bullet"])
        r = p.add_run()
        r.text = d["text"]
        f = r.font
        f.size = Pt(d["pt"])
        f.bold = bool(d.get("bold"))
        f.name = d.get("font", F_THAN)
        f.color.rgb = _rgb(d.get("color", CHU))
    return tb


def _bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(color)


def _head(slide, pal, page, title):
    """Tiêu đề trang nội dung + vòng tròn nhỏ (điểm nhấn lặp lại cả bài — không dùng sọc / gạch chân)."""
    first_line_mid = 0.45 + PAD + page["tpt"] * 1.2 / 72 / 2
    _shape(slide, "oval", LE, first_line_mid - 0.16, 0.32, 0.32, pal["nhan"], "Diem nhan")
    _text(slide, LE + 0.55, 0.45, CW - 0.55, page["th"],
          [{"text": title, "pt": page["tpt"], "bold": True, "color": pal["dam"], "font": F_TIEU_DE}], "Title")


def _draw_cover(slide, pal, page, title, dark=True):
    _bg(slide, pal["dam"])
    _shape(slide, "oval", 9.9, 1.0, 3.0, 3.0, _mix(pal["dam"], "FFFFFF", 0.18), "Diem nhan bia")
    _shape(slide, "oval", 11.6, 4.6, 1.0, 1.0, _mix(pal["dam"], "FFFFFF", 0.45), "Diem nhan bia 2")
    y = 1.4
    _text(slide, 0.9, y, COVER_W, page["th"] + 0.2,
          [{"text": title, "pt": page["tpt"], "bold": True, "color": "FFFFFF", "font": F_TIEU_DE}], "Title")
    if page["sub"]:
        _text(slide, 0.9, y + page["th"] + 0.4, COVER_W, 5.3 - page["th"] - 0.2,
              [{"text": x, "pt": page["spt"], "color": pal["nhat"], "after": 6} for x in page["sub"].split("\n")], "Phu de")


def _draw_bullets(slide, pal, page):
    y = page["top"]
    for k, x in enumerate(page["items"]):
        hh = max(_h(x, CW - 0.75 - 2 * PAD, page["pt"]) + 2 * PAD, 0.5)
        c = _shape(slide, "oval", LE, y + (min(hh, 0.5) - 0.42) / 2 + 0.02, 0.42, 0.42, pal["dam"], f"So {k + 1}")
        _text(slide, 0, 0, 0, 0, [{"text": str(page["n0"] + k + 1), "pt": 14, "bold": True, "color": "FFFFFF", "align": "center"}],
              "", anchor="middle", shape=c)
        _text(slide, LE + 0.75, y, CW - 0.75, hh, [{"text": x, "pt": page["pt"], "color": CHU}], f"Dong {k + 1}",
              anchor="middle" if hh <= 0.5 else "top")
        y += hh + 0.22


def _draw_two(slide, pal, page, compare):
    w, y = page["w"], page["top"]
    h = BOTTOM - y
    for side, (hd, body) in enumerate(((page["hl"], page["L"]), (page["hr"], page["R"]))):
        x = LE + side * (w + 0.4)
        fill = ("F2F2F2" if side == 0 else pal["nhat"]) if compare else pal["nhat"]
        hc = ("555555" if side == 0 else pal["dam"]) if compare else pal["dam"]
        _shape(slide, "round", x, y, w, h, fill, f"The {side + 1}")
        paras = []
        if hd:
            paras.append({"text": hd, "pt": 20, "bold": True, "color": hc, "font": F_TIEU_DE, "after": 8})
        paras += [{"text": t, "pt": page["pt"], "color": CHU, "bullet": hc, "after": 6} for t in body]
        if paras:
            _text(slide, x + 0.3, y + 0.3, w - 0.6, h - 0.6, paras, f"Chu the {side + 1}")


def _draw_stats(slide, pal, page):
    for k, (v, lab, vpt, lpt) in enumerate(page["cells"]):
        r, c = divmod(k, page["cols"])
        x = LE + c * (page["cw"] + 0.4)
        y = page["top"] + r * (page["ch"] + 0.3)
        _shape(slide, "round", x, y, page["cw"], page["ch"], pal["nhat"], f"The so {k + 1}")
        iw = page["cw"] - 0.5 - 2 * PAD
        vh = _h(v, iw, vpt, True)
        lh = _h(lab, iw, lpt)
        y0 = y + (page["ch"] - vh - 0.15 - lh) / 2
        _text(slide, x + 0.25, y0, page["cw"] - 0.5, vh + 2 * PAD,
              [{"text": v, "pt": vpt, "bold": True, "color": pal["nhan"]}], f"So lon {k + 1}")
        if lab:
            _text(slide, x + 0.25, y0 + vh + 0.15, page["cw"] - 0.5, lh + 2 * PAD,
                  [{"text": lab, "pt": lpt, "color": CHU_PHU}], f"Nhan {k + 1}")


def _draw_timeline(slide, pal, page):
    from pptx.enum.shapes import MSO_CONNECTOR
    top, sw = page["top"], page["sw"]
    axis_y = top + 1.35
    ln = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, _in(LE + sw / 2), _in(axis_y), _in(LE + CW - sw / 2), _in(axis_y))
    ln.line.color.rgb = _rgb(VIEN)
    ln.line.width = _in(0.03)
    ln.name = "Truc thoi gian"
    for k, (m, d, mpt, dpt) in enumerate(page["steps"]):
        cx = LE + sw * k + sw / 2
        if m:
            _text(slide, cx - sw / 2 + 0.15, top, sw - 0.3, 1.0,
                  [{"text": m, "pt": mpt, "bold": True, "color": pal["dam"], "align": "center"}], f"Moc {k + 1}", anchor="bottom")
        c = _shape(slide, "oval", cx - 0.3, axis_y - 0.3, 0.6, 0.6, pal["dam"], f"Buoc {k + 1}")
        _text(slide, 0, 0, 0, 0, [{"text": str(page["start"] + k + 1), "pt": 16, "bold": True, "color": "FFFFFF", "align": "center"}],
              "", anchor="middle", shape=c)
        _text(slide, cx - sw / 2 + 0.15, axis_y + 0.5, sw - 0.3, BOTTOM - axis_y - 0.5,
              [{"text": d, "pt": dpt, "color": CHU, "align": "center"}], f"Mo ta {k + 1}")


def _num_like(s: str) -> bool:
    return bool(re.fullmatch(r"[-+]?[\d.,\s]+(%|₫|đ|tỷ|triệu)?", s.strip())) and any(ch.isdigit() for ch in s)


def _draw_table(slide, pal, page):
    from pptx.util import Pt
    rows = [page["head"]] + page["rows"]
    heights = [max(_h(c, w - 0.2, page["pt"], i == 0) for c, w in zip(r, page["widths"])) + 0.2 for i, r in enumerate(rows)]
    gs = slide.shapes.add_table(len(rows), len(page["head"]), _in(LE), _in(page["top"]), _in(CW), _in(sum(heights)))
    gs.name = "Bang"
    t = gs.table
    t.first_row = True
    t.horz_banding = False
    for j, w in enumerate(page["widths"]):
        t.columns[j].width = _in(w)
    for i, r in enumerate(rows):
        t.rows[i].height = _in(heights[i])
        for j, v in enumerate(r):
            cell = t.cell(i, j)
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(pal["dam"] if i == 0 else ("FFFFFF" if i % 2 else pal["nhat"]))
            cell.margin_left = cell.margin_right = _in(0.1)
            cell.margin_top = cell.margin_bottom = _in(0.1)
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            from pptx.enum.text import PP_ALIGN
            p.alignment = PP_ALIGN.RIGHT if (i > 0 and _num_like(v)) else PP_ALIGN.LEFT
            run = p.add_run()
            run.text = v
            run.font.size = Pt(page["pt"])
            run.font.bold = i == 0
            run.font.name = F_THAN
            run.font.color.rgb = _rgb("FFFFFF" if i == 0 else CHU)


def _footer(slide, pal, k, total):
    _text(slide, SW - LE - 2.0, SH - 0.6, 2.0, 0.35, [{"text": f"{k} / {total}", "pt": 12, "color": CHU_PHU, "align": "right"}], "So trang")


# [ghi chú] biểu đồ PowerPoint do CODE vẽ (biểu đồ gốc của PowerPoint — python-pptx; sửa / xem số được trong PowerPoint).
# Mỗi biểu đồ là MỘT trang mới chèn SAU trang thứ "trang" của slides (0 = sau bìa) ⇒ không đè bố cục có sẵn (thống nhất với B 09/11).
# GIỚI HẠN (đã chốt 09/11, Q3): số trong biểu đồ PPT do AI đưa — CHƯA kiểm khớp với kết quả công cụ của lượt. (Excel: biểu đồ trỏ ô bảng nên không có lỗ này.)
BD_MAX, BD_MAX_DIEM, BD_MAX_DIEM_TRON = 6, 12, 8


def _kiem_bieu_do_pptx(bieu_do, so_trang: int) -> list:
    if bieu_do is None:
        return []
    if not isinstance(bieu_do, list) or len(bieu_do) > BD_MAX:
        raise ValueError(f"bieu_do tối đa {BD_MAX} biểu đồ")
    out = []
    for i, x in enumerate(bieu_do):
        if not isinstance(x, dict) or set(x) - {"trang", "loai", "nhan", "gia_tri", "tieu_de", "don_vi"}:
            raise ValueError(f"bieu_do[{i}] phải là object với khoá trang, loai, nhan, gia_tri, tieu_de, don_vi")
        loai = str(x.get("loai") or "").strip().lower()
        if loai not in ("cot", "tron"):
            raise ValueError(f"bieu_do[{i}].loai phải là 'cot' hoặc 'tron'")
        tr = x.get("trang", so_trang)
        if not isinstance(tr, int) or isinstance(tr, bool) or not 0 <= tr <= so_trang:
            raise ValueError(f"bieu_do[{i}].trang phải là số nguyên 0–{so_trang} (0 = ngay sau bìa)")
        nhan, gia = x.get("nhan"), x.get("gia_tri")
        tran = BD_MAX_DIEM_TRON if loai == "tron" else BD_MAX_DIEM
        if not isinstance(nhan, list) or not isinstance(gia, list) or not 1 <= len(nhan) <= tran or len(nhan) != len(gia):
            raise ValueError(f"bieu_do[{i}]: nhan và gia_tri phải là 2 danh sách cùng độ dài, 1–{tran} điểm")
        if not all(_la_so(v) for v in gia):
            raise ValueError(f"bieu_do[{i}]: gia_tri phải toàn là số (như công cụ trả, không thêm chữ / dấu)")
        if loai == "tron" and (any(v < 0 for v in gia) or not sum(gia) > 0):
            raise ValueError(f"bieu_do[{i}]: biểu đồ tròn cần số không âm, tổng > 0")
        nh = [_sach(v).strip() for v in nhan]
        if any(not v for v in nh):
            raise ValueError(f"bieu_do[{i}]: có nhãn rỗng — biểu đồ sẽ thiếu tên điểm")
        if any(len(v) > 40 for v in nh):
            raise ValueError(f"bieu_do[{i}]: nhãn tối đa 40 ký tự — rút gọn (hệ thống không cắt)")
        td = _sach(x.get("tieu_de")).strip()
        if not td or len(td) > 120:
            raise ValueError(f"bieu_do[{i}].tieu_de cần 1–120 ký tự")
        dv = _sach(x.get("don_vi")).strip() if x.get("don_vi") else ""
        if len(dv) > 20:
            raise ValueError(f"bieu_do[{i}].don_vi tối đa 20 ký tự")
        out.append({"trang": tr, "loai": loai, "nhan": nh, "gia_tri": list(gia), "tieu_de": td, "don_vi": dv,
                    "so_le": min(max(_so_le(v) for v in gia), 6)})
    return out


def _draw_chart(slide, pal, page):
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
    from pptx.util import Pt
    bd = page["chart"]
    cd = CategoryChartData()
    cd.categories = bd["nhan"]
    cd.add_series(bd["don_vi"] or bd["tieu_de"], bd["gia_tri"])
    fmt = ("#,##0." + "0" * bd["so_le"]) if bd["so_le"] else "#,##0"   # hiện ĐÚNG số lẻ như số đưa vào — không làm tròn thêm
    dv = bd["don_vi"].replace('"', "").replace("\\", "")
    fmt_nhan = f'{fmt} "{dv}"' if dv else fmt   # D v4 B08 (bản 3): nhãn số kèm đơn vị (vd 1.050.000 ₫, 60,34 %)
    top = page["top"]
    gf = slide.shapes.add_chart(XL_CHART_TYPE.PIE if bd["loai"] == "tron" else XL_CHART_TYPE.COLUMN_CLUSTERED,
                                _in(LE), _in(top), _in(CW), _in(BOTTOM - top - 0.1), cd)
    gf.name = "Bieu do"
    ch = gf.chart
    ch.font.size, ch.font.name = Pt(14), F_THAN
    ch.has_title = True   # D v4 B08 (bản 3): tiêu đề RIÊNG của biểu đồ (không để PowerPoint tự lấy tên chuỗi, vd "₫"); còn đọc được khi chép biểu đồ ra ngoài
    ch.chart_title.text_frame.text = bd["tieu_de"]
    ch.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(16)
    ch.chart_title.text_frame.paragraphs[0].runs[0].font.bold = True
    pl = ch.plots[0]
    pl.has_data_labels = True
    dl = pl.data_labels
    dl.number_format, dl.number_format_is_linked = fmt_nhan, False
    dl.font.size = Pt(13)
    if bd["loai"] == "tron":
        ch.has_legend = True
        ch.legend.position, ch.legend.include_in_layout = XL_LEGEND_POSITION.RIGHT, False
        dl.show_percentage, dl.show_value = True, True
        dl.position = XL_LABEL_POSITION.OUTSIDE_END
        mau = [pal["dam"], pal["nhan"], _mix(pal["dam"], "FFFFFF", 0.35), _mix(pal["nhan"], "FFFFFF", 0.35),
               _mix(pal["dam"], "FFFFFF", 0.6), _mix(pal["nhan"], "FFFFFF", 0.6), CHU_PHU, _mix(CHU_PHU, "FFFFFF", 0.5)]
        for k, pt in enumerate(pl.series[0].points):
            pt.format.fill.solid()
            pt.format.fill.fore_color.rgb = _rgb(mau[k % len(mau)])
    else:
        ch.has_legend = False
        dl.position = XL_LABEL_POSITION.OUTSIDE_END
        pl.gap_width = 80
        sr = pl.series[0]
        sr.format.fill.solid()
        sr.format.fill.fore_color.rgb = _rgb(pal["dam"])
        ax = ch.value_axis
        ax.has_major_gridlines = True
        ax.major_gridlines.format.line.color.rgb = _rgb("E5E5E5")
        ax.tick_labels.number_format, ax.tick_labels.number_format_is_linked = fmt, False
        ax.tick_labels.font.size = Pt(12)
        ch.category_axis.tick_labels.font.size = Pt(12)
        if bd["don_vi"]:   # D v4 B08: trục giá trị có tên (đơn vị)
            ax.has_title = True
            ax.axis_title.text_frame.text = bd["don_vi"]
            ax.axis_title.text_frame.paragraphs[0].runs[0].font.size = Pt(12)


def _build_pptx(f, tieu_de: str, slides: list[list[str]], kieu: list[str], pal: dict, phu_de: str, canh_bao: list,
                bieu_do: list | None = None) -> dict:
    from pptx import Presentation
    plans = []                                    # (kieu, tiêu đề, trang đã đo)
    cover_sub = [phu_de] if phu_de else []
    plans.append(("bia", tieu_de, _plan_cover(tieu_de, cover_sub + [f"Tạo lúc {_gio()} — {NHAN_NGUON}"])[0]))
    bieu_do = bieu_do or []
    plans += [("bieu_do", b["tieu_de"], {"chart": b}) for b in bieu_do if b["trang"] == 0]
    for idx, (s, kt) in enumerate(zip(slides, kieu)):
        kt = _doi_kieu(idx, kt, s[1:], canh_bao)
        title, items = s[0], _sua_khuon(idx, kt, s[1:], canh_bao)
        tm = title + " (tiếp 99)"          # đo bằng tiêu đề dài nhất (trang tiếp có thêm chữ) ⇒ không lấn vùng nội dung
        try:
            if kt == "bia":
                pages = _plan_cover(title, items)
            elif kt == "bang" and items:
                pages = _plan_table(tm, items, canh_bao)
            elif kt in ("hai_cot", "so_sanh") and items:
                pages = _plan_two(tm, items)
            elif kt == "so_lon" and items:
                pages = _plan_stats(tm, items)
            elif kt == "dong_thoi_gian" and items:
                pages = _plan_timeline(tm, items)
            else:
                pages = _plan_bullets(tm, items) if items else [{"items": [], "pt": 20, **dict(zip(("tpt", "th", "top"), _content_top(tm)))}]
        except _KhongVua as e:
            raise _KhongVua(f"Trang {idx + 1} ('{title[:40]}'): {e} — rút gọn hoặc chia nhỏ mục đó.") from None
        n0 = 0
        for j, pg in enumerate(pages):
            pg["n0"] = n0
            n0 += len(pg.get("items", []))
            plans.append((kt, title if j == 0 else f"{title} (tiếp {j + 1})", pg))
        if len(pages) > 1:
            canh_bao.append(f"trang {idx + 1} dài ⇒ tách thành {len(pages)} trang (không cắt chữ).")
        plans += [("bieu_do", b["tieu_de"], {"chart": b}) for b in bieu_do if b["trang"] == idx + 1]
    if len(plans) > MAX_SLIDES_OUT:
        raise _KhongVua(f"sau khi tách trang ra {len(plans)} trang (> {MAX_SLIDES_OUT}) — rút gọn nội dung.")
    # Tiêu đề trang tiếp đã đổi ⇒ đo lại cỡ tiêu đề cho trang đó (vẫn ≥ 20pt)
    prs = Presentation()
    prs.slide_width, prs.slide_height = _in(SW), _in(SH)
    blank = prs.slide_layouts[6]
    sizes = []
    for k, (kt, title, pg) in enumerate(plans):
        sl = prs.slides.add_slide(blank)
        if kt == "bia":
            if k > 0:
                pg = _plan_cover(title, pg["sub"].split("\n") if pg["sub"] else [])[0]
            _draw_cover(sl, pal, pg, title)
            sizes += [pg["tpt"], pg["spt"]]
            continue
        _bg(sl, "FFFFFF")
        tpt, th, _top = _content_top(title)
        pg = {**pg, "tpt": tpt, "th": th}
        _head(sl, pal, pg, title)
        if kt == "bieu_do":
            _draw_chart(sl, pal, {**pg, "top": _top})
        elif kt == "bang" and "head" in pg:
            _draw_table(sl, pal, pg)
        elif kt in ("hai_cot", "so_sanh") and "L" in pg:
            _draw_two(sl, pal, pg, kt == "so_sanh")
        elif kt == "so_lon" and "cells" in pg:
            _draw_stats(sl, pal, pg)
            sizes += [c[3] for c in pg["cells"]]
        elif kt == "dong_thoi_gian" and "steps" in pg:
            _draw_timeline(sl, pal, pg)
            sizes += [s[3] for s in pg["steps"]]
        else:
            _draw_bullets(sl, pal, pg)
        sizes += [tpt, pg.get("pt", 20)]
        _footer(sl, pal, k + 1, len(plans))
    prs.save(f)        # [bản 5, D 68.D m3] f = ĐỐI TƯỢNG FILE đã mở bởi _ghi_qua_tam (không phải đường dẫn)
    return {"so_trang": len(plans), "kieu_trang_da_dung": [k for k, _t, _p in plans], "co_chu_nho_nhat": min(sizes) if sizes else None,
            "bieu_do": len(bieu_do)}


@_ghi_tham_so
async def make_pptx(ten_file: str, tieu_de: str, slides: list[list[str]], kieu_trang: list[str] | None = None,
                    chu_de_mau: str = MAC_DINH_MAU, phu_de: str | None = None, bieu_do: list[dict] | None = None) -> dict:
    """Ghi một bài trình chiếu PowerPoint (.pptx) có thiết kế: trang bìa + các trang nội dung, mỗi trang một kiểu bố cục.
    Bạn chỉ đưa NỘI DUNG; hệ thống tự dựng, tự canh cỡ chữ và tự tách trang khi chữ dài.
    Kết quả trả so_trang là số trang THẬT (kể cả bìa) và canh_bao là các chỗ hệ thống đã tự sửa; nói thật với người dùng nếu khác yêu cầu.
    Không tự tạo đường dẫn tải (link) cho file — hệ thống không cấp link; chỉ nói tên file (trường file), file nằm ở thư mục xuất.
    Chỉ ghi NỘI DUNG cho người đọc file; lời cam kết về cách làm (vd "không bổ sung số liệu ngoài dữ liệu được cung cấp") nói trong câu trả lời chat, không ghi vào file.

    Args:
        ten_file: tên ngắn cho file (chữ không dấu), vd "eu_tariff_impact".
        tieu_de: tiêu đề bài (trang bìa).
        slides: mỗi phần tử là MỘT trang: phần tử đầu là tiêu đề trang, các phần tử sau là nội dung (mỗi mục ≤ 600 ký tự, ≤ 20 mục / trang, tối đa 30 trang).
        kieu_trang: (tuỳ chọn) kiểu bố cục cho TỪNG trang, cùng thứ tự với slides. ĐỔI kiểu giữa các trang cho bài sinh động. Các kiểu:
            "gach_dau_dong" (mặc định) — các ý, mỗi ý một dòng có số thứ tự;
            "hai_cot" / "so_sanh" — hai thẻ cạnh nhau; BẮT BUỘC một mục "---" ngăn hai bên (thiếu thì hệ thống dựng thành gạch đầu dòng); mục đầu mỗi bên là tên cột, vd ["Trước", "ý 1", "---", "Sau", "ý 1"];
            "so_lon" — 1–6 con số nổi bật, mỗi mục "con số | nhãn" (SỐ đứng TRƯỚC dấu |, dùng dấu | chứ không dùng dấu :), vd "20% | thuế Mỹ áp từ 7/8/2025";
            "dong_thoi_gian" — các bước / mốc theo thứ tự, mỗi mục "mốc | mô tả", vd "Q1/2026 | Ký hợp đồng"; mốc là THỜI ĐIỂM (năm, quý, ngày), không phải khoảng ("7 năm" ⇒ ghi vào mô tả);
            "bang" — mục đầu là hàng tên cột "Cột A | Cột B", các mục sau là các dòng "giá trị | giá trị";
            "bia" — trang chuyển phần / kết luận nền đậm, các mục là dòng phụ.
            Dấu "|" chỉ dùng cho so_lon / dong_thoi_gian / bang; ở kiểu khác nó hiện nguyên trên trang. Danh sách rủi ro / ý ngang hàng ⇒ "gach_dau_dong" hoặc "bang".
        chu_de_mau: (tuỳ chọn) TÊN bộ màu hợp chủ đề: "than_chi" (trung tính, mặc định), "dem_xanh" (tài chính, điều hành),
            "rung_reu" (nông nghiệp, thực phẩm), "san_ho" (marketing, bán lẻ), "dat_nung" (ẩm thực, du lịch), "dai_duong" (xuất nhập khẩu, logistics),
            "xanh_ngoc" (y tế, chăm sóc khách hàng), "dau_kem" (thời trang, mỹ phẩm), "anh_dao" (chính sách, thuế quan, rủi ro).
            Thêm 10 chủ đề: "ocean_depths" (tài chính, tư vấn), "modern_minimalist" (tối giản), "tech_innovation" (công nghệ), "forest_canopy" (môi trường), "botanical_garden" (thực phẩm), "golden_hour" (nhà hàng), "desert_rose" (làm đẹp), "arctic_frost" (y tế), "midnight_galaxy" (giải trí), "sunset_boulevard" (sự kiện).
        phu_de: (tuỳ chọn) một dòng phụ đề trên trang bìa.
        bieu_do: (tuỳ chọn) biểu đồ cột / tròn do hệ thống vẽ, mỗi biểu đồ là MỘT trang mới chèn ngay sau trang thứ "trang" của slides (đếm từ 1; 0 = ngay sau bìa). Mỗi mục {"trang": số, "loai": "cot" hoặc "tron", "nhan": [các nhãn], "gia_tri": [các số đúng như công cụ trả, cùng độ dài với nhan], "tieu_de": chữ, "don_vi": chữ (tuỳ chọn, vd "₫")}. Tối đa 6 biểu đồ, 12 điểm (tròn: 8).
    """
    canh_bao: list[str] = []
    try:
        if not isinstance(slides, list) or not slides or len(slides) > MAX_SLIDES_IN:
            return {"ok": False, "error": f"Cần 1–{MAX_SLIDES_IN} trang."}
        if not all(isinstance(s, list) for s in slides):
            return {"ok": False, "error": "Mỗi trang phải là một danh sách chữ: [tiêu đề, mục 1, mục 2, …]."}
        xau = [i + 1 for i, s in enumerate(slides) if not s or len(s) > MAX_ITEMS_PER_SLIDE + 1 or any(len(str(x)) > MAX_ITEM_CHARS for x in s)]
        if xau:
            return {"ok": False, "error": f"Trang {xau[:5]} trống, quá {MAX_ITEMS_PER_SLIDE} mục hoặc có mục quá {MAX_ITEM_CHARS} ký tự."}
        td = _sach(tieu_de, canh_bao)
        if len(td) > MAX_TITLE_CHARS:
            return {"ok": False, "error": f"Tiêu đề bài quá {MAX_TITLE_CHARS} ký tự — rút gọn (không cắt lặng lẽ)."}
        sl = [[_sach(x, canh_bao) for x in s] for s in slides]
        if any(len(s[0]) > MAX_TITLE_CHARS for s in sl):
            return {"ok": False, "error": f"Có tiêu đề trang quá {MAX_TITLE_CHARS} ký tự."}
        kt_in = kieu_trang if isinstance(kieu_trang, list) else []
        if kieu_trang is not None and not isinstance(kieu_trang, list):
            canh_bao.append("kieu_trang không phải danh sách ⇒ bỏ qua.")
        if kt_in and len(kt_in) != len(sl):
            canh_bao.append(f"kieu_trang có {len(kt_in)} mục, slides có {len(sl)} trang ⇒ trang thiếu dùng 'gach_dau_dong'.")
        kieu = []
        for i in range(len(sl)):
            v = kt_in[i] if i < len(kt_in) else None
            k = v.strip().lower() if isinstance(v, str) else ""
            if k not in KIEU_TRANG:
                if v not in (None, ""):
                    canh_bao.append(f"trang {i + 1}: kieu_trang '{str(v)[:30]}' không có ⇒ 'gach_dau_dong'.")
                k = "gach_dau_dong"
            if k == "bang" and len(sl[i]) < 2:
                k = "gach_dau_dong"
            kieu.append(k)
        mau, pal = _chon_mau(chu_de_mau, canh_bao)
        pd = _sach(phu_de, canh_bao)[:MAX_ITEM_CHARS] if phu_de else ""
        _bao_cam_ket([x for t in sl for x in t[1:]], canh_bao)          # 8f: lời cam kết lọt vào file ⇒ báo (không xoá)
        if phu_de and len(_sach(phu_de)) > MAX_ITEM_CHARS:
            return {"ok": False, "error": f"phu_de quá {MAX_ITEM_CHARS} ký tự."}
        bd = _kiem_bieu_do_pptx(bieu_do, len(sl))   # [ghi chú]
        for b_ in bd:
            trung = sorted({v for v in b_["nhan"] if b_["nhan"].count(v) > 1})
            if trung:
                canh_bao.append(f"biểu đồ '{b_['tieu_de']}': nhãn trùng {trung[:3]} — người xem khó phân biệt điểm.")
        if bd:
            canh_bao.append(f"{len(bd)} biểu đồ: số do bạn đưa — hệ thống CHƯA đối chiếu với kết quả công cụ; kiểm lại số trước khi gửi.")
    except ValueError as e:   # [ghi chú] lỗi khuôn biểu đồ ⇒ báo rõ (trước đây mọi lỗi tham số chỉ báo tên lỗi)
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — công cụ không được làm hỏng lượt chạy
        return {"ok": False, "error": f"Tham số không hợp lệ: {type(e).__name__}."}

    def ghi() -> dict:
        p = _safe_name(ten_file, ".pptx")
        info = _ghi_qua_tam(p, lambda t: _build_pptx(t, td, sl, kieu, pal, pd, canh_bao, bd))
        return {"ok": True, "file": p.name, "so_trang": info["so_trang"], "sha256": _sha(p), "chu_de_mau": mau,
                "kieu_trang_da_dung": info["kieu_trang_da_dung"], "co_chu_nho_nhat": info["co_chu_nho_nhat"], "bieu_do": info["bieu_do"],
                "canh_bao": canh_bao}

    try:
        r = await asyncio.to_thread(ghi)
    except _KhongVua as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Không ghi được file: {type(e).__name__}."}
    logger.info("[NATIVE_TOOL] make_pptx file=%s slides=%s mau=%s", r.get("file"), r.get("so_trang"), mau)
    return r


# ================================================================== văn bản chung cho DOCX / PDF
def _parse_doan(x: str) -> tuple[str, str]:
    """Đánh dấu kiểu markdown đơn giản AI hay viết: "## " mục, "### " mục nhỏ, "- " / "* " / "• " gạch đầu dòng, "1. " đánh số."""
    s = x.strip()
    if s.startswith("### "):
        return "h3", s[4:].strip()
    if s.startswith("## ") or s.startswith("# "):
        return "h2", s.split(" ", 1)[1].strip()
    if re.match(r"^[-*•]\s+", s):
        return "bullet", re.sub(r"^[-*•]\s+", "", s)
    if re.match(r"^\d{1,3}[.)]\s+", s):
        return "number", s
    return "p", x


def _bold_parts(s: str) -> list[tuple[str, bool]]:
    """**đậm** ⇒ [(chữ, đậm?)]; dấu ** bị bỏ (chỉ là đánh dấu)."""
    parts = re.split(r"(\*\*[^*]+\*\*)", s)
    return [(p[2:-2], True) if p.startswith("**") and p.endswith("**") and len(p) > 4 else (p, False) for p in parts if p]


def _check_doc_args(tieu_de, doan, bang_cot, bang_dong, canh_bao, max_cols=MAX_COLS):
    if not isinstance(doan, list):
        return "doan phải là danh sách đoạn văn."
    if len(doan) > MAX_DOAN or any(len(str(x)) > MAX_DOAN_CHARS for x in doan):
        return "Tối đa 200 đoạn, mỗi đoạn 5.000 ký tự."
    if bang_cot and any((not isinstance(d, list)) or len(d) != len(bang_cot) for d in (bang_dong or [])):
        return "Có dòng bảng không đủ / thừa ô so với số cột."
    if bang_cot and len(bang_cot) > max_cols:
        return f"Bảng tối đa {max_cols} cột."
    if bang_dong and len(bang_dong) > MAX_ROWS:
        return f"Bảng tối đa {MAX_ROWS} dòng."
    if len(_sach(tieu_de)) > MAX_TITLE_CHARS:
        return f"Tiêu đề quá {MAX_TITLE_CHARS} ký tự — rút gọn (không cắt lặng lẽ)."
    return None


# ================================================================== DOCX
def _docx_shade(el, fill):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    pr = el.get_or_add_pPr() if hasattr(el, "get_or_add_pPr") else el.get_or_add_tcPr()
    for old in pr.findall(qn("w:shd")):
        pr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")              # skill docx: ShadingType CLEAR, không SOLID (ra màu đen)
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pr.append(shd)


def _docx_field(par, instr):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    r = par.add_run()
    for kind, text in (("begin", None), (None, instr), ("separate", None), (None, "1"), ("end", None)):
        if kind:
            e = OxmlElement("w:fldChar")
            e.set(qn("w:fldCharType"), kind)
        elif text == instr:
            e = OxmlElement("w:instrText")
            e.set(qn("xml:space"), "preserve")
            e.text = f" {instr} "
        else:
            e = OxmlElement("w:t")
            e.text = text
        r._r.append(e)
    return r


def _font_run(run, name, pt, bold=False, color=CHU):
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    run.font.name = name
    rpr = run._r.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = rpr.makeelement(qn("w:rFonts"), {})
        rpr.insert(0, rf)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rf.set(qn(a), name)
    run.font.size = Pt(pt)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def _docx_table_borders(t):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tblPr = t._tbl.tblPr
    b = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:val"), "single")
        e.set(qn("w:sz"), "4")
        e.set(qn("w:color"), VIEN)
        b.append(e)
    nxt = next((tblPr.find(qn(t)) for t in ("w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook") if tblPr.find(qn(t)) is not None), None)
    if nxt is not None:
        nxt.addprevious(b)                # Word đòi đúng thứ tự phần tử trong tblPr
    else:
        tblPr.append(b)


def _build_docx(f, tieu_de, doan, bang_cot, bang_dong, pal, phu_de):
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)                      # A4
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin, sec.bottom_margin = Cm(2.0), Cm(2.0)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = F_THAN, Pt(12)
    st.paragraph_format.space_after = Pt(6)
    st.paragraph_format.line_spacing = 1.15
    for name, pt in (("Heading 1", 26), ("Heading 2", 16), ("Heading 3", 13)):
        hs = doc.styles[name]
        hs.font.name, hs.font.size, hs.font.bold = F_TIEU_DE, Pt(pt), True
        hs.font.color.rgb = _docx_rgb(pal["dam"])
        hs.paragraph_format.space_before = Pt(14 if pt < 26 else 0)
        hs.paragraph_format.space_after = Pt(6)
        rpr = hs.element.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = rpr.makeelement(qn("w:rFonts"), {})
            rpr.insert(0, rf)
        for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rf.set(qn(a), F_TIEU_DE)
    # Khối tiêu đề: nền nhạt (skill: dùng nền / khoảng trắng, không gạch chân tiêu đề)
    h = doc.add_heading(level=1)
    _docx_shade(h._p, pal["nhat"])
    h.paragraph_format.space_after = Pt(0)
    for txt, bold in _bold_parts(tieu_de):
        _font_run(h.add_run(txt), F_TIEU_DE, 26, True, pal["dam"])
    sub = doc.add_paragraph()
    _docx_shade(sub._p, pal["nhat"])
    sub.paragraph_format.space_after = Pt(14)
    _font_run(sub.add_run((phu_de + " — ") if phu_de else ""), F_THAN, 12, False, CHU_PHU)
    _font_run(sub.add_run(f"Tạo lúc {_gio()} — {NHAN_NGUON}"), F_THAN, 10, False, CHU_PHU)
    for x in doan:
        kind, s = _parse_doan(x)
        if kind in ("h2", "h3"):
            par = doc.add_heading(level=2 if kind == "h2" else 3)
            for txt, _b in _bold_parts(s):
                _font_run(par.add_run(txt), F_TIEU_DE, 16 if kind == "h2" else 13, True, pal["dam"])
            continue
        par = doc.add_paragraph(style="List Bullet" if kind == "bullet" else None)
        for txt, b in _bold_parts(s):
            _font_run(par.add_run(txt), F_THAN, 12, b, CHU)
    if bang_cot:
        n = len(bang_cot)
        t = doc.add_table(rows=1, cols=n)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        _docx_table_borders(t)
        rows = [[_sach(c) for c in bang_cot]] + [["" if v is None else _sach(v) for v in d] for d in (bang_dong or [])]
        tpt = 10.5 if n > 5 else 11
        widths = [x * 2.54 for x in _col_widths(rows, (21 - 4.4) / 2.54, tpt, 0.25)]   # cm; ô Word có lề ~0,19 cm mỗi bên
        for i, r in enumerate(rows):
            cells = t.rows[0].cells if i == 0 else t.add_row().cells
            for j, v in enumerate(r):
                c = cells[j]
                c.width = Cm(widths[j])
                c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT if (i > 0 and _num_like(v)) else WD_ALIGN_PARAGRAPH.LEFT
                c.paragraphs[0].paragraph_format.space_after = Pt(0)
                _font_run(c.paragraphs[0].add_run(v), F_THAN, tpt, i == 0, "FFFFFF" if i == 0 else CHU)
                _docx_shade(c._tc, pal["dam"] if i == 0 else ("FFFFFF" if i % 2 else pal["nhat"]))
        trPr = t.rows[0]._tr.get_or_add_trPr()                              # lặp hàng tên cột ở mỗi trang
        e = OxmlElement("w:tblHeader")
        e.set(qn("w:val"), "true")
        trPr.append(e)
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font_run(fp.add_run(f"{tieu_de[:80]}  ·  Trang "), F_THAN, 9, False, CHU_PHU)
    _font_run(_docx_field(fp, "PAGE"), F_THAN, 9, False, CHU_PHU)
    _font_run(fp.add_run(" / "), F_THAN, 9, False, CHU_PHU)
    _font_run(_docx_field(fp, "NUMPAGES"), F_THAN, 9, False, CHU_PHU)
    doc.core_properties.title = tieu_de[:200]
    doc.save(f)        # [bản 5, D 68.D m3] f = đối tượng file đã mở bởi _ghi_qua_tam


def _docx_rgb(h):
    from docx.shared import RGBColor
    return RGBColor.from_string(h)


@_ghi_tham_so
async def make_docx(ten_file: str, tieu_de: str, doan: list[str], bang_cot: list[str] | None = None,
                    bang_dong: list[list[str | int | float | None]] | None = None,
                    chu_de_mau: str = MAC_DINH_MAU, phu_de: str | None = None) -> dict:
    """Ghi một tài liệu Word (.docx) có trình bày: khối tiêu đề, các đoạn văn, (tuỳ chọn) một bảng có màu, số trang ở chân trang.
    Dùng cho biên bản, thư, phiếu, hồ sơ gửi người khác.
    Không tự tạo đường dẫn tải (link) cho file — hệ thống không cấp link; chỉ nói tên file (trường file), file nằm ở thư mục xuất.
    Chỉ ghi NỘI DUNG cho người đọc file; lời cam kết về cách làm (vd "không bổ sung số liệu ngoài dữ liệu được cung cấp") nói trong câu trả lời chat, không ghi vào file.

    Args:
        ten_file: tên ngắn cho file (chữ không dấu).
        tieu_de: tiêu đề tài liệu.
        doan: các đoạn văn, mỗi phần tử một đoạn. Bắt đầu bằng "## " là tên mục, "- " là gạch đầu dòng; **chữ** là chữ đậm.
            Mỗi gạch đầu dòng là MỘT phần tử riêng — đừng nối nhiều ý bằng " - " trong một phần tử.
        bang_cot: (tuỳ chọn) tên cột của bảng.
        bang_dong: (tuỳ chọn) các dòng của bảng, cùng thứ tự với bang_cot.
        chu_de_mau: (tuỳ chọn) TÊN bộ màu, như make_pptx: "than_chi" (mặc định), "dem_xanh", "rung_reu", "san_ho", "dat_nung", "dai_duong",
            "xanh_ngoc", "dau_kem", "anh_dao", và 10 chủ đề "ocean_depths", "modern_minimalist", "tech_innovation", "forest_canopy",
            "botanical_garden", "golden_hour", "desert_rose", "arctic_frost", "midnight_galaxy", "sunset_boulevard".
        phu_de: (tuỳ chọn) một dòng phụ đề dưới tiêu đề.
    """
    canh_bao: list[str] = []
    try:
        err = _check_doc_args(tieu_de, doan, bang_cot, bang_dong, canh_bao)
        if err:
            return {"ok": False, "error": err}
        td = _sach(tieu_de, canh_bao)
        dn = _tach_gach([_sach(x, canh_bao) for x in doan], canh_bao)
        mau, pal = _chon_mau(chu_de_mau, canh_bao)
        pd = _sach(phu_de, canh_bao)[:MAX_ITEM_CHARS] if phu_de else ""
        _bao_cam_ket(dn, canh_bao)                                      # 8f
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Tham số không hợp lệ: {type(e).__name__}."}

    def ghi() -> dict:
        p = _safe_name(ten_file, ".docx")
        _ghi_qua_tam(p, lambda t: _build_docx(t, td, dn, bang_cot, bang_dong, pal, pd))
        return {"ok": True, "file": p.name, "so_doan": len(doan), "so_dong_bang": len(bang_dong or []), "sha256": _sha(p),
                "chu_de_mau": mau, "canh_bao": canh_bao}

    try:
        r = await asyncio.to_thread(ghi)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Không ghi được file: {type(e).__name__}."}
    logger.info("[NATIVE_TOOL] make_docx file=%s mau=%s", r.get("file"), mau)
    return r


# ================================================================== PDF
def _pdf_markup(s: str) -> str:
    """Chữ của AI ⇒ ESCAPE hết (< > &) rồi mới thêm thẻ <b> của mình. Không escape ⇒ AI chèn được <img src="/đường/dẫn"> (reportlab đọc file
    trên máy và nhúng vào PDF) hoặc <link href=…>; hoặc chỉ một dấu < làm hỏng cả file."""
    return "".join(f"<b>{escape(t)}</b>" if b else escape(t) for t, b in _bold_parts(s))


def _build_pdf(tieu_de, doan, bang_cot, bang_dong, pal, phu_de) -> tuple[bytes, int]:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    sans, sans_b, serif_b = _font("sans"), _font("sans_b"), _font("serif_b")
    _font("serif")
    registerFontFamily(sans, normal=sans, bold=sans_b, italic=sans, boldItalic=sans_b)
    C = lambda h: colors.HexColor("#" + h)  # noqa: E731
    n_cols = len(bang_cot or [])
    size = landscape(A4) if n_cols > 6 else A4
    body = ParagraphStyle("than", fontName=sans, fontSize=12, leading=16.5, textColor=C(CHU), spaceAfter=6, alignment=TA_LEFT)
    h2 = ParagraphStyle("muc", fontName=serif_b, fontSize=16, leading=20, textColor=C(pal["dam"]), spaceBefore=12, spaceAfter=6)
    h3 = ParagraphStyle("muc_nho", fontName=sans_b, fontSize=13, leading=17, textColor=C(pal["dam"]), spaceBefore=8, spaceAfter=4)
    bul = ParagraphStyle("gach", parent=body, leftIndent=16, bulletIndent=4, bulletFontName=sans, bulletColor=C(pal["nhan"]))
    t1 = ParagraphStyle("tieu_de", fontName=serif_b, fontSize=24, leading=30, textColor=C(pal["dam"]))
    t2 = ParagraphStyle("phu_de", fontName=sans, fontSize=11, leading=15, textColor=C(CHU_PHU))
    W = size[0] - 4 * cm

    story = []
    head = [[Paragraph(_pdf_markup(tieu_de), t1)],
            [Paragraph(_pdf_markup(((phu_de + " — ") if phu_de else "") + f"Tạo lúc {_gio()} — {NHAN_NGUON}"), t2)]]
    tb = Table(head, colWidths=[W])
    tb.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), sans), ("BACKGROUND", (0, 0), (-1, -1), C(pal["nhat"])), ("LEFTPADDING", (0, 0), (-1, -1), 16),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 16), ("TOPPADDING", (0, 0), (0, 0), 16), ("BOTTOMPADDING", (0, -1), (-1, -1), 14)]))
    story += [tb, Spacer(1, 14)]
    for x in doan:
        kind, s = _parse_doan(x)
        if kind == "h2":
            story.append(Paragraph(_pdf_markup(s), h2))
        elif kind == "h3":
            story.append(Paragraph(_pdf_markup(s), h3))
        elif kind == "bullet":
            story.append(Paragraph(_pdf_markup(s), bul, bulletText="•"))
        else:
            story.append(Paragraph(_pdf_markup(s).replace("\n", "<br/>"), body))
    if bang_cot:
        tpt = 10.5 if n_cols > 5 else 11
        cs = ParagraphStyle("o", fontName=sans, fontSize=tpt, leading=tpt * 1.3, textColor=C(CHU))
        cr = ParagraphStyle("o_so", parent=cs, alignment=TA_RIGHT)
        ch = ParagraphStyle("o_dau", parent=cs, fontName=sans_b, textColor=colors.white)
        rows = [[_sach(c) for c in bang_cot]] + [["" if v is None else _sach(v) for v in d] for d in (bang_dong or [])]
        widths = [x * 72 for x in _col_widths(rows, W / 72, tpt, 14 / 72)]     # ô có đệm 6pt mỗi bên (mặc định reportlab) + dư
        data = [[Paragraph(escape(v), ch if i == 0 else (cr if _num_like(v) else cs)) for v in r] for i, r in enumerate(rows)]
        t = Table(data, colWidths=widths, repeatRows=1)
        style = [("FONTNAME", (0, 0), (-1, -1), sans), ("BACKGROUND", (0, 0), (-1, 0), C(pal["dam"])), ("GRID", (0, 0), (-1, -1), 0.5, C(VIEN)),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
        style += [("BACKGROUND", (0, i), (-1, i), C(pal["nhat"])) for i in range(2, len(rows), 2)]
        t.setStyle(TableStyle(style))
        story += [Spacer(1, 10), t]

    footer_title = tieu_de[:80]

    class _Dem(rl_canvas.Canvas):
        """Ghi "Trang n / N" (cần biết tổng số trang ⇒ giữ trang lại, vẽ chân trang lúc lưu)."""
        def __init__(self, *a, **k):
            if not k.get("initialFontName"):          # SimpleDocTemplate truyền initialFontName=None ⇒ reportlab dùng Helvetica (không nhúng)
                k["initialFontName"] = sans
            if not k.get("initialFontSize"):
                k["initialFontSize"] = 12
            super().__init__(*a, **k)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._pages)
            _Dem.tong = n
            for st in self._pages:
                self.__dict__.update(st)
                self.setFont(sans, 9)
                self.setFillColor(C(CHU_PHU))
                self.drawCentredString(size[0] / 2, 1.1 * cm, f"{footer_title}  ·  Trang {self._pageNumber} / {n}")
                super().showPage()
            super().save()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                            title=tieu_de[:200], author="chay_thu — code ghi", subject=NHAN_NGUON.split(",")[0])
    doc.build(story, canvasmaker=_Dem)
    return buf.getvalue(), getattr(_Dem, "tong", 0)


@_ghi_tham_so
async def make_pdf(ten_file: str, tieu_de: str, doan: list[str], bang_cot: list[str] | None = None,
                   bang_dong: list[list[str | int | float | None]] | None = None,
                   chu_de_mau: str = MAC_DINH_MAU, phu_de: str | None = None) -> dict:
    """Ghi một tài liệu PDF có trình bày (khối tiêu đề, đoạn văn, (tuỳ chọn) một bảng có màu, số trang), chữ tiếng Việt có dấu hiển thị đúng.
    Dùng khi cần file chỉ để đọc / in / gửi, không cần người nhận sửa. Hệ thống không kéo dài cho đủ số trang:
    kết quả trả so_trang là số trang THẬT — nếu ít hơn số trang được yêu cầu thì nói thật với người dùng (hoặc viết thêm nội dung rồi ghi lại).
    Không tự tạo đường dẫn tải (link) cho file — hệ thống không cấp link; chỉ nói tên file (trường file), file nằm ở thư mục xuất.
    Chỉ ghi NỘI DUNG cho người đọc file; lời cam kết về cách làm (vd "không bổ sung số liệu ngoài dữ liệu được cung cấp") nói trong câu trả lời chat, không ghi vào file.

    Args:
        ten_file: tên ngắn cho file (chữ không dấu).
        tieu_de: tiêu đề tài liệu.
        doan: các đoạn văn, mỗi phần tử một đoạn. Bắt đầu bằng "## " là tên mục, "- " là gạch đầu dòng; **chữ** là chữ đậm.
            Mỗi gạch đầu dòng là MỘT phần tử riêng — đừng nối nhiều ý bằng " - " trong một phần tử.
        bang_cot: (tuỳ chọn) tên cột của bảng (tối đa 15 cột; quá 6 cột thì trang tự xoay ngang).
        bang_dong: (tuỳ chọn) các dòng của bảng, cùng thứ tự với bang_cot.
        chu_de_mau: (tuỳ chọn) TÊN bộ màu, như make_pptx.
        phu_de: (tuỳ chọn) một dòng phụ đề dưới tiêu đề.
    """
    canh_bao: list[str] = []
    try:
        err = _check_doc_args(tieu_de, doan, bang_cot, bang_dong, canh_bao, max_cols=MAX_PDF_COLS)
        if not err and any(len(str(v)) > MAX_PDF_CELL for d in (bang_dong or []) for v in d):
            err = f"Ô bảng tối đa {MAX_PDF_CELL} ký tự (ô dài hơn một trang thì PDF không dựng được)."
        if err:
            return {"ok": False, "error": err}
        td = _sach(tieu_de, canh_bao)
        dn = _tach_gach([_sach(x, canh_bao) for x in doan], canh_bao)
        mau, pal = _chon_mau(chu_de_mau, canh_bao)
        pd = _sach(phu_de, canh_bao)[:MAX_ITEM_CHARS] if phu_de else ""
        _bao_cam_ket(dn, canh_bao)                                      # 8f
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Tham số không hợp lệ: {type(e).__name__}."}

    def ghi() -> dict:
        data, n = _build_pdf(td, dn, bang_cot, bang_dong, pal, pd)
        if n > MAX_PDF_PAGES:                                                  # trần số trang: kiểm TRƯỚC khi ghi đĩa
            return {"ok": False, "error": f"Tài liệu ra {n} trang (> {MAX_PDF_PAGES}) — rút gọn nội dung."}
        p = _safe_name(ten_file, ".pdf")
        _ghi_qua_tam(p, lambda f: f.write(data))
        return {"ok": True, "file": p.name, "so_trang": n, "so_doan": len(doan), "so_dong_bang": len(bang_dong or []),
                "sha256": _sha(p), "chu_de_mau": mau, "canh_bao": canh_bao}

    try:
        r = await asyncio.to_thread(ghi)
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "LayoutError":
            return {"ok": False, "error": "Có một khối chữ / dòng bảng dài hơn một trang — chia nhỏ đoạn đó."}
        return {"ok": False, "error": f"Không ghi được file: {type(e).__name__}."}
    logger.info("[NATIVE_TOOL] make_pdf file=%s pages=%s mau=%s", r.get("file"), r.get("so_trang"), mau)
    return r


# Để A gắn vào native_tools.py: TOOLS cập nhật 3 mục dưới; _GHI_FILE thêm "tool_make_pdf".
TOOLS_MOI = {"tool_make_docx": make_docx, "tool_make_pptx": make_pptx, "tool_make_pdf": make_pdf}


# ================================================================== [ghi chú] make_xlsx BẢN 2 (X1)
# Thay make_xlsx thô ở native_tools.py (chỉ tiêu đề đậm). Nguyên tắc lấy từ skill xlsx của Anthropic (Apache 2.0, tài liệu kỹ thuật
# SKILL.md: phông chuyên nghiệp, công thức thật cho dòng tổng, ghi rõ nguồn số) + kết quả tra bên mekongai (căn phải số, cố định tiêu đề, dấu nghìn,
# không làm tròn tiền, nhãn trạng thái + nguồn số). Chiều dữ liệu: AI đưa SỐ ĐÃ CÓ từ công cụ + chọn kiểu cột; CODE dựng file, CODE cộng tổng.
# Khác skill: máy không có LibreOffice ⇒ không recalc ⇒ dòng tổng là công thức =SUM thật (Excel / LibreOffice tự tính khi mở, đặt fullCalcOnLoad)
# và CODE kiểm lại bằng cách ĐỌC LẠI file: lấy vùng trong công thức, cộng các ô số đã ghi ⇒ phải == tổng code tính; lệch ⇒ xoá file, báo lỗi.
from decimal import Decimal, InvalidOperation  # noqa: E402

KIEU_COT = ("chu", "so", "tien_vnd", "phan_tram", "ngay")
XLSX_MAX_SHEET, XLSX_MAX_COT, XLSX_MAX_DONG, XLSX_MAX_O, XLSX_MAX_CHU = 6, 30, 1000, 60_000, 2000
XLSX_MAX_GHI_CHU = 10
PHONG_XLSX = "Arial"            # có đủ dấu tiếng Việt trên Windows / macOS; skill xlsx khuyên phông chuyên nghiệp
_CAM_TEN_SHEET = re.compile(r"[\[\]:*?/\\]")
_SUM = re.compile(r"^=SUM\(([A-Z]{1,3})(\d+):([A-Z]{1,3})(\d+)\)$")


class _LechTong(Exception):
    """Đọc lại file: công thức dòng tổng không khớp tổng hệ thống ⇒ không giao file."""


def _so_le(v) -> int:
    """Số chữ số thập phân ĐÚNG như giá trị công cụ trả (repr ngắn nhất của float) — để hiện đúng, không làm tròn thêm (luật User 09/11)."""
    try:
        d = Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    except (InvalidOperation, ValueError):
        return 0
    e = d.normalize().as_tuple().exponent
    return max(0, -e) if isinstance(e, int) else 0


def _la_so(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


_DON_VI = re.compile(r"^[^\W\d_][\w$€£¥%/ .-]{0,9}$|^[$€£¥%]$")


def _dv_fmt(fm: str, dv: str | None) -> str:
    """Gắn đơn vị (đã kiểm khuôn) vào number_format: '#,##0' ⇒ '#,##0 "USD"' (cả phần âm)."""
    if not dv:
        return fm
    return ";".join(f'{x} "{dv}"' for x in fm.split(";"))


def _dinh_dang(kieu: str, v, canh_bao: list | None = None, dv: str | None = None) -> tuple:
    """(giá trị ghi vào ô, number_format) cho một ô. Lỗi khuôn ⇒ ValueError lời thường. Không cắt chữ (D v4 X10): quá trần ⇒ lỗi."""
    if v is None:
        return None, None
    if kieu == "chu":
        t = _sach(v, canh_bao)
        if len(t) > XLSX_MAX_CHU:
            raise ValueError(f"ô chữ dài {len(t)} ký tự, quá {XLSX_MAX_CHU} — rút gọn hoặc tách ô (hệ thống không cắt)")
        return t, "@"
    if kieu == "ngay":
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(v).strip())
        if not m:
            raise ValueError(f"ô ngày '{str(v)[:20]}' phải dạng YYYY-MM-DD")
        from datetime import date
        return date(int(m[1]), int(m[2]), int(m[3])), "dd/mm/yyyy"
    if not _la_so(v):
        raise ValueError(f"ô '{str(v)[:20]}' không phải số (cột kiểu {kieu}) — đưa số như công cụ trả, không thêm chữ / dấu")
    n = min(_so_le(v), 6)
    if kieu == "tien_vnd":
        if n:
            raise ValueError(f"tiền VNĐ phải là số nguyên đồng; nhận {v} — không làm tròn hộ")
        return v, '#,##0 "₫";-#,##0 "₫";0 "₫"'
    if kieu == "phan_tram":   # Q1 (A, 09/11): công cụ trả 89.74 nghĩa là 89,74 % ⇒ lưu 0.8974 (cộng / trung bình được), hiện đúng số lẻ
        return float(Decimal(repr(v) if isinstance(v, float) else v) / 100), ("0." + "0" * n + "%") if n else "0%"
    return v, _dv_fmt(("#,##0." + "0" * n) if n else "#,##0", dv)   # "so" (+ đơn vị do dữ liệu ghi, vd USD — bản 4)


def _ten_sheet(ten, da: set, canh_bao: list) -> str:
    t = _CAM_TEN_SHEET.sub("_", _sach(ten or "").strip())[:31].strip("'") or f"Sheet{len(da) + 1}"
    goc, i = t, 2
    while t.lower() in da:
        t = f"{goc[:28]}_{i}"
        i += 1
    if t != str(ten or "")[:31] and ten:
        canh_bao.append(f"tên sheet '{str(ten)[:31]}' đổi thành '{t}' (Excel cấm []:*?/\\, tối đa 31 ký tự, không trùng).")
    da.add(t.lower())
    return t


_NHAN_TONG = re.compile(r"^\s*(t[ổo]ng(\s+c[ộo]ng)?|c[ộo]ng|total|grand\s+total|sum)\b", re.I)


def _kiem_don_vi(ten_sheet, ten_cot, kieu, don_vi_cot) -> list:
    """[C bản 4] đơn vị theo cột — chỉ cho cột "so" (tien_vnd đã có ₫, phan_tram đã có %). Trả danh sách đơn vị theo cột (None = không có)."""
    don_vi_cot = don_vi_cot or {}
    if not isinstance(don_vi_cot, dict):
        raise ValueError(f"sheet '{ten_sheet}': don_vi_cot phải là object {{tên cột: đơn vị}}")
    out = [None] * len(ten_cot)
    for c, dv in don_vi_cot.items():
        c = _sach(c).strip()
        if c not in ten_cot:
            raise ValueError(f"sheet '{ten_sheet}': don_vi_cot có cột không tồn tại '{c[:40]}'")
        j = ten_cot.index(c)
        if kieu[j] != "so":
            raise ValueError(f"sheet '{ten_sheet}': don_vi_cot chỉ dùng cho cột kiểu 'so' (cột '{c}' kiểu {kieu[j]})")
        dv = _sach(dv).strip()
        if not _DON_VI.match(dv):
            raise ValueError(f"sheet '{ten_sheet}': đơn vị '{dv[:20]}' không hợp lệ (1–10 ký tự chữ, vd USD, kg, đơn)")
        out[j] = dv
    return out


def _bang_tong(v, cac: list) -> bool:
    """[C bản 5, D 58.D m3] v có bằng tổng các số trong cac (None = 0) không. Toàn số NGUYÊN ⇒ so tuyệt đối (cột tiền: không nới chút nào).
    Có số THỰC ⇒ math.fsum (cộng không cộng dồn sai số) + isclose(rel 1e-9, abs 1e-9): 0,1 + 0,2 vẫn khớp 0,3 ⇒ dòng TỔNG AI tự thêm bị chặn.
    Khi nào sai: hai số thực khác nhau thật nhưng lệch < 1e-9 tương đối ⇒ coi là bằng (chặn một dòng nhãn "Tổng" lẽ ra chỉ cảnh báo) — chấp nhận."""
    xs = [0 if x is None else x for x in cac]
    if isinstance(v, int) and all(isinstance(x, int) for x in xs):
        return v == sum(xs)
    return math.isclose(v, math.fsum(xs), rel_tol=1e-9, abs_tol=1e-9)


def _kiem_dong_tong_tu_them(ten_sheet, ten_cot, kieu, dong, canh_bao) -> None:
    """[C bản 4, MỤC 58] AI tự thêm hàng "TỔNG" vào dữ liệu (đúng chỗ "AI tự cộng" mà dòng tổng của hệ thống chặn). Bắt CHÍNH XÁC: nhãn chữ trông như
    tổng VÀ ít nhất một cột số bằng tổng các dòng còn lại ⇒ từ chối, chỉ cách dùng cot_can_tong. Chỉ nhãn giống (số không khớp) ⇒ cảnh báo."""
    so = [j for j, k in enumerate(kieu) if k in ("so", "tien_vnd")]
    chu = [j for j, k in enumerate(kieu) if k == "chu"]
    for i, d in enumerate(dong):
        if not any(isinstance(d[j], str) and _NHAN_TONG.match(unicodedata.normalize("NFC", d[j])) for j in chu):
            continue
        khac = [x for k, x in enumerate(dong) if k != i]
        khop = [ten_cot[j] for j in so if _la_so(d[j]) and khac and all(_la_so(x[j]) or x[j] is None for x in khac)
                and _bang_tong(d[j], [x[j] for x in khac])]
        if khop:
            raise ValueError(f"sheet '{ten_sheet}': dòng {i + 1} là dòng TỔNG bạn tự thêm (cột {khop[:3]} = tổng các dòng khác) — bỏ dòng đó, "
                             "dùng cot_can_tong để hệ thống tự cộng")
        canh_bao.append(f"sheet '{ten_sheet}': dòng {i + 1} có nhãn giống dòng tổng nhưng số không bằng tổng — giữ nguyên, kiểm lại.")


def _kiem_bang(ten_sheet, cot, dong, cot_can_tong, kieu_cot) -> tuple[list, dict]:
    """Kiểm khuôn MỘT bảng; trả (danh sách kiểu theo cột, tổng code tính theo cột). Lỗi ⇒ ValueError."""
    if not isinstance(cot, list) or not cot or len(cot) > XLSX_MAX_COT:
        raise ValueError(f"sheet '{ten_sheet}': cần 1–{XLSX_MAX_COT} cột")
    ten_cot = [_sach(c).strip() for c in cot]
    if any(not c for c in ten_cot) or len(set(ten_cot)) != len(ten_cot):
        raise ValueError(f"sheet '{ten_sheet}': tên cột rỗng hoặc trùng")
    if not isinstance(dong, list) or len(dong) > XLSX_MAX_DONG:
        raise ValueError(f"sheet '{ten_sheet}': tối đa {XLSX_MAX_DONG} dòng")
    lech = [i + 1 for i, d in enumerate(dong) if not isinstance(d, list) or len(d) != len(cot)]
    if lech:
        raise ValueError(f"sheet '{ten_sheet}': dòng {lech[:5]} không đủ / thừa ô so với {len(cot)} cột")
    kieu_cot = kieu_cot or {}
    la = [k for k in kieu_cot if k not in ten_cot]
    if la:
        raise ValueError(f"sheet '{ten_sheet}': kieu_cot có cột không tồn tại {la[:3]}")
    kieu = []
    for j, c in enumerate(ten_cot):
        k = kieu_cot.get(c)
        if k is None:   # không khai ⇒ toàn số thì "so", ngược lại "chu"
            gt = [d[j] for d in dong if d[j] is not None]
            k = "so" if gt and all(_la_so(x) for x in gt) else "chu"
        if k not in KIEU_COT:
            raise ValueError(f"sheet '{ten_sheet}': kiểu cột '{k}' không có; chọn trong {list(KIEU_COT)}")
        kieu.append(k)
    tong = {}
    for c in cot_can_tong or []:
        c = _sach(c).strip()
        if c not in ten_cot:
            raise ValueError(f"sheet '{ten_sheet}': không có cột '{c}' để cộng tổng")
        j = ten_cot.index(c)
        if kieu[j] not in ("so", "tien_vnd"):
            raise ValueError(f"sheet '{ten_sheet}': cột '{c}' kiểu {kieu[j]} — chỉ cộng tổng cột 'so' / 'tien_vnd' (cộng % là sai nghĩa)")
        chu = [i + 1 for i, d in enumerate(dong) if d[j] is not None and not _la_so(d[j])]
        if chu:
            raise ValueError(f"sheet '{ten_sheet}': cột '{c}' có ô không phải số ở dòng {chu[:5]}")
        tong[c] = sum(d[j] or 0 for d in dong)
    return kieu, tong


def _do_rong(chu: str) -> float:
    """Độ rộng cột Excel (đơn vị ~ một chữ số) — ước theo số ký tự hiện, chữ hoa / đậm rộng hơn; trần 60, sàn 8. Chống '#####' ở cột số."""
    if not chu:
        return 0
    return max(len(x) for x in chu.split("\n")) * 1.15 + 2


@_ghi_tham_so
async def make_xlsx(ten_file: str, cot: list[str], dong: list[list[str | int | float | None]],
                    cot_can_tong: list[str] | None = None, kieu_cot: dict[str, str] | None = None,
                    ten_sheet: str | None = None, tieu_de: str | None = None, ghi_chu: list[str] | None = None,
                    nhan_nhap: bool = False, sheet_them: list[dict] | None = None,
                    chu_de_mau: str = MAC_DINH_MAU, bieu_do: list[dict] | None = None,
                    don_vi_cot: dict[str, str] | None = None) -> dict:
    """Ghi bảng số ra file Excel (.xlsx) có trình bày: tiêu đề cột đậm có màu, cố định hàng tiêu đề, lọc, định dạng tiền / phần trăm / ngày,
    độ rộng cột tự đo, dòng TỔNG là công thức Excel thật (hệ thống tạo và tự kiểm lại), nhiều sheet, (tuỳ chọn) biểu đồ cột / tròn vẽ từ ô của bảng.
    Bạn chỉ đưa SỐ ĐÃ CÓ từ công cụ, đúng như công cụ trả — không tự tính, không làm tròn, không thêm chữ "đ" hay dấu chấm nghìn.
    Kết quả trả canh_bao là các chỗ hệ thống đã tự sửa hoặc cần lưu ý; nói thật với người dùng nếu khác yêu cầu. Lỗi (ok = false) thì báo lỗi, không nói là đã có file.
    Không tự tạo đường dẫn tải (link) cho file — hệ thống không cấp link; chỉ nói tên file (trường file), file nằm ở thư mục xuất. Mỗi lượt chỉ ghi một lần.
    Chỉ ghi NỘI DUNG cho người đọc file; lời cam kết về cách làm (vd "không bổ sung số liệu ngoài dữ liệu được cung cấp") nói trong câu trả lời chat, không ghi vào file.

    Args:
        ten_file: tên ngắn cho file (chữ không dấu), vd "thuong_doi_A_thang10".
        cot: tên các cột của sheet đầu, vd ["Mã NV", "Doanh số", "Tỷ lệ đạt", "Thưởng"].
        dong: các dòng của sheet đầu, mỗi dòng một danh sách cùng thứ tự với cot. Số để dạng số.
        cot_can_tong: (tuỳ chọn) tên các cột số cần dòng TỔNG — hệ thống ghi công thức =SUM và tự kiểm; bạn KHÔNG tự cộng.
        kieu_cot: (tuỳ chọn) kiểu từng cột: "tien_vnd" (số nguyên đồng, hiện 1.740.000 ₫ — CHỈ khi dữ liệu / công cụ ghi rõ là VNĐ; không rõ đơn vị thì dùng "so", không tự thêm ₫), "phan_tram" (đưa 89.74 nghĩa là 89,74 %), "so", "chu", "ngay" (YYYY-MM-DD). Cột không khai: toàn số thì "so", còn lại "chu".
        ten_sheet: (tuỳ chọn) tên sheet đầu.
        tieu_de: (tuỳ chọn) dòng tiêu đề lớn phía trên bảng sheet đầu.
        ghi_chu: (tuỳ chọn) các dòng ghi chú dưới bảng sheet đầu, vd "Nguồn số: bản tính KPI lúc 10:48 09/11".
        nhan_nhap: (tuỳ chọn) true ⇒ thêm nhãn "NHÁP — chưa duyệt" nổi bật ở đầu sheet đầu.
        sheet_them: (tuỳ chọn) các sheet thêm, mỗi mục {"ten_sheet", "cot", "dong", "cot_can_tong", "kieu_cot", "tieu_de", "ghi_chu"} cùng nghĩa như trên (tối đa 5).
        chu_de_mau: (tuỳ chọn) TÊN bộ màu như make_pptx: "than_chi" (mặc định), "dem_xanh", "rung_reu", "san_ho", "dat_nung", "dai_duong", "xanh_ngoc", "dau_kem", "anh_dao", "ocean_depths", "modern_minimalist", "tech_innovation", "forest_canopy", "botanical_garden", "golden_hour", "desert_rose", "arctic_frost", "midnight_galaxy", "sunset_boulevard".
        bieu_do: (tuỳ chọn) biểu đồ vẽ từ ô của bảng, mỗi mục {"loai": "cot" hoặc "tron", "sheet": tên sheet (bỏ trống = sheet đầu), "cot_nhan": tên cột làm nhãn, "cot_gia_tri": tên cột số, "tieu_de": chữ} (tối đa 4).
        don_vi_cot: (tuỳ chọn) đơn vị cho cột kiểu "so" ĐÚNG như dữ liệu ghi, vd {"Doanh thu": "USD"} — hiện "23.056.510 USD". Không rõ đơn vị thì bỏ trống. Không tự thêm dòng TỔNG vào dong — dùng cot_can_tong.
    """
    canh_bao: list[str] = []
    ten_mau, mau = _chon_mau(chu_de_mau, canh_bao)
    bang = [{"ten_sheet": ten_sheet, "cot": cot, "dong": dong, "cot_can_tong": cot_can_tong, "kieu_cot": kieu_cot,
             "tieu_de": tieu_de, "ghi_chu": ghi_chu, "nhan_nhap": bool(nhan_nhap), "don_vi_cot": don_vi_cot}]
    if sheet_them is not None and (not isinstance(sheet_them, list) or len(sheet_them) > XLSX_MAX_SHEET - 1):
        return {"ok": False, "error": f"sheet_them tối đa {XLSX_MAX_SHEET - 1} sheet."}
    for i, s in enumerate(sheet_them or []):
        if not isinstance(s, dict) or set(s) - {"ten_sheet", "cot", "dong", "cot_can_tong", "kieu_cot", "tieu_de", "ghi_chu", "don_vi_cot"}:
            return {"ok": False, "error": f"sheet_them[{i}] phải là object với các khoá ten_sheet, cot, dong, cot_can_tong, kieu_cot, tieu_de, ghi_chu, don_vi_cot."}
        bang.append({**{"ten_sheet": None, "cot": None, "dong": None, "cot_can_tong": None, "kieu_cot": None, "tieu_de": None, "ghi_chu": None,
                        "don_vi_cot": None}, **s, "nhan_nhap": False})
    da_ten: set = set()
    try:
        for b in bang:
            b["ten"] = _ten_sheet(b["ten_sheet"], da_ten, canh_bao)
            b["kieu"], b["tong"] = _kiem_bang(b["ten"], b["cot"], b.get("dong") or [], b["cot_can_tong"], b["kieu_cot"])
            b["dong"] = b.get("dong") or []
            ten_cot_ = [_sach(c).strip() for c in b["cot"]]
            b["dv"] = _kiem_don_vi(b["ten"], ten_cot_, b["kieu"], b.get("don_vi_cot"))
            _kiem_dong_tong_tu_them(b["ten"], ten_cot_, b["kieu"], b["dong"], canh_bao)
            if "tien_vnd" in b["kieu"]:
                canh_bao.append(f"sheet '{b['ten']}': cột tiền hiện '₫' — chỉ đúng nếu dữ liệu là VNĐ; nói rõ với người dùng nếu không chắc.")
            gc = b.get("ghi_chu") or []
            if not isinstance(gc, list) or len(gc) > XLSX_MAX_GHI_CHU:
                raise ValueError(f"sheet '{b['ten']}': ghi_chu tối đa {XLSX_MAX_GHI_CHU} dòng")
            b["ghi_chu"] = [_sach(x, canh_bao) for x in gc]
            if any(len(x) > 500 for x in b["ghi_chu"]):
                raise ValueError(f"sheet '{b['ten']}': mỗi dòng ghi_chu tối đa 500 ký tự (hệ thống không cắt)")
            for j, k in enumerate(b["kieu"]):   # Q1: hiện đúng số lẻ công cụ trả, TỐI ĐA 6 ⇒ quá 6 thì báo rõ (giá trị lưu vẫn đủ)
                nhieu = [d[j] for d in b["dong"] if k in ("so", "phan_tram") and _la_so(d[j]) and _so_le(d[j]) > 6]
                if nhieu:
                    canh_bao.append(f"sheet '{b['ten']}' cột '{_sach(b['cot'][j])}': {len(nhieu)} ô có hơn 6 chữ số lẻ — HIỆN 6 chữ số, giá trị lưu giữ nguyên.")
            b["tieu_de"] = _sach(b["tieu_de"], canh_bao) if b.get("tieu_de") else None
            if b["tieu_de"] and len(b["tieu_de"]) > MAX_TITLE_CHARS:
                raise ValueError(f"sheet '{b['ten']}': tieu_de quá {MAX_TITLE_CHARS} ký tự — rút gọn (hệ thống không cắt)")
        if sum(len(b["cot"]) * (len(b["dong"]) + 1) for b in bang) > XLSX_MAX_O:
            raise ValueError(f"tổng số ô quá {XLSX_MAX_O}")
        bd = _kiem_bieu_do_xlsx(bieu_do, bang)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    def ghi() -> dict:
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.properties import PageSetupProperties
        p = _safe_name(ten_file, ".xlsx")
        wb = Workbook()
        wb.remove(wb.active)
        f_tieu_de = Font(name=PHONG_XLSX, size=11, bold=True, color="FFFFFF")
        nen_tieu_de = PatternFill("solid", fgColor=mau["dam"])
        nen_soc = PatternFill("solid", fgColor=mau["nhat"])
        vien = Border(bottom=Side(style="thin", color=VIEN))
        vien_tong = Border(top=Side(style="medium", color=mau["dam"]))
        vi_tri = {}   # tên sheet ⇒ (hàng tiêu đề, hàng dữ liệu đầu, hàng dữ liệu cuối, danh sách cột)
        kiem = []
        for b in bang:
            ws = wb.create_sheet(b["ten"])
            ws.sheet_view.showGridLines = False
            r = 1
            if b["nhan_nhap"]:
                ws.cell(r, 1, "NHÁP — chưa duyệt").font = Font(name=PHONG_XLSX, size=12, bold=True, color="C00000")
                r += 1
            if b["tieu_de"]:
                ws.cell(r, 1, b["tieu_de"]).font = Font(name=PHONG_XLSX, size=14, bold=True, color=mau["dam"])
                r += 2
            h0 = r
            rong = [0.0] * len(b["cot"])
            for j, c in enumerate(b["cot"]):
                o = ws.cell(h0, j + 1, _sach(c).strip())
                o.font, o.fill, o.border = f_tieu_de, nen_tieu_de, vien
                o.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                rong[j] = _do_rong(str(o.value))
            for i, d in enumerate(b["dong"]):
                rr = h0 + 1 + i
                for j, v in enumerate(d):
                    gt, fm = _dinh_dang(b["kieu"][j], v, canh_bao, b["dv"][j])
                    o = ws.cell(rr, j + 1)
                    o.value = gt
                    if b["kieu"][j] == "chu" and gt is not None:
                        o.data_type = "s"   # chữ bắt đầu bằng "=" vẫn là CHỮ (không thành công thức) — không cần thêm dấu ' như bản cũ
                    if fm:
                        o.number_format = fm
                    o.font = Font(name=PHONG_XLSX, size=11)
                    o.alignment = Alignment(horizontal="left" if b["kieu"][j] == "chu" else "right", vertical="top",
                                            wrap_text=b["kieu"][j] == "chu")
                    if i % 2 == 1:
                        o.fill = nen_soc
                    hien = "" if gt is None else (f"{v:,}" + (f" {b['dv'][j]}" if b["dv"][j] else "") if b["kieu"][j] in ("so", "tien_vnd") else
                                                  (f"{v}%" if b["kieu"][j] == "phan_tram" else str(gt)))
                    rong[j] = max(rong[j], _do_rong(hien) + (2 if b["kieu"][j] == "tien_vnd" else 0))
            r_cuoi = h0 + len(b["dong"])
            if b["tong"]:
                rt = r_cuoi + 1
                nhan_o = next((j for j, c in enumerate(b["cot"]) if _sach(c).strip() not in b["tong"]), None)
                if nhan_o is not None:
                    ws.cell(rt, nhan_o + 1, "TỔNG").font = Font(name=PHONG_XLSX, size=11, bold=True)
                for j, c in enumerate(b["cot"]):
                    c = _sach(c).strip()
                    o = ws.cell(rt, j + 1)
                    o.border = vien_tong
                    if c in b["tong"]:
                        L = get_column_letter(j + 1)
                        o.value = f"=SUM({L}{h0 + 1}:{L}{max(r_cuoi, h0 + 1)})"
                        o.number_format = _dinh_dang(b["kieu"][j], 0, None, b["dv"][j])[1]
                        o.font = Font(name=PHONG_XLSX, size=11, bold=True)
                        o.alignment = Alignment(horizontal="right")
                        kiem.append((b["ten"], o.coordinate, b["tong"][c], c))   # vị trí theo chính ô (không theo chữ cột vừa tính)
                        rong[j] = max(rong[j], _do_rong(f"{b['tong'][c]:,}") + 2)
                r_cuoi_bang = rt
            else:
                r_cuoi_bang = r_cuoi
            for k, gc in enumerate(b["ghi_chu"]):
                ws.cell(r_cuoi_bang + 2 + k, 1, gc).font = Font(name=PHONG_XLSX, size=9, italic=True, color=CHU_PHU)
            for j, w in enumerate(rong):
                ws.column_dimensions[get_column_letter(j + 1)].width = max(8.0, min(60.0, w))
            ws.freeze_panes = ws.cell(h0 + 1, 1)
            if b["dong"]:
                ws.auto_filter.ref = f"A{h0}:{get_column_letter(len(b['cot']))}{r_cuoi}"
            ws.print_title_rows = f"{h0}:{h0}"
            ws.page_setup.orientation = "landscape" if len(b["cot"]) > 6 else "portrait"
            ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
            ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
            vi_tri[b["ten"]] = (h0, h0 + 1, r_cuoi, [_sach(c).strip() for c in b["cot"]])
        _ve_bieu_do_xlsx(wb, bd, vi_tri, mau, canh_bao)
        wb.calculation.fullCalcOnLoad = True   # chưa có giá trị đệm cho công thức ⇒ buộc Excel / LibreOffice tính khi mở
        _ghi_qua_tam(p, wb.save)
        # KIỂM LẠI BẰNG CODE: đọc lại file, lấy vùng trong từng công thức =SUM, cộng ô số đã ghi ⇒ == tổng code tính
        wb2 = load_workbook(io.BytesIO(_doc(p)))      # [bản 5, m1] đọc lại không theo symlink
        lech = []
        for sh, o, mong, c in kiem:
            ws2 = wb2[sh]
            m = _SUM.match(str(ws2[o].value or ""))
            if not m or m[1] != m[3]:
                lech.append(f"{sh}!{o}: công thức lạ {ws2[o].value!r}")
                continue
            s = sum((ws2[f"{m[1]}{i}"].value or 0) for i in range(int(m[2]), int(m[4]) + 1))
            if s != mong:
                lech.append(f"{sh}!{o}: vùng {m[1]}{m[2]}:{m[3]}{m[4]} cộng ra {s} ≠ {mong}")
        if lech:
            p.unlink(missing_ok=True)
            raise _LechTong("; ".join(lech)[:400])
        return {"ok": True, "file": p.name, "sha256": _sha(p), "chu_de_mau": ten_mau,
                "sheets": [{"ten": b["ten"], "so_dong": len(b["dong"]), "tong": b["tong"]} for b in bang],
                "kiem_cong_thuc": f"{len(kiem)} ô tổng: công thức =SUM đúng vùng, cộng lại khớp tổng hệ thống",
                "bieu_do": len(bd), "luu_y": "dòng TỔNG là công thức — Excel / LibreOffice tự tính khi mở; trình xem không tính công thức sẽ thấy ô trống",
                "canh_bao": canh_bao}

    try:
        r = await asyncio.to_thread(ghi)
    except _LechTong as e:
        logger.error("[NATIVE_TOOL] make_xlsx tự kiểm dòng tổng LỆCH: %s", e)
        return {"ok": False, "error": f"Tự kiểm dòng tổng KHÔNG khớp — không giao file: {e}"}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Không ghi được file: {type(e).__name__}: {str(e)[:200]}"}
    logger.info("[NATIVE_TOOL] make_xlsx file=%s sheets=%s bieu_do=%s", r.get("file"), len(r.get("sheets", [])), r.get("bieu_do"))
    return r


def _kiem_bieu_do_xlsx(bieu_do, bang) -> list:
    """X3 (Excel): biểu đồ chỉ TRỎ vào ô của bảng ⇒ số trên biểu đồ = số trong bảng (AI không đưa số riêng cho biểu đồ)."""
    if bieu_do is None:
        return []
    if not isinstance(bieu_do, list) or len(bieu_do) > 4:
        raise ValueError("bieu_do tối đa 4 biểu đồ")
    ten_sheet = {b["ten"]: b for b in bang}
    goc = {str(b.get("ten_sheet") or "").strip(): b for b in bang if b.get("ten_sheet")}
    out = []
    for i, x in enumerate(bieu_do):
        if not isinstance(x, dict) or set(x) - {"loai", "sheet", "cot_nhan", "cot_gia_tri", "tieu_de"}:
            raise ValueError(f"bieu_do[{i}] phải là object với khoá loai, sheet, cot_nhan, cot_gia_tri, tieu_de")
        loai = str(x.get("loai") or "").strip().lower()
        if loai not in ("cot", "tron"):
            raise ValueError(f"bieu_do[{i}].loai phải là 'cot' hoặc 'tron'")
        sh = str(x.get("sheet") or "").strip()
        b = bang[0] if not sh else (ten_sheet.get(sh) or goc.get(sh))
        if b is None:
            raise ValueError(f"bieu_do[{i}]: không có sheet '{sh[:31]}'")
        cot = [_sach(c).strip() for c in b["cot"]]
        cn, cg = _sach(x.get("cot_nhan")).strip(), _sach(x.get("cot_gia_tri")).strip()
        if cn not in cot or cg not in cot:
            raise ValueError(f"bieu_do[{i}]: cột '{cn}' / '{cg}' không có trong sheet '{b['ten']}'")
        if b["kieu"][cot.index(cg)] not in ("so", "tien_vnd", "phan_tram"):
            raise ValueError(f"bieu_do[{i}]: cột giá trị '{cg}' không phải cột số")
        if not b["dong"] or len(b["dong"]) > 50:
            raise ValueError(f"bieu_do[{i}]: cần 1–50 dòng dữ liệu để vẽ")
        trong = [k + 1 for k, d in enumerate(b["dong"]) if d[cot.index(cg)] is None or not _sach(d[cot.index(cn)]).strip()]
        if trong:   # D v4 B13: ô trống ⇒ biểu đồ thiếu điểm / thiếu tên mà không ai biết ⇒ báo lỗi, không vẽ thiếu
            raise ValueError(f"bieu_do[{i}]: dòng {trong[:5]} trống ở cột '{cn}' hoặc '{cg}' — biểu đồ sẽ thiếu điểm; điền đủ hoặc bỏ dòng đó")
        if loai == "tron" and any((d[cot.index(cg)] or 0) < 0 for d in b["dong"]):
            raise ValueError(f"bieu_do[{i}]: biểu đồ tròn không vẽ được số âm")
        td_bd = _sach(x.get("tieu_de")).strip() or cg
        if len(td_bd) > 120:
            raise ValueError(f"bieu_do[{i}].tieu_de tối đa 120 ký tự (hệ thống không cắt)")
        nhan_cot = [_sach(d[cot.index(cn)]).strip() for d in b["dong"]]
        trung = sorted({x2 for x2 in nhan_cot if nhan_cot.count(x2) > 1})
        out.append({"loai": loai, "sheet": b["ten"], "jn": cot.index(cn), "jg": cot.index(cg), "tieu_de": td_bd,
                    "ten_nhan": cn, "ten_gia_tri": cg, "trung": trung})
    return out


def _ve_bieu_do_xlsx(wb, bd: list, vi_tri: dict, mau: dict, canh_bao: list | None = None) -> None:
    from openpyxl.chart import BarChart, PieChart, Reference
    from openpyxl.chart.label import DataLabelList
    dem: dict = {}
    for x in bd:
        if x.get("trung") and canh_bao is not None:
            canh_bao.append(f"biểu đồ '{x['tieu_de']}': nhãn trùng {x['trung'][:3]} — người xem khó phân biệt điểm.")
        ws = wb[x["sheet"]]
        h0, d0, d1, cot = vi_tri[x["sheet"]]
        nhan = Reference(ws, min_col=x["jn"] + 1, min_row=d0, max_row=d1)
        gia = Reference(ws, min_col=x["jg"] + 1, min_row=h0, max_row=d1)   # gồm ô tiêu đề ⇒ tên chuỗi
        if x["loai"] == "cot":   # D v4 B08: trục HIỆN (openpyxl 3.1 mặc định ẩn trục), tên trục, nhãn số trên cột
            ch = BarChart()
            ch.type, ch.style = "col", 10
            ch.y_axis.numFmt = ws.cell(d0, x["jg"] + 1).number_format or "General"
            ch.x_axis.delete, ch.y_axis.delete = False, False
            ch.x_axis.title, ch.y_axis.title = x["ten_nhan"], x["ten_gia_tri"]
            ch.dataLabels = DataLabelList()
            ch.dataLabels.showVal = True
            ch.legend = None
        else:
            ch = PieChart()
            ch.dataLabels = DataLabelList()
            ch.dataLabels.showPercent, ch.dataLabels.showCatName = True, True
            from openpyxl.chart.legend import Legend
            ch.legend = Legend()
            ch.legend.position = "r"
        ch.add_data(gia, titles_from_data=True)
        ch.set_categories(nhan)
        ch.title = x["tieu_de"]
        ch.height, ch.width = 8, 14
        if x["loai"] == "cot":
            from openpyxl.chart.shapes import GraphicalProperties
            ch.series[0].graphicalProperties = GraphicalProperties(solidFill=mau["dam"])
        n = dem.get(x["sheet"], 0)
        dem[x["sheet"]] = n + 1
        ws.add_chart(ch, ws.cell(h0 + (n * 18), len(cot) + 2).coordinate)


# ================================================================== [ghi chú] make_md / make_txt / make_csv (X2)
# Một chỗ ghi file chữ cho agent: UTF-8; tên qua _safe_name (không thành đường dẫn); bảng qua cùng bộ kiểm _kiem_bang của Excel;
# số hiện KIỂU VIỆT NAM (1.740.000 ₫, 89,74 %) đúng số lẻ công cụ trả — không làm tròn. CSV: số để THÔ (mở lại tính được), chữ chống công thức như R10.
CHU_MAX_DOAN, CHU_MAX_KY_TU_DOAN, CHU_MAX_TONG = 200, 5000, 500_000
_CONG_THUC_CSV = re.compile(r"(^|[;,\t\r\n])\s*[=+\-@]")   # giống native_tools._CONG_THUC (R10, S4 06/11)


def _so_vn(v, n: int) -> str:
    """Số ⇒ chữ kiểu Việt Nam: dấu chấm nghìn, dấu phẩy thập phân, đúng n chữ số lẻ (n lấy từ chính giá trị — không làm tròn)."""
    d = Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    am = d < 0
    nguyen, _, le = f"{abs(d):.{n}f}".partition(".")
    nguyen = f"{int(nguyen):,}".replace(",", ".")
    return ("-" if am else "") + nguyen + ("," + le if le else "")


def _hien_vn(v, kieu: str, dv: str | None = None) -> str:
    if v is None:
        return ""
    if kieu in ("chu", "ngay"):
        return _sach(v)
    n = min(_so_le(v), 6)
    if kieu == "tien_vnd":
        return _so_vn(v, 0) + " ₫"
    if kieu == "phan_tram":
        return _so_vn(v, n) + " %"
    return _so_vn(v, n) + (f" {dv}" if dv else "")


def _kiem_chu(tieu_de, doan, ghi_chu, canh_bao) -> tuple[str, list, list]:
    if not isinstance(doan, list) or len(doan) > CHU_MAX_DOAN:
        raise ValueError(f"doan phải là danh sách tối đa {CHU_MAX_DOAN} đoạn")
    if ghi_chu is not None and (not isinstance(ghi_chu, list) or len(ghi_chu) > XLSX_MAX_GHI_CHU):
        raise ValueError(f"ghi_chu tối đa {XLSX_MAX_GHI_CHU} dòng")
    td = _sach(tieu_de, canh_bao).strip()
    if not td or len(td) > MAX_TITLE_CHARS:
        raise ValueError(f"tieu_de cần 1–{MAX_TITLE_CHARS} ký tự")
    dn = []
    for i, x in enumerate(doan):
        s = _sach(x, canh_bao)
        if len(s) > CHU_MAX_KY_TU_DOAN:
            raise ValueError(f"đoạn {i + 1} dài {len(s)} ký tự, quá {CHU_MAX_KY_TU_DOAN} — tách đoạn, không cắt")
        dn.append(s)
    gc = [_sach(x, canh_bao) for x in (ghi_chu or [])]
    if any(len(x) > 500 for x in gc):
        raise ValueError("mỗi dòng ghi_chu tối đa 500 ký tự (hệ thống không cắt)")
    return td, dn, gc


def _bang_chu(bang_cot, bang_dong, kieu_cot, cot_can_tong=None, don_vi_cot=None, canh_bao=None) -> tuple[list, list, list] | None:
    """[C bản 4] thêm cot_can_tong (CODE cộng, như make_xlsx) + don_vi_cot; dòng TỔNG do AI tự thêm ⇒ từ chối (MỤC 58)."""
    if bang_cot is None and bang_dong is None:
        if cot_can_tong or don_vi_cot:
            raise ValueError("cot_can_tong / don_vi_cot cần có bang_cot + bang_dong")
        return None
    kieu, tong = _kiem_bang("bang", bang_cot, bang_dong or [], cot_can_tong, kieu_cot)
    cot = [_sach(c).strip() for c in bang_cot]
    dv = _kiem_don_vi("bang", cot, kieu, don_vi_cot)
    _kiem_dong_tong_tu_them("bang", cot, kieu, bang_dong or [], canh_bao if canh_bao is not None else [])
    if canh_bao is not None and "tien_vnd" in kieu:
        canh_bao.append("cột tiền hiện '₫' — chỉ đúng nếu dữ liệu là VNĐ; nói rõ với người dùng nếu không chắc.")
    rows = [[_hien_vn(v, kieu[j], dv[j]) for j, v in enumerate(d)] for d in (bang_dong or [])]
    if tong:
        nhan = next((j for j, c in enumerate(cot) if c not in tong), None)
        rows.append([_hien_vn(tong[c], kieu[j], dv[j]) if c in tong else ("TỔNG" if j == nhan else "") for j, c in enumerate(cot)])
    return cot, rows, kieu


def _ghi_chu_file(p: Path, chu: str) -> None:
    if len(chu) > CHU_MAX_TONG:
        raise ValueError(f"file sẽ dài {len(chu)} ký tự, quá {CHU_MAX_TONG}")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(chu)


def _md_o(s: str) -> str:
    """Ô / đoạn Markdown: chữ của AI KHÔNG được thành nội dung chủ động khi xem dạng trang (D v4 M07).
    Thoát: \\ (trước hết, để chữ không tự mở lối thoát), & < > (HTML / thực thể), [ ] (link, ảnh tải từ máy khác, định nghĩa link
    kiểu [a]: javascript:…), ` (mã nhúng), | (vỡ bảng). Xuống dòng trong ô ⇒ <br> (do mình chèn, không phải chữ AI)."""
    s = s.replace("\\", "\\\\").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("[", "\\[").replace("]", "\\]").replace("`", "\\`").replace("|", "\\|")
    return s.replace("\r", "").replace("\n", "<br>")


@_ghi_tham_so
async def make_md(ten_file: str, tieu_de: str, doan: list[str], bang_cot: list[str] | None = None,
                  bang_dong: list[list[str | int | float | None]] | None = None, kieu_cot: dict[str, str] | None = None,
                  ghi_chu: list[str] | None = None, cot_can_tong: list[str] | None = None,
                  don_vi_cot: dict[str, str] | None = None) -> dict:
    """Ghi tài liệu Markdown (.md, UTF-8): tiêu đề, các đoạn, (tuỳ chọn) một bảng có số hiện kiểu Việt Nam (1.740.000 ₫, 89,74 %), ghi chú cuối.
    Bạn chỉ đưa nội dung và SỐ ĐÃ CÓ từ công cụ, đúng như công cụ trả.
    Kết quả trả canh_bao là các chỗ hệ thống đã tự sửa hoặc cần lưu ý; nói thật với người dùng nếu khác yêu cầu. Lỗi (ok = false) thì báo lỗi, không nói là đã có file.
    Không tự tạo đường dẫn tải (link) cho file — hệ thống không cấp link; chỉ nói tên file (trường file), file nằm ở thư mục xuất. Mỗi lượt chỉ ghi một lần.
    Chỉ ghi NỘI DUNG cho người đọc file; lời cam kết về cách làm (vd "không bổ sung số liệu ngoài dữ liệu được cung cấp") nói trong câu trả lời chat, không ghi vào file.

    Args:
        ten_file: tên ngắn cho file (chữ không dấu), vd "tom_tat_thuong_thang10".
        tieu_de: tiêu đề tài liệu.
        doan: các đoạn văn (mỗi phần tử một đoạn; dòng bắt đầu "- " là gạch đầu dòng).
        bang_cot: (tuỳ chọn) tên các cột của bảng.
        bang_dong: (tuỳ chọn) các dòng của bảng, cùng thứ tự với bang_cot.
        kieu_cot: (tuỳ chọn) kiểu từng cột như công cụ Excel: "tien_vnd" (CHỈ khi dữ liệu ghi rõ VNĐ), "phan_tram", "so", "chu", "ngay".
        ghi_chu: (tuỳ chọn) các dòng ghi chú cuối, vd "Nguồn số: bản tính KPI lúc 10:48 09/11".
        cot_can_tong: (tuỳ chọn) tên các cột số cần dòng TỔNG — HỆ THỐNG cộng; không tự thêm dòng tổng vào bang_dong.
        don_vi_cot: (tuỳ chọn) đơn vị cho cột kiểu "so" đúng như dữ liệu ghi, vd {"Doanh thu": "USD"}; không rõ thì bỏ trống.
    """
    canh_bao: list[str] = []
    try:
        td, dn, gc = _kiem_chu(tieu_de, doan, ghi_chu, canh_bao)
        bang = _bang_chu(bang_cot, bang_dong, kieu_cot, cot_can_tong, don_vi_cot, canh_bao)
        dong = [f"# {_md_o(td)}", ""]
        for s in dn:
            dong += [_md_o(s).replace("<br>", "  \n"), ""]
        if bang:
            cot, rows, kieu = bang
            dong.append("| " + " | ".join(_md_o(c) for c in cot) + " |")
            dong.append("|" + "|".join("---:" if k in ("so", "tien_vnd", "phan_tram") else "---" for k in kieu) + "|")
            dong += ["| " + " | ".join(_md_o(x) for x in r) + " |" for r in rows]
            dong.append("")
        for s in gc:
            dong.append(f"*{_md_o(s)}*  ")
        p = _safe_name(ten_file, ".md")
        await asyncio.to_thread(_ghi_chu_file, p, "\n".join(dong).rstrip() + "\n")
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"Không ghi được file: {type(e).__name__}."}
    logger.info("[NATIVE_TOOL] make_md file=%s", p.name)
    return {"ok": True, "file": p.name, "sha256": _sha(p), "so_doan": len(dn), "so_dong_bang": len(bang[1]) if bang else 0, "canh_bao": canh_bao}


@_ghi_tham_so
async def make_txt(ten_file: str, tieu_de: str, doan: list[str], bang_cot: list[str] | None = None,
                   bang_dong: list[list[str | int | float | None]] | None = None, kieu_cot: dict[str, str] | None = None,
                   ghi_chu: list[str] | None = None, cot_can_tong: list[str] | None = None,
                   don_vi_cot: dict[str, str] | None = None) -> dict:
    """Ghi văn bản thuần (.txt, UTF-8) — hợp để dán vào tin nhắn: tiêu đề, các đoạn, (tuỳ chọn) một bảng canh cột bằng khoảng trắng, ghi chú cuối.
    Bạn chỉ đưa nội dung và SỐ ĐÃ CÓ từ công cụ, đúng như công cụ trả.
    Kết quả trả canh_bao là các chỗ hệ thống đã tự sửa hoặc cần lưu ý; nói thật với người dùng nếu khác yêu cầu. Lỗi (ok = false) thì báo lỗi, không nói là đã có file.
    Không tự tạo đường dẫn tải (link) cho file — hệ thống không cấp link; chỉ nói tên file (trường file), file nằm ở thư mục xuất. Mỗi lượt chỉ ghi một lần.
    Chỉ ghi NỘI DUNG cho người đọc file; lời cam kết về cách làm (vd "không bổ sung số liệu ngoài dữ liệu được cung cấp") nói trong câu trả lời chat, không ghi vào file.

    Args:
        ten_file: tên ngắn cho file (chữ không dấu).
        tieu_de: tiêu đề.
        doan: các đoạn văn.
        bang_cot: (tuỳ chọn) tên các cột của bảng.
        bang_dong: (tuỳ chọn) các dòng của bảng, cùng thứ tự với bang_cot.
        kieu_cot: (tuỳ chọn) kiểu từng cột như công cụ Excel: "tien_vnd" (CHỈ khi dữ liệu ghi rõ VNĐ), "phan_tram", "so", "chu", "ngay".
        ghi_chu: (tuỳ chọn) các dòng ghi chú cuối.
        cot_can_tong: (tuỳ chọn) tên các cột số cần dòng TỔNG — HỆ THỐNG cộng; không tự thêm dòng tổng vào bang_dong.
        don_vi_cot: (tuỳ chọn) đơn vị cho cột kiểu "so" đúng như dữ liệu ghi, vd {"Doanh thu": "USD"}; không rõ thì bỏ trống.
    """
    canh_bao: list[str] = []
    try:
        td, dn, gc = _kiem_chu(tieu_de, doan, ghi_chu, canh_bao)
        bang = _bang_chu(bang_cot, bang_dong, kieu_cot, cot_can_tong, don_vi_cot, canh_bao)
        dong = [td, "=" * min(len(td), 80), ""]
        for s in dn:
            dong += [s, ""]
        if bang:
            cot, rows, kieu = bang
            rong = [max([len(c)] + [len(r[j]) for r in rows]) for j, c in enumerate(cot)]
            def hang(xs):
                return "  ".join((x.rjust(rong[j]) if kieu[j] in ("so", "tien_vnd", "phan_tram") else x.ljust(rong[j]))
                                 for j, x in enumerate(xs)).rstrip()
            dong += [hang(cot), "  ".join("-" * w for w in rong)] + [hang([x.replace("\n", " ") for x in r]) for r in rows] + [""]
        dong += gc
        p = _safe_name(ten_file, ".txt")
        await asyncio.to_thread(_ghi_chu_file, p, "\n".join(dong).rstrip() + "\n")
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"Không ghi được file: {type(e).__name__}."}
    logger.info("[NATIVE_TOOL] make_txt file=%s", p.name)
    return {"ok": True, "file": p.name, "sha256": _sha(p), "so_doan": len(dn), "so_dong_bang": len(bang[1]) if bang else 0, "canh_bao": canh_bao}


@_ghi_tham_so
async def make_csv(ten_file: str, cot: list[str], dong: list[list[str | int | float | None]]) -> dict:
    """Ghi bảng ra file CSV (UTF-8 có BOM để Excel mở đúng tiếng Việt; mọi ô có ngoặc kép). Số để thô như công cụ trả (mở lại tính được).
    Bạn chỉ đưa SỐ ĐÃ CÓ từ công cụ, đúng như công cụ trả.
    Kết quả trả canh_bao là các chỗ hệ thống đã tự sửa hoặc cần lưu ý; nói thật với người dùng nếu khác yêu cầu. Lỗi (ok = false) thì báo lỗi, không nói là đã có file.
    Không tự tạo đường dẫn tải (link) cho file — hệ thống không cấp link; chỉ nói tên file (trường file), file nằm ở thư mục xuất. Mỗi lượt chỉ ghi một lần.
    Chỉ ghi NỘI DUNG cho người đọc file; lời cam kết về cách làm (vd "không bổ sung số liệu ngoài dữ liệu được cung cấp") nói trong câu trả lời chat, không ghi vào file.

    Args:
        ten_file: tên ngắn cho file (chữ không dấu).
        cot: tên các cột.
        dong: các dòng, mỗi dòng một danh sách cùng thứ tự với cot.
    """
    import csv
    canh_bao: list[str] = []
    try:
        _kiem_bang("csv", cot, dong, None, None)

        def o(v):
            if v is None:
                return ""
            if _la_so(v):
                return repr(v) if isinstance(v, float) else str(v)
            s = _sach(v, canh_bao)
            if len(s) > XLSX_MAX_CHU:
                raise ValueError(f"ô chữ dài {len(s)} ký tự, quá {XLSX_MAX_CHU} — rút gọn (hệ thống không cắt)")
            return "'" + s if _CONG_THUC_CSV.search(s) else s   # chống công thức (R10): ô chữ có = + - @ ở đầu hoặc sau ; , tab, xuống dòng
        out = io.StringIO()
        w = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        w.writerow([o(c) for c in cot])
        for d in dong:
            w.writerow([o(v) for v in d])
        p = _safe_name(ten_file, ".csv")
        chu = "\ufeff" + out.getvalue()
        if len(chu) > CHU_MAX_TONG * 4:
            raise ValueError("file CSV quá lớn")
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(chu)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"Không ghi được file: {type(e).__name__}."}
    logger.info("[NATIVE_TOOL] make_csv file=%s rows=%s", p.name, len(dong))
    return {"ok": True, "file": p.name, "sha256": _sha(p), "so_dong": len(dong), "canh_bao": canh_bao}
