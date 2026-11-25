"""Chạy thật trên dữ liệu tháng 10 (file ở sample_data/thang_10/, Git bỏ qua — máy khác không có thì bỏ qua test).

G1: đọc đủ 14 NV × 8 KPI, cảnh báo đúng. G2 tầng 1: thực hiện + % khớp bảng 1 sheet 9_KPI 112/112.
"""
from datetime import date
from pathlib import Path

import pytest

from kpi_agent.core.check_data import check_data
from kpi_agent.core.compute_kpi import compute_actuals
from kpi_agent.core.load_data import load_targets, load_tracking, load_tracking_kpi_tables
from kpi_agent.core.rules import KPI_ORDER, load_rules

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
TRACKING = ROOT / "sample_data/thang_10/Tracking.xlsx"
CONFIG = ROOT / "kpi_agent/config/rules_2026_10.yaml"

pytestmark = pytest.mark.skipif(not (TARGETS.exists() and TRACKING.exists()), reason="không có dữ liệu thật trên máy này")


@pytest.fixture(scope="module")
def data():
    rules = load_rules(CONFIG)
    targets, tracking = load_targets(TARGETS), load_tracking(TRACKING)
    return rules, targets, tracking, compute_actuals(targets, tracking, rules)


def test_reads_14_employees_8_kpis(data):
    _, targets, tracking, results = data
    assert len(targets["employees"]) == 14
    assert all(list(by_kpi) == KPI_ORDER for by_kpi in results.values())
    assert set(tracking["blocks"]) == set(KPI_ORDER)


def test_warnings_on_real_data(data):
    _, targets, tracking, _ = data
    issues = check_data(targets, tracking, date(2026, 10, 20))
    messages = " | ".join(i["message"] for i in issues)
    assert not [i for i in issues if i["level"] == "error"]
    assert "ASO_ALL: 1 cửa hàng không thuộc MCP" in messages
    assert "khớp 38/42" in messages
    # Ngày chốt sau lần làm mới pivot ⇒ phải cảnh báo dữ liệu cũ
    late = " | ".join(i["message"] for i in check_data(targets, tracking, date(2026, 10, 31)))
    assert "không trước lần làm mới pivot" in late


def test_tier_1_matches_tracking_table_1(data):
    _, targets, _, results = data
    table = load_tracking_kpi_tables(TRACKING)["table_1_rawdata"]
    mismatches = []
    for code, by_kpi in results.items():
        for kpi, r in by_kpi.items():
            book = table[code][kpi]
            tolerance = 1 if kpi == "Turnover" else (1e-4 if kpi in ("LPPC", "LPPC_Meat") else 0)
            if abs(r.actual - book["actual"]) > tolerance or abs(r.pct - book["pct"]) > 1e-4 or r.target != targets["targets"][code][kpi]:
                mismatches.append((code, kpi, r.actual, book["actual"]))
    assert mismatches == []
