"""Bậc 2 bước 2.2: lõi đọc RAW QUA bảng ghép (cột file ⇒ trường chuẩn). Bảng ghép mặc định = bố cục RAW T10 ⇒ số không đổi.

Giữ sàn: (1) file đổi tên cột / sheet / dòng tiêu đề + bảng ghép đúng ⇒ ra ĐÚNG số như file chuẩn; (2) ô CHỮ dạng số ("384,00", "1.234") — kiểu nào cũng
⇒ đỏ, không đoán (17/11 sau ca nhóm kỹ thuật ×1000); (3) bảng ghép sai khuôn ⇒ dừng; (4) bảng ghép khác ⇒ data_version khác (E major)."""
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from kpi_agent.core import khuon
from functools import partial

from kpi_agent.core import load_raw as raw_reader
from kpi_agent.core.rules import load_rules
from luat_thu import load_raw_allowing, write_raw_workflow_allowing

ROOT = Path(__file__).resolve().parents[2]
# bậc 3a: bộ đọc RAW cần luật kỳ (không có mặc định) — lời gọi truyền luật RAW tháng 10, kỳ vọng không đổi
load_raw = partial(raw_reader.load_raw, period_rules=raw_reader.period_rules(load_rules(ROOT / "kpi_agent/config/rules_2026_10_raw.yaml")["assumptions"]))
# bậc 3a P2: file RAW TỰ DỰNG vài dòng không có nhóm Trà 365 / mã Homey 2,9 kg / ngành Thịt (và Bia) mà luật tháng 10 nêu ⇒ luật thật sẽ dừng (ca giữ hành
# vi đó: test_luat_ky_raw, test_loi_tai_len). Ca file tự dựng đọc bằng bản luật thử = luật thật + cho qua đúng các giá trị này (luat_thu.py)
SMALL_FILE_LACKS = ("Trà Búp Non Tea 365", "04HP00639", "Processed Meats")
load_small = load_raw_allowing(*SMALL_FILE_LACKS, "Beer")
COLUMNS = ["SM CODE", "ORDER NUM", "ORDER STATUS", "ORDER DATE", "OUTLET CODE", "SUB DIV", "DMS OBJ", "SKU CODE", "Sales Before Discount", "L1",
           "MOQ_FLAG", "FREE ITEM FLAG", "INVOICED DATE"]
ROW = lambda order, sku, sales, moq="Y", free="N", channel="Kênh Off Premise": ["SM1", order, "Invoiced", date(2026, 11, 2), 1, "Seasoning", "x", sku, sales,
                                                                              channel, moq, free, datetime(2026, 11, 3, 9)]


def _book(tmp_path, name, sheet_name, title_rows, header, rows) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = sheet_name
    for row in title_rows + [header] + rows:
        sheet.append(row)
    path = tmp_path / name
    book.save(path)
    return path


def _summary(result):
    return (result["reconciliation"]["counted_sales"], sorted((row["ORDER NUM"], row["Total"]) for row in result["blocks"]["PC_BT"]["rows"]))


def test_default_mapping_is_the_t10_layout():
    mapping = khuon.default_mapping("don_hang")
    assert (mapping["sheet"], mapping["header_row"]) == ("Export", 1) and mapping["columns"]["sales_before_discount"] == "Sales Before Discount"
    assert khuon.normalize_mapping(mapping) == mapping and len(khuon.mapping_sha(mapping)) == 64


def test_renamed_file_with_mapping_gives_same_numbers(tmp_path):
    rows = [ROW("D1", "a", 1000), ROW("D2", "b", 500, moq="N")]
    standard = load_small(_book(tmp_path, "a.xlsx", "Export", [], COLUMNS, rows), date(2026, 11, 30), "2026-11")
    # file khác: sheet "Don hang", 2 dòng tựa, tên cột khác, cờ "Có / Không", kênh "Off"
    renamed = [f"Cột {i}" for i in range(len(COLUMNS))]
    other_rows = [[*row[:9], "Off", "Có" if row[10] == "Y" else "Không", "Không", row[12]] for row in rows]
    mapping = {**khuon.default_mapping("don_hang"), "sheet": "Don hang", "header_row": 3,
               "columns": {field: renamed[COLUMNS.index(column)] for field, column in khuon.default_mapping("don_hang")["columns"].items()
                           if column in COLUMNS},
               "values": {"moq_flag": {"Có": "Y", "Không": "N"}, "is_free_item": {"Có": "Y", "Không": "N"}, "channel": {"Off": "Kênh Off Premise"}}}
    other = load_small(_book(tmp_path, "b.xlsx", "Don hang", [["Báo cáo"], []], renamed, other_rows), date(2026, 11, 30), "2026-11",
                     mapping=mapping)
    assert _summary(other) == _summary(standard) and other["reconciliation"]["counted_lines"] == 2


