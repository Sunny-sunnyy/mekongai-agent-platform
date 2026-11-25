"""Kiểm bằng code: mọi con số trong câu trả lời của agent phải có nguồn — trong kết quả công cụ của câu đó hoặc trong chính câu hỏi.

Vì sao (kế hoạch G4 mục 2; học Capitol chủ đề 3–4): "chỉ nói số có bằng chứng" của Capitol chỉ là lời dặn; ở đây tiền người thật
nên chặn bằng code. Cách so:
- Số trong câu trả lời được đọc theo MỌI cách viết hợp lý (5.040.000 / 5,040,000 = 5040000; "54,15" = 54.15; "2.200" có thể là 2200
  hoặc 2,2) kèm hệ số "triệu" / "tr" / "nghìn" / "k" / "tỷ" — chỉ cần một cách đọc khớp nguồn.
Khớp nguồn = BẰNG ĐÚNG giá trị một số nguồn (trước 04/11: "làm tròn tới số lẻ đã viết", 54,2 % từng khớp 54.15).
  Không đổi tỷ lệ ⇔ %: công cụ đã trả % sẵn (bản đầu nhân mọi số ×100 ⇒ "574 triệu" lọt — G4a).
- Không coi là "con số": mã NV (NV05), số thứ tự đầu dòng danh sách ("1." "2)"), số đi sau chữ "ngày / tháng / năm".
  Ngày dạng dd/mm(/yyyy) vẫn được kiểm từng phần (các phần thường có trong nguồn).
- TIỀN (số có hệ số triệu / tr / nghìn / ngàn / k / tỷ, hoặc có "đ / đồng / VNĐ" ngay sau) phải lệch DƯỚI 1 ĐỒNG so với một số nguồn sau khi nhân hệ số —
  không làm tròn, kể cả có chữ "khoảng" (tiền đúng tới đồng). "5,74 triệu" = 5.740.000 ⇒ đạt (đúng giá trị, khác cách viết
  cho qua); "5,7 triệu", "6 triệu" ⇒ trượt. Doanh số (đồng) cũng là tiền.
%, tỷ lệ KPI (LPPC / LPPC_Meat), số đếm: BẰNG ĐÚNG một số nguồn — không làm tròn chiều nào (ĐỔI QUYẾT ĐỊNH: công cụ
  89,74 ⇒ "89,74 %" đạt, "90 %" / "89,7 %" sai; LPPC 3,82 ⇒ "3,8" sai). Tiền giữ "lệch dưới 1 đồng" ở trên.
  Kế hoạch (sha 7fdc2c0b27b5).
Giới hạn: số nhỏ như 0, 1 gần như luôn có nguồn; cách viết "5tr74", "5 triệu 740 nghìn" và số âm viết bằng chữ ("giảm 200.000 đ" khi nguồn
là -200000) bị bắt nhầm (GĐ 1 04/11 — có từ trước, không sửa ở bậc này);
kiểm "số có xuất hiện trong nguồn", KHÔNG kiểm số đó gắn đúng NV / đúng KPI (vd nói tiền NV05 bằng số của NV06 vẫn lọt) —
phần đó đo bằng bộ câu có đáp án (G4c)."""
import json
import re
from decimal import Decimal, InvalidOperation

MULTIPLIERS = {"tỷ": Decimal(10) ** 9, "ty": Decimal(10) ** 9, "triệu": Decimal(10) ** 6, "tr": Decimal(10) ** 6,
               "nghìn": Decimal(1000), "ngàn": Decimal(1000), "k": Decimal(1000)}
# Nhóm nghìn bằng dấu cách ("5 740 000", G4a) đứng trước cách viết thường để không bị cắt thành 3 số
NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:[  ]\d{3})+(?![\d.,])|\d+(?:[.,]\d+)*)(\s*(?:tỷ|ty|triệu|tr|nghìn|ngàn|k)\b)?",
                    re.IGNORECASE)
