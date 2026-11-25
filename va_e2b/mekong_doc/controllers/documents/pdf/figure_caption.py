"""Tìm cặp ảnh–chú thích trong PDF bằng hình học (PyMuPDF). Không OCR, không model, không gọi API.

Mục đích: mỗi cặp ảnh–chú thích thành MỘT point Qdrant có trang, figure_id, bbox và ảnh (xem node_structured_pdf),
để câu hỏi như "Norman Norell Coat 1964" trả về đúng hình chứ không chỉ cả trang.

Một trang chỉ được nhận khi thỏa tất cả:
- có khoảng trống dọc rộng >= MIN_GAP_PT giữa nội dung (còn chữ/ảnh ở cả hai phía) => ranh giới cột;
- có ảnh nhỏ (< MAX_PAGE_COVER diện tích trang, mỗi cạnh >= MIN_FIG_PT) nằm bên phải khoảng trống;
- có dòng chữ kề ảnh: tâm y nằm trong dải y của ảnh (± Y_MARGIN_PT); nhóm chú thích của một ảnh hợp lệ khi dòng gần ảnh nhất
  cách ảnh <= MAX_CAPTION_GAP_PT (từng dòng được phép xa tới MAX_LINE_GAP_PT, vì chú thích căn trái có dòng ngắn).
Trang không thỏa thì không thêm gì, nên hành vi Loader trên các PDF khác giữ nguyên.

Giới hạn đã biết (kiểm trên 1 tài liệu catalogue + 7 PDF thật cũ): chỉ xử lý bố cục "cột chữ bên trái, cột ảnh + chú thích
bên phải". Chú thích dưới ảnh, ảnh trong cột chữ, ảnh chồng nhau, chú thích dạng ảnh hoặc chữ vector sẽ không được nhận.
"""
import base64
import logging
import re

import fitz

logger = logging.getLogger(__name__)

MIN_GAP_PT = 20
MARGIN_PT = 30
MIN_FIG_PT = 40
MAX_PAGE_COVER = 0.3
Y_MARGIN_PT = 6
MAX_CAPTION_GAP_PT = 40
MAX_LINE_GAP_PT = 120
MAX_FIGS_PER_PAGE = 12
MAX_CAPTION_LINES = 12
MAX_IMAGE_BYTES = 200_000


def _page_lines(page):
    lines = []
    # TEXTFLAGS_TEXT: không kèm byte ảnh (get_text("dict") mặc định trích cả ảnh, rất chậm ở PDF scan)
    for block in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            text = re.sub(r"\s+", " ", "".join(span["text"] for span in line["spans"])).strip()
            if text:
                lines.append({"bbox": list(line["bbox"]), "text": text})
    return lines


def _widest_column_gap(width, lines, image_boxes):
    """Khoảng trống dọc rộng nhất (không dòng chữ/ảnh nào phủ), còn nội dung ở cả hai phía; None nếu không có."""
    w = int(width)
    cover = [0] * (w + 1)
    for box in [line["bbox"] for line in lines] + image_boxes:
        for x in range(max(0, int(box[0])), min(w, int(box[2])) + 1):
            cover[x] += 1
    best, start = None, None
    for x in range(MARGIN_PT, w - MARGIN_PT):
        if cover[x] == 0 and start is None:
            start = x
        if (cover[x] != 0 or x == w - MARGIN_PT - 1) and start is not None:
            if x - start >= MIN_GAP_PT and sum(cover[:start]) > 0 and sum(cover[x:]) > 0:
                if best is None or x - start > best[1] - best[0]:
                    best = (start, x)
            start = None
    return best


def _image_base64(doc, xref):
    try:
        pix = fitz.Pixmap(doc, xref)
        if pix.alpha or (pix.colorspace is not None and pix.colorspace.n != 3):
            pix = fitz.Pixmap(fitz.csRGB, pix)
        raw = pix.tobytes("png")
    except Exception as e:
        logger.warning(f"Cannot export figure image xref={xref}: {e}")
        return ""
    return base64.b64encode(raw).decode() if len(raw) <= MAX_IMAGE_BYTES else ""


