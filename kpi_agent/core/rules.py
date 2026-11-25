"""Đọc + kiểm khuôn file luật thưởng (YAML) trước khi tính.

Vì sao kiểm ở đây: sai một mốc là sai tiền của người thật. Kiểm 4 điều có thể chứng minh bằng chính chữ công văn
chép trong file: (1) đủ 8 KPI; (2) tổng tiền 100 % = lương kinh doanh; (3) tỷ trọng × tổng ≈ tiền từng KPI;
(4) số trong `label` (chữ công văn) = số của mốc — chặn lỗi gõ kiểu 95 thành 59; (5) tiền 100 % × tỷ lệ = số tiền
in sẵn ở mục 5.2 / 2.3 (`printed_amount`) — chặn lỗi gõ tỷ lệ kiểu 0,80 thành 0,08; (6) ngày lễ là ngày thật trong kỳ;
(7) hàm thưởng vượt chạy trên ví dụ in sẵn của mục 7.2 ra đúng số in sẵn; (8) hai ngưỡng thưởng vượt (NPP ≥ 100 %,
ĐDKD > 103 %) = số trong nhãn chép chữ 7.2 — ví dụ in sẵn không ghim được ngưỡng. Giới hạn: gõ sai CẢ số lẫn chữ chép cùng lúc
thì không bắt được — chữ chép cũng là gõ tay."""
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

KPI_ORDER = ["Turnover", "PC_BT", "LPPC", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat"]
SUPPORTED_ASSUMPTIONS = {
    "turnover_basis": {"before_discount"},
    # sku_list (10/11, theo trang sếp): chỉ các mã ở `aso_focus_2_skus` — cần dữ liệu có mã hàng từng dòng (RAW), sổ Tracking không có
    "aso_focus_2_scope": {"whole_group", "sku_list"},
}
# Tỷ trọng in trong công văn có 4 chữ số lẻ ⇒ tỷ trọng × tổng lệch tiền in sẵn vài đồng; 1.000 đ là đủ bắt lỗi gõ thật.
WEIGHT_TOLERANCE_VND = 1000


class RulesError(ValueError):
    """Luật sai khuôn — dừng trước khi tính tiền."""


def load_rules(path: str | Path) -> dict:
    rules = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    errors = validate_rules(rules)
    if errors:
        raise RulesError("File luật sai khuôn:\n- " + "\n- ".join(errors))
    return rules


def validate_rules(rules: dict) -> list[str]:
    """Trả danh sách lỗi (rỗng = đạt)."""
    errors = []
    kpis = rules.get("kpis", {})
    missing = [k for k in KPI_ORDER if k not in kpis]
    extra = [k for k in kpis if k not in KPI_ORDER]
    if missing or extra:
        errors.append(f"KPI thiếu {missing}, thừa {extra}")
        return errors

    total = rules["business_salary_total"]
    amount_sum = sum(kpis[k]["amount_100"] for k in KPI_ORDER)
    if amount_sum != total:
        errors.append(f"tổng tiền 100 % các KPI = {amount_sum:,} ≠ lương kinh doanh {total:,}")

    for name in KPI_ORDER:
        kpi = kpis[name]
        by_weight = kpi["weight_pct"] / 100 * total
        if abs(by_weight - kpi["amount_100"]) > WEIGHT_TOLERANCE_VND:
            errors.append(f"{name}: tỷ trọng {kpi['weight_pct']} % × tổng = {by_weight:,.0f} ≠ tiền {kpi['amount_100']:,}")
        tier_lists = kpi["region_tiers"].values() if kpi["measure"] == "value_by_region" else [kpi["tiers"]]
        for tiers in tier_lists:
            errors.extend(_check_tiers(name, tiers, kpi["amount_100"]))

    for key, allowed in SUPPORTED_ASSUMPTIONS.items():
        value = rules.get("assumptions", {}).get(key)
        if value not in allowed:
            errors.append(f"assumptions.{key} = {value!r}: dữ liệu hiện có chỉ tính được {sorted(allowed)}")

    assumptions = rules.get("assumptions", {})
    if assumptions.get("aso_focus_2_scope") == "sku_list":
        skus = assumptions.get("aso_focus_2_skus")
        if not isinstance(skus, list) or not skus or not all(isinstance(sku, str) and sku.strip() for sku in skus):
            errors.append("assumptions.aso_focus_2_skus: cần danh sách mã hàng khi aso_focus_2_scope = sku_list")

    # m1 (N1, 16/11): danh sách tên KPI trong luật phải là danh sách tên trong 8 KPI — gõ "LPPC_Meatt" thì KPI đó lặng lẽ thôi lọc / thôi cộng
    for key in ("mcp_filter_kpis", "late_invoice_kpis"):
        if key in assumptions:
            names = assumptions[key]
            if not isinstance(names, list) or any(name not in KPI_ORDER for name in names):
                errors.append(f"assumptions.{key} = {names!r}: cần danh sách tên trong {KPI_ORDER}")
    subdivs = assumptions.get("pc_bt_excluded_subdiv", [])
    if not isinstance(subdivs, list) or not all(isinstance(name, str) and name.strip() for name in subdivs):
        errors.append(f"assumptions.pc_bt_excluded_subdiv = {subdivs!r}: cần danh sách tên ngành (SUB DIV)")

    # bậc 1 nạp chung (17/11): luật kỳ về hoá đơn + ngưỡng cổng đối soát — gõ sai trạng thái ("Shiped") ⇒ dòng đó lặng lẽ thôi cần ngày xuất ⇒ dừng
    if "invoice_required_status" in assumptions:
        statuses = assumptions["invoice_required_status"]
        if not isinstance(statuses, list) or any(status not in ("Invoiced", "Shipped") for status in statuses):
            errors.append(f"assumptions.invoice_required_status = {statuses!r}: cần danh sách trong ['Invoiced', 'Shipped']")
    limit = assumptions.get("max_excluded_sales_pct")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (int, float)) or not 0 <= limit <= 100):
        errors.append(f"assumptions.max_excluded_sales_pct = {limit!r}: cần số từ 0 tới 100")

    for region, target_region in rules.get("region_map", {}).items():
        if target_region not in kpis["LPPC"]["region_tiers"]:
            errors.append(f"region_map: '{region}' ⇒ '{target_region}' không có trong LPPC.region_tiers")

    errors.extend(_check_holidays(rules))
    errors.extend(_check_over_target_labels(rules))
    errors.extend(_check_over_target_examples(rules))
    return errors


