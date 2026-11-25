"""
Evaluation service for managing evaluations and tests in the platform.

This module provides a high-level interface for evaluation and test operations,
encapsulating all evaluation-related functionality from the platform service.
Since evaluations and tests are powered by the same service from a platform
infrastructure perspective, they are managed together in this module.
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, Dict, List, Optional

import httpx
from sentry_sdk import capture_exception

from api.validation import sanitize_name
from auth.enums import OtherCapitolServicesEnum
from auth.manager import GlobalM2MAuthManager
from core.settings import settings
from services.platform.client import PlatformAPIClient
from temporal.errors.node_errors import EvalNotFoundError

logger = logging.getLogger(__name__)


def _with_platform_safe_name(test_config: Dict[str, Any]) -> Dict[str, Any]:
    """platform-api rejects eval-test names over NAME_MAX_LENGTH or containing
    denylisted characters; a 422 here fails the caller's whole run. Names can
    be machine-generated (e.g. bound node output), so coerce instead of raise.
    Applied at this boundary so every present and future caller is covered."""
    name = test_config.get("name")
    if not isinstance(name, str):
        return test_config
    safe = sanitize_name(name, fallback="Eval Test")
    if safe != name:
        logger.info(
            "Eval test name sanitized for platform validation (len %d -> %d)",
            len(name),
            len(safe),
        )
        test_config = {**test_config, "name": safe}
    return test_config


class EvaluationService:
    """
    Service for managing evaluations and tests.

    This service handles all evaluation and test-related operations including
    creating, listing, retrieving, updating, deleting, and running tests.
    """

    def __init__(self, client: Optional[PlatformAPIClient] = None):
        """
        Initialize the evaluation service.

        Args:
            client: Optional PlatformAPIClient instance. If not provided,
                    a new instance will be created.
        """
        self.client = client or PlatformAPIClient()
        logger.info("Evaluation service initialized")

    # Evaluation Management Methods
    async def list_evaluations(
        self, orgid: str, user_token: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List all evaluations for an organization.

        Merges records from both the v1 and v2 eval-config tables

        Args:
            orgid: Organization ID
            user_token: User authentication token

        Returns:
            List of evaluation dictionaries
        """
        try:
            v1_evals = await self.list_v1_evaluations(orgid)
            v2_evals = await self.list_v2_evaluations(orgid)
            merged = v1_evals + v2_evals
            logger.info(
                f"Retrieved {len(merged)} evaluations for org {orgid} "
                f"(v1={len(v1_evals)}, v2={len(v2_evals)})"
            )
            return merged
        except Exception as e:
            capture_exception(e)
            logger.error(f"Failed to list evaluations for org {orgid}: {str(e)}")
            raise

    async def list_v1_evaluations(self, orgid: str) -> List[Dict[str, Any]]:
        """Fetch eval configs from the v1 table."""
        result = await self.client.make_request("GET", f"/evaluations/{orgid}")
        return result if isinstance(result, list) else result.get("evaluations", [])

    async def list_v2_evaluations(self, orgid: str) -> List[Dict[str, Any]]:
        """Fetch eval configs from the v2 table and normalize to v1 shape."""
        result = await self.client.make_request("GET", f"/v2/evaluations/{orgid}")
        records = result if isinstance(result, list) else []
        return [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "description": r.get("description", ""),
                "type": r.get("type"),
                "model": r.get("judge_model"),
                "eval_config": r.get("eval_config"),
            }
            for r in records
        ]

    async def get_evaluation(
        self, orgid: str, eval_id: str, user_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific evaluation by ID.

        Args:
            orgid: Organization ID
            eval_id: Evaluation ID
            user_token: User authentication token

        Returns:
            Dict containing evaluation data
        """
        try:
            result = await self.client.make_request(
                "GET", f"/evaluations/{orgid}/{eval_id}"
            )
            logger.info(f"Retrieved evaluation {eval_id} for org {orgid}")
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(
                f"Failed to get evaluation {eval_id} for org {orgid}: {str(e)}"
            )
            raise

    async def create_evaluation(
        self,
        orgid: str,
        eval_data: Dict[str, Any],
        user_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new evaluation.

        Args:
            orgid: Organization ID
            eval_data: Evaluation creation data
            user_token: User authentication token

        Returns:
            Dict containing the created evaluation data
        """
        try:
            result = await self.client.make_request(
                "POST", f"/evaluations/{orgid}", json_data=eval_data
            )
            logger.info(
                f"Created evaluation {result.get('eval_id', 'unknown')} for org {orgid}"
            )
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(f"Failed to create evaluation for org {orgid}: {str(e)}")
            raise

    async def update_evaluation(
        self,
        orgid: str,
        eval_id: str,
        eval_data: Dict[str, Any],
        user_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an evaluation.

        Args:
            orgid: Organization ID
            eval_id: Evaluation ID
            eval_data: Evaluation update data
            user_token: User authentication token

        Returns:
            Dict containing updated evaluation data
        """
        try:
            result = await self.client.make_request(
                "PUT", f"/evaluations/{orgid}/{eval_id}", json_data=eval_data
            )
            logger.info(f"Updated evaluation {eval_id} for org {orgid}")
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(
                f"Failed to update evaluation {eval_id} for org {orgid}: {str(e)}"
            )
            raise

    async def delete_evaluation(
        self, orgid: str, eval_id: str, user_token: Optional[str] = None
    ) -> None:
        """
        Delete an evaluation.

        Args:
            orgid: Organization ID
            eval_id: Evaluation ID
            user_token: User authentication token
        """
        try:
            await self.client.make_request("DELETE", f"/evaluations/{orgid}/{eval_id}")
            logger.info(f"Deleted evaluation {eval_id} for org {orgid}")
        except Exception as e:
            capture_exception(e)
            logger.error(
                f"Failed to delete evaluation {eval_id} for org {orgid}: {str(e)}"
            )
            raise

    # Test Management Methods
    async def list_tests(
        self, orgid: str, user_token: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List all tests for an organization.

        Args:
            orgid: Organization ID
            user_token: User authentication token

        Returns:
            List of test dictionaries
        """
        try:
            result = await self.client.make_request(
                "GET", f"/evaluations/tests/{orgid}", user_token=user_token
            )
            tests = result if isinstance(result, list) else result.get("tests", [])
            logger.info(f"Retrieved {len(tests)} tests for org {orgid}")
            return tests
        except Exception as e:
            capture_exception(e)
            logger.error(f"Failed to list tests for org {orgid}: {str(e)}")
            raise

    async def get_test(
        self, orgid: str, test_id: str, user_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific test by ID.

        Args:
            orgid: Organization ID
            test_id: Test ID
            user_token: User authentication token

        Returns:
            Dict containing test data
        """
        try:
            result = await self.client.make_request(
                "GET", f"/evaluations/test/{orgid}/{test_id}"
            )
            logger.info(f"Retrieved test {test_id} for org {orgid}")
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(f"Failed to get test {test_id} for org {orgid}: {str(e)}")
            raise

    async def create_test(
        self,
        orgid: str,
        test_config: Dict[str, Any],
        user_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new test.

        Args:
            orgid: Organization ID
            test_config: Test configuration data
            user_token: User authentication token

        Returns:
            Dict containing the created test data
        """
        try:
            test_config = _with_platform_safe_name(test_config)
            logger.info(
                f"Creating test for org {orgid} with config: {json.dumps(test_config, indent=2)}"
            )
            result = await self.client.make_request(
                "POST", f"/evaluations/test/{orgid}", json_data=test_config
            )
            logger.info(
                f"Created test {result.get('test_id', 'unknown')} for org {orgid}"
            )
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(f"Failed to create test for org {orgid}: {str(e)}")
            raise

    async def update_test(
        self,
        orgid: str,
        test_id: str,
        test_config: Dict[str, Any],
        user_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update a test configuration.

        Args:
            orgid: Organization ID
            test_id: Test ID
            test_config: Updated test configuration
            user_token: User authentication token

        Returns:
            Dict containing updated test data
        """
        try:
            test_config = _with_platform_safe_name(test_config)
            result = await self.client.make_request(
                "PATCH", f"/evaluations/test/{orgid}/{test_id}", json_data=test_config
            )
            logger.info(f"Updated test {test_id} for org {orgid}")
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(f"Failed to update test {test_id} for org {orgid}: {str(e)}")
            raise

    async def delete_test(
        self, orgid: str, test_id: str, user_token: Optional[str] = None
    ) -> None:
        """
        Delete a test.

        Args:
            orgid: Organization ID
            test_id: Test ID
            user_token: User authentication token
        """
        try:
            await self.client.make_request(
                "DELETE", f"/evaluations/test/{orgid}/{test_id}"
            )
            logger.info(f"Deleted test {test_id} for org {orgid}")
        except Exception as e:
            capture_exception(e)
            logger.error(f"Failed to delete test {test_id} for org {orgid}: {str(e)}")
            raise

    async def run_test(
        self,
        orgid: str,
        test_id: str,
        user_token: Optional[str] = None,
        node_id: Optional[str] = None,
        workflow_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run a test by test_id and org_id.

        Args:
            orgid: Organization ID
            test_id: Test ID
            user_token: User authentication token
            node_id: Workflow node instance ID (for tracking)
            workflow_run_id: Workflow run/session ID (for tracking)

        Returns:
            Dict containing test run result
        """
        try:
            params = {}
            if node_id:
                params["node_id"] = node_id
            if workflow_run_id:
                params["workflow_run_id"] = workflow_run_id
            result = await self.client.make_request(
                "POST", f"/evaluations/test/run/{orgid}/{test_id}", params=params
            )
            logger.info(f"Started test run for test {test_id} in org {orgid}")
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(f"Failed to run test {test_id} for org {orgid}: {str(e)}")
            raise

    async def list_test_runs(
        self, orgid: str, test_id: str, user_token: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List all test runs for a specific test.

        Args:
            orgid: Organization ID
            test_id: Test ID
            user_token: User authentication token

        Returns:
            List of test run dictionaries
        """
        try:
            result = await self.client.make_request(
                "GET", f"/evaluations/test/runs/{orgid}/{test_id}"
            )
            runs = result if isinstance(result, list) else result.get("runs", [])
            logger.info(
                f"Retrieved {len(runs)} test runs for test {test_id} in org {orgid}"
            )
            return runs
        except Exception as e:
            capture_exception(e)
            logger.error(
                f"Failed to list test runs for test {test_id} in org {orgid}: {str(e)}"
            )
            raise

    async def list_test_runs_by_workflow(
        self, orgid: str, workflow_run_id: str
    ) -> List[Dict[str, Any]]:
        """
        List all eval results for a specific workflow run.

        Args:
            orgid: Organization ID
            workflow_run_id: Workflow run/session ID

        Returns:
            List of eval result dictionaries
        """
        try:
            result = await self.client.make_request(
                "GET",
                f"/evaluations/test/runs-by-workflow/{orgid}/{workflow_run_id}",
            )
            if not isinstance(result, list):
                raise ValueError(
                    f"Expected list response for workflow run {workflow_run_id} in org {orgid}, "
                    f"got {type(result).__name__}"
                )
            logger.info(
                f"Retrieved {len(result)} eval results for workflow run {workflow_run_id} in org {orgid}"
            )
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(
                f"Failed to list eval results for workflow run {workflow_run_id} in org {orgid}: {str(e)}"
            )
            raise

    async def get_test_run(
        self, orgid: str, test_id: str, run_id: str, user_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get a specific test run by run_id, org_id and test_id.

        Args:
            orgid: Organization ID
            test_id: Test ID
            run_id: Run ID
            user_token: User authentication token

        Returns:
            Dict containing test run data
        """
        try:
            result = await self.client.make_request(
                "GET", f"/evaluations/test/run/{orgid}/{test_id}/{run_id}"
            )
            logger.info(
                f"Retrieved test run {run_id} for test {test_id} in org {orgid}"
            )
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(
                f"Failed to get test run {run_id} for test {test_id} in org {orgid}: {str(e)}"
            )
            raise

    async def run_reference_output_eval(
        self,
        orgid: str,
        eval_id: str,
        workflow_output: str,
        workflow_output_file: dict | None = None,
        test_id: str | None = None,
        node_id: str | None = None,
        workflow_run_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Run a V2 reference output eval via SSE streaming."""
        # [ghi chú] bài chấm v2 (exemplar / dimensional) đi đường platform-api SSE ⇒ eval-service nhiều loại,
        # có vòng agent / file — chưa có mức tiền tối đa ⇒ TỪ CHỐI trước khi gửi (0 đồng). Tắt: CHAY_THU_EVAL_CHI_PASS_FAIL=0.
        if __import__("os").environ.get("CHAY_THU_EVAL_CHI_PASS_FAIL", "1") != "0":
            raise ValueError("Bộ chạy thử chỉ nhận bài chấm pass_fail / score (bài v2 reference output chưa có mức tiền tối đa).")
        json_data = {"eval_id": eval_id, "workflow_output": workflow_output}
        if workflow_output_file:
            json_data["workflow_output_file"] = workflow_output_file
        if test_id:
            json_data["test_id"] = test_id
        if node_id:
            json_data["node_id"] = node_id
        if workflow_run_id:
            json_data["workflow_run_id"] = workflow_run_id

        endpoint = f"/v2/evaluations/{orgid}/run-reference-output"
        return self._stream_reference_output_eval(endpoint, json_data, eval_id, orgid)

    async def _stream_reference_output_eval(
        self,
        endpoint: str,
        json_data: dict,
        eval_id: str,
        orgid: str,
    ) -> AsyncGenerator[dict, None]:
        base_url = settings.PLATFORM_API_BASE_URL
        headers = {"Content-Type": "application/json"}
        # [VÁ CỦA MÌNH 08/11/2026, nhóm kỹ thuật] Bỏ lấy token M2M khi chạy local — ĐÚNG điều kiện của họ ở
        # services/platform/client.py:81-89 (make_request). Vì sao: lấy token đọc bộ đệm DynamoDB THẬT của AWS
        # (auth/dynamo_cache/dynamo_cache.py:29-30, không endpoint) ⇒ trên máy lỗi "Unable to locate credentials";
        # platform-api local chạy INTERNAL_ROUTES_AUTH_ENABLED=false nên nhận yêu cầu không token. Môi trường khác: giữ nguyên.
        env = (getattr(settings, "ENVIRONMENT", "") or "").lower()
        if env not in ("local", "dev-local", "test"):
            token = await GlobalM2MAuthManager.get_token_for_service(
                OtherCapitolServicesEnum.PLATFORM_API
            )
            headers["Authorization"] = f"Bearer {token}"
        url = f"{base_url}{endpoint}"
        timeout = httpx.Timeout(1800)

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                json=json_data,
                headers=headers,
                params={"stream": "true"},
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    detail = body.decode(errors="replace")
                    if response.status_code == 404:
                        logger.warning(
                            f"Reference output eval {eval_id} not found for org "
                            f"{orgid}; it may have been deleted or never existed "
                            f"in this org: {detail}"
                        )
                        raise EvalNotFoundError(
                            f"Eval {eval_id} no longer exists in this org. "
                            f"Remove or replace the eval node."
                        )
                    logger.error(
                        f"{response.status_code} streaming reference output eval "
                        f"{eval_id} for org {orgid}: {detail}"
                    )
                    response.raise_for_status()

                event_type = None
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: ") and event_type:
                        data = json.loads(line[6:])
                        yield {"event": event_type, "data": data}
                        event_type = None

        logger.info(f"Streamed reference output eval {eval_id} for org {orgid}")

    async def run_dimensional_eval_by_id(
        self,
        orgid: str,
        eval_id: str,
        output: str | dict,
        meta_data: dict | None = None,
        node_id: Optional[str] = None,
        workflow_run_id: Optional[str] = None,
        workflow_output_files: Optional[list[dict]] = None,
    ) -> AsyncGenerator[dict, None]:
        """Run a saved dimensional eval by ID via SSE streaming."""
        # [ghi chú] bài chấm v2 (exemplar / dimensional) đi đường platform-api SSE ⇒ eval-service nhiều loại,
        # có vòng agent / file — chưa có mức tiền tối đa ⇒ TỪ CHỐI trước khi gửi (0 đồng). Tắt: CHAY_THU_EVAL_CHI_PASS_FAIL=0.
        if __import__("os").environ.get("CHAY_THU_EVAL_CHI_PASS_FAIL", "1") != "0":
            raise ValueError("Bộ chạy thử chỉ nhận bài chấm pass_fail / score (bài v2 dimensional chưa có mức tiền tối đa).")
        json_data: dict[str, Any] = {"eval_id": eval_id, "output": output}
        if meta_data:
            json_data["meta_data"] = meta_data
        if node_id:
            json_data["node_id"] = node_id
        if workflow_run_id:
            json_data["workflow_run_id"] = workflow_run_id
        if workflow_output_files:
            json_data["workflow_output_files"] = workflow_output_files

        endpoint = f"/v2/evaluations/{orgid}/run-dimensional-eval-by-id"
        return self._stream_dimensional_eval(endpoint, json_data, eval_id, orgid)

    async def _stream_dimensional_eval(
        self,
        endpoint: str,
        json_data: dict,
        eval_id: str,
        orgid: str,
    ) -> AsyncGenerator[dict, None]:
        base_url = settings.PLATFORM_API_BASE_URL
        headers = {"Content-Type": "application/json"}
        # [VÁ CỦA MÌNH 08/11/2026, nhóm kỹ thuật] Như _stream_reference_output_eval ở trên: bỏ token M2M khi local,
        # đúng điều kiện services/platform/client.py:81-89. Đây là chỗ khối Đánh giá lỗi lần chạy 1 (07/11).
        env = (getattr(settings, "ENVIRONMENT", "") or "").lower()
        if env not in ("local", "dev-local", "test"):
            token = await GlobalM2MAuthManager.get_token_for_service(
                OtherCapitolServicesEnum.PLATFORM_API
            )
            headers["Authorization"] = f"Bearer {token}"
        url = f"{base_url}{endpoint}"
        timeout = httpx.Timeout(1800)

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                json=json_data,
                headers=headers,
                params={"stream": "true"},
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    detail = body.decode(errors="replace")
                    if response.status_code == 404:
                        logger.warning(
                            f"Dimensional eval {eval_id} not found for org "
                            f"{orgid}; it may have been deleted or never existed "
                            f"in this org: {detail}"
                        )
                        raise EvalNotFoundError(
                            f"Eval {eval_id} no longer exists in this org. "
                            f"Remove or replace the eval node."
                        )
                    logger.error(
                        f"{response.status_code} streaming dimensional eval "
                        f"{eval_id} for org {orgid}: {detail}"
                    )
                    response.raise_for_status()

                event_type = None
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: ") and event_type:
                        data = json.loads(line[6:])
                        yield {"event": event_type, "data": data}
                        event_type = None

        logger.info(f"Streamed dimensional eval {eval_id} for org {orgid}")

    def run_workflow_eval_by_id(self, *args, **kwargs) -> AsyncGenerator[dict, None]:
        """Backwards-compat alias for run_dimensional_eval_by_id."""
        return self.run_dimensional_eval_by_id(*args, **kwargs)

    async def get_eval_status(self, orgid: str, s3_key: str) -> Dict[str, Any]:
        """
        Poll for reference output eval completion.

        Returns:
            Dict with status (pending/completed/failed), result, and error fields.
        """
        try:
            result = await self.client.make_request(
                "GET", f"/v2/evaluations/{orgid}/eval-status/{s3_key}"
            )
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(
                f"Failed to get eval status for {s3_key} in org {orgid}: {str(e)}"
            )
            raise

    async def get_test_status(
        self, orgid: str, test_id: str, run_id: str, user_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get the status of a specific test run.

        Args:
            orgid: Organization ID
            test_id: Test ID
            run_id: Run ID
            user_token: User authentication token

        Returns:
            Dict containing test run status
        """
        try:
            result = await self.client.make_request(
                "GET", f"/evaluations/test/status/{orgid}/{test_id}/{run_id}"
            )
            logger.info(
                f"Retrieved test status for run {run_id} for test {test_id} in org {orgid} : {result}"
            )
            return result
        except Exception as e:
            capture_exception(e)
            logger.error(
                f"Failed to get test status for run {run_id} for test {test_id} in org {orgid}: {str(e)}"
            )
            raise
