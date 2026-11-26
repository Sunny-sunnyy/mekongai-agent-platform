"""Đọc RAW DATA đơn hàng (DMS export — mỗi dòng một mã hàng của một đơn) ⇒ ĐÚNG hình `tracking["blocks"]` mà `compute_kpi.compute_actuals` đang nhận.

Vì sao (10/11 "chuyển 8 KPI sang RAW" — kế hoạch): sổ Tracking là ảnh chụp
pivot (số đã gộp, ngày chụp cố định); RAW có từng dòng đơn + ngày đơn ⇒ ngày chốt có tác dụng thật, kiểm được luật cần dòng đơn. Dựng lại đúng các khối
pivot (bộ lọc từng KPI chép từ ô lọc pivot của Tracking — E đọc 10/11) ⇒ phép tính, luật, tiền KHÔNG đổi.
- Mỗi khối: danh sách dòng {SM CODE, ORDER NUM, OUTLET CODE, Total} (+ SKU CODE cho ASO_Focus_2 để luật chọn phạm vi mã).
- Ngày chốt: chỉ lấy dòng có ORDER DATE ≤ cutoff (RAW tháng 10 có ngày 01–31/10).
MCP: RAW không có ⇒ đọc riêng (`load_mcp_json`) — danh sách điểm bán trong tuyến tách từ trang sếp.
Dữ liệu sai khuôn (thiếu cột, ngày khác kỳ, trạng thái lạ) ⇒ `ValueError` — không ra số (lỗi nặng ⇒ dừng hẳn)."""
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from kpi_agent.core import khuon, raw_sheet

SHEET = "Export"
NEEDED = ("SM CODE", "ORDER NUM", "ORDER STATUS", "ORDER DATE", "OUTLET CODE", "SUB DIV", "DMS OBJ", "SKU CODE",
          "Sales Before Discount", "L1", "MOQ_FLAG", "FREE ITEM FLAG", "INVOICED DATE")
# Bậc 1 nạp dữ liệu chung (17/11): lý do loại một dòng RAW — mọi dòng hoặc được tính, hoặc có ĐÚNG một lý do (cổng đối soát `reconcile`)
EXCLUDE_REASONS = {
    "after_cutoff": "đơn sau ngày chốt",
    "status_not_counted": "trạng thái đơn không được tính",
    "no_invoice_date": "chưa có ngày xuất hoá đơn",
    "invoice_late": "xuất hoá đơn sau hạn 22:00 ngày 02 tháng sau (công văn 6.1)",
}
RECONCILE_STOP = "cổng đối soát RAW chặn"
OFF, ON = "Kênh Off Premise", "Kênh On Premise"
# Bậc 3a: trạng thái được tính, ngành trừ khỏi LPPC, ngành KPI 8, nhóm / mã KPI 5 – 6 KHÔNG còn là hằng ở đây — đọc từ luật kỳ (`period_rules`)

def period_rules(assumptions: dict) -> dict:
    """Giá trị THEO KỲ mà bộ đọc RAW cần — đọc từ khối `assumptions` của file luật kỳ: trạng thái đơn được tính, hạn xuất hoá đơn (công văn 6.1),
    ngành trừ khỏi LPPC, ngành của KPI 8, nhóm / mã của KPI 5 – 6. Không có giá trị mặc định: tháng sau đổi nhóm / mã mà file luật thiếu khoá thì
    mặc định theo tháng cũ sẽ ra số sai không ai thấy ⇒ thiếu / sai khoá ⇒ ValueError nêu đúng tên khoá."""
    counted = _names(assumptions, "counted_status", "trạng thái đơn nào được tính")
    unknown = sorted(set(counted) - khuon.CANONICAL_VALUES["order_status"])
    if unknown:
        raise ValueError(f"file luật kỳ: `counted_status` có trạng thái lạ {unknown} — chỉ nhận {sorted(khuon.CANONICAL_VALUES['order_status'])}")
    deadline = _required(assumptions, "invoice_deadline", "hạn xuất hoá đơn là ngày / giờ nào của tháng sau kỳ")
    whole = lambda value: isinstance(value, int) and not isinstance(value, bool)
    if not (isinstance(deadline, dict) and set(deadline) == {"day", "hour"} and whole(deadline["day"]) and whole(deadline["hour"])
            and 1 <= deadline["day"] <= 28 and 0 <= deadline["hour"] <= 23):
        raise ValueError("file luật kỳ: `invoice_deadline` phải là {day: 1–28, hour: 0–23} (ngày / giờ của tháng sau kỳ)")
    return {"counted_status": frozenset(counted), "invoice_deadline": (deadline["day"], deadline["hour"]),
            "lppc_excluded_subdiv": lppc_subdivs(assumptions)[0], "lppc_meat_subdiv": lppc_subdivs(assumptions)[1],
            "focus": {kpi: focus_spec(assumptions, kpi) for kpi in ("ASO_Focus", "ASO_Focus_2")},
            "allow_no_lines": _allowed_without_lines(assumptions)}


ALLOWANCE_KPIS = ("LPPC", "LPPC_Meat", "ASO_Focus", "ASO_Focus_2")     # các KPI mà luật kỳ nêu mã / nhóm / ngành


def _allowed_without_lines(assumptions: dict) -> dict[tuple[str, str], str]:
    """`cho_qua_khong_co_don` (không bắt buộc): mã / nhóm / ngành luật nêu mà kỳ này ĐÚNG LÀ không có dòng nào — mỗi mục phải có ghi chú vì sao
    (cùng khuôn "cho qua phải có ghi chú" của bảng ghép cột) và ghi rõ cho KPI nào: cùng một ngành có thể được nêu ở hai KPI (ngành thịt chế biến: KPI 3
    trừ, KPI 8 đếm) — cho qua ở KPI này không được lặng lẽ cho qua luôn ở KPI kia. Trả {(KPI, giá trị): ghi chú}."""
    entries = assumptions.get("cho_qua_khong_co_don", [])
    text = lambda value: isinstance(value, str) and bool(value.strip())
    if not isinstance(entries, list) or not all(isinstance(e, dict) and set(e) == {"kpi", "ma", "ghi_chu"} and e["kpi"] in ALLOWANCE_KPIS
                                                and text(e["ma"]) and text(e["ghi_chu"]) for e in entries):
        raise ValueError(f"file luật kỳ: `cho_qua_khong_co_don` phải là danh sách {{kpi: …, ma: …, ghi_chu: …}} — kpi là một trong {list(ALLOWANCE_KPIS)}, "
                         "ghi_chu bắt buộc (vì sao kỳ này không có dòng nào)")
    return {(entry["kpi"], entry["ma"]): entry["ghi_chu"] for entry in entries}


