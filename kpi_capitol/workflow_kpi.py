"""Workflow KPI trên bộ Capitol — dựng payload + kiểm Đ-2 / Đ-3 / Đ-6 BẰNG CODE (B 10/11/2026, đã giao; đặc tả mekongai
tài liệu kỹ thuật; kế hoạch đo D chay_thu/kpi_capitol/KE_HOACH_DO_D.md).
Khối (đều là khối Capitol + bản vá của mình, C giữ bản gắn):
  Ô nhập (câu giám sát) ⇒ Agent (gpt-6-luna, tool_kpi = 10 công cụ gọi thẳng backend) ⇒ Rào chắn che_do_kiem="code" (kiem-so + bảng thưởng + băm)
  ⇒ Người duyệt che_do_kpi=True (bảng từ lõi, không tóm tắt AI, rỗng ≠ duyệt) ⇒ Ô ra.
  Bước 6–7 (xuất Excel + kiểm ô) CHƯA có khối — chờ chốt cách xuất (a5 / A).
Kiểm bằng code TRƯỚC khi lưu / chạy (sai ⇒ ValueError, không lưu):
  Đ-3a danh mục đang chạy có đủ ô mới (tool_kpi, che_do_kiem∋"code", che_do_kpi) — thiếu ⇒ Ve.khoi bỏ LẶNG ô ⇒ khối chạy đường AI ⇒ DỪNG;
  Đ-3b mọi Rào chắn trong payload che_do_kiem == "code"; Người duyệt che_do_kpi == True;
  Đ-2 mọi đường từ Agent có tool_kpi tới khối ra / Người duyệt đều đi QUA Rào chắn; mọi khối ra / Người duyệt là hậu duệ của Rào chắn
      (không cùng tầng, không đứng trước);
  Đ-6 Người duyệt chỉ có đúng 1 dây vào, dây đó từ Rào chắn.
  Đ-19 [lam_lai C 10/11, đã chốt] ô `cau_hoi` của Rào chắn (câu hỏi gửi dịch vụ kiểm) CHỈ được nối từ Ô nhập GỐC (text_input_node không có dây vào);
      cấm nối từ Agent / khối khác, cấm gõ sẵn giá trị; mọi dây vào Rào chắn phải ghi rõ ô đích. Vì sao: nối từ Agent ⇒ mọi số AI viết đều
      "có trong câu hỏi" ⇒ dịch vụ kiểm coi là số người hỏi nêu ⇒ lọt. Kiểm cả danh sách edges LẪN incoming_connections của ô (bộ dựng ghi 2 nơi).
Luật KPI ⇄ Exa / web: C chặn ở hook dựng công cụ (Đ-10) — ở đây chỉ KHÔNG bật (A: không kiểm trùng).
"""
from __future__ import annotations

import re

KHOI_RA = {"text_output_node", "markdown_output_node", "file_browser_output_node", "xlsx_chat_node", "human_intervention_node"}
RAO = "guardrails_node"
KHOI_CAM_KPI = {"text_to_speech_output_node", "deep_research_node", "agentic_deep_research_node"}   # [ghi chú] Đ-10b

