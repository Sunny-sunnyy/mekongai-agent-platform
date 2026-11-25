"""Xuất bảng thưởng ra file (Excel · Markdown · chữ thường) từ BẢN TÍNH ĐÃ LƯU —.

Vì sao chỉ đọc bản đã lưu (`store.run_summary` + `store.run_lines`, mã che NV01–NV14): file là cách TRÌNH BÀY một bản tính, không tính lại — số trong file
phải y hệt số trên trang và số trợ lý dùng. Tiền do code tính (`core/`), file không có công thức.
Vì sao mọi ô là GIÁ TRỊ, kể cả dòng tổng (khác lời khuyên "dùng công thức" của skill xlsx): A dặn 09/11 "ghi giá trị code tính, không công thức tính tiền";
máy không có LibreOffice để tính lại công thức trước khi giao ⇒ ô công thức đọc lại bằng openpyxl ra None, không kiểm được. Đổi lại: sửa tay một ô trong Excel thì
dòng tổng không tự đổi — file là bản chụp, sheet Nguồn ghi rõ.
Vì sao KHÔNG khoá sheet: file tải về là bản của người dùng, mở ra phải sửa / chép được ngay; khoá không mật khẩu không bảo vệ được gì mà chỉ làm Excel báo
"protected sheet". Số gốc vẫn nằm ở bản tính đã lưu, không ở file.
File này NGOÀI dấu vân tay bản tính (`main.CODE_FILES` chỉ gồm `core/`, `workflow/`, `backend/queries.py`) — thêm / sửa ở đây không đẻ bản tính mới."""
import io
import unicodedata
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from kpi_agent.backend import store
from kpi_agent.core.rules import KPI_ORDER

TITLE = "BẢNG THƯỞNG TẠM TÍNH — NHÁP, CHƯA PHẢI SỐ CHỐT"
# Bước 3 (10/11): file chỉ ra sau khi soát ⇒ tiêu đề theo trạng thái bản tính (TITLE còn cho bản chưa soát — đường đó backend đã chặn)
TITLE_REVIEWED = "BẢNG THƯỞNG ĐÃ SOÁT — CHƯA PHẢI SỐ CHỐT (ADMIN MIỀN CHỐT)"
# Ô chữ bắt đầu bằng các ký tự này bị Excel / LibreOffice hiểu là công thức khi mở (OWASP "CSV injection") ⇒ ô ép kiểu chữ + quotePrefix (`_put`)
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
NOTES = [
    "Số do code tính (kpi_agent/core) theo bản luật ở trên; file chỉ GHI GIÁ TRỊ của bản tính đã lưu. Mọi tổng do code tính, không phải công thức Excel — "
    "sửa tay một ô thì dòng tổng không tự đổi.",
    "Thưởng vượt doanh số (NPP chi) KHÔNG có trong file: bản tính không lưu ô này (chưa có % doanh số cả NPP).",
    "Mã nhân viên đã che NV01–NV14 (bảng che ở kpi_agent/data, không gửi kèm).",
    "% thực hiện ghi đúng số công cụ trả (vd 54,14 nghĩa là 54,14 %) — ô là số 54,14, không phải 0,5414.",
]


def clean_text(text: str) -> str:
    """Chuẩn hoá chữ trước khi ghi (góp ý): dạng NFC (dấu tiếng Việt dựng sẵn — cùng chữ thì cùng byte, tìm / so được)
    + bỏ ký tự điều khiển (giữ xuống dòng) — ký tự lạ làm Excel báo file hỏng hoặc hiện ô vuông.
    """
    text = unicodedata.normalize("NFC", text)
    return "".join(ch for ch in text if ch == "\n" or not unicodedata.category(ch).startswith("C"))


def looks_like_formula(text: str) -> bool:
    """Chữ bắt đầu bằng = + - @ — Excel / LibreOffice hiểu là công thức khi người dùng mở ô ra sửa (OWASP "CSV injection")."""
    return text.startswith(FORMULA_START)


