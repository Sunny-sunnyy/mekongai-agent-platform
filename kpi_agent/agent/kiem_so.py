"""`kiem_so` — Rào chắn KIỂM BẰNG CODE cho KPI agent chạy trên Capitol: gọi SAU khối Agent, trượt ⇒ dừng lượt (kế hoạch
10/11).

Vì sao (gói bài học 03 mục 1–2): Rào chắn gốc của Capitol là AI chấm 0–100 và LUÔN cho đi tiếp ⇒ với tiền người thật, số phải
đối chiếu bằng code. Hàm này KHÔNG viết luật mới cho câu trả lời — dùng lại đúng hai phép kiểm trợ lý đang dùng (`chat._check_answer`):
`number_check` (mọi số có nguồn trong kết quả công cụ của lượt; tiền đúng tới đồng, % đúng y) + `owner_check` (số thuộc đúng NV, câu đếm người khớp
bảng đội). Phần MỚI chỉ là file Excel đi kèm câu trả lời (AI / hộp chạy code dựng): ô công thức / link ⇒ trượt; mọi ô số phải có nguồn; số trong
hàng có mã NV phải thuộc NV đó.

Nguồn số — một trong hai (A +):
  (a) `tool_calls` [{name, args}] ⇒ CHẠY LẠI từng lần qua `run_tool(name, args)` bên gọi đưa vào ⇒ nguồn = kết quả chạy lại: bước trước không gửi
      được số giả. Có calls mà không có `run_tool` ⇒ trượt (không đoán).
  (b) `tool_results` [dict] do HỆ THỐNG ghi (không phải chữ AI chép lại).
  Có cả hai ⇒ dùng (a).
Đóng khi lỗi: vào sai hình, Excel không đọc được, công cụ chạy lại lỗi ⇒ `dat: false` (bài 03 mục 2 — "lỗi chấm ⇒ coi như trượt").
Giới hạn (kế thừa docstring `number_check` / `owner_check`): lỗi CÙNG NV (tiền KPI này nói thành KPI kia) không bắt; số viết trước mã NV lấy nhầm NV;
số nhỏ (0, 1) gần như luôn có nguồn. Excel: chỉ xét ô kiểu số (ô chữ "5.040.000" không xét); ô ngày giờ bỏ qua."""
import base64
import binascii
import io
import re
import zipfile
from decimal import Decimal

import openpyxl

from kpi_agent.agent import number_check as nc
from kpi_agent.agent import owner_check as oc
from kpi_agent.agent import period_check
# Một nguồn "dấu" nhận ra cờ của một người trong chữ trả lời — đúng bảng bộ chấm đang dùng (run_g4c, mức tra cứu vòng 3), rào và bộ chấm không lệch nhau
FLAG_SIGNS = {
    "không thuộc MCP": re.compile(r"(?:ngoài|không thuộc)\s+(?:tuyến\s+)?MCP|công văn(?: mục)? 6\.2", re.IGNORECASE),
    "không có doanh số": re.compile(r"không có doanh số", re.IGNORECASE),
    "thiếu chỉ tiêu": re.compile(r"thiếu chỉ tiêu", re.IGNORECASE),
    "lệch (MCP": re.compile(
        r"(?:lệch|không khớp)[^.\n]{0,40}MCP|MCP[^.\n]{0,40}(?:lệch|không khớp)"
        r"|(?:lệch|không khớp)[^.\n]{0,40}danh sách tuyến|danh sách tuyến[^.\n]{0,60}(?:lệch|không khớp)",
        re.IGNORECASE,
    ),
}
EMPLOYEE_CODE = re.compile(r"\bNV\d{2}\b", re.IGNORECASE)

VERSION = "kiem_so@31"   # @31 (20/11): kỳ nêu bằng số trong câu trả lời (tháng / mã kỳ / năm) phải là kỳ của dữ liệu (`period_check`) · @30 (19/11): số đúng bằng 8 KPI hoặc thưởng thêm của một người, nhắc lại sau khi đã nêu tiền cả hai phía dưới nhãn không nêu tên khoản đó ⇒ chặn · "8 KPI" trần là tên khoản · số tiền sau chữ "tháng" được xét (owner_check._repeated_part); @29 (19/11): dòng "Tổng cộng" sau hai phần 8 KPI + thưởng thêm phải là ô tổng gộp (owner_check.GRAND_TOTAL); @28 (19/11): lời chú có "không được tính" không còn làm mục thành câu "không có cờ" (lỗ của @26); @27 (19/11, 3a P5): che câu chuẩn lưu ý dữ liệu chép nguyên (owner_check.issue_sentences); @26 (18/11): mục nêu đúng một KPI ⇒ người gắn cờ lưu ý phải là chủ dòng của KPI đó; @25 (18/11): bảng đọc tên khoản ở tiêu đề cột · tổng 8 KPI + thưởng thêm cấp đội so ô đội · khoản lẻ không được gọi là "tổng" trần (owner_check); @24: khoản tổng 8 KPI + thưởng thêm   # đổi luật ⇒ đổi số — Capitol ghi vào bản ghi lượt để biết lượt đó kiểm bằng luật nào
MAX_EXCEL_BYTES = 5 * 1024 * 1024   # file KPI xuất của mình ≈ 14 KB; trần để file lạ không làm treo máy chủ
EMPLOYEE_CELL = re.compile(r"^\s*(NV\d+)\b", re.IGNORECASE)
# Mã kỳ YYYY-MM ("kỳ 2026-09") không phải số KPI — như ngày tháng (number_check.SKIP_PATTERNS). 10/11 (aa9ce76, hồi quy): từ khi lời
# từ chối không là nguồn, câu từ chối kỳ khác ĐÚNG ("Chưa có dữ liệu kỳ 2026-09 …") trượt vì "09" không nguồn. Che bằng dấu cách CÙNG độ dài
# (giữ vị trí chữ / dòng trong câu trả lời). Kỳ không phải tiền / % ⇒ không mở lại lỗ cài số tiền qua tham số.
PERIOD_CODE = re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])\b")
# Số nằm TRONG tên sản phẩm ("Trà 365", "Homey 2,9 kg", "Kokomi Đại 90") không phải số KPI — che như mã kỳ. 10/11 M3 trên Capitol câu 8, 9:
# câu đúng bị chặn vì '365' (Rào chắn Capitol không gửi câu hỏi ⇒ luật tha số của câu hỏi không chạy). Tên lấy từ dữ liệu / luật: "Trà Búp Non Tea 365"
# (`core/load_raw.py:27`), mã Homey túi 2.9kg (`config/rules_2026_10_raw.yaml:23`), "Mì Kokomi Đại 90" (Thưởng thêm 06,).
# Giữ hẹp: chỉ số DÍNH LIỀN ngay sau tên ("Trà 365" che; "365 cửa hàng", "Trà 365: 365 cửa hàng" — số thứ hai — vẫn kiểm).
PRODUCT_NAME = re.compile(r"\b(?:trà(?:\s+búp\s+non)?(?:\s+tea)?\s*365|tea\s*365"
                          r"|homey(?:\s+jeju)?\s*2[.,]9\s*kg"
                          r"|(?:mì\s+)?kokomi(?:\s+đại)?\s*90)(?![\d.,]*\d)"
                          # @7 (minor 1): "Kokomi 90 cửa hàng" ⇒ 90 là SỐ ĐẾM, không che khi ngay sau là đơn vị đếm / tiền / %
                          r"(?!\s*(?:cửa hàng|đơn|người|bạn|nhân viên|thùng|dòng|lần|đồng|đ\b|vnđ|%|triệu|tr\b|nghìn|ngàn|k\b))", re.IGNORECASE)
