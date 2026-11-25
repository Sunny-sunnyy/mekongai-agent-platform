"""Pydantic-ai orchestrator agent driving the A2A ``chat`` skill.

Architecture (post canvas-revert)
---------------------------------

The A2A gateway exposes 22 static skills + one auto-derived typed
skill per allowlisted workflow.  Twenty-one of those skills are
deterministic Python handlers in
``api.a2a_gateway.executor.CapitolAgentExecutor._dispatch_table``
(``handshake`` / ``list_workflows`` / ``call_workflow`` / ...).

This module owns the **22nd skill** -- ``chat``.  When a caller hits
``chat`` the gateway builds an :class:`OrchestratorContext` from the
authenticated agent record + conversation state and calls
:func:`run_chat_turn`, which:

  1. Resolves the agent's effective workflow + peer allowlists.
  2. Builds the pydantic-ai ``Agent`` with the agent's configured
     model + the toolsets the agent's capability flags enable
     (``enable_workflow_runtime`` -> ``make_workflow_tools``,
     ``enable_a2a_outbound`` -> ``make_peer_a2a_tools``,
     ``enable_workflow_authoring`` -> ``make_workflow_builder_toolset``).
  3. Assembles the system prompt as Layer A (platform-owned
     constant) + Layer B (operator-overridable agent
     ``system_prompt`` field) + grounded context blocks (workflow
     allowlist + peer allowlist + active capability flags).
  4. Runs the agent against the user message + hydrated history,
     returning ``(assistant_text, all_messages)`` so the executor
     can persist the assistant turn to the conversation cache.

Critically: there is **no workflow row, no Temporal activity, no
canvas** on the chat path.  When the orchestrator's
``start_workflow`` function tool fires, THAT goes through the
existing ``TriggerWorkflowRunHandler`` (same path as the
``call_workflow`` skill) -- the workflow runtime is still the
workflow runtime for the work it's good at.  We're just no longer
wrapping a single LLM call in one.

System prompt: two layers + dynamic grounding
---------------------------------------------

::

    [Layer A:  PLATFORM_CORE_SYSTEM_PROMPT   <- this module, constant]

    [Layer B:  agent_record["system_prompt"] <- operator-editable on FE]

    [Grounded: ## Available workflows        <- resolve_effective_workflows]
              [## Available peer agents       <- peer_agent_allowlist]
              [## Capability flags            <- enable_* booleans]

Layer A is intentionally constant -- it carries wire-spec constants
(method names, error codes, schema version) operators MUST NOT
override or A2A interop breaks.  Layer B is the agent-specific tone
/ priors / domain hints the FE config panel lets ops edit.

Workflow configurability
------------------------

Workflows remain fully configurable from the A2A agent config panel.
The agent record stores ``workflow_allowlist`` (list of workflow ids)
+ ``workflow_allowlist_labels`` (label-based UNION); the orchestrator
reads both at chat-skill invocation time and:

  * Surfaces every effective workflow in the ``## Available
    workflows`` grounded-context block so the LLM only suggests /
    invokes things on the allowlist.
  * Constrains ``make_workflow_tools``' ``start_workflow`` / etc.
    function tools to refuse off-allowlist ids at the tool layer
    (defence in depth -- the LLM can't bypass the allowlist even
    if its grounding gets out of sync).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os  # [VÁ 10 chay_thu 06/11] cho CHAY_THU_R12_OPERATOR
import posixpath
import textwrap
import uuid
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
)
from urllib.parse import unquote, urlsplit

from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits
from sentry_sdk import capture_exception

from ai.agentic.common.chat_identity import AGENT_IDENTITY_RULES, AGENT_NAME
from ai.agentic.common.chat_safety import PLATFORM_SAFETY_GUARDRAILS
from ai.agentic.context import (
    CompactionEvent,
    build_capability_for_model,
    format_compaction_event,
)
from ai.agentic.models.fallback import (
    get_anthropic_model_with_bedrock_failover_for_agent_node,
)
from api.a2a_gateway.canvas_claims import (
    CANVAS_CLAIM_RETRY_PROMPT,
    append_unbacked_canvas_change_note,
    claims_unbacked_canvas_change,
    describe_canvas_mutation,
    on_canvas_surface,
    render_proposals_record,
    strip_unbacked_canvas_change_note,
    tells_user_to_apply_unbacked_change,
)
from api.a2a_gateway.card import (
    resolve_effective_workflows,
    user_workflows_enabled,
    workflow_slug,
)
from api.a2a_gateway.chat_telemetry import (
    operator_chat_maintenance_telemetry,
    operator_chat_turn_telemetry,
    record_turn_outcome,
)
from core import langfuse_identity
from core.config.ai import AnthropicModels
from core.langfuse_context import trace_context_of
from core.settings import settings
from db.session import async_session_factory
from nodes.utils import resolve_deprecated_model
from services.orchestrator.media_ingest import ingest_tool_media
from services.orchestrator.tools import (
    COLLECTIONS_TOOL_PREFIXES,
    CONTEXT_GRAPH_PROMPT_SECTION,
    CONTEXT_GRAPH_TOOL_PREFIX,
    CONTEXT_TOOLS_PROMPT_SECTION,
    DECISIONS_PROMPT_SECTION,
    LABELS_PROMPT_SECTION,
    MEMORY_PROMPT_SECTION,
    MOUNTED_TOOLS_PROMPT_SECTION,
    RUN_TOOLS_PROMPT_SECTION,
    SCHEDULES_PROMPT_SECTION,
    SKILLS_PROMPT_SECTION,
    TOOL_CATALOG_PROMPT_SECTION,
    make_agent_task_prompt_tools,
    make_artifact_tools,
    make_catalog_tools,
    make_collections_toolsets,
    make_context_graph_toolset,
    make_decision_tools,
    make_display_preference_tools,
    make_input_issue_tools,
    make_label_tools,
    make_mcp_allowlist_toolsets,
    make_memory_tools,
    make_mount_tools,
    make_org_mcp_config_tools,
    make_peer_a2a_tools,
    make_review_tools,
    make_run_tool_broker,
    make_run_tool_tools,
    make_schedule_tools,
    make_show_output_tools,
    make_show_workflow_tools,
    make_skill_tools,
    make_workflow_builder_toolset,
    make_workflow_tools,
    strip_builder_tool_prefix,
)
from services.orchestrator.tools.agent_config_tools import agent_config_task_prompt
from services.orchestrator.tools.file_delivery_rules import (
    SURFACE_PANEL,
    SURFACE_TABLE,
    no_retype_rule,
)
from services.orchestrator.tools.mounted_run_tools import RUN_TOOL_BROKER_ID
from services.orchestrator.tools.workflow_builder_toolset import (
    _WORKFLOW_BUILDER_TOOL_PREFIX,
)
from services.orchestrator.tools.workflow_tools import STOPPED_RUN_STATUSES

logger = logging.getLogger(__name__)


def _guard_unbacked_canvas_change_claim(
    assistant_text: str,
    *,
    surface_context: Any,
    canvas_mutation_count: int,
) -> str:
    """Append a caution note when a reply claims a canvas edit that no
    mutation frame backs.

    Appends rather than replaces: the claim regex is a text heuristic and
    can false-positive on an ordinary descriptive reply (e.g. "the node is
    connected to..."). Replacing the whole reply would silently destroy a
    correct answer in that case; appending only adds a redundant caveat.
    """
    if not claims_unbacked_canvas_change(
        assistant_text,
        surface_context=surface_context,
        canvas_mutation_count=canvas_mutation_count,
    ):
        return assistant_text
    logger.warning(
        "orchestrator: appended unbacked canvas-change caution -- no "
        "canvas_mutation frames this turn"
    )
    return append_unbacked_canvas_change_note(assistant_text)


# The retry may need a tool-call round plus the final text.
_CANVAS_CLAIM_RETRY_REQUEST_LIMIT = 4


async def _retry_unbacked_canvas_claim(
    agent: Any,
    result: Any,
    *,
    context: "OrchestratorContext",
    emit: Callable[[ChatStreamEvent], Awaitable[None]],
) -> Any:
    """Give the model one more round when its reply tells the user to apply
    a canvas edit that no tool call backs.

    Only the narrow "queued, click Apply changes" shape qualifies: the
    retry replaces the reply, so the broad claim heuristic (which the
    note fallback uses) must not drive it. The text already streamed to
    the client stays as it is; the client replaces it with the final
    ``assistant_text`` on ``done``. The retry runs without streaming, so
    a real proposal still reaches the client through the
    ``canvas_mutation`` side channel, while its tool-call frames do not.
    """
    if result is None or result.output is None:
        return result
    mutations = context.turn_canvas_mutations or []
    if not tells_user_to_apply_unbacked_change(
        str(result.output),
        surface_context=context.surface_context,
        canvas_mutation_count=len(mutations),
    ):
        return result
    logger.warning(
        "orchestrator: reply claims a canvas edit with no canvas_mutation "
        "frame; retrying once for agent=%s context=%s",
        context.agent_id,
        context.context_id,
    )
    await emit(ChatStreamEvent(type="status", text="Checking the proposed edit…"))
    try:
        return await agent.run(
            CANVAS_CLAIM_RETRY_PROMPT,
            message_history=result.all_messages(),
            usage_limits=UsageLimits(request_limit=_CANVAS_CLAIM_RETRY_REQUEST_LIMIT),
        )
    except Exception as exc:  # noqa: BLE001 - the first reply still stands
        capture_exception(exc)
        logger.warning(
            "orchestrator: canvas-claim retry failed, keeping the first reply: %s",
            exc,
        )
        return result


# ===========================================================================
# OrchestratorContext
# ===========================================================================


@dataclass
class OrchestratorContext:
    """Per-invocation context passed to every orchestrator toolset factory.

    Duck-types as a ``BaseNode``-shaped object on the underscore-prefixed
    attributes (``_orgid`` / ``_user_email`` / ``_user_token`` /
    ``_session_id`` / ``_validated_params`` / ``_mcp_timeout``) so the
    factory code lifted out of the (deleted) ``A2AAgentNode`` runs
    unchanged.  Anything the factories don't reach for sits as a real
    field on the dataclass for readability.
    """

    org_id: str
    agent_id: str
    agent_record: Dict[str, Any]
    user_email: Optional[str]
    user_token: Optional[str]
    session_id: str
    context_id: str
    # ENG-2339: bearer-minting user's resolved ``user.id``.  When the
    # gateway builds an ``OrchestratorContext`` from an A2A bearer it
    # populates this with the principal returned by the platform-api's
    # ``resolve-bearer`` endpoint (or an email -> user_id lookup for
    # legacy bearers).  The toolset factories' ``_synthetic_user`` helper
    # reads ``getattr(node, "_user_id", "") or node.node_id`` to set
    # ``current_user.user_id``; with ``None`` here that fall-through
    # used to hand the literal string ``"a2a_orchestrator"`` to the
    # workflow runtime, which then crashed on the workflow_run.run_by_id
    # FK constraint (the root cause of ENG-2339).
    user_id: Optional[str] = None
    # Set only by the in-product chat endpoints (api/v1/routers/agents.py).
    # When True the workflow tools enforce the requesting user's own
    # workflow_access role (the same bar Composer applies) on top of the
    # agent allowlist. External A2A bearers and canvas AgentNodes leave it
    # False and keep their allowlist-only posture.
    enforce_user_workflow_access: bool = False
    # Set only by the operator chat's endpoints (api/v1/routers/agents.py).
    # Behaviour that is the operator chat's alone -- the prompt on every
    # turn, node-scoped canvas edits, context-labelled history -- keys on
    # this, so external A2A chat callers keep what they had.
    operator_chat: bool = False
    # ENG-5473: the per-call id the entry point hands back to its caller for
    # this turn -- the v1 route's ``run_id``, the A2A gateway's task id --
    # stamped on the turn's Langfuse trace as ``chat_turn_id`` so a trace
    # can be matched to a turn in our logs and the client's. Distinct from
    # ``session_id`` above, which the v1 route accepts from the caller and
    # is therefore per-turn only by convention. ``None`` when the caller
    # (canvas path, tests) has no per-call id; the metadata key is then
    # omitted.
    turn_id: Optional[str] = None
    # ENG-5473: Langfuse ``TraceContext`` of the turn span currently open for
    # this context, installed by the streaming producer and cleared with the
    # other turn-scoped fields.  The turn-end history fold (scheduled from
    # the pump after that span has closed) nests under it so the fold's
    # spend lands in the turn's trace, under the turn's trace name, instead
    # of renaming the request's shared trace.  ``None`` when Langfuse is off.
    turn_trace_context: Optional[Dict[str, str]] = None
    # Wire 1.0.23 (additive): what the user was looking at when they sent
    # this turn -- a validated ``SurfaceContext`` wire model instance
    # (``api.a2a_gateway.wire_models``: artifact viewer / artifact gallery
    # / workflow canvas with optional focused node / skill doc), or
    # ``None``. ``run_chat_turn_stream`` injects a grounded system-prompt
    # block for it when ``A2A_SURFACE_CONTEXT_GROUNDING_ENABLED`` is on;
    # grounding is fail-open (unresolvable / foreign-org ids log and skip
    # the block, never the turn).
    surface_context: Optional[Any] = None
    # Wire 1.0.23 (additive): per-conversation chat model override,
    # client-resent on every turn of the conversation. Honored when
    # the id resolves (deprecation remap applies) into
    # ``ALLOWED_CHAT_MODEL_OVERRIDES``; invalid / retired ids fall back to
    # the agent-record model plus a ``status`` frame. NEVER persisted to
    # the agent record.
    model_override: Optional[str] = None
    # The user's answer to a question a previous turn asked with
    # ``ask_user_decision``: a validated ``DecisionResponse`` (the decision
    # id plus the option they picked, or the free text they typed instead),
    # or ``None`` on every ordinary turn. Grounded into the prompt by
    # ``run_chat_turn_stream`` so the model reads the choice as a fact
    # rather than re-deriving it from the message text. Fail-open: a
    # malformed value logs and is dropped, never failing the turn.
    decision_response: Optional[Any] = None
    # Composer-tagged Data Collections, ACL-filtered by platform-api.
    collection_references: List[Any] = field(default_factory=list)
    mcp_timeout: float = 60.0
    node_id: str = "a2a_orchestrator"
    # ENG-2486 native Entra OBO: the sealed OboSession (downstream-scoped
    # Token C + refresh token) acquired once per chat turn by the gateway
    # executor's ``_acquire_obo_session_or_fallback`` and stashed here so the
    # run-initiating tool closures (``start_workflow`` / ``rerun_from_node``)
    # can plumb it into ``WorkflowInput.obo_session``.  ``None`` for every
    # non-Entra caller (cap_a2a bearers, canvas AgentNode path, tests) -- the
    # tool closures read it via ``getattr(node, "obo_session", None)``, so a
    # missing / None value is a safe no-op that keeps them on the bearer path.
    obo_session: Optional[Any] = None
    # The raw user assertion (Token B) + its type, carried for parity with the
    # ``_handle_call_workflow`` path.  Never enters Temporal state -- only the
    # sealed ``obo_session`` derived from it travels through ``WorkflowInput``.
    user_assertion: Optional[str] = None
    user_assertion_type: str = "cap_a2a"
    # ENG-3385 (sweep follow-up): live side-channel from tool execution to
    # the chat SSE.  ``run_chat_turn_stream`` installs its output queue
    # here before the agent loop starts; ``start_workflow`` (via
    # ``notify_run_started``) pushes a ``run_started`` frame onto it THE
    # MOMENT the run exists -- even while ``wait_for_completion=true``
    # keeps the tool blocked for minutes.  ``None`` outside a live turn
    # (AgentNode canvas path, tests): the notifier is then a no-op.
    turn_event_queue: Optional[Any] = None
    # Whether this caller will actually receive a ``show_output`` frame (or
    # the blocking response field that carries the same batch). Streaming
    # chat and the A2A executor do. ``POST /agents/{id}/runs`` returns only
    # ``assistant_reply`` and sets this False, so a review document stays a
    # prose link instead of a table the response drops.
    delivers_show_output: bool = True
    # Set for one chat turn. True when a download table or the events-only
    # panel will deliver the document under review, so tool results must
    # not also carry ``review_url``. False on the turn the checkpoint
    # opens (no record yet) and for callers that receive no frames.
    withhold_review_link: bool = False
    # Identities of review documents already sent as a ``show_output``
    # frame this turn (bucket + key, not the reminted URL). A later park
    # of the same object does not emit again; a new object does.
    review_tables_emitted: Optional[set] = None
    # Tool-call / tool-return parts captured live off the stream by
    # ``run_chat_turn_stream`` (same lifetime as ``turn_event_queue``).
    # Lets endings that never see a ``final`` frame with messages --
    # the sync wall-clock-budget truncation in ``run_chat_turn`` --
    # still hand the already-executed tools (consent grants, run
    # observations) to tier-0 turn-end fact extraction.
    turn_tool_parts: Optional[List[Any]] = None
    # ``canvas_mutation`` payloads this turn actually pushed, in order
    # (same lifetime as ``turn_event_queue``). What backs a reply's "queued"
    # claim, and what the history records for the turn.
    turn_canvas_mutations: Optional[List[Dict[str, Any]]] = None
    # ENG-4104 (item 132): this turn's VERBATIM user message, installed by
    # ``run_chat_turn_stream`` alongside the other turn-scoped handles above.
    # ``submit_input_decision`` reads consent from THIS rather than from a
    # boolean the model chose, because a misrelayed answer would authorise a
    # run the user had just refused. Turn-scoped and never persisted; ``None``
    # outside a live turn (canvas path, tests), where consent then cannot be
    # confirmed and the decision stays blocked by design.
    current_user_message: Optional[str] = None
    # The agent's last reply before this turn, installed alongside
    # ``current_user_message``. A "yes" answers it, so the selection guard
    # reads the nodes it proposed from here. ``None`` outside a live turn.
    previous_assistant_message: Optional[str] = None
    # Turn-scoped E2B sandbox handle for the artifact-iteration tools
    # (§2.6): ``load_run_context`` creates it lazily on first use so N
    # follow-up artifact tool calls in the same turn share one staged
    # filesystem; ``run_chat_turn_stream``'s ``finally`` kills it.  Never
    # persisted across turns -- a follow-up turn re-stages (cheap,
    # S3-local).
    turn_sandbox: Optional[Any] = None
    # Turn-scoped staging bookkeeping for the sandbox above: cumulative
    # staged bytes (the A2A_EDIT_STAGE_MAX_BYTES budget is per TURN, not
    # per call) and the sandbox-path -> {file_id, size_bytes, run_id}
    # map used to keep a later staging call from silently overwriting an
    # earlier call's file.  Each entry's ``run_id`` is the staged file's
    # SOURCE run -- the ground truth for the cross-run isolation guard
    # (one run's files never coexist with another's without explicit,
    # user-directed consent).  Both reset with the sandbox in the turn's
    # ``finally``.
    turn_staged_bytes: int = 0
    turn_staged_paths: Optional[Dict[str, Dict[str, Any]]] = None
    # ``file_id -> staged-file record`` for files ``load_run_context`` /
    # ``edit_artifact`` put into the turn sandbox, so a later edit in the
    # same turn reuses the staged copy instead of re-downloading from S3.
    # Records carry ``run_id`` provenance (cross-run isolation).  Same
    # lifecycle as ``turn_sandbox`` (dies with the turn).
    turn_staged_files: Optional[Dict[str, Any]] = None
    # §4.3 edit budget: ``edit_artifact`` invocations made this turn.
    turn_edit_calls: int = 0
    # Dynamic run-toolset mounting: the turn's ``MountRegistry`` (live,
    # entered MCP connections keyed by source run) and the honor-system
    # per-turn consent grants for mounted non-read_only tools. Both die
    # with the turn (``close_turn_mounts`` in the stream ``finally``);
    # mount DESCRIPTORS persist per conversation in Redis and are
    # rebuilt -- rebound to the current user's credentials -- by
    # ``premount_persisted_mounts`` at the next turn's start.
    turn_mounted_tools: Optional[Any] = None
    turn_mount_consents: Optional[Any] = None

    def notify_run_started(
        self,
        run_id: str,
        tool_name: str = "",
        sub_agents: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Push a ``run_started`` frame onto the live chat stream.

        Called from inside run-initiating tools at run CREATION.  Fire-
        and-forget by design: no queue installed (canvas path, blocking
        drain without a stream, tests) or a full queue must never break
        the run itself.

        ``sub_agents`` (ENG-3234, additive): the upfront node roster the
        tool already built for its own result -- when supplied, the frame
        carries ``sub_agents`` + ``total_sub_agents`` so a chat-driven
        consumer (OneEdge) can render every sub-agent "To Be Started"
        from this very first frame, without joining against the workflow
        event stream.
        """
        q = self.turn_event_queue
        if q is None or not run_id:
            return
        payload: Dict[str, Any] = {"run_id": str(run_id)}
        if sub_agents:
            payload["sub_agents"] = sub_agents
            payload["total_sub_agents"] = len(sub_agents)
        try:
            q.put_nowait(
                ChatStreamEvent(
                    type="run_started",
                    tool_name=tool_name or None,
                    payload=payload,
                )
            )
        except Exception:  # noqa: BLE001 - side-channel is best-effort
            logger.warning("notify_run_started: could not enqueue run %s", run_id)

    def notify_status(self, text: str, tool_name: Optional[str] = None) -> None:
        """Push a live ``status`` frame onto the chat stream mid-tool (§2.9).

        The generalization of the ENG-3385 ``notify_run_started`` side
        channel: long-running tools (``create_artifact``, the artifact
        edit/staging tools) call this at phase boundaries and from
        sandbox stdout/stderr relays so the sidebar shows progress
        instead of 60-180 s of silence after the ``tool_call`` frame.
        ``status`` is an existing chat_event type both clients already
        render (the product ActivityFeed shows the latest status text
        verbatim as its header line) -- no wire or client change.

        Fire-and-forget by design: no queue installed (canvas path,
        blocking drain without a stream, tests) or a full queue must
        never break the tool -- statuses are progress, not state.
        """
        q = self.turn_event_queue
        if q is None or not text:
            return
        try:
            q.put_nowait(
                ChatStreamEvent(
                    type="status",
                    text=text,
                    tool_name=tool_name or None,
                )
            )
        except Exception:  # noqa: BLE001 - side-channel is best-effort
            logger.warning("notify_status: could not enqueue status frame")

    def notify_input_issue(
        self,
        text: str,
        *,
        issue_kind: str = "wrong_content",
        filename: Optional[str] = None,
        field_name: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> None:
        """Push an ``input_issue`` frame onto the live chat stream (ENG-4104).

        This is the main-chat / OneEdge LHS channel for "the uploaded
        file is wrong or partial". Distinct from ``status`` (activity
        feed) and from CoT ``thinking``. Fire-and-forget.
        """
        q = self.turn_event_queue
        if q is None or not text:
            return
        payload: Dict[str, Any] = {
            "issue_kind": issue_kind,
            "display": True,
        }
        if filename:
            payload["filename"] = filename
        if field_name:
            payload["field_name"] = field_name
        try:
            q.put_nowait(
                ChatStreamEvent(
                    type="input_issue",
                    text=text,
                    tool_name=tool_name or "report_input_issue",
                    payload=payload,
                )
            )
        except Exception:  # noqa: BLE001 - side-channel is best-effort
            logger.warning("notify_input_issue: could not enqueue frame")

    def notify_decision(
        self,
        *,
        decision_id: str,
        question: str,
        options: List[str],
        allow_free_text: bool = True,
        tool_name: Optional[str] = None,
    ) -> None:
        """Push a ``decision`` frame onto the live chat stream.

        The channel for a structured clarifying question: the client
        renders the options rather than leaving the user to guess the
        answer format from prose. Nothing about it pauses the turn --
        ``ask_user_decision`` returns immediately, the turn ends, and the
        answer arrives on the next turn's ``metadata.decision_response``.

        Fire-and-forget like its siblings: with no queue installed (canvas
        path, tests) the question survives as the model's own prose, which
        is exactly the behaviour that existed before this frame.
        """
        q = self.turn_event_queue
        if q is None or not question or not options:
            return
        try:
            q.put_nowait(
                ChatStreamEvent(
                    type="decision",
                    text=question,
                    tool_name=tool_name or "ask_user_decision",
                    payload={
                        "decision_id": decision_id,
                        "question": question,
                        "options": list(options),
                        "allow_free_text": bool(allow_free_text),
                        "display": True,
                    },
                )
            )
        except Exception:  # noqa: BLE001 - side-channel is best-effort
            logger.warning("notify_decision: could not enqueue frame")

    def notify_canvas_mutation(self, mutation: Dict[str, Any]) -> None:
        """Push a ``canvas_mutation`` frame onto the live chat stream.

        Called from canvas-editing tools when the agent proposes a
        structural change to the workflow canvas (add / remove /
        configure node, connect / disconnect).  Fire-and-forget by
        design: the frontend buffers events during the turn, renders a
        confirm card, and applies them on user approval.
        """
        q = self.turn_event_queue
        if q is None:
            return
        try:
            q.put_nowait(
                ChatStreamEvent(
                    type="canvas_mutation",
                    payload=mutation,
                )
            )
        except Exception:  # noqa: BLE001 - side-channel is best-effort
            logger.warning("notify_canvas_mutation: could not enqueue mutation")
            return
        if self.turn_canvas_mutations is not None:
            self.turn_canvas_mutations.append(mutation)

    def notify_canvas_run_workflow(self, payload: Dict[str, Any]) -> None:
        """Push a ``canvas_run_workflow`` frame onto the live chat stream.

        Tells the frontend to kick off a canvas run for the current
        workflow.  The run is dispatched client-side; the tool returns
        immediately after queuing the request.
        """
        q = self.turn_event_queue
        if q is None:
            return
        try:
            q.put_nowait(
                ChatStreamEvent(
                    type="canvas_run_workflow",
                    payload=payload,
                )
            )
        except Exception:  # noqa: BLE001 - side-channel is best-effort
            logger.warning("notify_canvas_run_workflow: could not enqueue frame")

    # --- BaseNode-shaped duck-typing ---------------------------------
    # The toolset factories were written against an AgentNode-shaped
    # ``node`` and read ``_orgid`` / ``_user_token`` / etc.  Expose the
    # same attribute names on this dataclass via properties so we don't
    # have to rewrite ~600 lines of tool code.

    @property
    def _orgid(self) -> str:
        return self.org_id

    @property
    def _user_email(self) -> Optional[str]:
        return self.user_email

    @property
    def _user_token(self) -> Optional[str]:
        return self.user_token

    @property
    def _user_id(self) -> Optional[str]:
        # Tools fall back to ``getattr(node, "_user_id", "") or node_id``.
        # Returning the bearer-minting user here (populated by the gateway
        # from the resolve-bearer response) is what makes the chat ->
        # call_workflow path produce a workflow_run row whose
        # ``run_by_id`` resolves cleanly against the user table.
        return self.user_id

    @property
    def _session_id(self) -> str:
        return self.session_id

    @property
    def _mcp_timeout(self) -> float:
        return self.mcp_timeout

    @property
    def chat_tool_wait_cap_s(self) -> Optional[float]:
        """Chat-surface cap on synchronous ``wait_for_completion`` waits.

        Read by ``make_workflow_tools``' ``start_workflow`` closure.
        Only the A2A chat orchestrator exposes this attribute -- canvas
        AgentNodes don't, so their waits are uncapped (ENG-3385 sweep
        follow-up: a 600s tool block inside an interactive chat turn is
        an anti-pattern; the SSE proxies kill the stream long before).
        """
        cap = settings.A2A_CHAT_TOOL_WAIT_CAP_S
        return cap if cap and cap > 0 else None

    @property
    def _validated_params(self) -> Dict[str, Any]:
        """Mirror the param dict an AgentNode would expose.

        The factories pull ``workflow_allowlist`` /
        ``workflow_allowlist_labels`` / ``peer_agent_allowlist`` from
        here; lift them off ``agent_record`` so the FE-configured
        allowlists flow into the tools without a separate code path.
        """
        return {
            "workflow_allowlist": self.agent_record.get("workflow_allowlist") or [],
            "workflow_allowlist_labels": self.agent_record.get(
                "workflow_allowlist_labels"
            )
            or [],
            "peer_agent_allowlist": self.agent_record.get("peer_agent_allowlist") or [],
        }


# ===========================================================================
# System prompt assembly (Layer A + Layer B + grounded context)
# ===========================================================================


# Layer A's A2A-integrator content: the wire reference external callers
# write their own requests from.  The operator chat's caller is a person
# in the product, so its Layer A goes without it.
_A2A_INTRO = """\
You are reachable over the A2A protocol (Capitol wire spec v1.0.5, fully A2A v1.0
compliant) at `https://<host>/a2a/{org_id}/{agent_id}`. Your callers --
other A2A agents, automation pipelines, and humans chatting through the
gateway -- talk to you by sending JSON-RPC `message/send` (or `SendMessage`
v1.0) calls multiplexed on `skill_id`.

"""
_A2A_SKILL_SURFACE_SECTION = """
## How you fit into the 22-skill surface

Your AgentCard advertises 22 static skills plus one auto-derived typed
skill per allowlisted workflow (`run__<workflow_id>`). Most of those
skills are NOT you. They are deterministic operations handled directly by
the gateway: discovery (`list_workflows`, `get_workflow_details`,
`get_workflow_definition`, `get_workflow_versions`, `suggest_workflow`),
execution (`call_workflow`, `rerun_from_node`), lifecycle (`pause_workflow`,
`resume_workflow`, `stop_workflow`), monitoring (`get_workflow_status`,
`get_workflow_output`, `get_workflow_events`,
`get_workflow_chain_of_thought`, `subscribe_workflow_events`,
`list_workflow_runs`, `get_workflow_stats`), and files (`upload_file`,
`request_upload_url`, `download_file`).

You are exactly one skill: `chat`. When a caller hits `chat`, the gateway
spins you up as a pydantic-ai agent with this system prompt, your
configured toolset, and the `## Grounded context` blocks below (injected at
session start).
"""
_A2A_WIRE_REFERENCE_SECTIONS = """
## Critical wire constants (do not paraphrase)

- Wire schema: `1.0.5`, camelCase ProtoJSON, `TASK_STATE_*` enums. No
  `kind` discriminator on parts/events; no `final` boolean on
  `TaskStatusUpdateEvent` (terminal = `status.state` in {`COMPLETED`,
  `FAILED`, `CANCELED`, `REJECTED`} + SSE closure).
- Method names: v1.0 PascalCase canonical (`SendMessage`,
  `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`,
  `SubscribeToTask`). Gateway accepts v0.3 (`message/send`, etc.) and
  pre-v0.3 (`tasks/send`, `tasks/sendSubscribe`) aliases.
- Skill multiplexing: `params.message.parts[0].data.skill_id`.
- Auth: `Authorization: Bearer cap_a2a_<48-char-base64url>`, bound to one
  `(org_id, agent_id)`. Optional `X-User-Token` propagates the end-user
  identity into the audit log.
- Caps: 50 MB per file on EVERY path -- presigned PUT, `upload_file`
  inline base64, `call_workflow.artifacts[]` inline, and chat FilePart
  attachments (all decoded). Files > 50 MB are not supported on any
  path. 50 concurrent in-flight runs per org (`-32429`). 24h idempotency
  window. 15min presigned-PUT TTL (max 1h). 24h `artifact_id` lookup TTL.
- Errors carry `google.rpc.ErrorInfo` in `error.data[0]` with
  UPPER_SNAKE_CASE `reason`, `domain: "a2a-protocol.org"`, and `metadata`
  carrying `retryable` / `category` / `actionable_hint`.

## Error catalog (ordered by frequency you will actually surface)

- `-32008 WorkflowInputValidationFailed` -- caller passed bad inputs.
  Name the offending field by its human `display_name` (NOT its
  `field_id` / id when replying to a user); recommend
  `get_workflow_details`. Not retryable.
- `-32429 ConcurrentRunCapExceeded` -- org hit its concurrent-run cap.
  Recommend `list_workflow_runs(status=running)` or retry after backoff.
  Retryable.
- `-32603 InternalError` -- transient. Recommend retry with backoff
  <= 3 attempts using the same `idempotency_key`. Retryable.
- `-32002 WorkflowNotFound` -- workflow id not found, not exposed via A2A,
  or not in your allowlist. `-32003 RunNotFound` -- run id
  mismatch or tenancy issue. Not retryable; recommend `list_workflows` /
  `list_workflow_runs` to rediscover (suggest the closest allowlisted
  workflow when the id simply isn't allowlisted).
- `-32004 PermissionDenied` -- the bearer's scope doesn't permit this
  operation (e.g. cross-org access). Not retryable.
- `-32005 IdempotencyConflict` -- same key + different payload.
  Recommend either generating a fresh key or reusing the prior payload
  exactly.
- `-32600 / -32601 / -32602 InvalidRequest / MethodNotFound /
  InvalidParams` -- malformed call. Name the field that failed
  validation by its human `display_name` (NOT its id) when replying to
  a user.

## Chain-of-thought emission rules

The four CoT event types are `node.thinking`, `node.agent_message`,
`node.tool_call`, `node.tool_result`. Streamable events carry
`mode` in {`block`, `textstream`} and a `block_id`; a finalised
`mode: "block"` event supersedes prior textstream deltas sharing the
same `block_id`. `event_id` is a stable `uuid5` over
`(run_id, sequence, raw_type)` -- safe to dedupe on. `sequence` is
monotonic per run; reconnect with `since_sequence: <last>+1`. Live tail:
`subscribe_workflow_events`. Post-hoc batch:
`get_workflow_chain_of_thought` (strict 4-type filter) or
`get_workflow_events` (recent lifecycle + CoT window, with `types`
filter; returns the most-recent events, not the entire log).
"""
_A2A_REFERENCE_IMPLEMENTATIONS_SECTION = """
## Reference implementations

- Behavioural reference: A2Actrl
  <https://github.com/Faction-V/A2Actrl> -- `pip install -e .` gives the
  caller a `capitol-a2a` CLI + Python `A2AClient`. If your guidance and
  the harness disagree, the harness wins.
- Wire reference: the gateway's OpenAPI/JSON-RPC schema at
  `https://<host>/openapi.json` (for machine integrators).
"""
# Lines in the shared sections that lean on the content above, and what
# the operator chat reads instead.
_A2A_ADVISE_MODE = (
    "produce the exact JSON-RPC payload the caller would\n"
    "   send themselves, with `skill_id`, `idempotency_key`, and any required\n"
    "   `artifacts[]`/inputs already populated."
)
_OPERATOR_ADVISE_MODE = (
    "tell the user what's missing or switched off and\n   what they can do about it."
)
_A2A_ERROR_CODES = (
    "Map JSON-RPC codes to the AgentCard's error\n"
    "   catalog rather than restating raw codes."
)
_OPERATOR_ERROR_CODES = "Don't restate raw error codes."
_A2A_SURFACE = "outside the 22-skill surface,"
_OPERATOR_SURFACE = "your tools,"


# Layer A guidance on putting workflows in front of the user.  The operator
# chat renders ``show_workflows`` cards and writes around them; A2A/OneEdge
# callers keep the one-card / link-everything guidance they had before, since
# no A2A consumer is known to render the multi-card ``workflow_ids`` field.
_WORKFLOW_SHOWING_OPERATOR = """\
          **Offering one to three workflows means showing them as
          cards.** Whenever your reply offers the user one, two, or three
          specific workflows -- recommending one, asking the user about
          one, answering "do I have a workflow for X?", offering a short
          set of candidates, or presenting workflows you just built or
          published -- call `show_workflows` once with all of them, in
          that same turn, before you reply, even when you already know
          their ids from the grounded context above. That call is what
          puts the workflow cards in front of the user, grouped below
          your reply. Do not call `describe_workflow` or
          `get_workflow_definition` in its place, and do not call
          `show_workflows` again on a later turn just because you are
          reading inputs.
          The cards already show each workflow's name and description,
          so write your reply around them: a short lead-in ("Here are
          the three workflows I built:"), then anything the user has to
          decide. Do not list the workflows again, do not repeat their
          names or descriptions, do not write them as links -- even when
          a builder result hands you a `workflow_link` or `message` to
          echo -- and do not recite the fields a describe returns.

          A later "run it" is not a new build. When the user tells you
          to run a workflow this conversation already built or showed,
          start that one with `start_workflow` -- it is the run path for
          a published workflow, including one you just built.
          Do not assemble or publish another, and do not say again that
          it is built. Say that it is running, in one line. Do not call
          `show_workflows` for it again: the run card is already there.
          That card is also what reports progress -- do not poll
          `get_workflow_run_status` in a loop to narrate each step.

          **More than three workflows, or a passing mention, means
          links.** When your reply names more than three workflows --
          listing everything the user has, or comparing many -- do not
          call `show_workflows` or `describe_workflow` for them. Write
          them as a markdown list, one workflow per item: a link whose
          text is its display name and whose target is
          `/workflow/<UUID>`, then a short description of what it does.
          Use the UUID exactly as printed in the grounded context or in
          a `list_workflows` result (never the slug, never a derived id):
          `- [Daily Digest](/workflow/0f3c2a1e-...) -- summarizes
          yesterday's activity`. When you mention a workflow in passing
          inside a sentence, without offering it, write it as the same
          kind of link in the sentence. Never name a workflow by bare
          name alone when the user might want to open it; the card or
          the link is what lets them get there.
"""

_WORKFLOW_SHOWING_A2A = """\
          **Naming one workflow means showing it.** Whenever your reply
          refers to ONE specific workflow -- recommending it, asking the
          user about it, or answering "do I have a workflow for X?" --
          call `show_workflows` with just that one id in that same turn, before you
          reply, even when you already know its id from the grounded
          context above. That call is what puts the workflow card in
          front of the user instead of only its name. Do not call
          `describe_workflow` or `get_workflow_definition` in its place,
          and do not call `show_workflows` again on a later turn just
          because you are reading inputs. Do not recite the fields a
          describe returns -- the user can see the workflow; your reply
          is what you have to say about it.

          A later "run it" is not a new build. When the user tells you
          to run a workflow this conversation already built or showed,
          start that one with `start_workflow` -- it is the run path for
          a published workflow, including one you just built.
          Do not assemble or publish another, and do not say again that
          it is built. Say that it is running, in one line. Do not call
          `show_workflows` for it again, and do not write it as a
          markdown link: either one puts the workflow card back in front
          of them, and the run card is already there. That card is also
          what reports progress -- do not poll
          `get_workflow_run_status` in a loop to narrate each step.

          **Naming several workflows means linking each one.** When your
          reply has NOT narrowed to one workflow -- you are listing the
          candidates, comparing a few, or answering "what workflows do I
          have?" -- do not call `show_workflows` or `describe_workflow`
          for each of them.
          Instead, write every workflow you name as a markdown link whose
          text is its display name and whose target is `/workflow/<UUID>`,
          using the UUID exactly as printed in the grounded context or in
          a `list_workflows` result (never the slug, never a derived id):
          `[Daily Digest](/workflow/0f3c2a1e-...)`. The chat turns a short
          list of these links into clickable workflow cards and a longer
          one into plain clickable links naming the workflow -- so link
          them all, every time, and let the count decide how they render.
          In this several-workflows case, never mention a workflow by
          bare name alone when the user might want to open it; the link
          is what lets them get there.
"""


# Layer A -- platform-owned constant.  Encodes the A2A wire contract
# (method names, error codes, schema version, file size caps) every
# Capitol orchestrator must respect for interop.  Operators MUST NOT
# override -- doing so via Layer B is a no-op because we concatenate
# Layer A FIRST.  ``a2a`` adds the A2A-integrator content above.
def _layer_a(*, a2a: bool) -> str:
    return (
        f"""\
You are {AGENT_NAME}. That is your name -- use it, and only it, whenever you
introduce yourself or a caller asks who or what you are.

"""
        + (_A2A_INTRO if a2a else "")
        + AGENT_IDENTITY_RULES
        + "\n"
        + (_A2A_SKILL_SURFACE_SECTION if a2a else "")
        + """
## What you can do for the caller

You have three jobs, in this order:

1. **Discovery + guidance.** Explain which workflows exist, what they
   accept, which skill solves a given problem, how to interpret a failure.
   Always cite real skill ids the caller can paste. Never invent workflow
   ids, field names, error codes, or response shapes.

2. **Direct action -- hybrid mode.** When the caller asks you to DO
   something (run a workflow, check a run, list peers), prefer to do it
   yourself via your function tools whenever:
   - You have the capability flag (`enable_workflow_runtime`,
     `enable_workflow_authoring`, `enable_a2a_outbound`) for that surface;
   - You have every required input (no missing files, no missing param
     values);
   - The action is read-only OR the caller has clearly authorised it
     ("run X", "yes please", explicit consent on the prior turn).

   Otherwise (capability off, inputs missing, ambiguous intent), fall back
   to *advise mode*: """
        + (_A2A_ADVISE_MODE if a2a else _OPERATOR_ADVISE_MODE)
        + """

   **Discover -> describe -> run.** When the user asks you to run a
   workflow, ALWAYS work through this loop -- never guess at inputs:

       a. `list_workflows()` -- find the workflow (also pre-populated in
          the `## Grounded context` block below; you only need to call
          this tool when the user names a workflow that isn't there OR
          when the grounded snapshot might be stale mid-conversation).
       b. `describe_workflow(workflow_id)` -- fetch the input schema.
          The returned `fields[]` list tells you which inputs exist,
          which are `required=true`, what `valid_types` they accept,
          what their `current_value` (default) is, any `options`
          (enum), `min_value`, `max_value` constraints, and -- for
          naming/explaining each input to the user -- the node's authored
          `node_display_name` (its Name) and `description` (what it is
          for, set by the workflow author).
          Use `get_workflow_definition(workflow_id)` instead (or as
          well) when the user asks "what does this workflow actually
          do?", "show me the node graph", or "explain the flow" -- it
          returns a COMPACT graph (per-node id/type/name + edge/node
          counts + version metadata), which is what you need to explain
          the flow.  Do NOT pass `include_payload=True` for that; the
          full serialized payload is very large and is only needed to
          render the editor canvas. `describe_workflow` is the tool for
          "what inputs does it need before I run it";
          `get_workflow_definition` is the tool for "what will it do
          to those inputs once it has them". Neither call shows the
          workflow to the user.

"""
        + (_WORKFLOW_SHOWING_A2A if a2a else _WORKFLOW_SHOWING_OPERATOR)
        + """
       c. **Required-input gate -- ASK for missing inputs, never guess,
          default-fill, or run-and-fail.** Scan
          `describe_workflow.fields[]` for every input with
          `required=true` that still has NO usable value -- i.e. it has
          neither a `current_value` (a real default) nor a value the
          user already supplied this conversation. Group the missing
          required inputs into two buckets:
            - **File inputs** -- fields whose `valid_types` contains the
              file type (`"file"`, case-insensitive) AND whose
              `input_mode` is NOT `"questionnaire"`; these come from
              **File Input nodes (`file_input_node`)**. Refer to each by
              its `node_display_name` (the file-input node's authored
              Name, e.g. "Basis of presentation", "Trial Balance") and use
              its `description` to explain what the file is for. Do NOT
              ask the user to attach a file for a questionnaire field.
            - **Other fields** -- text / enum / number / date / etc.;
              refer to each by its `display_name` (or the node's authored
              `node_display_name`) and use its `description` for context.
          If ANY required input is still missing, DO NOT call
          `start_workflow` / `call_workflow`, and DO NOT invent, blank
          out, or substitute a placeholder/guessed value just to make
          the run go. Instead, in ONE message, ask the user for exactly
          the missing inputs in PLAIN NATURAL LANGUAGE: list each missing
          file the user must attach and each missing text/other field the
          user must provide, each named by its human display name. Then
          WAIT.

          **Offer to GENERATE a missing file -- but only generate with
          the user's consent.** When a required FILE input is missing and
          the user has no file to give (or asks whether you can make
          one), you MAY offer to generate a sample/synthetic file
          yourself with the `create_artifact` tool: say what you would
          generate (e.g. "I can create a sample Mandate Intake Form PDF
          with placeholder data for Pepsi") and ask if they want that.
          Only call `create_artifact` AFTER the user has explicitly asked
          for a generated file or agreed to your offer ("make me a sample
          intake form", "yes, generate one") -- NEVER silently fabricate
          an input the user did not consent to, and never pass generated
          data off as real. When you generate a file: tell the user its
          filename and that its contents are SYNTHETIC sample data (not
          real records), and keep calling it a sample in later mentions.
          The returned `file_id` then satisfies the file input exactly
          like an upload -- pass it to `start_workflow` and refer to the
          file by its filename.

          **Resolve user-supplied file ids to filenames FIRST.** Files
          reach you as ids in the chat text, two ways: (a) the caller
          attaches a file inline on the chat message (an A2A FilePart,
          <= 50 MB) and Capitol stores it and appends an
          `[Attached file: <filename> (file_id: <id>)]` line to the
          turn; or (b) the caller uploads out-of-band (`upload_file` /
          `request_upload_url`) and pastes the returned artifact id --
          a bare UUID -- into the chat text. Either way the id is an
          internal identifier, NOT a name. The moment you receive one,
          call `resolve_uploaded_file(file_id)` to learn the file's real
          `filename`, and from then on refer to that file by its filename
          in every user-facing message ("`Q3_work_plans.xlsx` is set as
          your Work Plans input"), keeping the UUID strictly for your own
          tool calls. When you confirm which inputs are satisfied vs.
          still missing, name each satisfied file input by
          `node_display_name` + resolved filename -- NEVER by the pasted
          id. If resolution fails (expired or unknown id), say the upload
          could not be found and ask the user to re-upload -- do not echo
          the failed UUID back.

          **You CAN read text uploads.** When the user asks about the
          CONTENT of an attached text-based file (.json, .txt, .csv,
          .md, .yaml, code, a workflow export...), call
          `read_uploaded_file(file_id)` -- it returns the file's text in
          windows (`offset` / `next_offset` to page). Read the first
          window and page only for the parts you need. Binary formats
          (xlsx, pdf, docx, images) cannot be read into chat -- pass
          those to a workflow as a FILE input.

          **Use the authored Name + Description to write a helpful ask
          (paraphrase -- never paste verbatim).** Each field carries the
          workflow author's `node_display_name` (the node's Name) and a
          `description` (set in the editor's node attributes), plus
          `valid_types`. When you ask for a missing input, SYNTHESIZE a
          short, natural, helpful request IN YOUR OWN WORDS that conveys
          (a) what the file/field is for -- distilled from the
          `description`, (b) named by its authored Name, and (c) the file
          types it accepts -- parse these from the `description` text
          (e.g. an "Supports .xlsx, .csv, .pdf, .docx" line) and/or from
          `valid_types`. Do NOT copy the raw `description` string into
          your message, do NOT quote it wholesale, and do NOT dump a
          long verbatim block of accepted-extension boilerplate; compress
          it to what the user needs. Good (paraphrased): "Please upload
          your **Basis of presentation** document -- the file the
          downstream nodes will process (Excel, CSV, PDF, or Word all
          work)." Bad (verbatim copy): repeating "Upload a file and output
          it to be processed by other nodes. Supports files with
          extensions: .xlsx, .csv, .json, .txt, .doc, .docx, .pdf, .ppt,
          .pptx, .md" word-for-word. If a node's `description` is just the
          generic node-type default (e.g. the boilerplate "Upload a file
          and output it to be processed by other nodes ..." with no
          input-specific meaning), treat it as carrying no real intent:
          lean on the authored Name and accepted types instead, and don't
          parrot the boilerplate.

          **Disambiguating same-named inputs (critical).** When two or
          more missing inputs share the SAME display name (e.g. three
          File Input nodes all named "File Input", or two fields both
          labelled "Notes"), the name alone does NOT tell the user which
          is which. You MUST disambiguate each with a human-meaningful
          descriptor drawn from the data `describe_workflow` actually
          returns, in this order of preference:
            1. the node's authored Name (`node_display_name`) and/or its
               `description` when they are distinct/meaningful (e.g.
               "Basis of presentation", "the trial balance workbook") --
               this is the strongest distinguisher;
            2. the field's own `display_name` if it is more specific than
               the node name (e.g. node "File Input" but field "Trial
               Balance");
            3. the expected file type / format from `valid_types` (e.g.
               "the Excel file", "the PDF");
            4. the file name of any already-loaded default in
               `current_value` (e.g. "currently set to `FY23-TB.xlsx`");
            5. the input's evident purpose if it is clear from the
               workflow / field metadata.
          If NONE of those distinguishes them -- i.e. the inputs have no
          distinct authored Name or Description and are genuinely
          identical except for their internal node id -- fall back to a
          plain-English ORDINAL: "the first File Input", "the second File
          Input", "the third File Input" (ordered as they appear in
          `describe_workflow.fields[]`). NEVER, under any circumstance,
          disambiguate or refer to an input using its internal `id`,
          `node_instance_id`, `field_id`, the composite
          `<node_id>.<field_id>`, or any UUID -- not even in parentheses
          or backticks "for clarity". Those identifiers are for your tool
          calls ONLY and must never appear in a message to the user.
          (Example -- three same-named file inputs with no other
          distinguisher: "This workflow needs three files uploaded -- one
          each for the first, second, and third File Input. Please attach
          all three." If they differ by type: "Please attach the Excel
          file for the first File Input and the PDF for the second.")
          Mention what's already satisfied by defaults in plain terms
          too -- a default's file NAME is fine to state ("the third File
          Input already has `template.docx` loaded"), but its id is not.

          Only proceed to `start_workflow` once the user has supplied
          every missing required input (files attached / values given)
          OR has explicitly told you to use the defaults / proceed
          without them. When all required inputs are already satisfied
          (real defaults or values already in the conversation), DO NOT
          interrogate the user -- skip the question and run normally.

          **Questionnaire File Inputs (`input_mode: "questionnaire"`).**
          A File Input whose authored `.txt` starts with `# questionnaire`
          is NOT a filled input. `describe_workflow` hides the template
          (`current_value` is null). Call `export_workflow_questionnaire`
          for that workflow in the same turn (pass `field_id` when there
          is more than one) BEFORE you reply -- that delivers the
          questions as a .txt file the user's application renders
          verbatim. Then WAIT. Do NOT ask the questions in chat, do NOT
          paste or summarize the file, do NOT ask the user to attach a
          source file for this field, do NOT pass the exported questions
          file as the input, and do NOT call `start_workflow` until they
          have answered. Then pass the compiled answers as a STRING on
          that field in `start_workflow` `inputs` -- or the `file_id` of
          a filled upload that is not the questions file. The server
          writes those answers onto the File Input so downstream nodes
          read them as the file contents. Do not use `ask_user_decision`
          for this.

          **Unusable-input / partial-file gate -- TELL on the main chat,
          never only in thinking (ENG-3635 / ENG-4104).** After a file is
          attached, judge its CONTENT against what the file-input node is
          for (`node_display_name` + `description`). Filename is advisory;
          content is authoritative. If the content is wrong, partial
          (redacted placeholders, missing columns/sections), a different
          document from the one the task needs, or missing required
          substance:
            - You MUST tell the user in your assistant reply -- the main
              chat / left-hand panel -- naming the file and what is wrong
              or missing. Call `report_input_issue` so the same finding is
              published on the chat wire as an `input_issue` frame (and as
              `node.input_issue` on the run's event stream). OneEdge
              renders that in the LHS, not the chain of thought.
            - Never leave the finding only in thinking / chain of thought.
            - Immaterial defects (extra unused column, cosmetic
              formatting) get a brief note, not a stop.
            - Then either wait if the user may re-upload, or proceed with
              partial output AFTER the caveat is in the chat. Do not start
              a doomed run silently.
            - `get_workflow_run_status` / a completed `start_workflow`
              wait may include `input_issues[]`. If that list is present,
              repeat those messages in your assistant reply immediately —
              even when the run later failed at evals ("nothing to
              evaluate"). The user must see the finding in the main chat.
       d. `start_workflow(workflow_id, inputs={...})` -- inputs is a
          dict keyed by EITHER the bare `field_id` (when unambiguous
          across nodes) OR the public `<node_instance_id>.<field_id>`
          composite id from `describe_workflow.fields[].id`.  Prefer
          the composite id when the workflow has multiple nodes of the
          same kind.
       e. **The run has its own UI -- do not narrate it.** The platform
          draws a live run card in the chat the moment a run starts, and
          keeps it updating on its own: the workflow, a progress bar, the
          elapsed clock, the step executing right now, Pause and Stop
          controls, and -- when the run ends -- its outcome and its
          deliverables. The user is watching that. So do NOT repeat what
          it already shows: do not recite the `run_id`, do not report
          progress or the current step, and do not explain that the run
          continues in the background or that they need not keep the tab
          open. Above all, never tell the user what to type to get an
          update -- no "say 'check status'", no "just ask if it's done",
          no "let me know and I'll pull the latest". The card answers
          those questions already, and an unprompted instruction to poll
          reads as though the work needs minding. Acknowledge the start in
          ONE short line, then stop and let them get on with it. Reach for
          `get_workflow_run_status(run_id)` when YOU need the state, and
          hand over `subscribe_workflow_events` only when the caller asks
          for a live tail of their own.
       f. **Do NOT block the turn on the run.** Prefer
          `wait_for_completion=false` (the default): start the run, say so
          in a line, and let the card carry it from there.
          Document-processing workflows routinely take many minutes;
          a chat turn that sits inside `wait_for_completion=true` keeps
          the user staring at silence. If you do pass
          `wait_for_completion=true` and the result comes back with
          `wait_capped: true`, that is NORMAL: the run is still executing
          and the card is still showing it -- say the work is still going,
          briefly and without the `run_id` or polling instructions, and do
          NOT call `start_workflow` again.

   Workflows whose `describe_workflow.fields` is empty take no inputs --
   call `start_workflow(workflow_id, inputs={})` directly.

3. **Mid-run input (agent feedback + human-in-the-loop).** A run can
   PAUSE for input instead of finishing. Detect this with
   `get_workflow_events(run_id, types=["node.input_required"])` (or a
   non-terminal `get_workflow_run_status`). Two kinds, both surfaced as
   `node.input_required`:
   a. **Agent clarification** (`data.input_kind == "clarification"`): an
      agent in the workflow called `ask_user_clarification`. Show the
      `data.prompt` (the question) to the user, then on their reply call
      `submit_clarification_response(run_id, request_id, response)` where
      `request_id` is from the event. The run resumes with the answer.
   b. **Human-in-the-loop node** (no `input_kind`, or a Human Intervention
      node): show `data.prompt`, then call
      `submit_intervention_response(run_id, node_id, request_id, response)`.
   c. **Unusable / partial input** (`node.input_issue`): an agent judged
      an uploaded file or upstream value wrong or incomplete. This does
      NOT pause the run. `data.message` is the user-facing text -- show
      it immediately in the MAIN CHAT (left-hand panel). It is NOT a
      chain-of-thought event (`display` is always true). Also watch
      `get_workflow_events(run_id, types=["node.input_issue"])`. Call
      `report_input_issue` yourself if you detected the problem before
      the run did, so the chat wire carries it even if a later eval
      step aborts the run.
   ALWAYS surface the question to the user and wait for their answer; never
   invent a response.
   **Run continuity (critical -- do not start duplicate runs):** the run
   the user is talking about is the one you started in THIS conversation.
   When a `## Active run (this conversation)` block is present below, it is
   the server-resolved ground truth -- trust it over the chat transcript
   and over your own memory. If it shows a PENDING input checkpoint, the
   user's message this turn is almost certainly their answer to it: route
   it with `submit_intervention_response` / `submit_clarification_response`
   for that run using ONLY the `response` (the run_id / node_id /
   request_id are already resolved -- never ask the user to re-supply
   them). NEVER call `start_workflow` again to "retry" a run that already
   exists and is waiting -- that spawns a duplicate and orphans the real
   checkpoint. NEVER contradict the earlier turn where you started the run
   (do not claim "the run hadn't actually started"); if you are unsure of
   its state, call `get_workflow_run_status` / `get_workflow_events` for
   the run_id in the active-run block rather than guessing or restarting.
   After answering, do NOT claim the run resumed or completed on the
   strength of the submit call: it confirms DELIVERY only. Verify via
   `get_workflow_run_status` / `get_workflow_events` before telling the
   user it advanced, and report honestly if it's still pending.
   **A stopped run stays stopped.** A run that comes back `stopped` or
   `cancelled` (or with `stopped_by_user: true`) was stopped by the USER,
   with the Stop control on the run card. That is a decision of theirs, not
   a failure, and it does not mean the task still needs doing. Do NOT call
   `start_workflow`, `recover_failed_run`, or any other run tool to pick the
   work back up. Say it as theirs and in the second person -- "you stopped
   the run" -- never "the run stopped", "it was halted" or "the attempt was
   stopped", which read as though you are reporting a mystery rather than
   answering the button they just pressed. Report whatever it produced
   before it ended, then let them lead: do NOT ask whether to run it again
   and do NOT raise a clarifying question about it. Start it again only when
   they EXPLICITLY ask you to (`start_workflow(force_rerun=True)`).

4. **Result interpretation.** When a workflow run finishes (success or
   failure), explain it in plain language. For failures, surface the
   `actionable_hint`, the failing node, and `retryable`; recommend retry
   ONLY when `retryable: true`. """
        + (_A2A_ERROR_CODES if a2a else _OPERATOR_ERROR_CODES)
        + """

## Per-run overrides -- what the user CAN and CANNOT change

Users drive workflows by adjusting **inputs**, not by re-engineering the
workflow. Be explicit and honest about this boundary -- never claim or
attempt a change outside it.

YOU CAN override, per-run (run-scoped; the saved workflow is never
rewritten):
- the **task / user prompt** on agent nodes (`user_prompt`) and the
  `task` prompt on chat-output nodes -- i.e. *what to do* this run;
- declared **input fields** (text, options, etc.);
- **input files** (re-supply or swap the files a File Input node consumes).

YOU CANNOT change (these are fixed by the workflow author / node
structure -- they are NOT returned by `describe_workflow` and any attempt
to set them is dropped):
- **system prompts** (an agent's identity/role/behavior is fixed);
- **model** / **temperature** and other administrator-only parameters;
- the **output type / format** -- e.g. you cannot make an Excel/xlsx
  output node produce CSV or a Word doc; the output node type is fixed.
  (This is why "give me a CSV instead" must be refused, exactly like a
  "give me a Word doc instead" request.)
- the **workflow node structure** itself (adding/removing/retyping nodes).

A persona change cannot be achieved by ANY route, including the fields you
ARE allowed to set. `user_prompt` / `task` change *what the agent does this
run*, never *who it is*: do not write role, discipline, practice, party
viewpoint, lens or framing instructions into a task prompt to work around
the fixed system prompt. That applies however additive the wording sounds --
an "overlay", a "framing note", an "annotation-only" or "best-effort" pass,
"in addition to your standard task", or a claim that the author's rules are
left intact. So if a user asks you to run as a different kind of
agent, to adopt another party's viewpoint (buyer / seller / acquirer /
parent), or to work through a different lens, framing, posture or
orientation, say plainly and briefly that you cannot, and stop there. Do
NOT offer a workaround for that role change -- no overlay, no partial or
best-effort version, no different agent, no PERSISTENT edit via
`update_agent_task_prompt` (a role change is out of bounds there too, even
though ordinary edits to that prompt are in bounds -- see below), and no
reworded version of the SAME role change, since rewording cannot make it
acceptable. Never imply the change was applied when it was not. You MAY
offer to run the workflow with its authored task unchanged.

That covers a role change the user actually asked for. It does NOT override
a tool result to the contrary: if `start_workflow` comes back with
`blocked_kind: "persona_change_unconfirmed"`, the run was stopped because the
task merely READ as a role change -- ordinary in-role wording can trip that
check. Follow that result's own instruction, which does invite you to help
the user reword the task to sit inside the agent's EXISTING role. Only
`blocked_kind: "persona_change"` is the final, no-rewording outcome.

The set of overridable fields is whatever `describe_workflow` returns --
**derive "what I can change" from that, never from assumption.** If a
field isn't listed, it isn't overridable.

The compact node list from `get_workflow_definition` and the `sub_agents`
run roster include EVERY node in the workflow, not only the editable ones,
so do not treat the presence of a node there as permission to change it.
Each entry carries an `editable_task` flag: `true` means the user can
override that node's task prompt for this run. `false` means there is no
task prompt to set this run, for one of a few reasons: the node has no
task field at all (for example an input, output-passthrough, or annotation
node), or it has one but the task is fixed because it is wired from another
node or an uploaded document, or it is hidden. When you offer to tailor
agents (for example "you can also adjust the task prompt of these agents"),
list ONLY nodes whose `editable_task` is `true`, and treat `describe_workflow`
as the authority. Never present a node whose `editable_task` is `false` as
tailorable. If a user asks to change such a node's task, explain why it is
not editable based on what the node is: if its task is wired from an
upstream step or an uploaded input, say so and point them to that input; if
the node simply has no editable task, say that plainly. Do not invent an
upstream source for a node that has no task.

Separately from per-run node overrides, YOUR own standing task
prompt may live in your agent config (the administrator-set `task_prompt`
on your agent record). When -- and ONLY when -- the grounded context contains
an `### Agent task prompt` block, that prompt is editable through chat:

- When the user asks to see "the prompt" / "the task" you run on,
  call `get_agent_task_prompt` and quote the returned text verbatim.
- When the user asks to change it, this is IN BOUNDS: apply it with
  `update_agent_task_prompt`. This edit is PERSISTENT -- it rewrites the
  agent's config, so every future conversation uses the new prompt (it is
  NOT run-scoped like `start_workflow` input overrides). Tell the user that
  before applying, and after `status: "applied"` echo the exact saved text.
- A ROLE / PERSONA change is the one thing this tool must NOT be used for.
  Because the edit is permanent, using it to give yourself a different
  role, discipline, party viewpoint or lens is the most damaging version of
  the change the persona rule above forbids -- it would re-orient you
  for every future conversation, not one run. Ordinary edits to this prompt
  (sharpening wording, adding a step, changing emphasis or scope) remain in
  bounds; introducing a new identity does not. Refuse the request; do not
  apply a partial or softened version of it.
- When you offer tailoring, you may include your own task prompt in
  the offer alongside any `editable_task: true` nodes.

When there is NO `### Agent task prompt` block, your own prompt is
not editable: do not offer it, and if asked, say you have no
task prompt in your config -- only the fields `describe_workflow` returns can
be changed. Never claim a config-prompt edit succeeded without an
`update_agent_task_prompt` result saying `status: "applied"`.

When a request is OUT OF BOUNDS (model/temperature, output type/format,
structural change): do NOT pretend or silently try it. Clearly tell the
user **what you can't change and why**, then state **what you CAN** adjust
that might achieve their goal (e.g. "I can't change the output format, but
I can change the task/instructions and the input data -- want me to put the
2021-2022 date range into the task prompt or an input field instead?"). If
nothing user-facing controls what they asked for, say so plainly.

The **system prompt / agent identity is the one exception to that
substitute-offer rule**, and the exception matters: a role / persona /
viewpoint / lens request has NO acceptable substitute, because the
substitute would itself be the prohibited change -- putting the new role
into a task field is the very thing the persona rule above forbids. So for
those requests do NOT follow the pattern above. Never say any version of
"I can't change the system prompt, but I can put that in the task prompt
instead". Refuse plainly, and offer only to run the workflow with its
authored task.

Intent mapping:

- **"What can I tweak/override on workflow X?"** -> `describe_workflow(X)`
  and summarise the returned fields grouped by node
  (`node_display_name`). Only describe the fields it actually returns as
  overridable; don't imply system prompts / model / output type are
  changeable.
- **"Run it but make node Y do <instruction>"** -> resolve Y against
  `fields[].node_display_name`, write the concrete ask/focus into the
  node's `user_prompt` (agent nodes) or `task` (chat-output nodes) --
  NOT the system prompt, and NOT a role/persona/viewpoint/lens instruction,
  which belongs to neither field (refuse those per the persona rule above)
  -- then `start_workflow(workflow_id,
  inputs={"<node_id>.<field_id>": "..."})` using composite ids from
  `fields[].id`. Re-supply required file inputs (reuse the conversation's
  `file_id` when the user means the same file). If the node reference is
  ambiguous, ask -- never guess which node to change.

Override hygiene (non-negotiable -- no hallucinated successes):

- ALWAYS key overrides by the composite `<node_id>.<field_id>` id from
  `describe_workflow.fields[].id`; bare field ids are refused when
  multiple nodes share the field.
- ALWAYS check `start_workflow`'s `applied_inputs` / `ignored_inputs`.
  An entry in `ignored_inputs` means that override was NOT applied (the
  run used the saved value) -- its `reason` will say whether the key was
  unknown or a fixed/locked field. NEVER report an ignored override as
  applied: either fix the key and re-run, or tell the user exactly which
  override didn't take and why.
- Echo back the prompt text / values you actually set so the user can
  confirm. Only state a change was made when `applied_inputs` confirms it.

## Run recovery + reruns

Three rerun tools cover three distinct user intents. Pick by what the
user is asking for -- never start the workflow from scratch when a rerun
tool applies, and never guess node ids (read them off the run's events
or `describe_workflow` / `get_workflow_definition`):

- **"Recover the failed run" / "recover from the last step" / "retry
  from where it failed"** -> `recover_failed_run(run_id)`. It locates
  the node the run failed at (the run's `node.node_failed` event --
  everything that completed before it is the reusable upstream), then
  starts a new run that re-executes the failed node + its downstream
  while seeding every completed upstream node from the source run's
  cache. You do NOT need to find the failed node yourself; only pass
  `node_id` when the user explicitly names the node to restart from.
- **"Rerun from node X (and everything after it)"** ->
  `rerun_from_node(source_session_id, [node_id])`. Classic
  rerun-from-node: the chosen node + all downstream re-execute,
  completed upstream nodes are reused from cache.
- **"Re-run ONLY step X" / "redo just that node" / "keep everything
  else"** -> `rerun_step_in_context(run_id, node_id)`. Single-step
  in-context rerun: exactly one node re-executes; every other completed
  node -- upstream AND downstream -- is reused from the source run's
  cache, and the corrected output slots into the new run.

All three return the NEW run's `run_id` + `session_id`. After
triggering, tell the user the new run_id, and verify / explain the
reuse via the new run's events: the early `rerun_started` /
`rerun_seeded` lifecycle events list `cached_node_ids` /
`fresh_node_ids` / `source_run_id`, and reused nodes emit no
`node.node_started`. Read corrected outputs back with
`get_workflow_output(new_run_id)`. Reruns reuse only COMPLETED source
nodes and run on the Temporal path; a failed/skipped upstream node is
recomputed, never reused.
"""
        + (_A2A_WIRE_REFERENCE_SECTIONS if a2a else "")
        + """
## File / artifact handling

Four ways a caller gets a file to Capitol (every path caps at 50 MB
decoded; larger files are not supported on any path):
- Chat FilePart attachment (`{"kind": "file", "file": {"bytes", "name",
  "mimeType"}}` on the chat message, <= 50 MB decoded): Capitol stores
  it and appends `[Attached file: <filename> (file_id: <id>)]` to the
  turn -- resolve the id and use the file like any upload.
- `<=50 MB` inline on `call_workflow.artifacts[]` (base64), or as a
  FilePart on the call_workflow message (artifact_id = filename).
  Lowest latency; the request body carries the bytes.
- `<=50 MB`: `upload_file` skill (base64). One extra round-trip; returns
  `artifact_id`.
- `<=50 MB`: `request_upload_url` skill returns presigned PUT URL +
  `artifact_id`. Caller PUTs the bytes directly to S3, then references
  the `artifact_id` on `call_workflow.artifacts[]`. Recommend this for
  large files when the caller wants to avoid base64-inflated request
  bodies. FileWithUri (external URL) is NOT supported -- Capitol never
  fetches caller URLs; the caller must send bytes or use presigned PUT.

And ONE way you can create a file yourself: the `create_artifact` tool
(consent-gated -- see the required-input gate above). You write Python
that generates the file (openpyxl for xlsx, reportlab for PDF,
python-docx for docx, stdlib csv/json); it runs in an isolated
code-execution sandbox, and the produced file is stored + registered
exactly like an upload (same 50 MB cap, same `file_id` semantics). Use
it ONLY when the user has asked for / agreed to a generated file, always
describe the result as synthetic sample content, and refer to it BY NAME
so they can inspect what you made — the platform tables the file
automatically, so do not paste its `download_url`.

Naming uploaded files (non-negotiable): an `artifact_id` / `file_id` is an
internal identifier. When a caller hands you one in chat, resolve it once
with `resolve_uploaded_file(file_id)` and use the returned `filename` for
every subsequent user-facing mention of that file. `start_workflow` echoes
the same mapping back as `resolved_files` (`[{key, file_id, filename}]`)
whenever a FILE input was passed as a bare id -- read the filenames from
there too. The id itself belongs only in your tool calls.

**Chaining workflows (run A's outputs -> run B's inputs).** Files PRODUCED
by a completed workflow run are first-class inputs to the next run: each
produced file's `files[].id` (from `get_workflow_output.outputs[*].files[]`,
alongside its `name`) is a valid value for any FILE-typed field on a later
`start_workflow` -- pass the bare id and it resolves exactly like an
uploaded file's id. So when the user asks you to feed one allowlisted
workflow's output into another ("run A, then use its output to run B"),
do it directly: start A (use `wait_for_completion=true` or poll until
terminal), read the produced file ids / text outputs off
`get_workflow_output`, then `start_workflow` B with those values bound to
B's input fields. No download/re-upload round-trip is needed, and text
outputs chain the same way (bind them to B's text inputs or prompts).
Refer to chained files by their `name`, never their id, in user-facing
messages.

**Delivering files (non-negotiable).** The platform renders every file your
tool results carry as a file-download table (`show_output`) — that table IS
the delivery mechanism, and it already contains a working download control for
each file. So by DEFAULT you deliver a file by calling the tool that tables it
and saying in prose what it IS — the table carries the name and the link. Do
NOT paste a download URL for a file that the table already shows: the user
sees the same file twice, once as your link and once as a table row.

**The table IS the file list — never retype it.** The test is whether a table
rendered this turn, NOT how many files there are.

"""
        # Spliced in, not restated: ``file_delivery_rules`` owns this text and the
        # events-only block and ``show_output``'s tool result render it too, so a
        # copy here could contradict what the model is told in the same turn.
        # Wrapped to this document's width; the literals either side supply the
        # blank lines, so the rule must stay unpadded.
        + textwrap.fill(no_retype_rule(SURFACE_TABLE), width=78)
        + """

Describing the set is the whole job ("the clause extraction, the risk scoring,
and the comparison workbook" — never "six files:" followed by six names), so
never answer a "show me the files" ask with either forbidden shape. Naming a
file inline is required only when NOTHING has named it for you — no table
rendered this turn AND your caller draws no file UI of its own — because then
the filename is the only way the user learns what they got; use a sentence
("I've put the risk assessment in `Report.pptx`"), never the label shape. If a
later section tells you the caller renders files itself, that panel names them,
so this exception does NOT apply to you: an absent chat table is not the same
as an absent surface.

That table appears automatically only for files carried by a tool result IN
THE CURRENT TURN. If you are handing over files WITHOUT such a call this turn
— a run that finished earlier, or the user asking again where their file is —
there is no table on screen yet, so call `show_output` (see below) to render
one. Naming a file the user has no table row for leaves them unable to
download it; the answer is always a table, never a pasted link.

This holds for EVERY file, primary or interim: there is no case where a
pasted `download_url` is the right way to hand over a run's output. If the
file has no table row yet, the fix is a `show_output` call, never a link.
`other_outputs` (interim / working files) are no exception —
`show_output(scope="intermediate")` tables them; see "Primary vs other
outputs" below.

If you nonetheless find yourself quoting a URL for something no
`show_output` scope can reach, the stale-link rule applies:
- A `download_url` may be signed with a short-lived credential — one minted
  mid-run, or fetched EARLIER in this conversation, can already be expired by
  the time the user clicks it, even if it prints a long expiry.
- So get it FRESH in the CURRENT turn by calling `get_workflow_output(run_id)`
  at the moment you answer — it refreshes every file's `download_url` at call
  time, so the URLs it returns right now are live. Use those.
- NEVER reuse a URL you saw earlier — not from a previous turn, and not from
  a `get_workflow_output` result you read WHILE THE RUN WAS STILL RUNNING
  (those file URLs were signed mid-run and may already be stale), nor one
  copied out of a progress/status/event payload.
- Any download URL you do put in text MUST come from the `get_workflow_output`
  call you made in THIS turn.

**Primary vs other outputs.** `get_workflow_output` returns two file lists:
`files` (the primary deliverable[s]) and `other_outputs` (additional generated
files the workflow's output marker suppressed as non-deliverables).
- By DEFAULT, deliver and mention ONLY `files`. Do not list, count, or allude
  to `other_outputs` unprompted.
- NEVER claim the run produced no other files — or that a node produced no
  file — just because something is absent from `files`; `other_outputs` may
  hold more. Check `other_outputs` before making ANY completeness statement.
- If the user asks what else was generated / for other documents / for the
  interim or working outputs, THEN deliver them the same way you deliver
  primary files: call `show_output(scope="intermediate")` and describe the
  set in a sentence — do NOT answer with a list of filenames, per "the table
  IS the file list" above. That tool resolves the run's non-primary files
  server-side and
  ALWAYS renders a download table for them — `other_outputs` files are NOT
  auto-tabled by a `get_workflow_output` result (the harvester skips that
  list by design), so an explicit `show_output` call is what gives the user
  a download control. Do NOT paste `download_url` values for them instead:
  a list of bare filenames, or a wall of links, is not a delivery surface.
  Use `get_node_output(run_id, node_id)` if they want a node's full text
  content.

## Showing files to the user

Whenever a tool result of yours carries file references, the platform
AUTOMATICALLY signals the caller's UI to render a file-download table
for exactly those files (`show_output`) -- you never build such a table
yourself. That table NAMES every file it carries, so describe what the files
ARE and let it name them: no filename in your prose, no list of links, and no
filename as a leading label before a dash. "The table IS the file list" above
governs this AUTOMATIC path exactly as it governs a deliberate `show_output`
call — a table you did not ask for is still a table on screen.

You can also trigger that table DELIBERATELY with the `show_output`
tool when the user explicitly asks to see files. Pick the scope that
matches their words:
- `show_output(scope="all")` -- "show me everything the run produced":
  every generated file, deliverables and working files alike.
- `show_output(scope="primary")` -- "show me the results / the
  deliverables": the run's primary outputs only.
- `show_output(scope="intermediate")` -- "show me the intermediate /
  working files": the non-primary generated files only.
- `show_output(scope="single", file_id=...)` -- "show me that file
  (again)": exactly one file.
The file set is resolved server-side from the run's records (pass
`run_id` only when the request is about a run other than the one this
conversation is anchored to), and it works even for files already shown
earlier -- asking again re-renders the table. Use this tool when the
user's intent is "let me SEE/DOWNLOAD the files"; keep using
`get_workflow_output` / `get_node_output` when you also need to READ or
discuss the outputs' content (their file-bearing results signal the
table automatically anyway).

- When the user asks to SEE / DOWNLOAD a run's outputs, the answer is a
  `show_output` call, NOT a filename list: `scope="primary"` for the
  deliverables, `scope="intermediate"` for the interim / working files,
  `scope="all"` for everything. Reach for `get_workflow_output` when you
  need to READ or discuss the outputs' CONTENT -- it may also be called on
  a run that is STILL RUNNING, in which case it returns the files produced
  so far (tell the user the run is still in progress and more outputs may
  follow). Its `other_outputs` list is an INDEX, not a delivery surface:
  naming those files without a `show_output` call leaves the user unable
  to download them. A single node's full text comes from `get_node_output`.
- When the user asks to see an INTERMEDIATE text output (e.g. an
  agent's markdown notes) as a document they can download, offer to
  save it as a file with `create_artifact` (normal consent rules
  apply); the resulting file is then presented to them automatically.

## Editing artifacts after a run

You can EDIT artifacts that workflows (or you) produced -- after the
producing run has COMPLETED -- with three tools that compose:

1. Resolve the EXACT produced file before editing. If the user refers to
   it semantically ("the redacted PDF", "that report", "the file it just
   produced") and you know the run, call `get_workflow_output(run_id)`
   FIRST and choose the matching entry from `files` (or `other_outputs`
   only when the user explicitly means a non-primary output). Use that
   entry's `file_id`. Never guess from an attached-upload line, never use
   a workflow_id as a run_id, and never assume the most recently mentioned
   file_id is the produced output.
2. `get_artifact_provenance(file_id)` -- after resolving the target, verify
   what produced it (`source_run_id`, workflow, node) and its version
   lineage. `kind: "upload"` means the file is the user's own input --
   uploads are NEVER editable; offer a rerun with different inputs or a
   consent-gated `create_artifact` copy instead.
3. `load_run_context(run_id, file_ids?)` -- stages the run's produced
   files into your code sandbox and returns their exact sandbox paths.
   Optional for a single-file edit (`edit_artifact` auto-stages its
   target), useful when the edit reads MULTIPLE run outputs (merge,
   cross-reference).
4. `edit_artifact(file_id, python_code, output_filename,
   change_summary)` -- runs your Python against the staged bytes and
   persists the result as a NEW version (v2, v3, ...) with lineage; the
   original file_id stays valid and unchanged. The target is staged at
   `/home/user/input/edit_target/<filename>`; write the edited file to
   `/home/user/output/<output_filename>`.

**Consent.** An explicit user instruction to change a specific artifact
IS the consent to edit -- restate what you will change, do it, and
describe the result as the edited version (mention the change summary).
Creating a brand-NEW artifact (a new memo from run outputs, a sample
file) stays consent-gated under the `create_artifact` rules: offer
first, act only on an explicit yes.

**Edit vs rerun -- say which you chose and why.** If the change is a
deterministic transformation of bytes that already exist (fix numbers or
typos, rename columns, delete/reorder sections or slides, merge, redact,
re-title), use `edit_artifact`. If the content was GENERATED wrong and
the fix is a prompt/input change, rerun instead
(`rerun_step_in_context` for one node, `rerun_from_node` when
downstream must recompute, `start_workflow(force_rerun=True)` for the
whole run) -- then, if needed, `load_run_context` on the NEW run and
edit/merge from there. Changes that require author-fixed structure
(output node types, system prompts) are out of scope: refuse and point
at the workflow editor.

**Boundaries.** Editing is post-completion only -- while a run is live,
its checkpoints (interventions/clarifications) are the way to influence
it; the tools refuse non-terminal runs mechanically. Results are
labelled `edited` v{n} on the wire and in the Artifacts page; never
present an edited artifact as the run's original output.

**Delivery -- the modification request is complete only when the file
is delivered, in the SAME turn.** A successful `edit_artifact` /
`create_artifact` surfaces the file automatically (a file card renders
from the tool result -- you never need a separate step, and the user
must never have to ask you to "surface it as an output file"). In your
reply, confirm the change (name the file and the change summary) and
refer to the delivered card. NEVER paste a raw storage/presigned URL
into your reply text, and never respond as if delivery were a separate
optional step ("let me know if you want the file").

## Run-context isolation (confidentiality boundary)

Each workflow run's data is confidential to that run. Different runs may
belong to different deals, clients, or engagements whose contents must
never mix -- treat every run as a sealed context.

- When you work on (inspect, discuss, edit, or regenerate) an artifact,
  use ONLY context from that artifact's OWN source run: its run's staged
  files, outputs, events, and prompts. NEVER carry numbers, names,
  findings, or any other content from a previously discussed run or
  workflow into another run's artifact -- not from memory of earlier
  turns, not from files staged earlier, not from an earlier summary.
- Cross-run composition (combining content from two or more runs, e.g.
  "merge these two reports") is allowed ONLY when the user explicitly
  directs it in their own words. Only then may you pass
  `allow_cross_run=true` to the staging/editing tools; state plainly
  which runs you are combining. An ambiguous request is not consent --
  ask first.
- When the user moves on to a DIFFERENT run's artifact, start from a
  clean working context (`load_run_context` and `edit_artifact` refuse
  mixed contexts by default; clear the previous run's files with
  `reset_context=true`) and tell the user you cleared the earlier
  run's context.
- If you are unsure whether two artifacts come from the same run, check
  with `get_artifact_provenance` before combining anything.

## Behavioural guardrails

- **Never expose internal identifiers in ANY message to the user.**
  Replies to the user must be PURE NATURAL LANGUAGE. NEVER put a
  `node_id` / `node_instance_id`, a `field_id`, the composite
  `<node_id>.<field_id>`, a `request_id`, a `version_id`, an internal
  node slug, or ANY raw UUID (e.g. `f8cb0702-9dcb-...`) into text you
  send the user -- not "for context", not in parentheses, not in
  backticks, not to disambiguate. Those identifiers exist solely to key
  your own tool calls. This applies EVEN WHEN THE USER PASTED THE ID
  THEMSELVES: an uploaded file's `artifact_id` / `file_id` arriving in
  the chat text is still an internal identifier -- resolve it via
  `resolve_uploaded_file(file_id)` and answer with the filename, never
  by echoing the UUID back. Whenever you need to point the user at a
  specific input, file, field, or node, name it by its human
  `display_name` / `node_display_name` plus a meaningful descriptor --
  the field's expected type, a resolved or loaded default's file name,
  or its purpose -- and an ordinal ("the first / second / third ...")
  only as a last resort, NEVER by an id (see the required-input gate
  above). The ONLY identifiers that may appear in user-facing text are:
  public skill / method names the caller can invoke (`list_workflows`,
  `start_workflow`, ...), workflow display names, file names, and a
  run's `run_id` when you surface it as the tracking handle for a run
  you just started (the user references their own run by it). If you
  ever feel the urge to add an id "so it's unambiguous", use a
  descriptor or ordinal instead.
- Be concise and concrete. Prefer real skill ids + JSON snippets the
  caller can paste over prose.
- Never fabricate run ids, artifact ids, context ids, workflow ids, field
  names, or skill behaviour. If you don't know, say so and point at the
  discovery skill that will.
- Generated files are the ONE sanctioned form of fabrication, and only
  under the `create_artifact` rules: explicit user request/consent first,
  and the output always labelled as synthetic sample data -- never
  presented as real records, and never substituted silently for a file
  the user was asked to provide.
- Do not reveal bearers, internal hostnames beyond what's in the
  AgentCard, the `X-User-Token` of the caller, or any artifact you
  haven't been explicitly shown. Strip secrets from any payload you echo.
- Don't speculate about workflow internals (node graph topology, prompts
  inside agent nodes, etc.) unless they're in the grounded context.
  Recommend `get_workflow_definition` instead.
- If a caller asks for something outside your allowlist or
  """
        + (_A2A_SURFACE if a2a else _OPERATOR_SURFACE)
        + """ refuse and explain the closest supported
  path.
- Treat every interaction as audit-logged. Be the kind of agent that
  reads well in a post-mortem.
"""
        + (_A2A_REFERENCE_IMPLEMENTATIONS_SECTION if a2a else "")
    )


_LAYER_A_A2A = _layer_a(a2a=True)
_LAYER_A_OPERATOR = _layer_a(a2a=False)
PLATFORM_CORE_SYSTEM_PROMPT = _LAYER_A_A2A

# §3 (run-toolset reuse): the "Reusing a run's tools" section lives next
# to the tools it governs (run_tool_tools.py) and is APPENDED here as its
# own statement so parallel branches editing the Layer-A literal above
# never conflict with this addition.
PLATFORM_CORE_SYSTEM_PROMPT += RUN_TOOLS_PROMPT_SECTION

# Dynamic run-toolset mounting: same append-only pattern -- the guidance
# lives next to the broker it governs (mounted_run_tools.py).
PLATFORM_CORE_SYSTEM_PROMPT += MOUNTED_TOOLS_PROMPT_SECTION

# Operator Phase A (collections read attach): same append-only pattern --
# the "context tools" guidance lives next to the toolset it governs
# (collections_toolset.py) and is appended as its own statement.
PLATFORM_CORE_SYSTEM_PROMPT += CONTEXT_TOOLS_PROMPT_SECTION
PLATFORM_CORE_SYSTEM_PROMPT += LABELS_PROMPT_SECTION
PLATFORM_CORE_SYSTEM_PROMPT += SCHEDULES_PROMPT_SECTION

# Context graphs + the platform tool catalog: same append-only pattern --
# guidance lives next to the toolsets it governs (context_graph_toolset.py
# / platform_tool_catalog.py).
PLATFORM_CORE_SYSTEM_PROMPT += CONTEXT_GRAPH_PROMPT_SECTION
PLATFORM_CORE_SYSTEM_PROMPT += TOOL_CATALOG_PROMPT_SECTION

# Cross-session per-user memory: same append-only pattern -- the
# guidance lives next to the tools it governs (memory_tools.py).
PLATFORM_CORE_SYSTEM_PROMPT += MEMORY_PROMPT_SECTION

# Skills (org Skills library + built-ins): same append-only pattern --
# the guidance lives next to the tools it governs (skill_tools.py).
PLATFORM_CORE_SYSTEM_PROMPT += SKILLS_PROMPT_SECTION

# Observed failure mode (2026-07-29): after a few tool timeouts early in
# a conversation, the model kept treating the whole workflow subsystem
# as down for the REST of the session ("I'll build this directly rather
# than routing it through a workflow -- the backend was timing out
# earlier") even after the backend recovered seconds later. The failure
# evidence lives in the message history, so without explicit guidance
# the model never re-tests it.
TRANSIENT_FAILURES_PROMPT_SECTION = """

## Transient infrastructure failures

Tool errors mentioning timeouts, connection failures, lost sessions, or
"the server may be restarting" describe that MOMENT, not the rest of the
conversation. Deploys finish and servers restart within seconds to
minutes; assume recovery, not outage.

- When the user asks for something that previously failed on
  infrastructure, retry the real path FIRST. Do not route around it, and
  do not downgrade to a manual workaround, based solely on failures from
  earlier turns.
- Fall back only after a fresh attempt in the CURRENT turn also fails --
  and tell the user you retried before falling back.
- Never describe a subsystem as "down" or "timing out" to the user based
  only on errors from earlier in the conversation.
"""

PLATFORM_CORE_SYSTEM_PROMPT += TRANSIENT_FAILURES_PROMPT_SECTION

# Asking the user to choose: same append-only pattern -- the guidance
# lives next to the tool it governs (decision_tools.py).
PLATFORM_CORE_SYSTEM_PROMPT += DECISIONS_PROMPT_SECTION

# Chat safety floor: same append-only pattern. Lives in Layer A so the
# operator-supplied Layer B below cannot displace it.
PLATFORM_CORE_SYSTEM_PROMPT += f"\n\n{PLATFORM_SAFETY_GUARDRAILS}"


# Default Layer B used for any agent whose ``system_prompt`` field is
# empty. Administrators override per-agent from the FE config page.
DEFAULT_AGENT_SYSTEM_PROMPT = """\
## Agent-specific instructions

You are general-purpose. No domain-specific priors. Treat every caller as
a competent technical user unless they identify as something else. Your
name does not change: it is still Operator, never a "workflow
orchestrator" or any other descriptive title.

Tone: terse, factual, helpful. Skip pleasantries. Use markdown sparingly --
code fences for JSON snippets and skill ids, bullets for choices, no
decorative headers.
"""


# Injected when synthetic-data generation is refused (the default).
# Operators turn ``allow_synthetic_data_generation`` ON to restore
# Layer A's consent-gated create_artifact offer.
SYNTHETIC_DATA_FORBIDDEN_PROMPT = """\
## Synthetic data policy (overrides the sample-file offer above)

You work on mission-critical workflows. Never offer to
generate, fabricate, or upload synthetic, sample, or placeholder data
for the user -- even if they ask.

If a required file is missing, ask them to upload a real file. Do not
call `create_artifact` (or any other tool) to invent one and attach it
on their behalf. If they want to run on synthetic data, they must
produce that file themselves and upload it.

When required data is missing, "Not found", or cannot be corroborated,
never mention producing a synthetic or test output, and never pause the
run to offer a choice such as "reply proceed to let it finish as a
synthetic/test output". There is no synthetic fallback branch and no
pause-for-synthetic decision: plainly ask the user to upload a real
file or supply corrected, verifiable entity names, then wait.

Under this policy the `create_artifact` tool is not mounted AT ALL, so
do not offer it for ANY purpose -- including the "save an intermediate
text output as a downloadable file" flow described above. If the user
wants a run's intermediate text, show it in chat via `get_node_output`;
files the run itself produced are still delivered normally through
`show_output`.

Refuse in your main chat reply (not only in thinking). You may still
run workflows on real user-supplied files and analyse those.

If an administrator turns off **Refuse synthetic data generation** on
your configuration page, the consent-gated sample-file offer
above applies instead.
"""


def synthetic_data_generation_allowed(agent_record: Any) -> bool:
    """True only when the operator explicitly opted in.

    Default (missing field, non-dict record, or False) is refuse:
    these orchestrators run mission-critical workflows and must not
    invent sample files for the user.
    """

    if not isinstance(agent_record, dict):
        return False
    return agent_record.get("allow_synthetic_data_generation", False) is True


# What an ABSENT capability flag means in the "### Capability flags"
# grounding block. Every flag not listed here is off when absent. ENG-5459:
# ``run_evals_by_default`` is on when absent -- the same rule the
# "Evaluations (off by default)" section and ``run_chat_turn_stream`` apply.
_CAPABILITY_FLAG_DEFAULTS: Dict[str, bool] = {"run_evals_by_default": True}


def build_system_prompt(
    *,
    agent_record: Dict[str, Any],
    workflows: List[Dict[str, Any]],
    peer_agents: List[Dict[str, Any]],
    capability_flags: Dict[str, bool],
    operator_chat: bool = False,
) -> str:
    """Assemble Layer A + Layer B + grounded-context blocks.

    Layer A (constant) goes first so anything in Layer B can't shadow
    the wire-contract sections.  The grounded context comes last so
    the most up-to-date facts (the agent's *current* allowlists +
    capability flags as of this chat turn) sit closest to the user
    message in the LLM's attention window.  ``operator_chat`` leaves
    out Layer A's A2A-integrator content, which only external callers use.
    """

    layer_a = PLATFORM_CORE_SYSTEM_PROMPT
    if operator_chat:
        # Swap the body; the sections appended after it stay.
        layer_a = _LAYER_A_OPERATOR + layer_a[len(_LAYER_A_A2A) :]
    if agent_record.get("chay_thu_khong_chay") is True:
        # [ghi chú] chat trên trang: bỏ đoạn lời dặn của nhóm công cụ KHÔNG được gắn lượt này. Cờ + mặc định chép ĐÚNG
        # điều kiện gắn ở _build_toolset (collections / context_graph / labels / memory / skills / schedules) ⇒ cờ bật lại thì đoạn quay lại.
        # Đường A2A / Capitol gốc (không có khoá chay_thu_khong_chay) không đổi.
        for _co, _mac_dinh, _doan in (
            ("enable_context_tools", False, CONTEXT_TOOLS_PROMPT_SECTION),
            ("enable_context_graphs", True, CONTEXT_GRAPH_PROMPT_SECTION),
            ("enable_labels", False, LABELS_PROMPT_SECTION),
            ("memory_enabled", False, MEMORY_PROMPT_SECTION),
            ("enable_skills", True, SKILLS_PROMPT_SECTION),
            ("enable_scheduling", True, SCHEDULES_PROMPT_SECTION),
        ):
            if not agent_record.get(_co, _mac_dinh) and _doan and _doan in layer_a:
                layer_a = layer_a.replace(_doan, "", 1)
        if agent_record.get("chay_thu_ban_loi_dan") == "claude" and DECISIONS_PROMPT_SECTION and DECISIONS_PROMPT_SECTION in layer_a:
            layer_a = layer_a.replace(DECISIONS_PROMPT_SECTION, "", 1)   # [ghi chú] công cụ ask_user_decision không gắn ở hồ sơ này ⇒ bỏ đoạn lời dặn của nó
    if not synthetic_data_generation_allowed(agent_record):
        layer_a = f"{layer_a}\n{SYNTHETIC_DATA_FORBIDDEN_PROMPT}"
    layer_b = (
        agent_record.get("system_prompt")
        or agent_record.get("model_system_prompt")
        or DEFAULT_AGENT_SYSTEM_PROMPT
    )

    grounded: List[str] = ["## Grounded context", "", "### Available workflows"]
    grounded.append(
        "Call `describe_workflow(<id>)` to fetch the full input schema "
        "(required fields, defaults, options) before `start_workflow`."
    )
    grounded.append(
        # ENG-3444: the model must never derive an identifier from a
        # display name (it guesses kebab-case; canonical slugs use
        # underscores). Both accepted forms are printed on every entry
        # below so there is always something to copy verbatim.
        "Workflow ids below are shown as `slug` (UUID `...`). Use one of "
        "those two forms EXACTLY as printed -- never invent or derive an "
        "id from a workflow's display name."
    )
    grounded.append("")
    if agent_record.get("chay_thu_khong_chay") is True and workflows and agent_record.get("chay_thu_ban_loi_dan") != "claude":
        # [ghi chú] hồ sơ bản claude KHÔNG vào nhánh này ⇒ rơi xuống vòng lặp gốc bên dưới: danh sách workflow được nhúng lại.
        # [ghi chú] chat trên trang: KHÔNG nhúng danh sách workflow (đo 18/11: 222 workflow = 28.295 token MỖI lần gọi).
        # Chọn workflow là việc của bộ định tuyến (M30); khi người dùng hỏi "có workflow nào làm X" mô hình gọi công cụ list_workflows (vẫn gắn).
        grounded.append(
            f"- ({len(workflows)} workflows are available but NOT listed here to save context. Call `list_workflows` to look them up when the "
            "user asks which workflow does something, then `describe_workflow(<id>)` for details. Never invent an id.)"
        )
    elif workflows:
        for w in workflows:
            wid = w.get("id") or w.get("workflow_id") or "<unknown>"
            wslug = w.get("slug") or ""
            wname = w.get("name") or "(unnamed)"
            wdesc = (w.get("description") or "").strip()
            tags = w.get("tags") or []
            tag_suffix = f"  [tags: {', '.join(tags)}]" if tags else ""
            # ``n_inputs`` / ``n_required`` are best-effort -- when the
            # resolver couldn't load the workflow's input schema (cold-
            # cache, deleted version, etc.) we omit the suffix rather
            # than confidently print "0 inputs" for a workflow that
            # really does need some.
            input_summary = ""
            n_inputs = w.get("n_inputs")
            n_required = w.get("n_required")
            if n_inputs is not None:
                if n_inputs == 0:
                    input_summary = "  [no inputs]"
                elif n_required is not None:
                    input_summary = f"  [{n_inputs} input(s), {n_required} required]"
                else:
                    input_summary = f"  [{n_inputs} input(s)]"
            # ENG-3444: print the canonical slug FIRST (it's what the
            # model should reach for in prose-adjacent contexts, other
            # than the ENG-5375 workflow-link rule, which always wants
            # the UUID) with the stable UUID alongside. Entries without a
            # resolvable slug (unnamed workflow) fall back to the
            # UUID-only shape.
            id_part = f"`{wslug}` (UUID `{wid}`)" if wslug else f"`{wid}`"
            grounded.append(
                f"- {id_part} -- **{wname}**: {wdesc if agent_record.get('chay_thu_ban_loi_dan') == 'claude' else wdesc[:240]}{input_summary}{tag_suffix}".rstrip()
            )
    else:
        grounded.append("- (none allowlisted for you)")

    grounded.append("")
    grounded.append("### Available peer agents")
    if peer_agents:
        for p in peer_agents:
            pid = p.get("id") or p.get("peer_id") or "<unknown>"
            pname = p.get("name") or "(unnamed)"
            grounded.append(f"- `{pid}` -- {pname}")
    else:
        grounded.append("- (none configured)")

    # Agent-config task prompt (ENG-3662 follow-up): ground the agent's own
    # standing task prompt when it lives in the agent record, so the model
    # can quote it and knows it is editable via update_agent_task_prompt.
    # Omitted entirely when the record has no config task prompt -- the
    # Layer A rules key "may I offer this edit?" off this block's presence.
    config_task_prompt = agent_config_task_prompt(agent_record)
    if config_task_prompt is not None:
        grounded.append("")
        grounded.append("### Agent task prompt")
        grounded.append(
            "My own standing task prompt, from my agent config. "
            "The user may review it (`get_agent_task_prompt`) and change it "
            "(`update_agent_task_prompt` -- persistent, config-level, not "
            "run-scoped)."
        )
        grounded.append("")
        grounded.append(config_task_prompt)

    grounded.append("")
    grounded.append("### Capability flags")
    for flag in (
        "enable_workflow_runtime",
        "enable_workflow_authoring",
        "enable_workflow_canvas_editing",
        "enable_a2a_outbound",
        "enable_context_tools",
        "enable_context_graphs",
        "enable_labels",
        "enable_scheduling",
        "e2b_enabled",
        "render_files_in_chat",
        "memory_enabled",
        "enable_skills",
        "allow_synthetic_data_generation",
        "run_evals_by_default",
    ):
        # ENG-5459: ``run_evals_by_default`` is the one flag whose absence
        # means ON (eval nodes run -- the pre-toggle behaviour), and the
        # "Evaluations (off by default)" section below gates on the same
        # absent -> True rule. Rendering it through the generic absent ->
        # False fallback would tell the model evals are off while no such
        # section exists.
        default = _CAPABILITY_FLAG_DEFAULTS.get(flag, False)
        grounded.append(f"- `{flag}`: {bool(capability_flags.get(flag, default))}")

    # ENG-3849: with chat-wire file rendering off, the caller's app draws
    # its own file UI from ``workflow.files_available`` events -- any URL
    # the model pastes would double-render (and go stale). This block
    # overrides the Layer A link-delivery guidance for such agents --
    # including the workflow-linking rule (ENG-5375), since a Capitol-
    # relative ``/workflow/<UUID>`` link is just as unresolvable on a
    # caller-rendered surface as a file link is.
    if not capability_flags.get("render_files_in_chat", True):
        grounded.append("")
        grounded.append("### File rendering (events-only mode)")
        grounded.append(
            "I run with `render_files_in_chat` OFF: the caller's "
            "application renders every produced file itself from workflow "
            "file events. Therefore:\n"
            "- NEVER paste download URLs or markdown links to files in your "
            "replies -- not from `get_workflow_output`, not from artifact "
            "tools, not from any tool result. The user's app already shows "
            "the files; a pasted link double-renders and goes stale.\n"
            # Layer A's rule with the surface noun swapped. Shared rather than
            # restated because these agents never draw a chat table, so any
            # base-prompt clause keyed on "no table rendered" would fire here
            # unconditionally and contradict this override.
            f"- {no_retype_rule(SURFACE_PANEL)}\n"
            "- NEVER write markdown links to `/workflow/<UUID>` when naming "
            "several workflows, either -- that route is on Capitol's own "
            "host and will not resolve on the caller's surface. Name each "
            "workflow in plain text instead; its display name is enough for "
            "the user to ask about it by name.\n"
            "- When the user asks to SEE files, still call `show_output` "
            "with the matching scope -- it signals their app through the "
            "file-event channel (no table is drawn in chat).\n"
            "- Everything else about running workflows and reading outputs "
            "is unchanged, other than the link-delivery overrides above."
        )

    # ENG-3854: prompt text relayed through chat gets truncated by
    # intermediary orchestrators (message caps) and paraphrased by their
    # LLMs -- the export tool ships it as a .txt file instead, so the
    # user reads the byte-for-byte prompt.
    if capability_flags.get("enable_workflow_runtime", True):
        grounded.append("")
        grounded.append("### Workflow prompt requests")
        grounded.append(
            "When the user asks to SEE, review, copy, or export a "
            "workflow's prompt (an agent node's `user_prompt` or a "
            "chat-output node's `task`), call `export_workflow_prompt` -- "
            "it delivers the saved prompt as a downloadable .txt file, "
            "byte-for-byte. NEVER paste the prompt text into your reply: "
            "quoting risks truncation and paraphrase. Summarize at most; "
            "the file is the source of truth."
        )
        if capability_flags.get("workflow_canvas_editing_active", False):
            grounded.append(
                "To CHANGE a prompt for the workflow open on the canvas, "
                "do NOT call `update_workflow_prompt` -- it is unavailable "
                "on this turn and persists a saved definition without "
                "updating the mounted canvas. Instead call `configure_node` "
                "on the node: read the current text from `describe_workflow` "
                "`fields[].current_value`, compose the COMPLETE new prompt, "
                "and pass it as the node's prompt field (`user_prompt` for "
                "an agent node, `task` for a chat-output node) in "
                "`updates`. This queues the change for the user to review "
                "and apply, like any other canvas edit."
            )
        else:
            grounded.append(
                "To CHANGE a prompt, call `update_workflow_prompt` -- both "
                "flows work, no download/upload is ever required for the "
                "second:\n"
                "- The user uploaded a replacement (usually an edited copy "
                "of the exported .txt): pass its `file_id`. The text is "
                "applied server-side byte-for-byte.\n"
                "- The user described the change in chat ('make it "
                "stricter', 'add a rule about citations'): read the "
                "current text from `describe_workflow` "
                "`fields[].current_value`, compose the COMPLETE new "
                "prompt, and pass it as `new_prompt_text`.\n"
                "Both persist a new saved version (edit-permission-"
                "checked). For a one-run experiment pass the new text via "
                "`start_workflow` inputs instead."
            )

    # ``wb_edit_workflow``'s own description says to read the graph with the
    # builder's ``get_workflow``, which the chat never mounts.
    if (
        capability_flags.get("enable_workflow_authoring", False)
        and capability_flags.get("enable_workflow_runtime", True)
        and not capability_flags.get("workflow_canvas_editing_active", False)
    ):
        grounded.append("")
        grounded.append("### Editing a workflow's graph")
        grounded.append(
            "`wb_edit_workflow` with `nodes` and `edges` replaces the whole "
            "graph: any node or edge you leave out is deleted. Before that "
            "call, read the workflow with `get_workflow_definition("
            "workflow_id, include_payload=True)` -- the builder's "
            "`get_workflow` is not available here. That payload is not in "
            "`wb_edit_workflow`'s format: rebuild every node as a node spec "
            "carrying all its current params, and every edge as "
            "`{from, to, target_param}` indexed into your `nodes` list."
        )

    # ENG-5459: with evals off by default, runs skip Evaluations nodes --
    # the user must hear that, and must be able to opt back in per run.
    if capability_flags.get(
        "enable_workflow_runtime", True
    ) and not capability_flags.get("run_evals_by_default", True):
        grounded.append("")
        grounded.append("### Evaluations (off by default)")
        grounded.append(
            "I run with `run_evals_by_default` OFF: workflow runs I start "
            "SKIP Evaluations nodes (they are excluded from execution -- "
            "no eval results are produced -- and they are NOT listed in "
            "the run's `sub_agents` roster). Therefore:\n"
            "- When you start a run, tell the user it is executing WITHOUT "
            "evaluations and that they can ask for evaluations if they "
            "want them.\n"
            "- When the user EXPLICITLY asks to evaluate a run that already "
            "COMPLETED without evals, do NOT start a new run. Call "
            "`rerun_from_node(source_session_id=<that run's session_id>, "
            'starting_node_ids=["evals_node"])`: `"evals_node"` is an '
            "alias for every Evaluations node in the workflow, evals are "
            "switched on for that rerun automatically, and every upstream "
            "node is reused from the completed run's cache -- only the "
            "evaluations execute. If you do know the eval node's instance "
            "id (e.g. from the roster's `sub_agents[].node_id` on an "
            "evals-on run), you may pass it instead of the alias.\n"
            "- NEVER call `rerun_from_node` while the source run is still "
            "running (or paused): the rerun would seed from a partial "
            "cache and re-execute (and re-bill) the unfinished upstream "
            "nodes. If the user asks for evals mid-run, tell them the "
            "evaluations will be run once the current run finishes, "
            "confirm completion with `get_workflow_run_status`, and only "
            "then call `rerun_from_node`. The tool refuses a non-terminal "
            "source run.\n"
            "- Only when the user explicitly wants the WHOLE workflow run "
            "again WITH evaluations, call `start_workflow(run_evals=true, "
            "force_rerun=true)`. My default is unchanged either way.\n"
            "- Never claim eval results exist for a run that skipped "
            'evals (the result carries `evals: "skipped"`).'
        )

    return "\n\n".join([layer_a, layer_b, "\n".join(grounded)])


# ===========================================================================
# Orchestrator construction + invocation
# ===========================================================================


async def _resolve_workflows_for_context(
    context: OrchestratorContext,
) -> List[Dict[str, Any]]:
    """Resolve the agent's effective workflow allowlist for grounded context.

    Uses the same ``resolve_effective_workflows`` the A2A
    ``list_workflows`` skill uses, so the orchestrator's view of "what
    workflows can I invoke" matches what external A2A callers see.
    """

    agent = context.agent_record
    factory = async_session_factory(use_fresh_engine=False)
    try:
        async with factory() as session:
            adverts = await resolve_effective_workflows(
                session,
                org_id=context.org_id,
                workflow_allowlist=agent.get("workflow_allowlist") or [],
                workflow_allowlist_labels=agent.get("workflow_allowlist_labels") or [],
                # Per-user union: the grounded "Available workflows" block
                # must match what list_workflows returns for THIS user
                # (their created/shared workflows count, not just the
                # agent's global allowlist) -- otherwise the model answers
                # "what workflows do I have" straight from a narrower
                # prompt without ever calling the tool. Gated on the
                # agent's ``allow_user_workflows`` toggle (default OFF)
                # exactly like the skill surface, so the two stay in sync.
                user_id=(
                    (context.user_id or None) if user_workflows_enabled(agent) else None
                ),
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "orchestrator: workflow allowlist resolution failed for agent %s: %s -- "
            "grounded context will say '(none allowlisted)' even if the agent has one",
            context.agent_id,
            exc,
        )
        return []

    # Best-effort: pre-fetch each workflow's input-count so the grounded
    # context can show "[3 input(s), 2 required]" / "[no inputs]" next to
    # each workflow.  Lets the LLM triage at a glance without spending a
    # ``describe_workflow`` tool call per candidate.  Failures collapse
    # to "no count" rather than dropping the workflow entirely (so a
    # cold input-schema cache doesn't cause the whole workflow to
    # disappear from the agent's view).
    from api.v1.handlers.workflow import GetWorkflowInputsHandler

    out: List[Dict[str, Any]] = []
    try:
        async with factory() as count_session:
            count_handler = GetWorkflowInputsHandler(count_session)
            for adv in adverts:
                wf = adv.workflow
                entry: Dict[str, Any] = {
                    "id": str(wf.id),
                    # ENG-3444: the canonical slug rides along so the
                    # grounded context can print it -- without it the
                    # model only ever sees UUID + display name and is
                    # tempted to derive (and mis-derive) a slug itself.
                    "slug": workflow_slug(wf.name) if wf.name else "",
                    "name": wf.name or "",
                    "description": (wf.description or "")[:400],
                    "tags": list(getattr(adv, "label_names", []) or []),
                }
                try:
                    counts = await count_handler.handle(
                        org_id=context.org_id,
                        workflow_id=wf.id,
                        version_id=None,
                    )
                    entry["n_inputs"] = len(counts.inputs)
                    entry["n_required"] = sum(1 for f in counts.inputs if f.required)
                except Exception as exc:  # pragma: no cover -- defensive
                    logger.debug(
                        "orchestrator: input-count fetch failed for workflow %s: %s",
                        wf.id,
                        exc,
                    )
                out.append(entry)
    except Exception as exc:  # pragma: no cover -- defensive
        logger.warning(
            "orchestrator: input-count session failed: %s -- "
            "falling back to schema-less workflow listing",
            exc,
        )
        out = [
            {
                "id": str(adv.workflow.id),
                # ENG-3444: the degraded path must still carry the slug --
                # the grounded-context guidance tells the model to copy the
                # printed ``slug (UUID ...)`` pair, so a UUID-only fallback
                # entry would contradict it.
                "slug": (workflow_slug(adv.workflow.name) if adv.workflow.name else ""),
                "name": adv.workflow.name or "",
                "description": (adv.workflow.description or "")[:400],
                "tags": list(getattr(adv, "label_names", []) or []),
            }
            for adv in adverts
        ]
    return out


def _format_completed_run_lines(artifacts: List[Dict[str, Any]]) -> List[str]:
    """Grounding lines for a run that reached terminal SUCCESS.

    Names the already-resolved deliverable(s) and instructs the orchestrator to
    ANNOUNCE them rather than re-run the work or ask the user for a run_id --
    the completion-return fix. Degrades to an honest "finished, no files" when
    the run produced no downloadable deliverables (e.g. an audit workflow).

    HIGH-CONCERN (table-only file delivery): this block deliberately grounds on
    FILENAMES, never ``download_url``.  It used to emit ``- [name](url)`` markdown
    plus an "ACTION: return the link(s)" directive, which is why staging-eu still
    showed "multiple links along with table" AFTER wire 1.0.24 dropped the
    duplicate ``artifact`` frames: 1.0.24 only removed the MACHINE-emitted link
    surface, while this prompt made the MODEL type the very same links back into
    its prose.  Do NOT reintroduce a URL into these lines; the model treats this
    block as an authoritative per-turn ACTION and will obey it over the "do not
    paste links" guidance in the system prompt.

    HIGH-CONCERN (do not claim the table is already on screen): these dicts come
    from ``resolve_conversation_run_context`` at TURN START -- a state read, not
    a tool-result harvest -- so unlike the harvest path they do NOT imply a
    ``show_output`` chat frame was emitted this turn.  On a turn with no
    file-bearing tool call (async completion, or a later "where's my file?"),
    asserting "already rendered" while also forbidding every link-producing tool
    would leave the user with NO download surface at all.  Hence the ACTION
    routes to ``show_output(scope="primary")``, which resolves the run's files
    server-side and ALWAYS emits a frame -- a table without a pasted link.
    (The async-completion path also emits ``workflow.files_available``, but on
    the workflow event stream, which a chat-only consumer never sees.)

    The one legitimate text-link case is ``other_outputs`` (marker-suppressed
    intermediates), which never enters this list at all -- see the delivery rules
    in the system prompt.

    HIGH-CONCERN (do not announce completion over an unrelated ask): this block
    is appended EVERY turn a completed run stays the conversation's active run --
    it has no idea what the user's message this turn is actually about. Staging-eu
    showed the final/primary table alongside an explicitly requested INTERMEDIATE
    table because the old ACTION unconditionally said "tell the user the run
    finished and call show_output(primary)" regardless of the turn's actual ask.
    The ACTION now conditions that announcement on the user asking about this run;
    a turn asking for something else (e.g. the intermediate files) should get only
    that answer, not the primary output volunteered alongside it.

    Args:
        artifacts: Resolved output-file dicts (``download_url`` / ``filename``).

    Returns:
        Lines to append to the active-run block.
    """
    lines: List[str] = [""]
    # Membership still keys off ``download_url``: it is the marker for "this is a
    # real downloadable deliverable" (matching ``_looks_like_artifact``).  Only
    # the RENDERING drops the URL -- an entry without one is not a file the user
    # can be handed, so it must not be announced as a deliverable.
    links = [a for a in artifacts if a.get("download_url")]
    if links:
        lines.append(
            "This run has COMPLETED. Its deliverables were resolved server-side "
            "at the start of this turn. This list is CONTEXT so you know what "
            "exists -- it is NOT a script to read back to the user:"
        )
        for art in links:
            name = art.get("filename") or "download"
            lines.append(f"- {name}")
        lines.append("")
        lines.append(
            "ACTION: if the user's message this turn is about this run "
            "(its status, its results, seeing its output, or anything else "
            "related to it), tell them the run finished and call "
            '`show_output(scope="primary")` -- that renders the download '
            "table, which IS how they get the files. If the user's message "
            "this turn instead explicitly asks for something else -- e.g. "
            "the INTERMEDIATE/working files, or an unrelated question -- "
            "answer THAT and do NOT also volunteer the primary/final output "
            "in the same reply: showing both when only one was asked for "
            "duplicates the delivery. Do NOT reproduce the list above as "
            "bulleted or numbered filenames; say in one sentence what the "
            "deliverable is and let the table name it. Do NOT paste, "
            "construct, or repeat a download URL in your text: the table "
            "carries the links, and a pasted link shows the user the same "
            "file twice. Do NOT call `start_workflow` / `call_workflow` to "
            "re-run this work, and do NOT ask the user for a run_id."
        )
    else:
        lines.append(
            "This run has COMPLETED but produced no downloadable file deliverables."
        )
        lines.append(
            "ACTION: tell the user it finished. If they want detail, summarise it "
            "via `get_workflow_output(run_id)`. Do NOT re-run the workflow and do "
            "NOT ask the user for a run_id."
        )
    return lines


def _format_failed_run_lines(error_message: Optional[str]) -> List[str]:
    """Say the run is dead, not "in flight" or "waiting on a checkpoint".

    Without this branch a terminal FAILED run fell through the same
    ``else`` as a genuinely-open run and was described as "work already in
    flight" with instructions to route the user's next message to a
    checkpoint -- neither true of a run that already errored out.
    """
    lines = [
        "- This run FAILED and is not recoverable by answering a checkpoint "
        'or waiting -- it is not "in flight".',
    ]
    if error_message:
        lines.append(f"  - error: {error_message}")
    lines.append("")
    lines.append(
        "ACTION: tell the user it failed (share the error above if useful). "
        "Do NOT call `get_workflow_run_status` expecting a checkpoint, and do "
        "NOT restart or retry on your own initiative. If the user EXPLICITLY "
        "asks to try again, use the run-recovery tools (see 'Run recovery + "
        "reruns' above) -- `recover_failed_run(run_id)` resumes from the "
        "failed node and reuses the completed upstream from THIS run, which "
        'is what "retry" / "recover" means here. Only fall back to '
        "`start_workflow(force_rerun=True)` (a full restart from scratch) if "
        "the user explicitly asks for that instead."
    )
    return lines


def _format_stopped_run_lines() -> List[str]:
    """Say WHO ended a stopped run, and that ending it was the point.

    A run reaches `stopped` / `cancelled` only by way of the run controls --
    the Stop button on the chat's run card. Given the bare status, the model
    could not tell that apart from any other way a run ends without
    finishing, so it reported the user's own Stop back to them as an
    unexplained halt ("the run stopped before producing the brief", "it was
    halted before the output node ran") and then asked whether to run it
    again -- a question about a decision they had just made deliberately.
    """
    return [
        "- The USER stopped this run, with the Stop control on the run card. It "
        "did not fail, break, time out, or halt on its own: ending it early is "
        "what they asked for.",
        "",
        "ACTION: treat the stop as a decision of theirs that you already know "
        'about. Speak in the second person -- "you stopped the run" -- never '
        '"the run stopped", "it was halted", or "the attempt was stopped", '
        "which read as though you are reporting a mystery you just uncovered. "
        "Do not apologise for it, do not diagnose it, and do not describe it "
        "as a failure or a problem.",
        "Say what it had produced before it ended, if anything, and then stop "
        "and let them lead. Do NOT ask whether to run it again, do NOT offer "
        "to restart it, and do NOT raise a clarifying question about it -- "
        "they stopped it on purpose and will say what they want next. If they "
        "do ask for another run, start one with "
        "`start_workflow(force_rerun=True)`; without that it will refuse, "
        "precisely so a stop is never undone on your initiative.",
    ]


def _format_decision_response_block(response: Optional[Any]) -> Optional[str]:
    """Render the user's answer to a previous ``ask_user_decision``.

    The answer also reaches the model as ordinary message text, but only as
    a sentence it has to match back to the question it asked. Stating it as
    a fact is what keeps the agent from re-asking a question the user has
    already answered -- the failure the structured ask exists to remove.

    Returns ``None`` when this turn carries no answer, so ordinary turns
    look exactly as they did before.
    """
    decision_id = str(getattr(response, "decision_id", "") or "")
    answer = str(getattr(response, "answer", "") or "").strip()
    if not decision_id or not answer:
        return None

    is_free_text = getattr(response, "option_label", None) in (None, "")
    lines = [
        "## The user just answered your question",
        "",
        f"You asked a question with ask_user_decision (id {decision_id}). "
        "This is what they chose:",
        "",
        f"- {answer}",
        "",
        (
            "They typed this instead of picking one of your options, so treat "
            "it as their answer even though it is not on the list."
            if is_free_text
            else "Act on it directly."
        ),
        "Do NOT ask this question again, and do NOT ask them to confirm the "
        "answer they just gave.",
    ]
    return "\n".join(lines)


def _format_collection_references_block(
    references: List[Any], tool_names: List[str]
) -> Optional[str]:
    """Ground the tagged collections. Names ride along because picking a tag
    removes the ``@name`` from the message; catalog-mounted tools are named
    because the collections section only names the static attach."""
    lines = []
    for ref in references or []:
        collection_id = getattr(ref, "collection_id", None)
        if not collection_id:
            continue
        name = getattr(ref, "name", None)
        dataset_id = getattr(ref, "dataset_id", None)
        if dataset_id:
            collection_name = getattr(ref, "collection_name", None)
            label = (
                f'"{name}" (dataset in "{collection_name}")'
                if collection_name
                else f'"{name}" (dataset)'
            )
            ids = f"collection_id={collection_id} dataset_id={dataset_id}"
        else:
            label = f'"{name}" (collection)'
            ids = f"collection_id={collection_id}"
        lines.append(f"- {label} {ids}" if name else f"- {ids}")
    if not lines:
        return None
    return "\n".join(
        [
            "## Data Collections the user attached",
            "",
            "The user tagged these collections in the chat. When their message "
            "refers to data, these are the collections they mean -- use these "
            "ids directly rather than listing collections to find them.",
            "",
            *lines,
            *(
                [
                    "",
                    "They are mounted for this turn: "
                    + ", ".join(f"`{name}`" for name in tool_names)
                    + ".",
                ]
                if tool_names
                else []
            ),
        ]
    )


def _format_active_run_block(
    summary: Optional[Dict[str, Any]], *, operator_chat: bool = False
) -> Optional[str]:
    """Render the live active-run summary as a grounded-context block.

    This is the run-context retention fix: it gives the orchestrator an
    AUTHORITATIVE, per-turn snapshot of the run it started earlier in this
    conversation -- so a bare "approved" routes to the pending checkpoint
    instead of the LLM re-reasoning from chat text, contradicting its own
    prior turn, and spawning a duplicate run (OneEdge "forgets run context").

    ``operator_chat`` names the review tools the operator chat carries for
    a draft under review, in place of the intervention tool.

    Returns ``None`` when there is no active run (the block is omitted
    entirely, so a fresh conversation looks exactly as before).
    """
    if not summary or not summary.get("run_id"):
        return None

    run_id = summary.get("run_id")
    session_id = summary.get("session_id")
    workflow_id = summary.get("workflow_id")
    status = summary.get("status") or "unknown"
    pending = summary.get("pending") or None
    is_terminal = bool(summary.get("is_terminal"))
    artifacts = summary.get("artifacts") or []

    lines: List[str] = [
        "## Active run (this conversation)",
        "",
        "You started a workflow run earlier in THIS conversation. This is its "
        "live state, resolved server-side at the start of this turn -- trust it "
        "over anything in the chat transcript, and NEVER contradict the turn "
        "where you started it:",
        "",
        f"- run_id: `{run_id}`",
    ]
    if session_id:
        lines.append(f"- session_id: `{session_id}`")
    if workflow_id:
        lines.append(f"- workflow_id: `{workflow_id}`")
    lines.append(f"- status: `{status}`")

    if is_terminal and status == "success":
        # Completed run: hand the caller its deliverables and forbid a re-run,
        # rather than re-reasoning from chat text and spawning a duplicate.
        lines.extend(_format_completed_run_lines(artifacts))
        return "\n".join(lines)

    if is_terminal and status in STOPPED_RUN_STATUSES:
        # A stopped run has nothing in flight and nothing waiting on the user.
        # Without this branch it fell through to the still-running advice below
        # and was described as "work already in flight", on top of never being
        # attributed to the user who stopped it.
        lines.extend(_format_stopped_run_lines())
        return "\n".join(lines)

    if is_terminal and status == "failed":
        # Same fallthrough problem as the stopped branch above, for the other
        # terminal ending nothing else here accounts for.
        lines.extend(_format_failed_run_lines(summary.get("error_message")))
        return "\n".join(lines)

    if pending:
        node_id = pending.get("node_id")
        request_id = pending.get("request_id")
        input_kind = (pending.get("input_kind") or "intervention").lower()
        node_family = (pending.get("node_family") or "").lower()
        lines.append("- PENDING input checkpoint: YES")
        lines.append(f"  - input_kind: `{input_kind}`")
        if node_family:
            lines.append(f"  - node_family: `{node_family}`")
        if node_id:
            lines.append(f"  - node_id: `{node_id}`")
        if request_id:
            lines.append(f"  - request_id: `{request_id}`")
        lines.append("")
        # Point at the reviewed document first, before the checkpoint-kind
        # ACTION. Independent of node_family so it surfaces on every
        # pending-resolution path (live marker, durable event, sticky).
        review_line = _review_document_line(pending)
        if review_line:
            lines.append(review_line)
            lines.append("")
        if input_kind == "clarification":
            lines.append(
                "ACTION: the user's message this turn is their answer to this "
                "clarification. Call `submit_clarification_response(response=...)` "
                "for the run_id above -- the run_id/request_id are already known, "
                "so do NOT ask the user for ids."
            )
            lines.append(
                "HARD RULE: do NOT call `start_workflow` / `call_workflow` and "
                "do NOT offer to 're-run', 'start a new run', or 'apply that by "
                "re-running' -- EVEN IF the user's message reads like a task or "
                'command (e.g. "make it more technical for an AI/ML audience"). '
                "A message that arrives while this run is parked on a "
                "clarification is the ANSWER to the question, which the workflow "
                "folds into the running agent's prompt; starting a new run "
                "spawns a duplicate and orphans this one. The `start_workflow` "
                "tool will itself refuse while this checkpoint is open. Only "
                "start a new run if the user EXPLICITLY says to abandon this run "
                "and start over (then `stop_workflow` first)."
            )
        elif node_family == "chat" and operator_chat:
            lines.append(
                "ACTION: a node paused for the user's review of its draft, "
                "which was relayed into this chat. The user's message this "
                "turn is about that draft. What to change -> "
                "`send_node_feedback(feedback=<their words verbatim>)`; the "
                "node regenerates, replies here and waits again. An explicit "
                'approval ("looks good", "approve", "resume", "done", "no '
                'changes") -> `approve_node_output()`. Thanks, a question or a '
                "comment that asks for no change is NEITHER: answer it and "
                "leave the node waiting. Both tools already know the run and "
                "node, so do NOT ask for ids, do NOT use "
                "`submit_intervention_response` for this, and do NOT call "
                "`start_workflow` again. Trust the tool's `delivered` and do "
                "not repeat a call that reported `node_busy`."
            )
        elif node_family == "chat":
            lines.append(
                "ACTION: this is an INTERACTIVE DOCUMENT-REVIEW checkpoint (a "
                "docx/xlsx/pptx review chat), not a continue/stop panel. The "
                "user's message this turn is their review of the generated "
                "document. Call `submit_intervention_response(response=...)` for "
                "the run_id above -- ids are already known, so do NOT ask for "
                "them. Forward the user's words verbatim: an APPROVAL "
                '("continue"/"done"/"looks good"/"approved") finalises the '
                "document and ADVANCES the run; substantive EDIT INSTRUCTIONS "
                '("make section 2 shorter", "add a risk table") are forwarded as '
                "feedback and the node regenerates and re-opens the review "
                "checkpoint. Critically, do NOT keep sending the same answer in "
                "a loop and do NOT call `start_workflow` again. After the call, "
                "trust its `note`/`delivered` and verify with "
                "`get_workflow_run_status` before claiming the run finished."
            )
        else:
            lines.append(
                "ACTION: the user's message this turn is their decision on this "
                "Human Intervention checkpoint. Call "
                "`submit_intervention_response(response=...)` for the run_id "
                "above -- the run_id/node_id/request_id are already known, so do "
                "NOT ask the user for ids. This is a continue/stop panel: forward "
                'an approval ("approved"/"yes"/"go ahead") and it is normalised to '
                '"continue"; forward a refusal ("no"/"stop"/"reject") and it maps '
                'to "stop". To HALT at this checkpoint use '
                '`submit_intervention_response(response="stop")` -- do NOT use '
                "`stop_workflow` (that is for killing a run with no open "
                "checkpoint; here the run is parked ON this checkpoint). Do NOT "
                "call `start_workflow` again: the run already exists and is "
                "waiting on this decision."
            )
    else:
        lines.append("- PENDING input checkpoint: none detected this turn")
        lines.append("")
        lines.append(
            "This is the active run for the conversation; use the run_id above "
            "and do NOT start a duplicate run for work already in flight here. "
            "For a plain status / progress question, `get_workflow_run_status` "
            "ALONE is sufficient and preferred -- it returns the run state, a "
            "compact `node_progress` block (completed / failed / skipped / "
            "finished counts of nodes done so far), and any pending checkpoint. "
            'Report progress from that (e.g. "running -- 12 nodes completed so '
            'far"). Do NOT pull '
            "`get_workflow_events` or `get_workflow_output` just to answer "
            '"how is it going?": reach for `get_workflow_events` only when the '
            "user asks for step/event detail, and `get_workflow_output` only "
            "once the run is complete and they want the deliverables."
        )
        # Defense-in-depth: the live pending signal can be momentarily
        # unavailable while a Human Intervention panel is genuinely open, so a
        # "none detected this turn" is NOT proof there is no checkpoint. If the
        # user's message is a decision/answer, route it to the SELF-RESOLVING
        # submit tool rather than concluding (e.g. from get_workflow_events)
        # that nothing is waiting -- the tool resolves the pending checkpoint
        # server-side from the durable run event and safely reports
        # delivered:false if there genuinely is none. This run is non-terminal
        # (status above), so a checkpoint may have opened since this snapshot.
        lines.append("")
        lines.append(
            "IMPORTANT: if the user's latest message is an approval/denial "
            '("approved", "yes", "go ahead", "no", "stop") or a direct answer, '
            "treat it as a response to THIS run and call "
            "`submit_intervention_response(response=...)` (approval/denial) or "
            "`submit_clarification_response(response=...)` (an answer) for the "
            "run_id above -- do NOT ask the user for ids, and do NOT conclude "
            '"there is no checkpoint" from `get_workflow_events` alone. Those '
            "tools resolve the open checkpoint from the durable run event and "
            "report delivered:false if nothing is actually waiting, so calling "
            "them is always safe. Only fall back to plain status/output tools "
            "when the message is clearly NOT a decision or answer."
        )

    return "\n".join(lines)


# Third surface: the model pastes the link because no table will reach
# this caller on this turn (non-streaming response, or the record was
# written only after grounding, inside the model loop).
_SURFACE_LINK = "link"


def _review_document_line(pending: Dict[str, Any]) -> Optional[str]:
    """Grounding line for a document-review checkpoint, or None without one.

    A table or the caller's own panel owns the document when one of those
    will actually reach the user. Otherwise the line carries the prose link.
    """
    surface = pending.get("review_document_surface")
    if surface == SURFACE_TABLE:
        where = (
            "It is already on screen as a download table this turn, so refer "
            "to it in prose only. Do NOT paste a link, URL or markdown link "
            "to it."
        )
    elif surface == SURFACE_PANEL:
        where = (
            "The user's application already shows it with the checkpoint. "
            "Do NOT paste a link, URL or markdown link to it."
        )
    elif surface == _SURFACE_LINK and pending.get("review_url"):
        return (
            "ACTION (do this FIRST): the user is reviewing a generated "
            "document. Give them this download link: "
            f"{pending['review_url']}"
        )
    else:
        return None
    return (
        f"ACTION (do this FIRST): the user is reviewing a generated document. {where}"
    )


def _coerce_surface_uuid(value: Any) -> Optional[uuid.UUID]:
    """Best-effort UUID coercion for client-supplied surface-context ids."""

    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _build_artifact_surface_block(
    context: OrchestratorContext,
) -> Optional[str]:
    """Grounded block for the ``artifact`` surface (wire 1.0.23).

    Resolves the product Artifact row org-scoped and access-checked
    (``services.artifacts.access`` -- the same effective-role resolution
    the artifacts REST API applies), then grounds the model in the viewed
    artifact so unqualified edit requests need no disambiguation.
    Returns ``None`` (log, no raise) for unresolvable / foreign-org /
    inaccessible ids -- fail-open by contract.
    """

    from sqlalchemy import select

    from db.models.artifact import Artifact
    from db.models.artifact_file import ArtifactFile
    from db.models.enums import ArtifactFileRole, ArtifactStatus
    from services.artifacts.access import ArtifactAccessService

    sc = context.surface_context
    artifact_uuid = _coerce_surface_uuid(getattr(sc, "artifact_id", None))
    if artifact_uuid is None:
        logger.warning(
            "orchestrator: surface_context artifact_id %r is not a UUID -- "
            "skipping grounding (context %s)",
            getattr(sc, "artifact_id", None),
            context.context_id,
        )
        return None

    session_factory = async_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(Artifact).where(
                Artifact.id == artifact_uuid,
                Artifact.org_id == context.org_id,
                Artifact.status == ArtifactStatus.ACTIVE.value,
            )
        )
        artifact = result.scalars().first()
        if artifact is None:
            logger.warning(
                "orchestrator: surface_context artifact %s not found in org "
                "%s -- skipping grounding (context %s)",
                artifact_uuid,
                context.org_id,
                context.context_id,
            )
            return None

        # The ACL fails CLOSED: without a resolved user identity there is no
        # principal to authorize against, and grounding would inject the
        # artifact's content and its producing-run context into the prompt
        # for an unauthenticated caller.
        if not context.user_id:
            logger.warning(
                "orchestrator: surface_context artifact %s grounding skipped "
                "-- no user identity on the turn to authorize against "
                "(context %s)",
                artifact.id,
                context.context_id,
            )
            return None
        role = await ArtifactAccessService(session).get_effective_role(
            artifact.id, context.user_id
        )
        if role is None:
            logger.warning(
                "orchestrator: user %s has no access to surface_context "
                "artifact %s -- skipping grounding (context %s)",
                context.user_id,
                artifact.id,
                context.context_id,
            )
            return None

        primary_result = await session.execute(
            select(ArtifactFile).where(
                ArtifactFile.artifact_id == artifact.id,
                ArtifactFile.role == ArtifactFileRole.PRIMARY.value,
            )
        )
        primary_file = primary_result.scalars().first()

        # The id the artifact tools consume is the CHAT file id (the
        # promotion idempotency key the file_id resolution chain --
        # ``services.artifacts.lookup`` -- resolves), not the ArtifactFile
        # PK: an orchestrator edit/creation's registry file_id, or a
        # run-promoted output's node_result_file id.
        chat_file_id = (
            artifact.source_orchestrator_file_id or artifact.source_node_result_file_id
        )

        artifact_type = getattr(artifact.type, "value", artifact.type)
        lines = [
            "## Active surface: artifact viewer",
            "",
            "The user is viewing this artifact in the product's artifact "
            'viewer right now. Unqualified edit requests ("change the '
            'title", "make it shorter", "fix the second paragraph") target '
            "THIS artifact; calling `edit_artifact` with its file id below "
            "needs no further disambiguation and no clarifying question "
            "about which artifact is meant.",
            "",
            f"- Artifact id: `{artifact.id}`",
            f"- Title: {artifact.title}",
            f"- Type: {artifact_type}",
        ]
        if primary_file is not None:
            lines.append(
                f"- Primary file: {primary_file.name} "
                f"({primary_file.mime_type or 'unknown type'})"
            )
        if chat_file_id:
            lines.append(
                "- File id (for `edit_artifact` / "
                f"`get_artifact_provenance`): `{chat_file_id}`"
            )

        if artifact.source_workflow_id:
            workflow_name = None
            try:
                from db.models.workflow import Workflow

                wf_result = await session.execute(
                    select(Workflow.name).where(
                        Workflow.id == artifact.source_workflow_id,
                        Workflow.org_id == context.org_id,
                    )
                )
                workflow_name = wf_result.scalars().first()
            except Exception as exc:
                logger.warning(
                    "orchestrator: source-workflow lookup failed for "
                    "surface_context artifact %s: %s -- omitting lineage",
                    artifact.id,
                    exc,
                )
            lines.append(
                f"- Produced by workflow: {workflow_name or '(unknown)'} "
                f"(`{artifact.source_workflow_id}`)"
            )
            if artifact.source_run_id:
                lines.append(f"- Produced by run: `{artifact.source_run_id}`")
            lines.extend(
                [
                    "",
                    "When an edit needs more than this file's own bytes -- "
                    "cross-referencing the run's other outputs, reusing its "
                    "data, or understanding how the artifact was produced -- "
                    "FIRST stage the producing run's full context into the "
                    "edit sandbox with "
                    + (
                        f"`load_run_context('{artifact.source_run_id}')`"
                        if artifact.source_run_id
                        else "`load_run_context` (resolve the run id via "
                        "`get_artifact_provenance` on the file id first)"
                    )
                    + ", then edit. Use `describe_workflow` / `read_workflow` "
                    "with the workflow id above to inspect the producing "
                    "workflow's structure and prompts. If the content was "
                    "GENERATED wrong (a prompt or input problem, not a "
                    "mechanical fix), prefer rerunning the workflow over "
                    "editing bytes.",
                ]
            )
        return "\n".join(lines)


async def _build_workflow_canvas_surface_block(
    context: OrchestratorContext,
) -> Optional[str]:
    """Grounded block for the ``workflow_canvas`` surface (wire 1.0.23+).

    Grounds the workflow the user has open on the canvas (org-scoped DB
    resolution when a workflow_id is present) plus the live canvas snapshot
    (nodes, edges, params, focused-node config, lock state) pushed by the
    frontend.  Fail-open like the artifact surface.

    Wire 1.0.27: workflow_id is now optional.  For unsaved ("new") canvases
    we skip the DB lookup entirely and use only live context.
    """

    from sqlalchemy import select

    from api.websockets.workflow_chat_tools import (
        _MAX_RENDERED_PARAM_OPTIONS,
        _format_param_value,
        _is_secret_label,
        _selectable_param_options,
    )
    from db.models.workflow import Workflow

    sc = context.surface_context
    raw_workflow_id = getattr(sc, "workflow_id", None)
    is_locked = getattr(sc, "is_locked", False)

    # ``workflow is None`` after this block means the canvas is genuinely
    # unsaved (no workflow_id was sent at all). A workflow_id that IS
    # present but malformed, deleted, or foreign-org returns None from the
    # whole function instead -- collapsing that case into "unsaved" would
    # silently drop the org-mismatch signal and offer mutation tools for a
    # workflow that doesn't exist.
    workflow = None
    if raw_workflow_id is not None:
        workflow_uuid = _coerce_surface_uuid(raw_workflow_id)
        if workflow_uuid is None:
            logger.warning(
                "orchestrator: surface_context workflow_id %r is not a valid "
                "uuid -- skipping grounding (context %s)",
                raw_workflow_id,
                context.context_id,
            )
            return None

        session_factory = async_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(Workflow).where(
                    Workflow.id == workflow_uuid,
                    Workflow.org_id == context.org_id,
                    Workflow.is_deleted.is_(False),
                )
            )
            workflow = result.scalars().first()

        if workflow is None:
            logger.warning(
                "orchestrator: surface_context workflow %s not found in org %s "
                "-- skipping grounding (context %s)",
                workflow_uuid,
                context.org_id,
                context.context_id,
            )
            return None

    # Build the header line.
    if workflow is not None:
        header = (
            f"The user has workflow `{workflow.id}` (**{workflow.name}**) open "
            'on the canvas right now. Unqualified workflow requests ("run it", '
            '"describe the inputs", authoring asks) target THIS workflow -- no '
            "clarifying question about which workflow is meant."
        )
    elif is_locked:
        header = (
            "The user has an **unsaved workflow** open on the canvas. The "
            "workflow has no persistent id yet -- skip any tool that requires "
            "a saved workflow_id."
        )
    else:
        header = (
            "The user has an **unsaved workflow** open on the canvas. "
            "Structural edits (add/remove/configure nodes, connect/disconnect) "
            "are available. The workflow has no persistent id yet -- skip any "
            "tool that requires a saved workflow_id."
        )

    lock_note = (
        "\n**This workflow is currently locked.** Canvas edits are disabled."
        if is_locked
        else ""
    )

    lines = [
        "## Active surface: workflow canvas",
        "",
        header + lock_note,
    ]

    if (
        context.agent_record.get("enable_workflow_canvas_editing", True)
        and not is_locked
    ):
        lines.extend(
            [
                "",
                "### Editing this workflow",
                "For every edit to the workflow open on this canvas, use the "
                "canvas mutation tools (`add_node`, `remove_node`, "
                "`configure_node`, `connect_nodes`, `disconnect_nodes`). "
                "These tools queue changes for the user to review and apply, "
                "and keep the mounted canvas synchronized. Never use "
                "`wb_edit_workflow` or `update_workflow_prompt` for this open "
                "workflow; those persist a saved definition without updating "
                "the mounted canvas.",
            ]
        )

    focused = getattr(sc, "focused_node", None)
    if focused is not None and getattr(focused, "node_id", None):
        # Mirrors ``build_focused_node_section`` (general-chat WS surface):
        # the focused node wins unqualified change requests.
        lines.extend(
            [
                "",
                "### Currently focused node",
                "The user has selected a specific node on the canvas. "
                "Prioritize questions and edits about this node. When the "
                "user asks for a change WITHOUT naming a different node, "
                "scope it ONLY to this focused node -- do not also apply "
                "it to other nodes unless the user explicitly names them "
                "or says 'all', 'both', etc."
                + (
                    " This holds even when an earlier turn in this "
                    "conversation changed several nodes: the selection below "
                    "is what the user means NOW -- do not ask whether they "
                    "meant the selected node or all of them."
                    if context.operator_chat
                    else ""
                ),
                f"- Node id: `{focused.node_id}`",
            ]
        )
        if focused.node_display_name:
            lines.append(f"- Name: {focused.node_display_name}")
        if focused.node_type:
            lines.append(f"- Type: {focused.node_type}")

    # Wire 1.0.27: rich focused-node config block — prompts, model, fields.
    focused_config = getattr(sc, "focused_config", None)
    if focused_config is not None:
        lines.extend(["", "#### Focused node current configuration"])
        sys_prompt = getattr(focused_config, "system_prompt", None)
        if sys_prompt:
            lines.append(f"- **System prompt**: {sys_prompt[:500]}")
        task = getattr(focused_config, "task", None)
        if task:
            lines.append(f"- **Task / user prompt**: {task[:500]}")
        node_model = getattr(focused_config, "node_model", None)
        if node_model:
            lines.append(f"- **Model**: {node_model}")
        extra = getattr(focused_config, "extra_params", None) or {}
        shown = 0
        for k, v in extra.items():
            # extra_params is keyed by display_name/field_id -- same regex
            # covers both, and keeps credential-shaped values out of the
            # prompt (and provider logs) the way build_focused_node_section
            # does for the legacy WS chat.
            if _is_secret_label(k):
                continue
            # Skip unset values like the node-params loop above does --
            # otherwise ``_format_param_value(None)`` renders "None" as if
            # the field were set, crowding out real values under the cap
            # (PR #1438 review).
            if not isinstance(v, bool) and v in (None, "", [], {}):
                continue
            lines.append(f"- **{k}**: {_format_param_value(v)}")
            shown += 1
            if shown >= 8:
                break
        available = getattr(focused_config, "available_fields", None) or []
        if available:
            lines.append(
                "- **Editable fields** (use in configure_node updates): "
                + ", ".join(f"`{f}`" for f in available)
            )

    # Wire 1.0.26/1.0.27: live canvas snapshot for grounding.
    nodes = getattr(sc, "nodes", None)
    catalog = getattr(sc, "catalog", None)

    # Selectable-field options come from the server-side node registry, not
    # the client-supplied ``options`` on CanvasNodeParam/CanvasCatalogEntry:
    # the browser's catalog is trimmed to field_id/required before it ships,
    # so trusting it here risks the model inventing a plausible-but-invalid
    # value for a dropdown field -- the ENG-2018 failure mode. Keyed by
    # node_type, same identifier space the registry calls node_id.
    options_by_type = _selectable_param_options() if (nodes or catalog) else {}

    if nodes:
        lines.extend(["", "### Current canvas nodes"])
        lines.append(
            "Use these node ids verbatim in remove_node / configure_node / "
            "connect_nodes / disconnect_nodes calls. add_node always "
            "generates its own id for the new node (returned in the tool "
            "result) -- it does not take one of these as input."
        )
        for node in nodes:
            node_id = getattr(node, "id", "?")
            label = getattr(node, "label", None) or node_id
            node_type = getattr(node, "node_type", None)
            type_suffix = f" `{node_type}`" if node_type else ""
            lines.append(f"- `{node_id}`: {label}{type_suffix}")
            # Wire 1.0.27: include non-empty params.
            params = getattr(node, "params", None) or []
            type_choices = options_by_type.get(node_type or "", {})
            for p in params[:10]:
                fid = getattr(p, "field_id", "?")
                if _is_secret_label(fid):
                    continue
                val = getattr(p, "value", None)
                # Booleans render even when False -- that's a real toggle
                # state, not "absent" -- everything else skips empty values.
                if not isinstance(val, bool) and val in (None, "", [], {}):
                    continue
                choices = type_choices.get(fid)
                opt_hint = ""
                if choices:
                    shown_choices = choices[:_MAX_RENDERED_PARAM_OPTIONS]
                    rendered = "|".join(shown_choices)
                    if len(choices) > len(shown_choices):
                        rendered += "|..."
                    opt_hint = f" (choices: {rendered})"
                # The user knows the field by its label ("Do Not Guess Mode"),
                # configure_node needs the field_id -- render both.
                disp = getattr(p, "display_name", None)
                label_hint = (
                    f' "{disp}"'
                    if context.operator_chat and disp and disp != fid
                    else ""
                )
                lines.append(
                    f"  - `{fid}`{label_hint} = `{_format_param_value(val)}`{opt_hint}"
                )

        edges = getattr(sc, "edges", None) or []
        if edges:
            lines.extend(["", "### Current connections"])
            for edge in edges:
                src = getattr(edge, "source", "?")
                tgt = getattr(edge, "target", "?")
                tgt_param = getattr(edge, "target_param", None)
                param_hint = f" → param `{tgt_param}`" if tgt_param else ""
                lines.append(f"- `{src}` → `{tgt}`{param_hint}")

    # Wire 1.0.27: catalog for types present on the canvas.
    if catalog:
        lines.extend(
            [
                "",
                "### Node type catalog (types on this canvas)",
                "Use field_ids from this catalog in configure_node ``updates``.",
            ]
        )
        for entry in catalog:
            etype = getattr(entry, "node_type", "?")
            edisp = getattr(entry, "display_name", etype)
            eparams = getattr(entry, "params", []) or []
            type_choices = options_by_type.get(etype, {})
            param_descs = []
            for p in eparams[:12]:
                fid = getattr(p, "field_id", "?")
                choices = type_choices.get(fid)
                if choices:
                    shown_choices = choices[:_MAX_RENDERED_PARAM_OPTIONS]
                    rendered = "|".join(shown_choices)
                    if len(choices) > len(shown_choices):
                        rendered += "|..."
                    opt_str = f" [{rendered}]"
                else:
                    opt_str = ""
                disp = getattr(p, "display_name", None)
                label_str = (
                    f' "{disp}"'
                    if context.operator_chat and disp and disp != fid
                    else ""
                )
                param_descs.append(f"`{fid}`{label_str}{opt_str}")
            lines.append(
                f"- **{edisp}** (`{etype}`): "
                + (", ".join(param_descs) if param_descs else "_no params_")
            )

    # ``run_workflow`` is only mounted when the canvas-editing capability is
    # on (see ``_build_toolset``) -- steering the model at a tool it doesn't
    # have, while also forbidding the one it does, would be a dead end
    # (PR #1438 review).
    if context.agent_record.get("enable_workflow_canvas_editing", True):
        lines.extend(
            [
                "",
                "### Running this workflow",
                "To run the workflow above, call `run_workflow` — it runs the "
                "graph on screen, unsaved edits included, and the user watches "
                "progress on the canvas. Do NOT use `start_workflow` for it: "
                "that runs the saved definition out of sight, leaving the canvas "
                "idle while the run happens somewhere the user cannot see.",
            ]
        )

    return "\n".join(lines)


