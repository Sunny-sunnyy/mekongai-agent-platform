"""Bước 4b — gửi một workflow thử KHÔNG gọi AI: ô nhập chữ ⇒ khối xuất chữ (một dây port_to_param).

Viết 03/11/2026, CHƯA CHẠY. Chạy bằng:  DA_DUYET=co python3 -I chay_thu/buoc4b_gui_workflow_thu.py
Chỉ dùng thư viện chuẩn (urllib). Chỉ gọi 127.0.0.1:18300 (agentic-backend của mình).

Khuôn dữ liệu lấy từ CHÍNH hệ thống đang chạy, không đoán:
  - định nghĩa khối: ket_qua/b4_list_nodes.json (POST /nodes/<org>/list-nodes, lấy lúc 14:1x 03/11)
  - hình một khối / một dây trong workflow: loadtest/fixtures/simple_agent.json của họ
    (node = {id, type, position, data: {struct: <định nghĩa khối + react_flow_generated_id>}}; dây port_to_param có
     source_node_id, source_port_id, target_node_id, target_port_id, target_param_id)
Đường đi (đọc code, chưa chạy):
  1) POST /api/v1/orgs/<org>/workflows        lưu ⇒ workflow_id (UUID) + phiên bản
  2) GET  /session/create-session             ⇒ session_id
  3) POST /workflow/<org>/<wf>/<session>/run-workflow  ⇒ hỏi platform-api quyền khối ⇒ xếp vào hàng đợi Redis
  4) worker chạy ⇒ bảng workflow_run (status) trong PostgreSQL — xem bằng buoc4c_xem_ket_qua.sh
"""

import copy
import json
import os
import re   # [ghi chú]
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

THU_MUC = Path(__file__).resolve().parent
KQ = THU_MUC / "ket_qua"
API = "http://127.0.0.1:18300"
CHU_THU = "Xin chào từ MekongAI — workflow thử bộ lõi Capitol, không gọi AI."


def ghi(msg: str) -> None:
    dong = f"[{datetime.now():%H:%M:%S %d/%m/%Y}] {msg}"
    print(dong)
    with open(KQ / "nhat_ky.txt", "a", encoding="utf-8") as f:
        f.write(dong + "\n")



def _ma_18300(goc: Path) -> dict:
    """[ghi chú] Header mã cho hộp gác 18300 (Caddy chặn MẶC ĐỊNH mọi đường ghi / chạy khi thiếu mã — lượt AI ngoài sổ, E ca 1 + 2).
    Mã ở env/cong_18300.khoa.env (600, Git bỏ qua). Đọc MỖI lần gọi; thiếu / sai dạng ⇒ {} ⇒ Caddy trả 403 (hỏng ồn, không chạy ngoài sổ). Không in, không log."""
    try:
        for dong in (goc / "env" / "cong_18300.khoa.env").read_text(encoding="utf-8").splitlines():
            if dong.startswith("CONG_18300_MA="):
                ma = dong.split("=", 1)[1].strip()
                if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", ma):
                    return {"X-Chay-Thu-Ma": ma}
    except OSError:
        pass
    return {}


def goi(method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    # [ghi chú] CHỈ tiến trình đã nạp sổ tiền (sandbox.chay_s5 — chay_s5, chay_mau, hoi_kpi, tao_workflow_mau…) gửi mã; script khác ⇒ 403 (luật 3.9)
    co_so = "sandbox.chay_s5" in sys.modules or "chay_s5" in sys.modules
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(_ma_18300(THU_MUC) if co_so else {})})
    try:
        with urllib.request.urlopen(req, timeout=int(os.environ.get("TIMEOUT_GOI", "60"))) as r:
            raw = r.read().decode()
            code = r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read().decode(), e.code
    try:
        return code, json.loads(raw)
    except json.JSONDecodeError:
        return code, raw


def tim_khoi(catalog: object, node_id: str) -> dict:
    if isinstance(catalog, dict):
        if catalog.get("node_id") == node_id:
            return catalog
        for v in catalog.values():
            r = tim_khoi(v, node_id)
            if r:
                return r
    elif isinstance(catalog, list):
        for v in catalog:
            r = tim_khoi(v, node_id)
            if r:
                return r
    return {}


