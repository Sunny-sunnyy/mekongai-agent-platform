"""Hộp cổng RAG — B 16/11/2026 (đã giao, người vận hành "cách 1": khối Agent của Capitol gọi app RAG mức 1 của mekongai). Kế hoạch + soát:
tài liệu kỹ thuật (tài liệu nội bộ). Cùng khuôn hộp chuyển tiếp 18402 (sua_doi/kpi_tai_len/chuyen_tiep.py).

Vì sao có hộp: app mekongai chỉ nghe 127.0.0.1:1945 (compose của họ) ⇒ worker ab-* không gọi thẳng được. Hộp chạy network_mode host, nghe
NGHE_TREN (mặc định 172.19.0.1:19450 — cổng mạng capitol-chay-thu_default), chuyển tới 127.0.0.1:1945.
Chỉ nhận ĐÚNG 1 đường: POST /api/v1/search, thân JSON {"collections": [tên…], "query": chữ, "k": số}. Hộp:
  * mã bí mật X-Rag-Ma BẮT BUỘC (env RAG_MA, nạp từ file 600 ngoài repo; thiếu / < 24 ký tự ⇒ KHÔNG bật), so hmac.compare_digest TRƯỚC khi đọc thân
    (E major 2: IP máy chủ ⇒ mọi hộp docker — kể cả capitol-eval có Internet, hộp dự án khác — gọi tới được);
  * GHIM user_id = capitol_thu (bỏ mọi user_id gửi lên); collection phải bắt đầu "CHATBOT_capitol_thu_" (app tự kiểm thuộc user — không đọc file sổ
    đăng ký của mekongai, theo nhóm kỹ thuật); 1–5 collection; query 1–500 ký tự; k kẹp 1–8; thân ≤ 4 KB; trả lời app ≤ 400 KB;
  * ≤ 20 lần / 60 s (quá ⇒ 429); hết giờ 15 s; nhật ký MỖI lần: mã HTTP, số collection, độ dài câu hỏi, số trang, cỡ — KHÔNG chữ câu hỏi / trang.
Không mở /upload-files, /chatbot-reference (gọi luna bên họ ⇒ ngoài sổ), hay đường nào khác.
Chạy (trong hộp): RAG_MA=… python -I rag_cong.py   (compose.rag_cong.yaml)

[ghi chú] TỔ CHỨC gắn Ở CỔNG:
  * header X-Rag-Org BẮT BUỘC (mã tổ chức của LƯỢT, dạng UUID — rag_tool gửi từ thuộc tính hệ thống, không phải ô người dựng gõ);
    user_id = "capitol_" + sha256(org)[:32] (128 bit; org[:8] không đủ ⇒ trùng thì hai tổ chức thấy kho của nhau vì app KHÔNG xác thực);
    cổng TỰ suy user_id, bỏ mọi user_id gửi lên; chỉ nhận collection có tiền tố CHATBOT_<user_id>_ của chính tổ chức đó.
  * Danh sách CHO PHÉP: POST /api/v1/search · GET /api/v1/kho (⇒ app GET /api/v1/qdrant/collections/user/<user_id> suy từ org).
    Mọi đường khác 404 — gồm /api/v1/qdrant/collections/all (kho của MỌI user), /upload-files (chờ nhóm kỹ thuật: nạp có gọi AI trả phí không).
  * Nhật ký chỉ ghi 8 ký tự đầu của user_id (không ghi mã tổ chức).
  * Bỏ user cố định capitol_thu: kho cũ của capitol_thu không còn dùng được qua cổng (đã dọn 16/11).
[ghi chú] ĐƯỜNG NẠP POST /api/v1/nap:
  * mã RIÊNG X-Rag-Nap-Ma (env RAG_NAP_MA, file $CAPITOL_STATE_DIR/rag_nap.env 600 — KHÔNG nạp vào ab-*) ⇒ chỉ máy chủ trang (may_chu, có sổ tiền +
    giữ chỗ trước — va_rag_to_chuc/nap_rag.py) nạp được; khối Agent / hộp khác có RAG_MA vẫn chỉ tìm. Thiếu RAG_NAP_MA ⇒ đường nạp ĐÓNG (404).
  * X-Rag-Org (như trên) · X-Ten-File: tên mã hoá % (UTF-8), CÙNG luật lam_sach_ten của trang (kho_tai_lieu.py), đuôi trong DUOI_NAP ·
    X-Nap-Lai: true / false · thân = byte file, 1 B – 20 MB.
  * Cổng TỰ dựng multipart gửi app /api/v1/upload-files với ĐÚNG 4 trường: user_id (suy từ org), file, language="vie+eng", force_reindex (X-Nap-Lai) —
    KHÔNG bao giờ gửi trường url (app tự tải URL từ Internet, document.py:93-99).
  * 1 việc nạp một lúc (đang có ⇒ 409), hạn 900 s; ≤ 6 lần nạp / giờ. Từ chối TRƯỚC app ⇒ header X-Rag-Cong: tu_choi (nap_rag trả chỗ, không ghi tiền).
  * Nhật ký: đuôi + cỡ + 8 ký tự user_id — KHÔNG tên file.
[ghi chú]
  * Đường nạp nhận THÊM 10 đuôi (DUOI_NAC_2): ảnh rời .png .jpg .jpeg · trang web .html .htm · .json · bảng tính .xlsx .xlsm .xls .csv — đều là loại app RAG nhận.
    KHÔNG nhận .doc .ppt .pptx .xlsb .ods (app trả 400; trang đã từ chối kèm cách lưu lại).
  * 10 đuôi mới phải qua LỚP KIỂM ĐẦU FILE trước khi tới app (từ chối = tu_choi, chưa tốn tiền): cùng MỘT hàm với trang — can_nap_kho() của
    va_tai_lieu_chat/doc_tai_lieu.py (bản ĐANG GẮN của trang), cổng nạp theo đường dẫn lúc khởi động (nap_luat_dau_file). Lý do có lớp thứ hai: app chạy chung máy,
    ảnh nén nhỏ bung ra hàng GB RAM từng làm treo máy ⇒ không để cỡ ảnh chỉ được kiểm ở phía gọi. Không nạp được hàm / hàm không qua 5 ca tự thử lúc khởi động
    ⇒ cổng VẪN chạy cho 5 đuôi cũ nhưng TỪ CHỐI cả 10 đuôi mới (503, lời rõ) — không cho qua khi chưa kiểm được.
"""
import hmac
import http.client
import json
import logging
import os
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rag_cong")

