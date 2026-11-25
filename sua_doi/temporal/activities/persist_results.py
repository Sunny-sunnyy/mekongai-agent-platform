"""Persistence activities for AgenticWorkflow.

Three activity entry points live here:

* :func:`persist_one_node_result_activity` -- write a single node's
  result row as soon as the node completes.  The workflow calls this
  after each node inside its per-level dispatch loop so the FE sees
  progress incrementally and pause/resume state is durable even
  without an open WebSocket.

* :func:`mark_run_paused_activity` /
  :func:`mark_run_resumed_activity` -- flip ``WorkflowRun.status``
  between ``RUNNING`` and ``PAUSED`` atomically with the pause_state
  snapshot.

* :func:`persist_results_activity` -- the end-of-run finalise.  Writes
  the run-level status, the error message aggregate, and emits the
  terminal WebSocket events.  Also calls ``persist_node_results``
  once more as a safety net (``create_from_execution`` is now
  idempotent, so re-writing rows the incremental persist already
  wrote is a no-op).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sentry_sdk import capture_exception
from temporalio import activity

from temporal.models.node_io import (
    ApplyEditInput,
    ApplyEditResult,
    CachedNodeSnapshot,
    LoadCachedResultsInput,
    LoadCachedResultsOutput,
    MarkRunPausedInput,
    MarkRunResumedInput,
    PersistOneNodeResultInput,
    PersistResultsInput,
)
from temporal.utils.node_output_envelope import ENVELOPE_TAG, slim_envelope

logger = logging.getLogger(__name__)


# Map our terminal_status string to the WebSocket termination code the
# frontend already understands.  Keeps the Temporal path behaviourally
# identical to services/worker/executor.py.
def _termination_code(terminal_status: str):
    from api.websockets.event_models import TerminationStatusCode

    if terminal_status == "success":
        return TerminationStatusCode.SUCCESS
    if terminal_status == "stopped":
        return TerminationStatusCode.STOPPED
    return TerminationStatusCode.ERROR


def _build_execution_result(
    workflow_payload_dict: dict[str, Any] | None,
    node_results: dict[str, dict[str, Any]],
    success: bool,
) -> dict[str, Any]:
    """Build the legacy-shaped execution_result expected by persist_node_results.

    ``persist_node_results`` -> ``NodeResult.create_from_execution`` reads
    ``execution_result['workflow']['nodes']`` and looks for ``node_result``
    on each node entry.  We attach one synthesised from the per-node state
    we accumulated in the workflow, including the structured
    ``error_info`` so it lands in PostgreSQL alongside the run row.
    """
    if not workflow_payload_dict:
        # Nothing to thread results through; the persist layer will warn
        # and skip but the run-level status update still happens below.
        return {
            "success": success,
            "results": node_results,
        }

    # Shallow-copy the payload so we don't mutate the workflow input dict.
    payload = dict(workflow_payload_dict)
    nodes_in: list[dict[str, Any]] = payload.get("nodes", []) or []
    nodes_out: list[dict[str, Any]] = []

    for node in nodes_in:
        node_copy = dict(node)
        nid = node_copy.get("react_flow_generated_id") or node_copy.get("id")
        state = node_results.get(nid) if nid else None

        if state is not None:
            # ``output_ports_data`` MUST be carried through here.
            # ``create_from_execution`` (the consumer) writes whatever
            # we put on ``node_result`` straight onto
            # ``node_result.output_data`` via ``upsert_for_node_instance``,
            # which is a full replace -- so the per-port envelope dict
            # the incremental persist wrote during the run gets
            # overwritten by whatever shape we emit here.
            #
            # Dropping ``output_ports_data`` (the prior bug) caused
            # rerun-from-node to silently corrupt downstream inputs on
            # any multi-port upstream: ``load_cached_results_activity``
            # reads ``output.get("output_ports_data", {})`` and falls
            # back to the bare ``output_data`` dict when it's missing,
            # which for a multi-port emit is the entire
            # ``{port_a: ..., port_b: ...}`` map -- a downstream wired
            # to ``port_a`` then receives the whole map instead of the
            # single port value.
            #
            # Keeping this aligned with the incremental persist
            # (``_persist_incremental`` in agentic_workflow.py) is the
            # invariant; ``test_build_execution_result_preserves_output_ports_data``
            # locks it in.
            #
            # When `display_data` is present the node emitted a
            # display_model (e.g. MarkdownDisplay).  Use it as the
            # base so past-run loads and the EXECUTION_RESULT WS
            # event deliver the same shape the FE received during
            # live execution (display_modal, value, field_id, etc.).
            display_data = state.get("display_data")
            if display_data:
                node_copy["node_result"] = {
                    **display_data,
                    "data": state.get("output_data"),
                    "output_ports_data": state.get("output_ports_data") or {},
                    "error": state.get("error"),
                    "error_info": state.get("error_info"),
                    "metadata": state.get("metadata") or {},
                    "files": state.get("files") or [],
                }
            else:
                node_copy["node_result"] = {
                    "data": state.get("output_data"),
                    "output_ports_data": state.get("output_ports_data") or {},
                    "error": state.get("error"),
                    "error_info": state.get("error_info"),
                    "metadata": state.get("metadata") or {},
                    "files": state.get("files") or [],
                }
        else:
            node_copy["node_result"] = None

        nodes_out.append(node_copy)

    payload["nodes"] = nodes_out

    return {
        "success": success,
        "workflow": payload,
        "results": node_results,
    }


# Fallback copy for error entries that carry no structured ErrorInfo
# (e.g. an in-band node error that skipped classification).  The raw
# ``err["error"]`` string is deliberately NOT used as a fallback --
# on some paths it holds unsanitised exception text, and this event's
# contract is that every field is safe to show end users verbatim.
_GENERIC_STEP_ERROR = "This step encountered an error and could not finish."


def _build_user_facing_error_summary(
    errors: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    """Build the (message, data) pair for the run-level ``user_facing_error``.

    Sources copy exclusively from each error's structured
    ``error_info.user_message`` / ``actionable_hint`` (the curated
    templates in ``temporal/errors/error_info.py``), falling back to a
    generic sentence when absent -- never from the legacy ``error``
    string, which can carry raw technical text (ENG-3154).

    Returns ``None`` when there is nothing to report.
    """
    entries: list[dict[str, Any]] = []
    for err in errors or []:
        info = err.get("error_info") or {}
        if not isinstance(info, dict):
            info = {}
        entries.append(
            {
                "node_id": err.get("node_id"),
                "display_name": err.get("display_name"),
                "message": info.get("user_message") or _GENERIC_STEP_ERROR,
                "actionable_hint": info.get("actionable_hint"),
                "error_code": info.get("code"),
                "category": info.get("category"),
            }
        )
    if not entries:
        return None

    first = entries[0]
    step_name = first.get("display_name") or "A step"
    message = (
        f"The workflow stopped because a step failed. {step_name}: {first['message']}"
    )
    if first.get("actionable_hint"):
        message = f"{message} {first['actionable_hint']}"
    if len(entries) > 1:
        message = f"{message} ({len(entries) - 1} more step(s) also failed.)"

    data: dict[str, Any] = {
        "scope": "workflow",
        "status": "failed",
        "errors": entries,
    }
    return message, data


def _format_duration(started_at, completed_at) -> str | None:
    """Human-readable run duration (e.g. "8s", "1m 12s", "2h 3m").

    Returns ``None`` when either timestamp is missing so callers can omit it.
    """
    if not started_at or not completed_at:
        return None
    total = int((completed_at - started_at).total_seconds())
    if total < 0:
        return None
    if total < 60:
        return f"{total}s"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _build_execution_summary(
    errors: list[dict[str, Any]],
    execution_order: list[str],
) -> dict[str, Any]:
    """Build the ``execution_summary`` shape the frontend already consumes.

    Mirrors ``RunWorkflowHandler.handle``'s output so the
    ``EXECUTION_RESULT`` WebSocket event has identical structure
    whether the workflow ran on the legacy in-process executor or on
    Temporal.  Each error entry carries the structured ``error_info``
    payload (introduced for typed errors) alongside the legacy
    ``error`` / ``type`` / ``display_name`` fields the existing
    ``status-helpers.ts`` code already reads.
    """
    return {
        "errors": [
            {
                "node_id": err.get("node_id"),
                "display_name": err.get("display_name"),
                "error": err.get("error"),
                "error_info": err.get("error_info"),
                "type": err.get("type"),
            }
            for err in errors
        ],
        "execution_order": execution_order,
    }


@activity.defn
async def persist_results_activity(input: PersistResultsInput) -> None:
    """Write results to PostgreSQL and publish FE-visible terminal events.

    Three things happen here in order:

    1. **Persist node results to PostgreSQL.**  Includes the structured
       ``error_info`` per node so a later run inspector / debugging UI
       can render the same banner without re-classifying anything.
    2. **Update the run row's status.**  The error message column gets
       a concatenation of per-node user-facing messages so the legacy
       run-list UI keeps working.
    3. **Emit the FE-visible terminal events.**  ``EXECUTION_RESULT``
       carries the structured ``execution_summary`` (errors with
       ``error_info``) the frontend uses to render category-specific
       banners; ``TERMINATION`` is the lightweight signal the FE uses
       to switch the status bar.  Both are emitted on the same Redis
       pub/sub channel the WebSocket layer already forwards.
    """
    from api.websockets.emitters import (
        emit_user_facing_error,
        emit_workflow_result,
        publish_termination_message,
    )
    from api.websockets.event_models import NodeEvent
    from api.websockets.redis_manager import RedisWebSocketManager
    from services.crud import persist_node_results, update_workflow_run_status

    execution_result = _build_execution_result(
        input.workflow_payload_dict,
        input.node_results,
        input.success,
    )

    try:
        await persist_node_results(input.session_id, execution_result)
        logger.info("Persisted node results for session %s", input.session_id)
    except Exception as exc:
        capture_exception(exc)
        logger.error(
            "Failed to persist node results for session %s: %s",
            input.session_id,
            exc,
        )

    # Build a human-readable error message only for non-success terminals.
    error_msg: str | None = None
    if input.terminal_status != "success" and input.errors:
        parts = []
        for err in input.errors:
            name = err.get("display_name", err.get("node_id", "unknown"))
            parts.append(f"{name}: {err.get('error', 'Unknown')}")
        error_msg = "; ".join(parts)
    elif input.terminal_status == "stopped":
        # Match the message the legacy stop path writes for consistency.
        error_msg = "Workflow was stopped by user"

    try:
        await update_workflow_run_status(
            input.session_id,
            input.terminal_status,
            error_msg,
        )
    except Exception as exc:
        capture_exception(exc)
        logger.error(
            "Failed to update run status for session %s: %s",
            input.session_id,
            exc,
        )

    # Emit the structured workflow result event BEFORE the termination
    # signal so the frontend reliably sees the error_info payload
    # before deciding to tear down the WebSocket subscription.  The
    # event shape mirrors ``RunWorkflowHandler.handle``'s response
    # envelope so the existing FE consumers in ``status-helpers.ts``
    # work unchanged regardless of whether the run executed on the
    # legacy worker or on Temporal.
    try:
        execution_summary = _build_execution_summary(
            input.errors, input.execution_order
        )
        await emit_workflow_result(
            input.session_id,
            message=(
                f"Workflow {input.workflow_id} for session {input.session_id} "
                f"finished with status: {input.terminal_status}"
            ),
            data={
                "success": input.success,
                "execution_summary": execution_summary,
                "session_id": input.session_id,
            },
        )
    except Exception as exc:
        capture_exception(exc)
        logger.warning(
            "Failed to emit workflow_result for session %s: %s",
            input.session_id,
            exc,
        )

    # ENG-3154: emit the run-level human-readable error summary BEFORE
    # the termination signal (which closes the WS channel).  Only for
    # ``failed`` terminals -- ``stopped`` is a user action, not an
    # error.  Additive: the technical ``execution_result`` /
    # ``termination`` events above are unchanged.
    if input.terminal_status == "failed":
        try:
            summary = _build_user_facing_error_summary(input.errors)
            if summary is not None:
                message, data = summary
                await emit_user_facing_error(input.session_id, message, data=data)
            # A node blocked at prepare never ran, so nothing emitted its
            # node-level failure; emit the pair a failing node would.
            for err in input.errors:
                if err.get("stage") != "prepare" or not err.get("node_id"):
                    continue
                info = err.get("error_info") or {}
                text = str(err.get("error") or info.get("user_message") or "")
                await RedisWebSocketManager.publish_message_to_redis_channel(
                    session_id=input.session_id,
                    message=NodeEvent.error(
                        err["node_id"], err.get("display_name") or "", text
                    ).model_dump_json(exclude_none=True),
                )
                await emit_user_facing_error(
                    input.session_id,
                    text,
                    data={
                        "scope": "node",
                        "error_code": info.get("code"),
                        "actionable_hint": info.get("actionable_hint"),
                    },
                    node_id=err["node_id"],
                    node_display_name=err.get("display_name"),
                )
        except Exception as exc:
            capture_exception(exc)
            logger.warning(
                "Failed to emit user_facing_error for session %s: %s",
                input.session_id,
                exc,
            )

    # Wire 1.0.24: persist the run's final deliverable set as a
    # first-class ``files_available`` event (scope=primary) BEFORE the
    # termination signal, so it carries a sequence below the terminal and
    # a polling consumer (get_workflow_events) sees the deliverables
    # without any stream. The gateway's synthesized ``a2a.show_output``
    # terminal frame is unchanged. Best-effort: never delays termination
    # by failing.
    if input.terminal_status == "success":
        try:
            await _emit_run_completed_files_available(input.session_id)
        except Exception as exc:  # noqa: BLE001
            capture_exception(exc)
            logger.warning(
                "run-completed files_available emit failed for session %s: %s",
                input.session_id,
                exc,
            )

    await publish_termination_message(
        input.session_id,
        _termination_code(input.terminal_status),
    )

    await _emit_run_finished_notification(input)


async def _emit_run_completed_files_available(session_id: str) -> None:
    """Announce the run's post-allowlist primary files on its event log."""
    from sqlalchemy import select

    from db.models.workflow import Workflow as WorkflowModel
    from db.models.workflow_run import WorkflowRun
    from db.session import async_session_factory
    from services.node_results.files_available import (
        REASON_RUN_COMPLETED,
        SCOPE_PRIMARY,
        emit_files_available,
    )

    factory = async_session_factory(use_fresh_engine=True)
    async with factory() as session:
        row = (
            await session.execute(
                select(WorkflowRun.id, WorkflowModel.org_id)
                .join(WorkflowModel, WorkflowModel.id == WorkflowRun.workflow_id)
                .where(WorkflowRun.session_id == session_id)
            )
        ).first()
    if row is None:
        return

    run_id, org_id = str(row[0]), str(row[1])

    from services.orchestrator.tools.workflow_tools import (
        _resolve_run_output_files,
    )

    primary, _other = await _resolve_run_output_files(run_id, org_id)
    if not primary:
        return

    # Scale display contract: the terminal deliverable set always displays.
    files = [{**f, "display": True} if isinstance(f, dict) else f for f in primary]

    await emit_files_available(
        session_id,
        files=files,
        reason=REASON_RUN_COMPLETED,
        scope=SCOPE_PRIMARY,
        run_id=run_id,
        event_seed=f"run_completed:{session_id}",
    )


