"""Review digest for the Human Intervention node (ENG-4536).

Why this exists
---------------

The Human Intervention node's ``preview_context`` param is, in practice,
the author's static review instructions with the ENTIRE upstream node
output appended (``ActivityNodeRuntime._merge_bound_param`` text-modal
concatenation). Agent-class upstreams put their full narration in
``NodeOutput.data`` — profiling play-by-play, tool commentary, self-talk —
so reviewers were shown a wall of process text instead of the handful of
items their decision actually needs.

This module distills the upstream content into a short decision brief with
one tool-less LLM call:

* The brief replaces the upstream content ONLY on the two HUMAN-facing
  surfaces (the node's ``display_model`` and the ``preview_context``
  carried on the intervention-request event -> A2A ``node.input_required``
  ``data.content.preview``). ``NodeOutput.data`` and the port emission keep
  the raw text byte-identical, so downstream nodes are provably unaffected.
* Nothing parses the checkpoint text. The model is handed the whole thing
  and told what it contains; it extracts semantically. The author's
  instructions are re-attached from the node's own ``node_config`` value,
  so they survive byte-identical without the model restating them and
  without recovering them from the merged string.
* The model may declare ``all_clear`` when nothing needs the reviewer's
  attention, so a clean checkpoint reads as exactly that.
* Idempotent across the INTERACTIVE activity's retries: the rendered brief
  is cached in Redis keyed by (session, node, content-hash), so re-emitted
  ``node.input_required`` events (deduped client-side by request_id) always
  carry the same brief. A different upstream content (rerun/loop) hashes to
  a new key and gets a fresh brief.
* Fail-open by contract: if the model cannot be reached after the bounded
  retries, the caller falls back to the raw text — today's exact behavior.
  A summarizer outage must never block a human checkpoint. There is no
  heuristic middle tier.

Config lives on ``core.settings``: ``HITL_REVIEW_DIGEST_MODEL`` /
``_TIMEOUT_S`` / ``_RETRIES`` / ``_MAX_INPUT_CHARS``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from pydantic import BaseModel, Field

from core.settings import settings

logger = logging.getLogger(__name__)


# Separator between the author's instructions (rendered verbatim) and the
# model's brief on the display surfaces.
DIGEST_SECTION_SEPARATOR = "\n\n---\n\n"

# Deterministic body used when the model declares all-clear but returned an
# empty markdown body (defensive; the prompt asks for a short paragraph).
_ALL_CLEAR_FALLBACK_MARKDOWN = (
    "**Nothing requires your attention.** The previous step completed "
    "normally and raised no issues."
)

# Head kept when clamping oversized upstream text. Conclusions cluster at
# the END of agent output (narration first, results last), so the clamp is
# tail-biased: keep a small head for context and spend the rest of the
# budget on the tail. This is a size guard only — never content detection.
_CLAMP_HEAD_CHARS = 1_000
_CLAMP_NOTICE = "\n\n[... middle of step output truncated for length ...]\n\n"


class ReviewDigest(BaseModel):
    """Structured output the digest model must return."""

    all_clear: bool = Field(
        description=(
            "True when, after filtering, nothing in the step output requires "
            "the reviewer's attention or decision."
        )
    )
    digest_markdown: str = Field(
        description=(
            "The decision brief as self-contained GitHub-flavored markdown. "
            "When all_clear is true: one short paragraph saying nothing needs "
            "attention plus a one-line summary of what the step produced."
        )
    )


_SYSTEM_PROMPT = """You prepare the brief shown to a HUMAN REVIEWER when an \
automated workflow pauses and waits for their review or approval.

You receive:
1. REVIEW INSTRUCTIONS (may be absent) — written by the workflow author; \
they say what the reviewer must check and which actions they can take.
2. CHECKPOINT TEXT — everything the reviewer would otherwise be shown. It \
begins with the REVIEW INSTRUCTIONS above (repeated verbatim), followed by \
the raw output of the workflow step that just ran. That step output \
typically mixes process narration ("I'll start by...", "Let me profile...") \
with the actual results, findings, and warnings. Treat only the part after \
the instructions as step output.

