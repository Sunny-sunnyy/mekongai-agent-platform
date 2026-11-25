"""Chế độ "một nguồn số" (`agent/xem_proxy.py` + các đường /api/* của máy chủ trợ lý).

CỔNG GIẢ: hai ứng dụng nhỏ trong file này đóng vai cổng xem 18403 và cổng vân tay 18402 của bộ Capitol (nối thẳng vào httpx, không mở cổng thật).
Test ở đây kiểm ĐƯỜNG CODE phía mình (danh sách đường, hai mã tách nhau, hàng đợi, vân tay, đóng khi nghi ngờ) — CHƯA phải bằng chứng chạy thật
với hộp của Capitol; "xong" thật = một ca chạy với hộp 18403 thật khi bên đó bật."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from kpi_agent.agent import backend_client, server, xem_proxy
from kpi_agent.agent.backend_client import AgentContext

XEM, MAIN = "http://xem.test", "http://chinh.test"
XEM_KEY, MAIN_KEY = "ma-xem-gia-chi-song-trong-test-0123456789", "ma-van-tay-gia-chi-song-trong-test-0123456789"   # mã GIẢ, không phải mã thật
SESSION = "ma-phien-gia-chi-song-trong-test-0123456789"
TINH = {"kpi_head": "aafb7a8d", "che_do": "xem", "ds_sha256": "ab" * 32, "du_lieu": {"RAW DATA.xlsx": "0f" * 8}}
DONG = {"period": "2026-10", "cutoff": "2026-10-31", "files_sha256": {"a": "1", "b": "2", "c": "3", "d": "4"}, "rule_version": "luat@1", "workflow_version": "wf@1"}


class Gate:
    """Trạng thái của hai cổng giả — test đổi các ô này để dựng từng tình huống."""

    def __init__(self):
        self.calls, self.busy_times, self.active, self.peak = [], 0, 0, 0
        self.view = {"tinh": dict(TINH), "dong": dict(DONG)}
        self.main = {"tinh": dict(TINH), "dong": {**DONG, "status": "tam_tinh"}}
        self.main_status, self.plain_text, self.lock = 200, False, threading.Lock()
        self.fingerprint_failures = 0   # số lần kế tiếp cổng xem trả 503 cho /van-tay (trục trặc thoáng qua)


def _apps(gate: Gate) -> tuple[FastAPI, FastAPI]:
    xem, main = FastAPI(), FastAPI()

    @xem.middleware("http")
    async def key_gate(request: Request, call_next):
        return await call_next(request) if request.headers.get("x-xem-ma") == XEM_KEY else JSONResponse({"detail": "Unauthorized"}, status_code=401)

    @xem.get("/van-tay")
    async def view_fingerprint():
        if gate.fingerprint_failures > 0:
            gate.fingerprint_failures -= 1
            return JSONResponse({"detail": "Service Unavailable"}, status_code=503)
        return gate.view

    @xem.get("/api/{rest:path}")
    async def data(request: Request, rest: str):
        with gate.lock:
            gate.calls.append((f"/api/{rest}", dict(request.query_params), dict(request.headers)))
            gate.active += 1
            gate.peak = max(gate.peak, gate.active)
            busy = gate.busy_times > 0
            gate.busy_times -= busy
        try:
            await asyncio.sleep(0.03)
            if busy:
                return JSONResponse({"detail": "Too Many Requests"}, status_code=429)
            if gate.plain_text:
                return PlainTextResponse("<html>không phải JSON</html>")
            return {"duong": f"/api/{rest}", "tham_so": dict(request.query_params), "team_total": 126_660_000}
        finally:
            with gate.lock:
                gate.active -= 1

    @main.get("/van-tay")
    async def main_fingerprint(request: Request):
        if request.headers.get("x-van-tay-ma") != MAIN_KEY:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return JSONResponse(gate.main, status_code=gate.main_status)

    return xem, main


def _key_file(tmp_path, name, line):
    path = tmp_path / name
    path.write_text(line + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


class Model:
    def __init__(self):
        self.seen = []

    def invoke(self, messages):
        self.seen.append(messages)
        return AIMessage(content="Chưa có dữ liệu cho câu này.")


@pytest.fixture
def n2(tmp_path, monkeypatch):
    """Máy chủ trợ lý bật chế độ "một nguồn số" trỏ vào hai cổng GIẢ; mã phiên của trang bật thật."""
    gate, model = Gate(), Model()
    xem_app, main_app = _apps(gate)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(server.httpx, "AsyncClient", lambda: real_client(mounts={
        XEM + "/": httpx.ASGITransport(app=xem_app), MAIN + "/": httpx.ASGITransport(app=main_app)}))
    monkeypatch.setenv("KPI_XEM_URL", XEM)
    monkeypatch.setenv("KPI_VAN_TAY_URL", MAIN)
    monkeypatch.setenv("KPI_XEM_MA_FILE", str(_key_file(tmp_path, "xem.env", f"XEM_MA={XEM_KEY}")))
    monkeypatch.setenv("KPI_VAN_TAY_FILE", str(_key_file(tmp_path, "van_tay.env", f"VAN_TAY_MA={MAIN_KEY}")))
    monkeypatch.delenv("KPI_SESSION_KEY", raising=False)
    monkeypatch.setenv("KPI_SESSION_KEY_FILE", str(_key_file(tmp_path, "session.key", SESSION)))
    monkeypatch.setattr(xem_proxy, "FINGERPRINT_TTL_S", 0)        # test đổi vân tay giữa chừng ⇒ không giữ kết quả so
    monkeypatch.setattr(xem_proxy, "BUSY_WAIT_S", 0.01)
    ctx = AgentContext(XEM, "2026-10", date(2026, 10, 31), [f"NV{i:02d}" for i in range(1, 15)], [], [])
    monkeypatch.setattr(server, "load_context", lambda url: ctx)
    monkeypatch.setattr(server, "make_model", lambda kpis, reasoning, names=False: model)
    monkeypatch.setattr(server, "LOG_DIR", tmp_path / "logs")
    with TestClient(server.app, base_url="http://127.0.0.1:8120", headers={"X-Kpi-Session": SESSION}) as client:
        yield client, gate, model, tmp_path


def test_mode_off_keeps_old_behaviour(tmp_path, monkeypatch):
    monkeypatch.delenv("KPI_XEM_URL", raising=False)
    monkeypatch.setattr(server, "load_context", lambda url: AgentContext("http://127.0.0.1:9", "2026-10", date(2026, 10, 20), ["NV01"], [], []))
    monkeypatch.setattr(server, "make_model", lambda kpis, reasoning, names=False: Model())
    monkeypatch.setattr(server, "LOG_DIR", tmp_path)
    with TestClient(server.app, base_url="http://127.0.0.1:8120") as client:
        assert client.app.state.xem is None
        codes = [client.get("/api/meta").status_code, client.get("/api/xem/trang-thai").status_code, client.post("/api/runs/1/review").status_code,
                 client.get("/api/chat").status_code]
        assert codes == [404, 404, 404, 405]                         # không bật ⇒ máy chủ trợ lý vẫn chỉ có POST /api/chat


def test_listed_get_is_forwarded_with_the_service_key_only(n2):
    client, gate, _, _ = n2
    response = client.get("/api/employees/NV05", params={"period": "2026-10"}, headers={"Cookie": "a=b", "Authorization": "Bearer x", "X-La": "1"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/json")
    assert response.json() == {"duong": "/api/employees/NV05", "tham_so": {"period": "2026-10"}, "team_total": 126_660_000}   # nguyên thân cổng xem trả
    path, query, headers = gate.calls[-1]
    assert (path, query) == ("/api/employees/NV05", {"period": "2026-10"}) and headers["x-xem-ma"] == XEM_KEY
    assert not {"cookie", "authorization", "x-la", "x-kpi-session"} & set(headers)      # không header nào của trình duyệt đi tiếp, kể cả mã phiên


@pytest.mark.parametrize("path", ["/api/system/bonus-table", "/api/tool/get_employee_result", "/api/export/xlsx", "/api/workflow", "/api/blocks",
                                  "/api/khong-co", "/api/meta/them", "/api/employees/NV05/x"])
def test_routes_outside_the_list_are_404_and_never_reach_the_gate(n2, path):
    client, gate, _, _ = n2
    assert client.get(path).status_code == 404 and gate.calls == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_only_get_goes_through(n2, method):
    client, gate, _, _ = n2
    assert client.request(method, "/api/runs/1/review", json={}).status_code == 405 and gate.calls == []


@pytest.mark.parametrize("path, params", [
    ("/api/employees/NV5", None), ("/api/employees/nv05", None), ("/api/employees/NV05%2F..%2FNV01", None), ("/api/explain/NV05/KHONG_CO", None),
    ("/api/meta", {"la": "1"}), ("/api/gap", {"employee_id": "NV05\n"}), ("/api/gap", {"period": "2026-13"}), ("/api/runs/0", None),
    ("/api/what-if/NV05/PC_BT", {"extra": "-5"}), ("/api/what-if/NV05/PC_BT", {"extra": "1e+16"}), ("/api/what-if/NV05/PC_BT", {"target_pct": "abc"}),
])
def test_bad_segments_and_params_are_422_before_the_gate(n2, path, params):
    client, gate, _, _ = n2
    response = client.get(path, params=params)
    assert response.status_code == 422 and "refused" in response.json() and gate.calls == []


def test_repeated_param_is_refused(n2):
    client, gate, _, _ = n2
    assert client.get("/api/gap?employee_id=NV05&employee_id=NV01").status_code == 422 and gate.calls == []


def test_page_session_key_is_checked_before_anything(n2):
    client, gate, _, _ = n2
    for path in ("/api/meta", "/api/xem/trang-thai", "/api/system/bonus-table"):
        for headers in ({"X-Kpi-Session": ""}, {"X-Kpi-Session": "sai" * 15}, {"X-Kpi-Session": XEM_KEY}):   # mã dịch vụ KHÔNG mở được cửa của trang
            assert client.get(path, headers=headers).status_code == 401
    assert gate.calls == []


def test_service_keys_never_appear_in_replies_or_logs(n2):
    client, gate, _, tmp_path = n2
    texts = [client.get("/api/team-board").text, client.get("/api/xem/trang-thai").text, client.get("/api/khong-co").text,
             client.get("/api/meta", params={"la": XEM_KEY}).text, client.post("/api/chat", json={"question": "Lương của NV01?"}).text]
    gate.view["tinh"]["kpi_head"] = "khac"
    texts += [client.get("/api/team-board").text, client.get("/api/xem/trang-thai").text]
    logs = "".join(path.read_text(encoding="utf-8") for path in (tmp_path / "logs").glob("*.jsonl"))
    assert logs and not any(key in text for key in (XEM_KEY, MAIN_KEY, SESSION) for text in texts + [logs])
    assert '"xem_duong_sha8"' in logs                                   # lúc bật ghi 8 ký tự sha của file danh sách đường để đối chiếu với bên Capitol


def test_forwarded_bodies_are_never_written_to_the_agent_log(n2):
    # 13/11: thân /api/outlets qua cổng xem ≈ 950 KB toàn tên + địa chỉ khách ⇒ lối chuyển tiếp chỉ chuyển, không ghi thân / tham số vào nhật ký của agent
    client, gate, _, tmp_path = n2
    for path in ("/api/outlets", "/api/tea-365", "/api/team-board"):
        assert client.get(path, params={"employee_id": "NV07"} if path != "/api/team-board" else None).status_code == 200
    written = "".join(file.read_text(encoding="utf-8") for file in (tmp_path / "logs").glob("*"))
    assert "126660000" not in written and "outlets" not in written and "NV07" not in written and len(gate.calls) == 3


def test_status_route_has_no_numbers_and_says_in_sync(n2):
    client, _, _, _ = n2
    body = client.get("/api/xem/trang-thai").json()
    assert body["khop"] is True and body["ly_do"] == "khop" and body["status"] == "tam_tinh" and body["duong_sha8"] == xem_proxy.load_routes()[1]
    assert set(body) == {"khop", "ly_do", "loi", "status", "luc_so", "duong_sha8"}


def _break(gate: Gate, how: str) -> None:
    if how == "tinh":
        gate.view["tinh"]["ds_sha256"] = "cd" * 32
    elif how == "dong":
        gate.view["dong"]["rule_version"] = "luat@2"
    elif how == "them_khoa":
        gate.main["dong"]["files_sha256"] = {**DONG["files_sha256"], "e": "5"}      # hộp chính có thêm một file dữ liệu ⇒ tập khoá khác ⇒ lệch
    elif how == "chinh_tat":
        gate.main_status = 502
    elif how == "thieu_phan":
        gate.main = {"tinh": dict(TINH)}
    elif how == "rong":
        gate.view = {"tinh": {}, "dong": {}}


@pytest.mark.parametrize("how", ["tinh", "dong", "them_khoa", "chinh_tat", "thieu_phan", "rong"])
def test_fingerprint_mismatch_closes_every_forwarded_route(n2, how):
    client, gate, _, _ = n2
    _break(gate, how)
    for path in ("/api/team-board", "/api/meta", "/api/names", "/api/spice-loyalty", "/api/outlets"):
        response = client.get(path)
        assert response.status_code == 409 and response.json() == {"refused": xem_proxy.UPDATING}
    assert gate.calls == []                                             # không lời gọi số nào tới cổng xem khi nghi ngờ
    status = client.get("/api/xem/trang-thai").json()
    assert status["khop"] is False and status["loi"] == xem_proxy.UPDATING


def test_one_failed_fingerprint_call_is_retried_but_two_close_the_page(n2, monkeypatch):
    # Nối thử hộp thật 13/11: một lần hỏi vân tay lỗi thoáng qua từng khoá cả trang 15 giây ⇒ hỏi lại MỘT lần; lỗi cả hai lần thì vẫn đóng
    client, gate, _, _ = n2
    gate.fingerprint_failures = 1
    assert client.get("/api/team-board").status_code == 200
    monkeypatch.setattr(xem_proxy, "FINGERPRINT_TTL_S", 0)
    gate.fingerprint_failures = 2
    response = client.get("/api/team-board")
    assert response.status_code == 409 and response.json() == {"refused": xem_proxy.UPDATING}
    assert client.get("/api/xem/trang-thai").json()["ly_do"] == "khong_hoi_duoc"      # kết quả lỗi được giữ ngắn (không hỏi lại ngay trong 2 giây)
    monkeypatch.setattr(xem_proxy, "FINGERPRINT_ERROR_TTL_S", 0)
    assert client.get("/api/team-board").status_code == 200                           # hết hạn giữ ⇒ hỏi lại ⇒ có số


def test_numbers_come_back_when_fingerprints_match_again(n2):
    client, gate, _, _ = n2
    gate.view["dong"]["cutoff"] = "2026-10-30"
    assert client.get("/api/team-board").status_code == 409
    gate.view["dong"]["cutoff"] = "2026-10-31"
    assert client.get("/api/team-board").status_code == 200 and client.get("/api/xem/trang-thai").json()["khop"] is True


def test_new_upload_at_the_main_box_closes_the_page_with_its_own_message(n2):
    client, gate, _, _ = n2
    gate.main["dong"].update({"data_version": "v1", "latest_data_version": "v2"})
    gate.view["dong"]["data_version"] = "v1"
    response = client.get("/api/team-board")
    assert response.status_code == 409 and response.json() == {"refused": xem_proxy.NEW_DATA}
    assert client.get("/api/xem/trang-thai").json()["ly_do"] == "du_lieu_moi"
    gate.main["dong"]["latest_data_version"] = "v1"                    # chưa ai tải lên: mới nhất = bản lúc bật
    gate.view["dong"]["latest_data_version"] = "v1"                    # hộp xem cùng bản ⇒ khớp (13/11: trường này CŨNG đem so)
    assert client.get("/api/team-board").status_code == 200


def test_boxes_differing_only_in_latest_data_version_show_no_numbers(n2):
    # Ca thật bên Capitol 13/11: tải file lên hộp chính rồi dựng lại ⇒ hai cổng lệch ĐÚNG MỘT trường latest_data_version, 6 trường chung kia bằng nhau
    client, gate, _, _ = n2
    gate.main["dong"].update({"data_version": "v2", "latest_data_version": "v2"})
    gate.view["dong"].update({"data_version": "v2", "latest_data_version": "v1"})
    response = client.get("/api/team-board")
    assert response.status_code == 409 and response.json() == {"refused": xem_proxy.UPDATING}
    assert client.get("/api/xem/trang-thai").json()["khop"] is False
    gate.view["dong"]["latest_data_version"] = "v2"
    assert client.get("/api/team-board").status_code == 200


def test_display_only_fields_are_not_compared():
    view, main = {"tinh": TINH, "dong": dict(DONG)}, {"tinh": TINH, "dong": {**DONG, "status": "da_soat"}}
    assert xem_proxy.compare(view, main) == {"khop": True, "ly_do": "khop", "loi": None, "status": "da_soat"}
    # trường có ở hộp chính mà hộp xem không trả ⇒ coi là lệch (đóng khi nghi ngờ)
    assert xem_proxy.compare(view, {"tinh": TINH, "dong": {**DONG, "latest_data_version": "v1"}})["khop"] is False


def test_at_most_four_calls_reach_the_gate_at_once(n2):
    client, gate, _, _ = n2
    paths = ["/api/meta", "/api/names", "/api/team-board", "/api/team-bonus", "/api/gap", "/api/runs", "/api/tracking", "/api/opportunities",
             "/api/data-issues", "/api/extra-bonus", "/api/evaluations", "/api/outlets", "/api/tea-365"]
    with ThreadPoolExecutor(max_workers=13) as pool:
        codes = list(pool.map(lambda path: client.get(path).status_code, paths))
    assert codes == [200] * 13 and len(gate.calls) == 13 and 1 <= gate.peak <= xem_proxy.MAX_PARALLEL


def test_busy_gate_is_retried_then_reported_as_503(n2):
    client, gate, _, _ = n2
    gate.busy_times = 1
    assert client.get("/api/meta").status_code == 200 and len(gate.calls) == 2      # đầy một lần ⇒ thử lại ⇒ có số
    gate.calls.clear()
    gate.busy_times = 99
    response = client.get("/api/meta")
    assert response.status_code == 503 and "đang bận" in response.json()["refused"] and len(gate.calls) == xem_proxy.BUSY_RETRIES + 1


def test_non_json_body_from_the_gate_is_not_passed_on(n2):
    client, gate, _, _ = n2
    gate.plain_text = True
    response = client.get("/api/meta")
    assert response.status_code == 502 and "<html>" not in response.text and response.headers["content-type"].startswith("application/json")


def test_chat_does_not_call_the_model_when_the_source_is_out_of_sync(n2):
    client, gate, model, _ = n2
    gate.view["tinh"]["kpi_head"] = "khac"
    body = client.post("/api/chat", json={"question": "Lương của NV01?"}).json()
    assert body["stop"] == "source_not_in_sync" and xem_proxy.UPDATING in body["answer"] and model.seen == [] and body["usd"] == 0.0
    gate.view["tinh"]["kpi_head"] = TINH["kpi_head"]
    assert client.post("/api/chat", json={"question": "Lương của NV01?"}).json()["stop"] == "answered" and len(model.seen) == 1


def test_agent_still_starts_when_the_gate_is_down_and_recovers_later(n2, monkeypatch):
    # Nối thử hộp thật 13/11: cổng xem không trả lời LÚC BẬT ⇒ agent từng thoát với một dòng lỗi kết nối. Nay: vẫn bật, không số nào, không gọi mô hình;
    # cổng lên lại ⇒ câu hỏi kế nạp ngữ cảnh và trả lời.
    _, gate, model, _ = n2
    good, down = server.load_context, {"yes": True}

    def flaky(url):
        if down["yes"]:
            raise httpx.ConnectError("từ chối kết nối")
        return good(url)
    monkeypatch.setattr(server, "load_context", flaky)
    monkeypatch.setattr(xem_proxy, "FINGERPRINT_ERROR_TTL_S", 0)        # test đổi trạng thái cổng liền nhau ⇒ không giữ kết quả "không hỏi được"
    with TestClient(server.app, base_url="http://127.0.0.1:8120", headers={"X-Kpi-Session": SESSION}) as client:
        assert client.app.state.ctx is None and client.app.state.model is None
        response = client.post("/api/chat", json={"question": "Lương của NV01?"})
        assert response.status_code == 409 and response.json() == {"refused": xem_proxy.UPDATING} and model.seen == []
        gate.main_status = 502                                             # hộp cũng không cho hỏi vân tay ⇒ đường số đóng như mọi lúc nghi ngờ
        assert client.get("/api/team-board").status_code == 409
        gate.main_status, down["yes"] = 200, False
        assert client.post("/api/chat", json={"question": "Lương của NV01?"}).json()["stop"] == "answered" and len(model.seen) == 1


def test_normal_mode_still_refuses_to_start_without_its_backend(tmp_path, monkeypatch):
    monkeypatch.delenv("KPI_XEM_URL", raising=False)

    def refuse(url):
        raise httpx.ConnectError("từ chối kết nối")
    monkeypatch.setattr(server, "load_context", refuse)
    monkeypatch.setattr(server, "LOG_DIR", tmp_path)
    with pytest.raises(httpx.ConnectError):
        with TestClient(server.app, base_url="http://127.0.0.1:8120"):
            pass


# ---------- phần không cần máy chủ: file mã, lời gọi của chính agent ----------
def test_key_file_must_be_private_and_complete(tmp_path):
    good = _key_file(tmp_path, "a.env", f"# ghi chú\nXEM_MA={XEM_KEY}")
    assert xem_proxy.read_key_file(good, "XEM_MA") == XEM_KEY
    good.chmod(0o644)
    for path, name in ((good, "XEM_MA"), (_key_file(tmp_path, "b.env", f"XEM_MA={XEM_KEY}"), "VAN_TAY_MA"), (_key_file(tmp_path, "c.env", "XEM_MA="), "XEM_MA"),
                       (tmp_path / "khong_co.env", "XEM_MA")):
        with pytest.raises(RuntimeError) as stopped:
            xem_proxy.read_key_file(path, name)
        assert XEM_KEY not in str(stopped.value) and str(tmp_path) not in str(stopped.value)     # lời lỗi không chứa mã, không chứa đường dẫn đầy đủ


def test_agent_does_not_start_without_the_service_key(tmp_path, monkeypatch):
    monkeypatch.setenv("KPI_XEM_URL", XEM)
    monkeypatch.setenv("KPI_XEM_MA_FILE", str(tmp_path / "khong_co.env"))
    monkeypatch.setenv("KPI_SESSION_KEY", "off")
    monkeypatch.setattr(server, "LOG_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="không đọc được file mã"):
        with TestClient(server.app, base_url="http://127.0.0.1:8120"):
            pass


TOOL_CALLS = [("get_team_bonus_table", {}), ("get_employee_result", {"employee_id": "NV05"}), ("get_mid_month_gap", {"employee_id": "NV07"}),
              ("get_mid_month_gap", {}), ("explain_kpi", {"employee_id": "NV01", "kpi": "Turnover"}), ("get_opportunities", {"period": "2026-10"}),
              ("get_data_provenance", {}), ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "PC_BT", "extra": 5}),
              ("simulate_kpi_change", {"employee_id": "NV05", "kpi": "LPPC", "target_pct": 100.5}), ("get_team_kpi_summary", {"kpi": "ASO_ALL"}),
              ("compare_employees", {"employee_a": "NV01", "employee_b": "NV02", "kpi": "PC_ON"}), ("get_data_issues", {"employee_id": "NV03"}),
              ("get_extra_bonus", {"employee_id": "NV05", "period": "2026-10"})]


def test_every_call_the_agent_makes_is_on_the_list(monkeypatch):
    # Không dựa vào đối chiếu tên: chạy THẬT từng công cụ + bước đọc ngữ cảnh với một backend ghi lại (đường, tham số), rồi cho qua đúng phép khớp của cổng
    seen = []

    def recording_get(backend_url, path, query=None):
        seen.append((path, [(key, str(value)) for key, value in (query or {}).items()]))
        body = {"period": "2026-10", "cutoff": "2026-10-31", "employee_ids": ["NV01"], "kpis": ["PC_BT"], "data_flags_for_model": [], "current_run_id": 7,
                "names": {"NV01": {"name": "A", "sm_code": "S"}}, "snapshot_at": None, "rule_version": "x", "status": "tam_tinh", "status_label_for_model": "x",
                "items": [], "employees": [], "team_sentences": []}
        return httpx.Response(200, json=body, request=httpx.Request("GET", f"{backend_url}{path}"))
    monkeypatch.setattr(backend_client, "backend_get", recording_get)
    monkeypatch.setenv("KPI_NAMES", "on")
    ctx = backend_client.load_context(XEM)                              # /api/meta + /api/names
    ctx.kpis = ["Turnover", "PC_BT", "LPPC", "ASO_ALL", "PC_ON"]
    ctx.employee_ids = [f"NV{i:02d}" for i in range(1, 15)]
    ctx.name_entries = {}
    backend_client.provenance(ctx)
    for name, args in TOOL_CALLS:
        backend_client.call_tool(ctx, name, args)
    routes, _sha = xem_proxy.load_routes()
    outside = [(path, query) for path, query in seen if xem_proxy.match(routes, path, query)[0] != 200]
    assert outside == [] and len({path.split("/")[2] for path, _ in seen}) >= 12 and len(seen) >= 16


def test_agent_own_calls_use_the_service_key_and_turn_422_into_a_refusal(tmp_path, monkeypatch):
    sent = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        sent.update(url=url, headers=headers, timeout=timeout)
        return httpx.Response(422, json={"detail": "x"}, request=httpx.Request("GET", url))
    monkeypatch.setattr(backend_client.httpx, "get", fake_get)
    monkeypatch.setenv("KPI_XEM_URL", XEM)
    monkeypatch.setenv("KPI_XEM_MA_FILE", str(_key_file(tmp_path, "xem.env", f"XEM_MA={XEM_KEY}")))
    monkeypatch.setenv("KPI_SESSION_KEY_FILE", str(_key_file(tmp_path, "session.key", SESSION)))
    monkeypatch.delenv("KPI_SESSION_KEY", raising=False)
    ctx = AgentContext(XEM, "2026-10", date(2026, 10, 31), ["NV05"], ["PC_BT"], [])
    result = backend_client.call_tool(ctx, "simulate_kpi_change", {"employee_id": "NV05", "kpi": "PC_BT", "extra": -5})
    assert "refused" in result and "error" not in result                 # mô hình nhận lời từ chối để sửa tham số, không phải "backend không trả lời"
    assert sent["headers"] == {"X-Xem-Ma": XEM_KEY} and sent["timeout"] == xem_proxy.CALL_TIMEOUT_S and sent["url"].startswith(XEM)
    # backend trên máy (không phải cổng xem) vẫn dùng mã phiên như cũ
    backend_client.backend_get("http://127.0.0.1:8110", "/api/meta")
    assert sent["headers"] == {"x-kpi-session": SESSION} and sent["timeout"] == backend_client.BACKEND_TIMEOUT_S


def test_routes_file_is_read_not_imported_from_scripts():
    source = (xem_proxy.ROUTES_FILE.parents[1] / "agent/xem_proxy.py").read_text(encoding="utf-8")
    assert "import kpi_agent.scripts" not in source and "from kpi_agent.scripts" not in source and json.loads(xem_proxy.ROUTES_FILE.read_text(encoding="utf-8"))["duong"]
