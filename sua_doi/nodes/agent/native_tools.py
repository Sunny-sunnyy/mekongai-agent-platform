"""[VÁ 8 chay_thu, 04/11/2026 — nhóm kỹ thuật, User yêu cầu] Công cụ GỌI THẲNG hàm Python cho khối Agent (không qua MCP).

Vì sao không qua MCP: workflow này do mình tự dựng, tự dùng — một tiến trình, một chỗ cấu hình; MCP chỉ thêm tiến trình, độ trễ và
xác thực (bên mekongai cũng chọn gọi thẳng). Cắm vào agent qua hook có sẵn của Capitol `_compose_agent_tools()`
(workflow/node/base/agent/node.py:387, chính họ cắm công cụ nội bộ report_input_issue theo cách này).

Dựng lại từ công cụ MCP của Capitol, có cải tiến:
  web_search   ← find_urls (SerpAPI, chỉ trả URL)  ⇒ Serper, trả TIÊU ĐỀ + ĐOẠN TRÍCH + URL — agent thường trả lời được ngay, đỡ một lần đọc trang
  fetch_url    ← get_url_content                    ⇒ CHẶN địa chỉ nội bộ (chống SSRF: agent bị lừa đọc capitol-postgres, 169.254…), trần dung lượng + thời gian
  current_time ← get_current_time                   ⇒ giờ Việt Nam, kèm thứ trong tuần
  order_lookup — mới: tra đơn hàng trong bảng giả (chay_thu/du_lieu/don_hang.json), chỉ đọc
Mọi công cụ: trả dict có `ok`, lỗi trả chữ lời thường (không ném lỗi làm hỏng lượt), có trần kết quả để không đẩy quá nhiều token vào AI.
Khoá Serper đọc từ biến môi trường SERPER_API_KEY (file env/*.khoa.env, Git bỏ qua) — không bao giờ in ra.
"""

import ipaddress
import json
import logging
import os
import re
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from pydantic_ai import Tool

logger = logging.getLogger(__name__)

SERPER_URL = "https://google.serper.dev/search"
ORDERS_FILE = Path(os.environ.get("NATIVE_TOOLS_ORDERS_FILE", "/srv/du_lieu/don_hang.json"))
MAX_PAGE_BYTES = 1_000_000
MAX_PAGE_CHARS = 6_000
VN_TZ = timezone(timedelta(hours=7))
WEEKDAYS_VI = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


async def web_search(query: str, max_results: int = 5) -> dict:
    """Tìm trên web (Google qua Serper, đặt sẵn quốc gia Việt Nam gl=vn, ngôn ngữ tiếng Việt hl=vi). Trả danh sách {title, snippet, url}.
    Dùng khi cần thông tin công khai, mới, có nguồn. Mỗi lần gọi tốn một lượt Serper — đừng tìm lặp cùng một ý.

    Args:
        query: câu tìm kiếm VIẾT BẰNG TIẾNG VIỆT CÓ DẤU, ngắn và cụ thể, vd "giá áo thun cotton cổ tròn".
        max_results: số kết quả, 1 đến 10.
    """
    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key:
        return {"ok": False, "error": "Máy chưa có khoá Serper (SERPER_API_KEY) — không tìm web được."}
    n = max(1, min(int(max_results or 5), 10))
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(SERPER_URL, headers={"X-API-KEY": key, "Content-Type": "application/json"},
                             json={"q": query, "num": n, "gl": "vn", "hl": "vi"})
        if r.status_code != 200:
            return {"ok": False, "error": f"Serper trả HTTP {r.status_code}."}
        data = r.json()
    except Exception as e:  # noqa: BLE001 — công cụ không được làm hỏng lượt chạy
        return {"ok": False, "error": f"Không gọi được Serper: {type(e).__name__}."}
    results = [{"title": x.get("title"), "snippet": x.get("snippet"), "url": x.get("link")} for x in (data.get("organic") or [])[:n]]
    answer = (data.get("answerBox") or {}).get("answer") or (data.get("answerBox") or {}).get("snippet")
    logger.info("[NATIVE_TOOL] web_search query_chars=%d results=%d", len(query), len(results))
    return {"ok": True, "query": query, "answer_box": answer, "results": results}


