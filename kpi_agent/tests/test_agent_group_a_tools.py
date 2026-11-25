"""Nhóm A bậc mức tra cứu: A1 get_team_kpi_summary,
A2 % + mốc trong get_team_bonus_table, A3 compare_employees, A6 get_data_issues (+ mã NV trong cờ của `core/check_data.py`), mô tả `period`.

Đường gọi như `test_agent_new_tools.py`: `call_tool` của agent ⇒ backend thật qua TestClient (dữ liệu thật tháng 10, bảng lưu tạm) — chỉ bỏ cổng
mạng, không giả lập kết quả. Số mong đợi lấy từ CÔNG CỤ KHÁC đã có (bảng đội, get_employee_result) — so chéo hai đường, không so với chính nó."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kpi_agent.agent import backend_client, chat
from kpi_agent.agent.backend_client import call_tool, load_context
from kpi_agent.agent.owner_check import check_owners, team_kpi_counts

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
BASE = "http://127.0.0.1:8110"
REAL_CODE = re.compile(r"\b\d{2}[A-Z]{2}\.[A-Z]{2}\d{4,5}\b")   # cùng mẫu `workflow/runner.py` REAL_CODE_PATTERN


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("KPI_STORE", str(tmp_path_factory.mktemp("store") / "kpi_store.sqlite"))
    from kpi_agent.backend.main import app
    with TestClient(app, base_url=BASE) as client:
        def route(url, params=None, timeout=None, headers=None):   # headers: agent gửi kèm mã phiên (12/11) — bộ test tắt mã nên bỏ qua
            assert url.startswith(BASE)
            return client.get(url[len(BASE):], params=params or {})
        monkeypatch.setattr(backend_client.httpx, "get", route)
        yield client
    monkeypatch.undo()


@pytest.fixture(scope="module")
def ctx(backend):
    return load_context(BASE)


@pytest.fixture(scope="module")
def team(ctx):
    return call_tool(ctx, "get_team_bonus_table", {})


def test_a1_team_kpi_summary_matches_team_table(ctx, team):
    for kpi in ctx.kpis:
        summary = call_tool(ctx, "get_team_kpi_summary", {"kpi": kpi})
        amounts = {e["employee_id"]: e["kpi_amounts"][kpi] for e in team["employees"]}
        assert summary["team_amount_total"] == sum(amounts.values())
        assert summary["paid_employee_ids"] == sorted(code for code, amount in amounts.items() if amount > 0)
        assert summary["paid_count"] + summary["unpaid_count"] == summary["employee_count"] == len(amounts)
        # mỗi NV đúng một mốc; tổng tiền theo mốc cộng lại = tổng KPI; mốc khớp ô mốc của bảng đội (A2)
        assert sorted(code for tier in summary["by_tier"] for code in tier["employee_ids"]) == sorted(amounts)
        assert sum(tier["amount_total"] for tier in summary["by_tier"]) == summary["team_amount_total"]
        for tier in summary["by_tier"]:
            for code in tier["employee_ids"]:
                reached = next(e for e in team["employees"] if e["employee_id"] == code)["kpi_reached_tier"][kpi]
                assert tier["reached_tier"] == (reached or "chưa tới mốc thấp nhất")
    # số đã biết (KIEM_GIA_DINH_A_I_E.md, khớp C tính tay 04/11): ASO_Focus 9 người có tiền, 8.200.000 đ
    focus = call_tool(ctx, "get_team_kpi_summary", {"kpi": "aso_focus"})
    assert (focus["kpi"], focus["paid_count"], focus["team_amount_total"]) == ("ASO_Focus", 9, 8200000)


def test_a2_team_table_pct_and_tier_equal_employee_result(ctx, team):
    for row in team["employees"]:
        one = {k["kpi"]: k for k in call_tool(ctx, "get_employee_result", {"employee_id": row["employee_id"]})["kpis"]}
        assert row["kpi_pct"] == {kpi: one[kpi]["pct"] for kpi in ctx.kpis}
        assert row["kpi_reached_tier"] == {kpi: one[kpi]["reached_tier"] for kpi in ctx.kpis}
        assert row["kpi_amounts"] == {kpi: one[kpi]["amount"] for kpi in ctx.kpis}   # ô cũ giữ nguyên
    assert team["team_total"] == 55700000


def test_a3_compare_employees_matches_employee_result(ctx):
    both = call_tool(ctx, "compare_employees", {"employee_a": "NV13", "employee_b": "nv11"})
    a, b = (call_tool(ctx, "get_employee_result", {"employee_id": code}) for code in ("NV13", "NV11"))
    assert [side["employee_id"] for side in both["sides"]] == ["NV13", "NV11"]
    assert [side["business_salary"] for side in both["sides"]] == [a["business_salary"], b["business_salary"]]
    diff = both["business_salary_difference"]
    assert diff["a_minus_b"] == a["business_salary"] - b["business_salary"] and diff["gap"] == abs(diff["a_minus_b"])
    assert diff["higher"] == ("NV13" if diff["a_minus_b"] > 0 else "NV11" if diff["a_minus_b"] < 0 else "bằng nhau")
    for side, result in zip(both["sides"], (a, b)):
        assert [(k["kpi"], k["pct"], k["actual"], k["reached_tier"], k["amount"]) for k in side["kpis"]] == \
               [(k["kpi"], k["pct"], k["actual"], k["reached_tier"], k["amount"]) for k in result["kpis"]]
    one_kpi = call_tool(ctx, "compare_employees", {"employee_a": "NV13", "employee_b": "NV11", "kpi": "ASO_Focus"})
    assert [k["kpi"] for k in one_kpi["sides"][0]["kpis"]] == ["ASO_Focus"] and len(one_kpi["kpi_amount_differences"]) == 1


@pytest.mark.parametrize("args, said", [
    ({"employee_a": "NV13", "employee_b": "NV13"}, "giống nhau"),
    ({"employee_a": "../data/ma_che_ten.json", "employee_b": "NV11"}, "không hợp lệ"),
    ({"employee_a": "NV13", "employee_b": "NV99"}, "không có NV99"),
    ({"employee_a": "NV13"}, "cần employee_a và employee_b"),
    ({"employee_a": "NV13", "employee_b": "NV11", "kpi": "../PC_BT"}, "không có"),
    ({"employee_a": "NV13", "employee_b": "NV11", "period": "2026-11"}, "chưa có dữ liệu kỳ"),
])
def test_a3_refusals_carry_no_numbers_and_no_raw_strings(ctx, args, said):
    result = call_tool(ctx, "compare_employees", args)
    assert said in result["refused"] and "sides" not in result
    assert "ma_che_ten" not in json.dumps(result, ensure_ascii=False)   # Q1: chữ lạ (có thể là mã thật) không được nói lại


def test_a6_data_issues_all_levels_masked_and_filtered(backend, ctx):
    everyone = call_tool(ctx, "get_data_issues", {})
    raw = backend.app.state.ctx.workflow_result.outputs["check"]["issues"]   # khối Kiểm dữ liệu của lượt đang chạy
    assert [(i["level"], i["employee_ids"] != []) for i in everyone["issues"]] == [(i["level"], i["employees"] != []) for i in raw]
    assert {i["level"] for i in everyone["issues"]} >= {"info"}          # 09/11: trả cả mức thông tin ("lệch MCP")
    assert not REAL_CODE.search(json.dumps(everyone, ensure_ascii=False))
    assert "không có xu hướng" in everyone["note"]
    for code in ctx.employee_ids:
        mine = call_tool(ctx, "get_data_issues", {"employee_id": code})
        assert all(i["scope"] == "ca_doi" or code in i["employee_ids"] for i in mine["issues"])
        assert sum(i["scope"] == "nhan_vien" for i in mine["issues"]) == sum(code in i["employee_ids"] for i in everyone["issues"])
    assert "không có NV99" in call_tool(ctx, "get_data_issues", {"employee_id": "NV99"})["refused"]


def test_v3_person_flags_reach_model_only_through_data_issues(backend, ctx):
    # mức tra cứu vòng 3 ( hướng (a) 09/11): cờ của riêng một người KHÔNG vào data_flags của mọi kết quả công cụ (K3 vòng 2: gán nhầm cho
    # người được hỏi); trang (/api/meta data_flags) vẫn đủ cờ; get_data_issues vẫn trả cờ đó cho đúng người.
    page = backend.get("/api/meta").json()["data_flags"]
    issues = call_tool(ctx, "get_data_issues", {})["issues"]
    person = [i for i in issues if i["scope"] == "nhan_vien" and i["message"] in page]
    assert person, "dữ liệu tháng 10 có cờ ASO_ALL ngoài MCP của một người — mất cờ này là dữ liệu đổi, xem lại test"
    expected = [f for f in page if f not in {i["message"] for i in person}]
    calls = [("get_employee_result", {"employee_id": code}) for code in ctx.employee_ids]
    calls += [("get_team_bonus_table", {}), ("get_team_kpi_summary", {"kpi": "ASO_ALL"}), ("get_data_provenance", {})]
    for name, args in calls:
        assert call_tool(ctx, name, args)["data_flags"] == expected, (name, args)
    for issue in person:
        for code in issue["employee_ids"]:
            mine = call_tool(ctx, "get_data_issues", {"employee_id": code})
            assert issue["message"] in [i["message"] for i in mine["issues"]]
            assert issue["message"] not in mine["data_flags"]


def test_a_ii_1_zero_in_unused_field_is_dropped(ctx):
    # Đúng tham số mô hình gửi ở T3 câu 12 lượt 20261109_160950 (bị từ chối trước bản sửa): extra 200 + target_pct 0
    them = call_tool(ctx, "simulate_kpi_change", {"employee_id": "NV07", "kpi": "PC_BT", "extra": 200, "target_pct": 0, "period": "2026-10"})
    assert them["mode"] == "them" and them["scenario"]["amount"] == 2_200_000 and "target_pct" not in them["params_used"]
    dat = call_tool(ctx, "simulate_kpi_change", {"employee_id": "NV01", "kpi": "Turnover", "extra": 0, "target_pct": 95})
    assert dat["mode"] == "dat_phan_tram" and dat["scenario"]["amount"] == 3_920_000
    for both in ({"extra": 5, "target_pct": 95}, {"extra": 0, "target_pct": 0}):   # hai số khác 0 / hai số 0 ⇒ vẫn từ chối
        assert "đúng một trong hai" in call_tool(ctx, "simulate_kpi_change", {"employee_id": "NV01", "kpi": "Turnover", **both})["refused"]


def test_period_description_and_params_match_schemas():
    schemas = chat.tool_schemas(["PC_BT"])
    for tool in schemas:
        props = tool["function"]["parameters"]["properties"]
        assert set(props) == backend_client.ALL_TOOL_PARAMS[tool["function"]["name"]]
        if "period" in props:
            assert props["period"]["description"] == "kỳ YYYY-MM: điền kỳ người hỏi nói; bỏ trống nếu không nói; số luôn theo kỳ đang có"


def test_people_count_rule_reads_a1_summary(ctx):
    summary = call_tool(ctx, "get_team_kpi_summary", {"kpi": "ASO_Focus"})
    assert team_kpi_counts([summary]) == {"team": 14, "paid": {"ASO_Focus": 9}}
    assert check_owners("9 người có tiền ASO_Focus.", [summary])["ok"]
    assert not check_owners("10 người có tiền ASO_Focus.", [summary])["ok"]
    # KPI có tên trong lượt (explain_kpi PC_ON) nhưng không có số đếm (A1 chỉ của ASO_Focus) ⇒ chưa đối chiếu được, KHÔNG coi là 0 người
    explain = call_tool(ctx, "explain_kpi", {"employee_id": "NV04", "kpi": "PC_ON"})
    problems = check_owners("1 người có tiền PC_ON.", [summary, explain])["problems"]
    assert problems and "chưa đối chiếu được" in problems[0]
    # KPI không có tên trong kết quả lượt ⇒ luật không xét (như trước 09/11)
    assert check_owners("1 người có tiền PC_ON.", [summary])["ok"]


def test_3a_p5_each_data_issue_has_a_code_written_sentence_with_exactly_its_people(ctx):
    # Bậc 3a P5: mỗi lưu ý có ô `sentence` do code viết = nguyên lời lưu ý + ĐÚNG những người nó gắn (không hơn, không kém) — mô hình chép nguyên,
    # không tự ghép mã của hai lưu ý vào một câu (ca Capitol 18–19/11: gán lưu ý cho người không liên quan)
    import re
    issues = call_tool(ctx, "get_data_issues", {})["issues"]
    assert issues and all(isinstance(issue.get("sentence"), str) for issue in issues)
    for issue in issues:
        body, _, tail = issue["sentence"].rpartition(" — ")
        assert body == issue["message"].rstrip(". ")
        if issue["employee_ids"]:
            assert tail == f"liên quan {', '.join(issue['employee_ids'])}."
            assert set(re.findall(r"NV\d{2}", tail)) == set(issue["employee_ids"])
        else:
            assert tail == "lưu ý chung của cả đội."
    assert any(issue["employee_ids"] for issue in issues) and any(not issue["employee_ids"] for issue in issues)


def test_issue_sentence_is_added_at_the_exit_not_in_queries(backend, ctx):
    # Người kiểm độc lập 19/11 (đọc diff P5): ô `sentence` từng gắn trong backend/queries.py — file thuộc dấu vân tay bản tính ⇒ sửa chữ câu chuẩn là mất
    # "Đã soát" dù không số nào đổi. Gắn ở cửa ra như `team_sentences`: đường cho mô hình (/api/tool, call_tool) có ô; truy vấn gốc thì không.
    assert all("sentence" not in issue for issue in backend.get("/api/data-issues").json()["issues"])
    via_tool = backend.post("/api/tool/get_data_issues", json={"args": {}}).json()["issues"]
    assert via_tool and [issue["sentence"] for issue in via_tool] == [issue["sentence"] for issue in call_tool(ctx, "get_data_issues", {})["issues"]]
    refused = call_tool(ctx, "get_data_issues", {"employee_id": "NV99"})
    assert "refused" in refused and "issues" not in refused
