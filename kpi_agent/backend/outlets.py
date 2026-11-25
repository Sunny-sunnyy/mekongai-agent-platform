"""Điểm bán thuộc tuyến (tab "Chi tiết") + danh sách Trà 365 (tab "Trà 365") — /2026,: hai tab còn thiếu so với trang theo dõi của đội.

Mỗi điểm bán trong file tuyến của một nhân viên ⇒ một dòng: doanh số tháng, đạt / chưa đạt MOQ, Trà 365 (thùng + tiền), Homey, kênh On, nước mắm cao cấp.
CHỈ ĐẾM, không có tiền thưởng nào (tiền Trà 365 theo công văn 419 chưa dựng) ⇒ không đụng bảng thưởng 8 KPI / Thưởng thêm.
Vì sao ngoài `core/` (như `spice_loyalty.py`): không quyết số 8 KPI ⇒ thêm / sửa không được làm mất "Đã soát" của bảng thưởng.
Dòng trả về CÓ tên + địa chỉ cửa hàng (dữ liệu khách) ⇒ chỉ cho trang xem; KHÔNG đưa vào công cụ / chữ gửi mô hình.
Số thùng và tiền cộng bằng số thập phân chính xác (Decimal) — ngưỡng "từ 1 thùng", "từ 5 thùng" không được lệch vì sai số số thực."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from kpi_agent.core.load_raw import OFF, ON, _exclude_reasons, _read_rows, in_focus

AFTER = "Sales After Discount"
EXTRA_COLUMNS = ("TOTAL CASES", "OUTLET NAME", "ADDRESS", AFTER)   # cột RAW thêm ngoài các cột 8 KPI đã đọc
TEA_MIN_CASES, TEA_BIG_CASES = 1, 5                         # trang của đội: "≥ 1 thùng cộng dồn từ đầu tháng", "khách từ 5 thùng"
CHANNEL_LABEL = {OFF: "Off", ON: "On"}
# Tên các ô tiền / số cộng dồn của một điểm bán (mọi ô bắt đầu từ 0)
# `page_*` = tiền THEO CÁCH TRANG THEO DÕI CỦA ĐỘI (dòng 13 bài 15b — đã đếm lại 13/11 bằng script riêng, khớp 2.501 / 2.501 điểm ở cả 3 ô):
# doanh số + nước mắm cao cấp = sau chiết khấu; Trà 365 = sau chiết khấu, CHỈ dòng đạt MOQ; cả ba CỘNG CẢ hoá đơn xuất trễ (công văn 6.1 loại; từ mốc 7 N2 KPI 1 + PC_BT cộng dòng trễ có ngày xuất, KPI khác vẫn bỏ).
SUMS = ("sales", "moq_sales", "tea_cases", "tea_sales", "homey_moq_sales", "on_sales", "premium_fish_sauce_sales",
        "page_sales", "page_tea_sales", "page_premium_fish_sauce_sales")


def _number(line: dict, column: str) -> Decimal:
    value = line[column]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"RAW dòng Excel {line['_row']}: ô '{column}' trống hoặc không phải số")
    return Decimal(str(value))


def _text(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def visit_days(text) -> list[str]:
    """File tuyến ghi "Thứ 2,Thứ 5," ⇒ ["Thứ 2", "Thứ 5"]."""
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def read_late_lines(path, cutoff, period: str, invoice_required_status: tuple[str, ...] | None = None, mapping: dict | None = None, *,
                    period_rules: dict) -> list[dict]:
    """Dòng RAW xuất hoá đơn TRỄ (sau 22:00 ngày 02 tháng sau — công văn 6.1; hoặc chưa có ngày xuất mà trạng thái cần ngày xuất) của đơn được tính,
    ngày đơn ≤ ngày chốt. `read_raw_lines` mặc định bỏ các dòng này (KPI 1 + PC_BT cộng dòng có ngày xuất qua `keep_late` — mốc 7 N2); trang theo dõi
    của đội cộng vào tiền trên thẻ Chi tiết ⇒ đọc riêng ở đây, KHÔNG đổi `core/`.
    Bậc 2 bước 2.5 (17/11): đọc + xét lý do bằng CHÍNH hàm của lõi (qua bảng ghép, theo luật kỳ) ⇒ "trễ" ở đây = đúng hai lý do trễ của lõi."""
    lines, _file_total = _read_rows(path, period, ("OUTLET NAME", "ADDRESS", AFTER), mapping)
    reasons = _exclude_reasons(lines, cutoff, period, invoice_required_status, period_rules)
    return [line for line, reason in zip(lines, reasons) if reason in ("no_invoice_date", "invoice_late")]


def _add_page_money(cell: dict, line: dict, fish: set[str], tea: dict) -> None:
    """Cộng một dòng đơn vào 3 ô `page_*` (cách trang theo dõi của đội)."""
    after = _number(line, AFTER)
    cell["page_sales"] += after
    if in_focus(line, tea) and line["MOQ_FLAG"] == "Y":
        cell["page_tea_sales"] += after
    if line["DMS OBJ"] in fish:
        cell["page_premium_fish_sauce_sales"] += after


def compute(lines: list[dict], mcp: dict, to_masked: dict[str, str], focus: dict, fish_sauce_groups: list[str],
            late_lines: list[dict] = (), late_in_kpi1: bool = False) -> list[dict]:
    """Một dòng cho MỖI cặp (nhân viên, điểm bán) trong file tuyến — kể cả điểm không có đơn nào trong tháng (mọi số = 0, không có tên).

    `lines` = dòng RAW đã lọc như 8 KPI (`read_raw_lines` + cột `EXTRA_COLUMNS`); đơn của điểm ngoài tuyến của chính NV đó không tính (công văn 6.2).
    `focus` (bậc 3a): nhóm / mã của KPI 5 và KPI 6 theo luật kỳ — `load_raw.period_rules(...)["focus"]`; cột "tea_*" đi theo KPI 5, cột
    "homey_*" đi theo KPI 6 (tên cột giữ theo tháng 10 — trang đang đọc các tên này).
    Nhân viên trong file tuyến không thuộc đội / nhóm nước mắm không có trong RAW ⇒ dừng (không thành 0 lặng lẽ).
    `late_lines` (`read_late_lines`): dòng hoá đơn trễ — CHỈ cộng vào 3 ô `page_*`, không đổi số đếm / ô nào khác (kể cả "có đơn" và đạt MOQ).
    `late_in_kpi1` (mốc 7 N2, 16/11 — luật `late_invoice_kpis` có Turnover): dòng trễ CÓ ngày xuất cộng thêm vào ô `sales` ("Doanh số tính KPI 1")
    như lõi; dòng chưa có ngày xuất không. `lines` không có dòng trễ (`read_raw_lines` mặc định) ⇒ không cộng hai lần."""
    strangers = sorted(set(mcp) - set(to_masked))
    if strangers:
        raise ValueError(f"file tuyến có {len(strangers)} nhân viên không thuộc đội — không gom được")
    fish, tea, homey = set(fish_sauce_groups), focus["ASO_Focus"], focus["ASO_Focus_2"]
    missing = sorted(fish - {line["DMS OBJ"] for line in lines})
    if missing:
        raise ValueError(f"nhóm nước mắm cao cấp không có trong RAW: {', '.join(missing)}")
    sums = defaultdict(lambda: dict.fromkeys(SUMS, Decimal(0)))
    names: dict[tuple, tuple] = {}
    for line in lines:
        key = (line["SM CODE"], line["OUTLET CODE"])
        if key[1] not in mcp.get(key[0], {}):
            continue
        cell, sales, moq = sums[key], _number(line, "Sales Before Discount"), line["MOQ_FLAG"] == "Y"
        names.setdefault(key, (_text(line["OUTLET NAME"]), _text(line["ADDRESS"])))
        cell["sales"] += sales
        if moq:
            cell["moq_sales"] += sales
        if in_focus(line, tea):
            # Số thùng: MỌI dòng Trà 365 (kể cả dòng chưa đủ số lượng tối thiểu) — cách này ra đúng 461 khách / 49 khách của trang đội
            cell["tea_cases"] += _number(line, "TOTAL CASES")
            cell["tea_sales"] += sales
        if moq and in_focus(line, homey):
            cell["homey_moq_sales"] += sales
        if line["L1"] == ON:
            cell["on_sales"] += sales
        if line["DMS OBJ"] in fish:
            cell["premium_fish_sauce_sales"] += sales
        _add_page_money(cell, line, fish, tea)
    ordered = set(sums)   # "có đơn" = có dòng đúng hạn (nghĩa cũ, khớp 387 điểm "chưa có đơn" của trang) — trước khi cộng dòng trễ
    for line in late_lines:
        key = (line["SM CODE"], line["OUTLET CODE"])
        if key[1] in mcp.get(key[0], {}):
            names.setdefault(key, (_text(line["OUTLET NAME"]), _text(line["ADDRESS"])))
            _add_page_money(sums[key], line, fish, tea)
            if late_in_kpi1 and isinstance(line.get("INVOICED DATE"), datetime):
                sums[key]["sales"] += _number(line, "Sales Before Discount")
    rows = []
    for employee in sorted(mcp, key=to_masked.get):
        for outlet in sorted(mcp[employee]):
            cell, info = sums.get((employee, outlet)) or dict.fromkeys(SUMS, Decimal(0)), mcp[employee][outlet]
            name, address = names.get((employee, outlet), (None, None))
            rows.append({
                "outlet_code": outlet, "outlet_name": name, "address": address, "employee_id": to_masked[employee],
                "visit_days": visit_days(info.get("visit_day")), "channel": CHANNEL_LABEL.get(info.get("channel"), info.get("channel")),
                "has_orders": (employee, outlet) in ordered,
                "sales": float(cell["sales"]),
                "moq_reached": cell["moq_sales"] > 0,           # cùng cách đếm KPI 4: có hàng đạt số lượng tối thiểu, doanh số > 0
                "tea_cases": float(cell["tea_cases"]), "tea_sales": float(cell["tea_sales"]),
                "homey_reached": cell["homey_moq_sales"] > 0,   # cùng cách đếm KPI 6
                "on_sales": float(cell["on_sales"]), "premium_fish_sauce_sales": float(cell["premium_fish_sauce_sales"]),
                "page_sales": float(cell["page_sales"]), "page_tea_sales": float(cell["page_tea_sales"]),
                "page_premium_fish_sauce_sales": float(cell["page_premium_fish_sauce_sales"]),
            })
    return rows


def _by_employee(rows: list[dict], team: list[str], count) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["employee_id"]].append(row)
    return [{"employee_id": masked, **count(grouped[masked])} for masked in team]


def _outlet_counts(rows: list[dict]) -> dict:
    reached = sum(row["moq_reached"] for row in rows)
    return {"outlets": len(rows), "moq_reached": reached, "moq_not_reached": len(rows) - reached}


def _tea_counts(rows: list[dict]) -> dict:
    return {"customers": len(rows), "cases": float(sum(Decimal(str(row["tea_cases"])) for row in rows)),
            "customers_from_5": sum(row["tea_cases"] >= TEA_BIG_CASES for row in rows)}


def outlets_view(rows: list[dict], team: list[str], employee_id: str | None = None) -> dict:
    """Tab "Chi tiết": tổng + theo nhân viên (luôn cả đội) + danh sách điểm bán (cả đội, hoặc của một nhân viên)."""
    shown = [row for row in rows if employee_id in (None, row["employee_id"])]
    return {"summary": {**_outlet_counts(shown), "sales": float(sum(Decimal(str(row["sales"])) for row in shown)),
                        "page_sales": float(sum(Decimal(str(row["page_sales"])) for row in shown)),
                        "without_orders": sum(not row["has_orders"] for row in shown)},
            "by_employee": _by_employee(rows, team, _outlet_counts), "rows": shown}


def tea_view(rows: list[dict], team: list[str], employee_id: str | None = None) -> dict:
    """Tab "Trà 365": điểm bán thuộc tuyến mua từ 1 thùng Trà 365 cộng dồn trong tháng. Tổng thùng chỉ cộng các khách đó."""
    buyers = [row for row in rows if row["tea_cases"] >= TEA_MIN_CASES]
    shown = [row for row in buyers if employee_id in (None, row["employee_id"])]
    keep = ("outlet_code", "outlet_name", "employee_id", "visit_days", "channel", "tea_cases")
    return {"summary": _tea_counts(shown), "by_employee": _by_employee(buyers, team, _tea_counts),
            "rows": [{key: row[key] for key in keep} for row in sorted(shown, key=lambda row: (-row["tea_cases"], row["outlet_code"]))]}


ROUTE_SHEET, ROUTE_HEADER_ROW = "MCP", 7   # sheet danh sách tuyến trong sổ Tracking của đội: hàng tiêu đề thứ 7 (xem 13/11), bảng chi tiết nằm sau các bảng tổng hợp


def read_route_names(workbook_path) -> dict[str, tuple[str | None, str | None]]:
    """{mã điểm bán: (tên, địa chỉ)} từ sheet "MCP" của sổ Tracking — để điểm thuộc tuyến CHƯA có đơn trong tháng vẫn có tên (file đơn hàng không có
    dòng nào của điểm đó). Chỉ đọc 3 cột `Outlet_code`, `Outlet_name`, `Address`; thiếu sheet / cột ⇒ ValueError (bên gọi giữ "chưa có tên")."""
    from openpyxl import load_workbook
    wb = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        if ROUTE_SHEET not in wb.sheetnames:
            raise ValueError(f"sổ không có sheet '{ROUTE_SHEET}'")
        rows = wb[ROUTE_SHEET].iter_rows(min_row=ROUTE_HEADER_ROW, values_only=True)
        header = [str(value).strip() if value is not None else "" for value in next(rows)]
        missing = [name for name in ("Outlet_code", "Outlet_name", "Address") if name not in header]
        if missing:
            raise ValueError(f"sheet '{ROUTE_SHEET}' thiếu cột: {', '.join(missing)}")
        code_at, name_at, address_at = (header.index(name) for name in ("Outlet_code", "Outlet_name", "Address"))
        out = {}
        for values in rows:
            if len(values) > code_at and values[code_at] not in (None, ""):
                out.setdefault(str(values[code_at]).strip(), (_text(values[name_at]), _text(values[address_at])))
        return out
    finally:
        wb.close()


def fill_names(rows: list[dict], route_names: dict[str, tuple[str | None, str | None]]) -> int:
    """Điền tên + địa chỉ từ sổ tuyến cho dòng CHƯA có tên (điểm chưa có đơn). Dòng đã có tên từ file đơn hàng giữ nguyên. Trả số dòng đã điền."""
    filled = 0
    for row in rows:
        if row["outlet_name"] is None and route_names.get(row["outlet_code"], (None, None))[0]:
            row["outlet_name"], row["address"] = route_names[row["outlet_code"]]
            row["name_from_route_sheet"] = True
            filled += 1
    return filled