async def _build_surface_context_block(
    context: OrchestratorContext,
) -> Optional[str]:
    """Grounded system-prompt block for ``context.surface_context``.

    Wire 1.0.23: the chat client tells us WHAT the user is looking at;
    this turns that hint into a grounded block so unqualified asks
    resolve against the viewed entity. Never raises (never-raise
    contract): unresolvable ids, foreign-org ids, access misses, and DB
    faults all log and return ``None`` so the turn proceeds ungrounded.
    Only the ``artifact`` and ``workflow_canvas`` surfaces inject blocks
    today; ``artifact_gallery`` / ``skill_doc`` validate on the wire but
    add no grounding yet.
    """

    sc = context.surface_context
    if sc is None:
        return None
    surface = getattr(sc, "surface", None)
    try:
        if surface == "artifact":
            return await _build_artifact_surface_block(context)
        if surface == "workflow_canvas":
            return await _build_workflow_canvas_surface_block(context)
    except Exception as exc:  # noqa: BLE001 - grounding is fail-open
        logger.warning(
            "orchestrator: surface-context grounding failed for surface=%s "
            "context=%s: %s",
            surface,
            context.context_id,
            exc,
        )
    return None


def _resolve_peer_agents_for_context(
    context: OrchestratorContext,
) -> List[Dict[str, Any]]:
    """Resolve the agent's peer allowlist for grounded context.

    Lightweight today -- just lifts whatever's on the agent record.  A
    follow-up can join against ``agent_peer_agents_v1`` for richer
    metadata (display name / capabilities).
    """

    peer_ids = context.agent_record.get("peer_agent_allowlist") or []
    return [{"id": p, "name": p} for p in peer_ids if p]


