from datetime import date
from enum import Enum


class OpenAIModels(str, Enum):
    # GPT-5.6 family (GA 2026-07-09). ``gpt-5.6-sol`` is the flagship tier;
    # the bare ``gpt-5.6`` API alias routes to it. Catalog-only today: used
    # by the HITL review digest (ENG-4536), deliberately NOT added to any
    # UI-offered model list.
    GPT_5_6_SOL = "gpt-5.6-sol"
    GPT_5_6_TERRA = "gpt-5.6-terra"
    GPT_5_6_LUNA = "gpt-5.6-luna"
    GPT_6_LUNA = "gpt-6-luna"  # [VÁ CỦA MÌNH 03/11/2026 — chay_thu/sua_doi/README.md]

    GPT_5_5 = "gpt-5.5"
    GPT_5_5_PRO = "gpt-5.5-pro"

    GPT_5_4 = "gpt-5.4"
    GPT_5_4_PRO = "gpt-5.4-pro"

    GPT_5_2 = "gpt-5.2"
    GPT_5_2_PRO = "gpt-5.2-pro"

    GPT_5 = "gpt-5"
    GPT_5_MINI = "gpt-5-mini"
    GPT_5_NANO = "gpt-5-nano"

    GPT_4_1 = "gpt-4.1"
    GPT_4_1_MINI = "gpt-4.1-mini"

    O3 = "o3"


# Max output tokens per model based on official OpenAI documentation
# Source: https://platform.openai.com/docs/models
OPENAI_MAX_OUTPUT_TOKENS: dict[OpenAIModels, int] = {
    # GPT-5.6 series - 128K max output (1.05M context window)
    OpenAIModels.GPT_5_6_SOL: 128_000,
    OpenAIModels.GPT_5_6_TERRA: 128_000,
    OpenAIModels.GPT_5_6_LUNA: 128_000,
    OpenAIModels.GPT_6_LUNA: 128_000,  # [VÁ CỦA MÌNH 03/11/2026 — chay_thu/sua_doi/README.md]
    # GPT-5.5 series - 128K max output (1.05M context window)
    OpenAIModels.GPT_5_5: 128_000,
    OpenAIModels.GPT_5_5_PRO: 128_000,
    # GPT-5.4 series - 128K max output
    OpenAIModels.GPT_5_4: 128_000,
    OpenAIModels.GPT_5_4_PRO: 128_000,
    # GPT-5.2 series - 128K max output
    OpenAIModels.GPT_5_2: 128_000,
    OpenAIModels.GPT_5_2_PRO: 128_000,
    # GPT-5 series - 128K max output
    OpenAIModels.GPT_5: 128_000,
    OpenAIModels.GPT_5_MINI: 128_000,
    OpenAIModels.GPT_5_NANO: 128_000,
    # GPT-4.1 series - 32K max output (non-reasoning models)
    OpenAIModels.GPT_4_1: 32_768,
    OpenAIModels.GPT_4_1_MINI: 32_768,
    # O-series reasoning models - 100K max output
    OpenAIModels.O3: 100_000,
}

# Default max output tokens for unknown models
OPENAI_DEFAULT_MAX_OUTPUT_TOKENS = 128_000


def get_openai_max_output_tokens(model: OpenAIModels) -> int:
    """Get the maximum output tokens for an OpenAI model."""
    return OPENAI_MAX_OUTPUT_TOKENS.get(model, OPENAI_DEFAULT_MAX_OUTPUT_TOKENS)


# Total context window (input + output) per model. Sources:
#   * GPT-4.1 family: 1,047,576 tokens (https://platform.openai.com/docs/models/gpt-4.1)
#   * o-series reasoning: 200K tokens (o3)
#   * GPT-5 / 5-mini / 5-nano: 400K tokens
#   * GPT-5.2 family: 400K tokens
#   * GPT-5.4 / 5.5 family: 1,050,000 tokens
#   * GPT-5.6 family (Sol / Terra / Luna): 1,050,000 tokens
# When GPT-5.5 public spec lands, treat as identical to 5.4 unless OpenAI
# documents otherwise; add an explicit entry below at the same time the
# enum value is introduced — ``get_openai_context_window`` deliberately
# raises on missing entries rather than guessing a default.
OPENAI_CONTEXT_WINDOWS: dict[OpenAIModels, int] = {
    # GPT-5.6 series
    OpenAIModels.GPT_5_6_SOL: 1_050_000,
    OpenAIModels.GPT_5_6_TERRA: 1_050_000,
    OpenAIModels.GPT_5_6_LUNA: 1_050_000,
    OpenAIModels.GPT_6_LUNA: 1_050_000,  # [VÁ CỦA MÌNH 03/11/2026 — chay_thu/sua_doi/README.md]
    # GPT-5.5 series
    OpenAIModels.GPT_5_5: 1_050_000,
    OpenAIModels.GPT_5_5_PRO: 1_050_000,
    # GPT-5.4 series
    OpenAIModels.GPT_5_4: 1_050_000,
    OpenAIModels.GPT_5_4_PRO: 1_050_000,
    # GPT-5.2 series
    OpenAIModels.GPT_5_2: 400_000,
    OpenAIModels.GPT_5_2_PRO: 400_000,
    # GPT-5 base series
    OpenAIModels.GPT_5: 400_000,
    OpenAIModels.GPT_5_MINI: 400_000,
    OpenAIModels.GPT_5_NANO: 400_000,
    # GPT-4.1 series
    OpenAIModels.GPT_4_1: 1_047_576,
    OpenAIModels.GPT_4_1_MINI: 1_047_576,
    # o-series reasoning
    OpenAIModels.O3: 200_000,
}