def report_data(conn, run_id: int) -> dict | None:
    """Gom một bản tính đã lưu thành dữ liệu cho cả 3 loại file. Không có bản ⇒ None."""
    summary = store.run_summary(conn, run_id)
    if summary is None:
        return None
    lines = store.run_lines(conn, run_id)
    cells = {(line["employee_id"], line["kpi"]): line for line in lines}
    employees = sorted({line["employee_id"] for line in lines})
    kpis = [kpi for kpi in KPI_ORDER if any(line["kpi"] == kpi for line in lines)]
    salary = {nv: sum(cells[(nv, kpi)]["amount"] for kpi in kpis if (nv, kpi) in cells) for nv in employees}
    kpi_total = {kpi: sum(cells[(nv, kpi)]["amount"] for nv in employees if (nv, kpi) in cells) for kpi in kpis}
    missing = [f"{nv} {kpi}" for nv in employees for kpi in kpis if (nv, kpi) not in cells]
    if missing:
        # Thiếu một ô thì KHÔNG xuất (góp ý D: lỗi "thiếu dữ liệu mà không báo") — tổng sẽ sai lặng lẽ nếu cộng tiếp
        raise ValueError(f"bản tính {run_id} thiếu {len(missing)} ô NV × KPI: {', '.join(missing[:5])}")
    team_total = sum(salary.values())
    if team_total != summary["team_total"]:
        # Tổng cộng từ 112 dòng phải bằng tổng ghi lúc lưu bản — lệch nghĩa là file lưu hỏng, không xuất số sai
        raise ValueError(f"bản tính {run_id}: tổng các dòng {team_total} ≠ tổng đã lưu {summary['team_total']}")
    # Phiên bản workflow đã cho ra bản tính này: lượt workflow `ok` đầu tiên trỏ tới nó (bước 1 S3 — mỗi lượt ok mang bonus_run_id)
    first = conn.execute("SELECT id FROM workflow_run WHERE bonus_run_id = ? ORDER BY id LIMIT 1", (run_id,)).fetchone()
    workflow = store.workflow_run_summary(conn, first["id"])["workflow_version"] if first else "không rõ"
    status = summary["status_label_for_model"]   # bản KHÔNG có tên người soát — file dễ bị chuyển tiếp (kế hoạch mục 5)
    if summary["replaced_by"] is not None:
        status += f" — ĐÃ BỊ THAY bởi bản tính {summary['replaced_by']}"
    snapshot = summary["data_snapshot"]
    source = [("Bản tính số", summary["run_id"]), ("Tính lúc", summary["created_at"]), ("Kỳ", summary["period"]),
              ("Ngày chốt số", snapshot["cutoff"]), ("Lúc chụp số liệu (pivot làm mới)", snapshot["refreshed_at"] or "không rõ"),
              ("Bản luật", summary["rule_version"]), ("sha256 bản luật", summary["rule_sha256"])]
    source += [(f"sha256 file dữ liệu: {name}", sha) for name, sha in sorted(snapshot["files_sha256"].items())]
    source += [("Dấu vân tay code tính", summary["code_sha"]), ("Phiên bản workflow", workflow), ("Trạng thái", status),
               ("Tổng lương kinh doanh cả đội (đồng)", team_total), ("Xuất file lúc (giờ máy)", datetime.now().isoformat(timespec="seconds"))]
    return {"run_id": summary["run_id"], "period": summary["period"], "cutoff": snapshot["cutoff"], "status": status,
            # Backend chỉ xuất bản đã soát, chưa bị thay (store.exported_formats) ⇒ TITLE chỉ còn cho gọi thẳng hàm (test)
            "title": TITLE_REVIEWED if summary["status"] == "da_soat" and summary["replaced_by"] is None else TITLE,
            "employees": employees, "kpis": kpis, "cells": cells, "salary": salary, "kpi_total": kpi_total, "team_total": team_total,
            "source": source, "notes": NOTES}


NAME_NOTE = "File có họ tên + mã SM thật của nhân viên (bản chạy bật KPI_NAMES) — chỉ dùng nội bộ."
PERSON_HEADERS = ["Tên", "Mã SM"]


