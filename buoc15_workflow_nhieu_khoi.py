"""Bước 15 — WORKFLOW NHIỀU KHỐI giống việc thật (shop nhận tin nhắn khách) + ĐO TOKEN TỪNG KHỐI. TỐN TIỀN THẬT (rất ít).

Viết 04/11/2026 (nhóm kỹ thuật, phương án 1 người vận hành). Chạy:  DA_DUYET=co python3 -I chay_thu/buoc15_workflow_nhieu_khoi.py
Cần: bộ chạy đã bật đủ 11 hộp (HUONG_DAN_TU_CHAY.md mục 3, cả bước C tạo tổ chức + bước D đặt phiên bản Temporal).
Kết quả: ket_qua/sang_04_11/n15_*.json (quy ước kết quả theo buổi, sổ cải tiến D10).

Workflow 7 khối, 5 tầng (dây luôn đi từ khối chạy trước = nguồn tới khối chạy sau = đích):

  Tầng 0  [Ô nhập: tin nhắn khách]
              │ ──────────────────────────────┐
  Tầng 1  [AI 1: Phân loại]                   │
              │ ──────────────┐               │
  Tầng 2  [AI 3: Phiếu kho]   [AI 2: Trả lời khách]   ← AI 2 nhận HAI dây vào cùng ô user_prompt
              │               │                         (tin gốc + phân loại; gom danh sách ở input_resolver.py:150-160,
  Tầng 3  [Xuất: phiếu kho]   [Người duyệt]              nối "\n" ở temporal/runtime/activity_node_runtime.py:180)
                              │
  Tầng 4                      [Xuất: trả lời đã duyệt]

Hai ca:
  C1 — chạy bình thường, script tự duyệt ⇒ kỳ vọng SUCCESS. Xem: dữ liệu có tới đúng ô không; AI 3 chỉ thấy kết quả phân loại
       (không có dây từ tin gốc) ⇒ có giữ được mã đơn không; token từng lần gọi AI.
  C2 — AI 3 đặt tên mô hình KHÔNG TỒN TẠI "khong-co-mo-hinh" ⇒ khối hỏng giữa chừng, không tốn tiền cho khối đó.
       KHÔNG dùng tên bắt đầu bằng "gpt": code lặng lẽ đổi sang gpt-5.2 (bậc đắt, suy nghĩ cao) và khối vẫn SUCCESS
       (AB workflow/node/base/agent/model_resolution.py:220-226; D phát hiện 04/11, A mở lại). Tên không rõ nhà cung cấp
       ⇒ đổi sang Claude Sonnet 4.6 (:236-242) — máy mình KHÔNG có khoá Anthropic ⇒ kỳ vọng khối lỗi vì thiếu khoá [suy luận].
       Câu hỏi: nhánh bên kia (AI 2 ⇒ duyệt ⇒ xuất) có chạy tiếp không, hay cả lượt dừng? Khối sau AI 3 có SKIPPED không?
       Nếu lưu bị từ chối ngay (có kiểm tên mô hình lúc lưu) thì đó cũng là kết quả — ghi lại.
Trần tiền: mỗi khối AI max_tokens 800, timeout 120 giây, retries 1, không công cụ. Ước cả hai ca dưới 0,01 USD (bước 5: 609 / 92
token ≈ 0,00011 USD một lần gọi) — [ước lượng], số thật lấy từ log CACHE_USAGE.
"""

import copy
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parent))
from buoc4b_gui_workflow_thu import KQ, ghi, goi, tim_khoi
from buoc6b_chay_lai_tu_khoi import psql

BUOI = KQ / "sang_04_11"

TIN_KHACH = ("Chào shop, chị đặt 2 áo thun size M (đơn DH-1042) hôm 01/11, hôm qua nhận hàng nhưng 1 áo bị lỗi đường may. "
             "Chị muốn đổi áo lỗi sang size L, áo còn lại chị giữ. Shop xử lý giúp chị nhé.")
