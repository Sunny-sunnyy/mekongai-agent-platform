"""Công cụ 12 `get_spice_loyalty` — 1 % gia vị cho trợ lý (`backend/spice_tool.py`).

Sàn: tiền 1 % gia vị trả cho KHÁCH — câu trả lời không được gán nó cho nhân viên. Giữ bằng HÌNH kết quả (không dòng nào có khoá `employee_id`) + câu
chuẩn do code viết (rào che câu chép nguyên). Ba ca đầu dùng số tự đặt; ca cuối bật backend RAW 31/10 thật (kho tạm, không mở cổng)."""
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kpi_agent.agent import kiem_so
from kpi_agent.agent.backend_client import ALL_TOOL_PARAMS
from kpi_agent.agent.number_check import check_numbers
from kpi_agent.agent.owner_check import check_owners
from kpi_agent.agent.tool_specs import tool_schemas
from kpi_agent.backend import spice_tool

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx",
          ROOT / "kpi_agent/data/ma_che_ten.json", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
CTX = SimpleNamespace(period="2026-10", cutoff=date(2026, 10, 31), data_flags_for_model=[], rule_version="r@12345678", status="tam_tinh",
                      status_label="tạm tính", to_real={"NV04": "SM04", "NV05": "SM05"})
SPICE = {"employees": [{"employee_id": "NV04", "customers": 30, "customers_reached": 9, "sales": 61_000_000, "reward": 610_000},
                       {"employee_id": "NV05", "customers": 40, "customers_reached": 12, "sales": 81_200_000, "reward": 812_000}],
         "team_customers": 70, "team_customers_reached": 21, "team_reward": 1_422_000}


def _has_key(value, key: str) -> bool:
    if isinstance(value, dict):
        return any(name == key or _has_key(item, key) for name, item in value.items() if name != "params_used")
    return isinstance(value, list) and any(_has_key(item, key) for item in value)


def test_result_shape_never_puts_customer_money_under_an_employee_id():
    team, one = spice_tool.get_spice_loyalty(CTX, SPICE), spice_tool.get_spice_loyalty(CTX, SPICE, employee_id=" nv05 ")
    assert not _has_key(team, "employee_id") and not _has_key(one, "employee_id")          # rào: số dưới employee_id = số của nhân viên đó
    assert [row["salesperson_in_charge"] for row in team["by_salesperson_in_charge"]] == ["NV04", "NV05"]
    assert team["team"]["reward_paid_to_customers"] == 1_422_000 == sum(row["reward_paid_to_customers"] for row in team["by_salesperson_in_charge"])
    assert [row["reward_paid_to_customers"] for row in one["by_salesperson_in_charge"]] == [812_000] and "team" not in one
    assert all("KHÔNG phải nhân viên" in row["recipient"] for row in team["by_salesperson_in_charge"]) and "KHÁCH HÀNG" in team["note"]
    assert one["customer_reward_sentences"] == ["Khách hàng do NV05 phụ trách: 12 / 40 khách đạt mức; "
                                                "tiền thưởng 1 % gia vị trả cho KHÁCH là 812.000 đ (không phải tiền của nhân viên)."]


def test_bad_arguments_and_unavailable_source_are_refused():
    assert "refused" in spice_tool.get_spice_loyalty(CTX, SPICE, employee_id="NV99")
    assert "refused" in spice_tool.get_spice_loyalty(CTX, SPICE, employee_id="../NV05")
    assert "refused" in spice_tool.get_spice_loyalty(CTX, SPICE, period="2026-09")
    unavailable = spice_tool.get_spice_loyalty(CTX, {"refused": "1 % gia vị chỉ có ở workflow đường RAW DATA (cần dòng đơn từng nhóm hàng)"})
    assert unavailable["refused"].startswith("1 % gia vị chỉ có ở workflow đường RAW DATA") and "by_salesperson_in_charge" not in unavailable


