"""Giai đoạn 2a-1 (P2) — ba đường /bo-cuc ⇒ /ghep-thu ⇒ /ghep-xac-nhan nhận bảng `tuyen` (sổ theo dõi tải lên làm ĐẦU VÀO).

Backend thật (file đơn hàng + chỉ tiêu tháng 10 thật); sổ thật tháng 10 cho đường "công thức lưu sẵn", sổ tự dựng bố cục khác cho đường "cần công thức đọc".
Đường của file đơn hàng (bang = don_hang) không đổi — lưới của nó là test_ghep_cot.py.
"""
from datetime import date
from pathlib import Path

import pytest

from kpi_agent.core import khuon
from kpi_agent.core.load_data import load_targets
from test_tuyen_tu_so import HEADER, OFF, ON, make_book

ROOT = Path(__file__).resolve().parents[2]
BOOK = ROOT / "sample_data/thang_10/Tracking.xlsx"
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
needs_data = pytest.mark.skipif(not (BOOK.exists() and TARGETS.exists() and (ROOT / "sample_data/thang_10/RAW DATA.xlsx").exists()),
                                reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
BIN = {**KEY, "Content-Type": "application/octet-stream"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    folder = tmp_path_factory.mktemp("tuyen")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")   # đường file đơn hàng (đường raw) với luật tháng 10 THẬT
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        mp.setattr(main, "UPLOAD_DIR", folder / "uploads")
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


def _try(client, sha: str, **more):
    return client.post("/api/system/ghep-thu", json={"sha_file": sha, "bang": "tuyen", **more}, headers=KEY)


@needs_data
def test_october_book_goes_through_with_the_stored_formula(client):
    before = client.get("/api/meta").json()["data_version"]
    layout = client.post("/api/system/bo-cuc", params={"bang": "tuyen"}, content=BOOK.read_bytes(), headers=BIN)
    assert layout.status_code == 200, layout.text[:200]
    layout = layout.json()
    assert (layout["sheet"], layout["header_row"], layout["known"], layout["fingerprint"]) == ("MCP", 7, True, khuon.KNOWN_LAYOUTS["tuyen"])
    tried = _try(client, layout["sha_file"])
    assert tried.status_code == 200, tried.text[:300]
    body = tried.json()
    summary = body["tom_tat"]
    assert body["ok"] is True and body["nguon_cong_thuc"] == "luu_san" and body["cong_thuc"] == khuon.default_mapping("tuyen")
    assert (summary["employees"], summary["outlets"], summary["by_channel"]) == (14, 2501, {OFF: 1803, ON: 698})
    assert [item["rows"] for item in summary["dropped_outside_team"]] == [1] and summary["team_codes_without_routes"] == 0
    assert len(summary["sample_first"]) == 3 and "mcp" not in body and "cot_ma_diem_ban" not in body      # bảng tuyến + đáp án câu kiểm không ra cửa
    wrong = client.post("/api/system/ghep-xac-nhan", json={"ma": body["cau_kiem"]["ma"], "tra_loi": 41, "nguoi": "Giám sát thử"}, headers=KEY)
    assert wrong.status_code == 409 and "mã điểm bán" in wrong.json()["refused"]
    again = _try(client, layout["sha_file"]).json()
    saved = client.post("/api/system/ghep-xac-nhan", json={"ma": again["cau_kiem"]["ma"], "tra_loi": 44, "nguoi": "Giám sát thử"}, headers=KEY)
    assert saved.status_code == 200 and saved.json()["van_tay_bo_cuc"] == khuon.KNOWN_LAYOUTS["tuyen"]
    assert client.get("/api/meta").json()["data_version"] == before                                        # thử + xác nhận không đổi bản dữ liệu


def real_rows() -> list[tuple]:
    """Mọi dòng tuyến của người TRONG ĐỘI trong sổ tháng 10 thật, dạng dòng của `make_book`: (mã nhân viên, kênh, mã điểm bán, tần suất, ngày viếng thăm).
    Sổ thử dựng từ các dòng này (đổi một phần) ⇒ đối chiếu được với file đơn hàng thật của backend."""
    from kpi_agent.core import load_tuyen
    team = {str(code).strip() for code in load_targets(TARGETS)["employees"]}
    mcp = load_tuyen.read_routes(BOOK, khuon.default_mapping("tuyen"), team)["mcp"]
    return [(sm, info["channel"], outlet, "F4", info["visit_day"]) for sm, outlets in mcp.items() for outlet, info in outlets.items()]


@pytest.fixture(scope="module")
def routes() -> list[tuple]:
    return real_rows()


@needs_data
def test_book_with_another_layout_needs_a_formula_then_reuses_the_confirmed_one(client, tmp_path, routes):
    short = {OFF: "Off", ON: "On"}
    rows = [(sm, short[channel], outlet, freq, visit) for sm, channel, outlet, freq, visit in routes] + [("NGOAIDOI", "Off", 9001, "F4", "")]
    shifted = {column + 2: name for column, name in HEADER.items()}      # bên xuất chèn hai cột đầu ⇒ mọi vị trí lệch
    path = make_book(tmp_path / "so_khac.xlsx", rows=[], header=shifted, header_row=9)
    from openpyxl import load_workbook
    wb = load_workbook(path)
    for offset, (sm, channel, outlet, _freq, visit) in enumerate(rows, start=1):
        for column, value in ((43, sm), (45, channel), (46, outlet), (52, visit)):
            wb["MCP"].cell(row=9 + offset, column=column, value=value)
    wb.save(path)
    layout = client.post("/api/system/bo-cuc", params={"bang": "tuyen", "sheet_so": "1", "dong_tieu_de": "9"}, content=path.read_bytes(), headers=BIN).json()
    assert layout["known"] is False
    needed = _try(client, layout["sha_file"])
    assert needed.status_code == 409 and needed.json()["code"] == "can_cong_thuc_doc"
    assert [field["field"] for field in needed.json()["khuon"]] == ["sm_code", "outlet_code", "channel", "visit_day"]
    formula = {"sheet": "MCP", "header_row": 9, "columns": {"sm_code": 43, "channel": 45, "outlet_code": 46, "visit_day": 52},
               "values": {"channel": {"Off": OFF, "On": ON}}, "rows": {"bo_dong_trong": ["sm_code"]}}
    unmapped = _try(client, layout["sha_file"], ghep={**formula, "values": {}}).json()
    assert unmapped["ok"] is False and [(p["ma"], p["truong"], p["so_dong"], p["so_gia_tri"]) for p in unmapped["loi"]] == [("gia_tri_la", "channel", 2501, 2)]
    assert _try(client, layout["sha_file"], ghep={**formula, "rows": {"bieu_thuc": "1"}}).status_code == 422       # công thức sai khuôn
    assert _try(client, layout["sha_file"], ghep="MCP").status_code == 422
    # công thức ghép NHẦM cột mã điểm bán (lấy cột tên điểm bán — trống) ⇒ dừng; ghép nhầm cột kênh sang cột ngày viếng thăm ⇒ dừng
    wrong_column = _try(client, layout["sha_file"], ghep={**formula, "columns": {**formula["columns"], "outlet_code": 47}}).json()
    assert wrong_column["ok"] is False
    tried = _try(client, layout["sha_file"], ghep=formula).json()
    assert tried["ok"] is True and tried["nguon_cong_thuc"] == "gui_kem" and tried["tom_tat"]["outlets"] == 2501
    assert tried["tom_tat"]["dropped_outside_team"] == [{"sm_code": "NGOAIDOI", "rows": 1}] and tried["tom_tat"]["canh_bao"] == []
    saved = client.post("/api/system/ghep-xac-nhan", json={"ma": tried["cau_kiem"]["ma"], "tra_loi": 46, "nguoi": "Giám sát thử"}, headers=KEY)
    assert saved.status_code == 200, saved.text[:200]
    reused = _try(client, layout["sha_file"]).json()                                                                # không gửi công thức ⇒ dùng cái đã xác nhận
    assert reused["ok"] is True and reused["nguon_cong_thuc"] == "da_xac_nhan" and reused["tom_tat"]["outlets"] == 2501


# vòng 2 (M1): tuyến ĐỌC ĐƯỢC nhưng SAI phải DỪNG — đối chiếu với file đơn hàng của bản đang dùng ----------

def _swap_people(rows):
    """Mọi dòng mang mã nhân viên HỢP LỆ nhưng sai người (xoay vòng mã trong đội) — dạng lỗi "ghép nhầm cột mã của khối khác"."""
    team = sorted({row[0] for row in rows})
    following = dict(zip(team, team[1:] + team[:1]))
    return [(following[sm], channel, outlet, freq, visit) for sm, channel, outlet, freq, visit in rows]


def _swap_channels(rows):
    return [(sm, ON if channel == OFF else OFF, outlet, freq, visit) for sm, channel, outlet, freq, visit in rows]


def _retype_one_person(rows):
    """Sổ ghi sai mã của MỘT người thật ở mọi dòng của người đó ⇒ người đó không còn điểm bán nào."""
    victim = sorted({row[0] for row in rows})[0]
    return [(sm + "X" if sm == victim else sm, channel, outlet, freq, visit) for sm, channel, outlet, freq, visit in rows]


def _share_one_outlet(rows):
    people = sorted({row[0] for row in rows})
    first = next(row for row in rows if row[0] == people[0])
    return [*rows, (people[1], first[1], first[2], first[3], first[4])]


@needs_data
@pytest.mark.parametrize("change, code", [(_swap_people, "tuyen_lech_don_hang"), (_swap_channels, "tuyen_lech_kenh"),
                                          (_retype_one_person, "nguoi_trong_doi_khong_co_tuyen"), (_share_one_outlet, "so_lech_khuon_tuyen")])
def test_readable_but_wrong_routes_stop_and_make_no_version(client, tmp_path, routes, change, code):
    before = set(client.app.state.versions)
    body = make_book(tmp_path / "so_sai.xlsx", rows=change(routes)).read_bytes()
    refused = _upload(client, body)
    assert refused.status_code == 422 and refused.json()["code"] == code, refused.text[:300]
    assert set(client.app.state.versions) == before                                    # KHÔNG ra bản dữ liệu mới
    layout = client.post("/api/system/bo-cuc", params={"bang": "tuyen"}, content=body, headers=BIN).json()
    tried = _try(client, layout["sha_file"]).json()                                    # đường thử công thức: cùng phép dừng ⇒ không có câu kiểm để xác nhận
    assert tried["ok"] is False and tried["cau_kiem"] is None, str(tried)[:300]


@needs_data
def test_table_comparison_has_teeth_and_full_routes_equal_startup(client, tmp_path, routes):
    # Phép so "số không đổi" phải có RĂNG (vòng 1, M2): một bản tải lên có dữ liệu KHÁC (cùng file đơn hàng, ngày chốt 20/10) phải cho bảng
    # KHÁC bản lúc bật qua chính `_table` — nếu `_table` mù mã bản thì ca này đỏ.
    base = _table(client)
    raw = (ROOT / "sample_data/thang_10/RAW DATA.xlsx").read_bytes()
    other = client.post("/api/system/upload", params={"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-20"}, content=raw, headers=BIN)
    assert other.status_code == 200, other.text[:300]
    different = _table(client, other.json()["data_version"])
    assert different != base and different["team_total"] != base["team_total"]
    same_book = _upload(client, make_book(tmp_path / "so_du_tuyen.xlsx", rows=routes).read_bytes())      # sổ thử ĐỦ tuyến ⇒ bằng bản lúc bật từng ô
    assert same_book.status_code == 200 and _table(client, same_book.json()["data_version"]) == base
    # Gỡ mọi điểm kênh On của MỘT người khỏi sổ: với ngưỡng 98 % theo kênh (siết lần 2) sổ như vậy bị TỪ CHỐI, không ra bản mới.
    # Ca "sửa NHỎ ⇒ qua, đúng một người đổi ô": `test_small_route_change_goes_through_and_moves_only_that_person` cuối file.
    victim = sorted({row[0] for row in routes if row[1] == ON})[0]
    fewer = [row for row in routes if not (row[0] == victim and row[1] == ON)]
    refused = _upload(client, make_book(tmp_path / "so_bot_tuyen.xlsx", rows=fewer).read_bytes())
    assert refused.status_code == 422 and refused.json()["code"] == "tuyen_lech_don_hang", refused.text[:300]


def test_route_stop_thresholds():
    from kpi_agent.backend import route_upload as ru
    cell = lambda with_orders, inside, same: {"outlets_with_orders": with_orders, "in_routes": inside, "same_channel": same}
    codes = lambda summary: ([p["ma"] for p in ru.route_stops(summary)[0]], len(ru.route_stops(summary)[1]))
    ok = {"team_codes_without_routes": 0}
    assert codes({**ok, "orders_vs_routes": {OFF: cell(1642, 1640, 1640), ON: cell(476, 474, 474)}}) == ([], 0)          # tháng 10 thật
    assert codes({**ok, "orders_vs_routes": {OFF: cell(1000, 979, 979)}}) == (["tuyen_lech_don_hang"], 0)                # 97,9 % < 98 % ⇒ dừng
    assert codes({**ok, "orders_vs_routes": {OFF: cell(1000, 980, 980)}}) == ([], 1)                                     # 98 % ⇒ qua + cảnh báo
    assert codes({**ok, "orders_vs_routes": {OFF: cell(1000, 994, 994)}}) == ([], 1) and codes({**ok, "orders_vs_routes": {OFF: cell(1000, 995, 995)}}) == ([], 0)
    # hai ca tuyến sai MỘT PHẦN đo ở vòng 2 (lọt ngưỡng 90 % cũ): ≈ 91,5 % cả hai kênh · kênh Off 95,5 %
    assert codes({**ok, "orders_vs_routes": {OFF: cell(1642, 1502, 1502), ON: cell(476, 436, 436)}})[0] == ["tuyen_lech_don_hang", "tuyen_lech_don_hang"]
    assert codes({**ok, "orders_vs_routes": {OFF: cell(1642, 1568, 1568), ON: cell(476, 474, 474)}})[0] == ["tuyen_lech_don_hang"]
    assert codes({**ok, "orders_vs_routes": {ON: cell(1000, 1000, 980)}}) == ([], 0)                                     # lệch kênh đúng 2 % ⇒ qua
    assert codes({**ok, "orders_vs_routes": {ON: cell(1000, 1000, 979)}}) == (["tuyen_lech_kenh"], 0)                    # trên 2 % ⇒ dừng
    assert codes({**ok, "orders_vs_routes": {OFF: cell(476, 32, 32), ON: cell(1642, 114, 0)}})[0] == ["tuyen_lech_don_hang", "tuyen_lech_don_hang", "tuyen_lech_kenh"]
    assert codes({"team_codes_without_routes": 2, "orders_vs_routes": {OFF: cell(10, 10, 10)}})[0] == ["nguoi_trong_doi_khong_co_tuyen"]
    assert codes({**ok, "orders_vs_routes": None})[0] == ["khong_doi_chieu_duoc_voi_don_hang"]
    assert codes({**ok, "orders_vs_routes": {OFF: cell(0, 0, 0)}}) == ([], 0)                                             # kênh không có đơn nào ⇒ không xét
    # theo TỪNG NGƯỜI gộp hai kênh: dừng khi CẢ HAI — tỉ lệ < 95 % VÀ ≥ 5 điểm có đơn ngoài tuyến; lời chỉ nêu số người
    person = lambda with_orders, inside: {"outlets_with_orders": with_orders, "in_routes": inside}
    fine = {OFF: cell(1642, 1640, 1640), ON: cell(476, 474, 474)}
    by_person = lambda *people: codes({**ok, "orders_vs_routes": fine, "orders_vs_routes_by_person": list(people)})[0]
    assert by_person(person(127, 125), person(126, 125), person(171, 170)) == []                 # tháng 10 thật: nhỏ nhất 98,43 %, nhiều nhất 2 điểm
    assert by_person(person(120, 60), person(127, 125)) == ["tuyen_lech_don_hang_theo_nguoi"]     # nửa số điểm của một người sang người khác
    assert by_person(person(37, 33)) == [] and by_person(person(100, 95)) == []                   # < 95 % nhưng chỉ 4 điểm · đủ 5 điểm nhưng đúng 95 %
    assert by_person(person(100, 94)) == ["tuyen_lech_don_hang_theo_nguoi"]
    stop = ru.route_stops({**ok, "orders_vs_routes": fine, "orders_vs_routes_by_person": [person(120, 60), person(100, 90)]})[0][0]
    assert (stop["so_ma_sm"], stop["so_dong"]) == (2, 70)


@needs_data
def test_missing_file_and_wrong_table_are_refused(client):
    assert _try(client, "a" * 64).status_code == 404
    assert client.post("/api/system/ghep-thu", json={"sha_file": "a" * 64, "bang": "tuyen_khac"}, headers=KEY).status_code in (404, 422)


# ---------- P3: loại tải lên `so_tracking` — bản dữ liệu lấy bảng tuyến từ sổ ----------

def _upload(client, body: bytes, **params):
    return client.post("/api/system/upload", params={"loai": "so_tracking", "ky": "2026-10", **params}, content=body, headers=BIN)


def _table(client, version: str | None = None) -> dict:
    """Bảng thưởng 8 KPI của MỘT bản qua đường hệ thống — đường này đi qua `_for_version` (nhận mã bản). KHÔNG dùng /api/team-bonus: đường đó không
    đọc ?data_version= và luôn trả bản lúc bật ⇒ phép so "số không đổi" của bản đầu so bản lúc bật với chính nó (vòng 1, M2).
    Bỏ khối `source` (nguồn số: tên file, mã băm — khác nhau giữa hai bản là đúng).
    """
    reply = client.get("/api/system/bonus-table", params={"data_version": version} if version else {}, headers=KEY)
    assert reply.status_code == 200, reply.text[:200]
    return {key: value for key, value in reply.json()["table"].items() if key != "source"}


@needs_data
def test_uploading_the_october_book_gives_a_version_with_the_same_money(client):
    meta = client.get("/api/meta").json()
    base_table = _table(client)
    reply = _upload(client, BOOK.read_bytes())
    assert reply.status_code == 200, reply.text[:300]
    body = reply.json()
    # bản MỚI (nguồn tuyến khác file) nhưng SỐ không đổi một đồng: bảng thưởng đội từng ô giống bản lúc bật
    assert body["data_version"] != meta["data_version"] and body["da_co_san"] is False and body["cutoff"] == meta["cutoff"]
    assert _table(client, body["data_version"]) == base_table
    assert body["team_total"] == base_table["team_total"] == 152_360_000 and len(base_table["lines"]) == 112      # 14 người × 8 KPI, từng ô
    assert "Tracking.xlsx" not in " ".join(body["files_sha256"]) and "mcp_trang_sep_3110.json" not in body["files_sha256"]   # file tuyến = sổ đã lưu theo sha
    # câu kiểm số đi kèm MỖI lần tải, kể cả khi dùng công thức lưu sẵn
    route = body["tuyen_tu_so"]
    assert route["nguon_cong_thuc"] == "luu_san" and (route["tom_tat"]["employees"], route["tom_tat"]["outlets"]) == (14, 2501)
    assert [item["rows"] for item in route["tom_tat"]["dropped_outside_team"]] == [1]
    coverage = route["tom_tat"]["orders_vs_routes"]
    assert set(coverage) == {OFF, ON}
    for cell in coverage.values():                                 # điểm bán có đơn nằm trong tuyến đều TRÙNG kênh với đơn (bắt đổi chéo On ↔ Off)
        assert 0 < cell["in_routes"] <= cell["outlets_with_orders"] and cell["same_channel"] == cell["in_routes"], coverage
    again = _upload(client, BOOK.read_bytes()).json()
    assert again["data_version"] == body["data_version"] and again["da_co_san"] is True and again["tuyen_tu_so"]["tom_tat"]["outlets"] == 2501
    assert client.get("/api/meta").json()["data_version"] == meta["data_version"]                 # bản lúc bật không bị thay


@needs_data
def test_route_book_refusals_have_codes(client, tmp_path):
    other_period = _upload(client, BOOK.read_bytes(), ky="2026-11")
    assert other_period.status_code == 409 and other_period.json()["code"] == "chua_co_don_hang_cua_ky"
    unknown_layout = make_book(tmp_path / "bo_cuc_la.xlsx", header={column + 1: name for column, name in HEADER.items()})
    needed = _upload(client, unknown_layout.read_bytes())
    assert needed.status_code == 409 and needed.json()["code"] == "can_cong_thuc_doc"
    strangers = make_book(tmp_path / "toan_nguoi_la.xlsx")          # đúng bố cục đã biết nhưng không dòng nào của người trong đội
    refused = _upload(client, strangers.read_bytes())
    assert refused.status_code == 422 and refused.json()["code"] == "so_lech_khuon_tuyen" and "khong_co_dong" in refused.json()["refused"]
    assert "SM01" not in refused.text                               # lời từ chối không lặp chữ của file
    assert _upload(client, b"khong phai xlsx").status_code == 422
    old_kind = client.post("/api/system/upload", params={"loai": "tracking", "ky": "2026-10", "ngay_chot": "2026-10-31"}, content=BOOK.read_bytes(),
                           headers=BIN)
    assert old_kind.status_code == 422                              # loại `tracking` của đường tính cũ vẫn bị từ chối ở đường raw


def test_team_list_must_equal_the_target_file_codes():
    from kpi_agent.backend import route_upload
    assert route_upload.team_mismatch(["A", "B"], ["A", "B", "C"]) and route_upload.team_mismatch(["A", "X"], ["A", "B"])
    assert route_upload.team_mismatch(None, ["A"]) and not route_upload.team_mismatch(["B", " A "], ["A", "B"])


def test_order_coverage_counts_per_order_channel():
    from kpi_agent.backend import route_upload
    mcp = {"S1": {"1": {"channel": OFF}, "2": {"channel": ON}}, "S2": {"9": {"channel": OFF}}}
    lines = [{"SM CODE": "S1", "OUTLET CODE": 1, "L1": OFF}, {"SM CODE": "S1", "OUTLET CODE": 1, "L1": OFF}, {"SM CODE": "S1", "OUTLET CODE": 2, "L1": OFF},
             {"SM CODE": "S1", "OUTLET CODE": 3, "L1": ON}, {"SM CODE": "X9", "OUTLET CODE": 9, "L1": OFF}]
    assert route_upload.order_coverage(lines, mcp, {"S1", "S2"}) == {
        OFF: {"outlets_with_orders": 2, "in_routes": 2, "same_channel": 1},     # điểm 2 có trong tuyến nhưng tuyến ghi On ⇒ lệch kênh
        ON: {"outlets_with_orders": 1, "in_routes": 0, "same_channel": 0}}       # điểm 3 không có trong tuyến; đơn của người ngoài đội không đếm


@needs_data
def test_target_file_uploaded_on_a_book_version_keeps_team_in_step(client):
    # Bản lấy tuyến từ sổ ⇒ tải file chỉ tiêu lên bản đó: "đội" của bảng tuyến do lõi rút LẠI từ file chỉ tiêu vừa tải rồi so bất biến (đội == mã của file
    # chỉ tiêu) ⇒ dựng được, bảng thưởng không đổi. GIỚI HẠN: ca file chỉ tiêu ĐỔI đội (thêm / bớt người) chưa thử qua cửa — mã mới bị rào mã che từ chối,
    # file bớt người làm backend lỗi ở `queries.bonus_lines` (có từ trước, cả trên bản không dùng sổ) — xem 20/11.
    book_version = _upload(client, BOOK.read_bytes()).json()["data_version"]
    same = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-10", "data_version": book_version}, content=TARGETS.read_bytes(),
                       headers=BIN)
    assert same.status_code == 200, same.text[:300]
    assert _table(client, same.json()["data_version"]) == _table(client, book_version)


@needs_data
def test_book_version_has_the_same_extra_bonus_and_routes_for_every_screen(client):
    # Lỗi tôi tự bắt 20/11 sau "bản cuối" đầu: thưởng thêm / màn điểm bán / danh sách đơn từng đọc tuyến bằng `load_mcp_json(mcp_path)` — với bản lấy
    # tuyến từ sổ, mcp_path là file .xlsx ⇒ thưởng thêm của bản đó thành LỜI LỖI lặng lẽ (mất 19.026.300 đ). Mọi màn nay đọc tuyến qua `main._routes_of`.
    from kpi_agent.backend import main
    version = _upload(client, BOOK.read_bytes()).json()["data_version"]
    base = client.get("/api/system/extra-bonus", headers=KEY)
    new = client.get("/api/system/extra-bonus", params={"data_version": version}, headers=KEY)
    assert base.status_code == 200 and new.status_code == 200, new.text[:300]
    base, new = base.json(), new.json()
    assert new["lines"] == base["lines"] and len(base["lines"]) == 126                      # 9 khoản × 14 người, từng dòng giống hệt
    money = lambda body: sum(line["amount"] for line in body["lines"])
    assert money(new) == money(base) == 19_026_300
    state = client.app.state
    assert main._routes_of(state.versions[version].workflow) == main._routes_of(state.workflow)   # bảng tuyến mọi màn dùng: sổ == file lúc bật


def test_extra_bonus_reason_keeps_only_text_written_by_code():
    # Lỗi lạ của Python / hệ điều hành không ra cửa (chữ thô, đường dẫn tuyệt đối, nội dung file) — chỉ lời chung + mã cố định; lời của lõi giữ nguyên
    import json
    from kpi_agent.backend import main
    from kpi_agent.core import load_tuyen
    try:
        b"PK\x03\x04\xc9\xff".decode("utf-8")
    except UnicodeDecodeError as error:
        decode_error = error
    try:
        json.loads("{hỏng")
    except json.JSONDecodeError as error:
        json_error = error
    strange = [decode_error, json_error, FileNotFoundError(2, "No such file or directory", "/home/ai_do/kpi_agent/data/x.json"), KeyError("T03"),
               RuntimeError("Traceback (most recent call last)")]
    for error in strange:
        text = main._extra_bonus_reason(error)
        assert text == main.EXTRA_BONUS_UNREADABLE and not any(word in text for word in ("codec", "Traceback", "/home", "Errno")), text
    assert main._extra_bonus_reason(ValueError("chưa có file luật thưởng thêm cho kỳ 2026-11")) == "chưa có file luật thưởng thêm cho kỳ 2026-11"
    problems = load_tuyen.RouteProblems([{"ma": "thieu_sheet", "truong": None, "so_dong": 0}])
    assert "thieu_sheet" in main._extra_bonus_reason(problems)


@needs_data
def test_upload_reply_says_whether_extra_bonus_was_computed(client):
    body = _upload(client, BOOK.read_bytes()).json()
    assert body["thuong_them"] == "co" and body["thuong_them_ly_do"] is None
    same = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-10"}, content=TARGETS.read_bytes(), headers=BIN).json()
    assert same["thuong_them"] == "co"                                 # mọi loại tải lên đều có ô này (đây: bản lúc bật được dùng lại)

# ---------- commit nhỏ sau vòng 2: lưu ý đi theo BẢN + ca tuyến sai MỘT PHẦN dựng bằng sổ qua cửa ----------

def _issues(client, version: str | None = None) -> dict:
    reply = client.post("/api/tool/get_data_issues", params={"data_version": version} if version else {}, json={"args": {}}, headers=KEY)
    assert reply.status_code == 200, reply.text[:200]
    return reply.json()


def test_version_notes_say_what_was_dropped_and_what_is_near_the_threshold():
    from kpi_agent.backend import route_upload as ru
    assert ru.version_notes({"dropped_outside_team": [], "rows_blank_skipped": 0, "canh_bao": []}) == []
    notes = ru.version_notes({"dropped_outside_team": [{"sm_code": "LA1", "rows": 2}, {"sm_code": "LA2", "rows": 1}], "rows_blank_skipped": 4,
                              "canh_bao": ["Kênh X: 980 / 1000 điểm bán"]})
    assert [note["level"] for note in notes] == ["info", "warning"]
    assert "3 dòng của 2 mã nhân viên" in notes[0]["message"] and "4 dòng trống" in notes[0]["message"] and "LA1" not in notes[0]["message"]
    assert notes[1]["message"].endswith("Kênh X: 980 / 1000 điểm bán")
    # sát ngưỡng theo người: dưới 95 % nhưng chưa đủ 5 điểm ngoài tuyến ⇒ qua + cảnh báo chỉ nêu SỐ người
    person = lambda with_orders, inside: {"outlets_with_orders": with_orders, "in_routes": inside}
    fine = {OFF: {"outlets_with_orders": 1642, "in_routes": 1640, "same_channel": 1640}}
    stops, warnings = ru.route_stops({"team_codes_without_routes": 0, "orders_vs_routes": fine,
                                      "orders_vs_routes_by_person": [person(37, 33), person(20, 18), person(127, 125)]})
    assert stops == [] and len(warnings) == 1 and warnings[0].startswith("2 nhân viên")
    # cảnh báo theo người từ 3 điểm có đơn ngoài tuyến (kể cả khi tỉ lệ còn cao); 2 điểm (sổ thật tháng 10: nhiều nhất 2) ⇒ không
    by_person = lambda *people: ru.route_stops({"team_codes_without_routes": 0, "orders_vs_routes": fine, "orders_vs_routes_by_person": list(people)})
    assert by_person(person(127, 125), person(171, 169)) == ([], [])
    assert [len(part) for part in by_person(person(127, 124), person(171, 169))] == [0, 1] and by_person(person(127, 124))[1][0].startswith("1 nhân viên")
    stops, warnings = by_person(person(120, 60), person(127, 122))                   # người đã bị DỪNG không đếm lại vào cảnh báo
    assert [stop["ma"] for stop in stops] == ["tuyen_lech_don_hang_theo_nguoi"] and warnings[0].startswith("1 nhân viên")


@needs_data
def test_book_version_carries_its_route_notes_to_every_tool_result(client):
    version = _upload(client, BOOK.read_bytes()).json()
    assert version["tuyen_tu_so"]["tom_tat"]["canh_bao"] == []                               # sổ thật tháng 10: không cảnh báo
    marker = "bảng tuyến lấy từ sổ theo dõi tải lên"
    base, new = _issues(client), _issues(client, version["data_version"])
    assert not any(marker in issue["message"] for issue in base["issues"])                   # bản lúc bật (tuyến từ file cố định) không có dòng này
    mine = [issue for issue in new["issues"] if marker in issue["message"]]
    assert [(issue["level"], issue["scope"]) for issue in mine] == [("info", "ca_doi")]      # thứ bị bỏ: thông tin, cấp đội; không có cảnh báo
    assert "1 dòng của 1 mã nhân viên" in mine[0]["message"] and "1 dòng trống" in mine[0]["message"]
    assert new["issue_count"] == base["issue_count"] + 1 and mine[0]["message"] in new["data_flags"] and mine[0]["message"] in version["data_flags"]
    # bản dựng TRÊN bản lấy tuyến từ sổ (tải chỉ tiêu lên bản đó) vẫn dùng bảng tuyến ấy ⇒ vẫn mang lưu ý
    onto = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-10", "data_version": version["data_version"]},
                       content=TARGETS.read_bytes(), headers=BIN).json()
    assert mine[0]["message"] in _issues(client, onto["data_version"])["data_flags"]


