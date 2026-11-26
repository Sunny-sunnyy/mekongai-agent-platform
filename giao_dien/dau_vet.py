"""Dấu vết một lượt chạy — C 11/11/2026 (đã giao, bảng hướng phát triển bậc 1). Chỉ ĐỌC dữ liệu Capitol đã có của lượt (PostgreSQL + sự kiện khối),
không gọi AI, 0 đồng. Gom theo khối: thời gian · lần gọi mô hình (tên mô hình + token + tiền LẤY TỪ BẢN GHI LẦN GỌI — message_history, qua
may_chu.chi_phi) · công cụ đã gọi + tham số + tóm kết quả (cắt ngắn, may_chu.cong_cu_da_goi) · Rào chắn (lần đầu / lần cuối đạt hay không + lý do)
· Người duyệt (chờ từ, quyết lúc, chờ bao lâu, qua đâu).

Vì sao "từ bản ghi lần gọi", không từ cấu hình khối (bài học "kiểm từ nguồn sự thật"): cấu hình ghi gpt-6-luna nhưng lần gọi thật có thể khác
(đường dự phòng, khối gọi mô hình mặc định). Cấu hình vẫn hiện bên cạnh để so; khác nhau ⇒ cờ đỏ.
Mức suy nghĩ: bản ghi lần gọi KHÔNG có (pydantic-ai bỏ trường reasoning của OpenAI) ⇒ ghi rõ "không có trong bản ghi" + cấu hình trong mã
(đã chốt 11/11, phương án a). Bằng chứng gián tiếp có thật: reasoning_tokens trong usage.
"""
import json
import re
from datetime import datetime

MO_HINH_DUNG = "gpt-6-luna"   # luật người vận hành: mọi khối AI dùng gpt-6-luna — [ghi chú] từ 18/11 User chuyển sang Claude qua Bedrock ⇒ chỉ còn là mặc định khi chưa nối
# [ghi chú] may_chu gán lại hai hàm dưới (một nguồn: bảng cổng AI + file mô hình khối). Mặc định = hành vi cũ (chỉ luna hợp lệ) để module tự chạy được.
MO_HINH_HOP_LE = lambda m: m == MO_HINH_DUNG   # noqa: E731 — mô hình có trong bảng cổng AI không
CAU_HINH_FILE = lambda: {}                      # noqa: E731 — {"mac_dinh", "theo_khoi", "khoi_doi_duoc", "loi"} của file mô hình khối LÚC XEM
_MUC = ("low", "medium", "high", "xhigh", "max")


def _muc_tu_ten(ten: str):
    """Tên gửi trên dây "claude-sonnet-5-5-medium" ⇒ "medium"; không có đuôi mức ⇒ None."""
    duoi = str(ten or "").rsplit("-", 1)[-1]
    return duoi if duoi in _MUC else None


def _mo_hinh_file(loai: str):
    """Mô hình file mô hình khối đang đặt cho loại khối này (None = file không đổi loại khối này / không đọc được)."""
    try:
        c = CAU_HINH_FILE() or {}
        return (c.get("theo_khoi") or {}).get(loai) or c.get("mac_dinh") or None if loai in (c.get("khoi_doi_duoc") or []) else None
    except Exception:  # noqa: BLE001
        return None
TRAN_CONG_CU = 60              # công cụ hiện tối đa mỗi khối
TRAN_THAM_SO, TRAN_KET_QUA = 300, 200
TRAN_NHAT_KY = 2_000_000     # byte cuối của nhật ký trang được đọc khi tìm dòng DUYỆT / TỪ CHỐI
TRAN_GIA_TRI = 300_000       # đọc giá trị node_result (Rào code ≈ 21.000 ký tự — cắt 20.000 làm JSON cụt, đọc không được: C thử 11/11)
# Mức suy nghĩ theo cấu hình trong MÃ (không phải sự thật lần gọi). Khối Agent: không có ô đè (danh mục khối: model / temperature / max_tokens —
# va_lam_lai/workflow_kpi.py:133) ⇒ registry quyết. Loại khối AI khác: chưa lần đường cấu hình ⇒ không khẳng định.
MUC_CAU_HINH = {"gpt-6-luna": "low (sua_doi/core/config/ai/registry.py:468)"}
LOAI_AGENT_KHONG_O_DE = {"agent_node"}


