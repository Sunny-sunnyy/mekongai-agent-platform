"""[ghi chú kỹ thuật] "CHẠY LẠI TỪ KHỐI NÀY" cho trang 18400 — phần máy chủ. Thuần + hàm đọc đưa từ may_chu vào (thử được không cần trang).
Thiết kế + soát: tài liệu thiết kế nội bộ (đối chiếu kỹ thuật), va_chay_lai/NOI_MAY_CHU_KIEM_KHOA.md, kiem_khoa.py (đối chiếu kỹ thuật).

Đường Capitol dùng: POST /workflow/{org}/{wf}/rerun-from-node (AB api/routers/workflow.py:1458-1500) thân {source_session_id, starting_node_ids,
payload (AdvancedWorkflowPayload, session_id MỚI), single_step}. Capitol KHÔNG kiểm khoá cache lúc dùng lại ⇒ mình kiểm TRƯỚC bằng kiem_khoa.py
(chạy trong capitol-ab-api bằng chính hàm Capitol) ⇒ khối đã sửa (hoặc phía trước đã sửa) bị đưa vào tập chạy mới. Kiểm lỗi / hết giờ ⇒ KHÔNG
chạy lại (đối chiếu kỹ thuật).
Chặn TRƯỚC giữ chỗ: phiên nguồn chưa kết thúc · khác workflow · workflow KPI / có khối Rào chế độ code / sql (Rào đối chiếu theo lần gọi công cụ
CỦA LƯỢT — khối dùng lại không gọi công cụ trong lượt mới ⇒ đối chiếu sai lượt) · khối chọn không có trong bản đã lưu.
[ghi chú kỹ thuật] MÔ HÌNH ĐÃ ĐỔI: khoá cache của Capitol KHÔNG gồm file mô hình khối (cau_hinh_mo_hinh/mo_hinh_khoi.txt) ⇒ đổi luna ⇒
Claude rồi "chạy lại từ khối" sẽ dùng lại kết quả do mô hình CŨ tạo ra mà không ai biết. Vá: trước khi kiểm khoá, với mỗi khối thuộc loại đổi được mô hình, so
mô hình THẬT lượt nguồn đã gọi (node_result … message_history, kind=response, model_name — tên gửi trên dây) với mô hình khối sẽ gửi BÂY GIỜ
(muc_toi_da.mo_hinh_cua_khoi); khác ⇒ bỏ khoá đã lưu của khối đó ⇒ kiem_khoa đưa nó + mọi khối phía sau vào tập chạy mới. Khối KHÔNG lưu lịch sử lời gọi
(Word / Excel / PowerPoint / phân tích dữ liệu / D3 / đồ thị — đi hộp phiên) ⇒ không xác minh được ⇒ cũng chạy mới (đóng khi không chắc). Đọc mô hình lỗi ⇒ 503.
Tiền: giữ chỗ = muc_toi_da CHỈ của tập chạy mới (khối dùng lại = 0 đồng; sổ đã bỏ dòng cached_from_run_id — sandbox/chay_s5.py 27c4c7eb).
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

KET_THUC = ("SUCCESS", "FAILED", "STOPPED", "CANCELLED")
_MA_KHOI = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
KHOI_TOI_DA = 20
KIEM_GIAY = 10
HOP_KIEM = "capitol-ab-api"
FILE_KIEM = Path(__file__).resolve().with_name("kiem_khoa_chay_lai.py")   # bản gắn của va_chay_lai/kiem_khoa.py (đối chiếu kỹ thuật)


def kiem_than(b: dict) -> tuple[dict | None, str]:
    """Thân POST /api/chay-lai[/xem-truoc]: {workflow_id, phien_nguon, khoi: [mã khối react-flow], single_step?}. Trả (đã chuẩn, '') | (None, lý do)."""
    if not isinstance(b, dict) or set(b) - {"workflow_id", "phien_nguon", "khoi", "single_step"}:
        return None, "Chỉ nhận workflow_id, phien_nguon, khoi, single_step."
    wf, s, k = b.get("workflow_id"), b.get("phien_nguon"), b.get("khoi")
    if not (isinstance(wf, str) and re.fullmatch(r"[0-9a-f-]{36}", wf)) or not (isinstance(s, str) and re.fullmatch(r"[0-9a-f-]{36}", s)):
        return None, "workflow_id / phien_nguon phải là mã 36 ký tự."
    if not isinstance(k, list) or not 1 <= len(k) <= KHOI_TOI_DA or not all(isinstance(x, str) and _MA_KHOI.match(x) for x in k):
        return None, f"khoi: 1–{KHOI_TOI_DA} mã khối."
    ss = b.get("single_step", False)
    if not isinstance(ss, bool):
        return None, "single_step phải là true / false."
    return {"workflow_id": wf, "phien_nguon": s, "khoi": sorted(set(k)), "single_step": ss}, ""


def ly_do_khong_cho(payload: dict, la_kpi) -> str | None:
    """Workflow không được chạy lại từ khối (mục 3 tài liệu đối chiếu.md). la_kpi(nodes, edges) = khoi_dung_san.la_workflow_kpi."""
    nodes, edges = payload.get("nodes") or [], payload.get("edges") or []
    if la_kpi(nodes, edges):
        return "Workflow KPI không cho chạy lại từ khối (khối Rào kiểm số đối chiếu theo lần gọi công cụ của chính lượt) — chạy lại cả workflow."
    for n in nodes:
        st = (n.get("data") or {}).get("struct") or {}
        if st.get("node_id") == "guardrails_node":
            v = str(next((p.get("value") for p in st.get("params") or [] if p.get("field_id") == "che_do_kiem"), "") or "").strip().lower()
            if v in ("code", "sql"):
                return f"Workflow có khối Rào chế độ {v} (đối chiếu theo lượt) — không cho chạy lại từ khối; chạy lại cả workflow."
    return None


def kiem_khoa_trong_hop(payload: dict, luu: dict, bat_dau: list[str], single_step: bool, chay=subprocess.run) -> dict:
    """Chạy kiem_khoa (tap) trong capitol-ab-api. Lỗi / hết giờ / JSON hỏng ⇒ NÉM RuntimeError (người gọi KHÔNG chạy lại)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"payload": payload, "luu": luu, "bat_dau": bat_dau, "single_step": single_step}, f, ensure_ascii=False)
        tep = f.name
    dich = "/tmp/" + Path(tep).name   # tên riêng mỗi lần ⇒ hai lần xem trước cùng lúc không đè file của nhau
    try:
        for lenh in (["docker", "cp", str(FILE_KIEM), f"{HOP_KIEM}:/tmp/kiem_khoa_chay_lai.py"],
                     ["docker", "cp", tep, f"{HOP_KIEM}:{dich}"]):
            r = chay(lenh, capture_output=True, text=True, timeout=KIEM_GIAY)
            if r.returncode != 0:
                raise RuntimeError("không chép được vào hộp kiểm")
        r = chay(["docker", "exec", "-w", "/tmp", HOP_KIEM, "python", "-I", "/tmp/kiem_khoa_chay_lai.py", "tap", dich],
                 capture_output=True, text=True, timeout=KIEM_GIAY)
        if r.returncode != 0:
            raise RuntimeError("hàm kiểm khoá lỗi")
        d = json.loads((r.stdout or "").strip().splitlines()[-1])
        if not isinstance(d, dict) or not isinstance(d.get("tap_chay_moi"), list) or not d["tap_chay_moi"]:
            raise RuntimeError("kết quả kiểm khoá sai dạng")
        return d
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("kiểm khoá quá giờ") from e
    except (ValueError, IndexError) as e:
        raise RuntimeError("kết quả kiểm khoá không đọc được") from e
    finally:
        Path(tep).unlink(missing_ok=True)
        try:
            chay(["docker", "exec", HOP_KIEM, "rm", "-f", dich], capture_output=True, text=True, timeout=KIEM_GIAY)
        except Exception:  # noqa: BLE001 — dọn file tạm trong hộp, lỗi không ảnh hưởng kết quả
            pass


