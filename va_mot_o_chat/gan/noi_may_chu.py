"""Nối gan/tro_ly.py vào may_chu (đợt 1) — C 18/11/2026. BẢN SAO, CHƯA GẮN. Dựng `dep` thật từ hàm SẴN CÓ của may_chu (không viết lại đường chạy):
  pb = may_chu.pb_moi_nhat · toi_da = muc_toi_da.toi_da · chay = may_chu.chay (nút Chạy: luật lúc Chạy + giữ chỗ + trần + sổ) · ket_qua = ket_qua_luot
  trang_thai = status workflow_run + SỰ KIỆN awaiting_intervention (lúc chờ duyệt status vẫn RUNNING — bài học 16/11) · operator = chat_operator.gui
  goi_jev = OpenRouter Decisions qua SỔ (giữ chỗ byte × giá × 2 TRƯỚC, ghi usage.cost làm tròn LÊN, khoá B CG-7), chờ CHO_JEV giây
Khoá OpenRouter: biến OPENROUTER*KEY trong môi trường may_chu (A bật may_chu kèm `set -a; . chay_thu/env/openrouter.khoa.env`) — không in.
Gắn = thêm route POST /api/tro-ly vào may_chu bằng gan_dot1.sh (sha gốc + lùi), đường cũ /api/chat /api/hoi-nhanh GIỮ.
[ghi chú] thẻ KPI đầy đủ: bang = hoi_nhanh._bang (cùng hình "view" /api/hoi-nhanh) · xuất kết quả vừa trả lời = va_xuat_ket_qua.xuat nhóm kỹ thuật
(0 AI; máy chủ tự lấy phiên trả lời cuối của conv) · GET /api/tro-ly/tep?conv=&id= ⇒ tai_tep (chỉ file đã xuất trong ĐÚNG cuộc chat, 30 phút).
"""
from __future__ import annotations

import json
import math
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

G = Path(__file__).resolve().parent
sys.path[:0] = [str(G), str(G.parent)]
import chon as C  # noqa: E402
import chon_jev as J  # noqa: E402
import tro_ly as TL  # noqa: E402

GIA_JEV_VAO = 0.042 / 1e6      # USD / token vào — ĐẶT LẠI theo bộ định tuyến lúc dung_dep (pplx 0,02 · jev 0,042, đo 18/11: usage.cost đúng giá bảng); giữ chỗ ×2
_TRANG = {"dep": None}


def _khoa_or() -> str:
    ten = [k for k in os.environ if k.startswith("OPENROUTER") and k.endswith("KEY")]
    if len(ten) != 1 or not os.environ[ten[0]].startswith("sk-or-"):
        raise RuntimeError("chưa có khoá OpenRouter trong môi trường máy chủ")
    return os.environ[ten[0]]


