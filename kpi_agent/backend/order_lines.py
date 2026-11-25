"""Đơn hàng của MỘT nhân viên cho MỘT chỉ tiêu: đơn / cửa hàng nào được tính, cái nào không và VÌ SAO (bậc 1).

VÌ SAO CÓ FILE NÀY: lõi (`core/load_raw.py` + `core/compute_kpi.py`) lọc bỏ rồi chỉ ĐẾM — không giữ dòng bị loại, nên không trả lời được câu khiếu nại
"đơn nào của tôi được tính". File này là BẢN LUẬT THỨ HAI đi song song lõi: đi lại đúng thứ tự luật, mỗi bước loại ghi một lý do. Nằm ngoài `core/`
⇒ không đổi dấu vân tay bản tính / "Đã soát".
PHẢI KHỚP LÕI: với mỗi (nhân viên, chỉ tiêu) số "được tính" ở đây phải BẰNG số thực hiện của lõi — `kpi_agent/tests/test_order_lines.py`
(`test_real_3110_every_employee_matches_core`, 14 NV × các chỉ tiêu đã làm) + rào lúc chạy ở `backend/main.py` (lệch ⇒ từ chối, không hiện danh sách).
ĐỔI LUẬT Ở `core/` (bộ lọc dòng, ngưỡng số mã, cách gộp đơn cùng ngày, lọc tuyến, hàng tặng) ⇒ SỬA Ở ĐÂY CÙNG LÚC; quên thì test trên đỏ.

Bậc 1: Turnover, PC_BT, PC_ON (đơn vị dòng = một ĐƠN) và ASO_ALL (đơn vị dòng = một CỬA HÀNG). Bậc 2 (13/11): + LPPC, LPPC_Meat (một ĐƠN),
ASO_Focus, ASO_Focus_2 (một CỬA HÀNG) ⇒ đủ 8 KPI. Chỉ tiêu khác ⇒ `SUPPORTED` không có ⇒ nơi gọi từ chối.
Chỉ cho TRANG xem (có tên cửa hàng) — không vào công cụ / chữ gửi mô hình."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from kpi_agent.core.compute_kpi import OUTLET_KPIS
from kpi_agent.core.load_raw import OFF, ON, _day, _exclude_reasons, _read_rows, focus_spec, in_focus, lppc_subdivs

# Bậc 2: + LPPC, LPPC_Meat (dòng = một ĐƠN; số của lõi là TỶ LỆ line ÷ đơn) và ASO_Focus, ASO_Focus_2 (dòng = một CỬA HÀNG) ⇒ đủ 8 KPI
SUPPORTED = ("Turnover", "PC_BT", "LPPC", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat")
RATIO_KPIS = ("LPPC", "LPPC_Meat")
EXTRA_COLUMNS = ("OUTLET NAME", "ADDRESS")
# Lý do — chữ cố định, lời thường, kèm mục công văn. Thứ tự trong `DROP_ORDER` = thứ tự lõi loại dòng (`read_raw_lines`).
AFTER_CUTOFF = "đơn sau ngày chốt số"
BAD_STATUS = "trạng thái đơn không được tính (chỉ tính đơn đã xuất hoá đơn / đã giao)"
LATE_INVOICE = "hoá đơn xuất sau 22:00 ngày 02 tháng sau (công văn mục 6.1)"
# Q9: tách khỏi LATE_INVOICE — dòng CHƯA có ngày xuất (trạng thái cần ngày xuất theo luật kỳ) khác dòng xuất trễ
NO_INVOICE_DATE = "chưa có ngày xuất hoá đơn (trạng thái này cần ngày xuất — công văn mục 6.1)"
# khoá lý do của lõi (`load_raw.EXCLUDE_REASONS`) ⇒ chữ của màn này; None = lõi giữ dòng
DROP_LABEL = {None: None, "after_cutoff": AFTER_CUTOFF, "status_not_counted": BAD_STATUS, "no_invoice_date": NO_INVOICE_DATE, "invoice_late": LATE_INVOICE}
OUTSIDE_ROUTE = "cửa hàng không thuộc tuyến của nhân viên (công văn mục 6.2)"
WRONG_CHANNEL = "không phải kênh của chỉ tiêu này"
NO_MOQ_LINE = "không mã hàng nào đủ số lượng mua tối thiểu (công văn mục 8.1; dòng hàng tặng không tính là mã)"
TOO_FEW_LINES = "chưa đủ {n} mã hàng đủ số lượng mua tối thiểu trong một đơn (công văn mục 2.2)"
# Mốc 7 N3 (16/11): PC_BT bỏ mã ngành Bia như trang theo dõi của đội (luật `pc_bt_excluded_subdiv`) — đơn chỉ có Bia đạt MOQ nhận lý do này
NO_MOQ_LINE_EXCEPT = "không mã hàng nào ngoài ngành {what} đủ số lượng mua tối thiểu (cách đếm của trang theo dõi của đội; dòng hàng tặng không tính là mã)"
SUBDIV_LABEL = {"Beer": "Bia", "Processed Meats": "Thịt chế biến"}
SAME_DAY = "đã tính 1 đơn khác cùng ngày ở cùng cửa hàng (tối đa 1 đơn / ngày / cửa hàng)"
# ASO_ALL / ASO_Focus*: điều kiện thật là "doanh số ở các dòng có cờ đủ số lượng > 0" — dòng tặng có cờ VẪN thuộc nhóm (góp ý C bậc 1 minor 2)
NO_MOQ_SALES = "không có doanh số ở mã hàng đủ số lượng mua tối thiểu (công văn mục 8.1)"
NO_FOCUS = "không mua {what} đủ số lượng mua tối thiểu trong tháng"
# Tên gọi tắt trên màn của nhóm hàng (như SUBDIV_LABEL): nhóm không có ở đây ⇒ hiện nguyên tên trong luật kỳ
GROUP_LABEL = {"Trà Búp Non Tea 365": "Trà 365"}
NO_LPPC_LINE = "không có mã hàng đủ số lượng mua tối thiểu ngoài ngành Thịt chế biến và Bia (công văn mục 2.3; dòng hàng tặng không tính là mã)"
NO_MEAT_LINE = "không có mã Thịt chế biến đủ số lượng mua tối thiểu (công văn mục 2.8; dòng hàng tặng không tính là mã)"
TOO_FEW_LPPC = "chưa đủ {n} mã hàng đủ số lượng mua tối thiểu trong một đơn (công văn mục 2.3)"
SAME_DAY_FEWER = "cùng ngày ở cùng cửa hàng đã tính 1 đơn khác có nhiều mã hơn (tối đa 1 đơn / ngày / cửa hàng; số mã lấy theo đơn nhiều mã nhất)"
NOTE = ("Được tính / không được tính ở đây là theo từng đơn hoặc từng cửa hàng. Tiền thưởng thì tính theo mốc của cả tháng — "
        "màn này không nói một đơn đáng bao nhiêu tiền.")
# Góp ý C bậc 1 minor 3: đơn nào trong nhóm cùng ngày được ✓ là QUY ƯỚC của màn (lõi chỉ đếm nhóm) — nói rõ trên màn
SAME_DAY_NOTE = ("Nhiều đơn đủ điều kiện cùng ngày ở cùng cửa hàng chỉ tính là 1: bảng thưởng chỉ đếm số ngày–cửa hàng, nên việc đánh dấu ✓ cho đơn nào "
                 "là quy ước của màn này: LPPC / LPPC Meat đánh dấu đơn nhiều mã nhất (đúng cách bảng thưởng lấy số mã), chỉ tiêu khác đánh dấu đơn gặp trước trong file.")


def read_all_lines(path: str | Path, cutoff: date, period: str, invoice_required_status: tuple[str, ...] | None = None,
                   mapping: dict | None = None, *, period_rules: dict) -> list[dict]:
    """MỌI dòng RAW có mã nhân viên, mỗi dòng thêm `_drop` = lý do lõi loại dòng đó (None = lõi giữ). Lõi chỉ trả phần được giữ nên phải đọc lại ở đây.
    Bậc 2 bước 2.5 (17/11): đọc + xét lý do bằng CHÍNH hàm của lõi (`_read_rows` qua bảng ghép, `_exclude_reasons` theo luật kỳ
    `invoice_required_status`) — trước đây file này tự đọc sheet / tự lọc, nên file đã ghép cột hoặc kỳ có Shipped chưa xuất HĐ ra tập dòng khác lõi."""
    lines, _file_total = _read_rows(path, period, EXTRA_COLUMNS, mapping)
    reasons = _exclude_reasons(lines, cutoff, period, invoice_required_status, period_rules)
    # `_late` (mốc 7 N2): dòng trễ CÓ ngày xuất — KPI trong `late_invoice_kpis` vẫn tính (như lõi `read_raw_lines(keep_late=True)`), xem `build`
    return [{**line, "_drop": DROP_LABEL[reason], "_late": reason == "invoice_late"} for line, reason in zip(lines, reasons)]


def _text(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _orders(lines: list[dict]) -> dict:
    """{(số đơn, cửa hàng, ngày): [dòng]} theo thứ tự gặp trong file — cùng khoá gộp đơn với lõi (`_lines_by_order`, đã cố định một nhân viên)."""
    grouped = defaultdict(list)
    for line in lines:
        grouped[(line["ORDER NUM"], line["OUTLET CODE"], _day(line["ORDER DATE"]))].append(line)
    return grouped


def _order_row(key, lines: list[dict], counted: bool, reasons: list[str], **extra) -> dict:
    order, outlet, day = key
    return {"order_num": str(order), "day": day.isoformat(), "outlet_code": outlet, "outlet_name": _text(lines[0]["OUTLET NAME"]),
            "channel": {OFF: "Off", ON: "On"}.get(lines[0]["L1"], lines[0]["L1"]), "line_count": len(lines),
            "sales": float(sum(Decimal(str(line["Sales Before Discount"])) for line in lines if isinstance(line["Sales Before Discount"], (int, float)))),
            "counted": counted, "reasons": reasons, **extra}


def _dropped(lines: list[dict]) -> list[str]:
    return list(dict.fromkeys(line["_drop"] for line in lines if line["_drop"]))


def turnover(lines: list[dict], route: dict) -> tuple[list[dict], float]:
    """Doanh số: mọi dòng lõi giữ, ở cửa hàng thuộc tuyến — KHÔNG xét cờ đủ số lượng. Trả (dòng theo đơn, tổng doanh số được tính)."""
    rows, total = [], Decimal(0)
    for key, order_lines in _orders(lines).items():
        kept = [line for line in order_lines if line["_drop"] is None]
        in_route = route is None or key[1] in route
        counted_sales = sum((Decimal(str(line["Sales Before Discount"])) for line in kept), Decimal(0)) if in_route else Decimal(0)
        total += counted_sales
        reasons = _dropped(order_lines) + ([OUTSIDE_ROUTE] if kept and not in_route else [])
        rows.append(_order_row(key, order_lines, bool(kept) and in_route, reasons, counted_sales=float(counted_sales)))
    return rows, float(total)


def _pc(lines: list[dict], route: dict, channel: str, min_lines: int, free_is_line: bool, excluded_subdiv: tuple = ()) -> tuple[list[dict], int]:
    """Số đơn (PC) của một kênh: mã = MÃ HÀNG KHÁC NHAU trong đơn có cờ đủ số lượng (hàng tặng tính hay không theo luật); đơn phải có ≥ `min_lines` mã,
    cửa hàng thuộc tuyến; RỒI mỗi (cửa hàng, ngày) chỉ tính 1 đơn. Thứ tự này là thứ tự của lõi (`build_blocks` ⇒ `_kept_rows` ⇒ ngưỡng ⇒ `_pc_and_lines`)."""
    rows, taken = [], set()
    for key, order_lines in _orders(lines).items():
        kept = [line for line in order_lines if line["_drop"] is None]
        in_channel = [line for line in kept if line["L1"] == channel]
        skus = {line["SKU CODE"] for line in in_channel if line["MOQ_FLAG"] == "Y" and (free_is_line or line["FREE ITEM FLAG"] != "Y")
                and line["SUB DIV"] not in excluded_subdiv}
        reasons = _dropped(order_lines)
        counted = False
        if kept and not in_channel:
            reasons.append(WRONG_CHANNEL)
        elif in_channel:
            # Nêu ĐỦ mọi điều kiện chưa đạt (góp ý C bậc 1 minor 1: chỉ nêu lý do đầu ⇒ người khiếu nại sửa một lý do vẫn không được tính)
            no_line = (NO_MOQ_LINE_EXCEPT.format(what=", ".join(SUBDIV_LABEL.get(name, name) for name in excluded_subdiv)) if excluded_subdiv
                       else NO_MOQ_LINE)
            fails = [no_line] if not skus else [TOO_FEW_LINES.format(n=min_lines)] if len(skus) < min_lines else []
            fails += [OUTSIDE_ROUTE] if route is not None and key[1] not in route else []
            if not fails and (key[1], key[2]) in taken:
                fails.append(SAME_DAY)
            elif not fails:
                counted = True
                taken.add((key[1], key[2]))
            reasons += fails
        rows.append(_order_row(key, order_lines, counted, reasons, moq_skus=len(skus)))
    return rows, len(taken)


def _lppc(lines: list[dict], route: dict, in_group, min_lines: int, line_mode: str, no_line: str, too_few: str) -> tuple[list[dict], int, int]:
    """LPPC / LPPC_Meat: mã = MÃ HÀNG KHÁC NHAU có cờ đủ số lượng, KHÔNG tính dòng tặng (luật `lppc_lines_exclude_free_items`), thuộc nhóm ngành
    `in_group(SUB DIV)`; đơn đạt khi ≥ `min_lines` mã và cửa hàng thuộc tuyến; RỒI mỗi (cửa hàng, ngày) 1 đơn. Số mã của nhóm cùng ngày: "one" = đơn
    nhiều mã nhất (luật `lppc_same_day_lines`, chọn theo trang) · "all" = cộng mọi đơn đạt. Thứ tự như lõi (`build_blocks` ⇒ `_kept_rows` ⇒
    `_orders_with_min_lines` ⇒ `_pc_and_lines`). Trả (dòng theo đơn, tổng mã được tính, số đơn được tính)."""
    rows, qualified = [], defaultdict(list)          # (cửa hàng, ngày) ⇒ [chỉ số dòng đạt]
    for key, order_lines in _orders(lines).items():
        kept = [line for line in order_lines if line["_drop"] is None]
        skus = {line["SKU CODE"] for line in kept if line["MOQ_FLAG"] == "Y" and line["FREE ITEM FLAG"] != "Y" and in_group(line["SUB DIV"])}
        reasons = _dropped(order_lines)
        fails = []
        if kept:
            fails = [no_line] if not skus else [too_few.format(n=min_lines)] if len(skus) < min_lines else []
            fails += [OUTSIDE_ROUTE] if route is not None and key[1] not in route else []
            if not fails:
                qualified[(key[1], key[2])].append(len(rows))
        rows.append(_order_row(key, order_lines, False, reasons + fails, moq_skus=len(skus)))
    total_lines = 0
    for indexes in qualified.values():
        # đơn được ✓ trong nhóm cùng ngày: đơn nhiều mã nhất (bằng nhau ⇒ đơn gặp trước) — quy ước của màn, ghi ở SAME_DAY_NOTE
        best = max(indexes, key=lambda index: (rows[index]["moq_skus"], -index))
        group_lines = rows[best]["moq_skus"] if line_mode == "one" else sum(rows[index]["moq_skus"] for index in indexes)
        total_lines += group_lines
        for index in indexes:
            if index == best:
                rows[index].update(counted=True, counted_lines=group_lines)
            elif line_mode == "one":
                rows[index]["reasons"].append(SAME_DAY_FEWER)
            else:   # "all": mã của đơn khác cùng ngày vẫn cộng vào, chỉ không thành đơn riêng
                rows[index]["reasons"].append(SAME_DAY)
    return rows, total_lines, len(qualified)


def aso_all(lines: list[dict], route: dict, pick=None, no_sales: str = NO_MOQ_SALES, by_sku: bool = False) -> tuple[list[dict], int]:
    """Bao phủ: cửa hàng thuộc tuyến có doanh số > 0 ở các dòng có cờ đủ số lượng (dòng hàng tặng CÓ cờ vẫn thuộc nhóm này — như lõi). Dòng = một cửa hàng.
    Bậc 2: `pick(line)` chỉ giữ dòng của nhóm hàng (ASO_Focus = Trà 365, ASO_Focus_2 = mã Homey 2,9 kg); `by_sku` = lõi cộng doanh số THEO MÃ HÀNG rồi
    xét > 0 (`build_blocks` khối ASO_Focus_2 gom theo cả SKU CODE) — cửa hàng được tính khi có ít nhất một mã có tổng > 0."""
    by_outlet = defaultdict(list)
    for line in lines:
        by_outlet[line["OUTLET CODE"]].append(line)
    rows, count = [], 0
    for outlet, outlet_lines in by_outlet.items():
        kept = [line for line in outlet_lines if line["_drop"] is None]
        group = [line for line in kept if line["MOQ_FLAG"] == "Y" and (pick is None or pick(line))]
        per_sku = defaultdict(Decimal)
        for line in group:
            per_sku[line["SKU CODE"] if by_sku else None] += Decimal(str(line["Sales Before Discount"]))
        moq_sales = sum(per_sku.values(), Decimal(0))
        has_sales = any(total > 0 for total in per_sku.values())
        reasons = _dropped(outlet_lines) if not kept else []
        counted = False
        if kept:
            # Nêu ĐỦ mọi điều kiện chưa đạt (góp ý C bậc 1 minor 1)
            fails = ([] if has_sales else [no_sales]) + ([] if route is None or outlet in route else [OUTSIDE_ROUTE])
            reasons += fails
            if not fails:
                counted = True
                count += 1
        rows.append({"outlet_code": outlet, "outlet_name": _text(outlet_lines[0]["OUTLET NAME"]), "address": _text(outlet_lines[0]["ADDRESS"]),
                     "order_count": len({line["ORDER NUM"] for line in outlet_lines}), "moq_sales": float(moq_sales), "counted": counted, "reasons": reasons})
    return rows, count


def build(kpi: str, lines: list[dict], route: dict, assumptions: dict) -> dict:
    """Danh sách + số "được tính" của một nhân viên (`lines` = mọi dòng của người đó từ `read_all_lines`; `route` = cửa hàng thuộc tuyến của người đó).
    `counted_value` cùng đơn vị với số thực hiện của lõi: Turnover = đồng; PC_BT / PC_ON = số đơn; ASO_ALL = số cửa hàng."""
    free_is_line = not assumptions.get("moq_lines_exclude_free_items", False)
    # Mốc 7 N2 (16/11): KPI trong `late_invoice_kpis` (Turnover, PC_BT — trang sếp cộng) tính cả dòng hoá đơn trễ có ngày xuất, ĐÚNG như lõi
    # (`load_raw.build_blocks(late_kpis)`); KPI khác vẫn loại với lý do LATE_INVOICE. Dòng không có ngày xuất luôn loại.
    if kpi in assumptions.get("late_invoice_kpis", ()):
        lines = [{**line, "_drop": None} if line.get("_late") else line for line in lines]
    # Mốc 7 N1 (16/11): lọc tuyến ĐÚNG như lõi (`compute_kpi.compute_actuals`) — chỉ KPI có trong `mcp_filter_kpis` (thiếu khoá ⇒ OUTLET_KPIS);
    # `route=None` = KPI này không lọc tuyến (trang sếp tính cả cửa hàng ngoài tuyến ở Turnover / PC_BT / LPPC / PC_ON)
    if not assumptions.get("exclude_outlets_outside_mcp", False) or kpi not in assumptions.get("mcp_filter_kpis", OUTLET_KPIS):
        route = None
    extra = {}
    if kpi == "Turnover":
        rows, value = turnover(lines, route)
    elif kpi == "PC_BT":
        rows, value = _pc(lines, route, OFF, assumptions["pc_bt_min_lines"], free_is_line, tuple(assumptions.get("pc_bt_excluded_subdiv", ())))
    elif kpi == "PC_ON":
        rows, value = _pc(lines, route, ON, 1, free_is_line)
    elif kpi == "ASO_ALL":
        rows, value = aso_all(lines, route)
    elif kpi in RATIO_KPIS:
        if not assumptions.get("lppc_lines_exclude_free_items", False):
            # bản luật thứ hai chỉ viết cho luật đang dùng (dòng tặng không là mã) — luật đổi mà quên sửa ở đây ⇒ dừng, không hiện danh sách sai
            raise ValueError("danh sách đơn LPPC chỉ viết cho luật 'dòng tặng không là mã' — luật đang khác")
        meat = kpi == "LPPC_Meat"
        excluded, meat_subdiv = lppc_subdivs(assumptions)          # bậc 3a: ngành theo luật kỳ, như lõi (`load_raw.build_blocks`)
        rows, lines_total, orders = _lppc(lines, route, (lambda sub: sub == meat_subdiv) if meat else (lambda sub: sub not in excluded),
                                          1 if meat else assumptions["lppc_min_lines"], assumptions.get("lppc_same_day_lines", "all"),
                                          NO_MEAT_LINE if meat else NO_LPPC_LINE, TOO_FEW_LPPC)
        value = lines_total / orders if orders else 0.0          # cùng phép chia với lõi (`_ratio`)
        extra = {"lines": lines_total, "orders": orders}
    elif kpi == "ASO_Focus":
        # bậc 3a: nhóm / mã + tên gọi theo luật kỳ (`load_raw.focus_spec`) — cùng phép chọn dòng với lõi
        spec = focus_spec(assumptions, kpi)
        rows, value = aso_all(lines, route, lambda line: in_focus(line, spec), NO_FOCUS.format(what=GROUP_LABEL.get(spec["label"], spec["label"])))
    elif kpi == "ASO_Focus_2":
        # theo mã: KHÔNG lọc nhóm trước (trước 3a lọc nhóm Homey rồi mới lọc mã ⇒ mã thuộc nhóm khác ra 0 lặng lẽ)
        spec = focus_spec(assumptions, kpi)
        rows, value = aso_all(lines, route, lambda line: in_focus(line, spec), NO_FOCUS.format(what=spec["label"]), by_sku=True)
    else:
        raise ValueError(f"chưa có danh sách đơn cho chỉ tiêu {kpi}")
    by_reason = defaultdict(int)
    for row in rows:
        for reason in row["reasons"]:
            by_reason[reason] += 1
    unit = "outlet" if kpi.startswith("ASO") else "order"
    return {"unit": unit, "rows": rows, "counted_value": value,
            "summary": {"rows": len(rows), "counted": sum(row["counted"] for row in rows), "not_counted": sum(not row["counted"] for row in rows),
                        "by_reason": dict(by_reason), **extra}}


def matches_core(kpi: str, counted_value: float, core_actual: float) -> bool:
    """Số của bản luật thứ hai có bằng số thực hiện của lõi không. Doanh số là tổng số thực (cộng theo thứ tự khác nhau) ⇒ cho lệch dưới 1 xu;
    LPPC = cùng phép chia hai số nguyên với lõi ⇒ cho lệch số thực rất nhỏ; số đếm phải bằng đúng."""
    if kpi == "Turnover":
        return abs(counted_value - core_actual) < 0.01
    if kpi in RATIO_KPIS:
        return abs(counted_value - core_actual) < 1e-9
    return counted_value == core_actual
