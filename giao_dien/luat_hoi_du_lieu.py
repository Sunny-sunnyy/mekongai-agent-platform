"""LUẬT NỐI KHỐI cho workflow "Hỏi dữ liệu bằng lời" — phía máy chủ trang (đối chiếu kỹ thuật). BẢN SOẠN: gắn thì chép sang chay_thu/giao_dien/.
Áp cho payload Capitol (nodes[].data.struct{node_id, name, params[field_id, value]}, edges[source, target, target_param_id]). Hàm THUẦN, không gọi gì.
Trả (lỗi CHẶN, cảnh báo) bằng lời thường:
  CẢNH BÁO (chưa chặn — A: "để người dùng quyết sau"): khối Agent bật công cụ sql_query mà câu trả lời tới được khối khác KHÔNG qua khối Rào chắn chế độ "sql".
  CHẶN (lỗ làm rào vô nghĩa — cùng lý do luật Đ-19 bên KPI): ô "câu hỏi" của khối Rào chắn chế độ "sql" có chữ gõ sẵn, hoặc nối từ khối không phải
  Ô nhập chữ, hoặc từ Ô nhập đứng SAU một khối Agent, hoặc nhiều hơn 1 dây. Vì: số có trong "câu hỏi" được coi là số người hỏi tự nêu ⇒ AI tự viết
  số vào đó là tự cấp nguồn cho mình.
Workflow không có Agent bật sql_query và không có Rào chắn chế độ "sql" ⇒ ([], []) (không phải loại này)."""
from __future__ import annotations

RAO, AGENT, O_NHAP = "guardrails_node", "agent_node", "text_input_node"


def _bat(v) -> bool:   # cùng luật workflow_kpi._bat / native_tools._on
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "on")) or (isinstance(v, int) and not isinstance(v, bool) and v == 1)


def _o(struct: dict, field: str) -> dict:
    return next((p for p in struct.get("params", []) if p.get("field_id") == field), {}) or {}


def kiem(nodes: list[dict], edges: list[dict]) -> tuple[list[str], list[str]]:
    s = {n["id"]: n["data"]["struct"] for n in nodes or []}
    loai = {k: v.get("node_id") for k, v in s.items()}
    ten = lambda k: str(s[k].get("name") or loai[k])   # noqa: E731
    agent_sql = [k for k in s if loai[k] == AGENT and _bat(_o(s[k], "tool_sql_query").get("value")) and not _bat(_o(s[k], "tool_kpi").get("value"))]
    rao_sql = [k for k in s if loai[k] == RAO and str(_o(s[k], "che_do_kiem").get("value") or "").strip().lower() == "sql"]
    if not agent_sql and not rao_sql:
        return [], []
    sau: dict = {}
    for e in edges or []:
        if e.get("source") in s and e.get("target") in s:
            sau.setdefault(e["source"], set()).add(e["target"])

    def toi_duoc(tu, bo=frozenset()):
        da, hang = set(), [tu]
        while hang:
            for b in sau.get(hang.pop(), ()):
                if b not in da and b not in bo:
                    da.add(b)
                    hang.append(b)
        return da
    loi, canh_bao = [], []
    bo = frozenset(rao_sql)
    for a in agent_sql:
        lot = sorted(ten(x) for x in toi_duoc(a, bo))
        if lot:
            canh_bao.append(f"Khối “{ten(a)}” trả lời bằng số lấy từ truy vấn dữ liệu, nhưng câu trả lời đi tới “{'”, “'.join(lot[:3])}” mà không qua khối "
                            "“Kiểm số theo truy vấn” — số AI viết chưa được so với kết quả truy vấn. Nên chèn khối kiểm vào giữa.")
    sau_agent = set().union(*(toi_duoc(a) for a in s if loai[a] == AGENT)) if any(loai[a] == AGENT for a in s) else set()
    for k in rao_sql:
        o = _o(s[k], "cau_hoi")
        v = o.get("value")
        if isinstance(v, list):
            v = "\n".join(str(x) for x in v)
        if isinstance(v, str) and v.strip():
            loi.append(f"Ô “câu hỏi” của khối “{ten(k)}” đang có chữ gõ sẵn — ô này chỉ được nối bằng dây từ khối Câu hỏi.")
        nguon = [e.get("source") for e in edges or [] if e.get("target") == k and "cau_hoi" in str(e.get("target_param_id") or e.get("target_port_id") or e.get("targetHandle") or "")]
        ket = (((o.get("bindable_config") or {}).get("input_port") or {}).get("incoming_connections")) or []
        nguon += [c.get("node_id") for c in ket if isinstance(c, dict) and c.get("node_id") and c.get("node_id") not in nguon]
        if len(nguon) > 1:
            loi.append(f"Ô “câu hỏi” của khối “{ten(k)}” đang nhận {len(nguon)} dây — chỉ được đúng 1 dây từ khối Câu hỏi.")
        for n in nguon:
            if n not in s or loai[n] != O_NHAP or n in sau_agent:
                loi.append(f"Ô “câu hỏi” của khối “{ten(k)}” phải nối từ khối Câu hỏi (ô nhập chữ đứng TRƯỚC trợ lý), không nối từ "
                           f"“{ten(n) if n in s else 'khối không có trong workflow'}” — nếu không, số AI viết sẽ được coi là số người hỏi nêu.")
    return loi, canh_bao
