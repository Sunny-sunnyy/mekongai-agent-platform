"""Trang "Hỏi KPI" — phần xử lý (B 10/11/2026, đã giao: triển khai KPI agent trên Capitol, hai đã chốt). may_chu.py gọi các hàm ở đây.

Luồng (như `chay_kpi.py` CA=HOI, nhưng KHÔNG chờ hết lượt để người dùng bấm duyệt được):
  bat_dau(câu) ⇒ dựng workflow KPI 5 khối (Ô nhập ⇒ Agent + công cụ KPI ⇒ Rào chắn code ⇒ Người duyệt ⇒ Ô ra) ⇒ lưu + chạy qua API Capitol
  trang_thai(phiên) ⇒ câu trả lời Agent, Rào QUA / CHẶN (+ lý do), đang chờ duyệt (+ mã khối duyệt), tiền lượt + sổ ngày, phiên bản.
  Duyệt / Từ chối: may_chu.duyet() có sẵn (resume / stop của Capitol). Người duyệt hết giờ ⇒ lượt DỪNG, KHÔNG tự duyệt.
Sàn: một lượt một lúc; trần tiền ngày dùng chung sổ sandbox/chay_s5 (BUOI_S5 + TRAN_USD BẮT BUỘC đặt khi bật may_chu) — chưa đủ chỗ
cho 1 lượt (0,01) ⇒ không chạy; backend KPI_NAMES=off ⇒ chỉ có mã che NV01–NV14; on ⇒ câu trả lời có tên + mã SM thật (chủ ý, trang chỉ ở máy này); chỉ gọi API Capitol ở 127.0.0.1.
"""
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

DAY = Path(__file__).resolve().parent
sys.path[:0] = [str(DAY), str(DAY.parent), str(DAY.parent / "workflow_mau")]
import workflow_kpi as W  # noqa: E402
import temporal_luot as T  # noqa: E402
import chay_mau as CM  # noqa: E402
import chay_m3  # noqa: E402  (meta, phien_ban)
from sandbox.chay_s5 import SO, TRAN, da_tieu, dang_giu, ghi_tien, tien_luot  # noqa: E402,F401
from sandbox import chay_s5 as _so_chung  # noqa: E402

DUYET_GIAY = 300          # Người duyệt chờ 5 phút rồi dừng lượt (không tự duyệt)
TRAN_LUOT = 0.01          # chỗ trống tối thiểu trong trần ngày để bắt đầu 1 lượt (1 lượt KPI đo được ~0,0003–0,0009)
_khoa = threading.Lock()
PHIEN: dict[str, dict] = {}   # phiên ⇒ {cau, k (mã khối payload), pb, t0, tien, rao}
_MA = re.compile(r"^[0-9a-f-]{36}$")


_khoa_so = threading.Lock()

# [ghi chú] GIỮ CHỖ THẬT: sổ chỉ cộng khi lượt XONG ⇒ bấm nhiều lượt liền nhau thì lượt nào cũng thấy sổ còn trống.
# [ghi chú] bảng "đang giữ" nay là FILE DÙNG CHUNG mọi tiến trình (sandbox/chay_s5.py: giu_cho.json cạnh sổ, cùng khoá flock với sổ)
# ⇒ trang và các script đo thấy khoản giữ của nhau. Hai hàm dưới giữ NGUYÊN chữ ký cũ cho may_chu / chat_operator / so_skill; bảng trong bộ nhớ đã bỏ —
# xem bảng bằng dang_giu(). Dòng giữ không được trả (ghi sổ lỗi, tiến trình chết) KHÔNG mất khi may_chu khởi động lại: quá hạn thì thành dòng sổ cận trên
# "mo_coi_giu_cho" (chay_s5._don_mo_coi).


def giu_cho(can: float, nhan: str = "trang_18400", session: str | None = None) -> tuple[str | None, float]:
    """Kiểm + giữ trong CÙNG vùng khoá chung: đã tiêu + đang giữ (mọi tiến trình) + can ≤ TRAN ⇒ (mã, tổng trước); không đủ / không lấy được khoá trong 5 giây
    ⇒ (None, tổng trước)."""
    return _so_chung.giu_cho(can, nhan, session)


def giu_cho_ly_do() -> str | None:
    """Lý do lần giu_cho gần nhất của luồng này bị từ chối: None | "het_tran" | "so_ban" (sổ đang bận — báo "thử lại", không phải "sắp vượt trần")."""
    return _so_chung.ly_do_tu_choi()


