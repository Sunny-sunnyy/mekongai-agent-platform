"""Cờ lưu ý dữ liệu gán THỪA nhân viên cho một KPI (ca thật Capitol 18/11, phiên d53cc44e): get_data_issues trả mỗi KPI một dòng lưu ý "không thuộc MCP"
với danh sách NV riêng (PC_BT ⇒ NV04, NV14 · PC_ON ⇒ NV08 · ASO_ALL / Turnover / LPPC ⇒ NV04, NV08, NV14); câu trả lời gắn NV08 vào PC_BT mà rào cho qua —
rào gom chủ của MỌI dòng cùng dấu hiệu thành một tập (cố ý từ 13/11 để câu "NV04 có cửa hàng ngoài MCP" không bị chặn oan).
Luật thêm (kiem_so@26, làm lại từ mốc d637b759): mục của câu trả lời nêu ĐÚNG MỘT tên KPI ⇒ người bị gắn cờ (mọi mã trong chữ của mục, trừ danh sách mã
liền sau chữ phủ định và mã ở vế khác có "không" / "chưa") phải là chủ dòng lưu ý của chính KPI đó. Kết quả công cụ dựng theo đúng hình thật (lời do code
viết), không lấy dữ liệu thật. Bản đầu của file chép từ nhánh b-co-theo-kpi (vòng 2), bổ sung theo kết quả kiểm độc lập hai vòng."""
import pytest

from kpi_agent.agent.kiem_so import kiem_so

NOTE = "không thuộc MCP của chính NV (công văn 6.2: không được tính; đã loại theo luật đường RAW)"
ISSUES = {"employee_id": "cả đội", "issues": [
    {"level": "info", "message": f"ASO_ALL: 4 cửa hàng {NOTE}", "employee_ids": ["NV04", "NV08", "NV14"]},
    {"level": "info", "message": f"PC_ON: 2 đơn ở cửa hàng {NOTE}", "employee_ids": ["NV08"]},
    {"level": "info", "message": f"Turnover: 4 cửa hàng {NOTE}", "employee_ids": ["NV04", "NV08", "NV14"]},
    {"level": "info", "message": f"PC_BT: 2 đơn ở cửa hàng {NOTE}", "employee_ids": ["NV04", "NV14"]},
    {"level": "info", "message": f"LPPC: 4 đơn ở cửa hàng {NOTE}", "employee_ids": ["NV04", "NV08", "NV14"]}]}
QUESTION = "Dữ liệu tháng này có gì cần để ý không?"
RIGHT = ("Các dòng ngoài MCP:\n- ASO_ALL: 4 cửa hàng ngoài MCP — NV04, NV08, NV14\n- PC_ON: 2 đơn ngoài MCP — NV08\n"
         "- Turnover: 4 cửa hàng ngoài MCP — NV04, NV08, NV14\n- PC_BT: 2 đơn ngoài MCP — NV04, NV14\n- LPPC: 4 đơn ngoài MCP — NV04, NV08, NV14")
TABLE = "| KPI | Lưu ý | Nhân viên |\n|---|---|---|\n"


def flagged(answer: str) -> bool:
    return "co_sai_nguoi" in [p["loai"] for p in kiem_so({"answer": answer, "question": QUESTION, "tool_results": [ISSUES]})["loi"]]


