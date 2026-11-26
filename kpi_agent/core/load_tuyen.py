"""Bảng TUYẾN (điểm bán trong tuyến của từng nhân viên) đọc từ sổ theo dõi tải lên, theo một CÔNG THỨC ĐỌC (`khuon` bảng `tuyen`) — giai đoạn 2a-1.

Vì sao: tuyến từng lấy từ file JSON do người chạy script tách từ trang (`scripts/tach_mcp_trang_sep.py`); đích là "chỉ tải lên". Lõi tự chạy công thức
(sheet · dòng tiêu đề · cột ⇒ trường · ghép giá trị · lọc dòng) — tất định, không có biểu thức, số không qua tay mô hình.
ĐẦU VÀO ⇄ ĐÁP ÁN: chỉ mở ĐÚNG sheet công thức nêu; các sheet kết quả sếp tính trong cùng sổ không bao giờ được đọc (có test).
Đầu ra đúng hình VÀ đúng chữ của `load_raw.load_mcp_json` (kênh đã ở chữ chuẩn của lõi): {mã SM: {mã điểm bán: {channel, visit_day}}}.
"""
import re
from pathlib import Path

from openpyxl import load_workbook

from kpi_agent.core import khuon

SAMPLE_ROWS = 3   # câu kiểm trả 3 dòng đầu + 3 dòng cuối đã đọc để người / agent thấy mình ghép đúng cột
# Mã (nhân viên, điểm bán) là DỮ LIỆU của file đi ra cửa trong câu kiểm ⇒ chỉ in khi đúng khuôn mã (chữ cái / chữ số / gạch, ≤ 20 ký tự); ghép nhầm
# cột chữ tự do (tên, địa chỉ — có dấu cách / dấu tiếng Việt / dấu phẩy) vào trường mã thì câu kiểm in "<không hợp lệ>", không lặp chữ của file (rào U1)
CODE_SHAPE = re.compile(r"[A-Za-z0-9_.-]{1,20}")   # mã nhân viên thật có dấu chấm (20/11: khuôn cũ in "<không hợp lệ>" cho mọi mã thật)
NOT_A_CODE = "<không hợp lệ>"


def _shown(code: str) -> str:
    return code if CODE_SHAPE.fullmatch(code) else NOT_A_CODE


class RouteProblems(ValueError):
    """Sổ không đọc được theo công thức. `problems`: [{"ma", "truong", "so_dong"}] — mã cố định, không lặp chữ của file (rào U1)."""

    def __init__(self, problems: list[dict]):
        super().__init__("bảng tuyến không đọc được theo công thức: " + "; ".join(
            f"{p['ma']}" + (f" ({p['truong']})" if p["truong"] else "") + (f" — {p['so_dong']} dòng" if p["so_dong"] else "") for p in problems))
        self.problems = problems


def _problem(code: str, field: str | None = None, rows: int = 0) -> dict:
    return {"ma": code, "truong": field, "so_dong": rows}