@pytest.mark.parametrize("cell", ["384,00", "1.234", "1.234,5", "87000"])
def test_number_text_always_stops_never_guessed(tmp_path, cell):
    # Ca xấu (nhóm kỹ thuật 17/11): "384,00" người gõ ý 384.000 ⇒ đọc thành 384 là sai ×1000.: ô CHỮ dạng số kiểu nào cũng dừng, người sửa file
    with pytest.raises(ValueError, match="không phải số") as stopped:
        load_raw(_book(tmp_path, "m.xlsx", "Export", [], COLUMNS, [ROW("D1", "a", cell)]), date(2026, 11, 30), "2026-11")
    assert "dòng Excel 2" in str(stopped.value) and cell not in str(stopped.value)


def test_bad_mapping_shape_stops():
    base = khuon.default_mapping("don_hang")
    missing = {**base, "columns": {k: v for k, v in base["columns"].items() if k != "invoiced_at"}}
    with pytest.raises(ValueError, match="invoiced_at"):
        khuon.normalize_mapping(missing)
    with pytest.raises(ValueError, match="moq_flag"):
        khuon.normalize_mapping({**base, "values": {"moq_flag": {"Có": "Yes"}}})       # cờ chỉ ghép về Y / N
    with pytest.raises(ValueError, match="khoá lạ number_style"):
        khuon.normalize_mapping({**base, "number_style": "viet"})


def test_mapping_changes_data_version():
    from kpi_agent.backend import main
    files, code, cutoff = {"RAW DATA.xlsx": "aa"}, "c1", date(2026, 10, 31)
    plain = main._data_version(files, code, cutoff)
    assert main._data_version(files, code, cutoff, mapping_sha=None) == plain                       # không ghép riêng ⇒ công thức cũ
    other = {**khuon.default_mapping("don_hang"), "columns": {**khuon.default_mapping("don_hang")["columns"], "total_pieces": "QUANTITY PIECE"}}
    assert main._data_version(files, code, cutoff, mapping_sha=khuon.mapping_sha(other)) != plain


