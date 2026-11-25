"""S5 — lượt AI THẬT cho công cụ mới (06/11/2026, A; người vận hành "Duyệt cả 5", trần 0,10 USD). TỐN TIỀN (gpt-6-luna, khoá User).
Chạy:  DA_DUYET=co CA=L1,L2,L3 python3 -I chay_thu/sandbox/chay_s5.py        Kết quả: ket_qua/06_11_sang/s5/
  L1 — chạy code: khối Agent có run_python trên don_hang_gia.xlsx; đáp án tính bằng openpyxl TRONG hộp worker (không qua AI).
  L2 — SQL: khách KH-LAN hỏi "đơn của tôi tổng bao nhiêu tiền" ⇒ phải có 696.000 và AI gọi sql_query (dùng sum, không tự cộng).
  L3 — file đẹp: khối Agent bật make_pdf + make_pptx, dữ liệu "thuế quan EU" đưa sẵn trong câu (không tra web) ⇒ file soát bằng soat.py của B.
Trần tiền BẰNG CODE: sổ ket_qua/06_11_sang/s5/so_tien.json cộng dồn mọi lượt S5 (kể cả Operator, giữ kín v2 — ghi qua ghi_tien());
trước mỗi lượt: đã tiêu ≥ TRAN thì dừng. Giá gpt-6-luna từ giao_dien/bang_gia.json; token đọc đệm tính như token vào thường (cận trên).
"""
import fcntl
import re
import json
import os
import threading
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from buoc4b_gui_workflow_thu import KQ, ghi, goi
from buoc6b_chay_lai_tu_khoi import psql
from buoc15_workflow_nhieu_khoi import Ve
from sandbox.thu_s4_workflow import luu

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "giao_dien"))   # [E minor 16/11] một lần ở đầu file, không mỗi lần gọi
import muc_toi_da as MT  # noqa: E402 — bảng mức của trang (cùng file trang giữ chỗ)


def psql_chac(sql: str) -> str:
    """[E M1 16/11] psql cho TIỀN: lỗi thì NÉM. psql chung (buoc6b:29) chỉ trả stdout ⇒ Postgres báo ERROR / hộp không chạy ⇒ "" ⇒ người gọi
    `or "[]"` thành [] ⇒ khối tính 0 lặng lẽ ⇒ trả chỗ giữ. Ở đây: mã thoát ≠ 0 (psql -c lỗi SQL trả 1; docker exec lỗi cũng ≠ 0) hoặc
    stderr có ERROR / FATAL ⇒ RuntimeError ⇒ người gọi không ghi sổ ⇒ chỗ giữ còn chặn (đóng khi lỗi). Không sửa psql chung (nhiều script dùng)."""
    r = subprocess.run(["docker", "exec", "capitol-postgres", "psql", "-U", "capitol", "-d", "agentic", "-tA", "-v", "ON_ERROR_STOP=1",
                        "-c", sql], capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or "ERROR" in r.stderr or "FATAL" in r.stderr:
        raise RuntimeError(f"tien_luot: psql lỗi (mã {r.returncode}): {r.stderr.strip()[:200]} — không ghi sổ (đóng khi lỗi)")
    return r.stdout.strip()


def _json_chac(sql: str):
    """Truy vấn trả MỘT giá trị JSON (json_agg có coalesce '[]'). Rỗng ⇒ NÉM (truy vấn đúng luôn trả ít nhất '[]' / payload)."""
    ra = psql_chac(sql)
    if not ra:
        raise RuntimeError("tien_luot: truy vấn trả rỗng (mong JSON) — không ghi sổ (đóng khi lỗi)")
    return json.loads(ra)

BUOI = KQ / os.environ.get("BUOI_S5", "06_11_sang/s5")   # [16:13 07/11, A] chọn thư mục buổi để không đè bằng chứng 06/11 (sổ D10); mặc định như cũ
SO = BUOI / "so_tien.json"
TRAN_TIEN_TRINH = float(os.environ.get("TRAN_USD", "0.10"))   # trần TIẾN TRÌNH tự khai — KHÔNG còn là trần cuối
# [ghi chú] trước: giu_cho so với TRAN của chính tiến trình gọi + mỗi lần ghi sổ ĐÈ tran_usd ⇒ một script bật TRAN_USD cao
# hơn là vượt trần ngày User đặt. Nay: trần hiệu lực = min(TRAN_USD tiến trình, trần NGÀY). Trần ngày đọc từ MỘT nguồn do A ghi số User nói:
# <thư mục nhà thật (pwd, không theo $HOME)>/$CAPITOL_STATE_DIR/tran_ngay/<YYYY-MM-DD>.txt (ngày = ngày của sổ BUOI_S5 dạng dd_mm/…, không có thì hôm nay).
# File có mà đọc không ra số ≥ 0 ⇒ trần 0 (đóng). Thiếu file ⇒ ĐÓNG ở TRAN_THIEU_FILE = 0,10 (mặc định bản gốc), hạ thêm theo mức thấp nhất
#   từng ghi trong sổ ngày [E MAJOR 17/11: bản trước "thiếu file + thiếu sổ ⇒ trần tiến trình" ⇒ tiến trình ĐẦU TIÊN của ngày tự đặt trần cả ngày].
#   A ghi file theo số User nói lúc mở phiên (HUONG_DAN_BAT_DEMO).
# Sổ KHÔNG BAO GIỜ ghi tăng: tran_usd + tran_thap_nhat chỉ giữ nguyên hoặc hạ. Đọc lại mỗi lần giu_cho / ghi sổ ⇒ A hạ trần giữa ngày có tác dụng ngay.
import pwd  # noqa: E402
TRAN_NGAY_DIR = Path(os.environ.get("CAPITOL_STATE_DIR") or (Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "share" / "capitol-platform")).expanduser() / "tran_ngay"


TRAN_THIEU_FILE = 0.10


def _ngay_so() -> str:
    m = re.match(r"(\d{2})_(\d{2})(?:_|/|$)", os.environ.get("BUOI_S5", ""))
    if not m:
        return time.strftime("%Y-%m-%d")
    nam = int(time.strftime("%Y"))
    if (m.group(2), m.group(1)) > (time.strftime("%m"), time.strftime("%d")) and m.group(2) == "12" and time.strftime("%m") == "01":
        nam -= 1   # [E minor 1] sổ 31_12 đọc vào ngày 01/01 ⇒ năm trước
    return f"{nam}-{m.group(2)}-{m.group(1)}"


def _so_thap_nhat(d: dict | None) -> float | None:
    if not d:
        return None
    v = [float(d[k]) for k in ("tran_usd", "tran_thap_nhat") if isinstance(d.get(k), (int, float))]
    return min(v) if v else None


def tran_ngay() -> tuple[float | None, str]:
    """(trần ngày, nguồn). Luôn có số: file ⇒ số trong file; thiếu file ⇒ ≤ 0,10 (đóng)."""
    f = TRAN_NGAY_DIR / f"{_ngay_so()}.txt"
    if f.exists():
        try:
            dong = [x.split("#", 1)[0].strip() for x in f.read_text(encoding="utf-8").splitlines()]
            v = float(next(x for x in dong if x).replace(",", "."))
            if not (v >= 0 and v == v and v != float("inf")):
                raise ValueError
            return v, f"file {f.name}"
        except Exception:
            return 0.0, f"file {f.name} HỎNG ⇒ 0 (đóng)"
    try:
        v = _so_thap_nhat(json.loads(SO.read_text(encoding="utf-8")) if SO.is_file() else None)
    except Exception:
        return 0.0, "sổ HỎNG ⇒ 0 (đóng)"
    if v is not None and v < TRAN_THIEU_FILE:
        return v, "thiếu file ⇒ thấp nhất trong sổ ngày (< 0,10)"
    return TRAN_THIEU_FILE, "thiếu file ⇒ ĐÓNG 0,10"


def tran_hieu_luc() -> float:
    n, _ = tran_ngay()
    return TRAN_TIEN_TRINH if n is None else min(TRAN_TIEN_TRINH, n)


TRAN = tran_hieu_luc()   # giá trị lúc nạp — cho các script cũ đọc S5.TRAN; phép chặn thật (giu_cho) đọc lại tran_hieu_luc() mỗi lần
GIA = json.loads((Path(__file__).resolve().parent.parent / "giao_dien" / "bang_gia.json").read_text())
CHUNG = {"temperature": None, "max_tokens": 4000, "timeout": 180, "retries": 1, "model": "gpt-6-luna"}


def _doc_so() -> dict:
    return json.loads(SO.read_text(encoding="utf-8")) if SO.is_file() else {"tong_usd": 0.0, "luot": []}   # [ghi chú] sổ mới chưa mang trần: dòng đầu ghi trần hiệu lực lúc ghi


def da_tieu() -> float:
    """Tổng đã tiêu = TỔNG CÁC DÒNG của sổ (không dùng ô tong_usd cộng dồn — cộng dồn làm tròn từng bước lệch thấp ~0,000007 và lệch hẳn nếu ai sửa tay
    một dòng). Đọc không cần khoá: sổ được thay bằng os.replace (nguyên tử) ⇒ luôn đọc được một bản trọn vẹn."""
    return round(sum(float(x.get("usd") or 0) for x in _doc_so()["luot"]), 6)


# ---------- KHOÁ CHUNG + BẢNG GIỮ CHỖ DÙNG CHUNG [ghi chú] ----------
# Vì sao: sổ chỉ cộng khi lượt XONG; "giữ chỗ" trước đây nằm trong bộ nhớ từng tiến trình (hoi_kpi._DANG_GIU của trang; script đo chỉ so rồi chạy) ⇒ trang và
# script chạy cùng lúc không thấy khoản giữ của nhau, có thể cùng qua phép "đã tiêu + đang giữ + cần ≤ trần". Nay: MỘT file giu_cho.json cạnh sổ
# {mã: {usd, pid, luc, nhan, session}}; mọi đọc – sửa – ghi (sổ lẫn bảng giữ) nằm trong CÙNG khoá flock so_tien.json.lock ⇒ phép kiểm + thêm dòng là một bước.
# [D S1] Dòng giữ QUÁ HẠN không bị xoá trắng: nó là một lượt có thể ĐÃ tiêu tiền mà không ai còn ghi sổ ⇒ khi dọn, CHUYỂN thành một dòng sổ cận trên
#   (nhãn NHAN_MO_COI, usd = khoản giữ) rồi mới bỏ khỏi bảng — cùng vùng khoá. Ghi dư nếu lượt thật ra không chạy: chấp nhận (thà dư).
#   Hạn: pid CÒN SỐNG ⇒ GIU_SONG_GIAY (3 giờ 10 phút; trang theo dõi một lượt tối đa 3 giờ); pid ĐÃ CHẾT ⇒ GIU_CHET_GIAY (30 phút — lượt có thể vẫn đang
#   chạy trong Capitol); `luc` ở TƯƠNG LAI quá 5 phút (đồng hồ WSL nhảy) ⇒ coi như vừa giữ.
# [D S2] KHÔNG có atexit trả chỗ: chỉ trả SAU khi ghi_tien của chính lượt đó thành công (ghi_tien(..., ma_giu=…) ghi + trả trong MỘT vùng khoá, hoặc người
#   gọi tra_cho sau khi ghi). Tiến trình chết giữa lượt ⇒ dòng giữ ở lại ⇒ thành mồ côi ⇒ S1.
# [D S3] dòng giữ mang `session` khi người gọi biết ⇒ dòng sổ mồ côi ghi được session (kiểm chéo sổ ↔ PG; thấy ngay nếu về sau có dòng thật cùng session).
# File sổ / bảng giữ hỏng ⇒ ném lỗi (đóng). Chờ khoá: ghi sổ chờ KHÔNG trần (phải ghi được); giu_cho chờ tối đa `cho_giay` rồi từ chối (không treo trang).
GIU = BUOI / "giu_cho.json"
GIU_SONG_GIAY = int(os.environ.get("GIU_SONG_GIAY", str(3 * 3600 + 600)))
GIU_CHET_GIAY = int(os.environ.get("GIU_CHET_GIAY", str(30 * 60)))
NHAN_MO_COI = "mo_coi_giu_cho"
_LY_DO = threading.local()   # lý do lần giu_cho gần nhất CỦA LUỒNG NÀY bị từ chối — xem ly_do_tu_choi()
_MA_CHO_GHI: list[str] = []   # mã giữ của TIẾN TRÌNH NÀY do du_tien() (script đo, chạy tuần tự) tạo, chờ lần ghi_tien kế để ghi + trả cùng lúc


class _KhoaSo:
    """Khoá flock chung (sổ + bảng giữ). KHÔNG lồng nhau: hai fd của cùng tiến trình cũng chặn nhau. cho_giay=None ⇒ chờ không trần."""
    def __init__(self, cho_giay: float | None = None):
        self.cho_giay = cho_giay

    def __enter__(self):
        SO.parent.mkdir(parents=True, exist_ok=True)   # 11/11 (B bắt): ngày mới chưa có thư mục sổ ⇒ lượt đầu tiêu tiền rồi mới lỗi ghi sổ
        self.f = open(SO.with_name(SO.name + ".lock"), "a")
        if self.cho_giay is None:
            fcntl.flock(self.f, fcntl.LOCK_EX)
            return self
        het = time.monotonic() + self.cho_giay
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= het:
                    self.f.close()
                    raise TimeoutError("chờ khoá sổ tiền quá lâu") from None
                time.sleep(0.05)

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def _ghi_tep(duong: Path, d) -> None:
    tam = duong.with_name(f"{duong.name}.tam_{os.getpid()}")
    tam.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tam, duong)
    if duong in (SO, GIU):   # [ghi chú] mọi lần ghi sổ / bảng giữ đều trong _KhoaSo ⇒ bản tóm tắt cho cổng ghi trong CÙNG khoá
        _ghi_tom_tat()


