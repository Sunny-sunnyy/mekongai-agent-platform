"""Bậc 3a — giá trị theo THÁNG của đường RAW (trạng thái được tính, hạn xuất hoá đơn, ngành trừ khỏi LPPC, ngành KPI 8, nhóm / mã KPI 5 – 6) nằm ở
file luật kỳ (`assumptions`), không ở code. Thiếu khoá ⇒ dừng nêu tên khoá (không mặc định về tháng 10). File RAW + luật ở đây DỰNG GIẢ trong thư
mục tạm (không lấy từ dữ liệu thật); luật giả = bản chép `rules_2026_10_raw.yaml` rồi sửa đúng khoá cần thử. Không mock.
"""
import copy
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest
import yaml

from kpi_agent.core.compute_kpi import compute_actuals
from kpi_agent.core.load_raw import load_raw, period_rules
from kpi_agent.workflow.blocks import BlockStop, raw_options, run_block

ROOT = Path(__file__).resolve().parents[2]
RULES_RAW = ROOT / "kpi_agent/config/rules_2026_10_raw.yaml"
OFF, ON = "Kênh Off Premise", "Kênh On Premise"
HEADER = ["SM CODE", "ORDER NUM", "ORDER STATUS", "ORDER DATE", "OUTLET CODE", "SUB DIV", "DMS OBJ", "SKU CODE", "Sales Before Discount", "L1",
          "MOQ_FLAG", "FREE ITEM FLAG", "INVOICED DATE"]
DAY, INVOICED = date(2026, 10, 5), datetime(2026, 10, 5, 9)
KPIS = ("Turnover", "PC_BT", "LPPC", "ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat")


def line(order, sku, group, sub="Seasoning", sales=1000, status="Invoiced", invoiced=INVOICED, outlet=1, channel=OFF):
    return ["SM1", order, status, DAY, outlet, sub, group, sku, sales, channel, "Y", "N", invoiced]


# Mỗi giá trị luật tháng 10 có ít nhất một dòng (nhóm Trà 365, mã Homey 2,9 kg, ngành Thịt chế biến, Bia) + một dòng nhóm khác
BASE = [line("D1", "T365", "Trà Búp Non Tea 365"), line("D2", "04HP00639", "Nước giặt xả Homey"), line("D3", "M1", "Thịt nguội", sub="Processed Meats"),
        line("D4", "B1", "Bia lon", sub="Beer"), line("D5", "X99", "Nhóm khác")]


def raw_file(tmp_path: Path, rows: list[list]) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export"
    sheet.append(HEADER)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "raw.xlsx"
    book.save(path)
    return path


def rules(**changes) -> dict:
    """Luật giả = luật RAW tháng 10 + sửa khoá trong assumptions (giá trị None ⇒ xoá khoá)."""
    data = copy.deepcopy(yaml.safe_load(RULES_RAW.read_text(encoding="utf-8")))
    for key, value in changes.items():
        if value is None:
            data["assumptions"].pop(key, None)
        else:
            data["assumptions"][key] = value
    return data


def load(tmp_path, rows, rule_data, cutoff=date(2026, 10, 31)) -> dict:
    return load_raw(raw_file(tmp_path, rows), cutoff, rule_data["period"], **raw_options(rule_data), period_rules=period_rules(rule_data["assumptions"]))


def actuals(tmp_path, rows, rule_data) -> dict:
    tracking = {**load(tmp_path, rows, rule_data), "mcp": {"SM1": {"1": {}, "2": {}}}}
    return compute_actuals({"targets": {"SM1": dict.fromkeys(KPIS, 1)}}, tracking, rule_data)["SM1"]


# ---------- (a) thiếu khoá ⇒ dừng nêu đúng tên khoá ----------
@pytest.mark.parametrize("key", ["counted_status", "invoice_deadline", "lppc_excluded_subdiv", "lppc_meat_subdiv", "aso_focus_groups",
                                 "aso_focus_2_label"])
def test_missing_key_stops_with_key_name(key):
    with pytest.raises(ValueError, match=f"thiếu khoá `{key}`"):
        period_rules(rules(**{key: None})["assumptions"])


def test_kpi6_groups_required_when_not_sku_list():
    with pytest.raises(ValueError, match="thiếu khoá `aso_focus_2_groups`"):
        period_rules(rules(aso_focus_2_scope="whole_group", aso_focus_2_groups=None)["assumptions"])


def test_kpi5_skus_need_label_and_not_both_lists():
    with pytest.raises(ValueError, match="thiếu khoá `aso_focus_label`"):
        period_rules(rules(aso_focus_groups=None, aso_focus_skus=["T365"])["assumptions"])
    with pytest.raises(ValueError, match="aso_focus_groups.*aso_focus_skus"):
        period_rules(rules(aso_focus_skus=["T365"])["assumptions"])