@pytest.mark.parametrize("answer", [
    RIGHT.replace("PC_BT: 2 đơn ngoài MCP — NV04, NV14", "PC_BT: 2 đơn ngoài MCP — NV04, NV14, NV08"),     # gán thừa NV08 cho PC_BT (ca thật)
    RIGHT.replace("PC_ON: 2 đơn ngoài MCP — NV08", "PC_ON: 2 đơn ngoài MCP — NV04"),                        # sai người
    "PC_BT có 2 đơn ngoài MCP, liên quan NV08.",
    TABLE + "| PC_BT | 2 đơn ngoài MCP | NV04, NV08, NV14 |",
    # vòng 2, điểm 1: mã đứng sau dấu chấm phẩy (vế không có "không" / "chưa") vẫn là người bị gắn cờ
    "- PC_BT (độ phủ bột): có đơn của cửa hàng không thuộc MCP; mã liên quan: NV08.",
    "- PC_BT: 2 đơn ngoài MCP — NV04, NV14; NV08 cũng vậy.",
    # vòng 2, điểm 2: chữ phủ định thuộc LỜI TẢ không được nuốt mã. Câu gốc "2 đơn không có trong MCP — NV08" không mang dấu hiệu nào
    # của FLAG_SIGNS (đã chạy trên mốc: qua vì không phải câu về cờ) ⇒ viết lại có "ngoài MCP" để thử đúng luật 1a.
    "- PC_BT: 2 đơn không có trong MCP, tức ngoài MCP — NV08.",
    TABLE + "| PC_BT | đơn không có trong MCP (ngoài MCP) | NV08 |",
    "- PC_BT: đơn không phải của tuyến mình, ngoài MCP — NV08.",
    "- PC_ON: đơn bị trừ vì ngoài MCP — NV04.",
    "- PC_BT: 2 đơn ngoài MCP — trừ NV04, NV14 ra thì còn NV08.",
    "- PC_BT: 2 đơn ngoài MCP — không có NV04, chỉ có NV08.",
    # Bổ sung của: lời tả có "không" đứng trước danh sách mã không làm mất mã
    "- PC_BT: 2 đơn ở cửa hàng không thuộc MCP, không được tính theo công văn 6.2 — NV04, NV14, NV08",
    # KPI không có dòng lưu ý nào mang dấu hiệu này (mọi dòng "không thuộc MCP" đều gắn một KPI khác) ⇒ gắn ai vào cũng sai
    "- ASO_Focus: 2 đơn ngoài MCP — NV04",
    "- LPPC_Meat: 4 đơn ngoài MCP — NV04, NV08, NV14",
])
def test_person_attached_to_the_wrong_kpi_flag_is_blocked(answer):
    assert flagged(answer)


@pytest.mark.parametrize("answer", [
    RIGHT,
    "NV04 có cửa hàng ngoài MCP.",                                       # không nêu KPI ⇒ luật gom chủ như cũ
    "NV08 có đơn ngoài MCP ở PC_ON.",
    "PC_BT: 2 đơn ngoài MCP (NV04, NV14); PC_ON: 2 đơn ngoài MCP (NV08).",   # hai KPI trong một mục ⇒ luật gom chủ như cũ (không đoán vế)
    TABLE + "| PC_BT | 2 đơn ngoài MCP | NV04, NV14 |\n| PC_ON | 2 đơn ngoài MCP | NV08 |",
    # mã ở VẾ KHÁC (vế không mang dấu hiệu, có "không" / "chưa") không phải người bị gắn cờ (luật 1b)
    "- PC_BT: NV04, NV14 có đơn của cửa hàng không thuộc MCP. NV08 không liên quan tới KPI này.",
    "Với PC_ON, chỉ NV08 có đơn ở cửa hàng không thuộc MCP; NV04 và NV14 thì không.",
    "PC_ON: NV08 có 2 đơn ở cửa hàng không thuộc MCP. NV04 không có đơn nào như vậy ở PC_ON.",
    # danh sách mã liền sau chữ phủ định (luật 1a)
    "- PC_ON: 2 đơn ngoài MCP — NV08 (không phải NV04)",
    "- PC_ON: 2 đơn ngoài MCP — chỉ NV08",
    "Riêng PC_ON chỉ có NV08 có đơn ở cửa hàng không thuộc MCP.",
    "- PC_BT: 2 đơn ngoài MCP — NV04 và NV14; không có NV08",
    "- PC_BT: 2 đơn ngoài MCP — NV04, NV14, chưa thấy NV08",
    # Bổ sung của: lời tả có "không" ĐỨNG TRƯỚC danh sách mã — chính lời công cụ có cụm này
    "- PC_BT: 2 đơn ở cửa hàng không thuộc MCP, không được tính theo công văn 6.2 — NV04, NV14",
    "- PC_ON: 2 đơn ngoài MCP (không được tính) — NV08",
])
def test_right_or_undetermined_flag_sentences_pass(answer):
    assert not flagged(answer)


