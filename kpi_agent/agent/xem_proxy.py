"""Chế độ "một nguồn số" (N2).

Vì sao: trang KPI trên máy tính số bằng backend riêng, trang xưởng bên Capitol tính bằng hộp KPI của họ ⇒ hai nơi có thể ra hai con số.
Bật `KPI_XEM_URL` ⇒ máy chủ trợ lý KHÔNG đọc backend trên máy nữa mà đọc số từ CỔNG XEM chỉ-đọc của hộp KPI trong Capitol, và chuyển tiếp các
lời gọi GET của trang tới cổng đó. Trang ở chế độ này chỉ xem; duyệt + tải file làm ở xưởng.

Ba lớp ở đây (đều đóng khi nghi ngờ):
1. HAI MÃ TÁCH NHAU: mã phiên của trang (trình duyệt giữ, `session_key.py` kiểm ở ngoài cùng) ≠ mã dịch vụ `X-Xem-Ma` (chỉ tiến trình này đọc từ
   file quyền 600 ngoài repo, gắn khi gọi cổng xem). Mã dịch vụ không vào log, không vào thân trả, không vào lời lỗi.
2. DANH SÁCH ĐƯỜNG: chỉ GET, chỉ đúng đường + tham số trong `kpi_agent/config/duong_get_xem.json` (file do `scripts/xuat_duong_get.py` xuất — ở đây
   chỉ ĐỌC FILE, không import `kpi_agent.scripts`). Không chuyển header nào của trình duyệt, không chuyển nguyên chuỗi truy vấn.
3. VÂN TAY: hộp xem và hộp chính phải cùng bản code + dữ liệu. Lệch / không hỏi được / hộp chính đã có dữ liệu mới ⇒ KHÔNG hiện số."""
import asyncio
import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

ROUTES_FILE = Path(__file__).resolve().parents[1] / "config/duong_get_xem.json"
XEM_HEADER, VAN_TAY_HEADER = "X-Xem-Ma", "X-Van-Tay-Ma"
CALL_TIMEOUT_S = 60          # /api/spice-loyalty lần đầu 33 s, /api/runs/{id}/verify 17,5 s — 10 s của backend_get không đủ
MAX_PARALLEL = 4             # cổng xem nhận tối đa 4 yêu cầu cùng lúc, đầy ⇒ 429; màn Cả đội gọi ≈ 12 đường một lượt ⇒ tự xếp hàng ở đây
BUSY_RETRIES, BUSY_WAIT_S = 3, 1.0
FINGERPRINT_TTL_S = 15       # giữ kết quả so vân tay tối đa 15 s (không giữ SỐ — số luôn hỏi lại cổng xem)
FINGERPRINT_TIMEOUT_S = 10
FINGERPRINT_ERROR_TTL_S = 2  # "không hỏi được" chỉ giữ 2 s (xem fingerprint_state)
# Trường của phần động chỉ để HIỆN, không đem so bằng nhau giữa hai hộp. `latest_data_version` PHẢI so (hợp đồng bản 5, 13/11: 7 trường chung —
# period, cutoff, files_sha256, rule_version, workflow_version, data_version, latest_data_version): phép thử bên Capitol 13/11 tải file lên hộp chính
# làm hai cổng lệch ĐÚNG MỘT trường này, 6 trường kia bằng nhau ⇒ bỏ qua nó thì trang vẫn hiện số cũ.
NOT_COMPARED = ("status",)
UPDATING = "nguồn số đang cập nhật — xem bản chính thức ở Xưởng workflow"
NEW_DATA = "đã có dữ liệu mới — xem bản chính thức ở Xưởng workflow"


@dataclass
class XemConfig:
    url: str                      # cổng xem (đọc số)
    key: str                      # mã dịch vụ của cổng xem
    fingerprint_url: str          # cổng trả vân tay của hộp chính
    fingerprint_key: str
    routes: list[dict]            # [{"segments": [...], "doan": {tên: regex}, "tham_so": {tên: regex}}]
    routes_sha8: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(MAX_PARALLEL))
    cached: tuple[float, dict] | None = None


