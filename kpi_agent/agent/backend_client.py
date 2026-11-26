"""Agent nói chuyện với backend CHỈ qua HTTP (bậc 1, `docs/SPEC.md` mục 3, 5) — agent không import `core/` hay `backend/`.

Vì sao: luật + quyền nằm ở backend (FDE 2.2); nếu agent gọi thẳng hàm lõi thì một lỗi ở agent có thể đi vòng qua bước kiểm tham số.
Ở đây agent chỉ biết URL + các endpoint GET; mã NV, KPI, kỳ do backend kiểm và từ chối bằng lời (`{"refused": …}`).
"""
import json
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote

import httpx

from kpi_agent import session_key   # module dùng chung (không thuộc backend/ hay core/) — agent vẫn không import code tính số
from kpi_agent.agent import names as agent_names
from kpi_agent.agent import xem_proxy
from kpi_agent.agent.owner_check import with_team_sentences

BACKEND_TIMEOUT_S = 10   # backend đọc ngữ cảnh đã tính sẵn trong bộ nhớ — một lần gọi thường < 0,1 s


@dataclass
class AgentContext:
    """Những gì vòng chat cần biết, lấy từ GET /api/meta lúc bật (không tự tính gì)."""
    backend_url: str
    period: str
    cutoff: date
    employee_ids: list[str]
    kpis: list[str]
    data_flags: list[str]
    rule_version: str = ""     # bậc 4b: đưa vào dòng ngữ cảnh lời dặn (câu không gọi công cụ cũng thấy — dev 4a câu D3)
    status_label: str = ""
    # Bậc "tên vào trợ lý" (KPI_NAMES=on): {mã che: {"name", "sm_code"}} lấy từ GET /api/names của backend lúc bật; {} = cờ off ⇒ chỉ mã che như cũ
    name_entries: dict = field(default_factory=dict)


def context_numbers(ctx: AgentContext) -> list[str]:
    """Đúng các chuỗi số code điền vào dòng ngữ cảnh (`system_prompt`) — nguồn số hợp lệ cho mọi câu."""
    # + nhãn trạng thái (bậc 2a: "đã soát lúc 03/11/2026 14:05" — mô hình chép giờ soát thì có nguồn)
    return [ctx.period, f"{ctx.cutoff:%d/%m/%Y}", ctx.cutoff.isoformat(), str(len(ctx.employee_ids)), ctx.status_label]


def backend_get(backend_url: str, path: str, query: dict | None = None) -> httpx.Response:
    """MỘT chỗ gọi backend: gắn header mã phiên (`kpi_agent/session_key.py`) — mọi GET của agent / script đo đi qua đây.
    Đọc file mã mỗi lần gọi (file nhỏ): backend bật lại với mã mới thì agent bật lại cùng script, không giữ mã cũ trong biến toàn cục.
    """
    # Chế độ "một nguồn số" (12/11): `backend_url` là cổng xem của hộp KPI trong Capitol ⇒ gắn mã dịch vụ của cổng đó thay mã phiên, và chờ lâu hơn
    # (cổng xem có đường tính lần đầu 33 s). Không ở chế độ đó ⇒ y như cũ.
    xem_headers = xem_proxy.client_headers(backend_url)
    if xem_headers is not None:
        return httpx.get(f"{backend_url}{path}", params=query or {}, timeout=xem_proxy.CALL_TIMEOUT_S, headers=xem_headers)
    return httpx.get(f"{backend_url}{path}", params=query or {}, timeout=BACKEND_TIMEOUT_S, headers=session_key.client_headers(backend_url))


def load_context(backend_url: str) -> AgentContext:
    meta = backend_get(backend_url, "/api/meta").raise_for_status().json()
    # KPI_NAMES=on: bảng tên lấy MỘT lần lúc bật; backend không trả được (409 thiếu tên, 404 khi KPI_SYSTEM_ONLY, 401 thiếu mã phiên) ⇒ lỗi ⇒ agent
    # không bật (đóng khi lỗi — không chạy nửa tên nửa mã). Bảng tên phải đúng đội của /api/meta. Gọi qua backend_get ⇒ mang mã phiên như mọi GET khác.
    name_entries = {}
    if agent_names.names_enabled():
        name_entries = backend_get(backend_url, "/api/names").raise_for_status().json()["names"]
        if sorted(name_entries) != sorted(meta["employee_ids"]):
            raise ValueError("KPI_NAMES=on nhưng bảng tên của backend không khớp đội")
    return AgentContext(backend_url, meta["period"], date.fromisoformat(meta["cutoff"]), meta["employee_ids"], meta["kpis"],
                        # mức tra cứu vòng 3 (B minor m1): bản cho mô hình — không cờ của riêng một người (trang dùng meta["data_flags"] đủ)
                        meta["data_flags_for_model"], meta.get("rule_version", ""),
                        meta.get("status_label_for_model", meta.get("status_label", "")), name_entries)

