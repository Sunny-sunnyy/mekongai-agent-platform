"""Thưởng thêm khoản 01–06 + Meat 7.1–7.3 (trang sếp / sổ Tracking tab "Thưởng thêm") từ dòng RAW đã lọc ⇒ thực hiện / % / tiền từng NV × khoản.

Vì sao tách khỏi 8 KPI (13:20–13:25 10/11 — kế hoạch): các khoản này
KHÔNG có trong công văn — luật lấy từ chữ in trên trang sếp, tiền trả riêng ⇒ không đụng `compute_kpi` / `compute_bonus`, tổng 8 KPI không đổi.
Luật (nhóm hàng, ngưỡng, tiền, cách đếm ASO) nằm hết trong `config/extra_bonus_<tháng>.yaml`; file này chỉ đếm theo luật.
Dữ liệu / luật sai ⇒ `ValueError` (lỗi nặng ⇒ dừng hẳn) — không có chỗ nào `or 0` (bài học 10/11: ô trống thành 0 đ lặng lẽ)."""
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from kpi_agent.core.compute_bonus import _vn, floor_pct

ASO_KINDS = {"moq_8_1", "min_amount", "min_amount_per_order", "min_qty_per_order", "any"}
ASO_KINDS_WITH_VALUE = {"min_amount", "min_amount_per_order", "min_qty_per_order"}   # kiểu có ngưỡng `value`
# sales_rate_of_target (Meat 7.1–7.3, 11/11): tiền = tỷ lệ % × doanh số thực đạt khi % chỉ tiêu tới mức, có trần, có cổng (khoản khác cùng tới mức)
MEASURES = {"cases", "aso", "sales_pct_of_target", "sales_rate_of_target"}
TARGET_MEASURES = {"sales_pct_of_target", "sales_rate_of_target"}   # cần chỉ tiêu riêng từng NV (targets_file)


@dataclass
class ExtraLine:
    item: str
    title: str
    actual: float          # thùng / số điểm bán / doanh số (đồng)
    target: float          # ngưỡng (thùng, ASO) hoặc chỉ tiêu doanh số của NV (đồng)
    reached: bool
    amount: int            # đồng
    how: str


def load_extra_rules(path: str | Path) -> dict:
    rules = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    errors = validate_extra_rules(rules)
    if errors:
        raise ValueError("File luật thưởng thêm sai khuôn:\n- " + "\n- ".join(errors))
    return rules


def _label_numbers(label: str) -> set[int]:
    """Số trong chữ trang: "6.000đ" ⇒ 6000, "300k" ⇒ 300000, "≥108%" ⇒ 108."""
    return {int(digits.replace(".", "")) * (1000 if k else 1) for digits, k in re.findall(r"(\d[\d.]*)(k?)", label)}


