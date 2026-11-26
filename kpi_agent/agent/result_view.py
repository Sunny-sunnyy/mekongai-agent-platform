"""Kết quả một lần gọi công cụ ⇒ hình để TRANG vẽ: ô đơn + bảng + ghi chú (User: "mô tả trợ lý gọi công cụ nào, tham số gì, kết quả trả về").

Chỉ SẮP LẠI đúng kết quả đã gửi cho mô hình (backend đã che mã) — không tính gì mới, không đọc thêm dữ liệu, không đổi chữ gửi mô hình.
Có trần (bảng dài không đổ hết về trình duyệt): `MAX_ROWS` dòng / bảng kèm `total_rows`, `MAX_COLUMNS` cột kèm `hidden_columns`."""
MAX_ROWS, MAX_COLUMNS, MAX_TABLES, MAX_FACTS, MAX_NOTES, MAX_TEXT = 50, 12, 4, 16, 10, 200
SHORT_TEXT = 20   # chữ dài hơn mức này coi là câu, không phải mã
NAME_KEYS = ("name", "sm_code")   # cột tên thật backend gắn cạnh mã che khi KPI_NAMES=on (agent/names.py)
# Khoá của phong bì kết quả (kỳ, nguồn, nhãn trạng thái…) — đã hiện ở dòng "Nguồn" của câu trả lời, không lặp lại trong thẻ công cụ
ENVELOPE_KEYS = {"period", "cutoff", "params_used", "data_flags", "rule_version", "status", "status_label", "status_label_for_model", "refused"}


def _is_scalar(value) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _cell(value):
    return value[:MAX_TEXT] + "…" if isinstance(value, str) and len(value) > MAX_TEXT else value


def _flat(item: dict, left: list) -> dict:
    """Một đối tượng ⇒ {cột: ô}: ô đơn giữ nguyên; đối tượng con một tầng ⇒ "cha.con"; danh sách chữ / số ngắn ⇒ nối bằng dấu phẩy.
    Thứ lồng sâu hơn KHÔNG trải (giữ đơn giản) — ghi tên khoá vào `left` để trang báo "còn dữ liệu lồng, xem chi tiết kỹ thuật".
    """
    own, children = {}, {}
    for key, value in item.items():
        if _is_scalar(value):
            own[key] = _cell(value)
        elif isinstance(value, dict):
            children.update({f"{key}.{inner}": _cell(cell) for inner, cell in value.items() if _is_scalar(cell)})
            left += [key for cell in value.values() if not _is_scalar(cell)][:1]
        elif isinstance(value, list) and all(_is_scalar(cell) for cell in value):
            own[key] = _cell(", ".join(str(cell) for cell in value))
        else:
            left.append(key)
    # Thứ tự cột (trần cột cắt từ cuối): ô của chính dòng (mã NV, tổng) ⇒ các cột TIỀN trải từ đối tượng con ⇒ cột trải khác. Bảng thưởng cả đội có
    # 3 đối tượng con × 8 KPI: người xem phải thấy ĐỦ 8 cột tiền KPI trước cột % / mốc (12/11: tiền LPPC_Meat từng rơi vào cột ẩn)
    money = {key: value for key, value in children.items() if "amount" in key.split(".")[0]}
    # Tên / mã SM kèm theo mã nhân viên (khi bật KPI_NAMES) đứng SAU nhóm cột tiền: trang đang hiện tên thì ẩn hai cột này, nên không để chúng chiếm
    # chỗ của cột tiền thứ 8 trong trần cột (12/11: bật tên ⇒ 5 ô + 8 cột tiền = 13 > 12 ⇒ tiền LPPC_Meat lại rơi vào cột ẩn)
    names = {key: value for key, value in own.items() if key in NAME_KEYS}
    return {**{key: value for key, value in own.items() if key not in names}, **money, **names,
            **{key: value for key, value in children.items() if key not in money}}


def _rows(items: list[dict], left: list) -> list[dict]:
    """Dòng của một bảng. Dòng có đúng MỘT danh sách đối tượng con (vd employees[].gaps, sides[].kpis) ⇒ mỗi phần tử con một dòng, kèm cột của dòng cha."""
    rows = []
    for item in items:
        nested = [key for key, value in item.items() if isinstance(value, list) and value and all(isinstance(cell, dict) for cell in value)]
        if len(nested) == 1:
            parent = _flat({key: value for key, value in item.items() if key != nested[0]}, left)
            rows += [{**parent, **_flat(child, left)} for child in item[nested[0]]]
        else:
            rows.append(_flat(item, left))
    return rows


def _table(key: str, items: list[dict], left: list) -> dict:
    rows = _rows(items, left)
    columns = list(dict.fromkeys(column for row in rows for column in row))
    shown = columns[:MAX_COLUMNS]
    return {"key": key, "columns": shown, "hidden_columns": len(columns) - len(shown), "total_rows": len(rows),
            "rows": [[row.get(column) for column in shown] for row in rows[:MAX_ROWS]]}


def result_view(result) -> dict:
    """{facts: [{key, value}], tables: [{key, columns, hidden_columns, total_rows, rows}], notes: [chữ], nested_left: [khoá chưa trải],
    total_facts, total_tables, total_notes} của một kết quả công cụ (không phải dict ⇒ rỗng)."""
    facts, tables, notes, left = [], [], [], []
    for key, value in (result.items() if isinstance(result, dict) else ()):
        if key in ENVELOPE_KEYS:
            continue
        if _is_scalar(value):
            facts.append({"key": key, "value": _cell(value)})
        elif isinstance(value, dict):
            flat = _flat(value, left)
            if flat:
                tables.append({"key": key, "columns": list(flat)[:MAX_COLUMNS], "hidden_columns": max(0, len(flat) - MAX_COLUMNS), "total_rows": 1,
                               "rows": [list(flat.values())[:MAX_COLUMNS]]})
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            tables.append(_table(key, value, left))
        elif isinstance(value, list) and value and all(_is_scalar(item) for item in value):
            if all(isinstance(item, str) and len(item) > SHORT_TEXT for item in value):
                notes += [_cell(item) for item in value]                                  # câu chữ dài (câu cả đội viết sẵn, giả định) ⇒ ghi chú
            else:
                facts.append({"key": key, "value": _cell(", ".join(str(item) for item in value))})   # danh sách mã ngắn (vd các NVxx có tiền) ⇒ một ô
        elif value not in ([], {}):
            left.append(key)
    # Phần bị trần cắt phải KÈM SỐ để trang ghi "hiện 10 / 13 ghi chú" (12/11: cắt mà không nói)
    return {"facts": facts[:MAX_FACTS], "tables": tables[:MAX_TABLES], "notes": notes[:MAX_NOTES], "nested_left": sorted(set(left)),
            "total_facts": len(facts), "total_tables": len(tables), "total_notes": len(notes)}