# [ghi chú] Cổng AI (capitol-cong-ai) cộng tiền chay_s5 vào trần ngày. Trước: cổng mount CẢ ket_qua/ và chỉ
# đọc `<dd_mm>/s5` ⇒ thấy dữ liệu thử thừa + BUOI_S5 tên khác (06_11_sang/s5…) bị cộng thiếu. Nay: chay_s5 ghi MỘT tệp số / buổi vào ket_qua/so_cong_doc/,
# cổng chỉ mount thư mục đó, cộng mọi tệp cập nhật hôm nay / hôm qua. Chỉ số — không nhãn, không chi tiết lượt. Ghi hỏng ⇒ NÉM (đóng khi lỗi).
SO_CONG_DOC = KQ / "so_cong_doc"


def _ten_tom_tat() -> str | None:
    """Tên theo THƯ MỤC CHỨA SỔ (không theo BUOI): script thử đặt S5.SO sang thư mục tạm ⇒ sổ không nằm trong ket_qua/ ⇒ None ⇒ KHÔNG ghi
    (nếu ghi sẽ ĐÈ số thật của buổi bằng số thử ⇒ cổng cộng thiếu)."""
    try:
        r = SO.resolve().parent.relative_to(KQ.resolve())
    except ValueError:
        return None
    return str(r).replace("/", "__") + ".json"


def _ghi_tom_tat(ngay: str | None = None) -> None:
    """CHỈ gọi khi đang giữ _KhoaSo. tong_usd = tổng các dòng sổ; dang_giu_usd = MỌI dòng giữ (kể cả quá hạn chưa dọn — thà dư)."""
    ten = _ten_tom_tat()
    if ten is None:
        return
    giu = _doc_giu()
    d = {"buoi": str(SO.parent), "tong_usd": da_tieu(), "dang_giu_usd": round(sum(float(x.get("usd") or 0) for x in giu.values()), 6),
         "so_dong_giu": len(giu), "ngay_cap_nhat": ngay or time.strftime("%Y-%m-%d"), "luc": time.strftime("%H:%M:%S %d/%m/%Y")}
    SO_CONG_DOC.mkdir(parents=True, exist_ok=True)
    moc = SO_CONG_DOC / "DA_GAN_CG2"   # cổng chỉ tin thư mục khi có mốc này (thư mục rỗng do docker tự tạo ⇒ cổng chặn)
    if not moc.is_file():
        moc.write_text("chay_s5 ghi bản tóm tắt sổ cho cổng AI (CG-2)\n", encoding="utf-8")
    dich = SO_CONG_DOC / ten
    tam = dich.with_name(f"{dich.name}.tam_{os.getpid()}")
    tam.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tam, dich)


def lam_moi_tom_tat(ngay: str | None = None) -> dict:
    """Ghi lại bản tóm tắt của buổi hiện tại (dưới khoá) — gan.sh gọi một lần khi gắn để cổng có số ngay, không chờ lượt kế.
    `ngay` = ngày của sổ (sổ hôm qua giữ ngày hôm qua ⇒ cổng không cộng nó sang ngày mai)."""
    ten = _ten_tom_tat()
    if ten is None:
        raise RuntimeError(f"sổ {SO} không nằm trong {KQ} — không có bản tóm tắt cho cổng")
    with _KhoaSo():
        _ghi_tom_tat(ngay)
    return json.loads((SO_CONG_DOC / ten).read_text(encoding="utf-8"))


def _them_dong_so(nhan: str, usd: float, chi_tiet: dict) -> float:
    """Thêm MỘT dòng sổ — CHỈ gọi khi đang giữ _KhoaSo."""
    d = _doc_so()
    d["luot"].append({"nhan": nhan, "usd": round(usd, 6), "gio": time.strftime("%H:%M:%S %d/%m/%Y"), **chi_tiet})
    d["tong_usd"] = round(sum(float(x.get("usd") or 0) for x in d["luot"]), 6)
    # [ghi chú] ô tran_usd / tran_thap_nhat là trần
    # NGÀY (file A ghi; thiếu file ⇒ tran_ngay() đóng ≤ 0,10) — KHÔNG ghi trần TIẾN TRÌNH (TRAN_USD) vào đây; trần tiến trình vẫn chặn chính nó ở giu_cho.
    t, _ = tran_ngay()
    cu_min = _so_thap_nhat(d)
    t = t if cu_min is None else min(t, cu_min)   # KHÔNG BAO GIỜ ghi tăng
    d["tran_usd"] = t
    d["tran_thap_nhat"] = t
    _ghi_tep(SO, d)
    return d["tong_usd"]


def _pid_song(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError, TypeError, OverflowError):
        return True   # không chắc ⇒ coi như còn sống (chặt hơn)


def _doc_giu() -> dict:
    return json.loads(GIU.read_text(encoding="utf-8")) if GIU.is_file() else {}


def _con_hieu_luc(x: dict, bay_gio: float) -> bool:
    tuoi = bay_gio - float(x.get("luc") or 0)
    if tuoi < -300:            # `luc` ở tương lai quá 5 phút: đồng hồ nhảy ⇒ coi như vừa giữ
        return True
    return tuoi <= (GIU_SONG_GIAY if _pid_song(x.get("pid")) else GIU_CHET_GIAY)


def _don_mo_coi(d: dict, bay_gio: float) -> dict:
    """CHỈ gọi khi đang giữ _KhoaSo. Dòng quá hạn ⇒ thành dòng sổ cận trên [D S1] rồi bỏ khỏi bảng. Trả bảng còn lại (đã ghi file nếu có đổi)."""
    het = {m: x for m, x in d.items() if not _con_hieu_luc(x, bay_gio)}
    if not het:
        return d
    for m, x in het.items():
        ct = {"ghi_chu": f"giữ chỗ quá hạn, CHƯA thấy ghi sổ ⇒ ghi cận trên bằng khoản giữ (nhãn gốc {x.get('nhan')!r}, pid {x.get('pid')}, "
                         f"giữ lúc {time.strftime('%H:%M:%S %d/%m/%Y', time.localtime(float(x.get('luc') or 0)))}, mã giữ {m[:8]}) — người vận hành đối chiếu"}
        if x.get("session"):
            ct["session"] = x["session"]
        _them_dong_so(NHAN_MO_COI, float(x.get("usd") or 0), ct)
    con = {m: x for m, x in d.items() if m not in het}
    _ghi_tep(GIU, con)
    return con


def dang_giu() -> dict:
    """Các dòng giữ chỗ còn hiệu lực (đọc không khoá, KHÔNG dọn). Chỉ để xem / thử."""
    bay_gio = time.time()
    return {m: x for m, x in _doc_giu().items() if _con_hieu_luc(x, bay_gio)}


def giu_cho(can: float, nhan: str = "", session: str | None = None, cho_giay: float | None = 5.0) -> tuple[str | None, float]:
    """Kiểm + giữ trong MỘT vùng khoá chung mọi tiến trình: tổng các dòng sổ + tổng đang giữ + can ≤ TRAN ⇒ (mã, tổng trước); không đủ ⇒ (None, tổng trước).
    Trần = tran_hieu_luc() = min(TRAN_USD tiến trình, trần ngày) [ghi chú]. Không lấy được khoá trong `cho_giay` ⇒ (None, tổng đọc không khoá) — từ chối, không treo người gọi."""
    import uuid
    _LY_DO.gia_tri = None
    try:
        with _KhoaSo(cho_giay):
            bay_gio = time.time()
            d = _don_mo_coi(_doc_giu(), bay_gio)
            tong = round(da_tieu() + sum(float(x["usd"]) for x in d.values()), 6)
            if tong + can > tran_hieu_luc():   # [ghi chú] min(trần tiến trình, trần ngày) — đọc lại mỗi lần
                _LY_DO.gia_tri = "het_tran"
                return None, tong
            ma = uuid.uuid4().hex
            d[ma] = {"usd": round(float(can), 6), "pid": os.getpid(), "luc": round(bay_gio, 1), "nhan": str(nhan)[:80]}
            if session:
                d[ma]["session"] = str(session)[:64]
            _ghi_tep(GIU, d)
            return ma, tong
    except TimeoutError:
        _LY_DO.gia_tri = "so_ban"
        return None, round(da_tieu() + sum(float(x.get("usd") or 0) for x in dang_giu().values()), 6)


def ly_do_tu_choi() -> str | None:
    """[C yêu cầu 12/11] Lần giu_cho gần nhất của LUỒNG ĐANG GỌI: None = giữ được (hoặc chưa gọi) · "het_tran" = đã tiêu + đang giữ + cần > trần ·
    "so_ban" = không lấy được khoá sổ trong thời gian chờ (bên khác đang ghi) ⇒ người gọi báo "sổ đang bận, thử lại", không báo "sắp vượt trần"."""
    return getattr(_LY_DO, "gia_tri", None)


def tra_cho(ma: str | None) -> None:
    """Bỏ dòng giữ: SAU khi lượt đã ghi sổ, hoặc khi chắc chắn lượt không chạy (Capitol từ chối trước run-workflow). Mã lạ / đã dọn ⇒ không làm gì."""
    if not ma:
        return
    with _KhoaSo():
        d = _doc_giu()
        if d.pop(ma, None) is not None:
            _ghi_tep(GIU, d)
    if ma in _MA_CHO_GHI:
        _MA_CHO_GHI.remove(ma)


def gan_session(ma: str | None, session: str) -> None:
    """[D S3] ghi mã phiên vào dòng giữ khi người gọi biết sau lúc giữ (script đo: phiên tạo trong luu_va_chay)."""
    if not ma or not session:
        return
    with _KhoaSo():
        d = _doc_giu()
        if ma in d:
            d[ma]["session"] = str(session)[:64]
            _ghi_tep(GIU, d)


def ghi_tien(nhan: str, usd: float, chi_tiet: dict, ma_giu: str | None = None) -> float:
    """Thêm một dòng sổ dưới khoá chung (trang 18400 và các script đo là CÁC TIẾN TRÌNH khác nhau cùng ghi một sổ — bản cũ không khoá ⇒ có thể mất dòng).
    `ma_giu` (hoặc mã du_tien() của tiến trình này đang chờ) ⇒ GHI + TRẢ CHỖ trong cùng vùng khoá [D S2: chỉ trả sau khi ghi thành công]."""
    with _KhoaSo():
        tong = _them_dong_so(nhan, usd, chi_tiet)
        tra = [m for m in ([ma_giu] if ma_giu else []) + list(_MA_CHO_GHI) if m]
        if tra:
            d = _doc_giu()
            if any(d.pop(m, None) is not None for m in tra):
                _ghi_tep(GIU, d)
            _MA_CHO_GHI.clear()
        return tong


