"""Agent service của trợ lý KPI (bậc 1 P2 — `docs/SPEC.md` mục 3, 5): POST /api/chat, lịch sử giữ ở server, công cụ gọi HTTP backend.

Vì sao lịch sử ở server (SPEC mục 3, Capitol "never from the payload"): nếu trình duyệt gửi cả lịch sử, ai cũng có thể chèn một câu trả lời
giả ("NV01 được 99 triệu") vào lượt trước. Ở đây trình duyệt chỉ gửi `conversation_id` + câu mới; khoá khác (vd "history") bị bỏ qua;
mã hội thoại server không biết ⇒ mở hội thoại MỚI với mã do server sinh (không nhận mã người gửi tự đặt).
- Ghi câu hỏi NGAY khi nhận rồi mới gọi mô hình (SPEC mục 3) ⇒ lỗi giữa chừng vẫn có dấu; ghi tiếp bản ghi đủ khi trả lời.
- Hạn cả request 90 s ⇒ câu cố định "quá thời gian". Giới hạn: luồng đang chạy không dừng được — mô hình có thể vẫn chạy (và tốn tiền) sau hạn.
- Bậc P2c (03/11, C rà bảo mật): chỉ nhận Host 127.0.0.1 / localhost; /docs, /openapi.json tắt (bật bằng KPI_API_DOCS=1);
  trần tiền mỗi ngày KPI_DAILY_USD_LIMIT (tính cả tiền đã ghi hôm nay trong bản ghi); bỏ ảnh / link http(s) khỏi câu trả lời trước khi trả.
- Mức suy luận `medium` = mức đã đo đạt (KET_QUA mục 2e, 2f). Lời dặn, mô tả công cụ, rào câu gốc, kiểm số: dùng nguyên `chat.py`.
Chạy (từ gốc repo, chỉ máy này; backend phải đang chạy):
  kpi_agent/scripts/with_env.sh .venv/bin/uvicorn kpi_agent.agent.server:app --host 127.0.0.1 --port 8120
"""
import asyncio
import httpx
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field

from kpi_agent.agent.backend_client import load_context, provenance
from kpi_agent import session_key
from kpi_agent.agent.chat import CANNOT_ANSWER, MODEL, answer_question, config_version, context_numbers, make_model
from kpi_agent.agent.number_check import trace_numbers
from kpi_agent.agent.presentation import present_record
from kpi_agent.agent.result_view import result_view
from kpi_agent.agent import xem_proxy

REPO_ROOT = Path(__file__).resolve().parents[2]
# Chế độ "một nguồn số" (12/11): đặt KPI_XEM_URL ⇒ agent đọc số từ cổng xem của hộp KPI trong Capitol thay cho backend trên máy (agent/xem_proxy.py)
BACKEND_URL = os.getenv("KPI_XEM_URL", "").rstrip("/") or os.getenv("KPI_BACKEND_URL", "http://127.0.0.1:8110")
REASONING = "medium"
REQUEST_DEADLINE_S = 90
# Bản ghi chat có mã che + số thật của đội ⇒ để trong kpi_agent/data/ (Git bỏ qua)
LOG_DIR = REPO_ROOT / "kpi_agent/data/chat_logs"
# Trần tiền mỗi ngày (C rà bảo mật 03/11): một vòng lặp gọi API lỗi / người gửi liên tục không đốt quá mức này. 0,10 USD ≈ 200 câu medium
DAILY_USD_LIMIT = float(os.getenv("KPI_DAILY_USD_LIMIT", "0.10"))
BUDGET_REACHED = "Xin lỗi, trợ lý đã dùng hết hạn mức gọi mô hình của hôm nay. Bảng thưởng và thẻ nhân viên trên trang vẫn xem được."


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")   # "history", "messages"… trình duyệt gửi kèm ⇒ bỏ qua
    conversation_id: str | None = None
    question: str = Field(min_length=1, max_length=2000)


def _log(entry: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"at": datetime.now().isoformat(timespec="seconds"), **entry}
    with (LOG_DIR / f"{datetime.now():%Y%m%d}.jsonl").open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def _load_context_and_model(app: FastAPI) -> None:
    """Nạp ngữ cảnh (kỳ, đội, tên KPI) từ nguồn số rồi dựng mô hình theo đó; lỗi mạng / nguồn từ chối ⇒ httpx.HTTPError, không đổi trạng thái."""
    ctx = load_context(BACKEND_URL)
    app.state.ctx, app.state.model = ctx, make_model(ctx.kpis, REASONING, names=bool(ctx.name_entries))
    _log({"event": "started", **config_version(ctx, REASONING),
          **({"xem_duong_sha8": app.state.xem.routes_sha8, "xem_so_duong": len(app.state.xem.routes)} if app.state.xem else {})})


