import asyncio
from abc import ABC, abstractmethod
from typing import AsyncGenerator
from uuid import UUID

from eval_srvc import system_settings
from eval_srvc.api.generator_cloning import GeneratorCloningFactory
from eval_srvc.context_vars import org_id_ctx
from eval_srvc.custom_types.app_response_schemas.eval_request_response import EvalResult
from eval_srvc.custom_types.content_storage import Document
from eval_srvc.custom_types.primitives import TEvalConfig
from eval_srvc.eval.eval_factory import EvalFactory
from eval_srvc.logging_utils import Logger
from eval_srvc.report_generation.generate_reports import generate_reports
from eval_srvc.web_scraping.bs4_scraper import BS4Scraper

logger = Logger.get_logger()

# [ghi chú] Bộ chạy thử chỉ cho bài chấm pass_fail / score trên MỘT
# đầu vào chữ có trần ⇒ giữ chỗ tiền theo mức tối đa được (muc_toi_da.py EVAL_*). Mỗi đầu vào × mỗi bài = đúng 1 aquery (pass_fail_eval.py:298,
# score_eval.py:255; gpt5_service max_retries=0). Mặc định BẬT; tắt bằng EVAL_CHI_PASS_FAIL=0. Vượt ⇒ từ chối CẢ lượt TRƯỚC khi gọi AI.
import json as _json  # noqa: E402
import os as _os  # noqa: E402

from eval_srvc.custom_types.eval_configs import PassFailEvalConfig, ScoreEvalConfig  # noqa: E402

EVAL_BAI_TOI_DA, EVAL_DAU_VAO_TOI_DA = 5, 1
# Dữ liệu chấm đi vào lời dặn dạng json.dumps({"content": …, **meta}, indent=4) với ensure_ascii MẶC ĐỊNH (pass_fail_eval.py:262;
# score_eval.py:220) ⇒ chữ Việt thành \uXXXX (gấp 2–3 lần byte UTF-8) ⇒ đo ĐÚNG chuỗi đó, không đo byte UTF-8 (đo thẳng — D minor 1).
EVAL_DU_LIEU_BYTE, EVAL_TIEU_CHI_BYTE = 60_000, 4_000
# [ghi chú] + "workflow_output_file" (SỐ ÍT — _attachment_helpers.py:136 cũng tải từ S3): trước đây S3 chết nên lọt cũng vô hại;
# S3 sống (MinIO) ⇒ file thật vào bài chấm, token file ngoài trần EVAL_DU_LIEU_BYTE ⇒ phải chặn TRƯỚC khi bật S3.
_KHOA_FILE = ("workflow_output_files", "workflow_output_file", "attachments", "files", "url")


def _chi_pass_fail() -> bool:
    # [D lô 2 MAJOR] mặc định BẬT (như phía agentic-backend): quên đặt cờ ⇒ vẫn kẹp. Tắt hẳn (như Capitol gốc): EVAL_CHI_PASS_FAIL=0
    return _os.environ.get("EVAL_CHI_PASS_FAIL", "1") != "0"


