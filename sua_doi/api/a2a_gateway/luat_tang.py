"""[ghi chú] Hàm THUẦN (không I/O). Dùng chung cho:
tài liệu kỹ thuật kiem_cau_truc, chay_thu/va_kiem_sau_ghep/kiem_sau_ghep.py, chay_thu/giao_dien/may_chu.py.

Vì sao: Capitol chạy SONG SONG mọi khối cùng tầng (agentic_workflow.py:1018-1022, 1382-1388; temporal/utils/toposort.py:83
topological_sort_leveled), còn người chạy phiên (sandbox/nguoi_chay_phien.py) chỉ mở 1 hộp mỗi lúc + giữ 1 yêu cầu chờ ⇒ ≥ 2 khối hộp phiên cùng
tầng ⇒ khối thứ 2 / 3 hỏng (dang_ban / khong_tra_loi) SAU KHI các khối trước đã gọi AI (lượt FFIEC a579e1d0, 16/11).

Tầng tính GIỐNG Capitol (đọc temporal/utils/node_exclusion.py + toposort.py bản release-4.10.0):
- khối tool / memory / annotation (data.struct.node_type) LUÔN bị loại khỏi lượt; khối evals_node bị loại khi lượt chạy với run_evals=False;
- dây chạm khối bị loại thì bỏ (toposort.py: chỉ dây có CẢ HAI đầu trong tập chạy) ⇒ khối A → evals → B: khi bỏ evals, B có thể về cùng tầng với A.
Không biết trước lượt có bật evals không ⇒ xét CẢ HAI trường hợp (run_evals True và False), trường hợp nào có tầng ≥ 2 khối hộp phiên thì báo.
Bản cũ (C 16/11, dung_lai_workflow.tang_hop_phien) không loại khối nào — khớp Capitol 84 / 84 bản đã lưu vì 0 bản có khối bị loại
(B_DOI_CHIEU_LUAT_TANG.md); ca evals ở giữa là chỗ bản cũ lọt.
"""
from __future__ import annotations

HOP_PHIEN = frozenset({"docx_chat_node", "pptx_chat_node", "ey_pptx_chat_node", "xlsx_chat_node", "d3_data_visualization_node",
                       "data_analysis_agent_node", "network_graph_output_node", "flexible_code_interpreter_node",
                       "skill_code_interpreter_node"})   # 9 loại mở hộp phiên — tài liệu nội bộ
LOAI_TAY_CAM = frozenset({"tool", "memory", "annotation"})   # = node_exclusion.HANDLE_NODE_TYPES
KHOI_DANH_GIA = "evals_node"                                 # = node_exclusion.EVALS_NODE_ID


def _st(n: dict) -> dict:
    d = n.get("data")
    s = d.get("struct") if isinstance(d, dict) else None
    return s if isinstance(s, dict) else {}


def _bi_loai(n: dict, run_evals: bool) -> bool:
    s = _st(n)
    if (s.get("node_type") or n.get("node_type")) in LOAI_TAY_CAM:
        return True
    return not run_evals and (s.get("node_id") or n.get("node_id")) == KHOI_DANH_GIA


def tang_hop_phien(pl: dict, run_evals: bool = True) -> dict[int, list[str]]:
    """{tầng: [mã khối hộp phiên]} của payload dạng lưu (nodes[].id / data.struct, edges[].source_node_id / target_node_id).
    Vòng (không xếp được) ⇒ dừng ở phần đã xếp (validate_connections của Capitol bắt vòng riêng)."""
    # Capitol xếp tầng theo data.struct.react_flow_generated_id (toposort.py); payload lưu thường = nodes[].id ⇒ lấy cái trước, thiếu thì id
    nut = {(_st(n).get("react_flow_generated_id") or n["id"]): n for n in pl.get("nodes") or []}
    chay = {i for i, n in nut.items() if not _bi_loai(n, run_evals)}
    truoc = {i: set() for i in chay}
    for e in pl.get("edges") or []:
        # toposort.py đọc source_node_id / target_node_id (dây lưu ghi mã khối ở 3 cặp ô: source / sourceNodeId / source_node_id);
        # thiếu thì lùi về source / target như payload cũ
        a, b = e.get("source_node_id") or e.get("source"), e.get("target_node_id") or e.get("target")
        if a in chay and b in chay:
            truoc[b].add(a)
    tang, con, muc = {}, set(chay), 0
    while con:
        lop = [i for i in con if truoc[i] <= tang.keys()]
        if not lop:
            break
        for i in lop:
            tang[i] = muc
        con -= set(lop)
        muc += 1
    out: dict[int, list[str]] = {}
    for i, t in tang.items():
        if _st(nut[i]).get("node_id") in HOP_PHIEN:
            out.setdefault(t, []).append(i)
    return {t: sorted(ds) for t, ds in sorted(out.items())}


def loi_tang(pl: dict) -> list[str]:
    """Danh sách LỖI (chặn lưu / chạy): mỗi tầng có > 1 khối hộp phiên, xét cả lượt bật và tắt evals. Rỗng = qua."""
    loi, da = [], set()
    for re_ in (True, False):
        for t, ds in tang_hop_phien(pl, re_).items():
            if len(ds) > 1 and tuple(ds) not in da:
                da.add(tuple(ds))
                khi = "" if re_ else " khi lượt chạy KHÔNG bật Đánh giá (khối Đánh giá đứng giữa bị bỏ)"
                loi.append(f"tầng {t} có {len(ds)} khối hộp phiên chạy song song{khi} ({', '.join(k[:8] for k in ds)}) — "
                           "người chạy phiên chỉ mở 1 hộp; nối dây cho chúng chạy lần lượt")
    return loi
