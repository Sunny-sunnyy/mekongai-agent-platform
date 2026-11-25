"""Trình bày câu trả lời bằng CODE (bậc 03/11,) — dùng chung cho server và bộ đo (cùng một hàm).

Vì sao code chứ không lời dặn: bậc "dặn mô hình không markdown" KHÔNG ĐẠT, đã gỡ (`KET_QUA.md` 2h). Ở đây mô hình vẫn đọc y chữ cũ;
code chỉ sửa cách TRÌNH BÀY câu trả lời đã qua kiểm số: bỏ markdown (giữ chữ), đổi số kiểu Anh sang kiểu Việt.
Rào: giá trị mọi con số trước / sau khi đổi phải bằng nhau theo thứ tự — lệch ⇒ trả chữ GỐC (đổi cách viết không được đổi số)."""
import re
from decimal import Decimal

from kpi_agent.agent.number_check import MULTIPLIERS, NUMBER, RULE_VERSION_TOKEN, SKIP_PATTERNS, check_numbers, readings

# Ảnh / link markdown và URL trần (P2c, C rà bảo mật R6 — mô hình từng nhét số tiền vào URL) — dời từ server.py về đây
MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
BARE_URL = re.compile(r"\b(?:https?|ftp)://\S+", re.IGNORECASE)

TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
HEADING = re.compile(r"^(\s*)#{1,6}\s*")
BULLET = re.compile(r"^(\s*)[*+]\s+")
BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
# Số kiểu Anh rõ ràng: nhóm nghìn bằng phẩy (3,600,000 — có thể kèm .lẻ) · phần lẻ bằng chấm 1–2 chữ số (90.08). Không đụng ngày 20/10/2026, 2026-10
# 1 nhóm ("1,940") mơ hồ (1940 hay 1,94) ⇒ chỉ đổi khi ≥ 2 nhóm, hoặc 1 nhóm mà có "đ" / "đồng" theo sau (tiền luôn là số nguyên đồng)
ENGLISH_GROUPED = re.compile(r"(?<![\d.,/\-])(\d{1,3}(?:,\d{3}){2,}|\d{1,3},\d{3}(?=\s*(?:đ|đồng)\b))(\.\d+)?(?![\d,/])")
# Cuối câu "đạt mốc Mức 2: 4.7." — dấu chấm câu theo sau KHÔNG chặn (chỉ chặn khi sau đó là chữ số / "/": 20.10.2026, 4.7/5)
ENGLISH_DECIMAL = re.compile(r"(?<![\d.,/\-])(\d+)\.(\d{1,2})(?!\d|\.\d|/)")
PERCENT_STUCK = re.compile(r"(\d)%")
# 03/11 (major): "mục 2.3", "Mục 9.1", "bước 3.1" là SỐ MỤC, không phải số thập phân (lời dặn bắt nói mục công văn — 82/387 câu đã lưu có dạng này);
# "v3.7" (chữ cái dính ngay trước) là phiên bản ⇒ không đổi
# bản cuối 2: cả DANH SÁCH sau từ khoá — "mục 2.2, 2.3, 2.7", "mục 6.1 và 6.3", "mục 9.1, 10.1–10.3", "các mục 2.1 đến 2.8"
SECTION_WORD_BEFORE = re.compile(r"(?:mục|điều|khoản|bước|phần|chương|mẫu|tiểu mục|section)\s*"
                                 r"(?:\d+(?:\.\d+)*\s*(?:,|và|hoặc|đến|tới|–|-|—)\s*)*$", re.IGNORECASE)
# 13/11 (người kiểm độc lập, 4 / 4 câu): tên khoản Thưởng thêm "Thưởng 7.1 / 7.2 / 7.3" bị đổi thành "Thưởng 7,1" — mô hình viết đúng, bước này đổi.
# "thưởng" khác các chữ trên ở chỗ nó cũng đứng trước SỐ ĐO ("được thưởng 1.5 triệu") ⇒ chỉ coi là tên khoản khi sau số KHÔNG có đơn vị tiền / %.
REWARD_ITEM_BEFORE = re.compile(r"\bthưởng\s*(?:\d+(?:\.\d+)*\s*(?:,|và|hoặc|đến|tới|–|-|—)\s*)*$", re.IGNORECASE)
UNIT_AFTER = re.compile(r"\s*(?:%|đ\b|đồng|vnđ|vnd|triệu|tr\b|nghìn|ngàn|k\b|tỷ|ty\b)", re.IGNORECASE)


def strip_links(text: str) -> tuple[str, int]:
    """Bỏ ảnh markdown, link markdown (giữ chữ hiện), URL trần ⇒ (chữ đã bỏ, số chỗ đã bỏ)."""
    text, images = MARKDOWN_IMAGE.subn("[ảnh đã bỏ]", text)
    text, links = MARKDOWN_LINK.subn(r"\1", text)
    text, urls = BARE_URL.subn("[link đã bỏ]", text)
    return text, images + links + urls