async def _emit_run_finished_notification(input: PersistResultsInput) -> None:
    """Notify the run owner that their workflow finished. Best-effort.

    Resolves the owner (``WorkflowRun.run_by_id``) and their email with a
    fresh session, then emits ``workflow_success`` / ``workflow_failure``.
    User-initiated stops are intentionally not notified. Any failure here
    is captured to Sentry and swallowed so it never fails the activity.
    """
    from db.session import async_session_factory
    from services.notifications import Recipient, notify
    from services.users import User as UserService
    from services.workflows import Workflow as WorkflowService

    if input.terminal_status == "stopped":
        return

    try:
        factory = async_session_factory(use_fresh_engine=True)
        async with factory() as session:
            wf_service = WorkflowService(session)
            run = await wf_service.get_run_by_session(input.session_id)
            if run is None:
                return
            owner = await UserService(session).get_by_id(run.run_by_id)
            workflow = await wf_service.get_by_id(run.workflow_id)
            org_id = workflow.org_id if workflow else None
            workflow_name = workflow.name if workflow else None
            error_message = run.error_message
            duration_text = _format_duration(run.started_at, run.completed_at)
            run_id = str(run.id)

        label = workflow_name or "Your workflow"
        if input.terminal_status == "success":
            event_type = "workflow_success"
            title = f"{label} finished successfully"
        else:
            event_type = "workflow_failure"
            title = f"{label} failed"

        output_count = sum(
            1
            for r in (input.node_results or {}).values()
            if isinstance(r, dict)
            and not r.get("error")
            and r.get("output_data") is not None
        )

        await notify(
            event_type=event_type,
            event_key=f"run:{run_id}:finished",
            title=title,
            recipients=[
                Recipient(
                    user_id=run.run_by_id,
                    email=owner.email if owner else None,
                    org_id=org_id,
                )
            ],
            link=f"/workflow/{input.workflow_id}?runId={run_id}",
            metadata={
                "run_id": run_id,
                "workflow_id": input.workflow_id,
                "workflow_name": workflow_name,
                "session_id": input.session_id,
                "terminal_status": input.terminal_status,
                "error_message": error_message,
                "duration_text": duration_text,
                "output_count": output_count,
            },
        )
    except Exception as exc:  # noqa: BLE001 — best-effort emit
        capture_exception(exc)
        logger.warning(
            "persist_results: notification emit failed for session %s: %s",
            input.session_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Incremental per-node persist (called after each node completes)
# ---------------------------------------------------------------------------


def _parse_iso(ts: str | None) -> datetime | None:
    """Tolerant ISO-8601 parser for the per-node timestamps.

    Accepts the str-dumped datetimes we serialise in the workflow via
    ``workflow.now().isoformat()``.  Returns ``None`` on parse failure
    so a malformed timestamp doesn't block the persist.
    """
    if not ts:
        return None
    try:
        # ``fromisoformat`` handles the ``+00:00`` suffix on Python 3.11+;
        # strip a trailing ``Z`` for safety on older runtimes.
        return datetime.fromisoformat(ts.rstrip("Z"))
    except (TypeError, ValueError):
        logger.warning("persist_one_node_result: unparseable timestamp %r", ts)
        return None


@activity.defn
async def persist_one_node_result_activity(
    input: PersistOneNodeResultInput,
) -> None:
    """Upsert a single node_result row for ``input.node_instance_id``.

    Called by AgenticWorkflow immediately after a node activity
    returns -- before the workflow moves on to the next level -- so
    the FE reflects completed nodes as soon as they complete, not
    only at end-of-run.  Any failure to write is non-fatal: the
    final persist_results_activity will re-upsert from the workflow's
    full in-memory snapshot.

    Idempotent: re-invoking this activity with the same inputs is a
    no-op (NodeResult.upsert_for_node_instance uses UPDATE when the
    row already exists and DELETEs the dependent file rows before
    re-seeding).
    """
    from db.models.enums import NodeResultStatus
    from db.session import async_session_factory
    from services.node_results import NodeResult as NodeResultService
    from services.workflows import Workflow as WorkflowService

    try:
        status = NodeResultStatus(input.status)
    except ValueError:
        logger.warning(
            "persist_one_node_result: unknown status %r for node %s; "
            "falling back to PENDING",
            input.status,
            input.node_instance_id,
        )
        status = NodeResultStatus.PENDING

    factory = async_session_factory(use_fresh_engine=True)
    try:
        async with factory() as session:
            wf_service = WorkflowService(session)
            run = await wf_service.get_run_by_session(input.session_id)
            if run is None:
                logger.warning(
                    "persist_one_node_result: run not found for session %s; skipping",
                    input.session_id,
                )
                return

            cached_from_run_uuid = None
            if input.cached_from_session_id:
                source_run = await wf_service.get_run_by_session(
                    input.cached_from_session_id
                )
                if source_run is not None:
                    cached_from_run_uuid = source_run.id

            node_service = NodeResultService(session)
            result_row = await node_service.upsert_for_node_instance(
                run_id=run.id,
                version_id=run.version_id,
                node_instance_id=input.node_instance_id,
                status=status,
                output_data=input.output_data
                if isinstance(input.output_data, dict) or input.output_data is None
                else {"value": input.output_data},
                error_message=input.error_message,
                started_at=_parse_iso(input.started_at_iso),
                completed_at=_parse_iso(input.completed_at_iso),
                cache_key=input.cache_key,
                cached_from_run_id=cached_from_run_uuid,
                files_data=input.files or None,
            )

            # Wire 1.0.24: snapshot this node's generated files BEFORE the
            # commit (rows are flushed, ids assigned) so the announcement
            # below only fires once they are durable + servable.
            generated_files: list = []
            if result_row is not None and status == NodeResultStatus.COMPLETED:
                try:
                    generated_files = [
                        row
                        for row in await node_service.file.list_by_result(result_row.id)
                        if row.generated
                    ]
                except Exception as exc:  # noqa: BLE001 - never block persist
                    logger.warning(
                        "persist_one_node_result: file snapshot failed for node %s: %s",
                        input.node_instance_id,
                        exc,
                    )

            await session.commit()

        # Wire 1.0.24: announce the node's freshly-durable generated files
        # as a first-class ``files_available`` workflow event, so an
        # external watcher on the run's event stream surfaces them the
        # moment they exist (not only at the terminal). After the commit:
        # every ``file_id`` here already resolves via the gateway files
        # API. Best-effort by contract.
        if generated_files:
            await _emit_node_files_available(
                session_id=input.session_id,
                run_id=str(run.id),
                version_id=run.version_id,
                node_instance_id=input.node_instance_id,
                file_rows=generated_files,
            )
    except Exception as exc:
        capture_exception(exc)
        logger.error(
            "persist_one_node_result failed for session %s node %s: %s",
            input.session_id,
            input.node_instance_id,
            exc,
        )


# ENG-3826: per-version primary-output rules cache for the mid-run tagger.
# One payload read per (version, worker process, TTL window) instead of one
# per node persist. Keyed on the version id; rules are payload-derived so a
# version's rules never change (the TTL just bounds memory).
_PRIMARY_RULES_TTL_SECONDS = 600.0
_primary_rules_cache: dict = {}


async def _primary_rules_for_version(version_id) -> Optional[dict]:
    """Primary-output rules (``{node_instance_id -> globs|None}``) for a
    workflow version, or ``None`` when unresolvable (callers fail open --
    ``is_primary_file(None, ...)`` verdicts every file ``True``)."""
    import time as _time

    if version_id is None:
        return None
    key = str(version_id)
    hit = _primary_rules_cache.get(key)
    now = _time.monotonic()
    if hit is not None and now - hit[1] < _PRIMARY_RULES_TTL_SECONDS:
        return hit[0]
    try:
        from sqlalchemy import select

        from api.a2a_gateway.a2a_output_allowlist import (
            primary_output_rules_from_payload,
        )
        from db.models.workflow_version import WorkflowVersion
        from db.session import async_session_factory

        factory = async_session_factory(use_fresh_engine=False)
        async with factory() as session:
            payload = (
                await session.execute(
                    select(WorkflowVersion.payload).where(
                        WorkflowVersion.id == version_id
                    )
                )
            ).scalar_one_or_none()
        rules = primary_output_rules_from_payload(payload)
    except Exception as exc:  # noqa: BLE001 - designation is best-effort
        logger.warning(
            "primary-output rules resolve failed for version %s: %s",
            version_id,
            exc,
        )
        return None
    _primary_rules_cache[key] = (rules, now)
    return rules


async def _emit_node_files_available(
    *,
    session_id: str,
    run_id: str,
    node_instance_id: str,
    file_rows: list,
    version_id=None,
) -> None:
    """Emit ``files_available`` for one node's generated files. Best-effort.

    ENG-3826: each entry carries ``primary: true/false`` -- the workflow
    author's designation (structured fields / legacy ``[[primary-output]]``
    marker; no designation or resolver failure -> everything ``True``).
    Consumers filter ``primary === true`` by default; the events themselves
    fire for every producing node so intermediate files stay on the record.

    Scale display contract: each entry also carries ``display`` -- the
    server-computed render verdict. Primary files always display; interim
    files display only when the session's show-intermediate preference is
    ON (see ``services.node_results.display_preference``; absent == OFF).
    """
    from api.a2a_gateway.a2a_output_allowlist import is_primary_file
    from services.node_results.display_preference import (
        get_session_show_intermediate,
    )
    from services.node_results.files_available import (
        REASON_NODE_FILES,
        SCOPE_FILES,
        emit_files_available,
    )

    try:
        rules = await _primary_rules_for_version(version_id)
        show_intermediate = await get_session_show_intermediate(session_id)
        files = []
        for row in file_rows:
            download_url = ""
            try:
                download_url = row.generate_durable_url()
            except Exception:  # noqa: BLE001 - metadata usable without it
                pass
            primary = is_primary_file(rules, node_instance_id, row.name)
            files.append(
                {
                    "file_id": str(row.id),
                    "filename": row.name,
                    "mime_type": row.mime_type,
                    "size_bytes": None,
                    "node_id": node_instance_id,
                    "download_url": download_url,
                    "source": "node_result_file",
                    "primary": primary,
                    "display": primary or show_intermediate,
                }
            )
        # Seed includes the file-id set: a Temporal retry with identical
        # files dedupes in the persister (same event_id), while a genuine
        # re-execution that produced different files announces again.
        seed = ":".join(
            [session_id, node_instance_id, *sorted(f["file_id"] for f in files)]
        )
        await emit_files_available(
            session_id,
            files=files,
            reason=REASON_NODE_FILES,
            scope=SCOPE_FILES,
            run_id=run_id,
            node_instance_id=node_instance_id,
            event_seed=seed,
        )
    except Exception as exc:  # noqa: BLE001 - never fail the activity
        capture_exception(exc)
        logger.warning(
            "files_available emit failed for node %s (session %s): %s",
            node_instance_id,
            session_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Pause / resume status persists
# ---------------------------------------------------------------------------


@activity.defn
async def mark_run_paused_activity(input: MarkRunPausedInput) -> None:
    """Flip a run into ``PAUSED`` with the captured state snapshot.

    Runs as its own activity so the workflow body stays pure and the
    DB write gets ``PERSIST_RETRY`` automatic retry semantics.  The
    shape of the snapshot written here is what the FE reads to render
    the pause panel -- see ``WorkflowRun.pause_state`` for the
    documented structure.

    Also emits a lifecycle event on the WebSocket channel so the FE
    can flip its status bar instantly rather than waiting for a
    get_detailed_status poll.

    Critical design: the WS event fires INDEPENDENT of the DB write.
    The workflow IS paused at the instant this activity is called
    (it's about to park on wait_condition); the FE must reflect that
    even if the DB row is temporarily out of sync (e.g. a transient
    asyncpg error or a misaligned enum value).  Without that
    decoupling a DB issue would leave the FE stuck on "Pausing"
    indefinitely while the workflow is actually paused.
    """
    from api.websockets.emitters import emit_lifecycle_event
    from api.websockets.event_models import LifecyclePhase
    from db.session import async_session_factory
    from services.workflows import Workflow as WorkflowService

    # Build the snapshot up-front (no I/O) so we can emit the WS
    # event even if the DB write blows up.
    pause_state_snapshot: dict = {
        "paused_at": datetime.utcnow().isoformat() + "Z",
        "paused_by_user_id": input.paused_by_user_id,
        "completed_node_ids": list(input.completed_node_ids),
        "in_flight_at_pause": list(input.in_flight_at_pause),
        "version_history": list(input.version_history),
        "notes": input.notes,
    }

    db_write_ok = False
    factory = async_session_factory(use_fresh_engine=True)
    try:
        async with factory() as session:
            wf_service = WorkflowService(session)
            run = await wf_service.get_run_by_session(input.session_id)
            if run is None:
                logger.warning(
                    "mark_run_paused: run not found for session %s",
                    input.session_id,
                )
            else:
                await wf_service.run.mark_paused(run.id, pause_state_snapshot)
                await session.commit()
                db_write_ok = True
                logger.info(
                    "mark_run_paused: DB row updated for session %s "
                    "(completed=%d, in_flight_at_pause=%d)",
                    input.session_id,
                    len(input.completed_node_ids),
                    len(input.in_flight_at_pause),
                )
    except Exception as exc:
        capture_exception(exc)
        logger.error(
            "mark_run_paused: DB write FAILED for session %s: %s "
            "-- WS lifecycle/paused event will still be emitted so "
            "the FE reflects the paused workflow",
            input.session_id,
            exc,
        )

    # WS emit is the user-visible side effect.  Always attempt it,
    # regardless of DB write outcome -- the workflow is paused on
    # the worker right now and the FE needs to know.
    try:
        await emit_lifecycle_event(
            input.session_id,
            LifecyclePhase.PAUSED,
            message="Workflow paused",
            data={
                "pause_state": pause_state_snapshot,
                "db_write_ok": db_write_ok,
            },
        )
        logger.info(
            "mark_run_paused: emitted lifecycle/paused for session %s (db_write_ok=%s)",
            input.session_id,
            db_write_ok,
        )
    except Exception as exc:
        capture_exception(exc)
        logger.error(
            "mark_run_paused: WS emit FAILED for session %s: %s -- "
            "FE will hit the pause-watchdog timeout and surface a warning",
            input.session_id,
            exc,
        )


@activity.defn
async def mark_run_resumed_activity(input: MarkRunResumedInput) -> None:
    """Flip a paused run back to ``RUNNING`` and clear ``pause_state``.

    Idempotent: if the run is already ``RUNNING`` this is a no-op.
    Emits a lifecycle event so the FE can restart its "running"
    animation without waiting for the next per-node event.

    Same DB/WS decoupling as ``mark_run_paused_activity`` -- the
    workflow IS resumed at the instant this activity is called
    (the wait_condition has already returned in the workflow body),
    so the FE must reflect that even if the DB row is stale.
    """
    from api.websockets.emitters import emit_lifecycle_event
    from api.websockets.event_models import LifecyclePhase
    from db.models.enums import RunStatus
    from db.session import async_session_factory
    from services.workflows import Workflow as WorkflowService

    db_write_ok = False
    already_running = False
    factory = async_session_factory(use_fresh_engine=True)
    try:
        async with factory() as session:
            wf_service = WorkflowService(session)
            run = await wf_service.get_run_by_session(input.session_id)
            if run is None:
                logger.warning(
                    "mark_run_resumed: run not found for session %s",
                    input.session_id,
                )
            elif run.status != RunStatus.PAUSED:
                # Already running (or terminal).  Don't emit RESUMED
                # in this case -- there's no PAUSED state to leave.
                already_running = True
                logger.info(
                    "mark_run_resumed: session %s is already %s; no-op",
                    input.session_id,
                    run.status.value,
                )
            else:
                await wf_service.update_run_status(run.id, RunStatus.RUNNING)
                await session.commit()
                db_write_ok = True
                logger.info(
                    "mark_run_resumed: DB row updated for session %s "
                    "(PAUSED -> RUNNING)",
                    input.session_id,
                )
    except Exception as exc:
        capture_exception(exc)
        logger.error(
            "mark_run_resumed: DB write FAILED for session %s: %s -- "
            "WS lifecycle/resumed event will still be emitted",
            input.session_id,
            exc,
        )

    if already_running:
        # Genuine no-op -- skip the WS emit too.
        return

    try:
        await emit_lifecycle_event(
            input.session_id,
            LifecyclePhase.RESUMED,
            message="Workflow resumed",
            data={"db_write_ok": db_write_ok},
        )
        logger.info(
            "mark_run_resumed: emitted lifecycle/resumed for session %s "
            "(db_write_ok=%s)",
            input.session_id,
            db_write_ok,
        )
    except Exception as exc:
        capture_exception(exc)
        logger.error(
            "mark_run_resumed: WS emit FAILED for session %s: %s -- "
            "FE will hit the resume-watchdog timeout and surface a warning",
            input.session_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Apply-edit: new RUN version + invalidate downstream NodeResult rows
# ---------------------------------------------------------------------------


@activity.defn
async def apply_edit_activity(input: ApplyEditInput) -> ApplyEditResult:
    """Atomically record an edit received during pause.

    Steps:

    1. Look up the run by session.
    2. Create a new ``workflow_version`` of type ``RUN`` containing the
       edited payload (and extract Node rows for queryability).
    3. Re-point ``workflow_run.version_id`` at the new version.
    4. Delete every ``NodeResult`` row whose ``node_instance_id`` is in
       ``invalidated_node_ids`` so the resumed run recomputes them.
       ``node_result_file`` rows cascade via their FK.

    Returns the new version's ID + number + the count of invalidated
    rows removed so the workflow can record those in ``pause_state``.

    The old ``workflow_version`` row stays put -- it remains the
    permanent audit record of what was originally executed.
    """
    from db.models.enums import VersionType
    from db.session import async_session_factory
    from services.node_results import NodeResult as NodeResultService
    from services.workflows import Workflow as WorkflowService

    # [VÁ 10 chay_thu 06/11] sandbox R12 chỗ (3) — D (SOAT_THIET_KE_SANDBOX_D.md mục 11): sửa khi tạm dừng KHÔNG đi qua prepare_workflow
    # (agentic_workflow.py:2411-2528) ⇒ kiểm sơ đồ MỚI ở đây, TRƯỚC khi ghi phiên bản. Lỗi KHÔNG thử lại; bên gọi bắt lỗi ⇒ giữ sơ đồ cũ
    # (agentic_workflow.py:2460-2466). Không ném trong transform ở thân workflow (kẹt lượt — D).
    from temporalio.exceptions import ApplicationError

    from workflow.r12_kiem import kiem_r12
    from workflow.runtime.payload_transformer import transform_advanced_to_backend

    try:
        kiem_r12(transform_advanced_to_backend(input.edited_payload))
    except ValueError as _e:
        raise ApplicationError(str(_e), type="R12Rejected", non_retryable=True) from _e

    factory = async_session_factory(use_fresh_engine=True)
    async with factory() as session:
        wf_service = WorkflowService(session)
        run = await wf_service.get_run_by_session(input.session_id)
        if run is None:
            raise ValueError(f"No run found for session {input.session_id}")

        new_version = await wf_service.version.create(
            workflow_id=run.workflow_id,
            user_id=input.user_id,
            payload=input.edited_payload,
            version_type=VersionType.RUN,
        )
        run.version_id = new_version.id
        await session.flush()

        deleted_count = 0
        if input.invalidated_node_ids:
            node_service = NodeResultService(session)
            deleted_count = await node_service.delete_for_node_instances(
                run_id=run.id,
                version_id=new_version.id,
                node_instance_ids=input.invalidated_node_ids,
            )
            # Also delete old-version rows for the same node_instance_ids
            # so the FE doesn't see stale results lingering.  The old
            # Node rows (on the replaced version) still have their own
            # node_result rows attached -- delete_for_node_instances
            # above only matched rows whose node.version_id equals the
            # NEW version_id.  Wipe the old ones too.
            from sqlalchemy import delete as sa_delete
            from sqlalchemy import select as sa_select

            from db.models import Node as NodeModel
            from db.models import NodeResult as NodeResultModel

            old_version_nodes = (
                (
                    await session.execute(
                        sa_select(NodeModel).where(
                            NodeModel.version_id != new_version.id,
                            NodeModel.node_instance_id.in_(input.invalidated_node_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if old_version_nodes:
                old_deletion = await session.execute(
                    sa_delete(NodeResultModel).where(
                        NodeResultModel.run_id == run.id,
                        NodeResultModel.node_id.in_(n.id for n in old_version_nodes),
                    )
                )
                deleted_count += old_deletion.rowcount or 0

        await session.commit()

    # Emit lifecycle event so the FE can show "edit applied -- N nodes
    # will re-run" immediately.  Outside the DB session so a WS
    # failure doesn't roll anything back.
    from api.websockets.emitters import emit_lifecycle_event
    from api.websockets.event_models import LifecyclePhase

    try:
        await emit_lifecycle_event(
            input.session_id,
            LifecyclePhase.EDIT_APPLIED,
            message=f"Edit applied; {deleted_count} cached result(s) invalidated",
            data={
                "new_version_id": str(new_version.id),
                "new_version_number": new_version.version_number,
                "invalidated_node_ids": list(input.invalidated_node_ids),
                "deleted_result_count": deleted_count,
            },
        )
    except Exception as exc:
        capture_exception(exc)
        logger.warning(
            "apply_edit_activity: WS emit failed for session %s: %s",
            input.session_id,
            exc,
        )

    return ApplyEditResult(
        new_version_id=str(new_version.id),
        new_version_number=new_version.version_number,
        deleted_result_count=deleted_count,
    )


# ---------------------------------------------------------------------------
# Rerun-from-node: load snapshots + persist them against the new run
# ---------------------------------------------------------------------------


def seed_port_envelopes(
    persisted_ports: dict[str, Any] | None,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild the per-port envelopes a cached node hands downstream on rerun.

    Matches the live-execution shape (see
    ``temporal/utils/node_output_envelope.py``). Two cases:

    * NEW persisted rows: per-port values are already envelopes (they carry
      their own ``files`` list). Trust them, but slim their metadata: rows
      stored before the slimming still carry ``message_history``.
    * OLD persisted rows (pre-envelope migration): per-port values are bare
      ``.data`` scalars. Wrap each in an envelope and attach the row-level
      ``files`` so downstream consumers can still pull files off the
      upstream output. For multi-port nodes this is overinclusive (we don't
      know which port emitted which file), but that beats losing every
      file. Consumers dedupe by (s3_key, name).

    Args:
        persisted_ports: The stored ``output_data["output_ports_data"]``.
        files: The row-level file dicts, reused for OLD-shape ports.

    Returns:
        ``{port_id: envelope}`` ready for ``CachedNodeSnapshot``.
    """
    per_port_envelopes: dict[str, Any] = {}
    for port_id, value in (persisted_ports or {}).items():
        if isinstance(value, dict) and value.get(ENVELOPE_TAG) is True:
            per_port_envelopes[port_id] = slim_envelope(value)
        else:
            per_port_envelopes[port_id] = {
                ENVELOPE_TAG: True,
                "id": "",
                "data": value,
                "files": list(files),
                "metadata": {},
                "error": None,
            }
    return per_port_envelopes


@activity.defn
async def load_cached_results_activity(
    input: LoadCachedResultsInput,
) -> LoadCachedResultsOutput:
    """Load cached node results from the source run for rerun seeding.

    Projects every ``node_result`` row in the source run into a
    ``CachedNodeSnapshot`` and persists an equivalent row on the
    TARGET run with ``cached_from_run_id`` set, so the FE knows those
    results were re-used rather than re-computed.

    Nodes in ``fresh_node_ids`` (the user's starting points and
    everything downstream) are NOT returned -- the resumed workflow
    executes them fresh.

    The workflow calls this ONCE right after prepare, then pre-seeds
    ``self._node_results`` with the returned snapshots.  From that
    point on the dispatch loop treats cached nodes as already-done
    and skips them.
    """
    from db.models.enums import NodeResultStatus
    from db.session import async_session_factory
    from services.node_results import NodeResult as NodeResultService
    from services.workflows import Workflow as WorkflowService

    fresh = set(input.fresh_node_ids)
    factory = async_session_factory(use_fresh_engine=True)

    async with factory() as session:
        wf_service = WorkflowService(session)
        source_run = await wf_service.get_run_by_session(input.source_session_id)
        if source_run is None:
            logger.warning(
                "load_cached_results: source session %s has no run; nothing to seed",
                input.source_session_id,
            )
            return LoadCachedResultsOutput(snapshots=[], source_run_id=None)

        target_run = await wf_service.get_run_by_session(input.target_session_id)
        if target_run is None:
            logger.warning(
                "load_cached_results: target session %s has no run; aborting seed",
                input.target_session_id,
            )
            return LoadCachedResultsOutput(
                snapshots=[], source_run_id=str(source_run.id)
            )

        node_service = NodeResultService(session)
        source_results = await node_service.list_by_run_keyed_by_instance(source_run.id)

        # Compute the cached-node id set BEFORE the heavy persist
        # loop so we can emit the "rerun started" lifecycle event
        # early.  This is what gives the FE its teal cached badges
        # within ~1s of clicking "Re-run from here", instead of
        # ~N×DB-write later when the seed loop finishes.  The
        # FE consumer is the matching ``case LifecyclePhase.RERUN_STARTED``
        # handler in ``workflow-editor-page.tsx``.
        early_cached_ids = sorted(
            nid
            for nid, row in source_results.items()
            if nid not in fresh and row.status == NodeResultStatus.COMPLETED
        )
        # Only emit on the first attempt.  If the activity fails and
        # Temporal retries it, re-emitting the lifecycle event each
        # time spams the FE with N copies of the same "rerun started"
        # entry in the events panel.  Attempt is 1-based on the SDK.
        try:
            attempt = activity.info().attempt
        except Exception:
            attempt = 1  # outside an activity context (tests)
        if attempt == 1:
            try:
                from api.websockets.emitters import emit_lifecycle_event
                from api.websockets.event_models import LifecyclePhase

                await emit_lifecycle_event(
                    input.target_session_id,
                    LifecyclePhase.RERUN_STARTED,
                    message=(
                        f"Re-run started; reusing {len(early_cached_ids)} "
                        f"cached node result(s) from source run"
                    ),
                    data={
                        "source_session_id": input.source_session_id,
                        "source_run_id": str(source_run.id),
                        "cached_node_count": len(early_cached_ids),
                        "cached_node_ids": early_cached_ids,
                        "fresh_node_ids": list(input.fresh_node_ids),
                    },
                )
            except Exception as exc:
                capture_exception(exc)
                logger.warning(
                    "load_cached_results: early RERUN_STARTED emit failed for "
                    "session %s: %s",
                    input.target_session_id,
                    exc,
                )

        snapshots: list[CachedNodeSnapshot] = []
        for node_instance_id, row in source_results.items():
            if node_instance_id in fresh:
                continue
            # Only seed COMPLETED rows -- failed / skipped upstream
            # outputs were not actually produced, and seeding them
            # would mislead the resolver and the FE.
            if row.status != NodeResultStatus.COMPLETED:
                continue

            output = row.output_data or {}
            files = []
            for f in row.files or []:
                files.append(
                    {
                        # Drop the source row's file UUID -- the target's
                        # ``node_result_file`` rows MUST have fresh UUIDs.
                        # Reusing the source's primary key collided on
                        # ``node_result_file_pkey`` and crashed every
                        # rerun-from-node whose source had any file
                        # outputs.  ``NodeResultFileService.create_many``
                        # treats id=None as "let SQLAlchemy generate one"
                        # which is exactly what we want -- the new rows
                        # point at the SAME S3 object via s3_key but
                        # have their own row identity on the target run.
                        "id": None,
                        "name": f.name,
                        "mime_type": f.mime_type,
                        "s3_bucket": f.s3_bucket,
                        "s3_key": f.s3_key,
                        "s3_region": f.s3_region,
                        "generated": bool(f.generated),
                        # Carry the content-addressed metadata across rerun
                        # reuse -- the fresh rows point at the SAME bytes, so
                        # the hash/size/blob_store_artifact_id are identical.
                        "content_hash": f.content_hash,
                        "size_bytes": f.size_bytes,
                        "blob_store_artifact_id": (
                            str(f.blob_store_artifact_id)
                            if f.blob_store_artifact_id is not None
                            else None
                        ),
                    }
                )

            persisted_ports = (
                output.get("output_ports_data", {}) if isinstance(output, dict) else {}
            )
            per_port_envelopes = seed_port_envelopes(persisted_ports, files)

            # Reconstruct display_data from the persisted output_data.
            # Post-fix rows have `display_modal` at the top level
            # (the display_model envelope was merged in during persist).
            # Extract those fields so the final persist rebuild
            # preserves display metadata for cached nodes.
            cached_display_data: dict[str, Any] | None = None
            if isinstance(output, dict) and output.get("display_modal"):
                # Keys that belong to the display envelope (from
                # NodeParam.to_dict()); everything else is persist
                # infrastructure (output_ports_data, error, etc.).
                display_keys = {
                    "field_id",
                    "display_name",
                    "display_modal",
                    "value",
                    "valid_types",
                    "show",
                    "advanced",
                    "required",
                    "info",
                    "is_list",
                    "hidden",
                    "id",
                }
                cached_display_data = {
                    k: v
                    for k, v in output.items()
                    if k in display_keys and v is not None
                }

            snapshot = CachedNodeSnapshot(
                node_instance_id=node_instance_id,
                display_name=row.node.node_display_name if row.node else "",
                node_type=row.node.node_type if row.node else "",
                output_data=output.get("data") if isinstance(output, dict) else output,
                output_ports_data=per_port_envelopes,
                display_data=cached_display_data,
                files=files,
                error=output.get("error") if isinstance(output, dict) else None,
                error_info=(
                    output.get("error_info") if isinstance(output, dict) else None
                ),
                metadata=(
                    output.get("metadata") or {} if isinstance(output, dict) else {}
                ),
                cache_key=row.cache_key,
            )
            snapshots.append(snapshot)

            # Also persist the cached row against the TARGET run so the
            # run-detail view displays the full graph immediately (and
            # so a later "persist final" call doesn't overwrite the
            # cached-from pointer).  Upsert keys on (run_id,
            # node_instance_id); the FE's "cached" badge hangs off
            # ``cached_from_run_id``.
            await node_service.upsert_for_node_instance(
                run_id=target_run.id,
                version_id=target_run.version_id,
                node_instance_id=node_instance_id,
                status=NodeResultStatus.COMPLETED,
                output_data=row.output_data,
                error_message=row.error_message,
                started_at=row.started_at,
                completed_at=row.completed_at,
                cache_key=row.cache_key,
                cached_from_run_id=source_run.id,
                files_data=files or None,
            )

        await session.commit()

    # Emit lifecycle event outside the DB context so the FE can show
    # "seeded N nodes from source run" while the workflow starts
    # dispatching the fresh ones.
    from api.websockets.emitters import emit_lifecycle_event
    from api.websockets.event_models import LifecyclePhase

    try:
        await emit_lifecycle_event(
            input.target_session_id,
            LifecyclePhase.RERUN_SEEDED,
            message=f"Seeded {len(snapshots)} cached node result(s) from source run",
            data={
                "source_session_id": input.source_session_id,
                "source_run_id": str(source_run.id),
                "cached_node_count": len(snapshots),
                "cached_node_ids": [s.node_instance_id for s in snapshots],
                "fresh_node_ids": list(input.fresh_node_ids),
            },
        )
    except Exception as exc:
        capture_exception(exc)
        logger.warning(
            "load_cached_results: WS emit failed for session %s: %s",
            input.target_session_id,
            exc,
        )

    return LoadCachedResultsOutput(
        snapshots=snapshots, source_run_id=str(source_run.id)
    )
