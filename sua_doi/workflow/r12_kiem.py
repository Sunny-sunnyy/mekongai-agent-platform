"""[VÁ 10 chay_thu, 06/11/2026 — A] Sandbox R12 (b) + (c): chặn "bộ ba nguy hiểm" ở MỨC WORKFLOW (chay_thu/THIET_KE_SANDBOX.md mục 3, R12).
[VÁ R12b, 09/11/2026 — nhóm kỹ thuật; lỗ C tìm, D xác nhận `r12_kiem.py:72` cũ] "gốc chạy code" xét theo LOẠI KHỐI, không chỉ ô tool_run_python.
[R12b bản 2, 09/11 — B, sửa minor D mục 53: m1 khối công cụ xét theo DÂY; m2 loại lạ nhận file ⇒ gốc]. Bản 1 (de016e4e) đang gắn.

Vì sao: hộp chạy code không có mạng, nhưng KẾT QUẢ của nó (có dữ liệu Excel) quay về AI rồi theo dây sang các khối sau. Một khối sau gửi
ra ngoài được (tra web, đọc trang, MCP, WordPress…) là dữ liệu đi ra. Nên: mọi khối đứng SAU (theo dây, kể cả gián tiếp) một khối chạy
code phải thuộc DANH SÁCH CHO PHÉP — danh sách cho phép, không phải danh sách cấm, để khối mới thêm vào danh mục mặc định bị cấm.

Lỗ bản 06/11: chỉ khối bật `tool_run_python` (công cụ của mình) được coi là gốc. Capitol còn 2 họ tự mở hộp E2B (CÓ MẠNG mặc định —
AB services/sandbox/manager.py:375-381) mà R12 không thấy [code, agentic-backend release-4.10.0]:
  * LUÔN mở hộp: code interpreter (`SandboxMode.ALWAYS`, nodes/agent/code_interpreter/node.py:73; bản skill kế thừa, code_interpreter_skill/node.py:44)
    và 7 khối dùng E2BSkillsChatService (nodes/output/{data_analysis,spreadsheet_chat,word_doc_chat,powerpoint_chat,ey_powerpoint_chat,
    d3_data_visualization,org_chart}/node.py).
  * mở hộp KHI NHẬN FILE: họ Agent (`SandboxMode.IF_FILES` mặc định, workflow/node/base/agent/sandbox_runtime.py:117; agent lấy file từ MỌI ô
    có dây vào, :215-262) — agent_node, mcp_agent_node, agentic_deep_research_node (kế thừa mcp_agent). vercel_deploy_agent_node = NEVER.
  Lớp lúc chạy (vá 10, sua_doi/workflow/node/base/agent/node.py:1019-1031) cấm E2B cho họ Agent, nhưng 7 khối E2BSkillsChatService KHÔNG đi
  qua lớp đó — chỉ đang hỏng vì E2B_API_KEY rỗng. R12 mức workflow là lớp chặn sớm cho cả hai họ.

Gọi ở đâu (D, chay_thu/SOAT_THIET_KE_SANDBOX_D.md mục 11 — không có một chỗ chung, cần 3 chỗ):
  (1) temporal/activities/prepare_workflow.py ngay sau validate_connections — mọi lượt Temporal (trang, API v1, lịch chạy, chạy lại, Operator);
  (2) services/worker/executor.py ngay sau validate_connections — đường hàng đợi Redis;
  (3) sửa khi tạm dừng (apply_edit_activity, temporal/activities/persist_results.py).
Hàm chỉ ĐỌC payload, ném ValueError có lý do rõ ⇒ lượt chạy FAILED trước khi khối nào chạy.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Loại khối (NodeInstance.node_id) được đứng sau khối chạy code. D lập từ danh mục + code (việc 4, 06/11) — xem mục 11 file soát.
CHO_PHEP: set[str] = {"text_output_node", "markdown_output_node", "file_browser_output_node", "human_intervention_node",
                      "sticky_note_node", "agent_node"}   # D việc 4, 06/11 (SOAT_THIET_KE_SANDBOX_D.md mục 11.4): mọi loại khác CẤM
# [ghi chú] 5 khối chạy code trong HỘP PHIÊN của mình (--network none — sua_doi/services/
# sandbox/hop_phien.py:59; cờ CHAY_THU_HOP_PHIEN bật ở 2 worker) được đứng sau gốc chạy code CHỈ KHI ô mcp_servers RỖNG và không có khối công
# cụ / ô tools gắn vào — vì e2b_chat_service.py:984 gắn toolsets=mcp_servers mà KHÔNG qua danh sách cho phép MCP của lớp nền. Luật viết theo Ô.
# VẪN CẤM: vercel_deploy_agent_node (đăng trang ra Internet), Agent / agentic có tra web / MCP, ey_pptx_chat_node (chưa có bằng chứng).
# [vòng 3 16/11] network_graph, evals, text_redaction đã có bằng chứng ⇒ mở có điều kiện (dưới).
CHO_PHEP_KHI_SACH: set[str] = {"docx_chat_node", "xlsx_chat_node", "pptx_chat_node", "d3_data_visualization_node", "data_analysis_agent_node",
                                # [B vòng 3, 16/11 — bằng chứng B_BANG_CHUNG_R12_KHOI.md mục 1] cùng E2BSkillsChatService (org_chart/node.py:60), gọi
                                # stream_generate KHÔNG truyền mcp_servers (:119-125), ô chỉ model / system_prompt / task / files (params.py)
                                "network_graph_output_node"}
# [B vòng 3, 16/11 — bằng chứng B_BANG_CHUNG_R12_KHOI.md mục 2–3] khối chỉ gửi chữ tới mô hình qua dịch vụ CỦA MÌNH, đích cố định (không ô URL /
# MCP / công cụ), không mở hộp chạy code:
#   text_redaction_node — chỉ đọc inbound.data (chữ), trả NodeOutput(data) KHÔNG file (nodes/redaction/node.py:128-133, 200); Agent tools=[]
#     request_limit=1, gpt-6-luna (sua_doi/services/redaction/redaction_service.py:233-236, 327-330, :76-78).
#   evals_node — CHỈ KHI cờ CHAY_THU_EVAL_CHI_PASS_FAIL bật (bài v2 có vòng agent / file / tra web của eval-service bị từ chối trước khi gửi,
#     sua_doi/services/platform/evals.py:506-509, 594-597); đích = platform-api ⇒ eval-service (evals__BASE_URL cố định); eval-service từ chối
#     file / URL / bài khác pass_fail-score (eval_srvc/api/eval_runner.py:39-56, cờ EVAL_CHI_PASS_FAIL mặc định bật).
CHO_PHEP_CO_DIEU_KIEN: set[str] = {"text_redaction_node", "evals_node"}
# Khối Agent: chỉ cho phép khi chính nó không bật công cụ ra ngoài và không có toolset / khối công cụ nối vào.
LOAI_AGENT: set[str] = {"agent_node"}
CONG_CU_DI_KEM = {"tool_current_time", "tool_order_lookup", "tool_sql_query",
                  "tool_make_xlsx", "tool_make_docx", "tool_make_pptx", "tool_make_pdf",
                  "tool_make_md", "tool_make_txt", "tool_make_csv"}   # giống native_tools.CONG_CU_DI_KEM_RUN_PYTHON ([ghi chú] thêm md / txt / csv)

# [R12b 09/11] gốc chạy code THEO LOẠI KHỐI (danh mục 44 của máy mình; mã lấy từ `node_id = "…"` trong từng file khối — dẫn ở docstring)
GOC_LUON: set[str] = {
    "flexible_code_interpreter_node", "skill_code_interpreter_node",                      # SandboxMode.ALWAYS
    "data_analysis_agent_node", "xlsx_chat_node", "docx_chat_node", "pptx_chat_node",     # E2BSkillsChatService
    "ey_pptx_chat_node", "d3_data_visualization_node", "network_graph_output_node",
}
GOC_KHI_CO_FILE: set[str] = {"agent_node", "mcp_agent_node", "agentic_deep_research_node"}   # SandboxMode.IF_FILES (mặc định họ Agent)
# Nguồn CHẮC CHẮN không mang file xuống khối sau. Loại KHÔNG có ở đây và không đi-qua ⇒ coi là CÓ THỂ mang file (mặc định nghi — cùng
# tinh thần danh sách cho phép: loại mới / lạ thì nghi).
KHONG_FILE: set[str] = {"text_input_node", "sticky_note_node",
                        "deep_research_node"}   # BaseNode, ra NodeOutput(data=str) không files (AB nodes/deep_research/node.py:31, 329-340) [code];
                                                # thiếu ⇒ chặn nhầm 1 mẫu (thử 09/11)
# Khối chuyển tiếp: mang file chỉ khi khối trước nó mang file [suy luận — chưa đọc hết thân từng khối; nghi thì để ngoài nhóm này].
DI_QUA: set[str] = {"human_intervention_node", "guardrails_node", "text_redaction_node", "text_output_node", "markdown_output_node"}
BIET: set[str] = (GOC_LUON | GOC_KHI_CO_FILE | KHONG_FILE | DI_QUA | CHO_PHEP | CHO_PHEP_CO_DIEU_KIEN
                   | {"vercel_deploy_agent_node", "file_input_node"})
# Ghi chú [suy luận]: Agent KHÔNG mở hộp thì không phát NodeFile — file ra chỉ từ hộp (_persist_sandbox_files, AB workflow/node/base/
# agent/node.py:1026) và vòng phản hồi đầu ra trong nhánh có hộp; công cụ xuất file của mình ghi thư mục, không ra NodeFile.


def _on(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "on"))


def _params(node) -> dict:
    return {p.field_id: p.value for p in (node.params or [])}


def _khoa(node) -> str:
    return node.react_flow_generated_id or node.node_id


def _cong_cu_ra_ngoai(node, vao_tu: list) -> list[str]:
    """Lý do một khối Agent KHÔNG sạch: bật công cụ ngoài danh sách, có máy chủ MCP gắn vào, hoặc có khối công cụ nối vào."""
    p = _params(node)
    ly_do = [f for f, v in p.items() if f.startswith("tool_") and f not in CONG_CU_DI_KEM
             and f not in ("tool_run_python", "tool_ma_file", "tool_customer_id") and _on(v)]
    if p.get("mcp_servers"):
        ly_do.append("mcp_servers")
    if p.get("tools"):
        ly_do.append("tools (khối công cụ)")
    ly_do += [f"khối công cụ {n.node_id}" for n in vao_tu if (n.node_type or "").lower() == "tool"]
    return ly_do


def _co_mcp(v) -> bool:
    """[mở hẹp] Ô mcp_servers có máy chủ nào không. Dạng thật: {"mcp_servers": [...]} / [...] / chuỗi JSON. Lạ / không đọc được ⇒ True (nghi)."""
    if v in (None, "", [], {}):
        return False
    if isinstance(v, str):
        import json
        try:
            v = json.loads(v)
        except ValueError:
            return True
        return _co_mcp(v)
    if isinstance(v, dict):
        if set(v) - {"mcp_servers"}:
            return True
        return _co_mcp(v.get("mcp_servers"))
    if isinstance(v, list):
        return len(v) > 0
    return True


def _hop_phien_bat() -> bool:
    """[E R7] CÙNG cách đọc với e2b_chat_service._hop_phien_bat (sua_doi/services/pydantic_ai_skills/e2b_chat_service.py:100-101): cờ tắt ⇒
    5 khối chạy E2B CÓ MẠNG (Capitol gốc) ⇒ mở hẹp KHÔNG áp dụng."""
    import os
    return os.environ.get("CHAY_THU_HOP_PHIEN", "true").strip().lower() not in ("false", "0", "no", "off")


def _khoi_skills_ra_ngoai(node, vao_tu: list) -> list[str]:
    """[mở hẹp] Lý do một khối CHO_PHEP_KHI_SACH KHÔNG sạch: hộp phiên tắt, ô mcp_servers có máy chủ, ô tools có giá trị, ô web_search bật,
    hoặc khối công cụ nối vào."""
    p = _params(node)
    ly_do = [] if _hop_phien_bat() else ["CHAY_THU_HOP_PHIEN tắt (khối chạy E2B có mạng)"]
    if _on(p.get("web_search")):   # [E minor 1] data_analysis có ô web_search ẩn (params.py:84) — chưa code nào đọc, phòng sau này
        ly_do.append("web_search")
    if _co_mcp(p.get("mcp_servers")):
        ly_do.append("mcp_servers")
    if p.get("tools"):
        ly_do.append("tools (khối công cụ)")
    ly_do += [f"khối công cụ {n.node_id}" for n in vao_tu if (n.node_type or "").lower() == "tool"]
    return ly_do


def _khoi_dieu_kien_ra_ngoai(node, vao_tu: list) -> list[str]:
    """[vòng 3] Lý do một khối CHO_PHEP_CO_DIEU_KIEN KHÔNG được đứng sau gốc. Khối công cụ nối vào ⇒ nghi (hai loại này không đọc công cụ —
    có dây công cụ là payload lạ)."""
    import os
    ly_do = [f"khối công cụ {n.node_id}" for n in vao_tu if (n.node_type or "").lower() == "tool"]
    if node.node_id == "evals_node" and os.environ.get("CHAY_THU_EVAL_CHI_PASS_FAIL", "1") == "0":   # CÙNG cách đọc với platform/evals.py:508
        ly_do.append("CHAY_THU_EVAL_CHI_PASS_FAIL tắt (bài chấm v2 có vòng agent / tra web được gửi)")
    return ly_do


def _vao_cong_tools(e) -> bool:
    """Dây vào cổng (Tools), không vào ô cấu hình: edge_type PORT_TO_PORT + không target_param_id (AB api/routers/models/workflow.py:264-311).
    Thiếu trường (payload lạ) ⇒ False (nghi)."""
    loai = getattr(getattr(e, "edge_type", None), "value", getattr(e, "edge_type", None))
    return str(loai or "").lower() == "port_to_port" and not getattr(e, "target_param_id", None)


def _goc(nodes: dict, truoc_day: dict) -> dict[str, str]:
    """{khoá khối: lý do là gốc}. Gốc = bật tool_run_python, HOẶC loại luôn mở hộp, HOẶC họ Agent có khối trước có thể mang file."""
    nho: dict[str, bool] = {}

    def co_file(k: str, dang_xet: frozenset = frozenset()) -> bool:
        """Khối k có thể phát file cho khối sau không (đệ quy theo dây; vòng ⇒ nghi = True)."""
        if k in nho:
            return nho[k]
        if k in dang_xet:
            return True
        n, ds = nodes[k], dang_xet | {k}
        loai = n.node_id
        if loai in GOC_LUON or _on(_params(n).get("tool_run_python")):
            kq = True
        elif loai in GOC_KHI_CO_FILE:
            kq = any(qua_day(a, e, ds) for a, e in truoc_day.get(k, []))
        elif loai in KHONG_FILE:
            kq = False
        elif loai in DI_QUA:
            kq = any(qua_day(a, e, ds) for a, e in truoc_day.get(k, []))
        elif loai == "vercel_deploy_agent_node":
            kq = False   # SandboxMode.NEVER (nodes/agent/vercel_deploy/node.py:188) — vẫn không thuộc CHO_PHEP nên không được đứng sau gốc
        else:
            kq = True    # file_input_node, file_browser, pdf_redaction, khối lạ … ⇒ nghi
        nho[k] = kq
        return kq

    def qua_day(a, e, dang_xet: frozenset = frozenset()) -> bool:
        """Khối a có đưa file theo DÂY e không. [D m1, 09/11] khối công cụ (node_type = tool: EXA, SerpAPI…) chỉ "không mang file" khi
        nối vào cổng Tools (dây port_to_port, không ô đích) — Agent chỉ lấy file từ ô cấu hình có dây (_raw_bound_outputs(field_id),
        AB sandbox_runtime.py:242-262). Nối vào Ô cấu hình thì xét như khối thường (nghi). Thiếu luật này ⇒ chặn nhầm 42 / 58 mẫu (thử 09/11)."""
        if (a.node_type or "").lower() == "tool" and _vao_cong_tools(e):
            return False
        return co_file(_khoa(a), dang_xet)

    goc: dict[str, str] = {}
    for k, n in nodes.items():
        if _on(_params(n).get("tool_run_python")):
            goc[k] = "bật chạy code (tool_run_python)"
        elif n.node_id in GOC_LUON:
            goc[k] = f"loại {n.node_id} luôn mở hộp chạy code (E2B)"
        elif n.node_id in GOC_KHI_CO_FILE or (n.node_id not in BIET and (n.node_type or "").lower() != "tool"):
            # [D m2, 09/11] loại LẠ (không có trong các nhóm đã đọc code) mà nhận file ⇒ cũng là gốc (có thể xử lý file bằng code / AI)
            nguon = [a for a, e in truoc_day.get(k, []) if qua_day(a, e)]
            if nguon:
                ten = nguon[0].display_name or nguon[0].name or nguon[0].node_id
                goc[k] = (f"loại {n.node_id} nhận file từ '{ten}' ⇒ Capitol mở hộp chạy code (E2B)" if n.node_id in GOC_KHI_CO_FILE
                          else f"loại lạ {n.node_id} nhận file từ '{ten}' ⇒ coi như chạy code (chưa đọc code loại này)")
    return goc


def kiem_r12(workflow_payload) -> None:
    nodes = {_khoa(n): n for n in workflow_payload.nodes}

    def tim(nid):
        return nodes.get(nid) or workflow_payload.get_node_by_id(nid)

    sau: dict[str, set[str]] = {}
    truoc: dict[str, list] = {}
    truoc_day: dict[str, list] = {}   # [m1] (khối trước, dây) — luật "khối công cụ không mang file" xét theo DÂY
    dau_la: list[str] = []
    for e in workflow_payload.edges:
        a, b = tim(e.source_node_id or e.source), tim(e.target_node_id or e.target)
        if a is None or b is None:
            # D kiểm S4 (m1): dây trỏ mã khối lạ từng bị bỏ qua im lặng ⇒ A run_python ⇒ WordPress qua dây mã lạ vẫn QUA. Không biết khối
            # là gì thì không chứng minh được an toàn ⇒ từ chối (chỉ khi workflow có khối chạy code — kiểm ở dưới).
            dau_la.append(f"dây {e.id} trỏ tới khối không có trong sơ đồ")
            continue
        sau.setdefault(_khoa(a), set()).add(_khoa(b))
        truoc.setdefault(_khoa(b), []).append(a)
        truoc_day.setdefault(_khoa(b), []).append((a, e))

    goc = _goc(nodes, truoc_day)
    if not goc:
        return
    loi = list(dau_la)
    for k in goc:   # chính khối chạy code: không có công cụ ra ngoài (lớp này báo SỚM; lớp lúc dựng agent ở node.py vẫn giữ)
        ly_do = _cong_cu_ra_ngoai(nodes[k], truoc.get(k, []))
        if ly_do:
            loi.append(f"khối '{nodes[k].display_name or nodes[k].name}' ({goc[k]}) có thêm: {', '.join(ly_do)}")
    # [bản 2, 09/11 — B tự bắt bằng R2 / R18 / R20] duyệt riêng tập "đã thăm"; KHÔNG khởi tạo bằng tập gốc — khối đứng SAU một gốc phải qua
    # kiểm danh sách cho phép KỂ CẢ khi chính nó cũng là gốc (vd data_analysis ⇒ xlsx_chat: xlsx_chat mở E2B có mạng với dữ liệu của khối trước).
    da, hang = set(), list(goc)
    while hang:
        for k2 in sau.get(hang.pop(), ()):
            if k2 in da:
                continue
            da.add(k2)
            hang.append(k2)
            n = nodes[k2]
            ten = n.display_name or n.name or n.node_id
            if n.node_id in CHO_PHEP_KHI_SACH:   # [mở hẹp 16/11] chỉ khi Ô sạch
                ly_do = _khoi_skills_ra_ngoai(n, truoc.get(k2, []))
                if ly_do:
                    loi.append(f"khối '{ten}' (loại {n.node_id}) đứng sau khối chạy code có đường ra ngoài: {', '.join(ly_do)}")
            elif n.node_id in CHO_PHEP_CO_DIEU_KIEN:   # [vòng 3 16/11]
                ly_do = _khoi_dieu_kien_ra_ngoai(n, truoc.get(k2, []))
                if ly_do:
                    loi.append(f"khối '{ten}' (loại {n.node_id}) đứng sau khối chạy code có đường ra ngoài: {', '.join(ly_do)}")
            elif n.node_id not in CHO_PHEP:
                loi.append(f"khối '{ten}' (loại {n.node_id}) đứng sau khối chạy code mà không thuộc danh sách cho phép")
            elif n.node_id in LOAI_AGENT:
                ly_do = _cong_cu_ra_ngoai(n, truoc.get(k2, []))
                if ly_do:
                    loi.append(f"khối Agent '{ten}' đứng sau khối chạy code có công cụ ra ngoài: {', '.join(ly_do)}")
    if loi:
        logger.warning("[R12] tu_choi workflow=%s goc=%s ly_do=%s", getattr(workflow_payload, "id", "?"), list(goc.values()), loi)
        raise ValueError("Workflow bị từ chối (sandbox R12 — dữ liệu từ khối chạy code không được đi ra ngoài): " + "; ".join(loi)
                         + ". Cần tra web thì đặt khối tra web TRƯỚC khối chạy code.")
    logger.info("[R12] dat workflow=%s khoi_chay_code=%s khoi_sau=%s", getattr(workflow_payload, "id", "?"), len(goc), len(da))
