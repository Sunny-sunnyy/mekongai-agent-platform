"""Lượt 3 của Loader: luna (mô hình đọc ảnh) đọc ảnh những trang máy không đọc được hết ⇒ chép chữ trong ảnh + mô tả từng hình, thêm vào chữ trang.
Module này chỉ CHỌN trang và GỌI luna; ghép chữ vào trang ở process_pdf.add_vlm_text. Prompt bỏ đoạn tóm tắt cả trang: trang chữ chỉ cần chữ nguyên văn,
còn hình thì cần mô tả từng phần hình (CHANGELOG: CH-20261015-A15, CH-20261016-A19).
"""
import base64
import io
import json
import logging
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import fitz
from PIL import Image

logger = logging.getLogger(__name__)

VLM_MODEL = "gpt-6-luna"
# Lịch sử prompt: page-v4 ghi số của biểu đồ kèm nhãn ngay trong mô tả — trước đó số rời không nhãn, gắn đúng nhãn 13 ⇒ 121 / 126 (CHANGELOG: CH-20261019-A4);
# page-v6 thêm `bbox` từng phần hình để cắt ảnh biểu đồ (CHANGELOG: CH-20261020-A1); page-v7 thêm `caption` để ảnh chụp có chú thích cũng thành "hình"
# ở mọi bố cục — cách tìm cặp ảnh–chú thích bằng hình học (figure_caption.py) chỉ nhận một bố cục (CHANGELOG: CH-20261020-A2)
PROMPT_VERSION = "page-v7"
# Trần trang luna mỗi file: ~0,0012 USD và ~24 s mỗi trang ⇒ 200 trang ≤ ~0,24 USD; chặn được PDF scan 1.000 trang (~1,2 USD, gần 1 giờ).
# Trần 30 trang (bản đầu) làm mất món ở trang sau của menu dài. Vượt trần ⇒ chọn trang khó trước (scan_priority) (CHANGELOG: CH-20261015-A15, CH-20261018-A42)
VLM_MAX_PAGES_PER_FILE = 200
VLM_WORKERS = 8                 # gọi API, không tốn CPU máy; 4 luồng làm tải lên menu chậm thêm ~4 phút ⇒ User cho 8
# Tắt suy nghĩ khi đọc trang: chỉ chép chữ + mô tả hình. Đo menu 17 trang: đủ món + giá như mức mặc định, tiền −32 %, nhanh hơn ~1/3 (CHANGELOG: CH-20261016-A23).
# Riêng cho luna đọc trang — không theo OPENAI_REASONING_EFFORT của lượt trả lời (ở đó "none" nghĩa là không gửi tham số)
VLM_REASONING_EFFORT = "none"
# Thời gian chờ mỗi lượt luna: mặc định thư viện OpenAI 600 s ⇒ một lượt treo phía nhà cung cấp làm cả file chờ ~10 phút. Bình thường 10–40 s / trang
# ⇒ 120 s; thư viện vẫn tự gọi lại tối đa 2 lần, hết lượt ⇒ cờ vlm_error (CHANGELOG: CH-20261020-A5)
VLM_TIMEOUT_SECONDS = 120
VLM_LONG_SIDE = 2048           # cạnh dài ảnh trang gửi luna: 2048 px + detail "high" đọc đúng 54 / 54 món + giá của menu thử
# Trang chữ sẵn có ảnh lớn nhất ≥ 3 % diện tích trang ⇒ luna đọc: ảnh nhỏ cũng mang chữ / số không có ở lớp chữ (ô số liệu dạng ảnh, ảnh chụp màn hình,
# thẻ chữ brochure). Đo 3 bộ gán nhãn trước: 3 % bắt 174 / 174 ảnh có chữ; luật rẻ hơn (OCR ảnh cắt, dò vùng chữ) sót / nhầm 13–22 %; 5 % đã sót infographic.
# Chi phí: tài liệu thường ~1 trang / 100 (CHANGELOG: CH-20261017-A28)
VLM_MIN_IMAGE_RATIO = 0.03
# Ảnh trang trí lặp lại (logo, dải đầu / chân trang) không tính: cùng kích thước ảnh gốc ở ≥ 30 % số trang chữ sẵn (và ≥ 3 trang). Không lọc thì một báo cáo
# 117 trang gửi luna 113 trang chỉ vì ảnh đầu / chân trang (CHANGELOG: CH-20261017-A29)
REPEATED_IMAGE_MIN_SHARE = 0.3
REPEATED_IMAGE_MIN_PAGES = 3
# Biểu đồ / sơ đồ / bản đồ: khối "picture" của mô hình bố cục ≥ 3 % trang (process_pdf ghi `picture_ratio` ở lượt 1). Luật cũ (vùng nét vẽ ≥ 20 %) bắt
# 4 / 12 biểu đồ và nhầm 7 trang trang trí; khối "picture" bắt 10 / 12, nhầm 0 (CHANGELOG: CH-20261017-A27)
VLM_MIN_PICTURE_RATIO = 0.03
# Trang chữ sẵn có ≥ 1 dòng chữ vẽ bằng nét (process_pdf.vector_text_lines — catalog / menu thiết kế) ⇒ luna đọc, vì chữ đó không có trong lớp chữ.
# KHÔNG lọc dòng lặp ở nhiều trang (như ảnh trang trí): dòng lặp thường là chữ thật (đơn vị giá, "chưa gồm VAT" ở mọi trang menu); đổi lại +~2 % trang luna.
# Giới hạn: logo vẽ bằng nét ở mọi trang ⇒ cả file gửi luna (chưa gặp; trần VLM_MAX_PAGES_PER_FILE vẫn chặn) (CHANGELOG: CH-20261020-A5)
VLM_MIN_VECTOR_TEXT_LINES = 1