def _public_ips(host: str) -> list[str]:
    """Mọi IP của host phải là địa chỉ CÔNG KHAI (ipaddress.is_global — chặn cả 100.64/10, IPv4-mapped, 6to4…). Không đạt ⇒ []."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return []
        ips.append(str(ip))
    return ips


def _peer_ip_ok(resp: httpx.Response) -> bool:
    """D kiểm 04/11 (MAJOR, DNS rebinding): kiểm lúc trước có thể khác IP httpx tự phân giải lúc kết nối ⇒ kiểm lại IP THẬT đã kết nối."""
    stream = resp.extensions.get("network_stream")
    addr = stream.get_extra_info("server_addr") if stream is not None else None
    if not addr:
        return False                                   # không biết đã nối vào đâu ⇒ coi như không an toàn
    ip = ipaddress.ip_address(addr[0])
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def fetch_url(url: str) -> dict:
    """Đọc nội dung chữ của MỘT trang web công khai (bỏ thẻ HTML, cắt còn khoảng 6.000 ký tự). Dùng sau web_search khi đoạn trích chưa đủ.

    Args:
        url: địa chỉ http(s) đầy đủ của trang công khai.
    """
    cur = httpx.URL(url) if isinstance(url, str) else None
    if cur is None or cur.scheme not in ("http", "https") or not cur.host:
        return {"ok": False, "error": "Chỉ đọc được địa chỉ http(s) đầy đủ."}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (chay_thu)"}) as c:
            for _hop in range(4):                      # tự theo tối đa 3 lần chuyển hướng, kiểm lại MỖI đích
                if not _public_ips(cur.host):
                    return {"ok": False, "error": "Địa chỉ trỏ vào mạng nội bộ — bị chặn (chống đọc trộm hệ thống nội bộ)."}
                async with c.stream("GET", cur) as r:
                    if not _peer_ip_ok(r):
                        return {"ok": False, "error": "Kết nối thật trỏ vào mạng nội bộ — bị chặn."}
                    if r.is_redirect:
                        cur = cur.join(r.headers.get("location", ""))   # chuyển hướng tương đối nối với URL HIỆN TẠI (D kiểm)
                        continue
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():            # đọc dần, dừng ở trần — không tải cả trang lớn vào bộ nhớ
                        buf.extend(chunk)
                        if len(buf) >= MAX_PAGE_BYTES:
                            break
                    status, enc = r.status_code, r.encoding
                    break
            else:
                return {"ok": False, "error": "Chuyển hướng quá nhiều lần."}
        raw = bytes(buf[:MAX_PAGE_BYTES]).decode(enc or "utf-8", errors="ignore")
        # Bước 23 (15:48 04/11, D tìm gốc): trang có ký tự NUL / điều khiển ⇒ PostgreSQL jsonb từ chối ("unsupported Unicode escape sequence")
        # ⇒ Capitol nuốt lỗi lưu (persist_results.py:687-694) ⇒ MẤT kết quả khối mà lượt vẫn SUCCESS ⇒ bỏ mọi ký tự điều khiển trừ \t \n \r.
        raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", raw)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Không đọc được trang: {type(e).__name__}."}
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    title = (re.search(r"(?is)<title[^>]*>(.*?)</title>", text) or [None, ""])[1].strip()
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    logger.info("[NATIVE_TOOL] fetch_url status=%s chars=%d", status, len(text))
    return {"ok": status < 400, "status": status, "url": str(cur), "title": title[:200],
            "text": text[:MAX_PAGE_CHARS], "truncated": len(text) > MAX_PAGE_CHARS}


async def current_time() -> dict:
    """Ngày giờ hiện tại theo giờ Việt Nam (UTC+7), kèm thứ trong tuần. Dùng khi câu hỏi phụ thuộc hôm nay / bây giờ."""
    now = datetime.now(VN_TZ)
    return {"ok": True, "iso": now.isoformat(timespec="seconds"), "date": now.strftime("%d/%m/%Y"), "time": now.strftime("%H:%M"),
            "weekday": WEEKDAYS_VI[now.weekday()]}


async def order_lookup(order_id: str) -> dict:
    """Tra MỘT đơn hàng của shop theo mã đơn (vd DH-1042): sản phẩm, size, trạng thái giao, có được đổi không, hạn đổi. Chỉ đọc; KHÔNG trả tên khách.

    Args:
        order_id: mã đơn đúng như khách ghi (không phân biệt hoa thường, bỏ khoảng trắng).
    """
    # Bộ ca thử 04/11 (P04): AI đưa nguyên "dh 1042" ⇒ chỉ bỏ khoảng trắng ra "DH1042" ≠ "DH-1042" ⇒ báo sai "không tìm thấy".
    # So mã sau khi bỏ MỌI ký tự không phải chữ / số, không phân biệt hoa thường.
    key = re.sub(r"[^0-9A-Za-z]", "", order_id or "").upper()
    try:
        orders = json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Không đọc được bảng đơn hàng: {type(e).__name__}."}
    found = next((o for o in orders if re.sub(r"[^0-9A-Za-z]", "", o.get("order_id", "")).upper() == key), None)
    if not found:
        return {"ok": False, "error": f"Không có đơn {key} trong bảng — đừng đoán thông tin đơn."}
    # Bộ ca thử 04/11 (P12): AI đem trường ghi chú nội bộ ("dữ liệu GIẢ để thử…") kể cho khách ⇒ chỉ trả các trường dành cho khách.
    # Bản 2 (chiều 04/11): danh sách CẤM 3 tên lọt trường tên khác + items[].note (D kiểm, mục 17 C3); bước 19 C2: khách hỏi đơn của
    # NGƯỜI KHÁC vẫn được trả tên chủ đơn ("Anh Minh") ⇒ đổi sang danh sách CHO PHÉP, bỏ tên khách. Công cụ chưa biết ai đang nhắn
    # (chưa có mã khách do hệ thống đưa) ⇒ chỉ trả điều không lộ danh tính: trạng thái, ngày, sản phẩm, chính sách đổi.
    public = {k: found[k] for k in _ORDER_PUBLIC if k in found}
    if isinstance(public.get("items"), list):
        public["items"] = [{k: it[k] for k in _ITEM_PUBLIC if k in it} for it in public["items"] if isinstance(it, dict)]
    return {"ok": True, "order": public}


_ORDER_PUBLIC = ("order_id", "items", "ordered_at", "delivery_status", "exchange_policy", "exchange_deadline")
_ITEM_PUBLIC = ("product", "size", "qty", "unit_price_vnd")


# ------------------------------------------------------------------ hướng C (chiều 04/11): xuất Excel / Word BẰNG CODE
# Capitol: khối "Tạo bảng tính Excel / tài liệu Word" cần CẢ máy ảo E2B lẫn Claude — AI viết code trong máy ảo rồi chạy (e2b_chat_service.py:53, D5).
# Ở đây làm ngược lại: AI chỉ đưa DỮ LIỆU (cột, dòng, đoạn văn) qua tham số công cụ; CODE ghi file bằng openpyxl / python-docx (có sẵn trong image).
# Lợi: không cần máy ảo, không cần Claude; số tổng do CODE cộng (không để AI tự nhẩm — "không để mô hình tự nhẩm số"); file chỉ ghi vào một thư mục.
EXPORT_DIR = Path(os.environ.get("NATIVE_TOOLS_EXPORT_DIR", "/srv/xuat_file"))
MAX_COLS, MAX_ROWS, MAX_CELL = 30, 1000, 2000


def _safe_name(name: str, ext: str) -> Path:
    """Tên file do AI đưa ⇒ chỉ giữ chữ không dấu / số / gạch, thêm giờ cho khỏi đè; luôn nằm trong EXPORT_DIR (chặn ../)."""
    base = re.sub(r"[^0-9A-Za-z_-]+", "_", (name or "").strip())[:60].strip("_") or "file"
    p = (EXPORT_DIR / f"{datetime.now(VN_TZ).strftime('%Y%m%d_%H%M%S')}_{base}{ext}").resolve()
    if p.parent != EXPORT_DIR.resolve():
        raise ValueError("tên file không hợp lệ")
    return p


def _cell(v):
    """Chống chèn công thức (CSV / formula injection): chữ bắt đầu bằng = + - @ bị Excel hiểu là công thức ⇒ thêm dấu ' ở đầu."""
    if isinstance(v, str):
        v = v[:MAX_CELL]
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
    return v


def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


