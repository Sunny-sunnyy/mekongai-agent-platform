"""Dựng file "kết quả vừa trả lời" — chạy BÊN TRONG capitol-ab-worker (đối chiếu kỹ thuật).
Máy chủ trang (xuat.py) gửi script này qua stdin `docker exec -i capitol-ab-worker python -I -`, dòng ĐẦU là `DU_LIEU = {...}` do xuat.py chèn:
  {"goi": gói Rào chắn chế độ code đã kiểm (kiem.dat + sha), "dang": "xlsx" | "docx" | "pdf" | "pptx", "xf": đường xuat_file.py (tuỳ chọn, để thử)}.
In ra MỘT dòng JSON: {"ok": true, "ten", "b64", "sha16", "so_da_so"} hoặc {"ok": false, "loi": lời thường}. Không gọi AI. Không ghi gì ngoài thư mục
tạm của chính lượt này (xoá khi xong).

Vì sao code dựng (không để AI viết code như Capitol): file mang SỐ TIỀN thật — số phải là số của bảng lõi KPI, không qua chữ AI.
Luật (đối chiếu kỹ thuật): (a) chữ NHÁP ở tiêu đề + tên file + MỖI sheet / trang · (c) đọc lại file ⇒ so TỪNG số với bảng nguồn, lệch ⇒ không trả file
· tên thật chuẩn hoá như export_report.clean_text · Excel đúng bố cục export_report.to_xlsx của lõi ⇒ kiểm bằng CHÍNH _kpi_kiem_o (Người duyệt).
"""
import ast
import asyncio
import base64
import collections
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata

NHAP = "BẢNG THƯỞNG TẠM TÍNH — NHÁP, CHƯA DUYỆT"
NHAP_TEP = "NHAP_bang_thuong"
# [ghi chú kỹ thuật] nhãn do CODE điền (không do AI viết) ở dòng đầu MỖI sheet + tên file
TU_DUYET = "TỰ DUYỆT KHI QUÁ HẠN — CHƯA CÓ NGƯỜI DUYỆT"
TU_DUYET_TEP = "TU_DUYET_QUA_HAN"


def nhan_tu_duyet(g: dict) -> int | None:
    """[bản 2 — điều phối: nhãn thật ở MỌI cửa ra] Gói của một lượt TỰ DUYỆT khi quá hạn (duyet.kieu = "tu_duyet_qua_han") ⇒ số giây đã chờ; gói khác ⇒ None.
    Gói nói là tự duyệt mà số giây sai dạng ⇒ ValueError (không lặng lẽ bỏ nhãn)."""
    d = g.get("duyet") if isinstance(g, dict) else None
    if not isinstance(d, dict) or d.get("kieu") != "tu_duyet_qua_han":
        return None
    n = d.get("cho_giay")
    if isinstance(n, bool) or not isinstance(n, int) or not 30 <= n <= 3600:
        raise ValueError("gói tự duyệt khi quá hạn sai khuôn (số giây chờ) — không xuất file")
    return n


def _tua(tu_duyet) -> str:
    return NHAP if tu_duyet is None else f"{TU_DUYET} · {NHAP}"


def _cau_tu_duyet(tu_duyet: int) -> str:
    return f"{TU_DUYET}: không ai bấm duyệt trong {int(tu_duyet)} giây nên hệ thống tự cho lượt đi tiếp; bảng chính thức chỉ có sau khi một người bấm duyệt."
NGUON = "số lấy từ bảng thưởng của lõi KPI (code), không qua AI — file do code ghi"
MAX_RA = 10 * 1024 * 1024
XF = "/srv/src/nodes/agent/xuat_file.py"
NODE = "/srv/src/nodes/logic/human_intervention/node.py"
TIEN = re.compile(r"(\d{1,3}(?:\.\d{3})*)\s+đ(?!\w)")   # mọi số tiền in ra đều dạng "1.234.567 đ" ⇒ trích lại được, không lẫn số trang / giờ


def chuan(x) -> str:
    """= export_report.clean_text của lõi: NFC + bỏ ký tự điều khiển (giữ xuống dòng)."""
    x = unicodedata.normalize("NFC", str(x))
    return "".join(c for c in x if c == "\n" or not unicodedata.category(c).startswith("C"))


def vn(x: int) -> str:
    return f"{x:,}".replace(",", ".") + " đ"


