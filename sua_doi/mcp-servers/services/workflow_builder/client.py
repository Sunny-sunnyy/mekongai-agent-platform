"""
Workflow Builder Service Client.

Provides the WorkflowBuilderService abstraction used by workflow_builder_server.py.
Core workflow operations (build, assemble, lint, publish) call the absorbed SDK
and agent modules directly. Catalog and execution endpoints still call the
agentic-backend API over HTTP.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastmcp import Context

from logging_config import get_logger
from services.workflow_builder.agent import WorkflowAgent
from services.workflow_builder.job_store import create_workflow_store

# Absorbed SDK modules — direct function calls, no HTTP hop
from services.workflow_builder.sdk import (
    Workflow,
    _load_catalog,
    _require_successful_publish_response,
)
from services.workflow_builder.sdk import (
    publish_workflow as sdk_publish_workflow,
)
from services.workflow_builder.sdk import (
    publish_workflow_local as sdk_publish_workflow_local,
)
from services.workflow_builder.sdk import (
    register_node as sdk_register_node,
)
from services.workflow_builder.sdk import (
    update_node as sdk_update_node,
)
from settings import settings

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30.0
BUILD_TIMEOUT = 180.0
PUBLISH_TIMEOUT = 180.0

OUTPUT_DIR = settings.WORKFLOW_OUTPUT_DIR
DEFAULT_MODEL = settings.DEFAULT_MODEL

_workflow_store = create_workflow_store()

# Shared httpx client for agentic-backend calls
_http_client: httpx.AsyncClient | None = None


def _get_http_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    """Return the shared httpx.AsyncClient, creating it on first use."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=timeout)
    return _http_client


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _persist_workflow_to_store(workflow_path: str) -> None:
    """Read a workflow JSON file from disk and store it in the workflow store."""
    try:
        path = Path(workflow_path)
        if path.exists() and path.suffix == ".json":
            data = json.loads(path.read_text())
            _workflow_store.save(path.name, data)
            logger.info("Stored workflow '%s' in workflow store", path.name)
    except Exception as e:
        logger.warning("Failed to store workflow in workflow store: %s", e)


