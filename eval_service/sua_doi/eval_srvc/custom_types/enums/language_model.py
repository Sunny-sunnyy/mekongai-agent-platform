from eval_srvc.custom_types.enums.wrapped_enum import WrappedEnum


class LanguageModel(WrappedEnum):
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_4O = "gpt-4o"
    GPT_54 = "gpt-5.4"
    GPT_5_MINI = "gpt-5-mini"
    CLAUDE_SONNET_40 = "claude-sonnet-4.0"
    CLAUDE_SONNET_45 = "claude-sonnet-4.5"
    CLAUDE_OPUS_46 = "claude-opus-4.6"
    CLAUDE_SONNET_46 = "claude-sonnet-4.6"
    GPT_6_LUNA = "gpt-6-luna"  # [VÁ CỦA MÌNH 06/11/2026 — nhóm kỹ thuật, chay_thu/eval_service/HUONG_DAN.md] máy chỉ có khoá OpenAI, User chỉ dùng gpt-6-luna
