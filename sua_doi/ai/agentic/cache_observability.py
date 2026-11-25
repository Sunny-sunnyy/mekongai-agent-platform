"""Lightweight prompt/KV-cache usage observability for agent request paths.

pydantic-ai normalises provider cache counters onto its usage objects
(``RunUsage`` / ``RequestUsage``) as ``cache_read_tokens`` /
``cache_write_tokens``, and also exposes the raw provider keys in
``usage.details`` (Anthropic ``cache_read_input_tokens`` /
``cache_creation_input_tokens``; OpenAI ``cached_tokens``). Nothing in the app
logged these before, so the prompt-cache hit rate could not be measured from
app logs/metrics.

We emit a single structured log line per completed run so the hit rate is
measurable per node sub-type and provider. Langfuse independently captures the
same counters via the configured OTel auto-instrumentation, so this is a
lightweight cross-check rather than a parallel metrics pipeline.

``log_cache_usage`` also records Prometheus metrics on the *same* code path (no
duplicated call sites): it both logs the line and records into the process-default
Prometheus registry, which the worker already exposes on ``/metrics``
(``services/worker/worker.py``). The headline ``agentic_prompt_cache_read_ratio``
histogram plus the ``agentic_prompt_cache_input_tokens_total`` counter are
labelled by ``node_sub_type``, ``provider`` and ``model`` so a dashboard can
slice the hit rate per agent, provider family and model.

The ``provider``/``model`` labels reflect the model that ACTUALLY served the run
(the agent nodes pass :func:`resolve_executor_model`, so a Bedrock-failover run
is labelled ``bedrock`` — not the configured Anthropic primary). Providers with
no ``cache_write`` signal (OpenAI/OpenRouter) simply record ``cache_read`` +
``uncached``; the ``read/write`` reuse ratio is only meaningful for
Anthropic/Bedrock.
"""

from __future__ import annotations

import logging
from typing import Any

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

# Cache-relevant input-token disposition keys (used as the ``disposition`` label
# on the token counter).
_DISPOSITION_CACHE_READ = "cache_read"
_DISPOSITION_CACHE_WRITE = "cache_write"
_DISPOSITION_UNCACHED = "uncached"

# Provider families we deliberately keep as a bounded label set so the metric
# stays low-cardinality. ``_provider_family`` normalises model names / arbitrary
# provider strings down to one of these (or ``other`` / ``unknown``).
_KNOWN_PROVIDER_FAMILIES = frozenset({"anthropic", "bedrock", "openai", "openrouter"})

# Bedrock cross-region inference-profile prefixes. Reused in two places:
#   * ``_provider_family`` — a model id carrying one of these is Bedrock-served.
#   * ``_model_label`` — stripped off so Anthropic-direct and Bedrock report the
#     SAME base model id.
_BEDROCK_PROFILE_PREFIXES = ("global.", "us.", "eu.", "apac.")

# Registered once on the process-default registry at import time, mirroring the
# module-level pattern in ``services/worker/worker.py`` so the existing
# ``generate_latest()`` /metrics endpoint exports them with no extra wiring.
CACHE_READ_RATIO = Histogram(
    "agentic_prompt_cache_read_ratio",
    "Prompt/KV cache read ratio (cache_read / total billed input) per completed "
    "agent run.",
    labelnames=("node_sub_type", "provider", "model"),
    buckets=(0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0),
)

CACHE_INPUT_TOKENS = Counter(
    "agentic_prompt_cache_input_tokens_total",
    "Input tokens by cache disposition per completed agent run "
    "(disposition: cache_read | cache_write | uncached).",
    labelnames=("node_sub_type", "provider", "model", "disposition"),
)

# NOTE (multi-replica): every metric here is a per-process counter/histogram.
# In production each temporal-worker pod exposes its own /metrics and Prometheus
# scrapes each pod as a separate target (distinct ``instance`` label), so all
# dashboards MUST aggregate across pods at query time with ``sum`` / ``sum by``.
# The dashboards compute every value over the selected time range with
# ``increase(metric[$__range])`` — reset-aware (spans pod restarts) AND fleet-safe
# (sums across replicas) — e.g. hit rate = ``sum(increase(cache_read[$__range])) /
# sum(increase(total[$__range]))``. This is why there is deliberately NO
# per-process "last value" Gauge: a ``.set()``-per-run gauge is last-writer-wins
# per process, resets on restart, and produces one noisy series per pod with no
# meaningful cross-pod aggregate. Counters + a histogram avoid all three.


