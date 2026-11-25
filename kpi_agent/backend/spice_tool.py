"""Công cụ 12 `get_spice_loyalty` — chương trình "1 % gia vị" cho trợ lý (kế hoạch 14:24 12/11).

Tiền của chương trình này trả cho KHÁCH HÀNG (điểm bán), không phải cho nhân viên. Rủi ro chính là câu trả lời gán nhầm người nhận
("NV05 được thưởng gia vị … đ"). Rào kiểm số coi MỌI ô số nằm trong một dict có khoá `employee_id` là số của nhân viên đó
(`agent/owner_check.py` — `actual_money_by_employee`). Vì vậy kết quả ở đây CỐ Ý không có khoá `employee_id` ở dòng nào: nhân viên phụ trách
ghi ở khoá `salesperson_in_charge` ⇒ tiền thưởng khách không bao giờ thành "tiền của NVxx" trong mắt rào (: sửa bằng HÌNH dữ liệu, không
thêm luật dò chữ). Câu đúng muốn qua rào thì chép nguyên `customer_reward_sentences` (câu do code viết — rào tính lại rồi che, như câu chuẩn cả đội).

File riêng, ngoài `queries.py` / `core/` (dấu vân tay bản tính) — như `extra_bonus_tool.py`. Không tính gì mới: bọc kết quả `main.spice()`."""
from kpi_agent.agent.owner_check import spice_sentence
from kpi_agent.backend import queries

RECIPIENT = "khách hàng (điểm bán) — KHÔNG phải nhân viên"
NOTE = ("Chương trình 1 % gia vị: tiền thưởng trả cho KHÁCH HÀNG (điểm bán) đạt mức mua gia vị, KHÔNG phải lương hay thưởng của nhân viên; "
        "không cộng vào 8 KPI, không cộng vào Thưởng thêm. Số ở đây gom theo nhân viên PHỤ TRÁCH khách. "
        "Khi nêu số tiền của một nhân viên phụ trách, chép NGUYÊN câu trong 'customer_reward_sentences'.")


def get_spice_loyalty(ctx: queries.KpiContext, spice: dict, employee_id: str | None = None, period: str | None = None) -> dict:
    """Tiền 1 % gia vị trả khách, gom theo nhân viên phụ trách (mã che) — một người hoặc cả đội. `spice` = kết quả `main.spice()` (đã tính / lời từ chối)."""
    code = None
    if employee_id not in (None, ""):
        _real, refusal = queries._check_employee(ctx, employee_id)
        if refusal:
            return queries._refused(refusal, employee_id=employee_id, period=period)
        code = employee_id.strip().upper()
    if refusal := queries._check_period(ctx, period):
        return queries._refused(refusal, employee_id=employee_id, period=period)
    if "refused" in spice:
        return queries._refused(spice["refused"], employee_id=employee_id, period=period)
    rows = [{"salesperson_in_charge": row["employee_id"], "recipient": RECIPIENT, "customers_in_program": row["customers"],
             "customers_reached": row["customers_reached"], "spice_sales_of_customers": row["sales"],
             "reward_paid_to_customers": row["reward"]}
            for row in spice["employees"] if code is None or row["employee_id"] == code]
    body = {"by_salesperson_in_charge": rows, "customer_reward_sentences": [spice_sentence(row) for row in rows]}
    if code is None:
        body["team"] = {"recipient": RECIPIENT, "customers_in_program": spice["team_customers"],
                        "customers_reached": spice["team_customers_reached"], "reward_paid_to_customers": spice["team_reward"]}
    # params_used KHÔNG có khoá employee_id ở cấp ngoài cùng của kết quả (chỉ nằm trong params_used — rào bỏ qua nhánh đó)
    return queries._envelope(ctx, {"employee_id": code or "cả đội", "period": ctx.period}, {**body, "note": NOTE})
