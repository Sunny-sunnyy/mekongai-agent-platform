"""Built-in workflow-runtime tools for the A2A chat orchestrator.

The orchestrator (``api/a2a_gateway/orchestrator.py``) attaches this fixed
set of pydantic-ai tools when the agent record has
``enable_workflow_runtime=true`` - the agent can list, start, await, stop,
pause, resume, rerun, and read status / output / events / chain-of-thought
directly without the LLM having to call HTTP endpoints.

Canvas AgentNodes do not attach these tools: the empty-allowlist
fan-out to all published org workflows is only safe on the
operator-owned chat surface.

Tools are async closures over the AgentNode so they have access to::

    self._orgid
    self._user_token
    self._user_email
    self._user_id
    self._session_id

When that context is missing (e.g., a unit test that constructs an
AgentNode without setting those fields), :func:`make_workflow_tools`
returns an empty list rather than failing the compile.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
)

from sqlalchemy import Select, func, select, text

from api.a2a_gateway.decision_vocabulary import DENY_WORDS
from api.handlers.human_interruption.post import (
    INTERVENTION_CONTINUE_TOKEN as _INTERVENTION_CONTINUE_TOKEN,
)
from api.handlers.human_interruption.post import (
    INTERVENTION_STOP_TOKEN as _INTERVENTION_STOP_TOKEN,
)
from db.models.enums import TERMINAL_RUN_STATUSES
from db.models.workflow import Workflow as WorkflowModel
from db.models.workflow_event import WorkflowEvent
from db.models.workflow_run import WorkflowRun
from db.session import async_session_factory
from services.a2a.node_io import classify_output_kind as _classify_output_kind
from services.agent_run_user import build_run_user
from services.operator_relay import inbox as operator_inbox
from services.orchestrator.tools.file_delivery_rules import (
    CAUSE_NO_RUN,
    nothing_rendered_note,
)
from services.redis.client import RedisServiceSingleton

if TYPE_CHECKING:
    from services.node_results.files_available import FilesAvailableDelivery

logger = logging.getLogger(__name__)


# Terminal run statuses we use to detect "the workflow is done" when a tool
# was asked to wait_for_completion. Aliased, not re-listed -- the same four
# had four homes. The str-mixin keeps bare-string comparisons working.
_TERMINAL_STATUSES = TERMINAL_RUN_STATUSES

# Terminal statuses that mean somebody called the run off, rather than the run
# finishing or breaking on its own. A run only reaches one of these by way of
# the run controls, so a run that ends this way ended because a person decided
# it should.
STOPPED_RUN_STATUSES = {"stopped", "cancelled"}

# Legacy ``workflow_event.event_type`` strings (+ the v1 alias) that mark a
# node waiting on human input. Mirrors event_adapter._PG_LEGACY_TO_V1 ->
# NODE_INPUT_REQUIRED so the chat pending-input fallback agrees with the A2A
# events surface on what "this run is waiting for input" means.
_INPUT_REQUIRED_EVENT_TYPES = (
    "awaiting_intervention",
    "awaiting_user_input",
    "node.input_required",
)

# Continue/stop tokens the Human Intervention node's panel understands. The
# node treats ONLY ``"continue"`` as proceed; anything else is a STOP.

# Natural-language words that clearly mean "halt the run". Kept deliberately
# conservative: stopping is the safety-critical direction, so we only map to
# ``stop`` on an unambiguous denial and otherwise default to ``continue``
# (which is also the node's own default for an empty/ambiguous decision).
# Single-sourced with the input-consent classifier's vocabulary (opposite
# default, same words) -- see ``api.a2a_gateway.decision_vocabulary``.
_INTERVENTION_DENY_WORDS = DENY_WORDS

# Private describe_workflow payload: classified InputFieldResponse rows
# (with template_s3_key / template_s3_bucket / questions) so
# export_workflow_questionnaire can reuse the load already paid for the
# public schema. Stripped before the model sees the tool result.
_CLASSIFIED_INPUTS_KEY = "_classified_inputs"

# Interactive-feedback nodes (docx/xlsx/pptx/ey-pptx chat-output nodes, plus
# agent / code-interpreter nodes with "Ask for feedback on outputs" enabled)
# are a SEPARATE human-input family from the continue/stop Human Intervention
# node, and they have OPPOSITE finalize semantics: they treat "continue" (and
# any other non-end string) as MORE edit feedback and loop -- regenerating the
# document and re-opening the same checkpoint forever. They only COMPLETE on a
# chat-end token (``ChatBaseNode._should_end_chat`` -> the explicit
# ``END_CHAT_SESSION-{session}-{node}`` token, or "exit"/"quit"/"done"). So a
# bare "continue"/"approved" forwarded to one of these nodes never advances the
# run (the EY/Scale "second checkpoint loops" defect). When the orchestrator is
# replying to a chat node we therefore map an APPROVAL/continue onto the
# chat-end token (so the node finalises and the run advances) and forward
# genuine edit instructions VERBATIM (so the node iterates on the document).
_CHAT_FINALIZE_WORDS = frozenset(
    {
        "",
        "continue",
        "proceed",
        "approved",
        "approve",
        "yes",
        "y",
        "yeah",
        "yep",
        "ok",
        "okay",
        "go",
        "done",
        "finish",
        "finished",
        "complete",
        "completed",
        "accept",
        "accepted",
        "lgtm",
        "good",
        "stop",
        "exit",
        "quit",
        "cancel",
    }
)

# Multi-word approvals/finishes that should also finalise a chat node.
_CHAT_FINALIZE_PHRASES = frozenset(
    {
        "go ahead",
        "looks good",
        "looks good to me",
        "no changes",
        "no further changes",
        "no more changes",
        "all good",
        "that works",
        "ship it",
        "we're done",
        "i'm done",
        "im done",
    }
)


def _chat_end_token(session_id: str, node_id: str) -> str:
    """Build the explicit chat-end token an interactive-feedback chat node
    recognises (``ChatBaseNode.is_user_end_chat_response`` checks for
    ``END_CHAT_SESSION-{session}-{node}`` as a substring of the delivered
    input). Delivering this on the chat:response channel makes the node break
    its review loop and finalise with the last good output, advancing the run.
    """
    return f"END_CHAT_SESSION-{session_id}-{node_id}"


def _is_chat_finalize_response(response: Optional[str]) -> bool:
    """Return True when the user's reply to a chat-feedback node means
    "I'm happy, finish" (approval/continue/done) rather than substantive edit
    feedback. Conservative: only a recognised finalize word/phrase finalises;
    anything else is treated as an edit instruction and forwarded verbatim so
    the node iterates."""
    if response is None:
        return True
    text = response.strip().lower()
    if text in _CHAT_FINALIZE_WORDS or text in _CHAT_FINALIZE_PHRASES:
        return True
    tokens = text.split()
    if not tokens:
        return True
    first = tokens[0].strip(".,!;:?'\"")
    # Single-token replies like "continue." / "done!" / "yes," -> finalise.
    if len(tokens) == 1 and first in _CHAT_FINALIZE_WORDS:
        return True
    return False


def _chat_node_response_token(
    response: Optional[str], session_id: str, node_id: str
) -> str:
    """Map a user reply onto the token an interactive-feedback chat node should
    receive: the chat-end token for an approval/continue/done (so it finalises
    and the run advances) or the reply VERBATIM for substantive edit feedback
    (so it iterates on the document)."""
    if _is_chat_finalize_response(response):
        return _chat_end_token(session_id, node_id)
    return response


# Phase B clarifications on the chat-orchestrator path (ENG-2805).
#
# Unlike the raw A2A ``call_workflow`` skill (which defaults clarifications
# OFF for explicit opt-in), the chat orchestrator OWNS the full
# clarification loop end-to-end: when a run it started via ``start_workflow``
# pauses to ask the user a question, the orchestrator surfaces the question
# as assistant chat text and routes the user's next turn back into the run
# via ``submit_clarification_response``. Because that round-trip is handled
# transparently here, enabling clarifications by default for chat-driven
# runs is the right behaviour -- a chat user can naturally answer an agent's
# mid-run question. This is a deliberate, documented default (not a magic
# literal at the call site); a finite-but-generous timeout is used instead
# of indefinite "ask and wait" so a chat run can never park forever waiting
# on a user who walked away.
_CHAT_ORCHESTRATOR_ALLOW_CLARIFICATIONS = True
_CHAT_ORCHESTRATOR_CLARIFICATION_TIMEOUT_SECS = 300

# Cap for "what IS available" listings inside workflow-resolution errors
# (ENG-3388) -- keeps the message legible for agents with large allowlists.
_WORKFLOW_CATALOG_CAP = 10

# ENG-3444: lead with "copy the id" -- the observed failure is a caller
# (usually the orchestrator LLM itself) DERIVING an id from the display
# name rather than holding a stale one, and the old "update your catalog"
# copy coached models into inventing a phantom stale-catalog story.
_WORKFLOW_RENAME_HINT = (
    "Copy the id exactly from list_workflows (slugs are lowercase with "
    "underscores, never guessed from the display name). Slugs change when "
    "a workflow is renamed; UUIDs are stable."
)


def _format_workflow_catalog(entries: List[tuple]) -> str:
    """Render ``(slug, uuid)`` pairs as a capped, human-readable list."""
    shown = ", ".join(
        f"'{slug}' (UUID {wf_id})" for slug, wf_id in entries[:_WORKFLOW_CATALOG_CAP]
    )
    overflow = len(entries) - _WORKFLOW_CATALOG_CAP
    if overflow > 0:
        shown += f", ... and {overflow} more (call list_workflows for the full set)"
    return shown


def _workflow_not_found_message(raw_id: Any, entries: List[tuple]) -> str:
    """Self-healing resolution error (ENG-3388): name what IS available so
    a caller holding a stale slug (workflow renamed -> slug changed) fixes
    its catalog instead of suspecting an allowlist/permission problem."""
    if not entries:
        return (
            f"workflow '{raw_id}' does not resolve. No allowlisted "
            "workflows could be resolved for me -- call "
            "list_workflows to see what's available."
        )
    if len(entries) == 1:
        slug, wf_id = entries[0]
        return (
            f"workflow '{raw_id}' does not resolve. My workflow "
            f"is '{slug}' (UUID {wf_id}). {_WORKFLOW_RENAME_HINT}"
        )
    return (
        f"workflow '{raw_id}' does not resolve. My allowlisted "
        f"workflows are: {_format_workflow_catalog(entries)}. "
        f"{_WORKFLOW_RENAME_HINT}"
    )


def _missing_workflow_id_message(entries: List[tuple]) -> str:
    """Error for ``start_workflow`` called without ``workflow_id`` when the
    agent exposes zero or multiple workflows (no unambiguous default)."""
    if not entries:
        return (
            "start_workflow requires 'workflow_id', and no allowlisted "
            "workflows could be resolved for me -- call "
            "list_workflows to see what's available."
        )
    return (
        "start_workflow requires 'workflow_id' when I expose "
        "more than one workflow. Available workflows: "
        f"{_format_workflow_catalog(entries)}."
    )


def _open_canvas_workflow_id(node: Any) -> Optional[str]:
    """The workflow the user has open on the canvas, if there is one.

    ``None`` for every other case, including canvas AgentNodes (which carry no
    ``surface_context`` at all) and unsaved canvases (which have no id yet) --
    use :func:`_is_canvas_surface` to distinguish "no canvas" from "canvas,
    but unsaved".
    """
    surface = getattr(node, "surface_context", None)
    if getattr(surface, "surface", None) != "workflow_canvas":
        return None
    open_id = getattr(surface, "workflow_id", None)
    return str(open_id) if open_id else None


def _is_canvas_surface(node: Any) -> bool:
    """Whether the node's surface_context is a workflow canvas, saved or not."""
    surface = getattr(node, "surface_context", None)
    return getattr(surface, "surface", None) == "workflow_canvas"


_CANVAS_REDIRECT_ERROR = {
    "error": (
        "The workflow the user is looking at is open on the canvas. Call "
        "run_workflow instead -- it runs the graph they are looking at "
        "(including edits they have not saved) and shows the run on the "
        "canvas. start_workflow would run the saved definition out of "
        "sight, so the canvas would sit idle while the run happened "
        "elsewhere. Do not retry start_workflow, and do not ask the user "
        "which workflow they mean -- the canvas already answers that."
    )
}


_PERMISSION_PHRASES = {
    "run": "run",
    "view_content": "view the contents of",
    "edit": "edit",
}


def _workflow_access_denied_message(subject: str, permission: str) -> str:
    verb = _PERMISSION_PHRASES.get(permission, permission)
    return (
        f"The user does not have permission to {verb} {subject}. Their role "
        "on the workflow does not allow it, so the request cannot be served "
        "from chat. Tell the user the workflow's owner can grant them the "
        "editor role, or they can request access from the workflow list."
    )


def _enforces_user_workflow_access(node: Any) -> bool:
    return bool(getattr(node, "enforce_user_workflow_access", False))


