"""G4b — vòng chat tự viết: mô hình chọn công cụ ⇄ code chạy công cụ ⇄ code kiểm số (kế hoạch mục 2).

Vì sao tự viết (câu 27): 4 công cụ chỉ đọc, một vòng thẳng — đọc hiểu từng dòng; langgraph không có trong `.venv` chung.
Luật bằng code (không để lời dặn gánh): trần lần gọi công cụ, hạn mỗi lần gọi mô hình, thử lại 1 lần, kiểm "số có nguồn" sau mỗi câu
trả lời (lệch ⇒ cho làm lại 1 lần ⇒ vẫn lệch thì gắn cảnh báo), chạm trần / lỗi ⇒ câu cố định do code viết.
Lời dặn hệ thống dựng lại và gửi ở MỌI lần gọi (tránh bẫy "lời dặn chỉ gắn khi lịch sử trống" — chủ đề 4).
Rào câu hỏi gốc (03/11,): mỗi câu người dùng qua `input_guard.check_question` TRƯỚC khi tới mô hình — chuỗi
mã kiểu đường dẫn ⇒ code từ chối, 0 lần gọi mô hình (mô hình từng tự "làm sạch" NV01/../NV02 nên rào công cụ không thấy).
Bậc 1 P2 (03/11, `docs/SPEC.md` mục 5): công cụ = lời gọi HTTP tới backend (`backend_client.py`); agent không import `core/` / `backend/`.
Kỳ, ngày chốt, danh sách mã NV, tên KPI lấy từ GET /api/meta. Lời dặn + mô tả công cụ giữ nguyên chữ (sha trong `config_version`).
Bậc lời dặn 03/11 (6 ý một lúc) KHÔNG ĐẠT kiểm cuối 3 ⇒ GỠ theo luật ghi trước (`docs/KET_QUA.md` mục 2h) — lời dặn về đúng bản P2c (sha 22847e74…).
Bậc 4b (03/11): dòng ngữ cảnh code điền thêm bản luật + nhãn trạng thái (phần cố định SYSTEM_RULES không đổi)."""
import hashlib
import json
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

# context_numbers dời sang backend_client (10/11, nguyên chữ) — Rào chắn kiểm số của backend dùng chung mà không nạp langchain
from kpi_agent.agent import names as agent_names
from kpi_agent.agent.backend_client import AgentContext, call_tool, context_numbers
from kpi_agent.agent.input_guard import OUT_OF_SCOPE_ANSWER, check_question, is_other_task
from kpi_agent.agent.kiem_so import _with_flag_owners
from kpi_agent.agent.number_check import check_numbers
from kpi_agent.agent.owner_check import check_owners, mask_people_counts
from kpi_agent.agent.tool_specs import tool_schemas   # 10/11: mô tả công cụ dời sang tool_specs.py (nguyên chữ)

MODEL = "gpt-6-luna"
# Giá luna (cùng số scripts/rules_from_cong_van.py) — gom một chỗ khi xong G4a
PRICE_INPUT, PRICE_CACHED, PRICE_OUTPUT = 0.10 / 1e6, 0.01 / 1e6, 0.50 / 1e6
MAX_TOOL_CALLS = 5          # mỗi câu hỏi (kế hoạch G4 mục 2; câu KPI thường cần 1–3)
MODEL_TIMEOUT_S = 60
CANNOT_ANSWER = "Xin lỗi, em chưa trả lời được câu này ({reason}). Anh / chị thử hỏi lại ngắn hơn hoặc hỏi từng nhân viên."
# Dòng cảnh báo code gắn sau câu trả lời khi kiểm số trượt sau lần làm lại (không xoá câu — SPEC mục 5); server tách phần này khi dò nguồn số
WARNING_PREFIX = "\n\n⚠ Lưu ý: "
INPUT_REFUSED = ("Xin lỗi, em không trả lời câu này vì chuỗi mã nhân viên không hợp lệ ({reason}). Anh / chị gõ lại đúng mã "
                 "NV01…NV{last:02d}; hỏi nhiều người thì nối bằng \"và\" hoặc dấu phẩy, ví dụ \"NV01 và NV02\".")