DAN_PHAN_LOAI = ("Bạn phân loại tin nhắn khách của một shop quần áo. Trả ĐÚNG 3 dòng, không thêm gì:\n"
                 "LOAI: <doi_hang | khieu_nai_giao_hang | hoi_gia | khac>\n"
                 "PHONG_BAN: <kho | sale | cskh> (nhiều thì cách bằng dấu phẩy)\n"
                 "TOM_TAT: <một câu tiếng Việt>")
DAN_TRA_LOI = ("Bạn là nhân viên chăm sóc khách của shop. Bạn nhận tin nhắn gốc của khách và kết quả phân loại (có thể nối liền nhau). "
               "Viết trả lời cho khách, tối đa 4 câu, lịch sự, tiếng Việt. Không hứa ngày cụ thể, không bịa chính sách; "
               "chỉ nói shop sẽ liên hệ xác nhận.")
DAN_KHO = ("Bạn viết phiếu việc cho bộ phận kho từ kết quả phân loại. Trả ĐÚNG 3 dòng:\n"
           "VIEC: <việc kho cần làm>\n"
           "MA_DON: <mã đơn nếu có trong dữ liệu bạn nhận, không có thì ghi KHONG_RO — không đoán>\n"
           "UU_TIEN: <cao | thuong>")
CHUNG = {"temperature": None, "max_tokens": 800, "timeout": 120, "retries": 1}


def day(src, sport, dst, dport, field):
    return {"id": f"e-{src}-{dst}-{field}", "source": src, "target": dst, "sourceNodeId": src, "targetNodeId": dst,
            "type": "default", "data": {}, "animated": False, "sourceHandle": sport, "targetHandle": dport,
            "edge_type": "port_to_param", "source_node_id": src, "source_port_id": sport,
            "target_node_id": dst, "target_port_id": dport, "target_param_id": field}


class Ve:
    """Gom khối + dây; mỗi dây ghi ở HAI nơi (danh sách edges + incoming_connections của ô đích) — lớp kiểm của bộ dựng đòi khớp."""

    def __init__(self, catalog):
        self.catalog, self.nodes, self.edges, self.ten = catalog, [], [], {}

    def khoi(self, node_id, ten, x, y, dat=None):
        s = copy.deepcopy(tim_khoi(self.catalog, node_id))
        nid = str(uuid.uuid4())
        for p in s["params"]:
            if dat and p["field_id"] in dat:
                p["value"] = dat[p["field_id"]]
        s.update(react_flow_generated_id=nid, name=ten)
        self.nodes.append({"id": nid, "type": "custom", "position": {"x": x, "y": y}, "data": {"struct": s}})
        self.ten[nid] = ten
        return nid

    def _struct(self, nid):
        return next(n["data"]["struct"] for n in self.nodes if n["id"] == nid)

    def noi(self, src, dst, field):
        cong_ra = self._struct(src)["output_ports"][0]["id"]
        o = next(p for p in self._struct(dst)["params"] if p["field_id"] == field)
        o["bindable_config"]["is_bound"] = True
        o["bindable_config"]["input_port"].setdefault("incoming_connections", [])
        o["bindable_config"]["input_port"]["incoming_connections"] = (
            (o["bindable_config"]["input_port"]["incoming_connections"] or []) + [{"node_id": src, "port_id": cong_ra}])
        self.edges.append(day(src, cong_ra, dst, o["bindable_config"]["input_port"]["id"], field))


