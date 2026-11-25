"""Dựng THẺ ĐẶC THÙ cho bước chọn (Jev) từ workflow ĐÃ LƯU của tổ chức hiện tại — C 18/11/2026, nhóm phát triển xác nhận (mục 3 kế hoạch Jev; người dùng: "câu đặc thù đã có
trong workflow xây sẵn"). 0 đồng, chỉ SELECT PostgreSQL, KHÔNG gọi mạng.

  python3 -I chay_thu/va_mot_o_chat/dung_the.py            # in bảng thẻ + số byte lời gửi Jev; KHÔNG ghi
  GHI=co python3 -I chay_thu/va_mot_o_chat/dung_the.py     # ghi the_dac_thu.json (chỉ khi phép kiểm chữ cấm ĐẠT)

Nguồn thẻ: "KPI mẫu N — …" (27), "KPI — …", "R2 — … — bản dựng lại", "R2 mẫu — …", "Tóm tắt văn bản …" — bản MỚI NHẤT cùng tên.
Bỏ: workflow đã có thẻ chung (trùng ten_tim của the_wf.json) + thẻ đối chứng.
Chữ gửi ra ngoài (đối chiếu kỹ thuật): KPI ⇒ phần tên sau "— " (chữ mình đặt). R2 / khác ⇒ mô tả CHUNG do C viết tay ở MO_TA
(không tên khách, tổ chức, người, URL, số liệu thật). PHÉP KIỂM BẰNG CODE trước khi ghi: chữ cấm = từ riêng trong tên workflow R2 GỐC + tên tổ chức +
URL / email / số ≥ 4 chữ số ⇒ có ⇒ KHÔNG ghi.
Cờ lấy bằng CODE từ khối (không tin chữ): goi_ra_ngoai = Agent bật tra web / đọc URL / tra tài liệu RAG / có máy chủ MCP · xuat_file = khối Word /
Excel / PowerPoint / biểu đồ / chạy code hoặc Agent bật công cụ tạo file · co_nguoi_duyet = human_intervention_node. Mức = muc_toi_da.toi_da(payload)
(thiếu mức ⇒ None ⇒ sàn ép nút). chay_tu_chat = False cho MỌI thẻ đặc thù (chạy bằng nút) — sàn bậc 3 giữ nguyên.
"""
import json
import os
import re
import sys
from pathlib import Path

G = Path(__file__).resolve().parent
sys.path[:0] = [str(G), str(G.parent / "giao_dien")]
import chon as C  # noqa: E402
import muc_toi_da as MT  # noqa: E402
import xem_workflow as X  # noqa: E402

_RUNTIME_DIR = Path(os.environ.get("CAPITOL_RUNTIME_DIR") or (G.parent / "runtime")).expanduser()
ORG = (_RUNTIME_DIR / "org_id.txt").read_text().strip()
assert re.fullmatch(r"[0-9a-f-]{36}", ORG)
RA = G / "the_dac_thu.json"