# [ghi chú] make_xlsx thô (chỉ tiêu đề đậm) đã THAY bằng bản 2 ở xuat_file.py (định dạng tiền / %, cố định tiêu đề, công thức =SUM
# tự kiểm, nhiều sheet, biểu đồ). Bản cũ còn ở native_tools.py.goc của chay_thu/file_dep_c/.


# [Gắn file đẹp, nhóm kỹ thuật 05/11/2026] make_docx / make_pptx bản 2 + make_pdf (mới) lấy từ xuat_file.py (gắn cạnh file này, chỉ đọc):
# bộ màu theo chủ đề, 7 kiểu trang, code tự canh cỡ / tách trang, PDF nhúng phông DejaVu. Giữ đúng hình tham số + kiểu trả về bản cũ
# (tự kiểm 79/79 + D kiểm độc lập — chay_thu/xuat_file_dep/BAO_CAO.md, BAO_CAO_KET_QUA.md mục 23). make_xlsx, _safe_name, EXPORT_DIR, _mot_lan ở file này giữ nguyên.
from nodes.agent.xuat_file import make_csv, make_docx, make_md, make_pdf, make_pptx, make_txt, make_xlsx  # noqa: E402  (X1, X2 — C 09/11)
# [Gắn sql_query, nhóm kỹ thuật 05/11/2026 — người vận hành thiết kế 21:11] hỏi SQLite CHỈ ĐỌC (chay_thu/sql_chi_doc/sql_query.py, gắn cạnh file này):
# mã khách do HỆ THỐNG truyền qua bao đóng sql_query_for(cid) — AI chỉ thấy tham số cau_sql. Tự kiểm 50/50 trong hộp (sql_chi_doc/KET_QUA.md).
from nodes.agent.sql_query import sql_query_for  # noqa: E402


# [VÁ 10 — 06/11/2026, A; sandbox S4, chay_thu/THIET_KE_SANDBOX.md bản 5] AI chạy code Python phân tích MỘT file Excel trong hộp cách ly.
# Chiều dữ liệu: công cụ ghi PHIẾU {code, ma_file} vào thư mục hàng đợi (gắn rw) ⇒ người chạy hộp trên WSL (sandbox/nguoi_chay_hop.py, KHÔNG có AI)
# chạy một hộp `--network none` với cờ viết cứng ⇒ ghi phiếu kết quả vào thư mục ra (gắn ro) ⇒ công cụ đọc, trả chữ cho AI.
# AI chỉ điền `code`. Mã file do HỆ THỐNG điền (ô tool_ma_file, như mã khách) ⇒ AI không chọn được file khác (T33).
SANDBOX_VAO = Path(os.environ.get("NATIVE_TOOLS_SANDBOX_VAO", "/srv/sandbox/vao"))
SANDBOX_RA = Path(os.environ.get("NATIVE_TOOLS_SANDBOX_RA", "/srv/sandbox/ra"))
TRAN_RUN_PYTHON = 5          # R12 (e): tối đa 5 lần gọi mỗi lần khối chạy — trần bằng code, không bằng lời dặn
CHO_KET_QUA_GIAY = 100       # hộp có trần 60 s + hàng đợi tối đa 3 phiếu ⇒ chờ dư

# [VÁ B2b chay_thu 09/11 — nhóm kỹ thuật, người vận hành] file đầu vào của khối (S3 / MinIO) ⇒ thư mục file của người chạy hộp.
# Chiều dữ liệu: khối Nhận file (phong bì s3_key) ⇒ Agent: hàm dưới TẢI file (boto3 ⇒ MinIO) ⇒ ghi FILE_TAI/tai_<32 hex>.<đuôi> ⇒ run_python
# gửi mã đó ⇒ người chạy hộp (sandbox/nguoi_chay_hop.py) chép bản sao vào /dau_vao/ của hộp không mạng.
# Tên do hàm này đặt (không dùng tên người tải — chữ người dùng không vào đường dẫn); người chạy hộp kiểm lại khuôn tên, cỡ, không symlink.
FILE_TAI = Path(os.environ.get("NATIVE_TOOLS_SANDBOX_FILE_TAI", "/srv/sandbox/file_tai"))
TRAN_FILE_TAI = 3_000_000           # byte — trùng trần người chạy hộp
DUOI_FILE_TAI = {".xlsx", ".csv"}   # việc KPI cần; đuôi khác ⇒ từ chối rõ


_MA_TO_CHUC = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def kiem_khoa_cua_to_chuc(s3_key: str, orgid: str) -> None:
    """[D kiểm B2b, BLOCKER S5] phong bì do người soạn workflow viết được ⇒ s3_key tuỳ ý ⇒ phải nằm trong vùng file TẢI LÊN của chính tổ
    chức chạy lượt: khuôn khoá của route tải lên là files/<orgid>/… (AB services/aws/s3.py:313-323; B1 thấy files/<org>/<uuid>/<tên>).
    Thêm: không đoạn rỗng / "." / "..", không ký tự điều khiển, ≤ 1024 ký tự. Lệch ⇒ ValueError, KHÔNG tải."""
    if not _MA_TO_CHUC.match(orgid or ""):
        raise ValueError("Không xác định được tổ chức của lượt chạy ⇒ không tải file đầu vào.")
    k = str(s3_key or "")
    doan = k.split("/")
    if (len(k) > 1024 or not k.startswith(f"files/{orgid}/") or any(d in ("", ".", "..") for d in doan)
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in k)):
        raise ValueError("File đầu vào không thuộc vùng file tải lên của tổ chức này ⇒ không tải.")


def _hoi_co_s3(nf) -> int:
    """[D kiểm B2b, minor a] hỏi cỡ (head_object) TRƯỚC khi tải — file 5 GB không vào bộ nhớ worker rồi mới bị từ chối."""
    from services.aws.s3 import S3Service
    from workflow.node.base.file import _resolve_read_bucket

    bucket = _resolve_read_bucket(nf.s3_bucket, bool(getattr(nf, "generated", False)))
    return int(S3Service().client.head_object(Bucket=bucket, Key=nf.s3_key)["ContentLength"])