def test_merged_summary_sentence_of_the_real_case_is_a_known_limit():
    # Dạng câu thật: liệt kê 5 KPI rồi "Liên quan: NV04, NV08, NV14; riêng PC_ON là NV08" — một đoạn nhiều KPI, rào đọc chữ không tách được ý "riêng …"
    # ⇒ VẪN QUA (giới hạn ghi rõ; chữa ở phía công cụ / lời dặn: trả từng dòng lưu ý kèm đúng mã của dòng đó).
    answer = ("Có vài điểm cần để ý. ASO_ALL: 4 cửa hàng ngoài MCP; PC_ON: 2 đơn; Turnover: 4 cửa hàng; PC_BT: 2 đơn; LPPC: 4 đơn. "
              "Liên quan: NV04, NV08, NV14; riêng PC_ON là NV08.")
    assert not flagged(answer)


# GIỮ ĐÚNG NHƯ MỐC d637b759 (kiem_so@25) — đã chạy trên mốc, kết quả ghi ở đây; lớp theo KPI không được đổi các câu này
@pytest.mark.parametrize("answer, blocked_at_base", [
    ("NV05 không có đơn của cửa hàng không thuộc MCP.", True),                         # phủ định đứng SAU mã — chặn oan có từ trước (giới hạn cũ)
    ("NV04, NV08, NV14 có đơn của cửa hàng không thuộc MCP; NV05 thì không.", True),
    ("- ASO_Focus: NV04 không có đơn ngoài MCP.", False),                               # câu "không có" ⇒ phần siết KPI-không-có-dòng không áp
])
def test_same_as_base(answer, blocked_at_base):
    assert flagged(answer) is blocked_at_base


# Ca tự thêm để thử chính luật 1a / 1b (kỳ vọng ghi TRƯỚC khi chạy)
@pytest.mark.parametrize("answer, blocked", [
    ("- PC_ON: không phải NV04 mà NV08 có 2 đơn ngoài MCP.", False),                    # phủ định đứng TRƯỚC dấu hiệu, chỉ bỏ NV04
    ("- PC_BT: không có NV08; có đơn ngoài MCP của NV04, NV14 và NV08.", True),         # mã lặp: một lần bị phủ định, một lần không ⇒ vẫn tính
    # (hai ca dưới, bản đầu dùng NV05 — không là chủ dòng nào nên luật gom chủ chặn sẵn, không thử được lớp mới ⇒ đổi sang mã chủ của dòng khác)
    ("- PC_ON: 2 đơn ngoài MCP — NV08 (trừ NV04 và NV14)", False),                      # danh sách sau "trừ" nối bằng "và"
    ("- PC_ON: 2 đơn ngoài MCP — NV08 và NV14, không gồm NV04", True),                  # hai danh sách một vế: chỉ bỏ danh sách sau "không gồm"
    ("- PC_ON: 2 đơn ngoài MCP — NV08. NV04, NV14 chưa có đơn như vậy.", False),        # vế khác có "chưa"
    ("- PC_ON: 2 đơn ngoài MCP — NV08. NV04 cũng có.", True),                           # vế khác KHÔNG có "không" / "chưa" ⇒ vẫn tính
    ("- PC_BT: 2 đơn ngoài MCP (công văn 6.2) — NV04, NV14, NV08", True),               # dấu chấm trong "6.2" không cắt vế
    ("- PC_ON: 2 đơn ngoài MCP — ngoại trừ NV04 hoặc NV14, chỉ NV08", False),            # danh sách nối bằng "hoặc"
    ("- PC_ON: 2 đơn ngoài MCP — không phải: NV04", False),                              # chỉ cách bởi dấu hai chấm vẫn là liền kề
    # (hai ca dưới thêm SAU khi sửa, để ghi lại hành vi đã thấy)
    ("NV08:\n- PC_BT có đơn ngoài MCP", True),                                           # chữ của mục không có mã ⇒ người do dòng dẫn truyền xuống
    # Phủ định đứng NGAY SAU mã ("NV04 không có …") cũng là người câu trả lời nói KHÔNG dính — mốc d637b759 cho qua các câu này, phải giữ
    ("- PC_ON: NV04 không có đơn ngoài MCP.", False),
    ("- PC_BT: NV08 chưa có đơn ngoài MCP; NV04, NV14 thì có.", False),
    ("- PC_BT: NV04 và NV14 có đơn ngoài MCP, NV08 thì không có.", False),
    ("- PC_BT: NV04 không có đơn ngoài MCP, còn NV08 thì có.", True),                    # NV08 vẫn bị gắn ⇒ chặn
])
def test_own_negation_cases(answer, blocked):
    assert flagged(answer) is blocked