def tra_cho(ma: str | None) -> None:
    """Bỏ chỗ đã giữ: SAU khi lượt đã ghi sổ (theo_doi_ghi_tien), hoặc khi lượt không chạy được (lỗi trước run-workflow)."""
    _so_chung.tra_cho(ma)


def ghi_tien_mot_lan(s: str, nhan: str, ghi_chu: str) -> dict:
    """[ghi chú] cộng tiền AI của phiên `s` vào sổ ngày ĐÚNG MỘT LẦN (sổ đã có phiên ⇒ bỏ qua). Lượt hỏng / dừng vẫn ghi phần đã tiêu."""
    with _khoa_so:
        try:
            da_co = any(x.get("session") == s for x in json.loads(SO.read_text()).get("luot", [])) if SO.is_file() else False
        except (OSError, json.JSONDecodeError):
            da_co = False
        t = tien_luot(s)
        # [ghi chú] phiên có gọi mô hình Bedrock VẪN có đúng 1 dòng sổ lượt dù usd (phần tính trần) = 0: dòng mang token_bedrock +
        # usd_bedrock_tham_khao (không cộng vào tổng / trần) ⇒ đối soát "phiên có kết quả mà thiếu dòng sổ" còn dùng được và bắt được lời gọi đi vòng cổng.
        tb = t.get("token_bedrock") or {}
        co_bedrock = bool((tb.get("vao") or 0) + (tb.get("ra") or 0)) or (t.get("usd_bedrock_tham_khao") or 0) > 0
        tu_choi = t.get("tu_choi_truoc_khi_chay") or []   # [ghi chú] lượt có khối bị hộp từ chối trước khi chạy ⇒ vẫn có MỘT dòng sổ (kể cả 0 đồng) kèm nhãn
        if not da_co and (t["usd"] > 0 or co_bedrock or tu_choi):
            ct = {"session": s, "ghi_chu": ghi_chu}
            if tu_choi:
                ct.update({"nhan_loi": "lỗi trước khi gọi AI", "tu_choi_truoc_khi_chay": tu_choi,
                           "ghi_chu": ghi_chu + f" — {len(tu_choi)} khối bị hộp từ chối trước khi chạy (lỗi trước khi gọi AI): phần của các khối đó ghi 0"})
            if co_bedrock:
                ct.update({"ncc": "bedrock", "token_bedrock": tb, "usd_bedrock_tham_khao": t.get("usd_bedrock_tham_khao")})
            ghi_tien(nhan, t["usd"], ct)
        return t


def _mat_dong_mac_dinh(s: str):
    """[ghi chú] None | "khong_khoi" | "co_khoi" (hoi_temporal.mat_dong_loai): lượt không có dòng Postgres mà Temporal báo đã kết thúc."""
    import importlib.util as _iu
    _sp = _iu.spec_from_file_location("hoi_temporal", _DUONG_HOI_TEMPORAL); _m = _iu.module_from_spec(_sp); _sp.loader.exec_module(_m)
    return _m.mat_dong_loai(s, lambda: bool(CM.psql(f"select 1 from workflow_run where session_id='{s}' limit 1")), ngu=time.sleep)


_DUONG_HOI_TEMPORAL = DAY.parent / "giao_dien" / "hoi_temporal.py"   # bộ thử trỏ sang bản trong ban/ trước khi gắn
MAT_DONG_SAU_GIAY, MAT_DONG_CACH_GIAY = 20, 30   # không thấy dòng lượt ≥ 20 giây ⇒ hỏi Temporal, sau đó mỗi 30 giây một lần


def _nen_giu_mac_dinh(s: str) -> bool:
    import importlib.util as _iu   # [E DT-M2] nạp theo ĐƯỜNG DẪN TUYỆT ĐỐI (không dựa sys.path[0] = giao_dien/ — script khác gọi cũng nạp được)
    _sp = _iu.spec_from_file_location("hoi_temporal", DAY.parent / "giao_dien" / "hoi_temporal.py"); _m = _iu.module_from_spec(_sp); _sp.loader.exec_module(_m)
    return _m.nen_giu_cho(s)


