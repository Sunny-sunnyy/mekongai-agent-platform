"""Node instantiation utilities for Temporal activities.

Extracts and adapts the node creation logic from
``WorkflowExecutor._instantiate_node_with_handles`` and
``_instantiate_handle_node`` so that per-node activities can build
``BaseNode`` instances from serialised ``NodeInstance`` dicts without
needing a live ``WorkflowExecutor``.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from typing import Any

from workflow.node.base.node import BaseNode
from workflow.node.base.port.base import InputPort, OutputPort

logger = logging.getLogger(__name__)


class _SensitiveStr(str):
    """A ``str`` subclass whose ``repr`` / ``str`` is redacted.

    Used to store bearer tokens (e.g. OBO Token C) as node attributes so
    they are opaque in log output, exception tracebacks, and any generic
    ``vars(node)`` / ``node.__dict__`` dumps — while still behaving as a
    plain string for HTTP header construction.
    """

    def __repr__(self) -> str:
        length = len(self)
        return f"<redacted {length}-char token>" if length else "<empty token>"

    def __str__(self) -> str:  # type: ignore[override]
        return self.__repr__()


# [va_so_tu_choi] BẰNG sandbox/chay_s5.py DAU_TU_CHOI_TRUOC_KHI_CHAY — đổi một bên phải đổi bên kia (ca thử T0 so hai chuỗi).
DAU_TU_CHOI_TRUOC_KHI_CHAY = "[CHAY_THU_TU_CHOI_TRUOC_KHI_CHAY]"
# [E K1 19/11] câu CỐ ĐỊNH cho người dùng — sổ so NGUYÊN VĂN ô user_message với câu này. Vì sao cần: lỗi ValueError / TypeError… ném TRONG lúc khối chạy cũng được
# bộ phân loại của Capitol xếp internal_error · không thử lại · technical_message = nguyên chữ lỗi ⇒ dấu ở technical_message GIẢ được; còn user_message của
# các lỗi đó là câu mẫu của Capitol, không sinh từ chữ lỗi ⇒ chỉ lỗi dựng Ở ĐÂY mới mang câu này. BẰNG sandbox/chay_s5.py LOI_TU_CHOI_NGUOI_DUNG.
LOI_TU_CHOI_NGUOI_DUNG = "Loại khối này chưa chạy được trên hệ thống — bị từ chối trước khi chạy, chưa có lời gọi AI nào."


def instantiate_node(
    node_dict: dict[str, Any],
    *,
    handle_nodes: list[BaseNode] | None = None,
    session_id: str = "",
    workflow_id: str = "",
    workflow_name: str = "",
    run_id: str = "",
    org_id: str = "",
    user_token: str = "",
    user_id: str = "",
    user_email: str = "",
    composio_config: dict[str, Any] | None = None,
    workflow_asset_mode: str | None = None,
    obo_access_token: str = "",
    workflow_special_instructions: str | None = None,
) -> BaseNode:
    """Build a ``BaseNode`` instance from a serialised ``NodeInstance`` dict.

    This replicates the logic of ``WorkflowExecutor._instantiate_node_with_handles``:
    dynamic import, constructor call, metadata wiring, port-ID overrides.

    Args:
        node_dict: Serialised ``NodeInstance`` from the workflow payload.
        handle_nodes: Pre-built tool / memory handle nodes to attach.
        session_id: Workflow session identifier.
        workflow_id: Workflow definition identifier.
        workflow_name: Human-readable workflow name.
        run_id: WorkflowRun.id (UUID string) -- distinct from session_id.
        org_id: Organisation identifier.
        user_token: Capitol platform user JWT for ``capitol_ai_platform_auth``
            MCP servers and internal service calls.
        user_id: User identifier string.
        user_email: User email address.
        composio_config: Composio integration configuration dict.
        workflow_asset_mode: Asset serving mode (``"presigned"`` etc.).
        obo_access_token: Plaintext Token C from Entra OBO swap #2, unsealed
            from ``WorkflowContext.obo_session`` by the activity layer.
            Forwarded to the node as ``_obo_access_token`` so MCP nodes with
            ``auth_type == "entra_obo"`` can use it as their
            ``Authorization: Bearer`` header.  Empty string for non-OneEdge
            workflows.
    """
    module_path = node_dict["module"]
    class_name = node_dict["class_name"]

    # [VÁ nạp khối theo mã, chay_thu 09/11] class lấy từ sổ đăng ký theo node_id, không import theo module / class_name
    # của yêu cầu — chay_thu/va_nap_khoi/nap_khoi.py
    from workflow.nap_khoi import lay_class

    try:
        node_class = lay_class(node_dict.get("node_id"), module_path, class_name)
    except ValueError as _e:
        # [ghi chú] Hộp TỪ CHỐI loại khối TRƯỚC khi khối được dựng
        # (chưa có đối tượng khối ⇒ chưa thể có lời gọi AI / công cụ trả phí nào). Trước: ValueError trần văng ra NGOÀI bộ phân loại lỗi của Capitol
        # (temporal/activities/_shared.py:419) ⇒ Temporal thử lại như lỗi tạm, kết quả khối chỉ còn "Activity task failed" ⇒ sổ tiền không phân biệt được
        # với khối chết giữa chừng và ghi MỨC TỐI ĐA × số lần thử (lượt 6ab50122: 0,6 USD cho 0 lời gọi). Nay: lỗi CÓ KIỂU, KHÔNG thử lại, mang DẤU ở đầu
        # technical_message — sandbox/chay_s5.py đọc dấu này (cùng 4 điều kiện khác) để ghi 0 đồng có nhãn. DẤU CHỈ ĐƯỢC ĐẶT Ở ĐÂY (trước khi dựng khối).
        from temporalio.exceptions import ApplicationError

        from temporal.errors.error_info import ErrorCode, build_error_info

        _tm = f"{DAU_TU_CHOI_TRUOC_KHI_CHAY} {_e}"
        try:   # [E K2] số lần thử THẬT của activity (Temporal đếm từ 1); không ở trong activity / không đọc được ⇒ 0 ⇒ sổ KHÔNG nhận là "đúng 1 lần thử" ⇒ ghi mức tối đa
            from temporalio import activity as _act

            _lan = int(_act.info().attempt)
        except Exception:  # noqa: BLE001
            _lan = 0
        raise ApplicationError(
            _tm,
            build_error_info(
                code=ErrorCode.INTERNAL_ERROR,
                technical_message=_tm,
                user_message=LOI_TU_CHOI_NGUOI_DUNG,
                attempts_made=_lan,
                actionable_hint="Báo người quản trị kèm mã lượt chạy; chạy lại sẽ không khác.",
            ),
            type="InternalError",
            non_retryable=True,
        ) from _e

    params_raw = node_dict.get("params", [])
    node_config = [p if isinstance(p, dict) else p for p in params_raw]

    init_sig = inspect.signature(node_class.__init__)
    if "handle_nodes" in init_sig.parameters and handle_nodes:
        node = node_class(
            node_config=node_config,
            node_inputs=None,
            validate_required=False,
            validate_type=False,
            handle_nodes=handle_nodes,
        )
    else:
        node = node_class(
            node_config=node_config,
            node_inputs=None,
            validate_required=False,
            validate_type=False,
        )
        if handle_nodes:
            node.handle_nodes = handle_nodes

    node.node_id = node_dict["react_flow_generated_id"]
    node.display_name = node_dict.get("display_name") or node_dict.get("name", "")
    node.node_type = node_dict.get("node_type", "")
    node.node_sub_type = node_dict.get("node_sub_type")
    node._session_id = session_id
    node._workflowid = workflow_id
    node._workflow_name = workflow_name
    node._run_id = run_id
    node._orgid = org_id
    node._user_token = user_token
    node._user_id = user_id
    node._user_email = user_email
    node._composio_config = composio_config or {}
    node._workflow_asset_mode = workflow_asset_mode
    node._obo_access_token = _SensitiveStr(obo_access_token) if obo_access_token else ""
    node._workflow_special_instructions = workflow_special_instructions

    node.user_config_name = node_dict.get("user_config_name")
    node.user_config_description = node_dict.get("user_config_description")
    node.icon_color = node_dict.get("icon_color")

    _override_port_ids(node, node_dict)

    return node


def instantiate_handle_node(
    node_dict: dict[str, Any],
    *,
    session_id: str = "",
    workflow_id: str = "",
    org_id: str = "",
    user_token: str = "",
    user_id: str = "",
    user_email: str = "",
    composio_config: dict[str, Any] | None = None,
) -> BaseNode:
    """Build a handle (tool / memory) node from a serialised dict.

    Replicates ``WorkflowExecutor._instantiate_handle_node``.
    """
    node_type = node_dict.get("node_type", "")

    if node_type == "tool":
        return _instantiate_tool_node(
            node_dict,
            session_id=session_id,
            workflow_id=workflow_id,
            org_id=org_id,
            user_token=user_token,
            user_id=user_id,
            user_email=user_email,
            composio_config=composio_config,
        )

    return instantiate_node(
        node_dict,
        session_id=session_id,
        workflow_id=workflow_id,
        org_id=org_id,
        user_token=user_token,
        user_id=user_id,
        user_email=user_email,
        composio_config=composio_config,
    )


def _instantiate_tool_node(
    node_dict: dict[str, Any],
    **ctx: Any,
) -> BaseNode:
    """Replicate the dynamic ToolBaseNode class creation from WorkflowExecutor."""
    from nodes.tool.ports.output import OUTPUT_PORTS
    from workflow.node.base.param.base import NodeParams
    from workflow.node.base.param.custom import (
        BoolInput,
        FloatInput,
        IntegerInput,
        TextInput,
    )
    from workflow.node.base.tool import ToolBaseNode

    params_raw = node_dict.get("params", [])
    param_objects = []
    for p in params_raw:
        p_dict = p if isinstance(p, dict) else p
        valid_types = p_dict.get("valid_types") or ["str"]
        field_id = p_dict.get("field_id", "")
        display_name = p_dict.get("display_name", "")
        info = p_dict.get("info", "")
        required = p_dict.get("required", False)
        default = p_dict.get("default")
        value = p_dict.get("value")

        if "int" in valid_types:
            param_objects.append(
                IntegerInput(
                    field_id=field_id,
                    display_name=display_name,
                    info=info,
                    required=required,
                    default=default,
                    value=value,
                )
            )
        elif "float" in valid_types:
            param_objects.append(
                FloatInput(
                    field_id=field_id,
                    display_name=display_name,
                    info=info,
                    required=required,
                    default=default,
                    value=value,
                )
            )
        elif "bool" in valid_types:
            param_objects.append(
                BoolInput(
                    field_id=field_id,
                    display_name=display_name,
                    info=info,
                    required=required,
                    default=default,
                    value=value,
                )
            )
        else:
            param_objects.append(
                TextInput(
                    field_id=field_id,
                    display_name=display_name,
                    info=info,
                    required=required,
                    default=default or "",
                    value=value or "",
                )
            )

    mcp_server = node_dict.get("mcp_server") or ""
    composio_apps = node_dict.get("composio_apps") or []

    def _to_pascal(name: str) -> str:
        return "".join(w.capitalize() for w in name.replace("-", "_").split("_") if w)

    class_name = _to_pascal(node_dict.get("node_id", "tool")) + "Node"

    class_attrs: dict[str, Any] = {
        "version": node_dict.get("version") or "0.1.0",
        "display_name": node_dict.get("display_name") or node_dict.get("name", ""),
        "description": node_dict.get("description") or "",
        "icon": node_dict.get("icon") or "Tool",
        "show": node_dict.get("show", True),
        "beta": node_dict.get("beta", False),
        "can_be_tool": True,
        "node_id": node_dict.get("node_id", ""),
        "node_type": "tool",
        "node_sub_type": node_dict.get("node_sub_type") or "mcp_tool",
        "category": node_dict.get("category") or "Tools",
        "tool_name": node_dict.get("tool_name") or node_dict.get("name", ""),
        "mcp_server": mcp_server,
        "auth_type": node_dict.get("auth_type") or "none",
        "composio_apps": composio_apps,
        "input_ports": [],
        "output_ports": OUTPUT_PORTS,
        "params": NodeParams(param_objects),
        "_session_id": ctx.get("session_id", ""),
        "_orgid": ctx.get("org_id", ""),
        "_user_token": ctx.get("user_token", ""),
        "_user_id": ctx.get("user_id", ""),
        "_workflowid": ctx.get("workflow_id", ""),
        "_composio_config": ctx.get("composio_config") or {},
    }

    # A Composio tool baked to "" (no toolkit resolved) is tagged degraded so
    # the agent node warns instead of silently skipping it.
    if node_dict.get("composio_degraded") or (not mcp_server and composio_apps):
        class_attrs["_composio_degraded"] = True
        class_attrs["_composio_degraded_reason"] = (
            "This Composio tool node names no toolkit. Set its toolkit and re-run."
        )

    DynamicToolNode = type(class_name, (ToolBaseNode,), class_attrs)

    node_config = [p if isinstance(p, dict) else p for p in params_raw]
    node = DynamicToolNode(
        node_config=node_config,
        node_inputs=None,
        validate_required=False,
        validate_type=False,
    )

    node.node_id = node_dict["react_flow_generated_id"]
    if not hasattr(node, "mcp_server"):
        node.mcp_server = mcp_server

    return node


# ---------------------------------------------------------------------------
# Port-ID override  (replicate WorkflowExecutor._override_port_ids)
# ---------------------------------------------------------------------------


def _override_port_ids(node: BaseNode, node_dict: dict[str, Any]) -> None:
    """Override port IDs on *node* with the frontend-assigned IDs."""
    from workflow.node.base.port.type import PortType as RuntimePortType

    input_ports_data = node_dict.get("input_ports") or []
    if hasattr(node, "input_ports") and input_ports_data:
        new_ports: list[InputPort] = []
        for pd in input_ports_data:
            pd = pd if isinstance(pd, dict) else pd
            matched = next(
                (p for p in node.input_ports if p.name == pd.get("name")), None
            )
            if matched:
                new_ports.append(matched.model_copy(update={"id": pd["id"]}))
            else:
                pt = pd.get("port_type", {})
                accepted = (
                    pt.get("accepted_types", ["Any"])
                    if isinstance(pt, dict)
                    else ["Any"]
                )
                new_ports.append(
                    InputPort(
                        id=pd["id"],
                        name=pd.get("name", ""),
                        display_name=pd.get("display_name", ""),
                        info=pd.get("info", ""),
                        port_type=RuntimePortType(accepted_types=accepted),
                        cardinality=pd.get("cardinality", "one"),
                        binding_type=pd.get("binding_type", "handle"),
                        optional=pd.get("optional", True),
                    )
                )
        node.input_ports = new_ports

    output_ports_data = node_dict.get("output_ports") or []
    if hasattr(node, "output_ports") and output_ports_data:
        new_ports_out: list[OutputPort] = []
        for pd in output_ports_data:
            pd = pd if isinstance(pd, dict) else pd
            matched = next(
                (p for p in node.output_ports if p.name == pd.get("name")), None
            )
            if matched:
                new_ports_out.append(matched.model_copy(update={"id": pd["id"]}))
            else:
                pt = pd.get("port_type", {})
                accepted = (
                    pt.get("accepted_types", ["Any"])
                    if isinstance(pt, dict)
                    else ["Any"]
                )
                new_ports_out.append(
                    OutputPort(
                        id=pd["id"],
                        name=pd.get("name", ""),
                        display_name=pd.get("display_name", ""),
                        info=pd.get("info", ""),
                        port_type=RuntimePortType(accepted_types=accepted),
                        cardinality=pd.get("cardinality", "one"),
                        optional=pd.get("optional", False),
                    )
                )
        node.output_ports = new_ports_out
