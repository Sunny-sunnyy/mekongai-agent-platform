"""Bổ sung cho kết quả `get_mid_month_gap` ở CỬA RA của backend.

Vì sao có file này: `queries.get_mid_month_gap` chỉ liệt kê chỉ tiêu CÒN mốc kế ⇒ chỉ tiêu đã ở mốc cao nhất không có dòng nào, mô hình đọc
thành "không ghi nhận khoảng thiếu" (User gặp thật 12/11: "NV09 còn thiếu bao nhiêu PC_BT?" trong khi NV09 đã đạt mốc cao nhất, có tiền).
Vì sao KHÔNG sửa trong `queries.py`: file đó nằm trong dấu vân tay bản tính (`main.CODE_FILES`) — sửa là mất dấu "Đã soát" dù không số nào đổi.
Ở đây chỉ ĐỌC số lõi đã tính (`ctx.bonuses`), không tính lại gì."""

TOP_TIER_STATUS = "đã đạt mốc cao nhất — không còn mốc kế"
# Chữ KHÔNG có số (rào kiểm số coi mọi số trong câu trả lời là số phải có nguồn)
PERIOD_CLOSED_NOTE = "kỳ đã hết ngày làm việc — không còn lên mốc được; 'còn thiếu' chỉ cho biết đã hụt bao nhiêu"


def add_gap_status(ctx, result: dict) -> dict:
    """Thêm `at_top_tier` cho từng nhân viên và `period_closed` khi kỳ hết ngày. Lời từ chối (không có `employees`) trả nguyên."""
    if "employees" not in result:
        return result
    for employee in result["employees"]:
        lines = ctx.bonuses[ctx.to_real[employee["employee_id"]]]["lines"]
        # Số nằm trong dict có `employee_id` của chính người đó ⇒ rào "chủ của số" đọc được tiền này là của ai
        employee["at_top_tier"] = [{"kpi": line.kpi, "reached_tier": line.reached_label, "amount_now": line.amount, "status": TOP_TIER_STATUS}
                                   for line in lines if line.next_label is None and line.reached_label is not None]
    if ctx.days_left == 0:
        result["period_closed"] = True
        result["period_note"] = PERIOD_CLOSED_NOTE
    return result