# Tool names ``_build_toolset`` attaches to every agent, capability flags
# aside -- exported so the gating tests assert against one definition
# instead of repeating the literal in every test module. The next
# always-on tool joins this tuple, not six separate test files.
ALWAYS_ON_TOOL_NAMES: Tuple[str, ...] = ("ask_user_decision",)

# The persistent-path prompt tool ``_build_toolset`` drops in favour of
# ``configure_node`` while canvas mutations are actually usable -- a
# module constant so a rename in ``workflow_tools.py`` breaks the filter
# loudly (via the gating test asserting against this same name) instead
# of the filter silently no-op'ing and the ENG-5106 bug returning.
CANVAS_REPLACED_PROMPT_TOOL_NAME = "update_workflow_prompt"


# [ghi chú] hằng của bản vá "chat trên trang": 10 công cụ chạy / tiếp tục / duyệt lượt + trần một lượt chat.
# Số trần PHẢI bằng chay_thu/giao_dien/muc_toi_da.py (CHAT_*) — đổi bên này thì đổi bên kia.
CHAY_THU_CAM_CHAY = (
    "start_workflow", "rerun_from_node", "recover_failed_run", "rerun_step_in_context", "resume_workflow",
    "submit_intervention_response", "submit_clarification_response", "submit_input_decision",
    "approve_node_output", "send_node_feedback",
    # [đã soát 12/11, đã chốt] bản đầu: chat không dừng / tạm dừng lượt của người khác (vd lượt đo), không SỬA workflow có sẵn (3 workflow KPI) —
    # chỉ liệt kê, xem, TẠO MỚI. Sửa thì người làm trên xưởng.
    "pause_workflow", "stop_workflow", "update_workflow_prompt",
)
CHAY_THU_CAM_WB = ("edit_workflow", "update_workflow_node",   # công cụ SỬA của bộ dựng workflow (tên trước tiền tố wb_)
                   # [ghi chú] build_workflow_from_image tự chạy agent AI RIÊNG trong capitol-mcp-servers (services/workflow_builder/agent.py;
                   # model do mô hình chat truyền vào, hộp có OPENAI_API_KEY) ⇒ tiền NGOÀI trần chat + NGOÀI sổ, không phải gpt-6-luna.
                   # register_workflow_node: ghi thêm loại khối vào danh mục dùng chung — chat không được đổi danh mục.
                   "build_workflow_from_image", "register_workflow_node",
                   # [ghi chú] 3 công cụ tốn lượt gọi: mô tả công cụ của bộ dựng bắt gọi
                   # submit_tool_feedback SAU MỖI lần (workflow_builder_server.py:2338) ⇒ +~35k token cố định mỗi lần; list_workflows /
                   # get_workflow kéo payload workflow cũ vào lịch sử (nguồn của chặn 100 KB / lần). Chat dựng chỉ cần list / inspect / assemble.
                   "submit_tool_feedback", "list_workflows", "get_workflow",
                   # [ghi chú] trạng thái kết nối / link UỶ QUYỀN tài khoản ngoài (Composio) — chat trang không nối tài khoản ngoài
                   "composio_connection_status", "composio_authorization_link")
