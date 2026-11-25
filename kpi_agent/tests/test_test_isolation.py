"""Bộ test không ghi vào dữ liệu thật dưới kpi_agent/data/ (conftest.py cô lập).

Vì sao (18/11/2026, scripts/gop_co_kiem.sh): worktree gộp nối kpi_agent/data bằng LIÊN KẾT tới kho chính ⇒ test nào ghi vào đó là ghi vào dữ
liệu thật. conftest đã cô lập KPI_STORE từ trước, chưa cô lập thư mục file tải lên (KPI_UPLOAD_DIR — mặc định kpi_agent/data/uploads) và bản ghi
chat của agent (server.LOG_DIR — kpi_agent/data/chat_logs; nhiều test tự đổi từng chỗ, không có lưới chung)."""
import os
from pathlib import Path

REAL_DATA = (Path(__file__).resolve().parents[1] / "data").resolve()


def _inside_real_data(path) -> bool:
    resolved = Path(path).resolve()
    return resolved == REAL_DATA or REAL_DATA in resolved.parents


def test_store_and_upload_dir_are_temporary():
    for name in ("KPI_STORE", "KPI_UPLOAD_DIR"):
        value = os.environ.get(name)
        assert value, f"{name} chưa được conftest đặt ⇒ backend ghi vào kpi_agent/data thật"
        assert not _inside_real_data(value), f"{name}={value} nằm trong kpi_agent/data thật"


def test_backend_upload_dir_follows_env():
    from kpi_agent.backend import main
    # lifespan đọc KPI_UPLOAD_DIR; thiếu ⇒ UPLOAD_DIR (kpi_agent/data/uploads) — biến phải có trước mọi lần bật backend trong test
    assert Path(os.environ["KPI_UPLOAD_DIR"]) != main.UPLOAD_DIR


def test_agent_chat_log_dir_is_temporary():
    from kpi_agent.agent import server
    assert not _inside_real_data(server.LOG_DIR), f"server.LOG_DIR={server.LOG_DIR} nằm trong kpi_agent/data thật"
