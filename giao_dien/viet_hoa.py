"""Bản dịch tiếng Việt cho danh mục khối Capitol (tên khối, mô tả, tên ô, giải thích, tên cổng ra) — người dùng yêu cầu 04/11:
"chuyển sang tiếng việt, chỉ giữ các thuật ngữ chuyên ngành tiếng anh cần thiết, đừng có vừa anh vừa việt".

A dịch tay từ chữ gốc trong danh mục thật (runtime/b4_list_nodes.json + danh mục trực tiếp 04/11), giữ nghĩa theo code khi đã kiểm.
Chữ gốc tiếng Anh vẫn gửi kèm (label_goc / info_goc) để trang hiện khi rê chuột — truy được về nguồn.
Giữ tiếng Anh: tên sản phẩm (Word, PowerPoint, PDF, Markdown, D3, Vercel, Composio, MCP), thuật ngữ đã quen (prompt, token, sandbox, URL).
"""

KHOI = {
    "text_input_node": ("Ô nhập chữ", "Gõ đoạn chữ để các khối phía sau xử lý."),
    "file_input_node": ("Nhận file", "Tải lên một file rồi đưa cho các khối phía sau (csv, doc, docx, json, pdf, ảnh, âm thanh…). Cần kho file — chưa thử trên máy này."),
    "collections_node": ("Dữ liệu có cấu trúc", "Truy vấn các bảng dữ liệu đã nạp vào nền tảng (bằng câu hỏi thường)."),
    "unstructured_data_search_node": ("Tìm trong tài liệu", "Tìm kiếm trong kho tài liệu (văn bản, PDF…) đã nạp — đây là đường RAG của Capitol."),
    "agent_node": ("Agent AI", "Một AI tự suy luận, ra quyết định hoặc làm việc theo lời dặn; có thể dùng công cụ nếu được nối."),
    "flexible_code_interpreter_node": ("Chạy code (AI)", "AI viết và chạy code trong máy ảo tạm: Python, JavaScript, shell; cài gói, đọc / ghi file, gọi công cụ MCP. Cần máy ảo E2B."),
    "skill_code_interpreter_node": ("Chạy code theo kỹ năng", "Như “Chạy code (AI)” nhưng nạp sẵn một bộ hướng dẫn chuyên môn (SKILL.md + file kèm) vào máy ảo rồi làm theo. Cần máy ảo E2B."),
    "data_analysis_agent_node": ("Agent phân tích dữ liệu", "AI phân tích dữ liệu trong file (XLSX, CSV, JSON, TXT, PDF…) bằng cách chạy code trong máy ảo; xuất được nhiều dạng. Cần máy ảo E2B."),
    "agentic_deep_research_node": ("Nghiên cứu sâu (dùng công cụ)", "Nghiên cứu nhiều vòng bằng các công cụ tra cứu được nối: chia đề thành câu hỏi nhỏ, gom bằng chứng, tìm chỗ thiếu rồi tổng hợp."),
    "vercel_deploy_agent_node": ("Đăng trang web lên Vercel", "Nhận chữ / code / file từ khối trước, dựng thành trang web và đăng lên Vercel. Cần tài khoản Vercel."),
    "deep_research_node": ("Nghiên cứu web sâu", "Tra cứu và phân tích sâu trên web cho một câu hỏi, theo mức tìm kiếm đã chọn. Cần dịch vụ tìm web bên ngoài."),
    "human_intervention_node": ("Người duyệt", "Dừng workflow để người xem và bấm Duyệt / Từ chối. AI viết sẵn bản tóm tắt cho người duyệt (tốn một lần gọi AI)."),
    "evals_node": ("Chấm điểm nội dung", "Chấm chất lượng, độ đúng, mức tuân thủ của nội dung bằng các bài chấm có sẵn."),
    "guardrails_node": ("Rào chắn nội dung", "Chấm nội dung theo các luật rào chắn đã chọn (0–100). Chỉ chấm, KHÔNG tự chặn — khối sau phải tự đọc kết quả."),
    "pdf_redaction_node": ("Che thông tin trong PDF", "Xoá hoặc che thông tin nhạy cảm trong PDF theo luật / yêu cầu riêng."),
    "text_redaction_node": ("Che thông tin trong chữ", "Xoá hoặc che thông tin nhạy cảm trong đoạn chữ theo luật / yêu cầu riêng (AI liệt kê chỗ cần che, code thay)."),
    "text_output_node": ("Kết quả (chữ thường)", "Hiện đoạn chữ lên màn hình kết quả."),
    "d3_data_visualization_node": ("Biểu đồ", "Vẽ biểu đồ từ dữ liệu."),
    "file_browser_output_node": ("Xem file", "Hiện và duyệt các file từ khối được nối."),
    "markdown_output_node": ("Kết quả (chữ có định dạng)", "Hiện kết quả lên màn hình, giữ chữ đậm, danh sách, bảng."),   # [ghi chú kỹ thuật] nhãn lời thường; tên loại khối bên trong không đổi
    "network_graph_output_node": ("Sơ đồ mạng lưới", "Vẽ sơ đồ các điểm và đường nối giữa chúng."),
    "pptx_chat_node": ("Tạo PowerPoint", "AI tạo bài trình chiếu PowerPoint."),
    "xlsx_chat_node": ("Tạo bảng tính Excel", "AI tạo bảng tính (xlsx)."),
    "text_to_speech_output_node": ("Đọc thành tiếng", "Chuyển chữ thành giọng nói và phát trên màn hình kết quả. Cần dịch vụ giọng nói bên ngoài."),
    "docx_chat_node": ("Tạo tài liệu Word", "AI tạo tài liệu Word (docx)."),
    "EXECUTE_COMPOSIO_TOOL": ("Công cụ Composio", "Cho agent dùng các công cụ của Composio (Slack, Gmail…), lọc theo tên ứng dụng. Đang chặn có chủ ý trên máy này."),
}

