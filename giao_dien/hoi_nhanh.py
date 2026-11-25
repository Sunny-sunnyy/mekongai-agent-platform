"""CHẾ ĐỘ "HỎI NHANH" của trang Hỏi KPI (đối chiếu kỹ thuật). BẢN SOẠN, CHƯA GẮN.

Vì sao: đo thật 14:53–14:58 đường "Trợ lý chat đề nghị ⇒ trang chạy": an toàn + tiền đạt nhưng chỉ 1 / 4 câu hợp lệ ra đúng số, 20–47 giây, ~0,004 USD
cho riêng lượt định tuyến. Ở đây KHÔNG có lượt AI định tuyến: câu hỏi ⇒ CODE chọn mẫu ⇒ chạy bằng đúng hàm chay() của nút Chạy.
  1. chọn workflow mẫu theo TÊN (bắt đầu bằng TEN_MAU) trong tổ chức hiện tại — không ghim mã (DynamoDB trên RAM ⇒ mỗi lần bật lại là mã mới);
     bản mới nhất QUA: chat_chay.du_dieu_kien (hình Ô nhập ⇒ Trợ lý KPI ⇒ Rào code ⇒ Kết quả, không Người duyệt / xuất file) + có mức tiền + luật lúc Chạy;
     không thấy / không qua ⇒ nói rõ lý do, KHÔNG chạy bản khác;
  2. chay(mã, {ô nhập: NGUYÊN VĂN câu hỏi}) — hàm đó tự kiểm luật, giữ chỗ tiền theo mức tối đa của workflow, trần lượt, ghi sổ;
  3. trang hỏi trạng thái theo mã phiên; lượt xong ⇒ trả câu trả lời ĐÃ QUA Rào + nhãn hằng "tạm tính" + BẢNG SỐ do lõi dựng (gói `bang_thuong`
     của khối Rào chế độ code) + kết quả kiểm; lượt bị chặn / lỗi / quá giờ ⇒ KHÔNG trả số, trả câu báo lời thường theo loại.
Mỗi câu hỏi là MỘT lượt riêng (workflow không nhận lịch sử trò chuyện). Một lượt / lúc.

BẢNG ĐƯỜNG TỐN TIỀN (gửi D):
  đường                     | giữ chỗ                                   | thử lại                                  | song song
  lượt workflow "hỏi nhanh" | chay(): muc_toi_da.toi_da(payload) (≈0,075) | thử lại trong khối đã nằm trong mức khối; | _khoa + sổ PHIEN: 1 lượt / lúc; trần lượt của
                            | thiếu mức ⇒ không chạy                    | bat_dau KHÔNG gọi chay lần hai            | trang (TRAN_LUOT) áp chung với bấm tay
  lượt AI định tuyến        | KHÔNG CÓ                                  | —                                        | —
  hỏi trạng thái (GET)      | 0 đồng (chỉ đọc PG)                       | trang hỏi lại mỗi ~1,5 giây              | —
"""
from __future__ import annotations

import re
import threading
import time

import chat_chay

TEN_MAU = "KPI hỏi nhanh"          # B lưu: "KPI hỏi nhanh — lương kinh doanh, thưởng KPI (không duyệt, không xuất file)"
GIAY_TOI_DA = 120                   # quá mức này mà lượt chưa xong ⇒ coi là quá giờ (lượt KPI đo ≈ 10–20 giây)
CAU_TOI_DA = 2000
NHAN_TAM_TINH = chat_chay.NHAN_TAM_TINH
CAU_BI_CHAN = ("Trợ lý chưa đưa ra được câu trả lời qua bước kiểm số, nên trang không hiện câu trả lời (trong câu có con số không có nguồn, hoặc không thuộc "
               "đúng nhân viên / đúng KPI được hỏi). Lý do chi tiết xem ở lượt trên xưởng. "
               # [ghi chú kỹ thuật] chặn oan thất thường của kiem_so@17 với câu "dữ liệu cần để ý"
               # — GỠ câu dưới khi mốc 5 mekongai đo đạt
               "Nếu bạn hỏi về dữ liệu cần để ý / cờ dữ liệu: loại câu này hiện đôi lúc bị bước kiểm chặn nhầm và đang được sửa — hỏi lại một lần là thường qua.")