class WorkflowBuilderService:
    """Service for building, assembling, and publishing workflows.

    Core operations (build, assemble, lint, publish) use the absorbed SDK
    directly. Catalog browsing and execution use the agentic-backend API.
    """

    def __init__(
        self,
        ctx: Context,
        user_email: Optional[str] = None,
        conversation_id: Optional[str] = None,
        auth_token: Optional[str] = None,
        org_id: Optional[str] = None,
    ):
        self.ctx = ctx
        self._user_email = user_email
        self._conversation_id = conversation_id
        self._auth_token = auth_token
        self._org_id = org_id

    async def _publish_credentials(self) -> dict:
        """Return merged credentials for build+publish endpoints."""
        creds = {}
        if settings.CAPITOL_BASE_URL:
            creds["base_url"] = settings.CAPITOL_BASE_URL
        if settings.effective_agentic_backend_url:
            creds["agentic_backend_url"] = settings.effective_agentic_backend_url
        if settings.effective_platform_frontend_url:
            creds["platform_frontend_url"] = settings.effective_platform_frontend_url

        if self._auth_token:
            creds["auth_token"] = self._auth_token
        if self._user_email:
            creds["user_email"] = self._user_email
        if self._org_id:
            creds["org_id"] = self._org_id

        logger.info(
            "_publish_credentials: has_auth_token=%s, has_email=%s, has_org_id=%s, org_id=%s",
            bool(self._auth_token),
            "user_email" in creds,
            bool(self._org_id),
            self._org_id or "MISSING",
        )
        return creds

    def _auth_headers(self) -> dict:
        """Build auth headers for agentic-backend calls."""
        headers = {}
        if self._auth_token:
            headers["X-User-Token"] = self._auth_token
        if self._org_id:
            headers["X-Org-Id"] = self._org_id
        if settings.effective_agentic_backend_url:
            headers["X-Agentic-Backend-Url"] = settings.effective_agentic_backend_url
        return headers

    def _set_workflow_url(self, result: dict) -> dict:
        """Set workflow_url using the platform frontend base URL."""
        workflow_id = result.get("workflow_id")
        if not workflow_id:
            return result

        existing = result.get("workflow_url", "")
        if existing and existing.startswith("http"):
            return result

        base = settings.effective_platform_frontend_url.rstrip("/")
        if base:
            result["workflow_url"] = f"{base}/workflow/{workflow_id}"
        else:
            result["workflow_url"] = f"/workflow/{workflow_id}"
        return result

    # ── Catalog (agentic-backend HTTP) ──────────────────────────────────

    async def list_nodes(self, node_type: Optional[str] = None) -> Dict[str, Any]:
        """List available node types from the agentic-backend filtered catalog."""
        if not self._auth_token or not self._org_id:
            return {
                "success": False,
                "error": "Authentication required (auth_token and org_id)",
                "nodes": [],
                "count": 0,
            }

        agentic_url = settings.effective_agentic_backend_url
        url = f"{agentic_url.rstrip('/')}/nodes/{self._org_id}/list-nodes"
        headers = {"X-User-Token": self._auth_token}
        logger.info("[list_nodes] POST %s (org_id=%s)", url, self._org_id)

        client = _get_http_client()
        response = await client.post(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        catalog = data.get("catalog", [])
        if node_type:
            catalog = [n for n in catalog if n.get("node_type") == node_type]

        nodes = [
            {
                "node_id": n["node_id"],
                "display_name": n.get("display_name", n.get("node_id")),
                "node_type": n.get("node_type", ""),
                "description": n.get("description", ""),
                "params": [p["field_id"] for p in n.get("params", [])],
            }
            for n in catalog
        ]
        return {"success": True, "count": len(nodes), "nodes": nodes}

    async def inspect_node(self, node_id: str) -> Dict[str, Any]:
        """Get full details for a single node type from the agentic-backend catalog."""
        if not self._auth_token or not self._org_id:
            return {
                "success": False,
                "error": "Authentication required (auth_token and org_id)",
            }

        agentic_url = settings.effective_agentic_backend_url
        url = f"{agentic_url.rstrip('/')}/nodes/{self._org_id}/list-nodes"
        headers = {"X-User-Token": self._auth_token}

        client = _get_http_client()
        response = await client.post(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        catalog = data.get("catalog", [])
        for node in catalog:
            if node.get("node_id") == node_id:
                return {"success": True, "node": node}

        available_ids = sorted({n.get("node_id", "?") for n in catalog})
        return {
            "success": False,
            "error": f"Node '{node_id}' not found. Available: {', '.join(available_ids)}",
        }

    # ── Node registration (direct SDK) ──────────────────────────────────

    async def register_node(self, node_def: Dict[str, Any]) -> Dict[str, Any]:
        """Refuse runtime-only registration without a deployed implementation.

        The live catalog is loaded first so the SDK can distinguish an existing
        node from a new ID, but neither may be replaced by an implementation-free
        runtime definition.
        """
        logger.info("[register_node] node_id=%s", node_def.get("node_id"))
        try:
            catalog = await asyncio.to_thread(
                _load_catalog,
                auth_token=self._auth_token,
                org_id=self._org_id,
                base_url=settings.effective_agentic_backend_url,
            )
            entry = sdk_register_node(node_def, catalog=catalog)
            return {"success": True, "node": entry}
        except (ValueError, RuntimeError) as e:
            return {"success": False, "error": str(e)}

    async def update_node(
        self, node_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update an existing node type in the runtime catalog."""
        logger.info("[update_node] node_id=%s keys=%s", node_id, list(updates.keys()))
        try:
            entry = sdk_update_node(
                node_id,
                updates,
                auth_token=self._auth_token,
                org_id=self._org_id,
                base_url=settings.effective_agentic_backend_url,
            )
            return {"success": True, "node": entry}
        except (KeyError, ValueError, RuntimeError) as e:
            return {"success": False, "error": str(e)}

    # ── Build (direct agent call) ───────────────────────────────────────

    async def build_workflow(
        self,
        prompt: str,
        model: str = DEFAULT_MODEL,
        max_turns: int = 30,
        images: list | None = None,
    ) -> Dict[str, Any]:
        """Build and publish a workflow synchronously using the absorbed agent."""
        logger.info(
            "Building workflow with model=%s, prompt=%s...", model, prompt[:100]
        )

        if not settings.ANTHROPIC_API_KEY:
            return {"success": False, "error": "ANTHROPIC_API_KEY not configured"}

        creds = await self._publish_credentials()
        agentic_url = (
            creds.get("agentic_backend_url") or settings.effective_agentic_backend_url
        )

        # Save images to temp files if provided
        image_paths: list[str] | None = None
        temp_dir: str | None = None
        if images:
            temp_dir = tempfile.mkdtemp(prefix="workflow_images_")
            image_paths = []
            for img_path in images:
                filename = os.path.basename(img_path)
                dest = os.path.join(temp_dir, filename)
                with open(img_path, "rb") as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                image_paths.append(dest)

        try:
            # WorkflowAgent.run() is synchronous — run in a thread.
            # Enforce a wall-clock timeout so runaway agent loops (e.g. 30
            # turns with slow API calls) don't block the MCP tool indefinitely.
            agent = WorkflowAgent(
                model=model,
                output_dir=OUTPUT_DIR,
                verbose=True,
                max_turns=max_turns,
                user_email=self._user_email,
                org_id=self._org_id,
                auth_token=self._auth_token,
                agentic_backend_url=agentic_url,
            )
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(agent.run, prompt, images=image_paths),
                    timeout=BUILD_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Build timed out after %ss for prompt: %s...",
                    BUILD_TIMEOUT,
                    prompt[:100],
                )
                return {
                    "success": False,
                    "error": f"Build timed out after {int(BUILD_TIMEOUT)}s. Try a simpler workflow or break it into steps.",
                }

            workflow_path = result.get("workflow_path")
            if not workflow_path:
                return {
                    "success": False,
                    "message": "Build failed",
                    "error": "No workflow file created",
                }

            _persist_workflow_to_store(workflow_path)

            # Publish
            try:
                if agentic_url:
                    pub_result = await asyncio.to_thread(
                        sdk_publish_workflow_local,
                        workflow_path,
                        agentic_backend_url=agentic_url,
                        platform_frontend_url=creds.get("platform_frontend_url"),
                        org_id=self._org_id,
                        auth_token=self._auth_token,
                    )
                else:
                    pub_result = await asyncio.to_thread(
                        sdk_publish_workflow,
                        workflow_path,
                        org_id=self._org_id,
                        auth_token=self._auth_token,
                    )

                screenshot_data = None
                try:
                    wf_obj = Workflow.load(workflow_path)
                    screenshot_data = wf_obj.generate_screenshot_svg()
                except Exception:
                    pass

                out = {
                    "success": True,
                    "workflow_id": pub_result.get("workflow_id"),
                    "workflow_path": workflow_path,
                    "screenshot": screenshot_data,
                    "org_id": pub_result.get("orgid"),
                    "node_count": pub_result.get(
                        "node_count", len(result.get("nodes", []))
                    ),
                    "edge_count": pub_result.get(
                        "edge_count", len(result.get("edges", []))
                    ),
                    "lint_issues": result.get("lint_results", []),
                    "summary": result.get("summary", ""),
                    "message": "Workflow built and published successfully",
                }
                self._set_workflow_url(out)
                return out
            except Exception as pub_err:
                logger.error("Publish failed: %s", pub_err)
                return {
                    "success": False,
                    "error": f"Workflow built but publish failed: {pub_err}",
                }

        except Exception as e:
            logger.error("Build failed: %s\n%s", e, traceback.format_exc())
            return {"success": False, "error": f"Build failed: {e}"}
        finally:
            if temp_dir and os.path.exists(temp_dir):
                import shutil

                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass

    # ── Workflow CRUD (direct SDK / workflow store) ──────────────────────

    async def list_workflows(self) -> Dict[str, Any]:
        """List saved workflow JSON files from the workflow store."""
        filenames = _workflow_store.list_all()
        workflows = []
        for filename in filenames:
            if filename == "list-nodes.json":
                continue
            data = _workflow_store.get(filename)
            if data is None:
                continue
            raw = json.dumps(data).encode("utf-8")
            workflows.append(
                {
                    "filename": filename,
                    "size_bytes": len(raw),
                    "name": data.get("name"),
                    "node_count": len(data.get("nodes", [])),
                    "edge_count": len(data.get("edges", [])),
                }
            )

        # Fallback: scan local disk
        if not workflows:
            output_path = Path(OUTPUT_DIR).resolve()
            if output_path.exists():
                for f in sorted(output_path.glob("*.json")):
                    if f.name == "list-nodes.json":
                        continue
                    try:
                        data = json.loads(f.read_text())
                        workflows.append(
                            {
                                "filename": f.name,
                                "size_bytes": f.stat().st_size,
                                "name": data.get("name"),
                                "node_count": len(data.get("nodes", [])),
                                "edge_count": len(data.get("edges", [])),
                            }
                        )
                    except (json.JSONDecodeError, OSError):
                        workflows.append(
                            {"filename": f.name, "size_bytes": f.stat().st_size}
                        )

        return {"success": True, "count": len(workflows), "workflows": workflows}

    async def list_published_workflows(self) -> Dict[str, Any]:
        """List published workflows for the user's org from agentic-backend."""
        if not self._org_id:
            return {
                "success": False,
                "error": "org_id is required",
                "workflows": [],
                "count": 0,
            }
        if not self._auth_token:
            return {
                "success": False,
                "error": "Authentication token is required",
                "workflows": [],
                "count": 0,
            }

        agentic_url = settings.effective_agentic_backend_url
        url = f"{agentic_url.rstrip('/')}/workflow/{self._org_id}/list-workflows"
        headers = {"X-User-Token": self._auth_token}

        logger.info("[list_published_workflows] GET %s", url)
        client = _get_http_client()
        response = await client.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return response.json()

    async def get_workflow(self, filename: str) -> Dict[str, Any]:
        """Get full JSON content of a saved workflow."""
        data = _workflow_store.get(filename)
        if data is not None:
            return {"success": True, "filename": filename, "workflow": data}

        # Fallback: local disk
        filepath = Path(OUTPUT_DIR).resolve() / filename
        if filepath.exists() and filepath.suffix == ".json":
            data = json.loads(filepath.read_text())
            return {"success": True, "filename": filename, "workflow": data}

        return {"success": False, "error": f"Workflow '{filename}' not found"}

    async def lint_workflow(self, filename: str) -> Dict[str, Any]:
        """Lint a saved workflow and return any issues."""
        data = _workflow_store.get(filename)
        if data is None:
            filepath = Path(OUTPUT_DIR).resolve() / filename
            if filepath.exists() and filepath.suffix == ".json":
                data = json.loads(filepath.read_text())
            else:
                return {"success": False, "error": f"Workflow '{filename}' not found"}

        # Write to a temp file for Workflow.load()
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", prefix=f"lint_{filename}_", delete=False
        )
        try:
            tmp.write(json.dumps(data, indent=2).encode("utf-8"))
            tmp.close()

            wf = Workflow.load(tmp.name)
            issues = wf.lint()
            errors = [i for i in issues if i.startswith("ERROR")]
            warnings = [i for i in issues if i.startswith("WARNING")]

            return {
                "success": len(errors) == 0,
                "filename": filename,
                "issues": issues,
                "error_count": len(errors),
                "warning_count": len(warnings),
            }
        except Exception as e:
            return {"success": False, "error": f"Lint failed: {e}"}
        finally:
            os.unlink(tmp.name)

    async def publish_workflow(self, filename: str) -> Dict[str, Any]:
        """Publish a saved workflow to Capitol Platform via the local publish path."""
        data = _workflow_store.get(filename)
        if data is None:
            filepath = Path(OUTPUT_DIR).resolve() / filename
            if filepath.exists() and filepath.suffix == ".json":
                data = json.loads(filepath.read_text())
            else:
                return {"success": False, "error": f"Workflow '{filename}' not found"}

        # Write to temp file for the publish function
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", prefix=f"pub_{filename}_", delete=False
        )
        try:
            tmp.write(json.dumps(data, indent=2).encode("utf-8"))
            tmp.close()

            agentic_url = settings.effective_agentic_backend_url
            if agentic_url:
                result = await asyncio.to_thread(
                    sdk_publish_workflow_local,
                    tmp.name,
                    agentic_backend_url=agentic_url,
                    platform_frontend_url=settings.effective_platform_frontend_url,
                    org_id=self._org_id,
                    auth_token=self._auth_token,
                )
            else:
                result = await asyncio.to_thread(
                    sdk_publish_workflow,
                    tmp.name,
                    org_id=self._org_id,
                    auth_token=self._auth_token,
                )

            self._set_workflow_url(result)
            return result
        except Exception as e:
            logger.error("Publish failed: %s", e)
            raise
        finally:
            os.unlink(tmp.name)

    # ── Assemble (direct SDK, no LLM) ──────────────────────────────────

    async def assemble_workflow(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Assemble a workflow from a structured spec — instant, no LLM."""
        import time as _time

        try:
            t_start = _time.monotonic()

            auth_kwargs = {
                "auth_token": spec.get("auth_token") or self._auth_token,
                "org_id": spec.get("org_id") or self._org_id,
                "base_url": settings.effective_agentic_backend_url,
            }
            logger.info(
                "[ASSEMBLE] auth: has_token=%s, org_id=%s",
                bool(auth_kwargs["auth_token"]),
                auth_kwargs["org_id"] or "MISSING",
            )

            # _load_catalog is synchronous HTTP — run in a thread
            catalog = await asyncio.to_thread(_load_catalog, **auth_kwargs)
            logger.info(
                "[ASSEMBLE] catalog loaded in %.1fs", _time.monotonic() - t_start
            )

            name = spec.get("name", "Untitled Workflow")
            description = spec.get("description", "")
            wf = Workflow(name, description=description, catalog=catalog)

            # Add nodes
            node_specs = spec.get("nodes", [])
            node_instances = []
            node_errors = []
            for i, node_spec in enumerate(node_specs):
                pos = tuple(node_spec.get("position", [i * 400, 200]))
                try:
                    node = wf.add_node(node_spec["node_id"], position=pos)
                except (ValueError, KeyError) as e:
                    node_errors.append(
                        f"Node {i} ({node_spec.get('node_id', '?')}): {e}"
                    )
                    continue

                for field_id, value in node_spec.get("params", {}).items():
                    try:
                        node.set_param(field_id, value)
                    except Exception as e:
                        node_errors.append(f"Node {i} param '{field_id}': {e}")

                composio_apps = node_spec.get("composio_apps")
                if composio_apps:
                    node.set_param("composio_apps", composio_apps)

                user_config_name = node_spec.get("user_config_name")
                if user_config_name:
                    node.set_display(
                        user_config_name, node_spec.get("user_config_description", "")
                    )

                node_instances.append(node)

            if node_errors:
                return {
                    "success": False,
                    "message": "Assembly failed: node errors",
                    "error": "; ".join(node_errors),
                    "lint_issues": node_errors,
                }

            # Wire edges
            edge_specs = spec.get("edges", [])
            edge_errors = []
            for j, edge_spec in enumerate(edge_specs):
                # Edge dicts use "from"/"to" keys (aliased from from_node/to_node)
                from_idx = edge_spec.get("from", edge_spec.get("from_node", 0))
                to_idx = edge_spec.get("to", edge_spec.get("to_node", 0))
                if (
                    type(from_idx) is not int
                    or type(to_idx) is not int
                    or from_idx < 0
                    or to_idx < 0
                    or from_idx >= len(node_instances)
                    or to_idx >= len(node_instances)
                ):
                    edge_errors.append(f"Edge {j}: index out of range")
                    continue
                source = node_instances[from_idx]
                target = node_instances[to_idx]
                try:
                    wf.connect(
                        source, target, target_param=edge_spec.get("target_param")
                    )
                except Exception as e:
                    edge_errors.append(f"Edge {j}: {e}")

            if edge_errors:
                return {
                    "success": False,
                    "message": "Assembly failed: edge wiring errors",
                    "error": "; ".join(edge_errors),
                    "lint_issues": edge_errors,
                }

            # Build and lint the complete graph before any side effect.
            issues = wf.lint()
            errors = [i for i in issues if i.startswith("ERROR")]
            warnings = [i for i in issues if i.startswith("WARNING")]

            validation_result = {
                "success": len(errors) == 0,
                "node_count": len(node_instances),
                "edge_count": len(edge_specs),
                "lint_errors": len(errors),
                "lint_warnings": len(warnings),
                "lint_issues": issues,
                "message": (
                    "Validation passed"
                    if not errors
                    else f"Validation failed with {len(errors)} error(s)"
                ),
            }
            if spec.get("validate_only"):
                return validation_result

            if errors:
                return {
                    **validation_result,
                    "success": False,
                    "error": "Workflow has lint errors and was not saved or published.",
                }

            # Save
            safe_name = name.lower().replace(" ", "_")[:50]
            filename = f"{safe_name}.json"
            filepath = os.path.join(OUTPUT_DIR, filename)
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            wf.save(filepath)
            _persist_workflow_to_store(filepath)

            # Publish if requested
            workflow_id = None
            workflow_url = None
            publish = spec.get("publish", False)
            if publish:
                try:
                    agentic_url = settings.effective_agentic_backend_url
                    if agentic_url:
                        pub_result = await asyncio.to_thread(
                            sdk_publish_workflow_local,
                            filepath,
                            agentic_backend_url=agentic_url,
                            org_id=auth_kwargs["org_id"],
                            auth_token=auth_kwargs["auth_token"],
                        )
                    else:
                        pub_result = await asyncio.to_thread(
                            sdk_publish_workflow,
                            filepath,
                            org_id=auth_kwargs["org_id"],
                            auth_token=auth_kwargs["auth_token"],
                        )
                    workflow_id = pub_result.get("workflow_id")
                    workflow_url = pub_result.get("workflow_url")
                except Exception as pub_err:
                    logger.error("Assemble publish failed: %s", pub_err)
                    return {
                        "success": False,
                        "filename": filename,
                        "node_count": len(node_instances),
                        "edge_count": len(edge_specs),
                        "lint_errors": len(errors),
                        "lint_warnings": len(warnings),
                        "lint_issues": issues,
                        "error": f"Workflow assembled but publish failed: {pub_err}",
                    }
                if pub_result.get("success") is False:
                    return {
                        "success": False,
                        "workflow_id": pub_result.get("workflow_id"),
                        "workflow_url": pub_result.get("workflow_url"),
                        "filename": filename,
                        "node_count": len(node_instances),
                        "edge_count": len(edge_specs),
                        "lint_errors": len(errors),
                        "lint_warnings": len(warnings),
                        "lint_issues": issues,
                        "error": "Workflow assembled but publishing failed.",
                    }

            total_elapsed = _time.monotonic() - t_start
            logger.info(
                "[ASSEMBLE] total=%.1fs nodes=%d publish=%s",
                total_elapsed,
                len(node_instances),
                publish,
            )

            msg = (
                "Workflow assembled and published"
                if publish
                else (
                    f"Workflow assembled with {len(node_instances)} nodes and {len(edge_specs)} edges. "
                    f"Lint: {len(errors)} errors, {len(warnings)} warnings. "
                    f"Call publish_workflow(filename='{filename}') to publish."
                )
            )
            result = {
                "success": True,
                "workflow_id": workflow_id,
                "filename": filename,
                "node_count": len(node_instances),
                "edge_count": len(edge_specs),
                "lint_errors": len(errors),
                "lint_warnings": len(warnings),
                "lint_issues": issues,
                "message": msg,
            }
            if workflow_url:
                result["workflow_url"] = workflow_url
            self._set_workflow_url(result)
            return result

        except ValueError as e:
            error_msg = str(e)
            if "ALL required for catalog fetch" in error_msg:
                return {
                    "success": False,
                    "message": "Authentication required",
                    "error": (
                        "The workflow catalog requires authentication credentials "
                        "(auth_token, org_id, base_url) but they were not provided. "
                        "Ask the user to refresh the page and try again."
                    ),
                }
            return {"success": False, "message": "Assembly failed", "error": error_msg}
        except Exception as e:
            tb = traceback.format_exc()
            error_msg = (
                str(e)
                or f"{type(e).__name__}: {tb.splitlines()[-1] if tb else 'unknown'}"
            )
            logger.error("Assemble failed: %s\n%s", error_msg, tb)
            return {"success": False, "message": "Assembly failed", "error": error_msg}

    # ── Edit (fetch existing → patch → republish) ───────────────────────

    async def _fetch_published_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Fetch a published workflow's payload from agentic-backend.

        Returns a dict with either {"success": True, "payload": {...}, "name": str,
        "description": str} or {"success": False, "error": str}.
        """
        if not self._org_id or not self._auth_token:
            return {
                "success": False,
                "error": "Authentication required (org_id and auth_token)",
            }
        agentic_url = settings.effective_agentic_backend_url.rstrip("/")
        # [VÁ CỦA MÌNH 03/11/2026 — chay_thu/sua_doi/README.md] gốc: GET /workflow/{org}/{id} (đường cũ, đọc qua platform-api ⇒ máy mình 401).
        # Đường v1 đọc thẳng PostgreSQL, trả cùng khuôn {"workflow": {"payload": ...}} (đo curl khoảng 19:53 03/11).
        url = f"{agentic_url}/api/v1/orgs/{self._org_id}/workflows/{workflow_id}"
        headers = {"X-User-Token": self._auth_token}
        logger.info("[edit_workflow] GET %s", url)
        client = _get_http_client()
        try:
            resp = await client.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        except httpx.HTTPError as e:
            return {
                "success": False,
                "error": f"Failed to fetch workflow {workflow_id}: {e}",
            }
        if resp.status_code == 404:
            return {
                "success": False,
                "error": (
                    f"Workflow '{workflow_id}' not found in org '{self._org_id}'. "
                    "Use list_published_workflows to see available workflow_ids."
                ),
            }
        if resp.status_code == 403:
            return {
                "success": False,
                "error": (
                    f"Access denied for workflow '{workflow_id}' in org "
                    f"'{self._org_id}'. You may not have permission to edit this "
                    "workflow, or it belongs to a different organization."
                ),
            }
        try:
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return {
                "success": False,
                "error": f"Fetch failed ({resp.status_code}): {e}",
            }
        data = resp.json()
        wf_obj = data.get("workflow", {}) or {}
        payload = wf_obj.get("payload") or wf_obj
        if not payload:
            return {
                "success": False,
                "error": "Workflow fetched but payload was empty.",
            }
        return {
            "success": True,
            "payload": payload,
            "name": wf_obj.get("name") or payload.get("name") or "",
            "description": (
                wf_obj.get("description") or payload.get("description") or ""
            ),
        }

    async def edit_workflow(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Edit an existing published workflow.

        Fetches the workflow by ``workflow_id``, applies the supplied edits,
        and re-publishes via the same persist-workflow endpoint (which
        updates the existing record because ``payload.id`` is preserved).

        Supported edits (all optional except ``workflow_id``):
          - ``name``: replace workflow name
          - ``description``: replace workflow description
          - ``nodes`` + ``edges``: full replacement of the node/edge graph.
            When provided, the workflow is rebuilt from scratch using the
            same code path as ``assemble_workflow``, preserving only the
            workflow_id. Both must be provided together to avoid stranding
            edges that reference removed nodes.
        """
        import time as _time

        workflow_id = spec.get("workflow_id")
        if not workflow_id:
            return {"success": False, "error": "workflow_id is required"}

        new_nodes = spec.get("nodes")
        new_edges = spec.get("edges")
        new_name = spec.get("name")
        new_description = spec.get("description")

        graph_supplied = new_nodes is not None or new_edges is not None
        if graph_supplied and (new_nodes is None or new_edges is None):
            return {
                "success": False,
                "error": (
                    "When editing the graph you must supply BOTH `nodes` and `edges` "
                    "together — partial graph updates are not supported because "
                    "edges reference nodes by index. To edit only metadata, omit "
                    "both nodes and edges and pass `name`/`description`."
                ),
            }

        if not graph_supplied and new_name is None and new_description is None:
            return {
                "success": False,
                "error": (
                    "No edits supplied. Provide at least one of: name, description, "
                    "or both nodes+edges."
                ),
            }

        validate_only = bool(spec.get("validate_only"))

        # Always fetch first, including graph dry-runs, to prove the target
        # exists and the caller is authorized to edit it. This is read-only.
        fetched = await self._fetch_published_workflow(workflow_id)
        if not fetched.get("success"):
            return fetched

        existing_payload = fetched["payload"]
        effective_name = new_name if new_name is not None else fetched["name"]
        effective_description = (
            new_description if new_description is not None else fetched["description"]
        )

        # Step 2a: metadata-only edit — patch the existing payload in place
        # so node positions, port wiring, and runtime config are untouched.
        if not graph_supplied:
            patched = dict(existing_payload)
            patched["id"] = workflow_id
            if new_name is not None:
                patched["name"] = new_name
            if new_description is not None:
                patched["description"] = new_description

            try:
                agentic_url = settings.effective_agentic_backend_url.rstrip("/")
                # [VÁ CỦA MÌNH 03/11/2026] gốc: /workflow/{org}/persist-workflow — đổi sang đường lưu v1 như sdk.py.
                url = f"{agentic_url}/api/v1/orgs/{self._org_id}/workflows"
                headers = {
                    "Content-Type": "application/json",
                    "X-User-Token": self._auth_token,
                }
                client = _get_http_client()
                resp = await client.post(
                    url,
                    json=patched,
                    headers=headers,
                    timeout=PUBLISH_TIMEOUT,
                )
                resp.raise_for_status()
                persist_result = _require_successful_publish_response(resp.json())
                if persist_result["workflow_id"] != workflow_id:
                    raise RuntimeError(
                        "persist-workflow returned a different workflow_id"
                    )
            except (httpx.HTTPError, ValueError, RuntimeError) as e:
                return {
                    "success": False,
                    "error": f"persist-workflow failed for metadata edit: {e}",
                }

            result = {
                "success": True,
                "workflow_id": workflow_id,
                "name": effective_name,
                "description": effective_description,
                "node_count": len(patched.get("nodes", [])),
                "edge_count": len(patched.get("edges", [])),
                "edited_fields": [
                    f
                    for f, v in [
                        ("name", new_name),
                        ("description", new_description),
                    ]
                    if v is not None
                ],
                "message": "Workflow metadata updated.",
            }
            self._set_workflow_url(result)
            return result

        # Step 2b: graph edit — rebuild the workflow from the supplied spec
        # using the same path as assemble_workflow, then override the id so
        # the backend treats this as an update of the existing workflow.
        try:
            t_start = _time.monotonic()

            auth_kwargs = {
                "auth_token": spec.get("auth_token") or self._auth_token,
                "org_id": spec.get("org_id") or self._org_id,
                "base_url": settings.effective_agentic_backend_url,
            }
            logger.info(
                "[EDIT] graph rebuild: has_token=%s, org_id=%s, workflow_id=%s",
                bool(auth_kwargs["auth_token"]),
                auth_kwargs["org_id"] or "MISSING",
                workflow_id,
            )

            catalog = await asyncio.to_thread(_load_catalog, **auth_kwargs)
            wf = Workflow(
                effective_name or "Untitled Workflow",
                description=effective_description or "",
                catalog=catalog,
            )
            wf.id = workflow_id

            node_instances = []
            node_errors: list[str] = []
            for i, node_spec in enumerate(new_nodes):
                pos = tuple(node_spec.get("position", [i * 400, 200]))
                try:
                    node = wf.add_node(node_spec["node_id"], position=pos)
                except (ValueError, KeyError) as e:
                    node_errors.append(
                        f"Node {i} ({node_spec.get('node_id', '?')}): {e}"
                    )
                    continue

                for field_id, value in node_spec.get("params", {}).items():
                    try:
                        node.set_param(field_id, value)
                    except Exception as e:
                        node_errors.append(f"Node {i} param '{field_id}': {e}")

                composio_apps = node_spec.get("composio_apps")
                if composio_apps:
                    node.set_param("composio_apps", composio_apps)

                user_config_name = node_spec.get("user_config_name")
                if user_config_name:
                    node.set_display(
                        user_config_name,
                        node_spec.get("user_config_description", ""),
                    )

                node_instances.append(node)

            if node_errors:
                return {
                    "success": False,
                    "message": "Edit failed: node errors",
                    "error": "; ".join(node_errors),
                    "lint_issues": node_errors,
                }

            edge_errors: list[str] = []
            for j, edge_spec in enumerate(new_edges):
                from_idx = edge_spec.get("from", edge_spec.get("from_node", 0))
                to_idx = edge_spec.get("to", edge_spec.get("to_node", 0))
                # Reject negative indices explicitly: Python's negative indexing
                # would otherwise silently wire the edge to the wrong node.
                if (
                    type(from_idx) is not int
                    or type(to_idx) is not int
                    or from_idx < 0
                    or to_idx < 0
                    or from_idx >= len(node_instances)
                    or to_idx >= len(node_instances)
                ):
                    edge_errors.append(f"Edge {j}: index out of range")
                    continue
                source = node_instances[from_idx]
                target = node_instances[to_idx]
                try:
                    wf.connect(
                        source,
                        target,
                        target_param=edge_spec.get("target_param"),
                    )
                except Exception as e:
                    edge_errors.append(f"Edge {j}: {e}")

            if edge_errors:
                return {
                    "success": False,
                    "message": "Edit failed: edge wiring errors",
                    "error": "; ".join(edge_errors),
                    "lint_issues": edge_errors,
                }

            issues = wf.lint()
            errors = [i for i in issues if i.startswith("ERROR")]
            warnings = [i for i in issues if i.startswith("WARNING")]

            if validate_only:
                return {
                    "success": len(errors) == 0,
                    "workflow_id": workflow_id,
                    "node_count": len(node_instances),
                    "edge_count": len(new_edges),
                    "lint_errors": len(errors),
                    "lint_warnings": len(warnings),
                    "lint_issues": issues,
                    "message": (
                        "Edit validation passed"
                        if not errors
                        else f"Edit validation failed with {len(errors)} error(s)"
                    ),
                }

            if errors:
                return {
                    "success": False,
                    "workflow_id": workflow_id,
                    "node_count": len(node_instances),
                    "edge_count": len(new_edges),
                    "lint_errors": len(errors),
                    "lint_warnings": len(warnings),
                    "lint_issues": issues,
                    "error": "Workflow has lint errors and was not saved or published.",
                }

            # Persist to disk so the file-backed publish path can read it.
            safe_name = (effective_name or workflow_id).lower().replace(" ", "_")[:50]
            filename = f"{safe_name}.json"
            filepath = os.path.join(OUTPUT_DIR, filename)
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            wf.save(filepath)
            _persist_workflow_to_store(filepath)

            try:
                agentic_url = settings.effective_agentic_backend_url
                if agentic_url:
                    pub_result = await asyncio.to_thread(
                        sdk_publish_workflow_local,
                        filepath,
                        agentic_backend_url=agentic_url,
                        org_id=auth_kwargs["org_id"],
                        auth_token=auth_kwargs["auth_token"],
                    )
                else:
                    pub_result = await asyncio.to_thread(
                        sdk_publish_workflow,
                        filepath,
                        org_id=auth_kwargs["org_id"],
                        auth_token=auth_kwargs["auth_token"],
                    )
            except Exception as pub_err:
                logger.error("Edit publish failed: %s", pub_err)
                return {
                    "success": False,
                    "workflow_id": workflow_id,
                    "filename": filename,
                    "node_count": len(node_instances),
                    "edge_count": len(new_edges),
                    "lint_errors": len(errors),
                    "lint_warnings": len(warnings),
                    "lint_issues": issues,
                    "error": f"Workflow rebuilt but publish failed: {pub_err}",
                }
            if pub_result.get("success") is False:
                return {
                    "success": False,
                    "workflow_id": pub_result.get("workflow_id") or workflow_id,
                    "workflow_url": pub_result.get("workflow_url"),
                    "filename": filename,
                    "node_count": len(node_instances),
                    "edge_count": len(new_edges),
                    "lint_errors": len(errors),
                    "lint_warnings": len(warnings),
                    "lint_issues": issues,
                    "error": "Workflow rebuilt but publishing failed.",
                }

            total_elapsed = _time.monotonic() - t_start
            logger.info(
                "[EDIT] total=%.1fs workflow_id=%s nodes=%d",
                total_elapsed,
                workflow_id,
                len(node_instances),
            )

            # Defensive check: the persist endpoint may assign a new id if
            # the supplied id was rejected (e.g. permission scope). Warn the
            # caller so they don't lose track of the workflow.
            returned_id = pub_result.get("workflow_id") or workflow_id
            result = {
                "success": True,
                "workflow_id": returned_id,
                "filename": filename,
                "name": effective_name,
                "description": effective_description,
                "node_count": len(node_instances),
                "edge_count": len(new_edges),
                "lint_errors": len(errors),
                "lint_warnings": len(warnings),
                "lint_issues": issues,
                "edited_fields": [
                    f
                    for f, v in [
                        ("name", new_name),
                        ("description", new_description),
                        ("nodes", new_nodes),
                        ("edges", new_edges),
                    ]
                    if v is not None
                ],
                "message": (
                    "Workflow edited and republished."
                    if returned_id == workflow_id
                    else (
                        f"Workflow republished but backend returned a new id "
                        f"({returned_id}) — original id was {workflow_id}."
                    )
                ),
            }
            if pub_result.get("workflow_url"):
                result["workflow_url"] = pub_result["workflow_url"]
            self._set_workflow_url(result)
            return result

        except ValueError as e:
            error_msg = str(e)
            if "ALL required for catalog fetch" in error_msg:
                return {
                    "success": False,
                    "message": "Authentication required",
                    "error": (
                        "The workflow catalog requires authentication credentials "
                        "(auth_token, org_id, base_url) but they were not provided. "
                        "Ask the user to refresh the page and try again."
                    ),
                }
            return {"success": False, "message": "Edit failed", "error": error_msg}
        except Exception as e:
            tb = traceback.format_exc()
            error_msg = (
                str(e)
                or f"{type(e).__name__}: {tb.splitlines()[-1] if tb else 'unknown'}"
            )
            logger.error("Edit failed: %s\n%s", error_msg, tb)
            return {"success": False, "message": "Edit failed", "error": error_msg}

    # ── Execution (agentic-backend HTTP) ────────────────────────────────

    async def execute_workflow(
        self,
        workflow_id: str,
        input_overrides: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Trigger a published workflow run via agentic-backend."""
        # [ghi chú] Trợ lý dựng CHỈ DỰNG + LƯU, không tự chạy (tài liệu kỹ thuật câu 12 / M5): hàm này gọi THẲNG
        # ab-api …/async-run-workflow trong mạng Docker (không qua cổng gác 18300, không qua giữ chỗ tiền của trang) ⇒ lượt AI ngoài sổ. Từ chối bằng
        # code, chữ rõ cho trợ lý chuyển lời người dùng. Mở lại = bỏ khối này (cần A + E: phải có giữ chỗ / ghi sổ trên đường này trước).
        return {
            "success": False,
            "error": ("Máy thử: trợ lý dựng workflow CHỈ dựng và lưu, KHÔNG tự chạy. Hãy mở workflow trên trang 18400 và bấm Chạy "
                      "(trang giữ chỗ tiền + ghi sổ trước khi chạy)."),
        }
        if not self._org_id or not self._auth_token:
            return {
                "success": False,
                "error": "Authentication required (org_id and auth_token)",
            }

        agentic_url = settings.effective_agentic_backend_url.rstrip("/")
        headers = {"X-User-Token": self._auth_token}

        get_url = f"{agentic_url}/workflow/{self._org_id}/{workflow_id}"
        logger.info("[execute_workflow] GET %s", get_url)
        client = _get_http_client()
        resp = await client.get(get_url, headers=headers, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        wf_data = resp.json()

        wf_obj = wf_data.get("workflow", {})
        payload = wf_obj.get("payload") or wf_obj
        if not payload:
            return {"success": False, "error": "Could not retrieve workflow payload"}

        if input_overrides and "nodes" in payload:
            for node in payload["nodes"]:
                config_name = node.get("user_config_name", "")
                rf_id = node.get("react_flow_generated_id", "")
                node_id = node.get("node_id", "")
                for key, value in input_overrides.items():
                    if key in (config_name, rf_id, node_id):
                        for param in node.get("params", []):
                            if param.get("field_id") in ("text_input", "user_prompt"):
                                param["value"] = value

        import uuid as uuid_mod

        session_id = str(uuid_mod.uuid4())
        payload["session_id"] = session_id

        run_url = f"{agentic_url}/workflow/{self._org_id}/{workflow_id}/{session_id}/async-run-workflow"
        logger.info("[execute_workflow] POST %s", run_url)
        client = _get_http_client()
        resp = await client.post(
            run_url, json=payload, headers=headers, timeout=PUBLISH_TIMEOUT
        )
        resp.raise_for_status()
        result = resp.json()

        result["session_id"] = session_id
        return result

    async def get_execution_status(
        self, workflow_id: str, run_id: str
    ) -> Dict[str, Any]:
        """Fetch a run's status from agentic-backend."""
        if not self._org_id or not self._auth_token:
            return {
                "success": False,
                "error": "Authentication required (org_id and auth_token)",
            }

        agentic_url = settings.effective_agentic_backend_url.rstrip("/")
        url = f"{agentic_url}/api/v1/orgs/{self._org_id}/workflows/{workflow_id}/runs/{run_id}"
        headers = {"X-User-Token": self._auth_token}

        client = _get_http_client()
        resp = await client.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    async def list_workflow_runs(
        self,
        workflow_id: str,
        status_filter: str | None = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List past runs for a workflow from agentic-backend."""
        if not self._org_id or not self._auth_token:
            return {
                "success": False,
                "error": "Authentication required (org_id and auth_token)",
            }

        agentic_url = settings.effective_agentic_backend_url.rstrip("/")
        url = f"{agentic_url}/api/v1/orgs/{self._org_id}/workflows/{workflow_id}/runs"
        headers = {"X-User-Token": self._auth_token}
        params: dict = {"limit": limit}
        if status_filter:
            params["status"] = status_filter

        client = _get_http_client()
        resp = await client.get(
            url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
