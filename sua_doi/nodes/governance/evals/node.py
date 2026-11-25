"""Evals Node implementation for running evaluations against content"""

import asyncio
import logging
import re
import uuid
from typing import Any, Callable, Dict, List

from sentry_sdk import capture_exception

from api.validation import sanitize_name
from nodes.governance.evals.dimensional_eval_helpers import (
    DIMENSIONAL_EVAL_TYPES,
    build_dimensional_eval_meta_data,
    is_dimensional_eval,
)
from nodes.governance.evals.file_parsers import (
    merge_text_chunks,
    parse_file,
)
from nodes.governance.evals.models import EvalTestTypeEnum
from nodes.governance.evals.params import PARAMS
from nodes.governance.evals.ports import OUTPUT_PORTS
from services.evals.metrics import success_rate
from services.platform.evals import EvaluationService
from temporal.errors.node_errors import EvalNotFoundError
from workflow.node.base.node import BaseNode, NodeOutput
from workflow.node.base.param.custom import MarkdownDisplay
from workflow.node.registry import register

logger = logging.getLogger(__name__)

# [ghi chú] Khối Đánh giá gốc trả COMPLETED dù MỌI bài chấm ERROR (hết giờ, thiếu khoá…)
# ⇒ lượt ghi SUCCESS / trang "thành công" mà không có điểm nào (c90a9c0c 07/11). Vá: mọi bài ERROR ⇒ NodeOutput.error (⇒ NodeReportedError, khối
# FAILED — cùng đường dừng lượt như khối Rào của bộ). Có ≥ 1 bài chấm được (PASS / FAIL / NA) ⇒ y bản gốc. Câu báo CỐ ĐỊNH, không chép lỗi gốc:
# lỗi gốc hay chứa "timed out" / "connection" mà bộ xếp lỗi (temporal/errors/classification.py) coi là lỗi tạm ⇒ Temporal thử lại cả khối ⇒ chấm lại tốn
# tiền. Hỏi chính bộ xếp lỗi, câu bị coi là thử lại được ⇒ dùng câu ngắn nhất. Lỗi gốc từng bài vẫn ở bảng kết quả (display_model) + emit_error.
_CAU_MOI_BAI_LOI = "Evaluation produced no verdict: every eval returned ERROR ({n}/{n}) — see the per-eval errors in the results table."
_CAU_MOI_BAI_LOI_NGAN = "Evaluation produced no verdict: every eval returned ERROR."


def _cau_moi_bai_loi(n: int) -> str:
    cau = _CAU_MOI_BAI_LOI.format(n=n)
    try:
        from temporal.errors.classification import classify_exception
        from temporal.errors.node_errors import NodeReportedError

        if classify_exception(NodeReportedError(cau)).non_retryable:
            return cau
    except Exception:  # noqa: BLE001
        logger.warning("evals: không kiểm được câu báo bằng bộ xếp lỗi ⇒ câu ngắn", exc_info=True)
    return _CAU_MOI_BAI_LOI_NGAN


# Per-batch concurrency limit for eval API calls. Exemplar and dimensional
# batches each get their own semaphore, so total concurrent calls can be
# up to 2x this value when both batches run in parallel.
MAX_CONCURRENT_EVALS_PER_BATCH = 5


def _extract_file_ref_from_node_outputs(node_outputs: list) -> dict | None:
    """Extract S3 file ref from inbound NodeOutput objects.

    Reads structured NodeFile metadata attached by upstream file-producing
    nodes instead of parsing presigned/proxy URLs.
    """
    for node_output in node_outputs:
        if not isinstance(node_output, NodeOutput) or not node_output.files:
            continue
        f = node_output.files[0]
        if f.s3_key and f.s3_bucket:
            return {
                "s3_bucket": f.s3_bucket,
                "s3_key": f.s3_key,
                "file_name": f.name,
            }
    return None


def _extract_all_file_refs_from_node_outputs(node_outputs: list) -> list[dict]:
    """Collect S3 refs for EVERY NodeFile across inbound NodeOutputs.

    Used to forward file attachments to dimensional + quantitative_check
    evals so they can use Claude's native file handling (PDF/image
    attachment blocks; code execution sandbox for Office files).
    """
    refs: list[dict] = []
    for node_output in node_outputs:
        if not isinstance(node_output, NodeOutput) or not node_output.files:
            continue
        for f in node_output.files:
            if not f.s3_key:
                continue
            ref: dict = {
                "s3_key": f.s3_key,
                "file_name": getattr(f, "name", None),
            }
            if getattr(f, "s3_bucket", None):
                ref["s3_bucket"] = f.s3_bucket
            mime = getattr(f, "mime_type", None)
            if mime:
                ref["mime_type"] = mime
            refs.append(ref)
    return refs


def _normalize_eval_entry(entry: Any) -> dict:
    """Coerce a bare-string eval ID to the canonical dict shape."""
    if isinstance(entry, dict):
        return entry
    eval_id = str(entry) if entry is not None else ""
    logger.warning(
        "Coercing bare-string eval entry %r to dict",
        eval_id,
    )
    return {"option_id": eval_id, "option_display_name": eval_id}


