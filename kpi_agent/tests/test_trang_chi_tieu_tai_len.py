"""2a-2 nấc 3 — loại tải lên `trang_chi_tieu` QUA CỬA /api/system/upload: trang theo dõi .html ⇒ chỉ tiêu riêng Kokomi / Meat của bản dữ liệu.

Backend thật đường file đơn hàng (luật tháng 10 thật, ngày chốt 31/10) + trang thật tháng 10. Phần tách (mã lỗi từng kiểu lệch khuôn) ở test_page_targets.py.
"""
from datetime import date
from pathlib import Path

import pytest

from kpi_agent.backend import page_targets as pt
from test_page_targets import OUTER_PAGE, PAGE, RAW, TARGETS, needs_data, real, rebuilt   # noqa: F401 — `real` là fixture dùng chung

ROOT = Path(__file__).resolve().parents[2]
SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
BIN = {**KEY, "Content-Type": "application/octet-stream"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    folder = tmp_path_factory.mktemp("trang")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        # bộ test chung đặt sẵn biến KPI_UPLOAD_DIR (một thư mục cho mọi file test) ⇒ phải đặt ĐÈ biến; chỉ sửa hằng UPLOAD_DIR thì khi chạy cả lô
        # thư mục vẫn là thư mục chung và phép đếm "chỉ còn file .json đã tách" đếm nhầm file của test khác (lượt nặng fd313773: 1 ca đỏ vì thế)
        mp.setenv("KPI_UPLOAD_DIR", str(folder / "uploads"))
        mp.setattr(main, "UPLOAD_DIR", folder / "uploads")
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


def _upload(client, body: bytes, **params):
    return client.post("/api/system/upload", params={"loai": "trang_chi_tieu", "ky": "2026-10", **params}, content=body, headers=BIN)


def _extra(client, version: str | None = None) -> dict:
    reply = client.get("/api/system/extra-bonus", params={"data_version": version} if version else {}, headers=KEY)
    assert reply.status_code == 200, reply.text[:200]
    return reply.json()


def _table(client, version: str | None = None) -> dict:
    reply = client.get("/api/system/bonus-table", params={"data_version": version} if version else {}, headers=KEY)
    assert reply.status_code == 200, reply.text[:200]
    return {key: value for key, value in reply.json()["table"].items() if key != "source"}


def _kept_files(client) -> list[str]:
    folder = client.app.state.upload_dir
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file()) if folder.exists() else []


@needs_data
def test_october_page_gives_a_version_with_the_same_money_and_keeps_no_page(client, real):
    boot = client.get("/api/meta").json()
    base_extra, base_table = _extra(client), _table(client)
    reply = _upload(client, real["body"])
    assert reply.status_code == 200, reply.text[:300]
    body = reply.json()
    assert body["chi_tieu_rieng"] == "tu_trang" and body["tu_trang"]["so_khoan"] == 5 and body["tu_trang"]["so_nguoi"] == 14
    assert body["data_version"] != boot["data_version"] and body["cutoff"] == boot["cutoff"] and body["thuong_them"] == "co"
    new_extra = _extra(client, body["data_version"])
    money = lambda extra: sum(line["amount"] for line in extra["lines"])
    assert new_extra["lines"] == base_extra["lines"] and money(new_extra) == money(base_extra) == 19_026_300        # từng dòng thưởng thêm giống bản lúc bật
    assert _table(client, body["data_version"]) == base_table and body["team_total"] == 152_360_000                  # 112 ô 8 KPI không đổi
    assert any("lấy từ trang theo dõi tải lên" in flag for flag in body["data_flags"])                             # lưu ý đi theo bản
    assert not any("lấy từ trang theo dõi tải lên" in flag for flag in boot["data_flags"])
    # mã bản đổi vì hai file chỉ tiêu riêng đổi; dấu vân tay 8 KPI (files_sha256) không đổi
    meta = client.get("/api/meta", params={"data_version": body["data_version"]}).json()
    assert meta["files_sha256"] == boot["files_sha256"] and set(meta["extra_files_sha256"]) == set(boot["extra_files_sha256"])
    differing = sorted(name for name in meta["extra_files_sha256"] if meta["extra_files_sha256"][name] != boot["extra_files_sha256"][name])
    assert len(differing) == 2 and all("chi_tieu" in name for name in differing)
    # trang KHÔNG được lưu: chỉ có ba file .json đã tách (hai file lõi đọc + một file nguồn cho việc nạp lại lúc bật); không file nào chứa khoá đáp án
    kept = _kept_files(client)
    assert len(kept) == 3 and all(name.startswith("chi_tieu_rieng/") and name.endswith(".json") for name in kept), kept
    for name in kept:
        text = (client.app.state.upload_dir / name).read_text(encoding="utf-8")
        assert set(__import__("json").loads(text)) in ({"unit", "targets"}, {"kokomi", "meat"}) and "upd" not in text
    assert client.get("/api/meta").json()["data_version"] == boot["data_version"]                                   # bản lúc bật không bị thay
    again = _upload(client, real["body"]).json()
    assert again["data_version"] == body["data_version"] and again["da_co_san"] is True and _kept_files(client) == kept


