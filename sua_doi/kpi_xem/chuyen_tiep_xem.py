"""Hộp chuyển tiếp CHỈ ĐỌC cho trang KPI :5183 — B 12/11/2026, theo HOP_DONG_N2_N3_A.md bản 2 mục 4 (NHÁP: chưa bật, chưa có đã soát).
Đường đi: máy chủ agent mekongai (đã kiểm mã phiên của trang) ⇒ 127.0.0.1:18403 kèm header X-Xem-Ma ⇒ hộp này ⇒ kpi-xem:8110 (mạng riêng).
Vì sao có hộp này: hộp xem KHÔNG bật cờ KPI_SYSTEM_ONLY (trang cần các đường của trang) nên không được mở cổng thẳng; mọi cổng 127.0.0.1 đều bị
hộp Docker khác gọi vòng tới được ⇒ phải đòi mã + chỉ cho GET theo danh sách TƯỜNG MINH.
Luật (sai ⇒ từ chối, không chuyển gì):
  - Host đúng địa chỉ máy (chống DNS rebinding) ⇒ sai 403.
  - Chỉ GET; POST / PUT / PATCH / DELETE / OPTIONS / HEAD ⇒ 405 (không CORS — trình duyệt không gọi thẳng hộp này).
  - Header X-Xem-Ma == XEM_MA (hmac.compare_digest) ⇒ thiếu / sai 401, TRƯỚC khi đọc đường / tham số. Thiếu XEM_MA ⇒ hộp không bật.
  - Đường + tham số: file JSON do mekongai xuất từ đúng sha (XEM_DUONG_FILE; sha256 của file phải bằng XEM_DUONG_SHA256, lệch ⇒ không bật).
    Hình file: {"duong": [{"path": "/api/team-board", "tham_so": {"data_version": "^[0-9a-f]{1,64}$"}},
                          {"path": "/api/employee/{employee_id}", "doan": {"employee_id": "^NV\\d{2}$"}, "tham_so": {}}]}
    Đoạn {tên} trong path kiểm bằng regex "doan"; tham số ngoài danh sách hoặc sai regex ⇒ 422; tham số lặp ⇒ 422.
    KHÔNG chuyển nguyên chuỗi truy vấn: dựng lại từ các cặp đã kiểm. KHÔNG nhận đường có "..", "//", "%", "\\".
  - Trả về: nguyên mã + thân của hộp xem, tối đa XEM_TRAN_BYTE (mặc định 8 MB; vượt ⇒ 502), Content-Type của hộp xem (mặc định chỉ json; html chỉ cho đường ghi "loai": "html").
  - GET /van-tay (cũng cần mã): {"tinh": {ds_sha256, du_lieu_sha16…} ghi lúc bật qua env XEM_VAN_TAY_TINH (JSON),
    "dong": các trường XEM_VAN_TAY_TRUONG của /api/meta hộp xem}. Trường thiếu / rỗng hoặc hộp xem không trả lời ⇒ {"loi": true} mã 502 ⇒ bên so coi là LỆCH.
Nhật ký: mã HTTP + tên đường mẫu (không tham số, không mã). Không khoá hệ thống nào trong hộp này.
"""
import hashlib
import hmac
import http.client
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlencode

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kpi_xem_cong")
MA = os.environ.get("XEM_MA", "")
BACKEND = (os.environ.get("XEM_BACKEND_HOST", "kpi-xem"), int(os.environ.get("XEM_BACKEND_PORT", "8110")))
HOST_CHO_PHEP = {h.strip().lower() for h in os.environ.get("XEM_HOST_CHO_PHEP", "127.0.0.1:18403,localhost:18403").split(",") if h.strip()}
TRAN_BYTE = int(os.environ.get("XEM_TRAN_BYTE", str(8 * 1024 * 1024)))
HET_GIO = int(os.environ.get("XEM_HET_GIO", "90"))   # lần gọi đầu của 1 % gia vị ≈ 15 s (đọc lại RAW)
VAN_TAY_TRUONG = [x for x in os.environ.get("XEM_VAN_TAY_TRUONG", "period,cutoff,files_sha256,rule_version,workflow_version").split(",") if x]
# [D S2] mặc định CHỈ json: html từ hộp xem sẽ được máy chủ :5183 đưa vào trình duyệt cùng origin trang KPI ⇒ chữ từ dữ liệu thành mã chạy.
# Đường nào cần html thì ghi "loai": "html" cho ĐÚNG đường đó trong file JSON (bên mekongai phát kèm CSP sandbox).
LOAI = {"json": "application/json", "html": "text/html"}
# [ghi chú] backend KHÔNG bật cờ KPI_SYSTEM_ONLY (hộp xem) ĐÒI mã phiên ở header X-Kpi-Session cho
# MỌI lời gọi (thiếu ⇒ 401; không có file mã ⇒ backend không bật). Mã phiên của hộp xem do script bật sinh riêng mỗi lần (file 600), gắn chỉ đọc vào
# hộp xem + hộp này; KHÔNG phải mã X-Xem-Ma của người gọi, không rời mạng kpi_xem. Không đặt XEM_PHIEN_FILE ⇒ không gửi (backend bản cũ / thử).
def _doc_ma_phien() -> str:
    f = os.environ.get("XEM_PHIEN_FILE")
    if not f:
        return ""
    try:                                     # [D 12:29] biến CÓ đặt mà file không đọc được (sai uid / chưa gắn) ⇒ DỪNG lúc bật — không chạy tiếp kiểu "không gửi mã"
        k = open(f, encoding="utf-8").read().rstrip("\n")
    except OSError as e:
        raise SystemExit(f"không đọc được file mã phiên của hộp xem ({type(e).__name__}) — hộp cổng phải chạy cùng uid với chủ file (600)") from None
    if len(k) < 32 or any(c.isspace() for c in k):
        raise SystemExit("file mã phiên của hộp xem không hợp lệ (cần 1 dòng ≥ 32 ký tự, không khoảng trắng)")
    return k