def _ts(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00").replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def _giay(a, b):
    ta, tb = _ts(a), _ts(b)
    return round((tb - ta).total_seconds(), 1) if ta and tb else None


def _giay_khoi(es: list[dict]) -> float | None:
    """Tính thời lượng một khối chịu được lệch giờ lùi trên WSL:
    nếu begin.ts > end.ts hoặc chênh timestamp âm, lùi về created_at (DB) rồi kẹp >= 0.0."""
    if not es:
        return None
    tss = [_ts(e.get("ts")) for e in es]
    valid_ts = [x for x in tss if x is not None]
    if valid_ts:
        g_ts = round((valid_ts[-1] - valid_ts[0]).total_seconds(), 1)
        g_span = round((max(valid_ts) - min(valid_ts)).total_seconds(), 1)
    else:
        g_ts, g_span = None, None
    cs = [_ts(e.get("c")) for e in es]
    valid_c = [x for x in cs if x is not None]
    g_c = round((valid_c[-1] - valid_c[0]).total_seconds(), 1) if len(valid_c) >= 2 else None
    if g_ts is not None and g_ts >= 0:
        # Nếu sự kiện đầu/cuối trùng ts (vd begin bị kẹp về end) mà created_at hoặc dải ts rộng hơn ⇒ lấy dải đáng tin
        if g_ts == 0.0:
            if g_c is not None and g_c > 0:
                return g_c
            if g_span is not None and g_span > 0:
                return g_span
        return g_ts
    if g_c is not None and g_c >= 0:
        return g_c
    return max(0.0, g_span) if g_span is not None else None


DAU_HIEU_DUNG = "KHÔNG LÀM ĐƯỢC"   # [ghi chú] chuỗi cố định của lời dặn dùng chung (va_chan_luong/loi_dan_chung.py)


def _cong_cu_loi(ket_qua, thu_lai: bool) -> bool:
    """[ghi chú] một lần gọi công cụ là LỖI khi: pydantic-ai bắt thử lại (retry-prompt) hoặc kết quả là {\"ok\": false, …} (khuôn lỗi chung
    của công cụ bộ mình — native_tools.py). {\"refused\": …} của công cụ KPI là TỪ CHỐI ĐÚNG LUẬT, không tính lỗi."""
    if thu_lai:
        return True
    s = str(ket_qua or "")
    try:
        x = json.loads(s)
        return isinstance(x, dict) and x.get("ok") is False
    except ValueError:   # kết quả bị cắt 400 ký tự ⇒ không đọc được JSON ⇒ dò chuỗi
        return bool(re.match(r'\s*\{\s*["\']ok["\']\s*:\s*(false|False)\b', s))


def _cat(s, n):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + "…"


def _rao_tu_gia_tri(v: str):
    """Giá trị node_result của Rào chắn (JSON "Kết quả kiểm bằng code") ⇒ (đạt?, lý do) hoặc None."""
    try:
        d = json.loads(v or "")
    except ValueError:
        return None
    if not isinstance(d, dict) or "dat" not in d:
        return None
    kq = d.get("ket_qua_dich_vu") if isinstance(d.get("ket_qua_dich_vu"), dict) else {}
    ly = kq.get("ly_do")
    if not ly and isinstance(kq.get("loi"), list):
        ly = "; ".join(str((x or {}).get("loai") or x)[:80] for x in kq["loi"][:5])
    return bool(d.get("dat")), _cat(ly or ("đạt" if d.get("dat") else "không đạt"), 300)


def _duyet_tu_nhat_ky(nhat_ky_path, phien: str, node: str):
    """Dòng 'DUYỆT / TỪ CHỐI phiên <s> khối <node>' do may_chu ghi khi người dùng bấm trên trang 18400 ⇒ (quyết, giờ) hoặc None."""
    try:   # [D minor 11/11] chỉ đọc ≤ 2 MB CUỐI file (nay 66 KB) — file lớn dần không làm chậm / tốn RAM mỗi lần mở; dòng cũ hơn ⇒ "không có trong nhật ký"
        with open(nhat_ky_path, "rb") as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - TRAN_NHAT_KY))
            dong = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    m = None
    for d in dong:
        if f"phiên {phien} khối {node}:" in d and ("DUYỆT" in d or "TỪ CHỐI" in d):
            m = d
    if not m:
        return None
    g = re.match(r"^\[(\d\d:\d\d:\d\d \d\d/\d\d/\d{4})\]\s+(DUYỆT|TỪ CHỐI)", m)
    return (g.group(2), g.group(1)) if g else ("không đọc được dòng", None)


