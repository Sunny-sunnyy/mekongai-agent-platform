"""Hỏi Temporal một lượt còn chạy không — C 17/11/2026 (DANH_MUC_AP_DUNG_DAC_TA dòng 4b; nguồn ngoài A3 #2). Chỉ đọc, 0 đồng.
Vì sao: 3 luồng theo dõi tiền (hoi_kpi / chay_lai / so_skill) sau 3 giờ chờ mà PG chưa thấy SUCCESS / FAILED / STOPPED thì "ghi phần đã tiêu" rồi TRẢ CHỖ
giữ tiền. Nếu lượt thật ra VẪN chạy (Temporal còn RUNNING — ví dụ chờ người duyệt lâu, hoặc PG ghi trạng thái trễ) ⇒ phần tiêu sau đó KHÔNG được chỗ giữ
chặn nữa ⇒ lọt trần. Nay ở nhánh quá giờ: hỏi Temporal trước; còn chạy HOẶC không hỏi được ⇒ GIỮ chỗ (không ghi, không trả) — chay_s5 sẽ tự chuyển chỗ
quá hạn thành dòng sổ cận trên (đối chiếu kỹ thuật) ⇒ thà ghi dư. Temporal nói ĐÃ xong ⇒ như cũ (ghi phần đã tiêu + trả).
Mã workflow Temporal = "workflow-<mã phiên>" (cách B đọc lịch sử 16/11: va_chan_loi_mem/doc_attempt.sh).
"""
import json
import re
import subprocess

_MA = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LENH = ["docker", "exec", "capitol-temporal", "temporal", "workflow", "describe", "--namespace", "capitol", "--address", "127.0.0.1:7233", "-o", "json", "-w"]


def con_chay(phien: str, chay=subprocess.run) -> bool | None:
    """True = Temporal báo RUNNING · False = Temporal báo trạng thái khác (COMPLETED / FAILED / TERMINATED / CANCELED / TIMED_OUT / CONTINUED_AS_NEW)
    · None = không hỏi được (hộp tắt, lỗi CLI, không thấy workflow, đầu ra lạ) ⇒ người gọi coi như CÒN chạy (đóng về phía an toàn)."""
    if not isinstance(phien, str) or not _MA.fullmatch(phien):
        return None
    try:
        r = chay(LENH + [f"workflow-{phien}"], capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    try:
        d = json.loads(r.stdout)
    except (ValueError, TypeError):
        return None
    st = ((d.get("workflowExecutionInfo") or {}).get("status") if isinstance(d, dict) else None) or ""
    if not isinstance(st, str) or not st:
        return None
    return st.upper().endswith("RUNNING")


def nen_giu_cho(phien: str, chay=subprocess.run) -> bool:
    """Nhánh quá giờ của luồng theo dõi: True ⇒ GIỮ chỗ (không ghi, không trả)."""
    return con_chay(phien, chay) is not False


def mat_dong(phien: str, co_dong, chay=subprocess.run, ngu=None) -> bool:
    """[ghi chú kỹ thuật] True CHỈ KHI chắc lượt "mất dòng": Temporal báo lượt ĐÃ KẾT THÚC (con_chay là False) VÀ sau đó hỏi lại `co_dong()`
    (Postgres có dòng workflow_run của phiên chưa) vẫn là không. Temporal còn chạy / không hỏi được / dòng vừa xuất hiện ⇒ False (người gọi chờ tiếp như cũ).
    Hỏi Temporal TRƯỚC rồi mới hỏi lại Postgres: nếu dòng tới chậm thì lần hỏi lại thấy nó ⇒ không kết luận oan."""
    if con_chay(phien, chay) is not False:
        return False
    if ngu:
        ngu(2)
    try:
        return not co_dong()
    except Exception:  # noqa: BLE001 — không hỏi lại được Postgres ⇒ không kết luận
        return False


LENH_LICH_SU = ["docker", "exec", "capitol-temporal", "temporal", "workflow", "show", "--namespace", "capitol", "--address", "127.0.0.1:7233", "-o", "json", "-w"]


def so_khoi_da_chay(phien: str, chay=subprocess.run):
    """[ghi chú kỹ thuật] Nguồn KHÔNG phụ thuộc Postgres: số activity CHẠY KHỐI (tên bắt đầu "execute_", vd execute_agent_node, execute_lightweight_node) đã được xếp
    lịch trong lịch sử Temporal của lượt. 0 ⇒ chắc chưa khối nào chạy ⇒ chưa có lời gọi AI. None = không đọc được (người gọi coi như CÓ THỂ đã chạy)."""
    if not isinstance(phien, str) or not _MA.fullmatch(phien):
        return None
    try:
        r = chay(LENH_LICH_SU + [f"workflow-{phien}"], capture_output=True, text=True, timeout=40)
        if getattr(r, "returncode", 1) != 0:
            return None
        ev = json.loads(r.stdout).get("events")
        if not isinstance(ev, list) or not ev:
            return None
        return sum(1 for e in ev if str(((e.get("activityTaskScheduledEventAttributes") or {}).get("activityType") or {}).get("name") or "").startswith("execute_"))
    except Exception:  # noqa: BLE001
        return None


def mat_dong_loai(phien: str, co_dong, chay=subprocess.run, ngu=None):
    """None = không phải lượt mất dòng (hoặc chưa kết luận được) · "khong_khoi" = mất dòng VÀ lịch sử Temporal cho thấy 0 khối được chạy (chắc chưa tốn tiền AI)
    · "co_khoi" = mất dòng nhưng đã có khối chạy HOẶC không đọc được lịch sử ⇒ người gọi KHÔNG được nói / ghi "0 đồng"."""
    if not mat_dong(phien, co_dong, chay, ngu):
        return None
    return "khong_khoi" if so_khoi_da_chay(phien, chay) == 0 else "co_khoi"
