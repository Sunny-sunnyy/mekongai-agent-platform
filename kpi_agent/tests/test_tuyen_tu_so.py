"""Giai đoạn 2a-1 (P1) — bảng TUYẾN đọc từ sổ theo dõi qua công thức đọc (`core/load_tuyen.py`, khuôn `tuyen` ở `core/khuon.py`).

Hai lớp ca: (1) file sổ TỰ DỰNG nhỏ (đúng hình sheet MCP: tiêu đề dòng 7, khối đếm đầu sheet lặp tên cột) cho từng LOẠI lệch khuôn; (2) sổ tháng 10
THẬT — sàn ghi trước khi viết code: bảng tuyến đọc từ sổ = bảng trong file JSON đang dùng (2.501 cặp, 14 mã nhân viên, kênh và ngày viếng thăm giống hệt).
"""
import json
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from kpi_agent.core import khuon, load_tuyen
from kpi_agent.core.load_raw import load_mcp_json

REPO = Path(__file__).resolve().parents[2]
REAL_BOOK = REPO / "sample_data/thang_10/Tracking.xlsx"
REAL_JSON = REPO / "sample_data/thang_10/mcp_trang_sep_3110.json"
needs_data = pytest.mark.skipif(not (REAL_BOOK.exists() and REAL_JSON.exists()), reason="thiếu sổ tháng 10 / file tuyến thật")
OFF, ON = "Kênh Off Premise", "Kênh On Premise"
# Dòng tiêu đề thật của sheet MCP: 3 khối đếm (4 cột, cách nhau bằng cột trống) rồi khối chi tiết từ cột 40
HEADER = {1: "Position code", 2: "SM code", 3: "SM name", 4: "Total", 14: "Position code", 15: "SM code", 16: "SM name", 17: "Total",
          27: "Position code", 28: "SM code", 29: "SM name", 30: "Total", 40: "Position code", 41: "SM code", 42: "SM name", 43: "L1",
          44: "Outlet_code", 45: "Outlet_name", 46: "Address", 47: "Ward", 48: "Frequency", 49: "Tuần viếng thăm", 50: "Viếng thăm", 51: "VIP MCH"}
ROWS = [("SM01", OFF, 1001, "F4", "Thứ 2"), ("SM01", ON, 1002, "F2", "Thứ 3"), ("SM02", OFF, 2001, "F4", "Thứ 2"), ("SM02", OFF, "0207", "F8", "Thứ 5")]


def make_book(path: Path, rows=ROWS, header: dict = HEADER, header_row: int = 7, sheet: str = "MCP", extra_sheets: tuple = ("9_KPI",)) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for column, name in header.items():
        ws.cell(row=header_row, column=column, value=name)
    for offset, (sm, channel, outlet, frequency, visit) in enumerate(rows, start=1):
        for column, value in ((41, sm), (43, channel), (44, outlet), (48, frequency), (50, visit)):
            ws.cell(row=header_row + offset, column=column, value=value)
    for name in extra_sheets:
        wb.create_sheet(name).cell(row=1, column=1, value="kết quả sếp tính")
    wb.save(path)
    return path


def formula(**changes) -> dict:
    return {**khuon.default_mapping("tuyen"), **changes}


def codes_of(error) -> list:
    return [(problem["ma"], problem["truong"], problem["so_dong"]) for problem in error.value.problems]


# ---------- đọc đúng ----------

def test_reads_routes_in_the_shape_the_core_already_uses(tmp_path):
    result = load_tuyen.read_routes(make_book(tmp_path / "so.xlsx"), formula())
    assert result["mcp"] == {"SM01": {"1001": {"channel": OFF, "visit_day": "Thứ 2"}, "1002": {"channel": ON, "visit_day": "Thứ 3"}},
                             "SM02": {"2001": {"channel": OFF, "visit_day": "Thứ 2"}, "0207": {"channel": OFF, "visit_day": "Thứ 5"}}}
    summary = result["summary"]
    assert (summary["rows_read"], summary["employees"], summary["outlets"], summary["by_channel"]) == (4, 2, 4, {OFF: 3, ON: 1})
    assert summary["sample_first"][0] == {"sm_code": "SM01", "outlet_code": "1001", "channel": OFF} and len(summary["sample_last"]) == 3


