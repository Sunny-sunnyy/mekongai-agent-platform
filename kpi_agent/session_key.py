"""Mã phiên (A +, ca kiểm).

Vì sao có: backend / agent "chỉ nghe 127.0.0.1" vẫn bị một hộp Docker trên cùng máy gọi tới (qua `host.docker.internal` + header Host giả) —
C tái hiện 12/11: bảng thưởng và họ tên thật trả 200. Địa chỉ và header Host đều do bên gọi tự viết ⇒ không phân biệt được hộp với trình duyệt.
Cách chặn: một mã ngẫu nhiên chỉ NGƯỜI MỞ TRANG có (script bật in ra màn hình, file quyền 600 ngoài repo); mọi lời gọi phải mang mã ở header.

MẶC ĐỊNH ĐÓNG: không có mã thì tiến trình KHÔNG bật — trừ hai trường hợp viết rõ: `KPI_SYSTEM_ONLY=1` (hộp trong bộ Capitol: chỉ đường hệ thống,
đã có khoá riêng) hoặc `KPI_SESSION_KEY=off` (bộ test). Một mã chung cho backend + agent + trang (cùng người dùng, cùng máy).
Mã CHỈ đi qua header `X-Kpi-Session` — không nhận ở tham số URL / cookie (URL nằm trong log truy cập)."""
import hmac
import os
import stat
import sys
from pathlib import Path

from starlette.responses import JSONResponse

HEADER = "x-kpi-session"
MIN_LENGTH = 32
# Thân 401 cố định cho MỌI trường hợp thiếu / sai mã: không lặp lại mã đã gửi, không nói sai ở đâu, không lộ đường nào có thật
UNAUTHORIZED_BODY = {"detail": "Unauthorized"}


def key_dir() -> Path:
    """Thư mục file mã — NGOÀI repo và ngoài `kpi_agent/data/` (thư mục có thể bị gắn vào hộp Docker)."""
    runtime = os.getenv("XDG_RUNTIME_DIR")
    return (Path(runtime) if runtime else Path.home() / ".config") / "kpi_agent"


def key_file_for_port(backend_port: int | str) -> Path:
    return key_dir() / f"session_{backend_port}.key"


def _read_key_file(path: Path) -> str:
    """Đọc file mã; file thiếu / quyền rộng hơn chủ sở hữu / mã ngắn hay có khoảng trắng ⇒ RuntimeError (lời không chứa mã)."""
    try:
        mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise RuntimeError(f"không đọc được file mã phiên {path}") from None
    if stat.S_IMODE(mode) & 0o077:
        raise RuntimeError(f"file mã phiên {path} phải có quyền 600 (chỉ chủ sở hữu đọc)")
    key = text.rstrip("\n")
    if len(key) < MIN_LENGTH or key != key.strip() or any(ch.isspace() for ch in key):
        raise RuntimeError(f"file mã phiên {path} không hợp lệ (cần một dòng ≥ {MIN_LENGTH} ký tự, không khoảng trắng)")
    return key


def load_server_key(system_only: bool = False) -> str | None:
    """Mã mà tiến trình này ĐÒI ở mọi lời gọi; None = không đòi (chỉ khi `KPI_SYSTEM_ONLY=1` hoặc `KPI_SESSION_KEY=off`).
    Không có mã mà cũng không thuộc hai trường hợp đó ⇒ RuntimeError ⇒ tiến trình không bật."""
    if system_only:
        # hộp Capitol: mã phiên không dùng (hộp không có trình duyệt nào để giữ mã). Lỡ đặt cả file mã ⇒ BỎ QUA, báo một dòng (không in mã, không in đường dẫn)
        if os.getenv("KPI_SESSION_KEY_FILE"):
            print("KPI_SYSTEM_ONLY=1: bỏ qua mã phiên (KPI_SESSION_KEY_FILE không dùng ở chế độ này)", file=sys.stderr)
        return None
    switch = os.getenv("KPI_SESSION_KEY")
    if switch is not None:
        if switch == "off":
            return None
        # không nhận GIÁ TRỊ mã qua biến môi trường (dễ lọt vào log / lệnh) — chỉ nhận chữ "off"
        raise RuntimeError("KHÔNG bật — KPI_SESSION_KEY chỉ nhận 'off'; mã thật đặt trong file, trỏ bằng KPI_SESSION_KEY_FILE")
    path = os.getenv("KPI_SESSION_KEY_FILE")
    if not path:
        raise RuntimeError("KHÔNG bật — thiếu mã phiên: bật bằng `bash kpi_agent/scripts/chay_ban1.sh start` (script sinh mã), hoặc đặt "
                           "KPI_SESSION_KEY_FILE=<file mã quyền 600>; chỉ bộ test mới đặt KPI_SESSION_KEY=off")
    return _read_key_file(Path(path))


def client_headers(backend_url: str) -> dict[str, str]:
    """Header mã cho bên GỌI backend (agent, script đo). `KPI_SESSION_KEY=off` ⇒ {} (bộ test). Còn lại đọc `KPI_SESSION_KEY_FILE`, không đặt thì
    file mặc định theo cổng của `backend_url`; không có ⇒ RuntimeError rõ ngay (không chạy nửa chừng rồi mới 401)."""
    if os.getenv("KPI_SESSION_KEY") == "off":
        return {}
    path = os.getenv("KPI_SESSION_KEY_FILE")
    if not path:
        port = backend_url.rstrip("/").rsplit(":", 1)[-1]
        path = str(key_file_for_port(port))
    return {HEADER: _read_key_file(Path(path))}


def unauthorized(request_headers, key: str | None) -> JSONResponse | None:
    """None = cho qua. So mã bằng `hmac.compare_digest` (thời gian không phụ thuộc vị trí ký tự sai)."""
    if key is None:
        return None
    sent = request_headers.get(HEADER, "")
    if hmac.compare_digest(sent.encode("utf-8"), key.encode("utf-8")):
        return None
    return JSONResponse(UNAUTHORIZED_BODY, status_code=401)
