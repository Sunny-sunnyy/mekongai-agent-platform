import asyncio
import json
import logging
import math
import re
import unicodedata
from collections.abc import Awaitable, Callable

import fitz  # PyMuPDF
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from sentry_sdk import capture_exception

# [VÁ 9 chay_thu, chiều 04/11/2026 — nhóm kỹ thuật, User: "làm các hướng A, B, C, D"; điểm vá do nhóm kỹ thuật đọc (DE_XUAT_DUNG_THEM.md mục 6)]
# Khối "Che thông tin" GHI CỨNG Claude Sonnet 4.6 (dòng gốc :77) ⇒ máy chỉ có khoá OpenAI, User chỉ dùng gpt-6-luna ⇒ đổi sang gpt-6-luna qua
# hàm có sẵn của Capitol get_openai_model_for_agent_node (cách digest.py:163-184 đã chạy). Bỏ model_settings vì {"thinking": …} là khoá riêng
# của Anthropic; mức suy nghĩ lấy từ registry (low). Khoá: Provider.OPENAI (khoá tổ chức 404 ⇒ khoá chung OPENAI_API_KEY). Logic che giữ NGUYÊN.
from ai.agentic.models.openai import get_openai_model_for_agent_node
from core.config.ai.anthropic import AnthropicModels  # giữ: chú thích kiểu ở _is_thinking_required (:169) còn dùng
from core.config.ai.openai import OpenAIModels
from services.provider_keys import Provider, resolve_api_key_for_provider
from services.redaction.custom_exceptions import (
    RedactAgentNotDefinedError,
    RedactDataInvalidError,
    RedactionTypeInvalidForDataError,
    RedactOuputIsNotJsonError,
)
from services.redaction.enums import RedactionReplacementType, RedactionType
from services.redaction.schemas import (
    RedactionConfig,
    RedactionCustomConfig,
    RedactionPIIConfig,
    RedactionRegexConfig,
)

logger = logging.getLogger(__name__)

REDACTION_SYSTEM_PROMPT = """
You are a redaction agent.
Your task is to identify and redact information according to the redaction instructions provided.

Key Rules:
    - You will receive raw data (which may be text, JSON, CSV, or other formats).
    - Apply the given redaction instructions directly to the data.
    - Output must preserve the original format of the input data (e.g., JSON in -> valid JSON out).
    - HARD REQUIREMENT: Preserve the original text exactly aside from the redactions. Do not change capitalization, punctuation (including quotes/apostrophes), whitespace, spacing, or line breaks unless the redaction instructions explicitly require it.
    - Do not add, remove, or explain anything outside the required redactions.
    - For JSON inputs, ensure the output maintains valid JSON format. Return only the JSON object string that can be parsed by Python's json.loads() function without errors. Do not return a python dictionary or list.
    - Do not ask for clarifications — always perform the best possible redaction with the given instructions.
{additional_instructions}
"""

ADDITIONAL_INSTRUCTIONS_FOR_PII = """
    - Remove the PII entities provided by the user from the data.
    - The entities can either be masked or removed completely depending on the config provided by the user.
"""

ADDITIONAL_INSTRUCTIONS_FOR_CUSTOM = """
    - Redact the data according to the instructions provided by the user.
    - Deny the request if the instructions are not for redaction. Assume the instructions are for redaction unless clearly misaligned with redaction. 
"""

USER_PROMPT = """
User config for redactions:
{user_config}

Data to be redacted:
{data}
"""

# [ghi chú] Trần CỨNG để giữ chỗ tiền được. Vượt ⇒ TỪ CHỐI cả khối, KHÔNG cắt:
# cắt đầu vào của khối che = phần bị cắt không được che (lộ dữ liệu). Đo theo BYTE UTF-8 (token ≤ byte).
CHE_DOAN_TOI_DA, CHE_DOAN_BYTE, CHE_CAU_HINH_BYTE, CHE_RA_MOT_LAN = 40, 6_000, 4_000, 2_000   # đường chữ: ≤ 40 đoạn × 1 lần gọi
CHE_JSON_BYTE, CHE_JSON_RA = 12_000, 16_000                                                    # đường JSON: 1 lần gọi, trả lại cả JSON
CHE_PDF_ANH_TOI_DA = 0.30   # [va_che_pdf vòng 3] ảnh phủ ≥ 30 % trang ⇒ coi là bản chụp ⇒ không che được ⇒ từ chối
# [ghi chú] đường PDF (chế độ AI): TỔNG số đoạn của MỌI PDF trong một khối ≤ CHE_DOAN_TOI_DA (đếm trên đối tượng dịch vụ — mỗi
# khối tạo một RedactionService, AB nodes/redaction/node.py:65); mỗi đoạn cùng trần byte như đường chữ. Kiểm HẾT trước lần gọi đầu.


def _gioi_han(ra: int, claude: bool = False) -> dict:
    """model_settings + usage_limits cho MỘT lần gọi không công cụ (đầu ra chữ ⇒ đúng 1 request).
    [ghi chú] claude=True (file mô hình khối bảo Claude): KHÔNG gửi max_tokens nhỏ, KHÔNG trần token đầu ra — giữ đúng 1 lần gọi.
    Lý do riêng của khối che: đầu ra bị cắt giữa chừng = phần sau không được che; Claude tính cả token suy luận vào đầu ra nên trần nhỏ dễ chạm."""
    if claude:
        return {"usage_limits": UsageLimits(request_limit=1)}
    return {"model_settings": {"max_tokens": ra}, "usage_limits": UsageLimits(request_limit=1, output_tokens_limit=ra)}