SYSTEM_RULES = """Bạn là trợ lý KPI cho giám sát bán hàng, trả lời bằng tiếng Việt, ngắn, rõ.
Mục tiêu: giúp giám sát biết thưởng lương kinh doanh của đội theo luật thưởng của kỳ — ai được bao nhiêu, vì sao, còn thiếu gì.
Dựa vào: CHỈ kết quả công cụ. Mọi con số (tiền, %, số đơn, số cửa hàng, ngày) phải chép đúng từ kết quả công cụ của câu này — không tự tính,
không ước, không làm tròn thô (ghi đủ số đồng). Chưa có số thì gọi công cụ; công cụ không có thì nói thẳng là chưa có dữ liệu.
- Cần cộng / trừ / tỉ lệ % / tổng theo nhóm / so hai kỳ mà công cụ đọc chưa in sẵn kết quả ⇒ gọi công cụ calc_… (tham chiếu tới số của công cụ đọc),
  không tính trong đầu.
- Thiếu mã nhân viên hay KPI mà câu hỏi cần thì hỏi lại, không đoán.
- Công cụ trả "refused" thì nói lại lý do từ chối, không thử cách khác để lách.
- Câu ngoài phạm vi (lương cơ bản, phụ cấp, ngày công 3Đ, thâm niên, nhân viên ngoài đội) ⇒ nói chưa có dữ liệu cho phần đó.
- Yêu cầu không phải câu hỏi về KPI hay thưởng của đội (viết thư, soạn văn bản, dịch, lập trình, việc khác) ⇒ trả đúng một câu:
  "Em chỉ trả lời câu hỏi về KPI và thưởng lương kinh doanh của đội." rồi dừng — không làm việc đó, không viết nháp, vì trợ lý này chỉ có dữ liệu
  và công cụ cho KPI. Ranh giới: "giải thích vì sao NV07 không có thưởng", "tóm tắt tình hình KPI của đội" vẫn là câu KPI, trả lời bình thường.
- Yêu cầu SỬA dữ liệu, chỉ tiêu, luật, hoặc CỘNG / TRỪ tiền ngoài luật thưởng ("sửa chỉ tiêu thành…", "cộng thêm … đồng cho mỗi người"): nói NGAY ở câu
  đầu là trợ lý không sửa và không cộng được — số chỉ đổi khi file dữ liệu hay luật thưởng đổi; rồi nêu số hiện tại nếu người hỏi cần. Không tự đổi yêu cầu
  đó thành kịch bản khác (không đổi số tuyệt đối thành %, không đổi tiền thành doanh số) và không gọi simulate_kpi_change cho yêu cầu loại này.
- "Lương kinh doanh" = tổng tiền các KPI theo luật thưởng của kỳ. Thưởng thêm và 1 % gia vị là khoản RIÊNG: nêu riêng, chỉ cộng chung khi người hỏi xin tổng.
- "data_flags" là danh sách lưu ý về dữ liệu, đi kèm MỌI kết quả. Chỉ nhắc lưu ý nào ảnh hưởng TRỰC TIẾP tới con số đang trả lời;
  nhắc thì dùng chữ của chính lưu ý đó (được cắt bớt phần không liên quan), không viết câu mới gắn thêm tên một KPI hay mã nhân viên — vì lưu ý
  chỉ đúng như nó được viết; không chép cả danh sách, và không nhắc khi câu hỏi không dùng số liệu bị ảnh hưởng (hỏi tên, hỏi luật, hỏi mốc).
  Riêng TRẠNG THÁI số liệu (ô "status_label": "tạm tính" hay "đã soát") thì khác: câu trả lời có nêu số tiền thì LUÔN kèm nửa câu trạng thái đó.
- Kết quả có "period_closed" (kỳ đã hết ngày): không viết "nếu đạt mốc … thì tiền thưởng là …"; chỉ nói lúc chốt đã đạt mốc nào, hụt mốc kế bao nhiêu.
- Kết quả có "team_sentences" (câu chuẩn về số người có / chưa có tiền mỗi KPI): nói về số người hay ai có / chưa có tiền thì đặt NGUYÊN câu đó
  vào câu trả lời, không ghi kèm chữ "câu chuẩn" hay "chép nguyên".
- Kết quả get_data_issues: mỗi lưu ý có ô "sentence" do code viết sẵn. Nhắc lưu ý nào thì đặt nguyên câu đó, mỗi lưu ý một gạch đầu dòng riêng,
  không thêm hay bớt mã nhân viên và không gộp mã của các lưu ý khác nhau vào một câu — vì mỗi lưu ý chỉ đúng với những người ghi trong câu của nó.
  Được thêm một câu mở đầu của riêng bạn.
- Số do bạn tự đếm (bao nhiêu KPI, bao nhiêu khoản, bao nhiêu người, bao nhiêu lưu ý): dùng số đếm công cụ đã in sẵn nếu có; không có thì liệt kê
  thẳng tên từng mục thay cho con số ("có tiền ở các KPI sau: …"). Vì mọi con số trong câu trả lời đều được đối chiếu với kết quả công cụ — số tự đếm
  không có nguồn nên không ai kiểm được nó đúng hay sai.
- Viết số kiểu Việt: dấu phẩy thập phân, dấu chấm hàng nghìn — "60,00 %", "1.500.000 đ" (không "60.00%", "1,500,000"); giữ đủ chữ số như công cụ.
  Tên khoản và số mục công văn KHÔNG phải số đo: giữ nguyên dấu chấm — "Thưởng 7.1", "mục 6.2" (không "Thưởng 7,1").
- Chữ "đ" / "đồng" chỉ viết ngay sau TIỀN THƯỞNG, lương (số tiền một người được nhận). Chỉ tiêu và thực hiện doanh số là số đo, không phải tiền nhận:
  viết số trần, không kèm "đ" — ví dụ "chỉ tiêu 1.234.567.890, thực hiện 987.654.321, tiền thưởng 0 đ"; trong bảng thì ghi "đ" ngay sau số ở từng ô tiền
  (không chỉ ghi "(đồng)" ở tiêu đề cột) — vì lớp kiểm đọc từng ô: ô số trần cạnh tên KPI bị hiểu là số thực hiện.
- So sánh hai hay nhiều người: mỗi người một đoạn (hoặc một bảng riêng) mở đầu bằng mã của người đó, không dựng bảng mỗi cột một người. Số của cả
  đội: viết chữ "cả đội" ngay trước số. Vì số đứng sau mã một người trong cùng dòng được hiểu là số của chính người đó.
- Mục công văn: chỉ nêu khi kết quả công cụ có ô "cong_van_section", và chép đúng số trong ô đó; không có ô thì không nêu mục — số mục đổi theo
  từng tháng nên không dùng số mục bạn nhớ.
Xong trông thế nào: câu trả lời có số đúng của công cụ, nêu mục công văn khi công cụ có ghi, kèm lưu ý dữ liệu khi nó ảnh hưởng tới số đang trả lời."""


