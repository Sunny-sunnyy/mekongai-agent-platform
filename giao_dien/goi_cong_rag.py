"""goi_app cho trang 18400 (giao_dien/kho_tai_lieu.py của C) đi QUA hộp cổng RAG thay vì gọi thẳng app mekongai 127.0.0.1:1945.
B 17/11/2026 (nhóm phát triển xác nhận; C_KE_HOACH_TRANG_RAG.md mục 1–2). Chạy trong tiến trình may_chu (máy chủ, ngoài hộp).

Chỉ dịch ĐÚNG 2 đường kho_tai_lieu.bat_dau_nap dùng; đường khác ⇒ ValueError (không bao giờ /collections/all):
  GET  /api/v1/qdrant/collections/user/<uid>   ⇒ cổng GET  /api/v1/kho  (X-Rag-Ma + X-Rag-Org)
  POST /api/v1/upload-files (form user_id, language, force_reindex; tep=(tên, byte)) ⇒ cổng POST /api/v1/nap
       (X-Rag-Nap-Ma + X-Rag-Org + X-Ten-File mã hoá % + X-Nap-Lai; thân = byte file)
uid trong đường / form PHẢI = user_id cổng suy từ org (cùng công thức) ⇒ khác ⇒ ValueError trước khi gọi.
Bí mật: RAG_MA ($CAPITOL_STATE_DIR/rag_cong.env) + RAG_NAP_MA ($CAPITOL_STATE_DIR/rag_nap.env, CHỈ may_chu + hộp cổng) đọc từ env tiến trình; không in.
Kết quả: (mã HTTP, dict). Cổng từ chối TRƯỚC app (header X-Rag-Cong: tu_choi) ⇒ dict có "_cong_tu_choi": True ⇒ app chưa nhận gì, không tốn tiền.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import urllib.parse

_ORG = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DUONG_KHO = re.compile(r"/api/v1/qdrant/collections/user/(capitol_[0-9a-f]{32})/?")


def _uid(org: str) -> str:
    return "capitol_" + hashlib.sha256(org.encode()).hexdigest()[:32]


def tao_goi_app(org: str, goc: str | None = None, ma: str | None = None, ma_nap: str | None = None, ket_noi=None):
    """goi_app(phương thức, đường, form=None, tep=None, timeout=30) cho MỘT tổ chức. ket_noi(host, port, timeout) thay được khi thử."""
    org = (org or "").strip().lower()
    if not _ORG.fullmatch(org):
        raise ValueError("mã tổ chức không đúng khuôn")
    uid = _uid(org)
    u = urllib.parse.urlsplit(goc or os.environ.get("RAG_CONG_GOC", "http://127.0.0.1:19453"))
    if u.scheme != "http" or not u.hostname:
        raise ValueError("RAG_CONG_GOC phải là http://host:cổng")
    ma = os.environ.get("RAG_MA", "") if ma is None else ma
    ma_nap = os.environ.get("RAG_NAP_MA", "") if ma_nap is None else ma_nap
    ket_noi = ket_noi or (lambda h, p, t: http.client.HTTPConnection(h, p, timeout=t))

    def _goi(phuong_thuc: str, duong: str, headers: dict, than: bytes | None, timeout: float):
        c = ket_noi(u.hostname, u.port or 80, timeout)
        try:
            c.request(phuong_thuc, duong, body=than, headers=headers)
            r = c.getresponse()
            b = r.read(400 * 1024 + 1)
            tu_choi = r.getheader("X-Rag-Cong") == "tu_choi"
            code = r.status
        finally:
            c.close()
        try:
            d = json.loads(b) if b else {}
        except ValueError:
            d = {}
        d = d if isinstance(d, dict) else {"data": d}
        if tu_choi:
            d["_cong_tu_choi"] = True
        return code, d

    def goi_app(phuong_thuc: str, duong: str, form: dict | None = None, tep: tuple | None = None, timeout: float = 30):
        m = _DUONG_KHO.fullmatch(duong or "")
        if phuong_thuc == "GET" and m:
            if m.group(1) != uid:
                raise ValueError("user_id trong đường khác tổ chức của lời gọi")
            if not ma:
                return 503, {"message": "máy chủ trang chưa có RAG_MA — chưa nối cổng RAG", "_cong_tu_choi": True}
            return _goi("GET", "/api/v1/kho", {"X-Rag-Ma": ma, "X-Rag-Org": org}, None, timeout)
        if phuong_thuc == "POST" and duong in ("/api/v1/upload-files", "/api/v1/upload-files/"):
            form = form or {}
            if form.get("user_id") != uid:
                raise ValueError("user_id trong form khác tổ chức của lời gọi")
            if not (isinstance(tep, tuple) and len(tep) == 2 and isinstance(tep[0], str) and isinstance(tep[1], (bytes, bytearray))):
                raise ValueError("tep phải là (tên, byte)")
            if not ma_nap:
                return 503, {"message": "máy chủ trang chưa có RAG_NAP_MA — đường nạp đóng", "_cong_tu_choi": True}
            nl = str(form.get("force_reindex", "false")).lower()
            h = {"X-Rag-Nap-Ma": ma_nap, "X-Rag-Org": org, "X-Ten-File": urllib.parse.quote(tep[0], safe=""),
                 "X-Nap-Lai": "true" if nl == "true" else "false", "Content-Type": "application/octet-stream", "Content-Length": str(len(tep[1]))}
            return _goi("POST", "/api/v1/nap", h, bytes(tep[1]), timeout)
        raise ValueError(f"goi_cong: đường không được phép ({phuong_thuc} {str(duong)[:60]})")

    return goi_app