async def nhan_file_vao_hop(input_files: list, orgid: str, hoi_co=None) -> str:
    """Tải ĐÚNG MỘT file đầu vào (file TẢI LÊN của chính tổ chức), ghi vào FILE_TAI, trả mã file. Lỗi ⇒ ValueError lời thường.
    hoi_co: hàm hỏi cỡ (mặc định head_object S3) — tham số để phép thử thay được."""
    import asyncio
    import hashlib
    import uuid

    if len(input_files) != 1:
        raise ValueError(f"Hộp chạy code nhận đúng 1 file đầu vào; khối đang nhận {len(input_files)} file.")
    nf = input_files[0]
    duoi = os.path.splitext(str(getattr(nf, "name", "") or ""))[1].lower()
    if duoi not in DUOI_FILE_TAI:
        raise ValueError(f"Hộp chạy code chỉ nhận file {sorted(DUOI_FILE_TAI)}; file đầu vào có đuôi {duoi or '(không có)'}.")
    if getattr(nf, "generated", False):
        raise ValueError("Hộp chạy code chỉ nhận file NGƯỜI DÙNG TẢI LÊN (khối Nhận file), không nhận file do khối khác sinh ra.")
    kiem_khoa_cua_to_chuc(getattr(nf, "s3_key", ""), orgid)
    co = await asyncio.to_thread(hoi_co or _hoi_co_s3, nf)
    if co > TRAN_FILE_TAI:
        raise ValueError(f"File đầu vào {co} byte, quá trần {TRAN_FILE_TAI} byte của hộp chạy code.")
    du_lieu = await asyncio.to_thread(nf.download_file_content)
    if len(du_lieu) > TRAN_FILE_TAI:
        raise ValueError(f"File đầu vào {len(du_lieu)} byte, quá trần {TRAN_FILE_TAI} byte của hộp chạy code.")
    if duoi == ".xlsx" and not du_lieu.startswith(b"PK\x03\x04"):
        raise ValueError("File đuôi .xlsx nhưng nội dung không phải file Excel (không có chữ ký zip).")
    ten = f"tai_{uuid.uuid4().hex}{duoi}"
    fd = os.open(FILE_TAI / ten, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(du_lieu)
    logger.info("[NATIVE_TOOL] nhan_file_vao_hop ma=%s co=%s sha16=%s", ten, len(du_lieu), hashlib.sha256(du_lieu).hexdigest()[:16])
    return ten
TRAN_DOC_KET_QUA = 24 * 1024 * 1024
# R12 (a): khối có run_python thì KHÔNG có công cụ gửi ra ngoài. Danh sách CHO PHÉP (không phải danh sách cấm): công cụ mới mặc định bị cấm đi kèm.
CONG_CU_DI_KEM_RUN_PYTHON = {"tool_current_time", "tool_order_lookup", "tool_sql_query",
                             "tool_make_xlsx", "tool_make_docx", "tool_make_pptx", "tool_make_pdf",
                             "tool_make_md", "tool_make_txt", "tool_make_csv"}   # [ghi chú] chỉ ghi file vào thư mục xuất — không ra ngoài


CHOT_CHU_RUN_PYTHON = 200_000   # ký tự — bằng trần của lớp bọc trong hộp chạy code (sandbox/boc.py TRAN_CHU) và của người chạy hộp (CHOT_CHU)
CHOT_CHU_TINH_TRAN = (8_000, 2_000)   # (chữ in ra: ký tự ĐẦU, chữ lỗi: ký tự CUỐI) khi khối chạy mô hình TÍNH TRẦN tiền — đúng hai số của bản trước gói này


def _chot_chu_theo_mo_hinh() -> tuple[int, int]:
    """[ghi chú] (chốt chữ in ra, chốt chữ lỗi) theo mô hình khối Agent ĐANG chạy, hỏi CÙNG file mô hình khối như các gói khác.
    Vì sao phân nhánh: mức tiền trang giữ chỗ cho khối Agent chạy mô hình tính trần (giao_dien/muc_toi_da.py) được tính với kết quả công cụ ~10.000 ký tự;
    đưa 200.000 ký tự cho mô hình đó ⇒ tiền thật có thể vượt mức đã giữ tới ~1,4 lần (E tính 19/11). Claude 5.5 qua Bedrock không tính trần ⇒ 200.000.
    KHÔNG hỏi được (thiếu mô-đun, file hỏng, tên lạ) hoặc file không bảo đổi ⇒ nhánh nhỏ (hướng an toàn về tiền) — nhánh nhỏ nay CŨNG có dấu cắt.
    run_python chỉ gắn ở khối Agent (nodes/agent/node.py, node_id "agent_node"; không có lớp con) ⇒ hỏi đúng khoá đó."""
    try:
        from workflow.node.base.agent.model_resolution import CHAY_THU_MO_HINH_BO_TRAN_TOKEN, _chay_thu_mo_hinh_ep
        ep = _chay_thu_mo_hinh_ep("agent_node", "")
        if ep and ep[0] in CHAY_THU_MO_HINH_BO_TRAN_TOKEN:
            return CHOT_CHU_RUN_PYTHON, CHOT_CHU_RUN_PYTHON
    except Exception as e:  # noqa: BLE001
        logger.warning("[NATIVE_TOOL] run_python không hỏi được file mô hình khối (%s) — dùng chốt chữ nhỏ", type(e).__name__)
    return CHOT_CHU_TINH_TRAN


def _chu_ra_khong_cat_lang(kq: dict, chot_ra: int = CHOT_CHU_RUN_PYTHON, chot_loi: int = CHOT_CHU_RUN_PYTHON) -> dict:
    """[ghi chú] Phần chữ in ra / chữ lỗi của kết quả run_python đưa cho AI.
    Trước: chu_ra[:8000] + chu_loi[-2000:] mà KHÔNG có dấu nào ⇒ AI tưởng đã thấy hết (in một bảng 300 dòng ⇒ AI chỉ thấy ~80 dòng đầu rồi trả lời như đủ).
    Nay: giữ tới chot_ra ký tự ĐẦU của chữ in ra, chot_loi ký tự CUỐI của chữ lỗi; có phần bị bỏ (ở đây, ở người chạy hộp, hay ở lớp bọc trong hộp — các ô *_bo)
    ⇒ thêm ô `chu_ra_bi_cat` / `chu_loi_bi_cat` nói rõ và dặn in gọn hơn. Ô dấu đứng TRƯỚC ô chữ (E góp ý: AI đọc cảnh báo trước khối chữ dài). Thuần — thử được không cần hộp."""
    def so(x):
        return x if isinstance(x, int) and not isinstance(x, bool) and x >= 0 else 0
    ra, loi = str(kq.get("chu_ra", "")), str(kq.get("chu_loi", ""))
    giu_ra, giu_loi = ra[:chot_ra], (loi[-chot_loi:] if chot_loi > 0 else "")
    bo_ra, bo_loi = so(kq.get("chu_ra_bo")) + len(ra) - len(giu_ra), so(kq.get("chu_loi_bo")) + len(loi) - len(giu_loi)
    r = {}
    if bo_ra:
        r["chu_ra_bi_cat"] = (f"CHỮ IN RA ĐÃ BỊ CẮT: chỉ giữ {len(giu_ra)} ký tự ĐẦU, đã bỏ khoảng {bo_ra} ký tự / byte phía sau. Đừng coi phần thấy được là đủ — "
                              "chạy lại và in gọn hơn (tổng hợp, lọc, hoặc ghi ra file trong /out/), hoặc nói rõ với người dùng là kết quả bị cắt.")
    if bo_loi:
        r["chu_loi_bi_cat"] = f"CHỮ LỖI ĐÃ BỊ CẮT: chỉ giữ {len(giu_loi)} ký tự CUỐI, đã bỏ khoảng {bo_loi} ký tự / byte phía trước."
    r["chu_ra"], r["chu_loi"] = giu_ra, giu_loi
    return r


def _csv_an_toan(b: bytes) -> bytes:
    """R10: CSV do code AI viết ra có thể chứa ô công thức (=HYPERLINK(...), +cmd, @SUM...) — mở bằng Excel là chạy. Ghi lại (cách OWASP):
    - ô có công thức ở ĐẦU hoặc sau dấu ; , tab, xuống dòng, khoảng trắng (Excel máy đặt dấu ; làm dấu tách sẽ tách "a;=1+1" thành ô mới
      — D kiểm S4, M1) mà ô KHÔNG phải số ⇒ thêm dấu ' đứng trước; số âm (-5, -1.2) giữ nguyên;
    - mọi ô đặt trong ngoặc kép (QUOTE_ALL) ⇒ dấu ; , trong ô không bị coi là dấu tách."""
    import csv
    import io
    chu = b.decode("utf-8-sig", "replace")
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL)
    for dong in csv.reader(io.StringIO(chu)):
        moi = []
        for o in dong:
            if _CONG_THUC.search(o):
                try:
                    float(o)
                except ValueError:
                    o = "'" + o
            moi.append(o)
        w.writerow(moi)
    return out.getvalue().encode("utf-8")


