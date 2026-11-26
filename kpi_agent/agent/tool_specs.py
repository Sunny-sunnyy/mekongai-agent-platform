"""Mô tả 12 công cụ KPI gửi mô hình — MỘT nguồn cho vòng chat (`agent/chat.py`), khối agent và API công cụ cho Capitol (`backend/main.py`).

Tách khỏi chat.py (10/11, nguyên chữ — mã băm mô tả không đổi) vì chat.py nạp langchain, còn backend phải chạy được trong image Capitol
(capitol-local/agentic-backend: có fastapi / yaml / openpyxl, KHÔNG có langchain).
"""


_EMPLOYEE = {"type": "string", "description": "mã nhân viên dạng NV01…NV14"}
# KPI_NAMES=on (bậc "tên vào trợ lý"): cửa vào công cụ nhận cả tên / mã SM (agent/names.py) — chữ mô tả chỉ đổi khi cờ bật (off ⇒ y cũ)
_EMPLOYEE_WITH_NAMES = {"type": "string", "description": "mã nhân viên NV01…NV14, hoặc họ tên đủ / 2 chữ cuối của tên / mã SM — đúng như người hỏi gõ"}
# Nhóm A (09/11): người vận hành A5 (giữ tham số kỳ — backend dùng nó để TỪ CHỐI kỳ khác, queries.py _check_period), chỉ sửa mô tả
_PERIOD = {"type": "string", "description": "kỳ YYYY-MM: điền kỳ người hỏi nói; bỏ trống nếu không nói; số luôn theo kỳ đang có"}
# mức tra cứu vòng 2 (09/11, đã chọn F2 — tài liệu nội bộ): chỉ đường "một người" ở mô tả A1 phải đủ HAI nhánh. Bản e66def9 chỉ trỏ
# get_employee_result ⇒ câu "vì sao không có tiền KPI X" của một người chuyển khỏi explain_kpi (T4 câu 13 ×2; câu "vì sao" 5/5 ⇒ 2/5 explain_kpi)
_ONE_EMPLOYEE_ROUTE = ("Câu về MỘT nhân viên: dùng get_employee_result; câu vì sao một nhân viên được / không được tiền ở MỘT KPI: "
                       "dùng explain_kpi.")
# Bậc (a) 11/11: câu cả đội do CODE viết (owner_check.team_sentences) — rào kiểm số chặn câu cả đội tự viết không đối chiếu được
# 11/11 chiều (Capitol v14 câu 4 lộ cụm "chép nguyên văn câu chuẩn:" ra câu trả lời): dặn chép câu vào câu trả lời, không chép kèm lời dặn
_TEAM_SENTENCES_RULE = ("Kết quả có 'team_sentences' = câu chuẩn code viết về số người có / chưa có tiền mỗi KPI: nói về số người hay ai có / chưa có "
                        "tiền thì đặt NGUYÊN câu đó vào câu trả lời, không tự viết câu khác, không ghi kèm chữ 'câu chuẩn' hay 'chép nguyên'.")


# Một tham chiếu tới MỘT số trong kết quả công cụ đọc — dùng chung cho 5 công cụ tính
_REF = {"type": "object", "description": "tham chiếu tới MỘT số đã có trong kết quả công cụ đọc: tool = tên công cụ đọc (vd get_team_bonus_table), "
        "args = đúng tham số đã gọi, KHÔNG có period — trừ calc_period_change (vd {\"employee_id\": \"NV05\"}; không có thì {}), field = đường dẫn tới số theo tên khoá của kết quả, "
        "chọn dòng trong danh sách bằng [khoá=giá trị] — vd 'employees[employee_id=NV05].business_salary', 'kpis[kpi=Turnover].actual', "
        "'extra_bonus_total'. Không gõ số.",
        "properties": {"tool": {"type": "string"}, "args": {"type": "object"}, "field": {"type": "string"}},
        "required": ["tool", "field"]}
_CALC_RULE = ("Mọi số do code lấy lại từ công cụ đọc theo tham chiếu — không gõ số. Chép số kết quả nguyên văn (tiền tới đồng, % đúng 2 số lẻ). "
              "Kết quả có 'sentences' (câu code viết về tổng / chênh của nhiều người): nói về số đó thì đặt NGUYÊN câu vào câu trả lời. "
              "Bị từ chối thì đọc lý do, sửa tham chiếu.")


