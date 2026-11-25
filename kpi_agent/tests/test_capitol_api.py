"""API cho bộ Capitol (10/11 — KPI agent chạy trên Capitol, công cụ gắn thẳng gọi HTTP backend này).

- `/api/tool/{name}` phải trả ĐÚNG kết quả mô hình của agent mình nhận qua `backend_client.call_tool` (đường đang dùng) — cùng tham số, kể cả sai;
- kỳ do hệ thống điền: mô tả `/api/tools` không có ô `period`, gửi kèm `period` ⇒ từ chối;
- đường hệ thống có khoá: chưa đặt ⇒ 503, thiếu / sai ⇒ 401, đúng ⇒ bảng thưởng + sha256 tính lại được; 0 mã NV thật.
Backend chạy thật (TestClient, dữ liệu thật tháng 10, KPI_STORE tạm); đường HTTP của agent chuyển sang cùng TestClient như test_workflow_agent_block.py."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kpi_agent.agent import backend_client
from kpi_agent.agent.backend_client import ALL_TOOL_PARAMS, call_tool, load_context
from kpi_agent.workflow import runner

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
BASE = "http://127.0.0.1:8110"
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"   # khoá giả chỉ sống trong test — không phải khoá thật


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))
    monkeypatch.delenv("KPI_SYS_KEY", raising=False)
    from kpi_agent.backend.main import app
    with TestClient(app, base_url=BASE) as test_client:
        def route(url, params=None, timeout=None, headers=None):   # headers: agent gửi kèm mã phiên (12/11) — bộ test tắt mã nên bỏ qua
            assert url.startswith(BASE)
            return test_client.get(url[len(BASE):], params=params or {})
        monkeypatch.setattr(backend_client.httpx, "get", route)   # đường agent mình (GET) đi vào CÙNG backend thật
        yield test_client


# Mọi công cụ × tham số đúng / sai — KHÔNG có `period` (Capitol không có ô này)
CALLS = [("get_team_bonus_table", {}), ("get_employee_result", {"employee_id": "NV05"}), ("get_employee_result", {"employee_id": "nv14 "}),
         ("get_employee_result", {"employee_id": "NV99"}), ("get_employee_result", {"employee_id": "NV01/../NV02"}), ("get_employee_result", {}),
         ("get_employee_result", {"employee_id": "NV05", "foo": 1}), ("get_mid_month_gap", {}), ("get_mid_month_gap", {"employee_id": "NV05"}),
         ("explain_kpi", {"employee_id": "NV05", "kpi": "ASO_ALL"}), ("explain_kpi", {"employee_id": "NV05", "kpi": "../PC_BT"}),
         ("get_opportunities", {}), ("get_data_provenance", {}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "extra": 10}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "target_pct": 100}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "extra": 10, "target_pct": 0}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "extra": 10, "target_pct": 100}),
         ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "target_pct": 301}),
         ("get_team_kpi_summary", {"kpi": "PC_ON"}), ("get_team_kpi_summary", {}), ("get_team_kpi_summary", {"kpi": "XYZ"}),
         ("compare_employees", {"employee_a": "NV05", "employee_b": "NV14"}), ("compare_employees", {"employee_a": "NV05"}),
         ("get_data_issues", {}), ("get_data_issues", {"employee_id": "NV14"})]


def test_tool_list_has_every_tool_and_no_period(client):
    body = client.get("/api/tools").json()
    assert [tool["name"] for tool in body["tools"]] == list(ALL_TOOL_PARAMS)
    assert all("period" not in tool["parameters"]["properties"] and "period" not in tool["parameters"]["required"] for tool in body["tools"])
    assert body["period"] == "2026-10"


def test_tool_call_equals_agent_http_path(client):
    agent_ctx = load_context(BASE)
    for name, args in CALLS:
        via_api = client.post(f"/api/tool/{name}", json={"args": dict(args)})
        assert via_api.status_code == 200, (name, args)
        assert via_api.json() == call_tool(agent_ctx, name, dict(args)), (name, args)


@pytest.mark.parametrize("name, args, words", [
    ("get_team_bonus_table", {"period": "2026-10"}, "kỳ do hệ thống điền"),   # kể cả đúng kỳ đang có — AI không điền kỳ
    ("get_employee_result", {"employee_id": "NV05", "period": "2026-09"}, "kỳ do hệ thống điền"),
    ("delete_everything", {}, "không có công cụ"),
])
def test_tool_call_refusals(client, name, args, words):
    body = client.post(f"/api/tool/{name}", json={"args": args}).json()
    assert words in body["refused"]


@pytest.mark.parametrize("payload, status", [({"args": [1]}, 422), ({"args": {}, "x": 1}, 422), ("chữ", 422)])
def test_tool_call_bad_body(client, payload, status):
    assert client.post("/api/tool/get_team_bonus_table", json=payload).status_code == status
    assert client.post("/api/tool/get_team_bonus_table", content=b"{}", headers={"content-type": "text/plain"}).status_code == 415


def test_system_route_needs_key(client, monkeypatch):
    assert client.get("/api/system/bonus-table").status_code == 503          # chưa đặt khoá ⇒ không chạy đường hệ thống
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    assert client.get("/api/system/bonus-table").status_code == 401
    assert client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": SYS_KEY + "x"}).status_code == 401
    monkeypatch.setenv("KPI_SYS_KEY", "ngan")
    assert client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": "ngan"}).status_code == 503   # khoá < 16 ký tự ⇒ coi như chưa đặt


def test_system_bonus_table_hash_and_masking(client, monkeypatch):
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    body = client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": SYS_KEY}).json()
    canonical = json.dumps(body["table"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert body["sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    text = json.dumps(body, ensure_ascii=False)
    assert not runner.REAL_CODE_PATTERN.search(text) and not [code for code in runner.load_mask() if code in text]
    assert body == client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": SYS_KEY}).json()   # gọi lại ⇒ cùng bảng, cùng mã băm


def test_bonus_table_hash_survives_review(client, monkeypatch):
    # Người duyệt gắn mã băm LÚC duyệt; Xuất kiểm lại SAU khi đã duyệt ⇒ mã phải giữ nguyên dù trạng thái đổi (trạng thái nằm ngoài mã băm)
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    before = client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": SYS_KEY}).json()
    reviewed = client.post(f"/api/runs/{before['run_id']}/review", json={"reviewer": "Người thử"})
    assert reviewed.status_code == 200, reviewed.text
    after = client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": SYS_KEY}).json()
    assert after["status"] != before["status"] and after["sha256"] == before["sha256"]


def test_tool_call_does_not_write(client, tmp_path):
    # POST nhưng chỉ đọc: mọi công cụ × tham số gọi xong, file bảng lưu (SQLite) không đổi một byte
    store_file = tmp_path / "kpi_store.sqlite"
    before = hashlib.sha256(store_file.read_bytes()).hexdigest()
    for name, args in CALLS:
        client.post(f"/api/tool/{name}", json={"args": dict(args)})
    assert hashlib.sha256(store_file.read_bytes()).hexdigest() == before


# /api/system/kiem-so (lõi kiem_so) — số mong đợi lấy từ chính kết quả công cụ, không gõ tay ----------
def _money(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _kiem(client, body: dict):
    return client.post("/api/system/kiem-so", json=body, headers={"X-Kpi-Sys-Key": SYS_KEY})


def test_kiem_so_needs_key(client, monkeypatch):
    assert client.post("/api/system/kiem-so", json={"answer": "x"}).status_code == 503
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    assert client.post("/api/system/kiem-so", json={"answer": "x"}).status_code == 401


def test_kiem_so_true_answer_passes_and_carries_bonus_table(client, monkeypatch):
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    team = client.post("/api/tool/get_team_bonus_table", json={"args": {}}).json()
    salary = {row["employee_id"]: row["business_salary"] for row in team["employees"]}
    answer = f"NV05 hiện có {_money(salary['NV05'])} đồng lương kinh doanh. Tổng cả đội {_money(sum(salary.values()))} đồng."
    body = _kiem(client, {"answer": answer, "question": "NV05 được bao nhiêu?"}).json()
    assert body["dat"] is True, body["loi"]
    table = client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": SYS_KEY}).json()
    assert body["sha256_bang"] == table["sha256"] and body["bang_thuong"] == table["table"] and body["phien_ban"].startswith("kiem_so@")


def test_kiem_so_blocks_invented_and_other_persons_number(client, monkeypatch):
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    team = client.post("/api/tool/get_team_bonus_table", json={"args": {}}).json()
    salary = {row["employee_id"]: row["business_salary"] for row in team["employees"]}
    invented = _kiem(client, {"answer": "NV05 hiện có 9.876.543 đồng lương kinh doanh."}).json()
    assert invented["dat"] is False and {item["loai"] for item in invented["loi"]} == {"so_khong_nguon"}
    other = next(nv for nv, value in salary.items() if value != salary["NV05"] and value > 0)
    swapped = _kiem(client, {"answer": f"NV05 hiện có {_money(salary[other])} đồng lương kinh doanh."}).json()
    assert swapped["dat"] is False and "so_sai_nguoi" in {item["loai"] for item in swapped["loi"]}


def test_kiem_so_refuses_caller_sources_and_bad_body(client, monkeypatch):
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    assert _kiem(client, {"answer": "x", "tool_results": [{"total": 1}]}).status_code == 422   # bên gọi không được tự đưa nguồn số
    bad = _kiem(client, {"question": "thiếu answer"})
    assert bad.status_code == 200 and bad.json()["dat"] is False and bad.json()["loi"][0]["loai"] == "dau_vao_loi"


def test_kiem_so_reruns_tool_calls(client, monkeypatch):
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    result = client.post("/api/tool/get_employee_result", json={"args": {"employee_id": "NV05"}}).json()
    calls = [{"name": "get_employee_result", "args": {"employee_id": "NV05"}}]
    team = client.post("/api/tool/get_team_bonus_table", json={"args": {}}).json()
    nv05 = next(row["business_salary"] for row in team["employees"] if row["employee_id"] == "NV05")
    assert result and _kiem(client, {"answer": f"NV05 hiện có {_money(nv05)} đồng.", "tool_calls": calls}).json()["dat"] is True
    # tool_calls chỉ gọi công cụ một người ⇒ tổng cả đội KHÔNG có nguồn ⇒ trượt (máy chủ chạy lại đúng các lần gọi, không lấy nguồn cả đội)
    assert _kiem(client, {"answer": "Tổng cả đội 55.700.000 đồng.", "tool_calls": calls}).json()["dat"] is False


def test_kiem_so_real_excel_passes_after_review(client, monkeypatch):
    import base64
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    run_id = client.get("/api/meta").json()["current_run_id"]
    assert client.post(f"/api/runs/{run_id}/review", json={"reviewer": "test kiem-so"}).status_code == 200   # khối xuất: chưa soát ⇒ 409
    xlsx = client.get("/api/export/xlsx")
    assert xlsx.status_code == 200
    body = _kiem(client, {"answer": "Đã xuất bảng thưởng.", "excel_b64": base64.b64encode(xlsx.content).decode(), "excel_name": "bang.xlsx"}).json()
    assert body["dat"] is True, body["loi"]


def test_kiem_so_does_not_write(client, monkeypatch, tmp_path):
    # POST nhưng chỉ đọc: kiểm câu đúng / sai / có tool_calls xong, file bảng lưu không đổi một byte
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    store_file = tmp_path / "kpi_store.sqlite"
    before = hashlib.sha256(store_file.read_bytes()).hexdigest()
    for body in ({"answer": "NV05 hiện có 9.876.543 đồng."}, {"answer": "Tổng cả đội 55.700.000 đồng."},
                 {"answer": "x", "tool_calls": [{"name": "get_employee_result", "args": {"employee_id": "NV05"}}]}):
        assert _kiem(client, body).status_code == 200
    assert hashlib.sha256(store_file.read_bytes()).hexdigest() == before


# 10/11: lời từ chối không là nguồn số + nhật ký theo mã lượt (cách 2 — 27 đề xuất, A hai bên chốt) ----------
C_BOGUS_CALLS = [[{"name": "explain_kpi", "args": {"employee_id": "NV02", "kpi": "5.041.000"}}],                         # KPI lạ
                 [{"name": "simulate_kpi_change", "args": {"employee_id": "NV02", "kpi": "PC_BT", "extra": "5041000"}}],   # extra vượt trần
                 [{"name": "get_employee_result", "args": {"employee_id": "5041000"}}]]                                  # mã NV lạ


@pytest.mark.parametrize("calls", C_BOGUS_CALLS)
def test_refusal_echo_is_not_a_source(client, monkeypatch, calls):
    # Ca: AI cài số bịa vào tham số ⇒ lời từ chối nhắc lại số đó ⇒ trước đây "có nguồn" ⇒ dat TRUE
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    body = _kiem(client, {"answer": "NV02 được 5.041.000 đồng.", "tool_calls": calls}).json()
    assert body["dat"] is False and "so_khong_nguon" in {item["loai"] for item in body["loi"]}, body


def _call(client, name, args, run_id, org="org-thu"):
    return client.post(f"/api/tool/{name}", json={"args": args}, headers={"X-Kpi-Run-Id": run_id, "X-Kpi-Org-Id": org}).json()


def test_run_id_two_calls_then_kiem_so_passes(client, monkeypatch):
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    target = _call(client, "simulate_kpi_change", {"employee_id": "NV05", "kpi": "ASO_ALL", "target_pct": 100}, "luot-1")
    nv05 = _call(client, "get_employee_result", {"employee_id": "NV05"}, "luot-1")
    assert "refused" not in target and "refused" not in nv05
    team = client.post("/api/tool/get_team_bonus_table", json={"args": {}}).json()   # KHÔNG có mã lượt ⇒ không ghi
    salary = next(row["business_salary"] for row in team["employees"] if row["employee_id"] == "NV05")
    ok = _kiem(client, {"answer": f"NV05 hiện có {_money(salary)} đồng.", "run_id": "luot-1", "org_id": "org-thu"}).json()
    assert ok["dat"] is True, ok["loi"]
    # câu "nếu đạt X %" chỉ có số của simulate — qua vì lần gọi đó nằm trong nhật ký lượt (không có run_id thì trượt — C điểm 2)
    change = target["amount_change"] if isinstance(target["amount_change"], int) else None
    if change:
        assert _kiem(client, {"answer": f"Nếu đạt 100 % thì NV05 có thêm {_money(change)} đồng.", "run_id": "luot-1",
                              "org_id": "org-thu"}).json()["dat"] is True


def test_run_id_unknown_other_run_other_org_fail(client, monkeypatch):
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    _call(client, "get_employee_result", {"employee_id": "NV05"}, "luot-A")
    nv04 = _call(client, "get_employee_result", {"employee_id": "NV04"}, "luot-B")
    team = client.post("/api/tool/get_team_bonus_table", json={"args": {}}).json()
    salary04 = next(row["business_salary"] for row in team["employees"] if row["employee_id"] == "NV04")
    assert "refused" not in nv04
    other_run = _kiem(client, {"answer": f"NV04 hiện có {_money(salary04)} đồng.", "run_id": "luot-A", "org_id": "org-thu"}).json()
    assert other_run["dat"] is False                                                         # số của lượt khác ⇒ trượt
    # ĐỔI TIÊU CHÍ (10/11 theo 4bd59dd, major): mã lạ ⇒ nguồn RỖNG (không trượt sẵn) ⇒ câu có số trượt, câu không số qua
    assert _kiem(client, {"answer": "NV05 được 5.040.000 đồng.", "run_id": "luot-la", "org_id": "org-thu"}).json()["dat"] is False
    assert _kiem(client, {"answer": "Chưa có dữ liệu lương cơ bản.", "run_id": "luot-la", "org_id": "org-thu"}).json()["dat"] is True
    for body in ({"answer": "Không có số.", "run_id": "luot-A", "org_id": "org-khac"},      # cùng mã, tổ chức khác ⇒ trượt sẵn
                 {"answer": "Không có số.", "run_id": "luot-A"},                            # thiếu tổ chức (= tổ chức khác) ⇒ trượt sẵn
                 {"answer": "Không có số.", "run_id": "luot A có dấu cách"}):               # sai dạng ⇒ trượt sẵn
        fail = _kiem(client, body).json()
        assert fail["dat"] is False and fail["loi"][0]["loai"] == "khong_co_lan_goi" and "sha256_bang" in fail, body


def test_run_id_limits_and_bad_input(client, monkeypatch):
    from kpi_agent.backend import main
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    assert _kiem(client, {"answer": "x", "run_id": "r", "tool_calls": []}).status_code == 422
    assert client.post("/api/tool/get_team_bonus_table", json={"args": {}}, headers={"X-Kpi-Run-Id": "co dau cach"}).status_code == 422   # header chỉ ASCII ⇒ thử dấu cách
    # lời từ chối không ghi vào nhật ký ⇒ lượt chỉ có lời từ chối = nguồn rỗng: câu từ chối đúng (không số) qua; câu có số trượt (ý E 1)
    _call(client, "get_employee_result", {"employee_id": "NV99"}, "luot-tu-choi")
    assert _kiem(client, {"answer": "Không có NV99 trong đội.", "run_id": "luot-tu-choi", "org_id": "org-thu"}).json()["dat"] is True
    assert _kiem(client, {"answer": "NV05 được 5.040.000 đồng.", "run_id": "luot-tu-choi", "org_id": "org-thu"}).json()["dat"] is False
    # quá MAX_CALLS_PER_RUN lần gọi ⇒ lượt tràn ⇒ trượt (không tự cắt bớt nguồn)
    for _ in range(main.MAX_CALLS_PER_RUN + 1):
        _call(client, "get_team_bonus_table", {}, "luot-tran")
    tran = _kiem(client, {"answer": "Không có số.", "run_id": "luot-tran", "org_id": "org-thu"}).json()
    assert tran["dat"] is False and "vượt 20" in tran["loi"][0]["chi_tiet"]
    # quá MAX_LOGGED_RUNS lượt ⇒ lượt cũ nhất bị bỏ
    _call(client, "get_team_bonus_table", {}, "luot-cu-nhat")
    for index in range(main.MAX_LOGGED_RUNS):
        _call(client, "get_mid_month_gap", {}, f"luot-moi-{index}")
    # lượt cũ bị đẩy khỏi nhật ký ⇒ coi như mã lạ ⇒ câu có số (tổng đội của chính lượt đó) trượt
    assert _kiem(client, {"answer": "Tổng cả đội 55.700.000 đồng.", "run_id": "luot-cu-nhat", "org_id": "org-thu"}).json()["dat"] is False


def test_all_calls_refused_means_no_source(client, monkeypatch):
    # Ý E (1): mọi lần gọi bị từ chối ⇒ nguồn rỗng ⇒ câu có số phải trượt so_khong_nguon
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    calls = [{"name": "get_employee_result", "args": {"employee_id": "NV99"}}, {"name": "explain_kpi", "args": {"employee_id": "NV05", "kpi": "XYZ"}}]
    body = _kiem(client, {"answer": "NV05 được 5.040.000 đồng.", "tool_calls": calls}).json()
    assert body["dat"] is False and {item["loai"] for item in body["loi"]} == {"so_khong_nguon"}


# ---------- 10/11 (A): mã băm bảng thưởng chỉ băm SỐ + NGUỒN — cùng số thì cùng mã qua mọi lần bật ----------
def _bonus_sha(tmp_path, monkeypatch, name: str, workflow=None) -> str:
    from kpi_agent.backend import main
    monkeypatch.setenv("KPI_STORE", str(tmp_path / f"{name}.sqlite"))
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    if workflow is not None:
        monkeypatch.setattr(main, "WORKFLOW", workflow)
    with TestClient(main.app, base_url=BASE) as fresh:
        return fresh.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": SYS_KEY}).json()["sha256"]


def test_bonus_sha_same_across_restarts_and_changes_with_rules(tmp_path, monkeypatch):
    import time
    first = _bonus_sha(tmp_path, monkeypatch, "lan1")
    time.sleep(1.1)   # "Tính lúc" ghi tới giây — bật lần 2 ở giây khác (lỗi cũ: mã đổi mỗi lần bật)
    assert _bonus_sha(tmp_path, monkeypatch, "lan2") == first
    # đổi MỘT ô luật ⇒ mã phải khác. Ô tiền bị bộ kiểm luật chặn (tổng tiền phải khớp lương kinh doanh) ⇒ đổi mốc 95 % đầu tiên thành 96 % (cả nhãn)
    rules_text = (ROOT / "kpi_agent/config/rules_2026_10.yaml").read_text(encoding="utf-8")
    old_tier = '{min: 95, pay_ratio: 0.70, printed_amount: 3920000, label: "Đạt 95%"}'
    assert old_tier in rules_text
    changed_rules = tmp_path / "rules_doi_mot_o.yaml"
    changed_rules.write_text(rules_text.replace(old_tier, old_tier.replace("min: 95", "min: 96").replace("Đạt 95%", "Đạt 96%"), 1), encoding="utf-8")
    workflow_text = (ROOT / "kpi_agent/workflows/kpi_thuong_nvbh.yaml").read_text(encoding="utf-8")
    changed_workflow = tmp_path / "wf_doi_mot_o.yaml"
    changed_workflow.write_text(workflow_text.replace("kpi_agent/config/rules_2026_10.yaml", str(changed_rules)), encoding="utf-8")
    assert _bonus_sha(tmp_path, monkeypatch, "lan3", workflow=changed_workflow) != first



def test_period_refusal_via_run_id_passes(client, monkeypatch):
    # Đường thật trên Capitol: AI hỏi kỳ khác ⇒ công cụ từ chối (không ghi nhật ký) ⇒ Rào chắn gửi run_id ⇒ nguồn rỗng ⇒ câu từ chối đúng phải QUA
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    _call(client, "get_team_bonus_table", {"period": "2026-09"}, "luot-ky")
    body = _kiem(client, {"answer": "Chưa có dữ liệu kỳ 2026-09; hiện chỉ có kỳ 2026-10.", "run_id": "luot-ky", "org_id": "org-thu"}).json()
    assert body["dat"] is True, body["loi"]
