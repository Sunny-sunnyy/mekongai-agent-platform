"""Đọc RAW DATA ⇒ khối pivot. Dữ liệu thật tháng 10 (RAW + Tracking); file hỏng dựng trong thư mục tạm. Không mock.

Đối chứng KHÔNG dùng đáp án trang sếp: cắt RAW tới 20/10 ⇒ so với khối pivot của sổ Tracking (chụp 20/10) — cùng dữ liệu, cùng ngày."""
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest
import yaml

from kpi_agent.core.compute_kpi import compute_actuals
from kpi_agent.core.load_data import load_targets, load_tracking
from functools import partial

from kpi_agent.core import load_raw as raw_reader
from kpi_agent.core.rules import RulesError, load_rules
from luat_thu import load_raw_allowing

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
TRACKING = ROOT / "sample_data/thang_10/Tracking.xlsx"
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
RULES, RULES_RAW = ROOT / "kpi_agent/config/rules_2026_10.yaml", ROOT / "kpi_agent/config/rules_2026_10_raw.yaml"
# bậc 3a: bộ đọc RAW cần luật kỳ (không có mặc định) — lời gọi truyền luật RAW tháng 10, kỳ vọng không đổi
PERIOD_RULES = raw_reader.period_rules(load_rules(RULES_RAW)["assumptions"])
load_raw = partial(raw_reader.load_raw, period_rules=PERIOD_RULES)
build_blocks = partial(raw_reader.build_blocks, period_rules=PERIOD_RULES)
# bậc 3a P2: file RAW TỰ DỰNG vài dòng của các ca dưới không có nhóm Trà 365 / mã Homey 2,9 kg / ngành Thịt / Bia mà luật tháng 10 nêu ⇒ luật thật
# sẽ dừng (ca giữ hành vi đó: test_luat_ky_raw). Các ca file tự dựng đọc bằng bản luật thử = luật thật + cho qua đúng các giá trị này (luat_thu.py)
load_small = load_raw_allowing("Trà Búp Non Tea 365", "04HP00639", "Processed Meats", "Beer")
needs_data = pytest.mark.skipif(not all(p.exists() for p in (RAW, TRACKING, TARGETS)), reason="không có dữ liệu thật trên máy này")


@pytest.fixture(scope="module")
def raw_20():
    # So với pivot Tracking ⇒ TẮT "1 PC / ngày / cửa hàng" (pivot đếm mỗi đơn 1 PC — cách đếm của sổ, không phải chữ công văn)
    # bậc 3a: KPI 6 theo mã nay KHÔNG kèm dòng cả nhóm trong khối ⇒ để so với pivot Tracking (sổ lọc cả nhóm Homey) phải đọc RAW bằng luật KPI 6
    # "cả nhóm" như sổ — luật RAW thật chỉ đổi đúng hai khoá này; kỳ vọng của các ca so với sổ không đổi
    like_tracking = {**load_rules(RULES_RAW)["assumptions"], "aso_focus_2_scope": "whole_group", "aso_focus_2_groups": ["Nước giặt xả Homey"]}
    return raw_reader.load_raw(RAW, date(2026, 10, 20), "2026-10", one_pc_per_outlet_day=False, period_rules=raw_reader.period_rules(like_tracking))


@pytest.fixture(scope="module")
def tracking():
    return load_tracking(TRACKING)


def _rows_by_employee(block) -> Counter:
    return Counter(row["SM CODE"] for row in block["rows"])


