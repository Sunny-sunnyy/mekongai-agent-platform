"""Activity: fetch, transform, and validate the workflow payload.

Runs once at the start of every workflow execution.  Replaces the
``run_workflow_async`` preamble (DB fetch, transform, validate, Composio
sessions) with a Temporal activity that returns a serialised
``WorkflowPayload`` plus supporting metadata.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from sentry_sdk import capture_exception
from temporalio import activity

from services.asset_mode import resolve_asset_mode
from temporal.config import MAX_PARALLEL_NODES
from temporal.models.node_io import PrepareWorkflowInput, PrepareWorkflowResult
from temporal.utils.node_exclusion import is_excluded_from_execution

logger = logging.getLogger(__name__)


# The run row and its version are flushed before Temporal is started, so a
# fast worker can look before the creating transaction commits. Retry briefly
# rather than substituting a different version: the fallback below picks the
# newest version for the workflow, which for a triggered run is a different
# snapshot — one without the file the trigger staged (ENG-4582).
_RUN_PAYLOAD_ATTEMPTS = 5
_RUN_PAYLOAD_INITIAL_DELAY_SECONDS = 0.1


async def _await_run_payload(session_id: str) -> dict | None:
    """Re-read a run's own payload, tolerating an uncommitted creator.

    Exponential backoff from 100 ms, ~3 s total — orders of magnitude more than
    a commit needs, and only paid when the first read misses.
    """
    # Imported here for the same reason the caller does: services.crud pulls a
    # heavy import tree that must stay out of the workflow sandbox.
    from services.crud import get_run_payload_from_db

    delay = _RUN_PAYLOAD_INITIAL_DELAY_SECONDS
    for attempt in range(1, _RUN_PAYLOAD_ATTEMPTS + 1):
        await asyncio.sleep(delay)
        payload = await get_run_payload_from_db(session_id)
        if payload:
            logger.info(
                "Run payload for session %s became visible on attempt %d",
                session_id,
                attempt,
            )
            return payload
        delay *= 2
    return None


async def _run_row_visible(session_id: str) -> bool:
    """Whether the run row itself is readable.

    ``get_run_payload_from_db`` collapses four different outcomes into ``None``
    -- no row, a row with no version, a version with an empty payload, and any
    exception it swallowed. Only the first is worth waiting for, so ask the
    narrower question. Errors here are deliberately allowed to propagate rather
    than be misreported as a commit-visibility race.
    """
    from sqlalchemy import select

    from db.models.workflow_run import WorkflowRun
    from db.session import async_session_factory

    factory = async_session_factory(use_fresh_engine=True)
    async with factory() as session:
        result = await session.execute(
            select(WorkflowRun.id).where(WorkflowRun.session_id == session_id).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def _resolve_run_payload(session_id: str, is_scheduled: bool) -> dict | None:
    """The payload this run must execute, or None if there is no run row yet.

    None is only ever returned for a scheduled run, which has no row by design
    and whose caller then creates one from the latest version. For every other
    run the row was created before Temporal started, so a miss means the
    creating transaction has not committed — wait for it, and fail rather than
    let the caller run a different version (ENG-4582).
    """
    from services.crud import get_run_payload_from_db

    payload = await get_run_payload_from_db(session_id)
    if payload:
        return payload
    if is_scheduled:
        return None

    # A visible row with no usable payload is not a race -- waiting cannot fix
    # it, and reporting it as one sends whoever triages the run after a
    # commit-visibility problem that never happened. But the row may have become
    # visible *between* the two reads, which is precisely the race this guards,
    # so re-read the payload before concluding it is unusable.
    if await _run_row_visible(session_id):
        payload = await get_run_payload_from_db(session_id)
        if payload:
            return payload
        raise ValueError(
            f"Run for session {session_id} exists but has no usable version "
            f"payload; refusing to run a different workflow version"
        )

    payload = await _await_run_payload(session_id)
    if payload:
        return payload
    raise ValueError(
        f"Run payload for session {session_id} is still not visible after "
        f"{_RUN_PAYLOAD_ATTEMPTS} attempts; refusing to run a different "
        f"workflow version"
    )


async def _get_latest_authored_payload(workflow_id: str) -> dict | None:
    """Fetch the workflow's latest *authored* payload from our own DB.

    Deliberately not "newest version by created_at". Every run writes a version
    row -- ``RUN``, ``SCHEDULED_RUN``, ``TRIGGERED_RUN`` -- so on any workflow
    that has been run at all, the newest row is a run snapshot rather than the
    saved graph. A scheduled tick reading that would execute the previous run's
    payload, including a trigger's baked-in ``file_data``, and then persist it
    as its own version so the next tick inherits it (ENG-4582).

    Uses the same ladder as ``TriggerWorkflowRunHandler._resolve_version``:
    published, then latest manual save, so the answer matches what every other
    caller means by "the current workflow".
    """
    try:
        import uuid

        from db.session import async_session_factory
        from services.workflows import Workflow

        wf_uuid = uuid.UUID(workflow_id)
        factory = async_session_factory(use_fresh_engine=True)
        async with factory() as session:
            service = Workflow(session)
            version = await service.get_latest_published_version(wf_uuid)
            if version is None or not version.payload:
                version = await service.get_latest_manual_save_version(wf_uuid)
            if version and version.payload:
                logger.info(
                    "Retrieved authored payload (%s) for workflow %s from local DB",
                    getattr(version, "version_type", "unknown"),
                    workflow_id,
                )
                return version.payload
            logger.warning(
                "No published or manually-saved version for workflow %s; refusing "
                "to fall back to a run snapshot",
                workflow_id,
            )
    except Exception as exc:
        logger.warning(
            "Failed to get workflow payload from local DB for %s: %s",
            workflow_id,
            exc,
        )
    return None


async def _rederive_tool_auth_types(workflow_payload) -> None:
    """Re-resolve each platform tool node's ``auth_type`` from the MCP registry.

    ``auth_type`` is stamped onto a tool node by the catalog at the moment the
    node is added to the canvas, then frozen into the saved payload. When a
    server's auth type changes afterwards — as ``procured-data-server`` did
    when it moved to ``entra_obo`` — every previously-saved workflow stays
    pinned to the stale value and the run connects with the wrong credentials
    (or none) until each node is manually re-added. The registry is the same
    source of truth the catalog was generated from, so resolve against it at
    run time instead of trusting the payload.

    Mirrors the inference the chat-side rebind path already performs in
    :mod:`services.run_tools.binding`, which documents the same staleness.

    Only servers present in the registry are touched: composio, org-managed and
    external servers resolve to ``None`` and keep their payload value (org
    tools carry authoritative auth resolved from the DB instead). A registry
    failure leaves the payload untouched rather than breaking the run.
    """
    from services.run_tools.binding import infer_platform_auth_type

    for node in workflow_payload.nodes:
        if node.node_type != "tool" or not node.mcp_server:
            continue
        try:
            inferred = await infer_platform_auth_type(node.mcp_server)
        except Exception as exc:  # noqa: BLE001 - never break the run
            logger.warning(
                "auth_type inference failed for %s: %s", node.mcp_server, exc
            )
            continue
        if inferred and inferred != node.auth_type:
            logger.info(
                "Re-derived auth_type for tool node %s (%s): payload=%s -> registry=%s",
                node.react_flow_generated_id,
                node.mcp_server,
                node.auth_type,
                inferred,
            )
            node.auth_type = inferred


async def _resolve_composio_sessions(
    input: PrepareWorkflowInput,
    nodes: list[dict[str, Any]],
    composio_config: dict[str, Any],
) -> tuple[dict[str, dict[str, str]], dict[str, Any] | None]:
    """Create the run's Composio sessions in place: ``(sessions, blocked)``."""
    from services.composio.connections import oauth_callback_url
    from services.composio.sessions import prepare_run_sessions

    sessions, blocked = await prepare_run_sessions(
        nodes,
        user_id=input.user_id,
        composio_config=composio_config,
        callback_url=oauth_callback_url(),
    )
    return sessions, blocked


