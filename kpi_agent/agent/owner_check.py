"""Kiểm bằng code: số trong câu trả lời có thuộc ĐÚNG nhân viên không, và câu "tất cả / không ai / N người" có khớp bảng thưởng đội không.

Vì sao ( GĐ 1b — hướng (i) 04/11): `number_check` chỉ biết số "có xuất hiện đâu đó" trong kết quả công cụ ⇒ lọt
"NV05 được 4.100.000" khi đó là số của NV06, và "cả 14 người có tiền" khi bảng ghi 13 / 14 (kiểm cuối T5 04/11). Hai luật, đều tất định:
1. Số thuộc đúng NV: mỗi số trong chữ lấy mã NVxx GẦN NHẤT ĐỨNG TRƯỚC nó (cả câu trả lời, không chỉ cùng câu — câu trả lời hay là bảng
   "NV02 · 5.040.000 đồng" hoặc mã ở dòng tiêu đề; 04/11 14:21: theo cùng câu chỉ 39 / 118 số tiền có mã). Số đó phải có trong dữ liệu
   của chính NV đó trong kết quả công cụ (mỗi số gán cho `employee_id` gần nhất bao nó) hoặc ở cấp đội. Không có mã đứng trước ⇒ không xét
   (number_check vẫn xét số có nguồn). So số bằng đúng luật của number_check (tiền đúng tới đồng, mọi số khác bằng đúng số công cụ).
2. Câu đếm người (chỉ câu có chữ nói về tiền: "có tiền / được thưởng / bị 0 đ"): câu nhắc tên một KPI + "N người / bạn / nhân viên" ⇒ N phải bằng số người CÓ hoặc CHƯA CÓ tiền KPI đó, do code đếm từ
   `get_team_bonus_table` của chính lượt; câu nhắc KPI + "tất cả / cả đội / mọi người / không ai / chưa ai / không bạn nào" ⇒ chỉ đúng được khi
   cả đội cùng có hoặc cùng chưa có tiền KPI đó. Lượt chưa gọi bảng thưởng đội ⇒ cờ (không có gì để đối chiếu).
Giới hạn (ghi trước, trên bộ phát triển): lỗi CÙNG NV không bắt (mốc thay tiền đang được, tiền KPI này nói thành KPI kia); câu viết số
TRƯỚC mã ("4.100.000 là của NV06") lấy nhầm NV đứng trước — `kiem_so` (10/11) lấy các mã sau số khi CÂU đó không có mã trước số
(`_owners_of_number`); câu "tất cả" không nhắc KPI nào không xét;
% của NV lấy nhầm từ mức mốc của chính NV đó ("NV02 đạt 95 %" khi 95 là mốc, tầng A -28) không bắt — cùng loại lỗi cùng NV.
Hai chỗ hẹp (09/11): "NV… đạt X %" so ô `pct`; "LPPC / LPPC_Meat … là / đạt X" so ô `actual` của KPI tỷ lệ — chỉ khi vế câu trước số không có "mốc".
"ở N KPI": qua khi N = số KPI công cụ liệt kê riêng cho NV đó (đếm bằng code); khác thì xét như mọi số (vd ô `kpis_paid` — tầng A C A2-13).
Đảo luật (kiem_so@14, 11/11 chiều): số TIỀN có mã chủ phải là tiền của CHÍNH người đó (6 đường qua ở khối "ĐẢO LUẬT" trước `check_owners`)
— thay luật "chặn khi khớp tiền người khác"; đóng hai lỗ bản 13: câu "sẽ …" và số cấp đội gán cho một NV."""
import re
from decimal import Decimal

from kpi_agent.agent import number_check as nc
from kpi_agent.agent import names as agent_names

EMPLOYEE_CODE = re.compile(r"\bNV\d+\b", re.IGNORECASE)
SENTENCE_END = re.compile(r"[.!?;\n]")
# "N người" — hoặc "N/M người", "N trên M người": M là cả đội, N mới là số đếm (1b, m1: "13/14 người" từng bị đọc thành "14 người")
PEOPLE_COUNT = re.compile(r"(?<![\w.,/])(\d+)(?:\s*(?:/|trên)\s*(\d+))?\s*(?:người|bạn|nhân viên)\b", re.IGNORECASE)
# Chiều "chưa có tiền" ("8 người chưa có tiền", "3 bạn bị 0 đồng") — chỉ xét CỤM NGAY SAU số / từ chỉ cả đội, tới dấu phẩy ( 1b, m2:
# "13 người có tiền ASO_Focus_2, chỉ NV14 chưa có" từng bị đọc thành chiều "chưa có" vì xét cả câu)
UNPAID_WORDS = re.compile(r"\b(?:chưa|không|bị 0|0 đ)", re.IGNORECASE)
# kiem_so@11: + ":" — "Chưa có ai được tiền KPI Turnover: cột Turnover đang là 0 đồng cho cả đội" (câu ĐÚNG, lượt 04/11 g3d c204) từng bị đọc thành
# chiều "không ai bị 0 đ" vì cụm kéo qua ":" tới "0 đồng"
CLAUSE_END = re.compile(r"[,.!?;:\n]")
# Từ chỉ CẢ ĐỘI (khẳng định "ai cũng …") và KHÔNG AI (khẳng định "không người nào …"). "cả đội" một mình thường là danh từ
# ("tổng tiền của cả đội") — chỉ tính khi kèm "đều / cùng" (phát lại 140 câu thật 04/11: 3 cờ nhầm)
# kiem_so@12 (@11, major: "Không phải nhân viên nào cũng có tiền ASO_ALL." 14 / 14 LỌT — cụm ngoài danh sách, lần thứ 3 liền): viết theo KHUÔN
# như NONE_WORDS — (ai / người / bạn / nhân viên) + "nào cũng", hoặc "ai cũng / tất cả / mọi người / cả đội đều|cùng". Không có "toàn đội" ( 11/11:
# 2 / 1.798 chữ trả lời thật dùng "toàn đội" cho phạm vi tính, không phải câu tiền ⇒ sẽ chặn oan).
ALL_WORDS = re.compile(r"\b(?:(?:ai|người|bạn|nhân\s+viên)\s+nào\s+cũng|ai\s+cũng|tất\s+cả|cả\s+đội\s+(?:đều|cùng)|mọi\s+người)\b", re.IGNORECASE)
# kiem_so@10: + "không có ai / chẳng ai / chẳng có ai" (@9, T1: "PC_BT không có ai có tiền" khi 2 / 14 có ⇒ LỌT vì cụm không nằm trong danh sách)
# kiem_so@11 (@10, major 2: "chưa có ai", "không một ai", "chả ai", "không có nhân viên nào", "không người nào" LỌT):
# viết theo KHUÔN thay vì liệt kê từng cụm — (không / chưa / chẳng / chả) + (có / một, tuỳ ý) + (ai / người nào / bạn nào / nhân viên nào).
# "không phải ai cũng …" không khớp ("phải" không nằm trong phần tuỳ ý) — đó là câu phủ định CẢ ĐỘI, NOT_ALL_BEFORE xét.
NONE_WORDS = re.compile(r"\b(?:không|chưa|chẳng|chả)(?:\s+(?:có|một))*\s+(?:ai|người\s+nào|bạn\s+nào|nhân\s+viên\s+nào)\b", re.IGNORECASE)
# kiem_so@11 (@10, major 3: "Không phải ai cũng có tiền LPPC." khi 14 / 14 có ⇒ LỌT — đọc thành "ai cũng có"): phủ định ngay trước
# "ai cũng / tất cả / mọi người / cả đội đều" ⇒ câu nói ĐIỀU NGƯỢC LẠI ⇒ kiểm ngược (như "không thuộc nhóm chưa ai" của @9)
NOT_ALL_BEFORE = re.compile(r"\b(?:không|chưa|chẳng|chả)\s+(?:phải|hẳn|chắc)(?:\s+là)?\s*$", re.IGNORECASE)
# Có chữ phủ định khác trong cùng vế TRƯỚC "tất cả / ai cũng" mà không theo khuôn trên ⇒ không chắc nghĩa ⇒ chặn (hướng "nghi thì chặn", 11/11)
NEGATION_WORD = re.compile(r"\b(?:không|chưa|chẳng|chả)\b", re.IGNORECASE)
TEAM_SIZE_BEFORE = re.compile(r"\bcả\s*$", re.IGNORECASE)   # "cả 14 người" ⇒ số người của đội
ACHIEVED_BEFORE = re.compile(r"\bđạt\s*$", re.IGNORECASE)    # "NV13 đạt 99,5 %" — % đạt (không phải "mốc Đạt 100%")
TIER_WORD = re.compile(r"\bmốc\b", re.IGNORECASE)   # cùng vế câu có "mốc" ⇒ số là tên mốc, không phải % đạt
# KPI tỷ lệ (dòng / đơn) — số viết không "%"; như `backend/queries.py` RATIO_KPIS (agent không nhập backend)
RATIO_KPIS = ("LPPC", "LPPC_Meat")
RATIO_NAME = re.compile(r"\bLPPC(?:_Meat)?\b", re.IGNORECASE)
VALUE_BEFORE = re.compile(r"\b(?:là|đạt|thực hiện|ở mức|hiện)\s*$", re.IGNORECASE)   # "LPPC_Meat của NV04 là 3,8"
# "ở 3 KPI" = số KPI đếm từ danh sách công cụ (phát lại 140 câu thật 09/11: 3 cờ nhầm khi bỏ làm tròn — T3 câu 6)
KPI_COUNT_AFTER = re.compile(r"\s*KPI\b")
# Luật 2 chỉ xét câu nói về CÓ / CHƯA CÓ TIỀN — "7 người còn cơ hội lên mốc PC_BT" là câu khác (phát lại 140 câu thật: 2 cờ nhầm)
MONEY_WORDS = re.compile(r"(?:có tiền|được tiền|nhận tiền|có thưởng|được thưởng|bị 0|0 đ)", re.IGNORECASE)
# kiem_so@12 (@11: "LPPC không phải ai cũng có." / "PC_BT ai cũng có." LỌT vì thiếu chữ "tiền"): câu CẢ ĐỘI / KHÔNG AI có tên KPI + "có / được"
# (ngoài chính cụm khẳng định) ⇒ cũng xét, hướng "nghi thì chặn". Câu đếm "N người" vẫn cần MONEY_WORDS ("7 người còn cơ hội lên mốc PC_BT").
HAVE_WORDS = re.compile(r"\b(?:có|được)\b", re.IGNORECASE)
# Bậc (a) câu cả đội do code viết: câu có tên KPI + dấu hiệu NHÓM NGƯỜI + chữ về tiền / có / đạt mà không trùng câu chuẩn và luật 2 không đối chiếu được ⇒ chặn.
# Dấu hiệu lấy RỘNG có chủ đích (nghi thì chặn) — không cần đoán đúng mọi cách nói ("toàn đội", "cả nhóm", "hết thảy", "mỗi người", "mọi nhân viên"
# lọt ở @12 — C KIEM_KIEM_SO_12_C.md); mô hình có câu chuẩn để chép nên câu đúng vẫn có đường qua.
GROUP_SIGNAL = re.compile(r"\b(?:ai|người|bạn|nhân\s+viên|đội|nhóm|tất\s+cả|hết|mọi|mỗi|toàn)\b", re.IGNORECASE)
TEAM_VERB = re.compile(r"\b(?:có|được|đạt|nhận|tiền|thưởng)\b|\b0\s*đ", re.IGNORECASE)
TEAM_SENTENCE_END = re.compile(r"[!?;\n]|\.(?=\s|$)")   # dấu chấm trong số ("4.100.000") không cắt câu
# Phương án (ii) 11/11 sau phát lại (bản đầu luật 3 chặn mới 131 / 1.798 chữ thật): KIỂM BẰNG CODE thay vì đoán chữ
# câu liệt kê mã NV ⇒ tập mã phải BẰNG ĐÚNG tập có / chưa có tiền; câu có số tiền / mốc / cơ hội ra khỏi luật 3 CHỈ KHI không có từ khẳng định cả nhóm
# (A: "Cả đội đều có tiền PC_BT, tổng 4.400.000 đ" — số có nguồn nên number_check cho qua; miễn vì có tiền thì "đều có" lọt).
GROUP_ASSERT = re.compile(r"\b(?:đều|cũng|tất\s+cả|mọi|hết)\b", re.IGNORECASE)
LIST_MONEY = re.compile(r"\b(?:có|được|nhận)\s+(?:tiền|thưởng)\b|\bbị\s+0\b|\b0\s*đ", re.IGNORECASE)   # "chưa có tiền" cũng chứa "có tiền"
NEG_MONEY = re.compile(r"\b(?:chưa|không)\s+(?:có|được|nhận)\s+(?:tiền|thưởng)\b|\bbị\s+0\b|\b0\s*đ", re.IGNORECASE)
ONLY_WORD = re.compile(r"\b(?:chỉ(?!\s+tiêu)|duy\s+nhất|riêng)\b", re.IGNORECASE)   # "chỉ tiêu" không phải "chỉ" (phát lại: 3 câu chặn oan)
BULLET = re.compile(r"^[\s\-*•·>#]*")
NEG_AFTER_CODES = re.compile(r"^[\s,)]*(?:(?:thì|vẫn|đều|hiện|đang|là)\s+)*(?:chưa|không)\b", re.IGNORECASE)
CODE_OR_RANGE = re.compile(r"\bNV(\d+)\b(?:\s*[–-]\s*NV(\d+)\b)?", re.IGNORECASE)
# "KPI chưa ai có tiền: …", "Các KPI cả đội đều có tiền là …" — câu nói về TẬP chỉ tiêu (chữ "KPI" / "chỉ tiêu" đứng như danh từ chung)
KPI_LIST_CLAIM = re.compile(r"\b(?:các\s+|những\s+)?(?:KPI|chỉ\s+tiêu)\b(?!_)", re.IGNORECASE)
NEGATED_BEFORE_ANY = re.compile(r"\bkhông\s+(?:thuộc|phải|nằm\s+trong)\b", re.IGNORECASE)
STANDARD_LIKE = re.compile(r"\b\d+\s*/\s*\d+\s*(?:người|bạn|nhân\s+viên)\b", re.IGNORECASE)   # khuôn câu chuẩn "N / M người"
# Luật 1 không xét số mục công văn ("mục 6.2", "các mục 2.2, 2.3 và 8.2") và ngày đủ dd/mm/yyyy ("30/10/2026"): không phải số của NV đứng trước.
# 10/11 chạy thử 8 câu (C, CHAY_THU_8_CAU_CHAT_C.md câu 7): câu cờ dữ liệu ĐÚNG bị chặn trên Capitol / gắn cảnh báo trên chat. number_check vẫn xét các số đó.
# Mỗi phần số mục ≤ 2 chữ số (@7, minor 2: "mục 3.000.000" từng được che ⇒ tiền của NV khác sau "mục" thoát owner check)
# kiem_so@20 (13/11, câu 201 thật trên đường chat — C, KIEM_RAO_HOP_NHAT_C.md): (1) chữ chen giữa "mục / công văn / điều" và số ("Các mục công văn liên
# quan: 2,2, 2,3 … và 8,2", "theo công văn 6,2"), dấu "/" giữa các mục, dấu phẩy thập phân (bước trình bày đổi "2.2" ⇒ "2,2"); số ngay sau là đơn vị tiền /
# % thì KHÔNG che ("điều 5 triệu"); (2) giờ HH:MM ("pivot làm mới 30/10/2026 00:00") và ngày dd/mm sau chữ chỉ thời điểm ("tính đến 20/10").
# Không che "NV06 đạt 6.2 điểm" (không có chữ mục / công văn / điều).
SECTION_NUMBER = r"\d{1,2}(?:[.,]\d{1,2})*"
NOT_EMPLOYEE_NUMBERS = [re.compile(rf"\b(?:mục|công\s+văn|điều)(?:\s+(?:công\s+văn|liên\s+quan|số))*\s*:?\s*{SECTION_NUMBER}"
                                   rf"(?:\s*(?:,|/|và|hoặc)\s*{SECTION_NUMBER})*(?![\d.,]*\d)"
                                   r"(?!\s*(?:triệu|tr\b|nghìn|ngàn|k\b|đồng|đ\b|vnđ|vnd|%))", re.IGNORECASE),
                        re.compile(r"(?<![\d/])\d{1,2}/\d{1,2}/\d{4}(?![\d/])"),
                        re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?![\d:])"),
                        re.compile(r"\b(?:ngày|đến|tới|từ|hết|lúc)\s+\d{1,2}/\d{1,2}(?![\d/])", re.IGNORECASE)]
TIER_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
# Phủ định ngay trước "chưa ai / không ai" ("không thuộc nhóm “chưa ai…”", "không phải KPI cả đội chưa ai" — câu thật lượt e0b câu 4) và chữ loại trừ trong đoạn KPI ("…, trừ PC_ON") — `_claim_kpis`
NEGATED_BEFORE = re.compile(r"(?:không\s+(?:thuộc|phải|nằm\s+trong)|ngoại\s+trừ|\btrừ)(?:\s+(?:nhóm|là|vào|danh\s+sách|KPI|cả\s+đội|loại))*\s*[“\"«']?\s*$", re.IGNORECASE)
EXCEPT_WORD = re.compile(r"\b(?:ngoại\s+trừ|trừ|không\s+kể)\b", re.IGNORECASE)


