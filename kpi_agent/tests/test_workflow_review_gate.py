"""Khối người duyệt `review_gate` (bước 3 — mục 3–4).

Luật cổng: chưa duyệt ⇒ lượt "awaiting_review", khối sau KHÔNG chạy; đã duyệt ⇒ đi tiếp với ĐÚNG bảng thưởng cổng vào. Không bao giờ tự duyệt, không có hạn.
Không mock phép tính: workflow thật tháng 10 + dữ liệu thật. Khối đứng SAU cổng là khối giả chỉ có trong test (khối xuất file = bậc kế)."""
import copy
from datetime import date
from pathlib import Path

import pytest

from kpi_agent.workflow import blocks, runner

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "kpi_agent/workflows/kpi_thuong_nvbh.yaml"
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
TRACKING = ROOT / "sample_data/thang_10/Tracking.xlsx"
CUTOFF = date(2026, 10, 20)
TEAM_TOTAL = 55_700_000   # tổng lương kinh doanh tháng 10 @ 20/10 — runner chạy thật trên develop dbd5862 (E 08:25 10/11)
needs_data = pytest.mark.skipif(not (TARGETS.exists() and TRACKING.exists()), reason="không có dữ liệu thật trên máy này")


@pytest.fixture(scope="module")
def base_workflow():
    # Workflow chính từ bậc xuất file (10/11) đã có duyet + xuat ⇒ test này dựng lại bản CHỈ TÍNH (bỏ 2 khối đó + dây của chúng) rồi tự gắn cổng
    workflow = runner.load_workflow(WORKFLOW)[0]
    dropped = {node["id"] for node in workflow["nodes"] if node["type"] in ("review_gate", "export_files")}
    return {**workflow, "nodes": [node for node in workflow["nodes"] if node["id"] not in dropped],
            "edges": [edge for edge in workflow["edges"] if edge["to_node"] not in dropped and edge["from_node"] not in dropped]}


@pytest.fixture
def after_gate_block(monkeypatch):
    # Khối giả đứng sau cổng (chỉ trong test): đếm số NV nhận được — đủ để biết khối sau có chạy và nhận gì
    monkeypatch.setitem(blocks.BLOCKS, "_after_gate", blocks.Block(lambda inputs, params, run: {"count": len(inputs["bonuses"])},
                                                                   inputs=("bonuses",), outputs=("count",)))


def _with_gate(workflow: dict, after_gate: bool = False) -> dict:
    workflow = copy.deepcopy(workflow)
    workflow["nodes"].append({"id": "duyet", "type": "review_gate"})
    workflow["edges"].append({"from_node": "bonus", "from_port": "bonuses", "to_node": "duyet", "to_port": "bonuses"})
    if after_gate:
        workflow["nodes"].append({"id": "sau", "type": "_after_gate"})
        workflow["edges"].append({"from_node": "duyet", "from_port": "bonuses", "to_node": "sau", "to_port": "bonuses"})
    return workflow


def _step(result, node_id):
    return next(step for step in result.steps if step["node"] == node_id)


# ---------- kiểm file ----------
def test_gate_is_in_catalog_with_bonuses_ports():
    gate = blocks.BLOCKS["review_gate"]
    assert gate.inputs == ("bonuses",) and gate.outputs == ("bonuses",) and gate.params == {}


def test_workflow_with_gate_is_valid(base_workflow, after_gate_block):
    assert runner.check_workflow(_with_gate(base_workflow, after_gate=True)) == []


def test_gate_without_wire_is_rejected(base_workflow):
    workflow = copy.deepcopy(base_workflow)
    workflow["nodes"].append({"id": "duyet", "type": "review_gate"})
    assert "khối 'duyet' cổng vào bắt buộc 'bonuses' chưa có dây" in runner.check_workflow(workflow)


def test_gate_has_no_settings_to_auto_approve(base_workflow):
    # Ca xấu: ghi ô kiểu Capitol `auto_approve_on_timeout` ⇒ từ chối (cổng không có ô nào — không có đường tự duyệt)
    workflow = _with_gate(base_workflow)
    workflow["nodes"][-1]["params"] = {"auto_approve_on_timeout": True}
    assert any("không có ô: auto_approve_on_timeout" in problem for problem in runner.check_workflow(workflow))


