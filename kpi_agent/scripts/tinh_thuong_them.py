"""Tính Thưởng thêm 01–06 × mọi NV từ RAW DATA bằng `core/compute_extra_bonus` ⇒ JSON mã che NV01–NV14 cho người kiểm so mù với đáp án của mình.
Không gọi AI, 0 USD. In thêm số ASO 03 / 04 theo cả 4 cách đếm × có / không lọc tuyến (để A đưa — luật ghi ở file luật mục (d)).

Chạy từ gốc repo:
  .venv/bin/python -m kpi_agent.scripts.tinh_thuong_them "sample_data/thang_10/RAW DATA.xlsx" 2026-10-31 kpi_agent/data/E_THUONG_THEM_3110.json"""
import json
import sys
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path

from kpi_agent.core.compute_extra_bonus import late_filter, compute_extra_bonus, count_aso, load_extra_rules, load_item_targets
from kpi_agent.core.load_data import load_targets
from kpi_agent.core.load_raw import load_mcp_json, period_rules, read_raw_lines
from kpi_agent.core.rules import load_rules

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRA_RULES = REPO_ROOT / "kpi_agent/config/extra_bonus_2026_10.yaml"
KPI_RULES = REPO_ROOT / Path("kpi_agent/config/rules_2026_10_raw.yaml")   # luật kỳ của bộ đọc RAW (trạng thái được tính, hạn hoá đơn — bậc 3a)
TARGETS = REPO_ROOT / Path("sample_data/thang_10/Chi tieu NVBH.xlsx")
MCP = REPO_ROOT / Path("sample_data/thang_10/mcp_trang_sep_3110.json")
MASK = REPO_ROOT / Path("kpi_agent/data/ma_che_ten.json")
EXTRA_COLUMNS = ("TOTAL CASES", "TOTAL PIECES")


def compute(raw_path: str, cutoff: date) -> tuple[dict, dict, list[dict], dict]:
    rules = load_extra_rules(EXTRA_RULES)
    # keep_late (mốc 7 N2): giữ dòng hoá đơn trễ CÓ ngày xuất, cờ LATE — lõi chỉ dùng ở khoản / cổng `include_late_invoice: true`
    lines, _ = read_raw_lines(raw_path, cutoff, rules["period"], extra_columns=EXTRA_COLUMNS, keep_late=True,
                              period_rules=period_rules(load_rules(KPI_RULES)["assumptions"]))
    employees = sorted(load_targets(TARGETS)["employees"])
    mcp = load_mcp_json(MCP)
    return compute_extra_bonus(lines, rules, employees, mcp, load_item_targets(rules, resolve=lambda p: REPO_ROOT / p)), rules, lines, mcp


def aso_variants(lines: list[dict], rules: dict, mcp: dict) -> dict:
    """Tổng cả đội ASO 03 / 04 theo mọi cách đếm (moq_8_1 · min_amount 150000 · min_qty_per_order 2 · any) × có / không lọc tuyến."""
    out = {}
    lines = late_filter(lines, {})   # các cách đếm ASO 03 / 04 không cộng dòng hoá đơn trễ (như lõi)
    for item_id in ("T03", "T04"):
        item = rules["items"][item_id]
        groups = set(item.get("dms_obj") or [])
        scoped = [line for line in lines if line["DMS OBJ"] in groups and line["FREE ITEM FLAG"] != "Y"]
        for rule in ({"kind": "moq_8_1"}, {"kind": "min_amount", "value": 150000}, {"kind": "min_qty_per_order", "value": 2}, {"kind": "any"}):
            for use_mcp in (True, False):
                keep = (lambda sm, o: o in mcp.get(sm, {})) if use_mcp else (lambda sm, o: True)
                name = f"{item_id} {rule['kind']}{' ' + str(rule['value']) if 'value' in rule else ''} {'lọc tuyến' if use_mcp else 'không lọc'}"
                out[name] = sum(count_aso(scoped, rule, keep).values())
    return out


def main(raw_path: str, cutoff_text: str, out_path: str) -> None:
    started = time.perf_counter()
    result, rules, lines, mcp = compute(raw_path, date.fromisoformat(cutoff_text))
    to_masked = {real: masked for masked, real in json.loads(MASK.read_text(encoding="utf-8"))["map"].items()}
    cells = {to_masked[code]: {line.item: asdict(line) for line in item_lines} for code, item_lines in result.items()}
    team = {item_id: {"actual": round(sum(cells[nv][item_id]["actual"] for nv in cells), 4), "amount": sum(cells[nv][item_id]["amount"] for nv in cells)}
            for item_id in rules["items"]}
    total = sum(item["amount"] for item in team.values())
    Path(out_path).write_text(json.dumps({"cutoff": cutoff_text, "rules": str(EXTRA_RULES), "raw": Path(raw_path).name, "mcp": str(MCP),
                                          "aso_rules": {i: rules["items"][i].get("aso_rule") for i in rules["items"] if rules["items"][i]["measure"] == "aso"},
                                          "team": team, "team_total_amount": total, "cells": dict(sorted(cells.items()))},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
    for item_id, item in team.items():
        print(f"{item_id} {rules['items'][item_id]['title']}: thực hiện cả đội {item['actual']:,} · tiền {item['amount']:,} đ")
    print(f"Tổng thưởng thêm 01–06: {total:,} đ (TÁCH khỏi 8 KPI) ⇒ {out_path} · {time.perf_counter() - started:.1f} s")
    print("ASO 03 / 04 theo mọi cách đếm (cả đội):")
    for name, value in aso_variants(lines, rules, mcp).items():
        print(f"  {name}: {value}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