# kiem_so@28 (19/11) — lỗ của @26 do tìm: câu viết đúng KHUÔN câu chuẩn lưu ý (lời chú có "không được tính") gắn vào một KPI KHÔNG có
# dòng lưu ý nào vẫn qua khi người được nêu có cờ ở KPI khác — chữ "không" của lời chú bị đọc thành câu "không có cờ". Năm câu đầu là nguyên văn của
# (đỏ trên @26 / @27); hai câu kế là ca đang chặn đúng, không được hỏng. Kỳ vọng ghi TRƯỚC khi chạy.
HOLE = "- {kpi}: 2 đơn ở cửa hàng " + NOTE + " — liên quan {codes}."


@pytest.mark.parametrize("kpi, codes", [
    ("LPPC_Meat", "NV08"), ("ASO_Focus", "NV08"), ("ASO_Focus_2", "NV08"), ("LPPC_Meat", "NV04, NV08, NV14"), ("ASO_Focus", "NV04, NV08, NV14"),
    ("LPPC_Meat", "NV05"),      # người không có cờ ở đâu — luật gom chủ chặn sẵn
    ("PC_ON", "NV04"),          # KPI có dòng, sai người — @26 chặn sẵn
])
def test_standard_shaped_sentence_on_a_kpi_without_a_flag_line_is_blocked(kpi, codes):
    assert flagged(HOLE.format(kpi=kpi, codes=codes))


@pytest.mark.parametrize("answer, blocked", [
    ("- ASO_Focus: 2 đơn ngoài MCP, không được tính — NV08", True),                     # lời chú không ngoặc
    ("- ASO_Focus: 2 đơn ngoài MCP (chưa được tính vào KPI) — NV08", True),              # "chưa" của lời chú
    ("- LPPC_Meat: 2 đơn không có trong MCP, tức ngoài MCP — NV08.", True),              # "không có trong MCP" là lời tả cửa hàng
    (TABLE + "| ASO_Focus | 2 đơn ngoài MCP, không được tính | NV08 |", True),
    ("- ASO_Focus: không có đơn ngoài MCP nào của NV04.", False),                        # câu "không có cờ" thật ⇒ luật gom chủ quyết như @26
    ("- ASO_Focus: chưa thấy đơn ngoài MCP của NV04, NV08.", False),
    ("- PC_ON: 2 đơn ở cửa hàng " + NOTE + " — liên quan NV08.", False),                 # câu chuẩn đúng KPI + đúng người ⇒ qua
])
def test_note_negation_is_not_a_no_flag_claim(answer, blocked):
    assert flagged(answer) is blocked


