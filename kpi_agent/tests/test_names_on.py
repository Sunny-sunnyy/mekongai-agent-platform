"""Bậc "tên vào trợ lý" trên BACKEND THẬT khi `KPI_NAMES=on` (TestClient, workflow RAW 31/10, dữ liệu thật trên máy — tên đọc lúc chạy từ
`/api/names`, file test không chứa tên / mã thật nào). 0 lần gọi AI. File riêng (không chung `test_names.py`) vì hai TestClient cùng một app
sẽ ghi đè trạng thái cờ của nhau.

Giữ sàn: gọi công cụ theo tên / mã SM cho ĐÚNG số của lần gọi theo mã che; tên không rõ ⇒ từ chối; câu tiền theo tên sai người ⇒ rào chặn;
file xuất khi cờ bật có tên và không còn bị 409. Cờ off: `test_names.py`, `test_capitol_api.py`, `test_capitol_export_extra.py` (không đổi).
"""
import hashlib
import io
import json
from datetime import date
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json",
          ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"   # khoá giả chỉ sống trong test
KEY = {"X-Kpi-Sys-Key": SYS_KEY}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(tmp_path_factory.mktemp("store") / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setenv("KPI_NAMES", "on")
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


@pytest.fixture(scope="module")
def people(client) -> dict:
    return client.get("/api/names").json()["names"]


def _tool(client, name: str, **args) -> dict:
    return client.post(f"/api/tool/{name}", json={"args": args}).json()


def _money(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def test_tool_by_full_name_and_sm_code_equals_call_by_masked_code(client, people):
    for masked, entry in people.items():
        by_code = _tool(client, "get_employee_result", employee_id=masked)
        assert by_code["employee_id"] == masked and by_code["name"] == entry["name"] and by_code["sm_code"] == entry["sm_code"]
        for typed in (entry["name"], entry["name"].upper(), entry["sm_code"]):
            assert _tool(client, "get_employee_result", employee_id=typed) == by_code   # cùng người, cùng mọi số


def test_last_two_words_work_only_when_unique(client, people):
    from kpi_agent.agent import names
    table = names.alias_table(people)
    for masked, entry in people.items():
        last_two = " ".join(entry["name"].split()[-2:])
        result = _tool(client, "get_employee_result", employee_id=last_two)
        if table[names.normalize(last_two)] == masked:
            assert result["employee_id"] == masked
        else:   # 2 chữ cuối trùng với người khác trong đội ⇒ từ chối, không đoán
            assert result["refused"] == names.REFUSED_UNCLEAR


def test_unclear_name_is_refused_without_echo(client, people):
    from kpi_agent.agent import names
    one_word = next(iter(people.values()))["name"].split()[-1]
    for typed in (one_word, "Người Không Có Trong Đội"):
        result = _tool(client, "get_employee_result", employee_id=typed)
        assert result["refused"] == names.REFUSED_UNCLEAR and typed not in str(result)
    compare = _tool(client, "compare_employees", employee_a="NV01", employee_b=one_word)
    assert compare["refused"] == names.REFUSED_UNCLEAR


def test_tool_list_tells_model_names_are_accepted(client):
    tools = client.get("/api/tools").json()["tools"]
    employee = next(tool for tool in tools if tool["name"] == "get_employee_result")["parameters"]["properties"]["employee_id"]
    assert "họ tên" in employee["description"]


def test_kiem_so_blocks_money_of_another_person_said_by_name(client, people):
    team = _tool(client, "get_team_bonus_table")["employees"]
    salary = {row["employee_id"]: row["business_salary"] for row in team}
    # hai người có lương kinh doanh khác nhau, và số của người này không phải ô tiền nào của người kia (chọn bằng code — bài học 11/11)
    owned = lambda row: {value for value in [row["business_salary"], *row["kpi_amounts"].values()]}
    good, other = next((a["employee_id"], b["employee_id"]) for a in team for b in team
                       if a["employee_id"] != b["employee_id"] and b["business_salary"] not in owned(a) and b["business_salary"] > 0)
    calls = [{"name": "get_team_bonus_table", "args": {}}]
    def check(answer):
        return client.post("/api/system/kiem-so", json={"answer": answer, "question": "lương kinh doanh?", "tool_calls": calls}, headers=KEY).json()
    right = check(f"{people[good]['name']} có lương kinh doanh {_money(salary[good])} đồng.")
    assert right["dat"] is True, right
    # cách hiển thị "Tên · mã SM · NVxx": mã SM (có chữ số) không bị coi là số không nguồn / số tiền của người đó
    shown = check(f"{people[good]['name']} · {people[good]['sm_code']} · {good} có lương kinh doanh {_money(salary[good])} đồng.")
    assert shown["dat"] is True, shown
    for who in (people[good]["name"], people[good]["sm_code"]):
        wrong = check(f"{who} có lương kinh doanh {_money(salary[other])} đồng.")   # tiền của người khác, nói theo tên / mã SM
        assert wrong["dat"] is False, wrong


def test_system_export_has_names_and_is_not_refused(client, people):
    table = client.get("/api/system/bonus-table", headers=KEY).json()
    body = {"run_id": table["run_id"], "sha256_bang": table["sha256"], "org_id": "org-thu", "nguoi_duyet": "giám sát thử"}
    assert client.post("/api/system/duyet", json=body, headers=KEY).status_code == 200
    response = client.get("/api/export/xlsx", headers=KEY)
    assert response.status_code == 200                                # cờ off: file có tên thật ⇒ 409; cờ on: có tên là đúng ý
    rows = [[cell.value for cell in row] for row in openpyxl.load_workbook(io.BytesIO(response.content))["Tổng đội"].iter_rows(min_row=5)]
    assert rows[0][:3] == ["Mã NV", "Tên", "Mã SM"]
    assert {row[0]: (row[1], row[2]) for row in rows[1:15]} == {code: (entry["name"], entry["sm_code"]) for code, entry in people.items()}
    # mã băm bảng thưởng không đổi theo cờ: tên chỉ thêm ở bước trình bày file
    assert client.get("/api/system/bonus-table", headers=KEY).json()["sha256"] == table["sha256"]
    assert "Mã SM" in client.get("/api/export/md", headers=KEY).text


def test_bonus_table_carries_names_outside_the_hash(client, people):
    # Hộp bật KPI_SYSTEM_ONLY không có /api/names ⇒ khối kiểm ô bên Capitol lấy bảng tên ở đây để so từng ô Tên / Mã SM của file xuất
    body = client.get("/api/system/bonus-table", headers=KEY).json()
    assert body["names"] == people and sorted(body["names"]) == [f"NV{i:02d}" for i in range(1, 15)]
    assert "names" not in body["table"]
    # mã băm tính CHỈ trên `table` (như lúc cờ off — `test_names.py`) ⇒ thêm `names` không đổi mã băm bảng
    canonical = json.dumps(body["table"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == body["sha256"]


def test_kiem_so_blocks_money_of_a_said_by_name_of_b_when_turn_only_called_tool_for_a(client, people):
    # BLOCKER 12/11: lượt chỉ gọi get_employee_result cho A ⇒ kết quả không có tên B; câu trả lời gán tiền A cho B bằng họ tên / mã SM /
    # 2 chữ cuối từng qua (bảng tên suy từ kết quả lượt). Nay bảng tên đủ đội ⇒ chặn — ở CẢ kiem-so có tool_calls lẫn có run_id.
    from kpi_agent.agent import names
    team = _tool(client, "get_team_bonus_table")["employees"]
    owned = lambda row: {row["business_salary"], *row["kpi_amounts"].values()}
    a, b = next((x["employee_id"], y["employee_id"]) for x in team for y in team
                if x["employee_id"] != y["employee_id"] and x["business_salary"] > 0 and x["business_salary"] not in owned(y))
    salary = next(row["business_salary"] for row in team if row["employee_id"] == a)
    run = {"X-Kpi-Run-Id": "luot-ten-1", "X-Kpi-Org-Id": "org-thu"}
    assert client.post("/api/tool/get_employee_result", json={"args": {"employee_id": a}}, headers=run).json()["employee_id"] == a
    sources = ({"tool_calls": [{"name": "get_employee_result", "args": {"employee_id": a}}]}, {"run_id": "luot-ten-1", "org_id": "org-thu"})
    last_two = " ".join(people[b]["name"].split()[-2:])
    ways = [people[b]["name"], people[b]["sm_code"], b] + ([last_two] if names.alias_table(people)[names.normalize(last_two)] == b else [])
    for source in sources:
        check = lambda who: client.post("/api/system/kiem-so", headers=KEY, json={
            "answer": f"{who} được tổng lương kinh doanh {_money(salary)} đồng.", "question": "lương kinh doanh?", **source}).json()
        assert check(people[a]["name"])["dat"] is True, source          # đúng người (nói theo tên) vẫn qua
        for who in ways:
            result = check(who)
            assert result["dat"] is False and any(item["loai"] == "so_sai_nguoi" for item in result["loi"]), (source, result["loi"])