@pytest.mark.parametrize("answer, passes", [
    ("NV05 được thưởng 1 % gia vị 812.000 đ.", False),                                    # sai NGHĨA: tiền của khách gán cho nhân viên
    ("NV05 nhận 812.000 đ tiền gia vị.", False),
    ("Khách hàng do NV04 phụ trách được trả 812.000 đ.", False),                          # sai NGƯỜI phụ trách
    ("Khách hàng do NV05 phụ trách: 12 / 40 khách đạt mức; tiền thưởng 1 % gia vị trả cho KHÁCH là 812.000 đ (không phải tiền của nhân viên).", True),
    ("Khách hàng do NV05 phụ trách: 12 / 40 khách đạt mức; tiền thưởng 1 % gia vị trả cho KHÁCH là 900.000 đ (không phải tiền của nhân viên).", False),
    ("Cả đội: tiền 1 % gia vị trả cho khách là 1.422.000 đ, 21 / 70 khách đạt.", True),
    # CHẶN OAN đã biết (ghi ở báo cáo): câu ĐÚNG tự viết, không chép câu chuẩn — sàn "tiền gán sai người" được ưu tiên hơn
    ("Khách của NV05 được trả 812.000 đ tiền thưởng 1 % gia vị; đây không phải tiền của nhân viên.", False),
])
def test_guard_blocks_customer_money_said_as_employee_money(answer, passes):
    results = [spice_tool.get_spice_loyalty(CTX, SPICE)]
    ok = check_owners(answer, results, question="1 % gia vị?")["ok"] and check_numbers(answer, results, question="1 % gia vị?")["ok"]
    assert ok is passes


def test_tool_is_wired_for_the_model_and_guard_version_moved():
    schemas = {schema["function"]["name"]: schema["function"] for schema in tool_schemas(["Turnover"])}
    assert len(schemas) == 17 and "KHÁCH HÀNG" in schemas["get_spice_loyalty"]["description"]
    assert "get_spice_loyalty" in schemas["get_extra_bonus"]["description"]                 # chỉ đường: "gia vị" không rơi vào 9 khoản Thưởng thêm
    assert ALL_TOOL_PARAMS["get_spice_loyalty"] == {"employee_id", "period"}
    assert kiem_so.VERSION == "kiem_so@31"


@pytest.mark.skipif(not all(path.exists() for path in NEEDED), reason="không có dữ liệu thật trên máy này")
def test_real_backend_serves_the_tool_with_the_same_money_as_the_page(tmp_path):
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        mp.setenv("KPI_SYS_KEY", "khoa-thu-trong-test-0123456789")   # khoá giả chỉ sống trong test
        key = {"X-Kpi-Sys-Key": "khoa-thu-trong-test-0123456789"}
        mp.setenv("KPI_WARM_SPICE", "on")   # như script bật bộ của người dùng đặt; mặc định tắt (mọi test khác bật backend không chậm thêm)
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
            # 13/11: 1 % gia vị tính SẴN lúc bật — tính ở lần gọi đầu mất ≈ 15–35 s > hạn chờ 10 s của trợ lý ⇒ câu gia vị đầu tiên sau khi bật hết giờ
            computed_at_startup = client.app.state.spice is not None and "team_reward" in client.app.state.spice
            # Rào chắn Capitol (chỉ có chữ, không tool_calls ⇒ nguồn = `_team_sources`): câu chuẩn chép nguyên QUA; đổi 1 chữ số tiền ⇒ CHẶN
            sentence = client.post("/api/tool/get_spice_loyalty", json={"args": {"employee_id": "NV05"}}).json()["customer_reward_sentences"][0]
            question = "Tiền 1 % gia vị của khách do NV05 phụ trách?"
            capitol_ok = client.post("/api/system/kiem-so", json={"answer": sentence, "question": question}, headers=key).json()
            forged = sentence.replace(" đ (không", "1 đ (không")
            capitol_bad = client.post("/api/system/kiem-so", json={"answer": forged, "question": question}, headers=key).json()
            page = client.get("/api/spice-loyalty").json()
            team = client.post("/api/tool/get_spice_loyalty", json={"args": {}}).json()
            one = client.get("/api/spice-tool", params={"employee_id": "NV05"}).json()
            bad = client.post("/api/tool/get_spice_loyalty", json={"args": {"employee_id": "NV99"}}).json()
            extra_arg = client.post("/api/tool/get_spice_loyalty", json={"args": {"kpi": "Turnover"}}).json()
    assert team["team"]["reward_paid_to_customers"] == page["team_reward"] == 11_868_854
    assert sum(row["reward_paid_to_customers"] for row in team["by_salesperson_in_charge"]) == 11_868_854 and len(team["by_salesperson_in_charge"]) == 14
    page_nv05 = next(row for row in page["employees"] if row["employee_id"] == "NV05")
    assert one["by_salesperson_in_charge"] == [row for row in team["by_salesperson_in_charge"] if row["salesperson_in_charge"] == "NV05"]
    assert one["by_salesperson_in_charge"][0]["reward_paid_to_customers"] == page_nv05["reward"]
    assert not _has_key(team, "employee_id") and "refused" in bad and "refused" in extra_arg
    assert "employees" in page and "by_salesperson_in_charge" not in page                    # đường của trang giữ nguyên hình
    assert computed_at_startup
    assert capitol_ok["dat"] is True, capitol_ok["loi"]
    assert forged != sentence and capitol_bad["dat"] is False
