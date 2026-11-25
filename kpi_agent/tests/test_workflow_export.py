"""Khối xuất file `export_files` + workflow chính có cổng duyệt (bước 3 phần 2 —).

Luật (10/11: BỎ tải nháp): chưa soát ⇒ khối xuất không chạy ⇒ KHÔNG tải được file nào; soát xong ⇒ tải được đúng định dạng khối khai báo.
File vẫn do `export_report.py` dựng từ BẢN TÍNH ĐÃ LƯU (mã che) — khối chỉ khai báo, không dựng file. Dữ liệu thật tháng 10, SQLite thật trong file tạm."""
import copy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from kpi_agent.backend import main
from kpi_agent.workflow import blocks, runner

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
needs_data = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
KINDS = ("xlsx", "md", "txt")


def _main_workflow() -> dict:
    return runner.load_workflow(runner.DEFAULT_WORKFLOW)[0]


def _export_node(workflow: dict) -> dict:
    return next(node for node in workflow["nodes"] if node["type"] == "export_files")


# ---------- danh bạ + file ----------
def test_export_block_only_declares_formats():
    block = blocks.BLOCKS["export_files"]
    assert block.inputs == ("bonuses",) and block.outputs == ("export",) and block.params == {"formats": ["xlsx", "md", "txt"]}
    assert blocks.run_block("export_files", {"bonuses": {}}, {"formats": ["md"]}) == {"export": {"formats": ["md"]}}


def test_main_workflow_is_compute_then_review_then_export():
    workflow = _main_workflow()
    assert runner.check_workflow(workflow) == []
    assert [node["type"] for node in workflow["nodes"]][-3:] == ["compute_bonus", "review_gate", "export_files"]
    wires = {(edge["from_node"], edge["to_node"]) for edge in workflow["edges"]}
    gate, export = runner.gate_ids(workflow), _export_node(workflow)["id"]
    assert any((source, export) in wires for source in gate)   # khối xuất đứng SAU cổng — không có dây đi vòng qua cổng


@pytest.mark.parametrize("bad", [["pdf"], "xlsx", [], ["xlsx", "xlsx"], [1]])
def test_bad_formats_are_rejected(bad):
    # Ca xấu: định dạng lạ / chuỗi thay danh sách / rỗng / trùng ⇒ bộ kiểm file từ chối, 0 khối chạy
    workflow = _main_workflow()
    _export_node(workflow).setdefault("params", {})["formats"] = bad
    assert any("formats" in problem for problem in runner.check_workflow(workflow)), bad


# ---------- backend: chưa soát ⇒ không file; soát ⇒ có file ----------
@pytest.fixture
def start(tmp_path, monkeypatch):
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))

    def _start(workflow: dict | None = None, name: str = "wf"):
        path = runner.DEFAULT_WORKFLOW
        if workflow is not None:
            path = tmp_path / f"{name}.yaml"
            path.write_text(yaml.safe_dump(workflow, allow_unicode=True, sort_keys=False), encoding="utf-8")
        monkeypatch.setattr(main, "WORKFLOW", path)
        return TestClient(main.app, base_url="http://127.0.0.1:8110")
    return _start


def _review(client) -> int:
    run_id = client.get("/api/meta").json()["current_run_id"]
    assert client.post(f"/api/runs/{run_id}/review", json={"reviewer": "Giám sát thử"}).status_code == 200
    return run_id


@needs_data
def test_unreviewed_has_no_file(start):
    with start() as client:
        meta = client.get("/api/meta").json()
        assert meta["export"] == {"allowed": False, "formats": [], "reason": meta["export"]["reason"]} and "chưa" in meta["export"]["reason"]
        for kind in KINDS:
            response = client.get(f"/api/export/{kind}")
            assert response.status_code == 409 and "chưa duyệt" in response.json()["refused"], kind


@needs_data
def test_review_unlocks_all_declared_files(start):
    with start() as client:
        run_id = _review(client)
        assert client.get("/api/meta").json()["export"]["allowed"] is True
        for kind in KINDS:
            response = client.get(f"/api/export/{kind}")
            assert response.status_code == 200, (kind, response.text[:200])
            assert response.headers["content-disposition"] == f'attachment; filename="bang_thuong_2026-10_ban{run_id}.{kind}"'
        assert "ĐÃ SOÁT" in client.get("/api/export/md").text and "NHÁP" not in client.get("/api/export/md").text


