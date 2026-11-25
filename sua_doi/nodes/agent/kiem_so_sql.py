"""Phép so "số trong câu trả lời phải có trong kết quả SQL của lượt đó" — B 12/11/2026, đã giao; D phản biện 12/11.
GIỚI HẠN PHẢI NHỚ (D): phép này chỉ chứng minh AI CHÉP ĐÚNG kết quả công cụ. Nó KHÔNG chứng minh câu SQL trả lời đúng câu hỏi (nối bảng nhân đôi
dòng, quên điều kiện lọc ⇒ số sai mà vẫn "có nguồn"). Vì vậy luôn đi kèm: hiện câu SQL + bảng kết quả do code dựng cạnh câu trả lời; dữ liệu thật
chỉ truy vấn các view đã áp luật lọc; tiền thưởng không do SQL tính. Đừng gọi một mình nó là "rào đúng số".

Dùng: kiem_so(tra_loi, ket_qua_cong_cu, cau_hoi) -> {"dat": bool, "so_la": [...], "so_kiem": n, "ly_do": str}
  tra_loi           chữ AI trả lời. Khối ```…``` CHỈ được bỏ khi nó đúng bằng một câu SQL đã chạy trong lượt (cau_sql_da_chay); chữ trong `…` vẫn bị kiểm
  ket_qua_cong_cu   danh sách dict mà sql_query trả trong lượt ({"ok", "cot", "dong", "so_dong", …}); chỉ lấy kết quả ok = True
  cau_hoi           số người hỏi tự nêu được nhắc lại. GIỚI HẠN (như rào KPI): người hỏi nêu một số tiền rồi AI lặp lại như sự thật ⇒ vẫn qua
  cau_sql_da_chay   các câu SQL công cụ đã thật sự nhận trong lượt (tham số cau_sql của từng lần gọi, lấy từ bản ghi lượt — không lấy từ chữ AI)
Luật: mỗi số trong câu trả lời phải BẰNG GIÁ TRỊ một ô số của kết quả SQL, hoặc so_dong của một kết quả, hoặc một số trong câu hỏi.
  Số AI tự suy (tổng, hiệu, %, làm tròn) không có trong ô nào ⇒ KHÔNG ĐẠT (bắt AI tính trong SQL). Không có kết quả SQL ok mà câu trả lời có số ⇒ KHÔNG ĐẠT.
  Bỏ qua: ngày (dd/mm/yyyy, yyyy-mm-dd), số thứ tự đầu dòng ("1.", "2)"), số có chữ / "-" đứng NGAY TRƯỚC (mã DH-1042). Số dính đơn vị phía sau
  ("12.000đ", "30%") vẫn bị kiểm; "599k", "1,2 triệu", "2 tỷ" được nhân hệ số rồi mới so.
Số viết kiểu Việt hay kiểu Anh đều nhận: "1.234.567", "1,234,567", "12,5", "12.5" ⇒ so theo MỌI cách đọc hợp lệ của chuỗi (tập giá trị)."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_KHOI = re.compile(r"```[ \t]*([A-Za-z]*)[ \t]*\n?(.*?)```", re.S)
_NGAY = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b|\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")
_DAU_DONG = re.compile(r"(?m)^\s*\d{1,2}[.)]\s")
# [ghi chú] số được phép DÍNH chữ đơn vị phía sau ("12.000đ", "599k", "30%") — chỉ cấm chữ / chữ số / "-" đứng NGAY TRƯỚC (mã kiểu DH-1042, size42).
_SO = re.compile(r"(?<![\w.,-])(-?\d(?:[\d.,]*\d)?)[ \t]?([^\W\d_]*)")
_HE_SO = {"k": 1000, "nghìn": 1000, "ngàn": 1000, "nghin": 1000, "ngan": 1000, "tr": 10**6, "triệu": 10**6, "trieu": 10**6, "tỷ": 10**9, "tỉ": 10**9, "ty": 10**9}


def _chuan_sql(c: str) -> str:
    return re.sub(r"\s+", " ", (c or "")).strip().rstrip(";").strip().lower()


_NGHIN_CHAM, _NGHIN_PHAY = re.compile(r"\d{1,3}(?:\.\d{3})+"), re.compile(r"\d{1,3}(?:,\d{3})+")


def gia_tri(chuoi: str) -> set[Decimal]:
    """Giá trị số của một chuỗi. [D Đ1] KHÔNG còn cách đọc "bỏ hết dấu" vô điều kiện ("1,2" từng đọc thành 12):
      toàn chữ số ⇒ số nguyên · đúng khuôn ngăn nghìn (1–3 chữ số + các nhóm ĐÚNG 3 chữ số, một loại dấu) "1.234.567" / "1,234" ⇒ chỉ đọc nghìn ·
      ngăn nghìn + phần lẻ bằng dấu kia "1.234,5" / "1,234.5" · một dấu duy nhất không đúng khuôn nghìn "12,5" / "0.3825" ⇒ thập phân · còn lại ⇒ rỗng (số lạ)."""
    t = chuoi.strip().rstrip(".,")
    am = t.startswith("-")
    t = t.lstrip("-")
    u = None
    if t.isdigit():
        u = t
    elif _NGHIN_CHAM.fullmatch(t) or _NGHIN_PHAY.fullmatch(t):
        u = t.replace(".", "").replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", t):
        u = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d+", t):
        u = t.replace(",", "")
    elif re.fullmatch(r"\d+[.,]\d+", t):
        u = t.replace(",", ".")
    if u is None:
        return set()
    try:
        d = Decimal(u)
    except InvalidOperation:
        return set()
    return {-d if am else d}


_NGHIN_CACH = re.compile(r"(?<![\d.,])(\d{1,3})((?:[ \t\u00a0\u202f]\d{3})+)(?![\d])")   # [D Đ1] "12 000", "1 234 567" (kể cả dấu cách không ngắt) = MỘT số


def _so_trong(chu: str, cau_sql_da_chay=()) -> list[tuple[str, int]]:
    """Trả [(chuỗi số, hệ số)] — hệ số ≠ 1 khi số đi kèm "k / nghìn / triệu / tỷ" (vd "599k" ⇒ ("599", 1000)).
    [ghi chú] KHÔNG bỏ chữ trong dấu nháy ngược. [ghi chú] khối ``` chỉ được bỏ khi nội dung khối đúng bằng MỘT câu SQL đã thật sự chạy trong lượt
    (so sau khi gộp khoảng trắng, bỏ ";" cuối, không phân biệt hoa thường); khối khác vẫn bị tìm số."""
    da_chay = {_chuan_sql(c) for c in cau_sql_da_chay or () if c}

    def khoi(m):
        return " " if _chuan_sql(m.group(2)) in da_chay else " " + m.group(2) + " "
    chu = _KHOI.sub(khoi, chu or "").replace("`", " ")
    chu = _DAU_DONG.sub(" ", _NGAY.sub(" ", chu))
    chu = _NGHIN_CACH.sub(lambda m: m.group(1) + re.sub(r"\s", "", m.group(2)), chu)
    return [(m.group(1), _HE_SO.get(m.group(2).lower(), 1)) for m in _SO.finditer(chu)]