def test_outlet_code_number_cell_becomes_text_and_text_cell_keeps_leading_zero(tmp_path):
    # ô SỐ (sổ thật: mọi mã điểm bán là ô số) ⇒ "1001", không "1001.0"; ô CHỮ "0207" giữ số 0 đầu. Giới hạn: số 0 đầu không giữ được từ ô số.
    rows = [("SM01", OFF, 1001.0, "F4", "Thứ 2"), ("SM01", OFF, "0207", "F4", "Thứ 2"), (" SM01 ", OFF, " 1003 ", "F4", "Thứ 2")]
    assert load_tuyen.read_routes(make_book(tmp_path / "so.xlsx", rows), formula())["mcp"] == {"SM01": {
        "1001": {"channel": OFF, "visit_day": "Thứ 2"}, "0207": {"channel": OFF, "visit_day": "Thứ 2"}, "1003": {"channel": OFF, "visit_day": "Thứ 2"}}}


def test_rows_of_codes_outside_the_team_are_dropped_and_listed_per_code(tmp_path):
    # LUẬT DỮ LIỆU: mã không có trong file chỉ tiêu ⇒ không thuộc đội ⇒ bỏ dòng, nêu TỪNG mã + số dòng (gõ sai mã một người thật cũng hiện ở đây).
    # Kênh lạ của mã ngoài đội không làm dừng; dòng trắng đếm riêng; người trong đội không có điểm bán nào cũng được nêu.
    rows = [*ROWS, ("SM15", "Kênh Vãng lai", 9001, "F12", ""), ("SM15", OFF, 9002, "F4", ""), ("SM0l", OFF, 1003, "F4", ""), (None, None, None, None, None)]
    result = load_tuyen.read_routes(make_book(tmp_path / "so.xlsx", rows), formula(), team_codes={"SM01", "SM02", "SM03"})
    assert set(result["mcp"]) == {"SM01", "SM02"} and result["summary"]["rows_blank_skipped"] == 1
    assert result["summary"]["dropped_outside_team"] == [{"sm_code": "SM0l", "rows": 1}, {"sm_code": "SM15", "rows": 2}]
    assert result["summary"]["team_codes_without_routes"] == 1


def test_unknown_channel_of_a_team_member_still_stops(tmp_path):
    rows = [*ROWS, ("SM02", "Kênh Vãng lai", 2009, "F4", "")]
    with pytest.raises(load_tuyen.RouteProblems) as error:
        load_tuyen.read_routes(make_book(tmp_path / "so.xlsx", rows), formula(), team_codes={"SM01", "SM02"})
    assert codes_of(error) == [("gia_tri_la", "channel", 1)]


def test_channel_values_can_be_mapped_by_the_formula(tmp_path):
    rows = [("SM01", "Off", 1001, "F4", "Thứ 2"), ("SM01", "On", 1002, "F4", "Thứ 2")]
    result = load_tuyen.read_routes(make_book(tmp_path / "so.xlsx", rows), formula(values={"channel": {"Off": OFF, "On": ON}}))
    assert result["summary"]["by_channel"] == {OFF: 1, ON: 1}


# ---------- lệch khuôn ⇒ dừng có mã (mỗi LOẠI một ca) ----------

