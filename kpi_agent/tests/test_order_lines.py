"""Đơn hàng của một nhân viên (`backend/order_lines.py`, đường /api/order-lines): bản luật THỨ HAI đi song song lõi.

Phép giữ sàn chính: `test_real_3110_every_employee_matches_core` — với MỌI nhân viên × MỌI chỉ tiêu đã làm, số "được tính" ở đây phải bằng số thực
hiện của lõi trên RAW 31/10. Ai đổi luật ở `core/` mà quên `order_lines.py` ⇒ test này đỏ. Các ca nhỏ còn lại giữ từng lý do loại."""
from datetime import date, datetime
from pathlib import Path

import pytest

from kpi_agent.backend import order_lines as ol
from kpi_agent.core.load_raw import OFF, ON

# bậc 3a P3: các giá trị này trước là hằng của load_raw; nay là giá trị luật kỳ — test tự khai giá trị tháng 10
FOCUS, FOCUS_2, MEAT = "Trà Búp Non Tea 365", "Nước giặt xả Homey", "Processed Meats"

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
NEEDED = [RAW, ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json", ROOT / "kpi_agent/data/ma_che_ten.json", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"]
needs_data = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
RULES = {"pc_bt_min_lines": 4, "moq_lines_exclude_free_items": True, "lppc_min_lines": 4, "lppc_lines_exclude_free_items": True,
         "lppc_same_day_lines": "one", "aso_focus_2_scope": "sku_list", "aso_focus_2_skus": ["H29"],
         # bậc 3a P3: màn xem đơn đọc nhóm / mã KPI 5 – 6 và ngành LPPC từ luật kỳ (trước là hằng trong code)
         "aso_focus_groups": [FOCUS], "aso_focus_2_groups": [FOCUS_2], "aso_focus_2_label": "Nước giặt xả Homey túi 2,9 kg",
         "lppc_excluded_subdiv": [MEAT, "Beer"], "lppc_meat_subdiv": MEAT,
         # bộ luật tự đặt của test: lọc tuyến ở MỌI KPI để thử logic lọc; luật RAW thật (mốc 7) chỉ lọc 4 KPI — ca riêng bên dưới
         "exclude_outlets_outside_mcp": True,
         "mcp_filter_kpis": ["Turnover", "PC_BT", "LPPC", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat"]}
ROUTE = {"K1": {}, "K2": {}}
D1, D2 = date(2026, 10, 5), date(2026, 10, 6)


def _line(order="D1", outlet="K1", day=D1, sku="S1", sales=100.0, moq="Y", free="N", channel=OFF, drop=None, sub="Food", dms="Hàng khác"):
    return {"SM CODE": "SM1", "ORDER NUM": order, "OUTLET CODE": outlet, "ORDER DATE": datetime(day.year, day.month, day.day), "SKU CODE": sku,
            "Sales Before Discount": sales, "MOQ_FLAG": moq, "FREE ITEM FLAG": free, "L1": channel, "OUTLET NAME": f"Cửa hàng {outlet}", "ADDRESS": "x",
            "SUB DIV": sub, "DMS OBJ": dms, "_drop": drop}


def _order(order, outlet="K1", day=D1, skus=4, **kw):
    return [_line(order=order, outlet=outlet, day=day, sku=f"S{i}", **kw) for i in range(skus)]


def _row(result, order):
    return next(row for row in result["rows"] if row.get("order_num") == order)


def test_turnover_counts_every_kept_line_in_route_whatever_the_moq_flag():
    lines = _order("D1", moq="N") + _order("D2", outlet="K9") + [_line(order="D3", drop=ol.LATE_INVOICE, sales=999.0)] + [_line(order="D4", free="Y", sales=0.0)]
    result = ol.build("Turnover", lines, ROUTE, RULES)
    assert result["counted_value"] == 400.0                                   # D1 (không cờ đủ số lượng vẫn tính doanh số) + D4 (0 đ)
    assert (_row(result, "D1")["counted"], _row(result, "D1")["reasons"]) == (True, [])
    assert _row(result, "D2")["reasons"] == [ol.OUTSIDE_ROUTE] and _row(result, "D3")["reasons"] == [ol.LATE_INVOICE]
    assert all(ol.NO_MOQ_LINE not in row["reasons"] and ol.WRONG_CHANNEL not in row["reasons"] for row in result["rows"])   # doanh số không xét hai lý do này


def test_pc_bt_needs_four_distinct_skus_one_order_per_outlet_per_day():
    lines = (_order("DU") + _order("BA", skus=3) + _order("TRUNG", skus=4) + _order("HOMSAU", day=D2) + _order("NGOAI", outlet="K9")
             + _order("ON", outlet="K2", channel=ON) + _order("TANG", outlet="K2", free="Y") + _order("TRE", outlet="K2", drop=ol.LATE_INVOICE)
             + [_line(order="LAP", outlet="K2", sku="S1")] * 5)
    result = ol.build("PC_BT", lines, ROUTE, RULES)
    assert result["counted_value"] == 2                                       # DU + HOMSAU (cùng cửa hàng, khác ngày)
    assert _row(result, "DU")["counted"] and _row(result, "HOMSAU")["counted"]
    assert _row(result, "BA")["reasons"] == [ol.TOO_FEW_LINES.format(n=4)] and _row(result, "BA")["moq_skus"] == 3
    assert _row(result, "TRUNG")["reasons"] == [ol.SAME_DAY]                  # đơn thứ hai cùng ngày cùng cửa hàng
    assert _row(result, "NGOAI")["reasons"] == [ol.OUTSIDE_ROUTE] and _row(result, "ON")["reasons"] == [ol.WRONG_CHANNEL]
    assert _row(result, "TANG")["reasons"] == [ol.NO_MOQ_LINE]                # hàng tặng không là mã (luật moq_lines_exclude_free_items)
    assert _row(result, "TRE")["reasons"] == [ol.LATE_INVOICE]
    assert _row(result, "LAP")["reasons"] == [ol.TOO_FEW_LINES.format(n=4)] and _row(result, "LAP")["moq_skus"] == 1   # 5 dòng cùng một mã = 1 mã
    assert result["summary"]["counted"] == 2 and result["summary"]["not_counted"] == 7 and result["summary"]["by_reason"][ol.SAME_DAY] == 1


def test_free_items_count_as_lines_when_the_rule_says_so():
    assert ol.build("PC_BT", _order("TANG", free="Y"), ROUTE, {**RULES, "moq_lines_exclude_free_items": False})["counted_value"] == 1


def test_pc_on_needs_one_moq_sku_on_the_on_channel():
    lines = [_line(order="MOT", channel=ON)] + [_line(order="HAI", channel=ON)] + [_line(order="KHONG", outlet="K2", channel=ON, moq="N")] + _order("OFF", outlet="K2")
    result = ol.build("PC_ON", lines, ROUTE, RULES)
    assert result["counted_value"] == 1 and _row(result, "MOT")["counted"] and _row(result, "HAI")["reasons"] == [ol.SAME_DAY]
    assert _row(result, "KHONG")["reasons"] == [ol.NO_MOQ_LINE] and _row(result, "OFF")["reasons"] == [ol.WRONG_CHANNEL]


def test_aso_all_is_one_row_per_outlet():
    lines = (_order("D1") + [_line(order="D2", outlet="K2", moq="N")] + _order("D3", outlet="K9") + [_line(order="D4", outlet="K7", drop=ol.BAD_STATUS)]
             + [_line(order="D5", outlet="K8", free="Y", sales=0.0)])
    result = ol.build("ASO_ALL", lines, {**ROUTE, "K8": {}}, RULES)
    by_outlet = {row["outlet_code"]: row for row in result["rows"]}
    assert result["unit"] == "outlet" and result["counted_value"] == 1 and by_outlet["K1"]["counted"] and by_outlet["K1"]["order_count"] == 1
    assert by_outlet["K2"]["reasons"] == [ol.NO_MOQ_SALES] and by_outlet["K9"]["reasons"] == [ol.OUTSIDE_ROUTE] and by_outlet["K7"]["reasons"] == [ol.BAD_STATUS]
    assert by_outlet["K8"]["reasons"] == [ol.NO_MOQ_SALES]                    # chỉ có dòng hàng tặng 0 đ ⇒ doanh số dòng đủ số lượng = 0 ⇒ không tính


def test_unsupported_kpi_stops_and_match_is_exact_for_counts():
    with pytest.raises(ValueError, match="chưa có danh sách"):
        ol.build("KHONG_CO", [], ROUTE, RULES)
    assert ol.matches_core("PC_BT", 12, 12.0) and not ol.matches_core("PC_BT", 12, 13) and not ol.matches_core("ASO_ALL", 187, 186)
    assert ol.matches_core("Turnover", 849_613_006.004, 849_613_006.0) and not ol.matches_core("Turnover", 849_613_006.0, 849_613_007.0)


def test_every_failed_condition_is_listed_not_only_the_first():
    # Góp ý C bậc 1 minor 1: đơn ngoài tuyến VÀ chưa đủ 4 mã ⇒ nêu cả hai; cửa hàng ngoài tuyến VÀ không doanh số mã đủ số lượng ⇒ nêu cả hai
    pc = ol.build("PC_BT", _order("X1", outlet="K9", skus=2), ROUTE, RULES)
    assert _row(pc, "X1")["reasons"] == [ol.TOO_FEW_LINES.format(n=4), ol.OUTSIDE_ROUTE]
    aso = ol.build("ASO_ALL", [_line(order="X2", outlet="K9", moq="N")], ROUTE, RULES)
    assert aso["rows"][0]["reasons"] == [ol.NO_MOQ_SALES, ol.OUTSIDE_ROUTE]
    # đơn LẪN dòng bị loại + dòng giữ đủ điều kiện ⇒ vẫn được tính (lý do của dòng bị loại vẫn hiện)
    mixed = ol.build("PC_BT", _order("X3") + [_line(order="X3", sku="S9", drop=ol.LATE_INVOICE)], ROUTE, RULES)
    assert _row(mixed, "X3")["counted"] is True and _row(mixed, "X3")["reasons"] == [ol.LATE_INVOICE]


def test_lppc_counts_skus_outside_meat_and_beer_one_order_per_outlet_day_with_most_skus():
    lines = (_order("L1", skus=4) + _order("L2", skus=5)                              # cùng ngày, cùng K1: tính 1 đơn — đơn nhiều mã hơn (L2, 5 mã)
             + _order("L3", day=D2, skus=3) + [_line(order="L3", day=D2, sku="M1", sub=MEAT)]   # 3 mã + 1 mã thịt ⇒ chỉ 3 mã LPPC ⇒ chưa đủ 4
             + _order("L4", day=D2, outlet="K2", skus=4) + [_line(order="L4", day=D2, outlet="K2", sku="T1", free="Y")]   # dòng tặng không là mã
             + _order("L5", outlet="K9", skus=4)                                          # ngoài tuyến
             + [_line(order="L6", outlet="K2", sku="B1", sub="Beer")])                   # chỉ có bia ⇒ không mã LPPC
    result = ol.build("LPPC", lines, ROUTE, RULES)
    assert result["unit"] == "order" and (result["summary"]["lines"], result["summary"]["orders"]) == (9, 2) and result["counted_value"] == 4.5
    assert _row(result, "L2")["counted"] and _row(result, "L2")["counted_lines"] == 5 and _row(result, "L1")["reasons"] == [ol.SAME_DAY_FEWER]
    assert _row(result, "L3")["reasons"] == [ol.TOO_FEW_LPPC.format(n=4)] and _row(result, "L4")["counted_lines"] == 4
    assert _row(result, "L5")["reasons"] == [ol.OUTSIDE_ROUTE] and _row(result, "L6")["reasons"] == [ol.NO_LPPC_LINE]
    all_mode = ol.build("LPPC", lines, ROUTE, {**RULES, "lppc_same_day_lines": "all"})
    assert (all_mode["summary"]["lines"], all_mode["summary"]["orders"]) == (13, 2) and _row(all_mode, "L1")["reasons"] == [ol.SAME_DAY]


def test_lppc_meat_needs_one_meat_sku_and_rule_drift_stops():
    lines = [_line(order="M1", sku="X1", sub=MEAT), _line(order="M1", sku="X2", sub=MEAT), _line(order="M2", day=D2, sku="F1")]
    result = ol.build("LPPC_Meat", lines, ROUTE, RULES)
    assert (result["summary"]["lines"], result["summary"]["orders"], result["counted_value"]) == (2, 1, 2.0) and _row(result, "M2")["reasons"] == [ol.NO_MEAT_LINE]
    with pytest.raises(ValueError, match="dòng tặng"):
        ol.build("LPPC", lines, ROUTE, {**RULES, "lppc_lines_exclude_free_items": False})


def test_aso_focus_and_focus_2_count_outlets_of_the_product_group():
    lines = ([_line(order="F1", outlet="K1", dms=FOCUS), _line(order="F2", outlet="K2", dms=FOCUS, moq="N"), _line(order="F3", outlet="K2")]
             + [_line(order="H1", outlet="K1", dms=FOCUS_2, sku="H29"), _line(order="H2", outlet="K2", dms=FOCUS_2, sku="H-KHAC")])
    tea = ol.build("ASO_Focus", lines, ROUTE, RULES)
    tea_rows = {row["outlet_code"]: row for row in tea["rows"]}
    assert tea["unit"] == "outlet" and tea["counted_value"] == 1 and tea_rows["K1"]["counted"]
    assert tea_rows["K2"]["reasons"] == [ol.NO_FOCUS.format(what="Trà 365")]
    homey = ol.build("ASO_Focus_2", lines, ROUTE, RULES)
    assert homey["counted_value"] == 1 and {row["outlet_code"]: row["counted"] for row in homey["rows"]} == {"K1": True, "K2": False}
    whole = ol.build("ASO_Focus_2", lines, ROUTE, {**RULES, "aso_focus_2_scope": "group"})
    assert whole["counted_value"] == 2                                                   # luật chọn cả nhóm Homey ⇒ mã nào của nhóm cũng tính


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    patch = pytest.MonkeyPatch()
    patch.setattr(main, "CUTOFF", date(2026, 10, 31))
    patch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    patch.setenv("KPI_STORE", str(tmp_path_factory.mktemp("orders") / "store.sqlite"))
    patch.delenv("KPI_SYSTEM_ONLY", raising=False)
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as started:
        yield started
    patch.undo()


@needs_data
def test_real_3110_every_employee_matches_core(client):
    # GIỮ SÀN: bản luật thứ hai = lõi, cho mọi nhân viên × mọi chỉ tiêu đã làm. Số của lõi lấy từ bảng đội (đường khác đường /api/order-lines).
    board = client.get("/api/team-board").json()
    checked = 0
    for employee in board["employees"]:
        for kpi in ol.SUPPORTED:
            body = client.get("/api/order-lines", params={"employee_id": employee["employee_id"], "kpi": kpi}).json()
            assert "refused" not in body, (employee["employee_id"], kpi, body.get("refused"))
            cell = employee["cells"][kpi]
            assert body["summary"]["matches_core"] is True and abs(body["summary"]["counted_value"] - cell["actual"]) < 0.5, (employee["employee_id"], kpi)
            assert body["summary"]["counted"] + body["summary"]["not_counted"] == body["summary"]["rows"] == len(body["rows"])
            if kpi in ol.RATIO_KPIS:   # LPPC: số dòng "được tính" = số đơn (mẫu số); tử / mẫu = đúng tử / mẫu của lõi (rào ở main)
                assert body["summary"]["counted"] == body["summary"]["orders"] and body["summary"]["lines"] == sum(row.get("counted_lines", 0) for row in body["rows"])
            elif kpi != "Turnover":
                assert body["summary"]["counted"] == body["summary"]["counted_value"]      # số dòng "được tính" = số đơn / cửa hàng của chỉ tiêu
            checked += 1
    assert checked == 14 * len(ol.SUPPORTED)


@needs_data
def test_real_3110_reasons_seen_in_the_data(client):
    # Tháng 10 có 3 dòng hoá đơn trễ hạn (1 đơn) và 13 dòng ở cửa hàng ngoài tuyến — phải hiện thành lý do ở đúng người, không lặng lẽ biến mất
    seen = {}
    for number in range(1, 15):
        body = client.get("/api/order-lines", params={"employee_id": f"NV{number:02d}", "kpi": "Turnover"}).json()
        for reason, count in body["summary"]["by_reason"].items():
            seen[reason] = seen.get(reason, 0) + count
        assert body["notes"] == [ol.NOTE, ol.SAME_DAY_NOTE]
    # Mốc 7 N1: Turnover không lọc tuyến nữa (trang sếp) ⇒ "ngoài tuyến" không còn ở Turnover, vẫn còn ở ASO_ALL (có lọc)
    # Mốc 7 N2: Turnover cộng dòng hoá đơn trễ có ngày xuất (trang sếp) ⇒ lý do trễ không còn ở Turnover; vẫn hiện ở LPPC_Meat (KPI không cộng trễ)
    assert ol.LATE_INVOICE not in seen and ol.OUTSIDE_ROUTE not in seen and ol.NO_MOQ_LINE not in seen
    meat_late = sum(client.get("/api/order-lines", params={"employee_id": f"NV{n:02d}", "kpi": "LPPC_Meat"}).json()["summary"]["by_reason"].get(ol.LATE_INVOICE, 0)
                    for n in range(1, 15))
    assert meat_late == 1                                                      # 1 đơn trễ (3 dòng) của tháng 10
    aso_seen = sum(client.get("/api/order-lines", params={"employee_id": f"NV{n:02d}", "kpi": "ASO_ALL"}).json()["summary"]["by_reason"].get(ol.OUTSIDE_ROUTE, 0)
                   for n in range(1, 15))
    assert aso_seen >= 1


@needs_data
def test_runtime_gate_refuses_when_the_second_rule_copy_drifts_from_core(client, monkeypatch):
    # Giả lập "đổi luật ở lõi mà quên file này": bản luật thứ hai đếm PC_BT với ngưỡng 3 mã ⇒ số lệch lõi ⇒ đường từ chối, không trả danh sách
    from kpi_agent.backend import main
    real_build = ol.build
    monkeypatch.setattr(main.order_lines, "build", lambda kpi, lines, route, rules: real_build(kpi, lines, route, {**rules, "pc_bt_min_lines": 3}))
    refused = [body for body in (client.get("/api/order-lines", params={"employee_id": f"NV{n:02d}", "kpi": "PC_BT"}).json() for n in range(1, 15)) if "refused" in body]
    assert refused and all("chưa đối chiếu được với bảng thưởng" in body["refused"] and "rows" not in body for body in refused)



@needs_data
def test_runtime_gate_checks_lppc_numerator_and_denominator(client, monkeypatch):
    # Bậc 2: LPPC so cả tử (số mã) lẫn mẫu (số đơn) với lõi. Giả lập bản luật thứ hai đếm thêm 1 mã ở một đơn ⇒ từ chối, không trả danh sách
    from kpi_agent.backend import main
    real_build = ol.build
    def drifted(kpi, lines, route, rules):
        result = real_build(kpi, lines, route, rules)
        if kpi == "LPPC":
            result["summary"]["lines"] += 1
        return result
    monkeypatch.setattr(main.order_lines, "build", drifted)
    body = client.get("/api/order-lines", params={"employee_id": "NV05", "kpi": "LPPC"}).json()
    assert "chưa đối chiếu được với bảng thưởng" in body["refused"] and "rows" not in body
    # luật LPPC đổi sang "dòng tặng là mã" mà bản này chưa viết ⇒ lời từ chối rõ, không 500
    monkeypatch.setattr(main.order_lines, "build", lambda kpi, lines, route, rules: real_build(kpi, lines, route, {**rules, "lppc_lines_exclude_free_items": False}))
    body = client.get("/api/order-lines", params={"employee_id": "NV05", "kpi": "LPPC_Meat"}).json()
    assert "chưa dựng cho luật đang dùng" in body["refused"] and "rows" not in body


@needs_data
@pytest.mark.parametrize("params, expected", [
    ({"employee_id": "NV99", "kpi": "PC_BT"}, "không có NV99"), ({"kpi": "PC_BT"}, "mã nhân viên không hợp lệ"), ({"employee_id": "../../etc", "kpi": "PC_BT"}, "không hợp lệ"),
    ({"employee_id": "NV05"}, "thiếu tên KPI"), ({"employee_id": "NV05", "kpi": "KHONG_CO"}, "không có"),   # "KPI None" sửa mốc 7
])
def test_bad_requests_are_refused_without_rows_or_paths(client, params, expected):
    response = client.get("/api/order-lines", params=params)
    body = response.json()
    assert response.status_code == 200 and expected in body["refused"] and "rows" not in body
    assert "/home" not in response.text and "Errno" not in response.text and "Traceback" not in response.text


@needs_data
def test_route_is_read_only_page_only_and_not_on_the_view_gate_list(client):
    from kpi_agent.agent import backend_client
    from kpi_agent.backend import main
    from kpi_agent.scripts.xuat_duong_get import get_routes
    assert [client.request(method, "/api/order-lines").status_code for method in ("POST", "PUT", "DELETE")] == [405, 405, 405]
    assert not "/api/order-lines".startswith(main.SYSTEM_ONLY_PREFIXES)                                   # KPI_SYSTEM_ONLY ⇒ 404 như mọi đường của trang
    assert "/api/order-lines" not in Path(backend_client.__file__).read_text(encoding="utf-8")            # agent không gọi ⇒ tên cửa hàng không vào chữ gửi mô hình
    assert "/api/order-lines" not in {entry["path"] for entry in get_routes()}                            # chưa cho qua cổng xem ở bậc này


def test_route_filter_follows_mcp_filter_kpis():
    # Mốc 7 N1 (16/11): bản luật thứ hai lọc tuyến ĐÚNG như lõi — KPI không có trong `mcp_filter_kpis` tính cả cửa hàng ngoài tuyến (K9)
    rules = {**RULES, "mcp_filter_kpis": ["ASO_ALL", "ASO_Focus", "ASO_Focus_2", "LPPC_Meat"]}
    outside = _order("X1", outlet="K9", skus=4)
    assert ol.build("PC_BT", outside, ROUTE, rules)["counted_value"] == 1
    assert ol.build("PC_BT", outside, ROUTE, RULES)["counted_value"] == 0
    assert ol.build("ASO_ALL", outside, ROUTE, rules)["counted_value"] == 0


def test_late_invoice_lines_count_only_for_late_invoice_kpis():
    # Mốc 7 N2 (16/11, trang sếp là chuẩn): dòng trễ CÓ ngày xuất (`_late`) tính ở KPI trong `late_invoice_kpis`, như lõi; KPI khác vẫn loại;
    # dòng trễ KHÔNG có ngày xuất (`_late` False) luôn loại — ca xấu
    rules = {**RULES, "late_invoice_kpis": ["Turnover", "PC_BT"]}
    late = lambda line, has_date=True: {**line, "_drop": ol.LATE_INVOICE, "_late": has_date}
    lines = [late(_line(order="L1", sales=300.0)), late(_line(order="L2", sales=500.0), has_date=False)]
    turnover = ol.build("Turnover", lines, ROUTE, rules)
    assert turnover["counted_value"] == 300.0 and _row(turnover, "L1")["counted"] and _row(turnover, "L2")["reasons"] == [ol.LATE_INVOICE]
    pc_on = ol.build("PC_ON", [late(_line(order="L3", channel=ON))], ROUTE, rules)
    assert pc_on["counted_value"] == 0 and _row(pc_on, "L3")["reasons"] == [ol.LATE_INVOICE]
    assert ol.build("Turnover", lines, ROUTE, RULES)["counted_value"] == 0.0     # không có khoá ⇒ như cũ (bỏ mọi dòng trễ)


def test_pc_bt_skips_beer_like_core_when_rules_say_so():
    # Mốc 7 N3 (16/11): PC_BT ≥ 1 mã, bỏ Bia như trang — đơn chỉ có Bia nhận lý do riêng (ca xấu); 2 đơn cùng ngày cùng cửa hàng ⇒ 1
    rules = {**RULES, "pc_bt_min_lines": 1, "pc_bt_excluded_subdiv": ["Beer"]}
    lines = ([_line(order="B1", sku="BB", sub="Beer")] + [_line(order="M1", outlet="K2", sku="BB", sub="Beer"), _line(order="M1", outlet="K2", sku="S1")]
             + [_line(order="D1", sku="S1", day=D2), _line(order="D2", sku="S2", day=D2)])
    result = ol.build("PC_BT", lines, ROUTE, rules)
    assert result["counted_value"] == 2 and _row(result, "M1")["counted"] and _row(result, "D1")["counted"]
    assert _row(result, "B1")["reasons"] == [ol.NO_MOQ_LINE_EXCEPT.format(what="Bia")] and _row(result, "D2")["reasons"] == [ol.SAME_DAY]
    assert ol.build("PC_BT", lines, ROUTE, {**RULES, "pc_bt_min_lines": 1})["counted_value"] == 3     # thiếu khoá ⇒ Bia vẫn là mã như cũ
