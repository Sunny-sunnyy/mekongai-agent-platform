"""Params for the generic Agent node.

Every entry is a FRESH instance minted by the shared factories in
``workflow.node.base.agent.params`` (or a fresh local instantiation) —
never a shared object. See the factory module docstring for why param
identity matters.
"""

import logging

from nodes.agent.ports.input import files_input_port, text_input_port
from nodes.grounding import GroundingInput
from nodes.primary_output import PrimaryOutputExtensionsInput, PrimaryOutputInput
from workflow.node.base.agent.params import (
    make_agent_name_param,
    make_fast_mode_param,
    make_files_param,
    make_frequency_penalty_param,
    make_max_tokens_param,
    make_mcp_servers_param,
    make_model_param,
    make_output_feedback_param,
    make_parallel_tool_calls_param,
    make_presence_penalty_param,
    make_retries_param,
    make_seed_param,
    make_system_prompt_param,
    make_task_param,
    make_temperature_param,
    make_timeout_param,
    make_tool_choice_param,
    make_top_k_param,
    make_top_p_param,
)
from workflow.node.base.param.base import NodeParams
from workflow.node.base.param.custom import BoolInput, MultilineTextInput  # [VÁ 8]
from workflow.node.base.param.trace import TraceInput

logger = logging.getLogger(__name__)

PARAMS = NodeParams(
    [
        make_agent_name_param(),
        make_model_param(),
        make_system_prompt_param(
            default=(
                "You are a helpful assistant. Analyze the given information "
                "and provide detailed insights."
            ),
        ),
        GroundingInput(),
        make_task_param(input_port=text_input_port),
        make_files_param(input_port=files_input_port),
        make_mcp_servers_param(),
        # "Ask for feedback on outputs" review toggle (off by default).
        # Same field_id / runtime gate as the chat-output nodes'
        # interactive-feedback param; the pause only opens when the run
        # produced qualifying output files (see AgentBaseNode.execute).
        make_output_feedback_param(),
        TraceInput(),
        make_tool_choice_param(),
        make_temperature_param(),
        make_max_tokens_param(),
        make_parallel_tool_calls_param(),
        make_fast_mode_param(),
        make_timeout_param(),
        make_retries_param(),
        make_top_p_param(),
        make_top_k_param(),
        make_frequency_penalty_param(),
        make_presence_penalty_param(),
        make_seed_param(),
    ]
)

