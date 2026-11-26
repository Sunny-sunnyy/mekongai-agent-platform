"""Phép kiểm "số thuộc đúng NV" + "câu đếm người khớp bảng đội" (bậc cấu trúc GĐ 1b —, hướng (i) 04/11).
Dữ liệu: cùng HÌNH kết quả thật của get_team_bonus_table / explain_kpi (số nhỏ tự đặt), mã che NVxx."""
import re

import pytest

from kpi_agent.agent.number_check import check_numbers
from kpi_agent.agent.owner_check import check_owners, mask_people_counts, team_kpi_counts

# Bảng đội 14 người: ASO_Focus có tiền 13 / 14 (NV14 = 0), PC_ON chỉ NV03 có tiền, LPPC cả đội có tiền
TEAM = {"employees": [{"employee_id": f"NV{i:02d}", "business_salary": 1000000 + i * 100000,
                       "kpi_amounts": {"ASO_Focus": 0 if i == 14 else 800000, "PC_ON": 500000 if i == 3 else 0, "LPPC": 2000000,
                                      "PC_BT": 0, "Turnover": 0},
                       "kpis_paid": 2 if i != 3 else 3} for i in range(1, 15)],
        "employee_count": 14, "kpi_count": 3, "team_total": 50000000}
EXPLAIN_NV02 = {"employee_id": "NV02", "kpi": "ASO_Focus", "target": 72, "actual": 68, "pct": 94.44, "amount": 800000,
                "tiers": [{"label": "Đạt 90%", "min": 65, "amount": 800000}, {"label": "Đạt 100%", "min": 72, "amount": 1000000}]}
TWO = {"employees": [{"employee_id": "NV05", "kpi_amounts": {"PC_BT": 3200000}}, {"employee_id": "NV06", "kpi_amounts": {"PC_BT": 4100000}}]}


@pytest.mark.parametrize("answer", [
    "NV05 được 3.200.000 đ PC_BT, NV06 được 4.100.000 đ.",
    "Bảng:\nNV05 · 3.200.000 đồng\nNV06 · 4.100.000 đồng",      # mã ở đầu dòng bảng — "gần nhất đứng trước", không phải cùng câu
    "Tổng đội 50.000.000 đ.",                                     # không có mã đứng trước ⇒ cấp đội, không xét
])
def test_numbers_of_the_right_employee_pass(answer):
    assert check_owners(answer, [TWO, {"team_total": 50000000}])["ok"]


def test_number_of_another_employee_is_caught():
    result = check_owners("NV05 được 4.100.000 đ PC_BT.", [TWO])   # số của NV06 (B mẫu "sai_nguoi")
    assert not result["ok"] and "NV05" in result["problems"][0]


@pytest.mark.parametrize("answer, ok", [
    ("13 người có tiền ASO_Focus.", True), ("1 người chưa có tiền ASO_Focus.", True),
    ("14 người có tiền ASO_Focus.", False), ("8 người có tiền ASO_Focus.", False),
    ("Cả đội đều có tiền ASO_Focus.", False),          # 13 / 14 — lỗi kiểm cuối T5 04/11
    ("Không ai có tiền PC_ON.", False), ("Không bạn nào bị 0 đồng ASO_Focus.", False),
    ("Cả đội đều có tiền LPPC.", True),                # 14 / 14
    ("Tổng tiền ASO_Focus của cả đội chưa có trong công cụ.", True),   # "cả đội" là danh từ, không phải khẳng định
    ("7 người còn cơ hội lên mốc ASO_Focus.", True),   # không phải câu có / chưa có tiền ⇒ không xét
    # 1b (M-a): chiều câu ở hai đầu — "tất cả có" cần cả đội có; "không ai có" cần 0 người có
    ("Tất cả đều có tiền PC_ON.", False), ("Không ai có tiền LPPC.", False), ("Tất cả đều chưa có tiền PC_ON.", False),
    ("Không bạn nào bị 0 đồng LPPC.", True), ("Chưa ai có tiền PC_BT.", True),
    # (M-b): mọi KPI trong câu
    ("Chưa ai có tiền ở KPI PC_BT, PC_ON và Turnover.", False), ("Chưa ai có tiền ở KPI PC_BT và Turnover.", True),
    # (m1): "N/M người", "N trên M người" — M là cả đội
    ("13/14 người có tiền ASO_Focus.", True), ("13 trên 14 người có tiền ASO_Focus.", True), ("13/15 người có tiền ASO_Focus.", False),
    # (m2): chiều lấy từ cụm ngay sau số người, không từ cả câu
    ("13 người có tiền ASO_Focus, chỉ NV14 chưa có.", True), ("13 người có tiền ASO_Focus, không tính thưởng vượt.", True),
    ("ASO_Focus: 13 người có tiền, 1 người chưa có tiền.", True),   # hai khẳng định một câu, KPI đứng trước
    # Vòng 2 ý 4: "cả N người" = số người của đội (T4 lượt 2 câu 7 04/11 bắt nhầm)
    ("Cả đội chưa ai có tiền PC_BT (đều 0 ở cả 14 nhân viên).", True), ("Chưa ai có tiền PC_BT (0 đồng ở cả 13 nhân viên).", False),
    ("ASO_Focus đang 0 đồng ở cả 14 nhân viên.", False),   # tầng A C Y4-46: 13 / 14 có tiền ⇒ "0 đồng ở cả 14" sai
    ("Cả 14 người đều có tiền LPPC, không tính thưởng vượt.", True),   # V2 v2: "không" ở vế sau không đổi chiều
])
def test_people_count_and_whole_team_sentences(answer, ok):
    assert check_owners(answer, [TEAM])["ok"] is ok, check_owners(answer, [TEAM])


def test_people_sentence_without_team_table_is_flagged():
    result = check_owners("13 người có tiền ASO_Focus.", [EXPLAIN_NV02])
    assert not result["ok"] and "không có bảng thưởng đội" in result["problems"][0]


def test_people_counts_are_masked_only_where_they_count_people():
    # "13" không có trong kết quả công cụ — chỉ code đếm ra (B mẫu "dem_13_dung" từng bị bắt nhầm) ⇒ che đúng chỗ đếm người
    assert team_kpi_counts([TEAM])["paid"]["ASO_Focus"] == 13
    assert not check_numbers("13 người có tiền ASO_Focus.", [TEAM])["ok"]
    assert check_numbers(mask_people_counts("13 người có tiền ASO_Focus."), [TEAM])["ok"]
    # 1b, m3: số 13 ở chỗ khác ("13 cửa hàng") vẫn phải có nguồn
    assert not check_numbers(mask_people_counts("NV01 hiện có 13 cửa hàng ASO_Focus."), [TEAM])["ok"]


def test_numbers_from_question_are_not_owner_checked():
    assert check_owners("Nếu NV02 thêm 3 cửa hàng thì vẫn 800.000 đ.", [EXPLAIN_NV02], question="NV02 thêm 3 cửa hàng ASO_Focus?")["ok"]


def test_same_employee_tier_money_said_as_current_is_now_blocked():
    # Đảo luật 11/11: trước là giới hạn (lỗi CÙNG NV lọt) — 1.000.000 là tiền mốc 100 % của chính NV02, câu không có điều kiện / chữ mốc ⇒ trượt
    assert check_owners("NV02 ASO_Focus hiện được 1.000.000 đồng.", [EXPLAIN_NV02])["ok"] is False
    assert check_owners("NV02 ASO_Focus hiện được 800.000 đồng; mốc 100 % là 1.000.000 đồng.", [EXPLAIN_NV02])["ok"] is True


@pytest.mark.parametrize("answer", [
    "NV02 ASO_Focus đạt mốc 90 %.",                 # mức mốc nói như % — CÙNG NV, vế có "mốc" ⇒ không xét (vòng 2 đã bắt "NV02 đạt 90 %")
])
def test_known_limit_same_employee_errors_pass(answer):
    # Giới hạn ghi trước (kế hoạch mục 2, tầng A -22 -28): S+ không bắt lỗi cùng NV — chỉ Phần B (khuôn claims) bắt được
    assert check_owners(answer, [EXPLAIN_NV02])["ok"]


@pytest.mark.parametrize("answer, ok", [
    ("NV02 đạt 94,44 % ASO_Focus.", True), ("NV02 đạt 94,440 % ASO_Focus.", True),
    ("NV02 đạt 94 % ASO_Focus.", False), ("NV02 đạt 94,4 % ASO_Focus.", False),   # luật %: không làm tròn chiều nào
    ("NV02 đạt 95 % ASO_Focus.", False),                        # 95 không phải % đạt của NV02 (tầng A C A4-28)
    ("NV02 đạt 100 % ASO_Focus.", False),                       # 100 chỉ có ở tên mốc "Đạt 100%" (Y5-51)
    ("NV02 chưa tới mốc, mốc kế là Đạt 100 %.", True),          # cùng vế có "mốc" ⇒ tên mốc, không xét
])
def test_achieved_percent_must_match_pct_field(answer, ok):
    _assert_both(answer, ok, EXPLAIN_NV02)


