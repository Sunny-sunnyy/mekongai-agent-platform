"""Khối agent có công cụ (09/11 —).

Trợ lý KPI chạy như MỘT khối trên workflow:
- số lấy từ CHÍNH các khối tính đứng trước (cổng vào `rules, targets, tracking, issues, results, bonuses`), không qua backend đang chạy;
- chỉ dùng công cụ nối dây vào cổng `tools` — công cụ không nối thì mô hình không thấy mô tả, và code từ chối nếu mô hình vẫn gọi;
- công cụ = gọi thẳng ĐÚNG hàm mà mỗi đường backend gọi (`backend/main.py`: queries / what_if / opportunities), đi qua ĐÚNG luật kiểm tham số
  của agent (`backend_client.check_tool_args`) rồi đổi kết quả sang JSON như FastAPI ⇒ mô hình nhận y như qua HTTP (đo T2).
Che mã: mô hình chỉ nhận lời dặn + câu hỏi + kết quả công cụ; mọi hàm công cụ đã che mã NV (`queries` dùng mã NVxx) — mã thật nằm trong cổng vào
nhưng không cổng nào đi thẳng vào lời gửi mô hình (#1, đo T4).
Khác backend (ghi rõ): không có bảng lưu bản tính ⇒ nhãn trạng thái luôn "tạm tính", `get_data_provenance` chưa nối được; bản luật ghi theo mã băm
NỘI DUNG luật (khối không biết đường file luật)."""
import hashlib
import json

from kpi_agent.agent import chat
from kpi_agent.agent.backend_client import AgentContext
from kpi_agent.agent.presentation import present_record
from kpi_agent.backend import queries
from kpi_agent.workflow.direct_tools import _run_tool, local_tool_caller   # 10/11: dời sang direct_tools.py (nguyên chữ)
from kpi_agent.workflow.runner import load_mask

CONTEXT_PORTS = ("rules", "targets", "tracking", "issues", "results", "bonuses")


def build_contexts(inputs: dict, cutoff) -> tuple[queries.KpiContext, AgentContext]:
    """Cổng vào (mã THẬT) ⇒ ngữ cảnh công cụ (như backend) + ngữ cảnh agent (như agent đọc từ /api/meta lúc bật)."""
    to_real = {masked: real for real, masked in load_mask().items()}
    rule_sha = hashlib.sha256(json.dumps(inputs["rules"], sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:8]
    kpi_ctx = queries.context_from_outputs(cutoff, to_real, {port: inputs[port] for port in CONTEXT_PORTS},
                                           f"luật kỳ {inputs['rules']['period']} (khối load_rules)@{rule_sha}")
    agent_ctx = AgentContext("khối agent — gọi thẳng hàm", kpi_ctx.period, cutoff, sorted(kpi_ctx.to_real), list(queries.KPI_ORDER),
                             # mức tra cứu vòng 3: bản cờ cho mô hình (bỏ cờ của riêng một người) — trường này chưa vào lời dặn; nạp đúng bản để sau này khỏi lọt (minor m1)
                             kpi_ctx.data_flags_for_model, kpi_ctx.rule_version, kpi_ctx.status_label)
    return kpi_ctx, agent_ctx


def answer(inputs: dict, params: dict, run: dict) -> dict:
    """Một câu hỏi (tham số lượt `question`) ⇒ bản ghi của vòng chat + danh sách công cụ được nối + phiên bản lời dặn / mô tả công cụ."""
    allowed = list(dict.fromkeys(inputs["tools"]))   # nối 2 dây cùng một công cụ ⇒ một công cụ
    kpi_ctx, agent_ctx = build_contexts(inputs, run["cutoff"])
    model = chat.make_model(agent_ctx.kpis, params["reasoning"], tools=allowed)
    record = chat.answer_question(model, agent_ctx, run["question"], tool_caller=local_tool_caller(kpi_ctx, allowed),
                                  max_tool_calls=params["max_tool_calls"])
    # Cùng bước trình bày bằng code mà server chat + bộ đo gọi (agent/server.py:137) ⇒ chữ khối trả = chữ người dùng chat thấy
    record = present_record(record, chat.context_numbers(agent_ctx))
    # "Nguồn của câu trả lời" (tính năng 4) — server đọc /api/meta; khối lấy từ chính ngữ cảnh của nó, cùng các trường
    record["provenance"] = {"period": kpi_ctx.period, "snapshot_at": kpi_ctx.snapshot_at.isoformat() if kpi_ctx.snapshot_at else None,
                            "cutoff": kpi_ctx.cutoff.isoformat(), "rule_version": kpi_ctx.rule_version, "status": kpi_ctx.status,
                            "status_label_for_model": kpi_ctx.status_label}
    return {**record, "tools_allowed": allowed,
            "config": {**chat.config_version(agent_ctx, params["reasoning"], tools=allowed), "max_tool_calls": params["max_tool_calls"]}}
