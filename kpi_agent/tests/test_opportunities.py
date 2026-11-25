"""Tính năng 2 bản đầu "Có cơ hội lên bậc" (, (a) theo nhịp) — trên dữ liệu thật tháng 10.

Kiểm: quy tắc (a) đúng chữ đã chốt, số khớp /api/gap (cùng lõi), không có trường xếp hạng, thứ tự = mã NV rồi KPI, kỳ lạ bị từ chối."""
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")

RATIO_KPIS = {"LPPC", "LPPC_Meat"}


@pytest.fixture(scope="module")
def ctx():
    from kpi_agent.backend.queries import build_context
    return build_context(date(2026, 10, 20), mask_path=str(NEEDED[2]))   # S3: luật / dữ liệu theo file workflow mặc định


def test_rule_a_and_numbers_match_gap(ctx):
    from kpi_agent.backend.opportunities import opportunities
    from kpi_agent.backend.queries import KPI_ORDER, get_mid_month_gap
    body = opportunities(ctx)
    assert "chưa dự báo" in body["label"] and body["working_days_left"] == ctx.days_left
    gaps = {(e["employee_id"], g["kpi"]): g for e in get_mid_month_gap(ctx)["employees"] for g in e["gaps"]}
    real_of = {masked: real for masked, real in ctx.to_real.items()}
    assert {(o["employee_id"], o["kpi"]) for o in body["items"]} == set(gaps)          # đúng mọi cặp còn mốc kế, không thừa không thiếu
    for item in body["items"]:
        gap = gaps[(item["employee_id"], item["kpi"])]
        assert (item["gap_to_next"], item["gap_per_working_day_left"], item["amount_if_reached"], item["amount_now"]) == \
               (gap["gap_to_next"], gap["gap_per_working_day_left"], gap["amount_if_reached"], gap["amount_now"])
        assert item["amount_gain"] == item["amount_if_reached"] - item["amount_now"]
        if item["kpi"] in RATIO_KPIS:
            assert item["is_opportunity"] is None and item["pace_per_working_day"] is None and "tỷ lệ" in item["why"]
        else:
            # TN2b (sau kiểm C): so KHÔNG làm tròn — còn thiếu ≤ nhịp thật × ngày còn lại; làm tròn chỉ để hiện
            real = real_of[item["employee_id"]]
            line = next(l for l in ctx.bonuses[real]["lines"] if l.kpi == item["kpi"])
            raw_pace = ctx.results[real][item["kpi"]].actual / (ctx.days_total - ctx.days_left)
            assert item["is_opportunity"] == (line.gap_to_next <= raw_pace * ctx.days_left)
            assert "/ngày" in item["why"] and ("là tới mốc kế" in item["why"]) == item["is_opportunity"]
            assert ("phải nhanh hơn" in item["why"]) == (not item["is_opportunity"])
        assert (item.get("note") is not None) == item["kpi"].startswith("ASO_")
    order = [(o["employee_id"], KPI_ORDER.index(o["kpi"])) for o in body["items"]]
    assert order == sorted(order)                                                     # mã NV rồi thứ tự KPI — không theo tiền
    assert not {"rank", "score", "priority"} & {k for o in body["items"] for k in o}


def test_pace_edge_equal_counts_as_opportunity():
    # Ca biên ghi trước: giữ nhịp ĐÚNG BẰNG phần thiếu ⇒ là cơ hội. is_opportunity(còn thiếu, nhịp thật / ngày, ngày còn lại)
    from kpi_agent.backend.opportunities import is_opportunity
    assert is_opportunity(30, 3, 10) is True and is_opportunity(31, 3, 10) is False and is_opportunity(0, 0, 10) is True
    assert is_opportunity(1, 0, 10) is False and is_opportunity(None, 3, 10) is None
    # Ca 03/11 (NV09 PC_ON): thiếu 14, nhịp 1,94 × 10 = 19,4 ⇒ CÓ cơ hội (cách cũ ceil(1,4) = 2 > 1,94 loại oan)
    assert is_opportunity(14, 1.94, 10) is True


def test_other_period_refused(ctx):
    from kpi_agent.backend.opportunities import opportunities
    body = opportunities(ctx, period="2026-11")
    assert "refused" in body and "items" not in body


def test_why_product_matches_shown_numbers(ctx):
    # TN2b (minor): người đọc nhân "nhịp hiện × ngày" phải ra đúng tích hiện, và tích hiện không nói ngược kết luận
    from kpi_agent.backend.opportunities import opportunities
    for item in opportunities(ctx)["items"]:
        if item["is_opportunity"] is None:
            continue
        reach_text = item["why"].split("≈ ")[1].split(",")[0] if item["kpi"] == "Turnover" else item["why"].split("≈ ")[1].split(", còn")[0]
        reach = float(reach_text.replace(".", "").replace(",", ".")) if item["kpi"] == "Turnover" else float(reach_text.replace(",", "."))
        assert abs(reach - item["pace_per_working_day"] * ctx.days_left) < 0.01, item["why"]
        assert (reach >= item["gap_to_next"]) == item["is_opportunity"] or item["kpi"] == "Turnover", item["why"]