def ve_workflow(catalog, mo_hinh_kho):
    v = Ve(catalog)
    vao = v.khoi("text_input_node", "Tin nhắn khách", 0, 0, {"text_input": TIN_KHACH})
    pl = v.khoi("agent_node", "AI 1 - Phân loại", 350, 0, dict(CHUNG, model="gpt-6-luna", system_prompt=DAN_PHAN_LOAI))
    kho = v.khoi("agent_node", "AI 3 - Phiếu kho", 700, -200, dict(CHUNG, model=mo_hinh_kho, system_prompt=DAN_KHO))
    tl = v.khoi("agent_node", "AI 2 - Trả lời khách", 700, 200, dict(CHUNG, model="gpt-6-luna", system_prompt=DAN_TRA_LOI))
    ra_kho = v.khoi("text_output_node", "Xuất - phiếu kho", 1050, -200)
    duyet = v.khoi("human_intervention_node", "Giám sát duyệt", 1050, 200, {"timeout": 180, "auto_approve_on_timeout": False})
    ra_tl = v.khoi("text_output_node", "Xuất - trả lời đã duyệt", 1400, 200)
    v.noi(vao, pl, "user_prompt")
    v.noi(pl, kho, "user_prompt")
    v.noi(vao, tl, "user_prompt")      # dây 1 vào ô user_prompt của AI 2: tin gốc
    v.noi(pl, tl, "user_prompt")       # dây 2 vào CÙNG ô: kết quả phân loại
    v.noi(kho, ra_kho, "text_content")
    v.noi(tl, duyet, "preview_context")
    v.noi(duyet, ra_tl, "text_content")
    return v, duyet, tl


def lay_log_token(tu: str) -> list[str]:
    """Dòng CACHE_USAGE (mỗi lần gọi AI một dòng) + dòng có 'digest' từ worker Temporal, kể từ mốc `tu` (UTC). Lọc 'sk-'."""
    out = subprocess.run(["docker", "logs", "--since", tu, "capitol-ab-worker-temporal"], capture_output=True, text=True, timeout=60)
    dong = (out.stdout + out.stderr).splitlines()
    return [d for d in dong if ("CACHE_USAGE" in d or "digest" in d.lower() or "Defaulting" in d or "ERROR" in d)
            and "sk-" not in d]


