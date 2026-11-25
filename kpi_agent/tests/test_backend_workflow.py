"""Bước 1 S3 (mục 3–4): backend lấy số từ MỘT lượt chạy workflow — W3 (dữ liệu lỗi ⇒ backend không bật), W5 (đổi ô ⇒ bản tính
mới, lượt cũ trỏ phiên bản cũ), kéo khối (chỉ đổi position) ⇒ KHÔNG bản tính mới, không mất "Đã soát".

SQLite thật trong file tạm; dữ liệu thật tháng 10; file workflow thử + bản sao chỉ tiêu hỏng ghi vào thư mục tạm. `main.WORKFLOW` đổi bằng monkeypatch
(chỉ đổi đường file cấu hình — như biến môi trường KPI_WORKFLOW lúc bật server), không giả lập hành vi nào."""
import sqlite3
from pathlib import Path

import openpyxl
import pytest
import yaml
from fastapi.testclient import TestClient

from kpi_agent.backend import main
from kpi_agent.workflow import runner

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    path = tmp_path / "kpi_store.sqlite"
    monkeypatch.setenv("KPI_STORE", str(path))
    return path


def _workflow_file(tmp_path: Path, name: str, edit=None) -> Path:
    workflow, _ = runner.load_workflow(runner.DEFAULT_WORKFLOW)
    if edit:
        edit(workflow)
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(workflow, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _start(monkeypatch, workflow_path: Path) -> dict:
    monkeypatch.setattr(main, "WORKFLOW", workflow_path)
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        return {"meta": client.get("/api/meta").json(), "runs": client.get("/api/runs").json()}


def _rows(store_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(store_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_startup_records_workflow_run_after_bonus_run(store_path, monkeypatch, tmp_path):
    started = _start(monkeypatch, _workflow_file(tmp_path, "goc"))
    meta = started["meta"]
    assert meta["workflow_version"].startswith("kpi_thuong_nvbh@") and started["runs"]["runs"][0]["team_total"] == 55_700_000
    rows = _rows(store_path, "SELECT id, status, bonus_run_id, cutoff, inputs_sha256 FROM workflow_run")
    assert rows == [(meta["workflow_run_id"], "ok", meta["current_run_id"], "2026-10-20", rows[0][4])]
    assert any(name.startswith("workflow:kpi_thuong_nvbh@") for name in started["runs"]["runs"][0]["code_files"])
    assert "kpi_agent/workflow/runner.py" in started["runs"]["runs"][0]["code_files"]


def test_w3_missing_kpi_backend_does_not_start_no_bonus_run(store_path, monkeypatch, tmp_path):
    broken = tmp_path / "chi_tieu_thieu_1_kpi.xlsx"
    book = openpyxl.load_workbook(NEEDED[0])
    book["Export"].delete_rows(2)
    book.save(broken)
    path = _workflow_file(tmp_path, "hong", lambda wf: next(n for n in wf["nodes"] if n["id"] == "targets")["params"].update(path=str(broken)))
    monkeypatch.setattr(main, "WORKFLOW", path)
    with pytest.raises(RuntimeError, match="backend KHÔNG bật") as stopped:
        with TestClient(main.app, base_url="http://127.0.0.1:8110"):
            pass
    message = str(stopped.value)
    assert not any(code in message for code in runner.load_mask()) and not runner.REAL_CODE_PATTERN.search(message)
    assert _rows(store_path, "SELECT COUNT(*) FROM bonus_run") == [(0,)]          # 0 bản tính mới
    (status, bonus_run, steps), = _rows(store_path, "SELECT status, bonus_run_id, steps FROM workflow_run")
    assert (status, bonus_run) == ("stopped", None) and '"check", "type": "check_data", "status": "stopped"' in steps
    assert not runner.REAL_CODE_PATTERN.search(steps) and '"issues"' in steps     # lượt dừng giữ các mục kiểm (đã che)


def test_w5_changed_param_new_run_old_run_keeps_old_version(store_path, monkeypatch, tmp_path):
    first = _start(monkeypatch, _workflow_file(tmp_path, "goc"))["meta"]
    edited = _start(monkeypatch, _workflow_file(tmp_path, "sua_o", lambda wf: next(n for n in wf["nodes"] if n["type"] == "compute_bonus")["params"].update(npp_turnover_pct=130)))   # 10/11 (bước 3): workflow chính thêm duyet (người duyệt) + xuat (xuất file) sau compute_bonus ⇒ tìm theo loại
    second = edited["meta"]
    assert second["current_run_id"] != first["current_run_id"] and second["workflow_version"] != first["workflow_version"]
    runs = {run["run_id"]: run for run in edited["runs"]["runs"]}
    assert runs[first["current_run_id"]]["replaced_by"] == second["current_run_id"]       # bản cũ "bị thay"
    assert runs[second["current_run_id"]]["team_total"] == runs[first["current_run_id"]]["team_total"] == 55_700_000
    # lượt workflow cũ vẫn trỏ phiên bản cũ + bản tính cũ
    rows = _rows(store_path, "SELECT r.id, r.workflow_version_id, r.bonus_run_id FROM workflow_run r ORDER BY r.id")
    assert [row[2] for row in rows] == [first["current_run_id"], second["current_run_id"]] and rows[0][1] != rows[1][1]
    for table in ("workflow_version", "workflow_run"):
        conn = sqlite3.connect(store_path)
        with pytest.raises(sqlite3.IntegrityError, match="chỉ thêm"):
            conn.execute(f"UPDATE {table} SET id = id")
        with pytest.raises(sqlite3.IntegrityError, match="chỉ thêm"):
            conn.execute(f"DELETE FROM {table}")
        conn.close()


def test_moving_blocks_keeps_bonus_run_and_review(store_path, monkeypatch, tmp_path):
    # 04/11: position là bố cục ⇒ kéo khối không tạo bản tính mới, không mất "Đã soát"
    first = _start(monkeypatch, _workflow_file(tmp_path, "goc"))["meta"]
    monkeypatch.setattr(main, "WORKFLOW", _workflow_file(tmp_path, "goc"))
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        assert client.post(f"/api/runs/{first['current_run_id']}/review", json={"reviewer": "Giám sát A"}).status_code == 200

    def move(workflow):
        for index, node in enumerate(workflow["nodes"]):
            node["position"] = {"x": 200 * index, "y": 80}
    moved = _start(monkeypatch, _workflow_file(tmp_path, "keo_khoi", move))["meta"]
    assert (moved["current_run_id"], moved["workflow_version"], moved["status"]) == (first["current_run_id"], first["workflow_version"], "da_soat")
    assert _rows(store_path, "SELECT COUNT(*) FROM bonus_run") == [(1,)]
    assert _rows(store_path, "SELECT COUNT(*) FROM workflow_version") == [(1,)]


def test_bad_workflow_file_backend_does_not_start(store_path, monkeypatch, tmp_path):
    bad = tmp_path / "sai.yaml"
    bad.write_text(runner.DEFAULT_WORKFLOW.read_text(encoding="utf-8").replace("type: compute_kpi", "type: khoi_la"), encoding="utf-8")
    monkeypatch.setattr(main, "WORKFLOW", bad)
    with pytest.raises(RuntimeError, match="backend KHÔNG bật"):
        with TestClient(main.app, base_url="http://127.0.0.1:8110"):
            pass
    # "rejected": 0 khối chạy ⇒ không mở bảng lưu, không có gì để ghi
    assert not store_path.exists()