TRUOC_TOI_DA, TRUOC_KY_TU = 3, 300   # [ghi chú kỹ thuật] chỉ nhớ CÂU HỎI trước của người dùng — không câu trả lời, không kết quả công cụ
TRAN_PHIEN = 200                    # [ghi chú kỹ thuật] sổ PHIEN trong bộ nhớ không lớn mãi: quá mức thì bỏ các phiên cũ nhất đã xong
XONG, HONG, DUNG = ("SUCCESS",), ("FAILED", "CANCELLED", "STOPPED"), ("PAUSED", "paused")
_MA = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PHIEN: dict[str, dict] = {}        # mã phiên ⇒ {cau, wf, ten, so, t0, xong} — chỉ câu hỏi tạo từ trang, từ lúc may_chu bật
_khoa = threading.Lock()


def _la_kpi(payload) -> bool:
    """Chỉ nhận workflow mà khối Trợ lý bật công cụ KPI (du_dieu_kien đã bảo đảm đúng 1 Trợ lý + Rào khớp chế độ)."""
    for n in (payload or {}).get("nodes") or []:
        st = chat_chay._struct(n)
        if st.get("node_id") == "agent_node":
            return chat_chay._bat(chat_chay._gia_tri(st, "tool_kpi"))
    return False


def chon_mau(ds, pb, toi_da, luat) -> tuple[dict | None, str]:
    """({"id", "ten", "so", "o_nhap"} | None, lý do không có). ds: [{id, ten}] của tổ chức, MỚI NHẤT TRƯỚC. Lấy bản ĐẦU TIÊN trùng tên mẫu mà qua mọi phép
    kiểm; bản không qua thì ghi lý do rồi xét bản kế (bản lưu ở mốc dữ liệu cũ nằm dưới bản mới là chuyện thường)."""
    ly_do = ""
    for w in ds or []:
        if not str(w.get("ten") or "").startswith(TEN_MAU) or not _MA.fullmatch(str(w.get("id") or "")):
            continue
        try:
            v = pb(w["id"])
            if not v:
                ly_do = ly_do or "không đọc được phiên bản đã lưu của mẫu"
                continue
            o, ld = chat_chay.du_dieu_kien(v.get("payload"))
            if ld or not o:
                ly_do = ly_do or ("mẫu không đúng hình chạy nhanh: " + "; ".join(ld)[:300])
                continue
            if not _la_kpi(v["payload"]):
                ly_do = ly_do or "mẫu không dùng công cụ KPI"
                continue
            if toi_da(v["payload"])[1]:
                ly_do = ly_do or "mẫu có khối chưa có mức tiền tối đa"
                continue
            loi = luat(v["payload"])
            if loi:
                ly_do = ly_do or ("mẫu đã cũ so với dữ liệu KPI đang bật — cần lưu lại mẫu: " + " ".join(str(x) for x in loi)[:300])
                continue
            return {"id": w["id"], "ten": str(w.get("ten") or "")[:160], "so": v.get("so"), "o_nhap": o}, ""
        except Exception as e:  # noqa: BLE001 — không kiểm được ⇒ không dùng bản đó
            ly_do = ly_do or f"không kiểm được mẫu ({type(e).__name__})"
    return None, ly_do or f"chưa có workflow mẫu nào tên bắt đầu bằng “{TEN_MAU}” trong xưởng — cần lưu mẫu trước"


def _dang_chay(gio=time.time) -> str | None:
    for s, x in PHIEN.items():
        if not x.get("xong") and gio() - x["t0"] < GIAY_TOI_DA:
            return s
    return None


def sach_truoc(truoc) -> list[str] | None:
    """[ghi chú kỹ thuật] Danh sách câu hỏi trước do TRANG gửi ⇒ danh sách đã làm sạch (cũ ⇒ mới); None = sai hình (người gọi trả 400).
    Không tin trang: phải là list ≤ TRUOC_TOI_DA phần tử chữ; bỏ ký tự điều khiển / xuống dòng, gom khoảng trắng, cắt TRUOC_KY_TU; rỗng ⇒ bỏ."""
    if truoc is None:
        return []
    if not isinstance(truoc, list) or len(truoc) > TRUOC_TOI_DA or not all(isinstance(x, str) for x in truoc):
        return None
    ra = []
    for x in truoc:
        x = " ".join("".join(c if c.isprintable() else " " for c in x).split())[:TRUOC_KY_TU].strip()
        if x:
            ra.append(x)
    return ra


