"""Self-consistency vote wrapper for LLM-judge calls.

Every judge call goes through ``judge_with_vote(...)`` which runs N
parallel samples and surfaces the sample with the highest
``overall_score`` as the result. The other samples are persisted on
``result['_judge_samples']`` for internal audit only and never
surface to the user.

Why pick a whole sample (not per-field MAX): keeping all surfaced
fields from a single sample guarantees internal consistency.
Per-dim scores, overall_score, and the explanation all come from
the same judgment, so the math always adds up and the narrative
references the per-dim values you actually see.

Why pick the highest-overall sample (not median, not min, not
mean): gives the workflow the benefit of the doubt. If the model
COULD have produced a good evaluation on any of the N samples,
that's what we surface. Reduces FALSE NEGATIVES \u2014 a noisy
judge giving a one-off bad score on actually-good output is
dropped.

Usage:

    async def call_once() -> dict:
        response = await llm.aquery(...)
        return parse_json_response(response.content)

    result = await judge_with_vote(call_once, n=3)

Configuration knobs:
    EVAL_JUDGE_VOTE_N (env, default 3) \u2014 ops can throttle
    if Anthropic concurrency or cost becomes an issue. Tests pass
    n=1 to stay fast.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

from eval_srvc.logging_utils import Logger

logger = Logger.get_logger()


# Default vote count. Operators can override via env to throttle
# Anthropic concurrency / cost without code changes.
def _default_vote_n() -> int:
    raw = os.environ.get("EVAL_JUDGE_VOTE_N", "3")
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return 3
    return max(1, min(n, 9))  # clamp to a sane range


def _is_numeric(v: Any) -> bool:
    """Treat bool as non-numeric (Python's ``isinstance(True, int)``
    is True, which would corrupt comparisons against verdict-typed
    fields)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _winning_sample_index(samples: list[dict]) -> int:
    """Pick the sample with the highest ``overall_score``.

    Ties are broken by lower index (stable). Samples without a
    numeric overall_score sort last. If NO sample has a numeric
    overall_score, the first sample wins by default.
    """
    scores = [
        (i, s.get("overall_score") if isinstance(s, dict) else None)
        for i, s in enumerate(samples)
    ]
    numeric = [(i, sc) for i, sc in scores if _is_numeric(sc)]
    if not numeric:
        return 0
    # [VÁ CỦA MÌNH 06/11/2026 — nhóm kỹ thuật, chay_thu/eval_service/HUONG_DAN.md] lấy lần CAO NHẤT đẩy điểm lên + che dao động (bao_cao/he_thong/eval-service mục 4)
    # ⇒ EVAL_VOTE_CHON=trung_vi | thap_nhat để đo "tin được không"; mặc định giữ đúng Capitol (cao_nhat) để so được với họ.
    chon = os.environ.get("EVAL_VOTE_CHON", "cao_nhat")
    if chon in ("trung_vi", "thap_nhat"):
        xep = sorted(numeric, key=lambda t: (t[1], t[0]))
        return (xep[(len(xep) - 1) // 2] if chon == "trung_vi" else xep[0])[0]
    return max(numeric, key=lambda t: (t[1], -t[0]))[0]


def _reduce_dict(samples: list[dict]) -> dict:
    """Pick the sample with the highest ``overall_score`` and return
    it wholesale.

    Keeping all surfaced fields from a single sample guarantees
    internal consistency: per-dim scores, overall_score, and the
    explanation all come from the same judgment, so the math always
    adds up and the narrative references the per-dim values the
    user actually sees.
    """
    if not samples:
        return {}
    winning_idx = _winning_sample_index(samples)
    winner = samples[winning_idx]
    if not isinstance(winner, dict):
        # Defensive: should not happen because the caller filters
        # successes to dicts, but if it does, fall back to the first
        # dict-shaped sample.
        for s in samples:
            if isinstance(s, dict):
                return dict(s)
        return {}
    return dict(winner)


async def judge_with_vote(
    call_fn: Callable[[], Awaitable[dict]],
    *,
    n: int | None = None,
    persist_samples: bool = True,
) -> dict:
    """Run ``call_fn`` ``n`` times in parallel and surface the
    sample with the highest ``overall_score``.

    Returns the winning sample wholesale so all surfaced fields
    (per-dim scores, overall_score, explanation) come from the
    same judgment \u2014 internally consistent by construction.
    The other samples are persisted on ``result['_judge_samples']``
    for internal audit only and never surface to the user.

    Parallelism: all N calls fire concurrently via
    ``asyncio.gather(*, return_exceptions=True)``. Per-call failures
    do NOT bring down the batch; failed samples are dropped (logged
    at WARNING) and only the successful ones are considered. If ALL
    N calls fail, the exception from the first one is re-raised so
    the outer try/except in the eval module can surface a clean
    error row to the user.

    Args:
        call_fn: zero-arg coroutine that returns the structured
            payload. Each invocation is independent (no shared
            state) so the parallel calls are safe.
        n: number of samples; defaults to ``EVAL_JUDGE_VOTE_N`` env
            var (default 3, clamped 1-9).
        persist_samples: if False, omit ``_judge_samples`` from the
            result (saves a few KB per row when the audit envelope
            isn't needed).

    Returns:
        A single dict with the same shape as ``call_fn()`` would
        return, with the addition of ``_judge_samples`` when
        ``persist_samples=True``.
    """
    vote_n = n if n is not None else _default_vote_n()
    if vote_n <= 1:
        # Vote count of 1 \u2014 no point in the gather overhead.
        # Useful for tests and emergency env-var throttling.
        return await call_fn()

    raw_results = await asyncio.gather(
        *(call_fn() for _ in range(vote_n)),
        return_exceptions=True,
    )

    # Partition successful samples vs exceptions.
    successes: list[dict] = []
    failures: list[BaseException] = []
    samples_envelope: list[dict] = []
    for idx, r in enumerate(raw_results):
        if isinstance(r, BaseException):
            failures.append(r)
            samples_envelope.append({
                "sample_index": idx,
                "error": f"{type(r).__name__}: {r}",
            })
        else:
            if isinstance(r, dict):
                successes.append(r)
                samples_envelope.append({
                    "sample_index": idx,
                    "payload": r,
                })
            else:
                # Non-dict success \u2014 unusual but tolerate.
                samples_envelope.append({
                    "sample_index": idx,
                    "error": (
                        f"non-dict sample (got "
                        f"{type(r).__name__}); cannot reduce"
                    ),
                })

    if not successes:
        # All samples failed. Surface the first failure so the
        # caller's outer try/except can convert it to a clean
        # verdict=error row.
        first = failures[0] if failures else RuntimeError(
            f"All {vote_n} judge samples returned non-dict shapes; "
            "cannot reduce."
        )
        logger.error(
            "judge_with_vote: ALL %d samples failed (first error: "
            "%s: %s); re-raising for caller to surface as "
            "verdict=error.",
            vote_n, type(first).__name__, first,
        )
        raise first

    if failures:
        # Partial failure \u2014 reduce on the survivors and log.
        logger.warning(
            "judge_with_vote: %d of %d samples failed; reducing on "
            "the %d survivors. First failure: %s: %s",
            len(failures), vote_n, len(successes),
            type(failures[0]).__name__, failures[0],
        )

    reduced = _reduce_dict(successes)

    if persist_samples:
        reduced["_judge_samples"] = samples_envelope

    return reduced
