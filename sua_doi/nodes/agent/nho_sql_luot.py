"""SỔ KẾT QUẢ SQL THEO LƯỢT — C 12/11/2026, đã giao ("đưa phép so số của text-to-SQL thành rào CHẶN THẬT"). BẢN SOẠN, CHƯA GẮN.
Vì sao cần: phép so nhóm kỹ thuật (kiem_so_sql.kiem_so) cần "các kết quả công cụ sql_query của lượt". Khối Rào chắn chạy SAU khối Agent, là khối khác,
chỉ nhận CHỮ qua dây ⇒ không thấy kết quả công cụ. Nếu lấy kết quả từ chữ AI viết thì AI tự làm nguồn cho mình. Nên:
  khối Agent: MỖI lần công cụ sql_query trả về, CODE (không phải AI) ghi {cau_sql, ket_qua} vào Redis của bộ chạy thử, khoá theo tổ chức + mã lượt
              (WorkflowRun.id — thuộc tính hệ thống của khối, giống cách công cụ KPI gửi X-Kpi-Run-Id).
  khối Rào chắn chế độ "sql": đọc sổ của ĐÚNG lượt mình (self._run_id + self._orgid) rồi gọi kiem_so nhóm kỹ thuật.
ĐÓNG KHI LỖI: ghi sổ hỏng ⇒ công cụ vẫn trả kết quả cho AI nhưng sổ thiếu ⇒ số trong câu trả lời không có nguồn ⇒ Rào chặn. Đọc sổ hỏng ⇒ ném ⇒ Rào chặn.
GIỚI HẠN (ghi cho D): (1) Temporal chạy lại khối Agent trong CÙNG lượt ⇒ sổ có cả kết quả của lần chạy trước (nguồn rộng hơn một chút, vẫn là kết quả
công cụ thật của lượt). (2) Ai ghi được vào Redis nội bộ thì làm giả được sổ — cùng mức tin với câu trả lời duyệt của khối Người duyệt (cũng qua Redis).
(3) Phép so chỉ chứng minh AI CHÉP ĐÚNG kết quả công cụ, không chứng minh câu SQL đúng câu hỏi (xem đầu file kiem_so_sql.py nhóm kỹ thuật).
Một nguồn luật: hàm so là nhóm kỹ thuật — chay_thu/hoi_du_lieu/kiem_so_sql.py, GẮN vào hộp ở /srv/src/nodes/agent/kiem_so_sql.py (không chép).
"""
from __future__ import annotations

import json
import re
import time

_MA = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")          # cùng dạng _MA_HE_THONG của khối Rào chắn
TTL_GIAY = 6 * 3600                                    # lượt có chờ người duyệt vẫn còn sổ; hết hạn tự xoá
TRAN_LAN = 40                                          # số lần gọi tối đa được ghi mỗi lượt (lần thứ 41 trở đi không ghi ⇒ số của nó không có nguồn)
TRAN_BYTE = 400_000                                    # một bản ghi (kết quả tối đa 200 dòng của công cụ) — lớn hơn ⇒ ghi dấu "quá cỡ", không ghi dữ liệu
# [đã soát 12/11, Đ3] LUẬT 0 DÒNG nhóm kỹ thuật (kiem_0_dong): câu SQL cuối trả rỗng mà chưa dò cột đang lọc ⇒ chưa được kết luận "không có". Phép so số không bắt
# được loại này (câu sai không có số). Ở khối Agent: LUÔN là lý do cho AI làm lại 1 lần. Ở khối Rào chắn — đã chốt sau khi D đo bằng bộ câu mới:
#   "ghi" chỉ ghi vào bảng kết quả kiểm + log, câu vẫn đi tiếp;   "nhan" gắn dòng nhãn hằng NHAN_CHUA_CHAC lên đầu câu trả lời;   "chan" dừng lượt.
# [A CHỐT 12:21 12/11] lần gắn đầu = "nhan" (không chặn oan, người đọc vẫn được báo; "ghi" để câu "không có" sai đi tiếp không dấu hiệu — không chấp nhận).
# Luật không chạy được (vd hộp còn công cụ bản 1) ở "nhan" / "chan" ⇒ khối Rào ĐÓNG (dừng lượt). Xét lại sau khi D đo trên DB giả 2 (GAN.md).
LUAT_0_DONG_O_RAO = "nhan"


def khoa(org: str, run_id: str) -> str:
    if not (_MA.match(org or "") and _MA.match(run_id or "")):
        raise ValueError("mã lượt / tổ chức rỗng hoặc sai dạng")
    return f"chay_thu:sql_luot:{org}:{run_id}"


def _client():
    from services.redis.client import RedisServiceSingleton   # lớp có sẵn của Capitol (khối Người duyệt cũng dùng)

    return RedisServiceSingleton()._get_client()


