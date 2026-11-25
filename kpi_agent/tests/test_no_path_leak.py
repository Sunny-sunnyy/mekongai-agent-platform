"""Lời từ chối / lời lỗi của backend không được lộ ĐƯỜNG DẪN TUYỆT ĐỐI của máy hay chuỗi lỗi của hệ điều hành ("[Errno 2] …") — 12/11.

Vì sao: người kiểm độc lập thấy thiếu sổ nền của khoản 1 % gia vị ⇒ `/api/spice-loyalty` trả nguyên chuỗi `FileNotFoundError` (đường dẫn thư mục nhà
trên máy) ra lời trả; thiếu bảng mã che ⇒ lỗi trần lúc bật. Lời trả đi tới trang, tới hộp Capitol và có thể tới mô hình AI.
Backend chạy thật (TestClient, workflow RAW 31/10, dữ liệu trên máy), 0 lần gọi AI.
"""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kpi_agent.scripts.xuat_duong_get import get_routes

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx",
          ROOT / "kpi_agent/data/ma_che_ten.json", ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json", ROOT / "kpi_agent/data/chi_tieu_meat_3110.json",
          ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
LEAKS = (str(Path.home()), "/home/", "/tmp/", "Errno", "Traceback", "No such file")


def _clean(text: str) -> bool:
    return not [mark for mark in LEAKS if mark in text]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(tmp_path_factory.mktemp("store") / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


def test_missing_spice_baseline_names_the_relative_file_and_system_route_is_409(client, monkeypatch):
    from kpi_agent.backend import main, spice_loyalty
    real_rules = spice_loyalty.load_rules
    missing = "sample_data/thang_10/KHONG_CO_SO_NAY.xlsx"
    monkeypatch.setattr(spice_loyalty, "load_rules", lambda path: {**real_rules(path), "baseline": {**real_rules(path)["baseline"], "file": missing}})
    monkeypatch.setattr(client.app.state, "spice", None)                 # tính lại (kết quả giữ đến khi bật lại)
    page = client.get("/api/spice-loyalty")
    assert page.status_code == 200 and page.json()["refused"] == f"1 % gia vị không tính được — thiếu file {missing}"   # trang: 200 + lời (trang chỉ hiện lời khi 200)
    system = client.get("/api/system/spice-loyalty", headers=KEY)
    assert system.status_code == 409 and system.json() == {"refused": page.json()["refused"]}    # bên gọi bằng máy: 409, như /api/system/extra-bonus
    assert _clean(page.text) and _clean(system.text)
    monkeypatch.setattr(client.app.state, "spice", None)                 # trả lại để test sau tính lại bằng luật thật
    assert main._shown_path(ROOT / missing) == missing and main._shown_path("/noi/khac/ngoai/repo/file.json") == "file.json"


def test_missing_extra_bonus_targets_refusal_has_no_machine_path(client, monkeypatch):
    from kpi_agent.core import compute_extra_bonus
    real = compute_extra_bonus.load_extra_rules
    def broken(path):
        rules = real(path)
        first = next(item_id for item_id, item in rules["items"].items() if item.get("targets_file"))
        return {**rules, "items": {**rules["items"], first: {**rules["items"][first], "targets_file": "kpi_agent/data/khong_co_file_nay.json"}}}
    monkeypatch.setattr(compute_extra_bonus, "load_extra_rules", broken)
    monkeypatch.setattr(client.app.state, "extra_bonus", None)
    system = client.get("/api/system/extra-bonus", headers=KEY)
    tool = client.post("/api/tool/get_extra_bonus", json={"args": {}})
    assert system.status_code == 409 and "kpi_agent/data/khong_co_file_nay.json" in system.json()["refused"]
    assert "refused" in tool.json() and _clean(system.text) and _clean(tool.text)
    monkeypatch.setattr(client.app.state, "extra_bonus", None)


def test_refusals_of_every_listed_get_route_have_no_machine_path(client):
    # Mọi đường trong danh sách GET, với giá trị SAI kiểu người / máy hay gửi ⇒ lời trả (từ chối hay lỗi) không lộ đường dẫn / chuỗi lỗi hệ thống
    bad = {"employee_id": "NV99", "employee_a": "NV99", "employee_b": "../../etc/passwd", "kpi": "KHONG_CO", "period": "1999-01", "run_id": "999999",
           "extra": "abc", "target_pct": "abc"}
    seen = 0
    for entry in get_routes():
        path = entry["path"]
        for name in entry.get("doan", {}):
            path = path.replace("{" + name + "}", bad[name])
        response = client.get(path, params={name: bad[name] for name in entry["tham_so"]})
        assert response.status_code < 500 and _clean(response.text), (entry["path"], response.status_code)
        seen += 1
    assert seen >= 15


def test_missing_mask_file_stops_startup_with_a_plain_message(tmp_path, monkeypatch):
    from kpi_agent.backend import main
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    monkeypatch.setattr(main, "MASK", tmp_path / "ma_che_ten.json")      # file không tồn tại
    with pytest.raises(RuntimeError) as stopped:
        with TestClient(main.app, base_url="http://127.0.0.1:8110"):
            pass
    assert str(stopped.value) == "backend KHÔNG bật: thiếu ma_che_ten.json" and _clean(str(stopped.value))
    monkeypatch.setattr(main, "MASK", ROOT / "kpi_agent/data/ma_che_ten.json")
    assert main._shown_path(main.MASK) == "kpi_agent/data/ma_che_ten.json"
