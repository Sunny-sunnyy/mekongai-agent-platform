"""Kỳ nêu BẰNG SỐ trong câu trả lời phải là kỳ của dữ liệu (kiem_so@31).

Vì sao: công cụ KPI chỉ trả số của kỳ đang nạp (vd 2026-10). Câu "Tháng 9, NV03 có 5.040.000 đ" mang số ĐÚNG, đúng người ⇒ mọi lớp kiểm số cho qua,
nhưng người đọc hiểu đó là tiền của tháng khác.

Cách làm (đề): đọc câu trả lời thành danh sách CHỖ NÊU KỲ; chỗ nào lệch kỳ dữ liệu
thì MẶC ĐỊNH là lỗi; mỗi ngoại lệ là một câu hỏi về CHÍNH chỗ đó. Không có cờ nào bật một lần rồi tha cho cả câu trả lời — hai bản trước hỏng ở
đúng điểm này (lời "bạn hỏi tháng 9" tha luôn câu trình bày số sau nó; lời "chưa có dữ liệu tháng 8" bật cờ "đã nói kỳ thật").

Ngoại lệ:
(a) không phải lời nêu kỳ — ngày đủ, số văn bản, tần suất ("mỗi tháng 1 lần"), tiền sau chữ "tháng", quý: lọc ngay lúc đọc;
(b) chỗ đó là TÂN NGỮ của lời "chưa / không có dữ liệu" và sau nó (trước chỗ nêu đúng kỳ kế tiếp) không có con số cỡ tiền; chỗ khác nêu cùng kỳ X cũng được tha, TRỪ khi nó đang đứng làm nhãn cho con số;
(c) chỗ đó là tân ngữ của lời nhắc câu hỏi ("bạn hỏi tháng 9") VÀ câu trả lời có nêu đúng kỳ dữ liệu bằng số (tính trên CẢ câu trả lời — câu đúng
    thường viết thành hai câu) hoặc có lời (b) cho chính kỳ X;
(c') lời bác bỏ "không phải tháng 9" khi câu trả lời có nêu đúng kỳ dữ liệu;
(d) tháng KẾ của kỳ dữ liệu trong câu nói về hạn hoá đơn hoặc về tương lai, khi câu đó không mang con số / mã nhân viên nào (ngày giờ của hạn
    không tính) và sau nó không có số nào đứng trước chỗ nêu đúng kỳ kế tiếp (tức nó không làm nhãn cho số ở câu sau).
"Không phải <kỳ của dữ liệu>" ⇒ lỗi. Quý không bị đọc thành tháng và KHÔNG tính là "đã nêu đúng kỳ".

Giới hạn đã chấp nhận (không bắt): tháng viết bằng chữ ("tháng chín"), kỳ tương đối ("tháng trước"), "T9" không kèm năm (trùng thứ trong tuần),
ngày đủ viết sai ("tính tới 20/9/2026"), dạng "09-2026" / "2026/09" (lớp số-không-nguồn đang chặn), QUÝ sai nêu bằng số ("Quý 3/2026" khi dữ
liệu là tháng 10 — đề chỉ kể tháng · tháng/năm · mã kỳ · năm), số thực hiện NHỎ (dưới 4 chữ số) đặt ngay sau lời "chưa có dữ liệu tháng X". Đường chat trên máy chưa gọi lớp này."""
import re

YEAR = r"(?:19|20)\d{2}"
DAY_WORD = r"(?:ngày|mùng|mồng)"
# Thứ tự = thứ tự ưu tiên: mẫu trước chiếm chữ trước, mẫu sau không đọc lại phần đã chiếm (ngày đủ chiếm "tháng 11" của "ngày 02 tháng 11").
FULL_DATE = re.compile(
    rf"\b{YEAR}-\d{{1,2}}-\d{{1,2}}\b|(?<![\d/-])\d{{1,2}}\s*[/-]\s*\d{{1,2}}\s*[/-]\s*{YEAR}(?!\d)"
    rf"|\b{DAY_WORD}\s+\d{{1,2}}\s*(?:/\s*\d{{1,2}}(?!\s*/?\s*\d)|\s+(?:của\s+)?tháng\s+\d{{1,2}}(?:\s*(?:/|-|\s+năm\s+)\s*{YEAR})?)", re.IGNORECASE)
