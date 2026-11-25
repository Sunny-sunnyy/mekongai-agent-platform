"""[BẢN 2 — nhóm kỹ thuật 12/11/2026, đã giao sau lượt câu bẫy text-to-SQL 3 / 5; CHƯA GẮN (bản đang gắn: chay_thu/sua_doi/nodes/agent/sql_query.py 25975106)]
Thêm so với bản 1, ba lớp rào + chay() GIỮ NGUYÊN:
  (a) LUẬT "0 dòng mà chưa dò" bằng code (kiem_0_dong): câu cuối trả rỗng với điều kiện lọc so cột với GIÁ TRỊ CHỮ mà trong lượt chưa có câu dò cột đó
      ⇒ công cụ trả kèm `chua_do` + chỉ dẫn chạy SELECT DISTINCT rồi viết lại điều kiện (AI hay kết luận "không có" từ 0 dòng do sai định dạng ngày /
      hoa thường — lời dặn không giữ được, phải là code). Nhớ lịch sử câu đã chạy TRONG MỘT lượt (bao đóng của sql_query_for).
  (b) MÔ TẢ CÔNG CỤ sinh từ DB: mỗi cột một giá trị ví dụ thật + ghi rõ cột ngày lưu dạng chữ dd/mm/yyyy và cách so sánh (mo_ta_cot) — không gõ tay.
Phép kiểm sau lượt (chay_thu/hoi_du_lieu/kiem_so_sql.py) nhập kiem_0_dong TỪ FILE NÀY — một nguồn.

[nhóm kỹ thuật, 05/11/2026 — người vận hành thiết kế bản 2 lúc 21:11: "A. Duyệt, cho B dựng"] Công cụ `sql_query` CHỈ ĐỌC cho khối Agent.
Thiết kế: chay_thu/sql_chi_doc/THIET_KE.md (mục 2–4, 6–7). Dựng lại phần rào tốt nhất của txt2sql (Capitol) và sửa 3 chỗ chưa tốt của họ
(lấy hết dòng rồi cắt 20 không báo; tự thêm LIMIT 10; câu lỗi = "không có dữ liệu").

AI chỉ điền MỘT tham số: `cau_sql` (một câu SELECT). Mã khách do HỆ THỐNG truyền khi dựng công cụ (`sql_query_for(customer_id)`, bao đóng như
`_order_lookup_for` trong native_tools) — chữ ký công cụ AI thấy không có mã khách. File DB, trần giờ / dòng / độ dài đều cố định trong code.

Ba lớp rào:
 1. Kết nối KHÔNG chứa dữ liệu của khách khác: mỗi lượt gọi một DB trong bộ nhớ; code ATTACH file gốc mode=ro (kiểm ghi ⇒ readonly), chép đúng
    4 bảng (2 chung + 2 của khách, lọc bằng tham số ràng buộc), DETACH, PRAGMA query_only=ON. Bỏ cách TEMP VIEW vì CTE trùng tên lọt qua (mục 6).
 2. Chính SQLite phân tích câu: authorizer CHO PHÉP THEO DANH SÁCH (SELECT; READ 4 bảng chép; một số hàm) — còn lại từ chối
    (ATTACH, PRAGMA, mọi lệnh ghi, CTE đệ quy, load_extension, zeroblob / randomblob…). Python tự từ chối chuỗi có 2 câu.
 3. Trần + báo rõ: quá 2 giây ⇒ ngắt; độ dài chuỗi / blob ≤ 1 MB; tối đa 200 dòng + cờ da_cat; ô > 500 ký tự cắt có dấu;
    lỗi chia loại (bi_chan / cu_phap / qua_gio / qua_lon / loi_he_thong) — KHÁC "0 dòng" (ok True, so_dong 0). Không tự thêm LIMIT.
"""

import asyncio
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DB_FILE = Path(os.environ.get("SQL_QUERY_DB_FILE", "/srv/sql_chi_doc/du_lieu_gia.sqlite"))
TRAN_GIAY, TRAN_DONG, TRAN_O, TRAN_SQL, TRAN_DO_DAI = 2.0, 200, 500, 4000, 1_000_000
MA_KHACH = re.compile(r"[A-Z0-9-]{1,20}")