def du_tien_giu(can: float, nhan: str) -> bool:
    """Cho SCRIPT ĐO chạy tuần tự (chay_kpi.du_tien gọi): giữ `can` trong bảng chung; mã chờ lần ghi_tien kế của tiến trình này để ghi + trả cùng lúc.
    Lượt trước chưa ghi sổ (lỗi giữa chừng) ⇒ dòng giữ cũ ĐỂ NGUYÊN (không trả) — sẽ thành mồ côi ⇒ dòng sổ cận trên."""
    ma, tong = giu_cho(can, nhan, cho_giay=30.0)
    if not ma:
        print(f"DỪNG: đã tiêu + đang giữ {tong:.4f} + giữ chỗ tối đa {can:.4f} > trần {tran_hieu_luc()} (hoặc không lấy được khoá sổ)")
        return False
    _MA_CHO_GHI.clear()
    _MA_CHO_GHI.append(ma)
    print(f"Giữ chỗ tối đa {can:.4f} USD (đã tiêu + đang giữ trước đó {tong:.4f} / {tran_hieu_luc()})")
    return True


def tien_luot(s: str) -> dict:
    """Token từng lần gọi AI của lượt (node_result.metadata.message_history, kind=response) — chỉ số, không nguyên văn."""
    ds = _json_chac(f"""select coalesce(json_agg(json_build_object('model', m->>'model_name',
            'in', (m->'usage'->>'input_tokens')::int, 'out', (m->'usage'->>'output_tokens')::int,
            'cache', coalesce((m->'usage'->>'cache_read_tokens')::int, 0),
            'cache_w', coalesce((m->'usage'->>'cache_write_tokens')::int, 0),
            'tools', (select coalesce(json_agg(pp->>'tool_name'), '[]') from jsonb_array_elements(m->'parts') pp where pp->>'part_kind'='tool-call'))), '[]')
        from node_result nr join workflow_run r on r.id=nr.run_id,
             jsonb_array_elements(coalesce(nr.output_data::jsonb->'metadata'->'message_history', '[]'::jsonb)) m
        where r.session_id='{s}' and m->>'kind'='response' and nr.cached_from_run_id is null""")   # [E M1] truy vấn chính cũng NÉM khi lỗi
    # [ghi chú] dòng DÙNG LẠI (cached_from_run_id) chép cả message_history của phiên
    # nguồn ⇒ không bỏ thì tính tiền khối dùng lại lần nữa (0 lời gọi AI thật trong lượt mới). Bỏ ở cả hai truy vấn.
    usd = 0.0
    # [ghi chú] Lời gọi của mô hình thuộc nhà cung cấp BEDROCK (tra bảng định tuyến của cổng):
    # KHÔNG cộng vào usd (usd = phần TÍNH TRẦN); cộng riêng token + tiền theo giá bảng vào ô THAM KHẢO (sổ cổng mới là tiền thật). Mô hình KHÔNG có trong
    # bang_gia.json lẫn bảng định tuyến ⇒ NÉM (trước: lặng lẽ tính theo giá luna ⇒ Sonnet sẽ bị ghi thiếu ~20 lần). Cờ CHAY_THU_TIEN_BEDROCK=tat ⇒ y như cũ.
    br_vao = br_ra = 0
    br_usd = 0.0
    for x in ds:
        day_du = x["model"] or ""
        ten = day_du.split(":")[-1]   # bỏ tiền tố nhà cung cấp kiểu "openai-responses:gpt-6-luna"
        # tên Bedrock có thể CHỨA dấu ":" ở đuôi phiên bản ("…-v1:0") ⇒ tra TÊN ĐẦY ĐỦ trước, rồi mới tới phần sau dấu ":" cuối
        m = (_mo_hinh_bang(day_du) or _mo_hinh_bang(ten)) if MT.TIEN_BEDROCK else None
        if m and m["ncc"] == MT.NCC_BEDROCK:
            br_vao += (x["in"] or 0); br_ra += (x["out"] or 0)
            # [ghi chú] token vào của lịch sử GỒM cả phần đọc + ghi bộ nhớ đệm;
            # giá đọc rẻ hơn ~20 lần, giá ghi đắt hơn 1,25 lần ⇒ tính từng phần theo giá bảng (như cổng tính) thay vì mọi token × giá vào.
            br_usd += _usd_bedrock(m, (x["in"] or 0), (x["out"] or 0), x.get("cache") or 0, x.get("cache_w") or 0)
            continue
        g = GIA.get(ten)
        if g is None and m:
            g = {"input": m["gia_vao"], "output": m["gia_ra"]}
        if g is None:
            if MT.TIEN_BEDROCK:
                raise ValueError(f"tien_luot: mô hình '{day_du[:80]}' của lượt {s[:8]} không có trong bang_gia.json lẫn bảng định tuyến — không ghi sổ (đóng khi lỗi)")
            g = GIA["gpt-6-luna"]   # cờ tắt: cách tính cũ
        usd += ((x["in"] or 0) * g["input"] + (x["out"] or 0) * g["output"]) / 1_000_000
    cong_cu = [t for x in ds for t in x["tools"]]
    # [ghi chú] Khối gọi AI mà kết quả KHÔNG có lịch sử
    # (FAILED; hoặc loại khối vốn không lưu lịch sử: nghiên cứu sâu agentic, Rào AI, chấm điểm, che, đồ thị, hộp phiên…) ⇒ token không đếm được
    # ⇒ cộng MỨC TỐI ĐA của khối (muc_toi_da._muc_khoi — cùng bảng trang giữ chỗ). Trước đây những khối này tính 0 và ghi_tien_mot_lan bỏ
    # qua dòng khi usd = 0 ⇒ 3 lượt 16/11 không có dòng sổ nào (ghi bù B_ghi_bu_failed 0,1842).
    bu = _muc_khoi_khong_lich_su(s)
    bu = _thay_bang_cache_usage(s, bu)   # [ghi chú] khối có dòng [CACHE_USAGE] khớp ⇒ tiền ĐO thay mức tối đa
    bu = _thay_dr_bang_dem_cong_cu(s, bu)   # [ghi chú] tra cứu sâu thành công: tiền tra web theo số lần gọi công cụ THẬT (dòng log [DR_CONG_CU])
    usd += sum(m for _, _, m in bu)
    # [va_tien_bedrock] khối Bedrock KHÔNG có lịch sử (FAILED…): phần tính trần ở trên chỉ còn phần ngoài mô hình (MT._muc_khoi); phần mô hình không đếm
    # được token ⇒ ô tham khảo cộng MỨC Bedrock ước của khối (thà dư).
    br_kls = _thay_bedrock_bang_cache_usage(s, _muc_bedrock_khong_lich_su(s))   # [ghi chú] số ĐO thay mức ước khi có dòng [CACHE_USAGE] khớp
    br_usd += sum(b for _, _, b, _, _ in br_kls)
    br_vao += sum(v for _, _, _, v, _ in br_kls); br_ra += sum(r for _, _, _, _, r in br_kls)
    # [E M2] công cụ Exa (EXA_*) trong lịch sử các khối CÓ lịch sử ⇒ cộng mức giữ chỗ / lần của hộp Exa (va_exa/may_chu.py:31) vào sổ NGÀY
    # (đã chốt: tiền Exa tính vào trần ngày chung; sổ Exa riêng vẫn chặn thêm). Khối không lịch sử đã gồm Exa trong mức tối đa.
    so_exa = sum(1 for t in cong_cu if isinstance(t, str) and t.upper().startswith("EXA_"))
    usd += so_exa * EXA_MOT_LAN
    # [đã giao 16/11 — E báo khối tra web CDD đi Serper, không vào sổ ngày] công cụ web_search của mình = Serper (native_tools.py: SERPER_URL
    # google.serper.dev/search; hộp worker có SERPER_API_KEY) ⇒ đếm tool-call "web_search" × giá / lần theo MỨC TỐI ĐA (dưới).
    so_serper = sum(1 for t in cong_cu if t == "web_search")
    usd += so_serper * SERPER_MOT_LAN
    # [ghi chú] công cụ rag_search = app RAG mekongai tạo vector câu hỏi qua OpenRouter (qwen3-embedding-4b 0,02 USD / 1 triệu token —
    # bang_gia.json) ⇒ ngoài sổ luna ⇒ đếm tool-call × RAG_MOT_LAN (cận trên: câu ≤ 500 ký tự ≤ ~1.000 token ⇒ ≤ 0,00002; lấy 0,0001).
    so_rag = sum(1 for t in cong_cu if t == "rag_search")
    usd += so_rag * RAG_MOT_LAN
    # [ghi chú] message_history chỉ giữ LẦN THỬ CUỐI ⇒ khối Agent bị Temporal thử lại
    # (tối đa THU_LAI_TOI_DA lần) thì tiền các lần trước không có trong usd ở trên. Lượt có dấu thử lại / thiếu sự kiện (_co_thu_lai) ⇒ sàn:
    # MỌI khối AI của lượt × mức tối đa × THU_LAI_TOI_DA (+ công cụ đã đếm). Lấy số lớn hơn (đo, sàn). Không dấu ⇒ như cũ.
    so_ci = sum(1 for l, _, _ in _muc_khoi_khong_lich_su(s, ca_co_lich_su=True) if l == E2B_KHOI) if E2B_DAU_GAN.exists() else 0
    # [ghi chú] khối chạy code KHÔNG lịch sử (FAILED…) đã cộng ĐỦ mức khối ở `bu` (mức GỒM
    #   E2B_TOI_DA) ⇒ bỏ khỏi so_ci, kẻo _e2b_bu_thieu cộng E2B lần 2. Khối thay bằng tiền ĐO (CACHE_USAGE, mức < tối đa) ⇒ vẫn đếm.
    so_ci = max(0, so_ci - sum(1 for l, _, m in bu if l == E2B_KHOI and m >= (MT.MUC_TOI_DA.get(E2B_KHOI) or 0)))
    e2b_usd, e2b_may = tien_e2b(s, so_ci)   # [ghi chú] máy E2B không có trong message_history ⇒ cộng riêng (đo theo sổ máy)
    usd += e2b_usd
    thu_lai = _co_thu_lai(s)
    if thu_lai:
        san = sum(m for _, _, m in _muc_khoi_khong_lich_su(s, ca_co_lich_su=True)) * THU_LAI_TOI_DA \
            + so_exa * EXA_MOT_LAN + so_serper * SERPER_MOT_LAN + so_rag * RAG_MOT_LAN
        usd = max(usd, san)
        br_usd = max(br_usd, sum(b for _, _, b in _muc_bedrock_khong_lich_su(s, ca_co_lich_su=True)) * THU_LAI_TOI_DA)   # [va_tien_bedrock] sàn tham khảo khi có thử lại   # san theo mức tối đa đã gồm E2B_TOI_DA của khối chạy code (muc_toi_da va_e2b); usd đã gồm e2b_usd
    return {"lan_goi": len(ds), "token_vao": sum(x["in"] or 0 for x in ds), "token_ra": sum(x["out"] or 0 for x in ds), "co_thu_lai": thu_lai,
            "token_doc_dem": sum(x["cache"] for x in ds), "cong_cu": cong_cu, "usd": usd,
            "muc_khoi_khong_lich_su": [{"loai": l, "trang_thai": st, "usd": m} for l, st, m in bu], "exa_lan": so_exa, "serper_lan": so_serper, "rag_lan": so_rag, "e2b_usd": e2b_usd, "e2b_may": e2b_may,
            # [va_tien_bedrock] THAM KHẢO — không nằm trong "usd", không tính trần: token + tiền theo giá bảng của các lời gọi Bedrock (tiền thật: sổ cổng)
            "token_bedrock": {"vao": br_vao, "ra": br_ra}, "usd_bedrock_tham_khao": round(br_usd, 6),
            # [va_so_tu_choi] loại khối bị hộp từ chối TRƯỚC khi dựng (0 đồng) — có phần tử ⇒ người ghi sổ vẫn ghi MỘT dòng (usd phần đó = 0) kèm nhãn NHAN_TU_CHOI
            "tu_choi_truoc_khi_chay": [l for l, _, _ in _muc_khoi_khong_lich_su(s, ca_co_lich_su=True, _phan="tu_choi")],
            # [va_so_bedrock_do] từng khối Bedrock không lịch sử: trạng thái "DO_CACHE_USAGE" = số đo từ log hộp; khác = mức ước (không đo được)
            "bedrock_khoi_khong_lich_su": [{"loai": l, "trang_thai": st, "usd": round(b, 6), "token_vao": v, "token_ra": r} for l, st, b, v, r in br_kls]}