def validate_extra_rules(rules: dict) -> list[str]:
    errors = []
    for item_id, item in rules.get("items", {}).items():
        measure = item.get("measure")
        if measure not in MEASURES:
            errors.append(f"{item_id}: measure '{measure}' không hỗ trợ")
            continue
        if not (item.get("dms_obj") or item.get("sku_code")):
            errors.append(f"{item_id}: thiếu nhóm hàng (dms_obj hoặc sku_code)")
        if measure in TARGET_MEASURES and not item.get("targets_file"):
            errors.append(f"{item_id}: thiếu targets_file (chỉ tiêu riêng từng NV)")
        # mốc 7 N2: cờ cộng dòng hoá đơn trễ phải là true / false thật (gõ "yes" ⇒ dừng, không đoán nghĩa)
        for where, value in (("", item.get("include_late_invoice", False)), (" gate", (item.get("gate") or {}).get("include_late_invoice", False))):
            if not isinstance(value, bool):
                errors.append(f"{item_id}{where}: include_late_invoice phải là true / false")
        if measure == "sales_pct_of_target":
            for tier in item.get("tiers", []):
                if _label_numbers(tier["label"]) != {tier["min"], tier["amount"]}:
                    errors.append(f"{item_id}: số trong nhãn '{tier['label']}' khác mức {tier['min']} % / {tier['amount']} đ")
        elif measure == "sales_rate_of_target":
            errors.extend(_rate_item_errors(item_id, item))
        else:
            if _label_numbers(item["label"]) != {item["min"], item["pay_per_unit"]}:
                errors.append(f"{item_id}: số trong nhãn '{item['label']}' khác ngưỡng {item['min']} / tiền {item['pay_per_unit']} đ")
        if measure == "aso" and item.get("aso_rule", {}).get("kind") not in ASO_KINDS:
            errors.append(f"{item_id}: aso_rule.kind phải là một trong {sorted(ASO_KINDS)}")
        elif measure == "aso" and item["aso_rule"]["kind"] in ASO_KINDS_WITH_VALUE:
            value = item["aso_rule"].get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                # Thiếu / gõ sai ngưỡng (vd "150.000" thành chữ) ⇒ dừng lúc nạp luật, không để phép so lỗi giữa chừng
                errors.append(f"{item_id}: aso_rule.value phải là số > 0 với kiểu '{item['aso_rule']['kind']}'")
    if rules.get("pay_all_units_when_reached") is not True:
        # Cách "chỉ trả phần vượt" chưa có code — để false thì phải viết thêm, không lặng lẽ tính theo cách cũ
        errors.append("pay_all_units_when_reached phải là true (cách 'phần vượt' chưa có code)")
    return errors


def _positive(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value > 0


def _rate_item_errors(item_id: str, item: dict) -> list[str]:
    """Khuôn khoản `sales_rate_of_target` — thiếu / sai ⇒ dừng lúc nạp luật (không tính 0 đ lặng lẽ)."""
    errors = []
    tiers = item.get("tiers") or []
    if not tiers or not all(_positive(tier.get("min")) and _positive(tier.get("rate_pct")) for tier in tiers):
        errors.append(f"{item_id}: tiers phải có min (%) và rate_pct (%) > 0 ở mọi mức")
    if not _positive(item.get("cap")):
        errors.append(f"{item_id}: cap (trần tiền, đồng) phải là số > 0")
    if not item.get("targets_key"):
        errors.append(f"{item_id}: thiếu targets_key (khối chỉ tiêu trong targets_file)")
    gate = item.get("gate")
    if gate is not None and (not isinstance(gate, dict) or not gate.get("targets_key") or not gate.get("sub_div") or gate.get("same_tier") is not True):
        # same_tier = mức k của khoản cần cổng cũng tới mức k (cách đọc P1, 11/11); cách khác chưa có code ⇒ dừng
        errors.append(f"{item_id}: gate cần targets_key, sub_div và same_tier: true")
    return errors


def load_item_targets(rules: dict, resolve=Path) -> dict[str, dict[str, dict[str, float]]]:
    """{khoản: {"own": {mã SM: chỉ tiêu}, "gate": {mã SM: chỉ tiêu} | None}} cho các khoản có `targets_file`. File một khối ({"targets": {mã: số}},
    Kokomi) hoặc nhiều khối ({"targets": {khoá: {mã: số}}}, Meat) — chọn khối bằng `targets_key`. `resolve`: đường dẫn trong luật ⇒ file trên đĩa."""
    found = {}
    for item_id, item in rules["items"].items():
        if item["measure"] not in TARGET_MEASURES:
            continue
        try:
            data = json.loads(Path(resolve(item["targets_file"])).read_text(encoding="utf-8"))["targets"]
        except (OSError, ValueError, KeyError, TypeError):
            # 12/11: hộp Capitol dựng từ ảnh chụp thiếu `chi_tieu_meat_3110.json` (file Git bỏ qua) ⇒ FileNotFoundError trần làm backend chết lúc bật.
            # Nay là lỗi dữ liệu có TÊN FILE ⇒ Thưởng thêm trả lời từ chối nêu đúng file thiếu, 8 KPI vẫn chạy (luật sẵn có ở `main._extra_bonus`)
            raise ValueError(f"{item_id}: không đọc được file chỉ tiêu {item['targets_file']} (thiếu file, hoặc không phải JSON có khoá 'targets')") from None
        pick = lambda key: data if key is None else data.get(key)
        own, gate = pick(item.get("targets_key")), (pick(item["gate"]["targets_key"]) if item.get("gate") else None)
        if own is None or (item.get("gate") and gate is None):
            raise ValueError(f"{item_id}: {item['targets_file']} không có khối chỉ tiêu '{item.get('targets_key')}' / cổng")
        found[item_id] = {"own": own, "gate": gate}
    return found


def _in_scope(item: dict):
    groups, skus = set(item.get("dms_obj") or []), set(item.get("sku_code") or [])
    return lambda line: line["DMS OBJ"] in groups or line["SKU CODE"] in skus


def _number(line: dict, column: str) -> float:
    value = line[column]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"RAW dòng Excel {line['_row']}: ô '{column}' trống hoặc không phải số — sửa file rồi chạy lại")
    return value