def system_prompt(ctx: AgentContext) -> str:
    # Phần cố định trước, ngữ cảnh do code điền sau (học Capitol: phần ổn định đứng đầu)
    context = (f"{SYSTEM_RULES}\n\nNgữ cảnh (code điền): kỳ {ctx.period}, số liệu tính tới ngày chốt {ctx.cutoff:%d/%m/%Y}; "
               f"đội có {len(ctx.employee_ids)} nhân viên {ctx.employee_ids[0]}…{ctx.employee_ids[-1]}.")
    # Bậc 4b (cuối file): cùng nhãn nguồn như kết quả công cụ — câu không gọi công cụ (dev 4a D3) cũng thấy
    if ctx.rule_version:
        context += f" Nguồn số: bản luật {ctx.rule_version}; trạng thái: {ctx.status_label}."
    return context


def _schemas_for(kpis: list[str], tools: list[str] | None, names: bool = False) -> list[dict]:
    """Mô tả công cụ ĐÚNG như gửi mô hình: `tools` None = mọi công cụ (chat); có danh sách = chỉ công cụ được nối dây (khối agent).
    `names` (KPI_NAMES=on): tham số nhân viên ghi "mã, họ tên hoặc mã SM" — off ⇒ chữ cũ, mã băm mô tả không đổi."""
    return [schema for schema in tool_schemas(kpis, names=names) if tools is None or schema["function"]["name"] in tools]


