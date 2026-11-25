"""1 % gia vị (`backend/spice_loyalty.py`, luật `config/gia_vi_1pct_2026_10.yaml`) — dòng nhỏ tự dựng cho từng luật + ca xấu + 1 ca thật RAW 31/10."""
import copy
import json
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from kpi_agent.backend import spice_loyalty as sl

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "kpi_agent/config/gia_vi_1pct_2026_10.yaml"
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
TRACKING = ROOT / "sample_data/thang_10/Tracking.xlsx"
MASK = ROOT / "kpi_agent/data/ma_che_ten.json"
needs_data = pytest.mark.skipif(not all(p.exists() for p in (RAW, TRACKING, MASK)), reason="không có dữ liệu thật trên máy này")


def _rules():
    return copy.deepcopy(sl.load_rules(RULES))


def _line(outlet="K1", dms="Hạt nêm Chinsu", sales=100.0, free="N", row=2):
    return {"OUTLET CODE": outlet, "DMS OBJ": dms, "Sales After Discount": sales, "FREE ITEM FLAG": free, "_row": row}


def _all_groups():
    """Mỗi nhóm của luật 1 dòng 0 đ ở khách khác — để phép kiểm "nhóm có trong RAW" qua mà không đổi số của K1."""
    return [_line(outlet="K9", dms=name, sales=0.0) for name in _rules()["dms_obj"]]


BASE = [{"customer": "K1", "employee_name": "Người A", "baseline": 1_000_000.0}]
CODES = {"Người A": "SM1"}


@pytest.mark.parametrize("sales, reached, reward", [
    (1_099_999.0, False, 0),          # 109,9999 % nền ⇒ chưa đạt (so số thô, không làm tròn % lên 110)
    (1_100_000.0, True, 11_000),      # đúng 110 % ⇒ đạt, 1 %
    (1_100_050.0, True, 11_001),      # 11.000,5 ⇒ làm tròn gần nhất 11.001
    (150_000_000.0, True, 1_000_000), # 1 % = 1,5 tr ⇒ trần 1.000.000 đ / khách
])
def test_threshold_rounding_and_cap(sales, reached, reward):
    rows = sl.compute(_all_groups() + [_line(sales=sales)], _rules(), BASE, CODES)
    assert (rows[0]["reached"], rows[0]["reward"]) == (reached, reward)


def test_only_listed_groups_and_sold_items_count():
    lines = _all_groups() + [_line(sales=1_100_000.0), _line(dms="Nước mắm Nam Ngư", sales=5_000_000.0), _line(sales=9_000_000.0, free="Y")]
    rows = sl.compute(lines, _rules(), BASE, CODES)
    assert rows[0]["sales"] == 1_100_000.0 and rows[0]["reward"] == 11_000   # nhóm ngoài chữ trang + hàng tặng không tính


def test_zero_baseline_is_never_reached():
    rows = sl.compute(_all_groups() + [_line(sales=5_000_000.0)], _rules(), [{**BASE[0], "baseline": 0.0}], CODES)
    assert rows[0]["reached"] is False and rows[0]["reward"] == 0


def test_page_totals_potential_is_reward_at_threshold_for_unpaid_customers():
    # K1 đạt (1,1 tr / nền 1 tr); K2 chưa đạt (0,5 tr / nền 2 tr) ⇒ tiềm năng = 1 % × 110 % × 2 tr = 22.000 đ; K3 nền 0 không tính; K4 nền 200 tr ⇒ chạm trần 1 tr
    base = [BASE[0], {**BASE[0], "customer": "K2", "baseline": 2_000_000.0}, {**BASE[0], "customer": "K3", "baseline": 0.0},
            {**BASE[0], "customer": "K4", "baseline": 200_000_000.0}]
    rows = sl.compute(_all_groups() + [_line(sales=1_100_000.0), _line(outlet="K2", sales=500_000.0)], _rules(), base, CODES)
    totals = sl.page_totals(rows, _rules())
    assert (totals["potential_customers"], totals["potential_reward"]) == (2, 22_000 + 1_000_000)
    assert (totals["team_baseline"], totals["team_sales"], totals["team_pct"]) == (203_000_000, 1_600_000, 0.78)


def test_customer_rows_group_and_what_is_still_needed():
    # nền 1 tr: K1 1,1 tr ⇒ có thưởng; K2 1,05 tr ⇒ đủ nền chưa tới 110 %; K3 0,7 tr ⇒ 70 %–<100 %; K4 0,69 tr ⇒ dưới 70 %; K5 nền 0 ⇒ không xét
    base = [{**BASE[0], "customer": code, "baseline": 0.0 if code == "K5" else 1_000_000.0} for code in ("K1", "K2", "K3", "K4", "K5")]
    lines = _all_groups() + [_line(outlet="K1", sales=1_100_000.0), _line(outlet="K2", sales=1_050_000.0), _line(outlet="K3", sales=700_000.0),
                             _line(outlet="K4", sales=699_999.5), _line(outlet="K5", sales=10.0)]
    rows = sl.customer_rows(sl.compute(lines, _rules(), base, CODES), _rules(), {"K1": ("Tạp hoá K1", ["Thứ 2"])}, {"SM1": "NV01"})
    assert [row["group"] for row in rows] == ["co_thuong", "du_nen", "gan_nen", "duoi_70", "duoi_70"]
    assert (rows[0]["customer_name"], rows[0]["visit_days"], rows[0]["reward"], rows[0]["need_more"]) == ("Tạp hoá K1", ["Thứ 2"], 11_000, None)
    assert (rows[1]["need_more"], rows[1]["reward_at_threshold"], rows[1]["pct_of_baseline"]) == (50_000, 11_000, 105.0)
    assert (rows[3]["need_more"], rows[3]["pct_of_baseline"]) == (400_001, 69.99)          # còn thiếu 400.000,5 đ ⇒ làm tròn LÊN; % làm tròn XUỐNG
    assert (rows[4]["need_more"], rows[4]["reward_at_threshold"], rows[4]["pct_of_baseline"], rows[4]["customer_name"]) == (None, None, None, None)