def main() -> int:
    if os.environ.get("DA_DUYET") != "co":
        print("Chưa duyệt. Chạy lại: DA_DUYET=co python3 -I chay_thu/buoc4b_gui_workflow_thu.py")
        return 1
    org = (KQ / "org_id.txt").read_text().strip()
    catalog = json.loads((KQ / "b4_list_nodes.json").read_text())
    vao = copy.deepcopy(tim_khoi(catalog, "text_input_node"))
    ra = copy.deepcopy(tim_khoi(catalog, "text_output_node"))
    if not vao or not ra:
        ghi("Không thấy text_input_node / text_output_node trong danh mục — dừng.")
        return 1

    id_vao, id_ra = str(uuid.uuid4()), str(uuid.uuid4())
    cong_ra_vao = vao["output_ports"][0]["id"]  # cổng ra "text" của ô nhập chữ

    for p in vao["params"]:
        if p["field_id"] == "text_input":
            p["value"] = CHU_THU
    o_dich = next(p for p in ra["params"] if p["field_id"] == "text_content")
    cong_vao_ra = o_dich["bindable_config"]["input_port"]["id"]  # cổng gắn với ô text_content
    o_dich["bindable_config"]["is_bound"] = True
    o_dich["bindable_config"]["input_port"]["incoming_connections"] = [{"node_id": id_vao, "port_id": cong_ra_vao}]

    def nut(struct: dict, nid: str, x: int) -> dict:
        s = dict(struct, react_flow_generated_id=nid, name=struct["display_name"])
        return {"id": nid, "type": "custom", "position": {"x": x, "y": 0}, "data": {"struct": s}}

    session_luu = str(uuid.uuid4())
    payload = {
        "id": str(uuid.uuid4()),
        "session_id": session_luu,
        "name": "Thu bo loi - nhap chu ra chu",
        "description": "Workflow thử không gọi AI (chay_thu bước 4b)",
        "nodes": [nut(vao, id_vao, 0), nut(ra, id_ra, 400)],
        "edges": [{
            "id": f"e-{id_vao}-{id_ra}",
            # dây luôn từ khối chạy trước (nguồn) tới khối chạy sau (đích)
            "source": id_vao, "target": id_ra,
            # khuôn dây bắt buộc cả bản camelCase (lần 1 ngày 03/11 bị 422 "sourceNodeId / targetNodeId: Field required")
            "sourceNodeId": id_vao, "targetNodeId": id_ra,
            "type": "default", "data": {}, "animated": False,
            "sourceHandle": cong_ra_vao, "targetHandle": cong_vao_ra,
            "edge_type": "port_to_param",
            "source_node_id": id_vao, "source_port_id": cong_ra_vao,
            "target_node_id": id_ra, "target_port_id": cong_vao_ra, "target_param_id": "text_content",
        }],
        "metadata": {}, "global_context": {},
        "publish_to_api": False, "publish_to_mcp": False, "publish_to_template": False,
    }
    (KQ / "b4_payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    code, luu = goi("POST", f"/api/v1/orgs/{org}/workflows", payload)
    (KQ / "b4_1_luu_workflow.json").write_text(json.dumps(luu, ensure_ascii=False, indent=2) if not isinstance(luu, str) else luu)
    ghi(f"1) Lưu workflow: HTTP {code}")
    if code >= 300:
        return 1
    wf = (luu.get("workflow") or {}).get("id") or luu.get("workflow_id") or luu.get("id")
    ghi(f"   workflow_id = {wf}")

    code, ses = goi("GET", "/session/create-session")
    session = ses.get("session_id") if isinstance(ses, dict) else None
    ghi(f"2) Tạo phiên: HTTP {code}, session_id = {session}")
    if not session:
        return 1

    payload_chay = dict(payload, session_id=session)
    code, chay = goi("POST", f"/workflow/{org}/{wf}/{session}/run-workflow", payload_chay)
    (KQ / "b4_3_chay_workflow.json").write_text(json.dumps(chay, ensure_ascii=False, indent=2) if not isinstance(chay, str) else chay)
    ghi(f"3) Bấm chạy: HTTP {code} — {str(chay)[:300]}")
    (KQ / "b4_session_id.txt").write_text(session)
    return 0 if code < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
