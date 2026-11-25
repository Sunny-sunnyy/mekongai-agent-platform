"""Model resolution shared by every agent node.

Consolidates the previously copy-pasted ``__fetch_model`` /
``__get_model_settings_params`` / ``_stable_prompt_cache_key`` trio from
the Agent, MCP Agent, Code Interpreter and Vercel Deploy nodes. The
implementation is the Agent node's (the most complete copy): deprecated
alias remapping, per-provider model lookup, per-org API keys, prompt-cache
routing keys, and the provider-heuristic fallbacks for unknown model ids.

Bedrock behaviour (``BEDROCK_FALLBACK_ENABLED`` / ``BEDROCK_PRIMARY`` /
residency) is fully encapsulated inside
``get_anthropic_model_with_bedrock_failover_for_agent_node``; nodes only
declare the per-class ``_enable_bedrock_failover`` opt-out.
"""

import hashlib
import logging
from typing import Any, ClassVar, NamedTuple, final

from pydantic_ai.models import Model

from ai.agentic.models.fallback import (
    get_anthropic_model_with_bedrock_failover_for_agent_node,
)
from ai.agentic.models.openai import get_openai_model_for_agent_node
from ai.agentic.models.openrouter import get_openrouter_model_for_agent_node
from core.config.ai import (
    DEPRECATED_ANTHROPIC_MODEL_ALIASES,
    DEPRECATED_OPENAI_MODEL_ALIASES,
    DEPRECATED_OPENROUTER_MODEL_ALIASES,
    AnthropicModels,
    OpenAIModels,
    OpenRouterModels,
)
from core.settings import settings
from nodes.utils import parse_max_tokens
from services.provider_keys import Provider, resolve_api_key_for_provider

logger = logging.getLogger(__name__)

RegistryModel = AnthropicModels | OpenAIModels | OpenRouterModels


# [ghi chú] ĐỔI MÔ HÌNH cho khối kiểu Agent bằng MỘT FILE.
# Vì sao ở đây: các khối kế thừa AgentBaseNode chọn mô hình ở ĐÚNG hàm _fetch_model dưới. 2.624 nút đã lưu ghi "gpt-6-luna" ⇒ không sửa DB, không dịch khuôn
# ở cổng: bỏ qua tên lưu trong nút, dùng tên ở FILE ⇒ Capitol dựng AnthropicModel ⇒ đi /v1/messages của cổng AI ⇒ cổng gọi Bedrock; sổ cổng ghi tên thật.
# File: biến CHAY_THU_MO_HINH_FILE (mặc định /srv/cau_hinh_mo_hinh/mo_hinh_khoi.txt) — nằm trong THƯ MỤC gắn, đọc MỖI LẦN khối chạy ⇒ đổi = ghi tại chỗ 1 dòng,
# KHÔNG tạo lại hộp. Mỗi dòng "<khoá> = <tên mô hình>" hoặc "<khoá> = <tên mô hình> / <mức suy luận>"; dòng trống / bắt đầu # bỏ qua.
#     mac_dinh = claude-sonnet-5-5              ⇐ MỌI loại khối trong danh sách dưới
#     agent_node = claude-opus-5-5 / high       ⇐ riêng một loại khối — đè mac_dinh; "/ high" = mức suy luận (low | medium | high | xhigh | max)
# [đã soát 18/11 — SỬA BẮT BUỘC] CHỈ đổi mô hình cho các loại khối trong CHAY_THU_KHOI_DOI_MO_HINH (đúng các loại trang đã tính mức tiền theo Claude);
# loại khác (code_interpreter_skill, data_analysis…) GIỮ ô model của nút dù file có mac_dinh. Dòng riêng cho loại NGOÀI danh sách ⇒ NÉM. Danh sách này là
# MỘT NGUỒN: giao_dien/muc_toi_da.py đọc chính hằng dưới đây từ file này (không chép tay) — thêm loại = sửa ở đây + thêm mức tiền + đã soát.
# Mức suy luận: Capitol không có ô mức cho khối ⇒ sau khi dựng mô hình theo tên danh mục, bản vá đổi TÊN GỬI TRÊN DÂY thành "<tên>-<mức>" — cổng AI có sẵn
# tuyến tên đó (bảng định tuyến) và ghi sổ đúng mô hình + mức thật. Không ghi mức ⇒ gửi tên trần ⇒ mức mặc định của tuyến (bảng cổng).
# Không có file / không dòng nào áp cho khối ⇒ bản vá TẮT, chạy y như mã gốc (= cách lùi nhanh). Tên ngoài danh mục AnthropicModels, mức lạ, dòng sai khuôn,
# lặp khoá, file không đọc được ⇒ NÉM trước khi gọi AI (không lặng lẽ chạy luna hay lùi về "Claude Sonnet 4.6").
# [ghi chú] thêm 2 loại khối họ Agent còn luna: chạy code có skill (SkillCodeInterpreterNode ←
# FlexibleCodeInterpreterNode) và tra cứu sâu (AgenticDeepResearchNode ← MCPAgentNode) — cả hai chọn mô hình ở ĐÚNG hàm _fetch_model dưới. Trang đã có mức tiền
# cho cả hai (muc_toi_da.py bản cùng gói: tra cứu sâu tách tiền tra web trả phí khỏi tiền mô hình).
CHAY_THU_KHOI_DOI_MO_HINH = ("agent_node", "flexible_code_interpreter_node", "mcp_agent_node", "vercel_deploy_agent_node",
                             "skill_code_interpreter_node", "agentic_deep_research_node")