MA_PHIEN = _doc_ma_phien()
_cung_luc = threading.BoundedSemaphore(int(os.environ.get("XEM_CUNG_LUC", "4")))   # [D m2] mỗi yêu cầu giữ tới 8 MB + bắt hộp xem tính tới 90 s; đầy ⇒ 429


# [ghi chú] Đường có dữ liệu khách / dòng đơn: cấm CỨNG ở cả hai chỗ (lúc nạp file + lúc nhận yêu cầu)
# ⇒ rào không chỉ dựa vào file đường GET do mekongai sinh; file lỡ có các đường này thì hộp không bật.
CAM_CUNG = ("/api/membership", "/api/spice-customers", "/api/order-lines")


def nap_duong(duong_file: str, sha_mong: str) -> list[dict]:
    """Đọc danh sách đường; sha256 file ≠ sha_mong ⇒ dừng. Trả [{mau: regex cả đường, ten: path mẫu, tham_so: {tên: regex}}]."""
    b = open(duong_file, "rb").read()
    if not hmac.compare_digest(hashlib.sha256(b).hexdigest(), sha_mong):
        raise SystemExit("file danh sách đường khác sha256 đã ghim (XEM_DUONG_SHA256)")
    ra = []
    for d in json.loads(b)["duong"]:
        p = d["path"]
        if not re.fullmatch(r"/api/[A-Za-z0-9_\-/{}]+", p) or p.startswith(("/api/system", "/api/tool", "/api/export", "/api/workflow", "/api/blocks") + CAM_CUNG):
            raise SystemExit(f"đường không được phép trong danh sách: {p}")
        if p.split("/")[2].startswith("{"):   # [D m1] đoạn đầu sau /api/ phải là chữ cố định — không thì "/api/{a}/{b}" lách được danh sách cấm tiền tố
            raise SystemExit(f"đoạn đầu sau /api/ phải cố định: {p}")
        if d.get("loai", "json") not in LOAI:
            raise SystemExit(f"{p}: loai chỉ nhận json | html")
        doan = d.get("doan") or {}
        mau = ""
        for phan in re.split(r"(\{[a-z_]+\})", p):
            if phan.startswith("{"):
                ten = phan[1:-1]
                if ten not in doan:
                    raise SystemExit(f"{p}: đoạn {{{ten}}} thiếu regex")
                mau += f"(?P<{ten}>[^/]+)"
            else:
                mau += re.escape(phan)
        # [nhóm kỹ thuật + A, 12/11] regex của file JSON luôn so bằng fullmatch + cờ re.ASCII: `$` của re.match / re.search khớp TRƯỚC dấu xuống dòng cuối
        # ("NV04\n" lọt) và `\d` không có ASCII nhận cả chữ số Unicode ("NV０４"). fullmatch đòi khớp HẾT chuỗi; ASCII bó \d \w \s về ASCII.
        ra.append({"ten": p, "loai": LOAI[d.get("loai", "json")], "mau": re.compile(mau, re.ASCII), "doan": {k: re.compile(v, re.ASCII) for k, v in doan.items()},
                   "tham_so": {k: re.compile(v, re.ASCII) for k, v in (d.get("tham_so") or {}).items()}})
    if not ra:
        raise SystemExit("danh sách đường rỗng")
    return ra


def chon(duong: list[dict], path: str, query: str):
    """Trả (tên mẫu, đường + truy vấn DỰNG LẠI) hoặc (None, mã lỗi, lời)."""
    if any(x in path for x in ("..", "//", "%", "\\")) or len(path) > 200 or len(query) > 600 or path.lower().startswith(CAM_CUNG):
        return None, 404, "không có đường này"
    for d in duong:
        m = d["mau"].fullmatch(path)
        if not m:
            continue
        if any(not d["doan"][k].fullmatch(v) for k, v in m.groupdict().items()):
            return None, 422, "tham số trong đường sai dạng"
        try:
            cap = parse_qsl(query, keep_blank_values=True, strict_parsing=True) if query else []
        except ValueError:
            return None, 422, "chuỗi tham số sai dạng"
        if len({k for k, _ in cap}) != len(cap) or any(k not in d["tham_so"] or not d["tham_so"][k].fullmatch(v) for k, v in cap):
            return None, 422, "tham số không có trong danh sách hoặc sai dạng"
        return d, path + ("?" + urlencode(cap) if cap else ""), None
    return None, 404, "không có đường này"