EXPLAIN_LPPC_NV04 = {"employee_id": "NV04", "kpi": "LPPC_Meat", "target": 3.8, "actual": 3.82, "pct": 100.52, "amount": 400000,
                     "tiers": [{"label": "Mức 1: 3.8", "min": 3.8, "amount": 400000}, {"label": "Mức 2: 4.2", "min": 4.2, "amount": 600000}],
                     "reason": "LPPC_Meat: thực hiện 3.82 ⇒ đạt mốc 'Mức 1: 3.8' ⇒ 400,000 đ"}


@pytest.mark.parametrize("answer, ok", [
    ("LPPC_Meat của NV04 là 3,82 — đã tới mốc 3,8 nên được 400.000 đồng.", True),
    ("LPPC_Meat của NV04 là 3,8 — đã tới mốc 3,8 nên được 400.000 đồng.", False),   # tầng A C Y5-50: 3,8 chỉ có ở mốc
    ("NV04 LPPC_Meat đạt 3,820.", True),                                            # cùng giá trị, khác cách viết
    ("NV04 LPPC_Meat đã qua mốc 3,8.", True),                                       # vế có "mốc" ⇒ tên mốc, không xét
])
def test_ratio_value_must_match_actual_field(answer, ok):
    # Luật: tỷ lệ KPI cũng đúng y số công cụ
    _assert_both(answer, ok, EXPLAIN_LPPC_NV04)


def test_kpi_count_is_not_an_employee_value():
    # T3 câu 6 thật (3 lượt 04/11): "NV07 có cơ hội lên bậc ở 3 KPI" — 3 = số cặp đếm được, không phải số liệu của NV07
    tool = {"items": [{"employee_id": "NV07", "kpi": k, "gap_to_next": 2.5} for k in ("Turnover", "ASO_ALL", "ASO_Focus")],
            "working_days_left": 3}
    assert check_owners("NV07 có cơ hội lên bậc ở 3 KPI.", [tool])["ok"]
    # đếm sai số KPI liệt kê vẫn bị bắt nếu số đó không thuộc NV (số 2 chỉ có ở NV08)
    other = {"employee_id": "NV08", "kpis_paid": 2}
    assert not check_owners("NV07 có cơ hội lên bậc ở 2 KPI.", [tool, other])["ok"]


def _assert_both(answer, ok, tool):
    # Như đường chạy thật: hai phép kiểm cùng xét ("95" không có ở đâu ⇒ number_check bắt; "100" có ở tên mốc ⇒ owner_check bắt)
    both = check_owners(answer, [tool])["ok"] and check_numbers(answer, [tool])["ok"]
    assert both is ok, (check_owners(answer, [tool]), check_numbers(answer, [tool]))


# 10/11 (B, kiem_so@7 — chạy thử 8 câu, CHAY_THU_8_CAU_CHAT_C.md câu 2, 7) ----------
RESULT_NV02 = {"employee_id": "NV02", "kpis": [{"kpi": "ASO_ALL", "pct": 104.34, "reached_tier": "Đạt 100%", "amount": 1040000},
                                               {"kpi": "PC_BT", "pct": 92.44, "reached_tier": None, "amount": 0}]}


@pytest.mark.parametrize("answer, ok", [
    ("NV02 ASO_ALL: thực hiện 104,34 %, “Đạt 100%”.", True),        # tên mốc NV02 ĐÃ ĐẠT (reached_tier) — câu 2 chat bị gắn nhầm
    ("NV02 đạt 100 % ASO_ALL.", True),                                 # cùng chữ, cùng là mốc đã đạt — chấp nhận (cùng NV, ghi rõ)
    ("NV02 đạt 104,3 % ASO_ALL.", False),                              # % thật vẫn phải đúng y ô pct
    ("NV02 đạt 95 % ASO_ALL.", False),                                 # không phải pct, không phải mốc đã đạt
])
def test_reached_tier_label_of_the_same_employee(answer, ok):
    _assert_both(answer, ok, RESULT_NV02)   # như đường chạy thật: owner_check + number_check


def test_tier_list_label_still_not_a_source():
    # Y5-51 giữ: EXPLAIN_NV02 chỉ có danh sách mốc (tiers), không có reached_tier ⇒ "đạt 100 %" vẫn bị bắt
    assert check_owners("NV02 đạt 100 % ASO_Focus.", [EXPLAIN_NV02])["ok"] is False


# Đúng HÌNH get_data_issues thật: `employee_id: "cả đội"` ở gốc ⇒ số trong chữ cờ thuộc "CẢ ĐỘI" (không phải cấp đội TEAM) ⇒ trước @7 số mục / ngày
# đứng sau mã NV bị coi là số của NV đó (C câu 7)
ISSUES = {"employee_id": "cả đội", "issues": [{"message": "ASO_ALL: 4 dòng ngoài MCP — loại theo công văn mục 6.2", "employee_ids": ["NV08", "NV14"]}],
          "data_flags": ["RAW DATA tới ngày đơn 30/10/2026", "luật chưa kiểm do thiếu dòng đơn (mục 2.2, 2.3, 2.7, 2.8, 8.1, 8.2)"]}


@pytest.mark.parametrize("answer, ok", [
    ("ASO_ALL ngoài MCP của NV08, NV14, đã loại theo công văn mục 6.2.", True),
    ("NV08: luật chưa kiểm được theo các mục 2.2, 2.3, 2.7, 2.8, 8.1 và 8.2.", True),
    ("NV08: dữ liệu tới ngày đơn 30/10/2026.", True),
    ("NV08: RAW DATA tới ngày 30/10/2026.", True),   # kiem_so@9: "ngày 30" từng bị che trước ⇒ "10", "2026" lộ (lượt e0b câu 7)
    ("NV08 được 4.100.000 đ.", True),        # số không có nguồn ở đâu ⇒ owner bỏ qua (number_check bắt) — như cũ
    ("NV08 có 7 dòng, mục 6.2.", True),      # 7 không có ở đâu ⇒ number_check bắt, owner bỏ qua — như cũ
])
def test_section_numbers_and_full_dates_are_not_employee_numbers(answer, ok):
    assert check_owners(answer, [ISSUES])["ok"] is ok


def test_number_of_another_employee_still_caught_next_to_a_section():
    # Che "mục 6.2" không làm số tiền của NV06 nói cho NV05 lọt
    assert check_owners("NV05 được 4.100.000 đ PC_BT theo mục 6.2.", [TWO])["ok"] is False


# 10/11 (B, kiem_so@7): số viết TRƯỚC mã NV trong cùng câu (chạy thử câu 7: "2" bị gán cho NV14) — chỉ kiem_so bật ----------
# Cờ theo người ĐÃ gắn chủ như `kiem_so._with_flag_owners` làm (mục phụ employee_id + flag_message)
FLAGS_OWNED = {"employee_id": "cả đội", "issues": [{"message": "ASO_ALL: 4 dòng ngoài MCP", "employee_ids": ["NV04", "NV14"]}],
               "owned": [{"employee_id": "NV04", "flag_message": "ASO_ALL: 4 dòng ngoài MCP"},
                         {"employee_id": "NV14", "flag_message": "ASO_ALL: 4 dòng ngoài MCP"},
                         {"employee_id": "NV08", "flag_message": "PC_ON: 2 dòng ngoài MCP"}]}


@pytest.mark.parametrize("answer", [
    "- ASO_ALL: 4 dòng ngoài MCP của NV04, NV14.\n- PC_ON: 2 dòng ngoài MCP của NV08.",                                     # Capitol câu 7 (dòng)
    "- ASO_ALL không thuộc MCP: 4 dòng, liên quan NV04 và NV14. PC_ON cũng có 2 dòng ngoài MCP, liên quan NV08; đã loại.",  # chat câu 7 (câu)
])
def test_number_before_code_in_same_sentence_belongs_to_that_code(answer):
    result = check_owners(answer, [FLAGS_OWNED], same_sentence_after=True)
    assert result["ok"], result


@pytest.mark.parametrize("answer, ok", [
    ("- PC_BT: 3.200.000 đ của NV05.", True),
    ("- PC_BT: 4.100.000 đ của NV05.", False),     # ca âm ghi trước: số của NV06 viết trước mã NV05 trong câu ⇒ chặn (trước @7: lọt — không có mã đứng trước)
    ("NV06\n- PC_BT: 3.200.000 đ của NV05.", True),  # câu có mã sau số ⇒ lấy mã đó, không lấy NV06 dòng trên
    ("NV05\n- PC_BT: 3.200.000 đ.", True),           # câu không có mã ⇒ như cũ (mã dòng trên)
    ("NV06\n- PC_BT: 3.200.000 đ.", False),          # như cũ: số của NV05 dưới tiêu đề NV06
    # ĐỔI có chủ đích ở @11: @7 ghi True. Mã NV05 đứng trần sau số tiền, NV06 ở câu trên ⇒ không rõ chủ ⇒ phải đúng với cả hai ⇒ chặn
    # (cùng hình "NV05⏎…4.100.000 đ, NV06 cũng vậy" phải trượt — nghi thì chặn). Có "của NV05" thì chủ rõ (ca ngay trên).
    ("NV06 lệch. PC_BT 3.200.000 đ, NV05.", False),
    ("NV06 lệch. PC_BT 3.200.000 đ thuộc NV05.", True),
    ("NV06 đạt 94.44 điểm và 3.200.000 đ của NV05.", False),   # "94.44" không cắt câu ⇒ có NV06 đứng trước ⇒ như cũ
])
def test_owner_of_number_by_sentence(answer, ok):
    assert check_owners(answer, [TWO], same_sentence_after=True)["ok"] is ok


