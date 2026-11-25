### !!! DO NOT RUN THE USAGE DIRECTLY IN THIS FILE, YOU WILL GET A CIRCULAR DEPENDENCY ERROR DUE TO THE FILE NAME. !!! ###

from typing import Any
from urllib.parse import urlsplit

from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from ai.agentic.models.reporting import ReportedNameMixin
from core.config.ai import REGISTRY, ModelSpec, OpenAIModels
from core.config.ai.registry import is_regional_uplift_eligible
from core.settings import settings

# The SDK's own documented default. Passed explicitly rather than relying on the
# SDK's env-var fallback -- see _resolve_base_url.
_GLOBAL_API_ROOT = "https://api.openai.com/v1"


def _resolve_base_url() -> str:
    """Configured regional OpenAI API root, or the global default.

    **``/v1`` is appended when the configured value is host-only.** ``base_url`` is
    the API *root*, not the host: the SDK default is ``https://api.openai.com/v1``
    and it does NOT add the version segment to a custom value. A bare
    ``https://us.api.openai.com`` would send requests to ``/responses`` instead of
    ``/v1/responses`` and 404 every call. Operators (and EY's own docs) naturally
    write the host, so normalize here rather than relying on every terraform value
    carrying the suffix.

    A value that already has a path is left alone, so an explicit ``.../v1`` or a
    custom gateway path still works. Whitespace and trailing slashes are stripped.

    Returns an explicit URL in every case, never ``None``. Passing ``None`` would
    let the SDK fall back to reading the ``OPENAI_BASE_URL`` env var, and it treats
    an empty value there as a literal empty base URL -- producing host-less request
    paths like ``/responses``. Being explicit means no stray empty env var, from any
    source, can reach the client. The trade-off is that the raw ``OPENAI_BASE_URL``
    env var no longer redirects this client; use ``OPENAI_REGIONAL_BASE_URL``.
    """
    configured = settings.OPENAI_REGIONAL_BASE_URL.strip().rstrip("/")
    if not configured:
        return _GLOBAL_API_ROOT
    if urlsplit(configured).path in ("", "/"):
        configured = f"{configured}/v1"
    return configured


def build_openai_provider(api_key: str | None = None) -> OpenAIProvider:
    """Build the OpenAI provider, honoring the per-deployment regional host.

    ALL ``OpenAIProvider`` construction must go through here. A region-pinned
    key (data residency) is rejected by the global host with a 401, and a
    non-residency key is rejected by ``us.api.openai.com`` the same way — so
    the host has to travel with whatever key this cluster resolves.

    A workspace that does not set ``OPENAI_REGIONAL_BASE_URL`` gets the global
    API root explicitly -- unchanged behavior, but immune to a stray empty
    ``OPENAI_BASE_URL`` in the environment.
    """
    return OpenAIProvider(
        api_key=api_key or settings.OPENAI_API_KEY,
        base_url=_resolve_base_url(),
    )


def uses_regional_endpoint() -> bool:
    """Is this deployment pointed at an OpenAI data-residency host?

    Matches ``<region>.api.openai.com`` only. A custom gateway or proxy carries
    no uplift, so treating any base-URL override as regional would report a 10%
    premium nobody was charged.
    """
    configured = settings.OPENAI_REGIONAL_BASE_URL.strip()
    if not configured:
        return False
    host = urlsplit(
        configured if "//" in configured else f"//{configured}"
    ).netloc.lower()
    return host.endswith(".api.openai.com") and host != "api.openai.com"


def regional_reported_model_name(model_id: str) -> str | None:
    """Telemetry label for ``model_id``, or ``None`` to report it unchanged.

    Returns ``<model_id>-regional`` only when BOTH conditions hold: this
    deployment calls a regional host, AND the model is in the uplift-eligible
    set (released on or after OpenAI's 2026-03-05 cutoff). Either alone means
    the global rate applies and the base id is the correct label.
    """
    if not uses_regional_endpoint() or not is_regional_uplift_eligible(model_id):
        return None
    return f"{model_id}-regional"


class _RegionalReportingOpenAIResponsesModel(ReportedNameMixin, OpenAIResponsesModel):
    """``OpenAIResponsesModel`` that reports the ``-regional`` label. Agent nodes."""


class _RegionalReportingOpenAIChatModel(ReportedNameMixin, OpenAIChatModel):
    """``OpenAIChatModel`` that reports the ``-regional`` label. General chat."""


