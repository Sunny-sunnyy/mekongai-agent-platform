"""Thưởng thêm 01–06 + Meat 7.1–7.3 (`core/compute_extra_bonus.py`, luật `config/extra_bonus_2026_10.yaml`) — dòng nhỏ tự dựng cho từng luật + 1 ca thật RAW 31/10."""
import re
import copy
from datetime import date, datetime
from pathlib import Path

import pytest

from kpi_agent.core.compute_extra_bonus import compute_extra_bonus, count_aso, load_extra_rules, validate_extra_rules
from kpi_agent.core.load_raw import read_raw_lines

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "kpi_agent/config/extra_bonus_2026_10.yaml"
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
KOKOMI = ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json"
needs_data = pytest.mark.skipif(not all(p.exists() for p in (RAW, KOKOMI)), reason="không có dữ liệu thật trên máy này")


def _line(sm="S1", outlet="O1", order="D1", dms="Bia White Lion", sku="X", sales=100000.0, cases=1.0, pieces=1, moq="Y", free="N", row=2, sub_div="Beer"):
    return {"SM CODE": sm, "OUTLET CODE": outlet, "ORDER NUM": order, "DMS OBJ": dms, "SKU CODE": sku, "Sales Before Discount": sales,
            "TOTAL CASES": cases, "TOTAL PIECES": pieces, "MOQ_FLAG": moq, "FREE ITEM FLAG": free, "_row": row, "SUB DIV": sub_div}


def _targets(kokomi: dict, meat: dict | None = None, gate: dict | None = None) -> dict:
    """Chỉ tiêu theo khoản cho `compute_extra_bonus`: T06 = `kokomi`; Meat 7.x = `meat` (mặc định cùng mã với Kokomi, chỉ tiêu 1 đ — doanh số 0 ⇒ 0 %)."""
    meat = meat if meat is not None else {code: 1 for code in kokomi}
    gate = gate if gate is not None else {code: 1 for code in meat}
    return {"T06": {"own": kokomi, "gate": None}, **{item: {"own": meat, "gate": gate} for item in ("T071", "T072", "T073")}}


def _rules():
    return copy.deepcopy(load_extra_rules(RULES))


def _all_groups():
    """Mỗi nhóm hàng trong luật 1 dòng 0 đ ở NV khác — để kiểm 'nhóm có trong RAW' qua mà không đổi số của S1."""
    rules = _rules()
    lines = [_line(sm="S9", dms=name, sales=0.0, cases=0.0, pieces=0) for item in rules["items"].values() for name in item.get("dms_obj") or []]
    lines += [_line(sm="S9", dms="khác", sku=code, sales=0.0, cases=0.0, pieces=0) for item in rules["items"].values() for code in item.get("sku_code") or []]
    return lines


def _by_item(result, code="S1"):
    return {line.item: line for line in result[code]}


def test_rules_file_is_valid_and_label_typo_is_refused():
    rules = _rules()
    assert validate_extra_rules(rules) == []
    rules["items"]["T01"]["min"] = 2            # gõ 20 thành 2 ⇒ số trong nhãn "≥20 thùng" không khớp
    rules["items"]["T06"]["tiers"][1]["amount"] = 50000
    errors = validate_extra_rules(rules)
    assert any("T01" in e for e in errors) and any("T06" in e for e in errors)


def test_cases_pay_every_case_once_threshold_reached():
    # (a) lựa chọn 13:35: đạt ≥ 20 thùng ⇒ trả MỌI thùng (30 thùng ⇒ 180.000 đ); 19 thùng ⇒ 0
    rules = _rules()
    lines = _all_groups() + [_line(cases=20.0), _line(order="D2", cases=10.0), _line(sm="S2", cases=19.0)]
    result = compute_extra_bonus(lines, rules, ["S1", "S2"], {}, _targets({"S1": 1, "S2": 1}))
    assert (_by_item(result)["T01"].actual, _by_item(result)["T01"].amount) == (30.0, 180000)
    assert (_by_item(result, "S2")["T01"].reached, _by_item(result, "S2")["T01"].amount) == (False, 0)


def test_free_items_are_not_counted():
    rules = _rules()
    lines = _all_groups() + [_line(cases=19.0), _line(order="D2", cases=5.0, free="Y", sales=0.0)]
    assert _by_item(compute_extra_bonus(lines, rules, ["S1"], {}, _targets({"S1": 1})))["T01"].actual == 19.0


