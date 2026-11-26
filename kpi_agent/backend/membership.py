"""Tab "Hội viên" — PHẦN DOANH SỐ (trang theo dõi của đội có tab này, bên mình trước đó để trống).

Dựng được: danh sách khách hội viên + doanh số nền (sheet "Hoi Vien Masan" của sổ Tracking) và doanh số tháng của từng khách từ file đơn hàng ⇒
% so với nền, còn lại, nhóm theo doanh số. Cách cộng doanh số = cách của trang của đội (so 13/11: khớp 417 / 417 khách): cột `Sales After Discount`,
mọi dòng đơn có ngày đơn ≤ ngày chốt và trạng thái được tính, KỂ CẢ hoá đơn xuất trễ, không lọc theo nhân viên viết đơn.
CHƯA dựng (thiếu dữ liệu, không đoán): hạng từng khách, phân loại ngành Focus / Mới / Cũ, nhiệm vụ Duy trì / Mở mới, tiền thưởng (công văn 408).
⇒ "đủ doanh số" ở đây KHÔNG có nghĩa là "đã đạt chương trình".

Vì sao tự đọc file đơn hàng thay vì dùng `core.load_raw.read_raw_lines`: hàm của lõi bỏ dòng hoá đơn trễ (đúng cho 8 KPI — công văn 6.1) còn chương
trình này tính cả; lõi nằm trong dấu vân tay bản tính nên không sửa. Ngoài `core/`: không quyết số 8 KPI nào.
Dòng trả về CÓ tên + mã khách ⇒ chỉ cho trang xem; KHÔNG đưa vào công cụ / chữ gửi mô hình."""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import yaml
from openpyxl import load_workbook

from kpi_agent.backend.outlets import visit_days
from kpi_agent.core import raw_sheet
from kpi_agent.core.compute_bonus import floor_pct
from kpi_agent.core.load_raw import SHEET

TOTAL_ROW_CODES = ("(blank)", "grand total")   # 13/11: dòng Excel cuối bảng là dòng tổng, từng bị đếm thành "1 khách không ghép được"
NEAR_PCT = 60   # trang của đội: "Gần đạt (≥ 60 %)", "Đang bán (< 60 %)"
# Nhóm theo DOANH SỐ (thứ tự hiện). "du_doanh_so" gộp hai nhóm của trang ("Đã đạt CT" + "Đủ DS chưa đạt") vì chưa xét được nhiệm vụ.
GROUPS = {"du_doanh_so": "Đủ doanh số ≥ nền (chưa xét nhiệm vụ)", "gan_dat": "Gần đạt (60 % đến dưới 100 % nền)", "dang_ban": "Đang bán (dưới 60 % nền)",
          "chua_phat_sinh": "Chưa phát sinh doanh số"}
NOT_BUILT = "Chưa có hạng khách, chưa xét nhiệm vụ Duy trì / Mở mới, chưa có tiền thưởng — thiếu: hạng từng khách, phân loại ngành Focus / Mới / Cũ, công văn 408."


def load_source(path: str | Path) -> dict:
    """File cấu hình nguồn của kỳ (`config/hoi_vien_<kỳ>.yaml`): đường sổ, sheet, hàng tiêu đề, tên cột. Thiếu mục ⇒ ValueError."""
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in ("period", "workbook", "sheet", "header_row", "columns") if key not in (spec or {})]
    missing += [f"columns.{key}" for key in ("customer", "name", "employee_name", "baseline", "visit") if key not in (spec or {}).get("columns", {})]
    if missing:
        raise ValueError(f"file nguồn hội viên thiếu mục: {', '.join(missing)}")
    return spec


def read_members(workbook_path, spec: dict) -> list[dict]:
    """[{customer, name, employee_name, baseline, visit_days}] từ sổ. Thiếu sheet / cột, mã khách lặp, nền không phải số ⇒ ValueError (dừng, không đoán)."""
    MEMBER_SHEET, MEMBER_HEADER_ROW, MEMBER_COLUMNS = spec["sheet"], spec["header_row"], spec["columns"]
    wb = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        if MEMBER_SHEET not in wb.sheetnames:
            raise ValueError(f"sổ không có sheet '{MEMBER_SHEET}'")
        rows = wb[MEMBER_SHEET].iter_rows(min_row=MEMBER_HEADER_ROW, values_only=True)
        header = [str(value).strip() if value is not None else "" for value in next(rows)]
        missing = [name for name in MEMBER_COLUMNS.values() if name not in header]
        if missing:
            raise ValueError(f"sheet '{MEMBER_SHEET}' thiếu cột: {', '.join(missing)}")
        at = {key: header.index(name) for key, name in MEMBER_COLUMNS.items()}   # cột trùng tên ("Chỉ tiêu" có 2 lần) ⇒ lấy cột ĐẦU = nền của khách
        out, seen = [], set()
        for offset, values in enumerate(rows, start=MEMBER_HEADER_ROW + 1):
            code = values[at["customer"]] if len(values) > at["customer"] else None
            if code in (None, ""):
                continue
            code, baseline = str(code).strip(), values[at["baseline"]]
            if code.lower() in TOTAL_ROW_CODES:
                continue                                    # dòng TỔNG của bảng tổng hợp trong sổ (ô mã ghi "(blank)", ô nền = tổng nền) — không phải khách
            if isinstance(baseline, bool) or not isinstance(baseline, (int, float)):
                raise ValueError(f"sheet '{MEMBER_SHEET}' dòng {offset}: ô nền không phải số")
            if code in seen:
                raise ValueError(f"sheet '{MEMBER_SHEET}' dòng {offset}: mã khách lặp lại")
            seen.add(code)
            name = str(values[at["name"]]).strip() if values[at["name"]] not in (None, "") else None
            out.append({"customer": code, "name": name, "employee_name": str(values[at["employee_name"]] or "").strip(),
                        "baseline": float(baseline), "visit_days": visit_days(values[at["visit"]] if len(values) > at["visit"] else None)})
    finally:
        wb.close()
    if not out:
        raise ValueError(f"sheet '{MEMBER_SHEET}' không có khách nào")
    return out