def _field_by_employee(tool_results: list, pick) -> dict[str, set[Decimal]]:
    """{mã NV: giá trị các ô số mà `pick(dict chứa ô, tên ô)` chọn} — mỗi ô gán cho `employee_id` gần nhất bao nó."""
    found: dict[str, set[Decimal]] = {}

    def walk(value, owner):
        if isinstance(value, dict):
            if isinstance(value.get("employee_id"), str):
                owner = value["employee_id"].upper()
            for key, item in value.items():
                if isinstance(item, (int, float)) and not isinstance(item, bool) and pick(value, key):
                    found.setdefault(owner, set()).add(Decimal(str(item)))
                elif key != "params_used":
                    walk(item, owner)
        elif isinstance(value, list):
            for item in value:
                walk(item, owner)

    for result in tool_results:
        walk(result, "TEAM")
    return found


def achieved_percent_by_employee(tool_results: list) -> dict[str, set[Decimal]]:
    """{mã NV: các giá trị trường `pct` (% ĐẠT của KPI, công cụ đã làm tròn xuống)} — để "NV13 đạt 100 %" không khớp nhờ tên mốc "Đạt 100%"
    (tầng A -51: công cụ 99,5 % ⇒ số 100 có sẵn ở tên mốc nên luật 1 cho qua).
    """
    return _field_by_employee(tool_results, lambda row, key: key == "pct")


def reached_tier_percents_by_employee(tool_results: list) -> dict[str, set[Decimal]]:
    """{mã NV: mức % trong tên mốc NV đó ĐÃ ĐẠT} — chỉ ô `reached_tier` / `kpi_reached_tier` (không lấy danh sách mốc `tiers`, để "NV02 đạt 100 %"
    khi chỉ có mốc kế "Đạt 100%" vẫn bị bắt — Y5-51). 10/11 (thử câu 2): "ASO_ALL … mốc thưởng 'Đạt 100%'" — tên mốc đúng của NV02 bị chặn.
    """
    found: dict[str, set[Decimal]] = {}

    def walk(value, owner):
        if isinstance(value, dict):
            if isinstance(value.get("employee_id"), str):
                owner = value["employee_id"].upper()
            for key, item in value.items():
                labels = [item] if key == "reached_tier" else list(item.values()) if key == "kpi_reached_tier" and isinstance(item, dict) else []
                for label in labels:
                    if isinstance(label, str):
                        found.setdefault(owner, set()).update(Decimal(m.group(1).replace(",", ".")) for m in TIER_PERCENT.finditer(label))
                if key != "params_used":
                    walk(item, owner)
        elif isinstance(value, list):
            for item in value:
                walk(item, owner)

    for result in tool_results:
        walk(result, "TEAM")
    return found


def ratio_actual_by_employee(tool_results: list) -> dict[str, set[Decimal]]:
    """{mã NV: ô `actual` của KPI tỷ lệ} — để "LPPC_Meat của NV04 là 3,8" (công cụ 3,82) không khớp nhờ mốc "Mức 1: 3.8"
    (tầng A -50 — "tỷ lệ cũng đúng y số công cụ" — cùng loại Y5-51 của %).
    """
    return _field_by_employee(tool_results, lambda row, key: key == "actual" and row.get("kpi") in RATIO_KPIS)


def kpis_listed_by_employee(tool_results: list) -> dict[str, set[str]]:
    """{mã NV: tên KPI của các dòng công cụ liệt kê riêng cho NV đó} — dòng = dict có `employee_id` + `kpi` (vd mục của get_opportunities)."""
    found: dict[str, set[str]] = {}

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("employee_id"), str) and isinstance(value.get("kpi"), str):
                found.setdefault(value["employee_id"].upper(), set()).add(value["kpi"])
            for key, item in value.items():
                if key != "params_used":
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for result in tool_results:
        walk(result)
    return found


def numbers_by_employee(tool_results: list) -> dict[str, set[Decimal]]:
    """{mã NV hoặc "TEAM": tập số} — mỗi số (cả số nằm trong chữ, vd `reason`) gán cho `employee_id` gần nhất bao nó."""
    found: dict[str, set[Decimal]] = {}

    def walk(value, owner):
        if isinstance(value, dict):
            if isinstance(value.get("employee_id"), str):
                owner = value["employee_id"].upper()
            for key, item in value.items():
                # params_used: tham số mô hình gửi, không phải dữ liệu · team_sentences: câu chuẩn code viết (số đếm người) — không là số "cấp đội"
                # cho mọi NV (để "NV05 có 13 cửa hàng" không có nguồn nhờ "13 / 14 người")
                if key not in ("params_used", "team_sentences"):
                    walk(item, owner)
        elif isinstance(value, list):
            for item in value:
                walk(item, owner)
        else:
            found.setdefault(owner, set()).update(nc.source_numbers([value]))

    for result in tool_results:
        walk(result, "TEAM")
    return found


TABLE_SEPARATOR = re.compile(r"[·|]")
HYPOTHETICAL_KEY = ("if_", "gain", "tier", "gap", "next", "need", "missing", "target", "params_used", "team_sentences")
# Đảo luật — ô TIỀN THẬT có tên trùng phần tên giả định ("target", "gaps"): phát lại 942 chữ thật — "NV04 không có thưởng vượt … 0 đồng"
# (`over_target_bonus`), "NV03 đang được 800.000 đồng" (`gaps[…].amount_now`) từng bị coi là không phải tiền của chính người đó
# kiem_so@17 (12/11) — LÙI phần nới của @15: ô `target` (CHỈ TIÊU) KHÔNG nằm trong tiền thật của người đó (như @14).
# @15 đưa "target" vào đây để hết chặn oan câu "NV04 — Turnover: chỉ tiêu 1.314.914.524 đ" (M3 Capitol câu 03) ⇒ lọt "NV04 được 23.800.000 đ" (thật là
# chỉ tiêu Kokomi của NV04). @16 thử chỉ mở trong "ngữ cảnh chỉ tiêu" bằng dò chữ ⇒ người kiểm độc lập tìm 14 câu lọt mới ngoài bộ ghi trước ⇒ bỏ theo điểm
# dừng một vòng (hồ sơ: nhánh `b-rao-16`). Cái giá đã biết của bản này: câu chỉ tiêu đúng viết kèm "đ" bị chặn oan — lớp sau là bước làm lại + Người duyệt.
REAL_MONEY_KEYS = ("over_target_bonus", "amount_now")


def actual_money_by_employee(tool_results: list) -> dict[str, set[Decimal]]:
    """{mã NV: các ô SỐ (không lấy số trong chữ) là tiền / số THẬT của NV đó} — bỏ cây con giả định / mốc ("amount_if_reached", "amount_gain", "tiers",
    "gaps", "next_…", "target"): bản cuối 3, C B5 — 1.500.000 là "nếu lên mốc" của NV05 nên luật 1 cho "NV05 được 1.500.000 đ" qua."""
    found: dict[str, set[Decimal]] = {}

    def walk(value, owner, real_only=False):
        if isinstance(value, dict):
            if isinstance(value.get("employee_id"), str):
                owner = value["employee_id"].upper()
            for key, item in value.items():
                number = isinstance(item, (int, float)) and not isinstance(item, bool)
                if key in REAL_MONEY_KEYS and number:
                    found.setdefault(owner, set()).add(Decimal(str(item)))
                elif real_only or any(part in key for part in HYPOTHETICAL_KEY):
                    if not number:
                        walk(item, owner, True)   # trong cây con giả định chỉ lấy REAL_MONEY_KEYS ("gaps[…].amount_now")
                elif number:
                    found.setdefault(owner, set()).add(Decimal(str(item)))
                else:
                    walk(item, owner)
        elif isinstance(value, list):
            for item in value:
                walk(item, owner, real_only)

    for result in tool_results:
        walk(result, "TEAM")
    return found


def single_person_totals(tool_results: list) -> dict[str, set[Decimal]]:
    """{mã NV: các số tiền / tổng của một NHÓM chỉ có đúng MỘT người} — bản cuối 3 (trên dữ liệu thật): PC_ON chỉ NV04 có tiền ⇒ tổng đội
    1.500.000 = tiền NV04, nhưng nằm ở cấp đội nên "NV05 được 1.500.000 đ ở PC_ON" từng được tha qua `team_numbers`. Suy từ HÌNH dữ liệu, không theo tên
    công cụ: dict có danh sách người có tiền (`paid_employee_ids`, hoặc `employee_ids` của một mốc) dài đúng 1 ⇒ mọi ô số có "amount" / "total" trong tên
    ô ở dict đó là của người ấy (tóm tắt một KPI, mốc một người, khoản Thưởng thêm chỉ một người có).
    """
    found: dict[str, set[Decimal]] = {}

    def walk(value):
        if isinstance(value, dict):
            ids = value.get("paid_employee_ids") if isinstance(value.get("paid_employee_ids"), list) else value.get("employee_ids")
            if isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], str):
                found.setdefault(ids[0].upper(), set()).update(
                    Decimal(str(item)) for key, item in value.items()
                    if isinstance(item, (int, float)) and not isinstance(item, bool) and ("amount" in key or "total" in key))
            for key, item in value.items():
                if key not in ("params_used", "team_sentences"):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for result in tool_results:
        walk(result)
    return found


def team_kpi_counts(tool_results: list) -> dict | None:
    """Số người có tiền theo KPI do code đếm, từ kết quả của lượt: {"team": số NV, "paid": {KPI: số người có tiền > 0}}.

    Nguồn: `get_team_bonus_table` (mọi KPI) và / hoặc `get_team_kpi_summary` (A1 nhóm — một KPI, ô `paid_count`); không có cả hai ⇒ None.
    KPI không có trong "paid" = lượt này không có số đếm của KPI đó (bên gọi báo "chưa đối chiếu được", không coi là 0).
    """
    team, paid = None, {}
    for result in tool_results:
        if not isinstance(result, dict):
            continue
        employees = result.get("employees")
        if employees and all(isinstance(e, dict) and "kpi_amounts" in e for e in employees):
            team = len(employees)
            kpis = {kpi for e in employees for kpi in e["kpi_amounts"]}
            paid |= {kpi: sum(1 for e in employees if (e["kpi_amounts"].get(kpi) or 0) > 0) for kpi in kpis}
        elif isinstance(result.get("kpi"), str) and isinstance(result.get("paid_count"), int) and isinstance(result.get("employee_count"), int):
            team = result["employee_count"]
            paid[result["kpi"]] = result["paid_count"]
    return None if team is None else {"team": team, "paid": paid}


# Câu cả đội do CODE viết sẵn (bậc (a) 11/11 — sau @12: 3 bản vá liền @10–@12 vẫn lọt câu cả đội vì rào phải đoán cách nói) ----------
TEAM_TOOLS = ("get_team_bonus_table", "get_team_kpi_summary")


def _paid_by_kpi(tool_results: list) -> tuple[dict[str, tuple[list[str], list[str]]], bool]:
    """({KPI: (mã NV có tiền, mã NV chưa có)}, có bảng thưởng ĐỦ đội không) — đọc đúng ô code đã tính (`kpi_amounts` / `paid_employee_ids`), như team_kpi_counts."""
    found, full_table = {}, False
    for result in tool_results:
        if not isinstance(result, dict):
            continue
        employees = result.get("employees")
        if employees and all(isinstance(e, dict) and "kpi_amounts" in e and isinstance(e.get("employee_id"), str) for e in employees):
            full_table = True
            for kpi in dict.fromkeys(kpi for e in employees for kpi in e["kpi_amounts"]):   # giữ thứ tự KPI của công cụ
                paid = [e["employee_id"] for e in employees if (e["kpi_amounts"].get(kpi) or 0) > 0]
                found[kpi] = (paid, [e["employee_id"] for e in employees if e["employee_id"] not in paid])
        elif isinstance(result.get("kpi"), str) and isinstance(result.get("paid_employee_ids"), list) \
                and isinstance(result.get("unpaid_employee_ids"), list):
            found[result["kpi"]] = (list(result["paid_employee_ids"]), list(result["unpaid_employee_ids"]))
    return found, full_table


def _listed_codes(sentence: str) -> list[str]:
    """Mã NV nêu trong câu, mở dải "NV01–NV09" thành từng mã (câu thật 04/11 g3d c201 "Cả 14 nhân viên (NV01–NV14) …")."""
    codes = []
    for match in CODE_OR_RANGE.finditer(sentence):
        first, last = int(match.group(1)), int(match.group(2) or match.group(1))
        width = len(match.group(1))
        codes += [f"NV{number:0{width}d}" for number in range(first, last + 1)] if last >= first else [f"NV{match.group(1)}"]
    return list(dict.fromkeys(codes))


def team_sentences(tool_results: list) -> list[str]:
    """Câu chuẩn về số người có / chưa có tiền theo KPI — CODE viết từ chính kết quả công cụ cả đội; mô hình chỉ được CHÉP NGUYÊN (lời dặn + mô tả công cụ).
    Mã NV liệt kê ở phía ít người hơn (bằng nhau ⇒ phía có tiền). Chỉ mã che NVxx (kết quả công cụ không có tên thật)."""
    found, full_table = _paid_by_kpi(tool_results)
    sentences = []
    for kpi, (paid, unpaid) in found.items():
        team = len(paid) + len(unpaid)
        if not unpaid:
            sentences.append(f"{kpi}: {team} / {team} người có tiền.")
        elif not paid:
            sentences.append(f"{kpi}: 0 / {team} người có tiền, {team} người chưa có.")
        elif len(paid) <= len(unpaid):
            sentences.append(f"{kpi}: {len(paid)} / {team} người có tiền ({', '.join(paid)}), {len(unpaid)} người chưa có.")
        else:
            sentences.append(f"{kpi}: {len(paid)} / {team} người có tiền, {len(unpaid)} người chưa có ({', '.join(unpaid)}).")
    if full_table:   # câu nhóm chỉ khi có bảng ĐỦ mọi KPI — một KPI lẻ không nói được "KPI nào chưa ai có"
        nobody = [kpi for kpi, (paid, _) in found.items() if not paid]
        everybody = [kpi for kpi, (_, unpaid) in found.items() if not unpaid]
        sentences.append(f"KPI chưa ai có tiền: {', '.join(nobody)}." if nobody else "Không có KPI nào chưa ai có tiền.")
        sentences.append(f"KPI cả đội đều có tiền: {', '.join(everybody)}." if everybody else "Không có KPI nào cả đội đều có tiền.")
    return sentences


def spice_sentence(row: dict) -> str:
    """Câu chuẩn cho MỘT dòng của công cụ `get_spice_loyalty` (1 % gia vị — tiền trả KHÁCH, gom theo nhân viên phụ trách). Code viết, mô hình chép nguyên:
    câu tự nói rõ người nhận là khách, để số tiền đứng sau mã nhân viên không bị đọc thành tiền của nhân viên đó."""
    reward = f"{row['reward_paid_to_customers']:,}".replace(",", ".")
    return (f"Khách hàng do {row['salesperson_in_charge']} phụ trách: {row['customers_reached']} / {row['customers_in_program']} khách đạt mức; "
            f"tiền thưởng 1 % gia vị trả cho KHÁCH là {reward} đ (không phải tiền của nhân viên).")


SPICE_ROW_KEYS = ("salesperson_in_charge", "customers_in_program", "customers_reached", "reward_paid_to_customers")


def spice_sentences(tool_results: list) -> list[str]:
    """Câu chuẩn 1 % gia vị tính LẠI từ kết quả công cụ `get_spice_loyalty` của lượt (không tin ô `customer_reward_sentences` gửi kèm — như câu chuẩn cả đội)."""
    sentences = []
    for result in tool_results:
        rows = result.get("by_salesperson_in_charge") if isinstance(result, dict) else None
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and all(key in row for key in SPICE_ROW_KEYS):
                sentences.append(spice_sentence(row))
    return sentences


def issue_sentence(issue: dict) -> str:
    """Câu chuẩn của MỘT lưu ý dữ liệu (`get_data_issues`, bậc 3a P5): nguyên lời lưu ý + "liên quan <các mã che>" (lưu ý gắn người) hoặc "lưu ý chung
    của cả đội". Một chỗ viết cho cả backend (ô `sentence`) và lớp kiểm (tính lại câu để che) — như `spice_sentence`."""
    who = f"liên quan {', '.join(issue['employee_ids'])}" if issue["employee_ids"] else "lưu ý chung của cả đội"
    return f"{issue['message'].rstrip('. ')} — {who}."


def issue_sentences(tool_results: list) -> list[str]:
    """Câu chuẩn lưu ý dữ liệu tính LẠI từ lời + mã của từng lưu ý trong kết quả `get_data_issues` của lượt (không tin ô `sentence` gửi kèm — như câu
    chuẩn cả đội): mô hình chép nguyên một câu thì lớp kiểm che câu đó, vì số trong lời lưu ý ("2 đơn …") là số của lưu ý chứ không phải số KPI của
    người đứng cuối câu."""
    sentences = []
    for result in tool_results:
        issues = result.get("issues") if isinstance(result, dict) else None
        for issue in issues if isinstance(issues, list) else []:
            if isinstance(issue, dict) and isinstance(issue.get("message"), str) and isinstance(issue.get("employee_ids"), list) \
                    and all(isinstance(code, str) for code in issue["employee_ids"]):
                sentences.append(issue_sentence(issue))
    return sentences


def with_team_sentences(name: str, result: dict) -> dict:
    """Kết quả công cụ cả đội + ô `team_sentences` (câu chuẩn để mô hình chép) — gắn ở cửa ra cho mô hình (`/api/tool`, chat `call_tool`), KHÔNG ở
    `backend/queries.py` (file đó nằm trong dấu vân tay bản tính — sửa ở đó = bản tính mới, mất "Đã soát"). Lời từ chối / lỗi giữ nguyên.
    `get_data_issues` (bậc 3a P5): mỗi lưu ý thêm ô `sentence` — cùng cửa ra, cùng lý do."""
    if not isinstance(result, dict) or "refused" in result or "error" in result:
        return result
    if name == "get_data_issues" and isinstance(result.get("issues"), list):
        return {**result, "issues": [{**issue, "sentence": issue_sentence(issue)} for issue in result["issues"]]}
    if name not in TEAM_TOOLS:
        return result
    return {**result, "team_sentences": team_sentences([result])}