def _ten_tran(x) -> str:
    """Tên mô hình bỏ tiền tố nhà cung cấp kiểu "openai-responses:gpt-6-luna" (như sandbox/chay_s5.tien_luot)."""
    return str(x or "").split(":")[-1].strip()


def _mo_hinh_nguon_mac_dinh(s: str) -> dict:
    """{mã khối: [tên mô hình THẬT đã gọi]} của lượt mới nhất phiên s — đọc node_result.output_data.metadata.message_history (kind=response, model_name).
    Dòng DÙNG LẠI (cached_from_run_id) chép cả lịch sử của phiên nguồn (persist_results.py:1365-1380) ⇒ chuỗi chạy lại nhiều đời vẫn mang đúng mô hình đã tạo ra kết quả."""
    import xem_workflow as _xem   # cùng thư mục giao_dien/ (may_chu đã nạp)
    if not (isinstance(s, str) and re.fullmatch(r"[0-9a-f-]{36}", s)):
        raise ValueError("mã phiên không hợp lệ")
    return _xem.sql(f"""select coalesce(json_object_agg(x.k, x.ds), '{{}}') from (select n.node_instance_id as k, json_agg(distinct m->>'model_name') as ds
        from node_result nr join node n on n.id=nr.node_id,
             jsonb_array_elements(coalesce(nr.output_data::jsonb->'metadata'->'message_history', '[]'::jsonb)) m
        where nr.run_id=(select id from workflow_run where session_id='{s}' order by created_at desc limit 1)
          and nr.status='COMPLETED' and m->>'kind'='response' group by 1) x""") or {}


