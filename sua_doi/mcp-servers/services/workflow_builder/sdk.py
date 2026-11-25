"""
Capitol Workflow Builder SDK — programmatic workflow creation for Capitol AI Composer.

Provides Workflow, Node, and Edge classes for building, linting, and publishing
workflows. Used by WorkflowBuilderService and WorkflowAgent.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from utils.cache import BoundedTTLCache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

_RUNTIME_NODES: BoundedTTLCache[dict] = BoundedTTLCache(
    maxsize=500, ttl=86400.0
)  # length-prefixed org key → identity fingerprint + mutable override delta


def _runtime_node_key(org_id: str, node_id: str) -> str:
    """Return a collision-safe cache key scoped to one organization."""
    return f"{len(org_id)}:{org_id}{node_id}"


# ---------------------------------------------------------------------------
# Synthetic catalog entries — tool nodes not served by the API catalog
# ---------------------------------------------------------------------------

_EXECUTE_COMPOSIO_TOOL_ENTRY: dict = {
    "auth_type": "none",
    "beta": False,
    "can_be_tool": True,
    "category": "",
    "description": (
        "Execute Composio tools filtered by app name. Set composio_apps to "
        "restrict which tools the agent sees (e.g. ['slack'] exposes only "
        "SLACK_* tools). Connect the Tools output port to an agent node."
    ),
    "display_name": "Composio Tool",
    "has_chat": False,
    "icon": "CogIcon",
    "input_ports": [],
    "mcp_server": "USER_COMPOSIO_URL",
    "node_id": "EXECUTE_COMPOSIO_TOOL",
    "node_sub_type": "mcp_tool",
    "node_type": "tool",
    "output_ports": [
        {
            "id": "composio-tool-output",
            "name": "tool",
            "category": "input",
            "display_name": "Tools",
            "info": "Port to output tool type.",
            "cardinality": "one",
            "port_type": {"accepted_types": ["tools"]},
            "optional": False,
        }
    ],
    "params": [
        {
            "field_id": "composio_apps",
            "display_name": "Composio Apps",
            "display_modal": "TextBox",
            "valid_types": ["list"],
            "show": True,
            "advanced": False,
            "required": True,
            "info": (
                "List of Composio app/action slugs to expose to the agent. "
                "Use lowercase for all tools in an app (e.g. ['gmail', 'slack']) "
                "or UPPERCASE for exact actions (e.g. ['GMAIL_FETCH_EMAILS'])."
            ),
            "default": [],
            "value": [],
            "is_list": True,
            "hidden": False,
        },
    ],
    "composio_apps": [],
    "show": True,
    "tool_category": "App Integrations",
    "tool_name": "EXECUTE_COMPOSIO_TOOL",
    "trace": False,
    "version": "0.1.0",
    "class_name": "ComposioToolNode",
    "module": "abc",
    "full_name": "abc.ComposioToolNode",
}

# ---------------------------------------------------------------------------
# Composio output ports (shared by EXECUTE_COMPOSIO_TOOL)
# ---------------------------------------------------------------------------

_COMPOSIO_TOOL_OUTPUT_PORTS: list[dict] = [
    {
        "id": "composio-tool-output",
        "name": "tool",
        "category": "input",
        "display_name": "Tools",
        "info": "Port to output tool type.",
        "cardinality": "one",
        "port_type": {"accepted_types": ["tools"]},
        "optional": False,
    }
]

_SYNTHETIC_ENTRIES: list[dict] = [
    # Explicit CLI/test catalog files predate backend catalog injection.
    # Production API catalogs are already org-filtered and must never use this.
    _EXECUTE_COMPOSIO_TOOL_ENTRY,
]


def _inject_synthetic_entries(catalog: dict[str, dict]) -> dict[str, dict]:
    """Inject legacy synthetic entries into explicit CLI/test fixture catalogs."""
    for entry in _SYNTHETIC_ENTRIES:
        nid = entry["node_id"]
        if nid not in catalog:
            catalog[nid] = entry
    return catalog


def _has_backend_implementation(entry: dict) -> bool:
    """True when the entry names the class agentic-backend imports at run time.

    ``module`` / ``class_name`` come from the node registry via the list-nodes
    API. Entries created with ``register_node`` never carry them, and a
    workflow built from such an entry fails at run time with
    ``ValueError: Empty module name``.
    """
    return bool(entry.get("module")) and bool(entry.get("class_name"))


_IMPLEMENTATION_IDENTITY_FIELDS = (
    "node_id",
    "module",
    "class_name",
    "full_name",
)


def _same_implementation_identity(left: dict, right: dict) -> bool:
    return all(
        left.get(field) == right.get(field) for field in _IMPLEMENTATION_IDENTITY_FIELDS
    )


def _implementation_identity(entry: dict) -> dict:
    return {field: entry.get(field) for field in _IMPLEMENTATION_IDENTITY_FIELDS}


def _merge_runtime_nodes(
    catalog: dict[str, dict],
    *,
    org_id: str,
) -> dict[str, dict]:
    """Apply safe updates to real catalog entries without adding new node types.

    ``_RUNTIME_NODES`` is process-global with a 24h TTL, but entries are keyed
    by org and node ID. An entry may replace an existing, org-visible catalog
    entry only when its identity fingerprint still matches the backend.
    Override deltas are applied onto each fresh backend entry so new backend
    fields remain visible. Legacy snapshots and runtime-only IDs are ignored.
    """
    for node_id, authoritative_entry in list(catalog.items()):
        cache_key = _runtime_node_key(org_id, node_id)
        cached_override = _RUNTIME_NODES.get(cache_key)
        if cached_override is None:
            continue
        if (
            not isinstance(cached_override, dict)
            or not isinstance(cached_override.get("identity"), dict)
            or not isinstance(cached_override.get("overrides"), dict)
        ):
            logger.warning(
                "Ignoring legacy runtime snapshot for '%s' in org '%s'",
                node_id,
                org_id,
            )
            _RUNTIME_NODES.delete(cache_key)
            continue
        if not _same_implementation_identity(
            cached_override["identity"],
            authoritative_entry,
        ):
            logger.warning(
                "Discarding stale runtime override for '%s' in org '%s': "
                "backend implementation identity changed",
                node_id,
                org_id,
            )
            _RUNTIME_NODES.delete(cache_key)
            continue

        merged_entry = copy.deepcopy(authoritative_entry)
        merged_entry.update(copy.deepcopy(cached_override["overrides"]))
        catalog[node_id] = merged_entry
    return catalog


def register_node(node_def: dict, *, catalog: dict[str, dict] | None = None) -> dict:
    """Refuse runtime-only registration without a deployed implementation."""
    required = ("node_id", "display_name", "node_type", "description")
    missing = [k for k in required if not node_def.get(k)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    node_id = node_def["node_id"]
    if catalog is not None and node_id in catalog:
        raise ValueError(
            f"Node type '{node_id}' already exists in the catalog. Use it "
            "directly in assemble_workflow instead of re-registering it; a "
            "re-registered copy has no backend implementation and cannot run."
        )

    raise ValueError(
        f"Cannot register new node type '{node_id}' at runtime. New node types "
        "require a deployed backend implementation and must appear in the "
        "current organization catalog before workflows can use them."
    )


def update_node(node_id: str, updates: dict, **catalog_kwargs) -> dict:
    """Update fields on an existing node type in the runtime catalog.

    Only the keys present in *updates* are stored as an org-scoped delta.
    Future catalog refreshes start from the new backend entry and reapply that
    delta while the implementation identity fingerprint still matches.

    Raises ``KeyError`` if the node_id doesn't exist.
    """
    org_id = catalog_kwargs.get("org_id")
    if not isinstance(org_id, str) or not org_id:
        raise ValueError("org_id is required for scoped runtime node updates")

    authoritative_catalog = _load_catalog(
        include_runtime=False,
        **catalog_kwargs,
    )
    if node_id not in authoritative_catalog:
        raise KeyError(f"Node '{node_id}' not found in current org catalog")
    authoritative_entry = authoritative_catalog[node_id]
    if not _has_backend_implementation(authoritative_entry):
        raise ValueError(
            f"Node '{node_id}' has no deployed backend implementation and "
            "cannot be updated for production assembly."
        )

    for field in _IMPLEMENTATION_IDENTITY_FIELDS:
        if field in updates:
            raise ValueError(
                f"Update for node '{node_id}' cannot change backend "
                f"implementation identity field '{field}'."
            )

    cache_key = _runtime_node_key(org_id, node_id)
    cached_override = _RUNTIME_NODES.get(cache_key)
    accumulated_overrides: dict = {}
    if (
        cached_override is not None
        and isinstance(cached_override, dict)
        and isinstance(cached_override.get("identity"), dict)
        and isinstance(cached_override.get("overrides"), dict)
        and _same_implementation_identity(
            cached_override["identity"],
            authoritative_entry,
        )
    ):
        accumulated_overrides = copy.deepcopy(cached_override["overrides"])
    elif cached_override is not None:
        _RUNTIME_NODES.delete(cache_key)

    accumulated_overrides.update(copy.deepcopy(updates))
    _RUNTIME_NODES.set(
        cache_key,
        {
            "node_id": node_id,
            "identity": _implementation_identity(authoritative_entry),
            "overrides": accumulated_overrides,
        },
    )
    logger.info(
        "Updated node type '%s' — changed keys: %s", node_id, list(updates.keys())
    )
    updated_entry = copy.deepcopy(authoritative_entry)
    updated_entry.update(copy.deepcopy(accumulated_overrides))
    return updated_entry


def _fetch_catalog_from_api(
    auth_token: str, org_id: str, base_url: str
) -> dict[str, dict]:
    """Fetch node catalog from the agentic-backend list-nodes endpoint."""
    if requests is None:
        raise RuntimeError("requests library is required for dynamic catalog fetch")

    resp = requests.post(
        f"{base_url.rstrip('/')}/nodes/{org_id}/list-nodes",
        headers={"Content-Type": "application/json", "X-User-Token": auth_token},
        timeout=240,  # [VÁ CỦA MÌNH 03/11/2026 — chay_thu/sua_doi/README.md] danh mục khối máy mình mất ~30 s chiều 03/11, 52 s tối 03/11 (đo curl 19:34; 401 ở khối kho tài liệu / đồ thị tri thức khi không có đăng nhập thật, mỗi lần thử lại ~8 s) ⇒ 10 s gốc hết hạn; 90 s vẫn hết hạn khi chat chạy song song (19:30) ⇒ 240 s
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success") or "catalog" not in data:
        raise RuntimeError(f"Unexpected API response: success={data.get('success')}")

    return {n["node_id"]: n for n in data["catalog"]}


def _load_catalog(
    path: str | None = None,
    *,
    auth_token: str | None = None,
    org_id: str | None = None,
    base_url: str | None = None,
    include_runtime: bool = True,
) -> dict[str, dict]:
    """Load the node catalog from the agentic-backend API (single source of truth).

    The API returns per-org filtered nodes — no static fallback, no stale data.
    If auth credentials are missing or the API is unreachable, this fails loudly
    rather than silently serving unfiltered nodes.

    Safe updates to existing real nodes may be merged on top. New runtime-only
    node IDs are never added.

    Args:
        path: Explicit file path to load from (for CLI/testing only).
        auth_token: User auth token for agentic-backend API.
        org_id: Organisation ID for agentic-backend API.
        base_url: Agentic-backend base URL.
        include_runtime: Apply safe org-scoped runtime metadata overrides.
    """
    if path:
        # CLI/testing only — explicit file path bypasses API
        with open(path) as f:
            data = json.load(f)
        raw = {n["node_id"]: n for n in data["catalog"]}
        catalog = _inject_synthetic_entries(raw)
        return (
            _merge_runtime_nodes(catalog, org_id=org_id)
            if org_id and include_runtime
            else catalog
        )

    if not auth_token or not org_id or not base_url:
        raise ValueError(
            "auth_token, org_id, and base_url are ALL required for catalog fetch. "
            "The agentic-backend API is the single source of truth for per-org "
            "node access control. Got: auth_token=%s, org_id=%s, base_url=%s"
            % (bool(auth_token), bool(org_id), bool(base_url))
        )

    catalog = _fetch_catalog_from_api(auth_token, org_id, base_url)
    return _merge_runtime_nodes(catalog, org_id=org_id) if include_runtime else catalog


# ---------------------------------------------------------------------------
# Port / param helpers
# ---------------------------------------------------------------------------


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _require_successful_publish_response(result: object) -> dict:
    """Validate the persist-workflow success contract."""
    if not isinstance(result, dict):
        raise RuntimeError("persist-workflow returned a non-object response")
    if result.get("success") is not True:
        raise RuntimeError("persist-workflow did not report explicit success")
    workflow_id = result.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise RuntimeError("persist-workflow returned no workflow_id")
    return result


def _svg_escape(text: str) -> str:
    """Escape text for safe SVG/XML embedding."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _find_output_port(
    node_struct: dict, *, port_name: str | None = None
) -> dict | None:
    """Find an output port on a node struct, optionally by name."""
    ports = node_struct.get("output_ports", [])
    if not ports:
        return None
    if port_name:
        for p in ports:
            if p["name"] == port_name:
                return p
    return ports[0]  # default to first