LOI_DAN_GOC = """Bạn là trợ lý KPI cho giám sát bán hàng, trả lời bằng tiếng Việt, ngắn, rõ.
Mục tiêu: giúp giám sát biết thưởng lương kinh doanh của đội theo luật thưởng của kỳ — ai được bao nhiêu, vì sao, còn thiếu gì.
Dựa vào: CHỈ kết quả công cụ. Mọi con số (tiền, %, số đơn, số cửa hàng, ngày) phải chép đúng từ kết quả công cụ của câu này — không tự tính,
không ước, không làm tròn thô (ghi đủ số đồng). Chưa có số thì gọi công cụ; công cụ không có thì nói thẳng là chưa có dữ liệu.
- Cần cộng / trừ / tỉ lệ % / tổng theo nhóm / so hai kỳ mà công cụ đọc chưa in sẵn kết quả ⇒ gọi công cụ calc_… (tham chiếu tới số của công cụ đọc),
  không tính trong đầu.
- Thiếu mã nhân viên hay KPI mà câu hỏi cần thì hỏi lại, không đoán.
- Công cụ trả "refused" thì nói lại lý do từ chối, không thử cách khác để lách.
- Câu ngoài phạm vi (lương cơ bản, phụ cấp, ngày công 3Đ, thâm niên, nhân viên ngoài đội) ⇒ nói chưa có dữ liệu cho phần đó.
- Yêu cầu không phải câu hỏi về KPI hay thưởng của đội (viết thư, soạn văn bản, dịch, lập trình, việc khác) ⇒ trả đúng một câu:
  "Em chỉ trả lời câu hỏi về KPI và thưởng lương kinh doanh của đội." rồi dừng — không làm việc đó, không viết nháp, vì trợ lý này chỉ có dữ liệu
  và công cụ cho KPI. Ranh giới: "giải thích vì sao NV07 không có thưởng", "tóm tắt tình hình KPI của đội" vẫn là câu KPI, trả lời bình thường.
- Yêu cầu SỬA dữ liệu, chỉ tiêu, luật, hoặc CỘNG / TRỪ tiền ngoài luật thưởng ("sửa chỉ tiêu thành…", "cộng thêm … đồng cho mỗi người"): nói NGAY ở câu
  đầu là trợ lý không sửa và không cộng được — số chỉ đổi khi file dữ liệu hay luật thưởng đổi; rồi nêu số hiện tại nếu người hỏi cần. Không tự đổi yêu cầu
  đó thành kịch bản khác (không đổi số tuyệt đối thành %, không đổi tiền thành doanh số) và không gọi simulate_kpi_change cho yêu cầu loại này.
- "Lương kinh doanh" = tổng tiền các KPI theo luật thưởng của kỳ. Thưởng thêm và 1 % gia vị là khoản RIÊNG: nêu riêng, chỉ cộng chung khi người hỏi xin tổng.
- "data_flags" là danh sách lưu ý về dữ liệu, đi kèm MỌI kết quả. Chỉ nhắc lưu ý nào ảnh hưởng TRỰC TIẾP tới con số đang trả lời;
  nhắc thì dùng chữ của chính lưu ý đó (được cắt bớt phần không liên quan), không viết câu mới gắn thêm tên một KPI hay mã nhân viên — vì lưu ý
  chỉ đúng như nó được viết; không chép cả danh sách, và không nhắc khi câu hỏi không dùng số liệu bị ảnh hưởng (hỏi tên, hỏi luật, hỏi mốc).
  Riêng TRẠNG THÁI số liệu (ô "status_label": "tạm tính" hay "đã soát") thì khác: câu trả lời có nêu số tiền thì LUÔN kèm nửa câu trạng thái đó.
- Kết quả có "period_closed" (kỳ đã hết ngày): không viết "nếu đạt mốc … thì tiền thưởng là …"; chỉ nói lúc chốt đã đạt mốc nào, hụt mốc kế bao nhiêu.
- Kết quả có "team_sentences" (câu chuẩn về số người có / chưa có tiền mỗi KPI): nói về số người hay ai có / chưa có tiền thì đặt NGUYÊN câu đó
  vào câu trả lời, không ghi kèm chữ "câu chuẩn" hay "chép nguyên".
- Kết quả get_data_issues: mỗi lưu ý có ô "sentence" do code viết sẵn. Nhắc lưu ý nào thì đặt nguyên câu đó, mỗi lưu ý một gạch đầu dòng riêng,
  không thêm hay bớt mã nhân viên và không gộp mã của các lưu ý khác nhau vào một câu — vì mỗi lưu ý chỉ đúng với những người ghi trong câu của nó.
  Được thêm một câu mở đầu của riêng bạn.
- Số do bạn tự đếm (bao nhiêu KPI, bao nhiêu khoản, bao nhiêu người, bao nhiêu lưu ý): dùng số đếm công cụ đã in sẵn nếu có; không có thì liệt kê
  thẳng tên từng mục thay cho con số ("có tiền ở các KPI sau: …"). Vì mọi con số trong câu trả lời đều được đối chiếu với kết quả công cụ — số tự đếm
  không có nguồn nên không ai kiểm được nó đúng hay sai.
- Viết số kiểu Việt: dấu phẩy thập phân, dấu chấm hàng nghìn — "60,00 %", "1.500.000 đ" (không "60.00%", "1,500,000"); giữ đủ chữ số như công cụ.
  Tên khoản và số mục công văn KHÔNG phải số đo: giữ nguyên dấu chấm — "Thưởng 7.1", "mục 6.2" (không "Thưởng 7,1").
- Chữ "đ" / "đồng" chỉ viết ngay sau TIỀN THƯỞNG, lương (số tiền một người được nhận). Chỉ tiêu và thực hiện doanh số là số đo, không phải tiền nhận:
  viết số trần, không kèm "đ" — ví dụ "chỉ tiêu 1.234.567.890, thực hiện 987.654.321, tiền thưởng 0 đ"; trong bảng thì ghi "đ" ngay sau số ở từng ô tiền
  (không chỉ ghi "(đồng)" ở tiêu đề cột) — vì lớp kiểm đọc từng ô: ô số trần cạnh tên KPI bị hiểu là số thực hiện.
- So sánh hai hay nhiều người: mỗi người một đoạn (hoặc một bảng riêng) mở đầu bằng mã của người đó, không dựng bảng mỗi cột một người. Số của cả
  đội: viết chữ "cả đội" ngay trước số. Vì số đứng sau mã một người trong cùng dòng được hiểu là số của chính người đó.
- Mục công văn: chỉ nêu khi kết quả công cụ có ô "cong_van_section", và chép đúng số trong ô đó; không có ô thì không nêu mục — số mục đổi theo
  từng tháng nên không dùng số mục bạn nhớ.
Xong trông thế nào: câu trả lời có số đúng của công cụ, nêu mục công văn khi công cụ có ghi, kèm lưu ý dữ liệu khi nó ảnh hưởng tới số đang trả lời."""
# ↑ [ghi chú] chép NGUYÊN VĂN SYSTEM_RULES của mekongai b3d95954 (kpi_agent/agent/chat.py; 4.841 ký tự, sha256 đầu
#   9a1bcffad0b7eb87 — lời dặn chuẩn P5, đi CÙNG rào kiem_so@30); so BẰNG CODE trong va_loi_dan_p5/thu.sh. Bản trước (75e10c43a5e0a22a, mốc 1e7202d4): .truoc_loi_dan_p5.
# ↑ chép NGUYÊN VĂN SYSTEM_RULES của mekongai (kpi_agent/agent/chat.py:39-48, commit aa9ce76) để so 2 đường (P0).
# [ghi chú] chép lại NGUYÊN VĂN SYSTEM_RULES của mekongai develop 5d371961 (team_sentences đổi chữ thành 2 dòng + thêm dòng "Viết số kiểu Việt"); so bằng code b_11_11/so_loi_dan.py.
# [ghi chú] thêm dòng "team_sentences" chép từ mekongai nhánh b-cau-ca-doi-2 5bf3e143 (kiem_so@13); so BẰNG CODE (ast) == SYSTEM_RULES bản đó. Thêm (A + a5 10/11, kiem-so chặn câu
# bác bỏ nhắc lại số sai sau mã NV):
# [ghi chú] E đo 4 / 27 câu đầu trên Claude Sonnet medium: đúng số cả 4 nhưng 3 / 4 bị lớp
# kiểm số chặn LẦN VIẾT ĐẦU vì "số '8' … không có trong kết quả công cụ" (Sonnet viết "8 KPI" — số tự đếm) ⇒ 3–4 lời gọi / câu. Luật của lớp kiểm số (mekongai
# kpi_agent/agent/number_check.py, kiem_so@23): mọi chữ số phải BẰNG ĐÚNG một số nguồn; được tha: mã NV, số thứ tự đầu dòng, số ngay sau chữ ngày / tháng / năm,
# mã kỳ YYYY-MM. [Sửa theo người giữ lớp kiểm số bên mekongai + E N1: KHÔNG cho viết số đếm bằng chữ — đếm sai không ai bắt; chỉ chép số công cụ in sẵn hoặc liệt kê tên.]
# Ba ý dưới viết kèm LÝ DO + ví dụ (lời dặn cho Claude: giải thích vì sao, không ép bằng chữ in hoa). Đổi THEM ⇒ mã băm lời dặn đổi ⇒ phải lưu lại
# 28 mẫu KPI + ghim lại thẻ khung chat + bật lại máy chủ trang (va_loi_dan_claude/gan.sh ghi đủ trình tự + cách lùi).
# [ghi chú] E đo bản 2 (27 câu Sonnet medium): bị chặn lần viết đầu 13 ⇒ 3; ba câu còn lại là cách TRÌNH BÀY. Thêm ý 4–6,
# mỗi ý viết theo ĐÚNG luật của lớp kiểm bản 1e7202d4 (owner_check._owners_of_number + đường "số cấp đội"; owner_check luật 3; kiem_so._ratio_problems) và mọi cặp
# "chưa ổn / ổn" đã CHẠY qua chính lớp kiểm đó với kết quả công cụ thật của lượt đo (0 đồng — phiếu tài liệu nội bộ bản 3). Số trong ví dụ là số minh hoạ.
# KHÔNG thêm ý "việc ngoài KPI ⇒ từ chối" ở đây (A quyết 19/11): dự án KPI đưa đúng ý đó vào lời dặn CHUẨN (nháp P5: một câu cố định + rào code trước mô hình) —
# thêm tạm ở phần riêng sẽ làm hai bản lệch chữ.
# [ghi chú] thêm
# MỘT ý về "nhờ xuất file / hỏi bản chính thức" vào phần riêng; và sửa câu "lớp kiểm bằng code … cả câu bị chặn" đã lỗi thời từ 11:05 20/11 (AI chấm số là bên chặn, lớp code chỉ ghi). Soạn ở bản sao; phép đo tách hai nguyên nhân + tiêu chí ghi TRƯỚC ở phiếu
# tài liệu kỹ thuật Đổi THEM ⇒ mã băm lời dặn đổi ⇒ lưu lại 28 mẫu + ghim lại thẻ + bật lại trang.
THEM = """- Không nhắc lại số sai trong câu hỏi — chỉ nói số đúng.
- Khi người hỏi nhờ xuất, tạo hay gửi file (Excel, PowerPoint, PDF…), hoặc hỏi bảng đã được duyệt hay đã là bản chính thức chưa: bạn chỉ trả lời phần số liệu
  của câu hỏi, không viết rằng mình xuất được hay không xuất được file, và không tự nói bảng đã duyệt hay chưa. Lý do: việc dựng file và trạng thái duyệt do
  hệ thống xử lý ở bước sau câu trả lời của bạn và hiện ngay cạnh câu trả lời; một câu như "Em không xuất được file Excel" nằm ngay dưới liên kết tải file sẽ
  làm người đọc tưởng file hỏng. Chưa ổn: "Em không xuất được file Excel, anh chị chép bảng dưới đây nhé." Ổn: trả lời số liệu như mọi câu khác và không nhắc
  tới chuyện file.
- Vì sao phải cẩn thận với chữ số: sau khi bạn trả lời, một bộ kiểm số độc lập đọc lại câu trả lời cùng kết quả công cụ của chính câu này và đối chiếu từng
  con số (một lớp so chữ số bằng code chạy kèm để ghi nhận). Con số nào không lần ra được từ kết quả công cụ thì câu bị giữ lại, bạn phải viết lại một lần và
  người hỏi phải chờ lâu hơn; viết lại vẫn chưa đạt thì người hỏi không nhận được câu trả lời. Các thói quen dưới đây giúp câu trả lời qua ngay lần đầu:
  1. Số đếm (bao nhiêu KPI, bao nhiêu khoản, bao nhiêu mục, bao nhiêu lưu ý): nếu kết quả công cụ đã in sẵn số đếm đó thì chép đúng số ấy; nếu không thì liệt
     kê thẳng tên từng mục và không nêu số lượng. Lý do: số bạn tự đếm không nằm trong kết quả công cụ nên sẽ bị chặn, còn đổi sang viết bằng chữ ("tám KPI")
     thì không lớp nào kiểm được bạn đếm đúng hay sai — nên đừng viết số đếm tự đếm dưới bất kỳ dạng nào. Chưa ổn: "NV03 có tiền ở 8 KPI", "gồm ba khoản".
     Ổn: "NV03 có tiền ở các KPI sau: …", "gồm các khoản: …". Riêng số người có / chưa có tiền thì dùng nguyên câu "team_sentences" như đã nói ở trên.
  2. Chỉ viết chữ số có sẵn trong kết quả công cụ. Đừng tự chia, nhân, cộng, trừ, đổi đơn vị hay làm tròn để ra một số mới, kể cả số trung gian dùng để giải
     thích (ví dụ tự lấy doanh số chia cho số đơn rồi viết ra kết quả). Thật sự cần một số như vậy thì gọi công cụ calc_… và chép kết quả của nó; không cần thì
     diễn đạt bằng lời ("cao hơn chỉ tiêu", "chưa tới mốc kế").
  3. Ngày, tháng, năm, kỳ: chép đúng dạng đã có trong kết quả công cụ hoặc trong dòng ngữ cảnh bên dưới (ngày chốt, mã kỳ). Khi cần viết ra lời thì đặt chữ
     "ngày", "tháng", "năm" ngay trước từng số và chỉ dùng đúng ngày, tháng, năm đã có ở đó. Đừng tự suy ra một con số mới về thời gian — "quý 4",
     "còn 11 ngày", "sau 2 tuần" đều là số không có trong kết quả công cụ nên bị chặn.
  4. Số tiền đứng sau một mã nhân viên được hiểu là tiền của chính người đó: lớp kiểm lấy mã gần nhất đứng trước con số, kể cả mã nằm ở câu hay dòng phía trên.
     Vì vậy số của cả đội hay của một nhóm cần có chữ "cả đội", "tổng đội" hoặc "cả nhóm" đứng ngay trước con số, tốt nhất trong một câu hoặc một dòng riêng.
     Chưa ổn: "Nhóm đạt mốc gồm NV01, NV02 và NV13, tổng 9.900.000 đ." (9.900.000 bị đọc là tiền của NV13).
     Ổn: "Nhóm đạt mốc gồm NV01, NV02 và NV13. Tổng tiền của cả nhóm là 9.900.000 đ." hoặc "Tổng cả đội: 9.900.000 đ."
     Cũng vì lý do đó, khi so hai hay nhiều người thì viết mỗi người một đoạn riêng: mã đứng trước, rồi tới các số của người đó. Đừng đặt số của hai người trên
     cùng một hàng của bảng có hai cột "NV02 | NV04" — mọi số trên hàng sẽ bị đọc là của mã đứng sau cùng.
  5. Lưu ý dữ liệu ("data_flags"): khi cần nhắc thì chép phần chữ liên quan của chính lưu ý đó (được bỏ bớt vế không liên quan, không đổi chữ), đừng tự diễn lại
     thành một câu mới gắn với tên một KPI. Lý do: câu tự viết có tên KPI kèm các chữ như "hết", "đều", "tất cả", "mọi" bị đọc là một khẳng định về cả đội mà
     không có gì để đối chiếu, nên bị chặn. Chưa ổn: "Lưu ý dữ liệu: luật LPPC chưa kiểm được hết."
     Ổn: "Lưu ý dữ liệu: luật chưa kiểm được — chưa có bảng số lượng tối thiểu theo từng mã hàng, đang dùng cờ đủ số lượng của phần mềm bán hàng."
  6. Tiền thưởng trong bảng: ghi "đ" ngay sau số ở từng ô tiền, không chỉ ghi "(đồng)" ở tiêu đề cột. Lý do: ở hàng có LPPC hoặc LPPC_Meat, con số không kèm "đ" hay "%" bị đọc là số thực hiện của KPI tỷ lệ và phải đúng y số
     thực hiện trong kết quả công cụ. Chưa ổn: "| LPPC | 2.000.000 |". Ổn: "| LPPC | 2.000.000 đ |", hoặc hàng có cả số thực hiện: "| LPPC | 5,55 | 2.000.000 đ |".
  Mã nhân viên ("NV03") viết bình thường. Số thứ tự của danh sách chỉ được bỏ qua khi đứng ngay đầu dòng, dạng "1. " hoặc "2) ", không in đậm và không đặt sau
  gạch đầu dòng; nếu không chắc thì dùng gạch đầu dòng không đánh số."""