def _gia_tri_he_so(so: tuple[str, int]) -> set[Decimal]:
    return {g * so[1] for g in gia_tri(so[0])}


def _nguon(ket_qua_cong_cu: list[dict], cau_hoi: str) -> tuple[set[Decimal], int]:
    nguon, so_kq = set(), 0
    for r in ket_qua_cong_cu or []:
        if not isinstance(r, dict) or r.get("ok") is not True:
            continue
        so_kq += 1
        if isinstance(r.get("so_dong"), int):
            nguon.add(Decimal(r["so_dong"]))
        for dong in r.get("dong") or []:
            for o in dong if isinstance(dong, (list, tuple)) else list(dong.values()) if isinstance(dong, dict) else [dong]:
                if isinstance(o, bool) or o is None:
                    continue
                if isinstance(o, (int, float)):
                    nguon.add(Decimal(str(o)))
                elif isinstance(o, str):
                    for s in _so_trong(o):
                        nguon |= _gia_tri_he_so(s)
    for s in _so_trong(cau_hoi or ""):
        nguon |= _gia_tri_he_so(s)
    return nguon, so_kq


def kiem_so(tra_loi: str, ket_qua_cong_cu: list[dict], cau_hoi: str = "", cau_sql_da_chay=()) -> dict:
    so = _so_trong(tra_loi, cau_sql_da_chay)
    nguon, so_kq = _nguon(ket_qua_cong_cu, cau_hoi)
    la = [s[0] + ("" if s[1] == 1 else f"×{s[1]}") for s in so if not (_gia_tri_he_so(s) & nguon)]
    if la:
        ly = ("câu trả lời có số nhưng lượt này không có kết quả SQL nào chạy được" if so_kq == 0
              else "có số không nằm trong kết quả SQL của lượt (AI tự tính / tự gõ) — phải để SQL tính")
        return {"dat": False, "so_la": la, "so_kiem": len(so), "ly_do": ly}
    return {"dat": True, "so_la": [], "so_kiem": len(so), "ly_do": ""}