# [ghi chú] tiền máy E2B của lượt: sổ máy ket_qua/e2b_so/e2b_so.jsonl (hộp ab-worker-temporal ghi qua va_e2b/sandbox_runtime.py:
# dòng "tao" TRƯỚC mỗi lần gọi E2B tạo máy — không ghi được thì không tạo; dòng "xong" lúc dọn với giây tính tiền + số máy). Theo phiên, theo mã máy:
# có "xong" ⇒ giây × giá + (số "tao" thừa so với so_may) × TTL × giá; chỉ có "tao" (hộp chết / chưa dọn) ⇒ mỗi "tao" × TTL × giá (E2B tự tắt ở TTL).
# Giá = cận trên 2 vCPU + 4 GiB (muc_toi_da.E2B_GIA_GIAY); đo cấu hình thật ở ca đầu. Dòng sổ hỏng ⇒ NÉM (như truy vấn chính).
E2B_SO = KQ / "e2b_so" / "e2b_so.jsonl"
E2B_GIA_GIAY = getattr(MT, "E2B_GIA_GIAY", 0.000046)   # = giá CAO NHẤT của bảng (muc_toi_da)
E2B_GIA_THEO_TEMPLATE = getattr(MT, "E2B_GIA_THEO_TEMPLATE", {})   # [ghi chú] giá theo template — dòng "tao" ghi template


def _gia_e2b(template) -> float:
    return E2B_GIA_THEO_TEMPLATE.get(template, E2B_GIA_GIAY)   # template lạ / dòng cũ không có template ⇒ giá cao nhất (thà dư)
# [E minor 1 vòng 2] dấu "đã gắn E2B" (gan_e2b.sh tạo, LUI xoá): có dấu mà phiên có khối chạy code KHÔNG có dòng sổ máy (bind lệch, sổ mất, khối bị chặn
# trước khi tạo máy) ⇒ cộng MỨC E2B TỐI ĐA cho mỗi khối thiếu + cảnh báo (thà dư; khối bị rào sha chặn cũng bị tính — ghi nhận).
E2B_DAU_GAN = KQ / "e2b_so" / "DA_GAN_E2B"
# [ghi chú] sổ nơi chạy: hộp ghi MỘT dòng {"loai": "may", session, node} khi FILE nơi chạy đặt khối Chạy code ở hộp máy
# (sua_doi/workflow/node/base/agent/sandbox_runtime.py _noi_chay_ghi_may). Khối có dòng này KHÔNG bị coi là "thiếu dòng sổ máy" ⇒ không cộng oan mức E2B tối đa.
NOI_CHAY_SO = KQ / "e2b_so" / "noi_chay_so.jsonl"


def _node_chay_o_may(s: str) -> set:
    """Mã nút của các khối thuộc phiên s có dòng bằng chứng "chạy ở hộp máy". Không có file ⇒ rỗng. Dòng hỏng / thiếu trường / loại lạ ⇒ BỎ dòng đó (khối không được
    tính là chạy ở máy ⇒ vẫn bị cộng mức tối đa — thà dư); khác sổ máy (dòng hỏng ⇒ ném) vì ở đây bỏ dòng luôn đi về phía ghi DƯ."""
    ra = set()
    try:
        cac_dong = NOI_CHAY_SO.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ra
    for x in cac_dong:
        try:
            r = json.loads(x)
            if isinstance(r, dict) and r.get("loai") == "may" and r.get("session") == s and isinstance(r.get("node"), str) and r["node"] not in ("", "None"):
                ra.add(r["node"])
        except ValueError:
            continue
    return ra
E2B_KHOI = "flexible_code_interpreter_node"


def tien_e2b(s: str, so_khoi_ci: int = 0) -> tuple[float, int]:
    """(USD, số máy) E2B của phiên s theo sổ máy. so_khoi_ci = số khối chạy code của phiên (người gọi đếm) ⇒ khối thiếu dòng sổ khi đã gắn E2B
    ⇒ + mức tối đa / khối."""
    nhom: dict[str, dict] = {}
    if not E2B_SO.is_file():
        return _e2b_bu_thieu(s, 0.0, 0, _node_chay_o_may(s), so_khoi_ci)
    for i, x in enumerate(E2B_SO.read_text(encoding="utf-8").splitlines(), 1):
        if not x.strip():
            continue
        try:
            r = json.loads(x)
            assert r["loai"] in ("tao", "xong") and isinstance(r["ma"], str)
        except Exception:
            raise ValueError(f"sổ máy E2B {E2B_SO.name} dòng {i} hỏng") from None
        if r.get("session") != s:
            continue
        g = nhom.setdefault(r["ma"], {"tao": 0, "ttl": 0, "xong": None, "node": r.get("node"), "gia": 0.0})
        if r["loai"] == "tao":
            g["tao"] += 1
            g["ttl"] = max(g["ttl"], int(r.get("ttl") or 600))
            g["gia"] = max(g["gia"], _gia_e2b(r.get("template")))   # nhiều dòng tao cùng mã (tạo lại) ⇒ giá cao nhất
        else:
            g["xong"] = r
    usd, may = 0.0, 0
    for g in nhom.values():
        ttl, gia = g["ttl"] or 600, g["gia"] or E2B_GIA_GIAY
        if g["xong"]:
            thua = max(0, g["tao"] - int(g["xong"].get("so_may") or 1))
            usd += float(g["xong"].get("giay") or 0) * gia + thua * ttl * gia
        else:
            usd += g["tao"] * ttl * gia
        may += g["tao"]
    # [va_noi_chay_b2] khối có dòng "may" (bằng chứng lúc chạy) = đã biết nơi chạy ⇒ không tính là thiếu. Khối vừa có "may" vừa có máy E2B (thử lại sau khi đổi file):
    # tiền máy đã cộng ở trên theo sổ máy; hợp hai tập nên không đếm đôi.
    return _e2b_bu_thieu(s, usd, may, {g.get("node") for g in nhom.values()} | _node_chay_o_may(s), so_khoi_ci)


def _e2b_bu_thieu(s: str, usd: float, may: int, node_co_so: set, so_khoi_ci: int) -> tuple[float, int]:
    thieu = max(0, so_khoi_ci - len(node_co_so)) if E2B_DAU_GAN.exists() else 0
    if thieu:
        bu = thieu * getattr(MT, "E2B_TOI_DA", 0.0497)
        print(f"CẢNH BÁO sổ E2B: lượt {s[:8]} có {so_khoi_ci} khối chạy code mà sổ máy chỉ thấy {len(node_co_so)} ⇒ cộng mức tối đa {bu:.4f} USD", file=sys.stderr)
        usd += bu
    return round(usd, 6), may


THU_LAI_TOI_DA = 3   # [ghi chú] số lần thử tối đa của activity khối AI (E đọc: AGENT maximum_attempts=3) — cận trên khi có dấu thử lại
EXA_MOT_LAN = 0.02   # BẰNG va_exa/may_chu.py:31 DU_PHONG_LUOT (mức giữ chỗ / lần của hộp Exa — ƯỚC, không phải trần cứng phía Exa)
# [ghi chú] Giá Serper / lần theo MỨC TỐI ĐA: gói trả phí ĐẮT NHẤT / lần trong bảng giá công khai serper.dev = gói nhỏ nhất 50 USD / 50.000 lượt
# = 0,001 USD [B nhớ từ bảng giá công khai — CHƯA mở lại trang giá; người kiểm cần xác nhận]. User dùng gói miễn phí ⇒ tiền thật hiện = 0 ⇒
# số này là cận trên. Khối KHÔNG có lịch sử (FAILED…) không đếm được lần tra web ⇒ chưa cộng (giới hạn đã biết, cỡ 0,001 × số lần).
SERPER_MOT_LAN = 0.001
# [ghi chú] 0,0001 ⇒ 0: sổ bên này không còn cộng tiền
# cho mỗi lần rag_search (ô "rag_lan" vẫn đếm số lần để đối soát). Dòng cũ:  RAG_MOT_LAN = 0.0001
RAG_MOT_LAN = 0.0   # [ghi chú] 1 lần rag_search (tạo vector câu hỏi bên app mekongai) — cận trên ×5


# [ghi chú] nhãn node_type trong log [CACHE_USAGE] (sua_doi/ai/agentic/cache_observability.py:267, có session= từ 16/11) của
# khối KHÔNG lưu lịch sử, MỘT dòng / lần chạy khối: chạy code = node_sub_type (nodes/agent/code_interpreter*/node.py:60, :67); khối tài liệu =
# "skills:<skill_name>" (e2b_chat_service.py:1044-1047; skill_name ở services/*/config.py). Loại KHÔNG có ở đây (nghiên cứu sâu: nhiều dòng / khối;
# Rào AI, chấm, che: không log) ⇒ giữ MỨC TỐI ĐA.
NHAN_CACHE = {"flexible_code_interpreter_node": "code_interpreter", "skill_code_interpreter_node": "code_interpreter_skill",
              "pptx_chat_node": "skills:pptx", "ey_pptx_chat_node": "skills:ey-pptx", "xlsx_chat_node": "skills:xlsx",
              "docx_chat_node": "skills:docx", "data_analysis_agent_node": "skills:data-analysis",
              "network_graph_output_node": "skills:org-chart", "d3_data_visualization_node": "skills:d3-data-viz"}
HOP_LOG = ("capitol-ab-worker-temporal", "capitol-ab-worker")
DR_KHOI, DR_NHAN_CACHE = "agentic_deep_research_node", "agentic_deep_research"   # [va_so_tu_choi F6 (b)] nhãn node_type của khối tra cứu sâu trong log [CACHE_USAGE]
_CU = re.compile(r"\[CACHE_USAGE\] node_type=(\S+) provider=(\S+) cache_read=(\d+) cache_write=(\d+) uncached_input=(\d+) "
                 r"total_input=(\d+) output=(\d+) cache_read_ratio=\S+ session=(\S+)")


# [E major vòng 3] dòng log chỉ mang usage của lần chạy CUỐI (_stream_agent / vòng nối lại e2b chạy agent.iter mới) ⇒ khối có thử lại vẫn 1 dòng
# mà tiền các lần trước mất ⇒ phiên có câu thử lại trong workflow_event (emit_progress: streaming.py:471, e2b_chat_service.py:1120) ⇒ KHÔNG dùng số đo.
CAU_THU_LAI = ("Retryable error, retrying%", "Connection to the model stalled%")