MA_NV = re.compile(r"^[A-Za-z0-9_.-]{1,20}$")   # [ghi chú] mã NV từ dữ liệu chỉ vào lời dặn khi đúng dạng (không chữ tự do — rào D G3); dùng fullmatch: .match + $ cho qua "NV01\n" (E N-M1)


def cum_doi(nhan_vien) -> str:
    """[ghi chú] "; đội có N nhân viên <đầu>…<cuối>" đúng khuôn mekongai chat.py:67-68 (e2b4dd97) từ /api/meta `employee_ids`
    (thứ tự như backend trả). Thiếu / rỗng / có mã sai dạng ⇒ "" (BỎ cụm — không đoán số người, không chép chữ lạ từ dữ liệu vào lời dặn)."""
    if not isinstance(nhan_vien, (list, tuple)) or not nhan_vien or len(nhan_vien) > 999:
        return ""
    if not all(isinstance(x, str) and MA_NV.fullmatch(x) for x in nhan_vien):
        return ""
    return f"; đội có {len(nhan_vien)} nhân viên {nhan_vien[0]}…{nhan_vien[-1]}"


def loi_dan(ky: str, ngay_chot: str, ban_luat: str | None = None, trang_thai: str | None = None, nhan_vien=None) -> str:
    """Như system_prompt(ctx) của mekongai (chat.py:50-57): phần cố định + dòng ngữ cảnh code điền (kỳ, ngày chốt, đội) + [P0 lệch 2, -73]
    "Nguồn số: bản luật …; trạng thái …" khi có (lấy từ backend lúc dựng, không phải AI)."""
    # [ghi chú] trước: so_nv=14 + "NV01…NV{so_nv:02d}" ghi cứng ⇒ sai khi đội đổi người (mọi kỳ). Nay theo danh sách thật hoặc bỏ cụm.
    s = f"{LOI_DAN_GOC}\n{THEM}\n\nNgữ cảnh (code điền): kỳ {ky}, số liệu tính tới ngày chốt {ngay_chot}{cum_doi(nhan_vien)}."
    if ban_luat:
        s += f" Nguồn số: bản luật {ban_luat}; trạng thái: {trang_thai}."
    return s


