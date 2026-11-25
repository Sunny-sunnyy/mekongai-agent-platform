"""Rào câu hỏi gốc (kế hoạch — ranh giới =, 03/11).

Ca viết tay theo đúng ví dụ dạng chuỗi của file C — không phải câu của bộ kiểm cuối (E không mở bộ đó)."""
from datetime import date

import pytest

from kpi_agent.agent.chat import answer_question
from kpi_agent.agent.input_guard import check_question
from kpi_agent.agent.backend_client import AgentContext

TEAM_SIZE = 14
TEAM = {f"NV{i:02d}" for i in range(1, TEAM_SIZE + 1)}

BAD = [
    "Xem kết quả NV01/../NV02",          # (1) `..` cạnh `/`
    "../NV01 được bao nhiêu",
    "NV01/.. thế nào",
    "NV01/../../NV02",
    "..\\NV01",
    "NV01\\NV02 ai hơn",                 # (2) `\` dính mã
    "NV01\\..\\NV02",
    "NV01%2FNV02",                       # (3) mã hoá URL dính mã / giữa hai mã
    "nv01%2e%2e%2fnv02",
    "NV01 %5C NV02",
    "%2fNV03 thưởng bao nhiêu",
    "NV00 được thưởng không",            # (4) mã ngoài đội
    "NV15 được bao nhiêu",
    "So NV99 với NV01",
    "NV12 và NV13/../NV06",              # nửa xấu nửa hợp lệ ⇒ xấu cả câu
    "NV01／..／NV02",                     # gạch chéo toàn khổ (NFKC ⇒ `/`)
]

VALID = [
    "NV12/NV13 ai cao hơn",              # (a) hai mã đúng nối bằng một `/`
    "NV12 / NV13",
    "nv07 được bao nhiêu",
    "NV06... sao vậy",                   # (b) chấm lửng sau mã
    "NV06..",
    "NV06… sao vậy",                     # dấu chấm lửng một ký tự
    "Tiến độ tới ngày 21/10",            # (c) `/` không dính mã
    "Kỳ 10/2026 cả đội",
    "NV04 ASO_Focus/ASO_Focus_2 thế nào",
    "NV12 và NV13",                      # (d) nối bằng chữ / dấu phẩy
    "NV12, NV13",
    "LPPC 4.7 của NV05 là đạt chưa",
    "NV14 đạt 95% chưa",
    "Bảng thưởng cả đội",
]


@pytest.mark.parametrize("question", BAD)
def test_bad_question_is_refused(question):
    assert check_question(question, TEAM) is not None


@pytest.mark.parametrize("question", VALID)
def test_valid_question_passes(question):
    assert check_question(question, TEAM) is None


def test_reason_has_no_raw_input():
    # Lời từ chối không chép lại chuỗi người gõ (tránh đưa chuỗi lạ ngược vào câu trả lời / bản ghi dài)
    reason = check_question("NV01/../NV02" + "x" * 500, TEAM)
    assert reason is not None and "xxx" not in reason and len(reason) < 120


class CountingModel:
    """Chỉ đếm lần gọi mô hình — kiểm đường code "bị chặn thì không gọi mô hình", không phải bằng chứng chất lượng."""
    calls = 0

    def invoke(self, messages):
        self.calls += 1
        raise AssertionError("câu bị rào chặn không được tới mô hình")


def small_context() -> AgentContext:
    # Ngữ cảnh tối thiểu viết tay: rào chỉ cần danh sách mã trong đội; URL không có ai nghe — câu bị chặn không được gọi tới đó
    return AgentContext(backend_url="http://127.0.0.1:9", period="2026-10", cutoff=date(2026, 10, 20),
                        employee_ids=sorted(TEAM), kpis=[], data_flags=[])


def test_refused_question_never_reaches_model():
    model = CountingModel()
    record = answer_question(model, small_context(), "Xem kết quả nhân viên NV01/../NV02")
    assert model.calls == 0
    assert record["stop"] == "input_refused" and record["tool_calls"] == [] and record["usd"] == 0
    assert record["number_check_final"]["ok"] is True
    assert "NV01 và NV02" in record["answer"]      # chỉ cách hỏi lại đúng


