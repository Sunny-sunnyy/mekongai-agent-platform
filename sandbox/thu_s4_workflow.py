"""S4 — thử vá 10 R12 (b)(c) ở MỨC WORKFLOW, KHÔNG tốn tiền AI (06/11/2026, A).
Chạy trên WSL:  DA_DUYET=co python3 -I chay_thu/sandbox/thu_s4_workflow.py
Mọi khối Agent đặt mô hình "khong-co-mo-hinh" ⇒ nếu rào hở và khối chạy thì khối LỖI vì thiếu khoá Anthropic, không gọi AI nào
(bước 15 C2, 04/11). Kết quả: ket_qua/06_11_sang/s4/thu_s4_workflow.json.

Workflow sai (phải bị từ chối TRƯỚC khi khối nào chạy: workflow_run FAILED, lỗi có "R12", node_result = 0 dòng):
  T29   Ô nhập ⇒ Agent A (chạy code) ⇒ Agent B (đọc trang fetch_url) ⇒ Xuất chữ
  T29b  Ô nhập ⇒ Agent A (chạy code) ⇒ Người duyệt (cho phép) ⇒ Agent C (tra web) ⇒ Xuất chữ        — gián tiếp
  T36   Ô nhập ⇒ Agent A (chạy code) ⇒ khối ngoài danh sách cho phép (loại đầu tiên tìm được trong danh mục)
  T30   Ô nhập ⇒ Agent A (chạy code, ô mcp_servers có một máy chủ) ⇒ Xuất chữ
Đối chứng dương:
  T44   Ô nhập ⇒ Agent A (chạy code) ⇒ Xuất chữ  — R12 cho qua (log "[R12] dat"), khối A tự lỗi vì mô hình không tồn tại.
Các đường chạy (T37) cho T29: (1) route cũ run-workflow, (2) API v1 async-run-workflow-version, (3) chạy lại từ một khối v1,
(4) lịch chạy mỗi phút (đợi 1 lượt rồi XOÁ lịch). Đường worker Redis không đi được trên máy thử (ab-api đặt USE_TEMPORAL=true) ⇒
thử hàm kiểm trực tiếp trong hộp capitol-ab-worker (đúng code worker Redis nạp).
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from buoc4b_gui_workflow_thu import KQ, ghi, goi, tim_khoi
from buoc6b_chay_lai_tu_khoi import psql
from buoc15_workflow_nhieu_khoi import Ve

BUOI = KQ / "06_11_sang" / "s4"
MH = "khong-co-mo-hinh"
CHUNG = {"temperature": None, "max_tokens": 200, "timeout": 60, "retries": 0, "model": MH}
CHO_PHEP = {"text_output_node", "markdown_output_node", "file_browser_output_node", "human_intervention_node", "sticky_note_node",
            "agent_node", "text_input_node"}


def ve(catalog, ca):
    v = Ve(catalog)
    vao = v.khoi("text_input_node", "Ô nhập", 0, 0, {"text_input": "Tính tổng đơn giá trong file."})
    a = v.khoi("agent_node", "Agent A - chạy code", 300, 0,
               dict(CHUNG, system_prompt="Phân tích file.", tool_run_python=True, tool_ma_file="don_hang_gia.xlsx",
                    **({"mcp_servers": {"may_chu_thu": {"url": "http://127.0.0.1:9/mcp"}}} if ca == "T30" else {})))
    v.noi(vao, a, "user_prompt")
    if ca == "T29":
        b = v.khoi("agent_node", "Agent B - đọc trang", 600, 0, dict(CHUNG, system_prompt="Gửi đi.", tool_fetch_url=True))
        v.noi(a, b, "user_prompt")
        v.noi(b, v.khoi("text_output_node", "Xuất", 900, 0), "text_content")
    elif ca == "T29b":
        d = v.khoi("human_intervention_node", "Duyệt", 600, 0, {"timeout": 60, "auto_approve_on_timeout": False})
        v.noi(a, d, "preview_context")
        c = v.khoi("agent_node", "Agent C - tra web", 900, 0, dict(CHUNG, system_prompt="Tra.", tool_web_search=True))
        v.noi(d, c, "user_prompt")
        v.noi(c, v.khoi("text_output_node", "Xuất", 1200, 0), "text_content")
    elif ca == "T36":
        loai = next(x for x in ten_cac_khoi_loc(catalog) if x[0] not in CHO_PHEP)
        k = v.khoi(loai[0], f"Khối cấm ({loai[0]})", 600, 0)
        v.noi(a, k, loai[1])
        v.loai_cam = loai[0]
    else:   # T30, T44
        v.noi(a, v.khoi("text_output_node", "Xuất", 600, 0), "text_content")
    return v


def ten_cac_khoi(catalog):
    out = []

    def di(x):
        if isinstance(x, dict):
            if "node_id" in x and "params" in x:
                out.append(x["node_id"])
            for y in x.values():
                di(y)
        elif isinstance(x, list):
            for y in x:
                di(y)
    di(catalog)
    return out


def khoi_co_o_chu(catalog, node_id):
    """Trả (node_id, field) nếu khối có một ô nhận dây (bindable) — để nối dây vào được."""
    s = tim_khoi(catalog, node_id)
    for p in s.get("params", []):
        bc = p.get("bindable_config") or {}
        if bc.get("input_port"):
            return (node_id, p["field_id"])
    return None


def ten_cac_khoi_loc(catalog):
    return [x for x in (khoi_co_o_chu(catalog, n) for n in ten_cac_khoi(catalog)) if x]


def luu(org, v, ten):
    pl = {"id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "name": f"S4 R12 - {ten}", "description": "chay_thu S4 R12 (không AI)",
          "nodes": v.nodes, "edges": v.edges, "metadata": {}, "global_context": {},
          "publish_to_api": False, "publish_to_mcp": False, "publish_to_template": False}
    code, r = goi("POST", f"/api/v1/orgs/{org}/workflows", pl)
    wf = ((r.get("workflow") or {}).get("id") or r.get("workflow_id") or r.get("id")) if isinstance(r, dict) else None
    return pl, code, wf, r


def cho(session, giay=120):
    st = ""
    for _ in range(giay // 2):
        st = psql(f"select status from workflow_run where session_id='{session}'")
        if st and st not in ("RUNNING", "PENDING", "QUEUED"):
            break
        time.sleep(2)
    return st


def ket(session):
    return {"status": psql(f"select status from workflow_run where session_id='{session}'"),
            "loi": psql(f"select left(coalesce(error_message,''),500) from workflow_run where session_id='{session}'"),
            "so_node_result": psql("select count(*) from node_result nr join workflow_run r on r.id=nr.run_id "
                                   f"where r.session_id='{session}'")}


def bi_chan(k):
    """Bị chặn đúng = FAILED + lý do có "R12" (ghi vào workflow_run từ bản vá 10:24) + 0 kết quả khối (không khối nào chạy)."""
    return k["status"] == "FAILED" and "R12" in (k["loi"] or "") and k["so_node_result"] == "0"


def log_tu(tu, chu):
    out = []
    for c in ("capitol-ab-worker-temporal", "capitol-ab-api"):
        r = subprocess.run(["docker", "logs", "--since", tu, c], capture_output=True, text=True, timeout=60)
        out += [f"{c}: {d[:300]}" for d in (r.stdout + r.stderr).splitlines() if chu in d and "sk-" not in d]
    return out


def main() -> int:
    if os.environ.get("DA_DUYET") != "co":
        print("Chưa duyệt. Chạy lại: DA_DUYET=co python3 -I chay_thu/sandbox/thu_s4_workflow.py")
        return 1
    BUOI.mkdir(parents=True, exist_ok=True)
    org = (KQ / "org_id.txt").read_text().strip()
    code, catalog = goi("POST", f"/nodes/{org}/list-nodes", {})
    (BUOI / "danh_muc_khoi.json").write_text(json.dumps(catalog, ensure_ascii=False))
    co_o = any(p.get("field_id") == "tool_run_python" for p in tim_khoi(catalog, "agent_node").get("params", []))
    ghi(f"[S4] danh mục khối HTTP {code}; agent_node có ô tool_run_python: {co_o}")
    kq = {"danh_muc_co_tool_run_python": co_o, "ca": {}}
    tu = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for ca in ("T29", "T29b", "T36", "T30", "T44"):
        v = ve(catalog, ca)
        pl, c, wf, r = luu(org, v, ca)
        if c >= 300 or not wf:
            kq["ca"][ca] = {"luu_http": c, "luu": str(r)[:500]}
            ghi(f"[S4 {ca}] lưu HTTP {c}: {str(r)[:200]}")
            continue
        _, ses = goi("GET", "/session/create-session")
        s = ses["session_id"]
        c2, r2 = goi("POST", f"/workflow/{org}/{wf}/{s}/run-workflow", dict(pl, session_id=s))
        cho(s)
        k = ket(s)
        dat = (k["status"] == "FAILED" and "R12" not in (k["loi"] or "")) if ca == "T44" else bi_chan(k)
        kq["ca"][ca] = {"workflow_id": wf, "session": s, "run_http": c2, **k, "dat": dat,
                        **({"loai_cam": v.loai_cam} if ca == "T36" else {})}
        ghi(f"[S4 {ca}] {'ĐẠT' if dat else 'TRƯỢT'} — {k}")
        if ca == "T29":
            kq["T29_wf"], kq["T29_pl"], kq["T29_s"] = wf, pl, s
    # T37 — các đường khác cho workflow T29
    wf, pl, s0 = kq.pop("T29_wf", None), kq.pop("T29_pl", None), kq.pop("T29_s", None)
    duong = {}
    if wf:
        _, ses = goi("GET", "/session/create-session")
        s = ses["session_id"]
        c, r = goi("POST", f"/api/v1/orgs/{org}/workflows/{wf}/async-run-workflow-version", {"session_id": s})
        sid = (r.get("session_id") if isinstance(r, dict) else None) or s
        cho(sid)
        duong["v1_async_run"] = {"http": c, "phan_hoi": str(r)[:300], **ket(sid)}
        _, ses = goi("GET", "/session/create-session")
        sb = ses["session_id"]
        c, r = goi("POST", f"/api/v1/orgs/{org}/workflows/{wf}/runs/rerun-from-node",
                   {"source_session_id": s0, "starting_node_ids": [pl["nodes"][1]["id"]], "payload": dict(pl, session_id=sb)})
        sid = (r.get("session_id") if isinstance(r, dict) else None) or sb
        cho(sid)
        duong["v1_rerun_from_node"] = {"http": c, "phan_hoi": str(r)[:300], **ket(sid)}
        base = f"/api/v1/orgs/{org}/workflows/{wf}/schedules"
        truoc = psql(f"select count(*) from workflow_run where workflow_id='{wf}'")
        c, r = goi("POST", base, {"name": "S4 R12 lich", "cron_expression": "* * * * *", "timezone": "Asia/Ho_Chi_Minh",
                                  "idempotency_key": f"s4-r12-{uuid.uuid4().hex[:8]}", "overlap_policy": "skip"})
        lid = (r.get("id") or r.get("schedule_id")) if isinstance(r, dict) else None
        t0, moi = time.time(), truoc
        while time.time() - t0 < 150:
            moi = psql(f"select count(*) from workflow_run where workflow_id='{wf}'")
            if int(moi or 0) > int(truoc or 0):
                break
            time.sleep(5)
        time.sleep(15)
        s_lich = psql(f"select session_id from workflow_run where workflow_id='{wf}' order by created_at desc limit 1")
        k = ket(s_lich) if s_lich else {}
        cx, rx = goi("DELETE", f"{base}/{lid}") if lid else (None, None)
        duong["lich_chay"] = {"http_tao": c, "lich_id": lid, "luot_truoc": truoc, "luot_sau": moi, "session": s_lich, **k,
                              "xoa_lich_http": cx, "lich_con_trong_db": psql(f"select count(*) from workflow_schedule where id='{lid}'") if lid else None}
        for ten, d in duong.items():
            d["dat"] = bi_chan(d) if d.get("status") else False
            ghi(f"[S4 T37 {ten}] {'ĐẠT' if d['dat'] else 'TRƯỢT'} — status={d.get('status')} node_result={d.get('so_node_result')} loi={str(d.get('loi'))[:120]}")
    kq["T37"] = duong
    # đường worker Redis: hàm kiểm trong hộp capitol-ab-worker trên đúng payload T29
    if pl:
        ma = ("import json,sys\nfrom workflow.runtime.payload_transformer import transform_advanced_to_backend\n"
              "from workflow.r12_kiem import kiem_r12\np=transform_advanced_to_backend(json.load(sys.stdin))\n"
              "try:\n    kiem_r12(p); print('KHONG_CHAN')\nexcept ValueError as e:\n    print('CHAN', str(e)[:200])\n")
        r = subprocess.run(["docker", "exec", "-i", "-w", "/srv/src", "capitol-ab-worker", "python", "-c", ma],
                           input=json.dumps(pl), capture_output=True, text=True, timeout=120)
        dong = (r.stdout.strip().splitlines() or [""])[-1]
        kq["T37_redis_worker_ham_kiem"] = {"ra": dong, "dat": dong.startswith("CHAN"), "loi": r.stderr[-300:]}
        ghi(f"[S4 T37 worker Redis — hàm kiểm] {dong[:160]}")
    time.sleep(3)
    kq["log_R12"] = log_tu(tu, "[R12]")
    kq["log_E2B_bi_cam"] = log_tu(tu, "cấm hộp E2B")
    tong = [d.get("dat") for d in kq["ca"].values()] + [d.get("dat") for d in duong.values()] + [kq.get("T37_redis_worker_ham_kiem", {}).get("dat")]
    kq["tong"] = {"dat": sum(1 for x in tong if x), "tong": len(tong)}
    (BUOI / "thu_s4_workflow.json").write_text(json.dumps(kq, ensure_ascii=False, indent=2, default=str))
    ghi(f"[S4] xong: {kq['tong']} — ket_qua/06_11_sang/s4/thu_s4_workflow.json")
    return 0



if __name__ == "__main__":
    sys.exit(main())
