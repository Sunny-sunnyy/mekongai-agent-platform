"""Lớp đọc trọn một sheet RAW một lần (`core/raw_sheet.py`, 19/11/2026) — file .xlsx TỰ DỰNG nhỏ trong tmp_path + 1 ca trên RAW thật.

Giữ sàn: (1) không bao giờ dùng lại dòng của file CŨ sau khi nội dung file đổi (số tiền sai lặng); (2) dòng thô giữ nguyên như thư viện trả
(dòng trống, dòng tổng, thứ tự) — cổng đối soát + số "dòng Excel N" dựa vào đó; (3) cùng một file chỉ mở một lần dù nhiều phép tính / nhiều luồng hỏi.
Đếm lần mở bằng cách BỌC hàm mở file thật (gọi hàm thật, chỉ đếm) — không thay kết quả nào.
"""
import threading
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from kpi_agent.core import raw_sheet

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
RULES = ROOT / "kpi_agent/config/rules_2026_10_raw.yaml"
needs_data = pytest.mark.skipif(not RAW.exists(), reason="không có dữ liệu thật trên máy này")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv(raw_sheet.KEPT_ENV, raising=False)
    raw_sheet.forget_all()
    yield
    raw_sheet.forget_all()


@pytest.fixture
def opens(monkeypatch):
    """Danh sách tên file mỗi lần `raw_sheet` mở file thật."""
    seen = []
    real = raw_sheet.load_workbook

    def counting(source, *args, **kwargs):
        seen.append(Path(getattr(source, "name", source)).name)   # lớp nhớ mở từ byte trong bộ nhớ, tên file nằm ở thuộc tính `name`
        return real(source, *args, **kwargs)
    monkeypatch.setattr(raw_sheet, "load_workbook", counting)
    return seen


def write_book(path: Path, first_value="M01", header_row=1, blank_rows=()):
    """Sheet "Export": tiêu đề ở `header_row`, 6 dòng dữ liệu (dòng trong `blank_rows` để trống hẳn), dòng cuối ghi "Total"; thêm sheet "Khac"."""
    book = Workbook()
    sheet = book.active
    sheet.title = "Export"
    for column, name in enumerate(("SM CODE", "Sales", "Ghi chu"), start=1):
        sheet.cell(row=header_row, column=column, value=name)
    for offset in range(1, 7):
        row = header_row + offset
        if row in blank_rows:
            continue
        sheet.cell(row=row, column=1, value=first_value if offset == 1 else f"M{offset:02d}")
        sheet.cell(row=row, column=2, value=offset * 1000)
    sheet.cell(row=header_row + 7, column=3, value="Total")
    book.create_sheet("Khac").cell(row=1, column=1, value="x")
    book.save(path)
    return path


def direct_rows(path: Path, sheet="Export", min_row=1):
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        return tuple(book[sheet].iter_rows(min_row=min_row, values_only=True))
    finally:
        book.close()


def test_same_file_is_opened_once_and_rows_equal_a_direct_read(tmp_path, opens):
    path = write_book(tmp_path / "a.xlsx", header_row=3, blank_rows=(6,))
    names, rows = raw_sheet.sheet_rows(path, "Export")
    again_names, again = raw_sheet.sheet_rows(str(path), 1)                     # số thứ tự sheet 1-based = cùng sheet
    assert names == again_names == ["Export", "Khac"] and opens == ["a.xlsx"]
    assert rows is again and rows == direct_rows(path)                           # mọi dòng từ dòng 1, kể cả dòng trống + dòng Total
    assert rows[2][0] == "SM CODE" and rows[-1][2] == "Total" and not any(rows[5])
    assert rows[2:] == direct_rows(path, min_row=3)                              # cắt theo dòng tiêu đề = đọc từ dòng tiêu đề (số "dòng Excel N")
    assert all(isinstance(row, tuple) for row in rows)                           # nơi gọi không sửa được dòng dùng chung


def test_changed_content_under_the_same_name_is_read_again(tmp_path, opens):
    path = write_book(tmp_path / "a.xlsx", first_value="M01")
    assert raw_sheet.sheet_rows(path, "Export")[1][1][0] == "M01"
    write_book(path, first_value="DOI")                                          # cùng tên, cùng số dòng — chỉ đổi một ô
    assert raw_sheet.sheet_rows(path, "Export")[1][1][0] == "DOI" and opens == ["a.xlsx", "a.xlsx"]


