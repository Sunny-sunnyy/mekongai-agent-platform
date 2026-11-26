"""KỲ TRỐNG (20/11/2026) — backend bật khi CHƯA có file dữ liệu nào của kỳ; người dùng tải file đơn hàng + file chỉ tiêu + sổ theo dõi ⇒ bản đầu.

Vì sao: phép thử "tháng 10 chỉ bằng tải lên" kẹt ở chỗ backend chỉ bật được khi đã gắn sẵn bộ file tháng 10 ⇒ tải đúng các file đó chỉ nhận "đã có
sẵn". Ba ca, đều đi đúng đường người dùng (cửa tải lên qua HTTP, dữ liệu thật tháng 10):
 1. bật trống ⇒ hỏi số ⇒ 409 có lời ⇒ tải ba file (sổ trước, đơn hàng sau cùng) ⇒ 152.360.000 đ, 112 ô bằng bản bật theo đường cũ ⇒ bật lại vẫn còn;
 2. cùng ba file theo thứ tự khác ⇒ cùng kết quả;
 3. mới có hai file rồi bật lại ⇒ kỳ vẫn trống, không số, không 500.
"""
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
BOOK = ROOT / "sample_data/thang_10/Tracking.xlsx"
NEEDED = [RAW, TARGETS, BOOK, ROOT / "kpi_agent/data/ma_che_ten.json", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
BIN = {**KEY, "Content-Type": "application/octet-stream"}
FILES = {"raw": (RAW, {"ngay_chot": "2026-10-31"}), "chi_tieu": (TARGETS, {}), "so_tracking": (BOOK, {})}
TEAM_TOTAL = 152_360_000          # tổng lương kinh doanh 8 KPI tháng 10 — con số đích
EXTRA_TOTAL = 19_026_300          # thưởng thêm (cần chỉ tiêu Kokomi / Meat từ trang theo dõi của đội)
SPICE_TOTAL = 11_868_854          # 1 % gia vị (nền đọc từ sổ theo dõi)
PAGE = ROOT / "sample_data/thang_10/saved_resource.html"


@contextmanager
def backend(folder: Path, empty: bool = True):
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_UPLOAD_DIR", str(folder / "uploads"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        if empty:
            mp.setenv("KPI_EMPTY_PERIOD", "2026-10")
        else:
            mp.delenv("KPI_EMPTY_PERIOD", raising=False)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110", raise_server_exceptions=False) as client:
            yield client


def send(client, kind: str):
    path, extra = FILES[kind]
    return client.post("/api/system/upload", params={"loai": kind, "ky": "2026-10", **extra}, content=path.read_bytes(), headers=BIN)


def cells(client) -> list:
    """112 ô (nhân viên × KPI) của bảng thưởng đang dùng — đúng dữ liệu khối xuất file dùng."""
    table = client.get("/api/system/bonus-table", headers=KEY).json()["table"]
    assert table["team_total"] == TEAM_TOTAL and len(table["lines"]) == 112
    return table["lines"]


@pytest.fixture(scope="module")
def old_way_cells(tmp_path_factory):
    """Đường CŨ (bật với file gắn sẵn trong workflow): không đổi — vẫn 152.360.000 đ; 112 ô của nó là mốc so cho bản dựng từ file tải lên."""
    with backend(tmp_path_factory.mktemp("duong_cu"), empty=False) as client:
        assert client.get("/api/team-bonus").json()["team_total"] == TEAM_TOTAL
        return cells(client)


def assert_no_numbers_yet(client, missing: list[str]):
    # đường không mang số vẫn trả 200 để bên dựng hộp biết backend đã bật: meta tối thiểu (không ngày chốt), danh sách công cụ
    meta = client.get("/api/meta").json()
    assert meta["trang_thai"] == "cho_du_file" and meta["period"] == "2026-10" and "cutoff" not in meta and meta["latest_data_version"] is None
    assert len(client.get("/api/tools").json()["tools"]) > 5
    for path in ("/api/team-bonus", "/api/employees/NV01", "/api/system/bonus-table", "/api/export/xlsx", "/api/spice-loyalty"):
        response = client.get(path, headers=KEY)
        assert response.status_code == 409, (path, response.status_code)
        body = response.json()
        assert body["code"] == "ky_chua_co_du_lieu" and "chưa có dữ liệu" in body["refused"] and "team_total" not in body, path
    status = client.get("/api/system/ky/2026-10", headers=KEY).json()
    assert status["ready"] is False and status["con_thieu"] == missing
    # cùng hình items[] với kỳ đã có bản — trang đọc danh sách này để vẽ "tháng này đã có gì"
    states = {item["key"]: item["state"] for item in status["items"]}
    wanted = {"raw": "raw", "chi_tieu": "chi_tieu", "tuyen": "so_tracking"}
    assert {key: states[key] for key in wanted} == {key: "thieu" if kind in missing else "co" for key, kind in wanted.items()}
    assert states["luat_chinh"] == "co" and status["latest_data_version"] is None
    assert client.get("/api/system/ky", headers=KEY).json()["periods"] == [{"period": "2026-10", "ready": False}]


def test_empty_period_then_three_uploads_make_the_first_version_and_it_survives_restart(tmp_path, old_way_cells):
    with backend(tmp_path) as client:
        assert_no_numbers_yet(client, ["raw", "chi_tieu", "so_tracking"])
        waiting = send(client, "so_tracking")                               # sổ trước — thứ tự "ngược" với thói quen
        assert waiting.status_code == 200 and waiting.json()["trang_thai"] == "cho_du_file" and "data_version" not in waiting.json(), waiting.text[:300]
        assert "còn thiếu" in waiting.json()["loi"] and waiting.json()["con_thieu"] == ["raw", "chi_tieu"]
        assert send(client, "chi_tieu").json()["con_thieu"] == ["raw"]
        assert_no_numbers_yet(client, ["raw"])
        first = send(client, "raw")
        assert first.status_code == 200, first.text[:400]
        body = first.json()
        assert body["ban_dau_cua_ky"] is True and body["team_total"] == TEAM_TOTAL and body["period"] == "2026-10" and body["cutoff"] == "2026-10-31"
        version = body["data_version"]
        assert cells(client) == old_way_cells                               # từng ô bằng bản bật theo đường cũ
        assert client.get("/api/meta").json()["data_version"] == version
        assert client.get("/api/system/ky", headers=KEY).json()["periods"] == [{"period": "2026-10", "ready": True}]
        # thưởng thêm chưa có nguồn tải lên (trang chỉ tiêu): nói rõ thiếu gì, KHÔNG đọc file tháng 10 gắn sẵn; 1 % gia vị đọc nền từ CHÍNH sổ vừa tải
        assert body["thuong_them"] == "khong_tinh_duoc" and "Kokomi" in body["thuong_them_ly_do"]
        assert client.get("/api/spice-loyalty").json()["team_reward"] == SPICE_TOTAL
        # tải lại đúng file đơn hàng ⇒ "đã có sẵn" lúc này mới đúng nghĩa
        assert send(client, "raw").json()["da_co_san"] is True
    with backend(tmp_path) as client:                                       # bật lại: bản đầu được dựng lại từ kho, không cần ai tải lại
        assert client.get("/api/meta").json()["data_version"] == version
        assert cells(client) == old_way_cells


def test_other_upload_order_gives_the_same_first_version(tmp_path, old_way_cells):
    with backend(tmp_path) as client:
        assert send(client, "raw").json()["con_thieu"] == ["chi_tieu", "so_tracking"]
        assert send(client, "so_tracking").json()["con_thieu"] == ["chi_tieu"]
        first = send(client, "chi_tieu")
        assert first.status_code == 200 and first.json()["team_total"] == TEAM_TOTAL, first.text[:400]
        assert cells(client) == old_way_cells


def test_two_of_three_files_keep_the_period_empty_across_restart(tmp_path):
    with backend(tmp_path) as client:
        assert send(client, "raw").status_code == 200 and send(client, "chi_tieu").status_code == 200
        assert_no_numbers_yet(client, ["so_tracking"])
    with backend(tmp_path) as client:
        assert_no_numbers_yet(client, ["so_tracking"])
        other = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-11"}, content=TARGETS.read_bytes(), headers=BIN)
        assert other.status_code == 409 and other.json()["code"] == "khac_ky_dang_mo"       # kỳ khác: từ chối có lời, không 500


def three_totals(client, version: str) -> tuple[int, int, int]:
    params = {"data_version": version}
    table = client.get("/api/system/bonus-table", params=params, headers=KEY).json()["table"]
    extra = client.get("/api/system/extra-bonus", params=params, headers=KEY).json()
    spice = client.get("/api/system/spice-loyalty", params=params, headers=KEY).json()
    return table["team_total"], sum(line["amount"] for line in extra["lines"]), spice["team_reward"]


@pytest.mark.skipif(not PAGE.exists(), reason="không có trang theo dõi tháng 10 trên máy này")
def test_october_only_by_uploads_gives_all_three_totals_and_survives_restart(tmp_path):
    """ĐÍCH: tháng 10 đi trọn đường "chỉ tải lên" — bật trống ⇒ sổ + chỉ tiêu + đơn hàng ⇒ trang .html ⇒ ba tổng ⇒ bật lại vẫn còn."""
    with backend(tmp_path) as client:
        for kind in ("so_tracking", "chi_tieu", "raw"):
            assert send(client, kind).status_code == 200
        page = client.post("/api/system/upload", params={"loai": "trang_chi_tieu", "ky": "2026-10"}, content=PAGE.read_bytes(), headers=BIN)
        assert page.status_code == 200 and page.json()["thuong_them"] == "co", page.text[:400]
        version = page.json()["data_version"]
        assert three_totals(client, version) == (TEAM_TOTAL, EXTRA_TOTAL, SPICE_TOTAL)
        # hỏi KHÔNG kèm mã bản ⇒ bản mới nhất của kỳ (có thưởng thêm), không phải bản đầu; mã bản mới nhất có ở đường "kỳ này có gì"
        extra = client.get("/api/system/extra-bonus", headers=KEY).json()
        assert sum(line["amount"] for line in extra["lines"]) == EXTRA_TOTAL and client.get("/api/meta").json()["data_version"] == version
        assert client.get("/api/system/ky/2026-10", headers=KEY).json()["latest_data_version"] == version
        # trang Dữ liệu chỉ gọi MỘT đường: /ky/<kỳ>?data_version= trả đúng ba tổng + trạng thái từng khoản của bản đó
        status = client.get("/api/system/ky/2026-10", params={"data_version": version}, headers=KEY).json()
        assert (status["data_version"], status["tam_kpi"]["tong"], status["thuong_them"]["tong"], status["gia_vi"]["tong"]) == \
            (version, TEAM_TOTAL, EXTRA_TOTAL, SPICE_TOTAL) and (status["thuong_them"]["trang_thai"], status["gia_vi"]["trang_thai"]) == ("co", "co")
    with backend(tmp_path) as client:
        assert three_totals(client, version) == (TEAM_TOTAL, EXTRA_TOTAL, SPICE_TOTAL)
        assert client.get("/api/meta").json()["data_version"] == version
