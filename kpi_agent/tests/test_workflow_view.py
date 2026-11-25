"""Bước 2a giai đoạn 1 (mục 1, 3): 4 đường GET cho trang chỉ xem workflow — khớp file YAML, đọc được lượt `stopped` của lần bật
trước, tóm tắt cổng ra không lộ mã / tên thật, và thêm các đường này KHÔNG đổi bản tính (dấu vân tay code không gồm backend/main.py).

Dữ liệu thật tháng 10; store + file workflow thử trong thư mục tạm (như test_backend_workflow.py)."""
import json
from pathlib import Path

import openpyxl
import pytest
import yaml
from fastapi.testclient import TestClient

from kpi_agent.backend import main
from kpi_agent.workflow import runner
from kpi_agent.workflow.blocks import BLOCKS

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
NEEDED = [TARGETS, ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")


def _real_strings() -> list[str]:
    # Mã + tên thật đọc thẳng từ sheet Export (không qua load_targets) — để dò lộ
    rows = list(openpyxl.load_workbook(TARGETS, read_only=True)["Export"].iter_rows(values_only=True))
    header = rows[0]
    code, name = header.index("SM code"), header.index("SM name")
    return sorted({str(r[code]) for r in rows[1:] if r[code]} | {str(r[name]) for r in rows[1:] if r[name]})


def _workflow_file(tmp_path: Path, name: str, edit=None) -> Path:
    workflow, _ = runner.load_workflow(runner.DEFAULT_WORKFLOW)
    if edit:
        edit(workflow)
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(workflow, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def client_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))

    def start(workflow_path: Path):
        monkeypatch.setattr(main, "WORKFLOW", workflow_path)
        return TestClient(main.app, base_url="http://127.0.0.1:8110")
    return start


def test_blocks_catalog_matches_BLOCKS(client_factory, tmp_path):
    with client_factory(_workflow_file(tmp_path, "goc")) as client:   # store tạm — không tạo kpi_store.sqlite thật
        catalog = client.get("/api/blocks").json()["blocks"]
    assert [b["type"] for b in catalog] == list(BLOCKS)
    for entry in catalog:
        block = BLOCKS[entry["type"]]
        assert [p["name"] for p in entry["inputs"]] == list(block.inputs) and [p["name"] for p in entry["outputs"]] == list(block.outputs)
        assert [p["name"] for p in entry["params"]] == list(block.params) and entry["label"] and entry["group"] and entry["description"]
        assert all(port["label"] != port["name"] for port in entry["inputs"] + entry["outputs"])   # mọi cổng có chữ tiếng Việt
    npp = next(p for e in catalog for p in e["params"] if p["name"] == "npp_turnover_pct")
    assert "0–300" in npp["kind"] and npp["default"] is None and npp["required"] is False   # khoảng lấy đúng chữ của bộ kiểm


def test_workflow_and_current_run_match_yaml_and_do_not_leak(client_factory, tmp_path):
    source = yaml.safe_load(runner.DEFAULT_WORKFLOW.read_text(encoding="utf-8"))
    with client_factory(_workflow_file(tmp_path, "goc")) as client:
        meta = client.get("/api/meta").json()
        wf = client.get("/api/workflow").json()
        runs = client.get("/api/workflow-runs").json()
        run = client.get(f"/api/workflow-runs/{meta['workflow_run_id']}").json()
        missing, bad = client.get("/api/workflow-runs/999"), client.get("/api/workflow-runs/abc")
        huge, zero = client.get("/api/workflow-runs/99999999999999999999"), client.get("/api/workflow-runs/0")   # GĐ1: số lớn từng ⇒ 500
    assert [n["id"] for n in wf["nodes"]] == [n["id"] for n in source["nodes"]] and wf["edges"] == source["edges"]
    assert wf["workflow_version"] == meta["workflow_version"] and runs["current_workflow_run_id"] == meta["workflow_run_id"]
    assert run["is_current"] and [s["node"] for s in run["steps"]] == [n["id"] for n in source["nodes"]]
    # 10/11 (bước 3): workflow chính thêm duyet (người duyệt) + xuat (xuất file) sau compute_bonus: chưa ai soát ⇒ cổng chờ, khối xuất chưa chạy
    assert [s["status"] for s in run["steps"]] == ["ok"] * 6 + ["awaiting_review", "not_run"] and all(s["seconds"] is not None for s in run["steps"][:7])
    summaries = run["port_summaries"]
    assert summaries["bonus"]["bonuses"]["business_salary_total"] == 55_700_000 and summaries["targets"]["targets"]["employees"] == 14
    assert summaries["check"]["issues"]["levels"] == {"warning": 1, "info": 2}
    assert missing.status_code == 404 and bad.status_code == 422 and huge.status_code == 422 and zero.status_code == 422
    assert all("inputs" not in item for item in runs["runs"])
    dumped = json.dumps([wf, runs, run], ensure_ascii=False)
    assert not [s for s in _real_strings() if s in dumped]          # 0 mã / tên thật trong cả 3 đường