def _source_ready(app: FastAPI) -> bool:
    """Chưa có ngữ cảnh (cổng xem không trả lời lúc bật) ⇒ thử nạp lại; vẫn không được ⇒ False."""
    if app.state.ctx is None:
        try:
            _load_context_and_model(app)
        except httpx.HTTPError:
            return False
    return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Mã phiên: agent đòi CÙNG mã với backend ở mọi lời gọi (POST /api/chat tốn tiền mô hình); không có mã ⇒ không bật
    app.state.session_key = session_key.load_server_key()
    # "Một nguồn số": None = không bật (như cũ). Bật mà thiếu file mã dịch vụ / file danh sách đường ⇒ lỗi ở đây ⇒ agent không bật
    app.state.xem = xem_proxy.load_config()
    app.state.xem_client = httpx.AsyncClient() if app.state.xem else None
    app.state.conversations = {}   # conversation_id ⇒ [(câu hỏi, câu trả lời)] — chỉ trong bộ nhớ tiến trình (bậc 1)
    app.state.ctx = app.state.model = None
    try:
        _load_context_and_model(app)
    except httpx.HTTPError:
        # Nối thử hộp Capitol thật 13/11: cổng xem chưa trả lời lúc bật ⇒ agent thoát với một dòng lỗi kết nối, trang trắng. Ở chế độ "một nguồn số"
        # giờ VẪN khởi động: mọi đường số tự đóng (so vân tay không hỏi được ⇒ 409), câu hỏi bị từ chối, và ngữ cảnh nạp lại ở câu hỏi đầu tiên sau
        # khi cổng lên. Chế độ thường (backend trên máy) giữ như cũ: không có backend ⇒ không bật.
        if app.state.xem is None:
            raise
        _log({"event": "started_without_source", "xem_duong_sha8": app.state.xem.routes_sha8})
    yield
    if app.state.xem_client is not None:
        await app.state.xem_client.aclose()


_docs = os.getenv("KPI_API_DOCS") == "1"
app = FastAPI(title="Trợ lý KPI — agent", lifespan=lifespan,
              docs_url="/docs" if _docs else None, redoc_url=None, openapi_url="/openapi.json" if _docs else None)
# Host lạ ⇒ 400: chặn trang web lạ trong trình duyệt gọi vào cổng máy này qua DNS-rebinding (C rà bảo mật 03/11, R2)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

# Bản 2 tab "Theo dõi" (như cổng 18400 bên Capitol): trang web lạ trong trình duyệt gửi được POST "form" / "text/plain" tới cổng
# máy này mà KHÔNG cần CORS (yêu cầu "đơn giản") ⇒ tốn tiền mô hình. Chặn: (1) chỉ nhận JSON — trình duyệt muốn gửi JSON sang trang khác phải hỏi
# trước (preflight) và server không trả lời CORS ⇒ bị chặn; (2) có Origin mà không phải trang của mình ⇒ 403. Không có Origin (bộ đo, curl) ⇒ như cũ.
ALLOWED_ORIGINS = {origin.strip() for origin in os.getenv("KPI_ALLOWED_ORIGINS", "http://127.0.0.1:5183,http://localhost:5183").split(",")
                   if origin.strip()}


