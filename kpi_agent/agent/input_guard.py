r"""Rào câu hỏi gốc: code kiểm chuỗi người dùng gõ TRƯỚC khi gửi mô hình .

Vì sao (kiểm cuối 02/11, `docs/KET_QUA.md` mục 2e): gặp "NV01/../NV02", mô hình tự "làm sạch" thành NV01 + NV02 rồi mới gọi
công cụ ⇒ rào tham số ở công cụ không còn thấy chuỗi gốc. Rào này đứng ở chỗ duy nhất thấy chuỗi gốc; rào ở công cụ giữ nguyên.

Ranh giới xấu ↔ hợp lệ:. XẤU cả câu khi có:
  (1) `..` đứng cạnh `/` hoặc `\`;  (2) `\` dính liền mã NV;  (3) `%2F` / `%5C` / `%2E%2E` dính mã hoặc nằm giữa hai mã;
  (4) mã 2 chữ số không có trong đội (NV00, NV15+ với đội 14 người). Câu nửa xấu nửa hợp lệ ⇒ xấu cả câu.
HỢP LỆ: "NV12/NV13", "NV06...", "21/10", "ASO_Focus/ASO_Focus_2", "NV12 và NV13".
Ngoài ranh giới (không rào ở bậc này): mã 3 chữ số "NV007", "NV 07", "NV07;", chèn lời dặn — rào công cụ vẫn là lớp thứ hai.
Dạng lọt đã biết (03/11, danh sách cho bậc sau — đổi ranh giới thì): dấu cách quanh `..` ("NV01 /.. / NV02"),
"/. ./", ký tự rộng 0 chèn giữa, gạch chéo trông giống (U+2044, U+2215 — NFKC không đổi), mã hoá hai lần "%252F", "NV12/NV13/NV14"."""
import re
import unicodedata

PATH_DOTS = re.compile(r"\.\.[/\\]|[/\\]\.\.")                         # (1)
BACKSLASH_ON_CODE = re.compile(r"NV\d+\\|\\NV\d+", re.IGNORECASE)        # (2)
URL_ENCODED = re.compile(r"%2F|%5C|%2E%2E", re.IGNORECASE)               # (3)
# Mã cho luật (3): không đòi ranh giới chữ — "%2FNV02" có chữ F dính ngay trước mã
CODE_ANYWHERE = re.compile(r"NV\d+", re.IGNORECASE)
# Bậc 3a P5 — yêu cầu LÀM VIỆC KHÁC (viết thư, soạn đơn, dịch, làm thơ, lập trình…) không phải câu hỏi về KPI: code trả một câu cố định, không gọi mô
# hình. Vì sao ở code: 19/11 cả hai mô hình thử trên Capitol đều nói "ngoài phạm vi" rồi VẪN viết đủ bản nháp email xin nghỉ phép; rào số không bắt
# được (không có số sai). Giữ hẹp để không chặn oan câu KPI viết lạ: chỉ chặn khi có ĐỘNG TỪ giao việc + LOẠI VĂN BẢN đứng gần nhau VÀ cả câu không
# có chữ nào của miền KPI. Câu lưỡng lự ("soạn email báo kết quả KPI cho đội") ⇒ để mô hình xử theo lời dặn.
OUT_OF_SCOPE_ANSWER = "Em chỉ trả lời câu hỏi về KPI và thưởng lương kinh doanh của đội."
OTHER_TASK = re.compile(r"\b(?:viết|soạn|dịch|sáng tác|làm|kể|lập trình)\b[^.?!\n]{0,40}?\b(?:e-?mail|thư|đơn xin|bài thơ|thơ|bài văn|bài luận|bài hát|"
                        r"truyện|chuyện cười|câu chuyện|văn bản|hợp đồng|tin nhắn|chương trình|đoạn code|script)\b", re.IGNORECASE)
KPI_WORDS = re.compile(r"kpi|thưởng|lương|chỉ tiêu|doanh số|mốc|đơn hàng|cửa hàng|điểm bán|nhân viên|\bNV\d|\bđội\b|tuyến|mcp|công văn|"
                       r"turnover|pc_bt|pc_on|lppc|aso", re.IGNORECASE)
# 19/11 — 2 mẫu lọt vì không có "loại văn bản" trong danh sách: "Dịch đoạn này sang tiếng Anh: …", "Lập trình giúp một hàm Python …"
OTHER_TASK_MORE = re.compile(r"\bdịch\b[^.?!\n]{0,60}?\bsang tiếng\b|"
                             r"\b(?:lập trình|viết)\b[^.?!\n]{0,40}?\b(?:hàm|đoạn mã|python|javascript|sql)\b", re.IGNORECASE)