# [ghi chú] loại khối KHÔNG kế thừa
# AgentBaseNode mà tự mở "hộp phiên" (services/pydantic_ai_skills/e2b_chat_service.py — Word / Excel / PowerPoint / phân tích dữ liệu / D3 / đồ thị quan hệ).
# Chúng KHÔNG đi qua _fetch_model dưới; e2b_chat_service.py (bản vá cùng gói) hỏi CÙNG hàm _chay_thu_mo_hinh_ep + CÙNG file ⇒ một nguồn. Tách hằng riêng
# vì mức tiền của hai họ khác nhau (giao_dien/muc_toi_da.py đọc cả hai hằng bằng ast). ey_pptx_chat_node CHƯA vào: trang chưa có mức cho nó.
CHAY_THU_KHOI_HOP_PHIEN = ("pptx_chat_node", "docx_chat_node", "xlsx_chat_node", "data_analysis_agent_node", "d3_data_visualization_node", "network_graph_output_node")
# [ghi chú] "NGƯỜI GÁC": khối / dịch vụ gọi AI để KIỂM hoặc TÓM, không phải khối họ Agent,
# trước đây GHI CỨNG luna trong code (Rào chế độ AI, Che thông tin) hoặc theo biến cấu hình (bản tóm tắt cho Người duyệt). Nay hỏi CÙNG file mô hình khối qua
# chay_thu_mo_hinh_nguoi_gac() dưới ⇒ file có mac_dinh (hoặc dòng riêng) thì chạy Claude qua cổng AI; file trống ⇒ y như trước (luna) = cách lùi.
# Tách hằng riêng vì mức tiền khác (giao_dien/muc_toi_da.py đọc hằng này bằng ast): toàn bộ tiền của người gác là tiền mô hình ⇒ chạy Claude thì phần phải báo = 0.
CHAY_THU_KHOI_NGUOI_GAC = ("guardrails_node", "text_redaction_node", "human_intervention_node")
# Loại khối DÙNG CHUNG dòng cấu hình với loại khác: che PDF chạy cùng dịch vụ che với che chữ (services/redaction/redaction_service.py không biết khối nào gọi)
# ⇒ không có dòng riêng cho pdf_redaction_node; trang tính mức cho nó theo dòng của text_redaction_node.
CHAY_THU_NGUOI_GAC_THEO = {"pdf_redaction_node": "text_redaction_node"}
CHAY_THU_MUC_SUY_LUAN = ("low", "medium", "high", "xhigh", "max")
# [ghi chú] khối chạy MỘT TRONG các mô hình dưới
# (Claude 5.5 qua Bedrock — tiền không tính trần, người vận hành 18/11) thì KHÔNG còn trần TỔNG token vào / ra của cả lượt khối (khối Agent 120.000 / 16.000 ở
# node.py, hộp phiên 200.000 / 24.000 ở e2b_chat_service.py — đặt từ thời luna để giữ tiền; Claude tính cả token suy luận vào đầu ra nên dễ chạm). GIỮ rào SỐ LẦN GỌI
# (chống vòng lặp). Danh sách ghi TÊN CỤ THỂ, không lấy "mọi tên Anthropic": có nhãn Anthropic cũ mà cổng AI trỏ về luna (tính trần tiền) — những tên đó phải giữ trần.
CHAY_THU_MO_HINH_BO_TRAN_TOKEN = ("claude-sonnet-5-5", "claude-opus-5-5")