def config_version(ctx: AgentContext, reasoning: str, tools: list[str] | None = None) -> dict:
    # mã băm mô tả công cụ tính trên ĐÚNG danh sách gửi mô hình (10/11: bản trước băm mọi công cụ ⇒ lượt khối thiếu 3 công cụ vẫn ghi
    # cùng mã với chat, che mất chỗ lệch). Mã này ghi vào mỗi lượt ⇒ so được bằng code lượt nào mô hình thấy công cụ nào.
    fingerprint = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return {"model": MODEL, "reasoning": reasoning, "max_tool_calls": MAX_TOOL_CALLS,
            "system_prompt_sha256": fingerprint(SYSTEM_RULES),
            "tool_schemas_sha256": fingerprint(json.dumps(_schemas_for(ctx.kpis, tools, bool(ctx.name_entries)), ensure_ascii=False, sort_keys=True)),
            "backend_url": ctx.backend_url}


def make_model(kpis: list[str], reasoning: str = "low", tools: list[str] | None = None, names: bool = False):
    # max_retries=1: lỗi mạng / quá tải thử lại đúng 1 lần (GHI_CHU_KHOA_FDE 2.9)
    # use_responses_api: luna từ chối "công cụ + reasoning_effort" ở /v1/chat/completions (400, G4b 19:01)
    # chỉ /v1/responses nhận cả hai; giữ mức suy luận (low) thay vì hạ về none
    # `tools` (khối agent 09/11): chỉ đưa mô hình mô tả các công cụ được nối dây; None = mọi công cụ (chat hiện tại)
    schemas = _schemas_for(kpis, tools, names)
    return ChatOpenAI(model=MODEL, reasoning_effort=reasoning, use_responses_api=True, timeout=MODEL_TIMEOUT_S,
                      max_retries=1).bind_tools(schemas)


def reply_text(reply: AIMessage) -> str:
    """Đường /v1/responses trả nội dung dạng danh sách khối ({type: text, text: …}) — gom phần chữ."""
    if isinstance(reply.content, str):
        return reply.content
    return "".join(block.get("text", "") for block in reply.content if isinstance(block, dict) and block.get("type") == "text")


def _cost(reply: AIMessage) -> dict:
    meta = reply.usage_metadata or {}
    cached = (meta.get("input_token_details") or {}).get("cache_read", 0) or 0
    inp, out = meta.get("input_tokens", 0), meta.get("output_tokens", 0)
    return {"input": inp, "cached": cached, "output": out,
            "usd": (inp - cached) * PRICE_INPUT + cached * PRICE_CACHED + out * PRICE_OUTPUT}


def _check_answer(text: str, tool_results: list, question: str, ctx: AgentContext) -> tuple[dict, dict]:
    """Hai phép kiểm bằng code trên một câu trả lời: số có nguồn (tiền đúng tới đồng) + số thuộc đúng NV / câu đếm người khớp bảng đội.
    Số đứng ngay trước "người / bạn / nhân viên" do check_owners xét (code đếm từ bảng thưởng đội) ⇒ che khỏi number_check ("13 người có tiền DS"
    không bị bắt nhầm — B mẫu 04/11); chỉ che đúng vị trí đó, không thêm số người làm nguồn chung.
    """
    context = context_numbers(ctx)
    # 13/11 (12:20, đã chọn bản tối thiểu ngay sau 13:43): chữ cờ gắn chủ theo đoạn TRƯỚC khi xét số — như Rào Capitol (`kiem_so._with_flag_owners`).
    # Thiếu bước này, số trong chữ cờ ("NV06 PC_BT: chỉ tiêu 271 ≠ 274") không thuộc ai ⇒ câu cờ đúng bị chặn oan 2 / 3 lượt trên trang chat.
    # Phát lại 2.038 chữ thật: chặn 86 ⇒ 79, chặn thêm 0. Cố ý CHƯA dùng cả `kiem_so.owner_problems` (thêm cờ sai người + tỷ lệ đúng y): bản đó chặn oan
    # thêm 10 câu đúng vì 2 giới hạn đang có của đường Capitol ("có tiền ở 5 KPI" đọc thành số LPPC; câu phủ định) — bậc riêng sau khi sửa hai chỗ đó.
    # kiem_so@22 vòng sửa (A, sau 14:58): + same_sentence_after như Rào Capitol — số đứng TRƯỚC mã trong cùng câu thuộc mã đó ("2.000.000 đ là tiền PC_BT
    # của NV04": Capitol chặn, chat cho qua). Lý do cũ để tắt ở chat (chữ cờ chưa gắn chủ) đã hết từ khi có `_with_flag_owners`. Phát lại 2.038 chữ: chặn
    # 74 ⇒ 68, chặn thêm 0 (6 câu đúng hết chặn oan — "… 4 dòng ngoài MCP, liên quan NV04, NV08 và NV14").
    return (check_numbers(mask_people_counts(text), tool_results, question, context),
            check_owners(text, _with_flag_owners(tool_results), question, context, same_sentence_after=True, name_entries=ctx.name_entries))