@pytest.mark.parametrize("key, value", [("counted_status", ["Invoiced", "Shiped"]), ("counted_status", "Invoiced"), ("invoice_deadline", {"day": 2}),
                                        ("invoice_deadline", {"day": 31, "hour": 22}), ("lppc_meat_subdiv", ""), ("aso_focus_groups", [])])
def test_bad_value_stops_with_key_name(key, value):
    with pytest.raises(ValueError, match=key):
        period_rules(rules(**{key: value})["assumptions"])


def test_reader_has_no_default_period_rules(tmp_path):
    # Không có đường "quên truyền luật kỳ ⇒ dùng giá trị tháng 10": thiếu tham số là lỗi gọi hàm
    with pytest.raises(TypeError, match="period_rules"):
        load_raw(raw_file(tmp_path, BASE), date(2026, 10, 31), "2026-10")


def test_workflow_block_turns_missing_key_into_block_stop(tmp_path):
    mcp = tmp_path / "mcp.json"
    mcp.write_text('{"mcp": {"SM1": {"1": {"channel": "Off"}}}}', encoding="utf-8")
    params = {"path": str(raw_file(tmp_path, BASE)), "mcp_path": str(mcp)}
    with pytest.raises(BlockStop, match="thiếu khoá `aso_focus_groups`"):
        run_block("read_raw", {"rules": rules(aso_focus_groups=None)}, params, {"cutoff": date(2026, 10, 31)})
    assert run_block("read_raw", {"rules": rules()}, params, {"cutoff": date(2026, 10, 31)})["tracking"]["source"] == "raw"


# ---------- (b) KPI 6 theo danh sách mã KHÔNG lọc nhóm trước ----------
def test_kpi6_sku_from_another_group_is_counted(tmp_path):
    # Trước 3a: khối KPI 6 lọc nhóm "Nước giặt xả Homey" rồi mới lọc mã ⇒ mã thuộc nhóm khác ra 0 lặng lẽ
    other = rules(aso_focus_2_skus=["X99"], aso_focus_2_label="Mã thử nhóm khác", aso_focus_2_groups=None)
    result = actuals(tmp_path, BASE, other)
    assert result["ASO_Focus_2"].actual == 1
    assert actuals(tmp_path, BASE, rules())["ASO_Focus_2"].actual == 1    # luật tháng 10: mã 04HP00639 vẫn đếm


# ---------- (c) KPI 5 theo danh sách mã ----------
def test_kpi5_by_sku_list(tmp_path):
    by_sku = rules(aso_focus_groups=None, aso_focus_skus=["X99"], aso_focus_label="Mã thử KPI 5")
    rows = BASE + [line("D6", "T365", "Trà Búp Non Tea 365", outlet=2)]
    assert actuals(tmp_path, rows, rules())["ASO_Focus"].actual == 2          # theo nhóm: 2 cửa hàng có Trà 365
    result = actuals(tmp_path, rows, by_sku)
    assert result["ASO_Focus"].actual == 1


# ---------- (e) đổi giá trị trong luật ⇒ kết quả đổi theo (không còn ghi cứng) ----------
def test_counted_status_follows_rules(tmp_path):
    rows = BASE + [line("S1", "X99", "Nhóm khác", sales=500, status="Shipped")]
    assert load(tmp_path, rows, rules())["blocks"]["Turnover"]["rows"][0]["Total"] == 5500
    only_invoiced = rules(counted_status=["Invoiced"], invoice_required_status=["Invoiced"])
    assert load(tmp_path, rows, only_invoiced)["blocks"]["Turnover"]["rows"][0]["Total"] == 5000


def test_invoice_deadline_follows_rules(tmp_path):
    rows = BASE + [line("L1", "X99", "Nhóm khác", sales=700, invoiced=datetime(2026, 11, 3, 8, 56))]   # sau 22:00 ngày 02/11
    assert load(tmp_path, rows, rules())["late_invoice_lines"] == 1
    assert load(tmp_path, rows, rules(invoice_deadline={"day": 3, "hour": 9}))["late_invoice_lines"] == 0


def test_lppc_subdivs_follow_rules(tmp_path):
    rows = [line("LP", f"S{n}", "Nhóm khác", sub="Processed Meats") for n in range(4)] + BASE
    orders = lambda data: {row["ORDER NUM"] for row in load(tmp_path, rows, data)["blocks"]["LPPC"]["rows"]}
    assert "LP" not in orders(rules())                                         # tháng 10: Thịt chế biến không vào LPPC
    assert "LP" in orders(rules(lppc_excluded_subdiv=["Beer"]))
    meat = rules(lppc_meat_subdiv="Beer")
    assert {row["ORDER NUM"] for row in load(tmp_path, rows, meat)["blocks"]["LPPC_Meat"]["rows"]} == {"D4"}