def _chay_thu_mo_hinh_ep(loai_khoi: str | None, ten_luu: str) -> tuple[str, str | None] | None:
    """(tên mô hình trong danh mục, mức suy luận | None) cho loại khối này theo file; None = không đổi (chạy theo ô model của nút)."""
    import os

    duong = os.environ.get("CHAY_THU_MO_HINH_FILE") or "/srv/cau_hinh_mo_hinh/mo_hinh_khoi.txt"
    try:
        with open(duong, encoding="utf-8") as f:
            dong = f.read().splitlines()
    except FileNotFoundError:
        return None
    bang: dict[str, tuple[str, str | None]] = {}
    for so, d in enumerate(dong, 1):
        d = d.strip()
        if not d or d.startswith("#"):
            continue
        khoa, dau, gia_tri = d.partition("=")
        ten, co_muc, muc = gia_tri.partition("/")
        khoa, ten, muc = khoa.strip(), ten.strip(), muc.strip()
        if not dau or not khoa or not ten or khoa in bang or (co_muc and not muc):
            raise ValueError(f"file mô hình khối dòng {so} sai khuôn hoặc lặp khoá ('{d[:60]}') — mỗi dòng: <mac_dinh | loại khối> = <tên mô hình> [/ <mức>]")
        if khoa != "mac_dinh" and khoa not in CHAY_THU_KHOI_DOI_MO_HINH + CHAY_THU_KHOI_HOP_PHIEN + CHAY_THU_KHOI_NGUOI_GAC:
            raise ValueError(f"file mô hình khối dòng {so}: loại khối '{khoa[:60]}' chưa hỗ trợ đổi mô hình (chỉ: {', '.join(CHAY_THU_KHOI_DOI_MO_HINH + CHAY_THU_KHOI_HOP_PHIEN + CHAY_THU_KHOI_NGUOI_GAC)})")
        if ten not in {m.value for m in AnthropicModels}:
            raise ValueError(f"file mô hình khối dòng {so}: mô hình '{ten[:60]}' không có trong danh mục Anthropic — sửa file {duong}")
        if muc and muc not in CHAY_THU_MUC_SUY_LUAN:
            raise ValueError(f"file mô hình khối dòng {so}: mức suy luận '{muc[:20]}' không hợp lệ (chỉ: {' | '.join(CHAY_THU_MUC_SUY_LUAN)})")
        bang[khoa] = (ten, muc or None)
    if loai_khoi not in CHAY_THU_KHOI_DOI_MO_HINH + CHAY_THU_KHOI_HOP_PHIEN + CHAY_THU_KHOI_NGUOI_GAC:
        return None
    return bang.get(loai_khoi) or bang.get("mac_dinh")


def chay_thu_loi_ro_nguoi_gac(e: BaseException, ten: str, co_byte: int) -> str | None:
    """[ghi chú] Không còn rào cỡ đầu vào trên tuyến Claude ⇒ khi NHÀ CUNG CẤP từ chối vì nội dung vượt sức đọc một lần của mô hình, hoặc
    quá giờ, người dùng phải thấy LỜI BÁO RÕ (cỡ thật + nguyên nhân) thay cho lỗi kỹ thuật. Đây KHÔNG phải rào: không chặn trước, chỉ đổi lời của lỗi đã xảy ra;
    khối vẫn ĐÓNG (người gọi ném / ghi ERROR, không cho qua). Không nhận ra loại lỗi ⇒ None ⇒ người gọi giữ lỗi gốc."""
    chu, kieu, x, da = "", "", e, set()
    while x is not None and id(x) not in da:   # lỗi của pydantic-ai / httpx thường bọc nhau
        da.add(id(x)); chu += f" {x} {getattr(x, 'body', '') or ''}"; kieu += " " + type(x).__name__
        x = x.__cause__ or x.__context__
    chu, kieu, co = chu.lower(), kieu.lower(), f"{int(co_byte):,}".replace(",", ".")
    if any(k in chu for k in ("prompt is too long", "input is too long", "too many tokens", "context window", "context length", "maximum context",
                              "request too large", "request entity too large", "exceeds the maximum", "status_code: 413")):
        return (f"{ten}: nội dung gửi đi {co} byte VƯỢT SỨC ĐỌC một lần của mô hình (cửa sổ ngữ cảnh) — nhà cung cấp từ chối, khối không cho qua. "
                "Chia nhỏ nội dung rồi chạy lại.")
    if "timeout" in kieu or "timed out" in chu or "timeout" in chu:
        return f"{ten}: QUÁ GIỜ chờ mô hình trả lời (nội dung {co} byte) — khối không cho qua. Chạy lại; nếu vẫn quá giờ thì chia nhỏ nội dung."
    return None


