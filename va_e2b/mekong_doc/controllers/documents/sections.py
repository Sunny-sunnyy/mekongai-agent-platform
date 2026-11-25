"""Dựng "mục theo tiêu đề" dùng chung cho các Loader tài liệu chữ — Word, TXT, Markdown, HTML (đợt 2026-10-24_file_khac_pdf).
Mỗi Loader chỉ nhận ra tiêu đề / đoạn / bảng theo cách của định dạng mình rồi gọi build_sections; chia đoạn ở
controllers/modules/node_structured.py::node_structured_sections. Một đường cho mọi loại chữ (không mỗi loại một cách chia)."""
import re

# Tiêu đề nhận theo chữ (file không có kiểu tiêu đề — văn bản hành chính VN hay vậy): dòng ngắn, không kết thúc bằng dấu câu.
HEADING_MAX_WORDS = 20
KEYWORD_HEADING = re.compile(r"(phần|chương|mục)\s+([ivxlc]+|\d+)\b|điều\s+\d+", re.IGNORECASE)
KEYWORD_LEVEL = {"phần": 1, "chương": 1, "mục": 2}   # "Điều" ⇒ 3
ROMAN_HEADING = re.compile(r"[IVXLC]+\.\s")
NUMBERED_HEADING = re.compile(r"\d+(\.\d+)*\.?\s")
# Dòng gạch đầu dòng in đậm ("* Kê khai tài sản", "- Quy trình") là ý nhấn mạnh trong danh sách, không phải tiêu đề mục — nhận nhầm làm nó thay chỗ
# "MỤC 1…" và thành mục cha của các Điều bên dưới ⇒ tên mục trích nguồn sai (BAO_CAO_C mục 15, Đ1 đêm 24/10)
LIST_MARKER = re.compile(r"[*•+]|[\-–]\s")   # "*Tuyển dụng" (dính liền) cũng là gạch đầu dòng; "-" / "–" cần dấu cách (tránh số âm "-5 %")
# Dòng "Điều / Chương … + số" là tiêu đề dù dài và kết thúc bằng dấu câu ("Điều 14: Chi hội nghị, hội thảo, tổng kết cuối năm.", "Điều 29. Tiền nước
# uống cho học sinh:"). 40 từ: CHỌN SAU KHI XEM MẪU — đã kiểm chéo 24/10 thấy tiêu đề "Điều" dài nhất 29 từ trên 16 file Word thật (w01, w02, w04 …,
# nay là bộ đã thấy); kiểm cuối lần 2 trên file Word mới.
KEYWORD_HEADING_MAX_WORDS = 40
KEYWORD_SEPARATOR = re.compile(r"\s*[.:–\-]")


def keyword_heading_level(text):
    """Cấp của dòng "Phần / Chương / Mục + số (La Mã / Ả Rập)" hoặc "Điều + số" — luật chung của văn bản hành chính VN — None nếu không phải.
    Câu thân bài cũng có thể bắt đầu như vậy ("Điều 5 Luật Lao động quy định …", "Chương 2 trình bày …") ⇒ chỉ nhận khi sau số có dấu ngắt
    (". : – -") hoặc hết dòng, hoặc là dòng ngắn viết hoa đầu, không kết thúc bằng dấu câu ("Điều 3 Giờ làm việc")."""
    keyword = KEYWORD_HEADING.match(text or "")
    if not keyword or len(text.split()) > KEYWORD_HEADING_MAX_WORDS:
        return None
    level = KEYWORD_LEVEL.get((keyword.group(1) or "").lower(), 3)
    rest = text[keyword.end():].strip()
    # Dòng liệt kê "Chương 4 – Thực nghiệm: mô tả bộ dữ liệu …" (đề cương, mục lục viết tay): sau tên còn "nhãn: mô tả" ⇒ không phải tiêu đề
    if ":" in rest.lstrip(".:–- ").rstrip(": "):   # ":" cuối dòng ("Điều 27. Công tác xã hội hoá giáo dục:") vẫn là tiêu đề
        return None
    if not rest or KEYWORD_SEPARATOR.match(text[keyword.end():]):
        return level
    if rest[0].isupper() and len(text.split()) <= HEADING_MAX_WORDS and text[-1] not in ".:;,":
        return level
    return None


def text_heading_level(text, stands_out):
    """Cấp tiêu đề của một dòng theo luật chữ chung (không theo một mẫu nào), None nếu là chữ thường: "Phần / Chương / Mục / Điều + số"
    (keyword_heading_level), "I." số La Mã, hoặc dòng nổi bật — Word: cả dòng in đậm; TXT: dòng ngắn đứng riêng giữa hai dòng trống — không
    chứa ":" ("nhãn: giá trị" là thông tin); "1." chỉ là tiêu đề khi nổi bật (không thì là danh sách)."""
    keyword = keyword_heading_level(text)
    if keyword is not None:
        return keyword
    if not text or len(text.split()) > HEADING_MAX_WORDS or text[-1] in ".:;,":
        return None
    if not any(char.isalnum() for char in text):   # đường kẻ "---", "***" đứng riêng (TXT) không phải tiêu đề
        return None
    if ROMAN_HEADING.match(text):
        return 2
    if ":" not in text and stands_out and not LIST_MARKER.match(text):
        number = NUMBERED_HEADING.match(text)
        return 3 + number.group(0).strip().rstrip(".").count(".") if number else 2
    return None


def build_sections(items):
    """items theo thứ tự trong file: ("heading", cấp, chữ) | ("text", chữ) | ("table", [[ô, ...], ...]) | ("image", mã ảnh) ⇒ danh sách mục
    {"title": "Chương II > Điều 3. Giờ làm việc", "blocks": [chữ | {"rows": [[ô, ...], ...]}]}. title rỗng = phần trước tiêu đề đầu tiên /
    file không có tiêu đề. Tiêu đề bị thay trước khi có chữ bên dưới (tên công ty, tên văn bản ở đầu file) ⇒ chữ của nó thành dòng đầu của
    mục có nội dung kế tiếp, không mất."""
    sections, path, current, pending_titles = [], [], [], []   # path: [[cấp, chữ tiêu đề, đã nằm trong tên một mục chưa]]

    def add(block):
        current.extend(pending_titles)
        pending_titles.clear()
        current.append(block)

    def close_section():
        if current:
            sections.append({"title": " > ".join(entry[1] for entry in path), "blocks": current.copy()})
            current.clear()
            for entry in path:
                entry[2] = True

    for item in items:
        if item[0] == "heading":
            _, level, text = item
            close_section()
            pending_titles.extend(entry[1] for entry in path if entry[0] >= level and not entry[2])
            path[:] = [entry for entry in path if entry[0] < level] + [[level, text, False]]
        elif item[0] == "table":
            if item[1]:
                add({"rows": item[1]})
        elif item[0] == "lead":   # chữ mở đầu đi theo mục kế tiếp (Word: quốc hiệu + dòng ngắn liền sau — process_docx, 29/10)
            pending_titles.append(item[1])
        elif item[0] == "image":   # Word: chỗ một ảnh nhúng (process_docx.describe_images thay bằng mô tả luna hoặc bỏ)
            add({"image": item[1]})
        elif item[1]:
            add(item[1])
    if not current:   # tiêu đề cuối file không có chữ bên dưới
        current.extend(pending_titles + [entry[1] for entry in path if not entry[2]])
    else:             # chữ mở đầu ở cuối file, sau đó không còn gì ⇒ vẫn giữ (chỉ khối "lead" làm pending còn khi mục đang có chữ)
        current.extend(pending_titles)
    close_section()
    return sections
