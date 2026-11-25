"""Hình "ô đơn + bảng" của kết quả công cụ cho khung trợ lý (`agent/result_view.py`): ca nhỏ tự dựng cho từng luật sắp xếp + trần,
và ca thật trên kết quả của cả 12 công cụ (backend chạy trong tiến trình, RAW 31/10 — không gọi mô hình)."""
import json
from datetime import date
from pathlib import Path

import pytest

from kpi_agent.agent import server
from kpi_agent.agent.result_view import MAX_COLUMNS, MAX_ROWS, MAX_TEXT, result_view

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json", ROOT / "kpi_agent/data/ma_che_ten.json",
          ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"]
needs_data = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")


def test_scalars_become_facts_and_envelope_keys_are_left_out():
    view = result_view({"period": "2026-10", "params_used": {"kpi": "PC_BT"}, "data_flags": ["x"], "status_label": "tạm tính",
                        "team_total": 126_660_000, "kpi": "PC_BT", "reviewed_at": None})
    assert view["facts"] == [{"key": "team_total", "value": 126_660_000}, {"key": "kpi", "value": "PC_BT"}, {"key": "reviewed_at", "value": None}]
    assert view["tables"] == [] and view["notes"] == [] and view["nested_left"] == []
    assert (view["total_facts"], view["total_tables"], view["total_notes"]) == (3, 0, 0)


def test_list_of_objects_becomes_a_table_with_flattened_child_objects():
    view = result_view({"employees": [{"employee_id": "NV01", "kpi_amounts": {"Turnover": 5_600_000, "PC_BT": 0}, "business_salary": 5_600_000},
                                      {"employee_id": "NV02", "kpi_amounts": {"Turnover": 0, "PC_BT": 2_200_000}, "business_salary": 2_200_000}]})
    table = view["tables"][0]
    assert table["key"] == "employees" and table["columns"] == ["employee_id", "business_salary", "kpi_amounts.Turnover", "kpi_amounts.PC_BT"]
    assert table["rows"] == [["NV01", 5_600_000, 5_600_000, 0], ["NV02", 2_200_000, 0, 2_200_000]] and table["total_rows"] == 2


def test_one_level_of_child_rows_is_expanded_with_parent_columns():
    view = result_view({"employees": [{"employee_id": "NV07", "gaps": [{"kpi": "PC_BT", "gap_to_next": 12}, {"kpi": "LPPC", "gap_to_next": 0.3}]}]})
    table = view["tables"][0]
    assert table["columns"] == ["employee_id", "kpi", "gap_to_next"] and table["rows"] == [["NV07", "PC_BT", 12], ["NV07", "LPPC", 0.3]]
    assert view["nested_left"] == []


def test_deeper_nesting_is_not_expanded_but_reported():
    view = result_view({"sides": [{"employee_id": "NV01", "kpis": [{"kpi": "LPPC", "tiers": [{"label": "4,5", "amount": 1}]}]}],
                        "bonus_run": {"run_id": 3, "data_snapshot": {"cutoff": "2026-10-31", "files_sha256": {"a.xlsx": "0f"}}}})
    assert view["tables"][0]["columns"] == ["employee_id", "kpi"]                       # `tiers` (cấp 2) không thành cột / dòng
    assert view["tables"][1] == {"key": "bonus_run", "columns": ["run_id", "data_snapshot.cutoff"], "hidden_columns": 0, "total_rows": 1,
                                 "rows": [[3, "2026-10-31"]]}                           # đối tượng con MỘT tầng thành cột; tầng dưới nữa thì không
    assert view["nested_left"] == ["data_snapshot", "tiers"]


def test_short_code_lists_are_one_fact_and_long_sentences_are_notes():
    view = result_view({"paid_employee_ids": ["NV01", "NV05"], "team_sentences": ["Cả đội 14 người, 2 người có tiền PC_BT (NV01, NV05)."], "items": []})
    assert view["facts"] == [{"key": "paid_employee_ids", "value": "NV01, NV05"}]
    assert view["notes"] == ["Cả đội 14 người, 2 người có tiền PC_BT (NV01, NV05)."] and view["tables"] == []


def test_caps_keep_counts_so_the_page_can_say_what_is_hidden():
    wide = {f"c{i}": i for i in range(MAX_COLUMNS + 5)}
    view = result_view({"lines": [dict(wide, note="x" * (MAX_TEXT + 50)) for _ in range(MAX_ROWS + 76)]})
    table = view["tables"][0]
    assert len(table["rows"]) == MAX_ROWS and table["total_rows"] == MAX_ROWS + 76
    assert len(table["columns"]) == MAX_COLUMNS and table["hidden_columns"] == 6
    long_text = result_view({"lines": [{"how": "x" * (MAX_TEXT + 50)}]})["tables"][0]["rows"][0][0]
    assert len(long_text) == MAX_TEXT + 1 and long_text.endswith("…")
    # ô đơn / bảng / ghi chú vượt trần: phần hiện bị cắt nhưng tổng số vẫn được báo
    many = result_view({**{f"so_{i}": i for i in range(20)}, **{f"bang_{i}": [{"a": i}] for i in range(6)}, "cau": [f"Câu ghi chú dài hơn hai mươi ký tự số {i}." for i in range(13)]})
    assert (len(many["facts"]), many["total_facts"], len(many["tables"]), many["total_tables"], len(many["notes"]), many["total_notes"]) == (16, 20, 4, 6, 10, 13)


