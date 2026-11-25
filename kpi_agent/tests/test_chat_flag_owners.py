"""Đường chat gắn chủ cho chữ cờ trước khi xét số (hợp nhất lối vào rào chat ↔ Capitol, bản tối thiểu đã chọn).

Ca thật 12:20 13/11: câu trả lời đúng nêu số của chính NV06 trong chữ cờ bị chặn oan trên trang chat (Capitol thì qua), vì đường chat đưa kết
quả công cụ THÔ cho `check_owners` — số trong chữ cờ không thuộc đúng người. Chữ cờ + câu nháp dưới đây chép từ bản ghi thật câu 513 (lượt
20261109_115239, chỉ mã NV)."""
from datetime import date

import pytest

from kpi_agent.agent.backend_client import AgentContext
from kpi_agent.agent.chat import _check_answer
from kpi_agent.agent.owner_check import check_owners

FLAG = {"level": "info", "level_label": "thông tin", "scope": "nhan_vien", "employee_ids": ["NV06", "NV09"],
        "message": "lệch (MCP có thể đổi sau ngày chốt mẫu số 28/T-1): NV06 PC_BT: chỉ tiêu 271 ≠ tính từ MCP 274; "
                   "NV06 ASO_ALL: chỉ tiêu 131 ≠ tính từ MCP 132; NV09 PC_BT: chỉ tiêu 287 ≠ tính từ MCP 289; "
                   "NV09 ASO_ALL: chỉ tiêu 152 ≠ tính từ MCP 153"}
# Bản ghi thật: lúc kiểm câu nháp chỉ có MỘT kết quả — get_data_issues cả đội (lượt gọi riêng NV06 / NV09 đến sau, khi làm lại)
FLAG_RESULTS = [{"employee_id": "cả đội", "period": "2026-10", "issues": [FLAG]}]
CTX = AgentContext(backend_url="http://127.0.0.1:1", period="2026-10", cutoff=date(2026, 10, 31),
                   employee_ids=[f"NV{n:02d}" for n in range(1, 15)], kpis=["PC_BT", "ASO_ALL"], data_flags=[])
QUESTION = "Bạn nào có chỉ tiêu không khớp với danh sách cửa hàng MCP?"
TRUE_ANSWER = ("NV06 và NV09 có chỉ tiêu không khớp khi đối chiếu với MCP:\n\n"
               "- **NV06:** PC_BT 271, MCP tính lại 274; ASO_ALL 131, MCP tính lại 132.\n"
               "- **NV09:** PC_BT 287, MCP tính lại 289; ASO_ALL 152, MCP tính lại 153.\n")
# Ca xấu: đổi số của NV09 sang dòng NV06
SWAPPED_ANSWER = TRUE_ANSWER.replace("PC_BT 271, MCP tính lại 274", "PC_BT 287, MCP tính lại 289", 1)


def test_old_entry_blocked_the_true_answer():
    # Ghi lại gốc lỗi: lối cũ (kết quả công cụ thô) chặn câu đúng
    assert not check_owners(TRUE_ANSWER, FLAG_RESULTS, QUESTION, [])["ok"]


def test_number_of_own_flag_passes():
    _, owner = _check_answer(TRUE_ANSWER, FLAG_RESULTS, QUESTION, CTX)
    assert owner["ok"], owner["problems"]


def test_number_of_other_person_flag_is_blocked():
    _, owner = _check_answer(SWAPPED_ANSWER, FLAG_RESULTS, QUESTION, CTX)
    assert not owner["ok"]


# ---------- kiem_so@20 (13/11): câu 201 thật trên đường chat còn ⚠ 2 / 3 lượt vì giờ / số mục công văn bị coi là số của NV (C, KIEM_RAO_HOP_NHAT_C.md) ----------
ISSUE_LINE = "- **NV06:** PC_BT 271, MCP tính lại 274.\n- **NV09:** PC_BT 287, MCP tính lại 289.\n"


def test_time_section_numbers_and_short_date_are_not_employee_numbers():
    for tail in ("Lưu ý: pivot làm mới 30/10/2026 00:00; số liệu RAW DATA tới ngày đơn 30/10/2026.",
                 "Các mục công văn liên quan: 2,2, 2,3, 2,7, 2,8, 8,1 và 8,2.",
                 "Có cửa hàng/đơn ngoài MCP đã bị loại theo công văn 6,2; PC_BT chưa đối chiếu được.",
                 "Số liệu tính đến 20/10."):
        _, owner = _check_answer(ISSUE_LINE + tail, FLAG_RESULTS, QUESTION, CTX)
        assert owner["ok"], (tail, owner["problems"])


