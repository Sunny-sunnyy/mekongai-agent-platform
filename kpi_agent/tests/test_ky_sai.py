"""Lớp "câu trả lời ghi SAI KỲ" của rào (`agent/period_check.py`, kiem_so@31) — test viết theo LOẠI, trước khi có code.

. Mỗi nhóm là TÍCH của vài trục (động từ × cách nêu kỳ × dấu nối × có / không có
điều kiện ngoại lệ), không phải vài câu mẫu — hai vòng trước hỏng vì luật viết theo câu mẫu. Mọi ca đi qua cửa thật `kiem_so.kiem_so` và chỉ xét
lỗi loại `ky_sai` (lớp số / chủ-số có test riêng). Kết quả công cụ ở đây là bảng nhỏ đúng hình (logic thuần — ca dữ liệu thật ở `test_kiem_so.py`)."""
import itertools

import pytest

from kpi_agent.agent.kiem_so import kiem_so

TOOL = {"period": "2026-10", "employee_id": "NV11", "business_salary": 5040000}
FIGURE = "NV11 có lương kinh doanh 8 KPI 5.040.000 đ"
WRONG = ["tháng 9", "tháng 09/2026", "tháng 9 năm 2026", "kỳ 2026-09", "9/2026", "T9/2026", "tháng 9-2026"]     # kỳ KHÁC, nêu bằng số
RIGHT = ["tháng 10", "tháng 10/2026", "tháng 10 năm 2026", "kỳ 2026-10", "10/2026", "T10/2026"]
ASK = ["Bạn hỏi", "Bạn hỏi về", "Như bạn hỏi về", "Bạn cần xem", "Bạn muốn xem", "Anh yêu cầu", "Chị nhắc tới", "Bạn đề cập", "Bạn xin số liệu"]
JOIN = [": ", ", ", " thì ", ", kết quả là ", ":\n- "]
DENY_BEFORE = ["Chưa có dữ liệu {}", "Hiện không có dữ liệu của {}", "Tôi chưa có số liệu {}", "Hệ thống chưa nạp {}", "Không có dữ liệu cho {}"]
DENY_AFTER = ["{} thì tôi chưa có dữ liệu", "{} chưa có dữ liệu", "{} hiện không có số liệu", "{} chưa được nạp"]


def blocked(answer: str, results=(TOOL,)) -> bool:
    return any(problem["loai"] == "ky_sai" for problem in kiem_so({"answer": answer, "tool_results": list(results)})["loi"])


def cap(text: str) -> str:
    return text[0].upper() + text[1:]


# ---------- Luật gốc (đề mục 2): nêu kỳ khác bằng số trước con số ⇒ chặn; nêu đúng kỳ ⇒ qua ----------
@pytest.mark.parametrize("period,join", itertools.product(WRONG, [", ", ": ", " — "]))
def test_wrong_period_before_figure_is_blocked(period, join):
    assert blocked(f"{cap(period)}{join}{FIGURE}.")


@pytest.mark.parametrize("period", RIGHT)
def test_right_period_passes(period):
    assert not blocked(f"{cap(period)}, {FIGURE}.")


@pytest.mark.parametrize("answer", ["Năm 2025, " + FIGURE + ".", "Kết quả năm 2024: " + FIGURE + ".", FIGURE + " trong tháng 3.",
                                    FIGURE + " (tháng 11/2026).", "Tháng 10 năm 2025, " + FIGURE + "."])
def test_wrong_year_or_trailing_period_is_blocked(answer):
    assert blocked(answer)


def test_right_year_passes():
    assert not blocked(f"Năm 2026, tháng 10: {FIGURE}.")


# ---------- Kỳ của dữ liệu lấy từ ô `period` của kết quả dùng được — không từ tham số, không từ lời từ chối ----------
def test_period_comes_from_result_not_from_params_or_refusal():
    for block in ("params_used", "params"):
        assert blocked(f"Tháng 9, {FIGURE}.", [{**TOOL, block: {"period": "2026-09"}}])
    assert blocked(f"Tháng 9, {FIGURE}.", [{**TOOL, "base_period": "2026-09", "period_note": "2026-09"}])     # chỉ khoá tên đúng là period
    refusal = {"refused": "chưa có dữ liệu kỳ 2026-09", "params_used": {"period": "2026-09"}}
    assert blocked(f"Tháng 9, {FIGURE}.", [TOOL, refusal])
    assert not blocked("Chưa có dữ liệu tháng 9.", [refusal])        # không có kết quả dùng được ⇒ lớp này không xét (điều đã chốt)


