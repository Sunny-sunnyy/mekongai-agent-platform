"""Bậc 2 bước 2.5 (17/11): các đường đọc RAW NGOÀI lõi (xem đơn `order_lines.read_all_lines`, dòng trễ của thẻ Chi tiết `outlets.read_late_lines`)
đọc QUA bảng ghép + theo luật kỳ như lõi `read_raw_lines`.

Giữ sàn: (1) file đổi tên hết cột + bảng ghép ⇒ các đường phụ đọc được (trước 2.5: tự đọc sheet "Export" theo tên cột T10 ⇒ hỏng / sai cột);
(2) kỳ có Shipped chưa xuất HĐ mà luật kỳ không đòi ngày xuất ⇒ đường phụ giữ dòng ĐÚNG như lõi (trước 2.5: bỏ như "trễ" ⇒ màn xem đơn từ chối vì lệch lõi);
(3) Q9: "chưa có ngày xuất" là lý do riêng, khác "xuất trễ".
"""
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from kpi_agent.backend import order_lines, outlets
from kpi_agent.core import khuon
from kpi_agent.core.load_raw import period_rules, read_raw_lines
from kpi_agent.core.rules import load_rules

COLUMNS = [field["default_column"] for field in khuon.schema("don_hang")]
CUTOFF, PERIOD = date(2026, 11, 20), "2026-11"
# bậc 3a: bộ đọc RAW cần luật kỳ (không có mặc định) — lời gọi truyền luật RAW tháng 10, kỳ vọng không đổi
PR = period_rules(load_rules(Path(__file__).resolve().parents[2] / "kpi_agent/config/rules_2026_10_raw.yaml")["assumptions"])
ON_TIME, LATE = datetime(2026, 11, 3, 9), datetime(2026, 12, 5, 9)


def _row(order: str, status: str, invoiced, order_day: date = date(2026, 11, 2)) -> dict:
    return {"SM CODE": "SM1", "SM_NAME": "A", "ORDER NUM": order, "ORDER STATUS": status, "ORDER DATE": order_day, "INVOICED DATE": invoiced,
            "OUTLET CODE": 1, "SUB DIV": "Seasoning", "DMS OBJ": "x", "SKU CODE": "s", "Sales Before Discount": 1000, "Sales After Discount": 900,
            "Discount value": 100, "L1": "Off", "MOQ_FLAG": "Có", "FREE ITEM FLAG": "Không", "TOTAL CASES": 1.0, "TOTAL PIECES": 1,
            "OUTLET NAME": "cửa hàng", "ADDRESS": "địa chỉ"}


ROWS = [_row("D1", "Invoiced", ON_TIME),              # được tính
        _row("D2", "Shipped", None),                  # luật kỳ chỉ đòi ngày xuất cho Invoiced ⇒ được tính (như lõi)
        _row("D3", "Invoiced", LATE),                 # xuất trễ
        _row("D4", "Invoiced", None),                 # chưa có ngày xuất (Invoiced cần ngày xuất)
        _row("D5", "Invoiced", ON_TIME, date(2026, 11, 25))]   # sau ngày chốt
REQUIRED = ("Invoiced",)


def _renamed_file(tmp_path) -> tuple[Path, dict]:
    """Mọi cột đổi tên "C<n>", sheet "Don hang", 1 dòng tựa, cờ "Có / Không", kênh "Off" ⇒ chỉ đọc được qua bảng ghép."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Don hang"
    sheet.append(["Báo cáo đơn hàng"])
    sheet.append([f"C{n}" for n in range(len(COLUMNS))])
    for row in ROWS:
        sheet.append([row[column] for column in COLUMNS])
    path = tmp_path / "doi_ten.xlsx"
    book.save(path)
    mapping = {**khuon.default_mapping("don_hang"), "sheet": "Don hang", "header_row": 2,
               "columns": {field["field"]: f"C{n}" for n, field in enumerate(khuon.schema("don_hang"))},
               "values": {"moq_flag": {"Có": "Y", "Không": "N"}, "is_free_item": {"Có": "Y", "Không": "N"}, "channel": {"Off": "Kênh Off Premise"}}}
    return path, mapping


def test_order_lines_keep_exactly_the_core_lines_through_mapping_and_period_rule(tmp_path):
    path, mapping = _renamed_file(tmp_path)
    core_kept, _late = read_raw_lines(path, CUTOFF, PERIOD, invoice_required_status=REQUIRED, mapping=mapping, period_rules=PR)
    lines = order_lines.read_all_lines(path, CUTOFF, PERIOD, invoice_required_status=REQUIRED, mapping=mapping, period_rules=PR)
    drop = {line["ORDER NUM"]: line["_drop"] for line in lines}
    assert sorted(order for order, reason in drop.items() if reason is None) == sorted(line["ORDER NUM"] for line in core_kept) == ["D1", "D2"]
    assert drop == {"D1": None, "D2": None, "D3": order_lines.LATE_INVOICE, "D4": order_lines.NO_INVOICE_DATE, "D5": order_lines.AFTER_CUTOFF}
    assert [line["ORDER NUM"] for line in lines if line["_late"]] == ["D3"]   # chỉ dòng trễ CÓ ngày xuất được KPI trong late_invoice_kpis cộng
    assert lines[0]["OUTLET CODE"] == "1" and lines[0]["L1"] == "Kênh Off Premise" and lines[0]["OUTLET NAME"] == "cửa hàng"


def test_late_lines_of_detail_card_are_the_two_core_late_reasons(tmp_path):
    path, mapping = _renamed_file(tmp_path)
    late = outlets.read_late_lines(path, CUTOFF, PERIOD, invoice_required_status=REQUIRED, mapping=mapping, period_rules=PR)
    assert sorted(line["ORDER NUM"] for line in late) == ["D3", "D4"]   # Shipped không ngày xuất KHÔNG là trễ (luật kỳ); sau ngày chốt không tính
    assert {line[outlets.AFTER] for line in late} == {900} and all(line["OUTLET NAME"] == "cửa hàng" for line in late)
    # Không truyền luật kỳ ⇒ như cũ: mọi trạng thái được tính cần ngày xuất ⇒ Shipped không ngày xuất cũng là trễ
    assert sorted(line["ORDER NUM"] for line in outlets.read_late_lines(path, CUTOFF, PERIOD, mapping=mapping, period_rules=PR)) == ["D2", "D3", "D4"]


def test_employee_names_read_through_mapping(tmp_path):
    from kpi_agent.core.load_raw import raw_employee_names
    path, mapping = _renamed_file(tmp_path)
    assert raw_employee_names(path, mapping) == {"SM1": "A"}   # 1 % gia vị ghép tên ⇒ mã qua cùng bảng ghép
    with pytest.raises(ValueError, match="sheet"):              # không bảng ghép ⇒ tìm sheet "Export" ⇒ dừng có lời, không đoán
        raw_employee_names(path)


def test_membership_on_mapped_file_stops_with_a_reason_not_a_number(tmp_path):
    # Giới hạn đã biết: hội viên (chờ sếp) chưa đọc qua bảng ghép ⇒ file đã ghép cột phải DỪNG có lời, không trả số
    from kpi_agent.backend import membership
    path, _mapping = _renamed_file(tmp_path)
    with pytest.raises(ValueError, match="thiếu sheet"):
        membership.sales_after_discount(path, CUTOFF, counted_status={"Invoiced", "Shipped"})