_CONG_THUC = re.compile(r"(^|[;,\t\r\n])\s*[=+\-@]")


def _run_python_for(ma_file: str):
    """Dựng công cụ cho MỘT lần khối chạy (build_native_tools gọi mỗi lần khối chạy ⇒ bộ đếm tính lại từ 0)."""
    import asyncio
    import base64
    import uuid
    dem = {"n": 0}

    async def run_python(code: str) -> dict:
        """Chạy một đoạn code Python để phân tích file Excel / CSV của lượt này, trong hộp cách ly KHÔNG có mạng.

        Trong hộp: file nằm ở /dau_vao/ (chỉ đọc); có pandas, openpyxl, numpy, matplotlib. Muốn trả kết quả thì print() ra màn hình,
        hoặc ghi file .csv / .png / .json vào /out/ (tối đa 10 file, mỗi file ≤ 3 MB). Mỗi lần chạy tối đa 60 giây, 512 MB RAM.
        Chữ in ra chỉ được giữ tới một giới hạn; vượt thì phần sau bị bỏ và kết quả có ô chu_ra_bi_cat nói rõ đã giữ / đã bỏ bao nhiêu — thấy ô đó thì in gọn hơn rồi chạy lại.
        Không cài được gói, không gọi được mạng. Mỗi lượt khối chỉ gọi được 5 lần; gộp việc vào ít lần.
        Dùng công cụ này để TÍNH (tổng, đếm, lọc, nhóm) thay vì tự nhẩm số.

        Args:
            code: toàn bộ code Python sẽ chạy (≤ 20.000 ký tự).
        """
        dem["n"] += 1
        if dem["n"] > TRAN_RUN_PYTHON:
            logger.info("[NATIVE_TOOL] run_python chặn lần gọi thứ %s (trần %s)", dem["n"], TRAN_RUN_PYTHON)
            return {"ok": False, "ly_do": "qua_tran_so_lan",
                    "error": f"Đã gọi {TRAN_RUN_PYTHON} lần trong lượt này — hết lượt. Trả lời bằng kết quả đã có."}
        if not ma_file:
            return {"ok": False, "ly_do": "chua_co_file", "error": "Khối chưa được gắn file Excel (ô do hệ thống điền đang trống)."}
        ten = f"ab-{uuid.uuid4().hex[:16]}"
        phieu = json.dumps({"code": code, "ma_file": ma_file}, ensure_ascii=False)
        tam = SANDBOX_VAO / f".{ten}.tam"            # người chạy hộp bỏ qua tên bắt đầu bằng "." ⇒ không đọc phiếu đang ghi dở
        try:
            fd = os.open(tam, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(phieu)
            os.rename(tam, SANDBOX_VAO / f"{ten}.json")   # cùng thư mục ⇒ đổi tên nguyên tử
        except OSError as e:
            return {"ok": False, "ly_do": "khong_gui_duoc", "error": f"Không gửi được việc cho hộp chạy code: {type(e).__name__}."}
        logger.info("[NATIVE_TOOL] run_python gui phieu=%s ma_file=%s co_code=%s lan=%s", ten, ma_file, len(code), dem["n"])
        het = asyncio.get_running_loop().time() + CHO_KET_QUA_GIAY
        kq_file = None
        while asyncio.get_running_loop().time() < het:
            kq_file = next((p for p in SANDBOX_RA.glob(f"{ten}.*.ket_qua.json")), None)
            if kq_file:
                break
            await asyncio.sleep(0.5)
        if not kq_file:
            return {"ok": False, "ly_do": "khong_co_ket_qua", "error": "Hộp chạy code không trả kết quả kịp (người chạy hộp có thể đang tắt)."}
        try:
            if kq_file.stat().st_size > TRAN_DOC_KET_QUA:
                return {"ok": False, "ly_do": "ket_qua_qua_lon", "error": "Kết quả quá lớn."}
            kq = json.loads(kq_file.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "ly_do": "ket_qua_hong", "error": f"Không đọc được kết quả: {type(e).__name__}."}
        files = []
        for f in (kq.get("files") or [])[:10]:
            ten_f = str(f.get("ten", ""))
            duoi = os.path.splitext(ten_f)[1].lower()
            try:
                b = base64.b64decode(f.get("b64", ""), validate=True)
                if duoi == ".csv":
                    b = _csv_an_toan(b)
                dich = _safe_name(f"chay_code_{ten[3:11]}_{os.path.splitext(ten_f)[0]}", duoi)
                fd = os.open(dich, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
                with os.fdopen(fd, "wb") as out:
                    out.write(b)
                files.append({"file": dich.name, "co": len(b)})
            except Exception as e:  # noqa: BLE001
                files.append({"file": ten_f[:80], "loi": type(e).__name__})
        r = {"ok": bool(kq.get("ok")), "ly_do": kq.get("ly_do"), **_chu_ra_khong_cat_lang(kq, *_chot_chu_theo_mo_hinh()), "files": files, "bo_qua": kq.get("bo_qua") or []}
        logger.info("[NATIVE_TOOL] run_python ket_qua phieu=%s ok=%s ly_do=%s files=%s", ten, r["ok"], r["ly_do"], len(files))
        return r
    return run_python


TOOLS = {"tool_web_search": web_search, "tool_fetch_url": fetch_url, "tool_current_time": current_time, "tool_order_lookup": order_lookup,
         "tool_make_xlsx": make_xlsx, "tool_make_docx": make_docx, "tool_make_pptx": make_pptx, "tool_make_pdf": make_pdf,
         "tool_make_md": make_md, "tool_make_txt": make_txt, "tool_make_csv": make_csv}   # [ghi chú]


def _on(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "on"))


_KHONG_THUOC = ("Không tra được đơn này cho tài khoản đang nhắn — xin khách kiểm tra lại mã đơn, hoặc nhắn từ tài khoản đã đặt hàng. "
                "Đừng đoán, đừng nói đơn có tồn tại hay không.")


def _order_lookup_for(customer_id: str):
    """Hướng D (chiều 04/11): MÃ KHÁCH DO HỆ THỐNG ĐƯA, AI không điền được — cách (2) "bao đóng" của Capitol (report_tool.py:44-55, D10):
    mã khách được chụp vào hàm TRƯỚC khi đưa cho AI; chữ ký công cụ AI thấy vẫn chỉ có order_id. Đơn không thuộc khách đang nhắn ⇒ cùng một câu
    với "không có đơn" (không để lộ đơn có tồn tại — chống dò mã). Bước 19 C2: khách hỏi DH-1043 của người khác vẫn được báo trạng thái."""
    async def order_lookup(order_id: str) -> dict:
        """Tra MỘT đơn hàng CỦA KHÁCH ĐANG NHẮN theo mã đơn (vd DH-1042): sản phẩm, size, trạng thái giao, hạn đổi. Chỉ đọc.

        Args:
            order_id: mã đơn đúng như khách ghi (không phân biệt hoa thường, bỏ khoảng trắng).
        """
        key = re.sub(r"[^0-9A-Za-z]", "", order_id or "").upper()
        try:
            orders = json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Không đọc được bảng đơn hàng: {type(e).__name__}."}
        found = next((o for o in orders if re.sub(r"[^0-9A-Za-z]", "", o.get("order_id", "")).upper() == key), None)
        if not found or found.get("customer_id") != customer_id:
            logger.info("[NATIVE_TOOL] order_lookup tu_choi key=%s (không có hoặc không thuộc khách đang nhắn)", key)
            return {"ok": False, "error": _KHONG_THUOC}
        public = {k: found[k] for k in _ORDER_PUBLIC if k in found}
        if isinstance(public.get("items"), list):
            public["items"] = [{k: it[k] for k in _ITEM_PUBLIC if k in it} for it in public["items"] if isinstance(it, dict)]
        return {"ok": True, "order": public}
    return order_lookup


# [ghi chú] 10 CÔNG CỤ KPI = gọi HTTP backend KPI mekongai (không MCP — A hai bên chốt).
# Hình (a5 10/11): GET /api/tools ⇒ {"period", "tools": [{"name", "description", "parameters": JSON Schema — CHỈ ô AI điền}]}; POST /api/tool/{name}
# thân {"args": {...}}, header X-Kpi-Run-Id (= WorkflowRun.id — Rào chắn gửi cùng giá trị) + X-Kpi-Org-Id; 200 = JSON đã che mã NV (kể cả
# {"refused": …} — đưa NGUYÊN cho AI); 415 / 422 = lỗi hệ thống. Không khoá (chỉ đọc) ⇒ hộp backend KHÔNG có ports, chỉ mạng compose.
# HỆ THỐNG điền: địa chỉ (env, không payload), mã lượt + tổ chức (thuộc tính khối, chụp vào closure — AI không thấy / không sửa), kỳ (backend tự giữ).
KPI_URL_ENV = "CHAY_THU_KPI_URL"     # vd http://kpi-backend:8110 (ghim ở compose)
# [ghi chú] + get_extra_bonus (công cụ 11 — Thưởng thêm 6 khoản ngoài 8 KPI; mekongai a1329678, schema chỉ {employee_id}).
# Thiếu tên ở đây ⇒ build_kpi_tools bỏ LẶNG (chỉ log "bỏ mục lạ") ⇒ AI không có nguồn số thưởng thêm.
KPI_TEN = frozenset({"get_team_bonus_table", "get_employee_result", "get_mid_month_gap", "explain_kpi", "get_opportunities",
                     "get_data_provenance", "simulate_kpi_change", "get_team_kpi_summary", "compare_employees", "get_data_issues",
                     "get_extra_bonus", "get_spice_loyalty",   # [ghi chú] mốc 5b 4eabeba7: công cụ 12 (tiền 1 % gia vị trả KHÁCH) — thiếu ⇒ bị bỏ LẶNG
                     # [ghi chú] 5 công cụ TÍNH (chỉ nhận tham chiếu tới số của công cụ đọc,
                     # không nhận số gõ tay — backend/calc_tool.py); thiếu ⇒ build_kpi_tools bỏ LẶNG như 11/11, 16/11
                     "calc_sum", "calc_difference", "calc_percent_of", "calc_group_total", "calc_period_change"})
_KPI_O_HE_THONG = frozenset({"period", "ky", "run_id", "org_id", "orgid", "organization_id", "data_version"})   # [va_data_version] + data_version   # có trong schema ⇒ từ chối dựng (AI không được điền)
_KPI_MA = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
KPI_GIAY, KPI_MAX_RA = 10.0, 200_000
_KPI_DV = re.compile(r"^[0-9a-f]{1,64}$")


def kpi_data_version(params: dict) -> str:
    """[ghi chú] Ô HỆ THỐNG `kpi_data_version` (trang điền sau khi tải file lên): trống ⇒ "" (bản lúc bật backend);
    sai dạng ⇒ ValueError (khối dừng — KHÔNG lặng lẽ lùi về bản lúc bật). Dùng chung cho Agent / Rào chắn (một chỗ kiểm)."""
    v = (params or {}).get("kpi_data_version")
    v = v.strip() if isinstance(v, str) else ("" if v is None else str(v))
    if v and not _KPI_DV.match(v):
        raise ValueError("Bản dữ liệu KPI (kpi_data_version) sai dạng — chỉ nhận hex thường 1–64 ký tự.")
    return v


def _kpi_url() -> str:
    u = os.environ.get(KPI_URL_ENV, "").strip().rstrip("/")
    if not u.startswith(("http://", "https://")):
        raise ValueError(f"Công cụ KPI bật nhưng {KPI_URL_ENV} chưa cấu hình (http://…) — khối không chạy thiếu công cụ.")
    return u


def _kpi_goi_for(name: str, url: str, run_id: str, orgid: str, dv: str = ""):
    async def kpi_tool(**args) -> dict:
        if not (_KPI_MA.match(run_id or "") and _KPI_MA.match(orgid or "")):   # rỗng / sai dạng ⇒ KHÔNG gọi (backend sẽ không ghi được lần gọi)
            logger.warning("[NATIVE_TOOL] kpi %s không gọi: thiếu / sai mã lượt hoặc tổ chức", name)
            return {"ok": False, "error": "Lỗi hệ thống: thiếu mã lượt / tổ chức — không tra được số KPI. Báo người dùng thử lại, KHÔNG tự nêu số."}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(KPI_GIAY), follow_redirects=False, trust_env=False) as c:
                r = await c.post(f"{url}/api/tool/{name}", json={"args": args}, params={"data_version": dv} if dv else None,
                                 headers={"X-Kpi-Run-Id": run_id, "X-Kpi-Org-Id": orgid})   # [va_data_version] dv do hệ thống chụp vào closure
        except Exception as e:  # noqa: BLE001
            logger.warning("[NATIVE_TOOL] kpi %s lỗi gọi %s", name, type(e).__name__)
            return {"ok": False, "error": f"Lỗi hệ thống KPI ({type(e).__name__}) — không tra được số. KHÔNG tự nêu số."}
        if r.status_code != 200 or len(r.content) > KPI_MAX_RA:
            logger.warning("[NATIVE_TOOL] kpi %s HTTP %s, %d byte", name, r.status_code, len(r.content))
            return {"ok": False, "error": f"Lỗi hệ thống KPI (HTTP {r.status_code}) — không tra được số. KHÔNG tự nêu số."}
        try:
            kq = r.json()
        except ValueError:
            return {"ok": False, "error": "Lỗi hệ thống KPI (kết quả không phải JSON). KHÔNG tự nêu số."}
        logger.info("[NATIVE_TOOL] kpi %s ok %d byte", name, len(r.content))   # không ghi nội dung (có mã NV đã che, số tiền)
        return kq
    kpi_tool.__name__ = name
    return kpi_tool


