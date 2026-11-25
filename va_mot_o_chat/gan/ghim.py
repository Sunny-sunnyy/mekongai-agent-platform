"""Ghim THẺ MỞ ⇒ (UUID, sha payload, số phiên bản) — C 18/11/2026, E G2 (tài liệu đối chiếu.md "kế hoạch gắn"): KHÔNG tìm theo tên bản mới nhất lúc chạy.
0 đồng, chỉ SELECT PostgreSQL. Ai đó Lưu phiên bản mới ⇒ sha khác ⇒ máy chủ không tự chạy (hỏi lại) tới khi ghim lại (chạy lại script này + đã soát).
  python3 -I chay_thu/va_mot_o_chat/gan/ghim.py            # in bảng thẻ × UUID × mức × tự chạy / hỏi; KHÔNG ghi
  GHI=co python3 -I chay_thu/va_mot_o_chat/gan/ghim.py     # ghi the_ghim.json cạnh file này
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

G = Path(__file__).resolve().parent
sys.path[:0] = [str(G), str(G.parent), str(G.parent.parent / "giao_dien")]
import chon_jev as J  # noqa: E402
import muc_toi_da as MT  # noqa: E402
import xem_workflow as X  # noqa: E402
import tro_ly as TL  # noqa: E402  — [ghi chú kỹ thuật] ghim chạy CHÍNH rào hình của đường chạy (ra_ngoai_hop_le)

_STATE_DIR = Path(os.environ.get("CAPITOL_STATE_DIR") or (Path.home() / ".local" / "share" / "capitol-platform")).expanduser()
_RUNTIME_DIR = Path(os.environ.get("CAPITOL_RUNTIME_DIR") or (G.parent.parent / "runtime")).expanduser()
ORG = (_RUNTIME_DIR / "org_id.txt").read_text().strip()
assert re.fullmatch(r"[0-9a-f-]{36}", ORG)
RA = G / "the_ghim.json"


# [ghi chú kỹ thuật] mã NV xuất hiện trong lời dặn KPI chuẩn đang dùng (ví dụ minh hoạ trong lời dặn — không phải mã gắn cứng của workflow)
_F_CHUAN = [G.parent.parent / "kpi_capitol" / ten for ten in ("khoi_dung_san.json", "workflow_kpi.py")]   # hai nơi chứa lời dặn KPI chuẩn (bản đang dùng)
VI_DU_LOI_DAN_CHUAN = set(m for f in _F_CHUAN if f.is_file() for m in re.findall(r"(?<![A-Za-z0-9])NV\d{2}(?![A-Za-z0-9])", f.read_text(encoding="utf-8")))


def sha_payload(pl) -> str:
    """sha256 của payload chuẩn hoá (sort_keys) — máy chủ tính đúng hàm này trên payload đọc lúc chạy."""
    return hashlib.sha256(json.dumps(pl, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def ban_moi_nhat(dieu_kien: str) -> list[dict]:
    return X.sql(f"""select coalesce(json_agg(x), '[]') from (select distinct on (w.id) w.id, w.name, v.version_number as so, v.payload, w.created_at
        from workflow w join workflow_version v on v.workflow_id = w.id
        where w.org_id::text = '{ORG}' and not coalesce(w.is_deleted, false) and v.version_type::text <> 'RUN' and {dieu_kien}
        order by w.id, v.version_number desc) x""") or []


def ly_kho(pl) -> list[str]:
    """Mọi khối Agent bật tìm tài liệu: ô kho không rỗng và mọi tên kho có trong sổ kho của tổ chức, chưa ẩn (cùng luật may_chu._kiem_kho_rag). Không đọc được sổ ⇒ coi là lỗi (đóng)."""
    try:
        import kho_tai_lieu as KTL
        so = KTL.So(_STATE_DIR / "kho_tai_lieu")
        loi = []
        for n in (pl or {}).get("nodes") or []:
            st = (n.get("data") or {}).get("struct") or {}
            o = {q.get("field_id"): q.get("value") for q in st.get("params") or []}
            if st.get("node_id") != "agent_node" or not (o.get("tool_rag_search") is True or str(o.get("tool_rag_search")).strip().lower() in ("true", "1", "yes", "on")):
                continue
            cols = [x.strip() for x in str(o.get("rag_collections") or "").splitlines() if x.strip()]
            if not so.kho_hop_le(ORG, cols)[0]:
                loi.append("khối tìm tài liệu dùng kho không còn trong tổ chức" if cols else "khối tìm tài liệu chưa chọn kho nào")
        return loi
    except Exception as e:  # noqa: BLE001
        return [f"không kiểm được kho tài liệu ({type(e).__name__})"]


def dung() -> tuple[list[dict], list[str]]:
    the = json.loads((G.parent / "the_wf.json").read_text(encoding="utf-8"))["the"] +\
        json.loads((G.parent / "the_dac_thu.json").read_text(encoding="utf-8"))["the"]
    ra, loi, bo = [], [], []
    _PL = {}   # mã thẻ ⇒ payload (để kiểm luật KPI ở cuối, SAU khi đã tính mức tối đa)
    TLC = TL.TLC   # [ghi chú kỹ thuật] thẻ hỏi trên tài liệu của khung chat — ghim theo tên workflow cố định; chưa tạo workflow ⇒ "thẻ chưa mở"
    if not ban_moi_nhat("w.name = '" + TLC.TEN_WORKFLOW.replace("'", "''") + "'"):
        print("  (bỏ, chưa tạo workflow) " + TLC.MA_THE + ": chạy va_tai_lieu_chat/tao_workflow.py trước")
    else:
        the = the + [dict(TLC.THE_TAI_LIEU, ten_tim=TLC.TEN_WORKFLOW, ten_hien="Hỏi trên tài liệu đang mở", chay_tu_chat=True, nguon="tai_lieu_chat")]
    if not ban_moi_nhat("w.name = '" + TLC.TEN_WF_TINH.replace("'", "''") + "'"):   # [ghi chú kỹ thuật]
        print("  (bỏ, chưa tạo workflow) " + TLC.MA_THE_TINH + ": chạy va_tinh_tren_bang/tao_workflow.py trước")
    else:
        the = the + [dict(TLC.THE_TINH, ten_wf=TLC.TEN_WF_TINH, ten_hien="Tính toán trên bảng đang mở", chay_tu_chat=True, nguon="tinh_tren_bang")]
    for t in the:
        if not t.get("gui_buoc_chon", True):
            continue
        if t["ma"].startswith("wf_"):
            ds = ban_moi_nhat(f"w.id::text like '{t['ma'][3:]}%'")
        else:
            ten = (t.get("ten_wf") or t["ten_tim"]).replace("'", "''")   # [ghi chú kỹ thuật] thẻ nền của khung chat trỏ mẫu KHÔNG có khối Người duyệt
            ds = sorted(ban_moi_nhat(f"w.name = '{ten}'" if t["ma"] == TL.TLC.MA_THE or t.get("ten_wf") else f"w.name like '{ten}%'"), key=lambda w: w["created_at"], reverse=True)[:1]   # thẻ chung: bản tạo MỚI NHẤT cùng tên
        if len(ds) != 1:
            (loi if t.get("chay_tu_chat") else bo).append(f"{t['ma']}: thấy {len(ds)} workflow ⇒ không ghim"); continue   # thẻ chưa mở ⇒ chỉ ghi để biết
        w = ds[0]; pl = w["payload"] if isinstance(w["payload"], dict) else json.loads(w["payload"])
        muc, thieu = MT.toi_da(pl)
        _, ly_hinh = TL.ra_ngoai_hop_le(pl, TL.CACH_NAP.get(t["ma"]))   # [ghi chú kỹ thuật] mẫu nhiều ô / 0 ô theo cách nạp khai trong tro_ly.CACH_NAP
        ly_hinh = list(ly_hinh) + ly_kho(pl)   # [ghi chú kỹ thuật] kho tài liệu không hợp lệ ⇒ thẻ không mở
        if t['ma'] == TL.TLC.MA_THE: ly_hinh = ly_hinh + TL.TLC.kiem_workflow(pl)   # [ghi chú kỹ thuật] thẻ hỏi tài liệu: nội dung workflow phải ĐÚNG bản chuẩn, lệch ⇒ đóng
        _PL[t['ma']] = pl
        # [ghi chú kỹ thuật] mã NV GẮN CỨNG ⇒ thẻ chỉ được gửi định tuyến khi câu (hoặc lịch sử) có đúng mã đó. Chỉ tính TÊN workflow + LỜI DẶN khối AI
        # (ô nhập chữ bị câu người dùng THAY lúc chạy ⇒ không tính); mã mẫu chung (≥ ½ số thẻ — ví dụ trong lời dặn mẫu) bỏ ở bước sau.
        chu_ai = " ".join(json.dumps(n, ensure_ascii=False) for n in pl.get("nodes", [])
                          if ((n.get("data") or {}).get("struct") or {}).get("node_id") != "text_input_node")
        # [ghi chú kỹ thuật] mã trong TÊN workflow luôn tính là gắn cứng; mã trong lời dặn khối AI thì trừ đi các mã mà LỜI DẶN KPI CHUẨN (khoi_dung_san.json nhóm kỹ thuật)
        # dùng làm VÍ DỤ — lời dặn KPI bản 2 (19/11) lấy "NV03" làm ví dụ ⇒ 5 thẻ KPI chung bị coi nhầm là "chỉ dành cho NV03" (luật ≥ ½ số thẻ không còn bắt
        # được vì 28 thẻ riêng còn lời dặn bản cũ). Mã ví dụ của lời dặn chuẩn + tên workflow có mã ⇒ vẫn gắn cứng (lấy từ tên).
        _nv = lambda chu: set(re.findall(r"(?<![A-Za-z0-9])NV\d{2}(?![A-Za-z0-9])", chu))   # noqa: E731
        ma_nv = sorted(_nv(t.get("ten_goc") or t["ten_tim"]) | (_nv(chu_ai) - VI_DU_LOI_DAN_CHUAN))   # không qua rào hình ⇒ KHÔNG mở (người dùng sẽ được hỏi lại / mở trên xưởng), ghi lý do để biết
        ra.append({"ma": t["ma"], "uuid": w["id"], "ten": t.get("ten_hien") or t["ten_tim"], "so": w["so"], "sha": sha_payload(pl),
                   "muc": None if thieu else round(muc, 4), "mo": bool(t.get("chay_tu_chat")) and not ly_hinh, "ly_do_khong_mo": "; ".join(ly_hinh)[:200],
                   "ma_nv_co_dinh": ma_nv, **({"cach_nap": TL.CACH_NAP[t["ma"]]} if t["ma"] in TL.CACH_NAP else {}),
                   "xuat_file": bool(t.get("xuat_file")),
                   "gui_thay_nguoi_dung": t.get("gui_thay_nguoi_dung", t.get("nguon") == "dung_the.py")})
    for x in bo:
        print('  (bỏ, thẻ chưa mở)', x)
    from collections import Counter
    dem = Counter(m for x in ra for m in x["ma_nv_co_dinh"])
    mau = {m for m, k in dem.items() if k >= len(ra) / 2}   # mã xuất hiện ở ≥ ½ thẻ = ví dụ của lời dặn mẫu chung, không phải gắn cứng
    for x in ra:
        x["ma_nv_co_dinh"] = [m for m in x["ma_nv_co_dinh"] if m not in mau]
    if mau:
        print("  (mã mẫu chung, không tính gắn cứng:", ", ".join(sorted(mau)) + ")")
    try:   # đặt biến môi trường sổ thử chỉ để NẠP hoi_kpi (kiểm không ghi sổ, không giữ chỗ); làm SAU mọi phép tính mức ở trên
        os.environ.setdefault("BUOI_S5", "00_00/s5"); os.environ.setdefault("TRAN_USD", "1.00")
        sys.path.append(str(G.parent.parent / "kpi_capitol"))
        import hoi_kpi as _H
        import khoi_kpi as _KK
        for x in ra:
            l = _KK.kiem(_H, ORG, _PL[x["ma"]], True) if x["mo"] else []
            if l:
                x["mo"], x["ly_do_khong_mo"] = False, ("luật KPI lúc chạy: " + l[0])[:200]
    except Exception as e:  # noqa: BLE001 — không kiểm được ⇒ KHÔNG ghi (đóng khi không chắc)
        loi.append(f"không kiểm được luật KPI lúc ghim ({type(e).__name__}: {str(e)[:80]})")
    return ra, loi


if __name__ == "__main__":
    ra, loi = dung()
    print(f"{len(ra)} thẻ ghim · mở {sum(x['mo'] for x in ra)} · tự chạy được (mở, < {J.NGUONG_TU_CHAY}) {sum(x['mo'] and x['muc'] is not None and x['muc'] < J.NGUONG_TU_CHAY for x in ra)}")
    for x in ra:
        kq = ("rào hình" if x.get("ly_do_khong_mo") else "chưa mở") if not x["mo"] else ("thiếu mức" if x["muc"] is None else ("TỰ CHẠY" if x["muc"] < J.NGUONG_TU_CHAY else "hỏi tiền"))
        print(f"  {x['ma']:26s} {x['uuid'][:8]} v{x['so']:<3} {str(x['muc']):7s} {kq:9s} {'xuất file' if x['xuat_file'] else '':9s} {x['ten'][:50]}"
              + (f"  [mã NV cố định: {', '.join(x['ma_nv_co_dinh'])}]" if x["ma_nv_co_dinh"] else ""))
    for x in loi:
        print("  LỖI", x)
    if os.environ.get("GHI") == "co":
        if loi:
            print("KHÔNG ghi — còn lỗi"); sys.exit(1)
        RA.write_text(json.dumps({"ghi_chu": "C 18/11 — sinh bằng gan/ghim.py; KHÔNG sửa tay. sha = sha256(json sort_keys) của payload bản đã lưu mới nhất.",
                                  "the": ra}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("GHI", RA)
