"""Khối agent có công cụ (09/11 —) — phần đo 0 USD, không gọi mô hình.

T1: bộ kiểm file từ chối các nối sai (dây công cụ / dữ liệu lẫn cổng, công cụ lạ, agent không công cụ, dây ra từ khối AI, 2 dây một cổng dữ liệu),
    workflow tính thưởng cũ vẫn hợp lệ, mã băm logic không đổi.
T2: gọi công cụ THẲNG (khối) = gọi qua backend HTTP thật (đường agent đang dùng) — cùng tham số, kể cả tham số sai; dữ liệu thật tháng 10.
T4 (phần 0 USD): lời dặn + mọi kết quả công cụ của khối không có mã NV thật.
Đường HTTP: `httpx.get` của agent CHUYỂN sang TestClient của backend thật (như test_agent_new_tools.py) — chỉ bỏ cổng mạng, code backend chạy thật."""
import copy
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kpi_agent.agent import backend_client, chat
from kpi_agent.agent.backend_client import call_tool, load_context
from kpi_agent.workflow import agent_block, runner
from kpi_agent.workflow.blocks import BLOCK_TOOLS

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
BASE = "http://127.0.0.1:8110"
CUTOFF = date(2026, 10, 20)   # như backend (main.py KPI_CUTOFF mặc định)
QA_FILE = ROOT / "kpi_agent/workflows/kpi_hoi_dap.yaml"
BONUS_FILE = ROOT / "kpi_agent/workflows/kpi_thuong_nvbh.yaml"
needs_data = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")


def _qa() -> dict:
    return copy.deepcopy(runner.load_workflow(QA_FILE)[0])


def _problems(workflow: dict) -> str:
    return " | ".join(runner.check_workflow(workflow))


# ---------- T1: bộ kiểm file ----------
def test_qa_workflow_valid_and_bonus_workflow_logic_unchanged():
    assert runner.check_workflow(_qa()) == []
    bonus = runner.load_workflow(BONUS_FILE)[0]
    assert runner.check_workflow(bonus) == []
    # mã băm logic của file tính thưởng: khối agent KHÔNG đổi file này (09/11 16:12: aa67f7dc62176842). Đổi CÓ CHỦ ĐÍCH lúc gộp 10/11: E thêm
    # duyet (người duyệt) + xuat (xuất file) sau compute_bonus ⇒ d62002492c7724e7 (09:31 10/11, mục 6)
    assert runner.workflow_sha256(bonus)[:16] == "d62002492c7724e7"


def test_sample_workflow_wires_every_block_tool():
    # 10/11: bản 09/11 thiếu 3 công cụ mức tra cứu ⇒ lượt T5 qua khối có 6 công cụ, chat 9 (so không cùng điều kiện) — chặn bằng code, không trông vào mắt
    workflow = _qa()
    wired = [node["params"]["tool"] for node in workflow["nodes"] if node["type"] == "kpi_tool"]
    assert sorted(wired) == sorted(BLOCK_TOOLS)


@needs_data
def test_block_tool_fingerprint_equals_chat_only_when_every_tool_wired(block_side):
    agent_ctx, _ = block_side
    chat_sha = chat.config_version(agent_ctx, "medium")["tool_schemas_sha256"]
    assert chat.config_version(agent_ctx, "medium", tools=list(BLOCK_TOOLS))["tool_schemas_sha256"] != chat_sha   # khối không có get_data_provenance
    all_but_provenance = [name for name in backend_client.TOOL_PARAMS if name != "get_data_provenance"]
    assert chat.config_version(agent_ctx, "medium", tools=all_but_provenance[:6])["tool_schemas_sha256"] != chat.config_version(
        agent_ctx, "medium", tools=all_but_provenance)["tool_schemas_sha256"]   # 6 ↔ 9 công cụ phải khác mã (lỗi 09/11 ghi cùng mã)