TIME_OF_DAY = re.compile(r"\b\d{1,2}\s*(?:h|giờ)(?:\s*\d{2})?\b|\b\d{1,2}:\d{2}\b", re.IGNORECASE)   # "22h", "22:00" — giờ của hạn, không phải số liệu
QUARTER = re.compile(rf"\bquý\s*(?:IV|I{{1,3}}|[1-4])\b(?:\s*(?:/|-|\s+năm\s+)\s*{YEAR})?", re.IGNORECASE)
DOCUMENT = re.compile(rf"(?:\bcông\s+văn|\bCV|\bQĐ|\bquyết\s+định|\bvăn\s+bản|\bthông\s+báo|\btờ\s+trình)\s*(?:số\s*)?[:.]?\s*\d{{1,4}}\s*/\s*{YEAR}", re.IGNORECASE)
FREQUENCY = re.compile(r"\b(?:mỗi|hằng|hàng|một|vài|nửa|mấy|từng|\d+)\s+tháng\s+\d{1,2}\s+(?:lần|ngày|tuần|đợt|buổi|đơn|cuộc|kỳ)\b", re.IGNORECASE)
PERIOD_CODE = re.compile(rf"(?<![\d-])({YEAR})-(0[1-9]|1[0-2])(?![\d-])")
# "tháng 9", "tháng 09/2026", "tháng 10 năm 2026", "T10/2026". Số có nhóm nghìn / số lẻ sau chữ tháng ("lương tháng 5.040.000 đ") là tiền.
MONTH_WORD = re.compile(rf"(?:\btháng\s*|\bT(?=\d{{1,2}}\s*[/-]\s*{YEAR}))(\d{{1,2}})(?![.,]?\d)(?:\s*(?:/|-|\s+năm\s+)\s*({YEAR})(?!\d))?", re.IGNORECASE)
MONTH_SLASH_YEAR = re.compile(rf"(?<![\d/.,-])(\d{{1,2}})\s*/\s*({YEAR})(?![\d/])")
YEAR_WORD = re.compile(rf"\bnăm\s+({YEAR})\b", re.IGNORECASE)

SENTENCE_END = re.compile(r"[!?;\n]|\.(?=\s|$)")
EMPLOYEE = re.compile(r"\bNV\d+\b", re.IGNORECASE)
# Chữ được đứng giữa động từ và kỳ chỉ là chữ nói về VIỆC CÓ DỮ LIỆU / việc hỏi — không chữ nào tả kết quả ("đạt", "mốc", tên KPI) ⇒
# "NV03 không đạt mốc PC_BT tháng 9" không phải lời phủ nhận dữ liệu.
DATA_NOUN = r"(?:dữ\s+liệu|số\s+liệu|luật|file)"
LOAD_VERB = r"(?:nạp|tải\s+lên|tải|cập\s+nhật)"
DENIED_BEFORE = re.compile(
    rf"\b(?:chưa|không)\s+(?:hề\s+)?(?:(?:(?:có|thấy|tìm\s+thấy|được\s+nạp|nạp|ghi\s+nhận)\s+)?(?:bất\s+kỳ\s+)?{DATA_NOUN}(?:\s+nào)?"
    rf"(?:\s+(?:của|cho|về|ở|trong|thuộc))?|(?:có|(?:được\s+)?{LOAD_VERB})"
    # "không trả lời được cho tháng 9", "chưa tính được tháng 9", "không thể cung cấp số liệu tháng 9" — nói KHÔNG làm được cho kỳ đó
    rf"|(?:thể\s+)?(?:trả\s+lời|tính|xem|tra|cung\s+cấp|báo|lấy)(?:\s+được)?(?:\s+(?:{DATA_NOUN}|số|kết\s+quả))?(?:\s+(?:cho|về|của))?)"
    rf"(?:\s+kỳ)?\s*$", re.IGNORECASE)
ASKER = r"(?:bạn|anh|chị|em|quý\s+khách|người\s+dùng)"
ASK_VERB = r"(?:hỏi|nhắc|yêu\s+cầu|đề\s+cập|xin|muốn|cần|quan\s+tâm)"
ASKED_AFTER = re.compile(rf"^\s*(?:mà\s+)?{ASKER}\s+(?:(?:vừa|đang|đã)\s+)?{ASK_VERB}(?:\s+(?:xem|tới|đến|về))?", re.IGNORECASE)
DENIED_AFTER = re.compile(
    rf"^\s*,?\s*(?:thì\s+)?(?:(?:tôi|mình|em|hệ\s+thống|hiện\s+tại|hiện)\s+){{0,3}}(?:cũng\s+)?(?:chưa|không)\s+"
    rf"(?:(?:có|thấy|ghi\s+nhận)\s+{DATA_NOUN}|(?:được\s+)?{LOAD_VERB}\b|(?:nằm|có)\s+trong\s+(?:{DATA_NOUN}|hệ\s+thống)|có\s*(?:[,.]|$))", re.IGNORECASE)