def build_openai_chat_model(
    model_id: str,
    provider: OpenAIProvider,
    model_settings: ModelSettings | None = None,
) -> OpenAIChatModel:
    """Build the general-chat OpenAI model, relabelling regional generations.

    ALL ``OpenAIChatModel`` construction for OpenAI general chat must go through
    here, so a regional deployment cannot silently under-report cost.
    """
    reported = regional_reported_model_name(model_id)
    if reported is None:
        return OpenAIChatModel(model_id, provider=provider, settings=model_settings)
    return _RegionalReportingOpenAIChatModel(
        model_id,
        reported_model_name=reported,
        provider=provider,
        settings=model_settings,
    )


def build_openai_agent_settings_dict(
    spec: ModelSpec,
    model_settings: dict[str, Any] | None = None,
    prompt_cache_key: str | None = None,
) -> dict[str, Any]:
    """Build the settings dict used to construct an OpenAI agent model.

    Pure function: takes a ``ModelSpec`` and optional user-supplied settings,
    returns the fully-resolved settings dict. Kept separate from model
    construction so it is straightforward to snapshot-test without hitting
    the SDK or the API.

    ``prompt_cache_key`` is forwarded to OpenAI's ``prompt_cache_key`` request
    field (via pydantic-ai's ``openai_prompt_cache_key``). OpenAI auto-caches
    prefixes >=1024 tokens, but on a multi-replica fleet a stable cache key is
    what pins same-prefix requests to the same backend so the cache is actually
    hit. The caller MUST derive it from cache-relevant identity only (model +
    agent/node identity), never per-request data, or it just scatters routing.
    """
    result: dict[str, Any] = dict(model_settings) if model_settings else {}

    # fast_mode is Anthropic-only; pop it so it never reaches OpenAIResponsesModelSettings.
    result.pop("fast_mode", None)
    # [VÁ CỦA MÌNH 03/11/2026 — chay_thu/sua_doi/README.md] gpt-6-* từ chối tham số temperature (lỗi 400
    # "Unsupported parameter: 'temperature' is not supported with this model", lượt chạy 14:44 03/11) — ô temperature
    # của khối agent luôn có mặc định 0.7 nên phải bỏ ở đây. Chỉ áp cho họ gpt-6, mô hình khác giữ nguyên.
    if spec.id.startswith("gpt-6"):
        result.pop("temperature", None)
        result.pop("top_p", None)

    if prompt_cache_key:
        result.setdefault("openai_prompt_cache_key", prompt_cache_key)

    # O-series reasoning models reject ``parallel_tool_calls``.
    if not spec.supports_parallel_tool_calls:
        result.pop("parallel_tool_calls", None)

    if spec.reasoning_effort is not None:
        result.setdefault("openai_reasoning_effort", spec.reasoning_effort)
        result.setdefault("openai_reasoning_summary", "auto")

    # pydantic-ai's OpenAIResponsesModelSettings defines ``max_tokens`` (not
    # ``max_output_tokens``); its Responses-API caller forwards that value as
    # the ``max_output_tokens`` request field. Writing the wrong key here
    # silently leaves the request uncapped and lets a downstream default win.
    if "max_tokens" not in result:
        result["max_tokens"] = spec.max_output_tokens

    return result


def get_openai_model_for_agent_node(
    model_name: OpenAIModels,
    model_settings: dict | None = None,
    api_key: str | None = None,
    prompt_cache_key: str | None = None,
) -> OpenAIResponsesModel:
    spec = REGISTRY[model_name.value]
    final_settings = build_openai_agent_settings_dict(
        spec, model_settings, prompt_cache_key=prompt_cache_key
    )
    openai_model_settings = (
        OpenAIResponsesModelSettings(**final_settings) if final_settings else None
    )
    # A regional deployment pays 10% more for uplift-eligible models; report a
    # distinct ``-regional`` label so Langfuse prices it via a dedicated
    # definition instead of the global rate. The API request still
    # uses the plain model id.
    reported = regional_reported_model_name(model_name.value)
    if reported is not None:
        return _RegionalReportingOpenAIResponsesModel(
            model_name.value,
            reported_model_name=reported,
            provider=build_openai_provider(api_key),
            settings=openai_model_settings,
        )
    return OpenAIResponsesModel(
        # .value: keep span/model names as the plain id, not the enum repr (ENG-2641).
        model_name.value,
        provider=build_openai_provider(api_key),
        settings=openai_model_settings,
    )


## Usage: model = get_openai_model(OpenAIModels.GPT_5_2)
