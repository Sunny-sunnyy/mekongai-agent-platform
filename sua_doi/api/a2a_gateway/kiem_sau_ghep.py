"""BƯỚC KIỂM SAU KHI GHÉP — C 16/11/2026 (đã giao sau phép thử "trợ lý dựng workflow": 1 / 5 câu dựng được, workflow đó R12 từ chối + không có
khối Kiểm số). BẢN THỬ, CHƯA GẮN.

Ý: mỗi lần Operator gọi wb_assemble_workflow thành công, bản vá orchestrator đọc payload workflow vừa lưu, gọi kiem() dưới đây, rồi NỐI kết quả vào
kết quả công cụ trả cho mô hình ("KIỂM CỦA BỘ CHẠY THỬ: … — sửa rồi gọi assemble_workflow lại"). Tối đa 2 vòng sửa mỗi lượt chat; quá ⇒ báo người
dùng "chưa dựng được workflow chạy được", không lưu thêm. Hàm THUẦN (không I/O, không gọi AI): đầu vào là payload dạng lưu (nodes / edges), R12
truyền vào dạng hàm (trong hộp capitol-ab-api: transform_advanced_to_backend + validate_connections + kiem_r12).

Gộp 4 lớp (mỗi lớp đã có nguồn chuẩn — bản này CHÉP luật để chạy được trong hộp, đổi nguồn thì đổi đây):
  1. luật hộp (= tài liệu kỹ thuật kiem_cau_truc 5780072b): Agent run_python ≤ 1 dây file / chỉ .csv .xlsx / chỉ file tải lên;
     Agent không run_python không nhận file; khối tài liệu / chạy code không nhận file khối khác sinh; ≤ 1 khối hộp phiên mỗi tầng;
     (luật của e_kiem_hop.py 6f37e562 của E trùng các luật trên — không chép lần hai)
  2. R12 (sua_doi/workflow/r12_kiem.py fc467e3f) — gọi qua tham số r12;
  3. câu KPI phải có khối Kiểm số: Agent bật tool_kpi ⇒ phải có khối Rào (guardrails_node) che_do_kiem = "code" đứng sau Agent, và mọi khối kết
     quả (text_output / markdown_output) chỉ nhận từ khối Rào đó (hình chạy được từ chat — giao_dien/chat_chay.py du_dieu_kien);
  4. mô hình: mọi khối có ô model phải đặt MỘT tên trong MO_HINH_O_MODEL (trống = Capitol tự lấy mặc định khác ⇒ chặn); 5. không khối hành động ra ngoài.
  Cổng dùng kiem_an_toan (lỗi ⇒ chặn); luật KPI = hàm của trang truyền vào luat_kpi.
"""
from __future__ import annotations

DUOI_FILE_TAI = (".xlsx", ".csv")
RUN_PYTHON_LOAI = {"agent_node", "mcp_agent_node"}
HOP_PHIEN = {"docx_chat_node", "pptx_chat_node", "ey_pptx_chat_node", "xlsx_chat_node", "d3_data_visualization_node", "data_analysis_agent_node",
             "network_graph_output_node", "flexible_code_interpreter_node", "skill_code_interpreter_node"}
PHAT_FILE = set(HOP_PHIEN)   # khối có thể phát file ra dây
CHAY_CODE = {"flexible_code_interpreter_node", "skill_code_interpreter_node"}
KET_QUA = {"text_output_node", "markdown_output_node"}
TOI_DA_VONG = 2


def _nap_luat_tang():
    """luat_tang.py (B, hàm thuần): khi gắn thì bind CẠNH file này trong hộp; ở repo thì lấy ../r2/cong_cu/. Nạp theo đường file để khỏi trùng tên trong sys.path."""
    import importlib.util as _iu
    from pathlib import Path as _P
    for f in (_P(__file__).with_name("luat_tang.py"), _P(__file__).resolve().parents[1] / "r2" / "cong_cu" / "luat_tang.py"):
        if f.is_file():
            sp = _iu.spec_from_file_location("luat_tang_ksg", f); m = _iu.module_from_spec(sp); sp.loader.exec_module(m); return m
    raise ImportError("thiếu luat_tang.py cạnh kiem_sau_ghep.py")


_LT = _nap_luat_tang()
import re as _re
# Loại khối được dùng (danh mục chạy được ở bộ chạy thử + các khối hộp phiên đã đo 16/11). Thêm loại ⇒ thêm ở đây, có người soát.
CHO_PHEP_LOAI = {"text_input_node", "file_input_node", "agent_node", "guardrails_node", "human_intervention_node", "text_output_node",
                 "markdown_output_node", "file_browser_output_node", "sticky_note_node", "evals_node", "text_redaction_node",
                 "deep_research_node", "agentic_deep_research_node"} | HOP_PHIEN
HANH = _re.compile(r"composio|gmail|slack|wordpress|webhook|http_request|send_email|email|vercel|deploy|publish|outlook|teams|discord", _re.I)


def _st(n): return n["data"]["struct"]
def _p(n): return {q["field_id"]: q.get("value") for q in _st(n).get("params", [])}
def _bat(v): return v is True or str(v).strip().lower() in ("true", "1", "yes", "on")


