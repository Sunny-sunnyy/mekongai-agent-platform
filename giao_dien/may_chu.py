"""Máy chủ trang kéo–thả workflow trên bộ chạy thử Capitol. Viết 04/11/2026 (nhóm phát triển; thiết kế THIET_KE_KEO_THA.md, yêu cầu vận hành).

Chạy:  python3 -I chay_thu/giao_dien/may_chu.py      rồi mở  http://127.0.0.1:18400     (trang xem cũ: /xem)
Cần:   11 hộp capitol-* đang bật; bản dựng giao diện `chay_thu/giao_dien/web/dist/` (cd web && npm run build).

Đọc:  PostgreSQL của bộ chạy thử (hàm của xem_workflow.py) — chỉ SELECT.
Ghi:  CHỈ gọi agentic-backend ở 127.0.0.1:18300 — lưu (POST /api/v1/orgs/{org}/workflows), chạy (run-workflow), duyệt / từ chối (resume / stop).
      Chạy = gọi AI bằng khoá OpenAI của người dùng ⇒ TỐN TIỀN THẬT; trần TRAN_LUOT lượt / TRAN_GIAY giây.
Mọi lệnh ghi được ghi nhật ký ở chay_thu/runtime/giao_dien/nhat_ky.txt (Git bỏ qua).
Không bao giờ: xoá workflow / lượt, đổi cấu hình hộp, mở file khoá. Chỉ nghe 127.0.0.1.
"""

import ast
import hmac
import json
import os
import secrets
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))   # chạy bằng python3 -I: thư mục của script không tự vào sys.path
import xem_workflow as xem                                   # đọc PostgreSQL, che khoá, đồ thị cho trang xem cũ  # noqa: E402
from capitol_chuyen_doi import DanhMuc, kiem, sang_capitol, sang_chung, tu_capitol, tu_chung
from doi_chieu import doi_chieu, doi_chieu_khuon, nguon_cong_cu  # cải tiến (1), yêu cầu vận hành 04/11
import muc_toi_da                                             # [ghi chú kỹ thuật] mức tiền tối đa mỗi loại khối — giữ chỗ trước khi chạy
import tai_ve                                                 # [ghi chú kỹ thuật] tải file kết quả qua trang (link minio:9000 không mở được từ máy)
import khoi_kpi                                               # [ghi chú kỹ thuật] khối KPI dựng sẵn: nối dữ liệu + luật nhóm kỹ thuật (kpi_capitol/khoi_dung_san.py) vào trang
import thu_lai_403                                             # [ghi chú kỹ thuật] run-workflow trả 403 thất thường (quyền chưa commit ở phiên DB khác) ⇒ thử lại đúng 1 lần
import so_skill                                                # [ghi chú kỹ thuật] ghi sổ MỨC TỐI ĐA cho khối chạy trong hộp phiên (không để lại message_history)
import hoi_nhanh                                               # [ghi chú kỹ thuật] chế độ "hỏi nhanh" của trang Hỏi KPI: gắn thẳng mẫu KPI hỏi nhanh, không lượt AI định tuyến
import chat_operator                                          # [ghi chú kỹ thuật] chat Operator trên trang (giữ chỗ tiền + cấm công cụ chạy)
import dau_vet                                                # [ghi chú kỹ thuật] dấu vết lượt chạy (mô hình / công cụ / Rào / Người duyệt — từ bản ghi)
dau_vet.MO_HINH_HOP_LE = lambda m: m in muc_toi_da.GIA_BANG          # [ghi chú kỹ thuật] "mô hình hợp lệ" = có trong bảng cổng AI (luna + Claude qua Bedrock …)
dau_vet.CAU_HINH_FILE = lambda: mo_hinh_khoi_that()                    # file mô hình khối lúc XEM (đọc mỗi lần)
import truy_van_sql  # noqa: E402  [ghi chú kỹ thuật] câu SQL một lượt đã gọi — chỉ đọc bản ghi
import mo_khong_doi_huong as _MKDH  # noqa: E402  [ghi chú kỹ thuật] lời gọi hộp 18402 kèm mã riêng KHÔNG theo đổi hướng 3xx
import ghep_kpi  # noqa: E402  [ghi chú kỹ thuật] 4 đường màn ghép cột ⇒ hộp 18402 (kiểm khuôn ở ghep_kpi.py; 0 lượt AI)
import ban_du_lieu  # noqa: E402  [ghi chú kỹ thuật] chạy workflow KPI trên bản dữ liệu vừa tải (kiểm + điền ô kpi_data_version trước giữ chỗ tiền)
import tim_kho  # noqa: E402  [ghi chú kỹ thuật] trang tự gọi đường TÌM của cổng RAG (0 đồng)
import o_nhap  # noqa: E402  [ghi chú kỹ thuật] ô nhập chữ lúc Chạy: không cắt, vượt chốt thì từ chối
import kiem_luc_chay  # noqa: E402  [ghi chú kỹ thuật] kiểm hình lúc Chạy + cảnh báo nối file lúc Lưu
import kho_tai_lieu as KTL  # noqa: E402  [ghi chú kỹ thuật] "nạp vào kho rồi hỏi" bằng app RAG mekongai
_STATE_DIR = Path(os.environ.get("CAPITOL_STATE_DIR") or (Path.home() / ".local" / "share" / "capitol-platform")).expanduser()
_RUNTIME_DIR = Path(os.environ.get("CAPITOL_RUNTIME_DIR") or (Path(__file__).resolve().parent.parent / "runtime")).expanduser()
_KPI_TAI_LEN_URL = os.environ.get("KPI_TAI_LEN_URL", "http://127.0.0.1:18402").rstrip("/")
SO_KHO = KTL.So(_STATE_DIR / "kho_tai_lieu")
GOC_DU_LIEU = _STATE_DIR / "trang_du_lieu"   # [ghi chú kỹ thuật] sổ lần tải file KPI + tài liệu người dùng bấm Ẩn (không tên file, không nội dung)
# Đường tới app RAG: ĐI QUA hộp cổng RAG nhóm kỹ thuật (gắn tổ chức ở cổng — E major 1). Chưa đặt ⇒ mọi đường nạp trả 503, không gọi gì (0 đồng).
# [ghi chú kỹ thuật] nạp / xem kho đi QUA cổng RAG theo tổ chức (B, goi_cong_rag.py): cổng suy user_id từ X-Rag-Org — trang không tự khai.
# "Đã nối" = env may_chu có RAG_MA + RAG_NAP_MA ($CAPITOL_STATE_DIR/rag_cong.env + rag_nap.env, 600). Trống ⇒ nạp 503, không gọi gì.
RAG_APP = bool(os.environ.get("RAG_MA") and os.environ.get("RAG_NAP_MA"))
import importlib.util as _ilu  # noqa: E402
# [ghi chú kỹ thuật] luật "≤ 1 khối hộp phiên mỗi tầng": MỘT nguồn tài liệu kỹ thuật (cũng dùng ở dung_lai_workflow + kiem_sau_ghep).
# Nạp theo ĐƯỜNG FILE (không thêm r2/cong_cu vào sys.path — thư mục đó có module khác trùng tên). Thiếu file ⇒ máy chủ KHÔNG bật (đóng khi thiếu luật).
_lt_path = Path(__file__).resolve().with_name("luat_tang.py")
if not _lt_path.is_file():
    _lt_path = Path(__file__).resolve().parent.parent / "r2" / "cong_cu" / "luat_tang.py"
_sp_lt = _ilu.spec_from_file_location("luat_tang", _lt_path)
luat_tang = _ilu.module_from_spec(_sp_lt); _sp_lt.loader.exec_module(luat_tang)
import luat_hoi_du_lieu  # noqa: E402  [ghi chú kỹ thuật] luật nối khối của workflow "Hỏi dữ liệu bằng lời" (va_kiem_so_sql, D ĐẠT giấy)
import kho_bang  # noqa: E402  [ghi chú kỹ thuật] dataset của khối hỏi kho bảng phải thuộc tổ chức của trang
import chay_lai as CL  # noqa: E402  [ghi chú kỹ thuật] chạy lại từ khối: kiểm khoá trước (kiem_khoa_chay_lai.py chạy trong ab-api) — chép cạnh file này lúc gắn
import cache_tra_loi as CTL  # noqa: E402  [ghi chú kỹ thuật]
KHO_CTL = CTL.KhoCache()   # trong bộ nhớ tiến trình ⇒ bật lại may_chu là rỗng (chiều an toàn: chạy lượt mới)


def _van_tay_kpi() -> dict | None:
    """Vân tay hộp KPI LÚC HỎI (18402 /van-tay, mã đọc từ $CAPITOL_STATE_DIR/van_tay_18402.env — KHÔNG in). Lỗi ⇒ None ⇒ không dùng cache."""
    try:
        ma = (_STATE_DIR / "van_tay_18402.env").read_text().strip().split("=", 1)[1]
        r = _MKDH.mo(urllib.request.Request(f"{_KPI_TAI_LEN_URL}/van-tay", headers={"X-Van-Tay-Ma": ma}), timeout=15)
        d = json.loads(r.read())
        # [ghi chú kỹ thuật] thêm cấu hình mô hình khối vào vân tay ⇒ vào KHOÁ cache của cả 3 lối (hoi-kpi, hoi-nhanh, tro-ly):
        # đổi file mô hình (luna ⇄ Sonnet ⇄ Opus, đổi mức suy luận) ⇒ khoá khác ⇒ không trả lại câu của mô hình cũ trong 15 phút.
        return {"tinh": d.get("tinh"), "dong": d.get("dong"), "mo_hinh_khoi": _mo_hinh_khoi()} if isinstance(d, dict) and d.get("dong") else None
    except Exception:  # noqa: BLE001 — không đọc được vân tay ⇒ không cache (không đoán)
        return None


def _mo_hinh_khoi() -> str:
    """Cấu hình mô hình khối ĐANG ghi trong file (mô hình mặc định + bảng theo loại khối, tên đã gồm đuôi mức suy luận) — đọc file MỖI lần hỏi
    (muc_toi_da._doc_ep_mo_hinh, cùng luật đọc với bản vá trong hộp). Không có file / không có dòng ⇒ '["", []]' (= luna). File hỏng ⇒ "HONG"
    (lúc đó khối cũng bị chặn chạy; khoá riêng để không dùng lại câu cũ)."""
    try:
        mac_dinh, bang = muc_toi_da._doc_ep_mo_hinh()
        return json.dumps([mac_dinh, sorted(bang.items())], ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return "HONG"


def _cache_lay(k: str | None) -> dict | None:
    if not k:
        return None
    m = KHO_CTL.lay(k, lambda s: CTL.dat_kiem_phien(xem.sql(CTL.sql_kiem_phien(s))))
    if m:   # sổ: 1 dòng 0 đồng — đếm được số lần dùng lại (luật 3.9: mọi lượt trả lời qua trang đều có dòng sổ)
        _hoi_kpi().ghi_tien("trang_18400_cache", 0.0, {"session": "cache-" + uuid.uuid4().hex,
                                                       "ghi_chu": f"dùng lại phiên {m['session'][:8]} lúc {m['luc']} (cache câu trả lời, 0 lượt mới)"})
        ghi(f"CACHE dùng lại phiên {m['session']} (khởi lúc {m['luc']})")
    return m


def so_ban_luu(o: str) -> dict:
    """[ghi chú kỹ thuật] {mã workflow: số phiên bản LƯU} của tổ chức — KHÔNG đếm bản RUN / SCHEDULED_RUN / TRIGGERED_RUN (mỗi lần CHẠY Capitol ghi một bản như vậy).
    Dùng cho phép kiểm "workflow có sẵn bị đổi trong lượt chat" của chat_operator: chạy không làm đổi số; lưu / sửa thì có. Loại phiên bản lạ vẫn được đếm (an toàn)."""
    return xem.sql(f"""select coalesce(json_object_agg(w.id, (select count(*) from workflow_version v where v.workflow_id=w.id
            and v.version_type::text not in ('RUN', 'SCHEDULED_RUN', 'TRIGGERED_RUN'))), '{{}}')
        from workflow w where not coalesce(w.is_deleted,false) and w.{xem._loc_org(o)}""") or {}


def _cache_xoa(ly_do: str) -> None:
    n = KHO_CTL.xoa_het(ly_do)
    if n:
        ghi(f"CACHE xoá hết {n} mục — {ly_do}")

THU_MUC = Path(__file__).resolve().parent
DIST = THU_MUC / "web" / "dist"
KQ = _RUNTIME_DIR
NHAT_KY = KQ / "giao_dien" / "nhat_ky.txt"
API = os.environ.get("CAPITOL_API_URL", "http://127.0.0.1:18300").rstrip("/")
CONG = int(os.environ.get("MAY_CHU_CONG", "18400"))
TRAN_LUOT, TRAN_GIAY = int(os.environ.get("MAY_CHU_TRAN_LUOT", "20")), 600   # mặc định 20 lượt / 10 phút (thiết kế mục 6); bộ ca thử đặt cao hơn
GIA = json.loads((THU_MUC / "bang_gia.json").read_text())
DM = DanhMuc()
_luot_gan_day: deque = deque()


# [ghi chú kỹ thuật] MÃ MỞ TRANG. Vì sao: máy dùng Docker Desktop ⇒ MỌI hộp ở mạng thường gọi được 127.0.0.1:18400 của máy qua
# host.docker.internal và tự đặt header Host; máy chủ thấy bên gọi = 127.0.0.1 ⇒ rào Host / Origin (rào cho TRÌNH DUYỆT) không chặn (đo 12/11:
# từ capitol-ab-worker-temporal GET /api/dem ⇒ 200). Đóng: mọi đường /api/* (GET lẫn POST) đòi mã sinh lúc bật máy chủ. Mã KHÔNG nằm trong bất cứ
# thứ gì máy chủ trả ra khi chưa có mã; chỉ ghi vào file quyền 600 NGOÀI repo và ngoài mọi thư mục gắn vào hộp ($CAPITOL_STATE_DIR/). Terminal chỉ in
# ĐƯỜNG DẪN FILE, không in mã; không ai `cat` file này trong phiên agent — người dùng tự mở. Không có GET nào đổi trạng thái (rà _api_get 12/11).
# Người dùng mở MỘT LẦN đường dẫn http://127.0.0.1:<cổng>/#k=<mã> (phần sau # không gửi lên máy chủ, không vào nhật ký) ⇒ trang gửi POST /api/mo
# ⇒ máy chủ đặt cookie HttpOnly + SameSite=Strict ⇒ các yêu cầu sau (kể cả tải file) tự mang mã. Không lớp trung gian nào chèn mã hộ.
# File tĩnh (HTML / JS / CSS / phông) không cần mã. MA_MO = None (chưa tạo) ⇒ mọi /api/* bị từ chối (đóng khi nghi ngờ).
MA_MO: str | None = None


def tao_ma_mo() -> str:
    """Ghi đường mở trang vào $CAPITOL_STATE_DIR/mo_trang_<cổng>.txt (thư mục 700, file 600). Trả đường dẫn FILE (không trả mã).
    [Lần gắn 4 — người dùng: "sao phải lấy đường dẫn mỗi lần, mất công vậy"; nhóm phát triển xác nhận, đã soát] MÃ GIỮ CỐ ĐỊNH qua các lần khởi động lại: file đã có, đúng quyền
    600 (thư mục 700), đúng dạng ⇒ DÙNG LẠI mã cũ; chỉ sinh mã mới khi file thiếu / sai quyền / sai dạng, hoặc bật với DOI_MA_MO_TRANG=co (lộ file hay
    cookie thì đổi bằng biến này). Cùng cookie sống 30 ngày ⇒ sau lần dán đường dẫn đầu tiên, trình duyệt đó mở thẳng trang là vào.
    An toàn không đổi: hộp Docker không đọc được file 600 trên máy lẫn cookie của trình duyệt; đánh đổi: mã sống lâu hơn."""
    global MA_MO
    thu_muc = _STATE_DIR
    thu_muc.mkdir(mode=0o700, exist_ok=True)
    os.chmod(thu_muc, 0o700)
    f = thu_muc / f"mo_trang_{CONG}.txt"
    cu = None
    if os.environ.get("DOI_MA_MO_TRANG") != "co":
        try:
            if f.is_file() and not f.is_symlink() and not (os.stat(f).st_mode & 0o077) and os.stat(f).st_uid == os.getuid():
                m = re.fullmatch(r"http://127\.0\.0\.1:%d/#k=([A-Za-z0-9_-]{40,200})\s*" % CONG, f.read_text(encoding="utf-8"))
                cu = m.group(1) if m else None
        except OSError:
            cu = None
    if cu:
        MA_MO = cu
        return str(f)
    MA_MO = secrets.token_urlsafe(32)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as h:
        h.write(f"http://127.0.0.1:{CONG}/#k={MA_MO}\n")
    os.chmod(f, 0o600)
    return str(f)


def _hoi_ky_loi(duong: str):
    """[ghi chú kỹ thuật] GET hộp chuyển tiếp 18402 `/ky` | `/ky/<YYYY-MM>` bằng MÃ RIÊNG của máy chủ trang (đọc mỗi lần gọi, không in). Trả (mã HTTP, thân JSON | None).
    Trần 5 giây; thân ≤ 200.000 byte; đường khác hai dạng trên ⇒ ném ValueError (không ghép chuỗi lạ vào URL). Lỗi mạng ⇒ ném (người gọi rơi về sổ trang)."""
    if not re.fullmatch(r"/ky(/[0-9]{4}-(0[1-9]|1[0-2]))?", duong):
        raise ValueError("đường hỏi kỳ sai dạng")
    ma = _ma_tai_len()
    if ma is None:
        return 503, None
    try:
        r = _MKDH.mo(urllib.request.Request(_KPI_TAI_LEN_URL + duong, headers={"X-Tai-Len-Ma": ma}), timeout=5)
        code, than = r.status, r.read(200_001)
    except urllib.error.HTTPError as e:
        code, than = e.code, b""
    if len(than) > 200_000:
        return 502, None
    try:
        return code, (json.loads(than) if than else None)
    except ValueError:
        return code, None


def _ma_tai_len() -> str | None:
    """Mã riêng của hộp tải file KPI (đối chiếu kỹ thuật): $CAPITOL_STATE_DIR/tai_len_18402.env, 1 dòng TAI_LEN_MA=<hex 32–128>. Chỉ nhận khi file quyền 600."""
    f = _STATE_DIR / "tai_len_18402.env"
    try:
        if os.stat(f).st_mode & 0o077:
            return None
        for dong in f.read_text(encoding="utf-8").splitlines():
            if dong.startswith("TAI_LEN_MA="):
                v = dong.split("=", 1)[1].strip()
                return v if re.fullmatch(r"[0-9a-f]{32,128}", v) else None
    except OSError:
        pass
    return None


def ghi(msg: str) -> None:
    NHAT_KY.parent.mkdir(parents=True, exist_ok=True)
    with open(NHAT_KY, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%H:%M:%S %d/%m/%Y}] {msg}\n")