def goi_jev_qua_so(S5, body: dict, so_byte: int, cho: float, ghi=lambda s: None, dem=None) -> dict:
    """Giữ chỗ → 1 POST (timeout cho) → ghi usage.cost. Quá giờ / lỗi mạng / HTTP ≠ 200 ⇒ GHI LUÔN dòng sổ = mức đã giữ (cận trên — E n1: không để
    chỗ giữ treo tới khi tắt máy chủ) rồi ném (TimeoutError / RuntimeError), không gọi lại. cost > phần giữ ⇒ đếm jev_vuot_giu + ghi nhật ký (E n2)."""
    dem = {} if dem is None else dem
    muc = round(so_byte * GIA_JEV_VAO * 2, 8)
    ma_giu, tong = S5.giu_cho(muc, "tro_ly chon jev", cho_giay=5.0)
    if not ma_giu:
        raise RuntimeError("sổ tiền: hết trần hoặc đang bận")
    rq = urllib.request.Request(J.URL, data=json.dumps(body).encode(), method="POST",
                                headers={"Authorization": f"Bearer {_khoa_or()}", "Content-Type": "application/json"})
    def dong(ly):   # không biết đã tiêu bao nhiêu ⇒ ghi MỨC đã giữ (cận trên) + trả chỗ trong cùng khoá sổ
        S5.ghi_tien("tro_ly_jev", muc, {"ghi_chu": f"một khung chat — bước chọn Jev {ly}, ghi mức đã giữ (cận trên)", "usd_ncc": None}, ma_giu=ma_giu)
    try:
        with urllib.request.urlopen(rq, timeout=cho) as r:
            code, d = r.status, json.loads(r.read())
    except (socket.timeout, TimeoutError) as e:
        dong("quá giờ"); raise TimeoutError("jev quá giờ") from e
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            dong("quá giờ"); raise TimeoutError("jev quá giờ") from e
        dong(f"lỗi {type(e).__name__}"); raise RuntimeError(f"jev lỗi mạng {type(e).__name__}") from e
    except Exception as e:  # noqa: BLE001
        dong(f"lỗi {type(e).__name__}"); raise RuntimeError(f"jev lỗi {type(e).__name__}") from e
    if code != 200 or not isinstance(d, dict):
        dong(f"HTTP {code}"); raise RuntimeError(f"jev HTTP {code}")
    u = d.get("usage") or {}; cost = u.get("cost")
    so = lambda x: x if isinstance(x, (int, float)) and not isinstance(x, bool) else None   # noqa: E731
    usd = math.ceil((float(cost) if so(cost) is not None and cost >= 0 else muc) * 1e6) / 1e6
    if usd > muc:
        dem["jev_vuot_giu"] = dem.get("jev_vuot_giu", 0) + 1; ghi(f"TRO-LY Jev usage.cost {usd} > phần giữ {muc} (giá thật cao hơn cận trên)")
    S5.ghi_tien("tro_ly_jev", usd, {"ghi_chu": f"một khung chat — bước chọn {J.MO_HINH}, {u.get('input_tokens')} vào",
                                    "vao": so(u.get("input_tokens")), "ra": so(u.get("output_tokens")), "gia_vao": round(GIA_JEV_VAO * 1e6, 6), "gia_ra": 0,
                                    "usd_ncc": so(cost)}, ma_giu=ma_giu)
    return d


def dung_cache(CTL, KHO_CTL, cache_lay, van_tay_kpi, org):
    """[A — bài 46] cache câu trả lời DÙNG CHUNG của trang (cache_tra_loi nhóm kỹ thuật): khoá = câu chuẩn hoá + lịch sử trước + vân tay hộp KPI + workflow +
    số phiên bản + tổ chức; hộp KPI không đọc được vân tay ⇒ không cache (chạy như thường)."""
    def khoa(cau, uuid, so, ls):
        vt = van_tay_kpi()
        if not vt:
            return None
        return CTL.khoa(loi_vao="tro-ly", cau=str(cau or ""), truoc=[m.get("noi_dung", "") for m in (ls or [])] or None,   # [ghi chú] băm ĐỦ nội dung lịch sử (trước: 300 ký tự đầu)
                        data_version=((vt or {}).get("dong") or {}).get("latest_data_version"), van_tay=vt, workflow_id=uuid, so_phien_ban=so, to_chuc=org())

    def lay(cau, uuid, so, ls):
        k = khoa(cau, uuid, so, ls)
        m = cache_lay(k) if k else None
        return (m or {}).get("session")

    def ghi_c(cau, uuid, so, ls, phien):
        k = khoa(cau, uuid, so, ls)
        if k:
            KHO_CTL.ghi(k, phien)
    return {"lay": lay, "ghi": ghi_c}