def _co_thu_lai(s: str) -> bool:
    """Phiên có thể đã THỬ LẠI khối AI (hoặc không chứng minh được là không) ⇒ True ⇒ người gọi tính MỨC TỐI ĐA. Đọc lỗi ⇒ ném (psql_chac)
    ⇒ không ghi sổ (đóng khi lỗi). [ghi chú] True khi MỘT trong:
    (a) dynamic_message khớp CAU_THU_LAI (như cũ);
    (b) có sự kiện event_type='retry_scheduled' (Capitol phát khi Temporal SẼ chạy lại activity: temporal/activities/_shared.py:826, 989);
    (c) THIẾU sự kiện ⇒ không tin "không thấy thử lại": workflow_event chỉ do 1 tác vụ nền (leader Redis) ghi qua pub/sub, có thể mất
        (AB services/redis/event_persistence.py:73-149, NGUON_NGOAI_A3 mục 2). Thiếu = không có run_started, HOẶC số sự kiện node_result <
        số khối COMPLETED (không tính khối dùng lại), HOẶC số execution_failed < số khối FAILED. Đo 7 ngày (17/11, 525 lượt): 1 lượt thiếu
        run_started, 0 lượt thiếu 2 loại kia ⇒ hầu như không tính oan."""
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", s or ""):   # [E minor] mã phiên vào SQL ⇒ chỉ nhận uuid
        raise ValueError(f"_co_thu_lai: mã phiên không phải uuid ({str(s)[:40]!r}) — không ghi sổ")
    dk = " or ".join(f"e.event_data->>'dynamic_message' like '{c}'" for c in CAU_THU_LAI)
    q = (f"with r as (select id from workflow_run where session_id='{s}') select "
         f"(select count(*) from workflow_event e where e.run_id in (select id from r) and ({dk} or e.event_type='retry_scheduled')), "
         f"(select count(*) from workflow_event e where e.run_id in (select id from r) and e.event_type='run_started'), "
         f"(select count(*) from workflow_event e where e.run_id in (select id from r) and e.event_type='node_result'), "
         f"(select count(*) from node_result n where n.run_id in (select id from r) and n.status::text='COMPLETED' and n.cached_from_run_id is null), "
         f"(select count(*) from workflow_event e where e.run_id in (select id from r) and e.event_type='execution_failed'), "
         f"(select count(*) from node_result n where n.run_id in (select id from r) and n.status::text='FAILED')")
    v = [int(x) for x in (psql_chac(q) or "").replace("|", " ").split()]
    if len(v) != 6:
        raise RuntimeError(f"_co_thu_lai: đọc DB không đủ 6 số ({v}) — không ghi sổ")
    thu_lai, rs, enr, nc, ef, nf = v
    if thu_lai > 0:   # (a) / (b): dấu thử lại THẬT ⇒ luôn mức (không gỡ bằng Temporal — E điều kiện 3)
        return True
    if rs == 0 or enr < nc or ef < nf:   # (c) THIẾU sự kiện ⇒ hỏi nguồn ĐỘC LẬP: lịch sử Temporal của lượt [ghi chú]
        return not _temporal_mot_lan(s)
    return False


def _temporal_khong_thu_lai(d) -> bool:
    """Hàm THUẦN trên JSON `temporal workflow show --output json`. True CHỈ KHI chứng minh được không thử lại: workflow ĐÃ COMPLETED; KHÔNG có workflow
    con (không xem được bên trong ⇒ không chứng minh được — E điều kiện 2); mọi ACTIVITY_TASK_STARTED attempt == 1; số STARTED == số COMPLETED
    (không FAILED / TIMED_OUT / CANCELED); có ≥ 1 activity. Khuôn lạ / thiếu trường ⇒ False (đóng khi lỗi)."""
    ev = d.get("events") if isinstance(d, dict) else None
    if not isinstance(ev, list) or not ev:
        return False
    loai = [str(e.get("eventType", "")) for e in ev if isinstance(e, dict)]
    if len(loai) != len(ev) or "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED" not in loai:
        return False
    # [E minor 18/11] workflow chạy LẦN ĐẦU: attempt 1, không chạy lại / tiếp nối (firstExecutionRunId == originalExecutionRunId, không continued)
    bd = [e.get("workflowExecutionStartedEventAttributes") or {} for e in ev if e.get("eventType") == "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"]
    if len(bd) != 1 or bd[0].get("attempt") != 1 or not bd[0].get("firstExecutionRunId") \
            or bd[0].get("firstExecutionRunId") != bd[0].get("originalExecutionRunId") or bd[0].get("continuedExecutionRunId"):
        return False
    # [E minor] marker: chỉ chấp nhận core_patch (bản vá SDK); marker khác (vd local activity / side effect) ⇒ không chứng minh được
    if any((e.get("markerRecordedEventAttributes") or {}).get("markerName") != "core_patch" for e in ev if e.get("eventType") == "EVENT_TYPE_MARKER_RECORDED"):
        return False
    if any("CHILD_WORKFLOW" in x for x in loai) or any(x.startswith("EVENT_TYPE_ACTIVITY_TASK_") and x.split("EVENT_TYPE_ACTIVITY_TASK_")[1] in
                                                       ("FAILED", "TIMED_OUT", "CANCELED", "CANCEL_REQUESTED") for x in loai):
        return False
    bat_dau = [e for e in ev if e.get("eventType") == "EVENT_TYPE_ACTIVITY_TASK_STARTED"]
    xong = sum(1 for x in loai if x == "EVENT_TYPE_ACTIVITY_TASK_COMPLETED")
    if not bat_dau or len(bat_dau) != xong:
        return False
    for e in bat_dau:
        a = (e.get("activityTaskStartedEventAttributes") or {}).get("attempt")
        if isinstance(a, bool) or not isinstance(a, int) or a != 1:
            return False
    return True


