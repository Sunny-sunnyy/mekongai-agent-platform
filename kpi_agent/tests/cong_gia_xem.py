"""CỔNG GIẢ cho chế độ "một nguồn số" — CHỈ để thử trang trên trình duyệt khi hộp 18403 / 18402 thật của bộ Capitol chưa bật.

KHÔNG phải cổng thật, KHÔNG phải bằng chứng chạy thật: nó chỉ bọc một backend KPI thử trên máy và trả `/van-tay` tự dựng. Không dùng cho bộ đang chạy
của người dùng. Hai vai, mỗi vai một tiến trình:
  CONG_GIA_VAI=xem   — đòi header X-Xem-Ma; GET /van-tay; GET /api/* chuyển nguyên tới CONG_GIA_BACKEND (backend thử, bật KPI_SESSION_KEY=off)
  CONG_GIA_VAI=chinh — đòi header X-Van-Tay-Ma; chỉ có GET /van-tay
Mã đọc từ file kiểu .env (CONG_GIA_MA_FILE, dòng XEM_MA= hoặc VAN_TAY_MA=). Dựng tình huống lệch: tạo file CONG_GIA_LECH_FILE ⇒ vân tay vai "xem"
đổi phần tĩnh; nội dung file là "moi" ⇒ vai "chinh" báo có bản dữ liệu tải lên mới. Xoá file ⇒ khớp lại.

Chạy (từ gốc repo):  CONG_GIA_VAI=xem CONG_GIA_MA_FILE=… CONG_GIA_BACKEND=http://127.0.0.1:<cổng> .venv/bin/python -m uvicorn kpi_agent.tests.cong_gia_xem:app --port <cổng>"""
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from kpi_agent.agent.xem_proxy import read_key_file

ROLE = os.getenv("CONG_GIA_VAI", "xem")
HEADER, NAME = ("x-xem-ma", "XEM_MA") if ROLE == "xem" else ("x-van-tay-ma", "VAN_TAY_MA")
BACKEND = os.getenv("CONG_GIA_BACKEND", "").rstrip("/")
SWITCH = Path(os.getenv("CONG_GIA_LECH_FILE", "/nonexistent"))
app = FastAPI(title=f"CỔNG GIẢ ({ROLE}) — chỉ để thử")


@app.middleware("http")
async def key_gate(request: Request, call_next):
    key = read_key_file(os.environ["CONG_GIA_MA_FILE"], NAME)
    return await call_next(request) if request.headers.get(HEADER) == key else JSONResponse({"detail": "Unauthorized"}, status_code=401)


@app.get("/van-tay")
def fingerprint() -> dict:
    mode = SWITCH.read_text(encoding="utf-8").strip() if SWITCH.exists() else ""
    static = {"kpi_head": "cong-gia", "che_do": "gia", "ds_sha256": "0" * 64, "du_lieu": {"RAW DATA.xlsx": "0" * 16}}
    moving = {"period": "2026-10", "cutoff": "2026-10-31", "files_sha256": {"a": "1"}, "rule_version": "gia", "workflow_version": "gia", "data_version": "v1"}
    if ROLE == "xem":
        return {"tinh": {**static, "kpi_head": "khac"} if mode and mode != "moi" else static, "dong": moving}
    return {"tinh": static, "dong": {**moving, "status": "tam_tinh", "latest_data_version": "v2" if mode == "moi" else "v1"}}


if ROLE == "xem":
    @app.get("/api/{rest:path}")
    async def data(request: Request, rest: str):
        async with httpx.AsyncClient() as client:
            upstream = await client.get(f"{BACKEND}/api/{rest}", params=list(request.query_params.multi_items()), timeout=60)
        return Response(content=upstream.content, status_code=upstream.status_code, media_type=upstream.headers.get("content-type", "application/json"))