@needs_data
def test_page_with_changed_answer_cells_is_the_same_version_and_a_changed_target_is_a_new_one(client, real):
    original = _upload(client, real["body"]).json()["data_version"]

    def scramble(data):
        for item in data["bon"]:
            for row in item["rows"]:
                for key in ("a", "p", "k", "d", "m"):
                    row[key] = 987654.321
        data["upd"] = "05/10/2026"
        data.pop("kpi"), data.pop("ct"), data.pop("hv")
    same = _upload(client, rebuilt(real, scramble))
    assert same.status_code == 200 and same.json()["data_version"] == original and same.json()["da_co_san"] is True

    def lower_one_target(data):
        row = next(item for item in data["bon"] if item["short"] == "Mì Kokomi 90")["rows"][0]
        row["t"] = 0.001                                         # chỉ tiêu Kokomi của MỘT người còn 1.000 đ ⇒ người đó vượt chỉ tiêu
    changed = _upload(client, rebuilt(real, lower_one_target))
    assert changed.status_code == 200 and changed.json()["data_version"] != original
    before, after = _extra(client, original)["lines"], _extra(client, changed.json()["data_version"])["lines"]
    moved = [(old, new) for old, new in zip(before, after) if old != new]
    assert len(before) == len(after) and len({new["employee_id"] for _old, new in moved}) == 1, moved[:3]            # đúng một người đổi dòng
    assert _table(client, changed.json()["data_version"]) == _table(client)                                           # 8 KPI không dính


@needs_data
def test_wrong_pages_are_refused_with_codes_and_leave_nothing_behind(client, real):
    _upload(client, real["body"])
    versions, kept = set(client.app.state.versions), _kept_files(client)
    planted = "<script>alert(1)</script>\x07Nguyễn Văn Lạ"

    def plant(data):
        next(item for item in data["bon"] if item["short"] == "Meat N1")["rows"][2]["n"] = planted
    cases = [(OUTER_PAGE.read_bytes(), {}, 422, "trang_ngoai_khong_co_du_lieu"),
             (rebuilt(real, lambda data: data.update(upd="30/11/2026")), {}, 422, "trang_khac_ky"),
             (rebuilt(real, lambda data: data.update(upd="hôm qua")), {}, 422, "trang_khong_ro_ky"),
             (real["body"], {"ky": "2026-11"}, 409, "chua_co_don_hang_cua_ky"),
             (RAW.read_bytes()[:300_000], {}, 422, "trang_lech_khuon"),
             (rebuilt(real, lambda data: data.pop("bon")), {}, 422, "trang_lech_khuon"),
             (rebuilt(real, plant), {}, 422, "trang_lech_doi"),
             (b"x" * (pt.MAX_PAGE_BYTES + 1), {}, 413, "trang_qua_lon")]
    real_names = {row["n"] for item in real["data"]["bon"] for row in item["rows"]}
    for body, params, status, code in cases:
        reply = _upload(client, body, **params)
        assert (reply.status_code, reply.json().get("code")) == (status, code), (code, reply.text[:200])
        assert not any(piece in reply.text for piece in ("script", "alert", "\\u0007", "Lạ")) and not any(name in reply.text for name in real_names), code
    assert set(client.app.state.versions) == versions and _kept_files(client) == kept          # không bản mới, không file nào thêm (kể cả trang)
    assert _upload(client, real["body"], ngay_chot="2026-11-05").status_code == 422            # ngày chốt ngoài kỳ


@needs_data
def test_version_built_on_a_page_version_keeps_the_page_targets(client, real):
    page_version = _upload(client, real["body"]).json()["data_version"]
    onto = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-10", "data_version": page_version}, content=TARGETS.read_bytes(),
                       headers=BIN)
    assert onto.status_code == 200, onto.text[:300]
    assert onto.json()["chi_tieu_rieng"] == "tu_trang" and any("lấy từ trang theo dõi tải lên" in flag for flag in onto.json()["data_flags"])
    assert _extra(client, onto.json()["data_version"])["lines"] == _extra(client, page_version)["lines"]
    plain = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-10"}, content=TARGETS.read_bytes(), headers=BIN).json()
    assert plain["chi_tieu_rieng"] == "file_co_dinh"                                           # bản không có trang vẫn dùng file cố định