def _ordered_outlets(client, cutoff: date | None = None) -> dict[tuple[str, str], str]:
    """(mã nhân viên, mã điểm bán) CÓ ĐƠN ⇒ kênh của đơn, ở file đơn hàng của bản lúc bật tới ngày chốt (mặc định: ngày chốt của bản) — cùng cách đọc với
    phép đối chiếu của cửa tải lên."""
    from kpi_agent.backend import main
    from kpi_agent.core.load_raw import period_rules, read_raw_lines
    state = client.app.state
    lines, _late = read_raw_lines(main.runner.repo_path(main.runner.node_param(state.workflow, "read_raw", "path")), cutoff or state.ctx.cutoff,
                                  state.ctx.period, invoice_required_status=(), mapping=main.runner.node_param(state.workflow, "read_raw", "ghep"),
                                  period_rules=period_rules(state.ctx.rules["assumptions"]))
    return {(str(line["SM CODE"]).strip(), str(line["OUTLET CODE"]).strip()): line["L1"] for line in lines}


def _wrong_person_every_12th_row(rows, _ordered):
    """vòng 2, ca 1: ~8 % số dòng rải đều mang mã của người kế tiếp trong đội (đối chiếu ≈ 91,5 % — lọt ngưỡng 90 % cũ)."""
    team = sorted({row[0] for row in rows})
    following = dict(zip(team, team[1:] + team[:1]))
    return [(following[row[0]], *row[1:]) if index % 12 == 0 else row for index, row in enumerate(rows)]