DENIED_TOO = re.compile(r"^\s*(?:thì\s+)?cũng\s+(?:chưa|không)\s*(?:có\s*)?(?:[,.]|$)", re.IGNORECASE)   # "…, tháng 8 cũng chưa."
LIST_JOIN = re.compile(r"^\s*(?:,|và|hoặc|lẫn|hay|cũng\s+như)\s*(?:kỳ\s+)?$", re.IGNORECASE)             # "chưa có dữ liệu tháng 8 và tháng 9"
ASKED_BEFORE = re.compile(
    rf"\b{ASK_VERB}(?:\s+(?:về|đến|tới|xem|biết|tra|số\s+liệu|dữ\s+liệu|số|kết\s+quả|thông\s+tin|của|cho|kỳ|là|ở|trong))*\s*$", re.IGNORECASE)
REFUTED_BEFORE = re.compile(r"\bkhông\s+phải(?:\s+(?:là|số|của|kỳ|dữ\s+liệu|số\s+liệu))*\s*$", re.IGNORECASE)
NEGATION = re.compile(r"\b(?:chưa|không)\b", re.IGNORECASE)
DEADLINE = re.compile(r"\bhạn\b|\bho[áa]\s+đơn\b|\bhóa\s+đơn\b", re.IGNORECASE)
NUMBER = re.compile(r"\d[\d.,]*\d|\d")
FUTURE = re.compile(r"\b(?:sẽ|có\s+thể|khi|dự\s+kiến)\b", re.IGNORECASE)


# Khối chép lại THAM SỐ bên gọi gửi — kỳ ở đó là kỳ người hỏi xin, không phải kỳ của số. Lượt thật 20/11: 118 / 1.974 kết quả có `params.period`.
CALLER_PARAMS = ("params_used", "params")


def data_periods(tool_results: list) -> set[tuple[int, int]]:
    """Mọi kỳ (năm, tháng) ghi ở ô tên ĐÚNG là `period` của kết quả công cụ DÙNG ĐƯỢC, ở mọi tầng (công cụ so hai kỳ ghi cả hai). Không đọc khối
    tham số bên gọi gửi (`CALLER_PARAMS`), không đọc khoá khác có chữ period (`period_note`…), không đọc lời từ chối / lỗi."""
    found: set[tuple[int, int]] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            period = node.get("period")
            if isinstance(period, str) and (match := PERIOD_CODE.fullmatch(period.strip())):
                found.add((int(match.group(1)), int(match.group(2))))
            for key, value in node.items():
                if key not in CALLER_PARAMS:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tool_results)
    return found


def _mentions(answer: str) -> tuple[list[dict], list[tuple[int, int]]]:
    """(các chỗ nêu kỳ theo thứ tự xuất hiện, mọi khoảng chữ đã chiếm — gồm cả ngày đủ / quý / số văn bản / tần suất).
    Mỗi chỗ: {year | None, month | None, start, end, text}."""
    taken: list[tuple[int, int]] = []
    mentions = []

    def free(match) -> bool:
        return all(match.end() <= start or match.start() >= end for start, end in taken)

    for pattern in (FULL_DATE, TIME_OF_DAY, QUARTER, DOCUMENT, FREQUENCY):   # ngoại lệ (a): chiếm chữ, không thành chỗ nêu kỳ
        taken += [match.span() for match in pattern.finditer(answer) if free(match)]
    for pattern, year_group, month_group in ((PERIOD_CODE, 1, 2), (MONTH_WORD, 2, 1), (MONTH_SLASH_YEAR, 2, 1), (YEAR_WORD, 1, None)):
        for match in pattern.finditer(answer):
            if not free(match):
                continue
            month = int(match.group(month_group)) if month_group else None
            if month is not None and not 1 <= month <= 12:
                continue                                                  # "26/2026", "tháng 13": không phải tháng
            year = match.group(year_group)
            taken.append(match.span())
            mentions.append({"year": int(year) if year else None, "month": month, "start": match.start(), "end": match.end(), "text": match.group()})
    return sorted(mentions, key=lambda mention: mention["start"]), taken


def _sentence_span(answer: str, start: int, end: int) -> tuple[int, int]:
    left = max((match.end() for match in SENTENCE_END.finditer(answer, 0, start)), default=0)
    right = next((match.start() for match in SENTENCE_END.finditer(answer, end)), len(answer))
    return left, right