def kiem_kpi_khong_ra_ngoai(params: dict, co_khoi_cong_cu: bool) -> None:
    """[ghi chú] Khối có công cụ KPI (số tiền thật của NV) KHÔNG được cùng lúc có đường ra ngoài — tìm web / đọc trang (gọi thẳng),
    MCP gắn thêm (ô mcp_servers), hay BẤT KỲ khối công cụ nào cắm vào (Exa / SerpAPI / Composio … đi _assemble_handle_toolsets riêng). Chặn bằng code."""
    if not _on(params.get("tool_kpi")):
        return
    ngoai = [f for f in ("tool_web_search", "tool_fetch_url", "tool_rag_search") if _on(params.get(f))]   # [ghi chú] câu tìm ⇒ app RAG ⇒ OpenRouter (ra ngoài)
    if params.get("mcp_servers"):
        ngoai.append("mcp_servers")
    if co_khoi_cong_cu:
        ngoai.append("khối công cụ cắm vào")
    if ngoai:
        raise ValueError(f"Khối có công cụ KPI không được dùng cùng công cụ ra ngoài: {', '.join(ngoai)}. Tra web ở khối KHÁC, không cùng khối có số KPI.")


def build_kpi_tools(run_id: str, orgid: str, dv: str = "") -> list:
    """Dựng công cụ KPI từ GET /api/tools (một nguồn mô tả — a5). Lỗi / hình lạ ⇒ ValueError (khối Agent không chạy thiếu công cụ)."""
    url = _kpi_url()
    try:
        with httpx.Client(timeout=httpx.Timeout(KPI_GIAY), follow_redirects=False, trust_env=False) as c:
            r = c.get(f"{url}/api/tools", params={"data_version": dv} if dv else None)
        r.raise_for_status()
        ds = r.json().get("tools")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Không lấy được danh sách công cụ KPI ({type(e).__name__}) — khối không chạy thiếu công cụ.") from e
    if not isinstance(ds, list) or not ds:
        raise ValueError("Danh sách công cụ KPI rỗng / sai hình.")
    out, bo = [], []
    for t in ds:
        name, mo_ta, sc = (t or {}).get("name"), (t or {}).get("description"), (t or {}).get("parameters")
        if name not in KPI_TEN or not isinstance(mo_ta, str) or not isinstance(sc, dict) or sc.get("type") != "object":
            bo.append(str(name)[:40])
            continue
        if set(sc.get("properties") or {}) & _KPI_O_HE_THONG:
            raise ValueError(f"Công cụ KPI {name}: schema có ô HỆ THỐNG ({sorted(set(sc['properties']) & _KPI_O_HE_THONG)}) — AI không được điền.")
        out.append(Tool.from_schema(function=_kpi_goi_for(name, url, run_id, orgid, dv), name=name, description=mo_ta[:2000], json_schema=sc,
                                    takes_ctx=False))
    if bo:
        logger.warning("[NATIVE_TOOL] kpi bỏ %d mục lạ trong /api/tools: %s", len(bo), bo)
    if not out:
        raise ValueError("Không có công cụ KPI hợp lệ nào trong /api/tools.")
    logger.info("[NATIVE_TOOL] kpi dựng %d công cụ", len(out))
    return out