# ---------- (f) P2: mã / nhóm / ngành luật nêu mà file không có dòng nào ⇒ dừng; cho qua phải có ghi chú ----------
ABSENT = [("aso_focus_2_skus", ["KHONGCO"], "KHONGCO"), ("aso_focus_groups", ["Nhóm không có"], "Nhóm không có"),
          ("lppc_excluded_subdiv", ["Processed Meats", "Ngành lạ"], "Ngành lạ"), ("lppc_meat_subdiv", "Ngành lạ", "Ngành lạ")]


@pytest.mark.parametrize("key, value, absent", ABSENT)
def test_rule_value_without_any_line_stops(tmp_path, key, value, absent):
    # Trước P2: mã / nhóm / ngành gõ sai (hoặc tháng đó không bán) ⇒ KPI ra 0 hoặc LPPC thôi trừ ngành mà không ai thấy
    with pytest.raises(ValueError, match=f"luật kỳ nêu .*{absent}.*không có dòng nào"):
        load(tmp_path, BASE, rules(**{key: value}))


def test_allow_without_lines_needs_note(tmp_path):
    for entry in ({"kpi": "ASO_Focus_2", "ma": "KHONGCO"}, {"kpi": "ASO_Focus_2", "ma": "KHONGCO", "ghi_chu": "  "}, "KHONGCO",
                  {"ma": "KHONGCO", "ghi_chu": "thiếu kpi"}, {"kpi": "Turnover", "ma": "KHONGCO", "ghi_chu": "KPI không nêu mã / nhóm / ngành"}):
        with pytest.raises(ValueError, match="cho_qua_khong_co_don.*ghi_chu"):
            period_rules(rules(aso_focus_2_skus=["KHONGCO"], cho_qua_khong_co_don=[entry])["assumptions"])


def test_allowed_value_without_lines_computes_and_leaves_a_notice(tmp_path):
    from kpi_agent.core.check_data import check_data
    allowed = rules(aso_focus_2_skus=["KHONGCO"], cho_qua_khong_co_don=[{"kpi": "ASO_Focus_2", "ma": "KHONGCO", "ghi_chu": "tháng này chưa mở bán"}])
    tracking = {**load(tmp_path, BASE, allowed), "mcp": {"SM1": {"1": {"channel": OFF}}}}
    targets = {"employees": {"SM1": {}}, "targets": {"SM1": dict.fromkeys(KPIS, 1)}}
    assert compute_actuals(targets, tracking, allowed)["SM1"]["ASO_Focus_2"].actual == 0
    notices = [issue["message"] for issue in check_data(targets, tracking, date(2026, 10, 31)) if "KHONGCO" in issue["message"]]
    assert len(notices) == 1 and "tháng này chưa mở bán" in notices[0]
    # luật tháng 10 trên file có đủ giá trị ⇒ không có dòng lưu ý nào loại này (sàn tháng 10 không đổi)
    full = {**load(tmp_path, BASE, rules()), "mcp": {"SM1": {"1": {"channel": OFF}}}}
    assert full["rule_values_without_lines"] == [] and full["unused_rule_allowances"] == []


def test_allowance_is_per_kpi_and_an_unused_one_leaves_a_notice(tmp_path):
    # 19/11 (minor "sai lặng lẽ"): mục cho qua khớp theo giá trị không kèm KPI, và mục THỪA bị bỏ qua không ai thấy
    from kpi_agent.core.check_data import check_data
    note = "tháng này không bán"
    # cho qua «Ngành lạ» ở KPI 8 không được cho qua luôn ở KPI 3
    other_kpi = rules(lppc_excluded_subdiv=["Processed Meats", "Ngành lạ"], cho_qua_khong_co_don=[{"kpi": "LPPC_Meat", "ma": "Ngành lạ", "ghi_chu": note}])
    with pytest.raises(ValueError, match="luật kỳ nêu KPI 3 .*Ngành lạ.*không có dòng nào"):
        load(tmp_path, BASE, other_kpi)
    # mục thừa: giá trị luật không nêu (gõ nhầm mã) và giá trị file CÓ dòng ⇒ vẫn tính, mỗi mục một dòng lưu ý
    spare = [{"kpi": "ASO_Focus_2", "ma": "GONHAM", "ghi_chu": note}, {"kpi": "LPPC_Meat", "ma": "Processed Meats", "ghi_chu": note}]
    tracking = {**load(tmp_path, BASE, rules(cho_qua_khong_co_don=spare)), "mcp": {"SM1": {"1": {"channel": OFF}}}}
    assert tracking["rule_values_without_lines"] == []
    assert tracking["unused_rule_allowances"] == [{"kpi": "ASO_Focus_2", "value": "GONHAM"}, {"kpi": "LPPC_Meat", "value": "Processed Meats"}]
    targets = {"employees": {"SM1": {}}, "targets": {"SM1": dict.fromkeys(KPIS, 1)}}
    notices = [issue["message"] for issue in check_data(targets, tracking, date(2026, 10, 31)) if "không dùng tới" in issue["message"]]
    assert len(notices) == 2 and "GONHAM" in notices[0] and "Processed Meats" in notices[1]