def _half_of_one_person_to_another(rows, _ordered):
    """vòng 2, ca 2: nửa số điểm của MỘT người (người nhiều điểm nhất) gán sang người khác."""
    team = sorted({row[0] for row in rows}, key=lambda sm: (-sum(row[0] == sm for row in rows), sm))
    victim, other = team[0], team[1]
    own = [index for index, row in enumerate(rows) if row[0] == victim]
    moved = set(own[::2])
    return [(other, *row[1:]) if index in moved else row for index, row in enumerate(rows)]


def _twenty_ordered_outlets_of_one_person_to_another(rows, ordered):
    """Chỉ luật THEO NGƯỜI bắt được: 20 điểm có đơn kênh Off của một người sang người khác — theo kênh vẫn trên 98 %."""
    team = sorted({row[0] for row in rows}, key=lambda sm: (-sum(row[0] == sm for row in rows), sm))
    victim, other = team[0], team[1]
    moved = set([index for index, row in enumerate(rows) if row[0] == victim and row[1] == OFF and (row[0], str(row[2])) in ordered][:20])
    assert len(moved) == 20
    return [(other, *row[1:]) if index in moved else row for index, row in enumerate(rows)]


@needs_data
@pytest.mark.parametrize("change, code", [(_wrong_person_every_12th_row, "tuyen_lech_don_hang"), (_half_of_one_person_to_another, "tuyen_lech_don_hang"),
                                          (_twenty_ordered_outlets_of_one_person_to_another, "tuyen_lech_don_hang_theo_nguoi")])