def _sql_co_so(fn, run_id: str, orgid: str):
    """[va_kiem_so_sql C 12/11] Bọc công cụ sql_query: giữ NGUYÊN tên, chữ ký (cau_sql), mô tả AI thấy; thêm việc ghi sổ sau khi chạy.
    Ghi sổ hỏng / không có mã lượt ⇒ vẫn trả kết quả cho AI, sổ thiếu ⇒ Rào chắn chế độ "sql" chặn câu có số (đóng khi lỗi ở phía Rào)."""
    import functools

    @functools.wraps(fn)
    async def sql_query(cau_sql: str) -> dict:
        r = await fn(cau_sql)
        from nodes.agent.nho_sql_luot import ghi

        if not await ghi(orgid, run_id, cau_sql, r):
            logger.warning("[NATIVE_TOOL] sql_query KHÔNG ghi được sổ kết quả của lượt (thiếu mã lượt / Redis lỗi / quá %s lần)", "40")
        return r
    return sql_query


def build_native_tools(params: dict, run_id: str = "", orgid: str = "") -> list:
    """Công cụ bật theo các ô bật / tắt của khối (params tool_*). Có mã khách (ô tool_customer_id — hệ thống điền) ⇒ tra đơn chỉ trong đơn của
    khách đó; ô trống ⇒ giữ cách cũ (bộ ca thử chưa có mã khách) — ghi rõ là CHƯA kiểm chủ đơn."""
    out = []
    if _on(params.get("tool_run_python")):
        # R12 (a) — vá 10: chặn ngay lúc dựng công cụ (mỗi lần khối chạy), báo rõ lý do; lớp mức workflow ở prepare (vá 10 phần b, c)
        ngoai = sorted(f for f in params if f.startswith("tool_") and f not in CONG_CU_DI_KEM_RUN_PYTHON
                       and f not in ("tool_run_python", "tool_ma_file", "tool_customer_id") and _on(params.get(f)))
        if ngoai:
            raise ValueError(f"Khối bật 'chạy code' không được bật cùng công cụ gửi ra ngoài: {', '.join(ngoai)}. "
                             "Cần cả hai thì tra web ở khối TRƯỚC, chạy code ở khối sau (sandbox R12).")
        out.append(Tool(_run_python_for(str(params.get("tool_ma_file") or "").strip()), takes_ctx=False))
    for field, fn in TOOLS.items():
        if not _on(params.get(field)):
            continue
        cid = str(params.get("tool_customer_id") or "").strip()
        if field == "tool_order_lookup" and cid:
            fn = _order_lookup_for(cid)
        if field in _GHI_FILE:
            fn = _mot_lan(fn)
        out.append(Tool(fn, takes_ctx=False))
    if _on(params.get("tool_sql_query")):
        cid = str(params.get("tool_customer_id") or "").strip()
        if not cid:   # bài học D-1: ô mã khách trống thì KHÔNG chạy thiếu kiểm — công cụ vẫn bật nhưng mọi câu trả lỗi bi_chan (sql_query._mo)
            logger.warning("[NATIVE_TOOL] tool_sql_query bật nhưng ô tool_customer_id trống ⇒ mọi câu sẽ bị từ chối")
        # [va_kiem_so_sql C 12/11] mỗi lần công cụ trả về, CODE ghi {câu SQL, kết quả} vào sổ của lượt (nho_sql_luot) để khối Rào chắn chế độ "sql"
        # so số trong câu trả lời với ĐÚNG kết quả công cụ. sql_query.py (bản đóng băng của B) KHÔNG đổi — chỉ bọc ở đây.
        out.append(Tool(_sql_co_so(sql_query_for(cid), run_id, orgid), takes_ctx=False))
    if _on(params.get("tool_rag_search")):   # [ghi chú] tìm trong tài liệu đã tải lên app RAG mekongai qua hộp cổng capitol-rag-cong
        from nodes.agent.rag_tool import rag_search_for
        cols = [x.strip() for x in str(params.get("rag_collections") or "").splitlines() if x.strip()]
        out.append(Tool(rag_search_for(cols, orgid=orgid), takes_ctx=False))   # bộ đếm ≤ 5 lần dựng lại mỗi lần khối chạy · [ghi chú] orgid của LƯỢT (hệ thống)
    if _on(params.get("tool_kpi")):   # [ghi chú] mã lượt + tổ chức do khối Agent truyền (thuộc tính hệ thống), không từ params
        out.extend(build_kpi_tools(run_id, orgid, kpi_data_version(params)))   # [va_data_version] sai dạng ⇒ ValueError, khối dừng
    return out


