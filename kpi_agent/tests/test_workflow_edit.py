"""Bước 2b (bản 2 mục 4): kiểm / lưu / chạy THỬ workflow từ trang kéo–thả.

T1 ráp lại theo thứ tự lộn ⇒ đúng mã băm file gốc, không đẻ phiên bản · T2 chạy thử ⇒ 6 / 6 bước, 55.700.000 đ, số trang chính KHÔNG đổi ·
T3 chỉ kéo khối ⇒ cùng mã băm · T4 file sai bị backend từ chối khi gọi API thẳng · T5 đường file ngoài danh sách (5 ca) · T6 ngày chốt thiếu / sai ·
T7 thân không phải JSON. Dữ liệu thật tháng 10, store SQLite trong thư mục tạm (như test_workflow_view.py) — không giả lập hành vi nào."""
import copy
import random
import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from kpi_agent.backend import main, workflow_edit
from kpi_agent.workflow import runner

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
FILE_SHA = runner.workflow_sha256(runner.load_workflow(runner.DEFAULT_WORKFLOW)[0])
TEAM_TOTAL_OCT = 55_700_000   # tổng lương kinh doanh cả đội, ngày chốt 20/10 (đáp án 112 / 112 — KET_QUA mục 2)


@pytest.fixture
def client(tmp_path, monkeypatch):
    store_file = tmp_path / "store.sqlite"
    monkeypatch.setenv("KPI_STORE", str(store_file))
    monkeypatch.setattr(main, "WORKFLOW", runner.DEFAULT_WORKFLOW)
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
        test_client.store_file = store_file
        yield test_client


def _file_workflow() -> dict:
    return runner.load_workflow(runner.DEFAULT_WORKFLOW)[0]


def _as_page_builds_it(seed: int) -> dict:
    """Như người dùng ráp trên trang: khối + dây theo thứ tự lộn, có toạ độ, khối không ô gửi params rỗng."""
    rng, workflow = random.Random(seed), copy.deepcopy(_file_workflow())
    rng.shuffle(workflow["nodes"])
    rng.shuffle(workflow["edges"])
    for node in workflow["nodes"]:
        node["position"] = {"x": rng.randint(0, 900), "y": rng.randint(0, 500)}
        node.setdefault("params", {})
    return workflow


def _post(client, path: str, body: dict):
    return client.post(path, json=body)


def _count(store_file: Path, table: str) -> int:
    conn = sqlite3.connect(store_file)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_t1_rebuilt_in_any_order_has_file_sha_and_no_new_version(client):
    versions_before = _count(client.store_file, "workflow_version")
    for seed in range(5):
        response = _post(client, "/api/workflow/save", {"workflow": _as_page_builds_it(seed)})
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["workflow_version"] == f"kpi_thuong_nvbh@{FILE_SHA[:8]}" and body["is_new"] is False
        # thứ tự trả về = thứ tự file gốc (khối + dây) ⇒ bộ chạy chạy được, không "dây ngược"
        assert [n["id"] for n in body["workflow"]["nodes"]] == [n["id"] for n in _file_workflow()["nodes"]]
        assert body["workflow"]["edges"] == _file_workflow()["edges"]
    assert _count(client.store_file, "workflow_version") == versions_before


def test_t3_moving_a_block_keeps_sha(client):
    workflow = _as_page_builds_it(1)
    first = _post(client, "/api/workflow/check", {"workflow": workflow}).json()
    workflow["nodes"][0]["position"] = {"x": 1234, "y": -50}
    second = _post(client, "/api/workflow/check", {"workflow": workflow}).json()
    assert first["kiem"] == [] and first["workflow_sha256"] == second["workflow_sha256"] == FILE_SHA