@app.middleware("http")
async def only_json_from_own_page(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/api/chat":
        origin = request.headers.get("origin")
        if origin is not None and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": "nguồn gửi không được phép (Origin)"}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return JSONResponse({"detail": "chỉ nhận application/json"}, status_code=415)
    return await call_next(request)


# Rào mã phiên — đăng ký SAU CÙNG ⇒ chạy NGOÀI CÙNG: thiếu / sai mã ⇒ 401 trước khi đọc thân, trước rào Origin, KHÔNG gọi mô hình, không ghi sổ chat
@app.middleware("http")
async def session_gate(request: Request, call_next):
    return session_key.unauthorized(request.headers, request.app.state.session_key) or await call_next(request)


def _spent_today() -> float:
    path = LOG_DIR / f"{datetime.now():%Y%m%d}.jsonl"
    if not path.exists():
        return 0.0
    rows = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return sum(row.get("usd") or 0.0 for row in rows if row.get("event") == "answered")


# Tên lời thường của 12 công cụ cho khối "Dấu vết lượt hỏi" (bản 2, 11/11 — hướng AI FDE "nhìn thấy được mọi bước")
TOOL_LABELS = {"get_team_bonus_table": "Bảng thưởng cả đội", "get_employee_result": "Kết quả một nhân viên", "get_mid_month_gap": "Còn thiếu tới mốc kế",
               "explain_kpi": "Giải thích cách tính một KPI", "get_opportunities": "Cơ hội lên mốc", "get_data_provenance": "Nguồn số, kỳ, bản luật",
               "simulate_kpi_change": "Thử nếu số đổi", "get_team_kpi_summary": "Tóm tắt một KPI cả đội", "compare_employees": "So hai nhân viên",
               "get_data_issues": "Vấn đề dữ liệu", "get_extra_bonus": "Thưởng thêm", "get_spice_loyalty": "1 % gia vị (tiền trả khách)"}
SUMMARY_KEYS = ("amount", "total", "salary", "actual", "pct", "paid_count", "gap", "count")
SUMMARY_MAX = 8


def _row_name(item, index: int) -> str:
    """Tên một dòng trong danh sách kết quả công cụ: khoá "kpi" / "item" / "employee_id" nếu có (đều là mã KPI / khoản / mã che), không thì số thứ tự."""
    if isinstance(item, dict):
        for key in ("kpi", "item", "employee_id"):
            if isinstance(item.get(key), str):
                return item[key]
    return str(index)


def _result_summary(result) -> list[str]:
    """Tối đa SUMMARY_MAX ô số đáng xem của một kết quả công cụ ("extra_bonus_total = 950000") — kết quả backend đã che mã; không trả nguyên kết quả.
    Đi theo TẦNG (ô ngoài cùng trước): tổng / tiền của cả câu đứng trước các dòng chi tiết (lượt thật 11/11: duyệt sâu trước ⇒ 8 ô đầu toàn dòng 0 đ)."""
    found, queue = [], [("", result.get("result", result) if isinstance(result, dict) else result)]
    while queue and len(found) < SUMMARY_MAX:
        path, value = queue.pop(0)
        if isinstance(value, dict):
            queue += [(f"{path}.{key}" if path else key, item) for key, item in value.items()]
        elif isinstance(value, list):
            # Dòng có tên (KPI / khoản / mã che NV) ⇒ ghi tên thay số thứ tự: "kpis[Turnover].actual", không "kpis[0].actual"
            queue += [(f"{path}[{_row_name(item, index)}]", item) for index, item in enumerate(value[:3])]
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and any(word in path.rsplit(".", 1)[-1] for word in SUMMARY_KEYS):
            found.append(f"{path} = {value}")
    return found


def _trace(record: dict) -> dict:
    """Mọi bước của lượt hỏi cho người xem (khối gập dưới câu trả lời): công cụ + tham số + tóm tắt kết quả / lời từ chối, rào kiểm số
    lần đầu / làm lại / lần cuối, số lần gọi mô hình, token, tiền, giây. Chỉ lấy từ bản ghi `chat.answer_question` — không tính gì mới."""
    def check(result):
        return None if result is None else {"ok": result.get("ok", True), "problems": result.get("unsourced") or result.get("problems") or []}
    calls = [{"name": call["name"], "label": TOOL_LABELS.get(call["name"], call["name"]),
              "params_used": result.get("params_used", call["args"]) if isinstance(result, dict) else call["args"],
              "refused": result.get("refused") if isinstance(result, dict) else None,
              "summary": [] if isinstance(result, dict) and result.get("refused") else _result_summary(result),
              # kết quả dạng ô đơn + bảng để trang vẽ (12/11) — sắp lại đúng kết quả đã gửi mô hình, có trần dòng / cột (agent/result_view.py)
              "view": None if isinstance(result, dict) and result.get("refused") else result_view(result)}
             for call, result in zip(record["tool_calls"], record["tool_results"])]
    return {"tool_calls": calls, "requested_not_run": record.get("requested_tool_calls", []),
            "number_check_first": check(record.get("number_check_first")), "number_check_final": check(record.get("number_check_final")),
            "owner_check_first": check(record.get("owner_check_first")), "owner_check_final": check(record.get("owner_check_final")),
            "retried_for_numbers": record.get("retried_for_numbers", False), "input_guard": record.get("input_guard"),
            "model_calls": record.get("model_calls", 0), "input_tokens": record.get("input_tokens", 0), "output_tokens": record.get("output_tokens", 0),
            "usd": record["usd"], "seconds": record["seconds"], "stop": record["stop"],
            # tên mô hình + mức suy nghĩ của tiến trình này (Dấu vết trước đây không nói mô hình nào trả lời)
            "model": MODEL, "reasoning": REASONING}


def _response(conversation_id: str, record: dict, source: dict | None) -> dict:
    calls = [{"name": call["name"], "params_used": result.get("params_used", call["args"]), "refused": call["refused"]}
             for call, result in zip(record["tool_calls"], record["tool_results"])]
    warnings = []
    if not record["number_check_final"]["ok"]:
        warnings.append("có số chưa đối chiếu được với kết quả tính: " + ", ".join(record["number_check_final"]["unsourced"]))
    owner = record.get("owner_check_final") or {"ok": True, "problems": []}
    if not owner["ok"]:
        warnings.append("có chỗ chưa khớp dữ liệu: " + "; ".join(owner["problems"]))
    # "out_of_scope" (rào việc ngoài KPI): câu từ chối cố định của code là câu trả lời trọn vẹn, như "input_refused"
    if record["stop"] not in ("answered", "input_refused", "out_of_scope"):
        warnings.append(f"không trả lời trọn vẹn ({record['stop']})")
    if source is None:
        warnings.append("không lấy được nguồn số (kỳ, bản luật) từ backend — đừng dùng số trong câu này khi chưa kiểm")
    if record.get("links_removed"):
        warnings.append(f"đã bỏ {record['links_removed']} ảnh / link ra ngoài khỏi câu trả lời")
    # Dò nguồn trên lời của mô hình (trước khi code gắn cảnh báo — dòng đó nhắc lại số lạ ⇒ bảng nguồn bị lặp, E thấy bằng test 03/11)
    sources = trace_numbers(record["model_text"], [(c["name"], r) for c, r in zip(record["tool_calls"], record["tool_results"])],
                            record.get("question", ""))
    return {"conversation_id": conversation_id, "answer": record["answer"], "stop": record["stop"], "tool_calls": calls,
            "number_sources": sources, "warnings": warnings, "usd": record["usd"], "seconds": record["seconds"],
            # Tính năng 4: kỳ · số chụp · ngày chốt · bản luật · trạng thái — code điền ở MỌI phản hồi, không phải lời mô hình
            "provenance": source, "trace": _trace(record)}


def _fixed_record(question: str, answer: str, stop: str, usd, seconds) -> dict:
    """Câu do code viết (hết hạn mức, quá giờ) — không gọi công cụ, không số nào."""
    return {"question": question, "answer": answer, "model_text": answer, "stop": stop, "tool_calls": [], "tool_results": [],
            "number_check_final": {"ok": True, "unsourced": []}, "usd": usd, "seconds": seconds}


@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request) -> dict:
    state = request.app.state
    if not await run_in_threadpool(_source_ready, request.app):
        # nguồn số chưa trả lời từ lúc bật ⇒ không gọi mô hình, cùng lời với các đường số đang đóng
        return JSONResponse({"refused": xem_proxy.UPDATING}, status_code=409)
    known = body.conversation_id in state.conversations
    conversation_id = body.conversation_id if known else uuid.uuid4().hex
    turns = state.conversations.setdefault(conversation_id, [])
    _log({"event": "received", "conversation_id": conversation_id, "question": body.question,
          "unknown_conversation_id_sent": None if known or body.conversation_id is None else body.conversation_id[:64]})
    # Khi nạp lại chỉ giữ câu người dùng + câu trả lời (SPEC mục 3) — không giữ kết quả công cụ cũ
    history = [message for question, answer in turns for message in (HumanMessage(question), AIMessage(answer))]
    # Đọc nguồn MỘT lần TRƯỚC khi gọi mô hình (TN4, minor 2): backend bật lại giữa chừng thì nguồn vẫn là bản đã tính
    source = await run_in_threadpool(provenance, state.ctx)
    # Bậc 2a: dòng ngữ cảnh lời dặn dùng nhãn trạng thái MỚI NHẤT (sau "Đã soát") — không để lời dặn nói "tạm tính" khi công cụ nói "đã soát"
    ctx = replace(state.ctx, status_label=source.get("status_label_for_model", state.ctx.status_label)) if source else state.ctx
    xem_state = await xem_proxy.fingerprint_state(state.xem, state.xem_client) if state.xem else None
    if xem_state is not None and not xem_state["khop"]:
        # "Một nguồn số": hộp xem lệch hộp chính ⇒ không gọi mô hình, không tra công cụ (cùng luật với trang: không hiện số khi nghi ngờ)
        record = _fixed_record(body.question, CANNOT_ANSWER.format(reason=xem_state["loi"]), "source_not_in_sync", usd=0.0, seconds=0.0)
    elif _spent_today() >= DAILY_USD_LIMIT:
        record = _fixed_record(body.question, BUDGET_REACHED, "daily_budget_reached", usd=0.0, seconds=0.0)
    else:
        try:
            record = await asyncio.wait_for(run_in_threadpool(answer_question, state.model, ctx, body.question, history),
                                            REQUEST_DEADLINE_S)
        except asyncio.TimeoutError:
            record = _fixed_record(body.question, CANNOT_ANSWER.format(reason="quá thời gian chờ"), "request_timeout",
                                   usd=None, seconds=REQUEST_DEADLINE_S)
    # Trình bày bằng code (bỏ ảnh / link / markdown, số kiểu Việt) trước khi trả VÀ trước khi vào lịch sử — cùng hàm bộ đo dùng
    record = present_record(record, context_numbers(ctx))
    turns.append((body.question, record["answer"]))
    _log({"event": "answered", "conversation_id": conversation_id, **record, "provenance": source})
    return _response(conversation_id, record, source)


