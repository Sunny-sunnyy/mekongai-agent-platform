"""Gọi THẲNG một công cụ KPI (không qua HTTP): đúng hàm mà mỗi đường backend gọi (`backend/main.py`), qua đúng luật kiểm tham số của
lớp agent (`backend_client.check_tool_args`) — dùng chung cho khối agent (`agent_block.py`) và API công cụ cho Capitol (`backend/main.py`).

Tách khỏi agent_block.py (10/11, nguyên chữ) vì agent_block nạp `agent/chat.py` (langchain) — backend phải chạy được trong image Capitol.
"""
import json

from fastapi.encoders import jsonable_encoder

from kpi_agent.agent.backend_client import AgentContext, _drop_zero_partner, _only_opportunities, check_tool_args
from kpi_agent.backend import opportunities, queries, what_if


def _text(value) -> str | None:
    # Qua HTTP mọi tham số tới backend là chữ (đường dẫn / truy vấn) ⇒ gọi thẳng cũng đưa chữ, để hàm backend kiểm y như qua HTTP
    return None if value in (None, "") else str(value)


def _run_tool(kpi_ctx: queries.KpiContext, name: str, args: dict) -> dict:
    """Một công cụ = đúng hàm đường backend tương ứng gọi (`backend/main.py`), cùng cách đưa tham số như `backend_client.call_tool`."""
    period = _text(args.get("period"))
    if name == "get_team_bonus_table":
        return queries.get_team_bonus_table(kpi_ctx, period=period)
    if name == "get_employee_result":
        return queries.get_employee_result(kpi_ctx, employee_id=_text(args["employee_id"]), period=period)
    if name == "get_mid_month_gap":
        return queries.get_mid_month_gap(kpi_ctx, employee_id=_text(args.get("employee_id")), period=period)
    if name == "explain_kpi":
        return queries.explain_kpi(kpi_ctx, employee_id=_text(args["employee_id"]), kpi=_text(args["kpi"]), period=period)
    if name == "simulate_kpi_change":
        # A-ii-1: "nếu thêm N" (extra) hoặc "nếu đạt X %" (target_pct) — như backend_client: ô trống không gửi, có thì gửi dạng chữ
        return what_if.simulate_kpi_change(kpi_ctx, employee_id=_text(args["employee_id"]), kpi=_text(args["kpi"]),
                                           extra=_text(args.get("extra")), target_pct=_text(args.get("target_pct")), period=period)
    # Nhóm A bậc mức tra cứu (A1, A3): như `backend_client.call_tool` — mọi tham số đi qua truy vấn dạng chữ, tham số trống không gửi
    if name == "get_team_kpi_summary":
        return queries.get_team_kpi_summary(kpi_ctx, kpi=_text(args.get("kpi")), period=period)
    if name == "compare_employees":
        return queries.compare_employees(kpi_ctx, employee_a=_text(args.get("employee_a")), employee_b=_text(args.get("employee_b")),
                                         kpi=_text(args.get("kpi")), period=period)
    if name == "get_data_issues":
        return queries.get_data_issues(kpi_ctx, employee_id=_text(args.get("employee_id")), period=period)
    if name == "get_opportunities":
        body = jsonable_encoder(opportunities.opportunities(kpi_ctx, period=period))
        # như `backend_client._get_json`: lời từ chối đi thẳng về mô hình, không gọt
        return body if "refused" in body else _only_opportunities(body)
    raise ValueError(f"công cụ {name} chưa nối được trong khối agent")   # bộ kiểm file đã chặn — tới đây là lỗi code


def local_tool_caller(kpi_ctx: queries.KpiContext, allowed: list[str]):
    """Hàm gọi công cụ cho vòng chat: chỉ công cụ được nối dây; cùng lời từ chối của lớp agent; kết quả đổi sang JSON như FastAPI trả."""
    def call(agent_ctx: AgentContext, name: str, args: dict) -> dict:
        if name not in allowed:
            return {"refused": f"công cụ {name!r} không được nối vào khối agent (có: {', '.join(allowed)})"}
        if name == "simulate_kpi_change":
            args = _drop_zero_partner(args)   # cùng bước bỏ ô 0 thừa như `backend_client.call_tool` (A-ii-1)
        refusal = check_tool_args(agent_ctx, name, args)
        if refusal:
            return refusal
        return json.loads(json.dumps(jsonable_encoder(_run_tool(kpi_ctx, name, args))))
    return call
