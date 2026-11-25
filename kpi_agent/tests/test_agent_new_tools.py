"""Công cụ KPI G1: get_opportunities,
get_data_provenance (get_team_board có ở G1, BỎ ở G2-4 theo User). Tiêu chí T1 (số = endpoint backend) + một phần T2 (tham số lạ / kỳ khác ⇒ từ chối) — 0 USD, không gọi mô hình.

Đường gọi: `call_tool` của agent ⇒ `httpx.get` được CHUYỂN sang TestClient của backend thật (dữ liệu thật tháng 10, bảng lưu tạm) — chỉ bỏ cổng mạng,
code backend chạy thật; không giả lập kết quả nào."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kpi_agent.agent import backend_client, chat
from kpi_agent.agent.backend_client import call_tool, load_context

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
BASE = "http://127.0.0.1:8110"


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))
    from kpi_agent.backend.main import app
    with TestClient(app, base_url=BASE) as client:
        def route(url, params=None, timeout=None, headers=None):   # headers: agent gửi kèm mã phiên (12/11) — bộ test tắt mã nên bỏ qua
            assert url.startswith(BASE)
            return client.get(url[len(BASE):], params=params or {})
        monkeypatch.setattr(backend_client.httpx, "get", route)
        yield client


@pytest.fixture
def ctx(backend):
    return load_context(BASE)


def test_tool_list_is_exactly_ten_read_tools_board_removed():
    # get_team_board BỎ (04/11 giữa 11:31 và 11:40 — chồng vai với get_team_bonus_table); /api/team-board vẫn cho trang
    # + 3 công cụ nhóm A bậc mức tra cứu (09/11): A1 get_team_kpi_summary, A3 compare_employees, A6 get_data_issues
    # 11/11: + get_extra_bonus (bậc (b) — Thưởng thêm, chỉ đọc)
    # 13/11: + get_spice_loyalty (công cụ 12 — 1 % gia vị, tiền trả khách, chỉ đọc)
    assert [tool["function"]["name"] for tool in chat.tool_schemas(["PC_BT"])] == \
        ["get_team_bonus_table", "get_employee_result", "get_mid_month_gap", "explain_kpi",
         "get_opportunities", "get_data_provenance", "simulate_kpi_change",
         "get_team_kpi_summary", "compare_employees", "get_data_issues", "get_extra_bonus", "get_spice_loyalty",
         # 17/11: + 5 công cụ tính (backend/calc_tool.py — tham chiếu tới số của công cụ đọc, GET /api/calc/{name})
         "calc_sum", "calc_difference", "calc_percent_of", "calc_group_total", "calc_period_change"]
    assert "get_team_board" not in backend_client.TOOL_PARAMS
    assert set(backend_client.ALL_TOOL_PARAMS) == {tool["function"]["name"] for tool in chat.tool_schemas(["PC_BT"])}


def test_opportunities_only_true_pairs_with_count_note(backend, ctx):
    full, kept = backend.get("/api/opportunities").json(), call_tool(ctx, "get_opportunities", {})
    expected = [item for item in full["items"] if item["is_opportunity"] is True]
    assert kept["items"] == expected and len(expected) == full["opportunity_count"]
    assert f"đang hiện {len(expected)} / tổng {len(full['items'])}" in kept["shown"]
    ratio = sorted({item["kpi"] for item in full["items"] if item["is_opportunity"] is None})       # G1 minor 1: tên lấy từ dữ liệu
    assert f"({', '.join(ratio)})" in kept["shown"] and (ratio != ["LPPC_Meat"] or "LPPC," not in kept["shown"])
    assert {key: kept[key] for key in full if key != "items"} == {key: full[key] for key in full if key != "items"}   # nhãn "chưa dự báo" giữ


def test_provenance_matches_meta_and_run_and_never_carries_reviewer_name(backend, ctx):
    meta = backend.get("/api/meta").json()
    first = call_tool(ctx, "get_data_provenance", {})
    assert {key: first[key] for key in backend_client.META_FIELDS_FOR_MODEL} == {key: meta[key] for key in backend_client.META_FIELDS_FOR_MODEL}
    assert first["bonus_run"]["run_id"] == meta["current_run_id"] and first["bonus_run"]["status"] == "tam_tinh"
    assert first["bonus_run"]["team_total"] == 55_700_000 and first["workflow_version"].startswith("kpi_thuong_nvbh@")
    assert backend.post(f"/api/runs/{meta['current_run_id']}/review", json={"reviewer": "Giám Sát Tên Thật"}).status_code == 200
    after = call_tool(ctx, "get_data_provenance", {})
    assert after["bonus_run"]["status"] == "da_soat" and after["bonus_run"]["reviewed_at"]
    assert "Giám Sát Tên Thật" not in json.dumps(after, ensure_ascii=False)       # bản gửi mô hình không chép tên người soát


def test_new_tools_refuse_bad_params(ctx):
    assert "refused" in call_tool(ctx, "get_data_provenance", {"period": "2026-10"})       # công cụ không nhận tham số
    assert call_tool(ctx, "get_team_board", {}) == {"refused": "không có công cụ 'get_team_board'"}
    for name in ("get_opportunities",):
        for period in ("2026-11", "../2026-10"):
            body = call_tool(ctx, name, {"period": period})
            assert "refused" in body and "items" not in body and "employees" not in body, (name, period)


def test_backend_down_gives_error_not_numbers(monkeypatch):
    ctx = backend_client.AgentContext("http://127.0.0.1:1", "2026-10", None, ["NV01"], ["PC_BT"], [])
    for name in ("get_opportunities", "get_data_provenance"):
        assert set(call_tool(ctx, name, {})) == {"error"}


# ---------- công cụ 4: simulate_kpi_change qua route /api/what-if (G2) ----------
def test_simulate_through_client_equals_backend_function(backend, ctx):
    from kpi_agent.backend.main import app
    from kpi_agent.backend.what_if import simulate_kpi_change
    via_tool = call_tool(ctx, "simulate_kpi_change", {"employee_id": "NV07", "kpi": "PC_BT", "extra": 5})
    assert via_tool == simulate_kpi_change(app.state.ctx, "NV07", "PC_BT", 5)
    assert via_tool["scenario"]["actual"] == via_tool["now"]["actual"] + 5 and "GIẢ ĐỊNH" in via_tool["label"]


def test_simulate_bad_params_refused_at_client_and_backend(backend, ctx):
    assert "refused" in call_tool(ctx, "simulate_kpi_change", {"employee_id": "NV07", "kpi": "PC_BT"})            # thiếu extra
    assert "refused" in call_tool(ctx, "simulate_kpi_change", {"employee_id": "NV07", "kpi": "../PC_BT", "extra": 1})
    for url in ("/api/what-if/NV07/LPPC?extra=1", "/api/what-if/NV07/PC_BT?extra=abc", "/api/what-if/NV07/PC_BT?extra=-1",
                "/api/what-if/NV07/PC_BT", "/api/what-if/NV01%2F..%2FNV02/PC_BT?extra=1", "/api/what-if/NV07/PC_BT?extra=1&period=2026-11"):
        response = backend.get(url)
        assert response.status_code == 200 and set(response.json()) == {"refused", "params_used"}, url
