"""Công cụ 4 `simulate_kpi_change`: T1 (số đúng) + T2 (tham số xấu ⇒ từ chối), 0 USD, dữ liệu thật tháng 10.

Phép so đi ĐƯỜNG KHÁC: thêm đúng phần còn thiếu ⇒ tiền kịch bản = `amount_if_reached` của `get_mid_month_gap` (tiền in sẵn của mốc trong file luật —
không qua `bonus_for_kpi`). Thêm 0 ⇒ trùng tiền hiện tại của cả 14 NV × 6 KPI cộng được."""
import json
from datetime import date
from pathlib import Path

import pytest

from kpi_agent.workflow import runner

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
ADDITIVE = ["Turnover", "PC_BT", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON"]


@pytest.fixture(scope="module")
def ctx():
    from kpi_agent.backend.queries import build_context
    return build_context(date(2026, 10, 20), mask_path=str(NEEDED[2]))


def test_extra_zero_equals_current_money_everywhere(ctx):
    from kpi_agent.backend.queries import get_employee_result
    from kpi_agent.backend.what_if import simulate_kpi_change
    for code in sorted(ctx.to_real):
        current = {row["kpi"]: row["amount"] for row in get_employee_result(ctx, code)["kpis"]}
        for kpi in ADDITIVE:
            body = simulate_kpi_change(ctx, code, kpi, 0)
            assert body["scenario"] == body["now"] and body["amount_change"] == 0, (code, kpi)
            assert body["now"]["amount"] == current[kpi], (code, kpi)


def test_adding_exact_gap_reaches_next_tier_money_from_other_path(ctx):
    from kpi_agent.backend.queries import get_mid_month_gap
    from kpi_agent.backend.what_if import simulate_kpi_change
    checked = 0
    for employee in get_mid_month_gap(ctx)["employees"]:
        for gap in employee["gaps"]:
            if gap["kpi"] not in ADDITIVE:
                continue
            body = simulate_kpi_change(ctx, employee["employee_id"], gap["kpi"], gap["gap_to_next"])
            if "refused" in body:          # phần thiếu > chỉ tiêu (không có ở tháng 10 — kiểm dưới)
                continue
            assert body["scenario"]["reached_tier"] == gap["next_tier"], (employee["employee_id"], gap["kpi"])
            assert body["scenario"]["amount"] == gap["amount_if_reached"] and body["amount_change"] == gap["amount_if_reached"] - gap["amount_now"]
            checked += 1
            # thiếu MỘT đơn (KPI đếm) ⇒ chưa tới mốc
            if gap["kpi"] != "Turnover" and gap["gap_to_next"] >= 1:
                short = simulate_kpi_change(ctx, employee["employee_id"], gap["kpi"], gap["gap_to_next"] - 1)
                assert short["scenario"]["reached_tier"] == body["now"]["reached_tier"]
    assert checked >= 50          # tháng 10: hầu hết cặp NV × KPI cộng được còn mốc kế (đã đếm khi viết test)


@pytest.mark.parametrize("args", [
    {"employee_id": "NV01", "kpi": "LPPC", "extra": 1},                 # KPI tỷ lệ
    {"employee_id": "NV01", "kpi": "LPPC_Meat", "extra": 1},
    {"employee_id": "NV01", "kpi": "PC_BT", "extra": -1},
    {"employee_id": "NV01", "kpi": "PC_BT", "extra": 2.5},              # đơn phải là số nguyên
    {"employee_id": "NV01", "kpi": "PC_BT", "extra": "năm"},
    {"employee_id": "NV01", "kpi": "PC_BT", "extra": True},
    {"employee_id": "NV01", "kpi": "PC_BT", "extra": "1e9"},            # vượt mức mốc cao nhất
    {"employee_id": "NV01", "kpi": "PC_BT", "extra": "nan"},
    {"employee_id": "NV01", "kpi": "PC_BT", "extra": None},
    {"employee_id": "NV01/../NV02", "kpi": "PC_BT", "extra": 1},
    {"employee_id": "NV99", "kpi": "PC_BT", "extra": 1},
    {"employee_id": "NV01", "kpi": "../PC_BT", "extra": 1},
    {"employee_id": "NV01", "kpi": "PC_BT", "extra": 1, "period": "2026-11"},
])
def test_bad_params_refused_without_numbers(ctx, args):
    from kpi_agent.backend.what_if import simulate_kpi_change
    body = simulate_kpi_change(ctx, **args)
    assert set(body) == {"refused", "params_used"} and "scenario" not in body


def test_result_labels_assumptions_and_no_real_codes(ctx):
    from kpi_agent.backend.what_if import LABEL, simulate_kpi_change
    body = simulate_kpi_change(ctx, "nv07", "pc_bt", "5")             # chữ thường + chữ số (tham số HTTP) vẫn nhận
    assert body["label"] == LABEL and "GIẢ ĐỊNH" in body["label"] and len(body["assumptions"]) == 3
    assert body["params_used"] == {"employee_id": "NV07", "kpi": "PC_BT", "extra": 5, "period": "2026-10"}
    assert body["scenario"]["actual"] == body["now"]["actual"] + 5
    text = json.dumps(body, ensure_ascii=False)
    assert not any(code in text for code in runner.load_mask()) and not runner.REAL_CODE_PATTERN.search(text)
    turnover = simulate_kpi_change(ctx, "NV07", "Turnover", 1_000_000.5)   # doanh số nhận số lẻ đồng
    assert turnover["extra"] == 1_000_000.5 and turnover["scenario"]["pct"] > turnover["now"]["pct"]
    assert abs(turnover["scenario"]["actual"] - turnover["now"]["actual"] - 1_000_000.5) <= 1     # số hiện làm tròn đồng


def test_cap_is_top_tier_level_not_target(ctx):
    # G2 (minor 1): trần = % mốc cao nhất × chỉ tiêu (tháng 10 mốc cao nhất 100 % ⇒ đúng bằng chỉ tiêu); +1 vượt ⇒ từ chối
    from kpi_agent.backend.queries import _check_employee
    from kpi_agent.backend.what_if import simulate_kpi_change
    from kpi_agent.core.compute_bonus import kpi_tiers
    real, _ = _check_employee(ctx, "NV07")
    target = ctx.results[real]["PC_BT"].target
    top = max(tier["min"] for tier in kpi_tiers(ctx.rules, "PC_BT", ctx.regions[real]))
    cap = int(top * target / 100)
    assert "refused" not in simulate_kpi_change(ctx, "NV07", "PC_BT", cap)
    assert "refused" in simulate_kpi_change(ctx, "NV07", "PC_BT", cap + 1)


def test_bad_employee_refusal_never_echoes_input(ctx):
    # T2 (minor): mã thật gửi vào ⇒ từ chối mà KHÔNG nhắc lại chữ đã gửi (cả lời lẫn params_used)
    from kpi_agent.backend.what_if import simulate_kpi_change
    for sent in [*list(runner.load_mask())[:3], "NV99", "NV01/../NV02", "x" * 50]:
        body = simulate_kpi_change(ctx, sent, "PC_BT", 1)
        assert set(body) == {"refused", "params_used"} and body["params_used"]["employee_id"] is None
        assert sent not in json.dumps(body, ensure_ascii=False), sent


def test_spaces_and_case_normalised_to_same_employee(ctx):
    # SAU khi thấy kết quả T2 (04/11): "NV01 " / " nv01" là CHUẨN HOÁ đầu vào (như 4 công cụ cũ), không phải mã lạ
    from kpi_agent.backend.what_if import simulate_kpi_change
    base = simulate_kpi_change(ctx, "NV01", "PC_BT", 3)
    for sent in ("NV01 ", " nv01", "nV01\t"):
        assert simulate_kpi_change(ctx, sent, "PC_BT", 3) == base, repr(sent)