def test_partly_wrong_routes_stop_at_the_upload_gate(client, tmp_path, routes, change, code):
    before = set(client.app.state.versions)
    refused = _upload(client, make_book(tmp_path / "so_sai_mot_phan.xlsx", rows=change(routes, _ordered_outlets(client))).read_bytes())
    assert refused.status_code == 422 and refused.json()["code"] == code, refused.text[:300]
    assert set(client.app.state.versions) == before
    assert not any(row[0] in refused.text for row in routes[:50])                           # lời từ chối không nêu mã nhân viên


@needs_data
def test_small_route_change_goes_through_and_moves_only_that_person(client, tmp_path, routes):
    # Sửa NHỎ (gỡ 4 điểm CÓ ĐƠN kênh On của một người) không phải "tuyến sai": qua cửa, chỉ ô của ĐÚNG người đó đổi. Kênh On còn 470 / 476
    # (98,74 % — khoảng cảnh báo 98–99,5 %) ⇒ cảnh báo theo kênh + cảnh báo theo người (4 điểm), và cảnh báo đi theo bản tới kết quả công cụ.
    ordered = _ordered_outlets(client)
    victim = sorted({row[0] for row in routes if row[1] == ON})[0]
    gone = set([index for index, row in enumerate(routes) if row[0] == victim and row[1] == ON and (row[0], str(row[2])) in ordered][:4])
    assert len(gone) == 4
    reply = _upload(client, make_book(tmp_path / "so_sua_nho.xlsx", rows=[row for index, row in enumerate(routes) if index not in gone]).read_bytes())
    assert reply.status_code == 200, reply.text[:300]
    warnings = reply.json()["tuyen_tu_so"]["tom_tat"]["canh_bao"]
    assert len(warnings) == 2 and "470 / 476" in warnings[0] and warnings[1].startswith("1 nhân viên"), warnings   # theo kênh + theo người (4 điểm ≥ 3)
    carried = [issue for issue in _issues(client, reply.json()["data_version"])["issues"] if warnings[0] in issue["message"]]
    assert [issue["level"] for issue in carried] == ["warning"]
    base, new = _table(client)["lines"], _table(client, reply.json()["data_version"])["lines"]
    changed = {line["employee_id"] for old, line in zip(base, new) if old != line}
    assert len(base) == len(new) and len(changed) == 1, changed


