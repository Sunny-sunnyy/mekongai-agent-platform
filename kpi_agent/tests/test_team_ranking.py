"""`backend/team_ranking.py` — ô xếp hạng lương kinh doanh do code xếp trong kết quả `get_team_bonus_table`.

Giữ sàn: người "nhiều nhất / ít nhất / thấp thứ hai" phải đúng (lượt thật trên Capitol 13/11: mô hình tự so bảng, nêu NV13 5.540.000 đ là thấp nhất
trong khi thấp nhất là NV08 5.240.000 đ; đo bên mình: "ai thấp thứ hai?" sai 3–4 / 9 lượt); và ô mới KHÔNG được làm lỏng rào kiểm số.
Bốn ca đầu dùng số tự đặt; ca cuối bật backend RAW 31/10 thật (kho tạm, không mở cổng). 0 lần gọi AI."""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kpi_agent.agent.number_check import check_numbers
from kpi_agent.agent.owner_check import check_owners, numbers_by_employee
from kpi_agent.agent.tool_specs import tool_schemas
from kpi_agent.backend import team_ranking

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx",
          ROOT / "kpi_agent/data/ma_che_ten.json", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
SALARIES = {"NV03": 7_000_000, "NV01": 9_000_000, "NV02": 5_000_000, "NV04": 9_000_000, "NV05": 7_000_000}


def _table(salaries: dict) -> dict:
    return {"employees": [{"employee_id": code, "business_salary": salary, "kpi_amounts": {"Turnover": salary}} for code, salary in salaries.items()],
            "team_total": sum(salaries.values()), "params_used": {"period": "2026-10"}}


def _people(codes: list[str], salary: int) -> list[dict]:
    return [{"employee_id": code, "business_salary": salary} for code in codes]


def test_ties_share_a_group_and_each_group_has_word_positions():
    ranking = team_ranking.add_ranking(_table(SALARIES))["ranking"]
    assert ranking["order"] == [
        {"position_from_top": "cao nhất", "position_from_bottom": "thấp thứ ba", "employees": _people(["NV01", "NV04"], 9_000_000)},
        {"position_from_top": "cao thứ hai", "position_from_bottom": "thấp thứ hai", "employees": _people(["NV03", "NV05"], 7_000_000)},
        {"position_from_top": "cao thứ ba", "position_from_bottom": "thấp nhất", "employees": _people(["NV02"], 5_000_000)}]
    assert ranking["highest"] == ranking["order"][0] and ranking["lowest"] == ranking["order"][-1]
    assert ranking["by"] == "business_salary" and ranking["all_equal"] is False


def test_everyone_equal_and_single_person_are_one_group():
    equal = team_ranking.add_ranking(_table({"NV01": 6_000_000, "NV02": 6_000_000}))["ranking"]
    assert equal["highest"] == equal["lowest"] and equal["highest"]["employees"] == _people(["NV01", "NV02"], 6_000_000) and equal["all_equal"] is True
    one = team_ranking.add_ranking(_table({"NV01": 6_000_000}))["ranking"]
    assert [group["employees"] for group in one["order"]] == [_people(["NV01"], 6_000_000)] and one["all_equal"] is True


def test_refusal_and_empty_table_get_no_ranking():
    refused = {"refused": "không có kỳ 2026-09", "params_used": {"period": "2026-09"}}
    assert team_ranking.add_ranking(dict(refused)) == refused
    assert "ranking" not in team_ranking.add_ranking({"employees": [], "team_total": 0})