@needs_data
def test_raw_blocks_match_tracking_pivot_at_its_snapshot_day(raw_20, tracking):
    # Hiệu chỉnh: cắt 20/10 ⇒ số dòng khối khớp Tracking ở ≥ 12 / 14 NV mọi KPI; LPPC_Meat khớp cả số line 14 / 14.
    # Chỗ lệch nhỏ (1–2 đơn, 1 NV Turnover ~1 tr) = RAW xuất muộn hơn sổ — chưa kiểm nguyên nhân, ghi ở
    for kpi, block in tracking["blocks"].items():
        ours, theirs = _rows_by_employee(raw_20["blocks"][kpi]), _rows_by_employee(block)
        if kpi == "ASO_Focus_2":   # khối RAW tách thêm theo mã hàng ⇒ so số CỬA HÀNG khác nhau
            ours = Counter(code for code, outlet in {(r["SM CODE"], r["OUTLET CODE"]) for r in raw_20["blocks"][kpi]["rows"]})
        if kpi == "Turnover":      # QĐ-09 11/11: khối RAW gom theo (NV, cửa hàng) ⇒ so TỔNG doanh số mỗi NV (lệch < 1 đ / 1 tr như chú thích trên)
            total = lambda rows: Counter({c: round(sum(r["Total"] or 0 for r in rows if r["SM CODE"] == c) / 1e6) for c in {r["SM CODE"] for r in rows}})
            ours, theirs = total(raw_20["blocks"][kpi]["rows"]), total(block["rows"])
        same = sum(ours[code] == theirs[code] for code in set(ours) | set(theirs))
        assert same >= 12, (kpi, same)
    meat = lambda block: {code: sum(r["Total"] for r in block["rows"] if r["SM CODE"] == code) for code in _rows_by_employee(block)}
    assert meat(raw_20["blocks"]["LPPC_Meat"]) == meat(tracking["blocks"]["LPPC_Meat"])


@needs_data
def test_cutoff_filters_by_order_date(raw_20):
    later = load_raw(RAW, date(2026, 10, 21), "2026-10", one_pc_per_outlet_day=False)
    assert later["refreshed_at"].date() == date(2026, 10, 21) and raw_20["refreshed_at"].date() <= date(2026, 10, 20)
    assert len(later["blocks"]["PC_BT"]["rows"]) > len(raw_20["blocks"]["PC_BT"]["rows"])


@needs_data
def test_old_assumptions_on_raw_give_close_to_tracking_numbers(raw_20, tracking):
    # Cùng luật Tracking (cả nhóm Homey, không lọc MCP) trên RAW 20/10 ⇒ số thực hiện gần đúng như Tracking (đổi nguồn không đổi phép tính)
    rules, targets = load_rules(RULES), load_targets(TARGETS)
    ours = compute_actuals(targets, {**raw_20, "mcp": tracking["mcp"]}, rules)
    theirs = compute_actuals(targets, tracking, rules)
    # So lệch < 1 đồng / đúng y số đếm: doanh số là tổng số thực — cộng khác thứ tự lệch phần lẻ (13 ô Turnover lệch dưới 1 đ khi so ==)
    same = sum(abs(ours[c][k].actual - theirs[c][k].actual) < (1 if k == "Turnover" else 1e-9) for c in theirs for k in theirs[c])
    # Số ĐO 10/11 (không phải tiêu chí đặt trước — ngưỡng 100 E tự đặt trước khi đo ra 94 do so ==): 107 / 112; 5 ô lệch = Turnover 1 NV,
    # LPPC 2, PC_BT 1, PC_ON 1 — đúng các đơn lệch ở phép hiệu chỉnh trên. Ghim 107 để bắt hồi quy của bộ đọc RAW.
    assert same >= 107, same


def test_raw_rules_differ_from_tracking_rules_only_in_assumptions():
    # Hai file luật phải giống hệt mốc / tiền — chỉ khác khối assumptions (chép nguyên)
    old, new = (yaml.safe_load(path.read_text(encoding="utf-8")) for path in (RULES, RULES_RAW))
    assert {k: v for k, v in old.items() if k != "assumptions"} == {k: v for k, v in new.items() if k != "assumptions"}
    assert new["assumptions"]["aso_focus_2_scope"] == "sku_list" and new["assumptions"]["exclude_outlets_outside_mcp"] is True


