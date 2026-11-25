"""Khuôn chuẩn nạp dữ liệu KPI — bậc 2 bước 2.1 (11:15 17/11/2026).

Vì sao: "hệ thống dùng cho mọi tháng" — file tháng sau có thể đổi tên cột / sheet / dòng tiêu đề. Phép tính chỉ nên biết TRƯỜNG CHUẨN;
mỗi file được ghép (cột file ⇒ trường chuẩn) trước khi tính. File này giữ (1) danh sách trường chuẩn — MỘT nguồn cho lõi và cho API màn ghép
(trang không chép tay 44 trường — M1); (2) đọc bố cục file + vân tay bố cục. CHỈ ĐỌC: không đổi số nào (bước 2.2 mới cho lõi đọc qua ghép).
Bậc 2 chỉ làm bảng `don_hang` (RAW); 4 bảng khác (chỉ tiêu, tuyến, chỉ tiêu thưởng thêm, khách chương trình) để bậc 3.
Nguồn trường: nháp khuôn chuẩn mục 2.1 (agent con A, 10:45 17/11) — tên trường + kiểu giữ như nháp."""
import hashlib
import json
import unicodedata
from pathlib import Path

from openpyxl import load_workbook

# kind: chuoi · so_nguyen · so_le · tien (đồng, có phần lẻ, cộng bằng Decimal) · ngay · ngay_gio · tap (chỉ nhận giá trị liệt kê) · co (Y / N)
# required = lõi 8 KPI cần (đúng 13 cột `load_raw.NEEDED`); trường còn lại chỉ một màn / khoản phụ cần (ghi ở `used_for`)
_DON_HANG = [
    ("sm_code", "mã nhân viên bán hàng viết đơn", "chuoi", True, "SM CODE", ""),
    ("sm_name", "họ tên nhân viên (ghép tên trên trang / sổ ⇒ mã)", "chuoi", False, "SM_NAME", "Theo dõi, 1 % gia vị"),
    ("order_num", "số đơn", "chuoi", True, "ORDER NUM", ""),
    ("order_status", "trạng thái đơn", "tap", True, "ORDER STATUS", ""),
    ("order_date", "ngày đặt đơn (chọn kỳ + ngày chốt)", "ngay", True, "ORDER DATE", ""),
    ("invoiced_at", "lúc xuất hoá đơn (được trống — luật kỳ nói trạng thái nào cần)", "ngay_gio", True, "INVOICED DATE", ""),
    ("outlet_code", "mã cửa hàng (giữ số 0 đầu)", "chuoi", True, "OUTLET CODE", ""),
    ("sub_div", "ngành hàng", "tap", True, "SUB DIV", ""),
    ("product_group", "nhóm hàng (tên nhóm DMS; dòng hàng tặng được trống)", "chuoi", True, "DMS OBJ", ""),
    ("sku_code", "mã hàng", "chuoi", True, "SKU CODE", ""),
    ("sales_before_discount", "doanh số trước chiết khấu (KPI 1, ASO)", "tien", True, "Sales Before Discount", ""),
    ("sales_after_discount", "doanh số sau chiết khấu", "tien", False, "Sales After Discount", "Chi tiết, Hội viên, 1 % gia vị"),
    ("discount_value", "tiền chiết khấu (chỉ để kiểm nghĩa: trước − sau = chiết khấu)", "tien", False, "Discount value", "kiểm nghĩa cột tiền"),
    ("channel", "kênh bán (Off / On)", "tap", True, "L1", ""),
    ("moq_flag", "dòng đạt số lượng mua tối thiểu", "co", True, "MOQ_FLAG", ""),
    ("is_free_item", "dòng hàng tặng", "co", True, "FREE ITEM FLAG", ""),
    ("total_cases", "số thùng (quy đổi)", "so_le", False, "TOTAL CASES", "Thưởng thêm 01–02, Trà 365"),
    ("total_pieces", "số đơn vị lẻ (hộp / chai / túi)", "so_nguyen", False, "TOTAL PIECES", "Thưởng thêm 04–05"),
    ("outlet_name", "tên cửa hàng (dữ liệu khách — chỉ cho trang)", "chuoi", False, "OUTLET NAME", "Chi tiết, xem đơn"),
    ("address", "địa chỉ cửa hàng (dữ liệu khách — chỉ cho trang)", "chuoi", False, "ADDRESS", "Chi tiết"),
]
SCHEMAS = {"don_hang": [{"field": f, "meaning": m, "kind": k, "required": r, "default_column": c, "used_for": u, "may_be_blank": f == "invoiced_at"}
                        for f, m, k, r, c, u in _DON_HANG]}