def _temporal_mot_lan(s: str) -> bool:
    """Lịch sử Temporal của lượt (workflow_run.metadata.temporal_workflow_id, dạng workflow-<uuid>) ⇒ _temporal_khong_thu_lai. Mọi lỗi (không có mã, hết hạn
    giữ lịch sử, docker / temporal lỗi, JSON hỏng, quá giờ) ⇒ False ⇒ người gọi tính MỨC (giữ "thiếu số ⇒ ghi mức")."""
    try:
        # [E T-1] phiên phải có ĐÚNG 1 workflow_run (nhiều lượt ⇒ lịch sử 1 lượt không nói về lượt kia ⇒ không chứng minh được)
        n = psql_chac(f"select count(*) from workflow_run where session_id='{s}'")
        if n.strip() != "1":
            return False
        wid = psql_chac(f"select metadata->>'temporal_workflow_id' from workflow_run where session_id='{s}'")
        if not re.fullmatch(r"workflow-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", wid or ""):
            return False
        r = subprocess.run(["docker", "exec", "capitol-temporal", "temporal", "workflow", "show", "--namespace", "capitol", "--workflow-id", wid,
                            "--output", "json"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return False
        return _temporal_khong_thu_lai(json.loads(r.stdout))
    except Exception:  # noqa: BLE001 — đóng khi lỗi: không chứng minh được ⇒ mức
        return False


def _dong_cache_usage(s: str) -> list[tuple[str, str, int, int]]:
    """[(nhãn, mô hình, total_input, output)] của MỌI dòng [CACHE_USAGE] có session=s ở các hộp worker. Đọc log lỗi / hộp vừa tạo lại (log mất)
    ⇒ [] ⇒ người gọi giữ MỨC TỐI ĐA (chiều an toàn)."""
    ra = []
    tu = psql_chac(f"select to_char(min(created_at) at time zone 'UTC' - interval '5 minutes', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
                   f"from workflow_run where session_id='{s}'") or "48h"   # [E minor] --since theo lúc lượt bắt đầu (trừ 5 phút)
    for h in HOP_LOG:
        try:
            r = subprocess.run(["docker", "logs", "--since", tu, h], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for dong in (r.stdout + "\n" + r.stderr).splitlines():
            if "[CACHE_USAGE]" in dong and f"session={s}" in dong:
                m = _CU.search(dong)
                if m and m.group(8) == s:
                    ra.append((m.group(1), m.group(2), int(m.group(6)), int(m.group(7))))
    return ra


def _thay_bang_cache_usage(s: str, bu: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]:
    """Mỗi loại khối trong NHAN_CACHE: số dòng log nhãn đó == số khối COMPLETED loại đó VÀ 0 khối FAILED loại đó ⇒ thay mức tối đa bằng tiền ĐO
    (tổng các dòng: total_input × giá vào — token đọc đệm tính như vào thường, cận trên như đầu file — + output × giá ra). Khác ⇒ max(đo, mức)
    (khối hỏng có thể đã tiêu mà không có dòng; thử lại thêm dòng). Loại ngoài NHAN_CACHE ⇒ giữ nguyên."""
    if not any(l in NHAN_CACHE for l, _, _ in bu):
        return bu
    dong = [] if _co_thu_lai(s) else _dong_cache_usage(s)   # [E major] có thử lại ⇒ coi như không đo được ⇒ max(0, mức) = mức
    ra = [x for x in bu if x[0] not in NHAN_CACHE]
    for loai in sorted({l for l, _, _ in bu if l in NHAN_CACHE}):
        nhom = [x for x in bu if x[0] == loai]
        muc = sum(m for _, _, m in nhom)
        dd = [d for d in dong if d[0] == NHAN_CACHE[loai]]
        do = 0.0
        for _, mh, vao, ra_ in dd:
            g = GIA.get(mh.split(":")[-1]) or GIA["gpt-6-luna"]
            do += (vao * g["input"] + ra_ * g["output"]) / 1_000_000
        khop = len(dd) == sum(1 for x in nhom if x[1] == "COMPLETED") and not any(x[1] != "COMPLETED" for x in nhom) and dd
        ra.append((loai, "DO_CACHE_USAGE" if khop else "+".join(sorted({x[1] for x in nhom})), round(do, 6) if khop else max(do, muc)))
    return ra


# [bản 2 20/11 — E K2] NEO cả dòng theo khuôn log thật của hộp ("<ngày> <giờ> - <tên logger> - INFO - <lời>"; logger của file khối = nodes.agentic_deep_research.node —
# thu_trong_hop.py C6 kiểm tên này bằng mô-đun thật) ⇒ chuỗi [DR_CONG_CU] nằm GIỮA một dòng log khác (chữ người dùng / trang web bị ghi vào log) không được nhận.
# Giới hạn còn lại: lời log NHIỀU DÒNG của logger khác chứa nguyên một dòng giả đúng khuôn — khi đó có 2 dòng (giả + thật) ⇒ giữ mức tối đa; chỉ lọt nếu khối không in dòng thật.
_DRC = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)? - nodes\.agentic_deep_research\.node - INFO - "
                  r"\[DR_CONG_CU\] session=(\S+) goi_cong_cu=(\d+) ket_qua_cong_cu=(\d+) het_tran=([01])")


def _dong_dr_cong_cu(s: str) -> list[int]:
    """Số lần gọi công cụ ở MỌI dòng [DR_CONG_CU] có session=s trong log các hộp worker. Đọc log lỗi / hộp vừa tạo lại (log mất) ⇒ [] ⇒ giữ MỨC TỐI ĐA.
    [E K1] Con số là số lần công cụ TRẢ VỀ ĐƯỢC (thư viện không đếm lần công cụ ném lỗi) — đủ làm cận trên tiền Exa vì hộp tra web trả lỗi Exa bằng kết quả thường."""
    ra = []
    tu = psql_chac(f"select to_char(min(created_at) at time zone 'UTC' - interval '5 minutes', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
                   f"from workflow_run where session_id='{s}'") or "48h"
    for h in HOP_LOG:
        try:
            r = subprocess.run(["docker", "logs", "--since", tu, h], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        ra += _doc_dong_dr_cong_cu((r.stdout + "\n" + r.stderr).splitlines(), s)
    return ra


def _doc_dong_dr_cong_cu(cac_dong: list[str], s: str) -> list[int]:
    return [int(m.group(2)) for m in (_DRC.fullmatch(d.rstrip("\r")) for d in cac_dong if "[DR_CONG_CU]" in d and f"session={s}" in d) if m and m.group(1) == s]


def _muc_dr_theo_dem(bu: list[tuple[str, str, float]], dem: list[int], co_thu_lai: bool) -> list[tuple[str, str, float]]:
    """[ghi chú] Phần TÍNH TRẦN của khối tra cứu sâu chạy
    Claude chỉ là tiền tra web (≤ DR_CONG_CU lần × DR_EXA_MOT_LAN). Thay mức tối đa bằng số lần gọi công cụ THẬT × giá một lần CHỈ KHI đủ cả: đúng MỘT khối loại
    này trong lượt · COMPLETED · mức của nó ĐÚNG bằng mức tra web thuần (tức đang chạy Claude; luna còn gộp tiền mô hình ⇒ không đụng) · lượt không dấu thử lại ·
    đúng MỘT dòng log · số lần 0 ≤ n ≤ DR_CONG_CU. Thiếu một ⇒ giữ mức tối đa (thà dư). Hàm thuần — bộ thử gọi thẳng."""
    nhom = [x for x in bu if x[0] == DR_KHOI]
    muc_thuan = MT.DR_CONG_CU * MT.DR_EXA_MOT_LAN
    if (len(nhom) != 1 or nhom[0][1] != "COMPLETED" or abs(nhom[0][2] - muc_thuan) > 1e-9 or co_thu_lai or len(dem) != 1
            or not 0 <= dem[0] <= MT.DR_CONG_CU):
        return bu
    return [x for x in bu if x[0] != DR_KHOI] + [(DR_KHOI, "DO_DEM_CONG_CU", round(dem[0] * MT.DR_EXA_MOT_LAN, 6))]


def _thay_dr_bang_dem_cong_cu(s: str, bu: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]:
    if not any(l == DR_KHOI for l, _, _ in bu):
        return bu
    thu_lai = _co_thu_lai(s)
    return _muc_dr_theo_dem(bu, [] if thu_lai else _dong_dr_cong_cu(s), thu_lai)


def _usd_bedrock(m: dict, vao: int, ra: int, cache_doc: int = 0, cache_tao: int = 0) -> float:
    """Tiền theo giá bảng của MỘT lời gọi Bedrock: `vao` = tổng token vào (gồm đọc + ghi bộ nhớ đệm). Bảng thiếu giá đệm ⇒ tính phần đó theo giá vào (thà dư)."""
    thuong = max(0, vao - cache_doc - cache_tao)
    return (thuong * m["gia_vao"] + cache_doc * m.get("gia_cache_doc", m["gia_vao"]) + cache_tao * m.get("gia_cache_tao", m["gia_vao"]) + ra * m["gia_ra"]) / 1_000_000


def _thay_bedrock_bang_cache_usage(s: str, br: list[tuple[str, str, float]]) -> list[tuple[str, str, float, int, int]]:
    """[ghi chú] Khối chạy Bedrock mà KHÔNG lưu lịch sử lời gọi (hộp phiên:
    Word / Excel / PowerPoint / phân tích dữ liệu / D3 / đồ thị; khối chạy code) ⇒ lấy số ĐO từ dòng log [CACHE_USAGE] của chính lượt (hộp worker ghi một dòng / lần
    chạy khối: token đọc đệm, ghi đệm, vào thường, ra + tên mô hình thật) thay cho MỨC ƯỚC. Cùng luật khớp với _thay_bang_cache_usage: số dòng nhãn đó == số khối
    COMPLETED loại đó, không khối nào FAILED, lượt không có dấu thử lại, và mô hình của dòng tra được trong bảng cổng là Bedrock. Không khớp ⇒ giữ mức ước (thà dư).
    Trả [(loại, trạng thái, usd, token vào, token ra)]."""
    if not br:
        return []
    if not any(l in NHAN_CACHE or l in (DR_KHOI, DUYET_KHOI) for l, _, _ in br) or _co_thu_lai(s):
        return [(l, st, b, 0, 0) for l, st, b in br]
    dong = _dong_cache_usage_day_du(s) if any(l in NHAN_CACHE or l == DR_KHOI for l, _, _ in br) else []
    ra = [(l, st, b, 0, 0) for l, st, b in br if l not in NHAN_CACHE and l not in (DR_KHOI, DUYET_KHOI)]
    # [ghi chú] bản tóm tắt cho Người duyệt chạy Claude: ô tham khảo đang là MỨC ƯỚC (~0,4 USD) trong khi một bản tóm tắt thật chỉ vài
    # trăm token ⇒ lấy số ĐO từ dòng log [DIGEST_USAGE] của chính lượt (xem _bedrock_duyet_theo_do). Không khớp luật ⇒ giữ mức ước (thà dư).
    nhom_duyet = [x for x in br if x[0] == DUYET_KHOI]
    if nhom_duyet:
        dong_dg = _dong_digest_usage(s)
        # [ghi chú] Khối Người duyệt ở CHẾ ĐỘ KPI
        # dựng phần hiển thị bằng code, KHÔNG gọi AI viết tóm tắt (sua_doi/nodes/logic/human_intervention/node.py: `_hien_kpi(...) if kpi else await
        # self._resolve_review_display(...)`) ⇒ ô tham khảo của nó là 0. Chỉ ghi 0 khi CHẮC — đủ cả: mọi khối Người duyệt của lượt có ô che_do_kpi đúng là `true`
        # trong payload của CHÍNH phiên bản đã chạy · mọi khối COMPLETED / SKIPPED (cờ máy tắt thì khối đã FAILED) · KHÔNG có dòng [DIGEST_USAGE] nào của lượt
        # (có dòng = mâu thuẫn ⇒ theo luật số đo / mức ước như cũ). Không đọc được payload, lệch số khối, hay lẫn khối thường ⇒ giữ đường cũ (thà dư).
        # "Ca hợp đồng" trong bộ thử đọc node.py đang gắn: ai đổi khối để chế độ KPI gọi AI thì ca đỏ trước khi sổ ghi thiếu.
        kpi = _duyet_kpi_cua_luot(s)
        if (not dong_dg and kpi is not None and len(kpi) == len(nhom_duyet) and all(x is True for x in kpi)
                and all(x[1] in ("COMPLETED", "SKIPPED") for x in nhom_duyet)):
            ra.append((DUYET_KHOI, "KHONG_GOI_AI_CHE_DO_KPI", 0.0, 0, 0))
        else:
            ra.append(_bedrock_duyet_theo_do(nhom_duyet, dong_dg))
    # [ghi chú] Khối tra cứu sâu ghi NHIỀU dòng [CACHE_USAGE] / lần chạy
    # (mỗi vòng một dòng) nên không vào luật "một dòng một khối" ở dưới. Bản vá khối dùng MỘT bộ đếm chung cho mọi vòng (sua_doi/nodes/agentic_deep_research/
    # node.py: "usage": self._dr_usage) ⇒ các dòng là số CỘNG DỒN ⇒ dòng CUỐI = cả khối. Chỉ dùng khi: đúng MỘT khối loại này trong lượt, COMPLETED, lượt không dấu thử
    # lại (đã chặn ở trên), mọi dòng là mô hình Bedrock, và dãy không giảm (đúng là cộng dồn). Dãy có chỗ giảm ⇒ CỘNG mọi dòng (thà dư). Không dòng / nhiều khối /
    # khối hỏng ⇒ giữ mức ước. Đây là ô THAM KHẢO (không tính trần); phần tính trần của khối (tiền tra web) KHÔNG đổi.
    nhom_dr = [x for x in br if x[0] == DR_KHOI]
    if nhom_dr:
        dd = [d for d in dong if d["nhan"] == DR_NHAN_CACHE]
        gia = [_mo_hinh_bang(d["mo_hinh"]) for d in dd]
        if len(nhom_dr) == 1 and nhom_dr[0][1] == "COMPLETED" and dd and all(g and g["ncc"] == MT.NCC_BEDROCK for g in gia):
            tang = all(a["vao"] <= b["vao"] and a["ra"] <= b["ra"] for a, b in zip(dd, dd[1:]))
            chon = [(dd[-1], gia[-1])] if tang else list(zip(dd, gia))
            ra.append((DR_KHOI, "DO_CACHE_USAGE_CONG_DON" if tang else "DO_CACHE_USAGE_TONG_MOI_DONG",
                       round(sum(_usd_bedrock(g, d["vao"], d["ra"], d["cache_doc"], d["cache_tao"]) for d, g in chon), 6), sum(d["vao"] for d, _ in chon), sum(d["ra"] for d, _ in chon)))
        else:
            ra.append((DR_KHOI, "+".join(sorted({x[1] for x in nhom_dr})), sum(b for _, _, b in nhom_dr), 0, 0))
    for loai in sorted({l for l, _, _ in br if l in NHAN_CACHE}):
        nhom = [x for x in br if x[0] == loai]
        dd = [d for d in dong if d["nhan"] == NHAN_CACHE[loai]]
        gia = [_mo_hinh_bang(d["mo_hinh"]) for d in dd]
        khop = bool(dd) and len(dd) == sum(1 for x in nhom if x[1] == "COMPLETED") and not any(x[1] != "COMPLETED" for x in nhom) \
            and all(g and g["ncc"] == MT.NCC_BEDROCK for g in gia)
        if khop:
            do = sum(_usd_bedrock(g, d["vao"], d["ra"], d["cache_doc"], d["cache_tao"]) for d, g in zip(dd, gia))
            ra.append((loai, "DO_CACHE_USAGE", round(do, 6), sum(d["vao"] for d in dd), sum(d["ra"] for d in dd)))
        else:
            ra.append((loai, "+".join(sorted({x[1] for x in nhom})), sum(b for _, _, b in nhom), 0, 0))
    return ra


DUYET_KHOI = "human_intervention_node"
# NEO cả dòng theo khuôn log thật của hộp (bài học K2 của E ở gói tra web): "<ngày> <giờ> - <tên logger> - INFO - <lời>"; logger của file bản tóm tắt =
# nodes.logic.human_intervention.digest (18 / 18 dòng [DIGEST_USAGE] trong log lưu 19/11 đúng khuôn này) ⇒ chuỗi [DIGEST_USAGE] nằm giữa một dòng log khác không được nhận.
_DG = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)? - nodes\.logic\.human_intervention\.digest - INFO - "
                 r"\[DIGEST_USAGE\] session=(\S+) node=(\S+) model=(\S+) requests=(\d+) input=(\d+) output=(\d+) cache_read=(\d+) reasoning=(\d+)")


def _doc_dong_digest(cac_dong: list[str], s: str) -> list[dict]:
    """Các dòng [DIGEST_USAGE] ĐÚNG khuôn của phiên s ⇒ [{"node", "mo_hinh", "lan_goi", "vao", "ra", "cache_doc", "suy_luan"}]. Thuần — bộ thử gọi thẳng."""
    ra = []
    for d in cac_dong:
        if "[DIGEST_USAGE]" not in d or f"session={s}" not in d:
            continue
        m = _DG.fullmatch(d.rstrip("\r"))
        if m and m.group(1) == s:
            ra.append({"node": m.group(2), "mo_hinh": m.group(3), "lan_goi": int(m.group(4)), "vao": int(m.group(5)), "ra": int(m.group(6)),
                       "cache_doc": int(m.group(7)), "suy_luan": int(m.group(8))})
    return ra