@pytest.mark.parametrize("rule, expected", [
    ({"kind": "moq_8_1"}, 2),                       # O1 (dòng MOQ) + O3 (dòng MOQ); O2 chỉ dòng không đạt MOQ
    ({"kind": "min_amount", "value": 150000}, 2),   # O1 200.000 + O2 150.000; O3 100.000
    ({"kind": "min_amount_per_order", "value": 150000}, 1),   # chỉ O2 có MỘT đơn ≥ 150.000 (O1 đạt 200.000 cả tháng nhờ 2 đơn × 100.000 ⇒ không tính)
    ({"kind": "min_qty_per_order", "value": 2}, 1), # chỉ O2 có MỘT đơn ≥ 2 đơn vị (O1 hai đơn × 1)
    ({"kind": "any"}, 3),
])
def test_aso_definitions(rule, expected):
    lines = [_line(outlet="O1", order="D1", sales=100000.0, pieces=1), _line(outlet="O1", order="D2", sales=100000.0, pieces=1),
             _line(outlet="O2", order="D3", sales=150000.0, pieces=2, moq="N"),
             _line(outlet="O3", order="D4", sales=100000.0, pieces=1)]
    assert count_aso(lines, rule, lambda sm, outlet: True)["S1"] == expected



@pytest.mark.parametrize("value", [None, "150.000", 0, True])
def test_aso_rule_without_numeric_threshold_is_refused(value):
    # Ca xấu (11/11): kiểu đếm có ngưỡng mà file luật thiếu / gõ sai số ⇒ dừng lúc nạp luật
    bad = _rules()
    bad["items"]["T03"]["aso_rule"] = {"kind": "min_amount_per_order"} if value is None else {"kind": "min_amount_per_order", "value": value}
    assert any("T03: aso_rule.value" in error for error in validate_extra_rules(bad))

def test_aso_outside_route_is_dropped():
    lines = [_line(outlet="O1"), _line(outlet="O2")]
    assert count_aso(lines, {"kind": "any"}, lambda sm, outlet: outlet == "O1")["S1"] == 1


@pytest.mark.parametrize("sales, amount", [(9_999_999, 0), (10_000_000, 300000), (10_799_999, 300000), (10_800_000, 500000)])
def test_kokomi_tiers_are_steps_not_added(sales, amount):
    # (c) bậc: 100 % ⇒ 300k, ≥ 108 % ⇒ 500k (không cộng 800k); chỉ tiêu 10.000.000 đ
    rules = _rules()
    lines = _all_groups() + [_line(dms="Mì Kokomi Đại 90", sales=float(sales))]
    assert _by_item(compute_extra_bonus(lines, rules, ["S1"], {}, _targets({"S1": 10_000_000})))["T06"].amount == amount


def test_bad_inputs_stop_instead_of_counting_zero():
    rules = _rules()
    with pytest.raises(ValueError, match="không có trong RAW"):      # gõ sai tên nhóm ⇒ dừng, không thành 0 thùng
        bad = copy.deepcopy(rules)
        bad["items"]["T01"]["dms_obj"] = ["Bia White Lionn"]
        compute_extra_bonus(_all_groups(), bad, ["S1"], {}, _targets({"S1": 1}))
    with pytest.raises(ValueError, match="thiếu chỉ tiêu"):          # NV không có chỉ tiêu Kokomi ⇒ dừng
        compute_extra_bonus(_all_groups(), rules, ["S1"], {}, _targets({}))
    with pytest.raises(ValueError, match="dòng Excel 7"):            # ô thùng trống ⇒ dừng, chỉ đúng dòng
        compute_extra_bonus(_all_groups() + [_line(cases=None, row=7)], rules, ["S1"], {}, _targets({"S1": 1}))


@needs_data
def test_real_raw_3110_matches_page_totals_and_kpi_6():
    from kpi_agent.scripts import tinh_thuong_them
    result, rules, lines, mcp = tinh_thuong_them.compute(str(RAW), date(2026, 10, 31))
    team = {item: sum(_by_item(result, code)[item].actual for code in result) for item in rules["items"]}
    assert team["T01"] == 724 and team["T02"] == 74            # trang sếp: 724 / 74 thùng
    assert team["T05"] == 393                                  # ≥ 2 túi / đơn (chữ sổ) = KPI 6 = trang
    assert team["T04"] == 59                                   # ≥ 2 hộp / đơn, 2 nhóm hộp (chữ sổ ô W1) = trang 59, 14 / 14 NV (11/11)
    assert team["T03"] == 249                                  # 11/11 chiều: + "Nước chấm Nam Ngư pha sẵn" (phạm vi theo trang, nhóm kỹ thuật) = trang 14/14 · trước: ≥ 150.000 đ / đơn (chữ sổ ô Q1); trang 249 — RAW thiếu 4 cửa hàng (file luật (d))
    # Kokomi: trang 313.490.000 tính cả đơn hoá đơn trễ hạn (NV05, 2.300.000 đ Kokomi). Mốc 7 N2 (16/11, trang sếp là chuẩn): T06 cộng dòng trễ như trang
    # (trước: bỏ theo công văn 6.1 ⇒ 311.190.000)
    assert abs(team["T06"] - 313_490_000) < 1        # RAW có doanh số lẻ (0,54 đ)
    assert team["T01"] == 724 and team["T02"] == 74   # khoản không cộng trễ: không đổi


