"""Kiểm khuôn file luật + hàm mốc ⇒ tiền.

Các phép thử mốc ở đây (đúng 95 %, 89,99 %, ví dụ thưởng vượt in trong công văn) CHỈ KIỂM HÀM — không phải bằng chứng
kết quả tháng 10. Số đầu vào nhỏ, viết tay; bằng chứng trên dữ liệu thật ở test_real_data.py."""
import copy
from pathlib import Path

import pytest
import yaml

from kpi_agent.core.compute_bonus import bonus_for_kpi, employee_bonus, floor_pct, over_target_bonus
from kpi_agent.core.compute_kpi import KpiResult, working_days
from kpi_agent.core.rules import KPI_ORDER, RulesError, load_rules, validate_rules

CONFIG = Path(__file__).resolve().parents[1] / "config" / "rules_2026_10.yaml"
REGION = "CD - Nam Trung Bộ"


@pytest.fixture(scope="module")
def rules():
    return load_rules(CONFIG)


def _result(kpi, target, actual):
    return KpiResult(kpi, target, actual, actual, 1, "phép thử hàm")


def test_config_passes_validation(rules):
    assert validate_rules(rules) == []
    assert sum(rules["kpis"][k]["amount_100"] for k in KPI_ORDER) == 14_540_000


def test_typo_95_as_59_is_blocked_before_compute(tmp_path):
    # Tiêu chí 2 mục 3: một lỗi gõ cài sẵn phải bị chặn trước khi tính
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    raw["kpis"]["Turnover"]["tiers"][0]["min"] = 59
    bad = tmp_path / "rules_bad.yaml"
    bad.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RulesError, match="min=59"):
        load_rules(bad)


def test_amount_typo_is_blocked(rules):
    broken = copy.deepcopy(rules)
    broken["kpis"]["PC_ON"]["amount_100"] = 1_050_000
    errors = validate_rules(broken)
    assert any("tổng tiền" in e for e in errors) and any("PC_ON" in e for e in errors)


def test_unsupported_assumption_is_blocked(rules):
    broken = copy.deepcopy(rules)
    broken["assumptions"]["turnover_basis"] = "after_discount"
    assert any("turnover_basis" in e for e in validate_rules(broken))


@pytest.mark.parametrize("actual, expected", [
    (95, 3_920_000),        # đúng mốc 95 % ⇒ 70 % của 5,6 tr (bảng mục 5.2 in sẵn 3.920.000)
    (94.99, 0),             # hụt mốc một chút ⇒ 0
    (100, 5_600_000),
    (130, 5_600_000),       # công ty chi tối đa mức 100 %; phần vượt là thưởng vượt riêng
])
def test_turnover_tiers(rules, actual, expected):
    line = bonus_for_kpi(_result("Turnover", 100, actual), rules, REGION)
    assert line.amount == expected


def test_turnover_exact_95_with_real_size_target(rules):
    # Chỉ tiêu cỡ thật: 0.95 * 941438285 tính bằng số thực bị lệch; Decimal phải ra đúng mốc
    target = 941_438_285
    line = bonus_for_kpi(_result("Turnover", target, 894_366_370.75), rules, REGION)
    assert line.amount == 3_920_000
    # Tiền trong chữ lý do viết kiểu Việt (13/11 — trang từng hiện "⇒ 2,200,000 đ"); % giữ nguyên cách của ô pct (luật 09/11)
    assert line.reason.endswith("⇒ 3.920.000 đ") and "95.00%" in line.reason


@pytest.mark.parametrize("kpi, target, actual, expected", [
    ("ASO_Focus", 72, 64.8, 800_000),     # đúng 90 % ⇒ 80 % tiền
    ("ASO_Focus", 72, 64, 0),             # 88,9 % ⇒ 0
    ("ASO_Focus", 72, 72, 1_000_000),
    ("ASO_Focus_2", 26, 26, 800_000),     # đúng 100 %
    ("ASO_Focus_2", 26, 24, 640_000),     # 92 % ⇒ 640 k (bảng mục 5.2)
    ("PC_BT", 269, 268, 0),
    ("PC_ON", 61, 61, 1_500_000),
    ("ASO_ALL", 139, 139, 1_040_000),
])
def test_pct_tiers(rules, kpi, target, actual, expected):
    assert bonus_for_kpi(_result(kpi, target, actual), rules, REGION).amount == expected


@pytest.mark.parametrize("kpi, actual, expected", [
    ("LPPC", 4.49, 0), ("LPPC", 4.5, 1_800_000), ("LPPC", 4.7, 2_000_000), ("LPPC", 6.0, 2_000_000),
    ("LPPC_Meat", 3.79, 0), ("LPPC_Meat", 3.8, 400_000),
])
def test_value_tiers_nam_trung_bo(rules, kpi, actual, expected):
    target = 4.7 if kpi == "LPPC" else 3.8
    assert bonus_for_kpi(_result(kpi, target, actual), rules, REGION).amount == expected


def test_lppc_unknown_region_is_not_guessed(rules):
    with pytest.raises(ValueError, match="region_map"):
        bonus_for_kpi(_result("LPPC", 4.7, 5.0), rules, "CD - Vùng lạ")


def test_gap_to_next_tier_rounds_up_for_counts(rules):
    line = bonus_for_kpi(_result("ASO_Focus", 72, 60), rules, REGION)
    assert (line.next_label, line.gap_to_next) == ("Đạt 90%", 5)   # cần 64,8 ⇒ 65 cửa hàng


