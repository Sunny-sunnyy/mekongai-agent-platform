"""Bộ chuyển đổi DUY NHẤT giữa hình dữ liệu chung (khung vẽ) và payload Capitol + lớp kiểm của trang.

Viết 04/11/2026 (nhóm phát triển). Hình dữ liệu chung: mekongai-template `tài liệu thiết kế nội bộ` mục 8.
  Hình chung v1 (yêu cầu vận hành 09/11 hai bên; sửa 14:54 09/11 — điểm 1, 2, 3, 6 của mekongai-template
  `tài liệu thiết kế nội bộ`) — đây là hình TRANG GỬI / NHẬN:
  workflow = {id?, name, description, nodes: [{id, type, params?, position?}],                 ← khối đúng 4 trường
              edges: [{from_node, from_port, to_node, to_port}],                             ← dây đúng 4 khoá, from_port BẮT BUỘC
              ui?: {labels: {mã khối: tên hiển thị}}}                                       ← phần riêng của trang
  Bên trong máy chủ vẫn dùng "hình trong" (khối có `label`, dây có `id`) — `tu_chung` / `sang_chung` đổi qua lại ở ranh giới,
  vì `label` là tên khối lưu vào Capitol (`sang_capitol`) và các script cũ (buoc19, chuyen_mau_capitol) đọc nó.
Capitol cần dây ghi HAI nơi (danh sách edges + `bindable_config.incoming_connections` / `is_bound` của ô đích) — lệch hai nơi là lỗi
đã gặp 03/11 (lớp kiểm của máy chủ dựng chặn). Vì vậy chỉ hàm `sang_capitol` sinh cả hai, không nơi nào sửa tay.

Bản 1 chỉ hỗ trợ dây kiểu `port_to_param` (cổng ra ⇒ ô nhận dây). Dây `port_to_port` (cổng Tools của khối AI) đọc được nhưng chưa vẽ / sửa —
khối công cụ ngoài (Composio) đang chặn có chủ ý.
"""

import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from viet_hoa import viet_hoa_khoi  # noqa: E402  — tên / mô tả / ô tiếng Việt (người dùng yêu cầu 04/11)

THU_MUC = Path(__file__).resolve().parent
_RUNTIME_DIR = Path(os.environ.get("CAPITOL_RUNTIME_DIR") or (THU_MUC.parent / "runtime")).expanduser()
DANH_MUC_CU = THU_MUC / "b4_list_nodes.json" if (THU_MUC / "b4_list_nodes.json").is_file() else (_RUNTIME_DIR / "b4_list_nodes.json")
DANH_MUC_SONG = _RUNTIME_DIR / "giao_dien" / "danh_muc.json"
DANH_MUC_FILE = DANH_MUC_SONG if DANH_MUC_SONG.is_file() else DANH_MUC_CU

# ---- khối chạy được trên máy này: CHỈ ghi điều đã chạy thật hoặc đã biết chắc lý do -------------------------------------------
DA_CHAY = {"text_input_node", "agent_node", "human_intervention_node", "text_output_node",
           "markdown_output_node", "text_redaction_node",   # [đã chạy] 03–04/11
           # Các khối chính đã kiểm chạy thật:
           "guardrails_node", "evals_node", "agentic_deep_research_node", "file_input_node", "file_browser_output_node",
           "pdf_redaction_node", "data_analysis_agent_node", "xlsx_chat_node", "flexible_code_interpreter_node",
           "skill_code_interpreter_node", "pptx_chat_node", "docx_chat_node", "d3_data_visualization_node",
           "network_graph_output_node",
           "text_to_speech_output_node",
           "vercel_deploy_agent_node"}   # 10/11 15:4x — Đăng trang bản thay: lên kho file nội bộ, CSP, 0 ra Internet (C, D ĐẠT)   # 10/11 15:29 — đọc giọng bản thay OpenAI tts-1 (C, D ĐẠT), T1 SUCCESS
