"""2a-2 nấc 2 — bản lấy tuyến từ sổ theo dõi TẢI LÊN thì nền 1 % gia vị + danh sách hội viên cũng đọc từ CHÍNH sổ đó (không đọc sổ ghi cứng trong file luật).

Sổ thử = bảng tuyến thật (dựng lại bằng `make_book`) + hai sheet nền chép GIÁ TRỊ từ sổ thật tháng 10, sửa đúng một thứ mỗi ca.
"""
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from test_tuyen_duong_ghep import BOOK, needs_data, real_rows
from test_tuyen_tu_so import make_book

ROOT = Path(__file__).resolve().parents[2]
SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
BIN = {**KEY, "Content-Type": "application/octet-stream"}
SPICE_SHEET, SPICE_HEADER_ROW, MEMBER_SHEET = "1% gia vi", 11, "Hoi Vien Masan"
TEAM_SPICE = 11_868_854


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    folder = tmp_path_factory.mktemp("nen")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setenv("KPI_UPLOAD_DIR", str(folder / "uploads"))
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


@pytest.fixture(scope="module")
def parts() -> dict:
    """Bảng tuyến thật + giá trị hai sheet nền của sổ thật: {"routes": [...], "sheets": {tên sheet: [dòng…]}}."""
    source = openpyxl.load_workbook(BOOK, read_only=True, data_only=True)
    try:
        sheets = {name: [list(row) for row in source[name].iter_rows(values_only=True)] for name in (SPICE_SHEET, MEMBER_SHEET)}
    finally:
        source.close()
    return {"routes": real_rows(), "sheets": sheets}


def book(path: Path, parts: dict, change=None, with_sheets: bool = True) -> bytes:
    make_book(path, rows=parts["routes"])
    if with_sheets:
        sheets = {name: [list(row) for row in rows] for name, rows in parts["sheets"].items()}
        if change:
            change(sheets)
        wb = openpyxl.load_workbook(path)
        for name, rows in sheets.items():
            ws = wb.create_sheet(name)
            for row in rows:
                ws.append(row)
        wb.save(path)
    return path.read_bytes()


def _version(client, body: bytes) -> str:
    reply = client.post("/api/system/upload", params={"loai": "so_tracking", "ky": "2026-10"}, content=body, headers=BIN)
    assert reply.status_code == 200, reply.text[:300]
    return reply.json()["data_version"]


def _spice(client, version: str | None = None):
    return client.get("/api/system/spice-loyalty", params={"data_version": version} if version else {}, headers=KEY)


@needs_data
def test_book_version_reads_both_baselines_from_the_uploaded_book(client, parts, tmp_path):
    version = _version(client, book(tmp_path / "so_du.xlsx", parts))
    base, new = _spice(client).json(), _spice(client, version).json()
    assert new["team_reward"] == base["team_reward"] == TEAM_SPICE and new["team_customers"] == base["team_customers"]
    assert (base["source"]["baseline_from"], new["source"]["baseline_from"]) == ("file_co_dinh", "so_tai_len")
    members = lambda v: client.get("/api/membership", params={"data_version": v} if v else {}).json()
    assert members(version)["rows"] == members(None)["rows"] and len(members(None)["rows"]) == 417


@needs_data
def test_changing_a_baseline_cell_in_the_book_changes_the_money_and_a_result_cell_does_not(client, parts, tmp_path):
    header = [str(value).strip() if value is not None else "" for value in parts["sheets"][SPICE_SHEET][SPICE_HEADER_ROW - 1]]
    baseline_at = header.index("Chỉ tiêu")                       # cột nền (cột ĐẦU mang tên này, như file luật)
    other_numeric = [index for index in range(len(header)) if index != baseline_at and header[index] not in ("Outlet Code", "Tên nhân viên", "")]

    def raise_baselines(sheets):                                 # nền của 200 khách đầu gấp 1.000 lần ⇒ các khách đó hết đạt 110 % nền
        for row in sheets[SPICE_SHEET][SPICE_HEADER_ROW:SPICE_HEADER_ROW + 200]:
            if isinstance(row[baseline_at], (int, float)):
                row[baseline_at] = row[baseline_at] * 1000

    def scramble_results(sheets):                                # mọi cột KHÁC (doanh số, %, cột trùng tên phía sau…) đổi hết ⇒ tiền không đổi
        for row in sheets[SPICE_SHEET][SPICE_HEADER_ROW:]:
            for index in other_numeric:
                if index < len(row) and isinstance(row[index], (int, float)):
                    row[index] = 123456
    lower = _spice(client, _version(client, book(tmp_path / "so_nen_cao.xlsx", parts, raise_baselines))).json()
    same = _spice(client, _version(client, book(tmp_path / "so_doi_ket_qua.xlsx", parts, scramble_results))).json()
    assert 0 < lower["team_reward"] < TEAM_SPICE and same["team_reward"] == TEAM_SPICE


@needs_data
def test_book_without_the_baseline_sheets_gets_a_refusal_not_the_fixed_file_numbers(client, parts, tmp_path):
    version = _version(client, book(tmp_path / "so_khong_co_sheet_nen.xlsx", parts, with_sheets=False))
    refused = _spice(client, version)
    assert refused.status_code == 409 and "thiếu sheet" in refused.json()["refused"] and "team_reward" not in refused.json()
    members = client.get("/api/membership", params={"data_version": version}).json()
    assert "refused" in members and "rows" not in members
