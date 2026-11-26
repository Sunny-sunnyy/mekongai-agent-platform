"""Kiểm minor #1 (`items[]` kỳ trống + giờ VN `UTC+7`) và `K1` (nhân viên mới chưa có mã che `NV15…`).

Chạy bằng `.venv/bin/python -m pytest kpi_agent/tests/test_nhan_vien_moi_va_ky_trong.py -q`.
Dùng dữ liệu thật tháng 10 + SQLite / thư mục tạm trong `tmp_path` (không sửa `ma_che_ten.json`, không mock).
"""
import io
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from kpi_agent.backend import store
from kpi_agent.core import khuon, load_tuyen
from kpi_agent.core.load_data import load_targets
from test_tuyen_tu_so import OFF, make_book

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
BOOK = ROOT / "sample_data/thang_10/Tracking.xlsx"
MASK = ROOT / "kpi_agent/data/ma_che_ten.json"
NEEDED = [RAW, TARGETS, BOOK, MASK, ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")

SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
BIN = {**KEY, "Content-Type": "application/octet-stream"}
TEAM_TOTAL = 152_360_000
VN_OFFSET = timezone(timedelta(hours=7))


def _backend(folder: Path, *, empty: bool = True):
    from kpi_agent.backend import main
    mp = pytest.MonkeyPatch()
    mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
    mp.setenv("KPI_UPLOAD_DIR", str(folder / "uploads"))
    mp.setenv("KPI_SYS_KEY", SYS_KEY)
    if empty:
        mp.setenv("KPI_EMPTY_PERIOD", "2026-10")
    else:
        mp.delenv("KPI_EMPTY_PERIOD", raising=False)
    mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    mp.setattr(main, "CUTOFF", date(2026, 10, 31))
    return mp, main


def _post_file(client, kind: str, body: bytes, **extra):
    return client.post("/api/system/upload", params={"loai": kind, "ky": "2026-10", **extra}, content=body, headers=BIN)


def _targets_with_extra_employees(extras: list[tuple[str, str]], *, drop_base: int = 0) -> bytes:
    """File chỉ tiêu thật tháng 10: giữ (14 - drop_base) người đầu và thêm các nhân viên mới `(sm_code, sm_name)` nhân bản từ mẫu KPI của người đầu."""
    source = openpyxl.load_workbook(TARGETS, read_only=True, data_only=True)
    rows = list(source["Export"].iter_rows(values_only=True))
    source.close()
    header = list(rows[0])
    i_pos, i_code, i_name = header.index("SM pos"), header.index("SM code"), header.index("SM name")
    first_code = str(rows[1][i_code])
    template_rows = [list(r) for r in rows[1:] if str(r[i_code]) == first_code]
    kept_codes = sorted({str(r[i_code]) for r in rows[1:]})[drop_base:]

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export"
    sheet.append(header)
    for r in rows[1:]:
        if str(r[i_code]) in kept_codes:
            sheet.append(list(r))
    for idx, (sm_code, sm_name) in enumerate(extras, start=1):
        for r in template_rows:
            cloned = list(r)
            cloned[i_pos] = f"SM-MOI-{idx:02d}"
            cloned[i_code] = sm_code
            cloned[i_name] = sm_name
            sheet.append(cloned)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _small_raw(sm_code: str) -> bytes:
    from kpi_agent.core.load_raw import NEEDED as COLUMNS
    values = {"SM CODE": sm_code, "ORDER NUM": "D1", "ORDER STATUS": "Invoiced", "ORDER DATE": datetime(2026, 10, 5), "OUTLET CODE": "O1",
              "SUB DIV": "Beer", "DMS OBJ": "Bia White Lion", "SKU CODE": "X", "Sales Before Discount": 100000, "L1": "Kênh Off Premise",
              "MOQ_FLAG": "Y", "FREE ITEM FLAG": "N", "INVOICED DATE": datetime(2026, 10, 5, 9)}
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export"
    sheet.append(COLUMNS)
    sheet.append([values[c] for c in COLUMNS])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_minor_1_empty_period_items_show_uploaded_and_vn_timezone(tmp_path):
    """Kỳ trống dựng bản đầu từ 3 file tải lên ⇒ `raw`/`chi_tieu`/`tuyen` đều ghi đã tải lên (không 'nạp lúc bật') và giờ ghi mang `+07:00`."""
    assert store._now().endswith("+07:00")
    mp, main = _backend(tmp_path, empty=True)
    try:
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
            assert _post_file(client, "so_tracking", BOOK.read_bytes()).status_code == 200
            assert _post_file(client, "chi_tieu", TARGETS.read_bytes()).status_code == 200
            first = _post_file(client, "raw", RAW.read_bytes(), ngay_chot="2026-10-31")
            assert first.status_code == 200 and first.json()["ban_dau_cua_ky"] is True

            status = client.get("/api/system/ky/2026-10", headers=KEY).json()
            assert status["ready"] is True
            by_key = {item["key"]: item for item in status["items"]}
            for key in ("raw", "chi_tieu", "tuyen"):
                item = by_key[key]
                assert item["from_upload"] is True, (key, item)
                assert item["checked"] is True, (key, item)
                assert "đã tải lên và qua kiểm" in item["detail"] and "nạp lúc bật" not in item["detail"], (key, item)
                assert item["uploaded_at"].endswith("+07:00"), (key, item)
                assert datetime.fromisoformat(item["uploaded_at"]).utcoffset() == VN_OFFSET.utcoffset(None)
            saved_times = {key: by_key[key]["uploaded_at"] for key in ("raw", "chi_tieu", "tuyen")}
    finally:
        mp.undo()

    # Bật lại từ bộ chờ đã lưu: giữ nguyên trạng thái đã tải lên + đúng giờ VN
    mp2, main2 = _backend(tmp_path, empty=True)
    try:
        with TestClient(main2.app, base_url="http://127.0.0.1:8110") as client:
            status2 = client.get("/api/system/ky/2026-10", headers=KEY).json()
            by_key2 = {item["key"]: item for item in status2["items"]}
            for key in ("raw", "chi_tieu", "tuyen"):
                assert by_key2[key]["from_upload"] is True and by_key2[key]["checked"] is True
                assert "nạp lúc bật" not in by_key2[key]["detail"]
                assert by_key2[key]["uploaded_at"] == saved_times[key]
    finally:
        mp2.undo()


def _book_with_extra_routes(tmp_path: Path, extra_codes: list[str]) -> bytes:
    """Sổ theo dõi từ bộ tuyến thật tháng 10 + 1 tuyến mẫu cho mỗi mã nhân viên mới."""
    base_team = {str(code).strip() for code in load_targets(TARGETS)["employees"]}
    mcp = load_tuyen.read_routes(BOOK, khuon.default_mapping("tuyen"), base_team)["mcp"]
    rows = [(sm, info["channel"], outlet, "F4", info["visit_day"]) for sm, outlets in mcp.items() for outlet, info in outlets.items()]
    for idx, sm_code in enumerate(extra_codes, start=1):
        rows.append((sm_code, OFF, f"MOI_OUTLET_{idx:02d}", "F4", "Thứ 2"))
    path = make_book(tmp_path / "book_15.xlsx", rows=rows)
    return path.read_bytes()


def test_k1_new_employee_auto_mask_persists_keeps_14_and_blocks_bad_cases(tmp_path):
    """K1: tự cấp NV15 khi tải `chi_tieu` 15 người, giữ nguyên 14 người, bền qua restart, cho phép tải lại 14 người; chặn mã lạ ở `raw` & `file_sai_doi`."""
    mask_before = MASK.read_text(encoding="utf-8")
    new_code, new_name = "99SF.MOI01", "Nguyễn Văn Mới"
    targets_15 = _targets_with_extra_employees([(new_code, new_name)])
    book_15 = _book_with_extra_routes(tmp_path, [new_code])
    targets_wrong_team = _targets_with_extra_employees([(f"99SF.LA{i:02d}", f"Lạ {i}") for i in range(1, 15)], drop_base=14)
    targets_too_many_new = _targets_with_extra_employees([(f"99SF.TM{i:02d}", f"Thêm {i}") for i in range(1, 9)])
    targets_13 = _targets_with_extra_employees([], drop_base=1)

    mp, main = _backend(tmp_path, empty=True)
    try:
        with TestClient(main.app, base_url="http://127.0.0.1:8110", raise_server_exceptions=False) as client:
            # 1. Ca xấu: mã lạ chỉ có ở `raw` KHÔNG bao giờ được tự cấp mã
            bad_raw = _post_file(client, "raw", _small_raw("ZZ999"), ngay_chot="2026-10-31")
            assert bad_raw.status_code == 422 and "chưa có mã che" in bad_raw.json()["refused"] and "ZZ999" not in bad_raw.text

            # 2. Ca xấu: file chỉ tiêu sai đội (0 người trùng đội gốc) & quá nhiều người lạ (> max(5, 14//2) = 7) ⇒ 422 file_sai_doi
            wrong = _post_file(client, "chi_tieu", targets_wrong_team)
            assert wrong.status_code == 422 and wrong.json()["code"] == "file_sai_doi" and "99SF.LA" not in wrong.text
            too_many = _post_file(client, "chi_tieu", targets_too_many_new)
            assert too_many.status_code == 422 and too_many.json()["code"] == "file_sai_doi" and "99SF.TM" not in too_many.text

            # 3. Ca xấu: file chỉ tiêu thiếu 1 người của đội gốc vẫn bị chặn `doi_thieu_nguoi`
            short = _post_file(client, "chi_tieu", targets_13)
            assert short.status_code == 422 and short.json()["code"] == "doi_thieu_nguoi"

            # 4. Ca 15 người (tải chỉ tiêu 15 người trong kỳ trống ⇒ tự cấp NV15, đủ 3 file ⇒ bản đầu 15 người giữ nguyên số 14 người)
            up15 = _post_file(client, "chi_tieu", targets_15)
            assert up15.status_code == 200, up15.text[:400]
            assert up15.json()["nhan_vien_moi"] == [{"ma_che": "NV15", "ho_ten": new_name}]
            assert new_code not in up15.text

            assert _post_file(client, "raw", RAW.read_bytes(), ngay_chot="2026-10-31").status_code == 200
            built15 = _post_file(client, "so_tracking", book_15)
            assert built15.status_code == 200, built15.text[:400]
            body15 = built15.json()
            v15 = body15["data_version"]
            assert new_code not in json.dumps({k: v for k, v in body15.items() if k != "tuyen_tu_so"}, ensure_ascii=False)
            assert any("NV15" in flag and "tự cấp mã che" in flag for flag in body15["data_flags"])
            assert body15["team_total"] == TEAM_TOTAL

            table15 = client.get("/api/system/bonus-table", params={"data_version": v15}, headers=KEY).json()
            assert len(table15["table"]["lines"]) == 15 * 8
            assert table15["table"]["team_total"] == TEAM_TOTAL
            assert new_code not in json.dumps(table15, ensure_ascii=False)
            assert new_name not in json.dumps(table15, ensure_ascii=False)

            nv15 = client.post("/api/tool/get_employee_result", params={"data_version": v15}, json={"args": {"employee_id": "NV15"}}).json()
            assert nv15["employee_id"] == "NV15" and nv15["business_salary"] == 0 and new_code not in json.dumps(nv15)

            # Duyệt & xuất Excel trên bản 15 người vẫn chạy sạch, 0 rò mã/tên thật
            approved = client.post(
                "/api/system/duyet",
                params={"data_version": v15},
                headers=KEY,
                json={"run_id": table15["run_id"], "sha256_bang": table15["sha256"], "org_id": "o", "nguoi_duyet": "Kiểm"},
            )
            assert approved.status_code == 200, approved.text[:300]
            assert client.get("/api/export/xlsx", params={"data_version": v15}, headers=KEY).status_code == 200

            # 5. Tải lại file chỉ tiêu 14 người sau khi đã cấp NV15 ⇒ KHÔNG báo thiếu người, ra bản 14 người như cũ
            re14 = _post_file(client, "chi_tieu", TARGETS.read_bytes())
            assert re14.status_code == 200, re14.text[:300]
            v14 = re14.json()["data_version"]
            assert re14.json()["team_total"] == TEAM_TOTAL and "nhan_vien_moi" not in re14.json()
            assert len(client.get("/api/system/bonus-table", params={"data_version": v14}, headers=KEY).json()["table"]["lines"]) == 14 * 8
    finally:
        mp.undo()

    # Kiểm SQLite lưu bền + file `ma_che_ten.json` gốc không bị sửa
    assert MASK.read_text(encoding="utf-8") == mask_before
    with sqlite3.connect(tmp_path / "kpi_store_uploads.sqlite") as conn:
        rows = conn.execute("SELECT masked_code, real_code, full_name, created_at FROM auto_employee_mask").fetchall()
        assert len(rows) == 1 and rows[0][:3] == ("NV15", new_code, new_name) and rows[0][3].endswith("+07:00")

    # 6. Restart backend (kỳ trống nạp lại bộ chờ 15 người + bản 14 người chồng lên): giữ nguyên `data_version`, NV15 vẫn tra cứu được
    mp2, main2 = _backend(tmp_path, empty=True)
    try:
        with TestClient(main2.app, base_url="http://127.0.0.1:8110") as client:
            assert client.get("/api/meta").json()["data_version"] == v14
            nv15_after = client.post("/api/tool/get_employee_result", params={"data_version": v15}, json={"args": {"employee_id": "NV15"}}).json()
            assert nv15_after["employee_id"] == "NV15" and nv15_after["business_salary"] == 0
    finally:
        mp2.undo()