# LUẬT 2 — "0 dòng mà chưa dò": MỘT nguồn ở file công cụ bản 2 (chay_thu/sql_chi_doc_v2/sql_query.py — cùng hàm công cụ dùng để nhắc AI ngay trong lượt).
# Ở đây dùng lại để kiểm SAU lượt (script đo / khối Rào của trang): kiem_0_dong(cau_sql_da_chay, ket_qua_cong_cu) -> {"dat", "cot_chua_do", "ly_do", "chi_dan"}.
# Tìm file công cụ ở 2 nơi: (1) trên máy: thư mục chay_thu/sql_chi_doc_v2 cạnh thư mục này; (2) TRONG HỘP ab-* (C gắn riêng file này vào hộp):
# công cụ bản 2 nằm ở nodes/agent/sql_query.py. Hộp còn gắn công cụ BẢN 1 (chưa có luật) ⇒ kiem_0_dong ném lỗi rõ khi được gọi — KHÔNG lặng lẽ cho qua;
# kiem_so (luật 1) vẫn dùng được.
import importlib.util as _ilu
from pathlib import Path as _Path

NHAN_CHUA_CHAC = "chưa chắc — truy vấn trả 0 dòng nhưng chưa kiểm cách dữ liệu ghi ở cột đang lọc (điều kiện lọc có thể không khớp)"


def _nap_luat_0_dong():
    import os as _os
    # KIEM_SQL_CONG_CU: đường file công cụ đặt tường minh (bộ thử trong hộp dùng một lần — script hộp không cho gắn vào /srv)
    for f in (_Path(_os.environ["KIEM_SQL_CONG_CU"]),) if _os.environ.get("KIEM_SQL_CONG_CU") else (
            _Path(__file__).resolve().parent.parent / "sql_chi_doc_v2" / "sql_query.py", _Path("/srv/src/nodes/agent/sql_query.py")):
        if f.is_file() and "def kiem_0_dong(" in f.read_text(encoding="utf-8"):
            spec = _ilu.spec_from_file_location("_sql_query_ban_2", f)
            m = _ilu.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m.kiem_0_dong
    return None


_KIEM_0_DONG = _nap_luat_0_dong()


def kiem_0_dong(cau_sql_da_chay: list[str], ket_qua_cong_cu: list[dict]) -> dict:
    """Trả {"dat", "cot_chua_do", "ly_do", "chi_dan"} — xem luật ở file công cụ bản 2. Hai danh sách cùng thứ tự gọi trong lượt."""
    if _KIEM_0_DONG is None:
        raise RuntimeError("chưa có luật 0 dòng: không tìm thấy sql_query bản 2 (chay_thu/sql_chi_doc_v2/sql_query.py hoặc /srv/src/nodes/agent/sql_query.py)")
    return _KIEM_0_DONG(cau_sql_da_chay, ket_qua_cong_cu)


def kiem_luot(tra_loi: str, so_luot: list[dict], cau_hoi: str = "") -> dict:
    """Một lời gọi cho khối Rào chắn chế độ "sql" (C): `so_luot` = danh sách THEO THỨ TỰ [{"cau_sql": chữ, "ket_qua": dict công cụ trả}].
    Chạy luật 2 (0 dòng chưa dò) rồi luật 1 (số ∈ kết quả SQL). Trả {"dat", "ly_do", "luat": "0_dong" | "so" | "", "chi_dan", "so_la", "cot_chua_do"}."""
    sql = [str((x or {}).get("cau_sql") or "") for x in so_luot or []]
    kq = [(x or {}).get("ket_qua") if isinstance((x or {}).get("ket_qua"), dict) else {"ok": False} for x in so_luot or []]
    k0 = kiem_0_dong(sql, kq)
    if not k0["dat"]:
        return {"dat": False, "luat": "0_dong", "ly_do": k0["ly_do"], "chi_dan": k0["chi_dan"], "cot_chua_do": k0["cot_chua_do"], "so_la": []}
    k1 = kiem_so(tra_loi, kq, cau_hoi, sql)
    return {"dat": k1["dat"], "luat": "" if k1["dat"] else "so", "ly_do": k1["ly_do"], "chi_dan": "", "cot_chua_do": [], "so_la": k1["so_la"]}
