"""Phép kiểm "số có nguồn" (G4a) — cách đọc số Việt / Anh, làm tròn, hệ số triệu, và câu bịa số bị bắt."""
from decimal import Decimal

import pytest

from kpi_agent.agent.number_check import check_numbers, readings

TOOL_RESULT = {"employee_id": "NV04", "business_salary": 5740000, "pct": 88.89,
               "reason": "ASO_Focus: thực hiện 88.89% — chưa tới mốc thấp nhất 'Đạt 90%' ⇒ 0 đ",
               "gap_to_next": 384601087.9, "data_flags": ["pivot làm mới lần cuối 21/10/2026 14:16"]}


@pytest.mark.parametrize("text, expected", [
    ("5.040.000", {Decimal("5040000")}), ("5,040,000", {Decimal("5040000")}), ("54,15", {Decimal("54.15")}),
    ("54.15", {Decimal("54.15")}), ("2.200", {Decimal("2200"), Decimal("2.200")}), ("1.234,5", {Decimal("1234.5")}),
])
def test_readings(text, expected):
    assert {value for value, _ in readings(text)} == expected


@pytest.mark.parametrize("answer", [
    "NV04 được 5.740.000 đ, ASO_Focus đạt 88,89 % — chưa tới mốc 90 %.",
    "Tổng 5,74 triệu đồng; ASO_Focus 88,89%.",                      # hệ số triệu; % đúng số công cụ (trước: "88,8%" / "88,9%" đạt)
    "ASO_Focus đạt 88,890 %.",                                       # cùng giá trị, khác cách viết
    "Doanh số còn thiếu 384.601.088 đồng.",                        # nguồn 384601087.9 — lệch dưới 1 đồng
    "Số liệu chụp ngày 21/10/2026 lúc 14:16.\n1. ASO_Focus 0 đ\n2. Tổng 5.740.000 đ",   # ngày + số thứ tự danh sách
])
def test_sourced_answers_pass(answer):
    assert check_numbers(answer, [TOOL_RESULT])["ok"], check_numbers(answer, [TOOL_RESULT])


@pytest.mark.parametrize("answer, bad", [
    ("NV04 được 2.200.000 đ tiền PC_BT.", "2.200.000"),            # số không có trong kết quả công cụ (bịa)
    ("ASO_Focus đạt 91 %.", "91"),
    ("ASO_Focus đạt 88,9 %.", "88,9"),                 # % làm tròn lên — sai (% đúng số công cụ)
    ("ASO_Focus đạt 88,8 %.", "88,8"),                 # % làm tròn xuống — cũng sai
    ("NV04 được khoảng 7 triệu.", "7 triệu"),          # 5,74 triệu làm tròn không ra 7
])
def test_unsourced_numbers_are_caught(answer, bad):
    result = check_numbers(answer, [TOOL_RESULT])
    assert not result["ok"] and bad in result["unsourced"]


def test_answer_without_tool_call_is_caught():
    # Câu ví dụ của lộ trình chủ đề 3: trả "2.200.000 đ" mà không gọi công cụ nào
    assert not check_numbers("NV05 được 2.200.000 đ tiền PC_BT.", [], question="NV05 được bao nhiêu tiền PC_BT?")["ok"]


def test_numbers_from_question_are_sourced():
    assert check_numbers("Kỳ tháng 11 chưa có dữ liệu.", [], question="tính thưởng tháng 11 cho NV05")["ok"]


# G4a (KIEM_KPI_B.md mục G4a): major — số ×100 được coi là có nguồn; 2 minor về cách viết số / ngày
@pytest.mark.parametrize("answer", ["NV04 được 574.000.000 đ.", "NV04 được 574 triệu đồng."])
def test_times_100_of_source_is_caught(answer):
    assert not check_numbers(answer, [TOOL_RESULT])["ok"]


