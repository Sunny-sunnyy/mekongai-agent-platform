from typing import Any
from uuid import uuid4

from nodes.governance.guardrails.utils import fetch_guardrails_sync
from workflow.node.base.param.base import BindableConfig, NodeParams, OrgConfig
from workflow.node.base.param.custom import DropdownInput, MultilineTextInput, MultiselectInput
from workflow.node.base.port.base import InputPort
from workflow.node.base.port.enums import Cardinality
from workflow.node.base.port.type import ParamTypes, PortType


def text_reducer(input_data: Any):
    """Convert input data to a string format suitable for guardrail checks.

    Parameters
    ----------
    input_data : Any
        The input data to be converted, which can be a list, string, or any other type.

    Returns
    -------
    str
        A string representation of the input data.
        - For lists, joins elements with newlines
        - For strings, returns the original string
        - For other types, converts to string using str()

    Notes
    -----
    This function is used as a reducer for the guardrail query input port to ensure
    that any input data is properly formatted as text for guardrail checks.

    Examples
    --------
    ```
    # List input
    result = text_reducer(["Hello", "World"])
    # Returns: "Hello\nWorld"

    # String input
    result = text_reducer("Hello World")
    # Returns: "Hello World"

    # Other input
    result = text_reducer(123)
    # Returns: "123"
    ```
    """
    if isinstance(input_data, list):
        return "\n".join(str(item) for item in input_data)
    elif isinstance(input_data, str):
        return input_data
    else:
        return str(input_data)


def get_guardrails_for_org(orgid: str, user_token: str) -> dict:
    """Fetch available guardrails for an organization.

    Parameters
    ----------
    orgid : str
        The organization ID for which to fetch guardrails.
    user_token : str
        The user token for authentication.

    Returns
    -------
    dict
        A dictionary containing the "options" key with a list of available guardrails.

    Notes
    -----
    This function is used as a helper utility for the OrgConfig to dynamically
    populate the options for the guardrails multiselect input based on the
    organization's available guardrails.

    Examples
    --------
    ```
    guardrails_options = get_guardrails_for_org("org-123", "user-token-abc")
    # Returns: {"options": [{"option_id": "guardrail-1", "option_display_name": "Harmful Content"}]}
    ```
    """
    guardrails = fetch_guardrails_sync(orgid, user_token)
    return {"options": guardrails}


guardrail_query = MultilineTextInput(
    field_id="guardrail_query",
    required=True,
    display_name="query",
    info="Content to check against the selected guardrails",
    placeholder="Enter the input query",
    bindable_config=BindableConfig(
        is_bound=True,
        input_port=InputPort(
            id=str(uuid4()),
            name="text",
            display_name="Guardrail Check Content",
            info="Text content to check against guardrails",
            cardinality=Cardinality.MANY,
            port_type=PortType(accepted_types=[ParamTypes.TEXT]),
            binding_type="param",
            optional=False,
            reducer=text_reducer,
        ),
    ),
)


selected_guardrails = MultiselectInput(
    field_id="guardrails_list",
    required=False,   # [ghi chú] chế độ "code" không dùng luật AI ⇒ không bắt chọn; chế độ "ai" + cờ chặn: rỗng ⇒ trượt (node.py)
    display_name="Select guardrails",
    info="Select guardrail checks to be run",
    options=[],  # Empty by default, will be populated by org_config
    org_config=OrgConfig(
        is_org_specific=True,
        helper_util=get_guardrails_for_org,
    ),
)

# [ghi chú] chế độ kiểm: "ai" = đúng Capitol (AI chấm theo luật) · "code" = gọi dịch vụ kiểm của mình
# (backend KPI mekongai), KHÔNG dùng AI, trượt / lỗi ⇒ DỪNG lượt. Địa chỉ + khoá dịch vụ KHÔNG nằm ở ô này (người gửi payload sửa được ⇒ SSRF /
# lộ khoá) — đọc từ biến môi trường của hộp (node.py, _KIEM_CODE_*).
che_do_kiem = DropdownInput(
    field_id="che_do_kiem",
    required=False,
    display_name="Chế độ kiểm",
    default="ai",
    options=["ai", "code", "sql"],   # [va_kiem_so_sql C 12/11] + "sql"
    info="ai: AI chấm theo luật đã chọn (Capitol). code: dịch vụ kiểm số KPI bằng code, không AI. sql: mọi số trong câu trả lời phải có trong kết quả "
         "truy vấn SQL của chính lượt này, không AI. code / sql: trượt hoặc lỗi thì dừng lượt.",
)

# [ghi chú] câu hỏi của người dùng, CHỈ để dịch vụ kiểm biết số nào người hỏi tự nêu ("Trà 365",
# "nếu thêm 3 cửa hàng"). Chỉ chế độ "code" dùng. Luật "số trong câu hỏi không được làm nguồn cho số kết quả" nằm ở dịch vụ kiểm (mekongai).
# RỦI RO: ô này nối từ Agent (hoặc khối sau Agent) ⇒ mọi số AI viết đều "có trong câu hỏi" ⇒ workflow_kpi.kiem_payload chỉ cho nối từ
# text_input_node không phải hậu duệ Agent, cấm giá trị gõ sẵn. Không nối ⇒ rỗng (không tự lấy từ đâu khác).
cau_hoi = MultilineTextInput(
    field_id="cau_hoi",
    required=False,
    display_name="Câu hỏi của người dùng",
    info="Chỉ nối từ ô nhập câu hỏi. Dịch vụ kiểm dùng để nhận ra số người hỏi tự nêu; không phải nguồn số.",
    placeholder="",
    bindable_config=BindableConfig(
        is_bound=True,
        input_port=InputPort(
            id=str(uuid4()),
            name="cau_hoi",
            display_name="Câu hỏi",
            info="Câu hỏi gốc từ ô nhập",
            cardinality=Cardinality.ONE,
            port_type=PortType(accepted_types=[ParamTypes.TEXT]),
            binding_type="param",
            optional=True,
            reducer=text_reducer,
        ),
    ),
)

# [ghi chú] bản dữ liệu KPI (cùng giá trị ô kpi_data_version của khối Agent — workflow_kpi.kiem_payload so bằng nhau)
kpi_data_version = MultilineTextInput(
    field_id="kpi_data_version",
    required=False,
    display_name="Bản dữ liệu KPI (hệ thống điền)",
    info="Mã bản dữ liệu (hex 1–64) — trống: bản lúc bật backend. Phải BẰNG ô cùng tên của khối Agent. Sai dạng ⇒ dừng.",
    placeholder="",
)

PARAMS = NodeParams([guardrail_query, selected_guardrails, che_do_kiem, cau_hoi, kpi_data_version])
