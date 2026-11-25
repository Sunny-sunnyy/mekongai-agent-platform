"""Cắt ảnh từng biểu đồ / sơ đồ (và ảnh chụp có chú thích) trên trang luna đã đọc ⇒ "hình" cùng khuôn cặp ảnh – chú thích (dict type=figure_caption),
để khi người dùng hỏi về biểu đồ / ảnh, app hiện lại đúng ảnh gốc qua cùng đường với figure_caption.py (CHANGELOG: CH-20261020-A1, CH-20261020-A2).

Ai làm gì: luna cho biết trang có mấy biểu đồ, cái nào là biểu đồ, và khung ƯỚC LƯỢNG (thang 0–1000) mỗi cái; code bắt khung đó vào khung CHÍNH XÁC
máy thấy, rồi lấy thêm dòng chữ sát khung nằm trong khung luna (tiêu đề, nhãn trục, chú giải). Đo trên 12 trang biểu đồ: không tín hiệu máy nào một mình
đủ — khối "picture" của bố cục bỏ sót biểu đồ dạng ảnh và bắt cả số to; ảnh nhúng bắt cả ảnh chụp, ảnh nền; cụm nét vẽ gom cả bảng kẻ. Code không tự
phân biệt biểu đồ ↔ ảnh chụp (luật kiểu "nhiều chữ số, có trục" dễ thành luật theo mẫu) ⇒ để luna nói, code chỉ chỉnh mép."""
import base64
import logging

import fitz

logger = logging.getLogger(__name__)

CHART_KINDS = ("bieu_do", "so_do")
CROP_DPI = 110                  # ảnh cắt ~1.000 px cạnh dài cho biểu đồ nửa trang A4 — đọc được nhãn, base64 ~100–200 KB
PHOTO_JPEG_QUALITY = 85         # ảnh chụp: JPEG 85 (cùng kiểu ảnh trang gửi luna dùng JPEG — page_vision.py)
MIN_MACHINE_RATIO = 0.005       # khung máy nhỏ hơn 0,5 % trang (biểu tượng, gạch đầu dòng) không xét
SNAP_MIN_INSIDE = 0.6           # khung máy được nhận khi ≥ 60 % diện tích nằm trong khung luna (luna lệch mép vài %)
MIN_SNAP_COVER = 0.3            # khung máy gộp lại phủ < 30 % khung luna ⇒ máy chỉ thấy một mảnh (một trang thử: 13 %), dùng khung luna
TEXT_GAP_RATIO = 0.05           # dòng chữ cách khung ≤ 5 % cạnh dài trang (~42 pt ở A4) thì coi là tiêu đề / chú giải sát biểu đồ
PADDING = 4                     # pt lề quanh ảnh cắt
MAX_IMAGE_SIDE = 1600           # px cạnh dài tối đa: luna gọi cả trang khổ lớn là sơ đồ ⇒ 110 dpi ra 3.643 × 5.149 px, 1,3 MB base64 / point (bản vẽ A0)
# Không có khung máy nằm trong khung luna ⇒ lấy khung máy nhỏ nhất BAO gần trọn khung luna (phủ ≥ 80 %) mà không quá to (≤ 2 lần) — khối picture gồm cả tiêu đề
AROUND_MIN_COVER = 0.8
AROUND_MAX_GROWTH = 2
OTHER_CHART_OVERLAP = 0.3       # khung luna chồng ≥ 30 % lên khung luna của biểu đồ khác ⇒ không tin riêng khung luna (sẽ cắt dính biểu đồ bên cạnh)
LINE_IN_GUESS = 0.8             # dòng chữ được lấy thêm vào khung khi ≥ 80 % diện tích dòng nằm trong khung luna
CUT_LINE_MIN_INSIDE = 0.05      # dòng có ≥ 5 % nằm trong khung ⇒ đang bị khung cắt ngang
CUT_LINE_MIN_WIDTH_IN_GUESS = 0.5   # dòng bị cắt chỉ được lấy trọn khi ≥ 50 % bề ngang nằm trong khung luna (không lấy dòng thân bài dài chạm mép)
SHORT_LINE_MAX_WIDTH = 0.25     # … hoặc là dòng ngắn (≤ 25 % bề ngang khung luna: nhãn chú giải ở mép)
DUPLICATE_PHOTO_OVERLAP = 0.5   # ảnh chụp luna chồng ≥ 50 % (theo ảnh nhỏ hơn) lên cặp ảnh – chú thích hình học ⇒ trùng, bỏ bản luna


