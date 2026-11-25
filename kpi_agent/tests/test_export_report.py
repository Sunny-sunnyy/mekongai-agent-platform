"""Xuất file bảng thưởng (mục 3): đọc lại file bằng openpyxl / tách số trong chữ, so với kết quả backend của CÙNG bản tính;
0 công thức, 0 link, nhãn TẠM TÍNH, sheet Nguồn khớp /api/runs/{id}, 0 mã thật, thoát chữ đầu = + - @, GET không ghi gì.
Dữ liệu thật tháng 10, store SQLite trong thư mục tạm. (So với đáp án độc lập tính từ lõi: script kiểm riêng, không ở đây.)"""
import io
import json
import re
import sqlite3
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from kpi_agent.backend import export_report, main, store
from kpi_agent.workflow import runner

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
TEAM_TOTAL_OCT = 55_700_000


@pytest.fixture
def client(tmp_path, monkeypatch):
    store_file = tmp_path / "store.sqlite"
    monkeypatch.setenv("KPI_STORE", str(store_file))
    monkeypatch.setattr(main, "WORKFLOW", runner.DEFAULT_WORKFLOW)
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
        test_client.store_file = store_file
        # Bước 3 (bỏ tải nháp): chỉ bản ĐÃ SOÁT mới có file ⇒ soát trước, rồi mới kiểm nội dung file (chưa soát ⇒ 409: test_workflow_export.py)
        run_id = test_client.get("/api/meta").json()["current_run_id"]
        assert test_client.post(f"/api/runs/{run_id}/review", json={"reviewer": "Giám sát thử"}).status_code == 200
        yield test_client