def theo_doi_ghi_tien(s: str, nhan: str, ghi_chu: str, toi_da_giay: int = 3 * 3600, ma_giu: str | None = None, nen_giu=None, mat_dong=None) -> None:
    """Luồng nền: chờ lượt kết thúc (SUCCESS / FAILED / STOPPED) rồi ghi tiền một lần. Máy chủ tắt giữa chừng ⇒ lượt đó chưa vào sổ (ghi nhật ký).
    [ghi chú] ma_giu: chỗ đã giữ (giu_cho) — bỏ SAU khi ghi sổ (cả nhánh quá giờ), để không có lúc lượt này vắng cả sổ lẫn bảng giữ."""
    def chay():
        # Ghi sổ lỗi (ngoại lệ) ⇒ KHÔNG bỏ chỗ: tiền lượt này chưa vào sổ, giữ chỗ còn chặn thay (lệch về phía an toàn).
        t0 = time.time()
        hoi_ke = t0 + MAT_DONG_SAU_GIAY
        while time.time() - t0 < toi_da_giay:
            st = CM.psql(f"select status::text from workflow_run where session_id='{s}' order by created_at desc limit 1")
            if st in ("SUCCESS", "FAILED", "STOPPED"):
                time.sleep(3)
                ghi_tien_mot_lan(s, nhan, ghi_chu)
                tra_cho(ma_giu)
                return
            # [ghi chú] KHÔNG có dòng lượt (st rỗng) quá MAT_DONG_SAU_GIAY ⇒ hỏi Temporal; chắc "mất dòng" ⇒ ghi MỘT dòng sổ rồi trả chỗ ngay
            # (trước: chờ đủ 3 giờ). Có dòng rồi (RUNNING / PAUSED…) thì KHÔNG hỏi — đường cũ lo. Còn chạy / không hỏi được ⇒ chờ tiếp.
            if not st and time.time() >= hoi_ke:
                hoi_ke = time.time() + MAT_DONG_CACH_GIAY
                loai = (mat_dong or _mat_dong_mac_dinh)(s)
                if loai:
                    gc = ghi_chu + " (lượt không có dòng trong cơ sở dữ liệu của Capitol — Temporal báo đã kết thúc)"
                    t = ghi_tien_mot_lan(s, nhan, gc)
                    # [B góp] tien_luot = 0 ở đây CHỈ vì không có dòng Postgres để đếm, chưa chứng minh không có lời gọi ⇒ "0 đồng" CHỈ khi lịch sử Temporal
                    # (nguồn độc lập) cho thấy 0 khối được chạy; còn lại ghi MỨC ĐÃ GIỮ CHỖ của lượt (cận trên — thà dư).
                    giu = float((dang_giu().get(ma_giu) or {}).get("usd") or 0) if ma_giu else 0.0
                    usd, loi_them = (0.0, " — 0 đồng: lịch sử Temporal cho thấy chưa khối nào được chạy") if loai == "khong_khoi" else                         (max(giu, float(t.get("usd") or 0)), " — ghi MỨC ĐÃ GIỮ CHỖ (cận trên): lịch sử Temporal cho thấy đã có khối chạy hoặc không đọc được, không đo được tiền thật")
                    with _khoa_so:   # ghi_tien_mot_lan không ghi gì khi không đếm được ⇒ thêm đúng MỘT dòng (mọi lượt qua trang có dòng sổ)
                        da_co = SO.is_file() and any(x.get("session") == s for x in json.loads(SO.read_text(encoding="utf-8")).get("luot", []))
                        if not da_co:
                            ghi_tien(nhan, usd, {"session": s, "ghi_chu": gc + loi_them})
                    print(f"[chay_thu_tien] lượt {s} mất dòng (Temporal đã kết thúc, Postgres không có dòng) ⇒ đã ghi sổ + trả chỗ {ma_giu}", flush=True)
                    tra_cho(ma_giu)
                    return
            time.sleep(5)
        # [ghi chú] quá giờ: hỏi Temporal trước; còn chạy / không hỏi được ⇒ KHÔNG ghi, KHÔNG trả (giữ chỗ — chay_s5 sẽ ghi cận trên khi chỗ quá hạn (D S1))
        if ma_giu and (nen_giu or _nen_giu_mac_dinh)(s):   # [E DT-M1] không có chỗ giữ ⇒ không có gì để chay_s5 đổi thành dòng cận trên ⇒ ghi như cũ
            print(f"[chay_thu_tien] quá giờ theo dõi {s} nhưng Temporal còn chạy / không hỏi được ⇒ GIỮ chỗ {ma_giu}", flush=True)
            return
        ghi_tien_mot_lan(s, nhan, ghi_chu + " (quá giờ theo dõi — Temporal báo đã xong — ghi phần đã tiêu)")
        tra_cho(ma_giu)
    if _MA.match(s or ""):
        threading.Thread(target=chay, daemon=True).start()
    else:
        tra_cho(ma_giu)