# [ghi chú] Phần so chữ của đường che PDF (mã gốc Capitol)
# chuẩn hoá mỗi từ bằng mẫu CHỈ nhận chữ Latin không dấu ở hai đầu: r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$" ⇒ chữ có dấu ở đầu / cuối từ bị cắt như dấu câu:
#   "Đỗ", "Ý", "Ỷ" ⇒ rỗng ⇒ rơi khỏi cụm cần che ⇒ họ còn hiện trên file "đã che";   "Thế" / "thị" ⇒ cùng thành "th", "Ân" / "ăn" ⇒ cùng thành "n" ⇒ che nhầm
#   chữ của cụm khác ("khu đô thị Hồ Tây" mất "thị Hồ").
# Sửa: chuẩn hoá theo UNICODE — đưa về dạng dựng sẵn NFC trước (PDF / mô hình có thể trả dạng tách dấu NFD), rồi chỉ cắt ký tự KHÔNG phải chữ / số theo Unicode
# (\W và "_") ở hai đầu. So vẫn theo CẢ CỤM (dãy từ liền nhau bằng đúng dãy từ của cụm), như gốc. Tách thành hàm thuần để thử được không cần PDF.
def _chuan_tu_che(tu: str) -> str:
    """Một từ ⇒ dạng để so: NFC, bỏ dấu câu / ký hiệu ở hai đầu (giữ mọi chữ có dấu), chữ thường."""
    return re.sub(r"^[\W_]+|[\W_]+$", "", unicodedata.normalize("NFC", tu or "")).lower()


def chi_so_tu_can_che(tu_trang: list[str], cum_can_che: list[str]) -> list[int]:
    """Chỉ số (trong danh sách từ của trang, theo thứ tự đọc) các từ phải che: mọi chỗ một DÃY TỪ LIỀN NHAU của trang bằng đúng dãy từ của một cụm cần che
    (sau chuẩn hoá _chuan_tu_che). Không che từ lẻ trùng một phần của cụm; cụm chuẩn hoá ra rỗng ⇒ bỏ qua."""
    chuan = [_chuan_tu_che(w) for w in tu_trang]
    ra: set[int] = set()
    for cum in cum_can_che:
        tu = [x for x in (_chuan_tu_che(t) for t in (cum or "").split()) if x]
        n = len(tu)
        if not n:
            continue
        for i in range(0, max(len(chuan) - n + 1, 0)):
            if chuan[i : i + n] == tu:
                ra.update(range(i, i + n))
    return sorted(ra)


# [ghi chú] LƯỚI ĐỠ SAU KHI CHE — không thêm lời gọi AI. Bước che chính chỉ che được chỗ chữ trong file viết ĐÚNG
# như cụm AI trả (so từng từ). Chỗ viết khác cách ("Đỗ-Thị Hà", "Đỗ - Thị Hà", "0903.111.222", "0903111222", "CCCD:001099012345.") lọt mà khối vẫn báo xong
# ⇒ lộ lặng. Lưới: sau khi che, ĐỌC LẠI lớp chữ còn lại của file ra; còn chỗ khớp một cụm cần che theo luật dưới ⇒ TỪ CHỐI trả file (như "không trả PDF che
# thiếu" đang có), báo số chỗ + trang, KHÔNG in chính chuỗi còn sót (lời báo / log có thể tới chỗ người ngoài thấy).
#   (1) cụm CHỮ từ 2 từ trở lên: tách lại từ của file ở mọi ký tự không phải chữ / số ("Đỗ-Thị" ⇒ "Đỗ", "Thị"; từ chỉ có dấu câu bị bỏ) rồi tìm đúng DÃY TỪ của
#       cụm. Cụm chữ MỘT từ: bỏ qua (so chuỗi con sẽ từ chối oan — "Ý" trong "Ý kiến"; bước che chính đã che mọi từ bằng đúng nó).
#   (2) cụm SỐ (bỏ dấu câu / khoảng trắng chỉ còn chữ số): từ 6 chữ số ⇒ tìm chuỗi chữ số liền đó trong chuỗi chữ số của từng DÒNG (không nối cả trang — tránh
#       khớp chéo dòng); dưới 6 chữ số: bỏ qua.
#   (3) chỉ xét cụm PHẢI CHE đủ cả dãy ⇒ cụm phải giữ trùng một phần ("khu đô thị Hồ Tây") không kích lưới.
# GIỚI HẠN đã biết: số bị ngắt qua hai dòng; tên một từ dính gạch nối; cụm AI không trả về (lưới chỉ soát theo danh sách cụm AI đã trả).
SO_CHU_SO_TOI_THIEU = 6


def _tach_lai_tu(chu: str) -> list[str]:
    """Chuỗi ⇒ các mảnh chữ / số (NFC, chữ thường), cắt ở MỌI ký tự không phải chữ / số theo Unicode."""
    return [x.lower() for x in re.split(r"[\W_]+", unicodedata.normalize("NFC", chu or "")) if x]


def dem_sot_sau_che(dong_tu: list[list[str]], cum_can_che: list[str]) -> int:
    """Số chỗ CÒN SÓT sau khi che. `dong_tu`: các từ còn lại của file ra, gom theo DÒNG, theo thứ tự đọc. Thuần — không đọc gì ngoài tham số."""
    phang = [m for dong in dong_tu for w in dong for m in _tach_lai_tu(w)]
    so_dong = ["".join(ch for w in dong for ch in unicodedata.normalize("NFC", w or "") if ch.isdigit()) for dong in dong_tu]
    n, da_xet = 0, set()
    for cum in cum_can_che:
        manh = tuple(_tach_lai_tu(cum))
        if not manh or manh in da_xet:
            continue
        da_xet.add(manh)
        if all(m.isdigit() for m in manh):   # cụm SỐ
            so = "".join(manh)
            if len(so) >= SO_CHU_SO_TOI_THIEU:
                n += sum(d.count(so) for d in so_dong)
        elif len(manh) >= 2:                 # cụm chữ (hoặc chữ lẫn số) từ 2 mảnh
            k = len(manh)
            n += sum(1 for i in range(len(phang) - k + 1) if tuple(phang[i : i + k]) == manh)
    return n


