"""[ghi chú kỹ thuật] CACHE CÂU TRẢ LỜI trợ lý KPI cho trang 18400. Kế hoạch + soát: tài liệu thiết kế nội bộ
B_KE_HOACH_CACHE_TRA_LOI.md (đối chiếu kỹ thuật).

Cách: câu y hệt (sau chuẩn hoá) + cùng ngữ cảnh ⇒ trả lại MÃ PHIÊN của lượt cũ (trang đọc trạng thái phiên cũ như thường) ⇒ 0 lượt mới, 0 gọi AI.
Module THUẦN (không I/O): may_chu đưa vào các hàm đọc (vân tay, kiểm phiên). Giữ trong bộ nhớ may_chu ⇒ khởi động lại là rỗng (chiều an toàn).
GIỚI HẠN: chỉ khớp câu Y HỆT (sau chuẩn hoá chữ) — cùng nghĩa khác chữ ("NV02 thưởng bao nhiêu" ↔ "thưởng của NV02") KHÔNG khớp; số viết khác
cách ("8.540.000" ↔ "8540000", "tháng 10" ↔ "T10") cũng không khớp (đối chiếu kỹ thuật) — đúng ý "y hệt", lệch về phía chạy lại.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from collections import OrderedDict

HAN_GIAY = 15 * 60          # người dùng: "khoảng 10 đến 15p" ⇒ 15 phút
TOI_DA_MUC = 200
_DAU_CUOI = re.compile(r"[\s.?!…,;:]+$")


def chuan_hoa(cau) -> str:
    """NFC + chữ thường + gộp khoảng trắng + bỏ dấu câu ở CUỐI. GIỮ dấu tiếng Việt và mọi chữ / số ⇒ khác 1 chữ là khác khoá."""
    s = unicodedata.normalize("NFC", str(cau or "")).lower()
    s = re.sub(r"\s+", " ", s).strip()
    return _DAU_CUOI.sub("", s)


def khoa(*, loi_vao: str, cau: str, truoc, data_version: str, van_tay: dict, workflow_id: str, so_phien_ban, to_chuc: str) -> str:
    """SHA-256 của mọi thứ quyết định câu trả lời. van_tay = vân tay HỘP KPI lúc hỏi (/van-tay 18402: phần 'tinh' — kpi_head, ds_sha256,
    dữ liệu — + phần 'dong' — rule_version, workflow_version, files_sha256, cutoff, period…) ⇒ dựng lại hộp (đổi code / dữ liệu / luật) ⇒ khoá
    khác ⇒ không dùng cache cũ (đối chiếu kỹ thuật). data_version = bản THỰC DÙNG lấy LÚC HỎI (bản gửi lên, không có thì latest — E minor 2)."""
    goi = {"loi_vao": loi_vao, "cau": chuan_hoa(cau), "truoc": [chuan_hoa(x) for x in (truoc or [])],
           "data_version": str(data_version or ""), "van_tay": van_tay or {}, "workflow_id": str(workflow_id or ""),
           "so_phien_ban": str(so_phien_ban or ""), "to_chuc": str(to_chuc or "")}
    return hashlib.sha256(json.dumps(goi, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class KhoCache:
    """{khoá: (phiên, lúc tạo monotonic, giờ hiển thị)}. Hạn tính từ lúc KHỞI lượt cũ (≤ 15 phút + thời gian lượt chạy: an toàn hơn "từ lúc
    xong"). Đồng hồ: time.monotonic (đồng hồ WSL nhảy tới ~10 s — 16/11)."""

    def __init__(self, han=HAN_GIAY, toi_da=TOI_DA_MUC, dong_ho=time.monotonic, gio_hien=lambda: time.strftime("%H:%M")):
        self._d: OrderedDict[str, tuple[str, float, str]] = OrderedDict()
        self._khoa = threading.Lock()
        self.han, self.toi_da, self.dong_ho, self.gio_hien = han, toi_da, dong_ho, gio_hien

    def ghi(self, k: str, phien: str) -> None:
        """Gọi khi KHỞI một lượt mới thành công (đã có mã phiên). Lượt hỏng / bị Rào chặn sẽ bị loại lúc dùng (kiem_phien)."""
        with self._khoa:
            self._d[k] = (phien, self.dong_ho(), self.gio_hien())
            self._d.move_to_end(k)
            while len(self._d) > self.toi_da:
                self._d.popitem(last=False)

    def lay(self, k: str, kiem_phien) -> dict | None:
        """Trả {"session", "luc"} nếu có mục còn hạn VÀ kiem_phien(phiên) ⇒ True (lượt SUCCESS + Rào chế độ code ĐẠT + không chờ duyệt);
        không ⇒ None (và bỏ mục hết hạn / không đạt). kiem_phien lỗi ⇒ None (chạy lượt mới — chiều an toàn)."""
        with self._khoa:
            m = self._d.get(k)
            if m is None:
                return None
            phien, t0, luc = m
            if self.dong_ho() - t0 > self.han:
                self._d.pop(k, None)
                return None
        try:
            ok = bool(kiem_phien(phien))
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            return None   # KHÔNG bỏ mục: lượt có thể đang chạy (chưa SUCCESS) — câu sau trong hạn kiểm lại
        return {"session": phien, "luc": luc}

    def xoa_het(self, ly_do: str = "") -> int:
        """Tải dữ liệu mới / duyệt / đổi bản mặc định ⇒ xoá hết. Trả số mục đã xoá."""
        with self._khoa:
            n = len(self._d)
            self._d.clear()
            return n


# SQL kiểm phiên cũ (may_chu chạy bằng hàm sql chỉ đọc của nó). Đạt khi: lượt mới nhất của phiên SUCCESS, có ÍT NHẤT một khối Rào chế độ code
# với data.kiem.dat = true (guardrails/node.py:397-399 ghi output_data.data.kiem), KHÔNG khối Rào nào có kiem.dat = false, và không còn khối
# Người duyệt nào chờ (awaiting_intervention chưa có kết quả) — E minor 1.
def sql_kiem_phien(phien: str) -> str:
    assert re.fullmatch(r"[0-9a-f-]{36}", phien or ""), "mã phiên sai dạng"
    return f"""select row_to_json(x) from (select
      (select status::text from workflow_run where session_id='{phien}' order by created_at desc limit 1) as tt,
      (select count(*) from node_result nr join workflow_run r on r.id=nr.run_id where r.session_id='{phien}'
         and (nr.output_data::jsonb->'data'->'kiem'->>'dat') = 'true') as rao_dat,
      (select count(*) from node_result nr join workflow_run r on r.id=nr.run_id where r.session_id='{phien}'
         and (nr.output_data::jsonb->'data'->'kiem'->>'dat') = 'false') as rao_truot,
      (select count(*) from workflow_event e join workflow_run r on r.id=e.run_id where r.session_id='{phien}'
         and e.event_type='awaiting_intervention') as cho_duyet,
      (select count(*) from node_result nr join workflow_run r on r.id=nr.run_id where r.session_id='{phien}'
         and nr.status::text = 'COMPLETED') as xong) x"""


def dat_kiem_phien(dong: dict | None) -> bool:
    """Từ kết quả sql_kiem_phien. Có khối Người duyệt (cho_duyet > 0) ⇒ KHÔNG dùng lại (duyệt là quyết định của người, không cache)."""
    d = dong or {}
    return (d.get("tt") == "SUCCESS" and int(d.get("rao_dat") or 0) >= 1 and int(d.get("rao_truot") or 0) == 0
            and int(d.get("cho_duyet") or 0) == 0)
