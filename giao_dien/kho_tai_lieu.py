"""Kho tài liệu của trang 18400 — "nạp vào kho rồi hỏi" bằng app RAG mekongai (đối chiếu kỹ thuật). File này THUẦN logic + việc nạp nền; mọi lời gọi ra ngoài (app RAG, sổ tiền, nhật ký) TRUYỀN VÀO ⇒ thử được 0 đồng.

Luật (vì sao):
  - Tổ chức ⇒ user_id DO CODE GẮN: "capitol_" + sha256(org)[:32] (đối chiếu kỹ thuật). Người dùng không gõ / chọn user_id. Lớp gắn THẬT nằm ở hộp cổng RAG (B, E major 1); đây là lớp thứ hai.
  - Tên file là DỮ LIỆU: chỉ giữ tên đã làm sạch để HIỆN; đường lưu bản sao tra bằng sha256 nội dung, không bao giờ ghép tên vào đường dẫn; tên không vào nhật ký.
  - Nạp tốn tiền (nhóm phát triển xác nhận 17/11: chữ sẵn ~0,00002 USD / trang; trang quét ~0,0006 / trang; trần 200 trang ⇒ < ~0,15 / file) ⇒ GIỮ CHỖ MỨC TỐI ĐA
    trước khi gọi app, ghi sổ theo mức tối đa (app không trả tiền thật cho bên gọi) — luật 3.9. Nạp nặng ⇒ MỘT việc nạp mỗi lúc (khoá).
  - Kho tạm ("tải rồi hỏi ngay"): hạn 24 giờ; hết hạn chỉ ẨN khỏi danh sách chọn, ghi "đã ẩn, chưa xoá" (app chưa có đường xoá — nhóm phát triển xác nhận 17/11).
  - Không bao giờ gọi /collections/all của app.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path

DUOI = {".pdf", ".docx", ".txt", ".md", ".markdown"}   # giao của app RAG (SUPPORTED_SUFFIXES) và loại "tài liệu chữ"; bảng tính đi đường KPI / kho bảng
# [ghi chú kỹ thuật] đuôi CHỈ khung chat nhận thêm (ảnh rời, trang web, JSON, bảng tính) — tab Tài liệu giữ DUOI cũ; khung chat đã kiểm đầu file ở doc_tai_lieu
DUOI_CHAT = DUOI | {".png", ".jpg", ".jpeg", ".html", ".htm", ".json", ".xlsx", ".xlsm", ".xls", ".csv"}
CO_TOI_DA = 20 * 1024 * 1024                          # = rào _tai_len_tep của trang (app cho tới 100 MB)
TRANG_TOI_DA = 100                                    # [19/11] người dùng bên mekongai duyệt nạp "có trần": > ~100 trang phải hỏi trước ⇒ cổng mình chặn ở 100 (trước: 200)
def loi_nap_cho_nguoi(code, ly_do, cong_tu_choi: bool) -> str:
    """[ghi chú kỹ thuật] Lời hiện cho người dùng khi nạp tài liệu không được. 429 = bị PHANH (không phải lỗi của file) => lời thường + việc nên làm.
    Cổng không cho biết còn bao lâu => nói thật "sau ít phút, lâu nhất khoảng một giờ" (trần tính theo giờ trượt), không bịa số phút."""
    ly = " ".join(str(ly_do or "").split())[:200]
    if code == 429:
        m = re.search(r"quá (\d+) lần nạp / giờ", ly)
        if m:
            return (f"Bạn vừa đưa nhiều tài liệu liên tiếp nên hệ thống tạm ngừng nhận file mới (giới hạn {m.group(1)} file mỗi giờ). "
                    "Tài liệu này CHƯA được đọc và file không có lỗi — bạn đưa lại sau ít phút (lâu nhất khoảng một giờ).")
        return ly or "Hệ thống đang tạm ngừng nhận tài liệu mới — bạn đưa lại sau ít phút."   # chốt trang / ngày: lời của cổng đã là lời thường
    return f"{'Cổng RAG từ chối' if cong_tu_choi else 'App RAG không nạp được'} (mã {code}): " + ly


TRANG_NGAY_TOI_DA = 400                               # chốt trang nạp / ngày: tiền nạp nay ghi SỔ BÊN MEKONGAI, trần 0,50 USD / ngày, ~0,0008 USD / trang chụp
RAM_IT_NHAT_MB = 4000                                 # RAM khả dụng dưới mức này ⇒ không nạp (nạp bản chụp nặng máy; máy từng treo)
GHI_TIEN_NAP = False                                  # [19/11] KHÔNG giữ chỗ / ghi tiền nạp vào sổ bên mình nữa (thoả thuận 17/11 "ghi 0,0006 USD / trang" hết hiệu lực)
GIA_TRANG_TOI_DA = 0.0006                             # USD / trang quét (số đo mekongai) ⇒ mức tối đa = số trang × giá này
MUC_FILE_TOI_DA = 0.15                                # USD / file khi không đếm được trang (≥ 200 × 0,0006 = 0,12, làm tròn lên)
HAN_TAM_GIAY = 24 * 3600
GIAY_CHO_APP = 900                                    # app nạp ĐỒNG BỘ — script đo của mekongai chờ tới 900 s
_MA = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA = re.compile(r"[0-9a-f]{64}")
_KHOA_NAP = threading.Lock()
_VIEC: dict[str, dict] = {}                           # mã việc ⇒ trạng thái (bộ nhớ tiến trình; may_chu bật lại ⇒ mất — sổ kho thì bền)


def ram_kha_dung_mb():
    """MemAvailable (MB) từ /proc/meminfo; không đọc được ⇒ None (không chặn vì lý do này)."""
    try:
        for dong in Path("/proc/meminfo").read_text().splitlines():
            if dong.startswith("MemAvailable:"):
                return int(dong.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def user_id_cua(org: str) -> str:
    if not _MA.fullmatch(str(org)):
        raise ValueError("mã tổ chức không đúng khuôn")
    return "capitol_" + hashlib.sha256(org.encode()).hexdigest()[:32]


def lam_sach_ten(ten: str, duoi_nhan=None) -> str | None:
    """Tên để HIỆN + gửi app (app tự làm sạch lần nữa). Bỏ đường dẫn, ký tự điều khiển, ngoặc kép, dấu `|`; ≤ 120 ký tự; phải có đuôi trong DUOI
    (hoặc trong duoi_nhan nếu người gọi truyền — khung chat truyền DUOI_CHAT)."""
    if not isinstance(ten, str):
        return None
    # [thử 17/11] kiểm TRƯỚC khi cắt / bỏ khoảng trắng: tên có đường dẫn (/ \\) hay ký tự điều khiển (xuống dòng…) ⇒ TỪ CHỐI, không "sửa giùm"
    # [ghi chú kỹ thuật] CÙNG luật với cổng RAG (va_rag_to_chuc/rag_cong.py kiem_ten_file): cấm thêm ";" và "..", > 120 ký tự TỪ CHỐI (trước đây cắt
    # còn 120 ⇒ có thể mất đuôi) ⇒ tên cổng sẽ cấm bị chặn ở đây (422, TRƯỚC giữ chỗ) thay vì cổng trả 400 sau khi đã giữ chỗ.
    if any(ord(c) < 32 or ord(c) == 127 or c in '"|`<>/\\;' for c in ten):
        return None
    ten = ten.strip()
    if not ten or ten.startswith(".") or ".." in ten or len(ten) > 120:
        return None
    duoi = ("." + ten.rsplit(".", 1)[-1].lower()) if "." in ten else ""
    if duoi not in (duoi_nhan or DUOI):
        return None
    return ten


def dem_trang_pdf(than: bytes) -> int | None:
    """Đếm trang PDF thô (không thư viện) — CHỈ tin khi hai cách đếm KHỚP NHAU [ghi chú kỹ thuật]: (1) số đối tượng /Type /Page, (2) /Count lớn nhất của nút /Type /Pages. Có /ObjStm (đối tượng nén), thiếu một
    trong hai, hoặc lệch nhau ⇒ None ⇒ dùng MUC_FILE_TOI_DA (0,15)."""
    if b"/ObjStm" in than:
        return None
    n = len(re.findall(rb"/Type\s*/Page(?![s\w])", than))
    dem = [int(x) for x in re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", than)] +\
          [int(x) for x in re.findall(rb"/Count\s+(\d+)[^>]*?/Type\s*/Pages\b", than)]
    if not n or not dem or max(dem) != n:
        return None
    return n


def dem_trang_chac(than: bytes) -> int | None:
    """[ghi chú kỹ thuật] Số trang PDF theo pdfinfo (cùng cách cổng RAG đếm) — đọc file từ đầu vào chuẩn, có hạn giờ + CPU + bộ nhớ + cỡ đầu ra.
    Không chạy được / không ra số ⇒ None (người gọi rơi về phép đếm thô)."""
    import resource, subprocess   # noqa: E401

    def han():
        resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3)); resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 ** 2, 1024 ** 2)); resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        r = subprocess.run(["pdfinfo", "-"], input=than, capture_output=True, timeout=30, preexec_fn=han)
        if r.returncode != 0:
            return None
        m = re.search(rb"^Pages:\s+(\d+)\s*$", r.stdout, re.M)
        return int(m.group(1)) if m and 0 < int(m.group(1)) < 1_000_000 else None
    except Exception:  # noqa: BLE001
        return None


def muc_toi_da(ten: str, than: bytes) -> tuple[float, int | None]:
    trang = dem_trang_pdf(than) if ten.lower().endswith(".pdf") else None
    if trang is None:
        return MUC_FILE_TOI_DA, None
    return round(min(MUC_FILE_TOI_DA, trang * GIA_TRANG_TOI_DA) + 0.005, 6), trang   # +0,005: lề cho câu hỏi kiểm sau nạp (nếu app có)


class So:
    """Sổ kho của trang (JSON, quyền 600) + bản sao file theo sha (thư mục 700), chia theo user_id của tổ chức."""

    def __init__(self, goc: Path):
        self.goc = Path(goc)

    def _thu_muc(self, org: str) -> Path:
        d = self.goc / user_id_cua(org)
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
        return d

    def _doc(self, org: str) -> dict:
        f = self._thu_muc(org) / "so_kho.json"
        return json.loads(f.read_text()) if f.is_file() else {}

    def _ghi(self, org: str, d: dict) -> None:
        f = self._thu_muc(org) / "so_kho.json"
        t = f.with_suffix(".tam")
        t.write_text(json.dumps(d, ensure_ascii=False, indent=1)); t.chmod(0o600); t.replace(f)

    def luu_ban_sao(self, org: str, sha: str, duoi: str, than: bytes) -> Path:
        assert _SHA.fullmatch(sha) and duoi in DUOI_CHAT
        f = self._thu_muc(org) / f"{sha}{duoi}"
        if not f.is_file():
            t = f.with_suffix(".tam_ghi"); t.write_bytes(than); t.chmod(0o600); t.replace(f)
        return f

    def them(self, org: str, sha: str, muc: dict) -> None:
        d = self._doc(org); d[sha] = muc; self._ghi(org, d)

    def ds(self, org: str, bay_gio: float | None = None) -> list[dict]:
        """Danh sách kho ĐANG DÙNG được của tổ chức + kho tạm đã ẩn (đánh dấu). Không bao giờ hỏi app (không /all)."""
        bay_gio = time.time() if bay_gio is None else bay_gio
        ra = []
        for sha, m in self._doc(org).items():
            if not m.get("kho"):
                continue
            an = bool(m.get("tam")) and bay_gio > float(m.get("het_han") or 0)
            ra.append({"sha": sha, "ten": m.get("ten"), "kho": m["kho"], "trang": m.get("trang"), "luc": m.get("luc"), "tam": bool(m.get("tam")),
                       "an": an, "ghi_chu": "đã ẩn, chưa xoá" if an else ""})
        return sorted(ra, key=lambda x: x.get("luc") or 0, reverse=True)

    def kho_hop_le(self, org: str, kho: list[str], bay_gio: float | None = None) -> tuple[bool, list[str]]:
        """Mọi tên kho phải nằm trong sổ của ĐÚNG tổ chức và chưa ẩn. Trả (đạt, các tên bị từ chối)."""
        ok = {x["kho"] for x in self.ds(org, bay_gio) if not x["an"]}
        sai = [k for k in kho if k not in ok]
        return not sai and bool(kho), sai

    # [ghi chú kỹ thuật] tiền lượt TÌM (tạo vector câu hỏi) cũng ghi sổ BÊN HỌ ⇒ bên mình chỉ ĐẾM số lần tìm theo ngày để cuối ngày báo
    def ghi_tim(self, org: str, kho: str, http: int, so_trang: int) -> None:
        self.goc.mkdir(parents=True, exist_ok=True)
        with open(self.goc / f"tim_{time.strftime('%Y-%m-%d')}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"luc": time.time(), "org8": hashlib.sha256(org.encode()).hexdigest()[:8], "kho": kho, "http": http, "so_trang": so_trang}, ensure_ascii=False) + "\n")

    def tong_ket_ngay(self, ngay: str | None = None) -> dict:
        """Số báo mekongai cuối ngày: số file + số trang đã nạp, số lần tìm, danh sách kho đã tạo (user_id, tên kho, giờ, số trang)."""
        ngay = ngay or time.strftime("%Y-%m-%d")
        def doc(ten):
            try:
                return [json.loads(d) for d in (self.goc / ten).read_text(encoding="utf-8").splitlines() if d.strip()]
            except FileNotFoundError:
                return []
        nap, tim = doc(f"nap_{ngay}.jsonl"), doc(f"tim_{ngay}.jsonl")
        return {"ngay": ngay, "so_file_nap": len(nap), "so_trang_nap": sum(int(x.get("trang") or 0) for x in nap), "so_lan_tim": len(tim),
                "kho": [{"user_id": x.get("user_id"), "kho": x.get("kho"), "gio": time.strftime("%H:%M:%S", time.localtime(float(x.get("luc") or 0))), "trang": x.get("trang"), "tt": x.get("tt")} for x in nap]}

    # [ghi chú kỹ thuật] SỔ NẠP theo ngày (mọi tổ chức chung một chốt — một máy, một dịch vụ đọc): để chặn ở TRANG_NGAY_TOI_DA và để báo mekongai
    def _tep_nap(self, ngay: str | None = None) -> Path:
        return self.goc / f"nap_{ngay or time.strftime('%Y-%m-%d')}.jsonl"

    def trang_hom_nay(self) -> int:
        try:
            return sum(int(json.loads(d).get("trang") or 0) for d in self._tep_nap().read_text(encoding="utf-8").splitlines() if d.strip())
        except FileNotFoundError:
            return 0

    def ghi_nap(self, org: str, muc: dict) -> None:
        self.goc.mkdir(parents=True, exist_ok=True)
        with open(self._tep_nap(), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(muc, org8=hashlib.sha256(org.encode()).hexdigest()[:8]), ensure_ascii=False) + "\n")

    def duong_mo(self, org: str, sha: str) -> tuple[Path, str] | None:
        """Bản sao để mở nguồn: chỉ khi sha có trong sổ của ĐÚNG tổ chức. Trả (đường, tên hiện)."""
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            return None
        m = self._doc(org).get(sha)
        if not m:
            return None
        f = self._thu_muc(org) / f"{sha}{m.get('duoi', '')}"
        return (f, m.get("ten") or "tai_lieu") if f.is_file() else None


def bat_dau_nap(org: str, ten: str, than: bytes, *, tam: bool, so: So, goi_app, H, ghi, chay_nen=True, duoi_nhan=None, nap_lai=True) -> tuple[int, dict]:
    """Kiểm (0 đồng) ⇒ giữ chỗ mức tối đa ⇒ chạy nền: gọi app nạp ⇒ đọc kho mới của user ⇒ ghi sổ kho + sổ tiền ⇒ bỏ chỗ.
    goi_app(phương thức, đường, form=None, tep=None, timeout) ⇒ (http, json). H = hoi_kpi (giu_cho / tra_cho / ghi_tien / _khoa_so).
    Trả (http, {"ma": mã việc} | {"loi"})."""
    try:
        uid = user_id_cua(org)
    except ValueError:
        return 400, {"loi": "tổ chức không hợp lệ"}
    sach = lam_sach_ten(ten, duoi_nhan)   # [ghi chú kỹ thuật] duoi_nhan: khung chat mở thêm đuôi; nap_lai=False: cùng nội dung ⇒ app dùng lại kho cũ (không tạo hai kho)
    if sach is None:
        return 422, {"loi": "Tên / đuôi file không được nhận (chỉ nhận " + ", ".join(sorted(duoi_nhan or DUOI)) + ")."}
    if not 1 <= len(than) <= CO_TOI_DA:
        return 413, {"loi": "File phải từ 1 byte tới 20 MB."}
    muc, trang = muc_toi_da(sach, than)
    if sach.lower().endswith(".pdf"):   # [ghi chú kỹ thuật] số trang CHẮC (pdfinfo) thắng phép đếm thô — cho chốt 100 trang, chốt trang / ngày và sổ nạp
        trang = dem_trang_chac(than) or trang
    tho = len(re.findall(rb"/Type\s*/Page(?![s\w])", than)) if sach.lower().endswith(".pdf") else 0   # đếm thô là CẬN DƯỚI ⇒ > trần thì chắc chắn quá trần
    if max(trang or 0, tho) > TRANG_TOI_DA:
        trang = max(trang or 0, tho)
        return 413, {"loi": f"File có khoảng {trang} trang — hơn {TRANG_TOI_DA} trang thì phải hỏi người phụ trách dịch vụ đọc tài liệu trước khi nạp. "
                            "Bạn tách file thành phần nhỏ hơn hoặc báo người phụ trách nhé."}
    ram = ram_kha_dung_mb()
    if ram is not None and ram < RAM_IT_NHAT_MB:
        return 503, {"loi": f"Máy chủ đang bận (bộ nhớ trống còn {ram} MB) nên chưa đọc file này được. Bạn thử lại sau ít phút nhé."}
    da_nap = so.trang_hom_nay()
    if da_nap + (trang or 1) > TRANG_NGAY_TOI_DA:
        return 429, {"loi": f"Hôm nay dịch vụ đọc tài liệu đã đọc {da_nap} trang, thêm file này ({trang or 1} trang) sẽ quá mức {TRANG_NGAY_TOI_DA} trang / ngày. "
                            "Bạn thử lại vào ngày mai hoặc báo người phụ trách nhé."}
    if not _KHOA_NAP.acquire(blocking=False):
        return 409, {"loi": "Đang nạp một file khác (nạp nặng — mỗi lúc một file). Chờ xong rồi nạp tiếp."}
    ma_giu = None
    if GHI_TIEN_NAP:   # đường cũ (17/11): tiền nạp ghi sổ mình — nay tắt, giữ code để bật lại nếu thoả thuận đổi
        try:
            ma_giu, tong = H.giu_cho(muc, "kho_tai_lieu")
        except Exception:  # noqa: BLE001
            ma_giu, tong = None, 0
        if ma_giu is None:
            _KHOA_NAP.release()
            return 402, {"loi": f"Sắp vượt trần tiền AI hôm nay (đã tiêu + đang giữ {tong:.4f} + mức tối đa lần nạp {muc:.4f} > trần) — chưa nạp."}
    sha = hashlib.sha256(than).hexdigest()
    duoi = "." + sach.rsplit(".", 1)[-1].lower()
    try:
        so.luu_ban_sao(org, sha, duoi, than)
    except Exception as e:  # noqa: BLE001 — chưa gọi app ⇒ bỏ chỗ + mở khoá, không tốn đồng nào
        if ma_giu:
            H.tra_cho(ma_giu)
        _KHOA_NAP.release()
        return 500, {"loi": f"Không lưu được bản sao file ({type(e).__name__}) — chưa nạp."}
    ma = str(uuid.uuid4())
    _VIEC[ma] = {"org": org, "tt": "dang_nap", "ten": sach, "trang": trang, "muc": muc, "bat_dau": time.time()}

    def _chay():
        tt = _VIEC[ma]
        da_goi = False
        try:
            # [ghi chú kỹ thuật] nhớ kho đã có TRƯỚC khi nạp ⇒ sau nạp lấy kho MỚI của đúng tên file (2 file trùng tên cùng tổ chức không gắn nhầm kho cũ)
            c0, d0 = goi_app("GET", f"/api/v1/qdrant/collections/user/{uid}", timeout=30)
            truoc = {x.get("collection_name") for x in ((d0 or {}).get("data") or []) if isinstance(x, dict)} if c0 == 200 else set()
            tien_to = f"CHATBOT_{uid}_"   # [ghi chú kỹ thuật] kho của TỔ CHỨC NÀY luôn mang tiền tố này (app đặt tên kho theo user_id; cổng RAG cũng chỉ nhận tiền tố này)

            def chon_kho():
                """Kho của file vừa nạp trong danh sách của tổ chức — CHỈ kho mang tiền tố của mình. Trả (kho | None, số kho của user khác bị bỏ qua)."""
                c2, d2 = goi_app("GET", f"/api/v1/qdrant/collections/user/{uid}", timeout=30)
                ds = (d2 or {}).get("data") if c2 == 200 else None
                ds = [x for x in (ds if isinstance(ds, list) else []) if isinstance(x, dict) and isinstance(x.get("collection_name"), str)]
                la = [x for x in ds if not x["collection_name"].startswith(tien_to) and (x.get("file_name") == sach or x.get("file_hash") == sha)]
                ds = [x for x in ds if x["collection_name"].startswith(tien_to)]
                cung_ten = [x["collection_name"] for x in ds if x.get("file_name") == sach]
                moi_ = [k for k in cung_ten if k not in truoc]
                theo_bam = [x["collection_name"] for x in ds if x.get("file_hash") == sha]   # [ghi chú kỹ thuật] app ghi file_hash = sha256 nội dung
                return (moi_[-1] if moi_ else (theo_bam[-1] if theo_bam else (cung_ten[-1] if cung_ten else None))), len(la)

            code, d = goi_app("POST", "/api/v1/upload-files", form={"user_id": uid, "language": "vie+eng", "force_reindex": "true" if nap_lai else "false"},
                              tep=(sach, than), timeout=GIAY_CHO_APP)
            # [ghi chú kỹ thuật] cổng từ chối TRƯỚC app (header X-Rag-Cong: tu_choi ⇒ goi_cong gắn _cong_tu_choi; E đo: không có sau khi đã gửi app)
            # ⇒ app chưa nhận gì ⇒ KHÔNG ghi sổ (trước đây ghi mức tối đa ⇒ mỗi lần từ chối ăn tới 0,15 trần ngày)
            da_goi = not (isinstance(d, dict) and d.get("_cong_tu_choi"))
            if code != 200:
                ly_do = (d or {}).get("message") or (d or {}).get("loi") or ""   # [ghi chú kỹ thuật] cổng trả khoá "loi", app trả "message"
                tt.update(tt="loi", loi=loi_nap_cho_nguoi(code, ly_do, not da_goi))
                return
            kho, so_la = chon_kho()
            if not kho and not nap_lai:
                # app "dùng lại" kho của USER KHÁC có cùng nội dung (tra trùng theo nội dung, không theo user) ⇒ kho đó không dùng được cho tổ chức này (cổng từ chối)
                # ⇒ nạp LẠI một lần, ép tạo kho riêng. Đã đo 20/11: file Excel thật ⇒ app trả kho CHATBOT_thu_01_11_c_sql_… ⇒ lượt tìm 400.
                ghi(f"KHO TÀI LIỆU {ma}: dịch vụ đọc dùng lại kho của user khác ({so_la} kho bị bỏ qua) ⇒ nạp lại để có kho riêng của tổ chức")
                code, d = goi_app("POST", "/api/v1/upload-files", form={"user_id": uid, "language": "vie+eng", "force_reindex": "true"}, tep=(sach, than), timeout=GIAY_CHO_APP)
                if code != 200:
                    ly_do = (d or {}).get("message") or (d or {}).get("loi") or ""
                    tt.update(tt="loi", loi=loi_nap_cho_nguoi(code, ly_do, isinstance(d, dict) and bool(d.get("_cong_tu_choi"))))
                    return
                kho, so_la = chon_kho()
            if not kho:
                tt.update(tt="loi", loi="App báo nạp xong nhưng không tìm thấy kho của file trong danh sách của tổ chức.")
                return
            cu = so._doc(org).get(sha) or {}
            ben = bool(cu) and cu.get("kho") == kho and not cu.get("tam")   # [ghi chú kỹ thuật] kho BỀN đã có của tab Tài liệu được dùng lại ⇒ giữ bền, không đổi thành kho tạm
            so.them(org, sha, {"ten": sach, "duoi": duoi, "kho": kho, "trang": trang, "luc": time.time(), "tam": tam and not ben,
                               "het_han": (time.time() + HAN_TAM_GIAY) if tam and not ben else None})
            tt.update(tt="xong", kho=kho)
        except Exception as e:  # noqa: BLE001 — quá giờ / mất kết nối: app CÓ THỂ đã nạp + tiêu tiền ⇒ vẫn ghi mức tối đa
            da_goi = True
            tt.update(tt="loi", loi=f"Lỗi khi nạp ({type(e).__name__}) — có thể app vẫn đang nạp; xem lại danh sách kho sau ít phút.")
        finally:
            try:
                if da_goi:   # app ĐÃ nhận file (kể cả khi sau đó lỗi / quá giờ — có thể đã đọc và tốn tiền bên họ) ⇒ đếm trang vào chốt ngày + sổ nạp để báo mekongai
                    so.ghi_nap(org, {"viec": ma, "trang": trang or 1, "dem_duoc_trang": bool(trang), "byte": len(than), "tam": tam, "tt": tt.get("tt"), "kho": tt.get("kho"),
                                     "user_id": uid, "giay": round(time.time() - tt["bat_dau"], 1), "luc": time.time()})
                if GHI_TIEN_NAP and da_goi:
                    with H._khoa_so:
                        H.ghi_tien("kho_tai_lieu", muc, {"session": ma, "ghi_chu": "nạp file vào kho tài liệu (app RAG mekongai) — ghi MỨC TỐI ĐA "
                                                         f"({'%d trang × %.4f' % (trang, GIA_TRANG_TOI_DA) if trang else 'không đếm được trang'})"})
                if ma_giu:
                    H.tra_cho(ma_giu)
            except Exception as e:  # noqa: BLE001 — ghi sổ lỗi: KHÔNG bỏ chỗ (chỗ giữ còn chặn thay)
                ghi(f"KHO TÀI LIỆU {ma}: GHI SỔ LỖI {type(e).__name__} — giữ nguyên chỗ {muc}")
            tt["xong_luc"] = time.time()
            _KHOA_NAP.release()

    if chay_nen:
        threading.Thread(target=_chay, daemon=True, name="nap_kho_tai_lieu").start()
    else:
        _chay()
    ghi(f"KHO TÀI LIỆU: bắt đầu nạp {ma} ({len(than)} byte, {trang or '?'} trang{', tạm' if tam else ''}; tiền nạp ghi sổ bên dịch vụ đọc tài liệu)")   # không tên file
    return 202, {"ma": ma, "muc_toi_da": muc, "trang": trang}


def trang_thai(org: str, ma: str) -> tuple[int, dict]:
    v = _VIEC.get(ma) if isinstance(ma, str) else None
    if not v or v["org"] != org:
        return 404, {"loi": "không có việc nạp này"}
    return 200, {k: v.get(k) for k in ("tt", "ten", "trang", "muc", "kho", "loi", "bat_dau", "xong_luc")}