def test_tool_edge_into_data_port_rejected():
    workflow = _qa()
    edge = next(e for e in workflow["edges"] if e["to_node"] == "agent" and e["to_port"] == "rules")
    edge.update(from_node="t_team", from_port="tool")
    assert "chỉ cắm vào cổng công cụ" in _problems(workflow)


def test_data_edge_into_tools_port_rejected():
    workflow = _qa()
    workflow["edges"].append({"from_node": "kpi", "from_port": "results", "to_node": "agent", "to_port": "tools"})
    assert "chỉ nhận dây từ khối kpi_tool" in _problems(workflow)


@pytest.mark.parametrize("name", ["delete_everything", "get_data_provenance", "GET_TEAM_BONUS_TABLE"])
def test_unknown_or_unsupported_tool_rejected(name):
    workflow = _qa()
    workflow["nodes"][6]["params"] = {"tool": name}
    assert "ô tool phải là một trong" in _problems(workflow)


def test_agent_without_tools_rejected():
    workflow = _qa()
    workflow["nodes"] = [n for n in workflow["nodes"] if n["type"] != "kpi_tool"]
    workflow["edges"] = [e for e in workflow["edges"] if e["to_port"] != "tools"]
    assert "cổng vào bắt buộc 'tools' chưa có dây" in _problems(workflow)


def test_edge_out_of_ai_block_rejected():
    # #4: chữ AI không được vào khối tính tiền — thêm một khối tính SAU agent, nối agent.answer ⇒ results
    workflow = _qa()
    workflow["nodes"].append({"id": "bonus2", "type": "compute_bonus"})
    workflow["edges"] += [{"from_node": "agent", "from_port": "answer", "to_node": "bonus2", "to_port": "results"},
                          {"from_node": "rules", "from_port": "rules", "to_node": "bonus2", "to_port": "rules"},
                          {"from_node": "targets", "from_port": "targets", "to_node": "bonus2", "to_port": "targets"}]
    assert "khối 'agent' gọi mô hình — chưa được nối ra khối khác" in _problems(workflow)


def test_two_edges_into_one_data_port_still_rejected():
    workflow = _qa()
    workflow["edges"].append({"from_node": "rules", "from_port": "rules", "to_node": "agent", "to_port": "rules"})
    assert "cổng vào 'rules' có 2 dây" in _problems(workflow)


@pytest.mark.parametrize("params, words", [({"max_tool_calls": 6}, "ô max_tool_calls phải là số nguyên 1–5"),
                                           ({"max_tool_calls": True}, "ô max_tool_calls phải là số nguyên 1–5"),
                                           ({"reasoning": "high"}, "ô reasoning phải là low hoặc medium")])
def test_agent_params_checked(params, words):
    workflow = _qa()
    workflow["nodes"][-1]["params"] = params
    assert words in _problems(workflow)


@pytest.mark.parametrize("run, words", [({"cutoff": CUTOFF}, "thiếu tham số lượt: question"),
                                        ({"cutoff": CUTOFF, "question": "   "}, "question là chữ rỗng"),
                                        ({"cutoff": CUTOFF, "question": 5}, "question phải là str")])
def test_question_is_a_required_text_run_param(run, words):
    assert words in " | ".join(runner.check_run_params(_qa(), run))


def test_bonus_workflow_does_not_need_question():
    assert runner.check_run_params(runner.load_workflow(BONUS_FILE)[0], {"cutoff": CUTOFF}) == []


def test_agent_code_in_fingerprint_only_when_workflow_has_ai_block():
    # Sửa agent không được đổi dấu vân tay bản tính thưởng (mất "Đã soát") — chỉ lượt có khối gọi mô hình mới băm code agent
    assert runner.AGENT_BLOCK_FILE not in runner.CODE_FILES
    assert runner.AGENT_BLOCK_FILE in runner.AI_CODE_FILES
    qa = runner.input_fingerprint(_qa(), {"cutoff": CUTOFF, "question": "x"})
    bonus = runner.input_fingerprint(runner.load_workflow(BONUS_FILE)[0], {"cutoff": CUTOFF})
    assert qa["code_sha256"] != bonus["code_sha256"]


