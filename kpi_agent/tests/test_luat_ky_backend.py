"""Bậc 3a P3 — các màn đếm lại NGOÀI lõi (xem đơn, điểm bán, hội viên) đọc nhóm / mã KPI 5 – 6, ngành LPPC, trạng thái được tính từ luật kỳ như lõi,
không còn hằng tháng 10 trong code. Dòng thử DỰNG GIẢ trong bộ nhớ (không lấy từ dữ liệu thật). Không mock.
"""
import inspect
from datetime import date, datetime
from decimal import Decimal

import openpyxl

from kpi_agent.backend import membership, order_lines, outlets
from kpi_agent.core import load_raw
from kpi_agent.core.load_raw import OFF, focus_spec

D1 = date(2026, 10, 5)
# Luật của một THÁNG KHÁC tháng 10: KPI 5 theo nhóm "Nhóm tháng sau", KPI 6 theo mã Z1 (thuộc nhóm bất kỳ), LPPC chỉ trừ ngành "Ngành A", KPI 8 = "Ngành B"
RULES = {"pc_bt_min_lines": 4, "lppc_min_lines": 1, "lppc_lines_exclude_free_items": True, "lppc_same_day_lines": "all",
         "aso_focus_groups": ["Nhóm tháng sau"], "aso_focus_2_scope": "sku_list", "aso_focus_2_skus": ["Z1"], "aso_focus_2_label": "Mã Z1 của tháng sau",
         "lppc_excluded_subdiv": ["Ngành A"], "lppc_meat_subdiv": "Ngành B", "exclude_outlets_outside_mcp": False}
ROUTE = {"K1": {}, "K2": {}, "K3": {}}


def order_line(order, outlet="K1", sku="S1", sub="Ngành khác", dms="Hàng khác"):
    return {"SM CODE": "SM1", "ORDER NUM": order, "OUTLET CODE": outlet, "ORDER DATE": datetime(D1.year, D1.month, D1.day), "SKU CODE": sku,
            "Sales Before Discount": 100.0, "MOQ_FLAG": "Y", "FREE ITEM FLAG": "N", "L1": OFF, "OUTLET NAME": f"Cửa hàng {outlet}", "ADDRESS": "x",
            "SUB DIV": sub, "DMS OBJ": dms, "_drop": None}


def test_order_lines_kpi6_sku_from_any_group_is_counted():
    # Trước P3: màn xem đơn lọc nhóm "Nước giặt xả Homey" rồi mới lọc mã ⇒ mã Z1 thuộc nhóm khác ra 0 cửa hàng
    result = order_lines.build("ASO_Focus_2", [order_line("D1", sku="Z1", dms="Nhóm bất kỳ"), order_line("D2", outlet="K2", sku="KHAC")], ROUTE, RULES)
    assert result["counted_value"] == 1
    assert {row["outlet_code"]: row["counted"] for row in result["rows"]} == {"K1": True, "K2": False}
    assert [row["reasons"] for row in result["rows"] if not row["counted"]] == [[order_lines.NO_FOCUS.format(what="Mã Z1 của tháng sau")]]


def test_order_lines_kpi5_follows_period_group_and_shows_its_name():
    # Trước P3: KPI 5 luôn lọc "Trà Búp Non Tea 365" ⇒ nhóm của tháng sau ra 0; chữ lý do luôn nói "Trà 365"
    lines = [order_line("D1", dms="Nhóm tháng sau"), order_line("D2", outlet="K2", dms="Trà Búp Non Tea 365")]
    result = order_lines.build("ASO_Focus", lines, ROUTE, RULES)
    assert result["counted_value"] == 1 and {row["outlet_code"]: row["counted"] for row in result["rows"]} == {"K1": True, "K2": False}
    assert [row["reasons"] for row in result["rows"] if not row["counted"]] == [[order_lines.NO_FOCUS.format(what="Nhóm tháng sau")]]