# ---------- Mốc 7 N2 (16/11, trang sếp là chuẩn): dòng hoá đơn trễ (cờ LATE) chỉ vào T06 + cổng 07 — khoản trang chứng minh ----------
def test_late_lines_count_only_in_items_marked_include_late():
    # S1: Kokomi đúng hạn 6 tr + trễ 4 tr (chỉ tiêu 10 tr ⇒ chỉ đạt 100 % khi cộng trễ); bia 10 thùng đúng hạn + 10 thùng trễ (T01 cần ≥ 20 ⇒ trễ KHÔNG cộng ⇒ 0)
    # Meat 7.1: riêng 10 tr đúng hạn (100 %); cổng TPCB 10 tr đúng hạn + 10 tr trễ (chỉ tiêu 20 tr ⇒ chỉ qua cổng khi cộng trễ)
    late = lambda line: {**line, "LATE": True}
    lines = _all_groups() + [
        _line(dms="Mì Kokomi Đại 90", sales=6_000_000.0), late(_line(order="L1", dms="Mì Kokomi Đại 90", sales=4_000_000.0)),
        _line(cases=10.0), late(_line(order="L2", cases=10.0)),
        _line(order="M1", dms="Hotdog Ponnie", sales=10_000_000.0, sub_div="Processed Meats"),
        late(_line(order="L3", dms="Xúc xích Ponnie", sales=10_000_000.0, sub_div="Processed Meats")),
        late(_line(order="L4", dms="Hotdog Ponnie", sales=5_000_000.0, sub_div="Processed Meats")),   # trễ nhóm 7.1: vào cổng, KHÔNG vào doanh số riêng 7.1
    ]
    targets = _targets({"S1": 10_000_000}, meat={"S1": 10_000_000}, gate={"S1": 25_000_000})
    by_item = _by_item(compute_extra_bonus(lines, _rules(), ["S1"], {}, targets))
    assert by_item["T06"].actual == 10_000_000 and by_item["T06"].amount == 300000
    assert by_item["T01"].actual == 10 and by_item["T01"].amount == 0
    assert by_item["T071"].actual == 10_000_000 and by_item["T071"].amount == 250_000   # cổng 25 tr ÷ 25 tr = 100 % ⇒ mức 1


def test_lines_without_late_flag_behave_as_before():
    # read_raw_lines không giữ dòng trễ ⇒ không có khoá LATE ⇒ mọi khoản như cũ (file luật khác / đường gọi cũ không đổi)
    lines = _all_groups() + [_line(dms="Mì Kokomi Đại 90", sales=10_000_000.0)]
    assert _by_item(compute_extra_bonus(lines, _rules(), ["S1"], {}, _targets({"S1": 10_000_000})))["T06"].amount == 300000


def test_include_late_flag_must_be_true_or_false():
    # Ca xấu: gõ "yes" / "có" ⇒ dừng lúc nạp (không đoán nghĩa)
    rules = _rules()
    rules["items"]["T06"]["include_late_invoice"] = "yes"
    rules["items"]["T071"]["gate"]["include_late_invoice"] = 1
    errors = validate_extra_rules(rules)
    assert any("T06" in e and "include_late_invoice" in e for e in errors) and any("T071" in e and "include_late_invoice" in e for e in errors)


# ---------- Meat 7.1–7.3 (11/11, B — chữ sổ Tracking AU1 / BA1 / BG1; cổng P1: % riêng VÀ % TPCB cùng tới mức) ----------
def _meat(sales_71: float, tpcb_extra: float, target_71=10_000_000, target_tpcb=20_000_000):
    """S1: doanh số nhóm 7.1 = sales_71; TPCB (cả Processed Meats) = sales_71 + tpcb_extra. Chỉ tiêu 7.1 / TPCB tự đặt."""
    lines = _all_groups() + [_line(dms="Hotdog Ponnie", sales=float(sales_71), sub_div="Processed Meats"),
                             _line(order="D2", dms="Xúc xích Ponnie", sales=float(tpcb_extra), sub_div="Processed Meats")]
    targets = _targets({"S1": 1}, meat={"S1": target_71}, gate={"S1": target_tpcb})
    return _by_item(compute_extra_bonus(lines, _rules(), ["S1"], {}, targets))["T071"]