# ---------- T2 + T4: gọi thẳng = gọi backend HTTP ----------
@pytest.fixture(scope="module")
def block_side():
    """Ngữ cảnh của khối dựng từ CỔNG RA của một lượt workflow thật (6 khối tính) — đúng đường khối agent nhận."""
    workflow = runner.load_workflow(BONUS_FILE)[0]
    result = runner.run_workflow(workflow, {"cutoff": CUTOFF})
    # Gộp 10/11: file tính thưởng có cổng người duyệt ⇒ chưa soát thì dừng ở cổng ("awaiting_review"); cổng ra 6 khối tính vẫn đủ (khối agent chỉ đọc chúng)
    assert result.status in ("ok", "awaiting_review"), result.errors
    inputs = {port: runner.output_of(workflow, result, block_type, port) for block_type, port in
              (("load_rules", "rules"), ("read_targets", "targets"), ("read_tracking", "tracking"), ("check_data", "issues"),
               ("compute_kpi", "results"), ("compute_bonus", "bonuses"))}
    kpi_ctx, agent_ctx = agent_block.build_contexts(inputs, CUTOFF)
    return agent_ctx, agent_block.local_tool_caller(kpi_ctx, list(BLOCK_TOOLS))


@pytest.fixture
def http_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))
    from kpi_agent.backend.main import app
    with TestClient(app, base_url=BASE) as client:
        def route(url, params=None, timeout=None, headers=None):   # headers: agent gửi kèm mã phiên (12/11) — bộ test tắt mã nên bỏ qua
            assert url.startswith(BASE)
            return client.get(url[len(BASE):], params=params or {})
        monkeypatch.setattr(backend_client.httpx, "get", route)
        yield load_context(BASE)


CALLS = [("get_team_bonus_table", {}), ("get_team_bonus_table", {"period": "2026-09"}), ("get_team_bonus_table", {"period": "2026-10"}),
         ("get_employee_result", {"employee_id": "NV05"}), ("get_employee_result", {"employee_id": "NV14"}),
         ("get_employee_result", {"employee_id": "nv05 "}), ("get_employee_result", {"employee_id": "NV99"}),
         ("get_employee_result", {"employee_id": "NV01/../NV02"}), ("get_employee_result", {"employee_id": ""}),
         ("get_employee_result", {"employee_id": "NV05", "foo": 1}),
         ("get_mid_month_gap", {}), ("get_mid_month_gap", {"employee_id": "NV05"}), ("get_mid_month_gap", {"employee_id": "NV77"}),
         ("explain_kpi", {"employee_id": "NV05", "kpi": "ASO_ALL"}), ("explain_kpi", {"employee_id": "NV14", "kpi": "pc_bt"}),
         ("explain_kpi", {"employee_id": "NV05", "kpi": "XYZ"}), ("explain_kpi", {"employee_id": "NV05", "kpi": "../PC_BT"}),
         ("get_opportunities", {}), ("get_opportunities", {"period": "2026-11"}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "extra": 10}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "Turnover", "extra": "50000000"}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "extra": "abc"}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "extra": -5}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL"}),
         # Nhóm A bậc mức tra cứu (A1, A3 — gộp e-a-i-v3 10/11): đúng + thiếu / sai tham số
         ("get_team_kpi_summary", {"kpi": "ASO_ALL"}), ("get_team_kpi_summary", {"kpi": "turnover"}), ("get_team_kpi_summary", {}),
         ("get_team_kpi_summary", {"kpi": "XYZ"}), ("get_team_kpi_summary", {"kpi": "ASO_ALL", "period": "2026-09"}),
         ("compare_employees", {"employee_a": "NV05", "employee_b": "NV14"}),
         ("compare_employees", {"employee_a": "NV05", "employee_b": "NV14", "kpi": "PC_BT"}),
         ("compare_employees", {"employee_a": "NV05", "employee_b": "NV05"}), ("compare_employees", {"employee_a": "NV05"}),
         ("compare_employees", {"employee_a": "NV05", "employee_b": "NV99"}), ("compare_employees", {"employee_a": "../NV01", "employee_b": "NV02"}),
         ("get_data_issues", {}), ("get_data_issues", {"employee_id": "NV05"}), ("get_data_issues", {"employee_id": "NV14"}),
         ("get_data_issues", {"employee_id": "NV99"}), ("get_data_issues", {"period": "2026-11"}),
         # A-ii-1 (gộp develop d188f2b 10/11): "nếu đạt X %" + ô 0 thừa + hai ô / không ô + trần + KPI tỷ lệ
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "target_pct": 100}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "Turnover", "target_pct": "95"}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "target_pct": 110, "extra": 0}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "extra": 10, "target_pct": 0}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "extra": 10, "target_pct": 100}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "target_pct": 301}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "target_pct": -5}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "LPPC", "target_pct": 100}),
         ("simulate_kpi_change", {"employee_id": "NV14", "kpi": "PC_BT", "target_pct": "abc"}),
         ("no_such_tool", {})]
