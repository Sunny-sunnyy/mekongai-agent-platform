"""Chữ gửi mô hình — bậc "chữ cho mô hình" 12/11 .
Chỉ giữ các câu đã thêm khỏi bị xoá nhầm khi sửa mô tả lần sau; tác dụng lên mô hình đo bằng bộ câu (không phải ở đây). 0 lần gọi AI."""
from kpi_agent.agent.tool_specs import tool_schemas
from kpi_agent.backend import extra_bonus_tool

TOOLS = {schema["function"]["name"]: schema["function"]["description"] for schema in tool_schemas(["Turnover", "LPPC"])}


def test_team_ranking_question_is_routed_to_one_team_table_call():
    # (a) câu "ai nhiều / ít nhất" từng làm mô hình xin 14 lần gọi > trần 5 ⇒ chỉ đường hai đầu: bảng đội nói "một lần", công cụ một người trỏ về bảng đội
    assert "nhiều nhất" in TOOLS["get_team_bonus_table"] and "MỘT lần" in TOOLS["get_team_bonus_table"]
    assert "get_team_bonus_table" in TOOLS["get_employee_result"]


def test_extra_bonus_items_are_named_by_title_and_note_names_the_right_source():
    # (b) tên khoản lấy đúng ô title (không tự đặt "khoản gia vị"); (c) luật Thưởng thêm in trên SỔ theo dõi, công văn số 21 không có
    assert "'title'" in TOOLS["get_extra_bonus"] and "9 khoản" in TOOLS["get_extra_bonus"]
    assert "sổ theo dõi" in extra_bonus_tool.NOTE and "công văn số 21 không có" in extra_bonus_tool.NOTE and "trang theo dõi" not in extra_bonus_tool.NOTE


def test_system_rules_keep_money_unit_only_after_bonus_money():
    # 12/11: lời dặn "số kiểu Việt … đ" (11/11) làm mô hình viết CHỈ TIÊU doanh số kèm "đ" ⇒ rào "tiền của chính người đó" chặn oan câu đúng (M3 Capitol
    # câu 03). Rào giữ chặt (kiem_so@17); giảm chặn oan bằng lời dặn: "đ" chỉ sau tiền thưởng. Tác dụng thật đo bằng bộ câu + đếm số câu rào chặn.
    from kpi_agent.agent.chat import SYSTEM_RULES
    assert 'chỉ viết ngay sau TIỀN THƯỞNG' in SYSTEM_RULES and 'không kèm "đ"' in SYSTEM_RULES
    assert '"1.500.000 đ"' in SYSTEM_RULES                      # ví dụ số kiểu Việt cho TIỀN vẫn còn
    # 13/11: số ví dụ không được là số THẬT trong dữ liệu (trước: chỉ tiêu + thực hiện Turnover thật của một nhân viên — mô hình chép nhầm thì khó nhận ra)
    assert "1.314.914.524" not in SYSTEM_RULES and "796.038.773" not in SYSTEM_RULES


def test_wording_round_2_keeps_its_five_instructions():
    # 13/11 bậc "chữ cho mô hình 2" (chỉ chữ): (i) hỏi mốc ⇒ explain_kpi; (ii) yêu cầu sửa / cộng ngoài luật ⇒ từ chối trước, không tự đổi kịch bản
    # (Capitol: "cộng thêm 777.777 đồng mỗi người" bị đổi thành cộng DOANH SỐ rồi chạy giả định); (iii) lương kinh doanh = 8 KPI; (iv) chỉ nhắc lưu ý
    # dữ liệu liên quan (mọi câu trả lời chép cả đoạn cảnh báo); (v) kỳ đã hết ⇒ không "nếu đạt mốc". Tác dụng thật đo bằng bộ câu.
    from kpi_agent.agent.chat import SYSTEM_RULES
    assert "MỐC / bậc" in TOOLS["explain_kpi"] and "'tiers'" in TOOLS["explain_kpi"]
    assert "không sửa và không cộng được" in SYSTEM_RULES and "không gọi simulate_kpi_change cho yêu cầu loại này" in SYSTEM_RULES
    # không viết "8 KPI" trong dòng này: mô hình chép cụm "(tổng 8 KPI)" ra câu trả lời ⇒ rào bắt số "8" không nguồn ở lần đầu (đo 13/11, ≥ 3 lượt)
    # 19/11 (bậc 3a P5): "theo công văn" ⇒ "theo luật thưởng của kỳ" (chuẩn là file luật kỳ: công văn + cách tính của trang theo dõi của đội)
    assert '"Lương kinh doanh" = tổng tiền các KPI theo luật thưởng của kỳ' in SYSTEM_RULES and "tổng tiền 8 KPI" not in SYSTEM_RULES
    assert "không chép cả danh sách" in SYSTEM_RULES and '"period_closed"' in SYSTEM_RULES
    # Dòng dặn giữ dấu chấm ở tên khoản / số mục. (Đo 13/11: "Thưởng 7,1" thật ra do bước trình bày `presentation.py` đổi, không phải mô hình —
    # sửa ở code là bậc riêng; dòng dặn giữ để mô hình không tự đổi.)
    assert '"Thưởng 7.1", "mục 6.2"' in SYSTEM_RULES
    # Đo 13/11 trên bản c19032b3: gạch "chỉ nhắc lưu ý liên quan" làm 3 / 18 câu có tiền MẤT nhãn "tạm tính" (nền 0 / 6) ⇒ tách trạng thái khỏi lưu ý
    assert '"status_label"' in SYSTEM_RULES and "LUÔN kèm nửa câu trạng thái" in SYSTEM_RULES


def test_simulate_is_only_for_stated_assumptions_and_x_over_y_is_not_a_percent():
    # 12/11 chiều: câu "… vừa đúng 72 / 72, có được trọn tiền không?" có lượt bị hiểu thành "đạt 72 %" ⇒ gọi công cụ giả định ⇒ trả lời sai tiền mà rào
    # không chặn (số có thật trong kết quả kịch bản). Giữ ý chỉ đường; tần suất thật do đo bằng bộ câu.
    assert "GIẢ ĐỊNH" in TOOLS["simulate_kpi_change"] and "HIỆN TẠI" in TOOLS["simulate_kpi_change"]
    assert "'X / Y' là thực hiện / chỉ tiêu, không phải X %" in TOOLS["simulate_kpi_change"]
    assert "get_employee_result" in TOOLS["simulate_kpi_change"] and "explain_kpi" in TOOLS["simulate_kpi_change"]


def test_missing_kpi_says_missing_not_kpi_none():
    # "KPI None" (gộp mốc 7): thiếu tham số kpi ⇒ "thiếu tên KPI", không in chữ "None"; tên lạ vẫn báo như cũ (ca xấu)
    from kpi_agent.backend.queries import _check_kpi
    for missing in (None, "", "  "):
        name, refusal = _check_kpi(missing)
        assert name is None and refusal.startswith("thiếu tên KPI — chỉ có Turnover") and "None" not in refusal
    assert _check_kpi("pc_bt") == ("PC_BT", None)
    assert _check_kpi("LPPC_Meatt")[1].startswith("KPI 'LPPC_Meatt' không có")