# Mã bản luật "<tên file>@<8 ký tự hex>" (tính năng 4) không phải con số — bỏ ở câu trả lời VÀ ở chữ nguồn (;
# 4b: "0" của "0ef1182e" từng làm "0 đ" lọt; ngược lại "2026", "0" của mã bị bắt nhầm khi mô hình chép mã ra)
RULE_VERSION_TOKEN = re.compile(r"[\w.-]+@[0-9a-f]{8}\b", re.IGNORECASE)
SKIP_PATTERNS = [RULE_VERSION_TOKEN,
                 re.compile(r"\bNV\d+\b", re.IGNORECASE),            # mã NV che
                 re.compile(r"(?m)^\s*\d+[.)]\s"),                     # số thứ tự đầu dòng
                 # "ngày 02 tháng 11", "năm 2026": chữ chỉ ngày / tháng / năm, không phải tiền / số đếm (G4a)
                 re.compile(r"\b(?:ngày|tháng|năm)\s+\d{1,4}\b", re.IGNORECASE)]
# Tên khoá kết quả công cụ dạng "..._<số>_pct" (vd business_salary_at_100_pct) mang một mức % — số đó là nguồn CHỈ cho số viết kèm "%"
# (03/11: demo câu 1 "mức 100%" bị bắt nhầm ⇒ làm lại 28 s + "Xin lỗi…"). Giữ hẹp: không nhận mọi chữ số trong tên khoá.
PERCENT_KEY = re.compile(r"_(\d+)_pct(?=_|$)")
PERCENT_AFTER = re.compile(r"\s?%")
# "đ" / "đồng" / "VNĐ" ngay sau số ⇒ số đó là tiền. \b sau "đ" để "54 đạt" không bị coi là tiền ("đạ…" còn chữ cái nối tiếp)
MONEY_AFTER = re.compile(r"\s?(?:đồng|đ|vnđ|vnd)\b", re.IGNORECASE)
# Vòng 2 (04/11 mục 7, ý 1): số trong CÂU HỎI đứng sau "khoảng / gần / tầm…" là số người hỏi làm tròn — không phải nguồn
# (kiểm cuối K 04/11 lượt 2: "khoảng 5 triệu đúng không?" ⇒ "Đúng, 5.240.000" lọt); trả lời mở đầu "đúng / phải…" mà số đó không
# khớp đúng công cụ ⇒ bắt. Không có "độ" (dễ trùng "mức độ 90 %").
ROUNDED_BEFORE = re.compile(r"\b(?:khoảng|gần|tầm|xấp xỉ|cỡ|chừng|ước)\s*$", re.IGNORECASE)
AFFIRM_START = re.compile(r"^\W*(?:đúng|phải|chính xác|vâng|dạ|ừ|chuẩn)\b", re.IGNORECASE)
# "Dạ, không phải —" / "Vâng, chưa đúng" là lễ phép rồi phủ định — không phải đồng ý (V2, v1)
NEGATION_AFTER_AFFIRM = re.compile(r"^\W*\w+[\s,]*(?:không|chưa|sai)\b", re.IGNORECASE)


def readings(text: str) -> list[tuple[Decimal, int]]:
    """Các cách đọc một chuỗi số ⇒ [(giá trị, số chữ số lẻ đã viết)]."""
    options = []
    text = re.sub(r"[  ]", "", text)   # "5 740 000" ⇒ "5740000"
    if re.fullmatch(r"\d+", text):
        return [(Decimal(text), 0)]
    for decimal_sep, group_sep in ((",", "."), (".", ",")):
        head, _, tail = text.rpartition(decimal_sep) if decimal_sep in text else (text, "", "")
        # Cách 1: dấu cuối là dấu thập phân, các dấu kia là phân nhóm 3 chữ số
        if decimal_sep in text and decimal_sep not in head and group_sep not in tail:
            groups = head.split(group_sep)
            if all(len(g) == 3 for g in groups[1:]):
                options.append((Decimal("".join(groups) + "." + tail), len(tail)))
        # Cách 2: mọi dấu là phân nhóm 3 chữ số (không có phần lẻ)
        groups = re.split(r"[.,]", text)
        if all(len(g) == 3 for g in groups[1:]) and group_sep in text and decimal_sep not in text:
            options.append((Decimal("".join(groups)), 0))
    return options