def goi_xem(duong: str):
    c = http.client.HTTPConnection(*BACKEND, timeout=HET_GIO)
    try:
        h = {"Host": "kpi-backend", "Accept": "application/json"}
        if MA_PHIEN:
            h["X-Kpi-Session"] = MA_PHIEN
        c.request("GET", duong, headers=h)
        r = c.getresponse()
        than = r.read(TRAN_BYTE + 1)
        return r.status, (r.getheader("Content-Type") or "").split(";")[0].strip().lower(), than
    finally:
        c.close()


class XuLy(BaseHTTPRequestHandler):
    server_version = "kpi-xem-cong"
    sys_version = ""
    duong: list[dict] = []
    van_tay_tinh: dict = {}

    def _tra(self, ma: int, obj, loai: str = "application/json") -> None:
        b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(ma)
        self.send_header("Content-Type", f"{loai}; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b)

    def _cua(self) -> bool:
        """Rào Host rồi mã. Trả True nếu được đi tiếp (đã tự trả lời khi sai)."""
        if (self.headers.get("Host") or "").strip().lower() not in HOST_CHO_PHEP:
            self._tra(403, {"refused": "Host không được phép"})
            return False
        if not hmac.compare_digest((self.headers.get("X-Xem-Ma") or "").encode("utf-8"), MA.encode("utf-8")):
            log.warning("tu_choi thieu_ma")
            self._tra(401, {"refused": "thiếu hoặc sai mã đọc số"})
            return False
        return True

    def do_GET(self):
        if not self._cua():
            return
        if not _cung_luc.acquire(blocking=False):
            return self._tra(429, {"refused": "đang có nhiều yêu cầu — thử lại sau"})
        try:
            self._doc()
        finally:
            _cung_luc.release()

    def _doc(self):
        duong_goc, _, truy_van = self.path.partition("?")   # không dùng urlsplit: "//x/y" bị hiểu là tên máy "x" (http.server đã gộp "//" đầu thành "/")
        try:
            if duong_goc == "/van-tay" and not truy_van:
                ma, _, than = goi_xem("/api/meta")
                m = json.loads(than) if ma == 200 else None
                if not isinstance(m, dict) or any(k not in m or m[k] in (None, "", {}, []) for k in VAN_TAY_TRUONG):
                    return self._tra(502, {"loi": True})   # [D S1] trường vân tay THIẾU / rỗng ⇒ lỗi (bên so coi là LỆCH), không trả None ("khớp" giả)
                return self._tra(200, {"tinh": self.van_tay_tinh, "dong": {k: m.get(k) for k in VAN_TAY_TRUONG}})
            ten, duong, loi = chon(self.duong, duong_goc, truy_van)
            if ten is None:
                return self._tra(duong, {"refused": loi})
            muc, ten = ten, ten["ten"]
            ma, loai, than = goi_xem(duong)
        except (OSError, http.client.HTTPException, ValueError) as e:
            log.warning("loi_hop_xem %s", type(e).__name__)
            return self._tra(502, {"refused": "không gọi được hộp xem KPI — thử lại sau"})
        if len(than) > TRAN_BYTE or loai != muc["loai"]:
            log.warning("tu_choi tra_ve duong=%s http=%d co=%d", ten, ma, len(than))
            return self._tra(502, {"refused": "hộp xem trả về quá lớn hoặc sai loại"})
        log.info("xem duong=%s http=%d co=%d", ten, ma, len(than))
        return self._tra(ma, than, loai)

    def _khong_cho(self):
        if self._cua():
            self._tra(405, {"refused": "hộp này chỉ nhận GET"})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _khong_cho

    def log_message(self, *a):   # không ghi dòng truy cập mặc định (có tham số)
        pass


if __name__ == "__main__":
    if not re.fullmatch(r"[0-9a-f]{32,128}", MA):
        raise SystemExit("thiếu XEM_MA (hex ≥ 32 ký tự) — file $CAPITOL_STATE_DIR/xem_18403.env quyền 600")
    XuLy.duong = nap_duong(os.environ["XEM_DUONG_FILE"], os.environ.get("XEM_DUONG_SHA256", ""))
    XuLy.van_tay_tinh = json.loads(os.environ.get("XEM_VAN_TAY_TINH") or "{}")
    s = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("XEM_PORT", "8121"))), XuLy)
    log.info("kpi-xem-cong nghe :%s ⇒ %s:%s, %d đường", s.server_address[1], *BACKEND, len(XuLy.duong))
    s.serve_forever()
