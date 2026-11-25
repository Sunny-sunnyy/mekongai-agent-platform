"""GHI SỔ TIỀN cho khối chạy trong hộp phiên (đối chiếu kỹ thuật). BẢN SOẠN, CHƯA GẮN.

Lỗ: trang ghi sổ một lượt bằng tien_luot (sandbox/chay_s5.py) = chỉ các lần gọi AI có trong message_history. Khối PowerPoint / Word / Excel-chat / chạy code
(hộp phiên) không để lại message_history — tiền của chúng chỉ có ở dòng log `[CACHE_USAGE] node_type=skills:…` (không mang mã lượt) ⇒ lượt bấm Chạy
trên trang THIẾU khoản này trong sổ (~0,008–0,026 USD / lượt), khối hỏng vì chạm trần còn không có dòng log. Giữ chỗ TRƯỚC khi chạy vốn đúng.
Vá (nhóm phát triển xác nhận — đơn giản, lệch phía an toàn): lượt CÓ khối loại này ⇒ ghi THÊM một dòng sổ = TỔNG MỨC TỐI ĐA của các khối đó (cùng hàm mức với lúc giữ chỗ
⇒ tổng ghi của lượt ≤ khoản đã giữ, nếu phần message_history không vượt mức của các khối còn lại). KHÔNG đo từ log (log không gán chắc được cho lượt khi
chạy song song). Ghi cả khi phần chính đo được 0 (lượt hỏng sớm). Đúng MỘT lần / phiên (kiểm trùng theo session + nhãn).
MỘT luồng, đúng thứ tự:  chờ lượt kết thúc ⇒ ghi tiền message_history như cũ (H.ghi_tien_mot_lan) ⇒ ghi dòng hộp phiên ⇒ SAU CÙNG mới trả chỗ đã giữ
(không có lúc nào lượt vắng cả sổ lẫn bảng giữ chỗ; ghi sổ lỗi ⇒ KHÔNG trả chỗ). Workflow không có khối loại này ⇒ may_chu gọi H.theo_doi_ghi_tien như cũ.
GIỚI HẠN: ghi DƯ so với tiền thật (đo hôm nay một lượt PowerPoint thật ≈ 0,008–0,026, mức tối đa một khối 0,075) — chấp nhận để trần ngày không hở.
"""
from __future__ import annotations

import json
import threading
import time

KHOI_HOP_PHIEN = ("pptx_chat_node", "docx_chat_node", "xlsx_chat_node", "flexible_code_interpreter_node", "skill_code_interpreter_node")
NHAN_SO = "trang_18400_hop_phien"
KET = ("SUCCESS", "FAILED", "STOPPED", "CANCELLED")


def khoi_hop_phien(payload) -> list[dict]:
    """Các khối chạy trong hộp phiên của workflow: [struct]. Payload hỏng ⇒ []."""
    ra = []
    for n in ((payload or {}).get("nodes") or []) if isinstance(payload, dict) else []:
        d = n.get("data") if isinstance(n, dict) else None
        st = d.get("struct") if isinstance(d, dict) else None
        if isinstance(st, dict) and st.get("node_id") in KHOI_HOP_PHIEN:
            ra.append(st)
    return ra


def muc_hop_phien(payload, muc_khoi) -> tuple[float, int]:
    """(tổng mức tối đa của các khối hộp phiên, số khối). muc_khoi(struct) = muc_toi_da._muc_khoi; None (khối vốn bị chặn chạy) ⇒ 0."""
    ks = khoi_hop_phien(payload)
    return round(sum(float(muc_khoi(st) or 0) for st in ks), 6), len(ks)


def da_ghi(H, s: str) -> bool:
    """Sổ đã có dòng hộp phiên của phiên này chưa (session + nhãn). Không đọc được sổ ⇒ ném (người gọi không trả chỗ)."""
    if not H.SO.is_file():
        return False
    return any(x.get("session") == s and x.get("nhan") == NHAN_SO for x in json.loads(H.SO.read_text(encoding="utf-8")).get("luot", []))