def _xuat_b(ket_qua_luot, kiem_ma, xem=None, ghi=None):
    """Hàm xuất nhóm kỹ thuật (va_xuat_ket_qua.xuat) — nạp muộn; không có ⇒ None (thẻ xuất không gửi định tuyến).
    [ghi chú] trả hàm (phien, dang, cau=None) ⇒ (tên, byte, thông tin): chuyển câu vừa hỏi + kết quả công cụ của CHÍNH lượt (cong_cu_luot.doc —
    đọc đủ từ Postgres theo mã phiên do máy chủ tự lấy) cho bộ dựng. thông tin = {"phan_cau_hoi": bool, "ly_do", "cong_cu_da_in"} để câu báo nói đúng file có gì.
    Bộ dựng bản cũ (chưa có xuat_chi_tiet) ⇒ gọi xuat như trước, thông tin = {"phan_cau_hoi": False, "ly_do": None}."""
    try:
        if str(G.parent.parent) not in sys.path:
            sys.path.insert(0, str(G.parent.parent))
        from va_xuat_ket_qua import xuat as X
    except Exception:  # noqa: BLE001
        return None
    try:
        import cong_cu_luot as CCL
    except Exception:  # noqa: BLE001 — thiếu module ⇒ xuất như cũ (chỉ bảng đội)
        CCL = None

    def xuat(phien, dang, cau=None):
        goi = X.lay_goi(ket_qua_luot(kiem_ma(phien)))
        if not hasattr(X, "xuat_chi_tiet") or CCL is None or xem is None:
            ten, raw = X.xuat(goi, dang)
            return ten, raw, {"phan_cau_hoi": False, "ly_do": None, "cong_cu_da_in": []}
        cong_cu, ly_do = CCL.doc(kiem_ma(phien), xem.sql)
        if ghi:
            ghi(f"TRO-LY xuất {dang}: {len(cong_cu)} lần gọi công cụ của lượt đưa vào gói dựng" + (f" (rỗng: {ly_do})" if ly_do else ""))
        ten, raw, tt = X.xuat_chi_tiet(goi, dang, cau_hoi={"cau": " ".join(str(cau or "").split())[:500], "cong_cu": cong_cu})
        tt = dict(tt) if isinstance(tt, dict) else {}
        if ly_do:   # lý do phía chat (đọc lỗi / cắt còn 20 lần / quá cỡ) luôn tới câu báo, nối sau lý do của bộ dựng
            tt["ly_do"] = " ".join(x for x in (str(tt.get("ly_do") or "").strip(), ly_do) if x)
        return ten, raw, {"phan_cau_hoi": tt.get("phan_cau_hoi") is True, "ly_do": tt.get("ly_do"), "cong_cu_da_in": list(tt.get("cong_cu_da_in") or [])}
    return xuat


def _ly_do_hong(xem, kiem_ma, phien):
    """Lý do lượt hỏng ĐÃ được trang làm rõ (xem_workflow.mot_luot ⇒ loi_de_hieu.ap_dung đặt luot.loi_capitol khi làm rõ được). Không làm rõ ⇒ None."""
    l = (xem.mot_luot(kiem_ma(phien), None) or {}).get("luot") or {}
    return l.get("loi") if l.get("loi_capitol") is not None and isinstance(l.get("loi"), str) else None


def _viet_lai_cau_rag(S5, cau: str, lich_su: list | None = None, ghi=None) -> str | None:
    """Viết lại câu hỏi RAG nối tiếp thành câu độc lập bằng gpt-6-luna qua sổ tiền S5 (AGENTS 3.9).
    Ưu tiên OPENAI_REGIONAL_BASE_URL / OPENAI_API_KEY, kế đến OPENROUTER_API_KEY (model openai/gpt-6-luna, sẵn có trong môi trường may_chu);
    chưa có đường gọi hợp lệ hoặc quá trần ⇒ trả None để rơi về nối câu an toàn (0 đồng)."""
    ls = [m for m in (lich_su or []) if isinstance(m, dict) and str(m.get("noi_dung") or "").strip()][-4:]
    if not ls or not S5:
        return None
    base_reg = (os.environ.get("OPENAI_REGIONAL_BASE_URL") or "").strip().rstrip("/")
    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if base_reg:
        url, key, model = f"{base_reg}/v1/chat/completions", (openai_key or "noi-bo"), "gpt-6-luna"
    elif openai_key:
        url, key, model = "https://api.openai.com/v1/chat/completions", openai_key, "gpt-6-luna"
    else:
        try:
            url, key, model = "https://openrouter.ai/api/v1/chat/completions", _khoa_or(), "openai/gpt-6-luna"
        except RuntimeError:
            return None
    muc = 0.002
    ma_giu, _ = S5.giu_cho(muc, "tro_ly_viet_lai_rag")
    if not ma_giu:
        return None
    ngan = "\n".join(("Người dùng: " if m.get("vai") == "nguoi_dung" else "Trợ lý: ") + str(m["noi_dung"])[:300] for m in ls)
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "Chỉ trả về đúng 1 câu truy vấn tìm kiếm tài liệu độc lập (ngắn gọn, tiếng Việt, giữ đủ tên riêng/mục/số từ ngữ cảnh), không giải thích."},
            {"role": "user", "content": f"Ngữ cảnh:\n{ngan}\n\nCâu hỏi hiện tại: {cau}"},
        ],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    usd = 0.0
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        u = d.get("usage") or {}
        vao, ra = int(u.get("prompt_tokens") or u.get("input_tokens") or 0), int(u.get("completion_tokens") or u.get("output_tokens") or 0)
        usd = round((vao * 0.10 + ra * 0.50) / 1_000_000, 6)
        S5.ghi_tien("tro_ly_viet_lai_rag", usd, {"ghi_chu": "viết lại câu RAG gpt-6-luna", "vao": vao, "ra": ra}, ma_giu=ma_giu)
        chu = ((((d.get("choices") or [{}])[0]).get("message") or {}).get("content") or "").strip()
        return " ".join(chu.split())[:500] or None
    except Exception as e:  # noqa: BLE001
        try:
            S5.ghi_tien("tro_ly_viet_lai_rag", usd, {"ghi_chu": f"lỗi viết lại câu RAG ({type(e).__name__})"}, ma_giu=ma_giu)
        except Exception:  # noqa: BLE001
            pass
        if ghi:
            ghi(f"TRO-LY viết lại câu RAG lỗi ({type(e).__name__}) — dùng nối câu thường")
        return None