def _kpi_names(tool_results: list) -> list[str]:
    """Tên KPI có trong kết quả công cụ, dài trước (để "ASO_Focus_2" không bị đọc thành "ASO_Focus")."""
    names = set()

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("kpi"), str):
                names.add(value["kpi"])
            if isinstance(value.get("kpi_amounts"), dict):
                names.update(value["kpi_amounts"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for result in tool_results:
        walk(result)
    return sorted(names, key=len, reverse=True)


def _clause_start(text: str, position: int) -> int:
    """Đầu vế câu chứa vị trí (sau dấu phẩy / chấm / chấm phẩy / hai chấm / xuống dòng gần nhất) — "mốc thấp nhất là Đạt 100 %" là tên mốc
    (phát lại 140 câu thật 04/11 vòng 2: 2 cờ nhầm khi chỉ xét 16 ký tự trước số)."""
    return max((m.end() for m in re.finditer(r"[,.;:\n]", text[:position])), default=0)

# Cuối câu cho `_owners_of_number`: ". " / "!" / "?" / xuống dòng — dấu chấm trong số ("104.34", "mục 6.2") không phải cuối câu
# ";" KHÔNG là cuối câu ở đây (kiem_so@10, @9 điểm 2 — e0b câu 7 "- PC_ON: 2 dòng … mục 6.2 — mã được liệt kê là NV08" ⇒ số 2 bị gán NV14 dòng trên)
OWNER_SENTENCE_END = re.compile(r"[!?\n]|\.(?=\s|$)")
# kiem_so@11 (@10, major 1 — hồi quy do @10): với SỐ TIỀN / %, ";" lại là hết vế — "NV05⏎- PC_BT: 4.100.000 đ; NV06 cũng vậy." (4,1 tr là của NV06)
# từng QUA vì mã NV06 sau ";" được nhận là chủ; câu đúng "… 3.200.000 đ; NV06 thì khác." từng bị chặn oan. Số đếm thường ("2 dòng … mục 6.2; mã … NV08",
# lượt e0b câu 7 — lý do của @10) vẫn đọc qua ";".
STRICT_OWNER_END = re.compile(r"[!?;\n]|\.(?=\s|$)")
# "… 3.200.000 đ của NV05" / "… thuộc NV05" / "… cho NV05" / "- **1.000.000 đồng:** NV05, NV08" (danh sách theo mức — câu thật lượt 09/11 moc_da c503):
# chữ nói rõ chủ ⇒ mã sau số thắng mã đứng trước (ca @7 giữ); mã đứng trần ("NV06 cũng vậy") ⇒ không rõ chủ
POSSESSIVE_BEFORE = re.compile(r"(?:\b(?:của|thuộc(?:\s+về)?|cho)|:[\s*_]*(?:\d+\s*(?:người|bạn|nhân\s+viên)\s*[—–-])?)[\s*_]*$", re.IGNORECASE)


def _owners_of_number(answer: str, start: int, end: int, same_sentence_after: bool = False, strict: bool = False) -> tuple[list[str], bool]:
    """(mã NV "chủ" của một số, số phải khớp dữ liệu của TẤT CẢ các mã đó không). Mặc định: mã gần nhất ĐỨNG TRƯỚC (cả câu trả lời — bảng / tiêu đề dòng trên).
    `same_sentence_after` (chỉ `kiem_so` bật — 10/11, chạy thử câu 7: "… NV14. PC_ON cũng có 2 dòng ngoài MCP, liên quan NV08" ⇒ số 2 bị gán NV14):
    CÂU chứa số không có mã nào đứng trước số nhưng có mã đứng SAU trong câu ⇒ chủ = các mã đó. Đường chat không bật: cờ theo người ở đó chưa gắn chủ
    (`kiem_so._with_flag_owners`) ⇒ số trong chữ cờ sẽ bị gán nhầm (10/11: "số 4 … NV04, NV08, NV14"). Cái giá: câu nhiều mã ⇒ kiểm theo HỢP dữ liệu
    các mã đó, không chặt từng người.
    `strict` (kiem_so@11 — số tiền / %): (a) có mã đứng trước ⇒ câu dừng ở ";"; (b) số có mã đứng trước ở dòng / câu trên (tiêu đề) VÀ mã khác đứng sau trong câu,
    không có "của / thuộc / cho / :" ngay trước mã sau đầu tiên (POSSESSIVE_BEFORE) ⇒ không chắc chủ ⇒ số phải đúng với MỌI mã đó (hướng "nghi thì chặn": "NV05⏎- PC_BT: 4.100.000 đ, NV06 cũng vậy."
    — dấu phẩy, cùng loại lỗi D — phải trượt).
    """
    before = EMPLOYEE_CODE.findall(answer[:start])
    nearest = [before[-1].upper()] if before else []
    if same_sentence_after:
        # ";" chỉ cắt khi có mã đứng trước: không có mã nào trước số thì mã sau ";" vẫn là chủ ("- PC_BT: 4.100.000 đ; của NV05." — ca @10 giữ).
        # Cắt cả HAI đầu vế: "NV02 được 6.740.000 đ; 14.540.000 đ là của NV04" — vế sau không có mã trước số ⇒ chủ = NV04 (giới hạn ghi ở @10, nay qua)
        boundary = STRICT_OWNER_END if strict and nearest else OWNER_SENTENCE_END
        sentence_start = max((m.end() for m in boundary.finditer(answer, 0, start)), default=0)
        stop = boundary.search(answer, end)
        if not EMPLOYEE_CODE.search(answer, sentence_start, start):
            found = list(EMPLOYEE_CODE.finditer(answer, end, stop.start() if stop else len(answer)))
            # kiem_so@20 (13/11, câu 802 thật): "126.660.000 đ là tổng lương kinh doanh cả đội, không phải tiền của NV08" — chữ cả đội / tổng đội đứng
            # GIỮA số và mã sau ⇒ mã sau không phải chủ của số (về luật mặc định: mã đứng trước)
            if found and TEAM_WORDS.search(answer, end, found[0].start()):
                found = []
            after = list(dict.fromkeys(m.group(0).upper() for m in found))
            if after and strict and nearest and nearest[0] not in after and not POSSESSIVE_BEFORE.search(answer, end, found[0].start()):
                return [*nearest, *after], True
            if after:
                return after, False
    return nearest, False


def mask_people_counts(answer: str) -> str:
    """Che (bằng khoảng trắng cùng độ dài) đúng các con số đứng ngay trước "người / bạn / nhân viên" — số người do `check_owners` xét bằng
    code đếm, không bắt `number_check` tìm nguồn ("13 người có tiền DS": 13 không có trong kết quả công cụ — B mẫu 14:14). Chỉ che đúng vị trí
    đó (1b, m3: bản trước thêm số người làm nguồn CHUNG ⇒ "NV01 có 13 cửa hàng" lọt cả hai phép kiểm).
    """
    return PEOPLE_COUNT.sub(lambda m: " " * len(m.group(0)), answer)


def _kpis_in(segment: str, names: list[str]) -> list[str]:
    """Mọi tên KPI trong đoạn (1b, M-b: "Chưa ai có tiền ở KPI Turnover, PC_BT và PC_ON" phải xét cả ba). Tên dài đọc trước, che đi
    để "ASO_Focus_2" không tính thêm "ASO_Focus".
    """
    found = []
    for name in names:
        pattern = rf"(?<![\w]){re.escape(name)}(?![\w])"
        if re.search(pattern, segment):
            found.append(name)
            segment = re.sub(pattern, " ", segment)
    return found


def _claim_kpis(answer: str, start: int, segment_end: int, sentence_start: int, kind: str, names: list[str]) -> tuple[list[str], set[str]]:
    """(KPI của một khẳng định, KPI trong số đó bị PHỦ ĐỊNH). KPI = đoạn sau khẳng định tới hết câu; không có thì đoạn trước nó trong câu.
    kiem_so@9 (10/11, câu thật T4 câu 7 lần làm lại: "PC_ON có 1/14 người được tiền, nên không thuộc nhóm chưa ai có tiền" bị chặn): chỉ với "không ai /
    chưa ai" — (a) ngay trước khẳng định là "không thuộc / không phải / không nằm trong / (ngoại) trừ" ⇒ các KPI đứng trước bị phủ định;
    (b) trong đoạn sau, KPI đứng sau "(ngoại) trừ / không kể" bị phủ định. KPI bị phủ định vẫn bị kiểm — theo chiều NGƯỢC (phải có ≥ 1 người có tiền)."""
    after = _kpis_in(answer[start:segment_end], names)
    if kind == "none" and after and (cut := EXCEPT_WORD.search(answer, start, segment_end)):
        kept = _kpis_in(answer[start:cut.start()], names)
        return after, {kpi for kpi in _kpis_in(answer[cut.end():segment_end], names) if kpi not in kept}
    if after:
        return after, set()
    before = _kpis_in(answer[sentence_start:start], names)
    if kind == "none" and NEGATED_BEFORE.search(answer, sentence_start, start):
        return before, set(before)
    return before, set()


def _unpaid_after(answer: str, position: int) -> bool:
    """Cụm ngay sau vị trí (tới dấu phẩy / hết câu) nói "chưa có / không có / bị 0" không."""
    end = CLAUSE_END.search(answer, position)
    return UNPAID_WORDS.search(answer, position, end.start() if end else len(answer)) is not None


def _people_claims(answer: str, masked: str) -> list[tuple]:
    """Các khẳng định về số người trong chữ ⇒ [(vị trí đầu, cuối đoạn KPI, chữ gốc, loại, số nói, đầu câu)]; loại: count / all / none.
    Đoạn KPI của một khẳng định = từ nó tới khẳng định kế tiếp hoặc hết câu (hai câu đếm trong một câu không lấy KPI của nhau)."""
    hits = [(m.start(), m.end(), m.group(0), "count", int(m.group(1))) for m in PEOPLE_COUNT.finditer(masked)]
    hits += [(m.start(), m.end(), m.group(0), "all", None) for m in ALL_WORDS.finditer(answer)]
    hits += [(m.start(), m.end(), m.group(0), "none", None) for m in NONE_WORDS.finditer(answer)]
    hits.sort()
    claims = []
    for index, (start, end, text, kind, said) in enumerate(hits):
        stop = SENTENCE_END.search(answer, end)
        segment_end = stop.start() if stop else len(answer)
        if index + 1 < len(hits) and hits[index + 1][0] < segment_end:
            segment_end = hits[index + 1][0]
        sentence_start = max((m.end() for m in SENTENCE_END.finditer(answer, 0, start)), default=0)
        claims.append((start, segment_end, text, kind, said, sentence_start))
    return claims
# ĐẢO LUẬT ( 11/11 chiều, câu 1c (b)) — số TIỀN có mã NV chủ phải là tiền của CHÍNH người đó ----------
# Đường được qua (mỗi đường chỉ mở đúng ô của chủ số / nhóm được nêu, không mở "số có ở đâu đó"):
# 1. tiền THẬT của chính người đó (ô số ngoài cây con giả định + tổng một nhóm chỉ có người đó + Thưởng thêm trong dict có `employee_id` người đó);
# 2. câu có ĐIỀU KIỆN ( 13:31: "nếu / giả sử / giả định / khi … thì / đạt … thì", hoặc "… đồng khi / nếu …" ngay sau số, hoặc tiêu đề bảng
#    "… nếu đạt") ⇒ thêm mọi ô của chính người đó (mốc, `amount_if_reached`). "sẽ" một mình KHÔNG là điều kiện: 2.200.000 là mốc "Đạt 100%" PC_BT
# của cả 14 NV ⇒ "NV05 sẽ nhận 2.200.000 đ PC_BT" (NV05 0 đ) phải trượt (trên dữ liệu thật);
# 3. số "còn thiếu" của chính người đó (`gap_to_next`) khi vế có "thiếu / cần" — số khoảng cách, không phải tiền được nhận;
# 4. tổng của một NHÓM (dict có danh sách mã ≥ 2 người + ô amount / total) khi câu nêu ĐÚNG tập mã đó ("6 bạn đã có thưởng: NV02, …, NV09");
# 5. chênh lệch in sẵn của phép so hai người (`a_minus_b` / `gap` cạnh `higher`) khi câu nêu cả hai mã, có "hơn / chênh / kém" TRƯỚC số và ĐÚNG
#    chiều (mã ngay trước "hơn" là người cao hơn; trước "kém / thấp hơn" là người thấp hơn);
# 6. số cấp đội khi "cả đội / tổng đội / toàn đội / cả nhóm" đứng NGAY trước số (≤ 4 chữ chen) trong vế KHÔNG có mã NV (cụm đã chọn — hẹp có chủ đích).
# Bản cuối 3 (C lách trên _2, 13:56): đường 4 cần vế của số không có mã hoặc có ĐỦ tập mã của nhóm; đường 4, 6 không qua khi vế có từ chia
# đều ("mỗi người / từng bạn / đều").
# Giới hạn (13:31, không vá): luật ở mức NGƯỜI, không mức KPI — "NV05 được 1.000.000 đ ở PC_ON" (tiền KPI khác của chính NV05) qua.
# Bản cuối 3 (C lách 2d / 2e trên _2: "NV05 được 2.200.000 đ nếu tính theo bảng hiện tại", "… đồng khi chốt lương" QUA): chữ điều kiện phải kèm một
# hành động lên mốc (đạt / lên / chạm / tới / vượt / tăng / thêm / bán / mua) trong câu — "nếu" / "khi" trần không mở ô mốc
CONDITION_BEFORE = re.compile(r"\b(?:nếu|giả\s+sử|giả\s+định|khi)\b.*\b(?:đạt|lên|chạm|tới|vượt|tăng|thêm|bán|mua)\b|\b(?:đạt|lên|chạm)\b.*\bthì\b", re.IGNORECASE)
CONDITION_AFTER = re.compile(r"\s*(?:đồng|đ|vnđ|vnd)?[^.;!?\n]{0,20}?\b(?:khi|nếu)\s+(?:\S+\s+){0,3}?(?:đạt|lên|chạm|tới|vượt)\b", re.IGNORECASE)
GAP_WORDS = re.compile(r"\b(?:thiếu|cần|thêm|còn|trung\s+bình|mỗi\s+ngày)\b|/\s*ngày", re.IGNORECASE)
TIER_NAME_WORDS = re.compile(r"\b(?:mốc|mức)\b", re.IGNORECASE)
NEXT_TIER_LEAD = re.compile(r"\bmốc\s+(?:kế\s+tiếp|tiếp\s+theo|kế|sau|cao\s+hơn)|\b(?:còn\s+thiếu|cần\s+thêm)\b|\bđể\s+(?:đạt|lên|chạm)\b"
                            r"|\blên\s+(?:các\s+|những\s+)?mốc\b|\b(?:nếu|khi)\s+đạt\b", re.IGNORECASE)
ASSERT_NOW = re.compile(r"\b(?:hiện|đang|đã)\b|\bđược\s+nhận\b", re.IGNORECASE)
# Bản cuối 3b ( _3, major HỒI QUY ca cũ L-C5: "NV05 đã lên mốc, được 1.500.000 đ PC_ON." khi NV05 0 đ — a1329678 chặn, _3 qua vì dấu phẩy cắt vế
# nên "đã" ngoài vế, còn "mốc" mở ô mốc): câu nói người đó ĐÃ / ĐANG ở mốc ⇒ chữ mốc không mở ô mốc. Hẹp có chủ đích — xét "đã / đang / hiện" trên cả
# câu thì phát lại 942 chữ thật chặn oan thêm 9 ("hiện ghi nhận 151/335 đơn …, mốc thấp nhất … 2.200.000 đồng")
REACHED_TIER = re.compile(r"\b(?:đã|đang|hiện)\s+(?:(?:được|có)\s+)?(?:lên|đạt|chạm|ở|tới|vượt|nằm\s+ở)\s+(?:(?:được|tới)\s+)?(?:các\s+)?(?:mốc|mức)\b", re.IGNORECASE)
COMPARE_WORDS = re.compile(r"\b(?:hơn|chênh|kém)\b", re.IGNORECASE)
LOWER_WORDS = re.compile(r"\b(?:kém|thấp\s+hơn|ít\s+hơn)\b", re.IGNORECASE)   # chiều ngược: mã đứng trước là người THẤP hơn
# Bản cuối 3 (C lách 4a trên _2: "NV02, NV04, NV09, NV10, NV11 mỗi người được 5.200.000 đ" — tổng nhóm chia cho từng người): từ chia đều trong vế số
DISTRIBUTIVE = re.compile(r"\b(?:mỗi|từng)\s+(?:người|bạn|nhân\s+viên|NV)|\bđều\b", re.IGNORECASE)
TEAM_WORDS = re.compile(r"\b(?:cả|tổng|toàn)\s+(?:đội|nhóm)\b", re.IGNORECASE)
TEAM_NEAR_NUMBER = re.compile(r"\b(?:cả|tổng|toàn)\s+(?:đội|nhóm)\b[\s:*_]*(?:[^\s\d]+[\s:*_]+){0,4}$", re.IGNORECASE)
# Hết vế cho TEAM_WORDS / GAP_WORDS: dấu phẩy / chấm không nằm trong số ("4.100.000", "3,82"), chấm phẩy, xuống dòng — KHÔNG có ":" ("Tổng cả đội: 126.660.000 đ")
CLAUSE_END_NOT_COLON = re.compile(r"[;!?\n]|[.,](?!\d)")
# kiem_so@23: dấu phẩy (hoặc ", và") giữa hai mã NV — chỉ dùng cho đường tổng nhóm (xem _money_passes); nhóm 1 = mã đứng trước, nhóm 2 = dấu phẩy
LIST_COMMA = re.compile(r"(\bNV\d+\b)(\s*,)(?=\s*(?:và\s+)?NV\d+\b)", re.IGNORECASE)


def group_totals(tool_results: list) -> list[tuple[frozenset, set[Decimal]]]:
    """[(tập mã của nhóm, các ô amount / total của nhóm)] — dict có `paid_employee_ids` / `employee_ids` từ 2 người (một người: `single_person_totals`)."""
    found = []

    def walk(value):
        if isinstance(value, dict):
            ids = value.get("paid_employee_ids") if isinstance(value.get("paid_employee_ids"), list) else value.get("employee_ids")
            if isinstance(ids, list) and len(ids) >= 2 and all(isinstance(code, str) for code in ids):
                found.append((frozenset(code.upper() for code in ids), {
                    Decimal(str(item)) for key, item in value.items()
                    if isinstance(item, (int, float)) and not isinstance(item, bool) and ("amount" in key or "total" in key)}))
            for key, item in value.items():
                if key not in ("params_used", "team_sentences"):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for result in tool_results:
        walk(result)
    return found


def difference_pairs(tool_results: list) -> list[tuple[frozenset, dict[Decimal, str]]]:
    """[(hai mã được so, {ô chênh lệch: mã người CAO hơn})] — kết quả `compare_employees`: mỗi dict có `higher` là một chênh lệch; hai mã = các
    `employee_id` của chính kết quả đó (hai bên `sides`)."""
    found = []
    for result in tool_results:
        codes, values = set(), {}

        def walk(value):
            if isinstance(value, dict):
                if isinstance(value.get("employee_id"), str):
                    codes.add(value["employee_id"].upper())
                if "higher" in value:
                    for key in ("a_minus_b", "gap"):
                        if isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool):
                            for sign in (1, -1):
                                values[Decimal(str(value[key])) * sign] = str(value["higher"]).upper()
                for key, item in value.items():
                    if key != "params_used":
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(result)
        if len(codes) == 2 and values:
            found.append((frozenset(codes), values))
    return found


def _sentence_span(masked: str, position: int) -> tuple[int, int]:
    """Đầu / cuối câu chứa vị trí (". " / "!" / "?" / xuống dòng — dấu chấm trong số không cắt)."""
    start = max((m.end() for m in OWNER_SENTENCE_END.finditer(masked, 0, position)), default=0)
    stop = OWNER_SENTENCE_END.search(masked, position)
    return start, stop.start() if stop else len(masked)


def _clause_after_last_code(answer: str, masked: str, position: int) -> int:
    """Đầu vế chứa số (không cắt ở ":"), dời tới sau mã NV gần nhất nếu mã nằm trong vế — "Tổng cả đội NV01 nhận …" ⇒ chữ trước mã không tính."""
    start = max((m.end() for m in CLAUSE_END_NOT_COLON.finditer(masked, 0, position)), default=0)
    codes = list(EMPLOYEE_CODE.finditer(answer, start, position))
    return codes[-1].end() if codes else start


def _table_above(answer: str, position: int) -> str:
    """Các dòng bảng liền phía trên dòng chứa vị trí, gồm dòng tiêu đề ("KPI · Còn thiếu · Mốc kế tiếp · Tiền nếu đạt") — hàm cũ chỉ lấy
    dòng gần nhất không có mã NV nên với bảng không có mã ở mỗi dòng nó lấy nhầm dòng dữ liệu ngay trên, không phải tiêu đề. Ô không trong bảng ⇒ ""."""
    lines = answer[:position].split("\n")
    if not TABLE_SEPARATOR.search(lines[-1]):
        return ""
    above = []
    for line in reversed(lines[:-1]):
        if not TABLE_SEPARATOR.search(line):
            break
        above.append(line)
    return "\n".join(above)


LIST_ITEM = re.compile(r"^\s*(?:[-*•+]|\d+[.)])\s+")


def _list_lead_above(answer: str, position: int) -> str:
    """Dòng dẫn của danh sách chứa vị trí (dòng đầu tiên phía trên không phải mục danh sách, bỏ dòng trống) — bản cuối 2 (nhóm kỹ thuật soát @14,
    major: câu thật m3_v13/12 của get_mid_month_gap "NV02 còn thiếu để lên các mốc kế tiếp:⏎⏎- **PC_BT – đạt 100%:** cần thêm **104**; thưởng
    **2.200.000 đồng**." bị chặn oan — chữ "mốc" chỉ ở dòng dẫn). Dòng không phải mục danh sách ⇒ ""."""
    lines = answer[:position].split("\n")
    if not LIST_ITEM.match(lines[-1]):
        return ""
    for line in reversed(lines[:-1]):
        if line.strip() and not LIST_ITEM.match(line):
            return line
    return ""


def _context_above(answer: str, position: int) -> str:
    """Chữ phía trên được tính như cùng câu khi xét điều kiện / mốc / còn thiếu: các dòng bảng (cả tiêu đề) + dòng dẫn danh sách — chỉ để mở ô của
    CHÍNH chủ số, không mở số người khác / số cấp đội."""
    return _table_above(answer, position) + "\n" + _list_lead_above(answer, position)


def _has_condition(answer: str, masked: str, position: int, end: int) -> bool:
    sentence_from, _ = _sentence_span(masked, position)
    return bool(CONDITION_BEFORE.search(masked, sentence_from, position) or CONDITION_AFTER.match(masked, end)
                or CONDITION_BEFORE.search(_context_above(answer, position)))


def _money_passes(answer: str, masked: str, match, options, multiplier, owners: list[str], every: bool, sources: dict) -> bool | str:
    """Số tiền có mã chủ đi được đường nào trong 6 đường ở trên không — qua đường 1 (tiền của chính người đó) ⇒ "own" / "own_condition" / "own_gap",
    đường khác ⇒ True, không đường nào ⇒ False."""
    def sourced(values) -> bool:
        return nc._sourced(options, multiplier, False, values, set(), True)
    clause_from, table = _clause_after_last_code(answer, masked, match.start()), _context_above(answer, match.start())
    sentence_from, sentence_to = _sentence_span(masked, match.start())
    codes_before = list(EMPLOYEE_CODE.finditer(answer, sentence_from, match.start()))
    after_code = codes_before[-1].end() if codes_before else sentence_from   # "Mốc thấp nhất … là Đạt 100 %, tương ứng 2.200.000 đồng"
    # Câu có điều kiện, hoặc số được nêu là tiền CỦA MỐC ("mốc thấp nhất là 100 % để nhận 2.200.000 đồng", "tiền ở mốc đó là 640.000 đồng") —
    # phát lại 942 chữ thật: ≈ 90 câu giải thích mốc đúng bị chặn khi chỉ nhận câu điều kiện. Cái giá (giới hạn): "NV05 ở mốc 100 % sẽ nhận 2.200.000 đ"
    # qua (tiền mốc của chính NV05, câu nói sai NV05 đang ở mốc đó) — cùng loại lỗi CÙNG NV đã ghi giới hạn
    # Bản cuối 3 (nhóm kỹ thuật soát _2, (5)): dòng dẫn / tiêu đề chỉ mở ô mốc khi nói mốc KẾ TIẾP / còn thiếu / để đạt / lên mốc ("mức" trần không
    # mở); vế của số KHẲNG ĐỊNH tiền hiện có ("hiện / đang / đã / được nhận") ⇒ chữ mốc không mở ô mốc — đòi tiền thật (dòng dẫn "Mức thưởng:" + "- PC_BT:
    # đang được 2.200.000 đồng" khi NV 0 đ từng qua ở _2)
    number_clause = answer[max((m.end() for m in CLAUSE_END_NOT_COLON.finditer(masked, 0, match.start())), default=0):match.start()]
    tier_open = (TIER_NAME_WORDS.search(answer, after_code, match.start()) is not None or NEXT_TIER_LEAD.search(table) is not None) \
        and not ASSERT_NOW.search(number_clause) and not REACHED_TIER.search(answer, sentence_from, match.start())
    condition = _has_condition(answer, masked, match.start(), match.end()) or tier_open
    gap_words = GAP_WORDS.search(answer, after_code, match.start()) is not None or GAP_WORDS.search(table) is not None

    def mine(code: str) -> set[Decimal]:
        values = sources["actual"].get(code, set()) | sources["single"].get(code, set())
        if condition:
            values = values | sources["owned"].get(code, set())
        if gap_words:
            values = values | sources["gaps"].get(code, set())
        return values

    if all(sourced(mine(code)) for code in owners) if every else sourced(set().union(*(mine(code) for code in owners))):
        # kiem_so@22: báo ĐƯỜNG đã qua cho lớp "đúng khoản" — chỉ số qua đường tiền của chính người đó mới xét khoản (tổng nhóm / số cấp đội / chênh
        # lệch / số còn thiếu không phải ô của một khoản); câu mốc / điều kiện xét theo bảng mốc
        # "thêm" của tên khoản "thưởng thêm" không phải chữ còn thiếu / cần thêm (ca C9 "NV04 được thưởng thêm 2.000.000 đ" khi Thưởng thêm 1.558.000)
        unnamed = EXTRA_BONUS_NAME.sub(lambda m: " " * len(m.group(0)), answer)
        item_gap = GAP_WORDS.search(unnamed, after_code, match.start()) is not None or \
            GAP_WORDS.search(EXTRA_BONUS_NAME.sub(lambda m: " " * len(m.group(0)), table)) is not None
        return "own_gap" if item_gap else ("own_condition" if condition else "own")
    # Vế của số (cắt ở , ; . xuống dòng — không ở ":") có mã NV không: bản cuối 3 (C lách 4c, 6a / 6b / 6d trên _2 — "Trong NV02, …, NV11, NV02 nhận
    # 5.200.000 đ", "NV05 được cả đội khen và nhận 55.700.000 đ") ⇒ tổng nhóm / số cấp đội chỉ qua khi vế của số KHÔNG gán nó cho một mã.
    clause_start = max((m.end() for m in CLAUSE_END_NOT_COLON.finditer(masked, 0, match.start())), default=0)
    clause_stop = CLAUSE_END_NOT_COLON.search(masked, match.end())
    clause = answer[clause_start:clause_stop.start() if clause_stop else len(answer)]
    free_clause = not EMPLOYEE_CODE.search(clause) and not DISTRIBUTIVE.search(clause)
    named = set(code.upper() for code in _listed_codes(answer[sentence_from:sentence_to]))
    clause_codes = set(code.upper() for code in _listed_codes(clause))
    # Tổng nhóm ( 13:56 (1)): vế của số không có mã (mã nằm ở vế khác của câu — "…: NV04, NV09; tổng thưởng 4.400.000 đồng"), hoặc chủ ngữ là
    # CẢ nhóm (vế chứa đúng đủ tập mã — "Nhóm NV04 và NV09 được tổng 4.400.000 đ"); có "mỗi người / từng bạn / đều" ⇒ không miễn
    if not DISTRIBUTIVE.search(clause) and (not clause_codes or clause_codes == named) \
            and any(codes == named and sourced(values) for codes, values in sources["groups"]):
        return True
    # kiem_so@23 (17/11, HẸP): dấu phẩy NẰM GIỮA HAI MÃ là dấu liệt kê, không phải hết vế — "Tổng của NV01, NV02 và NV05 là 37.220.000 đ",
    # "Nhóm 7 KPI (NV01, NV02, …, NV10): tổng 72.980.000 đ" từng bị gán cho mã cuối (đo công cụ tính 17/11, câu CT4 ⚠ cả 2 lượt). Chỉ mở cho đường
    # TỔNG NHÓM: vế nới phải chứa ĐÚNG tập mã của câu VÀ số phải bằng tổng có sẵn của đúng tập đó (`group_totals`: kết quả calc_* có employee_ids,
    # hoặc nhóm của công cụ đọc); không khớp ⇒ đi tiếp luật cũ (chặn). Không đổi gì khác.
    listed = list(masked)   # `masked` đã che mã NV ⇒ tìm dấu phẩy liệt kê trên chữ gốc, xoá đúng vị trí đó trên bản che
    for comma in LIST_COMMA.finditer(answer):
        listed[comma.start(2):comma.end(2)] = " " * (comma.end(2) - comma.start(2))
    listed = "".join(listed)
    list_start = max((m.end() for m in CLAUSE_END_NOT_COLON.finditer(listed, 0, match.start())), default=0)
    list_clause = answer[list_start:clause_stop.start() if clause_stop else len(answer)]
    list_mentions = [code.upper() for code in EMPLOYEE_CODE.findall(list_clause)]
    # mã nhắc lại trong vế ("Trong NV02, …, NV11, NV02 nhận 5.200.000 đ" — C lách 4c) ⇒ không phải danh sách nhóm, không mở
    if list_start < clause_start and not DISTRIBUTIVE.search(list_clause) and len(list_mentions) == len(set(list_mentions)) \
            and set(code.upper() for code in _listed_codes(list_clause)) == named \
            and any(codes == named and sourced(values) for codes, values in sources["groups"]):
        return True
    compare = list(COMPARE_WORDS.finditer(answer, sentence_from, match.start()))
    if compare:
        # Chiều so (C lách 5a: "NV07 hơn NV03 400.000 đ" khi NV03 cao hơn): mã ngay trước chữ so sánh là người CAO hơn ("hơn", "cao hơn", "chênh"
        # không chiều thì không xét), hoặc THẤP hơn ("kém", "thấp hơn", "ít hơn")
        subject = list(EMPLOYEE_CODE.finditer(answer, sentence_from, compare[-1].start()))
        lower = LOWER_WORDS.search(answer, max(sentence_from, compare[-1].start() - 6), compare[-1].end()) is not None
        for codes, values in sources["differences"]:
            if not codes <= named:
                continue
            for value, higher in values.items():
                if not nc._sourced(options, multiplier, False, {value}, set(), True):
                    continue
                if compare[-1].group(0).lower() == "chênh" and not lower:
                    return True
                if subject and (subject[-1].group(0).upper() == higher) != lower:
                    return True
    # Số cấp đội ( 13:56 (3)): cụm "cả đội / tổng đội …" đứng NGAY trước số (≤ 4 chữ chen giữa: "Tổng cả đội: 126.660.000 đ", "tổng đội tháng
    # này là …") trong vế không có mã NV — "NV05 được cả đội khen và nhận 55.700.000 đ" ⇒ chặn
    return free_clause and TEAM_NEAR_NUMBER.search(answer[clause_start:match.start()]) is not None and sourced(sources["team"])


def tier_lists_by_employee(tool_results: list) -> dict[str, list[list[tuple[Decimal, Decimal]]]]:
    """{mã NV: [danh sách mốc của MỘT KPI = [(ngưỡng `min`, tiền `amount`), …], …]} — ô `tiers` của `explain_kpi` (dict có `employee_id` của người được
    tra; mốc đúng nhánh luật của người đó, vd LPPC theo miền)."""
    found: dict[str, list[list[tuple[Decimal, Decimal]]]] = {}

    def walk(value):
        if isinstance(value, dict):
            tiers = value.get("tiers")
            if isinstance(value.get("employee_id"), str) and isinstance(tiers, list):
                pairs = [(Decimal(str(tier["min"])), Decimal(str(tier["amount"]))) for tier in tiers
                         if isinstance(tier, dict) and all(isinstance(tier.get(key), (int, float)) and not isinstance(tier.get(key), bool)
                                                           for key in ("min", "amount"))]
                if pairs:
                    found.setdefault(value["employee_id"].upper(), []).append(pairs)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for result in tool_results:
        walk(result)
    return found


# Dòng nói người đó ĐANG CÓ / ĐƯỢC NHẬN tiền ⇒ không phải dòng mô tả mốc dù có đủ cặp ngưỡng–tiền ("- Đạt 95%: NV05 được 3.920.000 đ")
SAYS_MONEY_NOW = re.compile(r"\b(?:hiện|đang|đã|được|nhận)\b", re.IGNORECASE)


def _tier_listing_numbers(answer: str, position: int, tier_lists: list[list[tuple[Decimal, Decimal]]]) -> set[Decimal]:
    """Các số (ngưỡng + tiền) của DÒNG chứa vị trí được coi là "dòng mô tả mốc" — kiem_so@18 (13/11).

    Vì sao: câu hỏi "KPI này có những mốc nào, mỗi mốc bao nhiêu tiền" — mô hình liệt kê đúng ô `tiers` dưới tiêu đề người được tra
    ("NV05 – KPI Turnover" ⏎ "- Đạt 95%: 3.920.000 đ" ⏎ "- Đạt 100%: 5.600.000 đ") ⇒ luật "tiền sau mã NV phải là tiền của chính người đó" + luật
    "đạt X % phải bằng % đạt" chặn câu ĐÚNG; mô hình viết lại và BỎ mốc 95 % (người kiểm độc lập đo 13/11: đủ hai mốc 6 / 8 ⇒ 1 / 8 lượt).
    So bằng DỮ LIỆU công cụ, không dò chữ tự do. Một dòng là dòng mô tả mốc khi đủ BA điều:
    1. dòng có đúng CẶP (ngưỡng, tiền) của CÙNG một mốc trong `tiers` của người đó — nhãn ghép sai tiền ("Đạt 95%: 5.600.000 đ") không tính;
    2. cùng khối (các dòng liền nhau, không cách dòng trống) có ≥ 2 mốc KHÁC NHAU của cùng danh sách mốc được ghép đúng như thế — MỘT cặp lẻ
       ("NV05 – Turnover: Đạt 95%: 3.920.000 đ") đọc lên như "NV05 đạt mốc 95 %, được 3.920.000 đ" ⇒ giữ luật cũ (KPI chỉ có một mốc: phải có chữ
       "mốc" như cơ chế sẵn có);
    3. dòng không có chữ khẳng định tiền hiện có ("được", "nhận", "đã", "đang", "hiện").
    Trả tập rỗng nếu dòng không phải dòng mô tả mốc."""
    lines = answer.split("\n")
    at = answer.count("\n", 0, position)
    first, last = at, at
    while first > 0 and lines[first - 1].strip():
        first -= 1
    while last + 1 < len(lines) and lines[last + 1].strip():
        last += 1

    def numbers(line: str) -> set[Decimal]:
        return {value for match in nc.NUMBER.finditer(line) for value, _ in nc.readings(match.group(1))}

    def paired(line: str, tiers: list[tuple[Decimal, Decimal]]) -> list[tuple[Decimal, Decimal]]:
        values = numbers(line)
        return [] if SAYS_MONEY_NOW.search(line) else [tier for tier in tiers if tier[0] in values and tier[1] in values]

    for tiers in tier_lists:
        here = paired(lines[at], tiers)
        if here and len({tier for line in lines[first:last + 1] for tier in paired(line, tiers)}) >= 2:
            return {number for tier in here for number in tier}
    return set()

# ---------- kiem_so@22 (13/11): lớp "đúng KHOẢN" — số tiền đứng cạnh tên một khoản phải bằng ô của ĐÚNG khoản đó của đúng người ----------
# Lỗ có từ trước (C tìm 10:04, lại 14:21 trên @21, RAW 31/10): NV04 thưởng vượt 0, LPPC 2.000.000 ⇒ "NV04 được thưởng vượt 2.000.000 đ" QUA
# luật đúng người (2.000.000 đúng là tiền của NV04 — ở khoản khác). Cả 14 người có một ô 2.000.000 đ ⇒ dễ xảy ra.
# Danh mục khoản (thứ tự: tên dài trước tên ngắn — "LPPC_Meat" trước "LPPC"). "1 % gia vị" CỐ Ý KHÔNG vào: đó là tiền trả KHÁCH, chỉ câu chuẩn chép
# nguyên mới qua (D 14:2x). "Thưởng thêm" = tổng HOẶC tiền một dòng thưởng thêm (9 khoản lẻ không có tên riêng ở đây
# giới hạn: số của khoản lẻ này gán cho khoản lẻ khác vẫn qua).
ITEM_NAMES = [(re.compile(r"\bLPPC_Meat\b", re.IGNORECASE), "LPPC_Meat"), (re.compile(r"\bLPPC\b(?!_)", re.IGNORECASE), "LPPC"),
              (re.compile(r"\bASO_Focus_2\b", re.IGNORECASE), "ASO_Focus_2"), (re.compile(r"\bASO_Focus\b(?!_)", re.IGNORECASE), "ASO_Focus"),
              (re.compile(r"\bASO_ALL\b", re.IGNORECASE), "ASO_ALL"), (re.compile(r"\bPC_BT\b", re.IGNORECASE), "PC_BT"),
              (re.compile(r"\bPC_ON\b", re.IGNORECASE), "PC_ON"), (re.compile(r"\bTurnover\b", re.IGNORECASE), "Turnover"),
              (re.compile(r"\blương\s+kinh\s+doanh\b", re.IGNORECASE), "business_salary"),
              # kiem_so@30: "8 KPI" đứng trần cũng là tên khoản lương kinh doanh — "NV07: 8 KPI K đ và thưởng thêm E đ" từng bị đọc K là thưởng thêm
              (re.compile(r"\b8\s*KPI\b", re.IGNORECASE), "business_salary"),
              (re.compile(r"\bthưởng\s+vượt\b", re.IGNORECASE), "over_target_bonus"),
              (re.compile(r"\bthưởng\s+thêm\b", re.IGNORECASE), "extra_bonus")]
KPI_ITEMS = {"Turnover", "PC_BT", "LPPC", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat"}
EXTRA_BONUS_NAME = re.compile(r"\bthưởng\s+thêm\b", re.IGNORECASE)
# Số ĐO (chỉ tiêu / thực hiện / doanh số) viết kèm "đ" không phải tiền thưởng của khoản: "Turnover: chỉ tiêu 1.300.000.000 đ, thực hiện 1.310.000.000 đ,
# tiền 5.600.000 đ" — chữ đo nằm trong vế của số, hoặc ở tiêu đề cột của bảng ⇒ lớp khoản không xét số đó
MEASURE_WORDS = re.compile(r"\b(?:chỉ\s+tiêu|thực\s+hiện|doanh\s+số|doanh\s+thu|mục\s+tiêu)\b", re.IGNORECASE)
# Tên khoản SÁT sau số ("2.200.000 đ PC_BT", "2.000.000 đồng tiền Turnover", "800.000 đ) cho ASO_Focus") thắng tên đứng trước
ITEM_GLUE_AFTER = re.compile(r"\s*(?:đồng|đ|vnđ|vnd)?[\s*_)]*(?:(?:tiền|khoản|ở|cho|của|KPI)\s+)*", re.IGNORECASE)
# Luật kiem_so@30 nhận cả tên trong ngoặc sát sau số ("K đ (lương kinh doanh)"); lớp khoản thì không — "800.000 đ (PC_BT 400.000 đ, …)" là số tổng
REPEATED_GLUE_AFTER = re.compile(r"\s*(?:đồng|đ|vnđ|vnd)?[\s*_()]*(?:(?:là|thuộc|từ|tiền|khoản|ở|cho|của|KPI)\s+)*", re.IGNORECASE)
# Vòng 2 (20/11): tên khoản đứng sau lời LOẠI TRỪ ("không tính thưởng thêm, NV07 nhận K đ") không phải tên của số — câu đang nói về phía CÒN LẠI.
# Tên đứng SÁT số ("chưa cộng thưởng thêm E đ") thì vẫn là tên của chính số đó.
EXCLUDED_BEFORE = re.compile(r"\b(?:(?:không|chưa)\s+(?:tính|gồm|bao\s+gồm|kể|cộng|có)|trừ|ngoài|bỏ)(?:\s+(?:cả|tới|đến|đi|ra|phần|khoản|tiền))*\s*$",
                             re.IGNORECASE)
# Vòng 3 (M2 người kiểm): lời loại trừ BỊ PHỦ ĐỊNH ("không bỏ / chưa trừ thưởng thêm", "không phải là chưa tính …") nghĩa là CÓ gồm khoản đó
NEGATED_EXCLUSION = re.compile(r"(?:\b(?:không|chưa|chẳng|chả|đừng|khỏi)|\bđâu\s+(?:có|phải))(?:\s+(?:hề|phải|là|cần|được|có))*\s*$", re.IGNORECASE)
NAME_RIGHT_BEFORE_NUMBER = re.compile(r"[\s:*_(]*(?:(?:là|=)\s*)?")
OTHER_SIDE = {"extra_bonus": "business_salary", "business_salary": "extra_bonus"}
IN_TURN = re.compile(r"\blần\s+lượt\b", re.IGNORECASE)   # "8 KPI và thưởng thêm lần lượt là K đ và E đ": nhãn hai tên nhưng mỗi số một khoản
# "tổng" trần ("tổng là 7.730.000 đ", "tổng cộng", "tổng thưởng") = cộng nhiều khoản; trừ tên khoản "tổng lương kinh doanh"
# kiem_so@24: thêm "tổng thưởng thêm" (ô `extra_bonus_total`) — trước đó "Tổng thưởng thêm của NV03 là <số tổng 8 KPI + thưởng thêm>" LỌT vì chữ "tổng"
# làm lớp khoản bỏ qua
SUM_WORDS = re.compile(r"\btổng\b(?!\s+lương\s+kinh\s+doanh|\s+thưởng\s+thêm)|\bcộng\s+(?:lại|chung|cả)\b", re.IGNORECASE)
ITEM_SENTENCE_END = re.compile(r"[;!?]|\.(?!\d)")
# Số có trong câu hỏi chỉ được miễn khi PHỦ ĐỊNH đứng SÁT trước số: "không / chưa / chẳng / chả" + tối đa 2 chữ "được / có / nhận / phải" rồi tới số
# ("NV04 không được 2.000.000 đ PC_BT", "chưa nhận được 2.000.000 đ"). Bản trước (83ff97cf) xét cả câu + chữ "hỏi" ⇒ chữ ở XA số vẫn mở miễn: "Không sai,
# NV04 được 2.000.000 đ PC_BT." / "Có. Hỏi đúng rồi, …" / "Như bạn hỏi, …" QUA (15:15 13/11, ca K4c–K4e) ⇒ bỏ "hỏi", buộc phủ định sát số.
# Dấu Markdown (`**` / `_`) giữa các chữ được bỏ qua: mô hình hay viết "NV04 **không** được …" / "không được **2.000.000 đ**" ( 58cdff61, 16/11:
# hai câu đúng đó bị chặn oan vì bản trước chỉ nhận khoảng trắng).
NEGATION_RIGHT_BEFORE = re.compile(r"\b(?:không|chưa|chẳng|chả)(?:[\s*_]+(?:được|có|nhận|phải)){0,2}[\s*_]+$", re.IGNORECASE)

# ---------- kiem_so@24 (18/11): khoản "TỔNG 8 KPI + THƯỞNG THÊM" có ô thật `total_8_kpi_and_extra` (công cụ get_extra_bonus) ----------
# Lỗi thật (Capitol, phiếu NV03, 2 lượt 18/11): câu đúng "Tổng thưởng thêm: 990.000 đ. Tổng 8 KPI cộng thưởng thêm: 9.930.000 đ" bị chặn "9.930.000 không phải
# tiền thưởng thêm" — chữ số "8" của "8 KPI" bị coi là con số đứng trước nên chữ "tổng" nằm ngoài đoạn xét, "8 KPI" không phải tên khoản ⇒ còn một tên
# "thưởng thêm". Sửa bằng cách SO với ô tổng (không nới thành "không xác định"): số là số tổng khi NHÃN của chính số đó (từ con số tiền đứng trước trên dòng
# tới số — chữ số của "8 KPI" không tính là con số) có chữ cộng VÀ nhắc đủ hai phía, không có khoản thứ ba; hoặc số nối bằng dấu "=" với một phép cộng hai
# phía ("A + thưởng thêm B = T", "T = A lương kinh doanh + B thưởng thêm"). Quyết theo TỪNG số: thành phần A, B trên cùng dòng vẫn theo luật khoản cũ.
TOTAL_ITEM = "total_8_kpi_and_extra"
SALARY_SIDE = re.compile(r"\b8\s*KPI\b|\blương\s+kinh\s+doanh\b", re.IGNORECASE)
THIRD_SIDE = re.compile(r"\bgia\s+vị\b|\bhội\s+viên\b", re.IGNORECASE)      # khoản ngoài hai phía mà không có trong ITEM_NAMES
TOTAL_MARK = re.compile(r"\btổng\b|\bcộng\b|\+|\btính\s+cả\b|\bgộp\b", re.IGNORECASE)
EACH_IN_TURN = re.compile(r"\blần\s+lượt\b", re.IGNORECASE)                   # "… và thưởng thêm lần lượt là A và B" — liệt kê, không phải tổng
EQUALS_BEFORE = re.compile(r"=[\s*_]*$")
# kiem_so@29 (19/11, ca Capitol + người kiểm độc lập, làm lại sau vòng kiểm 1): số tiền đứng sau một NHÃN KIỂU TỔNG mà nhãn KHÔNG nêu tên khoản nào
# ("- Tổng cộng: …", "Cộng hai phần: …", "Tổng cả hai khoản: …", "Tổng gộp: …", hàng bảng "| Tổng cộng | … |", câu riêng "… Tổng gộp: …"), khi phần
# câu trả lời đứng TRƯỚC nó đã nêu TIỀN của cả hai phía (8 KPI và thưởng thêm) ⇒ số đó phải là ô tổng gộp. Trước @29 chữ "tổng cộng" làm lớp khoản coi
# số là "không xác định" ⇒ ghi nhầm tổng 8 KPI (hoặc tổng thưởng thêm) vào dòng tổng gộp vẫn QUA — sai tiền người đọc thấy. Bản đầu của @29 liệt kê
# từng nhãn và đòi tiền đứng sát tên phía đó ⇒ lọt nhãn lạ + dòng tổng viết thành câu riêng; nay ĐẢO CHIỀU: mọi nhãn có chữ tổng / cộng / gộp / cả hai
# / tất cả / thực nhận đều tính, chỉ cho qua khi nhãn NÊU RÕ khoản ("tổng 8 KPI", "tổng thưởng thêm", "Thưởng 03").
# Cả chữ "tổng" đứng MỘT MÌNH ("- Tổng: …", "…, tổng … đ") cũng tính — người kiểm đo: "- Tổng: <số 8 KPI>" sau hai dòng 8 KPI + thưởng thêm lọt 2 / 2.
# Giới hạn (sai về phía chặn): tổng RIÊNG của một nhóm dòng mà nhãn không nêu tên khoản, đứng sau khi đã có tiền của cả hai phía, bị đòi bằng tổng gộp.
SUM_LABEL = re.compile(r"\btổng\b|\bcộng\b|\bgộp\b|\bcả\s+hai\b|\btất\s+cả\b|\bthực\s+(?:nhận|lĩnh|lãnh)\b", re.IGNORECASE)
MONEY_FIGURE = re.compile(r"\d{1,3}(?:\.\d{3})+")


def _both_parts_have_money(text: str) -> bool:
    """Đoạn chữ đã nêu TIỀN của cả hai phía: có một số tiền mà chữ đứng trước nó (kể từ số tiền liền trước) nhắc 8 KPI / lương kinh doanh, và một số
    tiền khác mà chữ đứng trước nhắc thưởng thêm. Chữ nhắc CẢ HAI phía trước một số ("tổng 8 KPI và thưởng thêm: T") không tính cho phía nào."""
    salary = extra = False
    label_from = 0
    for figure in MONEY_FIGURE.finditer(text):
        label, label_from = text[label_from:figure.start()], figure.end()
        names_salary, names_extra = SALARY_SIDE.search(label) is not None, EXTRA_BONUS_NAME.search(label) is not None
        salary, extra = salary or (names_salary and not names_extra), extra or (names_extra and not names_salary)
    return salary and extra


def _unnamed_total_label(label: str) -> bool:
    """Nhãn kiểu tổng không nêu tên khoản nào (xem chú thích kiem_so@29)."""
    return bool(SUM_LABEL.search(label)) and not EACH_IN_TURN.search(label) and not SALARY_SIDE.search(label) and not EXTRA_BONUS_NAME.search(label) \
        and not THIRD_SIDE.search(label) and not NUMBERED_ITEM.search(label) and not any(pattern.search(label) for pattern, _ in ITEM_NAMES)


EQUALS_AFTER = re.compile(r"\s*(?:đồng|đ|vnđ|vnd)?[\s*_]*=", re.IGNORECASE)


def _names_both_sides_only(answer: str, text_from: int, text_to: int) -> bool:
    """Đoạn chữ nhắc ĐỦ hai phía (8 KPI / lương kinh doanh · thưởng thêm) và không nhắc khoản nào khác."""
    text = answer[text_from:text_to]
    if not SALARY_SIDE.search(text) or not EXTRA_BONUS_NAME.search(text) or THIRD_SIDE.search(text):
        return False
    return not any(pattern.search(text) for pattern, item in ITEM_NAMES if item not in ("business_salary", "extra_bonus"))


def _is_total_of_both(answer: str, masked: str, line_start: int, start: int, end: int, line_end: int) -> bool:
    """Số ở [start, end) được câu trả lời gọi là TỔNG 8 KPI + thưởng thêm — xem chú thích kiem_so@24 ở trên."""
    money_before = [m.end() for m in nc.NUMBER.finditer(masked, line_start, start) if not KPI_COUNT_AFTER.match(masked, m.end())]
    label_from = money_before[-1] if money_before else line_start
    label = answer[label_from:start]
    if TOTAL_MARK.search(label) and not EACH_IN_TURN.search(label) and _names_both_sides_only(answer, label_from, start):
        return True
    if _unnamed_total_label(label) and not THIRD_SIDE.search(answer, 0, label_from) \
            and _both_parts_have_money(answer[:label_from]):
        return True                                                   # "- 8 KPI: K\n- Thưởng thêm: E\n- Tổng cộng: T"
    sentence_from = max((m.end() for m in ITEM_SENTENCE_END.finditer(masked, line_start, start)), default=line_start)
    if EQUALS_BEFORE.search(label) and "+" in answer[sentence_from:start] and _names_both_sides_only(answer, sentence_from, start):
        return True                                                   # "A + thưởng thêm B = T"
    equals = EQUALS_AFTER.match(answer, end, line_end)
    if equals:
        stop = ITEM_SENTENCE_END.search(masked, equals.end(), line_end)
        sum_to = stop.start() if stop else line_end
        return "+" in answer[equals.end():sum_to] and _names_both_sides_only(answer, equals.end(), sum_to)   # "T = A lương kinh doanh + B thưởng thêm"
    return False
# ---------- kiem_so@25 (18/11): ba lỗ có từ trước người kiểm tìm khi dò @24 — câu SAI mà @23 lẫn @24 cho qua ----------
# (a) BẢNG: "| NV | 8 KPI | Thưởng thêm | Tổng |" + dòng "| NV03 | K | T | T |" qua vì lớp khoản chỉ đọc tên khoản trên DÒNG của số ⇒ nay đọc ô TIÊU ĐỀ
#     của đúng cột trước; ô tiêu đề không nói khoản nào ("Tiền", "Khoản") ⇒ về luật theo dòng như cũ.
# (b) CẤP ĐỘI: "Tổng 8 KPI và thưởng thêm của cả đội là <tổng của một người>" qua vì câu không có mã NV ⇒ không lớp nào so ⇒ nay so với ô
#     `team_total_8_kpi_and_extra`; lượt không có ô đó ⇒ chặn (câu nói về cả đội thì phải có kết quả cấp đội).
# (c) "TỔNG" TRẦN: "NV03 tổng <tiền một KPI>" qua vì chữ "tổng" làm lớp khoản bỏ qua ⇒ nay số đứng sau "tổng" trần (câu không nêu khoản nào) mà là tiền
#     MỘT khoản lẻ của người đó — và không trùng ô tổng nào của họ — thì chặn. Số là ô tổng thật (lương kinh doanh 8 KPI, tổng thưởng thêm, tổng hai phía)
#     hoặc tổng do công cụ tính trả vẫn qua như cũ.
TEAM_TOTAL_KEY = "team_total_8_kpi_and_extra"
BARE_TOTAL = re.compile(r"\btổng\b", re.IGNORECASE)
NUMBERED_ITEM = re.compile(r"\b(?:thưởng|khoản|KPI|mục)\s*\d", re.IGNORECASE)   # "Thưởng 03", "khoản 04", "KPI 5" — câu đang nói về một khoản có số hiệu


def _header_cell_item(answer: str, line_start: int, start: int) -> str | None:
    """Khoản mà TIÊU ĐỀ CỘT của ô chứa số nói tới (bảng Markdown); không phải bảng / tiêu đề không nêu đúng một khoản ⇒ None."""
    if not answer[line_start:start].lstrip().startswith("|"):
        return None
    above = _table_above(answer, start)
    if not above:
        return None
    cells = above.split("\n")[-1].split("|")            # dòng trên cùng của bảng = tiêu đề
    column = answer[line_start:start].count("|")
    if column >= len(cells):
        return None
    cell = cells[column]
    if SALARY_SIDE.search(cell) and EXTRA_BONUS_NAME.search(cell):
        return TOTAL_ITEM if _names_both_sides_only(cell, 0, len(cell)) else None
    found = {item for pattern, item in ITEM_NAMES if pattern.search(cell)}
    if "LPPC_Meat" in found:
        found.discard("LPPC")                            # "LPPC_Meat" không phải "LPPC"
    if "ASO_Focus_2" in found:
        found.discard("ASO_Focus")
    if len(found) == 1:
        return found.pop()
    return "business_salary" if not found and SALARY_SIDE.search(cell) else None


def team_total_cells(tool_results: list) -> set[Decimal]:
    """Ô tổng 8 KPI + thưởng thêm CỦA CẢ ĐỘI trong kết quả công cụ của lượt (get_extra_bonus không mã NV)."""
    found: set[Decimal] = set()

    def walk(value):
        if isinstance(value, dict):
            total = value.get(TEAM_TOTAL_KEY)
            if isinstance(total, (int, float)) and not isinstance(total, bool):
                found.add(Decimal(str(total)))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for result in tool_results:
        walk(result)
    return found


def _wrong_team_total(answer: str, masked: str, match, options, multiplier, team_totals: set[Decimal]) -> str:
    """Số KHÔNG có mã NV, được gọi là tổng 8 KPI + thưởng thêm trong câu nói về CẢ ĐỘI ⇒ phải bằng ô tổng đội; "" = không phải loại câu này / đúng."""
    line_start = answer.rfind("\n", 0, match.start()) + 1
    line_end = answer.find("\n", match.end())
    line_end = len(answer) if line_end == -1 else line_end
    if not _is_total_of_both(answer, masked, line_start, match.start(), match.end(), line_end):
        return ""
    sentence_from, sentence_to = _sentence_span(masked, match.start())
    if not TEAM_WORDS.search(answer, sentence_from, sentence_to):
        return ""
    if nc._sourced(options, multiplier, False, team_totals, set(), True):
        return ""
    return (f"số {match.group(0).strip()} không phải tổng 8 KPI + thưởng thêm của cả đội trong công cụ"
            + ("" if team_totals else " (lượt này không có kết quả cấp đội của công cụ thưởng thêm)"))


def _wrong_unowned_total(answer: str, masked: str, match, options, multiplier, cells: dict) -> str:
    """Số KHÔNG có mã NV nào làm chủ (bảng / câu không nêu mã — người được hỏi chỉ có trong câu hỏi) mà câu trả lời gọi là tổng 8 KPI + thưởng thêm ⇒
    phải bằng ô tổng gộp của MỘT người có trong kết quả công cụ của lượt (kiem_so@29: hàng bảng "| Tổng cộng | <tổng 8 KPI> |" từng qua vì số không
    chủ bị bỏ qua hẳn). Câu về cả đội do `_wrong_team_total` xét; lượt không có ô tổng gộp của ai ⇒ không xác định."""
    line_start = answer.rfind("\n", 0, match.start()) + 1
    line_end = answer.find("\n", match.end())
    line_end = len(answer) if line_end == -1 else line_end
    if not _is_total_of_both(answer, masked, line_start, match.start(), match.end(), line_end):
        return ""
    sentence_from, sentence_to = _sentence_span(masked, match.start())
    totals = set().union(*(mine.get(TOTAL_ITEM, set()) for mine in cells.values()), set())
    if TEAM_WORDS.search(answer, sentence_from, sentence_to) or not totals or nc._sourced(options, multiplier, False, totals, set(), True):
        return ""
    return f"số {match.group(0).strip()} đứng ở dòng tổng 8 KPI + thưởng thêm nhưng không phải ô tổng đó của ai trong kết quả công cụ"


def _component_called_total(answer: str, masked: str, match, options, multiplier, owners: list[str], cells: dict, every: bool) -> str:
    """Số đứng sau chữ "tổng" TRẦN (câu không nêu khoản nào) mà là tiền một khoản LẺ của chủ số, không trùng ô tổng nào của họ ⇒ lời lỗi."""
    line_start = answer.rfind("\n", 0, match.start()) + 1
    money_before = [m.end() for m in nc.NUMBER.finditer(masked, line_start, match.start()) if not KPI_COUNT_AFTER.match(masked, m.end())]
    label_from = money_before[-1] if money_before else line_start
    if not BARE_TOTAL.search(answer, label_from, match.start()):
        return ""
    sentence_from, sentence_to = _sentence_span(masked, match.start())
    sentence = answer[sentence_from:sentence_to]
    if any(pattern.search(sentence) for pattern, _ in ITEM_NAMES) or SALARY_SIDE.search(sentence) or NUMBERED_ITEM.search(sentence) \
            or MEASURE_WORDS.search(sentence) or ITEM_SKIP_CLAUSE.search(sentence):
        return ""

    def is_component_only(code: str) -> bool:
        mine = cells.get(code, {})
        totals = mine.get("business_salary", set()) | mine.get(TOTAL_ITEM, set()) | mine.get("extra_bonus_total", set())
        parts = set().union(*(values for item, values in mine.items() if item in KPI_ITEMS or item in ("extra_bonus", "over_target_bonus")), set())
        return nc._sourced(options, multiplier, False, parts - totals, set(), True) and not nc._sourced(options, multiplier, False, totals, set(), True)

    wrong = [code for code in owners if is_component_only(code)]
    if not wrong or (not every and len(wrong) < len(owners)):
        return ""
    return (f"số {match.group(0).strip()} là tiền của MỘT khoản của {', '.join(wrong)}, không phải số tổng — sau chữ \"tổng\" phải là ô tổng "
            "(lương kinh doanh 8 KPI, tổng thưởng thêm, tổng 8 KPI + thưởng thêm) hoặc nêu rõ tên khoản")


def _negated_right_before(answer: str, number_start: int) -> bool:
    """Chữ ngay trước số (tới đầu dòng) kết thúc bằng phủ định sát số — xem `NEGATION_RIGHT_BEFORE`."""
    line_start = answer.rfind("\n", 0, number_start) + 1
    return NEGATION_RIGHT_BEFORE.search(answer, line_start, number_start) is not None
# Vế có chữ so sánh / chia đều / phủ định cả đội ("chênh lệch 0 đồng", "mỗi người 3.840.000 đồng", "không có ai 0 đồng") ⇒ số không phải ô của một khoản
ITEM_SKIP_CLAUSE = re.compile(r"\b(?:hơn|chênh|kém|mỗi|từng|đều|không\s+có\s+ai|không\s+ai|chưa\s+ai)\b", re.IGNORECASE)


def item_cells_by_employee(tool_results: list) -> dict[str, dict[str, set[Decimal]]]:
    """{mã NV: {khoản: các ô tiền THẬT của khoản đó}} từ kết quả công cụ: `kpis[].amount`, `kpi_amounts`, `explain_kpi` (kpi + amount),
    `business_salary(_8_kpi)`, `over_target_bonus`, Thưởng thêm `extra_bonus_total` + `lines[].amount`, tổng `total_8_kpi_and_extra`. Khoản không có ô ⇒ không có khoá (= không xác định)."""
    found: dict[str, dict[str, set[Decimal]]] = {}

    def add(code: str, item: str, value) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found.setdefault(code, {}).setdefault(item, set()).add(Decimal(str(value)))

    def walk(value):
        if isinstance(value, dict):
            code = value.get("employee_id")
            if isinstance(code, str) and EMPLOYEE_CODE.fullmatch(code.strip()):
                code = code.strip().upper()
                for row in value.get("kpis") or []:
                    if isinstance(row, dict) and row.get("kpi") in KPI_ITEMS:
                        add(code, row["kpi"], row.get("amount"))
                for kpi, amount in (value.get("kpi_amounts") or {}).items() if isinstance(value.get("kpi_amounts"), dict) else ():
                    if kpi in KPI_ITEMS:
                        add(code, kpi, amount)
                if value.get("kpi") in KPI_ITEMS and "tiers" in value:
                    add(code, value["kpi"], value.get("amount"))
                for key in ("business_salary", "business_salary_8_kpi"):
                    add(code, "business_salary", value.get(key))
                add(code, "over_target_bonus", value.get("over_target_bonus"))
                add(code, "extra_bonus", value.get("extra_bonus_total"))
                add(code, "extra_bonus_total", value.get("extra_bonus_total"))   # riêng ô TỔNG thưởng thêm — luật "tổng trần" (kiem_so@25) cần phân biệt với dòng lẻ
                add(code, TOTAL_ITEM, value.get("total_8_kpi_and_extra"))
                for line in value.get("lines") or []:
                    if isinstance(line, dict) and str(line.get("employee_id", code)).upper() == code:
                        add(code, "extra_bonus", line.get("amount"))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for result in tool_results:
        walk(result)
    return found


def kpi_tiers_by_employee(tool_results: list) -> dict[tuple[str, str], set[Decimal]]:
    """{(mã NV, KPI): tiền các mốc} từ `explain_kpi` (dict có employee_id + kpi + tiers — đúng nhánh luật của người đó)."""
    found: dict[tuple[str, str], set[Decimal]] = {}

    def walk(value):
        if isinstance(value, dict):
            code, kpi, tiers = value.get("employee_id"), value.get("kpi"), value.get("tiers")
            if isinstance(code, str) and kpi in KPI_ITEMS and isinstance(tiers, list):
                found.setdefault((code.upper(), kpi), set()).update(
                    Decimal(str(tier["amount"])) for tier in tiers
                    if isinstance(tier, dict) and isinstance(tier.get("amount"), (int, float)) and not isinstance(tier.get("amount"), bool))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for result in tool_results:
        walk(result)
    return found


def _item_of_number(answer: str, masked: str, start: int, end: int) -> str | None:
    """Khoản mà số tiền ở [start, end) nói về, xét trong DÒNG của số (D 14:2x — ghép TỪNG số, không đòi "một khoản / dòng"):
    tên khoản đứng ngay sau số cùng vế ⇒ tên gần nhất đứng trước trên dòng ⇒ tên đầu tiên đứng sau trong cùng vế. Không có ⇒ None (luật cũ).
    """
    line_start = answer.rfind("\n", 0, start) + 1
    line_end = answer.find("\n", end)
    line_end = len(answer) if line_end == -1 else line_end
    stop = CLAUSE_END_NOT_COLON.search(masked, end, line_end)
    clause_end = stop.start() if stop else line_end
    header_item = _header_cell_item(answer, line_start, start)
    if header_item:
        return header_item
    if _is_total_of_both(answer, masked, line_start, start, end, line_end):
        return TOTAL_ITEM

    def mentions(text_from: int, text_to: int) -> list[tuple[int, str]]:
        spans = []
        for pattern, item in ITEM_NAMES:
            for found in pattern.finditer(answer, text_from, text_to):
                if not any(s <= found.start() < e for s, e, _ in spans):   # "LPPC_Meat" đã nhận thì "LPPC" trong đó bỏ
                    spans.append((found.start(), found.end(), item))
        return sorted((s, item) for s, _, item in spans)
    # (1) Tên đứng SÁT sau số, chỉ cách bởi đơn vị / ngoặc / chữ nối "tiền, khoản, ở, cho, của" ("2.200.000 đ PC_BT", "2.000.000 đ tiền Turnover").
    # Phát lại 2.038 chữ (vòng 1): cho phép mọi chữ trong 30 ký tự ⇒ "800.000 đ VÀ LPPC_Meat 400.000 đ" ghép nhầm tên của số kế tiếp ⇒ 14 câu đúng bị chặn
    glued = ITEM_GLUE_AFTER.match(answer, end, clause_end)
    right_after = [(s, item) for s, item in mentions(glued.end(), clause_end) if s == glued.end()] if glued else []
    if right_after:
        return right_after[0][1]
    # (2) Tên đứng trước số, kể từ con số đứng trước trên dòng (không vượt qua số khác — tên đó là của số kia). Có ≥ 2 tên khác nhau ("Tổng lương kinh
    # doanh VÀ thưởng thêm … là 7.730.000 đ") hoặc chữ cộng ("tổng cộng 7.730.000 đ") ⇒ số là tổng nhiều khoản ⇒ không xác định
    # chữ số của "8 KPI" không phải con số đứng trước (nó cắt mất chính tên khoản "8 KPI" — như `_is_total_of_both`)
    previous = [m.end() for m in nc.NUMBER.finditer(masked, line_start, start) if not KPI_COUNT_AFTER.match(masked, m.end())]
    segment_from = previous[-1] if previous else line_start
    if SUM_WORDS.search(answer, segment_from, start):
        return None
    between = {item for _, item in mentions(segment_from, start)}
    if len(between) > 1:
        return None
    if between:
        return between.pop()
    # (3) Không có tên từ số trước tới số này ⇒ tên gần nhất đứng trước trong cùng CÂU (cắt ở . ; ! ?) — "lương kinh doanh của NV03 là 4.000.000 đ, của
    # NV07 là 3.600.000 đ"; rồi tên đầu tiên đứng sau trong cùng vế ("2.000.000 đ là tiền PC_BT của NV04")
    sentence_from = max((m.end() for m in ITEM_SENTENCE_END.finditer(masked, line_start, start)), default=line_start)
    before = mentions(sentence_from, start)
    if before:
        return before[-1][1]
    after = mentions(end, clause_end)
    return after[0][1] if after else None


def _wrong_item(answer: str, masked: str, match, options, multiplier, owners: list[str], sources: dict, exempt: set, condition: bool,
                every: bool) -> str:
    """"" = đúng khoản hoặc không xác định được; lời lỗi = số đứng cạnh tên khoản không bằng ô của khoản đó. Nhiều chủ số: như luật đúng người —
    `every` (không chắc chủ) ⇒ đúng với MỌI người, không ⇒ đúng với một người là đủ. `condition` = câu mốc / điều kiện theo ĐÚNG cách `_money_passes`
    nhận ra (một luật, không đoán lại)."""
    item = _item_of_number(answer, masked, match.start(), match.end())
    if item is None:
        return ""
    # Số có trong câu hỏi chỉ được miễn khi câu chứa nó PHỦ ĐỊNH / nhắc lại câu hỏi (A vòng sửa sau 14:58): "Không, NV04 không được 2.000.000 đ
    # PC_BT" qua; câu KHẲNG ĐỊNH "Có, NV04 được 2.000.000 đ PC_BT" (hỏi "… 2.000.000 đ PC_BT không?") vẫn xét
    if nc._sourced(options, multiplier, False, exempt, set(), True) and _negated_right_before(answer, match.start()):
        return ""
    clause_start = max((m.end() for m in CLAUSE_END_NOT_COLON.finditer(masked, 0, match.start())), default=0)
    clause_stop = CLAUSE_END_NOT_COLON.search(masked, match.end())
    line_start = answer.rfind("\n", 0, match.start()) + 1
    in_table = answer[line_start:match.start()].lstrip().startswith("|")
    # Giới hạn (ghi rõ): bảng có cột đo (Chỉ tiêu / Thực hiện) ở tiêu đề ⇒ cả cột tiền của bảng đó cũng không xét khoản (chưa đọc theo cột)
    if MEASURE_WORDS.search(answer, clause_start, match.start()) or (in_table and MEASURE_WORDS.search(_table_above(answer, match.start()))) \
            or ITEM_SKIP_CLAUSE.search(answer, clause_start, clause_stop.start() if clause_stop else len(answer)):
        return ""
    wrong = []
    for code in owners:
        cells = sources["cells"].get(code, {}).get(item)
        if condition:
            tiers = sources["tiers"].get((code, item)) if item in KPI_ITEMS else None
            if tiers is None:
                continue   # câu mốc / điều kiện mà lượt không có bảng mốc của đúng KPI đó ⇒ không xác định (luật cũ)
            allowed = tiers | (cells or set())
        elif cells is None:
            continue       # lượt không có ô của khoản này cho người này ⇒ không xác định (luật cũ)
        else:
            allowed = cells
        if nc._sourced(options, multiplier, False, allowed, set(), True):
            if not every:
                return ""          # đúng khoản của một trong các chủ số là đủ (như luật đúng người khi chủ chắc)
        else:
            wrong.append(code)
    if not wrong:
        return ""
    label = {"business_salary": "lương kinh doanh", "over_target_bonus": "thưởng vượt", "extra_bonus": "thưởng thêm",
             TOTAL_ITEM: "tổng 8 KPI + thưởng thêm"}.get(item, item)
    return (f"số {match.group(0).strip()} không phải tiền {label} của {', '.join(wrong)} trong công cụ"
            + (" (không có trong bảng mốc của KPI đó)" if condition else "") + " — số tiền cạnh tên một khoản phải là tiền của ĐÚNG khoản đó")
# ---------- kiem_so@30 (19/11): MỘT KHOẢN LẺ nhắc lại dưới nhãn KHÔNG nêu tên khoản đó ----------
# Lỗ (người kiểm độc lập dò @29): câu trả lời đã nêu tiền 8 KPI (K) và thưởng thêm của một người, rồi "- Thành tiền: K đ", "⇒ Cả thảy NV07 nhận K đ.",
# "Số tiền nhận cuối cùng: K đ." QUA — @29 chỉ xét nhãn có chữ tổng / cộng / gộp, nhãn khác thì vô số cách nói. Nay xét theo SỐ, không theo chữ của nhãn:
# số bằng đúng K hoặc E của người đó, nhãn / vế của nó không nêu tên khoản có ô bằng số đó ⇒ chặn (người đọc hiểu như số tổng / một khoản khác).
# Cùng chỗ đó, số bằng ô tổng gộp mà nhãn không nêu khoản nào ⇒ là tổng, cho qua — không mượn tên khoản đứng trước trong câu
# ("8 KPI K đ, thưởng thêm E đ, được T đ" từng bị đọc T là thưởng thêm).
# Nhãn / vế của số = chữ từ con số đứng trước trên dòng (chữ số của "8 KPI" không tính) tới số + tên khoản đứng sát sau số ("K đ là lương kinh doanh").
# "Đã nêu tiền cả hai phía" = phía trước có một số bằng K mà nhãn / vế của nó nêu lương kinh doanh / 8 KPI, và một số bằng E mà nhãn / vế nêu thưởng thêm.
# "tháng 15.490.000 đ": chữ ngày / tháng / năm của number_check che mất số tiền đứng ngay sau (đọc như "tháng 10") ⇒ cả hai lớp không xét số đó.
# Số có nhóm nghìn không bao giờ là ngày / tháng / năm ⇒ lớp này mở lại.
DATE_WORD_MONEY = re.compile(r"\b(?:ngày|tháng|năm)\s+(\d{1,3}(?:\.\d{3})+)(?![\d.,]*\d)", re.IGNORECASE)


def _segment_items(answer: str, masked: str, start: int, end: int) -> set[str]:
    """Các khoản mà nhãn / vế của số ở [start, end) nêu tên (xem chú thích kiem_so@30); "Thưởng 03" tính là thưởng thêm, tiêu đề cột của ô bảng cũng tính."""
    line_start = answer.rfind("\n", 0, start) + 1
    line_end = answer.find("\n", end)
    line_end = len(answer) if line_end == -1 else line_end
    before = [m.end() for m in nc.NUMBER.finditer(masked, line_start, start) if not KPI_COUNT_AFTER.match(masked, m.end())]
    label_start = before[-1] if before else line_start
    if before:
        # Tên khoản dính SÁT SAU con số đứng trước ("K đ lương kinh doanh và E đ thưởng thêm") là tên của số đó, không thuộc nhãn của số này
        # ("K đ là lương kinh doanh 8 KPI, E đ …": hai cách gọi liền nhau của CÙNG một khoản đều thuộc số trước)
        first_item = None
        while True:
            glue_end = REPEATED_GLUE_AFTER.match(answer, label_start, start).end()
            names = [(name.end(), item) for pattern, item in ITEM_NAMES if (name := pattern.match(answer, glue_end, start))
                     and first_item in (None, item)]
            if not names:
                break
            label_start, first_item = max(names)
    label = answer[label_start:start]
    items, excluded, excluded_at_number = set(), set(), set()
    for pattern, item in ITEM_NAMES:
        for name in pattern.finditer(label):
            exclusion = EXCLUDED_BEFORE.search(label[:name.start()])
            if not exclusion or NEGATED_EXCLUSION.search(label[:exclusion.start()]):
                items.add(item)
            elif NAME_RIGHT_BEFORE_NUMBER.fullmatch(label[name.end():]):
                excluded_at_number.add(item)
            else:
                excluded.add(item)
    # Tên bị loại trừ đứng SÁT số: nhãn không nêu khoản nào khác ⇒ là tên của chính số đó ("chưa cộng thưởng thêm E đ"); nhãn đã nêu khoản khác
    # ("Riêng 8 KPI, không tính thưởng thêm: K đ") ⇒ là lời loại trừ
    if items - excluded_at_number:
        excluded |= excluded_at_number
    else:
        items |= excluded_at_number
    # "chưa kể lương kinh doanh 8 KPI": lời loại trừ áp cho cả cụm tên của một phía; loại trừ phía này = đang nói về phía kia
    items = (items - excluded) | {OTHER_SIDE[item] for item in excluded if item in OTHER_SIDE}
    if excluded & set(OTHER_SIDE):
        items.add("via_exclusion")
    if IN_TURN.search(label):
        items.add("in_turn")
    glued = REPEATED_GLUE_AFTER.match(answer, end, line_end)
    items |= {item for pattern, item in ITEM_NAMES if pattern.match(answer, glued.end(), line_end)}
    if NUMBERED_ITEM.search(label):
        items.add("extra_bonus")
    if THIRD_SIDE.search(label):
        items.add("third_side")
    header = _header_cell_item(answer, line_start, start)
    return items | ({header} if header else set())


def _repeated_part(answer: str, masked: str, match, options, multiplier, mine: dict) -> str:
    """"salary" / "extra" = số là K / E của người (ô `mine`), nhắc lại dưới nhãn không nêu khoản đó, sau khi đã nêu tiền cả hai phía ⇒ chặn
    (nhãn nêu CẢ HAI phía thì chặn luôn); "stated" = số là phía còn lại sau lời loại trừ ⇒ qua (bỏ lớp khoản);
    "total" = số là ô tổng gộp, nhãn không nêu khoản nào, cùng điều kiện ⇒ qua (bỏ lớp khoản); "" = luật này không xét."""
    salary, extra, total = mine.get("business_salary", set()), mine.get("extra_bonus_total", set()), mine.get(TOTAL_ITEM, set())
    if not salary or not extra:
        return ""
    def equals(values, opts=options, mult=multiplier) -> bool:
        return nc._sourced(opts, mult, False, values, set(), True)

    kind = "salary" if equals(salary) else "extra" if equals(extra) else "total" if equals(total) else ""
    if not kind or _negated_right_before(answer, match.start()):   # "… không phải K đ mà là T đ"
        return ""
    items = _segment_items(answer, masked, match.start(), match.end())
    # Vòng 2 (20/11, hồi quy người kiểm tìm): nhãn / vế nêu CẢ HAI phía ("- 8 KPI và thưởng thêm: K đ") ⇒ người đọc hiểu là tổng hai khoản ⇒ số phải là
    # ô tổng gộp; bằng đúng một khoản lẻ ⇒ chặn, không cần hai phía đã được nêu riêng ở phía trước
    if {"business_salary", "extra_bonus"} <= items and "in_turn" not in items:
        return "total" if equals(total) else kind
    # "Chưa kể thưởng thêm thì NV07 được K đ": số đúng là phía còn lại ⇒ đúng; trả "stated" để lớp khoản cũ không mượn tên khoản bị loại trừ cho số này
    if "via_exclusion" in items and kind != "total":
        # … còn số là chính phía BỊ loại trừ ("Riêng 8 KPI, không tính thưởng thêm: E đ") ⇒ chặn, không cần hai phía đã nêu riêng ở phía trước
        return "stated" if any(equals(mine.get(item, set())) for item in items) else kind
    if (kind == "total" and items) or (kind != "total" and any(equals(mine.get(item, set())) for item in items)):
        return ""   # nhãn nêu đúng tên khoản của số (hoặc số tổng có nhãn riêng — luật khoản cũ xét)
    salary_shown = extra_shown = False
    for prior in nc.NUMBER.finditer(masked, 0, match.start()):
        if KPI_COUNT_AFTER.match(masked, prior.end()):
            continue
        prior_options = nc.readings(prior.group(1))
        prior_multiplier = nc.MULTIPLIERS.get((prior.group(2) or "").strip().lower(), Decimal(1))
        is_salary, is_extra = equals(salary, prior_options, prior_multiplier), equals(extra, prior_options, prior_multiplier)
        if is_salary or is_extra:
            prior_items = _segment_items(answer, masked, prior.start(), prior.end())
            salary_shown = salary_shown or (is_salary and "business_salary" in prior_items)
            extra_shown = extra_shown or (is_extra and "extra_bonus" in prior_items)
    return kind if salary_shown and extra_shown else ""


def _repeated_part_problem(number: str, kinds: dict[str, str]) -> str:
    wrong = [code for code, kind in kinds.items() if kind in ("salary", "extra")]
    item = "lương kinh doanh 8 KPI" if kinds[wrong[0]] == "salary" else "thưởng thêm"
    return (f"số {number} là đúng số {item} của {', '.join(wrong)} nhưng nhãn / vế của nó không nêu riêng tên khoản đó — đọc như số tổng "
            "hoặc một khoản khác; ghi rõ tên khoản, hoặc dùng ô tổng 8 KPI + thưởng thêm")


def check_owners(answer: str, tool_results: list, question: str = "", context: list | tuple = (), same_sentence_after: bool = False,
                 name_entries: dict | None = None, item_question: str | None = None) -> dict:
    """{"ok": bool, "problems": [lời ngắn, mỗi chỗ sai một dòng]} — ok = mọi số có mã đứng trước thuộc đúng NV, mọi câu đếm người khớp bảng đội."""
    # KPI_NAMES=on: bên gọi đưa bảng tên ĐỦ ĐỘI {mã che: {name, sm_code}} ⇒ tên / mã SM trong câu trả lời đổi sang mã che TRƯỚC mọi luật. Không suy bảng
    # từ `tool_results` (12/11: lượt chỉ có kết quả ⇒ "<tên B> được <tiền A>" lọt). Cờ off ⇒ không có bảng ⇒ không đổi gì.
    answer = agent_names.names_to_codes(answer, name_entries or {})
    problems = []
    owned = numbers_by_employee(tool_results)
    single = single_person_totals(tool_results)
    actual = actual_money_by_employee(tool_results)
    team_numbers = owned.get("TEAM", set())
    percent_sources = nc.percent_key_numbers(tool_results)
    exempt = nc.source_numbers(list(context))   # số code điền ở dòng ngữ cảnh: không phải số của một NV
    asked = nc.source_numbers([question])       # số người dùng tự nói — chỉ miễn khi câu phủ định / nhắc lại câu hỏi (dưới, kiem_so@22)
    names = _kpi_names(tool_results)
    counts = team_kpi_counts(tool_results)
    achieved_pct = achieved_percent_by_employee(tool_results)
    ratio_actual = ratio_actual_by_employee(tool_results)
    listed_kpis = kpis_listed_by_employee(tool_results)
    money_sources = {"actual": actual, "single": single, "owned": owned, "team": team_numbers,
                     "gaps": _field_by_employee(tool_results, lambda row, key: key in ("gap_to_next", "gap_per_working_day_left")),
                     "groups": group_totals(tool_results), "differences": difference_pairs(tool_results)}
    # Câu chuẩn chép NGUYÊN (cả mã NV trong ngoặc — C X5: đúng số sai mã phải trượt) ⇒ code đã đúng, che đi trước mọi luật. Câu chuẩn tính LẠI từ
    # kết quả công cụ của lượt, không tin ô `team_sentences` trong kết quả (để câu giả gửi kèm không thành nguồn).
    # kiem_so@18 (12/11): + câu chuẩn 1 % gia vị (tiền trả KHÁCH, gom theo nhân viên phụ trách). Kết quả công cụ đó cố ý không có khoá `employee_id`
    # ⇒ luật 1 chặn MỌI câu đặt số tiền đó sau một mã NV, kể cả câu đúng; chỉ câu code viết (nói rõ người nhận là khách) chép nguyên mới qua.
    # kiem_so@27 (19/11, bậc 3a P5): + câu chuẩn của từng lưu ý dữ liệu (`issue_sentences`) — chép nguyên thì che; tự diễn lại thì vẫn qua mọi luật
    for sentence in [*team_sentences(tool_results), *spice_sentences(tool_results), *issue_sentences(tool_results)]:
        answer = answer.replace(sentence, " " * len(sentence))

    # Che như number_check nhưng giữ ĐÚNG độ dài để vị trí số khớp chữ gốc (B 14:21: thay bằng 1 dấu cách làm lệch ⇒ báo sai 13 số)
    masked = answer
    # NOT_EMPLOYEE_NUMBERS che TRƯỚC: "ngày N" của SKIP_PATTERNS ăn mất "ngày 30" của "ngày 30/10/2026" ⇒ "10", "2026" lộ (lượt e0b câu 7, 10/11)
    for pattern in [*NOT_EMPLOYEE_NUMBERS, *nc.SKIP_PATTERNS]:
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    date_word_money = set()   # kiem_so@30: số tiền sau "tháng / ngày / năm" — mở lại (xem DATE_WORD_MONEY)
    for found in DATE_WORD_MONEY.finditer(answer):
        masked = masked[:found.start(1)] + found.group(1) + masked[found.end(1):]
        date_word_money.add(found.start(1))
    everywhere = set().union(*owned.values()) if owned else set()
    reached_pct = reached_tier_percents_by_employee(tool_results)
    tier_lists = tier_lists_by_employee(tool_results)
    # kiem_so@22: lớp "đúng KHOẢN" sau luật đúng người. Số có trong câu hỏi chỉ được miễn ở
    # lớp NÀY (luật đúng người cố ý không miễn — A bản 3, 10/11, lách "…, đúng như anh nói")
    item_sources = {"cells": item_cells_by_employee(tool_results), "tiers": kpi_tiers_by_employee(tool_results)}
    item_exempt = nc.source_numbers([question if item_question is None else item_question])
    team_totals = team_total_cells(tool_results)

    def in_tier_listing(match, owners: list[str]) -> bool:
        """Số này nằm trên một DÒNG MÔ TẢ MỐC của chính (các) chủ số — xem `_tier_listing_numbers`."""
        listed = _tier_listing_numbers(answer, match.start(), [tiers for code in owners for tiers in tier_lists.get(code, [])])
        return bool(listed) and any(value in listed for value, _ in nc.readings(match.group(1)))
    # Luật 1 — số thuộc đúng NV (số người: luật 2 xét)
    for match in nc.NUMBER.finditer(mask_people_counts(masked)):
        unit = (match.group(2) or "").strip().lower()
        options = nc.readings(match.group(1))
        multiplier = nc.MULTIPLIERS.get(unit, Decimal(1))
        is_percent = not unit and nc.PERCENT_AFTER.match(masked, match.end()) is not None
        is_money = not is_percent and (bool(unit) or nc.MONEY_AFTER.match(masked, match.end()) is not None)
        # number_check cũng che số này (cùng mẫu "tháng N") ⇒ chính lớp này phải đòi nguồn, không thì số bịa sau "tháng" không lớp nào bắt
        if match.start() in date_word_money and not nc._sourced(options, multiplier, is_percent, everywhere | exempt, percent_sources, is_money):
            problems.append(f"số {match.group(0).strip()} không có trong kết quả công cụ")
            continue
        owners, need_every_owner = _owners_of_number(answer, match.start(), match.end(), same_sentence_after, strict=is_money or is_percent)
        if not owners and is_money:
            team_problem = _wrong_team_total(answer, masked, match, options, multiplier, team_totals)
            if team_problem:
                problems.append(team_problem)
            unowned_problem = _wrong_unowned_total(answer, masked, match, options, multiplier, item_sources["cells"])
            if unowned_problem:
                problems.append(unowned_problem)
            # kiem_so@30: số không có chủ ⇒ "người" = ai trong kết quả công cụ đã được nêu tiền cả hai phía ở phía trước
            kinds = {code: _repeated_part(answer, masked, match, options, multiplier, mine) for code, mine in item_sources["cells"].items()}
            kinds = {code: kind for code, kind in kinds.items() if kind}
            if not team_problem and not unowned_problem and kinds and all(kind in ("salary", "extra") for kind in kinds.values()):
                problems.append(_repeated_part_problem(match.group(0).strip(), kinds))
        if not owners or nc._sourced(options, multiplier, is_percent, exempt, set(), is_money):
            continue
        # kiem_so@22 (A sau 15:03, trên ĐƯỜNG CHAT): số có trong CÂU HỎI chỉ được miễn khi câu chứa nó phủ định / nhắc lại câu hỏi — "Có, NV04 được
        # 2.000.000 đ PC_BT" (hỏi "… 2.000.000 đ PC_BT không?") phải xét. Đường Capitol truyền question="" nên không đổi; số của dòng ngữ cảnh vẫn miễn.
        if nc._sourced(options, multiplier, is_percent, asked, set(), is_money) and _negated_right_before(answer, match.start()):
            continue
        if not nc._sourced(options, multiplier, is_percent, everywhere, percent_sources, is_money):
            continue   # số không có nguồn ở đâu cả: number_check đã báo — không báo hai lần
        if is_money:
            # ĐẢO LUẬT ( 11/11 chiều, câu 1c (b) — thay luật "chặn khi khớp tiền người khác" của bản cuối 3 / 4): hai lỗ của bản 4 (,
            # trên RAW thật) — câu "sẽ / nếu …" bỏ qua cả phép kiểm tiền; số cấp đội (tổng 126.660.000, mức 14.540.000) gán cho một NV qua `team_numbers`.
            passed = _money_passes(answer, masked, match, options, multiplier, owners, need_every_owner, money_sources)
            if not passed and not (not unit and in_tier_listing(match, owners)):   # đường 7 (kiem_so@18): tiền của MỐC trên dòng mô tả mốc
                problems.append(f"số {match.group(0).strip()} không phải tiền của {', '.join(owners)} trong công cụ — số tiền sau mã NV phải là "
                                f"tiền của chính người đó (ô mốc chỉ dùng trong câu có điều kiện \"nếu … thì\"); số của cả đội ghi kèm \"cả đội / tổng đội\"")
            elif passed in ("own", "own_condition"):
                # kiem_so@30: như luật đúng người — chủ không chắc (`need_every_owner`) ⇒ sai với một người là chặn; chắc ⇒ phải sai với mọi chủ
                kinds = {code: _repeated_part(answer, masked, match, options, multiplier, item_sources["cells"].get(code, {})) for code in owners}
                parts = [code for code, kind in kinds.items() if kind in ("salary", "extra")]
                if parts and (need_every_owner or len(parts) == len(owners)):
                    problems.append(_repeated_part_problem(match.group(0).strip(), kinds))
                    continue
                if any(kind in ("total", "stated") for kind in kinds.values()):
                    continue   # ô tổng gộp dưới nhãn không nêu khoản (hoặc phía còn lại sau lời loại trừ) ⇒ đúng; không để lớp khoản mượn tên khoản khác
                wrong_item = _wrong_item(answer, masked, match, options, multiplier, owners, item_sources, item_exempt, passed == "own_condition",
                                         need_every_owner)
                if wrong_item:
                    problems.append(wrong_item)
                elif passed == "own":
                    bare = _component_called_total(answer, masked, match, options, multiplier, owners, item_sources["cells"], need_every_owner)
                    if bare:
                        problems.append(bare)
            continue
        employee = owners[0]
        if not is_percent and not is_money and KPI_COUNT_AFTER.match(masked, match.end()) \
                and any(value == len(listed_kpis.get(employee, ())) for value, _ in options):
            continue   # "ở 3 KPI" = đúng số KPI công cụ liệt kê cho NV này (đếm bằng code) ⇒ có nguồn
        if need_every_owner:   # không chắc chủ (mã tiêu đề ≠ mã sau số trong câu) ⇒ phải đúng với từng mã
            wrong = [code for code in owners
                     if not nc._sourced(options, multiplier, is_percent, owned.get(code, set()) | team_numbers, percent_sources, is_money)]
            if wrong:
                problems.append(f"số {match.group(0).strip()} không rõ của {' hay '.join(owners)} — không thuộc dữ liệu của {', '.join(wrong)}")
                continue
        allowed = set().union(*(owned.get(code, set()) for code in owners)) | team_numbers
        if not nc._sourced(options, multiplier, is_percent, allowed, percent_sources, is_money):
            problems.append(f"số {match.group(0).strip()} không thuộc dữ liệu của {', '.join(owners)}")
        elif is_percent and ACHIEVED_BEFORE.search(masked, max(0, match.start() - 10), match.start()) \
                and not TIER_WORD.search(masked, _clause_start(masked, match.start()), match.start()) \
                and not in_tier_listing(match, owners):   # "Đạt 95%: 3.920.000 đ" trong danh sách mốc = TÊN mốc, không phải % đạt của người đó
            # Vòng 2 ý 5: "NV… đạt X %" phải BẰNG ĐÚNG % ĐẠT của NV đó (luật %), không khớp tên mốc
            # vẫn cần dù % đã so đúng: số 100 của tên mốc "Đạt 100%" có trong nguồn nên luật 1 cho "NV13 đạt 100 %" (công cụ 99,5) qua
            achieved = achieved_pct.get(employee, set())
            # "… mốc thưởng 'Đạt 100%'" = tên mốc NV này ĐÃ ĐẠT (ô reached_tier của công cụ) ⇒ có nguồn; % thật vẫn phải đúng ô `pct`
            if achieved and not nc._sourced(options, multiplier, True, achieved | reached_pct.get(employee, set()), set(), False):
                problems.append(f"\"đạt {match.group(0).strip()} %\" không khớp % đạt của {employee} trong công cụ")
        elif not is_percent and not is_money and VALUE_BEFORE.search(masked, max(0, match.start() - 14), match.start()) \
                and RATIO_NAME.search(masked, (clause := _clause_start(masked, match.start())), match.start()) \
                and not TIER_WORD.search(masked, clause, match.start()):
            # Luật (tỷ lệ đúng y số công cụ): "LPPC_Meat của NV04 là 3,8" phải BẰNG ĐÚNG ô actual KPI tỷ lệ của NV đó
            # cùng lý do nhánh trên: 3,8 có sẵn ở mốc "Mức 1: 3.8" nên luật 1 cho qua (tầng A C Y5-50)
            ratio = ratio_actual.get(employee, set())
            if ratio and not nc._sourced(options, multiplier, False, ratio, set(), False):
                problems.append(f"\"{match.group(0).strip()}\" không khớp giá trị KPI tỷ lệ của {employee} trong công cụ")

    # Luật 2 — câu đếm người / cả đội / không ai, so với số người code đếm từ bảng thưởng đội của lượt
    examined = []   # vị trí các khẳng định luật 2 ĐÃ đối chiếu (đúng hay sai đều đã có lời) — luật 3 không báo lại
    for start, segment_end, text, kind, said, sentence_start in _people_claims(answer, masked):
        have = kind != "count" and (HAVE_WORDS.search(answer, sentence_start, start) or HAVE_WORDS.search(answer, start + len(text), segment_end))
        if not MONEY_WORDS.search(answer, sentence_start, segment_end) and not have:
            continue
        kpis, negated = _claim_kpis(answer, start, segment_end, sentence_start, kind, names)
        if not kpis:
            continue
        examined.append(start)
        if counts is None:
            problems.append(f"câu \"{text} … {', '.join(kpis)}\" chưa đối chiếu được — lượt này không có bảng thưởng đội")
            continue
        team, unpaid = counts["team"], _unpaid_after(answer, start + len(text))   # chiều lấy từ cụm NGAY SAU khẳng định
        clause_before = answer[max(sentence_start, _clause_start(answer, start)):start]
        not_all = kind == "all" and NOT_ALL_BEFORE.search(clause_before) is not None   # "Không phải ai cũng có tiền …" (@11)
        if kind == "all" and not not_all and NEGATION_WORD.search(clause_before):
            problems.append(f"câu \"{clause_before.strip()} {text} … {', '.join(kpis)}\" có phủ định không rõ nghĩa — không đối chiếu được")
            continue
        for kpi in kpis:
            if kpi not in counts["paid"]:   # chỉ có A1 của KPI khác ⇒ không có gì để đối chiếu (trước 09/11 bảng đội luôn đủ 8 KPI)
                problems.append(f"câu \"{text} … {kpi}\" chưa đối chiếu được — lượt này không có số người có tiền {kpi}")
                continue
            paid = counts["paid"][kpi]
            if kind == "count" and TEAM_SIZE_BEFORE.search(answer, max(0, start - 8), start):
                # Vòng 2 ý 4: "đều 0 ở cả 14 nhân viên" nói số người CỦA ĐỘI, không phải số người có tiền (T4 lượt 2 câu 7 04/11 bắt nhầm)
                # … và chiều câu: "0 đồng ở cả 14" ⇒ không ai có tiền; "cả 14 người đều có tiền" ⇒ cả đội có (tầng A -46)
                # Chiều lấy trong CÙNG VẾ trước khẳng định + cụm ngay sau — không lấy cả câu (V2, v2: lặp lỗi m2)
                before_clause = answer[max(sentence_start, _clause_start(answer, start)):start]
                zero = UNPAID_WORDS.search(before_clause) or re.search(r"\b0\b", before_clause) or _unpaid_after(answer, start + len(text))
                ok = said == team and paid == (0 if zero else team)
            elif kind == "count":
                fraction = PEOPLE_COUNT.match(masked, start)
                whole = fraction.group(2) if fraction else None
                ok = said == (team - paid if unpaid else paid) and (whole is None or int(whole) == team)
            elif kind == "all":      # "tất cả đều có" ⇒ cả đội có; "tất cả đều chưa có" ⇒ không ai có
                ok = paid == (0 if unpaid else team)
                if not_all:          # "không phải ai cũng có" ⇒ ≥ 1 người chưa có ⇒ kiểm ngược
                    ok = not ok
            else:                    # "không ai có" ⇒ không ai có; "không ai bị 0 đ / chưa có" ⇒ cả đội có
                ok = paid == (team if unpaid else 0)
                if kpi in negated:   # "PC_ON … không thuộc nhóm chưa ai có tiền" / "… trừ PC_ON" ⇒ câu nói ĐIỀU NGƯỢC LẠI ⇒ kiểm ngược, không bỏ qua
                    ok = not ok
            if not ok:
                prefix = "không thuộc / trừ — " if kpi in negated else "không phải — " if not_all else ""
                problems.append(f"\"{prefix}{text}\" {kpi}: bảng đội ghi {paid} / {team} người có tiền")

    # Luật 3 (bậc (a) 11/11) — câu cả đội còn lại sau khi che câu chuẩn: có tên KPI + dấu hiệu nhóm người + chữ có / tiền / đạt. Không chắc ⇒ chặn
    # (mô hình có câu chuẩn để chép): (1) câu của MỘT người (mở đầu bằng mã duy nhất của câu) ⇒ luật 1 xét, bỏ qua; (2) câu liệt kê mã ⇒ đúng 1 KPI và
    # tập mã BẰNG ĐÚNG tập có tiền (hoặc chưa có — câu có "chưa / không / bị 0") của KPI đó (C X5: đúng số, sai mã ⇒ chặn); (3) không mã: có từ khẳng
    # định cả nhóm, hoặc không có số tiền / mốc / cơ hội ⇒ phải được luật 2 đối chiếu.
    paid_sets, full_table = _paid_by_kpi(tool_results)
    position = 0
    for stop in [*TEAM_SENTENCE_END.finditer(answer), None]:
        end = stop.start() if stop else len(answer)
        sentence, sentence_at = answer[position:end], position
        position = stop.end() if stop else len(answer)
        kpis_here = _kpis_in(sentence, names)
        if not (kpis_here and TEAM_VERB.search(sentence)):
            continue
        quoted = sentence.strip()[:80]
        fraction = STANDARD_LIKE.search(sentence)
        if fraction:
            # Giả khuôn câu chuẩn "KPI: N / M người có tiền (mã…)" mà không trùng nguyên văn (C X5: đúng số, sai mã) ⇒ mã trong ngoặc phải BẰNG ĐÚNG
            # tập có / chưa có tiền của KPI (code đếm); không có mã ⇒ luật 2 đã đối chiếu số
            codes = _listed_codes(sentence)
            if codes and (len(kpis_here) != 1 or kpis_here[0] not in paid_sets
                          or set(codes) not in (set(paid_sets[kpis_here[0]][0]), set(paid_sets[kpis_here[0]][1]))):
                problems.append(f"câu cả đội \"{quoted}\": danh sách mã không khớp tập có / chưa có tiền của công cụ — chép nguyên câu chuẩn")
            continue
        asserted = GROUP_ASSERT.search(sentence) or NONE_WORDS.search(sentence)
        claim = NONE_WORDS.search(sentence) or ALL_WORDS.search(sentence)
        if full_table and claim and KPI_LIST_CLAIM.search(sentence, 0, claim.start()) and not _kpis_in(sentence[:claim.start()], names) \
                and not (NEGATED_BEFORE_ANY.search(sentence) or EXCEPT_WORD.search(sentence)):
            # (chỉ câu "KPI … <cụm> … : danh sách" — tên chỉ tiêu đứng SAU cụm; "PC_BT là KPI chưa ai có tiền" nói về MỘT chỉ tiêu ⇒ luật 2 xét)
            # Bản cuối 3 (@13, major): "KPI chưa ai có tiền: PC_BT." QUA dù Turnover cũng 0 / 14 — câu DANH SÁCH chỉ tiêu phải nêu ĐỦ và ĐÚNG
            # tập chỉ tiêu code đếm (thiếu hay thừa ⇒ chặn). Chỉ khi có bảng đội đủ mọi KPI; câu phủ định / loại trừ để luật 2 xét.
            nobody = {kpi for kpi, (paid, _) in paid_sets.items() if not paid}
            everybody = {kpi for kpi, (_, unpaid) in paid_sets.items() if not unpaid}
            none_claim = NONE_WORDS.search(sentence) is not None
            flipped = UNPAID_WORDS.search(sentence, claim.end()) is not None
            expected = (everybody if flipped else nobody) if none_claim else (nobody if flipped else everybody)
            if set(kpis_here) != expected:
                problems.append(f"câu \"{quoted}\": danh sách chỉ tiêu không khớp {len(expected)} chỉ tiêu "
                                f"{'chưa ai có' if expected is nobody else 'cả đội đều có'} tiền theo công cụ")
            continue
        codes = _listed_codes(sentence)
        checked_by_rule_2 = any(sentence_at <= at < end for at in examined)   # câu phủ định @9 "… nên không thuộc nhóm chưa ai có tiền" — luật 2 đã xét
        if codes and LIST_MONEY.search(sentence):   # kể cả câu luật 2 đã đếm đúng số ("Có 2 bạn …: NV04, NV05" — số đúng, mã sai)
            # Bản cuối 2 (64b4d6ea, major): câu nêu mã + "có / được / nhận tiền" — "Có 1 bạn đang có tiền PC_ON: NV05 (1.500.000 đồng)"
            # (thật NV04; 1,5 tr có nguồn ở cấp đội vì KPI chỉ 1 người có), "Chỉ NV05 có tiền PC_ON", "Có 2 bạn có tiền PC_BT: NV04, NV05" ⇒ so TẬP MÃ với code:
            # nói về cả nhóm (từ chỉ nhóm / "chỉ / duy nhất / riêng") ⇒ BẰNG ĐÚNG; không ("NV01 và NV02 có tiền …", câu một người "NV05 có tiền PC_ON") ⇒ THUỘC.
            single = len(codes) == 1 and EMPLOYEE_CODE.match(BULLET.sub("", sentence)) is not None
            whole = not single and (GROUP_SIGNAL.search(sentence) or ONLY_WORD.search(sentence)) is not None
            said = PEOPLE_COUNT.search(sentence)
            if whole and said and not ONLY_WORD.search(sentence) and int(said.group(1)) != len(codes):
                whole = False   # "9 người chưa có tiền ASO_ALL: NV01 95,68 %" + các mã ở dòng dưới ⇒ câu chỉ có MỘT PHẦN danh sách ⇒ đòi THUỘC
            kpi = kpis_here[0] if len(kpis_here) == 1 else None
            if kpi not in paid_sets:
                if whole:
                    problems.append(f"câu \"{quoted}\" liệt kê người có / chưa có tiền nhưng không đối chiếu được với một KPI của công cụ")
                continue
            paid, unpaid = paid_sets[kpi]
            # chiều lấy ở phần TRƯỚC mã đầu khi chữ tiền đứng trước mã ("Chưa có tiền ASO_Focus: NV13, NV14" / "Có 1 bạn có tiền …: NV04, 13 bạn chưa có");
            # câu một người / chữ tiền đứng sau mã ("NV13, NV14 chưa có tiền …") ⇒ cả câu
            lead = sentence[:EMPLOYEE_CODE.search(sentence).start()]
            if single or not LIST_MONEY.search(lead):
                lead = sentence
            # "…, chỉ NV14 chưa có." — chữ "chưa / không" NGAY SAU danh sách mã thắng chữ tiền phía trước (câu thật test m2 1b)
            tail = sentence[list(EMPLOYEE_CODE.finditer(sentence))[-1].end():]
            expected = unpaid if NEG_AFTER_CODES.match(tail) or NEG_MONEY.search(lead) else paid
            if (set(codes) != set(expected)) if whole else not set(codes) <= set(expected):
                problems.append(f"câu \"{quoted}\": liệt kê sai người — công cụ ghi {len(expected)} người "
                                f"{'chưa có' if expected is unpaid else 'có'} tiền {kpi}")
            continue
        if asserted and codes and not checked_by_rule_2 and len(kpis_here) == 1 and kpis_here[0] in paid_sets:
            # "NV13 và NV14 đều có tiền ASO_Focus" ⇒ mỗi mã nêu phải THUỘC tập có (hoặc chưa có — câu có "chưa / không / bị 0") tiền của KPI đó
            paid, unpaid = paid_sets[kpis_here[0]]
            expected = unpaid if UNPAID_WORDS.search(sentence) else paid
            if not set(codes) <= set(expected):
                problems.append(f"câu \"{quoted}\": có mã không thuộc {len(expected)} người {'chưa có' if expected is unpaid else 'có'} tiền {kpis_here[0]}")
            continue
        # Câu KHẲNG ĐỊNH cả nhóm (đều / cũng / tất cả / mọi / hết, không ai …) ⇒ phải được luật 2 đối chiếu — 7 câu lọt @12 rơi vào đây
        if asserted and GROUP_SIGNAL.search(sentence) and not checked_by_rule_2:
            problems.append(f"câu cả đội \"{quoted}\" không đối chiếu được — chép nguyên câu chuẩn của công cụ")
    return {"ok": not problems, "problems": problems}