def test_money_columns_of_child_objects_come_before_other_child_columns():
    kpis = ["Turnover", "PC_BT", "LPPC", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat"]
    row = {"employee_id": "NV01", "kpi_pct": {k: 99.5 for k in kpis}, "kpi_reached_tier": {k: None for k in kpis}, "kpi_amounts": {k: 1 for k in kpis},
           "kpis_paid": 8, "business_salary": 8}
    table = result_view({"employees": [row]})["tables"][0]
    assert table["columns"][:3] == ["employee_id", "kpis_paid", "business_salary"] and table["columns"][3:11] == [f"kpi_amounts.{k}" for k in kpis]
    assert table["hidden_columns"] == 27 - 12                          # 3 + 24 cột, hiện 12: đủ 8 cột tiền, 1 cột % đầu
    # bật tên (KPI_NAMES=on): mỗi dòng thêm name + sm_code — 8 cột tiền vẫn phải nằm trong 12 cột hiện, tên đứng sau nhóm tiền
    named = result_view({"employees": [{**row, "name": "Chu Thị Nguyên", "sm_code": "SM000123"}]})["tables"][0]
    assert named["columns"][:11] == ["employee_id", "kpis_paid", "business_salary", *[f"kpi_amounts.{k}" for k in kpis]] and named["columns"][11] == "name"


@pytest.mark.parametrize("value", [None, "chuỗi", 5, ["a"], {"refused": "không có NV99 trong đội"}])
def test_non_results_give_an_empty_view(value):
    assert result_view(value) == {"facts": [], "tables": [], "notes": [], "nested_left": [], "total_facts": 0, "total_tables": 0, "total_notes": 0}


def test_trace_carries_the_view_and_refused_calls_have_none():
    record = {"tool_calls": [{"name": "get_employee_result", "args": {"employee_id": "NV05"}, "refused": False},
                             {"name": "get_employee_result", "args": {"employee_id": "NV99"}, "refused": True}],
              "tool_results": [{"employee_id": "NV05", "business_salary": 1_000_000, "kpis": [{"kpi": "PC_BT", "amount": 0}], "params_used": {"employee_id": "NV05"}},
                               {"refused": "không có NV99 trong đội", "params_used": {"employee_id": "NV99"}}],
              "usd": 0.0, "seconds": 0.1, "stop": "answered"}
    first, second = server._trace(record)["tool_calls"]
    assert first["view"]["facts"] == [{"key": "employee_id", "value": "NV05"}, {"key": "business_salary", "value": 1_000_000}]
    assert first["view"]["tables"][0]["rows"] == [["PC_BT", 0]]
    assert second["view"] is None and second["refused"] == "không có NV99 trong đội"


CALLS = [("get_team_bonus_table", {}, "employees", 14), ("get_employee_result", {"employee_id": "NV05"}, "kpis", 8),
         ("get_mid_month_gap", {"employee_id": "NV07"}, "employees", None), ("explain_kpi", {"employee_id": "NV01", "kpi": "Turnover"}, "tiers", None),
         ("get_opportunities", {}, None, None), ("get_data_provenance", {}, "bonus_run", 1),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "PC_BT", "extra": 5}, "now", 1), ("get_team_kpi_summary", {"kpi": "ASO_ALL"}, "by_tier", None),
         ("compare_employees", {"employee_a": "NV01", "employee_b": "NV02"}, "sides", 16), ("get_data_issues", {}, "issues", None),
         ("get_extra_bonus", {}, "lines", 126), ("get_spice_loyalty", {}, "by_salesperson_in_charge", 14)]


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    patch = pytest.MonkeyPatch()
    patch.setattr(main, "CUTOFF", date(2026, 10, 31))
    patch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    patch.setenv("KPI_STORE", str(tmp_path_factory.mktemp("view") / "store.sqlite"))
    patch.delenv("KPI_SYSTEM_ONLY", raising=False)
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as started:
        yield started
    patch.undo()


@needs_data
@pytest.mark.parametrize("name, args, table_key, total_rows", CALLS, ids=[call[0] for call in CALLS])
def test_real_tool_results_fit_the_view(backend, name, args, table_key, total_rows):
    result = backend.post(f"/api/tool/{name}", json={"args": args}).json()
    assert "refused" not in result
    view = result_view(result)
    json.dumps(view)                                                    # gửi được về trang
    assert view["facts"] or view["tables"]
    for table in view["tables"]:
        assert len(table["rows"]) <= MAX_ROWS and len(table["columns"]) <= MAX_COLUMNS
        assert all(len(row) == len(table["columns"]) for row in table["rows"])
        assert all(cell is None or isinstance(cell, (str, int, float, bool)) for row in table["rows"] for cell in row)   # ô nào cũng vẽ được thành chữ
    if table_key:
        table = next(item for item in view["tables"] if item["key"] == table_key)
        assert total_rows is None or table["total_rows"] == total_rows


@needs_data
def test_real_numbers_in_the_view_are_exactly_the_tool_numbers(backend):
    # Bảng trên trang phải mang ĐÚNG số công cụ trả (không làm tròn, không tính lại): so tổng đội + từng dòng nhân viên
    result = backend.post("/api/tool/get_team_bonus_table", json={"args": {}}).json()
    view = result_view(result)
    assert {"key": "team_total", "value": 152_360_000} in view["facts"]
    table = next(item for item in view["tables"] if item["key"] == "employees")
    assert sum(column.startswith("kpi_amounts.") for column in table["columns"]) == 8      # đủ 8 cột tiền KPI nằm trong phần hiện
    salary_at = table["columns"].index("business_salary")
    assert [row[salary_at] for row in table["rows"]] == [employee["business_salary"] for employee in result["employees"]]
    assert sum(row[salary_at] for row in table["rows"]) == 152_360_000
