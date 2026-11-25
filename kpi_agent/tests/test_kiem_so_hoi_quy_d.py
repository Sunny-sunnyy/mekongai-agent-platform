"""Ca hồi quy kiem_so từ lượt soát 0 AI (``, 26 ca).
Vì sao giữ trong pytest: @10 từng làm hỏng ";" (câu tiền sai của NV06 dưới tiêu đề NV05 lọt) mà bộ test lúc đó không bắt — lỗi thật, D tìm ra.
Mỗi câu SAI tiền có câu ĐÚNG cùng hình. Dữ liệu: cùng fixture giả với `test_owner_check.py` (số tự đặt, mã che NVxx); ca ";" thứ ba đổi số
(bản D dùng hai số trùng tổng tiền thật theo mã che — số thật từng ô không commit). Ca đã ghi là GIỚI HẠN ở @12 (`bao_cao/04_TRO_LY_HOI_DAP.md`) ⇒ xfail kèm lý do."""
import pytest

from kpi_agent.agent.kiem_so import kiem_so

# ASO_Focus 13 / 14 · PC_ON 1 / 14 (NV03) · LPPC 14 / 14 · PC_BT 0 / 14 · Turnover 0 / 14
TEAM = {"employees": [{"employee_id": f"NV{i:02d}", "business_salary": 1000000 + i * 100000,
                       "kpi_amounts": {"ASO_Focus": 0 if i == 14 else 800000, "PC_ON": 500000 if i == 3 else 0, "LPPC": 2000000,
                                      "PC_BT": 0, "Turnover": 0},
                       "kpis_paid": 2 if i != 3 else 3} for i in range(1, 15)],
        "employee_count": 14, "kpi_count": 3, "team_total": 50000000}
TWO = {"employees": [{"employee_id": "NV05", "kpi_amounts": {"PC_BT": 3200000}}, {"employee_id": "NV06", "kpi_amounts": {"PC_BT": 4100000}}]}
PAIR = {"employees": [{"employee_id": "NV02", "kpi_amounts": {"PC_BT": 6300000}}, {"employee_id": "NV04", "kpi_amounts": {"PC_BT": 9900000}}]}

LIMIT_KPI_AFTER = pytest.mark.xfail(strict=True, reason="giới hạn @12 (lỗi nhỏ 4 của D): KPI đứng SAU câu phủ định không xét — không vá")
LIMIT_KPI_AFTER_TRUE = pytest.mark.xfail(strict=True, reason="giới hạn @12 (lỗi nhỏ 4, chiều ngược): KPI đứng SAU câu phủ định đọc thành "
                                        "'chưa ai có tiền PC_ON' ⇒ câu ĐÚNG bị chặn oan — không vá")
LIMIT_UNLESS = pytest.mark.xfail(strict=True, reason="giới hạn @12 (lỗi nhỏ 5 của D): 'trừ khi' bị coi là loại trừ ⇒ chặn oan — không vá")


@pytest.mark.parametrize("answer, source, money_wrong", [
    # lỗi lớn 1 — ";" (hồi quy do @10)
    ("NV05\n- PC_BT: 4.100.000 đ; NV06 cũng vậy.", TWO, True),
    ("NV05\n- PC_BT: 3.200.000 đ; NV06 thì khác.", TWO, False),
    ("NV02 được 6.300.000 đ; 9.900.000 đ là của NV04.", PAIR, False),
    # lỗi lớn 2 — cụm "không ai"
    ("PC_ON chưa có ai có tiền.", TEAM, True),
    ("PC_BT chưa có ai có tiền.", TEAM, False),
    ("ASO_Focus không một ai có tiền.", TEAM, True),
    ("Turnover không một ai có tiền.", TEAM, False),
    ("ASO_Focus chả ai có tiền.", TEAM, True),
    ("ASO_Focus không có nhân viên nào có tiền.", TEAM, True),
    ("PC_BT không có nhân viên nào có tiền.", TEAM, False),
    ("ASO_Focus không người nào có tiền.", TEAM, True),
    # lỗi lớn 3 — phủ định cả đội
    ("Không phải ai cũng có tiền LPPC.", TEAM, True),
    ("Không phải ai cũng có tiền ASO_Focus.", TEAM, False),
    ("Không phải tất cả đều có tiền LPPC.", TEAM, True),
    ("Không phải tất cả đều có tiền ASO_Focus.", TEAM, False),
    # câu cả đội "<danh từ> nào cũng" (@12)
    ("Không phải nhân viên nào cũng có tiền LPPC.", TEAM, True),
    ("Không phải nhân viên nào cũng có tiền ASO_Focus.", TEAM, False),
    ("Nhân viên nào cũng có tiền PC_BT.", TEAM, True),
    ("Nhân viên nào cũng có tiền LPPC.", TEAM, False),
    # lỗi nhỏ 4, 5 — giới hạn đã ghi
    pytest.param("Các KPI không thuộc nhóm chưa ai có tiền: PC_BT.", TEAM, True, marks=LIMIT_KPI_AFTER),
    pytest.param("Các KPI không thuộc nhóm chưa ai có tiền: PC_ON.", TEAM, False, marks=LIMIT_KPI_AFTER_TRUE),
    pytest.param("Chưa ai có tiền ở Turnover, trừ khi tính PC_BT.", TEAM, False, marks=LIMIT_UNLESS),
    # đối chứng cũ
    ("PC_ON chưa ai có tiền.", TEAM, True),
    ("PC_BT không có ai có tiền.", TEAM, False),
    ("NV05\n- PC_BT: 3.200.000 đ.", TWO, False),
    ("NV05 được 4.100.000 đ PC_BT.", TWO, True),
])
def test_d_review_cases(answer, source, money_wrong):
    result = kiem_so({"answer": answer, "question": "", "tool_results": [source]})
    assert result["dat"] is (not money_wrong), result["ly_do"]