THE_CHINH_PA1 = frozenset({"kpi_hoi_nhanh", "kpi_bang_excel"})


def dung_dep(*, xem, kiem_ma, pb_moi_nhat, toi_da, chay, ket_qua_luot, operator, S5, ghi, cache=None, ds_nv=None,
             nap_tai_lieu=None, viec_tai_lieu=None, tim_tai_lieu=None, ds_kho_tai_lieu=None, dua_file=None, ky_trong=None, store=None, **_bo) -> dict:   # [ghi chú] ba hàm đường nạp kho + kiểm tra kỳ trống + kho SQLite
    """Gọi MỘT lần lúc may_chu bật (thẻ + ghim đọc lúc đó; đổi thẻ / ghim ⇒ bật lại may_chu)."""
    global GIA_JEV_VAO
    cfg = J.dat_dinh_tuyen(os.environ.get("TRO_LY_DINH_TUYEN", "pplx"))   # [người vận hành] mặc định pplx-decider 0,7; TRO_LY_DINH_TUYEN=jev ⇒ lùi Jev 0,8
    GIA_JEV_VAO = cfg["gia_vao_1m"] / 1e6
    ghi(f"TRO-LY định tuyến {J.MO_HINH} ngưỡng {J.NGUONG_CONF}")
    # [ghi chú] Rút gọn bộ định tuyến khung chat về 5–6 ý định lời thường không chồng lấn (workflow mẫu vẫn giữ đủ trong tab Xưởng)
    the = [t for t in C.doc_the() if t.get("ma") in THE_CHINH_PA1]

    thieu = J.kiem_mo_ta(the)   # [ghi chú] mọi thẻ riêng phải có mô tả (mo_ta_the.json, khoá theo tên mẫu) — thiếu ⇒ KHÔNG bật (không gửi tên trần)
    if thieu:
        ghi(f"TRO-LY KHÔNG BẬT: {len(thieu)} thẻ riêng chưa có mô tả cho bộ định tuyến: {thieu[:5]} — thêm vào va_mot_o_chat/mo_ta_the.json rồi bật lại may_chu")
        raise RuntimeError("thẻ riêng thiếu mô tả")

    def trang_thai(phien):
        p = kiem_ma(phien)
        tt = (xem.sql("select row_to_json(x) from (select status::text as tt from workflow_run "
                      f"where session_id='{p}' order by created_at desc limit 1) x") or {}).get("tt")
        if tt in ("RUNNING", "QUEUED", None):
            ev = xem.mot_luot(p, None) or {}
            if any(e.get("t") == "awaiting_intervention" for e in ev.get("su_kien") or []):
                return "awaiting_intervention"
        return tt

    def mat_dong(phien):
        """[ghi chú] None | "khong_khoi" | "co_khoi" (hoi_temporal.mat_dong_loai): Temporal báo lượt đã kết thúc mà Postgres không có dòng. Lỗi ⇒ None."""
        try:
            import hoi_temporal   # giao_dien/ (cùng thư mục may_chu)
            p = kiem_ma(phien)
            return hoi_temporal.mat_dong_loai(p, lambda: bool(xem.sql(f"select row_to_json(x) from (select 1 as co from workflow_run where session_id='{p}' limit 1) x")),
                                         ngu=__import__("time").sleep)
        except Exception:  # noqa: BLE001
            return None

    def tin_khoi(phien, ids):
        """[E minor 1 gói 5] mọi khối trong ids có ĐÚNG 1 node_result COMPLETED của lượt MỚI NHẤT của phiên, không cached_from lượt khác."""
        p = kiem_ma(phien)
        if not ids or any(not isinstance(i, str) or not __import__("re").fullmatch(r"[0-9a-f-]{36}", i) for i in ids):
            return False
        ds = ",".join(f"'{i}'" for i in ids)
        r = xem.sql(f"""select row_to_json(x) from (select count(*) filter (where nr.status::text = 'COMPLETED' and nr.cached_from_run_id is null) as tot,
                count(*) as tong, count(distinct n.node_instance_id) as khoi
            from node_result nr join node n on n.id = nr.node_id
            where nr.run_id = (select id from workflow_run where session_id='{p}' order by created_at desc limit 1)
              and n.node_instance_id in ({ds})) x""") or {}
        return r.get("tot") == len(ids) and r.get("tong") == len(ids) and r.get("khoi") == len(ids)

    DEM = {}
    HN = sys.modules.get("hoi_nhanh")   # bản may_chu đã nạp (giao_dien/hoi_nhanh.py) — chỉ dùng _bang + nhãn, không gọi đường chạy của nó
    xuat = _xuat_b(ket_qua_luot, kiem_ma, xem=xem, ghi=ghi)
    ghi(f"TRO-LY thẻ KPI: {'có' if HN else 'KHÔNG (chưa nạp hoi_nhanh)'} · xuất kết quả: {'có' if xuat else 'KHÔNG (thiếu va_xuat_ket_qua)'}")
    return {**({"bang": HN._bang, "nhan_tam_tinh": HN.NHAN_TAM_TINH} if HN else {}), **({"xuat": xuat} if xuat else {}), "the": the, "ghim": TL.doc_ghim(),
            "store": store, "kho": TL.Kho(ghi=ghi, store=store, user_id="mac_dinh"), "kho_nguoi": {}, "dem": DEM,
            **({"ds_kho": ds_kho_tai_lieu} if ds_kho_tai_lieu else {}),   # [ghi chú] hàm (tổ chức) ⇒ các kho bền chưa ẩn
            "goi_jev": lambda body, n, cho: goi_jev_qua_so(S5, body, n, cho, ghi=ghi, dem=DEM),
            "pb": pb_moi_nhat, "toi_da": toi_da, "con_tran": lambda: float(S5.tran_hieu_luc()) - float(S5.da_tieu()),
            "chay": lambda uuid, ti, fi=None: chay(uuid, ti, file_inputs=fi) if fi else chay(uuid, ti),   # [ghi chú] fi = {mã khối nhận file: phong bì}
            **({"dua_file": dua_file} if dua_file else {}), "trang_thai": trang_thai, "mat_dong": mat_dong, "ket_qua": ket_qua_luot,
            **({"ky_trong": ky_trong} if ky_trong else {}),
            "operator": operator, "ghi": ghi, "ngu": __import__("time").sleep, "gio": __import__("time").time,
            **({"cache": dung_cache(*cache)} if cache else {}),
            # [ghi chú] mã NV của bản dữ liệu đang dùng (meta backend KPI, khoi_kpi nhớ 60 s) — không có ⇒ thẻ thay_nv không chạy
            "ds_nv": ds_nv or (lambda: None), "tin_khoi": tin_khoi,
            # [ghi chú] file đang mở theo cuộc trò chuyện (rào tên file = kho_tai_lieu.lam_sach_ten ĐANG GẮN của tab Tài liệu) + lý do lượt hỏng
            **({"tai_lieu": TL.TLC.KhoFile((lambda ten_, _K=sys.modules["kho_tai_lieu"]: _K.lam_sach_ten(ten_, _K.DUOI_CHAT)),   # [ghi chú] khung chat nhận thêm đuôi đi đường kho
                                          ghi=ghi, nap=nap_tai_lieu, viec=viec_tai_lieu, tim=tim_tai_lieu,
                                          viet_lai_cau=lambda c, ls: _viet_lai_cau_rag(S5, c, ls, ghi=ghi))} if sys.modules.get("kho_tai_lieu") else {}),
            "ly_do_hong": lambda phien: _ly_do_hong(xem, kiem_ma, phien)}


