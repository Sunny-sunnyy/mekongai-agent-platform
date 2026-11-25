"""Công cụ tính (backend/calc_tool.py, 17/11): mọi số vào là tham chiếu tới kết quả công cụ đọc — backend chạy lại nguồn, không nhận số gõ tay.

Phần 1: lõi với nguồn giả CỐ ĐỊNH (chỉ để thử phép tính + luật từ chối — số mong đợi tính tay ngay trong test).
Phần 2: backend thật (TestClient, dữ liệu thật tháng 10): số của công cụ tính = số công cụ đọc tự in (tổng đội), Rào kiem_so chạy lại lần gọi
công cụ tính ⇒ số có nguồn; số tự cộng không qua công cụ ⇒ trượt.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from kpi_agent.backend import calc_tool

# ---------- Phần 1: nguồn giả ----------
TEAM = {"period": "2026-10", "cutoff": "2026-10-31", "team_total": 300,
        "employees": [{"employee_id": "NV01", "business_salary": 100, "kpis_paid": 2, "kpi_amounts": {"Turnover": 60, "LPPC": 40},
                       "kpi_pct": {"Turnover": 80.5, "LPPC": 90.0}, "kpi_reached_tier": {"Turnover": "M1"}},
                      {"employee_id": "NV02", "business_salary": 150, "kpis_paid": 3, "kpi_amounts": {"Turnover": 100, "LPPC": 50},
                       "kpi_pct": {"Turnover": 90.0, "LPPC": 70.0}, "kpi_reached_tier": {"Turnover": "M2"}},
                      {"employee_id": "NV03", "business_salary": 50, "kpis_paid": 2, "kpi_amounts": {"Turnover": 50, "LPPC": None},
                       "kpi_pct": {"Turnover": 70.0, "LPPC": 60.0}, "kpi_reached_tier": {"Turnover": "M1"}}]}
EMP = {"period": "2026-10", "cutoff": "2026-10-31", "employee_id": "NV01",
       "kpis": [{"kpi": "Turnover", "target": 1000, "actual": 805}, {"kpi": "LPPC", "target": 4, "actual": 3.6}]}
ZERO = {"period": "2026-10", "team_total": 0}


ASKED = []


def source(tool, args, period):
    # period = kỳ khác kỳ của lượt (chỉ calc_period_change xin) — backend thật tìm bản MỚI NHẤT của kỳ đó
    ASKED.append(period)
    if period == "2026-09":
        return {**TEAM, "period": "2026-09", "cutoff": "2026-09-30", "team_total": 250, "ban_du_lieu": "data@0909abcd", "status": "da_soat"}
    if period == "2026-11":
        return {**TEAM, "period": "2026-11", "cutoff": "2026-11-17", "team_total": 120}
    if period == "2026-08":
        return {**ZERO, "period": "2026-08", "cutoff": "2026-08-31"}
    if period is not None:
        return {"refused": f"chưa có bản dữ liệu kỳ {period}"}
    return {"get_team_bonus_table": TEAM, "get_employee_result": EMP, "zero_tool": ZERO,
            "bad_tool": {"refused": "NV99 không có"}}.get(tool, {"refused": f"không có công cụ {tool}"})


def ref(field, tool="get_team_bonus_table", **extra):
    return {"tool": tool, "args": {}, "field": field, **extra}


def calc(name, **args):
    return calc_tool.run(name, args, source, "2026-10")


def test_sum_money_and_sources_listed():
    out = calc("calc_sum", values=[ref("employees[employee_id=NV01].business_salary"), ref("employees[employee_id=NV02].kpi_amounts.Turnover")])
    assert out["total"] == 200 and out["unit"] == "đồng" and out["item_count"] == 2 and out["employee_ids"] == ["NV01", "NV02"]
    assert [item["value"] for item in out["sources"]] == [100, 100] and out["sources"][0]["field"].endswith("business_salary")
    assert [item["employee_id"] for item in out["sources"]] == ["NV01", "NV02"]


@pytest.mark.parametrize("args, words", [
    ({"values": [5040000, 100]}, "không nhận số gõ tay"),                                        # số AI tự gõ
    ({"values": [{"tool": "get_team_bonus_table", "field": "team_total", "value": 5040000}, ref("team_total")]}, "không nhận số gõ tay"),
    ({"values": [ref("team_total"), ref("team_total")]}, "trùng"),
    ({"values": [ref("team_total"), ref("employees[employee_id=NV01].kpi_pct.Turnover")]}, "khác đơn vị"),
    ({"values": [ref("employees[employee_id=NV01].kpi_pct.Turnover"), ref("employees[employee_id=NV02].kpi_pct.Turnover")]}, "không cộng %"),
    ({"values": [ref("actual", "get_employee_result") | {"field": "kpis[kpi=LPPC].actual"}, ref("employees[employee_id=NV01].kpis_paid")]}, "khác đơn vị"),
    ({"values": [ref("employees[employee_id=NV03].kpi_amounts.LPPC"), ref("team_total")]}, "trống"),             # thiếu ≠ 0
    ({"values": [ref("employees[employee_id=NV99].business_salary"), ref("team_total")]}, "không có dòng"),
    ({"values": [ref("period"), ref("team_total")]}, "không phải ô số"),                                    # trường chữ
    ({"values": [ref("team_total", "bad_tool"), ref("team_total")]}, "không cho số"),                            # nguồn từ chối
    ({"values": [ref("tong", "calc_sum"), ref("team_total")]}, "công cụ tính"),                                  # lồng vòng
    ({"values": [ref("team_total") | {"data_version": "abc"}, ref("employees[employee_id=NV01].business_salary")]}, "không nhận số gõ tay"),  # không có ô data_version
    ({"values": [ref("team_total") | {"args": {"period": "2026-09"}}, ref("employees[employee_id=NV01].business_salary")]}, "kỳ 2026-09"),  # 1–4 không đổi kỳ
    # cửa lách rào (A nhắc 17/11): số giả định dựng từ số mô hình gõ, hoặc tham số đã gửi ⇒ không là nguồn
    ({"values": [ref("scenario.actual", "simulate_kpi_change"), ref("team_total")]}, "không làm nguồn"),
    ({"values": [ref("params_used.extra", "get_team_bonus_table"), ref("team_total")]}, "params_used"),
    ({"values": [ref("team_total")]}, "2–50"),
    ({"values": [ref(f"employees[employee_id=NV0{i % 3 + 1}].business_salary") for i in range(51)]}, "2–50"),
])
def test_sum_refusals(args, words):
    out = calc_tool.run("calc_sum", args, source, "2026-10")
    assert set(out) == {"refused"} and words in out["refused"], out


def test_period_in_args_is_dropped_but_must_match():
    ok = calc("calc_sum", values=[ref("employees[employee_id=NV01].business_salary") | {"args": {"period": "2026-10"}}, ref("team_total")])
    assert ok["total"] == 400 and ok["sources"][0]["args"] == {}
    other = calc("calc_sum", values=[ref("team_total") | {"args": {"period": "2026-09"}}, ref("employees[employee_id=NV01].business_salary")])
    assert "kỳ 2026-09" in other["refused"]


def test_ratio_kpi_measure_is_not_added():
    out = calc("calc_sum", values=[{"tool": "get_employee_result", "args": {"employee_id": "NV01"}, "field": "kpis[kpi=LPPC].actual"},
                               {"tool": "get_employee_result", "args": {"employee_id": "NV02"}, "field": "kpis[kpi=LPPC].actual"}])
    assert "KPI tỉ lệ" in out["refused"]


def test_turnover_actual_is_money():
    value, unit, person = calc_tool.value_at(EMP, "kpis[kpi=Turnover].actual")
    assert person == "NV01"
    assert (value, unit) == (Decimal(805), "đồng")


def test_difference_money_and_percentage_points():
    money = calc("calc_difference", a=ref("employees[employee_id=NV01].business_salary"), b=ref("employees[employee_id=NV02].business_salary"))
    assert (money["a_minus_b"], money["gap"], money["higher"], money["unit"]) == (-50, 50, "NV02", "đồng")
    points = calc("calc_difference", a=ref("employees[employee_id=NV02].kpi_pct.Turnover"), b=ref("employees[employee_id=NV01].kpi_pct.Turnover"))
    assert points["a_minus_b"] == 9.5 and points["unit"] == "điểm %"


def test_percent_of_rounds_half_up_and_refuses_zero():
    out = calc("calc_percent_of", part=ref("employees[employee_id=NV01].business_salary"), whole=ref("team_total"))
    assert out["percent"] == 33.33 and out["percent_full"] == "33.333333" and out["part_employee_id"] == "NV01"      # 100 / 300
    half = calc_tool._percent(Decimal(1), Decimal(8))                                    # 12,5 ⇒ nửa lên ở số lẻ thứ 2: 12.50
    assert half["percent"] == 12.5
    assert calc_tool._percent(Decimal(1), Decimal(1600))["percent"] == 0.06            # 0,0625 ⇒ 0,06 (không phải 0,07: nửa lên chỉ ở đúng nửa)
    assert calc_tool._percent(Decimal(5), Decimal(4000))["percent"] == 0.13            # 0,125 ⇒ 0,13 (nửa lên; nửa chẵn sẽ ra 0,12)
    zero = calc("calc_percent_of", part=ref("employees[employee_id=NV01].business_salary"), whole=ref("team_total", "zero_tool"))
    assert "mẫu số" in zero["refused"]
    pct = calc("calc_percent_of", part=ref("employees[employee_id=NV01].kpi_pct.Turnover"), whole=ref("employees[employee_id=NV02].kpi_pct.Turnover"))
    assert "% của %" in pct["refused"]


def test_group_total_by_field_and_by_named_groups():
    by_tier = calc("calc_group_total", table=ref("employees"), value_field="business_salary", group_by="kpi_reached_tier.Turnover")
    assert [(g["group"], g["total"], g["employee_ids"]) for g in by_tier["groups"]] == [("M1", 150, ["NV01", "NV03"]), ("M2", 150, ["NV02"])]
    assert by_tier["table_total"] == 300 and by_tier["unit"] == "đồng"
    named = calc("calc_group_total", table=ref("employees"), value_field="kpi_amounts.LPPC", groups={"Nhóm A": ["NV01", "nv03"]})
    assert named["groups"][0] == {"group": "Nhóm A", "total": 40, "row_count": 2, "employee_ids": ["NV01", "NV03"], "missing_value_ids": ["NV03"]}
    assert named["groups"][1]["group"] == "(ngoài các nhóm)" and named["groups"][1]["total"] == 50 and named["rows_missing_value"] == ["NV03"]


@pytest.mark.parametrize("args, words", [
    ({"table": ref("employees"), "value_field": "business_salary", "groups": {"A": ["NV01"], "B": ["NV01"]}}, "hai nhóm"),
    ({"table": ref("employees"), "value_field": "business_salary", "groups": {"A": ["NV09"]}}, "không có trong bảng"),
    ({"table": ref("employees"), "value_field": "business_salary"}, "ĐÚNG MỘT"),
    ({"table": ref("employees"), "value_field": "business_salary", "group_by": "kpis_paid", "groups": {"A": ["NV01"]}}, "ĐÚNG MỘT"),
    ({"table": ref("team_total"), "value_field": "business_salary", "group_by": "kpis_paid"}, "không phải một bảng"),
    ({"table": ref("employees"), "value_field": "kpi_pct.Turnover", "group_by": "kpis_paid"}, "không cộng %"),
    ({"table": ref("employees"), "value_field": "business_salary", "group_by": "kpi_amounts"}, "không phải ô nhãn"),
])
def test_group_total_refusals(args, words):
    out = calc_tool.run("calc_group_total", args, source, "2026-10")
    assert set(out) == {"refused"} and words in out["refused"], out


def test_period_change_frame():
    pref = lambda field, period: ref(field) | {"args": {"period": period}}
    done = calc("calc_period_change", before=pref("team_total", "2026-09"), after=pref("team_total", "2026-10"))
    assert (done["change"], done["percent_change"], done["warnings"]) == (50, 20.0, [])     # 250 ⇒ 300
    assert [item["period"] for item in done["sources"]] == ["2026-09", "2026-10"]
    # kỳ so sánh: ghi bản + trạng thái của bản đó; kỳ hiện tại: xin bản của LƯỢT (None), không xin "mới nhất"
    assert {k: done["sources"][0][k] for k in ("ban_du_lieu", "status")} == {"ban_du_lieu": "data@0909abcd", "status": "da_soat"}
    ASKED.clear()
    calc("calc_period_change", before=pref("team_total", "2026-09"), after=pref("team_total", "2026-10"))
    assert ASKED == ["2026-09", None]
    running = calc("calc_period_change", before=pref("team_total", "2026-10"), after=pref("team_total", "2026-11"))
    assert running["after"]["period_closed"] is False and "chưa hết" in running["warnings"][0] and running["change"] == -180
    from_zero = calc("calc_period_change", before=pref("team_total", "2026-08"), after=pref("team_total", "2026-10"))
    assert from_zero["percent_change"] is None and from_zero["change"] == 300
    assert "cùng một kỳ" in calc("calc_period_change", before=pref("team_total", "2026-10"),
                                  after=pref("employees[employee_id=NV01].business_salary", "2026-10"))["refused"]
    assert "args.period" in calc("calc_period_change", before=ref("team_total"), after=pref("team_total", "2026-10"))["refused"]
    assert "không cho số" in calc("calc_period_change", before=pref("team_total", "2025-01"), after=pref("team_total", "2026-10"))["refused"]


# ---------- Phần 2: backend thật ----------
ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
SYS_KEY = "khoa-thu-trong-test-0123456789"   # khoá giả chỉ sống trong test


@pytest.fixture
def client(tmp_path, monkeypatch):
    if not all(p.exists() for p in NEEDED):
        pytest.skip("không có dữ liệu thật trên máy này")
    from fastapi.testclient import TestClient
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    from kpi_agent.backend.main import app
    with TestClient(app, base_url="http://127.0.0.1:8110") as test_client:
        yield test_client


def tool(client, name, args, headers=None):
    return client.post(f"/api/tool/{name}", json={"args": args}, headers=headers or {}).json()


def test_real_team_sum_equals_backend_total_and_kiem_so_accepts(client):
    team = tool(client, "get_team_bonus_table", {})
    ids = [row["employee_id"] for row in team["employees"]]
    args = {"values": [ref(f"employees[employee_id={nv}].business_salary") for nv in ids]}
    out = tool(client, "calc_sum", args)
    assert out["total"] == team["team_total"] == sum(row["business_salary"] for row in team["employees"]) and out["item_count"] == len(ids)
    # đường chat (GET /api/calc) ra cùng kết quả với cửa Capitol
    import json
    assert client.get("/api/calc/calc_sum", params={"args": json.dumps(args)}).json() == out
    # % một người trong tổng đội = phép chia tay bằng Decimal trên chính bảng đội
    share = tool(client, "calc_percent_of", {"part": ref(f"employees[employee_id={ids[0]}].business_salary"), "whole": ref("team_total")})
    expected = (Decimal(team["employees"][0]["business_salary"]) * 100 / Decimal(team["team_total"])).quantize(Decimal("0.01"), "ROUND_HALF_UP")
    assert share["percent"] == float(expected)
    # Rào kiem_so chạy lại lần gọi công cụ tính ⇒ tổng có nguồn; tổng tự cộng sai một đồng ⇒ trượt
    total = f"{out['total']:,}".replace(",", ".")
    calls = [{"name": "calc_sum", "args": args}]
    good = client.post("/api/system/kiem-so", json={"answer": f"Tổng lương kinh doanh cả đội là {total} đ.", "tool_calls": calls},
                       headers={"X-Kpi-Sys-Key": SYS_KEY}).json()
    assert good["dat"] is True, good
    wrong = f"{out['total'] + 1:,}".replace(",", ".")
    bad = client.post("/api/system/kiem-so", json={"answer": f"Tổng lương kinh doanh cả đội là {wrong} đ.", "tool_calls": calls},
                      headers={"X-Kpi-Sys-Key": SYS_KEY}).json()
    assert bad["dat"] is False


def test_real_refusals_and_period_change_single_version(client):
    assert "không nhận số gõ tay" in tool(client, "calc_sum", {"values": [152360000, 1]})["refused"]
    one = tool(client, "calc_period_change", {"before": ref("team_total") | {"args": {"period": "2026-09"}},
                                              "after": ref("team_total") | {"args": {"period": "2026-10"}}})
    assert "chưa có bản dữ liệu kỳ" in one["refused"] and "2026-09" in one["refused"]       # backend chỉ có một kỳ ⇒ từ chối có lời
    assert "tham số không hợp lệ" in tool(client, "calc_sum", {"values": [], "period": "2026-10"}).get("refused", "") or \
        "period" in tool(client, "calc_sum", {"values": [], "period": "2026-10"})["refused"]


def test_data_version_mark_is_not_a_number_source():
    # "ban_du_lieu" ghi dạng data@<8 hex> — mẫu mã bản luật number_check bỏ qua; hex trần mở đầu bằng chữ số (vd "7220abcd…") sẽ thành "số có nguồn" giả cho Rào
    from kpi_agent.agent.number_check import source_numbers
    assert source_numbers([{"ban_du_lieu": "data@fb0501e7"}]) == set()
    assert Decimal(7220) in source_numbers([{"x": "7220abcd"}])     # đối chứng: hex trần mở đầu bằng chữ số ⇒ lộ số