# Bậc 2a: + nhãn trạng thái bản KHÔNG tên người soát (C S3) — trạng thái đổi khi giám sát bấm "Đã soát" mà agent không bật lại
PROVENANCE_FIELDS = ("period", "snapshot_at", "cutoff", "rule_version", "status", "status_label_for_model")


def provenance(ctx: AgentContext) -> dict | None:
    """Tính năng 4: "nguồn của câu trả lời" do CODE điền — đọc /api/meta MỖI câu (backend bật lại với file luật khác thì biết ngay).
    Backend không trả lời ⇒ None (server gắn cảnh báo, không bịa nguồn)."""
    try:
        meta = backend_get(ctx.backend_url, "/api/meta").raise_for_status().json()
        return {field: meta[field] for field in PROVENANCE_FIELDS}
    except (httpx.HTTPError, ValueError, KeyError):
        return None

# Tên tham số mỗi công cụ nhận (đúng mô tả công cụ gửi mô hình). Tham số lạ ⇒ từ chối ở đây, không gửi đi (trước 03/11: TypeError của hàm)
TOOL_PARAMS = {"get_team_bonus_table": {"period"}, "get_employee_result": {"employee_id", "period"},
               "get_mid_month_gap": {"employee_id", "period"}, "explain_kpi": {"employee_id", "kpi", "period"},
               # 04/11: công cụ đọc endpoint có sẵn.
               # get_team_board ĐÃ BỎ (giữa 11:31 và 11:40): chồng vai get_team_board ↔ get_team_bonus_table làm mô hình chọn dao động,
               # bản gọt thiếu "ai chưa có tiền theo KPI" ⇒ câu 8 hồi quy không trả lời được.
               # /api/team-board vẫn còn cho trang; câu cấp đội về get_team_bonus_table.
               "get_opportunities": {"period"}, "get_data_provenance": set(),
               "simulate_kpi_change": {"employee_id", "kpi", "extra", "target_pct", "period"},   # A-ii-1: target_pct cạnh extra
               # Nhóm A bậc mức tra cứu: A1, A3, — A2 mở rộng get_team_bonus_table
               "get_team_kpi_summary": {"kpi", "period"}, "compare_employees": {"employee_a", "employee_b", "kpi", "period"},
               "get_data_issues": {"employee_id", "period"}}
# Công cụ CHỈ qua backend (cần app — đọc lại file RAW), khối agent của dây chuyền tự viết không có: TÁCH khỏi TOOL_PARAMS vì `workflow/blocks.py`
# lấy danh sách khối nối được từ TOOL_PARAMS, mà sửa `workflow/` đổi dấu vân tay bản tính (bậc (b) bản 2,).
# get_extra_bonus: Thưởng thêm 6 khoản ngoài 8 KPI — chỉ đường RAW; đường Tracking ⇒ backend từ chối
# get_spice_loyalty (12/11): 1 % gia vị — tiền trả KHÁCH, gom theo nhân viên phụ trách (backend/spice_tool.py); chỉ đường RAW
BACKEND_TOOL_PARAMS = {"get_extra_bonus": {"employee_id", "period"}, "get_spice_loyalty": {"employee_id", "period"},
                       # Công cụ tính (17/11, backend/calc_tool.py): số vào là THAM CHIẾU tới kết quả công cụ đọc — backend chạy lại nguồn, không nhận số gõ
                       "calc_sum": {"values"}, "calc_difference": {"a", "b"}, "calc_percent_of": {"part", "whole"},
                       "calc_group_total": {"table", "value_field", "group_by", "groups"}, "calc_period_change": {"before", "after"}}
ALL_TOOL_PARAMS = {**TOOL_PARAMS, **BACKEND_TOOL_PARAMS}   # chat + Capitol (`/api/tool/{name}`) nhận đủ
# Trường của bản tính đưa cho mô hình (`get_data_provenance`) — KHÔNG có `reviewed_by`, `status_label`, `events` (có tên người soát:
# bản gửi mô hình không chép tên — 03/11 S3), không có 112 dòng tiền (đã có ở công cụ khác)
RUN_FIELDS_FOR_MODEL = ("run_id", "created_at", "period", "rule_version", "data_snapshot", "team_total", "status",
                        "status_label_for_model", "reviewed_at", "replaces", "replaced_by")