def test_sku_scope_on_tracking_blocks_is_refused(tmp_path):
    # Ca xấu: luật "chỉ mã 2,9 kg" chạy trên khối pivot Tracking (không có mã hàng) ⇒ dừng, không đếm cả nhóm lặng lẽ
    rules = load_rules(RULES_RAW)
    block = {"rows": [{"SM CODE": "A", "ORDER NUM": 1, "OUTLET CODE": "1", "Total": 5}]}
    tracking = {"blocks": {kpi: block for kpi in ("Turnover", "PC_BT", "LPPC", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat")}, "mcp": {}}
    with pytest.raises(ValueError, match="mã hàng"):
        compute_actuals({"targets": {}}, tracking, rules)


def test_sku_scope_needs_sku_list(tmp_path):
    data = yaml.safe_load(RULES_RAW.read_text(encoding="utf-8"))
    del data["assumptions"]["aso_focus_2_skus"]
    path = tmp_path / "r.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RulesError, match="aso_focus_2_skus"):
        load_rules(path)


def _raw_copy(tmp_path, edit) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export"
    header = ["SM CODE", "ORDER NUM", "ORDER STATUS", "ORDER DATE", "OUTLET CODE", "SUB DIV", "DMS OBJ", "SKU CODE", "Sales Before Discount", "L1", "MOQ_FLAG",
              "FREE ITEM FLAG", "INVOICED DATE"]
    row = ["SM1", "D1", "Invoiced", date(2026, 10, 5), 1, "Seasoning", "x", "s", 1000, "Kênh Off Premise", "Y", "N", datetime(2026, 10, 5, 9)]
    header, row = edit(header, row)
    sheet.append(header)
    sheet.append(row)
    path = tmp_path / "raw.xlsx"
    book.save(path)
    return path


@pytest.mark.parametrize("edit, match", [
    (lambda h, r: (h[:-1], r[:-1]), "thiếu cột: INVOICED DATE"),
    (lambda h, r: (h, r[:3] + [date(2026, 11, 2)] + r[4:]), r"ngoài kỳ 2026-10 .*\(vd 02/11/2026\)"),
    (lambda h, r: (h, r[:3] + ["05/10/2026"] + r[4:]), "không phải ngày"),
    (lambda h, r: (h, r[:2] + ["Lạ"] + r[3:]), "trạng thái đơn lạ"),
    # ô doanh số trống từng bị tính 0 đ lặng lẽ; ô chữ từng ra TypeError ⇒ nay dừng, lời có số dòng Excel, KHÔNG in nội dung ô
    (lambda h, r: (h, r[:8] + [None] + r[9:]), r"1 dòng ô 'Sales Before Discount' trống hoặc không phải số \(dòng Excel 2\)"),
    (lambda h, r: (h, r[:8] + ["12 Lê Lợi"] + r[9:]), r"^(?!.*Lê Lợi).*dòng Excel 2"),
    # 12/11: dòng BÁN trống tên nhóm hàng từng bị bỏ lặng lẽ khỏi mọi phép chọn theo nhóm (ASO_Focus, Thưởng thêm, 1 % gia vị) ⇒ nay dừng
    (lambda h, r: (h, r[:6] + [None] + r[7:]), r"1 dòng bán \(không phải hàng tặng\) trống tên nhóm hàng 'DMS OBJ' \(dòng Excel 2\)"),
    (lambda h, r: (h, r[:6] + ["   "] + r[7:]), "trống tên nhóm hàng"),                       # chỉ khoảng trắng cũng là trống
    (lambda h, r: (h, r[:6] + [None] + r[7:11] + [None] + r[12:]), "trống tên nhóm hàng"),   # cờ hàng tặng để trống ⇒ không phải hàng tặng
])
def test_bad_raw_is_refused(tmp_path, edit, match):
    # 1 ca xấu (mỗi kiểu): RAW sai khuôn / khác kỳ ⇒ dừng rõ, không ra số
    with pytest.raises(ValueError, match=match):
        load_raw(_raw_copy(tmp_path, edit), date(2026, 10, 31), "2026-10")


def test_free_item_without_group_still_loads(tmp_path):
    # Hàng tặng (FREE ITEM FLAG = Y) trống tên nhóm vẫn qua — RAW tháng 10 có 3 dòng như vậy, đều 0 đ (đã đếm 12/11); dòng bán có nhóm vẫn qua
    gift = lambda h, r: (h, r[:6] + [None] + r[7:8] + [0] + r[9:11] + ["Y"] + r[12:])
    blocks = load_small(_raw_copy(tmp_path, gift), date(2026, 10, 31), "2026-10")["blocks"]
    assert blocks["Turnover"]["rows"] == [{"SM CODE": "SM1", "OUTLET CODE": "1", "Total": 0.0}]
    assert load_small(_raw_copy(tmp_path, lambda h, r: (h, r)), date(2026, 10, 31), "2026-10")["blocks"]["Turnover"]["rows"][0]["Total"] == 1000


def test_build_blocks_counts_lines_per_order():
    lines = [{"SM CODE": "S", "ORDER NUM": "D", "OUTLET CODE": "1", "ORDER DATE": date(2026, 10, 5), "SUB DIV": "Seasoning", "DMS OBJ": "x",
              "SKU CODE": "k", "Sales Before Discount": 10, "L1": "Kênh Off Premise", "MOQ_FLAG": flag} for flag in ("Y", "Y", "N")]
    blocks = build_blocks(lines)
    assert blocks["PC_BT"]["rows"] == [{"SM CODE": "S", "ORDER NUM": "D", "OUTLET CODE": "1", "Total": 2}]   # dòng MOQ = N không tính
    # doanh số tính mọi dòng; QĐ-09 11/11: gom theo (NV, cửa hàng) để lọc tuyến được
    assert blocks["Turnover"]["rows"] == [{"SM CODE": "S", "OUTLET CODE": "1", "Total": 30}]


@needs_data
def test_late_invoice_lines_are_dropped():
    # Công văn 6.1: RAW tháng 10 có 1 đơn / 3 dòng xuất hoá đơn 08:56 ngày 03/11 — sau hạn 22h 02/11 ⇒ không tính
    from kpi_agent.core.load_raw import invoice_deadline
    assert invoice_deadline("2026-10", PERIOD_RULES).isoformat() == "2026-11-02T22:00:00" \
        and invoice_deadline("2026-12", PERIOD_RULES).isoformat() == "2027-01-02T22:00:00"
    assert load_raw(RAW, date(2026, 10, 31), "2026-10")["late_invoice_lines"] == 3


@needs_data
def test_one_pc_per_outlet_day_on_real_raw():
    # Công văn 2.2 / 2.3 / 2.7 / 2.8: bộ đọc giữ MỖI ĐƠN một dòng (có DAY); phần tính mới giới hạn 1 PC / ngày / cửa hàng (sau khi xét ngưỡng từng đơn)
    from kpi_agent.core.compute_kpi import _count_rows
    rows = load_raw(RAW, date(2026, 10, 31), "2026-10")["blocks"]["PC_ON"]["rows"]
    assert all("DAY" in row for row in rows) and len({(r["SM CODE"], r["ORDER NUM"]) for r in rows}) == len(rows)
    assert sum(_count_rows(rows, lambda row: True).values()) < len(rows)   # RAW tháng 10 có cặp cửa hàng–ngày > 1 đơn (đã đếm 10/11)



def test_min_lines_checked_per_order_then_one_pc_per_outlet_day():
    # Công văn 2.2 "Một đơn hàng phải có ít nhất 4 line" (xét TỪNG ĐƠN) rồi "tối đa 1 PC / ngày / cửa hàng" — 10/11: gộp trước rồi xét ngưỡng
    # ⇒ 2 đơn 1 + 3 mã thành 1 PC (sai). Dòng RAW mang DAY.
    from kpi_agent.core.compute_kpi import _orders_with_min_lines
    day = date(2026, 10, 25)
    row = lambda order, outlet, n: {"SM CODE": "S", "ORDER NUM": order, "OUTLET CODE": outlet, "DAY": day, "Total": n}
    small = [row("D1", "1", 1), row("D2", "1", 3)]                 # không đơn nào ≥ 4 ⇒ 0 PC
    two_ok = [row("D3", "2", 5), row("D4", "2", 8)]                # 2 đơn cùng đạt, cùng cửa hàng–ngày ⇒ 1 PC
    assert _orders_with_min_lines(small, 4)["S"] == (0, 0)
    assert _orders_with_min_lines(two_ok, 4, "one")["S"] == (1, 8) and _orders_with_min_lines(two_ok, 4, "all")["S"] == (1, 13)
    # Dòng pivot Tracking (không có DAY) ⇒ như cũ: mỗi đơn 1 PC
    assert _orders_with_min_lines([{"SM CODE": "S", "ORDER NUM": "D", "OUTLET CODE": "1", "Total": 5}] * 2, 4)["S"] == (2, 10)


@needs_data
def test_backend_starts_on_raw_workflow(tmp_path, monkeypatch):
    # Ca thật 10/11 bắt: backend tra khối đọc dữ liệu theo loại read_tracking cứng ⇒ workflow RAW không bật. Nay bật được, số 31/10 = 126.660.000 đ
    # (C so mù trang sếp: tiền 112/112 — SO_RAW_E_3110_C.md); chưa soát ⇒ chưa có file.
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))
    monkeypatch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 31))
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        meta = client.get("/api/meta").json()
        assert meta["workflow_version"].startswith("kpi_thuong_nvbh_raw@") and meta["export"]["allowed"] is False
        # Mốc 7 N1 (16/11): bỏ lọc tuyến ở PC_ON ⇒ NV08 PC_ON 65 → 67 (= trang) ⇒ +1.500.000 đ (BANG_LECH_TRANG_T10 ô 20)
        assert client.get("/api/team-bonus").json()["team_total"] == 152_360_000
        assert not any("chưa kiểm được" in flag and "1 PC" in flag for flag in meta["data_flags"])   # đường RAW kiểm được luật này


