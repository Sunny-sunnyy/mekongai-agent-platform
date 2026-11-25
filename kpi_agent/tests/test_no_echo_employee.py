"""Bậc Q1 (04/11 — T2 minor, đã chọn (a)): lời từ chối của 3 công cụ cũ KHÔNG nhắc lại nguyên văn mã nhân viên SAI người gọi gửi.

Vì sao: chữ gửi sai có thể là mã NV THẬT (quy ước S2 "lời lỗi không chứa mã thật"). Mã đúng dạng NVxx (vd NV99 "không có trong đội") vẫn được nói lại —
dạng đó không bao giờ là mã thật. Chuẩn hoá giữ nguyên: " nv04 " ⇒ NV04 (sau khi thấy T2)."""
import json
from datetime import date
from pathlib import Path

import pytest

from kpi_agent.workflow import runner

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")


@pytest.fixture(scope="module")
def ctx():
    from kpi_agent.backend.queries import build_context
    return build_context(date(2026, 10, 20), mask_path=str(NEEDED[2]))


def _calls(ctx, employee_id):
    from kpi_agent.backend.queries import explain_kpi, get_employee_result, get_mid_month_gap
    return {"get_employee_result": get_employee_result(ctx, employee_id), "get_mid_month_gap": get_mid_month_gap(ctx, employee_id),
            "explain_kpi": explain_kpi(ctx, employee_id, "PC_BT")}


def test_real_or_malformed_code_never_echoed(ctx):
    for sent in [*list(runner.load_mask())[:3], "NV01/../NV02", "%2F..", "x" * 50]:
        for name, body in _calls(ctx, sent).items():
            assert set(body) == {"refused", "params_used"} and body["params_used"]["employee_id"] is None, (name, sent)
            text = json.dumps(body, ensure_ascii=False)
            assert sent not in text and not runner.REAL_CODE_PATTERN.search(text), (name, sent)


def test_well_formed_unknown_code_still_named(ctx):
    for name, body in _calls(ctx, "NV99").items():
        assert "NV99" in body["refused"] and body["params_used"]["employee_id"] == "NV99", name


def test_spaces_and_case_still_normalised(ctx):
    for name, body in _calls(ctx, " nv04 ").items():
        assert "refused" not in body and body["params_used"]["employee_id"] == "NV04", name