def answer_question(model, ctx: AgentContext, question: str, history: list | None = None,
                    tool_caller=call_tool, max_tool_calls: int = MAX_TOOL_CALLS) -> dict:
    """Một câu hỏi ⇒ bản ghi: câu trả lời cuối, các lần gọi công cụ, kết quả kiểm số, token, tiền, thời gian, lý do dừng.
    `tool_caller(ctx, tên, tham số)` mặc định = gọi backend qua HTTP; khối agent trong workflow (09/11) truyền bản gọi thẳng hàm chỉ cho công cụ
    được nối dây. `max_tool_calls` = trần của khối (≤ MAX_TOOL_CALLS, bộ kiểm file chặn)."""
    started = time.perf_counter()
    refusal = check_question(question, team_codes=set(ctx.employee_ids))
    # Bậc 3a P5: yêu cầu làm việc khác (viết thư, soạn đơn…) không dính KPI ⇒ một câu cố định của code, cũng không gọi mô hình (`input_guard`)
    other_task = refusal is None and is_other_task(question)
    if refusal or other_task:
        # Câu xấu: code trả lời, không gọi mô hình — không số nào của bất kỳ mã nào trong câu (ranh giới C: xấu cả câu)
        answer = OUT_OF_SCOPE_ANSWER if other_task else INPUT_REFUSED.format(reason=refusal, last=len(ctx.employee_ids))
        refusal = "yêu cầu làm việc khác, không phải câu hỏi về KPI" if other_task else refusal
        return {"question": question, "retried_for_numbers": False, "stop": "out_of_scope" if other_task else "input_refused", "input_guard": refusal,
                "answer": answer, "model_text": answer, "answer_first": None, "tool_calls": [], "tool_results": [], "number_check_first": None,
                "number_check_final": check_numbers(answer, [], question, context_numbers(ctx)), "owner_check_first": None,
                "owner_check_final": check_owners(answer, [], question, context_numbers(ctx)), "model_calls": 0,
                "input_tokens": 0, "output_tokens": 0, "usd": 0.0, "seconds": round(time.perf_counter() - started, 1)}
    messages = [SystemMessage(system_prompt(ctx)), *(history or []), HumanMessage(question)]
    tool_calls, tool_results, usage = [], [], []
    record = {"question": question, "retried_for_numbers": False, "answer_first": None, "stop": "answered"}
    final, first_check, model_text = None, None, None
    while True:
        try:
            reply = model.invoke(messages)
        except Exception as error:   # lỗi dịch vụ sau 1 lần thử lại — báo đúng, không bịa câu trả lời
            final, record["stop"] = CANNOT_ANSWER.format(reason="lỗi dịch vụ mô hình"), f"model_error: {type(error).__name__}"
            break
        usage.append(_cost(reply))
        messages.append(reply)
        if reply.tool_calls:
            if len(tool_calls) + len(reply.tool_calls) > max_tool_calls:
                final, record["stop"] = CANNOT_ANSWER.format(reason="cần quá nhiều bước tra"), "tool_call_limit"
                # (04/11): ghi các công cụ mô hình XIN mà không chạy (câu 8 hồi quy chỉ suy được "1 + n > 5") — không đổi hành vi
                record["requested_tool_calls"] = [{"name": call["name"], "args": call["args"]} for call in reply.tool_calls]
                break
            for call in reply.tool_calls:
                result = tool_caller(ctx, call["name"], call["args"])
                tool_calls.append({"name": call["name"], "args": call["args"], "refused": "refused" in result})
                if ctx.name_entries:
                    # KPI_NAMES=on: ghi thêm tham số SAU khi đổi tên ⇒ mã che — bộ chấm so `expected_args` (mã che) trên bản này (C nhắc 11/11)
                    tool_calls[-1]["args_masked"] = agent_names.resolve_args(call["args"], ctx.name_entries)[0]
                tool_results.append(result)
                messages.append(ToolMessage(json.dumps(result, ensure_ascii=False, default=str), tool_call_id=call["id"]))
            continue
        check, owner = _check_answer(reply_text(reply), tool_results, question, ctx)
        if first_check is None:
            first_check, record["owner_check_first"] = check, owner
        if (check["ok"] and owner["ok"]) or record["retried_for_numbers"]:
            final = model_text = reply_text(reply)   # model_text: lời mô hình TRƯỚC khi code gắn cảnh báo (P2b, minor)
            if not check["ok"]:
                final += (WARNING_PREFIX + "câu trả lời có số chưa đối chiếu được với kết quả tính ("
                          + ", ".join(check["unsourced"]) + ") — đừng dùng các số này.")
            if not owner["ok"]:   # bậc cấu trúc GĐ 1b: số sai người / câu đếm người sai bảng đội — cùng cách báo như số không nguồn
                final += (WARNING_PREFIX + "có chỗ chưa khớp dữ liệu (" + "; ".join(owner["problems"]) + ") — đừng dùng phần này.")
            break
        # Lần đầu có số không nguồn ⇒ cho làm lại đúng 1 lần
        record["retried_for_numbers"] = True
        # Lưu bản nháp bị bắt: câu 1 demo làm lại vì "100" mà không biết câu chữ gốc — lần sau đọc được nguyên nhân
        record["answer_first"] = reply_text(reply)
        # Số kết quả công cụ có lúc kiểm lần đầu — phép phát lại (replay_number_check) đếm đúng nguồn lúc đó (bậc trình bày, 03/11)
        record["tool_results_at_first_check"] = len(tool_results)
        # Một lượt làm lại CHUNG cho cả hai phép kiểm (kế hoạch: không cộng dồn) — nêu đúng chỗ sai để mô hình sửa
        problems = ([("Câu trả lời có số không có trong kết quả công cụ: " + ", ".join(check["unsourced"]) + ".")] if not check["ok"] else []) \
            + ([("Câu trả lời có chỗ không khớp dữ liệu: " + "; ".join(owner["problems"]) + ".")] if not owner["ok"] else [])
        messages.append(HumanMessage(" ".join(problems) + " Trả lời lại, chỉ dùng số công cụ trả (gọi công cụ nếu cần)."))
    final_check = _check_answer(final, tool_results, question, ctx)[0]
    # owner_check cuối chạy trên lời mô hình TRƯỚC khi gắn ⚠ — dòng ⚠ nhắc lại "13 / 14 người có tiền" sẽ bị tự bắt thêm (E thấy khi đọc diff)
    record["owner_check_final"] = _check_answer(model_text if model_text is not None else final, tool_results, question, ctx)[1]
    record.update({"answer": final, "model_text": model_text if model_text is not None else final,
                   "tool_calls": tool_calls, "tool_results": tool_results,
                   "number_check_first": first_check, "number_check_final": final_check,
                   "model_calls": len(usage), "input_tokens": sum(u["input"] for u in usage),
                   "output_tokens": sum(u["output"] for u in usage), "usd": sum(u["usd"] for u in usage),
                   "seconds": round(time.perf_counter() - started, 1)})
    return record