def _dang_chay() -> str | None:
    for s, x in PHIEN.items():
        if not x.get("xong") and time.time() - x["t0"] < DUYET_GIAY + 120:
            return s
    return None


def bat_dau(cau: str) -> tuple[int, dict]:
    cau = (cau or "").strip()
    if not 1 <= len(cau) <= 2000:
        return 400, {"loi": "Câu hỏi phải có 1–2000 ký tự."}
    if not (os.environ.get("BUOI_S5") and os.environ.get("TRAN_USD")):
        return 503, {"loi": "may_chu chưa đặt BUOI_S5 + TRAN_USD (sổ tiền ngày) — không chạy lượt có tiền."}
    with _khoa:
        if _dang_chay():
            return 409, {"loi": "Đang có một câu hỏi chạy / chờ duyệt — xong (hoặc duyệt / từ chối) rồi hỏi tiếp.", "session": _dang_chay()}
        if da_tieu() + TRAN_LUOT > TRAN:
            return 402, {"loi": f"Sắp vượt trần tiền ngày: đã tiêu {da_tieu():.4f} / {TRAN} USD — không chạy."}
        try:
            m = chay_m3.meta()
        except Exception:
            m = {}
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(m.get("cutoff") or "")):
            return 503, {"loi": "kpi-backend chưa sẵn (meta thiếu cutoff) — chờ vài giây rồi hỏi lại."}
        y, mth, d = m["cutoff"].split("-")
        org = (CM.KQ / "org_id.txt").read_text().strip()
        _, catalog = CM.goi("POST", f"/nodes/{org}/list-nodes", {})
        W.kiem_danh_muc(CM.tim_khoi, catalog)
        v = CM.Ve(catalog)
        k = W.dung(v, cau, ky=m["period"], ngay_chot=f"{d}/{mth}/{y}", ban_luat=m.get("rule_version"),
                   trang_thai=m.get("status_label_for_model"), duyet_giay=DUYET_GIAY, nhan_vien=m.get("employee_ids"))
        kiem = W.kiem_payload(v.nodes, v.edges)
        if not kiem.get("dat"):
            return 500, {"loi": f"Workflow KPI không qua kiểm payload: {kiem}"}
        # [ghi chú] giữ chỗ theo MỨC TỐI ĐA của workflow vừa dựng (kiểm 0,01 ở trên chỉ là cửa sớm trước khi dựng)
        from giao_dien.muc_toi_da import toi_da
        can, thieu = toi_da({"nodes": v.nodes})
        if thieu:
            return 402, {"loi": f"Workflow KPI có khối gọi AI chưa có mức tiền tối đa: {thieu[:5]} — không chạy."}
        ma_giu, tong = giu_cho(can)
        if ma_giu is None:
            return 402, {"loi": f"Sắp vượt trần tiền ngày: đã tiêu + đang giữ {tong:.4f} + giữ chỗ tối đa {can:.4f} > {TRAN} USD — không chạy."}
        try:
            return _chay_kpi(cau, k, m, v, org, ma_giu)
        except BaseException:
            tra_cho(ma_giu)   # lỗi trước khi lượt được giao cho luồng theo dõi ⇒ bỏ chỗ (_chay_kpi tự bỏ ở các nhánh trả lỗi)
            raise