async def chay_thu_mo_hinh_nguoi_gac(loai_khoi: str, orgid: str | None = None, model_settings: dict[str, Any] | None = None):
    """[va_nguoi_gac_claude] (mô hình Claude đã dựng, tên gửi trên dây) cho một "người gác" theo file mô hình khối; None = file không bảo đổi ⇒ bên gọi giữ đường cũ.
    Dựng bằng bộ dựng Claude GỐC của Capitol (địa chỉ gốc = cổng AI) y như hộp phiên (e2b_chat_service._build_model): phải ra mô hình Anthropic TRỰC TIẾP mang đúng
    tên gửi — lớp bọc lùi-sang-Bedrock của Capitol (hộp tự gọi Bedrock, đi vòng cổng + sổ) ⇒ NÉM. File hỏng / tên lạ ⇒ NÉM (không lặng lẽ chạy luna)."""
    if loai_khoi not in CHAY_THU_KHOI_NGUOI_GAC:
        raise ValueError(f"chay_thu_mo_hinh_nguoi_gac: '{loai_khoi}' không phải loại người gác ({', '.join(CHAY_THU_KHOI_NGUOI_GAC)})")
    ep = _chay_thu_mo_hinh_ep(loai_khoi, "")
    if not ep:
        return None
    from ai.agentic.models.fallback import get_anthropic_model_with_bedrock_failover

    ten, muc = ep
    mh = get_anthropic_model_with_bedrock_failover(
        AnthropicModels(ten), dict(model_settings or {}), api_key=await resolve_api_key_for_provider(Provider.ANTHROPIC, orgid)
    )
    if getattr(mh, "_model_name", None) != ten:
        raise ValueError(f"người gác {loai_khoi} (chay_thu): mô hình dựng ra ({type(mh).__name__}) không mang tên gửi '{ten}' — không chạy (tránh đi vòng cổng AI)")
    if muc:   # mức suy luận ⇒ tên gửi trên dây là tuyến "<tên>-<mức>" của bảng cổng (như _fetch_model dưới)
        mh._model_name = f"{ten}-{muc}"
    return mh, (f"{ten}-{muc}" if muc else ten)


class ResolvedModel(NamedTuple):
    """An agent node's model and the registry entry it was built from.

    ``model.model_name`` is the provider's wire id, which changes with
    provider routing (under Bedrock-primary or residency it is a Bedrock
    inference-profile id). Look up per-model limits such as the context
    budget by ``registry_model_id``, never by the model's name.
    """

    model: Model
    registry_model_id: str


