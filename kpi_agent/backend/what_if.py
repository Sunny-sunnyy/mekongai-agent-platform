"""Công cụ 4 — "nếu… thì…" (what-if) cho MỘT KPI của MỘT nhân viên.

Câu giám sát hỏi: "Nếu NV07 thêm 5 đơn PC_BT thì KPI đó được bao nhiêu tiền?" — `get_mid_month_gap` chỉ trả mốc KẾ TIẾP, không trả "thêm N tuỳ ý".
Vì sao code tính, không để mô hình nhẩm: tiền đi theo mốc của công văn; chỉ hàm lõi `core/compute_bonus.bonus_for_kpi` (đã khớp 112 / 112) được quyết.
Nghiên cứu (tra_cuu_cli/2026-11-04_cong-cu-kpi-e/KIEM_E.md): 5 hãng phần mềm thưởng có "what-if"; Spiff ghi rõ KHÔNG hỗ trợ luật dựa trên đếm — ở đây làm được vì
lõi đã có "số đếm ⇒ mốc ⇒ tiền": cộng `extra` vào số thực hiện rồi chạy lại đúng hàm đó.

Giới hạn (nói ra trong mọi kết quả — `assumptions`):
- KPI tỷ lệ (LPPC, LPPC_Meat = số dòng ÷ số đơn) ⇒ TỪ CHỐI: thêm một số vào tử không cho ra tỷ lệ chắc chắn (không biết thêm bao nhiêu đơn).
- Chỉ tính lại ĐÚNG KPI được hỏi: thêm đơn có thể làm đổi LPPC, thêm doanh số có thể đổi thưởng vượt — công cụ không tính các phần đó.
- Giả định phần thêm ĐẠT đủ điều kiện công văn (đơn đạt MOQ / đủ dòng; cửa hàng là cửa hàng MỚI trong MCP) — không kiểm được vì thiếu dữ liệu từng đơn.
- Trần `extra` = mức của mốc CAO NHẤT (% mốc cao nhất × chỉ tiêu) — thêm quá mức đó không đổi được tiền nữa; chặn số rác kiểu 10^9.
  (G2, minor 1: bản đầu lấy trần = chỉ tiêu — đủ cho tháng 10 vì mốc cao nhất 100 %, nhưng kỳ có mốc > 100 % sẽ chặn câu thật.)
A-ii-1 (4 câu lúc 11:57): công cụ nhận MỘT trong hai kiểu giả định
`extra` (nếu THÊM N) hoặc `target_pct` (nếu ĐẠT X % chỉ tiêu, 0 < X ≤ 300). Trước đây câu "nếu đạt X %" nằm ở mô tả `explain_kpi` (mô hình tự đối chiếu mốc)
⇒ hai công cụ cùng nhận câu "nếu…", câu giả định KPI tỷ lệ bị đẩy nhầm sang `explain_kpi` (kiểm cuối 04/11: 2 / 2 lượt — tài liệu nội bộ).
"Đạt X %" so mốc bằng X TRỰC TIẾP: thực hiện giả định = X % × chỉ tiêu (tính bằng Decimal ⇒ X đúng bằng mốc thì đạt mốc); KPI đếm có thể ra số lẻ — ghi ở `assumptions`.
File này NGOÀI dấu vân tay bản tính (`backend/main.py` CODE_FILES): nó không đổi số đã lưu, chỉ tính kịch bản trên số đang có."""
import dataclasses
from decimal import Decimal, InvalidOperation

from kpi_agent.backend.queries import (KPI_ORDER, KpiContext, cong_van_section, _check_employee, _check_kpi, _check_period, _envelope, _pct, _refused,
                                       _short, _shown)
from kpi_agent.core.compute_bonus import COUNT_KPIS, bonus_for_kpi, kpi_tiers

