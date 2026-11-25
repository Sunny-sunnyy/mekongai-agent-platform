"""Core workflow execution logic.

Contains the async workflow execution function, decoupled from any task queue.
This is the function that actually runs a workflow end-to-end.
"""

import asyncio
import contextlib
import logging
from typing import Any, Dict

from sentry_sdk import capture_exception

from api.handlers.workflow.post import RunWorkflowHandler
from api.websockets.emitters import emit_status_messages, publish_termination_message
from api.websockets.event_models import TerminationStatusCode
from common.context_vars.context_vars import (
    set_org_id_context_var,
    set_session_id_context_var,
    set_user_token_context_var,
)
from core import langfuse_identity
from core.settings import settings
from services.crud import (
    get_run_payload_from_db,
    persist_node_results,
    update_workflow_run_status,
)

logger = logging.getLogger(__name__)


async def run_workflow_async(
    session_id: str,
    workflowid: str,
    orgid: str,
    current_user: Dict[str, Any],
):
    """
    Execute a workflow.

    This function:
    1. Tries to fetch payload from PostgreSQL first (created by router)
    2. Falls back to DynamoDB (platform API) if not found
    3. Transforms AdvancedWorkflowPayload to WorkflowPayload for execution
    4. Executes the workflow
    5. Updates run status and persists node results
    """
    await set_session_id_context_var(session_id)
    await set_org_id_context_var(orgid)
    await set_user_token_context_var(current_user.get("token"))

    if settings.LANGFUSE_ENABLED:
        from langfuse import get_client, propagate_attributes

        # Same identity vocabulary as the Temporal node activities
        # (``core.langfuse_identity``) so a run reads identically in Langfuse
        # whichever execution path produced it.
        user_id = langfuse_identity.workflow_run_user_id(
            user_email=current_user.get("email"), user_id=current_user.get("user_id")
        )
        langfuse = get_client()
        workflow_obs = langfuse.start_as_current_observation(
            as_type="span",
            name=langfuse_identity.WORKFLOW_RUN_TRACE_NAME,
            input={
                "session_id": session_id,
                "workflow_id": workflowid,
                "org_id": orgid,
            },
            metadata={
                "workflow_id": workflowid,
                "org_id": orgid,
                "user_id": user_id,
                "environment": settings.ENVIRONMENT,
            },
        )
        attr_ctx = propagate_attributes(
            session_id=session_id,
            user_id=user_id,
            tags=langfuse_identity.workflow_run_tags(
                org_id=orgid, environment=settings.ENVIRONMENT
            ),
            metadata=langfuse_identity.workflow_run_metadata(
                workflow_id=workflowid, org_id=orgid, environment=settings.ENVIRONMENT
            ),
        )
    else:
        workflow_obs = contextlib.nullcontext()
        attr_ctx = contextlib.nullcontext()

    with workflow_obs as observation:
        with attr_ctx:
            try:
                result = await _execute_workflow(
                    session_id=session_id,
                    workflowid=workflowid,
                    orgid=orgid,
                    current_user=current_user,
                )
                if observation and hasattr(observation, "update"):
                    observation.update(output={"status": "success"})
                return result
            except asyncio.CancelledError:
                if observation and hasattr(observation, "update"):
                    observation.update(
                        output={"status": "stopped", "reason": "Cancelled by user"},
                        level="WARNING",
                    )
                raise
            except Exception as e:
                if observation and hasattr(observation, "update"):
                    observation.update(
                        output={"status": "failed", "error": str(e)},
                        level="ERROR",
                    )
                raise


