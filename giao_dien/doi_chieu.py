"""Đối chiếu câu trả lời của khối Agent với nguồn — cải tiến (1), yêu cầu vận hành 04/11/2026. Bản 2 (sửa theo D kiểm độc lập, BAO_CAO_KET_QUA mục 16).

Vì sao: khuôn đầu ra chỉ ép HÌNH DẠNG, không ép ĐÚNG (bên mekongai đo: gpt-6-luna bịa số mà vẫn điền khuôn đẹp). Bộ này soát bằng code:
mọi SỐ, NGÀY, GIỜ, THỨ, ĐƯỜNG LINK, MÃ trong câu trả lời phải có trong nguồn của CHÍNH khối đó — kết quả công cụ (mạnh nhất), đầu vào khối
nhận (câu nhờ thật), lời dặn hệ thống (yếu: tác giả viết — đếm riêng, không coi là đủ).

Học ý (không chép code) từ mekongai-template kpi_agent/agent/number_check.py: đọc một số theo mọi cách viết, khớp theo số chữ số lẻ đã viết.
Luật số tiền (người dùng hai bên chốt 04/11, giữa 11:05 và 11:08): tiền ĐÚNG CHÍNH XÁC tới đồng, không ngoại lệ "khoảng"; % làm tròn tới đúng
số chữ số lẻ đã viết. Bộ này so theo chữ số lẻ ĐÃ VIẾT sau khi nhân đơn vị ⇒ "5,7 triệu" cho 5.740.000 KHÔNG khớp (đúng luật).

Bản 2 sửa (đối chiếu kỹ thuật): (L1) không bỏ số nhỏ 0–10; (L3) nguồn công cụ bỏ trường `query` (câu AI tự viết) và bỏ kết quả `ok: false` (lời báo lỗi
nhắc lại mã AI đưa vào) — do hàm `nguon_cong_cu` làm; (L4) đọc ngày dd/mm/yyyy, dd-mm-yyyy, yyyy-mm-dd, "ngày 12 tháng 11 năm 2026",
"12 tháng 11"; thêm GIỜ hh:mm và THỨ; mã không phân biệt hoa thường, có / không gạch; link so "khớp gần" khi chỉ khác tham số theo dõi
(srsltid, utm_*, gclid, fbclid) hoặc http ↔ https; số không có nguồn mà bằng phép tính đơn giản của hai số nguồn (a×k, a+b, a−b) ⇒ nhãn
"có thể là phép tính — cần kiểm" (vd 378.000 = 2 × 189.000).
Giới hạn còn lại (nói thật): (L2) mọi số của mọi công cụ vẫn là một túi — số bịa trùng một số bất kỳ trong kết quả tra web vẫn lọt;
chỉ kiểm "có trong nguồn", KHÔNG kiểm gắn đúng đối tượng, KHÔNG kiểm nghĩa (vd hiểu sai "khách đã ghi size L") — phần nghĩa chấm ở bộ ca thử.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlsplit

HE_SO = {"tỷ": 10 ** 9, "ty": 10 ** 9, "triệu": 10 ** 6, "tr": 10 ** 6, "nghìn": 1000, "ngàn": 1000, "k": 1000}
URL = re.compile(r"https?://[^\s\"'<>)\]]+")
MA = re.compile(r"(?<![\w-])([A-Za-z]{2,3})-?(\d{3,})(?![\w-])")   # DH-1042 / dh1042; không nhận "nay 04" (đối chiếu kỹ thuật)
# ngày: dd/mm(/yyyy) · dd-mm-yyyy · yyyy-mm-dd · "ngày 12 tháng 11 (năm 2026)" · "12 tháng 11"
NGAY_DMY = re.compile(r"(?<![\d/-])(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?(?![\d/-])")
NGAY_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
NGAY_CHU = re.compile(r"(?:ngày\s+)?(\d{1,2})\s+tháng\s+(\d{1,2})(?:\s*(?:năm|,)?\s*(\d{4}))?", re.IGNORECASE)
GIO = re.compile(r"(?<!\d)(\d{1,2})[:h](\d{2})(?!\d)")
THU = re.compile(r"\b(thứ\s+(?:hai|ba|tư|năm|sáu|bảy)|chủ\s+nhật)\b", re.IGNORECASE)
SO = re.compile(r"(?<![\w.,/-])(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![0-9_/])(?:\s*(tỷ|ty|triệu|tr|nghìn|ngàn|k)(?![A-Za-zà-ỹ]))?", re.IGNORECASE)
# Đọc số trong NGUỒN lỏng hơn: chữ lấy từ web hay dính liền ("FullTag Mác217.000₫" — báo nhầm thật ở bộ ca thử P10 04/11) ⇒ cho phép số đứng
# ngay sau chữ cái. Câu trả lời vẫn đọc chặt (SO) để không tách nhầm mã / tên.
SO_NGUON = re.compile(r"(?<![\d.,/-])(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![0-9_/])(?:\s*(tỷ|ty|triệu|tr|nghìn|ngàn|k)(?![A-Za-zà-ỹ]))?", re.IGNORECASE)
SO_THU_TU = re.compile(r"(?m)^\s*\d+[.)]\s")
THEO_DOI = re.compile(r"^(utm_.*|srsltid|gclid|fbclid|ref|ref_src)$", re.IGNORECASE)
NGUON = ("cong_cu", "dau_vao", "loi_dan")


# ---------------------------------------------------------------- nguồn
def nguon_cong_cu(ket_qua: list[str]) -> str:
    """Kết quả công cụ ⇒ chữ nguồn. Bỏ kết quả ok:false và trường `query` (đối chiếu kỹ thuật)."""
    ra = []
    for c in ket_qua:
        try:
            d = json.loads(c)
        except (ValueError, TypeError):
            ra.append(c)
            continue
        if isinstance(d, dict):
            if d.get("ok") is False:
                continue
            d = {k: v for k, v in d.items() if k not in ("query", "error")}
        ra.append(json.dumps(d, ensure_ascii=False))
    return "\n".join(ra)


# ---------------------------------------------------------------- đọc từng loại ý
def _doc_so(chuoi: str, he_so: str | None) -> list[tuple[Decimal, int]]:
    k = Decimal(HE_SO.get((he_so or "").lower(), 1))
    if re.fullmatch(r"\d+", chuoi):
        return [(Decimal(chuoi) * k, 0)]
    ra = []
    for thap_phan, nhom in ((",", "."), (".", ",")):
        phan = re.split(r"[.,]", chuoi)
        if nhom in chuoi and thap_phan not in chuoi and all(len(p) == 3 for p in phan[1:]):
            ra.append((Decimal("".join(phan)) * k, 0))
        if thap_phan in chuoi:
            dau, _, duoi = chuoi.rpartition(thap_phan)
            nhom_dau = dau.split(nhom)
            if thap_phan not in dau and nhom not in duoi and all(len(p) == 3 for p in nhom_dau[1:]):
                try:
                    ra.append((Decimal("".join(nhom_dau) + "." + duoi) * k, len(duoi)))
                except InvalidOperation:
                    pass
    return ra


def _ngay(text: str) -> list[tuple[str, tuple]]:
    """[(chữ gốc, (ngày, tháng, năm | None))] — đủ các dạng; trả cả vị trí để xoá trước khi đọc số."""
    ra = []
    for m in NGAY_ISO.finditer(text):
        ra.append((m.group(0), (int(m.group(3)), int(m.group(2)), int(m.group(1)))))
    for m in NGAY_CHU.finditer(text):
        ra.append((m.group(0), (int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)))
    t2 = NGAY_ISO.sub(" ", NGAY_CHU.sub(" ", text))
    for m in NGAY_DMY.finditer(t2):
        d, mo = int(m.group(1)), int(m.group(2))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            ra.append((m.group(0), (d, mo, int(m.group(3)) if m.group(3) else None)))
    return ra


def _ma(text: str) -> list[str]:
    return [f"{a.upper()}-{b}" for a, b in MA.findall(text)]


def _chuan_url(u: str, gan: bool = False) -> str:
    u = u.rstrip(".,;:").rstrip("/").replace("\\/", "/")
    if not gan:
        return u
    p = urlsplit(u)
    q = urlencode([(k, v) for k, v in parse_qsl(p.query) if not THEO_DOI.match(k)])
    return f"{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?{q}" if q else "")


def _xoa_khong_phai_so(text: str) -> str:
    """Bỏ phần đã đọc riêng (link, mã, ngày, giờ, số thứ tự đầu dòng) trước khi đọc số — để không đếm hai lần."""
    text = URL.sub(" ", text)
    text = MA.sub(" ", text)
    for goc, _ in _ngay(text):
        text = text.replace(goc, " ")
    text = GIO.sub(" ", text)
    return SO_THU_TU.sub(" ", text)


def _cac_so(text: str) -> set[Decimal]:
    out = {Decimal(k[2]) for _, k in _ngay(text) if k[2]}       # năm trong ngày của nguồn cũng là nguồn cho "năm 2026" đứng riêng
    for m in SO_NGUON.finditer(_xoa_khong_phai_so(text)):
        for v, _ in _doc_so(m.group(1), m.group(2)):
            out.add(v)
    return out


def _co_the_la_phep_tinh(v: Decimal, nguon_so: set[Decimal]) -> bool:
    """v có thể là phép tính từ số trong nguồn ⇒ cần người kiểm. Một bước: a × k (k 2..12), a × b, a ± b. Hai bước (đối chiếu kỹ thuật): (a × k) ± c, (a × k) + (b × m). Chỉ cho số ≥ 100 (số nhỏ: phép tính nào cũng ra được)."""
    if v < 100:
        return False
    so = sorted(s for s in nguon_so if s != 0)[:300]
    tap = set(so)
    nhan = {a * k for a in so for k in range(2, 13)} | {a * b for a in so for b in so if a <= 1000 and b <= 1000}
    mot_buoc = tap | nhan
    if v in nhan or any((v - a) in tap or (a - v) in tap or (v + a) in tap for a in so):
        return True
    return any((v - x) in mot_buoc for x in mot_buoc)


# ---------------------------------------------------------------- đối chiếu
def doi_chieu(cau_tra_loi: str, nguon: dict[str, str]) -> dict:
    """nguon = {"cong_cu": nguon_cong_cu([...]), "dau_vao": câu nhờ thật, "loi_dan": lời dặn hệ thống}.
    Trả {"y": [{kieu, gia_tri, nguon: cong_cu|dau_vao|loi_dan|phep_tinh|None, gan: bool}], "tong", "khong_nguon", "nguon_yeu", "phep_tinh"}."""
    y = []

    def tim(ok) -> str | None:
        return next((ten for ten in NGUON if nguon.get(ten) and ok(nguon[ten])), None)

    # đường link: khớp nguyên ⇒ tốt; chỉ khác tham số theo dõi / http↔https ⇒ "khớp gần"
    for u in dict.fromkeys(_chuan_url(x) for x in URL.findall(cau_tra_loi)):
        hit = tim(lambda s, u=u: u in {_chuan_url(x) for x in URL.findall(s.replace("\\/", "/"))})
        gan = False
        if not hit:
            g = _chuan_url(u, gan=True)
            hit = tim(lambda s, g=g: g in {_chuan_url(x, gan=True) for x in URL.findall(s.replace("\\/", "/"))})
            gan = bool(hit)
        y.append({"kieu": "đường link", "gia_tri": u, "nguon": hit, "gan": gan})
    # mã (DH-1042, dh1042, DH 1042 cùng là một)
    nguon_ma = {ten: set(_ma(nguon.get(ten) or "")) for ten in NGUON}
    for ma in dict.fromkeys(_ma(URL.sub(" ", cau_tra_loi))):
        y.append({"kieu": "mã", "gia_tri": ma, "nguon": next((t for t in NGUON if ma in nguon_ma[t]), None), "gan": False})
    # ngày
    nguon_ngay = {ten: [k for _, k in _ngay(nguon.get(ten) or "")] for ten in NGUON}
    da = set()
    for goc, k in _ngay(URL.sub(" ", cau_tra_loi)):
        if k in da:
            continue
        da.add(k)
        hit = next((t for t in NGUON if any(x[:2] == k[:2] and (k[2] is None or x[2] in (None, k[2])) for x in nguon_ngay[t])), None)
        y.append({"kieu": "ngày", "gia_tri": goc.strip(), "nguon": hit, "gan": False})
    # giờ
    nguon_gio = {ten: {(int(a), int(b)) for a, b in GIO.findall(nguon.get(ten) or "")} for ten in NGUON}
    for a, b in dict.fromkeys(GIO.findall(URL.sub(" ", cau_tra_loi))):
        k = (int(a), int(b))
        y.append({"kieu": "giờ", "gia_tri": f"{a}:{b}", "nguon": next((t for t in NGUON if k in nguon_gio[t]), None), "gan": False})
    # thứ trong tuần
    for th in dict.fromkeys(x.lower() for x in THU.findall(cau_tra_loi)):
        y.append({"kieu": "thứ", "gia_tri": th, "nguon": tim(lambda s, th=th: th in s.lower()), "gan": False})
    # số
    nguon_so = {ten: _cac_so(nguon.get(ten) or "") for ten in NGUON}
    # Phép tính chỉ ghép từ số của nguồn "tinh" (công cụ dữ liệu nội bộ, vd tra đơn) + đầu vào — KHÔNG từ đoạn trích web: web nhiều số,
    # ghép 2 bước ra gần như mọi giá (thử 04/11: giá bịa 95.000₫ lọt thành "phép tính"). Không có "tinh" ⇒ dùng mọi nguồn (cách cũ).
    tat_ca_so = (_cac_so(nguon["tinh"]) | nguon_so["dau_vao"]) if "tinh" in nguon else set().union(*nguon_so.values())
    for m in SO.finditer(_xoa_khong_phai_so(cau_tra_loi)):
        cach_doc = _doc_so(m.group(1), m.group(2))
        if not cach_doc:
            continue
        hit = None
        for ten in NGUON:
            if any(any(s.quantize(Decimal(1).scaleb(-le)) == v.quantize(Decimal(1).scaleb(-le)) for s in nguon_so[ten]) for v, le in cach_doc):
                hit = ten
                break
        if not hit and any(_co_the_la_phep_tinh(v, tat_ca_so) for v, le in cach_doc if le == 0):
            hit = "phep_tinh"
        y.append({"kieu": "số", "gia_tri": m.group(0).strip(), "nguon": hit, "gan": False})
    return {"y": y, "tong": len(y), "khong_nguon": sum(1 for x in y if not x["nguon"]),
            "nguon_yeu": sum(1 for x in y if x["nguon"] == "loi_dan"), "phep_tinh": sum(1 for x in y if x["nguon"] == "phep_tinh")}


def doi_chieu_khuon(dau_ra: str, nguon: dict[str, str]) -> list[dict]:
    """Khuôn có trường `nguon` / `sources` / `source_urls` (danh sách link) ⇒ từng link phải có trong KẾT QUẢ CÔNG CỤ."""
    try:
        d = json.loads(dau_ra)
    except (ValueError, TypeError):
        return []
    if not isinstance(d, dict):
        return []
    s = (nguon.get("cong_cu") or "").replace("\\/", "/")
    nguyen = {_chuan_url(x) for x in URL.findall(s)}
    gan = {_chuan_url(x, gan=True) for x in URL.findall(s)}
    ra = []
    for truong in ("nguon", "sources", "source_urls"):
        for u in d.get(truong) or []:
            if isinstance(u, str):
                ok = _chuan_url(u) in nguyen
                ok_gan = not ok and _chuan_url(u, gan=True) in gan
                ra.append({"truong": truong, "gia_tri": u, "co_trong_ket_qua_cong_cu": ok or ok_gan, "gan": ok_gan})
    return ra