def kiem_goi(g: dict) -> dict:
    """Kiểm lại trong hộp (không tin bên gọi): kiem.dat True + sha256 bảng tính lại == sha256_bang + hình bảng đủ."""
    b, sha = g.get("bang_thuong"), g.get("sha256_bang")
    if not isinstance(b, dict) or (g.get("kiem") or {}).get("dat") is not True:
        raise ValueError("câu trả lời này chưa qua bước kiểm số — không xuất file")
    if hashlib.sha256(json.dumps(b, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != sha:
        raise ValueError("bảng số không khớp mã kiểm — không xuất file")
    for k in ("employees", "kpis", "lines", "team_total"):
        if k not in b:
            raise ValueError("bảng số thiếu phần cần để xuất")
    tien = {(d["employee_id"], d["kpi"]): d["amount"] for d in b["lines"]}
    if any(isinstance(v, bool) or not isinstance(v, int) for v in tien.values()) or len(tien) != len(b["employees"]) * len(b["kpis"]):
        raise ValueError("bảng số thiếu ô nhân viên × KPI hoặc tiền không phải số nguyên")
    if sum(tien.values()) != b["team_total"]:
        raise ValueError("tổng đội trong bảng không bằng tổng các ô — không xuất file")
    return b


def lay_ten(sha: str) -> dict | None:
    """Bảng tên {mã che: {name, sm_code}} từ ĐƯỜNG HỆ THỐNG, cùng lần đọc với sha (như Người duyệt). KPI_NAMES tắt ⇒ None. Lỗi ⇒ NÉM (không đoán tên)."""
    import httpx

    goc = os.environ.get("CHAY_THU_KPI_URL", "").rstrip("/")
    ten, khoa = os.environ.get("CHAY_THU_KIEM_CODE_KHOA_TEN", ""), os.environ.get("CHAY_THU_KIEM_CODE_KHOA", "")
    if not goc.startswith("http") or not ten or not khoa:
        raise ValueError("hộp chưa cấu hình đường tới lõi KPI")
    r = httpx.get(f"{goc}/api/system/bonus-table", headers={ten: khoa}, timeout=60, trust_env=False)
    j = r.json() if r.status_code == 200 else {}
    if j.get("sha256") != sha:
        raise ValueError("bảng số hiện tại của lõi KPI khác bảng đã trả lời (số đã đổi) — hỏi lại rồi xuất")
    n = j.get("names")
    if n is None:
        return None
    if not isinstance(n, dict) or not all(isinstance(v, dict) for v in n.values()):
        raise ValueError("bảng tên của lõi sai khuôn")
    return n


def nguoi(b, names, nv) -> list[str]:
    if names is None:
        return [nv]
    e = names.get(nv) or {}
    return [nv, chuan(e.get("name") or ""), chuan(e.get("sm_code") or "")]


def dau_nguoi(names) -> list[str]:
    return ["Mã NV", "Tên", "Mã SM"] if names is not None else ["Mã NV"]


# ---------------- [ghi chú kỹ thuật] PHẦN ĐẦU của file = nội dung câu vừa hỏi ----------------
# Bối cảnh: hỏi "chỉ tiêu + mốc thưởng KPI Turnover của NV01" rồi xuất file ⇒ file chỉ có bảng thưởng cả đội, không có chỉ tiêu / bảng mốc vừa hỏi.
# Nguồn của phần đầu = KẾT QUẢ CÔNG CỤ của chính lượt (trang đọc từ lịch sử lời gọi của khối AI, gửi kèm tên + tham số + kết quả) — KHÔNG lấy từ chữ AI viết.
# Không tin thứ gửi sang: với mỗi lần gọi, bộ dựng GỌI LẠI đúng công cụ đó ở lõi KPI bằng đúng tham số (không kèm mã lượt ⇒ lõi không ghi vào nhật ký của lượt
# nào — kpi_agent/backend/main.py _run_key) và so với kết quả gửi sang: KHÁC một chỗ, hoặc không gọi lại được ⇒ KHÔNG in lần tra đó (không in số chưa kiểm, cũng
# không in số lõi mới vì sẽ trái câu trả lời trong khung chat); file vẫn có bảng đội + một dòng nói rõ lý do, và bên gọi nhận lý do để báo người dùng (đối chiếu kỹ thuật).
# Số in ra là số lõi vừa trả. Chỉ gọi lại công cụ ĐỌC có trong GET /api/tools của lõi; công cụ tính (calc_*) không gọi lại được ngoài lượt ⇒ chỉ ghi một dòng chú.
CH_CAU_TOI_DA, CH_CONG_CU_TOI_DA, CH_GOI_LAI_TOI_DA, CH_DONG_TOI_DA, CH_COT_TOI_DA, CH_O_TOI_DA = 500, 20, 6, 60, 8, 300
CH_TEN = re.compile(r"[a-z][a-z0-9_]{0,63}")
CH_SHEET = "Theo câu hỏi"
CH_BO = ("params_used",)   # phần lặp lại tham số — không in
CH_BO_KHI_GON = ("Cách tính số thực hiện",)   # dòng dài — chỉ in ở Excel + Word (PowerPoint / PDF: bản gọn)
CH_DONG_MOT_TRANG = 7   # PowerPoint: bộ dựng tự tách trang khi quá 7 dòng ⇒ tự chia đúng 7 để không có trang "tiếp" chỉ một dòng
KY = re.compile(r"\d{4}-(0[1-9]|1[0-2])"), re.compile(r"\d{4}-\d{2}-\d{2}")
KY_CANH_BAO = "CẢNH BÁO: không xác định được kỳ dữ liệu của bảng này — kiểm lại kỳ trước khi dùng số."


def ky_chu(b: dict) -> tuple[str, str, str | None, str]:
    """(chữ kỳ, chữ ngày dữ liệu, câu cảnh báo | None, phần kỳ cho TÊN FILE). Kỳ / ngày thiếu hoặc sai khuôn ⇒ không in "None": ghi "chưa xác định được" + câu cảnh báo
    (cùng luật với dòng kỳ của khung chat — tro_ly.ky_cua_goi); tên file dùng "ky" (chỉ chữ an toàn)."""
    per, cut = str(b.get("period") or "").strip(), str(b.get("cutoff") or "").strip()
    if KY[0].fullmatch(per) and KY[1].fullmatch(cut):
        return per, cut, None, per
    return "chưa xác định được", "chưa xác định được", KY_CANH_BAO, "ky"


class Tien(int):
    """[bản 2] Số TIỀN (đồng) trong phần theo câu hỏi: Excel ghi Ô SỐ có định dạng '#,##0 "đ"' (người dùng tính tiếp được); Word / PDF / PowerPoint in '1.234.567 đ'."""


DINH_DANG_TIEN = '#,##0 "đ"'


def so(x) -> str:
    """Số ⇒ chữ kiểu Việt: 941438285 ⇒ '941.438.285'; 100.21 ⇒ '100,21'. Không phải số ⇒ chữ đã chuẩn hoá."""
    if isinstance(x, bool):
        return "có" if x else "không"
    if isinstance(x, int):
        return f"{x:,}".replace(",", ".")
    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return str(x)
        nguyen, _, le = f"{x:,.2f}".partition(".")
        return nguyen.replace(",", ".") + ("," + le.rstrip("0") if le.strip("0") else "")
    return chuan(x)


def lay_cau_hoi(du: dict) -> dict | None:
    """Phần "cau_hoi" của gói: None / thiếu ⇒ None (file như trước). Sai khuôn ⇒ NÉM (không lặng lẽ bỏ phần người dùng đang chờ)."""
    ch = du.get("cau_hoi")
    if ch is None:
        return None
    cc = ch.get("cong_cu") if isinstance(ch, dict) else None
    if not isinstance(ch, dict) or not isinstance(ch.get("cau", ""), str) or not isinstance(cc, list) or len(cc) > CH_CONG_CU_TOI_DA:
        raise ValueError("phần nội dung theo câu hỏi gửi sang sai khuôn — không xuất file")
    for x in cc:
        if not (isinstance(x, dict) and isinstance(x.get("ten"), str) and CH_TEN.fullmatch(x["ten"]) and isinstance(x.get("tham_so"), dict)
                and isinstance(x.get("ket_qua"), (dict, list))):
            raise ValueError("phần nội dung theo câu hỏi gửi sang sai khuôn — không xuất file")
    return {"cau": chuan(ch.get("cau") or "")[:CH_CAU_TOI_DA], "cong_cu": cc}


def _loi_kpi():
    import httpx

    goc = os.environ.get("CHAY_THU_KPI_URL", "").rstrip("/")
    if not goc.startswith("http"):
        raise ValueError("hộp chưa cấu hình đường tới lõi KPI")
    return httpx, goc


def ten_cong_cu_doc() -> set:
    """Tên công cụ ĐỌC của lõi KPI (GET /api/tools — một nguồn), bỏ công cụ tính calc_*."""
    httpx, goc = _loi_kpi()
    r = httpx.get(f"{goc}/api/tools", timeout=30, trust_env=False)
    ds = (r.json() if r.status_code == 200 else {}).get("tools")
    if not isinstance(ds, list):
        raise ValueError("không lấy được danh sách công cụ của lõi KPI — không xuất phần theo câu hỏi")
    ten = {str((t.get("function") or t).get("name") or "") for t in ds if isinstance(t, dict)}
    return {t for t in ten if CH_TEN.fullmatch(t) and not t.startswith("calc_")}


def goi_lai(ten: str, tham_so: dict):
    """Gọi lại MỘT công cụ đọc ở lõi KPI (không mã lượt ⇒ không ghi nhật ký lượt). Lỗi ⇒ NÉM (người gọi bỏ lần tra này khỏi file, có lời nói rõ)."""
    httpx, goc = _loi_kpi()
    r = httpx.post(f"{goc}/api/tool/{ten}", json={"args": tham_so}, timeout=30, trust_env=False)
    if r.status_code != 200 or len(r.content) > 400_000:
        raise ValueError(f"lõi KPI không trả lại được kết quả của công cụ {ten}")
    return r.json()


def kiem_cau_hoi(ch: dict | None, goi_lai_ham=None, ten_doc=None) -> tuple[list, list[str]]:
    """Trả ([(tên công cụ, tham số, kết quả LÕI VỪA TRẢ)], [dòng chú]) cho phần đầu của file. Mỗi lần gọi được in phải: tên thuộc công cụ đọc của lõi · gọi lại ra
    ĐÚNG kết quả gửi sang. Khác / không gọi lại được ⇒ KHÔNG in lần đó + một dòng chú nói rõ. Bỏ qua: lời từ chối của lõi ({"refused": …} — không phải nguồn số),
    lần gọi lặp y hệt. Công cụ tính / tên lạ ⇒ một dòng chú."""
    if not ch or not ch["cong_cu"]:
        return [], []
    ten_doc = (ten_doc or ten_cong_cu_doc)()
    ra, chu, da = [], [], set()
    for x in ch["cong_cu"]:
        ten, ts, kq = x["ten"], x["tham_so"], x["ket_qua"]
        khoa = (ten, json.dumps(ts, ensure_ascii=False, sort_keys=True))
        if khoa in da or (isinstance(kq, dict) and "refused" in kq):
            continue
        da.add(khoa)
        if ten.startswith("calc_"):
            chu.append(f"Phép tính {ten}: kết quả xem ở câu trả lời trong khung chat (file chỉ in số đọc thẳng từ lõi KPI).")
            continue
        if ten not in ten_doc:
            chu.append(f"Công cụ {ten}: chưa in được trong file.")
            continue
        if len(ra) >= CH_GOI_LAI_TOI_DA:
            chu.append(f"Lượt này còn lần tra khác chưa in (file in tối đa {CH_GOI_LAI_TOI_DA} lần tra).")
            break
        try:
            moi = (goi_lai_ham or goi_lai)(ten, ts)
        except Exception as e:  # noqa: BLE001 — không gọi lại được ⇒ không in lần này (không đoán)
            chu.append(f"Nội dung câu vừa hỏi (lần tra {ten}) KHÔNG in được: không tra lại được ở lõi KPI ({type(e).__name__}) — thử xuất lại sau.")
            continue
        if moi != kq:
            chu.append(f"Nội dung câu vừa hỏi (lần tra {ten}) KHÔNG in được: số tra lại ở lõi KPI khác lúc trả lời — hỏi lại rồi xuất để có phần này.")
            continue
        ra.append((ten, ts, moi))
    return ra, chu


def _o(v) -> str:
    """Một ô của bảng thô: số ⇒ chữ kiểu Việt; dict / list ⇒ JSON rút gọn; cắt CH_O_TOI_DA ký tự có dấu …"""
    s = so(v) if not isinstance(v, (dict, list)) else chuan(json.dumps(v, ensure_ascii=False))
    return s if len(s) <= CH_O_TOI_DA else s[:CH_O_TOI_DA - 1] + "…"


def khoi_explain_kpi(ts: dict, k: dict) -> dict | None:
    """KHUÔN cho explain_kpi (mẫu 18: chỉ tiêu + mốc thưởng của MỘT KPI của MỘT nhân viên). Kết quả không đúng hình ⇒ None (người gọi rơi về bảng thô)."""
    tiers = k.get("tiers")
    if not (isinstance(k.get("kpi"), str) and isinstance(k.get("employee_id"), str) and isinstance(tiers, list)
            and all(isinstance(t, dict) and isinstance(t.get("amount"), int) and not isinstance(t.get("amount"), bool) for t in tiers)
            and isinstance(k.get("amount"), int) and not isinstance(k.get("amount"), bool)):
        return None
    ai = " · ".join(x for x in (k["employee_id"], chuan(k.get("name") or ""), chuan(k.get("sm_code") or "")) if x)
    dat = next((chuan(str(t.get("label"))) for t in reversed(tiers) if t.get("amount") == k["amount"] and k["amount"] > 0), None)
    dong = [("Nhân viên", ai), ("KPI", chuan(k["kpi"]) + (f" (công văn mục {chuan(k['cong_van_section'])})" if k.get("cong_van_section") else ""))]
    if k.get("target") is not None:
        dong.append(("Chỉ tiêu", k["target"]))
    if k.get("actual") is not None:
        dong.append(("Thực hiện", k["actual"]))
    if k.get("pct") is not None:
        dong.append(("% thực hiện so với chỉ tiêu", f"{so(k['pct'])} %"))
    dong += [("Mốc đã đạt", dat or "chưa đạt mốc nào"), ("Tiền thưởng KPI này", Tien(k["amount"]))]
    if k.get("how_counted"):
        dong.append(("Cách tính số thực hiện", chuan(k["how_counted"])))
    if k.get("status_label"):
        dong.append(("Trạng thái số liệu", chuan(k["status_label"])))
    bang = {"cot": ["Mốc thưởng", "Từ mức (%)", "Tiền thưởng"],
            "dong": [[chuan(str(t.get("label") or "")), t.get("min") if isinstance(t.get("min"), (int, float)) and not isinstance(t.get("min"), bool) else "",
                      Tien(t["amount"])] for t in tiers]}
    return {"tieu_de": f"Chỉ tiêu và mốc thưởng — KPI {chuan(k['kpi'])} — {ai}", "dong": dong, "bang": bang,
            "ghi_chu": [chuan(str(x)) for x in (k.get("data_flags") or []) if isinstance(x, str)][:6]}


def khoi_tho(ten: str, ts: dict, k) -> dict:
    """Công cụ CHƯA có khuôn ⇒ bảng thô có tiêu đề: ô đơn in thành dòng nhãn — giá trị; danh sách các dict ⇒ MỘT bảng (cột = khoá của dòng đầu); phần còn lại ⇒ ô chữ."""
    tieu_de = f"Kết quả tra: {ten}" + (" (" + ", ".join(f"{a} = {_o(b)}" for a, b in list(ts.items())[:4]) + ")" if ts else "")
    dong, bang, ghi = [], None, []
    goc = k if isinstance(k, dict) else {"danh sách": k}
    for a, v in goc.items():
        if a in CH_BO or v is None or v == "" or v == [] or v == {}:
            continue
        if bang is None and isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            cot = [c for c in v[0] if not isinstance(v[0][c], (dict, list))][:CH_COT_TOI_DA]
            if cot:
                bang = {"cot": [chuan(c) for c in cot],   # [bản 2] số giữ là SỐ (Excel: ô số); chữ đã chuẩn hoá / cắt
                        "dong": [[(x[c] if isinstance(x.get(c), (int, float)) and not isinstance(x.get(c), bool) else _o(x[c])) if x.get(c) is not None else "" for c in cot]
                                 for x in v[:CH_DONG_TOI_DA]]}
                if len(v) > CH_DONG_TOI_DA:
                    ghi.append(f"Bảng “{chuan(a)}” có {len(v)} dòng — file in {CH_DONG_TOI_DA} dòng đầu.")
                continue
        if len(dong) < CH_DONG_TOI_DA:
            dong.append((chuan(a), v if isinstance(v, (int, float)) and not isinstance(v, bool) else _o(v)))
    return {"tieu_de": tieu_de, "dong": dong, "bang": bang, "ghi_chu": ghi}


KHUON = {"explain_kpi": khoi_explain_kpi}


def dung_khoi(ds: list) -> list[dict]:
    """[(tên, tham số, kết quả lõi)] ⇒ các khối in được {"tieu_de", "dong": [(nhãn, giá trị)], "bang": {"cot", "dong"} | None, "ghi_chu": [chữ]}."""
    ra = []
    for ten, ts, k in ds:
        kh = KHUON[ten](ts, k) if ten in KHUON and isinstance(k, dict) else None
        ra.append(kh or khoi_tho(ten, ts, k))
    return ra


def _gt(v) -> str:
    if isinstance(v, Tien):
        return vn(int(v))
    return so(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def dong_chu(ch: dict | None, khoi: list[dict], chu: list[str], gon: bool = False) -> list[str]:
    """Phần đầu dạng DÒNG CHỮ cho Word / PDF / PowerPoint ("## " tên mục, "- " gạch đầu dòng). gon=True (PowerPoint, PDF): bỏ phần lưu ý dữ liệu dài.
    Dòng có số tiền luôn NGẮN (số tiền không bị xuống dòng giữa chừng ⇒ bước đọc lại so đủ từng số)."""
    if not khoi and not chu:
        return []
    ra = ["## Theo câu vừa hỏi" + (f": {ch['cau']}" if ch and ch.get("cau") else ""),
          "- Phần này là đúng nội dung câu vừa hỏi, số lấy lại từ lõi KPI lúc xuất file; phía sau là bảng thưởng cả đội."]
    for kh in khoi:
        ra.append(f"## {kh['tieu_de']}")
        ra += [f"- {a}: {_gt(v)}" for a, v in kh["dong"] if not (gon and a in CH_BO_KHI_GON)]
        if kh["bang"]:
            cot = kh["bang"]["cot"]
            ra += ["- " + " · ".join(f"{c}: {_gt(x)}" for c, x in zip(cot, d) if x != "") for d in kh["bang"]["dong"]]
        if not gon:
            ra += [f"- Lưu ý dữ liệu: {g}" for g in kh["ghi_chu"] if not TIEN.search(g)]
    ra += [f"- {c}" for c in chu]
    return ra


def ghi_sheet_cau_hoi(wb, dat, dau, ch: dict | None, khoi: list[dict], chu: list[str]) -> list:
    """Sheet ĐẦU "Theo câu hỏi" của file Excel. Trả danh sách (dòng, cột, giá trị) đã ghi — kiem_sheet_cau_hoi đọc lại file so TỪNG ô."""
    from openpyxl.styles import PatternFill

    nen = PatternFill("solid", fgColor="E6F2F1")
    ws = wb.create_sheet(CH_SHEET, 0)
    da = []

    def o(r, c, v, **kw):
        if v is None or v == "":   # ô rỗng: không ghi, không so (Excel đọc lại ô rỗng là "không có giá trị")
            return
        tien = isinstance(v, Tien)
        v = int(v) if tien else v
        dat(ws, r, c, v, **kw)
        if tien:
            ws.cell(r, c).number_format = DINH_DANG_TIEN
        da.append((r, c, chuan(v) if isinstance(v, str) else v))
    r = dau(ws, "Nội dung theo câu vừa hỏi (số lấy lại từ lõi KPI lúc xuất file)")
    if ch and ch.get("cau"):
        o(r, 1, "Câu hỏi", dam=True); o(r, 2, ch["cau"]); r += 2
    for kh in khoi:
        o(r, 1, kh["tieu_de"], dam=True); r += 1
        for a, v in kh["dong"]:
            o(r, 1, a); o(r, 2, v, so=isinstance(v, int) and not isinstance(v, bool)); r += 1
        if kh["bang"]:
            r += 1
            for c, h in enumerate(kh["bang"]["cot"], 1):
                o(r, c, h, dam=True, fill=nen)
            for d in kh["bang"]["dong"]:
                r += 1
                for c, x in enumerate(d, 1):
                    o(r, c, x, so=isinstance(x, int) and not isinstance(x, (bool, Tien)))
            r += 1
        for g in kh["ghi_chu"]:
            o(r, 1, "Lưu ý dữ liệu"); o(r, 2, g); r += 1
        r += 1
    for c in chu:
        o(r, 1, c); r += 1
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width, ws.column_dimensions["C"].width = 44, 40, 22
    return da


def kiem_sheet_cau_hoi(raw: bytes, da: list) -> int:
    """Đọc lại file Excel: MỌI ô đã ghi ở sheet "Theo câu hỏi" phải đúng giá trị (số là số, chữ là chữ, không công thức). Lệch ⇒ NÉM."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw))
    if wb.sheetnames[0] != CH_SHEET:
        raise ValueError("file Excel thiếu sheet nội dung theo câu hỏi — không trả file")
    ws = wb[CH_SHEET]
    for r, c, v in da:
        x = ws.cell(r, c)
        if x.value != v or x.data_type == "f":
            raise ValueError("file Excel: nội dung theo câu hỏi đọc lại khác số của lõi KPI — không trả file")
    return len(da)


def kiem_chu_cau_hoi(dang: str, raw: bytes, dong: list[str]) -> int:
    """Word / PDF / PowerPoint: MỌI giá trị số của phần đầu (chuỗi có chữ số trong các dòng đã ghi) phải có trong chữ đọc lại từ file. Thiếu ⇒ NÉM."""
    chu = re.sub(r"\s+", " ", unicodedata.normalize("NFC", doc_chu(dang, raw)))
    can = [x for d in dong for x in re.findall(r"\d[\d.,]*\d|\d", unicodedata.normalize("NFC", d))]
    thieu = [x for x in can if x not in chu]
    if thieu:
        raise ValueError(f"file {dang}: phần theo câu hỏi thiếu {len(thieu)} số so với lõi KPI — không trả file")
    return len(can)


# ---------------- Excel: ĐÚNG bố cục export_report.to_xlsx (3 sheet, 1 hoặc 3 cột người) ⇒ _kpi_kiem_o kiểm được ----------------
def dung_xlsx(b: dict, names, sha: str, ch=None, khoi=(), chu=(), tu_duyet: int | None = None) -> tuple[bytes, list]:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    nen, tong = PatternFill("solid", fgColor="E6F2F1"), PatternFill("solid", fgColor="F6F7F9")
    tien = {(d["employee_id"], d["kpi"]): d for d in b["lines"]}

    def dat(ws, r, c, v, dam=False, so=False, fill=None):
        if isinstance(v, str):
            v = chuan(v)
        o = ws.cell(r, c, v)
        if isinstance(v, str) and v[:1] in "=+-@\t\r":   # chống công thức như lõi: giữ là CHỮ
            o.data_type, o.quotePrefix = "s", True
        o.font = Font(name="Arial", size=11, bold=dam)
        if so:
            o.number_format = "#,##0"
        if fill:
            o.fill = fill

    def dau(ws, phu):
        # tu_duyet = số giây đã chờ ⇒ dòng ĐẦU mỗi sheet mở bằng nhãn tự duyệt (3 dòng đầu nằm ngoài vùng _kpi_kiem_o so)
        dat(ws, 1, 1, NHAP if tu_duyet is None else f"{TU_DUYET} (hệ thống tự cho đi tiếp sau {int(tu_duyet)} giây không ai bấm) · {NHAP}", dam=True)
        per, cut, cb, _ = ky_chu(b)   # [va_xuat_theo_cau] kỳ thiếu / sai khuôn ⇒ không in "None": câu cảnh báo ở dòng 3 (3 dòng đầu nằm ngoài vùng _kpi_kiem_o so)
        dat(ws, 2, 1, f"{phu} · Kỳ {per} · dữ liệu tới {cut} · mã bảng {sha[:12]}")
        dat(ws, 3, 1, NGUON if not cb else f"{cb} {NGUON}")
        return 4

    wb = Workbook()
    t = wb.active
    t.title = "Tổng đội"
    r0, ng = dau(t, "Tiền thưởng từng KPI và lương kinh doanh (đồng)"), dau_nguoi(names)
    for c, h in enumerate([*ng, *b["kpis"], "Lương kinh doanh"], 1):
        dat(t, r0, c, h, dam=True, fill=nen)
    p = len(ng)
    for i, nv in enumerate(b["employees"], r0 + 1):
        for c, x in enumerate(nguoi(b, names, nv), 1):
            dat(t, i, c, x)
        for c, k in enumerate(b["kpis"], p + 1):
            dat(t, i, c, tien[(nv, k)]["amount"], so=True)
        dat(t, i, p + len(b["kpis"]) + 1, sum(tien[(nv, k)]["amount"] for k in b["kpis"]), dam=True, so=True)
    cuoi = r0 + len(b["employees"]) + 1
    dat(t, cuoi, 1, "Cả đội", dam=True, fill=tong)
    for c, k in enumerate(b["kpis"], p + 1):
        dat(t, cuoi, c, sum(tien[(n, k)]["amount"] for n in b["employees"]), dam=True, so=True, fill=tong)
    dat(t, cuoi, p + len(b["kpis"]) + 1, b["team_total"], dam=True, so=True, fill=tong)
    ct = wb.create_sheet("Chi tiết")
    r0 = dau(ct, "Từng nhân viên × KPI")
    for c, h in enumerate([*ng, "KPI", "% thực hiện", "Mốc đạt", "Tiền (đồng)", "Lý do"], 1):
        dat(ct, r0, c, h, dam=True, fill=nen)
    r = r0 + 1
    for nv in b["employees"]:
        for k in b["kpis"]:
            d = tien[(nv, k)]
            for c, x in enumerate(nguoi(b, names, nv), 1):
                dat(ct, r, c, x)
            dat(ct, r, p + 1, k)
            dat(ct, r, p + 2, d.get("pct"))
            dat(ct, r, p + 3, d.get("reached_tier") or "chưa đạt mốc")
            dat(ct, r, p + 4, d["amount"], so=True)
            ct.cell(r, p + 5, d.get("reason"))   # lý do: ghi NGUYÊN chữ của bảng (_kpi_kiem_o so đúng chữ)
            r += 1
    ng_s = wb.create_sheet("Nguồn")
    r = dau(ng_s, "Số trong file lấy từ đâu")
    for x in b.get("source") or []:
        if isinstance(x, (list, tuple)) and len(x) == 2:
            dat(ng_s, r, 1, str(x[0]), dam=True)
            dat(ng_s, r, 2, x[1] if isinstance(x[1], (int, float)) and not isinstance(x[1], bool) else str(x[1]))
            r += 1
    dat(ng_s, r + 1, 1, "Ghi chú", dam=True)
    dat(ng_s, r + 1, 2, "Bảng TẠM TÍNH, CHƯA DUYỆT — bảng chính thức chỉ xuất sau bước Người duyệt." if tu_duyet is None else
        f"{TU_DUYET}: không ai bấm duyệt trong {int(tu_duyet)} giây nên hệ thống tự cho lượt đi tiếp. Đây là bảng TẠM TÍNH; bảng chính thức chỉ xuất sau khi một người bấm duyệt.")
    # [bản 2 20/11/2026 — người dùng: "bạn chỉ việc xuất file excel cho người dùng, người dùng có thể xem và sửa thoải mái (chỉ sửa khi đã tải về), tại sao bạn lại đi khoá vậy"]
    # KHÔNG khoá sheet nào (trước: ws.protection.sheet = True "như lõi" ⇒ Excel báo "protected sheet"). File vẫn mang chữ NHÁP + dòng kỳ ở mỗi sheet.
    da = ghi_sheet_cau_hoi(wb, dat, dau, ch, list(khoi), list(chu)) if (khoi or chu) else []   # [va_xuat_theo_cau] sheet ĐẦU = nội dung câu vừa hỏi
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), da


def kiem_xlsx(raw: bytes, b: dict, names) -> int:
    """Phần BẢNG ĐỘI so bằng CHÍNH _kpi_kiem_o của khối Người duyệt. Hàm đó đòi đúng danh sách 3 sheet ⇒ [va_xuat_theo_cau] file có sheet đầu "Theo câu hỏi" thì
    mở CHÍNH file sẽ trả, gỡ riêng sheet đó, lưu ra bộ nhớ rồi đưa bản ấy cho _kpi_kiem_o (các ô bảng đội được so là ô đọc từ file thật; sheet đầu có phép so riêng
    — kiem_sheet_cau_hoi)."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw))
    if CH_SHEET in wb.sheetnames:
        del wb[CH_SHEET]
        buf = io.BytesIO()
        wb.save(buf)
        raw = buf.getvalue()
    src = open(NODE, encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_kpi_kiem_o")
    ns: dict = {}
    exec(compile(ast.Module([fn], []), NODE, "exec"), ns)
    return ns["_kpi_kiem_o"](raw, b, names)


def dung_nhap_tu_duyet(goi: dict, cho_giay: int) -> tuple[str, bytes, int]:
    """[ghi chú kỹ thuật] File Excel NHÁP cho lượt KPI TỰ DUYỆT khi quá hạn — khối Người duyệt gọi TRONG hộp chạy khối (không qua stdin).
    Trả (tên file, byte, số ô đã so). Cùng đường với file nháp của khung chat: kiem_goi (dấu đạt + mã băm bảng) ⇒ lấy tên từ lõi cùng mã băm ⇒ dựng ⇒ đọc lại file, so TỪNG ô
    bằng _kpi_kiem_o ⇒ đọc lại lần nữa: dòng đầu của CẢ BA sheet phải mở bằng nhãn tự duyệt. Mọi lỗi ⇒ ValueError (khối lỗi, lượt dừng, không file dở)."""
    from openpyxl import load_workbook

    if isinstance(cho_giay, bool) or not isinstance(cho_giay, int) or not 30 <= cho_giay <= 3600:
        raise ValueError("tự duyệt khi quá hạn: số giây chờ phải là số nguyên 30–3600")
    b = kiem_goi(goi)
    sha = goi["sha256_bang"]
    names = lay_ten(sha)
    raw, _ = dung_xlsx(b, names, sha, tu_duyet=cho_giay)
    n = kiem_xlsx(raw, b, names)
    wb = load_workbook(io.BytesIO(raw))
    if wb.sheetnames != ["Tổng đội", "Chi tiết", "Nguồn"] or not all(str(ws.cell(1, 1).value or "").startswith(TU_DUYET) for ws in wb):
        raise ValueError("file nháp tự duyệt thiếu nhãn ở dòng đầu một sheet")
    if any(ws.protection.sheet for ws in wb):
        raise ValueError("file nháp tự duyệt có sheet bị khoá")
    if len(raw) > MAX_RA:
        raise ValueError("file quá lớn")
    return f"{TU_DUYET_TEP}_{NHAP_TEP}_{ky_chu(b)[3]}_{sha[:8]}.xlsx", raw, n


# ---------------- Word / PDF / PowerPoint: dùng lại xuat_file.make_* (đang chạy trong hộp, AI không chạm) ----------------
def nap_xuat_file(path: str):
    spec = importlib.util.spec_from_file_location("xuat_file_b", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    if hasattr(m, "NHAN_NGUON"):
        m.NHAN_NGUON = NGUON
    return m


def noi_dung(b: dict, names, sha: str, dau_cau=(), tu_duyet=None):
    """BẢN TÓM TẮT cho Word / PDF / PPT: đoạn (kỳ, tổng đội, tiền từng KPI cả đội) + bảng [người…, Lương kinh doanh] — MỌI số tiền dạng 'x đ'.
    Ma trận đủ NV × KPI nằm ở file Excel (bảng 12 cột trong PDF làm số xuống dòng giữa chừng — thử 18/11 ⇒ đọc lại lệch 88 số).
    Trả (doan, cot, dong, kỳ vọng tiền: Counter)."""
    tien = {(d["employee_id"], d["kpi"]): d["amount"] for d in b["lines"]}
    per, cut, cb, _ = ky_chu(b)
    doan = [*dau_cau, f"## {_tua(tu_duyet)}", *([f"- {_cau_tu_duyet(tu_duyet)}"] if tu_duyet is not None else []), f"- Kỳ: {per} · dữ liệu tới ngày {cut}", *([f"- {cb}"] if cb else []),
            f"- Tổng lương kinh doanh cả đội (8 KPI): {vn(b['team_total'])}", f"- Mã bảng: {sha[:12]}",
            "- Bảng tạm tính, CHƯA DUYỆT — bảng chính thức chỉ xuất sau bước Người duyệt; ma trận đủ nhân viên × KPI ở file Excel.",
            f"- Nguồn: {NGUON}.", "## Tiền từng KPI — cả đội",
            *[f"- {chuan(k)}: {vn(sum(tien[(n, k)] for n in b['employees']))}" for k in b["kpis"]], "## Lương kinh doanh theo nhân viên"]
    cot = [*dau_nguoi(names), "Lương kinh doanh"]
    dong = [[*nguoi(b, names, nv), vn(sum(tien[(nv, k)] for k in b["kpis"]))] for nv in b["employees"]]
    dong.append(["Cả đội", *([""] * (len(cot) - 2)), vn(b["team_total"])])
    mong = collections.Counter(TIEN.findall("\n".join(doan + [c for d in dong for c in d])))
    return doan, cot, dong, mong


def doc_chu(dang: str, raw: bytes) -> str:
    if dang == "docx":
        import docx

        d = docx.Document(io.BytesIO(raw))
        phan = [p.text for p in d.paragraphs] + [c.text for t in d.tables for row in t.rows for c in row.cells]
        phan += [p.text for s in d.sections for p in s.header.paragraphs + s.footer.paragraphs]
        return "\n".join(phan)
    if dang == "pdf":
        import fitz

        return "\n".join(pg.get_text() for pg in fitz.open(stream=raw, filetype="pdf"))
    if dang == "pptx":
        from pptx import Presentation

        out = []
        for sl in Presentation(io.BytesIO(raw)).slides:
            for sh in sl.shapes:
                if sh.has_text_frame:
                    out.append(sh.text_frame.text)
                if getattr(sh, "has_table", False) and sh.has_table:
                    out += [c.text for row in sh.table.rows for c in row.cells]
        return "\n".join(out)
    raise ValueError("dạng lạ")


def so_tung_so(dang: str, raw: bytes, mong: collections.Counter, names, tu_duyet=None) -> int:
    chu = doc_chu(dang, raw)
    thay = collections.Counter(TIEN.findall(chu))
    if thay != mong:
        thieu, la = mong - thay, thay - mong
        raise ValueError(f"file {dang} khác bảng nguồn: thiếu {sum(thieu.values())} số, thừa {sum(la.values())} số — không trả file")
    if chu.count("NHÁP") < 1:
        raise ValueError(f"file {dang} thiếu chữ NHÁP")
    if tu_duyet is not None and TU_DUYET not in " ".join(unicodedata.normalize("NFC", chu).split()):   # đọc LẠI file: nhãn tự duyệt phải có thật (PDF có thể ngắt dòng giữa nhãn ⇒ gộp khoảng trắng)
        raise ValueError(f"file {dang} thiếu nhãn tự duyệt khi quá hạn — không trả file")
    for e in (names or {}).values():   # tên thật phải hiện đúng chữ đã chuẩn hoá
        if e.get("name") and chuan(e["name"]) not in unicodedata.normalize("NFC", chu):
            raise ValueError(f"file {dang} thiếu tên một nhân viên")
    return sum(mong.values())


async def dung_van_ban(dang: str, b: dict, names, sha: str, xf, dau_cau=(), tu_duyet=None):
    doan, cot, dong, mong = noi_dung(b, names, sha, dau_cau, tu_duyet)
    ten = ("" if tu_duyet is None else TU_DUYET_TEP + "_") + f"{NHAP_TEP}_{ky_chu(b)[3]}_{sha[:8]}"
    tua, phu = _tua(tu_duyet), ("Tạm tính — chưa duyệt" if tu_duyet is None else "Tự duyệt khi quá hạn — chưa có người duyệt")
    if dang == "docx":
        r = await xf.make_docx(ten, tua, doan, cot, dong, phu_de=phu)
    elif dang == "pdf":
        r = await xf.make_pdf(ten, tua, doan, cot, dong, phu_de=phu)
    else:   # pptx: 1 trang tóm tắt + các trang "Mã · tên — lương", ≤ 8 dòng / trang
        muc = [" · ".join(x for x in d[:-1] if x) + " — " + d[-1] for d in dong]
        than = doan[len(dau_cau):]   # [va_xuat_theo_cau] phần bảng đội như cũ; phần theo câu hỏi đứng TRƯỚC, mỗi mục "## " một nhóm trang, ≤ 8 dòng / trang
        tom = [x.lstrip("#- ").strip() for x in than[1:] if not x.startswith("## ")]
        slides, nhom = [], None
        for x in dau_cau:
            if x.startswith("## "):
                nhom = ["NHÁP · " + x[3:][:110]]; slides.append(nhom)
            elif nhom is not None:
                if len(nhom) > CH_DONG_MOT_TRANG:
                    nhom = [nhom[0]]; slides.append(nhom)
                nhom.append(x.lstrip("- ").strip())
        slides = [s for s in slides if len(s) > 1 or s[0].startswith("NHÁP · Theo câu vừa hỏi")]
        if tu_duyet is not None:   # trang riêng mang nhãn, đứng TRƯỚC phần bảng đội
            slides.append([f"NHÁP · {TU_DUYET}", _cau_tu_duyet(tu_duyet)])
            tom = [x for x in tom if not x.startswith(TU_DUYET)]
        slides += [["NHÁP · Tóm tắt", *tom[:8]], *([["NHÁP · Tiền từng KPI", *tom[8:]]] if tom[8:] else [])]
        slides += [[f"NHÁP · Lương kinh doanh ({i // 8 + 1})", *muc[i:i + 8]] for i in range(0, len(muc), 8)]
        mong = collections.Counter(TIEN.findall("\n".join(x for s in slides for x in s)))
        r = await xf.make_pptx(ten, tua, slides, phu_de=phu)
    if not r.get("ok"):
        raise ValueError(f"không dựng được file {dang}: {str(r.get('error'))[:200]}")
    # canh_bao của bộ dựng (vd "tách thành 2 trang") KHÔNG chặn: bước so TỪNG số + tên sau đó bắt mọi chữ / số bị đổi hoặc cắt
    return r["file"], mong


def main() -> dict:
    du = globals().get("DU_LIEU") or {}
    g, dang = du.get("goi") or {}, du.get("dang")
    if dang not in ("xlsx", "docx", "pdf", "pptx"):
        return {"ok": False, "loi": "chỉ xuất được Excel, Word, PDF hoặc PowerPoint"}
    b = kiem_goi(g)
    sha = g["sha256_bang"]
    td = nhan_tu_duyet(g)                     # [bản 2] lượt tự duyệt khi quá hạn ⇒ MỌI dạng file xuất từ khung chat mang nhãn + tên file có nhãn
    tien_to = "" if td is None else TU_DUYET_TEP + "_"
    names = lay_ten(sha)
    ch = lay_cau_hoi(du)                      # [va_xuat_theo_cau] phần đầu theo câu hỏi: gọi lại lõi KPI + so (khác ⇒ ném, không file)
    ds, chu = kiem_cau_hoi(ch)
    khoi = dung_khoi(ds)
    ky_tep = ky_chu(b)[3]
    tam = tempfile.mkdtemp(prefix="dis_b_xkq_")
    try:
        if dang == "xlsx":
            raw, da = dung_xlsx(b, names, sha, ch, khoi, chu, tu_duyet=td)
            n = kiem_xlsx(raw, b, names)
            n_cau = kiem_sheet_cau_hoi(raw, da) if da else 0
            if td is not None:   # đọc lại: dòng đầu MỌI sheet (kể cả sheet "Theo câu hỏi") phải mở bằng nhãn
                from openpyxl import load_workbook
                if not all(str(ws.cell(1, 1).value or "").startswith(TU_DUYET) for ws in load_workbook(io.BytesIO(raw))):
                    raise ValueError("file xlsx thiếu nhãn tự duyệt khi quá hạn ở dòng đầu một sheet — không trả file")
            ten = f"{tien_to}{NHAP_TEP}_{ky_tep}_{sha[:8]}.xlsx"
        else:
            os.environ["NATIVE_TOOLS_EXPORT_DIR"] = tam
            xf = nap_xuat_file(du.get("xf") or XF)
            xf.EXPORT_DIR = __import__("pathlib").Path(tam)
            dau_cau = dong_chu(ch, khoi, chu, gon=dang in ("pptx", "pdf"))
            tep, mong = asyncio.run(dung_van_ban(dang, b, names, sha, xf, dau_cau, td))
            raw = open(os.path.join(tam, tep), "rb").read()
            ten = f"{tien_to}{NHAP_TEP}_{ky_tep}_{sha[:8]}.{dang}"   # tên do code đặt (bộ dựng thêm giờ + mã ngẫu nhiên ở đầu)
            n = so_tung_so(dang, raw, mong, names, td)
            n_cau = kiem_chu_cau_hoi(dang, raw, dau_cau) if dau_cau else 0
        if len(raw) > MAX_RA:
            return {"ok": False, "loi": "file quá lớn"}
        return {"ok": True, "ten": ten, "b64": base64.b64encode(raw).decode(), "sha16": hashlib.sha256(raw).hexdigest()[:16], "so_da_so": n,
                "tu_duyet_qua_han": td is not None, "co_ten": names is not None, "theo_cau": len(khoi), "so_cau_da_so": n_cau, "chu_cau": chu, "cong_cu_da_in": [x[0] for x in ds]}
    finally:
        shutil.rmtree(tam, ignore_errors=True)


if __name__ == "__main__":
    try:
        kq = main()
    except ValueError as e:
        kq = {"ok": False, "loi": str(e)[:300]}
    except Exception as e:  # noqa: BLE001 — lỗi lạ: không lộ chi tiết, không file dở
        kq = {"ok": False, "loi": f"lỗi khi dựng file ({type(e).__name__})"}
    sys.stdout.write("\n" + json.dumps(kq, ensure_ascii=False) + "\n")
