"""Công cụ 11 `get_extra_bonus` — Thưởng thêm 6 khoản ngoài 8 KPI cho trợ lý (bậc (b) 11/11,
C phản biện 10:33).

Phép ở đây chỉ kiểm DÂY NỐI (công cụ dùng cùng hàm `main._extra_bonus` với đường hệ thống ⇒ so hai đường là vòng tròn, không chứng minh số đúng);
số ĐÚNG = so 84 ô (+ 42 ô Meat 7.x từ 11/11) qua công cụ với đáp án độc lập của mình (ngoài test này).
Backend chạy thật (TestClient, workflow đường RAW, ngày chốt 31/10, dữ liệu thật trên máy, KPI_STORE tạm). 0 lần gọi AI. Ca xấu ghi trước khi code."""
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json",
          ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"   # khoá giả chỉ sống trong test
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
TEAM = [f"NV{i:02d}" for i in range(1, 15)]


@pytest.fixture(scope="module")
def raw_client(tmp_path_factory):
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(tmp_path_factory.mktemp("store") / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
            yield client


def _tool(client, args: dict) -> dict:
    return client.post("/api/tool/get_extra_bonus", json={"args": args}).json()


def _money(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _kiem(client, answer: str, question: str = "") -> dict:
    # Không gửi tool_calls ⇒ nguồn số = `_team_sources` (đúng cách Rào chắn Capitol gọi — chỉ có chữ)
    return client.post("/api/system/kiem-so", json={"answer": answer, "question": question}, headers=KEY).json()


def test_1_team_tool_equals_system_route_84_cells(raw_client):
    # DÂY NỐI: 84 ô công cụ (Capitol) = 84 ô đường hệ thống; tổng từng NV / cả đội; tổng 8 KPI + thưởng thêm = bảng 8 KPI + thưởng thêm
    system = raw_client.get("/api/system/extra-bonus", headers=KEY).json()
    team = _tool(raw_client, {})
    # 13/11: khoá `gate_progress` (khoản điều kiện "07" cho kiểu xem của trang) không ra đường hệ thống, không vào kết quả công cụ
    assert "gate_progress" not in system and "gate_progress" not in team
    assert len(team["lines"]) == 9 * 14   # 11/11: + Meat T071–T073
    assert [(l["employee_id"], l["item"], l["amount"]) for l in team["lines"]] == \
        [(l["employee_id"], l["item"], l["amount"]) for l in system["lines"]]
    assert {row["employee_id"]: row["extra_bonus_total"] for row in team["employees"]} == system["employee_total"]
    assert team["extra_bonus_team_total"] == system["team_total"] == 17_784_000 + 1_242_300   # + Meat 7.x (B, cổng P1; C so đáp án độc lập)      # 11/11 luật chữ sổ T03–T05: T04 530.000 ⇒ 510.000 (trước: 17.644.000, gộp 88a3b6f)
    table = raw_client.post("/api/tool/get_team_bonus_table", json={"args": {}}).json()
    salary = {row["employee_id"]: row["business_salary"] for row in table["employees"]}
    assert team["team_total_8_kpi"] == table["team_total"] == 152_360_000
    assert team["team_total_8_kpi_and_extra"] == 152_360_000 + 17_784_000 + 1_242_300
    for row in team["employees"]:
        assert row["business_salary_8_kpi"] == salary[row["employee_id"]]
        assert row["total_8_kpi_and_extra"] == salary[row["employee_id"]] + row["extra_bonus_total"]
    # Không có tổng từng NV dạng {mã: số} ở cấp đội (rào gán số cấp đội cho MỌI NV)
    assert "employee_total" not in team


def test_2_one_employee_and_limit_next_to_items_03_04(raw_client):
    system = raw_client.get("/api/system/extra-bonus", headers=KEY).json()
    one = _tool(raw_client, {"employee_id": " nv05"})
    assert one["employee_id"] == "NV05" and [l["item"] for l in one["lines"]] == ["T01", "T02", "T03", "T04", "T05", "T06", "T071", "T072", "T073"]
    assert one["extra_bonus_total"] == system["employee_total"]["NV05"]
    salary = raw_client.post("/api/tool/get_employee_result", json={"args": {"employee_id": "NV05"}}).json()["business_salary"]
    assert one["business_salary_8_kpi"] == salary and one["total_8_kpi_and_extra"] == salary + one["extra_bonus_total"]
    assert {l["item"] for l in one["lines"] if "gioi_han" in l} == {"T03"}         # giới hạn ngay trên dòng khoản
    assert all(not any(ch.isdigit() for ch in l["gioi_han"]) for l in one["lines"] if "gioi_han" in l)   # chữ giới hạn không thành nguồn số
    # Chat (GET qua backend_client) và Capitol (POST /api/tool) ra cùng kết quả
    assert raw_client.get("/api/extra-bonus", params={"employee_id": "NV05"}).json() == one


@pytest.mark.parametrize("args, word", [({"employee_id": "NV99"}, "không có"), ({"employee_id": "NV01/../NV02"}, None),
                                        ({"period": "2026-10"}, "không nhận period"), ({"extra": 1}, "tham số không hợp lệ")])
def test_3_bad_args_refused_without_numbers(raw_client, args, word):
    refused = _tool(raw_client, args)
    assert "refused" in refused and "lines" not in refused
    if word:
        assert word in refused["refused"]
    if args.get("employee_id") == "NV01/../NV02":
        # hợp đồng có sẵn (`queries._refused`, bậc Q1): chỉ nhắc lại mã ĐÚNG DẠNG NVxx (như NV99); chuỗi sai dạng (có thể là mã thật) ⇒ None
        assert refused["params_used"]["employee_id"] is None


def test_3b_other_period_refused_on_chat_route(raw_client):
    refused = raw_client.get("/api/extra-bonus", params={"period": "2026-11"}).json()
    assert "refused" in refused and "2026-10" in refused["refused"] and "lines" not in refused


def test_4_no_real_identity_or_outlet_code_in_any_result(raw_client):
    # Kênh chữ mới tới AI + Capitol (how / title / gioi_han): 0 mã / tên NV thật, 0 mã cửa hàng trong kết quả cả đội + 14 NV
    from kpi_agent.backend import main
    from kpi_agent.core.load_raw import period_rules, read_raw_lines
    from kpi_agent.core.rules import load_rules
    results = [_tool(raw_client, {})] + [_tool(raw_client, {"employee_id": nv}) for nv in TEAM]
    text = json.dumps(results, ensure_ascii=False).lower()
    identities = main._real_identity_tokens(raw_client.app)
    assert identities and not [t for t in identities if t in text]
    lines, _late = read_raw_lines(ROOT / "sample_data/thang_10/RAW DATA.xlsx", date(2026, 10, 31), "2026-10",   # bậc 3a: truyền luật kỳ
                                  period_rules=period_rules(load_rules(ROOT / "kpi_agent/config/rules_2026_10_raw.yaml")["assumptions"]))
    outlets = {str(line["OUTLET CODE"]).strip().lower() for line in lines if str(line.get("OUTLET CODE") or "").strip()}
    assert len(outlets) > 100 and not [o for o in outlets if f'"{o}"' in text or f" {o} " in text]


def test_5_kiem_so_without_tool_calls_knows_extra_bonus(raw_client):
    # Ca thật: câu đúng về thưởng thêm qua Rào chắn Capitol (không tool_calls) ⇒ ĐẠT — trước bậc này: số không có trong `_team_sources` ⇒ chặn nhầm
    one = _tool(raw_client, {"employee_id": "NV05"})
    answer = (f"NV05 được thưởng thêm {_money(one['extra_bonus_total'])} đồng, ngoài 8 KPI. "
              f"Tính cả 8 KPI thì tổng là {_money(one['total_8_kpi_and_extra'])} đồng.")
    passed = _kiem(raw_client, answer, "NV05 được thưởng thêm bao nhiêu, tổng cả hai?")
    assert passed["dat"] is True, passed["loi"]


def test_5b_item_number_in_answer_is_not_blocked(raw_client):
    # Số khoản ("Thưởng 03", "khoản 04") không bị coi là số bịa / số của người khác (giống chuyện "mục 6.2")
    one = _tool(raw_client, {"employee_id": "NV05"})
    by_item = {l["item"]: l["amount"] for l in one["lines"]}
    answer = (f"NV05: Thưởng 03 (ASO nước mắm cao cấp) được {_money(by_item['T03'])} đồng; "
              f"khoản 04 (ASO xốt Chinsu) được {_money(by_item['T04'])} đồng.")
    passed = _kiem(raw_client, answer, "NV05 thưởng thêm khoản 03 và 04 bao nhiêu?")
    assert passed["dat"] is True, passed["loi"]


def test_6_bad_totals_blocked(raw_client):
    # Ca xấu: (i) tổng sai KHÔNG trùng số nào của đội ⇒ chặn; (ii) số thưởng thêm của NV khác gán cho NV05 ⇒ chặn
    team = _tool(raw_client, {})
    own = _tool(raw_client, {"employee_id": "NV05"})
    wrong = own["total_8_kpi_and_extra"] + 1_234
    assert f"{wrong}" not in json.dumps(team) and f"{wrong}" not in json.dumps(own)
    invented = _kiem(raw_client, f"NV05 tổng 8 KPI và thưởng thêm là {_money(wrong)} đồng.")
    assert invented["dat"] is False and "so_khong_nguon" in {item["loai"] for item in invented["loi"]}
    own_numbers = json.dumps(own)
    other = next(row for row in team["employees"] if row["employee_id"] != "NV05" and row["extra_bonus_total"] > 0
                 and str(row["extra_bonus_total"]) not in own_numbers)
    swapped = _kiem(raw_client, f"NV05 được thưởng thêm {_money(other['extra_bonus_total'])} đồng.")
    assert swapped["dat"] is False and "so_sai_nguoi" in {item["loai"] for item in swapped["loi"]}


def test_8_block_tools_exclude_extra_bonus():
    # Khối agent của dây chuyền tự viết không có công cụ này (cần đọc lại file RAW qua backend) — bộ kiểm file chặn ngay khi nối
    from kpi_agent.workflow.blocks import BLOCK_TOOLS
    assert "get_extra_bonus" not in BLOCK_TOOLS and "get_team_bonus_table" in BLOCK_TOOLS


# Bản 2: tính sẵn lúc nạp (lần gọi đầu < 10 s — hạn mỗi công cụ của Capitol) + tổng khoản chỉ 1 người có tiền ----------
def test_8b_capitol_schema_has_no_system_field(raw_client):
    # Khối Agent Capitol TỪ CHỐI dựng cả khối nếu schema công cụ có ô hệ thống (C đọc code: ``
    # `_KPI_O_HE_THONG`, đã chuyển 11/11) — kỳ / bản dữ liệu do backend tự giữ. Kiểm đủ 7 tên ô, MỌI công cụ trong /api/tools (test_capitol_api chỉ kiểm `period`)
    system = {"period", "ky", "run_id", "org_id", "orgid", "organization_id", "data_version"}
    tools = raw_client.get("/api/tools").json()["tools"]
    assert "get_extra_bonus" in {tool["name"] for tool in tools}
    for tool in tools:
        assert not set(tool["parameters"].get("properties", {})) & system, tool["name"]
        assert not set(tool["parameters"].get("required", [])) & system, tool["name"]
    extra = next(tool for tool in tools if tool["name"] == "get_extra_bonus")
    assert set(extra["parameters"]["properties"]) == {"employee_id"} and extra["parameters"]["required"] == []


def test_10_single_payer_item_total_belongs_to_that_employee(raw_client):
    # Lỗ trên bản 1 (dữ liệu thật): tổng khoản cả đội khi CHỈ một người có tiền = tiền người đó ⇒ là số "cấp đội" ⇒ "NV05 được thưởng thêm <số đó>" qua.
    # Bản 2: tổng khoản đó gắn employee_id người duy nhất ⇒ NV05 + số đó CHẶN, đúng người QUA. Khoản lấy theo dữ liệu (không gõ tay mã / số)
    team = _tool(raw_client, {})
    single = [item for item in team["items"] if item["paid_count"] == 1 and item["paid_employee_ids"] != ["NV05"]]
    assert single, "dữ liệu tháng 10 có khoản chỉ một người có tiền (01, 02)"
    for item in single:
        owner = item["paid_employee_ids"][0]
        assert item["employee_id"] == owner
        nv05 = json.dumps(_tool(raw_client, {"employee_id": "NV05"}))
        if str(item["team_total"]) in nv05:
            continue   # trùng số của chính NV05 ⇒ ca không dùng được
        wrong = _kiem(raw_client, f"NV05 được thưởng thêm {_money(item['team_total'])} đồng.")
        assert wrong["dat"] is False and "so_sai_nguoi" in {i["loai"] for i in wrong["loi"]}, (item["item"], wrong)
        right = _kiem(raw_client, f"{owner} được thưởng thêm {_money(item['team_total'])} đồng ở {item['item'].replace('T', 'khoản ')}.")
        assert right["dat"] is True, (item["item"], right["loi"])
    shared = [item for item in team["items"] if item["paid_count"] >= 2]
    assert shared and all("employee_id" not in item for item in shared)


def test_9_extra_bonus_ready_right_after_startup(tmp_path, monkeypatch):
    # Ca thật đo bản 1: lần gọi đầu 14,8 s / 13,6 s > 10 s ⇒ bản 2 tính lúc bật: trạng thái đã có số TRƯỚC lần gọi công cụ nào (backend bật mới)
    import time
    from kpi_agent.backend import main
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))
    monkeypatch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 31))
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        assert isinstance(client.app.state.extra_bonus, dict) and client.app.state.extra_bonus["team_total"] == 17_784_000 + 1_242_300
        started = time.perf_counter()
        assert "lines" in _tool(client, {"employee_id": "NV01"})
        assert time.perf_counter() - started < 2


# Cuối file: bật lại CÙNG app với đường Tracking ⇒ app.state của fixture module đổi — ca nào dùng raw_client phải đứng TRƯỚC
def test_99_tracking_workflow_refuses_clearly(tmp_path, monkeypatch):
    # Đường sổ Tracking (không có dòng đơn từng mã hàng) ⇒ lời từ chối rõ, 200 trong thân (như công cụ khác), không 500; Rào chắn vẫn chạy
    from kpi_agent.backend import main
    from kpi_agent.workflow import runner
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    monkeypatch.setattr(main, "WORKFLOW", runner.DEFAULT_WORKFLOW)
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 20))
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        response = client.post("/api/tool/get_extra_bonus", json={"args": {"employee_id": "NV05"}})
        assert response.status_code == 200 and "đường RAW" in response.json()["refused"]
        assert client.post("/api/system/kiem-so", json={"answer": "NV05 chưa có số."}, headers=KEY).status_code == 200