def luna_rect(page, bbox):
    """Khung luna (thang 0–1000 theo ảnh trang) ⇒ toạ độ trang; None nếu không hợp lệ."""
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    x0, y0, x1, y1 = (min(max(float(v), 0.0), 1000.0) / 1000 for v in bbox)
    r = page.rect
    rect = fitz.Rect(r.x0 + x0 * r.width, r.y0 + y0 * r.height, r.x0 + x1 * r.width, r.y0 + y1 * r.height)
    return rect if not rect.is_empty else None


def machine_rects(page, picture_boxes):
    """Khung chính xác máy thấy: khối "picture" của bố cục (lượt 1 đã tính, page_status.picture_boxes), ảnh nhúng, cụm nét vẽ."""
    rects = [fitz.Rect(box) for box in picture_boxes]
    rects += [fitz.Rect(info["bbox"]) for info in page.get_image_info()]
    rects += list(page.cluster_drawings())
    min_area = MIN_MACHINE_RATIO * abs(page.rect)
    return [r & page.rect for r in rects if abs(r & page.rect) >= min_area]


def text_line_rects(page):
    """Khung từng DÒNG chữ của trang (không dùng khối: hai tiêu đề biểu đồ đặt cạnh nhau hay bị gộp chung một khối, khối đó chỉ nằm nửa trong khung luna)."""
    lines = []
    for block in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
        for line in block.get("lines", []):
            if "".join(span["text"] for span in line["spans"]).strip():
                lines.append(fitz.Rect(line["bbox"]))
    return lines


def snap_rect(page, guess, machine, text_lines, others=()):
    """Khung cuối của một biểu đồ. Khung máy nằm phần lớn trong khung luna ⇒ gộp lại (biểu đồ nhiều mảnh: ảnh nền + nét vẽ);
    không có ⇒ khung máy nhỏ nhất bao gần trọn khung luna mà không quá to (khối picture gồm cả tiêu đề); vẫn không có (trang scan: cả trang là một ảnh)
    ⇒ khung luna. Sau đó lấy thêm dòng chữ nằm trong khung luna và sát khung (tiêu đề, nhãn trục, chú giải) — khung máy thường bỏ chữ."""
    inside = [r for r in machine if abs(r & guess) >= SNAP_MIN_INSIDE * abs(r)]
    if not inside:
        around = [r for r in machine if abs(r & guess) >= AROUND_MIN_COVER * abs(guess) and abs(r) <= AROUND_MAX_GROWTH * abs(guess)]
        inside = sorted(around, key=abs)[:1]
    if not inside:
        return guess
    rect = fitz.Rect(inside[0])
    for r in inside[1:]:
        rect |= r
    overlaps_other = any(abs(guess & other) >= OTHER_CHART_OVERLAP * abs(other) for other in others)
    if abs(rect & guess) < MIN_SNAP_COVER * abs(guess) and not overlaps_other:
        # Máy chỉ thấy một mảnh của biểu đồ (một cột dạng ảnh trong biểu đồ 5 nhóm cột ⇒ ảnh cắt còn 1 cột) ⇒ tin khung luna.
        # Khung luna phủ lên biểu đồ khác của trang thì không: tin khung luna sẽ cắt dính biểu đồ bên cạnh
        return guess | rect
    gap = TEXT_GAP_RATIO * max(page.rect.width, page.rect.height)
    changed = True
    while changed:  # tiêu đề 2 dòng, chú giải nhiều dòng: lấy dần từng dòng sát khung
        changed = False
        for line in text_lines:
            if line in rect or abs(line & guess) < LINE_IN_GUESS * abs(line):
                continue
            if (rect + (-gap, -gap, gap, gap)).intersects(line):
                rect |= line
                changed = True
    return rect


