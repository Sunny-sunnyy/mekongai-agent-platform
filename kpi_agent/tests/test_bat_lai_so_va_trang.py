"""Backend BẬT LẠI ⇒ bản lấy tuyến từ sổ theo dõi và bản có chỉ tiêu riêng từ trang .html vẫn còn nguyên (nối 2a-1 + nấc 3 với phần nạp lại 2j).

Người dùng thật gặp ngay lần khởi động máy đầu tiên: tải sổ + trang hôm nay, mai bật máy lên hỏi lại đúng mã bản đó phải ra đúng số, vẫn tuyến từ sổ,
vẫn chỉ tiêu từ trang, vẫn mang hai dòng lưu ý. Trang không được lưu ⇒ lúc bật dựng lại từ file chỉ tiêu ĐÃ TÁCH.
"""
import pytest

from test_nap_lai_ban_da_tai import BIN, KEY, backend
from test_page_targets import PAGE, needs_data
from test_tuyen_duong_ghep import BOOK

pytestmark = needs_data


def _upload(client, kind: str, body: bytes, **params):
    return client.post("/api/system/upload", params={"loai": kind, "ky": "2026-10", **params}, content=body, headers=BIN)


def _money(client, version: str) -> dict:
    extra = client.get("/api/system/extra-bonus", params={"data_version": version}, headers=KEY)
    table = client.get("/api/system/bonus-table", params={"data_version": version}, headers=KEY)
    meta = client.get("/api/meta", params={"data_version": version})
    assert extra.status_code == table.status_code == meta.status_code == 200, (extra.text[:200], table.text[:200], meta.text[:200])
    return {"extra": extra.json()["lines"], "table": {key: value for key, value in table.json()["table"].items() if key != "source"},
            "flags": meta.json()["data_flags"], "extra_files": meta.json()["extra_files_sha256"]}


def test_book_and_page_versions_survive_a_restart(tmp_path):
    from kpi_agent.backend import main
    with backend(tmp_path) as client:
        book = _upload(client, "so_tracking", BOOK.read_bytes())
        assert book.status_code == 200, book.text[:300]
        page = _upload(client, "trang_chi_tieu", PAGE.read_bytes(), data_version=book.json()["data_version"])
        assert page.status_code == 200, page.text[:300]
        book_version, page_version = book.json()["data_version"], page.json()["data_version"]
        before = _money(client, page_version)
        assert sum(line["amount"] for line in before["extra"]) == 19_026_300 and before["table"]["team_total"] == 152_360_000
    with backend(tmp_path) as client:                                    # BẬT LẠI trên cùng kho + cùng thư mục tải lên
        report = client.app.state.restore_report
        assert report["restored"] == 2 and report["skipped"] == [] and report["changed"] == [], report
        assert _money(client, page_version) == before                    # cùng mã bản ⇒ cùng tiền, cùng lưu ý, cùng mã băm file phụ
        assert any("lấy từ trang theo dõi tải lên" in flag for flag in before["flags"]) and any("lấy từ sổ theo dõi tải lên" in flag for flag in before["flags"])
        state = client.app.state.versions[page_version]
        assert main.runner.node_param(state.workflow, "read_raw", "mcp_ghep") is not None and state.targets_override   # vẫn tuyến từ sổ + chỉ tiêu từ trang
        assert client.get("/api/system/extra-bonus", params={"data_version": book_version}, headers=KEY).status_code == 200
        kept = [path.name for path in client.app.state.upload_dir.rglob("*") if path.is_file()]
        assert not any(name.endswith((".html", ".htm")) for name in kept), kept
        # mất file chỉ tiêu đã tách ⇒ bản có trang không nạp lại được, có lời; backend vẫn bật, bản từ sổ vẫn còn
        for path in (client.app.state.upload_dir / "chi_tieu_rieng").glob("*.json"):
            path.unlink()
    with backend(tmp_path) as client:
        report = client.app.state.restore_report
        assert report["restored"] == 1 and [item["code"] for item in report["skipped"]] == ["file_mat"], report
        assert client.get("/api/meta", params={"data_version": page_version}).status_code == 404
        assert client.get("/api/meta", params={"data_version": book_version}).status_code == 200