def dong_goi(cau_sql: str, ket_qua) -> str:
    """Một bản ghi dạng chữ JSON. Quá cỡ / không đổi được ra JSON ⇒ bản ghi "không dùng làm nguồn" (ok = False)."""
    try:
        # "luc": giờ ghi (giây) — để Dấu vết đối chiếu sổ với message_history trong PG (D gợi ý); không dùng cho phép so
        s = json.dumps({"cau_sql": str(cau_sql or "")[:8000], "ket_qua": ket_qua, "luc": round(time.time(), 3)}, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = ""
    if not s or len(s.encode()) > TRAN_BYTE:
        s = json.dumps({"cau_sql": str(cau_sql or "")[:8000], "ket_qua": {"ok": False, "loai": "khong_ghi_duoc"}}, ensure_ascii=False)
    return s


def mo_goi(dong) -> dict | None:
    try:
        d = json.loads(dong.decode() if isinstance(dong, (bytes, bytearray)) else dong)
    except (ValueError, AttributeError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("cau_sql"), str) else None


async def ghi(org: str, run_id: str, cau_sql: str, ket_qua, client=None) -> bool:
    """Gọi từ công cụ sql_query (khối Agent) sau MỖI lần chạy. Không ném: lỗi ⇒ False (sổ thiếu ⇒ Rào chặn)."""
    try:
        k = khoa(org, run_id)
        c = client or _client()
        if int(await c.llen(k) or 0) >= TRAN_LAN:
            return False
        await c.rpush(k, dong_goi(cau_sql, ket_qua))
        await c.expire(k, TTL_GIAY)
        return True
    except Exception:  # noqa: BLE001
        return False


async def doc(org: str, run_id: str, client=None) -> list[dict]:
    """Gọi từ khối Rào chắn / khối Agent tự kiểm. NÉM khi không đọc được (người gọi đóng khi lỗi). Bản ghi hỏng ⇒ ném (không lặng bỏ)."""
    c = client or _client()
    ra = []
    for dong in await c.lrange(khoa(org, run_id), 0, TRAN_LAN - 1):
        d = mo_goi(dong)
        if d is None:
            raise ValueError("sổ kết quả SQL của lượt có bản ghi hỏng")
        ra.append(d)
    return ra


def kiem(cau_tra_loi: str, ban_ghi: list[dict], cau_hoi: str = "") -> dict:
    """Phép so nhóm kỹ thuật trên sổ của lượt ⇒ {"dat", "so_la", "so_kiem", "ly_do", "so_lan_sql", "so_lan_ok"}. Hàm THUẦN (thử được ngoài hộp)."""
    try:
        from nodes.agent.kiem_so_sql import kiem_so       # trong hộp: file nhóm kỹ thuật gắn vào đây
    except ImportError:                                    # ngoài hộp (bộ thử): chay_thu/hoi_du_lieu ở sys.path
        from kiem_so_sql import kiem_so
    kq = [b.get("ket_qua") for b in ban_ghi if isinstance(b.get("ket_qua"), dict)]
    r = dict(kiem_so(cau_tra_loi or "", kq, cau_hoi or "", [b["cau_sql"] for b in ban_ghi]))
    r.update(so_lan_sql=len(ban_ghi), so_lan_ok=sum(1 for x in kq if x.get("ok") is True))
    # luật 0 dòng: hai danh sách CÙNG THỨ TỰ gọi (bản ghi không có kết quả dạng dict ⇒ {} để giữ vị trí). Không nhập / không chạy được ⇒ ghi rõ, KHÔNG coi là đạt.
    try:
        fn, nhan = _luat_0_dong()
        k0 = dict(fn([b["cau_sql"] for b in ban_ghi], [b["ket_qua"] if isinstance(b.get("ket_qua"), dict) else {} for b in ban_ghi]))
        k0["nhan"] = nhan
    except Exception as e:  # noqa: BLE001
        k0 = {"dat": None, "ly_do": f"không chạy được luật 0 dòng ({type(e).__name__})", "chi_dan": "", "cot_chua_do": [], "nhan": ""}
    r["luat_0_dong"] = k0
    return r


def _luat_0_dong():
    """(hàm kiem_0_dong, nhãn hằng) nhóm kỹ thuật — một nguồn ở file công cụ sql_query bản 2. Trong hộp: nodes.agent.sql_query; ngoài hộp: qua kiem_so_sql nhóm kỹ thuật."""
    try:
        from nodes.agent.sql_query import NHAN_CHUA_CHAC, kiem_0_dong
    except ImportError:
        from kiem_so_sql import NHAN_CHUA_CHAC, kiem_0_dong
    return kiem_0_dong, NHAN_CHUA_CHAC


def ly_do_0_dong(kq: dict) -> list[str]:
    """Lý do + chỉ dẫn của luật 0 dòng (rỗng = đạt / không áp dụng / không chạy được luật — trường hợp sau đã ghi ở kq["luat_0_dong"]["ly_do"])."""
    k0 = kq.get("luat_0_dong") or {}
    if k0.get("dat") is not False:
        return []
    return [(str(k0.get("ly_do") or "câu truy vấn cuối trả 0 dòng nhưng chưa dò dữ liệu")[:300] + (" " + str(k0.get("chi_dan"))[:500] if k0.get("chi_dan") else ""))]


def ly_do_lam_lai(kq: dict) -> list[str]:
    """Cho khối Agent tự kiểm: CẢ HAI luật đều là lý do làm lại 1 lần."""
    return ly_do(kq) + ly_do_0_dong(kq)


def ly_do(kq: dict) -> list[str]:
    """Rỗng = đạt. Lý do lời thường, có kèm tối đa 8 số lạ (số do AI viết trong câu trả lời — không phải dữ liệu khách)."""
    if kq.get("dat") is True:
        return []
    so = ", ".join(str(x)[:30] for x in (kq.get("so_la") or [])[:8])
    return [(str(kq.get("ly_do") or "câu trả lời không qua phép so với kết quả SQL")[:300] + (f" — số không có nguồn: {so}" if so else ""))]