# Khác có chủ đích (mục 3): khối không có bảng lưu bản tính ⇒ nhãn trạng thái mặc định "tạm tính"; bản luật ghi theo mã băm nội dung.
# + team_sentences (11/11, bậc câu cả đội): câu chuẩn gắn ở cửa ra chat / Capitol, KHÔNG ở khối agent của nền tự viết — `workflow/direct_tools.py`
# nằm trong dấu vân tay bản tính (sửa ⇒ mất "Đã soát") và nền tự viết đã dừng mở rộng. Rào vẫn tự tính câu chuẩn từ kết quả công cụ.
# + gap_status (13/11): `at_top_tier` của từng nhân viên + `period_closed` / `period_note` của get_mid_month_gap cũng gắn ở cửa ra (cùng lý do dấu vân tay)
# ⇒ khối agent của nền tự viết KHÔNG có ba trường này; mọi trường cũ vẫn phải bằng nhau.
# + ranking (13/11): ô xếp hạng lương kinh doanh của get_team_bonus_table — gắn ở cửa ra, cùng lý do ⇒ khối agent của nền tự viết không có.
# + sentence của get_data_issues (19/11, bậc 3a P5): câu chuẩn của TỪNG lưu ý — gắn ở cửa ra (`owner_check.with_team_sentences`), cùng lý do dấu vân tay
# ⇒ khối agent của nền tự viết không có; ô nằm lồng trong `issues` nên bỏ riêng ở test dưới (như `at_top_tier`), không qua DIFFERENT_ON_PURPOSE.
DIFFERENT_ON_PURPOSE = {"rule_version", "status", "status_label", "team_sentences", "period_closed", "period_note", "ranking"}


@needs_data
def test_block_context_matches_agent_context_from_backend(block_side, http_ctx):
    agent_ctx, _ = block_side
    for field in ("period", "cutoff", "employee_ids", "kpis"):
        assert getattr(agent_ctx, field) == getattr(http_ctx, field), field
    # mức tra cứu vòng 3: khối nạp bản cờ CHO MÔ HÌNH (bỏ cờ của riêng một người) — so với đúng khoá đó của /api/meta, không phải bản đủ của trang
    meta = backend_client.httpx.get(f"{BASE}/api/meta").json()
    assert agent_ctx.data_flags == meta["data_flags_for_model"]
    assert len(meta["data_flags_for_model"]) < len(meta["data_flags"])   # dữ liệu tháng 10 có ≥ 1 cờ của riêng một người (NV14)