def _bat(kw) -> dict | None:
    """Dựng dep nếu chưa có (đường tải tài liệu có thể là yêu cầu ĐẦU TIÊN sau khi máy chủ bật). Không bật được ⇒ None."""
    if _TRANG["dep"] is None:
        try:
            _TRANG["dep"] = dung_dep(**kw)
        except Exception as e:  # noqa: BLE001
            (kw.get("ghi") or (lambda s: None))(f"TRO-LY chưa bật được: {type(e).__name__}: {str(e)[:120]}")
            return None
    return _TRANG["dep"]


def _kho_cua(d: dict, user_id: str | None = None):
    uid = (user_id or "mac_dinh").strip() or "mac_dinh"
    if uid == "mac_dinh":
        return d["kho"]
    kn = d.setdefault("kho_nguoi", {})
    if uid not in kn:
        kn[uid] = TL.Kho(ghi=d.get("ghi"), store=d.get("store"), user_id=uid)
    return kn[uid]


def nhan_tai_lieu(org, conv, ten, than, **kw) -> tuple[int, dict]:
    """[ghi chú] POST /api/tro-ly/tai-lieu — người dùng kéo file vào khung chat. Thẻ hỏi tài liệu chưa ghim / chưa mở ⇒ 503 nói rõ, KHÔNG giữ file."""
    user_id = kw.pop("user_id", None)
    d = _bat(kw)
    if d is None or not (isinstance(conv, str) and TL.MA_CONV.fullmatch(conv)) or not org:
        return 400, {"loi": TL.CHU["loi"]}
    if not d.get("tai_lieu") or not (d["ghim"].get(TL.TLC.MA_THE) or {}).get("mo"):
        return 503, {"loi": TL.TLC.CHU["chua_bat"].replace("Mình đã đọc được file, nhưng phần", "Phần")}
    ma, r = d["tai_lieu"].mo(org, conv, ten, than)
    if ma == 200:
        _kho_cua(d, user_id).them(conv, "tro_ly", r["chu"], dt=TL.TLC.dt_mo(r))   # [E T1] người dùng thấy tên file; bộ định tuyến chỉ nhận dòng trung tính (loại + số phần)
    return ma, r