def _find_param(node_struct: dict, field_id: str) -> dict | None:
    """Find a param by field_id."""
    for p in node_struct.get("params", []):
        if p["field_id"] == field_id:
            return p
    return None


def _find_input_port(
    node_struct: dict, *, category: str | None = None, name: str | None = None
) -> dict | None:
    """Find an input port on a node struct."""
    ports = node_struct.get("input_ports", [])
    for p in ports:
        if category and p.get("category") == category:
            return p
        if name and p.get("name") == name:
            return p
    if ports:
        return ports[0]
    return None


def _get_bindable_input_port(param: dict) -> dict | None:
    """Get the input port from a param's bindable_config."""
    bc = param.get("bindable_config")
    if bc:
        return bc.get("input_port")
    return None


# ---------------------------------------------------------------------------
# Node wrapper
# ---------------------------------------------------------------------------


class Node:
    """A node instance within a workflow."""

    def __init__(
        self,
        node_id: str,
        catalog_entry: dict,
        instance_id: str | None = None,
        position: tuple[float, float] = (0.0, 0.0),
    ):
        self.node_id = node_id  # catalog node_id e.g. "agent_node"
        self.instance_id = instance_id or _new_uuid()
        self.position = {"x": float(position[0]), "y": float(position[1])}

        # Deep copy the catalog entry to build the struct
        self._catalog = copy.deepcopy(catalog_entry)

        # Build the struct that goes into data.struct
        self.struct = self._build_struct()

    def _build_struct(self) -> dict:
        """Build the node struct from catalog entry.

        Raises ``ValueError`` when the entry has no backend implementation:
        publishing it would only move the failure to run time.
        """
        c = self._catalog
        if not _has_backend_implementation(c):
            raise ValueError(
                f"Node type '{self.node_id}' has no backend implementation "
                "(missing module/class_name) and cannot run. Use a node type "
                "from list_workflow_nodes instead."
            )
        struct: dict[str, Any] = {
            "node_sub_type": c["node_sub_type"],
            "module": c.get("module", ""),
            "icon": c.get("icon", "CogIcon"),
            "show": c.get("show", True),
            "description": c["description"],
            "can_be_tool": c.get("can_be_tool", False),
            "display_name": c["display_name"],
            "params": copy.deepcopy(c.get("params", [])),
            "version": c.get("version", "0.1.0"),
            "output_ports": self._init_ports(c.get("output_ports", [])),
            "node_type": c["node_type"],
            "full_name": c.get("full_name", ""),
            "name": c.get("display_name", ""),
            "category": c.get("category", c["node_type"]),
            "class_name": c.get("class_name", ""),
            "beta": c.get("beta", False),
            "input_ports": self._init_ports(c.get("input_ports", [])),
            "node_id": self.node_id,
            "react_flow_generated_id": self.instance_id,
            "user_config_name": None,
            "user_config_description": None,
        }
        # Tool-specific fields
        if c.get("tool_name"):
            struct["tool_name"] = c["tool_name"]
        if c.get("mcp_server"):
            struct["mcp_server"] = c["mcp_server"]
        if c.get("trace") is not None:
            struct["trace"] = c["trace"]
        if c.get("composio_apps"):
            struct["composio_apps"] = list(c["composio_apps"])
        return struct

    def _init_ports(self, ports: list[dict]) -> list[dict]:
        """Deep copy ports and assign fresh UUIDs."""
        result = []
        for p in ports:
            port = copy.deepcopy(p)
            port["id"] = _new_uuid()
            # Reset incoming connections
            if "incoming_connections" in port:
                port["incoming_connections"] = [{"node_id": "", "port_id": ""}]
            result.append(port)
        return result

    # -- Parameter access --------------------------------------------------

    def set_param(self, field_id: str, value: Any) -> "Node":
        """Set a parameter value on this node."""
        param = _find_param(self.struct, field_id)
        if param is None:
            available = [p["field_id"] for p in self.struct.get("params", [])]
            raise ValueError(
                f"Parameter '{field_id}' not found on node '{self.node_id}'. "
                f"Available: {available}"
            )
        # Warn on invalid DropDown values (caught as ERROR by lint)
        options = param.get("options")
        if param.get("display_modal") == "DropDown" and options and value:
            if value not in options:
                logger.warning(
                    "Parameter '%s' on node '%s' has value '%s' which is not in "
                    "allowed options: %s — this will fail lint validation.",
                    field_id,
                    self.node_id,
                    value,
                    options,
                )
        param["value"] = value
        param["show"] = True
        # Sync composio_apps to struct level (runtime reads it there)
        if field_id == "composio_apps" and isinstance(value, list):
            self.struct["composio_apps"] = list(value)
        return self

    def get_param(self, field_id: str) -> Any:
        """Get a parameter value."""
        param = _find_param(self.struct, field_id)
        if param is None:
            raise ValueError(
                f"Parameter '{field_id}' not found on node '{self.node_id}'."
            )
        return param.get("value")

    def list_params(self) -> list[dict]:
        """List all parameters with their field_id, display_name, value, and required."""
        return [
            {
                "field_id": p["field_id"],
                "display_name": p["display_name"],
                "value": p.get("value", ""),
                "required": p.get("required", False),
                "valid_types": p.get("valid_types", []),
            }
            for p in self.struct.get("params", [])
        ]

    # -- Display customization ---------------------------------------------

    def set_display(self, name: str, description: str = "") -> "Node":
        """Set a custom display name and description for this node instance.

        Shown on the canvas instead of the generic node type name.
        """
        self.struct["user_config_name"] = name
        if description:
            self.struct["user_config_description"] = description
        return self

    def set_composio_apps(self, apps: list[str]) -> "Node":
        """Restrict which Composio apps this tool node exposes.

        Only relevant for EXECUTE_COMPOSIO_TOOL nodes.  The app names
        are matched as case-insensitive prefixes against tool names
        (e.g. ``["slack"]`` → only ``SLACK_*`` tools).
        """
        lowered = [a.lower() for a in apps]
        self.struct["composio_apps"] = lowered
        logger.info(
            "[COMPOSIO] set_composio_apps: node=%s apps=%s",
            self.display_name,
            lowered,
        )
        return self

    # -- Port access -------------------------------------------------------

    def get_output_port(self, name: str | None = None) -> dict:
        """Get an output port, optionally by name."""
        port = _find_output_port(self.struct, port_name=name)
        if port is None:
            raise ValueError(f"No output port found on '{self.node_id}'")
        return port

    def get_input_port(
        self, category: str | None = None, name: str | None = None
    ) -> dict:
        """Get an input port."""
        port = _find_input_port(self.struct, category=category, name=name)
        if port is None:
            raise ValueError(f"No input port found on '{self.node_id}'")
        return port

    # -- Serialization -----------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize to the workflow JSON node format."""
        return {
            "result": None,
            "measured": {"width": 300, "height": 400},
            "dragging": False,
            "data": {
                "struct": self.struct,
                "isSelected": False,
            },
            "isSelected": False,
            "id": self.instance_id,
            "position": self.position,
            "type": "GenericNode",
            "selected": False,
        }

    @property
    def display_name(self) -> str:
        return self.struct.get("user_config_name") or self.struct.get(
            "display_name", self.node_id
        )

    @property
    def node_type(self) -> str:
        return self.struct.get("node_type", "")

    def __repr__(self) -> str:
        return f"Node({self.node_id!r}, id={self.instance_id!r}, name={self.display_name!r})"


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------


