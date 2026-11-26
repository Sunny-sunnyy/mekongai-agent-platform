"""Mã phiên (`kpi_agent/session_key.py` — ca kiểm ghi trước).

Giữ sàn: thứ không có mã (hộp Docker, tiến trình khác trên máy) không đọc được tiền / tên, không ghi được dấu "Đã soát", không gọi được mô hình;
có mã thì mọi thứ như trước. Backend chạy thật (TestClient + lifespan, dữ liệu thật). Danh sách đường lấy từ CHÍNH `app.routes`.
Agent: mô hình là bản viết tay chỉ ĐẾM lần bị gọi (như test_agent_server.py) — để chứng minh 401 không tới mô hình, không tốn tiền."""
import json
import secrets
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from kpi_agent import session_key
from kpi_agent.agent import backend_client, server
from kpi_agent.agent.backend_client import AgentContext
from kpi_agent.backend import main

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
needs_data = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
BASE = "http://127.0.0.1:8110"
SYS_KEY = "khoa-thu-trong-test-0123456789"   # khoá hệ thống giả chỉ sống trong test
UNAUTHORIZED = (401, {"detail": "Unauthorized"})


def _key_file(folder: Path, name: str = "session.key", mode: int = 0o600, text: str | None = None) -> tuple[Path, str]:
    key = secrets.token_urlsafe(32)
    path = folder / name
    path.write_text((key if text is None else text) + "\n", encoding="utf-8")
    path.chmod(mode)
    return path, key


@pytest.fixture
def keyed_env(tmp_path, monkeypatch):
    """Môi trường CÓ mã phiên (gỡ `KPI_SESSION_KEY=off` của conftest). Trả (mã, header đúng)."""
    path, key = _key_file(tmp_path)
    monkeypatch.delenv("KPI_SESSION_KEY", raising=False)
    monkeypatch.delenv("KPI_SYSTEM_ONLY", raising=False)
    monkeypatch.setenv("KPI_SESSION_KEY_FILE", str(path))
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))
    return key, {"X-Kpi-Session": key}


def _all_routes():
    out = []
    for route in main.app.routes:
        path = route.path.replace("{employee_id:path}", "NV05").replace("{kpi}", "PC_BT").replace("{run_id}", "1").replace("{version_id}", "1") \
            .replace("{kind}", "xlsx").replace("{name}", "get_team_bonus_table")
        out += [(method, path) for method in sorted(route.methods)]
    return out


def _answer(response):
    return response.status_code, (response.json() if response.content else None)


