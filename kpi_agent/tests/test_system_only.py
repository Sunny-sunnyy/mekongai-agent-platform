"""Cờ KPI_SYSTEM_ONLY: hộp trong bộ Capitol chỉ mở đường hệ thống; tắt ⇒ y như cũ; giá trị lạ ⇒ không bật.

Backend chạy thật (TestClient, dữ liệu thật, KPI_STORE tạm). Danh sách đường bị đóng lấy từ CHÍNH `app.routes` ⇒ đường mới thêm sau này
(vd /api/names) tự vào phép kiểm, không phải nhớ sửa test."""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kpi_agent.backend import main

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
BASE = "http://127.0.0.1:8110"
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"   # khoá giả chỉ sống trong test — không phải khoá thật
KEY = {"X-Kpi-Sys-Key": SYS_KEY}


@pytest.fixture
def start(tmp_path, monkeypatch):
    """Bật backend với giá trị cờ cho trước (None = không đặt biến)."""
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)

    def boot(flag):
        if flag is None:
            monkeypatch.delenv("KPI_SYSTEM_ONLY", raising=False)
        else:
            monkeypatch.setenv("KPI_SYSTEM_ONLY", flag)
        return TestClient(main.app, base_url=BASE)
    return boot


def _closed_routes():
    """(method, đường đã điền tham số) của mọi đường KHÔNG thuộc danh sách cho phép."""
    out = []
    for route in main.app.routes:
        path = route.path.replace("{employee_id:path}", "NV05").replace("{kpi}", "PC_BT").replace("{run_id}", "1").replace("{version_id}", "1")
        if path in main.SYSTEM_ONLY_PATHS or path.startswith(main.SYSTEM_ONLY_PREFIXES):
            continue
        out += [(method, path) for method in sorted(route.methods - {"HEAD", "OPTIONS"})]
    return out


def test_flag_on_closes_every_page_route_and_keeps_system_routes(start):
    with start(None) as off:
        board_off = off.get("/api/team-board").json()
    closed = _closed_routes()
    # đủ các đường: bảng đội, "Đã soát" /review, tab Theo dõi, 1 % gia vị bản không khoá
    assert {("GET", "/api/team-board"), ("POST", "/api/runs/1/review"), ("GET", "/api/tracking"), ("GET", "/api/spice-loyalty")} <= set(closed)
    with start("1") as on:
        for method, path in closed:
            response = on.request(method, path, headers=KEY)   # có khoá đúng cũng không mở
            assert (response.status_code, response.json()) == (404, {"detail": "Not Found"}), (method, path)
        assert on.get("/api/khong-co-duong-nay").json() == {"detail": "Not Found"}   # đường đóng trả y như đường không tồn tại
        # sai phương thức / thân hỏng trên đường đóng vẫn 404 (không 405 / 422 — không lộ là đường có thật)
        assert on.post("/api/team-board", content=b"{", headers={"content-type": "application/json"}).status_code == 404
        assert on.delete("/api/runs/1/review").status_code == 404
        # các đường cho phép vẫn chạy
        assert on.get("/api/meta").json()["period"] == "2026-10"
        assert len(on.get("/api/tools").json()["tools"]) >= 10
        tool = on.post("/api/tool/get_team_bonus_table", json={"args": {}}).json()
        assert tool["team_total"] == board_off["team_total"]
        assert on.get("/api/system/bonus-table").status_code == 401                  # đường hệ thống vẫn đòi khoá
        assert on.get("/api/system/bonus-table", headers=KEY).json()["table"]["team_total"] == board_off["team_total"]
        assert on.get("/api/system/team-board").status_code == 401
        assert on.get("/api/system/team-board", headers=KEY).json() == board_off     # cùng bảng đội như đường của trang
        assert on.get("/api/system/team-board", headers=KEY, params={"data_version": "khong-co"}).status_code == 404
        # tải file: khi cờ bật không còn đường tải không khoá
        assert on.get("/api/export/xlsx").status_code == 401
        assert on.get("/api/export/xlsx", headers=KEY).status_code == 409            # có khoá ⇒ đường Capitol: chưa duyệt ⇒ 409


@pytest.mark.parametrize("flag", [None, "0"])
def test_flag_off_changes_nothing(start, flag):
    with start(flag) as client:
        assert client.get("/api/team-board").status_code == 200
        assert client.get("/api/runs").status_code == 200
        response = client.get("/api/export/xlsx")                                     # đường tải của trang: chưa soát ⇒ 409 (không phải 401)
        assert response.status_code == 409 and "chưa duyệt" in response.json()["refused"]


@pytest.mark.parametrize("flag", ["", "true", "on", "2", " 1", "yes"])
def test_strange_flag_value_stops_startup(start, flag):
    with pytest.raises(RuntimeError, match="KPI_SYSTEM_ONLY"):
        with start(flag):
            pass


@pytest.mark.skipif(not RAW.exists(), reason="không có RAW DATA trên máy này")
def test_real_raw_3110_system_routes(start, monkeypatch):
    # Ca thật đường RAW 31/10, cờ bật: số của 2 đường hệ thống mới = số đã chốt (8 KPI 152.360.000 (mốc 7 theo trang sếp 16/11; trước 126.660.000); 1 % gia vị 11.868.854)
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 31))
    monkeypatch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    with start("1") as on:
        assert on.get("/api/spice-loyalty").status_code == 404
        assert on.get("/api/system/spice-loyalty").status_code == 401
        board = on.get("/api/system/team-board", headers=KEY).json()
        assert board["team_total"] == 152_360_000 and len(board["employees"]) == 14
        spice = on.get("/api/system/spice-loyalty", headers=KEY).json()
        assert spice["team_reward"] == 11_868_854 and spice["team_customers"] == 1351
        # mã bản KHÔNG có ⇒ 404 như mọi đường hệ thống, không trả nhầm số bản lúc bật (2a-2 nấc 1: bản tải lên nay có số gia vị của chính nó nên
        # lời cũ 409 "chỉ có cho bản dữ liệu lúc bật" không còn đúng; ca "bản tải lên trả số của nó" ở test_gia_vi_ban_tai_len.py)
        other = on.get("/api/system/spice-loyalty", headers=KEY, params={"data_version": "a" * 64})
        assert other.status_code == 404 and "team_reward" not in other.json()
