"""Khối 5 — xuất bảng thưởng (Markdown + Excel) kèm câu giải thích, và bản ghi lần chạy (run.json).

Đầu ra có tên thật ⇒ chỉ ghi vào kpi_agent/data/ (Git bỏ qua).
"""
import json
from dataclasses import asdict
from pathlib import Path

from openpyxl import Workbook

from kpi_agent.core.rules import KPI_ORDER


def build_rows(employees: dict, results: dict, bonuses: dict) -> list[dict]:
    """Một dòng mỗi NV × KPI — dạng phẳng dùng chung cho Markdown, Excel và run.json."""
    rows = []
    for code in sorted(results):
        for kpi in KPI_ORDER:
            r, line = results[code][kpi], bonuses[code]["lines"][KPI_ORDER.index(kpi)]
            rows.append({"sm_code": code, "name": employees[code]["name"], "kpi": kpi, "target": r.target,
                         "actual": r.actual, "pct": r.pct, "reached": line.reached_label, "amount": line.amount,
                         "next_tier": line.next_label, "gap_to_next": line.gap_to_next,
                         "how": r.how, "reason": line.reason})
    return rows


def write_markdown(path: Path, employees: dict, bonuses: dict, rows: list[dict], header_lines: list[str]) -> None:
    out = ["# Bảng thưởng lương kinh doanh (tiến độ theo ngày chốt)", "", *header_lines, "",
           "| Mã NV | Tên | " + " | ".join(KPI_ORDER) + " | Lương KD | Thưởng vượt |",
           "|" + "---|" * (len(KPI_ORDER) + 4)]
    by_employee = {}
    for row in rows:
        by_employee.setdefault(row["sm_code"], {})[row["kpi"]] = row
    for code, by_kpi in by_employee.items():
        cells = [f"{_shown(by_kpi[k])} · {by_kpi[k]['amount']:,}" for k in KPI_ORDER]
        over = bonuses[code]["over_target_bonus"]
        out.append(f"| {code} | {employees[code]['name']} | " + " | ".join(cells)
                   + f" | {bonuses[code]['business_salary']:,} | {'thiếu dữ kiện' if over is None else f'{over:,}'} |")
    out += ["", "## Giải thích từng ô", ""]
    for code, by_kpi in by_employee.items():
        out.append(f"### {code} — {employees[code]['name']}")
        for kpi in KPI_ORDER:
            row = by_kpi[kpi]
            gap = f"; còn thiếu {row['gap_to_next']:,.2f} để lên '{row['next_tier']}'" if row["next_tier"] else ""
            out.append(f"- {row['reason']}{gap}. Cách đếm: {row['how']}.")
        out.append(f"- Thưởng vượt: {bonuses[code]['over_target_reason']}.")
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


def write_excel(path: Path, rows: list[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "bang_thuong"
    columns = list(rows[0])
    ws.append(columns)
    for row in rows:
        ws.append([row[c] for c in columns])
    wb.save(path)


def write_run_record(path: Path, record: dict, results: dict, bonuses: dict) -> None:
    record = {**record,
              "results": {code: {k: asdict(r) for k, r in by_kpi.items()} for code, by_kpi in results.items()},
              "bonuses": {code: {"lines": [asdict(line) for line in b["lines"]], "business_salary": b["business_salary"],
                                 "over_target_bonus": b["over_target_bonus"], "over_target_reason": b["over_target_reason"]}
                          for code, b in bonuses.items()}}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _shown(row: dict) -> str:
    return f"{row['actual']:.2f}" if row["kpi"] in ("LPPC", "LPPC_Meat") else f"{row['pct']:.0%}"