@needs_data
def test_only_declared_formats_after_review(start):
    workflow = _main_workflow()
    _export_node(workflow).setdefault("params", {})["formats"] = ["xlsx"]
    with start(workflow, "chi_xlsx") as client:
        _review(client)
        assert client.get("/api/export/xlsx").status_code == 200
        assert client.get("/api/export/md").status_code == 409 and client.get("/api/export/txt").status_code == 409


@needs_data
def test_changed_input_after_review_locks_files_again(start):
    # 1 ca xấu: soát bản cũ ⇒ đổi ô ⇒ bản tính MỚI chưa soát ⇒ không tải được (không mượn lời soát bản cũ)
    with start() as client:
        old = _review(client)
    edited = copy.deepcopy(_main_workflow())
    next(node for node in edited["nodes"] if node["type"] == "compute_bonus")["params"]["npp_turnover_pct"] = 105
    with start(edited, "doi_o") as client:
        assert client.get("/api/meta").json()["current_run_id"] != old
        assert client.get("/api/export/xlsx").status_code == 409


@needs_data
def test_run_without_workflow_run_cannot_export(start):
    # Bản tính ghi tay vào store (không qua workflow, không qua cổng) ⇒ không xuất — đường duy nhất ra file là đi qua cổng
    from kpi_agent.backend import store
    with start() as client:
        _review(client)
        with store.connect(Path(main.os.environ["KPI_STORE"])) as conn:
            run_id = client.get("/api/meta").json()["current_run_id"]
            summary, lines = store.run_summary(conn, run_id), store.run_lines(conn, run_id)
            other = store.record_run(conn, period=summary["period"], cutoff=summary["data_snapshot"]["cutoff"],
                                     rule_file=ROOT / "kpi_agent/config/rules_2026_10.yaml", data_files=summary["data_snapshot"]["files_sha256"],
                                     refreshed_at=None, code_sha="ghi_tay", code_files=[], lines=lines)
        assert client.get(f"/api/export/xlsx?run_id={other}").status_code == 409


@needs_data
def test_workflow_without_gate_still_needs_review(start):
    # Ca xấu C B3 (10/11, major): bỏ cổng, nối thẳng bonus ⇒ xuat — file hợp lệ, bước xuất chạy, nhưng CHƯA SOÁT ⇒ vẫn không có file
    workflow = _main_workflow()
    gate = next(node["id"] for node in workflow["nodes"] if node["type"] == "review_gate")
    workflow["nodes"] = [node for node in workflow["nodes"] if node["id"] != gate]
    workflow["edges"] = [edge for edge in workflow["edges"] if gate not in (edge["from_node"], edge["to_node"])]
    workflow["edges"].append({"from_node": "bonus", "from_port": "bonuses", "to_node": _export_node(workflow)["id"], "to_port": "bonuses"})
    assert runner.check_workflow(workflow) == []
    with start(workflow, "bo_cong") as client:
        assert client.get("/api/meta").json()["export"]["allowed"] is False
        assert all(client.get(f"/api/export/{kind}").status_code == 409 for kind in KINDS)
        _review(client)
        assert client.get("/api/meta").json()["export"]["allowed"] is True and client.get("/api/export/xlsx").status_code == 200


@needs_data
def test_replaced_run_cannot_export_even_if_reviewed(start):
    # 10/11: bản đã soát + xuất nhưng sau đó bị thay ⇒ KHÔNG tải nữa, lời nói rõ "đã bị thay — soát bản mới"
    with start() as client:
        old = _review(client)
        assert client.get(f"/api/export/xlsx?run_id={old}").status_code == 200
    edited = copy.deepcopy(_main_workflow())
    next(node for node in edited["nodes"] if node["type"] == "compute_bonus")["params"]["npp_turnover_pct"] = 105
    with start(edited, "doi_o") as client:
        response = client.get(f"/api/export/xlsx?run_id={old}")
        assert response.status_code == 409 and "đã bị thay" in response.json()["refused"] and "soát bản mới" in response.json()["refused"]