def test_swapped_flag_numbers_still_blocked_with_new_masks():
    # Ca R4: số NV06 gán NV09 — kèm đuôi có giờ / số mục vẫn CHẶN
    answer = "- **NV09:** PC_BT 271, MCP tính lại 274.\nCác mục công văn liên quan: 2,2 và 8,2; pivot làm mới 00:00."
    _, owner = _check_answer(answer, FLAG_RESULTS, QUESTION, CTX)
    assert not owner["ok"]


def test_number_without_section_word_is_still_checked():
    # Ca lách A: "NV06 đạt 6.2 điểm" (không có chữ mục / công văn / điều) ⇒ vẫn xét (số không nguồn)
    numbers, owner = _check_answer("NV06 đạt 6.2 điểm.", FLAG_RESULTS, QUESTION, CTX)
    assert not (numbers["ok"] and owner["ok"])


def test_money_after_section_word_is_not_masked():
    # "công văn 3.000.000 đ" / "điều 5 triệu": tiền sau chữ mục không được che (@7, minor 2)
    from kpi_agent.agent.owner_check import NOT_EMPLOYEE_NUMBERS
    for text in ("NV06 theo công văn 3.000.000 đ", "NV06 theo điều 5 triệu", "NV06 mục 2, 5 triệu"):
        masked = text
        for pattern in NOT_EMPLOYEE_NUMBERS:
            masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
        assert "3.000.000" in masked or "5 triệu" in masked, (text, masked)


def test_chat_number_before_code_belongs_to_that_code():
    # kiem_so@22 vòng sửa (C B-C4): "<số> đ là tiền PC_BT của NV04" — Capitol chặn, chat từng cho qua (thiếu same_sentence_after)
    results = [{"employee_id": "NV04", "kpis": [{"kpi": "LPPC", "amount": 2000000}, {"kpi": "PC_BT", "amount": 2200000}]}]
    _, wrong = _check_answer("2.000.000 đ là tiền PC_BT của NV04.", results, "NV04 được bao nhiêu?", CTX)
    _, right = _check_answer("2.200.000 đ là tiền PC_BT của NV04.", results, "NV04 được bao nhiêu?", CTX)
    assert not wrong["ok"] and right["ok"], (wrong, right)


def test_chat_question_number_exempt_only_when_negated():
    # kiem_so@22 (trên ĐƯỜNG CHAT — trang của User): số có trong câu hỏi không còn được miễn ở câu KHẲNG ĐỊNH
    results = [{"employee_id": "NV04", "kpis": [{"kpi": "LPPC", "amount": 2000000}, {"kpi": "PC_BT", "amount": 2200000}]}]
    question = "NV04 có được 2.000.000 đ PC_BT không?"
    _, yes = _check_answer("Có, NV04 được 2.000.000 đ PC_BT.", results, question, CTX)
    _, no = _check_answer("Không, NV04 không được 2.000.000 đ PC_BT; NV04 được 2.200.000 đ PC_BT.", results, question, CTX)
    assert not yes["ok"] and no["ok"], (yes, no)


@pytest.mark.parametrize("answer", [
    "Không sai, NV04 được 2.000.000 đ PC_BT.",           # C K4c — "không" ở XA số, câu khẳng định
    "Có. Hỏi đúng rồi, NV04 được 2.000.000 đ PC_BT.",    # C K4d — chữ "hỏi" không còn mở miễn
    "Như bạn hỏi, NV04 được 2.000.000 đ PC_BT.",         # C K4e
    "Không sai, NV04 được **2.000.000 đ** PC_BT.",       # chữ đậm sát số không mở lối miễn cho câu khẳng định
])
def test_chat_question_number_far_negation_or_echo_still_checked(answer):
    # Vòng sửa @22 (16/11): chỉ miễn khi phủ định đứng SÁT trước số
    results = [{"employee_id": "NV04", "kpis": [{"kpi": "LPPC", "amount": 2000000}, {"kpi": "PC_BT", "amount": 2200000}]}]
    _, owners = _check_answer(answer, results, "NV04 có được 2.000.000 đ PC_BT không?", CTX)
    assert not owners["ok"], owners


@pytest.mark.parametrize("answer", [
    "Không, NV04 **không** được 2.000.000 đ PC_BT; NV04 được 2.200.000 đ PC_BT.",
    "Không, NV04 không được **2.000.000 đ** PC_BT; NV04 được **2.200.000 đ** PC_BT.",
])
def test_chat_question_number_negated_with_markdown_bold_passes(answer):
    # C minor 58cdff61 (16/11): chữ đậm Markdown giữa phủ định và số từng làm chặn oan câu đúng
    results = [{"employee_id": "NV04", "kpis": [{"kpi": "LPPC", "amount": 2000000}, {"kpi": "PC_BT", "amount": 2200000}]}]
    _, owners = _check_answer(answer, results, "NV04 có được 2.000.000 đ PC_BT không?", CTX)
    assert owners["ok"], owners