def test_two_period_tool_accepts_both_periods_only():
    compare = {"before": {"period": "2026-09", "total": 1}, "after": {"period": "2026-10", "total": 2}}
    assert not blocked("Tháng 9 so với tháng 10: tổng tăng.", [compare])
    assert blocked("Tháng 8 so với tháng 10: tổng tăng.", [compare])


def test_usable_result_without_period_cell_blocks_any_period():
    # Đo 20/11 trên 1.943 bản ghi thật: 0 bản ghi có kết quả dùng được mà thiếu ô period ⇒ đóng khi lỗi, không đoán kỳ
    assert blocked(f"Tháng 10, {FIGURE}.", [{"employee_id": "NV11", "business_salary": 5040000}])


# ---------- Họ (i): lời "bạn hỏi <kỳ X>" KHÔNG tha cho câu trình bày số khi câu trả lời không nói kỳ thật / không phủ nhận chính kỳ X ----------
@pytest.mark.parametrize("ask,period,join", itertools.product(ASK, WRONG, JOIN))
def test_asked_period_alone_is_blocked(ask, period, join):
    assert blocked(f"{ask} {period}{join}{FIGURE}.")


@pytest.mark.parametrize("ask,period,right", itertools.product(ASK, WRONG[:4], RIGHT[:4]))
def test_asked_period_with_true_period_stated_passes(ask, period, right):
    assert not blocked(f"{ask} {period} nhưng dữ liệu hiện chỉ có {right}: {FIGURE}.")
    assert not blocked(f"{ask} {period}. Dữ liệu hiện có là {right}.\n{cap(right)}: {FIGURE}.")


@pytest.mark.parametrize("ask,period,deny", itertools.product(ASK[:5], WRONG[:4], DENY_BEFORE[:3] + DENY_AFTER[:3]))
def test_asked_period_with_denial_of_same_period_passes(ask, period, deny):
    assert not blocked(f"{ask} {period}. {cap(deny.format(period))}.")


@pytest.mark.parametrize("ask,period", itertools.product(ASK[:4], WRONG[:4]))
def test_asked_exemption_does_not_spread_to_a_second_mention(ask, period):
    # Kỳ thật có được nêu, nhưng chỗ nêu kỳ X THỨ HAI đứng trước con số không phải lời nhắc câu hỏi ⇒ vẫn chặn
    assert blocked(f"{ask} {period}. Tháng 10 thì có dữ liệu. {cap(period)}: {FIGURE}.")


@pytest.mark.parametrize("period", WRONG[:4])
def test_period_before_the_ask_words(period):
    assert not blocked(f"{cap(period)} bạn hỏi thì chưa có dữ liệu. Tháng 10, {FIGURE}.")
    assert blocked(f"{cap(period)} bạn hỏi: {FIGURE}.")


# ---------- Họ (ii): phủ nhận kỳ Y chỉ tha cho kỳ Y ----------
OTHER = ["tháng 8", "tháng 08/2026", "kỳ 2026-08"]


@pytest.mark.parametrize("period,other,deny", itertools.product(WRONG[:4], OTHER, DENY_BEFORE[:3] + DENY_AFTER[:3]))
def test_denial_of_another_period_does_not_excuse(period, other, deny):
    denial = cap(deny.format(other))
    assert blocked(f"Bạn hỏi {period}: {FIGURE}. {denial}.")
    assert blocked(f"{denial}. Bạn hỏi {period}: {FIGURE}.")
    assert blocked(f"{denial}. {cap(period)}, {FIGURE}.")


@pytest.mark.parametrize("period,deny", itertools.product(WRONG, DENY_BEFORE + DENY_AFTER))
def test_denial_of_the_period_itself_passes(period, deny):
    assert not blocked(f"{cap(deny.format(period))}. Tháng 10, {FIGURE}.")


@pytest.mark.parametrize("period", WRONG[:4])
def test_denied_list_and_elliptical_denial(period):
    assert not blocked(f"Chưa có dữ liệu tháng 8 và {period}. Tháng 10: {FIGURE}.")
    assert not blocked(f"Bạn hỏi {period}; {period} chưa có dữ liệu, tháng 8 cũng chưa. Tháng 10: {FIGURE}.")


@pytest.mark.parametrize("period", WRONG[:4])
def test_denial_does_not_excuse_a_presenting_mention_of_the_same_period(period):
    # "chưa có dữ liệu X" rồi lại trình bày số dưới nhãn X ⇒ chỗ nêu thứ hai là lời nêu kỳ của số, không phải lời phủ nhận
    assert blocked(f"Chưa có dữ liệu {period}. {cap(period)}: {FIGURE}.")