def org() -> str:
    return (KQ / "org_id.txt").read_text().strip()


def _ma_18300(goc: Path) -> dict:
    """[ghi chú kỹ thuật] Header mã cho hộp gác 18300 (Caddy chặn MẶC ĐỊNH mọi đường ghi / chạy khi thiếu mã — lượt AI ngoài sổ, E ca 1 + 2).
    Mã ở env/cong_18300.khoa.env (600, Git bỏ qua). Đọc MỖI lần gọi; thiếu / sai dạng ⇒ {} ⇒ Caddy trả 403 (hỏng ồn, không chạy ngoài sổ). Không in, không log."""
    try:
        _env_dir = Path(os.environ.get("CAPITOL_ENV_DIR") or (goc / "env")).expanduser()
        for dong in (_env_dir / "cong_18300.khoa.env").read_text(encoding="utf-8").splitlines():
            if dong.startswith("CONG_18300_MA="):
                ma = dong.split("=", 1)[1].strip()
                if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", ma):
                    return {"X-Chay-Thu-Ma": ma}
    except OSError:
        pass
    return {}


def goi_ab(method: str, path: str, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **_ma_18300(Path(__file__).resolve().parent.parent)})   # [ghi chú kỹ thuật]
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code = r.read().decode(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read().decode(), e.code
    try:
        return code, json.loads(raw)
    except json.JSONDecodeError:
        return code, raw


def kiem_ma(x) -> str:
    if not isinstance(x, str) or not xem.MA.fullmatch(x):   # [ghi chú kỹ thuật] fullmatch: "$" của match cho qua 1 "\n" cuối
        raise ValueError("mã không hợp lệ")
    return x


KQ_TRAN = 2_000_000   # trần ký tự một giá trị kết quả (đọc đủ, không cắt 4.000 như trang xem sự kiện)
# [ghi chú kỹ thuật] ast.literal_eval trên repr 2 MB dạng list các dict: 7 s + ~850 MB RAM trong tiến trình may_chu (máy từng treo vì hết RAM)
# ⇒ CHỈ literal_eval khi ≤ 256 KB (giá trị node_result lớn nhất trong DB lúc đo: 22.607 ký tự); json.loads (C, nhanh) giữ tới KQ_TRAN.
LITERAL_TRAN = 256_000


def _doc_gia_tri(s: str | None):
    """[ghi chú kỹ thuật] chuỗi kết quả khối ⇒ (kiểu, giá trị). JSON ⇒ dữ liệu; không được thì `ast.literal_eval` — CHỈ literal
    (số, chữ, list, dict, True/False/None), không chạy code — vì khối Xuất chữ gốc của Capitol in dict bằng str() (repr Python, nháy đơn).
    Quá trần / lỗi bất kỳ ⇒ ("chu", chuỗi gốc). Kiểu "kpi" = gói của Rào chắn chế độ code (có cau_tra_loi)."""
    if not isinstance(s, str) or not s.strip():
        return "chu", s
    if len(s) > KQ_TRAN:
        return "chu", s[:KQ_TRAN]
    t = s.strip()
    v = None
    try:
        v = json.loads(t)
    except ValueError:
        if t[0] in "{[" and len(t) <= LITERAL_TRAN:
            try:
                v = ast.literal_eval(t)
            except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                v = None
    if isinstance(v, (dict, list)):
        try:   # [ghi chú kỹ thuật] repr có bytes (b'..') / set / kiểu lạ ⇒ _json hỏng cả route ⇒ không nhận, trả chữ gốc
            json.dumps(v, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            return "chu", s
    if isinstance(v, dict):
        return ("kpi" if isinstance(v.get("cau_tra_loi"), str) else "dict"), v
    if isinstance(v, list):
        return "list", v
    return "chu", s


def ket_qua_luot(phien: str) -> dict | None:
    """Giá trị node_result CUỐI của từng khối trong lượt, đọc đủ (≤ KQ_TRAN) rồi phân loại bằng _doc_gia_tri. phien đã qua kiem_ma (UUID)."""
    r = xem.sql(f"select row_to_json(x) from (select r.id from workflow_run r where r.session_id='{kiem_ma(phien)}' "
                "order by r.created_at desc limit 1) x")
    if not r or not xem.MA.fullmatch(str(r.get("id") or "")):
        return None
    # [ghi chú kỹ thuật] lấy giá trị CUỐI mỗi khối ngay trong SQL (DISTINCT ON) — không kéo mọi node_result của lượt về máy chủ trang
    ev = xem.sql(f"""select coalesce(json_agg(json_build_object('n', x.n, 'v', x.v)), '[]') from (
            select distinct on (coalesce(e.node_instance_id, e.event_data->'data'->>'node_id'))
                   coalesce(e.node_instance_id, e.event_data->'data'->>'node_id') as n, left(e.event_data->'data'->>'value', {KQ_TRAN + 1}) as v
            from workflow_event e where e.run_id='{r["id"]}' and e.event_type='node_result'
              and coalesce(e.node_instance_id, e.event_data->'data'->>'node_id') is not null
            order by coalesce(e.node_instance_id, e.event_data->'data'->>'node_id'), e.timestamp desc, e.created_at desc) x""") or []
    cuoi = {}
    for e in ev:
        if e.get("n"):
            cuoi[e["n"]] = e.get("v")
    # [ghi chú kỹ thuật] mất sự kiện node_result (giờ Docker nhảy ⇒ hết lease, vd lượt E2B 4d741c6c) ⇒ khối THIẾU sự kiện lấy giá trị
    # từ bảng node_result (COMPLETED, output_data.value) — khối đã có sự kiện giữ nguyên giá trị sự kiện
    bu = xem.sql(f"""select coalesce(json_agg(json_build_object('n', n.node_instance_id, 'v', left(nr.output_data->>'value', {KQ_TRAN + 1}))), '[]')
            from node_result nr join node n on n.id = nr.node_id
            where nr.run_id='{r["id"]}' and nr.status='COMPLETED' and nr.output_data ? 'value'""") or []
    for e in bu:
        if e.get("n") and e["n"] not in cuoi:
            cuoi[e["n"]] = e.get("v")
    out = {}
    for n, v in cuoi.items():
        kieu, gt = _doc_gia_tri(v)
        out[n] = {"kieu": kieu, "v": gt}
    return out


# ------------------------------------------------------------ chạy lại từ khối [ghi chú kỹ thuật]
def _luot_nguon(s: str) -> dict | None:
    """Lượt mới nhất của phiên: trạng thái + workflow (chặn phiên chưa xong / khác workflow TRƯỚC kiểm khoá)."""
    return xem.sql(f"""select row_to_json(x) from (select status::text as status, workflow_id::text as workflow_id from workflow_run
        where session_id='{kiem_ma(s)}' order by created_at desc limit 1) x""")


def _khoa_luu(s: str) -> dict:
    """{mã khối: cache_key} các khối XONG của lượt mới nhất phiên s (như va_chay_lai/lay_khoa.sh)."""
    return xem.sql(f"""select coalesce(json_object_agg(n.node_instance_id, nr.cache_key), '{{}}') from node_result nr join node n on n.id=nr.node_id
        where nr.run_id=(select id from workflow_run where session_id='{kiem_ma(s)}' order by created_at desc limit 1)
          and nr.status='COMPLETED' and nr.cache_key is not null""") or {}


def _cung_to_chuc(wf: str) -> bool:
    """[ghi chú kỹ thuật] workflow.org_id = tổ chức của trang (Capitol rerun-from-node kiểm y vậy: workflow.py _assert_session_belongs_to_workflow)."""
    r = xem.sql(f"select row_to_json(x) from (select org_id::text as o from workflow where id='{kiem_ma(wf)}') x")
    return bool(r) and r.get("o") == org()


def _la_kpi(nodes, edges) -> bool:
    _hoi_kpi()   # thêm kpi_capitol vào sys.path
    import khoi_dung_san as _KD
    return _KD.la_workflow_kpi(nodes, edges)


def dung_lai_cua(s: str) -> dict:
    """Huy hiệu "dùng lại" của trang: khối nào ở lượt mới nhất phiên s lấy kết quả cũ (cached_from_run_id) + từ phiên nào."""
    r = xem.sql(f"""select row_to_json(x) from (select r.id as run, (select coalesce(json_agg(json_build_object('khoi', n.node_instance_id,
          'tu_phien', (select r2.session_id::text from workflow_run r2 where r2.id=nr.cached_from_run_id)) order by n.node_instance_id), '[]')
          from node_result nr join node n on n.id=nr.node_id where nr.run_id=r.id and nr.cached_from_run_id is not null) as dung_lai
        from workflow_run r where r.session_id='{kiem_ma(s)}' order by r.created_at desc limit 1) x""")
    if not r:
        raise LookupError("không thấy lượt của phiên này")
    return {"session": s, "dung_lai": r.get("dung_lai") or []}


# ------------------------------------------------------------ workflow
def pb_moi_nhat(wf: str) -> dict | None:
    return xem.sql(f"""select row_to_json(x) from (select version_number as so, version_type::text as loai, created_at as luc, payload
        from workflow_version where workflow_id='{wf}' and version_type::text <> 'RUN' order by version_number desc limit 1) x""")


def mot_workflow(wf: str) -> dict:
    v = pb_moi_nhat(wf)
    if not v:
        raise LookupError("không thấy workflow")
    hinh = sang_chung(tu_capitol(v["payload"], DM))   # trang nhận đúng hình chung v1 (tên khối ở ui.labels)
    hinh.update(id=wf, version=v["so"], saved_at=v["luc"])
    return hinh


def _kiem_wf_da_luu(wf: str) -> int | None:
    """[ghi chú kỹ thuật] workflow do CHAT tạo / sửa không đi qua /api/save ⇒ chạy lại phép kiểm của trang trên bản đã lưu; trả số lỗi (None = không đọc được)."""
    try:
        v = pb_moi_nhat(kiem_ma(wf))
        return sum(1 for x in kiem(tu_capitol(v["payload"], DM), DM) if x.get("muc") == "loi")
    except Exception:  # noqa: BLE001
        return None


# [ghi chú kỹ thuật] Trước đây trang chỉ kiểm HÌNH workflow (capitol_chuyen_doi.kiem); luật KPI chỉ chạy ở script đo và trang "Hỏi KPI"
# ⇒ người tự vẽ "Trợ lý KPI ⇒ Ô ra" vẫn Lưu + Chạy được. Nay kiểm ở MÁY CHỦ, cả lúc Lưu lẫn lúc Chạy (workflow do chat / API tạo không qua Lưu của
# trang). [Lần gắn 3, nhóm phát triển xác nhận theo D] MỘT nguồn luật ở module nhóm kỹ thuật: khoi_dung_san.kiem_khoi_dung_san (workflow KPI chỉ gồm 5 khối dựng sẵn, ô khoá
# bằng bản chuẩn, lời dặn đúng workflow_kpi.loi_dan) + workflow_kpi.kiem_payload (luật nối) — nối ở giao_dien/khoi_kpi.py, ở đây không còn danh sách riêng.
def kiem_kpi_payload(payload: dict, luc_chay: bool = False) -> list[str]:
    return khoi_kpi.kiem(_hoi_kpi(), org(), payload, luc_chay)


def kiem_chung(wf: dict) -> tuple[dict, list[dict]]:
    """Trang gửi hình chung ⇒ đổi sang hình trong + kiểm (lỗi hình chung trước, lỗi workflow sau)."""
    hinh, loi = tu_chung(wf)
    loi = loi + kiem(hinh, DM)
    # [ghi chú kỹ thuật] luật tầng hộp phiên (lượt FFIEC a579e1d0 hỏng SAU khi đã gọi AI vì 3 khối hộp phiên cùng tầng). Cần payload dạng lưu ⇒ đổi thử;
    # đổi hỏng thì hình đã có lỗi khác (kiem ở trên) và Lưu cũng hỏng ở sang_capitol ⇒ bỏ qua ở đây
    try:
        p_tang = sang_capitol(hinh, DM, hinh.get("id") or "kiem-tang")
    except Exception:  # noqa: BLE001
        p_tang = None
    if p_tang is not None:
        loi += [{"muc": "loi", "code": "hop_phien_cung_tang", "node": None, "field": None, "message": x,
                 "suggestion": "Nối dây để các khối tài liệu / chạy code chạy lần lượt (khối sau nhận từ khối trước)."} for x in luat_tang.loi_tang(p_tang)]
        loi += [{"muc": "loi", "code": "dataset_ngoai_to_chuc", "node": x["node"], "field": "datasets", "message": x["message"],   # [ghi chú kỹ thuật]
                 "suggestion": "Chọn lại ở ô Dataset — chỉ hiện dataset trong kho bảng của tổ chức này."} for x in kho_bang.kiem(p_tang, org())]
    return hinh, loi


def luu(wf: dict) -> tuple[int, dict]:
    hinh, loi = kiem_chung(wf)
    if any(x["muc"] == "loi" for x in loi):
        return 422, {"loi": "Kiểm chưa sạch — chưa lưu.", "kiem": loi}
    moi = not hinh.get("id")
    wf = hinh.get("id") or str(uuid.uuid4())
    kiem_ma(wf)
    payload = sang_capitol(hinh, DM, wf)
    # [ghi chú kỹ thuật] ô lời dặn KHOÁ do hệ thống điền: lưu là điền lại bản hiện hành (chỉ khi đang đúng khuôn). [D] máy chủ đổi nội dung người dùng gửi thì
    # phải NÓI RA: ghi nhật ký + trả `loi_dan_dien_lai` cho trang hiện câu báo.
    dien_lai = khoi_kpi.dien_loi_dan(_hoi_kpi(), org(), payload)
    if dien_lai:
        ghi(f"LƯU: điền lại lời dặn {dien_lai} khối Trợ lý KPI theo kỳ / ngày chốt hiện hành ('{hinh.get('name')}')")
    loi_kpi = kiem_kpi_payload(payload)
    if loi_kpi:
        ghi(f"LƯU bị chặn — luật KPI: {len(loi_kpi)} lỗi ('{hinh.get('name')}')")
        return 422, {"loi": "Workflow KPI chưa đúng luật an toàn — chưa lưu. " + " ".join(loi_kpi),
                     "kiem": loi + [{"muc": "loi", "code": "luat_kpi", "node": None, "field": None, "message": x, "suggestion": ""} for x in loi_kpi]}
    # [ghi chú kỹ thuật] luật nối khối "Hỏi dữ liệu bằng lời": nối sai ô "câu hỏi" ⇒ CHẶN lưu; thiếu khối kiểm ⇒ chỉ cảnh báo
    loi_sql, cb_sql = luat_hoi_du_lieu.kiem(payload["nodes"], payload["edges"])
    if loi_sql:
        ghi(f"LƯU bị chặn — luật hỏi dữ liệu: {len(loi_sql)} lỗi ('{hinh.get('name')}')")
        return 422, {"loi": "Workflow hỏi dữ liệu chưa đúng luật nối khối — chưa lưu. " + " ".join(loi_sql),
                     "kiem": loi + [{"muc": "loi", "code": "luat_hoi_du_lieu", "node": None, "field": None, "message": x, "suggestion": ""} for x in loi_sql]}
    loi += [{"muc": "canh_bao", "code": "luat_hoi_du_lieu", "node": None, "field": None, "message": x, "suggestion": ""} for x in cb_sql]
    # [ghi chú kỹ thuật] luật NỐI FILE của bộ "kiểm sau ghép": lúc Lưu chỉ CẢNH BÁO (không chặn)
    loi += [{"muc": "canh_bao", "code": "luat_noi_file", "node": None, "field": None, "message": x, "suggestion": ""} for x in kiem_luc_chay.canh_bao_noi_file(payload, chat_operator.KSG)]
    code, r = goi_ab("POST", f"/api/v1/orgs/{org()}/workflows", payload)
    ghi(f"LƯU {'mới' if moi else 'phiên bản mới'} {wf} '{hinh.get('name')}' ({len(hinh['nodes'])} khối, {len(hinh['edges'])} dây): HTTP {code}")
    if code >= 300:
        return 502, {"loi": f"Capitol từ chối lưu (HTTP {code}).", "chi_tiet": r}
    v = pb_moi_nhat(wf)
    return 200, {"workflow_id": wf, "version": v and v["so"], "kiem": loi, "loi_dan_dien_lai": dien_lai}


def _kiem_kho_rag(payload: dict) -> list[str]:
    """Mọi Agent bật tool_rag_search: ô rag_collections không rỗng và MỌI tên kho phải có trong sổ kho của tổ chức hiện tại, chưa ẩn.
    Tên kho do người dựng gõ là DỮ LIỆU ⇒ chỉ đếm, không chép tên vào câu báo."""
    loi = []
    for n in payload.get("nodes") or []:
        st = (n.get("data") or {}).get("struct") or {}
        if st.get("node_id") != "agent_node":
            continue
        o = {q.get("field_id"): q.get("value") for q in st.get("params") or []}
        if not (o.get("tool_rag_search") is True or str(o.get("tool_rag_search")).strip().lower() in ("true", "1", "yes", "on")):
            continue
        cols = [x.strip() for x in str(o.get("rag_collections") or "").splitlines() if x.strip()]
        dat, sai = SO_KHO.kho_hop_le(org(), cols)
        if not dat:
            loi.append(f"khối {str(n.get('id'))[:8]}: {len(sai) if cols else 0} / {len(cols)} kho không thuộc tổ chức" + ("" if cols else " (chưa chọn kho nào)"))
    return loi


def dua_file_vao_kho(ten: str, than: bytes) -> tuple[int, dict]:
    """[ghi chú kỹ thuật] Đưa byte một file vào KHO FILE CỦA CAPITOL (đúng đường của nút "Chọn file" khối Nhận file) ⇒ (200, phong bì file) | (mã lỗi, {"loi"}).
    Dùng cho khung chat "tính trên bảng": file người dùng đã kéo vào khung chat. Không ghi tên file vào nhật ký."""
    ten = str(ten or "").replace("\\", "/").split("/")[-1].strip()
    duoi = ("." + ten.rsplit(".", 1)[-1].lower()) if "." in ten else ""
    if not ten or len(ten) > 200 or any(c in ten for c in '"\r\n\x00') or duoi not in _DUOI_NHAN_FILE or not isinstance(than, (bytes, bytearray)) or not 1 <= len(than) <= 20 * 1024 * 1024:
        return 422, {"loi": "tên / đuôi / cỡ file không được nhận"}
    ranh = "----chaythu" + uuid.uuid4().hex
    goi = (f'--{ranh}\r\nContent-Disposition: form-data; name="file"; filename="{ten}"\r\n'
           f"Content-Type: application/octet-stream\r\n\r\n").encode() + bytes(than) + f"\r\n--{ranh}--\r\n".encode()
    req = urllib.request.Request(f"{API}/files/{org()}/upload", data=goi, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={ranh}", **_ma_18300(Path(__file__).resolve().parent.parent)})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            ma, d = r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        ma, d = e.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 502, {"loi": "không gọi được kho file"}
    ghi(f"TẢI FILE (khung chat tính trên bảng) đuôi={duoi} cỡ={len(than)} ⇒ HTTP {ma}")
    if ma >= 300 or not isinstance(d, dict) or not d.get("file"):
        return (ma if ma >= 300 else 502), {"loi": f"kho file không nhận file (mã {ma})"}
    return 200, d


def chay(wf: str, text_inputs: dict | None = None, khach: str | None = None, data_version=None, chay_lai: dict | None = None, file_inputs: dict | None = None) -> tuple[int, dict]:
    """Chạy phiên bản đã lưu mới nhất. `text_inputs` = {mã khối ô nhập chữ: chữ thay} ⇒ chạy với đầu vào khác MÀ KHÔNG lưu phiên bản
    (bộ ca thử; ô sửa đầu vào trên trang). Capitol vẫn tự tạo phiên bản loại RUN cho lượt đó — đúng như mọi lần bấm chạy."""
    kiem_ma(wf)
    try:
        dv = ban_du_lieu.doc_ban(data_version)   # [ghi chú kỹ thuật] kiểm dạng TRƯỚC mọi việc (ca V8 nhóm kỹ thuật)
    except ValueError as e:
        return 400, {"loi": str(e)}
    bay_gio = time.time()
    while _luot_gan_day and bay_gio - _luot_gan_day[0] > TRAN_GIAY:
        _luot_gan_day.popleft()
    if len(_luot_gan_day) >= TRAN_LUOT:
        return 429, {"loi": f"Đã chạy {TRAN_LUOT} lượt trong {TRAN_GIAY // 60} phút — chờ một lúc (trần chống tốn tiền, sửa ở may_chu.py)."}
    v = pb_moi_nhat(wf)
    if not v:
        return 404, {"loi": "Không thấy phiên bản đã lưu của workflow này."}
    if file_inputs:   # [ghi chú kỹ thuật] kiểm TRƯỚC mọi việc: phong bì file phải của đúng tổ chức; không đi cùng chạy lại / đổi bản dữ liệu
        ly_f = o_nhap.kiem_file(file_inputs, org())
        if ly_f or chay_lai is not None or data_version is not None:
            ghi(f"CHẠY bị chặn — file đưa vào lượt: {ly_f or 'không đi cùng chạy lại / bản dữ liệu'} (workflow {wf})")
            return 400, {"loi": ly_f or "File đưa vào lượt không đi cùng chạy lại từ khối / đổi bản dữ liệu."}
    if chay_lai is not None:   # [ghi chú kỹ thuật] chạy lại từ khối: chỉ nhận kết quả CL.chuan_bi (đã kiểm khoá trên ĐÚNG phiên bản này)
        if text_inputs or khach is not None or data_version is not None:
            return 400, {"loi": "Chạy lại từ khối không nhận đổi đầu vào / mã khách / bản dữ liệu (khoá đã kiểm trên bản đã lưu)."}
        if v["so"] != chay_lai.get("phien_ban"):
            return 409, {"loi": "Workflow vừa có phiên bản mới sau lúc xem trước — xem trước lại rồi mới chạy lại."}
    # [ghi chú kỹ thuật] kiểm HÌNH cả lúc Chạy (Lưu đã chặn; workflow do trợ lý dựng / nhập R2 / lưu trước khi luật đổi không qua Lưu của trang)
    loi_hinh = kiem_luc_chay.loi_hinh_luc_chay(v["payload"], DM, text_inputs)
    if loi_hinh is None:
        ghi(f"CHẠY: không chuyển được workflow {wf} sang hình của trang để kiểm hình — KHÔNG chặn vì lý do này")
    elif loi_hinh:
        ghi(f"CHẠY bị chặn — kiểm hình: {len(loi_hinh)} lỗi (workflow {wf})")
        return 422, {"loi": kiem_luc_chay.cau_bao_hinh(loi_hinh), "luat_hinh": loi_hinh[:50]}
    loi_kpi = kiem_kpi_payload(v["payload"], luc_chay=True)   # [ghi chú kỹ thuật] kiểm lại lúc CHẠY (+ D: so dòng ngữ cảnh lời dặn với hộp KPI đang bật): workflow do chat / API tạo không qua Lưu của trang
    if loi_kpi:
        ghi(f"CHẠY bị chặn — luật KPI: {len(loi_kpi)} lỗi (workflow {wf})")
        return 422, {"loi": "Workflow KPI chưa đúng luật an toàn — không chạy. " + " ".join(loi_kpi), "luat_kpi": loi_kpi}
    if dv:
        # [ghi chú kỹ thuật] SAU luật KPI (luật so ô khoá với khối dựng sẵn trên bản ĐÃ LƯU — ô kpi_data_version phải trống ở đó;
        # hệ thống điền là đường duy nhất, params.py:91) ⇒ điền bản dữ liệu vào BẢN SAO lượt này (bản đã lưu không đổi) — mọi đường từ chối ở đây trả về TRƯỚC giữ chỗ / gọi AI
        H0 = _hoi_kpi()
        import khoi_dung_san as _KD
        try:
            meta0 = khoi_kpi.moi_truong(H0, org())["meta"]
        except Exception:  # noqa: BLE001
            meta0 = None
        code_dv, r_dv = ban_du_lieu.dien(v["payload"], dv, la_kpi=_KD.la_workflow_kpi, meta=meta0, hop=H0.chay_m3.HOP)
        if code_dv != 200:
            ghi(f"CHẠY bị chặn — bản dữ liệu {dv[:12]}: HTTP {code_dv} (workflow {wf})")
            return code_dv, r_dv
        v = dict(v, payload=r_dv["payload"])
        ghi(f"CHẠY {wf} trên bản dữ liệu {dv[:12]} — điền {r_dv['so_khoi']} khối (chỉ lượt này)")
    loi_tang = luat_tang.loi_tang(v["payload"])   # [ghi chú kỹ thuật] kiểm lại lúc CHẠY: workflow do chat / script / API tạo không qua Lưu của trang
    if loi_tang:
        ghi(f"CHẠY bị chặn — luật tầng hộp phiên: {len(loi_tang)} lỗi (workflow {wf})")
        return 422, {"loi": "Workflow có nhiều khối hộp phiên chạy cùng lúc — không chạy (người chạy phiên chỉ mở 1 hộp). " + " ".join(loi_tang),
                     "luat_tang": loi_tang}
    loi_kho = _kiem_kho_rag(v["payload"])   # [ghi chú kỹ thuật] lớp thứ hai (lớp thật ở hộp cổng, E major 1)
    if loi_kho:
        ghi(f"CHẠY bị chặn — kho tài liệu: {len(loi_kho)} lỗi (workflow {wf})")
        return 422, {"loi": "Khối tìm trong tài liệu dùng kho không thuộc tổ chức này (hoặc đã ẩn) — không chạy. " + " ".join(loi_kho), "luat_kho": loi_kho}
    loi_sql, _ = luat_hoi_du_lieu.kiem(v["payload"].get("nodes") or [], v["payload"].get("edges") or [])   # [ghi chú kỹ thuật] chỉ phần CHẶN, kiểm lại lúc chạy
    if loi_sql:
        ghi(f"CHẠY bị chặn — luật hỏi dữ liệu: {len(loi_sql)} lỗi (workflow {wf})")
        return 422, {"loi": "Workflow hỏi dữ liệu chưa đúng luật nối khối — không chạy. " + " ".join(loi_sql), "luat_hoi_du_lieu": loi_sql}
    loi_kb = kho_bang.kiem(v["payload"], org())   # [ghi chú kỹ thuật] kiểm lại lúc CHẠY, TRƯỚC giữ chỗ: workflow do chat / API tạo không qua Lưu
    if loi_kb:
        ghi(f"CHẠY bị chặn — dataset ngoài tổ chức / sai: {len(loi_kb)} lỗi (workflow {wf})")
        return 422, {"loi": "Khối hỏi kho bảng có dataset không dùng được — không chạy. " + " ".join(x["message"] for x in loi_kb), "kho_bang": loi_kb}
    # [ghi chú kỹ thuật] mọi lượt bấm Chạy đi qua sổ tiền ngày như /hoi-kpi (trước đây trang không đọc / ghi sổ).
    # Không phân biệt lượt "có AI" (nhiều loại khối gọi AI, không chỉ Agent) ⇒ áp cho MỌI lượt: an toàn hơn, lượt 0 đồng không ghi gì.
    if not (os.environ.get("BUOI_S5") and os.environ.get("TRAN_USD")):
        return 503, {"loi": "Máy chủ trang chưa đặt sổ tiền ngày (BUOI_S5 + TRAN_USD) — không chạy lượt có thể tốn tiền. Báo người bật máy chủ."}
    H = _hoi_kpi()
    # [ghi chú kỹ thuật] giữ chỗ theo MỨC TỐI ĐA của chính workflow này (trước: 0,01 cố định ⇒ 10/11 vượt trần).
    # Rủi ro còn lại: lỗi mạng / 429 sau khi đã tiêu ⇒ khối tự thử lại, tiền thật có thể vượt mức giữ chỗ (muc_toi_da.py đầu file).
    can, thieu = muc_toi_da.toi_da(v["payload"] if chay_lai is None else CL.con_payload(v["payload"], chay_lai["chay_moi"]))   # [ghi chú kỹ thuật] chạy lại: chỉ khối chạy mới
    if thieu:
        return 402, {"loi": f"Workflow có khối gọi AI chưa có mức tiền tối đa: {thieu[:5]} — không chạy (luật giữ chỗ theo mức tối đa)."}
    qua_co = o_nhap.kiem_co(text_inputs, tinh_tran=can > 0)   # [ghi chú kỹ thuật] vượt chốt ⇒ TỪ CHỐI có lời báo (không cắt, không tạo phiên, không giữ chỗ)
    if qua_co:
        ghi(f"CHẠY bị chặn — ô nhập vượt chốt cỡ (workflow {wf}; mức tối đa {can:.4f})")
        return 413, {"loi": qua_co, "qua_co": True}
    # Giữ chỗ THẬT (đối chiếu kỹ thuật): làm ngay trước run-workflow — các bước trên / dưới tới đó không tốn tiền.
    code, ses = goi_ab("GET", "/session/create-session")
    s = ses.get("session_id") if isinstance(ses, dict) else None
    if not s:
        return 502, {"loi": f"Không tạo được phiên (HTTP {code})."}
    payload = dict(v["payload"], session_id=s)
    try:
        payload = o_nhap.thay_file(payload, file_inputs)   # [ghi chú kỹ thuật] file của lượt này vào BẢN SAO (bản đã lưu không đổi)
    except ValueError as e:
        ghi(f"CHẠY bị chặn — {e} (workflow {wf})")
        return 400, {"loi": "File đưa vào lượt không khớp workflow."}
    payload = o_nhap.thay(payload, text_inputs)   # [ghi chú kỹ thuật] NGUYÊN VĂN — bản cũ cắt lặng ở 20.000 ký tự làm mất câu hỏi sau tài liệu (phiên fa71e107)
    if khach is not None:
        # hướng D (chiều 04/11): "hệ thống" (ở đây: trang / runner) điền mã khách vào MỌI khối Agent cho lượt này — AI không thấy ô này
        if not re.fullmatch(r"[A-Za-z0-9-]{1,40}", str(khach)):
            return 400, {"loi": "mã khách không hợp lệ"}
        import copy as _copy
        payload = _copy.deepcopy(payload)
        thieu = []
        for n in payload.get("nodes", []):
            st = (n.get("data") or {}).get("struct") or {}
            if st.get("node_id") == "agent_node":
                o = next((p_ for p_ in st.get("params", []) if p_.get("field_id") == "tool_customer_id"), None)
                if o is None:
                    thieu.append(st.get("name") or n.get("id"))
                else:
                    o["value"] = str(khach)
        if thieu:
            # đã kiểm (mục 20, D-1 major): workflow lưu TRƯỚC khi danh mục có ô này ⇒ không điền được ⇒ tra đơn im lặng KHÔNG kiểm chủ đơn
            # ⇒ dừng hẳn, không chạy (an toàn hơn chạy thiếu): làm mới danh mục (/api/blocks/refresh) rồi lưu lại workflow.
            return 409, {"loi": f"Khối Agent chưa có ô 'Mã khách' (workflow lưu trước khi vá hướng D): {thieu[:5]} — làm mới danh mục rồi lưu lại workflow."}
    p_tien = payload if chay_lai is None else CL.con_payload(payload, chay_lai["chay_moi"])   # [ghi chú kỹ thuật] khối dùng lại không tốn tiền ⇒ không tính khối hộp phiên của chúng
    # kiểm + giữ trong CÙNG khoá (bảng "đang giữ" chung với trang Hỏi KPI, hoi_kpi.giu_cho) ⇒ bấm liền nhiều lượt không lọt trần
    ma_giu, tong = H.giu_cho(max(can, H.TRAN_LUOT), session=s)   # [ghi chú kỹ thuật] dòng giữ mang mã phiên
    if ma_giu is None:
        if H.giu_cho_ly_do() == "so_ban":   # [ghi chú kỹ thuật] không lấy được khoá sổ ≠ hết trần — chỉ đổi CHỮ báo, vẫn không chạy
            return 503, {"loi": "Sổ tiền AI đang bận ghi (một lượt khác vừa chạy xong) — chưa chạy gì, bấm lại sau vài giây."}
        return 402, {"loi": f"Sắp vượt trần tiền AI hôm nay: đã tiêu + đang giữ {tong:.4f} + giữ chỗ tối đa {can:.4f} > trần {H.TRAN} USD — không chạy. Nâng trần phải hỏi chủ khoá."}
    try:
        # [ghi chú kỹ thuật] 403 VÀ Postgres chưa có lượt của phiên ⇒ thử lại 1 lần (khoản giữ chỗ ma_giu giữ nguyên);
        # 403 mà Postgres ĐÃ có lượt ⇒ coi như lượt đã khởi động: đi đường thành công bên dưới (theo dõi ghi sổ, KHÔNG trả chỗ)
        code, r, da_co_luot = thu_lai_403.goi_run_workflow(
            lambda: (goi_ab("POST", f"/workflow/{org()}/{wf}/{s}/run-workflow", payload) if chay_lai is None
                     else goi_ab("POST", f"/workflow/{org()}/{wf}/rerun-from-node", CL.than_capitol(chay_lai, payload))), ghi=ghi, nhan=wf,
            co_luot=lambda: bool(xem.sql(f"select row_to_json(x) from (select 1 as co from workflow_run where session_id='{kiem_ma(s)}' limit 1) x")))
        if code == 403 and da_co_luot:
            code = 202
    except BaseException:
        # [ghi chú kỹ thuật] hết giờ chờ mà Capitol CÓ THỂ đã chạy ⇒ không bỏ chỗ; luồng theo dõi ghi sổ (nếu có lượt) rồi mới bỏ
        if so_skill.khoi_hop_phien(p_tien):
            so_skill.theo_doi(s, p_tien, H=H, muc_khoi=muc_toi_da._muc_khoi, nhan="trang_18400", ma_giu=ma_giu, ghi=ghi,
                              ghi_chu=f"bấm Chạy trên trang 18400, workflow {wf} (run-workflow lỗi / hết giờ)",
                              trang_thai=lambda p: (xem.sql("select row_to_json(x) from (select status::text as tt from workflow_run "
                                                            f"where session_id='{kiem_ma(p)}' order by created_at desc limit 1) x") or {}).get("tt"))
        else:
            if chay_lai is None:
                H.theo_doi_ghi_tien(s, "trang_18400", f"bấm Chạy trên trang 18400, workflow {wf} (run-workflow lỗi / hết giờ)", ma_giu=ma_giu)
            else:   # [ghi chú kỹ thuật] chạy lại: tiền 0 ⇒ vẫn 1 dòng sổ 0 đồng
                CL.theo_doi_ghi_tien(s, H, f"chạy lại từ khối trên trang 18400, workflow {wf} (lỗi / hết giờ)", ma_giu, trang_thai=lambda p: (xem.sql("select row_to_json(x) from (select status::text as tt from workflow_run "
                                                            f"where session_id='{kiem_ma(p)}' order by created_at desc limit 1) x") or {}).get("tt"))
        raise
    _luot_gan_day.append(bay_gio)
    ghi(f"CHẠY {wf} phiên bản {v['so']} ⇒ phiên {s}: HTTP {code}" + ("" if chay_lai is None else
        f" — CHẠY LẠI TỪ KHỐI của phiên {chay_lai['phien_nguon']}: chạy mới {len(chay_lai['chay_moi'])}, dùng lại {len(chay_lai['dung_lai'])}, lệch khoá {len(chay_lai['lech'])}"))
    if code >= 300:
        H.tra_cho(ma_giu)   # Capitol từ chối ⇒ chưa có lượt chạy
        return 502, {"loi": f"Capitol từ chối chạy (HTTP {code}).", "chi_tiet": r}
    if so_skill.khoi_hop_phien(p_tien):
        # [ghi chú kỹ thuật] khối PowerPoint / Word / Excel-chat / chạy code không để lại message_history ⇒ tien_luot không thấy tiền của chúng
        # ⇒ một luồng: ghi tiền chính ⇒ ghi thêm dòng MỨC TỐI ĐA của các khối đó (cùng muc_toi_da._muc_khoi với lúc giữ chỗ) ⇒ rồi mới trả chỗ.
        so_skill.theo_doi(s, p_tien, H=H, muc_khoi=muc_toi_da._muc_khoi, nhan="trang_18400", ghi_chu=f"bấm Chạy trên trang 18400, workflow {wf}", ma_giu=ma_giu, ghi=ghi,
                          trang_thai=lambda p: (xem.sql("select row_to_json(x) from (select status::text as tt from workflow_run "
                                                        f"where session_id='{kiem_ma(p)}' order by created_at desc limit 1) x") or {}).get("tt"))
    elif chay_lai is None:
        H.theo_doi_ghi_tien(s, "trang_18400", f"bấm Chạy trên trang 18400, workflow {wf}", ma_giu=ma_giu)   # ghi sổ 1 lần rồi mới bỏ chỗ
    else:   # [ghi chú kỹ thuật] lượt chạy lại tiền 0 (mọi khối AI dùng lại) vẫn có 1 dòng sổ 0 đồng nhãn trang_18400_chay_lai
        CL.theo_doi_ghi_tien(s, H, f"chạy lại từ khối trên trang 18400, workflow {wf} (nguồn {chay_lai['phien_nguon'][:8]})", ma_giu, trang_thai=lambda p: (xem.sql("select row_to_json(x) from (select status::text as tt from workflow_run "
                                                            f"where session_id='{kiem_ma(p)}' order by created_at desc limit 1) x") or {}).get("tt"))
    return 200, dict({"session": s, "version": v["so"]}, **({} if chay_lai is None else CL.ket_qua_chay(chay_lai)))


QUYET_DINH = ("dong_y", "tu_choi")


def duyet(s: str, node: str, quyet_dinh) -> tuple[int, dict]:
    if quyet_dinh not in QUYET_DINH:   # kiểm TRƯỚC mọi việc khác: không đoán ý người duyệt
        return 400, {"loi": f"ô decision phải là một trong {list(QUYET_DINH)} — nhận {quyet_dinh!r}"}
    kiem_ma(s)
    kiem_ma(node)
    r = xem.sql(f"""select row_to_json(x) from (select r.workflow_id as wf,
          (select e.event_data->'data'->>'request_id' from workflow_event e where e.run_id=r.id and e.node_instance_id='{node}'
             and e.event_type='awaiting_intervention' order by e.timestamp desc limit 1) as rid
        from workflow_run r where r.session_id='{s}' order by r.created_at desc limit 1) x""")
    if not r or not r.get("rid"):
        return 409, {"loi": "Khối này không đang chờ người duyệt (chưa có sự kiện chờ duyệt)."}
    duong = "resume" if quyet_dinh == "dong_y" else "stop"
    code, ph = goi_ab("POST", f"/human-intervention/{r['wf']}/{s}/{node}/{r['rid']}/{duong}", {})
    ghi(f"{'DUYỆT' if duong == 'resume' else 'TỪ CHỐI'} phiên {s} khối {node}: HTTP {code} — {str(ph)[:160]}")
    return (200 if code < 300 else 502), {"http": code, "phan_hoi": ph}


# ------------------------------------------------------------ tiền + tốc độ
def mo_hinh_khoi_that() -> dict:
    """[ghi chú kỹ thuật] Mô hình + mức suy luận ĐANG ĐẶT cho khối kiểu Agent — đọc file mô hình khối MỖI lần (muc_toi_da._doc_ep_mo_hinh, cùng hàm với
    phần tính mức tiền và cùng luật đọc với bản vá trong hộp). Tên trả về là tên GỬI TRÊN DÂY (vd "claude-sonnet-5-5-medium"). Không có dòng nào ⇒ mac_dinh "" =
    khối chạy theo ô model của nó. File sai khuôn ⇒ loi (lúc đó khối AI cũng bị chặn chạy)."""
    khoi = list(dict.fromkeys(list(muc_toi_da.KHOI_EP_MO_HINH) + list(getattr(muc_toi_da, 'KHOI_HOP_PHIEN', ())) + list(getattr(muc_toi_da, 'KHOI_NGUOI_GAC', ()))))   # [ghi chú kỹ thuật] + họ hộp phiên (+ họ người gác khi gói nhóm kỹ thuật gắn — chưa có thì bỏ qua)
    try:
        mac_dinh, bang = muc_toi_da._doc_ep_mo_hinh()
        return {"mac_dinh": mac_dinh, "theo_khoi": bang, "khoi_doi_duoc": khoi, "loi": None}
    except Exception:  # noqa: BLE001
        return {"mac_dinh": "", "theo_khoi": {}, "khoi_doi_duoc": khoi, "loi": "File mô hình khối đang sai khuôn — khối AI bị chặn chạy tới khi sửa file."}


def gia_mot_lan(mo_hinh: str, vao: int, ra: int, cache: int) -> dict:
    g = GIA.get(mo_hinh)
    if not g:
        # [ghi chú kỹ thuật] Claude qua Bedrock: giá THAM KHẢO từ bảng cổng AI (giá vào / ra lớn nhất của tên đó). Không tính trần — cờ mien_tran để trang ghi rõ.
        gb = muc_toi_da.GIA_BANG.get(mo_hinh or "")
        if gb and muc_toi_da.NCC_BANG.get(mo_hinh) == muc_toi_da.NCC_BEDROCK:
            return {"cost_usd": (vao * gb[0] + ra * gb[1]) / 1_000_000, "mien_tran": True, "price_source": "bảng cổng AI (chay_thu/cong_ai/dinh_tuyen.yaml)",
                    "price_read_at": "lúc bật máy chủ trang",
                    "price_note": "Claude qua Amazon Bedrock — giá tham khảo, KHÔNG tính vào trần tiền API; token đọc đệm tính như token vào thường nên số này dư"}
        return {"cost_usd": None, "price_source": None, "price_note": "chưa có giá"}
    gc = g.get("cache_read")
    tien = ((vao - cache) * g["input"] + cache * (gc if gc is not None else g["input"]) + ra * g["output"]) / 1_000_000
    return {"cost_usd": tien, "price_source": g["source"], "price_read_at": g["read_at"],
            "price_note": "" if gc is not None or not cache else "chưa biết giá đọc đệm — tính như token vào thường"}


_log_cache: dict = {}


def log_tom_tat(s: str) -> dict:
    """Đọc log hộp worker Temporal từ lúc lượt bắt đầu: dòng [DIGEST_USAGE] (vá 7) + 'digest cache hit'. Lượt đã xong ⇒ nhớ kết quả."""
    if s in _log_cache:
        return _log_cache[s]
    luu = KQ / "giao_dien" / f"tom_tat_{s}.json"          # đã kiểm 04/11: nhớ ra file để khởi động lại máy chủ / tạo lại hộp không mất số
    if luu.is_file():
        x = json.loads(luu.read_text())
        _log_cache[s] = {"usage": x["usage"], "cache_hit": set(x["cache_hit"]), "fail": x.get("fail", [])}
        return _log_cache[s]
    r = xem.sql(f"select row_to_json(x) from (select created_at as tao, status::text as tt from workflow_run where session_id='{s}' order by created_at desc limit 1) x")
    ra = {"usage": [], "cache_hit": set(), "fail": []}
    if not r:
        return ra
    tu = datetime.fromisoformat(str(r["tao"]).replace(" ", "T")).timestamp() - 10
    out = subprocess.run(["docker", "logs", "--since", str(int(tu)), "capitol-ab-worker-temporal"], capture_output=True, text=True, timeout=30)
    for dong in (out.stdout + out.stderr).splitlines():
        if "sk-" in dong:
            continue
        if "[DIGEST_USAGE]" in dong and f"session={s} " in dong:
            kv = dict(x.split("=", 1) for x in dong.split("[DIGEST_USAGE]", 1)[1].split() if "=" in x)
            kv["at"] = dong[:19]
            ra["usage"].append(kv)
        elif "digest cache hit for session" in dong and s in dong:
            ra["cache_hit"].add(dong.rsplit("node ", 1)[-1].strip())
        elif "HITL review digest attempt" in dong and f"session {s} " in dong:   # lần thử hỏng: có thể đã tốn token, không đo được
            ra["fail"].append(dong.split(" node ", 1)[-1].split(":")[0].strip())
    if r["tt"] not in ("RUNNING", "QUEUED", "PENDING", "PAUSED"):
        _log_cache[s] = ra
        if ra["usage"] or ra["cache_hit"] or ra["fail"]:
            luu.parent.mkdir(parents=True, exist_ok=True)
            luu.write_text(json.dumps({"usage": ra["usage"], "cache_hit": sorted(ra["cache_hit"]), "fail": ra["fail"]}, ensure_ascii=False))
    return ra


def lam_moi_danh_muc() -> int:
    """Lấy danh mục khối TRỰC TIẾP từ agentic-backend (có các ô của vá 8) ⇒ runtime/giao_dien/danh_muc.json; nạp lại DM."""
    global DM
    code, d = goi_ab("POST", f"/nodes/{org()}/list-nodes", {}, timeout=120)
    if code != 200 or not isinstance(d, dict):
        raise RuntimeError(f"không lấy được danh mục (HTTP {code})")
    f = KQ / "giao_dien" / "danh_muc.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, ensure_ascii=False))
    import capitol_chuyen_doi as cd
    cd.DANH_MUC_FILE = f
    DM = cd.DanhMuc(f)
    ghi(f"LÀM MỚI DANH MỤC: {len(DM.struct)} khối dùng được, ẩn {len(DM.an)} khối công cụ ngoài")
    return len(DM.struct)


def doi_chieu_luot(s: str) -> dict:
    """Cải tiến (1): mỗi khối AI của lượt — mọi số / ngày / link / mã trong đầu ra phải có trong nguồn của CHÍNH khối đó
    (kết quả công cụ > đầu vào khối nhận > lời dặn). Đầu ra có khuôn ⇒ soát mọi giá trị chữ / số trong JSON + link khai ở trường nguon."""
    kiem_ma(s)
    hang = xem.sql(f"""select coalesce(json_agg(json_build_object('node', n.node_instance_id, 'value', nr.output_data->>'value',
            'mh', nr.output_data->'metadata'->'message_history')), '[]')
        from node_result nr join node n on n.id=nr.node_id join workflow_run r on r.id=nr.run_id
        where r.session_id='{s}' and nr.output_data->'metadata' ? 'message_history'""")
    ra = {}
    for h in hang:
        cc, tinh, dv, ld = [], [], "", ""
        for m in h.get("mh") or []:
            for p in m.get("parts", []):
                c = p.get("content")
                c = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                if p.get("part_kind") == "tool-return" and p.get("tool_name") != "final_result":
                    cc.append(c)
                    if p.get("tool_name") not in ("web_search", "fetch_url"):
                        tinh.append(c)              # số để ghép phép tính: chỉ từ công cụ dữ liệu nội bộ
                elif p.get("part_kind") == "user-prompt" and not dv:
                    dv = c
                elif p.get("part_kind") == "system-prompt" and not ld:
                    ld = c
        nguon = {"cong_cu": nguon_cong_cu(cc), "dau_vao": dv, "loi_dan": ld, "tinh": nguon_cong_cu(tinh)}   # bỏ query + ok:false (đối chiếu kỹ thuật)
        val = h.get("value") or ""
        chu = val
        try:                                          # có khuôn ⇒ soát các giá trị lá (chữ, số), bỏ khoá JSON và true / false
            d = json.loads(val)
            if isinstance(d, (dict, list)):
                la = []
                def di(x):
                    if isinstance(x, dict):
                        for v in x.values():
                            di(v)
                    elif isinstance(x, list):
                        for v in x:
                            di(v)
                    elif isinstance(x, (str, int, float)) and not isinstance(x, bool):
                        la.append(str(x))
                di(d)
                chu = "\n".join(la)
        except ValueError:
            pass
        r = doi_chieu(chu, nguon)
        r["khuon"] = doi_chieu_khuon(val, nguon)
        r["co_cong_cu"] = bool(cc)
        ra[h["node"]] = r
    return ra


_dau_vao: dict = {}


def cong_cu_da_goi(s: str) -> list:
    """Agent đã gọi công cụ nào (từ message_history): tên, tham số, kết quả rút gọn 400 ký tự. Dữ liệu của mình, không có khoá."""
    kiem_ma(s)
    ds = xem.sql(f"""select coalesce(json_agg(json_build_object('node', n.node_instance_id, 'kind', m->>'kind', 'at', m->>'timestamp',
            'parts', (select coalesce(json_agg(json_build_object('k', pp->>'part_kind', 'tool', pp->>'tool_name', 'id', pp->>'tool_call_id',
                      'args', left(coalesce(pp->>'args', ''), 400),
                      'content', left(coalesce(pp->>'content', ''), case when pp->>'part_kind'='user-prompt' then 3000 else 400 end), 'at', pp->>'timestamp')), '[]')
                      from jsonb_array_elements(m->'parts') pp where pp->>'part_kind' in ('tool-call', 'tool-return', 'retry-prompt', 'user-prompt'))
        ) order by m->>'timestamp'), '[]')
        from node_result nr join node n on n.id=nr.node_id join workflow_run r on r.id=nr.run_id,
             jsonb_array_elements(coalesce(nr.output_data::jsonb->'metadata'->'message_history', '[]'::jsonb)) m
        where r.session_id='{s}'""")
    goi, tra = {}, {}
    _dau_vao.pop(s, None)
    for msg in ds:
        for p in msg["parts"]:
            if p["k"] == "tool-call":
                # khoá (khối, mã): bước 23 (04/11) 3 agent song song có thể trùng tool_call_id ⇒ khoá chỉ theo mã thì agent sau đè agent trước
                goi[(msg["node"], p["id"])] = {"node": msg["node"], "tool": p["tool"], "args": p["args"], "at": msg["at"]}
            elif p["k"] == "user-prompt" and msg["node"] not in _dau_vao.setdefault(s, {}):
                _dau_vao[s][msg["node"]] = p["content"]      # câu nhờ ĐẦU TIÊN = cái AI thật sự nhận (chữ gõ sẵn + phần nối qua dây)
            elif p["k"] in ("tool-return", "retry-prompt"):
                tra[(msg["node"], p["id"])] = {"content": p["content"], "at": p["at"], "retry": p["k"] == "retry-prompt"}
    ra = []
    for i, g in goi.items():
        r = tra.get(i, {})
        ra.append({**g, "result": r.get("content"), "returned_at": r.get("at"), "retry": r.get("retry", False),
                   "internal": g["tool"] in ("report_input_issue", "final_result")})
    return ra


def chi_phi(s: str) -> dict:
    """Mỗi lần gọi AI theo khối, lấy từ node_result → metadata → message_history (kind=response) — chỉ lấy số, KHÔNG lấy nguyên văn."""
    kiem_ma(s)
    ds = xem.sql(f"""select coalesce(json_agg(json_build_object('node', n.node_instance_id, 'model', m->>'model_name', 'at', m->>'timestamp',
            'in', (m->'usage'->>'input_tokens')::int, 'out', (m->'usage'->>'output_tokens')::int,
            'cache', coalesce((m->'usage'->>'cache_read_tokens')::int, 0),
            'reasoning', coalesce((m->'usage'->'details'->>'reasoning_tokens')::int, 0),
            'tools', (select coalesce(json_agg(pp->>'tool_name'), '[]') from jsonb_array_elements(m->'parts') pp where pp->>'part_kind'='tool-call')
        ) order by m->>'timestamp'), '[]')
        from node_result nr join node n on n.id=nr.node_id join workflow_run r on r.id=nr.run_id,
             jsonb_array_elements(coalesce(nr.output_data::jsonb->'metadata'->'message_history', '[]'::jsonb)) m
        where r.session_id='{s}' and m->>'kind'='response'""")
    # đã kiểm 04/11 chiều (BAO_CAO_KET_QUA mục 19, N1 — major, tiền đếm thiếu): khối cùng tầng với Người duyệt CHẠY XONG + trả tiền, nhưng
    # Từ chối huỷ cả tầng ⇒ dòng node_result bị ghi SKIPPED, không có message_history ⇒ trước đây trang không cộng tiền khối đó.
    # Bản sao nằm ở sự kiện node_result (workflow_event.event_data.data.metadata.message_history) ⇒ lấy từ đó cho khối chưa có ở trên.
    co_roi = {x["node"] for x in ds}
    them = xem.sql(f"""select coalesce(json_agg(json_build_object('node', e.node_instance_id, 'model', m->>'model_name', 'at', m->>'timestamp',
            'in', (m->'usage'->>'input_tokens')::int, 'out', (m->'usage'->>'output_tokens')::int,
            'cache', coalesce((m->'usage'->>'cache_read_tokens')::int, 0),
            'reasoning', coalesce((m->'usage'->'details'->>'reasoning_tokens')::int, 0),
            'tools', (select coalesce(json_agg(pp->>'tool_name'), '[]') from jsonb_array_elements(m->'parts') pp where pp->>'part_kind'='tool-call'),
            'tu_su_kien', true) order by m->>'timestamp'), '[]')
        from workflow_event e join workflow_run r on r.id=e.run_id,
             jsonb_array_elements(coalesce(e.event_data::jsonb->'data'->'metadata'->'message_history', '[]'::jsonb)) m
        where r.session_id='{s}' and e.event_type='node_result' and m->>'kind'='response'""") or []
    ds = ds + [x for x in them if x["node"] not in co_roi]
    lan = []
    for x in ds:
        ngam = "report_input_issue" in (x.get("tools") or [])
        g = gia_mot_lan(x["model"], x["in"] or 0, x["out"] or 0, x["cache"] or 0)
        lan.append({"node": x["node"], "run_session": s, "model": x["model"], "at": x["at"], "tokens_in": x["in"], "tokens_out": x["out"],
                    "tokens_cached": x["cache"], "tokens_reasoning": x["reasoning"], "call_kind": "side" if ngam else "main",
                    "why": ("AI tự gọi công cụ báo đầu vào (report_input_issue, luôn bật)" if ngam else "")
                           + (" — khối bị ghi SKIPPED nhưng đã chạy xong; số lấy từ sự kiện node_result" if x.get("tu_su_kien") else ""),
                    "measurable": True, **g})
    # Khối người duyệt gọi AI viết bản tóm tắt (digest.py) — Capitol không lưu token trong DB (đối chiếu kỹ thuật) ⇒ vá 7 (04/11) ghi dòng log
    # [DIGEST_USAGE] ở hộp capitol-ab-worker-temporal; ở đây đọc lại dòng đó. Không có dòng ⇒ "không đo được" kèm lý do.
    cho = xem.sql(f"""select coalesce(json_agg(distinct e.node_instance_id), '[]') from workflow_event e join workflow_run r on r.id=e.run_id
                      where r.session_id='{s}' and e.event_type='awaiting_intervention'""")
    if cho:
        log = log_tom_tat(s)
        # [ghi chú kỹ thuật] Người duyệt CHẾ ĐỘ KPI dựng màn duyệt bằng code, KHÔNG gọi AI tóm tắt (human_intervention/node.py:1012-1014) ⇒ không tính lần gọi
        kpi_duyet = set(xem.sql(f"""select coalesce(json_agg(n->>'id'), '[]') from workflow_run r join workflow_version v on v.id=r.version_id,
                 jsonb_array_elements(v.payload::jsonb->'nodes') n, jsonb_array_elements(n->'data'->'struct'->'params') q
                 where r.session_id='{s}' and n->'data'->'struct'->>'node_id'='human_intervention_node' and q->>'field_id'='che_do_kpi'
                   and lower(q->>'value') in ('true', '1', 'yes', 'on')""") or [])
        for nd in [x for x in cho if x not in kpi_duyet]:
            dong = [x for x in log["usage"] if x.get("node") == nd]
            for x in dong:
                vao, ra_, ca = int(x.get("input", 0)), int(x.get("output", 0)), int(x.get("cache_read", 0))
                lan.append({"node": nd, "run_session": s, "model": x.get("model"), "at": x.get("at"), "tokens_in": vao, "tokens_out": ra_,
                            "tokens_cached": ca, "tokens_reasoning": int(x.get("reasoning", 0)), "call_kind": "side", "measurable": True,
                            "why": f"khối duyệt gọi AI viết bản tóm tắt cho người duyệt ({x.get('requests')} yêu cầu) — số từ log vá 7",
                            **gia_mot_lan(x.get("model"), vao, ra_, ca)})
            for _ in [f for f in log.get("fail", []) if f == nd]:
                lan.append({"node": nd, "model": None, "call_kind": "side", "measurable": False, "cost_usd": None,
                            "why": "một lần thử viết bản tóm tắt bị hỏng (sai định dạng / hết hạn) — có thể đã tốn token nhưng không đo được"})
            if not dong and nd in log["cache_hit"]:
                lan.append({"node": nd, "model": None, "call_kind": "side", "measurable": True, "cost_usd": 0, "tokens_in": 0, "tokens_out": 0,
                            "why": "dùng lại bản tóm tắt đã có (đệm Redis theo nội dung) — không gọi AI"})
            elif not dong:
                lan.append({"node": nd, "model": None, "call_kind": "side", "measurable": False, "cost_usd": None,
                            "why": "khối duyệt có gọi AI viết bản tóm tắt nhưng không tìm thấy dòng log [DIGEST_USAGE] — lượt chạy trước khi vá 7 (04/11), "
                                   "hoặc log hộp đã mất khi tạo lại hộp ⇒ không đo được"})
    # đã kiểm 04/11 chiều (mục 21, B2 major): khối Che thông tin GỌI AI (RedactionService gọi Agent.run thẳng — 1 lần / lượt theo log) nhưng không lưu
    # token ở đâu ⇒ trước đây trang hiện "0" (sai) ⇒ ghi "không đo được" để tổng tiền không bị hiểu là đủ.
    che = xem.sql(f"""select coalesce(json_agg(distinct e.node_instance_id), '[]') from workflow_event e join workflow_run r on r.id=e.run_id
                      join workflow_version wv on wv.id=r.version_id, jsonb_array_elements(wv.payload::jsonb->'nodes') n
                      where r.session_id='{s}' and e.event_type='execution_successful' and e.node_instance_id = n->>'id'
                        and n->'data'->'struct'->>'node_id' in ('text_redaction_node', 'pdf_redaction_node')""") or []
    for nd in che:
        lan.append({"node": nd, "model": "gpt-6-luna", "call_kind": "side", "measurable": False, "cost_usd": None,
                    "why": "khối Che thông tin gọi AI (vá 9: gpt-6-luna) nhưng không lưu token ⇒ KHÔNG đo được (không phải 0)"})
    # đã kiểm 04/11 (mục 22, D15): khối agent báo xong (execution_successful) nhưng KHÔNG có node_result — Capitol nuốt lỗi lưu (persist_results.py:687-694,
    # vd ký tự NUL từ trang web) ⇒ lượt vẫn SUCCESS, tiền khối đó mất ⇒ ghi "không lưu được" để người xem biết tổng tiền thiếu.
    mat = xem.sql(f"""select coalesce(json_agg(distinct e.node_instance_id), '[]') from workflow_event e join workflow_run r on r.id=e.run_id
                      join workflow_version wv on wv.id=r.version_id, jsonb_array_elements(wv.payload::jsonb->'nodes') n
                      where r.session_id='{s}' and e.event_type='execution_successful' and e.node_instance_id = n->>'id'
                        and n->'data'->'struct'->>'node_id' = 'agent_node'
                        and not exists (select 1 from workflow_event e2 where e2.run_id=e.run_id and e2.node_instance_id=e.node_instance_id
                                        and e2.event_type='node_result')""") or []
    for nd in mat:
        lan.append({"node": nd, "model": None, "call_kind": "main", "measurable": False, "cost_usd": None,
                    "why": "⚠ khối chạy xong nhưng Capitol KHÔNG lưu được kết quả (nuốt lỗi lưu — xem log worker 'persist_one_node_result failed') ⇒ tiền khối này KHÔNG đo được"})
    tong = {"main": sum(x["cost_usd"] or 0 for x in lan if x["call_kind"] == "main"),
            "side": sum(x["cost_usd"] or 0 for x in lan if x["call_kind"] == "side"),
            "tokens_in": sum(x.get("tokens_in") or 0 for x in lan), "tokens_out": sum(x.get("tokens_out") or 0 for x in lan),
            "unmeasured_calls": sum(1 for x in lan if not x["measurable"]),
            "unpriced_calls": sum(1 for x in lan if x["measurable"] and x["cost_usd"] is None),
            "bedrock": sum(x["cost_usd"] or 0 for x in lan if x.get("mien_tran"))}   # [ghi chú kỹ thuật] phần tham khảo, không tính trần (nằm TRONG main / side)
    return {"calls": lan, "total": tong}


# ------------------------------------------------------------ HTTP
KIEU = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
        ".json": "application/json", ".woff2": "font/woff2", ".ttf": "font/ttf", ".png": "image/png", ".ico": "image/x-icon"}


_DUOI_NHAN_FILE = {".csv", ".doc", ".docx", ".gif", ".jpeg", ".jpg", ".json", ".jsonl", ".markdown", ".md", ".pdf", ".png", ".ppt", ".pptx",
                   ".tsv", ".txt", ".webp", ".xls", ".xlsx"}   # [ghi chú kỹ thuật] tập con danh sách cho phép của Capitol (services/upload_allowlist.py)


def _ly_do_rao_chan(phien: str) -> str | None:
    """[ghi chú kỹ thuật] lượt FAILED: CÓ phải do khối kiểm số chặn không (lần kiểm CUỐI không đạt), đọc từ dấu vết sẵn có. Không có / không đọc được ⇒ None.
    [ghi chú kỹ thuật] hoi_nhanh chỉ dùng giá trị này làm CỜ (có / không) — chữ lý do KHÔNG đi ra trang (thường nêu chính con số bị chặn)."""
    try:
        d = dau_vet.dau_vet(xem, kiem_ma(phien), chi_phi, cong_cu_da_goi, NHAT_KY) or {}
        for k in d.get("khoi") or []:
            c = ((k.get("rao") or {}).get("cuoi")) or {}
            if k.get("loai") == "guardrails_node" and c.get("dat") is False:
                return str(c.get("ly_do") or "câu trả lời không qua kiểm số")
    except Exception:  # noqa: BLE001
        return None
    return None


def _goi_app_rag(m: str, duong: str, form: dict | None = None, tep: tuple | None = None, timeout: int = 60):
    """[ghi chú kỹ thuật] goi_app cho KTL.bat_dau_nap — QUA cổng RAG theo tổ chức (goi_cong_rag.tao_goi_app nhóm kỹ thuật: chỉ 2 đường
    kho / nạp, uid phải khớp tổ chức, không /all). Tên file đi trong header mã hoá %, không vào URL / nhật ký."""
    if not RAG_APP:
        raise RuntimeError("chưa nối cổng RAG")
    import goi_cong_rag   # file nhóm kỹ thuật (gan_rag_to_chuc.sh chép vào giao_dien/); nạp lúc gọi ⇒ thiếu file chỉ hỏng đường nạp, trang vẫn bật
    return goi_cong_rag.tao_goi_app(org())(m, duong, form=form, tep=tep, timeout=timeout)

def _hoi_kpi():
    """[ghi chú kỹ thuật] nạp muộn: trang kéo–thả vẫn chạy khi phần KPI lỗi nạp; sổ tiền đọc BUOI_S5 / TRAN_USD của tiến trình này."""
    sys.path.insert(0, str(THU_MUC.parent / "kpi_capitol"))
    import hoi_kpi
    return hoi_kpi


def _tao_kpi_tu_chat(cau_hoi: str) -> tuple[int, dict]:
    """[ghi chú kỹ thuật] Trợ lý dựng nhờ trang tạo workflow KPI: dựng bộ 5 khối chuẩn bằng ĐÚNG hàm của nút "Bộ KPI sẵn" (khoi_kpi.bo_san — lời dặn theo kỳ /
    ngày chốt hiện hành) rồi lưu qua ĐÚNG đường Lưu của trang (luu: kiểm hình + điền lại lời dặn + luật KPI). Không chạy gì, 0 đồng. Tổ chức = org() của yêu cầu."""
    code, hinh = khoi_kpi.bo_san(_hoi_kpi(), org(), cau_hoi, None, lambda pl: sang_chung(tu_capitol(pl, DM)))
    if code != 200:
        return code, hinh
    ten = "KPI (trợ lý tạo) — " + " ".join(cau_hoi.split())[:80]
    hinh["name"], hinh["description"] = ten, "Trợ lý dựng nhờ trang tạo từ “Bộ KPI sẵn” theo câu hỏi của người dùng; chỉ khác bộ sẵn ở câu hỏi."
    code, r = luu(hinh)
    return code, (dict(r, ten=ten) if code == 200 and isinstance(r, dict) else r)


def _tim_tai_lieu(og, kho_, cau, k=8):
    """[ghi chú kỹ thuật] trang tự tìm trong kho của file kéo vào khung chat; mỗi lần gọi ghi MỘT dòng vào sổ đếm lần tìm của ngày (tiền tạo vector câu hỏi
    ghi sổ bên dịch vụ đọc tài liệu — bên mình chỉ đếm để cuối ngày báo họ)."""
    c, ds, loi = tim_kho.tim(og, kho_, cau, k)
    try:
        for _k in ([kho_] if isinstance(kho_, str) else list(kho_)):   # [ghi chú kỹ thuật] một lời gọi có thể mang ≤ 5 kho — đếm theo từng kho
            SO_KHO.ghi_tim(og, _k, c, len(ds))
    except OSError:
        ghi("KHO TÀI LIỆU: không ghi được sổ đếm lần tìm")
    return c, ds, loi


def _kho_ben_cua(og) -> list:
    """[ghi chú kỹ thuật] Kho BỀN (không tạm), chưa ẩn, người dùng không bấm Ẩn — của ĐÚNG tổ chức `og`. [sửa 20/11 sau lượt thật: bản đầu dùng tên TDL vốn chỉ được
    nạp BÊN TRONG các hàm xử lý ⇒ NameError bị nuốt ⇒ thẻ kho không bao giờ được gửi; nay nạp mô-đun ngay tại đây và dùng .get cho từng ô]"""
    import trang_du_lieu as _TDL
    an = _TDL.doc_an(GOC_DU_LIEU, og)
    return [x for x in SO_KHO.ds(og) if not x.get("an") and not x.get("tam") and x.get("sha") not in an]


def _kw_tro_ly(o):
    """[ghi chú kỹ thuật] Tham số của khung chat (noi_may_chu.dung_dep) — một chỗ cho /api/tro-ly và /api/tro-ly/tai-lieu*. o = tổ chức của yêu cầu."""
    return dict(
        xem=xem, kiem_ma=kiem_ma, pb_moi_nhat=pb_moi_nhat, toi_da=muc_toi_da.toi_da, chay=chay, ket_qua_luot=ket_qua_luot,
        S5=_hoi_kpi()._so_chung, ghi=ghi, cache=(CTL, KHO_CTL, _cache_lay, _van_tay_kpi, org),   # [ghi chú kỹ thuật] cache dùng chung (bài 46)
        ds_nv=lambda: ((khoi_kpi.moi_truong(_hoi_kpi(), o) or {}).get("meta") or {}).get("employee_ids"),   # [ghi chú kỹ thuật] mã NV bản dữ liệu đang dùng
        # [ghi chú kỹ thuật] PDF bản chụp kéo vào khung chat ⇒ nạp kho TẠM bằng ĐÚNG bộ nạp của tab Tài liệu; tìm qua cổng RAG. Nhận org làm tham số (dep dùng chung).
        nap_tai_lieu=(lambda og, ten, than: KTL.bat_dau_nap(og, ten, than, tam=True, so=SO_KHO, goi_app=_goi_app_rag, H=_hoi_kpi(), ghi=ghi,
                                                            duoi_nhan=KTL.DUOI_CHAT, nap_lai=False)) if RAG_APP else None,   # [ghi chú kỹ thuật]
        viec_tai_lieu=lambda og, ma: KTL.trang_thai(og, ma),
        tim_tai_lieu=_tim_tai_lieu,
        dua_file=dua_file_vao_kho,   # [ghi chú kỹ thuật] byte file bảng tính của khung chat ⇒ kho file Capitol
        # [ghi chú kỹ thuật] kho BỀN (không tạm), chưa ẩn, người dùng không bấm Ẩn — của ĐÚNG tổ chức được hỏi (hàm nhận tổ chức vì dep dùng chung)
        ds_kho_tai_lieu=_kho_ben_cua if RAG_APP else None,
        operator=lambda cau, conv: chat_operator.gui(cau, conv, org=o, goi_ab=goi_ab, H=_hoi_kpi(), ghi=ghi, tao_kpi=_tao_kpi_tu_chat,   # [ghi chú kỹ thuật]
                                                     ds_wf=lambda: so_ban_luu(o),   # [ghi chú kỹ thuật] chỉ đếm bản LƯU (bản RUN của lượt chạy khác không được làm khoá đường chat)
                                                     kiem_wf=_kiem_wf_da_luu))


class XuLy(BaseHTTPRequestHandler):
    def _gui(self, code, body: bytes, kieu: str, them: tuple = ()):
        self.send_response(code)
        self.send_header("Content-Type", kieu)
        self.send_header("Cache-Control", "no-store")
        for k, v in them:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._gui(code, json.dumps(xem.che(obj), ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

    def _than(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5_000_000:
            raise ValueError("thân yêu cầu quá lớn")
        b = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(b, dict):   # [ghi chú kỹ thuật] `[]` / `null` / số / chuỗi ⇒ 400 có lời (trước: 503 kèm tên lỗi Python ở mọi đường POST)
            raise ValueError("thân yêu cầu phải là một đối tượng JSON (dạng {…})")
        return b

    def _co_ma(self) -> bool:
        """Cookie xuong_ma_<cổng> phải BẰNG mã mở trang (so thời gian hằng). Chưa tạo mã ⇒ luôn sai."""
        if not MA_MO:
            return False
        ten = "xuong_ma_%d=" % CONG
        for manh in (self.headers.get("Cookie") or "").split(";"):
            manh = manh.strip()
            if manh.startswith(ten):
                return hmac.compare_digest(manh[len(ten):].encode(), MA_MO.encode())
        return False

    def _host_dung(self) -> bool:
        return (self.headers.get("Host") or "").strip().lower() in ("127.0.0.1:%d" % CONG, "localhost:%d" % CONG)

    def _tu_choi(self, loi: str, code: int = 403):
        """[ghi chú kỹ thuật] đọc BỎ thân (≤ 5 MB, như _than) TRƯỚC khi trả lỗi — trả sớm khi thân còn trong ống ⇒ khách đôi khi nhận RST thay vì 403.
        Thân lớn hơn ⇒ không đọc, đóng kết nối sau câu trả lời."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if 0 < n <= 5_000_000:
            self.rfile.read(n)
        elif n > 0:
            self.close_connection = True
        return self._json({"loi": loi}, code)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        # [ghi chú kỹ thuật] rào Host cho MỌI GET (như POST và như hộp gác 18300 / 18301 / 18233): thiếu rào này thì trang lạ dùng DNS rebinding
        # (tên lạ trỏ về 127.0.0.1) đọc được /api/runs, /api/runs/<id> (câu trả lời KPI, số tiền), /api/luot… cùng nguồn với chính nó.
        if not self._host_dung():
            return self._json({"loi": "Host không được phép"}, 403)
        try:
            if p == "/xem" or p == "/xem/":
                return self._gui(200, (THU_MUC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if p in ("/hoi-nhanh", "/hoi-nhanh/"):   # [ghi chú kỹ thuật] trang Hỏi KPI — chế độ hỏi nhanh (HTML thuần; file tĩnh không cần mã, mọi /api vẫn đòi mã)
                return self._gui(200, (THU_MUC / "hoi_nhanh.html").read_bytes(), "text/html; charset=utf-8")
            if p in ("/hoi-kpi", "/hoi-kpi/"):   # [ghi chú kỹ thuật] trang Hỏi KPI (HTML thuần, xử lý ở kpi_capitol/hoi_kpi.py)
                return self._gui(200, (THU_MUC / "hoi_kpi.html").read_bytes(), "text/html; charset=utf-8")
            if p.startswith("/api/"):
                if not self._co_ma():   # [ghi chú kỹ thuật] mọi GET /api/* đòi mã mở trang (dữ liệu lượt chạy, câu trả lời KPI, danh sách workflow, file)
                    return self._json({"loi": "Cần mã mở trang: mở đường dẫn có mã mà người bật máy chủ đưa.", "can_ma": True}, 401)
                return self._api_get(p, q)
            f = (DIST / (p.lstrip("/") or "index.html")).resolve()
            if not str(f).startswith(str(DIST.resolve())) or not f.is_file():
                f = DIST / "index.html"
            if not f.is_file():
                return self._gui(503, "Chưa có bản dựng giao diện: cd chay_thu/giao_dien/web && npm run build".encode(), "text/plain; charset=utf-8")
            return self._gui(200, f.read_bytes(), KIEU.get(f.suffix, "application/octet-stream"))
        except Exception as e:
            return self._json({"loi": f"{type(e).__name__}: {e}"}, 503)

    def _api_get(self, p, q):
        if p == "/api/du-lieu/kpi":   # [ghi chú kỹ thuật] bảng "tháng này đã có gì" + các lần tải gần đây (sổ của trang — không gọi hệ thống tính thưởng)
            import trang_du_lieu as TDL
            mt_nho = khoi_kpi._nho.get("mt")   # [ghi chú kỹ thuật] chỉ đọc bản đang nhớ — KHÔNG gọi moi_truong (đo: 18,9 giây khi bộ nhớ 60 giây vừa hết)
            ky_chay = ((mt_nho or {}).get("meta") or {}).get("period") if isinstance(mt_nho, dict) and mt_nho.get("org") == org() else None
            so = TDL.doc_kpi(GOC_DU_LIEU, org(), (q.get("ky") or [None])[0], ky_chay)
            # [ghi chú kỹ thuật] hỏi LÕI "kỳ này có gì" qua hộp 18402 (chỉ đọc, 0 đồng); không được ⇒ sổ trang (có ghi rõ nguồn)
            return self._json(TDL.bang_thang(so, (q.get("ky") or [None])[0] or ky_chay or so.get("ky"), _hoi_ky_loi))
        if p == "/api/du-lieu/tai-lieu":   # [ghi chú kỹ thuật] tài liệu đã đưa vào — cho người dùng thường: không kho tạm, không tài liệu đã ẩn, không tên kho kỹ thuật
            import trang_du_lieu as TDL
            return self._json({"tai_lieu": TDL.ds_tai_lieu(SO_KHO.ds(org()), TDL.doc_an(GOC_DU_LIEU, org())), "noi_app": bool(RAG_APP)})
        if p == "/api/tai-lieu/kho":   # [ghi chú kỹ thuật] kho của TỔ CHỨC hiện tại (sổ của trang, không hỏi app, không /all)
            return self._json({"noi_app": bool(RAG_APP), "kho": SO_KHO.ds(org()), "trang_toi_da": KTL.TRANG_TOI_DA, "duoi": sorted(KTL.DUOI)})
        if p == "/api/tai-lieu/viec":
            code, r = KTL.trang_thai(org(), (q.get("ma") or [""])[0])
            return self._json(r, code)
        if p == "/api/tai-lieu/mo":   # mở NGUỒN: bản sao theo sha, chỉ của tổ chức hiện tại; tên tải về cố định (không đưa tên người dùng vào header)
            r = SO_KHO.duong_mo(org(), (q.get("sha") or [""])[0])
            if not r:
                return self._json({"loi": "không có tài liệu này"}, 404)
            f, _ten = r
            kieu = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
                    ".markdown": "text/plain; charset=utf-8"}.get(f.suffix, "application/octet-stream")
            return self._gui(200, f.read_bytes(), kieu, (("Content-Disposition", "inline; filename=tai_lieu" + f.suffix), ("X-Content-Type-Options", "nosniff")))
        if p.startswith("/api/chay-lai/"):   # [ghi chú kỹ thuật] huy hiệu "dùng lại" — chỉ đọc
            try:
                ma = kiem_ma(p.rsplit("/", 1)[1])
            except ValueError:
                return self._json({"loi": "mã phiên không hợp lệ"}, 400)
            return self._json(dung_lai_cua(ma))
        # hình dữ liệu chung (mới)
        if p == "/api/blocks":
            return self._json(DM.chung())
        if p == "/api/kho-bang":   # [ghi chú kỹ thuật] danh sách dataset của tổ chức cho ô chọn (chỉ đọc platform-api, không AI)
            try:
                return self._json({"datasets": kho_bang.ds_cho_trang(org())})
            except Exception as e:  # noqa: BLE001
                return self._json({"loi": f"chưa đọc được kho bảng của tổ chức ({type(e).__name__})"}, 503)
        if p == "/api/prices":
            return self._json(GIA)
        if p == "/api/mo-hinh-khoi":   # [ghi chú kỹ thuật] nhãn mô hình thật — chỉ đọc file mô hình khối, 0 AI
            return self._json(mo_hinh_khoi_that())
        # [10/11, A] ?ngay=YYYY-MM-DD&so=N — bỏ giới hạn cứng 80 / 60 (người dùng: "xem thêm" + lọc theo ngày); /api/dem = tổng theo ngày
        ngay_loc = (q.get("ngay") or [None])[0] or None
        so_dong = (q.get("so") or [None])[0]
        so_dong = int(so_dong) if so_dong and so_dong.isdigit() else None
        # [ghi chú kỹ thuật] mặc định chỉ workflow của TỔ CHỨC HIỆN TẠI (bản của tổ chức cũ trùng tên, dễ bấm nhầm); ?ca_cu=1 ⇒ hiện cả tổ chức cũ
        org_loc = None if (q.get("ca_cu") or [""])[0] == "1" else org()
        if p == "/api/dem":
            return self._json(xem.dem(org_loc))
        if p == "/api/workflows":
            return self._json(xem.ds_workflow(ngay_loc, so_dong, org_loc))
        if p == "/api/kpi/ghep/khuon":   # [ghi chú kỹ thuật] khuôn cột chuẩn (cột trái màn ghép) — sau rào mã mở trang như mọi đường /api
            return self._ghep_kpi("GET")
        if p == "/api/kpi/dung-san":   # [ghi chú kỹ thuật] khối KPI dựng sẵn + bộ sẵn + mẫu + lời dặn / công cụ ĐỂ XEM (dữ liệu nhóm kỹ thuật, một nguồn)
            o = org()
            return self._json(khoi_kpi.ds(_hoi_kpi(), o, lambda: xem.ds_workflow(None, 2000, o)))
        m = re.match(r"^/api/workflows/([^/]+)$", p)
        if m:
            return self._json(mot_workflow(kiem_ma(m.group(1))))
        if p == "/api/runs":
            return self._json(xem.ds_luot(ngay_loc, so_dong))
        m = re.match(r"^/api/runs/([^/]+)/dau-vet$", p)   # [ghi chú kỹ thuật] dấu vết lượt chạy — chỉ đọc PG + sự kiện, qua _json (xem.che)
        if m:
            r = dau_vet.dau_vet(xem, kiem_ma(m.group(1)), chi_phi, cong_cu_da_goi, NHAT_KY)
            return self._json(r if r is not None else {"loi": "không thấy lượt"}, 200 if r is not None else 404)
        m = re.match(r"^/api/runs/([^/]+)/truy-van$", p)   # [ghi chú kỹ thuật] câu SQL lượt đã gọi + bảng kết quả (workflow "Hỏi dữ liệu bằng lời") — chỉ đọc PG
        if m:
            return self._json(truy_van_sql.doc(xem, kiem_ma(m.group(1))))
        m = re.match(r"^/api/runs/([^/]+)/tep$", p)   # [ghi chú kỹ thuật] danh sách file của lượt — KHÔNG có bucket / khoá / URL
        if m:
            return self._json(tai_ve.ds_cong_khai(xem, kiem_ma(m.group(1))))
        if p == "/api/tro-ly/tai-lieu":   # [ghi chú kỹ thuật] cuộc trò chuyện này còn file mở không (tên + cỡ; không nội dung)
            sys.path.insert(0, str(THU_MUC.parent / "va_mot_o_chat" / "gan"))
            import noi_may_chu
            code, r = noi_may_chu.tai_lieu_dang_mo(org(), (q.get("conv") or [""])[0])
            return self._json(r, code)
        if p == "/api/tro-ly/tep":   # [ghi chú kỹ thuật] file "xuất kết quả vừa trả lời" — chỉ đúng cuộc chat đã xuất, 30 phút (noi_may_chu.tai_tep)
            _g = str(THU_MUC.parent / "va_mot_o_chat" / "gan")
            if _g not in sys.path:
                sys.path.insert(0, _g)
            import noi_may_chu
            code, ten, data = noi_may_chu.tai_tep((q.get("conv") or [""])[0], (q.get("id") or [""])[0])
            if code != 200:
                return self._json({"loi": "Không có file này (đã quá 30 phút hoặc không thuộc cuộc trò chuyện này) — xin xuất lại trong chat."}, 404)
            self.send_response(200)
            for k, v in tai_ve.dau_tai_ve(ten, len(data)):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
            return None
        if p == "/api/tep/tai-ve":   # [ghi chú kỹ thuật] ?phien=<uuid>&tep=<uuid> — máy chủ tự tìm khoá trong sự kiện của lượt (tai_ve.py)
            code, info, data = tai_ve.doc_tep(xem, kiem_ma((q.get("phien") or [""])[0]), (q.get("tep") or [""])[0])
            if code != 200:
                return self._json(info, code)
            if (q.get("dang") or [""])[0] == "xlsx":   # [ghi chú kỹ thuật] bản Excel của file .csv / .tsv — file gốc không đổi (không có &dang thì trả nguyên byte như cũ)
                import bang_sang_xlsx
                try:
                    ten_x, data, tt_x = bang_sang_xlsx.chuyen(data, info["ten"])
                except bang_sang_xlsx.KhongChuyen as e:
                    return self._json({"loi": str(e)}, 422)
                info = dict(info, ten=ten_x)
                ghi(f"TẢI BẢN EXCEL của file bảng: {tt_x['dong']} dòng × {tt_x['cot']} cột · tách bằng {tt_x['tach']} · {tt_x['ma_hoa']} · {len(data)} byte")   # không tên file
            self.send_response(200)
            for k, v in tai_ve.dau_tai_ve(info["ten"], len(data)):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
            return None
        m = re.match(r"^/api/runs/([^/]+)/ket-qua$", p)   # [ghi chú kỹ thuật] kết quả đủ + đã đọc thành dữ liệu (màn hình kết quả)
        if m:
            r = ket_qua_luot(kiem_ma(m.group(1)))
            return self._json(r if r is not None else {"loi": "không thấy lượt"}, 200 if r is not None else 404)
        m = re.match(r"^/api/runs/([^/]+)/cost$", p)
        if m:
            r = chi_phi(m.group(1))
            r["tool_calls"] = cong_cu_da_goi(m.group(1))
            # đã kiểm 04/11 (MAJOR): tiền Serper không được ghi ở đâu ⇒ đếm lần gọi web_search × giá một lượt (bang_gia.json "serper")
            gs = GIA.get("serper") or {}
            for tc in [x for x in r["tool_calls"] if x["tool"] == "web_search"]:
                gia = gs.get("per_query")
                r["calls"].append({"node": tc["node"], "model": "serper", "at": tc["at"], "call_kind": "tool", "measurable": True,
                                   "tokens_in": 0, "tokens_out": 0, "cost_usd": gia, "price_source": gs.get("source"), "price_read_at": gs.get("read_at"),
                                   "why": "một lượt tìm Serper" + ("" if gia is not None else " — chưa có giá (điền per_query trong bang_gia.json)")})
            r["total"]["tool"] = sum(x["cost_usd"] or 0 for x in r["calls"] if x["call_kind"] == "tool")
            r["total"]["unpriced_calls"] = sum(1 for x in r["calls"] if x["measurable"] and x["cost_usd"] is None)
            r["inputs"] = _dau_vao.get(m.group(1), {})
            r["checks"] = doi_chieu_luot(m.group(1))
            r["so"] = __import__("tien_theo_so").tim(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), m.group(1))   # [ghi chú kỹ thuật] dòng sổ của phiên: khối chạy code / làm file không lưu lời gọi AI trong kết quả khối ⇒ calls rỗng, tiền thật nằm ở sổ
            return self._json(r)
        m = re.match(r"^/api/runs/([^/]+)$", p)
        if m:
            sau = (q.get("sau") or [None])[0]
            if sau and not xem.GIO.match(sau):
                raise ValueError("mốc không hợp lệ")
            r = xem.mot_luot(kiem_ma(m.group(1)), sau)
            return self._json(r if r else {"loi": "không thấy lượt"}, 200 if r else 404)
        # đường cũ của trang xem (/xem)
        if p == "/api/luot":
            return self._json(xem.ds_luot())
        m = re.match(r"^/api/luot/([^/]+)$", p)
        if m:
            sau = (q.get("sau") or [None])[0]
            r = xem.mot_luot(kiem_ma(m.group(1)), sau if sau and xem.GIO.match(sau) else None)
            return self._json(r or {"loi": "không thấy lượt"}, 200 if r else 404)
        if p == "/api/workflow":
            return self._json(xem.ds_workflow())
        m = re.match(r"^/api/workflow/([^/]+)$", p)
        if m:
            return self._json(xem.mot_workflow(kiem_ma(m.group(1))))
        if p == "/api/danh_muc":
            return self._json(xem.danh_muc())
        m = re.match(r"^/api/hoi-nhanh/([0-9a-f-]{36})$", p)   # [ghi chú kỹ thuật] trạng thái một lượt hỏi nhanh — chỉ đọc PG; lượt hỏng ⇒ không trả số
        if m:
            code, r = hoi_nhanh.trang_thai(
                m.group(1), ket_qua=ket_qua_luot, pb=pb_moi_nhat, ly_do_chan=_ly_do_rao_chan,
                tt_luot=lambda s: (xem.sql("select row_to_json(x) from (select status::text as tt from workflow_run "
                                           f"where session_id='{kiem_ma(s)}' order by created_at desc limit 1) x") or {}).get("tt"),
                khoi_ra=lambda pl: next((n.get("id") for n in pl.get("nodes") or [] if ((n.get("data") or {}).get("struct") or {}).get("node_id") == "text_output_node"), None))
            return self._json(r, code)
        m = re.match(r"^/api/hoi-kpi/([0-9a-f-]{36})$", p)
        if m:
            code, r = _hoi_kpi().trang_thai(m.group(1))
            return self._json(r, code)
        return self._json({"loi": "không có đường này"}, 404)

    def _ghep_kpi(self, phuong_thuc):
        """[ghi chú kỹ thuật] Màn ghép cột: mọi phép kiểm + gọi hộp 18402 ở ghep_kpi.xu_ly (thử: va_man_ghep/thu_ghep_kpi.py)."""
        ma, d = ghep_kpi.xu_ly(phuong_thuc, self.path, self.headers, self.rfile.read, cong=CONG, ma_tai_len=_ma_tai_len, ghi=ghi)
        return self._json(d, ma)

    def _tai_len_kpi(self):
        """[ghi chú kỹ thuật] Tải file Excel KPI: thân nhị phân ⇒ hộp chuyển tiếp 127.0.0.1:18402 ⇒ kpi-backend. Máy chủ trang KHÔNG giữ khoá,
        KHÔNG gửi tên file; chỉ trả tóm tắt cho trang (lỗi không vào lời nhắc Agent). Rào Host chống DNS rebinding (đối chiếu kỹ thuật)."""
        import urllib.parse as _up
        if (self.headers.get("Host") or "").strip().lower() not in ("127.0.0.1:%d" % CONG, "localhost:%d" % CONG):
            return self._json({"refused": "Host không được phép"}, 403)
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
            return self._json({"refused": "cần gửi thân nhị phân"}, 415)
        n = self.headers.get("Content-Length")
        if not n or not n.isdigit() or not 4 <= int(n) <= 20 * 1024 * 1024:
            return self._json({"refused": "file phải từ 4 byte tới 20 MB"}, 413)
        than = self.rfile.read(int(n))
        if than[:4] != b"PK\x03\x04":
            return self._json({"refused": "không phải file .xlsx"}, 422)
        q = {k: v for k, v in _up.parse_qsl(urlparse(self.path).query) if k in ("loai", "ky", "ngay_chot", "data_version")}
        if q.get("loai") in ("raw", "tracking") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", q.get("ngay_chot") or ""):
            # [ghi chú kỹ thuật] thiếu ngày chốt ⇒ backend tự lấy ngày khác ⇒ cùng file mà ra bản khác (nhóm phát triển xác nhận đo: 30/10 ≠ 31/10)
            return self._json({"refused": "File RAW / Tracking cần ngày chốt (năm-tháng-ngày)."}, 400)
        # [ghi chú kỹ thuật] hộp chuyển tiếp 18402 tự gắn khoá hệ thống ⇒ phải đòi thêm MÃ RIÊNG (hộp Docker khác gọi vòng qua
        # host.docker.internal tới được cổng này). Mã ở file 600 ngoài repo, đọc MỖI LẦN gọi; không có / sai dạng ⇒ không gửi. Không ghi mã vào nhật ký.
        ma_tl = _ma_tai_len()
        if ma_tl is None:
            return self._json({"refused": "hộp tải file KPI chưa sẵn mã (thiếu file mã trên máy) — báo người vận hành"}, 503)
        req = urllib.request.Request(f"{_KPI_TAI_LEN_URL}/tai-len?" + _up.urlencode(q), data=than, method="POST",
                                     headers={"Content-Type": "application/octet-stream", "Content-Length": str(len(than)), "X-Tai-Len-Ma": ma_tl})
        try:
            with _MKDH.mo(req, timeout=200) as r:   # [ghi chú kỹ thuật]
                ma, d = r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            ma = e.code
            try:
                d = json.loads(e.read() or b"{}")
            except json.JSONDecodeError:
                d = {}
        except (urllib.error.URLError, OSError):
            return self._json({"refused": "hộp tải file KPI chưa bật (127.0.0.1:18402) — báo người vận hành"}, 502)
        ghi(f"TẢI FILE KPI loai={q.get('loai')} ky={q.get('ky')} cỡ={len(than)} ⇒ HTTP {ma}")   # không tên file
        viec_nen_lam = None
        try:   # [ghi chú kỹ thuật] ghi sổ lần tải (cho bảng "tháng này đã có gì") + một dòng lời thường "việc nên làm" khi bị từ chối; lỗi ghi sổ KHÔNG chặn việc tải
            import trang_du_lieu as TDL
            lan = TDL.ghi_lan_tai(GOC_DU_LIEU, org(), q.get("loai"), q.get("ky"), q.get("ngay_chot"), ma, d or {}, len(than))
            viec_nen_lam = TDL.viec_theo(lan["ket_qua"], d)   # [ghi chú kỹ thuật] theo MÃ lỗi của lõi trước, rồi mới theo loại kết quả
        except Exception as e:  # noqa: BLE001
            ghi(f"TRANG DỮ LIỆU: không ghi được sổ lần tải ({type(e).__name__})")
        if ma != 200:
            # [ghi chú kỹ thuật] chuyển MÃ lỗi của lõi ra thân trả lời (ô "ma") để chấm / rẽ nhánh theo mã; lời cho người dùng bỏ đường API
            import trang_du_lieu as TDL2
            return self._json({"refused": TDL2.loi_cho_nguoi((d or {}).get("refused")) or f"tải lên không thành công (mã {ma})", "ma": TDL2.ma_cua_loi(d),
                               "viec_nen_lam": viec_nen_lam}, ma)
        ban_du_lieu.ghi_ban(d)   # [ghi chú kỹ thuật] nhớ kỳ / ngày chốt của bản này (backend trả) để lúc chạy so với bản đang bật
        _cache_xoa("tải file KPI mới")   # [ghi chú kỹ thuật] dữ liệu mới ⇒ mọi câu trả lời cũ không còn đúng
        return self._json({k: d.get(k) for k in ("data_version", "run_id", "period", "cutoff", "team_total", "da_co_san", "data_flags")})

    def _tro_ly_tai_lieu(self):
        """[ghi chú kỹ thuật] Kéo file vào khung chat: thân nhị phân + ?conv=<mã cuộc>&ten=<tên> ⇒ noi_may_chu.nhan_tai_lieu (kiểm tên / đuôi / cỡ, tách chữ
        có hạn tài nguyên, giữ "file đang mở" 24 giờ theo tổ chức + cuộc trò chuyện). Tên file KHÔNG vào nhật ký. Không gọi AI, không tốn tiền."""
        import urllib.parse as _up
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
            return self._json({"loi": "cần gửi thân nhị phân"}, 415)
        n = self.headers.get("Content-Length")
        if not n or not n.isdigit() or not 1 <= int(n) <= KTL.CO_TOI_DA:
            return self._json({"loi": "File phải từ 1 byte tới 20 MB."}, 413)
        if not (os.environ.get("BUOI_S5") and os.environ.get("TRAN_USD")):
            return self._json({"loi": "Máy chủ chưa sẵn sàng, bạn thử lại sau nhé."}, 503)
        q = dict(_up.parse_qsl(urlparse(self.path).query))
        than = self.rfile.read(int(n))
        sys.path.insert(0, str(THU_MUC.parent / "va_mot_o_chat" / "gan"))
        import noi_may_chu
        o = org()
        code, r = noi_may_chu.nhan_tai_lieu(o, q.get("conv"), q.get("ten", ""), than, **_kw_tro_ly(o))
        return self._json(r, code)

    def _nap_tai_lieu(self):
        """[ghi chú kỹ thuật] Nút "Nạp vào kho": thân nhị phân + ?ten=<tên>&tam=1 ⇒ KTL.bat_dau_nap (kiểm tên / đuôi / cỡ / trang, giữ chỗ
        mức tối đa, nạp NỀN một việc mỗi lúc). Tên KHÔNG vào nhật ký. Chưa nối cổng (RAG_MA / RAG_NAP_MA trống) ⇒ 503, không gọi gì."""
        import urllib.parse as _up
        if not RAG_APP:
            return self._json({"loi": "Kho tài liệu chưa nối app RAG (người vận hành chưa bật hộp cổng RAG) — chưa nạp gì."}, 503)
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
            return self._json({"loi": "cần gửi thân nhị phân"}, 415)
        n = self.headers.get("Content-Length")
        if not n or not n.isdigit() or not 1 <= int(n) <= KTL.CO_TOI_DA:
            return self._json({"loi": "file phải từ 1 byte tới 20 MB"}, 413)
        q = dict(_up.parse_qsl(urlparse(self.path).query))
        than = self.rfile.read(int(n))
        code, r = KTL.bat_dau_nap(org(), q.get("ten", ""), than, tam=q.get("tam") == "1", so=SO_KHO, goi_app=_goi_app_rag, H=_hoi_kpi(), ghi=ghi)
        return self._json(r, code)

    def _tai_len_tep(self):
        """[ghi chú kỹ thuật] Nút "Chọn file" của khối Nhận file: thân nhị phân + ?ten=<tên file> ⇒ multipart như trình duyệt ⇒ agentic-backend
        POST /files/{org}/upload (cùng đường nhan_file/chay_b1.py 09/11) ⇒ trả phong bì file cho trang điền vào ô file_data. Tên file cần cho
        Capitol kiểm đuôi; KHÔNG ghi vào nhật ký. File vào KHO FILE CỦA CAPITOL (khối nhận file) — file dữ liệu KPI tải ở tab Dữ liệu KPI (trang ghi rõ)."""
        import urllib.parse as _up
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
            return self._json({"loi": "cần gửi thân nhị phân"}, 415)
        n = self.headers.get("Content-Length")
        if not n or not n.isdigit() or not 1 <= int(n) <= 20 * 1024 * 1024:
            return self._json({"loi": "file phải từ 1 byte tới 20 MB"}, 413)
        ten = dict(_up.parse_qsl(urlparse(self.path).query)).get("ten", "")
        ten = ten.replace("\\", "/").split("/")[-1].strip()
        duoi = ("." + ten.rsplit(".", 1)[-1].lower()) if "." in ten else ""
        if not ten or len(ten) > 200 or any(c in ten for c in '"\r\n\x00') or duoi not in _DUOI_NHAN_FILE:
            return self._json({"loi": "tên / đuôi file không được nhận (xem danh sách trên trang)"}, 422)
        than = self.rfile.read(int(n))
        ranh = "----chaythu" + uuid.uuid4().hex
        goi = (f'--{ranh}\r\nContent-Disposition: form-data; name="file"; filename="{ten}"\r\n'
               f"Content-Type: application/octet-stream\r\n\r\n").encode() + than + f"\r\n--{ranh}--\r\n".encode()
        req = urllib.request.Request(f"{API}/files/{org()}/upload", data=goi, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={ranh}", **_ma_18300(Path(__file__).resolve().parent.parent)})   # [ghi chú kỹ thuật]
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                ma, d = r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            ma = e.code
            try:
                d = json.loads(e.read() or b"{}")
            except json.JSONDecodeError:
                d = {}
        except (urllib.error.URLError, OSError):
            return self._json({"loi": "không gọi được agentic-backend (18300)"}, 502)
        ghi(f"TẢI FILE KHỐI NHẬN FILE đuôi={duoi} cỡ={len(than)} ⇒ HTTP {ma}")   # không tên file
        if ma >= 300 or not isinstance(d, dict) or not d.get("file"):
            return self._json({"loi": (d.get("detail") if isinstance(d, dict) else None) or f"Capitol không nhận file (mã {ma})"}, ma if ma >= 300 else 502)
        return self._json(d)

    def do_POST(self):
        p = urlparse(self.path).path
        # [ghi chú kỹ thuật] rào Host CHUNG cho mọi POST (chống DNS rebinding: trang lạ trỏ tên về 127.0.0.1 rồi bấm Chạy / Duyệt)
        if not self._host_dung():
            return self._tu_choi("Host không được phép")
        # [ghi chú kỹ thuật] chống trang / file .html KHÁC NGUỒN gửi thẳng tới 127.0.0.1:18400 (Host vẫn đúng ⇒ rào Host
        # không chặn): có Origin thì phải là chính trang này; Origin "null" (file mở từ đĩa, iframe sandbox) ⇒ từ chối. Áp cho MỌI POST (cả tải lên).
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower() not in ("http://127.0.0.1:%d" % CONG, "http://localhost:%d" % CONG):
            return self._tu_choi("Origin không được phép")
        if p == "/api/mo":   # [ghi chú kỹ thuật] đổi mã (phần #k= của đường mở trang) lấy cookie — đường POST DUY NHẤT không đòi cookie
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._tu_choi("Content-Type phải là application/json")
            try:
                k = self._than().get("k")
            except (ValueError, AttributeError):
                k = None
            if not (MA_MO and isinstance(k, str) and hmac.compare_digest(k.encode(), MA_MO.encode())):
                time.sleep(0.5)   # chậm lại việc dò mã (mã 256 bit — dò không tới, đây chỉ là lớp phụ)
                return self._json({"loi": "Mã mở trang không đúng."}, 401)
            return self._gui(200, b'{"ok": true}', "application/json; charset=utf-8",
                             (("Set-Cookie", "xuong_ma_%d=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Strict" % (CONG, MA_MO, 30 * 24 * 3600)),))
        if not self._co_ma():   # [ghi chú kỹ thuật] mọi POST khác (chạy, duyệt, lưu, chat, hỏi KPI, tải lên…) đòi mã mở trang
            return self._tu_choi("Cần mã mở trang: mở đường dẫn có mã mà người bật máy chủ đưa.", 401)
        if p == "/api/kpi/tai-len":
            return self._tai_len_kpi()
        if p.startswith("/api/kpi/ghep/"):   # [ghi chú kỹ thuật] bo-cuc / thu / xac-nhan
            return self._ghep_kpi("POST")
        if p == "/api/tep/tai-len":
            return self._tai_len_tep()
        if p == "/api/du-lieu/nhan-loai":   # [ghi chú kỹ thuật] ĐỌC THỬ file kéo vào trang Dữ liệu: loại gì, tháng nào, tới ngày nào — không gửi đi đâu, không AI
            import urllib.parse as _up
            import nhan_loai_file
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
                return self._json({"loi": "cần gửi thân nhị phân"}, 415)
            n = self.headers.get("Content-Length")
            if not n or not n.isdigit() or not 1 <= int(n) <= 20 * 1024 * 1024:
                return self._json({"loi": "File phải từ 1 byte tới 20 MB.", "viec_nen_lam": "Bạn tách file theo tháng (mỗi file một tháng) rồi kéo lại."}, 413)
            ten = dict(_up.parse_qsl(urlparse(self.path).query)).get("ten", "")[:200]
            try:
                r = nhan_loai_file.nhan(ten, self.rfile.read(int(n)))
            except ValueError as e:
                return self._json({"loi": str(e)[:400], "viec_nen_lam": str(e)[:400]}, 422)
            except Exception as e:  # noqa: BLE001 — file lạ làm bộ đọc vỡ ⇒ nói rõ, không 500 trần
                ghi(f"TRANG DỮ LIỆU: đọc thử file lỗi {type(e).__name__}")
                return self._json({"loi": "Hệ thống chưa đọc được file này (cấu trúc file lạ).", "viec_nen_lam": "Mở file bằng Excel rồi “Lưu thành” một file .xlsx mới và kéo lại."}, 422)
            ghi(f"TRANG DỮ LIỆU: đọc thử file ({n} byte) ⇒ {r['loai']} · kỳ {r['ky']} · ngày chốt {r['ngay_chot']}")   # không tên file
            return self._json(r)
        if p == "/api/tai-lieu/nap":   # [ghi chú kỹ thuật]
            return self._nap_tai_lieu()
        if p == "/api/tro-ly/tai-lieu":   # [ghi chú kỹ thuật] file kéo vào khung chat
            return self._tro_ly_tai_lieu()
        # Route JSON (chạy / duyệt / hỏi KPI…): đòi Content-Type application/json — trình duyệt buộc hỏi trước (preflight) với kiểu này, máy chủ không trả
        # CORS ⇒ yêu cầu khác nguồn không tới được đây. Thiếu rào này thì body JSON gửi dạng text/plain ("yêu cầu đơn giản") vẫn được _than() đọc.
        # Trang của mình luôn gửi application/json (web/src/api.ts:39).
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self._tu_choi("Content-Type phải là application/json")
        try:
            b = self._than()
            if p == "/api/blocks/refresh":
                return self._json({"blocks": lam_moi_danh_muc()})
            if p == "/api/check":
                return self._json({"kiem": kiem_chung(b["workflow"])[1]})
            if p == "/api/save":
                code, r = luu(b["workflow"])
                return self._json(r, code)
            if p == "/api/run":
                code, r = chay(b["workflow_id"], b.get("text_inputs"), b.get("khach"), b.get("data_version"))
                return self._json(r, code)
            if p == "/api/hoi-kpi":
                vt = _van_tay_kpi()   # [ghi chú kỹ thuật] khoá = câu + vân tay hộp KPI (dữ liệu + luật + phiên bản workflow ở phần 'dong')
                k_ctl = CTL.khoa(loi_vao="hoi-kpi", cau=str(b.get("cau") or ""), truoc=None, data_version=((vt or {}).get("dong") or {}).get("latest_data_version"),
                                 van_tay=vt, workflow_id="hoi_kpi.dung", so_phien_ban=((vt or {}).get("dong") or {}).get("workflow_version"), to_chuc=org()) if vt else None
                m_ctl = _cache_lay(k_ctl)
                if m_ctl:
                    return self._json({"session": m_ctl["session"], "tu_cache": True, "luc": m_ctl["luc"]}, 200)
                code, r = _hoi_kpi().bat_dau(str(b.get("cau") or ""))
                if k_ctl and code == 200 and r.get("session"):
                    KHO_CTL.ghi(k_ctl, r["session"])
                ghi(f"HỎI KPI ⇒ HTTP {code} {r.get('session') or r.get('loi')}")
                return self._json(r, code)
            if p == "/api/hoi-nhanh":   # [ghi chú kỹ thuật] chỉ nhận {cau}; CODE chọn mẫu theo tên + kiểm, rồi chạy bằng chay() (luật + giữ chỗ + trần lượt + sổ)
                if set(b) - {"cau", "truoc", "data_version"}:   # [ghi chú kỹ thuật] truoc = ≤ 3 câu HỎI trước; [ghi chú kỹ thuật] + bản dữ liệu
                    return self._json({"loi": "Chỉ nhận 'cau', 'truoc' và 'data_version'."}, 400)
                try:
                    dv_hn = ban_du_lieu.doc_ban(b.get("data_version"))   # kiểm dạng trước khi chọn mẫu; chay() kiểm lại đủ (ca V7)
                except ValueError as e:
                    return self._json({"loi": str(e)}, 400)
                o = org()
                ds_hn = lambda: [{"id": w["id"], "ten": w.get("ten")} for w in (xem.ds_workflow(None, 200, o) or [])]
                luat_hn = lambda pl: kiem_kpi_payload(pl, luc_chay=True) + luat_hoi_du_lieu.kiem(pl.get("nodes") or [], pl.get("edges") or [])[0]
                # [ghi chú kỹ thuật] mẫu chọn TẤT ĐỊNH, không theo câu (hoi_nhanh.chon_mau) ⇒ chọn trước để đưa mã + số phiên bản mẫu vào khoá
                vt, k_ctl = _van_tay_kpi(), None
                if vt:
                    try:
                        mau_ctl, _ = hoi_nhanh.chon_mau(ds_hn(), pb_moi_nhat, muc_toi_da.toi_da, luat_hn)
                    except Exception:  # noqa: BLE001
                        mau_ctl = None
                    if mau_ctl:
                        k_ctl = CTL.khoa(loi_vao="hoi-nhanh", cau=str(b.get("cau") or ""), truoc=b.get("truoc"),
                                         data_version=dv_hn or (vt.get("dong") or {}).get("latest_data_version"), van_tay=vt,
                                         workflow_id=mau_ctl["id"], so_phien_ban=mau_ctl.get("so"), to_chuc=o)
                m_ctl = _cache_lay(k_ctl)
                if m_ctl:   # cùng hình trả lời như lượt mới (session + workflow + ten) để trang mở đúng workflow cũ
                    return self._json({"session": m_ctl["session"], "workflow": mau_ctl["id"], "ten": mau_ctl.get("ten"), "tu_cache": True, "luc": m_ctl["luc"]}, 200)
                code, r = hoi_nhanh.bat_dau(
                    b.get("cau"), truoc=b.get("truoc"), ghi=ghi, pb=pb_moi_nhat, toi_da=muc_toi_da.toi_da,
                    chay=lambda m, ti: chay(m, ti, data_version=dv_hn),
                    ds=ds_hn, luat=luat_hn)
                if k_ctl and code == 200 and r.get("session"):
                    KHO_CTL.ghi(k_ctl, r["session"])
                return self._json(r, code)
            if p == "/api/kpi/bo-san":   # [ghi chú kỹ thuật] "Bộ KPI sẵn" (5 khối 5 dây) hoặc một khối dựng sẵn — chỉ DỰNG hình cho trang, không lưu, không chạy
                if set(b) - {"cau", "ma"} or not isinstance(b.get("ma"), (str, type(None))):
                    return self._json({"loi": "Chỉ nhận 'cau' và 'ma'."}, 400)
                code, r = khoi_kpi.bo_san(_hoi_kpi(), org(), b.get("cau"), b.get("ma"), lambda pl: sang_chung(tu_capitol(pl, DM)))
                return self._json(r, code)
            if p == "/api/du-lieu/tai-lieu/an":   # [ghi chú kỹ thuật] nút "Ẩn" một tài liệu khỏi danh sách của trang Dữ liệu — chỉ đánh dấu trong sổ của trang, KHÔNG xoá gì
                import trang_du_lieu as TDL
                sha_ = b.get("sha")
                if not SO_KHO.duong_mo(org(), sha_ if isinstance(sha_, str) else ""):   # chỉ tài liệu có trong sổ kho của ĐÚNG tổ chức
                    return self._json({"loi": "không có tài liệu này"}, 404)
                return self._json({"an": TDL.an_tai_lieu(GOC_DU_LIEU, org(), sha_, b.get("an") is not False)})
            if p == "/api/tro-ly/tai-lieu/mo-kho":   # [ghi chú kỹ thuật] nút "Hỏi trên tài liệu này": gắn tài liệu ĐÃ nạp kho vào cuộc trò chuyện — không nạp lại, không AI
                if set(b) - {"conv", "sha"}:
                    return self._json({"loi": "Chỉ nhận 'conv' và 'sha'."}, 400)
                sys.path.insert(0, str(THU_MUC.parent / "va_mot_o_chat" / "gan"))
                import noi_may_chu
                import trang_du_lieu as TDL
                o = org()
                code, r = noi_may_chu.mo_tai_lieu_kho(o, b.get("conv"), b.get("sha"), SO_KHO.ds(o), TDL.doc_an(GOC_DU_LIEU, o), **_kw_tro_ly(o))   # sổ kho của ĐÚNG tổ chức của yêu cầu
                return self._json(r, code)
            if p == "/api/tro-ly/tai-lieu/dong":   # [ghi chú kỹ thuật] nút × của thẻ "file đang mở" — chỉ nhận {conv}
                if set(b) - {"conv"}:
                    return self._json({"loi": "Chỉ nhận 'conv'."}, 400)
                sys.path.insert(0, str(THU_MUC.parent / "va_mot_o_chat" / "gan"))
                import noi_may_chu
                o = org()
                code, r = noi_may_chu.dong_tai_lieu(o, b.get("conv"), **_kw_tro_ly(o))
                return self._json(r, code)
            if p == "/api/tro-ly":   # [ghi chú kỹ thuật] Jev định tuyến + tự chạy < 0,20 + hỏi lại / hỏi tiền (phiếu một lần)
                if not (os.environ.get("BUOI_S5") and os.environ.get("TRAN_USD")):
                    return self._json({"loai": "loi", "chu": "Máy chủ chưa sẵn sàng, bạn thử lại sau nhé."}, 503)
                sys.path.insert(0, str(THU_MUC.parent / "va_mot_o_chat" / "gan"))
                import noi_may_chu
                o = org()
                code, r = noi_may_chu.xu_ly_route(b, org=o, **_kw_tro_ly(o))   # [ghi chú kỹ thuật] tham số gom về _kw_tro_ly (đường tải tài liệu dùng chung)
                return self._json(r, code)
            if p == "/api/chat":   # [ghi chú kỹ thuật] chỉ nhận {cau, conv}; hồ sơ Operator gắn cứng trong chat_operator.ho_so
                if not (os.environ.get("BUOI_S5") and os.environ.get("TRAN_USD")):
                    return self._json({"loi": "Máy chủ trang chưa đặt sổ tiền ngày (BUOI_S5 + TRAN_USD) — không gửi chat."}, 503)
                if set(b) - {"cau", "conv"}:
                    return self._json({"loi": "Chỉ nhận 'cau' và 'conv'."}, 400)
                o = org()
                code, r = chat_operator.gui(
                    b.get("cau"), b.get("conv"), org=o, goi_ab=goi_ab, H=_hoi_kpi(), ghi=ghi,
                    ds_wf=lambda: so_ban_luu(o),   # [ghi chú kỹ thuật] chỉ đếm bản LƯU (bản RUN của lượt chạy khác không được làm khoá đường chat)
                    kiem_wf=_kiem_wf_da_luu,
                    # [va_chat_chay C 12/11, D ĐẠT] Trợ lý ĐỀ NGHỊ (dòng cuối CHAY: <mã>) — trang tự chạy bằng chay() của nút Chạy (luật + giữ chỗ + trần lượt + sổ)
                    # [đo thật 14:38] bỏ workflow script đo tự lưu (tên "Mẫu Capitol - …", quy ước nhóm kỹ thuật) + chỉ đưa vào bảng workflow QUA luật lúc Chạy
                    tao_kpi=_tao_kpi_tu_chat,   # [ghi chú kỹ thuật]
                    chay_ho={"ds": lambda: [{"id": w["id"], "ten": w.get("ten")} for w in (xem.ds_workflow(None, 60, o) or [])
                                            if not str(w.get("ten") or "").startswith(("Mẫu Capitol - ", "ĐO — "))],   # tên script đo tự đặt (B: cũ + mới)
                             "luat": lambda p: kiem_kpi_payload(p, luc_chay=True) + luat_hoi_du_lieu.kiem(p.get("nodes") or [], p.get("edges") or [])[0],
                             "pb": pb_moi_nhat, "toi_da": muc_toi_da.toi_da, "chay": chay, "ket_qua": ket_qua_luot,
                             "trang_thai": lambda s: (xem.sql("select row_to_json(x) from (select status::text as tt from workflow_run "
                                                              f"where session_id='{kiem_ma(s)}' order by created_at desc limit 1) x") or {}).get("tt"),
                             "ten": lambda m: (xem.sql(f"select row_to_json(x) from (select name as ten from workflow where id='{kiem_ma(m)}') x") or {}).get("ten")})
                return self._json(r, code)
            if p in ("/api/chay-lai/xem-truoc", "/api/chay-lai"):   # [ghi chú kỹ thuật] HOP_DONG_API.md cạnh file này
                code, r = CL.chuan_bi(b, nguon=_luot_nguon, ban_moi=pb_moi_nhat, luu_nguon=_khoa_luu, la_kpi=_la_kpi, toi_da=muc_toi_da.toi_da,
                                    cung_to_chuc=_cung_to_chuc)
                if code != 200:
                    ghi(f"CHẠY LẠI TỪ KHỐI bị chặn ({p}): HTTP {code} — {str(r.get('loi'))[:160]}")
                    return self._json(r, code)
                if p == "/api/chay-lai/xem-truoc":   # 0 đồng: không tạo phiên, không giữ chỗ
                    return self._json({k: x for k, x in r.items() if k != "payload"})
                code, r2 = chay(r["workflow_id"], chay_lai=r)
                return self._json(r2, code)
            if p == "/api/approve":
                # [ghi chú kỹ thuật] thiếu / lạ ô decision ⇒ 400 — rỗng KHÔNG phải duyệt (trước: mặc định "dong_y")
                code, r = duyet(b["session"], b["node"], b.get("decision"))
                if code == 200:
                    _cache_xoa("duyệt / từ chối bản tính")   # [ghi chú kỹ thuật] trạng thái duyệt đổi ⇒ câu "đã duyệt chưa" đổi câu trả lời
                return self._json(r, code)
            return self._json({"loi": "không có đường này"}, 404)
        except (ValueError, KeyError, LookupError) as e:
            return self._json({"loi": f"{type(e).__name__}: {e}"}, 400)
        except Exception as e:
            return self._json({"loi": f"{type(e).__name__}: {e}"}, 503)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Trang kéo–thả workflow: cổng {CONG}. ĐƯỜNG MỞ TRANG (có mã, mở MỘT LẦN mỗi trình duyệt) nằm trong file: {tao_ma_mo()}   Ctrl+C để tắt")
    ThreadingHTTPServer(("127.0.0.1", CONG), XuLy).serve_forever()