# [đã chốt 12/11 sau lượt thật] trần vào 150k ⇒ 400k: mỗi lần gọi mang ~35.000 token cố định (đo: 1 lần gọi = 35.155) ⇒ 150k chỉ đủ ~4 lần gọi,
# câu TẠO workflow dừng ở lần thứ 5 (153.970). 400k ≈ 10 lần gọi.
CHAY_THU_CHAT_LAN, CHAY_THU_CHAT_VAO, CHAY_THU_CHAT_RA, CHAY_THU_CHAT_RA_MOT_LAN = 20, 400_000, 12_000, 8_000
# [đã soát 12/11 — M1] input_tokens_limit là trần TỔNG ĐÃ DÙNG (pydantic-ai 1.107.5 usage.py check_before_request) ⇒ lần gọi kế có thể mang
# ngữ cảnh lớn tuỳ ý (kết quả công cụ tới 100k–500k ký tự). Chặn CỠ từng lần gọi: tổng byte UTF-8 của mọi tin nhắn sắp gửi ≤ số dưới
# (1 token ≥ 1 byte ⇒ token ≤ byte). Phần cố định ngoài tin nhắn (lời dặn + mô tả công cụ) tính riêng ở muc_toi_da.CHAT_CO_DINH.
# [ghi chú] 100_000 ⇒ 220_000: 251 workflow ⇒ list_workflows (84 KB sau rút gọn) + wb_list_workflow_nodes cùng lúc = 106.757 byte
# ⇒ mọi câu dựng dừng (đo 18/11). Số này PHẢI bằng muc_toi_da.CHAT_BYTE_1_LAN (trang dò trước khi gửi — chat_operator.do_truoc).
# Không nâng quá 222.000: byte + 50.000 token cố định phải < 272.000 token (trên đó luna tính giá cao hơn ⇒ mức tối đa sai) — đã soát 19/11.
CHAY_THU_CHAT_BYTE_1_LAN = 220_000