@needs_data
def test_new_order_file_on_a_book_version_is_checked_against_its_routes_again(client, tmp_path, routes):
    # Bản lấy tuyến từ sổ rồi tải file ĐƠN HÀNG khác lên bản đó: phép đối chiếu đơn ⇄ tuyến phải chạy LẠI với đơn mới (trước đây chỉ chạy lúc nhận sổ ⇒
    # đơn mới lệch tuyến vẫn ra tiền). Dựng: gỡ khỏi sổ vài điểm kênh On ĐÃ có đơn tới 10/10 (rải ≤ 3 điểm / người) sao cho với đơn cả tháng kênh On còn
    # từ 98 % (sổ qua, có cảnh báo) nhưng với cùng file đơn hàng chốt 10/10 (ít điểm có đơn hơn) thì dưới 98 % ⇒ phải DỪNG, không ra bản.
    full, early = _ordered_outlets(client), _ordered_outlets(client, date(2026, 10, 10))
    in_routes = {(row[0], str(row[2])) for row in routes}
    on_full, on_early = [key for key, channel in full.items() if channel == ON], [key for key, channel in early.items() if channel == ON]
    outside_full, outside_early = sum(key not in in_routes for key in on_full), sum(key not in in_routes for key in on_early)
    take = len(on_full) * 2 // 100 - outside_full          # số điểm gỡ nhiều nhất mà đơn cả tháng vẫn không dưới 98 %
    gone, per_person = set(), {}
    for index, row in enumerate(routes):
        if len(gone) < take and row[1] == ON and early.get((row[0], str(row[2]))) == ON and per_person.get(row[0], 0) < 3:
            gone.add(index)
            per_person[row[0]] = per_person.get(row[0], 0) + 1
    # điều kiện của ca, đo trên file thật: cả tháng không dưới 98 %, chốt 10/10 dưới 98 %
    assert len(gone) == take > 0 and (take + outside_early) * 100 / len(on_early) > 2, (take, outside_full, outside_early, len(on_full), len(on_early))
    book = _upload(client, make_book(tmp_path / "so_bot_9_diem.xlsx", rows=[row for index, row in enumerate(routes) if index not in gone]).read_bytes())
    assert book.status_code == 200, book.text[:300]
    before = set(client.app.state.versions)
    raw = (ROOT / "sample_data/thang_10/RAW DATA.xlsx").read_bytes()
    refused = client.post("/api/system/upload", params={"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-10", "data_version": book.json()["data_version"]},
                          content=raw, headers=BIN)
    assert refused.status_code == 422 and refused.json()["code"] == "tuyen_lech_don_hang", refused.text[:300]
    assert set(client.app.state.versions) == before
    # file đơn hàng chốt 20/10 lên bản lấy tuyến từ sổ ĐỦ tuyến ⇒ qua, và bản mới mang lưu ý của sổ
    whole = _upload(client, BOOK.read_bytes()).json()["data_version"]
    fine = client.post("/api/system/upload", params={"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-20", "data_version": whole}, content=raw, headers=BIN)
    assert fine.status_code == 200, fine.text[:300]
    assert any("bảng tuyến lấy từ sổ theo dõi tải lên" in flag for flag in fine.json()["data_flags"])