def kiem_danh_muc(tim_khoi, catalog) -> None:
    """Đ-3a: ô mới phải có trong danh mục đang chạy (bản gắn nhóm kỹ thuật)."""
    def o(loai):
        return {p["field_id"]: p for p in (tim_khoi(catalog, loai) or {}).get("params", [])}
    a, r, d = o("agent_node"), o(RAO), o("human_intervention_node")
    thieu = [x for x, ok in (("agent_node.tool_kpi", "tool_kpi" in a),
                             ("guardrails_node.che_do_kiem∋code", "code" in (r.get("che_do_kiem") or {}).get("options", [])),
                             ("guardrails_node.cau_hoi", "cau_hoi" in r),                       # [lam_lai] Đ-19
                             ("human_intervention_node.che_do_kpi", "che_do_kpi" in d)) if not ok]
    if thieu:
        raise ValueError(f"Đ-3a DỪNG: danh mục đang chạy thiếu ô {thieu} — bản vá chưa gắn (Ve.khoi sẽ bỏ lặng ⇒ khối chạy đường AI)")


def _val(struct, field):
    return next((p.get("value") for p in struct.get("params", []) if p["field_id"] == field), None)


def _bat(v) -> bool:
    """[ghi chú] cùng luật với bộ chạy: native_tools._on + toggle_enabled của Capitol coi True / "true" / "1" / "yes" / "on" là BẬT."""
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "on")) or (isinstance(v, int) and not isinstance(v, bool) and v == 1)