def _kiem_chi_pass_fail(eval_suite: list, inputs: list) -> None:
    """Ném ValueError (chữ cố định, không chép nội dung) nếu lượt nằm ngoài đường đã kẹp tiền."""
    if len(eval_suite) > EVAL_BAI_TOI_DA:
        raise ValueError(f"Bộ chạy thử: tối đa {EVAL_BAI_TOI_DA} bài chấm mỗi lượt (nhận {len(eval_suite)}).")
    for c in eval_suite:
        if not isinstance(c, (PassFailEvalConfig, ScoreEvalConfig)):
            raise ValueError(f"Bộ chạy thử chỉ nhận bài chấm pass_fail / score (nhận {type(c).__name__}).")
        # [vòng 3] giám khảo phải đi qua GPT5Service (nơi kẹp max_tokens + byte và ép gpt-6-luna) — họ Claude / gpt-4o đi service khác, không kẹp
        from eval_srvc.llm_service.gpt5_service import GPT5Service as _G5
        from eval_srvc.llm_service.llm_service_factory import LLMServiceFactory as _F  # noqa: F401 — chỉ để báo lỗi rõ nếu tên đổi
        jm = getattr(getattr(c, "judge_model", None), "value", None)
        if jm not in ("gpt-6-luna", "gpt-5-mini", "gpt-5.4") and jm not in _G5.SUPPORTED_MODELS:
            raise ValueError(f"Bộ chạy thử: giám khảo {jm!r} không đi đường gpt-6-luna có trần — chỉ nhận gpt-6-luna / gpt-5-mini / gpt-5.4 (ép về luna).")
        n = len(c.model_dump_json().encode())
        if n > EVAL_TIEU_CHI_BYTE:
            raise ValueError(f"Bộ chạy thử: cấu hình một bài chấm {n} byte vượt trần {EVAL_TIEU_CHI_BYTE}.")
    if len(inputs) != EVAL_DAU_VAO_TOI_DA:
        raise ValueError(f"Bộ chạy thử: đúng {EVAL_DAU_VAO_TOI_DA} đầu vào mỗi lượt (nhận {len(inputs)}).")
    for d in inputs:
        if not isinstance(d.content, str) or not d.content.strip():
            raise ValueError("Bộ chạy thử: đầu vào phải là chữ (không URL, không danh sách / JSON).")
        md = d.meta_data or {}
        if any(k in md for k in _KHOA_FILE):
            raise ValueError("Bộ chạy thử: không nhận file đính kèm / URL trong đầu vào bài chấm.")
        # cận trên của chuỗi dữ liệu thật: meta ĐẦY ĐỦ (bài chấm chỉ lấy tập con) + content, cùng cách json.dumps của bài chấm
        n = len(_json.dumps({"content": d.content, **md}, indent=4, default=str).encode())
        if n > EVAL_DU_LIEU_BYTE:
            raise ValueError(f"Bộ chạy thử: dữ liệu chấm {n} byte (đã mã hoá như lời dặn) vượt trần {EVAL_DU_LIEU_BYTE} — không cắt (chấm phần cắt là chấm sai bài).")


class BaseEvalRunner(ABC):
    """
    Base class for orchestrating the execution of multiple evaluation methods on input data.

    This class manages a suite of evaluations, running them in parallel
    on the same input data and collecting their results.
    """

    def __init__(self, eval_suite: list[TEvalConfig]) -> None:
        """
        Initialize the evaluation runner with a suite of evaluations.

        Parameters
        ----------
        eval_suite : list[BaseEvalConfig]
            List of evaluation configurations to run.
        """
        self.eval_suite = eval_suite
        self.evals = [EvalFactory.get_eval(eval) for eval in eval_suite]
        self.id_to_content = {}

    def _post_process_results(
        self, maybe_exceptions: list[None | Exception]
    ) -> list[EvalResult]:
        """
        Process evaluation results into a standardized format.

        Parameters
        ----------
        maybe_exceptions : list
            List of possibly exceptions.

        Returns
        -------
        processed_results : list[EvalResult]
            List of processed evaluation results in a standardized format.
        """
        processed_results = []

        for config, eval, maybe_exc in zip(
            self.eval_suite, self.evals, maybe_exceptions
        ):

            if isinstance(maybe_exc, Exception):
                logger.error(f"Failed to run eval {config.eval_id}: {maybe_exc}")
                result = EvalResult(
                    eval_id=config.eval_id,
                    input_results=[],
                    error_msg=f"Error: {maybe_exc}",
                    eval_name=config.name,
                )

            else:
                result = EvalResult(
                    eval_id=config.eval_id,
                    input_results=eval.input_results,
                    error_msg=None,
                    eval_name=config.name,
                )

            processed_results.append(result)

        return processed_results

    @abstractmethod
    def run(self) -> list[EvalResult]:
        """
        Runs the eval suite for a given eval scenario.

        Returns
        -------
        __ : list[EvalResult]
            A list of results, one per successful eval in the suite.
        """
        ...


