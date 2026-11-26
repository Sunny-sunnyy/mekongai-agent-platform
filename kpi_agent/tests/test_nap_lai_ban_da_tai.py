"""Backend bật lại ⇒ nạp lại các bản dữ liệu đã tải lên (2j, 20/11/2026) — từ thư mục tải lên + bảng `uploaded_version` trong kho bản tải lên.

Ca thật (dữ liệu thật tháng 10, một kho + một thư mục tải lên dùng qua NHIỀU lần bật): tải file đơn hàng cắt tới 20/10, rồi tải file chỉ tiêu CHỒNG lên
bản đó ⇒ tắt ⇒ bật lại (lần này bật cả cờ KPI_SYSTEM_ONLY) ⇒ `?data_version=` cũ trả ĐÚNG bản (cùng mã, cùng tổng đội, cùng ngày chốt, giờ tải giữ nguyên).
Ca xấu: file trong thư mục tải lên bị sửa 1 byte ⇒ không nạp, backend vẫn bật, có lời; file bị xoá ⇒ như trên. Các lần bật chạy NỐI TIẾP trong một ca
để chỉ dựng file thử một lần (mỗi lần bật đọc lại file đơn hàng thật).
"""
import io
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
NEEDED = [RAW, TARGETS, ROOT / "kpi_agent/data/ma_che_ten.json", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json",
          ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
BIN = {**KEY, "Content-Type": "application/octet-stream"}


@contextmanager
def backend(folder: Path, system_only: bool = False, restore: str | None = None):
    """Một lần BẬT backend trên cùng kho + cùng thư mục tải lên trong `folder`; ra khỏi khối = tắt."""
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        if system_only:
            mp.setenv("KPI_SYSTEM_ONLY", "1")
        else:
            mp.delenv("KPI_SYSTEM_ONLY", raising=False)
        if restore is None:
            mp.delenv("KPI_RESTORE_UPLOADS", raising=False)
        else:
            mp.setenv("KPI_RESTORE_UPLOADS", restore)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        mp.setenv("KPI_UPLOAD_DIR", str(folder / "uploads"))   # bộ test chung đã đặt biến này ⇒ phải đặt đè, sửa hằng UPLOAD_DIR không có tác dụng
        started = time.monotonic()
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
            # số đo cho báo cáo (chạy với -s để thấy): bật có nạp lại bản tải lên chậm hơn bật thường bao nhiêu
            print(f"[bật backend] {time.monotonic() - started:.1f} giây · nạp lại: {client.app.state.restore_report['restored']} bản")
            yield client


def _raw_until_20() -> bytes:
    source = openpyxl.load_workbook(RAW, read_only=True, data_only=True)
    rows = source["Export"].iter_rows(values_only=True)
    header = next(rows)
    at = header.index("ORDER DATE")
    target = openpyxl.Workbook(write_only=True)
    sheet = target.create_sheet("Export")
    sheet.append(header)
    for row in rows:
        if len(row) > at and isinstance(row[at], datetime) and row[at].date() <= date(2026, 10, 20):
            sheet.append(row)
    out = io.BytesIO()
    target.save(out)
    source.close()
    return out.getvalue()


def _board(client, version: str) -> dict:
    response = client.get("/api/system/team-board", params={"data_version": version}, headers=KEY)
    assert response.status_code == 200, response.text[:300]
    return response.json()


def _raw_item(client) -> dict:
    return client.get("/api/system/ky/2026-10", headers=KEY).json()["items"][0]


def test_uploaded_versions_come_back_after_restart_and_damaged_files_do_not(tmp_path):
    # ---- lần bật 1: tải file đơn hàng (bản A), rồi file chỉ tiêu chồng lên bản A (bản B)
    with backend(tmp_path) as client:
        boot_version = client.get("/api/meta").json()["data_version"]
        boot_total = _board(client, boot_version)["team_total"]
        sent = client.post("/api/system/upload", params={"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-20"}, content=_raw_until_20(), headers=BIN)
        assert sent.status_code == 200, sent.text[:300]
        version_a, total_a = sent.json()["data_version"], sent.json()["team_total"]
        stacked = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-10", "data_version": version_a},
                              content=TARGETS.read_bytes(), headers=BIN)
        assert stacked.status_code == 200, stacked.text[:300]
        version_b, total_b = stacked.json()["data_version"], stacked.json()["team_total"]
        assert len({boot_version, version_a, version_b}) == 3 and total_b == total_a != boot_total   # cùng chỉ tiêu ⇒ cùng tiền, khác mã bản
        uploaded_at = _raw_item(client)["uploaded_at"]
        assert client.get("/api/system/ky", headers=KEY).json()["restored_uploads"] == {"enabled": True, "restored": 0, "changed": [], "skipped": []}
    files = sorted((tmp_path / "uploads").glob("*.xlsx"))
    assert len(files) == 2

    # ---- lần bật 2 (cờ chỉ-hệ-thống bật): cả hai bản quay lại đúng mã, đúng số, đúng thứ tự; bản lúc bật không đổi
    with backend(tmp_path, system_only=True) as client:
        # bản ĐANG DÙNG không đổi: /api/meta không kèm ?data_version= vẫn là bản lúc bật, ngày chốt lúc bật (bản tải lên chốt 20/10 chỉ hỏi qua tham số)
        assert client.get("/api/meta").json()["data_version"] == boot_version and client.get("/api/meta").json()["cutoff"] == "2026-10-31"
        assert client.get("/api/meta").json()["latest_data_version"] == version_b
        assert _board(client, boot_version)["team_total"] == boot_total
        assert _board(client, version_a)["team_total"] == total_a and _board(client, version_b)["team_total"] == total_b
        assert client.get("/api/meta", params={"data_version": version_a}).json()["cutoff"] == "2026-10-20"
        periods = client.get("/api/system/ky", headers=KEY).json()
        assert periods["restored_uploads"] == {"enabled": True, "restored": 2, "changed": [], "skipped": []} and periods["periods"] == [{"period": "2026-10", "ready": True}]
        status = client.get("/api/system/ky/2026-10", headers=KEY).json()
        assert status["uploads_survive_restart"] is True
        raw, targets = status["items"][0], status["items"][1]
        assert raw["from_upload"] is True and raw["data_version"] == version_b and raw["uploaded_at"] == uploaded_at   # giờ TẢI, không phải giờ nạp lại
        assert targets["from_upload"] is True and targets["checked"] is True
        # hai đường mới đi qua cổng chỉ-hệ-thống; đường của trang trên máy thì đóng; không khoá vẫn 401
        assert client.get("/api/team-board").status_code == 404 and client.get("/api/system/ky/2026-10").status_code == 401
        # nạp lại KHÔNG ghi thêm dòng nào ⇒ lần bật sau không nạp trùng
        from kpi_agent.backend import store
        with store.connect(tmp_path / "kpi_store_uploads.sqlite") as conn:
            assert conn.execute("SELECT COUNT(*) FROM uploaded_version").fetchone()[0] == 2

    # ---- lần bật 2b: KPI_RESTORE_UPLOADS=0 ⇒ không nạp bản nào (bật nhanh như trước), mã cũ ra 404, trang được báo thật; giá trị lạ ⇒ không bật
    with backend(tmp_path, restore="0") as client:
        assert client.get("/api/system/ky", headers=KEY).json()["restored_uploads"] == {"enabled": False, "restored": 0, "changed": [], "skipped": []}
        assert client.get("/api/system/ky/2026-10", headers=KEY).json()["uploads_survive_restart"] is False
        assert client.get("/api/system/team-board", params={"data_version": version_a}, headers=KEY).status_code == 404
    with pytest.raises(RuntimeError, match="KPI_RESTORE_UPLOADS"):
        with backend(tmp_path, restore="off"):
            pass

    # ---- lần bật 3: file đơn hàng đã tải bị sửa 1 byte ⇒ bản A không nạp; bản B (nền là A) cũng không; backend VẪN bật
    raw_file = next(path for path in files if path.read_bytes() != TARGETS.read_bytes())
    original = raw_file.read_bytes()
    raw_file.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with backend(tmp_path) as client:
        assert _board(client, boot_version)["team_total"] == boot_total
        report = client.get("/api/system/ky", headers=KEY).json()["restored_uploads"]
        assert report["restored"] == 0 and [item["file"] for item in report["skipped"]] == [raw_file.name[:8], next(p for p in files if p != raw_file).name[:8]]
        assert "đã bị đổi" in report["skipped"][0]["reason"] and "bản làm nền" in report["skipped"][1]["reason"]
        assert [item["code"] for item in report["skipped"]] == ["file_bi_doi", "nen_khong_nap"]
        for version in (version_a, version_b):
            assert client.get("/api/system/team-board", params={"data_version": version}, headers=KEY).status_code == 404
        assert _raw_item(client)["from_upload"] is False

    # ---- lần bật 4: file bị xoá hẳn ⇒ như trên, lời khác
    raw_file.unlink()
    with backend(tmp_path) as client:
        report = client.get("/api/system/ky", headers=KEY).json()["restored_uploads"]
        assert report["restored"] == 0 and "không còn" in report["skipped"][0]["reason"] and report["skipped"][0]["code"] == "file_mat"
        assert client.get("/api/meta").json()["latest_data_version"] == boot_version


