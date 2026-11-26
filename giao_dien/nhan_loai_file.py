"""[C 20–26/11/2026 — Trang "Dữ liệu" & kỳ trống fdd20ab0] ĐỌC THỬ một file người dùng kéo vào phần "Số liệu để tính thưởng KPI" để ĐOÁN:
đây là loại file gì (`raw` | `chi_tieu` | `so_tracking` | `trang_chi_tieu` | `cong_van` | `khong_ro`), của tháng nào, số liệu tới ngày nào.
Chỉ ĐỌC, không gửi file đi đâu, không gọi AI. Thư viện chuẩn.
  nhan(ten, du_lieu) ⇒ {"loai", "ten_loai", "ho_tro": bool, "ky": "YYYY-MM" | None, "ngay_chot": "YYYY-MM-DD" | None, "vi_sao", "ghi_chu"}
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path

CO_TOI_DA = 20 * 1024 * 1024
CO_TRANG_TOI_DA = 5 * 1024 * 1024
BUNG_TOI_DA, SO_PHAN_TU_TOI_DA, SHEET_DOC_TOI_DA = 150 * 1024 * 1024, 5000, 48 * 1024 * 1024
TEN_LOAI = {
    "raw": "File đơn hàng",
    "chi_tieu": "File chỉ tiêu nhân viên",
    "so_tracking": "Sổ theo dõi (Tracking)",
    "trang_chi_tieu": "Trang theo dõi chỉ tiêu (.html)",
    "cong_van": "Công văn / tài liệu luật thưởng (PDF)",
    "khong_ro": "Chưa nhận ra loại file",
}
CHUA_HO_TRO = {
    "cong_van": "Hệ thống nhận ra đây là công văn / tài liệu luật thưởng, nhưng việc đọc công văn thành luật tính thưởng chưa tự làm được qua trang — đang làm.",
}
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_KHUON = None


def _chuan(s) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(s))).strip().lower()


def _goi_y() -> dict:
    """{id trường: [tên cột hay gặp]} của bảng đơn hàng (va_ghep_cot/khuon_chuan.json — cùng nguồn màn ghép cột)."""
    global _KHUON
    if _KHUON is None:
        try:
            k = json.loads((Path(__file__).resolve().parent.parent / "va_ghep_cot" / "khuon_chuan.json").read_text(encoding="utf-8"))
            _KHUON = {t["id"]: ([_chuan(g) for g in t["goi_y"]], bool(t.get("bat_buoc"))) for t in k["bang"]["don_hang"]["truong"]}
        except (OSError, ValueError, KeyError, TypeError):
            _KHUON = {}
    return _KHUON


def _doc_xlsx(b: bytes) -> dict:
    """{tên sheet: [[ô…]…]} — bộ đọc tối giản (zip + XML), có chốt cỡ; ô số trả số, ô chữ trả chữ. Lỗi ⇒ ValueError(lời thường)."""
    import xml.etree.ElementTree as ET
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        muc = z.infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ValueError("Không mở được file Excel này (file hỏng hoặc có mật khẩu).") from None
    ten = {i.filename for i in muc}
    if len(muc) > SO_PHAN_TU_TOI_DA or sum(i.file_size for i in muc) > BUNG_TOI_DA or "xl/workbook.xml" not in ten:
        raise ValueError("File Excel này bất thường (không đúng cấu trúc hoặc quá lớn sau giải nén) — hệ thống không mở.")
    ss = []
    if "xl/sharedStrings.xml" in ten:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_NS + "si"):
            ss.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rel = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))} if "xl/_rels/workbook.xml.rels" in ten else {}
    ra = {}
    for sh in wb.iter(_NS + "sheet"):
        t = rel.get(sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")) or ""
        t = t.lstrip("/")
        t = t if t.startswith("xl/") else "xl/" + t
        if t not in ten or z.getinfo(t).file_size > SHEET_DOC_TOI_DA:
            ra[sh.get("name") or ""] = []
            continue
        rows = []
        for r in ET.fromstring(z.read(t)).iter(_NS + "row"):
            d, i = {}, -1
            for c in r.findall(_NS + "c"):
                ref = c.get("r")
                if ref:
                    i = 0
                    for ch in re.match(r"[A-Z]+", ref).group():
                        i = i * 26 + ord(ch) - 64
                    i -= 1
                else:
                    i += 1
                v, typ = c.find(_NS + "v"), c.get("t")
                if typ == "inlineStr":
                    d[i] = "".join(x.text or "" for x in c.iter(_NS + "t"))
                elif v is None or v.text is None:
                    continue
                elif typ == "s":
                    d[i] = ss[int(v.text)] if v.text.isdigit() and int(v.text) < len(ss) else ""
                elif typ in (None, "n"):
                    try:
                        d[i] = float(v.text)
                    except ValueError:
                        d[i] = v.text
                else:
                    d[i] = v.text
            rows.append([d.get(k, "") for k in range(max(d) + 1)] if d else [])
        ra[sh.get("name") or ""] = rows
    return ra


def _ngay(v) -> date | None:
    """Giá trị ô ⇒ ngày: số ngày của Excel (40000–60000), hoặc chữ yyyy-mm-dd / dd/mm/yyyy / dd-mm-yyyy."""
    if isinstance(v, float) and 40000 <= v <= 60000:
        return date(1899, 12, 30) + timedelta(days=int(v))
    s = str(v).strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s) or None
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d) if 2015 <= y <= 2100 else None
    except ValueError:
        return None


def _ra(loai, ky=None, ngay_chot=None, vi_sao=""):
    return {
        "loai": loai,
        "ten_loai": TEN_LOAI[loai],
        "ho_tro": loai in ("raw", "chi_tieu", "so_tracking", "trang_chi_tieu"),
        "ky": ky,
        "ngay_chot": ngay_chot,
        "vi_sao": vi_sao,
        "ghi_chu": CHUA_HO_TRO.get(loai),
    }


def loi_theo_duoi(t: str) -> str:
    """File không hợp lệ kéo vào phần số liệu tính thưởng ⇒ MỘT câu đúng với đuôi đó."""
    t = (t or "").lower()
    duoi = ("." + t.rsplit(".", 1)[-1]) if "." in t else ""
    so_theo_doi = (
        " Nếu đây là sổ theo dõi (Tracking): mở bằng Excel rồi “Lưu thành” .xlsx và kéo lại — hệ thống nhận sổ theo dõi dạng .xlsx."
        if "tracking" in t
        else ""
    )
    if duoi == ".xlsb":
        return "File Excel dạng nhị phân (.xlsb) hệ thống chưa đọc được. Bạn mở bằng Excel rồi “Lưu thành” .xlsx và kéo lại." + so_theo_doi
    if duoi in (".xls", ".xlsm", ".csv", ".ods"):
        return f"Phần số liệu tính thưởng chỉ nhận file Excel .xlsx (file này là {duoi}). Bạn mở file bằng Excel rồi “Lưu thành” .xlsx và kéo lại." + so_theo_doi
    if duoi in (".docx", ".txt", ".md"):
        return "Đây là file văn bản, không phải bảng số liệu. Muốn hỏi về nội dung của nó: đưa vào phần “Tài liệu để hỏi đáp” bên dưới."
    if duoi in (".png", ".jpg", ".jpeg", ".json"):
        return "Phần số liệu tính thưởng chỉ nhận file Excel .xlsx hoặc trang theo dõi .html. Muốn hỏi về nội dung file này: kéo thẳng vào khung Trợ lý ở góc dưới bên phải."
    return "Phần số liệu tính thưởng chỉ nhận file Excel .xlsx hoặc trang theo dõi .html; loại file này hệ thống chưa nhận ở đây." + so_theo_doi


def nhan(ten: str, du_lieu: bytes) -> dict:
    t = (ten or "").lower()
    if not du_lieu or len(du_lieu) > CO_TOI_DA:
        raise ValueError("File phải từ 1 byte tới 20 MB.")
    if t.endswith(".pdf"):
        if du_lieu[:5] != b"%PDF-":
            raise ValueError("File có đuôi .pdf nhưng nội dung không phải PDF.")
        return _ra("cong_van", vi_sao="file PDF — thường là công văn / chương trình thưởng")
    if t.endswith((".html", ".htm")):
        if len(du_lieu) > CO_TRANG_TOI_DA:
            raise ValueError("Trang theo dõi (.html) không được quá 5 MB.")
        if len(du_lieu) < 4:
            raise ValueError("File .html quá nhỏ (cần tối thiểu 4 byte).")
        if du_lieu[:4] == b"PK\x03\x04":
            raise ValueError("File có đuôi .html nhưng nội dung là file Excel/nén — hãy chọn đúng trang web (.html).")
        if not du_lieu.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
            raise ValueError("File .html không bắt đầu bằng thẻ HTML ('<').")
        ten_goc = t.replace("\\", "/").rsplit("/", 1)[-1]
        nhac = (
            "lưu ý chọn đúng file saved_resource.html trong thư mục <tên trang>_files (file lớn), không phải file .html bên ngoài hay saved_resource(1).html"
            if ten_goc != "saved_resource.html"
            else "đúng tên file saved_resource.html (nhớ chọn file trong thư mục <tên trang>_files)"
        )
        return _ra("trang_chi_tieu", vi_sao=f"trang web (.html) — {nhac}")
    if not t.endswith(".xlsx"):
        raise ValueError(loi_theo_duoi(t))
    if du_lieu[:4] != b"PK\x03\x04":
        raise ValueError("File có đuôi .xlsx nhưng nội dung không phải file Excel.")
    sach = _doc_xlsx(du_lieu)
    goi_y = _goi_y()
    for ten_sheet, rows in sach.items():                       # 1. file đơn hàng: dòng tiêu đề trong 30 dòng đầu của một sheet
        for vt, r in enumerate(rows[:30]):
            co = {_chuan(c): i for i, c in enumerate(r) if str(c).strip()}
            trung = sum(1 for ds, bb in goi_y.values() if bb and any(g in co for g in ds))
            if goi_y and trung >= 8:
                cot_ngay = next((co[g] for k in ("order_date", "invoiced_at") for g in goi_y.get(k, ([], 0))[0] if g in co), None)
                lon = None
                if cot_ngay is not None:
                    for d in rows[vt + 1:]:
                        n = _ngay(d[cot_ngay]) if cot_ngay < len(d) else None
                        if n and (lon is None or n > lon):
                            lon = n
                return _ra(
                    "raw",
                    lon and f"{lon:%Y-%m}",
                    lon and lon.isoformat(),
                    f"sheet “{ten_sheet}” có {trung} / {sum(1 for _, bb in goi_y.values() if bb)} cột bắt buộc của bảng đơn hàng"
                    + (f"; ngày đơn mới nhất trong file là {lon:%d/%m/%Y}" if lon else "; không đọc được cột ngày đơn"),
                )
    for ten_sheet, rows in sach.items():                       # 2. sổ theo dõi (Tracking) ⇒ so_tracking (đã hỗ trợ qua trang)
        dau = " ".join(str(c) for r in rows[:12] for c in r)
        m = re.search(r"KPIs?\s+TH[ÁA]NG\s+(\d{1,2})\s*/\s*(\d{4})", dau, flags=re.I)
        if _chuan(ten_sheet) == "9_kpi" or m:
            return _ra(
                "so_tracking",
                f"{int(m.group(2)):04d}-{int(m.group(1)):02d}" if m else None,
                None,
                f"sheet “{ten_sheet}” là sổ theo dõi (Tracking)" + (f" tháng {int(m.group(1))}/{m.group(2)}" if m else ""),
            )
    for ten_sheet, rows in sach.items():                       # 3. file chỉ tiêu nhân viên
        for vt, r in enumerate(rows[:30]):
            co = {_chuan(c): i for i, c in enumerate(r) if str(c).strip()}
            if "month" in co and "sm code" in co:
                ky = None
                for d in rows[vt + 1:vt + 50]:
                    v = d[co["month"]] if co["month"] < len(d) else ""
                    n = _ngay(v)
                    m = re.fullmatch(r"(\d{1,2})\s*[/\-.]\s*(\d{4})", str(v).strip()) or re.fullmatch(r"(\d{4})\s*[/\-.]?\s*(\d{2})", str(v).strip().split(".")[0])
                    if n:
                        ky = f"{n:%Y-%m}"
                    elif m:
                        a, b2 = int(m.group(1)), int(m.group(2))
                        ky = f"{b2:04d}-{a:02d}" if a <= 12 else (f"{a:04d}-{b2:02d}" if 1 <= b2 <= 12 else None)
                    if ky:
                        break
                return _ra(
                    "chi_tieu",
                    ky,
                    None,
                    f"sheet “{ten_sheet}” có cột Month và SM code (chỉ tiêu theo nhân viên)" + ("" if ky else "; chưa đọc được tháng — bạn chọn tháng giúp"),
                )
    return _ra("khong_ro", vi_sao="không thấy bộ cột của file đơn hàng, file chỉ tiêu hay sổ theo dõi")