@dataclass
class Edge:
    source_node: Node
    source_port: dict
    target_node: Node
    target_port: dict
    target_param_id: str | None
    edge_type: str  # "port_to_param" or "port_to_port"

    @property
    def id(self) -> str:
        return (
            f"xy-edge__{self.source_node.instance_id}{self.source_port['id']}"
            f"-{self.target_node.instance_id}{self.target_port['id']}"
        )

    def to_dict(self) -> dict:
        return {
            "source_node_id": self.source_node.instance_id,
            "data": {},
            "source_port_id": self.source_port["id"],
            "source": self.source_node.instance_id,
            "type": "default",
            "targetHandle": self.target_port["id"],
            "target": self.target_node.instance_id,
            "sourceHandle": self.source_port["id"],
            "sourceNodeId": self.source_node.node_id,
            "animated": True,
            "target_param_id": self.target_param_id,
            "style": {
                "strokeWidth": "2",
                "stroke": "var(--color-muted-foreground)",
            },
            "id": self.id,
            "target_port_id": self.target_port["id"],
            "targetNodeId": self.target_node.node_id,
            "edge_type": self.edge_type,
            "target_node_id": self.target_node.instance_id,
        }


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class Workflow:
    """
    Programmatic builder for Capitol AI Composer workflows.

    Workflows consist of nodes (agents, tools, inputs, outputs, governance)
    connected by edges. The resulting JSON can be imported into the Composer
    via the Import Workflow function.
    """

    def __init__(
        self,
        name: str,
        *,
        description: str = "",
        catalog_path: str | None = None,
        catalog: dict[str, dict] | None = None,
    ):
        self.name = name
        self.description = description
        self.id = _new_uuid()
        self.session_id = _new_uuid()

        self._catalog = catalog if catalog is not None else _load_catalog(catalog_path)
        self._nodes: list[Node] = []
        self._edges: list[Edge] = []

    # -- Node creation -----------------------------------------------------

    def add_node(
        self,
        node_id: str,
        *,
        position: tuple[float, float] = (0.0, 0.0),
    ) -> Node:
        """
        Add a node to the workflow by its catalog node_id.

        Args:
            node_id: The node type from the catalog (e.g. "agent_node",
                     "text_input_node", "EXA_SEARCH").
            position: (x, y) canvas position for layout.

        Returns:
            A Node instance that can be configured with set_param().
        """
        if node_id not in self._catalog:
            available = sorted(self._catalog.keys())
            raise ValueError(f"Unknown node_id '{node_id}'. Available: {available}")
        node = Node(node_id, self._catalog[node_id], position=position)
        self._nodes.append(node)
        return node

    def remove_node(self, node: Node) -> None:
        """Remove a node and all its edges from the workflow."""
        self._edges = [
            e
            for e in self._edges
            if e.source_node is not node and e.target_node is not node
        ]
        self._nodes = [n for n in self._nodes if n is not node]

    # -- Connection --------------------------------------------------------

    def connect(
        self,
        source: Node,
        target: Node,
        *,
        target_param: str | None = None,
        source_port_name: str | None = None,
    ) -> Edge:
        """
        Connect two nodes with an edge.

        For data flow (text, url, etc.):
            wf.connect(text_input, agent, target_param="user_prompt")

        For tool connections:
            wf.connect(exa_search, agent)  # auto-detects tool->agent

        Args:
            source: The source node.
            target: The target node.
            target_param: The target parameter field_id to bind to.
                          If None, attempts to auto-detect (tool connection).
            source_port_name: Optional source port name (defaults to first).

        Raises:
            ValueError: If connection is invalid (type mismatch, missing param,
                        cardinality violation, etc.)
        """
        source_port = source.get_output_port(source_port_name)
        source_types = set(source_port.get("port_type", {}).get("accepted_types", []))

        # Determine edge type and target port
        if "tools" in source_types or "memory" in source_types:
            return self._connect_tool(source, target, source_port)
        elif target_param:
            return self._connect_param(source, target, source_port, target_param)
        else:
            raise ValueError(
                "target_param is required for non-tool connections. "
                "Specify which parameter on the target node to bind to."
            )

    def _connect_tool(self, source: Node, target: Node, source_port: dict) -> Edge:
        """Connect a tool/memory node to an agent's handle input port."""
        source_types = set(source_port.get("port_type", {}).get("accepted_types", []))

        # Find the matching input port on the target (tool or memory category)
        tool_port = None
        if "tools" in source_types:
            tool_port = _find_input_port(target.struct, category="tool")
        elif "memory" in source_types:
            tool_port = _find_input_port(target.struct, category="memory")
            if tool_port is None:
                tool_port = _find_input_port(target.struct, name="memory")

        if tool_port is None:
            raise ValueError(
                f"Target node '{target.node_id}' has no matching handle input port "
                f"for source types {source_types}. "
                "Tool/memory connections only work with agent nodes that have "
                "handle-type input ports."
            )

        # Validate binding type compatibility
        binding_type = tool_port.get("binding_type", "handle")
        if binding_type not in ("handle", "all"):
            raise ValueError(
                f"Target port '{tool_port.get('name', '?')}' on '{target.node_id}' "
                f"has binding_type='{binding_type}', which does not accept handle "
                f"connections. Tool/memory nodes require handle or 'all' binding type."
            )

        # Validate type compatibility
        tgt_types = set(tool_port.get("port_type", {}).get("accepted_types", []))
        if source_types and tgt_types and not (source_types & tgt_types):
            raise ValueError(
                f"Type mismatch: source '{source.node_id}' outputs {source_types} "
                f"but target port accepts {tgt_types}."
            )

        # Update incoming connections on the target port
        conns = tool_port.get("incoming_connections", [])
        # Remove empty placeholders
        conns = [c for c in conns if c.get("node_id") and c.get("port_id")]
        conns.append(
            {
                "node_id": source.instance_id,
                "port_id": source_port["id"],
            }
        )
        tool_port["incoming_connections"] = conns

        edge = Edge(
            source_node=source,
            source_port=source_port,
            target_node=target,
            target_port=tool_port,
            target_param_id=None,
            edge_type="port_to_port",
        )
        self._edges.append(edge)
        return edge

    def _connect_param(
        self, source: Node, target: Node, source_port: dict, target_param: str
    ) -> Edge:
        """Connect a source output to a target parameter's bindable input port."""
        param = _find_param(target.struct, target_param)
        if param is None:
            available = [p["field_id"] for p in target.struct.get("params", [])]
            raise ValueError(
                f"Parameter '{target_param}' not found on target node '{target.node_id}'. "
                f"Available params: {available}"
            )

        # Validate type compatibility between source output and target param
        source_types = set(source_port.get("port_type", {}).get("accepted_types", []))
        param_types = set(param.get("valid_types", []))
        if source_types and param_types and not (source_types & param_types):
            # Allow str to coerce to most types (common pattern)
            if not (
                "str" in source_types or "str" in param_types or "data" in param_types
            ):
                import warnings

                warnings.warn(
                    f"Type mismatch: source '{source.node_id}' outputs {source_types} "
                    f"but target param '{target_param}' accepts {param_types}. "
                    f"This may cause runtime errors.",
                    stacklevel=3,
                )

        bc = param.get("bindable_config")
        if bc is None:
            # Create a bindable_config for this param
            input_port = {
                "binding_type": "param",
                "incoming_connections": [],
                "port_type": {"accepted_types": param.get("valid_types", ["str"])},
                "name": "text",
                "optional": True,
                "id": _new_uuid(),
                "category": "input",
                "display_name": "Text Input",
                "cardinality": "many",
                "info": f"Accepts inputs for {param.get('display_name', target_param)}.",
            }
            bc = {"is_bound": True, "input_port": input_port}
            param["bindable_config"] = bc
        else:
            bc["is_bound"] = True
            input_port = bc["input_port"]

        # Check cardinality before adding connection
        cardinality = input_port.get("cardinality", "many")
        if cardinality == "one":
            existing_conns = [
                c
                for c in input_port.get("incoming_connections", [])
                if c.get("node_id") and c.get("port_id")
            ]
            if existing_conns:
                import warnings

                warnings.warn(
                    f"Parameter '{target_param}' on '{target.node_id}' has "
                    f"cardinality='one' but already has a connection. "
                    f"Adding another connection may cause issues.",
                    stacklevel=3,
                )

        # Update incoming connections
        conns = input_port.get("incoming_connections", [])
        conns = [c for c in conns if c.get("node_id") and c.get("port_id")]
        conns.append(
            {
                "node_id": source.instance_id,
                "port_id": source_port["id"],
            }
        )
        input_port["incoming_connections"] = conns

        # Mark param as bound (hide manual entry)
        param["show"] = False

        edge = Edge(
            source_node=source,
            source_port=source_port,
            target_node=target,
            target_port=input_port,
            target_param_id=target_param,
            edge_type="port_to_param",
        )
        self._edges.append(edge)
        return edge

    def disconnect(self, edge: Edge) -> None:
        """Remove an edge from the workflow."""
        self._edges = [e for e in self._edges if e is not edge]

    # -- Querying ----------------------------------------------------------

    @property
    def nodes(self) -> list[Node]:
        return list(self._nodes)

    @property
    def edges(self) -> list[Edge]:
        return list(self._edges)

    def get_node(self, instance_id: str) -> Node | None:
        """Find a node by its instance ID."""
        for n in self._nodes:
            if n.instance_id == instance_id:
                return n
        return None

    def get_nodes_by_type(self, node_type: str) -> list[Node]:
        """Get all nodes of a given type (agent, tool, input, output, governance)."""
        return [n for n in self._nodes if n.node_type == node_type]

    # -- Linting -----------------------------------------------------------

    def lint(self) -> list[str]:
        """
        Validate the workflow and return a list of error/warning messages.

        Performs comprehensive checks matching the agentic backend's validation:
        - Required parameters have values (unless bound to an incoming connection)
        - Agent nodes have a model set
        - Edge source/target nodes exist in the workflow
        - No duplicate node instance IDs
        - Type compatibility between connected ports and params
        - Orphan nodes (no connections at all)
        - Port cardinality limits (single vs many connections)
        - Binding type compatibility (handle vs param connections)
        - incoming_connections consistency with edges (backend requirement)
        - Param is_bound state consistency with actual connections
        - Tool/memory nodes must be connected to an agent
        """
        errors: list[str] = []
        node_ids = {n.instance_id for n in self._nodes}

        # Check for duplicate instance IDs
        if len(node_ids) != len(self._nodes):
            errors.append("ERROR: Duplicate node instance IDs detected.")

        # Check for empty workflow
        if not self._nodes:
            errors.append("WARNING: Workflow has no nodes.")
            return errors

        # Build connection tracking
        connected_nodes: set[str] = set()
        # Track connections per target port for cardinality checking
        target_port_connections: dict[str, int] = {}  # port_id -> count
        for edge in self._edges:
            connected_nodes.add(edge.source_node.instance_id)
            connected_nodes.add(edge.target_node.instance_id)
            tgt_port_id = edge.target_port.get("id", "")
            if tgt_port_id:
                target_port_connections[tgt_port_id] = (
                    target_port_connections.get(tgt_port_id, 0) + 1
                )

        for node in self._nodes:
            prefix = f"[{node.display_name} ({node.node_id})]"

            # Check required params have values
            for param in node.struct.get("params", []):
                if not param.get("required"):
                    continue
                field_id = param["field_id"]
                value = param.get("value")

                # If param is bound via edge, it's OK to be empty
                bc = param.get("bindable_config")
                if bc and bc.get("is_bound"):
                    ip = bc.get("input_port", {})
                    conns = ip.get("incoming_connections", [])
                    real_conns = [
                        c for c in conns if c.get("node_id") and c.get("port_id")
                    ]
                    if real_conns:
                        continue

                # Runtime-supplied params (file uploads) are expected to be
                # empty at build time — users provide values when they run
                # the workflow.  Don't flag these as lint errors.
                modal = param.get("display_modal", "")
                if modal == "FileUploadDialogueBox":
                    continue

                if value is None or value == "" or value == []:
                    errors.append(
                        f"ERROR: {prefix} Required parameter '{field_id}' has no value."
                    )

            # Validate DropDown param values against allowed options
            for param in node.struct.get("params", []):
                if param.get("display_modal") != "DropDown":
                    continue
                options = param.get("options")
                value = param.get("value")
                if not options or not value:
                    continue
                if value not in options:
                    errors.append(
                        f"ERROR: {prefix} Parameter '{param['field_id']}' has value "
                        f"'{value}' which is not a valid option. "
                        f"Allowed values: {options}"
                    )

            # Agent-specific checks
            if node.node_id == "agent_node":
                model = node.get_param("model")
                if not model:
                    errors.append(f"ERROR: {prefix} Agent has no model selected.")

            # Tool/memory nodes must be connected to an agent
            if node.node_type in ("tool", "memory"):
                has_agent_connection = any(
                    e.source_node.instance_id == node.instance_id
                    and e.edge_type == "port_to_port"
                    for e in self._edges
                )
                if not has_agent_connection and len(self._nodes) > 1:
                    errors.append(
                        f"WARNING: {prefix} Tool/memory node is not connected to any agent."
                    )

                # Cross-check: if an edge exists, verify the agent's handle
                # port actually lists this tool in incoming_connections.
                # A mismatch means the tool may be silently skipped at runtime.
                for edge in self._edges:
                    if (
                        edge.source_node.instance_id == node.instance_id
                        and edge.edge_type == "port_to_port"
                    ):
                        agent = edge.target_node
                        # These agent nodes always accept tool connections
                        # at runtime even when the catalog struct doesn't
                        # include input_ports metadata — skip check.
                        tool_accepting_agents = {
                            "agent_node",
                            "flexible_code_interpreter_node",
                        }
                        if agent.node_id in tool_accepting_agents:
                            continue
                        # Find the tool handle input port on the agent
                        tool_handle = None
                        for ip in agent.struct.get("input_ports", []):
                            if ip.get("port_type", {}).get("category") == "tool":
                                tool_handle = ip
                                break
                        if tool_handle is None:
                            errors.append(
                                f"WARNING: {prefix} Edge targets '{agent.display_name}' "
                                f"but that node has no tool handle input port. "
                                f"The tool may be silently skipped at runtime."
                            )
                        else:
                            conns = tool_handle.get("incoming_connections", [])
                            connected_ids = {c.get("node_id") for c in conns}
                            if node.instance_id not in connected_ids:
                                errors.append(
                                    f"WARNING: {prefix} Edge connects to "
                                    f"'{agent.display_name}' but the agent's tool "
                                    f"handle port does not list this tool in "
                                    f"incoming_connections. The tool may be silently "
                                    f"skipped at runtime."
                                )

            # EXECUTE_COMPOSIO_TOOL nodes: check they have an mcp_server URL
            if node.node_id == "EXECUTE_COMPOSIO_TOOL":
                mcp_url = node.struct.get("mcp_server", "")
                apps = node.struct.get("composio_apps", [])
                logger.info(
                    "[COMPOSIO:LINT] Validating EXECUTE_COMPOSIO_TOOL node=%s "
                    "mcp_server=%s composio_apps=%s",
                    node.display_name,
                    mcp_url or "(empty)",
                    apps or "(empty)",
                )
                if not mcp_url:
                    errors.append(
                        f"ERROR: {prefix} EXECUTE_COMPOSIO_TOOL node has no "
                        f"mcp_server URL set. The runtime will not be able to "
                        f"connect to the Composio MCP server."
                    )
                # composio_apps must be set so the tool node knows which apps to expose
                if not apps:
                    errors.append(
                        f"ERROR: {prefix} EXECUTE_COMPOSIO_TOOL node has no "
                        f"composio_apps set. The agent will not have access to "
                        f"any Composio tools at runtime. Set composio_apps to "
                        f"the app slugs you need (e.g. ['slack'], ['gmail'])."
                    )

            # Agent nodes with EXECUTE_COMPOSIO_TOOL connections: ensure
            # mcp_servers param doesn't also contain a composio URL (duplication)
            if node.node_id == "agent_node":
                mcp_servers_param = node.get_param("mcp_servers")
                if isinstance(mcp_servers_param, dict):
                    servers = mcp_servers_param.get("mcp_servers", [])
                    for srv in servers:
                        url = srv.get("url", "") if isinstance(srv, dict) else ""
                        if "composio" in url.lower():
                            errors.append(
                                f"WARNING: {prefix} Agent has a Composio URL in "
                                f"mcp_servers AND a connected EXECUTE_COMPOSIO_TOOL "
                                f"node. This may cause duplicate tool registrations."
                            )

            # file_input_node → agent: warn if parser_flag is off
            if node.node_id == "file_input_node":
                parser_flag = node.get_param("parser_flag")
                if not parser_flag:
                    agent_ids = {
                        "agent_node",
                        "deep_research_node",
                        "data_analysis_agent_node",
                        "flexible_code_interpreter_node",
                    }
                    feeds_agent = any(
                        e.source_node.instance_id == node.instance_id
                        and e.target_node.node_id in agent_ids
                        for e in self._edges
                    )
                    if feeds_agent:
                        errors.append(
                            f"WARNING: {prefix} parser_flag is off but this "
                            f"file input feeds an agent node. The agent will "
                            f"receive an S3 URL instead of the file contents. "
                            f"Set parser_flag=true so the file is parsed and "
                            f"passed as text."
                        )

            # Orphan check (skip tool/memory since they're checked above)
            if (
                node.instance_id not in connected_nodes
                and len(self._nodes) > 1
                and node.node_type not in ("tool", "memory")
            ):
                errors.append(
                    f"WARNING: {prefix} Node is not connected to any other node."
                )

            # Validate param is_bound consistency with actual connections
            for param in node.struct.get("params", []):
                bc = param.get("bindable_config")
                if not bc:
                    continue
                ip = bc.get("input_port", {})
                conns = ip.get("incoming_connections", [])
                real_conns = [c for c in conns if c.get("node_id") and c.get("port_id")]
                is_bound = bc.get("is_bound", False)

                if is_bound and not real_conns:
                    is_required = param.get("required", False)
                    level = "ERROR" if is_required else "WARNING"
                    errors.append(
                        f"{level}: {prefix} Parameter '{param['field_id']}' is marked as bound "
                        f"but has no incoming connections."
                    )

        # Check edges reference valid nodes
        for edge in self._edges:
            src_prefix = f"[Edge {edge.source_node.display_name} → {edge.target_node.display_name}]"

            if edge.source_node.instance_id not in node_ids:
                errors.append(
                    f"ERROR: {src_prefix} Source node "
                    f"'{edge.source_node.instance_id}' not found in workflow."
                )
            if edge.target_node.instance_id not in node_ids:
                errors.append(
                    f"ERROR: {src_prefix} Target node "
                    f"'{edge.target_node.instance_id}' not found in workflow."
                )

            # Validate edge type matches connection pattern
            if edge.edge_type == "port_to_param" and not edge.target_param_id:
                errors.append(
                    f"ERROR: {src_prefix} Edge type is 'port_to_param' but no "
                    f"target_param_id is set."
                )
            if edge.edge_type == "port_to_port" and edge.target_param_id:
                errors.append(
                    f"WARNING: {src_prefix} Edge type is 'port_to_port' but has "
                    f"target_param_id='{edge.target_param_id}'."
                )

            # Validate incoming_connections match (backend requirement)
            if edge.edge_type == "port_to_port":
                # For port_to_port, check that the target port has this source
                # in its incoming_connections
                tgt_port = edge.target_port
                conns = tgt_port.get("incoming_connections", [])
                has_conn = any(
                    c.get("node_id") == edge.source_node.instance_id
                    and c.get("port_id") == edge.source_port.get("id")
                    for c in conns
                )
                if not has_conn:
                    errors.append(
                        f"ERROR: {src_prefix} Target port '{tgt_port.get('name', '?')}' "
                        f"does not have a matching incoming_connection for this edge. "
                        f"The backend will reject this workflow."
                    )

            elif edge.edge_type == "port_to_param":
                # For port_to_param, check that the target param's bindable_config
                # input_port has this source in incoming_connections
                tgt_param_id = edge.target_param_id
                if tgt_param_id:
                    param = _find_param(edge.target_node.struct, tgt_param_id)
                    if param:
                        bc = param.get("bindable_config")
                        if not bc or not bc.get("is_bound"):
                            errors.append(
                                f"ERROR: {src_prefix} Target param '{tgt_param_id}' "
                                f"is not marked as bound. The backend will reject this."
                            )
                        elif bc.get("input_port"):
                            ip = bc["input_port"]
                            conns = ip.get("incoming_connections", [])
                            has_conn = any(
                                c.get("node_id") == edge.source_node.instance_id
                                and c.get("port_id") == edge.source_port.get("id")
                                for c in conns
                            )
                            if not has_conn:
                                errors.append(
                                    f"ERROR: {src_prefix} Target param '{tgt_param_id}' "
                                    f"input_port does not have a matching incoming_connection. "
                                    f"The backend will reject this workflow."
                                )
                    else:
                        errors.append(
                            f"ERROR: {src_prefix} Target param '{tgt_param_id}' "
                            f"not found on target node '{edge.target_node.node_id}'."
                        )

        # Check type compatibility
        for edge in self._edges:
            src_types = set(
                edge.source_port.get("port_type", {}).get("accepted_types", [])
            )
            tgt_types = set(
                edge.target_port.get("port_type", {}).get("accepted_types", [])
            )
            if src_types and tgt_types and not src_types & tgt_types:
                errors.append(
                    f"WARNING: Type mismatch on edge from "
                    f"'{edge.source_node.display_name}' ({src_types}) to "
                    f"'{edge.target_node.display_name}' ({tgt_types})."
                )

        # Check port cardinality limits
        for edge in self._edges:
            tgt_port = edge.target_port
            cardinality = tgt_port.get("cardinality", "many")
            port_id = tgt_port.get("id", "")
            if cardinality == "one" and port_id:
                conn_count = target_port_connections.get(port_id, 0)
                if conn_count > 1:
                    errors.append(
                        f"WARNING: Port '{tgt_port.get('name', '?')}' on "
                        f"'{edge.target_node.display_name}' has cardinality='one' "
                        f"but has {conn_count} incoming connections."
                    )

        # Check binding type compatibility
        for edge in self._edges:
            tgt_port = edge.target_port
            binding_type = tgt_port.get("binding_type", "param")
            if edge.edge_type == "port_to_port" and binding_type == "param":
                # port_to_port connections should go to handle or all ports
                src_types = set(
                    edge.source_port.get("port_type", {}).get("accepted_types", [])
                )
                # Only warn if source isn't a tools/memory type (those are handle types)
                if not (src_types & {"tools", "memory"}):
                    errors.append(
                        f"WARNING: Port-to-port edge from "
                        f"'{edge.source_node.display_name}' to "
                        f"'{edge.target_node.display_name}' targets a param-binding port. "
                        f"This may not work as expected — use target_param for data flow."
                    )
            if edge.edge_type == "port_to_param" and binding_type == "handle":
                errors.append(
                    f"WARNING: Data edge from '{edge.source_node.display_name}' to "
                    f"'{edge.target_node.display_name}' targets a handle-binding port. "
                    f"Handle ports are for tool/memory references, not data flow."
                )

        return errors

    # -- Screenshot --------------------------------------------------------

    def generate_screenshot_svg(self) -> str:
        """
        Generate an SVG screenshot of the workflow layout from node positions and edges.
        Returns a base64 data URL string (data:image/svg+xml;base64,...).
        """
        import base64

        if not self._nodes:
            return ""

        # Node type to color mapping
        type_colors = {
            "agent": "#4F46E5",  # indigo
            "tool": "#0891B2",  # cyan
            "input": "#059669",  # emerald
            "output": "#D97706",  # amber
            "governance": "#DC2626",  # red
        }
        default_color = "#6366F1"

        # Calculate bounds
        padding = 60
        node_w, node_h = 220, 60
        positions = [n.position for n in self._nodes]
        min_x = min(p["x"] for p in positions) - padding
        min_y = min(p["y"] for p in positions) - padding
        max_x = max(p["x"] for p in positions) + node_w + padding
        max_y = max(p["y"] for p in positions) + node_h + padding

        vw = max_x - min_x
        vh = max_y - min_y

        # Build SVG
        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x} {min_y} {vw} {vh}" '
            f'width="1024" height="{int(1024 * vh / vw)}">'
        ]

        # Background
        svg_parts.append(
            f'<rect x="{min_x}" y="{min_y}" width="{vw}" height="{vh}" '
            f'fill="#0F172A" rx="8"/>'
        )

        # Build node id -> position lookup for edges
        node_positions = {}
        for n in self._nodes:
            cx = n.position["x"] + node_w / 2
            cy = n.position["y"] + node_h / 2
            node_positions[n.instance_id] = (cx, cy)

        # Draw edges
        for e in self._edges:
            src = node_positions.get(e.source_node.instance_id)
            tgt = node_positions.get(e.target_node.instance_id)
            if src and tgt:
                # Curved path
                mx = (src[0] + tgt[0]) / 2
                svg_parts.append(
                    f'<path d="M {src[0]} {src[1] + node_h / 2} '
                    f"C {mx} {src[1] + node_h / 2}, {mx} {tgt[1] - node_h / 2}, "
                    f'{tgt[0]} {tgt[1] - node_h / 2}" '
                    f'stroke="#475569" stroke-width="2" fill="none" opacity="0.7"/>'
                )

        # Draw nodes
        for n in self._nodes:
            x = n.position["x"]
            y = n.position["y"]
            nt = n.node_type.lower()
            color = type_colors.get(nt, default_color)
            label = n.display_name
            # Truncate label
            if len(label) > 24:
                label = label[:22] + ".."

            # Node rectangle
            svg_parts.append(
                f'<rect x="{x}" y="{y}" width="{node_w}" height="{node_h}" '
                f'rx="8" fill="#1E293B" stroke="{color}" stroke-width="2"/>'
            )
            # Type indicator bar
            svg_parts.append(
                f'<rect x="{x}" y="{y}" width="6" height="{node_h}" '
                f'rx="3" fill="{color}"/>'
            )
            # Label text
            svg_parts.append(
                f'<text x="{x + 18}" y="{y + 25}" '
                f'font-family="Inter, system-ui, sans-serif" font-size="13" '
                f'fill="#F1F5F9" font-weight="600">{_svg_escape(label)}</text>'
            )
            # Type label
            svg_parts.append(
                f'<text x="{x + 18}" y="{y + 44}" '
                f'font-family="Inter, system-ui, sans-serif" font-size="10" '
                f'fill="#94A3B8">{_svg_escape(nt)}</text>'
            )

        svg_parts.append("</svg>")
        svg_str = "\n".join(svg_parts)

        # Encode as base64 data URL
        b64 = base64.b64encode(svg_str.encode("utf-8")).decode("utf-8")
        return f"data:image/svg+xml;base64,{b64}"

    # -- Serialization -----------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize the workflow to the import-compatible JSON dict."""
        return {
            "metadata": {},
            "mcp_code": None,
            "color": "",
            "entry_node_id": None,
            "icon": "",
            "edges": [e.to_dict() for e in self._edges],
            "session_id": self.session_id,
            "description": self.description,
            "publish_to_template": False,
            "publish_to_mcp": False,
            "publish_to_api": False,
            "api_code": None,
            "bg_color": "",
            "nodes": [n.to_dict() for n in self._nodes],
            "name": self.name,
            "id": self.id,
            "global_context": {},
        }

    def save(self, path: str) -> str:
        """Save the workflow to a JSON file. Returns the absolute path."""
        data = self.to_dict()
        p = Path(path).resolve()
        with open(p, "w") as f:
            json.dump(data, f, indent=2)
        return str(p)

    # -- Publishing --------------------------------------------------------

    def publish(
        self,
        base_url: str,
        org_id: str,
        *,
        auth_token: str | None = None,
    ) -> dict:
        """
        Publish the workflow to a Capitol Platform instance.

        Args:
            base_url: Agentic backend base URL (e.g. "https://ey.capitol.ai").
            org_id: Organisation ID to publish under.
            auth_token: X-User-Token for user authentication.

        Returns:
            The JSON response from the server.

        Raises:
            ValueError: If no auth token is provided.
            requests.HTTPError: If the request fails.
        """
        if requests is None:
            raise ImportError(
                "The 'requests' package is required for publishing. "
                "Install it with: pip install requests"
            )

        if not auth_token:
            raise ValueError("auth_token is required for publishing.")

        # [VÁ CỦA MÌNH 03/11/2026 — chay_thu/sua_doi/README.md] gốc: /workflow/{org_id}/persist-workflow (đường cũ, lưu qua platform-api bằng
        # đăng nhập thật ⇒ máy mình 401). Đường v1 lưu thẳng PostgreSQL, trả PersistWorkflowResponse cùng khuôn (AB api/v1/schemas/workflow.py:201).
        url = f"{base_url.rstrip('/')}/api/v1/orgs/{org_id}/workflows"

        headers = {"Content-Type": "application/json"}
        headers["X-User-Token"] = auth_token

        payload = self.to_dict()
        # "Publish" must mean runnable: flip publish_to_api so the platform
        # (and chat agents' native list_workflows/start_workflow tools) can
        # see and execute the workflow. A bare persist leaves
        # publish_to_api=false -- the workflow saves as an invisible draft
        # and every runner reports "not found / not in catalog".
        payload["publish_to_api"] = True

        # Generate and include screenshot
        try:
            screenshot = self.generate_screenshot_svg()
            if screenshot:
                payload["screenshot"] = screenshot
        except Exception:
            pass  # Non-fatal: publish without screenshot

        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        return _require_successful_publish_response(resp.json())

    def publish_local(
        self,
        agentic_backend_url: str,
        org_id: str,
        *,
        auth_token: str | None = None,
    ) -> dict:
        """
        Publish the workflow to a local agentic backend.

        Uses the same persist-workflow endpoint as publish(), but intended
        for local/Docker development where the agentic backend is reachable
        at a Docker-internal URL (e.g. http://agentic-backend:80).

        Args:
            agentic_backend_url: Agentic backend URL
                (e.g. "http://agentic-backend:80").
            org_id: Organisation ID to publish under.
            auth_token: X-User-Token for user authentication.

        Returns:
            The JSON response from the server, normalised to include
            workflow_id, node_count, edge_count, message.

        Raises:
            ValueError: If no auth token is provided.
            requests.HTTPError: If the request fails.
        """
        if requests is None:
            raise ImportError(
                "The 'requests' package is required for publishing. "
                "Install it with: pip install requests"
            )

        if not auth_token:
            raise ValueError("auth_token is required for publishing.")

        # [VÁ CỦA MÌNH 03/11/2026 — chay_thu/sua_doi/README.md] gốc: /workflow/{org_id}/persist-workflow — như publish() ở trên.
        url = f"{agentic_backend_url.rstrip('/')}/api/v1/orgs/{org_id}/workflows"

        headers = {"Content-Type": "application/json"}
        headers["X-User-Token"] = auth_token

        payload = self.to_dict()
        # "Publish" must mean runnable: flip publish_to_api so the platform
        # (and chat agents' native list_workflows/start_workflow tools) can
        # see and execute the workflow. A bare persist leaves
        # publish_to_api=false -- the workflow saves as an invisible draft
        # and every runner reports "not found / not in catalog".
        payload["publish_to_api"] = True

        # Generate and include screenshot
        try:
            screenshot = self.generate_screenshot_svg()
            if screenshot:
                payload["screenshot"] = screenshot
        except Exception:
            pass  # Non-fatal: publish without screenshot

        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        result = _require_successful_publish_response(resp.json())
        workflow_id = result["workflow_id"]
        return {
            "success": True,
            "workflow_id": workflow_id,
            "orgid": org_id,
            "node_count": len(payload.get("nodes", [])),
            "edge_count": len(payload.get("edges", [])),
            "message": result.get("message", "Published to local backend successfully"),
        }

    @classmethod
    def load(
        cls,
        path: str,
        catalog_path: str | None = None,
        catalog: dict[str, dict] | None = None,
    ) -> "Workflow":
        """
        Load a workflow from an existing JSON file.

        This allows editing workflows that were exported from the Composer.
        If neither *catalog* nor *catalog_path* is given, the workflow is loaded
        with an empty catalog — unknown nodes are preserved as-is from the JSON.
        """
        with open(path) as f:
            data = json.load(f)

        # When loading a saved workflow, we don't require a catalog —
        # nodes are reconstructed from the embedded JSON structures.
        if catalog is None and catalog_path is None:
            catalog = {}

        wf = cls(
            name=data.get("name", "Imported Workflow"),
            description=data.get("description", ""),
            catalog_path=catalog_path,
            catalog=catalog,
        )
        wf.id = data.get("id", _new_uuid())
        wf.session_id = data.get("session_id", _new_uuid())

        # Reconstruct nodes
        node_map: dict[str, Node] = {}
        for nd in data.get("nodes", []):
            struct = nd.get("data", {}).get("struct", {})
            nid = struct.get("node_id", "unknown")
            instance_id = nd.get(
                "id", struct.get("react_flow_generated_id", _new_uuid())
            )
            pos = nd.get("position", {"x": 0, "y": 0})

            # Try to get catalog entry; if not in catalog, use struct as-is
            catalog_entry = wf._catalog.get(nid)
            if catalog_entry:
                node = Node(
                    nid,
                    catalog_entry,
                    instance_id=instance_id,
                    position=(pos["x"], pos["y"]),
                )
                # Restore param values from the loaded data
                for p_loaded in struct.get("params", []):
                    fid = p_loaded.get("field_id")
                    if fid:
                        try:
                            node.set_param(fid, p_loaded.get("value"))
                        except ValueError:
                            pass
                # Restore bindable configs
                for p_loaded in struct.get("params", []):
                    fid = p_loaded.get("field_id")
                    bc = p_loaded.get("bindable_config")
                    if bc and fid:
                        p_node = _find_param(node.struct, fid)
                        if p_node:
                            p_node["bindable_config"] = copy.deepcopy(bc)
                # Restore input ports with connections
                node.struct["input_ports"] = copy.deepcopy(
                    struct.get("input_ports", [])
                )
                node.struct["output_ports"] = copy.deepcopy(
                    struct.get("output_ports", [])
                )
                # Restore user display customizations
                if struct.get("user_config_name"):
                    node.set_display(
                        struct["user_config_name"],
                        struct.get("user_config_description", ""),
                    )
                # Restore composio_apps filter
                if struct.get("composio_apps"):
                    node.set_composio_apps(struct["composio_apps"])
            else:
                # Unknown node type - preserve the struct as-is
                node = Node.__new__(Node)
                node.node_id = nid
                node.instance_id = instance_id
                node.position = {"x": pos["x"], "y": pos["y"]}
                node._catalog = struct
                node.struct = copy.deepcopy(struct)

            wf._nodes.append(node)
            node_map[instance_id] = node

        # Reconstruct edges
        for ed in data.get("edges", []):
            src_id = ed.get("source_node_id", ed.get("source"))
            tgt_id = ed.get("target_node_id", ed.get("target"))
            src_node = node_map.get(src_id)
            tgt_node = node_map.get(tgt_id)
            if not src_node or not tgt_node:
                continue

            # Find source port
            src_port_id = ed.get("source_port_id", ed.get("sourceHandle"))
            src_port = None
            for p in src_node.struct.get("output_ports", []):
                if p["id"] == src_port_id:
                    src_port = p
                    break
            if not src_port:
                src_port = {"id": src_port_id, "port_type": {"accepted_types": []}}

            # Find target port
            tgt_port_id = ed.get("target_port_id", ed.get("targetHandle"))
            tgt_port = None
            # Check input ports
            for p in tgt_node.struct.get("input_ports", []):
                if p["id"] == tgt_port_id:
                    tgt_port = p
                    break
            # Check bindable params
            if not tgt_port:
                for p in tgt_node.struct.get("params", []):
                    bc = p.get("bindable_config", {})
                    ip = bc.get("input_port", {})
                    if ip.get("id") == tgt_port_id:
                        tgt_port = ip
                        break
            if not tgt_port:
                tgt_port = {"id": tgt_port_id, "port_type": {"accepted_types": []}}

            edge = Edge(
                source_node=src_node,
                source_port=src_port,
                target_node=tgt_node,
                target_port=tgt_port,
                target_param_id=ed.get("target_param_id"),
                edge_type=ed.get("edge_type", "port_to_param"),
            )
            wf._edges.append(edge)

        return wf

    def __repr__(self) -> str:
        return (
            f"Workflow({self.name!r}, nodes={len(self._nodes)}, "
            f"edges={len(self._edges)})"
        )