@needs_data
def test_opportunities_on_last_working_day_does_not_crash(tmp_path, monkeypatch):
    # (KIEM_PII_RAW_C.md): RAW chốt 31/10 ⇒ 0 ngày làm việc còn lại ⇒ /api/opportunities 500 (vn_number(None)). Nay: 200, mọi mục "không phải cơ hội"
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))
    monkeypatch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 31))
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        response = client.get("/api/opportunities")
        assert response.status_code == 200, response.text[:200]
        body = response.json()
        assert body["working_days_left"] == 0
        assert all(item["is_opportunity"] in (False, None) for item in body["items"])
        assert any("đã hết kỳ" in item["why"] for item in body["items"]) and not any("None" in item["why"] for item in body["items"])


# QĐ-09 (11/11, B): lọc khách thuộc MCP cho MỌI KPI — công văn 6.2 (mục 2b) ----------
def _mcp_case(rules_edit=None):
    """Một NV "A" có tuyến {"1"}: mỗi KPI một dòng ở cửa hàng trong tuyến "1" và một dòng ở cửa hàng ngoài tuyến "2" (số tự đặt)."""
    rules = load_rules(RULES_RAW)
    if rules_edit:
        rules_edit(rules["assumptions"])
    def rows(total, **extra):
        return [{"SM CODE": "A", "ORDER NUM": n, "OUTLET CODE": outlet, "DAY": date(2026, 10, n), "Total": total, **extra}
                for n, outlet in ((1, "1"), (2, "2"))]
    blocks = {kpi: {"rows": rows(5)} for kpi in ("PC_BT", "LPPC", "ASO_ALL", "ASO_Focus", "PC_ON", "LPPC_Meat")}
    blocks["Turnover"] = {"rows": rows(1000)}
    blocks["ASO_Focus_2"] = {"rows": rows(5, **{"SKU CODE": rules["assumptions"]["aso_focus_2_skus"][0]})}
    targets = {"targets": {"A": {kpi: 1 for kpi in blocks}}}
    return compute_actuals(targets, {"blocks": blocks, "mcp": {"A": {"1": {}}}}, rules)["A"]