def _plain_markdown(text: str) -> str:
    lines = []
    for line in text.split("\n"):
        if TABLE_SEPARATOR.match(line) and "-" in line:
            continue
        if line.strip().startswith("|"):
            line = " · ".join(cell.strip() for cell in line.strip().strip("|").split("|"))
        line = BULLET.sub(r"\1- ", HEADING.sub(r"\1", line))
        lines.append(line)
    return BOLD.sub(lambda m: m.group(1) or m.group(2), "\n".join(lines)).replace("**", "")


def _vietnamese_numbers(text: str) -> str:
    def convert(part: str) -> str:
        part = ENGLISH_GROUPED.sub(lambda m: m.group(1).replace(",", ".") + (("," + m.group(2)[1:]) if m.group(2) else ""), part)
        part = ENGLISH_DECIMAL.sub(lambda m: m.group(0) if _is_label_number(part, m.start(), m.end()) else f"{m.group(1)},{m.group(2)}", part)
        return PERCENT_STUCK.sub(r"\1 %", part)
    # Không đụng mã bản luật "rules_2026_10.yaml@0ef1182e"
    pieces, last = [], 0
    for match in RULE_VERSION_TOKEN.finditer(text):
        pieces += [convert(text[last:match.start()]), match.group(0)]
        last = match.end()
    return "".join(pieces + [convert(text[last:])])


def _is_label_number(text: str, start: int, end: int) -> bool:
    """Số đứng sau chữ "mục / điều / khoản / bước / …", sau chữ "Thưởng" mà không kèm đơn vị (tên khoản "Thưởng 7.1"), hoặc dính ngay sau chữ cái
    (v3.7) ⇒ là nhãn, không phải số thập phân."""
    before = text[max(0, start - 120):start]
    return bool(SECTION_WORD_BEFORE.search(before)) or (start > 0 and text[start - 1].isalpha()) or \
        bool(REWARD_ITEM_BEFORE.search(before) and not UNIT_AFTER.match(text[end:]))


def number_values(text: str) -> list[frozenset]:
    """Mỗi con số trong chữ (bỏ mã NV, số thứ tự, ngày / tháng / năm, mã bản luật — như bộ kiểm số) ⇒ tập giá trị có thể."""
    for pattern in SKIP_PATTERNS:
        text = pattern.sub(" ", text)
    values = []
    for match in NUMBER.finditer(text):
        multiplier = MULTIPLIERS.get((match.group(2) or "").strip().lower(), Decimal(1))
        values.append(frozenset(value * multiplier for value, _ in readings(match.group(1))))
    return values


def _same_numbers(before: str, after: str) -> bool:
    a, b = number_values(before), number_values(after)
    # x == y: chuỗi bộ đọc số không đọc được ("20.10.2026", "6.2.10" ⇒ tập rỗng) mà KHÔNG đổi thì vẫn là "bằng"
    return len(a) == len(b) and all(x == y or x & y for x, y in zip(a, b))


def format_answer(text: str) -> tuple[str, dict]:
    """⇒ (chữ đã trình bày, {"kept_original": bool}). Rào: bước đổi cách viết số làm lệch giá trị ⇒ trả chữ gốc.

    Rào so SAU bước bỏ markdown (bước đó không đụng chữ số): "__3.600.000 đ__" — "_" dính số làm bộ đọc số không thấy số ở bản gốc
    (E thấy bằng test 03/11) ⇒ so với bản gốc thô sẽ báo lệch nhầm.
    """
    plain = _plain_markdown(text)
    formatted = _vietnamese_numbers(plain)
    if not _same_numbers(plain, formatted):
        return text, {"kept_original": True}
    return formatted, {"kept_original": False}


def present_record(record: dict, context: list[str]) -> dict:
    """Bước trình bày cuối của MỘT câu — server và bộ đo gọi cùng hàm này (đo đúng thứ người dùng thấy).

    1) bỏ ảnh / link (P2c); 2) bỏ markdown + số kiểu Việt; 3) kiểm số chạy lại trên chữ đã đổi — câu đã đạt mà nay trượt ⇒ giữ chữ trước bước 2.
    Bản ghi giữ chữ trước mỗi bước (`answer_before_link_strip`, `answer_before_presentation`) để tra lại.
    """
    record.setdefault("model_text", record["answer"])   # bản ghi cũ (trước P2c) chưa có — phát lại vẫn chạy được
    answer, removed = strip_links(record["answer"])
    if removed:
        record.update({"answer_before_link_strip": record["answer"], "answer": answer,
                       "model_text": strip_links(record["model_text"])[0], "links_removed": removed})
    formatted, info = format_answer(record["answer"])
    kept = info["kept_original"]
    if formatted != record["answer"] and not kept:
        args = (record.get("tool_results", []), record.get("question", ""), context)
        if check_numbers(record["answer"], *args)["ok"] and not check_numbers(formatted, *args)["ok"]:
            kept = True
        else:
            record.update({"answer_before_presentation": record["answer"], "answer": formatted,
                           "model_text": format_answer(record["model_text"])[0]})
    record["presentation"] = {"changed": "answer_before_presentation" in record, "kept_original": kept}
    return record