def dau_vet(xem, phien: str, chi_phi, cong_cu_da_goi, nhat_ky_path) -> dict | None:
    if not isinstance(phien, str) or not xem.MA.fullmatch(phien):
        raise ValueError("mã không hợp lệ")
    r = xem.sql(f"""select row_to_json(x) from (select r.id, r.status::text as tt, r.created_at as tao,
            (select payload from workflow_version v where v.id=r.version_id) as payload
        from workflow_run r where r.session_id='{phien}' order by r.created_at desc limit 1) x""")
    if not r or not xem.MA.fullmatch(str(r.get("id") or "")):
        return None
    rid = r["id"]
    do_thi = xem.do_thi(r.get("payload") or {})
    # sự kiện gọn của lượt: thời gian từng khối + sự kiện của Rào / Người duyệt (giá trị cắt TRAN_GIA_TRI ký tự)
    ev = xem.sql(f"""select coalesce(json_agg(json_build_object('n', coalesce(e.node_instance_id::text, e.event_data->'data'->>'node_id'),
            't', e.event_type, 'ts', e.timestamp, 'c', e.created_at,
            'v', case when e.event_type='node_result' then left(e.event_data->'data'->>'value', {TRAN_GIA_TRI}) end,
            'loi', case when e.event_type='execution_failed' then left(e.event_data->'data'->>'error', 500) end,
            'res', case when e.event_type='lifecycle' then e.event_data->'data'->>'resolution' end,
            'han', case when e.event_type='awaiting_intervention' then e.event_data->'data'->>'timeout' end) order by e.created_at, e.timestamp, e.id), '[]')
        from workflow_event e where e.run_id='{rid}' and e.event_type in
            ('begin', 'end', 'node_result', 'execution_failed', 'execution_successful', 'awaiting_intervention', 'lifecycle')""") or []
    theo_khoi: dict = {}
    for e in ev:
        if e.get("n"):
            theo_khoi.setdefault(e["n"], []).append(e)
    cp = chi_phi(phien)
    goi = {}
    for c in cp.get("calls", []):
        goi.setdefault(c.get("node"), []).append(c)
    cc = {}
    for t in cong_cu_da_goi(phien):
        if not t.get("internal"):
            cc.setdefault(t.get("node"), []).append(t)
    ra = []
    for k in do_thi["khoi"]:
        nid, loai = k["id"], k.get("ma_loai")
        es = theo_khoi.get(nid, [])
        tt = next((e["t"] for e in reversed(es) if e["t"] in ("execution_successful", "execution_failed")), None)
        muc = {"id": nid, "ten": k.get("ten"), "loai": loai, "loai_goc": k.get("loai_goc"),
               "trang_thai": {"execution_successful": "xong", "execution_failed": "lỗi"}.get(tt, "chưa chạy / bỏ qua" if not es else "dở"),
               "giay": _giay_khoi(es)}
        lan = goi.get(nid, [])
        if lan or k.get("mo_hinh"):
            mh = sorted({x.get("model") for x in lan if x.get("model")})
            # [ghi chú] cờ đỏ CHỈ khi mô hình trong bản ghi lần gọi KHÔNG có trong bảng cổng AI (tên lạ). Trong bảng nhưng khác file hiện tại ⇒ ghi chú thường.
            do = [m for m in mh if not MO_HINH_HOP_LE(m)]
            mh_file = _mo_hinh_file(loai)
            o_la = bool(k.get("mo_hinh")) and not MO_HINH_HOP_LE(str(k.get("mo_hinh")).rsplit(":", 1)[-1].strip()) and not mh and not mh_file
            khac_file = ([f"file mô hình khối hiện đặt {mh_file} cho loại khối này; lượt này đã chạy {', '.join(mh)} (file có thể đã đổi sau lượt)"]
                         if mh and mh_file and any(m != mh_file for m in mh) else
                         [f"lượt này chạy theo ô của khối ({', '.join(mh)}); file mô hình khối hiện không đặt gì cho loại khối này"] if mh and not mh_file and loai in LOAI_AGENT_KHONG_O_DE and any(m != MO_HINH_DUNG for m in mh) else [])
            muc["ai"] = {
                "so_lan_goi": len(lan), "mo_hinh_ban_ghi": mh, "mo_hinh_cau_hinh": k.get("mo_hinh"), "mo_hinh_file": mh_file,
                "co_do": bool(do) or o_la,
                "ly_do_co": (f"lần gọi thật dùng {', '.join(do)} — KHÔNG có trong bảng cổng AI" if do else
                             (f"ô của khối ghi {k.get('mo_hinh')} — không có trong bảng cổng AI" if o_la else "")),
                "token_vao": sum(x.get("tokens_in") or 0 for x in lan), "token_ra": sum(x.get("tokens_out") or 0 for x in lan),
                "token_suy_nghi": sum(x.get("tokens_reasoning") or 0 for x in lan),
                "tien_usd": round(sum(x.get("cost_usd") or 0 for x in lan), 6),
                "so_lan_khong_do_duoc": sum(1 for x in lan if not x.get("measurable", True) or (x.get("measurable") and x.get("cost_usd") is None)),
                "muc_suy_nghi": ((", ".join(sorted({_muc_tu_ten(m) for m in mh})) + " (theo đuôi tên tuyến gửi cổng AI, ghi trong bản ghi lần gọi)")
                                 if mh and all(_muc_tu_ten(m) for m in mh) else
                                 "không có trong bản ghi lần gọi — cấu hình trong mã: "
                                 + (MUC_CAU_HINH.get(mh[0] if len(mh) == 1 else "", "chưa rõ (tên không có đuôi mức ⇒ mức mặc định của bảng cổng AI)")
                                    + ("" if loai in LOAI_AGENT_KHONG_O_DE else " (loại khối này chưa lần đường cấu hình — có thể bị ô khối đè)"))),
                "ghi_chu": (khac_file + [x["why"] for x in lan if x.get("why")])[:5],
            }
        t = cc.get(nid, [])
        if t:
            muc["cong_cu"] = [{"ten": x.get("tool"), "tham_so": _cat(x.get("args"), TRAN_THAM_SO), "ket_qua": _cat(x.get("result"), TRAN_KET_QUA),
                               "thu_lai": bool(x.get("retry")), "giay": _giay(x.get("at"), x.get("returned_at"))} for x in t[:TRAN_CONG_CU]]
            muc["so_cong_cu"] = len(t)
            muc["so_cong_cu_loi"] = sum(_cong_cu_loi(x.get("result"), bool(x.get("retry"))) for x in t)   # [ghi chú] đếm trên MỌI lần, không chỉ phần hiện
            for x, c in zip(t, muc["cong_cu"]):
                c["loi"] = _cong_cu_loi(x.get("result"), bool(x.get("retry")))
        if any(e["t"] == "node_result" and DAU_HIEU_DUNG in str(e.get("v") or "")[:400] for e in es):
            muc["tu_bao_khong_lam_duoc"] = True   # [ghi chú] khối tự báo hỏng theo lời dặn dùng chung
        if loai == "guardrails_node":
            lan_kiem = []
            for e in es:
                if e["t"] == "node_result":
                    x = _rao_tu_gia_tri(e.get("v"))
                    if x:
                        lan_kiem.append({"luc": e["ts"], "dat": x[0], "ly_do": x[1]})
                elif e["t"] == "execution_failed":
                    lan_kiem.append({"luc": e["ts"], "dat": False, "ly_do": _cat(e.get("loi"), 300)})
            muc["rao"] = {"so_lan": len(lan_kiem), "dau": lan_kiem[0] if lan_kiem else None, "cuoi": lan_kiem[-1] if lan_kiem else None}
        if loai == "human_intervention_node":
            cho = next((e for e in es if e["t"] == "awaiting_intervention"), None)
            quyet = next((e for e in es if e["t"] == "lifecycle"), None)
            nk = _duyet_tu_nhat_ky(nhat_ky_path, phien, nid)
            muc["duyet"] = {"cho_tu": cho["ts"] if cho else None, "han_giay": cho.get("han") if cho else None,
                            "quyet_luc": quyet["ts"] if quyet else None, "ket_qua": quyet.get("res") if quyet else None,
                            "cho_giay": _giay(cho["ts"], quyet["ts"]) if cho and quyet else None,
                            "qua": (f"bấm trên trang 18400 (máy này): {nk[0]} lúc {nk[1]}" if nk else
                                    ("không có trong nhật ký trang 18400 — duyệt qua đường khác (script / API) hoặc hết hạn chờ" if quyet else "chưa quyết")),
                            "ai": "Capitol không ghi tên người duyệt trong sự kiện (chỉ có kết quả + giờ)"}
        ra.append(muc)
    tong = {"tien_usd": round(sum((m.get("ai") or {}).get("tien_usd") or 0 for m in ra), 6),
            "so_lan_goi": sum((m.get("ai") or {}).get("so_lan_goi") or 0 for m in ra),
            "co_do": [m["ten"] for m in ra if (m.get("ai") or {}).get("co_do")],
            "khong_do_duoc": cp.get("total", {}).get("unmeasured_calls", 0),
            "hong_mem": [m["ten"] for m in ra if m.get("so_cong_cu_loi") or m.get("tu_bao_khong_lam_duoc")]}   # [ghi chú] xanh mà rỗng
    return {"luot": {"phien": phien, "tt": r.get("tt"), "tao": r.get("tao"), "ten": do_thi.get("ten")}, "khoi": ra, "tong": tong}