PAGE_VLM_PROMPT = """Bạn là người số hóa tài liệu. Bạn nhận ảnh của MỘT trang tài liệu (văn bản, báo cáo, bảng biểu, biểu đồ, sơ đồ,
biểu mẫu, ảnh chụp, menu, catalog, slide… — có thể là bản scan, ảnh chụp điện thoại hoặc trang thiết kế).
Kết quả của bạn được đưa vào hệ thống tìm kiếm và để một trợ lý AI đọc lại khi trả lời câu hỏi về tài liệu.
Mục tiêu: không để mất thông tin nào nhìn thấy trên trang, và không thêm thông tin nào không có trên trang.

Cách làm:
1. Nhìn toàn trang, nhận ra trang gồm những loại nội dung nào (đoạn chữ, bảng, biểu đồ, sơ đồ, ảnh chụp, biểu mẫu,
   con dấu, chữ ký, logo) và thứ tự đọc tự nhiên (trên xuống, trái sang phải; theo cột nếu trang chia cột).
2. text_lines — chép NGUYÊN VĂN mọi chữ đọc được, theo thứ tự đọc, mỗi phần tử một dòng:
   - giữ đúng chữ, dấu tiếng Việt, số, đơn vị, dấu chấm/phẩy của số, ngày tháng, mã số, số điện thoại, email, giá;
   - giữ ngôn ngữ gốc (không dịch);
   - bảng: mỗi hàng một dòng, các ô cách nhau bằng " | ", hàng tiêu đề trước;
   - biểu mẫu: "Tên trường: giá trị"; ô đánh dấu ghi [x] hoặc [ ];
   - chữ trong ảnh chụp, trên biển, bao bì, nhãn biểu đồ cũng chép;
   - chữ viết tay hoặc mờ mà vẫn đọc được thì chép và thêm "(không chắc)"; không đọc được thì bỏ qua, không đoán.
3. visuals — mỗi phần hình ảnh không phải chữ thường (ảnh chụp, biểu đồ, sơ đồ, bảng dạng hình, logo, con dấu,
   chữ ký) một phần tử, mô tả bằng tiếng Việt những gì người tìm kiếm cần biết:
   - biểu đồ: loại biểu đồ, tiêu đề, trục/đơn vị, chú giải, xu hướng chính;
     biểu đồ có số IN SẴN trên cột / điểm / đường / lát: ghi ĐỦ từng giá trị kèm nhãn ngay trong mô tả này, dạng
     "chuỗi – mốc: giá trị đơn vị" (ví dụ "Doanh thu – Năm 2023: 120 tỷ đồng"; "Miền Bắc – Tháng 5: 12 %"), không viết kiểu
     "các giá trị đã chép vào text_lines" — người đọc chỉ thấy mô tả mới biết số nào thuộc nhãn nào;
     biểu đồ không in số: KHÔNG đọc hay ước giá trị từ vị trí hình, chỉ tả bằng lời (tăng / giảm, cao nhất / thấp nhất ở mốc nào);
   - sơ đồ / lưu đồ: các khối và quan hệ giữa chúng (A → B);
   - ảnh chụp: vật/người/cảnh nhìn thấy, đặc điểm rõ ràng; không đoán danh tính, thương hiệu, giá nếu không in trên trang;
   - con dấu, chữ ký: ghi có con dấu (chép chữ trên dấu nếu đọc được), có chữ ký;
   - bbox: khung bao phần hình đó trên ảnh trang, [trái, trên, phải, dưới] theo thang 0–1000 (0 = mép trái / mép trên ảnh,
     1000 = mép phải / mép dưới); khung biểu đồ / sơ đồ gồm cả tiêu đề, nhãn trục và chú giải của chính nó. Trang có nhiều
     biểu đồ thì mỗi biểu đồ là một phần tử riêng với khung riêng, không gộp chung;
   - caption: chú thích in trên trang dành riêng cho phần hình đó (dòng "Hình 3: …", tên / chức vụ in dưới ảnh chân dung,
     câu chú thích nhỏ ngay dưới, trên hoặc cạnh ảnh) — chép NGUYÊN VĂN, các dòng nối bằng dấu cách; tiêu đề bài, đoạn thân
     bài hay chữ trên bìa không phải chú thích; nhiều ảnh dùng chung một chú thích thì ghi cho từng ảnh; không có thì để "".
4. is_blank_or_decorative — true nếu trang trống hoặc chỉ có hình trang trí/ảnh nền không mang thông tin.
5. Trước khi trả lời, tự soát: đã chép hết các dòng chữ đọc được chưa; số và mã có đúng từng ký tự không; có câu
   nào là suy đoán không có trên trang không — nếu có, bỏ đi hoặc ghi vào uncertain."""