def _calc_schemas() -> list[dict]:
    return [
    {"type": "function", "function": {"name": "calc_sum",
     "description": "CỘNG các số đã có từ công cụ đọc (cùng đơn vị: tiền, số đếm). Dùng khi câu hỏi cần một tổng mà không công cụ nào in sẵn — vd lương "
                    "kinh doanh + thưởng thêm + tiền của vài người. Không cộng % (dùng calc_percent_of trên tổng thực hiện / tổng chỉ tiêu). " + _CALC_RULE,
     "parameters": {"type": "object", "properties": {"values": {"type": "array", "items": _REF, "description": "2–50 tham chiếu, mỗi số một lần"}},
                    "required": ["values"]}}},
    {"type": "function", "function": {"name": "calc_difference",
     "description": "TRỪ hai số đã có (a − b, cùng đơn vị): ai hơn ai bao nhiêu, còn thiếu bao nhiêu. Hai % ⇒ kết quả là điểm phần trăm. "
                    "So lương / tiền KPI của hai nhân viên đã có compare_employees. " + _CALC_RULE,
     "parameters": {"type": "object", "properties": {"a": _REF, "b": _REF}, "required": ["a", "b"]}}},
    {"type": "function", "function": {"name": "calc_percent_of",
     "description": "TỈ LỆ % = part / whole × 100 (cùng đơn vị): một người chiếm bao nhiêu % tổng đội, thưởng thêm là bao nhiêu % tổng thu nhập. "
                    "Mẫu số 0 ⇒ từ chối. " + _CALC_RULE,
     "parameters": {"type": "object", "properties": {"part": _REF, "whole": _REF}, "required": ["part", "whole"]}}},
    {"type": "function", "function": {"name": "calc_group_total",
     "description": "TỔNG THEO NHÓM trên một bảng (danh sách dòng) của công cụ đọc: table = tham chiếu tới danh sách (vd field 'employees'), "
                    "value_field = đường dẫn tới số trong mỗi dòng (vd 'business_salary', 'kpi_amounts.Turnover'), và ĐÚNG MỘT trong hai: "
                    "group_by = ô nhãn của dòng (vd 'kpis_paid', 'kpi_reached_tier.Turnover', 'item') hoặc groups = {tên nhóm: [mã NV]} do người hỏi nêu. "
                    "Trả tổng + số dòng + mã từng nhóm; tổng các nhóm = tổng bảng (code kiểm). " + _CALC_RULE,
     "parameters": {"type": "object", "properties": {"table": _REF, "value_field": {"type": "string"}, "group_by": {"type": "string"},
                    "groups": {"type": "object", "description": "{tên nhóm: [mã NV, …]}; một người chỉ ở một nhóm"}},
                    "required": ["table", "value_field"]}}},
    {"type": "function", "function": {"name": "calc_period_change",
     "description": "SO HAI KỲ của cùng một số: chênh lệch + % thay đổi (kỳ trước 0 ⇒ chỉ có chênh lệch). Mỗi tham chiếu ghi kỳ của bên đó ở "
                    "args.period (YYYY-MM); hệ thống tự lấy bản dữ liệu của kỳ đó; chưa có dữ liệu kỳ đó ⇒ từ chối. Một kỳ chưa hết ⇒ kết quả có cảnh báo, nói kèm. " + _CALC_RULE,
     "parameters": {"type": "object", "properties": {"before": _REF, "after": _REF}, "required": ["before", "after"]}}},
    ]