# KPI tỷ lệ (mốc theo giá trị, không theo %) — `LPPC` không ăn vào `LPPC_Meat`
RATIO_KPI = re.compile(r"\bLPPC(?:_Meat)?\b(?!_)", re.IGNORECASE)
# Dòng chỉ nói về mốc / chỉ tiêu (không phải số thực hiện) ⇒ không đòi số actual (vd "LPPC_Meat mốc 3,8")
TIER_WORDS = re.compile(r"mốc|chỉ tiêu|ngưỡng|mức|cần|tối thiểu", re.IGNORECASE)
# kiem_so@20 (13/11): số thực hiện KPI tỷ lệ chỉ tìm trong VẾ chứa tên KPI — vế hết ở dấu ; ! ? hoặc . , không nằm trong số ("3,82" giữ), hoặc ở tên KPI khác
OTHER_KPI_NAME = re.compile(r"\b(?:Turnover|PC_BT|PC_ON|ASO_ALL|ASO_Focus(?:_2)?|LPPC(?:_Meat)?)\b", re.IGNORECASE)
RATIO_CLAUSE_END = re.compile(r"[;!?]|[.,](?!\d)")
KPI_COUNT = re.compile(r"\d+(?=\s*KPI\b)", re.IGNORECASE)   # "có tiền ở 5 KPI" — số đếm KPI, không phải số thực hiện


def _problem(kind: str, detail: str) -> dict:
    return {"loai": kind, "chi_tiet": detail}


def _result(problems: list[dict]) -> dict:
    reason = "đạt — mọi số có nguồn, thuộc đúng nhân viên" if not problems else \
        f"trượt — {len(problems)} lỗi: " + "; ".join(problem["chi_tiet"] for problem in problems[:3]) + (" …" if len(problems) > 3 else "")
    return {"dat": not problems, "loi": problems, "ly_do": reason, "phien_ban": VERSION}


def _not_refusal(result) -> bool:
    # Lời từ chối / lỗi KHÔNG là nguồn số: lời từ chối nhắc lại tham số AI điền ⇒ AI cài số bịa vào tham số
    # (kpi "5.041.000", extra vượt trần, mã NV "5041000") rồi viết chính số đó vào câu trả lời ⇒ trước đây được tính là "có nguồn".
    return not (isinstance(result, dict) and ("refused" in result or "error" in result))


def _sources(payload: dict, run_tool) -> tuple[list | None, list[dict]]:
    """(kết quả công cụ dùng làm nguồn, lỗi). Dạng (a) chạy lại ưu tiên hơn (b). Lời từ chối / lỗi bị bỏ khỏi nguồn (`_not_refusal`)."""
    calls, recorded = payload.get("tool_calls"), payload.get("tool_results")
    if calls is not None:
        if not isinstance(calls, list) or not all(isinstance(call, dict) and isinstance(call.get("name"), str)
                                                  and isinstance(call.get("args", {}), dict) for call in calls):
            return None, [_problem("dau_vao_loi", "tool_calls phải là danh sách {name: chữ, args: bảng}")]
        if run_tool is None:
            return None, [_problem("khong_chay_lai_duoc", "có tool_calls nhưng bên gọi không đưa hàm chạy lại công cụ — không kiểm được nguồn số")]
        results = []
        for call in calls:
            try:
                results.append(run_tool(call["name"], call.get("args", {})))
            except Exception as error:   # công cụ chạy lại lỗi ⇒ không có nguồn ⇒ trượt, không cho qua
                return None, [_problem("khong_chay_lai_duoc", f"chạy lại {call['name']} lỗi: {type(error).__name__}")]
        return [result for result in results if _not_refusal(result)], []
    if recorded is not None:
        if not isinstance(recorded, list) or not all(isinstance(item, dict) for item in recorded):
            return None, [_problem("dau_vao_loi", "tool_results phải là danh sách kết quả công cụ (bảng)")]
        return [result for result in recorded if _not_refusal(result)], []
    return [], []   # không gọi công cụ nào: câu trả lời có số ⇒ number_check báo thiếu nguồn


def _kpis_named(text: str) -> set[str]:
    """Tên KPI (8 KPI) nêu trong đoạn chữ — bảng tên của lớp "đúng khoản" (owner_check; "LPPC_Meat" không tính là "LPPC")."""
    return {item for pattern, item in oc.ITEM_NAMES if item in oc.KPI_ITEMS and pattern.search(text)}


