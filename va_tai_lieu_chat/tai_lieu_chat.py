"""[ghi chú kỹ thuật] Phần TRANG của đường "đọc thẳng": giữ "file đang mở" theo cuộc trò chuyện + ghép ô nhập
cho khối AI + kiểm sau trả lời. Module THUẦN (không mạng, không AI); tro_ly.py / noi_may_chu.py / may_chu.py gọi vào (bản nối: dung_ban_noi.py).

  KhoFile.mo(org, conv, ten, than)  ⇒ (http, dict cho trang) — kiểm tên (rào của kho_tai_lieu.lam_sach_ten truyền vào), cỡ, tách chữ (doc_tai_lieu.tach_chu);
                                       không đọc được ⇒ 422 kèm câu nói với người dùng (KHÔNG nhận lặng lẽ). Mỗi cuộc MỘT file đang mở (file mới thay file cũ, có nói).
  KhoFile.dang_mo(org, conv)        ⇒ bản ghi | None — CHỈ đúng tổ chức đã tải lên + đúng cuộc trò chuyện + còn hạn 24 giờ.
  KhoFile.dong(org, conv)           ⇒ bỏ file đang mở (nút × trên trang).
  o_nhap(f, cau, lich_su)           ⇒ chữ đưa vào ô nhập của workflow "Hỏi trên tài liệu": TOÀN VĂN (không cắt) ⇒ các lượt trước ⇒ câu hỏi (cuối).
                                       Tài liệu đứng ĐẦU để phần đầu giống hệt giữa các câu hỏi về cùng file (bộ nhớ đệm của nhà cung cấp trúng được).
  hau_kiem(chu, f)                  ⇒ {"ten", "don_vi", "co_dan", "loi": [...]} — doc_tai_lieu.kiem_tra_loi; lỗi ⇒ trang hiện cảnh báo CẠNH câu trả lời.
  THE_TAI_LIEU                      ⇒ thẻ gửi bộ định tuyến, chỉ khi cuộc trò chuyện đang có file mở VÀ workflow "Hỏi trên tài liệu" đã ghim.
Không đặt trần token (người dùng 19/11). Chốt RAM: số file giữ + tổng ký tự giữ — vượt ⇒ bỏ file CŨ NHẤT (người dùng hỏi lại sẽ được báo "hết hạn, kéo lại file")."""
import hashlib
import re
import secrets
from pathlib import Path
import time

import doc_tai_lieu as D

HAN_GIAY = 24 * 3600            # A tạm chọn theo đề xuất: giữ 24 giờ (câu 3 của kế hoạch, người dùng chưa trả lời)
CO_TOI_DA = 20 * 1024 * 1024    # = rào tải lên của trang
SO_FILE_TOI_DA = 40             # số file đang mở giữ trong bộ nhớ máy chủ
KY_TU_GIU_TOI_DA = 20_000_000   # tổng ký tự các file đang mở (~ 60–80 MB RAM) — máy 15 GB
MA_THE = "hoi_tai_lieu"
TEN_WORKFLOW = "Hỏi trên tài liệu — đọc thẳng (khung chat)"   # tên workflow ghim cho thẻ (tao_workflow.py tạo, ghim.py tìm đúng tên này)
CHU = {
    "het_han": "File bạn đưa trước đó đã hết hạn giữ (24 giờ) hoặc máy chủ vừa bật lại. Bạn kéo lại file vào khung chat rồi hỏi nhé.",
    "chua_bat": "Mình đã đọc được file, nhưng phần hỏi đáp trên tài liệu chưa được bật trên máy chủ này. Bạn báo người phụ trách giúp nhé.",
    "ten": "Tên file không hợp lệ hoặc loại file chưa hỗ trợ. Khung chat nhận PDF, Word (.docx), .txt, .md, bảng tính (.xlsx, .xlsm, .xls, .csv), ảnh (.png, .jpg), "
           "trang web (.html) và .json; tên không chứa dấu / \\ ; | < > và không quá 120 ký tự.",
    "co": "File phải từ 1 byte tới 20 MB.",
    # [ghi chú kỹ thuật] đường nạp kho (bản chụp)
    "dang_doc": "File “{ten}” vẫn đang được đọc ({vi}; đã {giay} giây). Bạn hỏi lại sau ít phút nhé.",
    "nap_loi": "Mình chưa đọc được file “{ten}”: {ly_do} Bạn thử kéo lại file sau ít phút nhé.",
    "mat_dau": "Máy chủ vừa bật lại nên việc đọc file bị mất dấu. Bạn kéo lại file vào khung chat giúp mình nhé.",
    "khong_trang": "Mình chưa tìm thấy phần nào của “{ten}” liên quan tới câu hỏi này. Bạn thử hỏi bằng từ khoá cụ thể hơn (tên mục, con số, tên người…) nhé.",
    "kho_khong_co": "Không thấy tài liệu này trong danh sách của bạn (có thể đã bị ẩn). Bạn tải lại trang Dữ liệu rồi thử lại nhé.",   # [ghi chú kỹ thuật]
    "tim_loi": "Mình chưa tìm được trong file lúc này ({ly_do}). Bạn thử hỏi lại sau ít phút nhé.",
    "dich_vu_tat": "File loại này (bản chụp, ảnh, bảng tính, trang web, JSON) phải qua dịch vụ đọc tài liệu — dịch vụ đó hiện chưa bật trên máy chủ. Bạn báo người "
                   "phụ trách giúp nhé; file PDF / Word / chữ có sẵn chữ vẫn hỏi được bình thường.",
}
GIAY_MOI_TRANG_CHUP = 4   # ƯỚC để nói với người dùng "khoảng N phút" — lấy từ báo cáo của mekongai (nhận dạng 0,4–0,5 s + mô hình đọc trang 1,5–2,9 s / trang); chỉnh theo số đo thật
THE_TAI_LIEU = {"ma": MA_THE, "ten_tim": "Hỏi trên tài liệu đang mở",
                "lam_gi": "Trả lời câu hỏi dựa trên TÀI LIỆU người dùng vừa kéo vào khung chat (PDF / Word / chữ / bảng tính / ảnh / trang web / JSON): đọc tài liệu, trả lời kèm chỗ dẫn trang / đoạn.",
                "khi_nao_dung": "Cuộc trò chuyện đang có file mở và câu hỏi là về nội dung file đó, hoặc hỏi tiếp câu trước về file (vd 'tài liệu này nói gì về phí', 'tóm tắt giúp', 'điều 5 quy định gì', 'còn thời hạn thì sao'). Với file bảng tính: TRA CỨU một dòng / một mã / một tên cụ thể trong file "
                                "(vd 'dòng của mặt hàng nước mắm ghi đơn vị gì', 'mã SP014 là sản phẩm gì', 'trong file có mặt hàng dầu ăn không', 'ghi chú của khách A viết gì').",
                "khong_dung_khi": "Câu hỏi về thưởng / KPI / lương của nhân viên; nhờ dựng quy trình mới; hỏi về tài liệu đã nạp vào kho của công ty (không phải file vừa kéo vào).",
                "vi_du": ["tài liệu này nói gì về thời hạn thanh toán", "tóm tắt file giúp tôi", "trong file có nêu mức phạt không"],
                "chay_tu_chat": True, "muc_toi_da_uoc": 0.0, "xuat_file": False, "co_nguoi_duyet": False, "goi_ra_ngoai": False, "gui_thay_nguoi_dung": False, "can_ma_nv": False}