async def _user_workflow_access_error(
    node: Any,
    session: Any,
    workflow_id: Any,
    permission: str,
    subject: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Composer's per-user access bar, applied only where the context opts in.

    Returns ``None`` when the caller may proceed, otherwise the tool error to
    return. Fails closed when the surface enforces access but no user resolved.
    """
    if not _enforces_user_workflow_access(node):
        return None
    subject = subject or f"workflow '{workflow_id}'"
    user_id = getattr(node, "_user_id", None) or None
    if not user_id:
        return {"error": _workflow_access_denied_message(subject, permission)}

    from services.workflow_access.workflow_access import WorkflowAccess

    wf_uuid = (
        workflow_id
        if isinstance(workflow_id, uuid.UUID)
        else uuid.UUID(str(workflow_id))
    )
    allowed = await WorkflowAccess(session).check_permission(
        wf_uuid, user_id, permission
    )
    if allowed:
        return None
    return {"error": _workflow_access_denied_message(subject, permission)}


async def _load_run_for_node(
    session: Any, run_id: str, node: Any, permission: str
) -> tuple[Optional["WorkflowRun"], Optional[Dict[str, Any]]]:
    """Org-scoped run lookup plus the requesting user's permission on its workflow.

    Returns ``(run, None)`` when usable, ``(None, None)`` when no such run is
    retrievable in this org, and ``(None, error)`` when the surface enforces
    per-user access and the user lacks ``permission`` on the run's workflow.
    Every run-addressed tool resolves through here so coverage is not a
    per-call-site decision.
    """
    run = await _load_run_in_session(session, run_id, node._orgid)
    if run is None or not _enforces_user_workflow_access(node):
        return run, None
    denied = await _run_access_error(node, session, run, run_id, permission)
    if denied is not None:
        return None, denied
    return run, None


async def _run_access_error(
    node: Any, session: Any, run: Any, run_id: str, permission: str
) -> Optional[Dict[str, Any]]:
    return await _user_workflow_access_error(
        node,
        session,
        run.workflow_id,
        permission,
        subject=f"run '{run_id}' (workflow '{run.workflow_id}')",
    )


async def _user_runnable_workflow_ids(
    node: Any, session: Any, org_id: str
) -> Optional[set]:
    """Workflow ids the requesting user may run, or ``None`` when unrestricted."""
    if not _enforces_user_workflow_access(node):
        return None
    user_id = getattr(node, "_user_id", None) or None
    if not user_id:
        return set()

    from services.workflow_access.workflow_access import WorkflowAccess

    access = WorkflowAccess(session)
    if await access.should_bypass_access_control(user_id):
        return None
    access_map = await access.list_by_user(user_id, permission="run", org_id=org_id)
    return {str(wf_id) for wf_id in access_map}


def _canvas_run_redirect(node: Any, wf_uuid: Any = None) -> Optional[Dict[str, Any]]:
    """Refuse a headless run of the workflow the user is watching.

    A canvas run and a ``start_workflow`` run are not the same thing: the
    canvas dispatches the graph currently on screen -- unsaved edits and all
    -- and animates progress in place, while this tool runs the persisted
    definition in the background. When the user has this very workflow open,
    "run it" means the former, so send the model to ``run_workflow`` rather
    than starting a run the canvas has no way to show.

    ``wf_uuid`` is the workflow the caller asked for, once it is known. Pass
    nothing for the bare "run it" case: with a canvas on screen -- saved or
    not -- there is no ambiguity to resolve, so answering with a catalog of
    every allowlisted workflow asks the user to name what they are already
    looking at. An unsaved canvas has no id to compare against a caller-given
    ``wf_uuid``, so that branch only redirects the bare, id-less call.

    Never redirects when ``enable_workflow_canvas_editing`` is off: that
    flag withholds the ENTIRE canvas toolset, ``run_workflow`` included, so
    sending the model there would name a tool it doesn't have (PR #1438
    review).
    """
    if not (getattr(node, "agent_record", None) or {}).get(
        "enable_workflow_canvas_editing", True
    ):
        return None
    if wf_uuid is None:
        return _CANVAS_REDIRECT_ERROR if _is_canvas_surface(node) else None
    open_id = _open_canvas_workflow_id(node)
    if open_id is None or open_id != str(wf_uuid):
        return None
    return _CANVAS_REDIRECT_ERROR


async def _load_workflow_payload_and_purpose(
    workflow_uuid: uuid.UUID,
    org_id: str,
) -> Optional[tuple[Dict[str, Any], Optional[str]]]:
    """Load a workflow's version payload + declared purpose for the guardrail.

    Tenancy: the workflow row is looked up scoped to ``org_id`` FIRST. If it
    doesn't belong to the caller's org (or doesn't exist), this returns None and
    NO version payload / persona is read — the guardrail then skips and the
    trigger handler's own ``org_id`` check rejects the run. This keeps the
    multi-tenant access-control invariant: a caller can never make the guardrail
    load another org's workflow personas.

    Uses the same version-resolution order as ``describe_workflow`` /
    ``get_workflow_inputs`` (latest MANUAL_SAVE, then latest of any type) so the
    personas examined match the tasks the user was shown as editable.

    Args:
        workflow_uuid: The resolved workflow id about to be triggered.
        org_id: The caller's org; the lookup is scoped to it.

    Returns:
        ``(payload, purpose)`` when the workflow belongs to ``org_id``
        (``payload`` may be ``{}``; ``purpose`` may be None), or None when the
        workflow is not in the caller's org.
    """
    from services.workflows import Workflow as WorkflowService

    factory = async_session_factory(use_fresh_engine=False)
    async with factory() as session:
        workflow = (
            await session.execute(
                select(WorkflowModel).where(
                    WorkflowModel.id == workflow_uuid,
                    WorkflowModel.org_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if workflow is None:
            return None  # not this org's workflow -> skip guardrail (fail-open)

        svc = WorkflowService(session)
        version = await svc.get_latest_manual_save_version(workflow_uuid)
        if version is None:
            version = await svc.get_latest_version(workflow_uuid)
        payload: Dict[str, Any] = (
            (getattr(version, "payload", None) or {}) if version else {}
        )
        parts = [
            str(p).strip()
            for p in (
                getattr(workflow, "name", None),
                getattr(workflow, "description", None),
            )
            if p and str(p).strip()
        ]
        purpose: Optional[str] = " — ".join(parts) or None
        if purpose is None and payload.get("name"):
            purpose = str(payload["name"])
    return payload, purpose


async def _composio_connection_gate(
    node: Any, *, workflow_uuid: uuid.UUID, org_id: str
) -> Optional[Dict[str, Any]]:
    """Refuse to start a run whose Composio toolkits the chat user has not
    connected, handing back a connect link per toolkit instead.

    The same check ``prepare_workflow`` fails closed on, run here so no run
    row is created and the user is asked before anything starts. Fail-open
    on lookup errors: prepare re-checks.
    """
    user_id = getattr(node, "_user_id", None)
    if not user_id:
        return None
    try:
        from services.composio.connections import oauth_callback_url
        from services.composio.sessions import missing_connections, resolve_nodes
        from services.composio_tool_configs.runtime import (
            fetch_merged_composio_config,
        )

        loaded = await _load_workflow_payload_and_purpose(workflow_uuid, org_id)
        if loaded is None:
            return None
        payload, _purpose = loaded
        structs = [
            (n.get("data") or {}).get("struct") or {}
            for n in payload.get("nodes") or []
            if isinstance(n, dict)
        ]
        cfg = await fetch_merged_composio_config(
            org_id, user_id, user_email=getattr(node, "_user_email", None)
        )
        blocked = await missing_connections(
            structs,
            user_id=user_id,
            composio_config=cfg,
            callback_url=oauth_callback_url(),
            resolved=await resolve_nodes(structs),
        )
    except Exception as exc:  # noqa: BLE001 - prepare fails closed; this only saves a run row
        logger.warning("start_workflow: Composio connection check skipped: %s", exc)
        return None
    if not blocked:
        return None
    return {
        "status": "authorization_required",
        "missing_toolkits": blocked["missing"],
        "connect_markdown": blocked["connect_markdown"],
        "message": (
            "This workflow uses integrations the user has not connected. "
            "Relay each connect_markdown link verbatim; once they have "
            "connected, call start_workflow again."
        ),
    }


async def _persona_block_rejection(
    *, blocked: Any, workflow_purpose: Optional[str]
) -> Dict[str, Any]:
    """Pick the refusal for a persona-change match: terminal, or recoverable.

    A match ALWAYS stops the run -- that part is deterministic and needs no model
    call. What the judge decides is only whether the user is told the refusal is
    final. The patterns are a keyword heuristic that does fire on ordinary
    in-role wording, and the terminal refusal forbids the orchestrator from even
    offering a rewording, so pinning it on the heuristic alone leaves a user who
    tripped it on "Reframe the executive summary" with nowhere to go.

    Args:
        blocked: ``(task, matched_phrases)`` from ``find_persona_changes``.
        workflow_purpose: The workflow's declared purpose, for the judge prompt.

    Returns:
        The terminal ``persona_change`` refusal for matches the judge also places
        in the re-orientation band, else the recoverable
        ``persona_change_unconfirmed`` rejection.
    """
    from api.a2a_gateway import task_alignment as ta
    from core.settings import settings

    scores = await ta.score_task_relevance(
        amended_tasks=[task for task, _ in blocked],
        workflow_purpose=workflow_purpose,
        model_id=settings.A2A_TASK_ALIGNMENT_JUDGE_MODEL,
        timeout_s=settings.A2A_TASK_ALIGNMENT_TIMEOUT_S,
        retries=settings.A2A_TASK_ALIGNMENT_JUDGE_RETRIES,
    )
    confirmed = ta.confirmed_persona_changes(blocked, scores)
    detail = "; ".join(
        f"{(t.node_id or t.node_label)}.{t.field_id} matched={phrases}"
        for t, phrases in blocked
    )
    if confirmed:
        logger.info(
            "start_workflow: persona-change block HARD-FAILED %d of %d matched "
            "task(s), judge-confirmed; run not started. Matched: %s",
            len(confirmed),
            len(blocked),
            detail,
        )
        return ta.build_persona_change_rejection(confirmed)
    # Heuristic fired, judge did not concur (or could not answer). Still refuse
    # the run, but recoverably -- and log it: this is the signal for tuning the
    # patterns, and the only place a false positive is visible in ops.
    logger.info(
        "start_workflow: persona-change match NOT judge-confirmed for %d task(s); "
        "run not started, recoverable rejection returned. Scores: %s. Matched: %s",
        len(blocked),
        [s.relevance for s in scores],
        detail,
    )
    return ta.build_unconfirmed_persona_rejection(blocked)


async def _task_alignment_gate(
    *,
    node: Any,
    workflow_uuid: uuid.UUID,
    org_id: str,
    overrides: Any,
) -> Optional[Dict[str, Any]]:
    """Run the ENG-3634 task-relevance guardrail before a workflow triggers.

    Scores only the RESOLVED overrides (``_translate_inputs`` output) that will
    actually bind this run, so a key the translator would drop (unknown node id,
    ambiguous bare key, author-locked field) can never hard-fail the run.

    Args:
        node: The orchestrator context (carries ``agent_record`` on the A2A
            chat path; other callers have none and are skipped).
        workflow_uuid: The resolved workflow id about to be triggered.
        org_id: The caller's org (scopes the tenancy-checked payload load).
        overrides: The bound ``InputNodeOverride[]`` the trigger will apply.

    Returns:
        A ``rejected`` dict (hard fail -- the run must NOT start) when a bound
        amended task tries to re-orient the agent (``blocked_kind:
        persona_change``, terminal / not retryable) or scores below the
        relevance threshold, else None to proceed with the run. Fail-open: any
        error is logged and None (proceed) is returned.

        Both checks ride the single per-agent ``enable_task_alignment_guardrail``
        toggle. The persona-change block runs first and is model-free, so it
        cannot fail open the way the judge can.
    """
    try:
        from api.a2a_gateway import task_alignment as ta
        from core.settings import settings

        agent_record = getattr(node, "agent_record", None)
        if not isinstance(agent_record, dict):
            return None  # not the A2A chat orchestrator path
        if not ta.is_guardrail_enabled(agent_record):
            return None  # one per-agent switch gates BOTH checks below
        if not ta.has_task_override(overrides):
            return None  # no task actually bound -> nothing to check

        loaded = await _load_workflow_payload_and_purpose(workflow_uuid, org_id)
        if loaded is None:
            return None  # workflow not in caller's org -> skip (trigger rejects)
        payload, purpose = loaded
        amended = ta.amended_tasks_from_overrides(
            overrides, ta.persona_map_from_payload(payload)
        )
        if not amended:
            return None

        # Deterministic persona-change block FIRST: model-free, so unlike the
        # judge it cannot fail open. Its refusal is terminal (no workaround, no
        # retry), so it must win over the judge's "supply a corrected task"
        # rejection when an amendment trips both.
        if ta.is_persona_block_enabled(agent_record):
            blocked = ta.find_persona_changes(amended)
            if blocked:
                return await _persona_block_rejection(
                    blocked=blocked, workflow_purpose=purpose
                )

        scores = await ta.score_task_relevance(
            amended_tasks=amended,
            workflow_purpose=purpose,
            model_id=settings.A2A_TASK_ALIGNMENT_JUDGE_MODEL,
            timeout_s=settings.A2A_TASK_ALIGNMENT_TIMEOUT_S,
            retries=settings.A2A_TASK_ALIGNMENT_JUDGE_RETRIES,
        )
        threshold = ta.min_relevance(agent_record)
        failed = ta.failing_scores(scores, threshold)
        if not failed:
            return None
        # Log per-task node/score/reason (not just the count) so the
        # A2A_TASK_ALIGNMENT_MIN_RELEVANCE threshold can be calibrated from ops
        # logs alone.
        logger.info(
            "start_workflow: task-relevance guardrail HARD-FAILED %d amended "
            "task(s) below threshold %d; run not started. Failing: %s",
            len(failed),
            threshold,
            "; ".join(
                f"{(s.node_id or s.node_label)}.{s.field_id}={s.relevance} ({s.reason})"
                for s in failed
            ),
        )
        return ta.build_rejection_response(failed, threshold)
    except Exception:
        logger.exception(
            "start_workflow: task-relevance guardrail errored; proceeding (fail-open)"
        )
        return None


async def _pending_input_decision_guard(
    *,
    org_id: str,
    agent_id: Optional[str],
    context_id: Optional[str],
    agent_record: Optional[Dict[str, Any]] = None,
    launch_files: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Deterministic guard for ENG-4104: do not run on input the agent itself
    reported as unusable until the user has said whether to proceed.

    ``report_input_issue`` only publishes a finding, so nothing stopped the run
    and whether the user was asked -- and whether their answer was respected --
    was a model choice. The observable symptom is the same bad input halting one
    workflow and running on another. This turns the ask into a rule.

    Behaviour comes from the agent's ``input_issue_policy``
    (``input_decision_context.resolve_policy``), because what *should* happen on
    unusable input differs legitimately between deployments: ``warn`` never
    gates and keeps the pre-existing behaviour, ``confirm`` blocks until the user
    answers, ``block`` refuses outright with no consent path.

    Returns a refusal dict when ``start_workflow`` must be refused, ``None``
    when it is safe to proceed.

    FAILURE POSTURE, DELIBERATELY UNLIKE ITS NEIGHBOURS. The guards either side
    of this one fail OPEN so they can never wedge a legitimate run. This one
    fails CLOSED: if the decision store cannot be read we do not know whether
    the user was asked, and starting the run anyway is the defect itself. The
    store retries once internally first (the cached Redis client is loop-bound
    and reads collapse intermittently -- see
    ``active_run_context.get_active_run``), so only a second consecutive failure
    reaches this refusal. A successful read finding nothing pending is NOT a
    failure and proceeds normally.
    """
    if not (agent_id and context_id):
        # No conversation to ask in (canvas AgentNode). Unchanged behaviour.
        return None

    from api.a2a_gateway.input_decision_context import (
        POLICY_BLOCK,
        POLICY_WARN,
        STATUS_APPROVED,
        STATUS_DECLINED,
        STATUS_PENDING,
        UNVERIFIABLE,
        clear_decision,
        get_decision,
        resolve_policy,
    )

    policy = resolve_policy(agent_record)
    if policy == POLICY_WARN:
        # Report-only deployment: the finding is surfaced, the run proceeds.
        return None

    record, state = await get_decision(
        org_id=org_id, agent_id=agent_id, context_id=context_id
    )

    if state == UNVERIFIABLE:
        return {
            "error": "input_decision_unverifiable",
            "refused": True,
            "message": (
                "Refused: an input-quality decision may be open for this "
                "conversation and its state could not be read, so I cannot "
                "confirm the user approved this run. Tell the user the run was "
                "not started for that reason and ask them to try again."
            ),
        }

    if not record:
        return None

    status = str(record.get("status") or "")
    findings = record.get("findings") or []

    def _covers_this_launch() -> Optional[bool]:
        """Whether this launch involves the file the decision was about.

        Applied to approvals AND declines: an answer about file A must not carry
        over to a run over file B in either direction. Without the decline side,
        a "no" dead-ended the conversation, because every later launch was
        refused -- including one over a corrected file, since a good file raises
        no new finding to overwrite the record.

        ``None`` means "cannot compare" (nothing named, or the launch carries no
        file information), which is treated as covering the launch so the gate
        errs towards asking rather than towards running. Filename-level only;
        this is not a content hash.
        """
        named = {
            str(f.get("filename")).strip().lower()
            for f in findings
            if f.get("filename")
        }
        if not named:
            return None
        launch_names = {
            str((entry or {}).get("filename") or "").strip().lower()
            for entry in (launch_files or [])
        }
        launch_names.discard("")
        if not launch_names:
            return None
        return bool(named & launch_names)

    if policy == POLICY_BLOCK and status in (STATUS_PENDING, STATUS_APPROVED):
        # Same scope doctrine as the confirm-policy branches below: a finding
        # about file A must not gate a run over file B. Without this, a
        # corrected upload stayed blocked until the record's TTL, because a
        # good file raises no new finding to overwrite the old one.
        if _covers_this_launch() is False:
            await clear_decision(
                org_id=org_id, agent_id=agent_id, context_id=context_id
            )
            return None
        # No consent path: this deployment has chosen that input reported as
        # unusable never runs, so an approval cannot override it.
        return {
            "error": "input_issue_blocked",
            "refused": True,
            "decision_id": record.get("decision_id"),
            "findings": findings,
            "message": (
                "Refused: I am configured never to run on input "
                "reported as unusable. Do not start the run. Tell the user what "
                "was wrong and ask for a corrected file."
            ),
        }

    if status == STATUS_PENDING:
        return {
            "error": "pending_input_decision",
            "refused": True,
            "decision_id": record.get("decision_id"),
            "findings": findings,
            "redirect_tool": "submit_input_decision",
            "message": (
                "Refused: an input issue was reported to the user in THIS "
                "conversation and they have not yet said whether to proceed. Do "
                "NOT start the run. State the finding in the main chat, ask a "
                "direct question ('shall I proceed with this file, yes or "
                "no?'), and when they answer call submit_input_decision. The "
                "run starts only after that."
            ),
        }

    if status == STATUS_DECLINED:
        if _covers_this_launch() is False:
            # The refusal was about a different file, so it must not block a
            # corrected upload.
            await clear_decision(
                org_id=org_id, agent_id=agent_id, context_id=context_id
            )
            return None
        return {
            "error": "input_decision_declined",
            "refused": True,
            "decision_id": record.get("decision_id"),
            "message": (
                "Refused: the user declined to proceed with this input. Do not "
                "start the run. Tell them it was not started, and offer to "
                "continue once they supply a corrected file."
            ),
        }

    if status == STATUS_APPROVED:
        # An approval about file A must not authorise a run over file B: the
        # same shape as the cross-run input leak in ENG-4110.
        if _covers_this_launch() is False:
            await clear_decision(
                org_id=org_id, agent_id=agent_id, context_id=context_id
            )
            return {
                "error": "input_decision_scope_mismatch",
                "refused": True,
                "message": (
                    "Refused: the user's approval was about a different file "
                    "from the one this run would use, so it does not carry "
                    "over. Report the input finding for the CURRENT file and "
                    "ask again."
                ),
            }
        return None

    return None


def make_workflow_tools(node) -> List:
    """Build the pydantic-ai tool list for an AgentNode.

    Returns ``[]`` when the node has no org context yet - prevents tests
    that don't set ``_orgid`` from failing at compile time.
    """

    if not getattr(node, "_orgid", None):
        logger.debug(
            "Skipping workflow tools - AgentNode has no _orgid context (likely a test fixture)"
        )
        return []

    org_id = node._orgid
    workflow_allowlist = list(node._validated_params.get("workflow_allowlist") or [])
    # ``workflow_allowlist_labels`` was being silently dropped here even
    # though the runtime-canvas materialiser (services/agent_runtime_canvas.py
    # ``_filter_agent_params_for_node``) DOES propagate it from the agent
    # record onto the AgentNode params.  The A2A ``list_workflows`` skill
    # (api/a2a_gateway/executor.py) honours both fields via
    # ``resolve_effective_workflows`` (UNION of explicit ids + label-matched);
    # the runtime canvas chat must agree with the A2A surface or the agent
    # ends up with different views of "what workflows can I invoke" between
    # the in-product chat panel and external A2A callers.  Symptom on EU
    # staging was: A2A ``list_workflows`` returned ``lightning_accelerator``
    # (matched via labels), in-product chat returned ``[]`` and the LLM
    # hallucinated a "login/organization issue" to explain it.
    workflow_allowlist_labels = list(
        node._validated_params.get("workflow_allowlist_labels") or []
    )
    # Requesting user's resolved id (OrchestratorContext._user_id on the
    # A2A chat path, AgentNode._user_id on the canvas path).  Used for
    # run/prompt-update attribution below, ALWAYS.
    user_id = getattr(node, "_user_id", None) or None
    # Per-user allowlist expansion is a separate concern from attribution:
    # workflows the user created / was granted access to are unioned into
    # the agent's effective set ONLY when the agent record's
    # ``allow_user_workflows`` toggle is ON (default OFF -- every caller
    # then sees exactly the configured allowlist).  The agent record is
    # present on the A2A chat path (OrchestratorContext); canvas
    # AgentNodes have no A2A agent config, so the union stays as-is
    # there.
    from api.a2a_gateway.card import user_workflows_enabled as _uwe

    _node_agent_record = getattr(node, "agent_record", None)
    if isinstance(_node_agent_record, dict):
        allowlist_user_id = user_id if _uwe(_node_agent_record) else None
    else:
        allowlist_user_id = user_id

    # Active-run context (OneEdge report 2026-06-16, C1). Present only on
    # the A2A chat path (the ChatContext carries ``agent_id`` /
    # ``context_id``); absent on the in-canvas AgentNode, where these are
    # None and the run-context store is simply skipped (tools then require
    # explicit ids exactly as before -- fully backward compatible).
    run_ctx_agent_id = getattr(node, "agent_id", None)
    run_ctx_context_id = getattr(node, "context_id", None)
    run_ctx_enabled = bool(run_ctx_agent_id and run_ctx_context_id)

    # ENG-3385 (sweep follow-up): live-turn hooks, present only on the A2A
    # chat orchestrator's context. ``notify_run_started`` pushes a
    # run_started frame onto the active chat SSE THE MOMENT a run exists
    # (even while wait_for_completion keeps this tool blocked);
    # ``chat_tool_wait_cap_s`` bounds how long a chat turn may block
    # synchronously inside wait_for_completion. Both are absent (None) on
    # canvas AgentNodes -- behaviour there is unchanged.
    notify_run_started = getattr(node, "notify_run_started", None)
    chat_wait_cap_s = getattr(node, "chat_tool_wait_cap_s", None)

    async def _resolve_active_run_id() -> Optional[str]:
        """Return the conversation's active run_id, or None.

        Lets a bare "approved"/answer resolve to the run the user started
        in this same conversation without them re-supplying the id.
        """
        if not run_ctx_enabled:
            return None
        from api.a2a_gateway.active_run_context import get_active_run

        record = await get_active_run(
            org_id=org_id,
            agent_id=run_ctx_agent_id,
            context_id=run_ctx_context_id,
        )
        return (record or {}).get("run_id")

    async def _resolve_from_inbox(
        kind: str, id_value: Optional[str]
    ) -> tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        """``(run_id, node_id, error)`` from the user's pending items of ``kind``,
        for a reply typed in a chat with no active run. One waiting item is
        unambiguous; several with no id given is a question back to the user."""
        if not (user_id and org_id):
            return None, id_value, None
        entry, pending = operator_inbox.pick_pending(
            await operator_inbox.list_pending(org_id=org_id, user_id=user_id),
            kind=kind,
            id_field="node_id",
            id_value=id_value,
        )
        if entry is not None:
            return entry.get("run_id"), entry.get("node_id"), None
        if len(pending) > 1:
            return (
                None,
                id_value,
                operator_inbox.pending_choice_error(pending, kind=kind),
            )
        return None, id_value, None

    async def _note_answering_in_inbox(
        session_id: str, node_id: str, kind: Optional[str] = None
    ) -> None:
        """Mark the inbox entry with the chat the answer is coming from, so the
        outcome row in the run's own chat can say it was answered elsewhere."""
        if not (user_id and org_id):
            return
        try:
            entry = await operator_inbox.find_pending(
                org_id=org_id,
                user_id=user_id,
                session_id=session_id,
                node_id=node_id,
                kind=kind or operator_inbox.KIND_CLARIFICATION,
            )
            if entry is not None:
                await operator_inbox.update_pending(
                    org_id=org_id,
                    user_id=user_id,
                    request_key=str(entry.get("request_key")),
                    answering_context_id=run_ctx_context_id,
                )
        except Exception as e:  # noqa: BLE001 - bookkeeping only
            logger.warning("inbox answering mark failed (non-fatal): %s", e)

    async def _remember_active_run_from_result(
        result: Optional[Dict[str, Any]],
    ) -> None:
        """A1: make the conversation's active run track the MOST-RECENT run.

        ``start_workflow`` already records its run, but a rerun / recover
        starts a brand-new run that the conversation should now be anchored
        to -- otherwise the orchestrator keeps referencing the PREVIOUS
        (source) run and a later bare answer resolves against the wrong run.
        Best-effort; never blocks the rerun.
        """
        if not run_ctx_enabled or not isinstance(result, dict):
            return
        new_run_id = result.get("run_id")
        if not new_run_id or result.get("error"):
            return
        try:
            from api.a2a_gateway.active_run_context import set_active_run

            await set_active_run(
                org_id=org_id,
                agent_id=run_ctx_agent_id,
                context_id=run_ctx_context_id,
                run_id=str(new_run_id),
                session_id=str(result.get("session_id") or new_run_id),
                workflow_id=(
                    str(result.get("workflow_id"))
                    if result.get("workflow_id")
                    else None
                ),
            )
        except Exception as e:  # noqa: BLE001 - convenience layer, never fatal
            logger.warning("rerun active-run update failed (non-fatal): %s", e)

    async def _cancel_after_stop(session_id: str) -> Dict[str, Any]:
        """A stop token alone does not end a Temporal run: the node's own
        cancel is retried, not honoured. The canvas Stop follows the token
        with a server-side cancel; the operator chat does the same."""
        from core.settings import settings

        if not (getattr(node, "operator_chat", False) and settings.USE_TEMPORAL):
            return {}
        from api.handlers.human_interruption.post import cancel_run_at_checkpoint

        try:
            cancelled, message = await cancel_run_at_checkpoint(session_id)
        except Exception as e:  # noqa: BLE001 - the token was delivered
            logger.warning("cancel after stop failed (non-fatal): %s", e)
            return {"run_cancelled": False}
        return {"run_cancelled": cancelled, "cancel_message": message}

    async def _pending_checkpoint_guard() -> Optional[Dict[str, Any]]:
        """Deterministic guard for the "answer mis-read as a NEW run" defect
        (AI Eval Importance, Problem B).

        While the conversation's active run has an OPEN human-input checkpoint
        (clarification OR intervention), the user's message must be routed to
        that checkpoint via ``submit_clarification_response`` /
        ``submit_intervention_response`` -- NOT used to start another run.
        Without this, a substantive clarification answer that happens to read
        like a task ("make it more technical for an AI/ML audience") could be
        interpreted as a command to ``start_workflow``, spawning a duplicate
        run and orphaning the original until it idle-timed out.

        Returns a redirect error dict when ``start_workflow`` must be refused
        for the active run; ``None`` when it is safe to proceed. Best-effort:
        any failure degrades to ``None`` (start is allowed) so the guard can
        never wedge a legitimately new run.
        """
        if not run_ctx_enabled:
            return None
        try:
            run_id = await _resolve_active_run_id()
            if not run_id:
                return None
            factory = async_session_factory(use_fresh_engine=False)
            async with factory() as session:
                run, _denied = await _load_run_for_node(
                    session, run_id, node, "view_content"
                )
            if run is None:
                return None
            status = (
                run.status.value if hasattr(run.status, "value") else str(run.status)
            )
            if status in _TERMINAL_STATUSES:
                # A1 (never bind to a stale/terminal run): the conversation's
                # anchored run has finished, so purge the stale Redis record
                # NOW rather than waiting for the next turn's grounding pass.
                # This stops a later read from re-binding to a terminal run,
                # and lets this turn's new run become the active one cleanly.
                try:
                    from api.a2a_gateway.active_run_context import clear_active_run

                    await clear_active_run(
                        org_id=org_id,
                        agent_id=run_ctx_agent_id,
                        context_id=run_ctx_context_id,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("guard clear_active_run failed: %s", e)
                return None
            pending = await _resolve_open_checkpoint(run)
            if not pending:
                # A1 (dup-run guard must not misfire with no open checkpoint):
                # the active run is live but parked on NOTHING -- so the user's
                # message is a legitimate new request ("modify the prompt and
                # run again"), not an answer.  Allow start_workflow to proceed.
                return None
            kind = (pending.get("input_kind") or "intervention").lower()
            if kind == "clarification":
                redirect_tool = "submit_clarification_response"
                how = (
                    "call submit_clarification_response(response=\"<the user's "
                    'message>") for the active run -- the run_id/request_id are '
                    "already resolved."
                )
            else:
                redirect_tool = "submit_intervention_response"
                how = (
                    "call submit_intervention_response(response=\"<the user's "
                    'message>") for the active run -- the run_id/node_id/'
                    "request_id are already resolved."
                )
            return {
                "error": "pending_checkpoint_open",
                "refused": True,
                "run_id": str(run.id),
                "pending": await _pending_for_model(node, run, pending),
                "redirect_tool": redirect_tool,
                "message": (
                    "Refused: the active run for THIS conversation "
                    f"({run.id}) is parked on an open "
                    f"{kind} checkpoint, so the user's message is their ANSWER "
                    "to it, not a request for a new run. Do NOT start another "
                    f"run -- {how} If the user EXPLICITLY asked to abandon the "
                    "current run and start over, first stop it with "
                    "stop_workflow, then start_workflow again."
                ),
            }
        except Exception as e:  # noqa: BLE001 - guard must never wedge a run
            logger.warning("pending-checkpoint guard failed (non-fatal): %s", e)
            return None

    async def _terminal_run_restart_guard(
        target_workflow_id: uuid.UUID, force_rerun: bool
    ) -> Optional[Dict[str, Any]]:
        """No-rerun / no-restart guard: refuse to re-start a workflow whose
        active run in this conversation already reached a terminal state that
        implies "don't quietly start another one" -- either it COMPLETED (hand
        back its deliverables instead) or the USER STOPPED it (stopping kills
        the workflow but not the turn that started it, and nothing reports the
        stop back into the conversation -- a tool waiting on the run just sees
        a terminal status with no outputs, reads that as "unfinished", and
        invites another ``start_workflow``).

        Resolves the active run ONCE and branches on its status, rather than
        two near-identical guards each re-resolving and re-loading the same
        row. Fires only when this ``start_workflow`` targets the SAME
        workflow as that run. A different workflow, a non-terminal / non-
        matching status, or an explicit ``force_rerun=True`` (the user asked
        to run it again), passes through untouched. Best-effort: any failure
        degrades to ``None`` (start allowed) so the guard can never wedge a
        legitimately new run.
        """
        if force_rerun or not run_ctx_enabled:
            return None
        try:
            run_id = await _resolve_active_run_id()
            if not run_id:
                return None
            run = await _load_run(run_id, org_id)
            if run is None:
                return None
            status = (
                run.status.value if hasattr(run.status, "value") else str(run.status)
            )
            if str(run.workflow_id) != str(target_workflow_id):
                return None
            if status == "success":
                artifacts, _ = await _resolve_run_output_files(str(run.id), org_id)
                return {
                    "error": "already_completed",
                    "refused": True,
                    # Deliberately NOT keyed ``run_id``: the orchestrator's
                    # stream pump treats a top-level ``run_id`` on a
                    # run-initiating tool's result as "a run started THIS
                    # turn" and emits a run_started frame + sets
                    # ChatTurnResult.run_id. This is a refusal for an
                    # ALREADY-terminal run, so surfacing it as ``run_id``
                    # would fabricate a run_started for a finished run.
                    # ``artifacts`` is still nested for _extract_artifacts.
                    "completed_run_id": str(run.id),
                    # Keyed OUT of the harvest (see ``_NON_DELIVERABLE_KEYS``):
                    # these files were delivered when the run finished, so they
                    # are not this turn's output. A turn that goes on to
                    # ``force_rerun=True`` would otherwise card the previous
                    # run's file again, under the new run's card.
                    "completed_run_files": artifacts,
                    "message": (
                        "Refused: this workflow already COMPLETED in this "
                        f"conversation (run {run.id}). Its files are listed "
                        "in completed_run_files, already delivered when that "
                        "run finished -- they are NOT this turn's output, so "
                        "do not paste their links into your reply. To put "
                        "them back in front of the user, call "
                        f'show_output(scope="primary", run_id="{run.id}"). '
                        "Do NOT ask for a run_id. If the user EXPLICITLY "
                        "asked to run it again, call "
                        "start_workflow(force_rerun=True) and leave these "
                        "files alone -- the new run's card delivers its own."
                    ),
                }
            if status in STOPPED_RUN_STATUSES:
                return {
                    "error": "stopped_by_user",
                    "refused": True,
                    # Deliberately NOT keyed ``run_id``, for the same reason
                    # as the already-completed refusal above: the stream
                    # pump would read one as a run started this turn and
                    # emit a run_started frame.
                    "stopped_run_id": str(run.id),
                    "message": (
                        "Refused: the USER stopped this workflow's run in "
                        f"this conversation (run {run.id}), with the Stop "
                        "control on the run card. Stopping is a decision "
                        "they made, not a failure and not something you "
                        "need to fix -- do NOT start another run to finish "
                        'the task. Tell them "you stopped the run" (never '
                        '"the run stopped" or "it was halted"), say what it '
                        "produced before it ended, and let them lead. Do "
                        "NOT ask whether to run it again. If they "
                        "EXPLICITLY ask for another run, call "
                        "start_workflow(force_rerun=True)."
                    ),
                }
            return None
        except Exception as e:  # noqa: BLE001 - guard must never wedge a run
            logger.warning("terminal-run restart guard failed (non-fatal): %s", e)
            return None

    async def _resolve_pending_input(run) -> Dict[str, Any]:
        """Best-effort {node_id, request_id, input_kind} for a run's open
        input-required request.

        Resolution order (first hit wins):

          1. The active-run context's persisted ``pending_*`` fields
             (populated at turn start by
             ``resolve_conversation_run_context`` -> ``set_pending_input``).
          2. The live Redis awaiting markers -- the AUTHORITATIVE signal for
             "a node is waiting right now". The Human Intervention node's
             ``request_id`` is a deterministic ``uuid5`` over
             ``(session, node)``, so given the awaiting node we can hand the
             submit the exact ids the node is keyed on without depending on
             event persistence (the intervention event is published to Redis
             only, never written to ``workflow_event`` -- the old
             ``event_type == "node.input_required"`` query below never matched
             for HITL).
          3. The most recent input-required row in ``workflow_event`` (legacy
             event_type strings), as a last-ditch fallback.

        Returns an empty dict when nothing is found -- callers then ask for
        the id rather than guessing (no false acks).
        """
        if run_ctx_enabled:
            from api.a2a_gateway.active_run_context import get_active_run

            record = await get_active_run(
                org_id=org_id,
                agent_id=run_ctx_agent_id,
                context_id=run_ctx_context_id,
            )
            # A1 (explicit run_id wins): the sticky ``pending_*`` belongs to
            # the conversation's ACTIVE run.  When the caller passed an
            # explicit run_id that targets a DIFFERENT run, that sticky
            # pending is for the wrong run -- skip it and resolve the
            # requested run's own live checkpoint below.  Only trust sticky
            # when it is for the very run we're resolving.
            if (
                record
                and record.get("pending_request_id")
                and str(record.get("run_id")) == str(run.id)
            ):
                return {
                    "node_id": record.get("pending_node_id"),
                    "request_id": record.get("pending_request_id"),
                    "input_kind": record.get("pending_input_kind"),
                }
        # Reliable live signal: the Redis awaiting marker + deterministic
        # request_id. This is what makes a bare "approved" resolve for the
        # Human Intervention node.
        via_redis = await _resolve_pending_via_redis(run)
        if via_redis:
            return via_redis
        # Agent-driven clarification (ENG-2805): the pause keeps the run at
        # RUNNING with pause_state=null and has no durable Redis marker, so
        # resolve it explicitly. Prefer the LIVE Temporal query (authoritative,
        # independent of the best-effort lifecycle-event persister) and fall
        # back to the durable ``lifecycle`` row. This is what makes a bare
        # answer resolve for the clarification family (e.g. the AI Eval
        # Importance workflow, which pauses via ``ask_user_clarification``
        # rather than a Human Intervention node).
        via_clarification = await _resolve_pending_clarification_via_temporal(run)
        if via_clarification:
            return via_clarification
        via_clarification = await _resolve_pending_clarification_via_events(run)
        if via_clarification:
            return via_clarification
        # Last-ditch fallback: latest input-required event for the run. The
        # legacy worker persists these under ``awaiting_intervention`` /
        # ``awaiting_user_input`` (the adapter maps both to the v1
        # ``node.input_required``); accept the v1 string too for forward
        # compatibility.
        try:
            factory = async_session_factory(use_fresh_engine=False)
            async with factory() as session:
                stmt = (
                    select(WorkflowEvent)
                    .where(
                        WorkflowEvent.run_id == run.id,
                        WorkflowEvent.event_type.in_(_INPUT_REQUIRED_EVENT_TYPES),
                    )
                    .order_by(WorkflowEvent.timestamp.desc())
                )
                row = (await session.execute(stmt)).scalars().first()
            if row is not None:
                data = row.event_data or {}
                inner = data.get("data") if isinstance(data.get("data"), dict) else {}
                return {
                    "node_id": row.node_instance_id
                    or data.get("node_id")
                    or inner.get("node_id"),
                    "request_id": data.get("request_id") or inner.get("request_id"),
                    "input_kind": data.get("input_kind") or inner.get("input_kind"),
                }
        except Exception as e:  # noqa: BLE001 - best-effort resolution
            logger.warning("pending-input resolution failed for run: %s", e)
        # Last-resort document-review signal: the saved review-document record,
        # for the marker/event-gap turns where everything above misses, so a
        # bare approval resolves the same review pause the grounding and status
        # tools already surface (ENG-3543) instead of failing with "could not
        # find a pending request". Guarded against already-finished nodes inside
        # the helper.
        via_review_doc = await _resolve_pending_via_review_doc(run)
        if via_review_doc:
            return via_review_doc
        return {}

    # Closures - each tool is a plain async def, pydantic-ai wraps them.

    async def list_workflows() -> List[Dict[str, Any]]:
        """List published workflows I am allowlisted to invoke.

        The effective set is ``explicit_ids UNION label_matches UNION
        user-accessible workflows`` -- identical to what the A2A
        ``list_workflows`` skill returns
        (``resolve_effective_workflows`` in api/a2a_gateway/card.py).

        Returns a list of ``{id, name, description, version_id, labels}``
        dicts.  When BOTH ``workflow_allowlist`` and
        ``workflow_allowlist_labels`` are empty, we fall back to "all
        published workflows in this org" -- the implicit fan-out the
        chat panel has always had.  The A2A surface is stricter (empty
        + empty = no workflows, explicit opt-in only); the divergence
        is intentional -- the in-product chat is the operator's own
        agent and the implicit view is convenient, while external
        bearers should fail-closed.  In the in-product chat the set is
        further narrowed to workflows the requesting user may run under
        the workflow sharing rules.
        """

        from api.a2a_gateway.card import org_workflows_for_user

        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            # Published org workflows PLUS the requesting user's OWN
            # workflows regardless of publish_to_api. Workflows shared by
            # others stay publish-gated; explicit allowlist entries are
            # published at write time (see org_workflows_for_user).
            all_workflows, user_set = await org_workflows_for_user(
                session, org_id=org_id, user_id=allowlist_user_id
            )

            # No filtering configured at all -- show everything published
            # in the org plus the user's own (in-product convenience; the
            # A2A surface is stricter and returns [] in this case).
            if not workflow_allowlist and not workflow_allowlist_labels:
                eligible = all_workflows
            else:
                explicit_set = set(workflow_allowlist) | user_set
                label_query = set(workflow_allowlist_labels)
                # Resolve labels in one batch to avoid an N+1 round-trip.
                # We import lazily to keep the unit-test fixture path
                # (which constructs AgentNode without DB access) free
                # of the LabelService dependency.
                if label_query:
                    from services.labels import LabelService

                    label_service = LabelService(session)
                    labels_map = await label_service.get_labels_for_workflows(
                        [wf.id for wf in all_workflows]
                    )
                else:
                    labels_map = {}

                eligible = []
                for wf in all_workflows:
                    if str(wf.id) in explicit_set:
                        eligible.append(wf)
                        continue
                    if label_query:
                        wf_label_names = {
                            getattr(label, "name", str(label))
                            for label in labels_map.get(wf.id, [])
                        }
                        if wf_label_names & label_query:
                            eligible.append(wf)

            runnable = await _user_runnable_workflow_ids(node, session, org_id)
            if runnable is not None:
                eligible = [wf for wf in eligible if str(wf.id) in runnable]

            return [
                {
                    "id": str(wf.id),
                    "name": wf.name or "",
                    "description": wf.description or "",
                    "version_id": str(wf.id),
                }
                for wf in eligible
            ]

    async def _effective_workflow_entries() -> List[tuple]:
        """``(slug, uuid)`` pairs for the agent's effective workflow set.

        Strict semantics (``resolve_effective_workflows``: explicit ids
        UNION label matches; empty + empty = no workflows) -- deliberately
        NOT the in-product "all published" fallback ``list_workflows``
        uses, because these entries decide whether an omitted workflow_id
        can be defaulted, and defaulting must only happen for an agent
        that was explicitly scoped to exactly one workflow.  Fail-soft:
        resolution trouble returns [] (callers then error with a generic
        "call list_workflows" hint rather than crashing).
        """
        from api.a2a_gateway.card import resolve_effective_workflows, workflow_slug

        try:
            factory = async_session_factory(use_fresh_engine=False)
            async with factory() as session:
                adverts = await resolve_effective_workflows(
                    session,
                    org_id=org_id,
                    workflow_allowlist=workflow_allowlist,
                    workflow_allowlist_labels=workflow_allowlist_labels,
                    user_id=allowlist_user_id,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "effective-workflow resolution failed (non-fatal, org=%s): %s",
                org_id,
                e,
            )
            return []
        return [
            (
                workflow_slug(adv.workflow.name)
                if adv.workflow.name
                else str(adv.workflow.id),
                str(adv.workflow.id),
            )
            for adv in adverts
        ]

    async def start_workflow(
        workflow_id: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
        wait_for_completion: bool = False,
        timeout_s: int = 600,
        force_rerun: bool = False,
        run_evals: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Start a workflow run, optionally with per-run node parameter
        overrides (ENG-2721).

        Args
        ----
        workflow_id : str, optional
            Capitol workflow id (UUID or slug).  MAY be omitted when this
            agent is scoped to exactly one workflow -- that workflow runs.
            Omitting it on a multi-workflow agent returns an error listing
            the available ids.  A workflow_id that is supplied but does
            not resolve ALWAYS errors (never falls back) -- the error
            names the agent's available workflows.
        inputs : dict, optional
            Workflow input overrides keyed by ``"<node_instance_id>.<field_id>"``
            (the composite id ``describe_workflow.fields[].id`` returns)
            OR by bare ``field_id`` when unambiguous across nodes.

            ``inputs`` is the **per-run override map**, scoped to fields
            the user is allowed to change: the ``user_prompt`` on agent
            nodes, the ``task`` prompt on chat-output nodes (i.e. *what to
            do* this run), declared input fields, and input files.
            Overrides are run-scoped -- they NEVER rewrite the saved
            workflow.  Author-fixed parameters are NOT overridable and do
            not appear in ``describe_workflow``: ``system_prompt`` (agent
            identity), ``model`` / ``temperature``, and the output
            type/format (fixed by the output node type -- you cannot make
            an xlsx node emit CSV/Word).  A key targeting any of these (or
            an unknown id) is returned in ``ignored_inputs`` and does NOT
            bind -- never report it as applied.

            Values for FILE-typed fields can be passed as:

              * a bare ``file_id`` string previously returned by
                ``upload_file`` or ``request_upload_url``, OR the
                ``files[].id`` of a file PRODUCED by a completed
                workflow run (from ``get_workflow_output``) -- this tool
                auto-wraps either into the ``FileUploadResponse`` shape
                the ``file_input_node`` runtime requires (resolving the
                artifact's ``filename``/``s3_key``/``mime_type`` from
                the A2A upload cache or, for produced files, the run's
                stored outputs -- ENG-3266). This is how you CHAIN
                workflows: wait for run A to complete, read its produced
                file ids off ``get_workflow_output``, and pass them
                directly as FILE inputs to run B. Or
              * the full ``FileUploadResponse`` dict -- passed through
                untouched (useful when the caller built it themselves).

            Values for non-FILE fields (including prompt text) are passed
            through as-is.
        wait_for_completion : bool
            If true, blocks until the run reaches a terminal status (or
            ``timeout_s`` elapses), and returns the outputs in the response.
        timeout_s : int
            Wall-clock timeout when waiting. Default 10 min.
        force_rerun : bool
            Set true ONLY when the user has EXPLICITLY asked to run the same
            workflow again. Left false (the default), this tool refuses to
            re-start a workflow that already COMPLETED in this conversation and
            returns its existing deliverables instead -- so an accidental
            re-trigger never spawns a duplicate run or wastes compute. It also
            refuses to re-start a run the user STOPPED, which they stopped
            because they wanted it to stop.
        run_evals : bool, optional
            ENG-5459 evals toggle. Leave unset (``None``, the default) so the
            run follows the ``run_evals_by_default`` agent config. Pass
            ``True`` ONLY when the user explicitly asked for a FULL new run
            with evals (this overrides an agent whose default is off, for
            this run only); pass ``False`` ONLY when the user explicitly
            asked to skip evals. When the run executes without evals, the
            result carries ``evals: "skipped"`` -- tell the user the run is
            executing without evaluations and that they can ask for them
            afterwards. To evaluate a run that already COMPLETED without
            evals, do NOT start a new run: call
            ``rerun_from_node(source_session_id=<that run>,
            starting_node_ids=["evals_node"])`` -- only the Evaluations
            node(s) execute, everything upstream is reused from cache.

        Task-relevance guard: when an amended task (a ``user_prompt`` / ``task``
        override in ``inputs``) is not relevant to what the target agent is set
        up to do, this tool does NOT start the run and returns
        ``{"status": "rejected", ...}`` describing which task was rejected and
        why. There is NO override -- relay the reason to the user, ask them for
        a task that matches the agent's purpose, and only then call this tool
        again with the corrected task.

        Connection guard: when the workflow uses a Composio integration the
        user has not connected, this tool does NOT start the run and returns
        ``{"status": "authorization_required", "connect_markdown": {...}}``.
        Relay each ``connect_markdown`` link to the user verbatim; once they
        have connected, call this tool again.

        Returns ``{run_id, session_id, status, output?}`` plus, when
        ``inputs`` were supplied, ``applied_inputs`` / ``ignored_inputs``
        -- ALWAYS check ``ignored_inputs``: a key that didn't match any
        overridable field was dropped (wrong node_id / field_id, or an
        author-locked parameter), so the run proceeds WITHOUT that
        override.  Re-check ``describe_workflow.fields[].id`` and retry
        rather than telling the user the override was applied.

        When any FILE input was passed as a bare ``file_id``, the result
        also carries ``resolved_files`` (``[{key, file_id, filename}]``,
        ENG-3153) -- use each ``filename`` whenever you mention that file
        to the user; never the UUID.
        """

        if not workflow_id:
            # "Run it" with a canvas on screen names its own workflow, so
            # settle that before the catalog below asks which one they meant.
            canvas_redirect = _canvas_run_redirect(node)
            if canvas_redirect is not None:
                return canvas_redirect
            # ENG-3388: an OMITTED workflow_id on a single-workflow agent
            # is unambiguous -- run that workflow.  Only the omitted case
            # defaults; a supplied id that fails to resolve errors below.
            entries = await _effective_workflow_entries()
            if len(entries) == 1:
                workflow_id = entries[0][1]
                logger.info(
                    "start_workflow: workflow_id omitted; defaulting to the "
                    "agent's only allowlisted workflow %s (org=%s)",
                    workflow_id,
                    org_id,
                )
            else:
                return {"error": _missing_workflow_id_message(entries)}

        # ENG-3444: honor the documented "UUID or slug" contract (the old
        # code allowlist-checked the RAW id against UUID entries and then
        # required a UUID parse, so every slug -- even the correct one from
        # ``list_workflows.id`` -- failed with "does not resolve").  Resolve
        # the identifier FIRST: UUIDs pass through; anything else is a
        # normalized-slug lookup against the agent's effective workflows.
        # The allowlist is then enforced on the RESOLVED UUID.
        # The effective set is fetched AT MOST ONCE per call and reused by
        # both the slug resolution and the allowlist check below -- two
        # independent fetches let a transient failure of the second one
        # reject an id the first one just resolved (PR #1048 review).
        effective_entries: Optional[List[tuple]] = None
        try:
            wf_uuid = uuid.UUID(str(workflow_id))
        except (TypeError, ValueError):
            from api.a2a_gateway.card import workflow_slug as _wf_slug

            effective_entries = await _effective_workflow_entries()
            wanted = _wf_slug(str(workflow_id))
            matches = [uid for slug, uid in effective_entries if slug == wanted]
            if len(matches) != 1:
                # 0 = unknown slug; >1 = ambiguous (two same-named
                # workflows).  Either way the caller must pick from the
                # catalog (by UUID when names collide).
                return {
                    "error": _workflow_not_found_message(workflow_id, effective_entries)
                }
            workflow_id = matches[0]
            wf_uuid = uuid.UUID(workflow_id)

        # Enforce against the EFFECTIVE set (explicit ids UNION label-matched
        # workflows) -- the same posture as describe_workflow and the wire
        # call_workflow path.  Checking only the explicit list rejected
        # label-allowed workflows that list_workflows and the prompt
        # advertise as callable (PR #1048 review).  The explicit list is
        # kept as a union fallback so a transient effective-resolution
        # failure (entries == []) can never reject a valid explicit-id call.
        # Checked BEFORE the allowlist: the workflow open on the canvas is
        # always eligible for the redirect regardless of whether it happens
        # to be allowlisted for headless start_workflow calls -- otherwise a
        # canvas workflow outside the allowlist gets a "not found" error
        # instead of being pointed at run_workflow (PR #1438 review).
        canvas_redirect = _canvas_run_redirect(node, wf_uuid)
        if canvas_redirect is not None:
            return canvas_redirect

        if workflow_allowlist or workflow_allowlist_labels:
            if effective_entries is None:
                effective_entries = await _effective_workflow_entries()
            allowed_ids = {uid for _, uid in effective_entries} | {
                str(w) for w in (workflow_allowlist or [])
            }
            if str(wf_uuid) not in allowed_ids:
                return {
                    "error": _workflow_not_found_message(workflow_id, effective_entries)
                }

        # Before the guards below: the rerun guard hands back the conversation's
        # completed-run artifacts and input translation inspects the schema,
        # neither of which a user without run permission may receive.
        if _enforces_user_workflow_access(node):
            factory = async_session_factory(use_fresh_engine=False)
            async with factory() as session:
                denied = await _user_workflow_access_error(
                    node, session, wf_uuid, "run"
                )
            if denied is not None:
                return denied

        # No-rerun / no-restart guard FIRST: if THIS workflow already
        # completed in this conversation, return its deliverables instead of
        # spawning a duplicate; if the USER STOPPED it, don't quietly replace
        # it with a new one (unless the user explicitly asked to run it again
        # -> force_rerun). This MUST precede the pending-checkpoint guard:
        # that guard clears the active-run record for ANY terminal status, so
        # running it first would wipe the record this guard reads and
        # silently let a duplicate start through.
        terminal_guard = await _terminal_run_restart_guard(wf_uuid, force_rerun)
        if terminal_guard is not None:
            return terminal_guard

        # Hard guard (Problem B): if the conversation's active run is parked on
        # an open human-input checkpoint, the user's message is the ANSWER to
        # it -- refuse to spawn a duplicate run and redirect to submit_*.
        guard = await _pending_checkpoint_guard()
        if guard is not None:
            return guard

        from api.v1.handlers.run import TriggerWorkflowRunHandler
        from api.v1.schemas.trigger import TriggerRunRequest

        session_id = str(uuid.uuid4())
        current_user = _synthetic_user(node)

        # ENG-2469: translate the caller-supplied ``inputs`` dict into
        # the ``InputNodeOverride[]`` shape the trigger handler expects.
        # Before this, ``inputs`` was silently discarded (TODO marker in
        # the prior implementation), so workflows fell back to their
        # stored defaults -- which for FILE inputs on most EY agents are
        # partial (just filename + size, no file_id / s3_key) and fail
        # the ``FileUploadResponse`` validator in ``file_input_node``.
        # That's the root cause of the "AI provider is experiencing an
        # outage" message Scale saw when triggering workflows via chat.
        from services.orchestrator.questionnaire import QuestionnaireInputError

        try:
            (
                translated_inputs,
                applied_keys,
                ignored_keys,
                resolved_files,
            ) = await _translate_inputs(
                inputs=inputs,
                workflow_uuid=wf_uuid,
                org_id=org_id,
                artifact_resolver=_resolve_artifact_for_org(org_id),
                agent_id=str(getattr(node, "agent_id", "") or ""),
                known_file_ids=_conversation_file_ids_loader(
                    org_id, run_ctx_agent_id, run_ctx_context_id
                ),
            )
        except (QuestionnaireInputError, UnresolvedFileInputError) as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("start_workflow: input translation failed")
            return {"error": f"Failed to translate inputs: {e}"}

        # Task-relevance guardrail (ENG-3634): score the amended task(s) against
        # the target node's author-locked persona + the workflow purpose and
        # HARD-FAIL (do not start the run) when a task is not relevant, rather
        # than burning a long run on garbage output. Placed AFTER translation so
        # it scores only overrides that will actually bind (keys the translator
        # dropped never hard-fail) and BEFORE the trigger so no run is created.
        # Runs only on the A2A chat path (``node`` carries ``agent_record``).
        # Fail-open: any lookup/judge error proceeds with the run.
        rejection = await _task_alignment_gate(
            node=node,
            workflow_uuid=wf_uuid,
            org_id=org_id,
            overrides=translated_inputs,
        )
        if rejection is not None:
            return rejection

        # ENG-4104 / item 132: refuse to launch while an input-quality
        # decision is unanswered, declined, or was approved for a different
        # file. Placed AFTER translation for the same reason as the alignment
        # gate -- it needs the files this run would actually bind, so the
        # scope check compares against reality rather than the raw ``inputs``
        # dict -- and BEFORE the trigger so no run is created.
        input_decision = await _pending_input_decision_guard(
            org_id=org_id,
            agent_id=run_ctx_agent_id,
            context_id=run_ctx_context_id,
            agent_record=getattr(node, "agent_record", None),
            launch_files=resolved_files,
        )
        if input_decision is not None:
            return input_decision

        # Composio connections: a toolkit the user has not connected stops
        # the run here, with a connect link, before a run row exists.
        # prepare_workflow fails closed on the same check.
        composio_block = await _composio_connection_gate(
            node, workflow_uuid=wf_uuid, org_id=org_id
        )
        if composio_block is not None:
            return composio_block

        # Scale display contract: project the conversation's standing
        # "show intermediate files" preference (or the agent default) onto
        # the new run's session flag BEFORE triggering the run -- the
        # Temporal worker can emit ``node_files_available`` as soon as the
        # run starts, and those emissions read this flag. Best-effort by
        # construction.
        from services.node_results.display_preference import (
            seed_session_preference,
        )

        await seed_session_preference(
            session_id,
            agent_record=getattr(node, "agent_record", None) or {},
            agent_id=getattr(node, "agent_id", None),
            context_id=getattr(node, "context_id", None),
        )

        # ENG-5459: explicit per-run request wins; otherwise the agent's
        # ``run_evals_by_default`` config decides (absent -> True, i.e. eval
        # nodes run -- the pre-toggle behaviour).
        _effective_run_evals: bool = _resolve_run_evals(node, run_evals)

        try:
            factory = async_session_factory(use_fresh_engine=False)
            async with factory() as session:
                handler = TriggerWorkflowRunHandler(session)
                _trigger_metadata: dict = {
                    "started_by_agent_node": str(getattr(node, "node_id", "")),
                    "started_by_session": str(getattr(node, "_session_id", "")),
                }
                if run_ctx_context_id:
                    _trigger_metadata["conversation_id"] = str(run_ctx_context_id)
                if run_ctx_agent_id:
                    _trigger_metadata["agent_id"] = str(run_ctx_agent_id)
                response = await handler.handle(
                    org_id=org_id,
                    workflow_id=wf_uuid,
                    request=TriggerRunRequest(
                        session_id=session_id,
                        version_id=None,
                        inputs=translated_inputs,
                        metadata=_trigger_metadata,
                        # ENG-2805: the orchestrator handles the
                        # clarification loop transparently for chat-driven
                        # runs (see module constant docs above).
                        allow_clarifications=_CHAT_ORCHESTRATOR_ALLOW_CLARIFICATIONS,
                        clarification_timeout_secs=(
                            _CHAT_ORCHESTRATOR_CLARIFICATION_TIMEOUT_SECS
                        ),
                        run_evals=_effective_run_evals,
                    ),
                    current_user=current_user,
                    # ENG-2486: forward the sealed OboSession the chat
                    # executor stashed on the orchestrator context so the
                    # run carries Token C (downstream-scoped) for its EY MCP
                    # calls.  ``None`` for non-Entra callers (the gateway
                    # never set it) -> the run stays on the bearer path.
                    obo_session=getattr(node, "obo_session", None),
                )
                await session.commit()

                # ENG-3234: upfront sub-agent roster for the version this run
                # was pinned to, mirroring the ``call_workflow`` skill, so a
                # chat consumer can also render every sub-agent ("To Be
                # Started") immediately.  Best-effort; never fails the run.
                sub_agents: List[Dict[str, Any]] = []
                try:
                    from api.a2a_gateway.event_adapter import (
                        sub_agents_from_payload,
                    )
                    from db.models.workflow_version import (
                        WorkflowVersion as WorkflowVersionModel,
                    )

                    run_row = (
                        await session.execute(
                            select(WorkflowRun).where(WorkflowRun.id == response.run_id)
                        )
                    ).scalar_one_or_none()
                    version_id = getattr(run_row, "version_id", None)
                    if version_id is not None:
                        version = (
                            await session.execute(
                                select(WorkflowVersionModel).where(
                                    WorkflowVersionModel.id == version_id
                                )
                            )
                        ).scalar_one_or_none()
                        # ENG-5459: same toggle the run was started with, so
                        # the roster never lists an eval node the run skips.
                        sub_agents = sub_agents_from_payload(
                            (version.payload if version else None) or {},
                            run_evals=_effective_run_evals,
                        )
                except Exception as roster_err:  # noqa: BLE001
                    logger.warning(
                        "ENG-3234: sub-agent roster lookup failed for run %s: %s",
                        response.run_id,
                        roster_err,
                    )
        except Exception as e:
            logger.exception("start_workflow failed")
            return {"error": f"Failed to start workflow: {e}"}

        result: Dict[str, Any] = {
            "run_id": str(response.run_id),
            "session_id": response.session_id,
            "status": response.status,
            # ENG-3234 (additive): full node roster + count so the caller can
            # show total run progress from the start.
            "sub_agents": sub_agents,
            "total_sub_agents": len(sub_agents),
        }
        if not _effective_run_evals:
            # ENG-5459: surface the skip so the model reports it -- the user
            # should hear the run executes without evaluations and that they
            # can have them run afterwards from the eval point
            # (``rerun_from_node(source_session_id, ["evals_node"])``).
            result["evals"] = "skipped"
        # ENG-3385: the run exists NOW -- push its run_id onto the live chat
        # stream immediately, before any wait_for_completion block below.
        # Without this, a blocking wait meant the caller learned the run_id
        # only when the tool returned (minutes later, long after every proxy
        # idle limit had killed the SSE) or never.
        if notify_run_started is not None:
            try:
                # ENG-3234: the roster built above rides the frame too, so a
                # chat-driven consumer (OneEdge) gets the full sub-agent list
                # on the FIRST frame of the run -- no join against the
                # workflow event stream required.
                notify_run_started(
                    str(response.run_id), "start_workflow", sub_agents=sub_agents
                )
            except Exception as e:  # noqa: BLE001 - side-channel only
                logger.warning("notify_run_started failed (non-fatal): %s", e)
        # C1 (OneEdge 2026-06-16): remember this as the conversation's
        # active run so a later bare "approved"/answer resolves without the
        # user re-supplying the run_id. Best-effort; never blocks the run.
        if run_ctx_enabled:
            try:
                from api.a2a_gateway.active_run_context import set_active_run

                await set_active_run(
                    org_id=org_id,
                    agent_id=run_ctx_agent_id,
                    context_id=run_ctx_context_id,
                    run_id=str(response.run_id),
                    session_id=str(response.session_id),
                    workflow_id=str(wf_uuid),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("set_active_run failed (non-fatal): %s", e)
        # ENG-4104: the approval authorised THIS run and no other. Consumed
        # after the trigger succeeded, so a failed trigger does not burn the
        # user's answer, and a later run needs a fresh detection and a fresh
        # answer.
        #
        # The consume is UNCONDITIONAL. It used to run only when a re-read
        # returned a record, which meant an unreadable store (or a read that
        # raced the write) left the approval in place to authorise a second
        # launch, defeating the single-use rule. The read below is now only
        # enrichment for the result and cannot gate the delete.
        #
        # Residual, stated rather than hidden: ``consume_decision`` is a
        # best-effort delete, so a Redis failure can leave an approval behind.
        # The scope check bounds the damage -- a lingering approval can only
        # authorise a run over the SAME file the user already approved, never a
        # different one.
        if run_ctx_enabled:
            try:
                from api.a2a_gateway.input_decision_context import (
                    consume_decision,
                    get_decision,
                )

                _decision, _state = await get_decision(
                    org_id=org_id,
                    agent_id=run_ctx_agent_id,
                    context_id=run_ctx_context_id,
                )
                if _decision:
                    result["input_decision"] = {
                        "decision_id": _decision.get("decision_id"),
                        "status": _decision.get("status"),
                        "answered_text": _decision.get("answered_text"),
                        "answered_at": _decision.get("answered_at"),
                    }
            except Exception as e:  # noqa: BLE001
                logger.warning("input-decision read for the run record: %s", e)
            try:
                from api.a2a_gateway.input_decision_context import consume_decision

                await consume_decision(
                    org_id=org_id,
                    agent_id=run_ctx_agent_id,
                    context_id=run_ctx_context_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("input-decision consume failed: %s", e)
        # ENG-2721: surface which override keys actually bound to a field
        # so the LLM never reports a silently-dropped override as applied.
        if inputs:
            result["applied_inputs"] = applied_keys
            result["ignored_inputs"] = ignored_keys
            if ignored_keys:
                result["ignored_inputs_hint"] = (
                    "These keys did not match any overridable field on the "
                    "workflow (wrong node_id/field_id, or an author-locked "
                    "parameter) and were NOT applied -- the run uses the "
                    "saved values for them. Cross-check the exact ids via "
                    "describe_workflow.fields[].id."
                )
            # ENG-3153: give the LLM each auto-wrapped file's real filename
            # so it refers to files by name (never by artifact UUID) in
            # every subsequent user-facing message.
            if resolved_files:
                result["resolved_files"] = resolved_files

        if wait_for_completion:
            # ENG-3385 (sweep follow-up): on the chat surface, cap the
            # synchronous wait. The model choosing wait_for_completion=true
            # for "run it and summarize" phrasing used to block this tool
            # for up to timeout_s=600 -- the chat SSE went silent and the
            # proxy layer killed it (~125s Cloudflare / 300s ELB). Short
            # workflows still finish inside the cap and return outputs
            # unchanged; longer ones return promptly with the run_id and
            # an explicit still-running note the model can relay.
            effective_timeout_s = timeout_s
            if chat_wait_cap_s is not None:
                effective_timeout_s = min(timeout_s, int(chat_wait_cap_s))
            terminal = await _await_terminal(
                response.session_id,
                effective_timeout_s,
                on_input_issue=getattr(node, "notify_input_issue", None),
            )
            if terminal:
                result.update(terminal)
                try:
                    issues = await _collect_input_issues(response.run_id)
                    if issues:
                        result["input_issues"] = issues
                except Exception as e:  # noqa: BLE001
                    logger.warning("start_workflow input_issues enrich failed: %s", e)
            elif effective_timeout_s < timeout_s:
                result["wait_capped"] = True
                # The cap can expire because the run is BLOCKED on a human
                # checkpoint (interactive-feedback review or Human Intervention),
                # not merely slow. Resolve it from the SAME live signals
                # get_workflow_run_status uses and surface it here, so the agent
                # prompts the user NOW instead of reporting a bare "still
                # running" and going silent until asked -- which otherwise lets
                # the checkpoint time out un-answered.
                pending: Dict[str, Any] = {}
                try:
                    capped_run = await _load_run(response.run_id, org_id)
                    if capped_run is not None:
                        pending = await _resolve_open_checkpoint(capped_run)
                except Exception as e:  # noqa: BLE001 - never break the run return
                    logger.warning("start_workflow checkpoint enrich failed: %s", e)
                if pending and pending.get("request_id"):
                    pending = await _pending_for_model(node, capped_run, pending)
                    result["pending_input"] = pending
                    result["pause_state"] = _pending_pause_state(pending)
                    result["wait_note"] = _pending_checkpoint_wait_note(pending)
                else:
                    result["wait_note"] = _still_executing_wait_note(
                        str(response.run_id), effective_timeout_s
                    )

        return result

    async def get_workflow_run_status(run_id: str) -> Dict[str, Any]:
        """Return current status + lifecycle metadata for a workflow run.

        Checkpoint-aware: an agent-clarification pause keeps the run at
        ``status=running`` with ``pause_state=null`` (it never goes through
        ``mark_run_paused_activity``), so a bare "is it done yet?" mid-pause
        would otherwise report "running, nothing waiting" while a question is
        genuinely open -- the live failure where the orchestrator can't see the
        clarification. We resolve the open checkpoint from the SAME live signals
        the grounding block and submit tools use (Redis marker, Temporal
        ``pending_clarifications`` query, durable event rows) and, when one is
        found, both attach a ``pending_input`` block and synthesise a
        ``pause_state`` so a consumer keying off ``pause_state`` (the status
        turn, the LLM) sees the checkpoint instead of a misleading null.
        """

        run = await _load_run(run_id, org_id)
        if run is None:
            return {"error": f"Run {run_id} not found"}
        if _enforces_user_workflow_access(node):
            factory = async_session_factory(use_fresh_engine=False)
            async with factory() as session:
                denied = await _run_access_error(
                    node, session, run, run_id, "view_content"
                )
            if denied is not None:
                return denied
        status = _run_to_status_dict(run)
        # Compact node progress so a mid-run status turn can report "N nodes
        # completed so far" from this (tiny) call. Without it the tool returns
        # only "running", and the agent escalates to get_workflow_events --
        # whose unbounded return is what overflows the window. Best-effort:
        # a cheap grouped COUNT, never the node outputs.
        try:
            status["node_progress"] = await _resolve_node_progress(run.id)
        except Exception as e:  # noqa: BLE001 - never break a status read
            logger.warning("get_workflow_run_status node-progress enrich failed: %s", e)
        try:
            is_terminal = status.get("status") in _TERMINAL_STATUSES
            if not is_terminal and not status.get("pause_state"):
                pending = await _resolve_open_checkpoint(run)
                if pending and pending.get("request_id"):
                    pending = await _pending_for_model(node, run, pending)
                    status["pending_input"] = pending
                    status["pause_state"] = _pending_pause_state(pending)
        except Exception as e:  # noqa: BLE001 - never break a status read
            logger.warning("get_workflow_run_status checkpoint enrich failed: %s", e)
        try:
            issues = await _collect_input_issues(run.id)
            if issues:
                status["input_issues"] = issues
        except Exception as e:  # noqa: BLE001 - never break a status read
            logger.warning("get_workflow_run_status input_issues enrich failed: %s", e)
        return status

    async def get_workflow_output(run_id: str) -> Dict[str, Any]:
        """Return the final outputs of a completed run, keyed by node_id.

        ENG-3391: node ``output_data`` (generated report text, tables,
        analysis) is returned as a BOUNDED PREVIEW plus produced-file
        references -- never the full blob. Inlining every node's whole output
        here previously produced 1.17M-2.3M-token tool-returns that blew the
        orchestrator's 1M context window on the very next model turn. That
        failure is persistent and un-compactable: the oversized tool-return
        sits in the message tail, which the compaction capability preserves
        verbatim, so every retry re-sends it. To read a single node's full
        value call ``get_node_output(run_id, node_id)``; to hand the user a
        deliverable, use each produced file's ``download_url``.
        """

        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            run, denied = await _load_run_for_node(
                session, run_id, node, "view_content"
            )
            if denied is not None:
                return denied
            if run is None:
                return await _run_lookup_error(session, run_id, org_id)
            run_status = getattr(run, "status", None)
            status = (
                run_status.value
                if hasattr(run_status, "value")
                else str(run_status)
                if run_status is not None
                else None
            )
            if status and status not in _TERMINAL_STATUSES:
                return {
                    "error": "run_not_terminal",
                    "run_id": str(run.id),
                    "status": status,
                    "message": (
                        f"Run {run.id} is still {status}; final outputs are not "
                        "available yet. Monitor it with get_workflow_run_status "
                        "and call get_workflow_output again once it is terminal."
                    ),
                }
            query = text(
                """
                SELECT node_id, output_data AS output, created_at
                FROM node_result
                WHERE run_id = :run_id
                ORDER BY created_at DESC
                """
            )
            rows = (await session.execute(query, {"run_id": run.id})).fetchall()
            # A3: classify each node so an echoed workflow INPUT value is not
            # reported among the run's produced deliverables.
            node_metadata = await _build_eval_node_metadata(session, run)
            # A2A output allowlist: resolve the EY-pinned deliverable node(s)
            # for this workflow so the ``outputs`` map below (assembled from
            # every node_result) is trimmed to just those. ``None`` (no entry,
            # or none resolved) -> unchanged (every produced node returned).
            from api.a2a_gateway.a2a_output_allowlist import (
                resolve_allowed_node_ids,
            )

            allowed_output_ids = await resolve_allowed_node_ids(
                session,
                getattr(run, "workflow_id", None),
                getattr(run, "version_id", None),
            )
        # ENG-3390: stored presigned URLs were signed mid-run with STS session
        # credentials and are dead for runs longer than the STS session --
        # re-sign every embedded file record at call time.
        from services.node_results.url_refresh import refresh_presigned_urls

        outputs: Dict[str, Any] = {}
        input_echoes: Dict[str, Any] = {}

        # The per-node assembly below is synchronous CPU work --
        # _preview_output_data walks every node's full output_data (several MB
        # in total on a large multi-node run) and refresh_presigned_urls
        # re-signs each surviving file record. Run inline on the event loop it
        # (a) starves the chat keepalive pump, so the SSE stream goes
        # byte-silent and the client reports "connection timed out waiting for
        # the agent", and (b) cannot be interrupted by the blocking turn's
        # wall-clock budget (asyncio.timeout only cancels at an await). Offload
        # it to a worker thread so the loop stays live. The result SIZE is
        # already bounded (see the preview + char-cap logic); this bounds the
        # TIME/work to produce it. Only pure-CPU / local-signing work runs in
        # the thread -- the async DB reads above have already completed and
        # their rows are materialised.
        def _assemble() -> None:
            for row in rows:
                # node_id is a UUID column; key the maps by str so the result
                # is JSON-serialisable (json.dumps rejects non-str dict keys,
                # and ``default=`` does not apply to keys) -- matches
                # get_eval_report and the A2A executor, which also str() it.
                node_key = str(row.node_id) if row.node_id else None
                if (
                    node_key
                    and node_key not in outputs
                    and node_key not in input_echoes
                ):
                    meta = node_metadata.get(node_key) or {}
                    od = row.output if isinstance(row.output, dict) else {}
                    # Preview FIRST, then re-sign only the (small) surviving
                    # structure. Signing + deep-copying the full multi-MB blob
                    # before truncating each field to a ~2K-char preview was
                    # ~99% wasted work. _preview_output_data preserves reference
                    # keys and URL values verbatim, so every file record stays
                    # recognisable and re-signable after the preview -- the
                    # call-time re-sign is not lost, only the bulk text it used
                    # to walk.
                    # ENG-3542: durable mode -- any URL surviving into the
                    # preview may be pasted into a reply and clicked hours
                    # later, so it must be a click-time-fetched link, never
                    # a session-bound presigned URL.
                    signed = refresh_presigned_urls(
                        _preview_output_data(row.output), durable=True
                    )
                    if _classify_output_kind(meta, od) == "input_echo":
                        input_echoes[node_key] = signed
                    else:
                        outputs[node_key] = signed

        await asyncio.to_thread(_assemble)

        # A2A output allowlist: keep only the EY-pinned deliverable node(s),
        # applying any per-node file-glob rule. Mirrors the typed skill.
        # Eval outputs are ALWAYS retained regardless of the allowlist; eval-ness
        # is read from the RAW ``row.output`` (its ``metadata.eval_summary``),
        # not the bounded preview which may have dropped the summary. No-op when
        # the workflow has no entry (``allowed_output_ids`` None).
        if allowed_output_ids is not None:
            from api.a2a_gateway.a2a_output_allowlist import (
                filter_output_data_files,
            )
            from api.a2a_gateway.eval_rollup import is_eval_output

            eval_keys = {
                str(r.node_id) for r in rows if r.node_id and is_eval_output(r.output)
            }
            kept_outputs: Dict[str, Any] = {}
            for k, v in outputs.items():
                if k in allowed_output_ids:
                    kept_outputs[k] = filter_output_data_files(v, allowed_output_ids[k])
                elif k in eval_keys:
                    kept_outputs[k] = v
            outputs = kept_outputs
            input_echoes = {
                k: v for k, v in input_echoes.items() if k in allowed_output_ids
            }

        # ENG-3405 reuse: hand back produced files by reference so the agent can
        # deliver downloads without reading node text at all.
        try:
            files, other_outputs = await _resolve_run_output_files(str(run.id), org_id)
        except Exception as e:  # noqa: BLE001 - references are best-effort
            logger.warning(
                "get_workflow_output(%s): file resolve failed: %s", run_id, e
            )
            files, other_outputs = [], []

        result: Dict[str, Any] = {
            "run_id": str(run.id),
            # Produced deliverables only (bounded previews); echoed inputs split out.
            "outputs": outputs,
            "input_echoes": input_echoes,
            "files": files,
            # ENG-3566: generated files from non-primary nodes that the output
            # marker suppressed. NOT part of the default deliverable set --
            # surface these ONLY when the user asks what else was produced,
            # then hand them over via each entry's download_url.
            "other_outputs": other_outputs,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "output_note": (
                "Text outputs are bounded previews. Fetch a node's full value "
                "with get_node_output(run_id, node_id); deliver produced files "
                "to the user via their download_url. By default present ONLY "
                "`files` (the primary deliverable[s]). `other_outputs` lists "
                "additional generated files that were suppressed as non-"
                "deliverables; reveal and offer them only if the user asks "
                "what else was generated."
            ),
        }

        # Backstop: even bounded previews across many nodes/files can add up.
        # Guarantee this tool-return alone can never exceed the window.
        def _over_cap(payload: Dict[str, Any]) -> bool:
            return len(json.dumps(payload, default=str)) > _WORKFLOW_OUTPUT_CHAR_CAP

        if _over_cap(result):
            # Backstop trimming: pull the SMALLEST lever that brings the payload
            # under the cap, so we never sacrifice one component when trimming a
            # cheaper one alone would fit (avoids both "drop previews when
            # trimming other_outputs would fit" and "empty other_outputs when
            # dropping previews would fit"). ``files`` (the primary deliverable
            # links) is only ever trimmed as a last resort.
            stub_outputs = {nid: {"preview_omitted": True} for nid in outputs}
            stub_echoes = {nid: {"preview_omitted": True} for nid in input_echoes}
            _base_note = (
                "Output previews omitted to stay within the context window. "
                "Fetch each node's value with get_node_output(run_id, node_id), "
                "or deliver produced files to the user via their download_url."
            )

            # Keep output_note (which grows with files_truncated) inside the
            # MEASURED payload, so appending the note can never push a
            # just-under-cap result back over after the final check.
            def _set_note() -> None:
                note = _base_note
                if result.get("files_truncated"):
                    note += (
                        f" {result['files_truncated']} further file reference(s) "
                        "omitted to fit the window; fetch remaining outputs by "
                        "node with get_node_output(run_id, node_id)."
                    )
                result["output_note"] = note

            def _trim_other_outputs() -> None:
                while _over_cap(result) and result.get("other_outputs"):
                    cut = max(1, len(result["other_outputs"]) // 2)
                    result["other_outputs"] = result["other_outputs"][:-cut]
                    result["other_outputs_truncated"] = (
                        result.get("other_outputs_truncated", 0) + cut
                    )

            def _drop_previews() -> None:
                result["outputs"] = stub_outputs
                result["input_echoes"] = stub_echoes
                result["context_capped"] = True
                _set_note()

            # Preferred: pull the smallest lever that fits.
            if not _over_cap({**result, "other_outputs": []}):
                # other_outputs alone is the overflow -> trim it, keep previews.
                _trim_other_outputs()
            elif not _over_cap(
                {**result, "outputs": stub_outputs, "input_echoes": stub_echoes}
            ):
                # previews alone are the overflow -> drop them, keep other_outputs.
                _drop_previews()
            else:
                # Neither single lever fits -> drop previews and trim
                # other_outputs together.
                _drop_previews()
                _trim_other_outputs()

            # Hard guarantee: catch ANY residual over-cap, including boundary
            # cases where a truncation counter or the note that the branches
            # add tips the payload back over. Force the ceiling by dropping
            # previews, emptying the explore list, trimming file refs, then
            # collapsing node stubs to counts. No-op in the common case where a
            # single lever above already fit.
            if _over_cap(result):
                _drop_previews()
                _trim_other_outputs()
                while _over_cap(result) and result["files"]:
                    cut = max(1, len(result["files"]) // 2)
                    result["files"] = result["files"][:-cut]
                    result["files_truncated"] = result.get("files_truncated", 0) + cut
                    _set_note()
                if _over_cap(result):
                    result["outputs"] = {"_omitted_node_count": len(outputs)}
                    result["input_echoes"] = {"_omitted_node_count": len(input_echoes)}
        return result

    async def get_node_output(run_id: str, node_id: str) -> Dict[str, Any]:
        """Return ONE node's full ``output_data`` for a completed run (ENG-3391).

        The escape hatch paired with ``get_workflow_output``'s bounded
        previews: call this to retrieve a single node's complete generated
        text/tables/analysis when the preview is not enough. Scoped to one node
        so it cannot re-introduce the multi-node aggregate that overflows the
        window. For a very large single node, prefer delivering its produced
        file via ``download_url`` rather than reading the full text into
        context.
        """

        # node_id is a UUID column; bind a UUID, not the raw string param
        # (asyncpg rejects a text bind against a uuid column at execute time).
        try:
            node_uuid = uuid.UUID(str(node_id))
        except (TypeError, ValueError):
            return {"error": f"Invalid node_id: {node_id!r}"}

        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            run, denied = await _load_run_for_node(
                session, run_id, node, "view_content"
            )
            if denied is not None:
                return denied
            if run is None:
                return {"error": f"Run {run_id} not found"}
            query = text(
                """
                SELECT node_id, output_data AS output, created_at
                FROM node_result
                WHERE run_id = :run_id AND node_id = :node_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            row = (
                await session.execute(query, {"run_id": run.id, "node_id": node_uuid})
            ).fetchone()
        if row is None:
            return {"error": f"Node {node_id} not found in run {run_id}"}
        from services.node_results.url_refresh import refresh_presigned_urls

        return {
            "run_id": str(run.id),
            "node_id": str(row.node_id),
            # ENG-3542: durable links here too -- this is the full-value
            # escape hatch whose URLs equally end up in replies.
            "output": refresh_presigned_urls(row.output, durable=True),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }

    async def get_eval_report(run_id: str) -> Dict[str, Any]:
        """Return the eval roll-up for a run (ENG-2649).

        Folds every Evaluations node's results for the run into ONE
        suite-level report so you don't have to walk individual eval node
        outputs and re-derive pass/fail yourself:

            {
              "run_id": "<uuid>",
              "eval_rollup": {
                "has_evals": bool,
                "summary": {total, passed, failed, na, errors,
                            success_rate, suite_passed},
                "evals": [{node_id, node_display_name, eval_id, eval_name,
                           eval_type, eval_subtype, status, passed, score,
                           verdict, dimensions, error_msg}, ...],
                "eval_nodes": [{node_id, node_display_name, total, passed,
                                failed, na, errors}, ...]
              }
            }

        ``eval_type`` is one of ``pass_fail`` / ``score`` / ``exemplar`` /
        ``dimensional``; each eval is attributed to its producing node.
        Reads the same ``eval_summary`` metadata the gateway's
        ``get_workflow_output`` ``eval_rollup`` section is built from, so the
        chat path and the A2A path agree.  When the run has no eval nodes,
        ``has_evals`` is ``False`` with zeroed counts.

        Use this when the user asks for "the eval report / eval results /
        did the evals pass" for a run.
        """
        from api.a2a_gateway.eval_rollup import build_eval_rollup

        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            run, denied = await _load_run_for_node(
                session, run_id, node, "view_content"
            )
            if denied is not None:
                return denied
            if run is None:
                return {"error": f"Run {run_id} not found"}
            query = text(
                """
                SELECT node_id, output_data AS output, created_at
                FROM node_result
                WHERE run_id = :run_id
                ORDER BY created_at DESC
                """
            )
            rows = (await session.execute(query, {"run_id": run.id})).fetchall()
            node_metadata = await _build_eval_node_metadata(session, run)

        outputs: Dict[str, Any] = {}
        for row in rows:
            node_id = str(row.node_id) if row.node_id else None
            if node_id and node_id not in outputs:
                outputs[node_id] = row.output

        return {
            "run_id": str(run.id),
            "eval_rollup": build_eval_rollup(outputs, node_metadata),
        }

    async def get_workflow_events(
        run_id: str,
        since: Optional[str] = None,
        types: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Replay events for a run from the workflow_event table.

        The persisted ``event_type`` is the LEGACY worker string (e.g.
        ``awaiting_intervention``, ``end``, ``execution_failed``); the wire /
        A2A events surface and this tool's documented vocabulary use the
        frozen v1 names (``node.input_required``, ``node.node_completed``,
        ``node.node_failed`` ...). We translate each row to its v1 type and
        filter on the v1 name, so a caller asking for ``node.input_required``
        actually sees the Human Intervention ``awaiting_intervention`` row.
        Before this, ``types=["node.input_required"]`` matched NOTHING for a
        HITL run (the row is stored as ``awaiting_intervention``), so the
        orchestrator concluded "no node.input_required event has been emitted"
        while a panel was wide open -- and never routed a bare approval.

        The translation is the wire's own (``event_adapter.project_v1_event_type``,
        ENG-5362), so a row is typed here exactly as the A2A stream types it:
        a failed node's ``end`` trailer and the ``execution_successful`` /
        ``node_result`` / run-level rows come back as ``unknown`` (with
        ``legacy_type`` intact), never as ``node.node_completed`` or as their
        raw string. A ``types`` filter is applied to that projected type, so
        asking for ``node.node_completed`` returns one row per completed node,
        and ``total_count`` counts projected matches (older rows included),
        not every legacy ``end`` row.
        """
        from api.a2a_gateway.event_adapter import (
            _PG_LEGACY_TO_V1,
            adapt_pg_event,
            project_v1_event_type,
            resume_point,
        )

        types_filter = set(types) if types else None
        # Push the type filter into SQL so we never materialise the whole log.
        # ``event_type`` is stored as the LEGACY string; match a row when its
        # mapped v1 name OR its raw stored value is requested -- exactly the
        # in-Python match this replaced ((v1 in filter) or (legacy in filter)).
        wanted_types: Optional[set[str]] = None
        if types_filter is not None:
            wanted_types = {
                legacy for legacy, v1 in _PG_LEGACY_TO_V1.items() if v1 in types_filter
            } | types_filter

        since_dt: Optional[datetime] = resume_point(since)

        # Checkpoint/control events are few per run and are how the orchestrator
        # detects mid-run input (HITL). A query scoped to ONLY those must NOT be
        # windowed -- limiting it could hide an open-input event on a run with
        # many checkpoints. Any broader query (unfiltered, or a high-volume
        # detail filter like node.node_completed) still gets the recent window.
        # The exemption is checked against the v1-normalised request so it holds
        # whether the caller used the v1 name (``node.input_required``) or the
        # legacy stored name (``awaiting_intervention``/``run_paused``) -- both
        # are accepted by the filter above, so both must be exempt.
        requested_v1 = (
            {_PG_LEGACY_TO_V1.get(t, t) for t in types_filter}
            if types_filter is not None
            else None
        )
        window_exempt = (
            requested_v1 is not None and requested_v1 <= _UNWINDOWED_EVENT_TYPES
        )

        older_rows: List[Any] = []
        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            run, denied = await _load_run_for_node(
                session, run_id, node, "view_content"
            )
            if denied is not None:
                return denied
            if run is None:
                return {"error": f"Run {run_id} not found"}

            def _scoped(stmt: Select[Any]) -> Select[Any]:
                stmt = stmt.where(WorkflowEvent.run_id == run.id)
                if since_dt is not None:
                    stmt = stmt.where(WorkflowEvent.timestamp >= since_dt)
                if wanted_types is not None:
                    stmt = stmt.where(WorkflowEvent.event_type.in_(wanted_types))
                return stmt

            # Bound at the DATABASE, not in Python: fetch only the most-recent
            # window (newest-first + LIMIT) so a run with a huge event history
            # never transfers/materialises the whole log. ``total`` comes from a
            # separate COUNT. Restore chronological order for the caller.
            total = (
                await session.execute(
                    _scoped(select(func.count()).select_from(WorkflowEvent))
                )
            ).scalar_one()
            if window_exempt:
                # Checkpoint/control lookup: return the complete (small) set in
                # chronological order -- never window it.
                rows = list(
                    (
                        await session.execute(
                            _scoped(select(WorkflowEvent)).order_by(
                                WorkflowEvent.timestamp.asc()
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            else:
                # ``id`` as the tiebreaker makes the window and the
                # older-rows scan below disjoint and complete partitions
                # of the same ordering even when timestamps collide.
                newest_first = (
                    WorkflowEvent.timestamp.desc(),
                    WorkflowEvent.id.desc(),
                )
                rows = list(
                    (
                        await session.execute(
                            _scoped(select(WorkflowEvent))
                            .order_by(*newest_first)
                            .limit(_EVENTS_REPLAY_WINDOW)
                        )
                    )
                    .scalars()
                    .all()
                )
                rows.reverse()
                if types_filter is not None and total > len(rows):
                    # The projected-type filter below can drop rows the
                    # legacy pre-filter admitted (a failed node's ``end``
                    # trailer asked for as ``node.node_completed``), and the
                    # COUNT cannot see that. Project the rows OLDER than the
                    # window as well -- type columns only, bounded by the
                    # legacy filter, not the whole log -- so ``total`` counts
                    # matches rather than candidates.
                    older_rows = list(
                        (
                            await session.execute(
                                _scoped(
                                    select(
                                        WorkflowEvent.event_type,
                                        WorkflowEvent.event_data,
                                        WorkflowEvent.node_instance_id,
                                    )
                                )
                                .order_by(*newest_first)
                                .offset(_EVENTS_REPLAY_WINDOW)
                            )
                        ).all()
                    )

        # Persisted event payloads embed file records whose URLs were signed
        # MID-RUN (NodeFile ``presigned_url``s, File-input ``download_url``
        # echoes) -- dead by read time for any run longer than the signing
        # STS session. The A2A wire strips these in the event adapter
        # (ENG-3393), but this TOOL replay bypassed the adapter and handed
        # them to the orchestrator verbatim, which harvests ``download_url``-
        # bearing dicts into ``artifact`` chat frames and pastes URLs into
        # replies. Re-mint every such slot as a durable click-time-fetched
        # link, same as ``get_workflow_output`` (ENG-3542). CPU-bound walk
        # over up to _EVENTS_REPLAY_WINDOW payloads -> worker thread, for
        # the same keepalive-starvation reason as the output assembly above.
        from services.node_results.url_refresh import refresh_presigned_urls

        def _event_payload(row: Any, v1_type: str) -> Any:
            payload = refresh_presigned_urls(row.event_data, durable=True)
            if v1_type == "node.input_required":
                return _omit_review_document_link(payload)
            return payload

        def _matches(row: Any, v1_type: str) -> bool:
            # The wire's projection (ENG-5362): the SQL pre-filter above is
            # by legacy type, which is necessary but not sufficient -- an
            # ``end`` row that is a failed node's trailer projects to
            # ``unknown``, not ``node.node_completed``.  Apply the ``types``
            # filter to the projected type (or the raw stored name, which
            # the filter also accepts) so the answer matches what the same
            # filter yields on the A2A stream.
            return (
                types_filter is None
                or v1_type in types_filter
                or row.event_type in types_filter
            )

        def _assemble_events() -> tuple[List[Dict[str, Any]], int]:
            out: List[Dict[str, Any]] = []
            for row in rows:
                v1_type = project_v1_event_type(adapt_pg_event(row))
                if not _matches(row, v1_type):
                    continue
                out.append(
                    {
                        "ts": row.timestamp.isoformat() if row.timestamp else None,
                        "type": v1_type,
                        "legacy_type": row.event_type,
                        "node_id": row.node_instance_id,
                        "payload": _event_payload(row, v1_type),
                    }
                )
            older_matches = sum(
                1
                for row in older_rows
                if _matches(row, project_v1_event_type(adapt_pg_event(row)))
            )
            return out, older_matches

        events, older_matches = await asyncio.to_thread(_assemble_events)
        if types_filter is not None:
            # Rows the projected-type filter dropped are not matches, in the
            # window or before it, so ``total`` is what survived projection
            # -- never the legacy COUNT, which would report dropped trailers
            # as "older, omitted" completions.
            total = len(events) + older_matches

        # Payloads are returned VERBATIM (never truncated) so a HITL /
        # clarification ``data.prompt`` the agent must surface is never cut off.
        # A currently-open checkpoint sits in the recent window. Size backstop:
        # if the window is still oversized (pathologically large payloads), drop
        # whole OLDEST events until it fits -- never mutate a payload. For the
        # authoritative current run state, prefer ``get_workflow_run_status``.
        windowed = events
        while (
            len(windowed) > 1
            and len(json.dumps(windowed, default=str)) > _WORKFLOW_OUTPUT_CHAR_CAP
        ):
            windowed = windowed[1:]
        # Terminal backstop: dropping whole events stops at one, so a SINGLE
        # remaining event whose OWN payload exceeds the cap would still overflow
        # the context. Replace that lone payload with a DETERMINISTICALLY bounded
        # preview of its serialised form -- this holds whatever the payload shape
        # is (one huge string, many short strings, a preserved URL/reference),
        # which a per-string preview does not guarantee. This applies to
        # control/checkpoint events too: a normal HITL payload is tiny and never
        # triggers this, and for the pathological case (e.g. a huge
        # ``preview_context``) the reply-routing fields (request_id/node_id) stay
        # recoverable via get_workflow_run_status' pending_input, so bounding here
        # prevents an overflow that would otherwise fail the whole status turn.
        payload_previewed = False
        if (
            len(windowed) == 1
            and len(json.dumps(windowed, default=str)) > _WORKFLOW_OUTPUT_CHAR_CAP
        ):
            only = dict(windowed[0])
            raw = json.dumps(only["payload"], default=str)
            only["payload"] = {
                "preview": raw[:_NODE_OUTPUT_PREVIEW_CHARS],
                "truncated_chars": len(raw) - _NODE_OUTPUT_PREVIEW_CHARS,
                "note": (
                    "payload too large to inline; for the current run state use "
                    "get_workflow_run_status"
                ),
            }
            windowed = [only]
            payload_previewed = True
        result: Dict[str, Any] = {
            "run_id": str(run.id),
            "events": windowed,
            "count": len(windowed),
        }
        if payload_previewed:
            result["payload_truncated"] = True
        if total > len(windowed):
            result["total_count"] = total
            result["truncated"] = True
            result["note"] = (
                f"Showing the {len(windowed)} most recent of {total} matching "
                "events; older are omitted to bound context. For the current "
                "run state prefer get_workflow_run_status."
            )
        return result

    async def get_workflow_chain_of_thought(
        run_id: str, agent_node_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return reasoning trace from agent nodes inside a workflow run."""

        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            run, denied = await _load_run_for_node(
                session, run_id, node, "view_content"
            )
            if denied is not None:
                return denied
            if run is None:
                return {"error": f"Run {run_id} not found"}
            query = text(
                """
                SELECT node_id, output_data AS output, created_at
                FROM node_result
                WHERE run_id = :run_id
                ORDER BY created_at ASC
                """
            )
            rows = (await session.execute(query, {"run_id": run.id})).fetchall()

        trace: List[Dict[str, Any]] = []
        for row in rows:
            if agent_node_id and row.node_id != agent_node_id:
                continue
            output = row.output if isinstance(row.output, dict) else {}
            messages = (
                output.get("messages")
                or output.get("agent_messages")
                or output.get("message_history")
                or []
            )
            if isinstance(messages, list):
                for msg in messages:
                    trace.append(
                        {
                            "node_id": row.node_id,
                            "ts": row.created_at.isoformat()
                            if row.created_at
                            else None,
                            "role": msg.get("role") if isinstance(msg, dict) else None,
                            "content": msg.get("content")
                            if isinstance(msg, dict)
                            else str(msg),
                            "tool_calls": msg.get("tool_calls")
                            if isinstance(msg, dict)
                            else None,
                            "tool_results": msg.get("tool_results")
                            if isinstance(msg, dict)
                            else None,
                        }
                    )
        return {"run_id": str(run.id), "trace": trace, "count": len(trace)}

    # Lifecycle ops - thin wrappers around the existing endpoint helpers.
    # We call HTTP for these because the stop/pause/resume signal handling
    # lives in the Temporal-aware route layer, not the service layer.

    async def stop_workflow(run_id: Optional[str] = None) -> Dict[str, Any]:
        """Stop a running workflow.

        If the run is parked on an open Human Intervention checkpoint, the stop
        is delivered as a ``"stop"`` intervention response (the panel's own
        halt path) instead of the lifecycle stop endpoint. This is what makes a
        bare "no, stop it" reliably HALT a paused run from the chat
        orchestrator: the lifecycle ``stop-workflow`` endpoint is an in-cluster
        ``localhost`` HTTP call that is NOT reachable from the gateway in every
        topology (it returns "All connection attempts failed"), whereas the
        intervention-stop path goes through Redis and actually stops the parked
        node. ``run_id`` is OPTIONAL: when omitted it resolves to the
        conversation's active run, so "stop it" works without ids.
        """
        from_inbox = False
        if not run_id:
            run_id = await _resolve_active_run_id()
            # Deciding a checkpoint from another chat is an operator-chat
            # feature. On the A2A path the inbox belongs to the bearer's
            # minting user and is shared by every caller on that bearer.
            if not run_id and getattr(node, "operator_chat", False):
                run_id, _, inbox_error = await _resolve_from_inbox(
                    operator_inbox.KIND_INTERVENTION, None
                )
                if inbox_error is not None:
                    return inbox_error
                from_inbox = run_id is not None
            if not run_id:
                return {
                    "error": (
                        "No active run in this conversation to stop. Start a "
                        "run first, or pass the run_id explicitly."
                    )
                }

        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            run, denied = await _load_run_for_node(session, run_id, node, "run")
        if denied is not None:
            return denied
        if run is None:
            return {"error": f"Run {run_id} not found"}

        # Parked on a human-input checkpoint? Halt via the intervention-stop
        # path (deliver the "stop" token to the waiting node) -- robust
        # regardless of whether the LLM reached for stop_workflow or
        # submit_intervention_response on a denial.
        def _nothing_left_to_stop() -> Dict[str, Any]:
            # The inbox named this run only because its checkpoint was
            # waiting; with that decided, a lifecycle stop would kill a run
            # the user may have just continued.
            return {
                "success": False,
                "delivered": False,
                "run_id": str(run.id),
                "session_id": run.session_id,
                "note": (
                    "The checkpoint on this run was already decided, so there "
                    "was nothing to stop and the run was left alone. Pass the "
                    "run_id explicitly to stop a run that is still going."
                ),
            }

        pending = await _resolve_pending_input(run)
        if from_inbox and not (pending.get("node_id") and pending.get("request_id")):
            return _nothing_left_to_stop()
        if pending.get("node_id") and pending.get("request_id"):
            from api.handlers.human_interruption.post import (
                publish_intervention_response,
            )

            publish_kwargs: Dict[str, Any] = {}
            if getattr(node, "operator_chat", False):
                publish_kwargs["resolution"] = "stopped"
                await _note_answering_in_inbox(
                    run.session_id,
                    pending["node_id"],
                    kind=operator_inbox.KIND_INTERVENTION,
                )
            publish_result = await publish_intervention_response(
                session_id=run.session_id,
                node_id=pending["node_id"],
                request_id=pending["request_id"],
                user_input=_INTERVENTION_STOP_TOKEN,
                workflow_id=str(run.workflow_id),
                **publish_kwargs,
            )
            if publish_result.get("delivered"):
                cancel_result = await _cancel_after_stop(run.session_id)
                if run_ctx_enabled:
                    try:
                        from api.a2a_gateway.active_run_context import (
                            clear_pending_input,
                        )

                        await clear_pending_input(
                            org_id=org_id,
                            agent_id=run_ctx_agent_id,
                            context_id=run_ctx_context_id,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("clear_pending_input failed (non-fatal): %s", e)
                return {
                    "success": True,
                    "delivered": True,
                    "method": "intervention_stop",
                    "run_id": str(run.id),
                    "session_id": run.session_id,
                    "node_id": pending["node_id"],
                    "request_id": pending["request_id"],
                    "message": (
                        "Stop delivered to the open Human Intervention "
                        "checkpoint; the run will halt."
                    ),
                    **cancel_result,
                }
            if from_inbox:
                return _nothing_left_to_stop()
            # Not actually awaiting (raced/closed) -- fall through to the
            # lifecycle stop below.

        return await _call_lifecycle_endpoint(node, str(run.id), "stop-workflow")

    async def pause_workflow(run_id: str) -> Dict[str, Any]:
        """Pause a running workflow (Temporal signal)."""
        return await _call_lifecycle_endpoint(node, run_id, "pause-workflow")

    async def resume_workflow(
        run_id: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Resume a paused workflow (Temporal signal)."""
        return await _call_lifecycle_endpoint(
            node, run_id, "resume-workflow", body=payload
        )

    async def rerun_from_node(
        source_session_id: str,
        starting_node_ids: List[str],
        payload: Optional[Dict[str, Any]] = None,
        run_evals: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Re-run a workflow from a specific node downward (classic rerun).

        Starts a NEW run that re-executes the chosen node(s) and
        everything downstream of them, while every completed upstream
        node from the source run is reused from cache (its
        ``node_result`` is seeded onto the new run with
        ``cached_from_run_id`` lineage and emits no ``node.node_started``).
        The new run emits ``rerun_started`` / ``rerun_seeded`` lifecycle
        events listing ``cached_node_ids`` / ``fresh_node_ids``.

        Use ``recover_failed_run`` instead when the user asks to recover
        a FAILED run "from the last good step" -- it locates the failed
        node automatically.  Use ``rerun_step_in_context`` to re-run
        exactly ONE node while reusing everything else (upstream AND
        downstream).

        **Evaluations on demand (ENG-5459).** When a run completed WITHOUT
        evaluations (my ``run_evals_by_default`` is off, or the result
        said ``evals: "skipped"``) and the user asks for them, call
        ``rerun_from_node(source_session_id=<that run>,
        starting_node_ids=["evals_node"])``. ``"evals_node"`` is an alias
        that expands to every Evaluations node in the workflow -- you do
        not need their instance ids (they are not on the roster when evals
        were off). Only the Evaluations node(s) execute; every upstream
        node is reused from the source run's cache. Evals are forced ON
        for that rerun automatically. Do NOT ``start_workflow`` a whole
        new run just to get evals.

        Args:
            source_session_id: The source run's session_id (or run_id). The
                run must have FINISHED (success / failed / stopped /
                cancelled): a running or paused source is refused, because
                its cache is still partial -- wait for
                ``get_workflow_run_status`` to report a terminal status.
            starting_node_ids: Node id(s) to re-execute from (each plus
                its downstream set runs fresh). ``["evals_node"]`` =
                "run the evaluations for this run".
            payload: Optional full AdvancedWorkflowPayload override.
                Omit to inherit the source run's stored version payload.
            run_evals: Leave unset so the rerun follows the
                ``run_evals_by_default`` agent config (forced ON when a
                starting node is an Evaluations node). Pass ``True`` /
                ``False`` only on an explicit user request.

        Returns the rerun response, including the new ``run_id`` /
        ``session_id`` to follow with ``get_workflow_run_status`` /
        ``get_workflow_events``.
        """
        result = await _rerun_in_process(
            node,
            source_session_id,
            starting_node_ids,
            payload,
            single_step=False,
            run_evals=run_evals,
        )
        # A1: anchor the conversation to the new run so later turns reference
        # THIS run, not the source run.
        await _remember_active_run_from_result(result)
        return result

    async def recover_failed_run(
        run_id: str,
        node_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Recover a FAILED run by re-running from its last good step (ENG-2647).

        Finds the node where the source run failed (the run's
        ``node.node_failed`` event), then starts a NEW run that
        re-executes that node and everything downstream of it while
        reusing every completed upstream node's cached output -- so the
        caller does not re-pay for the upstream work.  The new run
        emits ``rerun_started`` / ``rerun_seeded`` lifecycle events
        carrying ``cached_node_ids`` / ``fresh_node_ids`` /
        ``source_run_id``; reused nodes emit no ``node.node_started``.

        Use this when the user says things like "recover from the last
        step", "recover the failed run", "retry from where it failed",
        or "rerun from the last good node".  When the user names a
        specific node to restart from, pass it as ``node_id`` to skip
        the failed-node lookup.

        Args:
            run_id: The failed run's run_id (or session_id).
            node_id: Optional explicit node to restart from.  Omit to
                auto-detect the failed node from the run's event log.
            payload: Optional full AdvancedWorkflowPayload override.
                Omit to inherit the source run's stored version payload.

        Returns the rerun response (new ``run_id`` / ``session_id``)
        plus ``failed_node_id`` and the recorded failure details so you
        can explain to the user what is being recovered.
        """

        failure: Optional[Dict[str, Any]] = None
        start_node_id = node_id
        if not start_node_id:
            factory = async_session_factory(use_fresh_engine=False)
            async with factory() as session:
                run, denied = await _load_run_for_node(session, run_id, node, "run")
                if denied is not None:
                    return denied
                if run is None:
                    return {"error": f"Run {run_id} not found"}
                failure = await _find_failed_node(session, run)
            if not failure:
                return {
                    "error": (
                        f"Run {run_id} has no node-failure event -- nothing to "
                        "recover from. Check the run with "
                        "get_workflow_run_status / get_workflow_events; if a "
                        "specific node should be re-run anyway, pass node_id "
                        "explicitly (or use rerun_step_in_context for a "
                        "single-step redo)."
                    )
                }
            start_node_id = failure["node_id"]

        result = await _rerun_in_process(
            node, run_id, [start_node_id], payload, single_step=False
        )
        if "error" in result:
            return result
        result["failed_node_id"] = start_node_id
        if failure:
            result["failure"] = failure
        # A1: anchor the conversation to the recovered run.
        await _remember_active_run_from_result(result)
        return result

    async def rerun_step_in_context(
        run_id: str,
        node_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Re-run ONE node within the parent run's context (ENG-2648).

        Unlike ``rerun_from_node`` -- which re-runs the chosen node AND
        everything downstream of it -- this re-runs ONLY ``node_id``.
        Every other completed node from the source run (its upstream
        dependencies AND its downstream consumers) is reused from the
        source run's cached ``node_result`` rows, so the re-run step
        executes with the full overall-run context and the rest of the
        graph is neither recomputed nor invalidated.

        The corrected output slots back into a new run whose
        ``output_nodes`` mark this node ``reran=True`` and carry a
        ``superseded_run_id`` pointer to the source run.  Read it back
        with ``get_workflow_output(run_id=<returned run_id>)``.

        Use this when a single step produced a wrong/low-quality output
        and you want to redo just that step (optionally with a
        correction in ``payload``) while keeping every other node's
        output identical to the original run.

        Args:
            run_id: The completed run's run_id (or session_id) whose
                cached results seed the new run.
            node_id: The react_flow_generated_id of the single node to
                re-run in context.
            payload: Optional overrides applied to the new run (e.g. an
                input/field correction).  Omit to re-run the node
                verbatim against the same cached upstream context.

        Returns the rerun-from-node response, including the new
        ``run_id`` to pass to ``get_workflow_output``.
        """
        result = await _rerun_in_process(
            node, run_id, [node_id], payload, single_step=True
        )
        # A1: anchor the conversation to the new in-context rerun.
        await _remember_active_run_from_result(result)
        return result

    async def submit_intervention_response(
        run_id: Optional[str] = None,
        node_id: Optional[str] = None,
        request_id: Optional[str] = None,
        response: str = "continue",
    ) -> Dict[str, Any]:
        """Reply to a ``node.input_required`` event (ENG-2650 human-in-the-loop).

        When a run reaches a human-input node it emits ``node.input_required``
        (with ``prompt``, ``required_fields``, and a ``request_id``) and waits.
        Call this to deliver the human's answer so the run continues -- the
        conversational equivalent of the A2A ``submit_intervention_response``
        skill.

        ``run_id`` / ``node_id`` / ``request_id`` are OPTIONAL: when omitted
        they're resolved from the active run for this conversation, so a bare
        "approved" works without the user repeating ids. Supply them
        explicitly when acting on a run other than the active one, or find
        them via
        ``get_workflow_events(run_id, types=["node.input_required"])``.

        Args:
            run_id: The run awaiting input (run_id or session_id). Optional.
            node_id: The waiting node's id. Optional.
            request_id: The correlation id from the event. Optional.
            response: The user's decision. A Human Intervention panel is
                continue/stop: send ``"continue"`` to proceed or ``"stop"``
                to halt. A natural-language approval ("approved", "yes", "go
                ahead") is normalised to ``"continue"`` and a clear denial
                ("no", "stop", "reject") to ``"stop"`` before delivery, so
                you can forward the user's words directly.

        Returns ``success``/``delivered``, the resolved ids, the token
        actually delivered (``response``) and the user's words
        (``original_response``). Only reports success on confirmed delivery.
        """
        from api.handlers.human_interruption.post import (
            publish_intervention_response,
        )

        if not run_id:
            run_id = await _resolve_active_run_id()
            if not run_id and getattr(node, "operator_chat", False):
                run_id, node_id, inbox_error = await _resolve_from_inbox(
                    operator_inbox.KIND_INTERVENTION, node_id
                )
                if inbox_error is not None:
                    return inbox_error
            if not run_id:
                return {
                    "error": (
                        "No active run in this conversation to approve. Start "
                        "a run first, or pass the run_id explicitly."
                    )
                }

        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            run, denied = await _load_run_for_node(session, run_id, node, "run")
        if denied is not None:
            return denied
        if run is None:
            return {"error": f"Run {run_id} not found"}

        # Resolve the pending request/node when not supplied.
        if not request_id or not node_id:
            pending = await _resolve_pending_input(run)
            node_id = node_id or pending.get("node_id")
            request_id = request_id or pending.get("request_id")
        if not request_id or not node_id:
            return {
                "error": (
                    "Could not find a pending human-input request on run "
                    f"{run.id}. It may have already been answered or the run "
                    "isn't paused. Re-check get_workflow_events."
                ),
                "run_id": str(run.id),
            }

        # Human-input nodes come in TWO families with OPPOSITE finalize
        # semantics, and the caller can't tell which the open checkpoint is:
        #
        #   * Human Intervention node (continue/stop panel): treats ONLY
        #     ``"continue"`` as proceed; any other string falls through to its
        #     STOP branch and kills the run. -> normalise approval->continue,
        #     denial->stop.
        #   * Interactive-feedback chat node (docx/xlsx/pptx review chat):
        #     treats ``"continue"`` (and any non-end string) as MORE edit
        #     feedback -> regenerates and re-opens the SAME checkpoint forever
        #     (the EY/Scale "second checkpoint loops + run never advances"
        #     defect). It only completes on a chat-end token. -> map an
        #     approval/continue/done onto the chat-end token so it finalises,
        #     and forward genuine edit instructions verbatim so it iterates.
        #
        # Resolve the family from the live ``chat_awaiting`` marker (the only
        # authoritative "this exact node is a chat node waiting right now"
        # signal); default to the intervention mapping on any uncertainty.
        is_chat_node = await _is_chat_feedback_node(run.session_id, node_id)
        if is_chat_node:
            forwarded = _chat_node_response_token(response, run.session_id, node_id)
            delivered_finalize = _is_chat_finalize_response(response)
        else:
            forwarded = _normalize_intervention_token(response)
            delivered_finalize = True
            if getattr(node, "operator_chat", False):
                await _note_answering_in_inbox(
                    run.session_id, node_id, kind=operator_inbox.KIND_INTERVENTION
                )
        stopping = not is_chat_node and forwarded == _INTERVENTION_STOP_TOKEN

        # nhóm kỹ thuật's durable resume primitive (ENG-2823, merged into
        # a2a/scale): ``publish_intervention_response`` durably stores the
        # answer AND returns an HONEST result. ``delivered`` (alias
        # ``was_awaiting``) is True only when a node was actively awaiting
        # this input -- that's the truthful ack signal. ``success`` is
        # ALWAYS True (it just means "accepted + durably stored"), so we do
        # NOT ack on it. When ``delivered`` is False there was no pending
        # request to answer, so we tell the user that rather than claiming a
        # successful approval.
        publish_kwargs: Dict[str, Any] = {}
        if stopping and getattr(node, "operator_chat", False):
            publish_kwargs["resolution"] = "stopped"
        publish_result = await publish_intervention_response(
            session_id=run.session_id,
            node_id=node_id,
            request_id=request_id,
            user_input=forwarded,
            workflow_id=str(run.workflow_id),
            **publish_kwargs,
        )
        delivered = bool(publish_result.get("delivered"))
        cancel_result = (
            await _cancel_after_stop(run.session_id) if stopping and delivered else {}
        )
        # Resolved in THIS conversation -> drop the sticky pending so the
        # next turn's grounding block doesn't re-surface an answered
        # checkpoint (paired with the sticky fallback in
        # ``resolve_conversation_run_context``). Best-effort; only on a
        # confirmed delivery so a no-op reply leaves the checkpoint intact.
        #
        # Multi-checkpoint nuance (the EY/Scale "second checkpoint" path):
        # when we forwarded EDIT FEEDBACK to a chat node it does NOT close the
        # checkpoint -- it iterates and re-opens one -- so clearing the pending
        # here would briefly de-bind a checkpoint that is genuinely still open.
        # Only clear when we delivered a FINALIZING token (intervention
        # continue/stop, or a chat-node approval/done): then the checkpoint is
        # expected to close and the run to advance. A chat-node feedback turn
        # keeps the pending bound until the next grounding turn re-resolves it.
        if run_ctx_enabled and delivered and delivered_finalize:
            try:
                from api.a2a_gateway.active_run_context import clear_pending_input

                await clear_pending_input(
                    org_id=org_id,
                    agent_id=run_ctx_agent_id,
                    context_id=run_ctx_context_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("clear_pending_input failed (non-fatal): %s", e)
        # ENG-3543: a finalized document review is over, so drop its saved
        # review-document link now instead of waiting for the TTL. This stops
        # the review-doc fallback resolver from reading a leftover link as a
        # still-open pause in the window before the node's completion event
        # lands. Only on a finalizing approval (not on forwarded edit feedback,
        # where the node re-opens the checkpoint and re-saves the link).
        if is_chat_node and delivered and delivered_finalize:
            try:
                await RedisServiceSingleton().clear_chat_review_document(run.session_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("clear_chat_review_document failed (non-fatal): %s", e)
        result_payload: Dict[str, Any] = {
            "success": delivered,  # honest: only true when a node was awaiting
            "delivered": delivered,
            "was_awaiting": bool(publish_result.get("was_awaiting", delivered)),
            "run_id": str(run.id),
            "session_id": run.session_id,
            "node_id": node_id,
            "request_id": request_id,
            # ``response`` is the token actually delivered to the node
            # (continue/stop); ``original_response`` preserves what the user
            # said so the assistant can echo it back honestly.
            "response": forwarded,
            "original_response": response or "continue",
            "node_family": "chat" if is_chat_node else "intervention",
            "message": publish_result.get("message"),
            **cancel_result,
        }
        if not delivered:
            result_payload["note"] = (
                "No node was awaiting input on this run, so there was nothing "
                "to approve. The answer was stored in case the node is "
                "mid-retry, but do NOT tell the user the run was approved or "
                "resumed -- check get_workflow_run_status / get_workflow_events "
                "to see what state it's actually in."
            )
        elif is_chat_node and not delivered_finalize:
            # We forwarded edit feedback to a review-chat node; it will iterate
            # and re-open a checkpoint rather than finish.
            result_payload["note"] = (
                "This is an interactive document-review checkpoint and your "
                "message was forwarded as EDIT FEEDBACK, so the node is "
                "regenerating the document and will re-open the review "
                "checkpoint. It did NOT finish the run. When the user is happy, "
                'reply with an approval ("continue"/"done"/"looks good") and it '
                "will finalise the document and advance the run."
            )
        elif is_chat_node and delivered_finalize:
            result_payload["note"] = (
                "Interactive document-review checkpoint finalised: the approval "
                "was delivered as the chat-end token so the node completes with "
                "the last generated document and the run advances. Verify via "
                "get_workflow_run_status before telling the user it finished."
            )
        return result_payload

    async def submit_clarification_response(
        response: str,
        run_id: Optional[str] = None,
        request_id: Optional[str] = None,
        declined: bool = False,
    ) -> Dict[str, Any]:
        """Answer an agent's clarifying question so a parked run resumes (ENG-2805).

        When an agent IN a workflow calls ``ask_user_clarification`` mid-run,
        the run parks and emits ``node.input_required`` with
        ``data.input_kind == "clarification"`` (the ``prompt`` is the
        question, ``request_id`` is the asking node id). Surface the question
        to the user, then call this with their answer to resume the run --
        the conversational equivalent of the A2A
        ``submit_clarification_response`` skill.

        ``run_id`` / ``request_id`` are OPTIONAL: when omitted they're
        resolved from the active run for this conversation, so the user's
        answer alone is enough. Supply them explicitly to target a
        different run, or find ``request_id`` via
        ``get_workflow_events(run_id, types=["node.input_required"])`` (the
        entry whose ``data.input_kind`` is ``"clarification"``).

        Args:
            response: The user's answer, folded into the asking node's prompt.
            run_id: The run awaiting clarification (run_id or session_id). Optional.
            request_id: The asking node id from the event. Optional.
            declined: Set true to explicitly skip rather than answer.

        Returns a dict with ``success``, ``run_id``, ``session_id``,
        ``request_id``, ``was_already_resolved`` and ``truncated``.
        """
        from datetime import timedelta

        from temporalio.client import WorkflowUpdateFailedError

        from temporal.client import get_temporal_client
        from temporal.config import workflow_id_for_session
        from temporal.workflows.agentic_workflow import AgenticWorkflow

        if not run_id:
            run_id = await _resolve_active_run_id()
        if not run_id:
            # No run started in this conversation: the answer may be for a
            # question relayed to another chat, which the inbox knows about.
            run_id, request_id, ambiguity = await _resolve_from_inbox(
                operator_inbox.KIND_CLARIFICATION, request_id
            )
            if ambiguity is not None:
                return ambiguity
            if not run_id:
                return {
                    "error": (
                        "No active run in this conversation, and nothing is "
                        "waiting on an answer from this user. Start a run first, "
                        "or pass the run_id explicitly."
                    )
                }

        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            run, denied = await _load_run_for_node(session, run_id, node, "run")
        if denied is not None:
            return denied
        if run is None:
            return {"error": f"Run {run_id} not found"}

        if not request_id:
            pending = await _resolve_pending_input(run)
            request_id = pending.get("request_id")
        if not request_id:
            return {
                "error": (
                    "Could not find a pending clarification request on run "
                    f"{run.id}. Re-check get_workflow_events."
                ),
                "run_id": str(run.id),
            }

        await _note_answering_in_inbox(run.session_id, str(request_id))

        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(workflow_id_for_session(run.session_id))
            try:
                update_result = await handle.execute_update(
                    AgenticWorkflow.deliver_clarification,
                    args=[str(request_id), str(response), bool(declined)],
                    rpc_timeout=timedelta(seconds=5),
                )
            except WorkflowUpdateFailedError as update_err:
                cause = update_err.cause
                return {
                    "error": getattr(cause, "message", None) or str(cause),
                    "run_id": str(run.id),
                    "request_id": request_id,
                }
        except Exception as e:  # noqa: BLE001
            return {"error": f"Failed to deliver clarification: {e}"}

        result_dict = update_result if isinstance(update_result, dict) else {}
        # Resolved in THIS conversation -> drop the sticky pending so a later
        # turn's grounding block doesn't re-surface the answered clarification
        # (paired with the sticky fallback in
        # ``resolve_conversation_run_context``). Best-effort.
        if run_ctx_enabled:
            try:
                from api.a2a_gateway.active_run_context import clear_pending_input

                await clear_pending_input(
                    org_id=org_id,
                    agent_id=run_ctx_agent_id,
                    context_id=run_ctx_context_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("clear_pending_input failed (non-fatal): %s", e)
        was_already_resolved = bool(result_dict.get("was_already_resolved", False))
        result: Dict[str, Any] = {
            "success": True,
            "run_id": str(run.id),
            "session_id": run.session_id,
            "request_id": request_id,
            "response": response,
            "was_already_resolved": was_already_resolved,
            "truncated": bool(result_dict.get("truncated", False)),
        }
        if was_already_resolved:
            result["note"] = (
                "The question had already been resolved (answered elsewhere, "
                "skipped, or timed out) before this answer arrived. Tell the "
                "user their answer came too late and what the run did instead."
            )
        return result

    async def get_workflow_definition(
        workflow_id: str,
        version_id: Optional[str] = None,
        include_payload: bool = False,
    ) -> Dict[str, Any]:
        """Fetch the workflow definition -- node graph, edges, layout,
        published flags, version metadata.

        Use this when you need the node graph: what the workflow does to
        its inputs. ``describe_workflow`` returns just the input schema.
        Neither call shows a card; ``show_workflows`` does that.

        By default it returns a COMPACT graph -- per-node ``{id, type, name}``
        plus edge/node counts -- NOT the full serialized payload. The full
        payload embeds every node's raw params (system prompts, and for
        template-heavy workflows large embedded deck templates / scoring
        frameworks), routinely tens of thousands of tokens; inlining it into
        the chat turn accumulates verbatim across turns and pushes the
        orchestrator context toward the model window. The compact graph
        answers "what does this workflow do / show me the nodes" without that
        cost. Pass ``include_payload=True`` only when the full serialized
        payload is genuinely needed (e.g. rendering the editor canvas).

        Args:
            workflow_id: Workflow slug or Capitol UUID.
            version_id: Optional UUID to pin a specific workflow version.
                Defaults to the most recent.
            include_payload: When True, also return the full serialized
                ``payload`` (can be very large). Defaults to False.

        Returns a dict with::

            {
              "id":            "<slug>",
              "capitol_workflow_id": "<uuid>",
              "name":          "<human name>",
              "description":   "<text>",
              "publish_to_api":      bool,
              "publish_to_template": bool,
              "is_agent_runtime":    bool,
              "created_at":    "<iso>",
              "updated_at":    "<iso>",
              "node_count":    int,
              "edge_count":    int,
              "version": {"version_id", "version_number", "version_type",
                          "created_at"},
              "graph": [{"node_id", "node_type", "display_name"}, ...],
              "edges": [{"source", "target"}, ...],
              # "payload" (the full serialized workflow) is included ONLY when
              # include_payload=True; otherwise a "payload_note" points here.
            }

        Raises:
            ValueError: If the workflow is not in my allowlist
                or doesn't exist in the org.
        """

        import uuid as _uuid

        from api.a2a_gateway.card import (
            resolve_effective_workflows,
            workflow_slug,
        )
        from api.a2a_gateway.event_adapter import sub_agents_from_payload
        from db.models.workflow_version import (
            WorkflowVersion as WorkflowVersionModel,
        )

        # Resolve slug -> Capitol UUID (mirrors describe_workflow).
        capitol_workflow_id: Optional[str] = None
        try:
            _uuid.UUID(str(workflow_id))
            capitol_workflow_id = str(workflow_id)
        except (TypeError, ValueError):
            from api.a2a_gateway.card import org_workflows_for_user

            factory = async_session_factory(use_fresh_engine=False)
            async with factory() as session:
                # Published pool + the user's own workflows (their slugs
                # must resolve even when unpublished).
                rows, _ = await org_workflows_for_user(
                    session, org_id=org_id, user_id=allowlist_user_id
                )
                # ENG-3444: normalize the incoming id through the slug
                # generator's own collapse so separator/case drift
                # (hyphens, spaces, capitals) still resolves.
                wanted = workflow_slug(str(workflow_id))
                match = next(
                    (wf for wf in rows if wf.name and workflow_slug(wf.name) == wanted),
                    None,
                )
                if match is None:
                    # ENG-3444: RETURN the self-healing error (never raise)
                    # so pydantic-ai hands it to the model as a tool result
                    # and the model corrects itself within the same turn.
                    # A raise here aborts the whole chat turn and the user
                    # sees "Orchestrator failed: ...".
                    return {
                        "error": _workflow_not_found_message(
                            workflow_id, await _effective_workflow_entries()
                        )
                    }
                capitol_workflow_id = str(match.id)

        # Enforce the agent's allowlist (same posture as describe_workflow).
        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            adverts = await resolve_effective_workflows(
                session,
                org_id=org_id,
                workflow_allowlist=workflow_allowlist,
                workflow_allowlist_labels=workflow_allowlist_labels,
                user_id=allowlist_user_id,
            )
            if workflow_allowlist or workflow_allowlist_labels:
                advertised_ids = {str(adv.workflow.id) for adv in adverts}
                if capitol_workflow_id not in advertised_ids:
                    return {
                        "error": (
                            f"workflow '{workflow_id}' is not in my "
                            f"allowlist; call list_workflows to see what's allowed."
                        )
                    }
            denied = await _user_workflow_access_error(
                node, session, capitol_workflow_id, "view_content"
            )
            if denied is not None:
                return denied

            wf = (
                await session.execute(
                    select(WorkflowModel).where(
                        WorkflowModel.id == _uuid.UUID(capitol_workflow_id)
                    )
                )
            ).scalar_one_or_none()
            if wf is None:
                return {"error": f"workflow {capitol_workflow_id} not found"}

            # Workflow row holds metadata; the actual graph lives on the
            # latest (or pinned) immutable WorkflowVersion's payload.
            version_stmt = (
                select(WorkflowVersionModel)
                .where(
                    WorkflowVersionModel.workflow_id == _uuid.UUID(capitol_workflow_id)
                )
                .order_by(WorkflowVersionModel.created_at.desc())
                .limit(1)
            )
            if version_id:
                try:
                    version_uuid = _uuid.UUID(str(version_id))
                except (TypeError, ValueError) as exc:
                    return {"error": f"version_id must be a UUID: {exc}"}
                version_stmt = select(WorkflowVersionModel).where(
                    WorkflowVersionModel.id == version_uuid,
                    WorkflowVersionModel.workflow_id == _uuid.UUID(capitol_workflow_id),
                )
            version = (await session.execute(version_stmt)).scalar_one_or_none()

        payload = (version.payload if version else None) or {}
        version_info: Optional[Dict[str, Any]] = None
        if version is not None:
            version_info = {
                "version_id": str(version.id),
                "version_number": getattr(version, "version_number", None),
                "version_type": (
                    version.version_type.value
                    if hasattr(version.version_type, "value")
                    else str(getattr(version, "version_type", ""))
                ),
                "created_at": version.created_at.isoformat()
                if getattr(version, "created_at", None)
                else None,
            }

        nodes_pl = payload.get("nodes", []) or []
        edges_pl = payload.get("edges", []) or []
        result: Dict[str, Any] = {
            "id": workflow_slug(wf.name) if wf.name else str(wf.id),
            "capitol_workflow_id": str(wf.id),
            "name": wf.name or "",
            "description": wf.description or "",
            "publish_to_api": getattr(wf, "publish_to_api", False),
            "publish_to_template": getattr(wf, "publish_to_template", False),
            "is_agent_runtime": getattr(wf, "is_agent_runtime", False),
            "created_at": wf.created_at.isoformat()
            if getattr(wf, "created_at", None)
            else None,
            "updated_at": wf.updated_at.isoformat()
            if getattr(wf, "updated_at", None)
            else None,
            "node_count": len(nodes_pl),
            "edge_count": len(edges_pl),
            "version": version_info,
            # Compact per-node summary instead of the full payload: node_id +
            # type + display name only, never the raw params (which embed
            # system prompts and large template/framework blobs). Reuses
            # sub_agents_from_payload so the graph nodes line up with run
            # events and there is a single source of truth for id/name
            # resolution. This is what "what does this workflow do / show me
            # the nodes" needs, without inlining tens of thousands of tokens
            # that then accumulate in the chat context across turns.
            "graph": sub_agents_from_payload(payload),
            # Compact edge list (source/target node ids only, not the opaque
            # port handles) so the graph still conveys topology / execution
            # order -- explaining "the flow" needs the connections, not just
            # the nodes.
            "edges": [
                {"source": e.get("source"), "target": e.get("target")}
                for e in edges_pl
                if isinstance(e, dict)
            ],
        }
        if include_payload:
            result["payload"] = payload
        else:
            result["payload_note"] = (
                "Compact graph only. Call get_workflow_definition("
                "include_payload=True) for the full serialized payload, or "
                "describe_workflow for the overridable input fields."
            )
        return result

    async def _describe_with_classified(
        workflow_id: str,
        version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch the input schema for a workflow so the agent knows what
        fields it can / must fill in before calling ``start_workflow``.

        This is ALSO the inspection surface for **per-run overrides**
        (ENG-2721): the returned fields are exactly the ones the user may
        override for a single run via ``start_workflow(inputs={...})``,
        keyed by ``fields[].id`` -- the ``user_prompt`` on agent nodes,
        the ``task`` prompt on chat-output nodes (PowerPoint /
        data-analysis / org-chart / D3), declared input fields, and input
        files.  Fields the author has fixed are deliberately EXCLUDED and
        cannot be overridden: ``system_prompt`` (agent identity),
        ``model`` / ``temperature``, output type/format (set by the output
        node type), and hidden / input-bound params.  Treat this returned
        set as the source of truth for "what can be changed" -- if a field
        isn't here, it isn't overridable.  Overrides are run-scoped only --
        the saved workflow definition is never modified.

        Returns the same rich descriptor the A2A ``get_workflow_details``
        skill returns -- so the in-canvas chat orchestrator has identical
        visibility to external A2A callers:

            {
              "id":          "<workflow_slug or capitol uuid>",
              "name":        "<human-readable name>",
              "description": "<short description>",
              "tags":        ["<label>", ...],
              "fields": [{
                "id":                "<node_id>.<field_id>",
                "node_instance_id":  "<uuid>",
                "node_display_name": "<author's node Name, else node type>",
                "field_id":          "<field name>",
                "display_name":      "<human-readable field name>",
                "valid_types":       ["TEXT", "FILE", ...],
                "description":       "<author's node Description, else"
                                     " node-type default, else null>",
                "current_value":     "<default value if any>",
                "required":          true | false,
                "options":           [...] | null,
                "min_value":         ... | null,
                "max_value":         ... | null,
                "input_mode":        "questionnaire" | omitted,
                "questions":         omitted on this chat path -- call
                                     export_workflow_questionnaire instead,
              }, ...],
              "input_schema": {
                "type": "object",
                "properties": { ... },
                "required":   [ ... ],
                "additionalProperties": false,
              },
            }

        Workflows that need no inputs return ``fields=[]`` and
        ``input_schema.required=[]``; you can call ``start_workflow``
        with ``inputs={}`` on those.

        Workflows with required fields: surface each ``display_name`` +
        ``valid_types`` to the user, collect their values, then call
        ``start_workflow`` with ``inputs={"<field_id>": value}``.

        Args:
            workflow_id: Workflow slug (from ``list_workflows``) OR the
                Capitol UUID.  Both work.
            version_id: Optional UUID to pin a specific workflow version.
                Defaults to the latest manually-saved version.

        Raises:
            ValueError: If the workflow is not in my allowlist
                (security: refuses to leak schemas for off-list workflows).
        """

        import uuid as _uuid

        from fastapi import HTTPException

        from api.a2a_gateway.card import resolve_effective_workflows, workflow_slug
        from api.v1.handlers.workflow import GetWorkflowInputsHandler

        # Translate slug -> Capitol UUID if a slug was supplied.
        capitol_workflow_id: Optional[str] = None
        try:
            # Canonical form, so uppercase / braced / urn:uuid: ids still
            # match the allowlist and the handler's not-found detail below.
            capitol_workflow_id = str(_uuid.UUID(str(workflow_id)))
        except (TypeError, ValueError):
            # Slug lookup -- find a workflow in this org whose slug
            # matches the NORMALIZED incoming id (ENG-3444: the same
            # collapse the generator uses, so hyphen/case/space drift
            # still resolves). The pool is published workflows plus the
            # user's own (their slugs must resolve even when unpublished).
            from api.a2a_gateway.card import org_workflows_for_user

            factory = async_session_factory(use_fresh_engine=False)
            async with factory() as session:
                rows, _ = await org_workflows_for_user(
                    session, org_id=org_id, user_id=allowlist_user_id
                )
                wanted = workflow_slug(str(workflow_id))
                match = next(
                    (wf for wf in rows if wf.name and workflow_slug(wf.name) == wanted),
                    None,
                )
                if match is None:
                    # ENG-3444: RETURN the self-healing error (never raise)
                    # so the model reads it as a tool result and corrects
                    # itself in-turn instead of the whole turn failing.
                    return {
                        "error": _workflow_not_found_message(
                            workflow_id, await _effective_workflow_entries()
                        )
                    }
                capitol_workflow_id = str(match.id)

        # Enforce allowlist -- even when caller passed a UUID, refuse to
        # leak schemas for workflows outside the agent's effective set.
        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            adverts = await resolve_effective_workflows(
                session,
                org_id=org_id,
                workflow_allowlist=workflow_allowlist,
                workflow_allowlist_labels=workflow_allowlist_labels,
                user_id=allowlist_user_id,
            )
            # When the agent has no allowlist configured at all, the
            # in-canvas chat falls back to "all published workflows in
            # this org" (matches the ``list_workflows`` behaviour above)
            # so the allowlist check is skipped in that case.
            if workflow_allowlist or workflow_allowlist_labels:
                advertised_ids = {str(adv.workflow.id) for adv in adverts}
                if capitol_workflow_id not in advertised_ids:
                    return {
                        "error": (
                            f"workflow '{workflow_id}' is not in my "
                            f"allowlist; call list_workflows to see what's allowed."
                        )
                    }
            denied = await _user_workflow_access_error(
                node, session, capitol_workflow_id, "view_content"
            )
            if denied is not None:
                return denied

            adv = next(
                (a for a in adverts if str(a.workflow.id) == capitol_workflow_id),
                None,
            )

            parsed_version_id: Optional[_uuid.UUID] = None
            if version_id:
                try:
                    parsed_version_id = _uuid.UUID(str(version_id))
                except (TypeError, ValueError) as exc:
                    return {"error": f"version_id must be a UUID: {exc}"}

            handler = GetWorkflowInputsHandler(session)
            try:
                response = await handler.handle(
                    org_id=org_id,
                    workflow_id=_uuid.UUID(capitol_workflow_id),
                    version_id=parsed_version_id,
                    classify_questionnaires=True,
                )
            except HTTPException as exc:
                # A raise here aborts the whole chat turn; returned, the
                # model reads the error and retries with a real id.
                if (
                    exc.status_code == 404
                    and exc.detail == f"Workflow {capitol_workflow_id} not found"
                ):
                    return {
                        "error": _workflow_not_found_message(
                            workflow_id, await _effective_workflow_entries()
                        ),
                        "status_code": exc.status_code,
                    }
                return {"error": str(exc.detail), "status_code": exc.status_code}

        from services.orchestrator.questionnaire import (
            is_questionnaire_field,
            public_input_field,
            questionnaire_schema_property,
        )

        def _to_json_type(capitol_type: str) -> str:
            """Map a Capitol field type onto a JSON-Schema ``type`` value."""
            t = (capitol_type or "").upper()
            if t in ("INTEGER", "INT"):
                return "integer"
            if t in ("FLOAT", "NUMBER", "DECIMAL"):
                return "number"
            if t in ("BOOL", "BOOLEAN"):
                return "boolean"
            if t in ("OBJECT", "DICT", "JSON"):
                return "object"
            if t in ("LIST", "ARRAY"):
                return "array"
            return "string"

        fields: List[Dict[str, Any]] = []
        properties: Dict[str, Any] = {}
        required_fields: List[str] = []
        for f in response.inputs:
            public = public_input_field(f, include_questions=False)
            public_id = str(public["id"])
            fields.append(public)
            if is_questionnaire_field(f):
                properties[public_id] = questionnaire_schema_property()
            else:
                json_types = sorted({_to_json_type(t) for t in f.valid_types if t})
                prop: Dict[str, Any] = {"description": f.description or f.display_name}
                if len(json_types) == 1:
                    prop["type"] = json_types[0]
                elif json_types:
                    prop["type"] = list(json_types)
                if f.options:
                    prop["enum"] = f.options
                if f.min_value is not None:
                    prop["minimum"] = f.min_value
                if f.max_value is not None:
                    prop["maximum"] = f.max_value
                if f.current_value is not None:
                    cv = f.current_value
                    if not isinstance(cv, str) or len(cv) <= _NODE_OUTPUT_PREVIEW_CHARS:
                        prop["default"] = cv
                properties[public_id] = prop
            if f.required:
                required_fields.append(public_id)

        result_id = (
            workflow_slug(adv.workflow.name)
            if (adv and adv.workflow.name)
            else capitol_workflow_id
        )
        return {
            "id": result_id,
            "capitol_workflow_id": capitol_workflow_id,
            "version_id": str(response.version_id),
            "name": (adv.workflow.name if adv else "") or "",
            "description": (adv.workflow.description if adv else "") or "",
            "tags": adv.label_names if adv else [],
            "fields": fields,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required_fields,
                "additionalProperties": False,
            },
            _CLASSIFIED_INPUTS_KEY: list(response.inputs),
        }

    async def describe_workflow(
        workflow_id: str,
        version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Public describe schema; classified rows are stripped for the model."""
        result = await _describe_with_classified(workflow_id, version_id)
        if isinstance(result, dict):
            public = dict(result)
            public.pop(_CLASSIFIED_INPUTS_KEY, None)
            return public
        return result

    describe_workflow.__doc__ = (
        (_describe_with_classified.__doc__ or "")
        + "\n\nThis call does not show the workflow to the user. It only "
        "returns the input schema for you. Call ``show_workflows`` when the "
        "reply offers that workflow and the user should see its card."
    )

    def _txt_export_agent_id() -> str:
        return str(run_ctx_agent_id or getattr(node, "node_id", None) or "unknown")

    def _txt_export_render_in_chat() -> bool:
        record = getattr(node, "agent_record", None) or {}
        return bool(record.get("render_files_in_chat", True))

    async def export_workflow_prompt(
        workflow_id: str,
        field_id: Optional[str] = None,
        version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deliver a workflow's prompt to the user as a downloadable .txt
        file -- byte-for-byte, with no length limit.

        Call this whenever the user asks to SEE, review, copy, export, or
        edit a workflow's prompt (an agent node's ``user_prompt`` or a
        chat-output node's ``task``). Chat replies are the wrong channel
        for prompt text: long prompts get truncated in transit and any
        quoting risks paraphrase. This tool writes the saved prompt text
        to a plain-text file and announces it through the platform's file
        channels, so the user reads exactly what the workflow runs.

        To CHANGE a prompt afterwards use ``update_workflow_prompt`` --
        with the user's uploaded replacement file (``file_id``) or with
        the new text they dictated in chat (``new_prompt_text``).

        Args:
            workflow_id: Workflow slug (from ``list_workflows``) OR the
                Capitol UUID. Both work.
            field_id: Which prompt to export when the workflow has more
                than one -- a ``fields[].id`` (``<node_id>.<field_id>``)
                from ``describe_workflow``, or a bare field name like
                ``user_prompt`` when unambiguous. Optional when the
                workflow has exactly one prompt field.
            version_id: Optional UUID to pin a specific workflow version.
                Defaults to the latest manually-saved version.

        Returns:
            ``{"file": {file_id, filename, ...}, "field": {...},
            "prompt_chars": int, "note": str, "error": null}`` on
            success -- follow the ``note``, refer to the file by
            filename, and NEVER paste the prompt's contents into your
            reply. On failure returns ``{"error": ...}`` explaining what
            to fix (an ambiguous ``field_id`` comes with the
            ``prompt_fields`` choices to pick from).
        """
        schema = await describe_workflow(workflow_id, version_id)
        if not isinstance(schema, dict):
            return {"error": "describe_workflow returned an unusable result."}
        if schema.get("error"):
            return schema

        return await _export_prompt_field_as_txt(
            schema=schema,
            field_id=field_id,
            org_id=org_id,
            agent_id=_txt_export_agent_id(),
            render_files_in_chat=_txt_export_render_in_chat(),
            active_run_id=await _resolve_active_run_id(),
        )

    async def export_workflow_questionnaire(
        workflow_id: str,
        field_id: Optional[str] = None,
        version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deliver a questionnaire File Input as a verbatim .txt file.

        Call this when ``describe_workflow`` marks a field
        ``input_mode: "questionnaire"``. The user's application renders
        the file; NEVER paste or summarize the questions. Then WAIT for
        compiled answers before ``start_workflow``.

        Args:
            workflow_id: Workflow slug or Capitol UUID.
            field_id: ``fields[].id`` when the workflow has more than one
                questionnaire. Optional when there is exactly one.
            version_id: Optional UUID of a specific saved version.

        Returns:
            ``{"file": {file_id, filename, category, prompt, ...},
            "note": str, "error": null}`` on success. Follow ``note``.
            On failure ``{"error": ...}`` (an ambiguous field lists
            ``questionnaire_fields``).
        """
        described = await _describe_with_classified(workflow_id, version_id)
        if not isinstance(described, dict):
            return {"error": "describe_workflow returned an unusable result."}
        if described.get("error"):
            return described
        from services.orchestrator.questionnaire import select_questionnaire_field

        field = select_questionnaire_field(
            described.pop(_CLASSIFIED_INPUTS_KEY, []),
            field_id,
            str(described.get("id") or workflow_id),
        )
        if isinstance(field, dict) and field.get("error"):
            return field
        return await _export_questionnaire_field_as_txt(
            field=field,
            schema=described,
            org_id=org_id,
            agent_id=_txt_export_agent_id(),
            render_files_in_chat=_txt_export_render_in_chat(),
            active_run_id=await _resolve_active_run_id(),
        )

    async def update_workflow_prompt(
        workflow_id: str,
        new_prompt_text: Optional[str] = None,
        file_id: Optional[str] = None,
        field_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """PERSISTENTLY change a workflow's prompt -- from the user's
        uploaded replacement file, or from new text they gave you in chat.

        Two ways the user edits a prompt (ENG-3854):

        * **They uploaded a replacement** (typically after downloading the
          current prompt via ``export_workflow_prompt`` and editing it):
          pass the upload's ``file_id``. The file's text is read and
          applied server-side, byte-for-byte -- you never see or retype
          it, so nothing gets truncated or paraphrased.
        * **They asked you to make a change in chat** ("make it more
          formal", "add a rule about citations"): fetch the current text
          with ``describe_workflow`` (``fields[].current_value``), compose
          the COMPLETE new prompt, and pass it as ``new_prompt_text``.
          Always pass the full replacement text, never a diff or just the
          changed sentence. No file or upload is required for this flow.

        The change is saved as a new manual-save version of the workflow
        (edit-permission-checked against the requesting user); every
        future run uses it automatically. For a ONE-RUN experiment do NOT
        use this tool -- pass the new text via ``start_workflow``
        ``inputs`` instead.

        Args:
            workflow_id: Workflow slug (from ``list_workflows``) OR the
                Capitol UUID. Both work.
            new_prompt_text: The complete replacement prompt text (chat
                flow). Mutually exclusive with ``file_id``.
            file_id: The uploaded replacement file's id (upload flow).
                Mutually exclusive with ``new_prompt_text``.
            field_id: Which prompt to change when the workflow has more
                than one -- a ``fields[].id`` (``<node_id>.<field_id>``)
                from ``describe_workflow``, or a bare field name like
                ``user_prompt`` when unambiguous. Optional when the
                workflow has exactly one prompt field.

        Returns:
            ``{"applied": true, "new_version_number": ..., "prompt_chars":
            ..., "note": ...}`` on success -- confirm to the user and
            follow the note. On failure ``{"error": ...}`` explaining what
            to fix (no edit permission, ambiguous field, unreadable file).
        """
        if not (getattr(node, "agent_record", None) or {}).get(
            "enable_workflow_authoring", True
        ):
            return {
                "error": (
                    "Persistent workflow edits are disabled for me "
                    "(enable_workflow_authoring is off). Offer a run-scoped "
                    "override instead: pass the new prompt via "
                    "start_workflow inputs."
                )
            }

        has_text = isinstance(new_prompt_text, str) and bool(new_prompt_text.strip())
        if has_text == bool(file_id):
            return {
                "error": (
                    "Pass exactly one of new_prompt_text (the complete "
                    "replacement text) or file_id (the user's uploaded "
                    "replacement .txt)."
                )
            }

        schema = await describe_workflow(workflow_id)
        if not isinstance(schema, dict):
            return {"error": "describe_workflow returned an unusable result."}
        if schema.get("error"):
            return schema

        if file_id:
            read = await _read_prompt_file_text(org_id, str(file_id))
            if read.get("error"):
                return read
            new_text = read["text"]
            source = "file"
        else:
            new_text = new_prompt_text
            source = "inline"

        return await _apply_prompt_update(
            schema=schema,
            field_id=field_id,
            new_text=new_text,
            org_id=org_id,
            user_id=user_id,
            source=source,
        )

    async def resolve_uploaded_file(file_id: str) -> Dict[str, Any]:
        """Resolve an uploaded file's ``file_id`` / ``artifact_id`` to its
        human-readable metadata (ENG-3153).

        Files reach you as ids in the chat text: an inline chat
        attachment arrives as an ``[Attached file: <filename>
        (file_id: <id>)]`` line, and out-of-band uploads (``upload_file``
        / ``request_upload_url``) arrive as a pasted artifact id (a bare
        UUID). That UUID means nothing to the user -- whenever you
        receive one without a filename, call this tool ONCE to learn the
        file's real ``filename``, and from then on refer to the file by
        that name in every message you send the user. Never echo the raw
        UUID back.

        Args:
            file_id: The ``file_id`` / ``artifact_id`` returned by the
                ``upload_file`` or ``request_upload_url`` skill, OR the
                ``files[].id`` of a workflow-PRODUCED output file from
                ``get_workflow_output`` (both are UUIDs).

        Returns ``{file_id, filename, mime_type, size_bytes}`` on success,
        or ``{"error": ...}`` when the id is malformed or unknown (upload
        registrations expire after 1 week, refreshed on each lookup;
        produced-file ids resolve for as long as the run's outputs exist,
        ENG-3266).
        """

        candidate = str(file_id or "").strip()
        if not _is_uuidish(candidate):
            return {
                "error": (
                    f"'{candidate}' is not a valid file_id (expected the "
                    "UUID returned by the upload_file / request_upload_url "
                    "skill)"
                )
            }

        artifact = await _resolve_artifact_for_org(org_id)(candidate)
        if not artifact:
            return {
                "error": (
                    f"No uploaded file registered under id '{candidate}', "
                    "and no workflow-produced output file matches it in "
                    "this org. Upload registrations expire after 1 week -- "
                    "ask the user to re-upload, or re-check the file id "
                    "against get_workflow_output.outputs[*].files[].id."
                )
            }

        # Deliberately exclude s3_key / s3_bucket -- internal identifiers
        # the model must never surface.
        return {
            "file_id": candidate,
            "filename": artifact.get("filename") or "",
            "mime_type": artifact.get("mime_type")
            or artifact.get("content_type")
            or "",
            "size_bytes": artifact.get("size_bytes"),
            "error": None,
        }

    async def read_uploaded_file(
        file_id: str, offset: int = 0, max_chars: int = 20_000
    ) -> Dict[str, Any]:
        """Read the CONTENT of an uploaded (or workflow-produced) text file.

        ``resolve_uploaded_file`` gives you a file's name/size only; THIS
        tool gives you what's inside it. Use it whenever the user attaches
        a text-based file (.json, .txt, .csv, .md, .yaml, code, a workflow
        export, ...) and asks you to read, summarize, inspect, validate,
        or act on its content -- e.g. "what's in this file?", "import this
        workflow export", "check this config".

        Text only: the file must decode as UTF-8. Binary formats (.xlsx,
        .pdf, .docx, images, ...) are refused with an error -- pass those
        to a workflow as a FILE input instead.

        Large files are read in WINDOWS: the response carries at most
        ``max_chars`` characters starting at character ``offset``, plus
        ``total_chars`` / ``truncated`` / ``next_offset`` so you can page
        through with repeated calls. For big structured files (a multi-MB
        JSON export), read the first window -- it usually carries the
        metadata you need -- and page further ONLY for the parts that
        matter; never try to exhaustively page a huge file into the chat.

        Args:
            file_id: The upload's ``file_id`` / ``artifact_id`` (UUID) --
                from an ``[Attached file: ...]`` line, an upload skill
                result, or ``get_workflow_output`` ``files[].id``.
            offset: Character position to start reading from (0-based;
                use the previous call's ``next_offset`` to continue).
            max_chars: Window size in characters (1 to 60,000).

        Returns ``{file_id, filename, mime_type, total_chars, offset,
        text, truncated, next_offset}`` on success, ``{"error": ...}``
        otherwise.
        """
        candidate = str(file_id or "").strip()
        if not _is_uuidish(candidate):
            return {
                "error": (
                    f"'{candidate}' is not a valid file_id (expected the "
                    "UUID from the [Attached file: ...] line or the "
                    "upload skill result)"
                )
            }
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        try:
            max_chars = min(max(1, int(max_chars)), _READ_FILE_MAX_CHARS)
        except (TypeError, ValueError):
            max_chars = 20_000

        artifact = await _resolve_artifact_for_org(org_id)(candidate)
        if not isinstance(artifact, dict):
            return {
                "error": (
                    f"No uploaded file registered under id '{candidate}', "
                    "and no workflow-produced output file matches it in "
                    "this org. Upload registrations expire after 1 week -- "
                    "ask the user to re-upload."
                )
            }
        filename = artifact.get("filename") or candidate
        s3_key = artifact.get("s3_key")
        if not s3_key:
            return {
                "error": (
                    f"File '{filename}' has no readable storage record -- "
                    "ask the user to re-upload it."
                )
            }
        size_hint = artifact.get("size_bytes")
        if isinstance(size_hint, int) and size_hint > _READ_FILE_MAX_BYTES:
            return {
                "error": (
                    f"File '{filename}' is {size_hint} bytes -- beyond the "
                    f"{_READ_FILE_MAX_BYTES}-byte chat read cap. Pass it "
                    "to a workflow as a FILE input instead of reading it "
                    "into the conversation."
                )
            }

        content = await _download_upload_bytes(
            s3_key, artifact.get("s3_bucket"), max_bytes=_READ_FILE_MAX_BYTES
        )
        if isinstance(content, dict):  # error envelope
            return content
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return {
                "error": (
                    f"File '{filename}' is not UTF-8 text (a binary format "
                    "like xlsx/pdf/docx/image). It can be passed to a "
                    "workflow as a FILE input, but its raw content cannot "
                    "be read into the chat."
                )
            }

        total = len(text)
        window = text[offset : offset + max_chars]
        truncated = offset + len(window) < total
        return {
            "file_id": candidate,
            "filename": artifact.get("filename") or "",
            "mime_type": artifact.get("mime_type")
            or artifact.get("content_type")
            or "",
            "total_chars": total,
            "offset": offset,
            "text": window,
            "truncated": truncated,
            "next_offset": (offset + len(window)) if truncated else None,
            "error": None,
        }

    async def submit_input_decision(proceed: bool) -> Dict[str, Any]:
        """Record the user's answer to a reported input issue (ENG-4104).

        Call this after ``report_input_issue`` once the user has said whether to
        proceed. ``start_workflow`` is refused until you do.

        IMPORTANT: the ``proceed`` flag you pass is NOT what decides this. The
        server reads consent from the user's own words on this turn, because a
        misrelayed answer would authorise a run the user had just refused. An
        unambiguous yes approves; a no, a mixed answer, an unrelated reply or no
        reply all fail to approve, and you must ask again with a direct yes/no
        question. Pass ``proceed`` as your reading of their answer; a
        disagreement with the server's reading is logged, not obeyed.

        Args:
            proceed: your reading of the user's answer.

        Returns:
            ``{status, decision_id, consent}`` once recorded, or an error dict
            when there was nothing to resolve or consent was not clear.
        """
        if not run_ctx_enabled:
            return {
                "error": "no_conversation_context",
                "message": (
                    "Input decisions exist only on the chat path; there is no "
                    "conversation to resolve here."
                ),
            }

        from api.a2a_gateway.input_decision_context import (
            STATUS_PENDING,
            UNVERIFIABLE,
            classify_consent,
            get_decision,
            resolve_decision,
        )

        record, state = await get_decision(
            org_id=org_id,
            agent_id=run_ctx_agent_id,
            context_id=run_ctx_context_id,
        )
        if state == UNVERIFIABLE:
            return {
                "error": "input_decision_unverifiable",
                "message": (
                    "Could not read the open decision, so the answer was not "
                    "recorded. Tell the user and ask them to repeat it."
                ),
            }
        if not record:
            return {
                "error": "no_pending_input_decision",
                "message": (
                    "There is no open input decision in this conversation, so "
                    "there is nothing to answer."
                ),
            }
        if str(record.get("status")) != STATUS_PENDING:
            return {
                "status": record.get("status"),
                "decision_id": record.get("decision_id"),
                "message": "This decision was already answered.",
            }

        # The REAL user turn, set by the orchestrator before tools run. Never
        # the model-supplied string: that is the link this exists to remove.
        actual = getattr(node, "current_user_message", None)
        if not actual or not str(actual).strip():
            return {
                "error": "consent_unverifiable",
                "message": (
                    "The user's message for this turn is not available, so "
                    "consent cannot be confirmed and nothing was recorded. Ask "
                    "the user again for a direct yes or no."
                ),
            }

        # The decision was opened DURING this same turn: the only user text
        # available is the message that raised the finding, which pre-dates
        # the ask. "Yes, run it" in the original request is not an answer to
        # a question the user has not seen -- consent must come from a later
        # turn.
        opened_msg = str(record.get("opened_turn_message") or "").strip()
        if opened_msg and opened_msg == str(actual).strip():
            return {
                "error": "consent_not_yet_given",
                "refused": True,
                "decision_id": record.get("decision_id"),
                "message": (
                    "The input issue was reported in THIS turn, so the user "
                    "has not seen the question yet -- their current message "
                    "cannot be their answer to it. Nothing was recorded and "
                    "the run stays blocked. State the finding in the main "
                    "chat, ask 'shall I proceed with this file, yes or no?', "
                    "and call this tool again after they reply."
                ),
            }

        consent = classify_consent(str(actual))
        if consent is None:
            return {
                "error": "consent_unclear",
                "refused": True,
                "decision_id": record.get("decision_id"),
                "message": (
                    "The user's reply was not a clear yes or no, so nothing "
                    "was recorded and the run is still blocked. Ask one direct "
                    "question: 'shall I proceed with this file, yes or no?'"
                ),
            }

        if bool(proceed) is not consent:
            logger.warning(
                "submit_input_decision: model read %s, user text reads %s "
                "(context=%s) -- honouring the user text",
                bool(proceed),
                consent,
                run_ctx_context_id,
            )

        updated = await resolve_decision(
            org_id=org_id,
            agent_id=run_ctx_agent_id,
            context_id=run_ctx_context_id,
            approved=consent,
            answered_text=str(actual),
        )
        if updated is None:
            return {
                "error": "input_decision_not_recorded",
                "message": (
                    "The answer could not be saved, so the run stays blocked. "
                    "Tell the user and ask them to repeat it."
                ),
            }

        return {
            "status": updated.get("status"),
            "decision_id": updated.get("decision_id"),
            "consent": consent,
            "message": (
                "Approved. You may now call start_workflow once."
                if consent
                else "Declined. Do not start the run."
            ),
        }

    return [
        list_workflows,
        describe_workflow,
        get_workflow_definition,
        start_workflow,
        get_workflow_run_status,
        get_workflow_output,
        get_node_output,
        get_eval_report,
        get_workflow_events,
        get_workflow_chain_of_thought,
        stop_workflow,
        pause_workflow,
        resume_workflow,
        rerun_from_node,
        recover_failed_run,
        rerun_step_in_context,
        submit_intervention_response,
        submit_clarification_response,
        submit_input_decision,
        resolve_uploaded_file,
        read_uploaded_file,
        export_workflow_prompt,
        export_workflow_questionnaire,
        update_workflow_prompt,
    ]


# ===========================================================================
# Helpers
# ===========================================================================


# ENG-3854: the prompt fields exportable as verbatim .txt files -- exactly
# the overridable prompt set ``describe_workflow`` exposes (an agent node's
# ``user_prompt``, a chat-output node's ``task``). ``system_prompt`` is
# locked out of the describe schema by design and therefore never exported.
PROMPT_EXPORT_FIELD_IDS = ("user_prompt", "task")

# ``category`` stamped on exported prompt files. Additive wire field: it
# rides the artifact chat frames and ``files_available`` events so consumers
# can distinguish a prompt export from a generated run deliverable.
PROMPT_FILE_CATEGORY = "prompt"

_PROMPT_FILENAME_PART_MAX = 60


def _prompt_export_filename(workflow_part: str, node_part: str, field_part: str) -> str:
    """``<workflow>__<node>__<field>.txt`` from already-meaningful parts,
    each slugified and length-capped so registry keys and download headers
    stay sane for pathological workflow/node names."""
    from api.a2a_gateway.card import workflow_slug

    def _part(raw: str, fallback: str) -> str:
        slug = workflow_slug(str(raw or ""))[:_PROMPT_FILENAME_PART_MAX]
        return slug or fallback

    return (
        f"{_part(workflow_part, 'workflow')}__"
        f"{_part(node_part, 'node')}__"
        f"{_part(field_part, 'prompt')}.txt"
    )


def _select_prompt_field(
    schema: Dict[str, Any], field_id: Optional[str]
) -> Dict[str, Any]:
    """Pick the prompt field to export from a ``describe_workflow`` result.

    Returns the field dict, or ``{"error": ..., "prompt_fields": [...]}``
    (self-healing, ENG-3444 style: the model reads the choices and
    corrects itself in-turn).
    """
    prompt_fields = [
        f
        for f in (schema.get("fields") or [])
        if isinstance(f, dict) and f.get("field_id") in PROMPT_EXPORT_FIELD_IDS
    ]
    choices = [
        {
            "id": f.get("id"),
            "field_id": f.get("field_id"),
            "node_display_name": f.get("node_display_name"),
            "display_name": f.get("display_name"),
        }
        for f in prompt_fields
    ]
    if not prompt_fields:
        return {
            "error": (
                f"Workflow '{schema.get('id')}' has no prompt fields in its "
                "overridable inputs (no agent user_prompt or chat-output "
                "task) -- there is no prompt to export. Use "
                "get_workflow_definition if the user needs other parts of "
                "the definition."
            )
        }
    wanted = str(field_id).strip() if field_id else ""
    if wanted:
        matches = [
            f
            for f in prompt_fields
            if f.get("id") == wanted or f.get("field_id") == wanted
        ]
        if not matches:
            return {
                "error": (
                    f"No prompt field '{wanted}' on workflow "
                    f"'{schema.get('id')}'. Pick one of the prompt_fields "
                    "listed (pass its `id`)."
                ),
                "prompt_fields": choices,
            }
        if len(matches) > 1:
            return {
                "error": (
                    f"field_id '{wanted}' matches {len(matches)} prompt "
                    "fields -- pass the fully-qualified `id` "
                    "(`<node_id>.<field_id>`) of the one the user means."
                ),
                "prompt_fields": choices,
            }
        return matches[0]
    if len(prompt_fields) > 1:
        return {
            "error": (
                f"Workflow '{schema.get('id')}' has {len(prompt_fields)} "
                "prompt fields -- pass field_id with the `id` of the one "
                "the user means (ask them if unclear)."
            ),
            "prompt_fields": choices,
        }
    return prompt_fields[0]


# Replacement prompt files are plain text; anything bigger than this is
# not a prompt (the biggest saved prompts are tens of KB).
_PROMPT_FILE_MAX_BYTES = 1_000_000

# ``read_uploaded_file`` guards: text uploads bigger than 25 MB don't
# belong in a chat turn at all (pass them to a workflow instead), and one
# window never exceeds 60k characters so a single tool result can't blow
# the turn's context.
_READ_FILE_MAX_BYTES = 26_214_400
_READ_FILE_MAX_CHARS = 60_000

# Paging a multi-MB upload means repeated windows over the same object;
# keep the last few downloads hot so page 2..N skip the S3 round-trip.
_READ_FILE_CACHE_TTL_SECONDS = 120.0
_READ_FILE_CACHE_MAX_ENTRIES = 4
_read_file_cache: Dict[str, tuple] = {}


async def _download_upload_bytes(
    s3_key: str, s3_bucket: Optional[str], *, max_bytes: int
):
    """Fetch an upload's bytes via ``S3Service`` with a tiny TTL cache.

    Returns ``bytes`` on success or an ``{"error": ...}`` dict the tool
    can return as-is (tools must never raise).
    """
    import time as _time

    cache_key = f"{s3_bucket or ''}/{s3_key}"
    now = _time.monotonic()
    hit = _read_file_cache.get(cache_key)
    if hit is not None and now - hit[0] < _READ_FILE_CACHE_TTL_SECONDS:
        return hit[1]
    try:
        from services.aws.s3 import S3Service

        # Thread offload: a synchronous S3 read on the event loop stalls
        # every concurrent chat turn on the process.
        content = await asyncio.to_thread(
            S3Service().download_file_content, s3_key, s3_bucket
        )
    except Exception as exc:  # noqa: BLE001 - tool must never raise
        logger.warning("read_uploaded_file: download failed for %s: %s", s3_key, exc)
        return {"error": f"The file could not be read from storage: {exc}. Try again."}
    if len(content) > max_bytes:
        return {
            "error": (
                f"File is {len(content)} bytes -- beyond the {max_bytes}-byte "
                "chat read cap. Pass it to a workflow as a FILE input instead."
            )
        }
    if len(_read_file_cache) >= _READ_FILE_CACHE_MAX_ENTRIES:
        oldest = min(_read_file_cache, key=lambda k: _read_file_cache[k][0])
        _read_file_cache.pop(oldest, None)
    _read_file_cache[cache_key] = (now, content)
    return content


async def _read_prompt_file_text(org_id: str, file_id: str) -> Dict[str, Any]:
    """Read an uploaded replacement-prompt file's text server-side.

    ENG-3854 round-trip: the replacement's bytes go straight from S3 into
    the saved workflow without ever transiting the model, so the applied
    prompt is byte-for-byte what the user uploaded. Returns ``{"text",
    "filename"}`` or ``{"error": ...}``.
    """
    candidate = str(file_id or "").strip()
    artifact = await _resolve_artifact_for_org(org_id)(candidate)
    if not isinstance(artifact, dict):
        return {
            "error": (
                f"No file with id '{candidate}' is visible to this "
                "conversation (unknown id, expired upload, or another "
                "org's file). Ask the user to re-upload the replacement "
                "prompt as a .txt attachment."
            )
        }
    s3_key = artifact.get("s3_key")
    if not s3_key:
        return {
            "error": (
                f"File '{artifact.get('filename') or candidate}' has no "
                "readable storage record. Ask the user to re-upload the "
                "replacement prompt as a plain-text .txt attachment."
            )
        }
    size_hint = artifact.get("size_bytes")
    if isinstance(size_hint, int) and size_hint > _PROMPT_FILE_MAX_BYTES:
        return {
            "error": (
                f"File '{artifact.get('filename') or candidate}' is "
                f"{size_hint} bytes -- too large for a prompt (cap "
                f"{_PROMPT_FILE_MAX_BYTES} bytes). Ask the user for a "
                "plain-text file containing just the prompt."
            )
        }
    try:
        from services.aws.s3 import S3Service

        # Thread offload: a synchronous S3 read on the event loop stalls
        # every concurrent chat turn on the process.
        content = await asyncio.to_thread(
            S3Service().download_file_content, s3_key, artifact.get("s3_bucket")
        )
    except Exception as exc:  # noqa: BLE001 - tool must never raise
        logger.warning(
            "_read_prompt_file_text: download failed for %s: %s", candidate, exc
        )
        return {
            "error": (f"The file could not be read from storage: {exc}. Try again.")
        }
    if len(content) > _PROMPT_FILE_MAX_BYTES:
        return {
            "error": (
                f"File is {len(content)} bytes -- too large for a prompt "
                f"(cap {_PROMPT_FILE_MAX_BYTES} bytes). Ask the user for a "
                "plain-text file containing just the prompt."
            )
        }
    try:
        # utf-8-sig: tolerate the BOM Windows editors prepend to .txt.
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {
            "error": (
                f"File '{artifact.get('filename') or candidate}' is not "
                "plain text (UTF-8). Ask the user to save the prompt as a "
                ".txt file and re-upload."
            )
        }
    if not text.strip():
        return {
            "error": (
                f"File '{artifact.get('filename') or candidate}' is empty "
                "-- there is no prompt text to apply."
            )
        }
    return {"text": text, "filename": artifact.get("filename")}


async def _apply_prompt_update(
    *,
    schema: Dict[str, Any],
    field_id: Optional[str],
    new_text: str,
    org_id: str,
    user_id: Optional[str],
    source: str,
) -> Dict[str, Any]:
    """Persist a new value for one prompt field as a MANUAL_SAVE version.

    Mirrors the run-override param addressing
    (``TriggerWorkflowRunHandler._apply_overrides``:
    ``nodes[].data.struct.params[].value`` keyed by node instance id +
    ``field_id``) but writes a durable MANUAL_SAVE version -- the one
    ``describe_workflow`` / ``start_workflow`` pick up next -- instead of
    a run-scoped RUN version. Edit-permission-checked against the
    requesting chat user.
    """
    field = _select_prompt_field(schema, field_id)
    if field.get("error"):
        return field

    if not user_id:
        return {
            "error": (
                "Persistent prompt edits need a user identity to record "
                "and authorize the change, and this session has none. "
                "Offer a run-scoped override instead: pass the new prompt "
                "via start_workflow inputs."
            )
        }

    try:
        wf_uuid = uuid.UUID(str(schema.get("capitol_workflow_id")))
    except (TypeError, ValueError):
        return {"error": "Workflow id could not be resolved -- try again."}

    node_instance_id = str(field.get("node_instance_id") or "")

    try:
        from db.models.enums import VersionType
        from services.workflow_access.workflow_access import WorkflowAccess
        from services.workflows.workflow import Workflow as WorkflowService

        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            svc = WorkflowService(session)
            wf = await svc.get_by_id(wf_uuid)
            if wf is None or str(wf.org_id) != str(org_id):
                return {
                    "error": (
                        f"Workflow '{schema.get('id')}' was not found in "
                        "this organization."
                    )
                }
            allowed = await WorkflowAccess(session).check_permission(
                wf_uuid, user_id, "edit"
            )
            if not allowed:
                return {
                    "error": (
                        "The user does not have edit permission on workflow "
                        f"'{schema.get('id')}', so the saved prompt cannot "
                        "be changed. Offer a run-scoped override instead "
                        "(pass the new prompt via start_workflow inputs)."
                    )
                }

            version = await svc.get_latest_manual_save_version(
                wf_uuid
            ) or await svc.get_latest_version(wf_uuid)
            if version is None or not (version.payload or {}).get("nodes"):
                return {
                    "error": (
                        f"Workflow '{schema.get('id')}' has no saved "
                        "definition to edit."
                    )
                }

            payload = copy.deepcopy(version.payload)
            target_node = next(
                (
                    n
                    for n in payload.get("nodes", [])
                    if isinstance(n, dict)
                    and (
                        str(n.get("id")) == node_instance_id
                        or str(
                            (n.get("data") or {})
                            .get("struct", {})
                            .get("react_flow_generated_id")
                        )
                        == node_instance_id
                    )
                ),
                None,
            )
            if target_node is None:
                return {
                    "error": (
                        f"Node '{node_instance_id}' was not found in the "
                        "saved workflow definition -- the workflow may have "
                        "changed. Re-run describe_workflow and retry."
                    )
                }
            params = (
                (target_node.get("data") or {}).get("struct", {}).get("params")
            ) or []
            param = next(
                (
                    p
                    for p in params
                    if isinstance(p, dict)
                    and p.get("field_id") == field.get("field_id")
                ),
                None,
            )
            if param is None:
                return {
                    "error": (
                        f"Field '{field.get('field_id')}' was not found on "
                        f"node '{node_instance_id}' in the saved definition "
                        "-- re-run describe_workflow and retry."
                    )
                }

            previous = param.get("value")
            param["value"] = new_text
            new_version = await svc.save_version(
                wf_uuid, user_id, payload, VersionType.MANUAL_SAVE
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - tool must never raise
        logger.warning(
            "update_workflow_prompt: persist failed for %s: %s",
            schema.get("id"),
            exc,
        )
        return {"error": (f"The prompt change could not be saved: {exc}. Try again.")}

    return {
        "applied": True,
        "workflow_id": schema.get("id"),
        "capitol_workflow_id": schema.get("capitol_workflow_id"),
        "field": {
            "id": field.get("id"),
            "field_id": field.get("field_id"),
            "node_display_name": field.get("node_display_name"),
            "display_name": field.get("display_name"),
        },
        "new_version_id": str(new_version.id) if new_version else None,
        "new_version_number": (new_version.version_number if new_version else None),
        "prompt_chars": len(new_text),
        "previous_chars": len(previous) if isinstance(previous, str) else None,
        "source": source,
        "note": (
            "The saved workflow now uses the new prompt (every future run "
            "picks it up automatically). Confirm the change to the user; "
            "offer export_workflow_prompt if they want to verify the saved "
            "text. NEVER paste the prompt's contents into your reply."
        ),
        "error": None,
    }


_TXT_EXPORT_CONTENT_TYPE = "text/plain; charset=utf-8"


def _never_paste_txt(noun: str, *, may_summarize: bool = True) -> str:
    """Forbid pasting exported text; the file is the source of truth.

    Prompt exports may be summarized. Questionnaire exports must not be
    recapped: a tool-result note outranks the system prompt, so inviting a
    summary here would put the questions back in chat.
    """
    if may_summarize:
        return (
            f" NEVER paste the {noun}'s contents into your reply -- the file is "
            "the verbatim source of truth; summarize at most."
        )
    return (
        f" NEVER paste, recap, paraphrase, or list the {noun}'s contents -- "
        "the file is the verbatim source of truth. Wait for the user's answers."
    )


async def _persist_verbatim_txt(
    *,
    blob: bytes,
    filename: str,
    category: str,
    source: str,
    description: str,
    schema: Dict[str, Any],
    org_id: str,
    agent_id: str,
    noun: str,
) -> Dict[str, Any]:
    """Store a verbatim .txt artifact; refuse an unregistered file_id."""
    from services.a2a.artifact_store import persist_artifact_blob

    try:
        stored = await persist_artifact_blob(
            org_id=org_id,
            agent_id=agent_id,
            blob=blob,
            filename=filename,
            content_type=_TXT_EXPORT_CONTENT_TYPE,
            source=source,
            workflow_id=(
                str(schema.get("capitol_workflow_id"))
                if schema.get("capitol_workflow_id")
                else None
            ),
            description=description,
            label=category,
            generated=True,
        )
    except Exception as exc:  # noqa: BLE001 - tool must never raise
        logger.warning("%s: persistence failed: %s", source, exc)
        return {"error": f"The {noun} could not be stored as a file: {exc}. Try again."}
    if not stored.get("registered"):
        return {
            "error": (
                f"The {noun} file was stored, but its file_id could not be "
                "registered for this conversation (the artifact registry "
                "is unavailable). Try again."
            )
        }
    return stored


def _verbatim_txt_entry(
    stored: Dict[str, Any], filename: str, size_bytes: int, category: str
) -> Dict[str, Any]:
    """Build the file dict stamped for Scale verbatim rendering."""
    return {
        "file_id": stored.get("file_id"),
        "filename": filename,
        "content_type": _TXT_EXPORT_CONTENT_TYPE,
        "size_bytes": size_bytes,
        "download_url": stored.get("download_url"),
        "category": category,
        # A file the user explicitly asked to see is a deliverable --
        # keep it visible to consumers that apply the primary filter.
        "primary": True,
        # Scale display contract: render it, and render it VERBATIM --
        # ``prompt: true`` tells the consumer to show the text as-is,
        # bypassing any LLM.
        "prompt": True,
        "display": True,
    }


async def _announce_verbatim_txt(
    source: str, active_run_id: Optional[str], entry: Dict[str, Any]
) -> Optional["FilesAvailableDelivery"]:
    """Mirror the export onto the active run's files_available channel.

    Returns the emitter's delivery outcome, or None when there was no run to
    mirror onto. ``event_emitted`` is ``bool(delivery)`` -- True only when a
    live consumer received the event. The run anchor outlives the run, so a
    published event can still land on a closed stream.
    """
    if not active_run_id:
        return None
    from services.node_results.files_available import (
        SCOPE_FILES,
        FilesAvailableDelivery,
        emit_user_requested_files_available,
    )

    try:
        return await emit_user_requested_files_available(
            run_id=str(active_run_id),
            scope=SCOPE_FILES,
            files=[entry],
        )
    except Exception as exc:  # noqa: BLE001 - chat delivery still valid
        logger.warning(
            "%s: files_available emit failed for run %s: %s",
            source,
            active_run_id,
            exc,
        )
        return FilesAvailableDelivery.DELIVERY_FAULT


def _verbatim_txt_result_file(
    entry: Dict[str, Any], render_files_in_chat: bool
) -> Dict[str, Any]:
    """Copy the file dict; drop download_url for events-only agents."""
    result_file = dict(entry)
    if not render_files_in_chat:
        result_file.pop("download_url", None)
    return result_file


def _verbatim_txt_note(
    *,
    noun: str,
    filename: str,
    render_files_in_chat: bool,
    delivery: Optional["FilesAvailableDelivery"],
    may_summarize: bool = True,
) -> str:
    """Tool-result note for one render posture. Does not mutate the file dict.

    The undelivered wording is shared with ``show_output`` via
    :func:`nothing_rendered_note`: a model obeys a tool result over the system
    prompt, so two copies that disagree are a behavioural bug.
    """
    never_paste = _never_paste_txt(noun, may_summarize=may_summarize)
    if render_files_in_chat:
        return (
            "The user's UI renders a download card for this file. Tell the "
            f"user the {noun} is ready to download as {filename}."
        ) + never_paste
    if delivery:
        return (
            "The user's application has been signalled through the "
            "file-event channel and renders the file itself. Refer to "
            f"it as {filename} in prose; NEVER paste links or URLs."
        ) + never_paste
    return (
        nothing_rendered_note(
            cause=CAUSE_NO_RUN if delivery is None else delivery.value
        )
        + never_paste
    )


async def _export_verbatim_txt(
    *,
    blob: bytes,
    filename: str,
    category: str,
    source: str,
    noun: str,
    description: str,
    schema: Dict[str, Any],
    field: Dict[str, Any],
    org_id: str,
    agent_id: str,
    render_files_in_chat: bool,
    active_run_id: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
    may_summarize: bool = True,
) -> Dict[str, Any]:
    """Persist one verbatim .txt and announce it on the file channels."""
    stored = await _persist_verbatim_txt(
        blob=blob,
        filename=filename,
        category=category,
        source=source,
        description=description,
        schema=schema,
        org_id=org_id,
        agent_id=agent_id,
        noun=noun,
    )
    if stored.get("error"):
        return stored
    entry = _verbatim_txt_entry(stored, filename, len(blob), category)
    delivery = await _announce_verbatim_txt(source, active_run_id, entry)
    result: Dict[str, Any] = {
        "file": _verbatim_txt_result_file(entry, render_files_in_chat),
        "workflow_id": schema.get("id"),
        "capitol_workflow_id": schema.get("capitol_workflow_id"),
        "version_id": schema.get("version_id"),
        "field": field,
        "event_emitted": bool(delivery),
        "note": _verbatim_txt_note(
            noun=noun,
            filename=filename,
            render_files_in_chat=render_files_in_chat,
            delivery=delivery,
            may_summarize=may_summarize,
        ),
        "error": None,
    }
    result.update(extra or {})
    return result


def _classified_field_as_export_field(field: Any) -> Dict[str, Any]:
    """Public field identity for a classified InputFieldResponse."""
    return {
        "id": f"{field.node_instance_id}.{field.field_id}",
        "field_id": field.field_id,
        "node_display_name": getattr(field, "node_display_name", None),
        "display_name": getattr(field, "display_name", None),
    }


async def _export_prompt_field_as_txt(
    *,
    schema: Dict[str, Any],
    field_id: Optional[str],
    org_id: str,
    agent_id: str,
    render_files_in_chat: bool,
    active_run_id: Optional[str],
) -> Dict[str, Any]:
    """Persist one prompt field of a described workflow as a .txt artifact.

    Split out of ``export_workflow_prompt`` so the selection contract is
    unit-testable against a fabricated ``describe_workflow`` schema.
    """
    field = _select_prompt_field(schema, field_id)
    if field.get("error"):
        return field
    text = field.get("current_value")
    if not isinstance(text, str) or not text.strip():
        return {
            "error": (
                f"Prompt field '{field.get('id')}' has no saved text (its "
                "current value is empty) -- there is nothing to export. "
                "The workflow author must save a prompt first."
            )
        }
    return await _export_verbatim_txt(
        blob=text.encode("utf-8"),
        filename=_prompt_export_filename(
            str(schema.get("id") or ""),
            str(field.get("node_display_name") or ""),
            str(field.get("field_id") or ""),
        ),
        category=PROMPT_FILE_CATEGORY,
        source="export_workflow_prompt",
        noun="prompt",
        description=(
            f"Verbatim {field.get('field_id')} of workflow "
            f"'{schema.get('name') or schema.get('id')}' "
            f"(version {schema.get('version_id')})"
        ),
        schema=schema,
        field={
            "id": field.get("id"),
            "field_id": field.get("field_id"),
            "node_display_name": field.get("node_display_name"),
            "display_name": field.get("display_name"),
        },
        org_id=org_id,
        agent_id=agent_id,
        render_files_in_chat=render_files_in_chat,
        active_run_id=active_run_id,
        extra={"prompt_chars": len(text)},
    )


async def _export_questionnaire_field_as_txt(
    *,
    field: Any,
    schema: Dict[str, Any],
    org_id: str,
    agent_id: str,
    render_files_in_chat: bool,
    active_run_id: Optional[str],
) -> Dict[str, Any]:
    """Persist the authored questions file via the shared txt-export path."""
    from services.orchestrator.questionnaire import (
        QUESTIONNAIRE_FILE_CATEGORY,
        QuestionnaireInputError,
        read_template_bytes,
    )

    try:
        blob = await read_template_bytes(field)
    except QuestionnaireInputError as exc:
        return {"error": str(exc)}
    return await _export_verbatim_txt(
        blob=blob,
        filename=_prompt_export_filename(
            str(schema.get("id") or ""),
            str(getattr(field, "node_display_name", "") or ""),
            "questionnaire",
        ),
        category=QUESTIONNAIRE_FILE_CATEGORY,
        source="export_workflow_questionnaire",
        noun="questionnaire",
        description=(
            f"Questionnaire for workflow '{schema.get('name') or schema.get('id')}'"
        ),
        schema=schema,
        field=_classified_field_as_export_field(field),
        org_id=org_id,
        agent_id=agent_id,
        render_files_in_chat=render_files_in_chat,
        active_run_id=active_run_id,
        extra={"size_bytes": len(blob)},
        may_summarize=False,
    )


def _normalize_intervention_token(response: Optional[str]) -> str:
    """Map a natural-language approval/denial onto the continue/stop token
    the Human Intervention node's panel accepts.

    The node (``nodes/logic/human_intervention/node.py``) branches on
    ``(user_decision or "continue").lower() == "continue"``: ONLY the literal
    ``"continue"`` proceeds; every other string -- including a forwarded
    "approved" -- raises ``asyncio.CancelledError`` and STOPS the run. So an
    approval passed through verbatim would silently kill the workflow
    (post-#921 it's durably consumed, which makes the wrong-token stop even
    more likely to land). Normalise here so the user's intent survives:

      * a clear denial ("stop"/"cancel"/"no"/"reject"/...) -> ``"stop"``
      * anything else (incl. "approved"/"yes"/"go ahead"/"") -> ``"continue"``
    """
    if not response:
        return _INTERVENTION_CONTINUE_TOKEN
    text = response.strip().lower()
    if text == _INTERVENTION_STOP_TOKEN:
        return _INTERVENTION_STOP_TOKEN
    if text == _INTERVENTION_CONTINUE_TOKEN:
        return _INTERVENTION_CONTINUE_TOKEN
    tokens = text.split()
    # Strip surrounding punctuation so "no," / "stop." / "reject!" still read
    # as denials.
    first = tokens[0].strip(".,!;:?'\"") if tokens else text
    if text in _INTERVENTION_DENY_WORDS or first in _INTERVENTION_DENY_WORDS:
        return _INTERVENTION_STOP_TOKEN
    return _INTERVENTION_CONTINUE_TOKEN


def _deterministic_intervention_request_id(session_id: str, node_id: str) -> str:
    """Recompute the Human Intervention node's stable ``request_id``.

    The node mints ``request_id = uuid5(NAMESPACE_URL,
    "intervention:{session}:{node}")`` (ENG-2823 -- stable across the
    INTERACTIVE activity's retries). Reproducing it here lets us hand
    ``submit_intervention_response`` the exact id the waiting node is keyed
    on, given only the awaiting node id from the Redis marker.
    """
    from uuid import NAMESPACE_URL, uuid5

    return str(uuid5(NAMESPACE_URL, f"intervention:{session_id}:{node_id}"))


# Legacy ``workflow_event.event_type`` strings that mark a node having
# FINISHED (completed or failed). Used to tell a STILL-OPEN human-input
# checkpoint apart from one that was already answered/timed-out: a node
# that emitted ``awaiting_intervention`` and later one of these (after the
# input_required timestamp) is no longer waiting. Mirrors
# event_adapter._PG_LEGACY_TO_V1 -> NODE_COMPLETED / NODE_FAILED.
_NODE_TERMINAL_EVENT_TYPES = (
    "end",
    "node_completed",
    "execution_failed",
    "node_failed",
)

# Agent-driven clarification (ENG-2805) is persisted as a ``lifecycle`` row,
# NOT an ``awaiting_*``/``node.input_required`` row, and carries the asking
# node under ``event_data['data']['asker_node_id']`` with the transition in
# ``event_data['phase']`` -- a shape the intervention/HITL detectors above
# never match. These constants drive the clarification-aware durable resolver.
_LIFECYCLE_EVENT_TYPE = "lifecycle"
_CLARIFICATION_REQUESTED_PHASE = "clarification_requested"
_CLARIFICATION_RESOLVED_PHASE = "clarification_resolved"


async def _resolve_pending_clarification_via_events(run) -> Dict[str, Any]:
    """Resolve a run's OPEN agent-clarification checkpoint from durable
    ``lifecycle`` rows (the clarification analogue of
    ``_resolve_pending_via_events``).

    When an agent calls ``ask_user_clarification`` the workflow parks and
    ``emit_clarification_requested_activity`` publishes a ``LifecycleEvent``
    that the Redis->Postgres persister writes to ``workflow_event`` as
    ``event_type='lifecycle'`` with ``node_instance_id=NULL`` and::

        event_data = {
            "phase": "clarification_requested",
            "data": {"asker_node_id": "<node>", "question": ..., "context": ...},
            ...
        }

    The HITL detectors key on ``awaiting_*`` event types + a top-level
    ``node_id``/``request_id`` and so NEVER matched this row -- which is why a
    clarification-paused eval run looked like "running, no pending checkpoint"
    to the orchestrator (it pauses via clarification, not the Human
    Intervention node). We read the lifecycle rows directly and treat a
    request as OPEN unless a later ``clarification_resolved`` for the SAME
    asker exists (answered / timed-out / skipped). The asking node id IS the
    reply key ``deliver_clarification`` expects, so we surface it as both
    ``node_id`` and ``request_id``.

    Returns ``{node_id, request_id, input_kind='clarification'}`` for an open
    checkpoint, else ``{}``. Best-effort: any failure degrades to ``{}``.
    """
    try:
        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            stmt = (
                select(WorkflowEvent)
                .where(
                    WorkflowEvent.run_id == run.id,
                    WorkflowEvent.event_type == _LIFECYCLE_EVENT_TYPE,
                )
                .order_by(WorkflowEvent.timestamp.desc())
            )
            rows = (await session.execute(stmt)).scalars().all()
        # Walk newest-first. An asker is "closed" once we have seen its
        # resolve row; the first still-open ``clarification_requested`` wins.
        resolved_askers: set = set()
        for row in rows:
            data = row.event_data or {}
            if not isinstance(data, dict):
                continue
            phase = data.get("phase")
            inner = data.get("data") if isinstance(data.get("data"), dict) else {}
            asker = inner.get("asker_node_id") or data.get("asker_node_id")
            if not phase or not asker:
                continue
            if phase == _CLARIFICATION_RESOLVED_PHASE:
                resolved_askers.add(asker)
            elif phase == _CLARIFICATION_REQUESTED_PHASE:
                if asker in resolved_askers:
                    continue
                return {
                    "node_id": asker,
                    "request_id": asker,
                    "input_kind": "clarification",
                }
    except Exception as e:  # noqa: BLE001 - best-effort durable resolution
        logger.warning("pending-clarification event resolution failed for run: %s", e)
    return {}


async def _resolve_pending_clarification_via_temporal(run) -> Dict[str, Any]:
    """Resolve a run's OPEN agent-clarification checkpoint by querying the
    LIVE Temporal workflow -- the authoritative, always-consistent source.

    A clarification pause keeps the ``WorkflowRun`` row at ``status=running``
    with ``pause_state=NULL`` (``AgenticWorkflow._enter_clarification_wait``
    deliberately does NOT call ``mark_run_paused_activity``), so every consumer
    that asks "is this run waiting on the user?" by reading the run row sees
    "running, nothing pending". The Human Intervention node also writes a
    DURABLE Redis ``intervention_awaiting`` KEY (read by
    ``_resolve_pending_via_redis``), which is why its bare-"approved" path is
    reliable; a clarification has NO durable marker. Its only out-of-workflow
    signal is the single ``CLARIFICATION_REQUESTED`` lifecycle event, mirrored
    to ``workflow_event`` by the best-effort, single-leader Redis->PG persister
    -- an EPHEMERAL pub/sub emit. When that one emit is missed (leader
    handover, resubscribe gap, persister not the lease holder at that instant)
    the lifecycle row never lands, so ``_resolve_pending_clarification_via_events``
    returns nothing and the orchestrator concludes "no checkpoint" -- the exact
    live failure where a bare answer never binds.

    The workflow's ``get_detailed_status`` query exposes the live
    ``pending_clarifications`` list (the same source the FE uses for reload
    recovery, and the same signal the A2A task projection already trusts in
    ``api/a2a_gateway/task_store._has_pending_clarification``). Reading it
    straight from the handle needs neither the pub/sub nor the persister, so it
    is reliable. The asking node id IS the reply key
    ``deliver_clarification`` expects, surfaced as both ``node_id`` and
    ``request_id``.

    Returns ``{node_id, request_id, input_kind='clarification'}`` for the first
    open clarification, else ``{}``. Best-effort: any failure degrades to ``{}``.
    """
    from datetime import timedelta

    try:
        from temporal.client import get_temporal_client
        from temporal.config import workflow_id_for_session
        from temporal.workflows.agentic_workflow import AgenticWorkflow

        client = await get_temporal_client()
        handle = client.get_workflow_handle(workflow_id_for_session(run.session_id))
        detailed = await handle.query(
            AgenticWorkflow.get_detailed_status,
            rpc_timeout=timedelta(seconds=5),
        )
        pending = (detailed or {}).get("pending_clarifications") or []
        for req in pending:
            if not isinstance(req, dict):
                continue
            asker = req.get("node_id")
            if asker:
                return {
                    "node_id": asker,
                    "request_id": asker,
                    "input_kind": "clarification",
                }
    except Exception as e:  # noqa: BLE001 - best-effort live resolution
        logger.warning(
            "pending-clarification temporal resolution failed for run: %s", e
        )
    return {}


def _pending_pause_state(pending: Dict[str, Any]) -> Dict[str, Any]:
    """Build the synthesised ``pause_state`` block for an open human-input
    checkpoint resolved by :func:`_resolve_open_checkpoint`.

    Both the status tool and the capped-wait return of ``start_workflow`` expose
    the same shape so a consumer keying off ``pause_state`` (the status turn, the
    LLM) sees the checkpoint consistently regardless of which tool surfaced it.

    Args:
        pending: A resolved checkpoint dict (``node_id``, ``request_id``,
            ``input_kind``; ``input_kind`` may be absent for the chat family).

    Returns:
        A ``pause_state`` dict with ``awaiting_user_input`` set to True.
    """
    return {
        "kind": pending.get("input_kind") or "intervention",
        "input_kind": pending.get("input_kind"),
        "node_id": pending.get("node_id"),
        "request_id": pending.get("request_id"),
        "awaiting_user_input": True,
    }


def _still_executing_wait_note(run_id: str, waited_s: int) -> str:
    """Note for a capped wait that ended with the run merely still running.

    The run card in the chat already reports a live run -- progress, the step
    executing now, the elapsed clock, and the outcome and deliverables once it
    ends. Told only that the wait was capped, the agent narrated all of that
    back in prose and then coached the user on how to ask for more ("the run is
    executing safely in the background, so you don't need to keep this open --
    just say 'check status'"). That duplicates the card and makes a run the
    platform is already watching sound like something the user has to mind.

    Args:
        run_id: The run the capped wait was following.
        waited_s: How long the synchronous wait actually lasted.

    Returns:
        The instruction string to place on the tool result's ``wait_note``.
    """
    return (
        f"The run is still executing after {waited_s}s. Chat turns cap "
        "synchronous waits to stay responsive; the run CONTINUES, and the "
        "chat's live run card goes on showing it -- progress, the step "
        "executing now, and its outcome and deliverables once it ends. So tell "
        "the user the work is still going in ONE short line and leave it there: "
        "do NOT recite the run_id, do NOT describe background execution or tabs "
        "they need not keep open, and do NOT tell them what to type for an "
        'update (no "say \'check status\'", no "ask me if it\'s done") -- the '
        f"card already answers that. Use get_workflow_run_status('{run_id}') "
        "when YOU need the state and get_workflow_output once terminal. Do NOT "
        "call start_workflow again."
    )


def _pending_checkpoint_wait_note(pending: Dict[str, Any]) -> str:
    """Directive note for the chat agent when a capped wait ends with the run
    blocked on a human-input checkpoint (rather than merely still executing).

    Tells the agent to surface the review/question to the user immediately and
    relay the answer via the correct submit tool -- instead of reporting a bare
    "still running" and going silent until asked, which lets the checkpoint time
    out un-answered.

    The submit tool depends on the checkpoint family (mirrors the duplicate-run
    guard): agent clarifications (``input_kind == "clarification"``) resume via
    ``submit_clarification_response``; Human Intervention and interactive-feedback
    chat checkpoints resume via ``submit_intervention_response``. Routing a
    clarification answer to the intervention channel would leave the run blocked
    until timeout.

    Args:
        pending: A resolved checkpoint dict (see :func:`_pending_pause_state`).

    Returns:
        The instruction string to place on the tool result's ``wait_note``.
    """
    kind = (pending.get("input_kind") or "intervention").lower()
    submit_tool = (
        "submit_clarification_response"
        if kind == "clarification"
        else "submit_intervention_response"
    )
    review_url = pending.get("review_url")
    if review_url:
        document = (
            "Give them this download link so they can open the document "
            f"under review: {review_url}"
        )
    elif pending.get("review_document_owned_elsewhere"):
        document = (
            "A document under review reaches the user separately: NEVER paste "
            "a link or URL to it."
        )
    else:
        document = ""
    return (
        "The run is WAITING FOR THE USER at a "
        f"{pending.get('input_kind') or 'review'} checkpoint "
        f"(node {pending.get('node_id')}). Surface this to the user NOW: present "
        "the checkpoint's ACTUAL prompt (fetch the pending-input details if "
        "they are not already in context) -- not a generic 'something is "
        f"waiting' message -- and ask them to respond, then relay their answer "
        f"with {submit_tool}.{f' {document}' if document else ''} Do NOT report the run as merely "
        "'still running', and do NOT call start_workflow again."
    )


def _omit_review_document_link(payload: Any) -> Any:
    """Drop the document-under-review link from a ``node.input_required`` payload.

    ``get_workflow_events`` otherwise returns that link verbatim (and
    re-mints it durable). The chat turn owns it: a table when one will
    reach the user, otherwise the prose link on the pending checkpoint.
    """
    if isinstance(payload, list):
        return [_omit_review_document_link(item) for item in payload]
    if not isinstance(payload, dict):
        return payload
    cleaned: Dict[str, Any] = {}
    for key, value in payload.items():
        if key in ("review_url", "latest_url"):
            continue
        if key == "content" and isinstance(value, dict):
            value = {ck: cv for ck, cv in value.items() if ck != "url"}
        cleaned[key] = _omit_review_document_link(value)
    return cleaned


def _review_table_key(review_url: str) -> str:
    """Dedup key for a review table. Stable across re-mints of one object."""
    from services.node_results.url_refresh import review_link_identity

    return review_link_identity(review_url)


def _review_table_already_shown(node: Any, review_url: str) -> bool:
    """True when this turn already emitted a table for this document."""
    shown = getattr(node, "review_tables_emitted", None) or set()
    return _review_table_key(review_url) in shown


def _emit_review_table(node: Any, review_url: str, run_id: Optional[str]) -> bool:
    """Enqueue a review-document table if this caller will actually see it.

    Returns True when the table is on the way to the user (just enqueued,
    or already emitted for this URL). Returns False when no frame will
    arrive, so the caller keeps the prose link.
    """
    record = getattr(node, "agent_record", None) or {}
    if not bool(record.get("render_files_in_chat", True)):
        return False
    if not bool(getattr(node, "delivers_show_output", True)):
        return False
    if _review_table_already_shown(node, review_url):
        return True
    queue = getattr(node, "turn_event_queue", None)
    if queue is None:
        return False
    from api.a2a_gateway.orchestrator import (
        ChatStreamEvent,
        _build_fallback_review_document_batch,
        _build_review_document_batch,
    )

    try:
        batch = _build_review_document_batch(review_url, run_id)
    except Exception as exc:  # noqa: BLE001 - side-channel is best-effort
        logger.warning("review table enqueue failed; using fallback batch: %s", exc)
        try:
            batch = _build_fallback_review_document_batch(review_url, run_id)
        except Exception as fallback_exc:  # noqa: BLE001 - defensive
            logger.warning(
                "review table fallback enqueue failed (non-fatal): %s",
                fallback_exc,
            )
            return False
    try:
        queue.put_nowait(ChatStreamEvent(type="show_output", payload=batch))
    except Exception as exc:  # noqa: BLE001 - side-channel is best-effort
        logger.warning("review table enqueue failed (non-fatal): %s", exc)
        return False
    shown = getattr(node, "review_tables_emitted", None)
    if shown is None:
        shown = set()
        node.review_tables_emitted = shown
    shown.add(_review_table_key(review_url))
    return True


def _mark_review_document_owned_elsewhere(pending: Dict[str, Any]) -> Dict[str, Any]:
    """Tag this pending item as having its review document surfaced elsewhere."""
    marked = dict(pending)
    marked["review_document_owned_elsewhere"] = True
    marked.pop("review_url", None)
    return marked


async def _review_url_for_pending(run: Any, pending: Dict[str, Any]) -> Optional[str]:
    """Durable review link for this checkpoint, or None when there is none."""
    try:
        from services.node_results.url_refresh import refresh_presigned_urls
    except Exception:  # pragma: no cover - import is stable in production
        refresh_presigned_urls = None
    if pending.get("review_url"):
        review_url = str(pending["review_url"])
        if refresh_presigned_urls is None:
            return review_url
        try:
            refreshed = refresh_presigned_urls({"data": review_url}, durable=True)
            return str(refreshed.get("data") or review_url)
        except Exception as exc:  # noqa: BLE001 - best-effort refresh
            logger.warning("review link refresh failed (non-fatal): %s", exc)
            return review_url
    session_id = getattr(run, "session_id", None)
    if not session_id or not pending.get("node_id"):
        return None
    try:
        doc = await RedisServiceSingleton().get_chat_review_document(str(session_id))
        if (
            not doc
            or str(doc.get("node_id")) != str(pending["node_id"])
            or not doc.get("review_url")
        ):
            return None
        review_url = str(doc["review_url"])
        try:
            refreshed = refresh_presigned_urls({"data": review_url}, durable=True)
        except Exception as exc:  # noqa: BLE001 - best-effort refresh
            logger.warning("review link refresh failed (non-fatal): %s", exc)
            return review_url
        return str(refreshed.get("data") or review_url)
    except Exception as exc:  # noqa: BLE001 - best-effort link lookup
        logger.warning("review link lookup failed (non-fatal): %s", exc)
        return None


async def _pending_for_model(
    node: Any, run: Any, pending: Dict[str, Any]
) -> Dict[str, Any]:
    """Pending checkpoint as a tool result should show it to the model.

    When this caller can receive a frame, the document goes out as a table
    now -- including a checkpoint that opened inside the model loop -- and
    the link stays off the result. Otherwise the prose link stays on.
    """
    review_url = await _review_url_for_pending(run, pending)
    run_id = getattr(run, "id", None)
    emitted = bool(review_url) and _emit_review_table(
        node, str(review_url), str(run_id) if run_id else None
    )
    if emitted:
        node.withhold_review_link = True
        return _mark_review_document_owned_elsewhere(pending)
    if not review_url:
        return pending
    attached = dict(pending)
    attached["review_url"] = review_url
    attached.pop("review_document_owned_elsewhere", None)
    return attached


async def _resolve_open_checkpoint(run) -> Dict[str, Any]:
    """Live, marker/record-independent resolution of a run's OPEN human-input
    checkpoint -- the shared detection the status tool, the grounding block and
    the submit tools all agree on, so they never disagree about whether a run is
    waiting on the user.

    Order (first hit wins), covering BOTH HITL families:
      1. Redis awaiting markers -- Human Intervention / interactive-feedback
         chat nodes (the durable, authoritative "waiting right now" key).
      2. Temporal ``pending_clarifications`` query -- agent clarification, the
         authoritative live source independent of the best-effort event
         persister (the clarification family has no durable Redis marker).
      3. Durable ``node.input_required`` event rows -- HITL fallback when the
         live marker momentarily races a gap.
      4. Durable clarification ``lifecycle`` rows -- clarification fallback
         when the Temporal query is unavailable but the event was mirrored.
      5. Saved review-document record -- document-review fallback for the
         moments right after a pause opens when the signals above all miss
         (ENG-3543). Last so it can never mask a real clarification pause, and
         guarded against already-finished nodes inside the helper. Included
         here so the status tool and the start-workflow duplicate-run guard
         agree with the chat grounding about an open review.

    Returns ``{node_id, request_id, input_kind}`` for an open checkpoint, else
    ``{}``. The chat-family resolvers (Redis marker, review-doc) add
    ``node_family="chat"``, and the review-doc resolver also adds
    ``review_url``. Best-effort: a failing resolver is skipped (each
    degrades to ``{}``).
    """
    for resolver in (
        _resolve_pending_via_redis,
        _resolve_pending_clarification_via_temporal,
        _resolve_pending_via_events,
        _resolve_pending_clarification_via_events,
        _resolve_pending_via_review_doc,
    ):
        found = await resolver(run)
        if found:
            return found
    return {}


async def _resolve_pending_via_events(run) -> Dict[str, Any]:
    """Resolve a run's OPEN human-input checkpoint from the durable
    ``workflow_event`` rows (the reliability fix's primary fallback).

    The live Redis ``intervention_awaiting`` marker is the fast signal, but
    live staging proved it is missed intermittently by the gateway's
    per-turn read while the panel is genuinely open (the panel fired, the run
    later TIMED OUT at +300s, yet the orchestrator's marker read returned
    nothing across several turns). The ``awaiting_intervention`` /
    ``awaiting_user_input`` event, by contrast, is durably persisted to
    ``workflow_event`` by the Redis->Postgres event persister the instant the
    panel opens and is reliably readable by the gateway -- so we use it as a
    marker-independent signal that a checkpoint is open.

    Staleness guard: the persisted input_required row is NOT cleared when the
    checkpoint is answered, so we only treat it as pending when the asking
    node has NOT since finished -- i.e. there is no ``end`` / ``node_completed``
    / node-failure event for that node AFTER the input_required timestamp.
    Combined with the caller's run-terminal short-circuit, an answered or
    timed-out checkpoint stops being surfaced.

    Returns ``{node_id, request_id, input_kind}`` for an open checkpoint, else
    an empty dict. Best-effort: any failure degrades to ``{}``.
    """
    try:
        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            stmt = (
                select(WorkflowEvent)
                .where(
                    WorkflowEvent.run_id == run.id,
                    WorkflowEvent.event_type.in_(_INPUT_REQUIRED_EVENT_TYPES),
                )
                .order_by(WorkflowEvent.timestamp.desc())
            )
            row = (await session.execute(stmt)).scalars().first()
            if row is None:
                return {}
            data = row.event_data or {}
            inner = data.get("data") if isinstance(data.get("data"), dict) else {}
            extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
            node_id = (
                row.node_instance_id
                or data.get("node_id")
                or inner.get("node_id")
                or extra.get("node_id")
            )
            request_id = (
                data.get("request_id")
                or inner.get("request_id")
                or extra.get("request_id")
            )
            input_kind = data.get("input_kind") or inner.get("input_kind")
            if not node_id:
                return {}
            # Still open? Only if the asking node has not finished AFTER it
            # asked for input.
            term_stmt = (
                select(WorkflowEvent.id)
                .where(
                    WorkflowEvent.run_id == run.id,
                    WorkflowEvent.node_instance_id == node_id,
                    WorkflowEvent.event_type.in_(_NODE_TERMINAL_EVENT_TYPES),
                    WorkflowEvent.timestamp > row.timestamp,
                )
                .limit(1)
            )
            resolved = (await session.execute(term_stmt)).first() is not None
        if resolved:
            return {}
        if not request_id:
            request_id = _deterministic_intervention_request_id(run.session_id, node_id)
        return {
            "node_id": node_id,
            "request_id": request_id,
            "input_kind": input_kind or "intervention",
        }
    except Exception as e:  # noqa: BLE001 - best-effort durable resolution
        logger.warning("pending-input event resolution failed for run: %s", e)
        return {}


async def _sticky_pending_resolved(
    run, node_id: Optional[str], since: Optional[datetime] = None
) -> bool:
    """A1: True when a sticky pending checkpoint's node has since COMPLETED.

    The sticky ``pending_*`` on the active-run record can outlive the
    checkpoint when it was answered/cleared outside this conversation (FE,
    timeout) and ``clear_pending_input`` never ran.  A node-terminal event
    (``end`` / ``node_completed`` / failure) for the sticky node means the
    checkpoint closed, so the grounding block must NOT keep surfacing it.

    ``since`` bounds the terminal event to AFTER a known park time. Without it,
    a node that ran once, finished, then parked again under the same id would
    look finished forever (its old terminal event still matches). Callers that
    know when the current pause opened pass it so only a completion AFTER that
    park counts as resolved.

    Best-effort: any failure returns ``False`` (keep the sticky pending) so
    we never accidentally drop a genuinely-open checkpoint.
    """
    if not node_id:
        return False
    try:
        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            stmt = (
                select(WorkflowEvent.id)
                .where(
                    WorkflowEvent.run_id == run.id,
                    WorkflowEvent.node_instance_id == node_id,
                    WorkflowEvent.event_type.in_(_NODE_TERMINAL_EVENT_TYPES),
                )
                .limit(1)
            )
            if since is not None:
                stmt = stmt.where(WorkflowEvent.timestamp > since)
            return (await session.execute(stmt)).first() is not None
    except Exception as e:  # noqa: BLE001 - best-effort staleness probe
        logger.warning("sticky-pending staleness probe failed: %s", e)
        return False


async def _is_chat_feedback_node(session_id: str, node_id: str) -> bool:
    """Return True when ``node_id`` is an interactive-feedback (review-loop)
    node rather than a Human Intervention node. That covers the docx/xlsx/
    pptx chat-output nodes AND the agent / code-interpreter nodes with
    "Ask for feedback on outputs" enabled: every node that pauses via the
    shared ``InteractiveFeedbackMixin.await_user_input`` sets the
    ``chat_awaiting`` marker while waiting, so the detection needs no
    per-node-type knowledge. Drives the finalize-token choice in
    ``submit_intervention_response`` so an approval actually completes the
    node instead of looping it for more edits.

    Checks two signals, either of which is proof:
      * the live ``chat_awaiting`` marker names this node, or
      * the saved review-document record is for this node.
    The live marker has brief gaps right after a pause opens; the saved
    review-document record is written only by the document-review nodes and
    stays put across that gap, so it keeps the classification correct on the
    exact turns the marker is missing (without it, an approval on such a turn
    is treated as more feedback and the node loops).

    Best-effort: any Redis failure degrades to ``False`` (treat as a normal
    intervention node), which is the pre-existing behaviour.
    """
    if not (session_id and node_id):
        return False
    try:
        redis = RedisServiceSingleton()
        if (await redis.get_chat_awaiting(session_id)) == node_id:
            return True
        doc = await redis.get_chat_review_document(session_id)
        return bool(doc) and str(doc.get("node_id")) == str(node_id)
    except Exception as e:  # noqa: BLE001 - convenience layer, never fatal
        logger.warning("chat-feedback node detection failed: %s", e)
        return False


async def _resolve_pending_via_redis(run) -> Dict[str, Any]:
    """Resolve a run's open human-input checkpoint from the live Redis
    awaiting markers (the authoritative "is a node waiting right now" signal).

    The Human Intervention node sets ``intervention_awaiting`` and the
    interactive-feedback chat nodes (docx/xlsx/pptx) set ``chat_awaiting``;
    both store the awaiting node id. Combined with the deterministic
    ``request_id`` we can resolve the checkpoint without relying on the
    intervention event being persisted to ``workflow_event`` (it is published
    to Redis only). Returns an empty dict when nothing is awaiting.
    """
    try:
        redis = RedisServiceSingleton()
        session_id = run.session_id
        intervention_node = await redis.get_intervention_awaiting(session_id)
        if intervention_node:
            return {
                "node_id": intervention_node,
                "request_id": _deterministic_intervention_request_id(
                    session_id, intervention_node
                ),
                "input_kind": "intervention",
            }
        chat_node = await redis.get_chat_awaiting(session_id)
        if chat_node:
            # The chat-node delivery channel is request_id-agnostic
            # (chat:response:{wf}:{session}:{node}), so any id is accepted;
            # we still reconstruct one for a consistent shape. ``node_family``
            # flags this as the interactive-feedback chat family (docx/xlsx/
            # pptx) so the submit path delivers a finalize token instead of a
            # bare "continue" (which these nodes treat as more feedback and
            # loop on). ``input_kind`` stays "intervention" so the existing
            # grounding/submit routing is unchanged.
            return {
                "node_id": chat_node,
                "request_id": _deterministic_intervention_request_id(
                    session_id, chat_node
                ),
                "input_kind": "intervention",
                "node_family": "chat",
            }
    except Exception as e:  # noqa: BLE001 - best-effort live resolution
        logger.warning("pending-input redis resolution failed for run: %s", e)
    return {}


async def _resolve_pending_via_review_doc(run) -> Dict[str, Any]:
    """Detect an open document-review pause from the saved review document.

    A document node (Word/Excel/PowerPoint) saves the link to the document it
    just generated as soon as it pauses to ask the user for changes. That saved
    link is the last-resort signal that the node is waiting: the two detectors
    above can both briefly come up empty right after a pause opens, and when
    that happens the chat loses track of the pause and shows the user unrelated
    files instead of the document under review (ENG-3543).

    Only treat the saved link as an open pause while its node has not finished
    yet, so a leftover link from an earlier round or a completed run is never
    mistaken for a live pause. The saved link also expires on its own shortly
    after the pause window, which bounds the worst case.

    Returns ``{node_id, request_id, input_kind, node_family, review_url}`` when
    a document review is open, else ``{}``. Callers that already delivered the
    document this turn drop ``review_url`` via :func:`_pending_for_model`.
    Never raises -- any error means "no pause".
    """
    try:
        session_id = run.session_id
        if not session_id:
            return {}
        redis = RedisServiceSingleton()
        doc = await redis.get_chat_review_document(str(session_id))
        if not doc or not doc.get("node_id") or not doc.get("review_url"):
            return {}
        node_id = str(doc["node_id"])
        # Ignore the saved link once its node has finished (the review was
        # answered, timed out, or the node moved on). Bound the check to this
        # record's park time so a completion from an EARLIER run of the same
        # node id (loops / re-entry) does not make a freshly re-opened review
        # look finished.
        since: Optional[datetime] = None
        written_at = doc.get("written_at")
        if written_at:
            try:
                since = datetime.fromisoformat(str(written_at))
            except ValueError:
                since = None
        if await _sticky_pending_resolved(run, node_id, since=since):
            return {}
        review_url = str(doc["review_url"])
        try:
            from services.node_results.url_refresh import refresh_presigned_urls

            refreshed = refresh_presigned_urls({"data": review_url}, durable=True)
            review_url = refreshed.get("data") or review_url
        except Exception:  # noqa: BLE001 - fall back to the stored link
            pass
        # node_family="chat" marks this as a document-review pause so an
        # "approved" reply finishes the node instead of looping for more edits.
        return {
            "node_id": node_id,
            "request_id": _deterministic_intervention_request_id(session_id, node_id),
            "input_kind": "intervention",
            "node_family": "chat",
            "review_url": review_url,
        }
    except Exception as e:  # noqa: BLE001 - best-effort fallback resolution
        logger.warning("pending-input review-doc resolution failed for run: %s", e)
        return {}


async def resolve_conversation_run_context(
    *,
    org_id: str,
    agent_id: Optional[str],
    context_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Build a live summary of a conversation's active run for the orchestrator.

    Read at the START of every chat turn so the LLM is grounded in the run it
    started earlier in THIS conversation -- the run-context retention defect
    (OneEdge "forgets run context"): the active-run record is persisted to
    Redis on ``start_workflow`` but nothing told the orchestrator about it at
    turn start, so a bare "approved" on a later turn made it re-reason from
    (possibly self-contradictory) chat text, deny its own prior turn, and
    spawn a DUPLICATE run instead of answering the pending checkpoint.

    This:
      * loads the active run and its live status,
      * clears the active-run record once the run is terminal (so a finished
        run stops being surfaced),
      * resolves + PERSISTS the pending ``node.input_required`` (node_id +
        request_id + input_kind) via ``set_pending_input`` so it carries
        across turns and the submit tools resolve instantly. Resolution is
        STICKY: it prefers the live Redis awaiting marker but falls back to
        the pending persisted on an earlier turn when that marker is
        momentarily absent (the marker has gaps while the panel is open --
        it is set just after the emit and cleared in the node's ``finally``
        between INTERACTIVE-activity retries), so a live checkpoint is
        detected EVERY turn the panel is open, not just when the read races
        the marker.

    Returns ``{run_id, session_id, workflow_id, status, is_terminal,
    pending}`` (``pending`` is ``None`` or ``{node_id, request_id,
    input_kind}``), or ``None`` when there is no active run. Best-effort:
    every failure degrades to ``None`` rather than breaking the chat turn.
    """
    if not (agent_id and context_id):
        return None
    try:
        from api.a2a_gateway.active_run_context import (
            clear_active_run,
            get_active_run,
            set_pending_input,
        )

        record = await get_active_run(
            org_id=org_id, agent_id=agent_id, context_id=context_id
        )
        if not record or not record.get("run_id"):
            return None

        run = await _load_run(record["run_id"], org_id)
        if run is None:
            # The run row isn't visible (cross-org, deleted, or not yet
            # committed) -- surface what we know without a status so the LLM
            # still knows an active run exists in this conversation.
            return {
                "run_id": record.get("run_id"),
                "session_id": record.get("session_id"),
                "workflow_id": record.get("workflow_id"),
                "status": "unknown",
                "is_terminal": False,
                "pending": None,
            }

        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        is_terminal = status in _TERMINAL_STATUSES
        summary: Dict[str, Any] = {
            "run_id": str(run.id),
            "session_id": run.session_id,
            "workflow_id": str(run.workflow_id),
            "status": status,
            "is_terminal": is_terminal,
            "pending": None,
        }
        if is_terminal:
            if status == "success":
                # Completion-return fix: on terminal SUCCESS, resolve the run's
                # download link(s) deterministically and KEEP the active-run
                # record so a later bare "where's my file?" still grounds on this
                # finished run (it expires with the conversation TTL). Clearing it
                # here -- as the checkpoint lifecycle used to -- is what made the
                # orchestrator forget a just-finished run and either ask the user
                # for the run_id or re-run the workflow from scratch.
                summary["artifacts"], _ = await _resolve_run_output_files(
                    str(run.id), org_id
                )
                summary["completed_at"] = (
                    run.completed_at.isoformat() if run.completed_at else None
                )
                return summary
            # Non-success terminal (failed/cancelled/stopped): drop the
            # record so recover/rerun can re-anchor to a fresh run cleanly.
            if status == "failed":
                summary["error_message"] = getattr(run, "error_message", None)
            await clear_active_run(
                org_id=org_id, agent_id=agent_id, context_id=context_id
            )
            return summary

        # Layered, STICKY pending resolution (the live-detection reliability
        # fix). The Redis ``intervention_awaiting`` / ``chat_awaiting`` marker
        # is the ONLY live signal that a HITL node is parked (the
        # ``awaiting_intervention`` event is published to the Redis WS channel
        # via ``_emit_node_event`` and is NEVER persisted to ``workflow_event``,
        # so the legacy event query can't see it; and the wait blocks INSIDE
        # the INTERACTIVE activity, so the Temporal workflow exposes no
        # HITL-pending query the way it does for clarifications). That marker
        # has transient GAPS while the panel is genuinely open:
        #   * it is set just AFTER the ``node.input_required`` emit, and
        #   * the node's ``finally`` clears it on every INTERACTIVE activity
        #     attempt exit, so across a retry (maximum_attempts=4) there is a
        #     backoff window where the panel is shown (clients dedup by the
        #     stable request_id) but no marker exists.
        # Reading ONLY the live marker therefore detected the checkpoint
        # "sometimes" -- exactly when the user's turn happened to miss a gap --
        # which is the intermittent miss seen on staging. We make detection
        # robust by mirroring the submit tools' resolution and falling back to
        # the ``pending_*`` we persisted on an EARLIER turn when the live
        # marker is momentarily absent:
        #   1. live Redis marker (authoritative + refreshes the ids), else
        #   2. the persisted pending on the active-run record (sticky across
        #      the marker gap).
        # The sticky value can only ever re-surface a checkpoint we observed
        # live at least once (``pending_*`` is written only after a live hit),
        # and it is dropped when the run goes terminal (above) or when a
        # ``submit_*`` delivers the answer in this conversation
        # (``clear_pending_input``), so an answered checkpoint stops being
        # surfaced.
        pending = await _resolve_pending_via_redis(run)
        if not pending:
            # Durable, marker-independent signal: the input_required event is
            # persisted to workflow_event the instant the panel opens and is
            # reliably readable here, so it catches checkpoints the flaky live
            # marker read misses (the staging intermittency). Guarded against
            # already-resolved checkpoints inside the helper.
            pending = await _resolve_pending_via_events(run)
        if not pending:
            # Agent-driven clarification (ENG-2805) keeps the run at RUNNING
            # with pause_state=null and has NO durable Redis marker, so the
            # detectors above miss it entirely. Without this the orchestrator
            # grounded itself as "running, no pending checkpoint" on a
            # clarification pause (the AI Eval Importance workflow's pause
            # kind), denied its own turn, and let the run time out into the
            # synthetic default. Prefer the LIVE Temporal query (the same
            # authoritative source the A2A task projection trusts), which is
            # independent of the best-effort lifecycle-event persister whose
            # single ephemeral emit can be missed -- the live-binding failure.
            pending = await _resolve_pending_clarification_via_temporal(run)
        if not pending:
            # Durable fallback: the persisted clarification ``lifecycle`` row,
            # when the Temporal query is unavailable (worker drained / run
            # already handed off) but the event was mirrored to Postgres.
            pending = await _resolve_pending_clarification_via_events(run)
        if not pending:
            # Last-resort document-review signal: the saved review-document
            # link, for the moments right after a pause opens when the live
            # detectors above briefly return nothing (ENG-3543). Runs AFTER the
            # clarification detectors so a leftover link can never mask a real
            # clarification pause, and is guarded against already-finished
            # nodes inside the helper.
            pending = await _resolve_pending_via_review_doc(run)
        if not pending and record.get("pending_request_id"):
            # A1 (never bind to a stale checkpoint): the live resolvers found
            # nothing open, so before falling back to the sticky pending from
            # an earlier turn, confirm the sticky node hasn't since COMPLETED.
            # If a node-terminal event exists for it, the checkpoint closed
            # (answered elsewhere / timed out) -- drop the sticky pending
            # instead of re-surfacing an already-resolved checkpoint.
            sticky_node = record.get("pending_node_id")
            sticky_stale = await _sticky_pending_resolved(run, sticky_node)
            if sticky_stale:
                try:
                    from api.a2a_gateway.active_run_context import (
                        clear_pending_input,
                    )

                    await clear_pending_input(
                        org_id=org_id, agent_id=agent_id, context_id=context_id
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("stale sticky clear failed: %s", e)
            else:
                pending = {
                    "node_id": sticky_node,
                    "request_id": record.get("pending_request_id"),
                    "input_kind": record.get("pending_input_kind"),
                }
        if pending:
            summary["pending"] = pending
            try:
                await set_pending_input(
                    org_id=org_id,
                    agent_id=agent_id,
                    context_id=context_id,
                    node_id=pending.get("node_id"),
                    request_id=pending.get("request_id"),
                    input_kind=pending.get("input_kind"),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("set_pending_input failed (non-fatal): %s", e)
        return summary
    except Exception as e:  # noqa: BLE001 - never break the chat turn
        logger.warning("resolve_conversation_run_context failed: %s", e)
        return None


def _synthetic_user(node) -> Dict[str, Any]:
    """current_user dict for runs initiated from inside an AgentNode tool.

    Plumbs the node's existing identity through to the trigger handler.
    The handler uses these for run_by + Temporal/Redis enqueue context.

    ENG-2365: ``user_id`` falls back to the empty string (not
    ``node.node_id``) when the caller forgot to populate ``_user_id``.
    The previous ``or node.node_id`` fallback silently handed the
    node's *label* to the trigger handler, which then hit a
    ``workflow_run.run_by_id_fkey`` violation several layers
    downstream -- a Postgres error blamed for a problem rooted in
    this helper.  Failing closed with an empty string + a logged
    error surfaces the missing-context problem at the orchestrator
    boundary where the stack trace points at the right code.
    The trigger handler's defensive guard (added in the same PR)
    converts the empty value into a clean 500 with an actionable hint.
    """

    user_id = getattr(node, "_user_id", "") or ""
    if not user_id:
        # Logged at error -- a tool reaching this branch means the
        # caller built the orchestrator context without threading the
        # authenticated user through.  Both production call sites
        # (``executor.py:692`` for A2A, ``agents.py:300`` for chat)
        # SHOULD populate this; if we're here, treat it as a bug to
        # fix in the call site rather than silently lie about who
        # initiated the run.
        logger.error(
            "_synthetic_user: node %r has no _user_id; "
            "downstream workflow_run.run_by_id will be empty -- the "
            "calling code must populate the user's id on the "
            "orchestrator context (see ENG-2365)",
            getattr(node, "node_id", "unknown"),
        )

    return build_run_user(
        user_id=user_id,
        email=getattr(node, "_user_email", None),
        token=getattr(node, "_user_token", "") or "",
        fallback_email=f"agent-tool+{getattr(node, 'node_id', 'unknown')}@capitol.ai",
    )


async def _build_eval_node_metadata(session, run) -> Dict[str, Dict[str, Any]]:
    """Map ``node_instance_id -> {node_type, display_name}`` for a run.

    Mirrors ``executor._build_node_metadata`` so the chat-path eval roll-up
    attributes each eval to the same human-readable node name external A2A
    callers see.  Best-effort: returns ``{}`` on any failure.
    """
    from db.models.workflow_version import WorkflowVersion as WorkflowVersionModel

    meta: Dict[str, Dict[str, Any]] = {}
    try:
        version_id = getattr(run, "version_id", None)
        if version_id is None:
            return meta
        version = (
            await session.execute(
                select(WorkflowVersionModel).where(
                    WorkflowVersionModel.id == version_id
                )
            )
        ).scalar_one_or_none()
        payload = (version.payload if version else None) or {}
        nodes = payload.get("nodes") or []
        if not isinstance(nodes, list):
            return meta
        for node in nodes:
            if not isinstance(node, dict):
                continue
            data = node.get("data")
            struct = data.get("struct") if isinstance(data, dict) else None
            struct = struct if isinstance(struct, dict) else {}
            display = (
                struct.get("user_config_name")
                or node.get("user_config_name")
                or struct.get("display_name")
                or node.get("display_name")
                or struct.get("name")
                or node.get("name")
            )
            entry = {
                "node_type": struct.get("node_type") or node.get("node_type"),
                # A3: sub-type + catalog id so the output projection can tell
                # an INPUT node from a produced output node reliably.
                "node_sub_type": (
                    struct.get("node_sub_type") or node.get("node_sub_type")
                ),
                "catalog_node_id": struct.get("node_id") or node.get("node_id"),
                "display_name": display,
            }
            for key in (
                node.get("id"),
                node.get("react_flow_generated_id"),
                struct.get("react_flow_generated_id"),
                struct.get("node_id"),
            ):
                if key:
                    meta[str(key)] = entry
    except Exception as e:
        logger.warning("could not build eval node metadata for run: %s", e)
    return meta


# A3: input-node detection for the chat-path output projection lives in the
# shared ``services.a2a.node_io`` module (imported above as
# ``_classify_output_kind``) so the orchestrator tool agrees with the A2A
# ``get_workflow_output`` skill on produced deliverables vs echoed inputs.


async def _load_run(run_id: str, org_id: str) -> Optional[WorkflowRun]:
    factory = async_session_factory(use_fresh_engine=False)
    async with factory() as session:
        return await _load_run_in_session(session, run_id, org_id)


async def _load_run_in_session(
    session, run_id: str, org_id: str
) -> Optional[WorkflowRun]:
    """Fetch a workflow_run by id OR session_id, scoped to the agent's org."""

    run: Optional[WorkflowRun] = None
    try:
        run_uuid = uuid.UUID(str(run_id))
        stmt = select(WorkflowRun).where(WorkflowRun.id == run_uuid)
        run = (await session.execute(stmt)).scalar_one_or_none()
    except (TypeError, ValueError):
        stmt = select(WorkflowRun).where(WorkflowRun.session_id == str(run_id))
        run = (await session.execute(stmt)).scalar_one_or_none()
    if run is None:
        return None
    # Authorization: only return runs for workflows in this org
    wf_stmt = select(WorkflowModel.org_id).where(WorkflowModel.id == run.workflow_id)
    org_id_for_run = (await session.execute(wf_stmt)).scalar_one_or_none()
    if org_id_for_run != org_id:
        return None
    return run


async def _run_lookup_error(session, run_id: str, org_id: str) -> Dict[str, Any]:
    """Explain why an identifier cannot be used as a run_id without leaking
    cross-organization records."""

    try:
        identifier = uuid.UUID(str(run_id))
    except (TypeError, ValueError):
        return {
            "error": "run_not_found",
            "run_id": str(run_id),
            "message": (
                f"No run with id or session_id '{run_id}' is retrievable in "
                "this organization. Use the run_id returned by start_workflow, "
                "not a workflow_id or file_id."
            ),
        }

    workflow_stmt = select(WorkflowModel.id).where(
        WorkflowModel.id == identifier,
        WorkflowModel.org_id == org_id,
    )
    workflow_id = (await session.execute(workflow_stmt)).scalar_one_or_none()
    if workflow_id is not None:
        return {
            "error": "workflow_id_used_as_run_id",
            "workflow_id": str(workflow_id),
            "message": (
                f"'{run_id}' is a workflow_id, not a run_id. Do not retry it "
                "as a run. Use the run_id returned by start_workflow (or the "
                "pinned session facts), then call get_workflow_output with "
                "that run_id."
            ),
        }

    return {
        "error": "run_not_found",
        "run_id": str(run_id),
        "message": (
            f"No run '{run_id}' is retrievable in this organization. Confirm "
            "the exact run_id returned by start_workflow; a workflow_id and "
            "run_id are different identifiers."
        ),
    }


def _run_to_status_dict(run: WorkflowRun) -> Dict[str, Any]:
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    return {
        "run_id": str(run.id),
        "session_id": run.session_id,
        "workflow_id": str(run.workflow_id),
        "status": status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
        "pause_state": run.pause_state,
        # Says WHO ended the run. Read off the bare status alone, a stop looks
        # like any other way a run can end without finishing, and gets reported
        # back to the user as an unexplained halt -- when they are the one who
        # halted it, a moment earlier, with the Stop control.
        "stopped_by_user": status in STOPPED_RUN_STATUSES,
    }


async def _resolve_node_progress(run_id: uuid.UUID) -> Dict[str, Any]:
    """Return a compact node-progress summary for a run's status read.

    Lets a status turn report "N nodes completed so far" from the light status
    tool instead of escalating to ``get_workflow_events`` (whose unbounded
    return overflows the context window). Deliberately cheap: one grouped
    ``COUNT`` over ``node_result`` (statuses only, never ``output_data``).

    Reports only FINISHED-node counts, on purpose:

    * ``node_result`` rows are written when a node finishes (``completed`` /
      ``failed`` / ``skipped``) -- there is no ``running``/``pending`` row for
      in-flight work, so those would always read 0 and are omitted.
    * No denominator is returned: ``payload.nodes`` includes non-executable
      canvas nodes (annotations, etc.) the runner never runs, so a "N of M"
      total would never reach completion. "N completed so far" is honest and
      is enough for the agent to answer "how is it going" without the event log.

    Args:
        run_id: The run to summarise.

    Returns:
        ``{completed, failed, skipped, finished}`` finished-node counts.
    """
    counts: Dict[str, int] = {}
    factory = async_session_factory(use_fresh_engine=False)
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT status, COUNT(*) AS n FROM node_result "
                    "WHERE run_id = :rid GROUP BY status"
                ),
                {"rid": run_id},
            )
        ).all()
        for status_val, n in rows:
            # node_result.status is a SQLAlchemy Enum stored by NAME (the
            # uppercase label, e.g. "COMPLETED"), so raw SQL returns that
            # label -- not the lowercase value. Normalise to the lowercase
            # value (handling a bare label, a "Enum.LABEL" repr, or an already
            # lowercase value) so the counts below line up.
            key = str(status_val).split(".")[-1].lower()
            counts[key] = int(n)
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    skipped = counts.get("skipped", 0)
    return {
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "finished": completed + failed + skipped,
    }


async def _collect_input_issues(
    run_id: Any, *, limit: int = 10
) -> List[Dict[str, Any]]:
    """ENG-4104: compact unusable-file findings for the chat orchestrator.

    Survives an evals abort that would otherwise hide the node's output.
    Best-effort; never raises to the caller.
    """
    try:
        run_uuid = uuid.UUID(str(run_id))
    except (TypeError, ValueError):
        return []
    factory = async_session_factory(use_fresh_engine=False)
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(WorkflowEvent)
                    .where(
                        WorkflowEvent.run_id == run_uuid,
                        WorkflowEvent.event_type.in_(
                            ("input_issue", "node.input_issue")
                        ),
                    )
                    .order_by(WorkflowEvent.timestamp.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    out: List[Dict[str, Any]] = []
    for row in rows:
        data = row.event_data if isinstance(row.event_data, dict) else {}
        nested = data.get("data") if isinstance(data.get("data"), dict) else {}
        message = data.get("message") or nested.get("message") or ""
        if not message:
            continue
        item: Dict[str, Any] = {"message": str(message)}
        kind = data.get("issue_kind") or nested.get("issue_kind")
        if kind:
            item["issue_kind"] = str(kind)
        filename = data.get("filename") or nested.get("filename")
        if filename:
            item["filename"] = str(filename)
        field_name = data.get("field_name") or nested.get("field_name")
        if field_name:
            item["field_name"] = str(field_name)
        out.append(item)
    return out


def _forward_input_issue(payload: Dict[str, Any], on_input_issue: Any) -> None:
    """Mirror a Redis ``input_issue`` onto the live chat SSE (ENG-4104)."""
    if not callable(on_input_issue):
        return
    message = str(payload.get("message") or "").strip()
    if not message:
        return
    try:
        on_input_issue(
            message,
            issue_kind=payload.get("issue_kind") or "wrong_content",
            filename=payload.get("filename"),
            field_name=payload.get("field_name"),
        )
    except Exception:  # noqa: BLE001 - side-channel is best-effort
        logger.warning("await_terminal: notify_input_issue failed", exc_info=True)


async def _await_terminal(
    session_id: str,
    timeout_s: int,
    *,
    on_input_issue: Any = None,
) -> Optional[Dict[str, Any]]:
    """Subscribe to the run's Redis channel and return when it reaches a
    terminal status. Falls back to polling the DB if Redis fanout is unhealthy.

    ``on_input_issue`` (chat orchestrator ``notify_input_issue``) is invoked
    the moment a workflow agent publishes ``input_issue`` so the finding
    reaches the main chat / OneEdge LHS during ``wait_for_completion``,
    not only after the run ends.
    """

    async def _poll_db():
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            factory = async_session_factory(use_fresh_engine=False)
            async with factory() as session:
                stmt = select(WorkflowRun).where(WorkflowRun.session_id == session_id)
                run = (await session.execute(stmt)).scalar_one_or_none()
            if (
                run
                and (
                    run.status.value
                    if hasattr(run.status, "value")
                    else str(run.status)
                )
                in _TERMINAL_STATUSES
            ):
                return _run_to_status_dict(run)
            await asyncio.sleep(2.0)
        return None

    try:
        redis = RedisServiceSingleton()
        pubsub = await redis.get_subscriber(session_id)
    except Exception as e:
        logger.warning(
            "await_terminal: Redis subscribe failed (%s), falling back to DB polling", e
        )
        return await _poll_db()

    try:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=2.0
            )
            if message is None:
                continue
            try:
                payload = json.loads(message.get("data") or "")
            except (TypeError, json.JSONDecodeError):
                continue
            event_type = payload.get("event_type")
            if event_type == "input_issue":
                _forward_input_issue(payload, on_input_issue)
                continue
            if event_type == "termination":
                # The run reached a terminal state - load the run record for
                # the canonical status + outputs.
                factory = async_session_factory(use_fresh_engine=False)
                async with factory() as session:
                    stmt = select(WorkflowRun).where(
                        WorkflowRun.session_id == session_id
                    )
                    run = (await session.execute(stmt)).scalar_one_or_none()
                if run:
                    return _run_to_status_dict(run)
                return {"status": "terminated"}
        return None
    finally:
        try:
            await pubsub.aclose()
        except Exception:
            pass


async def _call_lifecycle_endpoint(
    node,
    run_id: str,
    endpoint: str,
    *,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dispatch a lifecycle control (stop / pause / resume) IN-PROCESS.

    Historically these were POSTed to an in-cluster
    ``http://localhost:{PORT}/workflow/.../{endpoint}`` route.  The chat
    orchestrator runs inside the A2A gateway process, which in the
    OneEdge / A2A deployment topology is NOT co-located with the workflow
    router, so that hop failed with "All connection attempts failed" and
    the run was never actually stopped -- the user typed "stop", got an
    error, and the run kept going (ENG-3677).

    Dispatch through ``dispatch_lifecycle_action`` -- the same shared
    driver the A2A skill executor (``_lifecycle_signal``) uses -- so the
    chat-tool and typed-skill lifecycle paths run identical code.  The v1
    handlers own the ``USE_TEMPORAL`` branch, already-terminal PG
    reconciliation, and (for resume) node-access / connection /
    signal-size validation, so this wrapper must not reimplement any of
    it.
    """

    from api.v1.handlers.run import dispatch_lifecycle_action

    action = {
        "stop-workflow": "stop",
        "pause-workflow": "pause",
        "resume-workflow": "resume",
    }.get(endpoint)
    if action is None:
        return {"error": f"Unknown lifecycle endpoint: {endpoint}"}

    factory = async_session_factory(use_fresh_engine=False)
    async with factory() as session:
        run, denied = await _load_run_for_node(session, run_id, node, "run")
    if denied is not None:
        return denied
    if run is None:
        return {"error": f"Run {run_id} not found"}

    try:
        result = await dispatch_lifecycle_action(
            action=action,
            org_id=node._orgid,
            workflow_id=run.workflow_id,
            session_id=run.session_id,
            user_id=getattr(node, "_user_id", "") or "",
            user_name=getattr(node, "_user_email", "") or "",
            extra_payload=body,
        )
        return {"success": True, "body": result}
    except Exception as e:
        # The v1 handlers raise HTTPException on validation / auth failures
        # (e.g. an edited resume payload that fails node-access or size
        # checks); surface the clean detail to the agent rather than a repr.
        detail = getattr(e, "detail", None)
        logger.exception("Lifecycle %s failed in-process: %s", endpoint, e)
        return {"error": f"{endpoint} failed: {detail or e}"}


# Legacy Postgres ``workflow_event.event_type`` strings that mark a node
# failure.  Mirrors ``_PG_LEGACY_TO_V1`` in api/a2a_gateway/event_adapter.py
# (both map onto the frozen v1 ``node.node_failed`` wire type) -- the chat
# recovery tool and the A2A events surface must agree on what "the run
# failed at node X" means.
_NODE_FAILED_EVENT_TYPES = ("node_failed", "execution_failed")


async def _find_failed_node(session, run) -> Optional[Dict[str, Any]]:
    """Find the node a run failed at, from its persisted event log.

    Returns ``{node_id, event_type, ts, error_message?}`` for the LAST
    node-failure event on the run (a run that failed, was recovered
    mid-graph, and failed again should recover from the most recent
    failure point), or ``None`` when the run has no node-failure event.
    """

    stmt = (
        select(WorkflowEvent)
        .where(
            WorkflowEvent.run_id == run.id,
            WorkflowEvent.event_type.in_(_NODE_FAILED_EVENT_TYPES),
        )
        .order_by(WorkflowEvent.timestamp.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        if not row.node_instance_id:
            continue
        data = row.event_data if isinstance(row.event_data, dict) else {}
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        out: Dict[str, Any] = {
            "node_id": str(row.node_instance_id),
            "event_type": row.event_type,
            "ts": row.timestamp.isoformat() if row.timestamp else None,
        }
        error_message = (
            data.get("error_message")
            or inner.get("error_message")
            or data.get("dynamic_message")
        )
        if error_message:
            out["error_message"] = error_message
        return out
    return None


def _resolve_run_evals(node, requested: Optional[bool]) -> bool:
    """ENG-5459: the evals toggle a run started from this chat should use.

    An explicit per-run request wins; otherwise the agent's
    ``run_evals_by_default`` config decides (absent -> ``True``, i.e. eval
    nodes run -- the pre-toggle behaviour). Shared by ``start_workflow``
    and every rerun tool so a rerun inherits the same default a fresh run
    would.
    """
    if requested is not None:
        return bool(requested)
    return bool(
        (getattr(node, "agent_record", None) or {}).get("run_evals_by_default", True)
    )


def _non_terminal_source_run_error(run: Any, run_id: str) -> Optional[Dict[str, Any]]:
    """``{"error": ...}`` when ``run`` has a known, non-terminal status.

    A rerun's cache seed is only complete once the source run is terminal
    (``TERMINAL_RUN_STATUSES``); a running / queued / paused source would
    seed a partial cache and re-execute its unfinished nodes in parallel
    with the live run. The predicate is ``non_terminal_run_status`` --
    the same one ``RerunFromNodeHandler`` enforces (409) for every caller;
    this pre-check only turns it into the tool-result shape the model
    expects. Returns ``None`` for terminal runs and for stubs that carry
    no status at all.
    """
    from api.v1.handlers.run import non_terminal_run_status

    status = non_terminal_run_status(run)
    if status is None:
        return None
    return {
        "error": (
            f"Source run {run_id} is still '{status}' -- a rerun can only seed "
            "from a run that has finished (success / failed / stopped / "
            "cancelled). Wait for it to complete (get_workflow_run_status), "
            "then call this tool again."
        ),
        "status": status,
        "source_run_id": str(getattr(run, "id", "") or run_id),
    }


async def _rerun_in_process(
    node,
    run_id: str,
    starting_node_ids: List[str],
    payload: Optional[Dict[str, Any]],
    single_step: bool = False,
    run_evals: Optional[bool] = None,
) -> Dict[str, Any]:
    """Start a rerun by calling ``RerunFromNodeHandler`` directly, in the
    same process -- mirroring the A2A ``rerun_from_node`` skill handler
    (``executor.py::_handle_rerun_from_node``).

    The chat orchestrator runs inside the gateway process, so unlike the
    other lifecycle tools (which POST to a ``localhost`` HTTP endpoint that
    isn't reachable in every deployment topology), the rerun is dispatched
    through the handler/Temporal layer directly.  This is what makes the
    chat-driven single-step rerun work end to end on staging.

    ``run_evals`` (ENG-5459): ``None`` inherits the agent's
    ``run_evals_by_default``; the handler then forces evals ON when a
    starting node is an Evaluations node (or the ``evals_node`` alias).

    Returns a dict shaped like the rerun response, including the new
    ``run_id`` so the agent can read the corrected output back via
    ``get_workflow_output``.
    """

    import uuid as _uuid

    from api.routers.models.advanced_workflow import AdvancedWorkflowPayload
    from api.v1.handlers.run import RerunFromNodeHandler
    from api.v1.schemas.run import RerunFromNodeRequest
    from db.models.workflow_version import WorkflowVersion as WorkflowVersionModel

    factory = async_session_factory(use_fresh_engine=False)
    try:
        async with factory() as session:
            run, denied = await _load_run_for_node(session, run_id, node, "run")
            if denied is not None:
                return denied
            if run is None:
                return {"error": f"Source run {run_id} not found"}

            # ENG-5459: a rerun seeds ``node_result`` rows the source run has
            # ALREADY produced and re-executes everything else. Against a
            # run that is still running / paused that means a partial cache
            # plus a second, concurrent execution of the unfinished upstream
            # nodes -- the exact spend the evals-on-demand path exists to
            # avoid. Refuse so the model waits for the source to finish
            # instead (the prompt tells it to poll ``get_workflow_run_status``).
            non_terminal = _non_terminal_source_run_error(run, run_id)
            if non_terminal is not None:
                return non_terminal

            # Inherit the source run's stored version payload (mint a fresh
            # session_id) unless the caller supplied a full payload override.
            raw_payload: Dict[str, Any] = dict(payload) if payload else {}
            if not raw_payload.get("session_id"):
                if not raw_payload:
                    version = (
                        await session.execute(
                            select(WorkflowVersionModel).where(
                                WorkflowVersionModel.id == run.version_id
                            )
                        )
                    ).scalar_one_or_none()
                    base = (version.payload if version else None) or {}
                    if isinstance(base, dict):
                        raw_payload = dict(base)
                raw_payload["session_id"] = str(_uuid.uuid4())

            try:
                payload_obj = AdvancedWorkflowPayload.model_validate(raw_payload)
            except Exception as e:
                return {
                    "error": (
                        "Source run's stored payload could not be parsed as an "
                        f"AdvancedWorkflowPayload: {e}"
                    )
                }

            body = RerunFromNodeRequest(
                source_session_id=run.session_id,
                starting_node_ids=list(starting_node_ids),
                payload=payload_obj,
                single_step=single_step,
                run_evals=_resolve_run_evals(node, run_evals),
            )
            handler = RerunFromNodeHandler(session)
            result_obj = await handler.handle(
                org_id=node._orgid,
                workflow_id=run.workflow_id,
                body=body,
                current_user={
                    "user_id": getattr(node, "_user_id", "") or "",
                    "email": getattr(node, "_user_email", "") or "",
                    "token": getattr(node, "_user_token", None),
                    "name": getattr(node, "_user_name", "") or "",
                },
                # ENG-2486: without this a chat-driven rerun of an EY workflow
                # would silently drop Token C and fall back to the bearer path.
                # ``None`` for non-Entra callers (the gateway never stashed a
                # session on the context) -> unchanged bearer behaviour.
                obo_session=getattr(node, "obo_session", None),
            )
        result = (
            result_obj.model_dump()
            if hasattr(result_obj, "model_dump")
            else (
                result_obj if isinstance(result_obj, dict) else {"raw": str(result_obj)}
            )
        )
        out: Dict[str, Any] = {
            "source_run_id": str(run.id),
            "workflow_id": str(run.workflow_id),
            "start_node_id": starting_node_ids[0] if starting_node_ids else None,
            "single_step": single_step,
            "run_id": str(result.get("run_id") or ""),
            "session_id": str(result.get("session_id") or ""),
            "result": result,
        }
        if isinstance(result.get("run_evals"), bool):
            # ENG-5459: the toggle the new run EXECUTES with. Forced on by the
            # handler when a starting node is an Evaluations node, so it can
            # differ from the agent default / request -- say so, never
            # silently.
            out["run_evals"] = result["run_evals"]
            if not result["run_evals"]:
                out["evals"] = "skipped"
        return out
    except Exception as e:
        logger.exception("rerun_step_in_context failed: %s", e)
        return {"error": f"rerun_step_in_context failed: {e}"}


# =========================================================================
# Input-translation helpers for start_workflow (ENG-2469)
# =========================================================================


def _is_uuidish(s: Any) -> bool:
    """Cheap test: does this look like a bare file_id we should resolve?"""
    if not isinstance(s, str):
        return False
    try:
        uuid.UUID(s)
        return True
    except (TypeError, ValueError):
        return False


# Redis TTL for the A2A artifact lookup cache (1 week). Must match the
# write-side TTL in ``executor._persist_uploaded_blob``
# (``_ARTIFACT_LOOKUP_TTL_SECONDS``); the lookup slides this forward on every
# hit so files stay reachable for the lifetime of an active conversation
# instead of expiring on a fixed clock.
_A2A_ARTIFACT_TTL_SECONDS: int = 7 * 24 * 60 * 60


def _resolve_artifact_for_org(org_id: str):
    """Return an async function that resolves a file_id to its artifact
    metadata, mirroring the A2A gateway's lookup chain
    (``executor._resolve_file_reference_to_envelope``):

      (a) the Redis artifact cache (``upload_file`` /
          ``request_upload_url`` registrations plus orchestrator-created/
          edited artifacts, org-namespaced key), then
      (b) the ``node_result_file`` DB table (files PRODUCED by a prior
          workflow run), org-scoped via the file's run -> workflow.org_id
          (ENG-3266), then
      (c) the promoted ``Artifact`` row keyed by the chat file_id
          (``source_orchestrator_file_id`` / ``source_node_result_file_id``,
          org-scoped). Orchestrator-created/edited artifacts only live in
          Redis for 7 days; promotion (#1115) persists their S3
          coordinates + lineage durably, so without this hop their
          file_ids die with the registry entry.

    Before the (b) fallback, a produced file's id from
    ``get_workflow_output`` missed the Redis cache and was passed through
    unwrapped, so chaining "run A -> feed A's output file into run B" via
    the chat orchestrator failed at every File Input node even though the
    same id worked on the deterministic ``call_workflow`` skill.

    Precedence is fixed: when a file_id matches both a
    ``node_result_file`` row and an Artifact row, the ``node_result_file``
    resolution wins (it is the producing run's own record).

    Lifted out as a factory so unit tests can substitute a stub.
    """

    async def _resolve(file_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = RedisServiceSingleton()._get_client()  # noqa: SLF001
            key = f"agentic_backend_a2a:artifact:{org_id}:{file_id}"
            raw = await client.get(key)
            if raw is not None:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                # Slide the TTL forward so a file referenced across turns of a
                # long-running conversation doesn't expire mid-thread. Must
                # match the write-side TTL in ``executor._persist_uploaded_blob``.
                # Isolated try/except so an expire failure can never discard an
                # otherwise-successful lookup (which would resurface the file as
                # "truncated/invalid").
                try:
                    await client.expire(key, _A2A_ARTIFACT_TTL_SECONDS)
                except Exception as exc:
                    logger.warning(
                        "_resolve_artifact_for_org(%s): TTL refresh failed: "
                        "%s -- resolved OK but expiry not extended",
                        file_id,
                        exc,
                    )
                return parsed
        except Exception as e:
            logger.warning(
                "_resolve_artifact_for_org(%s): Redis artifact lookup "
                "raised: %s -- falling through to node_result_file",
                file_id,
                e,
            )

        # [VÁ 10b chay_thu 06/11/2026 — A; đã kiểm S4 M2] chat Operator dùng hàm này để đưa mã file do MỘT LƯỢT CHẠY tạo ra làm đầu vào cho
        # workflow KHÁC (start_workflow :1795) ⇒ dữ liệu từ khối chạy code đi vòng sang workflow có khối gửi ra ngoài, R12 không thấy.
        # Bản tối thiểu cho máy thử (cùng công tắc d2): không phân giải file do lượt chạy tạo (b) và artifact đã thăng hạng (c); file người dùng
        # tải lên (a, Redis) vẫn dùng được. Tắt: CHAY_THU_R12_OPERATOR=false.
        if os.environ.get("CHAY_THU_R12_OPERATOR", "true").strip().lower() not in ("false", "0", "no", "off"):
            logger.info("[R12_OPERATOR] tu_choi_file_luot_chay file_id=%s (chỉ nhận file người dùng tải lên)", file_id)
            return None
        try:
            produced = await _resolve_produced_file_for_org(org_id, file_id)
            if produced is not None:
                return produced
        except Exception as e:
            logger.warning(
                "_resolve_artifact_for_org(%s): node_result_file lookup "
                "raised: %s -- falling through to the promoted Artifact row",
                file_id,
                e,
            )

        # (c) Promoted Artifact row: orchestrator-created/edited artifacts
        # whose Redis registry entry has expired still resolve through
        # their promotion (S3 coords + lineage persisted by #1115). The
        # helper is org-scoped and never raises.
        from services.artifacts.lookup import resolve_promoted_artifact_for_org

        resolved = await resolve_promoted_artifact_for_org(org_id, file_id)
        if resolved is None:
            logger.info(
                "_resolve_artifact_for_org(%s): unresolved in registry, "
                "node_result_file, and Artifact rows -- file will be passed "
                "through as a bare file_id which the file_input_node "
                "validator will reject",
                file_id,
            )
        return resolved

    return _resolve


# ENG-3391: bound get_workflow_output so a single tool-return can never blow
# the orchestrator's 1M context window.
#
# Per-string preview budget (characters) for a text output field.
_NODE_OUTPUT_PREVIEW_CHARS = 2000
# Hard ceiling (characters) on the whole assembled get_workflow_output payload.
# ~4 chars/token => ~120K chars ≈ 30K tokens: far under the 1M window with room
# for the rest of the conversation. A char-count heuristic (not a live
# count_tokens call) is deliberate -- it is a synchronous backstop with no model
# round-trip; it only has to be conservative, not exact.
_WORKFLOW_OUTPUT_CHAR_CAP = 120_000
# Most-recent events returned by a get_workflow_events replay (filtered or
# not). A status poll only needs recent activity; the full log is re-sent
# verbatim into every subsequent chat turn otherwise. Small filtered results
# (e.g. the HITL node.input_required check) fall well under this and come back
# complete.
_EVENTS_REPLAY_WINDOW = 60
# v1 event types that are few-per-run and semantically critical (the
# orchestrator detects mid-run input from these). A get_workflow_events query
# scoped to ONLY these is returned complete -- never windowed -- so an open
# checkpoint on a run with many checkpoints is never hidden.
_UNWINDOWED_EVENT_TYPES = {"node.input_required", "workflow.run_paused"}
# Dict keys whose STRING values are references that must round-trip verbatim --
# never truncated to a preview. Presigned S3/STS download URLs in particular
# routinely exceed _NODE_OUTPUT_PREVIEW_CHARS, and a truncated URL is a dead
# link (the exact regression Bugbot flagged on the first cut of this fix).
_PRESERVE_STRING_KEYS = frozenset(
    {
        "download_url",
        "presigned_url",
        "signed_url",
        "url",
        "s3_url",
        "s3_key",
        "file_id",
        "node_id",
        "field_id",
        "mime_type",
        "filename",
        "name",
    }
)


def _preview_output_data(od: Any, _key: Optional[str] = None) -> Any:
    """Return a context-safe copy of a node's output_data (ENG-3391).

    Long text strings (generated report text, tables, analysis) are truncated
    to a bounded preview carrying a pointer to ``get_node_output``. Reference
    strings are preserved verbatim so file delivery keeps working: a value is
    preserved when its key is a known reference field OR the value itself is a
    URL (any length). Recurses into dicts/lists. Never mutates the input.
    """
    if isinstance(od, str):
        # Preserve references/URLs regardless of length -- a truncated
        # presigned URL is unusable.
        if _key in _PRESERVE_STRING_KEYS or od.startswith(("http://", "https://")):
            return od
        if len(od) > _NODE_OUTPUT_PREVIEW_CHARS:
            dropped = len(od) - _NODE_OUTPUT_PREVIEW_CHARS
            return od[:_NODE_OUTPUT_PREVIEW_CHARS] + (
                f"... [truncated {dropped} chars -- fetch the full value with "
                "get_node_output(run_id, node_id)]"
            )
        return od
    if isinstance(od, dict):
        return {k: _preview_output_data(v, _key=k) for k, v in od.items()}
    if isinstance(od, list):
        # Propagate the parent key so e.g. a list of URLs under "urls" is
        # still recognised as references.
        return [_preview_output_data(v, _key=_key) for v in od]
    return od


def _collapse_duplicate_file_rows(
    pairs: Sequence[tuple], is_primary: Callable[[Any, Any], bool]
) -> List[tuple]:
    """Collapse ``(row, node_id)`` pairs that point at the SAME stored object.

    ENG-4553. A file routed through a File Browser node is persisted TWICE:
    the producing node writes its own ``node_result_file`` row, and the
    browser writes a copy with a fresh row id but identical S3 metadata
    (``nodes/output/file/node.py::FileBrowserNode._copy_node_file``, whose
    docstring states the fresh id is deliberate -- it avoids a primary-key
    collision during persistence). Deduping on the row id alone therefore
    never collapses the pair, and the file renders as two download cards.

    Identity is ``(s3_key, name)`` -- deliberately the SAME tuple as the two
    existing definitions, so the codebase carries one convention with three
    uses rather than three spellings: the consumer contract documented in
    ``temporal/activities/persist_results.py`` and the cross-field dedupe in
    ``nodes/agent/node.py::_extract_input_files``. The bucket is NOT part of
    the identity: a single run's generated files all land in that run's
    bucket, and the cross-region re-hosted copies that share a key across
    buckets are workflow INPUTS (``generated=False``), which this resolver's
    query already excludes.

    Winner per group: a row whose node carries a primary-output designation,
    so a tagged deliverable is never demoted by an untagged duplicate -- and
    a designation on an AGGREGATOR node is honoured, the file being surfaced
    once, as primary. Ties break on the lowest row id so the result never
    depends on database row order. Rows with no ``s3_key`` are dropped,
    preserving the pre-existing behaviour.

    MUST run BEFORE the primary/other partition: ``show_output(scope="all")``
    is ``primary + other``, so a within-partition dedupe would still
    double-render a file whose two copies straddle the split.
    """
    winners: Dict[tuple, tuple] = {}
    order: List[tuple] = []
    for row, node_id in pairs:
        if not row.s3_key:
            continue
        key = (row.s3_key, row.name)
        rank = (0 if is_primary(row, node_id) else 1, str(row.id))
        current = winners.get(key)
        if current is None:
            winners[key] = (rank, row, node_id)
            order.append(key)
        elif rank < current[0]:
            winners[key] = (rank, row, node_id)
    return [(winners[k][1], winners[k][2]) for k in order]


async def _resolve_run_output_files(
    run_id: str, org_id: str
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolve a run's produced files, split into primary vs other outputs.

    Deterministic completion-return helper: on terminal SUCCESS the
    orchestrator must hand the caller the run's download link(s) WITHOUT the
    LLM reading the (often 500k-token) ``get_workflow_output`` payload to
    decide what to return. This walks the ``node_result_file`` rows the run
    produced -- joined ``node_result -> workflow_run -> workflow`` so a file
    from another org never leaks -- and presigns each, yielding the same
    ``download_url``-bearing shape ``_extract_artifacts`` surfaces to the A2A
    wire.

    Only ``generated`` files are returned. File-input nodes persist their
    UPLOADED files as ``node_result_file`` rows too (``generated=False``);
    without this filter a workflow that ingests an upload and then produces a
    report would list the caller's own upload as a deliverable. This mirrors
    the input-echo split ``get_workflow_output`` already does at the node
    level -- every producing node writes ``generated=True`` (the column
    defaults to ``False``), so the flag is the authoritative produced-vs-input
    discriminator.

    Args:
        run_id: The run whose produced files to resolve.
        org_id: Tenancy scope; only files of a workflow in this org resolve.

    Returns:
        ``(primary, other)`` -- two lists of artifact dicts (``file_id`` /
        ``filename`` / ``mime_type`` / ``s3_key`` / ``node_id`` /
        ``download_url`` / ``source``). ``primary`` is the marker-selected
        deliverable file(s) shown by default; ``other`` is every generated file
        the output marker suppressed, surfaced only on request (ENG-3566). When
        no marker is configured everything is ``primary`` and ``other`` is
        empty. Both empty when the run produced no files or on any error -- the
        caller then degrades to "completed, no downloadable files".

        Each stored object appears AT MOST ONCE across the two lists: rows
        pointing at the same ``(s3_key, name)`` are collapsed by
        ``_collapse_duplicate_file_rows`` before the split (ENG-4553).
    """
    try:
        run_uuid = uuid.UUID(str(run_id))
    except (TypeError, ValueError):
        return [], []

    from api.a2a_gateway.a2a_output_allowlist import resolve_allowed_node_ids
    from db.models.node_result import NodeResult
    from db.models.node_result_file import NodeResultFile

    try:
        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            # Resolve this run's workflow + version so the EY output allowlist
            # (keyed on the stable ``node_instance_id``) can be applied. The
            # per-org leak guard below is unchanged.
            wf_row = (
                await session.execute(
                    select(WorkflowRun.workflow_id, WorkflowRun.version_id).where(
                        WorkflowRun.id == run_uuid
                    )
                )
            ).first()
            allowed_ids = (
                await resolve_allowed_node_ids(
                    session, wf_row.workflow_id, wf_row.version_id
                )
                if wf_row
                else None
            )

            # Select every generated file plus its producing ``node_id``. The
            # query is NOT restricted to the allowlisted nodes: both the primary
            # (deliverable) files AND the marker-suppressed ("other") files are
            # needed -- primary for the default response, other for on-request
            # exploration (ENG-3566). Partitioning happens in Python below so a
            # single query covers both.
            stmt = (
                select(NodeResultFile, NodeResult.node_id)
                .join(NodeResult, NodeResult.id == NodeResultFile.node_result_id)
                .join(WorkflowRun, WorkflowRun.id == NodeResult.run_id)
                .join(WorkflowModel, WorkflowModel.id == WorkflowRun.workflow_id)
                .where(
                    NodeResult.run_id == run_uuid,
                    WorkflowModel.org_id == org_id,
                    NodeResultFile.generated.is_(True),
                )
            )
            result_rows = (await session.execute(stmt)).all()
    except Exception as e:  # noqa: BLE001 - convenience layer, never fatal
        logger.warning("_resolve_run_output_files(%s): query failed: %s", run_id, e)
        return [], []

    from api.a2a_gateway.a2a_output_allowlist import is_primary_file
    from core.settings import settings

    def _is_primary(f, node_id) -> bool:
        return is_primary_file(allowed_ids, node_id, f.name)

    def _mk_artifacts(pairs, *, primary_flag: bool) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for row, node_id in pairs:
            # Input is already collapsed by ``_collapse_duplicate_file_rows``,
            # which owns the storage-identity dedupe; the key guard stays as a
            # cheap defence against an undownloadable row.
            if not row.s3_key:
                continue
            download_url = ""
            try:
                # ENG-3528: durable click-time link (HMAC /s3/proxy, 7 days),
                # not a presigned URL that dies with the signing pod's STS
                # session while advertising a longer expiry.
                download_url = row.generate_durable_url()
            except Exception as e:  # noqa: BLE001 - metadata still usable without it
                logger.warning(
                    "_resolve_run_output_files(%s): durable-link mint failed for %s: %s",
                    run_id,
                    row.id,
                    e,
                )
            out.append(
                {
                    "file_id": str(row.id),
                    "filename": row.name,
                    "mime_type": row.mime_type,
                    "size_bytes": None,
                    "s3_key": row.s3_key,
                    "s3_bucket": settings.resolve_read_bucket(
                        getattr(row, "s3_bucket", None), row.generated
                    ),
                    "s3_region": row.s3_region,
                    "org_id": org_id,
                    "node_id": str(node_id) if node_id else None,
                    "download_url": download_url,
                    "source": "node_result_file",
                    # ENG-3826: per-file primary-output designation, so every
                    # surface built from these dicts (files_available events,
                    # show_output frames, get_workflow_output) carries the
                    # same "deliverable vs working file" verdict.
                    "primary": primary_flag,
                }
            )
        return out

    # ENG-4553: one entry per stored object, collapsed BEFORE the partition.
    # Rationale (and why not per-partition) in the helper's docstring.
    deduped = _collapse_duplicate_file_rows(result_rows, _is_primary)

    primary = _mk_artifacts(
        [(f, n) for f, n in deduped if _is_primary(f, n)], primary_flag=True
    )
    other = _mk_artifacts(
        [(f, n) for f, n in deduped if not _is_primary(f, n)], primary_flag=False
    )
    return primary, other


async def _resolve_produced_file_for_org(
    org_id: str, file_id: str
) -> Optional[Dict[str, Any]]:
    """Resolve a workflow-PRODUCED output file to artifact metadata (ENG-3266).

    Looks up the ``node_result_file`` row for ``file_id`` -- the id every
    produced file carries in ``get_workflow_output.outputs[*].files[].id``
    -- and returns the same artifact-dict shape the Redis cache yields, so
    ``_wrap_file_id_as_upload_response`` works identically for both
    sources.  Tenancy: the row must belong to a run of a workflow in
    ``org_id`` (joined through node_result -> workflow_run -> workflow),
    so a leaked file id from another org resolves to ``None``.
    """

    try:
        file_uuid = uuid.UUID(str(file_id))
    except (TypeError, ValueError):
        return None

    from db.models.node_result import NodeResult
    from db.models.node_result_file import NodeResultFile

    stmt = (
        select(NodeResultFile)
        .join(NodeResult, NodeResult.id == NodeResultFile.node_result_id)
        .join(WorkflowRun, WorkflowRun.id == NodeResult.run_id)
        .join(WorkflowModel, WorkflowModel.id == WorkflowRun.workflow_id)
        .where(NodeResultFile.id == file_uuid, WorkflowModel.org_id == org_id)
    )
    factory = async_session_factory(use_fresh_engine=False)
    async with factory() as session:
        row = (await session.execute(stmt)).scalar_one_or_none()

    if row is None or not row.s3_key:
        return None

    from core.settings import settings

    # Presign against the bucket the row actually lives in (produced files
    # go to the outputs bucket) so the File Input node hands downstream
    # consumers a working URL.
    download_url = ""
    try:
        download_url = row.generate_presigned_url()
    except Exception as e:  # noqa: BLE001 -- metadata still usable without it
        logger.warning(
            "_resolve_produced_file_for_org(%s): presign failed: %s", file_id, e
        )

    return {
        "filename": row.name,
        "mime_type": row.mime_type,
        "size_bytes": None,
        "s3_key": row.s3_key,
        "s3_bucket": settings.resolve_read_bucket(
            getattr(row, "s3_bucket", None), row.generated
        ),
        "s3_region": row.s3_region,
        "org_id": org_id,
        "download_url": download_url,
        "source": "node_result_file",
    }


def _wrap_file_id_as_upload_response(
    file_id: str, artifact: Dict[str, Any], org_id: str
) -> Dict[str, Any]:
    """Build a ``FileUploadResponse``-shaped dict the file_input_node accepts."""
    from services.orchestrator.questionnaire import wrap_file_id_as_upload_response

    return wrap_file_id_as_upload_response(file_id, artifact, org_id)


def _incoming_questionnaire_value(
    incoming: Dict[str, Any],
    field: Any,
    *,
    unique_bare_ids: set[str],
) -> tuple[Optional[str], Any]:
    """Return ``(caller_key, value)`` for a schema field in the inputs dict."""
    composite = f"{field.node_instance_id}.{field.field_id}"
    if composite in incoming:
        return composite, incoming[composite]
    bare = str(field.field_id)
    if bare in incoming and bare in unique_bare_ids:
        return bare, incoming[bare]
    return None, None


def _unique_bare_field_ids(fields: Sequence[Any]) -> set[str]:
    """Field ids that appear on exactly one schema row."""
    counts: Dict[str, int] = {}
    for field in fields:
        fid = str(field.field_id)
        counts[fid] = counts.get(fid, 0) + 1
    return {fid for fid, count in counts.items() if count == 1}


def _ambiguous_questionnaire_bare_message(
    fields: Sequence[Any], incoming: Dict[str, Any]
) -> Optional[str]:
    """Error when a bare File Input key cannot be mapped to one node."""
    from services.orchestrator.questionnaire import is_questionnaire_field

    by_bare: Dict[str, List[Any]] = {}
    for field in fields:
        by_bare.setdefault(str(field.field_id), []).append(field)
    for fid, matches in by_bare.items():
        if fid not in incoming or len(matches) < 2:
            continue
        if not any(is_questionnaire_field(match) for match in matches):
            continue
        candidates = ", ".join(
            f"{match.node_instance_id}.{match.field_id}" for match in matches
        )
        return (
            f"Input '{fid}' is ambiguous: {len(matches)} nodes have that "
            f"field. Pass compiled answers using a composite id from "
            f"describe_workflow: {candidates}."
        )
    return None


def _bind_questionnaire_answers(
    incoming: Dict[str, Any],
    field: Any,
    caller_key: str,
    envelope: Dict[str, Any],
) -> None:
    """Store the answers envelope under the composite id ``_translate_inputs`` applies."""
    composite = f"{field.node_instance_id}.{field.field_id}"
    incoming[composite] = envelope
    if caller_key != composite:
        incoming.pop(caller_key, None)


def _drop_unanswered_questionnaire_inputs(
    fields: List[Any],
    incoming: Dict[str, Any],
    unique_bare_ids: set[str],
) -> List[Any]:
    """Pop blank questionnaire keys and return fields that still need answers."""
    from services.orchestrator.questionnaire import unanswered_questionnaire_fields

    def lookup(field: Any) -> Any:
        _key, value = _incoming_questionnaire_value(
            incoming, field, unique_bare_ids=unique_bare_ids
        )
        return value

    missing = unanswered_questionnaire_fields(fields, lookup)
    for field in missing:
        key, _value = _incoming_questionnaire_value(
            incoming, field, unique_bare_ids=unique_bare_ids
        )
        if key is not None:
            incoming.pop(key, None)
    return missing


async def _materialize_questionnaire_inputs(
    *,
    fields: List[Any],
    incoming: Dict[str, Any],
    org_id: str,
    agent_id: str,
) -> None:
    """Rewrite questionnaire answer text to File Input blobs, or raise."""
    from services.orchestrator.questionnaire import (
        QuestionnaireInputError,
        is_questionnaire_field,
        materialize_questionnaire_answers,
        missing_questionnaire_message,
    )

    q_fields = [f for f in fields if is_questionnaire_field(f)]
    if not q_fields:
        return
    collision = _ambiguous_questionnaire_bare_message(fields, incoming)
    if collision:
        raise QuestionnaireInputError(collision)
    unique_bare_ids = _unique_bare_field_ids(fields)
    missing = _drop_unanswered_questionnaire_inputs(q_fields, incoming, unique_bare_ids)
    if missing:
        raise QuestionnaireInputError(
            missing_questionnaire_message(missing, suggest_export_tool=True)
        )
    for field in q_fields:
        key, value = _incoming_questionnaire_value(
            incoming, field, unique_bare_ids=unique_bare_ids
        )
        if key is None:
            continue
        envelope = await materialize_questionnaire_answers(
            field=field,
            value=value,
            org_id=org_id,
            agent_id=agent_id or "orchestrator",
        )
        if isinstance(envelope, dict) and envelope.get("file"):
            _bind_questionnaire_answers(incoming, field, key, envelope)


_UUID_IN_TEXT = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)

KnownFileIds = Callable[[], Awaitable[List[str]]]


def _unique_prefix_match(
    value: str,
    candidates: Sequence[str],
    min_prefix_len: int = 8,
    min_value_len: int = 30,
) -> Optional[str]:
    """Trim ``value`` one trailing char at a time until one candidate shares the prefix.

    Args:
        value: The file id the model passed, possibly garbled.
        candidates: Known-good file ids for this conversation.
        min_prefix_len: Shortest prefix allowed to count as a match.
        min_value_len: Shorter values are not treated as a mangled id.

    Returns:
        The single matching candidate, or ``None`` when ``value`` is too short,
        is itself a candidate (never swapped for a look-alike), the prefix ties
        (trimming further only widens the tie), or it drops below the floor.
    """
    probe = value.strip().lower()
    pool = {c.lower() for c in candidates}
    if len(probe) < min_value_len or probe in pool:
        return None
    for length in range(len(probe), min_prefix_len - 1, -1):
        hits = [c for c in pool if c.startswith(probe[:length])]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return None
    return None


async def _conversation_file_ids(
    org_id: str, agent_id: Optional[str], context_id: Optional[str]
) -> List[str]:
    """Return the file ids the caller sent in this conversation's user turns.

    The rolling-summary recap is skipped: it is model-written, not caller input.

    Args:
        org_id: Org owning the conversation.
        agent_id: A2A agent id; empty off the A2A path.
        context_id: A2A conversation id; empty off the A2A path.

    Returns:
        Unique ids in first-seen order; empty when there is no conversation.
    """
    if not (agent_id and context_id):
        return []
    from ai.agentic.context.rolling_summary import ROLLING_SUMMARY_MARKER
    from api.a2a_gateway import conversation_history

    turns = await conversation_history.get_history(
        org_id=org_id, agent_id=str(agent_id), context_id=str(context_id)
    )
    found: List[str] = []
    for turn in turns:
        content = str(turn.get("content") or "")
        if turn.get("role") == "user" and not content.startswith(
            ROLLING_SUMMARY_MARKER
        ):
            found.extend(_UUID_IN_TEXT.findall(content))
    return list(dict.fromkeys(found))


def _conversation_file_ids_loader(
    org_id: str, agent_id: Optional[str], context_id: Optional[str]
) -> KnownFileIds:
    """Build a memoized loader so history is read once, and only on a miss.

    Args:
        org_id: Org owning the conversation.
        agent_id: A2A agent id.
        context_id: A2A conversation id.

    Returns:
        An async callable returning :func:`_conversation_file_ids`.
    """
    cache: List[List[str]] = []

    async def load() -> List[str]:
        if not cache:
            cache.append(await _conversation_file_ids(org_id, agent_id, context_id))
        return cache[0]

    return load


async def _resolve_or_repair_file_id(
    file_id: str,
    artifact_resolver: Callable[[str], Awaitable[Optional[Dict[str, Any]]]],
    known_file_ids: Optional[KnownFileIds],
    input_key: str = "",
) -> tuple[str, Optional[Dict[str, Any]]]:
    """Resolve ``file_id``; on a miss, retry once with its unique-prefix repair.

    Args:
        file_id: The file id the model passed.
        artifact_resolver: Looks a file id up in the artifact registry.
        known_file_ids: Loader for the conversation's file ids, if any.
        input_key: The input being bound, for the repair log line.

    Returns:
        ``(id, artifact)`` for the id that resolved, else ``(file_id, None)``.
    """
    normalized = file_id.strip().lower()
    if _is_uuidish(normalized):
        artifact = await artifact_resolver(normalized)
        if artifact and artifact.get("s3_key"):
            return normalized, artifact
    if known_file_ids is None:
        return file_id, None
    repaired = _unique_prefix_match(normalized, await known_file_ids())
    if repaired is None:
        return file_id, None
    artifact = await artifact_resolver(repaired)
    if not (artifact and artifact.get("s3_key")):
        return file_id, None
    logger.warning(
        "start_workflow: repaired file_id %r -> %s for input %r (%d-char prefix match)",
        file_id[:40],
        repaired,
        input_key,
        len(os.path.commonprefix([normalized, repaired])),
    )
    return repaired, artifact


class UnresolvedFileInputError(ValueError):
    """Raised when a FILE input's file_id does not resolve to an uploaded file.

    ``start_workflow`` turns this into a tool error so the run is refused up
    front instead of failing later at the File Input node.
    """


async def _translate_inputs(
    *,
    inputs: Optional[Dict[str, Any]],
    workflow_uuid: uuid.UUID,
    org_id: str,
    artifact_resolver,
    agent_id: str = "",
    known_file_ids: Optional[KnownFileIds] = None,
):
    """Translate the LLM's flat ``{key: value}`` inputs dict into the
    ``InputNodeOverride[]`` shape ``TriggerRunRequest.inputs`` expects.

    Key formats accepted:
      * ``"<node_instance_id>.<field_id>"`` -- canonical
      * ``"<field_id>"`` -- only unambiguous when exactly one node has it

    For FILE-typed fields whose value is a bare ``file_id`` UUID (the
    most common LLM mistake), we look the file up via the two-source
    chain in ``_resolve_artifact_for_org`` (A2A Redis artifact cache,
    then org-scoped ``node_result_file`` rows for workflow-produced
    files -- ENG-3266) and auto-wrap it into ``FileUploadResponse``
    shape. An id that misses is repaired against ``known_file_ids`` (the
    caller's own ids) by unique prefix before the run is refused.

    Returns an ``(overrides, applied_keys, ignored, resolved_files)``
    tuple (ENG-2721, ENG-3153):

      * ``overrides`` -- ``InputNodeOverride[]`` (or ``None`` when
        ``inputs`` is falsy and the caller wants the stored defaults).
      * ``applied_keys`` -- caller keys that bound to a field.
      * ``ignored`` -- ``[{key, reason}]`` for keys that did NOT bind:
        unknown ids, author-locked (hidden / input-bound) params -- which
        the input schema simply doesn't list -- and bare field_ids that
        are ambiguous across nodes.  Ambiguous bare keys are REFUSED
        rather than bound to an arbitrary node, so a prompt override can
        never silently land on the wrong agent node.
      * ``resolved_files`` -- ``[{key, file_id, filename}]`` for every
        FILE-typed value that was auto-wrapped from a bare ``file_id``
        (ENG-3153).  Surfaced on the ``start_workflow`` result so the
        chat LLM learns each file's human-readable name and can stop
        referring to files by their artifact UUIDs.

    Raises:
        UnresolvedFileInputError: One or more FILE inputs were given a bare
            ``file_id`` that resolves to no uploaded file; the run must not
            start. Raised once, naming every such input.
    """
    from api.v1.handlers.workflow import (
        LOCKED_OVERRIDE_FIELD_IDS,
        GetWorkflowInputsHandler,
    )
    from api.v1.schemas.trigger import FieldOverride, InputNodeOverride

    schema_factory = async_session_factory(use_fresh_engine=False)
    async with schema_factory() as session:
        schema = await GetWorkflowInputsHandler(session).handle(
            org_id=org_id,
            workflow_id=workflow_uuid,
            version_id=None,
            classify_questionnaires=False,
        )

    from services.orchestrator.questionnaire import (
        enrich_questionnaire_fields,
        is_questionnaire_field,
        should_probe_file_default,
    )

    fields = list(schema.inputs)
    if any(should_probe_file_default(f) for f in fields):
        await enrich_questionnaire_fields(fields)

    incoming: Dict[str, Any] = dict(inputs or {})
    await _materialize_questionnaire_inputs(
        fields=fields,
        incoming=incoming,
        org_id=org_id,
        agent_id=agent_id,
    )
    if not incoming:
        return None, [], [], []

    by_composite: Dict[str, Any] = {}
    by_bare: Dict[str, List[Any]] = {}
    for f in schema.inputs:
        by_composite[f"{f.node_instance_id}.{f.field_id}"] = f
        by_bare.setdefault(f.field_id, []).append(f)

    applied_keys: List[str] = []
    ignored: List[Dict[str, str]] = []
    resolved_files: List[Dict[str, Any]] = []
    unresolved_files: List[Any] = []
    overrides_by_node: Dict[str, list] = {}
    for raw_key, raw_value in incoming.items():
        field_meta = by_composite.get(raw_key)
        if field_meta is None:
            bare_matches = by_bare.get(raw_key) or []
            if len(bare_matches) == 1:
                field_meta = bare_matches[0]
            elif len(bare_matches) > 1:
                candidates = ", ".join(
                    f"{m.node_instance_id}.{m.field_id}" for m in bare_matches
                )
                ignored.append(
                    {
                        "key": raw_key,
                        "reason": (
                            f"ambiguous: {len(bare_matches)} nodes have a "
                            f"'{raw_key}' field -- use one of the composite "
                            f"ids: {candidates}"
                        ),
                    }
                )
                continue
        if field_meta is None:
            # Distinguish a policy-locked field (system_prompt / model /
            # temperature) from a genuinely unknown id, so the orchestrator
            # can tell the user *why* it can't apply it rather than implying
            # a typo (OneEdge report 2026-06-16, Theme 1 guardrails).
            bare_field_id = raw_key.rsplit(".", 1)[-1]
            if bare_field_id == "system_prompt":
                # Called out separately from model/temperature: this is the
                # agent's IDENTITY. The generic wording used to add "only
                # task/user prompts ... are overridable", which the orchestrator
                # read as an invitation to re-route the persona change into a
                # task field -- exactly the workaround we now refuse.
                reason = (
                    "'system_prompt' is the agent's author-owned persona. It "
                    "cannot be overridden per-run, and its content must NOT be "
                    "re-routed into a task/user prompt or any other field. "
                    "Tell the user the agent's role cannot be changed; do not "
                    "offer a workaround."
                )
            elif bare_field_id in LOCKED_OVERRIDE_FIELD_IDS:
                reason = (
                    f"'{bare_field_id}' is fixed by the workflow and cannot "
                    "be overridden per-run. Only task/user prompts, input "
                    "fields, and input files are overridable."
                )
            else:
                reason = (
                    "no overridable field with this id (check "
                    "describe_workflow.fields[].id; author-locked "
                    "parameters are not overridable)"
                )
            logger.warning(
                "start_workflow: caller-supplied input key %r does not "
                "match any overridable field on workflow %s; dropping (%s)",
                raw_key,
                workflow_uuid,
                reason,
            )
            ignored.append({"key": raw_key, "reason": reason})
            continue
        applied_keys.append(raw_key)

        valid_types = [str(t).upper() for t in field_meta.valid_types or []]
        is_file = "FILE" in valid_types
        value: Any = raw_value

        if is_file:
            file_id_candidate: Optional[str] = None
            if isinstance(raw_value, str) and _is_uuidish(raw_value.strip()):
                file_id_candidate = raw_value
            elif isinstance(raw_value, dict):
                if "file" in raw_value and isinstance(raw_value["file"], dict):
                    if raw_value["file"].get("file_id") and raw_value["file"].get(
                        "s3_key"
                    ):
                        value = raw_value
                        file_id_candidate = None
                        # Full FileUploadResponse inputs bind a file to this
                        # launch just as auto-wrapped bare file_ids do -- they
                        # must enter resolved_files too, or the input-decision
                        # scope check (_covers_this_launch) sees no launch
                        # filenames and treats an approval for a DIFFERENT
                        # file as covering this run.
                        resolved_files.append(
                            {
                                "key": raw_key,
                                "file_id": str(raw_value["file"].get("file_id") or ""),
                                "filename": str(
                                    raw_value["file"].get("filename")
                                    or raw_value["file"].get("original_filename")
                                    or ""
                                ),
                            }
                        )
                if file_id_candidate is None and not (
                    isinstance(raw_value.get("file"), dict)
                    and raw_value["file"].get("s3_key")
                ):
                    inner = raw_value.get("file_id") or (
                        raw_value.get("file") or {}
                    ).get("file_id")
                    if inner and _is_uuidish(inner):
                        file_id_candidate = str(inner)

            # Non-UUID strings (e.g. a dropped char) get a repair attempt too.
            probe = file_id_candidate
            if (
                probe is None
                and isinstance(raw_value, str)
                and raw_value.strip()
                and not is_questionnaire_field(field_meta)
            ):
                probe = raw_value

            if probe:
                file_id, artifact = await _resolve_or_repair_file_id(
                    probe, artifact_resolver, known_file_ids, input_key=raw_key
                )
                if artifact:
                    value = _wrap_file_id_as_upload_response(file_id, artifact, org_id)
                    resolved_files.append(
                        {
                            "key": raw_key,
                            "file_id": file_id,
                            "filename": artifact.get("filename") or "",
                        }
                    )
                    logger.info(
                        "start_workflow: auto-wrapped file_id %s for field "
                        "%s (filename=%s, size=%s B)",
                        file_id[:12],
                        field_meta.field_id,
                        artifact.get("filename"),
                        artifact.get("size_bytes"),
                    )
                elif file_id_candidate:
                    logger.warning(
                        "start_workflow: file_id %s for input %r resolved through "
                        "none of the artifact registry hops (Redis cache, "
                        "node_result_file, promoted Artifact); refusing to start "
                        "the run",
                        file_id_candidate[:12],
                        raw_key,
                    )
                    unresolved_files.append(field_meta)
                    continue

        overrides_by_node.setdefault(field_meta.node_instance_id, []).append(
            FieldOverride(
                field_id=field_meta.field_id,
                type=valid_types[0] if valid_types else None,
                current_value=value,
                required=bool(field_meta.required),
            )
        )

    if unresolved_files:
        from services.orchestrator.questionnaire import _questionnaire_field_names

        raise UnresolvedFileInputError(
            "These file inputs could not be resolved to an uploaded file "
            f"(the upload may have expired or never completed): "
            f"{_questionnaire_field_names(unresolved_files)}. Ask the user to "
            "re-upload them, then retry."
        )

    overrides = [
        InputNodeOverride(
            node_instance_id=node_id,
            node_type=None,
            fields=fields,
        )
        for node_id, fields in overrides_by_node.items()
    ]
    return overrides or None, applied_keys, ignored, resolved_files
