"""Hai bẫy NẠP ĐƯỢC SAI LẶNG LẼ (Capitol thử như người dùng U11, sáng 18/11/2026 — E_THU_NHU_NGUOI_DUNG_18_11.md bên):
(1) số dạng chữ "384,00" ở cột tiền Sales After Discount ⇒ file nạp được (200); (2) dòng "Total" / dòng không mã NV lệch tổng ⇒ nạp được (200).
Luật mới: ô tiền phải là SỐ ở mọi dòng có mã NV (cả 3 cột tiền đọc được); dòng không mã NV có tiền ≠ 0 mà không phải dòng Total ⇒ dừng; dòng Total
phải có số ở cột trước chiết khấu, chỉ được MỘT dòng Total, và Σ sau chiết khấu = ô sau chiết khấu của dòng Total. Đo trên RAW thật T10 + T11:
mọi ô tiền dòng có mã đều là số, 0 dòng không mã có tiền, dòng Total có số ở cả 3 cột ⇒ luật không chặn bản đang chạy.
File bẫy ở đây DỰNG GIẢ (không lấy từ dữ liệu thật)."""
from datetime import date, datetime

import openpyxl
import pytest

from functools import partial
from pathlib import Path

from kpi_agent.core import load_raw as raw_reader
from kpi_agent.core.load_raw import RECONCILE_STOP
from kpi_agent.core.rules import load_rules
from luat_thu import load_raw_allowing

# bậc 3a: bộ đọc RAW cần luật kỳ (không có mặc định) — lời gọi truyền luật RAW tháng 10, kỳ vọng không đổi
load_raw = partial(raw_reader.load_raw, period_rules=raw_reader.period_rules(
    load_rules(Path(__file__).resolve().parents[2] / "kpi_agent/config/rules_2026_10_raw.yaml")["assumptions"]))
# bậc 3a P2: file RAW TỰ DỰNG vài dòng của các ca dưới không có nhóm Trà 365 / mã Homey 2,9 kg / ngành Thịt / Bia mà luật tháng 10 nêu ⇒ luật thật
# sẽ dừng (ca giữ hành vi đó: test_luat_ky_raw). Các ca file tự dựng đọc bằng bản luật thử = luật thật + cho qua đúng các giá trị này (luat_thu.py)
load_small = load_raw_allowing("Trà Búp Non Tea 365", "04HP00639", "Processed Meats", "Beer")

HEADER = ["SM CODE", "ORDER NUM", "ORDER STATUS", "ORDER DATE", "OUTLET CODE", "SUB DIV", "DMS OBJ", "SKU CODE", "Sales Before Discount",
          "Sales After Discount", "Discount value", "L1", "MOQ_FLAG", "FREE ITEM FLAG", "INVOICED DATE", "SHIP TO"]
DAY, INVOICED = date(2026, 11, 2), datetime(2026, 11, 3, 9)


def line(order, before, after, discount, code="SM1"):
    return [code, order, "Invoiced", DAY, 1, "Seasoning", "x", "a", before, after, discount, "Kênh Off Premise", "Y", "N", INVOICED, "Cửa hàng"]


def total(before, after, discount):
    return [None] * 8 + [before, after, discount] + [None] * 4 + ["Total"]


GOOD = [line("D1", 1000, 900, 100), line("D2", 500, 500, 0)]


def raw(tmp_path, rows):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export"
    sheet.append(HEADER)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "raw.xlsx"
    book.save(path)
    return path


def load(tmp_path, rows):
    return load_small(raw(tmp_path, rows), date(2026, 11, 10), "2026-11", invoice_required_status=("Invoiced",), max_excluded_sales_pct=10)


def test_good_file_with_matching_total_loads(tmp_path):
    rec = load(tmp_path, GOOD + [total(1500, 1400, 100)])["reconciliation"]
    assert rec["file_total_matches"] is True and rec["input_sales"] == 1500


@pytest.mark.parametrize("column", [9, 10])     # Sales After Discount, Discount value
def test_text_number_in_money_column_stops(tmp_path, column):
    bad = line("D3", 384, 384, 0)
    bad[column] = "384,00"                     # bẫy (1) Capitol: số kiểu Việt dạng chữ
    with pytest.raises(ValueError, match="không phải số") as stopped:
        load(tmp_path, GOOD + [bad] + [total(1884, 1784, 100)])
    assert "384,00" not in str(stopped.value)  # không in nội dung ô
    assert "dòng Excel 4" in str(stopped.value)


def test_total_row_after_discount_mismatch_stops(tmp_path):
    # bẫy (2): dòng Total khớp cột trước chiết khấu nhưng lệch cột sau chiết khấu
    with pytest.raises(ValueError, match=RECONCILE_STOP):
        load(tmp_path, GOOD + [total(1500, 1399, 100)])


def test_total_row_before_discount_as_text_stops(tmp_path):
    # ô tiền của dòng Total là chữ ⇒ trước đây "không có dòng Total" (chỉ ghi chú) ⇒ nạp được
    with pytest.raises(ValueError, match="dòng 'Total'"):
        load(tmp_path, GOOD + [total("1.500", 1400, 100)])


def test_two_total_rows_stop(tmp_path):
    with pytest.raises(ValueError, match="dòng 'Total'"):
        load(tmp_path, GOOD + [total(1000, 900, 100), total(1500, 1400, 100)])


def test_row_without_staff_code_with_money_stops(tmp_path):
    # dòng không mã NV mà có tiền (không phải dòng Total) ⇒ trước đây bị bỏ lặng ⇒ tổng lệch không ai biết
    stray = line("D9", 700, 700, 0, code=None)
    with pytest.raises(ValueError, match="không có mã nhân viên"):
        load(tmp_path, GOOD + [stray, total(1500, 1400, 100)])


def test_blank_rows_without_code_still_pass(tmp_path):
    blank = [None] * len(HEADER)
    rec = load(tmp_path, GOOD + [blank, total(1500, 1400, 100)])["reconciliation"]
    assert rec["file_total_matches"] is True


def test_row_without_code_with_text_money_stops(tmp_path):
    # dbf1130d: dòng KHÔNG mã NV có ô tiền DẠNG CHỮ ⇒ bản đầu chỉ xét số ≠ 0 ⇒ bỏ lặng, nạp được
    stray = line("D9", "384,00", None, None, code=None)
    with pytest.raises(ValueError, match="không có mã nhân viên"):
        load(tmp_path, GOOD + [stray, total(1500, 1400, 100)])


def test_total_row_after_discount_blank_stops(tmp_path):
    # dbf1130d: ô sau chiết khấu của dòng Total TRỐNG ⇒ bản đầu bỏ qua phép so Σ ⇒ nạp được
    with pytest.raises(ValueError, match="dòng 'Total'"):
        load(tmp_path, GOOD + [total(1500, None, 100)])


def test_total_after_discount_off_by_one_dong_stops(tmp_path):
    with pytest.raises(ValueError, match=RECONCILE_STOP):
        load(tmp_path, [line("D1", 1000, 900.5, 99.5), line("D2", 500, 500, 0)] + [total(1500, 1401.5, 99.5)])
