"""Tab "Hội viên" phần doanh số (`backend/membership.py`, đường /api/membership): dòng nhỏ tự dựng cho từng nhóm + ca xấu + 1 ca thật RAW 31/10.

Giữ sàn: người dùng so màn này với trang theo dõi của đội — số ghi TRƯỚC khi có code (đọc từ trang 13/11): 417 khách · 9 chưa phát sinh · 123 đang bán ·
100 gần đạt · 185 đủ doanh số (= 163 "đã đạt" + 22 "đủ doanh số chưa đạt" của trang) · doanh số 7.680.778.933 / nền 9.387.745.964."""
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from kpi_agent.backend import membership

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "kpi_agent/config/hoi_vien_2026_10.yaml"
MASK = ROOT / "kpi_agent/data/ma_che_ten.json"
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", MASK,
          ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
needs_data = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SPEC = {"sheet": "Hoi Vien", "header_row": 2, "columns": {"customer": "Outlet Code", "name": "Tên điểm bán", "employee_name": "Tên nhân viên",
                                                           "baseline": "Chỉ tiêu", "visit": "Viếng thăm"}}
HEADER = ["Outlet Code", "Tên điểm bán", "Tên nhân viên", "Chỉ tiêu", "Chỉ tiêu", "Viếng thăm"]   # sổ thật có HAI cột "Chỉ tiêu" — cột đầu là nền


def _book(tmp_path, rows, header=HEADER, sheet="Hoi Vien"):
    book = Workbook()
    book.active.title = sheet
    book.active.append(["dòng tiêu đề phụ"])
    book.active.append(header)
    for row in rows:
        book.active.append(row)
    path = tmp_path / "so.xlsx"
    book.save(path)
    return path


def _member(code, baseline, employee="Người A"):
    return {"customer": code, "name": f"Khách {code}", "employee_name": employee, "baseline": float(baseline), "visit_days": ["Thứ 2"]}


def test_groups_by_sales_against_baseline():
    members = [_member("K1", 1_000_000), _member("K2", 1_000_000), _member("K3", 1_000_000), _member("K4", 1_000_000), _member("K5", 1_000_000),
               _member("K6", 1_000_000, employee="Người lạ")]
    sales = {"K1": Decimal("1000000"), "K2": Decimal("999999.4"), "K3": Decimal("600000"), "K4": Decimal("599999")}   # K5 không có đơn
    rows, skipped = membership.compute(members, sales, {"Người A": "SM1"}, {"SM1": "NV01"})
    assert skipped == 1 and [row["group"] for row in rows] == ["du_doanh_so", "gan_dat", "gan_dat", "dang_ban", "chua_phat_sinh"]
    assert (rows[1]["pct_of_baseline"], rows[1]["remaining"]) == (99.99, 1)        # 999.999,4 / 1.000.000: % làm tròn XUỐNG, chưa đủ ⇒ còn thiếu ≥ 1 đ
    assert (rows[0]["pct_of_baseline"], rows[0]["remaining"]) == (100.0, 0)
    out = membership.view(rows, ["NV01", "NV02"])
    assert out["summary"] == {"customers": 5, "du_doanh_so": 1, "gan_dat": 2, "dang_ban": 1, "chua_phat_sinh": 1, "sales": 3_199_998, "baseline": 5_000_000,
                              "pct_of_baseline": 63.99}
    assert out["by_employee"][1] == {"employee_id": "NV02", "customers": 0, "du_doanh_so": 0, "gan_dat": 0, "dang_ban": 0, "chua_phat_sinh": 0, "sales": 0,
                                     "baseline": 0, "pct_of_baseline": None}
    assert membership.view(rows, ["NV01"], "NV02")["rows"] == [] and "chưa" in out["not_built"].lower()


def test_read_members_takes_first_baseline_column_and_visit_days(tmp_path):
    path = _book(tmp_path, [["K1", "Tạp hoá K1", "Người A", 2_000_000, 999, "Thứ 2,Thứ 5,"], [None, None, None, None, None, None], ["K2", None, "Người A", 0, 1, None],
                            ["(blank)", None, None, 2_000_000, 1000, None]])        # dòng TỔNG cuối bảng của sổ — không phải khách
    assert membership.read_members(path, SPEC) == [
        {"customer": "K1", "name": "Tạp hoá K1", "employee_name": "Người A", "baseline": 2_000_000.0, "visit_days": ["Thứ 2", "Thứ 5"]},
        {"customer": "K2", "name": None, "employee_name": "Người A", "baseline": 0.0, "visit_days": []}]