# [ghi chú kỹ thuật] tính trên CẢ bảng bằng code chạy ở máy ảo E2B (không phải tìm vài hàng rồi đọc)
MA_THE_TINH = "tinh_tren_bang"
TEN_WF_TINH = "Tính trên bảng — chạy code (khung chat)"   # tên workflow ghim cho thẻ (va_tinh_tren_bang/tao_workflow.py tạo, ghim.py tìm ĐÚNG tên này)
THE_TINH = {"ma": MA_THE_TINH, "ten_tim": "Tính toán trên bảng đang mở",
            "lam_gi": "TÍNH trên TOÀN BỘ file bảng tính (Excel / CSV) người dùng vừa kéo vào khung chat bằng cách chạy code: cộng, đếm, trung bình, lớn nhất / nhỏ nhất, xếp hạng, lọc, "
                      "nhóm theo cột, so sánh giữa các nhóm, số dòng / số cột / tên sheet.",
            "khi_nao_dung": "Cuộc trò chuyện đang mở một file bảng tính và câu hỏi cần một PHÉP TÍNH hoặc thống kê trên cả bảng (vd 'tổng cột doanh thu', 'trung bình giá theo đơn vị', "
                            "'mặt hàng nào đắt nhất', 'có bao nhiêu dòng', 'đếm theo từng loại').",
            "khong_dung_khi": "Chỉ tra cứu nội dung một dòng / một mục cụ thể hoặc hỏi nội dung văn bản (⇒ Hỏi trên tài liệu đang mở); câu hỏi về thưởng / KPI / lương của nhân viên; nhờ dựng quy trình mới.",
            "vi_du": ["tổng cột gia là bao nhiêu", "trung bình theo từng đơn vị tính", "file có bao nhiêu dòng, mặt hàng nào giá cao nhất"],
            "chay_tu_chat": True, "muc_toi_da_uoc": 0.05, "xuat_file": False, "co_nguoi_duyet": False, "goi_ra_ngoai": True, "gui_thay_nguoi_dung": False, "can_ma_nv": False}
TRANG_HINH_TOI_DA = 200   # [ghi chú kỹ thuật] trần số trang app RAG chịu mô tả hình cho một file (nhóm phát triển xác nhận, từ code app) ⇒ PDF dài hơn KHÔNG chuyển sang kho
# câu hỏi nhắc tới hình vẽ trong tài liệu. KHÔNG bắt chữ "hình" / "ảnh" trần ("tình hình", "mô hình", "hình thức", "ảnh hưởng").
_HOI_HINH = re.compile(r"biểu\s*đồ|đồ\s*thị|sơ\s*đồ|lưu\s*đồ|hình\s*vẽ|hình\s*ảnh|bức\s*ảnh|(?:trong|theo|ở|tại|xem)\s+hình|hình\s*\d|\b(?:chart|figure|diagram)\b", re.I)
_KHONG_DOC_HINH = re.compile(r"(?:biểu\s*đồ|đồ\s*thị|sơ\s*đồ|hình)[^.\n]{0,200}(?:lẫn thứ tự|không đọc được|không xác định|không đoán|không thấy|không có trong phần chữ)"
                             r"|(?:lẫn thứ tự|không đọc được|không xác định|không đoán)[^.\n]{0,200}(?:biểu\s*đồ|đồ\s*thị|sơ\s*đồ|hình)", re.I)
GOI_Y_HINH = "Nếu số bạn cần nằm trong hình vẽ / biểu đồ của file: hỏi lại câu này kèm chữ “biểu đồ” (hoặc “sơ đồ”, “hình vẽ”) — mình sẽ đọc lại cả hình trong file rồi trả lời (mất thêm ít phút)."


def hoi_ve_hinh(cau) -> bool:
    return bool(_HOI_HINH.search(str(cau or "")))


def can_goi_y_hinh(chu) -> bool:
    return bool(_KHONG_DOC_HINH.search(str(chu or "")))


RAW_GIU_TOI_DA = 4   # [ghi chú kỹ thuật] số file bảng tính còn giữ byte gốc cùng lúc (mỗi file ≤ 20 MB) — quá thì bỏ byte của file mở cũ nhất (file đó vẫn hỏi theo đường tìm được)


