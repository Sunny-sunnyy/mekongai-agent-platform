"""Mọi test bật backend (TestClient chạy lifespan) ghi vào chỗ TẠM, không vào kpi_agent/data/ thật: bảng lưu bậc 2a (KPI_STORE), thư mục file tải
lên (KPI_UPLOAD_DIR), bản ghi chat của agent (server.LOG_DIR).

18/11/2026 (scripts/gop_co_kiem.sh — A: "không bao giờ cắt an toàn dữ liệu"): worktree gộp nối kpi_agent/data bằng LIÊN KẾT tới kho chính ⇒
test ghi vào đó là ghi vào dữ liệu thật. Trước đó chỉ KPI_STORE được cô lập; test tải lên không đặt KPI_UPLOAD_DIR ghi vào kpi_agent/data/uploads.
Test tự đặt BIẾN MÔI TRƯỜNG KPI_UPLOAD_DIR hay đổi server.LOG_DIR (monkeypatch) vẫn thắng; test chỉ đổi hằng `main.UPLOAD_DIR` (vd fixture raw_client
của test_upload) thì bị KPI_UPLOAD_DIR ở đây đè (lifespan đọc biến trước hằng) — vô hại vì cả hai đều là thư mục tạm.
Kiểm: test_test_isolation.py."""
import os

import pytest

ISOLATED = ("KPI_STORE", "KPI_UPLOAD_DIR", "KPI_SESSION_KEY")


@pytest.fixture(scope="session", autouse=True)
def temporary_kpi_store(tmp_path_factory):
    old = {name: os.environ.get(name) for name in ISOLATED}
    os.environ["KPI_STORE"] = str(tmp_path_factory.mktemp("store") / "kpi_store.sqlite")
    os.environ["KPI_UPLOAD_DIR"] = str(tmp_path_factory.mktemp("uploads"))
    # Mã phiên: backend / agent mặc định KHÔNG bật khi thiếu mã ⇒ bộ test tắt TƯỜNG MINH ở đúng một chỗ này;
    # test của chính mã phiên (test_session_key.py) tự gỡ biến này và đặt file mã.
    os.environ["KPI_SESSION_KEY"] = "off"
    from kpi_agent.agent import server
    old_log_dir = server.LOG_DIR
    server.LOG_DIR = tmp_path_factory.mktemp("chat_logs")
    yield
    server.LOG_DIR = old_log_dir
    for name, value in old.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