def _provider_family(provider: str | None) -> str:
    """Normalise a provider/model string to a bounded provider-family label.

    Call sites pass the model id that actually served the run — the agent nodes
    resolve it via :func:`resolve_executor_model` (e.g. ``"claude-sonnet-4-6"``,
    ``"gpt-5.5-2026-04-23"``, ``"global.anthropic.claude-sonnet-4-6"``) — or, on
    the Skills path, a bare family literal (``"anthropic"``). We collapse all of
    these to the billing-relevant family so the Prometheus label set stays small.

    Note: because the id is the EXECUTOR's (not the configured primary), a
    ``FallbackModel`` that failed over to Bedrock reports the Bedrock id (e.g.
    ``global.anthropic.claude-sonnet-4-6``) and is correctly classified
    ``bedrock`` here — not mislabeled ``anthropic``.
    """
    if not provider:
        return "unknown"
    p = str(provider).lower()
    if p in _KNOWN_PROVIDER_FAMILIES:
        return p
    # Bedrock model ids carry a provider namespace ("anthropic.claude-...") and
    # usually a cross-region inference-profile prefix (_BEDROCK_PROFILE_PREFIXES),
    # unlike Anthropic-direct ids which are bare ("claude-..."). Check this BEFORE
    # the generic claude/anthropic test so Bedrock wins.
    if "bedrock" in p or "anthropic." in p or p.startswith(_BEDROCK_PROFILE_PREFIXES):
        return "bedrock"
    if "claude" in p or "anthropic" in p:
        return "anthropic"
    if p.startswith(("gpt", "o1", "o3", "o4", "chatgpt")) or "openai" in p:
        return "openai"
    if "gemini" in p or "openrouter" in p or "nemotron" in p or "/" in p:
        return "openrouter"
    return "other"


def _model_label(provider: str | None) -> str:
    """Best-effort bounded model name for the ``model`` metric label.

    Call sites pass the resolved model name as ``provider`` (e.g.
    ``"claude-sonnet-4-6"`` or the Bedrock id
    ``"global.anthropic.claude-sonnet-4-6"``). We strip the Bedrock
    inference-profile prefix and the ``anthropic.`` namespace so Anthropic-direct
    and Bedrock report the SAME base model (``claude-sonnet-4-6``) — letting a
    dashboard slice by model independently of provider.

    Returns ``"unknown"`` when the caller passed a bare provider family literal
    (e.g. the Skills path passes ``provider="anthropic"``) — no model is known.
    """
    if not provider:
        return "unknown"
    p = str(provider)
    if p.lower() in _KNOWN_PROVIDER_FAMILIES:
        return "unknown"
    low = p.lower()
    for pref in _BEDROCK_PROFILE_PREFIXES:
        if low.startswith(pref):
            p = p[len(pref) :]
            low = p.lower()
            break
    if low.startswith("anthropic."):
        p = p[len("anthropic.") :]
    return p


def _record_cache_metrics(
    stats: dict[str, Any], node_sub_type: str, provider: str | None
) -> None:
    """Record the computed cache stats into Prometheus. Best-effort."""
    try:
        family = _provider_family(provider)
        model = _model_label(provider)
        if stats["total_input_tokens"] > 0:
            CACHE_READ_RATIO.labels(
                node_sub_type=node_sub_type, provider=family, model=model
            ).observe(stats["cache_read_ratio"])
        for disposition, key in (
            (_DISPOSITION_CACHE_READ, "cache_read_tokens"),
            (_DISPOSITION_CACHE_WRITE, "cache_write_tokens"),
            (_DISPOSITION_UNCACHED, "uncached_input_tokens"),
        ):
            count = stats.get(key, 0)
            if count:
                CACHE_INPUT_TOKENS.labels(
                    node_sub_type=node_sub_type,
                    provider=family,
                    model=model,
                    disposition=disposition,
                ).inc(count)
    except Exception:  # pragma: no cover - metrics must never break a run
        logger.debug("Error recording cache metrics", exc_info=True)