def _nen_giu_mac_dinh(s: str) -> bool:
    from pathlib import Path as _P
    import importlib.util as _iu   # [ghi chú kỹ thuật] nạp theo ĐƯỜNG DẪN TUYỆT ĐỐI (không dựa sys.path[0] = giao_dien/ — script khác gọi cũng nạp được)
    _sp = _iu.spec_from_file_location("hoi_temporal", _P(__file__).resolve().parent / "hoi_temporal.py"); _m = _iu.module_from_spec(_sp); _sp.loader.exec_module(_m)
    return _m.nen_giu_cho(s)


def _ghi_luot_mat_dong(H, s: str, nhan: str, ghi_chu: str, ma_giu, loai: str, ghi=lambda x: None) -> None:
    """[ghi chú kỹ thuật] Lượt KHÔNG có dòng trong cơ sở dữ liệu của Capitol mà Temporal báo đã kết thúc ⇒ ghi ĐÚNG MỘT dòng sổ rồi trả chỗ ngay (trước: chỗ giữ treo
    tới 3 giờ). loai = "khong_khoi" (lịch sử Temporal — nguồn độc lập với cơ sở dữ liệu — cho thấy 0 khối được chạy) ⇒ 0 đồng; còn lại ⇒ MỨC ĐÃ GIỮ CHỖ của lượt
    (cận trên — tiền đếm được bằng 0 ở đây chỉ vì không có dòng để đếm). Ghi sổ lỗi (ngoại lệ) ⇒ KHÔNG tới dòng trả chỗ: chỗ giữ còn chặn thay."""
    gc = ghi_chu + " (lượt không có dòng trong cơ sở dữ liệu của Capitol — Temporal báo đã kết thúc)"
    t = H.ghi_tien_mot_lan(s, nhan, gc)
    giu = float((H.dang_giu().get(ma_giu) or {}).get("usd") or 0) if ma_giu else 0.0
    if loai == "khong_khoi":
        usd, them = 0.0, " — 0 đồng: lịch sử Temporal cho thấy chưa khối nào được chạy"
    else:
        usd, them = max(giu, float((t or {}).get("usd") or 0)), " — ghi MỨC ĐÃ GIỮ CHỖ (cận trên): lịch sử Temporal cho thấy đã có khối chạy hoặc không đọc được, không đo được tiền thật"
    with H._khoa_so:   # ghi_tien_mot_lan không ghi gì khi không đếm được ⇒ thêm đúng MỘT dòng (mọi lượt qua trang có dòng sổ)
        da_co = H.SO.is_file() and any(x.get("session") == s for x in json.loads(H.SO.read_text(encoding="utf-8")).get("luot", []))
        if not da_co:
            H.ghi_tien(nhan, usd, {"session": s, "ghi_chu": gc + them})
    ghi(f"SỔ lượt {s}: mất dòng (Temporal đã kết thúc, cơ sở dữ liệu không có dòng) ⇒ đã ghi sổ + trả chỗ {ma_giu}")
    H.tra_cho(ma_giu)


def _hoi_mat_dong(H, s: str, mat_dong=None):
    """None | "khong_khoi" | "co_khoi". Mặc định dùng CHÍNH hàm của hoi_kpi (H._mat_dong_mac_dinh — hỏi Temporal + hỏi lại cơ sở dữ liệu); hoi_kpi bản cũ chưa có
    hàm đó ⇒ None (tắt, như trước gói này)."""
    f = mat_dong or getattr(H, "_mat_dong_mac_dinh", None)
    return f(s) if f else None