Produce `digest_markdown`: ONLY what the reviewer needs in order to decide. \
Follow these rules exactly.

EXTRACT — never invent:
- When REVIEW INSTRUCTIONS list items to check, surface each item with the \
value the step output actually gives for it.
- Every value you surface must exist in the step output. Back each one with \
a short verbatim quote (25 words or fewer) so the reviewer can trust it \
without rereading everything.
- If the instructions ask for something the step output does not contain, \
list it under a `### Requested but not found` section. This is critical \
signal — never fill the gap with an inferred or plausible value.

FILTER:
- Discard process narration, self-talk, tool-by-tool commentary, progress \
updates, and repetition. The reviewer needs what came OUT of the step, not \
how the step worked.
- Keep warnings, contradictions, data-quality issues, and anything the step \
itself flagged, under `### Needs your attention`, ordered most severe first.
- When the decision depends on opening a file the step produced, name that \
file under `### Artifacts`.

WHEN NOTHING NEEDS ATTENTION:
- If, after filtering, nothing requires the reviewer's attention or \
decision, set `all_clear` to true and make `digest_markdown` a single short \
paragraph saying so, plus one line on what the step produced.

FORMAT:
- `digest_markdown` is self-contained GitHub-flavored markdown, `###` \
headings only, at most about 300 words.
- Do NOT repeat or paraphrase the REVIEW INSTRUCTIONS — the reviewer sees \
them separately, directly above your brief.
- No preamble, no sign-off, no mention of these rules."""


def _clamp_checkpoint_text(text: str) -> str:
    """Bound the text handed to the model; keep head + tail (see above)."""
    max_chars = int(settings.HITL_REVIEW_DIGEST_MAX_INPUT_CHARS)
    if len(text) <= max_chars:
        return text
    tail_budget = max(max_chars - _CLAMP_HEAD_CHARS - len(_CLAMP_NOTICE), 0)
    return text[:_CLAMP_HEAD_CHARS] + _CLAMP_NOTICE + text[-tail_budget:]


def _build_user_prompt(review_instructions: str, checkpoint_text: str, khong_cat: bool = False) -> str:
    parts: list[str] = []
    if review_instructions.strip():
        parts.append(f"REVIEW INSTRUCTIONS:\n{review_instructions.strip()}")
    else:
        parts.append(
            "REVIEW INSTRUCTIONS: (none provided — surface whatever in the "
            "step output needs a human's attention or decision)"
        )
    # [ghi chú] + N2 nhóm kỹ thuật: bản gốc Capitol CẮT PHẦN GIỮA nội dung dài hơn HITL_REVIEW_DIGEST_MAX_INPUT_CHARS (giữ 1.000 ký tự đầu + phần đuôi)
    # ⇒ người duyệt đọc bản tóm tắt của một văn bản đã mất đoạn giữa. Chạy Claude (khong_cat=True): gửi NGUYÊN VĂN. Tuyến tính trần tiền: cắt như gốc.
    parts.append(f"CHECKPOINT TEXT:\n{checkpoint_text if khong_cat else _clamp_checkpoint_text(checkpoint_text)}")
    return "\n\n".join(parts)


def _build_digest_model() -> object:
    """Resolve ``HITL_REVIEW_DIGEST_MODEL`` into what ``Agent(model=...)`` wants.

    The setting's documented contract allows an OpenAI OR Anthropic registry
    id, so both go through their shared agent-node factories and get the
    registry policy applied: an OpenAI id (the default ``gpt-5.6-sol``) gets
    the spec's reasoning effort ("high") and max output tokens; an Anthropic
    id gets fast-suffix stripping, its thinking shape, max output tokens, and
    Bedrock failover -- the same resolution the A2A orchestrator's
    ``_build_agent_model`` performs. Anything else is handed to pydantic-ai's
    ``infer_model`` as-is.
    """
    from ai.agentic.models.fallback import (
        get_anthropic_model_with_bedrock_failover_for_agent_node,
    )
    from ai.agentic.models.openai import get_openai_model_for_agent_node
    from core.config.ai import AnthropicModels, OpenAIModels

    model_id = settings.HITL_REVIEW_DIGEST_MODEL
    try:
        return get_openai_model_for_agent_node(OpenAIModels(model_id))
    except ValueError:
        pass
    try:
        return get_anthropic_model_with_bedrock_failover_for_agent_node(
            AnthropicModels(model_id)
        )
    except ValueError:
        return model_id


async def _invoke_digest(user_prompt: str, session_id: str = "", node_id: str = "") -> ReviewDigest:
    """One tool-less structured-output call. Raises on any failure."""
    from pydantic_ai import Agent
    from pydantic_ai.usage import UsageLimits

    # [ghi chú] Capitol chỉ đặt request_limit=3; vào đã bị kẹp ≤ 60.000 ký tự (_clamp) nhưng
    # ra KHÔNG có trần ⇒ thêm trần token cả lượt + max_tokens mỗi lần. Số đo (18 dòng [DIGEST_USAGE] trong ket_qua/): 1 lần gọi,
    # vào ≤ 4.697, ra ≤ 396. Chạm trần ⇒ lỗi ⇒ build_review_digest trả None (fail-open: người duyệt thấy bản thô).
    # [ghi chú] file mô hình khối bảo Claude (dòng human_intervention_node hoặc mac_dinh) ⇒ bản tóm tắt chạy Claude qua cổng AI,
    # đè biến HITL_REVIEW_DIGEST_MODEL. Chạy Claude: không gửi max_tokens nhỏ, không trần tổng token (người vận hành); GIỮ 3 lần gọi; đầu vào KHÔNG còn bị cắt giữa
    # khi chạy Claude (bản 2 — _build_user_prompt khong_cat). File trống ⇒ theo biến cấu hình như trước. Lỗi ở đây ⇒ build_review_digest trả None (người duyệt thấy bản thô) — như cũ.
    from workflow.node.base.agent.model_resolution import chay_thu_mo_hinh_nguoi_gac

    _claude = await chay_thu_mo_hinh_nguoi_gac("human_intervention_node", None, {"timeout": 600})
    _ten_mo_hinh = _claude[1] if _claude else settings.HITL_REVIEW_DIGEST_MODEL
    agent = Agent(
        model=_claude[0] if _claude else _build_digest_model(),
        output_type=ReviewDigest,
        system_prompt=_SYSTEM_PROMPT,
        retries=1,
        **({} if _claude else {"model_settings": {"max_tokens": 4_000}}),
    )
    result = await agent.run(
        user_prompt,
        usage_limits=UsageLimits(request_limit=3) if _claude else UsageLimits(request_limit=3, input_tokens_limit=70_000, output_tokens_limit=4_000),
    )
    # [ghi chú] Capitol không lưu token của bản tóm tắt ở đâu ⇒ ghi MỘT dòng log
    # để trang kéo–thả (chay_thu/giao_dien/may_chu.py) tính tiền. Không đổi hành vi; lỗi khi ghi log thì bỏ qua.
    try:
        u = result.usage()
        logger.info(
            "[DIGEST_USAGE] session=%s node=%s model=%s requests=%s input=%s output=%s cache_read=%s reasoning=%s",
            session_id, node_id, _ten_mo_hinh, getattr(u, "requests", 0),   # [va_nguoi_gac_claude] tên mô hình THẬT đã gọi (trước: luôn in biến cấu hình)
            getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0), getattr(u, "cache_read_tokens", 0),
            (getattr(u, "details", None) or {}).get("reasoning_tokens", 0),
        )
    except Exception:  # noqa: BLE001
        pass
    output = result.output
    assert isinstance(output, ReviewDigest)  # noqa: S101 - narrows Any for mypy
    return output


def _content_hash(review_instructions: str, checkpoint_text: str) -> str:
    """Stable key component tying a cached brief to its exact inputs."""
    raw = f"{review_instructions}\x00{checkpoint_text}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:16]


def compose_review_display(review_instructions: str, digest_body: str) -> str:
    """Assemble the human-facing text: author's instructions verbatim + brief.

    The instructions are re-attached deterministically here — the model is
    told never to restate them — so the author's wording reaches the
    reviewer byte-identical, exactly as the pre-digest concatenation did.
    """
    if review_instructions.strip():
        return f"{review_instructions}{DIGEST_SECTION_SEPARATOR}{digest_body}"
    return digest_body


async def build_review_digest(
    *,
    session_id: str,
    node_id: str,
    review_instructions: str,
    checkpoint_text: str,
) -> str | None:
    """Build (or fetch the cached) review-digest BODY for one checkpoint.

    Args:
        session_id: Run session, for the cache key and logging.
        node_id: Checkpoint node instance id, same.
        review_instructions: The author's static ``preview_context`` value,
            passed as the extraction spec. May be empty.
        checkpoint_text: Everything the reviewer would otherwise see — the
            instructions with the upstream step output appended.

    Returns the digest markdown body (WITHOUT the author's instructions —
    compose with :func:`compose_review_display`), or ``None`` when the
    caller must fall back to the raw text: nothing beyond the author's own
    instructions to digest, or the model was unreachable after the bounded
    retries. Never raises.
    """
    # Nothing bound upstream (static-only checkpoint): the text is just the
    # author's instructions, so there is nothing to distil.
    if not (checkpoint_text or "").strip():
        return None
    if checkpoint_text.strip() == (review_instructions or "").strip():
        return None

    from services.redis.client import RedisServiceSingleton

    redis_client = RedisServiceSingleton()
    content_hash = _content_hash(review_instructions, checkpoint_text)

    cached = await redis_client.get_intervention_digest(
        session_id, node_id, content_hash
    )
    if cached:
        logger.info(
            "HITL review digest cache hit for session %s node %s", session_id, node_id
        )
        return cached

    from workflow.node.base.agent.model_resolution import chay_thu_loi_ro_nguoi_gac, chay_thu_mo_hinh_nguoi_gac

    _khong_cat = bool(await chay_thu_mo_hinh_nguoi_gac("human_intervention_node", None, {"timeout": 600}))   # file mô hình khối bảo Claude ⇒ không cắt giữa
    user_prompt = _build_user_prompt(review_instructions, checkpoint_text, khong_cat=_khong_cat)

    timeout_s = float(settings.HITL_REVIEW_DIGEST_TIMEOUT_S)
    attempts = max(1, int(settings.HITL_REVIEW_DIGEST_RETRIES) + 1)

    digest: ReviewDigest | None = None
    for attempt in range(1, attempts + 1):
        try:
            digest = await asyncio.wait_for(
                _invoke_digest(user_prompt, session_id=session_id, node_id=node_id), timeout=timeout_s
            )
            break
        except Exception as exc:  # noqa: BLE001 - bounded, then fails open to raw
            _ro = chay_thu_loi_ro_nguoi_gac(exc, "Bản tóm tắt cho Người duyệt", len(user_prompt.encode()))
            if _ro:   # [bản 2] lời rõ vào log; hành vi như gốc: không có bản tóm tắt ⇒ người duyệt thấy NGUYÊN VĂN (không ai được tự cho qua)
                logger.warning("[NGUOI_GAC_LOI_RO] %s", _ro)
            logger.warning(
                "HITL review digest attempt %d/%d failed for session %s node %s: %s",
                attempt,
                attempts,
                session_id,
                node_id,
                exc,
            )
    if digest is None:
        return None

    body = digest.digest_markdown.strip()
    if not body:
        if not digest.all_clear:
            # An empty non-all-clear brief is worse than the raw text.
            return None
        body = _ALL_CLEAR_FALLBACK_MARKDOWN

    await redis_client.set_intervention_digest(session_id, node_id, content_hash, body)
    logger.info(
        "HITL review digest built for session %s node %s "
        "(all_clear=%s, %d chars from %d checkpoint chars)",
        session_id,
        node_id,
        digest.all_clear,
        len(body),
        len(checkpoint_text),
    )
    return body