# [ghi chú kỹ thuật] hỏi tự do trên KHO tài liệu công ty đã nạp sẵn (không kéo file, không chọn tài liệu trước).
MA_THE_KHO = "hoi_kho_tai_lieu"
TEN_THE_KHO = "Hỏi trên kho tài liệu của công ty"
THE_KHO = {"ma": MA_THE_KHO, "ten_tim": TEN_THE_KHO,
           "lam_gi": "Trả lời câu hỏi dựa trên các TÀI LIỆU CỦA CÔNG TY đã nạp sẵn vào kho (quy định, báo cáo, hợp đồng, bài viết…): tự tìm trong mọi tài liệu của kho rồi trả lời kèm chỗ dẫn và tên file.",
           "khi_nao_dung": "Câu hỏi về nội dung tài liệu / văn bản / quy định / báo cáo của công ty khi người dùng KHÔNG kéo file nào vào cuộc trò chuyện (vd 'quy định nghỉ phép thế nào', "
                           "'báo cáo quý 3 nói gì về doanh thu', 'trong tài liệu công ty có nhắc tới … không', 'tìm trong tài liệu giúp tôi …').",
           "khong_dung_khi": "Câu hỏi về thưởng / KPI / lương kinh doanh của nhân viên (đã có các việc KPI); nhờ dựng quy trình mới; hỏi trợ lý làm được gì.",
           "vi_du": ["báo cáo quý 3 nói gì về doanh thu", "quy định của công ty về thời hạn thanh toán", "trong tài liệu có nhắc tới chi phí thương mại không"],
           "chay_tu_chat": True, "muc_toi_da_uoc": 0.0, "xuat_file": False, "co_nguoi_duyet": False, "goi_ra_ngoai": False, "gui_thay_nguoi_dung": False, "can_ma_nv": False}
SO_DOAN_KHO_CHUNG, LO_KHO = 12, 5   # giữ 12 đoạn điểm cao nhất; cổng RAG nhận ≤ 5 kho mỗi lời gọi


# [ghi chú kỹ thuật] Bộ định tuyến chạy qua OpenRouter — NGOÀI ba nơi người dùng cho gửi tài liệu (Bedrock, app RAG, E2B) ⇒ lịch sử gửi bộ định tuyến KHÔNG mang
# tên file, không mang trích đoạn nào của tài liệu: hai mục dưới đây thay cho "Đã đọc <tên>…" và cho câu trả lời trên tài liệu (người dùng vẫn thấy bản đầy đủ).
DT_TRA_LOI = "(Trợ lý đã trả lời một câu hỏi trên tài liệu đang mở trong cuộc trò chuyện.)"
_LOAI_CHU = {"pdf": "PDF", "docx": "Word", "chu": "văn bản chữ", "anh": "ảnh", "bang_tinh": "bảng tính", "trang_web": "trang web", "json": "JSON"}


def dt_mo(r: dict) -> str:
    """Dòng trung tính gửi bộ định tuyến khi người dùng mở file: chỉ LOẠI + số phần, không tên, không nội dung."""
    n = r.get("so_phan")   # [ghi chú kỹ thuật] bảng tính / trang web / JSON đang nạp kho: chưa biết số phần ⇒ không ghi số
    return (f"(Người dùng vừa mở một tài liệu {_LOAI_CHU.get(r.get('loai'), 'chữ')}" + (f" — {n} {r.get('don_vi')}" if n else "")
            + " — trong cuộc trò chuyện; có thể hỏi tiếp về tài liệu đó.)")


MO_HINH_WORKFLOW = "claude-sonnet-5-5"


def kiem_workflow(pl) -> list[str]:
    """[ghi chú kỹ thuật] Thẻ hỏi tài liệu nhận TOÀN VĂN tài liệu người dùng ⇒ lúc GHIM phải chắc workflow đúng là bản mình tạo (tao_workflow.py), không phải bản ai đó
    lưu đè / tạo cùng tên: đúng 3 khối (ô nhập ⇒ agent_node ⇒ markdown), 2 dây đúng chiều, lời dặn == doc_tai_lieu.LOI_DAN từng ký tự, mô hình claude-sonnet-5-5,
    KHÔNG công cụ / MCP / kho tài liệu / file. Trả danh sách lý do lệch (rỗng = đúng). Lệch ⇒ bộ ghim ĐÓNG thẻ kèm lý do."""
    try:
        nut = {n["id"]: (n.get("data") or {}).get("struct") or {} for n in pl.get("nodes") or []}
        loai = {i: st.get("node_id") for i, st in nut.items()}
        ly = []
        if sorted(loai.values()) != sorted(["text_input_node", "agent_node", "markdown_output_node"]):
            return ["workflow hỏi tài liệu phải có đúng 3 khối: ô nhập, Agent, Markdown (đang có: " + ", ".join(sorted(str(v) for v in loai.values()))[:120] + ")"]
        vao, ai, ra = (next(i for i, v in loai.items() if v == k) for k in ("text_input_node", "agent_node", "markdown_output_node"))
        day = sorted((e.get("source"), e.get("target")) for e in pl.get("edges") or [])
        if day != sorted([(vao, ai), (ai, ra)]):
            ly.append("dây nối khác bản chuẩn (phải là ô nhập ⇒ Agent ⇒ Markdown)")
        ps = {q.get("field_id"): q.get("value") for q in nut[ai].get("params") or []}
        if ps.get("system_prompt") != D.LOI_DAN:
            ly.append("lời dặn của khối Agent khác doc_tai_lieu.LOI_DAN")
        if ps.get("model") != MO_HINH_WORKFLOW:
            ly.append("mô hình của khối Agent không phải " + MO_HINH_WORKFLOW)
        bat = lambda v: v is True or str(v).strip().lower() in ("true", "1", "yes", "on")   # noqa: E731
        cong_cu = sorted(k for k, v in ps.items() if isinstance(k, str) and k.startswith("tool_") and bat(v))
        if cong_cu:
            ly.append("khối Agent bật công cụ: " + ", ".join(cong_cu)[:120])
        for k in ("mcp_servers", "rag_collections", "tool_ma_file", "files"):
            if ps.get(k) not in (None, "", [], {}):
                ly.append(f"khối Agent có ô {k} khác rỗng")
        return ly
    except Exception as e:  # noqa: BLE001 — không đọc được ⇒ coi là lệch (đóng)
        return [f"không kiểm được workflow hỏi tài liệu ({type(e).__name__})"]