def _person_headers(data: dict) -> list[str]:
    """Tiêu đề cột nhân viên: "Mã NV" luôn ở cột 1 (bên kiểm ô so theo mã che); `data["names"]` có (KPI_NAMES=on) ⇒ thêm Tên + Mã SM ngay sau."""
    return ["Mã NV", *(PERSON_HEADERS if data.get("names") else [])]


def _person_cells(data: dict, nv: str) -> list[str]:
    """Ô nhân viên của một dòng, cùng thứ tự `_person_headers`; mã không có trong bảng tên (bản tính của đội khác) ⇒ ô trống, không đoán."""
    if not data.get("names"):
        return [nv]
    entry = data["names"].get(nv) or {}
    return [nv, str(entry.get("name") or ""), str(entry.get("sm_code") or "")]


def _notes(data: dict) -> list[str]:
    """Ghi chú cuối file: có tên thật thì thay câu "mã đã che" bằng câu báo file có tên."""
    if not data.get("names"):
        return data["notes"]
    return [NAME_NOTE if note.startswith("Mã nhân viên đã che") else note for note in data["notes"]]


def file_name(data: dict, extension: str) -> str:
    """Tên file do CODE đặt (chỉ chữ ASCII + số) — không lấy từ người gọi."""
    return f"bang_thuong_{data['period']}_ban{data['run_id']}.{extension}"


