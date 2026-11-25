"""[nhóm kỹ thuật, 06/11/2026 — đã giao việc 5] Máy chủ MCP CỦA MÌNH: 2 công cụ CHỈ ĐỌC trên dữ liệu giả của shop, HTTP streamable, có khoá riêng.

Vì sao làm: học cách gắn công cụ NGOÀI lõi qua MCP như Capitol (dòng 8 bản đồ "tái tạo đầy đủ", chay_thu/DE_XUAT_DUNG_THEM.md mục 7).
Khác công cụ gọi thẳng (native_tools.py): máy chủ là tiến trình / hộp riêng, nói giao thức MCP qua HTTP ⇒ một máy chủ dùng chung được cho nhiều
khối, nhiều hệ thống (Capitol, mekongai…), đổi công cụ không phải sửa lõi. Cái giá: thêm một hộp, một cổng, một khoá phải giữ.

Dựng giống Capitol (mcp-servers: mỗi máy chủ một FastMCP, `fastmcp run … --transport http`, supervisord.conf:142) nhưng KHÁC ở chỗ kiểm khoá:
máy chủ của Capitol không tự kiểm khoá — chỉ đọc X-Organization-ID / X-User-Token do agentic-backend gắn (servers/server_utils.py:23-34), dựa vào
mạng nội bộ. Ở đây máy chủ TỰ kiểm header X-Shop-Key (so hằng thời gian), sai / thiếu ⇒ 401 trước khi tới MCP.

Chạy:  SHOP_MCP_KEY=<khoá ≥ 16 ký tự> SHOP_MCP_DB=<file sqlite> python -I may_chu.py      (SHOP_MCP_HOST mặc định 127.0.0.1, SHOP_MCP_PORT 8765)
Đường MCP: http://<host>:<port>/mcp
"""

import hmac
from contextlib import closing
import json
import logging
import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

logger = logging.getLogger("shop_mcp")
DB = Path(os.environ.get("SHOP_MCP_DB", "/srv/du_lieu_gia.sqlite"))
TEN_HEADER = b"x-shop-key"                 # ASGI đưa tên header ở dạng chữ thường
TRAN_TEN = 100

mcp = FastMCP(name="shop-du-lieu-gia",
              instructions="Dữ liệu GIẢ của shop thời trang (để thử). Chỉ đọc: bảng giá và tồn kho theo size.")


def _mo() -> sqlite3.Connection:
    # Chỉ đọc 2 lớp: mở file mode=ro (SQLite từ chối ghi) + query_only. Câu SQL viết cứng trong code, tham số ràng buộc `?` — AI không viết SQL.
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.execute("PRAGMA query_only=ON")
    return c


def _chuan(s: str) -> str:
    """So tên không phân biệt hoa thường (casefold của Python đúng cả chữ có dấu: "Áo" ↔ "áo"), bỏ khoảng trắng thừa, chuẩn NFC."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().casefold()


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def bang_gia() -> dict:
    """Bảng giá bán của shop: mọi sản phẩm, tên đúng như trong dữ liệu, kèm giá bán (đồng). Chỉ đọc.
    Gọi trước khi tra tồn kho để lấy đúng tên sản phẩm."""
    try:
        with closing(_mo()) as c:
            rows = c.execute("SELECT product, gia_vnd FROM san_pham ORDER BY product").fetchall()
    except sqlite3.Error as e:
        logger.warning("bang_gia: lỗi đọc dữ liệu %s", type(e).__name__)
        return {"ok": False, "loai": "loi_he_thong", "error": "Không đọc được dữ liệu — báo người vận hành, đừng đoán giá."}
    return {"ok": True, "so_san_pham": len(rows), "san_pham": [{"product": p, "gia_vnd": g} for p, g in rows]}


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def ton_kho(san_pham: Annotated[str, Field(description="tên MỘT sản phẩm, đúng như trong bảng giá (không phân biệt hoa thường, bỏ khoảng trắng thừa)")]) -> dict:
    """Tồn kho theo từng size của MỘT sản phẩm, kèm tổng số lượng (hệ thống cộng, đừng tự cộng). Chỉ đọc.
    Tên không có trong bảng giá thì trả lỗi, không đoán."""
    if not isinstance(san_pham, str) or not (0 < len(san_pham) <= TRAN_TEN) or any(unicodedata.category(ch)[0] == "C" for ch in san_pham):
        return {"ok": False, "loai": "tham_so", "error": f"Tên sản phẩm phải là một chuỗi chữ dài 1–{TRAN_TEN} ký tự, không ký tự điều khiển."}
    try:
        with closing(_mo()) as c:
            # Danh sách CHO PHÉP: chỉ nhận đúng một tên đang có trong bảng giá ⇒ "../", "' OR 1=1" … không khớp tên nào ⇒ từ chối.
            ten = {_chuan(p): p for (p,) in c.execute("SELECT product FROM san_pham")}
            that = ten.get(_chuan(san_pham))
            if that is None:
                return {"ok": False, "loai": "khong_co", "error": "Không có sản phẩm này trong bảng giá — gọi bang_gia để lấy đúng tên, đừng đoán."}
            rows = c.execute("SELECT size, so_luong FROM ton_kho WHERE product = ? ORDER BY size", (that,)).fetchall()
    except sqlite3.Error as e:
        logger.warning("ton_kho: lỗi đọc dữ liệu %s", type(e).__name__)
        return {"ok": False, "loai": "loi_he_thong", "error": "Không đọc được dữ liệu — báo người vận hành, đừng đoán số."}
    return {"ok": True, "product": that, "ton_theo_size": [{"size": s, "so_luong": n} for s, n in rows],
            "tong_so_luong": sum(n for _s, n in rows)}


class KiemKhoa:
    """Lớp ASGI bọc ngoài app MCP: mọi yêu cầu HTTP phải có header X-Shop-Key đúng, không thì 401 — MCP không được chạm tới.
    So bằng hmac.compare_digest (thời gian không lộ khoá đúng tới đâu). Log không bao giờ ghi giá trị khoá."""

    def __init__(self, app, khoa: bytes):
        self.app, self.khoa = app, khoa

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            co = [v for k, v in scope.get("headers", []) if k == TEN_HEADER]
            if len(co) != 1 or not hmac.compare_digest(co[0], self.khoa):
                logger.warning("401 %s %s: %s", scope.get("method"), scope.get("path"), "thiếu khoá" if not co else "sai khoá")
                body = json.dumps({"error": "Thiếu hoặc sai khoá X-Shop-Key."}, ensure_ascii=False).encode()
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"www-authenticate", b"X-Shop-Key")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    khoa = os.environ.get("SHOP_MCP_KEY", "")
    if len(khoa) < 16:
        sys.exit("Thiếu SHOP_MCP_KEY (≥ 16 ký tự) — không chạy máy chủ không khoá.")
    if not DB.is_file():
        sys.exit(f"Không thấy file dữ liệu {DB}.")
    import uvicorn
    app = KiemKhoa(mcp.http_app(path="/mcp"), khoa.encode())
    uvicorn.run(app, host=os.environ.get("SHOP_MCP_HOST", "127.0.0.1"), port=int(os.environ.get("SHOP_MCP_PORT", "8765")), log_level="warning")


if __name__ == "__main__":
    main()