def ghep(cau: str, truoc: list[str]) -> str:
    """Chữ đưa vào Ô nhập. Không có câu trước ⇒ NGUYÊN VĂN câu hỏi (đường cũ). Có ⇒ khung cố định do CODE viết (KHÔNG chữ số) + các câu hỏi trước
    (gạch đầu dòng) + câu hiện tại. Tổng > CAU_TOI_DA ⇒ bỏ dần câu CŨ NHẤT (câu hiện tại luôn nguyên)."""
    truoc = list(truoc)
    while truoc and len(_ghep(cau, truoc)) > CAU_TOI_DA:
        truoc.pop(0)
    return _ghep(cau, truoc)


def _ghep(cau: str, truoc: list[str]) -> str:
    if not truoc:
        return cau
    return ("Các câu người dùng đã hỏi trước trong cuộc trò chuyện này (chỉ để hiểu \"anh ấy\", \"KPI đó\", \"còn…\" đang chỉ ai / cái gì; "
            "KHÔNG trả lời lại các câu này):\n" + "\n".join("- " + x for x in truoc)
            + "\nCâu hỏi cần trả lời bây giờ:\n" + cau)


def bat_dau(cau, *, ds, pb, toi_da, luat, chay, ghi=lambda s: None, gio=time.time, truoc=None) -> tuple[int, dict]:
    """Bắt đầu MỘT lượt hỏi nhanh. ds() ⇒ [{id, ten}] · pb(mã) ⇒ {"payload", "so"} · toi_da(payload) ⇒ (mức, thiếu) · luat(payload) ⇒ lỗi luật lúc Chạy
    · chay(mã, text_inputs) ⇒ (http, {"session", "version"} | {"loi"}) — hàm chay() của may_chu. Trả (http, dict) cho trang."""
    if not isinstance(cau, str) or not 1 <= len(cau.strip()) <= CAU_TOI_DA:
        return 400, {"loi": f"Câu hỏi phải có 1–{CAU_TOI_DA} ký tự."}
    truoc = sach_truoc(truoc)   # [ghi chú kỹ thuật] kiểm TRƯỚC khi chọn mẫu / giữ chỗ
    if truoc is None:
        return 400, {"loi": f"'truoc' phải là danh sách tối đa {TRUOC_TOI_DA} câu hỏi trước (chữ)."}
    with _khoa:
        s0 = _dang_chay(gio)
        if s0:
            return 409, {"loi": "Đang có một câu hỏi nhanh đang chạy — chờ câu đó xong rồi hỏi tiếp.", "session": s0}
        try:
            mau, ly_do = chon_mau(ds(), pb, toi_da, luat)
        except Exception as e:  # noqa: BLE001
            mau, ly_do = None, f"không đọc được danh sách workflow ({type(e).__name__})"
        if not mau:
            ghi(f"HỎI NHANH không chạy — {ly_do[:200]}")
            return 503, {"loi": "Chưa hỏi nhanh được: " + ly_do + ". Dùng chế độ Chính thức hoặc báo người vận hành.", "loai": "khong_co_mau"}
        try:
            code, r = chay(mau["id"], {mau["o_nhap"]: ghep(cau.strip(), truoc)})
        except Exception as e:  # noqa: BLE001 — [ghi chú kỹ thuật] lượt CÓ THỂ đã khởi động (chay() tự giao tiền cho luồng theo dõi của nó); không có mã phiên để theo
            ghi(f"HỎI NHANH {mau['id']}: chay() ném {type(e).__name__} — lượt có thể đã khởi động, xem danh sách lượt trên xưởng")
            return 502, {"loi": "Không bắt đầu được lượt chạy (lỗi kết nối tới bộ chạy). Xem danh sách lượt trên xưởng trước khi hỏi lại.", "loai": "loi_chay"}
        if code != 200 or not isinstance(r, dict) or not _MA.fullmatch(str(r.get("session") or "")):
            return (code if isinstance(code, int) and 400 <= code < 600 else 502), {
                "loi": str((r or {}).get("loi") if isinstance(r, dict) else "")[:600] or f"Trang từ chối chạy (HTTP {code}).", "loai": "tu_choi_chay"}
        s = r["session"]
        # [cùng ý D Đ3 của chat-chạy] chay() tự đọc phiên bản mới nhất lần nữa: khác bản đã kiểm ⇒ lượt này KHÔNG được trả số
        PHIEN[s] = {"cau": cau.strip(), "wf": mau["id"], "ten": mau["ten"], "so": mau["so"], "t0": gio(),
                    "lech_pb": mau["so"] is None or r.get("version") != mau["so"]}
        for cu in [k for k, v in PHIEN.items() if v.get("xong")][:max(0, len(PHIEN) - TRAN_PHIEN)]:
            PHIEN.pop(cu, None)
        ghi(f"HỎI NHANH {mau['id']} (phiên bản {mau['so']}) ⇒ phiên {s} (kèm {len(truoc)} câu hỏi trước)")
        return 200, {"session": s, "workflow": mau["id"], "ten": mau["ten"]}