# Mã sai (T2, minor): KHÔNG nhắc lại nguyên văn chữ người gọi gửi — chữ đó có thể là mã NV THẬT (quy ước S2: lời lỗi
# không chứa mã thật). `_check_employee` dùng chung của queries.py vẫn nhắc lại — 3 công cụ cũ còn như vậy (queries.py trong dấu vân tay ⇒ bậc riêng).
# Chuẩn hoá đầu vào (SAU khi thấy kết quả T2, 04/11): "nv07", " NV07 " ⇒ NV07 — bỏ khoảng trắng + không phân biệt hoa thường, như 4 công cụ cũ.
BAD_EMPLOYEE = "mã nhân viên không hợp lệ hoặc không thuộc đội — chỉ nhận NV01…NV{last:02d}"
ADDITIVE_KPIS = COUNT_KPIS | {"Turnover"}   # cộng thêm được: đếm đơn / cửa hàng + doanh số (đồng)
PCT_CAP = Decimal(300)   # trần "đạt X %" — cùng khoảng 0–300 với ô % doanh số NPP của khối tính thưởng
LABEL = "KỊCH BẢN GIẢ ĐỊNH — không phải dự báo, không phải số chốt; chỉ tính lại đúng KPI được hỏi theo mốc công văn"
UNIT = {"Turnover": "đồng doanh số", "PC_BT": "đơn đạt chuẩn (Off)", "PC_ON": "đơn đạt chuẩn (On)", "ASO_ALL": "cửa hàng mới có mua",
        "ASO_Focus": "cửa hàng mới mua nhóm trọng tâm", "ASO_Focus_2": "cửa hàng mới mua nhóm trọng tâm 2"}


def _check_extra(kpi: str, extra, cap: Decimal) -> tuple[Decimal | None, str | None]:
    """`extra` = số thêm (≥ 0; KPI đếm phải là số nguyên; ≤ `cap` = mức mốc cao nhất). Nhận số hoặc chữ số (tham số HTTP là chữ)."""
    if isinstance(extra, bool) or not isinstance(extra, (int, float, str)):
        return None, f"extra {_short(extra)} phải là một số"
    try:
        value = Decimal(str(extra).strip())
    except InvalidOperation:
        return None, f"extra {_short(extra)} phải là một số"
    if not value.is_finite() or value < 0:
        return None, f"extra {_short(extra)} phải là số ≥ 0"
    if kpi in COUNT_KPIS and value != value.to_integral_value():
        return None, f"extra cho {kpi} phải là số nguyên ({UNIT[kpi]})"
    if value > cap:
        return None, f"extra {_short(extra)} vượt {cap:,.0f} — mức của mốc cao nhất {kpi}; thêm hơn nữa không đổi tiền"
    return value, None


def _check_target_pct(target_pct) -> tuple[Decimal | None, str | None]:
    """`target_pct` = "đạt X % chỉ tiêu": số, 0 < X ≤ 300. Nhận số hoặc chữ số (tham số HTTP là chữ)."""
    if isinstance(target_pct, bool) or not isinstance(target_pct, (int, float, str)):
        return None, f"target_pct {_short(target_pct)} phải là một số (% chỉ tiêu)"
    try:
        value = Decimal(str(target_pct).strip())
    except InvalidOperation:
        return None, f"target_pct {_short(target_pct)} phải là một số (% chỉ tiêu)"
    if not value.is_finite() or not 0 < value <= PCT_CAP:
        return None, f"target_pct {_short(target_pct)} phải trong khoảng 0 < X ≤ {PCT_CAP} (% chỉ tiêu)"
    return value, None


