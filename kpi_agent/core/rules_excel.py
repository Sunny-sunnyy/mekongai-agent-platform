"""Cách B — luật thưởng trong một file Excel giám sát tự sửa (mục 3), đọc ra ĐÚNG khuôn dict của cách A
rồi kiểm bằng cùng `validate_rules` ⇒ hai cách so được trên cùng.

File có 3 sheet:
- `moc_thuong`: mỗi hàng một mốc — kpi · amount_100 · weight_pct · measure · lppc_region · min · pay_ratio · printed_amount · label
  (amount_100 / weight_pct / measure lặp lại ở mọi hàng của cùng KPI; lppc_region chỉ điền cho LPPC).
- `chung`: key · value · ghi_chu — tham số chung, khoá có dấu chấm (vd `assumptions.turnover_basis`,
  `region_map.CD - Nam Trung Bộ`).
- `vi_du_thuong_vuot`: ví dụ in sẵn ở mục 7.2 — target · actual · npp_pct · raw · paid (để kiểm tham số thưởng vượt)."""
from pathlib import Path

from openpyxl import load_workbook

from kpi_agent.core.rules import RulesError, validate_rules

TIER_COLUMNS = ["kpi", "amount_100", "weight_pct", "measure", "lppc_region", "min", "pay_ratio", "printed_amount", "label"]
EXAMPLE_COLUMNS = ["target", "actual", "npp_pct", "raw", "paid"]


def load_rules_excel(path: str | Path) -> dict:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        tier_rows = list(wb["moc_thuong"].iter_rows(values_only=True))
        common_rows = list(wb["chung"].iter_rows(values_only=True))
        example_rows = list(wb["vi_du_thuong_vuot"].iter_rows(values_only=True))
    finally:
        wb.close()

    rules = {"kpis": {}, "working_days": {}, "assumptions": {}, "over_target_bonus": {}, "region_map": {}}
    for key, value, *_ in common_rows[1:]:
        if key is None:
            continue
        _set_dotted(rules, str(key).strip(), value)
    holidays = rules["working_days"].get("holidays")
    rules["working_days"]["holidays"] = [h.strip() for h in str(holidays).split(",") if h.strip()] if holidays else []

    header = [str(h).strip() if h else h for h in tier_rows[0]]
    if header[:len(TIER_COLUMNS)] != TIER_COLUMNS:
        raise RulesError(f"sheet moc_thuong phải có cột {TIER_COLUMNS}, đang có {header}")
    for values in tier_rows[1:]:
        row = dict(zip(TIER_COLUMNS, values))
        if not row["kpi"]:
            continue
        kpi = rules["kpis"].setdefault(row["kpi"], {"amount_100": row["amount_100"], "weight_pct": row["weight_pct"],
                                                    "measure": row["measure"]})
        tier = {"min": row["min"], "pay_ratio": row["pay_ratio"], "printed_amount": row["printed_amount"],
                "label": str(row["label"])}
        if row["measure"] == "value_by_region":
            kpi.setdefault("region_tiers", {}).setdefault(row["lppc_region"], []).append(tier)
        else:
            kpi.setdefault("tiers", []).append(tier)

    rules["over_target_bonus"]["printed_examples"] = [dict(zip(EXAMPLE_COLUMNS, values)) for values in example_rows[1:]
                                                      if values and values[0] is not None]

    errors = validate_rules(rules)
    if errors:
        raise RulesError("File luật Excel sai khuôn:\n- " + "\n- ".join(errors))
    return rules


def _set_dotted(rules: dict, key: str, value) -> None:
    """'assumptions.turnover_basis' ⇒ rules['assumptions']['turnover_basis']; region_map giữ nguyên phần sau dấu chấm đầu."""
    head, _, rest = key.partition(".")
    if not rest:
        rules[head] = value
    else:
        rules.setdefault(head, {})[rest] = value
