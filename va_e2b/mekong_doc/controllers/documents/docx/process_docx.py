"""Loader Word (.docx): đọc thẳng file theo thứ tự thân bài và chia theo mục / tiêu đề (đợt 2026-10-24_file_khac_pdf).
Word không lưu trang thật (trang do Word tính lúc hiển thị) ⇒ "trang" của tài liệu Word là một mục (người vận hành 23/10).
Bản trước (tới v3.1): cả file thành một "trang" HTML kèm CSS, chỉ tách ở ngắt trang thủ công; bỏ bảng lồng, đầu / chân trang, chú thích,
hộp chữ; tiêu đề chỉ nhận style "Heading 1–3". `.doc` không đọc ở đây: image không có LibreOffice để chuyển ⇒ API báo "chưa hỗ trợ"."""
import base64
import io
import logging
import re
import unicodedata
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree
from PIL import Image

from controllers.documents.pdf import page_vision
from controllers.documents.sections import build_sections, keyword_heading_level, text_heading_level

logger = logging.getLogger(__name__)

W_T, W_TAB, W_BR, W_CR = qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")
W_TXBX = qn("w:txbxContent")
# Word ghi hộp chữ hai lần: bản mới (mc:Choice) và bản cho Word cũ (mc:Fallback) ⇒ chỉ đọc một bản
MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
A_BLIP, R_EMBED, R_ID = qn("a:blip"), qn("r:embed"), qn("r:id")
V_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"   # ảnh kiểu Word cũ (VML)
# Ảnh trong Word (W6, đợt 2026-10-24_file_khac_pdf): luna đọc bằng đúng hàm + prompt đọc trang PDF. Bỏ ảnh cạnh < 80 px (biểu tượng, gạch đầu dòng) và ảnh
# dùng ≥ 3 lần trong file (logo). Đếm 24/10 trên 23 file Word thật đã thấy: 11 ảnh nội dung, 1 biểu tượng, 0 ảnh lặp — ngưỡng chọn trước khi xem ảnh nào.
IMAGE_MIN_SIDE = 80
IMAGE_REPEAT_MIN = 3
FIGURE_JPEG_QUALITY = 85     # ảnh "hình" lưu kèm point để app hiện lại — như ảnh chụp PDF (chart_crop.PHOTO_JPEG_QUALITY)
FIGURE_MAX_SIDE = 1600       # px cạnh dài ảnh lưu (chart_crop.MAX_IMAGE_SIDE)
FIGURE_KINDS = ("anh_chup", "bieu_do", "so_do")
ARTICLE_LEVEL = 3    # cấp sections.keyword_heading_level trả cho "Điều N"
NATIONAL_MOTTO = re.compile(r"CỘNG H(?:ÒA|OÀ) XÃ HỘI CHỦ NGHĨA VIỆT NAM", re.IGNORECASE)   # "HÒA" / "HOÀ" đều gặp
# Dòng ngắn sau quốc hiệu ("…, ngày … tháng … năm", "____") đi theo mục kế tiếp. CHỌN SAU KHI XEM MẪU (29/10): 3 đoạn quốc hiệu ngoài bảng
# của 54 file Word thử, mục quốc hiệu chỉ có 1–2 dòng ngắn như vậy
LEAD_MAX_LINES = 3
LEAD_MAX_WORDS = 12


