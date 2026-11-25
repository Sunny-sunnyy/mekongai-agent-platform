"""Chương trình gắn kết "1 % gia vị" (bậc 1, /2026 — kế hoạch + giả thuyết ghi trước:
luật `config/gia_vi_1pct_<kỳ>.yaml`).

Khách (điểm bán) trong danh sách chương trình đạt doanh số gia vị ≥ 110 % nền tảng ⇒ thưởng 1 % doanh số, tối đa 1.000.000 đ / khách.
Tiền trả cho KHÁCH ⇒ không cộng vào lương 8 KPI / Thưởng thêm của NV; NV chỉ để gom số theo người phụ trách.
Vì sao ngoài `core/` (dấu vân tay bản tính): khoản này không quyết số 8 KPI ⇒ thêm / sửa nó không được làm mất "Đã soát" của bảng thưởng.
Tiền đúng tới đồng: so ngưỡng bằng phép nhân số thô (không làm tròn %), 1 % làm tròn tới đồng gần nhất (0,5 lên) bằng Decimal."""
import math
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml
from openpyxl import load_workbook

REQUIRED = ("period", "dms_obj", "sales_column", "threshold_pct", "rate_pct", "cap_per_customer", "baseline")


def load_rules(path: str | Path) -> dict:
    rules = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED if key not in rules]
    if missing:
        raise ValueError(f"file luật 1 % gia vị thiếu: {', '.join(missing)}")
    for key in ("threshold_pct", "rate_pct", "cap_per_customer"):
        value = rules[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"file luật 1 % gia vị: '{key}' phải là số > 0")
    if not rules["dms_obj"]:
        raise ValueError("file luật 1 % gia vị: thiếu nhóm hàng (dms_obj)")
    return rules


def read_baseline(repo_root: Path, spec: dict) -> list[dict]:
    """Danh sách khách + nền tảng từ sheet sổ Tracking: [{customer, employee_name, baseline}]. Thiếu cột / mã trùng / nền không phải số ⇒ dừng."""
    wb = load_workbook(repo_root / spec["file"], read_only=True, data_only=True)
    try:
        if spec["sheet"] not in wb.sheetnames:
            raise ValueError(f"sổ Tracking thiếu sheet '{spec['sheet']}'")
        rows = wb[spec["sheet"]].iter_rows(min_row=spec["header_row"], values_only=True)
        header = [str(value).strip() if value is not None else "" for value in next(rows)]
        wanted = spec["columns"]
        missing = [name for name in wanted.values() if name.strip() not in header]
        if missing:
            raise ValueError(f"sheet '{spec['sheet']}' thiếu cột: {', '.join(missing)}")
        # Cột đầu tiên mang tên đó (sheet có hai khối cùng tiêu đề — khối trái = danh sách gốc; hai khối trùng nền 1.351 / 1.351, đã đếm 11/11)
        at = {key: header.index(name.strip()) for key, name in wanted.items()}
        out, seen = [], set()
        for offset, values in enumerate(rows, start=spec["header_row"] + 1):
            code = values[at["customer"]] if len(values) > at["customer"] else None
            if code in (None, ""):
                continue
            baseline = values[at["baseline"]]
            if isinstance(code, str) and baseline is None:
                # Dòng chữ không có nền = dòng tổng của bảng tổng hợp (sổ 2/11: dòng 1363, 11 ký tự, ngay sau 1.351 khách) ⇒ hết danh sách
                break
            code = str(int(code)) if isinstance(code, float) and code.is_integer() else str(code).strip()
            if isinstance(baseline, bool) or not isinstance(baseline, (int, float)):
                raise ValueError(f"sheet '{spec['sheet']}' dòng {offset}: nền tảng trống hoặc không phải số — sửa sổ rồi chạy lại")
            if code in seen:
                raise ValueError(f"sheet '{spec['sheet']}' dòng {offset}: mã khách lặp lại")
            seen.add(code)
            out.append({"customer": code, "employee_name": str(values[at["employee_name"]] or "").strip(), "baseline": float(baseline)})
    finally:
        wb.close()
    if not out:
        raise ValueError(f"sheet '{spec['sheet']}' không có khách nào")
    return out