def _numbers_in(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text.replace(",", "."))]


def _check_over_target_labels(rules: dict) -> list[str]:
    """Ngưỡng thưởng vượt phải bằng số trong nhãn chép chữ công văn 7.2 (cùng cách chặn lỗi gõ như mốc KPI)."""
    config, errors = rules["over_target_bonus"], []
    for key in ("npp_min_pct", "employee_above_pct"):
        label = config.get(key.replace("_pct", "_label"))
        if not label:
            errors.append(f"over_target_bonus.{key}: thiếu nhãn {key.replace('_pct', '_label')} (chép chữ công văn 7.2)")
        elif float(config[key]) not in _numbers_in(str(label)):
            errors.append(f"over_target_bonus.{key} = {config[key]} không khớp chữ công văn '{label}'")
    return errors


def _check_holidays(rules: dict) -> list[str]:
    """Ngày lễ phải là ngày thật (YYYY-MM-DD) trong đúng kỳ — sai thì báo ở đây, không để traceback lúc tính ngày công."""
    errors = []
    for holiday in rules["working_days"].get("holidays") or []:
        try:
            day = date.fromisoformat(str(holiday).strip())
        except ValueError:
            errors.append(f"working_days.holidays: '{holiday}' không phải ngày YYYY-MM-DD hợp lệ")
            continue
        if f"{day:%Y-%m}" != rules["period"]:
            errors.append(f"working_days.holidays: {day} không thuộc kỳ {rules['period']}")
    return errors


def _check_over_target_examples(rules: dict) -> list[str]:
    """Chạy hàm thưởng vượt thật trên ví dụ in sẵn của công văn (mục 7.2) — chặn lỗi gõ tỷ lệ / trần / ngưỡng."""
    from kpi_agent.core.compute_bonus import over_target_bonus   # nhập trong hàm: compute_bonus cũng nhập file này

    config, errors = rules["over_target_bonus"], []
    examples = config.get("printed_examples") or []
    if not examples:
        return ["over_target_bonus.printed_examples: cần ít nhất 1 ví dụ in sẵn trong công văn để kiểm tham số"]
    for example in examples:
        raw = Decimal(str(config["rate_pct"])) / 100 * max(Decimal(0), Decimal(example["actual"]) - Decimal(example["target"]))
        paid, _ = over_target_bonus(example["target"], example["actual"], example["npp_pct"], rules)
        if raw != example["raw"] or paid != example["paid"]:
            errors.append(f"over_target_bonus: ví dụ {example['target']:,} ⇒ {example['actual']:,} ra raw {raw:,.0f} / chi {paid:,} "
                          f"≠ công văn {example['raw']:,} / {example['paid']:,}")
    return errors


def _check_tiers(name: str, tiers: list[dict], amount_100: int) -> list[str]:
    errors = []
    previous_min, previous_ratio = None, 0
    for tier in tiers:
        # Tiền của mốc = tiền 100 % × tỷ lệ phải bằng số tiền in sẵn ở công văn (02/11: 0,80 ⇒ 0,08 lọt)
        by_ratio = Decimal(str(amount_100)) * Decimal(str(tier["pay_ratio"]))
        if tier.get("printed_amount") is None:
            errors.append(f"{name}: mốc '{tier['label']}' thiếu printed_amount (số tiền in sẵn ở công văn)")
        elif by_ratio != Decimal(str(tier["printed_amount"])):
            errors.append(f"{name}: mốc '{tier['label']}' — {amount_100:,} × {tier['pay_ratio']} = {by_ratio:,.0f} "
                          f"≠ tiền in sẵn ở công văn {tier.get('printed_amount')}")
        # "Mức 1: 4.5" ⇒ chỉ lấy số sau dấu ":" (số "1" là tên mức, không phải ngưỡng)
        label_value_part = tier["label"].split(":")[-1]
        if float(tier["min"]) not in _numbers_in(label_value_part):
            errors.append(f"{name}: mốc min={tier['min']} không khớp chữ công văn '{tier['label']}'")
        if not 0 < tier["pay_ratio"] <= 1:
            errors.append(f"{name}: pay_ratio {tier['pay_ratio']} phải trong (0, 1]")
        if previous_min is not None and (tier["min"] <= previous_min or tier["pay_ratio"] < previous_ratio):
            errors.append(f"{name}: các mốc phải tăng dần (mốc và tỷ lệ tiền)")
        previous_min, previous_ratio = tier["min"], tier["pay_ratio"]
    return errors