def process_docx(file_path):
    """.docx ⇒ danh sách mục theo thứ tự trong file: {"title": "Chương II > Điều 3. Giờ làm việc", "blocks": [...]}.
    block là chuỗi (một đoạn) hoặc {"rows": [[ô, ...], ...]} (một bảng). title rỗng = phần trước tiêu đề đầu tiên / file không có tiêu đề.
    Đầu / chân trang (nếu có) thành mục riêng ở đầu. Lỗi đọc file ⇒ ném lên (tải lên báo thất bại)."""
    document = open_docx(file_path)
    footnotes = read_footnotes(document)
    blocks = list(iter_blocks(document.element.body, document))
    # File có dùng style tiêu đề ⇒ tin style, không đoán theo chữ đậm (chữ đậm nhấn mạnh trong đoạn không thành mục). Chỉ tính đoạn CÓ chữ:
    # một đoạn rỗng mang style tiêu đề (v03, B kiểm cuối lần 2) làm tắt luật "I." / chữ đậm ⇒ cả file thành một mục (vòng 3 Word, đã duyệt 11:3x)
    use_styles = any(isinstance(block, Paragraph) and block.text.strip() and style_heading_level(block) is not None for block in blocks)

    items = []   # ("heading", cấp, chữ) | ("text", chữ) | ("lead", chữ) | ("table", hàng) — mục dựng ở sections.build_sections (chung với TXT / MD / HTML)
    open_article = None   # cấp của "Điều N" (luật chữ) vừa gặp mà chưa có chữ nào bên dưới
    lead_left = 0         # số dòng ngắn sau quốc hiệu còn được đi theo mục kế tiếp
    for block in blocks:
        if isinstance(block, Table):
            open_article, lead_left = None, 0
            items.append(("table", table_rows(block)))
            # Ảnh trong ô bảng (danh mục sản phẩm có ảnh, bảng ký có con dấu) đọc như ảnh thân bài, mô tả đặt ngay sau bảng — trước 28/10 bị bỏ
            # (hỏi về ảnh trả lời "không có"; dòng 97 khung năng lực). Con dấu / chữ ký chỉ giữ chữ mô tả như PDF; ảnh lặp (logo) vẫn bỏ
            items += [("image", image_id) for image_id in image_ids(block._tbl)]
            continue
        text = " ".join(paragraph_text(block._p, footnotes).split())
        # Quốc hiệu không phải tiêu đề: nó, hộp chữ của nó ("Mẫu số 01" của biểu mẫu) và vài dòng ngắn liền sau đi vào mục KẾ TIẾP — trước 29/10
        # quốc hiệu in đậm (file không dùng style) thành một mục riêng, tách số hiệu mẫu khỏi chính mẫu ⇒ "Mẫu số 01 dành cho ai" trả nhầm (CS19, C)
        if NATIONAL_MOTTO.match(unicodedata.normalize("NFC", text)):
            items += [("lead", line) for line in [text] + text_box_lines(block._p)]
            lead_left = LEAD_MAX_LINES
            continue
        # dòng ngắn là tiêu đề ("PHIẾU ĐĂNG KÝ DỰ TUYỂN", "QUYẾT ĐỊNH") thì giữ vai trò tiêu đề — chữ mở đầu đi vào mục của nó (F-C15-1, B 29/10)
        if lead_left and text and len(text.split()) <= LEAD_MAX_WORDS and heading_level(block, text, use_styles) is None:
            items += [("lead", line) for line in [text] + text_box_lines(block._p)]
            lead_left -= 1
            continue
        if text:   # đoạn rỗng giữa quốc hiệu và dòng ngày tháng không đóng cửa sổ
            lead_left = 0
        level = heading_level(block, text, use_styles)
        # Ngay dưới "Điều N" chưa có chữ, đoạn không phải "Phần / Chương / Mục / Điều" mà mang dấu tiêu đề ngang / cao hơn Điều (người soạn đặt style
        # Heading hay mức dàn ý lên khoản 1) là chữ của Điều — trước 29/10 nó thay "Điều N" làm tên mục (6 / 202 "Điều" ở 4 / 54 file Word thử)
        if level is not None and open_article is not None and level <= open_article and keyword_heading_level(text) is None:
            level = None
        if level is not None and text:
            open_article = level if keyword_heading_level(text) == ARTICLE_LEVEL else None
            items.append(("heading", level, text))
            items += [("image", image_id) for image_id in image_ids(block._p)]   # ảnh neo vào đoạn tiêu đề (w04, W6 vòng 2) không bị bỏ
            continue
        lines = ([text] if text else []) + text_box_lines(block._p)
        if lines:
            open_article = None
        items += [("text", line) for line in lines]
        items += [("image", image_id) for image_id in image_ids(block._p)]
    sections = build_sections(items)
    describe_images(document, sections)

    header_footer = header_footer_lines(document)
    if header_footer:
        # Tên mục kèm tiêu đề đầu tiên của file: chữ chân trang ("BM.04-QT.CTSV.05") một mình không mang tên tài liệu ⇒ câu "Bảng điểm … mã
        # biểu mẫu là gì" không tìm ra (kiểm cuối 24/10, k16 sai ở cả bản cũ lẫn bản mới)
        first = next((s["title"].split(" > ")[0] for s in sections if s["title"]), "")
        first = first or " ".join(next((b for s in sections for b in s["blocks"] if isinstance(b, str)), "").split()[:15])
        sections.insert(0, {"title": f"Đầu trang / chân trang — {first}" if first else "Đầu trang / chân trang", "blocks": header_footer})
    return sections