def _values_with_unit(match) -> set[Decimal]:
    """Các giá trị của một số trong CHỮ nguồn — cả cách đọc trần lẫn đã nhân hệ số ("5 triệu" ⇒ 5 và 5.000.000). Vòng 2 ý 2: câu hỏi
    "từ 5 triệu trở lên" ⇒ lời đáp "5.000.000 đồng" từng bị bắt nhầm (K 04/11) vì nguồn chỉ có 5."""
    unit = (match.group(2) or "").strip().lower()
    values = {number for number, _ in readings(match.group(1))}
    return values | ({value * MULTIPLIERS[unit] for value in values} if unit in MULTIPLIERS else set())


def question_numbers(question: str) -> tuple[set[Decimal], list[tuple[str, set[Decimal]]]]:
    """(số trong câu hỏi dùng làm nguồn, [(chữ gốc, giá trị) của số người hỏi làm tròn — "khoảng 5 triệu"]) — số làm tròn KHÔNG là nguồn."""
    sources, rounded = set(), []
    for match in NUMBER.finditer(RULE_VERSION_TOKEN.sub(" ", question or "")):
        if ROUNDED_BEFORE.search(question[:match.start()]):
            rounded.append((match.group(0).strip(), _values_with_unit(match)))
        else:
            sources |= _values_with_unit(match)
    return sources, rounded


def source_numbers(sources: list) -> set[Decimal]:
    """Mọi số trong kết quả công cụ (số trong JSON lẫn số nằm trong chữ, vd lý do '2,000,000 đ') + câu hỏi."""
    found = set()

    def walk(value):
        if isinstance(value, bool) or value is None:
            return
        # Không nhân ×100: công cụ đã trả % sẵn (pct = 88.89). Nhân mọi số ×100 làm "574 triệu" khớp 5.740.000 (major, G4a)
        if isinstance(value, (int, float)):
            found.add(Decimal(str(value)))
        elif isinstance(value, str):
            for match in NUMBER.finditer(RULE_VERSION_TOKEN.sub(" ", value)):
                found.update(_values_with_unit(match))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    for source in sources:
        walk(json.loads(json.dumps(source, ensure_ascii=False, default=str)))
    return found


def percent_key_numbers(sources: list) -> set[Decimal]:
    """Các mức % nằm trong TÊN khoá dạng `_<số>_pct` của kết quả công cụ (vd `business_salary_at_100_pct` ⇒ 100)."""
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                found.update(Decimal(number) for number in PERCENT_KEY.findall(str(key)))
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    for source in sources:
        walk(json.loads(json.dumps(source, ensure_ascii=False, default=str)))
    return found


def _matches(value: Decimal, multiplier: Decimal, sources: set[Decimal], is_money: bool = False) -> bool:
    if is_money:
        # Tiền: đúng tới đồng = lệch DƯỚI 1 đồng — nguồn số nguyên (mọi ô tiền thưởng) ⇒ phải bằng đúng;
        # nguồn có phần lẻ (doanh số còn thiếu 384.601.087,9 — kết quả thật get_mid_month_gap) ⇒ "384.601.088 đ" đạt. "6 triệu" cho 5.740.000 trượt
        return any(abs(value * multiplier - s) < 1 for s in sources)
    # Mọi số khác (%, tỷ lệ KPI, số đếm) BẰNG ĐÚNG số công cụ trả — ĐỔI QUYẾT ĐỊNH (% giữa 09:47 và 10:00, tỷ lệ + số đếm
    # lúc 10:10): "code trả 89,74 % thì agent trả đúng 89,74 %"; thay luật 04/11 "làm tròn tới số lẻ đã viết" và bản vòng 2
    # "% chỉ làm tròn xuống". "90 %" / "89,7 %" cho 89.74 trượt; "3,8" cho LPPC 3.82 trượt; "89,740" đạt (Decimal so giá trị, không so cách viết).
    return any(s / multiplier == value for s in sources)


