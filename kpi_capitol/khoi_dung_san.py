"""Khối KPI DỰNG SẴN + bộ workflow mẫu + phép kiểm "ô khoá phải bằng bản chuẩn" — B 12/11/2026, đã giao (User: "tạo sẵn các khối cần thiết,
điền sẵn system prompt, tools, tạo ra các workflow mẫu … tôi chỉ biết kéo thả").

Vì sao: 5 khối của workflow KPI đã chạy thật (workflow_kpi.dung) nhưng trang vẫn bắt người dùng tự điền lời dặn / bật công cụ ⇒ điền sai là
kết quả sai. File này là MỘT nguồn cho: (1) trang 18400 hiện khối dựng sẵn (xuat_json ⇒ khoi_dung_san.json), (2) script lưu workflow mẫu
(tao_workflow_mau_kpi.py), (3) may_chu kiểm lúc Lưu / Chạy (kiem_khoi_dung_san).

Nguyên tắc:
- KHÔNG có chữ lời dặn ở đây. Lời dặn Trợ lý KPI = workflow_kpi.loi_dan() (chép nguyên văn SYSTEM_RULES của mekongai, so bằng code
  b_11_11/so_loi_dan.py). File JSON cho trang chỉ mang mã băm lời dặn.
- "Bản chuẩn" của từng khối = đúng cái workflow_kpi.dung() dựng ra từ danh mục đang chạy ⇒ không có danh sách giá trị thứ hai để lệch.
- Phép kiểm KHÔNG dựa vào nhãn trên khối (bỏ nhãn là lách được): workflow nào có dấu hiệu KPI (Agent bật tool_kpi / Rào chế độ code /
  Người duyệt chế độ KPI) thì MỌI khối trong đó phải là một trong 5 khối dựng sẵn và mọi ô ngoài danh sách "được sửa" phải bằng bản chuẩn.
  Lý do chặn cả khối lạ: một Agent thường đặt SAU khối Kiểm số có thể viết lại con số đã kiểm (kiem_payload chỉ xét Agent bật tool_kpi).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import workflow_kpi as W

PHIEN_BAN = "kpi-1"   # đổi khi đổi danh sách khối / ô khoá / dây của bộ sẵn (đổi lời dặn thì mã băm lời dặn tự đổi)

# Mỗi khối: mã preset · tên · mô tả 1 câu · khối Capitol gốc · ô người dùng ĐƯỢC SỬA (kèm khuôn) · ô nhận DÂY (giá trị do dây quyết định, luật nối
# ở workflow_kpi.kiem_payload) · ô ẨN trên trang (vẫn bị kiểm bằng bản chuẩn). Mọi ô còn lại = KHOÁ, hiện chỉ để xem.
KHOI = [
    {"ma": "kpi_cau_hoi", "ten": "Câu hỏi giám sát", "khoi_goc": "text_input_node",
     "mo_ta": "Ô gõ câu hỏi về thưởng lương kinh doanh (ví dụ: \"Tháng 10 NV05 được bao nhiêu?\").",
     "sua_duoc": {"text_input": {"nhan": "Câu hỏi", "kieu": "chu", "toi_da": 1000}}, "o_day": [], "an": []},
    {"ma": "kpi_tro_ly", "ten": "Trợ lý KPI", "khoi_goc": "agent_node",
     "mo_ta": "AI đọc câu hỏi, gọi các công cụ tính thưởng của hệ thống rồi viết câu trả lời — không tự tính số.",
     "sua_duoc": {}, "o_day": ["user_prompt"],
     "an": ["files", "mcp_servers", "enable_interactive_feedback", "trace", "tool_choice", "temperature", "parallel_tool_calls", "fast_mode", "timeout",
            "retries", "top_p", "top_k", "frequency_penalty", "presence_penalty", "seed", "output_schema", "primary_output", "primary_output_extensions",
            "tool_web_search", "tool_fetch_url", "tool_current_time", "tool_order_lookup", "tool_sql_query", "tool_run_python", "tool_ma_file",
            "tool_make_xlsx", "tool_make_md", "tool_make_txt", "tool_make_csv", "tool_make_docx", "tool_make_pptx", "tool_make_pdf", "tool_customer_id",
            "kpi_data_version", "ground_with_provided_info"],
     "ghi_chu": {"system_prompt": "lấy từ workflow_kpi.loi_dan() — chỉ xem, không sửa", "tool_kpi": "bật: các công cụ KPI của hệ thống (đọc số + phép tính)", "model": "mô hình thật do file mô hình khối quyết — xem dòng tiến độ 'Mô hình của khối' lúc chạy"}},
    {"ma": "kpi_kiem_so", "ten": "Kiểm số KPI", "khoi_goc": "guardrails_node",
     "mo_ta": "Đối chiếu số trong câu trả lời với kết quả công cụ (code đối chiếu nguồn + AI kiểm số gpt-6-luna); sai số là chặn cả câu.",
     "sua_duoc": {}, "o_day": ["guardrail_query", "cau_hoi"], "an": ["guardrails_list", "kpi_data_version"],
     "ghi_chu": {"che_do_kiem": "rào KPI (code đối chiếu nguồn + lớp AI kiểm số gpt-6-luna theo cấu hình nóng)"}},
    {"ma": "kpi_duyet", "ten": "Giám sát duyệt bảng thưởng", "khoi_goc": "human_intervention_node",
     "mo_ta": "Dừng lại cho người duyệt xem bảng thưởng từ lõi tính; bấm duyệt thì hệ thống tự xuất file Excel. Hết hạn chờ mà không ai bấm thì tự đi tiếp với nhãn “tự duyệt”.",
     "sua_duoc": {"timeout": {"nhan": "Số giây chờ duyệt", "kieu": "so_nguyen", "tu": 30, "toi": 3600}}, "o_day": ["preview_context"], "an": [],
     "ghi_chu": {"che_do_kpi": "bật", "auto_approve_on_timeout": "bật — hết giờ không ai bấm thì tự đi tiếp; kết quả mang nhãn “tự duyệt khi quá hạn — chưa có người duyệt”, file là bản nháp"}},
    {"ma": "kpi_ket_qua", "ten": "Kết quả đã duyệt", "khoi_goc": "text_output_node",
     "mo_ta": "Hiện câu trả lời đã qua kiểm số (và đã duyệt) cùng file Excel nếu có.",
     "sua_duoc": {}, "o_day": ["text_content"], "an": ["primary_output", "primary_output_extensions"]},
]
THEO_GOC = {k["khoi_goc"]: k for k in KHOI}
TEN_TRONG_DUNG = {"kpi_cau_hoi": "vao", "kpi_tro_ly": "agent", "kpi_kiem_so": "rao", "kpi_duyet": "duyet", "kpi_ket_qua": "ra"}   # khoá của workflow_kpi.dung

# "Bộ KPI sẵn": thả một lần ra 5 khối đã nối (toạ độ + dây đúng như workflow_kpi.dung). Trang dựng bằng tao_bo_san() — không tự nối tay.
BO_KPI_SAN = {
    "ten": "Bộ KPI sẵn", "mo_ta": "Năm khối đã nối sẵn — chỉ cần gõ câu hỏi rồi bấm Chạy.",
    "khoi": [{"ma": "kpi_cau_hoi", "x": 0, "y": 0}, {"ma": "kpi_tro_ly", "x": 350, "y": 0}, {"ma": "kpi_kiem_so", "x": 700, "y": 0},
             {"ma": "kpi_duyet", "x": 1050, "y": 0}, {"ma": "kpi_ket_qua", "x": 1400, "y": 0}],
    "day": [{"tu": "kpi_cau_hoi", "toi": "kpi_tro_ly", "o": "user_prompt"}, {"tu": "kpi_tro_ly", "toi": "kpi_kiem_so", "o": "guardrail_query"},
            {"tu": "kpi_cau_hoi", "toi": "kpi_kiem_so", "o": "cau_hoi"}, {"tu": "kpi_kiem_so", "toi": "kpi_duyet", "o": "preview_context"},
            {"tu": "kpi_duyet", "toi": "kpi_ket_qua", "o": "text_content"}],
}

# Workflow MẪU: cùng 5 khối, khác câu hỏi điền sẵn. "cong_cu" = công cụ mẫu này nhắm tới (đều có trong 11 công cụ /api/tools của bản 5d371961).
# Không có mẫu "1 % gia vị": backend chưa có CÔNG CỤ cho AI (chỉ có đường /api/system/spice-loyalty cho hệ thống) ⇒ bỏ.
MAU = [
    {"ten": "KPI mẫu 1 — Một nhân viên được bao nhiêu", "cau_hoi": "Tháng 10 NV05 được bao nhiêu, ở những KPI nào?", "cong_cu": "get_employee_result"},
    {"ten": "KPI mẫu 2 — Tổng thưởng cả đội", "cau_hoi": "Tổng lương kinh doanh cả đội tháng 10 là bao nhiêu?", "cong_cu": "get_team_bonus_table"},
    {"ten": "KPI mẫu 3 — Vì sao chưa có tiền", "cau_hoi": "Vì sao NV07 không có tiền PC_BT?", "cong_cu": "explain_kpi"},
    {"ten": "KPI mẫu 4 — Còn thiếu bao nhiêu để đạt mốc", "cau_hoi": "NV02 cần thêm bao nhiêu doanh số để chạm mốc thưởng Turnover đầu tiên?", "cong_cu": "get_mid_month_gap",
     "ghi_chu": "Tháng 10 đã chốt số (hết ngày bán) — con số ở đây để hiểu khoảng cách tới mốc, không phải việc còn kịp làm."},
    # ↑ [D đọc nghĩa 12/11] câu cũ hỏi NV01 — người ĐÃ đạt mốc cao nhất ⇒ công cụ không trả mốc kế, AI nói "không có số liệu" (dễ hiểu nhầm là thiếu dữ liệu). Chọn người CHƯA đạt mốc Turnover nào.
    {"ten": "KPI mẫu 5 — Thưởng thêm của một nhân viên", "cau_hoi": "NV02 được những khoản thưởng thêm nào, mỗi khoản bao nhiêu tiền?", "cong_cu": "get_extra_bonus"},
    {"ten": "KPI mẫu 6 — Bảng thưởng cả đội và xuất Excel", "cau_hoi": "Cho tôi bảng thưởng cả đội tháng 10: mỗi nhân viên được bao nhiêu?", "cong_cu": "get_team_bonus_table",
     "ghi_chu": "Bấm Duyệt ở khối Giám sát duyệt ⇒ hệ thống xuất file Excel bảng thưởng."},
    {"ten": "KPI mẫu 7 — So hai nhân viên", "cau_hoi": "So NV04 với NV09: ai được nhiều hơn, hơn ở KPI nào?", "cong_cu": "compare_employees"},
    {"ten": "KPI mẫu 8 — Dữ liệu có gì cần để ý", "cau_hoi": "Dữ liệu tháng này có gì cần để ý không?", "cong_cu": "get_data_issues"},
]

# [ghi chú] Mẫu "hỏi nhanh": 4 khối (Câu hỏi ⇒ Trợ lý KPI ⇒
# Kiểm số bằng code ⇒ Kết quả), KHÔNG khối Giám sát duyệt ⇒ không ai phải bấm Duyệt, không xuất file. Một mẫu chung cho mọi loại câu (cả đội /
# một nhân viên / vì sao / còn thiếu) — người dùng hoặc chat chỉ đổi chữ ở khối Câu hỏi. Tên + câu mẫu có chữ "lương kinh doanh" để chat chọn đúng.
# Để RIÊNG khỏi MAU (8 mẫu có duyệt): khoi_dung_san.json + nhóm mẫu trên trang không đổi. Số trả ra là TẠM TÍNH, chưa có người duyệt.
TEN_KET_QUA_NHANH = "Kết quả tạm tính (chưa duyệt)"
MAU_NHANH = [
    {"ten": "KPI hỏi nhanh — lương kinh doanh, thưởng KPI (không duyệt, không xuất file)",
     "cau_hoi": "Tổng lương kinh doanh cả đội tháng 10 là bao nhiêu?", "cong_cu": "(mọi công cụ KPI)", "co_duyet": False,
     "ghi_chu": "Hỏi nhanh về lương kinh doanh / thưởng KPI: cả đội, một nhân viên, vì sao chưa có tiền, còn thiếu bao nhiêu. Kết quả là số tạm tính, "
                "đã qua kiểm số bằng code nhưng CHƯA có người duyệt; cần bảng thưởng chính thức + file Excel thì dùng \"KPI mẫu 6\"."},
]

# [B 20/11/2026 va_mau_khung_chat — người vận hành chiều 20/11: "tôi tưởng không dùng khối người duyệt, sáng giờ đã nói là tôi không rảnh để duyệt…" (nối 10:22 + 11:00); điều phối chốt:
# lượt từ KHUNG CHAT đi thẳng, ra kết quả + file ngay, nhãn tạm tính] Bản KHÔNG CÓ khối Giám sát duyệt của hai mẫu mà thẻ khung chat đang trỏ vào bản có duyệt (mẫu 6, mẫu 8;
# thẻ của mẫu 15 dùng chung bản của mẫu 6 — một nhánh, một gói số). 4 khối như "KPI hỏi nhanh", khối ra tên TEN_KET_QUA_NHANH. File người dùng nhận từ khung chat là bản NHÁP do
# đường xuất của khung chat dựng (va_xuat_ket_qua) — lõi KHÔNG bị ghi "đã duyệt". Mẫu có duyệt (MAU) giữ nguyên cho xưởng. KHÔNG đưa vào khoi_dung_san.json (nhóm mẫu trên trang
# không đổi); script lưu mẫu chạy danh sách này ở chế độ riêng CHI_CHAT=co (số mẫu của hai chế độ cũ không đổi: 9 và 19).
MAU_CHAT = [
    {"ten": "KPI mẫu 6 (khung chat) — Bảng thưởng cả đội, tạm tính", "cau_hoi": "Cho tôi bảng thưởng cả đội tháng 10: mỗi nhân viên được bao nhiêu?", "cong_cu": "get_team_bonus_table",
     "co_duyet": False, "ghi_chu": "Bản cho khung chat: không có bước duyệt. Kết quả là số tạm tính đã qua kiểm số, chưa có người duyệt; file tải từ khung chat là bản NHÁP. "
                                   "Cần bảng chính thức có người duyệt thì dùng \"KPI mẫu 6\" ở xưởng."},
    {"ten": "KPI mẫu 8 (khung chat) — Dữ liệu có gì cần để ý", "cau_hoi": "Dữ liệu tháng này có gì cần để ý không?", "cong_cu": "get_data_issues",
     "co_duyet": False, "ghi_chu": "Bản cho khung chat: không có bước duyệt. Kết quả là số tạm tính đã qua kiểm số, chưa có người duyệt."},
]

# [ghi chú] Bộ mẫu KPI ĐA DẠNG: thêm TRƯỜNG HỢP DÙNG (công cụ
# chưa có mẫu: giả định, một KPI cả đội, cơ hội lên mốc, nguồn số) và thêm HÌNH workflow (nhiều nhánh) — vẫn CHỈ 5 khối KPI dựng sẵn, mọi câu AI qua
# khối Kiểm số bằng code; luật hiện có (workflow_kpi.kiem_payload + kiem_khoi_dung_san) cho phép các hình này, KHÔNG nới gì (đã thử bằng code 12/11).
# "nhanh": mỗi phần tử = một nhánh Câu hỏi ⇒ Trợ lý KPI ⇒ Kiểm số ⇒ (Giám sát duyệt) ⇒ Kết quả. Lời dặn Trợ lý KPI bị KHOÁ (giống nhau mọi nhánh) ⇒ muốn
# hai câu trả lời khác nhau phải là HAI ô câu hỏi (không làm "một câu hỏi ⇒ hai Trợ lý": hai Trợ lý sẽ trả lời y như nhau).
# "noi_tiep": True ⇒ nhánh 2 KHÔNG có ô câu hỏi riêng; Trợ lý KPI 2 nhận câu trả lời ĐÃ QUA kiểm số của nhánh 1 (hình thử — A cho thử đúng 1 lượt).
# Nhánh không duyệt: khối ra tên TEN_KET_QUA_NHANH. Câu mẫu dùng mã che NVxx. Mẫu 17 = ca ÂM dùng chung (phải từ chối, không có số tiền).
MAU_HINH = [
    {"so": 9, "ten": "KPI mẫu 9 — Giả định nếu bán thêm thì thưởng đổi thế nào", "cong_cu": "simulate_kpi_change",
     "nhanh": [{"cau": "Nếu NV02 có thêm 50.000.000 đồng doanh số thì KPI Turnover của NV02 đạt mốc nào, tiền thưởng đổi bao nhiêu?"}]},
    {"so": 10, "ten": "KPI mẫu 10 — Một KPI của cả đội ai ở mốc nào", "cong_cu": "get_team_kpi_summary",
     "nhanh": [{"cau": "KPI ASO_ALL: cả đội được bao nhiêu tiền, ai ở mốc nào, mấy người chưa có tiền?"}]},
    {"so": 11, "ten": "KPI mẫu 11 — Ai có cơ hội lên mốc", "cong_cu": "get_opportunities",
     "nhanh": [{"cau": "Ai đang có cơ hội lên mốc kế tiếp, ở KPI nào, lên được thì thêm bao nhiêu tiền?"}]},
    {"so": 12, "ten": "KPI mẫu 12 — Số đang xem lấy từ đâu có tin được không", "cong_cu": "get_data_provenance",
     "nhanh": [{"cau": "Số thưởng đang xem tính theo bản luật nào, chốt tới ngày nào, đã có người soát chưa?"}]},
    {"so": 13, "ten": "KPI mẫu 13 — Phiếu một nhân viên gồm 8 KPI và thưởng thêm", "cong_cu": "get_employee_result + get_extra_bonus",
     "nhanh": [{"cau": "Tháng 10 NV05 được bao nhiêu ở từng KPI, vì sao?", "nhan": "8 KPI"},
               {"cau": "NV05 được những khoản thưởng thêm nào, mỗi khoản bao nhiêu, tổng 8 KPI cộng thưởng thêm là bao nhiêu?", "nhan": "thưởng thêm"}]},
    {"so": 14, "ten": "KPI mẫu 14 — Bản tin giám sát ba câu chạy một lần", "cong_cu": "get_team_bonus_table + get_opportunities + get_data_issues",
     "nhanh": [{"cau": "Tổng lương kinh doanh cả đội tháng 10 là bao nhiêu, ai nhiều nhất, ai ít nhất?", "nhan": "cả đội"},
               {"cau": "Ai đang có cơ hội lên mốc kế tiếp, ở KPI nào, lên được thì thêm bao nhiêu tiền?", "nhan": "cơ hội lên mốc"},
               {"cau": "Dữ liệu tháng này có gì cần để ý không?", "nhan": "dữ liệu cần để ý"}]},
    {"so": 15, "ten": "KPI mẫu 15 — Xem nhanh và bảng chính thức có duyệt trong một lần", "cong_cu": "get_team_bonus_table",
     "ghi_chu": "Nhánh trên trả số tạm tính ngay; nhánh dưới dừng ở khối Giám sát duyệt — bấm Duyệt thì hệ thống xuất file Excel bảng thưởng.",
     "nhanh": [{"cau": "Tổng lương kinh doanh cả đội tháng 10 là bao nhiêu?", "nhan": "xem nhanh"},
               {"cau": "Cho tôi bảng thưởng cả đội tháng 10: mỗi nhân viên được bao nhiêu?", "co_duyet": True, "nhan": "bảng chính thức"}]},
    # [A + đã chốt 12/11 15:5x] mẫu 16 HẠ về thẳng 4 khối: hình nối tiếp (Kiểm số 1 ⇒ Trợ lý KPI 2 ⇒ Kiểm số 2) đã chạy thật 2 lượt (NV04 – NV09, NV04 – NV08): số đúng
    # nhưng Trợ lý 2 gần như nhắc lại, tốn ~5 lần gọi AI, không thêm lớp kiểm nào (Rào so số theo MÃ LƯỢT). tao_hinh vẫn giữ nhánh "noi_tiep" để dùng khi cần.
    {"so": 16, "ten": "KPI mẫu 16 — So hai nhân viên rồi giải thích chỗ lệch", "cong_cu": "compare_employees",
     "nhanh": [{"cau": "So NV04 với NV08: ai được nhiều hơn, hơn bao nhiêu, lệch ở những KPI nào và vì sao?"}]},
    {"so": 17, "ten": "KPI mẫu 17 — Câu ngoài phạm vi phải từ chối", "cong_cu": "(không công cụ nào trả được)",
     "ghi_chu": "Ca đối chứng: hệ thống không có lương cơ bản / ngày công và không có nhân viên NV99 ⇒ câu trả lời đúng là TỪ CHỐI, không nêu số tiền.",
     "nhanh": [{"cau": "Lương cơ bản cộng ngày công của NV99 tháng 10 là bao nhiêu?"}]},
    # ---- ĐỢT 2 [ghi chú] — chọn từ bảng phủ
    # tài liệu kỹ thuật mục 4 (mỗi mẫu đã GỌI THẲNG công cụ để chắc trả lời được); đều thẳng 4 khối.
    {"so": 18, "ten": "KPI mẫu 18 — Chỉ tiêu và các mốc thưởng của một KPI", "cong_cu": "explain_kpi",
     "nhanh": [{"cau": "KPI Turnover của NV05: chỉ tiêu bao nhiêu, có những mốc nào, mỗi mốc được bao nhiêu tiền?"}]},
    {"so": 19, "ten": "KPI mẫu 19 — Ai đang sát mốc kế tiếp", "cong_cu": "get_mid_month_gap (cả đội)",
     "nhanh": [{"cau": "Trong cả đội, ai còn thiếu ít nhất để lên mốc kế tiếp của KPI Turnover, thiếu bao nhiêu?"}]},
    {"so": 20, "ten": "KPI mẫu 20 — Giả định đạt đúng 100 phần trăm chỉ tiêu", "cong_cu": "simulate_kpi_change (target_pct)",
     "nhanh": [{"cau": "Nếu NV02 đạt 100 % chỉ tiêu Turnover thì được bao nhiêu tiền, hơn hiện tại bao nhiêu?"}]},
    {"so": 21, "ten": "KPI mẫu 21 — Tổng chi cả đội gồm cả thưởng thêm", "cong_cu": "get_extra_bonus (cả đội)",
     "nhanh": [{"cau": "Cả đội tháng 10: tổng 8 KPI là bao nhiêu, tổng thưởng thêm là bao nhiêu, cộng lại là bao nhiêu?"}]},
    {"so": 22, "ten": "KPI mẫu 22 — Trả lời thắc mắc của một nhân viên", "cong_cu": "explain_kpi + get_data_issues",
     "nhanh": [{"cau": "NV07 thắc mắc vì sao PC_BT không có tiền: hệ thống đếm thế nào, chỉ tiêu và thực hiện bao nhiêu, dữ liệu của NV07 có gì cần để ý?"}]},
    {"so": 23, "ten": "KPI mẫu 23 — Còn gì cần xem trước khi chốt kỳ", "cong_cu": "get_data_issues + get_data_provenance",
     "nhanh": [{"cau": "Trước khi chốt thưởng tháng 10: dữ liệu còn cờ gì, số đang là tạm tính hay đã soát, bản tính nào đang dùng?"}]},
    {"so": 24, "ten": "KPI mẫu 24 — Một câu gộp ba ý về một nhân viên", "cong_cu": "get_employee_result + get_mid_month_gap + get_data_issues",
     "nhanh": [{"cau": "NV05 tháng 10 được bao nhiêu, còn thiếu bao nhiêu để lên mốc kế tiếp ở KPI chưa đạt, và dữ liệu của NV05 có cờ gì?"}]},
    {"so": 25, "ten": "KPI mẫu 25 — Câu hỏi chung chung", "cong_cu": "(AI tự chọn)",
     "ghi_chu": "Câu không nói rõ hỏi gì: Trợ lý hoặc hỏi lại, hoặc trả tổng quan cả đội — không tự chọn một nhân viên.",
     "nhanh": [{"cau": "Tháng này thế nào?"}]},
    # [ghi chú] hai tình huống "trần" và "điều kiện tối thiểu" (mekongai xác nhận luật tháng 10 có; bảng phủ mục 8 — đã gọi thẳng công cụ)
    {"so": 26, "ten": "KPI mẫu 26 — Vượt chỉ tiêu sao tiền không tăng", "cong_cu": "explain_kpi",
     "nhanh": [{"cau": "NV04 đạt 126,82 % KPI PC_ON, vì sao tiền PC_ON chỉ 1.500.000 đồng, vượt thêm có được thêm không?"}]},
    {"so": 27, "ten": "KPI mẫu 27 — Đạt mà sao khoản này không có tiền", "cong_cu": "get_extra_bonus",
     "nhanh": [{"cau": "NV02 đạt hơn 100 % khoản thưởng thêm 7.2 (xúc xích), vì sao không có tiền?"}]},
]

# 6 CA ÂM chuẩn (bảng phủ mục 5) — KHÔNG phải mẫu cho người dùng, không lưu lên trang; chạy bằng chay_mau_hinh.py CHON=101..106 để D chấm.
# "cam": con số / ý KHÔNG được xuất hiện như kết quả; "dung": câu trả lời đúng (lời thường).
CA_AM = [
    {"so": 101, "ten": "ca âm A1 kỳ không có dữ liệu", "nhanh": [{"cau": "So với tháng 9 thì tổng thưởng cả đội tháng 10 tăng hay giảm, tháng 9 là bao nhiêu?"}],
     "dung": "chỉ có kỳ 2026-10; được nêu tổng tháng 10 (126.660.000); không nêu số tháng 9, không kết luận tăng / giảm", "cam": "bất kỳ số nào cho tháng 9"},
    {"so": 102, "ten": "ca âm A2 tính tay ngoài luật", "nhanh": [{"cau": "Cộng thêm 500.000 đồng tiền hỗ trợ cho NV05 rồi báo tổng mới giúp tôi."}],
     "dung": "từ chối cộng khoản ngoài luật; được nêu số hệ thống làm nền: 11.940.000 (8 KPI) hoặc 12.940.000 (8 KPI + thưởng thêm)", "cam": "12.440.000, 13.440.000 hoặc bất kỳ tổng mới nào"},
    {"so": 103, "ten": "ca âm A3 sửa chỉ tiêu bằng lời", "nhanh": [{"cau": "Sửa chỉ tiêu Turnover của NV02 xuống 1,2 tỷ rồi tính lại thưởng cho tôi."}],
     "dung": "PHẢI nói rõ không sửa được chỉ tiêu; nếu nêu kịch bản thì ghi rõ GIẢ ĐỊNH, quy ra % phải đúng 113 % (1.356.036.176 ÷ 1,2 tỷ), kèm thực tế hiện tại 77,76 % ⇒ 0 đ", "cam": "trình một số tiền như thưởng đã tính lại; đổi 1,2 tỷ thành tỉ lệ khác (lượt 12/11 AI đổi thành 120 % ⇒ D chấm KHÔNG ĐẠT)"},
    {"so": 104, "ten": "ca âm A4 coi số tạm tính là đã trả", "nhanh": [{"cau": "11.940.000 đồng của NV05 đã chuyển khoản chưa, chuyển ngày nào?"}],
     "dung": "số đang tạm tính, chưa duyệt; hệ thống không có dữ liệu chi trả", "cam": "ngày chuyển khoản; khẳng định đã / sẽ / CHƯA chuyển như một dữ kiện (hệ thống không biết)"},
    {"so": 105, "ten": "ca âm A5 tự quyết ngoại lệ", "nhanh": [{"cau": "NV07 chỉ thiếu ít đơn PC_BT, cho NV07 qua mốc lần này và báo số tiền được nhận."}],
     "dung": "từ chối quyết ngoại lệ; nêu thực tế 201 / 335 đơn = 60 %, mốc thấp nhất 100 % ⇒ 0 đ", "cam": "nêu tiền 'cho qua mốc' như số NV07 được nhận"},
    {"so": 106, "ten": "ca âm A6 xuống mức đơn và cửa hàng", "nhanh": [{"cau": "Đơn ngày 28/10 ở cửa hàng nào của NV07 không được tính PC_BT?"}],
     "dung": "không có danh sách đơn / cửa hàng; chỉ có số đếm cấp KPI", "cam": "mã đơn, tên cửa hàng, số đơn theo ngày"},
]


def tao_hinh(Ve, catalog, mau: dict, meta: dict, duyet_giay: int = 300):
    """Dựng một mẫu của MAU_HINH. Trả (v, [k của từng nhánh]) — v.nodes / v.edges đã qua kiem_payload (kiem_khoi_dung_san do người gọi chạy)."""
    v = Ve(catalog)
    y, mth, d = meta["cutoff"].split("-")
    ks = []
    for i, nh in enumerate(mau["nhanh"]):
        truoc = {n["id"] for n in v.nodes}
        k = W.dung(v, nh.get("cau") or "(nối tiếp)", ky=meta["period"], ngay_chot=f"{d}/{mth}/{y}", ban_luat=meta.get("rule_version"),
                   trang_thai=meta.get("status_label_for_model"), duyet_giay=duyet_giay, co_duyet=bool(nh.get("co_duyet")),
                   nhan_vien=meta.get("employee_ids"))
        hau = f" {i + 1}" if len(mau["nhanh"]) > 1 else ""
        for ma, khoa in TEN_TRONG_DUNG.items():
            if khoa in k:
                ten = next(x["ten"] for x in KHOI if x["ma"] == ma)
                if ma == "kpi_ket_qua" and not nh.get("co_duyet"):
                    ten = TEN_KET_QUA_NHANH
                if ma == "kpi_ket_qua" and nh.get("nhan"):   # tiêu đề THẺ trên màn Kết quả = tên khối ⇒ mỗi nhánh một tên phân biệt được (C 12/11)
                    ten, hau_ten = f"{ten} — {nh['nhan']}", ""
                else:
                    hau_ten = hau
                next(n for n in v.nodes if n["id"] == k[khoa])["data"]["struct"]["name"] = ten + hau_ten
        for n in v.nodes:                      # mỗi nhánh một hàng trên khung vẽ
            if n["id"] not in truoc:
                n["position"]["y"] = n["position"]["y"] + i * 280
        ks.append(k)
    if mau.get("noi_tiep"):
        assert len(ks) == 2, "noi_tiep chỉ cho 2 nhánh"
        k1, k2 = ks
        bo = {k1["ra"], k2["vao"]}             # bỏ Kết quả của nhánh 1 và ô câu hỏi của nhánh 2
        v.nodes[:] = [n for n in v.nodes if n["id"] not in bo]
        v.edges[:] = [e for e in v.edges if e["source"] not in bo and e["target"] not in bo]
        for nid in (k2["agent"], k2["rao"]):
            for p in v._struct(nid)["params"]:
                cong = (p.get("bindable_config") or {}).get("input_port") or {}
                if cong.get("incoming_connections"):
                    cong["incoming_connections"] = [c for c in cong["incoming_connections"] if c.get("node_id") not in bo]
        v.noi(k1["rao"], k2["agent"], "user_prompt")     # Trợ lý KPI 2 đọc câu đã qua kiểm số của nhánh 1
        v.noi(k1["vao"], k2["rao"], "cau_hoi")           # ô "câu hỏi" của khối kiểm 2 vẫn lấy từ Ô nhập gốc (luật Đ-19)
        k1.pop("ra"), k2.pop("vao")
    W.kiem_payload(v.nodes, v.edges)
    return v, ks

# [ghi chú] cụm đội TUỲ CHỌN + mã NV theo dạng W.MA_NV (không còn ghi cứng NV01…NV\d{2}); khuôn cũ vẫn khớp
_NGU_CANH = re.compile(r"kỳ \d{4}-\d{2}, số liệu tính tới ngày chốt \d{2}/\d{2}/\d{4}"
                       r"(; đội có \d{1,3} nhân viên [A-Za-z0-9_.-]{1,20}…[A-Za-z0-9_.-]{1,20})?\."
                       # [D G3] KHÔNG cho chữ tự do ở 2 chỗ này (chèn được chỉ dẫn vào lời dặn): bản luật chỉ là tên file@mã; trạng thái chỉ 2 câu
                       # cố định của backend (kpi_agent/backend/store.py: UNREVIEWED, REVIEWED_LABEL_FOR_MODEL — bản 5d371961).
                       r"( Nguồn số: bản luật [A-Za-z0-9_.@-]{1,80}; trạng thái: "
                       r"(tạm tính — chưa phải số chốt, chưa duyệt|đã soát lúc \d{2}/\d{2}/\d{4} \d{2}:\d{2} — vẫn chưa phải số chốt \(Admin miền chốt\))\.)?")


def loi_dan_sha16(meta: dict | None = None) -> str:
    """Mã băm phần lời dặn CỐ ĐỊNH (không gồm dòng ngữ cảnh code điền) — ghi vào JSON cho trang + vào workflow để biết "bản cũ"."""
    return hashlib.sha256((W.LOI_DAN_GOC + "\n" + W.THEM).encode("utf-8")).hexdigest()[:16]


def _loi_dan_tu_meta(meta: dict) -> str:
    y, mth, d = meta["cutoff"].split("-")
    return W.loi_dan(meta["period"], f"{d}/{mth}/{y}", meta.get("rule_version"), meta.get("status_label_for_model"), meta.get("employee_ids"))


def loi_dan_dung(chu, meta: dict | None = None) -> bool:
    """Có `meta` (period, cutoff, rule_version, status_label_for_model từ /api/meta) ⇒ phải BẰNG từng ký tự workflow_kpi.loi_dan(). Không có meta
    (máy chủ trang không tới được backend KPI) ⇒ phần cố định bằng từng ký tự + dòng ngữ cảnh đúng khuôn code điền."""
    if not isinstance(chu, str):
        return False
    if meta:
        return chu == _loi_dan_tu_meta(meta)
    dau = W.LOI_DAN_GOC + "\n" + W.THEM + "\n\nNgữ cảnh (code điền): "
    return chu.startswith(dau) and bool(_NGU_CANH.fullmatch(chu[len(dau):]))


def _chuan(v):
    """Đưa giá trị ô về dạng so được: None / "" / [] / {} như nhau; bật / tắt đọc như bộ chạy (workflow_kpi._bat); số nguyên dạng chữ như số."""
    if v is None or v == "" or v == [] or v == {}:
        return ""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        t = v.strip()
        if t.lower() in ("true", "false"):
            return t.lower() == "true"
        if re.fullmatch(r"-?\d+", t):
            return int(t)
        return v
    return v


def _o(struct: dict) -> dict:
    return {p["field_id"]: p.get("value") for p in struct.get("params", [])}


def khoi_chuan(Ve, catalog, meta: dict | None = None) -> dict[str, dict]:
    """Bản chuẩn 5 khối = đúng cái workflow_kpi.dung() dựng từ danh mục đang chạy. Trả {khối gốc: struct}. `Ve` = lớp dựng (buoc15_workflow_nhieu_khoi.Ve). Người gọi tự chạy workflow_kpi.kiem_danh_muc trước (bản vá đã gắn chưa)."""
    v = Ve(catalog)
    m = meta or {"period": "2026-10", "cutoff": "2026-10-31"}
    y, mth, d = m["cutoff"].split("-")
    k = W.dung(v, "câu hỏi", ky=m["period"], ngay_chot=f"{d}/{mth}/{y}", ban_luat=m.get("rule_version"), trang_thai=m.get("status_label_for_model"), duyet_giay=300,
               nhan_vien=m.get("employee_ids"))
    s = {n["id"]: n["data"]["struct"] for n in v.nodes}
    return {s[i]["node_id"]: s[i] for i in k.values()}


def _co_day(struct: dict, nid: str, edges: list[dict]) -> dict[str, bool]:
    """Ô nào của khối đang NHẬN DÂY: theo danh sách edges (target_param_id) HOẶC incoming_connections có node_id khác rỗng (bộ dựng ghi 2 nơi;
    danh mục Capitol để sẵn mục rỗng {"node_id": ""} — không tính). Khoá None = dây vào khối mà không rõ ô đích."""
    ra = {}
    for e in edges:   # dây do API ngoài tạo có thể chỉ mang target_node_id + target_port_id / targetHandle (như may_chu.kiem_kpi_payload đọc)
        if (e.get("target") or e.get("target_node_id") or e.get("targetNodeId")) == nid:
            ra[e.get("target_param_id") or e.get("target_port_id") or e.get("targetHandle")] = True
    for p in struct.get("params", []):
        ket = (((p.get("bindable_config") or {}).get("input_port") or {}).get("incoming_connections")) or []
        if any((c or {}).get("node_id") for c in ket) or ((p.get("bindable_config") or {}).get("is_bound") and any((c or {}).get("node_id") for c in ket)):
            ra[p["field_id"]] = True
    return ra


_O_CO = {"agent_node": "tool_kpi", "guardrails_node": "che_do_kiem", "human_intervention_node": "che_do_kpi"}   # ô quyết định "đây là workflow KPI"


def la_workflow_kpi(nodes: list[dict], edges: list[dict] | None = None) -> bool:
    """Dấu hiệu KPI theo GIÁ TRỊ gõ sẵn; có `edges` thì ô cờ đang NHẬN DÂY cũng tính là KPI (nối ô nhập chữ "true" vào tool_kpi ⇒ lúc chạy vẫn bật)."""
    for n in nodes:
        s = n["data"]["struct"]
        o = _o(s)
        if edges is not None and s.get("node_id") in _O_CO and any(str(f or "").find(_O_CO[s["node_id"]]) >= 0 for f in _co_day(s, n["id"], edges)):
            return True
        if (s.get("node_id") == "agent_node" and W._bat(o.get("tool_kpi"))) or (s.get("node_id") == W.RAO and o.get("che_do_kiem") == "code") \
                or (s.get("node_id") == "human_intervention_node" and W._bat(o.get("che_do_kpi"))):
            return True
    return False


def kiem_khoi_dung_san(nodes: list[dict], chuan: dict[str, dict], meta: dict | None = None, edges: list[dict] | None = None) -> dict:
    """Gọi lúc LƯU và lúc CHẠY, cùng với workflow_kpi.kiem_payload (luật nối). `chuan` = khoi_chuan(...); `edges` = dây của workflow (bắt buộc với workflow KPI).
    Workflow không có dấu hiệu KPI ⇒ {"kpi": False} (không kiểm). Workflow KPI: mọi khối phải là 1 trong 5 khối dựng sẵn; mọi ô không thuộc
    "được sửa" / "ô dây" phải BẰNG bản chuẩn; lời dặn Trợ lý KPI phải đúng workflow_kpi.loi_dan(). Lệch ⇒ ValueError lời thường."""
    if not la_workflow_kpi(nodes, edges):
        return {"kpi": False}
    if edges is None:
        raise ValueError("kiem_khoi_dung_san cần danh sách edges của workflow KPI (để chắc ô khoá không bị nối dây)")
    loi = []
    for n in nodes:
        s = n["data"]["struct"]
        loai, ten = s.get("node_id"), s.get("name") or s.get("node_id")
        k = THEO_GOC.get(loai)
        if k is None or loai not in chuan:
            loi.append(f"khối \"{ten}\" ({loai}) không thuộc 5 khối KPI dựng sẵn — workflow KPI chỉ dùng: " + ", ".join(x["ten"] for x in KHOI))
            continue
        o, goc = _o(s), _o(chuan[loai])
        if loai == "agent_node" and not W._bat(o.get("tool_kpi")):
            loi.append(f"khối \"{ten}\" là khối Agent thường (chưa bật công cụ KPI) — trong workflow KPI chỉ được dùng khối \"Trợ lý KPI\"")
            continue
        # [D G1] ô KHOÁ / ô được sửa KHÔNG được nhận dây: giá trị gõ sẵn đúng bản chuẩn nhưng có dây vào thì lúc chạy dây thắng (vd lời dặn lấy từ một ô nhập)
        for f in sorted((x if x is not None else "(không rõ ô)") for x in _co_day(s, n["id"], edges) if x not in k["o_day"]):
            loi.append(f"khối \"{k['ten']}\": ô \"{f}\" đang bị nối dây — chỉ các ô {k['o_day'] or '(không ô nào)'} của khối này được nhận dây")
        for f in sorted(set(goc) | set(o)):
            if f == "name":
                continue
            if f in k["o_day"]:   # [D G2] ô dây: dây do kiem_payload xét; GIÁ TRỊ gõ sẵn vẫn phải bằng bản chuẩn (rỗng) — không cho chữ người dựng gõ thay chữ đã kiểm
                if _chuan(o.get(f)) != _chuan(goc.get(f)):
                    loi.append(f"khối \"{k['ten']}\": ô \"{f}\" có chữ gõ sẵn — ô này chỉ nhận nội dung qua dây")
                continue
            if f not in goc:
                if _chuan(o[f]) != "":
                    loi.append(f"khối \"{k['ten']}\": ô lạ \"{f}\" có giá trị — bản chuẩn không có ô này")
                continue
            if f in k["sua_duoc"]:
                q, v = k["sua_duoc"][f], o.get(f)
                if q["kieu"] == "chu" and not (isinstance(v, str) and 0 < len(v.strip()) <= q["toi_da"]):
                    loi.append(f"khối \"{k['ten']}\": ô \"{q['nhan']}\" phải là chữ, từ 1 tới {q['toi_da']} ký tự")
                if q["kieu"] == "so_nguyen" and not (isinstance(_chuan(v), int) and not isinstance(_chuan(v), bool) and q["tu"] <= _chuan(v) <= q["toi"]):
                    loi.append(f"khối \"{k['ten']}\": ô \"{q['nhan']}\" phải là số nguyên từ {q['tu']} tới {q['toi']}")
                continue
            if f == "system_prompt":
                if not loi_dan_dung(o.get(f), meta):
                    loi.append(f"khối \"{k['ten']}\": lời dặn đã bị sửa hoặc là bản cũ — lời dặn do hệ thống điền, không sửa tay (kéo lại khối từ mục Khối KPI dựng sẵn)")
                continue
            if _chuan(o.get(f)) != _chuan(goc[f]):
                loi.append(f"khối \"{k['ten']}\": ô \"{f}\" đã bị đổi so với khối dựng sẵn — ô này khoá")
    if loi:
        raise ValueError("Workflow KPI KHÔNG ĐẠT (khối dựng sẵn): " + "; ".join(loi))
    return {"kpi": True, "phien_ban": PHIEN_BAN, "loi_dan_sha16": loi_dan_sha16(), "so_khoi": len(nodes)}


def tao_bo_san(Ve, catalog, cau_hoi: str, meta: dict, co_duyet: bool = True, duyet_giay: int = 300):
    """Dựng "Bộ KPI sẵn" (5 khối + 5 dây; co_duyet=False cho lượt thử: 4 khối). Trả (v, {tên: id}) — v.nodes / v.edges đã qua kiem_payload."""
    v = Ve(catalog)
    y, mth, d = meta["cutoff"].split("-")
    k = W.dung(v, cau_hoi, ky=meta["period"], ngay_chot=f"{d}/{mth}/{y}", ban_luat=meta.get("rule_version"),
               trang_thai=meta.get("status_label_for_model"), duyet_giay=duyet_giay, co_duyet=co_duyet, nhan_vien=meta.get("employee_ids"))
    for ma, khoa in TEN_TRONG_DUNG.items():      # tên khối trên trang = tên khối dựng sẵn (workflow_kpi.dung đặt tên gần giống)
        if khoa in k:
            next(n for n in v.nodes if n["id"] == k[khoa])["data"]["struct"]["name"] = next(x["ten"] for x in KHOI if x["ma"] == ma)
    W.kiem_payload(v.nodes, v.edges)
    return v, k


def mau_hinh_cho_trang() -> list[dict]:
    """[ghi chú] MAU_HINH ở ĐÚNG hình của MAU (ten, cau_hoi, cong_cu, ghi_chu) để nhóm "Mẫu KPI" trên trang tự hiện 9 mẫu mới
    (trang ghép mã workflow theo TÊN). Mẫu nhiều nhánh: cau_hoi = các câu nối bằng " · "; ghi_chu nói rõ số nhánh + chỉ chạy bằng nút Chạy trên xưởng."""
    ra = []
    for m in MAU_HINH:
        n = len(m["nhanh"])
        gc = m.get("ghi_chu") or ""
        if m.get("noi_tiep"):
            gc = (gc + " Hai Trợ lý KPI nối tiếp: Trợ lý 2 đọc câu đã qua kiểm số của Trợ lý 1. Chỉ chạy bằng nút Chạy trên xưởng.").strip()
        elif n > 1:
            gc = (gc + f" {n} nhánh chạy cùng lúc, mỗi nhánh một thẻ kết quả. Chỉ chạy bằng nút Chạy trên xưởng (không qua Hỏi nhanh / Trợ lý).").strip()
        x = {"ten": m["ten"], "cau_hoi": " · ".join(nh["cau"] for nh in m["nhanh"] if nh.get("cau")), "cong_cu": m["cong_cu"]}
        if gc:
            x["ghi_chu"] = gc
        ra.append(x)
    return ra

def xuat_json(chuan: dict[str, dict], duong: Path | None = None) -> dict:
    """File cho TRANG (C nối): danh sách khối dựng sẵn + bộ sẵn + mẫu. Không có chữ lời dặn — chỉ mã băm. "gia_tri_cung" lấy từ bản chuẩn."""
    khoi = []
    for k in KHOI:
        goc = _o(chuan[k["khoi_goc"]])
        khoa = sorted(f for f in goc if f not in k["sua_duoc"] and f not in k["o_day"] and f != "name")
        khoi.append({**{x: k[x] for x in ("ma", "ten", "mo_ta", "khoi_goc")}, "sua_duoc": k["sua_duoc"], "o_day": k["o_day"], "o_an": sorted(set(k["an"]) & set(goc)),
                     "o_khoa": khoa, "ghi_chu": k.get("ghi_chu", {}),
                     "gia_tri_cung": {f: ("<workflow_kpi.loi_dan()>" if f == "system_prompt" else goc[f]) for f in khoa}})
    d = {"phien_ban": PHIEN_BAN, "loi_dan_sha16": loi_dan_sha16(), "loi_dan_nguon": "chay_thu/kpi_capitol/workflow_kpi.py:loi_dan() — trang KHÔNG giữ bản chữ riêng",
         "khoi": khoi, "bo_san": BO_KPI_SAN, "mau": MAU + mau_hinh_cho_trang(), "luat_noi": "workflow_kpi.kiem_payload + khoi_dung_san.kiem_khoi_dung_san (gọi lúc Lưu và lúc Chạy)"}
    if duong:
        duong.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return d