@pytest.mark.parametrize("period,deny", itertools.product(WRONG[:4], DENY_AFTER[:3]))
def test_denial_followed_by_money_in_the_same_sentence_is_blocked(period, deny):
    # Lời phủ nhận rồi vẫn trình bày tiền ngay trong câu đó ⇒ kỳ X đang là nhãn của số; có chỗ nêu ĐÚNG kỳ chen giữa thì số thuộc kỳ đúng
    assert blocked(f"{cap(deny.format(period))} chính thức nhưng {FIGURE}.")
    assert not blocked(f"{cap(deny.format(period))}, còn tháng 10 thì {FIGURE}.")
    assert not blocked(f"{cap(deny.format(period))} của NV11 và 13 người còn lại.")


@pytest.mark.parametrize("answer", ["Đây không phải tháng 10: " + FIGURE + ".", "Số này không phải của kỳ 2026-10. " + FIGURE + "."])
def test_denying_the_true_period_is_blocked(answer):
    assert blocked(answer)


def test_refuting_another_period_needs_the_true_period():
    assert not blocked(f"Đây là dữ liệu tháng 10, không phải tháng 9: {FIGURE}.")
    assert blocked(f"Không phải tháng 9 đâu: {FIGURE}.")          # không hề nói kỳ thật


@pytest.mark.parametrize("answer", ["NV11 không đạt mốc PC_BT tháng 9.", "NV11 chưa đạt chỉ tiêu tháng 9, thực hiện 12.",
                                    "Tháng 9 NV11 không có đơn trễ, " + FIGURE + "."])
def test_negation_about_results_is_not_a_denial_of_data(answer):
    assert blocked(answer)


# ---------- Họ (iii): cách viết có chữ "tháng" / số-trên-năm mà KHÔNG phải lời nêu kỳ ⇒ không chặn oan ----------
NOT_A_PERIOD = [
    "hạn xuất hoá đơn là trước 22h ngày 02 của tháng 11", "hạn hoá đơn là 22h ngày 02 tháng 11", "hạn hoá đơn rơi vào đầu tháng 11",
    "hoá đơn phải xuất trước 22:00 ngày 02/11/2026", "hạn hóa đơn của kỳ này là ngày 2 tháng 11 năm 2026", "hạn xuất hoá đơn nằm ở tháng 11",
    "chỉ tiêu này xét mỗi tháng 1 lần", "đội họp hằng tháng 2 lần", "mỗi cửa hàng tính một tháng 1 đơn", "số liệu thuộc Quý 4/2026",
    "số liệu thuộc quý IV năm 2026", "theo công văn số 05/2026", "theo công văn 26/2026", "theo quyết định số 3/2026",
    "sang tháng 11 số này có thể đổi khi nạp dữ liệu mới", "đến tháng 11 sẽ có kỳ mới", "dữ liệu chốt tới ngày 31/10/2026",
    "dữ liệu chốt ngày 31 tháng 10 năm 2026", "ngày chốt 2026-10-31",
]


@pytest.mark.parametrize("clause", NOT_A_PERIOD)
def test_not_a_period_statement_passes(clause):
    assert not blocked(f"Tháng 10, {FIGURE}. Lưu ý: {clause}.")
    assert not blocked(f"{cap(clause)}. Kỳ 2026-10: {FIGURE}.")


@pytest.mark.parametrize("answer", ["Sang tháng 11, " + FIGURE + ".", "Đến tháng 11 NV11 có 5.040.000 đ.", "Đầu tháng 11: " + FIGURE + ".",
                                    "Hạn hoá đơn tháng 12 đã qua, " + FIGURE + ".", "Mỗi tháng 9 " + FIGURE + "."])
def test_exceptions_of_family_three_do_not_cover_figures(answer):
    assert blocked(answer)


# Câu ĐÚNG nói về kỳ khác mà không trình bày số của kỳ đó (dò tay 20/11: 4 / 27 câu tự soạn bị chặn oan ở bản đầu)
NO_DATA_ABOUT_OTHER_PERIOD = [
    "Nếu cần {x}, bạn tải file {x} lên nhé", "Tôi chỉ có dữ liệu tháng 10 nên không trả lời được cho {x}", "Dữ liệu {x} chưa được tải lên",
    "Tôi chưa tính được {x}", "Hệ thống không thể cung cấp số liệu {x}", "{x} chưa có luật nên chưa tính được", "{x} thì chưa có",
    "{x} chưa được cập nhật", "Bạn muốn xem {x} thì cần tải file {x}",
]


@pytest.mark.parametrize("clause,period", itertools.product(NO_DATA_ABOUT_OTHER_PERIOD, ["tháng 9", "tháng 11", "kỳ 2026-09", "tháng 09/2026"]))
def test_statement_about_another_period_without_its_figures_passes(clause, period):
    assert not blocked(f"Tháng 10: {FIGURE}. {cap(clause.format(x=period))}.")
    assert not blocked(f"{cap(clause.format(x=period))}. Số tháng 10: {FIGURE}.")