def check_groups_exist(lines: list[dict], rules: dict) -> None:
    """Tên nhóm / mã hàng trong luật phải có trong RAW — gõ sai tên ⇒ dừng, không thành 0 thùng / 0 ASO."""
    seen_groups, seen_skus = {line["DMS OBJ"] for line in lines}, {line["SKU CODE"] for line in lines}
    missing = [f"{item_id}: '{name}'" for item_id, item in rules["items"].items()
               for name in (item.get("dms_obj") or []) if name not in seen_groups]
    missing += [f"{item_id}: mã '{code}'" for item_id, item in rules["items"].items()
                for code in (item.get("sku_code") or []) if code not in seen_skus]
    if missing:
        raise ValueError("Luật thưởng thêm trỏ nhóm hàng không có trong RAW: " + ", ".join(missing))


def count_aso(lines: list[dict], rule: dict, keep_outlet) -> dict[str, int]:
    """Số điểm bán mỗi NV theo `rule` (xem chú thích (d) trong file luật). `lines` = dòng thuộc nhóm hàng của khoản, không có hàng tặng."""
    kind, value = rule["kind"], rule.get("value")
    sales, moq_sales, order_qty, order_sales = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float)
    for line in lines:
        outlet = (line["SM CODE"], line["OUTLET CODE"])
        amount = _number(line, "Sales Before Discount")
        sales[outlet] += amount
        # Cộng theo TỪNG ĐƠN: chữ sổ Tracking ô Q1 "tối thiểu 150,000 đồng/đơn hàng" (11/11) — tổng cả tháng có thể đạt nhờ nhiều đơn nhỏ
        order_sales[(outlet, line["ORDER NUM"])] += amount
        if line["MOQ_FLAG"] == "Y":
            moq_sales[outlet] += amount
        if kind == "min_qty_per_order":
            order_qty[(outlet, line["ORDER NUM"])] += _number(line, "TOTAL PIECES")
    if kind == "moq_8_1":
        counted = {outlet for outlet, amount in moq_sales.items() if amount > 0}
    elif kind == "min_amount":
        counted = {outlet for outlet, amount in sales.items() if amount >= value}
    elif kind == "min_amount_per_order":
        counted = {outlet for (outlet, _), amount in order_sales.items() if amount >= value}
    elif kind == "min_qty_per_order":
        counted = {outlet for (outlet, _), qty in order_qty.items() if qty >= value}
    else:
        counted = {outlet for outlet, amount in sales.items() if amount > 0}
    result = defaultdict(int)
    for sm, outlet_code in counted:
        if keep_outlet(sm, outlet_code):
            result[sm] += 1
    return result