def kiem(pl: dict, r12=None, luat_kpi=None) -> list[str]:
    """Danh sách lỗi viết cho MÔ HÌNH đọc (ngắn, có mã khối 8 ký tự + cách sửa). Rỗng ⇒ workflow chạy được trên bộ chạy thử."""
    nut = {n["id"]: n for n in pl.get("nodes") or []}
    loai = {i: _st(n).get("node_id") for i, n in nut.items()}
    E = pl.get("edges") or []
    loi = []
    tat = {i for i in nut if loai[i] in CHAY_CODE and _p(nut[i]).get("persist_workspace") is False}
    # 1. luật hộp
    for i, n in nut.items():
        if loai[i] not in RUN_PYTHON_LOAI:
            continue
        vao = [e for e in E if e["target"] == i]
        chac = {e["source"] for e in vao if e.get("target_param_id") == "files" or loai.get(e["source"]) == "file_input_node"}
        co_the = {e["source"] for e in vao if loai.get(e["source"]) in PHAT_FILE} - chac - tat
        if _bat(_p(n).get("tool_run_python")):
            # [ghi chú] ô tool_ma_file điền tĩnh vẫn cho chạy code có file (native_tools.py:566) nhưng chưa soát an toàn
            # mã file giữa tổ chức ⇒ VẪN chặn, chỉ đổi chữ cho đúng lý do
            if not chac and _p(n).get("tool_ma_file"): loi.append(f"{i[:8]}: Agent chạy code lấy file qua ô tool_ma_file điền tĩnh — không dùng cho workflow mới (chưa soát an toàn); nối khối Nhận file vào ô files rồi xoá ô tĩnh")
            elif not chac: loi.append(f"{i[:8]}: Agent bật chạy code mà không nhận file nào — chạy code luôn báo 'chưa có file'; tắt tool_run_python (Agent chỉ đọc chữ)")
            if len(chac) > 1: loi.append(f"{i[:8]}: Agent chạy code chỉ nhận ĐÚNG 1 file — dùng khối flexible_code_interpreter cho nhiều file")
            for k in chac:
                if loai.get(k) != "file_input_node": loi.append(f"{i[:8]}: Agent chạy code chỉ nhận file người dùng tải lên (khối Nhận file)")
                else:
                    fd = _p(nut[k]).get("file_data") or {}
                    ten = str(((fd.get("file") or {}) if isinstance(fd, dict) else {}).get("filename") or "")
                    if ten and not ten.lower().endswith(DUOI_FILE_TAI): loi.append(f"{i[:8]}: Agent chạy code chỉ đọc .csv / .xlsx (nhận '{ten}')")
            for k in co_the: loi.append(f"{i[:8]}: Agent chạy code nhận dây từ {loai[k]} (file do khối khác sinh) — nối chữ hoặc tắt persist_workspace")
        elif chac:
            loi.append(f"{i[:8]}: Agent không bật chạy code mà nhận file — bật tool_run_python hoặc dùng khối chạy code")
    for i, n in nut.items():
        if loai[i] not in HOP_PHIEN:
            continue
        chi_files = loai[i] not in CHAY_CODE
        sinh = {loai[e["source"]] for e in E if e["target"] == i and loai.get(e["source"]) in PHAT_FILE and e["source"] not in tat
                and (not chi_files or e.get("target_param_id") == "files")}
        if sinh: loi.append(f"{i[:8]}: khối {loai[i]} không nhận file do khối khác sinh ({', '.join(sorted(sinh))})")
    loi += _LT.loi_tang(pl)   # [ghi chú] luật tầng dùng chung luat_tang.py
    # 2. R12
    if r12 is not None:
        try:
            r = r12(pl)
            if r: loi.append("R12: " + str(r)[:400] + " — khối chạy code chỉ được đặt ở đầu (sau khối Nhận file); không đặt khối chạy code sau khối chạy code; tra web đặt TRƯỚC")
        except Exception as e:  # noqa: BLE001
            loi.append(f"R12: không kiểm được ({type(e).__name__}) — coi như chưa đạt")
    # 3. câu KPI — [E major 1, vòng 2] KHÔNG chép luật: gọi ĐÚNG hàm của trang (may_chu.kiem_kpi_payload(pl, luc_chay=True)) truyền vào luat_kpi
    #    ⇒ một nguồn luật; mô hình nhận ĐẠT ⇔ trang cho chạy. Bản chép vòng 1 chặn oan 112 / 525 workflow KPI có Người duyệt (E đo).
    kpi = [i for i, n in nut.items() if loai[i] in RUN_PYTHON_LOAI and _bat(_p(n).get("tool_kpi"))]
    if kpi:
        if luat_kpi is None:
            loi.append("KPI: chưa kiểm được luật KPI của trang ⇒ coi như chưa đạt")
        else:
            loi += ["KPI: " + str(x)[:300] for x in (luat_kpi(pl) or [])]
    # 4. mô hình — [E major 2] ô model TRỐNG ở khối AI ⇒ Capitol dùng mặc định (Agent: claude-sonnet-5) ⇒ chặn
    for i, n in nut.items():
        ps = _p(n)
        if "model" not in ps:
            continue
        m = ps.get("model")
        if not m:
            loi.append(f"{i[:8]}: ô model để trống — đặt {MO_HINH_O_MODEL[0]} (để trống thì Capitol tự lấy một mô hình mặc định khác)")
        elif m not in MO_HINH_O_MODEL:
            loi.append(f"{i[:8]}: ô model phải là một trong: {', '.join(MO_HINH_O_MODEL)} (đang {m})")
    # 5. [E minor vòng 2] DANH SÁCH CHO PHÉP loại khối (không chặn theo tên) — loại lạ / khối gửi ra ngoài đều không có ở đây ⇒ chặn
    for i in nut:
        if loai[i] not in CHO_PHEP_LOAI:
            loi.append(f"{i[:8]}: loại khối {loai[i]} không nằm trong danh sách khối được dùng ở bộ chạy thử — bỏ khối "
                       + ("(khối gửi dữ liệu ra ngoài — xuất file thay thế)" if HANH.search(str(loai[i] or "")) else "hoặc thay bằng khối có trong danh sách"))
    return loi