TRAN_DONG, TRAN_COT, TRAN_BANG = 50, 12, 4   # trần của hình "view" (thống nhất với trang :5183 bên mekongai — đã chuyển 15:05 12/11)


def _bang(goi: dict) -> dict | None:
    """Bảng số từ gói của khối Rào chế độ code (do LÕI dựng, không phải chữ AI), theo HÌNH CHUNG "view" với trang :5183:
      {"facts": [{"key", "label", "value"}], "tables": [{"key", "label", "columns": [{"key", "label"}], "rows", "total_rows"}], "notes": [str]}
    (≤ 50 dòng / 12 cột / 4 bảng; total_rows = số dòng thật trước khi cắt). [ghi chú kỹ thuật] key = KHOÁ KỸ THUẬT (trang tự thêm
    đơn vị, đổi mã ⇒ tên), label = nhãn tiếng Việt, value / ô giữ KIỂU GỐC (số là số). Sai hình ⇒ None."""
    b = goi.get("bang_thuong")
    if not isinstance(b, dict) or not isinstance(b.get("salary"), dict):
        return None
    so = lambda x: x if isinstance(x, (int, float)) and not isinstance(x, bool) else None   # noqa: E731
    nv = [[str(k)[:40], so(v)] for k, v in b["salary"].items()]
    kpi = [[str(k)[:60], so(v)] for k, v in b["kpi_total"].items()] if isinstance(b.get("kpi_total"), dict) else []
    cot = lambda k, l: {"key": k, "label": l}   # noqa: E731
    bang = [{"key": "salary_by_employee", "label": "Lương kinh doanh (8 KPI) theo nhân viên — không gồm thưởng thêm", "columns": [cot("employee_id", "Mã nhân viên"), cot("amount", "Lương kinh doanh (đ)")],
             "rows": nv[:TRAN_DONG], "total_rows": len(nv)}]
    if kpi:
        bang.append({"key": "total_by_kpi", "label": "Tiền theo từng KPI — cả đội", "columns": [cot("kpi", "KPI"), cot("amount", "Tiền KPI cả đội (đ)")],
                     "rows": kpi[:TRAN_DONG], "total_rows": len(kpi)})
    # [ghi chú kỹ thuật] ngày chụp số liệu: dòng nguồn [nhãn, giá trị] của gói (đường RAW: ngày đơn mới nhất ≤ ngày chốt); sai hình ⇒ không có ô
    nguon = b.get("source") if isinstance(b.get("source"), list) else []
    chup = next((str(r[1])[:10] for r in nguon if isinstance(r, (list, tuple)) and len(r) == 2 and str(r[0]).startswith("Lúc chụp số liệu")
                 and re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ].*)?", str(r[1]))), None)
    return {"facts": [{"key": "period", "label": "Kỳ", "value": str(b.get("period") or "")[:20]},
                      {"key": "cutoff", "label": "Dữ liệu tới ngày", "value": str(b.get("cutoff") or "")[:20]},
                      *([{"key": "captured_at", "label": "Đơn hàng mới nhất trong số liệu", "value": chup}] if chup else []),
                      {"key": "team_total", "label": "Tổng lương kinh doanh cả đội (đ)", "value": so(b.get("team_total"))},
                      {"key": "table_sha256", "label": "Mã bảng", "value": str(goi.get("sha256_bang") or "")[:12]}],
            "tables": bang[:TRAN_BANG],
            "notes": ["Bảng do phần tính thưởng dựng bằng code, không phải chữ AI viết.", NHAN_TAM_TINH]}