# ---------------------------------------------------------------------------
# Standalone publish function
# ---------------------------------------------------------------------------


def publish_workflow(
    filepath: str,
    *,
    base_url: str | None = None,
    org_id: str | None = None,
    auth_token: str | None = None,
    catalog_path: str | None = None,
) -> dict:
    """
    Publish a workflow JSON file to a Capitol Platform instance.

    Args:
        filepath: Path to the workflow JSON file.
        base_url: Platform URL. Falls back to CAPITOL_BASE_URL env var.
        org_id: Organisation ID. Must come from the user's session.
        auth_token: Auth token (user JWT). Must come from the user's session.
        catalog_path: Optional path to node catalog for loading.

    Returns:
        The JSON response from the server.

    Raises:
        ValueError: If base_url, org_id, or auth_token are not provided.
        requests.HTTPError: If the request fails.
    """
    base_url = base_url or os.environ.get("CAPITOL_BASE_URL")

    if not base_url:
        raise ValueError(
            "base_url is required. Pass it directly or set CAPITOL_BASE_URL env var."
        )
    if not org_id:
        raise ValueError(
            "org_id is required. It must come from the user's session (JWT/platform-api)."
        )
    if not auth_token:
        raise ValueError(
            "auth_token is required. It must come from the user's session (JWT/platform-api)."
        )

    wf = Workflow.load(filepath, catalog_path=catalog_path)

    # Log Composio node details for runtime debugging
    composio_nodes = [n for n in wf._nodes if n.node_id == "EXECUTE_COMPOSIO_TOOL"]
    if composio_nodes:
        for cn in composio_nodes:
            logger.info(
                "[COMPOSIO:PUBLISH] Publishing workflow with Composio node=%s "
                "composio_apps=%s mcp_server=%s org=%s filepath=%s",
                cn.display_name,
                cn.struct.get("composio_apps", []),
                cn.struct.get("mcp_server", "(unset)"),
                org_id,
                filepath,
            )

    errors = wf.lint()
    lint_errors = [e for e in errors if e.startswith("ERROR")]
    if lint_errors:
        raise RuntimeError(
            f"Workflow has {len(lint_errors)} lint error(s) and cannot be published: "
            + "; ".join(lint_errors)
        )

    result = _require_successful_publish_response(
        wf.publish(
            base_url,
            org_id,
            auth_token=auth_token,
        )
    )

    # Strip the full workflow definition from the result — it can be hundreds
    # of KB and causes [object Object] rendering when the LLM tries to
    # reference nested objects in its response text.
    result.pop("workflow", None)

    # Construct the user-facing workflow URL using the platform frontend URL,
    # NOT base_url (which may be an internal K8s service address).
    workflow_id = result.get("workflow_id")
    if workflow_id:
        platform_fe = os.environ.get("PLATFORM_FRONTEND_URL", "").rstrip("/")
        if platform_fe:
            result["workflow_url"] = f"{platform_fe}/workflow/{workflow_id}"
        elif base_url:
            # Fallback: extract origin from base_url (local dev)
            from urllib.parse import urlparse

            parsed = urlparse(base_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            result["workflow_url"] = f"{origin}/workflow/{workflow_id}"

    return result


def publish_workflow_local(
    filepath: str,
    *,
    agentic_backend_url: str | None = None,
    platform_frontend_url: str | None = None,
    org_id: str | None = None,
    auth_token: str | None = None,
    catalog_path: str | None = None,
) -> dict:
    """
    Publish a workflow JSON file to the local agentic backend.

    This is the local-dev publish path. It targets the locally running
    agentic backend via the persist-workflow endpoint.

    Args:
        filepath: Path to the workflow JSON file.
        agentic_backend_url: Agentic backend URL.
            Falls back to AGENTIC_BACKEND_URL env var, then "http://localhost:8300".
        platform_frontend_url: Platform frontend URL for constructing
            workflow view URLs. Falls back to PLATFORM_FRONTEND_URL env var,
            then "http://localhost:5173".
        org_id: Organisation ID. Must come from the user's session.
        auth_token: Auth token (user JWT). Must come from the user's session.
        catalog_path: Optional path to node catalog for loading.

    Returns:
        The JSON response from the server.

    Raises:
        ValueError: If org_id or auth_token are not provided.
        requests.HTTPError: If the request fails.
    """
    agentic_backend_url = (
        agentic_backend_url
        or os.environ.get("AGENTIC_BACKEND_URL")
        or "http://localhost:8300"
    )
    platform_frontend_url = (
        platform_frontend_url
        or os.environ.get("PLATFORM_FRONTEND_URL")
        or "http://localhost:5173"
    )
    if not org_id:
        raise ValueError(
            "org_id is required. It must come from the user's session (JWT/platform-api)."
        )
    if not auth_token:
        raise ValueError(
            "auth_token is required. It must come from the user's session (JWT/platform-api)."
        )

    wf = Workflow.load(filepath, catalog_path=catalog_path)

    # Log Composio node details for runtime debugging
    composio_nodes = [n for n in wf._nodes if n.node_id == "EXECUTE_COMPOSIO_TOOL"]
    if composio_nodes:
        for cn in composio_nodes:
            logger.info(
                "[COMPOSIO:PUBLISH-LOCAL] Publishing workflow with Composio node=%s "
                "composio_apps=%s mcp_server=%s org=%s filepath=%s",
                cn.display_name,
                cn.struct.get("composio_apps", []),
                cn.struct.get("mcp_server", "(unset)"),
                org_id,
                filepath,
            )

    errors = wf.lint()
    lint_errors = [e for e in errors if e.startswith("ERROR")]
    if lint_errors:
        raise RuntimeError(
            f"Workflow has {len(lint_errors)} lint error(s) and cannot be published: "
            + "; ".join(lint_errors)
        )

    result = _require_successful_publish_response(
        wf.publish_local(
            agentic_backend_url,
            org_id,
            auth_token=auth_token,
        )
    )

    # Strip the full workflow definition from the result — it can be hundreds
    # of KB and causes [object Object] rendering when the LLM tries to
    # reference nested objects in its response text.
    result.pop("workflow", None)

    # Construct the workflow URL using the local platform frontend
    workflow_id = result.get("workflow_id")
    if workflow_id:
        result["workflow_url"] = (
            f"{platform_frontend_url.rstrip('/')}/workflow/{workflow_id}"
        )

    return result