def theo_doi(s: str, payload, *, H, trang_thai, muc_khoi, nhan: str, ghi_chu: str, ma_giu, ghi=lambda x: None,
             toi_da_giay: int = 3 * 3600, ngu=time.sleep, gio=time.time, nen: bool = True, nen_giu=None, mat_dong=None):
    """Thay H.theo_doi_ghi_tien cho lượt CÓ khối hộp phiên. H: module hoi_kpi (ghi_tien_mot_lan, ghi_tien, tra_cho, _khoa_so, SO).
    trang_thai(phiên) ⇒ chữ trạng thái | None."""
    def lam():
        t0, qua = gio(), False
        hoi_ke = t0 + getattr(H, "MAT_DONG_SAU_GIAY", 20)
        while True:
            tt = trang_thai(s)
            if tt in KET:
                ngu(3)
                break
            if gio() - t0 >= toi_da_giay:
                qua = True
                break
            # [ghi chú kỹ thuật] KHÔNG có dòng lượt (trạng thái rỗng) quá MAT_DONG_SAU_GIAY ⇒ hỏi Temporal (mỗi MAT_DONG_CACH_GIAY); chắc "mất dòng"
            # ⇒ ghi MỘT dòng sổ + trả chỗ ngay. Có dòng rồi (RUNNING / PAUSED…) thì KHÔNG hỏi. Còn chạy / không hỏi được ⇒ chờ tiếp như cũ.
            # Lượt mất dòng không có khối nào được ghi nhận ⇒ không có dòng riêng "mức tối đa của khối hộp phiên": "co_khoi" đã ghi MỨC GIỮ CHỖ của cả lượt (gồm các khối đó).
            if not tt and gio() >= hoi_ke:
                hoi_ke = gio() + getattr(H, "MAT_DONG_CACH_GIAY", 30)
                loai = _hoi_mat_dong(H, s, mat_dong)
                if loai:
                    _ghi_luot_mat_dong(H, s, nhan, ghi_chu, ma_giu, loai, ghi)
                    return
            ngu(5)
        if qua:   # [ghi chú kỹ thuật] hỏi Temporal trước; còn chạy / không hỏi được ⇒ KHÔNG ghi, KHÔNG trả (giữ chỗ — chay_s5 sẽ ghi cận trên khi chỗ quá hạn (đối chiếu kỹ thuật))
            if ma_giu and (nen_giu or _nen_giu_mac_dinh)(s):   # [ghi chú kỹ thuật] không có chỗ giữ ⇒ ghi như cũ
                ghi(f"SỔ hộp phiên {s}: quá giờ theo dõi nhưng Temporal còn chạy / không hỏi được ⇒ GIỮ chỗ (chay_s5 ghi cận trên khi quá hạn)")
                return
        # Ghi sổ lỗi (ngoại lệ ở bất kỳ bước nào dưới đây) ⇒ KHÔNG tới dòng trả chỗ: chỗ giữ còn chặn thay (như hoi_kpi.theo_doi_ghi_tien)
        t = H.ghi_tien_mot_lan(s, nhan, ghi_chu + (" (quá giờ theo dõi — Temporal báo đã xong — ghi phần đã tiêu)" if qua else ""))
        # [ghi chú kỹ thuật] tien_luot bản mới đã cộng MỨC TỐI ĐA cho khối không có lịch sử (gồm khối hộp phiên) ⇒ không ghi dòng riêng nữa
        # (tránh đếm 2 lần). Bản cũ (không có khoá muc_khoi_khong_lich_su) ⇒ giữ dòng riêng như trước.
        if isinstance(t, dict) and "muc_khoi_khong_lich_su" in t:
            H.tra_cho(ma_giu)
            return
        usd, so_khoi = muc_hop_phien(payload, muc_khoi)
        if usd > 0:
            with H._khoa_so:
                if not da_ghi(H, s):
                    H.ghi_tien(NHAN_SO, usd, {"session": s, "ghi_chu": f"{so_khoi} khối chạy trong hộp phiên (PowerPoint / Word / Excel / chạy code) — ghi MỨC TỐI ĐA, "
                                                                        "không đo được theo lượt (log [CACHE_USAGE] không mang mã lượt)"})
                    ghi(f"SỔ hộp phiên {s}: +{usd:.6f} USD (mức tối đa của {so_khoi} khối)")
        H.tra_cho(ma_giu)
    def co_nhat_ky():   # [ghi chú kỹ thuật] luồng chết lặng (PG rớt, ghi sổ lỗi…) ⇒ để lại 1 dòng nhật ký; vẫn KHÔNG trả chỗ (ném tiếp)
        try:
            lam()
        except Exception as e:
            ghi(f"SỔ hộp phiên {s}: luồng theo dõi dừng vì {type(e).__name__} — CHƯA ghi đủ sổ, CHƯA trả chỗ đã giữ (kiểm sổ + bảng giữ chỗ)")
            raise
    if nen:
        threading.Thread(target=co_nhat_ky, daemon=True).start()
    else:
        co_nhat_ky()