def _answer_numbers(answer: str):
    """Mỗi con số trong câu trả lời (đã bỏ mã NV, số thứ tự, ngày / tháng / năm) ⇒ (chữ gốc, các cách đọc, hệ số đơn vị, có "%" ngay sau, là tiền)."""
    text = answer
    for pattern in SKIP_PATTERNS:
        text = pattern.sub(" ", text)
    for match in NUMBER.finditer(text):
        raw, unit = match.group(1), (match.group(2) or "").strip().lower()
        try:
            options = readings(raw)
        except InvalidOperation:
            options = []
        is_percent = not unit and PERCENT_AFTER.match(text, match.end()) is not None
        # Tiền = có hệ số đơn vị (triệu / nghìn / k / tỷ) hoặc "đ / đồng" ngay sau — % thì không bao giờ là tiền
        is_money = not is_percent and (bool(unit) or MONEY_AFTER.match(text, match.end()) is not None)
        yield match.group(0).strip(), options, MULTIPLIERS.get(unit, Decimal(1)), is_percent, is_money


def _sourced(options, multiplier: Decimal, is_percent: bool, sources: set[Decimal], percent_sources: set[Decimal],
             is_money: bool = False) -> bool:
    if any(_matches(value, multiplier, sources, is_money) for value, _ in options):
        return True
    return is_percent and any(_matches(value, multiplier, percent_sources) for value, _ in options)


def check_numbers(answer: str, tool_results: list, question: str = "", context: list | tuple = ()) -> dict:
    """{"ok": bool, "unsourced": [chuỗi số không tìm được nguồn]} — ok = không có số nào thiếu nguồn.

    `context`: các chuỗi CODE đã điền vào dòng ngữ cảnh của lời dặn (kỳ, ngày chốt, số người đội) — mô hình chép lại thì có nguồn,
    như câu có gọi công cụ (kết quả công cụ đã mang kỳ / ngày chốt)., 03/11.
    
    """
    tool_sources = source_numbers([*tool_results, *context])
    asked, rounded = question_numbers(question)
    sources = tool_sources | asked
    percent_sources = percent_key_numbers(tool_results)
    unsourced = [text for text, options, multiplier, is_percent, is_money in _answer_numbers(answer)
                 if not _sourced(options, multiplier, is_percent, sources, percent_sources, is_money)]
    # Vòng 2 ý 1: "đúng / phải…" mở đầu = đồng ý với số làm tròn của câu hỏi ⇒ số đó phải khớp ĐÚNG một số công cụ
    if AFFIRM_START.match(answer or "") and not NEGATION_AFTER_AFFIRM.match(answer):
        unsourced += [f"đồng ý '{raw}' của câu hỏi" for raw, values in rounded if not values & tool_sources]
    return {"ok": not unsourced, "unsourced": unsourced}


def trace_numbers(answer: str, named_results: list[tuple[str, dict]], question: str = "") -> list[dict]:
    """Bảng "số này lấy từ công cụ nào" cho trang (SPEC mục 5, 6): mỗi số ⇒ tên các công cụ có số đó; số chỉ có trong câu hỏi ⇒ "câu hỏi".

    Cùng cách so với `check_numbers` — chỉ nói số CÓ xuất hiện ở công cụ nào, không chứng minh số gắn đúng NV / KPI.
    """
    per_source = ([(name, source_numbers([result]), percent_key_numbers([result])) for name, result in named_results]
                  + [("câu hỏi", source_numbers([question]), set())])
    rows = []
    for text, options, multiplier, is_percent, is_money in _answer_numbers(answer):
        found = [name for name, numbers, percent_numbers in per_source
                 if _sourced(options, multiplier, is_percent, numbers, percent_numbers, is_money)]
        rows.append({"number": text, "sources": list(dict.fromkeys(found))})
    return rows