def _same_period(a: dict, b: dict) -> bool:
    """Hai chỗ nêu CÙNG một kỳ: cùng tháng (hoặc cả hai chỉ nêu năm), năm không trái nhau ("tháng 9" và "tháng 09/2026" là một)."""
    return a["month"] == b["month"] and (a["year"] is None or b["year"] is None or a["year"] == b["year"])


def _matches_data(mention: dict, periods: set[tuple[int, int]]) -> bool:
    if mention["month"] is None:
        return any(mention["year"] == year for year, _month in periods)
    return any(mention["month"] == month and mention["year"] in (None, year) for year, month in periods)


def _is_next_month(mention: dict, periods: set[tuple[int, int]]) -> bool:
    following = {(year + month // 12, month % 12 + 1) for year, month in periods}
    return mention["month"] is not None and any(mention["month"] == month and mention["year"] in (None, year) for year, month in following)


def _has_figures(answer: str, left: int, right: int, taken: list[tuple[int, int]]) -> bool:
    """Trong [left, right) có mã nhân viên hoặc chữ số nằm NGOÀI các chỗ nêu kỳ / ngày / quý — tức câu đang trình bày số."""
    if EMPLOYEE.search(answer, left, right):
        return True
    return any(char.isdigit() and not any(start <= index < end for start, end in taken) for index, char in enumerate(answer[left:right], left))


def _money_follows(answer: str, mention: dict, mentions: list[dict], taken: list[tuple[int, int]]) -> bool:
    """Sau chỗ nêu này, trong cùng câu và TRƯỚC chỗ nêu đúng kỳ dữ liệu kế tiếp, có con số cỡ tiền (từ 4 chữ số) — "Tháng 9 chưa có dữ liệu chính
    thức nhưng NV11 có 5.040.000 đ" là trình bày số dưới nhãn tháng 9, không còn là lời phủ nhận. Số nhỏ ("14 nhân viên", "8 KPI") không tính:
    câu phủ nhận thật hay kèm số đếm; cái giá là số thực hiện nhỏ đặt sau lời phủ nhận còn lọt."""
    left, right = mention["sentence"]
    stop = min((other["start"] for other in mentions if mention["end"] <= other["start"] < right and other["ok"] and other["month"] is not None),
               default=right)
    return any(sum(char.isdigit() for char in match.group()) >= 4 and not any(start <= match.start() < end for start, end in taken)
               for match in NUMBER.finditer(answer, mention["end"], stop))


def _leads_figures(answer: str, mention: dict, mentions: list[dict], taken: list[tuple[int, int]]) -> bool:
    """Chỗ nêu này là chỗ nêu kỳ GẦN NHẤT đứng trước một câu trình bày số: sau câu chứa nó có con số / mã nhân viên mà ở giữa không có chỗ nêu
    ĐÚNG kỳ dữ liệu nào. Khi đó người đọc gắn các số ấy với kỳ này, bất kể câu dẫn dài hay ngắn, kết bằng dấu gì ( vòng 1: câu dẫn dài
    kết bằng dấu chấm đã lọt qua phép nhận dòng dẫn theo hình dạng).
    """
    after = mention["sentence"][1]
    stop = min((other["start"] for other in mentions if other["start"] >= after and other["ok"] and other["month"] is not None), default=len(answer))
    return _has_figures(answer, after, stop, taken)


def _is_lead_in(sentence: str, mention_text: str) -> bool:
    """Câu này là TIÊU ĐỀ / dòng dẫn cho phần sau (số nằm ở câu / dòng dưới) chứ không phải một lời nói trọn ý: kết bằng dấu hai chấm, mở đầu bằng
    dấu tiêu đề / ô bảng, hoặc ngoài chỗ nêu kỳ chỉ còn ≤ 3 chữ ("Sang tháng 11 dự kiến"). Ngoại lệ (d) không bao giờ áp cho dạng này — nếu áp thì
    "Tháng 11 (dự kiến):" + số ở dòng dưới sẽ lọt, đúng kiểu lỗi lời tha tràn sang câu sau."""
    text = sentence.strip()
    return text.endswith(":") or text.startswith(("#", "|", "*")) or len(text.replace(mention_text, " ").split()) <= 3


def period_problems(answer: str, tool_results: list) -> list[str]:
    """Danh sách lời lỗi (rỗng = đạt). Không có kết quả công cụ dùng được ⇒ không xét (lớp số-không-nguồn lo câu bịa số khi mọi công cụ từ chối).
    Có kết quả dùng được mà không ô `period` nào ⇒ mọi chỗ nêu kỳ đều lỗi (đóng khi lỗi; 1.943 bản ghi thật đo 20/11: 0 trường hợp)."""
    usable = [result for result in tool_results if isinstance(result, dict) and "refused" not in result and "error" not in result]
    if not usable:
        return []
    periods = data_periods(usable)
    mentions, taken = _mentions(answer)
    for index, mention in enumerate(mentions):
        left, right = _sentence_span(answer, mention["start"], mention["end"])
        before, after = answer[left:mention["start"]], answer[mention["end"]:right]
        asked_after = ASKED_AFTER.match(after)
        rest = after[asked_after.end():] if asked_after else after         # "tháng 9 bạn hỏi thì chưa có dữ liệu"
        mention.update(
            sentence=(left, right), ok=_matches_data(mention, periods),
            asked=bool(ASKED_BEFORE.search(before) or asked_after),
            refuted=bool(REFUTED_BEFORE.search(before)),
            denied=bool(DENIED_BEFORE.search(before) or DENIED_AFTER.match(rest) or DENIED_TOO.match(rest)),
            negated_before=bool(NEGATION.search(before)),
            # đang làm NHÃN cho con số: sau nó trong câu có số / mã nhân viên, hoặc câu kết bằng dấu hai chấm (số nằm ở các dòng dưới)
            labels_figures=_has_figures(answer, mention["end"], right, taken) or answer[left:right].rstrip().endswith(":"))
    # Danh sách kỳ dính liền nhau chung một lời phủ nhận: "chưa có dữ liệu tháng 8 và tháng 9" (lan xuôi), "tháng 8, tháng 9 chưa có dữ liệu" (lan ngược)
    pairs = list(zip(mentions, mentions[1:]))
    for first, second in pairs:
        if first["denied"] and LIST_JOIN.match(answer[first["end"]:second["start"]]):
            second["denied"] = True
    for first, second in reversed(pairs):
        if second["denied"] and LIST_JOIN.match(answer[first["end"]:second["start"]]):
            first["denied"] = True
    truth_stated = any(mention["ok"] and mention["month"] is not None and not mention["refuted"] for mention in mentions)
    shown = ", ".join(f"{year}-{month:02d}" for year, month in sorted(periods)) or "không rõ"
    problems = []
    for mention in mentions:
        if mention["ok"]:
            if mention["refuted"]:
                problems.append(f"câu trả lời nói không phải '{mention['text']}' trong khi dữ liệu chính là kỳ {shown}")
            continue
        same_denied = any(other["denied"] and _same_period(other, mention) for other in mentions)
        left, right = mention["sentence"]
        # Cùng CÂU đã có một chỗ nêu kỳ X được tha theo (b) / (c) ("Nếu cần tháng 9, bạn tải file tháng 9 lên") ⇒ chỗ nhắc lại X trong câu đó
        # đi theo, miễn sau nó không có tiền
        echoed = any(other is not mention and other["sentence"] == mention["sentence"] and _same_period(other, mention)
                     and ((other["denied"] and not _money_follows(answer, other, mentions, taken)) or (other["asked"] and (truth_stated or same_denied)))
                     for other in mentions) and not _money_follows(answer, mention, mentions, taken)
        sentence = answer[left:right]
        excused = (
            (mention["denied"] and not _money_follows(answer, mention, mentions, taken))                        # (b) chính chỗ này bị phủ nhận
            or (same_denied and not mention["denied"] and not mention["asked"] and (mention["negated_before"] or not mention["labels_figures"]))   # (b) chỗ khác cùng kỳ X
            or (mention["asked"] and (truth_stated or same_denied))                                             # (c)
            or echoed
            or (mention["refuted"] and truth_stated)                                                            # (c')
            # (d) — cả hai cửa: câu KHÔNG mang con số / mã nhân viên nào (ngày, giờ của hạn đã được chiếm chữ nên không tính), không phải dòng
            # dẫn theo hình dạng, và không đứng làm kỳ gần nhất trước một câu trình bày số
            or (_is_next_month(mention, periods) and (DEADLINE.search(sentence) or FUTURE.search(sentence))
                and not _has_figures(answer, left, right, taken) and not _is_lead_in(sentence, mention["text"])
                and not _leads_figures(answer, mention, mentions, taken)))
        if not excused:
            problems.append(f"câu trả lời nêu kỳ '{mention['text']}' nhưng dữ liệu công cụ trả là kỳ {shown}")
    return problems
