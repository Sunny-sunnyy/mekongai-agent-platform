"""[ghi chú kỹ thuật]
Đổi file bảng dạng chữ (.csv / .tsv) thành .xlsx để BẤM ĐÚP mở bằng Excel là ra đúng cột — không cần thao tác gì. Thư viện chuẩn (zip + XML), không chạy gì
trong file, không gọi mạng. File gốc KHÔNG bị sửa (bản .csv vẫn tải được nguyên từng byte ở nút bên cạnh).
  chuyen(du_lieu: bytes, ten: str) ⇒ (tên .xlsx, byte .xlsx, {"dong", "cot", "tach", "ma_hoa", "cot_so": [...]})      lỗi ⇒ KhongChuyen(lời thường)
Luật (vì sao):
  - mã hoá: BOM UTF-8 / UTF-16 ⇒ theo BOM; không thì thử UTF-8, hỏng thì cp1258 (Windows tiếng Việt) rồi latin-1;
  - dấu tách: thử , ; tab | trên 30 dòng đầu bằng bộ đọc CSV chuẩn (hiểu ô trong ngoặc kép), chọn dấu cho NHIỀU cột nhất mà số cột các dòng ĐỀU nhau nhất; .tsv ⇒ tab;
  - dòng đầu là tiêu đề ⇒ in đậm + giữ cố định khi cuộn;
  - cột là SỐ chỉ khi MỌI ô có dữ liệu của cột đều là số viết gọn (−12, 3.5) — khi đó ghi ô số (cộng / lọc được). Giữ dạng CHỮ: mã có số 0 đứng đầu (0123), dãy ≥ 12 chữ số
    (số thẻ, mã vạch — Excel làm tròn số quá 15 chữ số), số điện thoại có dấu +, số có dấu chấm / phẩy ngăn nghìn hay phẩy thập phân (1.234.567 · 12,5 — không đoán
    kiểu viết), ngày viết kiểu dd/mm/yyyy (mơ hồ ngày ⇄ tháng — giữ nguyên chữ);
  - cột mà MỌI ô là ngày dạng ISO (2026-08-26, 2026-08-26 14:30:00) ⇒ ghi Ô NGÀY có định dạng yyyy-mm-dd (lọc / sắp theo ngày được); một ô sai dạng ⇒ cả cột giữ chữ;
  - ô chữ ghi kiểu "chuỗi trong ô" ⇒ Excel KHÔNG coi chữ bắt đầu bằng = + - @ là công thức (không cần thêm dấu nháy, không đổi nội dung);
  - không khoá sheet, không mật khẩu, không macro.
Chốt: file ≤ 20 MB, ≤ 200.000 dòng, ≤ 1.000 cột, ô ≤ 32.767 ký tự (giới hạn của Excel) — vượt ⇒ KhongChuyen nói rõ (bản .csv gốc vẫn tải được)."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.sax.saxutils import escape

CO_TOI_DA, DONG_TOI_DA, COT_TOI_DA, O_TOI_DA = 20 * 1024 * 1024, 200_000, 1_000, 32_767
DUOI = (".csv", ".tsv")
_SO = re.compile(r"-?(0|[1-9]\d{0,10})(\.\d{1,10})?")          # số viết gọn: tối đa 11 chữ số phần nguyên, không số 0 đứng đầu
_NGAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")   # CHỈ dạng ISO năm-tháng-ngày [giờ] — dạng dd/mm ⇄ mm/dd mơ hồ thì giữ chữ
_KY_TU_CAM = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")   # ký tự XML không cho phép


class KhongChuyen(ValueError):
    """Không đổi được sang .xlsx — chữ của lỗi là câu nói với người dùng."""


def giai_ma(b: bytes) -> tuple[str, str]:
    if b[:3] == b"\xef\xbb\xbf":
        return b[3:].decode("utf-8", "replace"), "UTF-8 (có BOM)"
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", "replace"), "UTF-16"
    for ma, ten in (("utf-8", "UTF-8"), ("cp1258", "Windows-1258"), ("latin-1", "Latin-1")):
        try:
            return b.decode(ma), ten
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1", "replace"), "Latin-1"


def do_dau_tach(chu: str, duoi: str) -> str:
    if duoi == ".tsv":
        return "\t"
    dau = "\n".join(chu.splitlines()[:30])
    tot, diem_tot = ",", (-1, -1.0, -1)
    for d in (",", ";", "\t", "|"):
        try:
            so = [len(r) for r in csv.reader(io.StringIO(dau), delimiter=d) if r and any(x.strip() for x in r)]
        except csv.Error:
            continue
        if not so:
            continue
        pho = max(set(so), key=so.count)
        diem = (int(pho > 1), so.count(pho) / len(so), pho)      # có tách được cột · các dòng đều nhau · nhiều cột
        if diem > diem_tot:
            tot, diem_tot = d, diem
    return tot


def _so_ngay(v: str):
    """Chữ ISO ⇒ (số ngày kiểu Excel, có giờ hay không) hoặc None nếu không phải ngày hợp lệ (vd 2026-02-30)."""
    m = _NGAY.fullmatch(v)
    if not m:
        return None
    from datetime import datetime
    y, mo, d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    try:
        t = datetime(y, mo, d, h, mi, se)
    except ValueError:
        return None
    if not 1900 < y < 2200:
        return None
    k = t - datetime(1899, 12, 30)
    co_gio = m.group(4) is not None and (h or mi or se)
    return (k.days + k.seconds / 86400) if co_gio else k.days, bool(co_gio)


def _ten_cot(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _xml(s: str) -> str:
    return escape(_KY_TU_CAM.sub("", s))


def chuyen(du_lieu: bytes, ten: str) -> tuple[str, bytes, dict]:
    t = (ten or "").lower()
    duoi = next((d for d in DUOI if t.endswith(d)), None)
    if duoi is None:
        raise KhongChuyen("Chỉ đổi được file .csv / .tsv sang Excel.")
    if not du_lieu:
        raise KhongChuyen("File này trống.")
    if len(du_lieu) > CO_TOI_DA:
        raise KhongChuyen(f"File lớn {len(du_lieu) / 2 ** 20:.1f} MB — quá mức đổi sang Excel ({CO_TOI_DA // 2 ** 20} MB). Bạn tải bản .csv gốc rồi mở bằng Dữ liệu ⇒ Từ văn bản / CSV.".replace(".", ",", 1))
    chu, ma_hoa = giai_ma(du_lieu)
    tach = do_dau_tach(chu, duoi)
    try:
        csv.field_size_limit(10_000_000)
        dong = [r for r in csv.reader(io.StringIO(chu, newline=""), delimiter=tach)]
    except csv.Error as e:
        raise KhongChuyen(f"Không đọc được file bảng này ({str(e)[:80]}).") from None
    while dong and not any(x.strip() for x in dong[-1]):
        dong.pop()                                             # bỏ dòng trống ở CUỐI file; dòng trống ở giữa giữ nguyên vị trí
    if not dong:
        raise KhongChuyen("File này không có dòng dữ liệu nào.")
    if len(dong) > DONG_TOI_DA:
        raise KhongChuyen(f"File có {len(dong):,} dòng — quá mức đổi sang Excel ({DONG_TOI_DA:,} dòng). Bạn tải bản .csv gốc rồi mở bằng Dữ liệu ⇒ Từ văn bản / CSV.".replace(",", "."))
    so_cot = max(len(r) for r in dong)
    if so_cot > COT_TOI_DA:
        raise KhongChuyen(f"File có {so_cot} cột — quá mức đổi sang Excel ({COT_TOI_DA} cột).")
    if any(len(x) > O_TOI_DA for r in dong for x in r):
        raise KhongChuyen(f"Có ô dài hơn {O_TOI_DA:,} ký tự (giới hạn một ô của Excel) — không đổi được; bạn tải bản .csv gốc.".replace(",", "."))
    la_so = []
    for c in range(so_cot):                                    # cột số: MỌI ô có dữ liệu (trừ dòng tiêu đề) là số viết gọn, và có ít nhất một ô
        o = [r[c].strip() for r in dong[1:] if c < len(r) and r[c].strip()]
        la_so.append(bool(o) and all(_SO.fullmatch(x) for x in o))
    la_ngay = []                                               # [ghi chú kỹ thuật] cột NGÀY: mọi ô có dữ liệu đúng dạng ISO và là ngày có thật ⇒ ghi ô ngày (lọc / sắp theo ngày được)
    for c in range(so_cot):
        o = [r[c].strip() for r in dong[1:] if c < len(r) and r[c].strip()]
        la_ngay.append(bool(o) and not la_so[c] and all(_so_ngay(x) is not None for x in o))
    rong = [min(60, max([len(r[c]) for r in dong[:2000] if c < len(r)] + [6]) + 2) for c in range(so_cot)]
    ra = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
          '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'
          + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(rong)) + "</cols><sheetData>"]
    for i, r in enumerate(dong):
        o = []
        for c, v in enumerate(r):
            if v == "":
                continue
            ref = f"{_ten_cot(c)}{i + 1}"
            if i == 0:
                o.append(f'<c r="{ref}" s="1" t="inlineStr"><is><t xml:space="preserve">{_xml(v)}</t></is></c>')
            elif la_so[c] and _SO.fullmatch(v.strip()):
                o.append(f'<c r="{ref}"><v>{v.strip()}</v></c>')
            elif la_ngay[c] and _so_ngay(v.strip()) is not None:
                so_n, co_gio = _so_ngay(v.strip())
                o.append(f'<c r="{ref}" s="{3 if co_gio else 2}"><v>{so_n!r}</v></c>')
            else:
                o.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_xml(v)}</t></is></c>')
        ra.append(f'<row r="{i + 1}">' + "".join(o) + "</row>")
    ra.append("</sheetData></worksheet>")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Dữ liệu" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd"/><numFmt numFmtId="165" formatCode="yyyy\\-mm\\-dd\\ hh:mm:ss"/></numFmts>'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
                   '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
                   # [ghi chú kỹ thuật] kiểu mặc định "Normal" — thiếu thì thư viện đọc báo "Workbook contains no default style"
                   '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        z.writestr("xl/worksheets/sheet1.xml", "".join(ra))
    goc = (ten or "bang").rsplit("/", 1)[-1]
    ten_moi = (goc[:-len(duoi)] if goc.lower().endswith(duoi) else goc) + ".xlsx"
    return ten_moi, b.getvalue(), {"dong": len(dong), "cot": so_cot, "tach": {",": "dấu phẩy", ";": "dấu chấm phẩy", "\t": "tab", "|": "dấu |"}[tach], "ma_hoa": ma_hoa,
                                   "cot_so": [dong[0][c] if c < len(dong[0]) else _ten_cot(c) for c in range(so_cot) if la_so[c]],
                                   "cot_ngay": [dong[0][c] if c < len(dong[0]) else _ten_cot(c) for c in range(so_cot) if la_ngay[c]]}