@pytest.mark.parametrize("sales_71, tpcb_extra, amount", [
    (10_000_000, 10_000_000, 250_000),    # 100 % / TPCB 100 % ⇒ mức 1: 2,5 %
    (10_500_000, 10_500_000, 525_000),    # 105 % / TPCB 105 % ⇒ mức 2: 5 %
    (10_500_000, 9_500_000, 262_500),     # 105 % nhưng TPCB 100 % ⇒ chỉ mức 1 (cổng cùng mức)
    (12_000_000, 7_000_000, 0),           # 120 % nhưng TPCB 95 % ⇒ 0 (cổng chặn)
    (9_999_999, 20_000_000, 0),           # 99,99 % ⇒ chưa tới mức
    (40_000_000, 10_000_000, 1_500_000),  # 400 % ⇒ 5 % × 40 tr = 2 tr ⇒ trần 1,5 tr
])
def test_meat_rate_with_gate_and_cap(sales_71, tpcb_extra, amount):
    assert _meat(sales_71, tpcb_extra).amount == amount


def test_meat_rules_bad_shape_stop_at_load():
    # Ca xấu: luật thiếu trần / cổng sai khoá ⇒ dừng lúc nạp (không tính tiền không trần, không bỏ cổng lặng lẽ)
    rules = _rules()
    assert validate_extra_rules(rules) == []
    rules["items"]["T071"].pop("cap")
    rules["items"]["T072"]["gate"] = {"targets_key": "07", "sub_div": "Processed Meats", "same_tier": False}
    rules["items"]["T073"]["tiers"] = [{"min": 105}]
    errors = validate_extra_rules(rules)
    assert any("T071" in e and "cap" in e for e in errors) and any("T072" in e and "gate" in e for e in errors) \
        and any("T073" in e and "rate_pct" in e for e in errors)


def test_meat_missing_target_stops():
    lines = _all_groups() + [_line(dms="Hotdog Ponnie", sub_div="Processed Meats")]
    with pytest.raises(ValueError, match="thiếu chỉ tiêu cổng"):
        compute_extra_bonus(lines, _rules(), ["S1"], {}, _targets({"S1": 1}, meat={"S1": 1}, gate={}))


def test_how_text_uses_vietnamese_number_format():
    # 12/11: chữ "Cách tính" từng viết số kiểu Anh ("10,693,500 đ … 172.50 %") trong khi mọi chỗ khác kiểu Việt. Số tự đặt.
    from kpi_agent.core import compute_extra_bonus as extra
    assert extra._vn(10693500, ",.0f") == "10.693.500" and extra._vn(74.5) == "74,5" and extra._vn(6000, ",") == "6.000"
    unit = extra._unit_line("T01", {"title": "x", "measure": "cases", "min": 20, "pay_per_unit": 6000}, 74.5)
    assert unit.how == "74,5 thùng ≥ 20 ⇒ 74,5 × 6.000 đ" and unit.amount == 447000
    tier = extra._tier_line("T06", {"title": "x", "targets_file": "f", "tiers": [{"min": 100, "amount": 300000, "label": "100%: 300k"}]},
                            16_040_250.0, {"SM1": 10_693_500.0}, "SM1")
    assert tier.how == "16.040.250 đ ÷ 10.693.500 đ = 150,00 % ⇒ mức '100%: 300k'"
    item = {"title": "x", "tiers": [{"min": 100, "rate_pct": 2.5}, {"min": 105, "rate_pct": 5}], "cap": 1500000, "gate": {"sub_div": "Processed Meats"}}
    rate = extra._rate_line("T071", item, 40_000_000.0, {"SM1": 10_000_000.0}, "SM1", (120_000_000.0, {"SM1": 100_000_000.0}))
    assert rate.how == ("40.000.000 đ ÷ 10.000.000 đ = 400,00 %; cổng Processed Meats 120.000.000 đ ÷ 100.000.000 đ = 120,00 %"
                        " ⇒ mức 105 %: 5 % × doanh số, trần 1.500.000 đ") and rate.amount == 1_500_000
    for line in (unit, tier, rate):
        assert not re.search(r"\d,\d{3}", line.how) and not re.search(r"\d\.\d{1,2}(?!\d)", line.how)   # không còn "1,234" hay "172.5" kiểu Anh