META_FIELDS_FOR_MODEL = ("period", "cutoff", "snapshot_at", "rule_version", "status", "status_label_for_model", "workflow_version",
                         "current_run_id")


def _drop_zero_partner(args: dict) -> dict:
    """simulate_kpi_change: mô hình hay điền 0 vào ô KHÔNG dùng (T3 câu 12–15 lượt `20261109_160950_a_ii1_…` 09/11: extra 200 + target_pct 0 ⇒
    bị từ chối cả 4). Cả hai ô có số mà đúng MỘT ô bằng 0 ⇒ bỏ ô đó (target_pct 0 vốn không hợp lệ; "thêm 0" không phải câu hỏi khi đã nói %).
    Backend vẫn giữ hợp đồng chặt "đúng một trong hai" — sửa ở đây vì đây là chỗ nhận tham số của mô hình."""
    def is_zero(value) -> bool:
        try:
            return not isinstance(value, bool) and float(value) == 0
        except (TypeError, ValueError):
            return False
    given = [key for key in ("extra", "target_pct") if args.get(key) not in (None, "")]
    zero = [key for key in given if is_zero(args[key])]
    if len(given) == 2 and len(zero) == 1:
        return {key: value for key, value in args.items() if key != zero[0]}
    return args


def _segment(value) -> str:
    # quote(safe="") ⇒ "/" thành %2F: chuỗi "NV01/../NV02" tới backend nguyên vẹn để bị KIỂM + từ chối, không bị httpx rút gọn "/../"
    return quote(str(value), safe="")


def call_tool(ctx: AgentContext, name: str, args: dict) -> dict:
    """Gọi một công cụ = một GET tới backend. Lỗi mạng / backend ⇒ {"error": …} (không bịa số, không ném lỗi vào vòng chat).
    KPI_NAMES=on (`ctx.name_entries` có bảng): tên / 2 chữ cuối / mã SM ở tham số nhân viên ⇒ mã che TRƯỚC mọi luật (backend chỉ biết mã che);
    kết quả thêm `name` / `sm_code` cạnh mỗi mã che — cùng hai hàm với cửa `/api/tool/*` của backend (`agent/names.py`)."""
    # Hợp đồng cũ (test_what_if_target_pct, A-ii-1): các phép kiểm tham số KHÔNG cần ngữ cảnh (tên công cụ, tham số lạ / thiếu, "đúng một trong hai")
    # chạy TRƯỚC mọi thứ đọc `ctx`. Bậc tên bản đầu đọc `ctx.name_entries` ngay dòng đầu ⇒ `call_tool(None, …)` với tham số sai ném AttributeError
    # thay vì trả lời từ chối (pytest đủ ở mốc gộp 12/11 bắt). Thứ tự: bỏ ô 0 thừa ⇒ kiểm không ngữ cảnh ⇒ đổi tên sang mã ⇒ phần còn lại.
    if name == "simulate_kpi_change":
        args = _drop_zero_partner(args)
    refusal = args_refusal_without_context(name, args)
    if refusal:
        return refusal
    if not ctx.name_entries:
        return _call_tool_masked(ctx, name, args)
    args, unclear = agent_names.resolve_args(args, ctx.name_entries)
    return unclear or agent_names.with_names(_call_tool_masked(ctx, name, args), ctx.name_entries)