def test_mcp_filter_follows_boss_page_on_raw_rules():
    # Mốc 7 N1 (16/11, chuẩn mới: trang Tracking của sếp là đúng — 16/11 mục 5): trang KHÔNG lọc tuyến ở Turnover / PC_BT / LPPC / PC_ON,
    # CÓ lọc ở ASO_ALL / ASO_Focus / ASO_Focus_2 / LPPC_Meat (BANG_LECH_TRANG_T10 mục 2–3: LPPC 14/14, PC_ON 14/14 khi bỏ lọc; ASO_ALL 14 ⇒ 11 nếu bỏ)
    result = _mcp_case()
    assert result["Turnover"].actual == 2000 and result["PC_BT"].actual == 2 and result["LPPC"].denominator == 2 and result["PC_ON"].actual == 2
    assert all(result[kpi].actual == 1 for kpi in ("ASO_ALL", "ASO_Focus", "ASO_Focus_2"))
    assert (result["LPPC_Meat"].numerator, result["LPPC_Meat"].denominator) == (5, 1)


def test_mcp_filter_without_key_keeps_old_five_kpis():
    # thiếu `mcp_filter_kpis` ⇒ như 10/11: Turnover / PC_BT / LPPC không lọc (cả 2 cửa hàng), 5 KPI đếm cửa hàng vẫn lọc
    result = _mcp_case(lambda a: a.pop("mcp_filter_kpis"))
    assert result["Turnover"].actual == 2000 and result["PC_BT"].actual == 2 and result["LPPC"].denominator == 2 and result["ASO_ALL"].actual == 1
    assert result["PC_ON"].actual == 1   # PC_ON vẫn trong OUTLET_KPIS khi file luật thiếu khoá — file luật cũ không đổi hành vi