def _dong_digest_usage(s: str) -> list[dict]:
    """Dòng [DIGEST_USAGE] của phiên trong log các hộp worker. Đọc log lỗi / hộp vừa tạo lại (log mất) ⇒ [] ⇒ giữ MỨC ƯỚC."""
    ra = []
    tu = psql_chac(f"select to_char(min(created_at) at time zone 'UTC' - interval '5 minutes', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
                   f"from workflow_run where session_id='{s}'") or "48h"
    for h in HOP_LOG:
        try:
            r = subprocess.run(["docker", "logs", "--since", tu, h], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        ra += _doc_dong_digest((r.stdout + "\n" + r.stderr).splitlines(), s)
    return ra


def _duyet_la_kpi_chac(st) -> bool:
    """Ô che_do_kpi của MỘT khối Người duyệt (struct trong payload) có CHẮC CHẮN bật không. Chặt hơn toggle_enabled của Capitol: chỉ nhận đúng giá trị boolean true
    (mẫu KPI lưu đúng kiểu này); chuỗi "true", số 1, thiếu ô, hai ô trùng tên ⇒ False (không chắc ⇒ giữ mức ước). Thuần — bộ thử gọi thẳng."""
    if not isinstance(st, dict) or st.get("node_id") != DUYET_KHOI or not isinstance(st.get("params"), list):
        return False
    v = [p.get("value") for p in st["params"] if isinstance(p, dict) and p.get("field_id") == "che_do_kpi"]
    return len(v) == 1 and v[0] is True


def _duyet_kpi_tu_payload(ds: list, pl: dict) -> list | None:
    """ds = [{"ver", "inst"}] các khối ĐÃ CHẠY của lượt; pl = {ver: payload}. ⇒ [True / False cho TỪNG khối Người duyệt đã chạy] | None khi không đọc được (thiếu payload,
    không tìm thấy khối trong payload) — None nghĩa là KHÔNG CHẮC. Thuần — bộ thử gọi thẳng."""
    ra = []
    try:
        for x in ds:
            n = next((n for n in (pl[x["ver"]].get("nodes") or []) if n.get("id") == x["inst"]), None)
            st = ((n or {}).get("data") or {}).get("struct")
            if not isinstance(st, dict) or not st.get("node_id"):
                return None
            if st["node_id"] == DUYET_KHOI:
                ra.append(_duyet_la_kpi_chac(st))
    except (KeyError, TypeError, AttributeError):
        return None
    return ra


def _duyet_kpi_cua_luot(s: str) -> list | None:
    """Đọc DB: các khối đã chạy của lượt (cùng điều kiện với _muc_khoi_khong_lich_su: COMPLETED / FAILED, hoặc SKIPPED đã bắt đầu) + payload phiên bản của chúng.
    Mọi lỗi đọc ⇒ None (không chắc ⇒ giữ mức ước; đây là ô THAM KHẢO nên không ném)."""
    try:
        ds = _json_chac(f"""select coalesce(json_agg(json_build_object('ver', n.version_id, 'inst', n.node_instance_id)), '[]')
            from node_result nr join workflow_run r on r.id=nr.run_id join node n on n.id=nr.node_id
            where r.session_id='{s}' and nr.cached_from_run_id is null and (nr.status in ('COMPLETED', 'FAILED')
                  or (nr.status = 'SKIPPED' and exists (select 1 from workflow_event e where e.run_id = r.id
                      and e.node_instance_id::text = n.node_instance_id::text and e.event_type in ('begin', 'awaiting_intervention'))))""")
        pl = {v: _json_chac(f"select payload from workflow_version where id='{v}'") for v in {x["ver"] for x in ds}}
    except Exception:  # noqa: BLE001 — ô tham khảo: không chắc thì giữ mức ước, không làm hỏng việc ghi sổ
        return None
    return _duyet_kpi_tu_payload(ds, pl)


def _bedrock_duyet_theo_do(nhom: list[tuple[str, str, float]], dong: list[dict]) -> tuple[str, str, float, int, int]:
    """Ô THAM KHẢO Bedrock của (các) khối Người duyệt trong MỘT lượt: thay mức ước bằng số đo CHỈ KHI đủ cả: số dòng log == số khối · mỗi khối COMPLETED hoặc
    SKIPPED (bị bấm Từ chối — bản tóm tắt đã gọi AI trước đó) · mỗi dòng thuộc một nút KHÁC nhau · mọi dòng là mô hình Bedrock tra được trong bảng cổng · mỗi dòng ≥ 1 lần gọi.
    Thiếu một ⇒ giữ mức ước (thà dư). Tiền một dòng tính theo CẬN TRÊN: (vào + đọc đệm) × giá vào + (ra + suy luận) × giá ra — không trừ giá đệm vì chưa chắc thư viện
    đã gộp hay tách hai số đó (với bản tóm tắt vài trăm token hai cách ra cùng số). Phần TÍNH TRẦN của khối không đổi ở đây. Thuần — bộ thử gọi thẳng."""
    giu = (DUYET_KHOI, "+".join(sorted({x[1] for x in nhom})), sum(b for _, _, b in nhom), 0, 0)
    gia = [_mo_hinh_bang(d["mo_hinh"]) for d in dong]
    if (not dong or len(dong) != len(nhom) or any(x[1] not in ("COMPLETED", "SKIPPED") for x in nhom) or len({d["node"] for d in dong}) != len(dong)
            or not all(g and g["ncc"] == MT.NCC_BEDROCK for g in gia) or any(d["lan_goi"] < 1 for d in dong)):
        return giu
    vao, ra_ = [d["vao"] + d["cache_doc"] for d in dong], [d["ra"] + d["suy_luan"] for d in dong]
    # [ghi chú] lời gọi đầu GHI bộ nhớ
    # đệm, giá ghi = 1,25 × giá vào, mà dòng log không mang số token ghi đệm ⇒ tính TOÀN BỘ token vào theo giá ghi đệm (bảng cổng có `gia_cache_tao` thì lấy số lớn hơn
    # giữa nó và 1,25 × giá vào) để ô tham khảo luôn ≥ thật (thà dư). Số token ghi vào sổ không đổi.
    usd = sum((v * max(g.get("gia_cache_tao") or 0, g["gia_vao"] * 1.25) + r * g["gia_ra"]) / 1_000_000 for g, v, r in zip(gia, vao, ra_))
    return (DUYET_KHOI, "DO_DIGEST_USAGE", round(usd, 6), sum(vao), sum(ra_))


def _dong_cache_usage_day_du(s: str) -> list[dict]:
    """Như _dong_cache_usage nhưng giữ đủ cột: [{"nhan", "mo_hinh", "cache_doc", "cache_tao", "vao" (tổng token vào), "ra"}]. Đọc log lỗi ⇒ []."""
    ra = []
    tu = psql_chac(f"select to_char(min(created_at) at time zone 'UTC' - interval '5 minutes', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
                   f"from workflow_run where session_id='{s}'") or "48h"
    for h in HOP_LOG:
        try:
            r = subprocess.run(["docker", "logs", "--since", tu, h], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for dong in (r.stdout + "\n" + r.stderr).splitlines():
            if "[CACHE_USAGE]" in dong and f"session={s}" in dong:
                m = _CU.search(dong)
                if m and m.group(8) == s:
                    ra.append({"nhan": m.group(1), "mo_hinh": m.group(2), "cache_doc": int(m.group(3)), "cache_tao": int(m.group(4)), "vao": int(m.group(6)), "ra": int(m.group(7))})
    return ra


_BANG_MH: dict | None = None


def _mo_hinh_bang(ten: str) -> dict | None:
    """Tra tên mô hình (như ghi trong lịch sử khối) trong bảng định tuyến của cổng ⇒ {"ncc", "gia_vao", "gia_ra"} (USD / 1 triệu token) hoặc None.
    Khớp: tên Capitol gửi (dinh_tuyen.ten) · id mục · tên mục; rồi thử bỏ tiền tố vùng / hãng ("global.", "us.", "anthropic.") và đuôi ngày / phiên bản
    ("-20261001", "-v1:0") — Bedrock trả tên dài hơn tên Capitol gửi. Bảng đọc 1 lần / tiến trình; đọc lỗi ⇒ NÉM (đóng khi lỗi)."""
    global _BANG_MH
    if _BANG_MH is None:
        import yaml
        b = yaml.safe_load(MT.BANG_DINH_TUYEN.read_text(encoding="utf-8"))
        ra = {}
        def _loai(m):   # "bedrock" = nhà cung cấp của mục có `mien_tran: true` trong bảng (bảng của F: bedrock_046) — KHÔNG theo tên
            return MT.NCC_BEDROCK if b["nha_cung_cap"][m["nha_cung_cap"]].get("mien_tran") is True else str(m["nha_cung_cap"])
        for k, m in b["mo_hinh"].items():
            v = {"ncc": _loai(m), "gia_vao": float(m["gia_vao"]), "gia_ra": float(m["gia_ra"])}
            for k_dem in ("gia_cache_doc", "gia_cache_tao"):   # [va_so_bedrock_do] giá đọc / ghi bộ nhớ đệm (mục Bedrock của bảng có; thiếu ⇒ _usd_bedrock dùng giá vào)
                if m.get(k_dem) is not None:
                    v[k_dem] = float(m[k_dem])
            ra.setdefault(str(k), v); ra.setdefault(str(m["id"]), v)
        for d in b["dinh_tuyen"]:
            m = b["mo_hinh"][d["toi"]]
            v = {"ncc": _loai(m), "gia_vao": float(m["gia_vao"]), "gia_ra": float(m["gia_ra"])}
            for k_dem in ("gia_cache_doc", "gia_cache_tao"):
                if m.get(k_dem) is not None:
                    v[k_dem] = float(m[k_dem])
            cu = ra.get(str(d["ten"]))
            if cu is None or (cu["ncc"] == v["ncc"]):
                gop = v if cu is None else {"ncc": v["ncc"], "gia_vao": max(cu["gia_vao"], v["gia_vao"]), "gia_ra": max(cu["gia_ra"], v["gia_ra"])}
                if cu is not None:   # một tên, nhiều đường cùng nhà cung cấp ⇒ giá đệm lấy số LỚN nhất khi cả hai có; chỉ một bên có ⇒ bỏ (dùng giá vào — thà dư)
                    for k_dem in ("gia_cache_doc", "gia_cache_tao"):
                        if k_dem in cu and k_dem in v:
                            gop[k_dem] = max(cu[k_dem], v[k_dem])
                ra[str(d["ten"])] = gop
            else:   # một tên trỏ hai nhà cung cấp khác nhau ⇒ KHÔNG coi là bedrock (tính trần, giá cao nhất)
                ra[str(d["ten"])] = {"ncc": "lan", "gia_vao": max(cu["gia_vao"], v["gia_vao"]), "gia_ra": max(cu["gia_ra"], v["gia_ra"])}
        _BANG_MH = ra
    if ten in _BANG_MH:
        return _BANG_MH[ten]
    gon = re.sub(r"^(global\.|us\.|eu\.|apac\.)?(anthropic\.)?", "", ten)
    gon = re.sub(r"(-\d{8})?(-v\d+(:\d+)?)?$", "", gon)
    return _BANG_MH.get(gon)


def _muc_bedrock_khong_lich_su(s: str, ca_co_lich_su: bool = False) -> list[tuple[str, str, float]]:
    """Như _muc_khoi_khong_lich_su nhưng trả MỨC BEDROCK ƯỚC (phần không tính trần) của từng khối. Cờ tắt ⇒ []."""
    if not MT.TIEN_BEDROCK:
        return []
    return [(l, st, b) for l, st, b in _muc_khoi_khong_lich_su(s, ca_co_lich_su, _phan="bedrock") if b > 0]


# [ghi chú] BẰNG sua_doi/temporal/utils/node_factory.py DAU_TU_CHOI_TRUOC_KHI_CHAY.
DAU_TU_CHOI_TRUOC_KHI_CHAY = "[CHAY_THU_TU_CHOI_TRUOC_KHI_CHAY]"
NHAN_TU_CHOI = "lỗi trước khi gọi AI"
LOI_TU_CHOI_NGUOI_DUNG = "Loại khối này chưa chạy được trên hệ thống — bị từ chối trước khi chạy, chưa có lời gọi AI nào."   # [E K1] BẰNG node_factory.py — ô user_message không sinh từ chữ lỗi


def _la_tu_choi_truoc_khi_chay(x: dict) -> bool:
    """Khối bị hộp TỪ CHỐI TRƯỚC KHI DỰNG (node_factory) ⇒ chắc chắn 0 lời gọi AI / công cụ trả phí. Phải ĐỦ CẢ 7 điều kiện — thiếu một ⇒ False ⇒ người gọi
    ghi mức tối đa như cũ (sai thì nghiêng về ghi dư): FAILED · không có lịch sử lời gọi · không có dữ liệu ra · error_info.code = internal_error ·
    retryable = false · attempts_made = 1 (đúng một lần thử — lần thử nào khác có thể đã chạy khối) · technical_message BẮT ĐẦU bằng dấu · user_message ĐÚNG NGUYÊN VĂN câu cố định của node_factory (E K1: sáu điều kiện đầu giả được bằng một ValueError ném trong khối).
    attempts_made do node_factory đọc từ Temporal (E K2); đọc không được ⇒ 0 ⇒ không nhận."""
    ei = x.get("ei")
    return (x.get("st") == "FAILED" and not x.get("mh") and not x.get("co_data") and isinstance(ei, dict)
            and ei.get("code") == "internal_error" and ei.get("retryable") is False and ei.get("attempts_made") == 1
            and isinstance(ei.get("technical_message"), str) and ei["technical_message"].startswith(DAU_TU_CHOI_TRUOC_KHI_CHAY)
            and ei.get("user_message") == LOI_TU_CHOI_NGUOI_DUNG)   # [E K1] điều kiện thứ bảy: lỗi ném TRONG khối không thể mang câu này (bộ phân loại Capitol đặt câu mẫu của họ)


def _muc_khoi_khong_lich_su(s: str, ca_co_lich_su: bool = False, _phan: str = "phai_bao") -> list[tuple[str, str, float]]:
    """[(loại khối, trạng thái, mức tối đa)] cho mọi khối COMPLETED / FAILED của lượt có mức > 0 mà output không có message_history.
    Đọc loại khối từ workflow_version.payload của chính khối (node.version_id + node_instance_id). Lỗi đọc ⇒ NÉM (người gọi không ghi sổ ⇒
    chỗ giữ còn chặn — đóng khi lỗi, như ghi_tien_mot_lan)."""
    # [ghi chú] (F4, E đo phiên 1d4c55ed) thêm khối SKIPPED mà ĐÃ BẮT ĐẦU chạy (có sự kiện begin / awaiting_intervention của đúng khối):
    # khối Người duyệt bị bấm Từ chối mang SKIPPED dù bản tóm tắt AI đã gọi ⇒ trước đây 0 đồng, không dòng sổ. Khối SKIPPED chưa bắt đầu (đứng sau
    # khối dừng) không có sự kiện đó ⇒ vẫn bỏ. Giới hạn: sự kiện ghi qua một tác vụ nền, có thể mất (đo 17/11: 1 / 525 lượt thiếu) ⇒ khi mất thì như cũ.
    ds = _json_chac(f"""select coalesce(json_agg(json_build_object('ver', n.version_id, 'inst', n.node_instance_id, 'st', nr.status,
            'mh', coalesce(jsonb_typeof(nr.output_data::jsonb->'metadata'->'message_history') = 'array', false),
            'ei', nr.output_data::jsonb->'error_info', 'co_data', coalesce(jsonb_typeof(nr.output_data::jsonb->'data') <> 'null', false))), '[]')
        from node_result nr join workflow_run r on r.id=nr.run_id join node n on n.id=nr.node_id
        where r.session_id='{s}' and nr.cached_from_run_id is null and (nr.status in ('COMPLETED', 'FAILED')
              or (nr.status = 'SKIPPED' and exists (select 1 from workflow_event e where e.run_id = r.id
                  and e.node_instance_id::text = n.node_instance_id::text and e.event_type in ('begin', 'awaiting_intervention'))))""")   # [chạy lại] khối dùng lại = 0 đồng
    pl, ra = {}, []
    for x in ds:
        if _phan != "tu_choi" and _la_tu_choi_truoc_khi_chay(x):   # [va_so_tu_choi] hộp từ chối trước khi dựng khối ⇒ 0 đồng (cả phần tính trần lẫn ô Bedrock)
            continue
        if x["mh"] and not ca_co_lich_su:   # [ghi chú] ca_co_lich_su=True: mọi khối AI (dùng khi lượt có dấu thử lại — tien_luot)
            continue
        if x["ver"] not in pl:
            pl[x["ver"]] = _json_chac(f"select payload from workflow_version where id='{x['ver']}'")
        n = next((n for n in pl[x["ver"]].get("nodes", []) if n.get("id") == x["inst"]), None)
        st = ((n or {}).get("data") or {}).get("struct") or {}
        if not st:
            raise ValueError(f"tien_luot: không đọc được loại khối {x['inst']} của lượt {s[:8]} — không ghi sổ (đóng khi lỗi)")
        m = MT._muc_khoi(st)
        if m is None:   # [E M2] loại chưa có mức = trang CHẶN trước khi chạy; đã chạy ⇒ có đường không qua toi_da (script / API) ⇒ lệch an toàn
            raise ValueError(f"tien_luot: khối {st.get('node_id')} ({x['st']}) của lượt {s[:8]} chưa có mức tối đa mà đã chạy — không ghi sổ, "
                             "báo A (đóng khi lỗi)")
        if _phan == "tu_choi":   # [va_so_tu_choi] chỉ liệt kê khối bị từ chối trước khi chạy (để ghi nhãn vào dòng sổ) — kể cả khối mức 0
            if _la_tu_choi_truoc_khi_chay(x):
                ra.append((st.get("node_id"), x["st"], 0.0))
            continue
        if _phan == "bedrock":   # [va_tien_bedrock] phần KHÔNG tính trần của khối (mức Bedrock ước)
            b = MT.muc_chi_tiet(st)[1]
            if b > 0:
                ra.append((st.get("node_id"), x["st"], float(b)))
            continue
        if m > 0:       # 0 = loại không gọi AI ⇒ bỏ
            ra.append((st.get("node_id"), x["st"], float(m)))
    return ra


def chu_ra(s: str) -> str:
    # bảng node không ghi loại khối (node_type = GenericNode / custom) ⇒ lấy chữ ra của mọi khối, kèm tên hiển thị
    return psql("select coalesce(string_agg(coalesce(n.node_display_name,'?') || ': ' || left(coalesce(nr.output_data->>'value', ''), 4000), "
                "E'\\n---\\n' order by nr.created_at), '') from node_result nr "
                "join workflow_run r on r.id=nr.run_id join node n on n.id=nr.node_id "
                f"where r.session_id='{s}'")


def chay_1(org, catalog, ten, dat_agent, cau):
    if da_tieu() >= tran_hieu_luc():
        ghi(f"[S5 {ten}] DỪNG: đã tiêu {da_tieu():.4f} ≥ trần {tran_hieu_luc()}")
        return {"ten": ten, "dung_vi_tran": True}
    v = Ve(catalog)
    vao = v.khoi("text_input_node", "Câu hỏi", 0, 0, {"text_input": cau})
    a = v.khoi("agent_node", f"Agent {ten}", 300, 0, dict(CHUNG, **dat_agent))
    v.noi(vao, a, "user_prompt")
    v.noi(a, v.khoi("text_output_node", "Xuất", 600, 0), "text_content")
    pl, c, wf, r = luu(org, v, f"S5 {ten}")
    if c >= 300:
        return {"ten": ten, "luu_http": c, "luu": str(r)[:300]}
    _, ses = goi("GET", "/session/create-session")
    s = ses["session_id"]
    t0 = time.time()
    goi("POST", f"/workflow/{org}/{wf}/{s}/run-workflow", dict(pl, session_id=s))
    st = ""
    for _ in range(150):
        st = psql(f"select status from workflow_run where session_id='{s}'")
        if st and st not in ("RUNNING", "PENDING", "QUEUED"):
            break
        time.sleep(2)
    t = tien_luot(s)
    tong = ghi_tien(ten, t["usd"], {"session": s, "workflow_id": wf, **{k: v_ for k, v_ in t.items() if k != "usd"}})
    out = {"ten": ten, "workflow_id": wf, "session": s, "status": st, "giay": round(time.time() - t0), **t,
           "loi": psql(f"select left(coalesce(error_message,''),400) from workflow_run where session_id='{s}'"), "chu_ra": chu_ra(s)}
    ghi(f"[S5 {ten}] {st} {out['giay']}s; {t['lan_goi']} lần gọi AI, {t['token_vao']}/{t['token_ra']} token, {t['usd']:.5f} USD "
        f"(cộng dồn {tong:.5f}); công cụ: {t['cong_cu']}")
    return out


def dap_an_L1() -> dict:
    ma = ("import openpyxl,json,collections\nws=openpyxl.load_workbook('/srv/du_lieu/excel/don_hang_gia.xlsx',read_only=True,data_only=True).active\n"
          "r=list(ws.iter_rows(values_only=True));h=r[0];print(json.dumps({'tieu_de':[str(x) for x in h],'dong':[list(map(str,x)) for x in r[1:]]},ensure_ascii=False))")
    p = subprocess.run(["docker", "exec", "-w", "/srv/src", "capitol-ab-worker-temporal", "python", "-c", ma],
                       capture_output=True, text=True, timeout=60)
    return json.loads(p.stdout.strip().splitlines()[-1])


def main() -> int:
    if os.environ.get("DA_DUYET") != "co":
        print("Chưa duyệt. Chạy lại: DA_DUYET=co python3 -I chay_thu/sandbox/chay_s5.py")
        return 1
    BUOI.mkdir(parents=True, exist_ok=True)
    org = (KQ / "org_id.txt").read_text().strip()
    catalog = json.loads((KQ / "06_11_sang" / "s4" / "danh_muc_khoi.json").read_text())
    ca = os.environ.get("CA", "L1,L2,L3").split(",")
    kq = {}
    if "L1" in ca:
        kq["L1_du_lieu"] = dap_an_L1()   # bảng gốc để A / D tự tính đáp án, so với câu trả lời
        kq["L1"] = chay_1(org, catalog, "L1_chay_code", {
            "system_prompt": "Bạn là trợ lý phân tích bán hàng. Mọi con số phải TÍNH bằng công cụ chạy code, không tự nhẩm. Trả lời tiếng Việt, ngắn.",
            "tool_run_python": True, "tool_ma_file": "don_hang_gia.xlsx"},
            "File Excel đơn hàng của shop: với mỗi sản phẩm, tính tổng số lượng và tổng tiền (số lượng × đơn giá). "
            "Liệt kê đủ các sản phẩm, sắp tổng tiền giảm dần, và cho biết tổng tiền của cả file.")
    if "L2" in ca:
        kq["L2"] = chay_1(org, catalog, "L2_sql", {
            "system_prompt": "Bạn là nhân viên chăm sóc khách của shop. Số tiền phải lấy từ công cụ, không tự cộng. Trả lời tiếng Việt, ngắn.",
            "tool_sql_query": True, "tool_customer_id": "KH-LAN"},
            "Chào shop, các đơn của mình tổng cộng bao nhiêu tiền vậy?")
        kq["L2"]["co_696000"] = any(x in kq["L2"].get("chu_ra", "") for x in ("696.000", "696,000", "696000"))
        kq["L2"]["goi_sql"] = "sql_query" in kq["L2"].get("cong_cu", [])
    if "L3" in ca:
        kq["L3"] = chay_1(org, catalog, os.environ.get("TEN_L3", "L3_file_dep"), {
            "system_prompt": "Bạn soạn tài liệu trình bày cho ban giám đốc. Dùng công cụ ghi file; mỗi loại file ghi một lần.",
            "tool_make_pdf": True, "tool_make_pptx": True},
            "Làm 1 file PDF (2 trang) và 1 file PowerPoint (5–6 trang) tóm tắt cho ban giám đốc chủ đề 'Thuế quan EU với hàng dệt may "
            "Việt Nam' từ dữ liệu sau (dữ liệu giả để thử, đừng thêm số khác): thuế MFN trung bình 12%; theo EVFTA lộ trình về 0% "
            "trong 7 năm từ 2020; năm 2024 đã giảm còn 4,8%; điều kiện: quy tắc xuất xứ từ vải; 3 rủi ro: quy tắc xuất xứ, "
            "tiêu chuẩn xanh EU, cạnh tranh Bangladesh. Kết luận: ưu tiên nguồn vải trong khối.")
        ra = (KQ / "xuat_file")
        kq["L3"]["file_moi"] = sorted(p.name for p in ra.glob("*") if p.stat().st_mtime > time.time() - 600 and p.suffix in (".pdf", ".pptx"))
    (BUOI / f"s5_{'_'.join(ca)}.json").write_text(json.dumps(kq, ensure_ascii=False, indent=2, default=str))
    ghi(f"[S5] xong {ca}; đã tiêu {da_tieu():.5f} / {TRAN} USD — {BUOI}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