def _vn_money(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _vn_pct(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + " %"


# ---------- Excel ----------
FONT = "Arial"   # có đủ dấu tiếng Việt, có trên mọi máy Windows
HEADER_FILL = PatternFill("solid", fgColor="E6F2F1")
TOTAL_FILL = PatternFill("solid", fgColor="F6F7F9")
MONEY = "#,##0"
PCT = '0.00 "%"'


def _put(ws, row: int, col: int, value, bold: bool = False, fmt: str | None = None, fill=None, color: str | None = None, wrap: bool = False):
    text = clean_text(value) if isinstance(value, str) else value
    cell = ws.cell(row=row, column=col, value=text)
    if isinstance(text, str) and looks_like_formula(text):
        # Bản đầu thêm dấu ' vào CHỮ ⇒ Excel hiện dấu ' (13:23, minor 2). Nay: giữ đúng chữ, ép kiểu ô là CHỮ + cờ quotePrefix của Excel
        # (dấu ' ẩn, sửa ô vẫn là chữ). openpyxl hỗ trợ — B thử ghi / đọc lại 09/11: data_type "s", quotePrefix True, styles.xml có cờ
        cell.data_type = "s"
        cell.quotePrefix = True
    cell.font = Font(name=FONT, size=11, bold=bold, color=color)
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    cell.alignment = Alignment(vertical="top", wrap_text=wrap)
    return cell


def _head(ws, data: dict, subtitle: str) -> int:
    """3 dòng đầu mọi sheet: nhãn TẠM TÍNH (đỏ), trạng thái, kỳ / ngày chốt / bản tính. Trả hàng bắt đầu bảng."""
    _put(ws, 1, 1, data["title"], bold=True, color="B42318").font = Font(name=FONT, size=14, bold=True, color="B42318")
    _put(ws, 2, 1, f"Trạng thái: {data['status']}")
    _put(ws, 3, 1, f"{subtitle} · kỳ {data['period']} · ngày chốt {data['cutoff']} · bản tính số {data['run_id']}")
    return 5


def _widths(ws, widths: dict[int, float]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = width


def to_xlsx(data: dict) -> bytes:
    wb = Workbook()
    team = wb.active
    team.title = "Tổng đội"
    top = _head(team, data, "Tiền thưởng từng KPI và lương kinh doanh (đồng)")
    person = _person_headers(data)   # 1 cột (mã che) hoặc 3 cột (mã che, Tên, Mã SM) — các cột số đứng sau, cùng thứ tự như cũ
    first = len(person) + 1          # cột KPI đầu tiên
    headers = [*person, *data["kpis"], "Lương kinh doanh"]
    for col, text in enumerate(headers, 1):
        _put(team, top, col, text, bold=True, fill=HEADER_FILL, wrap=True)
    for i, nv in enumerate(data["employees"], top + 1):
        for col, text in enumerate(_person_cells(data, nv), 1):
            _put(team, i, col, text)
        for col, kpi in enumerate(data["kpis"], first):
            _put(team, i, col, data["cells"][(nv, kpi)]["amount"], fmt=MONEY)
        _put(team, i, len(headers), data["salary"][nv], bold=True, fmt=MONEY)
    last = top + len(data["employees"]) + 1
    for col in range(1, first):
        _put(team, last, col, "Cả đội" if col == 1 else "", bold=True, fill=TOTAL_FILL)
    for col, kpi in enumerate(data["kpis"], first):
        _put(team, last, col, data["kpi_total"][kpi], bold=True, fmt=MONEY, fill=TOTAL_FILL)
    _put(team, last, len(headers), data["team_total"], bold=True, fmt=MONEY, fill=TOTAL_FILL)
    team.freeze_panes = team.cell(row=top + 1, column=first)
    _widths(team, {1: 10, **({2: 24, 3: 14} if first > 2 else {}), **{c: 13 for c in range(first, len(headers))}, len(headers): 18})

    detail = wb.create_sheet("Chi tiết")
    top = _head(detail, data, "Từng nhân viên × KPI")
    for col, text in enumerate([*person, "KPI", "% thực hiện", "Mốc đạt", "Tiền (đồng)", "Lý do"], 1):
        _put(detail, top, col, text, bold=True, fill=HEADER_FILL)
    row = top + 1
    for nv in data["employees"]:
        for kpi in data["kpis"]:
            line = data["cells"][(nv, kpi)]
            for col, text in enumerate(_person_cells(data, nv), 1):
                _put(detail, row, col, text)
            _put(detail, row, first, kpi)
            _put(detail, row, first + 1, line["pct"], fmt=PCT)
            _put(detail, row, first + 2, line["reached_tier"] or "chưa đạt mốc")
            _put(detail, row, first + 3, line["amount"], fmt=MONEY)
            _put(detail, row, first + 4, line["reason"], wrap=True)
            row += 1
    detail.freeze_panes = detail.cell(row=top + 1, column=1)
    _widths(detail, {1: 10, **({2: 24, 3: 14} if first > 2 else {}), first: 13, first + 1: 13, first + 2: 16, first + 3: 14, first + 4: 80})

    source = wb.create_sheet("Nguồn")
    top = _head(source, data, "Số trong file lấy từ đâu")
    for i, (key, value) in enumerate(data["source"], top):
        _put(source, i, 1, key, bold=True)
        _put(source, i, 2, value, fmt=MONEY if isinstance(value, int) and key.startswith("Tổng") else None)
    for i, note in enumerate(_notes(data), top + len(data["source"]) + 1):
        _put(source, i, 1, "Ghi chú", bold=True)
        _put(source, i, 2, note, wrap=True)
    _widths(source, {1: 36, 2: 90})

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ---------- Markdown + chữ thường ----------
def _md(text) -> str:
    """Thoát chữ cho ô bảng Markdown: | làm vỡ bảng; [ ] < > có thể thành link / thẻ HTML ở trình xem."""
    text = clean_text(str(text))
    # & trước (không thoát lại thực thể vừa tạo); ` chặn khối mã; [ ] < > chặn link / thẻ (bên kia từng lọt link javascript:)
    for old, new in (("&", "&amp;"), ("\\", "\\\\"), ("|", "\\|"), ("[", "\\["), ("]", "\\]"), ("<", "&lt;"), (">", "&gt;"), ("`", "\\`"),
                     ("\n", " ")):
        text = text.replace(old, new)
    return text


def to_markdown(data: dict) -> str:
    out = [f"# {data['title']}", "", f"**Trạng thái:** {_md(data['status'])}  ", f"**Kỳ** {data['period']} · **ngày chốt** {data['cutoff']} · "
           f"**bản tính số** {data['run_id']}", "", "## Tổng đội (đồng)", ""]
    person = _person_headers(data)
    who = lambda nv: " | ".join(_md(text) for text in _person_cells(data, nv))   # "NV05" hoặc "NV05 | Tên | Mã SM"
    out.append("| " + " | ".join(person) + " | " + " | ".join(data["kpis"]) + " | Lương kinh doanh |")
    out.append("|" + "---|" * len(person) + "---:|" * (len(data["kpis"]) + 1))
    for nv in data["employees"]:
        out.append(f"| {who(nv)} | " + " | ".join(_vn_money(data["cells"][(nv, k)]["amount"]) for k in data["kpis"]) + f" | **{_vn_money(data['salary'][nv])}** |")
    out.append("| **Cả đội** | " + " | " * (len(person) - 1) + " | ".join(f"**{_vn_money(data['kpi_total'][k])}**" for k in data["kpis"]) + f" | **{_vn_money(data['team_total'])}** |")
    out += ["", "## Chi tiết từng nhân viên × KPI", "", "| " + " | ".join(person) + " | KPI | % thực hiện | Mốc đạt | Tiền (đồng) | Lý do |",
            "|" + "---|" * len(person) + "---|---:|---|---:|---|"]
    for nv in data["employees"]:
        for k in data["kpis"]:
            line = data["cells"][(nv, k)]
            out.append(f"| {who(nv)} | {k} | {_vn_pct(line['pct'])} | {_md(line['reached_tier'] or 'chưa đạt mốc')} | {_vn_money(line['amount'])} | {_md(line['reason'])} |")
    out += ["", "## Nguồn", "", "| Mục | Giá trị |", "|---|---|"]
    out += [f"| {_md(key)} | {_md(_vn_money(value) if isinstance(value, int) and key.startswith('Tổng') else value)} |" for key, value in data["source"]]
    out += ["", *[f"- {_md(note)}" for note in _notes(data)], ""]
    return "\n".join(out)


def to_text(data: dict) -> str:
    width = 12
    out = [data["title"], f"Trạng thái: {data['status']}", f"Kỳ {data['period']} · ngày chốt {data['cutoff']} · bản tính số {data['run_id']}", "",
           "TỔNG ĐỘI (đồng)", "Mã NV".ljust(7) + "".join(k.rjust(width) for k in data["kpis"]) + "Lương KD".rjust(14)]
    for nv in data["employees"]:
        out.append(nv.ljust(7) + "".join(_vn_money(data["cells"][(nv, k)]["amount"]).rjust(width) for k in data["kpis"])
                   + _vn_money(data["salary"][nv]).rjust(14))
    out.append("Cả đội".ljust(7) + "".join(_vn_money(data["kpi_total"][k]).rjust(width) for k in data["kpis"]) + _vn_money(data["team_total"]).rjust(14))
    out += ["", "CHI TIẾT TỪNG NHÂN VIÊN × KPI"]
    for nv in data["employees"]:
        for k in data["kpis"]:
            line = data["cells"][(nv, k)]
            out.append(f"{nv}  {k.ljust(12)} {_vn_pct(line['pct']).rjust(10)}  {(line['reached_tier'] or 'chưa đạt mốc').ljust(14)} "
                       f"{_vn_money(line['amount']).rjust(11)} đ  {line['reason']}")
    out += ["", "NGUỒN"] + [f"- {key}: {_vn_money(value) if isinstance(value, int) and key.startswith('Tổng') else value}" for key, value in data["source"]]
    if data.get("names"):
        # Bảng chữ thường giữ cột cố định theo mã che ⇒ tên + mã SM ghi thành một khối riêng "Tên · mã SM · mã che"
        out += ["", "NHÂN VIÊN (Tên · mã SM · mã che)"] + [" · ".join([*_person_cells(data, nv)[1:], nv]) for nv in data["employees"]]
    out += [f"- {note}" for note in _notes(data)] + [""]
    return clean_text("\n".join(out))