def test_chat_path_keeps_nearest_code_before():
    # Đường chat (mặc định) giữ luật cũ: số đầu câu không có mã đứng trước ⇒ không xét (không đẻ cảnh báo "số 4 … NV04, NV14" khi cờ chưa gắn chủ)
    answer = "- ASO_ALL: 4 dòng ngoài MCP của NV04, NV14."
    raw_flags = {"employee_id": "cả đội", "issues": [{"message": "ASO_ALL: 4 dòng ngoài MCP", "employee_ids": ["NV04", "NV14"]}]}
    assert check_owners(answer, [raw_flags])["ok"]


def test_money_after_section_word_is_still_owner_checked():
    # kiem_so@8 (@7, minor 2): "mục" + số > 2 chữ số không phải số mục ⇒ không che ⇒ tiền của NV06 nói cho NV05 vẫn bị bắt
    assert check_owners("NV05 PC_BT theo mục 4.100.000 đ.", [TWO])["ok"] is False
    assert check_owners("NV05 PC_BT theo mục 6.2.", [TWO])["ok"] is True


# 10/11 (B, kiem_so@9): "chưa ai" có PHỦ ĐỊNH — ca ghi trước ở ----------
# TEAM: PC_ON chỉ NV03 có tiền (1 / 14), PC_BT + Turnover 0 / 14 — cùng hình câu thật T4 câu 7 (PC_ON 1 / 14)
@pytest.mark.parametrize("answer, ok", [
    ("Cả đội chưa ai có tiền ở Turnover, PC_BT và PC_ON.", False),                                    # N1 (M3 câu 7 thật)
    ("PC_BT không thuộc nhóm chưa ai có tiền.", False),                                                # N2: phủ định SAI (PC_BT 0 / 14) ⇒ kiểm ngược bắt
    ("Chưa ai có tiền ở Turnover và PC_BT, trừ PC_ON.", True),                                         # P5: PC_ON 1 / 14 ⇒ đúng
    ("Chưa ai có tiền ở Turnover, trừ PC_BT.", False),                                                 # N3: "trừ" SAI (PC_BT 0 / 14)
    ("Chưa ai có tiền ở Turnover, PC_BT và PC_ON, không phải chỉ PC_ON.", False),                     # N4: phủ định không đứng trước khẳng định
    ("Bạn đúng—PC_ON có 1/14 người được tiền, nên không thuộc nhóm chưa ai có tiền.\n\n"
     "Theo bảng đội, cả đội chưa ai có tiền ở Turnover và PC_BT.", True),                             # P1 (chữ thật lượt 135422, câu 7)
    ("Đúng, PC_ON có 1 người có tiền (NV03), nên không thuộc nhóm “chưa ai có tiền”.", True),         # P2 (hình câu thật mốc 112234)
    ("PC_ON không phải chưa ai có tiền — NV03 có.", True),                                             # P3
    ("Xin đính chính: PC_ON không phải KPI cả đội chưa ai có tiền. Có 1/14 người được thưởng: NV03.", True),   # câu thật lượt e0b câu 4 (đổi KPI theo TEAM)
    ("Xin đính chính: PC_BT không phải KPI cả đội chưa ai có tiền.", False),                           # cùng chữ, PC_BT 0 / 14 ⇒ chặn
])
def test_none_claim_with_negation_is_checked_in_reverse(answer, ok):
    assert check_owners(answer, [TEAM])["ok"] is ok, check_owners(answer, [TEAM])


@pytest.mark.parametrize("answer", [
    "Không, ASO_Focus chưa ai có tiền.",                                  # "Không" mở đầu không phải phủ định "chưa ai"
    "Không phải ai cũng biết, nhưng ASO_Focus chưa ai có tiền.",         # phủ định không đứng ngay trước khẳng định
    "Chưa ai có tiền ASO_Focus, không phải 13 người như bảng ghi.",       # "không phải" sau KPI không phải chữ loại trừ
    "ASO_Focus không thuộc nhóm có tiền: chưa ai có tiền.",               # "không thuộc nhóm có tiền" ≠ "không thuộc nhóm chưa ai"
    "ASO_Focus và PC_BT không thuộc nhóm chưa ai có tiền.",               # phủ định đúng 1 KPI, sai 1 KPI (PC_BT 0 / 14)
])
def test_negation_traps_still_blocked(answer):
    # kiem_so@9: chữ phủ định không đúng chỗ không làm câu sai lọt (ASO_Focus 13 / 14 có tiền)
    assert check_owners(answer, [TEAM])["ok"] is False, check_owners(answer, [TEAM])


# 10/11 (B, kiem_so@10 — LẦN CUỐI — ca ghi trước ở mục @10) ----------
@pytest.mark.parametrize("answer, ok", [
    ("ASO_Focus không có ai có tiền.", False),                         # C T1 (LỌT ở @9): ASO_Focus 13 / 14 có tiền
    ("Chẳng ai có tiền ASO_Focus.", False),
    ("Chẳng có ai có tiền ở ASO_Focus.", False),
    ("Turnover không có ai có tiền.", True),                            # Turnover 0 / 14
    ("PC_ON không thuộc nhóm không có ai có tiền.", True),              # phủ định + cụm mới, PC_ON 1 / 14
])
def test_more_none_phrases(answer, ok):
    assert check_owners(answer, [TEAM])["ok"] is ok, check_owners(answer, [TEAM])


@pytest.mark.parametrize("answer, ok", [
    ("- PC_BT: 4.100.000 đ; của NV05.", False),        # không có mã trước số ⇒ mã sau ";" vẫn là chủ ⇒ số của NV06 nói cho NV05 ⇒ chặn
    # ĐỔI có chủ đích ở @11 (@10, major 1): @10 ghi True (chủ = NV05 sau ";") — chính luật đó làm "NV05⏎…4.100.000 đ; NV06 cũng vậy" LỌT.
    # Nay: có mã tiêu đề NV06 ⇒ số tiền dừng ở ";" ⇒ chủ NV06 ⇒ 3,2 tr không phải của NV06 ⇒ chặn (nghi thì chặn)
    ("NV06\n- PC_BT: 3.200.000 đ; NV05 lệch.", False),
])
def test_semicolon_is_not_sentence_end_for_owner_after(answer, ok):
    assert check_owners(answer, [TWO], same_sentence_after=True)["ok"] is ok


# 11/11 (B, kiem_so@11 — 3 lỗi lớn @10 — ca D + biến thể B ở) ----------
TWO_PCT = {"employees": [{"employee_id": "NV05", "kpi_amounts": {"PC_BT": 3200000}, "pct": 95},
                         {"employee_id": "NV06", "kpi_amounts": {"PC_BT": 4100000}, "pct": 88}]}


@pytest.mark.parametrize("answer, ok", [
    ("NV05\n- PC_BT: 4.100.000 đ; NV06 cũng vậy.", False),         # D: lọt ở @10 (4,1 tr là của NV06)
    ("NV05\n- PC_BT: 4.100.000 đ, NV06 cũng vậy.", False),         # cùng loại, dấu phẩy ⇒ không rõ chủ ⇒ phải đúng với cả hai
    ("NV05\n- Tỷ lệ đạt: 88 %; NV06 cũng vậy.", False),            # % cũng như tiền
    ("NV05\n- PC_BT: 3.200.000 đ; NV06 thì khác.", True),          # D: chặn oan ở @10
    ("NV05\n- PC_BT: 3.200.000 đ; NV06 được 4.100.000 đ.", True),
    ("4.100.000 đ là tiền PC_BT của NV06.", True),                  # không có mã trước ⇒ mã sau số (luật @10 giữ)
])
def test_money_owner_with_heading_and_code_after(answer, ok):
    assert check_owners(answer, [TWO_PCT], same_sentence_after=True)["ok"] is ok, check_owners(answer, [TWO_PCT], same_sentence_after=True)