O = {
    "ai_disclaimer": ("Đánh dấu “do AI tạo”", "Đóng dòng ghi chú “nội dung do AI tạo” lên file kết quả (đầu trang đầu của Word, …)."),
    "ai_disclaimer_text": ("Chữ ghi chú “do AI tạo”", "Câu được đóng lên file khi bật “Đánh dấu do AI tạo”. Để trống thì dùng câu mặc định."),
    "auto_approve_on_timeout": ("Tự duyệt khi hết hạn", "Nếu hết hạn mà không ai trả lời thì cho workflow chạy tiếp và ghi rõ là MÁY tự duyệt. Tắt thì hết hạn là lượt LỖI. Ở workflow KPI: tự duyệt không ghi “đã duyệt” vào hệ thống tính thưởng, file ra là bản nháp mang nhãn tự duyệt."),
    "collections": ("Kho tài liệu", "Các kho tài liệu để lấy nội dung (xem / tạo / sửa ở phần dữ liệu của nền tảng)."),
    "composio_apps": ("Ứng dụng Composio", "Danh sách ứng dụng / hành động Composio cho agent thấy (vd ['slack'] chỉ hiện công cụ SLACK_*)."),
    "custom_instructions": ("Luật che riêng", "Thêm luật hoặc cụm từ cần che / xoá."),
    "data": ("Dữ liệu cần che", "Chữ / JSON (khối che chữ) hoặc file PDF (khối che PDF) cần xử lý theo luật. Sửa 04/11: trước ghi \"File PDF\" cho cả khối che chữ."),
    "datasets": ("Bảng dữ liệu", "Các bảng dữ liệu có cấu trúc để lấy nội dung (xem / tạo / sửa ở phần dữ liệu của nền tảng)."),
    "deep_research_query": ("Câu hỏi nghiên cứu", "Câu hỏi để tra cứu sâu trên web."),
    "deploy_timeout": ("Hạn chờ đăng web (giây)", "Số giây tối đa chờ Vercel báo trang đã sẵn sàng."),
    "enable_interactive_feedback": ("Hỏi góp ý sau khi ra file", "Bật thì khối dừng lại sau khi tạo file để hỏi anh có muốn sửa không; góp ý được đưa lại cho AI làm lại. Mặc định tắt: khối làm xong là đi tiếp."),
    "evals_list": ("Chọn bài chấm", "Chọn một hoặc nhiều bài chấm để chạy trên nội dung."),
    "file_data": ("Tải file lên", "Tải một file lên để đưa cho các khối sau (nối vào ô “File” của khối đó)."),
    "files": ("File", "File đưa vào cho khối xử lý."),
    "ground_with_provided_info": ("Chế độ không đoán", "Bật thì AI chỉ được khẳng định điều có trong thông tin được đưa (lời dặn, việc cần làm, file, kết quả công cụ, lượt trước) và phải nói rõ khi không có căn cứ, thay vì đoán. Vẫn được dùng kiến thức làm việc chung (viết code, định dạng) và gọi công cụ. Nên bật cho việc cần đúng sự thật; tắt cho việc sáng tạo."),
    "guardrail_query": ("Nội dung cần kiểm", "Nội dung đem chấm theo các rào chắn đã chọn."),
    "guardrails_list": ("Chọn rào chắn", "Chọn các luật rào chắn để chấm."),
    "include_citations": ("Kèm nguồn trích dẫn", "Có hiện nguồn trích dẫn trong phần xem trước kết quả không (phần này không chuyển cho khối sau)."),
    "language": ("Ngôn ngữ mặc định", "Ngôn ngữ chạy mặc định cho các đoạn code."),
    "markdown_content": ("Nội dung kết quả", "Nội dung cần hiện (nhận từ khối trước)."),
    "max_reflection_loops": ("Số vòng tự soát tối đa", "Số vòng “tra cứu ⇒ tự soát chỗ thiếu” trước khi tổng hợp (1 = một lượt tra, không soát chỗ thiếu)."),
    "mcp_servers": ("Máy chủ công cụ bên ngoài (MCP)", "Các máy chủ công cụ MCP từ xa cho agent dùng."),
    "model": ("Mô hình AI", "Chọn mô hình AI cho khối. Với các loại khối AI chính, hệ thống chạy theo file mô hình khối (mặc định Claude Sonnet 5.5 qua Amazon Bedrock) và ô này không có tác dụng — xem dòng “Đang chạy thật” ngay dưới ô."),
    "output_spec_description": ("Mô tả đầu ra mong muốn", "Mô tả đầu ra: muốn có những gì, trình bày ra sao."),
    "persist_workspace": ("Giữ file đầu ra", "Đưa file tạo trong máy ảo lên kho file cho các khối sau dùng."),
    "preview_context": ("Nội dung cho người duyệt xem", "Thứ người duyệt nhìn thấy. AI viết thêm bản tóm tắt; khối sau vẫn nhận dữ liệu gốc."),
    "primary_output": ("Là kết quả chính", "Bật (mặc định) thì file khối này tạo ra được coi là kết quả của workflow: hiện ở phần kết quả. Tắt cho khối chỉ tạo file trung gian — vẫn tải được khi cần."),
    "primary_output_extensions": ("Chỉ coi các đuôi file này là kết quả chính", "Không bắt buộc, cách nhau bằng dấu phẩy: chỉ file có đuôi này (vd “docx, pdf”) hoặc khớp mẫu tên (vd “report*.pdf”) mới là kết quả chính. Để trống = mọi file. Bỏ qua khi tắt “Là kết quả chính”."),
    "processor_type": ("Mức tìm kiếm", "Chọn mức tra cứu web: mức cao tìm sâu hơn, chậm và tốn hơn."),
    "project_name": ("Tên dự án Vercel", "Mã dự án trên Vercel (để trống thì tự sinh)."),
    "public_access": ("Cho xem công khai", "Tắt đăng nhập Vercel để ai có link cũng xem được."),
    "query_text": ("Câu truy vấn", "Câu hỏi / truy vấn để lấy dữ liệu từ bảng dữ liệu có cấu trúc."),
    "redaction_name": ("Tên lần che", "Tên của lần che thông tin."),
    "redaction_type": ("Kiểu che", "Kiểu che thông tin cần làm."),
    "request": ("Việc cần làm", "Yêu cầu ban đầu để AI tạo bài trình chiếu."),
    "sandbox_timeout": ("Hạn sống của máy ảo (giây)", "Thời gian tối đa máy ảo còn chạy (5–3600 giây)."),
    "score_threshold": ("Ngưỡng điểm liên quan", "Chỉ lấy kết quả có điểm liên quan trên ngưỡng này."),
    "similarity_boost": ("Độ giống giọng gốc", "Mức bám sát giọng gốc (0,0–1,0)."),
    "skill_id": ("Kỹ năng", "Chọn bộ kỹ năng nạp vào máy ảo; hướng dẫn SKILL.md được thêm vào lời dặn."),
    "speed": ("Tốc độ đọc", "Hệ số tốc độ đọc (0,7–1,2)."),
    "stability": ("Độ ổn định giọng", "Mức đều giọng (0,0–1,0)."),
    "system_prompt": ("Lời dặn hệ thống (system prompt)", "Lời dặn nền định hướng AI suy nghĩ và trả lời: vai trò, chuyên môn, cách cư xử."),
    "task": ("Việc cần làm", "Việc phân tích cần làm trên dữ liệu."),
    "test_contents": ("Nội dung đem chấm", "Đoạn chữ / dữ liệu cần chấm chất lượng, độ đúng, mức tuân thủ."),
    "test_name": ("Tên bài chấm", "Tên bài chấm sẽ tạo."),
    "text_content": ("Nội dung", "Đoạn chữ sẽ hiện ra."),
    "text_input": ("Chữ nhập", "Đoạn chữ đưa vào workflow."),
    "timeout": ("Thời hạn (giây)", "Số giây tối đa chờ người trả lời (tối đa 1 giờ). Để trống = 300 giây."),
    "top_k": ("Số kết quả mỗi kho", "Số kết quả lấy về cho mỗi kho tài liệu."),
    "transcript": ("Chữ cần đọc", "Đoạn chữ cần chuyển thành giọng nói."),
    "use_speaker_boost": ("Làm rõ giọng", "Tăng độ rõ, giảm tạp âm của giọng."),
    "user_prompt": ("Việc cần làm", "Mô tả rõ việc AI phải làm ở bước này và kết quả mong muốn."),
    "voice": ("Giọng đọc", "Giọng dùng để tạo âm thanh."),
    # quy tắc "ai đọc đầu ra" — người dùng hai bên chốt 04/11 (không bật khuôn cho mọi thứ)
    "output_schema": ("Khuôn đầu ra (dạng bảng trường – giá trị)", "Dùng khi CODE hoặc khối sau đọc đầu ra (rẽ nhánh, tính, lưu) ⇒ bắt buộc khuôn. Người đọc nhưng code cần kiểm ⇒ khuôn lai: "
                      "một trường chữ cho người + các trường cho code. Chỉ người đọc (thư, bài viết, chat) ⇒ để TRỐNG: giữ chữ chạy dần. "
                      "Trường không chắc có ⇒ cho phép null, đừng bắt buộc (bắt buộc dễ khiến AI bịa). Khuôn chỉ ép hình dạng, không ép đúng — "
                      'đúng sai do bộ đối chiếu kiểm. Ví dụ: {"loai": ["doi_hang", "hoi_gia"], "ma_don": {"type": ["string", "null"]}, "tom_tat": "string"}'),
    "wait_for_ready": ("Chờ trang lên hẳn", "Hỏi Vercel tới khi trang đã chạy rồi mới trả link."),
}