@needs_data
def test_five_ordered_outlets_moved_to_another_person_pass_with_a_person_warning(client, tmp_path, routes):
    # Ca của (20/11): 5 điểm kênh Off CÓ ĐƠN của một người gán sang người khác — theo kênh vẫn trên 99,5 %, theo người chưa tới mức dừng,
    # tiền của người đó lệch mà trước đây KHÔNG có cảnh báo nào. Nay: vẫn qua, kèm cảnh báo theo người (chỉ số người) đi theo bản.
    ordered = _ordered_outlets(client)
    team = sorted({row[0] for row in routes}, key=lambda sm: (-sum(row[0] == sm for row in routes), sm))
    victim, other = team[0], team[1]
    moved = set([index for index, row in enumerate(routes) if row[0] == victim and row[1] == OFF and (row[0], str(row[2])) in ordered][:5])
    assert len(moved) == 5
    reply = _upload(client, make_book(tmp_path / "so_gan_5_diem.xlsx", rows=[(other, *row[1:]) if index in moved else row
                                                                           for index, row in enumerate(routes)]).read_bytes())
    assert reply.status_code == 200, reply.text[:300]
    warnings = reply.json()["tuyen_tu_so"]["tom_tat"]["canh_bao"]
    assert len(warnings) == 1 and warnings[0].startswith("1 nhân viên") and victim not in warnings[0] and other not in warnings[0], warnings
    carried = [issue for issue in _issues(client, reply.json()["data_version"])["issues"] if warnings[0] in issue["message"]]
    assert [(issue["level"], issue["scope"]) for issue in carried] == [("warning", "ca_doi")]
    assert any(warnings[0] in flag for flag in reply.json()["data_flags"])