def _mo_hinh_nay_mac_dinh():
    """(hàm st ⇒ tên mô hình khối sẽ gửi bây giờ, bộ loại khối đổi được mô hình) — một nguồn với mức tiền của trang (muc_toi_da)."""
    import muc_toi_da as _mt
    return _mt.mo_hinh_cua_khoi, tuple(_mt.KHOI_EP_MO_HINH) + tuple(_mt.KHOI_HOP_PHIEN)


def mo_hinh_lech(payload: dict, luu: dict, mh_nguon: dict, mo_hinh_nay, loai_doi) -> dict:
    """{mã khối: lý do} các khối CÓ kết quả xong ở lượt nguồn, thuộc loại đổi được mô hình, mà mô hình lượt nguồn đã gọi ≠ mô hình bây giờ (hoặc không xác minh được).
    Thuần — không đọc gì ngoài tham số."""
    lech = {}
    for n in payload.get("nodes") or []:
        k, st = n.get("id"), (n.get("data") or {}).get("struct") or {}
        if k not in luu or st.get("node_id") not in loai_doi:
            continue
        nay = _ten_tran(mo_hinh_nay(st))
        cu = sorted({_ten_tran(x) for x in (mh_nguon.get(k) or []) if _ten_tran(x)})
        if not cu:
            lech[k] = f"lượt nguồn không ghi lại mô hình của khối này (loại khối không lưu lịch sử lời gọi) — không chắc kết quả cũ do mô hình hiện tại ({nay or '?'}) tạo ra"
        elif cu != [nay]:
            lech[k] = f"mô hình của khối đã đổi: lượt nguồn chạy {', '.join(cu)}; bây giờ là {nay or '?'}"
    return lech