def _is_small_figure(bbox, width, height):
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    return w >= MIN_FIG_PT and h >= MIN_FIG_PT and w * h < MAX_PAGE_COVER * width * height


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _figure_label(index):
    return "abcdefgh"[index] if index < 8 else f"i{index}"


def find_figure_caption_pairs(file_path):
    """Danh sách dict (một dict một cặp): type, page, figure_id, bbox, caption_bbox, caption_lines, image_base64, image_px."""
    pairs = []
    with fitz.open(file_path) as doc:
        for page in doc:
            if not page.get_images():
                continue  # rẻ: trang không có ảnh thì không có cặp ảnh–chú thích
            width, height = page.rect.width, page.rect.height
            # get_image_info() thường rẻ (0,05 s/47 trang scan) còn xrefs=True đắt (3 s): chỉ gọi bản đắt khi có ảnh nhỏ
            if not any(_is_small_figure(info["bbox"], width, height) for info in page.get_image_info()):
                continue
            images = [{"bbox": list(info["bbox"]), "xref": info["xref"], "px": [info["width"], info["height"]]}
                      for info in page.get_image_info(xrefs=True)]
            small = [im for im in images if im["xref"] and _is_small_figure(im["bbox"], width, height)]
            if not small:
                continue
            lines = _page_lines(page)
            if not lines:
                continue
            gap = _widest_column_gap(width, lines, [im["bbox"] for im in images])
            if gap is None:
                continue
            sep = (gap[0] + gap[1]) / 2
            side = sorted((im for im in small if (im["bbox"][0] + im["bbox"][2]) / 2 >= sep),
                          key=lambda im: im["bbox"][1])[:MAX_FIGS_PER_PAGE]
            captions = {k: [] for k in range(len(side))}
            for line in lines:
                lb = line["bbox"]
                if (lb[0] + lb[2]) / 2 < sep:
                    continue
                cy = (lb[1] + lb[3]) / 2
                if any(im["bbox"][0] <= (lb[0] + lb[2]) / 2 <= im["bbox"][2] and im["bbox"][1] <= cy <= im["bbox"][3] for im in side):
                    continue  # chữ nằm đè lên ảnh: không phải chú thích kề ảnh
                best = None
                for k, im in enumerate(side):
                    b = im["bbox"]
                    if b[1] - Y_MARGIN_PT <= cy <= b[3] + Y_MARGIN_PT:
                        dist = max(b[0] - lb[2], lb[0] - b[2], 0)
                        if dist <= MAX_LINE_GAP_PT and (best is None or dist < best[0]):
                            best = (dist, k)
                if best:
                    captions[best[1]].append((best[0], line))
            for k, im in enumerate(side):
                if not captions[k] or min(d for d, _ in captions[k]) > MAX_CAPTION_GAP_PT:
                    continue
                cap = sorted((ln for _, ln in captions[k]), key=lambda ln: (round(ln["bbox"][1]), ln["bbox"][0]))
                if len(cap) > MAX_CAPTION_LINES:
                    continue
                pairs.append({
                    "type": "figure_caption",
                    "page": page.number + 1,
                    "figure_id": f"p{page.number + 1}-{_figure_label(k)}",
                    "bbox": [round(v, 1) for v in im["bbox"]],
                    "caption_bbox": [round(v, 1) for v in _union([ln["bbox"] for ln in cap])],
                    "caption_lines": [ln["text"] for ln in cap],
                    "image_px": im["px"],
                    "image_base64": _image_base64(doc, im["xref"]),
                })
    return pairs


def add_figure_caption_pairs(file_path, raw_elements):
    """Thêm các dict cặp ảnh–chú thích vào cuối danh sách phần tử của trang tương ứng. Lỗi thì ghi log, không làm hỏng Loader."""
    try:
        pairs = find_figure_caption_pairs(file_path)
    except Exception as e:
        logger.warning(f"Figure-caption detection failed: {e}")
        return
    added = 0
    for pair in pairs:
        if 1 <= pair["page"] <= len(raw_elements):
            raw_elements[pair["page"] - 1].append(pair)
            added += 1
    if added:
        logger.info(f"Figure-caption pairs found: {added}")