def test_space_thousand_separator_is_read():
    assert check_numbers("NV04 được 5 740 000 đ.", [TOOL_RESULT])["ok"]
    assert not check_numbers("NV04 được 5 750 000 đ.", [TOOL_RESULT])["ok"]


def test_day_month_words_are_not_money():
    assert check_numbers("Hạn xuất hoá đơn là 22h ngày 02 tháng 11.", [{"flag": "trước 22h ngày 02 tháng T+1"}])["ok"]


# G4a-2 (minor mới): làm tròn LÊN bằng số thực ⇒ thiếu 1,1 hiện 1,11
@pytest.mark.parametrize("value, shown", [(1.1, 1.1), (2.2, 2.2), (0.581, 0.59), (4.7, 4.7)])
def test_round_up_has_no_float_error(value, shown):
    from kpi_agent.backend.queries import _shown
    assert _shown("LPPC", value, need_at_least=True) == shown


# 02/11 tối (bẫy "làm tròn sát mốc"): phần dư số thực có sẵn và % / LPPC sát mốc không được hiện thành "đã chạm mốc"
def test_float_residue_does_not_round_up():
    from kpi_agent.backend.queries import _shown
    assert _shown("LPPC_Meat", 3.8000000000000003, need_at_least=True) == 3.8      # B ghi chú G4a-3: từng hiện 3,81


@pytest.mark.parametrize("fraction, shown", [(0.94995, "94.99%"), (0.9499999, "94.99%"), (0.95, "95.00%"), (0.5415, "54.15%")])
def test_pct_near_tier_is_floored_in_reason(fraction, shown):
    from kpi_agent.core.compute_bonus import bonus_for_kpi
    from kpi_agent.core.compute_kpi import KpiResult
    from kpi_agent.core.rules import load_rules
    rules = load_rules("kpi_agent/config/rules_2026_10.yaml")
    line = bonus_for_kpi(KpiResult("Turnover", 1_000_000, fraction * 1_000_000, 0, 1, ""), rules, "CD - Nam Trung Bộ")
    assert shown in line.reason
    if fraction < 0.95:
        assert line.amount == 0 and "95.00%" not in line.reason


def test_lppc_near_tier_is_floored():
    from kpi_agent.backend.queries import _shown
    assert _shown("LPPC", 4.6999) == 4.69        # không hiện 4,70 khi mốc là 4,7 mà chưa đạt


def test_trace_numbers_names_the_tool_that_has_each_number():
    from kpi_agent.agent.number_check import trace_numbers
    results = [("get_employee_result", {"business_salary": 5740000}), ("explain_kpi", {"pct": 88.89, "amount": 0})]
    rows = trace_numbers("NV04 được 5.740.000 đ, ASO_Focus đạt 88,89 %, thiếu 9 cửa hàng", results, "NV04 thiếu mấy cửa hàng?")
    assert rows == [{"number": "5.740.000", "sources": ["get_employee_result"]},
                    {"number": "88,89", "sources": ["explain_kpi"]},
                    {"number": "9", "sources": []}]


# Bậc "nguồn số" (ghi trước 12:20 03/11): số code điền ở dòng ngữ cảnh có nguồn; mã bản luật không phải con số
CONTEXT = ["2026-10", "20/10/2026", "2026-10-20", "14"]


def test_context_numbers_are_sourced_only_when_given():
    answer = "Kỳ 2026-10, số tính tới 20/10/2026, đội 14 người — tạm tính."
    assert check_numbers(answer, [], "Số chốt ngày nào?", CONTEXT)["ok"] is True
    assert check_numbers(answer, [], "Số chốt ngày nào?")["ok"] is False                    # không truyền ngữ cảnh ⇒ như cũ