# Chữ gợi ý trong ô (placeholder). Ô không có ở đây ⇒ bỏ chữ gợi ý gốc tiếng Anh (người dùng: không trộn Anh – Việt).
GOI_Y = {
    "user_prompt": "Để trống thì AI nhận đúng nội dung từ dây. Gõ thêm ở đây thì chữ này đứng TRƯỚC phần nhận qua dây.",
    "text_content": "Để trống thì hiện đúng nội dung nhận qua dây.",
    "preview_context": "Để trống thì người duyệt xem đúng nội dung nhận qua dây.",
    "primary_output_extensions": "vd: docx, pdf — để trống = mọi file",
    "system_prompt": "vd: Bạn là nhân viên chăm sóc khách của shop…",
    "output_schema": '{"loai": ["doi_hang", "hoi_gia"], "tom_tat": "string"}',
    "text_input": "Gõ đoạn chữ đưa vào workflow…",
}

CONG_RA = {
    "analysis_output": "Kết quả phân tích", "audio_output": "Âm thanh", "deep_research_results": "Kết quả nghiên cứu web",
    "docx_output": "File Word", "files": "File", "files_output": "File", "graph_url": "Link sơ đồ", "guardrails_output": "Kết quả rào chắn",
    "markdown_output": "Chữ có định dạng", "output": "Kết quả", "pptx_output": "File PowerPoint", "redaction_result": "Kết quả đã che", "results": "Kết quả",
    "text": "Chữ", "text_output": "Chữ", "tool": "Công cụ", "visualization_url": "Link biểu đồ", "xlsx_output": "File Excel",
}


def viet_hoa_khoi(b: dict) -> dict:
    """Thay tên / mô tả / ô / cổng bằng tiếng Việt; giữ bản gốc ở *_goc. Chưa có bản dịch ⇒ giữ gốc (không bịa)."""
    if b["type"] in KHOI:
        b["label_goc"], b["description_goc"] = b["label"], b["description"]
        b["label"], b["description"] = KHOI[b["type"]]
    for p in b["params"]:
        if p["name"] in O:
            p["label_goc"], p["info_goc"] = p["label"], p["info"]
            p["label"], p["info"] = O[p["name"]]
        p["placeholder"] = GOI_Y.get(p["name"], "")
    for i in b["inputs"]:
        if i["name"] in O:
            i["label_goc"], i["label"] = i["label"], O[i["name"]][0]
    for o in b["outputs"]:
        if o["name"] in CONG_RA:
            o["label_goc"], o["label"] = o["label"], CONG_RA[o["name"]]
    return b
