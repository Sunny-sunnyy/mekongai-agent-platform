import logging
from typing import Any
from uuid import uuid4

from workflow.node.base.param.base import BindableConfig, NodeParams, ParamTypes
from workflow.node.base.param.custom import (
    BoolInput,
    IntegerInput,
    MultilineTextInput,
)
from workflow.node.base.port.base import Cardinality, InputPort
from workflow.node.base.port.type import PortType


def _la_goi_kpi(x: Any) -> bool:   # [ghi chú] gói của Rào chắn chế độ code
    return isinstance(x, dict) and {"bang_thuong", "sha256_bang"} <= set(x)


def text_reducer(input_data: Any):
    # [ghi chú] gói KPI (dict, hoặc danh sách đúng 1 gói) giữ NGUYÊN object — khối Người duyệt chế độ KPI đọc trường; mọi đầu vào khác như Capitol gốc
    if _la_goi_kpi(input_data):
        return input_data
    if isinstance(input_data, list) and len(input_data) == 1 and _la_goi_kpi(input_data[0]):
        return input_data[0]
    if isinstance(input_data, list):
        return "\n".join(str(item) for item in input_data)
    elif isinstance(input_data, str):
        return input_data
    else:
        return str(input_data)


logger = logging.getLogger(__name__)


prompt_param = MultilineTextInput(
    display_name="Preview Context",
    field_id="preview_context",
    show=False,
    required=True,
    valid_types=[ParamTypes.TEXT],
    bindable_config=BindableConfig(
        is_bound=False,
        input_port=InputPort(
            id=str(uuid4()),
            name="text",
            display_name="Text Input",
            info="Accepts different types of inputs",
            cardinality=Cardinality.MANY,
            port_type=PortType(
                accepted_types=[
                    ParamTypes.TEXT,
                    ParamTypes.URL,
                    ParamTypes.BOOLEAN,
                    ParamTypes.INTEGER,
                    ParamTypes.FLOAT,
                    ParamTypes.DICT,
                    ParamTypes.LIST,
                ]
            ),
            binding_type="param",
            optional=True,
            reducer=text_reducer,
        ),
    ),
    info="A clear description of what you want the user to review or provide input for in this step",
    placeholder="Enter the message to display to the user...",
)


timeout_param = IntegerInput(
    display_name="Timeout (seconds)",
    field_id="timeout",
    required=True,
    default=300,
    valid_types=[ParamTypes.INTEGER],
    info="Time in seconds to wait for user response (maximum wait time: 1 hour)",
    placeholder="300",
    max_value=3600,
)


auto_approve_param = BoolInput(
    display_name="Auto-approve on timeout",
    field_id="auto_approve_on_timeout",
    required=False,
    default=False,
    valid_types=[ParamTypes.BOOLEAN],
    info=(
        "If no one responds before the timeout, continue the workflow as if "
        "approved. When off, the run fails."
    ),
)

# [ghi chú] ô TĨNH ghi chú cho người duyệt: KHÔNG có bindable_config (không nối dây được, như sticky_note
# params.py:17) ⇒ không bao giờ vào preview_context / data / cổng ra. Lời dặn cho người duyệt viết ở đây thay vì gõ vào preview_context
# (gõ vào đó thì bị gửi xuống khối sau — C, GHI_CHU_DOC_CODE.md #5; node.py:700-716 gốc). Chỉ hiện khi cờ CHAY_THU_NGUOI_DUYET bật.
reviewer_note_param = MultilineTextInput(
    display_name="Ghi chú cho người duyệt (không gửi xuống khối sau)",
    field_id="reviewer_note",
    required=False,
    # [D m2 mục 61] chỉ hứa điều code bảo đảm: không vào dữ liệu ra cổng. Ghi chú VẪN nằm trong kết quả hiển thị / tin tiến độ của khối
    # (display_model, emit_progress) ⇒ ai đọc kết quả khối (giao diện, API, A2A) vẫn thấy — KHÔNG hứa "chỉ người duyệt đọc".
    info=("KHÔNG gửi xuống khối sau (không vào dữ liệu ra cổng). Hiện cùng nguyên văn chữ sẽ gửi. Lưu ý: vẫn nằm trong kết quả "
          "hiển thị của khối — ai xem kết quả khối vẫn đọc được; đừng ghi mật khẩu / thông tin không được lộ."),
    placeholder="Ví dụ: kiểm số tiền và tên khách trước khi gửi",
    valid_types=[ParamTypes.TEXT],
)


def _nguoi_duyet_bat() -> bool:   # [D m1 mục 61] ô chỉ có trong danh mục khi cờ bật (cờ tắt ⇒ danh mục y như Capitol gốc)
    import os

    return os.environ.get("CHAY_THU_NGUOI_DUYET", "true").strip().lower() not in ("false", "0", "no", "off")


# [ghi chú] ô bật chế độ KPI (chỉ có khi cờ CHAY_THU_NGUOI_DUYET bật, như reviewer_note)
che_do_kpi_param = BoolInput(
    display_name="Chế độ KPI (duyệt bảng thưởng từ lõi)",
    field_id="che_do_kpi",
    required=False,
    default=False,
    valid_types=[ParamTypes.BOOLEAN],
    info=("Đầu vào phải là gói của Rào chắn chế độ code. Hiện nguyên văn bảng + mã băm, KHÔNG tóm tắt bằng AI; rỗng / hết giờ = KHÔNG duyệt; "
          "mở cổng kiểm lại mã băm."),
)

PARAMS = NodeParams(
    [
        prompt_param,
        timeout_param,
        auto_approve_param,
    ]
    + ([reviewer_note_param, che_do_kpi_param] if _nguoi_duyet_bat() else [])
)
