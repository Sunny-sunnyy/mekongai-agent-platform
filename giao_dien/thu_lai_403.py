"""THỬ LẠI ĐÚNG 1 LẦN khi run-workflow trả HTTP 403 (đối chiếu kỹ thuật). BẢN SOẠN, CHƯA GẮN.

Vì sao có 403 thất thường (hành vi GỐC của Capitol, D: api/v1/deps.py:669–674, workflows.py:300–316, workflow_access.py:638–641, deps.py:132–136):
lưu workflow cấp quyền bằng flush, commit nằm SAU `yield` của dependency phiên DB; run-workflow kiểm quyền bằng dependency trên phiên DB KHÁC ⇒ gọi ngay
sau khi lưu thì có lúc chưa thấy dòng quyền ⇒ 403 "You do not have access to this resource". Dependency chặn TRƯỚC thân hàm ⇒ 403 = CHƯA tạo lượt
⇒ gọi lại không sợ chạy đôi.
Luật: CHỈ mã 403; đúng MỘT lần thử lại sau ~1,5 giây; khoản giữ chỗ của người gọi GIỮ NGUYÊN (hàm này không đụng tiền); ghi 1 dòng nhật ký có `detail`;
vẫn 403 ⇒ trả nguyên kết quả lần hai cho người gọi xử lý như cũ (trả lỗi + trả chỗ). Mọi mã khác / ngoại lệ ⇒ không thử lại, để nguyên cho người gọi."""
from __future__ import annotations

import time

CHO_GIAY = 1.5


def _da_co_luot(co_luot) -> bool:
    """Postgres đã có workflow_run của phiên này chưa. Không đọc được ⇒ coi như ĐÃ CÓ (đóng: không thử lại, người gọi không trả chỗ)."""
    try:
        return bool(co_luot())
    except Exception:  # noqa: BLE001
        return True


def goi_run_workflow(goi, co_luot, *, ghi=lambda s: None, ngu=time.sleep, nhan: str = "") -> tuple[int, object, bool]:
    """goi() ⇒ (mã HTTP, thân) — một lần gọi run-workflow. co_luot() ⇒ True nếu Postgres ĐÃ có workflow_run của đúng phiên này.
    Trả (mã, thân, da_co_luot) của lần gọi CUỐI. [ghi chú kỹ thuật] D mới đọc chắc dependency kiểm quyền chặn trước thân hàm; bộ xử lý
    api/v1/handlers/run.py còn 9 chỗ nhắc 403 CHƯA đọc ⇒ KHÔNG giả định "mọi 403 đều chưa tạo lượt":
      403 + PG CHƯA có lượt ⇒ thử lại đúng 1 lần;   403 + PG ĐÃ có lượt ⇒ KHÔNG thử lại, da_co_luot = True (người gọi coi như lượt đã khởi động:
      theo dõi ghi sổ, KHÔNG trả chỗ);   vẫn 403 sau lần thử lại ⇒ kiểm PG lần nữa, người gọi chỉ trả chỗ khi da_co_luot = False.
    Ngoại lệ của goi() (hết giờ, mất kết nối) KHÔNG bị bắt ở đây — người gọi giữ nguyên cách xử lý "Capitol có thể đã chạy"."""
    code, r = goi()
    if code != 403:
        return code, r, False
    chi_tiet = str((r.get("detail") if isinstance(r, dict) else r) or "")[:200]
    if _da_co_luot(co_luot):
        ghi(f"CHẠY {nhan}: run-workflow trả 403 ({chi_tiet}) NHƯNG Postgres đã có lượt của phiên này — không thử lại, coi như lượt đã khởi động (giữ chỗ, theo dõi ghi sổ)")
        return code, r, True
    ghi(f"CHẠY {nhan}: run-workflow trả 403 ({chi_tiet}), Postgres chưa có lượt — quyền vừa cấp có thể chưa commit ở phiên DB khác; thử lại 1 lần sau {CHO_GIAY} giây (khoản giữ chỗ giữ nguyên)")
    ngu(CHO_GIAY)
    code2, r2 = goi()
    da_co = _da_co_luot(co_luot) if code2 == 403 else False
    ghi(f"CHẠY {nhan}: lần thử lại sau 403 ⇒ HTTP {code2}" + (" — Postgres ĐÃ có lượt, coi như đã khởi động" if da_co else ""))
    return code2, r2, da_co
