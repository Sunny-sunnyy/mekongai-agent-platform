"""Khối 1 — đọc file chỉ tiêu + sổ Tracking thành bảng chuẩn (dict / list thuần, không phụ thuộc Excel về sau).

Đọc bằng openpyxl `read_only` (đo 02/11: cả sổ Tracking 7 MB ≤ 76 MB RAM, ≤ 5 s). Sổ Tracking không có dòng đơn hàng:
sheet `Rawdata` là các pivot đặt cạnh nhau (rút từ Power Pivot), mỗi khối mở bằng tiêu đề "KPI 0n" ở hàng 1, tên cột ở
hàng 9, dữ liệu từ hàng 10 tới "Grand Total". Xem kpi_agent/docs/HIEU_DU_LIEU.md mục 2–3.
"""
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook

from kpi_agent.core.rules import KPI_ORDER

RAWDATA_TITLE_ROW = 1
RAWDATA_FILTER_ROWS = range(2, 8)
RAWDATA_HEADER_ROW = 9
MCP_DETAIL_FIRST_COLUMN = "Position code"   # khối "Danh sách MCP chi tiết" của sheet MCP


def canonical_kpi(name: str) -> str:
    """'ASO_All' / 'ASO_ALL' / 'Turnover' ⇒ tên chuẩn trong KPI_ORDER (sổ và file chỉ tiêu viết hoa khác nhau)."""
    for kpi in KPI_ORDER:
        if kpi.upper() == str(name).strip().upper():
            return kpi
    raise ValueError(f"KPI lạ: {name!r}")


def load_targets(path: str | Path) -> dict:
    """File chỉ tiêu (sheet Export) ⇒ {"employees": {sm_code: {...}}, "targets": {sm_code: {kpi: số}}}."""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(wb["Export"].iter_rows(values_only=True))
    finally:
        wb.close()
    header = rows[0]
    employees, targets = {}, {}
    for values in rows[1:]:
        row = dict(zip(header, values))
        code = row["SM code"]
        if not code:
            continue   # file có 2 hàng trống cuối
        employees[code] = {"name": row["SM name"], "position": row["SM pos"], "region": row["Region"],
                           "month": row["Month"], "distributor": row["Distributor"]}
        targets.setdefault(code, {})[canonical_kpi(row["KPI type"])] = row["Target"]
    return {"employees": employees, "targets": targets}


def load_tracking(path: str | Path) -> dict:
    """Sổ Tracking ⇒ {"blocks": {kpi: {...}}, "mcp": {sm_code: {outlet: {...}}}, "refreshed_at": datetime | None}."""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        raw_rows = list(wb["Rawdata"].iter_rows(values_only=True))
        mcp_rows = list(wb["MCP"].iter_rows(values_only=True))
    finally:
        wb.close()
    return {"blocks": _read_kpi_blocks(raw_rows), "mcp": _read_mcp(mcp_rows), "refreshed_at": pivot_refreshed_at(path)}


def _read_kpi_blocks(rows: list[tuple]) -> dict:
    titles = [(i, str(v).strip()) for i, v in enumerate(rows[RAWDATA_TITLE_ROW - 1]) if v]
    blocks = {}
    for n, (start, title) in enumerate(titles):
        match = re.fullmatch(r"KPI\s*0?(\d)", title)
        if not match:
            continue   # khối "Thưởng 0n", "1% gia vị"… ngoài công văn
        end = titles[n + 1][0] if n + 1 < len(titles) else len(rows[RAWDATA_HEADER_ROW - 1])
        kpi = KPI_ORDER[int(match.group(1)) - 1]
        header = [h for h in rows[RAWDATA_HEADER_ROW - 1][start:end]]
        filters = {}
        for r in RAWDATA_FILTER_ROWS:
            cells = rows[r - 1][start:start + 2]
            if len(cells) == 2 and cells[0]:
                filters[str(cells[0]).strip()] = cells[1]
        data = []
        for values in rows[RAWDATA_HEADER_ROW:]:
            first = values[start] if start < len(values) else None
            if first is None or first == "Grand Total":
                break
            # Cột công thức phụ của sổ ("Đếm", "Số line") bỏ qua — lõi tự tính
            data.append({h: v for h, v in zip(header, values[start:end]) if h in ("SM CODE", "ORDER NUM", "OUTLET CODE", "Total")})
        blocks[kpi] = {"title": title, "filters": filters, "rows": data}
    missing = [k for k in KPI_ORDER if k not in blocks]
    if missing:
        raise ValueError(f"Rawdata thiếu khối KPI: {missing}")
    return blocks


def _read_mcp(rows: list[tuple]) -> dict:
    """Danh sách MCP chi tiết: tìm cột 'Position code' cuối cùng ở hàng tiêu đề (3 khối đếm đầu sheet cũng có cột này)."""
    header_index = next(i for i, r in enumerate(rows) if MCP_DETAIL_FIRST_COLUMN in r)
    header = rows[header_index]
    start = max(i for i, v in enumerate(header) if v == MCP_DETAIL_FIRST_COLUMN)
    names = header[start:]
    mcp = {}
    for values in rows[header_index + 1:]:
        row = dict(zip(names, values[start:]))
        if not row.get("SM code"):
            continue
        frequency = int(re.sub(r"\D", "", str(row["Frequency"])) or 0)   # "F4" ⇒ 4 lần viếng thăm / tháng
        mcp.setdefault(row["SM code"], {})[row["Outlet_code"]] = {"channel": row["L1"], "visits_per_month": frequency}
    return mcp


def pivot_refreshed_at(path: str | Path) -> datetime | None:
    """Lần làm mới pivot gần nhất (thuộc tính refreshedDate, số ngày kiểu Excel) — cho biết dữ liệu chụp tới đâu."""
    serials = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.startswith("xl/pivotCache/pivotCacheDefinition"):
                found = re.search(rb'refreshedDate="([\d.]+)"', zf.read(name)[:3000])
                if found:
                    serials.append(float(found.group(1)))
    if not serials:
        return None
    return datetime(1899, 12, 30) + timedelta(days=max(serials))


def load_tracking_kpi_tables(path: str | Path) -> dict:
    """Hai bảng 8 KPI của sheet 9_KPI (để đối chiếu, không dùng để tính).

    Bảng 1 (hàng 4–17) tính từ Rawdata; bảng 2 (hàng 34–47) lấy số hệ thống (sheet Power PI). Mỗi KPI chiếm 6 cột bắt đầu
    từ cột E: chỉ tiêu, thực hiện, %, xếp hạng, còn thiếu, còn lại / ngày. Turnover trong sổ chia 10^6 (triệu đồng).
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(wb["9_KPI"].iter_rows(min_row=1, max_row=47, max_col=52, values_only=True))
    finally:
        wb.close()
    tables = {}
    for table, (first, last) in {"table_1_rawdata": (4, 17), "table_2_system": (34, 47)}.items():
        result = {}
        for values in rows[first - 1:last]:
            code = values[2]
            result[code] = {}
            for k, kpi in enumerate(KPI_ORDER):
                col = 4 + 6 * k   # cột E = chỉ số 4
                target, actual, pct = values[col], values[col + 1], values[col + 2]
                scale = 1e6 if kpi == "Turnover" else 1
                result[code][kpi] = {"target": target * scale, "actual": actual * scale, "pct": pct}
        tables[table] = result
    return tables