# ---------- (g) P4: chữ "cách đếm" + số mục công văn lấy từ luật kỳ ----------
def test_how_strings_use_names_and_sections_of_the_period(tmp_path):
    # Trước P4: chữ "cách đếm" luôn ghi "Trà Búp Non Tea 365", "Homey túi 2,9 kg", "Processed Meats", "(mục 2.x)" dù luật kỳ nói khác
    october = actuals(tmp_path, BASE, rules())
    assert october["ASO_Focus"].how == "số cửa hàng mua Trà Búp Non Tea 365 đạt MOQ (mục 2.5)"
    assert october["ASO_Focus_2"].how == "số cửa hàng mua Nước giặt xả Homey túi 2,9 kg đạt MOQ (mục 2.6, theo công văn + trang sếp)"
    assert october["LPPC_Meat"].how == "1 line Processed Meats ÷ 1 đơn có ≥ 1 line đạt MOQ (mục 2.8)"
    other = rules(aso_focus_groups=["Nhóm khác"], aso_focus_2_skus=["X99"], aso_focus_2_label="Mã thử", aso_focus_2_groups=None, lppc_meat_subdiv="Beer")
    other["cong_van_sections"] = {"ASO_Focus": "3.5", "LPPC_Meat": "3.8"}          # công văn tháng khác đánh số khác; KPI 6 không ghi mục
    result = actuals(tmp_path, BASE, other)
    assert result["ASO_Focus"].how == "số cửa hàng mua Nhóm khác đạt MOQ (mục 3.5)"
    assert result["ASO_Focus_2"].how == "số cửa hàng mua Mã thử đạt MOQ (theo công văn + trang sếp)"
    assert result["LPPC_Meat"].how == "1 line Beer ÷ 1 đơn có ≥ 1 line đạt MOQ (mục 3.8)"
    assert result["PC_ON"].how == "số đơn kênh ON có ≥ 1 mã đạt MOQ"            # luật không ghi mục ⇒ không nêu mục (không đoán)


def test_tool_section_cell_follows_rules_and_is_absent_when_not_written():
    from types import SimpleNamespace

    from kpi_agent.backend.queries import cong_van_section
    assert cong_van_section(SimpleNamespace(rules=rules()), "PC_ON") == {"cong_van_section": "2.7"}
    without = rules()
    del without["cong_van_sections"]
    assert cong_van_section(SimpleNamespace(rules=without), "PC_ON") == {}


# ---------- (h) sau kiểm độc lập 19/11 (m1): khoá LẠ trong file luật kỳ ⇒ dừng nêu tên khoá + gợi ý ----------
def test_unknown_assumption_key_stops_with_its_name_and_a_hint(tmp_path):
    # Trước khi sửa: gõ nhầm tên một khoá không bắt buộc ⇒ code coi như không có khoá, luật tưởng đang áp lại không áp, không ai báo
    from kpi_agent.core.rules import RulesError, load_rules, validate_rules
    typo = rules(cho_qua_khong_co_donn=[{"ma": "KHONGCO", "ghi_chu": "gõ nhầm tên khoá"}])
    errors = validate_rules(typo)
    assert len(errors) == 1 and "cho_qua_khong_co_donn" in errors[0] and "`cho_qua_khong_co_don`" in errors[0], errors
    path = tmp_path / "rules_typo.yaml"
    path.write_text(yaml.safe_dump(rules(late_invoice_kpi=["Turnover"]), allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(RulesError, match="late_invoice_kpi.*late_invoice_kpis"):
        load_rules(path)


@pytest.mark.parametrize("name", ["rules_2026_10.yaml", "rules_2026_10_raw.yaml"])
def test_real_rule_files_have_only_known_assumption_keys(name):
    from kpi_agent.core.rules import KNOWN_ASSUMPTIONS, load_rules
    assert set(load_rules(ROOT / "kpi_agent/config" / name)["assumptions"]) <= KNOWN_ASSUMPTIONS
