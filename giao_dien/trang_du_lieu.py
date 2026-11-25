"""[ghi chú kỹ thuật] Phần THUẦN phía máy chủ trang cho trang Dữ liệu: sổ các lần tải file KPI (để vẽ "Tháng <kỳ> đã có gì" + "Các lần tải gần đây"),
danh sách tài liệu hỏi đáp cho người dùng thường (bỏ kho tạm / file thử), đánh dấu ẨN một tài liệu. Không gọi mạng, không gọi AI; nơi ghi do người gọi truyền vào.
Vì sao trang tự ghi sổ: hệ thống tính thưởng CHƯA có đường hỏi "kỳ này đã có / thiếu gì" (mekongai sẽ thêm GET /api/system/ky/<kỳ>) ⇒ tới lúc đó bảng tháng lấy từ
các lần tải THÀNH CÔNG mà trang ghi lại + lời từ chối "thiếu luật" của cửa tải lên. Sổ chỉ ghi: giờ, loại, kỳ, ngày chốt, kết quả, lý do (lời của hệ thống tính thưởng),
cỡ file — KHÔNG ghi tên file, không nội dung.
  ghi_lan_tai(goc, org, loai, ky, ngay_chot, http, tra, co_byte)      — gọi sau MỖI lần tải lên (thành công lẫn bị từ chối)
  doc_kpi(goc, org, ky=None) ⇒ {"ky", "ky_ds", "thang": [4 dòng], "lich_su": [...], "viec_ke": câu}
  ds_tai_lieu(ds_kho, an: set) ⇒ danh sách cho trang (không kho tạm, không kho đã ẩn tự động, không tài liệu người dùng đã ẩn)
  an_tai_lieu(goc, org, sha, an=True) / doc_an(goc, org)"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

_KY = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_SHA = re.compile(r"[0-9a-f]{64}")
_ORG = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LOAI = {"raw": "Đơn hàng", "chi_tieu": "Chỉ tiêu nhân viên", "tracking": "Sổ theo dõi (Tracking)"}
SO_DONG_GIU = 500


def _tep(goc: Path, org: str, ten: str) -> Path:
    if not _ORG.fullmatch(str(org)):
        raise ValueError("mã tổ chức không đúng khuôn")
    d = Path(goc) / hashlib.sha256(org.encode()).hexdigest()[:32]
    d.mkdir(parents=True, exist_ok=True); d.chmod(0o700)
    return d / ten


def phan_loai_ket_qua(http: int, tra: dict) -> tuple[str, str]:
    """(kết quả: "da_nhan" | "da_co_san" | "can_ghep_cot" | "thieu_luat" | "lech_ky" | "day_cho" | "tu_choi", lý do nguyên văn ≤ 400 ký tự)."""
    ly = str((tra or {}).get("refused") or (tra or {}).get("loi") or "")[:400]
    if http == 200 and not ly:
        return ("da_co_san" if (tra or {}).get("da_co_san") else "da_nhan"), ""
    thuong = ly.lower()
    if http == 422 and (ma_cua_loi(tra) == "can_ghep_cot" or "ghép cột trước" in thuong):   # mã của lõi trước, chữ là lưới đỡ cho lõi bản cũ
        return "can_ghep_cot", ly
    if http == 409 and "thư mục file tải lên" in thuong and "đã đủ" in thuong:   # [ghi chú kỹ thuật] lõi trần 50 file, không tự dọn (upload.py:105-106 của lõi)
        return "day_cho", ly
    if http == 409 and ("luật" in thuong or "rules" in thuong):
        return "thieu_luat", ly
    if http == 409:
        return "lech_ky", ly
    return "tu_choi", ly


VIEC_NEN_LAM = {   # [ghi chú kỹ thuật] một dòng lời thường theo từng kiểu từ chối đã biết; nguyên văn của hệ thống tính thưởng vẫn giữ để bấm mở
    "can_ghep_cot": "Tên cột trong file khác mẫu hệ thống đã biết. Bấm “Ghép cột” để chỉ cho hệ thống cột nào là cột nào — làm một lần là dùng được.",
    "thieu_luat": "Hệ thống chưa có luật tính thưởng của tháng này nên chưa tính được. Bạn báo người phụ trách đưa luật của tháng này vào, rồi tải lại file.",
    "lech_ky": "Tháng hoặc ngày chốt bạn chọn không khớp với số liệu hệ thống đang có. Bấm “Sửa” để kiểm lại tháng và ngày chốt của file rồi tải lại.",
    "tu_choi": "Hệ thống tính thưởng không nhận file này. Xem lý do bên dưới; nếu là file sai mẫu, hãy xuất lại file từ phần mềm bán hàng rồi tải lại.",
    "day_cho": "Hệ thống tính thưởng đã đầy chỗ giữ file tải lên — cần người phụ trách dọn, rồi bạn tải lại. File của bạn không có lỗi; không cần sửa tháng hay ngày chốt.",
    413: "File lớn hơn 20 MB. Bạn tách file theo tháng (mỗi file một tháng) rồi tải lại.",
    "khong_xlsx": "Phần này chỉ nhận file Excel (.xlsx). Bạn mở file rồi “Lưu thành” .xlsx, hoặc đưa file vào phần “Tài liệu để hỏi đáp” nếu chỉ muốn hỏi nội dung.",
}


VIEC_THEO_MA = {   # [ghi chú kỹ thuật] mã ổn định của lõi ⇒ MỘT dòng lời thường; mã lạ ⇒ rơi về lời theo loại kết quả (VIEC_NEN_LAM)
    "can_ghep_cot": VIEC_NEN_LAM["can_ghep_cot"],
    "gia_tri_chua_biet": "File có giá trị mà hệ thống chưa biết (ví dụ một ngành hàng / nhóm hàng mới). Bấm “Ghép cột” để xem giá trị mới là gì rồi cho qua hoặc ghép vào giá trị đã có.",
    "luat_neu_khong_co_dong": "Luật tính thưởng của tháng nhắc tới một giá trị mà file này không có dòng nào. Việc cần làm: kiểm lại luật của tháng (hoặc báo người phụ trách luật) — "
                              "hoặc kiểm xem file có bị thiếu dữ liệu của nhóm hàng đó không. Xuất lại file từ phần mềm bán hàng KHÔNG giải quyết được nếu luật mới là chỗ lệch.",
    "lech_tong": "Tổng trong file không khớp với tổng các dòng. Mở file kiểm dòng tổng / dòng bị lọc ẩn, hoặc xuất lại file từ phần mềm bán hàng rồi tải lại.",
    "ngay_sai": "File có ô ngày không đọc được hoặc nằm ngoài tháng đã chọn. Kiểm cột ngày trong file và tháng / ngày chốt ở thẻ này (bấm “Sửa”) rồi tải lại.",
    "o_khong_phai_so": "File có ô đáng lẽ là số nhưng lại là chữ. Mở file sửa các ô đó (hoặc xuất lại file từ phần mềm bán hàng) rồi tải lại.",
    "thieu_nhom_hang": "File có dòng thiếu nhóm hàng. Bổ sung nhóm hàng cho các dòng đó (hoặc xuất lại file từ phần mềm bán hàng) rồi tải lại.",
    "dong_khong_ma_nv": "File có dòng không có mã nhân viên. Bổ sung mã nhân viên cho các dòng đó rồi tải lại.",
    "dong_total": "File còn dòng tổng lẫn trong dữ liệu. Xoá dòng tổng (hoặc xuất lại file không kèm dòng tổng) rồi tải lại.",
    "sai_khuon": VIEC_NEN_LAM["tu_choi"],
    # [ghi chú kỹ thuật] lõi báo file chỉ tiêu thiếu người của đội: việc là BỔ SUNG chỉ tiêu, không phải xuất lại file bán hàng
    "doi_thieu_nguoi": "File chỉ tiêu chưa có đủ mọi nhân viên của đội. Bổ sung chỉ tiêu cho những người còn thiếu rồi tải lại file chỉ tiêu. "
                       "Xuất lại file từ phần mềm bán hàng KHÔNG giải quyết được.",
}
_MA = re.compile(r"[a-z][a-z0-9_]{2,40}")
_DUONG_API = re.compile(r"\s*\((?:POST|GET|PUT|DELETE)\s+/api/[^)]{0,200}\)|\s*(?:POST|GET)\s+/api/\S+|/api/\S+")


def ma_cua_loi(tra) -> str | None:
    """Mã ổn định lõi gửi kèm lời từ chối (ô "code"); sai dạng / vắng ⇒ None. Chỉ chữ thường, số, gạch dưới — không bao giờ in thứ khác ra trang."""
    m = (tra or {}).get("code") if isinstance(tra, dict) else None
    return m if isinstance(m, str) and _MA.fullmatch(m) else None


def viec_theo(ket_qua: str, tra) -> str | None:
    """Lời "việc nên làm": theo MÃ của lõi trước (VIEC_THEO_MA), không có / mã lạ ⇒ theo loại kết quả (VIEC_NEN_LAM)."""
    return VIEC_THEO_MA.get(ma_cua_loi(tra) or "") or VIEC_NEN_LAM.get(ket_qua)


def loi_cho_nguoi(ly) -> str:
    """Lời từ chối của lõi cho NGƯỜI DÙNG THƯỜNG đọc: bỏ mọi đường kỹ thuật kiểu "(POST /api/system/bo-cuc ⇒ ghep-thu)" / "/api/…" (đường đó người dùng không gọi được —
    việc tương ứng là nút “Ghép cột” ở thẻ file). Không đổi chữ nào khác."""
    s = _DUONG_API.sub("", str(ly or ""))
    return re.sub(r"\s{2,}", " ", s.replace(" — vào ghép thử để", " — bấm “Ghép cột” để")).strip()[:400]


def ghi_lan_tai(goc, org: str, loai: str, ky: str, ngay_chot, http: int, tra: dict, co_byte: int, gio=time.time) -> dict:
    kq, ly = phan_loai_ket_qua(http, tra)
    muc = {"luc": gio(), "loai": loai if loai in LOAI else "khac", "ky": ky if _KY.fullmatch(str(ky or "")) else None,
           "ngay_chot": str(ngay_chot)[:10] if ngay_chot else None, "http": int(http), "ket_qua": kq, "ly_do": ly, "co_byte": int(co_byte), "ma": ma_cua_loi(tra),
           "tong_doi": (tra or {}).get("team_total") if kq in ("da_nhan", "da_co_san") else None,
           "ban": str((tra or {}).get("data_version") or "")[:64] or None, "ky_tra": (tra or {}).get("period"), "chot_tra": (tra or {}).get("cutoff")}
    f = _tep(goc, org, "lan_tai_kpi.jsonl")
    cu = f.read_text(encoding="utf-8").splitlines()[-(SO_DONG_GIU - 1):] if f.is_file() else []
    t = f.with_suffix(".tam"); t.write_text("\n".join(cu + [json.dumps(muc, ensure_ascii=False)]) + "\n", encoding="utf-8"); t.chmod(0o600); t.replace(f)
    return muc


def _doc_lan_tai(goc, org) -> list[dict]:
    f = _tep(goc, org, "lan_tai_kpi.jsonl")
    ra = []
    for d in (f.read_text(encoding="utf-8").splitlines() if f.is_file() else []):
        try:
            ra.append(json.loads(d))
        except ValueError:
            continue
    return ra


def doc_kpi(goc, org: str, ky: str | None = None, ky_dang_chay: str | None = None) -> dict:
    ds = _doc_lan_tai(goc, org)
    ky_ds = sorted({x["ky"] for x in ds if x.get("ky")} | ({ky_dang_chay} if _KY.fullmatch(str(ky_dang_chay or "")) else set()), reverse=True)
    if not _KY.fullmatch(str(ky or "")):
        ky = next((x["ky"] for x in reversed(ds) if x.get("ky")), None) or (ky_dang_chay if _KY.fullmatch(str(ky_dang_chay or "")) else None)
    cua_ky = [x for x in ds if x.get("ky") == ky] if ky else []

    def moi_nhat(loai, kq):
        return next((x for x in reversed(cua_ky) if x.get("loai") == loai and x.get("ket_qua") in kq), None)

    def dong(loai, ten):
        ok, hong = moi_nhat(loai, ("da_nhan", "da_co_san")), moi_nhat(loai, ("can_ghep_cot", "thieu_luat", "lech_ky", "tu_choi", "day_cho"))
        if ok and (not hong or ok["luc"] >= hong["luc"]):
            return {"khoa": loai, "ten": ten, "trang_thai": "da_co", "luc": ok["luc"], "chi_tiet": ("số liệu tới ngày " + "/".join(reversed(str(ok["chot_tra"])[:10].split("-")))) if ok.get("chot_tra") else ""}
        if hong:
            return {"khoa": loai, "ten": ten, "trang_thai": "chua_duoc", "luc": hong["luc"], "chi_tiet": VIEC_NEN_LAM.get(hong["ket_qua"], "")}
        return {"khoa": loai, "ten": ten, "trang_thai": "thieu", "luc": None, "chi_tiet": ""}

    thang = [dong("raw", "Đơn hàng"), dong("chi_tieu", "Chỉ tiêu nhân viên"),
             {"khoa": "tracking", "ten": "Sổ theo dõi (Tracking)", "trang_thai": "chua_ho_tro", "luc": None, "chi_tiet": "Chưa hỗ trợ tự xử lý qua trang — đang làm."}]
    thieu_luat = next((x for x in reversed(cua_ky) if x.get("ket_qua") == "thieu_luat"), None)
    da_tinh = next((x for x in reversed(cua_ky) if x.get("ket_qua") in ("da_nhan", "da_co_san")), None)
    if da_tinh and (not thieu_luat or da_tinh["luc"] >= thieu_luat["luc"]):   # [ghi chú kỹ thuật] theo TRẢ LỜI của hệ thống tính thưởng: tải + tính được ⇒ có luật của tháng
        luat = {"trang_thai": "da_co", "chi_tiet": "Hệ thống tính thưởng đã tính được số liệu của tháng này (tức là đã có luật của tháng)."}
    elif thieu_luat:
        luat = {"trang_thai": "thieu", "chi_tiet": "Hệ thống tính thưởng báo chưa có luật của tháng này. Việc đưa luật vào chưa làm được qua trang — báo người phụ trách."}
    else:
        luat = {"trang_thai": "chua_ro", "chi_tiet": "Chưa rõ — hệ thống tính thưởng chỉ cho biết khi bạn tải file đơn hàng của tháng này lên."}
    thang.append(dict({"khoa": "luat", "ten": "Luật tính thưởng", "luc": None}, **luat))
    if not ky:
        viec = "Chưa có file nào được tải lên. Bắt đầu bằng file đơn hàng của tháng: kéo file vào ô phía trên."
    elif thang[0]["trang_thai"] != "da_co":
        viec = "Còn thiếu file đơn hàng của tháng này — kéo file vào ô phía trên." if thang[0]["trang_thai"] == "thieu" else "File đơn hàng chưa được nhận — xem lý do ở thẻ file rồi tải lại."
    elif luat["trang_thai"] == "thieu":
        viec = "Hệ thống chưa có luật tính thưởng của tháng này — báo người phụ trách."
    elif thang[1]["trang_thai"] == "thieu":
        viec = "Đã có file đơn hàng. Nếu tháng này có file chỉ tiêu nhân viên mới thì kéo vào ô phía trên; không thì có thể hỏi trợ lý ngay."
    else:
        viec = "Đã đủ để hỏi: bấm “Hỏi trợ lý về tháng này”."
    return {"ky": ky, "ky_ds": ky_ds, "thang": thang, "viec_ke": viec, "nguon": "trang_tu_ghi", "san_sang": thang[0]["trang_thai"] == "da_co", "giu_qua_bat_lai": False,
            "lich_su": [{k: x.get(k) for k in ("luc", "loai", "ky", "ngay_chot", "ket_qua", "ly_do", "tong_doi", "co_byte")} for x in reversed(ds[-30:])]}


# ============ [ghi chú kỹ thuật] bảng tháng từ LÕI ("kỳ này có gì") ============
TEN_MUC = {"raw": "Đơn hàng", "chi_tieu": "Chỉ tiêu nhân viên", "tuyen": "Danh sách tuyến (điểm bán)", "luat_chinh": "Luật tính thưởng",
           "luat_phu": "Luật phụ (thưởng thêm, 1 % gia vị, hội viên)"}
TRANG_THAI_LOI = {"co": "da_co", "thieu": "thieu", "dung_cua_ky_khac": "ky_khac"}   # state của lõi ⇒ nhãn của trang; state LẠ ⇒ "chua_ro" (không vỡ)
TEN_PHAN_PHU = {"thuong_them": "thưởng thêm", "extra": "thưởng thêm", "gia_vi_1pct": "1 % gia vị", "gia_vi": "1 % gia vị", "hoi_vien": "hội viên"}
_KY_TU_DK = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _chu(v, tran=300) -> str:
    """Chữ của lõi ⇒ chuỗi sạch cho trang: chỉ nhận kiểu chuỗi, bỏ ký tự điều khiển, cắt `tran` ký tự."""
    return _KY_TU_DK.sub(" ", v).strip()[:tran] if isinstance(v, str) else ""


def _loi_thuong_ky(s: str) -> str:
    """Lời do lõi viết (bảng chữ cố định trong code lõi) có vài chữ kỹ thuật ⇒ đổi sang chữ người dùng thường đọc; không đổi nghĩa."""
    s = re.sub(r"\s*\(loại [a-z_]+\)", "", s)
    # [vòng 2 — thân thật lõi 252dc30a] lời lõi có ĐƯỜNG DẪN file trên máy chủ ("thiếu file kpi_agent/config/rules_2026_11_raw.yaml") ⇒ người dùng không cần, không in
    s = re.sub(r"\s*(?:thiếu )?file (?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,5}", " thiếu file luật trong hệ thống", s)
    s = re.sub(r"(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,5}", "(một file của hệ thống)", s)
    return loi_cho_nguoi(s.replace("backend", "hệ thống tính thưởng").replace("sổ Tracking", "sổ theo dõi (Tracking)"))


def _luc_iso(v) -> float | None:
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() if isinstance(v, str) and v else None
    except ValueError:
        return None


def _ky_ke(ky: str) -> str:
    nam, th = int(ky[:4]), int(ky[5:7])
    return f"{nam + 1:04d}-01" if th == 12 else f"{nam:04d}-{th + 1:02d}"


def ky_de_chon(ky_loi: set, ky: str, hom_nay: str | None = None) -> list:
    """[ghi chú kỹ thuật] Danh sách tháng cho ô "Xem tháng khác": các kỳ lõi liệt kê + kỳ đang xem + THÁNG KẾ sau kỳ mới nhất + THÁNG HIỆN TẠI theo lịch
    (để xem "còn thiếu gì" TRƯỚC khi tải file). Chỉ là danh sách để chọn — trạng thái từng tháng vẫn do lõi trả khi chọn. Mới nhất trước."""
    ds = {k for k in ky_loi if isinstance(k, str) and _KY.fullmatch(k)} | {ky}
    ds.add(_ky_ke(max(ds)))
    hn = hom_nay or time.strftime("%Y-%m")
    if _KY.fullmatch(hn):
        ds.add(hn)
    return sorted(ds, reverse=True)[:24]


def ky_tu_loi(tra, ds_ky=None, lich_su: list | None = None, hom_nay: str | None = None) -> dict | None:
    """Trả lời /api/system/ky/<kỳ> của lõi ⇒ hình bảng tháng của trang (cùng khuôn doc_kpi + nguon = "loi"). Sai khuôn (không phải object, thiếu period đúng dạng,
    items không phải danh sách) ⇒ None ⇒ người gọi rơi về sổ trang. ds_ky = trả lời /api/system/ky (danh sách kỳ) nếu có."""
    if not isinstance(tra, dict) or not _KY.fullmatch(str(tra.get("period") or "")) or not isinstance(tra.get("items"), list):
        return None
    ky = tra["period"]
    thang, viec = [], None
    for it in tra["items"][:20]:
        if not isinstance(it, dict):
            continue
        khoa = it.get("key") if isinstance(it.get("key"), str) and _MA.fullmatch(it["key"]) else None
        if not khoa:
            continue
        st = it.get("state")
        tt = TRANG_THAI_LOI.get(st) if isinstance(st, str) else None
        ct = [_loi_thuong_ky(_chu(it.get("detail")))]
        if khoa == "raw" and tt == "da_co" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(it.get("cutoff") or "")):
            ct.append("số liệu tới ngày " + "/".join(reversed(it["cutoff"].split("-"))))
        if tt == "da_co":
            ct.append({True: "đã qua kiểm khi tải lên", False: "chưa ai kiểm cho tháng này"}.get(it.get("checked")) or "bản nạp sẵn lúc hệ thống bật")
        if khoa == "luat_phu":   # [vòng 2] lời detail của lõi liệt kê KHOÁ kỹ thuật ("… cho: gia_vi_1pct, hoi_vien") ⇒ đổi sang tên lời thường, khoá dài trước
            for k_, ten_ in sorted(TEN_PHAN_PHU.items(), key=lambda x: -len(x[0])):
                ct[0] = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(k_) + r"(?![A-Za-z0-9_])", ten_, ct[0])
        if khoa == "luat_phu" and isinstance(it.get("parts"), dict):
            thieu = [TEN_PHAN_PHU.get(k, _chu(k, 40)) for k, v in list(it["parts"].items())[:8] if isinstance(k, str) and v != "co"]
            if thieu:
                ct.append("còn thiếu: " + ", ".join(thieu))
        buoc = _loi_thuong_ky(_chu(it.get("next_step")))
        if tt != "da_co" and buoc:
            ct.append("Việc nên làm: " + buoc)
            viec = viec or (buoc[:1].upper() + buoc[1:] + ".")
        if tt is None:   # state lạ (lõi thêm giá trị mới) ⇒ không đoán nghĩa: "chưa rõ" + nguyên lời của lõi
            tt = "chua_ro"
            ct.insert(0, f"hệ thống tính thưởng báo trạng thái “{_chu(st, 40) if isinstance(st, str) else '?'}” mà trang chưa biết")
        thang.append({"khoa": khoa, "ten": TEN_MUC.get(khoa, f"Mục khác ({khoa})"), "trang_thai": tt, "luc": _luc_iso(it.get("uploaded_at")),
                      "chi_tiet": " · ".join(x for x in ct if x)[:600]})
    if not thang:
        return None
    san = tra.get("ready") is True
    ky_ds = ky_de_chon({p.get("period") for p in ((ds_ky or {}).get("periods") or []) if isinstance(p, dict) and isinstance(p.get("period"), str)}
                       if isinstance(ds_ky, dict) else set(), ky, hom_nay)
    return {"ky": ky, "ky_ds": ky_ds, "thang": thang, "nguon": "loi", "san_sang": san, "giu_qua_bat_lai": tra.get("uploads_survive_restart") is True,
            "viec_ke": "Đã đủ để hỏi: bấm “Hỏi trợ lý về tháng này”." if san else (viec or "Còn mục chưa đủ — xem từng dòng ở bảng."),
            "lich_su": lich_su or []}


def bang_thang(so: dict, ky_hoi, hoi) -> dict:
    """Bảng tháng cho trang: HỎI LÕI trước (hoi(đường) ⇒ (mã HTTP, thân đã phân tích | None) — người gọi lo mạng, mã riêng, trần thời gian), không được thì trả `so`
    (sổ trang tự ghi — doc_kpi). ky_hoi sai dạng / None ⇒ không hỏi. Mọi lỗi của `hoi` (ném, mã ≠ 200, thân sai khuôn, lõi bản cũ chưa có đường ⇒ 404) ⇒ sổ trang,
    kèm ô ly_do_khong_hoi_duoc (một chữ ngắn, không có nội dung của lõi) để dòng dưới bảng nói thật nguồn."""
    if not _KY.fullmatch(str(ky_hoi or "")):   # [ghi chú kỹ thuật] kỳ sai dạng (gõ tay) ⇒ kỳ của sổ trang, VẪN hỏi lõi; sổ cũng không có kỳ ⇒ sổ trang
        ky_hoi = so.get("ky") if isinstance(so, dict) else None
        if not _KY.fullmatch(str(ky_hoi or "")):
            return so
    try:
        ma, tra = hoi("/ky/" + ky_hoi)
    except Exception as e:  # noqa: BLE001
        return dict(so, ly_do_khong_hoi_duoc=f"không gọi được ({type(e).__name__})")
    if ma != 200:
        return dict(so, ly_do_khong_hoi_duoc=f"mã {int(ma) if isinstance(ma, int) else '?'}")
    if isinstance(tra, dict) and tra.get("period") != ky_hoi:   # lõi trả kỳ KHÁC kỳ hỏi ⇒ không tin
        return dict(so, ly_do_khong_hoi_duoc="trả lời không đúng tháng đã hỏi")
    try:
        ma2, ds = hoi("/ky")
    except Exception:  # noqa: BLE001 — danh sách tháng chỉ để chọn xem; hỏng thì dùng một tháng
        ma2, ds = 0, None
    r = ky_tu_loi(tra, ds if ma2 == 200 else None, so.get("lich_su"))
    return r if r else dict(so, ly_do_khong_hoi_duoc="trả lời sai khuôn")


def doc_an(goc, org) -> set:
    f = _tep(goc, org, "tai_lieu_an.json")
    try:
        return {s for s in json.loads(f.read_text(encoding="utf-8")) if isinstance(s, str) and _SHA.fullmatch(s)} if f.is_file() else set()
    except ValueError:
        return set()


def an_tai_lieu(goc, org, sha: str, an: bool = True) -> bool:
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        return False
    s = doc_an(goc, org); (s.add if an else s.discard)(sha)
    f = _tep(goc, org, "tai_lieu_an.json"); t = f.with_suffix(".tam"); t.write_text(json.dumps(sorted(s)), encoding="utf-8"); t.chmod(0o600); t.replace(f)
    return True


def ds_tai_lieu(ds_kho: list[dict], an: set) -> list[dict]:
    """Từ So.ds(org) của kho_tai_lieu ⇒ danh sách cho người dùng thường: bỏ kho TẠM (file kéo vào khung chat / file thử), kho đã tự ẩn, tài liệu người dùng bấm Ẩn.
    KHÔNG trả tên kho (tên kỹ thuật) — trang chỉ cần mã sha để hỏi / ẩn."""
    return [{"sha": x["sha"], "ten": x.get("ten"), "trang": x.get("trang"), "luc": x.get("luc")} for x in ds_kho
            if not x.get("tam") and not x.get("an") and x.get("sha") not in an]
