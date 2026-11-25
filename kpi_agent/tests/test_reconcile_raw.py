"""Bậc 1 nạp dữ liệu chung (10:37 17/11, "P1 theo bậc"): luật kỳ khai báo trạng thái nào CẦN ngày xuất hoá đơn + cổng đối soát.

Giữ sàn: (1) tháng 10 không đổi tiền (112/112, 152.360.000 — test khác); (2) dòng RAW không bao giờ biến mất lặng lẽ: dòng vào = dòng tính + Σ dòng
loại theo TỪNG lý do, tổng doanh số tới đồng; (3) loại vượt ngưỡng ⇒ dừng, lời nêu số dòng + số tiền từng lý do. Ca xấu thật: RAW tháng 11
(Shipped chưa xuất hoá đơn — 10:05 17/11 `KIEM_NAP_THANG_11_A.md`: 2.269 dòng / 802.621.910 đ bị bỏ lặng)."""
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

from functools import partial

from kpi_agent.core import load_raw as raw_reader
from kpi_agent.core.load_raw import RECONCILE_STOP
from kpi_agent.core.rules import load_rules
from luat_thu import load_raw_allowing

ROOT = Path(__file__).resolve().parents[2]
# bậc 3a: bộ đọc RAW cần luật kỳ (không có mặc định) — lời gọi truyền luật RAW tháng 10, kỳ vọng không đổi
load_raw = partial(raw_reader.load_raw, period_rules=raw_reader.period_rules(load_rules(ROOT / "kpi_agent/config/rules_2026_10_raw.yaml")["assumptions"]))
# bậc 3a P2: file RAW TỰ DỰNG vài dòng của các ca dưới không có nhóm Trà 365 / mã Homey 2,9 kg / ngành Thịt / Bia mà luật tháng 10 nêu ⇒ luật thật
# sẽ dừng (ca giữ hành vi đó: test_luat_ky_raw). Các ca file tự dựng đọc bằng bản luật thử = luật thật + cho qua đúng các giá trị này (luat_thu.py)
load_small = load_raw_allowing("Trà Búp Non Tea 365", "04HP00639", "Processed Meats", "Beer")
RAW_T10 = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
RAW_T11 = ROOT / "sample_data/thang_10/RAW DATA (3).xlsx"
needs_data = pytest.mark.skipif(not (RAW_T10.exists() and RAW_T11.exists()), reason="không có dữ liệu thật trên máy này")
T10 = {"late_kpis": ("Turnover", "PC_BT"), "invoice_required_status": ("Invoiced", "Shipped"), "max_excluded_sales_pct": 2}


def _raw(tmp_path, rows) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export"
    sheet.append(["SM CODE", "ORDER NUM", "ORDER STATUS", "ORDER DATE", "OUTLET CODE", "SUB DIV", "DMS OBJ", "SKU CODE", "Sales Before Discount", "L1",
                  "MOQ_FLAG", "FREE ITEM FLAG", "INVOICED DATE"])
    for order, status, day, sales, invoiced in rows:
        sheet.append(["SM1", order, status, day, 1, "Seasoning", "x", "a", sales, "Kênh Off Premise", "Y", "N", invoiced])
    path = tmp_path / "raw.xlsx"
    book.save(path)
    return path


ON_TIME = datetime(2026, 11, 3, 9)
ROWS = [("D1", "Invoiced", date(2026, 11, 2), 1000, ON_TIME), ("D2", "Shipped", date(2026, 11, 4), 300, None),
        ("D3", "Cancelled", date(2026, 11, 2), 50, None), ("D4", "Invoiced", date(2026, 11, 20), 7, ON_TIME)]


def _reasons(result):
    return {item["reason"]: (item["lines"], item["sales"]) for item in result["reconciliation"]["excluded"]}