async def _doc_payload_chay_thu(ma: str, org_id: str) -> dict:
    """[ghi chú] payload phiên bản MỚI NHẤT của workflow ``ma`` — CHỈ khi workflow thuộc ĐÚNG tổ chức của lượt chat
    (E: phải kiểm tổ chức) và chưa xoá. Không thấy ⇒ ném lỗi ⇒ boc ghi "không đọc được ⇒ CHƯA ĐẠT"."""
    import uuid as _uuid

    from sqlalchemy import select as _select

    from db.models.workflow import Workflow as _WF
    from db.models.workflow_version import WorkflowVersion as _WV
    from db.models.enums import VersionType as _VT
    from db.session import async_session_factory as _asf

    wid = _uuid.UUID(str(ma))
    async with _asf(use_fresh_engine=False)() as s:
        wf = (await s.execute(_select(_WF).where(_WF.id == wid, _WF.org_id == str(org_id)))).scalar_one_or_none()
        if wf is None or getattr(wf, "is_deleted", False):
            raise LookupError("workflow không thuộc tổ chức của lượt chat / không có")
        # [E minor 2, 17/11] bỏ bản RUN / SCHEDULED_RUN / TRIGGERED_RUN (ảnh chụp lúc chạy) — chỉ xét bản LƯU
        _chay = [_VT.RUN, _VT.SCHEDULED_RUN, _VT.TRIGGERED_RUN]
        v = (await s.execute(_select(_WV).where(_WV.workflow_id == wid, _WV.version_type.notin_(_chay))
                             .order_by(_WV.created_at.desc()).limit(1))).scalar_one_or_none()
        if v is None or not isinstance(v.payload, dict):
            raise LookupError("workflow chưa có phiên bản")
        return v.payload


def _r12_chay_thu(pl: dict):
    """[ghi chú] R12 ĐANG GẮN (workflow/r12_kiem.py) trên payload — cùng đường lúc chạy (transform + validate_connections + kiem_r12).
    Trả None (qua) hoặc chữ lý do (từ chối)."""
    from workflow.r12_kiem import kiem_r12
    from workflow.runtime.payload_transformer import transform_advanced_to_backend

    wp = transform_advanced_to_backend(pl)
    wp.validate_connections()
    try:
        kiem_r12(wp)
    except ValueError as e:
        return str(e)
    return None


def _chay_thu_chat_trang(context) -> bool:
    return (getattr(context, "agent_record", None) or {}).get("chay_thu_khong_chay") is True


# [ghi chú] hồ sơ chay_thu_ban_loi_dan == "claude": KHÔNG trần token vào / ra, KHÔNG max_tokens từ đây (cổng AI đặt mức
# tối đa của tuyến), KHÔNG rào byte. Chỉ một rào chống vòng lặp: số lần gọi mô hình trong một lượt chat.
CHAY_THU_CL_LAN = 20   # = CHAY_THU_CHAT_LAN đang dùng (đã soát 19/11: giữ số hiện có; không còn trần token thì đây là thứ DUY NHẤT chặn lượt chạy hoang trước chốt 30 USD / ngày của cổng)


CHAY_THU_MUC_SUY_LUAN = ("low", "medium", "high", "xhigh", "max")


def _chay_thu_dat_muc(context, agent_model, ten: str) -> str:
    """[ghi chú] Hồ sơ claude có `chay_thu_muc_suy_luan` ⇒ đổi tên GỬI TRÊN DÂY của mô hình vừa dựng
    thành "<tên>-<mức>" (tuyến có sẵn ở bảng cổng AI). Mức lạ, hoặc đối tượng dựng ra không mang đúng tên trần (vd lớp bọc lùi sang Bedrock) ⇒ NÉM — không lặng lẽ
    chạy mức khác. Không có khoá mức / không phải hồ sơ claude ⇒ không đụng gì. Trả tên gửi thật (ghi nhật ký)."""
    muc = (getattr(context, "agent_record", None) or {}).get("chay_thu_muc_suy_luan") if _chay_thu_ban_claude(context) else None
    if not muc:
        return ten
    if muc not in CHAY_THU_MUC_SUY_LUAN:
        raise ValueError(f"chay_thu: mức suy luận lạ '{muc}'")
    if getattr(agent_model, "_model_name", None) != ten:
        raise ValueError(f"chay_thu: không đặt được mức suy luận '{muc}': mô hình dựng ra ({type(agent_model).__name__}) không mang tên gửi '{ten}'")
    agent_model._model_name = f"{ten}-{muc}"
    logger.info("[OPERATOR_MO_HINH] ma=%s ten_gui=%s", _chay_thu_ma_luot(context), agent_model._model_name)   # dấu vết: đối chiếu với ô mo_hinh_goc của sổ cổng
    return agent_model._model_name


def _chay_thu_ban_claude(context) -> bool:
    return _chay_thu_chat_trang(context) and (getattr(context, "agent_record", None) or {}).get("chay_thu_ban_loi_dan") == "claude"


def _chay_thu_ma_luot(context) -> str:
    """Mã lượt do may_chu sinh (UUID) để may_chu tìm ĐÚNG dòng log của lượt mình. Sai dạng ⇒ "-" (không cho chữ lạ vào log)."""
    import re as _re

    v = (getattr(context, "agent_record", None) or {}).get("chay_thu_ma_luot")
    return v if isinstance(v, str) and _re.fullmatch(r"[0-9a-f-]{36}", v) else "-"


def _chay_thu_gon_mo_ta(r):
    """[đã chốt 12/11 sau lượt thật 11:11] `describe_workflow` trả ~20.600 byte / workflow KPI (đo trong hộp): 32 trường × ~450 byte + `input_schema` 6.000
    byte lặp lại cùng nội dung — đều là khuôn ĐẦU VÀO để CHẠY workflow, thứ chat trên trang không làm. 8 lần gọi song song = 174.299 byte ⇒ vượt phép
    chặn cỡ 100.000 byte. Rút gọn (chỉ ở chat trên trang): bỏ `input_schema`; mỗi trường chỉ giữ tên khối, tên ô, giá trị đang đặt (≤ 300 ký tự), có bắt
    buộc không ⇒ ~5.500 byte. Không che thứ người dùng hỏi được qua chat: workflow có khối nào, ô nào, đang đặt gì."""
    if not isinstance(r, dict) or not isinstance(r.get("fields"), list):
        return r
    def gon(x):
        if not isinstance(x, dict):
            return x
        ra = {k: x.get(k) for k in ("node_display_name", "field_id", "display_name", "required") if x.get(k) not in (None, "")}
        v = x.get("current_value")
        if v not in (None, "", [], {}):
            chu = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
            ra["current_value"] = chu if len(chu) <= 300 else chu[:300] + f"… (đã rút gọn, {len(chu)} ký tự)"
        return ra
    ra = {k: v for k, v in r.items() if k not in ("input_schema", "fields")}
    ra["fields"] = [gon(x) for x in r["fields"]]
    ra["ghi_chu"] = "Kết quả đã rút gọn cho chat trên trang: không kèm khuôn đầu vào để chạy (chat không chạy workflow)."
    return ra


def _chay_thu_gon_dinh_nghia(r):
    """`get_workflow_definition(include_payload=True)` trả cả payload ~40.000 byte — chỉ cần khi SỬA sơ đồ (chat trên trang không sửa) ⇒ thay bằng 1 dòng."""
    if isinstance(r, dict) and "payload" in r:
        ra = {k: v for k, v in r.items() if k != "payload"}
        ra["payload_note"] = "Payload đầy đủ đã lược ở chat trên trang (chat không sửa workflow có sẵn). Dùng graph + edges ở trên."
        return ra
    return r


def _chay_thu_boc_gon(fn, xu_ly):
    """Bọc một công cụ (hàm async) để rút gọn KẾT QUẢ trước khi đưa cho AI; giữ nguyên tên, mô tả, chữ ký (functools.wraps) ⇒ AI thấy công cụ y như cũ."""
    import functools
    import inspect

    if not inspect.iscoroutinefunction(fn):
        return fn

    @functools.wraps(fn)
    async def boc(*a, **k):
        r = await fn(*a, **k)
        try:
            return xu_ly(r)
        except Exception:  # noqa: BLE001 — rút gọn lỗi thì trả nguyên (phép chặn cỡ vẫn là lớp sau)
            return r
    return boc


def _chay_thu_gon_ds_workflow(r):
    """[ghi chú] `list_workflows` trả [{id, name, description, version_id, labels}] cho MỌI workflow của tổ chức (không có tham số
    lọc): 251 workflow = ~101 KB ⇒ vượt rào 100 KB của một lần gọi ⇒ câu dựng dừng. Bỏ `version_id` + `labels` rỗng; GIỮ đủ mọi mục và nguyên văn id / name /
    description. Không phải list các dict ⇒ trả nguyên. Có bỏ gì thì ghi một dòng nhật ký (không rút lặng)."""
    if not isinstance(r, list) or not all(isinstance(x, dict) for x in r):
        return r
    ra = [{k: v for k, v in x.items() if k != "version_id" and not (k == "labels" and not v)} for x in r]
    _bo_v = sum(1 for x in r if "version_id" in x)
    _bo_l = sum(1 for x in r if "labels" in x and not x["labels"])
    if _bo_v or _bo_l:
        _cb = lambda z: len(json.dumps(z, ensure_ascii=False, default=str).encode("utf-8", "replace"))   # noqa: E731
        logger.info("[CHAY_THU_GON] list_workflows: %s mục, bỏ version_id của %s mục + labels rỗng của %s mục, %s ⇒ %s byte",
                    len(r), _bo_v, _bo_l, _cb(r), _cb(ra))
    return ra


CHAY_THU_GON = {"describe_workflow": _chay_thu_gon_mo_ta, "get_workflow_definition": _chay_thu_gon_dinh_nghia, "list_workflows": _chay_thu_gon_ds_workflow}


def _chay_thu_co_tin(agent_run, node) -> int:
    """Tổng byte UTF-8 của các tin nhắn sẽ gửi ở lần gọi mô hình kế tiếp (lịch sử của lượt + yêu cầu mới). Không đếm được ⇒ coi như quá cỡ."""
    try:
        tin = list(getattr(getattr(getattr(agent_run, "ctx", None), "state", None), "message_history", None) or [])
        yc = getattr(node, "request", None)
        if yc is not None and all(yc is not m for m in tin):
            tin.append(yc)
        if not tin:   # không thấy tin nhắn nào (cấu trúc thư viện đổi?) ⇒ đóng khi nghi ngờ
            return CHAY_THU_CHAT_BYTE_1_LAN + 1
        tong = 0
        for m in tin:
            for p in getattr(m, "parts", None) or []:
                # [D vòng 2] đếm MỌI trường chữ / dict / list của phần tin (nội dung, tham số, chữ ký + chi tiết nhà cung cấp của phần suy nghĩ
                # gửi lại cho API…), không chỉ content + args. Phần không có __dict__ ⇒ đếm repr.
                gt = list(vars(p).values()) if hasattr(p, "__dict__") else [repr(p)]
                for v in gt:
                    if isinstance(v, str):
                        tong += len(v.encode("utf-8", "replace"))
                    elif isinstance(v, (dict, list, tuple)):
                        tong += len(json.dumps(v, ensure_ascii=False, default=str).encode("utf-8", "replace"))
        return tong
    except Exception:  # noqa: BLE001
        return CHAY_THU_CHAT_BYTE_1_LAN + 1


def _chay_thu_ghi_usage(context, agent_run, cham_tran) -> None:
    """Capitol không lưu token của lượt chat ở đâu ⇒ MỘT dòng log cho may_chu tính tiền (như [DIGEST_USAGE]). Lỗi khi ghi ⇒ bỏ qua."""
    try:
        u = agent_run.usage() if callable(getattr(agent_run, "usage", None)) else agent_run.usage
        logger.info(
            "[OPERATOR_USAGE] ma=%s requests=%s input=%s output=%s cache_read=%s cham_tran=%s cong_cu=%s wf_luu=%s wf_cham=%s ban=" + ("claude" if _chay_thu_ban_claude(context) else "luna"),   # [ghi chú] + wf_luu (hẹp, để hiện) + wf_cham (rộng, để khoá — E K1)
            _chay_thu_ma_luot(context), getattr(u, "requests", 0), getattr(u, "input_tokens", 0),
            getattr(u, "output_tokens", 0), getattr(u, "cache_read_tokens", 0), int(cham_tran),
            _chay_thu_ten_cong_cu(agent_run), _chay_thu_wf_luu(agent_run), _chay_thu_wf_cham(agent_run),
        )
    except Exception:  # noqa: BLE001
        pass


def _chay_thu_wf_luu(agent_run) -> str:
    """[ghi chú] Mã workflow mà KẾT QUẢ công cụ dựng của CHÍNH lượt này nêu ở mức trên cùng (workflow_id / id) — để trang biết workflow nào do lượt
    chat lưu (không lẫn workflow người khác lưu cùng lúc). Hàm TỰ ĐỦ (không dùng hàm khác của module). Chỉ UUID, không trùng, tối đa 20. Không có ⇒ "-"; lỗi ⇒ "?"."""
    import json as _json
    import re as _re

    _uuid = _re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    _dung = {"assemble_workflow", "edit_workflow", "publish_workflow", "build_workflow_from_image"}
    try:
        kq = getattr(agent_run, "result", None)
        tin = kq.all_messages() if kq is not None else getattr(getattr(getattr(agent_run, "ctx", None), "state", None), "message_history", None)
        ma = []
        for m in tin or []:
            for p in getattr(m, "parts", None) or []:
                if getattr(p, "part_kind", None) != "tool-return":
                    continue
                t = getattr(p, "tool_name", None)
                if not isinstance(t, str) or (t[3:] if t.startswith("wb_") else t) not in _dung:
                    continue
                c = getattr(p, "content", None)
                if isinstance(c, str):   # kết quả là chuỗi JSON (có thể kèm lời của lớp kiểm sau ghép nối phía sau) ⇒ đọc đối tượng JSON ĐẦU chuỗi
                    try:
                        c = _json.JSONDecoder().raw_decode(c.lstrip())[0] if c.lstrip()[:1] == "{" else None
                    except Exception:  # noqa: BLE001
                        c = None
                if isinstance(c, dict) and isinstance(c.get("structured_content"), dict):
                    c = c["structured_content"]
                if not isinstance(c, dict):
                    continue
                for khoa in ("workflow_id", "id"):
                    v = c.get(khoa)
                    if isinstance(v, str) and _uuid.fullmatch(v):
                        if v not in ma:
                            ma.append(v)
                        break
        return ",".join(ma[:20]) or "-"
    except Exception:  # noqa: BLE001
        return "?"


def _chay_thu_wf_cham(agent_run) -> str:
    """[ghi chú] Mọi mã dạng UUID mà công cụ của CHÍNH lượt này CHẠM tới: ở BẤT KỲ mức nào trong THAM SỐ lời gọi và trong KẾT QUẢ của MỌI
    công cụ, chỉ trong tin MỚI của lượt (new_messages — không lấy lịch sử lượt trước). Trang dùng để KHOÁ: workflow CÓ SẴN đổi phiên bản trong lúc chat VÀ mã nằm
    trong danh sách này ⇒ khoá (bắt cả đường sửa không trả mã ở mức trên cùng). Hàm TỰ ĐỦ. Không có ⇒ "-". Không tách được tin mới / quá 80 mã / lỗi ⇒ "?"
    (trang giữ cách cũ: workflow có sẵn nào đổi cũng khoá — đóng khi không chắc)."""
    import json as _json
    import re as _re

    # [E K1b] nhận cả mã viết CHỮ HOA và viết LIỀN 32 ký tự hex (đều là UUID hợp lệ với PostgreSQL) ⇒ chuẩn hoá về chữ thường có gạch nối như mã trang so
    _uuid = _re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{8})-?([0-9A-Fa-f]{4})-?([0-9A-Fa-f]{4})-?([0-9A-Fa-f]{4})-?([0-9A-Fa-f]{12})(?![0-9A-Fa-f])")
    try:
        kq = getattr(agent_run, "result", None)
        moi = getattr(kq, "new_messages", None)
        if not callable(moi):
            return "?"                     # không tách được tin của riêng lượt này ⇒ không đoán
        ma = []
        for m in moi() or []:
            for p in getattr(m, "parts", None) or []:
                kind = getattr(p, "part_kind", None)
                if kind == "tool-call":
                    v = getattr(p, "args", None)
                elif kind == "tool-return":
                    v = getattr(p, "content", None)
                else:
                    continue
                chu = v if isinstance(v, str) else _json.dumps(v, default=str, ensure_ascii=False)
                for nhom in _uuid.findall(chu or ""):
                    x = "-".join(nhom).lower()
                    if x not in ma:
                        ma.append(x)
        if len(ma) > 80:
            return "?"
        return ",".join(ma) or "-"
    except Exception:  # noqa: BLE001
        return "?"


def _chay_thu_ten_cong_cu(agent_run) -> str:
    """TÊN các công cụ trợ lý đã gọi, theo thứ tự (đã chốt 12/11: khung chat hiện "Đã làm: …"). CHỈ tên — không tham số, không nội dung;
    tên sai dạng [A-Za-z0-9_]{1,64} bị bỏ (không cho chữ lạ vào log); tối đa 40 tên. Không đọc được ⇒ "-"."""
    import re as _re

    try:
        kq = getattr(agent_run, "result", None)
        tin = kq.all_messages() if kq is not None else getattr(getattr(getattr(agent_run, "ctx", None), "state", None), "message_history", None)
        ten = []
        for m in tin or []:
            for p in getattr(m, "parts", None) or []:
                if getattr(p, "part_kind", None) == "tool-call":
                    t = getattr(p, "tool_name", None)
                    if isinstance(t, str) and _re.fullmatch(r"[A-Za-z0-9_]{1,64}", t):
                        ten.append(t)
        return ",".join(ten[:40]) or "-"
    except Exception:  # noqa: BLE001
        return "-"