@activity.defn
async def prepare_workflow(input: PrepareWorkflowInput) -> PrepareWorkflowResult:
    """Fetch payload from DB, transform, validate, resolve Composio sessions."""
    from api.routers.models.workflow import EdgeType
    from api.websockets.emitters import emit_status_messages
    from services.crud import update_workflow_run_status
    from workflow.runtime.payload_transformer import transform_advanced_to_backend

    # Visible "the worker has the workflow" event.  The router emits
    # "Scheduling workflow..." when it accepts the run; without this
    # second message there is dead air between accept and the first
    # node ``BEGIN`` event (which can be many seconds in production
    # and indefinitely if the worker is overloaded).  This single
    # status_update tells the user that the run actually reached a
    # worker and is being prepared.  Best-effort -- a Redis hiccup
    # must not block the run.
    try:
        await emit_status_messages(
            input.session_id,
            "Preparing workflow...",
        )
    except Exception as emit_exc:
        logger.warning(
            "prepare_workflow: failed to emit 'Preparing workflow...' "
            "status_update for session %s: %s",
            input.session_id,
            emit_exc,
        )

    payload_raw = await _resolve_run_payload(input.session_id, input.is_scheduled)

    if not payload_raw:
        # Scheduled run: no row exists by design. Fetch the latest workflow
        # version payload from our own PostgreSQL DB and create the run row.
        from services.crud import create_workflow_run_in_db

        payload_raw = await _get_latest_authored_payload(input.workflow_id)

        if not payload_raw:
            raise ValueError(f"Workflow {input.workflow_id} not found in database")

        try:
            await create_workflow_run_in_db(
                org_id=input.org_id,
                workflow_id=input.workflow_id,
                session_id=input.session_id,
                user_id=input.user_id or "scheduler",
                payload=payload_raw,
                user_email=input.user_email,
            )
            logger.info(
                "Created workflow_run row for scheduled session %s",
                input.session_id,
            )
        except Exception as exc:
            capture_exception(exc)
            logger.warning(
                "Failed to create workflow_run for session %s: %s",
                input.session_id,
                exc,
            )

    # Mark the workflow_version as a scheduled run and link to the
    # originating schedule.  Uses version_type instead of a separate
    # trigger_type column on workflow_run, per team convention.
    if input.is_scheduled:
        try:
            import uuid as _uuid

            from sqlalchemy import select, update

            from db.models.enums import VersionType
            from db.models.workflow_run import WorkflowRun as WorkflowRunModel
            from db.models.workflow_version import (
                WorkflowVersion as WorkflowVersionModel,
            )
            from db.session import async_session_factory

            factory = async_session_factory(use_fresh_engine=True)
            async with factory() as session:
                run_result = await session.execute(
                    select(WorkflowRunModel.version_id).where(
                        WorkflowRunModel.session_id == input.session_id
                    )
                )
                version_id = run_result.scalar_one_or_none()

                if version_id:
                    values: dict = {"version_type": VersionType.SCHEDULED_RUN}
                    if input.schedule_id:
                        values["schedule_id"] = _uuid.UUID(input.schedule_id)

                    await session.execute(
                        update(WorkflowVersionModel)
                        .where(WorkflowVersionModel.id == version_id)
                        .values(**values)
                    )
                    await session.commit()
        except Exception as trigger_exc:
            logger.warning(
                "Failed to mark version as scheduled_run for session %s: %s",
                input.session_id,
                trigger_exc,
            )

    workflow_payload = transform_advanced_to_backend(payload_raw)
    workflow_payload.validate_connections()
    # [VÁ 10 chay_thu 06/11] sandbox R12 (b)(c) chỗ (1): mọi lượt Temporal đi qua đây — chay_thu/sua_doi/workflow/r12_kiem.py
    # Lỗi KHÔNG thử lại (PREPARE_RETRY cho 2 lần — temporal/config.py:70-75); agentic_workflow.py:751-765 bắt lỗi ⇒ lượt FAILED.
    from temporalio.exceptions import ApplicationError

    from workflow.r12_kiem import kiem_r12

    try:
        kiem_r12(workflow_payload)
    except ValueError as _e:
        # Thử thật 06/11 10:21: không ghi thì workflow_run FAILED mà error_message TRỐNG (_persist_final không mang lý do) ⇒ người dùng
        # không biết vì sao. Ghi lý do trước khi ném.
        try:
            await update_workflow_run_status(input.session_id, "failed", str(_e))
        except Exception as _e2:  # noqa: BLE001
            logger.warning("R12: không ghi được lý do vào workflow_run: %s", _e2)
        raise ApplicationError(str(_e), type="R12Rejected", non_retryable=True) from _e

    # Stored payloads carry a build-time snapshot of each tool's auth_type;
    # correct it against the registry before the nodes are serialised into
    # the per-node activity inputs below.
    await _rederive_tool_auth_types(workflow_payload)

    # Transition the WorkflowRun to RUNNING here.  The legacy worker did
    # this in services/worker/executor.py before invoking the handler;
    # without it the row stays QUEUED for the entire execution and
    # ``started_at`` is never set, breaking duration metrics and the
    # in-progress run view.  Failing to update the status is non-fatal
    # (the persist_results activity will still finalise the row).
    try:
        await update_workflow_run_status(input.session_id, "running")
    except Exception as exc:
        capture_exception(exc)
        logger.warning(
            "Failed to mark workflow run as running for session %s: %s",
            input.session_id,
            exc,
        )

    # ENG-3234 (stream half): emit the once-per-run ``run_started`` event
    # THE MOMENT the run flips to RUNNING -- before any node event.  This
    # is the producer for the frozen ``workflow.run_started`` A2A wire
    # event, which was documented (roster attached in the event adapter)
    # but never actually emitted by anything: runs opened with node
    # events and the run-start roster only existed on the synchronous
    # call_workflow response.  Emitted HERE (not at trigger time) because
    # the WorkflowRun row is guaranteed committed at this point, so the
    # emitter can persist the event directly before publishing it. This
    # ordering closes the pre-subscribe gap: a caller either sees the live
    # Redis event or finds the durable row in its first backfill. The live
    # subscribe_workflow_events tail still picks up the same event from the
    # Redis channel, and the background persister's duplicate upsert is a
    # no-op. ``data`` carries the sub-agent roster built from the SAME raw
    # payload this run executes, so the persisted row is self-contained.
    # Best-effort -- persistence or Redis trouble must not block the run.
    #
    # Nodes excluded from the execution graph are computed FIRST, from the
    # shared ``is_excluded_from_execution`` rule, so the roster below can
    # apply the very same predicate: a node this run will never dispatch
    # (ENG-5459: Evaluations nodes when ``run_evals`` is off) must not be
    # listed as a sub-agent "To Be Started". Three kinds of exclusion:
    #   * Handle nodes (tool / memory) — instantiated as attachments to
    #     agent nodes via handle_connections rather than dispatched.
    #   * Annotation nodes (sticky_note) — purely canvas decoration; not
    #     dispatched and not attached to anything.
    #   * Evaluations nodes when ``input.run_evals`` is False — never
    #     dispatched (no eval node results, no eval LLM spend). Their
    #     outgoing edges are dropped by the input resolver like any
    #     excluded node's; eval nodes are normally sinks, so downstream
    #     impact is nil in practice.
    # We name the field handle_node_ids for backwards compatibility — the
    # toposort consumes it as a generic "exclude from execution" set.
    # Tool / memory / annotation rows stay on the roster as they always
    # have; only the toggle-driven eval exclusion filters it.
    handle_node_ids: list[str] = [
        nd.react_flow_generated_id
        for nd in workflow_payload.nodes
        if is_excluded_from_execution(nd, run_evals=input.run_evals)
    ]

    try:
        from api.a2a_gateway.event_adapter import sub_agents_from_payload
        from api.websockets.emitters import emit_run_started

        sub_agents = sub_agents_from_payload(
            payload_raw or {}, run_evals=input.run_evals
        )
        await emit_run_started(
            input.session_id,
            data={
                "workflow_id": input.workflow_id,
                "session_id": input.session_id,
                "sub_agents": sub_agents,
                "total_sub_agents": len(sub_agents),
            },
        )
    except Exception as emit_exc:
        capture_exception(emit_exc)
        logger.warning(
            "prepare_workflow: failed to emit run_started for session %s: %s",
            input.session_id,
            emit_exc,
        )

    # Resolve the WorkflowRun row's own primary key (distinct from
    # ``session_id``, which is Temporal/WebSocket routing plumbing and not
    # externally lookupable). This is the "run_id" the platform API and
    # frontend actually address a run by (``GET .../runs/{run_id}``), so
    # it's what downstream integrations need for traceability.
    run_id = ""
    try:
        from sqlalchemy import select

        from db.models.workflow_run import WorkflowRun as WorkflowRunModel
        from db.session import async_session_factory

        factory = async_session_factory(use_fresh_engine=True)
        async with factory() as session:
            result = await session.execute(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.session_id == input.session_id
                )
            )
            run_row = result.scalar_one_or_none()
            if run_row:
                run_id = str(run_row.id)
                metadata = dict(run_row.run_metadata or {})
                if input.temporal_workflow_id:
                    metadata["temporal_workflow_id"] = input.temporal_workflow_id
                # ENG-5459: record the evals toggle this run EXECUTES with --
                # the very value the exclusion set above was computed from --
                # so the row itself says whether its Evaluations nodes were
                # skipped (the absence of eval node_results is otherwise the
                # only trace). Operators, the rerun resolver and audits can
                # read ``run_metadata.run_evals`` instead of trusting what a
                # caller sent.
                metadata["run_evals"] = bool(input.run_evals)
                # The parallel cap this run executes with, so its performance
                # report replays it under that cap and not today's setting.
                # Clamped like the execution loop's semaphore.
                metadata["max_parallel_nodes"] = max(1, MAX_PARALLEL_NODES)
                run_row.run_metadata = metadata
                await session.commit()
    except Exception as exc:
        capture_exception(exc)
        logger.warning(
            "Failed to resolve workflow_run row for session %s: %s",
            input.session_id,
            exc,
        )

    # Admin tool allow-list and pinned accounts; rows are keyed by user id or
    # email across creation paths, so both are matched.
    composio_config: dict[str, Any] = {}
    if input.user_id or input.user_email:
        try:
            from services.composio_tool_configs.runtime import (
                fetch_merged_composio_config,
            )

            composio_config = await fetch_merged_composio_config(
                input.org_id, input.user_id or "", user_email=input.user_email
            )
        except Exception as exc:
            capture_exception(exc)
            logger.warning("Composio config lookup failed: %s", exc)

    # ``handle_node_ids`` (the exclude-from-execution set) was computed
    # above, before the run_started emission, so the roster and the
    # execution graph are derived from the same rule.
    handle_connections: dict[str, list[str]] = defaultdict(list)
    handle_set = set(handle_node_ids)
    node_map = {n.react_flow_generated_id: n for n in workflow_payload.nodes}
    for edge in workflow_payload.edges:
        if edge.source_node_id not in handle_set:
            continue
        tgt = node_map.get(edge.target_node_id)
        if not tgt:
            continue
        if edge.edge_type == EdgeType.PORT_TO_PORT and edge.target_port_id:
            for port in tgt.input_ports:
                if port.id == edge.target_port_id and port.binding_type == "handle":
                    handle_connections[edge.target_node_id].append(edge.source_node_id)
                    break

    wp_dict = workflow_payload.model_dump(mode="json")

    # Point each Composio tool node at a session for this run, after checking
    # the member's connections. A missing connection blocks the run here, with
    # a connect link, instead of failing inside the node without one.
    composio_sessions, composio_blocked = await _resolve_composio_sessions(
        input, wp_dict["nodes"], composio_config
    )

    workflow_metadata = payload_raw.get("metadata") or {}
    raw_asset_mode = workflow_metadata.get("asset_mode")

    resolved_asset_mode = await resolve_asset_mode(input.org_id, raw_asset_mode)

    raw_special_instructions = workflow_metadata.get("special_instructions")
    special_instructions = (
        raw_special_instructions.strip()
        if isinstance(raw_special_instructions, str)
        and raw_special_instructions.strip()
        else None
    )

    # Per-workflow clarification default (same metadata surface as
    # special_instructions). Invalid / unknown modes are dropped so a
    # corrupted payload degrades to "no workflow default" rather than
    # accidentally enabling clarifications.
    from temporal.models.workflow_io import CLARIFICATION_MODES

    raw_clarification_mode = workflow_metadata.get("default_clarification_mode")
    default_clarification_mode = (
        raw_clarification_mode
        if isinstance(raw_clarification_mode, str)
        and raw_clarification_mode in CLARIFICATION_MODES
        else None
    )
    raw_clarification_timeout = workflow_metadata.get(
        "default_clarification_timeout_secs"
    )
    try:
        default_clarification_timeout_secs = (
            int(raw_clarification_timeout)
            if raw_clarification_timeout is not None
            else None
        )
    except (TypeError, ValueError):
        default_clarification_timeout_secs = None

    return PrepareWorkflowResult(
        workflow_payload_dict=wp_dict,
        composio_config=composio_config,
        composio_blocked=composio_blocked,
        composio_sessions=composio_sessions,
        node_count=len(workflow_payload.nodes) - len(handle_node_ids),
        handle_node_ids=handle_node_ids,
        handle_connections=dict(handle_connections),
        workflow_asset_mode=resolved_asset_mode,
        workflow_special_instructions=special_instructions,
        default_clarification_mode=default_clarification_mode,
        default_clarification_timeout_secs=default_clarification_timeout_secs,
        run_id=run_id,
    )
