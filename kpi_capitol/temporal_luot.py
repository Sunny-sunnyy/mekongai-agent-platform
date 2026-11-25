"""Đọc lịch sử Temporal của 1 lượt (namespace capitol) — B 10/11/2026, D Đ-4: "đo thêm ở Temporal: không có ActivityTaskScheduled cho Người duyệt /
Ô ra, xếp theo eventId". Khuôn lấy từ sandbox/chay_rao_chan_chan.py:39-75. Chỉ trả SỐ ĐẾM + lý do lỗi (cắt 300 ký tự), không trả dữ liệu đầu vào.
"""
import base64
import json
import subprocess


def _tim_wid(session: str) -> str | None:
    r = subprocess.run(["docker", "exec", "capitol-temporal", "temporal", "workflow", "list", "--namespace", "capitol", "--limit", "400", "-o", "json"],   # [ghi chú] 30 ⇒ 400: lượt cũ hơn 30 lượt gần nhất không tìm thấy ⇒ mất lý do chặn,
                       capture_output=True, text=True, timeout=60)
    for x in json.loads(r.stdout or "[]"):
        w = x.get("execution", {}).get("workflowId", "")
        if session in w:
            return w
    return None


def _chu_payload(attr: dict) -> str:
    """Giải mã mọi payload base64 trong thuộc tính sự kiện thành chữ (chỉ để TÌM mã khối, không trả ra ngoài)."""
    out = []
    for p in ((attr.get("input") or {}).get("payloads") or []):
        try:
            out.append(base64.b64decode(p.get("data", "")).decode("utf-8", "replace"))
        except (ValueError, TypeError):
            pass
    return " ".join(out)


def lich_su(session: str, ma_khoi: dict[str, str]) -> dict:
    """ma_khoi: {tên: node_instance_id}. Trả: workflow_id, số ActivityTaskScheduled loại execute_* theo khối (mã khối trong input),
    eventId đầu tiên lên lịch mỗi khối, lý do lỗi của activity / workflow."""
    wid = _tim_wid(session)
    if not wid:
        return {"loi": "không thấy workflow Temporal có session trong ID"}
    h = subprocess.run(["docker", "exec", "capitol-temporal", "temporal", "workflow", "show", "--namespace", "capitol", "--workflow-id", wid,
                        "-o", "json"], capture_output=True, text=True, timeout=60)
    ev = json.loads(h.stdout or "{}").get("events", [])
    lich = {t: 0 for t in ma_khoi}
    xong = {t: 0 for t in ma_khoi}
    hong = {t: 0 for t in ma_khoi}
    cua = {}                                     # scheduledEventId ⇒ tên khối (chỉ hoạt động execute_*)
    dau = {}
    ly_do = []
    nguoc = {m: t for t, m in ma_khoi.items()}
    for e in ev:
        c = e.get("activityTaskCompletedEventAttributes") or e.get("activityTaskFailedEventAttributes") or e.get("activityTaskTimedOutEventAttributes")
        if c and int(c.get("scheduledEventId", 0)) in cua:
            (xong if "activityTaskCompletedEventAttributes" in e else hong)[cua[int(c["scheduledEventId"])]] += 1
        a = e.get("activityTaskScheduledEventAttributes")
        if a and (a.get("activityType") or {}).get("name", "").startswith("execute_"):
            # khối được CHẠY = input.node_instance.react_flow_generated_id (đọc trường, không đoán theo chuỗi — persist_* mang mọi mã khối)
            try:
                ni = json.loads(_chu_payload(a)).get("node_instance") or {}
            except (json.JSONDecodeError, AttributeError):
                ni = {}
            t = nguoc.get(ni.get("react_flow_generated_id") or ni.get("id") or "")
            if t:
                lich[t] += 1
                dau.setdefault(t, int(e.get("eventId", 0)))
                cua[int(e.get("eventId", 0))] = t
        for k in ("activityTaskFailedEventAttributes", "workflowExecutionFailedEventAttributes"):
            f = (e.get(k) or {}).get("failure")
            if f:
                ly_do.append({"eventId": int(e.get("eventId", 0)), "loai": k.split("EventAttributes")[0], "ly_do": (f.get("message") or "")[:3000],   # [ghi chú] 300 ⇒ 3000: lý do Rào chặn dài (10 lỗi) bị cắt mất LOẠI lỗi (cờ / chủ-số) cần để phân loại chặn oan
                              "goc": ((f.get("cause") or {}).get("message") or "")[:3000]})
    return {"workflow_id": wid, "so_su_kien": len(ev), "len_lich_theo_khoi": lich, "xong": xong, "hong": hong, "eventId_dau": dau, "ly_do_loi": ly_do}