def _chay_kpi(cau, k, m, v, org, ma_giu) -> tuple[int, dict]:
    """[ghi chú] phần sau giữ chỗ của bat_dau (tách ra để mọi nhánh lỗi đều bỏ chỗ đã giữ). Gọi trong _khoa của bat_dau."""
    pb = chay_m3.phien_ban()
    pl = {"id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "name": "KPI hỏi trên trang", "description": "chay_thu B 10/11: trang Hỏi KPI",
          "nodes": v.nodes, "edges": v.edges, "metadata": {}, "global_context": {},
          "publish_to_api": False, "publish_to_mcp": False, "publish_to_template": False}
    c, r = CM.goi("POST", f"/api/v1/orgs/{org}/workflows", pl)
    wf = ((r.get("workflow") or {}).get("id") or r.get("workflow_id") or r.get("id")) if isinstance(r, dict) else None
    if not wf:
        tra_cho(ma_giu)
        return 502, {"loi": f"Capitol không lưu được workflow (HTTP {c})."}
    _, ses = CM.goi("GET", "/session/create-session")
    s = ses["session_id"]
    c2, _ = CM.goi("POST", f"/workflow/{org}/{wf}/{s}/run-workflow", dict(pl, id=wf, session_id=s))
    if c2 >= 300:
        tra_cho(ma_giu)   # Capitol từ chối ⇒ chưa có lượt chạy ⇒ không tốn tiền
        return 502, {"loi": f"Capitol từ chối chạy (HTTP {c2})."}
    PHIEN[s] = {"cau": cau, "k": k, "pb": pb, "t0": time.time(), "meta": {x: m.get(x) for x in ("cutoff", "workflow_version", "rule_version")}}
    # luồng theo dõi giữ chỗ tới khi lượt KẾT THÚC rồi mới bỏ (trang_thai vẫn ghi sổ sớm lúc chờ duyệt — ghi_tien_mot_lan chỉ ghi 1 lần)
    theo_doi_ghi_tien(s, "kpi_trang_hoi", "trang Hỏi KPI (may_chu.py) — luồng theo dõi", ma_giu=ma_giu)
    return 200, {"session": s}


def trang_thai(s: str) -> tuple[int, dict]:
    if not _MA.match(s or "") or s not in PHIEN:
        return 404, {"loi": "Không có phiên hỏi này (trang chỉ theo dõi câu hỏi tạo từ trang, từ lúc may_chu bật)."}
    x = PHIEN[s]
    st = CM.psql(f"select status::text from workflow_run where session_id='{s}' order by created_at desc limit 1")
    ev = CM.psql("select coalesce(string_agg(e.event_type||'|'||coalesce(e.node_instance_id,''), ' ' order by e.timestamp), '') "
                 f"from workflow_event e join workflow_run r on r.id=e.run_id where r.session_id='{s}' "
                 "and e.event_type in ('awaiting_intervention','execution_failed','termination')").split()
    cho = [e.split("|")[1] for e in ev if e.startswith("awaiting_intervention|")]
    ket_thuc = st in ("SUCCESS", "FAILED", "STOPPED")
    tra_loi = CM.psql("select coalesce(string_agg(nr.output_data->>'data', ' '), '') from node_result nr join workflow_run r on r.id=nr.run_id "
                      f"where r.session_id='{s}' and nr.output_data::jsonb->'metadata' ? 'message_history'")
    if cho:
        x["rao"] = {"qua": True, "ly_do": []}          # Người duyệt chỉ được gọi khi Rào chắn qua
    elif ket_thuc and "rao" not in x:
        tl = T.lich_su(s, x["k"])
        x["rao"] = {"qua": (tl.get("xong") or {}).get("rao", 0) >= 1,
                    "ly_do": [y["ly_do"] for y in tl.get("ly_do_loi") or [] if "Human intervention" not in y["ly_do"]]}
    if (cho or ket_thuc) and "tien" not in x:          # Agent đã xong ⇒ ghi tiền MỘT lần vào sổ ngày
        t = ghi_tien_mot_lan(s, "kpi_trang_hoi", "trang Hỏi KPI (may_chu.py)")
        x["tien"], x["cong_cu"] = round(t["usd"], 6), t["cong_cu"]
    dang_cho = bool(cho) and not ket_thuc and not any(e.startswith(("execution_failed|", "termination|")) for e in ev)
    if ket_thuc:
        x["xong"] = True
    return 200, {"session": s, "cau": x["cau"], "status": st, "ket_thuc": ket_thuc, "dang_cho_duyet": dang_cho,
                 "node_duyet": cho[-1] if cho else None, "tra_loi": tra_loi, "rao": x.get("rao"), "cong_cu": x.get("cong_cu"),
                 "tien": x.get("tien"), "so_ngay": round(da_tieu(), 6), "tran": TRAN, "so_tien": os.environ.get("BUOI_S5"),
                 "giay_cho_con": max(0, int(DUYET_GIAY - (time.time() - x["t0"]))) if dang_cho else None,
                 "meta": x["meta"], "phien_ban": x["pb"]}