def _text(value) -> str:
    """Ô ⇒ chuỗi: ô số nguyên (kể cả 123.0) không mang '.0'; ô chữ giữ nguyên (giữ số 0 đầu), bỏ khoảng trắng hai đầu."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def header_of(path: str | Path, mapping: dict) -> list:
    """Dòng tiêu đề theo công thức (để băm vân tay bố cục) — chỉ mở sheet công thức nêu."""
    return _read(path, mapping, header_only=True)


def _read(path: str | Path, mapping: dict, header_only: bool = False):
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = mapping["sheet"]
        if isinstance(sheet, int):
            if sheet > len(wb.sheetnames):
                raise RouteProblems([_problem("thieu_sheet")])
            sheet = wb.sheetnames[sheet - 1]
        else:   # so tên đã bỏ khoảng trắng hai đầu: sổ thật có sheet tên kết thúc bằng dấu cách ("Thưởng thêm ")
            matches = [name for name in wb.sheetnames if khuon._clean(name) == khuon._clean(sheet)]
            if len(matches) != 1:
                raise RouteProblems([_problem("thieu_sheet")])
            sheet = matches[0]
        rows = wb[sheet].iter_rows(min_row=mapping["header_row"], max_row=mapping["header_row"] if header_only else None, values_only=True)
        header = next(rows, None)
        if header is None:
            raise RouteProblems([_problem("thieu_dong_tieu_de")])
        return list(header) if header_only else (list(header), list(rows))
    finally:
        wb.close()


def read_routes(path: str | Path, mapping: dict, team_codes: set[str] | None = None) -> dict:
    """Chạy công thức trên sổ ⇒ {"mcp", "summary", "fingerprint"}. Lệch khuôn ⇒ `RouteProblems`.

    `team_codes` = mã nhân viên của file chỉ tiêu của kỳ. LUẬT DỮ LIỆU: mã không có trong file chỉ tiêu ⇒ không thuộc đội ⇒ dòng của mã đó bị bỏ,
    câu kiểm nêu số dòng + số mã (sổ tháng 10: 1 dòng của 1 mã, kênh "Kênh Vãng lai"); giá trị lạ của người TRONG đội vẫn dừng. None ⇒ không lọc.

    `fingerprint` = vân tay bố cục thật của sheet (bên gọi so với vân tay công thức được lưu cho — công thức theo VỊ TRÍ cột không được áp cho bố cục khác).
    `summary` (câu kiểm): số dòng đọc / bỏ, số nhân viên, số điểm bán, theo kênh, dòng của mã ngoài đội bị bỏ (nêu từng mã, không lọc âm thầm), dòng mẫu."""
    mapping = khuon.normalize_mapping({**mapping, "table": "tuyen"})
    header, rows = _read(path, mapping)
    names = [khuon._clean(cell) for cell in header]
    index, problems = {}, []
    for field, column in mapping["columns"].items():
        if isinstance(column, int):
            position = column - 1 if column <= len(names) else None
        else:
            hits = [i for i, name in enumerate(names) if name == column]
            position = hits[0] if len(hits) == 1 else None
            if len(hits) > 1:   # sheet MCP thật: "SM code" có 4 lần trên dòng tiêu đề ⇒ ghép theo tên là mơ hồ ⇒ phải ghép theo VỊ TRÍ
                problems.append(_problem("ten_cot_lap_phai_ghep_theo_vi_tri", field))
                continue
        if position is None:
            problems.append(_problem("cot_khong_xac_dinh", field))
        index[field] = position
    if problems:
        raise RouteProblems(problems)
    blank_fields = mapping.get("rows", {}).get("bo_dong_trong", [])
    channel_of = mapping["values"].get("channel", {})
    cell = lambda row, field: row[index[field]] if field in index and index[field] < len(row) else None
    mcp, kept, blank_rows, repeated, blank_required = {}, [], 0, 0, {"sm_code": 0, "outlet_code": 0, "channel": 0}
    unknown_channel = {"rows": 0, "values": set(), "sm_codes": set()}
    outside_team = {}   # mã ngoài đội ⇒ số dòng bị bỏ
    for row in rows:
        sm, outlet, channel = _text(cell(row, "sm_code")), _text(cell(row, "outlet_code")), _text(cell(row, "channel"))
        if any(not _text(cell(row, field)) for field in blank_fields):
            if not (sm or outlet or channel):
                blank_rows += 1
                continue
            # trường "dòng trống" trống mà ô bắt buộc khác có chữ ⇒ không phải dòng trắng (vd ô công thức không có giá trị lưu sẵn) ⇒ dừng ở dưới
        if team_codes is not None and sm and sm not in team_codes:
            outside_team[sm] = outside_team.get(sm, 0) + 1
            continue
        channel = channel_of.get(channel, channel)
        if not sm or not outlet or not channel:
            for field, value in (("sm_code", sm), ("outlet_code", outlet), ("channel", channel)):
                blank_required[field] = blank_required.get(field, 0) + (not value)
            continue
        if channel not in khuon.CANONICAL_VALUES["channel"]:
            unknown_channel["rows"] += 1
            unknown_channel["values"].add(channel)
            unknown_channel["sm_codes"].add(sm)
            continue
        if outlet in mcp.get(sm, {}):
            repeated += 1
            continue
        info = {"channel": channel}
        if "visit_day" in index:
            info["visit_day"] = _text(cell(row, "visit_day"))
        mcp.setdefault(sm, {})[outlet] = info
        kept.append({"sm_code": sm, "outlet_code": outlet, "channel": channel})
    problems += [_problem("o_bat_buoc_trong", field, count) for field, count in blank_required.items() if count]
    if unknown_channel["rows"]:   # nói SỐ giá trị lạ + số mã nhân viên dính, không lặp chữ của file (rào U1 — như bước 2.4 của file đơn hàng)
        problems.append({**_problem("gia_tri_la", "channel", unknown_channel["rows"]), "so_gia_tri": len(unknown_channel["values"]),
                         "so_ma_sm": len(unknown_channel["sm_codes"])})
    problems += [_problem("diem_ban_lap_trong_mot_nhan_vien", "outlet_code", repeated)] if repeated else []
    problems += [_problem("khong_co_dong")] if not kept and not problems else []
    owners_of = {}
    for line in kept:
        owners_of.setdefault(line["outlet_code"], set()).add(line["sm_code"])
    shared = sum(len(owners) > 1 for owners in owners_of.values())
    if shared:   # một cửa hàng đếm cho hai người là đường tăng số điểm bán / thưởng của cả hai (sổ tháng 10: 0 ca) ⇒ dừng
        problems.append(_problem("diem_ban_thuoc_hai_nguoi", "outlet_code", shared))
    if problems:
        raise RouteProblems(problems)
    by_channel = {}
    for line in kept:
        by_channel[line["channel"]] = by_channel.get(line["channel"], 0) + 1
    summary = {"rows_read": len(rows), "rows_blank_skipped": blank_rows, "employees": len(mcp), "outlets": len(kept), "by_channel": by_channel,
               # gõ sai mã của một người thật cũng rơi vào đây ⇒ nêu TỪNG mã + số dòng để người tải nhìn thấy
               "dropped_outside_team": [{"sm_code": _shown(code), "rows": count} for code, count in sorted(outside_team.items())],
               "team_codes_without_routes": len(team_codes - set(mcp)) if team_codes is not None else None,
               "sample_first": [_sample(line) for line in kept[:SAMPLE_ROWS]], "sample_last": [_sample(line) for line in kept[-SAMPLE_ROWS:]]}
    return {"mcp": mcp, "summary": summary, "fingerprint": khuon.layout_fingerprint(str(mapping["sheet"]), mapping["header_row"], header)}


def _sample(line: dict) -> dict:
    return {"sm_code": _shown(line["sm_code"]), "outlet_code": _shown(line["outlet_code"]), "channel": line["channel"]}   # kênh đã ở chữ chuẩn của lõi


def known_formula(fingerprint: str) -> dict | None:
    """Công thức đọc lưu sẵn cho đúng vân tay bố cục này (sổ tháng 10), không có ⇒ None — KHÔNG áp công thức vị trí cột cho bố cục khác."""
    return khuon.default_mapping("tuyen") if fingerprint == khuon.KNOWN_LAYOUTS["tuyen"] else None