def chay(org, catalog, ca, mo_hinh_kho) -> dict:
    v, duyet, tl = ve_workflow(catalog, mo_hinh_kho)
    payload = {"id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "name": f"Thu nhieu khoi - shop - {ca}",
               "description": "Bước 15 chay_thu: tin nhắn khách ⇒ phân loại ⇒ (phiếu kho | trả lời ⇒ duyệt) ⇒ xuất",
               "nodes": v.nodes, "edges": v.edges, "metadata": {}, "global_context": {},
               "publish_to_api": False, "publish_to_mcp": False, "publish_to_template": False}
    (BUOI / f"n15_{ca}_payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    code, luu = goi("POST", f"/api/v1/orgs/{org}/workflows", payload)
    ghi(f"[b15 {ca}] Lưu workflow 7 khối: HTTP {code}")
    if code >= 300:
        ghi(f"[b15 {ca}] Lưu bị từ chối: {str(luu)[:400]}")
        return {"ca": ca, "luu_http": code, "luu": luu}
    wf = (luu.get("workflow") or {}).get("id") or luu.get("workflow_id") or luu.get("id")
    # [đã chạy 04/11, D kiểm độc lập sửa nhận định của A] server GIỮ NGUYÊN mã khối mình gửi (node.node_instance_id = mã gửi lên,
    # 7/7 khối). Lần 1 hỏng vì câu dò so mã khối với node_result.node_id — cột này là khoá dòng của bảng `node`, KHÔNG phải
    # mã khối ⇒ không bao giờ khớp ⇒ không gửi duyệt. Đọc lại mã theo tên khối từ phiên bản đã lưu cho chắc, rồi dò qua bảng `node`.
    ma = json.loads(psql("select coalesce(json_object_agg(n->'data'->'struct'->>'name', n->>'id'),'{}') from workflow_version wv, "
                         f"jsonb_array_elements(wv.payload::jsonb->'nodes') n where wv.workflow_id='{wf}' and wv.version_number=1"))
    duyet, tl = ma["Giám sát duyệt"], ma["AI 2 - Trả lời khách"]
    ten = {v_: k_ for k_, v_ in ma.items()}
    _, ses = goi("GET", "/session/create-session")
    s = ses["session_id"]
    tu = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t0 = time.time()
    code, r = goi("POST", f"/workflow/{org}/{wf}/{s}/run-workflow", dict(payload, session_id=s))
    ghi(f"[b15 {ca}] Bấm chạy HTTP {code}; session {s}")
    rid = str(uuid5(NAMESPACE_URL, f"intervention:{s}:{duyet}"))
    da_duyet, st = [], ""
    for i in range(150):  # tối đa ~5 phút
        st = psql(f"select status from workflow_run where session_id='{s}'")
        if st and st not in ("RUNNING", "PENDING", "QUEUED", "PAUSED"):
            break
        # duyệt khi AI 2 đã xong (khối duyệt đang chờ); gửi lại mỗi 30 giây nếu lượt chưa xong
        tl_xong = psql("select count(*) from node_result nr join workflow_run r on r.id=nr.run_id "
                       f"join node n on n.id=nr.node_id where r.session_id='{s}' and n.node_instance_id='{tl}'")
        if tl_xong and int(tl_xong) >= 1 and (not da_duyet or time.time() - da_duyet[-1][0] > 30):
            time.sleep(4)
            c, ph = goi("POST", f"/human-intervention/{wf}/{s}/{duyet}/{rid}/resume", {})
            da_duyet.append((time.time(), c, str(ph)[:200]))
            ghi(f"[b15 {ca}] Gửi DUYỆT lần {len(da_duyet)}: HTTP {c} — {str(ph)[:150]}")
        time.sleep(2)
    giay = round(time.time() - t0)
    hang = psql("select coalesce(json_agg(json_build_object('khoi', n.node_instance_id, 'status', nr.status, 'started_at', nr.started_at, "
                "'completed_at', nr.completed_at, 'created_at', nr.created_at, 'output_data', nr.output_data, 'error_message', nr.error_message) "
                "order by nr.created_at),'[]') from node_result nr join workflow_run r on r.id=nr.run_id join node n on n.id=nr.node_id "
                f"where r.session_id='{s}'")
    try:
        hang = json.loads(hang)
    except json.JSONDecodeError:
        pass
    loi = psql(f"select left(coalesce(error_message,''),400) from workflow_run where session_id='{s}'")
    time.sleep(3)
    token = lay_log_token(tu)
    ghi(f"[b15 {ca}] KẾT THÚC sau {giay} giây: {st}; lỗi lượt: {loi or '-'}; {sum('CACHE_USAGE' in d for d in token)} dòng CACHE_USAGE")
    kq = {"ca": ca, "workflow_id": wf, "session": s, "run_http": code, "ket_thuc": st, "giay": giay, "loi_luot": loi,
          "ten_khoi": ten, "ma_gui_len": v.ten, "duyet": da_duyet, "node_result": hang, "log_token": token, "tu_utc": tu}
    (BUOI / f"n15_{ca}_ket_qua.json").write_text(json.dumps(kq, ensure_ascii=False, indent=2, default=str))
    return kq


def main() -> int:
    if os.environ.get("DA_DUYET") != "co":
        print("Chưa duyệt. Chạy lại: DA_DUYET=co python3 -I chay_thu/buoc15_workflow_nhieu_khoi.py")
        return 1
    BUOI.mkdir(exist_ok=True)
    org = (KQ / "org_id.txt").read_text().strip()
    catalog = json.loads((KQ / "b4_list_nodes.json").read_text())
    ca = os.environ.get("CA", "C1,C2").split(",")
    if "C1" in ca:
        chay(org, catalog, "C1", "gpt-6-luna")
    if "C2" in ca:
        chay(org, catalog, "C2", "khong-co-mo-hinh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