# ---------- Bước 2.3: ghép thử ⇒ câu kiểm ⇒ lưu ⇒ tải lên file cùng bố cục dùng lại (đầu–cuối, RAW T10 thật đổi tên cột) ----------
RAW_T10 = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
needs_data = pytest.mark.skipif(not RAW_T10.exists(), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
BIN = {**KEY, "Content-Type": "application/octet-stream"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    folder = tmp_path_factory.mktemp("ghep")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        # bậc 3a P2: các ca ghép thử ở file này tải file tự dựng vài dòng (có Bia, không có Trà 365 / mã Homey / ngành Thịt) ⇒ backend chạy với bản
        # luật thử (luật thật + cho qua đúng 3 giá trị đó); RAW thật tháng 10 có đủ cả ba nên số của bản lúc bật không đổi
        mp.setattr(main, "WORKFLOW", write_raw_workflow_allowing(folder, *SMALL_FILE_LACKS))
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        mp.setattr(main, "UPLOAD_DIR", folder / "uploads")
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


@pytest.fixture(scope="module")
def renamed_t10(tmp_path_factory):
    """RAW T10 thật: sheet "Don hang", 2 dòng tựa, mọi cột đổi tên "C<n>" (không tên nào đã biết) ⇒ bố cục mới hẳn. Trả (đường file, bảng ghép đúng)."""
    source = openpyxl.load_workbook(RAW_T10, read_only=True, data_only=True)
    rows = source["Export"].iter_rows(values_only=True)
    header = list(next(rows))
    target = openpyxl.Workbook(write_only=True)
    sheet = target.create_sheet("Don hang")
    sheet.append(["Báo cáo đơn hàng"]); sheet.append([])
    sheet.append([f"C{i}" for i in range(len(header))])
    for row in rows:
        sheet.append(list(row))
    source.close()
    path = tmp_path_factory.mktemp("doi_ten") / "raw_doi_ten.xlsx"
    target.save(path)
    columns = {field: f"C{header.index(column)}" for field, column in khuon.default_mapping("don_hang")["columns"].items()}
    return path, {"sheet": "Don hang", "header_row": 3, "columns": columns, "values": {}}


def _answer_from_file(path, question) -> int:
    """Người thật mở file gốc, nhìn dòng tiêu đề (dòng 3): cột doanh số trước CK của T10 là "C26" ⇒ cột thứ 27 (A = 1). Không dựa vào bảng ghép."""
    assert "cột thứ mấy" in question
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        header = next(book["Don hang"].iter_rows(min_row=3, max_row=3, values_only=True))
        return list(header).index("C26") + 1
    finally:
        book.close()


@needs_data
def test_new_layout_needs_confirmed_mapping_then_upload_gives_same_money(client, renamed_t10):
    path, mapping = renamed_t10
    body = path.read_bytes()
    upload_params = {"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-31"}
    first = client.post("/api/system/upload", params=upload_params, content=body, headers=BIN)
    assert first.status_code == 422 and "ghép cột trước" in first.text                 # bố cục mới ⇒ không tính khi chưa ghép, lời chỉ đường ghép
    guessed = client.post("/api/system/bo-cuc", params={"bang": "don_hang"}, content=body, headers=BIN)
    assert guessed.status_code == 422 and "sheet_so + dong_tieu_de" in guessed.text      # 0 tên cột đã biết ⇒ máy không đoán, xin người chỉ dòng
    layout = client.post("/api/system/bo-cuc", params={"bang": "don_hang", "sheet_so": "1", "dong_tieu_de": "3"}, content=body, headers=BIN).json()
    assert layout["sheet_masked"] and all(column.get("masked") for column in layout["columns"]) and len(layout["columns"]) == 38   # U1: không trả "C<n>"
    # người ghép theo VỊ TRÍ (tên bị che): sheet số 1, cột số = vị trí cột chuẩn của T10
    mapping = {**mapping, "sheet": 1, "columns": {field: int(column[1:]) + 1 for field, column in mapping["columns"].items()}}
    request = {"sha_file": layout["sha_file"], "bang": "don_hang", "ghep": mapping, "ky": "2026-10", "ngay_chot": "2026-10-31"}
    tried = client.post("/api/system/ghep-thu", json=request, headers=KEY).json()
    assert tried["ok"] is True and tried["doi_soat"]["counted_lines"] == 24125 and tried["doi_soat"]["file_total_matches"] is True
    # ca xấu: trả lời sai ⇒ 409, không lưu; câu kiểm dùng MỘT lần
    wrong = client.post("/api/system/ghep-xac-nhan", json={"ma": tried["cau_kiem"]["ma"], "tra_loi": -1, "nguoi": "Giám sát thử"}, headers=KEY)
    again = client.post("/api/system/ghep-xac-nhan", json={"ma": tried["cau_kiem"]["ma"], "tra_loi": -1, "nguoi": "Giám sát thử"}, headers=KEY)
    assert wrong.status_code == 409 and "chưa đúng" in wrong.text and again.status_code == 409 and "hết hạn" in again.text
    assert client.post("/api/system/upload", params=upload_params, content=body, headers=BIN).status_code == 422   # vẫn chưa có ghép
    tried = client.post("/api/system/ghep-thu", json=request, headers=KEY).json()
    confirmed = client.post("/api/system/ghep-xac-nhan", headers=KEY, json={
        "ma": tried["cau_kiem"]["ma"], "tra_loi": _answer_from_file(path, tried["cau_kiem"]["cau"]), "nguoi": "Giám sát thử"})
    assert confirmed.status_code == 200, confirmed.text[:300]
    uploaded = client.post("/api/system/upload", params=upload_params, content=body, headers=BIN)
    assert uploaded.status_code == 200, uploaded.text[:300]
    data = uploaded.json()
    assert data["team_total"] == 152_360_000 and data["doi_soat"]["counted_lines"] == 24125          # cùng tiền như file chuẩn
    assert data["data_version"] != client.app.state.data_version                                   # ghép khác ⇒ bản khác


def test_try_mapping_bad_inputs(client):
    assert client.post("/api/system/ghep-thu", json={"sha_file": "../x"}, headers=KEY).status_code == 422
    assert client.post("/api/system/ghep-thu", json={"sha_file": "a" * 64, "bang": "don_hang"}, headers=KEY).status_code == 404
    assert client.post("/api/system/ghep-xac-nhan", json={"ma": "x", "tra_loi": 1}, headers=KEY).status_code == 422   # thiếu người


def test_new_division_shown_by_code_and_needs_explicit_pass(client, tmp_path):
    # Bước 2.4: ngành mới ⇒ ghep-thu trả MÃ CHE + số dòng (không chữ — U1), vẫn có câu kiểm; xác nhận thiếu cho_qua ⇒ 409; đủ ⇒ lưu, ngành vào accepted_values
    rows = [["SM1", f"D{i}", "Invoiced", date(2026, 10, 2), 1, "Snack <i>bí mật</i>" if i == 0 else "Seasoning", "x", "a", 1000 + i,
             "Kênh Off Premise", "Y", "N", datetime(2026, 10, 3, 9)] for i in range(3)]
    rows[2][5] = "Beer"       # luật kỳ T10 bỏ Bia ở PC_BT ⇒ file phải có ngành Bia (giới hạn đã biết m4 — giữ)
    path = _book(tmp_path, "moi.xlsx", "Export", [], COLUMNS, rows)
    layout = client.post("/api/system/bo-cuc", params={"bang": "don_hang"}, content=path.read_bytes(), headers=BIN).json()
    request = {"sha_file": layout["sha_file"], "bang": "don_hang", "ghep": khuon.default_mapping("don_hang"), "ky": "2026-10", "ngay_chot": "2026-10-31"}
    tried = client.post("/api/system/ghep-thu", json=request, headers=KEY)
    assert tried.status_code == 200 and "bí mật" not in tried.text and "Snack" not in tried.text
    body = tried.json()
    assert body["ok"] is False and body["gia_tri_moi"] == [{"truong": "sub_div", "ma": "moi_1", "so_dong": 1}] and body["cau_kiem"], body
    # 17/11: tóm tắt cột cho người ghép — giá trị đã biết hiện nguyên, chữ lạ chỉ hiện độ dài (U1: "bí mật" không lọt ở trên), số không có mẫu
    summary = {column["vi_tri"]: column for column in body["tom_tat_cot"]}
    assert len(summary) == len(COLUMNS) and summary[COLUMNS.index("L1") + 1]["mau"] == ["Kênh Off Premise"]
    sub_div = summary[COLUMNS.index("SUB DIV") + 1]
    assert sub_div["kieu"] == "chu" and sub_div["so_gia_tri_khac"] == 3 and sub_div["mau"] == ["Beer", "Seasoning", "‹chữ 19 ký tự›"]
    money = summary[COLUMNS.index("Sales Before Discount") + 1]
    assert (money["kieu"], money["ty_le_trong"], money["mau"]) == ("so", 0.0, [])
    answer = COLUMNS.index("Sales Before Discount") + 1          # người nhìn dòng tiêu đề file: cột thứ 9
    missing = client.post("/api/system/ghep-xac-nhan", json={"ma": body["cau_kiem"]["ma"], "tra_loi": answer, "nguoi": "Giám sát thử"}, headers=KEY)
    assert missing.status_code == 409 and "cho_qua" in missing.text
    body = client.post("/api/system/ghep-thu", json=request, headers=KEY).json()
    assert body["canh_bao"] and "chưa kiểm được nghĩa cột tiền" in body["canh_bao"][0]     # file không có cột sau CK ⇒ nói rõ
    confirm = {"ma": body["cau_kiem"]["ma"], "tra_loi": answer, "nguoi": "Giám sát thử", "cho_qua": ["moi_1"]}
    # 17/11: cho qua mà không ghi lý do ⇒ 422, câu kiểm CHƯA bị tiêu ⇒ ghi lý do rồi gửi lại cùng mã được
    no_reason = client.post("/api/system/ghep-xac-nhan", headers=KEY, json={**confirm, "ghi_chu": "  "})
    assert no_reason.status_code == 422 and "ghi_chu" in no_reason.text
    saved = client.post("/api/system/ghep-xac-nhan", headers=KEY, json={**confirm, "ghi_chu": "ngành Snack mới mở tháng này"})
    assert saved.status_code == 200, saved.text[:300]
    from kpi_agent.backend import store
    with store.connect(client.app.state.store) as conn:
        stored = store.latest_mapping(conn, "don_hang", saved.json()["van_tay_bo_cuc"])
    assert stored["mapping"]["accepted_values"] == {"sub_div": ["Snack <i>bí mật</i>"]}


# Bước 2.4 — lỗi NGHĨA khi ghép (2.1–2.3, 1 + 2 + 3): tự đối chiếu trong file không bắt được ----------
def _t10_header():
    book = openpyxl.load_workbook(RAW_T10, read_only=True, data_only=True)
    try:
        return [str(name) for name in next(book["Export"].iter_rows(max_row=1, values_only=True))]
    finally:
        book.close()


@needs_data
@pytest.mark.parametrize("renamed", [False, True])
def test_money_column_mapped_to_after_discount_is_red(renamed, renamed_t10):
    # Ca C 1: sales_before_discount ghép nhầm sang "Sales After Discount" — dòng Total + câu kiểm đều qua nếu không có kiểm nghĩa
    header = _t10_header()
    if not renamed:
        mapping = {**khuon.default_mapping("don_hang"), "columns": {**khuon.default_mapping("don_hang")["columns"],
                                                                   "sales_before_discount": "Sales After Discount"}}
        path, match = RAW_T10, "cùng một cột|cột chuẩn của trường sales_after_discount"   # cột sau CK mặc định vẫn ghép ⇒ trùng cột
    else:
        path, base = renamed_t10
        swapped = {**base["columns"], "sales_before_discount": f"C{header.index('Sales After Discount')}",
                   "sales_after_discount": f"C{header.index('Sales Before Discount')}"}   # đổi tên hết: người ghép đảo 2 cột tiền
        mapping, match = {**base, "columns": swapped}, "trước chiết khấu nhỏ hơn sau chiết khấu"
    with pytest.raises(ValueError, match=match):
        load_raw(path, date(2026, 10, 31), "2026-10", mapping=mapping)


@needs_data
@pytest.mark.parametrize("renamed", [False, True])
def test_moq_and_free_item_flags_swapped_is_red(renamed, renamed_t10):
    # Ca C 2: đổi chỗ cờ MOQ ↔ hàng tặng — T10 thật: MOQ Y 99,2 %, hàng tặng Y 1,9 % ⇒ đổi chỗ ⇒ MOQ Y 1,9 % < 50 % ⇒ đỏ (cả khi đổi tên hết cột)
    header = _t10_header()
    if not renamed:
        columns = {**khuon.default_mapping("don_hang")["columns"], "moq_flag": "FREE ITEM FLAG", "is_free_item": "MOQ_FLAG"}
        path, mapping = RAW_T10, {**khuon.default_mapping("don_hang"), "columns": columns}
    else:
        path, base = renamed_t10
        mapping = {**base, "columns": {**base["columns"], "moq_flag": f"C{header.index('FREE ITEM FLAG')}", "is_free_item": f"C{header.index('MOQ_FLAG')}"}}
    # còn tên DMS ⇒ luật "cột chuẩn của trường khác" bắt trước; đổi tên hết ⇒ tỉ lệ cờ / hàng tặng có tiền bắt
    with pytest.raises(ValueError, match="cột chuẩn của trường" if not renamed else "tỉ lệ cờ"):
        load_raw(path, date(2026, 10, 31), "2026-10", mapping=mapping)


def test_two_fields_on_the_same_column_is_red():
    base = khuon.default_mapping("don_hang")
    with pytest.raises(ValueError, match="cùng một cột"):
        khuon.normalize_mapping({**base, "columns": {**base["columns"], "sales_after_discount": "Sales Before Discount"}})


@needs_data
def test_case_c_m1_wrong_money_column_is_refused_by_try_mapping(client):
    # Ca C M1 vòng 1 qua API: ghép trước CK ⇒ "Sales After Discount" ⇒ trước: ok True, xác nhận 200, upload 145.640.000. Nay ghep-thu ok False, nêu lý do
    layout = client.post("/api/system/bo-cuc", params={"bang": "don_hang"}, content=RAW_T10.read_bytes(), headers=BIN).json()
    base = khuon.default_mapping("don_hang")
    wrong = {**base, "columns": {**base["columns"], "sales_before_discount": "Sales After Discount"}}
    reply = client.post("/api/system/ghep-thu", headers=KEY, json={"sha_file": layout["sha_file"], "bang": "don_hang", "ghep": wrong,
                                                                  "ky": "2026-10", "ngay_chot": "2026-10-31"})
    assert reply.status_code == 422 and "cùng một cột" in reply.text and "cau_kiem" not in reply.text   # bị từ chối ở khuôn ghép, không ra câu kiểm
    # chỉ đổi cột trước CK mà bỏ ghép cột sau CK (người ghép xoá dòng) ⇒ vẫn bị luật "cột chuẩn của trường khác" chặn ở bước đọc
    lone = {**wrong, "columns": {key: value for key, value in wrong["columns"].items() if key != "sales_after_discount"}}
    reply = client.post("/api/system/ghep-thu", headers=KEY, json={"sha_file": layout["sha_file"], "bang": "don_hang", "ghep": lone,
                                                                  "ky": "2026-10", "ngay_chot": "2026-10-31"}).json()
    assert reply["ok"] is False and reply["cau_kiem"] is None and "cột chuẩn của trường sales_after_discount" in reply["chan"]


@needs_data
def test_case_c_m1r_money_column_left_out_is_stopped_unless_declared(client, renamed_t10):
    # M1-r (MAJOR 17/11): file đổi tên hết, CÓ đủ hai cột tiền; người ghép trước CK ⇒ cột sau CK (C28) và BỎ TRỐNG sau CK + chiết khấu
    # ⇒ bất biến hai cột tiền không chạy ⇒ trước đây ok + cảnh báo chung ⇒ 145.640.000 lặng lẽ. Nay: chặn, nêu vị trí cột trước CK thật (C26 = cột 27).
    path, mapping = renamed_t10
    layout = client.post("/api/system/bo-cuc", params={"bang": "don_hang", "sheet_so": "1", "dong_tieu_de": "3"}, content=path.read_bytes(), headers=BIN).json()
    columns = {field: column for field, column in mapping["columns"].items() if field not in ("sales_after_discount", "discount_value")}
    wrong = {**mapping, "columns": {**columns, "sales_before_discount": "C28"}}
    request = {"sha_file": layout["sha_file"], "bang": "don_hang", "ghep": wrong, "ky": "2026-10", "ngay_chot": "2026-10-31"}
    stopped = client.post("/api/system/ghep-thu", json=request, headers=KEY).json()
    assert stopped["ok"] is False and stopped["cau_kiem"] is None and "cột thứ 27" in stopped["chan"] and "khong_co_cot_sau_ck" in stopped["chan"]
    # lối thoát có chủ đích: khai không có cột sau CK ⇒ có câu kiểm + cảnh báo RIÊNG; xác nhận thiếu lý do ⇒ 422 (câu kiểm chưa tiêu), có lý do ⇒ lưu
    declared = client.post("/api/system/ghep-thu", json={**request, "khong_co_cot_sau_ck": True}, headers=KEY).json()
    assert declared["cau_kiem"] and "bạn khai file không có cột doanh số sau chiết khấu" in declared["canh_bao"][0]
    confirm = {"ma": declared["cau_kiem"]["ma"], "tra_loi": 29, "nguoi": "Giám sát thử"}
    no_reason = client.post("/api/system/ghep-xac-nhan", json=confirm, headers=KEY)
    assert no_reason.status_code == 422 and "ghi_chu" in no_reason.text
    assert client.post("/api/system/ghep-xac-nhan", json={**confirm, "ghi_chu": "file này chỉ có một cột tiền"}, headers=KEY).status_code == 200
    # ghép ĐÚNG mà bỏ trống sau CK ⇒ không có cột nghi (tiền vẫn đúng) ⇒ cảnh báo "một cột tiền", không chặn
    right = client.post("/api/system/ghep-thu", json={**request, "ghep": {**mapping, "columns": columns}}, headers=KEY).json()
    assert right["ok"] is True and "file chỉ có một cột tiền" in right["canh_bao"][0]
    assert client.post("/api/system/ghep-thu", json={**request, "khong_co_cot_sau_ck": "có"}, headers=KEY).status_code == 422


@needs_data
def test_known_layout_new_division_passed_then_upload_works_and_old_file_keeps_version(client, tmp_path):
    # (MAJOR 17/11 — đường "mọi tháng"): bố cục T10 chuẩn + ngành mới ⇒ cho qua ở ghep-xac-nhan ⇒ tải lên PHẢI qua (trước: vẫn 422
    # vì bố cục đã biết bỏ qua ghép đã lưu). File T10 cũ (không có ngành đó) tải lại ⇒ data_version KHÔNG đổi.
    source = openpyxl.load_workbook(RAW_T10, read_only=True, data_only=True)
    rows = [list(row) for row in source["Export"].iter_rows(values_only=True)]
    source.close()
    at = {name: i for i, name in enumerate(rows[0])}
    changed = 0
    for row in rows[1:]:
        if changed < 20 and row[at["SM CODE"]] and row[at["FREE ITEM FLAG"]] == "N" and row[at["SUB DIV"]] == "Seasoning":
            row[at["SUB DIV"]], changed = "Bakery", changed + 1
    book = openpyxl.Workbook(write_only=True)
    sheet = book.create_sheet("Export")
    for row in rows:
        sheet.append(row)
    path = tmp_path / "nganh_moi.xlsx"
    book.save(path)
    upload_params = {"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-31"}
    refused = client.post("/api/system/upload", params=upload_params, content=path.read_bytes(), headers=BIN)
    assert refused.status_code == 422 and "sub_div: 1 giá trị / 20 dòng" in refused.text and "ghep-thu" in refused.text   # m5: lời chỉ đường
    assert "Bakery" not in refused.text                                                                                   # U1
    layout = client.post("/api/system/bo-cuc", params={"bang": "don_hang"}, content=path.read_bytes(), headers=BIN).json()
    assert layout["known"] is True
    request = {"sha_file": layout["sha_file"], "bang": "don_hang", "ghep": khuon.default_mapping("don_hang"), "ky": "2026-10", "ngay_chot": "2026-10-31"}
    tried = client.post("/api/system/ghep-thu", json=request, headers=KEY).json()
    assert tried["gia_tri_moi"] == [{"truong": "sub_div", "ma": "moi_1", "so_dong": 20}]
    saved = client.post("/api/system/ghep-xac-nhan", headers=KEY, json={"ma": tried["cau_kiem"]["ma"], "tra_loi": at["Sales Before Discount"] + 1,
                                                                          "nguoi": "Giám sát thử", "cho_qua": ["moi_1"], "ghi_chu": "ngành mới tháng này"})
    assert saved.status_code == 200, saved.text[:300]
    uploaded = client.post("/api/system/upload", params=upload_params, content=path.read_bytes(), headers=BIN)
    assert uploaded.status_code == 200, uploaded.text[:300]
    old = client.post("/api/system/upload", params=upload_params, content=RAW_T10.read_bytes(), headers=BIN)
    assert old.status_code == 200 and old.json()["data_version"] == client.app.state.data_version and old.json()["team_total"] == 152_360_000
    assert uploaded.json()["data_version"] != old.json()["data_version"]