# Vòng 2 (19/11, trên bản đầu của @28): test bắt CẢ LOẠI, không từng câu ----------
# Loại lỗ: mục nêu một KPI KHÔNG có dòng lưu ý nào + gắn mã người có cờ ở KPI khác + một LỜI CHÚ có chữ phủ định ở bất kỳ đâu (sau dấu hiệu, trước dấu
# hiệu, trong ngoặc, ở vế sau, trong ô bảng). Hai lời chú đầu là nguyên văn (lọt trên bản đầu @28). {c} = danh sách mã.
NOTED = [
    "đơn ở cửa hàng không thuộc MCP, hệ thống không ghi nhận vào kết quả — {c}.",
    "{c} có đơn ở cửa hàng không thuộc MCP, chưa thấy được tính.",
    "2 đơn ngoài MCP (chưa có trong kết quả) — {c}",
    "2 đơn ngoài MCP; không bị tính vào KPI — liên quan {c}",
    "đơn ngoài MCP không phát sinh tiền thưởng — {c}",
    "đơn ngoài MCP, không có thưởng cho phần này — {c}",
    "2 đơn không có trong MCP, tức ngoài MCP — {c}",
    "đơn ngoài MCP không bị trừ tiền nhưng không được cộng — {c}",
    "không được tính 2 đơn ngoài MCP — {c}",                       # lời chú đứng TRƯỚC dấu hiệu
    "không có thưởng cho phần này vì đơn ngoài MCP — {c}",
    "chưa thấy được tính: đơn ở cửa hàng không thuộc MCP — {c}",
    "2 đơn ngoài MCP. Không được tính, liên quan {c}.",             # lời chú ở VẾ SAU
    "2 đơn ngoài MCP; chưa có trong kết quả, liên quan {c}",
    "{c} không có thưởng vì đơn ngoài MCP",                         # mã là chủ ngữ của lời chú, không phải của lời "không dính cờ"
    "{c} không bị tính 2 đơn ngoài MCP",
    "không có đơn ngoài MCP nào được tính — {c}",                   # có lời phủ định đi liền dấu hiệu NHƯNG mục vẫn gắn mã ⇒ vẫn xét
]


@pytest.mark.parametrize("kpi", ["ASO_Focus", "ASO_Focus_2", "LPPC_Meat"])
@pytest.mark.parametrize("codes", ["NV08", "NV04, NV14"])
@pytest.mark.parametrize("note", NOTED)
def test_any_note_with_a_negation_word_never_frees_an_attached_code(kpi, codes, note):
    assert flagged(f"- {kpi}: " + note.format(c=codes))


def test_note_in_a_table_cell_never_frees_an_attached_code():
    assert flagged(TABLE + "| ASO_Focus | đơn ngoài MCP, hệ thống không ghi nhận | NV08 |")


# Chiều ngược: câu nói thẳng "người đó KHÔNG dính" (ba khuôn hẹp trong kiem_so) phải qua — hai câu đầu bản đầu @28 chặn oan
@pytest.mark.parametrize("answer", [
    "- LPPC_Meat: NV08 không hề có đơn ở cửa hàng không thuộc MCP.",
    "- ASO_Focus_2: NV04, NV08 đều không vướng cửa hàng không thuộc MCP.",
    "- ASO_Focus: NV04 không có đơn ngoài MCP.",
    "- ASO_Focus: không có đơn ngoài MCP nào của NV04.",
    "- ASO_Focus: chưa ghi nhận đơn ngoài MCP của NV04, NV08.",
    "NV04:\n- ASO_Focus: không có đơn ngoài MCP.",                  # mục không nêu mã, người do dòng dẫn truyền xuống
    "- PC_ON: 2 đơn ở cửa hàng " + NOTE + " — liên quan NV08.",     # câu chuẩn đúng KPI + đúng người
])
def test_plain_no_flag_claims_and_right_sentences_pass(answer):
    assert not flagged(answer)
