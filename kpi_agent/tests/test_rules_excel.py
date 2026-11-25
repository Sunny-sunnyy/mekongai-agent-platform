"""Cách B (luật trong Excel) so với cách A (YAML) — tiêu chí 1 + 2 mục 3: cùng luật ⇒ cùng tiền; lỗi gõ bị chặn."""
from pathlib import Path

import pytest
from openpyxl import load_workbook

from kpi_agent.core.rules import RulesError, load_rules
from kpi_agent.core.rules_excel import load_rules_excel

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def test_excel_rules_equal_yaml_rules():
    from_yaml = load_rules(CONFIG_DIR / "rules_2026_10.yaml")
    from_excel = load_rules_excel(CONFIG_DIR / "rules_2026_10.xlsx")
    for section in ("period", "business_salary_total", "kpis", "working_days", "assumptions", "over_target_bonus", "region_map"):
        assert from_excel[section] == from_yaml[section], section


def test_excel_typo_95_as_59_is_blocked(tmp_path):
    wb = load_workbook(CONFIG_DIR / "rules_2026_10.xlsx")
    ws = wb["moc_thuong"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value == "Turnover" and row[5].value == 95:
            row[5].value = 59
    bad = tmp_path / "rules_bad.xlsx"
    wb.save(bad)
    with pytest.raises(RulesError, match="min=59"):
        load_rules_excel(bad)


@pytest.mark.parametrize("key, value", [("over_target_bonus.employee_above_pct", 104), ("over_target_bonus.npp_min_pct", 90)])
def test_excel_over_target_threshold_typo_is_blocked(tmp_path, key, value):
    # Minor 1 (02/11): ngưỡng thưởng vượt gõ sai trong sheet `chung` phải bị chặn bằng nhãn chép chữ công văn
    wb = load_workbook(CONFIG_DIR / "rules_2026_10.xlsx")
    for row in wb["chung"].iter_rows(min_row=2):
        if row[0].value == key:
            row[1].value = value
    bad = tmp_path / "rules_bad_threshold.xlsx"
    wb.save(bad)
    with pytest.raises(RulesError, match="chữ công văn"):
        load_rules_excel(bad)


def test_excel_pay_ratio_typo_is_blocked(tmp_path):
    # Major (02/11): tỷ lệ tiền mốc ASO_Focus 90 % gõ 0,80 ⇒ 0,08 trong Excel phải bị chặn
    wb = load_workbook(CONFIG_DIR / "rules_2026_10.xlsx")
    ws = wb["moc_thuong"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value == "ASO_Focus" and row[5].value == 90:
            row[6].value = 0.08
    bad = tmp_path / "rules_bad_ratio.xlsx"
    wb.save(bad)
    with pytest.raises(RulesError, match="tiền in sẵn"):
        load_rules_excel(bad)