@needs_data
def test_files_uploaded_onto_a_book_version_report_the_rechecked_routes(client):
    # Tái hiện: tải đơn hàng / chỉ tiêu lên bản lấy tuyến từ sổ — phản hồi không mang câu kiểm đối chiếu lại ⇒ người tải không
    # thấy tỉ lệ mới. Nay mọi phản hồi của bản có sổ đều có ô tuyen_tu_so.tom_tat với tỉ lệ tính trên file đơn hàng CỦA BẢN ĐÓ.
    book = _upload(client, BOOK.read_bytes()).json()
    whole = book["tuyen_tu_so"]["tom_tat"]["orders_vs_routes"]
    onto = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-10", "data_version": book["data_version"]},
                       content=TARGETS.read_bytes(), headers=BIN).json()
    assert onto["tuyen_tu_so"]["tom_tat"]["orders_vs_routes"] == whole and onto["tuyen_tu_so"]["tom_tat"]["canh_bao"] == []      # ca thường: cùng đơn ⇒ cùng tỉ lệ
    raw = (ROOT / "sample_data/thang_10/RAW DATA.xlsx").read_bytes()
    cut = client.post("/api/system/upload", params={"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-20", "data_version": book["data_version"]},
                      content=raw, headers=BIN).json()
    early = cut["tuyen_tu_so"]["tom_tat"]["orders_vs_routes"]
    assert set(early) == set(whole) and all(0 < early[channel]["outlets_with_orders"] < whole[channel]["outlets_with_orders"] for channel in whole)
    plain = client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-10"}, content=TARGETS.read_bytes(), headers=BIN).json()
    assert "tuyen_tu_so" not in plain                                                                # bản không có sổ: không có ô này