@pytest.mark.parametrize("clause,period", itertools.product(NO_DATA_ABOUT_OTHER_PERIOD[1:8], ["tháng 9", "kỳ 2026-09"]))
def test_statement_about_another_period_then_money_under_it_is_blocked(clause, period):
    assert blocked(f"{cap(clause.format(x=period))}. {cap(period)}: {FIGURE}.")
    assert blocked(f"{cap(clause.format(x=period))} nhưng {FIGURE}.")


# Ngoại lệ (d) không áp cho TIÊU ĐỀ / dòng dẫn: chỗ nêu tháng kế đứng làm nhãn, số nằm ở dòng / câu sau
LEAD_IN = ["Sang tháng 11 dự kiến", "Tháng 11 (dự kiến):", "## Tháng 11 sẽ như sau", "Hạn hoá đơn tháng 11:", "Tháng 11 sẽ có", "**Tháng 11 khi chốt**",
           "| Tháng 11 dự kiến | Lương |", "Sang tháng 11 sẽ là:", "Hoá đơn tháng 11"]


@pytest.mark.parametrize("lead,sep", itertools.product(LEAD_IN, ["\n", "\n- ", "\n\n"]))
def test_next_month_as_heading_or_lead_in_is_blocked(lead, sep):
    assert blocked(f"{lead}{sep}{FIGURE}.")


# vòng 1 (major): ngoại lệ (d) dùng sai — câu dẫn DÀI nói tương lai rồi số ở câu / dòng sau; câu hạn / hoá đơn mang tiền
FUTURE_LEADS = ["Sang tháng 11 hệ thống sẽ cập nhật số liệu của cả đội như sau", "Khi sang tháng 11 số liệu của cả đội được ghi nhận như sau",
                "Tháng 11 có thể ghi nhận kết quả của từng người như sau", "Dự kiến tháng 11 cả đội đạt kết quả như sau"]


@pytest.mark.parametrize("lead,sep", itertools.product(FUTURE_LEADS, [". ", "\n", "\n- "]))
def test_future_sentence_about_next_month_leading_figures(lead, sep):
    assert blocked(f"{lead}{sep}{FIGURE}.")                                  # tháng 11 là kỳ gần nhất trước số ⇒ chặn, câu dẫn dài ngắn không quan trọng
    assert not blocked(f"{lead}{sep}Tháng 10: {FIGURE}.")                    # có chỗ nêu ĐÚNG kỳ chen giữa ⇒ số thuộc tháng 10
    assert not blocked(f"Tháng 10: {FIGURE}. {lead}.")                        # nói tương lai SAU số, không dẫn số nào ⇒ qua


@pytest.mark.parametrize("subject,money", itertools.product(["Hạn hoá đơn tháng 11", "Hoá đơn tháng 11 của cả đội", "Hóa đơn tháng 11 của NV11", "Hạn tháng 11"],
                                                            [" là 5.040.000 đ", ": 152.360.000 đ", " đạt 5040000 đồng"]))
def test_deadline_words_do_not_excuse_money(subject, money):
    assert blocked(f"{subject}{money}.")


@pytest.mark.parametrize("answer", ["Hạn hoá đơn rơi vào tháng 11, trước 22h", "Hạn xuất hoá đơn là 22:00 đầu tháng 11", "Hoá đơn của kỳ này xuất chậm nhất đầu tháng 11",
                                    "Số tháng 11 sẽ có khi kỳ sau chốt"])
def test_deadline_or_future_with_only_date_time_passes(answer):
    assert not blocked(f"Tháng 10: {FIGURE}. {answer}.")
    assert not blocked(f"{answer}. Tháng 10: {FIGURE}.")


def test_deadline_sentence_naming_an_employee_is_blocked():
    assert blocked("Tháng 10 xong. Hạn hoá đơn của NV11 rơi vào tháng 11.")


def test_money_after_the_word_thang_is_not_a_month():
    assert not blocked("Tháng 10, lương tháng 5.040.000 đ của NV11 là lương kinh doanh.")


# ---------- Giới hạn ĐÃ CHẤP NHẬN (đề mục 6) — ghi lại để ai nới / siết thì thấy ----------
@pytest.mark.parametrize("answer", ["Tháng chín, " + FIGURE + ".", "Tháng trước, " + FIGURE + ".", "T9, " + FIGURE + ".", "Quý 3/2026, " + FIGURE + ".",
                                    "Tháng 9 chưa có dữ liệu chính thức nhưng NV11 đạt 23 đơn."])
def test_accepted_limits_are_not_caught_by_this_layer(answer):
    assert not blocked(answer)