def get_openai_context_window(model: OpenAIModels) -> int:
    """Return the total context window (input + output tokens) for ``model``.

    Raises:
        KeyError: when ``model`` is missing from
            ``OPENAI_CONTEXT_WINDOWS``. Every value in
            :class:`OpenAIModels` is expected to have an explicit
            entry — a missing one is a code bug, not a runtime
            condition we should silently paper over with a guessed
            default. Raising here surfaces it during registry build at
            import time so it's caught in CI rather than baked into
            production context-budget math.
    """
    return OPENAI_CONTEXT_WINDOWS[model]


# Model release dates, used ONLY to decide the regional-endpoint price uplift.
# OpenAI's pricing docs state:
#
#   "Regional processing (data residency) endpoints are charged a 10% uplift for
#    models released on or after March 5, 2026, that are eligible for data
#    residency."
#
# Every date below except the GPT-5.6 family was read off the dated snapshot id
# the API itself echoes in the response body (e.g. ``gpt-4.1`` ->
# ``gpt-4.1-2025-04-14``), measured against real keys on the global, US and EU
# hosts. The residency-eligibility half of the rule is satisfied for all of them:
# that same sweep got a 200 from every model on both regional hosts, with no
# ``data_residency_mismatch``.
#
# The three GPT-5.6 ids echo NO date, so theirs comes from the launch
# announcement (previewed 2026-07-09) rather than the API -- which is why the
# version-monotonicity guard in the tests exists: it is the only automated check
# that can catch a wrong date on these three.
OPENAI_RELEASE_DATES: dict[OpenAIModels, date] = {
    # GPT-5.6 series — date from the launch announcement, NOT the API.
    OpenAIModels.GPT_5_6_SOL: date(2026, 7, 9),
    OpenAIModels.GPT_5_6_TERRA: date(2026, 7, 9),
    OpenAIModels.GPT_5_6_LUNA: date(2026, 7, 9),
    OpenAIModels.GPT_6_LUNA: date(2026, 11, 3),  # [VÁ CỦA MÌNH 03/11/2026 — chay_thu/sua_doi/README.md] ngày ĐẶT TẠM, trang OpenAI không ghi
    # GPT-5.5 series
    OpenAIModels.GPT_5_5: date(2026, 4, 23),
    OpenAIModels.GPT_5_5_PRO: date(2026, 4, 23),
    # GPT-5.4 series — lands exactly ON the cutoff, and the rule is "on or
    # after", so these ARE uplift-eligible. The one place `>` vs `>=` flips the
    # answer; pinned by a boundary test.
    OpenAIModels.GPT_5_4: date(2026, 3, 5),
    OpenAIModels.GPT_5_4_PRO: date(2026, 3, 5),
    # GPT-5.2 series — nearest ineligible version.
    OpenAIModels.GPT_5_2: date(2025, 12, 11),
    OpenAIModels.GPT_5_2_PRO: date(2025, 12, 11),
    # GPT-5 base series
    OpenAIModels.GPT_5: date(2025, 8, 7),
    OpenAIModels.GPT_5_MINI: date(2025, 8, 7),
    OpenAIModels.GPT_5_NANO: date(2025, 8, 7),
    # GPT-4.1 series
    OpenAIModels.GPT_4_1: date(2025, 4, 14),
    OpenAIModels.GPT_4_1_MINI: date(2025, 4, 14),
    # o-series reasoning. NOTE: the ``oN`` numbers are a separate scale from
    # ``gpt-N.M``, so versions are only ever comparable WITHIN a family.
    OpenAIModels.O3: date(2025, 4, 16),
}

# The cutoff from OpenAI's pricing docs. Kept as one constant so a change to
# OpenAI's rule is a one-line edit rather than a re-audit of every model.
REGIONAL_UPLIFT_CUTOFF = date(2026, 3, 5)


def get_openai_release_date(model: OpenAIModels) -> date:
    """Return the release date for ``model``.

    Raises:
        KeyError: when ``model`` is missing from ``OPENAI_RELEASE_DATES``.
            Deliberate, and for the same reason
            ``get_openai_context_window`` raises: every member of
            :class:`OpenAIModels` is expected to have an explicit entry, so a
            missing one is a code bug. Raising surfaces it during registry
            build at import time -- i.e. in CI -- rather than letting a new
            model silently bill at the wrong rate on a regional cluster.
    """
    return OPENAI_RELEASE_DATES[model]


# Mapping of retired OpenAI model IDs to their replacements. OpenAI shut these
# down in the API on 2026-10-23; the retired ID survives only as a key here so
# workflows saved with it are remapped (same cost tier) instead of failing.
DEPRECATED_OPENAI_MODEL_ALIASES: dict[str, OpenAIModels] = {
    "o3-mini": OpenAIModels.GPT_5_MINI,
    "o4-mini": OpenAIModels.GPT_5_MINI,
}