import hashlib
DUONG = "/api/v1/search"
DUONG_KHO = "/api/v1/kho"
DUONG_NAP = "/api/v1/nap"
DUOI_NAC_2 = (".png", ".jpg", ".jpeg", ".html", ".htm", ".json", ".xlsx", ".xlsm", ".xls", ".csv")   # [va_rag_cong_nac_2] = DUOI_CHAT − DUOI của trang; phải qua kiem_dau_file
DUOI_NAP = (".pdf", ".docx", ".txt", ".md", ".markdown") + DUOI_NAC_2   # = DUOI_CHAT của trang (kho_tai_lieu.py) ⊂ SUPPORTED_SUFFIXES của app (document.py:236)
NAP_TOI_DA, NAP_HET_GIO, NAP_LAN_GIO = 20 * 1024 * 1024, 900, 30   # [ghi chú] 6 ⇒ 30 lần nạp / giờ: người dùng kéo một xấp file bị chặn ở file thứ 7; các phanh thật (một việc một lúc, trang / file, trang / ngày, RAM) giữ
_khoa_nap = threading.Lock()
_nap_lan = deque()
_ORG = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def user_cua_org(org: str) -> str | None:
    """Mã tổ chức (UUID chữ thường) ⇒ user_id của app RAG; sai dạng ⇒ None. Thuần."""
    org = (org or "").strip().lower()
    if not _ORG.fullmatch(org):
        return None
    return "capitol_" + hashlib.sha256(org.encode()).hexdigest()[:32]


def tien_to(user_id: str) -> str:
    return f"CHATBOT_{user_id}_"
APP = (os.environ.get("RAG_APP_HOST", "127.0.0.1"), int(os.environ.get("RAG_APP_PORT", "1945")))
# [ghi chú] HET_GIO 15 ⇒ 35 s: lần tìm ĐẦU sau khi tải file, app tạo vector ≈ 20 s (log app mekongai, lượt fc900f09 08:24:45 UTC ⇒ cổng cắt, 503)
# [ghi chú] TRA_TOI_DA 400 KB ⇒ 4 MB. Vì sao: công cụ tìm phía khối (rag_tool.py CHOT_KY_TU) tự
# dừng + báo rõ khi kết quả > 400.000 KÝ TỰ; trần cũ của cổng tính theo BYTE (tiếng Việt có dấu ~1,3–1,5 byte / ký tự; JSON thoát \uXXXX tới 6 byte / ký tự)
# ⇒ kết quả 300.000–400.000 ký tự hợp lệ bị cổng chặn TRƯỚC với lời không có số. 4 MB > 400.000 × 6 byte + phần khung ⇒ cổng không bao giờ chặn trước công cụ;
# trần của cổng chỉ còn là chốt chống app trả về bất thường, và lời báo nay nêu cỡ thật.
THAN_TOI_DA, TRA_TOI_DA, HET_GIO = 4096, 4 * 1024 * 1024, 35


def _loi_qua_lon(co_da_doc: int) -> dict:
    return {"loi": f"trả lời của app RAG quá lớn: hơn {TRA_TOI_DA:,} byte (đã đọc {co_da_doc:,} byte rồi dừng) — cổng không chuyển tiếp một phần".replace(",", ".")}
K_TOI_DA, COL_TOI_DA, CAU_TOI_DA = 8, 5, 500
LAN_TOI_DA, CUA_SO = 20, 60.0
_TEN = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
_lan = deque()
_khoa_lan = threading.Lock()


def ma_bi_mat() -> str:
    ma = os.environ.get("RAG_MA", "")
    if len(ma) < 24:
        raise SystemExit("rag_cong: thiếu RAG_MA (≥ 24 ký tự) — KHÔNG bật hộp")
    return ma


