"""[ghi chú kỹ thuật] TRANG tự gọi đường TÌM của cổng RAG (127.0.0.1:19453 ⇒ app RAG mekongai /api/v1/search) cho MỘT kho của MỘT tổ chức.
Chỉ một đường: POST /api/v1/search {"collections": [kho], "query": chữ ≤ 500, "k": 1–8} + X-Rag-Ma + X-Rag-Org. Cổng tự suy user_id từ tổ chức và chỉ nhận kho mang tiền
tố của tổ chức đó ⇒ module này không khai user_id. Không gọi mô hình trả lời; bên app có tạo vector cho câu hỏi (tiền rất nhỏ, ghi SỔ BÊN HỌ — bên mình chỉ đếm số lần
tìm ở sổ của trang, may_chu._tim_tai_lieu). Bí mật RAG_MA đọc từ env tiến trình may_chu; không in, không ghi nhật ký.
  tim(org, kho, cau, k=8) ⇒ (mã HTTP, [ {page, text, file, kind, score} … ], lời lỗi | None)   — mã 0 = không nối được cổng (dịch vụ chưa bật)."""
from __future__ import annotations

import http.client
import json
import os
import re
import urllib.parse

_ORG = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_KHO = re.compile(r"CHATBOT_capitol_[0-9a-f]{32}_[A-Za-z0-9_\-]{1,200}")
CAU_TOI_DA, K_TOI_DA, TRA_TOI_DA, HET_GIO = 500, 8, 8 * 1024 * 1024, 20   # câu ≤ 500 ký tự và k ≤ 8 là luật của CỔNG (rag_cong.py); trả ≤ 8 MB phía trang


def tim(org: str, kho: str, cau: str, k: int = K_TOI_DA, goc: str | None = None, ma: str | None = None, ket_noi=None):
    goc = goc or os.environ.get("RAG_CONG_GOC") or ""
    ma = ma or os.environ.get("RAG_MA") or ""
    if not goc or not ma:
        return 0, [], "chưa nối cổng tìm tài liệu"
    ds_kho = [kho] if isinstance(kho, str) else list(kho) if isinstance(kho, (list, tuple)) else []   # [ghi chú kỹ thuật] ≤ 5 kho mỗi lời gọi (trần của cổng)
    if not _ORG.fullmatch(str(org)) or not 1 <= len(ds_kho) <= 5 or not all(isinstance(x, str) and _KHO.fullmatch(x) for x in ds_kho):
        return 400, [], "tổ chức / kho không hợp lệ"
    cau = " ".join(str(cau or "").split())[:CAU_TOI_DA]
    if not cau:
        return 400, [], "câu tìm rỗng"
    u = urllib.parse.urlsplit(goc)
    than = json.dumps({"collections": ds_kho, "query": cau, "k": max(1, min(int(k), K_TOI_DA))}, ensure_ascii=False).encode()
    try:
        c = (ket_noi or http.client.HTTPConnection)(u.hostname, u.port or 80, timeout=HET_GIO)
        c.request("POST", "/api/v1/search", body=than, headers={"Content-Type": "application/json", "X-Rag-Ma": ma, "X-Rag-Org": org})
        r = c.getresponse(); tho = r.read(TRA_TOI_DA + 1); ma_http = r.status; c.close()
    except (OSError, http.client.HTTPException) as e:
        return 0, [], f"không nối được cổng tìm tài liệu ({type(e).__name__})"
    if len(tho) > TRA_TOI_DA:
        return 502, [], f"kết quả tìm lớn hơn {TRA_TOI_DA} byte"
    try:
        d = json.loads(tho)
    except ValueError:
        return ma_http or 502, [], "cổng trả lời không phải JSON"
    if ma_http != 200:
        return ma_http, [], str((d.get("loi") or d.get("message") or "") if isinstance(d, dict) else "")[:300] or f"cổng trả mã {ma_http}"
    ds = ((d.get("data") or {}).get("pages") or []) if isinstance(d, dict) else []
    ra = []
    for x in ds if isinstance(ds, list) else []:
        if isinstance(x, dict) and isinstance(x.get("text"), str) and x["text"].strip() and isinstance(x.get("page"), int) and not isinstance(x.get("page"), bool):
            ra.append({"page": x["page"], "text": x["text"], "file": str(x.get("file") or "")[:200], "kind": str(x.get("kind") or "")[:40], "score": x.get("score")})
    return 200, ra, None
