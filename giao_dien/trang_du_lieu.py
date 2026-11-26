"""[C 20–26/11/2026 — Trang "Dữ liệu" & kỳ trống fdd20ab0] Phần THUẦN phía máy chủ trang cho trang Dữ liệu:
sổ các lần tải file KPI, phân loại phản hồi lõi (kể cả `cho_du_file` 200), lời thường theo mã `VIEC_THEO_MA`,
bảng tháng + 3 tổng từ `GET /ky/<kỳ>`, danh sách tài liệu hỏi đáp, và kiểm tra kỳ trống cho khung chat (`GET /ky`).
Không gọi mạng, không gọi AI; nơi ghi do người gọi truyền vào.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

_KY = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_SHA = re.compile(r"[0-9a-f]{64}")
_ORG = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LOAI = {
    "raw": "Đơn hàng",
    "chi_tieu": "Chỉ tiêu nhân viên",
    "so_tracking": "Sổ theo dõi (Tracking)",
    "trang_chi_tieu": "Trang theo dõi chỉ tiêu (.html)",
    "tracking": "Sổ theo dõi (Tracking)",
}
_TEN_FILE_CAN = {
    "raw": "File đơn hàng (.xlsx)",
    "chi_tieu": "File chỉ tiêu nhân viên (.xlsx)",
    "so_tracking": "Sổ theo dõi (.xlsx)",
    "tracking": "Sổ theo dõi (.xlsx)",
    "trang_chi_tieu": "Trang theo dõi (.html)",
}
SO_DONG_GIU = 500


def _tep(goc: Path, org: str, ten: str) -> Path:
    if not _ORG.fullmatch(str(org)):
        raise ValueError("mã tổ chức không đúng khuôn")
    d = Path(goc) / hashlib.sha256(org.encode()).hexdigest()[:32]
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d / ten


def phan_loai_ket_qua(http: int, tra: dict) -> tuple[str, str]:
    """(kết quả: "da_nhan" | "da_co_san" | "cho_du_file" | "can_ghep_cot" | "thieu_luat" | "lech_ky" | "day_cho" | "tu_choi", lý do ≤ 400)."""
    ly = str((tra or {}).get("refused") or (tra or {}).get("loi") or "")[:400]
    if http == 200 and (tra or {}).get("trang_thai") == "cho_du_file":
        return "cho_du_file", ly
    if http == 200 and not (tra or {}).get("refused"):
        return ("da_co_san" if (tra or {}).get("da_co_san") else "da_nhan"), ""
    thuong = ly.lower()
    if http == 422 and (ma_cua_loi(tra) == "can_ghep_cot" or "ghép cột trước" in thuong):
        return "can_ghep_cot", ly
    if http == 409 and "thư mục file tải lên" in thuong and "đã đủ" in thuong:
        return "day_cho", ly
    if http == 409 and ("luật" in thuong or "rules" in thuong):
        return "thieu_luat", ly
    if http == 409:
        return "lech_ky", ly
    return "tu_choi", ly


VIEC_NEN_LAM = {
    "cho_du_file": "Hệ thống đã nhận file này — tiếp tục tải các file còn thiếu của tháng để hệ thống tính thưởng.",
    "dang_ban": "Hệ thống đang xử lý file trước đó — bạn chờ vài giây rồi bấm tải lại.",
    "can_ghep_cot": "Tên cột trong file khác mẫu hệ thống đã biết. Bấm “Ghép cột” để chỉ cho hệ thống cột nào là cột nào — làm một lần là dùng được.",
    "thieu_luat": "Hệ thống chưa có luật tính thưởng của tháng này nên chưa tính được. Bạn báo người phụ trách đưa luật của tháng này vào, rồi tải lại file.",
    "lech_ky": "Tháng hoặc ngày chốt bạn chọn không khớp với số liệu hệ thống đang có. Bấm “Sửa” để kiểm lại tháng và ngày chốt của file rồi tải lại.",
    "tu_choi": "Hệ thống tính thưởng không nhận file này. Xem lý do bên dưới; nếu là file sai mẫu, hãy xuất lại file từ phần mềm bán hàng rồi tải lại.",
    "day_cho": "Hệ thống tính thưởng đã đầy chỗ giữ file tải lên — cần người phụ trách dọn, rồi bạn tải lại. File của bạn không có lỗi; không cần sửa tháng hay ngày chốt.",
    413: "File lớn hơn giới hạn cho phép. Kiểm tra lại kích thước file (≤ 20 MB với Excel, ≤ 5 MB với trang .html) rồi tải lại.",
    "khong_xlsx": "Phần này chỉ nhận file Excel (.xlsx) hoặc trang theo dõi (.html). Bạn mở file rồi “Lưu thành” .xlsx, hoặc đưa file vào phần “Tài liệu để hỏi đáp” nếu chỉ muốn hỏi nội dung.",
}


VIEC_THEO_MA = {
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
    "doi_thieu_nguoi": "File chỉ tiêu chưa có đủ mọi nhân viên của đội. Bổ sung chỉ tiêu cho những người còn thiếu rồi tải lại file chỉ tiêu. "
                       "Xuất lại file từ phần mềm bán hàng KHÔNG giải quyết được.",
    # ---- mã mới của lõi kỳ trống fdd20ab0 (mục 5 GUI_CAPITOL_CUA_TAI_LEN_B.md) ----
    "ky_chua_co_du_lieu": "Tháng này chưa có đủ file dữ liệu đầu vào — bạn tải đủ 3 file đầu vào (đơn hàng, chỉ tiêu nhân viên, sổ theo dõi .xlsx) ở trang này.",
    "xlsb_can_luu_xlsx": "File Excel dạng nhị phân (.xlsb) hệ thống chưa đọc được. Mở file bằng Excel rồi “Lưu thành” .xlsx và tải lại.",
    "khac_ky_dang_mo": "File thuộc tháng khác với tháng hệ thống đang mở. Bấm “Sửa” để kiểm tra lại tháng đang mở rồi tải lại.",
    "chua_co_don_hang_cua_ky": "Chưa có file đơn hàng của tháng này. Hãy tải file đơn hàng (.xlsx) của tháng lên trước.",
    "trang_ngoai_khong_co_du_lieu": "Bạn đang tải nhầm file trang web bên ngoài (không chứa dữ liệu chỉ tiêu). Chọn đúng file saved_resource.html trong thư mục <tên trang>_files (file lớn) rồi tải lại.",
    "trang_khac_ky": "Trang theo dõi (.html) thuộc tháng khác với tháng đang tải. Kiểm tra đúng trang của tháng cần tính rồi tải lại.",
    "trang_khong_ro_ky": "Không đọc được tháng của trang theo dõi (.html). Kiểm tra lại đúng file saved_resource.html của tháng rồi tải lại.",
    "trang_lech_khuon": "File .html này không đúng khuôn trang theo dõi chỉ tiêu. Chọn đúng file saved_resource.html trong thư mục <tên trang>_files rồi tải lại.",
    "trang_qua_lon": "Trang theo dõi (.html) lớn hơn 5 MB. Kiểm tra đúng file saved_resource.html rồi tải lại.",
    "trang_lech_doi": "Danh sách nhân viên trên trang theo dõi (.html) không khớp với file chỉ tiêu nhân viên đã tải. Kiểm tra lại cả hai file cùng đội / cùng tháng.",
    "so_lech_khuon_tuyen": "Sổ theo dõi có bố cục khác mẫu nên chưa đọc được bảng tuyến. Cần người hỗ trợ kiểm tra lại mẫu sổ.",
    "can_cong_thuc_doc": "Sổ theo dõi có bố cục lạ nên hệ thống chưa tự đọc được. Cần người hỗ trợ kiểm tra công thức đọc sổ.",
    "tuyen_lech_don_hang": "Danh sách tuyến trong sổ theo dõi không khớp với file đơn hàng — kiểm lại xem sổ đã đúng tháng chưa.",
    "tuyen_lech_kenh": "Kênh bán hàng trong sổ theo dõi không khớp với file đơn hàng — kiểm lại xem sổ đã đúng tháng chưa.",
    "tuyen_lech_don_hang_theo_nguoi": "Đơn hàng theo nhân viên trong sổ theo dõi không khớp với file đơn hàng — kiểm lại sổ và file đơn hàng.",
    "nguoi_trong_doi_khong_co_tuyen": "Có nhân viên trong file chỉ tiêu nhưng chưa thấy tuyến trong sổ theo dõi — kiểm lại sổ theo dõi đã đủ đội chưa.",
    "khong_doi_chieu_duoc_voi_don_hang": "Sổ và file đơn hàng không đối chiếu được với nhau — kiểm lại sổ đúng tháng chưa.",
    "bo_file_khong_tinh_duoc": "Đã nhận đủ 3 file nhưng bộ số liệu không tính được — xem nguyên văn lý do bên dưới để kiểm tra lại file.",
    "dang_ban": VIEC_NEN_LAM["dang_ban"],
}
_MA = re.compile(r"[a-z][a-z0-9_]{2,45}")
_DUONG_API = re.compile(r"\s*\((?:POST|GET|PUT|DELETE)\s+/api/[^)]{0,200}\)|\s*(?:POST|GET)\s+/api/\S+|/api/\S+")


def ma_cua_loi(tra) -> str | None:
    """Mã ổn định lõi gửi kèm lời từ chối (ô "code"); sai dạng / vắng ⇒ None."""
    m = (tra or {}).get("code") if isinstance(tra, dict) else None
    return m if isinstance(m, str) and _MA.fullmatch(m) else None


def viec_theo(ket_qua: str, tra) -> str | None:
    """Lời "việc nên làm": theo MÃ của lõi trước (VIEC_THEO_MA), không có / mã lạ ⇒ theo loại kết quả (VIEC_NEN_LAM)."""
    return VIEC_THEO_MA.get(ma_cua_loi(tra) or "") or VIEC_NEN_LAM.get(ket_qua)


def loi_cho_nguoi(ly) -> str:
    """Lời từ chối của lõi cho NGƯỜI DÙNG THƯỜNG đọc: bỏ mọi đường kỹ thuật kiểu /api/..."""
    s = _DUONG_API.sub("", str(ly or ""))
    return re.sub(r"\s{2,}", " ", s.replace(" — vào ghép thử để", " — bấm “Ghép cột” để")).strip()[:400]


def ghi_lan_tai(goc, org: str, loai: str, ky: str, ngay_chot, http: int, tra: dict, co_byte: int, gio=time.time) -> dict:
    kq, ly = phan_loai_ket_qua(http, tra)
    muc = {
        "luc": gio(),
        "loai": loai if loai in LOAI else "khac",
        "ky": ky if _KY.fullmatch(str(ky or "")) else None,
        "ngay_chot": str(ngay_chot)[:10] if ngay_chot else None,
        "http": int(http),
        "ket_qua": kq,
        "ly_do": ly,
        "co_byte": int(co_byte),
        "ma": ma_cua_loi(tra),
        "tong_doi": (tra or {}).get("team_total") if kq in ("da_nhan", "da_co_san") else None,
        "ban": str((tra or {}).get("data_version") or "")[:64] or None,
        "ky_tra": (tra or {}).get("period"),
        "chot_tra": (tra or {}).get("cutoff"),
    }
    f = _tep(goc, org, "lan_tai_kpi.jsonl")
    cu = f.read_text(encoding="utf-8").splitlines()[-(SO_DONG_GIU - 1):] if f.is_file() else []
    t = f.with_suffix(".tam")
    t.write_text("\n".join(cu + [json.dumps(muc, ensure_ascii=False)]) + "\n", encoding="utf-8")
    t.chmod(0o600)
    t.replace(f)
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
        ok = moi_nhat(loai, ("da_nhan", "da_co_san", "cho_du_file"))
        hong = moi_nhat(loai, ("can_ghep_cot", "thieu_luat", "lech_ky", "tu_choi", "day_cho"))
        if ok and (not hong or ok["luc"] >= hong["luc"]):
            return {"khoa": loai, "ten": ten, "trang_thai": "da_co", "luc": ok["luc"], "chi_tiet": ("số liệu tới ngày " + "/".join(reversed(str(ok["chot_tra"])[:10].split("-")))) if ok.get("chot_tra") else ""}
        if hong:
            return {"khoa": loai, "ten": ten, "trang_thai": "chua_duoc", "luc": hong["luc"], "chi_tiet": VIEC_NEN_LAM.get(hong["ket_qua"], "")}
        return {"khoa": loai, "ten": ten, "trang_thai": "thieu", "luc": None, "chi_tiet": ""}

    thang = [
        dong("raw", "Đơn hàng"),
        dong("chi_tieu", "Chỉ tiêu nhân viên"),
        dong("so_tracking", "Sổ theo dõi (Tracking)"),
    ]
    thieu_luat = next((x for x in reversed(cua_ky) if x.get("ket_qua") == "thieu_luat"), None)
    da_tinh = next((x for x in reversed(cua_ky) if x.get("ket_qua") in ("da_nhan", "da_co_san")), None)
    if da_tinh and (not thieu_luat or da_tinh["luc"] >= thieu_luat["luc"]):
        luat = {"trang_thai": "da_co", "chi_tiet": "Hệ thống tính thưởng đã tính được số liệu của tháng này (tức là đã có luật của tháng)."}
    elif thieu_luat:
        luat = {"trang_thai": "thieu", "chi_tiet": "Hệ thống tính thưởng báo chưa có luật của tháng này. Việc đưa luật vào chưa làm được qua trang — báo người phụ trách."}
    else:
        luat = {"trang_thai": "chua_ro", "chi_tiet": "Chưa rõ — hệ thống tính thưởng chỉ cho biết khi bạn tải file của tháng này lên."}
    thang.append(dict({"khoa": "luat", "ten": "Luật tính thưởng", "luc": None}, **luat))
    if not ky:
        viec = "Chưa có file nào được tải lên. Kéo các file của tháng (đơn hàng, chỉ tiêu nhân viên, sổ theo dõi) vào ô phía trên."
    elif any(thang[i]["trang_thai"] != "da_co" for i in range(3)):
        thieu_ten = [thang[i]["ten"].lower() for i in range(3) if thang[i]["trang_thai"] != "da_co"]
        viec = f"Còn thiếu / chưa nhận đủ: {', '.join(thieu_ten)} — kéo file vào ô phía trên."
    elif luat["trang_thai"] == "thieu":
        viec = "Hệ thống chưa có luật tính thưởng của tháng này — báo người phụ trách."
    else:
        viec = "Đã đủ để hỏi: bấm “Hỏi trợ lý về tháng này”."
    return {
        "ky": ky,
        "ky_ds": ky_ds,
        "thang": thang,
        "viec_ke": viec,
        "nguon": "trang_tu_ghi",
        "san_sang": all(thang[i]["trang_thai"] == "da_co" for i in range(3)),
        "giu_qua_bat_lai": False,
        "lich_su": [{k: x.get(k) for k in ("luc", "loai", "ky", "ngay_chot", "ket_qua", "ly_do", "tong_doi", "co_byte")} for x in reversed(ds[-30:])],
    }


# ============ bảng tháng từ LÕI ("kỳ này có gì" + 3 tổng) ============
TEN_MUC = {
    "raw": "Đơn hàng",
    "chi_tieu": "Chỉ tiêu nhân viên",
    "tuyen": "Danh sách tuyến (điểm bán)",
    "luat_chinh": "Luật tính thưởng",
    "luat_phu": "Luật phụ (thưởng thêm, 1 % gia vị, hội viên)",
}
TRANG_THAI_LOI = {"co": "da_co", "thieu": "thieu", "dung_cua_ky_khac": "ky_khac"}
TEN_PHAN_PHU = {"thuong_them": "thưởng thêm", "extra": "thưởng thêm", "gia_vi_1pct": "1 % gia vị", "gia_vi": "1 % gia vị", "hoi_vien": "hội viên"}
_KY_TU_DK = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _chu(v, tran=300) -> str:
    return _KY_TU_DK.sub(" ", v).strip()[:tran] if isinstance(v, str) else ""


def _loi_thuong_ky(s: str) -> str:
    s = re.sub(r"\s*\(loại [a-z_]+\)", "", s)
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
    ds = {k for k in ky_loi if isinstance(k, str) and _KY.fullmatch(k)} | {ky}
    ds.add(_ky_ke(max(ds)))
    hn = hom_nay or time.strftime("%Y-%m")
    if _KY.fullmatch(hn):
        ds.add(hn)
    return sorted(ds, reverse=True)[:24]


def _so_nguyen(v) -> int | None:
    return int(round(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _khoan_tong(obj) -> dict | None:
    if not isinstance(obj, dict):
        return None
    return {
        "tong": _so_nguyen(obj.get("tong")),
        "trang_thai": _chu(obj.get("trang_thai"), 40) or None,
        "ly_do": _loi_thuong_ky(_chu(obj.get("ly_do"), 300)) or None,
    }


def ky_tu_loi(tra, ds_ky=None, lich_su: list | None = None, hom_nay: str | None = None) -> dict | None:
    """Trả lời /api/system/ky/<kỳ>[?data_version=] của lõi ⇒ hình bảng tháng + khối 3 con số."""
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
        if khoa == "luat_phu":
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
        if tt is None:
            tt = "chua_ro"
            ct.insert(0, f"hệ thống tính thưởng báo trạng thái “{_chu(st, 40) if isinstance(st, str) else '?'}” mà trang chưa biết")
        thang.append({
            "khoa": khoa,
            "ten": TEN_MUC.get(khoa, f"Mục khác ({khoa})"),
            "trang_thai": tt,
            "luc": _luc_iso(it.get("uploaded_at")),
            "chi_tiet": " · ".join(x for x in ct if x)[:600],
        })
    if not thang:
        return None
    san = tra.get("ready") is True
    ky_ds = ky_de_chon(
        {p.get("period") for p in ((ds_ky or {}).get("periods") or []) if isinstance(p, dict) and isinstance(p.get("period"), str)}
        if isinstance(ds_ky, dict) else set(),
        ky,
        hom_nay,
    )
    latest_dv = tra.get("latest_data_version")
    latest_dv = latest_dv if isinstance(latest_dv, str) and _SHA.fullmatch(latest_dv) else None
    dv = tra.get("data_version")
    dv = dv if isinstance(dv, str) and _SHA.fullmatch(dv) else None
    ba_tong = None
    if any(isinstance(tra.get(k), dict) for k in ("tam_kpi", "thuong_them", "gia_vi")):
        ba_tong = {
            "tam_kpi": {"tong": _so_nguyen(tra["tam_kpi"].get("tong"))} if isinstance(tra.get("tam_kpi"), dict) else None,
            "thuong_them": _khoan_tong(tra.get("thuong_them")),
            "gia_vi": _khoan_tong(tra.get("gia_vi")),
        }
    trang_thai_ky = _chu(tra.get("trang_thai"), 40) or None
    da_co = [str(x) for x in (tra.get("da_co") or []) if isinstance(x, str)][:10]
    con_thieu = [str(x) for x in (tra.get("con_thieu") or []) if isinstance(x, str)][:10]
    loi_ky = _loi_thuong_ky(_chu(tra.get("loi"), 300)) or None
    viec_ke = "Đã đủ để hỏi: bấm “Hỏi trợ lý về tháng này”." if san else (loi_ky or viec or "Còn mục chưa đủ — xem từng dòng ở bảng.")
    return {
        "ky": ky,
        "ky_ds": ky_ds,
        "thang": thang,
        "nguon": "loi",
        "san_sang": san,
        "giu_qua_bat_lai": tra.get("uploads_survive_restart") is True,
        "viec_ke": viec_ke,
        "lich_su": lich_su or [],
        "latest_data_version": latest_dv,
        "data_version": dv,
        "ba_tong": ba_tong,
        "trang_thai_ky": trang_thai_ky,
        "da_co": da_co,
        "con_thieu": con_thieu,
    }


def bang_thang(so: dict, ky_hoi, hoi, data_version: str | None = None) -> dict:
    """Bảng tháng cho trang: HỎI LÕI trước (`/ky/<kỳ>[?data_version=]`), không được thì trả `so` (sổ trang)."""
    if not _KY.fullmatch(str(ky_hoi or "")):
        ky_hoi = so.get("ky") if isinstance(so, dict) else None
        if not _KY.fullmatch(str(ky_hoi or "")):
            return so
    dv = data_version if isinstance(data_version, str) and _SHA.fullmatch(data_version) else None
    duong = f"/ky/{ky_hoi}?data_version={dv}" if dv else f"/ky/{ky_hoi}"
    try:
        ma, tra = hoi(duong)
    except Exception as e:  # noqa: BLE001
        return dict(so, ly_do_khong_hoi_duoc=f"không gọi được ({type(e).__name__})")
    if ma != 200:
        return dict(so, ly_do_khong_hoi_duoc=f"mã {int(ma) if isinstance(ma, int) else '?'}")
    if isinstance(tra, dict) and tra.get("period") != ky_hoi:
        return dict(so, ly_do_khong_hoi_duoc="trả lời không đúng tháng đã hỏi")
    try:
        ma2, ds = hoi("/ky")
    except Exception:  # noqa: BLE001
        ma2, ds = 0, None
    r = ky_tu_loi(tra, ds if ma2 == 200 else None, so.get("lich_su"))
    return r if r else dict(so, ly_do_khong_hoi_duoc="trả lời sai khuôn")


def thong_bao_ky_trong(tra) -> str | None:
    """[ghi chú] Đọc phản hồi `GET /ky` của lõi: đang `cho_du_file` ⇒ câu báo rõ tháng nào chưa có dữ liệu + còn thiếu file gì;
    đã sẵn sàng hoặc không phải dict ⇒ None."""
    if not isinstance(tra, dict):
        return None
    cdf = tra.get("cho_du_file") if isinstance(tra.get("cho_du_file"), dict) else tra
    if not isinstance(cdf, dict) or cdf.get("trang_thai") != "cho_du_file":
        return None
    ky = str(cdf.get("period") or tra.get("period") or "")
    ten_ky = f"Tháng {int(ky[5:7])}/{ky[:4]}" if _KY.fullmatch(ky) else "Kỳ hiện tại"
    con_thieu = [x for x in (cdf.get("con_thieu") or cdf.get("missing") or []) if isinstance(x, str)]
    if con_thieu:
        ten_thieu = [_TEN_FILE_CAN.get(x, x) for x in con_thieu]
        return f"{ten_ky} chưa có dữ liệu — vào trang Dữ liệu tải: {', '.join(ten_thieu)}."
    loi = _loi_thuong_ky(_chu(cdf.get("loi"), 300))
    return loi or f"{ten_ky} chưa có dữ liệu — vào trang Dữ liệu tải đủ file của kỳ."


def doc_an(goc, org) -> set:
    f = _tep(goc, org, "tai_lieu_an.json")
    try:
        return {s for s in json.loads(f.read_text(encoding="utf-8")) if isinstance(s, str) and _SHA.fullmatch(s)} if f.is_file() else set()
    except ValueError:
        return set()


def an_tai_lieu(goc, org, sha: str, an: bool = True) -> bool:
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        return False
    s = doc_an(goc, org)
    (s.add if an else s.discard)(sha)
    f = _tep(goc, org, "tai_lieu_an.json")
    t = f.with_suffix(".tam")
    t.write_text(json.dumps(sorted(s)), encoding="utf-8")
    t.chmod(0o600)
    t.replace(f)
    return True


def ds_tai_lieu(ds_kho: list[dict], an: set) -> list[dict]:
    return [
        {"sha": x["sha"], "ten": x.get("ten"), "trang": x.get("trang"), "luc": x.get("luc")}
        for x in ds_kho
        if not x.get("tam") and not x.get("an") and x.get("sha") not in an
    ]