def reward_of(sales: float, rules: dict) -> int:
    """1 % doanh số làm tròn tới đồng gần nhất (0,5 lên), tối đa trần mỗi khách."""
    raw = (Decimal(str(sales)) * Decimal(str(rules["rate_pct"])) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(min(raw, Decimal(rules["cap_per_customer"])))


def compute(lines: list[dict], rules: dict, baseline_rows: list[dict], employee_codes: dict[str, str]) -> list[dict]:
    """Từng khách: doanh số gia vị, nền, đạt, thưởng, mã NV phụ trách. `lines` = dòng RAW đã lọc như 8 KPI (có cột `rules["sales_column"]`);
    `employee_codes` = {tên NV đầy đủ: mã SM}. Nhóm hàng không có trong RAW / tên NV không ghép được ⇒ dừng (không thành 0 lặng lẽ)."""
    groups = set(rules["dms_obj"])
    missing = sorted(groups - {line["DMS OBJ"] for line in lines})
    if missing:
        raise ValueError(f"luật 1 % gia vị trỏ nhóm hàng không có trong RAW: {', '.join(missing)}")
    sales = defaultdict(float)
    for line in lines:
        if line["DMS OBJ"] in groups and line["FREE ITEM FLAG"] != "Y":
            value = line[rules["sales_column"]]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"RAW dòng Excel {line['_row']}: ô '{rules['sales_column']}' trống hoặc không phải số")
            sales[str(line["OUTLET CODE"])] += value
    unmatched = sum(1 for row in baseline_rows if row["employee_name"] not in employee_codes)
    if unmatched:
        raise ValueError(f"{unmatched} khách có tên NV trong sổ không ghép được mã SM của RAW — không gom theo NV được")
    out = []
    for row in baseline_rows:
        amount = sales.get(row["customer"], 0.0)
        # So bằng phép nhân số thô: doanh số × 100 ≥ 110 × nền (không làm tròn % trước khi so)
        reached = row["baseline"] > 0 and amount * 100 >= rules["threshold_pct"] * row["baseline"]
        out.append({"customer": row["customer"], "employee": employee_codes[row["employee_name"]], "sales": amount, "baseline": row["baseline"],
                    "reached": reached, "reward": reward_of(amount, rules) if reached else 0})
    return out


def by_employee(rows: list[dict], to_masked: dict[str, str]) -> dict:
    """Gom theo NV phụ trách (mã che) — KHÔNG trả mã / tên khách (luật công cụ: 0 mã cửa hàng ra ngoài)."""
    agg = {masked: {"employee_id": masked, "customers": 0, "customers_reached": 0, "sales": 0.0, "reward": 0} for masked in sorted(to_masked.values())}
    for row in rows:
        if row["employee"] not in to_masked:
            raise ValueError("khách thuộc một NV không có trong danh sách đội — không gom được")
        item = agg[to_masked[row["employee"]]]
        item["customers"] += 1
        item["customers_reached"] += row["reached"]
        item["sales"] += row["sales"]
        item["reward"] += row["reward"]
    employees = [{**item, "sales": round(item["sales"])} for item in agg.values()]
    return {"employees": employees, "team_customers": len(rows), "team_customers_reached": sum(row["reached"] for row in rows),
            "team_reward": sum(row["reward"] for row in rows)}


def page_totals(rows: list[dict], rules: dict) -> dict:
    """4 ô tổng mà trang theo dõi của đội có: chỉ tiêu = Σ nền, thực hiện = Σ doanh số gia vị, % đạt, và "tiềm năng thêm" = thưởng sẽ có
    nếu mỗi khách CHƯA có thưởng (nền > 0) bán vừa đủ ngưỡng — Σ min(trần, 1 % × ngưỡng × nền). Chỉ cộng từ các dòng `compute` đã tính; không trả mã / tên khách.
    """
    from kpi_agent.core.compute_bonus import floor_pct
    baseline, sales = sum(row["baseline"] for row in rows), sum(row["sales"] for row in rows)
    waiting = [row for row in rows if row["reward"] == 0 and row["baseline"] > 0]
    return {"team_baseline": round(baseline), "team_sales": round(sales), "team_pct": float(floor_pct(sales / baseline)) if baseline else None,
            "potential_customers": len(waiting),
            "potential_reward": sum(reward_of(row["baseline"] * rules["threshold_pct"] / 100, rules) for row in waiting)}