# Câu gõ KHÔNG DẤU ("viet email xin nghi phep" — người gõ khung chat hay bỏ dấu): chỉ xét khi CẢ CÂU không có dấu tiếng Việt. Bỏ dấu làm nhiều chữ
# trùng nhau ("thư" / "thu nhập", "thơ" / "thợ", "làm" / "lam") ⇒ loại văn bản ở đây phải là cụm đủ rõ ("buc thu", "bai tho"), không nhận "thu" /
# "tho" đứng một mình; chữ miền KPI thì nhận RỘNG ("doi" = đội / đổi / đôi) — lệch về phía cho câu tới mô hình, không lệch về phía chặn oan.
OTHER_TASK_PLAIN = re.compile(r"\b(?:viet|soan|dich|sang tac|lam|ke|lap trinh)\b[^.?!\n]{0,40}?\b(?:e-?mail|buc thu|la thu|thu xin|don xin|bai tho|bai van|"
                              r"bai luan|bai hat|truyen|chuyen cuoi|cau chuyen|van ban|hop dong|tin nhan|chuong trinh|doan code|script)\b|"
                              r"\bdich\b[^.?!\n]{0,60}?\bsang tieng\b|"
                              r"\b(?:lap trinh|viet)\b[^.?!\n]{0,40}?\b(?:ham|doan ma|python|javascript|sql)\b")
KPI_WORDS_PLAIN = re.compile(r"kpi|thuong|luong|chi tieu|doanh so|moc|don hang|cua hang|diem ban|nhan vien|\bnv\d|\bdoi\b|tuyen|mcp|cong van|"
                             r"turnover|pc_bt|pc_on|lppc|aso")
# Mã cho luật (4): không có chữ cái ngay trước, không có chữ số ngay sau
CODE_TWO_DIGITS = re.compile(r"(?<![A-Za-z])NV(\d{2})(?!\d)", re.IGNORECASE)


def check_question(question: str, team_codes: set[str]) -> str | None:
    """None = cho qua; chuỗi = lý do từ chối. Lý do không chép chuỗi lạ người gõ — chỉ nêu lại mã dạng NVxx ở luật (4).

    `team_codes`: mã che có trong đội (lấy từ GET /api/meta của backend) — so từng mã, không giả định NV01…NVn liền nhau.
    
    """
    # NFKC: gạch chéo toàn khổ "／" ⇒ "/", chấm lửng "…" ⇒ "..." — để cách gõ khác không lách được luật (1)
    text = unicodedata.normalize("NFKC", question)
    if PATH_DOTS.search(text):
        return "có dạng đường dẫn `..` cạnh dấu gạch chéo"
    if BACKSLASH_ON_CODE.search(text):
        return "có dấu `\\` dính mã nhân viên"
    if _encoded_near_codes(text):
        return "có ký tự mã hoá URL dính mã nhân viên"
    for match in CODE_TWO_DIGITS.finditer(text):
        if f"NV{match.group(1)}" not in team_codes:
            return f"có mã NV{match.group(1)} không thuộc đội"
    return None


def is_other_task(question: str) -> bool:
    """True = yêu cầu làm việc khác, không dính gì tới KPI ⇒ bên gọi trả `OUT_OF_SCOPE_ANSWER`, không gọi mô hình."""
    text = unicodedata.normalize("NFKC", question)
    if (OTHER_TASK.search(text) or OTHER_TASK_MORE.search(text)) and KPI_WORDS.search(text) is None:
        return True
    plain = _without_marks(text)
    return plain == text.lower() and OTHER_TASK_PLAIN.search(plain) is not None and KPI_WORDS_PLAIN.search(plain) is None


def _without_marks(text: str) -> str:
    """Chữ thường đã bỏ dấu tiếng Việt ("đ" không tách được bằng NFKD ⇒ đổi riêng)."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).replace("đ", "d")


def _encoded_near_codes(text: str) -> bool:
    """Luật (3): mã hoá URL dính liền một mã, hoặc nằm giữa mã đầu và mã cuối của câu."""
    codes = list(CODE_ANYWHERE.finditer(text))
    if not codes:
        return False
    first_start, last_end = codes[0].start(), codes[-1].end()
    for enc in URL_ENCODED.finditer(text):
        touches = any(enc.end() == code.start() or enc.start() == code.end() for code in codes)
        between = len(codes) >= 2 and first_start < enc.start() < last_end
        if touches or between:
            return True
    return False