def _call_tool_masked(ctx: AgentContext, name: str, args: dict) -> dict:
    """Lần gọi với tham số nhân viên đã là mã che (đường cũ, nguyên vẹn)."""
    if name == "simulate_kpi_change":
        # A-ii-1: bỏ ô 0 thừa TRƯỚC các luật (như bản A-ii-1: bước này chỉ xoá khoá extra / target_pct ⇒ không đổi kết quả luật tên / tham số lạ / thiếu)
        args = _drop_zero_partner(args)
    refusal = check_tool_args(ctx, name, args)
    if refusal:
        return refusal
    if name.startswith("calc_"):
        # tham chiếu lồng nhau ⇒ cả bảng tham số đi qua MỘT ô query dạng JSON; backend tự chạy lại công cụ nguồn (calc_tool.py)
        return _get_json(ctx, f"/api/calc/{_segment(name)}", {"args": json.dumps(args, ensure_ascii=False)})
    query = {"period": args["period"]} if args.get("period") not in (None, "") else {}
    if name == "get_data_provenance":
        return _data_provenance(ctx)
    if name == "get_opportunities":
        return _get_json(ctx, "/api/opportunities", query, _only_opportunities)
    if name == "simulate_kpi_change":
        # backend kiểm lại MỌI tham số (`backend/what_if.py`) — ở đây chỉ chuyển; extra đi qua tham số truy vấn (chữ), backend đọc thành số
        return _get_json(ctx, f"/api/what-if/{_segment(args['employee_id'])}/{_segment(args['kpi'])}", {**query, **{key: str(args[key]) for key in ("extra", "target_pct") if args.get(key) not in (None, "")}})
    if name in ("get_team_kpi_summary", "compare_employees", "get_data_issues", "get_extra_bonus", "get_spice_loyalty"):
        # nhóm A + bậc (b): mọi tham số đi qua query (backend kiểm + từ chối) — tham số trống không gửi
        path = {"get_team_kpi_summary": "/api/team-kpi", "compare_employees": "/api/compare", "get_data_issues": "/api/data-issues",
                "get_extra_bonus": "/api/extra-bonus", "get_spice_loyalty": "/api/spice-tool"}[name]
        # get_team_kpi_summary: + câu chuẩn cả đội (owner_check.with_team_sentences — mô hình chép nguyên, bậc (a) 11/11)
        return with_team_sentences(name, _get_json(ctx, path, {**query, **{key: str(value) for key, value in args.items() if key != "period" and value not in (None, "")}}))
    if name == "get_team_bonus_table":
        path = "/api/team-bonus"
    elif name == "get_employee_result":
        path = f"/api/employees/{_segment(args['employee_id'])}"
    elif name == "get_mid_month_gap":
        path = "/api/gap"
        if args.get("employee_id") not in (None, ""):
            query["employee_id"] = args["employee_id"]
    else:
        path = f"/api/explain/{_segment(args['employee_id'])}/{_segment(args['kpi'])}"
    return with_team_sentences(name, _get_json(ctx, path, query))   # get_team_bonus_table: + câu chuẩn cả đội


def check_tool_args(ctx: AgentContext, name: str, args: dict) -> dict | None:
    """Lời từ chối của lớp agent cho một lần gọi công cụ (tên lạ, tham số lạ / thiếu, KPI không có) — None = cho gọi.
    Tách khỏi `call_tool` (09/11) để khối agent trong workflow (gọi thẳng hàm backend, không qua HTTP) đi qua ĐÚNG các luật này."""
    refusal = args_refusal_without_context(name, args)
    if refusal:
        return refusal
    # KPI ngoài danh sách /api/meta ⇒ từ chối ở đây: "../PC_BT" gửi đi sẽ bị route {employee_id:path} tách nhầm thành mã NV "…/.."
    # ⇒ vẫn bị từ chối nhưng lời từ chối nêu nhầm tham số (P2, minor 1). Backend vẫn kiểm lại (lớp chính).
    checks_kpi = name in ("explain_kpi", "simulate_kpi_change", "get_team_kpi_summary") or (name == "compare_employees" and args.get("kpi") not in (None, ""))
    if checks_kpi and str(args["kpi"]).strip().lower() not in {kpi.lower() for kpi in ctx.kpis}:
        kpi_text = str(args["kpi"]) if len(str(args["kpi"])) <= 40 else str(args["kpi"])[:40] + "…"
        return {"refused": f"KPI {kpi_text!r} không có — chỉ có {', '.join(ctx.kpis)}", "params_used": {"kpi": kpi_text}}
    return None