def test_stopped_run_of_previous_start_is_listed(client_factory, tmp_path):
    broken = tmp_path / "chi_tieu_thieu.xlsx"
    book = openpyxl.load_workbook(TARGETS)
    book["Export"].delete_rows(2)
    book.save(broken)
    bad = _workflow_file(tmp_path, "hong", lambda wf: next(n for n in wf["nodes"] if n["id"] == "targets")["params"].update(path=str(broken)))
    with pytest.raises(RuntimeError):
        with client_factory(bad):
            pass
    with client_factory(_workflow_file(tmp_path, "goc")) as client:     # lần bật sau, cùng store, dữ liệu tốt
        runs = client.get("/api/workflow-runs").json()["runs"]
        stopped = client.get(f"/api/workflow-runs/{runs[-1]['workflow_run_id']}").json()
    assert [r["status"] for r in runs] == ["ok", "stopped"]
    assert {s["node"]: s["status"] for s in stopped["steps"]} == {"rules": "ok", "targets": "ok", "tracking": "ok", "check": "stopped",
                                                                  "kpi": "not_run", "bonus": "not_run", "duyet": "not_run", "xuat": "not_run"}
    assert stopped["is_current"] is False and stopped["port_summaries"] is None
    assert not [s for s in _real_strings() if s in json.dumps(stopped, ensure_ascii=False)]


def test_run_shows_nodes_of_its_own_version(client_factory, tmp_path):
    with client_factory(_workflow_file(tmp_path, "goc")) as client:
        first = client.get("/api/meta").json()["workflow_run_id"]
    npp = _workflow_file(tmp_path, "npp", lambda wf: next(n for n in wf["nodes"] if n["id"] == "bonus")["params"].update(npp_turnover_pct=130))
    with client_factory(npp) as client:
        old = client.get(f"/api/workflow-runs/{first}").json()
    bonus = next(n for n in old["nodes"] if n["id"] == "bonus")
    assert bonus["params"]["npp_turnover_pct"] is None and old["is_current"] is False   # lượt cũ vẽ phiên bản cũ, không vẽ file hiện tại


def test_new_routes_do_not_change_bonus_fingerprint():
    # main.py / workflow_view.py không nằm trong dấu vân tay ⇒ thêm 2a không đẻ bản tính mới (G5)
    names = {path.relative_to(ROOT).as_posix() for path in main.CODE_FILES}
    assert "kpi_agent/backend/main.py" not in names and "kpi_agent/backend/workflow_view.py" not in names
    assert "kpi_agent/backend/store.py" not in names


def test_old_run_routes_reject_out_of_range_ids(client_factory, tmp_path):
    # 04/11 (theo ca ở 2a GĐ1): 3 đường bản tính cũ cũng từng ⇒ 500 (OverflowError SQLite) với mã rất lớn
    huge = "99999999999999999999"
    with client_factory(_workflow_file(tmp_path, "goc")) as client:
        codes = [client.get(f"/api/runs/{huge}").status_code, client.get(f"/api/runs/{huge}/verify").status_code,
                 client.post(f"/api/runs/{huge}/review", json={"reviewer": "x", "note": ""}).status_code,
                 client.get("/api/runs/0").status_code, client.get("/api/runs/999").status_code]
    assert codes == [422, 422, 422, 422, 404]
