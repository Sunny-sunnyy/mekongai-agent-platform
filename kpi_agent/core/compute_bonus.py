"""Khối 4 — mốc ⇒ tiền (lớp "bảng mức trả"), tách khỏi lớp đếm ở compute_kpi.py.

So mốc bằng Decimal để "đúng 95 %" không bị lỗi số thực (vd 0.95 * 941438285 lệch phần nghìn đồng).
Tiền làm tròn tới đồng; công văn không ghi cách làm tròn — mọi mức tiền của tháng 10 đều là số chẵn nên không ảnh hưởng.
"""
import math
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from kpi_agent.core.compute_kpi import KpiResult
from kpi_agent.core.rules import KPI_ORDER

COUNT_KPIS = {"PC_BT", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON"}


@dataclass
class BonusLine:
    kpi: str
    reached_label: str | None   # mốc đạt (chữ công văn) — None nếu chưa đạt mốc nào
    amount: int                 # tiền (đồng)
    reason: str                 # câu giải thích có số
    next_label: str | None      # mốc kế tiếp chưa đạt
    gap_to_next: float | None   # còn thiếu bao nhiêu (cùng đơn vị với thực hiện) để lên mốc kế tiếp


def round_2dp(value: float, rounding: str) -> Decimal:
    """Hiện số 2 chữ số lẻ theo chiều chỉ định (ROUND_FLOOR / ROUND_CEILING), không dính lỗi số thực.

    round(value, 9) bỏ phần dư nhị phân (3.8000000000000003 ⇒ 3.8) trước khi làm tròn — dữ liệu thật không có quá 9 chữ số lẻ có nghĩa.
    Chỉ dùng để HIỆN số; phép so mốc vẫn dùng Decimal của số gốc trong bonus_for_kpi.
    """
    return Decimal(str(round(value, 9))).quantize(Decimal("0.01"), rounding)


def floor_pct(fraction: float) -> Decimal:
    """% thực hiện để HIỆN: 2 chữ số lẻ, làm tròn XUỐNG (94,9999 % không hiện 95,00 % khi chưa tới mốc 95 %).

    MỘT hàm cho mọi chỗ hiện % — lý do mốc, lý do thưởng vượt, trường `pct` của công cụ (`backend/queries.py` `_pct`):
    "% trợ lý viết phải đúng y số công cụ" ⇒ chữ lý do và ô số không được làm tròn hai cách (trước: thưởng vượt in `.1f` "60.5 %" khi ô ghi 60.53).
    
    """
    return round_2dp(fraction * 100, ROUND_FLOOR)


def _vn(value, spec: str = "g") -> str:
    """Số viết kiểu Việt cho chữ giải thích: 10693500 ⇒ "10.693.500" (spec ",.0f"), 172.50 ⇒ "172,50", 2.5 ⇒ "2,5". Chữ này hiện nguyên trên trang
    và đi vào kết quả công cụ gửi mô hình; mọi chỗ khác đã viết kiểu Việt (12/11: màn Thưởng thêm hiện "10,693,500 đ … 172.50 %").
    Nằm ở đây (không ở compute_extra_bonus) vì lý do thưởng vượt cũng dùng, và compute_extra_bonus đã nhập từ file này."""
    return format(value, spec).replace(",", "\0").replace(".", ",").replace("\0", ".")


def kpi_tiers(rules: dict, kpi: str, region: str) -> list[dict]:
    """Danh sách mốc của một KPI; LPPC lấy theo miền (mục 2.3)."""
    config = rules["kpis"][kpi]
    if config["measure"] != "value_by_region":
        return config["tiers"]
    lppc_region = rules["region_map"].get(region)
    if lppc_region is None:
        raise ValueError(f"vùng {region!r} chưa có trong region_map — không đoán mốc LPPC")
    return config["region_tiers"][lppc_region]


def bonus_for_kpi(result: KpiResult, rules: dict, region: str) -> BonusLine:
    config = rules["kpis"][result.kpi]
    tiers = kpi_tiers(rules, result.kpi, region)
    by_pct = config["measure"] == "pct_of_target"
    actual = Decimal(str(result.actual))
    reached, next_tier = None, None
    for tier in tiers:
        # Mốc theo % chỉ tiêu: thực hiện ≥ min% × chỉ tiêu; mốc giá trị (LPPC): thực hiện ≥ min
        needed = Decimal(str(tier["min"])) / 100 * Decimal(str(result.target)) if by_pct else Decimal(str(tier["min"]))
        if actual >= needed:
            reached = tier
        elif next_tier is None:
            next_tier = (tier, needed)

    amount = 0
    if reached:
        amount = int((Decimal(config["amount_100"]) * Decimal(str(reached["pay_ratio"]))).quantize(Decimal(1), ROUND_HALF_UP))
    # 2 chữ số lẻ, làm tròn XUỐNG: 94,9999 % in "95.00%" kèm "chưa tới mốc 95%" sẽ gây hiểu nhầm (B ghi chú 02/11 — tối)
    shown = f"{floor_pct(result.pct)}%" if by_pct else f"{round_2dp(result.actual, ROUND_FLOOR)}"
    if reached:
        reason = f"{result.kpi}: thực hiện {shown} ⇒ đạt mốc '{reached['label']}' ⇒ {_vn(amount, ',')} đ"   # tiền kiểu Việt (13/11: trang hiện "2,200,000 đ")
    else:
        reason = f"{result.kpi}: thực hiện {shown} — chưa tới mốc thấp nhất '{tiers[0]['label']}' ⇒ 0 đ"
    gap = float(next_tier[1] - actual) if next_tier else None
    if gap is not None and result.kpi in COUNT_KPIS:
        gap = float(math.ceil(gap))   # đếm đơn / cửa hàng: thiếu 6,8 nghĩa là cần thêm 7
    return BonusLine(result.kpi, reached["label"] if reached else None, amount, reason,
                     next_tier[0]["label"] if next_tier else None, gap)


def over_target_bonus(target: float, actual: float, npp_turnover_pct: float | None, rules: dict) -> tuple[int | None, str]:
    """Mục 7.2 — thưởng vượt doanh số do NPP chi. Trả (tiền hoặc None nếu thiếu dữ kiện, lý do)."""
    config = rules["over_target_bonus"]
    employee_pct = Decimal(str(actual)) / Decimal(str(target)) * 100   # so ngưỡng bằng số gốc (không đổi từ trước)
    if employee_pct <= config["employee_above_pct"]:
        # Chữ hiện % bằng đúng cách của ô `pct` Turnover (KpiResult.pct = actual / target ⇒ floor_pct) — chọn (b)
        return 0, f"doanh số đạt {floor_pct(actual / target)} % ≤ {config['employee_above_pct']} % ⇒ không có thưởng vượt"
    if npp_turnover_pct is None:
        return None, "thiếu dữ kiện: chưa có % doanh số cả NPP (cần ≥ 100 %)"
    if npp_turnover_pct < config["npp_min_pct"]:
        return 0, f"NPP đạt {npp_turnover_pct} % < {config['npp_min_pct']} % ⇒ không có thưởng vượt"
    raw = (Decimal(str(actual)) - Decimal(str(target))) * Decimal(config["rate_pct"]) / 100
    amount = int(min(raw, Decimal(config["cap"])).quantize(Decimal(1), ROUND_HALF_UP))
    capped = " (chạm trần)" if raw > config["cap"] else ""
    # Số tiền kiểu Việt (13/11): trang + trợ lý từng hiện "2,200,000 đ" ở đúng dòng này trong khi mọi chỗ khác viết "2.200.000 đ"
    return amount, (f"{config['rate_pct']} % × ({_vn(actual, ',.0f')} − {_vn(target, ',.0f')}) = {_vn(raw, ',.0f')} đ"
                    f" ⇒ {_vn(amount, ',')} đ{capped}")


def employee_bonus(results: dict[str, KpiResult], rules: dict, region: str,
                   npp_turnover_pct: float | None = None) -> dict:
    """Bảng tiền của một NV: 8 dòng KPI + tổng lương kinh doanh (công ty chi) + thưởng vượt (NPP chi)."""
    lines = [bonus_for_kpi(results[k], rules, region) for k in KPI_ORDER]
    turnover = results["Turnover"]
    over_amount, over_reason = over_target_bonus(turnover.target, turnover.actual, npp_turnover_pct, rules)
    return {"lines": lines, "business_salary": sum(line.amount for line in lines),
            "over_target_bonus": over_amount, "over_target_reason": over_reason}