class BacktestRunner(BaseEvalRunner):

    @classmethod
    def _sort_and_find_failed_results(
        cls, ordering: list[UUID], eval_result: EvalResult
    ) -> None:
        """
        Sort evaluation results according to input ordering and identify failed evaluations.

        This method processes evaluation results by maintaining the original input ordering
        and separating successful results from failed ones. It modifies the eval_result
        object in-place to populate the failed_ids and reorder input_results.

        Parameters
        ----------
        ordering : list[UUID]
            List of UUIDs representing the original order of inputs that were evaluated.
        eval_result : EvalResult
            Evaluation result object containing input_results to be processed and sorted.
            This object is modified in-place.
        """

        results_dict = {
            input_result.id: input_result for input_result in eval_result.input_results
        }

        sorted_results = []

        for oid in ordering:

            if result := results_dict.get(oid):
                sorted_results.append(result)
            else:
                eval_result.failed_ids.append(oid)

        eval_result.input_results = sorted_results

        if eval_result.failed_ids:
            logger.warning(
                f"Eval {eval_result.eval_id} failed to evaluate {len(eval_result.failed_ids)} inputs."
            )

    async def _process_input(self, input: Document) -> Document | None:
        """
        Preprocess an input for backtesting.

        If the input content is a URL, fetches the content from the web.

        Parameters
        ----------
        input : Document
            Input document with either content or a url in the metadata.

        Returns
        -------
        input : Document
            A document after handling urls in the meta-data.
        """

        if not input.content and (url := input.meta_data.get("url")):
            # This is a temporary solution to web page parsing not occuring
            # before eval-micro-service is reached.
            self.id_to_content[input.source_id] = url
            content = await BS4Scraper.get_url_content(url)
            if not content:
                logger.error(f"Failed to get URL content from {url} for backtest input")
                return None
            else:
                input.content = content
        else:
            self.id_to_content[input.source_id] = input.content

        return input

    async def run(self, inputs: list[Document]) -> list[EvalResult]:
        """
        Run evaluations on a list of inputs for backtesting.

        Parameters
        ----------
        inputs : list[Document]
            List of input Documents to evaluate.

        Returns
        -------
        results : list[EvalResult]
            Processed evaluation results.
        """
        if _chi_pass_fail():   # [va_muc_toi_da_r2] kiểm TRƯỚC _process_input (tải URL) và trước mọi lần gọi AI
            _kiem_chi_pass_fail(self.eval_suite, inputs)
        tasks = [self._process_input(input) for input in inputs]
        processed_inputs = await asyncio.gather(*tasks, return_exceptions=True)
        processed_inputs = [ip for ip in processed_inputs if isinstance(ip, Document)]

        tasks = [eval.eval(processed_inputs) for eval in self.evals]
        maybe_exceptions = await asyncio.gather(*tasks, return_exceptions=True)

        results = self._post_process_results(maybe_exceptions)
        ordering = [input.id for input in inputs]

        for eval_result in results:
            self._sort_and_find_failed_results(ordering, eval_result)

        return results


class PipelineRunner(BaseEvalRunner):

    async def _process_stream(self, report_stream: AsyncGenerator) -> AsyncGenerator:
        """
        Process a stream of reports, adding URLs for each source ID.

        Parameters
        ----------
        report_stream : AsyncGenerator
            Stream of report items.

        Returns
        -------
        processed_stream : AsyncGenerator
            Stream of processed report items with URLs added.
        """
        async for item in report_stream:
            if item:
                source_id = item["source_id"]
                url = f"{system_settings.base_settings.FRONTEND_BASE_URL}/story/{source_id}"
                self.id_to_content[source_id] = url
                yield item

    async def run(self, pipeline: dict, prompts: list[str]) -> list[EvalResult]:
        """
        Run evaluations on reports generated from a pipeline with multiple prompts.

        Parameters
        ----------
        pipeline : dict
            Pipeline configuration to use for generating reports.
        prompts : list[str]
            List of prompts to run through the pipeline.

        Returns
        -------
        results : list[EvalResult]
            Processed evaluation results.

        Raises
        ------
        ValueError
            If no prompts are provided.
        """
        if _chi_pass_fail():   # [va_muc_toi_da_r2] chạy cả pipeline Capitol mỗi prompt — không kẹp được tiền ⇒ từ chối
            raise ValueError("Bộ chạy thử không nhận bài chấm pipeline (chạy cả pipeline mỗi câu — chưa có mức tiền tối đa).")
        if not prompts:
            raise ValueError("At least one prompt must be provided.")

        user_configs = [
            {"user_config_params": {"user_query": prompt, **pipeline}}
            for prompt in prompts
        ]

        report_stream = generate_reports(
            user_configs,
            org_id=org_id_ctx.get(),
            max_report_workers=system_settings.eval_settings.MAX_REPORT_WORKERS,
        )

        report_stream = self._process_stream(report_stream)

        # NOTE: data is a generator. it can only be iterated through once. to pass it to multiple
        # .eval calls, we need to clone the generators, which is surpisingly tricky...
        cloning_factory = GeneratorCloningFactory(report_stream)
        data_clones = cloning_factory.create_clones(len(self.evals))

        tasks = [eval.eval(clone) for eval, clone in zip(self.evals, data_clones)]
        maybe_exceptions = await asyncio.gather(*tasks, return_exceptions=True)

        return self._post_process_results(maybe_exceptions)