def test_list_by_amount_with_colon_keeps_codes_after():
    # Hình câu thật lượt 09/11 moc_da c503 (@11 bản nháp chặn oan): danh sách theo mức tiền, mã dòng trên KHÔNG phải tiêu đề của dòng dưới
    two = {"employees": [{"employee_id": "NV02", "kpi_amounts": {"ASO_Focus": 800000}}, {"employee_id": "NV05", "kpi_amounts": {"ASO_Focus": 1000000}},
                         {"employee_id": "NV08", "kpi_amounts": {"ASO_Focus": 1000000}}]}
    answer = "ASO_Focus:\n- **800.000 đồng:** NV02 — **1 người**\n- **1.000.000 đồng:** NV05, NV08 — **2 người**"
    assert check_owners(answer, [two], same_sentence_after=True)["ok"] is True
    answer_2 = "ASO_Focus:\n- **800.000 đồng:** 1 người — NV02.\n- **1.000.000 đồng:** 2 người — NV05, NV08."   # lượt 2 cùng câu
    assert check_owners(answer_2, [two], same_sentence_after=True)["ok"] is True
    wrong = "ASO_Focus:\n- **800.000 đồng:** NV05 — **1 người**\n- **1.000.000 đồng:** NV02 — **1 người**"   # đổi chỗ ⇒ cả hai số sai chủ
    assert check_owners(wrong, [two], same_sentence_after=True)["ok"] is False


def test_none_claim_direction_stops_at_colon():
    # Câu thật lượt 04/11 g3d c204 (đúng, Turnover 0 / 14): "0 đồng" sau ":" không đổi chiều câu thành "không ai bị 0 đ"
    assert check_owners("Chưa có ai được tiền KPI Turnover: cột Turnover đang là 0 đồng cho cả đội NV01–NV14.", [TEAM])["ok"] is True
    assert check_owners("Chưa có ai được tiền KPI PC_ON: cột PC_ON đang là 0 đồng cho cả đội.", [TEAM])["ok"] is False   # PC_ON 1 / 14


@pytest.mark.parametrize("answer, ok", [
    ("NV05 được 3.200.000 đ; 4.100.000 đ là của NV06.", True),     # giới hạn ghi ở @10 (chặn nhầm) — nay qua
    ("NV05 được 3.200.000 đ; 3.200.000 đ là của NV06.", False),    # vế sau gán số của NV05 cho NV06 ⇒ chặn
    ("NV05 được 3.200.000 đ; 4.100.000 đ, NV06 cũng vậy.", False),  # vế sau mã trần ≠ NV05 ⇒ không rõ chủ ⇒ chặn
])
def test_semicolon_clause_with_its_own_owner(answer, ok):
    assert check_owners(answer, [TWO], same_sentence_after=True)["ok"] is ok, check_owners(answer, [TWO], same_sentence_after=True)


def test_known_limit_money_with_heading_and_comma_blocks_true_sentence():
    # Giới hạn GHI (nghi thì chặn): câu ĐÚNG viết dấu phẩy, mã khác đứng sau ⇒ không rõ chủ ⇒ chặn oan
    assert check_owners("NV05\n- PC_BT: 3.200.000 đ, NV06 thì khác.", [TWO], same_sentence_after=True)["ok"] is False


def test_count_after_semicolon_keeps_code_after():
    # Số đếm thường (không tiền / %) vẫn đọc qua ";" dù có mã tiêu đề — hình câu thật lượt e0b câu 7 (lý do của @10) không bị chặn lại
    answer = "NV14\n- PC_ON: 2 dòng ngoài MCP theo mục 6.2; mã được liệt kê là NV08."
    assert check_owners(answer, [FLAGS_OWNED], same_sentence_after=True)["ok"] is True


@pytest.mark.parametrize("answer, ok", [
    ("PC_ON chưa có ai có tiền.", False), ("ASO_Focus chưa có ai được thưởng.", False), ("ASO_Focus không một ai có tiền.", False),
    ("ASO_Focus chả ai có tiền.", False), ("ASO_Focus không có nhân viên nào có tiền.", False), ("ASO_Focus không người nào có tiền.", False),
    ("PC_ON chẳng người nào có tiền.", False), ("PC_ON chưa có bạn nào được thưởng.", False),
    ("PC_BT chưa có nhân viên nào có tiền.", True), ("Turnover không một ai có tiền.", True),   # 0 / 14 ⇒ đúng
    ("Không người nào bị 0 đ ở LPPC.", True),                                                   # chiều ngược, 14 / 14
])
def test_none_phrases_by_pattern(answer, ok):
    assert check_owners(answer, [TEAM])["ok"] is ok, check_owners(answer, [TEAM])


@pytest.mark.parametrize("answer, ok", [
    ("Không phải ai cũng có tiền LPPC.", False), ("Không phải tất cả đều có tiền LPPC.", False),   # D: 14 / 14 có ⇒ sai
    ("Không phải mọi người đều có tiền LPPC.", False), ("Chưa hẳn tất cả đều có tiền LPPC.", False),
    ("Không phải ai cũng có tiền ASO_Focus.", True),                 # 13 / 14 ⇒ đúng ("không phải ai cũng" = ≥ 1 người không có)
    ("Không phải ai cũng có tiền PC_ON.", True),                     # 1 / 14 ⇒ đúng (@10 chặn oan)
    ("Không phải tất cả đều chưa có tiền PC_ON.", True),             # 1 / 14 có ⇒ đúng
    ("Không phải tất cả đều chưa có tiền PC_BT.", False),            # 0 / 14 ⇒ sai
    ("LPPC không hề có chuyện tất cả đều có tiền.", False),          # phủ định lạ trước "tất cả" ⇒ không rõ nghĩa ⇒ chặn
    ("Tất cả đều có tiền LPPC.", True),
])
def test_not_all_claims_are_checked_in_reverse(answer, ok):
    assert check_owners(answer, [TEAM])["ok"] is ok, check_owners(answer, [TEAM])


def test_known_limit_also_clause_after_negation():
    # Giới hạn GHI (không sửa @10, C N9): "PC_ON không thuộc nhóm chưa ai có tiền, PC_BT cũng vậy" (PC_BT 0 / 14) ⇒ QUA — câu khó gặp
    assert check_owners("PC_ON không thuộc nhóm chưa ai có tiền, PC_BT cũng vậy.", [TEAM])["ok"] is True


# 11/11 (B, kiem_so@12 — LẦN VÁ CUỐI): câu cả đội theo KHUÔN + không cần chữ "tiền" (@11 G3 / G4) ----------
@pytest.mark.parametrize("answer, ok", [
    ("Không phải nhân viên nào cũng có tiền LPPC.", False), ("Không phải bạn nào cũng có tiền LPPC.", False),   # C G3 (14 / 14)
    ("LPPC không phải ai cũng có.", False), ("Không phải ai cũng có LPPC.", False),                           # C G4: thiếu chữ "tiền"
    ("Nhân viên nào cũng có tiền PC_ON.", False), ("PC_ON ai cũng có.", False),                               # khẳng định sai (1 / 14) — lọt từ @10
    ("Ai cũng có ASO_Focus.", False), ("PC_ON chưa ai có.", False), ("Cả đội đều có PC_BT.", False),
    ("Người nào cũng được LPPC.", True), ("LPPC ai cũng có.", True), ("Tất cả đều được LPPC.", True),         # 14 / 14 ⇒ đúng
    ("Không phải người nào cũng có ASO_Focus.", True), ("Không phải ai cũng được PC_BT.", True),             # 13 / 14, 0 / 14 ⇒ đúng
    ("PC_BT không ai được.", True), ("Nhân viên nào cũng chưa có PC_BT.", True),                               # 0 / 14 ⇒ đúng
    ("Tổng tiền ASO_Focus của cả đội chưa có trong công cụ.", True),   # "cả đội" danh từ ⇒ không xét
    ("7 người còn cơ hội lên mốc ASO_Focus.", True),                    # câu đếm vẫn cần chữ tiền ⇒ không xét
])
def test_group_claims_by_pattern_without_money_word(answer, ok):
    assert check_owners(answer, [TEAM])["ok"] is ok, check_owners(answer, [TEAM])


def test_known_limit_count_claim_without_money_word():
    # Giới hạn GHI (@12 chỉ nới câu CẢ ĐỘI / KHÔNG AI): câu đếm "N người" thiếu chữ tiền không xét ("14 người có ASO_Focus" khi 13 / 14 ⇒ QUA)
    assert check_owners("14 người có ASO_Focus.", [TEAM])["ok"] is True


# 11/11 (B, bậc (a) "câu cả đội do code viết sẵn" — sau @12; ca C ở) ----------
from kpi_agent.agent.owner_check import team_sentences, with_team_sentences

STANDARD = team_sentences([TEAM])