@pytest.mark.parametrize("book, mapping, expected", [
    ({"sheet": "Tuyen"}, {}, [("thieu_sheet", None, 0)]),                                              # không có sheet công thức nêu
    ({}, {"sheet": 5}, [("thieu_sheet", None, 0)]),                                                    # số thứ tự sheet vượt quá số sheet
    ({}, {"header_row": 4000}, [("thieu_dong_tieu_de", None, 0)]),                                     # dòng tiêu đề nằm ngoài sheet
    ({}, {"columns": {"sm_code": 41, "channel": 43, "outlet_code": 99}}, [("cot_khong_xac_dinh", "outlet_code", 0)]),      # vị trí cột vượt bảng
    ({}, {"columns": {"sm_code": "SM code", "channel": 43, "outlet_code": 44}}, [("ten_cot_lap_phai_ghep_theo_vi_tri", "sm_code", 0)]),   # tên LẶP 4 lần
    ({}, {"columns": {"sm_code": 41, "channel": 43, "outlet_code": "Mã điểm bán"}}, [("cot_khong_xac_dinh", "outlet_code", 0)]),         # tên không có
    ({"rows": [*ROWS, ("SM03", "Kênh Lạ", 3001, "F4", "")]}, {}, [("gia_tri_la", "channel", 1)]),      # kênh lạ ngoài tập chuẩn, ngoài danh sách bỏ
    ({"rows": [*ROWS, ("SM01", OFF, 1001, "F4", "")]}, {}, [("diem_ban_lap_trong_mot_nhan_vien", "outlet_code", 1)]),
    ({"rows": [*ROWS, ("SM03", OFF, None, "F4", "")]}, {}, [("o_bat_buoc_trong", "outlet_code", 1)]),
    ({"rows": []}, {}, [("khong_co_dong", None, 0)]),
    ({"rows": [(None, OFF, 5001, "F4", "")]}, {}, [("o_bat_buoc_trong", "sm_code", 1)]),               # mã trống mà ô khác có chữ ⇒ không phải dòng trắng
    ({"sheet": "MCP ", "rows": []}, {}, [("khong_co_dong", None, 0)]),                                 # tên sheet có dấu cách cuối vẫn tìm thấy sheet
])
def test_book_that_does_not_fit_the_formula_stops_with_a_code(tmp_path, book, mapping, expected):
    with pytest.raises(load_tuyen.RouteProblems) as error:
        load_tuyen.read_routes(make_book(tmp_path / "so.xlsx", **book), formula(**mapping))
    assert codes_of(error) == expected
    assert "Kênh Lạ" not in str(error.value)   # lời lỗi không lặp chữ của file (rào U1)


def test_same_outlet_under_two_employees_stops(tmp_path):
    # Một cửa hàng đếm cho hai người là đường tăng số điểm bán / thưởng của cả hai ⇒ dừng có mã (kế hoạch mục 9 — vòng 1: bản đầu chỉ đếm)
    rows = [("SM01", OFF, 1001, "F4", ""), ("SM02", OFF, 1001, "F4", ""), ("SM02", OFF, 2002, "F4", "")]
    with pytest.raises(load_tuyen.RouteProblems) as error:
        load_tuyen.read_routes(make_book(tmp_path / "so.xlsx", rows), formula())
    assert codes_of(error) == [("diem_ban_thuoc_hai_nguoi", "outlet_code", 1)]


def test_real_looking_codes_with_dots_are_shown_in_samples(tmp_path):
    # Mã nhân viên thật có dấu chấm (dạng "14AB.CD0123") ⇒ câu kiểm phải in được để người xác nhận nhìn thấy cột mình ghép
    rows = [("14AB.CD0123", OFF, 1001, "F4", "")]
    summary = load_tuyen.read_routes(make_book(tmp_path / "so.xlsx", rows), formula())["summary"]
    assert summary["sample_first"][0]["sm_code"] == "14AB.CD0123"


def test_unknown_values_are_counted_not_echoed(tmp_path):
    rows = [*ROWS, ("SM03", "Kênh Lạ", 3001, "F4", ""), ("SM04", "Kênh Lạ", 4001, "F4", ""), ("SM04", "Kênh Khác", 4002, "F4", "")]
    with pytest.raises(load_tuyen.RouteProblems) as error:
        load_tuyen.read_routes(make_book(tmp_path / "so.xlsx", rows), formula())
    assert error.value.problems == [{"ma": "gia_tri_la", "truong": "channel", "so_dong": 3, "so_gia_tri": 2, "so_ma_sm": 2}]