def include_cut_lines(rect, guess, text_lines):
    """Dòng chữ bị khung cắt ngang (≥ 5 % dòng nằm trong khung — vệt chữ hở ở mép ảnh) ⇒ lấy trọn dòng (tiêu đề chữ to sát mép từng bị cắt còn "…g tại ngày
    31/12/2025", chú thích "Hình 4.1" bị cắt nửa dòng). Dòng thân bài dài chạm mép thì không: chỉ lấy dòng có ≥ 50 % bề ngang nằm trong khung luna, hoặc dòng
    ngắn (≤ 25 % bề ngang khung luna — nhãn chú giải "2023", "2024" ở mép ngoài khung luna)."""
    rect = fitz.Rect(rect)
    for line in text_lines:
        if line in rect or abs(line & rect) < CUT_LINE_MIN_INSIDE * abs(line):
            continue
        inside_luna = min(line.x1, guess.x1) - max(line.x0, guess.x0) >= CUT_LINE_MIN_WIDTH_IN_GUESS * line.width
        if inside_luna or line.width <= SHORT_LINE_MAX_WIDTH * guess.width:
            rect |= line
    return rect


def assign_machine_rects(guesses, machine):
    """Mỗi khung máy thuộc về MỘT biểu đồ: biểu đồ có khung luna nhỏ nhất chứa nó (≥ SNAP_MIN_INSIDE). Luna từng ước khung vành khuyên 1
    rộng sang cả vành khuyên 2 ⇒ không chia thì ảnh cắt 1 chứa cả 2 biểu đồ. Khung máy không nằm trong khung luna nào vẫn đưa cho mọi biểu đồ (nhánh "bao quanh")."""
    owned = [[] for _ in guesses]
    for r in machine:
        holders = [i for i, guess in enumerate(guesses) if abs(r & guess) >= SNAP_MIN_INSIDE * abs(r)]
        if holders:
            owned[min(holders, key=lambda i: abs(guesses[i]))].append(r)
        else:
            for chart_rects in owned:
                chart_rects.append(r)
    return owned


