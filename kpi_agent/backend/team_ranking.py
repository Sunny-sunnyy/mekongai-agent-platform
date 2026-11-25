"""Ô XẾP HẠNG lương kinh doanh cả đội, do CODE xếp, gắn vào kết quả `get_team_bonus_table` ở CỬA RA của backend.

Vì sao có file này: câu "ai được nhiều nhất, ai ít nhất?" — mô hình gọi đúng bảng cả đội MỘT lần rồi tự so 14 dòng và ĐỌC SAI (lượt thật trên Capitol
13/11: trả "ít nhất NV13 5.540.000 đ" trong khi thấp nhất là NV08 5.240.000 đ; 5.540.000 là tiền thật của NV13 nên rào "chủ của số" cho qua).
So sánh 14 con số là việc của code, không phải của mô hình. Ở đây chỉ SẮP XẾP số lõi đã tính (ô `business_salary` của từng dòng), không tính gì mới.
Vì sao KHÔNG sửa trong `queries.py`: file đó nằm trong dấu vân tay bản tính — sửa là mất dấu "Đã soát" dù không số nào đổi (như `gap_status.py`).
Giới hạn: rào kiểm số CHƯA so "người được gọi là nhiều / ít nhất" với ô này — câu trả lời tự so bảng sai vẫn có thể qua; bậc này dựa vào lời dặn."""

# Thứ hạng viết bằng CHỮ (không chữ số): mọi số trong kết quả công cụ — kể cả số nằm trong chữ — đều thành "số có nguồn" của rào kiểm số
ORDINALS = ("nhất", "hai", "ba", "tư", "năm", "sáu", "bảy", "tám", "chín", "mười", "mười một", "mười hai", "mười ba", "mười bốn", "mười lăm",
            "mười sáu", "mười bảy", "mười tám", "mười chín", "hai mươi")


def _position(word: str, index: int) -> str:
    """"cao nhất" / "cao thứ hai" … — quá 20 nhóm thì không ghi nhãn (đội hiện có 14 người)."""
    if index >= len(ORDINALS):
        return ""
    return f"{word} nhất" if index == 0 else f"{word} thứ {ORDINALS[index]}"


def add_ranking(result: dict) -> dict:
    """Thêm ô `ranking` vào bảng thưởng cả đội. Lời từ chối / bảng rỗng (không có dòng `employees`) trả nguyên.

    `order` = danh sách NHÓM từ cao xuống thấp; mỗi nhóm = {position_from_top, position_from_bottom, employees: [{employee_id, business_salary}, …]}
    — người bằng tiền nhau chung một nhóm, chung một nhãn. `highest` / `lowest` = nhóm đầu / nhóm cuối; `all_equal` = cả đội bằng tiền nhau.
    Nhãn thứ hạng do code ghi (13/11, đo qua mô hình: có ô xếp sẵn mà chưa có nhãn thì câu "ai thấp thứ hai?" vẫn sai 4 / 9 lượt — mô hình tự đếm ngược
    danh sách và lấy nhầm nhóm cuối).
    Bốn điều cố ý về hình (rào kiểm số đọc MỌI số trong kết quả công cụ, kể cả số nằm trong chữ):
    - mọi số tiền nằm trong dòng có `employee_id` của CHÍNH người đó. Bản đầu ghi {business_salary, employee_ids: [...]} cho cả nhóm ⇒ rào coi số đó
      là số CẤP ĐỘI ⇒ lương của người khác đặt vào dòng NV05 trong file Excel lọt (test `test_excel_other_persons_number_in_row_is_blocked` bắt);
    - KHÔNG có câu chuẩn bằng chữ mang số tiền trong ô này — cùng lỗ, cùng test bắt;
    - nhãn thứ hạng viết bằng CHỮ ("cao thứ hai"), không ghi số thứ hạng rời (1…14): thêm số là thêm "số có nguồn" không cần thiết;
    - nhóm không có khoá `employee_id` (chỉ các dòng người bên trong có)."""
    rows = result.get("employees")
    if not rows:
        return result
    by_salary: dict[int, list[dict]] = {}
    for row in sorted(rows, key=lambda row: row["employee_id"]):
        by_salary.setdefault(row["business_salary"], []).append({"employee_id": row["employee_id"], "business_salary": row["business_salary"]})
    groups = [group for _salary, group in sorted(by_salary.items(), reverse=True)]
    order = [{"position_from_top": _position("cao", index), "position_from_bottom": _position("thấp", len(groups) - 1 - index), "employees": group}
             for index, group in enumerate(groups)]
    return {**result, "ranking": {"by": "business_salary", "highest": order[0], "lowest": order[-1], "order": order, "all_equal": len(order) == 1}}