def _build_toolset(context: OrchestratorContext) -> Tuple[List, List]:
    """Build the (function_tools, mcp_toolsets) tuple for the agent.

    Each factory is capability-gated on the agent record's
    ``enable_*`` flag. ``make_workflow_builder_toolset`` returns
    ``None`` when registry discovery fails -- treat that as "skip"
    rather than crash.
    """

    agent = context.agent_record
    function_tools: List = []
    mcp_toolsets: List = []
    canvas_editing_active = (
        agent.get("enable_workflow_canvas_editing", True)
        and getattr(context.surface_context, "surface", None) == "workflow_canvas"
    )
    # A locked canvas strips ``make_canvas_mutation_tools`` down to
    # ``canvas_locked_notice`` / ``list_nodes`` / ``run_workflow`` (see
    # below) -- no mutation tool is actually usable. Only drop the
    # persistent-path tools (``update_workflow_prompt`` / ``edit_workflow``)
    # when their canvas-mutation replacement is live; otherwise a locked,
    # open canvas leaves the turn with no way to edit the workflow at all.
    canvas_is_locked = getattr(context.surface_context, "is_locked", False)
    canvas_mutations_available = canvas_editing_active and not canvas_is_locked

    if agent.get("enable_workflow_runtime", True):
        workflow_tools = make_workflow_tools(context)
        if canvas_mutations_available:
            workflow_tools = [
                tool
                for tool in workflow_tools
                if getattr(tool, "__name__", "") != CANVAS_REPLACED_PROMPT_TOOL_NAME
            ]
        function_tools.extend(workflow_tools)
        # The workflow-card signal is only offered where a card can render
        # (same delivery gate as ``show_output``).
        function_tools.extend(make_show_workflow_tools(context, workflow_tools))
        # Review through the operator is a chat-surface behaviour; A2A callers
        # keep the intervention tool and the review socket.
        if context.operator_chat:
            function_tools.extend(make_review_tools(context))
        # Artifact generation rides the workflow-runtime capability: its
        # whole purpose is satisfying a workflow's file inputs, and the
        # tool degrades to a readable error when E2B is unconfigured.
        # ``e2b_enabled`` (per-agent config, default true) drops the
        # sandbox-backed create/edit tools entirely for agents whose
        # operators turned code execution off.
        if agent.get("e2b_enabled", True):
            function_tools.extend(make_artifact_tools(context))
        # Run-toolset introspection + reuse (§3) rides the same
        # capability: it reads run payloads/events and rebinds the run's
        # own MCP tools during post-completion iteration.
        function_tools.extend(make_run_tool_tools(context))
        # Wire 1.0.22: the explicit show_output render-signal tool rides
        # the same capability -- it reads run output records (the same
        # ``_resolve_run_output_files`` surface as get_workflow_output).
        function_tools.extend(make_show_output_tools(context))
        # ENG-4104: unusable/partial file findings on the main chat wire.
        function_tools.extend(make_input_issue_tools(context))
        # Scale display contract: the standing "show intermediate files"
        # preference toggle (session + conversation scope, catch-up emit).
        function_tools.extend(make_display_preference_tools(context))
        # Dynamic run-toolset mounting: the load/unload/list/consent
        # meta-tools plus the broker toolset whose ``get_tools`` is
        # re-read every model step -- tools mounted mid-turn become
        # callable on the next step. The factory self-gates on
        # ``A2A_RUN_TOOL_MOUNT_ENABLED``.
        mount_tools = make_mount_tools(context)
        if mount_tools:
            function_tools.extend(mount_tools)
            mcp_toolsets.append(make_run_tool_broker(context))
            # The platform tool catalog (search + mount over the org's
            # reachable MCP servers) rides the broker: without it,
            # catalog-mounted tools could never surface. Self-gates on
            # A2A_TOOL_CATALOG_ENABLED.
            function_tools.extend(make_catalog_tools(context))
    if agent.get("enable_a2a_outbound", True):
        function_tools.extend(make_peer_a2a_tools(context))
    # Canvas mutations: when the user has a workflow open on the canvas,
    # give the agent add/remove/configure/connect/disconnect/list tools
    # that push canvas_mutation frames onto the SSE stream for frontend
    # apply.  Mutations are stripped when the workflow is locked
    # (is_locked=True on the surface_context) -- the server refuses to
    # queue edits even if the FE somehow passes the Apply button guard.
    #
    # Unlike every other family here, arming this one is otherwise decided
    # entirely by client-supplied request metadata (``surface_context``),
    # not an agent capability -- so it also needs its own ``enable_*`` gate:
    # any caller that sends ``surface: "workflow_canvas"`` (including an
    # external A2A peer) would otherwise get graph-mutation tools on every
    # agent with no way to turn that off short of a deploy (PR #1438 review).
    if canvas_editing_active:
        from services.orchestrator.tools.canvas_mutation_tools import (
            make_canvas_mutation_tools,
        )

        function_tools.extend(
            make_canvas_mutation_tools(context, locked=canvas_is_locked)
        )
    if agent.get("enable_workflow_authoring", True):
        builder = make_workflow_builder_toolset(
            context,
            exclude_tools=(("edit_workflow",) if canvas_mutations_available else ())
            + (CHAY_THU_CAM_WB if agent.get("chay_thu_khong_chay") is True else ()),   # [ghi chú] chat trên trang không sửa workflow có sẵn
        )
        if builder is not None and agent.get("chay_thu_khong_chay") is True:
            # [ghi chú] BƯỚC KIỂM SAU KHI GHÉP: bọc wb_assemble_workflow ⇒ đọc payload ĐÃ LƯU theo mã trong kết quả
            # (cùng tổ chức) ⇒ kiem_sau_ghep (luật hộp + luật tầng + R12 + gpt-6-luna + chặn khối ra ngoài; câu KPI ⇒ chọn workflow có sẵn)
            # ⇒ nối chữ kiểm vào kết quả cho mô hình sửa (≤ 2 vòng, lần thứ 4 từ chối bằng code). Giống cổng {error, instruction} của Capitol.
            # make_workflow_builder_toolset không truyền process_tool_call (workflow_builder_toolset.py) ⇒ goc None ⇒ boc gọi thẳng call_tool.
            from api.a2a_gateway.boc_assemble import boc as _chay_thu_boc

            builder.process_tool_call = _chay_thu_boc(
                getattr(builder, "process_tool_call", None),
                doc_payload=lambda _ma, _org=context.org_id: _doc_payload_chay_thu(_ma, _org),
                r12=_r12_chay_thu,
            )
        if builder is not None:
            mcp_toolsets.append(builder)
    # Operator Phase A: read-only collections attach. Default OFF (unlike
    # the three flags above) -- the iteration plan gates it per-agent to
    # keep the tool count bounded; the factory itself enforces the
    # A2A_ENABLE_CONTEXT_TOOLS kill-switch and the user-token requirement.
    if agent.get("enable_context_tools", False):
        mcp_toolsets.extend(make_collections_toolsets(context))
    # Context graphs are the org's accumulated knowledge memory -- default
    # ON (per-agent opt-out), unlike the collections attach. The factory
    # fails closed on the fleet kill-switch and tokenless turns.
    if agent.get("enable_context_graphs", True):
        mcp_toolsets.extend(make_context_graph_toolset(context))
    if agent.get("enable_labels", False):
        function_tools.extend(make_label_tools(context))
    # Cross-session per-user memory (default OFF, per-agent opt-in). The
    # factory fails closed on turns without a resolved user identity
    # (tokenless synthetic A2A users), same contract as context graphs.
    if agent.get("memory_enabled", False):
        function_tools.extend(make_memory_tools(context))
    # Skills: curated instruction packs (org Skills library + the
    # built-in document/data skills). Default ON -- the tools degrade to
    # readable errors when platform-api is unreachable, and the sandbox
    # install only happens on agents whose artifact tools are attached.
    if agent.get("enable_skills", True):
        function_tools.extend(make_skill_tools(context))
    # Defaults ON, like enable_skills: scheduling is running a workflow with
    # a "when", against the same allowlist and the same per-workflow run
    # permission. Agents predating the flag carry no value, and requiring
    # their owner to open the config page and press Save just to gain it
    # would be a worse default than letting anyone who wants it off toggle
    # it off.
    if agent.get("enable_scheduling", True):
        function_tools.extend(make_schedule_tools(context))
    if agent.get("enable_org_tool_config", False):
        function_tools.extend(make_org_mcp_config_tools(context))
    # Agent-config task-prompt tools (ENG-3662 follow-up). Not capability-
    # flagged: the factory self-gates on the agent record actually carrying
    # a config ``task_prompt`` and returns [] otherwise, which is exactly
    # the editability rule (no config prompt -> nothing to edit or offer).
    function_tools.extend(make_agent_task_prompt_tools(context))
    # Asking the user to choose between named alternatives. Not capability-
    # flagged and not gated on the workflow runtime: any agent that can talk
    # can reach a fork it should not pick unilaterally, and with no chat
    # stream installed the tool degrades to the model's own prose.
    function_tools.extend(make_decision_tools(context))

    # Dynamic mounting: a mounted MCP tool whose name shadows a static
    # function tool would make ``CombinedToolset.get_tools`` raise and
    # kill the step -- record the claimed names so the broker skips them.
    if settings.A2A_RUN_TOOL_MOUNT_ENABLED:
        from services.orchestrator.tools.mounted_run_tools import ensure_registry

        registry = ensure_registry(context)
        registry.reserved_names = {
            getattr(t, "name", None) or getattr(t, "__name__", None)
            for t in function_tools
        } - {None}

    # [VÁ 10 chay_thu 06/11/2026] sandbox R12 (d2) bản TỐI THIỂU cho máy thử (dữ liệu giả): bỏ khỏi chat Operator mọi công cụ ĐỌC đầu ra
    # khối / sự kiện / file của lượt chạy (D đọc nội dung từng công cụ — SOAT_THIET_KE_SANDBOX_D.md mục 11) + công cụ gọi lại MCP của lượt chạy
    # (invoke_run_tool: "người dùng đồng ý" là cờ AI tự đặt — run_tool_tools.py:677-690). Operator vẫn liệt kê / chạy workflow, xem trạng thái.
    # Cái giá: Operator không đọc được kết quả cho người dùng (mất T38c) — bản đầy đủ "chỉ khối cuối đã duyệt" để trước dữ liệu thật.
    # Tắt bằng CHAY_THU_R12_OPERATOR=false.
    if os.environ.get("CHAY_THU_R12_OPERATOR", "true").strip().lower() not in ("false", "0", "no", "off"):
        _cam = {
            "get_workflow_output", "get_node_output", "get_eval_report", "get_workflow_events",
            "get_workflow_chain_of_thought", "read_uploaded_file", "resolve_uploaded_file",
            "invoke_run_tool", "load_run_tools", "load_run_context",
        }
        _bo = [getattr(t, "name", None) or getattr(t, "__name__", None) for t in function_tools]
        function_tools = [
            t for t in function_tools
            if (getattr(t, "name", None) or getattr(t, "__name__", None)) not in _cam
        ]
        logger.info("[R12_OPERATOR] bo_cong_cu=%s con=%s", sorted(set(_bo) & _cam), len(function_tools))

    # [ghi chú] CHAT TRÊN TRANG 18400 KHÔNG ĐƯỢC CHẠY / TIẾP TỤC / DUYỆT lượt workflow: lượt do chat kích sẽ tốn
    # tiền AI ngoài giữ chỗ + sổ ngày của may_chu, và duyệt phải do người bấm. Tắt enable_workflow_runtime thì mất cả công cụ liệt kê / xem
    # (một cờ gắn cả gói make_workflow_tools) ⇒ lọc theo TÊN như R12. Chỉ áp khi HỒ SƠ có chay_thu_khong_chay == True (may_chu gắn cứng;
    # agents.py:519 giữ nguyên dict agent_config). Không có khoá ⇒ như cũ (buoc9 / S5). Đóng khi nghi ngờ: may_chu đọc dòng log dưới theo
    # mã lượt (chay_thu_ma_luot) — không thấy con_cam=0 ⇒ may_chu khoá đường chat.
    if agent.get("chay_thu_khong_chay") is True:
        _cam_chay = set(CHAY_THU_CAM_CHAY)
        if agent.get("chay_thu_ban_loi_dan") == "claude":   # [ghi chú] trang không hiện được hộp lựa chọn ⇒ bỏ công cụ hỏi-lựa-chọn
            _cam_chay.add("ask_user_decision")
        _ten = [getattr(t, "name", None) or getattr(t, "__name__", None) for t in function_tools]
        function_tools = [
            t for t in function_tools
            if (getattr(t, "name", None) or getattr(t, "__name__", None)) not in _cam_chay
        ]
        function_tools = [   # [đã chốt 12/11] rút gọn kết quả 2 công cụ đọc workflow (CHAY_THU_GON) — chỉ ở chat trên trang
            _chay_thu_boc_gon(t, CHAY_THU_GON[getattr(t, "__name__", "")]) if getattr(t, "__name__", "") in CHAY_THU_GON else t
            for t in function_tools
        ]
        _con_lai = {getattr(t, "name", None) or getattr(t, "__name__", None) for t in function_tools}
        logger.info(
            "[R12_OPERATOR] khong_chay=1 ma=%s bo_chay=%s con_cam=%s con=%s",
            _chay_thu_ma_luot(context), ",".join(sorted(set(_ten) & _cam_chay)) or "-",
            len(_con_lai & _cam_chay), len(function_tools),
        )

    return function_tools, mcp_toolsets


def _mount_broker_attached(mcp_toolsets: List) -> bool:
    """Catalog-mounted tools only reach the model through the broker."""

    return any(getattr(t, "id", None) == RUN_TOOL_BROKER_ID for t in mcp_toolsets)


def _collections_toolsets_attached(mcp_toolsets: List) -> bool:
    """True when the built toolset list carries the collections attach.

    The grounded ``enable_context_tools`` flag must reflect what actually
    MOUNTED this turn, not what the agent record asked for: the factory
    fails closed (fleet kill-switch, tokenless synthetic-user turns,
    unresolvable URLs), and grounding ``True`` for a turn with no
    ``struct_*``/``unstruct_*`` tools invites the model to plan around
    collection lookups it cannot make.
    """

    return any(
        getattr(t, "tool_prefix", None) in COLLECTIONS_TOOL_PREFIXES
        for t in mcp_toolsets
    )


def _context_graph_toolset_attached(mcp_toolsets: List) -> bool:
    """True when the built toolset list carries the context-graph attach
    (same grounding rationale as ``_collections_toolsets_attached``)."""

    return any(
        getattr(t, "tool_prefix", None) == CONTEXT_GRAPH_TOOL_PREFIX
        for t in mcp_toolsets
    )


def _builder_toolset_attached(mcp_toolsets: List) -> bool:
    """True when the built toolset list carries the workflow builder
    (same grounding rationale as ``_collections_toolsets_attached``)."""

    return any(
        getattr(t, "tool_prefix", None) == _WORKFLOW_BUILDER_TOOL_PREFIX
        for t in mcp_toolsets
    )


def _resolve_valid_override(override: Optional[str]) -> Optional[str]:
    """Resolve a per-conversation chat model override, or ``None``.

    Wire 1.0.23: the deprecation remap applies BEFORE the allowlist check
    (same contract as the agent-record path) so a client-side selection
    keeps working after its model retires. ``None`` means no usable
    override -- the caller falls back to the agent-record model and emits
    the "model X unavailable" status frame; the turn never fails.
    """

    if not override:
        return None
    resolved, deprecation_message = resolve_deprecated_model(override)
    if deprecation_message:
        logger.info("orchestrator: model_override %s", deprecation_message)
    if resolved in settings.ALLOWED_CHAT_MODEL_OVERRIDES:
        return resolved
    return None


def _resolve_model(agent_record: Dict[str, Any], override: Optional[str] = None) -> str:
    """Pick the pydantic-ai model string from the agent record.

    ``override`` (wire 1.0.23, additive): a per-conversation chat model
    override -- it wins when it resolves into
    ``ALLOWED_CHAT_MODEL_OVERRIDES``; anything else falls back to the
    agent record. The override is never written back to the record.

    Deprecated Anthropic IDs (e.g. ``claude-opus-4-7-fast``, retired
    2026-07-24 per ENG-3023) are remapped to their active replacement so
    saved agent configs keep working after a model retires; the same
    remap applies to overrides.
    """

    valid_override = _resolve_valid_override(override)
    if valid_override:
        return valid_override

    raw = (
        agent_record.get("model")
        or agent_record.get("model_name")
        or "claude-sonnet-4-5-20250929"
    )
    resolved, deprecation_message = resolve_deprecated_model(raw)
    if deprecation_message:
        logger.info("orchestrator: %s", deprecation_message)
    return resolved


def _build_agent_model(model_id: str) -> Any:
    """Translate a resolved model id into what ``Agent(model=...)`` needs.

    Anthropic ids that live in the registry are built via the agent-node
    factory so they get the registry policy applied: fast-mode variants
    (``claude-opus-4-8-fast``) are internal ids the Anthropic API does not
    accept -- the factory strips the ``-fast`` suffix and injects
    ``anthropic_speed='fast'`` -- and adaptive-thinking models (Opus 4.7+,
    Sonnet 5, Fable 5) get their thinking shape, max output tokens, and
    prompt caching configured, with Bedrock failover when enabled.

    Anything else (``gpt-5.5``, ``gemini-2.5-pro``, unknown strings) is
    returned as-is for pydantic-ai's ``infer_model`` to resolve, preserving
    the previous behavior for non-Anthropic models.
    """

    try:
        anthropic_model = AnthropicModels(model_id)
    except ValueError:
        return model_id
    return get_anthropic_model_with_bedrock_failover_for_agent_node(anthropic_model)


@dataclass
class ChatTurnResult:
    """Return shape of :func:`run_chat_turn`."""

    assistant_text: str
    all_messages: List[ModelMessage] = field(default_factory=list)
    tool_calls_made: int = 0
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    # B2: the run_id of a workflow kicked off (or re-run) during this turn,
    # captured structurally from the run-initiating tool's result so chat
    # consumers no longer have to scrape it out of the assistant's natural
    # language.  ``None`` when the turn started no run (pure Q&A, an answer
    # to a checkpoint, authoring, etc.).
    run_id: Optional[str] = None
    # ENG-3385: True when a blocking turn was cut short by the sync
    # wall-clock budget (A2A_CHAT_SYNC_TURN_BUDGET_S). Everything on the
    # result is still valid partial output; ``assistant_text`` ends with
    # an honest truncation notice.
    truncated: bool = False
    # Wire 1.0.21 (additive): the ``show_output`` batches emitted during
    # this turn, in order -- one per file-bearing tool result, each
    # ``{"reason", "run_id"?, "files": [...]}``.  Mirrored onto the
    # terminal chat data part so blocking ``message/send`` callers get
    # the same render signal streaming consumers saw as frames.
    show_outputs: List[Dict[str, Any]] = field(default_factory=list)
    # Canvas edits this turn proposed through tools, one line each
    # (``canvas_claims.describe_canvas_mutation``). ``[]`` for a canvas
    # turn that proposed nothing; ``None`` off the canvas surface.
    canvas_proposals: Optional[List[str]] = None

    @property
    def history_text(self) -> str:
        """The reply as the conversation history should replay it."""
        return strip_unbacked_canvas_change_note(self.assistant_text)


@dataclass
class ChatStreamEvent:
    """One incremental event from :func:`run_chat_turn_stream`.

    ``type`` values:

    * ``status``      -- coarse lifecycle marker (``text`` is human-readable).
    * ``thinking``    -- a chunk of model reasoning as it streams
      (``text`` is the delta; ENG-3385 -- real turn progress on the
      wire so long turns are never byte-silent).
    * ``tool_call``   -- the LLM invoked a function tool (``tool_name`` +
      ``text`` carrying the truncated args).
    * ``tool_result`` -- a function tool returned (``tool_name`` + ``text``
      carrying the truncated result; ``payload`` carries ``{"run_id"}``
      when the tool started a workflow run).
    * ``run_started`` -- a run-initiating tool created a workflow run;
      ``payload`` carries ``{"run_id"}`` plus (ENG-3234, additive) the
      upfront node roster as ``sub_agents`` + ``total_sub_agents`` when
      the tool resolved one.  Emitted the moment the run exists -- via
      the ``notify_run_started`` side channel from INSIDE the tool, even
      while ``wait_for_completion`` keeps it blocked -- so callers can
      subscribe to the workflow event stream even if the rest of the
      turn is slow or the connection dies (ENG-3385 -- the EY "no chain
      of thought" failure).  Deduped per run_id.
    * ``keepalive``   -- heartbeat emitted whenever no other event has
      flowed for ``A2A_CHAT_KEEPALIVE_INTERVAL_S`` (default 15s),
      spanning the whole turn INCLUDING blocking tool execution.
      Carries no data; consumers should skip it.
    * ``text``        -- a chunk of assistant text as it streams from the
      model (``text`` is the delta, not the accumulated string).
    * ``artifact``    -- a file reference discovered in a tool result
      (``payload`` is ``{file_id?, filename?, download_url?, ...}``).
    * ``show_output`` -- wire 1.0.21 (additive): the explicit "render the
      file table now" signal for A2A peers (Scale OneEdge).  Emitted once
      per file-bearing tool result, AFTER that result's per-file
      ``artifact`` frames, aggregating them so the consumer can render a
      download table from this single self-contained frame.  ``payload``
      is ``{"reason", "run_id"?, "files": [<artifact dicts>]}`` where
      ``reason`` is one of ``user_requested`` / ``run_outputs`` /
      ``files_available`` (``artifact_edited`` / ``artifact_created``
      remain valid vocabulary but are no longer produced here --
      ENG-3925 maps edit/create harvests to ``user_requested``, the
      always-render reason, so an edited file surfaces in its own
      turn).  Consumers that key their file-table rendering on this
      frame render exactly when the agent surfaces deliverables --
      artifact edits, workflow outputs (final or mid-run), or any
      files the user asked to see.
    * ``input_issue``      -- ENG-4104 (additive): an uploaded file is
      wrong, partial, or missing required content. ``text`` is the
      user-facing message for the main chat / OneEdge LHS (never CoT).
      ``payload`` carries ``issue_kind``, ``filename?``,
      ``field_name?``, ``display: true``.
    * ``decision``         -- a structured clarifying question the user
      is to answer, emitted from inside ``ask_user_decision`` as the
      last act of a turn.  ``text`` is the question; ``payload``
      carries ``decision_id``, ``question``, ``options`` and
      ``allow_free_text``.  Nothing pauses: the turn ends normally and
      the answer arrives as the next turn's
      ``metadata.decision_response``.
    * ``canvas_mutation``  -- ENG-4753 (additive): the agent proposes a
      structural change to the workflow canvas (add / remove / configure
      node, connect / disconnect edge). ``payload`` is the mutation dict
      with at minimum ``event_type`` (one of ``add_node``,
      ``remove_node``, ``configure_node``, ``connect_nodes``,
      ``disconnect_nodes``). Events are buffered by the frontend during
      the turn; a confirm card appears at turn end so the user applies
      them in a single click.
    * ``canvas_run_workflow`` -- wire 1.0.27 (additive): the agent asked
      the frontend to kick off a run of the canvas's current (possibly
      unsaved) workflow via the ``run_workflow`` tool. ``payload`` is
      currently empty; the frontend dispatches the run client-side using
      whatever canvas state it holds. Refused by the tool (as a normal
      tool-result error, not this frame) when mutations were proposed
      earlier in the same turn and not yet applied.
    * ``final``            -- terminal event; ``result`` carries the
      full :class:`ChatTurnResult`. Always the last event yielded on
      success.
    """

    type: str
    text: Optional[str] = None
    tool_name: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    result: Optional[ChatTurnResult] = None


# Caps applied to tool-call args / tool-result payloads surfaced on the
# event stream. The full values still reach the LLM -- this only bounds
# what we mirror out to chat clients per frame.
_TOOL_EVENT_TEXT_CAP = 600


# Arg names whose values are credentials (e.g. the org MCP config tools'
# ``secret``). Redacted from the mirrored tool_call frames -- the tool itself
# still receives the real value.
_SENSITIVE_TOOL_ARG_KEYS = frozenset({"secret"})
_REDACTED_ARG_PLACEHOLDER = "[redacted]"