FLAG_CLAUSE_END = re.compile(r"[!?;\n]|\.(?=\s|$)")      # hết vế — dấu chấm trong số ("công văn 6.2") không cắt
# AI trong mục được coi là KHÔNG bị gắn cờ — kiem_so@28 (19/11), làm lại sau hai lượt kiểm độc lập. Bản @26 bỏ luật theo KPI khi vế còn chữ "không" /
# "chưa" bất kỳ; bản đầu của @28 đổi sang danh sách động từ — cả hai đều lọt vì lời CHÚ của lưu ý tự có chữ phủ định ("công văn 6.2: không được tính",
# "hệ thống không ghi nhận vào kết quả", "chưa thấy được tính"). Nay ĐẢO CHIỀU: mọi mã trong mục là người bị gắn cờ, TRỪ đúng ba khuôn hẹp nói thẳng
# "người đó không dính dấu hiệu đó"; mọi chữ "không" / "chưa" khác (lời chú về tiền, cách tính, trong ngoặc) không đổi gì:
#   (1) danh sách mã LIỀN SAU chữ phủ định: "không phải NV04", "trừ NV04, NV14", "không có NV08", "chưa thấy NV08";
#   (2) mã là CHỦ NGỮ của lời phủ định: ở vế mang dấu hiệu thì lời phủ định phải đi LIỀN tới dấu hiệu ("NV04 không có đơn ngoài MCP", "NV04, NV08 đều
#       không vướng cửa hàng không thuộc MCP") hoặc hết vế ("NV08 thì không có"); ở vế khác: "NV08 không liên quan tới KPI này", "NV04 và NV14 thì không";
#   (3) mã đứng sau lời phủ định đi liền dấu hiệu: "không có đơn ngoài MCP nào của NV04".
# Chặn oan vài câu phủ định viết lạ là chấp nhận được (19/11: sai về phía an toàn) — đo bằng phát lại câu trả lời thật.
CODE_LIST = r"\bNV\d{2}\b(?:\s*(?:,|\bvà\b|\bhoặc\b)\s*\bNV\d{2}\b)*"
FLAG_NEGATED_LIST = re.compile(r"\b(?:không\s+(?:phải|có|gồm|kể)|ngoại\s+trừ|trừ|chưa\s+(?:thấy|có))(?:\s+là)?[\s:]*" + CODE_LIST, re.IGNORECASE)
FLAG_NEGATED_SUBJECT = re.compile(CODE_LIST + r"\s+(?:(?:thì|đều|cũng)\s+)?(?:không|chưa)\b(?:\s+hề\b)?(?:\s+(?:có|thấy|bị|dính|vướng|liên\s+quan|nằm)\b)?",
                                  re.IGNORECASE)
FLAG_DENIAL = re.compile(r"\b(?:không|chưa)\s+(?:hề\s+)?(?:có|thấy|bị|dính|vướng|ghi\s+nhận|phát\s+sinh)\b", re.IGNORECASE)
# Chữ được phép đứng GIỮA lời phủ định và dấu hiệu — chỉ danh từ của chính thứ bị gắn cờ; chữ khác ("thưởng", "được tính", "trong") ⇒ không phải phủ định cờ
FLAG_FILLER = re.compile(r"(?:\s+(?:đơn\s+hàng|đơn|cửa\s+hàng|điểm\s+bán|dòng|lưu\s+ý|trường\s+hợp|cờ|nào|ở|tại|của|các|những))*\s*", re.IGNORECASE)
FLAG_OF_CODES = re.compile(r"(?:\s+nào)?\s+(?:của|ở|cho|với)\s+" + CODE_LIST, re.IGNORECASE)
CLAUSE_TAIL = " \t.,)*_—–-"


def _denied_sign(clause: str, position: int, sign):
    """Lời phủ định kết thúc ở `position` có đi LIỀN tới dấu hiệu `sign` không (chỉ qua chữ của FLAG_FILLER) — trả match của dấu hiệu hoặc None."""
    return sign.match(clause, FLAG_FILLER.match(clause, position).end())


def _flagged_in_text(text: str, sign) -> tuple[set[str], bool]:
    """(người chữ của mục gắn cờ `sign`, mục có lời phủ định đi liền dấu hiệu — "không có đơn ngoài MCP"). Xem ba khuôn ở chú thích phía trên."""
    codes, denied = set(), False
    for clause in FLAG_CLAUSE_END.split(text):
        has_sign = sign.search(clause) is not None
        dropped = []                                            # (đầu, cuối) các đoạn mã không tính
        for subject in FLAG_NEGATED_SUBJECT.finditer(clause):   # khuôn (2)
            if not has_sign or _denied_sign(clause, subject.end(), sign) or not clause[subject.end():].strip(CLAUSE_TAIL):
                dropped.append(subject.span())
        if has_sign:
            for denial in FLAG_DENIAL.finditer(clause):         # khuôn (3)
                reached = _denied_sign(clause, denial.end(), sign)
                if reached:
                    denied = True
                    of_codes = FLAG_OF_CODES.match(clause, reached.end())
                    if of_codes:
                        dropped.append(of_codes.span())
        kept = "".join(" " if any(start <= at < end for start, end in dropped) else char for at, char in enumerate(clause))
        codes |= {code.upper() for code in EMPLOYEE_CODE.findall(FLAG_NEGATED_LIST.sub(" ", kept))}    # khuôn (1)
    return codes, denied