def dong_tai_lieu(org, conv, **kw) -> tuple[int, dict]:
    user_id = kw.pop("user_id", None)
    d = _bat(kw)
    if d is None or not d.get("tai_lieu") or not (isinstance(conv, str) and TL.MA_CONV.fullmatch(conv)):
        return 400, {"loi": TL.CHU["loi"]}
    da = d["tai_lieu"].dong(org, conv)
    if da:
        _kho_cua(d, user_id).them(conv, "tro_ly", "Đã đóng tài liệu đang mở.")
    return 200, {"da_dong": da}


def mo_tai_lieu_kho(org, conv, sha, ds_kho, an, **kw) -> tuple[int, dict]:
    """[ghi chú] POST /api/tro-ly/tai-lieu/mo-kho — nút "Hỏi trên tài liệu này" của trang Dữ liệu. ds_kho = So.ds(org) (sổ kho của ĐÚNG tổ chức của
    yêu cầu — máy chủ truyền, không lấy từ trình duyệt); an = tập sha người dùng đã bấm Ẩn. Chỉ nhận tài liệu đang HIỆN ở bảng "Tài liệu đã đưa vào": không kho tạm
    (file kéo vào khung chat của người khác trong tổ chức), không kho đã tự ẩn, không tài liệu đã ẩn. Thẻ hỏi tài liệu chưa ghim / chưa mở ⇒ 503 như đường kéo file."""
    user_id = kw.pop("user_id", None)
    d = _bat(kw)
    if d is None or not (isinstance(conv, str) and TL.MA_CONV.fullmatch(conv)) or not org or not (isinstance(sha, str) and len(sha) == 64 and set(sha) <= set("0123456789abcdef")):
        return 400, {"loi": TL.CHU["loi"]}
    if not d.get("tai_lieu") or not (d["ghim"].get(TL.TLC.MA_THE) or {}).get("mo"):
        return 503, {"loi": TL.TLC.CHU["chua_bat"].replace("Mình đã đọc được file, nhưng phần", "Phần")}
    muc = next((x for x in (ds_kho or []) if isinstance(x, dict) and x.get("sha") == sha and not x.get("tam") and not x.get("an") and sha not in (an or ())), None)
    if not muc:
        return 404, {"loi": TL.TLC.CHU["kho_khong_co"]}
    ma, r = d["tai_lieu"].mo_tu_kho(org, conv, muc)
    if ma == 200:
        _kho_cua(d, user_id).them(conv, "tro_ly", r["chu"], dt=TL.TLC.dt_mo(r))   # [E T1] bộ định tuyến chỉ nhận dòng trung tính (loại), không tên file
    return ma, r