def test_file_replaced_between_hashing_and_reading_never_puts_new_rows_under_the_old_hash(tmp_path, monkeypatch):
    """File bị thay ĐÚNG lúc lớp nhớ vừa băm xong, chưa đọc: dòng trả về phải là của nội dung ĐÃ BĂM; hỏi lại (nội dung mới) phải ra dòng mới."""
    path = write_book(tmp_path / "a.xlsx", first_value="CU")
    old_bytes = path.read_bytes()
    real = raw_sheet.load_workbook

    def replace_then_open(source, *args, **kwargs):
        write_book(path, first_value="MOI")                                      # thay file trên đĩa ngay trước khi thư viện đọc
        return real(source, *args, **kwargs)
    monkeypatch.setattr(raw_sheet, "load_workbook", replace_then_open)
    assert raw_sheet.sheet_rows(path, "Export")[1][1][0] == "CU"                 # đọc đúng các byte đã băm, không phải file vừa bị thay
    monkeypatch.setattr(raw_sheet, "load_workbook", real)
    assert raw_sheet.sheet_rows(path, "Export")[1][1][0] == "MOI"                # nội dung mới ⇒ mã băm mới ⇒ đọc lại
    path.write_bytes(old_bytes)
    assert raw_sheet.sheet_rows(path, "Export")[1][1][0] == "CU"                 # nội dung cũ quay lại ⇒ vẫn ra dòng cũ (mục nhớ cũ không bị bẩn)


def test_same_content_under_another_name_reuses_the_read(tmp_path, opens):
    path = write_book(tmp_path / "tam.xlsx")
    raw_sheet.sheet_rows(path, "Export")
    saved = tmp_path / "da_luu.xlsx"
    saved.write_bytes(path.read_bytes())                                         # file tạm lúc tải lên ⇒ file đã lưu: cùng nội dung
    assert raw_sheet.sheet_rows(saved, "Export")[1][1][0] == "M01" and opens == ["tam.xlsx"]


def test_missing_sheet_returns_none_without_raising_or_remembering(tmp_path, opens):
    path = write_book(tmp_path / "a.xlsx")
    assert raw_sheet.sheet_rows(path, "Khong co") == (["Export", "Khac"], None)
    assert raw_sheet.sheet_rows(path, 3) == (["Export", "Khac"], None)
    assert len(opens) == 2                                                       # không có mục nhớ nào được tạo cho sheet không có
    raw_sheet.sheet_rows(path, "Export")
    assert raw_sheet.sheet_rows(path, "Khong co") == (["Export", "Khac"], None) and len(opens) == 3   # file đã nhớ ⇒ trả lời không cần mở lại


def test_kept_zero_reads_every_time_like_before(tmp_path, opens, monkeypatch):
    monkeypatch.setenv(raw_sheet.KEPT_ENV, "0")
    path = write_book(tmp_path / "a.xlsx")
    first = raw_sheet.sheet_rows(path, "Export")[1]
    assert raw_sheet.sheet_rows(path, "Export")[1] == first and opens == ["a.xlsx", "a.xlsx"]


def test_kept_one_drops_the_older_file(tmp_path, opens, monkeypatch):
    monkeypatch.setenv(raw_sheet.KEPT_ENV, "1")
    old, new = write_book(tmp_path / "cu.xlsx", first_value="CU"), write_book(tmp_path / "moi.xlsx", first_value="MOI")
    raw_sheet.sheet_rows(old, "Export")
    raw_sheet.sheet_rows(new, "Export")
    raw_sheet.sheet_rows(new, "Export")
    raw_sheet.sheet_rows(old, "Export")                                          # file cũ đã bị bỏ ⇒ đọc lại
    assert opens == ["cu.xlsx", "moi.xlsx", "cu.xlsx"]


