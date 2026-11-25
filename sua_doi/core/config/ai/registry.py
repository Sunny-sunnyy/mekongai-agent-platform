"""Single source of truth for supported AI models.

The provider-specific enums (``AnthropicModels``, ``OpenAIModels``,
``OpenRouterModels``) still hold the raw model IDs, including retired /
deprecated ones, because callers need to be able to recognize legacy IDs
in saved workflows.

Two concepts live here and are intentionally separate:

* ``REGISTRY`` — the *catalog*. Maps every known model ID (including
  deprecated ones) to its ``ModelSpec``. ``get_spec()`` and the provider-
  specific settings builders in ``src/ai/agentic/models/*.py`` look up
  specs here, so a caller that deserializes a legacy model string into an
  enum member and builds a model directly (bypassing the node-level alias
  remap) still resolves to a valid spec instead of raising ``KeyError``.

  The providers retire models differently. Anthropic retirements keep
  the enum member and its spec in the catalog (direct builder calls with
  the retired ID still work). OpenAI and OpenRouter retirements delete the
  enum member and spec entirely — the retired ID survives only as a string
  key in ``DEPRECATED_OPENAI_MODEL_ALIASES`` /
  ``DEPRECATED_OPENROUTER_MODEL_ALIASES``, so legacy workflows resolve
  through the node-level alias remap before any catalog lookup.

* ``all_model_ids()`` and the named ``*_MODEL_IDS`` subsets — what the UI
  *offers*. These exclude ``_DEPRECATED_MODEL_IDS`` so dropdowns never
  suggest a retired model for a new workflow, plus ``_A2A_ONLY_MODEL_IDS``
  and ``_INTERNAL_ONLY_MODEL_IDS`` so surface-restricted models resolve in
  the catalog without being offered for a user's own runs.

When adding a shape-compatible new model, add its enum member and append
one ``_anthropic_spec`` / ``_openai_spec`` / ``_openrouter_spec`` call to
the ``_ALL_SPECS`` tuple below. No other file needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Literal

from core.config.ai.anthropic import (
    ANTHROPIC_CONTEXT_WINDOWS,
    ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS,
    ANTHROPIC_MAX_OUTPUT_TOKENS,
    DEPRECATED_ANTHROPIC_MODEL_ALIASES,
    AnthropicModels,
)
from core.config.ai.openai import (
    DEPRECATED_OPENAI_MODEL_ALIASES,
    OPENAI_CONTEXT_WINDOWS,
    OPENAI_DEFAULT_MAX_OUTPUT_TOKENS,
    OPENAI_MAX_OUTPUT_TOKENS,
    REGIONAL_UPLIFT_CUTOFF,
    OpenAIModels,
    get_openai_release_date,
)
from core.config.ai.openrouter import (
    DEPRECATED_OPENROUTER_MODEL_ALIASES,
    OPENROUTER_CONTEXT_WINDOWS,
    OPENROUTER_DEFAULT_MAX_OUTPUT_TOKENS,
    OPENROUTER_MAX_OUTPUT_TOKENS,
    OpenRouterModels,
)


class Provider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENROUTER = "openrouter"


class CostTier(str, Enum):
    """Relative cost tier for model recommendations (cheapest → most expensive)."""

    BUDGET = "budget"
    MID = "mid"
    PREMIUM = "premium"
    FLAGSHIP = "flagship"


@dataclass(frozen=True)
class ModelSpec:
    """Metadata for a single supported model.

    Fields are grouped by provider applicability. Fields not relevant to a
    given provider stay at their defaults (and are ignored by that provider's
    instantiation function).
    """

    id: str
    provider: Provider
    max_output_tokens: int
    # Total context window (input + output tokens) the model accepts in a
    # single request. Used by the context-management capability to decide
    # when to summarize message history before the next model request.
    # Sourced from per-provider ``*_CONTEXT_WINDOWS`` tables; unrecognised
    # models fall back to the smallest currently-shipping window so the
    # manager errs on the side of summarizing earlier.
    context_window: int
    cost_tier: CostTier = CostTier.MID

    # Anthropic-specific policy.
    # ``thinking_mode`` selects the shape of the ``anthropic_thinking``
    # parameter and the surrounding sampling config:
    #   "legacy"   — {"type": "enabled", "budget_tokens": N} plus
    #                ``temperature=1`` and stripped ``top_p``. Used by
    #                Opus 4.6, Sonnet 4.6, and every earlier model.
    #   "adaptive" — {"type": "adaptive", "display": <thinking_display>}
    #                with no sampling-param overrides. Opus 4.7+ rejects
    #                legacy thinking and non-default sampling params with
    #                a 400, so this is the only mode that works there.
    #   ``None``   — the model does not use extended thinking.
    # ``thinking_budget_tokens`` is only consulted for the legacy path.
    # ``thinking_display`` is only consulted for the adaptive path; when
    # ``None`` the API defaults to omitting thinking content from the
    # response. Recommended value: ``"summarized"``.
    # ``anthropic_beta_headers`` are merged into the ``anthropic-beta``
    # header in the order given; order matters and is preserved.
    thinking_mode: Literal["legacy", "adaptive"] | None = None
    thinking_budget_tokens: int | None = None
    thinking_display: str | None = None
    anthropic_beta_headers: tuple[str, ...] = ()

    # OpenAI-specific policy.
    # ``reasoning_effort`` is set on the model settings when non-``None``
    # ("low" / "medium" / "high"). ``supports_parallel_tool_calls`` is
    # ``False`` for o-series reasoning models that reject the parameter.
    reasoning_effort: str | None = None
    supports_parallel_tool_calls: bool = True
    # Provider release date, populated for OpenAI only. Decides the 10% regional
    # data-residency price uplift, which applies to models released on or after
    # ``REGIONAL_UPLIFT_CUTOFF`` -- see ``is_regional_uplift_eligible``. ``None``
    # on providers where it carries no pricing meaning (Anthropic, OpenRouter).
    release_date: date | None = None

    # OpenRouter-specific policy. Set on the model settings when non-``None``.
    openrouter_reasoning: dict | None = None

    # Fast-mode policy. When True, ``anthropic_speed='fast'`` is injected
    # into model settings automatically. The Anthropic API does not expose a
    # separate model ID for fast mode — the ``-fast`` suffix in our registry
    # IDs is internal only. Use ``base_model_id`` to retrieve the actual API
    # ID to pass to AnthropicModel constructors.
    fast_mode: bool = False
    base_model_id: str | None = None
    # True on every spec that the Anthropic API accepts ``speed='fast'`` for.
    # Used to gate caller-supplied ``fast_mode=True`` so it cannot be forwarded
    # to models that don't support fast mode (e.g. Sonnet, Haiku).
    supports_fast_mode: bool = False


# ---------------------------------------------------------------------------
# Shared policy constants.
# ---------------------------------------------------------------------------

# The one default Anthropic beta: interleaved thinking between tool calls for
# the legacy budget-thinking models (no-op on adaptive models). The
# code-execution / files-api betas that used to ride here served the retired
# native skills path -- tools run in E2B now, so nothing declares those
# features and the headers were dead weight (and rejected by Vertex-backed
# gateways).
_ANTHROPIC_DEFAULT_BETAS: tuple[str, ...] = ("interleaved-thinking-2025-05-14",)

# Beta header added for models that support the 1M-token context window.
# Prepended to the default list to preserve today's on-the-wire order.
_ANTHROPIC_1M_CONTEXT_BETA: str = "context-1m-2025-08-07"

_ANTHROPIC_BETAS_WITH_1M_CONTEXT: tuple[str, ...] = (
    _ANTHROPIC_1M_CONTEXT_BETA,
    *_ANTHROPIC_DEFAULT_BETAS,
)

# Thinking budget (tokens) shared by every thinking-enabled Anthropic model.
_ANTHROPIC_THINKING_BUDGET: int = 25_000

# Uniform OpenRouter reasoning config applied to every OpenRouter model today.
# Kept per-model so future tuning (e.g. disabling reasoning on small models)
# is a spec edit rather than a code change.
_OPENROUTER_DEFAULT_REASONING: dict = {"enabled": True, "effort": "high"}


# Model IDs that have been retired, filtered out of ``all_model_ids()`` and
# the named ``*_MODEL_IDS`` subsets so the UI stops offering them for new
# workflows. Retired Anthropic IDs keep their enum member and spec in
# ``REGISTRY`` (this set is what hides them); retired OpenAI and OpenRouter
# IDs have no enum member or spec at all and rely on the node-level alias
# remap instead (see the module docstring).
_DEPRECATED_MODEL_IDS: frozenset[str] = frozenset(
    {
        *DEPRECATED_ANTHROPIC_MODEL_ALIASES,
        *DEPRECATED_OPENAI_MODEL_ALIASES,
        *DEPRECATED_OPENROUTER_MODEL_ALIASES,
    }
)

# Model IDs exposed ONLY to the A2A orchestrator-agent surface (ENG-3242).
# Their specs live in ``REGISTRY`` so the orchestrator's model builder can
# resolve them, but they are filtered out of ``all_model_ids()`` so no other
# surface (workflow-canvas agent nodes, skill-backed document nodes, cost-tier
# prompt recommendations) offers them. The A2A-facing subset that DOES offer
# them is ``A2A_ORCHESTRATOR_MODEL_IDS`` below.
_A2A_ONLY_MODEL_IDS: frozenset[str] = frozenset({AnthropicModels.CLAUDE_FABLE_5.value})

# Model IDs used by INTERNAL platform features only — never user-selectable.
# Their specs live in ``REGISTRY`` so the owning feature's model builder can
# resolve them (and apply the spec's reasoning/token policy), but they are
# filtered out of ``all_model_ids()`` so no dropdown, skill subset, or
# cost-tier prompt recommendation offers them for a user's own runs.
#   * ``gpt-5.6-sol`` — the Human Intervention review digest
#     (``HITL_REVIEW_DIGEST_MODEL``).
_INTERNAL_ONLY_MODEL_IDS: frozenset[str] = frozenset({OpenAIModels.GPT_5_6_SOL.value})


# ---------------------------------------------------------------------------
# Spec builders.
# ---------------------------------------------------------------------------


def _max_tokens(m: AnthropicModels | OpenAIModels | OpenRouterModels) -> int:
    if isinstance(m, AnthropicModels):
        return ANTHROPIC_MAX_OUTPUT_TOKENS.get(m, ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS)
    if isinstance(m, OpenAIModels):
        return OPENAI_MAX_OUTPUT_TOKENS.get(m, OPENAI_DEFAULT_MAX_OUTPUT_TOKENS)
    return OPENROUTER_MAX_OUTPUT_TOKENS.get(m, OPENROUTER_DEFAULT_MAX_OUTPUT_TOKENS)


def _context_window(
    m: AnthropicModels | OpenAIModels | OpenRouterModels,
) -> int:
    """Return the provider's documented context window for ``m``.

    Direct dict lookup — no default fallback. Every value across the
    three model enums is expected to have an explicit entry in its
    provider's ``*_CONTEXT_WINDOWS`` table; a missing entry is a code
    bug. Raising the natural :class:`KeyError` at import time (the
    registry builds every spec at module load) surfaces it in CI so
    the compaction capability never operates on a guessed budget for
    a newly-added model.
    """
    if isinstance(m, AnthropicModels):
        return ANTHROPIC_CONTEXT_WINDOWS[m]
    if isinstance(m, OpenAIModels):
        return OPENAI_CONTEXT_WINDOWS[m]
    return OPENROUTER_CONTEXT_WINDOWS[m]


def _anthropic_spec(
    m: AnthropicModels,
    *,
    cost_tier: CostTier = CostTier.MID,
    thinking_mode: Literal["legacy", "adaptive"] = "legacy",
    thinking_display: str | None = None,
    supports_1m_context: bool = False,
    fast_mode: bool = False,
    base_model_id: str | None = None,
    supports_fast_mode: bool = False,
) -> ModelSpec:
    return ModelSpec(
        id=m.value,
        provider=Provider.ANTHROPIC,
        max_output_tokens=_max_tokens(m),
        context_window=_context_window(m),
        cost_tier=cost_tier,
        thinking_mode=thinking_mode,
        thinking_budget_tokens=(
            _ANTHROPIC_THINKING_BUDGET if thinking_mode == "legacy" else None
        ),
        thinking_display=thinking_display,
        anthropic_beta_headers=(
            _ANTHROPIC_BETAS_WITH_1M_CONTEXT
            if supports_1m_context
            else _ANTHROPIC_DEFAULT_BETAS
        ),
        fast_mode=fast_mode,
        base_model_id=base_model_id,
        supports_fast_mode=supports_fast_mode,
    )


def _openai_spec(
    m: OpenAIModels,
    *,
    cost_tier: CostTier = CostTier.MID,
    reasoning_effort: str | None = None,
    supports_parallel_tool_calls: bool = True,
) -> ModelSpec:
    return ModelSpec(
        id=m.value,
        provider=Provider.OPENAI,
        max_output_tokens=_max_tokens(m),
        context_window=_context_window(m),
        cost_tier=cost_tier,
        reasoning_effort=reasoning_effort,
        supports_parallel_tool_calls=supports_parallel_tool_calls,
        # Raises at import time if the model has no date entry — see
        # ``get_openai_release_date``.
        release_date=get_openai_release_date(m),
    )


def _openrouter_spec(
    m: OpenRouterModels,
    *,
    cost_tier: CostTier = CostTier.MID,
) -> ModelSpec:
    return ModelSpec(
        id=m.value,
        provider=Provider.OPENROUTER,
        max_output_tokens=_max_tokens(m),
        context_window=_context_window(m),
        cost_tier=cost_tier,
        openrouter_reasoning=_OPENROUTER_DEFAULT_REASONING,
    )


# ---------------------------------------------------------------------------
# The registry — the single source of truth for supported models.
# Includes deprecated entries so legacy lookups still resolve; see the
# module docstring for how the offered / catalog split is expressed.
# ---------------------------------------------------------------------------


_ALL_SPECS: tuple[ModelSpec, ...] = (
    # Anthropic — all thinking-enabled today. The ``supports_1m_context``
    # parameter is available on ``_anthropic_spec`` for when a model should
    # receive the ``context-1m`` beta header; no model uses it today.
    _anthropic_spec(AnthropicModels.CLAUDE_4_5_HAIKU, cost_tier=CostTier.BUDGET),
    # Fable 5 (launched 2026-06-09, redeployed 2026-07-01) shares the Sonnet 5 /
    # Opus 4.7+ API shape: adaptive thinking only (always on), 128K max output,
    # 1M context window by default, no fast-mode variant. Priced above Opus 4.8
    # ($10/$50 per MTok), hence flagship tier. Offered ONLY on the A2A
    # orchestrator-agent surface — see ``_A2A_ONLY_MODEL_IDS`` /
    # ``A2A_ORCHESTRATOR_MODEL_IDS`` (ENG-3242).
    _anthropic_spec(
        AnthropicModels.CLAUDE_FABLE_5,
        cost_tier=CostTier.FLAGSHIP,
        thinking_mode="adaptive",
        thinking_display="summarized",
    ),
    # Sonnet 5 uses the same adaptive-thinking shape as Opus 4.7/4.8 (extended
    # thinking is not supported) and ships with a 1M context window by default.
    _anthropic_spec(
        AnthropicModels.CLAUDE_SONNET_5,
        cost_tier=CostTier.MID,
        thinking_mode="adaptive",
        thinking_display="summarized",
    ),
    # Sonnet 5.5 always thinks (thinking cannot be disabled) and rejects forced
    # tool_choice; the adaptive path already strips tool_choice.
    _anthropic_spec(
        AnthropicModels.CLAUDE_SONNET_5_5,
        cost_tier=CostTier.MID,
        thinking_mode="adaptive",
        thinking_display="summarized",
    ),
    _anthropic_spec(AnthropicModels.CLAUDE_SONNET_4_6, cost_tier=CostTier.MID),
    _anthropic_spec(AnthropicModels.CLAUDE_SONNET_4_5, cost_tier=CostTier.MID),
    _anthropic_spec(AnthropicModels.CLAUDE_OPUS_4_5, cost_tier=CostTier.PREMIUM),
    _anthropic_spec(AnthropicModels.CLAUDE_OPUS_4_1, cost_tier=CostTier.PREMIUM),
    _anthropic_spec(
        AnthropicModels.CLAUDE_OPUS_4_6,
        cost_tier=CostTier.FLAGSHIP,
        supports_fast_mode=True,
    ),
    # Opus 4.7 / 4.8 require the new adaptive-thinking shape and reject
    # non-default sampling params; see build_anthropic_agent_settings_dict.
    # Opus 4.8 ships with the 1M context window enabled by default on the
    # Claude API, so no extra beta header is needed for that.
    _anthropic_spec(
        AnthropicModels.CLAUDE_OPUS_4_7,
        cost_tier=CostTier.FLAGSHIP,
        thinking_mode="adaptive",
        thinking_display="summarized",
        # Fast mode is retired for Opus 4.7 (speed='fast' now errors with no
        # fallback). Keep standard 4.7 selectable but never let a caller
        # fast-toggle forward speed='fast' to it.
        supports_fast_mode=False,
    ),
    _anthropic_spec(
        AnthropicModels.CLAUDE_OPUS_4_8,
        cost_tier=CostTier.FLAGSHIP,
        thinking_mode="adaptive",
        thinking_display="summarized",
        supports_fast_mode=True,
    ),
    _anthropic_spec(
        AnthropicModels.CLAUDE_OPUS_5,
        cost_tier=CostTier.FLAGSHIP,
        thinking_mode="adaptive",
        thinking_display="summarized",
        supports_fast_mode=True,
    ),
    # Opus 5.5: same constraints as Sonnet 5.5 (always thinks, no forced
    # tool_choice).
    _anthropic_spec(
        AnthropicModels.CLAUDE_OPUS_5_5,
        cost_tier=CostTier.FLAGSHIP,
        thinking_mode="adaptive",
        thinking_display="summarized",
        supports_fast_mode=True,
    ),
    # Fast-mode variants — same config as their base models but with
    # fast_mode=True so builders inject anthropic_speed='fast' automatically.
    # base_model_id is the actual Anthropic API model ID (no -fast suffix).
    _anthropic_spec(
        AnthropicModels.CLAUDE_OPUS_4_6_FAST,
        cost_tier=CostTier.FLAGSHIP,
        base_model_id=AnthropicModels.CLAUDE_OPUS_4_6.value,
        fast_mode=True,
        supports_fast_mode=True,
    ),
    _anthropic_spec(
        AnthropicModels.CLAUDE_OPUS_4_7_FAST,
        cost_tier=CostTier.FLAGSHIP,
        thinking_mode="adaptive",
        thinking_display="summarized",
        base_model_id=AnthropicModels.CLAUDE_OPUS_4_8.value,
        fast_mode=True,
        supports_fast_mode=True,
    ),
    _anthropic_spec(
        AnthropicModels.CLAUDE_OPUS_4_8_FAST,
        cost_tier=CostTier.FLAGSHIP,
        thinking_mode="adaptive",
        thinking_display="summarized",
        base_model_id=AnthropicModels.CLAUDE_OPUS_4_8.value,
        fast_mode=True,
        supports_fast_mode=True,
    ),
    _anthropic_spec(
        AnthropicModels.CLAUDE_OPUS_5_FAST,
        cost_tier=CostTier.FLAGSHIP,
        thinking_mode="adaptive",
        thinking_display="summarized",
        base_model_id=AnthropicModels.CLAUDE_OPUS_5.value,
        fast_mode=True,
        supports_fast_mode=True,
    ),
    _anthropic_spec(
        AnthropicModels.CLAUDE_OPUS_5_5_FAST,
        cost_tier=CostTier.FLAGSHIP,
        thinking_mode="adaptive",
        thinking_display="summarized",
        base_model_id=AnthropicModels.CLAUDE_OPUS_5_5.value,
        fast_mode=True,
        supports_fast_mode=True,
    ),
    # OpenAI — GPT-5 family gets reasoning_effort=high; o-series reasoning
    # models get medium and reject parallel_tool_calls; GPT-4.1 non-reasoning
    # models receive no reasoning config.
    # GPT-5.6 Sol: flagship of the 5.6 family ($5/$30 per MTok — same slot as
    # 5.5, hence PREMIUM). Internal-only: backs the Human Intervention review
    # digest and is excluded from every UI surface via
    # ``_INTERNAL_ONLY_MODEL_IDS``.
    _openai_spec(
        OpenAIModels.GPT_5_6_SOL, cost_tier=CostTier.PREMIUM, reasoning_effort="high"
    ),
    _openai_spec(
        OpenAIModels.GPT_5_6_TERRA, cost_tier=CostTier.PREMIUM, reasoning_effort="high"
    ),
    _openai_spec(
        OpenAIModels.GPT_5_6_LUNA, cost_tier=CostTier.BUDGET, reasoning_effort="high"
    ),
    # [VÁ CỦA MÌNH 03/11/2026 — chay_thu/sua_doi/README.md] gpt-6-luna, mức suy nghĩ "low" (người vận hành: rẻ, nhanh)
    _openai_spec(
        OpenAIModels.GPT_6_LUNA, cost_tier=CostTier.BUDGET, reasoning_effort="low"
    ),
    _openai_spec(
        OpenAIModels.GPT_5_5, cost_tier=CostTier.PREMIUM, reasoning_effort="high"
    ),
    _openai_spec(
        OpenAIModels.GPT_5_5_PRO, cost_tier=CostTier.FLAGSHIP, reasoning_effort="high"
    ),
    _openai_spec(
        OpenAIModels.GPT_5_4, cost_tier=CostTier.FLAGSHIP, reasoning_effort="high"
    ),
    _openai_spec(
        OpenAIModels.GPT_5_4_PRO, cost_tier=CostTier.FLAGSHIP, reasoning_effort="high"
    ),
    _openai_spec(
        OpenAIModels.GPT_5_2, cost_tier=CostTier.PREMIUM, reasoning_effort="high"
    ),
    _openai_spec(
        OpenAIModels.GPT_5_2_PRO, cost_tier=CostTier.FLAGSHIP, reasoning_effort="high"
    ),
    _openai_spec(
        OpenAIModels.GPT_5, cost_tier=CostTier.PREMIUM, reasoning_effort="high"
    ),
    _openai_spec(
        OpenAIModels.GPT_5_MINI, cost_tier=CostTier.MID, reasoning_effort="high"
    ),
    _openai_spec(
        OpenAIModels.GPT_5_NANO, cost_tier=CostTier.BUDGET, reasoning_effort="high"
    ),
    _openai_spec(OpenAIModels.GPT_4_1, cost_tier=CostTier.MID),
    _openai_spec(OpenAIModels.GPT_4_1_MINI, cost_tier=CostTier.BUDGET),
    _openai_spec(
        OpenAIModels.O3,
        cost_tier=CostTier.PREMIUM,
        reasoning_effort="medium",
        supports_parallel_tool_calls=False,
    ),
    # OpenRouter — Gemini Pro is flagship; Flash variants are budget/mid.
    _openrouter_spec(
        OpenRouterModels.GOOGLE_GEMINI_2_5_PRO, cost_tier=CostTier.FLAGSHIP
    ),
    _openrouter_spec(OpenRouterModels.GOOGLE_GEMINI_2_5_FLASH, cost_tier=CostTier.MID),
    _openrouter_spec(
        OpenRouterModels.GOOGLE_GEMINI_2_5_FLASH_LITE, cost_tier=CostTier.BUDGET
    ),
    _openrouter_spec(
        OpenRouterModels.GOOGLE_GEMINI_3_1_PRO_PREVIEW, cost_tier=CostTier.FLAGSHIP
    ),
    # Cohere Command A+ prices between Gemini 2.5 Flash's input and output
    # rates -- above Flash Lite (budget), well below either Pro (flagship).
    _openrouter_spec(OpenRouterModels.COHERE_COMMAND_A_PLUS, cost_tier=CostTier.MID),
)


REGISTRY: dict[str, ModelSpec] = {spec.id: spec for spec in _ALL_SPECS}


def all_model_ids() -> list[str]:
    """Return the model IDs the general UI should offer — every registered ID
    minus the deprecated ones, the A2A-orchestrator-only ones, and the
    internal-feature-only ones. Use ``REGISTRY`` / ``get_spec`` for catalog
    lookups that also need to resolve legacy, A2A-only, or internal IDs.
    """
    return [
        model_id
        for model_id in REGISTRY
        if model_id not in _DEPRECATED_MODEL_IDS
        and model_id not in _A2A_ONLY_MODEL_IDS
        and model_id not in _INTERNAL_ONLY_MODEL_IDS
    ]


def get_spec(model_id: str) -> ModelSpec | None:
    """Look up the spec for a model ID, or ``None`` if not registered.

    Returns specs for deprecated Anthropic IDs too; callers that want to
    block deprecated selections should check against ``all_model_ids()``.
    Retired OpenRouter IDs return ``None`` — they only exist as alias-map
    keys and must go through the node-level deprecated-model remap first.
    """
    return REGISTRY.get(model_id)


def is_regional_uplift_eligible(model_id: str) -> bool:
    """Does calling ``model_id`` on a regional OpenAI host cost 10% more?

    OpenAI charges a 10% uplift on its data-residency endpoints
    (``us.``/``eu.api.openai.com``) for models released on or after
    ``REGIONAL_UPLIFT_CUTOFF`` that are residency-eligible. Langfuse prices a
    generation purely from the reported model name and knows nothing about which
    host served it, so callers relabel eligible generations to ``<id>-regional``
    and a matching price definition carries the uplift.

    Returns ``False`` for an unregistered ID, a non-OpenAI provider, or a spec
    with no ``release_date``. That is the deliberate fail-safe: reporting the
    base ID understates an uplifted call by 10%, whereas relabelling an
    ineligible one invents a premium that does not exist. Under-reporting a
    known-real rate beats fabricating one.

    NOTE: this answers only "is this model uplift-eligible". Whether the uplift
    actually applies also depends on the deployment pointing at a regional host
    (``settings.OPENAI_REGIONAL_BASE_URL``); the two conditions are AND-ed at
    the call site, not here.
    """
    spec = REGISTRY.get(model_id)
    if spec is None or spec.provider is not Provider.OPENAI:
        return False
    return spec.release_date is not None and spec.release_date >= REGIONAL_UPLIFT_CUTOFF


def model_family(model_name: str) -> str:
    """Map a model identifier to its model *family* (vendor), e.g.
    ``anthropic``, ``openai``, ``google``.

    Accepts both registry model IDs and whatever string an observability
    backend recorded as the provided model name (which may be a raw
    provider slug that is not in ``REGISTRY``). OpenRouter passthrough IDs
    map to the underlying vendor prefix (``google/gemini-2.5-pro`` →
    ``google``), not to ``openrouter`` — token accounting cares about the
    model family, not the routing hop. Unknown names return ``other`` so
    callers can surface them instead of silently dropping usage.
    """
    name = (model_name or "").strip()
    if not name:
        return "other"

    spec = get_spec(name)
    if spec is not None:
        if spec.provider is Provider.OPENROUTER and "/" in spec.id:
            return spec.id.split("/", 1)[0].lower()
        return spec.provider.value

    lowered = name.lower()
    if "/" in lowered:
        return lowered.split("/", 1)[0]
    if lowered.startswith("claude") or "anthropic" in lowered:
        return Provider.ANTHROPIC.value
    if lowered.startswith(("gpt", "chatgpt", "text-embedding", "o1", "o3", "o4")):
        return Provider.OPENAI.value
    if lowered.startswith("gemini"):
        return "google"
    if lowered.startswith(("grok",)):
        return "xai"
    if lowered.startswith(("mistral", "ministral", "codestral")):
        return "mistralai"
    if lowered.startswith(("llama", "deepseek", "qwen", "kimi")):
        return lowered.split("-", 1)[0]
    return "other"


@dataclass(frozen=True)
class ContextBudget:
    """Per-model context budget the compaction capability operates against.

    ``context_window`` is the hard ceiling enforced by the provider; any
    request whose ``input_tokens + max_output_tokens`` exceeds this gets
    a 400 / context_length_exceeded. ``safe_input_window`` is the
    smaller working budget for *input only* — we reserve room for the
    model's output and a small safety margin so we trigger summarization
    before the model would refuse the request.
    """

    model_id: str
    context_window: int
    max_output_tokens: int
    safe_input_window: int


# Conservative slack we keep between the largest input we'll send and
# the model's context window, on top of the reserved ``max_output_tokens``.
# Five percent of the context window covers small inaccuracies in our
# token estimates plus any provider-side bookkeeping that isn't reflected
# in the user-visible token count.
_CONTEXT_SAFETY_MARGIN_FRACTION = 0.05


def get_context_budget(
    model_id: str,
    *,
    safety_margin_fraction: float = _CONTEXT_SAFETY_MARGIN_FRACTION,
) -> ContextBudget | None:
    """Return the input-only context budget for a model ID.

    Returns ``None`` when the model is not registered. Callers that want
    a default budget for unknown models should fall back themselves;
    silently making one up would hide registration bugs.

    ``safety_margin_fraction`` is a fraction of the full ``context_window``
    that's subtracted in addition to ``max_output_tokens`` to compute
    ``safe_input_window``. The default of 5% leaves headroom for small
    inaccuracies in pre-flight token counting and provider-side bookkeeping.
    """
    spec = REGISTRY.get(model_id)
    if spec is None:
        return None

    safety_margin = int(spec.context_window * safety_margin_fraction)
    safe_input_window = max(
        1, spec.context_window - spec.max_output_tokens - safety_margin
    )
    return ContextBudget(
        model_id=spec.id,
        context_window=spec.context_window,
        max_output_tokens=spec.max_output_tokens,
        safe_input_window=safe_input_window,
    )


_COST_TIER_LABELS: dict[CostTier, str] = {
    CostTier.BUDGET: "Budget",
    CostTier.MID: "Mid-tier",
    CostTier.PREMIUM: "Premium",
    CostTier.FLAGSHIP: "Flagship",
}

_COST_TIER_DESCRIPTIONS: dict[CostTier, str] = {
    CostTier.BUDGET: "fast & cheap, good for simple extraction/formatting",
    CostTier.MID: "balanced cost/quality",
    CostTier.PREMIUM: "strong reasoning, complex tasks",
    CostTier.FLAGSHIP: "highest capability & cost",
}


def build_cost_tier_prompt_section() -> str:
    """Build the 'Model Cost Tiers' prompt section from the registry.

    Only includes non-deprecated models so the LLM never recommends a
    retired model ID.
    """
    active_ids = set(all_model_ids())
    tiers: dict[CostTier, list[str]] = {tier: [] for tier in CostTier}
    for spec in _ALL_SPECS:
        if spec.id in active_ids:
            tiers[spec.cost_tier].append(spec.id)

    lines = [
        "### Model Cost Tiers (relative, cheapest → most expensive)",
        "Use this when recommending model changes for cost or quality optimization.",
        "IMPORTANT: Always use the **exact model ID** shown here when setting "
        "the `model` field via configure_node.",
    ]
    for tier in CostTier:
        model_ids = tiers[tier]
        if model_ids:
            label = _COST_TIER_LABELS[tier]
            description = _COST_TIER_DESCRIPTIONS[tier]
            lines.append(f"- **{label}**: {', '.join(model_ids)} — {description}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Named model subsets used by node UIs (and intended to be used by services
# for validation in a follow-up). Update these lists when a node's supported
# model set changes. Each subset has a paired ``_DEFAULT_MODEL_ID`` constant
# so the default is explicit at the call site rather than implied by list
# position.
# ---------------------------------------------------------------------------

# Models shown in dropdowns for skill-backed document / data generation nodes
# (XLSX, DOCX, Data Analysis, D3, Org Chart). These nodes run Anthropic custom
# skills with code execution, so the list is intentionally restricted to
# Anthropic models that the skills are tuned for. Fast-mode Opus variants are
# offered as selectable options (the skills service resolves them to the base
# model id + anthropic_speed='fast'); the default is the non-fast Opus 5.
DOCUMENT_GENERATION_MODEL_IDS: list[str] = [
    AnthropicModels.CLAUDE_OPUS_4_6.value,
    AnthropicModels.CLAUDE_OPUS_4_7.value,
    AnthropicModels.CLAUDE_OPUS_4_8.value,
    AnthropicModels.CLAUDE_OPUS_4_6_FAST.value,
    # ENG-3023: Opus 4.7 Fast retires 2026-07-24; dropped from the dropdown
    # (standard Opus 4.7 above stays). Saved selections auto-upgrade via the
    # deprecated-alias remap.
    AnthropicModels.CLAUDE_OPUS_4_8_FAST.value,
    # ENG-4119: Opus 5 (+fast) offered here so skill-backed nodes can be
    # benchmarked against the older Opus line (ENG-4111).
    AnthropicModels.CLAUDE_OPUS_5.value,
    AnthropicModels.CLAUDE_OPUS_5_FAST.value,
    AnthropicModels.CLAUDE_OPUS_5_5.value,
    AnthropicModels.CLAUDE_OPUS_5_5_FAST.value,
    AnthropicModels.CLAUDE_SONNET_5_5.value,
    AnthropicModels.CLAUDE_SONNET_5.value,
    AnthropicModels.CLAUDE_SONNET_4_6.value,
]
DOCUMENT_GENERATION_DEFAULT_MODEL_ID: str = AnthropicModels.CLAUDE_OPUS_5.value

# Models shown in the PowerPoint / EY-PowerPoint dropdowns. The pptx skill is
# tuned for Opus; the Opus variants (incl. fast-mode) are offered as selectable
# options.
POWERPOINT_MODEL_IDS: list[str] = [
    AnthropicModels.CLAUDE_OPUS_4_6.value,
    AnthropicModels.CLAUDE_OPUS_4_7.value,
    AnthropicModels.CLAUDE_OPUS_4_8.value,
    AnthropicModels.CLAUDE_OPUS_4_6_FAST.value,
    # ENG-3023: Opus 4.7 Fast retires 2026-07-24; dropped from the dropdown
    # (standard Opus 4.7 above stays). Saved selections auto-upgrade via the
    # deprecated-alias remap.
    AnthropicModels.CLAUDE_OPUS_4_8_FAST.value,
    # ENG-4119: kept in lockstep with DOCUMENT_GENERATION_MODEL_IDS above so
    # the pptx dropdowns do not fall a model line behind the other skills.
    AnthropicModels.CLAUDE_OPUS_5.value,
    AnthropicModels.CLAUDE_OPUS_5_FAST.value,
    AnthropicModels.CLAUDE_OPUS_5_5.value,
    AnthropicModels.CLAUDE_OPUS_5_5_FAST.value,
]
POWERPOINT_DEFAULT_MODEL_ID: str = AnthropicModels.CLAUDE_OPUS_5.value

# Default model for general agent-type nodes (Agent, MCP Agent, Code
# Interpreter, Skill Code Interpreter, Agentic Deep Research, Vercel Deploy).
# These nodes offer the full active catalog via ``get_all_models_list()``; only
# the default is centralized here so every agent node stays in lockstep rather
# than repeating the literal in each param schema.
AGENT_NODE_DEFAULT_MODEL_ID: str = AnthropicModels.CLAUDE_SONNET_5.value

# Models offered in the A2A orchestrator-agent config dropdown (ENG-3242).
# This is the ONLY surface that offers Fable 5 (see ``_A2A_ONLY_MODEL_IDS``);
# workflow-canvas agent nodes and skill-backed nodes keep their own subsets.
# The non-Anthropic entries are the raw slugs the A2A agents feature persists
# on the agent record and hands to pydantic-ai's ``infer_model`` (they are
# intentionally NOT the OpenRouter-prefixed ids used by workflow nodes).
# platform-frontend's ``AGENT_MODELS`` (src/types/agent-types.ts) must stay in
# lockstep with this list.
A2A_ORCHESTRATOR_MODEL_IDS: list[str] = [
    AnthropicModels.CLAUDE_FABLE_5.value,
    AnthropicModels.CLAUDE_OPUS_5_5_FAST.value,
    AnthropicModels.CLAUDE_OPUS_5_5.value,
    AnthropicModels.CLAUDE_OPUS_5_FAST.value,
    AnthropicModels.CLAUDE_OPUS_5.value,
    AnthropicModels.CLAUDE_OPUS_4_8.value,
    AnthropicModels.CLAUDE_OPUS_4_8_FAST.value,
    AnthropicModels.CLAUDE_OPUS_4_7.value,
    AnthropicModels.CLAUDE_OPUS_4_6.value,
    AnthropicModels.CLAUDE_SONNET_5_5.value,
    AnthropicModels.CLAUDE_SONNET_4_6.value,
    # gpt-5.6-sol is deliberately absent: it is the internal-only HITL review
    # digest model (ENG-4536), catalog-only and never offered on the A2A
    # orchestrator surface (see test_internal_only_digest_model_not_offered).
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.5-mini",
    "gemini-2.5-pro",
]
A2A_ORCHESTRATOR_DEFAULT_MODEL_ID: str = AnthropicModels.CLAUDE_OPUS_4_8_FAST.value
