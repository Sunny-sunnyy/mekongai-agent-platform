"""[ghi chú kỹ thuật] QUÉT bộ thẻ khung chat bằng CHÍNH các luật đường Chạy của trang dùng — chỉ đọc, 0 đồng.
  python3 -I chay_thu/va_mot_o_chat/gan/quet_the.py          # quét the_ghim.json ĐANG DÙNG (chạy sau mỗi lần bật lại trang / đổi lời dặn KPI / lưu lại mẫu)
  python3 -I chay_thu/va_mot_o_chat/gan/quet_the.py moi      # quét bộ thẻ ỨNG VIÊN (ghim.dung() — chưa ghi) trước khi GHI=co ghim.py
Mỗi thẻ: (1) bản đã lưu mới nhất còn đúng mã băm đã ghim không (chế độ mặc định); (2) luật KPI lúc chạy — khoi_kpi.kiem(…, luc_chay=True), hàm may_chu.chay gọi;
(3) kho tài liệu của khối tìm tài liệu có trong sổ kho của tổ chức không (cùng luật may_chu._kiem_kho_rag). Thẻ MỞ mà có lỗi ⇒ in "HỎNG" + mã thoát 1
(người dùng hỏi trúng thẻ đó sẽ bị từ chối). Thẻ ĐÓNG có lỗi ⇒ chỉ in. Luật đọc từ file TRÊN ĐĨA — máy chủ trang dùng bản nó nạp lúc bật (bật lại sau khi đổi luật)."""
import json
import os
import sys
from pathlib import Path

G = Path(__file__).resolve().parent
CT = G.parent.parent
sys.path[:0] = [str(G), str(G.parent), str(CT / "giao_dien"), str(CT / "kpi_capitol")]
import ghim as GH  # noqa: E402
import kho_tai_lieu as KTL  # noqa: E402

MOI = len(sys.argv) > 1 and sys.argv[1] == "moi"
if MOI:
    the, loi_ghim = GH.dung()          # TRƯỚC khi đặt biến môi trường sổ thử (mức tối đa không bị ảnh hưởng)
else:
    the, loi_ghim = json.loads((G / "the_ghim.json").read_text(encoding="utf-8"))["the"], []
os.environ.setdefault("BUOI_S5", "00_00/s5"); os.environ.setdefault("TRAN_USD", "1.00")   # hoi_kpi cần hai biến này để nạp; quét KHÔNG ghi sổ, không giữ chỗ
import hoi_kpi as H  # noqa: E402
import khoi_kpi  # noqa: E402

_STATE_DIR = Path(os.environ.get("CAPITOL_STATE_DIR") or (Path.home() / ".local" / "share" / "capitol-platform")).expanduser()
SO = KTL.So(_STATE_DIR / "kho_tai_lieu")


def kho_loi(pl) -> list[str]:
    loi = []
    for n in (pl or {}).get("nodes") or []:
        st = (n.get("data") or {}).get("struct") or {}
        o = {q.get("field_id"): q.get("value") for q in st.get("params") or []}
        if st.get("node_id") == "agent_node" and (o.get("tool_rag_search") is True or str(o.get("tool_rag_search")).strip().lower() in ("true", "1", "yes", "on")):
            cols = [x.strip() for x in str(o.get("rag_collections") or "").splitlines() if x.strip()]
            if not SO.kho_hop_le(GH.ORG, cols)[0]:
                loi.append("kho tài liệu: kho của khối tìm tài liệu không có trong sổ kho của tổ chức" if cols else "kho tài liệu: chưa chọn kho")
    return loi


hong = canh = 0
print(f"quét {len(the)} thẻ ({'ỨNG VIÊN — chưa ghi' if MOI else 'the_ghim.json đang dùng'}) · luật trên đĩa")
for t in the:
    ds = GH.ban_moi_nhat(f"w.id::text = '{t['uuid']}'")
    if not ds:
        loi = ["không đọc được bản đã lưu"]
    else:
        pl = ds[0]["payload"] if isinstance(ds[0]["payload"], dict) else json.loads(ds[0]["payload"])
        loi = ([] if MOI or GH.sha_payload(pl) == t["sha"] else [f"bản đã lưu (số {ds[0]['so']}) KHÁC mã băm đã ghim — khung chat sẽ không tự chạy thẻ này"])
        try:
            loi += ["luật KPI lúc chạy: " + x for x in khoi_kpi.kiem(H, GH.ORG, pl, True)]
        except Exception as e:  # noqa: BLE001
            loi.append(f"không chạy được phép kiểm luật KPI ({type(e).__name__}: {str(e)[:80]})")
        loi += kho_loi(pl)
    if loi:
        hong += bool(t["mo"]); canh += not t["mo"]
        print(f"  {'HỎNG' if t['mo'] else 'đóng'}  {t['ma']:28s} {t['uuid'][:8]}  {len(loi)} lỗi · {loi[0][:150]}")
for x in loi_ghim:
    print("  LỖI GHIM", x)
print(f"KẾT QUẢ: {len(the)} thẻ · mở {sum(bool(t['mo']) for t in the)} · thẻ MỞ có lỗi: {hong} · thẻ đóng có lỗi: {canh}")
print("QUET_THE: ĐẠT" if hong == 0 and not loi_ghim else "QUET_THE: HỎNG — thẻ mở có lỗi sẽ bị từ chối khi người dùng hỏi trúng")
sys.exit(0 if hong == 0 and not loi_ghim else 1)