def chuan_bi(than: dict, *, nguon, ban_moi, luu_nguon, la_kpi, toi_da, cung_to_chuc, kiem=kiem_khoa_trong_hop,
             mo_hinh_nguon=None, mo_hinh_nay=None, loai_doi=None) -> tuple[int, dict]:
    """Xem trước (KHÔNG chạy, 0 đồng): kiểm hết + kiểm khoá + mức tiền. nguon(phiên) ⇒ {"status", "workflow_id"} | None · ban_moi(wf) ⇒
    {"payload", "so"} | None · cung_to_chuc(wf) ⇒ True khi workflow thuộc tổ chức của trang · luu_nguon(phiên) ⇒ {inst: cache_key} (COMPLETED) · toi_da(payload) ⇒ (mức, thiếu). Trả (http, dict) —
    200 thì dict có "payload" (bản để chạy) để chay_lai dùng (trang chỉ thấy phần còn lại)."""
    b, ly = kiem_than(than)
    if b is None:
        return 400, {"loi": ly}
    if not cung_to_chuc(b["workflow_id"]):   # [ghi chú kỹ thuật] chặn TRƯỚC, như _assert_session_belongs_to_workflow
        return 403, {"loi": "Workflow không thuộc tổ chức của trang — không chạy lại."}
    src = nguon(b["phien_nguon"])
    if not src:
        return 404, {"loi": "Không thấy lượt nguồn."}
    if src.get("workflow_id") != b["workflow_id"]:
        return 400, {"loi": "Lượt nguồn không thuộc workflow này."}
    if src.get("status") not in KET_THUC:
        return 409, {"loi": f"Lượt nguồn chưa kết thúc ({src.get('status')}) — chờ xong rồi chạy lại."}
    v = ban_moi(b["workflow_id"])
    if not v:
        return 404, {"loi": "Không thấy phiên bản đã lưu của workflow này."}
    ly = ly_do_khong_cho(v["payload"], la_kpi)
    if ly:
        return 422, {"loi": ly}
    co = {n.get("id") for n in v["payload"].get("nodes") or []}
    la = [k for k in b["khoi"] if k not in co]
    if la:
        return 400, {"loi": f"Khối không có trong bản đã lưu: {la[:5]}"}
    try:
        luu = dict(luu_nguon(b["phien_nguon"]) or {})
        # [va_chay_lai_mo_hinh] mô hình của khối đã đổi so với lượt nguồn ⇒ bỏ khoá đã lưu của khối đó TRƯỚC khi kiểm khoá ⇒ kiem_khoa (hàm Capitol trong hộp)
        # coi như "không có kết quả xong" ⇒ khối + mọi khối phía sau vào tập chạy mới (single_step: khối phía sau khối chọn vẫn theo luật single_step — liệt kê).
        if mo_hinh_nay is None or loai_doi is None:
            _nay, _loai = _mo_hinh_nay_mac_dinh()
            mo_hinh_nay, loai_doi = mo_hinh_nay or _nay, _loai if loai_doi is None else loai_doi
        mh_lech = mo_hinh_lech(v["payload"], luu, (mo_hinh_nguon or _mo_hinh_nguon_mac_dinh)(b["phien_nguon"]) or {}, mo_hinh_nay, tuple(loai_doi))
        for k in mh_lech:
            luu.pop(k, None)
        kq = kiem(v["payload"], luu, b["khoi"], b["single_step"])
    except Exception as e:  # noqa: BLE001 — đóng khi lỗi (đối chiếu kỹ thuật): không chạy lại
        return 503, {"loi": f"Chưa kiểm được kết quả cũ còn đúng không ({e}) — KHÔNG chạy lại; chạy lại cả workflow."}
    for k, ly_do in mh_lech.items():   # lý do đúng cho người dùng (kiem_khoa chỉ biết "không có kết quả xong")
        if k in (kq.get("lech") or {}):
            kq["lech"][k] = ly_do
    tap = set(kq["tap_chay_moi"])
    con = {"nodes": [n for n in v["payload"].get("nodes") or [] if n.get("id") in tap], "edges": []}
    muc, thieu = toi_da(con)
    if thieu:
        return 402, {"loi": f"Khối chạy mới chưa có mức tiền tối đa: {thieu[:5]} — không chạy."}
    return 200, {"workflow_id": b["workflow_id"], "phien_nguon": b["phien_nguon"], "khoi_chon": b["khoi"], "single_step": b["single_step"],
                 "chay_moi": sorted(tap), "dung_lai": kq.get("dung_lai", []), "lech": kq.get("lech", {}),
                 "sau_dung_lai_cu": kq.get("sau_dung_lai_cu", []), "mo_hinh_lech": mh_lech, "muc_toi_da": muc, "phien_ban": v["so"], "payload": v["payload"]}