def _workbook(client) -> openpyxl.Workbook:
    response = client.get("/api/export/xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return openpyxl.load_workbook(io.BytesIO(response.content))


def test_xlsx_numbers_equal_backend_and_no_formula_or_link(client):
    wb = _workbook(client)
    assert wb.sheetnames == ["Tổng đội", "Chi tiết", "Nguồn"]
    assert all(ws["A1"].value == export_report.TITLE_REVIEWED for ws in wb.worksheets)
    cells = [cell for ws in wb.worksheets for row in ws.iter_rows() for cell in row]
    assert not [c.coordinate for c in cells if c.data_type == "f"] and not [c.coordinate for c in cells if c.hyperlink]
    table = {row["employee_id"]: row for row in client.get("/api/team-bonus").json()["employees"]}
    # Chi tiết: 112 hàng tiền + % đúng y backend
    detail = wb["Chi tiết"]
    rows = [r for r in detail.iter_rows(min_row=6, values_only=True) if r[0]]
    assert len(rows) == 112
    for nv, kpi, pct, _tier, amount, _reason in rows:
        assert amount == table[nv]["kpi_amounts"][kpi]
    # tiền + % là ô SỐ, không phải chữ (góp ý 09/11: lỗi SO_DANG_CHU)
    assert all(detail.cell(row=r, column=c).data_type == "n" for r in range(6, 6 + 112) for c in (3, 5))
    employee = client.get("/api/employees/NV01").json()
    assert {r[1]: r[2] for r in rows if r[0] == "NV01"} == {k["kpi"]: k["pct"] for k in employee["kpis"]}
    # Tổng đội: lương từng NV + dòng Cả đội
    team = wb["Tổng đội"]
    header = [c.value for c in team[5]]
    body = {r[0]: r for r in team.iter_rows(min_row=6, values_only=True) if r[0]}
    assert body["Cả đội"][-1] == TEAM_TOTAL_OCT
    for nv, row in table.items():
        assert body[nv][-1] == row["business_salary"]
        assert {header[i]: body[nv][i] for i in range(1, len(header) - 1)} == row["kpi_amounts"]
    assert team["F6"].number_format == export_report.MONEY and team.freeze_panes == "B6"


def test_downloaded_xlsx_has_no_protected_sheet(client):
    """File tải về là của người dùng: mở ra sửa được ngay, Excel không báo "protected sheet" ở sheet nào."""
    wb = _workbook(client)
    assert {ws.title: ws.protection.sheet for ws in wb.worksheets} == {"Tổng đội": False, "Chi tiết": False, "Nguồn": False}


def test_source_sheet_matches_saved_run(client):
    meta = client.get("/api/meta").json()
    run = client.get(f"/api/runs/{meta['current_run_id']}").json()
    source = {r[0]: r[1] for r in _workbook(client)["Nguồn"].iter_rows(min_row=5, values_only=True) if r[0] and r[0] != "Ghi chú"}
    assert source["Bản tính số"] == run["run_id"] and source["Bản luật"] == run["rule_version"] and source["sha256 bản luật"] == run["rule_sha256"]
    assert source["Ngày chốt số"] == run["data_snapshot"]["cutoff"] and source["Dấu vân tay code tính"] == run["code_sha"]
    assert {k.split(": ", 1)[1]: v for k, v in source.items() if k.startswith("sha256 file dữ liệu")} == run["data_snapshot"]["files_sha256"]
    assert source["Phiên bản workflow"] == meta["workflow_version"]
    assert source["Tổng lương kinh doanh cả đội (đồng)"] == TEAM_TOTAL_OCT


def test_markdown_and_text_carry_same_totals(client):
    table = client.get("/api/team-bonus").json()["employees"]
    for kind in ("md", "txt"):
        response = client.get(f"/api/export/{kind}")
        assert response.status_code == 200 and response.text.startswith(("# " + export_report.TITLE_REVIEWED, export_report.TITLE_REVIEWED))
        assert response.headers["content-disposition"] == f'attachment; filename="bang_thuong_2026-10_ban{client.get("/api/meta").json()["current_run_id"]}.{kind}"'
        lines = response.text.splitlines()
        for row in table:   # lương KD là số CUỐI của hàng tổng đội của NV đó
            hit = next(line for line in lines if line.lstrip("| ").startswith(row["employee_id"]) and line.count(".") >= 1)
            assert re.findall(r"\d[\d.]*", hit)[-1].replace(".", "") == str(row["business_salary"])
        total_line = next(line for line in lines if "Cả đội" in line)
        assert re.findall(r"\d[\d.]*", total_line)[-1].replace(".", "") == str(TEAM_TOTAL_OCT)


def test_no_real_employee_code_in_any_file(client):
    real = list(json.loads((ROOT / "kpi_agent/data/ma_che_ten.json").read_text(encoding="utf-8"))["map"].values())
    wb = _workbook(client)
    texts = [str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value is not None]
    texts += [client.get(f"/api/export/{kind}").text for kind in ("md", "txt")]
    joined = "\n".join(texts)
    assert not [code for code in real if code in joined] and not runner.REAL_CODE_PATTERN.findall(joined)


def test_text_starting_like_formula_stays_text(client):
    with store.connect(client.store_file) as conn:
        data = export_report.report_data(conn, client.get("/api/meta").json()["current_run_id"])
    first = (data["employees"][0], data["kpis"][0])
    data["cells"][first] = {**data["cells"][first], "reason": "=HYPERLINK(\"http://x\")", "reached_tier": "+1"}
    detail = openpyxl.load_workbook(io.BytesIO(export_report.to_xlsx(data)))["Chi tiết"]
    # minor 5 (C 13:23): giữ ĐÚNG chữ, ô kiểu chữ + quotePrefix (Excel ẩn dấu ') — bản đầu ghi thêm dấu ' vào chữ
    assert detail["F6"].value == "=HYPERLINK(\"http://x\")" and detail["F6"].data_type == "s" and detail["F6"].quotePrefix
    assert detail["D6"].value == "+1" and detail["D6"].data_type == "s" and detail["D6"].quotePrefix
    assert not detail["F7"].quotePrefix   # chữ thường không bật cờ
    assert all(export_report.looks_like_formula(t) for t in ("-5", "@x", "=1", "+1")) and not export_report.looks_like_formula("NV01")
    md = export_report.to_markdown({**data, "status": "a | b [x](y) <b>"})
    assert "a \\| b \\[x\\](y) &lt;b&gt;" in md


def test_bad_requests_and_get_writes_nothing(client):
    def counts():
        conn = sqlite3.connect(client.store_file)
        try:
            return [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in store.TABLES]
        finally:
            conn.close()
    before = counts()
    assert client.get("/api/export/pdf").status_code == 404
    assert client.get("/api/export/xlsx?run_id=999").status_code == 404
    assert client.get("/api/export/xlsx?run_id=0").status_code == 422 and client.get("/api/export/xlsx?run_id=abc").status_code == 422
    assert client.get(f"/api/export/xlsx?run_id={2**63}").status_code == 422
    for kind in ("xlsx", "md", "txt"):
        assert client.get(f"/api/export/{kind}").status_code == 200
    assert counts() == before


def test_long_text_not_cut_control_chars_removed_nfc(client):
    import unicodedata
    with store.connect(client.store_file) as conn:
        data = export_report.report_data(conn, client.get("/api/meta").json()["current_run_id"])
    first = (data["employees"][0], data["kpis"][0])
    long_reason = "lý do " + "x" * 2500   # góp ý D: lỗi "cắt chữ lặng lẽ" ở ô > 2000 ký tự
    decomposed = unicodedata.normalize("NFD", "Đạt mốc")
    data["cells"][first] = {**data["cells"][first], "reason": long_reason + "\x07", "reached_tier": decomposed}
    wb = openpyxl.load_workbook(io.BytesIO(export_report.to_xlsx(data)))
    detail = wb["Chi tiết"]
    assert detail["F6"].value == long_reason and detail["D6"].value == unicodedata.normalize("NFC", "Đạt mốc")
    assert not any(ws.protection.sheet for ws in wb.worksheets)
    assert long_reason in export_report.to_text(data) and "\x07" not in export_report.to_text(data)
    assert export_report._md("a & b `c`") == "a &amp; b \\`c\\`"


def test_missing_cell_refuses_instead_of_silent_total(client, tmp_path):
    # Bản tính THẬT thiếu một dòng NV × KPI, ghi bằng chính store.record_run vào SQLite tạm (store chỉ thêm nên bản thật không thiếu được —
    # ca này giữ luật "thiếu dữ liệu thì báo", góp ý). Không giả lập hàm nào.
    with store.connect(client.store_file) as conn:
        run_id = client.get("/api/meta").json()["current_run_id"]
        lines, summary = store.run_lines(conn, run_id), store.run_summary(conn, run_id)
    other = tmp_path / "thieu.sqlite"
    with store.connect(other) as conn:
        broken = store.record_run(conn, period=summary["period"], cutoff=summary["data_snapshot"]["cutoff"], rule_file=ROOT / "kpi_agent/config/rules_2026_10.yaml",
                                  data_files=summary["data_snapshot"]["files_sha256"], refreshed_at=None, code_sha="thu", code_files=[], lines=lines[1:])
        with pytest.raises(ValueError, match="thiếu 1 ô"):
            export_report.report_data(conn, broken)


def test_broken_run_gives_409_with_reason(client, tmp_path, monkeypatch):
    # minor 4 (C 13:23): bản tính thiếu ô ⇒ 409 kèm lời (bản đầu: ValueError ⇒ 500 trống). Bản thiếu ghi THẬT bằng store.record_run vào store riêng
    with store.connect(client.store_file) as conn:
        run_id = client.get("/api/meta").json()["current_run_id"]
        lines, summary = store.run_lines(conn, run_id), store.run_summary(conn, run_id)
        broken = store.record_run(conn, period=summary["period"], cutoff=summary["data_snapshot"]["cutoff"], rule_file=ROOT / "kpi_agent/config/rules_2026_10.yaml",
                                  data_files=summary["data_snapshot"]["files_sha256"], refreshed_at=None, code_sha="thu_409", code_files=[], lines=lines[1:])
    response = client.get(f"/api/export/xlsx?run_id={broken}")
    assert response.status_code == 409 and "thiếu 1 ô" in response.json()["refused"]
