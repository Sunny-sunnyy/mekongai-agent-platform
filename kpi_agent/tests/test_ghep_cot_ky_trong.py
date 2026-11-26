"""Màn GHÉP CỘT chạy được khi KỲ còn TRỐNG (20/11/2026).

Trước: kỳ chưa có bản ⇒ ba đường ghép (`bo-cuc` ⇒ `ghep-thu` ⇒ `ghep-xac-nhan`) trả 409 và cửa tải lên không tra cách ghép đã xác nhận ⇒ file đơn
hàng lệch mẫu (đổi tên MỘT cột) kẹt ngay bước đầu. Một ca đi đúng đường người dùng, dữ liệu thật tháng 10:
 tái hiện (file lệch mẫu bị từ chối có mã `can_ghep_cot`, màn ghép mở được) ⇒ ca xấu về TIỀN (ghép "doanh số trước chiết khấu" vào cột tiền khác
 ⇒ không có câu kiểm; trả lời sai câu kiểm ⇒ không lưu) ⇒ ghép đúng + xác nhận ⇒ tải file đó + chỉ tiêu + sổ ⇒ 152.360.000, 112 ô bằng đường cũ.
"""
import openpyxl
import pytest

from kpi_agent.core import khuon
from test_ky_trong import BIN, KEY, RAW, TEAM_TOTAL, backend, cells, old_way_cells, pytestmark, send   # noqa: F401 — fixture + điều kiện dữ liệu thật

RENAMED = "Doanh so truoc CK"          # tên mới của cột "Sales Before Discount" — không có trong mẫu


@pytest.fixture(scope="module")
def renamed_raw(tmp_path_factory):
    """File đơn hàng thật tháng 10, CHỈ đổi tên một cột ⇒ (byte file, vị trí cột đó tính từ 1, vị trí cột tiền sau chiết khấu)."""
    source = openpyxl.load_workbook(RAW, read_only=True, data_only=True)
    rows = source["Export"].iter_rows(values_only=True)
    header = list(next(rows))
    at, after = header.index("Sales Before Discount"), header.index("Sales After Discount")
    header[at] = RENAMED
    target = openpyxl.Workbook(write_only=True)
    sheet = target.create_sheet("Export")
    sheet.append(header)
    for row in rows:
        sheet.append(list(row))
    source.close()
    path = tmp_path_factory.mktemp("lech_mau") / "don_hang_doi_ten_cot.xlsx"
    target.save(path)
    return path.read_bytes(), at + 1, after + 1