# Danh sách khách cho TRANG (trang theo dõi của đội có 1.351 thẻ khách). Có tên + mã khách ⇒ không đưa vào công cụ / chữ gửi mô hình ----------
NEAR_PCT = 70   # nhóm lọc của trang của đội: "70 % đến dưới 100 %"
CUSTOMER_GROUPS = {"co_thuong": "Có thưởng (đạt ≥ 110 % nền)", "du_nen": "Đủ 100 % nền, chưa tới 110 %", "gan_nen": "70 % đến dưới 100 % nền", "duoi_70": "Dưới 70 % nền"}


def read_customer_info(repo_root: Path, spec: dict) -> dict[str, tuple[str | None, list[str]]]:
    """{mã khách: (tên, [ngày viếng thăm])} từ sheet nền của sổ. Tên = cột "Tên điểm bán" đầu tiên; ngày viếng = cột "Viếng thăm", ghép theo cột mã khách
    đứng NGAY TRƯỚC nó (sheet có hai bảng cạnh nhau, bảng thứ hai có thể xếp thứ tự khác). Thiếu cột ⇒ ValueError (bên gọi vẫn hiện danh sách, không tên)."""
    code_name = spec["columns"]["customer"]
    wb = load_workbook(repo_root / spec["file"], read_only=True, data_only=True)
    try:
        rows = wb[spec["sheet"]].iter_rows(min_row=spec["header_row"], values_only=True)
        header = [str(value).strip() if value is not None else "" for value in next(rows)]
        missing = [name for name in (code_name, "Tên điểm bán", "Viếng thăm") if name not in header]
        if missing:
            raise ValueError(f"sheet '{spec['sheet']}' thiếu cột: {', '.join(missing)}")
        code_at, name_at, visit_at = header.index(code_name), header.index("Tên điểm bán"), header.index("Viếng thăm")
        visit_code_at = max(index for index, title in enumerate(header) if title == code_name and index < visit_at)
        names, visits = {}, {}
        for values in rows:
            values = list(values) + [None] * (visit_at + 1 - len(values))
            if values[code_at] not in (None, ""):
                names.setdefault(str(values[code_at]).strip(), str(values[name_at]).strip() if values[name_at] not in (None, "") else None)
            if values[visit_code_at] not in (None, ""):
                visits.setdefault(str(values[visit_code_at]).strip(), [part.strip() for part in str(values[visit_at] or "").split(",") if part.strip()])
        return {code: (name, visits.get(code, [])) for code, name in names.items()}
    finally:
        wb.close()


def customer_rows(rows: list[dict], rules: dict, info: dict[str, tuple[str | None, list[str]]], to_masked: dict[str, str]) -> list[dict]:
    """Một dòng cho mỗi khách từ các dòng `compute` ĐÃ tính (không tính lại): nền, doanh số, % nền, thưởng, hoặc còn cần bao nhiêu để tới ngưỡng thưởng."""
    from kpi_agent.core.compute_bonus import floor_pct
    out = []
    for row in rows:
        baseline, sales = row["baseline"], row["sales"]
        threshold = baseline * rules["threshold_pct"] / 100
        group = "co_thuong" if row["reached"] else "du_nen" if baseline > 0 and sales >= baseline else \
            "gan_nen" if baseline > 0 and sales * 100 >= NEAR_PCT * baseline else "duoi_70"
        name, visit = info.get(row["customer"], (None, []))
        waiting = not row["reached"] and baseline > 0
        out.append({"customer_code": row["customer"], "customer_name": name, "employee_id": to_masked[row["employee"]], "visit_days": visit,
                    "baseline": round(baseline), "sales": round(sales), "pct_of_baseline": float(floor_pct(sales / baseline)) if baseline > 0 else None,
                    "group": group, "reward": row["reward"],
                    # chưa có thưởng: còn cần bao nhiêu doanh số để tới ngưỡng (làm tròn LÊN tới đồng) + thưởng nếu vừa tới ngưỡng
                    "need_more": math.ceil(threshold - sales) if waiting else None, "reward_at_threshold": reward_of(threshold, rules) if waiting else None})
    out.sort(key=lambda item: (item["employee_id"], item["customer_code"]))
    return out
