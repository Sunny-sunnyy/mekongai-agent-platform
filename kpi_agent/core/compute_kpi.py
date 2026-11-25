"""Khối 3 — tính "thực hiện" 8 KPI từ các khối pivot của Rawdata (lớp "đơn / cửa hàng nào được tính").

Dữ liệu nhỏ nhất có được: mỗi đơn số mã đạt MOQ (cờ MOQ_FLAG=Y do hệ thống DMS gắn sẵn), mỗi cửa hàng doanh số.
Vì vậy ở đây chỉ ĐẾM theo luật công văn; 3 luật cần dòng đơn nằm ở pending_rules.py. Không làm tròn số ở đây.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from kpi_agent.core.load_raw import how_names



@dataclass
class KpiResult:
    kpi: str
    target: float
    actual: float
    numerator: float     # tử số (vd tổng line) — với KPI đếm thì = actual
    denominator: float   # mẫu số (vd số đơn) — với KPI đếm thì = 1
    how: str             # cách tính bằng lời, để giải thích cho giám sát

    @property
    def pct(self) -> float:
        return self.actual / self.target if self.target else 0.0


def compute_actuals(targets: dict, tracking: dict, rules: dict) -> dict[str, dict[str, KpiResult]]:
    """{mã NV: {kpi: KpiResult}} cho mọi NV có trong file chỉ tiêu (NV không có dòng nào ⇒ thực hiện 0)."""
    blocks, mcp, assumptions = tracking["blocks"], tracking["mcp"], rules["assumptions"]
    keep_outlet = _outlet_filter(mcp, assumptions["exclude_outlets_outside_mcp"])
    # Lọc tuyến cho Turnover / PC_BT / LPPC chỉ khi luật ghi rõ (QĐ-09 11/11, công văn 6.2 "Điều kiện: KH thuộc MCP của chính ĐDKD" áp cả Turnover, PC);
    # thiếu khoá ⇒ như trước: chỉ 5 KPI đếm cửa hàng lọc (luật Tracking không lọc gì)
    filtered = set(assumptions.get("mcp_filter_kpis", OUTLET_KPIS)) if assumptions["exclude_outlets_outside_mcp"] else set()
    # Ca xấu X1 (C, 11/11): MCP thiếu tuyến của một NV có chỉ tiêu ⇒ `mcp.get(sm, {})` loại LẶNG LẼ mọi cửa hàng NV đó (QĐ-09: cả Turnover về 0) ⇒ dừng
    no_route = sorted(code for code in targets["targets"] if not mcp.get(code)) if filtered else []
    if no_route:
        # Lời chỉ nêu SỐ NV, không nêu mã: lời lỗi của lõi đi thẳng ra lời trả / bản ghi lượt (backend/main.py `_extra_bonus`: "không chứa mã NV thật");
        # mã CHE (NV05…) cũng không nêu được — lõi chỉ có mã thật, bảng che nằm ở backend (C minor bản kế: nêu mã che nếu có sẵn — không có, giữ)
        raise ValueError(f"MCP không có tuyến của {len(no_route)} NV có chỉ tiêu — lọc tuyến sẽ loại mọi cửa hàng của họ; kiểm lại file MCP")
    rows_of = lambda kpi: _kept_rows(blocks[kpi]["rows"], kpi, keep_outlet) if kpi in filtered else blocks[kpi]["rows"]
    # Mốc 7 N1 (16/11): KPI đếm cửa hàng cũng chỉ lọc khi có trong `filtered` — trước đây ASO / PC_ON / LPPC_Meat LUÔN lọc. File luật thiếu khoá
    # ⇒ `filtered` = OUTLET_KPIS (có PC_ON) ⇒ không đổi; file luật RAW bỏ PC_ON khỏi khoá vì trang sếp tính cả cửa hàng ngoài tuyến ở PC_ON
    keep_for = lambda kpi: keep_outlet if kpi in filtered else (lambda row: True)

    turnover = _sum_by_employee(rows_of("Turnover"))
    pc_bt = _orders_with_min_lines(rows_of("PC_BT"), assumptions["pc_bt_min_lines"])
    line_mode = assumptions.get("lppc_same_day_lines", "all")   # chỉ có tác dụng với dòng RAW (có DAY)
    lppc = _orders_with_min_lines(rows_of("LPPC"), assumptions["lppc_min_lines"], line_mode)
    aso = {kpi: _count_outlets(blocks[kpi]["rows"], keep_for(kpi)) for kpi in ("ASO_ALL", "ASO_Focus")}
    aso["ASO_Focus_2"] = _count_outlets(_focus_2_rows(blocks["ASO_Focus_2"]["rows"], assumptions), keep_for("ASO_Focus_2"))
    pc_on = _count_rows(blocks["PC_ON"]["rows"], keep_for("PC_ON"))
    meat_lines, meat_orders = _lines_and_orders(blocks["LPPC_Meat"]["rows"], keep_for("LPPC_Meat"), line_mode)

    min_bt, min_lppc = assumptions["pc_bt_min_lines"], assumptions["lppc_min_lines"]
    # Bậc 3a: tên nhóm / mã / ngành và số mục công văn trong chữ "cách đếm" lấy từ luật kỳ (trước ghi cứng theo tháng 10)
    names, sections = how_names(assumptions), rules.get("cong_van_sections", {})

    def cite(kpi: str, extra: str = "", sep: str = "; ") -> str:
        parts = ([f"mục {sections[kpi]}"] if kpi in sections else []) + ([extra] if extra else [])
        return f" ({sep.join(parts)})" if parts else ""
    results = {}
    for code, by_kpi in targets["targets"].items():
        lppc_orders, lppc_lines = lppc[code]
        rows = [
            ("Turnover", turnover[code], turnover[code], 1,
             "tổng doanh số trước chiết khấu (công văn + trang sếp) của đơn thuộc trạng thái được tính"),
            ("PC_BT", pc_bt[code][0], pc_bt[code][0], 1,
             f"số đơn kênh OFF có ≥ {min_bt} mã đạt MOQ" + (f", không tính mã ngành {', '.join(assumptions['pc_bt_excluded_subdiv'])}"
                                                            if assumptions.get("pc_bt_excluded_subdiv") else "")
             + ", tối đa 1 đơn / ngày / cửa hàng" + cite("PC_BT", "số mã + ngành bỏ theo trang theo dõi của đội")),
            ("LPPC", _ratio(lppc_lines, lppc_orders), lppc_lines, lppc_orders,
             f"{lppc_lines} line ÷ {lppc_orders} đơn có ≥ {min_lppc} mã đạt MOQ, trừ ngành theo bộ lọc pivot" + cite("LPPC")),
            ("ASO_ALL", aso["ASO_ALL"][code], aso["ASO_ALL"][code], 1,
             "số cửa hàng (OFF + ON) có mua hàng đạt MOQ" + cite("ASO_ALL")),
            ("ASO_Focus", aso["ASO_Focus"][code], aso["ASO_Focus"][code], 1,
             f"số cửa hàng mua {names['ASO_Focus']} đạt MOQ" + cite("ASO_Focus")),
            ("ASO_Focus_2", aso["ASO_Focus_2"][code], aso["ASO_Focus_2"][code], 1,
             f"số cửa hàng mua {names['ASO_Focus_2']} đạt MOQ"
             + (cite("ASO_Focus_2", "theo công văn + trang sếp", sep=", ") if assumptions["aso_focus_2_scope"] == "sku_list"
                else " — cả nhóm" + cite("ASO_Focus_2"))),
            ("PC_ON", pc_on[code], pc_on[code], 1,
             "số đơn kênh ON có ≥ 1 mã đạt MOQ" + cite("PC_ON")),
            ("LPPC_Meat", _ratio(meat_lines[code], meat_orders[code]), meat_lines[code], meat_orders[code],
             f"{meat_lines[code]} line {names['LPPC_Meat']} ÷ {meat_orders[code]} đơn có ≥ 1 line đạt MOQ" + cite("LPPC_Meat")),
        ]
        results[code] = {kpi: KpiResult(kpi, by_kpi[kpi], actual, num, den, how)
                         for kpi, actual, num, den, how in rows}
    return results


def _focus_2_rows(rows: list[dict], assumptions: dict) -> list[dict]:
    """KPI 6: cả nhóm Homey (sổ Tracking) hoặc chỉ các mã ở `aso_focus_2_skus` (công văn + trang sếp 10/11) — mã cần dữ liệu có mã hàng (RAW)."""
    if assumptions["aso_focus_2_scope"] != "sku_list":
        return rows
    if any("SKU CODE" not in row for row in rows):
        raise ValueError("aso_focus_2_scope = sku_list cần dữ liệu có mã hàng từng dòng (RAW DATA) — khối pivot Tracking không có")
    skus = set(assumptions["aso_focus_2_skus"])
    return [row for row in rows if row["SKU CODE"] in skus]


# KPI lọc tuyến khi file luật KHÔNG có `mcp_filter_kpis` (5 KPI đếm cửa hàng). Có khoá ⇒ đúng danh sách trong khoá (mốc 7 16/11)
OUTLET_KPIS = ("ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat")


def _kept_rows(rows: list[dict], kpi: str, keep) -> list[dict]:
    """Dòng thuộc tuyến của một KPI — dòng không có mã cửa hàng (khối pivot Tracking của Turnover / PC_BT / LPPC) ⇒ dừng, không bỏ lọc lặng lẽ."""
    if any("OUTLET CODE" not in row for row in rows):
        raise ValueError(f"mcp_filter_kpis có {kpi} nhưng khối {kpi} không có mã cửa hàng (OUTLET CODE) — chỉ dữ liệu RAW lọc tuyến được")
    return [row for row in rows if keep(row)]


def _outlet_filter(mcp: dict, exclude_outside_mcp: bool):
    if not exclude_outside_mcp:
        return lambda row: True
    return lambda row: row["OUTLET CODE"] in mcp.get(row["SM CODE"], {})


def _sum_by_employee(rows: list[dict]) -> dict:
    total = defaultdict(float)
    for row in rows:
        total[row["SM CODE"]] += row["Total"] or 0
    return total


def _pc_and_lines(rows: list[dict], line_mode: str) -> tuple[int, int]:
    """(số PC, tổng line) của các đơn ĐÃ ĐẠT ngưỡng của một NV. Dòng không có DAY (pivot Tracking) ⇒ mỗi đơn 1 PC.
    Dòng có DAY (RAW, công văn 2.2 / 2.3 / 2.7 / 2.8) ⇒ tối đa 1 PC / ngày / cửa hàng; line của các đơn cùng đạt trong cùng cửa hàng–ngày:
    "all" = cộng line mọi đơn đạt · "one" = chỉ đơn nhiều line nhất — chữ công văn mơ hồ ở đây (thử cả hai, chọn theo trang).
    """
    if not any("DAY" in row for row in rows):
        return len(rows), sum(row["Total"] for row in rows)
    groups = defaultdict(list)
    for row in rows:
        groups[(row["OUTLET CODE"], row["DAY"])].append(row["Total"])
    lines = sum(sum(totals) if line_mode == "all" else max(totals) for totals in groups.values())
    return len(groups), lines


def _orders_with_min_lines(rows: list[dict], min_lines: int, line_mode: str = "all") -> dict:
    """{mã NV: (số PC có ≥ min_lines mã đạt MOQ, tổng line của các PC đó)}. Ngưỡng xét TỪNG ĐƠN (mỗi dòng = một đơn), rồi mới giới hạn PC."""
    by_employee = defaultdict(list)
    for row in rows:
        if (row["Total"] or 0) >= min_lines:
            by_employee[row["SM CODE"]].append(row)
    return defaultdict(lambda: (0, 0), {code: _pc_and_lines(qualified, line_mode) for code, qualified in by_employee.items()})


def _count_outlets(rows: list[dict], keep) -> dict:
    """Mỗi dòng pivot = một cửa hàng có doanh số hàng đạt MOQ ⇒ đếm cửa hàng khác nhau."""
    outlets = defaultdict(set)
    for row in rows:
        if keep(row) and (row["Total"] or 0) > 0:
            outlets[row["SM CODE"]].add(row["OUTLET CODE"])
    return defaultdict(int, {code: len(s) for code, s in outlets.items()})


def _count_rows(rows: list[dict], keep) -> dict:
    """PC_ON: số PC (mỗi đơn có ≥ 1 line MOQ; RAW ⇒ tối đa 1 PC / ngày / cửa hàng)."""
    by_employee = defaultdict(list)
    for row in rows:
        if keep(row) and (row["Total"] or 0) > 0:
            by_employee[row["SM CODE"]].append(row)
    return defaultdict(int, {code: _pc_and_lines(kept, "all")[0] for code, kept in by_employee.items()})


def _lines_and_orders(rows: list[dict], keep, line_mode: str = "all") -> tuple[dict, dict]:
    """LPPC_Meat: (line, PC) theo NV — RAW ⇒ tối đa 1 PC / ngày / cửa hàng."""
    by_employee = defaultdict(list)
    for row in rows:
        if keep(row) and (row["Total"] or 0) > 0:
            by_employee[row["SM CODE"]].append(row)
    pcs = {code: _pc_and_lines(kept, line_mode) for code, kept in by_employee.items()}
    return defaultdict(int, {code: lines for code, (_, lines) in pcs.items()}), defaultdict(int, {code: pc for code, (pc, _) in pcs.items()})


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def working_days(period: str, rules: dict, until: date | None = None) -> list[date]:
    """Ngày công chuẩn của tháng (trừ Chủ nhật + ngày lễ trong cấu hình); `until` ⇒ chỉ tới hết ngày đó."""
    year, month = (int(x) for x in period.split("-"))
    day, days = date(year, month, 1), []
    holidays = {date.fromisoformat(str(h)) for h in rules["working_days"]["holidays"]}
    while day.month == month and (until is None or day <= until):
        if not (rules["working_days"]["exclude_sundays"] and day.weekday() == 6) and day not in holidays:
            days.append(day)
        day += timedelta(days=1)
    return days