def rule_values_without_lines(lines: list[dict], rules: dict) -> tuple[list[dict], list[dict]]:
    """Mã / nhóm hàng / ngành mà luật kỳ nêu nhưng file KHÔNG có dòng nào (xét MỌI dòng của file, chưa lọc): gõ sai tên hay chép luật tháng trước
    sẽ làm KPI 5 / 6 / 8 ra 0, hoặc LPPC thôi trừ ngành, mà không ai thấy ⇒ dừng. Giá trị có trong `cho_qua_khong_co_don` (đúng KPI) thì cho qua.
    Trả (các giá trị được cho qua, các mục cho qua THỪA) — cả hai để bộ kiểm dữ liệu ghi dòng lưu ý. Mục thừa = luật không nêu giá trị đó cho KPI đó,
    hoặc file CÓ dòng mang giá trị đó: không làm sai số nên không dừng, nhưng phải thấy được (mục chép từ tháng trước, hoặc gõ nhầm mã).
    Trạng thái đơn không xét ở đây (RAW tháng 10 không có dòng Shipped nào mà luật vẫn đúng)."""
    wanted = [("LPPC", "KPI 3 (LPPC) trừ ngành", "SUB DIV", name) for name in rules["lppc_excluded_subdiv"]]
    wanted.append(("LPPC_Meat", "KPI 8 (LPPC_Meat) đếm ngành", "SUB DIV", rules["lppc_meat_subdiv"]))
    for title, kpi in (("KPI 5", "ASO_Focus"), ("KPI 6", "ASO_Focus_2")):
        spec = rules["focus"][kpi]
        wanted += [(kpi, f"{title} đếm {'mã hàng' if spec['field'] == 'SKU CODE' else 'nhóm hàng'}", spec["field"], value) for value in spec["values"]]
    present = {field: {line[field] for line in lines} for field in ("SUB DIV", "DMS OBJ", "SKU CODE")}
    allowed = rules["allow_no_lines"]
    missing = [(kpi, what, value) for kpi, what, field, value in wanted if value not in present[field]]
    blocked = [f"{what} «{value}»" for kpi, what, value in missing if (kpi, value) not in allowed]
    if blocked:
        raise ValueError(f"luật kỳ nêu {'; '.join(blocked)} nhưng file không có dòng nào mang giá trị đó — kiểm lại file luật kỳ hoặc file dữ liệu; "
                         "kỳ này đúng là không có thì ghi KPI + giá trị đó vào `cho_qua_khong_co_don` của file luật kèm ghi_chu")
    used = {(kpi, value) for kpi, _, value in missing}
    unused = [{"kpi": kpi, "value": value} for kpi, value in allowed if (kpi, value) not in used]
    return [{"what": what, "value": value, "note": allowed[(kpi, value)]} for kpi, what, value in missing], unused


def lppc_subdivs(assumptions: dict) -> tuple[tuple[str, ...], str]:
    """(ngành trừ khỏi KPI 3 LPPC, ngành của KPI 8 LPPC_Meat) theo luật kỳ — một chỗ đọc cho lõi và màn xem đơn."""
    return (tuple(_names(assumptions, "lppc_excluded_subdiv", "KPI 3 LPPC trừ những ngành (SUB DIV) nào")),
            _text(assumptions, "lppc_meat_subdiv", "KPI 8 đếm ngành (SUB DIV) nào"))


def focus_spec(assumptions: dict, kpi: str) -> dict:
    """KPI 5 / KPI 6 đếm theo mã hàng (SKU CODE) hay nhóm hàng (DMS OBJ) + tên dùng trong chữ "cách đếm".
    KPI 5: `aso_focus_groups` HOẶC `aso_focus_skus` (theo mã thì cần `aso_focus_label`). KPI 6: `aso_focus_2_scope: sku_list` ⇒ `aso_focus_2_skus`
    + `aso_focus_2_label`, KHÔNG lọc nhóm (mã thuộc nhóm nào cũng đếm — trước 3a lọc nhóm Homey rồi mới lọc mã). Phạm vi khác ⇒ đếm theo
    `aso_focus_2_groups`."""
    if kpi == "ASO_Focus":
        if "aso_focus_groups" in assumptions and "aso_focus_skus" in assumptions:
            raise ValueError("file luật kỳ: KPI 5 ghi cả `aso_focus_groups` lẫn `aso_focus_skus` — chỉ ghi một trong hai")
        by_sku, label_key = "aso_focus_skus" in assumptions, "aso_focus_label"
        values = _names(assumptions, "aso_focus_skus" if by_sku else "aso_focus_groups", "KPI 5 đếm nhóm hàng / mã nào")
    else:
        by_sku, label_key = _required(assumptions, "aso_focus_2_scope", "KPI 6 đếm theo mã hay cả nhóm") == "sku_list", "aso_focus_2_label"
        values = _names(assumptions, "aso_focus_2_skus" if by_sku else "aso_focus_2_groups", "KPI 6 đếm nhóm hàng / mã nào")
    # theo nhóm: tên = chính tên nhóm (khoá tên chỉ để ghi đè); theo mã: mã không phải tên đọc được ⇒ bắt buộc có tên
    label = _text(assumptions, label_key, f"{'KPI 5' if kpi == 'ASO_Focus' else 'KPI 6'} gọi nhóm mã này là gì trong chữ cách đếm") \
        if by_sku or label_key in assumptions else ", ".join(values)
    return {"field": "SKU CODE" if by_sku else "DMS OBJ", "values": tuple(values), "label": label}


def how_names(assumptions: dict) -> dict:
    """Tên nhóm / mã KPI 5 – 6 và ngành KPI 8 để lõi viết chữ "cách đếm" (`compute_kpi`). Đường RAW: lấy theo `focus_spec`. Đường sổ Tracking (khối
    pivot đã lọc sẵn, luật không có danh sách nhóm / mã): chỉ cần khoá tên `aso_focus_label`, `aso_focus_2_label`. Thiếu ⇒ ValueError nêu tên khoá."""
    names = {}
    for kpi, label_key, list_keys in (("ASO_Focus", "aso_focus_label", ("aso_focus_groups", "aso_focus_skus")),
                                      ("ASO_Focus_2", "aso_focus_2_label", ("aso_focus_2_groups", "aso_focus_2_skus"))):
        has_lists = any(key in assumptions for key in list_keys)
        names[kpi] = focus_spec(assumptions, kpi)["label"] if has_lists else _text(assumptions, label_key, "chữ cách đếm gọi nhóm hàng này là gì")
    names["LPPC_Meat"] = _text(assumptions, "lppc_meat_subdiv", "KPI 8 đếm ngành (SUB DIV) nào")
    return names


def in_focus(line: dict, spec: dict) -> bool:
    """Dòng có thuộc nhóm / mã KPI 5 – 6 (`focus_spec`) — một phép chọn cho lõi và các màn đếm lại ngoài lõi."""
    return line[spec["field"]] in spec["values"]


def _required(assumptions: dict, key: str, question: str):
    if key not in assumptions:
        raise ValueError(f"file luật kỳ thiếu khoá `{key}` (khối assumptions) — {question}? Ghi rõ ở file luật của kỳ (không có giá trị mặc định)")
    return assumptions[key]


def _names(assumptions: dict, key: str, question: str) -> list[str]:
    value = _required(assumptions, key, question)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"file luật kỳ: `{key}` phải là danh sách chữ khác rỗng, đang là {value!r}")
    return value


def _text(assumptions: dict, key: str, question: str) -> str:
    value = _required(assumptions, key, question)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"file luật kỳ: `{key}` phải là chữ khác rỗng, đang là {value!r}")
    return value