def kiem_payload(nodes: list[dict], edges: list[dict], can_agent: bool = True) -> dict:
    """Đ-2 / Đ-3b / Đ-6 trên payload (nodes kiểu Ve: {"id", "data": {"struct"}}). Trả bảng kiểm; sai ⇒ ValueError."""
    s = {n["id"]: n["data"]["struct"] for n in nodes}
    loai = {k: v["node_id"] for k, v in s.items()}
    sau, truoc = {}, {}
    for e in edges:
        sau.setdefault(e["source"], set()).add(e["target"])
        truoc.setdefault(e["target"], []).append(e["source"])
    loi = []
    rao = [k for k in s if loai[k] == RAO]
    if not rao:
        loi.append("không có Rào chắn")
    for k in rao:
        if _val(s[k], "che_do_kiem") != "code":
            loi.append(f"Đ-3b Rào chắn {s[k].get('name')!r} che_do_kiem={_val(s[k], 'che_do_kiem')!r} ≠ 'code'")
    agent_kpi = [k for k in s if loai[k] == "agent_node" and _bat(_val(s[k], "tool_kpi"))]   # [D Đ-17] đọc như bộ chạy (_on)
    # [ghi chú] ô HỆ THỐNG kpi_data_version ở MỌI Agent KPI và MỌI Rào chắn phải CÙNG một giá trị (hex 1–64) hoặc cùng trống:
    # lệch ⇒ Agent tính trên bản này, Rào so số trên bản khác ⇒ số "có nguồn" giả.
    dv = {"" if _val(s[k], "kpi_data_version") in (None, "") else str(_val(s[k], "kpi_data_version")) for k in agent_kpi + rao}
    if len(dv) > 1:
        loi.append(f"Đ-20 kpi_data_version của Agent KPI và Rào chắn khác nhau ({sorted(dv)}) — phải cùng một bản dữ liệu")
    elif dv and next(iter(dv)) and not re.fullmatch(r"[0-9a-f]{1,64}", next(iter(dv))):
        loi.append("Đ-20 kpi_data_version phải là chuỗi hex 1–64 ký tự (hoặc để trống = bản lúc bật)")
    # [ghi chú] khối đưa dữ liệu RA NGOÀI / kéo nội dung web lạ VÀO không được nằm chung workflow KPI (cùng ý Đ-10 cấm web / MCP
    # trong Agent KPI): đọc giọng (chữ gửi OpenAI TTS), Nghiên cứu sâu (web lạ, có thể cài lệnh vào lượt có số tiền).
    cam = sorted({loai[k] for k in s if loai[k] in KHOI_CAM_KPI})
    if agent_kpi and cam:
        loi.append(f"Đ-10b workflow KPI có khối cấm {cam} (dữ liệu ra ngoài / nội dung web lạ) — tách sang workflow riêng")
    if can_agent and not agent_kpi:            # can_agent=False chỉ cho Đ-4 (câu giả đi THẲNG vào Rào chắn — chứng minh code chặn, không qua AI)
        loi.append("không có Agent bật tool_kpi")

    def toi_duoc(tu, bo=frozenset()):           # tập khối tới được từ `tu` không đi qua khối trong `bo`
        da, hang = set(), [tu]
        while hang:
            for b in sau.get(hang.pop(), ()):
                if b not in da and b not in bo:
                    da.add(b)
                    hang.append(b)
        return da
    bo_rao = frozenset(rao)
    for a in agent_kpi:                         # Đ-2a [D Đ-15 major: đảo luật] bỏ Rào chắn khỏi đồ thị ⇒ Agent KHÔNG được tới khối NÀO
        lot = toi_duoc(a, bo_rao)               # (bản trước chỉ xét 5 loại KHOI_RA ⇒ Agent ⇒ chat_output_node lọt)
        if lot:
            loi.append(f"Đ-2 Agent {s[a].get('name')!r} tới {sorted(loai[x] for x in lot)} mà KHÔNG qua Rào chắn")
    hau_due = set().union(*(toi_duoc(r) for r in rao)) if rao else set()
    for k in s:                                 # Đ-2b: khối ra / Người duyệt phải là hậu duệ của Rào chắn (không cùng tầng / đứng trước)
        if loai[k] in KHOI_RA and k not in hau_due:
            loi.append(f"Đ-2 khối {loai[k]} {s[k].get('name')!r} không đứng SAU Rào chắn")
    for k in rao:                               # Đ-19 [lam_lai] nguồn câu hỏi của Rào chắn
        for e in edges:
            if e["target"] == k and e.get("target_param_id") not in ("guardrail_query", "cau_hoi"):
                loi.append(f"Đ-19 dây vào Rào chắn {s[k].get('name')!r} không rõ ô đích ({e.get('target_param_id')!r})")
        nguon = [e["source"] for e in edges if e["target"] == k and e.get("target_param_id") == "cau_hoi"]
        o = next((p for p in s[k].get("params", []) if p.get("field_id") == "cau_hoi"), {}) or {}
        nguon += [c.get("node_id") for c in ((((o.get("bindable_config") or {}).get("input_port") or {}).get("incoming_connections")) or [])
                  if c.get("node_id")]   # [ghi chú] danh mục Capitol để sẵn mục rỗng {"node_id": "", "port_id": ""} — không phải nguồn (ca dựng tay nhóm kỹ thuật không có)
        for x in set(nguon):
            if loai.get(x) != "text_input_node" or truoc.get(x):
                loi.append(f"Đ-19 ô cau_hoi của Rào chắn nối từ {loai.get(x, x)!r} — chỉ được từ Ô nhập gốc (text_input_node không có dây vào)")
        if len(set(nguon)) > 1:
            loi.append(f"Đ-19 ô cau_hoi của Rào chắn có {len(set(nguon))} nguồn — chỉ 1")
        if isinstance(o.get("value"), str) and o["value"].strip() or isinstance(o.get("value"), list) and o["value"]:
            loi.append("Đ-19 ô cau_hoi của Rào chắn có giá trị gõ sẵn — chỉ được lấy qua dây từ Ô nhập")
    for k in s:                                 # Đ-6 + Đ-3b Người duyệt
        if loai[k] == "human_intervention_node":
            if _val(s[k], "che_do_kpi") is not True:
                loi.append(f"Đ-3b Người duyệt {s[k].get('name')!r} che_do_kpi ≠ True")
            vao = truoc.get(k, [])
            if len(vao) != 1 or loai[vao[0]] != RAO:
                loi.append(f"Đ-6 Người duyệt cần đúng 1 dây vào từ Rào chắn, đang có {[loai[x] for x in vao]}")
            # [ghi chú] Đ-17 cũ cấm bật; nay ĐƯỢC bật (khối ghi nhãn tự duyệt, không ghi
            # "đã duyệt" vào lõi). Đã bật thì hạn chờ phải rõ: số nguyên 30–3.600 giây ([D Đ-17] "true" / "1" / "yes" / "on" cũng là BẬT).
            if _bat(_val(s[k], "auto_approve_on_timeout")):
                han = _val(s[k], "timeout")
                if isinstance(han, bool) or not isinstance(han, int) or not 30 <= han <= 3600:
                    loi.append(f"Đ-17 Người duyệt bật tự duyệt khi hết giờ nhưng hạn chờ {han!r} không phải số nguyên 30–3600 giây")
    if loi:
        raise ValueError("Workflow KPI KHÔNG ĐẠT: " + "; ".join(loi))
    return {"rao_chan": len(rao), "agent_kpi": len(agent_kpi), "khoi_ra_sau_rao": sum(1 for k in s if loai[k] in KHOI_RA), "dat": True}