def test_every_line_is_counted_or_excluded_with_a_reason(tmp_path):
    result = load_small(_raw(tmp_path, ROWS), date(2026, 11, 10), "2026-11", invoice_required_status=("Invoiced",), max_excluded_sales_pct=10)
    rec = result["reconciliation"]
    # Shipped KHÔNG cần ngày xuất (luật kỳ) ⇒ tính; Cancelled ⇒ loại có lý do; đơn sau ngày chốt ⇒ loại có lý do (không tính vào ngưỡng)
    assert (rec["input_lines"], rec["input_sales"]) == (4, 1357) and (rec["counted_lines"], rec["counted_sales"]) == (2, 1300)
    assert _reasons(result) == {"status_not_counted": (1, 50), "after_cutoff": (1, 7)}
    assert rec["input_lines"] == rec["counted_lines"] + sum(lines for lines, _ in _reasons(result).values())
    assert rec["counted_sales"] == sum(row["Total"] for row in result["blocks"]["Turnover"]["rows"])   # = Σ KPI 1 của khối
    assert rec["excluded_pct"] == 0.0 and rec["ok"] is True        # đơn huỷ là loại có chủ đích (bậc 1) ⇒ không cộng ngưỡng (trước: 3,70 %)


def test_missing_invoice_date_over_threshold_stops_with_counts_and_money(tmp_path):
    # Ca xấu: luật kỳ nói Shipped CẦN ngày xuất ⇒ dòng Shipped trống ngày xuất bị loại "chưa có ngày xuất hoá đơn" ⇒ vượt ngưỡng ⇒ DỪNG, không ra số
    with pytest.raises(ValueError, match=RECONCILE_STOP) as stopped:
        load_small(_raw(tmp_path, ROWS), date(2026, 11, 10), "2026-11", invoice_required_status=("Invoiced", "Shipped"), max_excluded_sales_pct=10)
    assert "chưa có ngày xuất hoá đơn: 1 dòng / 300 đ" in str(stopped.value) and "ngưỡng 10 %" in str(stopped.value)


def test_without_new_keys_behaviour_is_unchanged(tmp_path):
    # Thiếu khoá luật mới ⇒ như trước: mọi trạng thái được tính đều cần ngày xuất; không có ngưỡng ⇒ không dừng, vẫn có kết quả đối soát
    result = load_small(_raw(tmp_path, ROWS), date(2026, 11, 10), "2026-11")
    assert result["late_invoice_lines"] == 1 and result["reconciliation"]["counted_sales"] == 1000
    assert _reasons(result)["no_invoice_date"] == (1, 300) and result["reconciliation"]["max_excluded_sales_pct"] is None


@needs_data
def test_real_october_passes_gate_with_nothing_lost():
    rec = load_raw(RAW_T10, date(2026, 10, 31), "2026-10", **T10)["reconciliation"]
    assert rec["ok"] and rec["input_lines"] == rec["counted_lines"] == 24125 and rec["excluded"] == []   # 3 dòng trễ CÓ ngày xuất ⇒ KPI 1 cộng (mốc 7)
    assert rec["file_total_matches"] is True and round(rec["file_total_sales"]) == 10_348_975_656        # dòng "Total" cuối file khớp tới đồng


@needs_data
def test_real_november_shipped_without_invoice_date_is_blocked():
    # Ca xấu thật (A 10:05 17/11): luật thử kỳ 11 = cách đọc 6.1 hiện tại (Shipped cần ngày xuất) ⇒ cổng CHẶN, nêu đúng 2.269 dòng / 802.621.910 đ
    with pytest.raises(ValueError, match=RECONCILE_STOP) as stopped:
        load_raw(RAW_T11, date(2026, 11, 30), "2026-11", **T10)
    assert "chưa có ngày xuất hoá đơn: 2.269 dòng / 802.621.910 đ" in str(stopped.value)
    # Luật kỳ khác (Shipped KHÔNG cần ngày xuất — câu hỏi sếp): mọi dòng vào KPI 1, tổng = cả file 1.695.607.804 đ (đã đếm 10:05)
    rec = load_raw(RAW_T11, date(2026, 11, 30), "2026-11", **{**T10, "invoice_required_status": ("Invoiced",)})["reconciliation"]
    assert rec["ok"] and rec["counted_lines"] == 4562 and round(rec["counted_sales"]) == 1_695_607_804