def tool_schemas(kpis: list[str], names: bool = False) -> list[dict]:
    """Mô tả 10 công cụ gửi mô hình (4 bậc rào + 3 công cụ đọc 04/11 — get_team_board đã bỏ + 3 công cụ nhóm); danh sách KPI lấy từ
    backend (/api/meta) thay vì import `core/`.
    """
    employee_arg = _EMPLOYEE_WITH_NAMES if names else _EMPLOYEE
    return [
    {"type": "function", "function": {"name": "get_team_bonus_table",
     "description": "Bảng thưởng cả đội: mỗi nhân viên tiền từng KPI, % thực hiện và mốc đạt từng KPI, số KPI có tiền, tổng lương kinh doanh. "
                    "Dùng khi hỏi cả đội đứng đâu với một KPI (% / mốc từng người). Tổng tiền hoặc đếm người theo MỘT KPI: dùng get_team_kpi_summary. "
                    # 12/11 (bậc chữ cho mô hình, (a)): câu "ai nhiều / ít nhất" mô hình xin get_employee_result ×14 > trần 5 lần gọi ⇒ dừng không trả lời
                    # (3 lượt: 04/11; 11/11 13:42 câu 7; 16:19 câu 5) — bảng này đã có đủ 14 người trong MỘT lần gọi
                    "Câu so cả đội / xếp hạng (ai nhiều nhất, ít nhất, cao nhất, thấp nhất): gọi công cụ này MỘT lần rồi đọc bảng — "
                    "không gọi get_employee_result cho từng người. "
                    # 13/11 (backend/team_ranking.py): lượt thật trên Capitol mô hình tự so bảng rồi nêu SAI người thấp nhất (NV13 thay NV08) ⇒ code xếp sẵn
                    "Ai được lương kinh doanh nhiều nhất / ít nhất, xếp hạng cả đội: KHÔNG tự so các dòng — đọc ô 'ranking' (code đã xếp): "
                    "'ranking.highest' / 'ranking.lowest' là NHÓM nhiều nhất / ít nhất (kể cả đồng hạng) — nêu đủ mọi người trong 'employees' của nhóm; "
                    "'ranking.order' là các nhóm đồng hạng từ cao xuống thấp, mỗi nhóm có nhãn thứ hạng do code ghi: 'position_from_top' "
                    "(\"cao nhất\", \"cao thứ hai\"…) và 'position_from_bottom' (\"thấp nhất\", \"thấp thứ hai\"…) — hỏi cao / thấp thứ mấy thì "
                    "tìm nhóm có ĐÚNG nhãn đó, không tự đếm. "
                    "Ô này chỉ xếp tổng lương kinh doanh 8 KPI, không xếp theo từng KPI. "
                    f"{_TEAM_SENTENCES_RULE}",
     "parameters": {"type": "object", "properties": {"period": _PERIOD}, "required": []}}},
    {"type": "function", "function": {"name": "get_employee_result",
     # 13/11 (Capitol mẫu 18, 3 / 3 lượt): hỏi "có những mốc nào, mỗi mốc bao nhiêu tiền" mô hình chỉ gọi công cụ này ⇒ thiếu mốc 95 % — trỏ ngược
     "description": "Kết quả một nhân viên: 8 KPI — chỉ tiêu, thực hiện, %, mốc đạt, tiền, lý do; tổng; thưởng vượt. "
                    "Kết quả này chỉ có mốc ĐANG đạt; muốn danh sách ĐỦ các mốc + tiền từng mốc của một KPI: gọi thêm explain_kpi. "
                    "So hai nhân viên với nhau: dùng compare_employees. Xếp hạng cả đội (ai nhiều / ít nhất): dùng get_team_bonus_table. Thưởng thêm (ngoài 8 KPI) hay tổng 8 KPI + thưởng thêm: dùng get_extra_bonus.",
     "parameters": {"type": "object", "properties": {"employee_id": employee_arg, "period": _PERIOD}, "required": ["employee_id"]}}},
    {"type": "function", "function": {"name": "get_mid_month_gap",
     # 10/11 (M3 Capitol câu 9, B -98 đọc bản ghi): "NV04 cần THÊM bao nhiêu CỬA HÀNG để có tiền" trùng chữ "thêm N cửa hàng" của
     # simulate_kpi_change ⇒ mô hình gọi simulate thiếu extra ⇒ bị từ chối ⇒ hỏi lại người dùng. Câu không cho sẵn số ⇒ về đây.
     "description": "Còn thiếu bao nhiêu để lên mốc kế tiếp, tiền nếu lên mốc, ngày làm việc còn lại. Bỏ trống employee_id = cả đội. "
                    "Cũng dùng cho câu 'cần thêm bao nhiêu … để có tiền / lên mốc' khi câu KHÔNG cho sẵn số N. "
                    "Hỏi 'nếu thêm N thì sao' (số tuỳ ý): dùng simulate_kpi_change.",
     "parameters": {"type": "object", "properties": {"employee_id": employee_arg, "period": _PERIOD}, "required": []}}},
    {"type": "function", "function": {"name": "explain_kpi",
     # A-ii-1: bỏ "dùng cả cho câu 'nếu đạt X %'" — hai công cụ cùng nhận câu "nếu…" ⇒ câu giả định KPI tỷ lệ bị đẩy nhầm sang đây
     # (kiểm cuối 04/11, tài liệu nội bộ cặp 1). Công cụ này CHỈ giải thích; code không đổi (vốn không tính kịch bản)
     # 13/11 (bậc "chữ cho mô hình 2"): câu hỏi về các MỐC / bậc thang của một KPI phải tới đây — chỉ công cụ này trả ô `tiers`
     "description": "Giải thích một KPI của một nhân viên: cách đếm, mốc của công văn, vì sao được / không được tiền. "
                    "Hỏi KPI này có những MỐC / bậc nào, mỗi mốc bao nhiêu tiền: dùng công cụ này (đọc ô 'tiers'). "
                    "Câu giả định 'nếu…' (nếu thêm N, nếu đạt X % chỉ tiêu): dùng simulate_kpi_change.",
     "parameters": {"type": "object", "properties": {"employee_id": employee_arg,
                    "kpi": {"type": "string", "enum": kpis}, "period": _PERIOD}, "required": ["employee_id", "kpi"]}}},
    # 04/11: công cụ chỉ đọc mới — mô tả nói rõ khi nào dùng để mô hình không lẫn với 4 công cụ trên.
    # get_team_board đã bỏ (04/11, giữa 11:31 và 11:40) — xem backend_client.TOOL_PARAMS
    {"type": "function", "function": {"name": "get_opportunities",
     "description": "Ai có cơ hội lên bậc tháng này: các cặp nhân viên × KPI mà giữ nhịp như từ đầu kỳ thì tới được mốc kế, "
                    "tiền thêm nếu tới. Là gợi ý theo tiến độ, CHƯA phải dự báo; không xếp hạng người. "
                    "Hỏi về MỘT nhân viên còn thiếu bao nhiêu: dùng get_mid_month_gap.",
     "parameters": {"type": "object", "properties": {"period": _PERIOD}, "required": []}}},
    {"type": "function", "function": {"name": "get_data_provenance",
     "description": "Số đang xem lấy từ đâu: kỳ, ngày chốt, lúc chụp số liệu, bản luật, phiên bản workflow, bản tính đang dùng "
                    "(tính lúc nào, đã soát chưa, có bị thay chưa). Dùng khi hỏi số tin được không / tính theo gì / cập nhật chưa.",
     "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "simulate_kpi_change",
     # A-ii-1 (09/11): MỘT công cụ cho mọi câu giả định — điền ĐÚNG MỘT trong hai: extra (nếu thêm N) hoặc target_pct (nếu đạt X %); code từ chối cả hai / không cái nào.
     # KPI tỷ lệ (LPPC, LPPC_Meat) vẫn gọi công cụ này — backend từ chối rõ, để câu loại này có đường đi, không quay về explain_kpi
     # 12/11 chiều: câu "NV05 ASO_Focus vừa đúng 72 / 72, có được trọn tiền không?" — bản chữ 6530b0b1 có 3 / 20 lượt mô hình gọi công cụ này
     # (1 lượt trả lời sai "0 đ", rào không chặn vì 0 đ có thật trong kết quả kịch bản); nền 0 / 20 (KIEM_CHU_CHO_MO_HINH_C.md) ⇒ thêm MỘT ý:
     # chỉ dùng khi câu nêu giả định; "X / Y" là thực hiện / chỉ tiêu. Tác dụng đo bằng câu đó × 20, không phải ở test chữ.
     "description": "Kịch bản giả định 'nếu…' cho MỘT KPI của MỘT nhân viên ⇒ KPI đó đạt mốc nào, tiền bao nhiêu, đổi bao nhiêu so với hiện tại. "
                    "Chỉ dùng khi người hỏi nêu GIẢ ĐỊNH ('nếu …', 'giả sử …', 'thêm … thì'); câu hỏi về kết quả HIỆN TẠI (kể cả viết 'X / Y') "
                    "⇒ dùng get_employee_result / explain_kpi. 'X / Y' là thực hiện / chỉ tiêu, không phải X %. "
                    "Điền ĐÚNG MỘT trong hai: extra khi câu nói một số THÊM (thêm N đơn / N cửa hàng / N đồng doanh số); "
                    "target_pct khi câu nói ĐẠT X % chỉ tiêu. KPI tỷ lệ (LPPC, LPPC_Meat): công cụ trả lời từ chối rõ. "
                    "Câu KHÔNG cho sẵn N hay X % (vd 'cần thêm bao nhiêu'): không dùng công cụ này — dùng get_mid_month_gap.",
     "parameters": {"type": "object", "properties": {"employee_id": employee_arg, "kpi": {"type": "string", "enum": kpis},
                    "extra": {"type": "number", "description": "số thêm ≥ 0 (KPI đếm: số nguyên; Turnover: đồng) — bỏ trống nếu câu nói %"},
                    "target_pct": {"type": "number", "description": "đạt X % chỉ tiêu, 0 < X ≤ 300 (vd 94.9) — bỏ trống nếu câu nói số thêm"},
                    "period": _PERIOD},
                    "required": ["employee_id", "kpi"]}}},
    # Nhóm A bậc mức tra cứu: số tổng / đếm / chênh lệch do CODE tính sẵn
    {"type": "function", "function": {"name": "get_team_kpi_summary",
     "description": "MỘT KPI cho cả đội: tổng tiền cả đội từ KPI đó, số người có / chưa có tiền, đếm người theo từng mốc kèm mã nhân viên mỗi mốc. "
                    "Dùng cho câu 'đội được bao nhiêu từ KPI X', 'mấy người chưa có tiền X', 'có ít nhất N người đạt mốc …'. "
                    f"{_ONE_EMPLOYEE_ROUTE} {_TEAM_SENTENCES_RULE}",
     "parameters": {"type": "object", "properties": {"kpi": {"type": "string", "enum": kpis}, "period": _PERIOD}, "required": ["kpi"]}}},
    {"type": "function", "function": {"name": "compare_employees",
     # mức tra cứu vòng 2 (đã chọn 13:19 09/11): D_A câu 511 ×2 lượt gọi get_employee_result hai lần dù mô tả get_employee_result đã có "so hai nhân viên:
     # dùng compare_employees" ⇒ chỉ đường đặt thêm ở công cụ ĐÍCH
     "description": "So HAI nhân viên: mỗi người tổng lương kinh doanh và từng KPI (%, mốc, tiền), chênh lệch tiền tính sẵn "
                    "(a_minus_b, gap, higher). Câu nêu hai mã nhân viên / ai hơn ai / hơn bao nhiêu / bằng nhau không ⇒ dùng công cụ này, "
                    "không gọi get_employee_result hai lần. "
                    f"Chỉ điền kpi khi câu hỏi nêu KPI; bỏ trống kpi = so cả 8 KPI. {_ONE_EMPLOYEE_ROUTE}",
     # mức tra cứu vòng 2 (A 13:08 09/11 từ phiếu D_A): câu 508, 510 không nêu KPI mà mô hình điền "Turnover" / "LPPC" ×2 lượt ⇒ nói ở cả mô tả tham số
     "parameters": {"type": "object", "properties": {"employee_a": employee_arg, "employee_b": employee_arg,
                    "kpi": {"type": "string", "enum": kpis, "description": "chỉ điền khi câu hỏi nêu KPI; bỏ trống = so cả 8 KPI"},
                    "period": _PERIOD}, "required": ["employee_a", "employee_b"]}}},
    {"type": "function", "function": {"name": "get_data_issues",
     "description": "Cờ của bước kiểm dữ liệu (lỗi / cảnh báo / thông tin): dữ liệu thiếu, lệch chỉ tiêu, cửa hàng ngoài tuyến MCP… "
                    "Bỏ trống employee_id = cả đội; có employee_id = cờ của người đó + cờ cả đội. Một lần chụp dữ liệu, không có xu hướng "
                    "theo thời gian. Dùng khi hỏi ai / dữ liệu nào cần kiểm tra. Mỗi lưu ý có ô 'sentence' (câu chuẩn code viết: lời lưu ý + đúng những "
                    "người liên quan) — nhắc lưu ý nào thì đặt nguyên câu đó.",
     "parameters": {"type": "object", "properties": {"employee_id": employee_arg, "period": _PERIOD}, "required": []}}},
    # Bậc (b) 11/11: 9 khoản thưởng thêm NGOÀI 8 KPI — trước đây chỉ
    # có ở đường hệ thống có khoá ⇒ câu "tổng tiền / thưởng thêm" không có nguồn. Chỉ đường hai đầu (bài học 09/11): get_employee_result trỏ sang đây
    {"type": "function", "function": {"name": "get_extra_bonus",
     "description": "Thưởng thêm: 9 khoản NGOÀI 8 KPI (bia, nước mắm, xốt, Homey, mì Kokomi, xúc xích Meat 7.1–7.3), trả riêng — từng khoản: thực hiện, ngưỡng, đạt, tiền, "
                    "cách tính; tổng thưởng thêm; tổng 8 KPI + thưởng thêm do code cộng sẵn (chép, không tự cộng). Dòng có 'gioi_han' ⇒ nói kèm giới hạn đó. "
                    # 12/11 (b): trợ lý từng gọi khoản 03 / 04 là "khoản gia vị" (nhãn tự đặt, sai) ⇒ tên khoản lấy đúng ô title
                    "Gọi tên mỗi khoản đúng theo ô 'title' của dòng (vd 'Thưởng 03: ASO nước mắm cao cấp') — không tự gom thành tên nhóm khác. "
                    "Bỏ trống employee_id = cả đội. Tiền 8 KPI chi tiết: dùng get_employee_result. "
                    "Chương trình '1 % gia vị' KHÔNG nằm trong 9 khoản này: dùng get_spice_loyalty.",
     "parameters": {"type": "object", "properties": {"employee_id": employee_arg, "period": _PERIOD}, "required": []}}},
    # Công cụ 12 (12/11, backend/spice_tool.py): "1 % gia vị" trước chỉ có trên trang + đường hệ thống ⇒ trợ lý không có nguồn cho câu hỏi về nó.
    # Tiền trả KHÁCH — mô tả + ô note + câu chuẩn đều nói người nhận; kết quả không có khoá employee_id (rào không coi là tiền của nhân viên).
    {"type": "function", "function": {"name": "get_spice_loyalty",
     "description": "Chương trình '1 % gia vị': tiền thưởng trả cho KHÁCH HÀNG (điểm bán) đạt mức mua gia vị — KHÔNG phải lương hay thưởng của nhân viên, "
                    "không cộng vào 8 KPI hay Thưởng thêm. Trả: số khách trong chương trình, số khách đạt, tiền trả khách — gom theo nhân viên PHỤ TRÁCH "
                    "và cả đội. Dùng khi hỏi '1 % gia vị', 'thưởng gia vị', 'tiền trả cho khách gia vị'. Bỏ trống employee_id = cả đội. "
                    "Nêu số tiền theo một nhân viên phụ trách ⇒ chép NGUYÊN câu trong 'customer_reward_sentences'; không viết 'NVxx được / nhận … đ'.",
     "parameters": {"type": "object", "properties": {"employee_id": employee_arg, "period": _PERIOD}, "required": []}}},
    # Công cụ tính (17/11, backend/calc_tool.py — User: "chỉ cộng / trừ / %, so 2 kỳ, tổng theo nhóm"): mô hình không tự tính; số vào là THAM CHIẾU
    # tới kết quả công cụ đọc, backend chạy lại công cụ đó lấy số ⇒ không có ô nhận số gõ tay. Không có ô period (kỳ theo bản dữ liệu của câu).
    *_calc_schemas(),
    ]