@needs_data
def test_direct_call_equals_http_call(block_side, http_ctx):
    agent_ctx, direct = block_side
    for name, args in CALLS:
        local, http = direct(agent_ctx, name, dict(args)), call_tool(http_ctx, name, dict(args))
        if name == "no_such_tool":   # khối từ chối trước (không nối) — khác lời, cùng từ chối
            assert "refused" in local and "refused" in http
            continue
        if name == "get_mid_month_gap" and "employees" in http:
            assert all("at_top_tier" in employee and "at_top_tier" not in before
                       for employee, before in zip(http["employees"], local["employees"], strict=True))
            http = {**http, "employees": [{key: value for key, value in employee.items() if key != "at_top_tier"} for employee in http["employees"]]}
        if name == "get_data_issues" and "issues" in http:
            # + sentence (19/11, bậc 3a P5): câu chuẩn của từng lưu ý gắn ở cửa ra chat / Capitol (`owner_check.with_team_sentences`) — cùng lý do dấu vân tay
            # như team_sentences ⇒ khối agent của nền tự viết KHÔNG có ô này; mọi ô cũ của từng lưu ý vẫn phải bằng nhau.
            assert all("sentence" in issue and "sentence" not in before for issue, before in zip(http["issues"], local["issues"], strict=True))
            http = {**http, "issues": [{key: value for key, value in issue.items() if key != "sentence"} for issue in http["issues"]]}
        strip = lambda body: {key: value for key, value in body.items() if key not in DIFFERENT_ON_PURPOSE}
        assert strip(local) == strip(http), (name, args)


@needs_data
def test_unwired_tool_refused_by_code(block_side):
    agent_ctx, _ = block_side
    kpi_ctx = None   # không tới hàm công cụ nào — lời từ chối có trước
    only_two = agent_block.local_tool_caller(kpi_ctx, ["get_team_bonus_table", "get_employee_result"])
    assert "không được nối" in only_two(agent_ctx, "explain_kpi", {"employee_id": "NV05", "kpi": "ASO_ALL"})["refused"]


@needs_data
def test_nothing_sent_to_model_has_real_codes(block_side):
    agent_ctx, direct = block_side
    real_codes = set(runner.load_mask())
    sent = chat.system_prompt(agent_ctx) + json.dumps([direct(agent_ctx, name, dict(args)) for name, args in CALLS], ensure_ascii=False)
    assert not runner.REAL_CODE_PATTERN.search(sent)
    assert not [code for code in real_codes if code in sent]


# ---------- trang kéo–thả (10/11): lỗi dây công cụ phải gắn vào ĐÚNG khối (trang tô đỏ khối theo `node`) ----------
def test_tool_wire_errors_point_at_the_right_block_on_the_page():
    from kpi_agent.backend import workflow_edit
    workflow = _qa()
    workflow["edges"].append({"from_node": "kpi", "from_port": "results", "to_node": "agent", "to_port": "tools"})   # khối tính ⇒ cổng công cụ
    edge = next(e for e in workflow["edges"] if e["to_node"] == "agent" and e["to_port"] == "rules")
    edge.update(from_node="t_team", from_port="tool")                                                                 # công cụ ⇒ cổng dữ liệu
    _, items = workflow_edit.check_items(workflow, workflow_edit.allowed_files(), {})
    by_message = {item["message"]: item["node"] for item in items}
    assert any("chỉ nhận dây từ khối kpi_tool" in m and node == "agent" for m, node in by_message.items()), by_message
    assert any("chỉ cắm vào cổng công cụ" in m and node == "t_team" for m, node in by_message.items()), by_message


def test_catalog_shows_tools_port_for_the_page():
    from kpi_agent.backend import workflow_view
    agent = next(b for b in workflow_view.block_catalog() if b["type"] == "kpi_agent")
    assert [p["name"] for p in agent["tool_inputs"]] == ["tools"] and agent["tool_inputs"][0]["many"] is True
    assert "tools" not in [p["name"] for p in agent["inputs"]]   # trang vẽ cổng công cụ riêng, không lẫn cổng dữ liệu
