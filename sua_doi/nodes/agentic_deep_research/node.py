"""
Agentic deep research node — performs multi-round iterative research using any
connected MCP tool server. Unlike DeepResearchNode (which calls Parallel's closed
API), this node runs entirely through PydanticAI so arbitrary MCP tool servers can
be attached and called mid-research.

Round structure
---------------
1. Plan       — LLM breaks the topic into 3–5 focused sub-questions.
2. Research   — LLM calls MCP tools to gather evidence for each sub-question.
3. Reflect    — LLM reviews findings for gaps; loops back to Round 2 if gaps
                remain (up to max_reflection_loops total iterations).
4. Synthesize — LLM writes the final report from accumulated findings.

An ``MCPAgentNode`` subclass (ENG-4437): per-round system prompts flow
through the ``_system_prompt_replacement`` hook, the round loop replaces
the ``_run`` hook, and each round streams through the shared
``_stream_agent`` (message-history-aware, with a round-correct
recompiler for transient-error retries).
"""

import logging
from typing import Any, ClassVar

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import RunUsage, UsageLimits

# [ghi chú] Trần CỘNG DỒN cả khối: một RunUsage chung cho MỌI vòng
# (kế hoạch / nghiên cứu / soát / viết) ⇒ lịch sử cộng dồn không nhân trần lên. Bản gốc mỗi vòng chỉ request_limit=200, không trần token.
# [E M1] vào 240k (không phải 480k): lần gọi cuối vượt tối đa 1 ngữ cảnh (lịch sử dồn) ⇒ mọi lần gọi < 272k (bang_gia: > 272k giá cao hơn).
DR_LAN_GOI, DR_TRAN_VAO, DR_TRAN_RA, DR_RA_MOT_LAN, DR_CONG_CU, DR_VONG_SOAT_TOI_DA = 30, 240_000, 48_000, 8_000, 10, 2
DR_DE_TAI_BYTE = 40_000   # [E M5] đề tài dài ⇒ từ chối TRƯỚC lần gọi đầu


def _tim_loi_tran(e: BaseException):
    """[E M4 + minor vòng 2] Lỗi UsageLimitExceeded do chạm tool_calls_limit (có thể bị bọc trong ExceptionGroup / __cause__) ⇒ trả chính lỗi
    đó, không có ⇒ None. Xét KIỂU lỗi + chữ (chữ của pydantic-ai 1.107.5: "…would exceed the tool_calls_limit of N…")."""
    from pydantic_ai.exceptions import UsageLimitExceeded
    seen, st = set(), [e]
    while st:
        x = st.pop()
        if x is None or id(x) in seen:
            continue
        seen.add(id(x))
        if isinstance(x, UsageLimitExceeded) and "tool_calls_limit" in str(x):
            return x
        st += [x.__cause__, x.__context__] + list(getattr(x, "exceptions", ()) or ())
    return None


def _cham_tran_cong_cu(e: BaseException) -> bool:
    return _tim_loi_tran(e) is not None