def crop_figures(file_path, page_index, reading, picture_boxes):
    """Các "hình" của một trang luna đã đọc (JSON page-v7): mọi biểu đồ / sơ đồ, và mọi ảnh chụp luna tả được nội dung — có chú thích hay không
    (người dùng hỏi "ảnh chiếc xe đỏ" thì chữ để tìm của hình là mô tả luna). Logo, con dấu, chữ ký, bảng dạng hình không cắt (kind khác).
    Lỗi ⇒ [] và ghi log: phần cắt ảnh chỉ thêm, không được làm mất chữ."""
    visuals = [v for v in reading.get("visuals", []) if v.get("description_vi", "").strip()
               and (v.get("kind") in CHART_KINDS or v.get("kind") == "anh_chup")]
    if not visuals or reading.get("is_blank_or_decorative"):
        return []
    try:
        with fitz.open(file_path) as doc:
            page = doc[page_index]
            if page.rotation:
                # Toạ độ của get_image_info / cluster_drawings theo trang chưa xoay, ảnh luna nhận đã xoay ⇒ bỏ qua, hiếm gặp ở trang biểu đồ
                return []
            text_lines = text_line_rects(page)
            items = [(visual, guess) for visual in visuals if (guess := luna_rect(page, visual.get("bbox"))) is not None]
            # Không bỏ ảnh chụp phủ gần cả trang (trước: ≥ 85 % ⇒ coi là ảnh nền): menu ảnh món ăn, trang bìa là ảnh nội dung người dùng hỏi tới.
            # Ảnh nền trang trí phủ cả trang cũng thành "hình" — chấp nhận; trang scan văn bản luna không gọi là ảnh chụp nên không bị cắt
            # (số đo: tài liệu kỹ thuật, thí nghiệm H1c / H1e).
            owned = assign_machine_rects([guess for _, guess in items], machine_rects(page, picture_boxes))
            figures, counts = [], {"bieu-do": 0, "anh": 0}
            for (visual, guess), machine in zip(items, owned):
                others = [g for _, g in items if g is not guess]
                # lề trước rồi mới xét dòng bị cắt: lề 4 pt chạm dòng chú thích bên dưới thì lấy trọn dòng, không để vệt chữ ở mép ảnh
                rect = snap_rect(page, guess, machine, text_lines, others) + (-PADDING, -PADDING, PADDING, PADDING)
                rect = include_cut_lines(rect, guess, text_lines) & page.rect
                dpi = min(CROP_DPI, MAX_IMAGE_SIDE * 72 / max(rect.width, rect.height))
                pix = page.get_pixmap(clip=rect, dpi=dpi, alpha=False)
                # tiền tố "bieu-do" / "anh": retriever hiện ảnh biểu đồ to hơn (220 px không đọc được nhãn); drop_duplicate_photos nhận ra ảnh luna
                prefix = "bieu-do" if visual.get("kind") in CHART_KINDS else "anh"
                counts[prefix] += 1
                caption = visual.get("caption", "").strip()
                figures.append({
                    "type": "figure_caption",
                    "page": page_index + 1,
                    "figure_id": f"p{page_index + 1}-{prefix}-{counts[prefix]}",
                    "bbox": [round(v, 1) for v in rect],
                    "caption_bbox": [round(v, 1) for v in guess],
                    "caption_lines": [caption, visual["description_vi"].strip()] if caption else [visual["description_vi"].strip()],
                    "image_px": [pix.width, pix.height],
                    # Ảnh chụp lưu JPEG: PNG của ảnh chụp nặng gấp ~6 lần (đo: BAO_CAO_B H1e); biểu đồ giữ PNG cho nét chữ, đường kẻ sắc
                    "image_base64": base64.b64encode(pix.tobytes("png") if prefix == "bieu-do" else pix.tobytes("jpeg", jpg_quality=PHOTO_JPEG_QUALITY)).decode(),
                })
            return figures
    except Exception as e:
        logger.warning(f"Figure crop failed on page {page_index + 1}: {type(e).__name__}")
        return []


def drop_duplicate_photos(elements):
    """Trang đã có cặp ảnh – chú thích do figure_caption.py tìm theo hình học (bố cục catalog thời trang) ⇒ bỏ ảnh chụp luna trùng chỗ (≥ 50 % diện tích),
    để một ảnh không thành hai "hình". Biểu đồ luna giữ nguyên. Lỗi ⇒ giữ nguyên elements: bước này chỉ bớt hình, nằm trong khối xử lý lỗi chung của
    process_pdf (lỗi ở đó làm mất chữ cả file — B kiểm chéo 20/10)."""
    try:
        return _without_duplicate_photos(elements)
    except Exception as e:
        logger.warning(f"Duplicate photo check failed: {type(e).__name__}")
        return elements


def _without_duplicate_photos(elements):
    geometric = [fitz.Rect(e["bbox"]) for e in elements
                 if isinstance(e, dict) and e.get("type") == "figure_caption" and "-anh-" not in str(e.get("figure_id")) and "-bieu-do-" not in str(e.get("figure_id"))]
    if not geometric:
        return elements

    def duplicate(e):
        if not (isinstance(e, dict) and e.get("type") == "figure_caption" and "-anh-" in str(e.get("figure_id"))):
            return False
        rect = fitz.Rect(e["bbox"])
        return any(abs(rect & g) >= DUPLICATE_PHOTO_OVERLAP * min(abs(rect), abs(g)) for g in geometric)

    return [e for e in elements if not duplicate(e)]