def test_team_sentences_are_written_by_code_from_team_table():
    assert STANDARD == ["ASO_Focus: 13 / 14 người có tiền, 1 người chưa có (NV14).", "PC_ON: 1 / 14 người có tiền (NV03), 13 người chưa có.",
                        "LPPC: 14 / 14 người có tiền.", "PC_BT: 0 / 14 người có tiền, 14 người chưa có.",
                        "Turnover: 0 / 14 người có tiền, 14 người chưa có.", "KPI chưa ai có tiền: PC_BT, Turnover.", "KPI cả đội đều có tiền: LPPC."]
    # Không tên thật: mọi từ viết hoa chỉ là "KPI", tên KPI của công cụ hoặc mã che NVxx (tên người Việt luôn viết hoa chữ đầu)
    allowed = {"KPI", "ASO_Focus", "PC_ON", "LPPC", "PC_BT", "Turnover"}
    for sentence in STANDARD:
        assert all(word in allowed or re.fullmatch(r"NV\d+", word) for word in re.findall(r"[^\W\d][\w]*", sentence) if word[0].isupper()), sentence


def test_team_sentences_only_on_team_tools_and_not_on_refusals():
    assert "team_sentences" in with_team_sentences("get_team_bonus_table", TEAM)
    assert "team_sentences" not in with_team_sentences("get_employee_result", TEAM)
    assert with_team_sentences("get_team_kpi_summary", {"refused": "KPI lạ"}) == {"refused": "KPI lạ"}
    summary = {"kpi": "PC_ON", "employee_count": 14, "paid_count": 1, "paid_employee_ids": ["NV03"],
               "unpaid_employee_ids": [f"NV{i:02d}" for i in range(1, 15) if i != 3]}
    assert with_team_sentences("get_team_kpi_summary", summary)["team_sentences"] == ["PC_ON: 1 / 14 người có tiền (NV03), 13 người chưa có."]


@pytest.mark.parametrize("answer, ok", [
    (STANDARD[1], True), ("Theo bảng thưởng đội, " + STANDARD[3], True), (STANDARD[1] + " " + STANDARD[2], True),   # chép nguyên ⇒ qua
    ("PC_ON: 1 / 14 người có tiền (NV05), 13 người chưa có.", False),   # C X5: giả khuôn, đúng số sai mã
    ("PC_ON: 2 / 14 người có tiền, 12 người chưa có.", False),          # giả khuôn, số sai (luật 2)
    ("Toàn đội đều có tiền PC_BT.", False), ("Cả nhóm đều có tiền PC_BT.", False), ("Hết thảy đều có tiền PC_BT.", False),   # 7 câu lọt @12
    ("Mỗi người đều có tiền PC_BT.", False), ("Mọi nhân viên đều có tiền PC_BT.", False),
    ("Không phải mọi nhân viên đều có tiền LPPC.", False), ("Ai cũng đạt PC_BT.", False),
    ("Cả đội đều có tiền PC_ON, tổng 500.000 đ.", False),             # rào A: "đều" + số tiền có nguồn vẫn bị xét
    ("NV13 và NV14 đều có tiền ASO_Focus.", False),                   # ca xấu: AI tự viết câu nhóm sai mã
    ("Có 1 bạn đang có tiền PC_ON: NV05 (500.000 đồng).", False),     # C Y4: sai mã, đúng tiền (luật 1)
    ("Có 1 bạn đang có tiền PC_ON: NV03 (500.000 đồng).", True),      # câu liệt kê đúng + tiền
    ("NV01 và NV02 đều có tiền ASO_Focus.", True),
    ("Cả đội đang nhận tổng 500.000 đ từ PC_ON.", True),              # tổng tiền, không khẳng định nhóm
    ("Tháng này 5 bạn còn cơ hội lên mốc PC_BT.", True), ("NV14 gần mốc ASO_Focus nhất trong nhóm chưa có tiền.", True),
    # bản cuối 2 (64b4d6ea, major — câu thật): liệt kê mã + có tiền ⇒ tập mã so với code
    ("Có 1 bạn đang có tiền PC_ON: NV05 (500.000 đồng).", False), ("Chỉ NV05 có tiền PC_ON.", False), ("NV05 có tiền PC_ON.", False),
    ("Chỉ NV03 có tiền PC_ON.", True), ("Chưa có tiền ASO_Focus: NV13, NV14.", False), ("Chưa có tiền ASO_Focus: NV14.", True),
    ("13 người có tiền ASO_Focus, chỉ NV14 chưa có.", True), ("Riêng NV13 chưa có tiền ASO_Focus.", False),
    ("Cả 14 nhân viên (NV01–NV14) đều đang có 2.000.000 đồng tiền LPPC.", True),   # câu thật 04/11 g3d c201
])
def test_team_claims_must_be_standard_or_checked_by_code(answer, ok):
    result = check_owners(answer, [with_team_sentences("get_team_bonus_table", TEAM)], same_sentence_after=True)
    assert result["ok"] is ok, result


def test_known_limit_team_claims_after_standard_sentences():
    # Giới hạn GHI: câu đếm thiếu chữ tiền không xét ("14 người có ASO_Focus" khi 13 / 14)
    assert check_owners("14 người có ASO_Focus.", [TEAM], same_sentence_after=True)["ok"] is True


def test_two_paid_list_must_equal_code_set():
    # 64b4d6ea: "Có 2 bạn có tiền PC_BT: NV04, NV05." — số 2 đúng (luật 2 qua) nhưng mã sai ⇒ chặn
    two_paid = {"employees": [{"employee_id": f"NV{i:02d}", "kpi_amounts": {"PC_BT": 500000 if i in (4, 9) else 0}} for i in range(1, 15)]}
    tools = [with_team_sentences("get_team_bonus_table", two_paid)]
    assert check_owners("Có 2 bạn có tiền PC_BT: NV04, NV05.", tools, same_sentence_after=True)["ok"] is False
    assert check_owners("Có 2 bạn có tiền PC_BT: NV04, NV09.", tools, same_sentence_after=True)["ok"] is True
    assert check_owners("Cả 14 nhân viên (NV01–NV14) chưa có tiền Turnover.", [TEAM], same_sentence_after=True)["ok"] is True   # dải mã


# 11/11 (B, bản cuối 3): tiền gán nhầm người qua tổng một-người / tiền giả định + câu danh sách chỉ tiêu (@13) ----------
PC_ON_SUMMARY = {"kpi": "PC_ON", "employee_count": 14, "team_amount_total": 1500000, "paid_count": 1, "paid_employee_ids": ["NV04"],
                 "unpaid_employee_ids": [f"NV{i:02d}" for i in range(1, 15) if i != 4],
                 "by_tier": [{"reached_tier": "Đạt 100%", "employee_count": 1, "employee_ids": ["NV04"], "amount_total": 1500000}]}
NV05_GAP = {"employee_id": "NV05", "gaps": [{"kpi": "PC_ON", "amount_if_reached": 1500000, "amount_now": 0}]}   # tiền GIẢ ĐỊNH của NV05


@pytest.mark.parametrize("answer, ok", [
    ("NV05 được 1.500.000 đ ở PC_ON.", False),                 # C: 1,5 tr là tổng đội = tiền NV04 duy nhất
    ("NV04 được 1.500.000 đ ở PC_ON.", True),
    ("Cả đội nhận 1.500.000 đ từ PC_ON.", True),               # tổng đội, không gán cho ai
])
def test_money_of_single_payer_not_given_to_another_employee(answer, ok):
    for tools in ([PC_ON_SUMMARY], [PC_ON_SUMMARY, NV05_GAP]):
        assert check_owners(answer, tools, same_sentence_after=True)["ok"] is ok, (tools, check_owners(answer, tools, same_sentence_after=True))


def test_conditional_money_needs_the_owners_own_tier_cell():
    # Đảo luật 11/11: câu điều kiện chỉ được dùng ô mốc của CHÍNH người đó — lượt không có dữ liệu mốc của NV05 ⇒ không đối chiếu được ⇒ trượt
    answer = "NV05 nếu lên mốc 100 % sẽ được 1.500.000 đ PC_ON."
    assert check_owners(answer, [PC_ON_SUMMARY, NV05_GAP], same_sentence_after=True)["ok"] is True
    assert check_owners(answer, [PC_ON_SUMMARY], same_sentence_after=True)["ok"] is False   # 1,5 tr chỉ là tiền NV04


def test_hypothetical_money_in_table_with_header_is_not_actual():
    # Hình câu thật 04/11 g3c_t5 c103: bảng cơ hội, chữ "nếu đạt" ở dòng tiêu đề bảng
    answer = "Nhân viên · Mốc kế · Thưởng nếu đạt\nNV05 · 100 % · 1.500.000 đồng"
    assert check_owners(answer, [PC_ON_SUMMARY, NV05_GAP], same_sentence_after=True)["ok"] is True
    assert check_owners("Nhân viên · PC_ON\nNV05 · 1.500.000 đồng", [PC_ON_SUMMARY, NV05_GAP], same_sentence_after=True)["ok"] is False