def test_team_codes_need_not_be_contiguous():
    # 09:39 (minor 3): so mã có trong đội, không giả định NV01…NVn liền nhau
    team = TEAM - {"NV05"}
    assert check_question("NV05 được bao nhiêu", team) is not None
    assert check_question("NV14 được bao nhiêu", team) is None


# ---------- Bậc 3a P5: yêu cầu làm việc khác (không phải câu hỏi KPI) ⇒ một câu cố định của code, không gọi mô hình ----------
OTHER_TASKS = [
    "Viết hộ tôi một email xin nghỉ phép 3 ngày gửi trưởng phòng, lý do việc gia đình.",   # ca Capitol 19/11: hai mô hình đều viết đủ bản nháp
    "Dịch giúp tôi bức thư này sang tiếng Anh",
    "làm cho tôi một bài thơ về mùa thu",
    "Soạn đơn xin thôi việc",
    "lập trình cho tôi một chương trình tính giai thừa",
]
STILL_KPI = [
    "Giải thích vì sao NV07 không có thưởng",                 # câu giải thích hợp lệ — không được từ chối oan
    "Tóm tắt tình hình KPI của đội",
    "Soạn email báo kết quả KPI tháng 10 cho đội",            # lưỡng lự (có chữ miền KPI) ⇒ để mô hình xử theo lời dặn
    "NV03 viết bao nhiêu đơn hàng kênh ON?",                  # "viết … đơn" nhưng là đơn hàng
    "Kể cho tôi cửa hàng nào chưa mua Trà 365",
    "Viết lại câu trả lời ngắn hơn",
    "Lương kinh doanh tháng 10 của Hoàng Hậu là bao nhiêu?",
    "Ai làm tốt nhất tháng này?",
]


@pytest.mark.parametrize("question", OTHER_TASKS)
def test_other_task_is_out_of_scope(question):
    from kpi_agent.agent.input_guard import is_other_task
    assert is_other_task(question)


@pytest.mark.parametrize("question", STILL_KPI)
def test_kpi_question_is_not_out_of_scope(question):
    from kpi_agent.agent.input_guard import is_other_task
    assert not is_other_task(question)


def test_other_task_gets_fixed_sentence_and_never_reaches_model():
    from kpi_agent.agent.input_guard import OUT_OF_SCOPE_ANSWER
    model = CountingModel()
    record = answer_question(model, small_context(), OTHER_TASKS[0])
    assert model.calls == 0 and record["stop"] == "out_of_scope" and record["tool_calls"] == [] and record["usd"] == 0
    # thước của ca Capitol: KHÔNG có nội dung email trong câu trả lời — câu trả lời là đúng một câu cố định
    assert record["answer"] == OUT_OF_SCOPE_ANSWER and "Kính gửi" not in record["answer"]
    assert record["number_check_final"]["ok"] is True


# Câu GHÉP (một nửa KPI, một nửa việc khác) và câu "giống mà không phải": rào code KHÔNG chặn — có chữ miền KPI ⇒ để mô hình xử theo lời dặn
# (giới hạn cố ý: rào code chỉ chặn ca rõ ràng; chặn oan một câu KPI hại hơn để lọt một câu lưỡng lự tới mô hình)
MIXED_OR_LOOKALIKE = [
    "NV03 được bao nhiêu thưởng, rồi viết giúp tôi một email xin nghỉ phép",       # câu ghép
    "Viết email xin nghỉ phép, à mà chỉ tiêu PC_ON của NV05 là bao nhiêu?",         # câu ghép, việc khác đứng trước
    "Soạn email thông báo kết quả KPI tháng 10 cho NV07",                           # lưỡng lự — phạm vi sản phẩm chưa chốt (người vận hành)
    "Lập bảng thưởng của cả đội",                                                   # giống "giao việc" nhưng là câu KPI
    "Làm bảng so sánh NV01 và NV02",
    "Dịch chuyển chỉ tiêu sang tháng sau được không?",                              # "dịch" không phải dịch văn bản
    "Viết code nào đang áp cho Homey 2,9 kg?",                                      # "code" = mã hàng (người kiểm độc lập 19/11: bản @28 chặn oan)
]