@needs_data
def test_backend_without_or_with_wrong_key_is_closed_everywhere(keyed_env, tmp_path):
    key, good = keyed_env
    with TestClient(main.app, base_url=BASE) as client:
        runs_before = client.get("/api/runs", headers=good).json()
        routes = _all_routes()
        assert ("GET", "/api/team-board") in routes and ("GET", "/api/names") in routes and ("POST", "/api/runs/1/review") in routes
        # M1, M2, M7: MỌI đường × phương thức không mã ⇒ 401 cùng một thân (HEAD không có thân)
        for method, path in routes + [(m, "/api/meta") for m in ("PUT", "DELETE", "OPTIONS", "PATCH")]:
            response = client.request(method, path)
            assert response.status_code == 401, (method, path)
            assert method == "HEAD" or response.json() == {"detail": "Unauthorized"}, (method, path)
        # M6: không mã + đường không tồn tại / thân hỏng / Host lạ ⇒ vẫn 401 (không 404 / 422 / 400 làm lộ đường nào có thật)
        assert _answer(client.get("/api/khong-co-duong-nay")) == UNAUTHORIZED
        assert _answer(client.get("/")) == UNAUTHORIZED
        assert _answer(client.post("/api/runs/1/review", content=b"{hong", headers={"content-type": "application/json"})) == UNAUTHORIZED
        assert _answer(client.get("/api/meta", headers={"host": "evil.example"})) == UNAUTHORIZED
        # M5: mã sai mọi kiểu
        old_path, old_key = _key_file(tmp_path, "cu.key")   # "mã của lần bật trước" = một mã hợp lệ khác
        wrong = [{"X-Kpi-Session": key[:-1] + ("A" if key[-1] != "A" else "B")}, {"X-Kpi-Session": ""}, {"X-Kpi-Session": key + " "},
                 {"X-Kpi-Session": " " + key}, {"X-Kpi-Session": old_key}, {"X-Kpi-Key": key}, {"Authorization": f"Bearer {key}"}, {"Cookie": f"k={key}"}]
        for headers in wrong:
            assert _answer(client.get("/api/team-board", headers=headers)) == UNAUTHORIZED, list(headers)
        assert _answer(client.get("/api/team-board", params={"k": key})) == UNAUTHORIZED             # mã ở tham số URL không được nhận
        assert _answer(client.get("/api/team-board", params={"X-Kpi-Session": key})) == UNAUTHORIZED
        # M9: đường hệ thống cần CẢ mã phiên lẫn khoá hệ thống
        assert client.get("/api/system/bonus-table", headers=good).status_code == 401               # có mã, thiếu khoá hệ thống
        assert _answer(client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": SYS_KEY})) == UNAUTHORIZED   # có khoá, thiếu mã
        assert client.get("/api/system/bonus-table", headers={**good, "X-Kpi-Sys-Key": SYS_KEY}).status_code == 200
        # P5: lời 401 không lặp lại mã đã gửi
        leaked = client.get("/api/meta", headers={"X-Kpi-Session": "ma-sai-" + "x" * 40})
        assert "ma-sai" not in leaked.text and not any("ma-sai" in value for value in leaked.headers.values())
        # M2: không lời gọi không-mã nào ghi được vào kho (dấu "Đã soát", lượt chạy)
        assert client.get("/api/runs", headers=good).json() == runs_before


@needs_data
def test_backend_with_key_answers_exactly_like_before(keyed_env, monkeypatch):
    _key, good = keyed_env
    paths = ["/api/meta", "/api/team-board", "/api/team-bonus", "/api/gap", "/api/team-kpi?kpi=PC_BT", "/api/employees/NV05", "/api/names", "/api/tools"]
    with TestClient(main.app, base_url=BASE) as client:
        with_key = {path: _answer(client.get(path, headers=good)) for path in paths}
    assert all(status == 200 for status, _body in with_key.values())
    # cùng kho + cùng dữ liệu, tắt mã tường minh (cách bộ test hiện có chạy) ⇒ thân phải y hệt
    monkeypatch.delenv("KPI_SESSION_KEY_FILE")
    monkeypatch.setenv("KPI_SESSION_KEY", "off")
    with TestClient(main.app, base_url=BASE) as client:
        without = {path: _answer(client.get(path)) for path in paths}
    # `workflow_run_id` trong /api/meta là bộ đếm tăng theo mỗi lần bật backend (không phải dữ liệu) ⇒ bỏ khỏi phép so
    for answers in (with_key, without):
        answers["/api/meta"][1].pop("workflow_run_id")
    assert without == with_key


def _boot_backend():
    with TestClient(main.app, base_url=BASE) as client:
        return client.get("/api/meta").status_code


@needs_data
def test_default_is_closed_backend_does_not_start_without_a_valid_key(tmp_path, monkeypatch):
    monkeypatch.delenv("KPI_SESSION_KEY", raising=False)
    monkeypatch.delenv("KPI_SESSION_KEY_FILE", raising=False)
    monkeypatch.delenv("KPI_SYSTEM_ONLY", raising=False)
    with pytest.raises(RuntimeError, match="thiếu mã phiên"):                      # Q3: không đặt gì ⇒ KHÔNG bật
        _boot_backend()
    for value in ("on", "1", "OFF", "", secrets.token_urlsafe(32)):                # biến chỉ nhận đúng chữ "off"; không nhận giá trị mã
        monkeypatch.setenv("KPI_SESSION_KEY", value)
        with pytest.raises(RuntimeError, match="chỉ nhận 'off'") as stopped:
            _boot_backend()
        assert len(value) < 20 or value not in str(stopped.value)                  # giá trị dài (có thể là mã thật) không được lặp lại trong lời lỗi
    monkeypatch.delenv("KPI_SESSION_KEY")
    bad_files = [tmp_path / "khong_co.key", _key_file(tmp_path, "rong.key", text="")[0], _key_file(tmp_path, "ngan.key", text="abc")[0],
                 _key_file(tmp_path, "cach.key", text="a" * 20 + " " + "b" * 20)[0], _key_file(tmp_path, "mo.key", mode=0o644)[0],
                 _key_file(tmp_path, "nhom.key", mode=0o640)[0]]
    for path in bad_files:
        monkeypatch.setenv("KPI_SESSION_KEY_FILE", str(path))
        with pytest.raises(RuntimeError, match="mã phiên"):
            _boot_backend()


@needs_data
def test_system_only_ignores_session_key(tmp_path, monkeypatch, capsys):
    # Q1, Q2: hộp Capitol (KPI_SYSTEM_ONLY=1) không có mã phiên vẫn bật; lỡ đặt cả file mã ⇒ bỏ qua (không thành "đòi mã mà hộp không có")
    monkeypatch.delenv("KPI_SESSION_KEY", raising=False)
    monkeypatch.delenv("KPI_SESSION_KEY_FILE", raising=False)
    monkeypatch.setenv("KPI_SYSTEM_ONLY", "1")
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    assert _boot_backend() == 200
    path, key = _key_file(tmp_path)
    monkeypatch.setenv("KPI_SESSION_KEY_FILE", str(path))
    with TestClient(main.app, base_url=BASE) as client:
        assert client.get("/api/meta").status_code == 200                           # không cần mã phiên
        assert client.get("/api/system/bonus-table").status_code == 401             # khoá hệ thống vẫn bắt buộc
        assert client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": SYS_KEY}).status_code == 200
        assert client.get("/api/team-board").status_code == 404                     # cổng SYSTEM_ONLY như cũ
    logged = capsys.readouterr().err
    assert "bỏ qua mã phiên" in logged and key not in logged and str(path) not in logged


class CountingModel:
    """Chỉ đếm số lần bị gọi — chứng minh lời gọi không mã KHÔNG tới mô hình."""

    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return AIMessage(content="Chưa có dữ liệu cho câu này.")


@pytest.fixture
def keyed_agent(keyed_env, tmp_path, monkeypatch):
    key, good = keyed_env
    model = CountingModel()
    ctx = AgentContext("http://127.0.0.1:9", "2026-10", date(2026, 10, 20), [f"NV{i:02d}" for i in range(1, 15)], [], [])
    monkeypatch.setattr(server, "load_context", lambda url: ctx)
    monkeypatch.setattr(server, "make_model", lambda kpis, reasoning, names=False: model)
    monkeypatch.setattr(server, "LOG_DIR", tmp_path / "chat_logs")
    (tmp_path / "chat_logs").mkdir()
    with TestClient(server.app, base_url="http://127.0.0.1:8120") as client:
        yield client, model, tmp_path / "chat_logs", key, good


def _events(log_dir: Path) -> list[str]:
    return [json.loads(line)["event"] for path in sorted(log_dir.glob("*.jsonl")) for line in path.read_text(encoding="utf-8").splitlines()]


def test_agent_chat_without_key_never_reaches_the_model(keyed_agent):
    client, model, log_dir, key, good = keyed_agent
    question = {"question": "Tháng này NV05 được bao nhiêu?"}
    before = _events(log_dir)
    assert _answer(client.post("/api/chat", json=question)) == UNAUTHORIZED                                      # M3: không mã
    assert _answer(client.post("/api/chat", json=question, headers={"X-Kpi-Session": key[:-1]})) == UNAUTHORIZED   # mã cụt
    assert _answer(client.post("/api/chat", json=question, params={"k": key})) == UNAUTHORIZED                    # mã ở URL
    assert _answer(client.post("/api/chat", content=b"{hong", headers={"content-type": "application/json"})) == UNAUTHORIZED
    assert _answer(client.post("/api/chat", json=question, headers={"Origin": "http://evil.example"})) == UNAUTHORIZED   # trước cả rào Origin
    assert _answer(client.get("/api/chat")) == UNAUTHORIZED and _answer(client.get("/")) == UNAUTHORIZED
    assert model.calls == 0 and _events(log_dir) == before                          # 0 lần gọi mô hình, sổ chat không thêm dòng nào
    # N4: có mã ⇒ chạy như cũ (câu này bị rào câu hỏi chặn trước mô hình ⇒ vẫn 0 lần gọi)
    body = client.post("/api/chat", json={"question": "Xem NV01/../NV02"}, headers=good).json()
    assert body["stop"] == "input_refused" and model.calls == 0
    assert client.post("/api/chat", json=question, headers=good).status_code == 200 and model.calls >= 1
    assert key not in "".join(path.read_text(encoding="utf-8") for path in log_dir.glob("*.jsonl"))   # P1: mã không vào sổ chat


def test_out_of_scope_refusal_carries_no_incomplete_warning(keyed_agent):
    # 19/11 (đọc diff P5): stop "out_of_scope" thiếu trong danh sách "trọn vẹn" của server ⇒ câu từ chối cố định bị kèm cảnh báo
    # "không trả lời trọn vẹn (out_of_scope)". Đi qua cửa /api/chat thật của server (test của rào chỉ soi bản ghi của chat).
    from kpi_agent.agent.input_guard import OUT_OF_SCOPE_ANSWER
    client, model, _, _, good = keyed_agent
    body = client.post("/api/chat", json={"question": "Viết hộ tôi một email xin nghỉ phép 3 ngày"}, headers=good).json()
    assert body["stop"] == "out_of_scope" and body["answer"] == OUT_OF_SCOPE_ANSWER and model.calls == 0
    assert not [warning for warning in body["warnings"] if "trọn vẹn" in warning], body["warnings"]


def test_agent_does_not_start_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("KPI_SESSION_KEY", raising=False)
    monkeypatch.delenv("KPI_SESSION_KEY_FILE", raising=False)
    monkeypatch.setattr(server, "LOG_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="thiếu mã phiên"):
        with TestClient(server.app, base_url="http://127.0.0.1:8120"):
            pass


@needs_data
def test_agent_calls_backend_with_the_key(keyed_env, monkeypatch):
    # N6: backend ĐANG đòi mã; agent nạp ngữ cảnh + gọi công cụ qua `backend_get` (đọc cùng file mã) ⇒ chạy. Đường HTTP của agent nối vào backend thật.
    _key, _good = keyed_env
    with TestClient(main.app, base_url=BASE) as backend:
        def route(url, params=None, timeout=None, headers=None):
            assert url.startswith(BASE)
            return backend.get(url[len(BASE):], params=params or {}, headers=headers or {})
        monkeypatch.setattr(backend_client.httpx, "get", route)
        ctx = backend_client.load_context(BASE)
        assert ctx.period == "2026-10" and len(ctx.employee_ids) == 14
        assert backend_client.call_tool(ctx, "get_team_bonus_table", {})["team_total"] > 0
        # Bậc tên (KPI_NAMES=on): bảng tên lúc agent bật cũng đi qua backend_get ⇒ mang mã; backend đòi mã mà agent vẫn lấy đủ 14 tên
        monkeypatch.setenv("KPI_NAMES", "on")
        named = backend_client.load_context(BASE)
        assert sorted(named.name_entries) == sorted(ctx.employee_ids) and all(entry["name"] for entry in named.name_entries.values())
        monkeypatch.delenv("KPI_NAMES")
        # bên gọi KHÔNG có mã (không đặt file, không có file mặc định cho cổng này) ⇒ lỗi rõ ngay, không gọi nửa chừng
        monkeypatch.delenv("KPI_SESSION_KEY_FILE")
        monkeypatch.setattr(session_key, "key_dir", lambda: Path("/khong-co-thu-muc-nay"))
        with pytest.raises(RuntimeError, match="không đọc được file mã phiên"):
            backend_client.load_context(BASE)


def test_key_file_lives_outside_the_repo():
    # P3: file mã mặc định KHÔNG nằm dưới gốc repo (kpi_agent/data/ có thể bị gắn vào hộp Docker)
    path = session_key.key_file_for_port(8110).resolve()
    assert ROOT not in path.parents and path.name == "session_8110.key"