@pytest.mark.parametrize("answer, ok", [
    ("KPI chưa ai có tiền: PC_BT.", False),                    # @13: thiếu Turnover (cũng 0 / 14)
    ("KPI chưa ai có tiền: PC_BT, Turnover.", True), ("Các KPI chưa ai có tiền là PC_BT và Turnover.", True),
    ("KPI cả đội đều có tiền: LPPC.", True), ("KPI cả đội đều có tiền: LPPC, ASO_Focus.", False),   # thừa (ASO_Focus 13 / 14)
    ("PC_BT là KPI chưa ai có tiền.", True),                   # nói về MỘT chỉ tiêu ⇒ luật 2
    ("PC_ON không thuộc nhóm KPI chưa ai có tiền.", True),     # phủ định ⇒ luật 2 kiểm ngược
])
def test_kpi_list_claim_must_name_exact_kpis(answer, ok):
    assert check_owners(answer, [with_team_sentences("get_team_bonus_table", TEAM)], same_sentence_after=True)["ok"] is ok


# 11/11 (B, bản cuối 4): chỉ cấu trúc GIẢ ĐỊNH thật miễn luật tiền thật ----------
@pytest.mark.parametrize("answer, ok", [
    ("NV05 được tổng 1.500.000 đ ở PC_ON.", False), ("NV05 còn được 1.500.000 đ PC_ON.", False),   # C: lọt ở bản 3 ("tổng", "còn" từng được miễn)
    ("NV05 được thêm 1.500.000 đ PC_ON.", False),
    ("NV05 nếu lên mốc 100 % sẽ được 1.500.000 đ PC_ON.", True), ("Giả sử NV05 đạt 100 %, NV05 được 1.500.000 đ PC_ON.", True),
    ("Khi NV05 đạt 100 % thì NV05 được 1.500.000 đ PC_ON.", True),
    ("Tổng PC_ON của cả đội là 1.500.000 đ.", True),                   # không mã ⇒ luật cũ
])
def test_only_real_hypothetical_structure_is_exempt(answer, ok):
    for tools in ([PC_ON_SUMMARY, NV05_GAP],):   # đảo luật: câu điều kiện cần ô mốc của chính NV05 (test_conditional_money_needs_the_owners_own_tier_cell)
        assert check_owners(answer, tools, same_sentence_after=True)["ok"] is ok, (tools, check_owners(answer, tools, same_sentence_after=True))


def test_team_level_difference_is_not_another_persons_money():
    # Hình câu thật 09/11 a_i_da c510: chênh lệch công cụ so sánh tính sẵn (cấp đội) tình cờ trùng tiền một người khác ⇒ không chặn
    # (hình `compare_employees`: hai bên có employee_id + chênh lệch in sẵn có `higher`)
    tools = [{"sides": [{"employee_id": "NV03", "amount": 4000000}, {"employee_id": "NV07", "amount": 3600000}],
              "difference": {"a_minus_b": 400000, "gap": 400000, "higher": "NV03"}},
             {"employee_id": "NV09", "amount": 400000}]
    assert check_owners("NV03 hơn NV07 400.000 đồng.", tools, same_sentence_after=True)["ok"] is True
    # Đảo luật 11/11 (trước là giới hạn): chênh lệch gán làm tiền của một người ⇒ trượt; chữ so sánh đứng SAU số ⇒ trượt
    assert check_owners("NV07 được 400.000 đồng.", tools, same_sentence_after=True)["ok"] is False
    assert check_owners("NV07 được 400.000 đồng, hơn NV03.", tools, same_sentence_after=True)["ok"] is False
    assert check_owners("NV07 được 400.000 đồng.", tools[1:] + [{"employee_id": "NV07", "amount": 3600000}], same_sentence_after=True)["ok"] is False


# 11/11 chiều (B): ĐẢO LUẬT — số tiền sau mã NV phải là tiền của CHÍNH người đó ( câu 1c (b); 13:31) ----------
# Hình dữ liệu như RAW 31/10 (số tự đặt): mốc "Đạt 100%" PC_BT 2.200.000 giống mọi NV; NV05 PC_BT 0 đ; tổng đội 126.660.000; lương khi đạt 100 % 14.540.000
RAW_LIKE = [{"employees": [{"employee_id": f"NV{i:02d}", "business_salary": 14540000 if i == 4 else 9000000 + i * 10000,
                            "kpi_amounts": {"PC_BT": 2200000 if i in (4, 9) else 0, "PC_ON": 1500000 if i == 4 else 0}} for i in range(1, 15)],
             "team_total": 126660000, "salary_if_all_100": 14540000},
            {"employee_id": "NV05", "gaps": [{"kpi": "PC_BT", "next_tier": "Đạt 100%", "gap_to_next": 104, "amount_if_reached": 2200000, "amount_now": 0}]},
            {"kpi": "PC_BT", "team_amount_total": 4400000, "paid_count": 2, "paid_employee_ids": ["NV04", "NV09"],
             "unpaid_employee_ids": [f"NV{i:02d}" for i in range(1, 15) if i not in (4, 9)],
             "by_tier": [{"reached_tier": "Đạt 100%", "employee_count": 2, "employee_ids": ["NV04", "NV09"], "amount_total": 4400000}]},
            {"employee_id": "NV05", "lines": [{"employee_id": "NV05", "item": "T03", "amount": 300000}], "extra_bonus_total": 300000}]


@pytest.mark.parametrize("answer, ok", [
    ("NV05 sẽ nhận 2.200.000 đ PC_BT.", False),                       # lỗ (A): "sẽ" một mình không là điều kiện (A 13:31)
    ("NV05 sẽ được 2.200.000 đ.", False), ("NV05 được thêm 2.200.000 đ PC_BT.", False),
    ("Nếu NV05 đạt 100 % PC_BT thì sẽ nhận 2.200.000 đ.", True),       # điều kiện + ô mốc của chính NV05
    ("NV05 sẽ nhận 2.200.000 đ khi đạt 100 % PC_BT.", True),           # điều kiện đứng sau số
    ("NV05 còn thiếu 104 đơn; khi đạt, thưởng PC_BT là 2.200.000 đ.", True),
    ("NV05: mốc thấp nhất của PC_BT là Đạt 100 %, tương ứng 2.200.000 đồng; hiện NV05 0 đồng.", True),
    ("NV01 được 126.660.000 đ.", False), ("NV05 có 14.540.000 đ lương kinh doanh.", False),   # lỗ (B): số cấp đội gán một NV
    ("NV05 được 2.200.000 đ PC_BT, tổng cả đội 126.660.000 đ.", False),   # 2,2 tr chỉ là ô mốc của NV05 (câu không điều kiện)
    ("NV04 có 14.540.000 đ lương kinh doanh.", True),                  # A 13:39: tiền THẬT của chính NV04 trùng số cấp đội ⇒ qua
    ("NV05 được 0 đ PC_BT. Tổng cả đội: 126.660.000 đ.", True),        # số cấp đội có chữ "tổng cả đội"
    ("Tổng cả đội NV01 nhận 126.660.000 đ.", False),                   # mã chen giữa cụm và số ⇒ vế của NV01
    ("Có 2 bạn có tiền PC_BT: NV04, NV09; tổng thưởng 4.400.000 đồng.", True),   # tổng của đúng nhóm được nêu
    ("NV04 được tổng 4.400.000 đồng PC_BT.", False),                   # tổng nhóm 2 người gán cho một người
    ("NV04 được 2.200.000 đ PC_BT, NV09 cũng 2.200.000 đ.", True),     # tiền thật từng người
    ("NV05 được thưởng thêm 300.000 đ.", True), ("NV06 được thưởng thêm 300.000 đ.", False),   # Thưởng thêm = ô của chính người đó
])
def test_reversed_rule_money_after_code_belongs_to_that_employee(answer, ok):
    assert check_owners(answer, RAW_LIKE, same_sentence_after=True)["ok"] is ok, check_owners(answer, RAW_LIKE, same_sentence_after=True)


def test_reversed_rule_real_money_in_named_fields():
    # Phát lại 942 chữ thật: tiền thật có tên ô trùng phần tên giả định (`over_target_bonus` có "target", `gaps[…].amount_now`)
    tools = [{"employee_id": "NV04", "over_target_bonus": 0, "kpis": [{"kpi": "PC_ON", "amount": 1500000}]},
             {"employee_id": "NV03", "gaps": [{"kpi": "ASO_Focus", "amount_now": 800000, "amount_if_reached": 1000000}]}]
    assert check_owners("NV04 không có thưởng vượt: thưởng 0 đồng.", tools, same_sentence_after=True)["ok"] is True
    assert check_owners("NV03 đang được 800.000 đồng ở ASO_Focus.", tools, same_sentence_after=True)["ok"] is True
    assert check_owners("NV03 đang được 1.000.000 đồng ở ASO_Focus.", tools, same_sentence_after=True)["ok"] is False


def test_reversed_rule_gap_numbers_only_with_gap_words():
    tools = [{"employee_id": "NV01", "gaps": [{"kpi": "Turnover", "gap_to_next": 384601088, "amount_if_reached": 3920000, "amount_now": 0}]}]
    assert check_owners("NV01 cần thêm 384.601.088 đồng doanh số để chạm mốc 95 %.", tools, same_sentence_after=True)["ok"] is True
    assert check_owners("NV01 được 384.601.088 đồng.", tools, same_sentence_after=True)["ok"] is False