def read_key_file(path: str | Path, name: str) -> str:
    """Đọc dòng `<name>=<mã>` trong file kiểu .env. File thiếu / quyền rộng hơn chủ sở hữu / không có dòng / mã rỗng ⇒ RuntimeError (lời KHÔNG chứa mã)."""
    path = Path(path).expanduser()
    try:
        mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise RuntimeError(f"không đọc được file mã {path.name}") from None
    if stat.S_IMODE(mode) & 0o077:
        raise RuntimeError(f"file mã {path.name} phải có quyền 600 (chỉ chủ sở hữu đọc)")
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name:
            value = value.strip().strip('"').strip("'")
            if value and not any(ch.isspace() for ch in value):
                return value
    raise RuntimeError(f"file mã {path.name} không có dòng {name}=<mã>")


def load_routes(path: Path = ROUTES_FILE) -> tuple[list[dict], str]:
    """Danh sách đường GET được chuyển tiếp + 8 ký tự đầu sha256 của file (in lúc bật để đối chiếu với bản bên Capitol ghim)."""
    raw = path.read_bytes()
    routes = []
    for entry in json.loads(raw)["duong"]:
        compiled = lambda patterns: {name: re.compile(pattern, re.ASCII) for name, pattern in (patterns or {}).items()}
        routes.append({"path": entry["path"], "segments": entry["path"].strip("/").split("/"), "doan": compiled(entry.get("doan")),
                       "tham_so": compiled(entry.get("tham_so"))})
    return routes, hashlib.sha256(raw).hexdigest()[:8]


def load_config() -> XemConfig | None:
    """None = không bật chế độ này (không đặt `KPI_XEM_URL`) ⇒ mọi thứ như cũ. Bật mà thiếu file mã / file đường ⇒ RuntimeError ⇒ tiến trình không bật."""
    url = os.getenv("KPI_XEM_URL", "").rstrip("/")
    if not url:
        return None
    home = Path(os.environ.get("CAPITOL_STATE_DIR") or (Path.home() / ".local" / "share" / "capitol-platform")).expanduser()
    routes, sha8 = load_routes()
    return XemConfig(url=url, key=read_key_file(os.getenv("KPI_XEM_MA_FILE", home / "xem_18403.env"), "XEM_MA"),
                     fingerprint_url=os.getenv("KPI_VAN_TAY_URL", "http://127.0.0.1:18402").rstrip("/"),
                     fingerprint_key=read_key_file(os.getenv("KPI_VAN_TAY_FILE", home / "van_tay_18402.env"), "VAN_TAY_MA"),
                     routes=routes, routes_sha8=sha8)


def client_headers(backend_url: str) -> dict[str, str] | None:
    """Header cho lời gọi của chính agent (ngữ cảnh lúc bật, công cụ) khi `backend_url` là cổng xem; None = không phải cổng xem (dùng mã phiên như cũ)."""
    url = os.getenv("KPI_XEM_URL", "").rstrip("/")
    if not url or backend_url.rstrip("/") != url:
        return None
    return {XEM_HEADER: read_key_file(os.getenv("KPI_XEM_MA_FILE", Path(os.environ.get("CAPITOL_STATE_DIR") or (Path.home() / ".local" / "share" / "capitol-platform")).expanduser() / "xem_18403.env"), "XEM_MA")}


def match(routes: list[dict], path: str, query: list[tuple[str, str]]) -> tuple[int, str | None, dict]:
    """(mã trạng thái, đường đã kiểm, tham số đã kiểm). 200 = cho qua · 404 = đường ngoài danh sách · 422 = đoạn đường / tham số sai dạng, lạ hoặc lặp.
    So bằng `fullmatch` + `re.ASCII` như cổng xem. Đường trả về ghép lại từ chính các đoạn ĐÃ khớp regex — không chuyển nguyên chuỗi trình duyệt gửi."""
    segments = path.strip("/").split("/")
    for route in routes:
        if len(route["segments"]) != len(segments):
            continue
        names = [part[1:-1] if part.startswith("{") else None for part in route["segments"]]
        if any(name is None and part != given for name, part, given in zip(names, route["segments"], segments)):
            continue
        if any(name is not None and not route["doan"][name].fullmatch(given) for name, given in zip(names, segments)):
            return 422, None, {}
        params: dict[str, str] = {}
        for key, value in query:
            if key not in route["tham_so"] or key in params or not route["tham_so"][key].fullmatch(value):
                return 422, None, {}
            params[key] = value
        return 200, "/" + "/".join(segments), params
    return 404, None, {}