def compute_extra_bonus(lines: list[dict], rules: dict, employees: list[str], mcp: dict, item_targets: dict[str, dict]) -> dict[str, list[ExtraLine]]:
    """{mã NV: [ExtraLine theo thứ tự khoản trong luật]}. `lines` = `load_raw.read_raw_lines(..., extra_columns=("TOTAL CASES", "TOTAL PIECES"))`;
    `item_targets` = `load_item_targets(rules)`.
    Dòng mang cờ `LATE` (hoá đơn xuất sau hạn 6.1 — `read_raw_lines(keep_late=True)`) chỉ vào khoản / cổng có `include_late_invoice: true`
    (mốc 7 N2, 16/11: trang sếp cộng trễ ở T06 + cổng 07; khoản khác cộng hay bỏ đều khớp trang ⇒ giữ bỏ theo 6.1)."""
    check_groups_exist(lines, rules)
    sold = [line for line in lines if line["FREE ITEM FLAG"] != "Y"]   # hàng tặng không phải hàng bán (cùng căn cứ 8.1 "số lượng MUA")
    if rules["exclude_outlets_outside_mcp"]:
        keep_outlet = lambda sm, outlet: outlet in mcp.get(sm, {})
    else:
        keep_outlet = lambda sm, outlet: True
    result = {code: [] for code in employees}
    for item_id, item in rules["items"].items():
        scoped = [line for line in late_filter(sold, item) if _in_scope(item)(line)]
        measure = item["measure"]
        if measure == "cases":
            totals = defaultdict(float)
            for line in scoped:
                totals[line["SM CODE"]] += _number(line, "TOTAL CASES")
        elif measure == "aso":
            totals = count_aso(scoped, item["aso_rule"], keep_outlet)
        else:
            totals = _sales_by_employee(scoped)
        gate_sales = gate_sales_by_employee(sold, item["gate"]) if item.get("gate") else None
        for code in employees:
            actual = round(totals.get(code, 0), 4)
            if measure == "sales_pct_of_target":
                result[code].append(_tier_line(item_id, item, actual, item_targets[item_id]["own"], code))
            elif measure == "sales_rate_of_target":
                gate = (round(gate_sales.get(code, 0), 4), item_targets[item_id]["gate"]) if gate_sales is not None else None
                result[code].append(_rate_line(item_id, item, actual, item_targets[item_id]["own"], code, gate))
            else:
                result[code].append(_unit_line(item_id, item, actual))
    return result


def late_filter(lines: list[dict], rule: dict) -> list[dict]:
    """Dòng dùng cho một khoản / cổng: có `include_late_invoice: true` ⇒ mọi dòng; không ⇒ bỏ dòng cờ `LATE`."""
    return lines if rule.get("include_late_invoice", False) else [line for line in lines if not line.get("LATE")]


def gate_sales_by_employee(sold: list[dict], gate: dict) -> dict[str, float]:
    """Doanh số cổng (cả SUB DIV của cổng, dòng bán) theo NV — một chỗ cho lõi và ô "07" của trang (backend `gate_progress`)."""
    return _sales_by_employee([line for line in late_filter(sold, gate) if line["SUB DIV"] == gate["sub_div"]])


def _sales_by_employee(lines: list[dict]) -> dict[str, float]:
    totals = defaultdict(float)
    for line in lines:
        totals[line["SM CODE"]] += _number(line, "Sales Before Discount")
    return totals


def _unit_line(item_id: str, item: dict, actual: float) -> ExtraLine:
    unit = "thùng" if item["measure"] == "cases" else "ASO"
    reached = actual >= item["min"]
    amount = round(actual * item["pay_per_unit"]) if reached else 0
    how = (f"{_vn(actual)} {unit} ≥ {item['min']} ⇒ {_vn(actual)} × {_vn(item['pay_per_unit'], ',')} đ" if reached
           else f"{_vn(actual)} {unit} < {item['min']} ⇒ chưa đạt ngưỡng")
    if item.get("same_data_as"):
        how += f" (cùng dữ liệu KPI {item['same_data_as']} — trả riêng)"
    return ExtraLine(item_id, item["title"], actual, item["min"], reached, amount, how)