def summarize_cache_usage(usage: Any) -> dict[str, Any]:
    """Extract cached vs. uncached input-token counters from a usage object.

    Accepts any pydantic-ai usage object (duck-typed) and falls back to ``0``
    for any missing counter so it never raises on an unexpected shape.

    The headline metric is ``cache_read_ratio`` = cache-read tokens over the
    total input the request was billed for.

    Token-accounting note: pydantic-ai's ``RequestUsage`` /
    ``RunUsage`` (built via genai-prices) reports ``input_tokens`` **inclusive
    of cache** -- i.e. ``input_tokens == uncached + cache_read + cache_write``,
    NOT just the uncached portion. (The raw uncached count lives in
    ``usage.details['input_tokens']``; the Anthropic API's top-level
    ``input_tokens`` is the exclusive one.) So the billed total is
    ``input_tokens`` itself, and the uncached (full-price) slice is
    ``input_tokens - cache_read - cache_write``. Verified end-to-end against the
    Anthropic response and Langfuse, which both decompose it the same way.
    """
    cache_read = int(getattr(usage, "cache_read_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_write_tokens", 0) or 0)
    # pydantic-ai input_tokens is cache-INCLUSIVE -> it is the billed total.
    total_input = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

    uncached_input = max(total_input - cache_read - cache_write, 0)
    cache_read_ratio = (cache_read / total_input) if total_input else 0.0

    return {
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "uncached_input_tokens": uncached_input,
        "total_input_tokens": total_input,
        "output_tokens": output_tokens,
        "cache_read_ratio": round(cache_read_ratio, 4),
    }


def resolve_executor_model(result: Any) -> str | None:
    """Best-effort: the model id that ACTUALLY served the run, read from
    pydantic-ai's result messages (the last ``ModelResponse.model_name``).

    Why not ``primary_model_name``: that peeks at the *configured* primary of a
    :class:`FallbackModel`, so a Bedrock→Anthropic (or Anthropic→Bedrock)
    failover is mislabeled with the primary. pydantic-ai's ``FallbackModel``
    returns the *successful child's* ``ModelResponse``, whose ``model_name`` is
    the true executor (e.g. ``global.anthropic.claude-…`` on Bedrock,
    ``claude-…`` on Anthropic-direct) — the same value it stamps on the OTel
    span (which is why Langfuse attributes it correctly). ``_provider_family`` /
    ``_model_label`` already classify that string into provider + model.

    Fully defensive: duck-typed (no pydantic-ai import) and returns ``None`` on
    anything unexpected so callers fall back to their previous provider label —
    observability must never break a run.
    """
    try:
        for msg in reversed(result.all_messages()):
            name = getattr(msg, "model_name", None)  # set only on ModelResponse
            if name:
                return str(name)
    except Exception:  # pragma: no cover - defensive, must never break a run
        return None
    return None


def log_cache_usage(
    usage: Any,
    *,
    node_type: str,
    provider: str | None = None,
    log: logging.Logger = logger,
) -> dict[str, Any]:
    """Emit a structured cache-usage log line and return the computed stats.

    Best-effort: a ``None`` usage or any extraction failure is swallowed so
    observability can never break an agent run.
    """
    if usage is None:
        return {}
    try:
        stats = summarize_cache_usage(usage)
    except Exception:  # pragma: no cover - defensive, must never break a run
        return {}

    _record_cache_metrics(stats, node_sub_type=node_type, provider=provider)

    log.info(
        "[CACHE_USAGE] node_type=%s provider=%s cache_read=%d cache_write=%d "
        "uncached_input=%d total_input=%d output=%d cache_read_ratio=%.4f session=%s",
        node_type,
        provider or "unknown",
        stats["cache_read_tokens"],
        stats["cache_write_tokens"],
        stats["uncached_input_tokens"],
        stats["total_input_tokens"],
        stats["output_tokens"],
        stats["cache_read_ratio"],
        _phien_hien_tai(),
    )
    return stats


def _phien_hien_tai() -> str:
    """[ghi chú] Mã phiên (session) của lượt đang chạy, để sổ tiền ghép được dòng [CACHE_USAGE] với lượt
    (trước đây giao_dien/so_skill.py phải ghi MỨC TỐI ĐA cho khối hộp phiên vì log không mang mã lượt). Lấy từ mã workflow Temporal
    = f"{WORKFLOW_ID_PREFIX}-{session_id}" (temporal/config.py:33-40) khi đang ở trong activity; ngoài activity / lỗi ⇒ "-".
    Chỉ ghi log — không đổi luồng chạy (mọi lỗi nuốt, như phần còn lại của file)."""
    try:
        from temporalio import activity

        wid = activity.info().workflow_id or ""
        from temporal.config import WORKFLOW_ID_PREFIX

        tien_to = f"{WORKFLOW_ID_PREFIX}-"
        return wid[len(tien_to):] if wid.startswith(tien_to) else (wid or "-")
    except Exception:  # noqa: BLE001 — ngoài activity (RuntimeError) hoặc thiếu module: không có mã phiên
        return "-"