# Danh sách bảng chép mỗi lượt (CHỐT). Bảng của khách lọc bằng tham số ràng buộc `?` = mã khách.
BANG = {
    "san_pham": "SELECT product, gia_vnd FROM goc.san_pham",
    "ton_kho": "SELECT product, size, so_luong FROM goc.ton_kho",
    "don_hang_cua_toi": "SELECT order_id, ordered_at, delivery_status, exchange_policy, exchange_deadline FROM goc.don_hang WHERE customer_id = ?",
    "dong_don_cua_toi": ("SELECT d.order_id, d.product, d.size, d.qty, d.unit_price_vnd FROM goc.dong_don d "
                         "JOIN goc.don_hang h ON h.order_id = d.order_id WHERE h.customer_id = ?"),
}
# "like": SQLite coi `x LIKE y` là gọi hàm like() ⇒ thiếu thì AI không tìm được sản phẩm theo một phần tên (B tìm ra khi thử m1, 06/11). GLOB vẫn chặn.
HAM = {"count", "sum", "total", "avg", "min", "max", "abs", "round", "coalesce", "ifnull", "nullif", "iif", "lower", "upper", "length",
       "substr", "substring", "trim", "ltrim", "rtrim", "replace", "instr", "printf", "format", "date", "time", "datetime", "strftime", "julianday", "cast",
       "like"}


class _TuChoi(Exception):
    pass


def _authorizer(action, x, y, db, _src):
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and db in (None, "main") and x in BANG:       # count(*) gọi READ với db=None (D, soát vòng 1)
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (y or "").lower() in HAM:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _mo(ma_khach: str) -> sqlite3.Connection:
    if not isinstance(ma_khach, str) or not MA_KHACH.fullmatch(ma_khach):
        raise _TuChoi("Thiếu / sai mã khách do hệ thống điền — không chạy truy vấn.")
    if not DB_FILE.is_file():
        raise FileNotFoundError("không thấy file dữ liệu")
    m = sqlite3.connect("file::memory:", uri=True)
    try:
        m.execute("ATTACH ? AS goc", (f"file:{DB_FILE}?mode=ro",))            # đường file do code đưa (tham số ràng buộc), AI không chạm
        try:
            m.execute("CREATE TABLE goc.__thu_ghi(x)")
            raise RuntimeError("ATTACH mode=ro không có tác dụng — dừng")       # SQLite không bật URI ⇒ file gốc ghi được ⇒ không chạy
        except sqlite3.OperationalError as e:
            if "readonly" not in str(e):
                raise
        for ten, q in BANG.items():
            m.execute(f"CREATE TABLE {ten} AS {q}", (ma_khach,) if "?" in q else ())
        m.commit()
        m.execute("DETACH goc")
        m.execute("PRAGMA query_only=ON")
        m.set_authorizer(_authorizer)
        m.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, TRAN_DO_DAI)
        return m
    except Exception:
        m.close()
        raise


def _o(v):
    if isinstance(v, bytes):
        return f"<dữ liệu nhị phân {len(v)} byte>"
    if isinstance(v, str) and len(v) > TRAN_O:
        return v[:TRAN_O] + f"…[cắt, ô dài {len(v)} ký tự]"
    return v


def _loai(e: Exception) -> str:
    t = str(e)
    # "not authorized" (lệnh), "authorization denied" (VACUUM…), "prohibited" (đọc cột), "readonly" (query_only) — đều là rào chặn
    if isinstance(e, sqlite3.ProgrammingError) or any(k in t for k in ("not authorized", "authorization denied", "prohibited", "readonly")):
        return "bi_chan"
    if "interrupted" in t:
        return "qua_gio"
    if "too big" in t:
        return "qua_lon"
    return "cu_phap"