def test_rule_version_token_is_not_a_number():
    tool = {"rule_version": "rules_2026_10.yaml@0ef1182e", "status_label": "tạm tính — chưa phải số chốt, chưa duyệt", "amount": 400000}
    assert check_numbers("Đang tính theo rules_2026_10.yaml@0ef1182e.", [], "", CONTEXT)["ok"] is True
    # minor B (KIEM_4B_B.md): "0" của 0ef1182e từng làm "0 đ" lọt — nay vẫn bị bắt
    assert check_numbers("NV07 được 0 đ.", [tool], "")["unsourced"] == ["0"]
    assert check_numbers("NV07 được 400.000 đ.", [tool], "")["ok"] is True


def test_invented_numbers_still_caught_with_context():
    assert check_numbers("NV07 được 5.000.000 đ.", [], "", CONTEXT)["unsourced"] == ["5.000.000"]
    # Giới hạn ghi trước (N2): "10" của kỳ luôn có nguồn ⇒ "thiếu 10 đơn" bịa sẽ LỌT
    assert check_numbers("NV07 thiếu 10 đơn.", [], "", CONTEXT)["ok"] is True


# Bậc "số % trong tên khoá" (K1): "100" chỉ nằm trong TÊN khoá business_salary_at_100_pct
PCT_KEY_TOOL = {"employee_id": "NV07", "business_salary_at_100_pct": 14540000, "amount": 400000}


@pytest.mark.parametrize("answer", [
    "Tiền kinh doanh ở mức 100%: 14.540.000 đồng.",
    "Tiền kinh doanh ở mức 100 %: 14.540.000 đồng.",
])
def test_percent_named_in_key_is_sourced(answer):
    assert check_numbers(answer, [PCT_KEY_TOOL])["ok"], check_numbers(answer, [PCT_KEY_TOOL])


@pytest.mark.parametrize("answer, caught", [
    ("Mức thưởng 100 triệu đồng.", ["100 triệu"]),           # không có % ⇒ khoá không làm nguồn
    ("NV07 đã có 100 đơn.", ["100"]),
    ("NV07 còn thiếu 100.", ["100"]),
])
def test_percent_key_number_without_percent_sign_still_caught(answer, caught):
    assert check_numbers(answer, [PCT_KEY_TOOL])["unsourced"] == caught


def test_other_percent_key_does_not_source_money():
    tool = {"business_salary_at_95_pct": 13813000}
    assert check_numbers("Mức 95 % được 13.813.000 đ.", [tool])["ok"] is True
    assert check_numbers("Mức 95 triệu được 13.813.000 đ.", [tool])["unsourced"] == ["95 triệu"]
    # số khác trong tên khoá không phải mẫu _<số>_pct ⇒ không là nguồn kể cả có %
    assert check_numbers("Đạt 2026 %.", [{"rules_2026_10": 1}])["unsourced"] == ["2026"]
    # số % không có trong khoá nào vẫn bị bắt
    assert check_numbers("Mức 103 % được 14.540.000 đ.", [PCT_KEY_TOOL])["unsourced"] == ["103"]


# Bậc "tiền đúng tới đồng" (GĐ 1). Trước bậc này "khoảng 384,6 triệu" cho 384.601.087,9 là ĐẠT
# (ca cũ ở test_sourced_answers_pass) — doanh số cũng là tiền ⇒ nay phải bắt; ca đó đổi thành số đủ tới đồng.
MONEY = {"employee_id": "NV01", "amount": 5740000, "pct": 54.15, "gap_to_next": 384601087.9}


@pytest.mark.parametrize("answer", [
    "NV01 được 5.740.000 đ", "NV01 được 5740000 đồng", "NV01 được 5,74 triệu", "NV01 được 5740k", "NV01 được 5.740 nghìn",
    "NV01 được 5 740 000 VNĐ", "Doanh số còn thiếu 384.601.088 đ",
])
def test_money_exact_to_the_dong_passes(answer):
    assert check_numbers(answer, [MONEY])["ok"], check_numbers(answer, [MONEY])