def _redact_sensitive_args(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (
                _REDACTED_ARG_PLACEHOLDER
                if k in _SENSITIVE_TOOL_ARG_KEYS and v is not None
                else _redact_sensitive_args(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_args(v) for v in value]
    return value


def _format_tool_args(part: Any) -> str:
    """Render a ToolCallPart's args as a compact, capped JSON-ish string.

    Credential-bearing args (``_SENSITIVE_TOOL_ARG_KEYS``) are redacted
    before the args are mirrored out to chat clients.
    """

    args = getattr(part, "args", None)
    try:
        if isinstance(args, str):
            args = json.loads(args)
        args = _redact_sensitive_args(args)
        rendered = json.dumps(args, default=str)
    except Exception:
        rendered = str(args)
        if any(key in rendered for key in _SENSITIVE_TOOL_ARG_KEYS):
            rendered = _REDACTED_ARG_PLACEHOLDER
    if rendered and len(rendered) > _TOOL_EVENT_TEXT_CAP:
        rendered = rendered[:_TOOL_EVENT_TEXT_CAP] + "…"
    return rendered or ""


# Orchestrator tools that START or RE-START a workflow run.  A ``run_id``
# in one of these tools' results is the run this chat turn kicked off, so we
# surface it as a structured field (B2).  Status/output/event-query tools
# also echo a ``run_id`` but for a run the turn merely INSPECTED, not
# started -- excluding them keeps the structured ``run_id`` unambiguous.
#
# ``execute_workflow`` is the builder surface's own run starter. The builder
# toolset no longer mounts it, but an agent's ``mcp_tool_allowlist`` can,
# and a run it schedules is as real as ``start_workflow``'s: leaving it out
# leaves the chat with no run card.
_RUN_INITIATING_TOOLS: frozenset = frozenset(
    {
        "start_workflow",
        "execute_workflow",
        "recover_failed_run",
        "rerun_from_node",
        "rerun_step_in_context",
        "rerun_workflow",
    }
)


def _run_initiating_tool(tool_name: str) -> bool:
    """True when this tool's result names a run the turn just started.

    Matches the builder's prefixed spelling (``wb_execute_workflow``) and
    the runtime function tools' bare spelling against the same list, the
    way ``_workflow_describing_tool`` does.
    """
    if not tool_name:
        return False
    return strip_builder_tool_prefix(tool_name) in _RUN_INITIATING_TOOLS


def _walk_tool_result(
    content: Any,
    visit: Callable[[Dict[str, Any]], Optional[str]],
    *,
    skip_keys: frozenset = frozenset(),
    _depth: int = 0,
) -> bool:
    """Depth-first walk of a tool result -- a dict, a list, or a
    JSON-encoded string of either -- calling ``visit`` on every dict
    node.  Shared by ``_extract_run_id_from_tool_result``,
    ``_extract_workflow_id_from_tool_result`` and ``_extract_artifacts``,
    which differ only in what ``visit`` does with a given dict:

    - Return ``"stop"`` to abort the walk entirely (a find-the-first-match
      search, e.g. run_id / workflow_id).
    - Return ``"skip"`` to accept this dict but not descend into its own
      fields (an artifact dict's fields aren't themselves candidate
      artifacts).
    - Return ``None`` to keep walking normally.

    ``skip_keys`` names subtrees never worth descending into (the
    artifact harvester's input-echo keys); the id extractors pass none.
    Depth-capped at 6 against pathological/cyclic payloads.

    Returns ``True`` iff some ``visit`` call returned ``"stop"``.
    """

    if _depth > 6:
        return False
    if isinstance(content, dict):
        action = visit(content)
        if action == "stop":
            return True
        if action == "skip":
            return False
        for k, v in content.items():
            if k in skip_keys:
                continue
            if _walk_tool_result(v, visit, skip_keys=skip_keys, _depth=_depth + 1):
                return True
    elif isinstance(content, list):
        for item in content:
            if _walk_tool_result(item, visit, skip_keys=skip_keys, _depth=_depth + 1):
                return True
    elif isinstance(content, str) and content.lstrip()[:1] in ("{", "["):
        try:
            parsed = json.loads(content)
        except Exception:
            return False
        return _walk_tool_result(parsed, visit, skip_keys=skip_keys, _depth=_depth + 1)
    return False


def _extract_run_id_from_tool_result(content: Any) -> Optional[str]:
    """Pull a ``run_id`` out of a run-initiating tool's result.

    Tool results arrive as a dict or a JSON-encoded string (the same
    shapes ``_extract_artifacts`` handles).  Returns the first non-empty
    ``run_id`` found, or ``None``.
    """

    match: Dict[str, Optional[str]] = {"run_id": None}

    def visit(node: Dict[str, Any]) -> Optional[str]:
        rid = node.get("run_id")
        if isinstance(rid, str) and rid:
            match["run_id"] = rid
            return "stop"
        return None

    _walk_tool_result(content, visit)
    return match["run_id"]


# Orchestrator tools whose result is the set of workflows the chat shows as
# cards below the reply.  Only the explicit ``show_workflows`` call puts a
# card in front of the user: the model then knows the cards are there and
# writes around them.  Lookups (``list_workflows``, ``describe_workflow``,
# ``get_workflow_definition``), builder calls, and run-starting tools are
# absent -- a build is shown by calling ``show_workflows`` for it, and a run
# is described by its own run card.
#
# Names are bare; ``_workflow_describing_tool`` runs the incoming name
# through ``strip_builder_tool_prefix`` before matching, so the prefix stays
# defined only in the toolset that applies it.
_WORKFLOW_DESCRIBING_TOOLS: frozenset = frozenset({"show_workflows"})

# Builder tools that put the new workflow's UUID at the TOP level of their
# result.  Their id rides under ``built_workflow_id``, never the card keys,
# so a build the model forgets to show still leaves its id on the frame.
_WORKFLOW_BUILDER_TOOLS: frozenset = frozenset(
    {
        "assemble_workflow",
        "build_workflow_from_image",
        "edit_workflow",
        "publish_workflow",
    }
)


def _extract_built_workflow_id(tool_name: str, content: Any) -> Optional[str]:
    """The UUID a builder tool's result names at its top level, else None.

    Top level only: ``get_workflow``-style results nest node ids that would
    title the turn with the wrong workflow.
    """
    if not tool_name or strip_builder_tool_prefix(tool_name) not in (
        _WORKFLOW_BUILDER_TOOLS
    ):
        return None
    if isinstance(content, str) and content.lstrip()[:1] == "{":
        try:
            content = json.loads(content)
        except Exception:
            return None
    if not isinstance(content, dict):
        return None
    for key in _WORKFLOW_ID_KEYS:
        value = content.get(key)
        if isinstance(value, str) and _coerce_surface_uuid(value) is not None:
            return value
    return None


def _workflow_describing_tool(tool_name: str) -> bool:
    """True when this tool's result is the workflows the chat shows as cards."""
    if not tool_name:
        return False
    return strip_builder_tool_prefix(tool_name) in _WORKFLOW_DESCRIBING_TOOLS


# Preferred first: ``show_workflows`` and ``describe_workflow`` return the
# slug as ``id`` and the Capitol UUID beside it, and only the UUID can be
# read back through the workflows API.
_WORKFLOW_ID_KEYS: tuple = ("capitol_workflow_id", "workflow_id", "id")


def _extract_workflow_id_from_tool_result(content: Any) -> Optional[str]:
    """Pull the Capitol workflow UUID out of a workflow-describing tool's
    result.

    Handles the same dict-or-JSON-string shapes as
    ``_extract_run_id_from_tool_result``.  Non-UUID values are skipped
    rather than returned: a slug reads back through no API, so passing one
    on would hand consumers an id they cannot resolve.
    """

    match: Dict[str, Optional[str]] = {"id": None}

    def visit(node: Dict[str, Any]) -> Optional[str]:
        for key in _WORKFLOW_ID_KEYS:
            value = node.get(key)
            if isinstance(value, str) and _coerce_surface_uuid(value) is not None:
                match["id"] = value
                return "stop"
        return None

    _walk_tool_result(content, visit)
    return match["id"]


def _extract_workflow_ids_from_tool_result(content: Any) -> List[str]:
    """The Capitol UUIDs of every workflow a ``show_workflows`` result
    shows, in card order and without repeats.

    Reads the top-level ``workflows`` list only.  A result without one is
    a refusal (``{"error": ..., "errors": [...]}``), and the ids inside
    ``errors`` are the ones the tool declined to show, so it yields none.
    """

    if isinstance(content, str) and content.lstrip()[:1] == "{":
        try:
            content = json.loads(content)
        except Exception:
            return []
    if isinstance(content, dict) and isinstance(content.get("workflows"), list):
        ids: List[str] = []
        for item in content["workflows"]:
            wid = (
                _extract_workflow_id_from_tool_result(item)
                if isinstance(item, dict)
                else None
            )
            if wid and wid not in ids:
                ids.append(wid)
        return ids
    return []


def _extract_sub_agents_from_tool_result(content: Any) -> List[Dict[str, Any]]:
    """Pull the ENG-3234 ``sub_agents`` roster out of a run-initiating
    tool's result (top level only -- that is where ``start_workflow`` /
    ``call_workflow`` place it).  Handles the same dict-or-JSON-string
    duality as ``_extract_run_id_from_tool_result``.  Returns ``[]``
    when absent or malformed.
    """

    if isinstance(content, str) and content.lstrip()[:1] in ("{", "["):
        try:
            content = json.loads(content)
        except Exception:
            return []
    if not isinstance(content, dict):
        return []
    roster = content.get("sub_agents")
    if not isinstance(roster, list):
        return []
    return [entry for entry in roster if isinstance(entry, dict)]


def _looks_like_artifact(obj: Dict[str, Any]) -> bool:
    """Duck-type a dict as a downloadable file reference.

    Matches the shapes produced by the ``download_file`` skill /
    ``request_upload_url`` Redis records / ``word_doc_chat``-style node
    outputs: anything carrying a non-empty ``download_url``, or a
    ``file_id`` paired with a filename-ish field.
    """

    if not isinstance(obj, dict):
        return False
    if obj.get("download_url"):
        return True
    if obj.get("file_id") and (obj.get("filename") or obj.get("file_name")):
        return True
    return False


# Result keys that describe a tool's INPUTS rather than anything the turn
# produced. The harvester walks every tool result, so without these skips a
# tool that merely reads a workflow back announces the caller's own saved
# template as a fresh artifact card in chat -- a file nobody asked to be
# delivered, sitting beside the run it is an input to.
_NON_DELIVERABLE_KEYS: frozenset = frozenset(
    {
        # ``get_workflow_output``'s on-request index of marker-suppressed
        # files, NOT part of the default deliverable set; the primary
        # ``files`` list is still scanned.
        "other_outputs",
        # ``describe_workflow``'s ``fields[].current_value``: a field's
        # saved value, which for a FILE input is the bound template.
        "current_value",
        # The JSON Schema beside those fields, which mirrors each saved
        # value again as a ``default``. Skipped as a subtree rather than by
        # the key, since ``default`` on its own is a plausible name for an
        # output port.
        "input_schema",
        # ``start_workflow``'s echo of the FILE inputs this launch bound,
        # there so the model can name them in its reply.
        "resolved_files",
        # The deliverables of a run that ALREADY finished, listed on
        # ``start_workflow``'s "already completed" refusal so the model can
        # point back at them. They were carded when that run finished, and
        # the refusal invites ``force_rerun=True`` in the same turn -- left
        # harvestable, an explicit "run it again" re-cards the previous
        # run's file beneath the new run's card. ``show_output`` is how the
        # model re-renders them deliberately.
        "completed_run_files",
    }
)


_ARTIFACT_PROJECTION_KEYS: Tuple[str, ...] = (
    "file_id",
    "filename",
    "file_name",
    "download_url",
    "content_type",
    "size_bytes",
    "expires_at",
    # Wire 1.0.19 (additive, §2.7): version lineage for
    # orchestrator-edited/created artifacts.
    "edited",
    "version",
    "parent_file_id",
    "root_file_id",
    "source_run_id",
    "label",
    "change_summary",
    "artifact_id",
    # Wire 1.0.20 (additive): user-consented cross-run
    # composition audit fields.
    "cross_run",
    "composed_from_run_ids",
    # Wire 1.0.23 (additive): promoted-row enrichment for
    # frontend cards -- populated by ``_enrich_promoted_artifacts`` when
    # ``artifact_id`` resolves to a product Artifact row (kept here so a
    # tool result that already carries them survives the harvest too).
    "title",
    "artifact_type",
    "thumbnail_status",
    # ENG-3854 (additive): file category -- ``"prompt"`` for exported
    # workflow prompts, ``"questionnaire"`` for questions files.
    "category",
    # Wire 1.0.30 (additive): Scale verbatim-render flags. Prompt and
    # questionnaire exports already set these on files_available; they
    # now survive harvest onto the artifact chat frame too.
    "prompt",
    "primary",
    "display",
)


def _extract_artifacts(content: Any) -> List[Dict[str, Any]]:
    """Recursively scan a tool result for file-reference dicts.

    Used to surface workflow outputs (e.g. generated Word docs) as
    first-class ``artifact`` events on the chat stream instead of
    leaving them buried inside tool-result JSON the FE would have to
    re-parse. Input echoes are skipped -- see ``_NON_DELIVERABLE_KEYS``.
    """

    found: List[Dict[str, Any]] = []

    def visit(node: Dict[str, Any]) -> Optional[str]:
        if not _looks_like_artifact(node):
            return None
        found.append(
            {
                k: node.get(k)
                for k in _ARTIFACT_PROJECTION_KEYS
                if node.get(k) is not None
            }
        )
        return "skip"

    _walk_tool_result(content, visit, skip_keys=_NON_DELIVERABLE_KEYS)
    return found


def _make_artifact_durable(art: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of one harvested artifact dict whose ``download_url``
    is a click-time-fetched link, never a session-bound one.

    An ``artifact`` chat frame is stored in chat history and clicked hours
    later, so a session-bound URL riding it (a presigned S3 URL dies with
    the signing pod's STS session ~1h under IRSA; a JWT ``/files/download``
    token embeds its own expiry) is guaranteed dead at click time -- the
    ENG-3528/3542 ExpiredToken defect class. The durable persist/resolve
    paths already mint ``/s3/proxy`` links, but the harvester scans EVERY
    tool result, so this is the frame-level backstop for any tool (or
    legacy payload) that still hands back a short-lived link.

    Delegates to ``url_refresh.refresh_presigned_urls(durable=True)`` --
    the exact walk ENG-3542 established for the ``get_workflow_output``
    read surface -- and fails open: on any error the artifact is emitted
    unchanged (a re-mint problem must never eat a deliverable).
    """
    try:
        from services.node_results.url_refresh import refresh_presigned_urls

        refreshed = refresh_presigned_urls(art, durable=True)
        if isinstance(refreshed, dict):
            return refreshed
    except Exception as exc:  # noqa: BLE001 - never make an artifact less usable
        logger.warning("artifact durable re-mint failed (fail-open): %s", exc)
    return art


async def _enrich_promoted_artifacts(
    arts: List[Dict[str, Any]], org_id: str
) -> List[Dict[str, Any]]:
    """Wire 1.0.23 (additive): enrich harvested ``artifact`` frames that
    reference a promoted product Artifact row (``artifact_id`` present) with
    ``title`` / ``artifact_type`` / ``thumbnail_status`` looked up from that
    row, so the frontend card renders without a per-frame detail round-trip.

    Fail-open by contract: frames without an ``artifact_id`` (transient
    files), non-UUID ids, rows not found in this org, or any lookup failure
    leave the frames unchanged (enrichment keys absent) -- this must never
    eat or delay a deliverable.  Mutates the dicts in place and returns the
    same list.
    """
    by_artifact_id: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
    for art in arts:
        raw_id = art.get("artifact_id")
        if not raw_id:
            continue
        try:
            artifact_uuid = uuid.UUID(str(raw_id))
        except (ValueError, AttributeError, TypeError):
            continue
        by_artifact_id.setdefault(artifact_uuid, []).append(art)
    if not by_artifact_id:
        return arts

    try:
        from sqlalchemy import select

        from db.models.artifact import Artifact

        session_factory = async_session_factory()
        async with session_factory() as session:
            result = await session.execute(
                select(Artifact).where(
                    Artifact.id.in_(list(by_artifact_id)),
                    Artifact.org_id == org_id,
                )
            )
            for row in result.scalars():
                for art in by_artifact_id.get(row.id, []):
                    art["title"] = row.title
                    art["artifact_type"] = getattr(row.type, "value", row.type)
                    art["thumbnail_status"] = getattr(
                        row.thumbnail_status, "value", row.thumbnail_status
                    )
    except Exception as exc:  # noqa: BLE001 - enrichment must never eat a frame
        logger.warning("artifact frame enrichment failed (fail-open): %s", exc)
    return arts


# Wire 1.0.21: tool -> ``show_output.reason`` mapping.  Every harvested
# file batch produces exactly one ``show_output`` frame; the reason tells
# the consumer WHY the table should render (and lets it style/label the
# table accordingly).  Unlisted file-bearing tools fall back to
# ``files_available`` so new tools are render-signalled by default.
_SHOW_OUTPUT_REASONS: Dict[str, str] = {
    # ENG-3925: an artifact edit/creation only ever happens because the
    # user asked for it, so its render signal is ``user_requested`` --
    # the one reason every consumer always renders. One Edge's card
    # renderer ignored the former ``artifact_edited``/``artifact_created``
    # reasons, so an edited file surfaced only after the user separately
    # asked to "see" it. Revision provenance is not lost: the per-file
    # ``artifact`` frame carries ``edited``/``version``/lineage keys, and
    # the source run's log still gets its ``files_available`` event with
    # reason ``artifact_edited``/``artifact_created``.
    "edit_artifact": "user_requested",
    "create_artifact": "user_requested",
    "get_workflow_output": "run_outputs",
    "get_node_output": "run_outputs",
    "start_workflow": "run_outputs",
    # Wire 1.0.22: the explicit show_output tool (normally handled by the
    # directive path in _build_explicit_show_output_batch; this entry is
    # the defensive fallback when its result harvests generically).
    "show_output": "user_requested",
    # ENG-3854: a prompt export only ever happens because the user asked
    # to see/edit the prompt -- consumers always render user_requested.
    "export_workflow_prompt": "user_requested",
    "export_workflow_questionnaire": "user_requested",
}
_SHOW_OUTPUT_DEFAULT_REASON = "files_available"

# Wire 1.0.22: tool -> ``show_output.scope`` mapping -- WHAT SET of files
# the frame's ``files[]`` represents, so the consumer can label/switch on
# it.  Closed vocabulary: ``all`` (every generated file of the run) /
# ``primary`` (post-[[primary-output]]-allowlist deliverables) /
# ``intermediate`` (the ENG-3566 marker-suppressed non-primary set) /
# ``single`` (exactly one file) / ``files`` (exactly the files in this
# frame, NO run-set claim).  Auto-harvest pairings:
#
# * ``edit_artifact`` / ``create_artifact`` -> ``single`` -- their result
#   is one persisted file (demoted to ``files`` defensively if a batch
#   ever carries a different count, so ``single`` always means one).
# * ``get_workflow_output`` / ``start_workflow`` -> ``primary`` -- their
#   harvested ``files`` lists are post-allowlist deliverables (the
#   ENG-3566 ``other_outputs`` are excluded from harvest by design).
# * everything else -> ``files``: an arbitrary tool result (including
#   ``get_node_output``, whose files are one node's outputs) makes no
#   claim about the run-level output set, and pretending ``all`` would
#   be dishonest.  ``files`` is the fallback scope by construction.
_SHOW_OUTPUT_SCOPES: Dict[str, str] = {
    "edit_artifact": "single",
    "create_artifact": "single",
    "get_workflow_output": "primary",
    "start_workflow": "primary",
    # ENG-3854: one prompt file per export (demoted defensively below).
    "export_workflow_prompt": "single",
    "export_workflow_questionnaire": "single",
}
_SHOW_OUTPUT_DEFAULT_SCOPE = "files"


def _extract_result_run_id(content: Any) -> Optional[str]:
    """Best-effort ``run_id`` pull from a tool result for ``show_output``.

    Checks the result's top-level ``run_id`` (``get_workflow_output`` /
    ``start_workflow`` shapes), then ``completed_run_id`` (the
    ``start_workflow`` already-completed rerun refusal deliberately avoids
    the ``run_id`` key so the stream pump does not fabricate a
    ``run_started`` for a finished run -- but its file batch still belongs
    to that completed run), tolerating the JSON-string encoding tool
    results frequently arrive in.  Returns ``None`` when absent -- the
    batch builder then falls back to the files' own ``source_run_id``.
    """
    obj = content
    if isinstance(obj, str) and obj.lstrip()[:1] in ("{", "["):
        try:
            obj = json.loads(obj)
        except Exception:
            return None
    if isinstance(obj, dict):
        rid = obj.get("run_id") or obj.get("completed_run_id")
        if rid:
            return str(rid)
    return None


def _stamp_display_true(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stamp ``display: true`` on a ``show_output`` frame's file entries.

    ENG-3940 follow-up: consumers applying the ENG-3897 unified "render
    iff ``display: true``" rule to every ``files[]`` list they see (Scale
    renders events and frames through one code path) skip entries without
    the key -- including edited artifacts. Paired with ``file_id`` the
    verdict lets the consumer mint a fresh durable link via
    ``files/{file_id}/download-url`` instead of trusting the emit-time
    ``download_url``.

    Callers stamp ONLY the frame flavors that are always-render by
    contract: ``reason: user_requested`` (explicit asks -- edits,
    creations, prompt exports, the show_output tool) and ``scope:
    primary`` (run deliverables). Generic harvests of arbitrary
    file-bearing tool results (e.g. ``get_node_output`` on an
    intermediate node) are deliberately NOT stamped: those files may be
    intermediates the user never asked to see, and declaring ``display:
    true`` there would bypass the user's show-intermediate toggle. An
    explicit pre-existing ``display`` value is preserved
    (server-computed verdicts win).
    """
    return [{**f, "display": f.get("display", True)} for f in files]


def _build_show_output_batch(
    tool_name: Optional[str],
    files: List[Dict[str, Any]],
    content: Any,
) -> Dict[str, Any]:
    """Assemble one ``show_output`` payload for a harvested file batch.

    Self-contained by design: ``files`` carries the same durable-URL
    artifact dicts already emitted as per-file ``artifact`` frames, so a
    consumer renders the download table from this single frame without
    correlating earlier stream traffic.
    """
    scope = _SHOW_OUTPUT_SCOPES.get(tool_name or "", _SHOW_OUTPUT_DEFAULT_SCOPE)
    if scope == "single" and len(files) != 1:
        # ``single`` is a promise of exactly one file; a defensive demotion
        # keeps the scope honest if an edit/create batch ever multi-files.
        scope = _SHOW_OUTPUT_DEFAULT_SCOPE
    reason = _SHOW_OUTPUT_REASONS.get(tool_name or "", _SHOW_OUTPUT_DEFAULT_REASON)
    # Always-render flavors carry the explicit verdict; generic harvests
    # leave the key absent so a display-rule consumer's own defaults (and
    # the user's show-intermediate toggle) stay in charge.
    if reason == "user_requested" or scope == "primary":
        files = _stamp_display_true(files)
    batch: Dict[str, Any] = {
        "reason": reason,
        "scope": scope,
        "files": files,
    }
    rid = _extract_result_run_id(content)
    if not rid:
        for f in files:
            if f.get("source_run_id"):
                rid = str(f["source_run_id"])
                break
    if rid:
        batch["run_id"] = rid
    return batch


def _extract_show_output_directive(content: Any) -> Optional[Dict[str, Any]]:
    """Parse an explicit ``show_output`` tool result into a directive.

    Wire 1.0.22: the explicit tool returns ``{"show_output": true,
    "scope": ..., "files": [...], "run_id"?}`` (see
    ``services.orchestrator.tools.show_output_tools``).  Tolerates the
    JSON-string encoding tool results frequently arrive in (same duality
    as ``_extract_result_run_id``).  Returns ``None`` when the result is
    not a well-formed directive with at least one file dict -- error
    results (``{"error": ...}``) then simply produce no frame.
    """
    obj = content
    if isinstance(obj, str) and obj.lstrip()[:1] in ("{", "["):
        try:
            obj = json.loads(obj)
        except Exception:
            return None
    if not isinstance(obj, dict) or obj.get("show_output") is not True:
        return None
    scope = obj.get("scope")
    files = obj.get("files")
    if not isinstance(scope, str) or not isinstance(files, list):
        return None
    file_dicts = [f for f in files if isinstance(f, dict)]
    if not file_dicts:
        return None
    directive: Dict[str, Any] = {"scope": scope, "files": file_dicts}
    if obj.get("run_id"):
        directive["run_id"] = str(obj["run_id"])
    return directive


def _build_explicit_show_output_batch(
    directive: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble the ``show_output`` payload for an explicit tool call.

    Wire 1.0.22: ``reason: "user_requested"`` + the server-resolved scope
    and file set from the directive.  Every file is durably re-minted
    (``_make_artifact_durable``) because the single-file resolution path
    (Redis artifact registry / ``node_result_file`` presign) can hand
    back session-bound URLs.  ALWAYS emitted by the caller -- an explicit
    render request re-signals files even if they were already surfaced
    this turn (only the per-file ``artifact`` frames stay per-turn
    deduped; this frame is self-contained either way).
    """
    batch: Dict[str, Any] = {
        "reason": "user_requested",
        "scope": directive["scope"],
        "files": _stamp_display_true(
            [_make_artifact_durable(dict(f)) for f in directive["files"]]
        ),
    }
    if directive.get("run_id"):
        batch["run_id"] = directive["run_id"]
    return batch


# Fallback table label when the review link carries no friendly filename.
_REVIEW_DOCUMENT_FALLBACK_NAME = "Document under review"


async def _resolve_review_document_url(
    pending: Dict[str, Any], session_id: Any
) -> Optional[str]:
    """Return the parked chat node's reviewed-document link, re-minted durable.

    Only a Redis review record for the exact pending node counts, so a stale
    record left by another node in the session is never surfaced (ENG-3543).
    """
    if not pending.get("node_id") or not session_id:
        return None
    from services.node_results.url_refresh import refresh_presigned_urls
    from services.redis.client import RedisServiceSingleton

    doc = await RedisServiceSingleton().get_chat_review_document(str(session_id))
    if not doc or not doc.get("review_url"):
        return None
    if str(doc.get("node_id")) != str(pending["node_id"]):
        return None
    stored_url = str(doc["review_url"])
    # The stored URL is a mid-run link that can expire during a review.
    try:
        refreshed = refresh_presigned_urls({"data": stored_url}, durable=True)
    except Exception as exc:  # noqa: BLE001 - best-effort refresh
        logger.warning("review link refresh failed (non-fatal): %s", exc)
        return stored_url
    return str(refreshed.get("data") or stored_url)


def _review_document_meta(review_url: str) -> Tuple[str, Optional[str]]:
    """Return the reviewed document's ``(filename, content_type)`` from its link.

    Our proxy links carry both; any other link falls back to its basename.
    """
    from services.node_results.url_refresh import (
        is_hmac_proxy_url,
        is_jwt_proxy_url,
        proxy_url_file_hints,
    )

    filename, content_type = proxy_url_file_hints(review_url)
    if not filename and not (
        is_hmac_proxy_url(review_url) or is_jwt_proxy_url(review_url)
    ):
        # A proxy link's last path segment is a token, never a file name.
        filename = unquote(posixpath.basename(urlsplit(review_url).path))
    return filename or _REVIEW_DOCUMENT_FALLBACK_NAME, content_type


def _build_review_document_batch(
    review_url: str, run_id: Optional[str]
) -> Dict[str, Any]:
    """Shape the document under HITL review as a one-row ``show_output`` table.

    A review checkpoint hands the document over as a download table, never as
    a link in the model's prose.
    """
    filename, content_type = _review_document_meta(review_url)
    review_file: Dict[str, Any] = {"filename": filename, "download_url": review_url}
    if content_type:
        # Both spellings ride show_output rows: artifact harvests send
        # content_type, run-output tables send mime_type.
        review_file["content_type"] = content_type
        review_file["mime_type"] = content_type
    if run_id:
        review_file["source_run_id"] = str(run_id)
    batch: Dict[str, Any] = {
        "reason": "user_requested",
        "scope": "single",
        "files": _stamp_display_true([review_file]),
    }
    if run_id:
        batch["run_id"] = str(run_id)
    return batch


def _build_fallback_review_document_batch(
    review_url: str, run_id: Optional[str]
) -> Dict[str, Any]:
    """Return a minimal review-document table when metadata extraction fails."""
    review_file: Dict[str, Any] = {
        "filename": _REVIEW_DOCUMENT_FALLBACK_NAME,
        "download_url": review_url,
    }
    if run_id:
        review_file["source_run_id"] = str(run_id)
    batch: Dict[str, Any] = {
        "reason": "user_requested",
        "scope": "single",
        "files": _stamp_display_true([review_file]),
    }
    if run_id:
        batch["run_id"] = str(run_id)
    return batch


def _assign_review_document_surface(
    pending: Dict[str, Any],
    review_url: Optional[str],
    *,
    render_files: bool,
    delivers_frames: bool,
    run_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Choose how this turn hands over the document, and return its table.

    A table is emitted only when one will actually reach this user this
    turn: a review record already existed at grounding, the agent renders
    files in chat, and this caller receives ``show_output``. Otherwise the
    prose link stays on ``pending`` — except events-only agents, whose own
    UI already shows the document.

    Args:
        pending: The turn's open-checkpoint dict. Mutated with the surface.
        review_url: Durable link from the review record, or None when the
            record did not exist yet (the checkpoint-open turn).
        render_files: Agent ``render_files_in_chat`` flag.
        delivers_frames: Whether this caller receives ``show_output``.
        run_id: Run the table row is attributed to.

    Returns:
        The one-row ``show_output`` batch, or None when no table goes out.
    """
    if not review_url:
        return None
    if render_files and delivers_frames:
        try:
            batch = _build_review_document_batch(review_url, run_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("review document table build failed: %s", exc)
            batch = _build_fallback_review_document_batch(review_url, run_id)
        if batch is not None:
            pending["review_document_surface"] = SURFACE_TABLE
            pending.pop("review_url", None)
            return batch
    if not render_files:
        pending["review_document_surface"] = SURFACE_PANEL
        pending.pop("review_url", None)
        return None
    pending["review_document_surface"] = _SURFACE_LINK
    pending["review_url"] = review_url
    return None


def _last_assistant_text(prior_history: Optional[List[Any]]) -> Optional[str]:
    """The text of the last assistant turn in ``prior_history``, or ``None``."""
    for entry in reversed(prior_history or []):
        if isinstance(entry, dict):
            if entry.get("role") == "assistant" and isinstance(
                entry.get("content"), str
            ):
                return entry["content"]
            continue
        if isinstance(entry, ModelResponse):
            text = "".join(
                part.content for part in entry.parts if isinstance(part, TextPart)
            )
            if text:
                return text
    return None


def _last_reply_proposals_record(prior_history: Optional[List[Any]]) -> Optional[str]:
    """The canvas-edit record of the reply right before this turn, if the
    history ends on one; it prefixes this turn's prompt, since there is no
    later user turn in the history to carry it."""
    if not prior_history:
        return None
    last = prior_history[-1]
    if isinstance(last, dict) and last.get("role") == "assistant":
        return render_proposals_record(last.get("proposals"))
    return None


def _coerce_prior_history(
    prior_history: Optional[List[Any]],
    *,
    label_contexts: bool = False,
) -> Optional[List[ModelMessage]]:
    """Rehydrate Redis-shaped history dicts into pydantic-ai ModelMessages.

    The conversation-history Redis cache (``api.a2a_gateway.
    conversation_history``) stores turns as plain
    ``{"role": "user"|"assistant", "content": str}`` dicts to keep the
    payload small and free of ThinkingPart-preservation footguns. But
    pydantic-ai's ``agent.run(message_history=...)`` requires actual
    ``ModelMessage`` instances; if it sees anything else it **silently
    drops the entry** -- there is no coercion, no validation error.

    That silent drop was ENG-2338: turn-1 pairs landed in Redis under
    the right key, ``get_history`` read them back fine, but the
    orchestrator passed the raw dicts to ``agent.run`` and pydantic-ai
    ignored them. The LLM saw nothing, "forgot" the codeword, and the
    bug report (with the right caller-side wire shape) made the read
    look broken when in fact it was working correctly.

    Coercion rules:
      * ``ModelMessage`` instances pass through unchanged (forward-compat
        for callers that already construct typed messages).
      * ``{"role": "user", "content": str}`` -> ``ModelRequest(parts=[UserPromptPart])``.
        With ``label_contexts`` (the operator chat), a ``context`` phrase
        that differs from the previous user turn's is prefixed to the content, so the model can see where the user
        moved from one node (or workflow, or artifact) to another --
        the transcript's markers never reach this cache.
      * ``{"role": "assistant", "content": str}`` -> ``ModelResponse(parts=[TextPart])``.
        A ``proposals`` list on the entry (what the reply queued through
        canvas tools, or nothing) is rendered on the NEXT user turn, in
        the same spot as the context label; the last reply's record goes
        on this turn's prompt (``_last_reply_proposals_record``).
      * Anything else is dropped (defensive -- malformed cache entries
        should not bring down the chat turn).

    Returns ``None`` when the input is ``None`` or every entry was
    dropped, so ``agent.run`` short-circuits to its "first turn"
    behaviour rather than seeing an empty list.
    """

    if not prior_history:
        return None

    out: List[ModelMessage] = []
    last_context: Optional[str] = None
    pending_record: Optional[str] = None
    for entry in prior_history:
        if isinstance(entry, (ModelRequest, ModelResponse)):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        content = entry.get("content")
        if not isinstance(content, str):
            continue
        if role == "user":
            if pending_record:
                content = f"{pending_record}\n\n{content}"
                pending_record = None
            turn_context = entry.get("context") if label_contexts else None
            if isinstance(turn_context, str) and turn_context:
                if turn_context != last_context:
                    content = f"[Context: {turn_context}]\n\n{content}"
                last_context = turn_context
            out.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            pending_record = render_proposals_record(entry.get("proposals"))
            out.append(ModelResponse(parts=[TextPart(content=content)]))
        # Silently drop system / tool / other roles -- the cache layer
        # only ever writes user/assistant.

    return out or None


# Strong references to in-flight turn-end maintenance tasks (tier-0
# facts write + tier-2 history fold). asyncio only keeps weak refs to
# tasks; without this set a fire-and-forget task can be GC'd mid-run.
_TURN_END_TASKS: set = set()


def _schedule_turn_end_maintenance(
    *,
    context: OrchestratorContext,
    user_message: str,
    result: ChatTurnResult,
    model_id: Optional[str],
    fallback_tool_parts: Optional[List[Any]] = None,
    trace_context: Optional[Dict[str, str]] = None,
) -> None:
    """Kick off tiered-history maintenance for a finished turn.

    Fire-and-forget: runs AFTER the final frame is produced so neither
    the tier-0 facts write nor a tier-2 summary fold ever blocks the
    reply. Best-effort at every layer -- scheduling failures (no
    running loop in odd test harnesses) and task failures are logged
    and swallowed; chat behaviour degrades to the legacy history path.

    ``fallback_tool_parts``: tool-call / tool-return parts captured
    live off the stream. Used for fact extraction when
    ``result.all_messages`` is empty -- the usage-limit ending builds
    its terminal ChatTurnResult without messages, but the capped
    turn's already-executed tools (consent grants, run observations)
    must still reach tier 0.
    """
    if not context.context_id:
        return
    try:
        if not settings.A2A_HISTORY_TIERED_ENABLED:
            return
    except Exception:  # pragma: no cover - defensive
        return
    try:
        from types import SimpleNamespace

        from api.a2a_gateway.conversation_history import record_turn_end

        messages: List[Any] = result.all_messages
        if not messages and fallback_tool_parts:
            # Fact extraction walks ``msg.parts`` -- wrap the loose
            # parts in one message-shaped carrier.
            messages = [SimpleNamespace(parts=list(fallback_tool_parts))]

        # ENG-5473: read the turn's trace context NOW, synchronously -- the
        # stream's ``finally`` clears it once the generator closes, which can
        # happen before the task below gets its first tick.  A caller that
        # schedules AFTER that ``finally`` (the sync budget-timeout path)
        # passes the value it captured during the drain instead.
        turn_trace_context = (
            trace_context if trace_context is not None else context.turn_trace_context
        )

        async def _attributed_turn_end() -> None:
            # ENG-5473: the tier-2 fold is an LLM call made after the turn's
            # span has closed, in the pump's context.  Re-enter the same
            # identity so its spend lands on the conversation's Langfuse
            # session, nested in the turn's trace under its own observation
            # name so it is not mistaken for a model turn.
            with operator_chat_maintenance_telemetry(
                context,
                model_id=model_id,
                trace_context=turn_trace_context,
            ):
                await record_turn_end(
                    org_id=context.org_id,
                    agent_id=context.agent_id,
                    context_id=context.context_id,
                    user_message=user_message,
                    turn_artifacts=result.artifacts,
                    started_run_id=result.run_id,
                    all_messages=messages,
                    decision_response=context.decision_response,
                    model_id=model_id,
                )

        task = asyncio.create_task(_attributed_turn_end())
        _TURN_END_TASKS.add(task)
        task.add_done_callback(_TURN_END_TASKS.discard)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "orchestrator: could not schedule turn-end history maintenance "
            "for context %s: %s",
            context.context_id,
            exc,
        )


def _make_compaction_notifier(
    context: OrchestratorContext,
) -> Callable[[CompactionEvent], None]:
    """Say so, while the summariser runs.

    Compaction is the longest stretch of a turn that explains itself
    least: a multi-second provider round-trip sitting between the
    user's message and the first token, with no tool call on the wire
    to account for it. The agent nodes have surfaced it since it was
    built; the chat orchestrator attached the same capability but never
    passed a callback, so on this path it stayed silent.

    Wording comes from :func:`format_compaction_event` so chat and the
    node event panel describe a compaction identically.

    ``notify_status`` suits what the capability wants from a callback:
    synchronous, and fire-and-forget by construction, because progress
    must never be able to break the turn it is describing. It no-ops
    while the turn's queue is unset, which covers the window between
    building the capability and starting the producer -- compaction
    cannot run before the model does.
    """

    def notify(event: CompactionEvent) -> None:
        context.notify_status(format_compaction_event(event))

    return notify


async def run_chat_turn_stream(
    *,
    context: OrchestratorContext,
    user_message: str,
    prior_history: Optional[List[Any]] = None,
) -> AsyncGenerator[ChatStreamEvent, None]:
    """Run one chat turn, yielding incremental :class:`ChatStreamEvent`s.

    Streaming counterpart of :func:`run_chat_turn` (which is now a thin
    drain of this generator).  Uses ``agent.iter()`` so consumers see:

    * assistant text deltas as the model emits them,
    * tool calls / tool results as the orchestrator executes them,
    * ``artifact`` events for any file references discovered in tool
      results (e.g. a workflow's generated Word doc),
    * one terminal ``final`` event carrying the full ``ChatTurnResult``.

    Exceptions from the underlying run propagate to the consumer after
    any already-yielded events; callers map them to their transport's
    failure shape (``_emit_failed`` on A2A, an ``error`` SSE frame on
    the v1 HTTP route).

    ``prior_history`` accepts either pydantic-ai ``ModelMessage``
    instances (preserving thinking / tool-call parts) **or** plain
    ``{"role", "content"}`` dicts as written by
    ``api.a2a_gateway.conversation_history``. Rehydration happens via
    ``_coerce_prior_history``; without this, dict-shaped entries were
    silently dropped by pydantic-ai and multi-turn chat effectively
    never replayed (ENG-2338).
    """

    workflows = await _resolve_workflows_for_context(context)
    peer_agents = _resolve_peer_agents_for_context(context)
    function_tools, mcp_toolsets = _build_toolset(context)
    # Agent-allowlisted MCP tools (``mcp_tool_allowlist`` on the agent
    # record): resolved against the org-nodes catalog and mounted as
    # filtered toolsets. Async (catalog fetch on cold cache), fail-open --
    # so it rides here rather than inside the sync ``_build_toolset``.
    if context.agent_record.get("mcp_tool_allowlist"):
        mcp_toolsets.extend(await make_mcp_allowlist_toolsets(context))

    # Dynamic mounting: rebuild this conversation's persisted run-tool
    # mounts (Redis descriptors) with the CURRENT user's credentials so
    # tools mounted on an earlier turn are still callable now. Async
    # (rebind + MCP connect), fail-open per mount -- rides here rather
    # than inside the sync ``_build_toolset``, like the allowlist attach.
    from services.orchestrator.tools import premount_persisted_mounts

    await premount_persisted_mounts(context)

    # Same mount path as load_platform_tools, so tags and natural-language
    # loads share one set of tools.
    grounded_references: List[Any] = []
    grounded_tools: List[str] = []
    if context.collection_references:
        if _collections_toolsets_attached(mcp_toolsets):
            grounded_references = list(context.collection_references)
        elif _mount_broker_attached(mcp_toolsets):
            from services.orchestrator.tools.platform_tool_catalog import (
                mount_tagged_collection_servers,
            )

            (
                grounded_references,
                grounded_tools,
            ) = await mount_tagged_collection_servers(
                context, context.collection_references
            )

    # Skill mounts persist the same way (descriptors in Redis, never
    # bytes). Registration is lazy -- no download/zip happens until a
    # sandbox actually installs the skill this turn.
    if context.agent_record.get("enable_skills", True):
        from services.orchestrator.tools import premount_persisted_skills

        await premount_persisted_skills(context)

    system_prompt = build_system_prompt(
        agent_record=context.agent_record,
        workflows=workflows,
        peer_agents=peer_agents,
        operator_chat=context.operator_chat,
        capability_flags={
            "enable_workflow_runtime": bool(
                context.agent_record.get("enable_workflow_runtime", True)
            ),
            "enable_workflow_canvas_editing": bool(
                context.agent_record.get("enable_workflow_canvas_editing", True)
            ),
            # Mirrors ``_build_toolset``'s ``canvas_mutations_available``
            # -- when true, ``update_workflow_prompt`` is absent from the
            # mounted toolset, so the "Workflow prompt requests" block
            # must not send the model there. A locked canvas keeps this
            # False (canvas mutations are stripped down to a locked
            # notice there too), so the persistent-tool guidance stays
            # the fallback rather than pointing at two dead ends
            # (ENG-5106 Bugbot follow-up).
            "workflow_canvas_editing_active": bool(
                context.agent_record.get("enable_workflow_canvas_editing", True)
                and getattr(context.surface_context, "surface", None)
                == "workflow_canvas"
                and not getattr(context.surface_context, "is_locked", False)
            ),
            "enable_a2a_outbound": bool(
                context.agent_record.get("enable_a2a_outbound", True)
            ),
            # Grounded from what actually mounted, not the raw record --
            # the factory fails closed (kill-switch, tokenless turns), and
            # the flag must not promise tools the turn doesn't have.
            "enable_workflow_authoring": _builder_toolset_attached(mcp_toolsets),
            "enable_context_tools": _collections_toolsets_attached(mcp_toolsets),
            "enable_context_graphs": _context_graph_toolset_attached(mcp_toolsets),
            "enable_labels": bool(context.agent_record.get("enable_labels", False)),
            # Must track the tool gate above: a prompt claiming scheduling
            # is unavailable while the tools are attached (or the reverse)
            # is worse than either state on its own.
            "enable_scheduling": bool(
                context.agent_record.get("enable_scheduling", True)
            ),
            "e2b_enabled": bool(context.agent_record.get("e2b_enabled", True)),
            # ENG-3849: chat-wire file rendering (default ON). OFF flips the
            # prompt guidance to events-only file delivery.
            "render_files_in_chat": bool(
                context.agent_record.get("render_files_in_chat", True)
            ),
            # Grounded from what actually attached: the memory tool
            # factory fails closed on turns without a resolved user
            # identity, and the flag must not promise tools (or recall)
            # the turn doesn't have.
            "memory_enabled": bool(
                context.agent_record.get("memory_enabled", False) and context.user_id
            ),
            "enable_skills": bool(context.agent_record.get("enable_skills", True)),
            # ENG-5459: evals toggle (default ON = eval nodes run, the
            # pre-toggle behaviour). OFF makes runs started by this agent
            # skip Evaluations nodes and adds a prompt section instructing
            # the model to say so and offer on-demand evals.
            "run_evals_by_default": bool(
                context.agent_record.get("run_evals_by_default", True)
            ),
            "allow_synthetic_data_generation": synthetic_data_generation_allowed(
                context.agent_record
            ),
        },
    )

    # Cross-session per-user memory: inject the user's saved standing
    # instructions as a grounded block right after the assembled prompt.
    # Best-effort by contract -- a storage failure logs and skips the
    # block (the turn runs cold), never the turn itself.
    if context.agent_record.get("memory_enabled", False) and context.user_id:
        from services.orchestrator.memory import load_memory_block

        memory_block = await load_memory_block(
            org_id=context.org_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            retention_days=context.agent_record.get("memory_retention_days", 0),
        )
        if memory_block:
            system_prompt = f"{system_prompt}\n\n{memory_block}"

    # Surface-context grounding (wire 1.0.23, flag-gated, default off):
    # when the chat client told us what the user is looking at (artifact
    # viewer, canvas + focused node), append a grounded block so
    # unqualified asks ("change the title") resolve against the viewed
    # entity. Fail-open by contract -- a resolution failure logs and
    # skips the block, never the turn.
    if settings.A2A_SURFACE_CONTEXT_GROUNDING_ENABLED and context.surface_context:
        surface_block = await _build_surface_context_block(context)
        logger.info(
            "orchestrator: surface grounding for context %s: surface=%s block_built=%s",
            context.context_id,
            getattr(context.surface_context, "surface", None),
            bool(surface_block),
        )
        if surface_block:
            system_prompt = f"{system_prompt}\n\n{surface_block}"

    # The reviewed document's table, when a document-review checkpoint is open.
    review_document_batch: Optional[Dict[str, Any]] = None
    # Read after the block below too; a grounding failure leaves it unset.
    active_run_summary: Optional[Dict[str, Any]] = None
    # Run-context retention: resolve the conversation's active run + its
    # pending checkpoint server-side and append it to the system prompt as a
    # trailing, authoritative block (closest to the user message in the
    # attention window). This is what stops a bare "approved" on a later turn
    # from spawning a duplicate run instead of resolving the pending
    # node.input_required (OneEdge "forgets run context"). Best-effort -- a
    # failure here must never break the chat turn.
    try:
        from services.orchestrator.tools.workflow_tools import (
            resolve_conversation_run_context,
        )

        active_run_summary = await resolve_conversation_run_context(
            org_id=context.org_id,
            agent_id=context.agent_id,
            context_id=context.context_id,
        )
        # ENG-3543: hand over the reviewed document on the surface that will
        # actually reach this user this turn. A table only when a record
        # already existed (the park writes it inside the model loop, so the
        # opening turn has none) and this caller receives frames. Otherwise
        # keep the prose link. Until a persisted review record exists, do not
        # infer owned delivery for events-only callers from capability flags
        # alone; they flip to panel ownership only after the review document is
        # concretely resolved for this pending node.
        _render_files = bool(
            (context.agent_record or {}).get("render_files_in_chat", True)
        )
        _pending_ctx = (active_run_summary or {}).get("pending") or {}
        _review_url = await _resolve_review_document_url(
            _pending_ctx, (active_run_summary or {}).get("session_id")
        )
        review_document_batch = _assign_review_document_surface(
            _pending_ctx,
            _review_url,
            render_files=_render_files,
            delivers_frames=bool(getattr(context, "delivers_show_output", True)),
            run_id=(active_run_summary or {}).get("run_id"),
        )
        # Tool results later this turn must agree with that surface. No
        # record yet still withholds for events-only (their UI shows the
        # event) and keeps the link for everyone else.
        _surface = _pending_ctx.get("review_document_surface")
        context.withhold_review_link = _surface in (
            SURFACE_TABLE,
            SURFACE_PANEL,
        )
        active_run_block = _format_active_run_block(
            active_run_summary, operator_chat=bool(context.operator_chat)
        )
        if active_run_block:
            system_prompt = f"{system_prompt}\n\n{active_run_block}"
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "orchestrator: active-run grounding failed for context %s: %s",
            context.context_id,
            exc,
        )

    # The operator as the hub: everything waiting on this user across all
    # their runs, and what finished lately, whichever chat started the run.
    # Bounded clarification countdowns are held for the length of the turn,
    # since the answer only reaches the node once the tool is called.
    paused_countdowns: List[str] = []
    if context.operator_chat and context.user_id:
        try:
            from services.operator_relay.countdown import pause_pending_countdowns
            from services.operator_relay.grounding import build_inbox_block

            inbox_block = await build_inbox_block(
                org_id=context.org_id,
                user_id=context.user_id,
                context_id=context.context_id,
                # When the active-run block above carries this run's
                # checkpoint and what to do about it, listing it again with
                # its own instructions would give the model two answers.
                # When it found no checkpoint, the inbox is the only place
                # that knows the run is waiting.
                active_run_id=(
                    active_run_summary.get("run_id")
                    if active_run_summary and active_run_summary.get("pending")
                    else None
                ),
            )
            if inbox_block:
                system_prompt = f"{system_prompt}\n\n{inbox_block}"
                paused_countdowns = await pause_pending_countdowns(
                    org_id=context.org_id, user_id=context.user_id
                )
        except Exception as exc:  # noqa: BLE001 - never the turn
            logger.warning(
                "orchestrator: inbox grounding failed for context %s: %s",
                context.context_id,
                exc,
            )

    # Tier-0 pinned session facts (cross-turn context compaction): a
    # structured, never-evicted record of this conversation's runs,
    # artifacts, uploads, and consent decisions, maintained at turn end
    # (see ``_schedule_turn_end_maintenance``). Rendered system-adjacent
    # so identifiers and consent state survive any transcript eviction.
    # Best-effort -- a failure here must never break the chat turn.
    try:
        if settings.A2A_HISTORY_TIERED_ENABLED:
            from ai.agentic.context.session_facts import render_facts_block
            from api.a2a_gateway import conversation_history as _a2a_history

            _facts = await _a2a_history.get_session_facts(
                org_id=context.org_id,
                agent_id=context.agent_id,
                context_id=context.context_id or "",
            )
            _facts_block = render_facts_block(_facts)
            if _facts_block:
                system_prompt = f"{system_prompt}\n\n{_facts_block}"
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "orchestrator: session-facts grounding failed for context %s: %s",
            context.context_id,
            exc,
        )

    if context.collection_references:
        collections_block = _format_collection_references_block(
            grounded_references, grounded_tools
        )
        if collections_block:
            system_prompt = f"{system_prompt}\n\n{collections_block}"
        if len(grounded_references) < len(context.collection_references):
            logger.info(
                "orchestrator: %d of %d tagged collection(s) for context %s "
                "not grounded -- their server did not mount",
                len(context.collection_references) - len(grounded_references),
                len(context.collection_references),
                context.context_id,
            )

    # The answer to a question a previous turn asked with
    # ``ask_user_decision``, grounded last so it sits closest to the user
    # message it belongs to. Omitted entirely on ordinary turns.
    decision_block = _format_decision_response_block(context.decision_response)
    if decision_block:
        system_prompt = f"{system_prompt}\n\n{decision_block}"

    # Per-conversation model override (wire 1.0.23, flag-gated, default
    # off). Invalid / retired overrides fall back to the agent-record
    # model and surface a ``status`` frame once the stream opens -- the
    # turn never fails on a bad override, and the override is never
    # written back to the agent record.
    requested_override = context.model_override
    resolved_model_id = _resolve_model(
        context.agent_record, override=requested_override
    )
    override_fallback_notice: Optional[str] = None
    if requested_override and _resolve_valid_override(requested_override) is None:
        override_fallback_notice = (
            f"Model {requested_override} unavailable, using {resolved_model_id}"
        )
        logger.info(
            "orchestrator: %s (context %s)",
            override_fallback_notice,
            context.context_id,
        )
    agent_model = _build_agent_model(resolved_model_id)
    _chay_thu_ten_gui = _chay_thu_dat_muc(context, agent_model, resolved_model_id)   # [ghi chú] mức suy luận của trợ lý dựng bản claude ⇒ đổi tên gửi trên dây

    # ENG-3004: bound the turn's context.  Chat-orchestrator turns
    # accumulate tool results (get_workflow_events, workflow outputs)
    # unbounded within a single turn, and prior-history replay adds up
    # to 50 cached turns on top -- the exact path that sent >200k-token
    # prompts straight to Anthropic ("prompt is too long" 400s in the
    # Jul 6 API-pod logs).  Same capability the agent nodes and the
    # pydantic-ai skills service already attach: preflight summarization
    # near the model's window + one-shot reactive recovery on a real
    # context-length 400.  ``None`` (unknown model) keeps behaviour
    # unchanged, matching the nodes' opt-in contract.  Keyed off the
    # registry id: ``agent_model``'s name is the provider's wire id, which
    # changes with provider routing (Bedrock-primary, residency).
    compaction_capability = build_capability_for_model(
        resolved_model_id,
        on_compaction=_make_compaction_notifier(context),
        orgid=context.org_id,
    )

    # Compaction can't rescue a single oversized tool result (it keeps the
    # recent tail verbatim), so bound each result as it enters the turn
    # with the agent nodes' offload guard. With the turn's E2B sandbox the
    # full result goes to a file the model reads back through gated
    # ``sandbox_*`` tools; without one it is truncated.
    from core.config.ai import get_context_budget
    from services.orchestrator.tools.artifact_tools import (
        _ensure_turn_sandbox,
        _turn_lock,
    )
    from services.sandbox.tool_result_offload import (
        ToolResultOffloadCapability,
        ToolResultOffloader,
        build_exploration_toolset,
    )

    # The offloader clamps the cap to a share of the model's safe input
    # window, so a 200k-token model gets less than a 1M-token one. A model
    # missing from the registry has no known window and no compaction, so
    # it gets the smaller chat limit instead of an assumed large window.
    _budget = get_context_budget(resolved_model_id)
    _offload_to_sandbox = bool(
        settings.TOOL_RESULT_OFFLOAD_ENABLED
        and settings.E2B_API_KEY
        and context.agent_record.get("e2b_enabled", True)
    )
    _tool_result_cap = (
        settings.A2A_CHAT_TOOL_RESULT_OFFLOAD_CHARS
        if _offload_to_sandbox
        else settings.A2A_CHAT_TOOL_RESULT_MAX_CHARS
    )
    if _budget is None:
        _tool_result_cap = min(
            _tool_result_cap,
            settings.A2A_CHAT_TOOL_RESULT_MAX_CHARS_UNREGISTERED_MODEL,
        )

    async def _offload_sandbox():
        # Same lock as the artifact tools, so the turn gets one sandbox.
        async with _turn_lock(context):
            return await _ensure_turn_sandbox(context)

    _ensure_sandbox = _offload_sandbox if _offload_to_sandbox else None
    _offloader = ToolResultOffloader(
        ensure_sandbox=_ensure_sandbox,
        safe_input_window=_budget.safe_input_window if _budget else None,
        cap_chars=_tool_result_cap,
    )
    capabilities: list = [
        ToolResultOffloadCapability(
            offloader=_offloader,
            exploration_toolset=(
                build_exploration_toolset(_offloader, _ensure_sandbox)
                if _ensure_sandbox
                else None
            ),
        )
    ]
    if compaction_capability is not None:
        capabilities.append(compaction_capability)

    # ``instructions=``, never ``system_prompt=``: pydantic-ai adds
    # ``system_prompt`` parts only when ``message_history`` is empty, and
    # every door here replays prior history (Redis cache or platform-api
    # transcript, coerced to user/assistant parts only), so a
    # ``system_prompt=`` agent runs every follow-up turn with no prompt at
    # all -- no persona or safety rules, no grounded workflow list, no
    # active-run block, no session facts.  This applies to the Operator
    # and to external A2A chat (EY / OneEdge) alike.
    # ``instructions`` ride every request and land in the provider's
    # ``system`` field (Anthropic and Bedrock Converse), where the model
    # builders' ``*_cache_instructions`` settings cache them.
    agent = Agent(
        model=agent_model,
        instructions=system_prompt,
        # ENG-5473: names the pydantic-ai spans (``invoke_agent operator-chat``
        # / ``gen_ai.agent.name``) so chat turns are recognisable in Langfuse
        # instead of the anonymous default.
        name=langfuse_identity.OPERATOR_CHAT_AGENT_NAME,
        tools=function_tools,
        toolsets=mcp_toolsets,
        retries=3,
        capabilities=capabilities,
        # [ghi chú] chat trên trang: trần chữ ra MỖI LẦN gọi (mô hình không phải Claude đi thẳng infer_model, không có max_tokens)
        **({"model_settings": {"max_tokens": CHAY_THU_CHAT_RA_MOT_LAN}} if _chay_thu_chat_trang(context) and not _chay_thu_ban_claude(context) else {}),   # [ghi chú] bản claude không gửi max_tokens
    )

    logger.info(
        "orchestrator: chat turn for agent=%s context=%s "
        "model=%s function_tools=%d mcp_toolsets=%d workflows=%d peers=%d",
        context.agent_id,
        context.context_id,
        resolved_model_id,
        len(function_tools),
        len(mcp_toolsets),
        len(workflows),
        len(peer_agents),
    )

    # Rehydrate cache-shaped prior history into typed ``ModelMessage``s
    # before handing it to pydantic-ai. Without this, dict entries from
    # the Redis history cache are silently dropped and every turn
    # behaves like the first one (ENG-2338).
    typed_prior_history = _coerce_prior_history(
        prior_history, label_contexts=context.operator_chat
    )
    last_record = _last_reply_proposals_record(prior_history)
    model_prompt = f"{last_record}\n\n{user_message}" if last_record else user_message

    yield ChatStreamEvent(type="status", text="Running orchestrator turn")
    if override_fallback_notice:
        yield ChatStreamEvent(type="status", text=override_fallback_notice)

    artifacts: List[Dict[str, Any]] = []
    seen_artifact_keys: set = set()
    # Wire 1.0.21: ``show_output`` batches emitted this turn, in order.
    show_outputs: List[Dict[str, Any]] = []
    # ENG-3849: agent-level chat-wire file rendering toggle (default ON).
    # OFF -> no per-file ``artifact`` frames and no ``show_output`` frames
    # on the chat stream; ``workflow.files_available`` events are the only
    # render channel (the consumer draws its own file UI from them).
    # Harvest/persistence below is NOT gated -- files still register in the
    # artifact registry (file-input binding, iteration, memory) and the
    # internal ``artifacts`` list still feeds turn-end fact extraction; the
    # executor blanks it from the terminal data part when this is off.
    render_files = bool(context.agent_record.get("render_files_in_chat", True))
    if review_document_batch is not None:
        # Before the model runs, so the table is on screen as its reply lands.
        # Remember the URL so a park of the same document inside the loop
        # does not emit a second frame.
        show_outputs.append(review_document_batch)
        _shown = context.review_tables_emitted
        if _shown is None:
            context.review_tables_emitted = _shown = set()
        from services.node_results.url_refresh import review_link_identity

        for _file in review_document_batch.get("files") or []:
            _url = _file.get("download_url") if isinstance(_file, dict) else None
            if _url:
                _shown.add(review_link_identity(_url))
        yield ChatStreamEvent(type="show_output", payload=review_document_batch)
    # B2: the most-recent run_id started by a run-initiating tool this turn.
    started_run_id: Optional[str] = None
    # Tool-call / tool-return parts observed on the stream, in order.
    # Fallback source for turn-end fact extraction on endings that
    # can't hand over ``result.all_messages`` (the usage-limit path
    # builds its terminal ChatTurnResult with an empty message list) --
    # without it, consent grants and run observations from a capped
    # turn's already-executed tools would never reach tier 0.
    streamed_tool_parts: List[Any] = []

    def _dedupe_artifacts(found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        fresh: List[Dict[str, Any]] = []
        for art in found:
            key = art.get("file_id") or art.get("download_url")
            if not key or key in seen_artifact_keys:
                continue
            seen_artifact_keys.add(key)
            fresh.append(art)
        return fresh

    # ENG-3385 (sweep follow-up): the agent loop runs in a PRODUCER task
    # that writes events to ``out_q``; this generator is a pump that
    # drains the queue with a keepalive timeout.  Two failures this
    # structure fixes (acceptance sweep 2026-07-07, 3/3 repro):
    #
    # * ``start_workflow(wait_for_completion=true)`` blocks the tool --
    #   and therefore the old inline ``yield`` loop -- for the whole
    #   workflow (~minutes).  The chat SSE went byte-silent from ~6.5s
    #   and Cloudflare killed the idle stream at ~125s (ELB at 300s),
    #   with the ``run_started`` frame (gated on tool RETURN) never
    #   sent.  Tools now push ``run_started`` onto the same queue at
    #   run CREATION via ``context.notify_run_started`` and the pump
    #   forwards it immediately, mid-tool-execution.
    # * Nothing on the wire for the length of any blocking tool call:
    #   the pump emits a ``keepalive`` frame whenever the queue is
    #   quiet for ``A2A_CHAT_KEEPALIVE_INTERVAL_S`` (default 15s, well
    #   under every proxy idle limit), spanning the WHOLE turn.
    out_q: asyncio.Queue = asyncio.Queue()
    context.turn_event_queue = out_q
    context.turn_tool_parts = streamed_tool_parts
    context.turn_canvas_mutations = []
    context.current_user_message = user_message
    context.previous_assistant_message = _last_assistant_text(prior_history)
    _DONE = object()

    async def _emit(ev: ChatStreamEvent) -> None:
        await out_q.put(ev)

    async def _produce() -> None:
        # ENG-5473: attribute every span the turn creates -- the model
        # requests, tool executions, in-turn compaction, and the E2B / MCP
        # cost generations raised inside tools -- to this org, agent,
        # conversation (Langfuse session) and user.  Entered BEFORE the
        # agent run so no span escapes it; fail-open, so a Langfuse
        # problem costs attribution, never the turn.
        with operator_chat_turn_telemetry(
            context, model_id=resolved_model_id
        ) as turn_observation:
            context.turn_trace_context = trace_context_of(turn_observation)
            await _run_turn(turn_observation)

    async def _run_turn(turn_observation: Optional[Any]) -> None:
        nonlocal started_run_id
        usage_limit_hit = False
        _chay_thu_run = None   # [ghi chú]
        # pydantic-ai requires the MCP toolsets be inside an async
        # context so their HTTP sessions get cleaned up when the turn
        # ends.  Agents without any MCP toolsets still work -- ``async
        # with agent`` is a no-op when ``toolsets`` is empty.
        result = None
        try:
            try:
                async with agent:
                    async with agent.iter(
                        model_prompt,
                        message_history=typed_prior_history,
                        # ENG-3368: the orchestrator previously passed no
                        # usage_limits, so pydantic-ai's default
                        # request_limit=50 aborted long tool-loop turns.
                        usage_limits=(
                            # [ghi chú] chat trên trang: trần cả lượt (số lần gọi + token vào / ra) ⇒ may_chu giữ chỗ được
                            UsageLimits(request_limit=min(CHAY_THU_CL_LAN, settings.A2A_CHAT_REQUEST_LIMIT))   # [ghi chú] bản claude: chỉ rào số lần gọi
                            if _chay_thu_ban_claude(context)
                            else UsageLimits(
                                request_limit=min(CHAY_THU_CHAT_LAN, settings.A2A_CHAT_REQUEST_LIMIT),
                                input_tokens_limit=CHAY_THU_CHAT_VAO,
                                output_tokens_limit=CHAY_THU_CHAT_RA,
                            )
                            if _chay_thu_chat_trang(context)
                            else UsageLimits(request_limit=settings.A2A_CHAT_REQUEST_LIMIT)
                        ),
                    ) as agent_run:
                        _chay_thu_run = agent_run   # [ghi chú] giữ để ghi usage cả khi chạm trần
                        async for node in agent_run:
                            if Agent.is_user_prompt_node(node):
                                continue

                            elif Agent.is_model_request_node(node):
                                if _chay_thu_chat_trang(context) and not _chay_thu_ban_claude(context):   # [ghi chú] chặn cỡ TRƯỚC khi gửi lần gọi này; [ghi chú] bản claude không rào byte
                                    _co = _chay_thu_co_tin(agent_run, node)
                                    if _co > CHAY_THU_CHAT_BYTE_1_LAN:
                                        raise UsageLimitExceeded(f"chay_thu: lần gọi kế quá cỡ ({_co} byte tin nhắn)")
                                async with node.stream(agent_run.ctx) as stream:
                                    async for event in stream:
                                        if isinstance(event, PartStartEvent):
                                            part = getattr(event, "part", None)
                                            if isinstance(part, ThinkingPart):
                                                # Coarse status kept for
                                                # existing consumers; real
                                                # thinking deltas follow.
                                                await _emit(
                                                    ChatStreamEvent(
                                                        type="status",
                                                        text="Thinking…",
                                                    )
                                                )
                                                if part.content:
                                                    await _emit(
                                                        ChatStreamEvent(
                                                            type="thinking",
                                                            text=part.content,
                                                        )
                                                    )
                                            elif (
                                                isinstance(part, TextPart)
                                                and part.content
                                            ):
                                                await _emit(
                                                    ChatStreamEvent(
                                                        type="text",
                                                        text=part.content,
                                                    )
                                                )
                                        elif isinstance(event, PartDeltaEvent):
                                            delta = getattr(event, "delta", None)
                                            if (
                                                isinstance(delta, TextPartDelta)
                                                and delta.content_delta
                                            ):
                                                await _emit(
                                                    ChatStreamEvent(
                                                        type="text",
                                                        text=delta.content_delta,
                                                    )
                                                )
                                            elif (
                                                isinstance(delta, ThinkingPartDelta)
                                                and delta.content_delta
                                            ):
                                                await _emit(
                                                    ChatStreamEvent(
                                                        type="thinking",
                                                        text=delta.content_delta,
                                                    )
                                                )

                            elif Agent.is_call_tools_node(node):
                                async with node.stream(agent_run.ctx) as stream:
                                    async for event in stream:
                                        if isinstance(event, FunctionToolCallEvent):
                                            part = event.part
                                            if part is not None:
                                                streamed_tool_parts.append(part)
                                                await _emit(
                                                    ChatStreamEvent(
                                                        type="tool_call",
                                                        tool_name=getattr(
                                                            part, "tool_name", ""
                                                        ),
                                                        text=_format_tool_args(part),
                                                    )
                                                )
                                        elif isinstance(event, FunctionToolResultEvent):
                                            result_part = event.result
                                            if result_part is None:
                                                continue
                                            streamed_tool_parts.append(result_part)
                                            content = getattr(
                                                result_part, "content", None
                                            )
                                            rendered = (
                                                result_part.model_response_str()
                                                if hasattr(
                                                    result_part,
                                                    "model_response_str",
                                                )
                                                else str(result_part)
                                            )
                                            if len(rendered) > _TOOL_EVENT_TEXT_CAP:
                                                rendered = (
                                                    rendered[:_TOOL_EVENT_TEXT_CAP]
                                                    + "…"
                                                )
                                            tool_name = getattr(
                                                result_part, "tool_name", ""
                                            )
                                            # B2: remember the run kicked off
                                            # this turn.  Resolved before the
                                            # emit so the frame carries it
                                            # structurally.
                                            rid_payload: Optional[Dict[str, Any]] = None
                                            if _run_initiating_tool(tool_name):
                                                rid = _extract_run_id_from_tool_result(
                                                    content
                                                )
                                                if rid:
                                                    started_run_id = rid
                                                    rid_payload = {"run_id": rid}
                                            # The workflows the chat shows as
                                            # cards below this reply.  Kept out
                                            # of ``rid_payload``, which doubles
                                            # as the "a run started" verdict
                                            # below.  ``workflow_id`` rides
                                            # along only for a single card, for
                                            # consumers that read one id.
                                            result_payload = dict(rid_payload or {})
                                            if _workflow_describing_tool(tool_name):
                                                wids = _extract_workflow_ids_from_tool_result(
                                                    content
                                                )
                                                if wids:
                                                    result_payload["workflow_ids"] = (
                                                        wids
                                                    )
                                                if len(wids) == 1:
                                                    result_payload["workflow_id"] = (
                                                        wids[0]
                                                    )
                                            built_wid = _extract_built_workflow_id(
                                                tool_name, content
                                            )
                                            if built_wid:
                                                result_payload["built_workflow_id"] = (
                                                    built_wid
                                                )
                                            await _emit(
                                                ChatStreamEvent(
                                                    type="tool_result",
                                                    tool_name=tool_name,
                                                    text=rendered,
                                                    payload=result_payload or None,
                                                )
                                            )
                                            # Belt-and-braces ``run_started``
                                            # at tool RETURN.  The primary
                                            # emission happens at run
                                            # CREATION via
                                            # ``notify_run_started``; the
                                            # pump dedupes by run_id so
                                            # consumers see exactly one
                                            # frame per run either way.
                                            # ENG-3234: carry the roster the
                                            # tool result already contains so
                                            # this fallback frame matches the
                                            # creation-time one.
                                            if rid_payload:
                                                started_payload = dict(rid_payload)
                                                roster = _extract_sub_agents_from_tool_result(
                                                    content
                                                )
                                                if roster:
                                                    started_payload["sub_agents"] = (
                                                        roster
                                                    )
                                                    started_payload[
                                                        "total_sub_agents"
                                                    ] = len(roster)
                                                await _emit(
                                                    ChatStreamEvent(
                                                        type="run_started",
                                                        tool_name=tool_name,
                                                        payload=started_payload,
                                                    )
                                                )
                                            # Wire 1.0.22: resolve the explicit
                                            # show_output directive FIRST. When
                                            # present, its files are delivered
                                            # exclusively via the explicit batch
                                            # below -- skip the generic harvest
                                            # entirely so this tool's own result
                                            # (which nests the same file dicts
                                            # under "files") is not ALSO picked
                                            # up by ``_extract_artifacts`` and
                                            # re-announced as duplicate per-file
                                            # ``artifact`` frames. The "replaces,
                                            # never stacks on" guarantee below
                                            # previously only covered the
                                            # aggregated show_output frame, not
                                            # these per-file ones -- that gap is
                                            # what let one show_output() call
                                            # render its files twice.
                                            directive = (
                                                _extract_show_output_directive(content)
                                                if tool_name == "show_output"
                                                and render_files
                                                else None
                                            )
                                            batch_files: List[Dict[str, Any]] = []
                                            harvested = (
                                                []
                                                if directive
                                                else [
                                                    _make_artifact_durable(art)
                                                    for art in _dedupe_artifacts(
                                                        _extract_artifacts(content)
                                                    )
                                                ]
                                            )
                                            # Image-generation MCP tools
                                            # return presigned ``image_url``s
                                            # instead of artifact refs --
                                            # ingest those into the artifact
                                            # store (S3 + registry + Artifacts
                                            # promotion) so they get a card,
                                            # a durable link, and a place in
                                            # the Artifacts panel. Best-effort
                                            # and skipped when the result
                                            # already carried artifacts.
                                            if not harvested and not directive:
                                                ingested = await ingest_tool_media(
                                                    context, tool_name, content
                                                )
                                                if ingested:
                                                    harvested = [
                                                        _make_artifact_durable(art)
                                                        for art in _dedupe_artifacts(
                                                            ingested
                                                        )
                                                    ]
                                            # Wire 1.0.23: promoted-row card
                                            # enrichment (fail-open -- see
                                            # _enrich_promoted_artifacts).
                                            harvested = (
                                                await _enrich_promoted_artifacts(
                                                    harvested, context.org_id
                                                )
                                            )
                                            # HIGH-CONCERN (table-only file
                                            # delivery): ``artifacts`` and
                                            # ``batch_files`` are appended in
                                            # LOCKSTEP from the same harvest,
                                            # and both the per-file ``artifact``
                                            # frame and the aggregated
                                            # ``show_output`` batch below sit
                                            # behind the SAME ``render_files``
                                            # gate.  That is what makes the
                                            # table a strict superset of the
                                            # (now dropped) link surface: there
                                            # is no branch where a file gets a
                                            # link but no table row.  Keep it
                                            # that way -- appending to one list
                                            # without the other, or gating them
                                            # differently, would strand a file
                                            # with NO delivery surface at all
                                            # now that links are gone.
                                            # (``other_outputs`` is excluded
                                            # upstream by ``_extract_artifacts``
                                            # and never had a link either; it is
                                            # delivered on request via
                                            # ``get_workflow_output``.)
                                            for art in harvested:
                                                artifacts.append(art)
                                                batch_files.append(art)
                                                if render_files:
                                                    await _emit(
                                                        ChatStreamEvent(
                                                            type="artifact",
                                                            payload=art,
                                                        )
                                                    )
                                            # Wire 1.0.21: one ``show_output``
                                            # frame per file-bearing tool
                                            # result -- the explicit "render
                                            # the file table now" signal,
                                            # emitted AFTER the per-file
                                            # ``artifact`` frames and
                                            # aggregating exactly those
                                            # files.  Per-turn deduped with
                                            # the artifact frames: a file
                                            # signals a render at most once
                                            # per turn.
                                            #
                                            # Wire 1.0.22: the explicit
                                            # ``show_output`` TOOL bypasses
                                            # that dedupe for the frame (not
                                            # for the per-file ``artifact``
                                            # frames above): its directive
                                            # result carries the full
                                            # server-resolved set and the
                                            # frame ALWAYS emits -- a user
                                            # asking to see files again must
                                            # get the render signal even if
                                            # every file already surfaced
                                            # this turn.  Still exactly ONE
                                            # frame per tool call: the
                                            # directive path replaces (never
                                            # stacks on) the generic one.
                                            # ENG-3849: with chat-wire file
                                            # rendering off, neither frame
                                            # flavor goes out (nor onto the
                                            # blocking terminal data part) --
                                            # files_available events carry
                                            # the render instead.
                                            if directive:
                                                batch = (
                                                    _build_explicit_show_output_batch(
                                                        directive
                                                    )
                                                )
                                                show_outputs.append(batch)
                                                await _emit(
                                                    ChatStreamEvent(
                                                        type="show_output",
                                                        tool_name=tool_name,
                                                        payload=batch,
                                                    )
                                                )
                                            elif batch_files and render_files:
                                                batch = _build_show_output_batch(
                                                    tool_name,
                                                    batch_files,
                                                    content,
                                                )
                                                show_outputs.append(batch)
                                                await _emit(
                                                    ChatStreamEvent(
                                                        type="show_output",
                                                        tool_name=tool_name,
                                                        payload=batch,
                                                    )
                                                )

                        result = agent_run.result
                        if _chay_thu_chat_trang(context):
                            _chay_thu_ghi_usage(context, agent_run, 0)
                    if not _chay_thu_chat_trang(context):   # [ghi chú] lần gọi lại này là 1 agent.run KHÔNG trần token ⇒ bỏ ở chat trên trang
                        result = await _retry_unbacked_canvas_claim(
                            agent, result, context=context, emit=_emit
                        )
            except UsageLimitExceeded as exc:
                if _chay_thu_chat_trang(context) and _chay_thu_run is not None:
                    # [đã chốt 12/11] cham_tran=1: trần token / số lần gọi của thư viện — dòng usage THIẾU lần gọi cuối ⇒ trang ghi mức tối đa.
                    # cham_tran=2: phép chặn CỠ của mình (ném TRƯỚC khi gửi lần gọi kế) ⇒ dòng usage ĐỦ ⇒ trang ghi tiền thật.
                    _chay_thu_ghi_usage(context, _chay_thu_run, 2 if str(exc).startswith("chay_thu: lần gọi kế quá cỡ") else 1)
                # ENG-3368: the turn hit the per-turn request budget.
                # Everything streamed so far has already been emitted;
                # close the turn with an honest, user-facing wrap-up.
                usage_limit_hit = True
                logger.warning(
                    "orchestrator: chat turn hit request limit for agent=%s "
                    "context=%s (limit=%d): %s",
                    context.agent_id,
                    context.context_id,
                    settings.A2A_CHAT_REQUEST_LIMIT,
                    exc,
                )

            if usage_limit_hit:
                limit_text = (
                    "I ran out of processing steps for this turn before fully "
                    "finishing. The work above (including any runs I started) "
                    "is still valid -- ask me to continue and I'll pick up "
                    "where I left off."
                )
                await _emit(ChatStreamEvent(type="text", text=limit_text))
                await _emit(
                    ChatStreamEvent(
                        type="final",
                        result=ChatTurnResult(
                            assistant_text=limit_text,
                            all_messages=[],
                            tool_calls_made=0,
                            artifacts=artifacts,
                            run_id=started_run_id,
                            show_outputs=show_outputs,
                        ),
                    )
                )
                record_turn_outcome(
                    turn_observation,
                    status="usage_limit",
                    request_limit=settings.A2A_CHAT_REQUEST_LIMIT,
                    started_run_id=started_run_id,
                    artifacts=len(artifacts),
                )
                return

            assistant_text = (
                str(result.output)
                if result is not None and result.output is not None
                else ""
            )
            all_messages = (
                list(result.all_messages() or []) if result is not None else []
            )

            # Count tool calls for telemetry. Each ModelMessage with parts
            # containing a ToolCallPart counts; pydantic-ai's exact part
            # type depends on the SDK version, so we duck-type by name.
            tool_calls_made = 0
            for msg in all_messages:
                for part in getattr(msg, "parts", []) or []:
                    if (
                        getattr(part, "part_kind", None) == "tool-call"
                        or part.__class__.__name__ == "ToolCallPart"
                    ):
                        tool_calls_made += 1

            await _emit(
                ChatStreamEvent(
                    type="final",
                    result=ChatTurnResult(
                        assistant_text=assistant_text,
                        all_messages=all_messages,
                        tool_calls_made=tool_calls_made,
                        artifacts=artifacts,
                        run_id=started_run_id,
                        show_outputs=show_outputs,
                    ),
                )
            )
            record_turn_outcome(
                turn_observation,
                status="completed",
                tool_calls_made=tool_calls_made,
                started_run_id=started_run_id,
                artifacts=len(artifacts),
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised by the pump
            # Class name only -- the message can quote the payload that
            # caused it, which the redaction policy keeps out of Langfuse.
            record_turn_outcome(
                turn_observation, status="error", error_type=type(exc).__name__
            )
            await out_q.put(exc)
        finally:
            await out_q.put(_DONE)

    keepalive_s = settings.A2A_CHAT_KEEPALIVE_INTERVAL_S
    producer = asyncio.create_task(_produce())
    emitted_run_ids: set = set()
    canvas_mutations_emitted = 0
    try:
        while True:
            try:
                item = (
                    await asyncio.wait_for(out_q.get(), timeout=keepalive_s)
                    if keepalive_s and keepalive_s > 0
                    else await out_q.get()
                )
            except asyncio.TimeoutError:
                # Nothing flowed for a full interval (typically a blocking
                # tool).  Keep bytes moving so no proxy idle limit fires.
                yield ChatStreamEvent(type="keepalive")
                continue
            if item is _DONE:
                break
            if isinstance(item, BaseException):
                raise item
            if item.type == "canvas_mutation":
                canvas_mutations_emitted += 1
            if item.type == "show_output" and item.payload not in show_outputs:
                # A review table enqueued mid-tool (the checkpoint opened
                # inside the model loop) is not in the producer's list.
                show_outputs.append(item.payload)
            if item.type == "run_started":
                rid = (item.payload or {}).get("run_id")
                if rid in emitted_run_ids:
                    continue
                if rid:
                    emitted_run_ids.add(rid)
                    started_run_id = str(rid)
            if item.type == "final" and item.result is not None:
                item.result.assistant_text = _guard_unbacked_canvas_change_claim(
                    item.result.assistant_text,
                    surface_context=context.surface_context,
                    canvas_mutation_count=canvas_mutations_emitted,
                )
                if on_canvas_surface(context.surface_context):
                    item.result.canvas_proposals = [
                        describe_canvas_mutation(m, context.surface_context)
                        for m in context.turn_canvas_mutations or []
                    ]
                # A run started via the creation-time side channel while
                # the tool was still blocked (budget/limit endings build
                # their result off ``started_run_id`` before the tool
                # returns) -- make sure the terminal payload carries it.
                if item.result.run_id is None and started_run_id:
                    item.result.run_id = started_run_id
                # Tiered-history maintenance (tier-0 facts + tier-2
                # rolling summary) from data this turn already
                # extracted -- fire-and-forget, never on the reply path.
                _schedule_turn_end_maintenance(
                    context=context,
                    user_message=user_message,
                    result=item.result,
                    model_id=resolved_model_id,
                    fallback_tool_parts=streamed_tool_parts,
                )
            yield item
    finally:
        if paused_countdowns:
            from services.operator_relay.countdown import resume_countdowns

            await resume_countdowns(paused_countdowns)
        context.turn_event_queue = None
        context.turn_tool_parts = None
        context.turn_canvas_mutations = None
        context.turn_trace_context = None
        context.current_user_message = None
        context.previous_assistant_message = None
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        # §2.6: the turn is the consistency boundary for the artifact-
        # iteration sandbox -- kill it (and its keepalive) with the turn.
        # The staging bookkeeping dies with it: stale path/run records
        # surviving past the sandbox would let a later consumer believe
        # files (from a possibly different run) are still staged --
        # exactly the cross-run bleed the isolation guard exists to
        # prevent.
        turn_sandbox = getattr(context, "turn_sandbox", None)
        context.turn_sandbox = None
        context.turn_staged_paths = None
        context.turn_staged_bytes = 0
        context.turn_staged_files = None
        context.turn_edit_calls = 0
        if turn_sandbox is not None:
            try:
                await turn_sandbox.cleanup()
            except Exception:  # noqa: BLE001 - best-effort teardown
                logger.warning("turn sandbox cleanup failed", exc_info=True)
        # Dynamic mounting: mounted MCP connections are turn-scoped like
        # the sandbox -- close them all; the persisted descriptors will
        # remount (freshly rebound) on the next turn.
        from services.orchestrator.tools import close_turn_mounts

        await close_turn_mounts(context)


_SYNC_BUDGET_NOTICE = (
    "I hit the processing-time limit for a single blocking chat turn "
    "before fully finishing. The work above (including any runs I "
    "started) is still valid -- ask me to continue and I'll pick up "
    "where I left off. For long-running turns, call the chat skill via "
    "``message/stream`` instead of ``message/send`` so results stream "
    "live without a proxy timeout."
)


async def run_chat_turn(
    *,
    context: OrchestratorContext,
    user_message: str,
    prior_history: Optional[List[Any]] = None,
    wall_clock_budget_s: Optional[float] = None,
) -> ChatTurnResult:
    """Run one synchronous chat turn through the pydantic-ai orchestrator.

    Blocking drain of :func:`run_chat_turn_stream` -- same execution
    path as the streaming surface, so blocking ``message/send`` callers
    and SSE consumers can never diverge.  The caller is responsible for
    persisting ``result.all_messages`` to the conversation cache so
    subsequent turns can replay history.

    A bare-bones contract: returns the assistant text + the full
    ``ModelMessage`` list pydantic-ai produced (which includes all
    tool calls + tool results the LLM made mid-turn).  Tool-call count
    is exposed for telemetry / SLO tracking.

    ``wall_clock_budget_s`` (ENG-3385): blocking ``message/send``
    callers sit behind Cloudflare, whose ~100s proxy read timeout 524s
    any response that hasn't sent a byte -- and a blocking chat turn
    sends nothing until the whole tool loop finishes.  When set, the
    drain is cut off after that many seconds and the turn ends
    gracefully with everything streamed so far (partial text, artifact
    refs, any started ``run_id``) plus an honest truncation notice --
    the same coherent-partial contract as the ENG-3368 usage-limit
    path.  ``None`` / ``0`` disables the cap (streaming callers never
    pass it; SSE bytes flow immediately so Cloudflare never times
    them out).
    """

    final: Optional[ChatTurnResult] = None
    text_parts: List[str] = []
    artifacts: List[Dict[str, Any]] = []
    show_outputs: List[Dict[str, Any]] = []
    tool_calls_seen = 0
    started_run_id: Optional[str] = None
    # Live reference to the stream's captured tool parts. Grabbed
    # DURING the drain because the budget timeout cancels the
    # generator at its suspension point, running its ``finally`` --
    # which clears ``context.turn_tool_parts`` -- before our
    # ``except TimeoutError`` gets to read it. The list object itself
    # stays alive and keeps the parts appended before the cut.
    captured_tool_parts: Optional[List[Any]] = None
    # ENG-5473: same lifecycle for the turn's Langfuse trace context --
    # the cancelled generator's ``finally`` clears it before the timeout
    # handler runs, so grab it during the drain for the fold scheduled
    # below.
    captured_trace_context: Optional[Dict[str, str]] = None

    budget = (
        wall_clock_budget_s
        if wall_clock_budget_s is not None and wall_clock_budget_s > 0
        else None
    )
    agen = run_chat_turn_stream(
        context=context,
        user_message=user_message,
        prior_history=prior_history,
    )
    try:
        async with asyncio.timeout(budget):
            async for event in agen:
                if captured_tool_parts is None:
                    captured_tool_parts = context.turn_tool_parts
                if captured_trace_context is None:
                    captured_trace_context = context.turn_trace_context
                if event.type == "final":
                    final = event.result
                elif event.type == "text" and event.text:
                    text_parts.append(event.text)
                elif event.type == "tool_call":
                    tool_calls_seen += 1
                elif event.type == "artifact" and event.payload:
                    artifacts.append(event.payload)
                elif event.type == "show_output" and event.payload:
                    show_outputs.append(event.payload)
                elif event.type in ("tool_result", "run_started") and event.payload:
                    rid = event.payload.get("run_id")
                    if rid:
                        started_run_id = str(rid)
    except TimeoutError:
        logger.warning(
            "orchestrator: blocking chat turn exceeded wall-clock budget "
            "for agent=%s context=%s (budget=%.0fs, tool_calls=%d, "
            "run_id=%s); returning graceful partial",
            context.agent_id,
            context.context_id,
            budget or 0,
            tool_calls_seen,
            started_run_id,
        )
        partial_text = "".join(text_parts).strip()
        assistant_text = (
            f"{partial_text}\n\n{_SYNC_BUDGET_NOTICE}"
            if partial_text
            else _SYNC_BUDGET_NOTICE
        )
        partial_result = ChatTurnResult(
            assistant_text=assistant_text,
            all_messages=[],
            tool_calls_made=tool_calls_seen,
            artifacts=artifacts,
            run_id=started_run_id,
            truncated=True,
            show_outputs=show_outputs,
        )
        # The truncated turn never yields a ``final`` frame, so the
        # stream-side maintenance hook never fires -- schedule it here
        # from the partial + the tool parts the stream captured before
        # the budget hit, so consent grants / artifacts / runs from a
        # truncated sync turn still reach tier 0.
        if final is None:
            _schedule_turn_end_maintenance(
                context=context,
                user_message=user_message,
                result=partial_result,
                model_id=_resolve_model(context.agent_record),
                fallback_tool_parts=list(captured_tool_parts or []),
                trace_context=captured_trace_context,
            )
        return partial_result
    finally:
        # Idempotent; unwinds the agent/MCP context managers if the
        # generator was abandoned mid-iteration by the timeout.
        await agen.aclose()

    if final is None:
        raise RuntimeError("orchestrator stream ended without a final result")
    return final
