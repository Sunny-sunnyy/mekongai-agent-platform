"""The pydantic-ai streaming loop shared by every agent node.

One implementation of the previously five-times-copied ``_stream_agent``:
walks ``agent.iter()``, forwards text/thinking deltas and tool
call/result events to the node's websocket emitters, harvests citations,
records tool calls for downstream evals, captures the final message
history for multi-turn continuation, and retries transient transport
errors with exponential backoff.

Retry classification uses the shared ``TRANSIENT_TRANSPORT_ERRORS`` union
plus ``is_anthropic_retryable`` (429/529 handling and exception-group
flattening) from ``nodes.utils`` — the union those helpers document as
built precisely for this consolidation.
"""

import asyncio
import json
import logging
from typing import Any, Callable, ClassVar, final
from uuid import uuid4

from pydantic_ai import (
    Agent,
    NativeToolCallPart,
    NativeToolReturnPart,
)
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from sentry_sdk import capture_exception

from ai.agentic.cache_observability import log_cache_usage, resolve_executor_model
from ai.agentic.context import primary_model_name
from nodes.utils import (
    TRANSIENT_TRANSPORT_ERRORS,
    anthropic_retry_exhausted_message,
    is_anthropic_retryable,
)
from workflow.clarification import extract_clarification_requested

logger = logging.getLogger(__name__)


def serialize_message_history(history: list[Any] | None) -> list[Any]:
    """Serialize a pydantic-ai ``list[ModelMessage]`` for transport.

    Used to surface the post-run message history in the node's
    ``NodeOutput.metadata`` so downstream consumers (the agents router,
    A2A gateway, platform-api chat endpoint) can read it from the
    workflow run's ``node_results`` and persist it for the next turn.

    Returns an empty list when ``history`` is None or capture failed.
    Uses ``ModelMessagesTypeAdapter.dump_python`` (mode='json') to
    preserve ``ThinkingPart`` blocks verbatim - Anthropic's API rejects
    modified thinking blocks, so the round-trip MUST be lossless.
    """
    if not history:
        return []
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter

        return ModelMessagesTypeAdapter.dump_python(history, mode="json")
    except Exception:
        # Last-ditch: fall back to pydantic model_dump per-message. May
        # lose Anthropic-specific block ordering on edge cases; logged so
        # we notice if it ever fires in production.
        out: list[Any] = []
        for msg in history:
            try:
                if hasattr(msg, "model_dump"):
                    out.append(msg.model_dump(mode="json"))
                else:
                    out.append(msg)
            except Exception:
                continue
        return out