def sales_after_discount(raw_path, cutoff: date, *, counted_status) -> dict[str, Decimal]:
    """{mã điểm bán: doanh số sau chiết khấu} — mọi dòng đơn tới ngày chốt, trạng thái được tính, kể cả hoá đơn xuất trễ. Ô doanh số không phải số ⇒ dừng."""
    # Dòng thô lấy từ lớp đọc-một-lần (19/11) — file đã được 8 KPI đọc lúc bật thì ở đây không mở lại. Phép lọc + lời lỗi riêng giữ nguyên
    # (chỉ lùi lề vì hết khối try / finally đóng file).
    _sheet_names, sheet_rows = raw_sheet.sheet_rows(raw_path, SHEET)
    if sheet_rows is None:
        raise ValueError(f"file đơn hàng thiếu sheet '{SHEET}'")
    rows = iter(sheet_rows)
    header = list(next(rows))
    columns = ("SM CODE", "OUTLET CODE", "Sales After Discount", "ORDER DATE", "ORDER STATUS")
    missing = [name for name in columns if name not in header]
    if missing:
        raise ValueError(f"file đơn hàng thiếu cột: {', '.join(missing)}")
    at = {name: header.index(name) for name in columns}
    last, totals = max(at.values()), defaultdict(Decimal)
    for index, values in enumerate(rows, start=2):
        if len(values) <= last or not values[at["SM CODE"]]:
            continue                                    # dòng trống cuối file
        day = values[at["ORDER DATE"]]
        day = day.date() if isinstance(day, datetime) else day
        if not isinstance(day, date) or day > cutoff or values[at["ORDER STATUS"]] not in counted_status:
            continue
        sales = values[at["Sales After Discount"]]
        if isinstance(sales, bool) or not isinstance(sales, (int, float)):
            raise ValueError(f"file đơn hàng dòng Excel {index}: ô 'Sales After Discount' trống hoặc không phải số")
        totals[str(values[at["OUTLET CODE"]])] += Decimal(str(sales))
    return totals


def group_of(sales: Decimal, baseline: float) -> str:
    """Nhóm theo doanh số — so bằng phép nhân số thô (không làm tròn % trước khi so)."""
    if sales <= 0:
        return "chua_phat_sinh"
    if baseline > 0 and sales >= Decimal(str(baseline)):
        return "du_doanh_so"
    return "gan_dat" if baseline > 0 and sales * 100 >= NEAR_PCT * Decimal(str(baseline)) else "dang_ban"


def compute(members: list[dict], sales: dict[str, Decimal], employee_codes: dict[str, str], to_masked: dict[str, str]) -> tuple[list[dict], int]:
    """Một dòng cho mỗi khách hội viên có nhân viên phụ trách thuộc đội + số khách bị bỏ vì nhân viên trong sổ không ghép được vào đội."""
    rows, skipped = [], 0
    for member in members:
        masked = to_masked.get(employee_codes.get(member["employee_name"]))
        if masked is None:
            skipped += 1
            continue
        amount, baseline = sales.get(member["customer"], Decimal(0)), member["baseline"]
        rows.append({"customer_code": member["customer"], "customer_name": member["name"], "employee_id": masked, "visit_days": member["visit_days"],
                     "baseline": round(baseline), "sales": round(amount),
                     "pct_of_baseline": float(floor_pct(float(amount) / baseline)) if baseline > 0 else None,
                     "remaining": max(0, round(Decimal(str(baseline)) - amount)), "group": group_of(amount, baseline)})
    rows.sort(key=lambda row: (row["employee_id"], row["customer_code"]))
    return rows, skipped


def _counts(rows: list[dict]) -> dict:
    baseline, sales = sum(row["baseline"] for row in rows), sum(row["sales"] for row in rows)
    return {"customers": len(rows), **{group: sum(row["group"] == group for row in rows) for group in GROUPS},
            "sales": sales, "baseline": baseline, "pct_of_baseline": float(floor_pct(sales / baseline)) if baseline else None}


def view(rows: list[dict], team: list[str], employee_id: str | None = None) -> dict:
    """Ô tổng (cả đội hoặc một nhân viên) + bảng theo nhân viên (luôn cả đội) + danh sách khách đang xem."""
    shown = [row for row in rows if employee_id in (None, row["employee_id"])]
    return {"summary": _counts(shown), "by_employee": [{"employee_id": masked, **_counts([row for row in rows if row["employee_id"] == masked])} for masked in team],
            "rows": shown, "groups": GROUPS, "not_built": NOT_BUILT}
