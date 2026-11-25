"""A-ii-1: `simulate_kpi_change` nhận "nếu đạt X % chỉ tiêu"
(`target_pct`) cạnh "nếu thêm N" (`extra`) — đúng một trong hai; KPI tỷ lệ từ chối rõ; 0 < X ≤ 300. Dữ liệu thật tháng 10, 0 USD.

Phép so đi ĐƯỜNG KHÁC: X = đúng mốc ⇒ tiền kịch bản = tiền IN SẴN của mốc (`explain_kpi` → `tiers[].amount` = `printed_amount` trong file luật —
không qua `bonus_for_kpi`); X = mốc − 0,01 ⇒ chưa đạt mốc đó."""
import os
import tempfile
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
ADDITIVE = ["Turnover", "PC_BT", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON"]


@pytest.fixture(scope="module")
def ctx():
    from kpi_agent.backend.queries import build_context
    return build_context(date(2026, 10, 20), mask_path=str(NEEDED[2]))


def test_exact_tier_pct_pays_printed_amount_and_just_below_does_not(ctx):
    from kpi_agent.backend.queries import explain_kpi
    from kpi_agent.backend.what_if import simulate_kpi_change
    checked = 0
    for code in sorted(ctx.to_real):
        for kpi in ADDITIVE:
            tiers = explain_kpi(ctx, code, kpi)["tiers"]
            for i, tier in enumerate(tiers):
                at = simulate_kpi_change(ctx, code, kpi, target_pct=tier["min"])
                assert at["mode"] == "dat_phan_tram" and at["scenario"]["reached_tier"] == tier["label"], (code, kpi, tier)
                assert at["scenario"]["amount"] == tier["amount"] and at["amount_change"] == tier["amount"] - at["now"]["amount"]
                below = simulate_kpi_change(ctx, code, kpi, target_pct=tier["min"] - 0.01)
                assert below["scenario"]["reached_tier"] == (tiers[i - 1]["label"] if i else None), (code, kpi, tier)
                checked += 1
    assert checked >= 14 * 6   # mỗi KPI cộng được có ≥ 1 mốc


def test_ratio_kpi_refused_clearly_for_target_pct(ctx):
    from kpi_agent.backend.what_if import simulate_kpi_change
    for kpi in ("LPPC", "LPPC_Meat"):
        body = simulate_kpi_change(ctx, "NV01", kpi, target_pct=100)
        assert set(body) == {"refused", "params_used"} and "KPI tỷ lệ" in body["refused"] and "đạt X %" in body["refused"]


@pytest.mark.parametrize("args", [
    {"extra": 5, "target_pct": 100},           # cả hai
    {},                                        # không cái nào
    {"extra": "", "target_pct": ""},
    {"target_pct": 0}, {"target_pct": -1}, {"target_pct": 300.01}, {"target_pct": "abc"}, {"target_pct": True},
    {"target_pct": "nan"}, {"target_pct": "inf"}, {"target_pct": [95]},
])
def test_bad_target_pct_or_both_refused_without_numbers(ctx, args):
    from kpi_agent.backend.what_if import simulate_kpi_change
    body = simulate_kpi_change(ctx, "NV01", "PC_BT", **args)
    assert set(body) == {"refused", "params_used"} and "scenario" not in body


def test_cap_300_accepted_and_params_used_is_what_was_sent(ctx):
    from kpi_agent.backend.what_if import simulate_kpi_change
    body = simulate_kpi_change(ctx, " nv07 ", "pc_bt", target_pct="300")
    assert body["params_used"] == {"employee_id": "NV07", "kpi": "PC_BT", "target_pct": 300.0, "period": "2026-10"}
    assert body["scenario"]["pct"] == 300.0 and "extra" not in body and any("300" in a and "% chỉ tiêu" in a for a in body["assumptions"])
    assert len(body["assumptions"]) == 3


def test_http_route_and_agent_client_contract(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from kpi_agent.agent import backend_client, chat
    from kpi_agent.backend import main, queries
    # client chặn "cả hai / không cái nào" TRƯỚC khi gọi HTTP (không cần ngữ cảnh)
    for args in ({"employee_id": "NV01", "kpi": "PC_BT"}, {"employee_id": "NV01", "kpi": "PC_BT", "extra": 1, "target_pct": 95}):
        assert "đúng một trong hai" in backend_client.call_tool(None, "simulate_kpi_change", args)["refused"]
    schema = {t["function"]["name"]: t["function"] for t in chat.tool_schemas(queries.KPI_ORDER)}
    assert set(schema["simulate_kpi_change"]["parameters"]["properties"]) == backend_client.TOOL_PARAMS["simulate_kpi_change"]
    assert schema["simulate_kpi_change"]["parameters"]["required"] == ["employee_id", "kpi"]
    assert "nếu đạt X %" not in schema["explain_kpi"]["description"].replace("nếu đạt X % chỉ tiêu): dùng simulate_kpi_change", "")
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        ok = client.get("/api/what-if/NV01/PC_BT", params={"target_pct": "100"}).json()
        both = client.get("/api/what-if/NV01/PC_BT", params={"target_pct": "100", "extra": "1"}).json()
    assert ok["mode"] == "dat_phan_tram" and ok["scenario"]["pct"] == 100.0 and "refused" in both


def test_result_cites_same_cong_van_section_as_explain_kpi(ctx):
    # D_A2 câu 803 (10/11): kết quả thiếu mục công văn ⇒ mô hình dẫn sai "PC_ON mục 2.3". So với explain_kpi (đường khác), cả 2 kiểu hỏi
    from kpi_agent.backend.queries import explain_kpi
    from kpi_agent.backend.what_if import simulate_kpi_change
    for kpi in ADDITIVE:
        section = explain_kpi(ctx, "NV01", kpi)["cong_van_section"]
        assert simulate_kpi_change(ctx, "NV01", kpi, target_pct=100)["cong_van_section"] == section, kpi
        assert simulate_kpi_change(ctx, "NV01", kpi, extra=1)["cong_van_section"] == section, kpi
    assert simulate_kpi_change(ctx, "NV01", "PC_ON", target_pct=95)["cong_van_section"] == "2.7"