def con_payload(payload: dict, tap) -> dict:
    """Phần payload CHỈ gồm khối chạy mới — để tính mức giữ chỗ / sổ khối hộp phiên (khối dùng lại = 0 đồng)."""
    tap = set(tap)
    return {"nodes": [n for n in payload.get("nodes") or [] if n.get("id") in tap], "edges": []}


def than_capitol(cl: dict, payload: dict) -> dict:
    """Thân POST /workflow/{org}/{wf}/rerun-from-node. starting_node_ids = TẬP CHẠY MỚI đã kiểm khoá (không phải chỉ khối người chọn)."""
    return {"source_session_id": cl["phien_nguon"], "starting_node_ids": list(cl["chay_moi"]), "payload": payload, "single_step": bool(cl["single_step"])}


def ket_qua_chay(cl: dict) -> dict:
    """Phần thêm vào câu trả lời 200 của POST /api/chay-lai (trang hiện cho người dùng)."""
    return {"chay_lai_tu": cl["phien_nguon"], "khoi_chon": cl["khoi_chon"], "chay_moi": cl["chay_moi"], "dung_lai": cl["dung_lai"],
            "lech": cl["lech"], "sau_dung_lai_cu": cl["sau_dung_lai_cu"], "mo_hinh_lech": cl.get("mo_hinh_lech", {}), "single_step": cl["single_step"],
            "muc_toi_da": cl["muc_toi_da"]}


NHAN_0 = "trang_18400_chay_lai"


def _nen_giu_mac_dinh(s: str) -> bool:
    from pathlib import Path as _P
    import importlib.util as _iu   # [ghi chú kỹ thuật] nạp theo ĐƯỜNG DẪN TUYỆT ĐỐI (không dựa sys.path[0] = giao_dien/ — script khác gọi cũng nạp được)
    _sp = _iu.spec_from_file_location("hoi_temporal", _P(__file__).resolve().parent / "hoi_temporal.py"); _m = _iu.module_from_spec(_sp); _sp.loader.exec_module(_m)
    return _m.nen_giu_cho(s)


def _ghi_luot_mat_dong(H, s: str, nhan: str, ghi_chu: str, ma_giu, loai: str, ghi=lambda x: None) -> None:
    """[ghi chú kỹ thuật] Lượt KHÔNG có dòng trong cơ sở dữ liệu của Capitol mà Temporal báo đã kết thúc ⇒ ghi ĐÚNG MỘT dòng sổ rồi trả chỗ ngay (trước: chỗ giữ treo
    tới 3 giờ). loai = "khong_khoi" (lịch sử Temporal — nguồn độc lập với cơ sở dữ liệu — cho thấy 0 khối được chạy) ⇒ 0 đồng; còn lại ⇒ MỨC ĐÃ GIỮ CHỖ của lượt
    (cận trên — tiền đếm được bằng 0 ở đây chỉ vì không có dòng để đếm). Ghi sổ lỗi (ngoại lệ) ⇒ KHÔNG tới dòng trả chỗ: chỗ giữ còn chặn thay."""
    gc = ghi_chu + " (lượt không có dòng trong cơ sở dữ liệu của Capitol — Temporal báo đã kết thúc)"
    t = H.ghi_tien_mot_lan(s, nhan, gc)
    giu = float((H.dang_giu().get(ma_giu) or {}).get("usd") or 0) if ma_giu else 0.0
    if loai == "khong_khoi":
        usd, them = 0.0, " — 0 đồng: lịch sử Temporal cho thấy chưa khối nào được chạy"
    else:
        usd, them = max(giu, float((t or {}).get("usd") or 0)), " — ghi MỨC ĐÃ GIỮ CHỖ (cận trên): lịch sử Temporal cho thấy đã có khối chạy hoặc không đọc được, không đo được tiền thật"
    with H._khoa_so:   # ghi_tien_mot_lan không ghi gì khi không đếm được ⇒ thêm đúng MỘT dòng (mọi lượt qua trang có dòng sổ)
        da_co = H.SO.is_file() and any(x.get("session") == s for x in json.loads(H.SO.read_text(encoding="utf-8")).get("luot", []))
        if not da_co:
            H.ghi_tien(nhan, usd, {"session": s, "ghi_chu": gc + them})
    ghi(f"SỔ lượt {s}: mất dòng (Temporal đã kết thúc, cơ sở dữ liệu không có dòng) ⇒ đã ghi sổ + trả chỗ {ma_giu}")
    H.tra_cho(ma_giu)