# ---------- chạy ----------
@needs_data
def test_unapproved_gate_waits_and_blocks_next(base_workflow, after_gate_block):
    result = runner.run_workflow(_with_gate(base_workflow, after_gate=True), {"cutoff": CUTOFF})
    assert result.status == "awaiting_review"
    assert _step(result, "duyet")["status"] == "awaiting_review" and _step(result, "sau")["status"] == "not_run"
    assert "sau" not in result.outputs and "duyet" not in result.outputs
    # Chờ duyệt không phải lỗi: số tới cổng vẫn đủ, không có lời lỗi
    assert result.errors == [] and runner.team_total(result) == TEAM_TOTAL


@needs_data
def test_approved_gate_passes_same_bonuses(base_workflow, after_gate_block):
    result = runner.run_workflow(_with_gate(base_workflow, after_gate=True), {"cutoff": CUTOFF}, approved_gates=frozenset({"duyet"}))
    assert result.status == "ok"
    assert [step["status"] for step in result.steps] == ["ok"] * len(result.steps)
    assert result.outputs["duyet"]["bonuses"] == result.outputs["bonus"]["bonuses"]   # cổng không sửa một đồng nào
    assert result.outputs["sau"]["count"] == len(result.outputs["bonus"]["bonuses"])


@needs_data
def test_approval_of_other_gate_does_not_open(base_workflow, after_gate_block):
    # Ca xấu: duyệt một id khác (vd khối cũ / gõ nhầm) ⇒ cổng vẫn đóng — mặc định ĐÓNG (Capitol mở khi quyết định rỗng — D 08:22)
    result = runner.run_workflow(_with_gate(base_workflow, after_gate=True), {"cutoff": CUTOFF}, approved_gates=frozenset({"khac"}))
    assert result.status == "awaiting_review" and _step(result, "sau")["status"] == "not_run"


def test_approved_gates_must_be_a_set_of_ids(base_workflow):
    # Ca xấu: chuỗi chứa id ("xduyetx") không được mở cổng theo kiểu so chuỗi con — từ chối ngay, trước khi chạy khối nào
    with pytest.raises(TypeError, match="set / frozenset"):
        runner.run_workflow(_with_gate(base_workflow), {"cutoff": CUTOFF}, approved_gates="xduyetx")


@needs_data
def test_approval_does_not_change_fingerprint(base_workflow, after_gate_block):
    # Duyệt là sự kiện về bản tính, không phải đầu vào của nó: cùng số ⇒ cùng dấu vân tay ⇒ backend tra đúng bản tính đã soát
    workflow = _with_gate(base_workflow, after_gate=True)
    waiting = runner.run_workflow(workflow, {"cutoff": CUTOFF})
    approved = runner.run_workflow(workflow, {"cutoff": CUTOFF}, approved_gates=frozenset({"duyet"}))
    assert waiting.fingerprint == approved.fingerprint


@needs_data
def test_cli_reports_waiting(tmp_path, base_workflow, capsys):
    # Dòng lệnh không có cách duyệt ⇒ luôn chờ; mã thoát riêng (3), không lẫn với "dừng vì lỗi" (1) hay "từ chối" (2)
    import yaml
    path = tmp_path / "co_cong.yaml"
    path.write_text(yaml.safe_dump(_with_gate(base_workflow), allow_unicode=True), encoding="utf-8")
    assert runner.main([str(path), "--cutoff", "2026-10-20"]) == 3
    assert "CHỜ DUYỆT" in capsys.readouterr().out


# ---------- backend: duyệt gắn bản tính (store thật trong file tạm, TestClient — như test_backend_workflow.py) ----------
@pytest.fixture
def backend(tmp_path, monkeypatch, base_workflow):
    """Bật backend với workflow tháng 10 + cổng (file tạm qua `main.WORKFLOW` — như biến KPI_WORKFLOW). Trả hàm `start(workflow)` mở một lần bật."""
    import yaml
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))

    def start(workflow: dict, name: str = "co_cong"):
        path = tmp_path / f"{name}.yaml"
        path.write_text(yaml.safe_dump(workflow, allow_unicode=True, sort_keys=False), encoding="utf-8")
        monkeypatch.setattr(main, "WORKFLOW", path)
        return TestClient(main.app, base_url="http://127.0.0.1:8110")
    start.tmp_path = tmp_path
    return start