@pytest.mark.parametrize("question", MIXED_OR_LOOKALIKE)
def test_mixed_or_lookalike_question_goes_to_the_model(question):
    from kpi_agent.agent.input_guard import is_other_task
    assert not is_other_task(question)


# Ba mẫu LỌT người kiểm độc lập tìm 19/11 trên bản P5 (đỏ trên bản đó) + vài câu cùng loại
LEAKED = [
    "Dịch đoạn này sang tiếng Anh: hôm nay trời đẹp",        # không có "loại văn bản" trong danh sách
    "Lập trình giúp một hàm Python tính giai thừa",
    "viet email xin nghi phep",                              # gõ không dấu — người gõ khung chat hay bỏ dấu
    "viet ho toi mot buc thu cam on",
    "Viết giúp đoạn code sắp xếp mảng",
]


@pytest.mark.parametrize("question", LEAKED)
def test_leaked_other_tasks_are_now_blocked(question):
    from kpi_agent.agent.input_guard import is_other_task
    assert is_other_task(question)


@pytest.mark.parametrize("question", OTHER_TASKS)
def test_other_task_typed_without_marks_is_blocked(question):
    from kpi_agent.agent.input_guard import _without_marks, is_other_task
    assert is_other_task(_without_marks(question))


# Bỏ dấu làm chữ trùng nhau ⇒ dễ chặn oan: mọi câu KPI / câu lưỡng lự ở trên gõ không dấu vẫn phải tới mô hình, cùng vài câu "trùng chữ sau khi bỏ dấu"
PLAIN_LOOKALIKE = [
    "lam sao de tang thu nhap thang nay",                    # "thu nhập", không phải "thư"
    "ke ten nhung nguoi tho gioi nhat",                      # "thợ", không phải "thơ"
    "dich vu nao ban chay nhat",                             # "dịch vụ"
    "viet nam co bao nhieu tinh",                            # "Việt Nam" — câu ngoài KPI nhưng không phải giao việc ⇒ để mô hình từ chối
]


@pytest.mark.parametrize("question", STILL_KPI + MIXED_OR_LOOKALIKE)
def test_kpi_question_typed_without_marks_goes_to_the_model(question):
    from kpi_agent.agent.input_guard import _without_marks, is_other_task
    assert not is_other_task(_without_marks(question))


@pytest.mark.parametrize("question", PLAIN_LOOKALIKE)
def test_words_that_collide_after_removing_marks_are_not_blocked(question):
    from kpi_agent.agent.input_guard import is_other_task
    assert not is_other_task(question)


def test_other_task_contract_for_outside_callers():
    # Hợp đồng cho bên gọi ngoài (khối trợ lý bên bộ Capitol dùng lại hàm này — một nguồn): chữ ký ổn định, trả bool, hằng câu trả lời cố định,
    # và nạp mô-đun KHÔNG kéo theo thư viện nặng (mô hình, backend) — chạy trong tiến trình riêng để không dính mô-đun test khác đã nạp
    import inspect
    import subprocess
    import sys

    from kpi_agent.agent import input_guard
    assert str(inspect.signature(input_guard.is_other_task)) == "(question: str) -> bool"
    assert input_guard.is_other_task("Soạn đơn xin thôi việc") is True and input_guard.is_other_task("NV01 được bao nhiêu?") is False
    assert input_guard.OUT_OF_SCOPE_ANSWER == "Em chỉ trả lời câu hỏi về KPI và thưởng lương kinh doanh của đội."
    probe = ("import sys; import kpi_agent.agent.input_guard; "
             "heavy = [name for name in sys.modules if name.split('.')[0] in ('langchain', 'langchain_core', 'langchain_openai', 'openai', 'fastapi', "
             "'openpyxl', 'yaml', 'httpx')]; print(heavy)")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=str(input_guard.__file__).split("/kpi_agent/")[0], timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "[]", (out.stdout, out.stderr[-300:])