@pytest.mark.parametrize("rows, header, sheet, match", [
    ([["K1", "x", "Người A", 1, 1, ""]], HEADER, "Sheet khác", "không có sheet"),
    ([["K1", "x", "Người A", 1, 1, ""]], ["Outlet Code", "Tên điểm bán", "Tên nhân viên", "Viếng thăm"], "Hoi Vien", "thiếu cột: Chỉ tiêu"),
    ([["K1", "x", "Người A", "một triệu", 1, ""]], HEADER, "Hoi Vien", "nền không phải số"),
    ([["K1", "x", "Người A", 1, 1, ""], ["K1", "y", "Người A", 1, 1, ""]], HEADER, "Hoi Vien", "mã khách lặp lại"),
    ([], HEADER, "Hoi Vien", "không có khách nào"),
])
def test_bad_member_sheet_stops_without_customer_text(tmp_path, rows, header, sheet, match):
    with pytest.raises(ValueError, match=match) as error:
        membership.read_members(_book(tmp_path, rows, header, sheet), SPEC)
    assert "K1" not in str(error.value) and "Người A" not in str(error.value)


def test_sales_include_late_invoices_but_not_orders_after_cutoff(tmp_path):
    from kpi_agent.core.load_raw import SHEET
    book = Workbook()
    book.active.title = SHEET
    book.active.append(["SM CODE", "OUTLET CODE", "Sales After Discount", "ORDER DATE", "ORDER STATUS", "INVOICED DATE"])
    book.active.append(["SM1", "K1", 100.5, datetime(2026, 10, 30), "Invoiced", datetime(2026, 11, 3, 8, 0)])    # hoá đơn trễ ⇒ VẪN tính
    book.active.append(["SM2", "K1", 50, datetime(2026, 10, 1), "Shipped", None])                              # nhân viên khác viết đơn ⇒ vẫn là doanh số của khách
    book.active.append(["SM1", "K1", 999, datetime(2026, 11, 1), "Invoiced", None])                            # sau ngày chốt ⇒ không tính
    book.active.append(["SM1", "K2", 70, datetime(2026, 10, 2), "Cancelled", None])                            # trạng thái không được tính
    book.active.append([None, None, None, None, None, None])
    path = tmp_path / "raw.xlsx"
    book.save(path)
    assert dict(membership.sales_after_discount(path, date(2026, 10, 31), counted_status={"Invoiced", "Shipped"})) == {"K1": Decimal("150.5")}
    book.active.append(["SM1", "K3", "chữ", datetime(2026, 10, 2), "Invoiced", None])
    book.save(path)
    with pytest.raises(ValueError, match="dòng Excel 7"):
        membership.sales_after_discount(path, date(2026, 10, 31), counted_status={"Invoiced", "Shipped"})


def test_source_file_lists_every_needed_key(tmp_path):
    assert membership.load_source(SOURCE)["sheet"] == "Hoi Vien Masan"
    bad = tmp_path / "nguon.yaml"
    bad.write_text("period: '2026-10'\nworkbook: so.xlsx\n", encoding="utf-8")
    with pytest.raises(ValueError, match="thiếu mục: sheet, header_row, columns"):
        membership.load_source(bad)


@needs_data
def test_api_real_3110_matches_numbers_written_before_code(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 31))
    monkeypatch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))
    monkeypatch.delenv("KPI_SYSTEM_ONLY", raising=False)
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        body = client.get("/api/membership").json()
        one = client.get("/api/membership", params={"employee_id": "NV09"}).json()
        bad = client.get("/api/membership", params={"employee_id": "NV99"}).json()
        team_total = client.get("/api/team-bonus").json()["team_total"]
    assert body["summary"] == {"customers": 417, "du_doanh_so": 185, "gan_dat": 100, "dang_ban": 123, "chua_phat_sinh": 9, "sales": 7_680_778_933,
                               "baseline": 9_387_745_964, "pct_of_baseline": 81.81}
    assert sum(row["customers"] for row in body["by_employee"]) == 417 and len(body["by_employee"]) == 14 and len(body["rows"]) == 417
    assert one["summary"]["customers"] == len(one["rows"]) == next(row for row in body["by_employee"] if row["employee_id"] == "NV09")["customers"]
    assert {row["employee_id"] for row in one["rows"]} == {"NV09"}
    assert "refused" in bad and "rows" not in bad                                           # ca xấu: mã nhân viên lạ ⇒ từ chối, không có danh sách
    real_codes = json.loads(MASK.read_text(encoding="utf-8"))["map"].values()
    assert not any(code in json.dumps(body, ensure_ascii=False) for code in real_codes)     # nhân viên chỉ bằng mã che
    assert not any("không ghép được" in note for note in body["notes"])                     # dòng tổng của sổ không còn bị đếm thành "1 khách không ghép được"
    assert team_total == 152_360_000                                                        # không đụng bảng thưởng
    assert "/api/membership" not in main.SYSTEM_ONLY_PATHS and not "/api/membership".startswith(main.SYSTEM_ONLY_PREFIXES)   # cờ KPI_SYSTEM_ONLY bật ⇒ 404