def kiem_an_toan(pl, r12=None, luat_kpi=None) -> list[str]:
    """[E major 2] CỔNG: kiem() ném lỗi / payload lạ ⇒ CHẶN (không cho qua khi nghi ngờ)."""
    try:
        return kiem(pl, r12, luat_kpi)
    except Exception as e:  # noqa: BLE001
        return [f"không kiểm được workflow ({type(e).__name__}) ⇒ coi như chưa đạt"]


def cho_phep_assemble(vong: int) -> bool:
    """[E minor] chặn bằng CODE (không chỉ bằng chữ): đã quá TOI_DA_VONG lần sửa trong lượt chat ⇒ bản vá từ chối lời gọi assemble tiếp."""
    return vong <= TOI_DA_VONG + 1


def tra_cho_mo_hinh(loi: list[str], vong: int) -> str:
    """Chữ NỐI vào kết quả wb_assemble_workflow. Vòng 1..2: đòi sửa; quá ⇒ dừng."""
    if not loi:
        return "KIỂM CỦA BỘ CHẠY THỬ: ĐẠT — workflow chạy được."
    if vong > TOI_DA_VONG:
        return "KIỂM CỦA BỘ CHẠY THỬ: vẫn chưa đạt sau 2 lần sửa — DỪNG, báo người dùng workflow chưa chạy được, liệt kê lỗi dưới:\n- " + "\n- ".join(loi[:8])
    return (f"KIỂM CỦA BỘ CHẠY THỬ (vòng {vong}/{TOI_DA_VONG}): CHƯA ĐẠT — workflow này KHÔNG chạy được. Sửa đúng các lỗi rồi gọi assemble_workflow lại "
            "(một workflow mới):\n- " + "\n- ".join(loi[:8]))


# [ghi chú] TÊN được ghi ở ô model của khối.
# Tên đầu là tên nên dùng. Hai tên Claude có tuyến ở bảng cổng AI (tên trần ⇒ mức suy luận của tuyến = medium). File mô hình khối (nếu có dòng cho loại khối)
# ĐÈ lên ô này lúc chạy. Vẫn chặn ô TRỐNG: với loại khối ngoài danh sách đổi mô hình, Capitol sẽ tự lấy mặc định "claude-sonnet-5" — tên không có ở bảng cổng.
MO_HINH_O_MODEL = ("claude-sonnet-5-5", "claude-opus-5-5", "gpt-6-luna")

# ≤ 800 ký tự — nạp vào lời dặn Operator (chat_operator.LOI_DAN) khi gắn
TOM_TAT_LUAT = (
    "LUẬT BỘ CHẠY THỬ (workflow vi phạm sẽ KHÔNG chạy):\n"
    "1. Mọi khối AI: ô model claude-sonnet-5-5.\n"
    "2. Câu về KPI: KHÔNG tự dựng khối mới — dùng / chép đúng 5 khối KPI dựng sẵn (Câu hỏi giám sát ⇒ Trợ lý KPI ⇒ Kiểm số bằng code ⇒ "
    "[Giám sát duyệt] ⇒ Kết quả), không đổi ô khoá; chỉ đổi câu hỏi. Ưu tiên workflow KPI có sẵn.\n"
    "3. Khối chạy code (flexible_code_interpreter) chỉ đặt ở đầu, ngay sau khối Nhận file; KHÔNG nối khối chạy code sau khối chạy code. Tra web đặt TRƯỚC.\n"
    "4. Agent chạy code nhận đúng 1 file .csv / .xlsx người dùng tải lên; nhiều file / file khác ⇒ dùng khối chạy code.\n"
    "5. Khối tài liệu (Excel / Word / PowerPoint / D3) không nhận file do khối khác sinh; mỗi tầng tối đa 1 khối tài liệu / chạy code — nối dây chữ để chạy lần lượt.\n"
    "6. Không dùng khối gửi ra ngoài (email, Vercel, webhook)."
)
assert len(TOM_TAT_LUAT) <= 800, len(TOM_TAT_LUAT)
