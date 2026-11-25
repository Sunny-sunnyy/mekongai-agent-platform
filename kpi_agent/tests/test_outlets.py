"""Điểm bán thuộc tuyến + Trà 365 (`backend/outlets.py`, đường /api/outlets và /api/tea-365): dòng nhỏ tự dựng cho từng cách đếm
+ ca xấu + 1 ca thật RAW 31/10 (số A ghi trước khi có code: 2.501 · 2.113 · 388; Trà 365 461 khách · 49 khách từ 5 thùng; tổng thùng đúng 1.180,375)."""
import json
from datetime import date
from pathlib import Path

import pytest

from kpi_agent.backend import outlets
from kpi_agent.core.load_raw import OFF, ON, focus_spec

# bậc 3a P3: các giá trị này trước là hằng của load_raw; nay là giá trị luật kỳ — test tự khai giá trị tháng 10
FOCUS, FOCUS_2 = "Trà Búp Non Tea 365", "Nước giặt xả Homey"


def _focus(homey_skus):
    """Nhóm / mã KPI 5 – 6 như luật kỳ đưa cho `outlets.compute` (bậc 3a P3): danh sách mã ⇒ KPI 6 theo mã; None ⇒ cả nhóm Homey."""
    homey = ({"aso_focus_2_scope": "sku_list", "aso_focus_2_skus": list(homey_skus), "aso_focus_2_label": "Homey của test"} if homey_skus is not None
             else {"aso_focus_2_scope": "whole_group"})
    assumptions = {"aso_focus_groups": [FOCUS], "aso_focus_2_groups": [FOCUS_2], **homey}
    return {kpi: focus_spec(assumptions, kpi) for kpi in ("ASO_Focus", "ASO_Focus_2")}

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
MCP = ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"
MASK = ROOT / "kpi_agent/data/ma_che_ten.json"
needs_data = pytest.mark.skipif(not all(p.exists() for p in (RAW, MCP, MASK, ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx")),
                                reason="không có dữ liệu thật trên máy này")
FISH = ["Nước mắm thử"]
MASKED = {"SM1": "NV01", "SM2": "NV02"}
ROUTE = {"SM1": {"K1": {"channel": OFF, "visit_day": "Thứ 2,Thứ 5,"}, "K2": {"channel": ON, "visit_day": "Thứ 3,"}}, "SM2": {"K3": {"channel": OFF, "visit_day": ""}}}


def _line(sm="SM1", outlet="K1", dms="Hàng khác", sku="S1", sales=100.0, moq="Y", cases=0.0, channel=OFF, row=2, after=None):
    return {"SM CODE": sm, "OUTLET CODE": outlet, "DMS OBJ": dms, "SKU CODE": sku, "Sales Before Discount": sales, "MOQ_FLAG": moq,
            "Sales After Discount": sales if after is None else after,
            "TOTAL CASES": cases, "L1": channel, "OUTLET NAME": f"Cửa hàng {outlet}", "ADDRESS": f"Số 1 đường {outlet}", "_row": row}


def _rows(lines, homey_skus=("H29",), late=()):
    lines = lines + [_line(sm="SM9", outlet="K9", dms=FISH[0], sales=0.0)]   # nhóm nước mắm có mặt trong RAW (ở điểm ngoài tuyến) ⇒ phép kiểm nhóm qua
    return outlets.compute(lines, ROUTE, MASKED, _focus(homey_skus), FISH, list(late))


def _row(rows, outlet):
    return next(row for row in rows if row["outlet_code"] == outlet)


def test_every_route_outlet_has_a_row_even_without_orders():
    rows = _rows([_line()])
    assert [(row["employee_id"], row["outlet_code"]) for row in rows] == [("NV01", "K1"), ("NV01", "K2"), ("NV02", "K3")]
    empty = _row(rows, "K3")
    assert empty["has_orders"] is False and empty["outlet_name"] is None and empty["address"] is None
    assert (empty["sales"], empty["moq_reached"], empty["tea_cases"], empty["homey_reached"]) == (0.0, False, 0.0, False)
    assert _row(rows, "K1")["visit_days"] == ["Thứ 2", "Thứ 5"] and _row(rows, "K2")["channel"] == "On" and empty["visit_days"] == []


def test_orders_outside_the_employees_own_route_do_not_count():
    # K3 thuộc tuyến SM2: đơn SM1 bán vào K3, và đơn vào điểm không thuộc tuyến nào, đều không tính (công văn 6.2)
    rows = _rows([_line(sm="SM1", outlet="K3", sales=500.0), _line(sm="SM1", outlet="K8", sales=900.0)])
    assert all(row["sales"] == 0.0 and not row["has_orders"] for row in rows)


def test_moq_reached_needs_a_moq_line_with_sales():
    rows = _rows([_line(outlet="K1", moq="N", sales=900.0), _line(outlet="K2", moq="Y", sales=0.0), _line(sm="SM2", outlet="K3", moq="Y", sales=1.0)])
    assert [row["moq_reached"] for row in rows] == [False, False, True]
    assert _row(rows, "K1")["has_orders"] is True          # có đơn nhưng không dòng nào đủ số lượng tối thiểu ⇒ chưa đạt


def test_tea_cases_count_every_tea_line_and_thresholds_are_inclusive():
    lines = [_line(outlet="K1", dms=FOCUS, cases=0.5, moq="N"), _line(outlet="K1", dms=FOCUS, cases=0.5),      # đúng 1 thùng, một dòng không cờ MOQ
             _line(outlet="K2", dms=FOCUS, cases=0.75),                                                          # dưới 1 thùng ⇒ không là khách Trà 365
             _line(sm="SM2", outlet="K3", dms=FOCUS, cases=4.875), _line(sm="SM2", outlet="K3", dms=FOCUS, cases=0.125)]   # đúng 5 thùng
    rows = _rows(lines)
    tea = outlets.tea_view(rows, ["NV01", "NV02"])
    assert tea["summary"] == {"customers": 2, "cases": 6.0, "customers_from_5": 1}
    assert [(row["outlet_code"], row["tea_cases"]) for row in tea["rows"]] == [("K3", 5.0), ("K1", 1.0)]
    assert tea["by_employee"] == [{"employee_id": "NV01", "customers": 1, "cases": 1.0, "customers_from_5": 0},
                                  {"employee_id": "NV02", "customers": 1, "cases": 5.0, "customers_from_5": 1}]
    assert "address" not in tea["rows"][0] and "sales" not in tea["rows"][0]


def test_tea_cases_add_up_exactly():
    # 0,1 thùng × 10 dòng = đúng 1 thùng (cộng số thực ra 0,9999999999999999 ⇒ rớt khỏi danh sách)
    rows = _rows([_line(dms=FOCUS, cases=0.1) for _ in range(10)])
    assert _row(rows, "K1")["tea_cases"] == 1.0 and outlets.tea_view(rows, ["NV01", "NV02"])["summary"]["customers"] == 1


def test_homey_on_channel_and_fish_sauce_columns():
    lines = [_line(outlet="K1", dms=FOCUS_2, sku="H29", sales=300.0), _line(outlet="K2", dms=FOCUS_2, sku="H-KHAC", sales=300.0, channel=ON),
             _line(outlet="K2", dms=FISH[0], sales=70.0, moq="N", channel=ON)]
    rows = _rows(lines)
    assert (_row(rows, "K1")["homey_reached"], _row(rows, "K2")["homey_reached"]) == (True, False)     # chỉ mã hàng của KPI 6
    assert (_row(rows, "K2")["on_sales"], _row(rows, "K2")["premium_fish_sauce_sales"], _row(rows, "K1")["on_sales"]) == (370.0, 70.0, 0.0)
    whole_group = _rows(lines, homey_skus=None)
    assert _row(whole_group, "K2")["homey_reached"] is True                                             # luật chọn cả nhóm ⇒ mã nào của nhóm cũng tính


def test_outlets_view_counts_and_employee_filter():
    rows = _rows([_line(outlet="K1", sales=100.5), _line(sm="SM2", outlet="K3", sales=200.25, moq="N")])
    team = outlets.outlets_view(rows, ["NV01", "NV02"])
    assert team["summary"] == {"outlets": 3, "moq_reached": 1, "moq_not_reached": 2, "sales": 300.75, "page_sales": 300.75, "without_orders": 1}
    one = outlets.outlets_view(rows, ["NV01", "NV02"], "NV02")
    assert one["summary"]["outlets"] == 1 and [row["outlet_code"] for row in one["rows"]] == ["K3"]
    assert one["by_employee"] == team["by_employee"] and len(team["by_employee"]) == 2                 # bảng theo nhân viên luôn là cả đội


def test_bad_inputs_stop_without_echoing_cell_content():
    with pytest.raises(ValueError, match="không thuộc đội"):
        outlets.compute([], ROUTE, {"SM1": "NV01"}, _focus(None), [])
    with pytest.raises(ValueError, match="không có trong RAW"):
        outlets.compute([_line()], ROUTE, MASKED, _focus(None), ["Nhóm gõ sai"])
    with pytest.raises(ValueError, match="dòng Excel 7") as stopped:
        _rows([_line(dms=FOCUS, cases="hai thùng rưỡi", row=7)])
    assert "hai thùng" not in str(stopped.value)


def test_page_money_is_after_discount_with_late_invoices_and_tea_moq_only():
    # Dòng 13 bài 15b (13/11): tiền "như trang" = sau chiết khấu + cộng cả hoá đơn trễ; Trà 365 chỉ dòng đạt MOQ. Số KPI cũ + số đếm KHÔNG đổi.
    lines = [_line(outlet="K1", sales=1000.0, after=900.0), _line(outlet="K1", dms=FOCUS, cases=1.0, sales=200.0, after=180.0),
             _line(outlet="K1", dms=FOCUS, cases=1.0, sales=50.0, after=45.0, moq="N"), _line(outlet="K1", dms=FISH[0], sales=100.0, after=95.0)]
    late = [_line(outlet="K1", sales=300.0, after=280.0), _line(outlet="K2", dms=FOCUS, sales=40.0, after=38.0),   # K2: CHỈ có đơn trễ
            _line(sm="SM1", outlet="K3", sales=999.0, after=999.0)]                                              # đơn trễ ngoài tuyến của SM1 ⇒ không tính
    rows = _rows(lines, late=late)
    k1, k2, k3 = _row(rows, "K1"), _row(rows, "K2"), _row(rows, "K3")
    assert (k1["sales"], k1["tea_sales"], k1["premium_fish_sauce_sales"]) == (1350.0, 250.0, 100.0)          # cách cũ (trước CK, đúng hạn) giữ nguyên
    assert (k1["page_sales"], k1["page_tea_sales"], k1["page_premium_fish_sauce_sales"]) == (1500.0, 180.0, 95.0)
    assert k2["has_orders"] is False and k2["moq_reached"] is False and (k2["page_sales"], k2["page_tea_sales"]) == (38.0, 38.0)
    assert k2["sales"] == 0.0 and k2["outlet_name"] == "Cửa hàng K2"                                          # tên lấy từ dòng trễ; "chưa có đơn" vẫn theo nghĩa cũ
    assert k3["page_sales"] == 0.0
    assert outlets.outlets_view(rows, ["NV01", "NV02"])["summary"]["without_orders"] == 2


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    patch = pytest.MonkeyPatch()
    patch.setattr(main, "CUTOFF", date(2026, 10, 31))
    patch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    patch.setenv("KPI_STORE", str(tmp_path_factory.mktemp("outlets") / "store.sqlite"))
    patch.delenv("KPI_SYSTEM_ONLY", raising=False)
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as started:
        yield started
    patch.undo()


def test_kpi1_sales_on_card_adds_late_lines_with_invoice_date_when_turnover_counts_them():
    # Mốc 7 N2 (16/11): KPI 1 cộng dòng hoá đơn trễ CÓ ngày xuất (trang sếp) ⇒ dòng "Doanh số tính KPI 1" trên thẻ cộng theo (luật `late_invoice_kpis`
    # có Turnover); dòng trễ CHƯA có ngày xuất vẫn không vào KPI 1 (ca xấu); tiền "như trang" + "có đơn" không đổi
    from datetime import datetime
    lines = [_line(outlet="K1", sales=1000.0, after=900.0), _line(outlet="K9", dms=FISH[0], sales=5.0)]   # K9 ngoài tuyến: chỉ để có nhóm nước mắm
    late = [{**_line(outlet="K1", sales=300.0, after=280.0), "INVOICED DATE": datetime(2026, 11, 3, 9)},
            {**_line(outlet="K2", sales=40.0, after=38.0), "INVOICED DATE": None}]
    rows = outlets.compute(lines, ROUTE, MASKED, _focus(["H29"]), FISH, late, late_in_kpi1=True)
    k1, k2 = _row(rows, "K1"), _row(rows, "K2")
    assert (k1["sales"], k1["page_sales"]) == (1300.0, 1180.0) and (k2["sales"], k2["page_sales"], k2["has_orders"]) == (0.0, 38.0, False)
    assert _row(outlets.compute(lines, ROUTE, MASKED, _focus(["H29"]), FISH, late), "K1")["sales"] == 1000.0        # mặc định như cũ


@needs_data
def test_api_real_3110_counts(client):
    body = client.get("/api/outlets").json()
    assert body["summary"]["outlets"] == 2501 and body["summary"]["moq_reached"] == 2113 and body["summary"]["moq_not_reached"] == 388
    assert len(body["rows"]) == 2501 and len(body["by_employee"]) == 14
    assert sum(item["outlets"] for item in body["by_employee"]) == 2501 and sum(item["moq_reached"] for item in body["by_employee"]) == 2113
    assert sum(row["homey_reached"] for row in body["rows"]) == 393          # = KPI 6 cả đội trên RAW 31/10
    assert body["summary"]["without_orders"] == 387 and any("387 điểm bán" in note for note in body["notes"])
    # Mốc 7 N2 (16/11): + 8.560.000 đ = 3 dòng hoá đơn trễ có ngày xuất (1 đơn của NV05 — đã đếm riêng 10/11) vì KPI 1 nay cộng như trang (trước: 10.332.199.856)
    assert round(body["summary"]["sales"]) == 10_340_759_856
    # Dòng 13 (13/11): tiền "như trang" khớp trang — đã đếm riêng (script ngoài repo) 2.501 / 2.501 điểm ở cả 3 ô; số trang đọc từ `D.ct` của trang 31/10
    assert round(sum(row["page_tea_sales"] for row in body["rows"])) == 258_649_195 and round(sum(row["page_premium_fish_sauce_sales"] for row in body["rows"])) == 199_338_201
    assert sum(round(row["page_sales"]) for row in body["rows"]) == 10_144_849_688                               # cộng số đã làm tròn tới đồng như trên thẻ = số trang
    # ghi chú: số của MÌNH do backend điền (không phải chữ cứng), số của trang lấy từ file lệch đã biết; ghi đúng số đồng lệch
    sales_note = next(note for note in body["notes"] if "trang theo dõi của đội ghi" in note and "ở đây" in note)
    assert "trang theo dõi của đội ghi 10.144.849.688 đ" in sales_note and ("khớp từng đồng" in sales_note or "do làm tròn từng điểm bán" in sales_note)
    assert "doanh số tính KPI 1 (trước chiết khấu) là 10.340.759.856 đ" in sales_note
    one = client.get("/api/outlets", params={"employee_id": "NV05"}).json()
    assert sales_note in one["notes"] and round(one["summary"]["sales"]) < 10_340_759_856      # lọc một người: ghi chú vẫn nói tổng CẢ ĐỘI
    tea = client.get("/api/tea-365").json()
    assert tea["summary"] == {"customers": 461, "cases": 1180.375, "customers_from_5": 49}
    assert len(tea["rows"]) == 461 and min(row["tea_cases"] for row in tea["rows"]) == 1.0
    assert any("chưa tính tiền" in note for note in tea["notes"])
    # mã SM thật của nhân viên không ra ngoài (dòng chỉ mang mã che)
    real_codes = json.loads(MASK.read_text(encoding="utf-8"))["map"].values()
    assert not any(code in json.dumps(body, ensure_ascii=False) for code in real_codes)


@needs_data
def test_api_one_employee_is_a_subset(client):
    team = client.get("/api/outlets").json()
    one = client.get("/api/outlets", params={"employee_id": "nv05"}).json()
    expected = next(item for item in team["by_employee"] if item["employee_id"] == "NV05")
    assert one["employee_id"] == "NV05" and {row["employee_id"] for row in one["rows"]} == {"NV05"}
    assert one["summary"]["outlets"] == expected["outlets"] == len(one["rows"]) and one["summary"]["moq_reached"] == expected["moq_reached"]
    tea_one = client.get("/api/tea-365", params={"employee_id": "NV05"}).json()
    tea_team = client.get("/api/tea-365").json()
    assert tea_one["summary"] == {key: value for key, value in next(i for i in tea_team["by_employee"] if i["employee_id"] == "NV05").items() if key != "employee_id"}


@needs_data
@pytest.mark.parametrize("path", ["/api/outlets", "/api/tea-365"])
@pytest.mark.parametrize("bad", ["NV99", "", "../../etc/passwd", "SM000123", "NV05/../NV01", "x" * 5000])
def test_api_bad_employee_is_refused_without_rows_or_paths(client, path, bad):
    response = client.get(path, params={"employee_id": bad})
    body = response.json()
    assert response.status_code == 200 and "refused" in body and "rows" not in body and "summary" not in body
    assert "/home" not in response.text and "Errno" not in response.text and "Traceback" not in response.text
    assert bad in ("", "NV99") or bad not in body["refused"]      # không nhắc lại chữ lạ người gọi gửi (mã đúng dạng NVxx thì được nói lại)


@needs_data
@pytest.mark.parametrize("path", ["/api/outlets", "/api/tea-365"])
def test_api_is_read_only(client, path):
    assert [client.request(method, path).status_code for method in ("POST", "PUT", "DELETE")] == [405, 405, 405]


@needs_data
def test_missing_route_file_refusal_names_only_the_relative_file(client, monkeypatch):
    from kpi_agent.backend import main
    real = main.runner.node_param
    monkeypatch.setattr(main.runner, "node_param", lambda workflow, block, key: "kpi_agent/data/khong_co_file_tuyen.json" if key == "mcp_path" else real(workflow, block, key))
    monkeypatch.setattr(client.app.state, "outlets", None)
    body = client.get("/api/outlets").json()
    assert body["refused"] == "danh sách điểm bán không tính được — thiếu file kpi_agent/data/khong_co_file_tuyen.json"
    monkeypatch.setattr(client.app.state, "outlets", None)        # ca sau tính lại từ file thật


@needs_data
def test_tracking_workflow_has_no_outlet_list(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))
    # workflow đọc sổ Tracking (ảnh chụp 20/10 — không có dòng đơn từng cửa hàng); đặt tường minh vì ca thật ở trên đang giữ workflow RAW
    monkeypatch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh.yaml")
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 20))
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as started:
        assert main.runner.data_block_type(started.app.state.workflow) != "read_raw"
        assert "chỉ có ở workflow đường RAW DATA" in started.get("/api/outlets").json()["refused"]
        assert "refused" in started.get("/api/tea-365").json()


