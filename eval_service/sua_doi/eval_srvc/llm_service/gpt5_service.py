from openai.types.chat.chat_completion import ChatCompletion

from eval_srvc.custom_types.llm_service_schemas.llm_response import LLMResponse
from eval_srvc.custom_types.llm_service_schemas.tool_schema import ToolSchema
from eval_srvc.custom_types.reference_input import LoadedFile
from eval_srvc.llm_service.base import DEFAULT_MAX_TOKENS
from eval_srvc.llm_service.open_ai_service import OpenAIService
from eval_srvc.logging_utils import Logger

logger = Logger.get_logger()
EVAL_RA_MOT_LAN, EVAL_VAO_MOT_LAN_BYTE = 8_000, 84_000   # VÀO = 20.000 cố định + 4.000 tiêu chí + 60.000 dữ liệu (muc_toi_da.py EVAL_*)   # [ghi chú] BẰNG số trong chay_thu/giao_dien/muc_toi_da.py (EVAL_RA_MOT_LAN)


class GPT5Service(OpenAIService):

    SUPPORTED_MODELS = (
        "gpt-5.4-2026-03-05",
        "gpt-5-mini-2025-08-07",
        "gpt-6-luna",          # [VÁ CỦA MÌNH 06/11/2026 — nhóm kỹ thuật, chay_thu/eval_service/HUONG_DAN.md] giám khảo dùng gpt-6-luna (khoá OpenAI của User)
    )

    _FIXED_TEMPERATURE_MODELS = (
        "gpt-5-mini-2025-08-07",
        "gpt-6-luna",          # mô hình suy nghĩ: KHÔNG gửi temperature (gửi có thể bị từ chối) — CHƯA KIỂM bằng lượt thật
    )
    # Mức suy nghĩ cho gpt-6-luna (none / low / medium — User 04/11). Mặc định "low" như agentic-backend máy thử (chay_thu/sua_doi/README.md).
    _REASONING_EFFORT = {"gpt-6-luna": __import__("os").environ.get("EVAL_LUNA_REASONING_EFFORT", "low")}

    async def aquery(
        self,
        user_prompt: str,
        *,
        system_prompt: str | None = None,
        message_history: list[dict] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.0,
        timeout: int = 15,
        tools: list[ToolSchema] | None = None,
        attachments: list[LoadedFile] | None = None,
        enable_thinking: bool = True,
    ) -> LLMResponse:
        """Override the inherited `OpenAIService.aquery` so that GPT-5 judges
        keep the 32k headroom instead of inheriting the 16k OpenAI default.

        `OpenAIService.aquery` defaults `max_tokens` to `OPENAI_MAX_TOKENS`
        (16384-cap-safe for gpt-4o). GPT-5 reasoning models support far larger
        output windows and that default would silently clip them.
        """
        # [ghi chú] bộ chạy thử (EVAL_CHI_PASS_FAIL=1): mỗi lần chấm ra ≤ EVAL_RA_MOT_LAN (mặc định Capitol 32.000,
        # llm_service/base.py:7). Bị cắt ⇒ stop_reason length ⇒ bài ra NA (pass_fail_eval.py:304-312), không thành ĐẠT.
        if __import__("os").environ.get("EVAL_CHI_PASS_FAIL", "1") != "0":   # [D lô 2 MAJOR] mặc định BẬT
            max_tokens = min(max_tokens, EVAL_RA_MOT_LAN)
            # [vòng 3 — C hỏi: platform-api chỉ nhận model trong enum Capitol (schemas/evals.py:9-18), không có gpt-6-luna] ép MỌI giám khảo
            # đi qua đây về gpt-6-luna (luật User) ⇒ bài chấm tạo qua API với "gpt-5-mini" vẫn chạy luna; _aquery đọc self.model (payload "model").
            if self.model != "gpt-6-luna":
                logger.warning(f"[B] ép giám khảo {self.model} ⇒ gpt-6-luna (EVAL_CHI_PASS_FAIL bật)")
                self.model = "gpt-6-luna"
            # [D lô 2 minor] một rào phủ MỌI đường chấm: đo thẳng chuỗi gửi đi (lời dặn hệ thống + tin) — vượt ⇒ từ chối, 0 đồng
            _n = len(((system_prompt or "") + user_prompt).encode())
            if _n > EVAL_VAO_MOT_LAN_BYTE:
                raise ValueError(f"Bộ chạy thử: lời gọi chấm {_n} byte vượt trần {EVAL_VAO_MOT_LAN_BYTE} — không gọi mô hình.")
        return await super().aquery(
            user_prompt,
            system_prompt=system_prompt,
            message_history=message_history,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            tools=tools,
            attachments=attachments,
            enable_thinking=enable_thinking,
        )

    async def _aquery(
        self,
        user_prompt: str,
        *,
        system_prompt: str | None = None,
        message_history: list[dict] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: int = 0,
        timeout: int = 15,
        tools: list[ToolSchema] | None = None,
        attachments: list[LoadedFile] | None = None,
    ) -> ChatCompletion:

        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if message_history:
            messages.extend(message_history)

        user_content, uploaded_ids = await self._build_user_content(
            user_prompt, attachments
        )
        messages.append({"role": "user", "content": user_content})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "stream": False,
            "timeout": timeout,
        }

        if self.model not in self._FIXED_TEMPERATURE_MODELS:
            payload["temperature"] = temperature
        if self.model in self._REASONING_EFFORT:
            payload["reasoning_effort"] = self._REASONING_EFFORT[self.model]

        if tools:
            payload["tools"] = [t.open_ai_schema for t in tools]
            payload["tool_choice"] = "required"

        try:
            try:
                response = await self._client.with_options(
                    max_retries=0
                ).chat.completions.create(**payload)
            except Exception as e:
                logger.debug(f"Failed to query {self.model}: {str(e)}")
                raise
        finally:
            await self._delete_uploaded_files(uploaded_ids)

        return response