_LOI_CHU = {"bi_chan": "Câu bị chặn: chỉ được MỘT câu SELECT trên các bảng đã liệt kê.",
            "qua_gio": f"Câu chạy quá {TRAN_GIAY:g} giây nên bị ngắt — viết câu gọn hơn (lọc / gộp trước).",
            "qua_lon": "Kết quả có ô quá lớn.",
            "cu_phap": "Câu SQL sai (cú pháp / tên bảng / tên cột) — sửa câu rồi chạy lại. Đây là LỖI, không phải \"không có dữ liệu\"."}


def chay(ma_khach: str, cau_sql: str) -> dict:
    """Chạy một câu cho một khách (đồng bộ). Luôn trả dict, không ném lỗi."""
    t0 = time.monotonic()
    if not isinstance(cau_sql, str) or not cau_sql.strip():
        return {"ok": False, "loai": "cu_phap", "error": "Thiếu câu SQL."}
    if len(cau_sql) > TRAN_SQL:
        return {"ok": False, "loai": "qua_lon", "error": f"Câu SQL dài quá {TRAN_SQL} ký tự."}
    try:
        m = _mo(ma_khach)
    except _TuChoi as e:
        return {"ok": False, "loai": "bi_chan", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.warning("[NATIVE_TOOL] sql_query không mở được dữ liệu: %s", type(e).__name__)
        return {"ok": False, "loai": "loi_he_thong", "error": f"Không mở được dữ liệu ({type(e).__name__}) — báo người vận hành, đừng đoán số."}
    try:
        m.set_progress_handler(lambda: 1 if time.monotonic() - t0 > TRAN_GIAY else 0, 1000)
        cur = m.execute(cau_sql)
        cot = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(TRAN_DONG + 1)                                     # không đọc hết vào bộ nhớ
        da_cat = len(rows) > TRAN_DONG
        dong = [[_o(v) for v in r] for r in rows[:TRAN_DONG]]
        r = {"ok": True, "cot": cot, "dong": dong, "so_dong": len(dong), "da_cat": da_cat, "tran_dong": TRAN_DONG}
        if da_cat:
            r["ghi_chu"] = f"KẾT QUẢ ĐÃ BỊ CẮT: chỉ trả {TRAN_DONG} dòng đầu, còn dòng khác. Muốn số tổng thì dùng count / sum trong SQL."
        elif not dong:
            r["ghi_chu"] = "Không có dòng nào khớp — câu chạy đúng, không phải lỗi."
    except Exception as e:  # noqa: BLE001
        loai = _loai(e)
        r = {"ok": False, "loai": loai, "error": _LOI_CHU[loai] + f" (SQLite: {str(e)[:120]})"}
    finally:
        m.close()
    logger.info("[NATIVE_TOOL] sql_query ok=%s loai=%s so_dong=%s giay=%.2f sql_chars=%d", r.get("ok"), r.get("loai"), r.get("so_dong"),
                time.monotonic() - t0, len(cau_sql))
    return r


# ---------------------------------------------------------------------------------------------------------------------------------------------
# LUẬT 2 — "0 dòng mà chưa dò" (B 12/11/2026, đã giao sau lượt câu bẫy 3 / 5: B2 lọc ngày '2026-10-20' trên cột ghi dd/mm/yyyy ⇒ đếm 0 ⇒ AI khẳng
# định "không có đơn nào"; B3 LIKE '%áo khoác gió%' (chữ á thường, dữ liệu ghi "Áo") ⇒ 0 dòng ⇒ AI dừng). Phép so số ở trên KHÔNG bắt được: câu sai
# không có con số. Lời dặn đã bảo "dò trước" nhưng AI không làm ⇒ luật phải nằm trong CODE.
# Luật: câu SQL chạy được CUỐI CÙNG của lượt trả "rỗng" (0 dòng, HOẶC đúng 1 dòng mà mọi ô đều 0 / NULL với câu có hàm gộp + WHERE) và câu đó có
# điều kiện lọc so cột với một GIÁ TRỊ CHỮ ('…') ⇒ với MỖI cột bị lọc như vậy, trong lượt phải có một câu DÒ chạy được, trả > 0 dòng, có nêu cột đó
# ở phần SELECT (hoặc SELECT *) và KHÔNG lọc chính cột đó. Thiếu ⇒ KHÔNG ĐẠT: không được trả lời "không có"; người gọi (script / khối Rào) bắt làm
# lại 1 lần kèm chỉ dẫn dò; vẫn thiếu ⇒ gắn nhãn NHAN_CHUA_CHAC. Lọc theo số (gia_vnd > 500000) hay so hai cột không tính — rỗng khi đó là rỗng thật.
# Giới hạn [đã soát 12/11]: câu dò trên BẢNG KHÁC có cùng tên cột vẫn được tính; phép kiểm SAU lượt chỉ xét câu chạy CUỐI (lúc gọi công cụ thì từng câu
# rỗng đều bị nhắc); chỉ bắt kết quả RỖNG — điều kiện sai mà vẫn ra dòng (so chuỗi ngày) không bắt được.
# Giới hạn: tách cột bằng biểu thức chính quy trên chữ câu SQL (không phải bộ phân tích SQL) ⇒ câu lồng nhiều tầng có thể bị bắt oan (phía an toàn:
# chỉ tốn 1 lần dò) hoặc sót cột trong truy vấn con.
NHAN_CHUA_CHAC = "chưa chắc — truy vấn trả 0 dòng nhưng chưa kiểm cách dữ liệu ghi ở cột đang lọc (điều kiện lọc có thể không khớp)"
_HAM_GOP = re.compile(r"\b(count|sum|total|avg|min|max)\s*\(", re.I)
_LOC_CHU = re.compile(r"(?:\b(?:lower|upper|trim)\s*\(\s*)?\b((?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)\s*\)?\s*"
                      r"(?:=|==|<>|!=|>=|<=|>|<|\bnot\s+like\b|\blike\b|\bglob\b|\bin\s*\(|\bbetween\b)\s*'", re.I)


def _sau_where(sql: str) -> str:
    m = re.search(r"\bwhere\b(.*)", sql or "", re.I | re.S)
    return re.split(r"\bgroup\s+by\b|\border\s+by\b|\blimit\b", m.group(1), maxsplit=1, flags=re.I)[0] if m else ""


def _cot_loc_chu(sql: str) -> set[str]:
    """Tên các cột (bỏ bí danh bảng, chữ thường) bị so với GIÁ TRỊ CHỮ trong WHERE."""
    return {c.split(".")[-1].lower() for c in _LOC_CHU.findall(_sau_where(sql))}


def _rong(sql: str, kq: dict) -> bool:
    if kq.get("so_dong") == 0:
        return True
    dong = kq.get("dong") or []
    return (kq.get("so_dong") == 1 and len(dong) == 1 and all(o in (0, None, 0.0) for o in dong[0])
            and bool(_HAM_GOP.search(sql or "")) and bool(_sau_where(sql)))


def _la_cau_do(sql: str, kq: dict, cot: str) -> bool:
    if kq.get("ok") is not True or not kq.get("so_dong"):
        return False
    m = re.search(r"\bselect\b(.*?)\bfrom\b", sql or "", re.I | re.S)
    chon = (m.group(1) if m else "").lower()
    co_cot = "*" in chon or bool(re.search(rf"\b{re.escape(cot)}\b", chon))
    return co_cot and cot not in _cot_loc_chu(sql) and not _HAM_GOP.search(chon.replace("distinct", ""))


def kiem_0_dong(cau_sql_da_chay: list[str], ket_qua_cong_cu: list[dict]) -> dict:
    """cau_sql_da_chay[i] ứng với ket_qua_cong_cu[i] (thứ tự gọi trong lượt). Trả {"dat", "cot_chua_do", "ly_do", "chi_dan"}."""
    cap = [(s or "", k) for s, k in zip(cau_sql_da_chay or [], ket_qua_cong_cu or []) if isinstance(k, dict) and k.get("ok") is True]
    if not cap:
        return {"dat": True, "cot_chua_do": [], "ly_do": "", "chi_dan": ""}      # không có câu nào chạy được: việc của phép so số / lời báo lỗi
    sql, kq = cap[-1]
    if not _rong(sql, kq):
        return {"dat": True, "cot_chua_do": [], "ly_do": "", "chi_dan": ""}
    thieu = sorted(c for c in _cot_loc_chu(sql) if not any(_la_cau_do(s, k, c) for s, k in cap))
    if not thieu:
        return {"dat": True, "cot_chua_do": [], "ly_do": "", "chi_dan": ""}
    return {"dat": False, "cot_chua_do": thieu,
            "ly_do": f"câu truy vấn cuối trả 0 dòng nhưng chưa dò cách dữ liệu ghi ở cột {thieu} — chưa được kết luận là không có",
            "chi_dan": "Kết quả rỗng có thể do điều kiện lọc không khớp cách dữ liệu ghi (định dạng ngày, hoa / thường, dấu). Hãy chạy: "
                       + "; ".join(f"SELECT DISTINCT {c} FROM <bảng> LIMIT 20" for c in thieu) + " — xem giá trị thật rồi viết lại điều kiện."}


_NGAY_CHU = re.compile(r"\d{2}/\d{2}/\d{4}")
# [D Đ4 — LUẬT] mo_ta_cot đưa chữ vào MÔ TẢ CÔNG CỤ = vị trí lời dặn (mạnh hơn kết quả công cụ): một ô chứa câu lệnh sẽ thành chỉ dẫn cho AI, và giá trị
# của khách đi sang nhà cung cấp AI mỗi lượt. DỮ LIỆU GIẢ: được đưa giá trị ví dụ (mặc định). DỮ LIỆU THẬT: BẮT BUỘC đặt SQL_QUERY_VI_DU_GIA_TRI=khong
# ⇒ chỉ đưa KHUÔN (ngày dd/mm/yyyy dạng chữ / số / chữ), không đưa giá trị.
VI_DU_GIA_TRI = os.environ.get("SQL_QUERY_VI_DU_GIA_TRI", "co") == "co"


def mo_ta_cot(ma_khach: str) -> str:
    """Một đoạn chữ (không xuống dòng, không thụt lề — pydantic-ai bỏ khối thụt lề ngoài Args) tả CÁCH DỮ LIỆU GHI ở từng cột, sinh từ chính DB:
    mỗi cột một giá trị ví dụ thật (cắt 40 ký tự); cột có giá trị dạng dd/mm/yyyy được ghi rõ là NGÀY LƯU DẠNG CHỮ + cách so sánh đúng.
    Không mở được dữ liệu ⇒ trả "" (mô tả gốc vẫn dùng được)."""
    try:
        m = _mo(ma_khach)
    except Exception:  # noqa: BLE001
        return ""
    try:
        phan, cot_ngay = [], []
        for bang in BANG:
            cot = [d[0] for d in m.execute(f"SELECT * FROM {bang} LIMIT 0").description]
            vd = []
            for c in cot:
                r = m.execute(f"SELECT {c} FROM {bang} WHERE {c} IS NOT NULL AND {c} <> '' LIMIT 1").fetchone()
                v = r[0] if r else None
                if isinstance(v, str) and _NGAY_CHU.fullmatch(v):
                    cot_ngay.append(f"{bang}.{c}")
                if not VI_DU_GIA_TRI:      # [D Đ4] dữ liệu THẬT: chỉ đưa KHUÔN, không đưa giá trị ô vào mô tả công cụ
                    vd.append(f"{c} ({'ngày dd/mm/yyyy dạng chữ' if isinstance(v, str) and _NGAY_CHU.fullmatch(v) else 'số' if isinstance(v, (int, float)) else 'chữ'})")
                else:
                    vd.append(f"{c} = {v!r}"[:60] if v is not None else f"{c} (trống)")
            phan.append(f"{bang}: " + ", ".join(vd))
        chu = ("Ví dụ cách dữ liệu ghi (mỗi cột một giá trị thật): " if VI_DU_GIA_TRI else "Kiểu dữ liệu từng cột: ") + "; ".join(phan) + "."
        if cot_ngay:
            chu += (" Cột ngày " + ", ".join(cot_ngay) + " lưu dạng CHỮ dd/mm/yyyy — KHÔNG so sánh >, <, BETWEEN trực tiếp và không so với '2026-10-20';"
                    " muốn lọc / sắp theo ngày thì đổi sang yyyy-mm-dd: substr(cột,7,4) || '-' || substr(cột,4,2) || '-' || substr(cột,1,2)"
                    " (vd tháng 10/2026: cột LIKE '%/10/2026').")
        chu += " Chưa chắc giá trị ghi thế nào thì chạy SELECT DISTINCT cột FROM bảng trước; kết quả 0 dòng có kèm chua_do nghĩa là PHẢI dò rồi viết lại điều kiện."
        return chu
    finally:
        m.close()


def sql_query_for(customer_id: str):
    """Dựng công cụ cho MỘT khách (hệ thống gọi, mã khách lấy từ ô tool_customer_id — không phải AI). Chữ ký AI thấy chỉ có cau_sql."""
    async def sql_query(cau_sql: str) -> dict:
        """Hỏi dữ liệu cửa hàng bằng MỘT câu SQL SELECT (SQLite), chỉ đọc. Dùng khi cần tra tồn kho, giá, hoặc đơn hàng CỦA KHÁCH ĐANG NHẮN;
        để hệ thống tính số (count, sum…), đừng tự nhẩm. Kết quả tối đa 200 dòng, có cờ da_cat nếu bị cắt.
        Các bảng (chỉ có 4 bảng này): san_pham(product, gia_vnd) là giá bán; ton_kho(product, size, so_luong) là tồn kho theo size;
        don_hang_cua_toi(order_id, ordered_at, delivery_status, exchange_policy, exchange_deadline) là các đơn CỦA KHÁCH ĐANG NHẮN;
        dong_don_cua_toi(order_id, product, size, qty, unit_price_vnd) là các dòng hàng trong đơn của khách đang nhắn.
        Tìm theo một phần tên thì dùng LIKE '%...%'; LIKE, lower, upper chỉ bỏ qua hoa thường với chữ KHÔNG dấu ("Áo" khác "áo"),
        nên với chữ có dấu hãy viết đúng hoa thường như trong dữ liệu, hoặc tìm bằng phần không dấu (vd '%thun%'). 0 dòng chưa chắc là không có hàng.

        Args:
            cau_sql: một câu SELECT duy nhất, vd "SELECT size, so_luong FROM ton_kho WHERE product = 'Áo thun cổ tròn'".
        """
        r = await asyncio.to_thread(chay, customer_id, cau_sql)
        lich_su.append((cau_sql if isinstance(cau_sql, str) else "", r))
        if r.get("ok") is True:                                   # [BẢN 2 (a)] rỗng mà chưa dò cột đang lọc ⇒ trả kèm chỉ dẫn, không để AI kết luận "không có"
            k = kiem_0_dong([c for c, _ in lich_su], [x for _, x in lich_su])
            if not k["dat"]:
                r = dict(r, chua_do=k["cot_chua_do"], ghi_chu="CHƯA ĐƯỢC KẾT LUẬN LÀ KHÔNG CÓ. " + k["chi_dan"])
        return r
    lich_su: list = []                                            # các câu đã chạy trong lượt này (một bao đóng = một khối Agent của một lượt)
    them = mo_ta_cot(customer_id)                                 # [BẢN 2 (b)]
    if them and sql_query.__doc__ and "\n\n        Args:" in sql_query.__doc__:
        dau, duoi = sql_query.__doc__.split("\n\n        Args:", 1)
        sql_query.__doc__ = dau + "\n        " + them + "\n\n        Args:" + duoi
    return sql_query