# Mô tả CHUNG do C viết tay (18/11) — khoá = tiền tố tên workflow đã lưu
MO_TA = {
    "R2 — 10q FAQ Creation": ("Bộ câu hỏi thường gặp từ báo cáo tài chính quý", "Đọc báo cáo tài chính quý của một công ty niêm yết, soạn bộ câu hỏi thường gặp kèm số liệu, ra bản trình chiếu."),
    "R2 — Article Output": ("Bài phân tích có cấu trúc", "Viết bài phân tích theo khuôn JSON từ một chủ đề, mỗi dữ kiện có nguồn."),
    "R2 — CDD": ("Thẩm định khách hàng doanh nghiệp", "Tổng hợp hồ sơ khách hàng doanh nghiệp, tra bối cảnh thị trường, đánh giá rủi ro, ra báo cáo trình chiếu."),
    "R2 — Capitol AI Daily AI News Briefing": ("Bản tin tin tức AI trong ngày", "Tra và tóm tắt tin tức AI trong ngày thành bản tin, có bước người duyệt trước khi gửi."),
    "R2 — Counter UAV Analysis": ("Phân tích chuyên đề từ nhiều tài liệu", "Phân tích chuyên đề phòng chống thiết bị bay không người lái từ nhiều file đầu vào, ra bản trình chiếu."),
    "R2 — FCA - POC workflow v7": ("Thẩm định công ty dịch vụ tài chính theo yêu cầu giám sát", "Đọc tài liệu đánh giá vốn và thanh khoản nội bộ của một công ty dịch vụ tài chính, đánh giá ban điều hành, mô hình kinh doanh, rủi ro tuân thủ, soạn thư yêu cầu bổ sung thông tin (Word)."),
    "R2 — FFIEC 041 Quarterly Call Report": ("Báo cáo tài chính quý của ngân hàng theo mẫu giám sát", "Lập báo cáo tài chính quý cho ngân hàng theo mẫu báo cáo giám sát, đối chiếu sổ cái, bắt lỗi dữ liệu, ra bảng tính và biểu đồ, có bước người duyệt."),
    "R2 — ICD 203 Analytic Standards Compliance Pipeline": ("Chấm bản phân tích theo chuẩn phân tích", "Chấm một bản phân tích theo chuẩn phân tích chuyên nghiệp: đánh giá nguồn, tách mức tin cậy, từ chỉ xác suất, giả thuyết thay thế."),
    "R2 — OFAC Blocked Accounts Daily Consolidation": ("Tổng hợp tài khoản bị phong toả hằng ngày", "Gộp danh sách tài khoản bị phong toả hằng ngày từ file CSV, che dữ liệu cá nhân, ra báo cáo gửi cơ quan quản lý (Word, bảng tính, biểu đồ)."),
    "R2 — OFAC Blocked-Property Reporting Workflow": ("Báo cáo tài sản bị phong toả theo quy định trừng phạt", "Lập báo cáo tài sản bị phong toả theo quy định trừng phạt, đánh dấu mục còn chờ xác minh."),
    "R2 — Slava test": ("Chỉ số tổng hợp và bảng điều khiển từ bảng số liệu", "Thu thập số liệu từ file, phản biện, tính chỉ số tổng hợp chuẩn hoá có trọng số, dựng bảng điều khiển và biểu đồ toàn cảnh."),
}
MOT_NV = ('Một nhân viên được bao nhiêu', 'Trả lời thắc mắc của một nhân viên', 'Vì sao chưa có tiền', 'Đạt mà sao khoản này không có tiền', 'Vượt chỉ tiêu sao tiền không tăng', 'Còn thiếu bao nhiêu để đạt mốc', 'Thưởng thêm của một nhân viên', 'Một câu gộp ba ý về một nhân viên', 'So hai nhân viên', 'So hai nhân viên rồi giải thích chỗ lệch')   # [ghi chú kỹ thuật] thẻ đặc thù cấp MỘT nhân viên (theo tên hiển thị); "Giả định …" chưa rõ ⇒ không đưa vào
XUAT = {"docx_chat_node", "xlsx_chat_node", "pptx_chat_node", "d3_data_visualization_node", "flexible_code_interpreter_node", "network_graph_output_node"}
TAO_FILE = {"tool_make_csv", "tool_make_docx", "tool_make_md", "tool_make_pdf", "tool_make_pptx", "tool_make_txt", "tool_make_xlsx"}
RA_NGOAI = {"tool_web_search", "tool_fetch_url", "tool_rag_search", "web_search"}
# [ghi chú kỹ thuật] cờ gui_thay_nguoi_dung = workflow GHI ra ngoài thay người dùng (email / đăng web / gọi dịch vụ có ghi). Đóng khi nghi:
# loại khối KHÔNG nằm trong danh sách ĐÃ XẾP dưới ⇒ coi là ghi; Agent có mcp_servers ⇒ coi là ghi (chưa xếp từng công cụ MCP).
KHOI_DA_XEP = {"agent_node", "text_input_node", "file_input_node", "guardrails_node", "text_output_node", "human_intervention_node",
               "flexible_code_interpreter_node", "markdown_output_node", "evals_node", "docx_chat_node", "xlsx_chat_node", "pptx_chat_node",
               "d3_data_visualization_node", "data_analysis_agent_node", "network_graph_output_node", "text_redaction_node"}   # đọc / tạo file trong bộ, không gửi thay người dùng