class ModelResolutionMixin:
    """Internal building block of ``AgentBaseNode`` — not for standalone use."""

    # Anthropic-primary / Bedrock-fallback opt-in. True lets the
    # ``ai.agentic.models.fallback`` wrapper wrap the Anthropic-direct model
    # with Bedrock (subject to ``settings.BEDROCK_FALLBACK_ENABLED``).
    # Subclasses that must never use Bedrock override to ``False``.
    _enable_bedrock_failover: ClassVar[bool] = True

    def _model_settings(self) -> dict[str, Any]:
        """Build the pydantic-ai model-settings dict from validated params.

        OVERRIDABLE HOOK. The default reads the superset of settings params;
        each setting is emitted only when the node actually declares (and the
        run provides) the corresponding param, so nodes with narrower param
        sets get exactly their old behaviour. Vercel Deploy overrides this
        entirely (its timeout comes from a differently-named param).
        """
        model_settings: dict[str, Any] = {}
        max_tokens: int | None = parse_max_tokens(self._validated_params)
        if max_tokens is not None:
            model_settings["max_tokens"] = max_tokens

        if (
            "temperature" in self._validated_params
            and self._validated_params["temperature"] is not None
        ):
            model_settings["temperature"] = float(self._validated_params["temperature"])

        if self._validated_params.get("timeout", 600):
            model_settings["timeout"] = int(self._validated_params["timeout"])

        if self._validated_params.get("parallel_tool_calls", False):
            model_settings["parallel_tool_calls"] = self._validated_params[
                "parallel_tool_calls"
            ]

        if self._validated_params.get("fast_mode", False):
            model_settings["fast_mode"] = True

        if self._validated_params.get("tool_choice", None):
            model_settings["tool_choice"] = self._validated_params["tool_choice"]

        if (
            "top_p" in self._validated_params
            and self._validated_params["top_p"] is not None
        ):
            model_settings["top_p"] = float(self._validated_params["top_p"])

        if (
            "top_k" in self._validated_params
            and self._validated_params["top_k"] is not None
        ):
            model_settings["top_k"] = int(self._validated_params["top_k"])

        if (
            "frequency_penalty" in self._validated_params
            and self._validated_params["frequency_penalty"] is not None
        ):
            model_settings["frequency_penalty"] = float(
                self._validated_params["frequency_penalty"]
            )

        if (
            "presence_penalty" in self._validated_params
            and self._validated_params["presence_penalty"] is not None
        ):
            model_settings["presence_penalty"] = float(
                self._validated_params["presence_penalty"]
            )

        if (
            "seed" in self._validated_params
            and self._validated_params["seed"] is not None
        ):
            model_settings["seed"] = int(self._validated_params["seed"])

        if self._validated_params.get("reasoning", None):
            model_settings["reasoning"] = self._validated_params["reasoning"]

        return model_settings

    @final
    def _stable_prompt_cache_key(self, model_name: str) -> str | None:
        """Stable OpenAI / OpenRouter ``prompt_cache_key`` for this agent node.

        Derived only from cache-relevant identity — the workflow + node id
        (which fix the system prompt and tool set) plus the model — so requests
        sharing the same prefix route to the same backend across runs and users
        and actually hit the prefix cache. Deliberately excludes per-request /
        per-user data (task text, user id) which would scatter routing and
        defeat the cache. Hashed so no workflow/node identifiers leak to the
        provider and the value is a fixed length. ``None`` disables the hint.
        """
        if not settings.PROMPT_CACHE_ENABLED:
            return None
        raw = f"{self._workflowid}:{self.node_id}:{model_name}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @final
    async def _fetch_model(self, model: str) -> ResolvedModel:
        """Resolve the model string to a registry model and build it.

        Falls back to default models if the requested model is not found:
        - Anthropic: Claude Sonnet 4.6
        - OpenAI: GPT 5.2
        - OpenRouter: Gemini 3.1 Pro Preview
        """
        # [VÁ chay_thu] xem chú thích đầu file. [ghi chú] LOẠI khối lấy từ LỚP: lúc chạy thật `self.node_id` của ĐỐI TƯỢNG là mã riêng của
        # nút (vd 5ca1e8ec-…), chữ "agent_node" chỉ còn ở thuộc tính lớp ⇒ bản trước đọc self.node_id nên không khối nào được đổi, lặng lẽ chạy luna.
        loai_khoi = getattr(type(self), "node_id", None)
        ep = _chay_thu_mo_hinh_ep(loai_khoi if isinstance(loai_khoi, str) else None, model)
        # [ghi chú] MỌI lượt đều có dòng tiến độ ghi mô hình THẬT + nguồn, kể cả khi không đổi ⇒ đối chiếu được với sổ cổng.
        if ep:
            await self.emit_progress(f"Mô hình của khối: {ep[0]}{(' — mức suy luận ' + ep[1]) if ep[1] else ''} (nguồn: file mô hình khối; ô model của khối đang ghi '{str(model)[:60]}'; loại khối {loai_khoi}).")
            model = ep[0]
        else:
            await self.emit_progress(f"Mô hình của khối: {str(model)[:60]} (nguồn: ô model của khối; loại khối {loai_khoi}).")
        model_settings = self._model_settings()
        registry_model = await self._resolve_registry_model(model)
        kq = ResolvedModel(
            model=await self._build_provider_model(registry_model, model_settings),
            registry_model_id=registry_model.value,
        )
        if ep and ep[1]:   # [VÁ chay_thu] mức suy luận ⇒ đổi tên gửi trên dây sang tuyến "<tên>-<mức>" của cổng AI; không đổi được ⇒ NÉM (không lặng lẽ chạy mức khác)
            if getattr(kq.model, "_model_name", None) != ep[0]:
                raise ValueError(f"không đặt được mức suy luận '{ep[1]}': mô hình dựng ra ({type(kq.model).__name__}) không mang tên gửi '{ep[0]}'")
            kq.model._model_name = f"{ep[0]}-{ep[1]}"
        return kq

    @final
    async def _resolve_registry_model(self, model: str) -> RegistryModel:
        """Map the model string to the registry model the node will run."""
        # Resolve deprecated OpenRouter model aliases so existing workflows
        # that reference removed models (e.g. gemini-3-pro-preview) keep working.
        if model in DEPRECATED_OPENROUTER_MODEL_ALIASES:
            replacement = DEPRECATED_OPENROUTER_MODEL_ALIASES[model]
            await self.emit_progress(
                f"Model '{model}' has been deprecated. "
                f"Using replacement model '{replacement.value}'."
            )
            return replacement

        # Resolve deprecated Anthropic model aliases so existing workflows
        # that reference old model strings keep working.
        if model in DEPRECATED_ANTHROPIC_MODEL_ALIASES:
            replacement = DEPRECATED_ANTHROPIC_MODEL_ALIASES[model]
            await self.emit_progress(
                f"Model '{model}' has been deprecated. "
                f"Using replacement model '{replacement.value}'."
            )
            return replacement

        # Resolve retired OpenAI model aliases so existing workflows that
        # reference shut-down models (e.g. o3-mini) keep working.
        if model in DEPRECATED_OPENAI_MODEL_ALIASES:
            replacement = DEPRECATED_OPENAI_MODEL_ALIASES[model]
            await self.emit_progress(
                f"Model '{model}' has been deprecated. "
                f"Using replacement model '{replacement.value}'."
            )
            return replacement

        for provider_models in (AnthropicModels, OpenAIModels, OpenRouterModels):
            for registry_model in provider_models:
                if model == registry_model.value:
                    return registry_model

        # Model not found - determine provider and use default
        # Check if the model string suggests a specific provider
        if model.startswith("claude") or "anthropic" in model.lower():
            default_model = AnthropicModels.CLAUDE_SONNET_4_6
            await self.emit_progress(
                f"Model '{model}' not found in Anthropic models. "
                f"Defaulting to {default_model.value}."
            )
            return default_model

        if model.startswith("gpt") or model.startswith("o3") or model.startswith("o4"):
            default_model = OpenAIModels.GPT_5_2
            await self.emit_progress(
                f"Model '{model}' not found in OpenAI models. "
                f"Defaulting to {default_model.value}."
            )
            return default_model

        if "/" in model:  # OpenRouter models use provider/model format
            default_model = OpenRouterModels.GOOGLE_GEMINI_3_1_PRO_PREVIEW
            await self.emit_progress(
                f"Model '{model}' not found in OpenRouter models. "
                f"Defaulting to {default_model.value}."
            )
            return default_model

        # If we can't determine the provider, default to Anthropic Sonnet 4.6
        default_model = AnthropicModels.CLAUDE_SONNET_4_6
        await self.emit_progress(
            f"Model '{model}' not recognized. "
            f"Defaulting to Anthropic {default_model.value}."
        )
        return default_model

    @final
    async def _build_provider_model(
        self, registry_model: RegistryModel, model_settings: dict[str, Any]
    ) -> Model:
        """Build the provider model for ``registry_model`` with the node's
        per-org API key and the deployment's provider routing."""
        if isinstance(registry_model, AnthropicModels):
            return get_anthropic_model_with_bedrock_failover_for_agent_node(
                registry_model,
                model_settings,
                api_key=await resolve_api_key_for_provider(
                    Provider.ANTHROPIC, self._orgid
                ),
                enable_bedrock_failover=self._enable_bedrock_failover,
            )
        if isinstance(registry_model, OpenAIModels):
            return get_openai_model_for_agent_node(
                registry_model,
                model_settings,
                api_key=await resolve_api_key_for_provider(
                    Provider.OPENAI, self._orgid
                ),
                prompt_cache_key=self._stable_prompt_cache_key(registry_model.value),
            )
        return get_openrouter_model_for_agent_node(
            registry_model,
            model_settings,
            api_key=await resolve_api_key_for_provider(
                Provider.OPENROUTER, self._orgid
            ),
            prompt_cache_key=self._stable_prompt_cache_key(registry_model.value),
        )
