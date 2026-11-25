"""Trang scan lưu cả chữ máy (Tesseract / lớp OCR ẩn) lẫn chữ luna ⇒ cùng một con số / ngày có thể có hai giá trị, LLM phải đoán bên nào đúng.
Module này tìm số / ngày máy đọc LỆCH luna (đọc khác cùng một chỗ in) để process_pdf xoá khỏi chữ máy — luna đúng hơn ở các cặp phân xử được.
Số: đo trên 230 trang có 2 bản, luna đúng 7 / 7 cặp phân xử được (CHANGELOG: CH-20261020-A4). Ngày: người vận hành luna khi hai bản lệch
(CHANGELOG: CH-20261021-A11). Chỉ xử lý chuỗi, không cần PyMuPDF."""
import re

# Con số trên trang: chữ số nối bằng "." hoặc "," ("37,307,000,821"). KHÔNG gồm "/": "342/2016" là hai số. Dùng cho so số ở đây và bù số lớp OCR ẩn
# lớp OCR ẩn (process_pdf.layer_number_lines). Trước 22/10 process_pdf có thêm một bản gồm "/" nhưng bị bản này đè ⇒ chưa bao giờ chạy
# (CHANGELOG: CH-20261022-B2)
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# Số hiệu văn bản 3 chữ số trước "/" + chữ in hoa (luna "336/QĐ-UBND" ↔ máy "936/QĐ-UBND")
_DOCUMENT_NUMBER = re.compile(r"(?<!\d)(\d{3,})/(?=[A-ZĐ])")
DATE = re.compile(r"(?<!\d)(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*((?:19|20)\d{2})(?!\d)"
                  r"|ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+((?:19|20)\d{2})(?!\d)", re.IGNORECASE)


def digits(number):
    return re.sub(r"[.,]", "", number)


def numbers_in(text):
    """Mọi con số của chuỗi, bỏ dấu ngăn cách (để so "1.234.560" với "1234560")."""
    return {digits(n) for n in NUMBER.findall(text)}


def _near_number(a, b):
    """Cùng một con số in trên trang mà hai bản đọc khác: cùng số chữ số, khác 1 chữ số (số 4–5 chữ số) hoặc ≤ 2 (từ 6 chữ số). Số ngắn khác 2 chữ số
    thường là hai số khác nhau thật (năm 2006 ↔ 2015) ⇒ không tính."""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= (1 if len(a) <= 5 else 2)


def _comparable_numbers(text):
    """Số đem so giữa hai bản: ≥ 4 chữ số trừ số dạng năm 1900–2099 (hai năm khác 1 chữ số thường là hai năm thật, luna sót một năm thì không được xoá năm
    của máy — test 20/10), cộng số hiệu văn bản."""
    kept = {n for n in numbers_in(text) if len(n) >= 4 and not (len(n) == 4 and 1900 <= int(n) <= 2099)}
    return kept | set(_DOCUMENT_NUMBER.findall(text))


def conflicting_ocr_numbers(ocr_text, vlm_text):
    """Số chỉ có trong bản Tesseract mà bản luna có một số "gần giống" ⇒ hai bản đọc khác cùng một con số. So với MỌI số của luna (số luna đã có thì không phải mâu thuẫn).
    Ngày tháng không so ở đây (`conflicting_ocr_dates` lo): "31.12.2023" đọc như số 8 chữ số sẽ bị coi là lệch với "31.12.2024" và xoá mất một ngày thật."""
    ocr_text, vlm_text = DATE.sub(" ", ocr_text), DATE.sub(" ", vlm_text)
    vlm_all, ocr_all = numbers_in(vlm_text), numbers_in(ocr_text)
    only_ocr = sorted(n for n in _comparable_numbers(ocr_text) if n not in vlm_all)
    only_vlm = sorted(n for n in _comparable_numbers(vlm_text) if n not in ocr_all)
    # Ghép 1–1 (như phép đo của B): một số luna chỉ "giải thích" một số Tesseract — hai số Tesseract khác nhau gần cùng một số luna có thể là hai số thật (luna sót một)
    bad, used = set(), set()
    for a in only_ocr:
        b = next((b for b in only_vlm if b not in used and _near_number(a, b)), None)
        if b:
            used.add(b)
            bad.add(a)
    return bad


def remove_numbers(text, bad):
    """Xoá các con số (theo chữ số, bỏ dấu ngăn cách) thuộc `bad`, giữ mọi chữ khác."""
    return NUMBER.sub(lambda m: "" if digits(m.group()) in bad else m.group(), text)


def _date_key(match):
    """(ngày, tháng, năm) của "29/5/2012", "29-05-2012" hoặc "ngày 29 tháng 5 năm 2012" — hai cách viết cùng một ngày so bằng nhau."""
    return tuple(int(g) for g in match.groups() if g is not None)


def dates(text):
    return {key for key in map(_date_key, DATE.finditer(text)) if 1 <= key[0] <= 31 and 1 <= key[1] <= 12}


def _near_date(a, b):
    """Cùng năm, khác đúng một trong hai: ngày hoặc tháng ("2/5/2012" ↔ "29/5/2012") ⇒ hai bản đọc khác cùng một ngày. Khác năm thì không tính:
    BCTC hay có hai ngày thật cùng ngày–tháng khác năm (31/12/2024 ↔ 31/12/2023)."""
    return a[2] == b[2] and (a[0] == b[0]) != (a[1] == b[1])


def conflicting_ocr_dates(ocr_text, vlm_text):
    """Ngày chỉ có trong bản Tesseract mà bản luna có đúng MỘT ngày "gần giống", và ngày luna đó cũng chỉ gần một ngày máy. Không rõ cặp
    (máy 1/6 + 15/6 ↔ luna 16/6; luna sót một trong hai ngày thật) ⇒ không xoá — xoá nhầm ngày thật tệ hơn để sót một ngày lệch."""
    ocr_all, vlm_all = dates(ocr_text), dates(vlm_text)
    only_ocr, only_vlm = ocr_all - vlm_all, vlm_all - ocr_all
    bad = set()
    for a in only_ocr:
        near = [b for b in only_vlm if _near_date(a, b)]
        if len(near) == 1 and sum(_near_date(o, near[0]) for o in only_ocr) == 1:
            bad.add(a)
    return bad


def remove_dates(text, bad):
    """Xoá các ngày (so theo (ngày, tháng, năm)) thuộc `bad`, giữ mọi chữ khác."""
    return DATE.sub(lambda m: "" if _date_key(m) in bad else m.group(), text)