DA_CHAY.add("collections_node")   # [ghi chú kỹ thuật] nhóm phát triển xác nhận (a) của E 17/11: hỏi kho bảng (ô query_text + datasets, cổng ra results); lượt thật đầu = E mục (10)
# người dùng 10/11: "ở phần 'thêm khối' chỉ hiển thị các khối mà chúng ta đã xây dựng được, những khối nào chưa xây dựng được thì đừng đưa vào"
# ⇒ danh mục gửi trang chỉ gồm DA_CHAY. Khối dựng xong thì THÊM vào DA_CHAY (sau khi đã chạy thật) rồi làm mới danh mục.
CHI_HIEN_DA_CHAY = True
KHONG_CHAY = {
    # mã khối đúng theo danh mục thật (04/11 sửa: trước ghi nhầm composio_tool_node / evaluations_node)
    "EXECUTE_COMPOSIO_TOOL": "công cụ ngoài Composio — đang chặn có chủ ý (compose.yaml extra_hosts, 03/11)",
    "evals_node": "cần dịch vụ chấm eval-service — chưa dựng trên máy này",
    "flexible_code_interpreter_node": "cần máy ảo E2B — máy này không có E2B_API_KEY",
    "skill_code_interpreter_node": "cần máy ảo E2B — máy này không có E2B_API_KEY",
    "data_analysis_agent_node": "cần máy ảo E2B — máy này không có E2B_API_KEY",
    "vercel_deploy_agent_node": "cần tài khoản Vercel",
}
# Mô hình: chỉ gpt-6-luna đã chạy thật (khoá OpenAI của người dùng + bản vá bỏ temperature, sua_doi/). Claude: máy không có khoá Anthropic.
# Tên lạ KHÔNG được để lọt: code Capitol tự đổi sang gpt-5.2 (đắt) hoặc Claude (model_resolution.py:205-242).
# [ghi chú kỹ thuật] khớp luật trong hộp (kiem_sau_ghep.MO_HINH_O_MODEL của B): Claude Sonnet / Opus 5.5 qua Bedrock + luna (còn giữ cho khối KPI khoá).
MO_HINH_DUOC = ["claude-sonnet-5-5", "claude-opus-5-5", "gpt-6-luna"]
MO_HINH_MAC_DINH = "claude-sonnet-5-5"
# [ghi chú kỹ thuật] loại khối CHƯA chuyển sang Claude (muc_toi_da chưa có mức cho ô model Claude ở các loại này ⇒ trang sẽ chặn chạy) ⇒ ô model mặc định còn luna.
# Bỏ một dòng ở đây khi đã chuyển xong loại khối đó.
MO_HINH_MAC_DINH_THEO_KHOI = {}   # [ghi chú kỹ thuật] trống từ khi gói va_skill_dr_claude của B gắn (skill chạy code + tra cứu sâu đã chạy Claude theo file mô hình khối)

NHOM_VIEC = {"input": "Đầu vào", "agent": "AI", "governance": "Kiểm soát", "output": "Đầu ra", "tool": "Công cụ"}


def _true(v) -> bool:
    return v is True or (isinstance(v, str) and v.lower() == "true")


def _tim_khoi(x, node_id):
    if isinstance(x, dict):
        if x.get("node_id") == node_id and "params" in x:
            return x
        for v in x.values():
            r = _tim_khoi(v, node_id)
            if r:
                return r
    elif isinstance(x, list):
        for v in x:
            r = _tim_khoi(v, node_id)
            if r:
                return r
    return None


def _tat_ca_khoi(x, ra):
    if isinstance(x, dict):
        if "node_id" in x and "params" in x:
            ra.append(x)
            return
        for v in x.values():
            _tat_ca_khoi(v, ra)
    elif isinstance(x, list):
        for v in x:
            _tat_ca_khoi(v, ra)