def image_ids(element):
    """Mã (rId) các ảnh nhúng trong một đoạn theo thứ tự; bỏ bản sao trong mc:Fallback (Word ghi một ảnh hai lần cho Word cũ)."""
    ids = []
    for node in element.iter(A_BLIP, V_IMAGEDATA):
        if any(ancestor.tag == MC_FALLBACK for ancestor in node.iterancestors()):
            continue
        image_id = node.get(R_EMBED) or node.get(R_ID)
        if image_id:
            ids.append(image_id)
    return ids


def describe_images(document, sections, client=None, on_usage=None):
    """Thay mỗi khối {"image": rId} của các mục bằng {"figure": {"text": mô tả luna, "image_base64": JPEG}}, hoặc bỏ khối: ảnh nhỏ / lặp / không mở
    được (EMF, WMF), luna tắt (PDF_VLM_ENABLED=false), luna lỗi hay báo trang trí. Mục chỉ có ảnh mà ảnh bị bỏ ⇒ bỏ mục. Đọc song song như trang PDF;
    trần số ảnh = page_vision.VLM_MAX_PAGES_PER_FILE. client / on_usage: như page_vision.describe_pages (None ⇒ client của app)."""
    blocks = [block for section in sections for block in section["blocks"] if isinstance(block, dict) and "image" in block]
    uses = Counter(block["image"] for block in blocks)
    images = {}
    for image_id, count in uses.items():
        if count >= IMAGE_REPEAT_MIN:
            continue
        try:
            image = Image.open(io.BytesIO(document.part.related_parts[image_id].blob))
            image.load()
        except Exception:   # EMF / WMF / ảnh liên kết ngoài file: PIL không mở được ⇒ bỏ ảnh, không làm hỏng file
            continue
        if min(image.size) >= IMAGE_MIN_SIDE:
            images[image_id] = image.convert("RGB")
    readings = {}
    if images and page_vision.vlm_enabled():
        if client is None:
            client, on_usage = page_vision.app_client()
        chosen = list(images)[:page_vision.VLM_MAX_PAGES_PER_FILE]
        with ThreadPoolExecutor(max_workers=page_vision.VLM_WORKERS) as executor:
            readings = dict(zip(chosen, executor.map(lambda image_id: read_image(client, images[image_id], on_usage), chosen)))
    number = 0
    for section in sections:
        kept = []
        for block in section["blocks"]:
            if not (isinstance(block, dict) and "image" in block):
                kept.append(block)
                continue
            reading = readings.get(block["image"])
            text = figure_text(reading, number + 1)
            if text:
                number += 1
                # Lưu ảnh để hiện lại chỉ khi là ảnh chụp / biểu đồ / sơ đồ — như PDF (chart_crop.crop_figures): con dấu, chữ ký, logo chỉ giữ chữ mô tả
                shown = any(visual.get("kind") in FIGURE_KINDS for visual in reading.get("visuals", []))
                kept.append({"figure": {"text": text, "image_base64": jpeg_base64(images[block["image"]], FIGURE_MAX_SIDE) if shown else ""}})
        section["blocks"] = kept
    sections[:] = [section for section in sections if section["blocks"]]


def read_image(client, image, on_usage):
    """Một lượt luna cho một ảnh (prompt đọc trang PDF page-v7). Lỗi ⇒ None và ghi log: bỏ ảnh đó, không làm hỏng cả file."""
    try:
        data_uri = "data:image/jpeg;base64," + jpeg_base64(image, page_vision.VLM_LONG_SIDE, quality=90)
        return page_vision.describe_page(client, data_uri, on_usage)
    except Exception as error:
        logger.warning(f"VLM failed for Word image: {type(error).__name__}")
        return None


def jpeg_base64(image, max_side, quality=FIGURE_JPEG_QUALITY):
    image = image.copy()
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode()