def test_t2_trial_run_does_not_change_numbers_in_use(client):
    meta_before, board_before = client.get("/api/meta").json(), client.get("/api/team-bonus").json()
    edited = copy.deepcopy(_file_workflow())
    next(n for n in edited["nodes"] if n["type"] == "compute_bonus")["params"]["npp_turnover_pct"] = 105   # bản KHÁC file đang dùng
    saved = _post(client, "/api/workflow/save", {"workflow": edited}).json()
    assert saved["is_new"] is True
    trial = _post(client, "/api/workflow/run", {"workflow_version_id": saved["workflow_version_id"], "cutoff": "2026-10-20"})
    assert trial.status_code == 200, trial.json()
    body = trial.json()
    # 10/11 (bước 3): workflow chính thêm duyet (người duyệt) + xuat (xuất file) sau compute_bonus: chạy thử chưa ai soát đúng đầu vào này ⇒ dừng ở cổng, 6 khối tính vẫn ok
    assert body["status"] == "awaiting_review" and [s["status"] for s in body["steps"]] == ["ok"] * 6 + ["awaiting_review", "not_run"]
    assert body["team_total"] == TEAM_TOTAL_OCT
    assert body["ports"]["bonus"]["bonuses"]["business_salary_total"] == TEAM_TOTAL_OCT
    # số trang chính / trợ lý dùng KHÔNG đổi; không bản tính mới, không lượt workflow_run mới
    assert client.get("/api/meta").json() == meta_before and client.get("/api/team-bonus").json() == board_before
    assert _count(client.store_file, "bonus_run") == 1 and _count(client.store_file, "workflow_run") == 1
    assert _count(client.store_file, "workflow_trial_run") == 1
    listed = client.get("/api/workflow/trial-runs").json()["runs"]
    assert listed[0]["trial_run_id"] == body["trial_run_id"] and listed[0]["summary"]["team_total"] == TEAM_TOTAL_OCT


def _bad_workflows() -> dict:
    def edit(change):
        workflow = copy.deepcopy(_file_workflow())
        change(workflow)
        return workflow
    return {
        "loại khối lạ": edit(lambda w: w["nodes"][3].update(type="send_email")),
        "thiếu dây vào bắt buộc": edit(lambda w: w["edges"].pop(0)),
        "hai dây một cổng": edit(lambda w: w["edges"].append(dict(w["edges"][0]))),
        "trường lạ của trang (label)": edit(lambda w: w["nodes"][0].update(label="Đọc bản luật")),
        "ô sai kiểu": edit(lambda w: w["nodes"][5]["params"].update(npp_turnover_pct="105")),
        "vòng": edit(lambda w: w["edges"].append({"from_node": "bonus", "from_port": "bonuses", "to_node": "kpi", "to_port": "rules"})),
    }


@pytest.mark.parametrize("case", list(_bad_workflows()))
def test_t4_bad_workflow_refused_by_backend_called_directly(client, case):
    versions_before = _count(client.store_file, "workflow_version")
    check = _post(client, "/api/workflow/check", {"workflow": _bad_workflows()[case]}).json()
    assert check["kiem"] and check["workflow_sha256"] is None and all(item["muc"] == "loi" for item in check["kiem"])
    save = _post(client, "/api/workflow/save", {"workflow": _bad_workflows()[case]})
    assert save.status_code == 422 and save.json()["kiem"]
    assert _count(client.store_file, "workflow_version") == versions_before


@pytest.mark.parametrize("path", ["../.env", "/etc/passwd", "sample_data/thang_10/../../.env", "%2e%2e/.env",
                                  str(ROOT / "kpi_agent/config/rules_2026_10.yaml")])
def test_t5_paths_outside_allowlist_refused(client, path):
    workflow = copy.deepcopy(_file_workflow())
    workflow["nodes"][0]["params"]["path"] = path
    check = _post(client, "/api/workflow/check", {"workflow": workflow}).json()
    assert [item["field"] for item in check["kiem"]] == ["path"] and check["kiem"][0]["node"] == "rules"
    assert _post(client, "/api/workflow/save", {"workflow": workflow}).status_code == 422