def test_unique_column_name_still_works_and_formula_carries_no_date(tmp_path):
    path = make_book(tmp_path / "so.xlsx")
    by_name = formula(columns={"sm_code": 41, "channel": "L1", "outlet_code": "Outlet_code"})   # tên chỉ có MỘT lần ⇒ ghép theo tên được
    assert load_tuyen.read_routes(path, by_name)["summary"]["outlets"] == 4
    # Công thức KHÔNG mang ngày: ngày chốt là tham số người tải gửi, không đọc từ file ⇒ khoá meta bị từ chối, câu kiểm không có ô ngày
    assert "data_as_of" not in load_tuyen.read_routes(path, formula())["summary"]
    with pytest.raises(ValueError, match="bảng ghép sai khuôn"):
        khuon.normalize_mapping(formula(meta={"ngay_so_lieu": "B3"}))


# ---------- công thức theo VỊ TRÍ cột chỉ hợp lệ khi vân tay bố cục khớp ----------

def test_position_formula_is_offered_only_for_the_known_layout(tmp_path):
    same = load_tuyen.read_routes(make_book(tmp_path / "so.xlsx"), formula())["fingerprint"]
    assert same == khuon.KNOWN_LAYOUTS["tuyen"] and load_tuyen.known_formula(same) == khuon.default_mapping("tuyen")
    shifted_header = {(column + 1 if column >= 40 else column): name for column, name in HEADER.items()}   # bên xuất chèn một cột ⇒ vị trí lệch
    for path, changes in ((tmp_path / "chen_cot.xlsx", {"header": shifted_header}), (tmp_path / "doi_dong.xlsx", {"header_row": 8}),
                          (tmp_path / "doi_ten.xlsx", {"header": {**HEADER, 44: "Mã điểm bán"}})):
        mapping = formula(header_row=changes.get("header_row", 7))
        other = khuon.layout_fingerprint("MCP", mapping["header_row"], load_tuyen.header_of(make_book(path, **changes), mapping))
        assert other != khuon.KNOWN_LAYOUTS["tuyen"] and load_tuyen.known_formula(other) is None, path.name


# ---------- ĐẦU VÀO ⇄ ĐÁP ÁN: không mở sheet kết quả nào của sổ ----------

