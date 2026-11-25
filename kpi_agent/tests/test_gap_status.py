"""`backend/gap_status.py` — chỉ tiêu đã ở mốc cao nhất + kỳ hết ngày trong kết quả `get_mid_month_gap`.

Giữ sàn: lỗi User gặp thật 12/11 ("NV09 còn thiếu bao nhiêu PC_BT?" ⇒ trợ lý nói "không ghi nhận khoảng thiếu" dù NV09 đã ở mốc cao nhất);
và phần bổ sung KHÔNG được chạm dấu vân tay bản tính / tổng 126.660.000 đ.
Backend chạy thật (TestClient, đường RAW, ngày chốt 31/10, dữ liệu thật trên máy, KPI_STORE tạm). 0 lần gọi AI. Ca ghi trước khi code:
12/11 mục (b) điểm 6."""
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kpi_agent.backend import gap_status
from kpi_agent.core.compute_bonus import BonusLine

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx",
          ROOT / "kpi_agent/data/ma_che_ten.json", ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
KPIS = {"Turnover", "PC_BT", "LPPC", "ASO_ALL", "PC_ON", "LPPC_Meat", "ASO_Focus", "ASO_Focus_2"}


@pytest.fixture(scope="module")
def raw_client(tmp_path_factory):
    """Kỳ ĐÃ hết ngày: RAW tháng 10, chốt 31/10."""
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(tmp_path_factory.mktemp("store") / "kpi_store.sqlite"))
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
            yield client


def _both_doors(client, employee_id: str | None) -> list[dict]:
    """Hai cửa ra cùng phải có trường mới: đường trang / chat của mình và cửa công cụ Capitol gọi."""
    args = {"employee_id": employee_id} if employee_id else {}
    return [client.get("/api/gap", params=args).json(), client.post("/api/tool/get_mid_month_gap", json={"args": args}).json()]


def test_nv09_pc_bt_is_reported_at_top_tier_with_its_money(raw_client):
    paid = {row["kpi"]: row["amount"] for row in raw_client.get("/api/employees/NV09").json()["kpis"]}
    for body in _both_doors(raw_client, "NV09"):
        (employee,) = body["employees"]
        top = {row["kpi"]: row for row in employee["at_top_tier"]}
        assert "PC_BT" in top and "PC_BT" not in {gap["kpi"] for gap in employee["gaps"]}
        assert top["PC_BT"]["amount_now"] == paid["PC_BT"] == 2_200_000
        assert top["PC_BT"]["status"] == gap_status.TOP_TIER_STATUS and top["PC_BT"]["reached_tier"]


def test_every_kpi_is_either_a_gap_or_at_top_tier_for_the_whole_team(raw_client):
    for body in _both_doors(raw_client, None):
        assert len(body["employees"]) == 14
        for employee in body["employees"]:
            gaps = [gap["kpi"] for gap in employee["gaps"]]
            top = [row["kpi"] for row in employee["at_top_tier"]]
            assert not set(gaps) & set(top) and set(gaps) | set(top) == KPIS, employee["employee_id"]


def test_closed_period_is_flagged_without_any_number(raw_client):
    for body in _both_doors(raw_client, "NV09"):
        assert body["working_days_left_after_cutoff"] == 0 and body["period_closed"] is True
        assert body["period_note"] == gap_status.PERIOD_CLOSED_NOTE and not re.search(r"\d", body["period_note"])


def test_open_period_has_no_closed_flag_and_unpaid_kpi_is_not_top_tier():
    # Dòng nhỏ tự dựng (hàm thuần, không cần backend): kỳ còn ngày ⇒ không gắn cờ; chỉ tiêu chưa đạt mốc nào và không có mốc kế ⇒ không coi là "mốc cao nhất"
    lines = [BonusLine("PC_BT", "Đạt 100%", 2_200_000, "", None, None), BonusLine("LPPC", "Mức 1", 500_000, "", "Mức 2", 0.3),
             BonusLine("PC_ON", None, 0, "", None, None)]
    ctx = SimpleNamespace(days_left=5, to_real={"NV01": "SM1"}, bonuses={"SM1": {"lines": lines}})
    body = gap_status.add_gap_status(ctx, {"employees": [{"employee_id": "NV01", "gaps": []}]})
    assert "period_closed" not in body and "period_note" not in body
    assert body["employees"][0]["at_top_tier"] == [
        {"kpi": "PC_BT", "reached_tier": "Đạt 100%", "amount_now": 2_200_000, "status": gap_status.TOP_TIER_STATUS}]


def test_unknown_employee_is_refused_as_before(raw_client):
    for body in _both_doors(raw_client, "NV99"):
        assert "refused" in body and "employees" not in body and "period_closed" not in body


def test_bonus_run_fingerprint_and_team_total_untouched(raw_client):
    from kpi_agent.backend import main
    assert not any(path.name == "gap_status.py" for path in main.CODE_FILES)
    assert raw_client.get("/api/team-bonus").json()["team_total"] == 152_360_000