def test_renamed_order_file_can_be_mapped_while_period_is_empty_and_gives_the_same_money(tmp_path, renamed_raw, old_way_cells):
    body, money_at, after_at = renamed_raw
    upload_params = {"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-31"}
    with backend(tmp_path) as client:
        # ---- tái hiện: kỳ trống vẫn mở được khuôn bảng để ghép cột (`GET /api/system/khuon/don_hang`), trước trả 409 kỳ chưa có dữ liệu
        schema = client.get("/api/system/khuon/don_hang", headers=KEY)
        assert schema.status_code == 200, schema.text[:300]
        assert schema.json().get("table") == "don_hang" and len(schema.json().get("fields") or []) > 0
        # ---- tái hiện: file lệch mẫu chưa ghép ⇒ từ chối có mã chỉ đường; màn ghép MỞ được ở kỳ trống (trước: 409 kỳ chưa có dữ liệu)
        first = client.post("/api/system/upload", params=upload_params, content=body, headers=BIN)
        assert first.status_code == 422 and first.json()["code"] == "can_ghep_cot", first.text[:300]
        layout = client.post("/api/system/bo-cuc", params={"bang": "don_hang"}, content=body, headers=BIN)
        assert layout.status_code == 200, layout.text[:300]
        mapping = khuon.default_mapping("don_hang")
        request = {"sha_file": layout.json()["sha_file"], "bang": "don_hang", "ky": "2026-10", "ngay_chot": "2026-10-31"}

        # ---- ca xấu về TIỀN: ghép "doanh số trước chiết khấu" vào cột tiền SAU chiết khấu ⇒ không có câu kiểm để xác nhận
        wrong = {**mapping, "columns": {**mapping["columns"], "sales_before_discount": after_at}}
        refused = client.post("/api/system/ghep-thu", json={**request, "ghep": wrong}, headers=KEY).json()
        assert refused["ok"] is False and refused["cau_kiem"] is None, refused

        # ---- ghép đúng: câu kiểm; trả lời sai ⇒ không lưu, tải file vẫn bị chặn
        right = {**mapping, "columns": {**mapping["columns"], "sales_before_discount": money_at}}
        tried = client.post("/api/system/ghep-thu", json={**request, "ghep": right}, headers=KEY).json()
        assert tried["ok"] is True and tried["doi_soat"]["file_total_matches"] is True, tried
        bad = client.post("/api/system/ghep-xac-nhan", json={"ma": tried["cau_kiem"]["ma"], "tra_loi": after_at, "nguoi": "Giám sát thử"}, headers=KEY)
        assert bad.status_code == 409 and "chưa đúng" in bad.text
        assert client.post("/api/system/upload", params=upload_params, content=body, headers=BIN).json()["code"] == "can_ghep_cot"

        # ---- ca thường: xác nhận đúng ⇒ file lệch mẫu vào bộ chờ như file đúng mẫu ⇒ đủ ba file ⇒ bản đầu, đúng tiền
        tried = client.post("/api/system/ghep-thu", json={**request, "ghep": right}, headers=KEY).json()
        saved = client.post("/api/system/ghep-xac-nhan", json={"ma": tried["cau_kiem"]["ma"], "tra_loi": money_at, "nguoi": "Giám sát thử"}, headers=KEY)
        assert saved.status_code == 200, saved.text[:300]
        waiting = client.post("/api/system/upload", params=upload_params, content=body, headers=BIN)
        assert waiting.status_code == 200 and waiting.json()["con_thieu"] == ["chi_tieu", "so_tracking"], waiting.text[:300]
        assert send(client, "chi_tieu").json()["con_thieu"] == ["so_tracking"]
        first_version = send(client, "so_tracking")
        assert first_version.status_code == 200 and first_version.json()["team_total"] == TEAM_TOTAL, first_version.text[:400]
        assert cells(client) == old_way_cells                                   # 112 ô bằng từng ô bản bật theo đường cũ (file đúng mẫu)
    with backend(tmp_path) as client:                                           # bật lại: bản đầu dựng lại bằng đúng cách ghép đã lưu
        assert cells(client) == old_way_cells


def test_order_file_without_after_discount_column_says_what_to_do_for_spice(tmp_path, tmp_path_factory):
    """Cột doanh số SAU chiết khấu không thuộc cột bắt buộc ⇒ file đổi tên cột đó vẫn được nhận, 8 KPI đúng; khoản 1 % gia vị cần cột này ⇒ lời
    thường chỉ việc phải làm (trước: "RAW thiếu cột: Sales After Discount")."""
    source = openpyxl.load_workbook(RAW, read_only=True, data_only=True)
    rows = source["Export"].iter_rows(values_only=True)
    header = list(next(rows))
    header[header.index("Sales After Discount")] = "Sau CK"
    target = openpyxl.Workbook(write_only=True)
    sheet = target.create_sheet("Export")
    sheet.append(header)
    for row in rows:
        sheet.append(list(row))
    source.close()
    path = tmp_path_factory.mktemp("thieu_cot_sau_ck") / "don_hang.xlsx"
    target.save(path)
    with backend(tmp_path) as client:
        sent = client.post("/api/system/upload", params={"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-31"}, content=path.read_bytes(), headers=BIN)
        assert sent.status_code == 200, sent.text[:300]      # cột này không bắt buộc ⇒ file được nhận thẳng (không qua màn ghép)
        send(client, "chi_tieu")
        first = send(client, "so_tracking")
        assert first.status_code == 200 and first.json()["team_total"] == TEAM_TOTAL, first.text[:300]
        refused = client.get("/api/spice-loyalty").json()["refused"]
        assert "doanh số sau chiết khấu" in refused and "tải lại" in refused and "RAW" not in refused