# Bộ cột của file xuất DMS đã gặp (RAW T10 = T11, 38 cột, B đọc 17/11). Chỉ tên trong bộ này được trả lại cho trang; tên khác là chữ người đặt
# ⇒ che (rào U1, `backend/upload.py` đầu file: lời trả không lặp chữ người tải đặt). Danh sách này KHÔNG phải luật ghép — chỉ để biết tên nào an toàn.
KNOWN_COLUMNS = {"don_hang": (
    "SHIP TO", "DISTRIBUTOR", "SS CODE", "SS NAME", "SM CODE", "SM_NAME", "SALETEAM", "POS_CODE", "ORDER NUM", "ORDER STATUS", "ORDER DATE",
    "OUTLET CODE", "OUTLET NAME", "ADDRESS", "PROVINCE", "Ward", "SUB DIV", "DMS OBJ", "SKU CODE", "SKU DESC", "UNIT PRICE", "CASE/PACK",
    "QUANTITY CASE", "QUANTITY PIECE", "TOTAL CASES", "TOTAL PIECES", "Sales Before Discount", "Discount value", "Sales After Discount",
    "FREE ITEM FLAG", "Promotion value", "INVOICED DATE", "INVOICE NUMBER", "L1", "L2", "L3", "MOQ_FLAG", "BRAND")}
KNOWN_SHEETS = {"don_hang": ("Export",)}
# Vân tay bố cục đã biết = bố cục RAW T10 / T11 (B tính 17/11 bằng `layout_fingerprint("Export", 1, <38 cột>)`). Bước 2.3 thêm bố cục người đã xác nhận (kho)
KNOWN_LAYOUTS = {"don_hang": "4ad9d836443f5a17ad608c2cf61333a4feee4896874e3bf1d3d63a0959f0d341"}
HEADER_SCAN_ROWS = 20   # dòng tiêu đề tìm trong 20 dòng đầu (file gõ tay có dòng tựa ở trên)


def schema(table: str) -> list[dict]:
    if table not in SCHEMAS:
        raise ValueError(f"chưa có khuôn cho bảng '{table}' — bậc 2 chỉ có: {', '.join(SCHEMAS)}")
    return SCHEMAS[table]


def _clean(name) -> str:
    return unicodedata.normalize("NFC", str(name).strip()) if name is not None else ""