# [VÁ 8 chay_thu 04/11] công cụ gọi thẳng (nodes/agent/native_tools.py) + khuôn đầu ra có cấu trúc
# (workflow/node/base/agent/structured_output.py). Tên hiển thị tiếng Việt — User yêu cầu giao diện tiếng Việt.
PARAMS.root.extend([
    BoolInput(display_name="Công cụ: tra web (Serper)", field_id="tool_web_search", default=False,
              info="Cho agent tự tìm trên web khi cần thông tin công khai, mới, có nguồn. Mỗi lần tìm tốn một lượt Serper."),
    # [ghi chú] hỏi đáp trên file đã tải lên app RAG mekongai (chỉ tìm, không gọi luna bên đó) — KHÔNG đi cùng chạy code / công cụ KPI
    BoolInput(display_name="Công cụ: tìm trong tài liệu đã tải lên (RAG)", field_id="tool_rag_search", default=False,
              info="Cho agent tìm các trang khớp trong tài liệu đã tải lên (mỗi lần tìm tốn một lần tạo vector câu hỏi). Tối đa 5 lần / khối."),
    MultilineTextInput(display_name="Tài liệu được tìm (mỗi dòng một tên collection)", field_id="rag_collections", default="",
                       info="Tên collection in ra sau bước tải lên (va_rag/tai_len_rag.py). Chỉ tài liệu của người dùng thử capitol_thu."),
    BoolInput(display_name="Công cụ: đọc trang web", field_id="tool_fetch_url", default=False,
              info="Cho agent đọc nội dung chữ của một trang công khai (chặn địa chỉ mạng nội bộ). Thường dùng sau khi tra web."),
    BoolInput(display_name="Công cụ: giờ hiện tại", field_id="tool_current_time", default=False,
              info="Cho agent biết ngày giờ Việt Nam hiện tại."),
    BoolInput(display_name="Công cụ: tra đơn hàng", field_id="tool_order_lookup", default=False,
              info="Cho agent tra một đơn hàng trong bảng đơn của shop (bảng giả để thử, chỉ đọc)."),
    # [ghi chú] 10 công cụ KPI (backend KPI mekongai, chỉ đọc) — danh sách lấy từ /api/tools lúc chạy
    BoolInput(display_name="Công cụ: KPI (tra bảng thưởng, kết quả NV, giải thích KPI — chỉ đọc)", field_id="tool_kpi", default=False,
              info="10 công cụ đọc của backend KPI (số do code tính). Mã lượt + tổ chức do hệ thống điền; kỳ do backend giữ. Thiếu cấu hình ⇒ khối báo lỗi."),
    # [ghi chú] bản dữ liệu sau khi người dùng tải file lên
    MultilineTextInput(display_name="Bản dữ liệu KPI (hệ thống điền)", field_id="kpi_data_version", default="",
                       info="Mã bản dữ liệu backend trả sau khi tải file lên (hex 1–64 ký tự). Trống: bản lúc bật backend. Hệ thống (trang) điền — "
                            "AI không thấy, không sửa; sai dạng ⇒ khối dừng (không lùi về bản lúc bật)."),
    BoolInput(display_name="Công cụ: hỏi dữ liệu cửa hàng bằng SQL (chỉ đọc)", field_id="tool_sql_query", default=False,
              info="Cho agent viết MỘT câu SELECT trên tồn kho, giá và đơn của KHÁCH ĐANG NHẮN (dữ liệu giả để thử). Chỉ đọc; cần ô mã khách do hệ thống điền, trống thì mọi câu bị từ chối."),
    # vá 10 (06/11): chạy code trong hộp cách ly của mình (thay E2B) — chay_thu/THIET_KE_SANDBOX.md
    BoolInput(display_name="Công cụ: chạy code Python trên file Excel (hộp cách ly, không mạng)", field_id="tool_run_python", default=False,
              info="Cho agent viết code Python để tính trên file Excel của lượt này, chạy trong hộp không mạng (60 giây, 512 MB, tối đa 5 lần / lượt). "
                   "Không bật cùng tra web / đọc trang / công cụ MCP; các khối sau nó chỉ được là khối không gửi ra ngoài."),
    MultilineTextInput(display_name="File Excel cho hộp chạy code (hệ thống điền)", field_id="tool_ma_file", default="",
                       info="Tên file trong thư mục dữ liệu Excel của máy thử (vd don_hang_gia.xlsx). Hệ thống điền — AI không chọn được file."),
    # hướng C (chiều 04/11): AI đưa dữ liệu, code ghi file — thay cho khối Excel / Word của Capitol cần E2B + Claude
    BoolInput(display_name="Công cụ: xuất Excel (code ghi)", field_id="tool_make_xlsx", default=False,
              info="Cho agent ghi file .xlsx có trình bày: tiền VNĐ / %, cố định tiêu đề, lọc, dòng tổng là công thức =SUM (hệ thống tạo + tự kiểm), "
                   "nhiều sheet, biểu đồ cột / tròn vẽ từ ô của bảng, nhãn NHÁP + ghi chú nguồn số. File ở thư mục xuất của máy chạy thử."),
    # [ghi chú] file chữ — code ghi, UTF-8, tên do hệ thống đặt
    BoolInput(display_name="Công cụ: xuất Markdown (code ghi)", field_id="tool_make_md", default=False,
              info="Cho agent ghi tài liệu .md (tiêu đề, đoạn, một bảng có số hiện kiểu Việt Nam, ghi chú)."),
    BoolInput(display_name="Công cụ: xuất văn bản .txt (code ghi)", field_id="tool_make_txt", default=False,
              info="Cho agent ghi văn bản thuần .txt (để dán vào tin nhắn); bảng canh cột bằng khoảng trắng."),
    BoolInput(display_name="Công cụ: xuất CSV (code ghi)", field_id="tool_make_csv", default=False,
              info="Cho agent ghi bảng .csv (UTF-8 có BOM, chống công thức); số để thô."),
    BoolInput(display_name="Công cụ: xuất Word (code ghi)", field_id="tool_make_docx", default=False,
              info="Cho agent ghi tài liệu .docx có trình bày (khối tiêu đề, tên mục / gạch đầu dòng / chữ đậm, bảng có màu, số trang)."),
    BoolInput(display_name="Công cụ: xuất PowerPoint (code ghi)", field_id="tool_make_pptx", default=False,
              info="Cho agent ghi bài trình chiếu .pptx có thiết kế: bộ màu theo chủ đề, 7 kiểu trang (gạch đầu dòng, hai cột, so sánh, số lớn, dòng thời gian, bảng, bìa); code tự canh cỡ chữ / tách trang. Thay khối PowerPoint cần E2B + Claude."),
    BoolInput(display_name="Công cụ: xuất PDF (code ghi)", field_id="tool_make_pdf", default=False,
              info="Cho agent ghi tài liệu .pdf (tiêu đề, đoạn văn, một bảng có màu, số trang); tiếng Việt có dấu hiển thị đúng (nhúng phông)."),
    # hướng D (chiều 04/11): mã khách do HỆ THỐNG điền (trang / kênh chat theo đăng nhập), AI không thấy, không điền
    MultilineTextInput(display_name="Mã khách đang nhắn (hệ thống điền)", field_id="tool_customer_id", default="",
                       info="Để trống: tra đơn KHÔNG kiểm chủ đơn (chỉ để thử). Có mã: công cụ tra đơn chỉ trả đơn của khách này. "
                            "Hệ thống điền theo người đang đăng nhập / nhắn — không để AI điền."),
    MultilineTextInput(display_name="Khuôn đầu ra (JSON)", field_id="output_schema", default="",
                       info=("Để trống: agent trả chữ như cũ. Có khuôn: agent BẮT BUỘC trả đúng các trường khai báo, kết quả là JSON. "
                             'Ví dụ: {"loai": ["doi_hang", "hoi_gia"], "tom_tat": "string", "ma_don": "string"}')),
])

# Shared primary-output designation (ENG-3827): consumed from the version
# payload by the A2A output gate, not by the node at runtime.
PARAMS.root.extend([PrimaryOutputInput(), PrimaryOutputExtensionsInput()])