def _bat(p: dict) -> bool:
    return p.get("value") is True


def co_tu_khoi(payload: dict) -> dict:
    ra_ngoai = xuat = duyet = gui = False
    for n in payload.get("nodes", []):
        st = (n.get("data") or {}).get("struct") or {}
        loai = st.get("node_id")
        ps = {p.get("field_id"): p for p in st.get("params", []) if isinstance(p, dict)}
        if loai == "human_intervention_node":
            duyet = True
        if loai in XUAT or any(_bat(ps[k]) for k in TAO_FILE if k in ps):
            xuat = True
        co_mcp = isinstance((ps.get("mcp_servers") or {}).get("value"), (list, str)) and ps["mcp_servers"]["value"] not in ("", [], None)
        if any(_bat(ps[k]) for k in RA_NGOAI if k in ps) or co_mcp:
            ra_ngoai = True
        if loai not in KHOI_DA_XEP or co_mcp:
            gui = True
    return {"goi_ra_ngoai": ra_ngoai, "xuat_file": xuat, "co_nguoi_duyet": duyet, "gui_thay_nguoi_dung": gui}


def chu_cam() -> set[str]:
    """Từ riêng (viết hoa / có số / chữ Latin không dấu ≥ 3 ký tự) trong tên workflow R2 GỐC ("R2 nhập — …") + tên tổ chức."""
    ten = X.sql(f"select coalesce(json_agg(name), '[]') from workflow where org_id::text = '{ORG}' and name like 'R2 nhập — %'") or []
    tu = set()   # tên tổ chức thử nằm ở platform-api (DynamoDB), do mình đặt ⇒ không có tên khách; chỉ lấy từ tên R2 gốc
    for t in ten:
        for w in re.findall(r"\b[0-9]+[A-Za-z]+\b", t):   # dạng "10q"
            tu.add(w.lower())
        for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", t.replace("R2 nhập — ", "")):
            if w.lower() not in {"and", "the", "with", "for", "report", "workflow", "inputs", "analysis", "creation", "output", "daily", "test", "poc",
                                 "consolidation", "regulatory", "reporting", "quarterly", "call", "bank", "company", "research", "briefing", "news",
                                 "accounts", "blocked", "property", "analytic", "standards", "compliance", "pipeline", "midsize", "regional", "article", "csv"}:
                tu.add(w.lower())
    return tu