def figure_text(reading, number):
    """Chữ của một hình từ kết quả luna (JSON page-v7): dòng nguồn, từng phần hình (loại: mô tả, chú thích), chữ đọc được trong ảnh. "" ⇒ bỏ hình."""
    if not reading or reading.get("is_blank_or_decorative"):
        return ""
    lines = [f"[Hình {number} — mô tả do mô hình đọc ảnh]"]
    for visual in reading.get("visuals", []):
        description = visual.get("description_vi", "").strip()
        if description:
            caption = visual.get("caption", "").strip()
            lines.append(f"- {visual.get('kind', 'khac')}: {description}" + (f" (chú thích: {caption})" if caption else ""))
    text_lines = [line.strip() for line in reading.get("text_lines", []) if line.strip()]
    if text_lines:
        lines.append("Chữ trong ảnh: " + " | ".join(text_lines))
    return "\n".join(lines) if len(lines) > 1 else ""


def open_docx(file_path):
    """python-docx Document của file. Zip có tên phần dùng "\\" thay "/" ("word\\document.xml" — gặp ở file Word ký số tải từ Công báo, 29/10;
    Word mở được, python-docx báo thiếu phần ⇒ app báo "file hỏng") ⇒ đọc từ bản sao trong bộ nhớ đã đổi "\\" thành "/"; file gốc không đổi,
    file chuẩn đi đường cũ. Cỡ giải nén đã được api/document.py chặn (200 MB) trước khi tới đây."""
    with zipfile.ZipFile(file_path) as archive:
        if not any("\\" in name for name in archive.namelist()):
            return Document(file_path)
        fixed = io.BytesIO()
        with zipfile.ZipFile(fixed, "w", zipfile.ZIP_DEFLATED) as copy:
            for info in archive.infolist():
                copy.writestr(info.filename.replace("\\", "/"), archive.read(info))
    fixed.seek(0)
    return Document(fixed)


def is_readable_docx(file_path):
    """File mở được như .docx không — file tải thiếu byte / đổi đuôi thì python-docx ném lỗi zip hoặc thiếu phần trong gói. API dùng để báo
    người dùng "file hỏng" thay vì lời chung "processing failed" (TIEU_CHI đợt 2026-10-24 bổ sung 10:08)."""
    try:
        open_docx(file_path)
        return True
    except Exception:
        return False


def iter_blocks(parent_element, document):
    """Đoạn và bảng của thân bài theo thứ tự; khối nội dung (w:sdt) đọc phần bên trong, trừ mục lục (lặp lại chữ tiêu đề)."""
    for child in parent_element.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            if not (paragraph.style is not None and (paragraph.style.name or "").lower().startswith("toc")):
                yield paragraph
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)
        elif child.tag == qn("w:sdt"):
            gallery = child.find(f".//{qn('w:docPartGallery')}")
            if gallery is not None and gallery.get(qn("w:val")) == "Table of Contents":
                continue
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from iter_blocks(content, document)


def paragraph_text(element, footnotes):
    """Chữ một đoạn: nối mọi w:t (kể cả trong liên kết, ô nội dung, chữ thêm khi sửa có theo dõi) KHÔNG chèn dấu cách giữa các run —
    Word hay tách một từ thành nhiều run ("M" + "SV"). Bỏ hộp chữ (đọc riêng) và bản sao mc:Fallback. Số chú thích ⇒ chèn chữ chú thích."""
    parts = []
    for child in element:
        if child.tag in (W_TXBX, MC_FALLBACK):
            continue
        if child.tag == W_T:
            parts.append(child.text or "")
        elif child.tag in (W_TAB, W_BR, W_CR):
            parts.append(" ")
        elif child.tag == qn("w:footnoteReference"):
            note = footnotes.get(child.get(qn("w:id")))
            if note:
                parts.append(f" (Chú thích: {note})")
        elif child.tag != qn("w:instrText"):   # mã trường (field code) không phải chữ hiển thị
            parts.append(paragraph_text(child, footnotes))
    return "".join(parts)


def text_box_lines(element):
    """Chữ trong hộp chữ của một đoạn ⇒ mỗi hộp một dòng "[Hộp chữ] …"."""
    lines = []
    for box in element.iter(W_TXBX):
        if any(ancestor.tag == MC_FALLBACK for ancestor in box.iterancestors()):
            continue
        text = " ".join(" ".join(paragraph_text(p, {}).split()) for p in box.iter(qn("w:p")))
        if text.strip():
            lines.append(f"[Hộp chữ] {text.strip()}")
    return lines