def trang_thai(s, *, tt_luot, ket_qua, pb, ly_do_chan, khoi_ra, gio=time.time) -> tuple[int, dict]:
    """Trạng thái một lượt hỏi nhanh. tt_luot(phiên) ⇒ chữ trạng thái | None · ket_qua(phiên) ⇒ {mã khối: {"kieu", "v"}} · pb(mã) ⇒ {"so", "payload"}
    · ly_do_chan(phiên) ⇒ lý do khối kiểm số chặn (chữ) | None · khoi_ra(payload) ⇒ mã khối Kết quả. KHÔNG ném; chỉ đọc."""
    if not isinstance(s, str) or not _MA.fullmatch(s) or s not in PHIEN:
        return 404, {"loi": "Không có phiên hỏi nhanh này (trang chỉ theo dõi câu hỏi tạo từ trang, từ lúc máy chủ bật)."}
    x = PHIEN[s]
    chung = {"session": s, "cau": x["cau"], "workflow": x["wf"], "ten": x["ten"], "giay": round(gio() - x["t0"], 1)}

    def het(loai, chu, tt=None):   # kết thúc KHÔNG có số
        x["xong"] = True
        return 200, dict(chung, status=tt, ket_thuc=True, tra_loi=None, nhan=None, view=None, kiem=None, loi={"loai": loai, "chu": chu})

    try:
        tt = tt_luot(s)
        if tt in DUNG:
            return het("cho_nguoi", "Lượt đang dừng chờ người — mở lượt trên xưởng.", tt)
        if tt in HONG:
            ld = ly_do_chan(s)
            if ld:
                # [ghi chú kỹ thuật] KHÔNG chép chữ lý do ra trang: lý do của dịch vụ kiểm số thường nêu chính con số bị chặn (số bịa / số của nhân viên khác)
                # ⇒ "bị chặn thì không hiện số bị chặn". Câu CỐ ĐỊNH; chi tiết xem ở lượt trên xưởng.
                return het("bi_chan", CAU_BI_CHAN, tt)
            return het("loi_chay", "Lượt chạy bị dừng giữa chừng — mở lượt trên xưởng để xem lý do.", tt)
        if tt not in XONG:
            if gio() - x["t0"] >= GIAY_TOI_DA:
                return het("qua_gio", f"Quá {GIAY_TOI_DA} giây mà lượt chưa xong — mở lượt trên xưởng để xem tiếp.", tt)
            return 200, dict(chung, status=tt, ket_thuc=False)
        if x.get("lech_pb") or (pb(x["wf"]) or {}).get("so") != x["so"]:
            return het("doi_phien_ban", "Mẫu vừa được lưu phiên bản mới trong lúc chạy — hỏi lại câu này.", tt)
        v = pb(x["wf"])
        o = (ket_qua(s) or {}).get(khoi_ra(v["payload"])) or {}
        goi = o.get("v") if o.get("kieu") == "kpi" and isinstance(o.get("v"), dict) else None
        chu = goi.get("cau_tra_loi") if goi else None
        if not (isinstance(chu, str) and chu.strip()) or (goi.get("kiem") or {}).get("dat") is not True:
            # khối Kết quả không mang gói đã kiểm (chữ trơn / gói thiếu dấu đạt) ⇒ đóng: không hiện số
            return het("khong_doc_duoc", "Lượt xong nhưng trang không đọc được câu trả lời đã kiểm ở khối Kết quả — mở lượt trên xưởng.", tt)
        x["xong"] = True
        k = goi.get("kiem") or {}
        return 200, dict(chung, status=tt, ket_thuc=True, tra_loi=chu.strip()[:20000], nhan=NHAN_TAM_TINH, view=_bang(goi),
                         kiem={"dat": True, "ly_do": str(k.get("ly_do") or "")[:300], "phien_ban": str(k.get("phien_ban") or "")[:40]}, loi=None)
    except Exception as e:  # noqa: BLE001 — ĐÓNG KHI LỖI
        return het("loi_doc", f"Không đọc được kết quả ({type(e).__name__}) — mở lượt trên xưởng.")