async def _fingerprint_of(client: httpx.AsyncClient, url: str, header: str, key: str) -> dict:
    response = await client.get(f"{url}/van-tay", headers={header: key}, timeout=FINGERPRINT_TIMEOUT_S)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("tinh"), dict) or not isinstance(body.get("dong"), dict) or not body["tinh"] or not body["dong"]:
        raise ValueError("vân tay thiếu phần tĩnh / phần động")
    return body


def compare(view: dict, main: dict) -> dict:
    """So vân tay hộp xem với hộp chính ⇒ {khop, ly_do, loi, status}. Phần tĩnh phải bằng nhau; phần động so theo TẬP KHOÁ backend trả (không ghim
    số khoá — bên Capitol 12/11), trừ các trường chỉ để hiện. Hộp chính có bản dữ liệu tải lên mới hơn bản lúc bật ⇒ không khớp (hợp đồng bản 3)."""
    status = main["dong"].get("status")
    latest, booted = main["dong"].get("latest_data_version"), main["dong"].get("data_version")
    if latest is not None and booted is not None and latest != booted:
        return {"khop": False, "ly_do": "du_lieu_moi", "loi": NEW_DATA, "status": status}
    moving = lambda part: {key: value for key, value in part.items() if key not in NOT_COMPARED}
    if view["tinh"] != main["tinh"] or moving(view["dong"]) != moving(main["dong"]):
        return {"khop": False, "ly_do": "lech", "loi": UPDATING, "status": status}
    return {"khop": True, "ly_do": "khop", "loi": None, "status": status}


async def fingerprint_state(config: XemConfig, client: httpx.AsyncClient) -> dict:
    """Kết quả so vân tay (giữ ≤ 15 s). Lời gọi nào lỗi / hết giờ / khác 200 / thiếu trường ⇒ coi là LỆCH (không hiện số)."""
    async with config.lock:
        if config.cached:
            # Kết quả HỎI ĐƯỢC (khớp / lệch) giữ 15 s; kết quả "không hỏi được" chỉ giữ 2 s — nối thử hộp thật 13/11: một lần hỏi vân tay trục trặc
            # thoáng qua (máy nặng) bị giữ 15 s làm cả trang đóng số dù 20 s sau hai hộp vẫn khớp. Vẫn đóng khi nghi ngờ, chỉ không đóng lâu vì lỗi mạng.
            ttl = FINGERPRINT_ERROR_TTL_S if config.cached[1]["ly_do"] == "khong_hoi_duoc" else FINGERPRINT_TTL_S
            if time.monotonic() - config.cached[0] < ttl:
                return config.cached[1]
        state = None
        for _attempt in range(2):      # hỏi lại MỘT lần khi lỗi / hết giờ; lệch thật (hỏi được mà khác nhau) thì không hỏi lại
            try:
                view = await _fingerprint_of(client, config.url, XEM_HEADER, config.key)
                main = await _fingerprint_of(client, config.fingerprint_url, VAN_TAY_HEADER, config.fingerprint_key)
                state = compare(view, main)
                break
            except (httpx.HTTPError, ValueError):
                state = {"khop": False, "ly_do": "khong_hoi_duoc", "loi": UPDATING, "status": None}
        state = {**state, "luc_so": datetime.now().isoformat(timespec="seconds"), "duong_sha8": config.routes_sha8}
        config.cached = (time.monotonic(), state)
        return state


async def forward(config: XemConfig, client: httpx.AsyncClient, path: str, params: dict) -> tuple[int, bytes]:
    """Một GET tới cổng xem, tối đa `MAX_PARALLEL` lời gọi cùng lúc. Cổng báo đầy (429) ⇒ chờ rồi thử lại vài lần ⇒ 503. Chỉ trả JSON; thân khác ⇒ 502."""
    async with config.slots:
        for attempt in range(BUSY_RETRIES + 1):
            try:
                response = await client.get(f"{config.url}{path}", params=params, headers={XEM_HEADER: config.key}, timeout=CALL_TIMEOUT_S)
            except httpx.HTTPError:
                return 502, _refusal("cổng xem không trả lời")
            if response.status_code != 429:
                break
            if attempt < BUSY_RETRIES:
                await asyncio.sleep(BUSY_WAIT_S)
    if response.status_code == 429:
        return 503, _refusal("cổng xem đang bận — thử lại sau ít giây")
    if response.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return 502, _refusal("cổng xem trả thân không phải JSON")
    return response.status_code, response.content


def _refusal(reason: str) -> bytes:
    return json.dumps({"refused": reason}, ensure_ascii=False).encode("utf-8")