def test_outlet_routes_are_page_routes_not_system_or_tool_routes():
    from kpi_agent.agent import backend_client
    from kpi_agent.backend import main
    for path in ("/api/outlets", "/api/tea-365"):
        assert path not in main.SYSTEM_ONLY_PATHS and not path.startswith(main.SYSTEM_ONLY_PREFIXES)     # cờ KPI_SYSTEM_ONLY bật ⇒ 404
    source = Path(backend_client.__file__).read_text(encoding="utf-8")
    assert "/api/outlets" not in source and "/api/tea-365" not in source                                # agent không gọi ⇒ tên cửa hàng không vào chữ gửi mô hình


# ---------- 13/11: tên + địa chỉ cho điểm thuộc tuyến CHƯA có đơn, lấy từ sheet "MCP" của sổ (trang của đội có tên cho đủ 2.501 điểm) ----------

def test_fill_names_only_touches_rows_without_a_name():
    rows = _rows([_line()])                                   # K1 có đơn (có tên từ file đơn hàng); K2, K3 chưa có đơn
    filled = outlets.fill_names(rows, {"K1": ("Tên khác trong sổ", "Địa chỉ khác"), "K2": ("Tạp hoá K2", "Số 2"), "K9": ("Ngoài tuyến", "x")})
    assert filled == 1
    assert (_row(rows, "K1")["outlet_name"], _row(rows, "K1").get("name_from_route_sheet")) == ("Cửa hàng K1", None)      # tên từ đơn hàng giữ nguyên
    assert (_row(rows, "K2")["outlet_name"], _row(rows, "K2")["address"], _row(rows, "K2")["name_from_route_sheet"]) == ("Tạp hoá K2", "Số 2", True)
    assert _row(rows, "K3")["outlet_name"] is None and _row(rows, "K2")["has_orders"] is False                             # sổ không có K3 ⇒ vẫn chưa có tên; số đếm không đổi


def test_read_route_names_bad_workbook_is_refused(tmp_path):
    from openpyxl import Workbook
    book = Workbook()
    book.active.title = "Khac"
    path = tmp_path / "so_khac.xlsx"
    book.save(path)
    with pytest.raises(ValueError, match="không có sheet"):
        outlets.read_route_names(path)


@needs_data
def test_real_route_sheet_names_every_outlet_3110():
    names = outlets.read_route_names(ROOT / "sample_data/thang_10/Tracking.xlsx")
    route = json.loads(MCP.read_text(encoding="utf-8"))
    route_outlets = {outlet for outlets_of in (route.get("mcp") or route).values() if isinstance(outlets_of, dict) for outlet in outlets_of}
    assert len(route_outlets) == 2501 and route_outlets <= set(names)                       # sổ có tên cho ĐỦ 2.501 điểm của file tuyến
    assert all(names[outlet][0] for outlet in route_outlets)