def _hoi_mat_dong(H, s: str, mat_dong=None):
    """None | "khong_khoi" | "co_khoi". Mặc định dùng CHÍNH hàm của hoi_kpi (H._mat_dong_mac_dinh — hỏi Temporal + hỏi lại cơ sở dữ liệu); hoi_kpi bản cũ chưa có
    hàm đó ⇒ None (tắt, như trước gói này)."""
    f = mat_dong or getattr(H, "_mat_dong_mac_dinh", None)
    return f(s) if f else None


def theo_doi_ghi_tien(s: str, H, ghi_chu: str, ma_giu, trang_thai, nhan: str = "trang_18400", toi_da_giay: int = 3 * 3600,
                      ngu=None, gio=None, nen: bool = True, nen_giu=None, mat_dong=None) -> None:
    """[ghi chú kỹ thuật] Như hoi_kpi.theo_doi_ghi_tien nhưng lượt chạy lại có tiền = 0 (mọi khối AI dùng lại) vẫn có ĐÚNG 1 dòng sổ
    0 đồng nhãn NHAN_0 (luật: mọi lượt qua trang có dòng sổ — như dòng 0 đồng của cache câu trả lời). Ghi sổ lỗi ⇒ KHÔNG trả chỗ (chỗ giữ chặn thay)."""
    import threading
    import time as _t
    ngu, gio = ngu or _t.sleep, gio or _t.time

    def lam():
        t0, qua = gio(), False
        hoi_ke = t0 + getattr(H, "MAT_DONG_SAU_GIAY", 20)
        while True:
            tt = trang_thai(s)
            if tt in KET_THUC:
                ngu(3)
                break
            if gio() - t0 >= toi_da_giay:
                qua = True
                break
            # [ghi chú kỹ thuật] KHÔNG có dòng lượt (trạng thái rỗng) quá MAT_DONG_SAU_GIAY ⇒ hỏi Temporal (mỗi MAT_DONG_CACH_GIAY); chắc "mất dòng"
            # ⇒ ghi MỘT dòng sổ + trả chỗ ngay. Có dòng rồi (RUNNING / PAUSED…) thì KHÔNG hỏi. Còn chạy / không hỏi được ⇒ chờ tiếp như cũ.
            if not tt and gio() >= hoi_ke:
                hoi_ke = gio() + getattr(H, "MAT_DONG_CACH_GIAY", 30)
                loai = _hoi_mat_dong(H, s, mat_dong)
                if loai:
                    _ghi_luot_mat_dong(H, s, nhan, ghi_chu, ma_giu, loai)
                    return
            ngu(5)
        if qua:   # [ghi chú kỹ thuật] hỏi Temporal trước; còn chạy / không hỏi được ⇒ KHÔNG ghi, KHÔNG trả (giữ chỗ — chay_s5 sẽ ghi cận trên khi chỗ quá hạn (đối chiếu kỹ thuật))
            if ma_giu and (nen_giu or _nen_giu_mac_dinh)(s):   # [ghi chú kỹ thuật] không có chỗ giữ ⇒ ghi như cũ
                return
        gc = ghi_chu + (" (quá giờ theo dõi — Temporal báo đã xong — ghi phần đã tiêu)" if qua else "")
        t = H.ghi_tien_mot_lan(s, nhan, gc)
        if float(t.get("usd") or 0) == 0:
            with H._khoa_so:
                da_co = H.SO.is_file() and any(x.get("session") == s for x in json.loads(H.SO.read_text(encoding="utf-8")).get("luot", []))
                if not da_co:
                    H.ghi_tien(NHAN_0, 0.0, {"session": s, "ghi_chu": gc + " — 0 đồng (khối AI dùng lại kết quả cũ)"})
        H.tra_cho(ma_giu)
    if nen:
        threading.Thread(target=lam, daemon=True).start()
    else:
        lam()