def dung(v, cau_hoi: str, ky: str, ngay_chot: str, ban_luat: str | None = None, trang_thai: str | None = None, duyet_giay: int = 300,
         data_version: str | None = None, co_duyet: bool = True, *, nhan_vien):
    # [ghi chú] nhan_vien BẮT BUỘC (không mặc định): lần gắn 14:51 sót 4 chỗ gọi ⇒ lời dặn thiếu cụm đội ⇒ kiểm mẫu trượt. Bắt buộc ⇒ chỗ gọi sót NÉM ngay.
    """co_duyet=False [ghi chú]: Rào chắn nối thẳng Ô ra (vẫn qua kiem_payload)."""
    """Dựng 5 khối vào Ve `v` (buoc15_workflow_nhieu_khoi.Ve). Trả {tên: id}."""
    k = {"vao": v.khoi("text_input_node", "Câu hỏi giám sát", 0, 0, {"text_input": cau_hoi}),
         "agent": v.khoi("agent_node", "Trợ lý KPI", 350, 0, {"model": "gpt-6-luna", "system_prompt": loi_dan(ky, ngay_chot, ban_luat, trang_thai, nhan_vien),
                                                              "tool_kpi": True, "temperature": None, "max_tokens": 4000,
                                                              "kpi_data_version": data_version or ""}),   # [P0 lệch 3] chat mekongai không đặt ⇒ 4000 (trần chay_s5) để không cắt câu dài
         # Capitol KHÔNG có ô reasoning_effort cho Agent (danh mục: model / temperature / max_tokens). Mức thật [code]: sổ đăng ký mô hình
         # của bộ ghi gpt-6-luna reasoning_effort="low" (vá 03/11, người vận hành — sua_doi/core/config/ai/registry.py:466-468) ⇒ đặt vào
         # openai_reasoning_effort (sua_doi/ai/agentic/models/openai.py:166-167). Áp MỌI chỗ dùng luna ⇒ không đổi riêng workflow này.
         # Chat mekongai chạy medium (-73 đính chính) ⇒ lệch P0: -73 / A chạy lại mốc chat ở "low".
         "rao": v.khoi(RAO, "Kiểm số bằng code", 700, 0, {"che_do_kiem": "code", "kpi_data_version": data_version or ""}),   # [C data_version] CÙNG giá trị với Agent
         "duyet": v.khoi("human_intervention_node", "Giám sát duyệt bảng thưởng", 1050, 0,
                         {"che_do_kpi": True, "timeout": duyet_giay, "auto_approve_on_timeout": True,   # [ghi chú] hết hạn không ai bấm ⇒ tự đi tiếp, mang nhãn tự duyệt
                          "reviewer_note": "Duyệt bảng thưởng từ lõi tính (không phải số AI viết). Kiểm mã băm trước khi bấm."}),
         "ra": v.khoi("text_output_node", "Kết quả đã duyệt", 1400, 0)}
    v.noi(k["vao"], k["agent"], "user_prompt")
    v.noi(k["agent"], k["rao"], "guardrail_query")
    v.noi(k["vao"], k["rao"], "cau_hoi")        # [lam_lai] câu hỏi gốc cho dịch vụ kiểm (Đ-19: chỉ từ Ô nhập)
    if co_duyet:
        v.noi(k["rao"], k["duyet"], "preview_context")
        v.noi(k["duyet"], k["ra"], "text_content")
    else:
        bo = k.pop("duyet")
        v.nodes[:] = [n for n in v.nodes if n["id"] != bo]
        v.noi(k["rao"], k["ra"], "text_content")
    return k