def _current_run(client) -> dict:
    run_id = client.get("/api/workflow-runs").json()["current_workflow_run_id"]
    return client.get(f"/api/workflow-runs/{run_id}").json()


def _store_rows(tmp_path, sql: str) -> list[tuple]:
    import sqlite3
    conn = sqlite3.connect(tmp_path / "kpi_store.sqlite")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@needs_data
def test_backend_starts_waiting_then_review_opens_gate(backend, base_workflow):
    # 1 ca thật: chưa soát ⇒ có số (tạm tính) nhưng cổng chờ; bấm "Đã soát" (POST thật) ⇒ chạy lại ⇒ cổng mở, CÙNG bản tính, cùng dấu vân tay
    with backend(_with_gate(base_workflow)) as client:
        first = _current_run(client)
        assert [s["status"] for s in first["steps"]][-1] == "awaiting_review" and first["status"] == "ok"   # bảng: ok ⇔ có bản tính (CHECK không đổi)
        run_id = client.get("/api/runs").json()["current_run_id"]
        assert client.get("/api/runs").json()["runs"][0]["team_total"] == TEAM_TOTAL
        reviewed = client.post(f"/api/runs/{run_id}/review", json={"reviewer": "Giám sát thử"})
        assert reviewed.status_code == 200, reviewed.json()
        second = _current_run(client)
        assert second["workflow_run_id"] != first["workflow_run_id"] and [s["status"] for s in second["steps"]] == ["ok"] * 7
        assert second["bonus_run_id"] == first["bonus_run_id"] == run_id and second["inputs"] == first["inputs"]
    assert _store_rows(backend.tmp_path, "SELECT COUNT(*) FROM bonus_run") == [(1,)]


@needs_data
def test_restart_after_review_opens_gate_without_new_bonus_run(backend, base_workflow):
    workflow = _with_gate(base_workflow)
    with backend(workflow) as client:
        run_id = client.get("/api/runs").json()["current_run_id"]
        client.post(f"/api/runs/{run_id}/review", json={"reviewer": "Giám sát thử"})
    with backend(workflow) as client:
        assert [s["status"] for s in _current_run(client)["steps"]][-1] == "ok"
        assert client.get("/api/runs").json()["current_run_id"] == run_id
    assert _store_rows(backend.tmp_path, "SELECT COUNT(*) FROM bonus_run") == [(1,)]


@needs_data
def test_changed_rule_input_needs_new_review(backend, base_workflow):
    # 1 ca xấu: soát bản cũ rồi đổi một ô (npp_turnover_pct) ⇒ bản tính MỚI ⇒ cổng ĐÓNG lại — không mượn lời soát của bản cũ
    with backend(_with_gate(base_workflow)) as client:
        old_run = client.get("/api/runs").json()["current_run_id"]
        client.post(f"/api/runs/{old_run}/review", json={"reviewer": "Giám sát thử"})
    edited = _with_gate(base_workflow)
    next(node for node in edited["nodes"] if node["type"] == "compute_bonus")["params"]["npp_turnover_pct"] = 105
    with backend(edited, name="doi_o") as client:
        assert client.get("/api/runs").json()["current_run_id"] != old_run
        assert [s["status"] for s in _current_run(client)["steps"]][-1] == "awaiting_review"


@needs_data
def test_trial_run_with_gate_is_recorded_and_waits(backend, base_workflow):
    # Chạy thử (bước 2b) một bản có cổng mà chưa ai soát đúng đầu vào đó ⇒ "awaiting_review", vẫn ghi được (bảng chỉ nhận ok / stopped — ghi "ok")
    with backend(base_workflow, name="goc") as client:
        saved = client.post("/api/workflow/save", json={"workflow": _with_gate(base_workflow)}).json()
        trial = client.post("/api/workflow/run", json={"workflow_version_id": saved["workflow_version_id"], "cutoff": "2026-10-20"})
        assert trial.status_code == 200, trial.json()
        assert trial.json()["status"] == "awaiting_review" and trial.json()["steps"][-1]["status"] == "awaiting_review"
    assert _store_rows(backend.tmp_path, "SELECT status FROM workflow_trial_run") == [("ok",)]