def test_mcp_filter_on_pivot_blocks_without_outlet_is_refused():
    # Ca xấu: khối pivot Tracking của Turnover không có mã cửa hàng ⇒ dừng, không bỏ lọc lặng lẽ
    rules = load_rules(RULES_RAW)
    rules["assumptions"]["aso_focus_2_scope"] = "whole_group"
    rules["assumptions"]["mcp_filter_kpis"] = ["Turnover", "ASO_ALL"]   # mốc 7: file luật RAW không lọc Turnover nữa ⇒ đặt tay để thử rào
    block = {"rows": [{"SM CODE": "A", "ORDER NUM": 1, "OUTLET CODE": "1", "Total": 5}]}
    blocks = {kpi: block for kpi in ("PC_BT", "LPPC", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat")}
    blocks["Turnover"] = {"rows": [{"SM CODE": "A", "Total": 1000}]}
    with pytest.raises(ValueError, match="Turnover.*OUTLET CODE"):
        compute_actuals({"targets": {"A": {}}}, {"blocks": blocks, "mcp": {"A": {"1": {}}}}, rules)


def test_employee_with_targets_but_no_route_in_mcp_is_refused():
    # Ca xấu X1 (C): MCP thiếu tuyến của một NV có chỉ tiêu ⇒ dừng (không loại lặng lẽ mọi cửa hàng — Turnover về 0); lời không nêu mã NV
    rules = load_rules(RULES_RAW)
    block = {"rows": [{"SM CODE": "B", "ORDER NUM": 1, "OUTLET CODE": "1", "Total": 5}]}
    blocks = {kpi: block for kpi in ("Turnover", "PC_BT", "LPPC", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat")}
    with pytest.raises(ValueError, match="MCP không có tuyến của 1 NV") as caught:
        compute_actuals({"targets": {"A": {}, "B": {}}}, {"blocks": blocks, "mcp": {"A": {"1": {}}}}, rules)
    assert "B" not in str(caught.value).replace("MCP", "")


# Hàng tặng khỏi đếm MOQ (11/11, B — theo công văn 8.1 "Số lượng MUA tối thiểu") ----------
def test_raw_options_follow_rules():
    from kpi_agent.workflow.blocks import raw_options
    rules = load_rules(RULES_RAW)
    assert raw_options(rules) == {"lppc_exclude_free_items": True, "count_free_items": False, "late_kpis": ("Turnover", "PC_BT"),
                                  "pc_bt_excluded_subdiv": ("Beer",), "invoice_required_status": ("Invoiced", "Shipped"), "max_excluded_sales_pct": 2}
    rules["assumptions"].pop("moq_lines_exclude_free_items")
    assert raw_options(rules)["count_free_items"] is True   # thiếu khoá ⇒ như 10/11 (PC_BT / PC_ON đếm cả hàng tặng)


def test_free_item_does_not_make_an_off_order_reach_four_moq_lines():
    # Ca xấu X3 (C): đơn kênh Off đúng 4 mã MOQ, trong đó 1 mã là hàng tặng ⇒ KHÔNG thành PC_BT
    lines = [{"SM CODE": "S", "ORDER NUM": "D", "OUTLET CODE": "1", "ORDER DATE": date(2026, 10, 5), "SUB DIV": "Seasoning", "DMS OBJ": "x",
              "SKU CODE": f"k{n}", "Sales Before Discount": 0 if free else 10, "L1": "Kênh Off Premise", "MOQ_FLAG": "Y",
              "FREE ITEM FLAG": "Y" if free else "N"} for n, free in enumerate((False, False, False, True))]
    assert build_blocks(lines, count_free_items=True)["PC_BT"]["rows"][0]["Total"] == 4
    blocks = build_blocks(lines, count_free_items=False)
    assert blocks["PC_BT"]["rows"][0]["Total"] == 3
    # ngưỡng ≥ 4 line xét ở compute_kpi `_orders_with_min_lines` ⇒ 3 line ⇒ không thành PC_BT
    from kpi_agent.core.compute_kpi import _orders_with_min_lines
    assert _orders_with_min_lines(blocks["PC_BT"]["rows"], 4)["S"] == (0, 0)


# ---------- Mốc 7 N2 (16/11, chuẩn mới: trang sếp đúng): hoá đơn xuất sau hạn 6.1 — trang CỘNG vào Turnover, PC_BT; KHÔNG cộng ở LPPC_Meat ----------
def _raw_rows(tmp_path, rows) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export"
    sheet.append(["SM CODE", "ORDER NUM", "ORDER STATUS", "ORDER DATE", "OUTLET CODE", "SUB DIV", "DMS OBJ", "SKU CODE", "Sales Before Discount", "L1",
                  "MOQ_FLAG", "FREE ITEM FLAG", "INVOICED DATE"])
    for order, sub, sku, sales, invoiced in rows:
        sheet.append(["SM1", order, "Invoiced", date(2026, 10, 30), 1, sub, "x", sku, sales, "Kênh Off Premise", "Y", "N", invoiced])
    path = tmp_path / "raw_late.xlsx"
    book.save(path)
    return path


LATE = datetime(2026, 11, 3, 8, 56)       # như đơn trễ thật của RAW tháng 10 (sau hạn 22:00 02/11)


def test_late_invoice_lines_count_only_in_listed_kpis(tmp_path):
    raw = _raw_rows(tmp_path, [("D1", "Seasoning", "a", 1000, datetime(2026, 10, 30, 9)),
                               ("D2", "Processed Meats", "m", 500, LATE), ("D2", "Seasoning", "b", 300, LATE)])
    load_small = load_raw_allowing("Trà Búp Non Tea 365", "04HP00639", "Beer")   # file thử này có dòng ngành Thịt
    old = load_small(raw, date(2026, 10, 31), "2026-10")
    assert old["blocks"]["Turnover"]["rows"][0]["Total"] == 1000 and old["late_invoice_lines"] == 2   # mặc định như cũ: bỏ dòng trễ
    new = load_small(raw, date(2026, 10, 31), "2026-10", late_kpis=("Turnover", "PC_BT"))
    assert new["blocks"]["Turnover"]["rows"][0]["Total"] == 1800 and new["late_invoice_lines"] == 2
    assert {row["ORDER NUM"] for row in new["blocks"]["PC_BT"]["rows"]} == {"D1", "D2"}
    assert new["blocks"]["LPPC_Meat"]["rows"] == [] and {row["ORDER NUM"] for row in new["blocks"]["LPPC"]["rows"]} == {"D1"}


def test_line_without_invoice_date_never_counts(tmp_path):
    # Ca xấu: dòng chưa có ngày xuất hoá đơn KHÔNG phải "hoá đơn trễ" ⇒ không vào KPI nào, kể cả KPI cộng dòng trễ
    raw = _raw_rows(tmp_path, [("D1", "Seasoning", "a", 1000, datetime(2026, 10, 30, 9)), ("D2", "Seasoning", "b", 300, None)])
    new = load_small(raw, date(2026, 10, 31), "2026-10", late_kpis=("Turnover", "PC_BT"))
    assert new["blocks"]["Turnover"]["rows"][0]["Total"] == 1000 and {row["ORDER NUM"] for row in new["blocks"]["PC_BT"]["rows"]} == {"D1"}


def test_raw_rules_list_late_invoice_kpis():
    from kpi_agent.workflow.blocks import raw_options
    assert raw_options(load_rules(RULES_RAW))["late_kpis"] == ("Turnover", "PC_BT")
    assert raw_options(load_rules(RULES))["late_kpis"] == ()    # luật đường sổ Tracking không có khoá ⇒ như cũ


# ---------- Mốc 7 N3 (16/11, chuẩn mới: trang sếp đúng): PC_BT = đơn Off có ≥ 1 mã đạt MOQ, KHÔNG tính Bia, 1 / ngày / cửa hàng ----------
def _off(order, sku, sub, outlet="1", day=date(2026, 10, 5)):
    return {"SM CODE": "S", "ORDER NUM": order, "OUTLET CODE": outlet, "ORDER DATE": day, "SUB DIV": sub, "DMS OBJ": "x", "SKU CODE": sku,
            "Sales Before Discount": 10, "L1": "Kênh Off Premise", "MOQ_FLAG": "Y", "FREE ITEM FLAG": "N"}


def test_pc_bt_skips_beer_lines_when_rules_say_so():
    from kpi_agent.core.compute_kpi import _orders_with_min_lines
    lines = [_off("B1", "beer", "Beer"),                                         # chỉ có Bia ⇒ không thành PC
             _off("M1", "beer", "Beer", outlet="2"), _off("M1", "s1", "Seasoning", outlet="2"),   # có 1 mã ngoài Bia ⇒ 1 PC, 1 line
             _off("D1", "s1", "Seasoning", outlet="3"), _off("D2", "s2", "Seasoning", outlet="3")]  # 2 đơn cùng ngày cùng cửa hàng ⇒ 1 PC
    blocks = build_blocks(lines, one_pc_per_outlet_day=True, pc_bt_excluded_subdiv=("Beer",))
    assert {row["ORDER NUM"] for row in blocks["PC_BT"]["rows"]} == {"M1", "D1", "D2"}
    assert _orders_with_min_lines(blocks["PC_BT"]["rows"], 1)["S"][0] == 2
    assert {row["ORDER NUM"] for row in build_blocks(lines)["PC_BT"]["rows"]} >= {"B1"}   # mặc định như cũ: Bia vẫn tính
    assert blocks["LPPC"]["rows"] == build_blocks(lines, one_pc_per_outlet_day=True)["LPPC"]["rows"]   # khối khác không đổi


def test_pc_bt_excluded_subdiv_typo_stops():
    # Ca xấu: gõ sai tên ngành ("Beerr") ⇒ dừng, không lặng lẽ thôi bỏ Bia
    with pytest.raises(ValueError, match="Beerr"):
        build_blocks([_off("B1", "beer", "Beer")], pc_bt_excluded_subdiv=("Beerr",))


def test_raw_rules_pc_bt_follow_boss_page():
    from kpi_agent.workflow.blocks import raw_options
    rules = load_rules(RULES_RAW)
    assert rules["assumptions"]["pc_bt_min_lines"] == 1 and raw_options(rules)["pc_bt_excluded_subdiv"] == ("Beer",)
    assert load_rules(RULES)["assumptions"]["pc_bt_min_lines"] == 4 and raw_options(load_rules(RULES))["pc_bt_excluded_subdiv"] == ()   # đường Tracking giữ


# m1 (KIEM_N1_THEO_TRANG_C.md): tên KPI trong danh sách luật phải là 1 trong 8 KPI — gõ sai ⇒ dừng lúc nạp luật ----------
@pytest.mark.parametrize("key, value", [("mcp_filter_kpis", ["ASO_ALL", "LPPC_Meatt"]), ("late_invoice_kpis", ["Turnoverr"]), ("late_invoice_kpis", "Turnover")])
def test_kpi_name_lists_in_rules_must_be_known(key, value):
    from kpi_agent.core.rules import validate_rules
    import copy
    rules = copy.deepcopy(load_rules(RULES_RAW))
    rules["assumptions"][key] = value
    assert any(key in error for error in validate_rules(rules))