def test_order_lines_lppc_subdivs_follow_period_rules():
    # Trước P3: LPPC luôn trừ Processed Meats + Beer, KPI 8 luôn đếm Processed Meats
    lines = [order_line("D1", sub="Ngành A"), order_line("D2", outlet="K2", sub="Processed Meats"), order_line("D3", outlet="K3", sub="Ngành B")]
    lppc = order_lines.build("LPPC", lines, ROUTE, RULES)
    assert {row["order_num"]: row["counted"] for row in lppc["rows"]} == {"D1": False, "D2": True, "D3": True}
    meat = order_lines.build("LPPC_Meat", lines, ROUTE, RULES)
    assert {row["order_num"]: row["counted"] for row in meat["rows"]} == {"D1": False, "D2": False, "D3": True}


def test_outlets_tea_and_homey_columns_follow_period_rules():
    # Trước P3: cột Trà chỉ nhận nhóm "Trà Búp Non Tea 365"; cột Homey theo mã / nhóm ghi cứng
    def line(outlet, dms, sku="S1"):
        return {"SM CODE": "SM1", "OUTLET CODE": outlet, "DMS OBJ": dms, "SKU CODE": sku, "Sales Before Discount": 100.0, "Sales After Discount": 90.0,
                "MOQ_FLAG": "Y", "TOTAL CASES": 2.0, "L1": OFF, "OUTLET NAME": outlet, "ADDRESS": "x", "_row": 2}
    route = {"SM1": {"K1": {"channel": OFF, "visit_day": ""}, "K2": {"channel": OFF, "visit_day": ""}}}
    focus = {kpi: focus_spec(RULES, kpi) for kpi in ("ASO_Focus", "ASO_Focus_2")}
    lines = [line("K1", "Nhóm tháng sau"), line("K2", "Trà Búp Non Tea 365"), line("K2", "Nhóm bất kỳ", sku="Z1"), line("K1", "Nước mắm thử")]
    rows = {row["outlet_code"]: row for row in outlets.compute(lines, route, {"SM1": "NV01"}, focus, ["Nước mắm thử"])}
    assert (rows["K1"]["tea_cases"], rows["K2"]["tea_cases"]) == (2.0, 0.0)
    assert (rows["K1"]["homey_reached"], rows["K2"]["homey_reached"]) == (False, True)


def test_membership_counted_status_follows_period_rules(tmp_path):
    book = openpyxl.Workbook()
    book.active.title = "Export"
    book.active.append(["SM CODE", "OUTLET CODE", "Sales After Discount", "ORDER DATE", "ORDER STATUS"])
    book.active.append(["SM1", "K1", 100, datetime(2026, 10, 2), "Invoiced"])
    book.active.append(["SM1", "K1", 50, datetime(2026, 10, 3), "Shipped"])
    path = tmp_path / "raw.xlsx"
    book.save(path)
    both = membership.sales_after_discount(path, date(2026, 10, 31), counted_status={"Invoiced", "Shipped"})
    only_invoiced = membership.sales_after_discount(path, date(2026, 10, 31), counted_status={"Invoiced"})
    assert (dict(both), dict(only_invoiced)) == ({"K1": Decimal("150")}, {"K1": Decimal("100")})


def test_month_values_are_not_constants_in_code():
    # Hằng theo tháng đã gỡ khỏi lõi; ba file dưới không còn chữ của giá trị tháng 10 (bảng TÊN GỌI TẮT trên màn xem đơn không phải phép lọc — không xét)
    for name in ("COUNTED_STATUS", "FOCUS", "FOCUS_2", "MEAT", "LPPC_EXCLUDED_SUBDIV"):
        assert not hasattr(load_raw, name), name
    for module in (load_raw, outlets, membership):
        source = inspect.getsource(module)
        for text in ("Trà Búp Non Tea 365", "Nước giặt xả Homey", "Processed Meats", '"Beer"', '"Invoiced"', '"Shipped"'):
            assert text not in source, (module.__name__, text)