def kiem_chu(chu: str, cam: set[str]) -> list[str]:
    loi = [f"chữ cấm '{w}'" for w in cam if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", chu.lower())]
    if re.search(r"https?://|www\.", chu): loi.append("URL")
    if re.search(r"[\w.+-]+@[\w-]+\.", chu): loi.append("email")
    if re.search(r"\d{4,}", chu): loi.append("số ≥ 4 chữ số")
    return loi


# [ghi chú kỹ thuật] 3 workflow "KPI — …" tạo 16/11 có ô KHOÁ khác khối dựng sẵn (ground_with_provided_info,
# max_tokens…) ⇒ đường Chạy của trang từ chối. Rút khỏi danh sách gửi bộ định tuyến tới khi dựng lại bằng bộ khối chuẩn (việc riêng) rồi bỏ khỏi bảng này.
_LY = "workflow không qua luật KPI lúc chạy (ô khoá khác khối dựng sẵn) — chờ dựng lại bằng bộ khối chuẩn"
KHONG_GUI = {"wf_c2574e88": _LY, "wf_5d2d3832": _LY, "wf_d7d06bf6": _LY}
# [ghi chú kỹ thuật] 5 mẫu "một con số, CÓ bước chờ người duyệt" trùng việc với thẻ chung "KPI hỏi nhanh": câu tự nhiên
# luôn đi vào KPI hỏi nhanh (đúng luật nhóm phát triển xác nhận 18/11) ⇒ 5 thẻ không bao giờ được chọn, chỉ làm dài danh sách gửi bộ định tuyến. RÚT khỏi danh sách gửi (không xoá workflow —
# mẫu vẫn mở được trên xưởng). Khoá theo TÊN mẫu vì mã thẻ wf_… đổi mỗi lần lưu lại mẫu. SAI KHI: muốn mọi câu về tiền một nhân viên đều qua người duyệt ⇒ làm ngược lại.
_LY_TRUNG = "trùng việc với thẻ chung KPI hỏi nhanh (mẫu có bước chờ người duyệt) — rút khỏi danh sách gửi bộ định tuyến, mẫu vẫn mở được trên xưởng"
KHONG_GUI_TEN = {ten: _LY_TRUNG for ten in ("KPI mẫu 1 — Một nhân viên được bao nhiêu", "KPI mẫu 2 — Tổng thưởng cả đội", "KPI mẫu 3 — Vì sao chưa có tiền",
                                           "KPI mẫu 4 — Còn thiếu bao nhiêu để đạt mốc", "KPI mẫu 5 — Thưởng thêm của một nhân viên",
                                           # [ghi chú kỹ thuật]
                                           # mẫu 7 cùng họ với 5 mẫu trên (so hai người, CÓ bước chờ duyệt; mô tả của chính nó ghi "không dùng khi: so nhanh hai người")
                                           "KPI mẫu 7 — So hai nhân viên")}
# [ghi chú kỹ thuật] mẫu 8 CÓ khối Người duyệt rút khỏi danh sách gửi, thay bằng bản "(khung chat)" không có khối đó;
# mẫu 6 (khung chat) do HAI thẻ nền (kpi_bang_excel, kpi_xem_nhanh_chinh_thuc — the_wf.json ô ten_wf) dùng ⇒ không sinh thêm thẻ riêng. Mẫu có duyệt vẫn mở được trên xưởng.
KHONG_GUI_TEN["KPI mẫu 8 — Dữ liệu có gì cần để ý"] = "khung chat không chờ duyệt — thay bằng mẫu 8 (khung chat) không có khối Người duyệt; mẫu này vẫn mở được trên xưởng"
KHONG_GUI_TEN["KPI mẫu 6 (khung chat) — Bảng thưởng cả đội, tạm tính"] = "đã có hai thẻ nền của khung chat trỏ vào mẫu này (the_wf.json ô ten_wf) — không sinh thẻ riêng"



def dung() -> tuple[list[dict], list[str]]:
    chung = [t["ten_tim"] for t in C.doc_the()]
    ds = X.sql(f"""select coalesce(json_agg(x), '[]') from (select distinct on (w.name) w.id, w.name, v.payload
        from workflow w join workflow_version v on v.workflow_id = w.id
        where w.org_id::text = '{ORG}' and not coalesce(w.is_deleted, false) and v.version_type::text <> 'RUN'
          and (w.name ~ '^KPI mẫu [0-9]+( [(]khung chat[)])? — ' or w.name ~ '^KPI — ' or w.name ~ '^Tóm tắt văn bản')   -- loc mau KPI / tom tat van ban] User: "không chạy r2 nữa" ⇒ bỏ thẻ R2
        order by w.name, w.created_at desc, v.version_number desc) x""") or []
    cam, the, loi = chu_cam(), [], []
    for w in sorted(ds, key=lambda x: x["name"]):
        ten = w["name"]
        if any(ten.startswith(c) for c in chung) or "ngoài phạm vi" in ten:
            continue
        pl = w["payload"] if isinstance(w["payload"], dict) else json.loads(w["payload"])
        if ten.startswith("R2 — "):
            k = next((k for k in MO_TA if ten.startswith(k)), None)
            if k is None:
                loi.append(f"{ten[:50]}: chưa có mô tả viết tay ⇒ bỏ"); continue
            ten_hien, lam_gi = MO_TA[k]
        else:
            ten_hien = ten.split(" — ", 1)[1] if " — " in ten else ten
            lam_gi = ten_hien
        muc, thieu = MT.toi_da(pl)
        t = {"ma": "wf_" + w["id"][:8], "ten_tim": ten, "ten_hien": ten_hien, "lam_gi": lam_gi, "khi_nao_dung": "", "khong_dung_khi": "", "vi_du": [],
             **co_tu_khoi(pl), "muc_toi_da_uoc": None if thieu else round(muc, 4),
             "chay_tu_chat": ten.startswith("KPI"),   # [ghi chú kỹ thuật] người dùng mở chạy từ chat cho mọi thẻ KPI; thẻ khác chưa mở
             "can_ma_nv": ten_hien in MOT_NV,   # [ghi chú kỹ thuật] thẻ cấp MỘT nhân viên ⇒ câu phụ thiếu mã NV được hỏi; thẻ cả đội / chưa rõ ⇒ không
             "nguon": "dung_the.py"}
        l = kiem_chu(ten_hien + " " + lam_gi, cam)
        if l:
            loi.append(f"{t['ma']}: {', '.join(l)}")
        if t["ma"] in KHONG_GUI:   # [ghi chú kỹ thuật] thẻ trỏ workflow KHÔNG qua luật KPI lúc chạy ⇒ không gửi bộ định tuyến, không ghim (không xoá workflow)
            t["gui_buoc_chon"], t["ly_do_khong_gui"] = False, KHONG_GUI[t["ma"]]
        if t.get("ten_tim") in KHONG_GUI_TEN:   # [ghi chú kỹ thuật] rút theo TÊN mẫu
            t["gui_buoc_chon"], t["ly_do_khong_gui"] = False, KHONG_GUI_TEN[t["ten_tim"]]
        the.append(t)
    return the, loi


if __name__ == "__main__":
    the, loi = dung()
    import chon_jev as J
    # [ghi chú kỹ thuật] workflow trùng khuôn tên ("KPI — …") nhưng CHƯA có mô tả cho bộ định tuyến (vd workflow trợ lý dựng tạo trong các lượt thử) ⇒ KHÔNG thành thẻ:
    # thẻ thiếu mô tả làm khung chat không bật (noi_may_chu.dung_dep). Bỏ có in — muốn thành thẻ thì thêm mô tả vào mo_ta_the.json rồi sinh lại.
    _thieu = set(J.kiem_mo_ta([dict(t, ten_tim=t["ten_hien"], ten_goc=t["ten_tim"]) for t in the]))
    for t in the:
        if t["ten_tim"] in _thieu:
            print(f"  (bỏ, chưa có mô tả cho bộ định tuyến) {t['ma']}  {t['ten_tim'][:80]}")
    the = [t for t in the if t["ten_tim"] not in _thieu]
    tat_ca = C.doc_the() + [dict(t, ten_tim=t["ten_hien"], ten_goc=t["ten_tim"]) for t in the]   # [ghi chú kỹ thuật] CÙNG phép đổi tên với noi_may_chu.dung_dep (mô tả thẻ tra theo ten_goc)
    _, b = J.than_gui("NV02 tháng 10 được bao nhiêu", tat_ca)
    print(f"{len(the)} thẻ đặc thù · cờ: ra ngoài {sum(t['goi_ra_ngoai'] for t in the)} · gửi thay người dùng {sum(t['gui_thay_nguoi_dung'] for t in the)} · xuất file {sum(t['xuat_file'] for t in the)} · duyệt {sum(t['co_nguoi_duyet'] for t in the)} · "
          f"thiếu mức {sum(t['muc_toi_da_uoc'] is None for t in the)} · byte lời gửi Jev (7 thẻ chung + đặc thù, câu ngắn) {b}")
    for t in the:
        print(f"  {t['ma']}  {'N' if t['goi_ra_ngoai'] else '-'}{'F' if t['xuat_file'] else '-'}{'D' if t['co_nguoi_duyet'] else '-'}  {t['muc_toi_da_uoc']}  {t['ten_hien'][:70]}")
    for x in loi: print("  LỖI", x)
    if os.environ.get("GHI") == "co":
        if loi:
            print("KHÔNG ghi — còn lỗi"); sys.exit(1)
        RA.write_text(json.dumps({"ghi_chu": "C 18/11 — sinh bằng dung_the.py; KHÔNG sửa tay (sửa MO_TA trong script rồi sinh lại).", "the": the}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("GHI", RA)
