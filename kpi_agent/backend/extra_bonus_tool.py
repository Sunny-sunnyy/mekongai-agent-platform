"""Công cụ 11 `get_extra_bonus` — Thưởng thêm 9 khoản (01–06 + Meat 7.1–7.3) NGOÀI 8 KPI cho trợ lý (bậc (b) 11/11,

Vì sao file riêng, không đặt trong `queries.py` (11/11, bản 2): `queries.py`, `core/`, `workflow/` nằm trong dấu vân tay code của bản tính
(`main.CODE_FILES`) — sửa chúng ⇒ bản tính mới ⇒ mất "Đã soát" + `data_version` của Capitol đổi, dù 8 KPI không đổi số nào. Thưởng thêm không quyết
số 8 KPI ⇒ để ngoài dấu vân tay. Dùng lại các hàm kiểm tham số của `queries.py` (gọi, không sửa) để lời từ chối giống hệt 10 công cụ kia."""
from kpi_agent.backend import queries

NOTE = ("Thưởng thêm là các khoản NGOÀI 8 KPI (luật lấy từ chữ in trên sổ theo dõi của đội; công văn số 21 không có các khoản này), trả riêng — "
        "không nằm trong lương kinh doanh 8 KPI. Tổng 8 KPI + thưởng thêm do code cộng sẵn; thưởng vượt doanh số (nhà phân phối chi) không gộp.")


def get_extra_bonus(ctx: queries.KpiContext, extra: dict | str, employee_id: str | None = None, period: str | None = None) -> dict:
    """Các khoản thưởng thêm của một NV hoặc cả đội, mã che. `extra` = kết quả `main._extra_bonus` (đọc lại RAW — cần workflow nên tính ở main) hoặc lời lỗi (chuỗi).

    Rào kiểm số gán mỗi số cho `employee_id` gần nhất bao nó; số ở cấp đội thì MỌI NV đều "có nguồn". Nên:
    - tổng 8 KPI + thưởng thêm do CODE cộng (lớp 1 — AI không tự cộng), mỗi số của NV nằm trong dict có `employee_id` của NV đó (không `{mã: số}`);
    - tổng một khoản mà chỉ ĐÚNG MỘT NV có tiền = tiền của người đó ⇒ đặt trong dict có `employee_id` người đó, không để số trần ở cấp đội
      (lỗ trên bản 1: "NV05 được thưởng thêm 4.176.000" qua — đó là khoản 01 của người duy nhất có khoản 01).
    """
    real = code = None
    if employee_id not in (None, ""):
        real, refusal = queries._check_employee(ctx, employee_id)
        if refusal:
            return queries._refused(refusal, employee_id=employee_id, period=period)
        code = employee_id.strip().upper()
    if refusal := queries._check_period(ctx, period):
        return queries._refused(refusal, employee_id=employee_id, period=period)
    if isinstance(extra, str):
        return queries._refused(f"thưởng thêm không tính được — {extra}", employee_id=employee_id, period=period)
    items = {item["id"]: item for item in extra["items"]}
    # Giới hạn ĐẶT CẠNH dòng khoản (chỉ để cuối thì AI dễ bỏ) — chữ lấy từ file luật (`limit_note`), không có chữ số
    lines = [{**line, "title": items[line["item"]]["title"],
              **({"gioi_han": items[line["item"]]["limit_note"]} if items[line["item"]].get("limit_note") else {})}
             for line in extra["lines"] if code is None or line["employee_id"] == code]
    if code:
        salary, extra_total = ctx.bonuses[real]["business_salary"], extra["employee_total"][code]
        body = {"employee_id": code, "lines": lines, "extra_bonus_total": extra_total, "business_salary_8_kpi": salary,
                "total_8_kpi_and_extra": salary + extra_total}
    else:
        masked = queries._masked_of(ctx)
        employees = [{"employee_id": masked[real_code], "extra_bonus_total": extra["employee_total"][masked[real_code]],
                      "business_salary_8_kpi": ctx.bonuses[real_code]["business_salary"],
                      "total_8_kpi_and_extra": ctx.bonuses[real_code]["business_salary"] + extra["employee_total"][masked[real_code]]}
                     for real_code in sorted(masked, key=masked.get)]
        team_8_kpi = sum(row["business_salary_8_kpi"] for row in employees)
        body = {"lines": lines, "employees": employees, "items": [_item_total(item_id, extra["lines"]) for item_id in items],
                "extra_bonus_team_total": extra["team_total"], "team_total_8_kpi": team_8_kpi,
                "team_total_8_kpi_and_extra": team_8_kpi + extra["team_total"]}
    return queries._envelope(ctx, {"employee_id": code or "cả đội", "period": ctx.period}, {**body, "note": NOTE})


def _item_total(item_id: str, lines: list[dict]) -> dict:
    """Tổng một khoản cả đội + ai có tiền — cùng tên ô với `get_team_kpi_summary` (`paid_count`, `paid_employee_ids`) để luật rào chung (B, owner_check)
    đọc được. Đúng một người ⇒ gắn thêm `employee_id` người đó (tổng = tiền của họ — xem docstring trên; chặn được cả trước khi luật chung có)."""
    paid = [line["employee_id"] for line in lines if line["item"] == item_id and line["amount"] > 0]
    total = {"item": item_id, "paid_count": len(paid), "paid_employee_ids": paid,
             "team_total": sum(line["amount"] for line in lines if line["item"] == item_id)}
    return {**total, "employee_id": paid[0]} if len(paid) == 1 else total