VISUAL_KINDS = ["anh_chup", "bieu_do", "so_do", "bang_dang_hinh", "bieu_mau", "logo", "con_dau", "chu_ky", "khac"]
PAGE_VLM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["text_lines", "visuals", "is_blank_or_decorative", "uncertain"],
    "properties": {
        "text_lines": {"type": "array", "items": {"type": "string"}},
        "visuals": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "description_vi", "bbox", "caption"],
            "properties": {"kind": {"type": "string", "enum": VISUAL_KINDS}, "description_vi": {"type": "string"},
                           "bbox": {"type": "array", "items": {"type": "integer"}}, "caption": {"type": "string"}},
        }},
        "is_blank_or_decorative": {"type": "boolean"},
        "uncertain": {"type": "array", "items": {"type": "string"}},
    },
}


def vlm_enabled():
    """Bật mặc định trong app. Runner offline (docker --network none) đặt PDF_VLM_ENABLED=false để đầu ra không lẫn lỗi mạng."""
    return os.getenv("PDF_VLM_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def select_pages(doc, statuses):
    """Chọn trang cho luna theo tín hiệu chung của trang (không theo loại tài liệu). Trả (chọn, vượt trần) — chỉ số trang 0-based.
    - Trang lớp `ocr` / `hidden_ocr` (scan, ảnh chụp, chữ vẽ bằng nét, lớp OCR ẩn): Tesseract đọc kém ở ảnh chụp / menu (món + giá 23 / 54 ↔ luna 54 / 54)
      và sai số / ngày trong bảng scan ⇒ chọn cả lớp. Thử bỏ trang scan "OCR đọc tốt" theo tỉ lệ âm tiết cả trang đã hoàn tác: tỉ lệ cả trang không thấy lỗi
      cục bộ (CHANGELOG: CH-20261016-A22). Lớp OCR ẩn "đủ dấu" vẫn có thể sai dấu nhiều ⇒ cũng chọn (CHANGELOG: CH-20261017-A30).
    - Trang `native` có ảnh lớn (chữ trong ảnh không có ở lớp chữ), có hình (khối "picture", trang khổ lớn, biểu đồ vẽ nét), có công thức, có dòng chữ vẽ nét.
    Lớp `empty` không chọn. Ưu tiên: scan ⇒ ảnh ⇒ hình ⇒ công thức ⇒ chữ nét. Vượt trần VLM_MAX_PAGES_PER_FILE ⇒ trong nhóm scan lấy trang 1 rồi trang khó nhất trước."""
    ocr_pages, image_pages, figure_pages, formula_pages, vector_text_pages = [], [], [], [], []
    native = [idx for idx, status in enumerate(statuses) if status.get("method") == "native"]
    decorations = repeated_images(doc, native)
    for idx, status in enumerate(statuses):
        if status.get("method") in ("ocr", "hidden_ocr"):
            ocr_pages.append(idx)
        elif status.get("method") == "native":
            if largest_image_ratio(doc[idx], decorations) >= VLM_MIN_IMAGE_RATIO:
                image_pages.append(idx)
            elif status.get("picture_ratio", 0.0) >= VLM_MIN_PICTURE_RATIO or {"large_page", "vector_chart"} & set(status.get("flags", [])):
                # Trang khổ lớn (bản đồ, bản vẽ) không chạy bố cục ⇒ coi như trang hình (CHANGELOG: CH-20261017-A39); biểu đồ vẽ nét mà bố cục
                # không ra khối "picture" cũng vậy (CHANGELOG: CH-20261020-A8)
                figure_pages.append(idx)
            elif "math_formula" in status.get("flags", []):
                formula_pages.append(idx)
            elif len(status.get("vector_text_lines", [])) >= VLM_MIN_VECTOR_TEXT_LINES:
                vector_text_pages.append(idx)
    ranked = sorted(ocr_pages, key=lambda idx: scan_priority(idx, statuses[idx])) + image_pages + figure_pages + formula_pages + vector_text_pages
    return sorted(ranked[:VLM_MAX_PAGES_PER_FILE]), sorted(ranked[VLM_MAX_PAGES_PER_FILE:])


def scan_priority(idx, status):
    """Khóa sắp trang scan khi vượt trần: trang 1 (đầu văn bản: số hiệu, ngày, cơ quan) trước, rồi tỉ lệ âm tiết tiếng Việt hợp lệ của chữ OCR tăng dần.
    Tỉ lệ thấp = nhiều số / mã (bảng), chữ ra rác, trang xoay — đúng các trang máy sai nhiều nhất khi đo 18/10 (bảng số scan 13–89 % số đúng,
    văn xuôi 95–99 % dấu). Trang không có tỉ lệ (lớp OCR ẩn được giữ, không OCR lại) coi là không chắc ⇒ xếp như trang khó."""
    return (idx != 0, status.get("ocr_syllable_ratio", 0.0), idx)


def largest_image_ratio(page, skip_sizes=frozenset()):
    """Tỉ lệ diện tích trang của ảnh lớn nhất, bỏ ảnh có kích thước gốc trong skip_sizes (ảnh trang trí lặp lại — repeated_images)."""
    area = abs(page.rect) or 1.0
    rects = [fitz.Rect(info["bbox"]) & page.rect for info in page.get_image_info() if (info["width"], info["height"]) not in skip_sizes]
    return max((abs(r) / area for r in rects if not r.is_empty), default=0.0)


def repeated_images(doc, page_indexes):
    """Kích thước gốc (rộng, cao) của ảnh xuất hiện ở ≥ REPEATED_IMAGE_MIN_SHARE số trang (logo, dải đầu / chân trang). get_image_info() không kèm xref: rẻ."""
    counts = Counter()
    for idx in page_indexes:
        counts.update({(info["width"], info["height"]) for info in doc[idx].get_image_info()})
    need = max(REPEATED_IMAGE_MIN_PAGES, REPEATED_IMAGE_MIN_SHARE * len(page_indexes))
    return frozenset(size for size, n in counts.items() if n >= need)


def page_jpeg_data_uri(doc, page_index, rotation=0):
    """Ảnh trang JPEG cạnh dài VLM_LONG_SIDE px, nền trắng — như I1b (PyMuPDF get_pixmap). rotation: góc (theo chiều kim đồng hồ) Loader đã
    phải xoay ảnh trang khi OCR (A20) ⇒ luna nhận ảnh đã thẳng."""
    page = doc[page_index]
    zoom = VLM_LONG_SIDE / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    if not rotation:
        return "data:image/jpeg;base64," + base64.b64encode(pix.tobytes("jpeg", jpg_quality=90)).decode()
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).rotate(-rotation, expand=True)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def describe_page(client, data_uri, on_usage=None):
    """Một lượt VLM, JSON schema strict. Lỗi provider ném ra cho nơi gọi ghi cờ (không trả giá trị giả).
    on_usage(dict): nhận số token của lượt (kind, model, prompt_version, reasoning_effort, usage) để ghi chi phí; None ⇒ không ghi."""
    kwargs = {
        "model": VLM_MODEL,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": PAGE_VLM_PROMPT},
            {"type": "input_image", "image_url": data_uri, "detail": "high"},
        ]}],
        "text": {"format": {"type": "json_schema", "name": "page_reading", "schema": PAGE_VLM_SCHEMA, "strict": True}},
        "reasoning": {"effort": VLM_REASONING_EFFORT},
    }
    response = client.responses.create(**kwargs, timeout=VLM_TIMEOUT_SECONDS)
    if response.usage and on_usage:
        on_usage({"kind": "vlm_page", "model": response.model, "prompt_version": PROMPT_VERSION,
                  "reasoning_effort": VLM_REASONING_EFFORT, "usage": response.usage.model_dump()})
    return json.loads(response.output_text)