def layout_fingerprint(sheet: str, header_row: int, columns: list) -> str:
    """sha256 của {sheet, dòng tiêu đề, tên cột THEO THỨ TỰ} (bỏ khoảng trắng 2 đầu, NFC, bỏ ô trống cuối) — nháp khuôn chuẩn mục 6.
    Có thứ tự cột: đổi thứ tự = bên xuất đổi mẫu ⇒ hỏi người xác nhận lại. Không băm nội dung ô / số dòng (đổi mỗi tháng)."""
    names = [_clean(name) for name in columns]
    while names and not names[-1]:
        names.pop()
    payload = json.dumps({"sheet": _clean(sheet), "header_row": header_row, "columns": names}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_layout(path: str | Path, table: str, sheet_number: int | None = None, header_row: int | None = None) -> dict:
    """Bố cục một file cho màn ghép: sheet + dòng tiêu đề (đoán = dòng có NHIỀU tên cột đã biết nhất trong 20 dòng đầu, ở mọi sheet), danh sách cột
    theo thứ tự (tên đã biết ⇒ trả tên; tên lạ ⇒ chỉ vị trí + độ dài — U1), vân tay, đã biết chưa, trường bắt buộc chưa thấy cột mặc định.
    Không nhận ra dòng tiêu đề ở sheet nào ⇒ ValueError (không đoán). `sheet_number` (1-based) + `header_row` do NGƯỜI chỉ (file đổi tên hết cột —
    máy không đoán được) ⇒ đọc đúng dòng đó, không đoán."""
    known, fields = set(KNOWN_COLUMNS[table]), schema(table)
    wb = load_workbook(path, read_only=True, data_only=True)
    sheet_names = list(wb.sheetnames)
    try:
        best = None   # (số tên đã biết, sheet, dòng, cột)
        if sheet_number is not None and header_row is not None:
            if not 1 <= sheet_number <= len(wb.sheetnames) or header_row < 1:
                raise ValueError("không có sheet / dòng tiêu đề này trong file")
            sheet = wb.sheetnames[sheet_number - 1]
            row = next(wb[sheet].iter_rows(min_row=header_row, max_row=header_row, values_only=True), ())
            best = (0, sheet, header_row, list(row))
        for sheet in ([] if best else wb.sheetnames):
            for number, row in enumerate(wb[sheet].iter_rows(max_row=HEADER_SCAN_ROWS, values_only=True), start=1):
                hits = sum(_clean(cell) in known for cell in row)
                if hits and (best is None or hits > best[0]):
                    best = (hits, sheet, number, list(row))
    finally:
        wb.close()
    if best is None:
        raise ValueError("không nhận ra dòng tiêu đề ở sheet nào (20 dòng đầu không có tên cột nào của file xuất DMS đã biết) — cần ghép tay")
    _, sheet, header_row, row = best
    names = [_clean(cell) for cell in row]
    while names and not names[-1]:
        names.pop()
    columns = [{"position": i, "name": name} if name in known else {"position": i, "masked": True, "length": len(name)}
               for i, name in enumerate(names, start=1)]
    fingerprint = layout_fingerprint(sheet, header_row, names)
    sheet_known = _clean(sheet) in KNOWN_SHEETS[table]
    return {"table": table, "sheet": _clean(sheet) if sheet_known else None, "sheet_masked": not sheet_known,
            "sheet_number": sheet_names.index(sheet) + 1, "header_row": header_row,
            "columns": columns, "fingerprint": fingerprint, "known": fingerprint == KNOWN_LAYOUTS[table],
            "missing_required": [field["field"] for field in fields if field["required"] and field["default_column"] not in names]}


# ---------- Bước 2.2: bảng ghép (cột file ⇒ trường chuẩn) — lõi đọc RAW qua đây (`load_raw._read_rows`) ----------
# Giá trị đích của các trường được ghép giá trị: lõi đang so đúng các chữ này (`load_raw` OFF / ON, cờ "Y", trạng thái đơn) ⇒ ghép dịch chữ của file
# về đúng chữ đó, lõi không phải đổi. Trạng thái bỏ qua (Cancelled…) như `load_raw._check`.
CANONICAL_VALUES = {"moq_flag": {"Y", "N"}, "is_free_item": {"Y", "N"}, "channel": {"Kênh Off Premise", "Kênh On Premise"},
                    "order_status": {"Invoiced", "Shipped", "Cancelled", "Canceled", "Draft", "Pending"}}
# Ô số phải là SỐ THẬT trong Excel: ô chữ dạng số (kiểu Việt hay Anh) ⇒ dừng, người sửa file ( 11:4x 17/11 — T10 / T11 đều là số thật;
# đoán "384,00" là 384 hay 384.000 đã từng sai ×1000 ở màn ghép nhóm kỹ thuật). Không có khoá kiểu số trong bảng ghép.

# Bước 2.4: tập giá trị ĐÃ BIẾT (RAW T10 + T11, đã đếm 17/11) của trường tap / co mà lõi so bằng chữ — giá trị khác ⇒ lõi so trượt LẶNG LẼ (vd kênh lạ
# không vào PC_BT / PC_ON, cờ "y" không đạt MOQ) ⇒ dừng. Kênh / cờ lạ phải GHÉP về chữ lõi dùng (`values`); ngành mới người được CHO QUA
# (`accepted_values.sub_div`) vì ngành chỉ dùng để trừ / chọn theo tên trong luật. Trạng thái lạ đã dừng ở `load_raw._check`.
KNOWN_VALUES = {"channel": {"Kênh Off Premise", "Kênh On Premise"}, "moq_flag": {"Y", "N"}, "is_free_item": {"Y", "N"},
                "sub_div": {"Seasoning", "Processed Meats", "Convenience Foods", "Refreshment Drinks", "Home Care", "Coffee", "Nutrition", "Beer"}}
ACCEPTABLE_NEW = ("sub_div",)


# Kiểm NGHĨA khi ghép (2.1–2.3 major M1, 17/11): cổng đối soát + câu kiểm đọc theo CHÍNH cột đã ghép ⇒ ghép nhầm cột tiền / đổi chỗ cờ
# vẫn qua (đo: tổng 8 KPI 145.640.000, −6.720.000). Các bất biến dưới ĐÚNG 100 % dòng RAW T10 (24.125) + T11 (4.562), 17/11:
# trước CK ≥ sau CK · trước − sau = tiền chiết khấu (lệch tối đa 0 đ) · dòng hàng tặng doanh số 0 đ (467 / 125 dòng) · tỉ lệ cờ Y: MOQ 99,2 % / 99,4 %,
# hàng tặng 1,9 % / 2,7 %. Ngưỡng cờ đặt rộng (50 %) — chỉ để bắt đổi chỗ, không bắt tháng bán khác thường. Tỉ lệ chỉ xét khi ≥ 100 dòng.
FLAG_RATE_RULES = {"moq_flag": ("min", 50), "is_free_item": ("max", 50)}
FLAG_RATE_MIN_LINES = 100
MONEY_TOLERANCE = 1   # đồng — RAW có doanh số lẻ (0,54 đ); lệch < 1 đ coi là bằng


def default_mapping(table: str) -> dict:
    """Bảng ghép của bố cục RAW T10 / T11 (đã biết) — mọi trường về cột mặc định, không ghép giá trị, ô số phải là số thật."""
    return {"table": table, "sheet": KNOWN_SHEETS[table][0], "header_row": 1,
            "columns": {field["field"]: field["default_column"] for field in schema(table)}, "values": {}}


def normalize_mapping(mapping: dict) -> dict:
    """Kiểm khuôn + dạng chuẩn của một bảng ghép (để băm). Sai ⇒ ValueError nêu TÊN TRƯỜNG (không lặp chữ cột / giá trị — có thể là chữ của file)."""
    table = mapping.get("table")
    fields = {field["field"]: field for field in schema(table)}
    errors = []
    # sheet: tên (bố cục đã biết) hoặc SỐ THỨ TỰ 1-based; cột: tên hoặc VỊ TRÍ 1-based — tên lạ bị che ở /bo-cuc (U1) nên người ghép theo số
    sheet = mapping.get("sheet")
    if not ((isinstance(sheet, str) and sheet.strip()) or (isinstance(sheet, int) and not isinstance(sheet, bool) and sheet >= 1)):
        errors.append("sheet: cần tên sheet hoặc số thứ tự ≥ 1")
    if isinstance(mapping.get("header_row"), bool) or not isinstance(mapping.get("header_row"), int) or mapping["header_row"] < 1:
        errors.append("header_row: cần số dòng ≥ 1")
    columns = mapping.get("columns") if isinstance(mapping.get("columns"), dict) else {}
    errors += [f"columns: trường lạ {name}" for name in columns if name not in fields]
    errors += [f"columns.{name}: thiếu cột cho trường bắt buộc" for name, field in fields.items() if field["required"] and not columns.get(name)]
    valid = lambda column: (isinstance(column, str) and column.strip()) or (isinstance(column, int) and not isinstance(column, bool) and column >= 1)
    # hai trường cùng một cột ⇒ kiểm nghĩa "trước ≥ sau" so cột với chính nó, luôn qua ⇒ sai khuôn
    used = [strip_value for strip_value in (column.strip() if isinstance(column, str) else column for column in columns.values() if valid(column))]
    errors += [f"columns: {len(used) - len(set(used))} cặp trường ghép vào cùng một cột"] if len(used) != len(set(used)) else []
    errors += [f"columns.{name}: cần tên cột hoặc vị trí ≥ 1" for name, column in columns.items() if name in fields and not valid(column)]
    values = mapping.get("values") if isinstance(mapping.get("values"), dict) else {}
    for name, pairs in values.items():
        if name not in CANONICAL_VALUES or not isinstance(pairs, dict) or any(target not in CANONICAL_VALUES[name] for target in pairs.values()):
            errors.append(f"values.{name}: chỉ ghép được về {sorted(CANONICAL_VALUES.get(name, ()))}")
    errors += [f"khoá lạ {name}" for name in mapping if name not in ("table", "sheet", "header_row", "columns", "values", "accepted_values")]
    accepted = mapping.get("accepted_values") if isinstance(mapping.get("accepted_values"), dict) else {}
    errors += [f"accepted_values.{name}: chỉ cho qua giá trị mới của {', '.join(ACCEPTABLE_NEW)} (kênh / cờ phải ghép)"
               for name, items in accepted.items() if name not in ACCEPTABLE_NEW or not isinstance(items, list)
               or not all(isinstance(item, str) and item.strip() for item in items)]
    if errors:
        raise ValueError("bảng ghép sai khuôn: " + "; ".join(errors))
    strip = lambda value: value.strip() if isinstance(value, str) else value
    return {"table": table, "sheet": strip(mapping["sheet"]), "header_row": mapping["header_row"],
            "columns": {name: strip(columns[name]) for name in fields if columns.get(name)},
            "values": {name: dict(sorted(values[name].items())) for name in sorted(values)},
            **({"accepted_values": {name: sorted(set(accepted[name])) for name in sorted(accepted)}} if accepted else {})}


def mapping_sha(mapping: dict) -> str:
    """sha256 của bảng ghép đã chuẩn hoá — vào `data_version` + phần dữ liệu của bản tính (3.1: cùng file, ghép khác ⇒ số khác ⇒ bản khác)."""
    return hashlib.sha256(json.dumps(normalize_mapping(mapping), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