def test_by_employee_hides_customer_codes():
    rows = sl.compute(_all_groups() + [_line(sales=1_100_000.0)], _rules(), BASE, CODES)
    out = sl.by_employee(rows, {"SM1": "NV01"})
    assert out["team_reward"] == 11_000 and out["employees"][0]["customers_reached"] == 1
    assert "K1" not in json.dumps(out)                                          # 0 mã khách ra ngoài


def _sheet(tmp_path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "1% gia vi"
    for _ in range(10):
        ws.append([None])
    ws.append(["Outlet Code", "Tên điểm bán ", "Tên nhân viên ", "Chỉ tiêu"])
    for row in rows:
        ws.append(row)
    wb.save(tmp_path / "t.xlsx")
    return {"file": "t.xlsx", "sheet": "1% gia vi", "header_row": 11, "columns": {"customer": "Outlet Code", "employee_name": "Tên nhân viên", "baseline": "Chỉ tiêu"}}


def test_baseline_sheet_stops_at_total_row(tmp_path):
    spec = _sheet(tmp_path, [[101, "x", "Người A", 500.0], [102, "y", "Người A", 300.0], ["Grand Total", None, None, None]])
    assert [row["customer"] for row in sl.read_baseline(tmp_path, spec)] == ["101", "102"]


@pytest.mark.parametrize("rows, match", [
    ([[101, "x", "Người A", None]], "nền tảng trống"),        # mã là số mà nền trống ⇒ dừng (không coi là dòng tổng)
    ([[101, "x", "Người A", 1.0], [101, "x", "Người A", 2.0]], "mã khách lặp lại"),
])
def test_bad_baseline_sheet_stops(tmp_path, rows, match):
    with pytest.raises(ValueError, match=match):
        sl.read_baseline(tmp_path, _sheet(tmp_path, rows))


def test_bad_inputs_stop():
    with pytest.raises(ValueError, match="không ghép được mã SM"):
        sl.compute(_all_groups(), _rules(), BASE, {})
    bad = _rules()
    bad["dms_obj"] = ["Hạt nêm Chinsuu"]
    with pytest.raises(ValueError, match="không có trong RAW"):
        sl.compute(_all_groups(), bad, BASE, CODES)


@needs_data
def test_api_real_3110(tmp_path, monkeypatch):
    # Ca thật: = trang theo dõi của đội (E so từng khách 11/11: thưởng 1.351 / 1.351 khách từng đồng, đạt 467, tổng 11.868.854)
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 31))
    monkeypatch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        body = client.get("/api/spice-loyalty").json()
        customers = client.get("/api/spice-customers").json()
        one = client.get("/api/spice-customers", params={"employee_id": "NV09"}).json()
        bad = client.get("/api/spice-customers", params={"employee_id": "NV99"}).json()
    assert body["team_customers"] == 1351 and body["team_customers_reached"] == 467 and body["team_reward"] == 11_868_854
    assert sum(row["reward"] for row in body["employees"]) == 11_868_854 and len(body["employees"]) == 14
    # 13/11: 4 ô tổng như trang của đội — số khách chưa có thưởng 884 = số trên trang; tổng doanh số = cộng 14 dòng nhân viên; không bật trang ⇒ page None
    totals = body["page_totals"]
    assert totals["potential_customers"] == 884 == 1351 - 467 and 0 < totals["potential_reward"] < 884 * 1_000_000
    assert abs(totals["team_sales"] - sum(row["sales"] for row in body["employees"])) <= 14 and totals["team_pct"] == 114.22 and totals["page"] is None
    # 13/11: từng khách cho trang — số ghi TRƯỚC khi code: 1.351 khách · 467 có thưởng · Σ thưởng 11.868.854 · 884 chưa có thưởng; mọi khách có tên + ngày viếng
    assert len(customers["rows"]) == customers["customers"] == 1351 and sum(row["reward"] for row in customers["rows"]) == 11_868_854
    assert sum(row["group"] == "co_thuong" for row in customers["rows"]) == 467 and sum(row["need_more"] is not None for row in customers["rows"]) == 884
    assert all(row["customer_name"] and row["visit_days"] for row in customers["rows"])
    assert {row["employee_id"] for row in one["rows"]} == {"NV09"} and "refused" in bad and "rows" not in bad
    real_codes = json.loads(MASK.read_text(encoding="utf-8"))["map"].values()
    assert not any(code in json.dumps(body, ensure_ascii=False) for code in real_codes)
    assert not any(code in json.dumps(customers, ensure_ascii=False) for code in real_codes)   # nhân viên chỉ bằng mã che (mã KHÁCH thì có — đường chỉ cho trang)