class DanhMuc:
    """Danh mục khối thật (struct đầy đủ của Capitol) + bản rút gọn theo hình dữ liệu chung cho khung vẽ."""

    def __init__(self, file: Path = DANH_MUC_FILE):
        goc = json.loads(file.read_text())
        ds: list = []
        _tat_ca_khoi(goc, ds)
        # Ẩn khối công cụ của nhà cung cấp dữ liệu ngoài (Carbon Arc… — 18 / 19 công cụ trong danh mục trực tiếp 04/11): cần tài khoản bên
        # ngoài, máy này không chạy được, lại không có bản dịch. Công cụ dùng được nằm trong khối Agent (vá 8: công cụ gọi thẳng).
        self.an = [k["node_id"] for k in ds if k.get("node_type") == "tool" and k["node_id"] != "EXECUTE_COMPOSIO_TOOL"]
        self.struct = {k["node_id"]: k for k in ds if k["node_id"] not in self.an}

    def mot(self, loai: str) -> dict | None:
        return self.struct.get(loai)

    def chung(self) -> list[dict]:
        """GET /api/blocks — hình dữ liệu chung mục 8.1."""
        ra = []
        for loai, s in self.struct.items():
            if CHI_HIEN_DA_CHAY and loai not in DA_CHAY:
                continue   # khối chưa hỗ trợ ⇒ không đưa vào "Thêm khối"
            params, inputs = [], []
            for p in s.get("params", []):
                bc = p.get("bindable_config")
                # D kiểm 04/11 (BLOCKER): ô ẩn khỏi form (show=False) VẪN có thể là ô nhận dây bắt buộc — vd preview_context của khối duyệt,
                # text_content của khối xuất chữ. Ẩn ⇒ chỉ bỏ khỏi form, KHÔNG bỏ khỏi danh sách cổng vào.
                an = _true(p.get("hidden")) or p.get("show") in (False, "False")
                if an and not bc:
                    continue
                if loai == "agent_node" and p["field_id"] == "files":
                    continue   # D kiểm 04/11: cổng ẩn, chỉ nhận file, đưa file vào máy ảo E2B — máy này không có E2B_API_KEY ⇒ chưa dùng được
                o = {"name": p["field_id"], "label": p.get("display_name") or p["field_id"], "kind": p.get("display_modal"),
                     "required": _true(p.get("required")), "default": p.get("default"), "info": p.get("info") or "",
                     "advanced": _true(p.get("advanced")), "bindable": bool(bc), "placeholder": p.get("placeholder") or ""}
                if p.get("options"):
                    o["choices"] = list(p["options"])
                if p["field_id"] == "model" and o.get("choices"):
                    o["choices"] = [{"value": m, "ok": m in MO_HINH_DUOC,
                                     "why": "" if m in MO_HINH_DUOC else ("chưa có trong danh sách mô hình cho phép" if m.startswith("claude")
                                                                         else "chưa chạy thử trên máy này")}
                                    for m in (o.get("choices") or [])]
                    o["default"] = MO_HINH_MAC_DINH_THEO_KHOI.get(loai, MO_HINH_MAC_DINH)
                if not an:
                    params.append(o)
                if bc:
                    ip = bc.get("input_port") or {}
                    inputs.append({"name": p["field_id"], "label": o["label"], "required": o["required"],
                                   "accepts": (ip.get("port_type") or {}).get("accepted_types") or [], "many": ip.get("cardinality") == "many"})
            outputs = [{"name": op.get("name"), "label": op.get("display_name") or op.get("name"), "id": op.get("id"),
                        "kind": (op.get("port_type") or {}).get("accepted_types") or []} for op in s.get("output_ports", [])]
            chay = loai in DA_CHAY
            ra.append(viet_hoa_khoi({"type": loai, "label": s.get("display_name"), "description": (s.get("description") or "")[:300],
                       "group": NHOM_VIEC.get(s.get("node_type"), s.get("node_type")), "group_key": s.get("node_type"),
                       "icon": s.get("icon"), "inputs": inputs, "outputs": outputs, "params": params,
                       "runnable_here": chay, "tested_here": chay,
                       "not_runnable_reason": KHONG_CHAY.get(loai) or ("" if chay else "chưa thử trên máy này — có thể cần dịch vụ chưa dựng")}))
        thu_tu = {"input": 0, "agent": 1, "governance": 2, "output": 3, "tool": 4}
        return sorted(ra, key=lambda k: (thu_tu.get(k["group_key"], 9), not k["runnable_here"], k["label"] or ""))


def _o_dich(struct: dict, field: str) -> dict | None:
    return next((p for p in struct.get("params", []) if p.get("field_id") == field), None)