def _person_flags(tool_results: list) -> list[tuple]:
    """[(dấu chữ, {mã NV sở hữu cờ}, KPI của dòng lưu ý — None nếu lời không nêu đúng một KPI)] từ các mục cờ có `employee_ids` (get_data_issues)."""
    flags = []

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("message"), str) and isinstance(value.get("employee_ids"), list) and value["employee_ids"]:
                kpis = _kpis_named(value["message"])
                flags.extend((sign, {str(code).upper() for code in value["employee_ids"]}, next(iter(kpis)) if len(kpis) == 1 else None)
                             for key, sign in FLAG_SIGNS.items() if key in value["message"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    for result in tool_results:
        walk(result)
    return flags


def _flag_problems(answer: str, question: str, tool_results: list) -> list[dict]:
    """Đặc tả bước 4 (DAC_TA_WORKFLOW_KPI_CAPITOL.md): "cờ dữ liệu gán nhầm người ⇒ DỪNG". Câu hỏi về MỘT / vài người (có mã NV — câu hỏi không
    có mã thì lấy mã trong câu trả lời) mà câu trả lời mang chữ của cờ thuộc người KHÁC ⇒ trượt (lỗi K3 09/11: gán cờ của NV14 cho NV05).
    Giới hạn: chỉ xét được khi kết quả công cụ có cờ theo người (get_data_issues); câu phủ định ("NV05 không có cửa hàng ngoài MCP") cũng bị bắt."""
    # kiem_so@18 (13/11) — luật CHA–CON thay cho "gom mọi mã trong câu trả lời": bản @17 lấy MỌI mã trong câu trả lời làm "người được hỏi" khi câu hỏi
    # không có mã ⇒ câu cả đội đúng ("- … ngoài MCP đã bị loại …" ở một gạch, "- ASO_ALL lệch ở NV06, NV09" ở gạch khác) bị chặn oan 2 / 2 lượt trên
    # Capitol sau khi dòng lưu ý cả đội bắt đầu nêu mã người lệch. Các gạch CÙNG CẤP độc lập với nhau; dòng DẪN phía trên truyền mã + dấu cờ xuống gạch con.
    asked = {code.upper() for code in EMPLOYEE_CODE.findall(question)}
    # Mỗi DẤU HIỆU lấy HỢP chủ của mọi cờ mang dấu đó: dấu "ngoài MCP" có 5 cờ với chủ khác nhau theo từng chỉ tiêu; xét
    # từng cờ riêng thì câu ĐÚNG "NV04 có cửa hàng ngoài MCP" bị chặn vì không giao với cờ PC_ON chỉ của NV08.
    # kiem_so@26 (18/11, ca thật Capitol): lớp THÊM lên luật gom chủ — mục nêu ĐÚNG MỘT tên KPI ⇒ người chữ của mục gắn cờ phải là chủ dòng lưu ý CỦA KPI ĐÓ
    # ("- PC_BT: 2 đơn ngoài MCP — NV04, NV14, NV08": NV08 là chủ dòng PC_ON, không phải PC_BT). KPI không có dòng nào mang dấu hiệu, mà mọi dòng của dấu
    # hiệu đều gắn một KPI ⇒ không ai là chủ; riêng câu "không có" ("- ASO_Focus: NV04 không có đơn ngoài MCP.") để luật gom chủ quyết.
    # Mục nêu 0 / ≥ 2 KPI ⇒ chỉ luật gom chủ (câu gộp nhiều KPI của ca thật vẫn qua — giới hạn có test).
    owners_of: dict = {}
    owners_of_kpi: dict = {}
    sign_without_kpi = set()
    for sign, owners, kpi in _person_flags(tool_results):
        owners_of[sign] = owners_of.get(sign, set()) | owners
        if kpi:
            owners_of_kpi[(sign, kpi)] = owners_of_kpi.get((sign, kpi), set()) | owners
        else:
            sign_without_kpi.add(sign)
    details = []
    for sign_text, people in _flag_claims(answer, list(owners_of)):
        people = people or asked          # mục không nêu ai, không có dòng dẫn nêu ai ⇒ là về người được hỏi (ca K3 09/11)
        named = _kpis_named(sign_text)
        for sign, owners in owners_of.items():
            if len(named) == 1 and sign.search(sign_text):
                key = (sign, next(iter(named)))
                marked, denied = _flagged_in_text(sign_text, sign)
                # Mục KHÔNG còn mã nào bị gắn mà có lời phủ định đi liền dấu hiệu ("NV04:" ⏎ "- ASO_Focus: không có đơn ngoài MCP.") là câu "không có cờ" ⇒
                # để luật gom chủ quyết như @26. Mục còn mã bị gắn thì luôn xét — lời phủ định ở đó không cứu được mã.
                no_flag_claim = denied and not marked
                marked = marked if EMPLOYEE_CODE.search(sign_text) else people    # chữ của mục không có mã ⇒ người do dòng dẫn truyền xuống
                if key in owners_of_kpi or (sign not in sign_without_kpi and not no_flag_claim):
                    wrong = ", ".join(sorted(marked - owners_of_kpi.get(key, set())))
                    if wrong:
                        details.append(f"câu trả lời về {wrong} nêu cờ dữ liệu KHÔNG thuộc {wrong} ('{sign.search(sign_text).group(0)}')")
            # MỌI người mục đó nói về phải là chủ của dấu hiệu ("NV04, NV06 có cửa hàng ngoài MCP": NV04 đúng, NV06 sai ⇒ chặn)
            if people - owners and sign.search(sign_text):
                # Lời lý do KHÔNG nêu mã chủ cờ: Capitol đưa nguyên văn lý do vào lời nhắc "làm lại" của Agent (, an toàn — lý do không mang mã /
                # cờ của NV khác người được hỏi). Chữ trích ('…') lấy từ CHÍNH câu trả lời.
                wrong = ", ".join(sorted(people - owners))
                details.append(f"câu trả lời về {wrong} nêu cờ dữ liệu KHÔNG thuộc {wrong} ('{sign.search(sign_text).group(0)}')")
    return [_problem("co_sai_nguoi", detail) for detail in dict.fromkeys(details)]


HEADING_FILLER = re.compile(r"[\s*#>•\-–—,.;:()|]|\bvà\b", re.IGNORECASE)
ITEM_START = re.compile(r"^(?:[-*•+–]|\d{1,2}[.)])\s")
TABLE_RULE = re.compile(r"^[\s|:\-]+$")


SENTENCE_ENDED = re.compile(r"[.!?:…][*_\s]*$")


def _units(answer: str) -> list[tuple[str, str, int | None]]:
    """Cắt câu trả lời thành [(loại, chữ, mức thụt lề nếu là GẠCH — không phải gạch thì None)]: "heading" = dòng kết thúc bằng ":" hoặc chỉ gồm mã NV +
    dấu câu (kể cả khi nó là một gạch — "- NV06:"), và hàng ĐẦU của một bảng; "child" = gạch đầu dòng / mục đánh số / hàng bảng còn lại; "prose" = dòng
    văn thường; "blank" = dòng trống (chỉ để ngắt). Dòng nối tiếp nhập vào mục / đoạn văn ngay trước ⇒ xuống dòng GIỮA CÂU không tách được mã khỏi chữ
    cờ ("- Lưu ý về NV06" ⏎ "có đơn ngoài MCP"); mục trước đã HẾT CÂU (kết thúc bằng . ! ? :) thì dòng không gạch theo sau là một đoạn văn mới."""
    units, in_table = [], False
    for line in answer.splitlines():
        text = line.strip()
        if not text:
            units.append(("blank", "", None))
            in_table = False
            continue
        if "|" in text:
            if not TABLE_RULE.match(text):          # bỏ hàng kẻ |---|---|
                units.append(("child" if in_table else "heading", text, None))
            in_table = True
            continue
        in_table = False
        indent = len(line) - len(line.lstrip()) if ITEM_START.match(text) else None
        bare = text.rstrip("*_ ")
        codes_only = bool(EMPLOYEE_CODE.search(text)) and not HEADING_FILLER.sub("", EMPLOYEE_CODE.sub("", ITEM_START.sub("", text)))
        if bare.endswith(":") or codes_only:
            units.append(("heading", text, indent))
        elif indent is not None:
            units.append(("child", text, indent))
        elif units and units[-1][0] == "prose" or (units and units[-1][0] == "child" and not SENTENCE_ENDED.search(units[-1][1])):
            units[-1] = (units[-1][0], units[-1][1] + " " + text, units[-1][2])      # dòng nối tiếp
        else:
            units.append(("prose", text, None))
    return units


def _flag_claims(answer: str, signs: list) -> list[tuple[str, set[str]]]:
    """[(chữ mang dấu cờ, {mã NV mà chữ đó được hiểu là nói về})] — luật cha–con:
    - gạch con: mã của CHÍNH nó thắng; không có mã ⇒ lấy mã của dòng dẫn gần nhất phía trên (kể cả cách một dòng trống — "NV06:" ⏎ ⏎ "- có đơn ngoài
      MCP"). Dấu cờ của chính nó thắng; không có dấu cờ ⇒ lấy dấu cờ của dòng dẫn ("Các nhân viên có đơn ngoài MCP:" ⏎ "- NV06"; tiêu đề cột của bảng);
    - các gạch CÙNG CẤP không truyền gì cho nhau (gạch 1 có chữ cờ không mã, gạch 2 có mã không chữ cờ ⇒ không ghép);
    - dòng dẫn = tiêu đề hoặc dòng văn thường. Dòng dẫn chỉ là chữ chung (không mã, không dấu cờ — "Lưu ý:") thì không đổi gì, dòng dẫn trước nó còn
      hiệu lực; dòng văn / tiêu đề không mã đứng NGAY SAU một tiêu đề (không cách dòng trống) cũng kế thừa mã tiêu đề đó ("NV06:" ⏎ "Có đơn ngoài MCP.").
    Còn hở (cố ý, ghi rõ): một mục / đoạn liền chứa mã người này và chữ cờ của người khác ở hai CÂU khác nhau bị chặn oan (không tách theo câu — tách
    thì "NV06 có đơn. Đơn đó ngoài MCP." lọt); câu phủ định đúng bị chặn; hai gạch cùng cấp "- Về NV06 thì sao" ⏎ "- có đơn ngoài MCP" lọt (gạch đầu
    không trông như tiêu đề) — cái giá của việc cho các gạch cùng cấp độc lập; tên thật thay mã không được xét (có từ trước).
    ĐO 13/11 (phát lại 942 chữ trả lời thật, so với @17): 0 chữ @17 chặn mà bản này cho qua; 2 chữ bản này chặn THÊM, cả hai là câu ĐÚNG — câu hỏi
    "ai có chỉ tiêu không khớp MCP" trả lời bằng ĐOẠN VĂN có phủ định ("NV14 có … ngoài MCP; đây không phải cảnh báo lệch chỉ tiêu") hoặc hai câu viết
    liền một đoạn; cùng nội dung viết thành gạch đầu dòng thì qua. chấp nhận (chặn oan an toàn hơn để lọt), không nới thêm sau vòng cuối.
    """
    has_sign = lambda text: any(sign.search(text) for sign in signs)
    claims, lead_codes, lead_sign_text, right_after_heading, heading_has_child = [], set(), "", False, False
    lead_indent = None      # mức thụt lề của dòng dẫn khi dòng dẫn là một GẠCH ("- Cửa hàng ngoài MCP đã bị loại:"); dòng dẫn không phải gạch ⇒ None
    for kind, text, indent in _units(answer):
        if kind == "blank":
            # Dòng trống chỉ khép khối của tiêu đề khi tiêu đề ĐÃ có gạch con (đoạn "Lưu ý chung: …" sau các gạch là khối mới). Tiêu đề chưa có gạch con
            # nào mà gặp đoạn văn ("NV06:" ⏎ ⏎ "Có đơn ngoài MCP đã bị loại.") ⇒ đoạn văn là nội dung của tiêu đề ⇒ vẫn kế thừa mã (ca H3)
            right_after_heading = right_after_heading and not heading_has_child
            continue
        # Quan hệ cha–con theo THỤT LỀ (hoàn tất vòng 3, 13/11 — hình câu nháp thật của mô hình bị chặn oan): dòng dẫn là GẠCH chỉ là cha của các gạch
        # thụt lề SÂU hơn nó; gặp gạch cùng mức / nông hơn ⇒ khép khối ("- … ngoài MCP đã bị loại:" ⏎ "  - ASO_ALL: NV04, NV08, NV14." ⏎
        # "- Chỉ tiêu … lệch ở NV06 và NV09." — gạch cuối là anh em của gạch đầu, không phải con). Dòng dẫn là đoạn văn / tiêu đề (không phải gạch):
        # là cha của các gạch liền sau; đã có gạch con mà gặp dòng KHÔNG phải gạch ⇒ khép khối.
        closes = (indent is not None and lead_indent is not None and indent <= lead_indent) or (indent is None and heading_has_child)
        if closes:
            lead_codes, lead_sign_text, lead_indent, right_after_heading, heading_has_child = set(), "", None, False, False
        codes = {code.upper() for code in EMPLOYEE_CODE.findall(text)}
        # Gạch chỉ gồm mã ("- NV06") dưới một dòng dẫn MANG CHỮ CỜ là một dòng của danh sách người ("Các nhân viên có đơn ngoài MCP:" ⏎ "- NV06" ⏎ "- NV04")
        # ⇒ gạch con, không phải tiêu đề mới; không có chữ cờ phía trên thì nó là tiêu đề của các gạch dưới ("- NV06" ⏎ "- Người này có đơn ngoài MCP")
        if kind == "heading" and lead_sign_text and codes and not has_sign(text) and ITEM_START.match(text) and not text.rstrip("*_ ").endswith(":"):
            kind = "child"
        if kind == "child":
            claims.append((text if has_sign(text) else lead_sign_text, codes or lead_codes))
            heading_has_child = True
            continue
        if has_sign(text):
            claims.append((text, codes or (lead_codes if right_after_heading else set())))
        # Luật thụt lề chỉ áp cho dòng dẫn là một gạch KẾT THÚC BẰNG ":" (một câu dẫn — hình câu thật bị chặn oan). Gạch chỉ gồm mã ("- NV06") vẫn là
        # tiêu đề của các gạch CÙNG MỨC phía sau như trước ("- NV06" ⏎ "- Người này có đơn ngoài MCP" phải chặn) ⇒ không ghi mức thụt lề cho nó.
        own_indent = indent if text.rstrip("*_ ").endswith(":") else None
        if codes:
            lead_codes, lead_sign_text, lead_indent = codes, text if has_sign(text) else "", own_indent
        elif has_sign(text):
            lead_codes, lead_sign_text, lead_indent = (lead_codes if right_after_heading else set()), text, own_indent
        # dòng dẫn chỉ là chữ chung: giữ nguyên dòng dẫn trước đó
        if kind == "heading" and (codes or has_sign(text)):
            heading_has_child = False                # tiêu đề mới (không phải dòng dẫn chung) mở khối mới
        right_after_heading = kind == "heading" or (right_after_heading and not codes and not has_sign(text))
    return [(sign_text, people) for sign_text, people in claims if sign_text]


def _with_flag_owners(tool_results: list) -> list:
    """Thêm (KHÔNG sửa) mục phụ: chữ của mỗi cờ có `employee_ids` gắn `employee_id` của từng NV đó — để `owner_check` coi số trong chữ cờ
    ("1 dòng ASO_ALL ngoài MCP") là số CỦA NV đó. 10/11 (K3c, major): get_data_issues ghi `employee_id: "cả đội"` ở gốc ⇒ số trong cờ bị gán
    cho "CẢ ĐỘI" ⇒ câu ĐÚNG về cờ của chính người được hỏi bị chặn — trên Capitol là dừng lượt. Mục phụ dựng từ kết quả công cụ, không từ chữ AI.
    """
    extra = []

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("message"), str) and isinstance(value.get("employee_ids"), list):
                extra.extend(_flag_segments(value["message"], [str(code).upper() for code in value["employee_ids"]]))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    for result in tool_results:
        walk(result)
    return [*tool_results, *extra]


def _flag_segments(message: str, owners: list[str]) -> list[dict]:
    """Chữ một cờ ⇒ mục phụ theo người. Cờ chung nhiều NV ghi "…: NV06 PC_BT: chỉ tiêu 271 ≠ 274; NV09 PC_BT: chỉ tiêu 287 ≠ 289" ⇒ đoạn từ mã NV tới mã kế
    thuộc NV đó; phần trước mã đầu tiên thuộc mọi NV của cờ. bản 2 (R6b): gắn CẢ chữ cho cả hai người ⇒ "NV06 … 287 ≠ 289" (số của NV09) lọt.
    """
    codes = [match for match in EMPLOYEE_CODE.finditer(message) if match.group(0).upper() in owners]
    head = message[:codes[0].start()] if codes else message
    items = [{"employee_id": owner, "flag_message": head} for owner in owners]
    for index, match in enumerate(codes):
        end = codes[index + 1].start() if index + 1 < len(codes) else len(message)
        items.append({"employee_id": match.group(0).upper(), "flag_message": message[match.end():end]})
    return items


def _ratio_clauses(line: str) -> list[str]:
    """Mỗi lần nêu LPPC / LPPC_Meat trong dòng ⇒ vế chứa nó: từ dấu hết vế / tên KPI khác gần nhất phía trước tới cái gần nhất phía sau; số đếm "N KPI"
    thay bằng khoảng trắng."""
    clauses = []
    for kpi in RATIO_KPI.finditer(line):
        cuts_before = [m.end() for m in RATIO_CLAUSE_END.finditer(line, 0, kpi.start())] + \
                      [m.end() for m in OTHER_KPI_NAME.finditer(line, 0, kpi.start())]
        cuts_after = [m.start() for pattern in (RATIO_CLAUSE_END, OTHER_KPI_NAME) for m in [pattern.search(line, kpi.end())] if m]
        clause = line[max(cuts_before, default=0):min(cuts_after, default=len(line))]
        clauses.append(KPI_COUNT.sub(lambda m: " " * len(m.group(0)), clause))
    return clauses


def _ratio_problems(answer: str, tool_results: list) -> list[dict]:
    """Luật "tỷ lệ cũng đúng y số công cụ" cho KPI tỷ lệ (LPPC / LPPC_Meat): dòng nào nêu KPI tỷ lệ kèm số THƯỜNG (không %, không tiền) của
    một NV (mã NV gần nhất đứng trước) thì trong các số đó phải có ĐÚNG ô `actual` của NV đó. 10/11 (K2, major): "LPPC_Meat · 3,8 · 3,8" lọt
    vì 3,8 là chỉ tiêu / mốc của chính NV04 (thật 3,82) — `number_check` chỉ biết "có trong nguồn", `owner_check` chỉ xét khi có chữ "là / đạt".
    Dòng có chữ mốc / chỉ tiêu / cần … không xét (đang nói mốc). Lượt không có ô actual của NV đó ⇒ không xét.
    kiem_so@20 (13/11): chỉ lấy số trong VẾ chứa tên KPI tỷ lệ (`_ratio_clauses`), bỏ số đếm "N KPI" — trước đó lấy cả dòng ⇒ 7 câu đúng bị chặn oan
    trong phát lại 2.038 chữ thật ("NV09 đang có tiền ở 5 KPI: LPPC 2.000.000 đ…", "… tính đến ngày 20/10. Trong đó, LPPC được …"). Vế xét CẢ HAI phía
    tên ⇒ "đạt 6,60 ở LPPC" (số sai đứng trước tên) vẫn bị xét.
    """
    actual = oc.ratio_actual_by_employee(tool_results)
    problems, offset = [], 0
    for line in answer.splitlines(keepends=True):
        start, offset = offset, offset + len(line)
        if not RATIO_KPI.search(line) or TIER_WORDS.search(line):
            continue
        before = EMPLOYEE_CODE.findall(answer[:start] + line[:RATIO_KPI.search(line).start()])
        owner = before[-1].upper() if before else None
        if owner not in actual:
            continue
        plain = [(text, options) for clause in _ratio_clauses(line)
                 for text, options, multiplier, is_percent, is_money in nc._answer_numbers(clause) if not is_percent and not is_money]
        if plain and not any(value in actual[owner] for _, options in plain for value, _ in options):
            problems.append(_problem("so_khong_nguon", f"dòng '{line.strip()[:60]}': số thực hiện KPI tỷ lệ của {owner} phải đúng y công cụ "
                                                       f"({', '.join(str(v) for v in sorted(actual[owner]))}) — có {', '.join(t for t, _ in plain)}"))
    return problems


def _excel_problems(excel_b64: str, tool_results: list, question: str, context) -> list[dict]:
    try:
        raw = base64.b64decode(excel_b64, validate=True)
    except (binascii.Error, ValueError, TypeError):
        return [_problem("dau_vao_loi", "excel_b64 không phải base64 hợp lệ")]
    if len(raw) > MAX_EXCEL_BYTES:
        return [_problem("dau_vao_loi", f"file Excel {len(raw)} byte > trần {MAX_EXCEL_BYTES}")]
    try:
        book = openpyxl.load_workbook(io.BytesIO(raw), data_only=False)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as error:
        return [_problem("dau_vao_loi", f"không mở được file Excel: {type(error).__name__}")]

    # 11/11 (A phân định (f), / E2 / E4): số của câu hỏi chỉ là nguồn khi là GIẢ ĐỊNH < 1.000 — cùng luật phần chữ (`_forgiven_from_question`);
    # trước đó lấy MỌI số câu hỏi ⇒ "Doanh số của tôi là 9.999.999" ghi vào ô hàng không mã NV từng qua
    sources = nc.source_numbers([*tool_results, *context]) | {value for value in _assumed_numbers(question) if abs(value) < QUESTION_NUMBER_LIMIT}
    percent_sources = nc.percent_key_numbers(tool_results)
    owned = oc.numbers_by_employee(tool_results)
    problems = []
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            # Mã NV của hàng = ô chữ đầu tiên bắt đầu bằng NVxx (bảng thưởng: cột "Mã NV")
            owner = next((m.group(1).upper() for cell in row if isinstance(cell.value, str) and (m := EMPLOYEE_CELL.match(cell.value))), None)
            for cell in row:
                where = f"{sheet.title}!{cell.coordinate}"
                if cell.data_type == "f" or (isinstance(cell.value, str) and cell.value.startswith("=")):
                    problems.append(_problem("excel_cong_thuc", f"{where} là công thức ({str(cell.value)[:40]}) — file KPI chỉ ghi giá trị code tính"))
                    continue
                if cell.hyperlink is not None:
                    problems.append(_problem("excel_cong_thuc", f"{where} có link — file KPI không mang link"))
                if isinstance(cell.value, str):
                    # Ô chữ có TIỀN / % ("5.040.000 đ" gõ thành chữ) ⇒ phải có nguồn, cùng luật câu trả lời. Chỉ tiền + %: ô chữ còn có giờ ISO,
                    # mã băm, ghi chú có ví dụ ("0,5414") — bắt mọi số trong chữ ⇒ file THẬT của mình bị chặn nhầm (10/11: 34 ⇒ 4 ô nhầm)
                    problems += [_problem("excel_so_khong_nguon", f"{where} chữ '{cell.value[:40]}' có số '{text}' không có trong kết quả công cụ")
                                 for text, options, multiplier, is_percent, is_money in nc._answer_numbers(cell.value)
                                 if (is_money or is_percent) and not nc._sourced(options, multiplier, is_percent, sources, percent_sources, is_money)]
                    continue
                if isinstance(cell.value, bool) or not isinstance(cell.value, (int, float)):
                    continue
                value = Decimal(str(cell.value))
                # Ô định dạng % lưu 0,5414 cho 54,14 % ⇒ so cả ×100; ô số nguyên lớn coi là tiền (lệch < 1 đồng như number_check)
                options = [(value, 0)] + ([(value * 100, 0)] if "%" in (cell.number_format or "") else [])
                is_money = value == value.to_integral_value() and abs(value) >= 1000
                if not nc._sourced(options, Decimal(1), True, sources, percent_sources, is_money):
                    problems.append(_problem("excel_so_khong_nguon", f"{where} = {cell.value} — không có trong kết quả công cụ của lượt"))
                elif owner and owner in owned and not nc._sourced(options, Decimal(1), True, owned[owner] | owned.get("TEAM", set()), set(), is_money):
                    problems.append(_problem("excel_so_sai_nguoi", f"{where} = {cell.value} — hàng của {owner} nhưng số không thuộc {owner}"))
    return problems
QUESTION_NUMBER_LIMIT = 1000   # số câu hỏi chỉ là nguồn khi < 1.000 — số đếm / giả định nhỏ; tiền, doanh số luôn ≥ 1.000 đồng
# Bản 6 (10/11 theo đề xuất B): số của câu hỏi chỉ là GIẢ ĐỊNH khi trong câu hỏi nó đứng sau một từ giả định, cách ≤ 2 từ ("Nếu NV04 thêm 3
# cửa hàng"). Bản 5 lọt: "Tôi có 80 cửa hàng ASO_ALL rồi phải không?" ⇒ "Đúng, bạn có 80 …" qua (lời người hỏi thành số thật — B thử trên 16b46a9).
ASSUMPTION_BEFORE = re.compile(r"\b(?:thêm|nếu|giả sử|giả định|tăng|bớt)(?:\s+[^\s\d]+){0,2}\s*$", re.IGNORECASE)


def _assumed_numbers(question: str) -> set[Decimal]:
    """Giá trị các số người hỏi nêu làm giả định (đứng sau từ giả định) — chỉ những số này được tha ở `_forgiven_from_question`."""
    text = nc.RULE_VERSION_TOKEN.sub(" ", question or "")
    return {value for match in nc.NUMBER.finditer(text) if ASSUMPTION_BEFORE.search(text[:match.start()])
            for value in nc._values_with_unit(match)}


def _forgiven_from_question(text: str, asked: set[Decimal], money_or_percent: bool) -> bool:
    """Số thiếu nguồn `text` có trong câu hỏi chỉ được tha khi là số THƯỜNG (không tiền / %), nhỏ hơn 1.000 và là số GIẢ ĐỊNH của câu hỏi (`asked` =
    `_assumed_numbers`) — "thêm 3 cửa hàng". Bản 5 (điều kiện Đ-4 khi Rào Capitol gửi kèm câu hỏi): số câu hỏi KHÔNG làm nguồn cho số kết quả — câu hỏi
    "doanh số của tôi là 9.999.999, thưởng bao nhiêu?" mà trả lời lấy 9.999.999 làm số thật ⇒ chặn. Bản 4 còn tha tiền / % của câu hỏi khi trước số không
    có mã NV — bỏ. So theo giá trị đầy đủ (3.000.000 không khớp 3). Tên sản phẩm có số ("Trà 365") đã che riêng (`PRODUCT_NAME`).
    """
    match = nc.NUMBER.search(text)
    if match is None or money_or_percent:
        return False
    return any(abs(value) < QUESTION_NUMBER_LIMIT for value in nc._values_with_unit(match) & asked)


def _mask_answer(text: str) -> str:
    """Che mã kỳ + tên sản phẩm có số ("Trà 365") bằng khoảng trắng CÙNG độ dài ⇒ vị trí chữ / dòng không lệch (`_ratio_problems` đọc theo dòng +
    mã NV đứng trước)."""
    blank = lambda match: " " * len(match.group())
    return PRODUCT_NAME.sub(blank, PERIOD_CODE.sub(blank, text))
def _owner_problems_masked(answer: str, question: str, tool_results: list, context, name_entries: dict | None) -> list[dict]:
    """Phần "số / cờ thuộc ĐÚNG người" của rào trên câu trả lời ĐÃ che (`_mask_answer`)."""
    # Số ĐỨNG SAU một mã NV phải thuộc đúng NV đó trong kết quả công cụ — KỂ CẢ số có trong câu hỏi ( bản 3, 10/11: luật bản 2 đoán chữ mở đầu
    # "đúng / không sai" bị lách — C R3c "…, đúng như anh nói", R3d "Không sai, …"). ⇒ owner_check KHÔNG nhận câu hỏi làm nguồn miễn.
    # Cái giá (ghi rõ): câu bác bỏ có NHẮC LẠI số sai sau mã NV ("NV02 được 5.040.000 đ, không phải 4.123.456 đ") cũng bị chặn.
    owner_sources = _with_flag_owners(tool_results)
    owners = oc.check_owners(answer, owner_sources, "", context, same_sentence_after=True, name_entries=name_entries,
                             item_question=question)   # 10/11: số viết trước mã trong cùng câu
    problems = [_problem("so_sai_nguoi", text) for text in owners["problems"]]
    problems += _flag_problems(answer, question, tool_results)
    problems += _ratio_problems(answer, tool_results)
    return problems


def _period_problems(raw_answer: str, tool_results: list) -> list[dict]:
    """Kỳ nêu sai (kiem_so@31) — đọc câu trả lời CHƯA che: `_mask_answer` xoá mã kỳ, mà lớp này cần đúng chữ đó.
    Giới hạn: đường chat trên máy (`chat._check_answer`) không gọi `kiem_so` / `owner_problems` ⇒ chưa có luật kỳ (việc riêng)."""
    return [_problem("ky_sai", text) for text in period_check.period_problems(raw_answer, tool_results)]


def owner_problems(answer: str, question: str, tool_results: list, context: list | tuple = (), name_entries: dict | None = None) -> list[dict]:
    """Phần "số / cờ thuộc đúng người" của rào — MỘT hàm cho cả hai lối vào: Rào Capitol (`kiem_so`) và đường chat trên máy
    (`chat._check_answer`). Trước đó đường chat gọi thẳng `check_owners` với kết quả công cụ thô ⇒ số trong chữ cờ không gắn chủ (chặn oan câu cờ có số
    2 / 3 lượt — 12:20 13/11), và không có phần cờ gán sai người / KPI tỷ lệ đúng y.
    """
    return _period_problems(answer, tool_results) + _owner_problems_masked(_mask_answer(answer), question, tool_results, context, name_entries)


def kiem_so(payload: dict, run_tool=None, context: list | tuple = (), name_entries: dict | None = None) -> dict:
    """{"dat", "loi": [{"loai", "chi_tiet"}], "ly_do", "phien_ban"} — chỉ `dat: true` là đạt.

    `payload`: answer (bắt buộc) · question · tool_calls HOẶC tool_results · excel_b64 (tuỳ chọn). `run_tool(name, args) -> dict`: bên gọi đưa hàm gọi
    công cụ KPI (vd `backend_client.call_tool` gắn ctx). `context`: chuỗi code điền vào dòng ngữ cảnh (kỳ, ngày chốt, số người — `chat.context_numbers`).
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("answer"), str):
        return _result([_problem("dau_vao_loi", "payload phải là bảng có answer (chữ)")])
    question = payload.get("question") or ""
    if not isinstance(question, str):
        return _result([_problem("dau_vao_loi", "question phải là chữ")])
    tool_results, problems = _sources(payload, run_tool)
    if problems:
        return _result(problems)
    answer = _mask_answer(payload["answer"])
    # owner_check bỏ qua số không có nguồn ở đâu (để number_check bắt) ⇒ number_check cũng chạy KHÔNG có câu hỏi; số của câu hỏi chỉ được tha khi
    # là số GIẢ ĐỊNH thường < 1.000 (bản 6 — `_assumed_numbers`, `_forgiven_from_question`).
    asked = _assumed_numbers(question)
    sources, percent_sources = nc.source_numbers([*tool_results, *context]), nc.percent_key_numbers(tool_results)
    numbers = {"unsourced": [text for text, options, multiplier, is_percent, is_money in nc._answer_numbers(oc.mask_people_counts(answer))
                             if not nc._sourced(options, multiplier, is_percent, sources, percent_sources, is_money)
                             and not _forgiven_from_question(text, asked, is_money or is_percent)]}
    problems = [_problem("so_khong_nguon", f"số '{text}' trong câu trả lời không có trong kết quả công cụ") for text in numbers["unsourced"]]
    problems += _period_problems(payload["answer"], tool_results)
    problems += _owner_problems_masked(answer, question, tool_results, context, name_entries)
    if payload.get("excel_b64") is not None:
        problems += _excel_problems(payload["excel_b64"], tool_results, question, context)
    return _result(problems)


def kiem_excel(excel_b64: str, tool_results: list, context: list | tuple = ()) -> dict:
    """Chỉ kiểm file Excel (cùng khuôn ra) — cho bước xuất / nhận Excel: Rào chắn Capitol chỉ có cổng CHỮ, không nhận file (-27 10/11, AB
    `nodes/governance/guardrails/params.py:91-109`) ⇒ file kiểm ở bước riêng. `tool_results` = kết quả công cụ KPI do hệ thống ghi / gọi."""
    if not isinstance(excel_b64, str) or not isinstance(tool_results, list) or not all(isinstance(item, dict) for item in tool_results):
        return _result([_problem("dau_vao_loi", "cần excel_b64 (chữ base64) + tool_results (danh sách bảng)")])
    return _result(_excel_problems(excel_b64, tool_results, "", context))