CHE_SONG_SONG = 8   # [bản 2] số lời gọi trích cụm chạy cùng lúc (xếp hàng, không từ chối)


def _loi_ro_che(e: BaseException, co_byte: int) -> str | None:
    from workflow.node.base.agent.model_resolution import chay_thu_loi_ro_nguoi_gac

    return chay_thu_loi_ro_nguoi_gac(e, "Khối che thông tin", co_byte)


def _qua_tran(ten: str, co: int, tran: int, bo: bool = False) -> None:
    """bo=True ([ghi chú] khối che chạy Claude): KHÔNG rào cỡ đầu vào. Tuyến tính trần tiền (luna): như cũ."""
    if co > tran and not bo:
        raise ValueError(f"Khối che thông tin từ chối: {ten} {co} vượt trần {tran} — không che một phần (phần còn lại sẽ lộ). Chia nhỏ đầu vào.")


TERM_EXTRACTION_SYSTEM_PROMPT = """
You are a redaction planner.
Return JSON only: {"terms": ["..."]}.
Hard requirements:
- Identify the exact words/phrases to redact per the instructions.
- Include casing variants if relevant (e.g., "Marley" and "MARLEY").
- Do not return redacted text and do not add any extra keys or explanation.
"""


class RedactionService:
    def __init__(self):
        self._agent = None
        self._default_model = OpenAIModels.GPT_6_LUNA  # [VÁ 9] gốc: AnthropicModels.CLAUDE_SONNET_4_6

    def _chunk_text_by_words(
        self,
        *,
        text: str,
        chunk_words: int,
        overlap_words: int,
    ) -> list[str]:
        if chunk_words <= 0:
            raise ValueError("chunk_words must be > 0")
        if overlap_words < 0:
            raise ValueError("overlap_words must be >= 0")
        if overlap_words >= chunk_words:
            raise ValueError("overlap_words must be < chunk_words")

        words = text.split()
        if not words:
            return []

        chunks: list[str] = []
        step = max(chunk_words - overlap_words, 1)
        for start in range(0, len(words), step):
            end = min(start + chunk_words, len(words))
            chunk = " ".join(words[start:end]).strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(words):
                break
        return chunks

    async def _regex_redaction(self, data: str, redactions: list[RedactionRegexConfig]):
        """
        Redacts the data using the regex pattern.
        """

        parts = [
            f"(?P<G{i}>{redaction.regex_pattern})"
            for i, redaction in enumerate(redactions)
        ]

        regex_pattern = f"({'|'.join(parts)})"

        def __replace_function(match: re.Match):
            # Find which group matched and get the corresponding redaction config
            for i in range(len(redactions)):
                if match.group(f"G{i}"):
                    return (
                        redactions[i].replacement
                        if redactions[i].replacement_type
                        == RedactionReplacementType.MASK
                        else ""
                    )
            return match.group(0)  # Return original text if no group matched

        return re.sub(regex_pattern, __replace_function, data)

    def _sanitize_json_output(self, data: str) -> dict | list:
        """
        Sanitizes the json output to be a valid json object.
        Simple approach: find the first and last bracket and return the substring between them.
        This is because the only failure mode observed is when the actual json is prefixed or suffixed with some additional text.
        """

        index_of_first_dict_bracket = data.find("{")
        index_of_first_list_bracket = data.find("[")

        if index_of_first_dict_bracket == -1 and index_of_first_list_bracket == -1:
            raise RedactOuputIsNotJsonError()

        if (
            index_of_first_list_bracket == -1
            or index_of_first_dict_bracket < index_of_first_list_bracket
        ):
            # data is a dict
            start_index = index_of_first_dict_bracket
            end_index = data.rfind("}")
            if end_index == -1:
                raise RedactOuputIsNotJsonError()
        else:
            # data is a list
            start_index = index_of_first_list_bracket
            end_index = data.rfind("]")
            if end_index == -1:
                raise RedactOuputIsNotJsonError()
        data = data[start_index : end_index + 1]
        return json.loads(data, strict=False)

    def _is_thinking_required(self, data: str | dict | list) -> AnthropicModels:
        if isinstance(data, str):
            return False

        if isinstance(data, dict) or isinstance(data, list):
            # determine nesting and depth of the data
            # Calculate the size and complexity of the JSON data
            data_size = len(str(data))
            if data_size > 50000:
                return True

            # Calculate nesting depth
            def calculate_depth(obj: dict | list, current_depth: int = 1):
                max_depth = current_depth
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if isinstance(value, (dict, list)):
                            depth = calculate_depth(value, current_depth + 1)
                            max_depth = max(max_depth, depth)
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, (dict, list)):
                            depth = calculate_depth(item, current_depth + 1)
                            max_depth = max(max_depth, depth)
                return max_depth

            nesting_depth = calculate_depth(data)

            # Choose model based on complexity
            if nesting_depth > 5:
                return True  # Use more powerful model for complex data
            else:
                return False  # Use standard model for simpler data

    async def _qua_tran_che(self, ten: str, co: int, tran: int, api_key: str | None = None) -> None:
        """[bản 2] Rào cỡ đầu vào của khối che: CHỈ hỏi "đang chạy Claude không" khi cỡ thật sự vượt (đường thường không đổi thứ tự so với bản trước).
        Chạy Claude ⇒ bỏ qua (User "Bỏ hẳn rào cỡ"); tuyến tính trần tiền (luna) ⇒ từ chối như cũ."""
        if co > tran:
            try:
                _claude = (await self._mo_hinh_che(api_key))[1]
            except ImportError:   # bản chọn mô hình trong hộp chưa có hàm người gác (gói gắn lệch) ⇒ không chắc đang chạy Claude ⇒ GIỮ rào (đóng khi không chắc)
                _claude = False
            _qua_tran(ten, co, tran, bo=_claude)

    async def _mo_hinh_che(self, api_key: str | None = None):
        """[ghi chú] (mô hình, chạy Claude?) của khối che — hỏi file mô hình khối MỘT lần cho mỗi đối tượng dịch vụ (mỗi khối tạo một
        RedactionService). Che chữ và che PDF dùng chung dòng `text_redaction_node` (dịch vụ không biết khối nào gọi). File trống ⇒ luna như trước."""
        if not hasattr(self, "_mh_che"):
            from workflow.node.base.agent.model_resolution import chay_thu_mo_hinh_nguoi_gac

            c = await chay_thu_mo_hinh_nguoi_gac("text_redaction_node", None, {"timeout": 600})
            self._mh_che = (c[0], True) if c else (get_openai_model_for_agent_node(self._default_model, api_key=api_key), False)
            logger.info("[NGUOI_GAC_MO_HINH] khoi=text_redaction_node mo_hinh=%s", c[1] if c else "gpt-6-luna")
        return self._mh_che

    def _get_anthropic_model_settings(self, thinking_required: bool) -> dict:
        if thinking_required:
            return {
                "thinking": {"type": "enabled", "budget_tokens": 20000},
                "timeout": 600,
            }
        else:
            return {"thinking": {"type": "disabled"}, "timeout": 600}

    async def _create_agent(
        self, thinking_required: bool, system_prompt: str, api_key: str | None = None
    ) -> Agent:
        return Agent(
            model=(await self._mo_hinh_che(api_key))[0],  # [VÁ 9] + [va_nguoi_gac_claude]
            system_prompt=system_prompt,
            tools=[],
        )

    async def _agentic_redaction(
        self,
        data: str | dict | list,
        redaction_configs: list[RedactionCustomConfig | RedactionPIIConfig],
        api_key: str | None = None,
    ) -> str | dict | list:
        data_is_json = False
        if isinstance(data, list) or isinstance(data, dict):
            # check if the data is a valid JSON object
            try:
                json.dumps(data)
                data_is_json = True
                # output_mode = NativeOutput
            except Exception as exc:
                capture_exception(exc)
                raise RedactDataInvalidError()

        redaction_configs_updated = []
        for redaction_config in redaction_configs:
            redaction_config_updated = {}
            if isinstance(redaction_config, RedactionPIIConfig):
                redaction_config_updated = {
                    "pii_entity": redaction_config.pii_entity.value
                }
                if redaction_config.replacement_type == RedactionReplacementType.MASK:
                    redaction_config_updated["replacement"] = (
                        redaction_config.replacement
                    )
                else:
                    redaction_config_updated["replacement"] = ""

                redaction_configs_updated.append(redaction_config_updated)
            if isinstance(redaction_config, RedactionCustomConfig):
                redaction_configs_updated.append(redaction_config.model_dump())

        thinking_required = self._is_thinking_required(data)

        system_prompt_additionals = ""
        if isinstance(redaction_configs, RedactionPIIConfig):
            system_prompt_additionals = ADDITIONAL_INSTRUCTIONS_FOR_PII
        elif isinstance(redaction_configs, RedactionCustomConfig):
            system_prompt_additionals = ADDITIONAL_INSTRUCTIONS_FOR_CUSTOM

        system_prompt = REDACTION_SYSTEM_PROMPT.format(
            additional_instructions=system_prompt_additionals
        )
        self._agent = await self._create_agent(
            thinking_required=thinking_required,
            system_prompt=system_prompt,
            api_key=api_key,
        )

        if not self._agent:
            raise RedactAgentNotDefinedError()

        user_prompt = USER_PROMPT.format(
            user_config=redaction_configs_updated, data=data
        )

        await self._qua_tran_che("cấu hình che (byte)", len(str(redaction_configs_updated).encode()), CHE_CAU_HINH_BYTE, api_key)   # [va_muc_toi_da_r2]
        # [D minor 1] đo THẲNG chuỗi gửi đi (USER_PROMPT.format dùng repr Python của data, không phải json.dumps)
        await self._qua_tran_che("tin gửi JSON (byte)", len(user_prompt.encode()), CHE_JSON_BYTE + CHE_CAU_HINH_BYTE + 72, api_key)
        try:
            result = await self._agent.run(user_prompt=user_prompt, **_gioi_han(CHE_JSON_RA, (await self._mo_hinh_che(api_key))[1]))
        except Exception as _e:
            _ro = _loi_ro_che(_e, len(user_prompt.encode()))
            if _ro:
                raise ValueError(_ro) from _e
            raise
        if data_is_json:
            return self._sanitize_json_output(result.output)
        else:
            return result.output

    def _build_user_config(
        self, redaction_configs: list[RedactionCustomConfig | RedactionPIIConfig]
    ) -> list[dict]:
        user_config: list[dict] = []
        for cfg in redaction_configs:
            if isinstance(cfg, RedactionPIIConfig):
                user_config.append({"pii_entity": cfg.pii_entity.value})
            elif isinstance(cfg, RedactionCustomConfig):
                user_config.append(cfg.model_dump())
        return user_config

    async def _extract_terms_for_chunk(
        self,
        *,
        chunk: str,
        user_config: list[dict],
        api_key: str | None = None,
    ) -> list[str]:
        if not hasattr(self, "_term_model"):
            self._term_model = (await self._mo_hinh_che(api_key))[0]  # [VÁ 9] + [va_nguoi_gac_claude]
        agent = Agent(
            model=self._term_model,
            system_prompt=TERM_EXTRACTION_SYSTEM_PROMPT,
            tools=[],
        )
        await self._qua_tran_che("một đoạn (byte)", len(chunk.encode()), CHE_DOAN_BYTE, api_key)   # [va_muc_toi_da_r2]
        # [bản 2] hết rào số đoạn trên tuyến Claude ⇒ văn bản lớn = hàng trăm đoạn; bản gốc gọi TẤT CẢ cùng lúc (asyncio.gather) ⇒ nhà cung cấp trả 429 hàng loạt.
        # Xếp hàng ≤ CHE_SONG_SONG lời gọi một lúc — KHÔNG phải rào cỡ: không từ chối, không bỏ đoạn nào, chỉ chậm hơn.
        if not hasattr(self, "_che_hang"):
            self._che_hang = asyncio.Semaphore(CHE_SONG_SONG)
        async with self._che_hang:
            try:
                result = await agent.run(
                    user_prompt=USER_PROMPT.format(user_config=user_config, data=chunk), **_gioi_han(CHE_RA_MOT_LAN, (await self._mo_hinh_che(api_key))[1])
                )
            except Exception as _e:
                _ro = _loi_ro_che(_e, len(chunk.encode()))
                if _ro:
                    raise ValueError(_ro) from _e
                raise
        parsed = self._sanitize_json_output(result.output)
        terms = parsed.get("terms", []) if isinstance(parsed, dict) else []
        return [t.strip() for t in terms if isinstance(t, str) and t.strip()]

    def _apply_term_redactions(
        self,
        *,
        text: str,
        terms: list[str],
        replacement: str = "<redacted>",
    ) -> str:
        if not text or not terms:
            return text
        # [va_che_co_dau] đưa chữ và cụm về CÙNG dạng Unicode dựng sẵn (NFC): mô hình thường trả cụm dạng NFC, chữ nguồn có thể ở dạng tách dấu (NFD) ⇒ so chuỗi
        # thô không khớp ⇒ SÓT. Hệ quả: chữ trả về ở dạng NFC (nhìn y hệt, mã ký tự có thể khác bản gốc). Cách so của đường chữ (thay chuỗi con, cụm dài trước) giữ như gốc.
        text = unicodedata.normalize("NFC", text)
        terms = [unicodedata.normalize("NFC", t) for t in terms if isinstance(t, str) and t]
        if not terms:
            return text

        # Redact longer phrases first to avoid partial matches swallowing them.
        unique_terms: list[str] = []
        seen = set()
        for t in terms:
            if t not in seen:
                unique_terms.append(t)
                seen.add(t)
        unique_terms.sort(key=len, reverse=True)

        pattern = re.compile("|".join(re.escape(t) for t in unique_terms))
        return pattern.sub(replacement, text)

    async def _term_based_text_redaction(
        self,
        *,
        data: str,
        redaction: RedactionConfig,
        term_extraction_chunk_words: int = 350,
        term_extraction_overlap_words: int = 30,
        api_key: str | None = None,
    ) -> str:
        user_config = self._build_user_config(redaction.redactions)
        chunks = self._chunk_text_by_words(
            text=data,
            chunk_words=term_extraction_chunk_words,
            overlap_words=term_extraction_overlap_words,
        )
        if not chunks:
            return data
        # [va_muc_toi_da_r2] kiểm HẾT trước khi gọi AI lần nào (từ chối sớm ⇒ 0 đồng)
        await self._qua_tran_che("số đoạn", len(chunks), CHE_DOAN_TOI_DA, api_key)
        await self._qua_tran_che("cấu hình che (byte)", len(str(user_config).encode()), CHE_CAU_HINH_BYTE, api_key)
        for c in chunks:
            await self._qua_tran_che("một đoạn (byte)", len(c.encode()), CHE_DOAN_BYTE, api_key)

        # Extract terms across chunks concurrently.
        chunk_terms = await asyncio.gather(
            *[
                self._extract_terms_for_chunk(
                    chunk=chunk, user_config=user_config, api_key=api_key
                )
                for chunk in chunks
            ]
        )
        all_terms: list[str] = []
        for terms in chunk_terms:
            all_terms.extend(terms)

        da_che = self._apply_term_redactions(text=data, terms=all_terms)
        # [va_che_luoi_do] lưới đỡ: đọc lại chữ ĐÃ che theo từng dòng; còn chỗ khớp cụm cần che (viết khác cách) ⇒ từ chối, không trả chữ che thiếu
        _sot = dem_sot_sau_che([dong.split() for dong in da_che.splitlines()], all_terms)
        if _sot:
            logger.warning("Text redaction: lưới đỡ sau che — còn %s chỗ khớp cụm cần che", _sot)
            raise ValueError(f"Khối che thông tin từ chối: sau khi che vẫn còn {_sot} chỗ khớp cụm cần che (tên hoặc số viết khác cách trong văn bản — ví dụ có "
                             "gạch nối, dấu chấm giữa các phần) — không trả văn bản che thiếu. Sửa cách viết trong văn bản rồi chạy lại.")
        return da_che

    async def _extract_redaction_terms(
        self,
        *,
        data: str,
        redaction_configs: list[RedactionCustomConfig | RedactionPIIConfig],
    ) -> list[str]:
        user_config = self._build_user_config(redaction_configs)

        result = await self._agent.run(
            user_prompt=USER_PROMPT.format(user_config=user_config, data=data)
        )
        parsed = self._sanitize_json_output(result.output)
        terms = parsed.get("terms", []) if isinstance(parsed, dict) else []
        return [t.strip() for t in terms if isinstance(t, str) and t.strip()]

    async def redact_text(
        self, data: str, redaction: RedactionConfig, orgid: str | None = None
    ) -> str:
        if redaction.redaction_type == RedactionType.REGEX:
            return await self._regex_redaction(data, redaction.redactions)
        else:
            # Avoid context overflow: chunk, extract terms, and apply them to the original.
            api_key = await resolve_api_key_for_provider(Provider.OPENAI, orgid)
            return await self._term_based_text_redaction(
                data=data, redaction=redaction, api_key=api_key
            )

    async def redact_json(
        self, data: dict | list, redaction: RedactionConfig, orgid: str | None = None
    ) -> dict | list:
        if redaction.redaction_type == RedactionType.REGEX:
            raise RedactionTypeInvalidForDataError()
        else:
            api_key = await resolve_api_key_for_provider(Provider.OPENAI, orgid)
            return await self._agentic_redaction(
                data, redaction.redactions, api_key=api_key
            )

    async def redact_pdf(
        self,
        *,
        pdf_bytes: bytes,
        redaction: RedactionConfig,
        render_dpi: int = 200,
        term_extraction_chunk_words: int = 350,
        term_extraction_overlap_words: int = 30,
        progress_callback: Callable[[dict], Awaitable[None]] | None = None,
        orgid: str | None = None,
        canh_bao: list[str] | None = None,   # [va_che_pdf vòng 3b] người gọi (khối) đưa danh sách ⇒ nhận cảnh báo ảnh không che để đưa vào ĐẦU RA
    ) -> bytes:
        """
        Redact a PDF by identifying sensitive terms (LLM or regex), rasterizing pages,
        and blacking out matched words.

        Args:
            pdf_bytes: Raw PDF bytes.
            redaction: Redaction configuration (CUSTOM/PII use LLM; REGEX uses provided patterns).
            render_dpi: DPI for rasterization.

        Returns:
            bytes: Redacted PDF bytes (image-based PDF).
        """
        logger.info(
            "PDF redaction started: type=%s bytes=%s",
            redaction.redaction_type,
            len(pdf_bytes),
        )
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = doc.page_count
        # [ghi chú] trang chỉ có ẢNH (bản chụp) ⇒ trích chữ rỗng ⇒ 0 đoạn ⇒ PDF trả NGUYÊN (lộ tên / CCCD trong ảnh)
        # mà khối vẫn báo "đã che" — lỗi có từ gốc Capitol. Kiểm TRƯỚC mọi lần gọi AI, cả chế độ regex: trang có ảnh mà chữ trích < 20 ký tự ⇒
        # từ chối cả khối. Trang vừa chữ vừa ảnh ⇒ chỉ ghi cảnh báo (chữ NẰM TRONG ảnh không được che — giới hạn đã biết).
        # [vòng 3 — E V4 major] bản chụp + 1 dòng tiêu đề chữ thật vượt ngưỡng 20 ký tự ⇒ thêm luật DIỆN TÍCH: tổng diện tích các ô ảnh (cắt theo
        # trang, get_image_rects) ≥ CHE_PDF_ANH_TOI_DA diện tích trang ⇒ từ chối. Logo / chữ ký nhỏ (< 30 %) vẫn qua, có cảnh báo.
        _trang_anh, _trang_tron, _so_anh_tron = [], [], 0
        for _i, _pg in enumerate(doc, start=1):
            _anh = _pg.get_images(full=True)
            if not _anh:
                continue
            _dt_trang = abs(_pg.rect) or 1.0
            _dt_anh = 0.0
            for _im in _anh:
                for _r in _pg.get_image_rects(_im[0]):
                    _dt_anh += abs(_r & _pg.rect)
            if len((_pg.get_text("text") or "").strip()) < 20 or _dt_anh / _dt_trang >= CHE_PDF_ANH_TOI_DA:
                _trang_anh.append(_i)
            else:
                _trang_tron.append(_i)
                _so_anh_tron += sum(len(_pg.get_image_rects(_im[0])) or 1 for _im in _anh)
        if _trang_anh:
            raise ValueError(f"Khối che PDF từ chối: trang {_trang_anh[:10]} là ẢNH / bản chụp (ảnh ≥ 30 % trang hoặc gần như không có chữ) — không che được nội dung trong ảnh, "
                             "không trả PDF. Dùng bản PDF có chữ (không phải bản chụp).")
        if _trang_tron:
            logger.warning("PDF redaction: trang %s có ảnh — chữ trong ảnh KHÔNG được che (chỉ che lớp chữ)", _trang_tron[:20])
            if canh_bao is not None:   # [vòng 3b — A] cảnh báo phải tới ĐẦU RA của khối, không chỉ log
                canh_bao.append(f"Có {_so_anh_tron} ảnh (trang {_trang_tron[:20]}) KHÔNG được che — chữ trong ảnh còn nguyên.")

        # Phase 1: extract terms for all pages/chunks concurrently.
        page_terms: dict[int, list[str]] = {i: [] for i in range(1, total_pages + 1)}

        if redaction.redaction_type == RedactionType.REGEX:
            for page_index, page in enumerate(doc, start=1):
                page_text = page.get_text("text") or ""
                terms: list[str] = []
                for cfg in redaction.redactions:
                    if isinstance(cfg, RedactionRegexConfig):
                        terms.extend(
                            m.group(0)
                            for m in re.finditer(cfg.regex_pattern, page_text)
                            if m.group(0)
                        )
                page_terms[page_index] = terms
        else:
            user_config = self._build_user_config(redaction.redactions)
            api_key = await resolve_api_key_for_provider(Provider.OPENAI, orgid)

            async def _extract_for_page_chunk(
                page_index: int, chunk: str
            ) -> tuple[int, list[str]]:
                terms = await self._extract_terms_for_chunk(
                    chunk=chunk, user_config=user_config, api_key=api_key
                )
                return page_index, terms

            # [va_che_pdf] tách HẾT các đoạn trước, kiểm trần (số đoạn cộng dồn mọi PDF của khối, byte từng đoạn, cấu hình) TRƯỚC lần gọi AI đầu.
            await self._qua_tran_che("cấu hình che (byte)", len(str(user_config).encode()), CHE_CAU_HINH_BYTE, api_key)
            _doan: list[tuple[int, str]] = []
            for page_index, page in enumerate(doc, start=1):
                for chunk in self._chunk_text_by_words(text=page.get_text("text") or "", chunk_words=term_extraction_chunk_words,
                                                       overlap_words=term_extraction_overlap_words):
                    await self._qua_tran_che("một đoạn (byte)", len(chunk.encode()), CHE_DOAN_BYTE, api_key)
                    _doan.append((page_index, chunk))
            _da = getattr(self, "_pdf_doan_da_dung", 0)
            await self._qua_tran_che("số đoạn PDF của khối (cộng mọi file)", _da + len(_doan), CHE_DOAN_TOI_DA, api_key)
            self._pdf_doan_da_dung = _da + len(_doan)   # [bản 2, E góp] _qua_tran_che chỉ nhường lượt (await thật) khi rào BỊ CHẠM — khi đó hoặc ném (luna) hoặc bỏ rào (Claude) ⇒ phép đếm chung của các PDF song song vẫn không hở

            tasks: list[asyncio.Task[tuple[int, list[str]]]] = []
            for page_index, page in enumerate(doc, start=1):
                page_text = page.get_text("text") or ""
                chunks = self._chunk_text_by_words(
                    text=page_text,
                    chunk_words=term_extraction_chunk_words,
                    overlap_words=term_extraction_overlap_words,
                )
                if progress_callback:
                    await progress_callback(
                        {
                            "event": "term_chunking_done",
                            "page_index": page_index,
                            "total_pages": total_pages,
                            "chunks": len(chunks),
                            "chunk_words": term_extraction_chunk_words,
                            "overlap_words": term_extraction_overlap_words,
                        }
                    )
                for chunk in chunks:
                    tasks.append(
                        asyncio.create_task(_extract_for_page_chunk(page_index, chunk))
                    )

            total_chunks = len(tasks)
            completed_total = 0
            milestones: dict[int, int] = {}
            if total_chunks:
                milestones[math.ceil(total_chunks * 0.25)] = 25
                milestones[math.ceil(total_chunks * 0.50)] = 50
                milestones[math.ceil(total_chunks * 0.75)] = 75
                milestones[total_chunks] = 100
            emitted_milestones: set[int] = set()

            for task in asyncio.as_completed(tasks):
                page_index_for_progress: int | None = None
                try:
                    page_index_for_progress, terms_for_chunk = await task
                    page_terms[page_index_for_progress].extend(terms_for_chunk)
                except Exception as exc:
                    logger.error(
                        "Failed extracting redaction terms from chunk: %s",
                        str(exc),
                        exc_info=True,
                    )
                    # [va_che_pdf] gốc nuốt lỗi rồi đi tiếp ⇒ đoạn lỗi KHÔNG được che mà PDF vẫn trả ra (lộ). Ở đây: huỷ các đoạn còn lại, báo hỏng cả khối.
                    for t in tasks:
                        t.cancel()
                    raise ValueError("Khối che PDF từ chối: tách từ cần che lỗi ở một đoạn — không trả PDF che thiếu. Chạy lại.") from exc
                finally:
                    completed_total += 1
                    if (
                        progress_callback
                        and total_chunks
                        and (pct := milestones.get(completed_total)) is not None
                        and pct not in emitted_milestones
                    ):
                        emitted_milestones.add(pct)
                        await progress_callback(
                            {
                                "event": "term_extraction_milestone",
                                "total_pages": total_pages,
                                "chunks_done": completed_total,
                                "chunks_total": total_chunks,
                                "pct": pct,
                            }
                        )

        # Phase 2: mutate the PDF serially (apply redactions to pages).
        _sot_trang: dict[int, int] = {}   # [va_che_luoi_do] {trang: số chỗ còn sót sau khi che}
        for page_index, page in enumerate(doc, start=1):
            terms = page_terms.get(page_index, [])

            seen: set[str] = set()
            terms = [
                t for t in terms if len(t) <= 200 and not (t in seen or seen.add(t))
            ]
            if not terms:
                continue

            # Use word-level bounding boxes (tight to glyphs) instead of search_for()
            # rectangles, which can be taller than the visible text.
            words = page.get_text(
                "words"
            )  # (x0, y0, x1, y1, word, block, line, word_no)
            word_texts = [w[4] for w in words]   # [va_che_co_dau] chuẩn hoá + so cụm: chi_so_tu_can_che() (Unicode) — trước: mẫu chỉ nhận chữ không dấu

            # Build per-line vertical bounds from word boxes, then derive non-overlapping
            # clamp ranges using neighboring lines. This prevents redaction rects from
            # bleeding into the line above/below even when font metrics overlap.
            line_bounds: dict[tuple[int, int], dict[str, float]] = {}
            for x0, y0, x1, y1, _t, block_no, line_no, _word_no in words:
                x0f = float(x0)
                x1f = float(x1)
                top = min(float(y0), float(y1))
                bottom = max(float(y0), float(y1))
                key = (int(block_no), int(line_no))
                if key in line_bounds:
                    b = line_bounds[key]
                    b["top"] = min(b["top"], top)
                    b["bottom"] = max(b["bottom"], bottom)
                    b["x0"] = min(b["x0"], x0f)
                    b["x1"] = max(b["x1"], x1f)
                else:
                    line_bounds[key] = {
                        "top": top,
                        "bottom": bottom,
                        "x0": x0f,
                        "x1": x1f,
                    }

            clamp_by_line: dict[tuple[int, int], tuple[float, float]] = {}

            # Neighbor selection: choose above/below lines within the same block that overlap
            # horizontally, to avoid cross-column lines collapsing the clamp range.
            by_block: dict[int, list[tuple[tuple[int, int], dict[str, float]]]] = {}
            for key, bounds in line_bounds.items():
                by_block.setdefault(key[0], []).append((key, bounds))

            def _x_overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
                return min(a1, b1) > max(a0, b0)

            for block_no, items in by_block.items():
                items_sorted = sorted(items, key=lambda item: item[1]["top"])
                for key, bounds in items_sorted:
                    y_top = bounds["top"]
                    y_bottom = bounds["bottom"]
                    x0 = bounds["x0"]
                    x1 = bounds["x1"]

                    above: tuple[tuple[int, int], dict[str, float]] | None = None
                    below: tuple[tuple[int, int], dict[str, float]] | None = None

                    for other_key, other_bounds in items_sorted:
                        if other_key == key:
                            continue
                        if not _x_overlaps(
                            x0, x1, other_bounds["x0"], other_bounds["x1"]
                        ):
                            continue
                        if other_bounds["top"] < y_top:
                            if above is None or other_bounds["top"] > above[1]["top"]:
                                above = (other_key, other_bounds)
                        elif other_bounds["top"] > y_top:
                            if below is None or other_bounds["top"] < below[1]["top"]:
                                below = (other_key, other_bounds)

                    prev_bottom = above[1]["bottom"] if above else y_top
                    next_top = below[1]["top"] if below else y_bottom
                    upper = (prev_bottom + y_top) / 2.0 if above else y_top
                    lower = (y_bottom + next_top) / 2.0 if below else y_bottom

                    # Fallback: if the clamp inverts/collapses, just use the line's own bounds.
                    if lower <= upper:
                        clamp_by_line[key] = (y_top, y_bottom)
                    else:
                        clamp_by_line[key] = (upper, lower)

            for j in chi_so_tu_can_che(word_texts, terms):   # [va_che_co_dau] mỗi từ phải che đúng MỘT ô đen (gốc: lặp theo từng cụm)
                x0, y0, x1, y1, _t, block_no, line_no, _word_no = words[j]
                top = min(float(y0), float(y1))
                bottom = max(float(y0), float(y1))
                clamp = clamp_by_line.get((int(block_no), int(line_no)))
                if clamp:
                    upper, lower = clamp
                    top = max(top, upper)
                    bottom = min(bottom, lower)
                if bottom <= top:
                    continue
                rect = fitz.Rect(
                    float(x0),
                    float(top),
                    float(x1),
                    float(bottom),
                )
                page.add_redact_annot(rect, fill=(0, 0, 0))

            page.apply_redactions()
            # [va_che_luoi_do] lưới đỡ: đọc lại lớp chữ CÒN LẠI của trang vừa che, gom theo dòng (khối, dòng của PyMuPDF), soát với đúng các cụm của trang này
            _dong: dict[tuple[int, int], list[str]] = {}
            for _x in page.get_text("words"):
                _dong.setdefault((int(_x[5]), int(_x[6])), []).append(_x[4])
            _n = dem_sot_sau_che(list(_dong.values()), terms)
            if _n:
                _sot_trang[page_index] = _n

        if _sot_trang:   # còn sót ⇒ KHÔNG trả file. Lời báo + log chỉ có số chỗ và số trang, không có chuỗi còn sót.
            _tong, _ds = sum(_sot_trang.values()), ", ".join(str(t) for t in sorted(_sot_trang)[:20])
            logger.warning("PDF redaction: lưới đỡ sau che — còn %s chỗ khớp cụm cần che ở trang %s", _tong, _ds)
            raise ValueError(f"Khối che PDF từ chối: sau khi che vẫn còn {_tong} chỗ khớp cụm cần che ở trang {_ds} (tên hoặc số viết khác cách trong file — ví dụ có "
                             "gạch nối, dấu chấm giữa các phần) — không trả PDF che thiếu. Sửa cách viết trong file rồi chạy lại.")

        output_bytes = doc.tobytes(garbage=4, deflate=True)
        logger.info(
            "PDF redaction finished: pages=%s bytes=%s",
            doc.page_count,
            len(output_bytes),
        )
        return output_bytes