async def _execute_workflow(
    session_id: str,
    workflowid: str,
    orgid: str,
    current_user: Dict[str, Any],
):
    """Inner workflow execution with Langfuse context already established."""
    await emit_status_messages(
        session_id,
        f"Fetching workflow configuration for '{workflowid}'",
    )

    # Try to fetch payload from PostgreSQL first (run was created by router)
    payload = await get_run_payload_from_db(session_id)

    if payload:
        logger.info(
            f"Fetched workflow payload from PostgreSQL for session {session_id} "
            f"(size: {len(str(payload))} chars)"
        )
    # Platform Fallback using workflow id.
    else:
        # Fallback to DynamoDB via platform API
        logger.info(
            f"Payload not found in PostgreSQL for session {session_id}, falling back to DynamoDB"
        )
        from services.platform import get_platform_service

        platform_service = get_platform_service()
        try:
            workflow_response = await platform_service.get_workflow(
                orgid=orgid,
                workflow_id=workflowid,
                user_token=current_user.get("token"),
            )
            # Extract the payload field which contains the actual workflow definition
            payload = workflow_response.get("payload")
            if not payload:
                raise ValueError(f"Workflow {workflowid} not found or payload is empty")

            logger.info(
                f"Fetched workflow config from DynamoDB for {workflowid} (size: {len(str(payload))} chars)"
            )
        except Exception as e:
            capture_exception(e)
            logger.error(f"Failed to fetch workflow {workflowid}: {str(e)}")
            await emit_status_messages(
                session_id,
                f"Failed to fetch workflow configuration: {str(e)}",
            )
            # Update run status to FAILED
            await update_workflow_run_status(session_id, "failed", str(e))
            raise

    await emit_status_messages(
        session_id,
        f"Dequeued workflow '{payload.get('name', payload.get('id', ''))}' for execution",
    )

    # Transform AdvancedWorkflowPayload (React Flow format) to WorkflowPayload (backend execution format) : Validator
    from workflow.runtime.payload_transformer import transform_advanced_to_backend

    logger.info("Transforming AdvancedWorkflowPayload to WorkflowPayload for execution")
    try:
        workflow_payload = transform_advanced_to_backend(payload)
        workflow_payload.validate_connections()
        # [VÁ 10 chay_thu 06/11] sandbox R12 (b)(c) chỗ (2): đường hàng đợi Redis — lỗi rơi vào except dưới ⇒ lượt FAILED, có lý do
        from workflow.r12_kiem import kiem_r12

        kiem_r12(workflow_payload)
        logger.info(
            f"Successfully transformed and validated workflow with {len(workflow_payload.nodes)} nodes"
        )
    except Exception as e:
        capture_exception(e)
        logger.error(f"Failed to transform workflow payload: {str(e)}")
        await emit_status_messages(
            session_id,
            f"Failed to transform workflow payload: {str(e)}",
        )
        # Update run status to FAILED
        await update_workflow_run_status(session_id, "failed", str(e))
        raise

    # Update run status to RUNNING
    await update_workflow_run_status(session_id, "running")

    handler = RunWorkflowHandler(
        orgid=orgid,
        workflowid=workflowid,
        current_user={
            "token": current_user.get("token"),
            "user_id": current_user.get("user_id", ""),
            "email": current_user.get("email", ""),
        },
        session_id=session_id,
    )

    # Execute the workflow - handler now always returns, even on exception
    try:
        result = await handler.handle(payload=workflow_payload)
    except Exception as e:
        capture_exception(e)
        # Handle unexpected exceptions that escape the handler
        error_msg = str(e)
        logger.exception(
            "Unexpected error during workflow execution for session %s (workflow %s): %s",
            session_id,
            workflowid,
            error_msg,
        )
        await update_workflow_run_status(session_id, "failed", error_msg)
        await publish_termination_message(session_id, TerminationStatusCode.ERROR)
        raise

    # Extract any exception that occurred during execution
    execution_exception = result.pop("_execution_exception", None)

    # ALWAYS persist node results - regardless of success/failure/cancellation
    # This ensures partial results are saved even when workflow is stopped
    await persist_node_results(session_id, result)
    logger.info(f"Persisted node results for session {session_id}")

    # Promote completed output files to first-class Artifact entities.
    # Best-effort: failures must not block run finalisation.
    try:
        from services.artifacts.promotion import promote_run_artifacts
        from services.artifacts.thumbnail_dispatch import dispatch_thumbnail_jobs

        artifact_ids = await promote_run_artifacts(session_id)
        if artifact_ids:
            await dispatch_thumbnail_jobs([str(aid) for aid in artifact_ids])
        logger.info(f"Artifacts promoted for session {session_id}")
    except Exception as _promo_exc:
        logger.warning(
            "Artifact promotion failed (non-blocking) for session %s: %s",
            session_id,
            _promo_exc,
        )

    # Now handle the different execution outcomes
    if execution_exception:
        if isinstance(execution_exception, asyncio.CancelledError):
            # Handle explicit cancellation (stop-workflow)
            logger.info(f"Workflow {workflowid} was stopped for session {session_id}")
            await update_workflow_run_status(
                session_id, "stopped", "Workflow was stopped by user"
            )
            await publish_termination_message(session_id, TerminationStatusCode.STOPPED)
            raise execution_exception
        else:
            # Handle other exceptions
            error_msg = str(execution_exception)
            logger.exception(
                "Workflow execution failed for session %s (workflow %s): %s",
                session_id,
                workflowid,
                error_msg,
            )
            await update_workflow_run_status(session_id, "failed", error_msg)
            await publish_termination_message(session_id, TerminationStatusCode.ERROR)
            raise execution_exception

    # Check if workflow succeeded (no exception but might have node errors)
    if not result.get("success", False):
        # Workflow failed - build a descriptive error message with all node failures
        error_details = result.get("execution_summary", {}).get("errors", [])
        if error_details:
            error_parts = []
            for err in error_details:
                node_name = err.get("display_name", err.get("node_id", "unknown"))
                err_text = err.get("error", "Unknown error")
                error_parts.append(f"{node_name}: {err_text}")
            error_msg = "; ".join(error_parts)
        else:
            error_msg = "Workflow execution failed"
        logger.error(
            f"Workflow {workflowid} failed for session {session_id}: {error_msg}"
        )
        await update_workflow_run_status(session_id, "failed", error_msg)
        await publish_termination_message(session_id, TerminationStatusCode.ERROR)
        raise RuntimeError(f"Workflow execution failed: {error_msg}")

    # Update run status to SUCCESS
    await update_workflow_run_status(session_id, "success")
    await publish_termination_message(session_id, TerminationStatusCode.SUCCESS)

    logger.info(
        f"Workflow {workflowid} completed successfully for session {session_id}"
    )
    return None
