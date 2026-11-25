"""[ghi chú kỹ thuật] Bốn đường MÀN GHÉP CỘT của trang 18400 ⇒ hộp chuyển tiếp 127.0.0.1:18402 (chuyen_tiep.py của B,
449e834f) ⇒ kpi-backend (lõi mekongai 1e7202d4: khuon / bo-cuc / ghep-thu / ghep-xac-nhan).

Vì sao tách file: may_chu.py chỉ thêm 2 dòng định tuyến; mọi phép kiểm nằm ở đây để thử được 0 đồng với hộp 18402 GIẢ (thu_ghep_kpi.py).
Đường đi:  trang ⇒ /api/kpi/ghep/<khuon|bo-cuc|thu|xac-nhan> ⇒ (file này kiểm khuôn) ⇒ 18402 kèm X-Tai-Len-Ma ⇒ kpi-backend kèm khoá hệ thống.
Luật chặn Ở ĐÂY (trước khi chạm 18402):
  - Host phải là 127.0.0.1:<cổng trang> / localhost:<cổng trang> (chống DNS rebinding — như /api/kpi/tai-len);
  - bảng CỐ ĐỊNH "don_hang" — không lấy tên bảng từ người gọi;
  - bo-cuc: thân octet-stream 4 byte…20 MB, 4 byte đầu PK\\x03\\x04; sheet_so + dong_tieu_de là số 1–3 chữ số, đi cùng nhau;
  - thu / xac-nhan: thân application/json ≤ 64 KB, là object, CHỈ các khoá đã biết, đúng kiểu (danh sách ở _kiem_thu / _kiem_xac_nhan);
  - thiếu file mã 600 ⇒ 503, không gửi gì.
Trả về: NGUYÊN mã HTTP + JSON của lõi (chữ do code lõi viết, tên lạ / mã NV đã che ở lõi) — trang hiện nguyên chữ, không tự diễn giải.
KHÔNG có lượt AI nào (0 đồng); KHÔNG đưa columns / chan / gia_tri_moi vào lời dặn hay lượt mô hình. Nhật ký: chỉ đường + cỡ + mã HTTP
(thân JSON có tên người xác nhận + câu trả lời kiểm ⇒ không ghi; tên file không bao giờ gửi đi).
Giới hạn: chưa có màn ghép GIÁ TRỊ (kênh / cờ lạ ⇒ lõi chặn, trang hiện chữ `chan`); chỉ bảng don_hang (RAW)."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import mo_khong_doi_huong   # [ghi chú kỹ thuật] cùng thư mục giao_dien/

GOC_18402 = "http://127.0.0.1:18402"
BANG = "don_hang"
TRAN_FILE = 20 * 1024 * 1024
TRAN_JSON = 64 * 1024
_SO_NHO = re.compile(r"\d{1,3}")
_SHA = re.compile(r"[0-9a-f]{64}")
_MA = re.compile(r"[0-9a-f]{32}")
_KY = re.compile(r"\d{4}-\d{2}")
_NGAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_MOI = re.compile(r"moi_\d{1,4}")
_TRUONG = re.compile(r"[a-z_]{1,40}")
DUONG = {"/api/kpi/ghep/khuon": ("GET", "khuon"), "/api/kpi/ghep/bo-cuc": ("POST", "bo-cuc"),
         "/api/kpi/ghep/thu": ("POST", "thu"), "/api/kpi/ghep/xac-nhan": ("POST", "xac-nhan")}


def _so(v, nho, lon):
    return isinstance(v, int) and not isinstance(v, bool) and nho <= v <= lon


def _cot(v):
    """Một cột trong bảng ghép: VỊ TRÍ 1-based (tên lạ bị che nên người ghép theo số) hoặc TÊN cột đã biết (chuỗi ngắn)."""
    return _so(v, 1, 2000) or (isinstance(v, str) and 0 < len(v.strip()) <= 80)


def _kiem_thu(b):
    """Thân ghép thử từ trang ⇒ (thân gửi lõi, None) | (None, lý do). Khoá cho phép: sha_file, ghep{sheet, header_row, columns}, ky, ngay_chot,
    khong_co_cot_sau_ck. `bang` do máy chủ tự điền. KHÔNG nhận ghep.values / accepted_values (chưa có màn ghép giá trị — xem Giới hạn)."""
    if not isinstance(b, dict) or set(b) - {"sha_file", "ghep", "ky", "ngay_chot", "khong_co_cot_sau_ck"}:
        return None, "Thân ghép thử chỉ nhận: sha_file, ghep, ky, ngay_chot, khong_co_cot_sau_ck."
    if not (isinstance(b.get("sha_file"), str) and _SHA.fullmatch(b["sha_file"])):
        return None, "sha_file sai dạng (phải là mã 64 ký tự do bước đọc bố cục trả)."
    if not (isinstance(b.get("ky"), str) and _KY.fullmatch(b["ky"])):
        return None, "Kỳ phải có dạng năm-tháng (YYYY-MM)."
    if not (isinstance(b.get("ngay_chot"), str) and _NGAY.fullmatch(b["ngay_chot"])):
        return None, "Ngày chốt phải có dạng năm-tháng-ngày."
    if not isinstance(b.get("khong_co_cot_sau_ck", False), bool):
        return None, "khong_co_cot_sau_ck phải là đúng / sai."
    g = b.get("ghep")
    if not isinstance(g, dict) or set(g) - {"sheet", "header_row", "columns"}:
        return None, "Bảng ghép chỉ nhận: sheet, header_row, columns."
    if not (_so(g.get("sheet"), 1, 999) or (isinstance(g.get("sheet"), str) and 0 < len(g["sheet"].strip()) <= 80)):
        return None, "Bảng ghép thiếu sheet (tên hoặc số thứ tự)."
    if not _so(g.get("header_row"), 1, 999):
        return None, "Bảng ghép thiếu dòng tiêu đề (số ≥ 1)."
    c = g.get("columns")
    if not isinstance(c, dict) or not c or len(c) > 60 or any(not (isinstance(k, str) and _TRUONG.fullmatch(k)) or not _cot(v) for k, v in c.items()):
        return None, "Bảng ghép: mỗi trường phải có một cột (vị trí hoặc tên)."
    return {"sha_file": b["sha_file"], "bang": BANG, "ghep": {"sheet": g["sheet"], "header_row": g["header_row"], "columns": c},
            "ky": b["ky"], "ngay_chot": b["ngay_chot"], **({"khong_co_cot_sau_ck": True} if b.get("khong_co_cot_sau_ck") else {})}, None


def _kiem_xac_nhan(b):
    """Thân xác nhận ⇒ (thân gửi lõi, None) | (None, lý do). Khoá: ma, tra_loi (số nguyên), nguoi (≤ 80), ghi_chu (≤ 500), cho_qua [moi_N].
    Có cho_qua mà ghi chú trống ⇒ chặn sớm (lõi cũng chặn — ở đây chỉ để khỏi một lượt gọi)."""
    if not isinstance(b, dict) or set(b) - {"ma", "tra_loi", "nguoi", "ghi_chu", "cho_qua"}:
        return None, "Thân xác nhận chỉ nhận: ma, tra_loi, nguoi, ghi_chu, cho_qua."
    if not (isinstance(b.get("ma"), str) and _MA.fullmatch(b["ma"])):
        return None, "Mã câu kiểm sai dạng — bấm Ghép thử lại."
    if not _so(b.get("tra_loi"), 1, 2000):
        return None, "Câu trả lời phải là một số nguyên (cột thứ mấy, cột A = 1)."
    if not (isinstance(b.get("nguoi"), str) and 0 < len(b["nguoi"].strip()) <= 80):
        return None, "Cần tên người xác nhận (tối đa 80 ký tự)."
    gc = b.get("ghi_chu", "")
    if not isinstance(gc, str) or len(gc) > 500:
        return None, "Ghi chú tối đa 500 ký tự."
    cq = b.get("cho_qua", [])
    if not isinstance(cq, list) or len(cq) > 200 or any(not (isinstance(x, str) and _MOI.fullmatch(x)) for x in cq):
        return None, "cho_qua phải là danh sách mã moi_…"
    if cq and not gc.strip():
        return None, "Có giá trị mới được cho qua thì phải ghi lý do ở ô Ghi chú."
    return {"ma": b["ma"], "tra_loi": b["tra_loi"], "nguoi": b["nguoi"].strip(), **({"ghi_chu": gc} if gc.strip() else {}),
            **({"cho_qua": cq} if cq else {})}, None


def _goi(phuong_thuc, duong, than, kieu, ma_tl, het_gio):
    """Gọi hộp 18402 ⇒ (mã HTTP, dict). Không tới được ⇒ (502, refused)."""
    h = {"X-Tai-Len-Ma": ma_tl}
    if than is not None:
        h |= {"Content-Type": kieu, "Content-Length": str(len(than))}
    req = urllib.request.Request(GOC_18402 + duong, data=than, method=phuong_thuc, headers=h)
    try:
        with mo_khong_doi_huong.mo(req, timeout=het_gio) as r:   # [ghi chú kỹ thuật] 3xx ⇒ HTTPError, mã riêng không đi theo Location
            ma, tho = r.status, r.read(1024 * 1024)
    except urllib.error.HTTPError as e:
        ma, tho = e.code, e.read(1024 * 1024)
    except (urllib.error.URLError, OSError):
        return 502, {"refused": "Hộp tải file KPI chưa bật (127.0.0.1:18402) — báo người vận hành."}
    try:
        d = json.loads(tho or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        d = None
    if not isinstance(d, dict):
        return (ma if ma != 200 else 502), {"refused": f"Hộp tải file KPI trả lời không đọc được (mã {ma})."}
    if ma != 200 and not d.get("refused"):
        d = {**d, "refused": f"Không thành công (mã {ma})."}
    return ma, d


def xu_ly(phuong_thuc, duong_day_du, headers, doc_than, *, cong, ma_tai_len, ghi=lambda s: None, goi=_goi):
    """Một yêu cầu màn ghép ⇒ (mã HTTP, dict trả trang). `headers`: đối tượng có .get; `doc_than(n)`: đọc n byte thân; `ma_tai_len()`: mã 600 hoặc None."""
    u = urllib.parse.urlparse(duong_day_du)
    viec = DUONG.get(u.path)
    if viec is None or viec[0] != phuong_thuc:
        return 404, {"refused": "Không có đường này."}
    if (headers.get("Host") or "").strip().lower() not in (f"127.0.0.1:{cong}", f"localhost:{cong}"):
        return 403, {"refused": "Host không được phép"}
    q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    kieu = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    n = headers.get("Content-Length")
    ten = viec[1]
    than = None
    if ten == "khuon":
        if q:
            return 422, {"refused": "Đường này không nhận tham số."}
    elif ten == "bo-cuc":
        if set(q) - {"sheet_so", "dong_tieu_de"} or any(not _SO_NHO.fullmatch(v) for v in q.values()) or (("sheet_so" in q) != ("dong_tieu_de" in q)):
            return 422, {"refused": "Sheet thứ mấy và dòng tiêu đề thứ mấy phải là số, và đi cùng nhau."}
        if kieu != "application/octet-stream":
            return 415, {"refused": "Cần gửi thân nhị phân (byte file .xlsx)."}
        if not n or not n.isdigit() or not 4 <= int(n) <= TRAN_FILE:
            return 413, {"refused": "File phải từ 4 byte tới 20 MB."}
        than = doc_than(int(n))
        if len(than) != int(n):
            return 400, {"refused": "Thân không đủ byte."}
        if than[:4] != b"PK\x03\x04":
            return 422, {"refused": "Không phải file .xlsx."}
    else:
        if q:
            return 422, {"refused": "Đường này không nhận tham số trên địa chỉ."}
        if kieu != "application/json":
            return 415, {"refused": "Cần gửi thân JSON."}
        if not n or not n.isdigit() or not 2 <= int(n) <= TRAN_JSON:
            return 413, {"refused": "Thân phải từ 2 byte tới 64 KB."}
        tho = doc_than(int(n))
        try:
            b = json.loads(tho)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return 422, {"refused": "Thân phải là JSON."}
        sach, ly_do = (_kiem_thu if ten == "thu" else _kiem_xac_nhan)(b)
        if sach is None:
            return 422, {"refused": ly_do}
        than = json.dumps(sach, ensure_ascii=False).encode("utf-8")
    ma_tl = ma_tai_len()
    if ma_tl is None:
        return 503, {"refused": "Hộp tải file KPI chưa sẵn mã (thiếu file mã trên máy) — báo người vận hành."}
    if ten == "khuon":
        ma, d = goi("GET", f"/khuon/{BANG}", None, None, ma_tl, 30)
    elif ten == "bo-cuc":
        ma, d = goi("POST", "/bo-cuc?" + urllib.parse.urlencode({"bang": BANG, **q}), than, "application/octet-stream", ma_tl, 200)
    else:
        ma, d = goi("POST", "/ghep-thu" if ten == "thu" else "/ghep-xac-nhan", than, "application/json", ma_tl, 200)
    ghi(f"GHÉP CỘT KPI {ten} cỡ={len(than) if than is not None else 0} ⇒ HTTP {ma}")   # không thân, không tên file
    return ma, d
