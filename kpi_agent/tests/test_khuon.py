"""Bậc 2 nạp dữ liệu chung — bước 2.1: lược đồ trường chuẩn `don_hang` (MỘT nguồn cho lõi + API) và
đọc bố cục file (sheet, dòng tiêu đề, cột, vân tay bố cục) — CHỈ ĐỌC, không đổi số.

Giữ sàn: (1) RAW T10 và T11 cùng bố cục ⇒ cùng vân tay, "đã biết"; (2) lời trả không lặp chữ người tải đặt (rào U1): tên cột / sheet lạ bị che,
chỉ trả tên thuộc bộ cột đã biết của file xuất DMS; (3) file không nhận ra dòng tiêu đề ⇒ từ chối, không đoán."""
from pathlib import Path

import openpyxl
import pytest

from kpi_agent.core import khuon

ROOT = Path(__file__).resolve().parents[2]
RAW_T10 = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
RAW_T11 = ROOT / "sample_data/thang_10/RAW DATA (thang sau).xlsx"
needs_data = pytest.mark.skipif(not (RAW_T10.exists() and RAW_T11.exists()), reason="không có dữ liệu thật trên máy này")


def test_schema_is_the_one_source_for_core_columns():
    from kpi_agent.core.load_raw import NEEDED
    fields = {field["field"]: field for field in khuon.schema("don_hang")}
    assert len(fields) == 20      # 19 + discount_value (bước 2.4 — kiểm nghĩa cột tiền)
    # 13 cột lõi bắt buộc của bậc 1 = đúng 13 trường bắt buộc ở lõi, cùng tên cột mặc định
    assert {field["default_column"] for field in fields.values() if field["required"]} == set(NEEDED)
    assert fields["invoiced_at"]["kind"] == "ngay_gio" and fields["invoiced_at"]["may_be_blank"] is True
    assert fields["sales_before_discount"]["kind"] == "tien" and fields["total_cases"]["kind"] == "so_le"
    with pytest.raises(ValueError, match="chưa có khuôn"):
        khuon.schema("chi_tieu")          # bậc 2 chỉ RAW


@needs_data
def test_real_t10_and_t11_have_the_same_known_layout():
    t10, t11 = khuon.read_layout(RAW_T10, "don_hang"), khuon.read_layout(RAW_T11, "don_hang")
    assert t10["fingerprint"] == t11["fingerprint"] == khuon.KNOWN_LAYOUTS["don_hang"] and t10["known"] is True
    assert (t10["sheet"], t10["header_row"], len(t10["columns"])) == ("Export", 1, 38)
    assert all(not column.get("masked") for column in t10["columns"])          # mọi cột thuộc bộ cột DMS đã biết ⇒ trả tên
    assert t10["missing_required"] == []


def _book(tmp_path, sheet_name, rows) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = sheet_name
    for row in rows:
        sheet.append(row)
    path = tmp_path / "f.xlsx"
    book.save(path)
    return path


def test_header_found_below_title_rows_and_unknown_names_are_masked(tmp_path):
    # File gõ tay: 2 dòng tựa ở trên, 1 cột lạ có chữ người đặt, thiếu cột INVOICED DATE ⇒ đoán dòng 3, che tên lạ, báo thiếu trường bắt buộc
    header = ["SM CODE", "ORDER NUM", "ORDER STATUS", "ORDER DATE", "OUTLET CODE", "SUB DIV", "DMS OBJ", "SKU CODE", "Sales Before Discount", "L1",
              "MOQ_FLAG", "FREE ITEM FLAG", "<script>ghi chú</script>"]
    layout = khuon.read_layout(_book(tmp_path, "Đơn hàng tháng 11", [["Báo cáo"], [], header, ["S1"]]), "don_hang")
    assert layout["header_row"] == 3 and layout["known"] is False
    assert layout["sheet"] is None and layout["sheet_masked"] is True        # tên sheet lạ không trả lại
    masked = [column for column in layout["columns"] if column.get("masked")]
    assert len(masked) == 1 and masked[0] == {"position": 13, "masked": True, "length": len("<script>ghi chú</script>")}
    assert layout["missing_required"] == ["invoiced_at"]
    assert "<script>" not in repr(layout)


def test_no_recognisable_header_is_refused(tmp_path):
    with pytest.raises(ValueError, match="không nhận ra dòng tiêu đề"):
        khuon.read_layout(_book(tmp_path, "Export", [["a", "b"], [1, 2]]), "don_hang")


def test_fingerprint_ignores_spaces_and_trailing_blanks_but_not_order():
    base = khuon.layout_fingerprint("Export", 1, ["A", "B"])
    assert khuon.layout_fingerprint(" Export ", 1, ["A ", "B", None, ""]) == base
    assert khuon.layout_fingerprint("Export", 1, ["B", "A"]) != base and khuon.layout_fingerprint("Export", 2, ["A", "B"]) != base


# ---------- API (bước 2.1): cùng rào với /api/system/upload ----------
SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
BIN = {**KEY, "Content-Type": "application/octet-stream"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from datetime import date
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    folder = tmp_path_factory.mktemp("khuon")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        mp.setattr(main, "UPLOAD_DIR", folder / "uploads")
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


@needs_data
def test_api_schema_and_layout_of_real_t10(client):
    assert client.get("/api/system/khuon/don_hang").status_code in (401, 403)           # thiếu khoá hệ thống
    body = client.get("/api/system/khuon/don_hang", headers=KEY).json()
    assert len(body["fields"]) == 20 and body["fields"][0]["field"] == "sm_code"
    assert client.get("/api/system/khuon/chi_tieu", headers=KEY).status_code == 404
    data = RAW_T10.read_bytes()
    reply = client.post("/api/system/bo-cuc", params={"bang": "don_hang"}, content=data, headers=BIN)
    assert reply.status_code == 200, reply.text[:300]
    layout = reply.json()
    import hashlib
    assert layout["sha_file"] == hashlib.sha256(data).hexdigest() and layout["known"] is True and layout["header_row"] == 1
    assert (client.app.state.upload_dir / "cho_ghep" / f"{layout['sha_file']}.xlsx").exists()
    assert not list((client.app.state.upload_dir / "cho_ghep" / ".dang_kiem").glob("*.xlsx"))      # file tạm đã dọn


def test_api_layout_bad_inputs_are_refused_without_echo(client, tmp_path):
    assert client.post("/api/system/bo-cuc", params={"bang": "x"}, content=b"PK", headers=BIN).status_code == 422
    assert client.post("/api/system/bo-cuc", params={"bang": "don_hang"}, content=b"PK", headers={**KEY, "Content-Type": "text/csv"}).status_code == 415
    assert client.post("/api/system/bo-cuc", params={"bang": "don_hang"}, content=b"khong phai xlsx", headers=BIN).status_code == 422
    path = _book(tmp_path, "sheet lạ bí mật", [["<script>cột lạ</script>", "SM CODE"], ["S1", "S1"]])
    reply = client.post("/api/system/bo-cuc", params={"bang": "don_hang"}, content=path.read_bytes(), headers=BIN)
    assert reply.status_code == 200 and "<script>" not in reply.text and "bí mật" not in reply.text
    assert reply.json()["columns"][0] == {"position": 1, "masked": True, "length": len("<script>cột lạ</script>")}
    path = _book(tmp_path, "Export", [["a", "b"]])
    reply = client.post("/api/system/bo-cuc", params={"bang": "don_hang"}, content=path.read_bytes(), headers=BIN)
    assert reply.status_code == 422 and "không nhận ra dòng tiêu đề" in reply.text