def test_rules_keys_are_checked_at_load():
    # Ca xấu: gõ sai trạng thái / ngưỡng không phải số ⇒ dừng lúc nạp luật (không lặng lẽ thôi đòi ngày xuất hoá đơn)
    import copy
    from kpi_agent.core.rules import load_rules, validate_rules
    from kpi_agent.workflow.blocks import raw_options
    rules = load_rules(ROOT / "kpi_agent/config/rules_2026_10_raw.yaml")
    assert raw_options(rules)["invoice_required_status"] == ("Invoiced", "Shipped") and raw_options(rules)["max_excluded_sales_pct"] == 2
    bad = copy.deepcopy(rules)
    bad["assumptions"].update(invoice_required_status=["Shiped"], max_excluded_sales_pct="2%")
    errors = validate_rules(bad)
    assert any("invoice_required_status" in e for e in errors) and any("max_excluded_sales_pct" in e for e in errors)
    assert raw_options(load_rules(ROOT / "kpi_agent/config/rules_2026_10.yaml"))["invoice_required_status"] is None   # đường Tracking: như cũ


def test_file_total_row_mismatch_stops_and_missing_row_does_not(tmp_path):
    # Ca xấu: dòng "Total" của file ≠ Σ dòng đọc được (vd mất dòng / đọc sai kiểu số) ⇒ DỪNG; file không có dòng Total ⇒ "không đối chiếu được", không chặn
    path = _raw(tmp_path, ROWS[:1])
    book = openpyxl.load_workbook(path)
    sheet = book["Export"]
    sheet.cell(1, 14, "SHIP TO")
    sheet.append([None] * 8 + [999, None, None, None, None, "Total"])
    book.save(path)
    with pytest.raises(ValueError, match="dòng 'Total' của file 999 đ"):
        load_small(path, date(2026, 11, 10), "2026-11")
    assert load_small(_raw(tmp_path, ROWS[:1]), date(2026, 11, 10), "2026-11")["reconciliation"]["file_total_matches"] is None


def test_vietnamese_number_read_1000_times_too_small_is_stopped(tmp_path):
    # Ca xấu (nhóm kỹ thuật 17/11, màn ghép bậc 2): "384,00" người gõ ý 384.000 đ mà bị đọc thành 384 đ ⇒ sai ×1000, không ô nào đỏ — chỉ dòng "Total" bắt được.
    # Bậc 1 chưa đọc số kiểu Việt: ô CHỮ "384,00" bị `_check` dừng; ô đã đọc thành SỐ 384 (như bộ ghép đọc nhầm) ⇒ cổng tổng file dừng.
    def with_total(rows, total):
        path = _raw(tmp_path, rows)
        book = openpyxl.load_workbook(path)
        sheet = book["Export"]
        sheet.cell(1, 14, "SHIP TO")
        sheet.append([None] * 8 + [total, None, None, None, None, "Total"])
        book.save(path)
        return path
    with pytest.raises(ValueError, match="không phải số"):
        load_small(with_total([("D1", "Invoiced", date(2026, 11, 2), "384,00", ON_TIME)], 384000), date(2026, 11, 10), "2026-11")
    with pytest.raises(ValueError, match=f"{RECONCILE_STOP}: tổng doanh số các dòng đọc được 384 đ ≠ dòng 'Total' của file 384.000 đ"):
        load_small(with_total([("D1", "Invoiced", date(2026, 11, 2), 384.0, ON_TIME)], 384000), date(2026, 11, 10), "2026-11")