if __name__ == "__main__":   # tự thử Đ-2 / Đ-6 trên đồ thị dựng tay (0 đồng, không cần bộ): 1 ca đúng + 3 ca sai, mỗi ca sai đúng 1 luật
    def n(i, loai, **p):
        return {"id": i, "data": {"struct": {"node_id": loai, "name": i, "params": [{"field_id": a, "value": b} for a, b in p.items()]}}}
    def e(a, b, o=None):   # [lam_lai] dây vào Rào chắn phải ghi ô đích (Đ-19)
        return {"source": a, "target": b, "target_param_id": o or ("guardrail_query" if b == "rao" else "x")}
    N = [n("vao", "text_input_node"), n("ag", "agent_node", tool_kpi=True), n("rao", RAO, che_do_kiem="code"),
         n("dy", "human_intervention_node", che_do_kpi=True), n("ra", "text_output_node")]
    E = [e("vao", "ag"), e("ag", "rao"), e("rao", "dy"), e("dy", "ra")]
    ca = {"dung": (N, E, True),
          "ra_tat_qua_rao": (N, E + [e("ag", "ra")], False),                                    # Đ-2a
          "rao_che_do_ai": ([x if x["id"] != "rao" else n("rao", RAO, che_do_kiem="ai") for x in N], E, False),   # Đ-3b
          "duyet_2_day": (N, E + [e("vao", "dy")], False),                                      # Đ-6 (+ Đ-2b vẫn đạt vì dy sau rao)
          "agent_toi_chat_output": (N + [n("co", "chat_output_node")], E + [e("ag", "co")], False),   # [D Đ-15] loại khối ra không có trong KHOI_RA
          # [lam_lai] Đ-19 — ca dương + 5 ca âm, mỗi ca âm sai đúng 1 luật
          "cau_hoi_tu_o_nhap": (N, E + [e("vao", "rao", "cau_hoi")], True),
          "cau_hoi_tu_agent": (N, E + [e("ag", "rao", "cau_hoi")], False),
          "cau_hoi_tu_o_nhap_khong_goc": (N + [n("v2", "text_input_node")], E + [e("ag", "v2"), e("v2", "rao", "cau_hoi")], False),
          "cau_hoi_go_san": ([x if x["id"] != "rao" else n("rao", RAO, che_do_kiem="code", cau_hoi="doanh số tôi 9.999.999") for x in N], E, False),
          "day_vao_rao_khong_ro_o": (N, E + [{"source": "vao", "target": "rao"}], False)}
    def _co_incoming(nd, nguon):   # ô cau_hoi có incoming_connections từ `nguon` mà KHÔNG có dây trong edges
        nd["data"]["struct"]["params"].append({"field_id": "cau_hoi", "value": None,
                                               "bindable_config": {"input_port": {"incoming_connections": [{"node_id": nguon}]}}})
        return nd
    ca["cau_hoi_chi_trong_incoming"] = ([x if x["id"] != "rao" else _co_incoming(n("rao", RAO, che_do_kiem="code"), "ag") for x in N], E, False)
    for ten, (nn, ee, mong) in ca.items():
        try:
            kiem_payload(nn, ee)
            ok = True
            ly = ""
        except ValueError as x:
            ok, ly = False, str(x)[:160]
        print(f"{ten}: {'ĐẠT' if ok else 'TRƯỢT'} (mong {'ĐẠT' if mong else 'TRƯỢT'}) {'✓' if ok == mong else '✗ SAI'} {ly}")