# Bản cuối 2 (nhóm kỹ thuật soát @14, major — câu thật m3_v13/12, hình get_mid_month_gap): chữ "mốc" chỉ ở dòng dẫn danh sách
LIST_LEAD = "NV05 còn thiếu để lên các mốc kế tiếp:\n\n- **PC_BT – đạt 100%:** cần thêm **104**; thưởng **{} đồng**."


@pytest.mark.parametrize("amount, ok", [
    ("2.200.000", True),     # ô mốc của chính NV05
    ("4.400.000", False),    # tổng nhóm NV04 + NV09 — dòng dẫn không mở số nhóm / cấp đội
    ("1.500.000", False),    # tiền thật PC_ON của NV04
    ("126.660.000", False),  # tổng đội
])
def test_reversed_rule_list_lead_line_opens_only_own_tier_cells(amount, ok):
    assert check_owners(LIST_LEAD.format(amount), RAW_LIKE, same_sentence_after=True)["ok"] is ok
    # không có dòng dẫn "mốc" ⇒ 2,2 tr (ô mốc, NV05 0 đ) trượt như câu khẳng định
    assert check_owners("NV05:\n- PC_BT: thưởng 2.200.000 đồng.", RAW_LIKE, same_sentence_after=True)["ok"] is False


# Bản cuối 3 (C lách trên _2 — câu): tổng nhóm chia / gán một người, chiều so sánh, cụm cả đội xa số, "nếu / khi" không điều kiện
FIVE = ["NV02", "NV04", "NV09", "NV10", "NV11"]
GROUP_FIVE = {"kpi": "ASO_ALL", "paid_count": 5, "paid_employee_ids": FIVE, "team_amount_total": 5200000,
              "by_tier": [{"reached_tier": "Đạt 100%", "employee_count": 5, "employee_ids": FIVE, "amount_total": 5200000}]}
ROUND3 = [*RAW_LIKE, GROUP_FIVE, *({"employee_id": code, "kpis": [{"kpi": "ASO_ALL", "amount": 1040000}]} for code in FIVE),
          {"sides": [{"employee_id": "NV03", "business_salary": 4000000}, {"employee_id": "NV07", "business_salary": 3600000}],
           "business_salary_difference": {"a_minus_b": 400000, "gap": 400000, "higher": "NV03"}},
          {"team_total": 55700000}]


@pytest.mark.parametrize("answer, ok", [
    ("NV02, NV04, NV09, NV10, NV11 mỗi người được 5.200.000 đ ở ASO_ALL.", False),          # C 4a
    ("Trong NV02, NV04, NV09, NV10, NV11, NV02 nhận 5.200.000 đ ASO_ALL.", False),           # C 4c
    ("Đạt mốc 100 % ASO_ALL: NV02, NV04, NV09, NV10, NV11; tổng thưởng 5.200.000 đồng.", True),
    # kiem_so@23: ĐÃ SỬA chặn oan này — dấu phẩy giữa hai mã là liệt kê ở đường tổng nhóm, 5.200.000 = tổng đúng của đúng 5 mã ⇒ qua
    ("Nhóm NV02, NV04, NV09, NV10 và NV11 được tổng 5.200.000 đ ASO_ALL.", True),
    ("NV07 hơn NV03 400.000 đ.", False), ("NV03 kém NV07 400.000 đ.", False),               # C 5a — ngược chiều
    ("NV03 hơn NV07 400.000 đ.", True), ("NV07 kém NV03 400.000 đ.", True), ("NV07 thấp hơn NV03 400.000 đ.", True),
    ("NV05 được cả đội khen và nhận 55.700.000 đ.", False),                                  # C 6a
    ("NV05 đứng đầu toàn nhóm với 55.700.000 đ.", False), ("NV05 và cả đội đều đạt 14.540.000 đ.", False),   # C 6b, 6d
    ("NV05 được 0 đ PC_BT. Tổng cả đội tháng này: 55.700.000 đ.", True),
    ("NV05 sẽ nhận 2.200.000 đồng khi chốt lương tháng này.", False),                        # C 2d
    ("NV05 được 2.200.000 đ nếu tính theo bảng hiện tại.", False),                           # C 2e
    ("Nếu tính theo bảng hiện tại, NV05 được 2.200.000 đ.", False),
    ("NV05 sẽ nhận 2.200.000 đ khi đạt 100 % PC_BT.", True),
])
def test_round3_narrowed_paths(answer, ok):
    assert check_owners(answer, ROUND3, same_sentence_after=True)["ok"] is ok, check_owners(answer, ROUND3, same_sentence_after=True)


@pytest.mark.parametrize("answer, ok", [
    ("Mức thưởng của NV05:\n- PC_BT: đang được 2.200.000 đồng.", False),          # _2: dòng dẫn "mức" + khẳng định tiền hiện có
    ("NV05 các mốc kế tiếp:\n- PC_BT: đã nhận 2.200.000 đồng.", False),            # dòng dẫn đúng loại nhưng dòng khẳng định "đã nhận"
    ("Mức thưởng của NV05:\n- PC_BT: 2.200.000 đồng.", False),                     # "mức" trần ở dòng dẫn không mở ô mốc
    ("NV05 còn thiếu để lên các mốc kế tiếp:\n- PC_BT: thưởng 2.200.000 đồng.", True),
    ("NV05 đang nhận mức 2.200.000 đ PC_BT.", False),                               # C 2f
    ("NV05 đã lên mốc, được 2.200.000 đ PC_BT.", False),                            # bản 3b — hồi quy ca cũ C L- (NV05 PC_BT 0 đ)
    ("NV05 đang ở mốc 100 %, nhận 2.200.000 đ PC_BT.", False),
    ("Nếu NV05 lên mốc 100 % thì được 2.200.000 đ PC_BT.", True),
    ("NV05 hiện 0 đồng; mốc thấp nhất của PC_BT là Đạt 100 %, tương ứng 2.200.000 đồng.", True),
])
def test_round3_lead_line_only_for_next_tier(answer, ok):
    assert check_owners(answer, ROUND3, same_sentence_after=True)["ok"] is ok, check_owners(answer, ROUND3, same_sentence_after=True)


@pytest.mark.parametrize("answer", [
    "NV05 ở mốc 100 % sẽ nhận 2.200.000 đ PC_BT.",   # chữ "mốc" trước số mở ô mốc của CHÍNH NV05 — câu nói sai NV05 đang ở mốc (cùng loại lỗi CÙNG NV)
])
def test_known_limit_reversed_rule(answer):
    assert check_owners(answer, RAW_LIKE, same_sentence_after=True)["ok"] is True


def test_item_level_closes_old_limit():
    # Giới hạn cũ "luật mức NGƯỜI, không mức KPI" (A 13:31 11/11): 1,5 tr là tiền PC_ON của chính NV04 — kiem_so@22 (lớp đúng KHOẢN) nay CHẶN
    assert check_owners("NV04 được 1.500.000 đ ở PC_BT.", RAW_LIKE, same_sentence_after=True)["ok"] is False


# 12/11 — kiem_so@17: ô CHỈ TIÊU (`target`) không phải tiền thật của người đó (như @14). @15 từng nới (hết chặn oan câu 03 M3 Capitol nhưng lọt "NV04 được
# <chỉ tiêu của chính NV04> đ"); @16 thử nới theo ngữ cảnh bằng dò chữ, không đồng ý ⇒ lùi. Hình kết quả như get_employee_result +
# get_extra_bonus (số tự đặt): 1.300.000.000 = chỉ tiêu Turnover NV04; 23.800.000 = chỉ tiêu Kokomi NV04 (thưởng thật 500.000).
TARGETS = [{"employee_id": "NV04", "kpis": [{"kpi": "Turnover", "target": 1300000000, "actual": 1310000000, "pct": 100.77, "amount": 5600000}]},
           {"employee_id": "NV05", "kpis": [{"kpi": "Turnover", "target": 900000000, "actual": 700000000, "pct": 77.78, "amount": 0}]},
           {"employee_id": "NV04", "lines": [{"employee_id": "NV04", "item": "T06", "target": 23800000, "actual": 20000000, "amount": 500000}],
            "extra_bonus_total": 500000}]


@pytest.mark.parametrize("answer", [
    "NV04 được 23.800.000 đ.", "NV04 được 1.300.000.000 đ.",                    # chỉ tiêu của chính NV04 nói như tiền nhận — lỗ của @15, nay chặn lại
    "Tổng lương kinh doanh theo chỉ tiêu của NV04 là 1.300.000.000 đ.",          # một trong các câu lọt ở @16 (chữ tiền ngoài danh sách dò)
    "Chỉ tiêu Turnover của NV04 là 900.000.000 đ.",                              # chỉ tiêu của NV05 gán cho NV04
    "NV04 — Turnover: thực hiện 700.000.000 đ.",                                 # thực hiện của người khác
])
def test_target_said_as_money_is_blocked(answer):
    assert not check_owners(answer, TARGETS, same_sentence_after=True)["ok"]
    assert not check_owners(answer, TARGETS)["ok"]


