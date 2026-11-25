"""Tính năng 2 bản đầu — "Có cơ hội lên bậc" (, (a) "theo nhịp" 03/11).

Quy tắc R2 của nghiên cứu ( mục 12.1), đúng chữ "cần mỗi ngày ≤ nhịp đang chạy":
  cơ hội ⇔ phần còn thiếu ≤ nhịp THẬT (thực hiện ÷ ngày làm việc đã qua) × ngày làm việc còn lại — so KHÔNG làm tròn.
Làm tròn chỉ để HIỆN số ("ít nhất ⌈cần⌉/ngày", nhịp 2 chữ số lẻ). Bản đầu so "cần làm tròn lên ≤ nhịp làm tròn xuống" — lỗi cài,
03/11 trước khi User thấy số (loại oan 5/68 cặp PC_ON, vd thiếu 14, nhịp 1,94 × 10 ngày ≈ 19,4). KPI tỷ lệ (LPPC, LPPC_Meat) ⇒ None.
KHÔNG xếp hạng người: không có điểm / thứ hạng; thứ tự = mã NV rồi thứ tự KPI. Chỉ là gợi ý theo tiến độ — không biết lịch tuyến còn lại,
nghỉ phép, hết hàng NPP (nghiên cứu: "không có công thức chung") ⇒ nhãn "chưa dự báo". Endpoint cho trang; agent KHÔNG đọc."""
import math
from decimal import ROUND_FLOOR

from kpi_agent.backend.queries import (KPI_ORDER, KpiContext, _check_period, _envelope, _masked_of, _refused, get_mid_month_gap,
                                       round_2dp)

LABEL = "Cơ hội theo tiến độ, chưa dự báo — giữ nhịp như từ đầu kỳ tới ngày chốt thì có tới mốc kế không"
RATIO_KPIS = {"LPPC", "LPPC_Meat"}
# 03/11: ghi chú cố định cho KPI đếm cửa hàng — cửa hàng "mới" khó dần về cuối tháng nên nhịp trung bình dễ lạc quan
ASO_NOTE = "nhịp là trung bình cả kỳ — cửa hàng mới thường chậm dần cuối tháng"


def is_opportunity(gap, pace_per_day, days_left) -> bool | None:
    """Giữ nhịp thật tới hết kỳ có bù đủ phần thiếu không. Ca biên ghi trước: vừa ĐÚNG BẰNG ⇒ là cơ hội."""
    if gap is None or pace_per_day is None:
        return None
    return gap <= pace_per_day * days_left


def _shown_pace(kpi: str, per_day: float) -> float:
    # Chỉ để hiện: Turnover theo đồng, KPI đếm 2 chữ số lẻ, làm tròn XUỐNG (không hiện nhịp cao hơn thật)
    return math.floor(per_day) if kpi == "Turnover" else float(round_2dp(per_day, ROUND_FLOOR))


def vn_number(value) -> str:
    """Số kiểu Việt cho câu `why` trang hiện nguyên chữ: 38.460.109 · 1,94."""
    text = f"{value:,.2f}".rstrip("0").rstrip(".") if isinstance(value, float) and not float(value).is_integer() else f"{int(value):,}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def opportunities(ctx: KpiContext, period: str | None = None) -> dict:
    if refusal := _check_period(ctx, period):
        return _refused(refusal, period=period)
    real_of = {masked: real for real, masked in _masked_of(ctx).items()}
    days_done = ctx.days_total - ctx.days_left
    items = []
    for employee in get_mid_month_gap(ctx)["employees"]:   # cùng số "còn thiếu" với /api/gap — không tính lại
        for gap in employee["gaps"]:
            kpi = gap["kpi"]
            real = real_of[employee["employee_id"]]
            line = next(l for l in ctx.bonuses[real]["lines"] if l.kpi == kpi)   # phần thiếu THẬT (chưa làm tròn để hiện)
            raw_pace = None if kpi in RATIO_KPIS or days_done <= 0 else ctx.results[real][kpi].actual / days_done
            need = gap["gap_per_working_day_left"]
            chance = is_opportunity(line.gap_to_next, raw_pace, ctx.days_left)
            pace = None if raw_pace is None else _shown_pace(kpi, raw_pace)
            if chance is None:
                why = "KPI tỷ lệ — không tính theo nhịp"
            elif ctx.days_left <= 0:
                # Ngày chốt = ngày làm việc cuối kỳ (đường RAW 31/10): không còn ngày ⇒ "cần / ngày" rỗng — 10/11: vn_number(None) ⇒ 500 ở
                # /api/opportunities và /api/system/kiem-so (nguồn cả đội). Nói thẳng là hết ngày, không in nhịp
                why = f"đã hết kỳ — không còn ngày để tính mỗi ngày cần bao nhiêu; còn thiếu {vn_number(gap['gap_to_next'])} để lên mốc kế"
            else:
                # TN2b (minor): "nhịp hiện × ngày" phải ra đúng tích hiện ⇒ tích tính từ CHÍNH nhịp đã hiện. Nếu làm tròn nhịp làm
                # tích hiện nói ngược kết luận (thật là tới, hiện thì chưa) ⇒ hiện nhịp 4 chữ số lẻ cho khớp
                if (pace * ctx.days_left >= line.gap_to_next) != chance:
                    pace = math.floor(raw_pace) if kpi == "Turnover" else float(round_2dp(raw_pace * 100, ROUND_FLOOR)) / 100
                reach = pace * ctx.days_left
                # "trung bình" (không "ít nhất"): need nay là trung bình không làm tròn (bậc "ít nhất N" bản sửa — C, chiều)
                why = (f"cần trung bình {vn_number(need)}/ngày; giữ nhịp {vn_number(pace)}/ngày × {ctx.days_left} ngày còn lại ≈ {vn_number(reach)}, "
                       f"còn thiếu {vn_number(gap['gap_to_next'])} ⇒ " + ("là tới mốc kế" if chance else "phải nhanh hơn nhịp hiện tại"))
            items.append({"employee_id": employee["employee_id"], "kpi": kpi, "next_tier": gap["next_tier"],
                          "gap_to_next": gap["gap_to_next"], "gap_per_working_day_left": need, "pace_per_working_day": pace,
                          "amount_now": gap["amount_now"], "amount_if_reached": gap["amount_if_reached"],
                          "amount_gain": gap["amount_if_reached"] - gap["amount_now"], "is_opportunity": chance, "why": why,
                          "note": ASO_NOTE if kpi.startswith("ASO_") else None})
    items.sort(key=lambda item: (item["employee_id"], KPI_ORDER.index(item["kpi"])))
    body = {"label": LABEL, "working_days_left": ctx.days_left, "working_days_done": days_done, "items": items,
            "opportunity_count": sum(item["is_opportunity"] is True for item in items)}
    return _envelope(ctx, {"period": ctx.period}, body)