def _cat_goi_treo(msgs: list) -> list:
    """[E N1] Lịch sử bắt được khi chạm trần kết thúc bằng lần trả lời có lời gọi công cụ CHƯA chạy (trần kiểm trước khi chạy) ⇒ gửi tiếp cho
    OpenAI sẽ lỗi (tool call không có kết quả) ⇒ bỏ các tin trả lời cuối có ToolCallPart chưa có ToolReturnPart / RetryPromptPart đi kèm."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    out = list(msgs)
    da_tra = {getattr(p, "tool_call_id", None) for m in out for p in getattr(m, "parts", []) if getattr(p, "part_kind", "") in ("tool-return", "retry-prompt")}
    while out and isinstance(out[-1], ModelResponse) and any(isinstance(p, ToolCallPart) and p.tool_call_id not in da_tra for p in out[-1].parts):
        out.pop()
    return out


def _so_ket_qua_cong_cu(msgs: list) -> int:
    """Số kết quả công cụ (ToolReturnPart) trong lịch sử — bằng chứng tra web thật đã có."""
    return sum(1 for m in msgs or [] for p in getattr(m, "parts", []) if getattr(p, "part_kind", "") == "tool-return")

from core.settings import settings
from nodes.agentic_deep_research.params import PARAMS
from nodes.agentic_deep_research.ports.input import INPUT_PORTS
from nodes.agentic_deep_research.ports.output import OUTPUT_PORTS
from nodes.agentic_deep_research.prompts import (
    PLAN_SYSTEM_PROMPT,
    REFLECT_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    SYNTHESIZE_SYSTEM_PROMPT,
)
from nodes.mcp_agent.node import MCPAgentNode
from workflow.citations import citations_to_metadata
from workflow.node.base.file import NodeFile
from workflow.node.base.param.base import NodeParam, NodeParams
from workflow.node.base.param.custom import MarkdownDisplay
from workflow.node.registry import register

logger: logging.Logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _dr_gioi_han_theo(bo_tran_token: bool) -> UsageLimits:
    """[ghi chú] Trần TỔNG cả khối tra cứu sâu (một RunUsage chung mọi vòng). luna và mọi mô hình tính trần
    tiền: như cũ (30 lần gọi, 240.000 vào, 48.000 ra, 10 lần tra web). Claude 5.5 qua Bedrock: BỎ hai trần token; GIỮ số lần gọi (chống vòng lặp) và số lần tra web
    (mỗi lần tra web tốn tiền Exa — tính trần ngày)."""
    if bo_tran_token:
        return UsageLimits(request_limit=DR_LAN_GOI, tool_calls_limit=DR_CONG_CU)
    return UsageLimits(request_limit=DR_LAN_GOI, input_tokens_limit=DR_TRAN_VAO, output_tokens_limit=DR_TRAN_RA, tool_calls_limit=DR_CONG_CU)


# [ghi chú] @register phải nằm NGAY TRÊN class: bản 76070b9e chèn hàm _dr_gioi_han_theo vào giữa ⇒ sổ đăng ký
# nhận hàm thay cho class ⇒ hộp từ chối loại khối này ("không có trong sổ đăng ký khối", lượt 6ab50122).
@register
class AgenticDeepResearchNode(MCPAgentNode):
    """Multi-round iterative deep research node backed by PydanticAI."""

    def _dr_gioi_han(self) -> UsageLimits:
        # cờ do lớp gốc đặt ngay sau khi chọn mô hình (workflow/node/base/agent/node.py — bản vá bỏ trần token); chưa có cờ ⇒ coi như luna (giữ trần)
        return _dr_gioi_han_theo(bool(getattr(self, "_chay_thu_bo_tran_token", False)))

    version: str = "0.1.0"

    display_name: str = "Deep Research (Agentic)"
    description: str = (
        "Performs multi-round iterative research using any connected MCP tool. "
        "Breaks the topic into sub-questions, gathers evidence via MCP tools, "
        "checks for gaps, and synthesizes a final structured report."
    )
    icon: str = "BotMessageSquareIcon"

    show: bool = True
    beta: bool = False

    node_id: str = "agentic_deep_research_node"
    node_type: str = "agent"
    node_sub_type: str = "agentic_deep_research"

    input_ports = INPUT_PORTS
    output_ports = OUTPUT_PORTS
    params: NodeParams = PARAMS

    # Anthropic-primary / Bedrock-fallback opt-out (ENG-2158). Deep
    # research runs many sequential LLM rounds against the same set of
    # MCP tools; the Bedrock fallback is excluded from this node
    # intentionally per the ticket scope. Overriding to ``False`` keeps
    # this node on the Anthropic-direct path regardless of
    # ``settings.BEDROCK_FALLBACK_ENABLED``.
    _enable_bedrock_failover: bool = False

    # The round loop compiles per-phase agents itself (each with its own
    # system prompt), so the pipeline's single compile step is skipped and
    # ``_run`` receives ``agent=None``.
    _compile_in_pipeline: ClassVar[bool] = False
    # Never blind-recompile on retry: a bare ``compile()`` would rebuild
    # with the node default prompt instead of the active round's. Rounds
    # pass an explicit round-correct recompiler to ``_stream_agent``.
    _recompile_on_retry: ClassVar[bool] = False
    # Skip the in-sandbox file probe: its findings only feed the
    # user-prompt files directive built by ``_get_runtime_args``, which
    # this node's round-based ``_run`` never uses — the probe would be
    # pure cost (pip installs + up to 120s). The sandbox manifest that the
    # rounds DO consume needs no probe.
    _sandbox_prompt_probing: ClassVar[bool] = False

    def __init__(
        self,
        node_config: list[dict],
        node_inputs: list[dict] | None = None,
        validate_required: bool = True,
        validate_type: bool = False,
    ) -> None:
        super().__init__(
            node_config=node_config,
            node_inputs=node_inputs,
            validate_required=validate_required,
            validate_type=validate_type,
        )
        # Set by _compile_for_round before each compile() call so the
        # _system_prompt_replacement hook injects the correct per-round
        # system prompt instead of the node's default.
        self._round_system_prompt: str | None = None

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _system_prompt_replacement(self) -> str | None:
        """Replace the (empty) param system prompt with the active round's.

        Grounding + workflow special instructions are applied to the
        replacement by the base pipeline, matching the historical
        behaviour of the round-prompt override.
        """
        return self._round_system_prompt

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _compile_for_round(self, system_prompt: str) -> Agent:
        """Compile a fresh pydantic-ai Agent configured for a specific research round.

        Sets _round_system_prompt so that the next compile() injects the
        round-specific prompt instead of the node's default. Always resets
        _round_system_prompt to None via try/finally, so a compilation
        failure cannot leave the node in a corrupted state.
        """
        self._round_system_prompt = system_prompt
        try:
            agent: Agent = await self.compile()
        finally:
            self._round_system_prompt = None
        return agent

    async def _stream_research_round(
        self,
        agent: Agent,
        prompt: str,
        message_history: list[ModelMessage] | None = None,
        *,
        round_system_prompt: str | None = None,
    ) -> tuple[str, list[ModelMessage]]:
        """Execute one streaming research round against a pre-compiled agent.

        Runs the shared ``_stream_agent`` with fixed run args (this round's
        prompt + accumulated history) and a round-correct recompiler, so a
        transient-error retry re-establishes MCP connections WITHOUT losing
        the per-round system prompt.

        Returns ``(output_text, all_messages)`` so callers can thread the
        full history into the next round.
        """

        async def _recompile() -> Agent:
            if round_system_prompt is not None:
                return await self._compile_for_round(round_system_prompt)
            return agent

        run_args = {
            "user_prompt": prompt,
            "message_history": message_history or [],
            # [va_deep_research] cùng RunUsage qua mọi vòng + mọi lần thử lại của _stream_agent (dùng lại run_args) ⇒ trần là TỔNG cả khối
            "usage": self._dr_usage,
            "usage_limits": self._dr_gioi_han(),
            # [E M4] mỗi lần ≤ 1 công cụ. [ghi chú] chạy Claude: KHÔNG gửi max_tokens (cổng AI vốn đã bỏ qua số nhỏ trên tuyến Bedrock — đây chỉ là
            # dọn chữ chết để người đọc code không hiểu nhầm là còn trần); luna (tính trần tiền): giữ 8.000 / lần như mức ở giao_dien/muc_toi_da.py DR_RA_MOT_LAN.
            "model_settings": ({"parallel_tool_calls": False} if getattr(self, "_chay_thu_bo_tran_token", False)
                               else {"max_tokens": DR_RA_MOT_LAN, "parallel_tool_calls": False}),
        }
        # History is LOAD-BEARING for this node — every later round reasons
        # against it — unlike the base pipeline, where capture is
        # best-effort metadata. Clear it before the run and fail the round
        # if it's still missing afterwards; silently continuing would chain
        # the next round off stale evidence and "succeed" with a hollow
        # report (pre-ENG-4437 the inline all_messages() call failed the
        # round visibly).
        self._final_message_history = None
        # [E N1] bắt lịch sử của vòng kể cả khi vòng hỏng vì chạm trần ⇒ kết quả tra web đã thu không bị bỏ. Gắn vào lỗi (thuộc tính
        # _dr_lich_su) để _run dùng cho vòng viết. Giới hạn: _stream_agent thử lại (lỗi tạm) trong cùng context ⇒ chỉ giữ lần chạy đầu (tài liệu
        # capture_run_messages, pydantic-ai 1.107.5).
        from pydantic_ai import capture_run_messages
        with capture_run_messages() as _bat:
            try:
                output_text = await self._stream_agent(
                    agent=agent,
                    run_args=run_args,
                    recompiler=_recompile,
                )
            except Exception as e:  # noqa: BLE001
                loi = _tim_loi_tran(e)
                if loi is not None:
                    gan = _cat_goi_treo(_bat)
                    for x in {id(e): e, id(loi): loi}.values():
                        x._dr_lich_su = gan
                raise
        if not self._final_message_history:
            raise RuntimeError(
                "Research round completed but its message history could not "
                "be captured; failing the round rather than continuing "
                "without this round's evidence."
            )
        return output_text, list(self._final_message_history)

    # ------------------------------------------------------------------
    # Per-round wrappers
    # ------------------------------------------------------------------

    async def _plan_round(
        self,
        research_topic: str,
    ) -> tuple[str, list[ModelMessage]]:
        """Round 1 — Break the research topic into 3–5 focused sub-questions."""
        await self.emit_progress("Round 1: Building research plan...")
        agent: Agent = await self._compile_for_round(PLAN_SYSTEM_PROMPT)
        return await self._stream_research_round(
            agent,
            f"Research topic: {research_topic}\n\nBreak this into 3–5 focused sub-questions.",
            round_system_prompt=PLAN_SYSTEM_PROMPT,
        )

    async def _research_round(
        self,
        plan_or_gaps: str,
        message_history: list[ModelMessage],
    ) -> tuple[str, list[ModelMessage]]:
        """Round 2 — Gather evidence for each sub-question using MCP tools.

        Called after the planning round (with sub-questions as plan_or_gaps)
        and again after a reflection round that identified gaps (with the gap
        description as plan_or_gaps). Message history carries full context
        from prior rounds.
        """
        await self.emit_progress("Round 2: Executing research...")
        agent: Agent = await self._compile_for_round(RESEARCH_SYSTEM_PROMPT)
        return await self._stream_research_round(
            agent,
            f"Questions / gaps to research:\n{plan_or_gaps}\n\nUse all available tools.",
            message_history=message_history,
            round_system_prompt=RESEARCH_SYSTEM_PROMPT,
        )

    async def _reflect_round(
        self,
        findings: str,
        message_history: list[ModelMessage],
    ) -> tuple[str, list[ModelMessage]]:
        """Round 3 — Review current findings for gaps and decide whether to loop back.

        The caller checks whether reflect_text starts with "No significant
        gaps found." to decide whether to stop looping — the decision logic
        stays centralised in ``_run``.
        """
        await self.emit_progress("Round 3: Reviewing for gaps...")
        agent: Agent = await self._compile_for_round(REFLECT_SYSTEM_PROMPT)
        return await self._stream_research_round(
            agent,
            f"Review the following research findings for gaps:\n{findings}",
            message_history=message_history,
            round_system_prompt=REFLECT_SYSTEM_PROMPT,
        )

    async def _synthesize_round(
        self,
        research_topic: str,
        findings: str,
        message_history: list[ModelMessage],
    ) -> tuple[str, list[ModelMessage]]:
        """Round 4 — Produce the final structured research report."""
        await self.emit_progress("Round 4: Synthesizing final report...")
        agent: Agent = await self._compile_for_round(SYNTHESIZE_SYSTEM_PROMPT)
        return await self._stream_research_round(
            agent,
            (
                f"Research topic: {research_topic}\n\n"
                f"Accumulated findings:\n{findings}\n\n"
                "Write the final comprehensive research report."
            ),
            message_history=message_history,
            round_system_prompt=SYNTHESIZE_SYSTEM_PROMPT,
        )

    # ------------------------------------------------------------------
    # Main run loop (pipeline ``_run`` hook)
    # ------------------------------------------------------------------

    async def _run(self, agent: Agent | None) -> str:
        """Run the 4-round iterative research loop and return the final report.

        Execution order:
            1. _plan_round       — sub-question decomposition (always runs once).
            2. _research_round   — MCP tool data gathering (runs at least once).
            3. _reflect_round    — gap detection; loops back to _research_round
                                   if gaps remain, up to max_reflection_loops
                                   total reflect iterations.
            4. _synthesize_round — final report generation (always runs once).

        Params are extracted into locals immediately because each
        ``_compile_for_round`` call re-invokes ``_get_validated_params``,
        which overwrites ``self._validated_params``.
        """
        research_topic: str = str(self._validated_params.get("user_prompt", ""))
        max_reflection_loops: int = int(
            self._validated_params.get("max_reflection_loops", 2)
        )
        # [va_deep_research] kẹp vòng soát (params cho tới 3) + đề tài rỗng ⇒ dừng TRƯỚC lần gọi đầu (0 đồng); usage mới mỗi lần chạy khối
        max_reflection_loops = max(1, min(max_reflection_loops, DR_VONG_SOAT_TOI_DA))
        if not research_topic.strip() or research_topic.strip().lower() in ("none", "null"):
            raise ValueError("Nghiên cứu sâu: no text content provided — chưa có đề tài (ô đề tài trống). Không gọi AI.")
        if len(research_topic.encode()) > DR_DE_TAI_BYTE:
            raise ValueError(f"Nghiên cứu sâu: đề tài {len(research_topic.encode())} byte vượt trần {DR_DE_TAI_BYTE} — rút gọn đề tài. Không gọi AI.")
        self._dr_lich_su_tran = None
        self._dr_het_cong_cu = False
        self._dr_usage = RunUsage()

        # ENG-4437: when input files are connected they are uploaded into
        # this node's E2B sandbox and every round's agent carries the
        # sandbox tools — surface the file manifest on the topic so all
        # rounds know the files exist.
        sandbox_manifest = self._build_sandbox_manifest_prompt()
        if sandbox_manifest:
            research_topic = f"{research_topic}\n\n{sandbox_manifest}"
            if len(research_topic.encode()) > DR_DE_TAI_BYTE + 8_000:   # [E minor vòng 2] kiểm lại SAU khi cộng danh sách file (≤ 8.000 B dư)
                raise ValueError(f"Nghiên cứu sâu: đề tài + danh sách file {len(research_topic.encode())} byte vượt trần — bớt file / rút gọn. Không gọi AI.")

        # ── Round 1: Plan ──────────────────────────────────────────────
        plan_text, messages = await self._plan_round(research_topic)

        # ── Round 2: Research (first pass) ─────────────────────────────
        try:
            findings, messages = await self._research_round(plan_text, messages)
        except Exception as e:  # noqa: BLE001 — [E M4] chạm trần tra web ⇒ không bỏ cả khối, sang viết với cái đã có
            if not _cham_tran_cong_cu(e):
                raise
            self._dr_het_cong_cu, findings = True, plan_text
            messages = self._lay_lich_su_tran(e, messages)   # [E N1] giữ kết quả tra web của vòng hỏng

        # ── Reflection loop ────────────────────────────────────────────
        # Runs max_reflection_loops times total. Each iteration emits
        # Round 3. If gaps are found and budget remains, loops back to
        # Round 2 before the next reflection.
        for reflection_round in range(max_reflection_loops):
            if self._dr_het_cong_cu:
                break
            is_last: bool = reflection_round == max_reflection_loops - 1
            try:
                reflect_text, messages = await self._reflect_round(findings, messages)
            except Exception as e:  # noqa: BLE001 — [E M4]
                if not _cham_tran_cong_cu(e):
                    raise
                self._dr_het_cong_cu = True
                messages = self._lay_lich_su_tran(e, messages)
                break

            normalized_reflection: str = reflect_text.strip().lower()
            if is_last or normalized_reflection.startswith(
                "no significant gaps found."
            ):
                break

            # Gaps found and loop budget remains — research again.
            try:
                findings, messages = await self._research_round(reflect_text, messages)
            except Exception as e:  # noqa: BLE001 — [E M4] giữ findings vòng trước
                if not _cham_tran_cong_cu(e):
                    raise
                self._dr_het_cong_cu = True
                messages = self._lay_lich_su_tran(e, messages)
                break

        # ── Round 4: Synthesize ────────────────────────────────────────
        n_kq = _so_ket_qua_cong_cu(messages)
        if self._dr_het_cong_cu:   # [E N1] nói thật với vòng viết: đã tra được bao nhiêu
            findings = (f"{findings}\n\n[LƯU Ý HỆ THỐNG] Đã chạm trần {DR_CONG_CU} lần tra web. Kết quả tra web đã thu: {n_kq} (nằm trong lịch sử "
                        "ở trên). Chỉ dùng số liệu / khẳng định có trong các kết quả đó; phần chưa tra được ghi rõ là CHƯA TRA ĐƯỢC, không tự nêu số."
                        if n_kq else
                        f"{findings}\n\n[LƯU Ý HỆ THỐNG] Đã chạm trần tra web và CHƯA thu được kết quả tra web nào. KHÔNG nêu số liệu, ngày, tên nguồn; "
                        "chỉ viết khung báo cáo + danh sách câu hỏi chưa tra được.")
        try:
            final_report, _ = await self._synthesize_round(research_topic, findings, messages)
        except Exception as e:  # noqa: BLE001 — [E M4] vòng viết cũng chạm trần tra web ⇒ trả kết quả nghiên cứu gần nhất (không gọi AI thêm)
            if not _cham_tran_cong_cu(e):
                raise
            self._dr_het_cong_cu = True
            final_report = str(findings)
        if self._dr_het_cong_cu:   # [E N1] ghi chú đúng sự thật theo số kết quả tra web thật có trong lịch sử
            final_report = (f"{final_report}\n\n_(Ghi chú hệ thống: đã chạm trần {DR_CONG_CU} lần tra web của khối — báo cáo dựng từ {n_kq} "
                            "kết quả tra web đã thu được, có thể thiếu phần chưa tra.)_" if n_kq else
                            f"{final_report}\n\n_(Ghi chú hệ thống: đã chạm trần {DR_CONG_CU} lần tra web mà CHƯA thu được kết quả tra web nào — "
                            "báo cáo KHÔNG có bằng chứng tra web, không dùng số liệu trong đó.)_")
        # [ghi chú] MỘT dòng log khi khối KẾT THÚC THÀNH CÔNG:
        # số lần gọi công cụ của CẢ khối (bộ đếm chung mọi vòng). [bản 2 20/11 — E K1] Bộ đếm của thư viện (usage.tool_calls) CHỈ tăng khi công cụ TRẢ VỀ mà không ném
        # (pydantic-ai 1.107.5 tool_manager.py:767); công cụ MCP báo isError ⇒ thư viện ném ModelRetry ⇒ KHÔNG đếm. Số này ≥ số lần trả tiền thật là NHỜ hộp tra web của
        # mình trả mọi lỗi Exa bằng kết quả thường {"ok": false} (va_exa/may_chu.py) ⇒ có đếm; đổi hộp Exa thành ném lỗi sau khi đã gọi Exa là phá điều kiện này. sandbox/chay_s5.py đọc dòng
        # này để ghi tiền tra web theo số lần thật thay cho mức tối đa. Khối hỏng không tới đây ⇒ không có dòng ⇒ sổ giữ mức tối đa. Chỉ ghi log — lỗi thì bỏ qua.
        try:
            from ai.agentic.cache_observability import _phien_hien_tai

            logger.info("[DR_CONG_CU] session=%s goi_cong_cu=%d ket_qua_cong_cu=%d het_tran=%d",
                        _phien_hien_tai(), int(getattr(self._dr_usage, "tool_calls", 0) or 0), int(n_kq), int(bool(self._dr_het_cong_cu)))
        except Exception:  # noqa: BLE001
            pass
        return final_report

    def _lay_lich_su_tran(self, e: BaseException, cu: list) -> list:
        """[E N1] Lịch sử vòng hỏng vì trần (gắn ở _stream_research_round). Không có / rỗng / NGẮN hơn lịch sử cũ ⇒ giữ lịch sử cũ."""
        loi = _tim_loi_tran(e)
        moi = getattr(loi, "_dr_lich_su", None) or getattr(e, "_dr_lich_su", None)
        return list(moi) if moi and len(moi) >= len(cu or []) else cu

    # ------------------------------------------------------------------
    # Output shape
    # ------------------------------------------------------------------

    def _output_display_model(self, result: Any) -> NodeParam:
        return MarkdownDisplay(
            display_name="Research Report",
            field_id="research_report",
            required=False,
            value=result,
            info="Final synthesized research report",
            show=True,
        )

    def _output_metadata(
        self,
        *,
        result: Any,
        output_node_files: list[NodeFile],
        input_file_names: set[str],
    ) -> dict[str, Any]:
        return {
            "research_topic": str(self._validated_params.get("user_prompt", "")),
            # Citations harvested by the shared streaming loop across all
            # research rounds (tool-retrieved sources).
            "citations": citations_to_metadata(self._all_run_citations()),
        }
