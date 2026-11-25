"""`GET /api/names` — tên + mã SM thật cạnh mã che, CHỈ cho giao diện trên máy:5183 ("Tên · mã SM thật · NV05").

Đường duy nhất trả tên / mã thật: kiểm (1) đủ 14 mã che, mỗi mã một tên không rỗng + đúng mã SM của bảng che; (2) không cho trình duyệt giữ bản;
(3) ca xấu: file chỉ tiêu thiếu tên một NV ⇒ 409, không trả nửa danh sách, lời trả không chứa tên / mã thật. Cờ `KPI_SYSTEM_ONLY` (danh sách cho phép)
⇒ đường này 404 khi cờ bật — test của cờ nằm ở nhánh cờ. Kết quả công cụ vẫn 0 tên thật: `test_agent_tools.py::test_no_real_names_or_codes_in_any_tool_output`.
Backend chạy thật (TestClient, workflow RAW, dữ liệu thật trên máy — đọc lúc chạy, file test không chứa tên / mã thật nào). 0 lần gọi AI."""
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json",
          ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
TEAM = [f"NV{i:02d}" for i in range(1, 15)]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(tmp_path_factory.mktemp("store") / "kpi_store.sqlite"))
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


def test_names_and_sm_codes_for_every_masked_code(client):
    response = client.get("/api/names")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    names = response.json()["names"]
    assert sorted(names) == TEAM and all(isinstance(entry["name"], str) and entry["name"].strip() for entry in names.values())
    mask = {masked: str(real) for masked, real in json.loads((ROOT / "kpi_agent/data/ma_che_ten.json").read_text(encoding="utf-8"))["map"].items()}
    assert {masked: entry["sm_code"] for masked, entry in names.items()} == {code: mask[code] for code in TEAM}   # đúng mã SM của bảng che


def test_missing_name_refuses_whole_list(client, monkeypatch):
    # Ca xấu: file chỉ tiêu thiếu tên một NV ⇒ 409, không trả 13 / 14 tên; lời trả không chứa tên nào
    from kpi_agent.backend import main
    from kpi_agent.core import load_data
    good = load_data.load_targets(main._workflow_files(client.app.state.workflow)[1])
    names = [info["name"] for info in good["employees"].values()] + [str(code) for code in good["employees"]]
    first = next(iter(good["employees"]))
    broken = {**good, "employees": {**good["employees"], first: {**good["employees"][first], "name": ""}}}
    monkeypatch.setattr(load_data, "load_targets", lambda path: broken)
    response = client.get("/api/names")
    assert response.status_code == 409 and not any(name in response.text for name in names)   # lời từ chối không lộ tên / mã thật


def test_flag_off_bonus_table_has_no_names_and_tools_take_only_masked_codes(client, monkeypatch):
    # Cờ KPI_NAMES off (mặc định): thân /api/system/bonus-table y cũ (không khoá `names`), mã băm tính trên `table`; công cụ không nhận tên, không trả tên
    monkeypatch.setenv("KPI_SYS_KEY", "khoa-thu-trong-test-0123456789")
    body = client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": "khoa-thu-trong-test-0123456789"}).json()
    assert sorted(body) == ["period", "run_id", "sha256", "status", "table"]
    canonical = json.dumps(body["table"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == body["sha256"]
    name = client.get("/api/names").json()["names"]["NV01"]["name"]
    assert "refused" in client.post("/api/tool/get_employee_result", json={"args": {"employee_id": name}}).json()
    by_code = client.post("/api/tool/get_employee_result", json={"args": {"employee_id": "NV01"}}).json()
    assert by_code["employee_id"] == "NV01" and "name" not in by_code and "sm_code" not in by_code