def token_uoc(ky_tu: int) -> int:
    """ƯỚC số token của toàn văn (chỉ để hiện + ghi phiếu chọn ngưỡng; KHÔNG dùng để chặn): tiếng Việt có dấu đo ~ 2,5–3,5 ký tự / token ⇒ lấy 3."""
    return ky_tu // 3


class KhoFile:
    def __init__(self, lam_sach_ten, gio=time.time, ghi=None, nap=None, viec=None, tim=None):
        """[ghi chú kỹ thuật] nap(org, tên, byte) ⇒ (http, {"ma"} | {"loi"}) — bắt đầu nạp kho TẠM (kho_tai_lieu.bat_dau_nap) · viec(org, mã) ⇒ (http, trạng thái)
        · tim(org, kho, câu, k) ⇒ (http, [trang…], lỗi). Thiếu nap ⇒ bản chụp bị từ chối như cũ."""
        self.lam_sach_ten, self.gio, self.ghi, self.f = lam_sach_ten, gio, ghi or (lambda s: None), {}
        self.nap, self.viec, self.tim = nap, viec, tim

    def _don(self):
        t = self.gio()
        for k in [k for k, v in self.f.items() if t > v["het"]]:
            self.f.pop(k, None)
        while len(self.f) > SO_FILE_TOI_DA or (len(self.f) > 1 and sum(v["kq"]["ky_tu"] for v in self.f.values()) > KY_TU_GIU_TOI_DA):
            cu = min(self.f, key=lambda k: self.f[k]["luc"]); self.f.pop(cu, None)
            self.ghi("TAI-LIEU-CHAT bỏ file đang mở cũ nhất vì chạm chốt bộ nhớ (người dùng hỏi lại sẽ được báo kéo lại file)")

    def _giu_raw(self):
        """Chỉ RAW_GIU_TOI_DA file mở GẦN NHẤT còn giữ byte gốc (bảng tính `raw` + PDF / Word `raw_vb` tính chung) — file cũ hơn bị bỏ byte, vẫn hỏi được theo đường đang có."""
        co = sorted((x for x in self.f.values() if x.get("raw") is not None or x.get("raw_vb") is not None), key=lambda x: x.get("luc") or 0)
        for x in co[:-RAW_GIU_TOI_DA]:
            x["raw"] = None; x["raw_vb"] = None

    def chuyen_sang_kho(self, conv: str) -> tuple[int, dict]:
        """[ghi chú kỹ thuật] File đọc thẳng (PDF / Word) đang mở ⇒ đưa byte gốc vào kho TẠM để app RAG mô tả hình; bản ghi file đang mở đổi sang kiểu "kho"
        (cùng hình với PDF chụp). 200 = đã bắt đầu nạp; khác 200 = không chuyển được (bản ghi GIỮ NGUYÊN, người gọi đi tiếp đường đọc thẳng)."""
        f = self.f.get(conv)
        if not f or f.get("kieu") or f.get("raw_vb") is None or not self.nap:
            return 409, {"loi": "không có file đọc thẳng còn byte gốc"}
        try:
            ma, r = self.nap(f["org"], f["ten"], f["raw_vb"])
        except Exception as x:  # noqa: BLE001
            self.ghi(f"TAI-LIEU-CHAT đọc cả hình: không bắt đầu nạp kho được ({type(x).__name__})"); return 503, {"loi": CHU["dich_vu_tat"]}
        if ma != 202 or not isinstance(r, dict) or not r.get("ma"):
            ly = str((r or {}).get("loi") or "")[:300] if isinstance(r, dict) else ""
            self.ghi(f"TAI-LIEU-CHAT đọc cả hình: nạp kho bị từ chối (mã {ma}): {ly[:80]}"); return (ma if ma in (402, 409, 413, 422, 429, 503) else 502), {"loi": ly}
        loai, so_trang = f["kq"].get("loai"), len(f["kq"]["phan"]) if f["kq"].get("don_vi") == "trang" else 0
        f.update(kieu="kho", viec=r["ma"], kho=None, ly_do="co_hinh", vi="đang đọc lại cả hình vẽ trong file", so_trang=so_trang, raw_vb=None,
                 kq={"loai": loai, "phan": [], "ky_tu": 0, "don_vi": "trang" if loai == "pdf" else "đoạn", "ghi_chu": ""}, luc=self.gio(), het=self.gio() + HAN_GIAY)
        self.ghi(f"TAI-LIEU-CHAT đọc cả hình: file {loai} · {so_trang} trang ⇒ NẠP KHO việc {str(r['ma'])[:8]} · sha {f['sha'][:8]}")
        phut = max(1, round(so_trang * GIAY_MOI_TRANG_CHUP / 60)) if so_trang else None
        return 200, {"chu": f"Câu này hỏi về hình vẽ / biểu đồ trong “{f['ten']}”. Lúc đầu mình chỉ đọc phần chữ của file, nên giờ mình đọc lại CẢ HÌNH"
                            + (f" ({so_trang} trang, ước khoảng {phut} phút)" if phut else "") + ". Bạn hỏi lại câu này sau ít phút nhé — đọc xong mình mới trả lời được."}

    def mo(self, org: str, conv: str, ten, than: bytes) -> tuple[int, dict]:
        if D.goi_y_luu_lai(ten):   # [ghi chú kỹ thuật] .doc / .pptx / .xlsb / .ods: nói đúng cách lưu lại (không lặp tên file)
            return 422, {"loi": D.goi_y_luu_lai(ten)}
        ten_sach = self.lam_sach_ten(ten)
        if not ten_sach or not (ten_sach.lower().endswith(D.DUOI) or D.duoi_cua(ten_sach) in D.DUOI_KHO):
            return 422, {"loi": CHU["ten"]}
        if not isinstance(than, (bytes, bytearray)) or not 1 <= len(than) <= CO_TOI_DA:
            return 413, {"loi": CHU["co"]}
        try:
            kq = D.tach_chu(ten_sach, bytes(than))
        except D.CanNapKho as e:   # [ghi chú kỹ thuật] bản chụp / lẫn trang chụp ⇒ tự nạp kho (có đường nạp) thay vì từ chối
            return self._mo_kho(org, conv, ten_sach, bytes(than), e)
        except D.TuChoi as e:
            self.ghi(f"TAI-LIEU-CHAT từ chối file ({len(than)} byte): {str(e)[:80]}")   # KHÔNG ghi tên file vào nhật ký
            return 422, {"loi": str(e)}
        self._don()
        cu = self.f.get(conv)
        van = D.van_ban_cho_ai(kq, ten_sach)
        ranh = secrets.token_hex(8)
        while ranh in van:            # tài liệu tình cờ chứa đúng mã vừa sinh ⇒ sinh lại (không từ chối file)
            ranh = secrets.token_hex(8)
        self.f[conv] = {"ranh": ranh, "org": org, "ten": ten_sach, "sha": hashlib.sha256(bytes(than)).hexdigest(), "kq": kq, "luc": self.gio(), "het": self.gio() + HAN_GIAY}
        if kq.get("loai") in ("pdf", "docx") and not (kq.get("don_vi") == "trang" and len(kq["phan"]) > TRANG_HINH_TOI_DA):   # [ghi chú kỹ thuật]
            self.f[conv]["raw_vb"] = bytes(than)
            self._giu_raw()
        self._don()
        if conv not in self.f:   # chính file này lớn hơn chốt tổng ⇒ không giữ được (không xảy ra với rào 3 triệu ký tự / file, để đóng)
            return 422, {"loi": "Máy chủ đang giữ quá nhiều tài liệu, chưa nhận thêm được. Bạn thử lại sau ít phút nhé."}
        n = len(kq["phan"])
        self.ghi(f"TAI-LIEU-CHAT nhận file {kq['loai']} · {n} {kq['don_vi']} · {kq['ky_tu']} ký tự · sha {self.f[conv]['sha'][:8]}")
        return 200, {"ten": ten_sach, "loai": kq["loai"], "so_phan": n, "don_vi": kq["don_vi"], "ky_tu": kq["ky_tu"], "token_uoc": token_uoc(kq["ky_tu"]),
                     "thay_file_cu": cu["ten"] if cu and cu["org"] == org else None, "giu_gio": HAN_GIAY // 3600,
                     "chu": f"Đã đọc “{ten_sach}”: {n} {kq['don_vi']}, {kq['ky_tu']:,} ký tự.".replace(",", ".") + (" " + kq["ghi_chu"] if kq["ghi_chu"] else "")
                            + " Bạn hỏi về tài liệu này ngay trong khung chat."}

    def _mo_kho(self, org, conv, ten_sach, than, e):
        if not self.nap:
            self.ghi(f"TAI-LIEU-CHAT từ chối file ({len(than)} byte): {str(e)[:80]}")
            return 422, {"loi": str(e)}
        try:
            ma, r = self.nap(org, ten_sach, than)
        except Exception as x:  # noqa: BLE001 — chưa nối cổng / lỗi bất ngờ ⇒ nói rõ, không nhận lặng lẽ
            self.ghi(f"TAI-LIEU-CHAT không bắt đầu nạp kho được ({type(x).__name__})")
            return 503, {"loi": CHU["dich_vu_tat"]}
        if ma != 202 or not isinstance(r, dict) or not r.get("ma"):
            ly = str((r or {}).get("loi") or "")[:300] if isinstance(r, dict) else ""
            self.ghi(f"TAI-LIEU-CHAT nạp kho bị từ chối (mã {ma}): {ly[:80]}")
            return (ma if ma in (402, 409, 413, 422, 429, 503) else 502), {"loi": CHU["dich_vu_tat"] if ma == 503 else (ly or CHU["dich_vu_tat"])}
        self._don()
        cu = self.f.get(conv)
        loai = getattr(e, "loai", "pdf")
        don_vi = "trang" if loai in ("pdf", "anh") else "đoạn"   # [ghi chú kỹ thuật] PDF chụp / ảnh dẫn theo trang; bảng tính / trang web / JSON dẫn theo đoạn tìm được
        vi = ("bản chụp" if e.ly_do == "ban_chup" else f"có {len(e.trang_chup)} trang là ảnh chụp") if loai == "pdf" else D.LOAI_KHO[loai][0]
        self.f[conv] = {"kieu": "kho", "viec": r["ma"], "kho": None, "ly_do": e.ly_do, "vi": vi, "so_trang": e.so_trang, "ranh": secrets.token_hex(8), "org": org, "ten": ten_sach,
                        "sha": hashlib.sha256(than).hexdigest(), "kq": {"loai": loai, "phan": [], "ky_tu": 0, "don_vi": don_vi, "ghi_chu": ""},
                        "luc": self.gio(), "het": self.gio() + HAN_GIAY}
        chung = {"ten": ten_sach, "loai": loai, "so_phan": e.so_trang, "don_vi": don_vi, "ky_tu": 0, "token_uoc": 0, "dang_doc": True,
                 "thay_file_cu": cu["ten"] if cu and cu["org"] == org else None, "giu_gio": HAN_GIAY // 3600}
        if loai != "pdf":   # KHÔNG nêu "khoảng N phút": chưa có số đo thật cho các loại này (bài học 19/11: không đưa số suy ra làm số đo)
            if loai == "bang_tinh":   # [ghi chú kỹ thuật] giữ byte gốc để tính trên CẢ bảng bằng code; giới hạn số file giữ byte
                self.f[conv]["raw"] = bytes(than)
                self._giu_raw()
            self.ghi(f"TAI-LIEU-CHAT nhận file {loai} ({len(than)} byte) ⇒ NẠP KHO việc {str(r['ma'])[:8]} · sha {self.f[conv]['sha'][:8]}")
            return 200, dict(chung, chu=f"Đang đọc “{ten_sach}” ({vi} — phải đưa vào kho tìm kiếm trước khi hỏi được). Bạn cứ gõ câu hỏi; đọc xong mình mới trả lời được. "
                             + ("Với bảng tính, mình tra được từng dòng / ô; câu cần cộng, đếm, so sánh trên cả bảng thì mình tính bằng code trên toàn bộ file."
                                if loai == "bang_tinh" else "Câu trả lời sẽ dẫn theo phần tìm được trong file."))
        phut = max(1, round(e.so_trang * GIAY_MOI_TRANG_CHUP / 60))
        self.ghi(f"TAI-LIEU-CHAT nhận file pdf {vi} · {e.so_trang} trang ⇒ NẠP KHO việc {str(r['ma'])[:8]} · sha {self.f[conv]['sha'][:8]}")
        return 200, dict(chung, chu=f"Đang đọc “{ten_sach}” ({e.so_trang} trang, {vi} nên phải nhận dạng chữ — lâu hơn, ước khoảng {phut} phút). "
                         "Bạn cứ gõ câu hỏi; đọc xong mình mới trả lời được, câu trả lời sẽ dẫn theo số trang.")

    def mo_tu_kho(self, org: str, conv: str, muc: dict) -> tuple[int, dict]:
        """[ghi chú kỹ thuật] Nút "Hỏi trên tài liệu này" của trang Dữ liệu: gắn một tài liệu ĐÃ nạp kho vào cuộc trò chuyện — không nạp lại, không gọi AI,
        không gửi gì đi. `muc` = MỘT dòng của sổ kho mà NGƯỜI GỌI đã tra theo sha trong sổ của ĐÚNG tổ chức `org` (hàm này không tự tra, không nhận tên kho từ trình duyệt).
        Đơn vị dẫn: PDF / ảnh = trang; loại khác = đoạn tìm được (dịch vụ đọc không trả số trang cho Word / chữ)."""
        ten, kho, ma = (muc or {}).get("ten"), (muc or {}).get("kho"), (muc or {}).get("sha")
        if not (isinstance(org, str) and org and isinstance(ten, str) and ten.strip() and isinstance(kho, str) and kho.strip() and isinstance(ma, str) and len(ma) == 64):
            return 404, {"loi": CHU["kho_khong_co"]}
        duoi = D.duoi_cua(ten)
        loai = "pdf" if duoi == ".pdf" else "docx" if duoi == ".docx" else D.DUOI_KHO.get(duoi, "chu")
        don_vi = "trang" if loai in ("pdf", "anh") else "đoạn"
        try:
            so_trang = max(0, int(muc.get("trang") or 0))
        except (TypeError, ValueError):
            so_trang = 0
        self._don()
        cu = self.f.get(conv)
        self.f[conv] = {"kieu": "kho", "viec": None, "kho": kho, "ly_do": "tu_kho", "vi": "tài liệu đã đưa vào từ trang Dữ liệu", "so_trang": so_trang if don_vi == "trang" else 0,
                        "ranh": secrets.token_hex(8), "org": org, "ten": ten, "sha": ma, "kq": {"loai": loai, "phan": [], "ky_tu": 0, "don_vi": don_vi, "ghi_chu": ""},
                        "luc": self.gio(), "het": self.gio() + HAN_GIAY}
        self._don()
        if conv not in self.f:
            return 422, {"loi": "Máy chủ đang giữ quá nhiều tài liệu, chưa nhận thêm được. Bạn thử lại sau ít phút nhé."}
        self.ghi(f"TAI-LIEU-CHAT mở tài liệu TỪ KHO (trang Dữ liệu) {loai} · sha {ma[:8]} — không nạp lại")   # KHÔNG ghi tên file, tên kho
        return 200, {"ten": ten, "loai": loai, "so_phan": so_trang if don_vi == "trang" and so_trang else None, "don_vi": don_vi, "ky_tu": 0, "token_uoc": 0, "tu_kho": True,
                     "thay_file_cu": cu["ten"] if cu and cu["org"] == org else None, "giu_gio": HAN_GIAY // 3600,
                     "chu": f"Đã mở “{ten}” (tài liệu bạn đã đưa vào ở trang Dữ liệu — không phải đọc lại). Bạn hỏi về tài liệu này ngay trong khung chat; mỗi câu hỏi mình tìm "
                            f"các phần liên quan nhất trong tài liệu rồi trả lời, có dẫn {don_vi}."}

    def mo_kho_chung(self, org: str, conv: str, ds: list) -> tuple[int, dict]:
        """[ghi chú kỹ thuật] Gắn "kho tài liệu của công ty" (mọi kho BỀN chưa ẩn — `ds` là các dòng sổ kho NGƯỜI GỌI đã lọc theo ĐÚNG tổ chức) làm tài liệu
        đang mở của cuộc trò chuyện. Không nạp gì, không gọi AI. Kho rỗng ⇒ 404."""
        kho = [(str(x.get("ten") or "tài liệu"), x["kho"]) for x in (ds or []) if isinstance(x, dict) and isinstance(x.get("kho"), str) and x["kho"].strip()]
        if not (isinstance(org, str) and org and kho):
            return 404, {"loi": CHU["kho_khong_co"]}
        self._don()
        ma = hashlib.sha256("\n".join(sorted(k_ for _, k_ in kho)).encode()).hexdigest()
        self.f[conv] = {"kieu": "kho_chung", "viec": None, "kho": None, "kho_ds": [k_ for _, k_ in kho], "ly_do": "kho_chung", "vi": "kho tài liệu của công ty", "so_trang": 0,
                        "ranh": secrets.token_hex(8), "org": org, "ten": f"Kho tài liệu của công ty ({len(kho)} tài liệu)", "sha": ma,
                        "kq": {"loai": "kho_chung", "phan": [], "ky_tu": 0, "don_vi": "đoạn", "ghi_chu": ""}, "luc": self.gio(), "het": self.gio() + HAN_GIAY}
        if self.ghi:
            self.ghi(f"TÀI LIỆU CHAT: mở kho chung {len(kho)} tài liệu cho cuộc trò chuyện")
        return 200, {"ten": self.f[conv]["ten"], "so_tai_lieu": len(kho)}

    def _tim_kho_chung(self, f, cau: str, cau_truoc: str | None, k: int) -> tuple[dict, dict]:
        """Tìm trên MỌI kho của kho chung: mỗi câu tìm gọi theo lô ≤ LO_KHO kho; gộp, bỏ trùng chữ, xếp theo điểm (cao trước; thiếu điểm xếp cuối), giữ SO_DOAN_KHO_CHUNG đoạn.
        Mỗi đoạn mở bằng dòng "Nguồn: <tên file> — trang N" (tên file do dịch vụ đọc trả). f["nguon_doan"] = {số đoạn: nhãn nguồn} để trợ lý in dòng Nguồn."""
        hs = {"k": k, "luot": [], "trang": [], "so_trang_tra": 0, "ky_tu": 0, "so_kho": len(f["kho_ds"]), "kho_chung": True}
        c1, c0 = " ".join(str(cau).split()), " ".join(str(cau_truoc or "").split())
        cau_tim = [c1[:500]] + ([(c0 + " " + c1)[:500]] if c0 and c0 != c1 else [])
        ung, da_co = [], set()
        for q in cau_tim:
            for i in range(0, len(f["kho_ds"]), LO_KHO):
                lo = f["kho_ds"][i:i + LO_KHO]
                try:
                    ma, ds, loi = self.tim(f["org"], lo, q, k) if self.tim else (0, [], "chưa nối cổng tìm tài liệu")
                except Exception as x:  # noqa: BLE001
                    ma, ds, loi = 0, [], type(x).__name__
                hs["luot"].append({"cau_tim": q, "http": ma, "so_trang": len(ds), "so_kho": len(lo), "trang": [x["page"] for x in ds], "loi": loi})
                for x in ds:
                    khoa = " ".join(x["text"].split())
                    if khoa in da_co:
                        continue
                    da_co.add(khoa)
                    diem = x.get("score") if isinstance(x.get("score"), (int, float)) and not isinstance(x.get("score"), bool) else None
                    ten = Path(str(x.get("file") or "")).name.strip() or "tài liệu trong kho"
                    ung.append((diem, ten, x["page"], x["text"]))
        ung.sort(key=lambda u: (u[0] is None, -(u[0] or 0)))
        phan, nguon = [], {}
        for i, (diem, ten, trang, chu) in enumerate(ung[:SO_DOAN_KHO_CHUNG]):
            nhan = f"{ten} — trang {trang}" if D.duoi_cua(ten) in (".pdf", ".png", ".jpg", ".jpeg") else ten
            nguon[i + 1] = nhan
            # [sửa 20/11 sau lượt thật 5ac3e083] dòng đầu đoạn KHÔNG mang chữ "trang": bản đầu ghi "Nguồn: <file> — trang N" ⇒ AI dẫn "[trang N]" thay vì "[đoạn N]" ⇒ bộ kiểm
            # chỗ dẫn báo lỗi và không ra dòng Nguồn. Tên file + trang vẫn tới người dùng qua dòng "Nguồn: [đoạn N] = …" do code thêm sau câu trả lời (f["nguon_doan"]).
            phan.append({"so": i + 1, "nhan": f"đoạn {i + 1}", "chu": f"(thuộc file: {ten})\n" + D._sach(chu)})
        f["nguon_doan"] = nguon
        hs.update(trang=[x["so"] for x in phan], so_trang_tra=len(phan), ky_tu=sum(len(x["chu"]) for x in phan), don_vi="đoạn", so_ung_vien=len(ung))
        return {"loai": "kho_chung", "phan": phan, "ky_tu": hs["ky_tu"], "don_vi": "đoạn", "ghi_chu": "", "doan": True, "tong_trang": None}, hs

    def trang_thai_kho(self, f) -> tuple[str, dict]:
        """("xong" | "dang_nap" | "loi", thông tin). Xong ⇒ f["kho"] = tên kho (ghi lại trong bản ghi file đang mở)."""
        if f.get("kho") or f.get("kieu") == "kho_chung":   # kho chung: các kho đã nạp xong từ trước
            return "xong", {}
        try:
            c, r = self.viec(f["org"], f["viec"]) if self.viec else (404, {})
        except Exception:  # noqa: BLE001
            c, r = 404, {}
        if c != 200 or not isinstance(r, dict):
            return "loi", {"chu": CHU["mat_dau"]}
        if r.get("tt") == "xong" and r.get("kho"):
            f["kho"] = r["kho"]
            return "xong", {}
        if r.get("tt") == "loi":
            return "loi", {"chu": CHU["nap_loi"].format(ten=f["ten"], ly_do=str(r.get("loi") or "không rõ lý do.")[:200])}
        return "dang_nap", {"chu": CHU["dang_doc"].format(ten=f["ten"], vi=(f.get("vi") or "bản chụp") + (" nên lâu hơn" if f["kq"].get("loai") == "pdf" else ""),
                                                          giay=int(self.gio() - f["luc"]))}

    def tim_trang(self, f, cau: str, cau_truoc: str | None = None, k: int = 8) -> tuple[dict, dict]:
        """Tìm trong kho của file: lượt 1 = câu hỏi; lượt 2 (chỉ khi có câu trước) = câu trước + câu hỏi (câu nối tiếp kiểu "còn mục 3 thì sao"). Gộp theo số trang.
        Trả (kq kiểu tach_chu nhưng chỉ gồm các trang tìm được — có khoá "doan": True, hồ sơ tìm để ghi vào lượt).
        [ghi chú kỹ thuật] File KHÔNG dẫn theo trang (bảng tính / trang web / JSON): app trả `page` = thứ tự SHEET / mục, mỗi kết quả là MỘT hàng / một mục ⇒ KHÔNG gộp
        theo `page` (gộp là mất hàng cùng sheet); giữ mọi kết quả khác chữ, đánh số "đoạn 1…n" theo thứ tự tìm thấy trong lượt này."""
        if f.get("kieu") == "kho_chung":   # [ghi chú kỹ thuật]
            return self._tim_kho_chung(f, cau, cau_truoc, k)
        hs = {"k": k, "luot": [], "trang": [], "so_trang_tra": 0, "ky_tu": 0}
        trang, doan, da_co = {}, [], set()
        theo_trang = f["kq"].get("don_vi") != "đoạn"
        c1, c0 = " ".join(str(cau).split()), " ".join(str(cau_truoc or "").split())
        cau_tim = [c1[:500]] + ([(c0 + " " + c1)[:500]] if c0 and c0 != c1 else [])   # câu trước trùng câu đang hỏi (hỏi lại) ⇒ không tìm lần hai
        for q in cau_tim:
            try:
                c, ds, loi = self.tim(f["org"], f["kho"], q, k) if self.tim else (0, [], "chưa nối cổng tìm tài liệu")
            except Exception as x:  # noqa: BLE001
                c, ds, loi = 0, [], type(x).__name__
            hs["luot"].append({"cau_tim": q, "http": c, "so_trang": len(ds), "trang": [x["page"] for x in ds], "loi": loi})
            for x in ds:
                if not theo_trang:
                    khoa = " ".join(x["text"].split())
                    if khoa not in da_co:
                        da_co.add(khoa); doan.append(x["text"])
                elif x["page"] not in trang or len(x["text"]) > len(trang[x["page"]]):
                    trang[x["page"]] = x["text"]
        if theo_trang:
            phan = [{"so": p, "nhan": f"trang {p}", "chu": D._sach(trang[p])} for p in sorted(trang)]
        else:
            phan = [{"so": i + 1, "nhan": f"đoạn {i + 1}", "chu": D._sach(c)} for i, c in enumerate(doan)]
        hs.update(trang=[x["so"] for x in phan], so_trang_tra=len(phan), ky_tu=sum(len(x["chu"]) for x in phan), don_vi="trang" if theo_trang else "đoạn")
        return {"loai": f["kq"].get("loai", "pdf"), "phan": phan, "ky_tu": hs["ky_tu"], "don_vi": "trang" if theo_trang else "đoạn", "ghi_chu": "", "doan": True,
                "tong_trang": f.get("so_trang") or None}, hs

    def dang_mo(self, org, conv):
        self._don()
        v = self.f.get(conv) if isinstance(conv, str) else None
        return v if v and v["org"] == org else None     # tổ chức khác (hoặc không rõ tổ chức) ⇒ coi như không có

    def dong(self, org, conv) -> bool:
        v = self.dang_mo(org, conv)
        if v:
            self.f.pop(conv, None)
        return bool(v)


def o_nhap(f: dict, cau: str, lich_su: list | None = None) -> str:
    """Chữ đưa vào ô nhập. KHÔNG cắt tài liệu, KHÔNG cắt câu hỏi. Lịch sử: các lượt trước của chính cuộc trò chuyện (đã có chốt RAM ở Kho của khung chat).
    [ghi chú kỹ thuật] MỐC CÓ MÃ: tài liệu là chữ người ngoài viết — nó có thể chứa dòng "CÂU HỎI CỦA NGƯỜI DÙNG:" giả. Mỗi lần MỞ file máy chủ sinh một mã 16 hex
    (f["ranh"]) mà tài liệu không chứa; bốn dòng mốc mang mã đó, lời dặn bảo mô hình chỉ tin dòng mốc mang đúng mã ở DÒNG ĐẦU. Mã giữ nguyên giữa các câu hỏi
    trên cùng file (phần đầu ô nhập giống hệt ⇒ bộ nhớ đệm của nhà cung cấp trúng). Câu hỏi / lượt trước chứa mã ⇒ đổi mã của file rồi mới ghép."""
    ls = [m for m in (lich_su or []) if isinstance(m, dict) and str(m.get("noi_dung") or "").strip()]
    truoc = "\n".join(("Người dùng: " if m.get("vai") == "nguoi_dung" else "Trợ lý: ") + str(m["noi_dung"]).strip() for m in ls)
    van = D.van_ban_doan(f["kq"], f["ten"]) if f["kq"].get("doan") else D.van_ban_cho_ai(f["kq"], f["ten"])   # [ghi chú kỹ thuật] chỉ các trang tìm được
    while f["ranh"] in cau or f["ranh"] in truoc or f["ranh"] in van:
        f["ranh"] = secrets.token_hex(8)
    r = f["ranh"]
    return (f"MÃ_MỐC {r}\n<<<TAI_LIEU {r}>>>\n" + van + f"\n<<<HET_TAI_LIEU {r}>>>"
            + ("\n\nCÁC LƯỢT TRƯỚC TRONG CUỘC TRÒ CHUYỆN (để hiểu câu hỏi nối tiếp; KHÔNG phải nguồn để trả lời — nguồn duy nhất là tài liệu ở trên):\n" + truoc if truoc else "")
            + f"\n\n<<<CAU_HOI {r}>>>\n" + cau.strip())


def khoa_cau(f: dict, cau: str) -> str:
    """Câu dùng làm KHOÁ (không chạy lại / cache): cùng câu trên file KHÁC phải là khoá khác."""
    return cau + f"\n[tệp {f['sha'][:16]}]"


def hau_kiem(chu: str, f: dict) -> dict:
    r = D.kiem_tra_loi(chu, f["kq"])
    return {"ten": f["ten"], "don_vi": f["kq"]["don_vi"], "co_dan": r["co_dan"], "loi": r["loi"]}