def test_no_cell_of_a_result_sheet_is_read(tmp_path):
    # Làm HỎNG phần Ô của sheet kết quả "9_KPI" trong gói .xlsx (dòng kích thước ở đầu còn nguyên — thư viện đọc Excel đọc dòng đó của MỌI sheet lúc mở
    # file): bộ đọc tuyến vẫn đọc được ⇒ nó không đọc ô nào của sheet đó; mở thẳng sheet đó thì lỗi ⇒ sheet hỏng thật.
    good = make_book(tmp_path / "so.xlsx")
    broken = tmp_path / "so_hong_sheet_ket_qua.xlsx"
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(broken, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet2.xml":
                data = data[:data.index(b"<sheetData>") + len(b"<sheetData>")] + b"<row r=\"1\"><c r=\"A1\"><v>hong"
            target.writestr(item, data)
    assert load_tuyen.read_routes(broken, formula())["summary"]["outlets"] == 4
    with pytest.raises(Exception):
        load_tuyen.read_routes(broken, formula(sheet="9_KPI", header_row=1, columns={"sm_code": 1, "channel": 2, "outlet_code": 3}))


# ---------- khuôn + hình công thức ----------

def test_formula_shape_is_validated_and_stable():
    assert [field["field"] for field in khuon.schema("tuyen") if field["required"]] == ["sm_code", "outlet_code", "channel"]
    assert khuon.mapping_sha(khuon.default_mapping("tuyen")) == khuon.mapping_sha(json.loads(json.dumps(khuon.default_mapping("tuyen"))))
    for bad in ({"rows": {"bo_dong_trong": ["visit_day"]}, "columns": {"sm_code": 41, "channel": 43, "outlet_code": 44}},   # trường chưa ghép cột
                {"rows": {"bieu_thuc": "x > 1"}}, {"rows": {"bo_dong_trong": ["sm_code"], "bo_gia_tri": {"channel": ["x"]}}},
                {"columns": {"sm_code": 41, "channel": 43}}):
        with pytest.raises(ValueError, match="bảng ghép sai khuôn"):
            khuon.normalize_mapping(formula(**bad))
    with pytest.raises(ValueError, match="bảng ghép sai khuôn"):   # khoá rows chỉ dành cho bảng tuyến — bảng đơn hàng không đổi hình
        khuon.normalize_mapping({**khuon.default_mapping("don_hang"), "rows": {"bo_dong_trong": ["sm_code"]}})


def test_order_table_mapping_hash_is_unchanged():
    # Bản tính đang dùng băm cách ghép của file đơn hàng vào `data_version` — thêm bảng tuyến không được làm đổi mã băm đó (đo trên b9a62f28 trước khi sửa)
    assert khuon.mapping_sha(khuon.default_mapping("don_hang")) == "334c697675e1a104ce32ddfaf304bba7bf24aa07fee23fb88f8feeadb4c55643"


# ---------- SÀN trên sổ tháng 10 thật ----------

@needs_data
def test_october_book_gives_the_same_routes_as_the_file_in_use():
    from kpi_agent.core.load_data import load_targets
    team = {str(code).strip() for code in load_targets(REPO / "sample_data/thang_10/Chi tieu NVBH.xlsx")["employees"]}
    result = load_tuyen.read_routes(REAL_BOOK, khuon.default_mapping("tuyen"), team_codes=team)
    in_use = load_mcp_json(REAL_JSON)                              # bảng lõi dựng từ file đang dùng — cùng tầng (kênh đã về chữ chuẩn của lõi)
    assert (len(result["mcp"]), sum(len(outlets) for outlets in result["mcp"].values())) == (14, 2501)
    assert result["mcp"] == in_use                                 # so SÂU: cùng mã, cùng điểm bán, cùng ô, cùng chữ từng ô
    assert json.dumps(result["mcp"], sort_keys=True, ensure_ascii=False) == json.dumps(in_use, sort_keys=True, ensure_ascii=False)
    assert result["fingerprint"] == khuon.KNOWN_LAYOUTS["tuyen"]
    summary = result["summary"]
    assert summary["by_channel"] == {OFF: 1803, ON: 698} and summary["rows_blank_skipped"] == 1 and summary["team_codes_without_routes"] == 0
    (dropped,) = summary["dropped_outside_team"]                   # đúng MỘT mã ngoài đội, một dòng
    assert dropped["rows"] == 1 and dropped["sm_code"] not in result["mcp"]


def test_samples_never_echo_free_text_put_in_a_code_field(tmp_path):
    # Ghép nhầm cột chữ tự do (tên người, địa chỉ) vào trường mã ⇒ câu kiểm in "<không hợp lệ>", không lặp chữ của file ra cửa (rào U1)
    from openpyxl import load_workbook
    path = make_book(tmp_path / "so.xlsx")
    wb = load_workbook(path)
    for row in range(8, 12):
        wb["MCP"].cell(row=row, column=42, value=f"Nguyễn Văn {row}")
        wb["MCP"].cell(row=row, column=46, value=f"12 đường Trần Phú, phường {row}")
    wb.save(path)
    summary = load_tuyen.read_routes(path, formula(columns={"sm_code": 42, "channel": 43, "outlet_code": 46}))["summary"]
    assert {line["sm_code"] for line in summary["sample_first"]} == {load_tuyen.NOT_A_CODE}
    assert {line["outlet_code"] for line in summary["sample_last"]} == {load_tuyen.NOT_A_CODE}
    with pytest.raises(load_tuyen.RouteProblems) as error:          # mọi dòng thành "mã ngoài đội" ⇒ không còn dòng nào ⇒ dừng; lời lỗi không có chữ của file
        load_tuyen.read_routes(path, formula(columns={"sm_code": 42, "channel": 43, "outlet_code": 44}), team_codes={"SM01"})
    assert codes_of(error) == [("khong_co_dong", None, 0)] and "Nguyễn" not in str(error.value)