def _tier_line(item_id: str, item: dict, sales: float, targets: dict[str, float], code: str) -> ExtraLine:
    if code not in targets:
        raise ValueError(f"{item_id}: thiếu chỉ tiêu cho một NV trong {item['targets_file']} — không tính 0 lặng lẽ")
    target = targets[code]
    if not target or target <= 0:
        raise ValueError(f"{item_id}: chỉ tiêu của một NV ≤ 0 trong {item['targets_file']}")
    # So bằng phép nhân để không vướng sai số chia số thực (bài học 10/11: so == trên số thực)
    reached = [tier for tier in item["tiers"] if sales * 100 >= tier["min"] * target]
    best = max(reached, key=lambda tier: tier["min"]) if reached else None
    # % hiện bằng ĐÚNG hàm chung của 8 KPI (2 số lẻ làm tròn xuống)
    how = (f"{_vn(sales, ',.0f')} đ ÷ {_vn(target, ',.0f')} đ = {_vn(floor_pct(sales / target), '')} %"
           + (f" ⇒ mức '{best['label']}'" if best else " ⇒ chưa tới 100 %"))
    return ExtraLine(item_id, item["title"], sales, target, best is not None, best["amount"] if best else 0, how)


def _target_of(item_id: str, targets: dict[str, float], code: str, what: str) -> float:
    if code not in targets:
        raise ValueError(f"{item_id}: thiếu chỉ tiêu {what} cho một NV — không tính 0 lặng lẽ")
    if not _positive(targets[code]):
        raise ValueError(f"{item_id}: chỉ tiêu {what} của một NV ≤ 0")
    return targets[code]


def _rate_line(item_id: str, item: dict, sales: float, targets: dict[str, float], code: str, gate) -> ExtraLine:
    """Meat 7.1–7.3 (sổ Tracking ô AU1 / BA1 / BG1): mức cao nhất mà % riêng ≥ min VÀ (có cổng) % của khoản cổng (TPCB = cả Processed Meats, chỉ tiêu
    "07") cũng ≥ min CỦA CHÍNH MỨC ĐÓ (cách đọc P1, 11/11) ⇒ tiền = làm tròn tới đồng (nửa lên — sổ không ghi cách làm tròn, giới hạn)
    của doanh số × rate_pct %, không quá `cap`. So % bằng phép nhân (như `_tier_line`).
    """
    target = _target_of(item_id, targets, code, "khoản")
    gate_sales, gate_target = (gate[0], _target_of(item_id, gate[1], code, "cổng")) if gate else (None, None)
    reached = [tier for tier in item["tiers"] if sales * 100 >= tier["min"] * target
               and (gate is None or gate_sales * 100 >= tier["min"] * gate_target)]
    best = max(reached, key=lambda tier: tier["min"]) if reached else None
    amount = 0
    if best:
        raw = (Decimal(str(sales)) * Decimal(str(best["rate_pct"])) / 100).quantize(Decimal(1), ROUND_HALF_UP)
        amount = min(int(raw), int(item["cap"]))
    how = f"{_vn(sales, ',.0f')} đ ÷ {_vn(target, ',.0f')} đ = {_vn(floor_pct(sales / target), '')} %"
    if gate:
        how += (f"; cổng {item['gate']['sub_div']} {_vn(gate_sales, ',.0f')} đ ÷ {_vn(gate_target, ',.0f')} đ"
                f" = {_vn(floor_pct(gate_sales / gate_target), '')} %")
    how += (f" ⇒ mức {_vn(best['min'])} %: {_vn(best['rate_pct'])} % × doanh số"
            + (f", trần {_vn(int(item['cap']), ',')} đ" if amount == int(item["cap"]) else "")
            if best else " ⇒ chưa tới mức")
    return ExtraLine(item_id, item["title"], sales, target, best is not None, amount, how)