def invoice_deadline(period: str, rules: dict) -> datetime:
    """Công văn 6.1: "Đơn hàng phải được xuất hóa đơn trước 22h ngày 02 hàng tháng (T+1)" — ngày / giờ lấy từ luật kỳ (`period_rules`)."""
    year, month = map(int, period.split("-"))
    day, hour = rules["invoice_deadline"]
    return datetime(year + month // 12, month % 12 + 1, day, hour, 0)


def load_raw(path: str | Path, cutoff: date, period: str, one_pc_per_outlet_day: bool = True, count_free_items: bool = True,
             lppc_exclude_free_items: bool = False, late_kpis: tuple[str, ...] = (), pc_bt_excluded_subdiv: tuple[str, ...] = (),
             invoice_required_status: tuple[str, ...] | None = None, max_excluded_sales_pct: float | None = None, mapping: dict | None = None,
             *, period_rules: dict) -> dict:
    """RAW ⇒ {"blocks": {kpi: {"title", "filters", "rows"}}, "refreshed_at", "late_invoice_lines", "source": "raw"} (chưa có "mcp").

    Theo CHỮ công văn (2 trong 3 luật `pending_rules.py` "chưa kiểm được", nay RAW có dữ liệu từng dòng):
    - 6.1 hạn xuất hoá đơn: dòng có INVOICED DATE sau 22h ngày 02 tháng T+1 (hoặc không có) ⇒ không tính (RAW tháng 10: 1 đơn, 3 dòng, 8.560.000 đ — đã đếm 10/11).
    - 2.2 / 2.3 / 2.7 / 2.8 "tối đa 1 PC / ngày / cửa hàng" ⇒ `one_pc_per_outlet_day` mặc định BẬT (tắt chỉ để so với pivot Tracking — pivot không gộp).
    - `late_kpis` (mốc 7 N2, 16/11 — chuẩn mới: trang sếp đúng): các KPI CỘNG dòng hoá đơn xuất sau hạn 6.1 (dòng có ngày xuất) như trang làm;
      KPI khác vẫn bỏ. Trống ⇒ như cũ.
    - `pc_bt_excluded_subdiv` (mốc 7 N3, 16/11): ngành (SUB DIV) không tính là mã của PC_BT — trang sếp bỏ Bia. Trống ⇒ như cũ.
    - `invoice_required_status` (bậc 1 nạp chung, 17/11): trạng thái đơn CẦN ngày xuất hoá đơn mới được tính (luật kỳ); None ⇒ mọi trạng thái được tính
      (như trước). Trạng thái không cần ⇒ dòng trống ngày xuất vẫn tính (vd Shipped giữa tháng — câu hỏi sếp).
    - `max_excluded_sales_pct`: cổng đối soát — doanh số bị loại (trừ đơn sau ngày chốt) vượt % này của doanh số tới ngày chốt ⇒ DỪNG (`ValueError`)
      với số dòng + số tiền từng lý do. None ⇒ không dừng, vẫn trả kết quả đối soát (`reconciliation`).
    - `period_rules` (bậc 3a): giá trị theo kỳ của file luật (`period_rules(assumptions)`) — bắt buộc, không có mặc định.
    """
    all_lines, file_total = _read_rows(path, period, mapping=mapping)   # `mapping` (bậc 2): bảng ghép của file; None ⇒ bố cục RAW T10
    without_lines, unused_allowances = rule_values_without_lines(all_lines, period_rules)
    reasons = _exclude_reasons(all_lines, cutoff, period, invoice_required_status, period_rules)
    lines = _kept(all_lines, reasons, keep_late=bool(late_kpis))
    blocks = build_blocks(lines, one_pc_per_outlet_day, count_free_items, lppc_exclude_free_items, late_kpis, pc_bt_excluded_subdiv,
                          period_rules=period_rules)
    reconciliation = reconcile(all_lines, reasons, blocks, kpi1_takes_late="Turnover" in late_kpis, max_excluded_sales_pct=max_excluded_sales_pct,
                               file_total=file_total)
    return {"blocks": blocks, "refreshed_at": max((line["ORDER DATE"] for line in lines), default=None),
            "late_invoice_lines": sum(reason in ("no_invoice_date", "invoice_late") for reason in reasons), "source": "raw",
            "reconciliation": reconciliation, "rule_values_without_lines": without_lines, "unused_rule_allowances": unused_allowances}


def read_raw_lines(path: str | Path, cutoff: date, period: str, extra_columns: tuple[str, ...] = (), keep_late: bool = False,
                   invoice_required_status: tuple[str, ...] | None = None, mapping: dict | None = None, *, period_rules: dict) -> tuple[list[dict], int]:
    """Dòng RAW đã lọc như 8 KPI (ngày đơn ≤ ngày chốt, trạng thái được tính, hoá đơn đúng hạn 6.1) + số dòng trễ hạn (kể cả dòng không có ngày xuất).
    `extra_columns`: cột thêm cho phép tính khác (vd "TOTAL CASES" cho Thưởng thêm — 10/11) — tách riêng để 8 KPI KHÔNG đòi thêm cột.
    `keep_late` (mốc 7 N2): GIỮ dòng xuất hoá đơn sau hạn nhưng CÓ ngày xuất, mỗi dòng mang cờ `LATE`; người gọi tự chọn KPI nào dùng.
    Dòng không có ngày xuất mà trạng thái cần ngày xuất (`invoice_required_status`, None = mọi trạng thái) luôn bỏ."""
    lines, _file_total = _read_rows(path, period, extra_columns, mapping)
    reasons = _exclude_reasons(lines, cutoff, period, invoice_required_status, period_rules)
    return _kept(lines, reasons, keep_late), sum(reason in ("no_invoice_date", "invoice_late") for reason in reasons)


def _read_rows(path: str | Path, period: str, extra_columns: tuple[str, ...] = (), mapping: dict | None = None,
               check_values: bool = True) -> tuple[list[dict], float | None]:
    """MỌI dòng RAW có mã NV (đã kiểm cột / ô / kỳ — `_check`), chưa lọc gì + ô doanh số của dòng tổng cuối file (`SHIP TO` = "Total" — RAW xuất từ
    hệ thống có sẵn, T10 + T11 khớp tới đồng; nháp khuôn chuẩn 17/11) để cổng đối soát kiểm đọc đủ dòng. File không có dòng / cột đó ⇒ None."""
    # Bậc 2 bước 2.2 (17/11): đọc QUA bảng ghép — mặc định = bố cục RAW T10 (sheet Export, tiêu đề dòng 1, tên cột như NEEDED) ⇒ đường cũ y hệt.
    # Ghép dịch cột / giá trị của file về ĐÚNG khoá lõi đang dùng (tên cột T10, cờ "Y", kênh OFF / ON) ⇒ phần dưới không đổi.
    mapping = khuon.normalize_mapping({"table": "don_hang", **mapping}) if mapping is not None else khuon.default_mapping("don_hang")
    field_of = {field["default_column"]: field["field"] for field in khuon.schema("don_hang")}
    columns = NEEDED + tuple(name for name in extra_columns if name not in NEEDED)
    # Dòng thô của sheet lấy từ lớp đọc-một-lần (`raw_sheet`, 19/11): cùng file + sheet chỉ mở một lần cho mọi nơi gọi. Phần chọn cột / kiểm / lời
    # dừng bên dưới KHÔNG đổi (chỉ lùi lề vì hết khối try / finally đóng file) — xem `git diff -w`.
    sheet_names, sheet_rows = raw_sheet.sheet_rows(path, mapping["sheet"])
    sheet = mapping["sheet"]
    if isinstance(sheet, int):            # số thứ tự sheet 1-based (tên lạ bị che ở /bo-cuc)
        sheet = sheet_names[sheet - 1] if sheet <= len(sheet_names) else None
    if sheet_rows is None:
        # sheet của bảng ghép mặc định là tên đã biết ⇒ nêu được; sheet do người ghép chọn ⇒ không lặp chữ
        raise ValueError(f"RAW thiếu sheet '{sheet}' (có: {', '.join(sheet_names)})" if sheet == SHEET else "RAW thiếu sheet của bảng ghép")
    rows = iter(sheet_rows[mapping["header_row"] - 1:])   # như đọc từ dòng tiêu đề (đo 19/11: giống hệt, kể cả dòng trống trước / giữa dữ liệu)
    header = [str(name).strip() if name is not None else None for name in next(rows, ())]
    file_column = {name: mapping["columns"].get(field_of.get(name), name) for name in columns}
    # cột: tên ⇒ vị trí trong dòng tiêu đề; số ⇒ vị trí 1-based (phải nằm trong dòng tiêu đề)
    position = lambda column: (column - 1 if column <= len(header) else None) if isinstance(column, int) else \
        (header.index(column) if column in header else None)
    missing = [name for name in columns if position(file_column[name]) is None]
    if missing:
        raise ValueError(f"RAW thiếu cột: {', '.join(missing)}")
    # Kiểm nghĩa 1: trường ghép vào cột MANG TÊN cột chuẩn của TRƯỜNG KHÁC (vd trước CK ⇒ "Sales After Discount", MOQ ⇒ "FREE ITEM FLAG")
    default_of = {field["field"]: field["default_column"] for field in khuon.schema("don_hang")}
    for field, column in mapping["columns"].items():
        index = position(column)
        name = header[index] if index is not None and index < len(header) else None
        owner = next((other for other, default in default_of.items() if default == name and other != field), None)
        if owner is not None:
            raise ValueError(f"bảng ghép: trường {field} đang ghép vào cột chuẩn của trường {owner} — kiểm lại ghép cột")
    # cột chỉ để kiểm nghĩa tiền (không bắt buộc): ghép được / có trong file ⇒ đọc
    for field in ("sales_after_discount", "discount_value"):
        name = default_of[field]
        if name not in columns and position(mapping["columns"].get(field, name)) is not None:
            columns = columns + (name,)
            file_column[name] = mapping["columns"].get(field, name)
    at = {name: position(file_column[name]) for name in columns}
    to_canonical = {name: mapping["values"].get(field_of.get(name), {}) for name in columns}
    # "_row" = số dòng Excel — để lời lỗi chỉ đúng dòng mà không in nội dung ô
    lines, total_lines, stray = [], [], []
    for index, values in enumerate(rows, start=mapping["header_row"] + 1):
        line = {**{name: values[i] if i < len(values) else None for name, i in at.items()}, "_row": index}
        for name, pairs in to_canonical.items():
            if pairs and line[name] in pairs:
                line[name] = pairs[line[name]]
        if line["SM CODE"]:
            lines.append({**line, "OUTLET CODE": str(line["OUTLET CODE"])})   # mã cửa hàng = chữ như MCP JSON
        elif any(isinstance(cell, str) and cell.strip() == "Total" for cell in values):
            # dòng tổng cuối file: không mã NV + một ô ghi đúng "Total" (T10 / T11: cột SHIP TO) — không dựa tên cột (file đã ghép có thể đổi tên)
            total_lines.append(line)
        elif any((_is_number(line[name]) and line[name] != 0) or (isinstance(line[name], str) and line[name].strip())
                 for name in MONEY_COLUMNS if name in line):
            # Bẫy (2) Capitol 18/11: dòng KHÔNG mã NV mà có tiền (không phải dòng Total) trước bị bỏ lặng ⇒ tổng lệch không ai biết.
            # Ô tiền có CHỮ cũng tính (dbf1130d: "384,00" ở dòng không mã lọt). RAW thật T10 + T11: 0 dòng như vậy (B + đã đếm 18/11).
            stray.append(index)
        # dòng không mã NV khác (dòng trống, "Applied filters") bỏ như trước (RAW tháng 10: 3 dòng)
    if stray:
        raise ValueError(f"RAW có {len(stray)} dòng không có mã nhân viên (SM CODE) mà có doanh số (dòng Excel {_rows_text(stray)}) — "
                         "không biết của ai; sửa file rồi chạy lại")
    _check(lines, period)          # khuôn ô / kỳ / trạng thái trước (lời cũ cho file hỏng); sau đó mới tới nghĩa cột + giá trị mới
    _check_meaning(lines)
    file_total = _file_total(lines, total_lines)   # sau kiểm từng dòng: ô chữ ⇒ lời "không phải số" (đúng chỗ), không phải lời lệch tổng
    unknown = unknown_values(lines, mapping) if check_values else {}   # check_values=False chỉ cho màn ghép ĐẾM giá trị mới (không tính gì)
    if unknown:
        # chỉ nêu TÊN TRƯỜNG + số giá trị / dòng — không in giá trị (chữ của file — U1); màn ghép xem qua mã che (ghep-thu)
        detail = "; ".join(f"{field}: {len(counts)} giá trị / {sum(counts.values())} dòng" for field, counts in unknown.items())
        raise ValueError(f"RAW có giá trị chưa biết — {detail}. Ghép giá trị (kênh, cờ) hoặc cho qua ngành mới ở màn ghép cột; không tính tiếp")
    return lines, file_total


MONEY_COLUMNS = ("Sales Before Discount", "Sales After Discount", "Discount value")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _rows_text(rows: list[int]) -> str:
    return ", ".join(map(str, rows[:10])) + (" …" if len(rows) > 10 else "")


def _file_total(lines: list[dict], total_lines: list[dict]) -> float | None:
    """Ô trước chiết khấu của dòng Total (cổng đối soát so với Σ dòng) — kèm kiểm dòng Total (bẫy (2) Capitol 18/11):
    hai dòng Total ⇒ không biết dòng nào ⇒ dừng; ô trước chiết khấu không phải số ⇒ dừng (trước đây coi như "không có dòng Total", chỉ ghi chú,
    nạp được); có cột sau chiết khấu ⇒ Σ sau chiết khấu phải bằng ô của dòng Total (tới đồng). Không có dòng Total ⇒ None (như cũ — có ghi chú)."""
    if not total_lines:
        return None
    if len(total_lines) > 1:
        raise ValueError(f"RAW có {len(total_lines)} dòng 'Total' (dòng Excel {_rows_text([t['_row'] for t in total_lines])}) — chỉ được một; sửa file")
    row = total_lines[0]
    if not _is_number(row["Sales Before Discount"]):
        raise ValueError(f"dòng 'Total' (dòng Excel {row['_row']}) có ô 'Sales Before Discount' trống hoặc không phải số — không đối chiếu được tổng; sửa file")
    if "Sales After Discount" in row:
        # cột có trong file ⇒ ô của dòng Total phải là số; TRỐNG cũng dừng (dbf1130d: ô trống ⇒ bỏ qua phép so ⇒ nạp được)
        after = row["Sales After Discount"]
        if not _is_number(after):
            raise ValueError(f"dòng 'Total' (dòng Excel {row['_row']}) có ô 'Sales After Discount' trống hoặc không phải số — không đối chiếu được tổng; sửa file")
        lines_after = sum((Decimal(str(line["Sales After Discount"])) for line in lines if _is_number(line.get("Sales After Discount"))), Decimal(0))
        # lệch từ NỬA đồng ⇒ dừng (abs, không round: round(Decimal) làm tròn về số chẵn — nhóm kỹ thuật). Ngưỡng 0,5 vì: RAW thật có ô lẻ
        # (T10 3.520 ô, T11 682 ô) và Σ − Total chỉ là nhiễu số thực (T10 −0,000062 đ, T11 0,0000002 đ — đã đếm 18/11) ⇒ cần ngưỡng; < 0,5 giữ
        # luật "đúng tới đồng" (lệch 0,5 đ ⇒ dừng — ca giữ nguyên kỳ vọng).
        if abs(lines_after - Decimal(str(after))) >= Decimal("0.5"):
            raise ValueError(f"{RECONCILE_STOP}: tổng doanh số SAU chiết khấu các dòng đọc được {_dots(round(lines_after))} đ ≠ dòng 'Total' của file "
                             f"{_dots(round(Decimal(str(after))))} đ — file thiếu / thừa dòng hoặc ô tiền đọc sai; kiểm lại file")
    return row["Sales Before Discount"]


def _check_meaning(lines: list[dict]) -> None:
    """Kiểm NGHĨA cột sau khi ghép (bất biến đo ở `khuon` — đúng 100 % dòng T10 + T11). Sai ⇒ dừng, chỉ nêu số dòng (không in ô)."""
    number = lambda value: value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    # Bẫy (1) Capitol 18/11: ô tiền dạng CHỮ (vd "384,00" — số kiểu Việt) ở cột sau chiết khấu / tiền chiết khấu bị các phép kiểm dưới BỎ QUA
    # (number() ⇒ None) ⇒ file nạp được, cột đó dùng ở Chi tiết, Hội viên, 1 % gia vị. Không đoán nghĩa "384,00" (384 hay 38.400 — dấu phẩy là thập
    # phân hay phân nghìn?) ⇒ DỪNG, nêu dòng. Cột trước chiết khấu đã kiểm ở `_check`. RAW thật T10 + T11: 100 % ô hai cột này là số.
    for column in ("Sales After Discount", "Discount value"):
        if lines and column in lines[0]:
            text = [line["_row"] for line in lines if number(line[column]) is None]
            if text:
                raise ValueError(f"RAW có {len(text)} dòng ô '{column}' trống hoặc không phải số (dòng Excel {_rows_text(text)}) — "
                                 "ô tiền phải là SỐ, không phải chữ kiểu \"1.234,00\"; sửa file rồi chạy lại")
    problems = []
    if "Sales After Discount" in (lines[0] if lines else {}):
        pairs = [(number(line["Sales Before Discount"]), number(line["Sales After Discount"]), line) for line in lines]
        below = [line["_row"] for before, after, line in pairs if before is not None and after is not None and before < after - khuon.MONEY_TOLERANCE]
        if below:
            problems.append(f"{len(below)} dòng doanh số trước chiết khấu nhỏ hơn sau chiết khấu (dòng Excel {below[0]} …) — hai cột tiền ghép đảo?")
        if "Discount value" in lines[0]:
            off = [line["_row"] for before, after, line in pairs if before is not None and after is not None and number(line["Discount value"]) is not None
                   and abs(before - after - line["Discount value"]) >= khuon.MONEY_TOLERANCE]
            if off:
                problems.append(f"{len(off)} dòng trước − sau chiết khấu ≠ tiền chiết khấu (dòng Excel {off[0]} …) — cột tiền ghép nhầm?")
    paid_gift = [line["_row"] for line in lines if line["FREE ITEM FLAG"] == "Y" and (number(line["Sales Before Discount"]) or 0) > khuon.MONEY_TOLERANCE]
    if paid_gift:
        problems.append(f"{len(paid_gift)} dòng cờ hàng tặng mà có doanh số (dòng Excel {paid_gift[0]} …) — cờ hàng tặng ghép nhầm?")
    if len(lines) >= khuon.FLAG_RATE_MIN_LINES:
        column_of = {"moq_flag": "MOQ_FLAG", "is_free_item": "FREE ITEM FLAG"}
        for field, (side, limit) in khuon.FLAG_RATE_RULES.items():
            rate = 100 * sum(line[column_of[field]] == "Y" for line in lines) / len(lines)
            if (side == "min" and rate < limit) or (side == "max" and rate > limit):
                problems.append(f"tỉ lệ cờ Y của {field} là {_pct_text(round(rate, 2))} % ({'dưới' if side == 'min' else 'trên'} {limit} %) — cờ ghép nhầm / đổi chỗ?")
    if problems:
        raise ValueError("RAW sai nghĩa cột sau khi ghép — " + "; ".join(problems) + ". Kiểm lại ghép cột, không tính tiếp")


def unknown_values(lines: list[dict], mapping: dict) -> dict:
    """{trường: {giá trị: số dòng}} các giá trị ngoài tập đã biết (`khuon.KNOWN_VALUES` + ngành người đã cho qua) — sau khi đã ghép giá trị."""
    column_of = {field["field"]: field["default_column"] for field in khuon.schema("don_hang")}
    accepted = mapping.get("accepted_values", {})
    found = {}
    for field, known in khuon.KNOWN_VALUES.items():
        allowed = known | set(accepted.get(field, ()))
        counts = defaultdict(int)
        for line in lines:
            value = line.get(column_of[field])
            if value not in allowed and not (field == "sub_div" and line.get("FREE ITEM FLAG") == "Y" and _blank(value)):
                counts[value] += 1
        if counts:
            found[field] = dict(counts)
    return found


def _exclude_reasons(lines: list[dict], cutoff: date, period: str, invoice_required_status: tuple[str, ...] | None,
                     period_rules: dict) -> list[str | None]:
    """Lý do loại từng dòng (khoá của EXCLUDE_REASONS) hoặc None = đúng hạn. Thứ tự xét = thứ tự lõi lọc từ trước (ngày chốt ⇒ trạng thái ⇒ hạn 6.1)
    — `backend/order_lines.py` đi cùng thứ tự. "invoice_late" = CÓ ngày xuất nhưng sau hạn (mốc 7: KPI trong late_invoice_kpis vẫn tính)."""
    deadline = invoice_deadline(period, period_rules)
    counted = period_rules["counted_status"]
    required = counted if invoice_required_status is None else set(invoice_required_status)
    reasons = []
    for line in lines:
        if _day(line["ORDER DATE"]) > cutoff:
            reasons.append("after_cutoff")
        elif line["ORDER STATUS"] not in counted:
            reasons.append("status_not_counted")
        elif not isinstance(line["INVOICED DATE"], datetime):
            reasons.append("no_invoice_date" if line["ORDER STATUS"] in required else None)
        elif line["INVOICED DATE"] > deadline:
            reasons.append("invoice_late")
        else:
            reasons.append(None)
    return reasons


def _kept(lines: list[dict], reasons: list[str | None], keep_late: bool) -> list[dict]:
    """Dòng được tính (lý do None); `keep_late` ⇒ giữ cả dòng "invoice_late", mọi dòng mang cờ `LATE`."""
    if not keep_late:
        return [line for line, reason in zip(lines, reasons) if reason is None]
    return [{**line, "LATE": reason == "invoice_late"} for line, reason in zip(lines, reasons) if reason in (None, "invoice_late")]


def reconcile(lines: list[dict], reasons: list[str | None], blocks: dict, kpi1_takes_late: bool, max_excluded_sales_pct: float | None,
              file_total: float | None = None) -> dict:
    """Cổng đối soát (bậc 1 nạp chung — đề xuất C, 17/11), nhìn từ KPI 1 (doanh số — mọi dòng được tính đều vào KPI 1, không lọc tuyến từ mốc 7):
    dòng vào = dòng tính + Σ dòng loại theo từng lý do; Σ doanh số file − Σ loại = Σ khối Turnover (tới đồng). Lệch ⇒ lỗi code (dừng). Doanh số loại
    (trừ đơn sau ngày chốt — loại có chủ đích) ÷ doanh số tới ngày chốt > `max_excluded_sales_pct` ⇒ DỪNG, nêu số dòng + tiền từng lý do.
    """
    sales = lambda line: Decimal(str(line["Sales Before Discount"]))
    excluded = defaultdict(lambda: [0, Decimal(0)])
    counted_lines, counted_sales = 0, Decimal(0)
    for line, reason in zip(lines, reasons):
        if reason is None or (reason == "invoice_late" and kpi1_takes_late):
            counted_lines, counted_sales = counted_lines + 1, counted_sales + sales(line)
        else:
            excluded[reason][0] += 1
            excluded[reason][1] += sales(line)
    input_sales = sum((sales(line) for line in lines), Decimal(0))
    turnover = sum((Decimal(str(row["Total"])) for row in blocks["Turnover"]["rows"]), Decimal(0))
    if len(lines) != counted_lines + sum(n for n, _ in excluded.values()) or abs(input_sales - sum(s for _, s in excluded.values()) - turnover) >= 1:
        raise ValueError("cổng đối soát RAW: số dòng / doanh số vào ≠ tính + loại — lỗi code bộ nạp, báo người phụ trách")
    # Dòng "Total" của file: Σ doanh số các dòng đọc được phải bằng nó (làm tròn tới đồng) — lệch = đọc thiếu / sai dòng hoặc kiểu số ⇒ dừng
    total_matches = None if file_total is None else round(input_sales) == round(Decimal(str(file_total)))
    if total_matches is False:
        raise ValueError(f"{RECONCILE_STOP}: tổng doanh số các dòng đọc được {_dots(round(input_sales))} đ ≠ dòng 'Total' của file "
                         f"{_dots(round(Decimal(str(file_total))))} đ — file thiếu / thừa dòng hoặc ô tiền đọc sai; kiểm lại file")
    # Ngưỡng chỉ tính dòng loại NGOÀI chủ đích: đơn sau ngày chốt + đơn trạng thái không tính (huỷ / nháp) là loại có chủ đích, không cộng
    deliberate = ("after_cutoff", "status_not_counted")
    floor2 = lambda part, whole: float((part * 100 / whole).quantize(Decimal("0.01"), ROUND_FLOOR)) if whole else 0.0
    within = input_sales - sum((excluded[reason][1] for reason in deliberate if reason in excluded), Decimal(0))
    lost = sum((s for reason, (_, s) in excluded.items() if reason not in deliberate), Decimal(0))
    pct = floor2(lost, within)
    items = [{"reason": reason, "label": EXCLUDE_REASONS[reason], "lines": n, "sales": float(s)} for reason, (n, s) in excluded.items()]
    # Theo TỪNG NV: đội < ngưỡng mà một người mất 15–56 % vẫn lọt (một người rơi mốc tiền không ai báo) ⇒ cùng ngưỡng cho mỗi NV
    by_person = defaultdict(lambda: [Decimal(0), Decimal(0), 0])        # mã NV ⇒ [doanh số tới ngày chốt, doanh số mất, dòng mất]
    for line, reason in zip(lines, reasons):
        if reason in deliberate:
            continue
        person = by_person[str(line["SM CODE"])]
        person[0] += sales(line)
        if reason is not None and not (reason == "invoice_late" and kpi1_takes_late):
            person[1] += sales(line)
            person[2] += 1
    over_people = sorted((code, n, s, floor2(s, total)) for code, (total, s, n) in by_person.items()
                         if max_excluded_sales_pct is not None and floor2(s, total) > max_excluded_sales_pct)
    ok = max_excluded_sales_pct is None or (pct <= max_excluded_sales_pct and not over_people)
    if not ok:
        detail = "; ".join(f"{item['label']}: {_dots(item['lines'])} dòng / {_dots(round(item['sales']))} đ" for item in items if item["reason"] not in deliberate)
        people = "; ".join(f"{code}: {_dots(n)} dòng / {_dots(round(s))} đ ({_pct_text(p)} %)" for code, n, s, p in over_people)
        raise ValueError(f"{RECONCILE_STOP}: bị loại {_pct_text(pct)} % doanh số tới ngày chốt của đội"
                         + (f" (vượt ngưỡng {_pct_text(max_excluded_sales_pct)} % của luật kỳ)" if pct > max_excluded_sales_pct else "")
                         + (f"; {len(over_people)} NV mất quá {_pct_text(max_excluded_sales_pct)} % doanh số riêng — {people}" if over_people else "")
                         + f" — {detail}. Kiểm lại file hoặc luật kỳ (trạng thái nào cần ngày xuất hoá đơn), không tính tiếp")
    return {"input_lines": len(lines), "input_sales": float(input_sales), "counted_lines": counted_lines, "counted_sales": float(counted_sales),
            "excluded": items, "excluded_pct": pct, "max_excluded_sales_pct": max_excluded_sales_pct, "ok": ok,
            "file_total_sales": file_total, "file_total_matches": total_matches,
            # không có dòng Total ⇒ lời cho người dùng (vào cờ dữ liệu của bản — `queries._data_flags`), không chỉ trường meta
            "note": "file không có dòng 'Total' — không đối chiếu được tổng file (vẫn kiểm số dòng + KPI 1)" if total_matches is None else None}


def _dots(number: int) -> str:
    return f"{number:,}".replace(",", ".")


def _pct_text(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _day(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def _blank(value) -> bool:
    """Ô trống: không có giá trị, hoặc chữ chỉ gồm khoảng trắng."""
    return value is None or (isinstance(value, str) and not value.strip())


def _check(lines: list[dict], period: str) -> None:
    # Doanh số trống / không phải số ⇒ DỪNG (luật User "lỗi nặng ⇒ dừng hẳn, báo rõ, không bỏ lặng"). 10/11: ô trống từng bị `or 0` tính là 0 đ, 0 cờ,
    # tiền đổi (−200.000 đ, một NV rớt mốc); ô chữ ⇒ TypeError kỹ thuật. Không in giá trị ô (ô lỗi có thể chứa tên / địa chỉ cửa hàng). Hàng tặng = 0 là số ⇒ qua.
    bad_sales = [line["_row"] for line in lines if isinstance(line["Sales Before Discount"], bool)
                 or not isinstance(line["Sales Before Discount"], (int, float))]
    if bad_sales:
        shown = ", ".join(map(str, bad_sales[:10])) + (" …" if len(bad_sales) > 10 else "")
        raise ValueError(f"RAW có {len(bad_sales)} dòng ô 'Sales Before Discount' trống hoặc không phải số (dòng Excel {shown}) — sửa file rồi chạy lại")
    # Dòng BÁN (không phải hàng tặng) mà trống tên nhóm hàng `DMS OBJ` ⇒ DỪNG (12/11 — người soát bên Capitol đọc code: mọi phép chọn theo nhóm —
    # ASO_Focus, ASO_Focus_2, Thưởng thêm chọn theo nhóm, 1 % gia vị — so `line["DMS OBJ"] == tên nhóm` nên dòng trống bị bỏ LẶNG LẼ, không cờ, tiền có
    # thể thiếu). Hàng tặng trống nhóm vẫn qua: RAW tháng 10 có đúng 3 dòng trống, đều FREE ITEM FLAG = Y, 0 đ (đã đếm 12/11) ⇒ số tháng 10 không đổi.
    # Chỉ nêu số dòng Excel, không in nội dung ô.
    no_group = [line["_row"] for line in lines if line["FREE ITEM FLAG"] != "Y" and _blank(line["DMS OBJ"])]
    if no_group:
        shown = ", ".join(map(str, no_group[:10])) + (" …" if len(no_group) > 10 else "")
        raise ValueError(f"RAW có {len(no_group)} dòng bán (không phải hàng tặng) trống tên nhóm hàng 'DMS OBJ' (dòng Excel {shown}) — "
                         "không biết xếp vào nhóm nào; sửa file rồi chạy lại")
    bad_date = [line["ORDER DATE"] for line in lines if not isinstance(line["ORDER DATE"], (date, datetime))
                or f"{_day(line['ORDER DATE']):%Y-%m}" != period]
    if bad_date:
        # Lời cho người sửa file: ngày in dd/mm/yyyy ( RAW 10/11, minor — bản đầu in "datetime.datetime(2026, 11, 2, 0, 0)"); ô không phải ngày in nguyên
        example = f"{_day(bad_date[0]):%d/%m/%Y}" if isinstance(bad_date[0], (date, datetime)) else repr(bad_date[0])
        raise ValueError(f"RAW có {len(bad_date)} dòng ngày đơn ngoài kỳ {period} hoặc không phải ngày (vd {example})")
    # Trạng thái "lạ" = ngoài tập trạng thái đã biết (`khuon`), không phụ thuộc luật kỳ: luật kỳ chỉ chọn trạng thái nào được TÍNH (`counted_status`)
    statuses = {line["ORDER STATUS"] for line in lines} - khuon.CANONICAL_VALUES["order_status"]
    if statuses:
        raise ValueError(f"RAW có trạng thái đơn lạ: {sorted(map(str, statuses))} — chưa biết có tính không")


def build_blocks(lines: list[dict], one_pc_per_outlet_day: bool = False, count_free_items: bool = True, lppc_exclude_free_items: bool = False,
                 late_kpis: tuple[str, ...] = (), pc_bt_excluded_subdiv: tuple[str, ...] = (), *, period_rules: dict) -> dict:
    """8 khối pivot từ dòng RAW đã lọc kỳ / ngày chốt / trạng thái. Line = một dòng RAW (một mã hàng của một đơn) đạt MOQ.

    Hai cách đếm đang THỬ (so mù với trang sếp — mặc định = cách pivot Tracking):
    - `one_pc_per_outlet_day`: công văn 2.2 / 2.3 / 2.7 / 2.8 "tối đa 1 PC mỗi ngày mỗi cửa hàng" (pending_rules — RAW có ngày + cửa hàng) ⇒ gộp đơn cùng cửa hàng cùng ngày.
    - `count_free_items=False`: dòng hàng tặng (FREE ITEM FLAG = Y) không là "line" (đếm line / PC) — doanh số vẫn tính mọi dòng.
    - `late_kpis` (mốc 7 N2): KPI dùng cả dòng mang cờ `LATE` (hoá đơn trễ, xem `read_raw_lines`); KPI khác chỉ dùng dòng đúng hạn.
    - `pc_bt_excluded_subdiv` (mốc 7 N3): dòng thuộc các ngành này không là mã của PC_BT (trang sếp bỏ Bia; công văn 2.2 không loại).
      Tên ngành không có trong dữ liệu ⇒ dừng (gõ sai sẽ lặng lẽ thôi loại).
    - `period_rules` (bậc 3a): trạng thái được tính, ngành trừ khỏi LPPC, ngành KPI 8, nhóm / mã KPI 5 – 6 của kỳ (`period_rules`).
    """
    unknown = sorted(set(pc_bt_excluded_subdiv) - {line["SUB DIV"] for line in lines})
    if unknown:
        raise ValueError(f"luật PC_BT bỏ ngành không có trong RAW: {', '.join(map(str, unknown))} — kiểm lại tên ngành (SUB DIV)")
    on_time = [line for line in lines if not line.get("LATE")]
    source = lambda kpi: lines if kpi in late_kpis else on_time
    moq = lambda kpi: [line for line in source(kpi) if line["MOQ_FLAG"] == "Y"]
    counted = lambda kpi: moq(kpi) if count_free_items else [line for line in moq(kpi) if line.get("FREE ITEM FLAG") != "Y"]
    by_order = lambda rows: _lines_by_order(rows, one_pc_per_outlet_day)
    # LPPC / LPPC_Meat: 10/11 bỏ dòng hàng tặng khỏi "line" (căn cứ 8.1 "số lượng MUA tối thiểu"; công văn không nói thẳng; khớp trang sếp 12/14)
    lppc_lines = lambda kpi: [line for line in counted(kpi) if line.get("FREE ITEM FLAG") != "Y"] if lppc_exclude_free_items else counted(kpi)
    lppc_out, meat = period_rules["lppc_excluded_subdiv"], period_rules["lppc_meat_subdiv"]
    focus, focus_2 = period_rules["focus"]["ASO_Focus"], period_rules["focus"]["ASO_Focus_2"]
    return {
        # 11/11 (QĐ-09, công văn 6.2): gom theo (NV, cửa hàng) để lọc được khách ngoài MCP; tổng mỗi NV không đổi
        "Turnover": _block("KPI 01", {"ORDER STATUS": sorted(period_rules["counted_status"])}, _sales_by(source("Turnover"), ("SM CODE", "OUTLET CODE"))),
        "PC_BT": _block("KPI 02", {"L1": OFF, "MOQ_FLAG": "Y", **({"SUB DIV": f"trừ {', '.join(sorted(pc_bt_excluded_subdiv))}"} if pc_bt_excluded_subdiv else {})},
                        by_order([l for l in counted("PC_BT") if l["L1"] == OFF and l["SUB DIV"] not in pc_bt_excluded_subdiv])),
        "LPPC": _block("KPI 03", {"SUB DIV": f"trừ {', '.join(sorted(lppc_out))}", "MOQ_FLAG": "Y"},
                       by_order([l for l in lppc_lines("LPPC") if l["SUB DIV"] not in lppc_out])),
        "ASO_ALL": _block("KPI 4", {"MOQ_FLAG": "Y"}, _sales_by(moq("ASO_ALL"), ("SM CODE", "OUTLET CODE"))),
        "ASO_Focus": _block("KPI 5", {"MOQ_FLAG": "Y", **_focus_filter(focus)},
                            _sales_by([l for l in moq("ASO_Focus") if in_focus(l, focus)], ("SM CODE", "OUTLET CODE"))),
        "ASO_Focus_2": _block("KPI 6", {"MOQ_FLAG": "Y", **_focus_filter(focus_2)},
                              _sales_by([l for l in moq("ASO_Focus_2") if in_focus(l, focus_2)], ("SM CODE", "OUTLET CODE", "SKU CODE"))),
        "PC_ON": _block("KPI 07", {"L1": ON, "MOQ_FLAG": "Y"}, by_order([l for l in counted("PC_ON") if l["L1"] == ON])),
        "LPPC_Meat": _block("KPI 08", {"SUB DIV": meat, "MOQ_FLAG": "Y"}, by_order([l for l in lppc_lines("LPPC_Meat") if l["SUB DIV"] == meat])),
    }


def _focus_filter(spec: dict) -> dict:
    """Ô "bộ lọc" của khối KPI 5 / 6 (như ô lọc pivot): đúng phép chọn của luật kỳ — theo nhóm hàng hoặc theo mã hàng."""
    return {spec["field"]: ", ".join(spec["values"])}


def _block(title: str, filters: dict, rows: list[dict]) -> dict:
    return {"title": title, "filters": filters, "rows": rows}


def _sales_by(lines: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """Một dòng mỗi nhóm `keys`, Total = tổng Sales Before Discount (như pivot "Sum of …")."""
    total = defaultdict(float)
    for line in lines:
        total[tuple(line[key] for key in keys)] += line["Sales Before Discount"] or 0
    return [{**dict(zip(keys, group)), "Total": amount} for group, amount in total.items()]


def _lines_by_order(lines: list[dict], one_pc_per_outlet_day: bool = False) -> list[dict]:
    """Một dòng mỗi ĐƠN (SM, đơn, cửa hàng), Total = số line — như pivot "Count of …" theo ORDER NUM.
    `one_pc_per_outlet_day` (công văn 2.2 / 2.3 / 2.7 / 2.8): line = MÃ HÀNG KHÁC NHAU trong đơn (8.1 "mỗi mã hàng trên đơn … là 1 Line") và dòng
    mang thêm "DAY" (ngày đơn). Ngưỡng "≥ N line" xét TỪNG ĐƠN ở compute_kpi, RỒI mới giới hạn 1 PC / ngày / cửa hàng ( 10/11: gộp đơn trước
    rồi xét ngưỡng ⇒ 2 đơn 1 + 3 mã thành 1 PC — sai chữ "Một đơn hàng phải có ít nhất 4 line").
    """
    skus = defaultdict(set)
    count = defaultdict(int)
    for line in lines:
        key = (line["SM CODE"], line["ORDER NUM"], line["OUTLET CODE"], _day(line["ORDER DATE"]))
        count[key] += 1
        skus[key].add(line["SKU CODE"])
    if not one_pc_per_outlet_day:
        return [{"SM CODE": sm, "ORDER NUM": order, "OUTLET CODE": outlet, "Total": n} for (sm, order, outlet, _), n in count.items()]
    return [{"SM CODE": sm, "ORDER NUM": order, "OUTLET CODE": outlet, "DAY": day, "Total": len(skus[key])}
            for key in count for sm, order, outlet, day in [key]]


def raw_employee_names(raw_path: str | Path, mapping: dict | None = None) -> dict[str, str]:
    """{mã SM: họ tên đủ} đọc từ cột SM_NAME của RAW. Nằm ở gói core (12/11): backend (1 % gia vị, tab Theo dõi) cần hàm này lúc chạy, mà thư mục
    `scripts/` không thuộc gói chạy của hộp Capitol ⇒ `/api/system/spice-loyalty` từng 500 ModuleNotFoundError. `scripts/` gọi lại từ đây.
    `mapping` (bậc 2 bước 2.5): bảng ghép của file đã ghép cột — sheet, dòng tiêu đề, cột sm_code / sm_name như `_read_rows`; None = bố cục RAW T10."""
    mapping = khuon.normalize_mapping({"table": "don_hang", **mapping}) if mapping is not None else khuon.default_mapping("don_hang")
    _sheet_names, sheet_rows = raw_sheet.sheet_rows(raw_path, mapping["sheet"])   # dùng chung lần đọc của `_read_rows` (19/11)
    if sheet_rows is None:
        raise ValueError("RAW thiếu sheet của bảng ghép")
    rows = iter(sheet_rows[mapping["header_row"] - 1:])
    header = [str(name).strip() if name is not None else None for name in next(rows, ())]
    # cột ghép theo tên ⇒ vị trí trong dòng tiêu đề; theo số ⇒ vị trí 1-based (như `_read_rows`)
    at = lambda column: column - 1 if isinstance(column, int) else header.index(column)
    code_at, name_at = at(mapping["columns"].get("sm_code", "SM CODE")), at(mapping["columns"].get("sm_name", "SM_NAME"))
    return {values[code_at]: str(values[name_at]).strip() for values in rows if len(values) > name_at and values[code_at]}


def name_to_code(page_name: str, names: dict[str, str]) -> str:
    """Tên trên trang theo dõi của đội (các chữ cuối của họ tên) ⇒ mã SM; không ghép được đúng MỘT người ⇒ dừng."""
    hits = [code for code, full in names.items() if full.split()[-len(page_name.split()):] == page_name.split()]
    if len(hits) != 1:
        raise ValueError(f"tên '{page_name}' trên trang ghép được {len(hits)} mã SM (cần đúng 1)")
    return hits[0]


def load_mcp_json(path: str | Path) -> dict:
    """Danh sách điểm bán trong tuyến {sm_code: {outlet_code: {...}}} — file JSON tách từ trang sếp (`scripts/tach_mcp_trang_sep.py`)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # Kênh theo cách ghi của sổ / RAW ("Off" ⇒ "Kênh Off Premise") để mọi chỗ đọc MCP hiểu như nhau; không có số lần viếng thăm / tháng
    channel = {"Off": OFF, "On": ON}
    return {sm: {outlet: {**info, "channel": channel.get(info.get("channel"), info.get("channel"))} for outlet, info in outlets.items()}
            for sm, outlets in data["mcp"].items()}