class StreamingMixin:
    """Internal building block of ``AgentBaseNode`` — not for standalone use."""

    # Per-class retry tuning. The Agent/MCP family historically used 5/3.0,
    # the code-interpreter and Vercel families 3/2.0 — preserved per node.
    _stream_max_retries: ClassVar[int] = 5
    _stream_base_delay: ClassVar[float] = 3.0
    # When True, a retry attempt recompiles the agent first so MCP
    # connections are re-established. Set False where recompiling is wrong
    # (the deep-research rounds compile with per-round prompts).
    _recompile_on_retry: ClassVar[bool] = True
    # >0 buffers streamed text into chunks of this many characters before
    # emitting, so token-level deltas don't flood the frontend (the
    # code-interpreter and Vercel families use 80; 0 emits per-delta).
    _stream_text_buffer_size: ClassVar[int] = 0
    # User-visible dynamic_status labels for the text stream.
    _stream_status_start: ClassVar[str] = "Agent is starting response"
    _stream_status_delta: ClassVar[str] = "Streaming response"

    @final
    def _record_tool_call(
        self, tool_name: str, args_json: str, kind: str = "function"
    ) -> None:
        """Append a tool invocation to the per-run buffer.

        Surfaces tool usage in NodeOutput.metadata['tool_calls'] so the
        downstream tool_use eval can score it. The shape matches what the
        eval expects: {tool_name, tool_args, kind}."""
        if not hasattr(self, "_tool_call_log") or self._tool_call_log is None:
            return
        try:
            args = json.loads(args_json) if args_json else {}
        except (TypeError, ValueError):
            args = {"raw": args_json}
        self._tool_call_log.append(
            {
                "tool_name": tool_name,
                "tool_args": args,
                "kind": kind,
            }
        )

    @final
    def _log_run_cache_usage(self, result: Any) -> None:
        """Best-effort log of cached vs. uncached input tokens for a finished run.

        ``result.usage()`` carries pydantic-ai's normalised cache counters
        across providers; emitted under this node's ``node_sub_type`` so the
        metric distinguishes per-node-type traffic.
        """
        if result is None:
            return
        try:
            usage = result.usage()
        except Exception:
            return
        model_obj = self._validated_params.get("model")
        # Prefer the model that ACTUALLY served the run (the FallbackModel's
        # successful child), so a Bedrock failover isn't mislabeled as the
        # configured primary. Fall back to primary_model_name if unavailable.
        provider = resolve_executor_model(result) or (
            primary_model_name(model_obj) if model_obj is not None else None
        )
        log_cache_usage(
            usage,
            node_type=self.node_sub_type or self.node_type or "agent",
            provider=provider,
        )

    @final
    def _capture_final_message_history(self, final_result: Any) -> None:
        """Capture the full ModelMessage history so downstream consumers
        (agents router, A2A gateway, feedback turns, deep-research rounds)
        can continue the conversation. Wrapped in a try so a malformed
        result never crashes the run."""
        try:
            if hasattr(final_result, "all_messages"):
                self._final_message_history = final_result.all_messages()
        except Exception as e:
            logger.warning("Failed to capture final message_history: %s", e)

    @final
    async def _run_agent_blocking(self, agent: Agent) -> str:
        """Non-streaming fallback: one ``agent.run`` with the same
        usage-logging / history-capture / citation-harvest side effects as
        the streaming path."""
        run_result = await agent.run(**self._get_runtime_args())
        self._log_run_cache_usage(run_result)
        self._capture_final_message_history(run_result)
        # Citations (ENG-2731): harvest tool-retrieved sources.
        self._harvest_citations_from_run_result(run_result)
        from workflow.node.base.agent.structured_output import output_to_text  # [VÁ 8]

        return output_to_text(run_result.output)

    @final
    async def _stream_agent(
        self,
        agent: Agent,
        *,
        run_args: dict[str, Any] | None = None,
        recompiler: Callable[[], Any] | None = None,
        on_attempt: Callable[[], None] | None = None,
        max_retries: int | None = None,
        base_delay: float | None = None,
    ) -> str:
        """Run *agent* with live event streaming and transient-error retries.

        Args:
            agent: The compiled pydantic-ai agent to run.
            run_args: Fixed kwargs for ``agent.iter``. When ``None`` (the
                node-pipeline default), ``self._get_runtime_args()`` is
                re-read on every attempt so retries replay the same
                feedback/clarification turn correctly.
            recompiler: Awaitable factory used to rebuild the agent before a
                retry attempt (re-establishes MCP connections). ``None``
                falls back to ``self.compile`` when the class opts in via
                ``_recompile_on_retry``; pass-through of the original agent
                otherwise.
            on_attempt: Optional sync callback invoked at the start of every
                attempt — for callers whose deps accumulate per-attempt state
                that must be reset before a retry (Vercel Deploy's staged
                files).
            max_retries / base_delay: Optional overrides of the per-class
                retry tuning.
        """
        max_retries = self._stream_max_retries if max_retries is None else max_retries
        base_delay = self._stream_base_delay if base_delay is None else base_delay
        buffer_size = self._stream_text_buffer_size

        last_error: Exception | None = None

        for attempt in range(max_retries):
            self._reset_cot_buffers()
            if on_attempt is not None:
                on_attempt()
            try:
                if attempt > 0:
                    # Recompile on retry to re-establish MCP connections
                    if recompiler is not None:
                        agent = await recompiler()
                    elif self._recompile_on_retry:
                        agent = await self.compile()

                # Block ID tracking for streaming
                current_text_block_id = None
                current_thinking_block_id = None
                text_buffer = ""

                async def _flush_text_buffer():
                    nonlocal text_buffer
                    if text_buffer and current_text_block_id:
                        await self.emit_agent_message(
                            block_id=current_text_block_id,
                            content=text_buffer,
                            dynamic_status=self._stream_status_delta,
                        )
                        text_buffer = ""

                # NOTE : Do not remove the pass statements. Stream events keep
                # updating, they offer a way for both backward and forward
                # compatibility.
                async with agent.iter(
                    deps=self._deps,
                    **(run_args if run_args is not None else self._get_runtime_args()),
                ) as run:
                    async for node in run:
                        # Check for UserPromptNode
                        if Agent.is_user_prompt_node(node):
                            pass
                        # Check for ModelRequestNode
                        elif Agent.is_model_request_node(node):
                            async with node.stream(run.ctx) as request_stream:
                                async for event in request_stream:
                                    if isinstance(event, PartStartEvent):
                                        part = getattr(event, "part", None)
                                        if part:
                                            if isinstance(part, TextPart):
                                                # Generate new block ID for text stream
                                                await _flush_text_buffer()
                                                current_text_block_id = str(uuid4())
                                                if part.content:
                                                    await self.emit_agent_message(
                                                        block_id=current_text_block_id,
                                                        content=part.content,
                                                        dynamic_status=self._stream_status_start,
                                                    )

                                            elif isinstance(part, ThinkingPart):
                                                # Generate new block ID for thinking stream
                                                current_thinking_block_id = str(uuid4())
                                                if part.content:
                                                    await self.emit_thinking(
                                                        thought=part.content,
                                                        dynamic_status="Agent is thinking",
                                                        block_id=current_thinking_block_id,
                                                    )

                                            else:
                                                pass

                                    elif isinstance(event, PartDeltaEvent):
                                        delta = getattr(event, "delta", None)
                                        if delta:
                                            if isinstance(delta, TextPartDelta):
                                                # Use existing text block ID for deltas
                                                if (
                                                    current_text_block_id
                                                    and delta.content_delta
                                                ):
                                                    if buffer_size > 0:
                                                        text_buffer += (
                                                            delta.content_delta
                                                        )
                                                        if (
                                                            len(text_buffer)
                                                            >= buffer_size
                                                        ):
                                                            await _flush_text_buffer()
                                                    else:
                                                        await self.emit_agent_message(
                                                            block_id=current_text_block_id,
                                                            content=delta.content_delta,
                                                            dynamic_status=self._stream_status_delta,
                                                        )
                                            elif isinstance(delta, ThinkingPartDelta):
                                                # Use existing thinking block ID for deltas
                                                if (
                                                    current_thinking_block_id
                                                    and delta.content_delta
                                                ):
                                                    await self.emit_thinking(
                                                        thought=delta.content_delta,
                                                        dynamic_status="Streaming thoughts",
                                                        block_id=current_thinking_block_id,
                                                    )

                                            else:
                                                pass
                                    else:
                                        pass
                            await _flush_text_buffer()

                        # CallToolsNode results will be streamed as blocks
                        elif Agent.is_call_tools_node(node):
                            await _flush_text_buffer()
                            async with node.stream(run.ctx) as request_stream:
                                async for event in request_stream:
                                    if isinstance(event, FunctionToolCallEvent):
                                        part = getattr(event, "part", None)
                                        if part:
                                            # While it seems redundant, its easier to
                                            # identify what parts we are capturing and
                                            # customize them as needed when separated
                                            # in their own block.
                                            if isinstance(part, ToolCallPart):
                                                tool_name = part.tool_name
                                                tool_args = part.args_as_json_str()
                                                self._record_tool_call(
                                                    tool_name, tool_args, "function"
                                                )
                                                await self.emit_tool_call(
                                                    tool_name, tool_args
                                                )

                                            elif isinstance(part, NativeToolCallPart):
                                                tool_name = part.tool_name
                                                tool_args = part.args_as_json_str()
                                                self._record_tool_call(
                                                    tool_name, tool_args, "builtin"
                                                )
                                                await self.emit_tool_call(
                                                    tool_name, tool_args
                                                )

                                            else:
                                                pass

                                    elif isinstance(event, FunctionToolResultEvent):
                                        result = getattr(event, "result", None)
                                        if result:
                                            if isinstance(
                                                result,
                                                (ToolReturnPart, NativeToolReturnPart),
                                            ):
                                                tool_name = result.tool_name
                                                tool_result_str = (
                                                    result.model_response_str()
                                                )
                                                logger.debug(
                                                    "[WORKFLOWBUILDER:COMPOSIO:TOOL_EXEC] "
                                                    "user=%s sid=%s tool=%s status=success",
                                                    self._user_email or self._user_id,
                                                    self._session_id,
                                                    tool_name,
                                                )
                                                await self.emit_tool_result(
                                                    tool_name,
                                                    tool_result_str,
                                                )
                                                # Citations (ENG-2731): mint
                                                # citations from tool-retrieved
                                                # sources (web search, collection
                                                # search, MCP data tools).
                                                self._harvest_tool_citations(
                                                    tool_name, tool_result_str
                                                )

                                            elif isinstance(result, RetryPromptPart):
                                                tool_name = result.tool_name
                                                retry_str = (
                                                    f"Retry: {result.model_response()}"
                                                )
                                                logger.debug(
                                                    "[WORKFLOWBUILDER:COMPOSIO:TOOL_EXEC] "
                                                    "user=%s sid=%s tool=%s status=retry",
                                                    self._user_email or self._user_id,
                                                    self._session_id,
                                                    tool_name,
                                                )
                                                await self.emit_tool_result(
                                                    tool_name,
                                                    retry_str,
                                                )
                                            else:
                                                pass

                                    else:
                                        pass
                        else:
                            pass

                # Flush accumulated streaming blocks to OpenSearch
                await self._flush_all_thinking()
                await self._flush_all_agent_messages()

                final_result = run.result
                if final_result is None:
                    raise RuntimeError("Agent run completed without a result")

                self._log_run_cache_usage(final_result)
                from workflow.node.base.agent.structured_output import output_to_text  # [VÁ 8]

                final_output = (
                    output_to_text(final_result.output)
                    if hasattr(final_result, "output")
                    else str(final_result)
                )

                self._capture_final_message_history(final_result)

                return final_output

            except Exception as e:
                # Phase B: a clarification ask is a control-flow
                # signal, not a failure. It surfaces here wrapped in pydantic-ai's
                # TaskGroup ExceptionGroup and is non-retryable, so re-raise it
                # BEFORE capture_exception/logger.error -- otherwise the agent
                # node's intended pause gets reported to Sentry + logged at ERROR
                # on the streaming path. Mirrors execute().
                clar = extract_clarification_requested(e)
                if clar is not None:
                    raise clar from e
                if not is_anthropic_retryable(e, TRANSIENT_TRANSPORT_ERRORS):
                    # Non-retryable error, raise immediately
                    capture_exception(e)
                    logger.error(
                        f"Error during agent streaming: {str(e)}", exc_info=True
                    )
                    raise

                last_error = e
                attempts_remaining = max_retries - attempt - 1

                if attempts_remaining > 0:
                    # Calculate delay with exponential backoff
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"Retryable error during agent streaming (attempt {attempt + 1}/{max_retries}): {str(e)}. "
                        f"Retrying in {delay}s..."
                    )
                    await self.emit_progress(
                        f"Retryable error, retrying ({attempts_remaining} attempts remaining)..."
                    )
                    await asyncio.sleep(delay)
                else:
                    # No more retries — raise with a user-facing message so that
                    # execute()'s except block surfaces it via emit_error.
                    capture_exception(e)
                    raise RuntimeError(anthropic_retry_exhausted_message(e)) from e

        # Should not reach here, but handle edge case
        if last_error:
            raise last_error