@pytest.mark.parametrize("answer", [
    "NV04 — Turnover: chỉ tiêu 1.300.000.000 đ, thực hiện 1.310.000.000 đ, tiền 5.600.000 đ.",
    "NV04\n| KPI | Chỉ tiêu | Thực hiện |\n| Turnover | 1.300.000.000 đ | 1.310.000.000 đ |",
])
def test_known_false_block_own_target_written_with_money_unit(answer):
    # CHẶN OAN ĐÃ BIẾT (giới hạn ghi ở bao_cao/04 "Còn hở"): câu ĐÚNG nêu chỉ tiêu của chính NV kèm "đ" bị coi là tiền không phải của họ
    # (M3 Capitol câu 03, 1 / 2 lượt 12/11). Bỏ chữ "đ" sau chỉ tiêu thì qua. Giữ ca này để lần sau đổi hành vi phải có chủ ý.
    assert not check_owners(answer, TARGETS, same_sentence_after=True)["ok"]
    without_unit = answer.replace("1.300.000.000 đ", "1.300.000.000")
    assert check_owners(without_unit, TARGETS, same_sentence_after=True)["ok"]


# ---------- kiem_so@18 (13/11) — DANH SÁCH MỐC của một KPI dưới tên người được tra: tiền của mốc không phải "tiền của người đó" ----------
# Kết quả `explain_kpi` dựng theo đúng hình + số thật của NV05 / Turnover trên RAW 31/10 (hai mốc; NV05 đang ở mốc 100 %).
TIER_RESULT = {"employee_id": "NV05", "kpi": "Turnover", "cong_van_section": "mục 2.1", "target": 838192683, "actual": 849600000, "pct": 101.36,
               "tiers": [{"label": "Đạt 95%", "min": 95, "amount": 3920000}, {"label": "Đạt 100%", "min": 100, "amount": 5600000}],
               "amount": 5600000, "reason": "Turnover: thực hiện 101.36% ⇒ đạt mốc 'Đạt 100%' ⇒ 5.600.000 đ", "params_used": {"employee_id": "NV05", "kpi": "Turnover"}}
TIER_QUESTION = "KPI Turnover của NV05: chỉ tiêu bao nhiêu, có những mốc nào, mỗi mốc được bao nhiêu tiền?"
# Hai câu trả lời THẬT lần đầu của mô hình bị @17 chặn (gửi nguyên văn 13/11) ⇒ mô hình viết lại và BỎ mốc 95 % (đủ hai mốc 6 / 8 ⇒ 1 / 8)
REAL_TIER_LISTINGS = [
    "**NV05 – Turnover** (công văn mục 2.1): chỉ tiêu **838.192.683**. Các mốc thưởng là:\n\n- Đạt 95%: **3.920.000 đ**\n- Đạt 100%: **5.600.000 đ**\n\n"
    "Số liệu **tạm tính — chưa phải số chốt, chưa duyệt**.",
    "NV05 có chỉ tiêu Turnover **838.192.683**. Theo công văn mục **2.1**, các mốc thưởng là:\n\n- Đạt 95%: **3.920.000 đ**\n- Đạt 100%: **5.600.000 đ**\n\n"
    "Hiện NV05 đạt **101,36%**, tương ứng mốc Đạt 100% và **5.600.000 đ** thưởng. Số liệu tạm tính — chưa phải số chốt, chưa duyệt.",
    "NV05 – KPI Turnover\n- Đạt 95 %: 3.920.000 đ\n- Đạt 100 %: 5.600.000 đ",
]
TIER_LISTING_STILL_BLOCKED = {
    "tiền của mốc nói thành tiền người đó nhận": "NV05 được 3.920.000 đ ở Turnover.",
    "% của mốc nói thành % đạt": "NV05 đạt 95 % Turnover.",
    "nhãn mốc ghép sai tiền": "NV05 – KPI Turnover\n- Đạt 95%: 5.600.000 đ\n- Đạt 100%: 3.920.000 đ",
    "một cặp lẻ, không chữ mốc": "NV05 – Turnover: Đạt 95%: 3.920.000 đ",
    "một cặp lẻ trên một gạch": "NV05 – KPI Turnover\n- Đạt 95%: 3.920.000 đ",
    "dòng có chữ khẳng định tiền hiện có": "NV05 – KPI Turnover\n- Đạt 95%: NV05 được 3.920.000 đ\n- Đạt 100%: 5.600.000 đ",
    "hai mốc cách nhau dòng trống, mốc thấp gán cho người": "NV05 – KPI Turnover\n- Đạt 100%: 5.600.000 đ\n\nNV05 nhận 3.920.000 đ.",
    "danh sách mốc của người KHÁC": "NV07 – KPI Turnover\n- Đạt 95%: 3.920.000 đ\n- Đạt 100%: 5.600.000 đ",
}


@pytest.mark.parametrize("answer", REAL_TIER_LISTINGS)
def test_tier_listing_under_the_asked_person_passes(answer):
    result = check_owners(answer, [TIER_RESULT], question=TIER_QUESTION)
    assert result["ok"], result["problems"]


@pytest.mark.parametrize("answer", TIER_LISTING_STILL_BLOCKED.values(), ids=TIER_LISTING_STILL_BLOCKED.keys())
def test_tier_money_or_percent_claimed_for_the_person_is_still_blocked(answer):
    other = {"employee_id": "NV07", "kpi": "Turnover", "pct": 80.0, "amount": 0, "params_used": {"employee_id": "NV07"}}   # NV07: không có ô mốc trong lượt
    result = check_owners(answer, [TIER_RESULT, other], question=TIER_QUESTION)
    assert not result["ok"], answer


# kiem_so@23 (17/11, HẸP): dấu phẩy giữa hai mã là liệt kê — CHỈ ở đường tổng nhóm, số phải là tổng của ĐÚNG tập mã ----------
# Đo công cụ tính 17/11 (câu CT4 ⚠ cả 2 lượt): "Nhóm 7 KPI (NV01, NV02, …): tổng …" từng bị gán cho mã cuối. 15 câu: 3 câu đổi chặn ⇒ qua (đúng ý),
# 12 câu giữ y như @22 (E so bản develop c67fbe67 ↔ nhánh bằng /tmp/mk_e_so/so_rao_e.py 17/11).
_P = lambda nv, sal: {"employee_id": nv, "business_salary": sal, "kpis": [{"kpi": "Turnover", "amount": sal}]}
_LIST_RESULTS = [
    {"team_total": 1000, "employees": [_P("NV01", 100), _P("NV02", 150), _P("NV03", 200), _P("NV05", 50), _P("NV06", 500)]},
    {"tool": "calc_sum", "total": 300, "unit": "đồng", "employee_ids": ["NV01", "NV02", "NV05"],
     "sources": [{"field": "business_salary", "value": v, "employee_id": nv} for nv, v in (("NV01", 100), ("NV02", 150), ("NV05", 50))]},
    {"tool": "calc_group_total", "groups": [{"group": 7, "total": 450, "employee_ids": ["NV01", "NV02", "NV03"]},
                                            {"group": 8, "total": 550, "employee_ids": ["NV05", "NV06"]}], "table_total": 1000}]


@pytest.mark.parametrize("text, ok", [
    ("Tổng lương của NV01, NV02 và NV05 là 300 đ.", True),
    ("Tổng của NV01, NV02, NV05 là 300 đ.", True),
    ("- Nhóm 7 KPI (NV01, NV02, NV03): tổng 450 đ.\n- Nhóm 8 KPI (NV05, NV06): tổng 550 đ.", True),
    ("Tổng của NV01, NV02 và NV05 là 450 đ.", False),                     # tổng của nhóm khác
    ("Tổng của NV01, NV02 và NV06 là 300 đ.", False),                     # lẫn mã người khác
    ("Tổng của NV01, NV02 và NV05 là 150 đ.", False),                     # tiền của một người trong nhóm
    ("NV01, NV02 và NV05 mỗi người được 300 đ.", False),                  # chia đều
    ("Tổng của NV01, NV02 là 300 đ.", False),                             # thiếu một mã
    ("Tổng của NV01, NV02, NV03 và NV05 là 300 đ.", False),               # thừa một mã
    ("NV01, NV02 được khen, NV05 nhận 300 đ.", False),                    # vế khác gán cho NV05
    ("Tổng của NV01, NV02 và NV05 là 300 đ, NV05 riêng được 300 đ.", False),
    ("NV05: 300 đ.", False),
    ("Tổng của nhóm NV01 + NV02 + NV05: 300 đ.", True),
    ("NV02 được 150 đ.", True),
    ("NV06, NV05 lần lượt 500 đ và 50 đ.", False),                        # như @22 (giới hạn cũ, không nới)
])
def test_list_comma_only_for_exact_group_total(text, ok):
    assert check_owners(text, _LIST_RESULTS, "", (), same_sentence_after=True)["ok"] is ok