def simulate_kpi_change(ctx: KpiContext, employee_id: str, kpi: str, extra=None, period: str | None = None, target_pct=None) -> dict:
    given = {"extra": extra not in (None, ""), "target_pct": target_pct not in (None, "")}
    received = {"kpi": kpi, "extra": extra, "target_pct": target_pct, "period": period}
    real, refusal = _check_employee(ctx, employee_id)
    if refusal:
        return _refused(BAD_EMPLOYEE.format(last=len(ctx.to_real)), employee_id=None, **received)
    name, kpi_refusal = _check_kpi(kpi)
    refusal = kpi_refusal or _check_period(ctx, period)
    if not refusal and given["extra"] == given["target_pct"]:
        refusal = "cần đúng MỘT trong hai: extra (nếu thêm N) hoặc target_pct (nếu đạt X % chỉ tiêu)"
    if not refusal and name not in ADDITIVE_KPIS:
        # KPI tỷ lệ (dòng ÷ đơn, mốc theo GIÁ TRỊ theo miền) — cả hai kiểu giả định đều không có đáp số chắc (11:57: từ chối rõ)
        refusal = (f"{name} là KPI tỷ lệ — không tính được 'nếu thêm' (cần biết thêm bao nhiêu đơn và bao nhiêu dòng)" if given["extra"] else
                   f"{name} là KPI tỷ lệ, mốc theo giá trị — không tính được 'nếu đạt X % chỉ tiêu'")
    if refusal:
        return _refused(refusal, employee_id=employee_id.strip().upper(), **received)
    now = ctx.results[real][name]
    region = ctx.regions[real]
    target = Decimal(str(now.target))
    if given["extra"]:
        # 6 KPI cộng được đều đo theo % chỉ tiêu (config `measure: pct_of_target`) ⇒ mức mốc cao nhất = % lớn nhất × chỉ tiêu
        top_pct = max(Decimal(str(tier["min"])) for tier in kpi_tiers(ctx.rules, name, region))
        value, problem = _check_extra(name, extra, top_pct / 100 * target)
        new_actual = None if problem else Decimal(str(now.actual)) + value
    else:
        value, problem = _check_target_pct(target_pct)
        # So mốc bằng X trực tiếp: thực hiện giả định = X % × chỉ tiêu, tính Decimal ⇒ lõi so "thực hiện ≥ min % × chỉ tiêu" ra đúng bằng ở ca biên
        new_actual = None if problem else value / 100 * target
    if problem:
        return _refused(problem, employee_id=employee_id.strip().upper(), **received)

    line_now = ctx.bonuses[real]["lines"][KPI_ORDER.index(name)]
    added = float(new_actual)
    scenario = dataclasses.replace(now, actual=added, numerator=added)   # KPI cộng được: tử số = thực hiện (KPI đếm: mẫu số 1)
    line_new = bonus_for_kpi(scenario, ctx.rules, region)

    def state(result, line):
        return {"actual": _shown(name, result.actual), "pct": _pct(result.pct), "reached_tier": line.reached_label, "amount": line.amount,
                "next_tier": line.next_label, "gap_to_next": _shown(name, line.gap_to_next, need_at_least=True)}

    assumptions = ["chỉ tính lại KPI này; KPI khác (vd LPPC khi thêm đơn) và thưởng vượt không được tính lại",
                   "số hiện tại là số chụp giữa tháng, chưa phải số chốt"]
    if given["extra"]:
        shown = {"mode": "them", "extra": int(value) if name in COUNT_KPIS else float(value), "extra_unit": UNIT[name]}
        assumptions.insert(0, "phần thêm được giả định đạt đủ điều kiện công văn (không kiểm được — thiếu dữ liệu từng đơn)")
    else:
        shown = {"mode": "dat_phan_tram", "target_pct": float(value)}
        assumptions.insert(0, f"giả định đạt đúng {value} % chỉ tiêu: mốc so bằng % này trực tiếp; số thực hiện quy đổi = {value} % × chỉ tiêu "
                              "(KPI đếm có thể ra số lẻ — không phải số đơn / cửa hàng thật)")
    # cong_van_section: D_A2 10/11 (minor, sửa trước gộp) — thiếu ô này mô hình tự đoán mục ("PC_ON mục 2.3", đúng 2.7), như explain_kpi đã có
    body = {"label": LABEL, "employee_id": employee_id.strip().upper(), "kpi": name, **cong_van_section(ctx, name), **shown,
            "target": now.target, "now": state(now, line_now), "scenario": state(scenario, line_new),
            "amount_change": line_new.amount - line_now.amount, "assumptions": assumptions}
    # params_used = đúng tham số người gọi gửi (không có `mode` / đơn vị) ⇒ kiểu "nếu thêm N" giữ y hợp đồng 04/11
    used = {"extra": shown["extra"]} if given["extra"] else {"target_pct": shown["target_pct"]}
    return _envelope(ctx, {"employee_id": employee_id.strip().upper(), "kpi": name, **used, "period": ctx.period}, body)