def _cong_ra(struct: dict, ten: str | None) -> dict | None:
    # Hình chung v1 điểm 3: from_port bắt buộc — bỏ cách cũ "rỗng = cổng ra đầu tiên" (khối nhiều cổng ra thì đó là đoán).
    return next((o for o in struct.get("output_ports") or [] if o.get("name") == ten), None) if ten else None


# ============================== HÌNH CHUNG v1 ⇄ HÌNH TRONG ==============================
KHOA_KHOI = {"id", "type", "params", "position"}
KHOA_DAY = {"from_node", "from_port", "to_node", "to_port"}


def tu_chung(wf: dict) -> tuple[dict, list[dict]]:
    """Hình chung (trang gửi) ⇒ hình trong. Trường lạ ở khối / dây ⇒ báo lỗi (không lặng lẽ bỏ) — điểm 1, 2."""
    if not isinstance(wf, dict):
        raise ValueError("workflow phải là đối tượng")   # 400, không 503 (đối chiếu kỹ thuật)
    ui = wf.get("ui") or {}
    nhan = ui.get("labels") or {} if isinstance(ui, dict) else None
    if not isinstance(nhan, dict) or not all(isinstance(v, str) for v in nhan.values()):
        raise ValueError("ui.labels phải là {mã khối: tên}")
    loi = []
    nodes, edges = [], []
    for n in wf.get("nodes") or []:
        if not isinstance(n, dict):
            raise ValueError("khối phải là đối tượng {id, type, params, position}")   # máy chủ trả 400, không 503 (đối chiếu kỹ thuật)
        thieu = [k for k in ("id", "type") if not n.get(k)]
        if thieu:
            loi.append({"muc": "loi", "code": "thieu_truong_khoi", "node": n.get("id"), "field": None,
                        "message": f"Khối thiếu trường bắt buộc: {', '.join(thieu)}.", "suggestion": "Khối phải có id và type."})
        la = sorted(set(n) - KHOA_KHOI)
        if la:
            loi.append({"muc": "loi", "code": "truong_la_khoi", "node": n.get("id"), "field": None,
                        "message": f"Khối “{n.get('id')}” có trường ngoài hình chung: {', '.join(la)}.",
                        "suggestion": "Khối chỉ có id, type, params, position; tên hiển thị để ở ui.labels."})
        nodes.append({**{k: n[k] for k in KHOA_KHOI if k in n}, "label": nhan.get(n.get("id")) or ""})
    for e in wf.get("edges") or []:
        if not isinstance(e, dict):
            raise ValueError("dây phải là đối tượng {from_node, from_port, to_node, to_port}")
        la = sorted(set(e) - KHOA_DAY)
        if la:
            loi.append({"muc": "loi", "code": "truong_la_day", "node": e.get("from_node"), "field": None,
                        "message": f"Dây có trường ngoài hình chung: {', '.join(la)}.", "suggestion": "Dây chỉ có 4 khoá from_node, from_port, to_node, to_port."})
        edges.append({**{k: e.get(k) for k in KHOA_DAY}, "id": f"e-{e.get('from_node')}-{e.get('from_port')}-{e.get('to_node')}-{e.get('to_port')}"})
    trong = {k: v for k, v in wf.items() if k not in ("nodes", "edges", "ui")}
    return {**trong, "nodes": nodes, "edges": edges}, loi


def dien_cong_ra(hinh: dict, dm: "DanhMuc") -> dict:
    """Script cũ (hình trong, dây chưa ghi from_port): điền cổng ra CHỈ khi khối nguồn có đúng 1 cổng ra — cùng luật với trang; còn lại để trống ⇒ kiem báo."""
    loai = {n["id"]: n.get("type") for n in hinh.get("nodes") or []}
    edges = []
    for e in hinh.get("edges") or []:
        if not e.get("from_port"):
            ops = ((dm.mot(loai.get(e.get("from_node"))) or {}).get("output_ports") or [])
            e = {**e, "from_port": ops[0]["name"] if len(ops) == 1 else e.get("from_port")}
        edges.append(e)
    return {**hinh, "edges": edges}


def _ten(n: dict) -> str:
    return n.get("label") or n.get("id") or "?"


