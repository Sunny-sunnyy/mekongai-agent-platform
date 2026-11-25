"""[ghi chú kỹ thuật] Chữ lỗi của lớp chặn lỗi mềm ("chan", sua_doi/nodes/agent/kiem_loi_mem.py:chu_loi) đọc dễ hiểu trên trang 18400.
Vì sao ở trang, không sửa chữ gốc: chữ gốc PHẢI giữ mảnh "no text content provided" (nhánh MISSING_INPUT của Capitol ⇒ không thử lại, tốn tiền 0);
Capitol lại đổi chữ đó thành "The workflow's Text Input step has no text." ở workflow_run.error_message (sai nghĩa khi đầu vào là chữ "None").
Ở đây: chỉ đổi chữ HIỂN THỊ khi đọc lượt (xem_workflow.mot_luot) — sự kiện execution_failed có chữ của lớp chặn ⇒ câu tiếng Việt + mã lý do;
luot.loi (tóm tắt) ⇒ câu đó, chữ Capitol giữ ở luot.loi_capitol. Chữ khác ⇒ để nguyên. Hàm thuần, không đọc DB."""
from __future__ import annotations

import re

MA = {
    "dau_vao_rong": "đầu vào từ khối trước trống",
    "dau_vao_none": "đầu vào từ khối trước chỉ là chữ \"None\" / \"null\" (khối trước không trả gì)",
    "con_bien_chua_thay": "lời gửi AI chỉ còn khuôn có biến {{…}} chưa được thay (kết quả khối trước không tới)",
    "con_cho_trong_mau": "lời gửi AI còn chỗ trống mẫu kiểu [Insert …]",
    "moi_cong_cu_loi": "mọi lần gọi công cụ đều lỗi",
    "moi_lan_chay_code_loi": "mọi lần chạy code đều lỗi",
    "chi_xin_loi": "câu trả lời chỉ là lời xin lỗi / \"không thể\", không có nội dung",
}
_TRUOC = re.compile(r"Khối Agent không gọi AI: .*?\(([a-z_, ]+)\)")
_SAU = re.compile(r"Khối Agent đã chạy nhưng kết quả không dùng được \(([a-z_, ]+)\)")
_RAO = re.compile(r"Guardrail blocked:\s*(.+)", re.S)
# [ghi chú kỹ thuật] Rào của MÌNH từ chối file / cấu hình của khối chạy code + khối tạo file (lời báo bắt đầu
# "E2B (chay_thu…): …" hoặc "hộp phiên (chay_thu): …" — chay_thu/sua_doi/workflow/node/base/agent/sandbox_runtime.py, …/pydantic_ai_skills/e2b_chat_service.py).
# Capitol ghi workflow_run.error_message = "<tên khối>: An internal error occurred while processing this step." ⇒ người dùng không biết vì sao; lời thật chỉ có ở
# sự kiện execution_failed ("<tên khối> node failed: E2B (chay_thu): tên file '…' giống file khoá …"). Lời đó do mình viết bằng tiếng Việt, không chứa nội dung
# file hay bí mật ⇒ đưa NGUYÊN lời ra, nói rõ đây là từ chối đúng luật, không phải lỗi hệ thống.
_RAO_FILE = re.compile(r"(?:^|: )((?:E2B|hộp phiên) \(chay_thu[^)\n]{0,40}\)):\s*(.+)", re.S)


def lam_ro(chu) -> str | None:
    """Câu dễ hiểu nếu `chu` là chữ của lớp chặn; None nếu không phải."""
    if not isinstance(chu, str):
        return None
    for mau, truoc in ((_TRUOC, True), (_SAU, False)):
        m = mau.search(chu)
        if m:
            ma = [x.strip() for x in m.group(1).split(",") if x.strip()]
            ly = "; ".join(MA.get(x, x) for x in ma)
            if truoc:
                return f"Bị CHẶN trước khi gọi AI (0 đồng): {ly}. Sửa khối đứng trước rồi chạy lại. [mã: {', '.join(ma)}]"
            return f"Bị CHẶN sau khi AI chạy: {ly} — dừng để khối sau không chạy trên kết quả hỏng. [mã: {', '.join(ma)}]"
    # [ghi chú kỹ thuật] Rào chặn ĐÚNG mà Capitol ghi workflow_run.error_message = "Guardrails: An internal error…"
    # (25 / 25 lượt lỗi Rào là chặn thật); lý do thật chỉ có ở sự kiện execution_failed "Guardrail blocked: <lý do>" ⇒ nói rõ là bị chặn
    m = _RAO.search(chu)
    if m:
        ly = " ".join(m.group(1).split())
        return f"Bị Rào CHẶN (đúng luật, không phải lỗi hệ thống): {ly[:400]}{'…' if len(ly) > 400 else ''}"
    m = _RAO_FILE.search(chu)
    if m:
        ly = " ".join(m.group(2).split())
        noi = "máy ảo chạy code (E2B)" if m.group(1).startswith("E2B") else "hộp chạy code / tạo file"
        return f"Khối KHÔNG chạy — bị từ chối trước khi dùng {noi} (đúng luật, không phải lỗi hệ thống, chưa tốn tiền AI): {ly[:500]}{'…' if len(ly) > 500 else ''}"
    return None


def ap_dung(out: dict) -> dict:
    """Sửa TẠI CHỖ kết quả xem_workflow.mot_luot: {"luot": {...,"loi"}, "su_kien": [{"t","d",...}]}. Trả lại chính out."""
    dau = None
    for e in out.get("su_kien") or []:
        if e.get("t") == "execution_failed":
            ro = lam_ro(e.get("d"))
            if ro:
                e["d_goc"], e["d"] = e.get("d"), ro
                dau = dau or ro
    l = out.get("luot")
    if dau and isinstance(l, dict):
        l["loi_capitol"], l["loi"] = l.get("loi"), dau
    return out