def test_over_target_bonus_example_printed_in_cong_van(rules):
    # Mục 7.2, ví dụ in sẵn: chỉ tiêu 800 tr, đạt 910 tr (114 %), NPP 108 % ⇒ 2,2 tr ⇒ trần 2 tr
    amount, reason = over_target_bonus(800_000_000, 910_000_000, 108, rules)
    assert amount == 2_000_000 and "trần" in reason
    # Số tiền trong chữ lý do viết kiểu Việt (13/11 — trước in "2,200,000 đ ⇒ 2,000,000 đ" cạnh các chữ khác kiểu "2.200.000 đ")
    assert reason == "2 % × (910.000.000 − 800.000.000) = 2.200.000 đ ⇒ 2.000.000 đ (chạm trần)"
    # Người B của ví dụ: 500 tr / 500 tr = 100 % ≤ 103 % ⇒ không thưởng
    assert over_target_bonus(500_000_000, 500_000_000, 108, rules)[0] == 0


def test_over_target_bonus_needs_npp_data(rules):
    assert over_target_bonus(800_000_000, 910_000_000, None, rules)[0] is None
    assert over_target_bonus(800_000_000, 910_000_000, 99, rules)[0] == 0
    assert over_target_bonus(800_000_000, 824_000_000, 108, rules)[0] == 0   # đúng 103 % ⇒ chưa "vượt trên 103 %"


@pytest.mark.parametrize("target, actual, shown", [
    (1_314_914_524, 796_038_773, "60.53 %"),    # số thật NV04 T4 04/11: trước in "60.5 %" khi ô pct = 60.53
    (1_000_000, 605_599, "60.55 %"),            # 60,5599 làm tròn XUỐNG — `.1f` cũ in "60.6 %" (làm tròn LÊN)
    (1_000_000, 1_029_999, "102.99 %"),         # sát ngưỡng 103 %: không hiện "103.0 %"
])
def test_over_target_reason_shows_pct_like_tool_field(rules, target, actual, shown):
    # chọn (b): chữ lý do in % bằng ĐÚNG cách của ô pct (floor_pct — cùng hàm với backend `_pct`)
    amount, reason = over_target_bonus(target, actual, 108, rules)
    assert amount == 0 and f"doanh số đạt {shown} ≤" in reason
    assert f"{floor_pct(actual / target)} %" == shown


def test_all_kpis_at_100_pct_pay_full_business_salary(rules):
    targets = {"Turnover": 900, "PC_BT": 269, "LPPC": 4.7, "ASO_ALL": 139, "ASO_Focus": 72, "ASO_Focus_2": 26,
               "PC_ON": 61, "LPPC_Meat": 3.8}
    results = {k: _result(k, t, t) for k, t in targets.items()}
    assert employee_bonus(results, rules, REGION)["business_salary"] == 14_540_000


def test_working_days_october_2026(rules):
    # 31 ngày − 4 Chủ nhật (4, 11, 18, 25/10) = 27; sổ Tracking ghi cứng 25 (phát hiện, HIEU_DU_LIEU)
    from datetime import date
    assert len(working_days("2026-10", rules)) == 27
    assert len(working_days("2026-10", rules, until=date(2026, 10, 20))) == 17


# Sau kiểm độc lập (02/11, KIEM_KPI_B.md): các lỗi gõ đã lọt nay phải bị chặn ---
@pytest.mark.parametrize("kpi, tier_index, ratio", [
    ("ASO_Focus", 0, 0.08),     # major: 0,80 ⇒ 0,08 làm tổng đội tụt 2,88 tr âm thầm
    ("ASO_Focus", 0, 0.85),
])
def test_pay_ratio_typo_is_blocked(rules, kpi, tier_index, ratio):
    broken = copy.deepcopy(rules)
    broken["kpis"][kpi]["tiers"][tier_index]["pay_ratio"] = ratio
    assert any("tiền in sẵn" in e for e in validate_rules(broken))


def test_lppc_region_pay_ratio_typo_is_blocked(rules):
    broken = copy.deepcopy(rules)
    broken["kpis"]["LPPC"]["region_tiers"]["Nam Trung Bộ & Tây Nguyên"][0]["pay_ratio"] = 0.09
    assert any("LPPC" in e and "tiền in sẵn" in e for e in validate_rules(broken))


@pytest.mark.parametrize("key, value", [("rate_pct", 20), ("cap", 20_000_000), ("employee_above_pct", 130), ("npp_min_pct", 110)])
def test_over_target_parameter_typo_is_blocked(rules, key, value):
    broken = copy.deepcopy(rules)
    broken["over_target_bonus"][key] = value
    assert any("over_target_bonus" in e for e in validate_rules(broken))


# Minor 1 (02/11): 4 lỗi gõ ngưỡng này lọt qua 2 ví dụ in sẵn mục 7.2 ⇒ phải bị chặn bằng nhãn chép chữ công văn
@pytest.mark.parametrize("key, value", [("employee_above_pct", 100), ("employee_above_pct", 104),
                                        ("employee_above_pct", 110), ("npp_min_pct", 90)])
def test_over_target_threshold_typo_is_blocked_by_label(rules, key, value):
    broken = copy.deepcopy(rules)
    broken["over_target_bonus"][key] = value
    assert any(f"over_target_bonus.{key}" in e and "chữ công văn" in e for e in validate_rules(broken))


@pytest.mark.parametrize("holiday, message", [("2026-10-32", "không phải ngày"), ("2026-09-02", "không thuộc kỳ")])
def test_bad_holiday_is_reported_not_traceback(rules, holiday, message):
    broken = copy.deepcopy(rules)
    broken["working_days"]["holidays"] = [holiday]
    assert any(message in e for e in validate_rules(broken))