def sang_chung(hinh: dict) -> dict:
    """Hình trong ⇒ hình chung (trang nhận). `label` chuyển vào ui.labels, `id` dây bỏ (trang tự sinh lại)."""
    ngoai = {k: v for k, v in hinh.items() if k not in ("nodes", "edges")}
    return {**ngoai,
            "nodes": [{k: n[k] for k in KHOA_KHOI if k in n} for n in hinh.get("nodes") or []],
            "edges": [{k: e.get(k) for k in KHOA_DAY} for e in hinh.get("edges") or []],
            "ui": {"labels": {n["id"]: n.get("label") or "" for n in hinh.get("nodes") or []}}}


# ============================== LỚP KIỂM CỦA TRANG ==============================
def kiem(wf: dict, dm: DanhMuc) -> list[dict]:
    """Trả danh sách lỗi / cảnh báo {muc: loi|canh_bao, code, node, field, message, suggestion}. Lỗi ⇒ không cho lưu."""
    ra = []

    def bao(muc, code, msg, goi_y="", node=None, field=None):
        ra.append({"muc": muc, "code": code, "node": node, "field": field, "message": msg, "suggestion": goi_y})

    nodes = wf.get("nodes") or []
    edges = wf.get("edges") or []
    if not (wf.get("name") or "").strip():
        bao("loi", "thieu_ten", "Workflow chưa có tên.", "Gõ tên ở thanh trên.")
    if not nodes:
        bao("loi", "trong", "Khung vẽ chưa có khối nào.", "Kéo một khối từ danh mục bên trái vào.")
    ids = [n.get("id") for n in nodes]
    if len(ids) != len(set(ids)):
        bao("loi", "trung_ma", "Có hai khối trùng mã.")
    theo_id = {n["id"]: n for n in nodes if n.get("id")}
    vao_o = {}   # (node, field) ⇒ số dây
    for e in edges:
        a, b = theo_id.get(e.get("from_node")), theo_id.get(e.get("to_node"))
        if not a or not b:
            bao("loi", "day_mo_coi", "Có dây nối tới khối không còn trên khung vẽ.", "Xoá dây đó.")
            continue
        if a["id"] == b["id"]:
            bao("loi", "tu_noi", f"Khối “{_ten(a)}” tự nối vào chính nó.", "Xoá dây đó.", node=a["id"])
            continue
        sb = dm.mot(b.get("type")) or {}
        o = _o_dich(sb, e.get("to_port"))
        if not o:
            bao("loi", "o_khong_co", f"Khối “{_ten(b)}” không có ô “{e.get('to_port')}”.", node=b["id"], field=e.get("to_port"))
        elif not o.get("bindable_config"):
            bao("loi", "o_khong_nhan_day", f"Ô “{o.get('display_name')}” của “{_ten(b)}” không nhận dây — chỉ gõ tay được.",
                "Nối vào ô có chấm tròn bên trái khối.", node=b["id"], field=e.get("to_port"))
        sa = dm.mot(a.get("type")) or {}
        if not e.get("from_port"):
            bao("loi", "thieu_cong_ra", f"Dây từ khối “{_ten(a)}” chưa ghi cổng ra.", "Nối lại dây từ đúng chấm cổng ra.", node=a["id"])
        elif not _cong_ra(sa, e.get("from_port")):
            bao("loi", "cong_ra_khong_co", f"Khối “{_ten(a)}” không có cổng ra “{e.get('from_port')}”.", node=a["id"])
        k = (b["id"], e.get("to_port"))
        vao_o[k] = vao_o.get(k, 0) + 1
    # vòng: Capitol chạy theo tầng — có vòng thì không xếp được tầng (máy chủ dựng của họ KHÔNG có luật này — 03/11)
    ke = {i: [] for i in theo_id}
    for e in edges:
        if e.get("from_node") in ke and e.get("to_node") in theo_id:
            ke[e["from_node"]].append(e["to_node"])
    mau = {}

    def dfs(u):
        mau[u] = 1
        for v in ke[u]:
            if mau.get(v) == 1 or (mau.get(v) is None and dfs(v)):
                return True
        mau[u] = 2
        return False
    if any(mau.get(i) is None and dfs(i) for i in theo_id):
        bao("loi", "vong", "Các dây tạo thành vòng — workflow không biết khối nào chạy trước.", "Bỏ một dây trong vòng.")
    for n in nodes:
        s = dm.mot(n.get("type"))
        if not s:
            bao("loi", "khoi_la", f"Loại khối “{n.get('type')}” không có trong danh mục.", node=n.get("id"))
            continue
        pr = n.get("params") or {}
        for p in s.get("params", []):
            f = p["field_id"]
            if f not in pr and not _true(p.get("required")):
                continue
            v = pr.get(f, p.get("value"))
            if v in (None, "") and p.get("default") not in (None, ""):
                v = p.get("default")          # D kiểm 04/11 (MAJOR): Capitol tự dùng mặc định khi chạy ⇒ không báo "bắt buộc mà trống"
            trong = v is None or (isinstance(v, str) and not v.strip())
            if _true(p.get("required")) and trong and vao_o.get((n["id"], f), 0) == 0 and not _true(p.get("hidden")):
                bao("loi", "o_bat_buoc_trong", f"“{_ten(n)}”: ô “{p.get('display_name')}” bắt buộc mà đang trống.",
                    "Gõ giá trị, hoặc nối một dây vào ô này.", node=n["id"], field=f)
        for f in pr:
            if not _o_dich(s, f):
                bao("loi", "o_la", f"“{_ten(n)}”: ô “{f}” không có trong khai báo khối.", node=n["id"], field=f)
        if (pr.get("output_schema") or "").strip() if isinstance(pr.get("output_schema"), str) else pr.get("output_schema"):
            try:
                sc = json.loads(pr["output_schema"]) if isinstance(pr["output_schema"], str) else pr["output_schema"]
                if not isinstance(sc, dict) or not (sc.get("properties") or {k: v for k, v in sc.items() if k not in ("type", "required")}):
                    raise ValueError("phải là object JSON có ít nhất một trường")
            except ValueError as e:
                bao("loi", "khuon_dau_ra_sai", f"“{_ten(n)}”: khuôn đầu ra không hợp lệ — {e}.",
                    'Ví dụ đúng: {"loai": ["doi_hang", "hoi_gia"], "tom_tat": "string"}', node=n["id"], field="output_schema")
        if n.get("type") == "agent_node":
            m = pr.get("model") or MO_HINH_MAC_DINH
            if m not in MO_HINH_DUOC:
                bao("loi", "mo_hinh_khong_duoc", f"“{_ten(n)}”: mô hình “{m}” không chạy được trên máy này.",
                    f"Chọn {', '.join(MO_HINH_DUOC)}. Tên lạ còn nguy hiểm hơn: Capitol tự đổi sang mô hình khác (có khi đắt hơn).",
                    node=n["id"], field="model")
        if n.get("type") not in DA_CHAY:
            bao("canh_bao", "chua_thu", f"“{_ten(n)}”: {KHONG_CHAY.get(n.get('type')) or 'loại khối này chưa chạy thử trên máy — lượt chạy có thể lỗi'}.",
                node=n.get("id"))
    try:   # [ghi chú kỹ thuật] 2 cảnh báo từ lỗi thật của Capitol (nối chéo theo tên · khoá lệch) — chỉ cảnh báo, hỏng thì bỏ qua
        import kiem_day
        ra += kiem_day.canh_bao(nodes, edges)
    except Exception:
        pass
    return ra