@register
class EvalsNode(BaseNode):
    """Node for running evaluations against content"""

    version = "0.2.1"

    display_name = "Evaluations"
    description = "Tests content quality, factual accuracy, and compliance using pre-built evaluations"
    icon = "ChartColumn"

    show = True  # Show in node catalog
    beta = False

    node_id = "evals_node"
    node_type = "governance"
    node_sub_type = "evals"

    # A judge that returns no rows did not pass. Shown on the node output
    # and on the roll-up when input_results is empty and error_msg is unset.
    _EMPTY_RESULTS_ERROR = "Evaluation returned no input results"

    input_ports = []  # Using param binding
    output_ports = OUTPUT_PORTS
    params = PARAMS

    def __init__(
        self,
        node_config: list[dict],
        node_inputs=None,
        validate_required=True,
        validate_type=False,
    ) -> None:
        """Initialize the evals node."""
        super().__init__(
            node_config=node_config,
            node_inputs=node_inputs,
            validate_required=validate_required,
            validate_type=validate_type,
        )
        self._validated_params = {}
        self._service = None
        self._evaluations_loaded = False

    async def _initialize_service(self):
        """Initialize the evaluation service if not already done."""
        if not self._service:
            self._service = EvaluationService()

    @staticmethod
    def _count_files(file_outputs: list) -> int:
        """Count NodeFile attachments across all inbound NodeOutput objects."""
        n = 0
        for out in file_outputs or []:
            if isinstance(out, NodeOutput) and out.files:
                n += len(out.files)
        return n

    async def _extract_text_from_file_outputs(self, file_outputs: list) -> str:
        """Download every attached file and parse it into plain text using the
        format-specific parsers in `file_parsers.py`. Returns a single string
        with each file wrapped in a `=== File: <name> ===` header. Failures
        on individual files become inline placeholder lines so one bad file
        doesn't stop the whole eval."""
        from services.aws.s3 import S3Service

        s3 = S3Service()
        chunks: list[str] = []
        for out in file_outputs or []:
            if not isinstance(out, NodeOutput) or not out.files:
                continue
            for nf in out.files:
                try:
                    if not nf.s3_key:
                        chunks.append(
                            f"=== File: {nf.name or '<unnamed>'} ===\n"
                            f"[No s3_key on this file — cannot download]"
                        )
                        continue
                    bucket = nf.s3_bucket or None
                    raw = await asyncio.to_thread(
                        s3.download_file_content,
                        s3_key=nf.s3_key,
                        bucket_name=bucket,
                    )
                    text = parse_file(
                        name=nf.name,
                        mime=getattr(nf, "mime_type", None),
                        content=raw,
                    )
                    chunks.append(text)
                except Exception as e:
                    capture_exception(e)
                    logger.warning(
                        f"EvalsNode: failed to parse file {nf.name!r} "
                        f"({nf.s3_key}): {e}"
                    )
                    chunks.append(
                        f"=== File: {nf.name or '<unnamed>'} ===\n"
                        f"[Could not parse: {e}]"
                    )
        return merge_text_chunks(chunks)

    async def _flush_thinking_blocks(self, block_ids: set[str]) -> None:
        accum = self.__dict__.get("_cot_thinking_accum", {})
        for bid in block_ids:
            content = accum.pop(bid, None)
            if content:
                await self._persist_thinking(content)

    # ── Chain-of-thought narration ─────────────────────────────────────
    #
    # Until this narration existed, the only progress an eval node put on
    # the wire was ``emit_progress`` (``display_type=update``), which the
    # A2A adapter deliberately projects as ``unknown`` -- so a consumer
    # filtering to the four CoT types (``node.thinking`` /
    # ``node.agent_message`` / ``node.tool_call`` / ``node.tool_result``;
    # OneEdge's default) never saw an Evaluations node do anything, even
    # while it was the step deciding whether the run's output was any
    # good.  (Not quite "never": the dimensional and reference-output
    # families already streamed the judge's own ``thinking`` deltas; what
    # was missing was any narration for standard evals and the framing --
    # parameters, plan, verdicts, summary -- around all three.)  Each
    # meaningful phase is now narrated as a completed ``thinking`` block
    # and the verdicts as one ``agent_message``, through the SAME
    # ``BaseNode`` emitters agent nodes use, so the events ride the
    # existing Redis -> persister -> event_adapter path (and the OpenSearch
    # copy) unchanged.  Per eval, exactly one "how it is being scored"
    # element: the judge's streamed thinking where there is one
    # (dimensional / reference output), a "Scoring against ..." pre-block
    # where there is not (standard, an opaque batched test) -- never both.
    # ``emit_progress`` calls stay: the FE status line reads them.
    #
    # Fail-open: narration is commentary on the evaluation, never a reason
    # for it to fail.  That covers BOTH halves -- building the text and
    # emitting it -- which is why the two helpers below take a callable:
    # the string is composed inside the guard, so an odd payload shape
    # (a non-dict eval entry, a malformed ``dimensions`` map) costs one
    # narration line, not the evaluation.

    _COT_EXPLANATION_MAX_CHARS = 280

    async def _narrate(
        self, thought: str | Callable[[], str], status: str | None = None
    ) -> None:
        """Emit one completed CoT ``thinking`` block for this eval node.

        ``thought`` may be a ready string or a zero-arg callable that builds
        it; the callable runs inside the fail-open guard.
        """
        try:
            text = thought() if callable(thought) else thought
            await self.emit_thinking(
                thought=text, dynamic_status=status, streaming=False
            )
        except Exception as exc:  # noqa: BLE001 - narration must not fail the eval
            logger.warning("EvalsNode: CoT narration failed: %s", exc)

    async def _narrate_summary(self, content: str | Callable[[], str]) -> None:
        """Emit the eval node's verdict summary as one CoT ``agent_message``.

        Same contract as ``_narrate``: a callable ``content`` is built inside
        the guard.
        """
        try:
            text = content() if callable(content) else content
            await self.emit_agent_message(
                content=text, dynamic_status="Evaluation summary", streaming=False
            )
        except Exception as exc:  # noqa: BLE001 - narration must not fail the eval
            logger.warning("EvalsNode: CoT summary emission failed: %s", exc)

    @staticmethod
    def _eval_label(eval_conf: Dict[str, Any]) -> str:
        """``"<display name> (<type>)"`` for a selected eval entry."""
        name = eval_conf.get("option_display_name") or eval_conf.get("option_id")
        eval_type = eval_conf.get("option_type")
        return f"{name} ({eval_type})" if eval_type else str(name)

    #: Exception types whose message this node (or platform-api's typed
    #: "eval not found" path) authored for a reader: configuration /
    #: validation errors and the node's own timeouts. Anything else is
    #: transport or upstream text and never reaches ``node.thinking``.
    _COT_ABORT_SAFE_EXCEPTIONS: tuple[type[BaseException], ...] = (
        EvalNotFoundError,
        ValueError,
        TimeoutError,
    )
    _COT_ABORT_MAX_CHARS = 200

    @classmethod
    def _cot_abort_text(cls, exc: BaseException) -> str:
        """The customer-visible ``thinking`` line for an aborted evaluation.

        ``node.thinking`` is rendered to the end user (OneEdge's default CoT
        ``types``), while ``node.node_failed`` -- where the raw ``str(exc)``
        rides as ``details`` -- is not. So the raw text is only repeated here
        for the exception types this node authored the message of; for
        everything else the line names the failure class and points at the
        failure event.
        """
        if isinstance(exc, cls._COT_ABORT_SAFE_EXCEPTIONS):
            text = " ".join(str(exc).split()) or type(exc).__name__
            if len(text) > cls._COT_ABORT_MAX_CHARS:
                text = text[: cls._COT_ABORT_MAX_CHARS - 1].rstrip() + "…"
            return f"Evaluation aborted: {text}"
        return (
            "Evaluation aborted before a verdict was reached "
            f"({type(exc).__name__}); the error detail is on the node's "
            "failure event."
        )

    _COT_ERROR_MAX_CHARS = 120
    _COT_ERROR_CUTOFF = re.compile(r"https?://|[{\[<]")

    @classmethod
    def _cot_safe_error_text(cls, error_msg: Any) -> str:
        """An errored eval's ``error_msg`` as it may appear on ``node.thinking``.

        ``error_msg`` is ``str(exc)[:200]`` of whatever failed -- for a
        transport failure that is a URL and the upstream body. Same policy
        as ``_cot_abort_text``: keep the human-readable head, cut at the
        first URL / structured payload, cap the length. The full text stays
        on the node's markdown result for operators.
        """
        text = " ".join(str(error_msg or "").split())
        cut = cls._COT_ERROR_CUTOFF.search(text)
        if cut:
            text = text[: cut.start()].rstrip(" :-,;(")
        if len(text) > cls._COT_ERROR_MAX_CHARS:
            text = text[: cls._COT_ERROR_MAX_CHARS - 1].rstrip() + "…"
        return text or "see the node's result for detail"

    @staticmethod
    def _cot_status_word(status: Any) -> str:
        return {
            "passed": "PASSED",
            "failed": "FAILED",
            "na": "N/A",
            "error": "ERROR",
        }.get(status, str(status or "unknown").upper())

    @classmethod
    def _cot_explanation(cls, eval_result: Dict[str, Any]) -> str | None:
        """The judge's explanation for one raw ``eval_result``, truncated.

        Standard evals carry ``explanation``; exemplar and dimensional
        evals carry ``overall_explanation``.  ``None`` when there is none
        (errored evals never reached a judgement).
        """
        input_results = eval_result.get("input_results") or []
        first = input_results[0] if input_results else None
        response = first.get("response") if isinstance(first, dict) else None
        if not isinstance(response, dict):
            return None
        text = response.get("explanation") or response.get("overall_explanation")
        if not isinstance(text, str) or not text.strip():
            return None
        text = " ".join(text.split())
        if len(text) > cls._COT_EXPLANATION_MAX_CHARS:
            text = text[: cls._COT_EXPLANATION_MAX_CHARS - 1].rstrip() + "…"
        return text

    @classmethod
    def _cot_result_line(
        cls, entry: Dict[str, Any], eval_result: Dict[str, Any] | None = None
    ) -> str:
        """One line of verdict for a ``_summarize_one_eval`` entry.

        ``eval_result`` (the raw entry the summary was built from) adds the
        judge's explanation when given.  Mirrors the markdown formatter's
        rules: an errored eval reports its error, not the fallback score
        beside it; exemplar/dimensional scores are out of 10, a standard
        ``score`` eval's scale is its own.
        """
        name = entry.get("eval_name") or entry.get("eval_id") or "evaluation"
        status = entry.get("status")
        eval_type = entry.get("eval_type")
        head = f"{name}: {cls._cot_status_word(status)}"
        if eval_type:
            type_label = entry.get("eval_subtype") or eval_type
            head += f" [{str(type_label).replace('_', ' ')}]"
        details: list[str] = []
        if status == "error":
            if entry.get("error_msg"):
                details.append(f"error: {cls._cot_safe_error_text(entry['error_msg'])}")
        else:
            score = entry.get("score")
            if score is not None:
                details.append(
                    f"score {score}/10"
                    if eval_type in ("exemplar", "dimensional")
                    else f"score {score}"
                )
            verdict = entry.get("verdict")
            if verdict and eval_type != "exemplar":
                details.append(f"verdict {verdict}")
            dims = entry.get("dimensions")
            if isinstance(dims, dict) and dims:
                details.append(
                    "dimensions: "
                    + ", ".join(
                        f"{dim.replace('_', ' ')} "
                        f"{d.get('score') if d.get('score') is not None else 'N/A'}"
                        for dim, d in dims.items()
                        if isinstance(d, dict)
                    )
                )
        if details:
            head += " — " + "; ".join(details)
        explanation = cls._cot_explanation(eval_result) if eval_result else None
        if explanation:
            head += f". {explanation}"
        return head

    @staticmethod
    def _success_rate(counts: Dict[str, Any]) -> float | None:
        """Percentage of SCORED evals that passed, or ``None`` when nothing
        was scored -- ``services.evals.metrics.success_rate``, the one
        definition shared with the A2A suite roll-up
        (``api/a2a_gateway/eval_rollup.py``), which imports that leaf module
        rather than this node."""
        return success_rate(counts)

    @classmethod
    def _build_cot_summary(cls, test_name: str, summary: Dict[str, Any]) -> str:
        """The ``agent_message`` text: suite counts plus one line per eval."""
        counts = summary.get("counts") or {}
        total = int(counts.get("total", 0) or 0)
        passed = int(counts.get("passed", 0) or 0)
        failed = int(counts.get("failed", 0) or 0)
        na = int(counts.get("na", 0) or 0)
        errors = int(counts.get("errors", 0) or 0)
        head = f'Evaluation results for "{test_name}": {passed} passed, {failed} failed'
        if na:
            head += f", {na} N/A"
        if errors:
            head += f", {errors} error{'s' if errors != 1 else ''}"
        head += f" ({total} total)."
        rate = cls._success_rate(counts)
        if rate is not None:
            head += f" Success rate {rate:.1f}%."
        else:
            head += " Success rate N/A (nothing was scored)."
        lines = [head]
        lines.extend(f"- {cls._cot_result_line(e)}" for e in summary.get("evals") or [])
        return "\n".join(lines)

    async def _run_dimensional_eval(
        self,
        orgid: str,
        eval_id: str,
        eval_type: str,
        output: str | dict,
        meta_data: dict | None = None,
        workflow_output_files: list[dict] | None = None,
    ) -> dict:
        """Run a node-level dimensional eval with streaming thinking.

        Streams thinking deltas to the frontend in real-time via SSE,
        then returns the final eval result."""
        sse_stream = await self._service.run_dimensional_eval_by_id(
            orgid=orgid,
            eval_id=eval_id,
            output=output,
            meta_data=meta_data,
            node_id=self.node_id,
            workflow_run_id=self._session_id,
            workflow_output_files=workflow_output_files,
        )

        result = None
        seen_block_ids: set[str] = set()
        async for event in sse_stream:
            if event["event"] == "thinking_delta":
                bid = event["data"]["block_id"]
                seen_block_ids.add(bid)
                await self.emit_thinking(
                    thought=event["data"]["text"],
                    streaming=True,
                    block_id=bid,
                    dynamic_status=event["data"].get(
                        "label", f"Eval thinking: {eval_type}"
                    ),
                )
            elif event["event"] == "status":
                await self.emit_progress(event["data"])
            elif event["event"] == "result":
                result = event["data"]
            elif event["event"] == "error":
                raise RuntimeError(f"Eval streaming error: {event['data']}")

        await self._flush_thinking_blocks(seen_block_ids)

        if result is None:
            raise RuntimeError("Eval stream ended without a result event")

        result.setdefault("eval_type", eval_type)
        return result

    async def _run_reference_output_eval(
        self,
        orgid: str,
        eval_id: str,
        workflow_output: str,
        workflow_output_file: dict | None = None,
        test_id: str | None = None,
        node_id: str | None = None,
        workflow_run_id: str | None = None,
        eval_name: str | None = None,
    ) -> dict | None:
        """Run a reference output eval with streaming thinking."""
        sse_stream = await self._service.run_reference_output_eval(
            orgid=orgid,
            eval_id=eval_id,
            workflow_output=workflow_output,
            workflow_output_file=workflow_output_file,
            test_id=test_id,
            node_id=node_id,
            workflow_run_id=workflow_run_id,
        )

        suite_result = None
        seen_block_ids: set[str] = set()
        async for event in sse_stream:
            if event["event"] == "thinking_delta":
                bid = event["data"]["block_id"]
                seen_block_ids.add(bid)
                await self.emit_thinking(
                    thought=event["data"]["text"],
                    streaming=True,
                    block_id=bid,
                    dynamic_status=event["data"].get(
                        "label", "Eval thinking: reference_output"
                    ),
                )
            elif event["event"] == "status":
                await self.emit_progress(event["data"])
            elif event["event"] == "result":
                suite_result = event["data"]
            elif event["event"] == "error":
                raise RuntimeError(
                    f"Reference output eval streaming error: {event['data']}"
                )

        await self._flush_thinking_blocks(seen_block_ids)

        if suite_result is None:
            raise RuntimeError(
                "Reference output eval stream ended without a result event"
            )

        error_msg = suite_result.get("error_msg") or suite_result.get("error")
        if error_msg and "eval_results" not in suite_result:
            raise RuntimeError(f"Reference output eval failed: {str(error_msg)[:100]}")

        # A direct score payload has no eval_results wrapper. Only a present
        # eval_results list with no rows is the empty-result case.
        if "eval_results" not in suite_result:
            direct = dict(suite_result)
            direct.pop("thinking", None)
            return direct

        eval_results = suite_result.get("eval_results") or []
        first = (
            eval_results[0]
            if eval_results and isinstance(eval_results[0], dict)
            else {}
        )
        input_results = first.get("input_results") or []
        if not input_results:
            # No judge row. Keep a service error when one was sent with the
            # empty list; otherwise the caller records a plain empty result.
            carried = (
                first.get("error_msg")
                or first.get("error")
                or suite_result.get("error_msg")
                or suite_result.get("error")
            )
            if carried:
                return {"error": str(carried)[:200]}
            return None
        response = input_results[0].get("response")
        if not isinstance(response, dict):
            return None
        response = dict(response)
        response.pop("thinking", None)
        return response

    async def _create_test_config(
        self,
        test_name: str,
        content: list,
        eval_ids: List[str],
        workflow_output_files: list[dict] | None = None,
        input_meta_data: dict | None = None,
    ) -> Dict[str, Any]:
        """Create test configuration for the evaluation service.

        `workflow_output_files`, when set, is forwarded to platform-api
        which threads it onto every input's `meta_data` so pass_fail /
        score evals can attach the files natively to Claude (PDF/image
        as document/image blocks; Office files via the code execution
        sandbox).

        `input_meta_data`, when set, is merged onto every input's
        `meta_data`. Used to thread upstream context (task,
        user_query, system_prompt, agent_task) so judges can score
        against the actual user query."""
        test_config: dict[str, Any] = {"contents": content}
        if workflow_output_files:
            test_config["workflow_output_files"] = workflow_output_files
        if input_meta_data:
            test_config["input_meta_data"] = input_meta_data
        return {
            "name": test_name,
            "evaluations": eval_ids,
            "test_type": EvalTestTypeEnum.CONTENT_TEST.value,
            "test_config": test_config,
        }

    async def _wait_for_test_completion(
        self, orgid: str, test_id: str, run_id: str, user_token: str, timeout: int = 300
    ) -> str:
        """Poll for test completion with timeout."""
        start_time = asyncio.get_event_loop().time()
        poll_interval = 3  # seconds

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"Test run timed out after {timeout} seconds")

            try:
                test_run_status = await self._service.get_test_status(
                    orgid=orgid,
                    test_id=test_id,
                    run_id=run_id,
                    user_token=user_token,
                )

                status = test_run_status.get("status")

                # Update progress
                await self.emit_progress(
                    dynamic_status=f"Test status: {status}",
                    data={"status": status, "elapsed_seconds": int(elapsed)},
                )

                # Check if completed
                if status.lower() == "completed":
                    run_results = await self._service.get_test_run(
                        orgid=orgid,
                        test_id=test_id,
                        run_id=run_id,
                        user_token=user_token,
                    )
                    logger.info(f"Specific test run results: {run_results}")

                    logger.info(f"Test evals run results : {run_results}")
                    return run_results

                elif status.lower() == "incomplete":
                    await asyncio.sleep(poll_interval)
                    continue

                else:
                    logger.error(f"Unknown test status received: {status}")
                    raise ValueError(f"Unknown test status: {status}")

            except Exception as e:
                capture_exception(e)
                if "Test run not found" in str(e):
                    # Test might not be ready yet, continue polling
                    await asyncio.sleep(poll_interval)
                    continue
                raise

    @staticmethod
    def _is_exemplar_result(eval_result: Dict[str, Any]) -> bool:
        return eval_result.get("eval_type") == "exemplar"

    @staticmethod
    def _is_dimensional_result(eval_result: Dict[str, Any]) -> bool:
        return eval_result.get("eval_type") in DIMENSIONAL_EVAL_TYPES

    @staticmethod
    def _classify_by_verdicts(input_results: Any) -> str:
        """Classify a list of eval input_results as passed/failed/na/error.

        Used by the dimensional AND standard paths. They classified
        separately before, and drifted: a standard NA counted as a pass while
        a dimensional NA did not, and the eval service returns NA for an
        incomplete response and for unparseable JSON.

        An eval that errored never reached a verdict, so error wins over
        FAIL; FAIL then wins over NA, and NA marks not-applicable rather
        than a silent pass — NA is a real verdict the eval service returns
        for standard evals too, not just dimensional ones. An empty list
        never reached a verdict either: the judge produced no result, so
        that is an error, not a pass. Non-dict entries and non-string
        verdicts are skipped rather than raising.
        """
        if not input_results:
            return "error"
        any_na = False
        for r in input_results:
            if not isinstance(r, dict):
                continue
            response = r.get("response")
            if not isinstance(response, dict):
                continue
            # A populated `error` outranks whatever verdict came with it: the
            # eval service may still report a crash as verdict=fail.
            if response.get("error"):
                return "error"
            verdict = response.get("verdict")
            if not isinstance(verdict, str):
                continue
            verdict = verdict.lower()
            if verdict == "error":
                return "error"
            if verdict == "fail":
                return "failed"
            if verdict == "na":
                any_na = True
        if any_na:
            return "na"
        return "passed"

    @staticmethod
    def _format_dimensional_response(response: Dict[str, Any]) -> list[str]:
        """Format a dimensional eval response (dimension scores) as markdown."""
        parts: list[str] = []
        eval_type = response.get("eval_type", "dimensional")
        verdict = response.get("verdict", "").lower()
        if verdict == "pass":
            verdict_emoji = "✅"
        elif verdict == "na":
            verdict_emoji = "⚪"
        elif verdict == "error":
            verdict_emoji = "⚠️"
        else:
            verdict_emoji = "❌"

        # For NA results suppress the parent "Overall Score" line —
        # the score is None / not meaningful — but still surface the
        # explanation and the per-dimension table below since each dim
        # carries its own N/A label and is useful context.
        is_na = verdict == "na"
        # A populated `error` means the eval never reached a verdict; any score
        # or verdict alongside it is the eval service's fallback, not a result.
        is_error = verdict == "error" or bool(response.get("error"))
        overall = response.get("overall_score")
        if overall is not None and not is_na and not is_error:
            parts.append(f"- **Overall Score:** {overall}/10")
        if verdict and not is_error:
            parts.append(f"- **Verdict:** {verdict_emoji} {verdict.upper()}")

        explanation = response.get("overall_explanation")
        if explanation:
            parts.append("- **Overall Assessment:**")
            parts.append(f"  > {explanation}\n")

        dims = response.get("dimensions") or {}
        # Keep dim listing visible even for NA verdicts — Quantitative
        # Check produces per-dim score=null with explicit N/A labels
        # (handled in the row formatter below), so the table is
        # informative rather than a misleading wall of zeros.
        if dims:
            parts.append("**Dimension scores:**")
            for dim_name, dim_data in dims.items():
                if not isinstance(dim_data, dict):
                    continue
                score = dim_data.get("score")
                label = dim_name.replace("_", " ").title()
                parts.append(
                    f"- **{label}:** {score if score is not None else 'N/A'}/10"
                )
                if dim_data.get("explanation"):
                    parts.append(f"  > {dim_data['explanation']}")
            parts.append("")

        # Eval-specific metadata extras
        md = response.get("metadata") or {}
        if eval_type == "goal_achievement" and md.get("inferred_goal"):
            parts.append(f"**Inferred goal:** {md['inferred_goal']}")
        if eval_type == "goal_achievement" and md.get("suggestion"):
            parts.append(f"**Suggestion:** {md['suggestion']}")
        if eval_type == "factuality":
            mode_note = md.get("mode_note")
            if mode_note:
                parts.append(f"_{mode_note}_")
            grounding = md.get("grounding_rate")
            if grounding is not None:
                # Denominator must be the claims actually checked, not every
                # claim found -- the rate excludes ones that couldn't be
                # verified. Older responses only carry total_claims.
                checked = md.get("checked_claims")
                if checked is None:
                    checked = md.get("total_claims") or 0
                parts.append(
                    f"**Grounding rate:** {grounding * 100:.1f}% "
                    f"({md.get('verified_claims', 0)}/{checked} claims verified)"
                )
        if eval_type == "tool_use":
            redundancy = md.get("redundancy_check") or {}
            if redundancy.get("total_calls"):
                parts.append(
                    f"**Tool call analysis:** {redundancy['total_calls']} calls, "
                    f"{redundancy.get('redundant_count', 0)} redundant"
                )

        if response.get("error"):
            parts.append(f"- **Error:** {response['error']}")

        return parts

    @staticmethod
    def _classify_exemplar(eval_result: Dict[str, Any]) -> str:
        """Classify exemplar eval as passed/failed based on overall_score.

        No score is not a pass. An empty list, or rows the judge never
        scored, is an error. A populated error wins over a fallback score.
        """
        saw_score = False
        for r in eval_result.get("input_results") or []:
            if not isinstance(r, dict):
                continue
            response = r.get("response")
            if not isinstance(response, dict):
                continue
            if response.get("error"):
                return "error"
            score = response.get("overall_score")
            if score is None:
                continue
            saw_score = True
            if score < 5.0:
                return "failed"
        if not saw_score:
            return "error"
        return "passed"

    @classmethod
    def _summarize_one_eval(cls, eval_result: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize one raw ``eval_result`` entry into the roll-up shape.

        ENG-2649: the gateway aggregates these per-eval entries across every
        eval node in a run into a single suite-level roll-up
        (``api/a2a_gateway/eval_rollup.py``).  Classification mirrors the
        markdown formatter (``_format_test_results``) exactly so the
        machine-readable roll-up and the human-readable Evaluation Results
        agree on pass/fail/na.

        Returns ``{eval_id, eval_name, eval_type, eval_subtype, status,
        passed, score, verdict, dimensions, error_msg}`` where ``eval_type``
        is one of ``pass_fail`` / ``score`` / ``exemplar`` / ``dimensional``.
        """
        eval_id = eval_result.get("eval_id")
        eval_name = eval_result.get("eval_name") or eval_id
        error_msg = eval_result.get("error_msg")
        input_results = eval_result.get("input_results", []) or []
        response = (
            input_results[0].get("response", {})
            if input_results and isinstance(input_results[0], dict)
            else {}
        )
        if not isinstance(response, dict):
            response = {}

        entry: Dict[str, Any] = {
            "eval_id": eval_id,
            "eval_name": eval_name,
            "eval_type": None,
            "eval_subtype": None,
            "status": None,
            "passed": None,
            "score": None,
            "verdict": None,
            "dimensions": None,
            "error_msg": error_msg or None,
        }

        if error_msg:
            entry["status"] = "error"
            entry["passed"] = False
            entry["verdict"] = "ERROR"
            return entry

        if cls._is_exemplar_result(eval_result):
            entry["eval_type"] = "exemplar"
            entry["status"] = cls._classify_exemplar(eval_result)
            entry["score"] = response.get("overall_score")
        elif cls._is_dimensional_result(eval_result):
            entry["eval_type"] = "dimensional"
            entry["eval_subtype"] = eval_result.get("eval_type")
            entry["status"] = cls._classify_by_verdicts(input_results)
            entry["score"] = response.get("overall_score")
            verdict = response.get("verdict")
            entry["verdict"] = verdict.upper() if isinstance(verdict, str) else verdict
            dims = response.get("dimensions")
            if isinstance(dims, dict):
                entry["dimensions"] = {
                    name: {
                        "score": d.get("score"),
                        "explanation": d.get("explanation"),
                    }
                    for name, d in dims.items()
                    if isinstance(d, dict)
                }
        else:
            # Standard v1 pass_fail / score eval.  ``score`` in the response
            # marks a scored eval; ``verdict`` (PASS/FAIL) marks pass_fail.
            verdict = response.get("verdict")
            entry["verdict"] = verdict
            entry["score"] = response.get("score")
            if "score" in response and "verdict" not in response:
                entry["eval_type"] = "score"
            else:
                entry["eval_type"] = "pass_fail"
            entry["status"] = cls._classify_by_verdicts(input_results)

        if entry["status"] == "error":
            # The eval service may still report a crash as verdict=fail with a
            # 0.0 score. That is not a judgement about the content, so carry
            # the reason instead of republishing the score. An empty
            # input_results has no response error to copy, so name that case.
            entry["error_msg"] = entry["error_msg"] or response.get("error")
            if not entry["error_msg"] and not input_results:
                entry["error_msg"] = cls._EMPTY_RESULTS_ERROR
            entry["score"] = None
            entry["verdict"] = "ERROR"

        entry["passed"] = entry["status"] == "passed"
        return entry

    @classmethod
    def _build_eval_summary(cls, eval_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build the per-node eval summary attached to ``NodeOutput.metadata``.

        ENG-2649: a structured, pre-classified view of this node's eval
        results so the A2A gateway can roll them up across the whole run
        without re-parsing the markdown output.  Counts mirror the markdown
        Summary section (N/A excluded from the success-rate denominator).
        """
        evals = [
            cls._summarize_one_eval(e)
            for e in (eval_results or [])
            if isinstance(e, dict)
        ]
        counts = {"total": len(evals), "passed": 0, "failed": 0, "na": 0, "errors": 0}
        for e in evals:
            status = e.get("status")
            if status == "passed":
                counts["passed"] += 1
            elif status == "failed":
                counts["failed"] += 1
            elif status == "na":
                counts["na"] += 1
            elif status == "error":
                counts["errors"] += 1
        return {"evals": evals, "counts": counts}

    @staticmethod
    def _format_exemplar_response(response: Dict[str, Any]) -> list[str]:
        """Format a reference output eval response as markdown."""
        parts = []
        overall = response.get("overall_score")
        if overall is not None:
            parts.append(f"- **Overall Score:** {overall}/10")
        explanation = response.get("overall_explanation")
        if explanation:
            parts.append("- **Overall Assessment:**")
            parts.append(f"  > {explanation}\n")

        for dim in (
            "content_coverage",
            "quality_match",
            "structure_alignment",
            "data_accuracy",
        ):
            entry = response.get(dim)
            if not isinstance(entry, dict):
                continue
            score = entry.get("score")
            if score is None and dim == "data_accuracy":
                continue
            label = dim.replace("_", " ").title()
            parts.append(f"**{label}:** {score if score is not None else 'N/A'}/10")
            if entry.get("explanation"):
                parts.append(f"> {entry['explanation']}\n")

        missing = response.get("missing_elements", [])
        if missing:
            parts.append("**Missing Elements:**")
            for item in missing:
                parts.append(f"- {item}")
            parts.append("")

        added = response.get("added_elements", [])
        if added:
            parts.append("**Added Elements:**")
            for item in added:
                parts.append(f"- {item}")
            parts.append("")

        if response.get("error"):
            parts.append(f"- **Error:** {response['error']}")

        return parts

    async def _format_test_results(self, test_run: Dict[str, Any]) -> str:
        """Format test results as markdown for display."""
        markdown_parts = []

        markdown_parts.append("# Evaluation Results\n")

        test_name = test_run.get("name", "Unknown Test")
        markdown_parts.append(f"**Test Name:** {test_name}")

        run_id = test_run.get("id")
        test_id = test_run.get("testid")
        run_date = test_run.get("run_date")
        if run_id:
            markdown_parts.append(f"**Run ID:** {run_id}")
        if test_id:
            markdown_parts.append(f"**Test ID:** {test_id}")
        if run_date:
            markdown_parts.append(f"**Run Date:** {run_date}")
        markdown_parts.append("")

        # Filter to the same entries ``_build_eval_summary`` keeps, so the
        # status list below stays index-aligned with what we render.
        eval_results = [
            e for e in (test_run.get("eval_results") or []) if isinstance(e, dict)
        ]

        if eval_results:
            # Classify once, here and for NodeOutput.metadata, so the markdown
            # and the machine-readable roll-up cannot disagree.
            summary = self._build_eval_summary(eval_results)
            counts = summary["counts"]
            total_evals = counts["total"]

            for idx, eval_result in enumerate(eval_results, 1):
                summary_entry = summary["evals"][idx - 1]
                eval_name = eval_result.get("eval_name", f"Evaluation {idx}")
                eval_id = eval_result.get("eval_id", "N/A")
                error_msg = eval_result.get("error_msg")
                input_results = eval_result.get("input_results", [])
                status_for_idx = summary_entry["status"]
                is_exemplar = self._is_exemplar_result(eval_result)
                is_dimensional = self._is_dimensional_result(eval_result)

                markdown_parts.append(f"### {eval_name}")
                markdown_parts.append(f"- **Eval ID:** {eval_id}")
                if is_exemplar:
                    markdown_parts.append("- **Type:** Reference Output")
                elif is_dimensional:
                    type_label = (
                        eval_result.get("eval_type", "dimensional")
                        .replace("_", " ")
                        .title()
                    )
                    markdown_parts.append(f"- **Type:** {type_label}")
                if status_for_idx == "na":
                    status_label = "⚪ N/A"
                elif status_for_idx == "passed":
                    status_label = "✅ Passed"
                elif status_for_idx == "error":
                    status_label = "⚠️ Error"
                else:
                    status_label = "❌ Failed"
                markdown_parts.append(f"- **Status:** {status_label}")

                if error_msg:
                    markdown_parts.append(f"- **Error:** {error_msg}")
                elif not input_results and summary_entry.get("error_msg"):
                    # Empty results have no per-row error to print. Use the
                    # reason already stored on the summary so this text cannot
                    # drift from the roll-up.
                    markdown_parts.append(f"- **Error:** {summary_entry['error_msg']}")

                if input_results:
                    markdown_parts.append("")
                    for i, result in enumerate(input_results, 1):
                        response = result.get("response") or {}

                        if is_exemplar:
                            markdown_parts.extend(
                                self._format_exemplar_response(response)
                            )
                        elif is_dimensional:
                            markdown_parts.extend(
                                self._format_dimensional_response(response)
                            )
                        else:
                            source_id = result.get("source_id", "N/A")
                            result_id = result.get("id", "N/A")

                            if len(input_results) > 1:
                                markdown_parts.append(f"#### Result {i}")
                            markdown_parts.append(f"- **Source ID:** {source_id}")
                            markdown_parts.append(f"- **Result ID:** {result_id}")

                            # Same rule as the dimensional renderer: a
                            # populated `error` means the score and verdict
                            # beside it are the eval service's fallback, not
                            # a judgement about the content.
                            std_errored = bool(response.get("error"))
                            if std_errored:
                                markdown_parts.append(
                                    f"- **Error:** {response['error']}"
                                )
                            if response and not std_errored:
                                if "score" in response:
                                    markdown_parts.append(
                                        f"- **Score:** {response.get('score')}"
                                    )
                                if "verdict" in response:
                                    verdict = response.get("verdict", "N/A")
                                    # NA is "nothing to judge", not a
                                    # failure; match the dimensional ⚪.
                                    if verdict == "PASS":
                                        verdict_emoji = "✅"
                                    elif verdict == "NA":
                                        verdict_emoji = "⚪"
                                    else:
                                        verdict_emoji = "❌"
                                    markdown_parts.append(
                                        f"- **Verdict:** {verdict_emoji} {verdict}"
                                    )
                                if "explanation" in response:
                                    markdown_parts.append("- **Explanation:**")
                                    markdown_parts.append(
                                        f"  > {response.get('explanation')}"
                                    )

                                for key, value in response.items():
                                    if key not in [
                                        "score",
                                        "verdict",
                                        "explanation",
                                        "error",
                                    ]:
                                        markdown_parts.append(
                                            f"- **{key.replace('_', ' ').title()}:** {value}"
                                        )

                            markdown_parts.append("")

                markdown_parts.append("")

            # Summary
            markdown_parts.append("## Summary\n")
            markdown_parts.append(f"- **Total Evaluations:** {total_evals}")
            markdown_parts.append(f"- **Passed:** {counts['passed']}")
            markdown_parts.append(f"- **Failed:** {counts['failed']}")
            if counts["na"]:
                markdown_parts.append(f"- **N/A:** {counts['na']}")
            if counts["errors"]:
                markdown_parts.append(f"- **Errors:** {counts['errors']}")
            rate = self._success_rate(counts)
            if rate is not None:
                markdown_parts.append(f"- **Success Rate:** {rate:.1f}%")
            else:
                markdown_parts.append("- **Success Rate:** N/A")

            id_to_content = test_run.get("id_to_content")
            if id_to_content:
                markdown_parts.append("\n## Content Tested\n")
                for idx, (content_id, content) in enumerate(id_to_content.items(), 1):
                    markdown_parts.append(f"### Content {idx}")
                    markdown_parts.append(f"**ID:** {content_id}\n")
                    markdown_parts.append("**Full Content:**")
                    markdown_parts.append(f"> {content}\n")

        else:
            markdown_parts.append("## No Evaluation Results Found\n")
            markdown_parts.append(
                "The test completed but no evaluation results were returned."
            )

        return "\n".join(markdown_parts)

    async def _run_standard_evals_batch(
        self,
        orgid: str,
        standard_evals: list,
        test_contents: list,
        user_text: str,
        file_refs: list,
        test_name: str,
        user_token: str,
        test_id: str,
    ) -> Dict[str, Any]:
        """Run standard (pass_fail/score) evals as a single batched test.

        Returns dict with 'test_id' and 'run_results' keys.
        """
        if file_refs:
            standard_contents: list = [user_text] if user_text else [""]
        else:
            standard_contents = test_contents
        eval_ids = [e.get("option_id") for e in standard_evals]

        try:
            standard_meta = await build_dimensional_eval_meta_data(
                eval_type="standard",
                workflow_id=self._workflowid,
                node=self,
            )
        except Exception as e:
            logger.warning(f"Failed to build runtime context for standard evals: {e}")
            standard_meta = {}
        if standard_meta and standard_meta.get("task"):
            standard_meta.setdefault("user_query", standard_meta["task"])

        test_config = await self._create_test_config(
            test_name=test_name,
            content=standard_contents,
            eval_ids=eval_ids,
            workflow_output_files=file_refs or None,
            input_meta_data=standard_meta or None,
        )

        await self._narrate(
            lambda: (
                "Scoring against "
                + ", ".join(self._eval_label(e) for e in standard_evals)
                + f" — running {len(standard_evals)} standard pass/fail or score "
                "evaluation(s) as one batched test"
                + (" with the attached file(s) inlined as text" if file_refs else "")
                + "."
            ),
            status="Scoring: standard evaluations",
        )
        await self.emit_progress("Creating test configuration")
        test_response = await self._service.create_test(
            orgid=orgid,
            test_config=test_config,
            user_token=user_token,
        )

        test_id = test_response.get("id") or test_id
        if not test_id:
            raise ValueError("Failed to create test: No test_id returned")

        logger.info(f"Created test with ID: {test_id}")
        await self.emit_progress("Starting test execution")
        run_response = await self._service.run_test(
            orgid=orgid,
            test_id=test_id,
            user_token=user_token,
            node_id=self.node_id,
            workflow_run_id=self._session_id,
        )

        logger.info(f"Run Test_response: {run_response}")

        run_key = run_response.get("s3_key")
        run_id = run_key.replace(".json", "") if run_key else None

        if not run_id:
            raise ValueError("Failed to start test: No run_id returned")

        logger.info(f"Started test run with ID: {run_id}")

        await self.emit_progress("Waiting for test completion")
        run_results = await self._wait_for_test_completion(
            orgid=orgid,
            test_id=test_id,
            run_id=run_id,
            user_token=user_token,
        )

        return {"test_id": test_id, "run_results": run_results}

    async def _run_exemplar_evals_batch(
        self,
        orgid: str,
        exemplar_evals: list,
        workflow_output: str,
        workflow_output_file: dict | None,
        test_id: str,
        v2_type_by_id: dict,
    ) -> Dict[str, Any]:
        """Run exemplar evals concurrently. Returns dict with 'eval_results' list."""
        if not exemplar_evals:
            return {"eval_results": []}

        sem = asyncio.Semaphore(MAX_CONCURRENT_EVALS_PER_BATCH)

        async def _run_single(eval_conf: dict) -> dict:
            async with sem:
                eval_id = eval_conf.get("option_id")
                eval_name = eval_conf.get("option_display_name", eval_id)
                await self.emit_progress(f"Running exemplar eval: {eval_name}")
                # No "Scoring against ..." block here: this family streams
                # the judge's own ``thinking`` deltas (``emit_thinking(...,
                # block_id=...)`` in ``_run_reference_output_eval``), which
                # IS the scoring narration. The plan block above already
                # named the eval; the verdict line below carries the
                # structured outcome. Only the standard (batched) family,
                # whose scoring is opaque to this node, gets a pre-block.
                result = await self._run_reference_output_eval(
                    orgid=orgid,
                    eval_id=eval_id,
                    workflow_output=workflow_output,
                    workflow_output_file=workflow_output_file,
                    test_id=test_id,
                    node_id=self.node_id,
                    workflow_run_id=self._session_id,
                    eval_name=eval_name,
                )
                return {
                    "eval_id": eval_id,
                    "eval_name": eval_name,
                    "result": result,
                }

        tasks = [_run_single(ec) for ec in exemplar_evals]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        eval_results: list[dict] = []
        for eval_conf, result in zip(exemplar_evals, raw_results):
            eval_id = eval_conf.get("option_id")
            eval_name = eval_conf.get("option_display_name", eval_id)
            if isinstance(result, EvalNotFoundError):
                logger.warning(
                    f"Exemplar eval {eval_name} ({eval_id}) skipped: {result}"
                )
                eval_results.append(
                    {
                        "eval_id": eval_id,
                        "eval_name": eval_name,
                        "eval_type": v2_type_by_id.get(eval_id),
                        "error_msg": str(result)[:200],
                        "input_results": [],
                    }
                )
            elif isinstance(result, Exception):
                logger.error(
                    f"Exemplar eval {eval_name} ({eval_id}) failed: {result}",
                    exc_info=(type(result), result, result.__traceback__),
                )
                capture_exception(result)
                eval_results.append(
                    {
                        "eval_id": eval_id,
                        "eval_name": eval_name,
                        "eval_type": v2_type_by_id.get(eval_id),
                        "error_msg": str(result)[:200],
                        "input_results": [],
                    }
                )
            else:
                response = result["result"]
                service_error = (
                    response.get("error") if isinstance(response, dict) else None
                )
                if response is None or service_error:
                    entry = {
                        "eval_id": result["eval_id"],
                        "eval_name": result["eval_name"],
                        "eval_type": v2_type_by_id.get(result["eval_id"]),
                        "input_results": [],
                    }
                    if service_error:
                        entry["error_msg"] = str(service_error)[:200]
                    eval_results.append(entry)
                else:
                    eval_results.append(
                        {
                            "eval_id": result["eval_id"],
                            "eval_name": result["eval_name"],
                            "eval_type": v2_type_by_id.get(result["eval_id"]),
                            "input_results": [
                                {
                                    "response": response,
                                    "source_id": "reference_output",
                                }
                            ],
                        }
                    )

        return {"eval_results": eval_results}

    async def _run_dimensional_evals_batch(
        self,
        orgid: str,
        dimensional_evals: list,
        dim_text_payload: str,
        user_text: str,
        extracted_file_text: str,
        file_refs: list,
    ) -> Dict[str, Any]:
        """Run dimensional evals concurrently. Returns dict with 'eval_results' list."""
        if not dimensional_evals:
            return {"eval_results": []}

        sem = asyncio.Semaphore(MAX_CONCURRENT_EVALS_PER_BATCH)

        async def _run_single(eval_conf: dict) -> dict:
            async with sem:
                eval_id = eval_conf.get("option_id")
                eval_name = eval_conf.get("option_display_name", eval_id)
                eval_type = eval_conf.get("option_type")

                await self.emit_progress(
                    f"Building runtime context for {eval_name} ({eval_type})"
                )

                meta_data = await build_dimensional_eval_meta_data(
                    eval_type=eval_type,
                    workflow_id=self._workflowid,
                    node=self,
                )

                if eval_type == "quantitative_check":
                    eval_output_for_this = (
                        f"{user_text}\n\n{extracted_file_text}".strip()
                        if user_text and extracted_file_text
                        else (user_text or extracted_file_text)
                    )
                else:
                    eval_output_for_this = dim_text_payload

                await self.emit_progress(f"Running dimensional eval: {eval_name}")
                # No "Scoring against ..." block: the judge streams its own
                # ``thinking`` here (see the reference-output branch above).
                result = await self._run_dimensional_eval(
                    orgid=orgid,
                    eval_id=eval_id,
                    eval_type=eval_type,
                    output=eval_output_for_this,
                    meta_data=meta_data,
                    workflow_output_files=file_refs or None,
                )
                return {
                    "eval_id": eval_id,
                    "eval_name": eval_name,
                    "eval_type": eval_type,
                    "result": result,
                }

        tasks = [_run_single(ec) for ec in dimensional_evals]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        eval_results: list[dict] = []
        for eval_conf, result in zip(dimensional_evals, raw_results):
            eval_id = eval_conf.get("option_id")
            eval_name = eval_conf.get("option_display_name", eval_id)
            eval_type = eval_conf.get("option_type")
            if isinstance(result, EvalNotFoundError):
                logger.warning(
                    f"Dimensional eval {eval_name} ({eval_id}) skipped: {result}"
                )
                eval_results.append(
                    {
                        "eval_id": eval_id,
                        "eval_name": eval_name,
                        "eval_type": eval_type,
                        "error_msg": str(result)[:200],
                        "input_results": [],
                    }
                )
            elif isinstance(result, Exception):
                logger.error(
                    f"Dimensional eval {eval_name} ({eval_id}) failed: {result}",
                    exc_info=(type(result), result, result.__traceback__),
                )
                capture_exception(result)
                eval_results.append(
                    {
                        "eval_id": eval_id,
                        "eval_name": eval_name,
                        "eval_type": eval_type,
                        "error_msg": str(result)[:200],
                        "input_results": [],
                    }
                )
            else:
                eval_results.append(
                    {
                        "eval_id": result["eval_id"],
                        "eval_name": result["eval_name"],
                        "eval_type": result["eval_type"],
                        "input_results": [
                            {
                                "response": result["result"],
                                "source_id": "dimensional_eval",
                            }
                        ],
                    }
                )

        return {"eval_results": eval_results}

    async def execute(self) -> NodeOutput:
        """Execute the evals node."""
        try:
            await self.emit_begin()

            # Initialize service
            await self._initialize_service()

            # Get context variables
            orgid = self._orgid
            user_token = self._user_token

            if not orgid:
                raise ValueError("Organization ID not found in context")

            # Get validated parameters
            await self.emit_progress("Retrieving evaluation parameters")
            self._validated_params = await self._get_validated_params()

            # Sanitize where the param is read so the platform record, the
            # run_results fallbacks, and node metadata all carry the same
            # name (test_name can be bound to arbitrary upstream output).
            # Empty must stay empty so the required-name check below fires.
            raw_test_name = self._validated_params.get("test_name", "").strip()
            test_name = (
                sanitize_name(raw_test_name, fallback="Eval Test")
                if raw_test_name
                else ""
            )
            user_text = self._validated_params.get("test_contents") or ""
            raw_evals = self._validated_params.get("evals_list") or []
            if not isinstance(raw_evals, list):
                raw_evals = [raw_evals]
            selected_evals = [
                ec
                for e in raw_evals
                if (ec := _normalize_eval_entry(e)).get("option_id")
            ]

            inbound = await self._get_validated_inbound_node_output()
            file_outputs = inbound.get("files", [])

            def _parameters_text() -> str:
                selected_names = ", ".join(
                    str(e.get("option_display_name") or e.get("option_id"))
                    for e in selected_evals
                )
                return (
                    f'Evaluating "{test_name or raw_test_name or "(unnamed test)"}": '
                    f"{len(selected_evals)} evaluation(s) selected"
                    + (f" — {selected_names}" if selected_names else "")
                    + f". Input: {len(user_text)} character(s) of text, "
                    f"{self._count_files(file_outputs)} attached file(s)."
                )

            await self._narrate(
                _parameters_text, status="Retrieving evaluation parameters"
            )

            # Track text and files separately so we can route them
            # appropriately by eval type.
            #
            # Standard pass_fail/score (legacy v1 path): receives MERGED
            #   text — extracted file content is inlined into test_contents
            #   because the v1 backtest endpoint has no native file support.
            #
            # Exemplar: receives the raw file ref (its multimodal LLM path
            #   already attaches files natively).
            #
            # Dimensional LLM evals (goal_achievement, output_quality,
            #   factuality, tool_use): receive the user_text only AND the
            #   raw file refs via meta_data — Claude attaches them natively
            #   (PDF/image as document blocks, Office via container_upload
            #   + code execution).
            #
            # quantitative_check: same pattern — gets the user_text + file
            #   refs and routes per-source internally (regex on text,
            #   Claude sandbox on files).
            extracted_file_text = ""
            file_refs: list[dict] = []
            if file_outputs:
                await self.emit_progress(
                    f"Parsing {self._count_files(file_outputs)} attached file(s)..."
                )
                extracted_file_text = await self._extract_text_from_file_outputs(
                    file_outputs
                )
                file_refs = _extract_all_file_refs_from_node_outputs(file_outputs)

            # Merged text for the legacy v1 path (pass_fail/score)
            merged_text = (
                f"{user_text}\n\n{extracted_file_text}".strip()
                if user_text and extracted_file_text
                else (user_text or extracted_file_text)
            )
            # `test_contents` keeps the legacy variable name for the
            # standard-eval path below; subsequent code reads it.
            test_contents = merged_text

            # Partition evals by which path handles them.
            # - V1 (legacy table): pass_fail/score → /test/run/... (standard)
            # - V2 exemplar: → /v2/evaluations/.../run-reference-output
            # - V2 workflow types (goal_achievement, output_quality,
            #   factuality, tool_use): → /v2/evaluations/.../run-workflow-eval-by-id
            # We cache the V2 type per id so results render correctly later.
            v1_evals = await self._service.list_v1_evaluations(orgid=orgid)
            v1_ids = {e["id"] for e in v1_evals}

            v2_evals = await self._service.list_v2_evaluations(orgid=orgid)
            v2_type_by_id: dict[str, str] = {
                e["id"]: e.get("type") for e in v2_evals if e.get("id")
            }

            standard_evals = []
            exemplar_evals = []
            dimensional_evals = []
            for eval_conf in selected_evals:
                eval_id = eval_conf.get("option_id")
                if eval_id in v1_ids:
                    standard_evals.append(eval_conf)
                    continue
                v2_type = v2_type_by_id.get(eval_id) or eval_conf.get("option_type")
                # Annotate with resolved type so downstream code (formatter,
                # dimensional eval dispatch) doesn't need to look it up again.
                if v2_type:
                    eval_conf["option_type"] = v2_type
                if v2_type == "exemplar":
                    exemplar_evals.append(eval_conf)
                elif is_dimensional_eval(eval_conf):
                    dimensional_evals.append(eval_conf)
                else:
                    # Default unknown V2 types to standard (pass_fail/score)
                    standard_evals.append(eval_conf)

            # Validate inputs.
            #
            # ENG-1694: workflow used to "succeed" silently when standard
            # evals were selected without real text input. Two holes
            # caused that:
            #   1. Whitespace-only `test_contents` (e.g. " \n ") got
            #      past `not test_contents` because non-empty whitespace
            #      strings are truthy in Python. Strip first.
            #   2. The "test_content or file input" gate looked at the
            #      raw upstream NodeOutput list (`file_outputs`), which
            #      can be non-empty even when the file yielded zero
            #      parseable text and zero `file_refs`. Use `file_refs`
            #      (the actual extracted-file-ref list) as the file
            #      signal everywhere instead.
            test_contents_stripped = (
                test_contents.strip() if isinstance(test_contents, str) else ""
            )
            has_text = bool(test_contents_stripped)
            has_files = bool(file_refs)

            if not test_name:
                raise ValueError("Test name is required")
            if has_text and re.fullmatch(
                r"https?://.*s3[.-].*amazonaws\.com/\S+",
                test_contents_stripped,
            ):
                raise ValueError(
                    "Text content contains a file URL — connect file-producing "
                    "nodes to the Files port instead of the Text Contents port"
                )
            if not has_text and not has_files:
                raise ValueError(
                    "Test content or file input is required. "
                    "Connect a text-producing node (e.g. Agent) to the "
                    "Text Contents port, or a file-producing node (e.g. "
                    "PowerPoint, Excel) to the Files port."
                )
            # Files have already been parsed into test_contents above. The
            # only remaining no-text scenarios are: (a) a file we couldn't
            # parse + no text, or (b) an exemplar-only run that doesn't need
            # text. Reject (a) for standard/dimensional evals since they
            # have nothing to operate on.
            if not has_text and not has_files and standard_evals:
                raise ValueError(
                    "Standard evaluations need either text content or an "
                    "attached file. Connect a text-producing node (e.g. "
                    "Agent) to the Text Contents port, or a file-producing "
                    "node (e.g. PowerPoint, Excel) to the Files port."
                )
            if not has_text and not has_files and dimensional_evals:
                raise ValueError(
                    "Dimensional evaluations need either text content or an "
                    "attached file. Connect a text-producing node (e.g. "
                    "Agent) to the Text Contents port, or a file-producing "
                    "node (e.g. PowerPoint, Excel) to the Files port."
                )
            if not selected_evals:
                raise ValueError("At least one evaluation must be selected")

            # Normalize for downstream consumers \u2014 the legacy v1
            # backtest endpoint expects a list of strings; the
            # dimensional endpoint expects a single string. Use the
            # stripped value so any whitespace junk from the upstream
            # binding is gone for good.
            test_contents = test_contents_stripped

            if isinstance(test_contents, str):
                test_contents = [test_contents]

            total = len(standard_evals) + len(exemplar_evals) + len(dimensional_evals)
            await self.emit_progress(
                f"Preparing to run {total} evaluations"
                f" ({len(standard_evals)} standard, {len(exemplar_evals)} reference output,"
                f" {len(dimensional_evals)} dimensional)",
                data={"evaluation_count": total},
            )

            def _plan_text() -> str:
                plan: list[str] = []
                if standard_evals:
                    plan.append(
                        f"{len(standard_evals)} standard pass/fail or score "
                        "(batched, file text inlined): "
                        + ", ".join(self._eval_label(e) for e in standard_evals)
                    )
                if exemplar_evals:
                    plan.append(
                        f"{len(exemplar_evals)} reference output: "
                        + ", ".join(self._eval_label(e) for e in exemplar_evals)
                    )
                if dimensional_evals:
                    plan.append(
                        f"{len(dimensional_evals)} dimensional "
                        "(files attached natively): "
                        + ", ".join(self._eval_label(e) for e in dimensional_evals)
                    )
                return (
                    f"Evaluation plan — {total} evaluation(s) against "
                    f"{len(test_contents_stripped)} character(s) of content"
                    + (f" and {len(file_refs)} file(s)" if file_refs else "")
                    + ": "
                    + "; ".join(plan)
                    + "."
                )

            await self._narrate(_plan_text, status="Preparing evaluations")

            test_id = str(uuid.uuid4())
            run_results = {}

            # Phase 1: Run standard evals first (they produce the test_id
            # needed by exemplar evals for OpenSearch grouping).
            if standard_evals:
                try:
                    standard_batch = await self._run_standard_evals_batch(
                        orgid=orgid,
                        standard_evals=standard_evals,
                        test_contents=test_contents,
                        user_text=user_text,
                        file_refs=file_refs,
                        test_name=test_name,
                        user_token=user_token,
                        test_id=test_id,
                    )
                    test_id = standard_batch["test_id"]
                    run_results = standard_batch["run_results"]
                except Exception as e:
                    logger.error(
                        f"Standard evals batch failed: {e}",
                        exc_info=True,
                    )
                    capture_exception(e)
                    run_results = {
                        "name": test_name,
                        "eval_results": [
                            {
                                "eval_id": ec.get("option_id"),
                                "eval_name": ec.get(
                                    "option_display_name",
                                    ec.get("option_id"),
                                ),
                                "error_msg": str(e)[:200],
                                "input_results": [],
                            }
                            for ec in standard_evals
                        ],
                    }

            # Phase 2: Run exemplar + dimensional evals in parallel.
            # Both are independent of each other and only depend on
            # test_id from phase 1.
            # Each entry: (coroutine, eval_list, type_label)
            parallel_batches: list[tuple] = []

            if exemplar_evals:
                workflow_output = (
                    test_contents[0]
                    if isinstance(test_contents, list)
                    else test_contents
                )
                workflow_output_file = _extract_file_ref_from_node_outputs(file_outputs)
                parallel_batches.append(
                    (
                        self._run_exemplar_evals_batch(
                            orgid=orgid,
                            exemplar_evals=exemplar_evals,
                            workflow_output=workflow_output,
                            workflow_output_file=workflow_output_file,
                            test_id=test_id,
                            v2_type_by_id=v2_type_by_id,
                        ),
                        exemplar_evals,
                        "exemplar",
                    )
                )

            if dimensional_evals:
                if user_text:
                    dim_text_payload = user_text
                elif isinstance(test_contents, list):
                    dim_text_payload = "\n\n".join(
                        c for c in test_contents if isinstance(c, str)
                    )
                else:
                    dim_text_payload = test_contents or ""

                parallel_batches.append(
                    (
                        self._run_dimensional_evals_batch(
                            orgid=orgid,
                            dimensional_evals=dimensional_evals,
                            dim_text_payload=dim_text_payload,
                            user_text=user_text,
                            extracted_file_text=extracted_file_text,
                            file_refs=file_refs,
                        ),
                        dimensional_evals,
                        "dimensional",
                    )
                )

            if parallel_batches:
                coros = [b[0] for b in parallel_batches]
                parallel_results = await asyncio.gather(*coros, return_exceptions=True)

                # Merge results from parallel batches into run_results
                if not run_results:
                    run_results = {"name": test_name, "eval_results": []}
                existing_results = run_results.get("eval_results", [])

                for (_, eval_list, type_label), batch_result in zip(
                    parallel_batches, parallel_results
                ):
                    if isinstance(batch_result, Exception):
                        logger.error(
                            f"{type_label} eval batch failed: {batch_result}",
                            exc_info=(
                                type(batch_result),
                                batch_result,
                                batch_result.__traceback__,
                            ),
                        )
                        capture_exception(batch_result)
                        for ec in eval_list:
                            existing_results.append(
                                {
                                    "eval_id": ec.get("option_id"),
                                    "eval_name": ec.get(
                                        "option_display_name",
                                        ec.get("option_id"),
                                    ),
                                    "eval_type": ec.get("option_type", type_label),
                                    "error_msg": str(batch_result)[:200],
                                    "input_results": [],
                                }
                            )
                    else:
                        existing_results.extend(batch_result.get("eval_results", []))

                run_results["eval_results"] = existing_results

            # ENG-2649: a structured, pre-classified summary of this node's
            # eval results so the A2A gateway can fold every eval node's
            # results into one suite-level roll-up
            # (``api/a2a_gateway/eval_rollup.py``) without re-parsing the
            # markdown below.  Attached to ``NodeOutput.metadata`` (purely
            # additive; the markdown ``data`` payload is unchanged) and
            # narrated here so the CoT verdicts and the roll-up agree.
            raw_eval_results = [
                e
                for e in (run_results.get("eval_results") or [])
                if isinstance(e, dict)
            ]
            eval_summary = self._build_eval_summary(raw_eval_results)
            for raw_entry, entry in zip(raw_eval_results, eval_summary["evals"]):
                await self._narrate(
                    lambda entry=entry, raw_entry=raw_entry: self._cot_result_line(
                        entry, raw_entry
                    ),
                    status=f"Evaluated: {entry.get('eval_name') or entry.get('eval_id')}",
                )
            await self._narrate_summary(
                lambda: self._build_cot_summary(test_name, eval_summary)
            )

            # Format results
            await self.emit_progress("Formatting results")

            formatted_results = await self._format_test_results(run_results)

            # [ghi chú] mọi bài ERROR ⇒ khối FAILED (xem _cau_moi_bai_loi). ≥ 1 bài có kết quả ⇒ đi tiếp như gốc.
            dem = eval_summary.get("counts") or {}
            if dem.get("total") and dem.get("errors") == dem.get("total"):
                cau = _cau_moi_bai_loi(int(dem["total"]))
                logger.warning("evals: mọi bài ERROR (%s/%s) ⇒ khối FAILED", dem.get("errors"), dem.get("total"))
                bang = MarkdownDisplay(display_name="Evaluation Results", field_id="eval_results", required=True,
                                       value=formatted_results, info="Results from evaluation tests", show=True)
                await self.emit_error({"error": cau, "details": formatted_results[:4000]})
                await self.emit_end({"error": cau})
                return NodeOutput(data=None, node=self, error=cau, display_model=bang,
                                  metadata={"evaluation_count": len(selected_evals), "test_name": test_name, "eval_summary": eval_summary})

            # Create output
            output = NodeOutput(
                data=formatted_results,
                node=self,
                display_model=MarkdownDisplay(
                    display_name="Evaluation Results",
                    field_id="eval_results",
                    required=True,
                    value=formatted_results,
                    info="Results from evaluation tests",
                    show=True,
                ),
                metadata={
                    "evaluation_count": len(selected_evals),
                    "test_name": test_name,
                    "eval_summary": eval_summary,
                },
            )
            await self.emit_node_result(output)

            # Emit to output port
            if hasattr(self, "runtime") and self.runtime and output:
                for port in self.output_ports:
                    await self.runtime.emit_output(port.id, formatted_results)

            await self.emit_success(formatted_results)
            await self.emit_end()

            return output

        except Exception as e:
            error_msg = f"Evals node failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            capture_exception(e)
            # The failure itself rides ``node.node_failed``, which a
            # CoT-filtered consumer drops; say it in the CoT too -- but
            # ``node.thinking`` is rendered to the END USER, so never the raw
            # exception text (service URLs, upstream HTTP bodies, whatever
            # the eval service returned). The detail stays on ``emit_error``.
            await self._narrate(self._cot_abort_text(e), status="Evaluation failed")
            await self.emit_error({"error": error_msg, "details": str(e)})
            await self.emit_end({"error": error_msg})
            return NodeOutput(data=None, node=self, error=error_msg)