@pytest.mark.parametrize("answer, bad", [
    ("NV01 được khoảng 6 triệu", "6 triệu"), ("NV01 được 5,7 triệu", "5,7 triệu"), ("NV01 được 6 triệu đồng", "6 triệu"),
    ("NV01 được 5.750.000 đ", "5.750.000"), ("NV01 được 5.739.999 đ", "5.739.999"),
    ("Doanh số còn thiếu khoảng 384,6 triệu đồng.", "384,6 triệu"), ("Doanh số còn thiếu 385 triệu", "385 triệu"),
])
def test_rounded_money_is_caught(answer, bad):
    result = check_numbers(answer, [MONEY])
    assert not result["ok"] and bad in result["unsourced"]


@pytest.mark.parametrize("answer, ok", [
    # Luật % (ĐỔI QUYẾT ĐỊNH — thay "làm tròn tới số lẻ đã viết" 04/11 và "chỉ làm tròn xuống" vòng 2): % bằng ĐÚNG số công cụ 54.15
    ("NV01 đạt 54,1 %", False), ("NV01 đạt 54,2 %", False), ("NV01 đạt 54 %", False), ("NV01 đạt 54,15 %", True),
    ("NV01 đạt 54,150 %", True), ("NV01 đạt 54,10 %", False), ("NV01 đạt 55 %", False),
    # "đ" của "đạt" không biến 54 thành tiền: là tiền thì |54 − 54.15| < 1 đồng ⇒ qua; là số thường thì phải bằng đúng 54.15 ⇒ trượt
    ("NV01 đạt 54 đạt mốc", False), ("NV01 đạt 54,15 đạt mốc", True),
])
def test_percent_and_words_starting_with_d_unchanged(answer, ok):
    assert check_numbers(answer, [MONEY])["ok"] is ok


# Vòng 2 (04/11 mục 7 — sửa thử, chưa đo thật, chờ)
@pytest.mark.parametrize("answer, question, ok", [
    ("Đúng, hiện NV01 được 5.740.000 đồng.", "NV01 được khoảng 6 triệu đúng không?", False),      # ý 1: đồng ý số làm tròn của câu hỏi (K lượt 2)
    ("Không, NV01 được 5.740.000 đồng.", "NV01 được khoảng 6 triệu đúng không?", True),           # không đồng ý ⇒ không bắt
    ("Dạ, không phải — NV01 được 5.740.000 đồng.", "NV01 được khoảng 6 triệu đúng không?", True),  # V2 v1: lễ phép rồi phủ định
    ("Đúng, NV01 được 5.740.000 đồng.", "NV01 được khoảng 5.740.000 đúng không?", True),          # số câu hỏi khớp đúng công cụ
    ("Đúng, NV01 được khoảng 6 triệu.", "NV01 được khoảng 6 triệu đúng không?", False),           # số làm tròn của câu hỏi không là nguồn
    ("Từ 5.000.000 đồng trở lên có NV01.", "Bao nhiêu bạn có tổng tiền từ 5 triệu trở lên?", True),   # ý 2: đổi đơn vị số câu hỏi (K 04/11 bắt nhầm)
    ("Nếu thêm 2.000.000 đồng doanh số thì vẫn 5.740.000 đồng.", "Nếu NV01 thêm 2 triệu doanh số?", True),
])
def test_question_numbers_round_two(answer, question, ok):
    assert check_numbers(answer, [MONEY], question)["ok"] is ok, check_numbers(answer, [MONEY], question)


@pytest.mark.parametrize("answer, ok", [("đạt 94,99 %", True), ("đạt 94,990 %", True), ("đạt 94,9 %", False), ("đạt 95 %", False),
                                        ("đạt 94 %", False)])
def test_percent_must_equal_tool_value(answer, ok):
    # ý 5: công cụ ghi 94.99 (đã làm tròn xuống) — "95 %" từng ĐẠT dù chưa tới mốc 95 %; từ mọi cách làm tròn đều trượt
    assert check_numbers(answer, [{"pct": 94.99}])["ok"] is ok