_GHI_FILE = {"tool_make_xlsx", "tool_make_docx", "tool_make_pptx", "tool_make_pdf",
             "tool_make_md", "tool_make_txt", "tool_make_csv"}   # make_pdf cũng ghi MỘT lần / lượt; [ghi chú] md / txt / csv cũng vậy


def _mot_lan(fn):
    """Bước 23 (15:48 04/11): khối gộp được DẶN "gọi công cụ xuất Excel MỘT lần" nhưng gọi 3 lần ⇒ 3 file. Lời dặn không giữ được số lần ⇒ TRẦN BẰNG CODE:
    mỗi công cụ ghi file chỉ ghi thành công MỘT lần cho mỗi lần khối chạy (hàm này dựng lại mỗi lần khối chạy — workflow/node/base/agent/node.py:736).
    Lần sau trả lỗi kèm tên file đã ghi ⇒ AI dùng lại tên đó thay vì ghi file mới."""
    import functools
    da = {}

    @functools.wraps(fn)
    async def boc(*a, **k):
        if "file" in da:
            logger.info("[NATIVE_TOOL] %s chặn lần ghi thứ %s (đã ghi %s)", fn.__name__, da.get("lan", 1) + 1, da["file"])
            da["lan"] = da.get("lan", 1) + 1
            return {"ok": False, "error": f"Đã ghi file {da['file']} trong lượt này — mỗi lượt chỉ ghi một lần. Dùng lại tên file đó, không gọi lại."}
        r = await fn(*a, **k)
        if isinstance(r, dict) and r.get("ok"):
            da["file"] = r.get("file")
        return r
    return boc