def app_client():
    """Client luna của app + hàm ghi chi phí của app: cùng cấu hình provider / khoá với LLM trả lời (OPENAI_BASE_URL, OPENAI_API_KEY), chi phí ghi
    resources/logs/openai_usage.jsonl (configs.usage). Import khi gọi ⇒ dùng Loader ở dự án khác (truyền client riêng) không cần code app."""
    from configs.usage import record_usage
    from controllers.llm.unified_client import UnifiedLLMClient
    return UnifiedLLMClient(provider="openai")._get_openai_client(), record_usage


def describe_pages(file_path, page_indexes, rotations=None, client=None, on_usage=None):
    """VLM cho các trang được chọn, song song VLM_WORKERS luồng. Trả list (idx, kết quả dict hoặc Exception, số giây).
    client: client OpenAI (Responses API, model VLM_MODEL) + on_usage (xem describe_page) khi dùng ngoài app; client None ⇒ app_client()."""
    if not page_indexes:
        return []
    if client is None:
        client, on_usage = app_client()

    def run(idx):
        # Render ngay trong luồng, trong try: lỗi render một trang chỉ hỏng trang đó (bản cũ render hết trước, ngoài try ⇒ một trang lỗi làm mất cả file);
        # mỗi luồng mở file riêng (PyMuPDF không dùng chung giữa các luồng), chỉ giữ ≤ VLM_WORKERS ảnh cùng lúc
        t0 = time.perf_counter()
        try:
            with fitz.open(file_path) as doc:
                data_uri = page_jpeg_data_uri(doc, idx, (rotations or {}).get(idx, 0))
            result = describe_page(client, data_uri, on_usage)
        except Exception as e:
            logger.warning(f"VLM failed for page {idx + 1}: {type(e).__name__}")
            result = e
        return idx, result, time.perf_counter() - t0

    with ThreadPoolExecutor(max_workers=VLM_WORKERS) as executor:
        return list(executor.map(run, page_indexes))
