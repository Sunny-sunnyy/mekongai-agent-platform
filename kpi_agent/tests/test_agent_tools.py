"""G4a — 4 công cụ chỉ đọc chạy trên dữ liệu thật tháng 10 (file Git bỏ qua — máy khác không có thì bỏ qua test).

Kiểm: số khớp lõi tính (cùng tổng 55.700.000 đ như run_kpi), tham số lạ bị từ chối mà không trả số, không lộ tên / mã thật,
cờ dữ liệu luôn có, số trong kết quả công cụ qua được phép kiểm "số có nguồn" khi agent chép lại đúng.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from kpi_agent.agent.number_check import check_numbers
from kpi_agent.backend.queries import (TOOLS, build_context, explain_kpi, get_employee_result, get_mid_month_gap,
                                   get_team_bonus_table)
from kpi_agent.core.load_data import load_targets

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
TRACKING = ROOT / "sample_data/thang_10/Tracking.xlsx"
MASK = ROOT / "kpi_agent/data/ma_che_ten.json"

pytestmark = pytest.mark.skipif(not (TARGETS.exists() and TRACKING.exists() and MASK.exists()),
                                reason="không có dữ liệu thật trên máy này")


@pytest.fixture(scope="module")
def ctx():
    # S3: đường file luật / dữ liệu lấy từ file workflow mặc định (cùng 3 file như trước)
    return build_context(date(2026, 10, 20), mask_path=str(MASK))


def test_team_table_matches_core_total(ctx):
    table = get_team_bonus_table(ctx)
    assert table["employee_count"] == 14 and table["team_total"] == 55_700_000
    assert [row["employee_id"] for row in table["employees"]] == [f"NV{i:02d}" for i in range(1, 15)]


def test_employee_result_sums_to_business_salary(ctx):
    result = get_employee_result(ctx, "NV04")
    assert len(result["kpis"]) == 8
    assert sum(k["amount"] for k in result["kpis"]) == result["business_salary"] == 5_740_000   # số tầng 2 đã kiểm (B, ChatGPT)


@pytest.mark.parametrize("bad", ["../NV01", "NV01/../NV02", "NV01%2F..", "/etc/passwd", "NV99", "NV1", "", "nv 01", 5, None])
def test_bad_employee_id_is_refused_without_numbers(ctx, bad):
    for call in (lambda: get_employee_result(ctx, bad), lambda: explain_kpi(ctx, bad, "PC_BT")):
        result = call()
        assert set(result) == {"refused", "params_used"}, result
    if bad not in (None, ""):          # bỏ trống ở get_mid_month_gap nghĩa là "cả đội" — hợp lệ
        assert set(get_mid_month_gap(ctx, bad)) == {"refused", "params_used"}


@pytest.mark.parametrize("bad_kpi", ["../PC_BT", "PCBT", "lương cơ bản", ""])
def test_bad_kpi_is_refused(ctx, bad_kpi):
    assert set(explain_kpi(ctx, "NV04", bad_kpi)) == {"refused", "params_used"}


def test_kpi_and_id_are_normalised_and_reported(ctx):
    result = explain_kpi(ctx, " nv04 ", "aso_focus")
    assert result["params_used"]["employee_id"] == "NV04" and result["params_used"]["kpi"] == "ASO_Focus"
    assert result["cong_van_section"] == "2.5" and result["amount"] == 0


def test_other_period_is_refused_not_replaced(ctx):
    # Hỏi tháng 11 ⇒ từ chối, không trả số tháng 10 thay thế (tránh agent nói nhầm kỳ)
    for name, fn in TOOLS.items():
        args = {"employee_id": "NV04"} if name in ("get_employee_result",) else {}
        if name == "explain_kpi":
            args = {"employee_id": "NV04", "kpi": "PC_BT"}
        assert set(fn(ctx, **args, period="2026-11")) == {"refused", "params_used"}, name


def test_no_real_names_or_codes_in_any_tool_output(ctx):
    employees = load_targets(TARGETS)["employees"]
    secrets = {str(code) for code in employees} | {str(info["name"]) for info in employees.values()}
    outputs = [get_team_bonus_table(ctx), get_mid_month_gap(ctx)]
    for i in range(1, 15):
        outputs += [get_employee_result(ctx, f"NV{i:02d}"), explain_kpi(ctx, f"NV{i:02d}", "LPPC")]
    text = json.dumps(outputs, ensure_ascii=False)
    leaked = [s for s in secrets if s and s in text]
    assert not leaked, f"lộ {len(leaked)} tên / mã thật"


def test_data_flags_always_present(ctx):
    flags = get_team_bonus_table(ctx)["data_flags"]
    assert any("CHƯA phải số chốt" in f for f in flags)
    assert sum("chưa kiểm được" in f for f in flags) == 3
    # 10/11 người vận hành "giả định chờ sếp" ("không cần chờ sếp nữa") ⇒ cờ này KHÔNG còn trong kết quả công cụ
    assert not any("giả định chờ sếp" in f for f in flags)


def test_mid_month_gap_has_money_if_reached(ctx):
    gap = get_mid_month_gap(ctx, "NV04")
    assert gap["working_days_left_after_cutoff"] > 0
    focus = next(g for g in gap["employees"][0]["gaps"] if g["kpi"] == "ASO_Focus")
    assert focus["next_tier"] == "Đạt 90%" and focus["amount_if_reached"] == 800_000


def test_faithful_answer_passes_number_check(ctx):
    # Câu trả lời chép đúng số công cụ trả ⇒ qua; đổi một số ⇒ bị bắt (kiểm phép kiểm trên kết quả công cụ thật)
    result = explain_kpi(ctx, "NV04", "ASO_Focus")
    good = (f"NV04 ASO_Focus đạt {result['pct']}% ({result['actual']:.0f}/{result['target']:.0f}), chưa tới mốc 90% "
            f"nên được {result['amount']} đ; nếu đạt 90% được 800.000 đ (công văn mục {result['cong_van_section']}).")
    assert check_numbers(good, [result])["ok"], check_numbers(good, [result])
    assert not check_numbers(good.replace("800.000", "850.000"), [result])["ok"]


def test_refusal_message_does_not_echo_long_input(ctx):
    # B minor (c): lời từ chối chép nguyên tham số 5.000 ký tự ⇒ cắt ngắn
    assert len(get_employee_result(ctx, "A" * 5000)["refused"]) < 200


def test_numbers_are_display_rounded(ctx):
    # G4b 02/11 tối: công cụ trả số thực thô ⇒ mô hình chép "thiếu 812888711.36" — mọi số ≤ 2 chữ số lẻ, Turnover theo đồng
    def floats(value):
        if isinstance(value, float):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from floats(item)
        elif isinstance(value, list):
            for item in value:
                yield from floats(item)
    outputs = [get_mid_month_gap(ctx)] + [get_employee_result(ctx, f"NV{i:02d}") for i in range(1, 15)]
    assert all(round(x, 2) == x for out in outputs for x in floats(out))
    turnover = next(k for k in get_employee_result(ctx, "NV01")["kpis"] if k["kpi"] == "Turnover")
    assert isinstance(turnover["actual"], int)