def test_ranking_adds_no_number_to_the_guard_and_an_answer_read_from_it_passes_both_guards():
    table = _table({f"NV{i:02d}": 1_000_000 * i for i in range(1, 15)})        # 14 nhóm ⇒ nhãn tới "cao thứ mười bốn"
    result = team_ranking.add_ranking(table)
    # Ô xếp hạng KHÔNG thêm số nào vào nguồn số của rào: mọi số tiền nằm trong dòng có `employee_id` của chính người đó; nhãn thứ hạng viết bằng CHỮ
    # (bản đầu ghi tiền theo nhóm + một câu chuẩn ⇒ lương của người khác đặt vào dòng NV05 trong file Excel lọt rào)
    assert numbers_by_employee([result]) == numbers_by_employee([table])
    assert not any(char.isdigit() for group in result["ranking"]["order"] for char in group["position_from_top"] + group["position_from_bottom"])
    assert result["ranking"]["order"][-2]["position_from_bottom"] == "thấp thứ hai" and result["ranking"]["order"][13]["position_from_top"] == "cao thứ mười bốn"
    answer, question = "Nhiều nhất: NV14 — 14.000.000 đ; ít nhất: NV01 — 1.000.000 đ; thấp thứ hai là NV02 — 2.000.000 đ.", "Ai nhiều nhất, ít nhất, thấp thứ hai?"
    assert check_owners(answer, [result], question=question)["ok"] and check_numbers(answer, [result], question=question)["ok"]
    assert not check_numbers("NV03 đứng thứ 12 trong đội.", [result], question=question)["ok"]   # không có số thứ hạng rời làm nguồn
    description = next(tool["function"]["description"] for tool in tool_schemas(["Turnover"]) if tool["function"]["name"] == "get_team_bonus_table")
    assert "'ranking'" in description and "KHÔNG tự so các dòng" in description and "'position_from_bottom'" in description


@pytest.mark.skipif(not all(path.exists() for path in NEEDED), reason="không có dữ liệu thật trên máy này")
def test_real_backend_ranks_the_team_at_both_doors_and_changes_no_money(tmp_path):
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", "khoa-thu-trong-test-0123456789")   # khoá giả chỉ sống trong test
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
            page = client.get("/api/team-bonus").json()
            tool = client.post("/api/tool/get_team_bonus_table", json={"args": {}}).json()
            key = {"X-Kpi-Sys-Key": "khoa-thu-trong-test-0123456789"}
            answer = "Lương kinh doanh nhiều nhất: NV04, NV07, NV09 — 14.540.000 đ; ít nhất: NV14 — 6.340.000 đ; thấp thứ hai: NV13 — 7.740.000 đ."
            passed = client.post("/api/system/kiem-so", json={"answer": answer, "question": "Ai được nhiều nhất, ai ít nhất?"}, headers=key).json()
    salaries = {row["employee_id"]: row["business_salary"] for row in tool["employees"]}
    distinct = sorted(set(salaries.values()), reverse=True)                        # tính lại bằng phép khác (tập giá trị trên bảng) rồi so với ô code xếp
    for body in (page, tool):
        order = body["ranking"]["order"]
        assert [[row["employee_id"] for row in group["employees"]] for group in order] == \
            [sorted(code for code in salaries if salaries[code] == salary) for salary in distinct]
        assert all(row["business_salary"] == salaries[row["employee_id"]] for group in order for row in group["employees"])
        assert body["ranking"]["highest"] == order[0] and body["ranking"]["lowest"] == order[-1]
        assert not any(char.isdigit() for group in order for char in group["position_from_top"] + group["position_from_bottom"])
    # Mốc 7 (17/11, lõi theo trang sếp): nhiều nhất NV04 + NV07 + NV09 14.540.000; ít nhất NV14 6.340.000; thấp nhì NV13 7.740.000
    # (B ghép từ bảng lệch T10 C đã kiểm: +2.200.000 PC_BT cho 11 người — NV04 / NV09 có sẵn, NV14 trang cũng 0 — và +1.500.000 PC_ON NV08; tổng = 152.360.000).
    # Trước mốc 7 (đáp án bên chấm Capitol 13/11): cao nhất NV04 + NV09; thấp nhất NV08 5.240.000; thấp nhì NV13 5.540.000. Câu 1–3 kiểm cuối 8 câu đổi theo.
    order = tool["ranking"]["order"]
    assert order[0] == {"position_from_top": "cao nhất", "position_from_bottom": f"thấp thứ {team_ranking.ORDINALS[len(order) - 1]}",
                        "employees": _people(["NV04", "NV07", "NV09"], 14_540_000)}
    assert order[-1]["employees"] == _people(["NV14"], 6_340_000) and order[-1]["position_from_bottom"] == "thấp nhất"
    assert order[-2]["employees"] == _people(["NV13"], 7_740_000) and order[-2]["position_from_bottom"] == "thấp thứ hai"
    assert tool["team_total"] == 152_360_000 and passed["dat"] is True, passed["loi"]