# ============================== CHUNG ⇒ CAPITOL ==============================
def _day(src, sport, dst, dport, field):
    return {"id": f"e-{src}-{dst}-{field}", "source": src, "target": dst, "sourceNodeId": src, "targetNodeId": dst,
            "type": "default", "data": {}, "animated": False, "sourceHandle": sport, "targetHandle": dport,
            "edge_type": "port_to_param", "source_node_id": src, "source_port_id": sport,
            "target_node_id": dst, "target_port_id": dport, "target_param_id": field}


def sang_capitol(wf: dict, dm: DanhMuc, wf_id: str) -> dict:
    """Hình chung ⇒ payload lưu v1 của Capitol. Dây sinh HAI nơi từ cùng một danh sách `edges`."""
    nodes, struct_cua = [], {}
    for n in wf["nodes"]:
        s = copy.deepcopy(dm.mot(n["type"]))
        pr = n.get("params") or {}
        for p in s.get("params", []):
            if p["field_id"] in pr:
                p["value"] = pr[p["field_id"]]
            if p["field_id"] == "model" and n["type"] == "agent_node" and not pr.get("model"):
                p["value"] = MO_HINH_MAC_DINH                         # không để mặc định claude-sonnet-5 của danh mục lọt vào
            if p["field_id"] in ("temperature", "top_p") and n["type"] == "agent_node" and pr.get(p["field_id"]) in (None, ""):
                p["value"] = None                                     # bước 5 (03/11): mô hình suy nghĩ trả 400 nếu gửi temperature
        s.update(react_flow_generated_id=n["id"], name=n.get("label") or s.get("display_name"))
        struct_cua[n["id"]] = s
        pos = n.get("position") or {"x": 0, "y": 0}
        nodes.append({"id": n["id"], "type": "custom", "position": {"x": pos.get("x", 0), "y": pos.get("y", 0)}, "data": {"struct": s}})
    edges = []
    for e in wf.get("edges") or []:
        sa, sb = struct_cua[e["from_node"]], struct_cua[e["to_node"]]
        cra = _cong_ra(sa, e.get("from_port"))
        if not cra:   # đường trang đã qua `kiem`; script gọi thẳng mà thiếu / sai cổng ra thì dừng rõ ràng, không đoán cổng
            raise ValueError(f"dây {e['from_node']} ⇒ {e['to_node']}: cổng ra {e.get('from_port')!r} không có")
        o = _o_dich(sb, e["to_port"])
        ip = o["bindable_config"]["input_port"]
        if not o["bindable_config"].get("is_bound"):
            ip["incoming_connections"] = []                           # bỏ chỗ giữ chỗ {"node_id": "", "port_id": ""} của danh mục
        o["bindable_config"]["is_bound"] = True
        ip["incoming_connections"].append({"node_id": e["from_node"], "port_id": cra["id"]})   # nơi 1
        edges.append(_day(e["from_node"], cra["id"], e["to_node"], ip["id"], e["to_port"]))   # nơi 2
    return {"id": wf_id, "session_id": wf.get("session_id") or wf_id, "name": wf["name"], "description": wf.get("description") or "",
            "nodes": nodes, "edges": edges, "metadata": {}, "global_context": {},
            "publish_to_api": False, "publish_to_mcp": False, "publish_to_template": False}