# Bậc 2 bước 2.4 (bậc 1, đã chuyển 11:2x 17/11): ngưỡng theo NV (m1), đơn huỷ không cộng ngưỡng (m3), giá trị mới (2.4) ----------
def _raw_people(tmp_path, rows) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export"
    sheet.append(["SM CODE", "ORDER NUM", "ORDER STATUS", "ORDER DATE", "OUTLET CODE", "SUB DIV", "DMS OBJ", "SKU CODE", "Sales Before Discount", "L1",
                  "MOQ_FLAG", "FREE ITEM FLAG", "INVOICED DATE"])
    for sm, order, status, sales, invoiced, sub in rows:
        sheet.append([sm, order, status, date(2026, 11, 2), 1, sub, "x", "a", sales, "Kênh Off Premise", "Y", "N", invoiced])
    path = tmp_path / "raw_nv.xlsx"
    book.save(path)
    return path


def test_one_employee_losing_a_lot_stops_even_if_team_is_under_threshold(tmp_path):
    # m1: đội mất 300 / 100.300 = 0,29 % (< 2 %) nhưng SM2 mất 300 / 1.300 = 23,07 % ⇒ DỪNG, nêu đúng người + số dòng + tiền
    rows = [("SM1", "D1", "Invoiced", 99000, ON_TIME, "Seasoning"), ("SM2", "D2", "Invoiced", 1000, ON_TIME, "Seasoning"),
            ("SM2", "D3", "Shipped", 300, None, "Seasoning")]
    with pytest.raises(ValueError, match=RECONCILE_STOP) as stopped:
        load_small(_raw_people(tmp_path, rows), date(2026, 11, 10), "2026-11", **T10)
    assert "SM2: 1 dòng / 300 đ (23,07 %)" in str(stopped.value) and "SM1" not in str(stopped.value)


def test_cancelled_orders_are_a_reason_but_not_counted_in_threshold(tmp_path):
    # m3: đơn huỷ là loại CÓ CHỦ ĐÍCH (như sau ngày chốt) ⇒ vẫn hiện lý do riêng, không làm dừng
    rows = [("SM1", "D1", "Invoiced", 1000, ON_TIME, "Seasoning"), ("SM1", "D2", "Cancelled", 900, None, "Seasoning")]
    rec = load_small(_raw_people(tmp_path, rows), date(2026, 11, 10), "2026-11", **T10)["reconciliation"]
    assert rec["ok"] and rec["excluded_pct"] == 0 and {item["reason"]: item["lines"] for item in rec["excluded"]} == {"status_not_counted": 1}


def test_no_total_row_gives_a_user_flag(tmp_path):
    # m2: không có dòng Total ⇒ lời cho người dùng (cờ dữ liệu), không chỉ một trường meta
    rows = [("SM1", "D1", "Invoiced", 1000, ON_TIME, "Seasoning")]
    rec = load_small(_raw_people(tmp_path, rows), date(2026, 11, 10), "2026-11", **T10)["reconciliation"]
    assert rec["file_total_matches"] is None and "không đối chiếu được tổng file" in rec["note"]


def test_unknown_value_stops_and_accepted_new_division_passes(tmp_path):
    # 2.4: ngành mới / kênh lạ / cờ lạ ⇒ DỪNG, lời KHÔNG lặp giá trị (U1); ngành mới người cho qua (accepted_values trong bảng ghép) ⇒ tính
    from kpi_agent.core import khuon
    path = _raw_people(tmp_path, [("SM1", "D1", "Invoiced", 1000, ON_TIME, "Snack <b>mới</b>")])
    with pytest.raises(ValueError, match="giá trị chưa biết") as stopped:
        load_small(path, date(2026, 11, 10), "2026-11", **T10)
    assert "sub_div: 1 giá trị / 1 dòng" in str(stopped.value) and "Snack" not in str(stopped.value)
    accepted = {**khuon.default_mapping("don_hang"), "accepted_values": {"sub_div": ["Snack <b>mới</b>"]}}
    assert load_small(path, date(2026, 11, 10), "2026-11", **T10, mapping=accepted)["reconciliation"]["counted_lines"] == 1
    with pytest.raises(ValueError, match="accepted_values"):
        khuon.normalize_mapping({**khuon.default_mapping("don_hang"), "accepted_values": {"channel": ["Kênh Vãng lai"]}})   # kênh lạ phải GHÉP, không cho qua