def test_sheet_over_the_cell_limit_is_still_returned_but_not_kept(tmp_path, opens, monkeypatch):
    path = write_book(tmp_path / "a.xlsx")                                       # sheet Export: 8 dòng × 3 cột = 24 ô
    monkeypatch.setenv(raw_sheet.MAX_CELLS_ENV, "23")
    first = raw_sheet.sheet_rows(path, "Export")[1]
    assert first == direct_rows(path) and raw_sheet.sheet_rows(path, "Export")[1] == first and opens == ["a.xlsx", "a.xlsx"]   # đủ dòng, không giữ
    monkeypatch.setenv(raw_sheet.MAX_CELLS_ENV, "24")
    raw_sheet.sheet_rows(path, "Export")
    raw_sheet.sheet_rows(path, "Export")
    assert opens == ["a.xlsx"] * 3                                               # vừa đúng trần ⇒ giữ
    monkeypatch.setenv(raw_sheet.MAX_CELLS_ENV, "nhiều")
    with pytest.raises(ValueError, match="KPI_RAW_SHEET_MAX_CELLS phải là số nguyên ≥ 0"):
        raw_sheet.sheet_rows(write_book(tmp_path / "b.xlsx", first_value="B"), "Export")


def test_forget_drops_only_that_file(tmp_path, opens):
    import hashlib
    kept, refused = write_book(tmp_path / "dang_dung.xlsx", first_value="A"), write_book(tmp_path / "bi_tu_choi.xlsx", first_value="B")
    raw_sheet.sheet_rows(kept, "Export")
    raw_sheet.sheet_rows(refused, "Export")
    raw_sheet.forget(hashlib.sha256(refused.read_bytes()).hexdigest())
    raw_sheet.forget("khong-co-ma-nay")                                          # mã lạ: không lỗi
    raw_sheet.sheet_rows(kept, "Export")
    raw_sheet.sheet_rows(refused, "Export")
    assert opens == ["dang_dung.xlsx", "bi_tu_choi.xlsx", "bi_tu_choi.xlsx"]     # file đang dùng vẫn được nhớ; file đã bỏ phải đọc lại


@pytest.mark.parametrize("bad", ["-1", "hai", "1.5", ""])
def test_bad_kept_value_stops_with_the_variable_name(tmp_path, monkeypatch, bad):
    monkeypatch.setenv(raw_sheet.KEPT_ENV, bad)
    with pytest.raises(ValueError, match="KPI_RAW_SHEETS_KEPT phải là số nguyên ≥ 0"):
        raw_sheet.sheet_rows(write_book(tmp_path / "a.xlsx"), "Export")


def test_two_threads_asking_for_the_same_file_open_it_once(tmp_path, opens):
    path = write_book(tmp_path / "a.xlsx")
    results = []
    threads = [threading.Thread(target=lambda: results.append(raw_sheet.sheet_rows(path, "Export")[1])) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 4 and all(rows is results[0] for rows in results) and opens == ["a.xlsx"]


@needs_data
def test_real_raw_file_is_opened_once_for_all_readers_and_numbers_match_a_run_without_memory(opens, monkeypatch):
    """Ca thật: 8 KPI + dòng cho Thưởng thêm + tên nhân viên + doanh số hội viên trên RAW tháng 10 ⇒ file mở ĐÚNG 1 lần; kết quả giống hệt lượt tắt lớp nhớ."""
    from kpi_agent.backend import membership
    from kpi_agent.core.load_raw import load_raw, period_rules, raw_employee_names, read_raw_lines
    from kpi_agent.core.rules import load_rules
    from kpi_agent.workflow.blocks import raw_options

    rules = load_rules(RULES)
    args = {"period_rules": period_rules(rules["assumptions"])}
    cutoff = date(2026, 10, 31)

    def everything():
        blocks = load_raw(RAW, cutoff, rules["period"], **raw_options(rules), **args)["blocks"]
        lines, late = read_raw_lines(RAW, cutoff, rules["period"], extra_columns=("TOTAL CASES", "TOTAL PIECES"), keep_late=True, **args)
        sales = membership.sales_after_discount(RAW, cutoff, counted_status=args["period_rules"]["counted_status"])
        return blocks, lines, late, raw_employee_names(RAW), sales

    with_memory = everything()
    assert opens == [RAW.name]
    monkeypatch.setenv(raw_sheet.KEPT_ENV, "0")
    without_memory = everything()
    assert len(opens) == 1 + 4                                                   # tắt lớp nhớ: mỗi phép đọc mở một lần như trước
    assert with_memory == without_memory