def tai_lieu_dang_mo(org, conv) -> tuple[int, dict]:
    """GET /api/tro-ly/tai-lieu?conv= — trang hỏi lại sau khi tải lại trang: cuộc trò chuyện này còn file mở không (chỉ tên + cỡ, không nội dung)."""
    d = _TRANG["dep"]
    v = d["tai_lieu"].dang_mo(org, conv) if d and d.get("tai_lieu") and isinstance(conv, str) and TL.MA_CONV.fullmatch(conv) else None
    return 200, ({"ten": v["ten"], "so_phan": len(v["kq"]["phan"]), "don_vi": v["kq"]["don_vi"], "ky_tu": v["kq"]["ky_tu"]} if v else {"ten": None})


def tai_tep(conv, m, user_id: str | None = None) -> tuple[int, str | None, bytes | None]:
    """GET /api/tro-ly/tep?conv=&id= — file đã xuất của ĐÚNG cuộc chat (Kho.lay_tep). Sai dạng / không có / hết hạn ⇒ 404 (không nói lý do)."""
    d = _TRANG["dep"]
    if d is None or not (isinstance(conv, str) and TL.MA_CONV.fullmatch(conv)) or not (isinstance(m, str) and 10 <= len(m) <= 40):
        return 404, None, None
    v = _kho_cua(d, user_id).lay_tep(conv, m)
    return (200, v["ten"], v["raw"]) if v else (404, None, None)


def lich_su_chat(conv: str | None = None, user_id: str | None = None, **kw) -> tuple[int, dict]:
    """GET /api/tro-ly/lich-su — đọc lại lịch sử bền vững từ SQLite cho đúng (user_id, conv)."""
    d = _bat(kw)
    if d is None:
        return 200, {"conv": conv, "tin": [], "cuoc_chat": []}
    k = _kho_cua(d, user_id)
    ds = k.ds_cuoc_chat()
    c_chon = conv if isinstance(conv, str) and TL.MA_CONV.fullmatch(conv) else (ds[0]["conv"] if ds else None)
    tin = []
    if c_chon:
        for m in k.lich_su(c_chon):
            tin.append({"vai": m.get("vai"), "noi_dung": m.get("noi_dung"), "ts": m.get("ts")})
    return 200, {"conv": c_chon, "tin": tin, "cuoc_chat": ds}


def xu_ly_route(than, **kw) -> tuple[int, dict]:
    """Route /api/tro-ly gọi hàm này. Chỉ nhận các khoá cau / conv / phieu / chon / xuat."""
    if not isinstance(than, dict) or set(than) - {"cau", "conv", "phieu", "chon", "xuat"}:
        return 400, {"loai": "loi", "chu": TL.CHU["loi"]}
    org = kw.pop("org", None)   # [ghi chú] tổ chức của yêu cầu — KHÔNG vào dep dùng chung
    user_id = kw.pop("user_id", None)
    if _bat(kw) is None:   # [ghi chú] không bật được (vd thẻ riêng thiếu mô tả) ⇒ câu lỗi chung, lần gọi sau thử lại; KHÔNG chạy gì
        return 200, {"loai": "loi", "chu": TL.CHU["loi"]}
    kho_u = _kho_cua(_TRANG["dep"], user_id)
    dep_u = dict(_TRANG["dep"], kho=kho_u, user_id=(user_id or "mac_dinh"))
    ma, r = TL.xu_ly(than, dep_u, org=org)
    if isinstance(r, dict) and kho_u.ls_rut.pop(than.get("conv"), None):   # [E F-U1, A] trả lời được câu "lượt này lịch sử có bị cắt không"
        r = dict(r, lich_su_rut_gon=True)
    return ma, r