def read_footnotes(document):
    """{id: chữ} của chú thích cuối trang (python-docx không đọc phần này)."""
    for rel in document.part.rels.values():
        if rel.reltype == RT.FOOTNOTES:
            root = etree.fromstring(rel.target_part.blob)
            notes = {}
            for note in root.iter(qn("w:footnote")):
                text = " ".join("".join(t.text or "" for t in p.iter(W_T)) for p in note.iter(qn("w:p")))
                if text.strip():
                    notes[note.get(qn("w:id"))] = " ".join(text.split())
            return notes
    return {}


def style_heading_level(paragraph):
    """Cấp tiêu đề theo style: "Title" ⇒ 0, "Heading N" ⇒ N, hoặc mức dàn ý (outline level) đặt ở đoạn / style (kể cả style kế thừa)."""
    level = outline_level(paragraph._p.pPr)
    style = paragraph.style
    while level is None and style is not None:
        name = (style.name or "").lower()
        if name == "title":
            return 0
        match = re.fullmatch(r"heading (\d)", name)
        if match:
            return int(match.group(1))
        level = outline_level(style.element.pPr)
        style = style.base_style
    return level


def outline_level(ppr):
    """w:outlineLvl 0–8 ⇒ cấp 1–9; 9 (hoặc không có) = chữ thường."""
    node = ppr.find(qn("w:outlineLvl")) if ppr is not None else None
    if node is None:
        return None
    value = int(node.get(qn("w:val"), "9"))
    return value + 1 if value < 9 else None


def heading_level(paragraph, text, use_styles):
    """Cấp tiêu đề của một đoạn, None nếu là chữ thường. "Phần / Chương / Mục / Điều + số" luôn là tiêu đề — kể cả file có style tiêu đề:
    B kiểm chéo 24/10 thấy văn bản thật đặt mức dàn ý tuỳ tiện cho vài "Điều" ⇒ chỉ tin style thì 26 / 31 "Điều" mất mục. File không có style
    tiêu đề ⇒ luật chữ chung: thêm "I." số La Mã, hoặc cả dòng in đậm; "1." chỉ là tiêu đề khi in đậm (không đậm = danh sách)."""
    keyword = keyword_heading_level(text)
    if keyword is not None:
        return keyword
    if use_styles:
        return style_heading_level(paragraph)
    return text_heading_level(text, is_bold(paragraph))


def is_bold(paragraph):
    """Mọi run có chữ đều in đậm — đậm đặt ở run hoặc ở style của đoạn (kể cả style kế thừa, ví dụ style riêng của một trường)."""
    runs = [run for run in paragraph.runs if run.text.strip()]
    return bool(runs) and all(run_bold(run, paragraph.style) for run in runs)


def run_bold(run, style):
    if run.bold is not None:
        return run.bold
    while style is not None:
        if style.font.bold is not None:
            return style.font.bold
        style = style.base_style
    return False


def table_rows(table):
    """[[chữ ô, ...], ...]: ô gộp ngang chỉ lấy một lần (python-docx trả cùng một ô cho mỗi cột nó phủ); ô gộp dọc giữ chữ ở mọi hàng nó phủ
    (hàng nào cũng đủ nghĩa khi tách thành đoạn riêng); bảng lồng trong ô nối vào chữ ô."""
    rows = []
    for row in table.rows:
        cells, previous = [], None
        for cell in row.cells:
            if cell._tc is previous:
                continue
            previous = cell._tc
            cells.append(cell_text(cell))
        if any(cells):
            rows.append(cells)
    return rows


def cell_text(cell):
    texts = [" ".join(paragraph_text(p._p, {}).split()) for p in cell.paragraphs]
    for inner in cell.tables:
        texts.append("; ".join(" / ".join(row) for row in table_rows(inner)))   # bảng lồng: hàng cách "; ", ô cách " / "
    return " ".join(text for text in texts if text)


def header_footer_lines(document):
    """Chữ đầu / chân trang của mọi section (bỏ trùng; phần "giống section trước" không đọc lại)."""
    lines = []
    for section in document.sections:
        for part in (section.header, section.first_page_header, section.even_page_header,
                     section.footer, section.first_page_footer, section.even_page_footer):
            if part.is_linked_to_previous:
                continue
            texts = [" ".join(paragraph_text(p._p, {}).split()) for p in part.paragraphs]
            texts += [" | ".join(row) for table in part.tables for row in table_rows(table)]
            for text in texts:
                if text and text not in lines:
                    lines.append(text)
    return lines
