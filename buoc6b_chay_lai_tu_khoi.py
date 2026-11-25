"""Bước 6b — thử "chạy lại từ một khối" trên đường Temporal (không gọi AI, không tốn tiền).

Viết 03/11/2026. Chạy:  DA_DUYET=co python3 -I chay_thu/buoc6b_chay_lai_tu_khoi.py
Câu hỏi muốn trả lời bằng chạy thật (chủ đề 03 mục 2.4, hiện là [code]):
  "Chạy lại từ một khối có SO dấu vân tay để biết khối phía trước đã đổi không?"
  Code: khoá được tính và lưu nhưng KHÔNG so — "the user is authoritative on which start point is safe"
  (src/temporal/utils/cache.py:18-24); khối chạy lại = khối được chọn + phía sau, khối phía trước lấy kết quả lượt cũ
  (src/temporal/workflows/agentic_workflow.py:935-975).
Cách thử:
  Lượt A: ô nhập "LẦN 1" ⇒ khối xuất chữ.  Chờ SUCCESS.
  Lượt B: SỬA ô nhập thành "LẦN 2", rồi "chạy lại từ khối xuất chữ" (starting_node_ids = [khối xuất chữ]).
  Nếu code đúng như đọc: khối xuất chữ ra "LẦN 1" (lấy kết quả cũ của ô nhập, không biết ô nhập đã đổi).
  Nếu ra "LẦN 2": chạy lại CÓ đọc lại khối phía trước ⇒ ghi chú code đọc sai.
"""

import copy
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from buoc4b_gui_workflow_thu import KQ, ghi, goi, tim_khoi


def psql(sql: str) -> str:
    return subprocess.run(["docker", "exec", "capitol-postgres", "psql", "-U", "capitol", "-d", "agentic", "-tA", "-c", sql],
                          capture_output=True, text=True, timeout=30).stdout.strip()