def _insert_row(folder: Path, **values) -> None:
    """Chèn thẳng một dòng vào bảng nạp lại (bảng chỉ-thêm: chèn được, không sửa / xoá được) — mô phỏng dòng do bản code KHÁC ghi hoặc dòng hỏng."""
    from kpi_agent.backend import store
    row = {"data_version": "x" * 64, "kind": "raw", "period": "2026-10", "wanted_cutoff": None, "base_data_version": None,
           "file_sha256": "0" * 64, "mapping_sha": None, **values}
    with store.connect(folder / "kpi_store_uploads.sqlite") as conn:
        store.record_uploaded_version(conn, **row)


def test_bad_or_strange_rows_never_stop_startup_and_runs_are_reused(tmp_path, monkeypatch):
    """Vòng 2: MỘT dòng hỏng — kể cả lỗi lạ — chỉ làm mất bản đó; backend vẫn bật. Và nạp lại không đẻ lượt tính mới:
    bản đã soát vẫn "đã soát" sau khi bật lại, kho không phình.
    """
    from kpi_agent.backend import main, store
    uploads_db = tmp_path / "kpi_store_uploads.sqlite"
    # ---- lần bật 1: HAI bản tải lên cùng kỳ (file đơn hàng nguyên vẹn chốt 30/10 · chỉ tiêu chồng lên bản đó), soát bản THỨ HAI
    #      (kho chỉ cho soát lượt tính MỚI NHẤT của kỳ; khi nạp lại, bản thứ nhất được dựng trước ⇒ trước vòng 2 nó đẻ lượt mới, đẩy lượt đã
    #      soát thành "đã bị thay" rồi bản thứ hai lại đẻ lượt nữa ⇒ mất dấu soát)
    with backend(tmp_path) as client:
        first = client.post("/api/system/upload", params={"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-30"}, content=RAW.read_bytes(),
                            headers=BIN)
        assert first.status_code == 200, first.text[:200]
        version_1, run_1 = first.json()["data_version"], first.json()["run_id"]
        second = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-10", "data_version": version_1},
                             content=TARGETS.read_bytes(), headers=BIN)
        assert second.status_code == 200, second.text[:200]
        version_2, run_2 = second.json()["data_version"], second.json()["run_id"]
        assert len({version_1, version_2, client.get("/api/meta").json()["data_version"]}) == 3 and run_1 != run_2
    with store.connect(uploads_db) as conn:
        store.mark_reviewed(conn, run_2, "Giám sát thử")
        runs_before = conn.execute("SELECT COUNT(*) FROM bonus_run").fetchone()[0]
        lines_before = conn.execute("SELECT COUNT(*) FROM bonus_line").fetchone()[0]
    good_sha = sorted(path.stem for path in (tmp_path / "uploads").glob("*.xlsx"))[0]
    # ---- chèn các dòng hỏng: loại file lạ (bản code khác ghi) · kỳ sai dạng · ngày chốt sai dạng · mã băm trỏ ra ngoài thư mục ·
    #      kỳ đúng dạng nhưng không có luật (bị từ chối khi kiểm lại) · một dòng hợp lệ sẽ bị cho gặp LỖI LẠ
    # loại file lạ: phải là loại KHÔNG bản code nào có ("so_tracking" từng dùng ở đây nay là loại thật của nhánh tải sổ ⇒ dòng đó thành "bị từ chối")
    _insert_row(tmp_path, kind="loai_cua_ban_code_khac", file_sha256=good_sha)
    _insert_row(tmp_path, period="2026-1x", file_sha256=good_sha)
    _insert_row(tmp_path, wanted_cutoff="20/10/2026", file_sha256=good_sha)
    (tmp_path / "ngoai.xlsx").write_bytes(TARGETS.read_bytes())
    _insert_row(tmp_path, kind="chi_tieu", file_sha256="../ngoai")
    _insert_row(tmp_path, kind="chi_tieu", period="2026-09", file_sha256=good_sha)
    _insert_row(tmp_path, kind="chi_tieu", period="2026-10", file_sha256=good_sha, data_version="la" * 32)
    real_build = main._build_version

    def build_or_break(app, base, kind, period, body, cutoff):
        if app.state.restoring and app.state.restoring["data_version"] == "la" * 32:
            raise ZeroDivisionError("lỗi không ai lường trước, có chữ nhạy cảm /home/x")
        return real_build(app, base, kind, period, body, cutoff)
    monkeypatch.setattr(main, "_build_version", build_or_break)
    # ---- lần bật 2: backend BẬT; hai bản tốt quay lại; sáu dòng hỏng bị bỏ qua, mỗi dòng một mã cố định
    with backend(tmp_path) as client:
        report = client.get("/api/system/ky", headers=KEY).json()["restored_uploads"]
        assert report["restored"] == 2 and report["changed"] == []
        assert [item["code"] for item in report["skipped"]] == ["dong_sai_dang"] * 4 + ["bi_tu_choi", "loi_la"]
        assert not [item for item in report["skipped"] if "ngoai" in item["file"] or ".." in item["file"] or "/home" in item["reason"]
                    or "ZeroDivision" in item["reason"]]                         # không lặp chữ của dòng hỏng, không chữ thô của lỗi
        assert client.get("/api/meta").json()["cutoff"] == "2026-10-31"            # bản đang dùng không đổi
        meta_1 = client.get("/api/meta", params={"data_version": version_1}).json()
        meta_2 = client.get("/api/meta", params={"data_version": version_2}).json()
        assert (meta_1["current_run_id"], meta_2["current_run_id"]) == (run_1, run_2)   # đúng lượt tính cũ, không đẻ lượt mới
        assert meta_1["status"] == "tam_tinh" and meta_2["status"] == "da_soat"         # dấu "Đã soát" còn sau khi bật lại
    with store.connect(uploads_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bonus_run").fetchone()[0] == runs_before
        assert conn.execute("SELECT COUNT(*) FROM bonus_line").fetchone()[0] == lines_before
    assert (tmp_path / "ngoai.xlsx").read_bytes() == TARGETS.read_bytes()
    # ---- lần bật 3: bật lại lần nữa — vẫn hai bản, kho vẫn không thêm lượt tính, bản đã soát VẪN "đã soát" (hai bản cùng kỳ, hai lần bật lại)
    with backend(tmp_path) as client:
        assert client.get("/api/system/ky", headers=KEY).json()["restored_uploads"]["restored"] == 2
        again = client.get("/api/meta", params={"data_version": version_2}).json()
        assert again["status"] == "da_soat" and again["current_run_id"] == run_2
    with store.connect(uploads_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bonus_run").fetchone()[0] == runs_before
    # ---- lần bật 4: CODE tính đã đổi sau lúc tải ⇒ bản nạp lại mang mã MỚI + lượt tính MỚI, KHÔNG thừa hưởng dấu soát của lượt cũ
    #      (dấu soát gắn với một bản tính cụ thể: file + luật + code)
    real_fingerprint = main._code_fingerprint
    monkeypatch.setattr(main, "_code_fingerprint", lambda workflow: ("codemoi" + real_fingerprint(workflow)[0][7:], real_fingerprint(workflow)[1]))
    with backend(tmp_path) as client:
        report = client.get("/api/system/ky", headers=KEY).json()["restored_uploads"]
        assert report["restored"] == 2 and len(report["changed"]) == 2
        assert client.get("/api/meta", params={"data_version": version_1}).status_code == 404        # mã cũ không còn hỏi được
        states = list(client.app.state.versions.values())
        assert len(states) == 2 and all(state.ctx.status == "tam_tinh" for state in states)          # không bản nào còn mang dấu soát cũ
        assert not {state.run_id for state in states} & {run_1, run_2}
    with store.connect(uploads_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bonus_run").fetchone()[0] == runs_before + 2
        assert store.run_summary(conn, run_2)["status"] == "da_soat"                                 # lượt cũ + dấu soát của nó vẫn nằm trong kho


def test_refused_upload_writes_no_restore_row(tmp_path):
    from kpi_agent.backend import store
    with backend(tmp_path) as client:
        refused = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-09"}, content=TARGETS.read_bytes(), headers=BIN)
        assert refused.status_code in (409, 422)
    uploads_db = tmp_path / "kpi_store_uploads.sqlite"
    if uploads_db.exists():
        with store.connect(uploads_db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM uploaded_version").fetchone()[0] == 0