# "Một nguồn số" (agent/xem_proxy.py): trang gọi /api/* vào ĐÂY, agent chuyển tiếp GET tới cổng xem của hộp KPI trong Capitol ----------
# Hai đường dưới LUÔN được đăng ký nhưng khi không bật KPI_XEM_URL thì trả 404 y như đường không tồn tại (hành vi cũ). Rào mã phiên vẫn ở ngoài cùng.
NOT_FOUND = {"detail": "Not Found"}


@app.get("/api/xem/trang-thai")
async def xem_status(request: Request):
    """Hộp xem có khớp hộp chính không (KHÔNG có số nào) — để trang hiện một khung lý do khi không hiện số."""
    state = request.app.state
    if state.xem is None:
        return JSONResponse(NOT_FOUND, status_code=404)
    return await xem_proxy.fingerprint_state(state.xem, state.xem_client)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def xem_forward(request: Request, rest: str):
    state = request.app.state
    if state.xem is None:
        # không bật ⇒ như trước khi có đường này: đường lạ 404; GET /api/chat (đường có thật, sai phương thức) vẫn 405
        return JSONResponse({"detail": "Method Not Allowed"}, status_code=405) if rest == "chat" else JSONResponse(NOT_FOUND, status_code=404)
    if request.method != "GET":
        return JSONResponse({"detail": "Method Not Allowed"}, status_code=405)   # chế độ này chỉ đọc: soát / tải lên / sửa workflow làm ở xưởng
    if b"%" in request.scope.get("raw_path", b""):
        return JSONResponse({"refused": "đường dẫn không hợp lệ"}, status_code=422)   # đoạn mã hoá (%2F, %2e…) ⇒ từ chối sớm, không đoán nghĩa
    fingerprint = await xem_proxy.fingerprint_state(state.xem, state.xem_client)
    if not fingerprint["khop"]:
        return JSONResponse({"refused": fingerprint["loi"]}, status_code=409)     # một luật cho MỌI đường qua đây: lệch ⇒ không số nào
    status, path, params = xem_proxy.match(state.xem.routes, f"/api/{rest}", list(request.query_params.multi_items()))
    if status == 404:
        return JSONResponse(NOT_FOUND, status_code=404)
    if status != 200:
        return JSONResponse({"refused": "tham số không hợp lệ"}, status_code=422)
    code, body = await xem_proxy.forward(state.xem, state.xem_client, path, params)
    return Response(content=body, status_code=code, media_type="application/json")