def cho_xong(session: str, toi_da: int = 120) -> str:
    for _ in range(toi_da // 2):
        s = psql(f"select status from workflow_run where session_id='{session}'")
        if s and s not in ("RUNNING", "PENDING", "QUEUED"):
            return s
        time.sleep(2)
    return s or "KHÔNG THẤY"


def lam_payload(catalog: dict, id_vao: str, id_ra: str, chu: str, session: str, wf_id: str) -> dict:
    vao, ra = copy.deepcopy(tim_khoi(catalog, "text_input_node")), copy.deepcopy(tim_khoi(catalog, "text_output_node"))
    for p in vao["params"]:
        if p["field_id"] == "text_input":
            p["value"] = chu
    cong_vao = vao["output_ports"][0]["id"]
    o = next(p for p in ra["params"] if p["field_id"] == "text_content")
    cong_ra = o["bindable_config"]["input_port"]["id"]
    o["bindable_config"]["is_bound"] = True
    o["bindable_config"]["input_port"]["incoming_connections"] = [{"node_id": id_vao, "port_id": cong_vao}]

    def nut(s: dict, nid: str, x: int) -> dict:
        return {"id": nid, "type": "custom", "position": {"x": x, "y": 0},
                "data": {"struct": dict(s, react_flow_generated_id=nid, name=s["display_name"])}}

    return {"id": wf_id, "session_id": session, "name": "Thu chay lai tu mot khoi",
            "nodes": [nut(vao, id_vao, 0), nut(ra, id_ra, 400)],
            "edges": [{"id": f"e-{id_vao}-{id_ra}", "source": id_vao, "target": id_ra, "sourceNodeId": id_vao,
                       "targetNodeId": id_ra, "type": "default", "data": {}, "animated": False,
                       "sourceHandle": cong_vao, "targetHandle": cong_ra, "edge_type": "port_to_param",
                       "source_node_id": id_vao, "source_port_id": cong_vao, "target_node_id": id_ra,
                       "target_port_id": cong_ra, "target_param_id": "text_content"}],
            "metadata": {}, "global_context": {}, "publish_to_api": False, "publish_to_mcp": False,
            "publish_to_template": False}


def ket_qua(session: str) -> str:
    return psql("select coalesce(string_agg(nr.status::text || ':' || coalesce(nr.output_data->>'value','') || "
                "' | cache_key=' || coalesce(left(nr.cache_key,12),'-') || ' | cached_from=' || coalesce(nr.cached_from_run_id::text,'-'), E'\\n'),'') "
                f"from node_result nr join workflow_run r on r.id=nr.run_id where r.session_id='{session}'")


def main() -> int:
    if os.environ.get("DA_DUYET") != "co":
        print("Chưa duyệt. Chạy lại: DA_DUYET=co python3 -I chay_thu/buoc6b_chay_lai_tu_khoi.py")
        return 1
    org = (KQ / "org_id.txt").read_text().strip()
    catalog = json.loads((KQ / "b4_list_nodes.json").read_text())
    id_vao, id_ra = str(uuid.uuid4()), str(uuid.uuid4())

    # Lượt A
    pa = lam_payload(catalog, id_vao, id_ra, "LẦN 1", str(uuid.uuid4()), str(uuid.uuid4()))
    code, luu = goi("POST", f"/api/v1/orgs/{org}/workflows", pa)
    ghi(f"[b6] Lưu workflow: HTTP {code}")
    if code >= 300:
        (KQ / "b6_loi_luu.json").write_text(json.dumps(luu, ensure_ascii=False, indent=2) if not isinstance(luu, str) else luu)
        return 1
    wf = (luu.get("workflow") or {}).get("id") or luu.get("workflow_id") or luu.get("id")
    _, ses = goi("GET", "/session/create-session")
    sa = ses["session_id"]
    code, chay = goi("POST", f"/workflow/{org}/{wf}/{sa}/run-workflow", dict(pa, session_id=sa))
    ghi(f"[b6] Lượt A (ô nhập 'LẦN 1'): bấm chạy HTTP {code} — {str(chay)[:160]}")
    st = cho_xong(sa)
    kq_a = ket_qua(sa)
    ghi(f"[b6] Lượt A: {st}\n{kq_a}")
    if st != "SUCCESS":
        return 1

    # Lượt B: sửa ô nhập rồi chạy lại từ khối xuất chữ
    _, ses = goi("GET", "/session/create-session")
    sb = ses["session_id"]
    pb = lam_payload(catalog, id_vao, id_ra, "LẦN 2", sb, pa["id"])
    yeu_cau = {"source_session_id": sa, "starting_node_ids": [id_ra], "payload": pb}
    code, rr = goi("POST", f"/workflow/{org}/{wf}/rerun-from-node", yeu_cau)
    (KQ / "b6_chay_lai_phan_hoi.json").write_text(json.dumps(rr, ensure_ascii=False, indent=2) if not isinstance(rr, str) else rr)
    ghi(f"[b6] Lượt B (ô nhập đã sửa 'LẦN 2', chạy lại từ khối xuất chữ): HTTP {code} — {str(rr)[:200]}")
    if code >= 300:
        return 1
    sb_that = (rr.get("session_id") if isinstance(rr, dict) else None) or sb
    st = cho_xong(sb_that)
    kq_b = ket_qua(sb_that)
    ghi(f"[b6] Lượt B: {st}\n{kq_b}")
    out = {"luot_A": {"session": sa, "status": psql(f"select status from workflow_run where session_id='{sa}'"), "node_result": kq_a},
           "luot_B": {"session": sb_that, "status": st, "node_result": kq_b},
           "khoi_xuat_chu_lan_B_ra": "LẦN 1" if "LẦN 1" in kq_b else ("LẦN 2" if "LẦN 2" in kq_b else "?")}
    (KQ / "b6_ket_qua.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    ghi(f"[b6] KẾT LUẬN: lượt B khối xuất chữ ra {out['khoi_xuat_chu_lan_B_ra']} "
        "(LẦN 1 = chạy lại KHÔNG so dấu vân tay, dùng kết quả cũ; LẦN 2 = có đọc lại)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