# ============================== CAPITOL ⇒ CHUNG ==============================
def tu_capitol(payload: dict, dm: DanhMuc | None = None) -> dict:
    """Payload phiên bản Capitol ⇒ hình chung. Giá trị ô chỉ giữ ô người dùng đã đặt (khác rỗng)."""
    nodes, cong_ten = [], {}
    for n in payload.get("nodes", []):
        s = (n.get("data") or {}).get("struct") or {}
        pr = {}
        for p in s.get("params", []):
            v = p.get("value")
            if v not in (None, "", [], {}):
                pr[p["field_id"]] = v
        for op in s.get("output_ports") or []:
            cong_ten[(n["id"], op.get("id"))] = op.get("name")
        nodes.append({"id": n["id"], "type": s.get("node_id"), "label": s.get("name") or s.get("display_name"), "params": pr,
                      "position": n.get("position") or {"x": 0, "y": 0}})
    edges, khac = [], 0
    for e in payload.get("edges", []):
        if (e.get("edge_type") or "port_to_param") != "port_to_param":
            khac += 1
            continue
        src, dst = e.get("source_node_id") or e.get("source"), e.get("target_node_id") or e.get("target")
        sp = e.get("source_port_id") or e.get("sourceHandle")
        edges.append({"id": e.get("id") or f"e-{src}-{dst}", "from_node": src, "from_port": cong_ten.get((src, sp)) or sp,
                      "to_node": dst, "to_port": e.get("target_param_id")})
    return {"id": payload.get("id"), "name": payload.get("name"), "description": payload.get("description") or "",
            "nodes": nodes, "edges": edges, "unsupported_edges": khac}