def kiem_than(b: bytes, user_id: str) -> tuple[dict | None, str]:
    """(thân gửi app, '') hoặc (None, lý do). user_id = user_cua_org(X-Rag-Org) do cổng suy. Thuần — thử được không cần mạng."""
    try:
        d = json.loads(b.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "thân không phải JSON UTF-8"
    if not isinstance(d, dict) or set(d) - {"collections", "query", "k", "user_id", "include_images", "whole_file_for_lists"}:
        return None, "chỉ nhận collections, query, k, include_images, whole_file_for_lists"
    cols, q, k = d.get("collections"), d.get("query"), d.get("k", 5)
    if not isinstance(cols, list) or not 1 <= len(cols) <= COL_TOI_DA or not all(isinstance(c, str) and _TEN.fullmatch(c) for c in cols):
        return None, f"collections: 1–{COL_TOI_DA} tên hợp lệ"
    if not all(c.startswith(tien_to(user_id)) for c in cols):
        return None, "collection không thuộc tổ chức của lượt"
    if not isinstance(q, str) or not 1 <= len(q.strip()) <= CAU_TOI_DA:
        return None, f"query: 1–{CAU_TOI_DA} ký tự"
    if not isinstance(k, int) or isinstance(k, bool):
        return None, "k phải là số nguyên"
    inc_img = d.get("include_images", False)
    whole_list = d.get("whole_file_for_lists", False)
    if not isinstance(inc_img, bool):
        return None, "include_images phải là true/false"
    if not isinstance(whole_list, bool):
        return None, "whole_file_for_lists phải là true/false"
    out = {"user_id": user_id, "collections": cols, "query": q.strip(), "k": max(1, min(k, K_TOI_DA))}
    if inc_img:
        out["include_images"] = True
    if whole_list:
        out["whole_file_for_lists"] = True
    return out, ""


def con_luot(bay_gio: float | None = None) -> bool:
    t = time.monotonic() if bay_gio is None else bay_gio
    with _khoa_lan:
        while _lan and t - _lan[0] > CUA_SO:
            _lan.popleft()
        if len(_lan) >= LAN_TOI_DA:
            return False
        _lan.append(t)
        return True


_CAM_TEN = set('"|`<>/\\;')


def kiem_ten_file(ma_hoa: str) -> str | None:
    """X-Ten-File (mã hoá % UTF-8) ⇒ tên đã kiểm; sai ⇒ None. Luật = lam_sach_ten của trang (giao_dien/kho_tai_lieu.py) nhưng KHÔNG sửa giùm
    (khác ⇒ từ chối) + cấm thêm ";" và "..". Thuần."""
    import urllib.parse
    try:
        ten = urllib.parse.unquote(ma_hoa or "", encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if not ten or ten != ten.strip() or len(ten) > 120 or ten.startswith(".") or ".." in ten:
        return None
    if any(ord(c) < 32 or ord(c) == 127 or c in _CAM_TEN for c in ten):
        return None
    return ten if any(ten.lower().endswith(d) for d in DUOI_NAP) else None


def da_multipart(uid: str, ten: str, du_lieu: bytes, nap_lai: bool = False) -> tuple[bytes, str]:
    """Thân multipart ĐÚNG 4 trường (không url). ten đã qua kiem_ten_file (không có " \\r \\n). Thuần."""
    import secrets
    ranh = "capitolrag" + secrets.token_hex(16)
    phan = []
    for k, v in (("user_id", uid), ("language", "vie+eng"), ("force_reindex", "true" if nap_lai else "false")):
        phan.append(f'--{ranh}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    phan.append(f'--{ranh}\r\nContent-Disposition: form-data; name="file"; filename="{ten}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode("utf-8"))
    phan.append(du_lieu)
    phan.append(f"\r\n--{ranh}--\r\n".encode())
    return b"".join(phan), f"multipart/form-data; boundary={ranh}"


# [ghi chú] BA CHỐT NẠP,
# đều là TỪ CHỐI TRƯỚC khi gửi app (tu_choi — chưa tốn tiền). Tiền nạp nay ghi sổ BÊN MEKONGAI (trần 0,50 USD / ngày, ~0,0008 USD / trang chụp).
NAP_TRANG_FILE, NAP_TRANG_NGAY, NAP_RAM_MB = 100, 400, 4096   # [ghi chú] 600 ⇒ 400 trang / ngày: giữ tiền đọc ảnh bên app RAG trong trần 0,50 USD / ngày của họ
# [ghi chú] App nạp ĐỒNG BỘ, không có đường huỷ: lần nạp chạm hạn NAP_HET_GIO thì phía mình cắt nhưng app VẪN chạy tiếp ⇒ không nhận lần nạp kế trong NAP_NGHI_GIAY.
NAP_NGHI_GIAY = 15 * 60
_nap_nghi_toi = 0.0
NAP_DEM_THU_MUC = os.environ.get("RAG_NAP_DEM_THU_MUC") or os.environ.get("CAPITOL_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".local", "share", "capitol-platform")   # file đếm ngoài repo
_GIAI_NEN_MOT, _GIAI_NEN_TONG = 8 * 1024 * 1024, 64 * 1024 * 1024


def _dem_trang_pdf_tho(than: bytes) -> int | None:
    """Số trang PDF, không thư viện ngoài. Đếm trên phần chữ thô CỘNG phần giải nén của các "dòng đối tượng nén" (/ObjStm — PDF hiện nay hay giấu cây trang
    trong đó; giải bằng zlib chuẩn, có trần cỡ chống bom nén). CHỈ tin khi hai cách đếm KHỚP: số đối tượng /Type /Page = /Count lớn nhất của nút /Type /Pages.
    [E K1 19/11] MỌI chữ /ObjStm trong file phải ứng với MỘT dòng nén đã giải được — sót một dòng (từ điển có << >> lồng, bộ lọc lạ…) là có thể sót cây trang
    thật (ca nhóm kỹ thuật: 300 trang giấu trong dòng nén + cây mồi 1 trang ở chữ thô ⇒ bản trước trả 1). Lệch / thiếu / mã hoá / giải lỗi / vượt trần ⇒ None."""
    import zlib
    chu = [than]; tong = 0; giai = 0
    if b"/Encrypt" in than:
        return None
    for m in re.finditer(rb"/ObjStm(?![\w])", than):
        dau = re.compile(rb"stream\r?\n").search(than, m.end(), m.end() + 4096)   # chữ stream của CHÍNH đối tượng này (từ điển dài tối đa 4 KB)
        if not dau:
            return None
        cuoi = than.find(b"endstream", dau.end())
        if cuoi < 0:
            return None
        try:
            d = zlib.decompressobj(); x = d.decompress(than[dau.end():cuoi], _GIAI_NEN_MOT)
        except zlib.error:
            return None
        if d.unconsumed_tail:   # một dòng nén bung ra quá trần ⇒ không chắc
            return None
        tong += len(x); giai += 1
        if tong > _GIAI_NEN_TONG:
            return None
        chu.append(x)
    if giai != len(re.findall(rb"/ObjStm(?![\w])", than)):
        return None
    tat = b"\n".join(chu)
    n = len(re.findall(rb"/Type\s*/Page(?![s\w])", tat))
    dem = [int(x) for x in re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", tat)] + [int(x) for x in re.findall(rb"/Count\s+(\d+)[^>]*?/Type\s*/Pages\b", tat)]
    return n if n and dem and max(dem) == n else None


PDFINFO_GIAY, PDFINFO_CPU_GIAY, PDFINFO_RAM, PDFINFO_RA = 20, 20, 2 * 1024 ** 3, 1024 * 1024


def _dem_trang_pdfinfo(than: bytes) -> int | None:
    """[ghi chú] Phép đếm THỨ BA, độc lập: chương trình pdfinfo của máy (poppler) chạy như TIẾN TRÌNH CON trên file CHƯA TIN CẬY. [E K2] bốn hạn như phần đọc PDF
    của khung chat (va_tai_lieu_chat/doc_tai_lieu.py _chay_co_han): giờ đồng hồ PDFINFO_GIAY, giờ CPU, vùng nhớ 2 GB, cỡ đầu ra 1 MB (đầu ra ghi vào file tạm vô
    danh để hạn cỡ file của hệ điều hành áp được), không ghi lõi. Gọi bằng DANH SÁCH tham số (không shell), file vào nằm ở thư mục tạm riêng, môi trường tối
    giản. KHÔNG có cách ly mạng (pdfinfo không tự mở mạng, nhưng đây không phải hộp kín). Không có pdfinfo / lỗi / chạm hạn / không ra dòng Pages ⇒ None."""
    import resource, subprocess, tempfile

    def han():
        resource.setrlimit(resource.RLIMIT_AS, (PDFINFO_RAM, PDFINFO_RAM))
        resource.setrlimit(resource.RLIMIT_CPU, (PDFINFO_CPU_GIAY, PDFINFO_CPU_GIAY))
        resource.setrlimit(resource.RLIMIT_FSIZE, (PDFINFO_RA, PDFINFO_RA))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    try:
        with tempfile.TemporaryDirectory(prefix="rag_cong_pdf_") as tm, tempfile.TemporaryFile() as ra:
            duong = os.path.join(tm, "f.pdf")
            with open(duong, "wb") as f:
                f.write(than)
            r = subprocess.run(["pdfinfo", duong], stdin=subprocess.DEVNULL, stdout=ra, stderr=subprocess.DEVNULL, timeout=PDFINFO_GIAY, preexec_fn=han,
                               env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
            ra.seek(0); chu = ra.read(PDFINFO_RA + 1)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(rb"^Pages:\s+(\d+)\s*$", chu, re.M)
    return int(m.group(1)) if r.returncode == 0 and len(chu) <= PDFINFO_RA and m else None


def dem_trang_pdf(than: bytes) -> int | None:
    """Số trang PDF dùng cho chốt nạp: CHỈ nhận khi phép đếm thô (hai cách khớp nhau) VÀ pdfinfo cho CÙNG một số. Một bên không đếm được hoặc lệch ⇒ None
    ⇒ người gọi TỪ CHỐI (đóng khi không chắc — tiền là của sổ bên mekongai)."""
    a = _dem_trang_pdf_tho(than)
    if a is None:
        return None
    b = _dem_trang_pdfinfo(than)
    return a if b == a else None


# [ghi chú] LỚP KIỂM ĐẦU FILE cho 10 đuôi nấc 2 — dùng lại ĐÚNG hàm của trang (một nguồn luật, không chép): can_nap_kho(ten, du_lieu) của
# doc_tai_lieu.py: file đạt ⇒ TRẢ VỀ (cổng bỏ qua giá trị trả); không đạt ⇒ NÉM TuChoi mà chữ là câu nói với người dùng. Hàm chỉ đọc ĐẦU file (không giải nén ảnh,
# không bung cả gói Excel). Biến RAG_LUAT_DAU_FILE để bộ thử trỏ file khác. KHÔNG ghim sha file đó (file còn chứa lời dặn + phần đọc PDF, C đổi thường xuyên) —
# thay bằng 5 ca TỰ THỬ lúc nạp: ba ca biết chắc phải bị từ chối + hai ca phải qua; lệch một ca ⇒ coi như không nạp được.
LUAT_DAU_FILE = os.environ.get("RAG_LUAT_DAU_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "va_tai_lieu_chat", "doc_tai_lieu.py")
_luat = {"ham": None, "tu_choi": None, "sha": None, "loi": "chưa nạp luật kiểm đầu file"}
_PNG = lambda rong, cao: b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + rong.to_bytes(4, "big") + cao.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + b"\x00" * 4  # noqa: E731


def nap_luat_dau_file(duong: str | None = None) -> str | None:
    """Nạp can_nap_kho + TuChoi từ file luật của trang. Trả None nếu dùng được, ngược lại trả lý do (và mọi đuôi nấc 2 sẽ bị từ chối). Gọi MỘT lần lúc khởi động."""
    import importlib.util
    duong = duong or LUAT_DAU_FILE
    _luat.update(ham=None, tu_choi=None, sha=None, loi="chưa nạp luật kiểm đầu file")
    try:
        with open(duong, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()[:8]
        sp = importlib.util.spec_from_file_location("rag_cong_luat_dau_file", duong)
        m = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(m)
        ham, tu_choi, duoi_kho = m.can_nap_kho, m.TuChoi, m.DUOI_KHO
    except Exception as e:  # noqa: BLE001
        _luat["loi"] = f"không nạp được file luật kiểm đầu file ({type(e).__name__})"
        return _luat["loi"]
    if not (callable(ham) and isinstance(tu_choi, type) and issubclass(tu_choi, Exception) and all(d in duoi_kho for d in DUOI_NAC_2)):
        _luat["loi"] = "file luật kiểm đầu file không có đủ can_nap_kho / TuChoi / 10 đuôi nấc 2"
        return _luat["loi"]
    def bi_tu_choi(ten, than):
        try:
            ham(ten, than)
        except tu_choi:
            return True
        return False
    try:   # ca biết chắc HỎNG phải ra hỏng, ca biết chắc ĐẠT phải qua — rồi mới tin hàm
        dung = (bi_tu_choi("a.png", _PNG(50_000, 50_000)) and not bi_tu_choi("a.png", _PNG(10, 10))
                and bi_tu_choi("a.xls", b"khong phai excel doi cu") and not bi_tu_choi("a.json", b'{"a": 1}') and bi_tu_choi("a.json", b'{"a": '))
    except Exception as e:  # noqa: BLE001
        _luat["loi"] = f"hàm kiểm đầu file ném lỗi lạ ở ca tự thử ({type(e).__name__})"
        return _luat["loi"]
    if not dung:
        _luat["loi"] = "hàm kiểm đầu file không qua ca tự thử lúc khởi động (ảnh quá cỡ / .xls sai dấu đầu / JSON hỏng phải bị từ chối; ảnh nhỏ / JSON đúng phải qua)"
        return _luat["loi"]
    _luat.update(ham=ham, tu_choi=tu_choi, sha=sha, loi=None)
    return None


def kiem_dau_file(ten: str, du_lieu: bytes) -> tuple[int, str] | None:
    """Đuôi nấc 2 ⇒ None nếu file qua luật đầu file; ngược lại (mã HTTP, lời). Đuôi cũ ⇒ None (không thuộc lớp này). Mọi trường hợp không kiểm được ⇒ TỪ CHỐI."""
    if not any(ten.lower().endswith(d) for d in DUOI_NAC_2):
        return None
    if _luat["ham"] is None:
        return 503, f"Cổng chưa kiểm được loại file này ({_luat['loi']}) — báo người quản trị; chưa nạp gì."
    try:
        _luat["ham"](ten, du_lieu)
    except _luat["tu_choi"] as e:
        return 422, str(e)[:600]
    except Exception as e:  # noqa: BLE001
        return 422, f"Không kiểm được nội dung file ({type(e).__name__}) — file hỏng hoặc không đúng định dạng theo đuôi; kiểm lại file rồi nạp lại."
    return None


_THU_MUC_ANH = {"docx": "word/media/", "xlsx": "xl/media/", "xlsm": "xl/media/", "pptx": "ppt/media/"}


def dem_trang_khac_pdf(duoi: str, than: bytes) -> int | None:
    """[ghi chú] File KHÔNG phải PDF: Word / Excel / PowerPoint dạng gói zip ⇒ mỗi ẢNH NHÚNG tính như 1 trang (app đọc ảnh bằng mô hình trả tiền), tối thiểu 1;
    chỉ đọc DANH SÁCH mục của gói, không giải nén. Gói hỏng ⇒ None (từ chối). Đuôi khác (txt, md, ảnh rời…) ⇒ 1."""
    if duoi not in _THU_MUC_ANH:
        return 1
    import io, zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(than)) as z:
            ten = z.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    if len(ten) > 20_000:
        return None
    return max(1, sum(1 for t in ten if t.startswith(_THU_MUC_ANH[duoi]) and not t.endswith("/")))


def ram_kha_dung_mb() -> int | None:
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for d in f:
                if d.startswith("MemAvailable:"):
                    return int(d.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _file_dem_ngay() -> str:
    return os.path.join(NAP_DEM_THU_MUC, f"rag_nap_trang_{time.strftime('%Y-%m-%d')}.json")


def doc_dem_ngay() -> dict | None:
    """{"trang": số trang đã nhận nạp hôm nay, "file": số file}. Chưa có file ⇒ 0. File hỏng / không đọc được ⇒ None ⇒ người gọi TỪ CHỐI (không đoán)."""
    p = _file_dem_ngay()
    if not os.path.exists(p):
        return {"trang": 0, "file": 0}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and all(isinstance(d.get(k), int) and not isinstance(d.get(k), bool) and d[k] >= 0 for k in ("trang", "file")):
            return {"trang": d["trang"], "file": d["file"]}
    except (OSError, ValueError):
        pass
    return None


def ghi_dem_ngay(d: dict) -> bool:
    p = _file_dem_ngay(); tam = p + ".tam"
    try:
        os.makedirs(NAP_DEM_THU_MUC, mode=0o700, exist_ok=True)
        with open(tam, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tam, p)
        return True
    except OSError:
        return False


def con_luot_nap(bay_gio: float | None = None) -> bool:
    t = time.monotonic() if bay_gio is None else bay_gio
    with _khoa_lan:
        while _nap_lan and t - _nap_lan[0] > 3600:
            _nap_lan.popleft()
        if len(_nap_lan) >= NAP_LAN_GIO:
            return False
        _nap_lan.append(t)
        return True


class Cong(BaseHTTPRequestHandler):
    server_version = "rag-cong"
    MA = ""
    MA_NAP = ""

    def log_message(self, *a):  # tắt nhật ký mặc định (có thể chứa đường dẫn lạ); nhật ký riêng ở _tra
        pass

    def _tra(self, code: int, d: dict | bytes, ghi: str = "", tu_choi: bool = False):
        b = d if isinstance(d, bytes) else json.dumps(d, ensure_ascii=False).encode()
        self.send_response(code)
        if tu_choi:
            self.send_header("X-Rag-Cong", "tu_choi")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
        log.info("rag_cong %s %s %s", code, self.path[:40] if code != 404 else "?", ghi)

    def _ma_dung(self) -> bool:
        try:   # [E minor] header ngoài ASCII ⇒ compare_digest ném TypeError ⇒ trước đây 500; so trên byte, lỗi ⇒ 401
            return hmac.compare_digest(self.headers.get("X-Rag-Ma", "").encode("utf-8", "surrogateescape"), self.MA.encode())
        except Exception:  # noqa: BLE001
            return False

    def _user(self) -> str | None:
        return user_cua_org(self.headers.get("X-Rag-Org", ""))

    def do_GET(self):
        if not self._ma_dung():
            return self._tra(401, {"loi": "thiếu / sai mã"})
        if self.path != DUONG_KHO:   # /all và mọi đường khác KHÔNG mở
            return self._tra(404, {"loi": "chỉ mở " + DUONG_KHO})
        uid = self._user()
        if not uid:
            return self._tra(400, {"loi": "thiếu / sai X-Rag-Org (mã tổ chức của lượt)"})
        if not con_luot():
            return self._tra(429, {"loi": f"quá {LAN_TOI_DA} lần / {int(CUA_SO)} s"})
        try:
            c = http.client.HTTPConnection(*APP, timeout=HET_GIO)
            c.request("GET", f"/api/v1/qdrant/collections/user/{uid}")
            r = c.getresponse()
            body = r.read(TRA_TOI_DA + 1)
            c.close()
        except (OSError, http.client.HTTPException) as e:
            return self._tra(503, {"loi": f"app RAG không trả lời ({type(e).__name__})"}, f"u={uid[8:16]}")
        if len(body) > TRA_TOI_DA:
            return self._tra(502, _loi_qua_lon(len(body)))
        self._tra(r.status, body, f"kho u={uid[8:16]} co={len(body)}")

    def _nap(self):
        """POST /api/v1/nap — mọi từ chối trước app mang tu_choi=True."""
        tc = lambda code, loi: self._tra(code, {"loi": loi}, tu_choi=True)  # noqa: E731
        try:
            dung = bool(self.MA_NAP) and hmac.compare_digest(self.headers.get("X-Rag-Nap-Ma", "").encode("utf-8", "surrogateescape"), self.MA_NAP.encode())
        except Exception:  # noqa: BLE001
            dung = False
        if not dung:
            return tc(401, "thiếu / sai mã nạp")
        uid = self._user()
        if not uid:
            return tc(400, "thiếu / sai X-Rag-Org (mã tổ chức)")
        ten = kiem_ten_file(self.headers.get("X-Ten-File", ""))
        if not ten:
            return tc(400, f"X-Ten-File: tên mã hoá %, ≤ 120 ký tự, không ký tự điều khiển / ký tự cấm, đuôi {', '.join(DUOI_NAP)}")
        nl = self.headers.get("X-Nap-Lai", "false")
        if nl not in ("true", "false"):
            return tc(400, "X-Nap-Lai: true / false")
        try:
            n = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            n = -1
        if not 0 < n <= NAP_TOI_DA:
            return tc(413, f"file 1 byte – {NAP_TOI_DA // (1024 * 1024)} MB")
        if not _khoa_nap.acquire(blocking=False):
            return tc(409, "đang có một việc nạp — thử lại sau")
        try:
            global _nap_nghi_toi
            con = _nap_nghi_toi - time.monotonic()
            if con > 0:
                return tc(503, f"Lần nạp trước chạy quá {NAP_HET_GIO} giây nên dịch vụ đọc tài liệu có thể còn bận — thử nạp lại sau khoảng {int(con // 60) + 1} phút.")
            ram = ram_kha_dung_mb()   # [chốt (c)] máy sát RAM (hai dự án kia đang chạy nặng) ⇒ không đẩy việc nạp cho app; không đọc được ⇒ coi như bận
            if ram is None or ram < NAP_RAM_MB:
                return tc(503, f"máy đang bận (RAM khả dụng {ram if ram is not None else '?'} MB, cần ≥ {NAP_RAM_MB} MB) — thử nạp lại sau")
            if not con_luot_nap():
                return tc(429, f"quá {NAP_LAN_GIO} lần nạp / giờ")
            du_lieu = self.rfile.read(n)
            if len(du_lieu) != n:
                return tc(400, "thân thiếu byte")
            duoi = ten.rsplit(".", 1)[-1].lower()
            kd = kiem_dau_file(ten, du_lieu)   # [va_rag_cong_nac_2] 10 đuôi nấc 2: kiểm đầu file TRƯỚC khi đếm trang / cộng sổ đếm / gửi app
            if kd:
                return tc(*kd)
            # [chốt (a)] PDF: đếm trang Ở CỔNG (không tin số trang phía trang gửi). > 100 trang, hoặc không đếm chắc được ⇒ từ chối trước app.
            if duoi == "pdf":
                trang = dem_trang_pdf(du_lieu)
                if trang is None:
                    return tc(422, "Không đếm chắc được số trang của file PDF này (file mã hoá hoặc cấu trúc lạ) — cần hỏi người phụ trách dịch vụ đọc tài liệu trước khi nạp.")
                if trang > NAP_TRANG_FILE:
                    return tc(413, f"File hơn {NAP_TRANG_FILE} trang ({trang} trang) — cần hỏi người phụ trách dịch vụ đọc tài liệu trước khi nạp.")
            else:
                trang = dem_trang_khac_pdf(duoi, du_lieu)   # Word / Excel: mỗi ảnh nhúng = 1 trang; đuôi khác = 1
                if trang is None:
                    return tc(422, "Không đọc được cấu trúc file (file hỏng hoặc không đúng định dạng theo đuôi) — kiểm lại file rồi nạp lại.")
                if trang > NAP_TRANG_FILE:
                    return tc(413, f"File có {trang} ảnh bên trong (mỗi ảnh tính như một trang, chốt {NAP_TRANG_FILE}) — cần hỏi người phụ trách dịch vụ đọc tài liệu trước khi nạp.")
            # [chốt (b)] tổng trang đã nhận nạp TRONG NGÀY + file này ≤ 600. Cộng TRƯỚC khi gửi app (app lỗi giữa chừng vẫn tính — thà dư); một việc nạp một lúc
            # (_khoa_nap) nên đọc–cộng–ghi không tranh nhau. File đếm hỏng / không ghi được ⇒ từ chối, không đoán.
            dem = doc_dem_ngay()
            if dem is None:
                return tc(503, "không đọc được sổ đếm trang nạp trong ngày của cổng — báo người quản trị; chưa nạp gì")
            if dem["trang"] + trang > NAP_TRANG_NGAY:
                return tc(429, f"Hôm nay đã nhận nạp {dem['trang']} trang ({dem['file']} file); file này {trang} trang sẽ vượt chốt {NAP_TRANG_NGAY} trang / ngày — nạp tiếp vào ngày mai hoặc hỏi người phụ trách dịch vụ đọc tài liệu.")
            if not ghi_dem_ngay({"trang": dem["trang"] + trang, "file": dem["file"] + 1}):
                return tc(503, "không ghi được sổ đếm trang nạp trong ngày của cổng — báo người quản trị; chưa nạp gì")
            than, kieu = da_multipart(uid, ten, du_lieu, nl == "true")
            try:
                c = http.client.HTTPConnection(*APP, timeout=NAP_HET_GIO)
                c.request("POST", "/api/v1/upload-files", body=than, headers={"Content-Type": kieu, "Content-Length": str(len(than))})
                r = c.getresponse()
                body = r.read(TRA_TOI_DA + 1)
                c.close()
            except TimeoutError:   # chạm hạn NAP_HET_GIO (socket.timeout): app có thể VẪN đang đọc file ⇒ nghỉ nhận nạp + nhật ký rõ; người vận hành báo bên mekongai
                _nap_nghi_toi = time.monotonic() + NAP_NGHI_GIAY
                log.warning("rag_cong NAP QUA HAN %s giay u=%s .%s co=%s trang=%s — app co the con chay; nghi nhan nap %s giay", NAP_HET_GIO, uid[8:16], duoi, n, trang, NAP_NGHI_GIAY)
                return self._tra(504, {"loi": f"Việc nạp chạy quá {NAP_HET_GIO} giây nên đã dừng chờ; dịch vụ đọc tài liệu có thể vẫn đang xử lý file này. "
                                              f"Chưa nạp tiếp được trong {NAP_NGHI_GIAY // 60} phút — báo người phụ trách dịch vụ đọc tài liệu."}, f"nap QUA HAN u={uid[8:16]} .{duoi} co={n} trang={trang}")
            except ConnectionRefusedError:   # app chưa bật (tắt ~21:45 mỗi ngày): CHƯA gửi được gì ⇒ lời rõ; số trang đã cộng giữ nguyên (thà dư)
                return self._tra(503, {"loi": "dịch vụ đọc tài liệu chưa bật — chưa nạp được; thử lại trong giờ làm việc"}, f"nap u={uid[8:16]} .{duoi} co={n} trang={trang}")
            except (OSError, http.client.HTTPException) as e:   # ĐÃ gửi tới app (có thể đã tốn tiền) ⇒ KHÔNG tu_choi
                return self._tra(503, {"loi": f"app RAG không trả lời ({type(e).__name__})"}, f"nap u={uid[8:16]} .{duoi} co={n} trang={trang}")
            if len(body) > TRA_TOI_DA:
                return self._tra(502, _loi_qua_lon(len(body)), f"nap u={uid[8:16]} .{duoi} co={n} trang={trang}")
            self._tra(r.status, body, f"nap u={uid[8:16]} .{duoi} co={n} trang={trang}")
        finally:
            _khoa_nap.release()

    def do_POST(self):
        if self.path == DUONG_NAP and self.MA_NAP:   # mã nạp riêng — kiểm trong _nap, TRƯỚC khi đọc thân
            return self._nap()
        if not self._ma_dung():   # TRƯỚC mọi thứ, trước khi đọc thân
            return self._tra(401, {"loi": "thiếu / sai mã"})
        if self.path != DUONG:
            return self._tra(404, {"loi": "chỉ mở " + DUONG})
        uid = self._user()
        if not uid:
            return self._tra(400, {"loi": "thiếu / sai X-Rag-Org (mã tổ chức của lượt)"})
        if not con_luot():
            return self._tra(429, {"loi": f"quá {LAN_TOI_DA} lần / {int(CUA_SO)} s"})
        try:
            n = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            n = -1
        if not 0 < n <= THAN_TOI_DA:
            return self._tra(413, {"loi": f"thân 1–{THAN_TOI_DA} byte"})
        gui, ly_do = kiem_than(self.rfile.read(n), uid)
        if gui is None:
            return self._tra(400, {"loi": ly_do})
        try:
            c = http.client.HTTPConnection(*APP, timeout=HET_GIO)
            c.request("POST", DUONG, body=json.dumps(gui, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"})
            r = c.getresponse()
            body = r.read(TRA_TOI_DA + 1)
            c.close()
        except (OSError, http.client.HTTPException) as e:
            return self._tra(503, {"loi": f"app RAG không trả lời ({type(e).__name__})"}, f"cols={len(gui['collections'])} q={len(gui['query'])}")
        if len(body) > TRA_TOI_DA:
            return self._tra(502, _loi_qua_lon(len(body)))
        so_trang = "?"
        try:
            dd = json.loads(body)
            so_trang = len(((dd.get("data") or {}).get("pages") or []) if isinstance(dd, dict) else [])   # api/chatbot.py:303-304 bên app
        except ValueError:
            pass
        self._tra(r.status, body, f"u={uid[8:16]} cols={len(gui['collections'])} q={len(gui['query'])} k={gui['k']} trang={so_trang} co={len(body)}")


def main():
    Cong.MA = ma_bi_mat()
    mn = os.environ.get("RAG_NAP_MA", "")
    if mn and (len(mn) < 24 or mn == Cong.MA):
        raise SystemExit("rag_cong: RAG_NAP_MA phải ≥ 24 ký tự và KHÁC RAG_MA — KHÔNG bật hộp")
    Cong.MA_NAP = mn
    loi = nap_luat_dau_file()
    if loi:
        log.warning("rag_cong LUAT DAU FILE KHONG DUNG DUOC: %s — TU CHOI ca %s duoi nac 2 (5 duoi cu van nhan)", loi, len(DUOI_NAC_2))
    else:
        log.info("rag_cong luat dau file %s (%s duoi nac 2 qua can_nap_kho)", _luat["sha"], len(DUOI_NAC_2))
    host, port = os.environ.get("NGHE_TREN", "172.19.0.1:19450").rsplit(":", 1)
    log.info("rag_cong nghe %s:%s ⇒ %s:%s (chỉ %s + %s%s; user_id suy từ X-Rag-Org)", host, port, *APP, DUONG, DUONG_KHO, " + " + DUONG_NAP if Cong.MA_NAP else "")
    ThreadingHTTPServer((host, int(port)), Cong).serve_forever()


if __name__ == "__main__":
    main()