def args_refusal_without_context(name: str, args: dict) -> dict | None:
    """Phần của `check_tool_args` KHÔNG đọc ngữ cảnh: tên công cụ lạ, tham số lạ / thiếu, simulate "đúng một trong hai". None = qua phần này.
    Hàm riêng để `call_tool` chạy nó trước khi đụng tới `ctx` (hợp đồng: tham số sai ⇒ từ chối ngay, không cần backend / ngữ cảnh)."""
    if name not in ALL_TOOL_PARAMS:
        return {"refused": f"không có công cụ {name!r}"}
    extra = set(args) - ALL_TOOL_PARAMS[name]
    if extra:
        return {"refused": f"tham số không hợp lệ cho {name}: {', '.join(sorted(extra))}"}
    if name in ("get_employee_result", "explain_kpi", "simulate_kpi_change") and args.get("employee_id") in (None, ""):
        return {"refused": f"{name} cần employee_id"}
    if name in ("explain_kpi", "simulate_kpi_change") and args.get("kpi") in (None, ""):
        return {"refused": f"{name} cần kpi"}
    if name == "simulate_kpi_change" and (args.get("extra") in (None, "")) == (args.get("target_pct") in (None, "")):
        # A-ii-1: đúng MỘT trong hai — "nếu thêm N" (extra) hoặc "nếu đạt X % chỉ tiêu" (target_pct). Backend kiểm lại (lớp chính)
        return {"refused": "simulate_kpi_change cần đúng một trong hai: extra (số thêm) hoặc target_pct (đạt X % chỉ tiêu)"}
    if name == "get_team_kpi_summary" and args.get("kpi") in (None, ""):
        return {"refused": "get_team_kpi_summary cần kpi"}
    if name == "compare_employees" and (args.get("employee_a") in (None, "") or args.get("employee_b") in (None, "")):
        return {"refused": "compare_employees cần employee_a và employee_b (hai mã nhân viên)"}
    return None


def _get_json(ctx: AgentContext, path: str, query: dict | None = None, shape=None) -> dict:
    """Một GET tới backend. Lời từ chối của backend đi thẳng về mô hình (không gọt). `shape` = bước gọt kết quả cho mô hình (nếu có)."""
    try:
        response = backend_get(ctx.backend_url, path, query)
        if response.status_code == 422 and xem_proxy.client_headers(ctx.backend_url) is not None:
            # Cổng xem kiểm tham số bằng regex TRƯỚC backend (số âm, "abc", mã NV / KPI sai dạng) ⇒ 422. Với mô hình đó là LỜI TỪ CHỐI như backend
            # vẫn trả, không phải lỗi mạng — để mô hình sửa tham số thay vì báo "backend không trả lời"
            return {"refused": "tham số không hợp lệ — kiểm lại mã nhân viên, tên chỉ tiêu, kỳ và các số đã gửi"}
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        return {"error": f"backend không trả lời được ({type(error).__name__})"}
    if shape is None or "refused" in body:
        return body
    return shape(body)


def _only_opportunities(body: dict) -> dict:
    """Chỉ giữ cặp NV × KPI `is_opportunity` đúng (68 ⇒ 37 cặp tháng 10, 04/11) và NÓI RA đang hiện N / tổng M (bài học Capitol: cắt không báo)."""
    kept = [item for item in body["items"] if item["is_opportunity"] is True]
    no_chance = sum(item["is_opportunity"] is False for item in body["items"])
    # G1 (minor 1): tên KPI tỷ lệ lấy từ CHÍNH các cặp không tính nhịp (tháng 10 chỉ LPPC_Meat) — không ghi cứng "LPPC, LPPC_Meat"
    ratio = sorted({item["kpi"] for item in body["items"] if item["is_opportunity"] is None})
    note = f"đang hiện {len(kept)} / tổng {len(body['items'])} cặp NV × KPI còn mốc kế — {no_chance} cặp không có cơ hội theo nhịp"
    if ratio:
        note += f", {len(body['items']) - len(kept) - no_chance} cặp KPI tỷ lệ không tính nhịp ({', '.join(ratio)})"
    return {**{key: value for key, value in body.items() if key != "items"}, "items": kept, "shown": note}


def _data_provenance(ctx: AgentContext) -> dict:
    """Số đang dùng lấy từ đâu: kỳ, ngày chốt, lúc chụp số, bản luật, phiên bản workflow, bản tính (lúc tính, đã soát chưa, có bị thay chưa).
    Không gọi `/verify` (tính lại ~1,6 s mỗi câu — bài học Capitol: công cụ đọc phải nhanh)."""
    meta = _get_json(ctx, "/api/meta")
    if "error" in meta:
        return meta
    run = _get_json(ctx, f"/api/runs/{_segment(meta['current_run_id'])}")
    if "error" in run or "refused" in run:
        return {"error": "không đọc được bản tính đang dùng"}
    return {**{key: meta.get(key) for key in META_FIELDS_FOR_MODEL},
            # mức tra cứu vòng 3: mô hình nhận cùng danh sách cờ như mọi kết quả công cụ (bỏ cờ của riêng một người — chỉ get_data_issues có)
            "data_flags": meta.get("data_flags_for_model"),
            "bonus_run": {key: run.get(key) for key in RUN_FIELDS_FOR_MODEL}}