def test_t5_symlink_out_of_allowed_folder_not_listed(tmp_path):
    inside, outside = tmp_path / "cho_phep", tmp_path / "ngoai"
    inside.mkdir(), outside.mkdir()
    (inside / "luat.yaml").write_text("x: 1", encoding="utf-8")
    (outside / "bi_mat.yaml").write_text("x: 2", encoding="utf-8")
    (inside / "tro_ra_ngoai.yaml").symlink_to(outside / "bi_mat.yaml")
    assert workflow_edit.allowed_files(("cho_phep",), tmp_path) == ["cho_phep/luat.yaml"]


def test_t5_saved_version_with_outside_path_is_not_run(client):
    # bảng phiên bản có thể chứa bản do file lúc bật ghi (KPI_WORKFLOW trỏ ngoài danh sách) — chạy thử vẫn phải từ chối, 0 khối chạy
    workflow = copy.deepcopy(_file_workflow())
    workflow["nodes"][0]["params"]["path"] = "/etc/passwd"
    with main.store.connect(client.store_file) as conn:
        version_id = main.store.record_workflow_version(conn, "thu_ngoai", yaml.safe_dump(workflow), runner.workflow_sha256(workflow))
    response = _post(client, "/api/workflow/run", {"workflow_version_id": version_id, "cutoff": "2026-10-20"})
    assert response.status_code == 422 and response.json()["kiem"][0]["field"] == "path"
    assert _count(client.store_file, "workflow_trial_run") == 0


@pytest.mark.parametrize("cutoff", [None, "", "20261020", "2026-13-01", 20261020, "hôm nay"])
def test_t6_missing_or_bad_cutoff_runs_nothing(client, cutoff):
    version = client.get("/api/workflow-versions").json()["versions"][0]["workflow_version_id"]
    body = {"workflow_version_id": version} | ({} if cutoff is None else {"cutoff": cutoff})
    response = _post(client, "/api/workflow/run", body)
    assert response.status_code == 422 and "ngày chốt" in response.json()["refused"]
    assert _count(client.store_file, "workflow_trial_run") == 0


@pytest.mark.parametrize("version_id", [0, -1, True, "1", 2**63])
def test_t6_bad_version_id(client, version_id):
    assert _post(client, "/api/workflow/run", {"workflow_version_id": version_id, "cutoff": "2026-10-20"}).status_code == 422


def test_t7_non_json_bodies_refused(client):
    for path in ("/api/workflow/check", "/api/workflow/save", "/api/workflow/run"):
        form = client.post(path, data={"workflow": "x"})
        text = client.post(path, content=b"{not json", headers={"content-type": "application/json"})
        array = client.post(path, json=[1, 2])
        # minor 1 (C 11:00): sai kiểu nội dung ⇒ 415 như kế hoạch T7; thân JSON hỏng / không phải bảng ⇒ 422
        assert (form.status_code, text.status_code, array.status_code) == (415, 422, 422)


def test_versions_list_and_detail(client):
    listed = client.get("/api/workflow-versions").json()
    assert listed["current"] == f"kpi_thuong_nvbh@{FILE_SHA[:8]}"
    detail = client.get(f"/api/workflow-versions/{listed['versions'][0]['workflow_version_id']}").json()
    assert detail["workflow"] == "kpi_thuong_nvbh" and len(detail["nodes"]) == 8 and len(detail["edges"]) == 10   # 10/11 (bước 3): workflow chính thêm duyet (người duyệt) + xuat (xuất file) sau compute_bonus
    assert client.get("/api/workflow-versions/999").status_code == 404
    assert client.get("/api/workflow/files").json()["files"] == workflow_edit.allowed_files()


def test_duplicate_id_points_at_the_block(client):
    # minor 2 (C 11:00): lời "khối số N: id '<id>' trùng …" phải ra CheckItem.node để trang tô được khối (bản đầu: None)
    workflow = copy.deepcopy(_file_workflow())
    workflow["nodes"].append(dict(workflow["nodes"][0]))
    items = _post(client, "/api/workflow/check", {"workflow": workflow}).json()["kiem"]
    duplicate = [i for i in items if "trùng" in i["message"]]
    assert duplicate and all(i["node"] == "rules" for i in duplicate)
