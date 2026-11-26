"""Hộp chuyển tiếp TẢI FILE KPI — B 10/11/2026 (đã duyệt kế hoạch 1–4; hợp đồng mekongai HOP_DONG_UPLOAD_E.md, develop e0b96682).
Vì sao có hộp này: kpi-backend chỉ ở mạng nội bộ kpi_noi_bo, không mở cổng; máy chủ trang (18400) KHÔNG được chạy docker exec với dữ liệu
người dùng và KHÔNG giữ khoá ⇒ hộp nhỏ này nằm ở cả kpi_noi_bo lẫn 1 mạng riêng chỉ để mở cổng 127.0.0.1 ra máy.
Đường (18/11 thêm 4 đường ghép cột — xem BANG / DUONG_JSON): POST /tai-len?loai=raw|tracking|chi_tieu&ky=YYYY-MM[&ngay_chot=YYYY-MM-DD][&data_version=hex]
  thân = byte file .xlsx (application/octet-stream, Content-Length ≤ 20 MB, 4 byte đầu PK\\x03\\x04) ⇒ POST kpi-backend:8110/api/system/upload
  kèm X-Kpi-Sys-Key (khoá CHỈ trong env hộp này) ⇒ trả nguyên mã + JSON của backend (lý do từ chối của backend không lặp chữ trong file).
GIỚI HẠN (A ghi): khoá KPI_SYS_KEY là khoá CHUNG mọi đường hệ thống của backend (backend chưa có khoá chỉ-tải-lên) ⇒ lớp hạn chế duy nhất là
  hộp này chỉ mở 1 đường. Nhật ký: chỉ sha256 + cỡ + mã HTTP — KHÔNG tên file (trang cũng không gửi tên file). Một lượt một lúc.
Chạy (trong hộp): KPI_SYS_KEY=… python -I chuyen_tiep.py   (compose.kpi_tai_len.yaml)
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
from urllib.parse import parse_qsl, urlencode, urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kpi_tai_len")
TRAN_BYTE = 20 * 1024 * 1024
BACKEND = (os.environ.get("KPI_BACKEND_HOST", "kpi-backend"), int(os.environ.get("KPI_BACKEND_PORT", "8110")))
HET_GIO = 180
# [ghi chú] thêm hai loại: so_tracking (sổ theo dõi .xlsx) + trang_chi_tieu (trang theo dõi .html — CHỮ, không phải
# gói "PK", trần RIÊNG 5 MB theo lõi). Ba loại .xlsx giữ kiểm 4 byte "PK" + trần 20 MB như cũ.
LOAI = {"raw", "tracking", "chi_tieu", "so_tracking", "trang_chi_tieu"}
TRAN_TRANG = 5 * 1024 * 1024
BAN = re.compile(r"[0-9a-f]{64}", re.ASCII)   # mã bản dữ liệu của lõi: sha256 hex chữ thường, ĐÚNG 64 ký tự (dùng fullmatch)
MAU = {"loai": re.compile(r"^(raw|tracking|chi_tieu|so_tracking|trang_chi_tieu)$"), "ky": re.compile(r"^\d{4}-\d{2}$"),
       "ngay_chot": re.compile(r"^\d{4}-\d{2}-\d{2}$"), "data_version": re.compile(r"^[0-9a-f]{1,64}$")}
_mot_luot = threading.Lock()
# [D mục 18, major] chống DNS rebinding: trang lạ trỏ tên miền của nó về 127.0.0.1 ⇒ trình duyệt coi cùng origin ⇒ POST file lạ (hộp tự gắn
# khoá) ⇒ đầu độc dữ liệu KPI. Chỉ nhận Host đúng địa chỉ máy (giống rào Host của backend); khác ⇒ 403.
# [B 12/11, đã giao — C đo: hộp ab-* gọi vòng host.docker.internal tới được cổng 18402, hộp này tự gắn khoá hệ thống ⇒ hộp khác POST là đổi được
# dữ liệu KPI] Mọi yêu cầu phải kèm MÃ RIÊNG ở header X-Tai-Len-Ma (file 600 $CAPITOL_STATE_DIR/tai_len_18402.env, ngoài repo, không bind vào hộp
# nào; chỉ compose của hộp này + may_chu đọc). Thiếu / sai ⇒ 401 TRƯỚC khi đọc thân và trước khi chạm kpi-backend. So bằng hmac.compare_digest.
MA_RIENG = os.environ.get("TAI_LEN_MA", "")
# [ghi chú] GET /van-tay: vân tay hộp KPI CHÍNH cho trang :5183 so với hộp xem. Mã ĐỌC riêng (header
# X-Van-Tay-Ma, file $CAPITOL_STATE_DIR/van_tay_18402.env) — KHÁC mã tải lên: người chỉ được đọc vân tay không tải file lên được và ngược lại.
# Không đặt VAN_TAY_MA ⇒ đường này không tồn tại (404). Chỉ gọi ĐÚNG MỘT địa chỉ viết cứng /api/meta của hộp chính (không khoá, không tham số
# từ người gọi) và chỉ trả các trường trong VAN_TAY_TRUONG + phần tĩnh (file VAN_TAY_TINH_FILE: sha ảnh chụp + sha file dữ liệu).
MA_VAN_TAY = os.environ.get("VAN_TAY_MA", "")
VAN_TAY_TRUONG = [x for x in os.environ.get("VAN_TAY_TRUONG", "period,cutoff,files_sha256,rule_version,workflow_version,status").split(",") if x]
# [D S3] phần tĩnh ĐỌC TỪ FILE mỗi lần gọi: bat_kpi_backend_raw.sh ghi lại file này MỖI lần bật hộp chính (thư mục ket_qua/kpi_van_tay gắn chỉ đọc)
# ⇒ đổi sha hộp chính mà không tạo lại hộp này thì vân tay vẫn đúng; file thiếu / hỏng ⇒ 502 (LỆCH).
VAN_TAY_TINH_FILE = os.environ.get("VAN_TAY_TINH_FILE", "/van_tay/van_tay_tinh.json")
# [ghi chú] 4 đường GHÉP CỘT (màn ghép của trang). Chỉ chuyển tiếp ĐÚNG các đường viết cứng dưới,
# tham số / thân kiểm chặt trước khi chạm backend; backend tự kiểm lại đủ. Nhật ký: chỉ đường + sha / cỡ + mã HTTP (thân JSON có tên người duyệt,
# câu trả lời kiểm ⇒ KHÔNG ghi).
BANG = re.compile(r"^[a-z_]{1,40}$")              # backend chỉ có "don_hang" (core/khuon.py SCHEMAS) — tên khác backend trả 404 / 422
SO_NHO = re.compile(r"^\d{1,3}$")                # sheet_so / dong_tieu_de
KY = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])", re.ASCII)   # [va_hop_chuyen_tiep_ky] kỳ của đường GET /ky/<kỳ>: đúng 4 chữ số ASCII, gạch, tháng 01–12 (dùng fullmatch)
TRAN_JSON = 64 * 1024
DUONG_JSON = {"/ghep-thu": "/api/system/ghep-thu", "/ghep-xac-nhan": "/api/system/ghep-xac-nhan"}
HOST_CHO_PHEP = {h.strip().lower() for h in os.environ.get("TAI_LEN_HOST_CHO_PHEP", "127.0.0.1:18402,localhost:18402").split(",") if h.strip()}


def _goi_backend(phuong_thuc: str, duong: str, than: bytes | None, kieu: str | None) -> tuple[int, bytes]:
    """Gọi ĐÚNG một đường của kpi-backend kèm khoá hệ thống. Lỗi mạng ⇒ ném (người gọi trả 502). Thân trả không phải JSON ⇒ thay bằng câu báo."""
    h = {"X-Kpi-Sys-Key": os.environ["KPI_SYS_KEY"], "Host": "kpi-backend"}
    if than is not None:
        h |= {"Content-Type": kieu or "application/octet-stream", "Content-Length": str(len(than))}
    c = http.client.HTTPConnection(*BACKEND, timeout=HET_GIO)
    try:
        c.request(phuong_thuc, duong, body=than, headers=h)
        r = c.getresponse()
        tl, ma = r.read(512 * 1024), r.status
    finally:
        c.close()
    try:
        json.loads(tl)
    except (json.JSONDecodeError, UnicodeDecodeError):
        tl = json.dumps({"refused": f"backend trả HTTP {ma} không đọc được"}, ensure_ascii=False).encode("utf-8")
    return ma, tl


class XuLy(BaseHTTPRequestHandler):
    server_version = "kpi-tai-len"
    sys_version = ""

    def _tra(self, ma: int, obj) -> None:
        b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(ma)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b)

    def _host_dung(self) -> bool:
        return (self.headers.get("Host") or "").strip().lower() in HOST_CHO_PHEP

    def _ma_dung(self) -> bool:
        return hmac.compare_digest((self.headers.get("X-Tai-Len-Ma") or "").encode("utf-8"), MA_RIENG.encode("utf-8"))

    def _van_tay(self):
        if not MA_VAN_TAY or not hmac.compare_digest((self.headers.get("X-Van-Tay-Ma") or "").encode("utf-8"), MA_VAN_TAY.encode("utf-8")):
            return self._tra(401 if MA_VAN_TAY else 404, {"refused": "thiếu hoặc sai mã đọc vân tay" if MA_VAN_TAY else "không có đường này"})
        try:
            c = http.client.HTTPConnection(*BACKEND, timeout=10)
            try:
                c.request("GET", "/api/meta", headers={"Host": "kpi-backend"})   # địa chỉ viết cứng; /api/meta không cần khoá
                r = c.getresponse()
                m = json.loads(r.read(256 * 1024)) if r.status == 200 else None
            finally:
                c.close()
        except (OSError, http.client.HTTPException, ValueError):
            m = None
        if not isinstance(m, dict) or any(k not in m or m[k] in (None, "", {}, []) for k in VAN_TAY_TRUONG):
            return self._tra(502, {"loi": True})   # [D S1] thiếu / rỗng một trường vân tay ⇒ lỗi, bên so coi là LỆCH (không trả None ⇒ "khớp" giả)
        try:
            tinh = json.loads(open(VAN_TAY_TINH_FILE, encoding="utf-8").read())
        except (OSError, ValueError):
            tinh = None
        if not isinstance(tinh, dict) or not tinh.get("kpi_head"):
            return self._tra(502, {"loi": True})
        return self._tra(200, {"tinh": tinh, "dong": {k: m.get(k) for k in VAN_TAY_TRUONG}})

    def do_GET(self):
        if not self._host_dung():
            return self._tra(403, {"refused": "Host không được phép"})
        if self.path == "/van-tay":
            return self._van_tay()
        if not self._ma_dung():
            return self._tra(401, {"refused": "thiếu hoặc sai mã của hộp tải file"})
        u = urlparse(self.path)
        if u.path == "/song":
            return self._tra(200, {"ok": True})
        if u.path.startswith("/khuon/") and not u.query:   # [ghi chú] khuôn cột chuẩn của bảng (màn ghép — cột trái)
            bang = u.path[len("/khuon/"):]
            if not BANG.fullmatch(bang):
                return self._tra(422, {"refused": "tên bảng sai dạng"})
            try:
                ma, tl = _goi_backend("GET", f"/api/system/khuon/{bang}", None, None)
            except (OSError, http.client.HTTPException) as e:
                log.warning("loi_backend khuon %s", type(e).__name__)
                return self._tra(502, {"refused": "không gọi được backend KPI — thử lại sau"})
            log.info("khuon bang=%s http=%d", bang, ma)
            return self._tra(ma, tl)
        # [ghi chú] HAI đường CHỈ ĐỌC "kỳ này có gì" cho bảng tháng của Trang Dữ liệu:
        #   GET /ky ⇒ /api/system/ky        GET /ky/<YYYY-MM> ⇒ /api/system/ky/<YYYY-MM>.   Tới đây đã qua rào Host + mã riêng ở trên (thiếu / sai ⇒ 403 / 401, chưa chạm lõi).
        # Lõi khai đường thứ hai là {period:path} (nhận cả dấu gạch chéo) ⇒ hộp TỰ CHẶN: kỳ phải khớp ĐÚNG khuôn KY trên đường dẫn THÔ (chưa giải mã %): có "/", "..", "%",
        # khoảng trắng, chữ số không phải ASCII, tháng 00 / 13… ⇒ 422, KHÔNG ghép chuỗi lạ vào đường của lõi. Không nhận tham số query. Trả nguyên mã HTTP + thân của lõi.
        if u.path in ("/ky", "/ky/") or u.path.startswith("/ky/"):
            # đường dẫn THÔ phải bằng ĐÚNG phần path đã phân tích: urlparse tự bỏ ký tự xuống dòng / tab và tách ? ; # — chuỗi thô khác đi một ký tự (có tham số, dấu ? trống,
            # ký tự điều khiển bị bỏ) ⇒ 404, không đoán ý người gọi
            # [va_hop_kpi_ky_trong] RIÊNG đường một kỳ nhận ĐÚNG MỘT tham số: ?data_version=<64 ký tự hex> (ba tổng của một bản — lõi fdd20ab0). Chuỗi thô phải bằng ĐÚNG
            # "<path>?data_version=<mã>"; tham số khác / lặp / mã sai dạng ⇒ 404 / 422, không chạm lõi. Đường danh sách /ky vẫn không nhận tham số.
            ban = None
            if u.query and u.path.startswith("/ky/") and not u.params and not u.fragment and u.query.startswith("data_version=") and self.path == u.path + "?" + u.query:
                ban = u.query[len("data_version="):]
                if not BAN.fullmatch(ban):
                    return self._tra(422, {"refused": "mã bản dữ liệu sai dạng"})
            elif u.query or u.params or u.fragment or self.path != u.path:
                return self._tra(404, {"refused": "không có đường này"})
            if u.path == "/ky":
                duong = "/api/system/ky"
            else:
                ky = u.path[len("/ky/"):]
                if not KY.fullmatch(ky):
                    return self._tra(422, {"refused": "kỳ sai dạng — cần YYYY-MM"})
                duong = f"/api/system/ky/{ky}" + (f"?data_version={ban}" if ban else "")
            try:
                ma, tl = _goi_backend("GET", duong, None, None)
            except (OSError, http.client.HTTPException) as e:
                log.warning("loi_backend ky %s", type(e).__name__)
                return self._tra(502, {"refused": "không gọi được backend KPI — thử lại sau"})
            log.info("ky duong=%s http=%d", "danh_sach" if duong.endswith("/ky") else ("mot_ky_theo_ban" if ban else "mot_ky"), ma)
            return self._tra(ma, tl)
        return self._tra(404, {"refused": "không có đường này"})

    def do_POST(self):
        if not self._host_dung():
            log.warning("tu_choi host_la")
            return self._tra(403, {"refused": "Host không được phép"})
        if not self._ma_dung():
            log.warning("tu_choi thieu_ma")
            return self._tra(401, {"refused": "thiếu hoặc sai mã của hộp tải file"})
        u = urlparse(self.path)
        if u.path in DUONG_JSON:
            return self._chuyen_json(u)
        if u.path == "/bo-cuc":
            return self._bo_cuc(u)
        if u.path != "/tai-len":
            return self._tra(404, {"refused": "không có đường này"})
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        if set(q) - set(MAU) or any(not MAU[k].match(v) for k, v in q.items()) or "loai" not in q or "ky" not in q:
            return self._tra(422, {"refused": "tham số sai: cần loai (raw|chi_tieu|so_tracking|trang_chi_tieu|tracking), ky (YYYY-MM); ngay_chot (YYYY-MM-DD), data_version (hex) tuỳ chọn"})
        if q["loai"] == "tracking" and "ngay_chot" not in q:
            return self._tra(422, {"refused": "file Tracking cần ngày chốt"})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
            return self._tra(415, {"refused": "cần gửi thân nhị phân (application/octet-stream)"})
        n = self.headers.get("Content-Length")
        if n is None or not n.isdigit():
            return self._tra(411, {"refused": "thiếu Content-Length"})
        n = int(n)
        la_trang = q["loai"] == "trang_chi_tieu"; tran = TRAN_TRANG if la_trang else TRAN_BYTE
        if n < 4 or n > tran:   # kiểm TRƯỚC khi đọc thân
            return self._tra(413, {"refused": f"file phải từ 4 byte tới {tran // (1024 * 1024)} MB"})
        if not _mot_luot.acquire(blocking=False):
            return self._tra(409, {"refused": "đang xử lý một file khác — thử lại sau", "code": "dang_ban"})   # cùng mã với lõi fdd20ab0
        try:
            than = self.rfile.read(n)
            if len(than) != n:
                return self._tra(400, {"refused": "thân không đủ byte"})
            sha = hashlib.sha256(than).hexdigest()
            if la_trang:   # trang .html là CHỮ; file Excel gửi nhầm vào loại này ⇒ nói rõ, không đẩy gói nén vào bộ đọc trang của lõi
                if than[:4] == b"PK\x03\x04":
                    log.info("tu_choi trang_la_goi_nen sha=%s co=%d", sha[:16], n)
                    return self._tra(422, {"refused": "đây là file Excel, không phải trang theo dõi (.html) — chọn lại loại file"})
            elif than[:4] != b"PK\x03\x04":
                log.info("tu_choi khong_phai_xlsx sha=%s co=%d", sha[:16], n)
                return self._tra(422, {"refused": "không phải file .xlsx"})
            c = http.client.HTTPConnection(*BACKEND, timeout=HET_GIO)
            try:
                c.request("POST", "/api/system/upload?" + urlencode(q), body=than,
                          headers={"X-Kpi-Sys-Key": os.environ["KPI_SYS_KEY"], "Content-Type": "application/octet-stream",
                                   "Content-Length": str(n), "Host": "kpi-backend"})
                r = c.getresponse()
                tl = r.read(256 * 1024)
                ma = r.status
            finally:
                c.close()
            log.info("tai_len sha=%s co=%d loai=%s http=%d", sha[:16], n, q["loai"], ma)
            try:
                json.loads(tl)
            except (json.JSONDecodeError, UnicodeDecodeError):
                tl = json.dumps({"refused": f"backend trả HTTP {ma} không đọc được"}, ensure_ascii=False).encode("utf-8")
            return self._tra(ma, tl)
        except (OSError, http.client.HTTPException) as e:
            log.warning("loi_backend %s", type(e).__name__)
            return self._tra(502, {"refused": "không gọi được backend KPI — thử lại sau"})
        finally:
            _mot_luot.release()

    def _doc_than(self, kieu_can: str, tran: int):
        """(thân, None) hoặc (None, đã trả lời lỗi). Kiểm Content-Type + Content-Length TRƯỚC khi đọc."""
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != kieu_can:
            self._tra(415, {"refused": f"cần gửi thân {kieu_can}"}); return None
        n = self.headers.get("Content-Length")
        if n is None or not n.isdigit():
            self._tra(411, {"refused": "thiếu Content-Length"}); return None
        n = int(n)
        if n < 2 or n > tran:
            self._tra(413, {"refused": f"thân phải từ 2 byte tới {tran} byte"}); return None
        than = self.rfile.read(n)
        if len(than) != n:
            self._tra(400, {"refused": "thân không đủ byte"}); return None
        return than

    def _bo_cuc(self, u):
        """[ghi chú] POST /bo-cuc?bang=…[&sheet_so=…&dong_tieu_de=…] thân .xlsx ⇒ /api/system/bo-cuc (đọc bố cục file — chưa tính, chưa lưu bản)."""
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        if set(q) - {"bang", "sheet_so", "dong_tieu_de"} or not BANG.fullmatch(q.get("bang", "")) \
                or any(k in q and not SO_NHO.fullmatch(q[k]) for k in ("sheet_so", "dong_tieu_de")) or (("sheet_so" in q) != ("dong_tieu_de" in q)):
            return self._tra(422, {"refused": "tham số sai: bang (chữ thường), sheet_so + dong_tieu_de (số, đi cùng nhau, tuỳ chọn)"})
        if not _mot_luot.acquire(blocking=False):
            return self._tra(409, {"refused": "đang xử lý một file khác — thử lại sau"})
        try:
            than = self._doc_than("application/octet-stream", TRAN_BYTE)
            if than is None:
                return None
            sha = hashlib.sha256(than).hexdigest()
            if than[:4] != b"PK\x03\x04":
                log.info("tu_choi bo_cuc khong_phai_xlsx sha=%s", sha[:16])
                return self._tra(422, {"refused": "không phải file .xlsx"})
            ma, tl = _goi_backend("POST", "/api/system/bo-cuc?" + urlencode(q), than, "application/octet-stream")
            log.info("bo_cuc sha=%s co=%d http=%d", sha[:16], len(than), ma)
            return self._tra(ma, tl)
        except (OSError, http.client.HTTPException) as e:
            log.warning("loi_backend bo_cuc %s", type(e).__name__)
            return self._tra(502, {"refused": "không gọi được backend KPI — thử lại sau"})
        finally:
            _mot_luot.release()

    def _chuyen_json(self, u):
        """[ghi chú] POST /ghep-thu | /ghep-xac-nhan thân JSON (object, ≤ 64 KB) ⇒ đường /api/system/… tương ứng. Không nhận tham số query."""
        if u.query:
            return self._tra(422, {"refused": "đường này không nhận tham số trên địa chỉ"})
        if not _mot_luot.acquire(blocking=False):
            return self._tra(409, {"refused": "đang xử lý một file khác — thử lại sau"})
        try:
            than = self._doc_than("application/json", TRAN_JSON)
            if than is None:
                return None
            try:
                ok = isinstance(json.loads(than), dict)
            except (json.JSONDecodeError, UnicodeDecodeError):
                ok = False
            if not ok:
                return self._tra(422, {"refused": "thân phải là một JSON object"})
            ma, tl = _goi_backend("POST", DUONG_JSON[u.path], than, "application/json")
            log.info("%s co=%d http=%d", u.path.strip("/"), len(than), ma)   # KHÔNG ghi thân (tên người duyệt, câu trả lời kiểm)
            return self._tra(ma, tl)
        except (OSError, http.client.HTTPException) as e:
            log.warning("loi_backend %s %s", u.path.strip("/"), type(e).__name__)
            return self._tra(502, {"refused": "không gọi được backend KPI — thử lại sau"})
        finally:
            _mot_luot.release()

    def log_message(self, *a):   # không ghi dòng truy cập mặc định (có đường dẫn + tham số); chỉ dòng log ở trên
        pass


if __name__ == "__main__":
    if len(os.environ.get("KPI_SYS_KEY", "")) < 16:
        raise SystemExit("thiếu KPI_SYS_KEY (≥ 16 ký tự)")
    if not re.fullmatch(r"[0-9a-f]{32,128}", MA_RIENG):   # thiếu / ngắn ⇒ KHÔNG bật (không có chế độ "không mã")
        raise SystemExit("thiếu TAI_LEN_MA (hex ≥ 32 ký tự) — tạo file $CAPITOL_STATE_DIR/tai_len_18402.env quyền 600")
    if MA_VAN_TAY and (not re.fullmatch(r"[0-9a-f]{32,128}", MA_VAN_TAY) or hmac.compare_digest(MA_VAN_TAY, MA_RIENG)):
        raise SystemExit("VAN_TAY_MA sai dạng hoặc TRÙNG mã tải lên — hai mã phải khác nhau")
    s = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("TAI_LEN_PORT", "8120"))), XuLy)   # trong hộp; ra máy CHỈ 127.0.0.1 (compose)
    s.timeout = 30
    log.info("kpi-tai-len nghe :%s ⇒ %s:%s", s.server_address[1], *BACKEND)
    s.serve_forever()
