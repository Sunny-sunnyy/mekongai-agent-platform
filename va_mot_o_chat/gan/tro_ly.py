"""MỘT khung chat — lõi máy chủ cho POST /api/tro-ly (đợt 1) — C 18/11/2026. ĐANG GẮN từ 18/11/2026 (gói U2; sửa chữ 19/11).
Kế hoạch: tài liệu thiết kế nội bộ (đối chiếu kỹ thuật).
yêu cầu vận hành 18/11: bộ định tuyến AI thay code (đang dùng pplx-decider ngưỡng 0,7 — chon_jev.DINH_TUYEN; chữ "Jev" trong mã là tên cũ của vai này); tự chạy, không nút xác nhận; dưới 0,20 USD tự chạy, đắt hơn thì hỏi; một loại chat.

Hàm THUẦN: mọi phụ thuộc truyền vào (thử bằng đồ giả ở thu_tro_ly.py). Không ném ra ngoài: lỗi ⇒ câu báo lời thường.
  xu_ly(than, dep) ⇒ (http, {"loai", "chu", "lua_chon"?, "phieu"?, "workflow"?, "phien"?, "nhan"?})
  loai ∈ tra_loi (đã chạy, có câu trả lời) · cho_duyet (lượt dừng chờ Người duyệt) · hoi (câu làm rõ / hỏi tiền — kèm phieu MỘT LẦN) ·
         khong_co · dung_moi (đã chuyển trợ lý dựng) · loi (lời thường, không chạy gì)
Rào (giữ bằng CODE, không dựa vào Jev):
  · bậc 0 từ chối (chon.bac0) · chỉ thẻ trong the_ghim.json (UUID + sha payload GHIM — E G2: sha lúc chạy khác ⇒ không chạy, hỏi lại)
  · khối của workflow ∈ KHOI_DA_XEP, không có ô nhận file, đúng 1 ô nhập chữ, có mức tối đa (muc_toi_da.toi_da)
  · mức LÚC CHẠY < 0,20 ⇒ tự chạy; ≥ 0,20 ⇒ hỏi tiền (phiếu gắn mức lúc hỏi); mức > phần còn của trần ngày ⇒ không chạy
  · phiếu (đối chiếu kỹ thuật): máy chủ cấp, MỘT LẦN, gắn conv + danh sách mã đưa ra + mức lúc hỏi, hết hạn PHIEU_GIAY; trang chỉ gửi lại mã phiếu + lựa chọn
  · Jev quá CHO_JEV giây ⇒ hỏi lại (không chạy), đếm; Jev / OpenRouter lỗi ⇒ báo lỗi thường, không chạy
  · cùng conv + cùng câu + cùng workflow đã chạy xong ⇒ trả lại câu trả lời cũ, không chạy lại
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import unicodedata
from pathlib import Path

G = Path(__file__).resolve().parent
import sys  # noqa: E402
sys.path[:0] = [str(G.parent)]
import chon as C  # noqa: E402
import chon_jev as J  # noqa: E402
sys.path.append(str(G.parent.parent / "va_tai_lieu_chat"))   # [ghi chú kỹ thuật] hỏi đáp trên file kéo vào khung chat (đường "đọc thẳng")
import tai_lieu_chat as TLC  # noqa: E402

PHIEU_GIAY = 600           # phiếu hỏi lại / hỏi tiền hết hạn sau 10 phút
LS_GIAY = 7200             # lịch sử một cuộc chat giữ 2 giờ (đối chiếu kỹ thuật)
LS_TOI_DA_CONV = 500       # tối đa số cuộc chat giữ trong bộ nhớ (cũ nhất bị bỏ trước)
LS_KY_TU_MOT_CUOC = 200_000   # [ghi chú kỹ thuật] tổng ký tự lịch sử của MỘT cuộc — chốt RAM: 500 cuộc × 200.000 ký tự ≈ 200 MB cận trên (vượt ⇒ bỏ mục cũ nhất, có đếm)
CHO_JEV = 5.0              # A: Jev quá 5 s ⇒ hỏi lại
KHOI_DA_XEP = {"agent_node", "text_input_node", "guardrails_node", "text_output_node", "human_intervention_node",
               "flexible_code_interpreter_node", "markdown_output_node", "evals_node", "docx_chat_node", "xlsx_chat_node", "pptx_chat_node",
               "d3_data_visualization_node", "data_analysis_agent_node", "network_graph_output_node", "text_redaction_node"}   # = dung_the trừ file_input_node
MA_CONV = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MA_PHIEU = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# Câu cho người dùng phổ thông — không chữ kỹ thuật (đối chiếu kỹ thuật)
CHU = {
    "ban": "Hệ thống đang bận, bạn hỏi lại giúp mình sau ít phút nhé.",
    "loi": "Mình chưa xử lý được câu này lúc này. Bạn thử hỏi lại sau ít phút nhé.",
    # [ghi chú kỹ thuật] lượt được nhận nhưng hệ thống chạy không ghi nhận được (Temporal đã kết thúc, không có dòng lượt) — chưa gọi AI
    "mat_dong": "Hệ thống đang bận nên lượt này chưa chạy được (chưa tốn tiền). Bạn bấm gửi lại câu hỏi giúp mình nhé.",
    "mat_dong_co_khoi": "Hệ thống đang bận nên lượt này bị gián đoạn giữa chừng, mình chưa có câu trả lời. Bạn bấm gửi lại câu hỏi giúp mình nhé.",
    "het_tran": "Hôm nay hệ thống đã gần hết hạn mức chi phí, nên việc này chưa chạy được. Bạn thử lại vào ngày mai hoặc báo người phụ trách nhé.",
    "thieu_muc": "Việc này chưa chạy được từ khung chat. Bạn mở trên xưởng hoặc báo người phụ trách nhé.",
    "doi": "Quy trình này vừa được chỉnh sửa, mình chưa dùng được bản mới. Bạn báo người phụ trách kiểm lại giúp nhé.",
    "phieu_het": "Lựa chọn này đã hết hạn. Bạn hỏi lại câu của mình nhé.",
    "khong_co": "Hiện chưa có quy trình nào làm được việc này. Bạn có thể hỏi về thưởng, KPI hoặc tài liệu của công ty.",
    "tu_choi": "Câu hỏi trống hoặc quá dài, bạn viết gọn lại giúp mình nhé.",
    "cho_duyet": "Mình đã chạy xong phần tính. Bảng chính thức đang chờ người phụ trách duyệt — khi duyệt xong bạn sẽ tải được file ở đây.",
    # [ghi chú kỹ thuật] workflow BẬT tự duyệt khi quá hạn ⇒ nói rõ hạn + điều xảy ra khi hết hạn (trước: lượt lỗi sau 300 giây mà lời vẫn hứa "duyệt xong sẽ có file")
    "cho_duyet_co_han": "Mình đã chạy xong phần tính. Bảng đang chờ người phụ trách duyệt (hạn {han}). Nếu hết hạn mà chưa ai bấm, hệ thống tự cho đi tiếp: file ra khi đó là "
                        "bản NHÁP mang nhãn “tự duyệt khi quá hạn — chưa có người duyệt”. Sau {han}, hỏi lại ĐÚNG câu này để lấy kết quả của lượt đang chờ (không chạy lượt mới), "
                        "hoặc bấm “Mở lượt này trên xưởng” bên dưới để xem.",   # [ghi chú kỹ thuật] nay hỏi lại ĐÚNG câu thì đọc lại lượt đang chờ (đã có ca thử) ⇒ được hứa
    "khong_doc": "Mình đã chạy nhưng chưa đọc được kết quả. Bạn mở lượt chạy trên xưởng để xem nhé.",
    # [ghi chú kỹ thuật] lượt bị bước kiểm số chặn: nói thật + gợi ý; KHÔNG nêu con số bị chặn (số chưa qua kiểm — E T2)
    # [ghi chú kỹ thuật] nói TRUNG TÍNH ("chưa xác nhận được", không khẳng định trợ lý tính sai — sáng 20/11 4 / 4 lượt bị chặn là chặn oan); lý do thô chỉ dành cho người phụ trách
    "rao_giu_lai": "Câu trả lời đã được viết nhưng bị GIỮ LẠI: bước kiểm số chưa xác nhận được một con số trong câu trả lời, nên mình không đưa ra. "
                   "Bạn hỏi lại giúp mình, hoặc hỏi hẹp hơn (một nhân viên, một KPI) nhé. Người phụ trách xem được lý do ở màn lượt chạy trên xưởng.",
    "hoi_ma_nv": "Bạn đang hỏi về nhân viên nào? Cho mình đúng một mã nhân viên (ví dụ NV05) nhé.",
    "khong_doc_nv": "Mình chưa đọc được danh sách nhân viên của dữ liệu kỳ này, nên chưa chạy. Bạn thử lại sau nhé.",
    "khong_co_nv": "Mình không thấy nhân viên {ma} trong dữ liệu kỳ này. Bạn kiểm lại mã giúp mình nhé.",
    "nhanh_chua_doc": "Phần này chưa đọc được (chưa qua bước kiểm số).",
    "khong_dat_luat": "Quy trình này chưa đúng luật an toàn KPI nên mình không chạy từ chat. Người phụ trách cần sửa nó trên xưởng trước.",
    "dung_giua": "_Lượt dừng giữa chừng ở một phần; phần nào chưa đọc được hoặc chưa qua bước kiểm số đều ghi rõ ngay tại phần đó._",   # [ghi chú kỹ thuật]
    "chua_kiem_so": "_Số trong phần này do khối chạy code / AI tính, CHƯA qua bước kiểm số bằng code._",
    "so_ca_doi": "_Mẫu này cho số của CẢ ĐỘI (không xem riêng từng người)._",
    "xuat_chua_co": "Mình chưa có kết quả nào trong cuộc trò chuyện này để xuất. Bạn hỏi một câu về thưởng / KPI trước nhé.",
    "xuat_hoi_dang": "Bạn muốn xuất kết quả vừa rồi ra dạng nào?",
    "kho_chua_san": "Quy trình hỏi trên tài liệu đã nạp vào kho chưa dùng được: nó chưa được gắn với kho tài liệu nào còn dùng được của tổ chức này. Bạn báo người phụ trách chọn lại kho trên xưởng giúp nhé.",
    "dang_cho_duyet": "Lượt trước của quy trình này vẫn đang chờ người phụ trách duyệt. Khi duyệt xong bạn hỏi lại nhé — mình chưa chạy lượt mới.",
}


def _usd(x: float) -> str:
    """[ghi chú kỹ thuật] số tiền kiểu Việt: 0,40 (dấu phẩy thập phân)."""
    return f"{float(x):.2f}".replace(".", ",")


def sha_payload(pl) -> str:
    """PHẢI giống gan/ghim.py.sha_payload."""
    return hashlib.sha256(json.dumps(pl, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def doc_ghim(f: Path = G / "the_ghim.json") -> dict:
    return {x["ma"]: x for x in json.loads(Path(f).read_text(encoding="utf-8"))["the"]}


# [ghi chú kỹ thuật] mẫu NHIỀU ô nhập (mỗi ô một câu CÀI SẴN, chạy thành nhánh riêng) hoặc 0 ô: câu người dùng KHÔNG đổ vào ô.
#   giu_nguyen ⇒ chạy đúng câu cài sẵn (bản đã ghim sha) · thay_nv ⇒ thay ĐÚNG 1 mã NV trong mỗi câu cài sẵn bằng mã lấy từ câu người dùng
CACH_NAP = {"kpi_ban_tin": "giu_nguyen", "kpi_xem_nhanh_chinh_thuc": "giu_nguyen", "kpi_phieu_nhan_vien": "thay_nv", TLC.MA_THE: "tai_lieu", TLC.MA_THE_TINH: "tinh_bang"}
# [ghi chú kỹ thuật] wf_11fa5c4d BỎ: ca thật G4 — trang chặn lúc chạy "luật KPI: 13 lỗi" (mẫu không theo luật an toàn KPI) ⇒ không mở từ chat
MA_NV = re.compile(r"(?<![A-Z0-9])NV\d{2}(?![A-Z0-9])")          # chỗ đánh dấu trong câu CÀI SẴN (mẫu dựng 2 chữ số)
MA_NV_CAU = re.compile(r"(?<![A-Z0-9])NV\d+(?![A-Z0-9])")        # [ghi chú kỹ thuật] mã trong câu người dùng (sau .upper()); hợp lệ = ∈ ds_nv, không theo số chữ số


def o_nhap(payload) -> dict:
    """{mã khối ô nhập chữ: câu cài sẵn} theo thứ tự trong payload."""
    ra = {}
    for n in (payload or {}).get("nodes") or []:
        st = ((n or {}).get("data") or {}).get("struct") or {}
        if st.get("node_id") == "text_input_node":
            ps = {p.get("field_id"): p.get("value") for p in st.get("params", []) if isinstance(p, dict)}
            ra[n.get("id")] = ps.get("text_input") if isinstance(ps.get("text_input"), str) else ""
    return ra


def ra_ngoai_hop_le(payload, cach=None):
    """Rào HÌNH workflow cho đường một khung chat (thay du_dieu_kien 4 khối).
    cach=None ⇒ (mã ô nhập chữ | None, lý do) — đúng 1 ô (y hệt trước gói 4). cach ∈ CACH_NAP ⇒ (danh sách mã ô | None, lý do):
    giu_nguyen 0..n ô · thay_nv ≥ 1 ô, mỗi câu cài sẵn có ĐÚNG 1 chỗ mã NV."""
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        return None, ["không đọc được workflow"]
    loai = {}
    for n in payload["nodes"]:
        st = ((n or {}).get("data") or {}).get("struct") or {}
        loai[n.get("id")] = st.get("node_id")
        ps = {p.get("field_id"): p.get("value") for p in st.get("params", []) if isinstance(p, dict)}
        if ps.get("mcp_servers") not in (None, "", []):
            return None, ["workflow có máy chủ MCP (có thể gửi ra ngoài)"]
    la = sorted({str(v) for v in loai.values() if v not in KHOI_DA_XEP and not (cach == "tinh_bang" and v == "file_input_node")})   # [ghi chú kỹ thuật] chỉ thẻ tính trên bảng được có khối nhận file
    if la:
        return None, ["có khối chưa được xếp cho chạy từ chat (" + ", ".join(la)[:200] + ")"]
    o = [k for k, v in loai.items() if v == "text_input_node"]
    if cach is None:
        if len(o) != 1:
            return None, ["phải có đúng một ô nhập câu hỏi"]
        return o[0], []
    if cach == "giu_nguyen":
        if any(MA_NV.search(c or "") for c in o_nhap(payload).values()):
            return None, ["câu cài sẵn có mã nhân viên nhưng thẻ khai giữ nguyên"]   # [ghi chú kỹ thuật] tránh chạy số một người mà câu hỏi người khác
        return o, []
    if cach == "thay_nv":
        cau = o_nhap(payload)
        if not o or any(len(MA_NV.findall(cau.get(k) or "")) != 1 for k in o):
            return None, ["mỗi câu cài sẵn phải có đúng một mã nhân viên để thay"]
        return o, []
    if cach == "tai_lieu":   # [ghi chú kỹ thuật] đúng 1 ô nhập: nhận toàn văn tài liệu + câu hỏi
        if len(o) != 1:
            return None, ["phải có đúng một ô nhập (nhận tài liệu + câu hỏi)"]
        return o, []
    if cach == "tinh_bang":   # [ghi chú kỹ thuật] ⇒ [mã ô câu hỏi, mã khối nhận file]
        fi = [k for k, v in loai.items() if v == "file_input_node"]
        if len(o) != 1 or len(fi) != 1:
            return None, ["phải có đúng một ô câu hỏi và đúng một khối nhận file"]
        return [o[0], fi[0]], []
    return None, ["cách nạp ô lạ"]


# [ghi chú kỹ thuật] thẻ HÀNH ĐỘNG "xuất kết quả vừa trả lời" — không phải workflow; chỉ đưa vào danh sách định tuyến
# khi cuộc chat ĐÃ có một câu trả lời KPI (kho.da_chay). Dạng file đọc bằng CODE từ chữ câu; xuất bằng hàm nhóm kỹ thuật (va_xuat_ket_qua.xuat — 0 AI, không tính lại).
THE_XUAT = {"ma": "xuat_ket_qua", "ten_tim": "Xuất kết quả vừa trả lời ra file",
            "lam_gi": "Xuất KẾT QUẢ VỪA TRẢ LỜI ở lượt trước trong cuộc trò chuyện ra file Excel / Word / PDF / PowerPoint — dùng đúng bảng số đã kiểm, không tính lại.",
            "khi_nao_dung": "Người dùng nhờ xuất / tải / gửi file / đổi sang dạng khác cho kết quả VỪA trả lời (vd 'xuất ra excel', 'word nữa', 'cho bản pdf').",
            "khong_dung_khi": "Câu hỏi MỚI về số liệu; xin bảng thưởng chính thức cả đội có người duyệt (⇒ KPI mẫu 6).",
            "vi_du": ["xuất kết quả này ra excel cho tôi", "word nữa", "cho tôi bản pdf"],
            "chay_tu_chat": True, "muc_toi_da_uoc": 0.0, "xuat_file": False, "co_nguoi_duyet": False, "goi_ra_ngoai": False, "gui_thay_nguoi_dung": False, "can_ma_nv": False}
DANG_CHU = (("pptx", r"\b(ppt|pptx|powerpoint|power point|slide|trình chiếu|trinh chieu)\b"), ("docx", r"\b(word|docx|doc)\b"),
            ("pdf", r"\bpdf\b"), ("xlsx", r"\b(excel|xlsx|xls|bảng tính|bang tinh)\b"))
TEN_DANG = {"xlsx": "Excel", "docx": "Word", "pdf": "PDF", "pptx": "PowerPoint"}
TEP_GIAY = 1800            # file đã xuất giữ 30 phút trong bộ nhớ máy chủ
TEP_TOI_DA = 30            # tối đa số file giữ (cũ nhất bỏ trước)


def dang_trong_cau(cau: str) -> str | None:
    c = (cau or "").lower()
    ds = [d for d, m in DANG_CHU if re.search(m, c)]
    return ds[0] if len(ds) == 1 else None   # 0 hoặc nhiều dạng ⇒ hỏi lại bằng nút


class Kho:
    """Lịch sử + phiếu + lượt đã chạy — trong bộ nhớ tiến trình (mất khi bật lại máy chủ: chấp nhận)."""

    def __init__(self, gio=time.time, ghi=None):
        self.ghi, self.dem_ls = ghi, {"cat_muc": 0, "bo_muc": 0}   # [ghi chú kỹ thuật] bộ đếm lịch sử bị cắt / bỏ lúc LƯU; ghi = hàm nhật ký của máy chủ
        self.ls_rut = {}   # conv ⇒ True khi lịch sử GỬI bộ định tuyến ở yêu cầu này bị cắt / bỏ (noi_may_chu đưa cờ lich_su_rut_gon vào kết quả lượt)
        self.gio, self.ls, self.phieu, self.da_chay, self.tep, self.cuoi = gio, {}, {}, {}, {}, {}   # cuoi: conv ⇒ phiên trả lời GẦN NHẤT (để xuất)
        self.cau_cuoi = {}   # [ghi chú kỹ thuật] conv ⇒ câu người dùng hỏi ở lượt trả lời gần nhất (in tiêu đề phần đầu file xuất)
        self.org_cuoi = {}   # [ghi chú kỹ thuật] conv ⇒ tổ chức của yêu cầu đã tạo lượt đó — _xuat chỉ xuất khi yêu cầu xuất thuộc ĐÚNG tổ chức ấy

    def cat_tep(self, conv, ten, raw) -> str:
        for k in [k for k, v in self.tep.items() if self.gio() > v["het"]]:
            self.tep.pop(k, None)
        while len(self.tep) >= TEP_TOI_DA:
            self.tep.pop(min(self.tep, key=lambda k: self.tep[k]["het"]), None)
        m = secrets.token_urlsafe(18)
        self.tep[m] = {"conv": conv, "ten": ten, "raw": raw, "het": self.gio() + TEP_GIAY}
        return m

    def lay_tep(self, conv, m):
        v = self.tep.get(m) if isinstance(m, str) else None
        return v if v and v["conv"] == conv and self.gio() <= v["het"] else None   # chỉ đúng cuộc chat đã xuất

    def phien_tra_loi_cuoi(self, conv) -> str | None:
        return self.cuoi.get(conv) if conv in self.ls else None

    def nho_cuoi(self, conv, out, cau=None, org=None):
        if out.get("loai") == "tra_loi" and out.get("phien"):
            self.cuoi[conv] = out["phien"]
            self.cau_cuoi[conv] = str(cau or "")[:2000]
            self.org_cuoi[conv] = org
        return out

    def _don(self):
        t = self.gio()
        for k in [k for k, v in self.ls.items() if t - v["t"] > LS_GIAY]:
            self.ls.pop(k, None); self.da_chay.pop(k, None); self.cuoi.pop(k, None); self.cau_cuoi.pop(k, None); self.org_cuoi.pop(k, None)
        for k in [k for k, v in self.phieu.items() if t > v["het"]]:
            self.phieu.pop(k, None)
        while len(self.ls) > LS_TOI_DA_CONV:
            cu = min(self.ls, key=lambda k: self.ls[k]["t"]); self.ls.pop(cu, None); self.da_chay.pop(cu, None); self.cuoi.pop(cu, None); self.cau_cuoi.pop(cu, None); self.org_cuoi.pop(cu, None)

    def lich_su(self, conv) -> list[dict]:
        self._don()
        return list((self.ls.get(conv) or {}).get("m", []))

    def lich_su_dt(self, conv) -> list[dict]:
        """Lịch sử để GỬI BỘ ĐỊNH TUYẾN (OpenRouter): mục nào có bản trung tính `dt` thì gửi bản đó. Lịch sử đầy đủ (lich_su) chỉ dùng trong máy chủ + gửi khối AI qua Bedrock."""
        return [{"vai": m["vai"], "noi_dung": m.get("dt") or m["noi_dung"]} for m in self.lich_su(conv)]

    def them(self, conv, vai, chu, dt=None):
        self._don()
        v = self.ls.setdefault(conv, {"t": self.gio(), "m": []})
        # [ghi chú kỹ thuật] giữ đủ câu trả lời (rào cỡ J.LS_KY_TU ký tự / mục, J.LS_SO_MUC mục — nay rất rộng) — cắt ngắn làm mất lời mời ở cuối. [ghi chú kỹ thuật] cắt / bỏ thì ĐẾM + ghi nhật ký (không ghi nội dung)
        cat, bo = int(len(str(chu)) > J.LS_KY_TU), int(len(v["m"]) + 1 > J.LS_SO_MUC)
        v["t"] = self.gio(); v["m"] = (v["m"] + [{"vai": vai, "noi_dung": str(chu)[:J.LS_KY_TU], **({"dt": dt} if dt else {})}])[-J.LS_SO_MUC:]
        while len(v["m"]) > 1 and sum(len(x["noi_dung"]) for x in v["m"]) > LS_KY_TU_MOT_CUOC:   # [ghi chú kỹ thuật] chốt RAM mỗi cuộc; mục MỚI nhất luôn giữ
            v["m"].pop(0); bo += 1
        if cat or bo:
            self.dem_ls["cat_muc"] += cat; self.dem_ls["bo_muc"] += bo
            if self.ghi:
                self.ghi(f"TRO-LY lịch sử (lúc lưu): cắt {cat} mục ở {J.LS_KY_TU} ký tự, bỏ {bo} mục cũ (quá {J.LS_SO_MUC} mục)")
        self._don()   # dọn SAU khi thêm ⇒ không vượt LS_TOI_DA_CONV

    def cap_phieu(self, conv, loai, lua_chon: list[str], muc=None, cau="") -> str:
        self._don()
        m = secrets.token_urlsafe(18)
        self.phieu[m] = {"conv": conv, "loai": loai, "lua_chon": list(lua_chon), "muc": muc, "cau": cau, "het": self.gio() + PHIEU_GIAY}
        return m

    def lay_phieu(self, m, conv) -> dict | None:
        """MỘT LẦN: lấy xong là xoá, kể cả khi sai conv (phiếu đã lộ sang conv khác ⇒ huỷ)."""
        self._don()
        p = self.phieu.pop(m, None) if isinstance(m, str) else None
        return p if p and p["conv"] == conv and self.gio() <= p["het"] else None


def _chuan(c: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", c or "")).strip().lower()


def _file_dang_mo(dep, conv):
    """[ghi chú kỹ thuật] File đang mở của cuộc trò chuyện — CHỈ khi đúng tổ chức của yêu cầu (dep["org"]) và thẻ hỏi tài liệu đã ghim + mở."""
    k = dep.get("tai_lieu")
    if not k or not dep.get("org") or not (dep["ghim"].get(TLC.MA_THE) or {}).get("mo"):
        return None
    return k.dang_mo(dep["org"], conv)


def _bang_dang_mo(dep, conv):
    """[ghi chú kỹ thuật] File bảng tính ĐANG MỞ của cuộc trò chuyện còn giữ byte gốc — và thẻ tính trên bảng đã ghim + mở, máy chủ có đường đưa file vào kho Capitol."""
    f = _file_dang_mo(dep, conv)
    if not f or f.get("raw") is None or not dep.get("dua_file") or not (dep["ghim"].get(TLC.MA_THE_TINH) or {}).get("mo"):
        return None
    return f


def _kho_cong_ty(dep) -> list:
    """[ghi chú kỹ thuật] Các kho BỀN chưa ẩn của tổ chức của yêu cầu (máy chủ trang lọc từ sổ kho). Thiếu phần nối / thẻ hỏi tài liệu chưa mở ⇒ []."""
    if not dep.get("ds_kho") or not dep.get("tai_lieu") or not dep.get("org") or not (dep["ghim"].get(TLC.MA_THE) or {}).get("mo"):
        return []
    try:
        return list(dep["ds_kho"](dep["org"]) or [])
    except Exception as e:  # noqa: BLE001 — không đọc được sổ kho ⇒ coi như không có kho (thẻ không gửi) NHƯNG phải ghi nhật ký (lỗi nuốt lặng từng làm thẻ kho biến mất)
        dep["ghi"](f"TRO-LY kho tài liệu: không lấy được danh sách kho ({type(e).__name__}) ⇒ không gửi thẻ kho")
        return []


def _chay_kho(cau, conv, dep):
    """Mở kho chung làm tài liệu đang mở của cuộc trò chuyện rồi chạy CHÍNH đường hỏi tài liệu (tìm ⇒ workflow hỏi tài liệu ⇒ kiểm chỗ dẫn)."""
    ds = _kho_cong_ty(dep)
    ma, r = dep["tai_lieu"].mo_kho_chung(dep["org"], conv, ds) if ds else (404, {})
    if ma != 200:
        chu = "Kho tài liệu của công ty hiện chưa có tài liệu nào. Bạn đưa tài liệu vào ở trang “Dữ liệu” (mục Tài liệu để hỏi đáp), hoặc kéo một file vào khung này."
        dep["kho"].them(conv, "tro_ly", chu); return 200, {"loai": "khong_co", "chu": chu}
    dep["ghi"](f"TRO-LY hỏi trên kho tài liệu: {r.get('so_tai_lieu')} tài liệu")
    return _chay(TLC.MA_THE, cau, conv, dep)


def _sau_tai_lieu(out, f_tl, tim=None):
    """Câu trả lời trên tài liệu: kiểm chỗ dẫn + câu trích bằng code ⇒ ô `tai_lieu`; bỏ nhãn "tạm tính" (nhãn của số KPI).
    [ghi chú kỹ thuật] tim = hồ sơ tìm (câu tìm, k, trang trả về) của lượt đi đường kho ⇒ gắn vào lượt (ô `tim`) cả khi lượt hỏng, để đo "tìm trượt" tách khỏi
    "AI trả lời sai"."""
    if tim and isinstance(out, dict):
        out = dict(out, tim=tim)
    if not f_tl or out.get("loai") != "tra_loi":
        return out
    out = dict(out, tai_lieu=dict(TLC.hau_kiem(out.get("chu") or "", f_tl), **({"chi_trang_tim_duoc": True} if f_tl["kq"].get("doan") else {})))
    out.pop("nhan", None)
    if not f_tl.get("kieu") and f_tl.get("raw_vb") is not None and TLC.can_goi_y_hinh(out.get("chu")):   # [ghi chú kỹ thuật]
        out = dict(out, chu=(out.get("chu") or "").rstrip() + "\n\n" + TLC.GOI_Y_HINH, goi_y_hinh=True)
    if f_tl.get("kieu") == "kho_chung":   # [ghi chú kỹ thuật] đổi [đoạn N] ra TÊN FILE + trang cho người đọc; nói rõ đã tìm trên bao nhiêu tài liệu
        nguon = f_tl.get("nguon_doan") or {}
        dan = sorted({int(n) for n in re.findall(r"\[đoạn (\d+)\]", out.get("chu") or "") if int(n) in nguon})
        duoi = "\n\n" + ("Nguồn: " + "; ".join(f"[đoạn {n}] = {nguon[n]}" for n in dan) + ". " if dan else "") + f"Đã tìm trên {len(f_tl.get('kho_ds') or [])} tài liệu trong kho của công ty."
        out = dict(out, chu=(out.get("chu") or "").rstrip() + duoi, kho_chung=True, workflow=dict(out.get("workflow") or {}, ten=TLC.TEN_THE_KHO))   # [A] "Đã chọn: Hỏi trên kho tài liệu của công ty"
    return out


def xu_ly(than, dep, org=None) -> tuple[int, dict]:
    """dep: the (danh sách thẻ gửi Jev, có ten_hien) · ghim (doc_ghim) · kho (Kho) · goi_jev(body, so_byte, cho_giay) ⇒ JSON — người gọi GIỮ CHỖ + ghi sổ
    usage.cost; ném TimeoutError khi quá giờ · pb(uuid) ⇒ {"payload", "so"} | None · toi_da(payload) ⇒ (mức, thiếu) · con_tran() ⇒ USD còn của trần ngày ·
    chay(uuid, text_inputs) ⇒ (http, {"session", "version"} | {"loi"}) — chay() của nút Chạy · trang_thai(phiên) · ket_qua(phiên) ·
    operator(cau, conv) ⇒ (http, dict) · ghi(chu) · ngu, gio · dem (dict đếm sự kiện)."""
    try:
        if not isinstance(than, dict):
            return 400, {"loai": "loi", "chu": CHU["loi"]}
        cau, conv = than.get("cau"), than.get("conv")
        if not (isinstance(conv, str) and MA_CONV.fullmatch(conv)):
            return 400, {"loai": "loi", "chu": CHU["loi"]}
        kho: Kho = dep["kho"]
        dep = dict(dep, org=org)   # [ghi chú kỹ thuật] tổ chức của CHÍNH yêu cầu này (may_chu truyền) — file đang mở chỉ thấy được trong đúng tổ chức
        if than.get("xuat") is not None:   # [ghi chú kỹ thuật] nút "Tải Excel" dưới câu trả lời: xuất THẲNG kết quả lượt gần nhất của chính cuộc trò chuyện (0 AI)
            if than.get("xuat") != "xlsx" or than.get("cau") is not None or than.get("phieu") is not None:
                return 400, {"loai": "loi", "chu": CHU["loi"]}
            return _xuat("Tải Excel", conv, dep, "xlsx")
        # --- đường TRẢ LỜI một phiếu (đối chiếu kỹ thuật): chỉ nhận mã phiếu + lựa chọn trong danh sách máy chủ đã đưa
        if than.get("phieu") is not None:
            if not (isinstance(than["phieu"], str) and MA_PHIEU.fullmatch(than["phieu"])):
                return 400, {"loai": "loi", "chu": CHU["phieu_het"]}
            p = kho.lay_phieu(than["phieu"], conv)
            chon = than.get("chon")
            if p is None or chon not in p["lua_chon"]:
                return 200, {"loai": "loi", "chu": CHU["phieu_het"]}
            if p["loai"] == "tien":
                if chon != "co":
                    kho.them(conv, "tro_ly", "Đã huỷ, không chạy."); return 200, {"loai": "khong_co", "chu": "Mình không chạy việc này. Bạn cần gì khác cứ hỏi nhé."}
                return _chay(p["ma"], p["cau"], conv, dep, muc_da_hoi=p["muc"])
            if p["loai"] == "xuat_dang":
                return _xuat(p["cau"], conv, dep, chon)
            if chon == "danh_sach":   # [ghi chú kỹ thuật] nút "Xem mọi việc mình làm được" — không chạy gì
                chu = danh_sach_viec(dep); kho.them(conv, "tro_ly", chu)
                return 200, {"loai": "khong_co", "chu": chu, "danh_sach_viec": True}
            if chon == "dung_moi":
                return _dung_moi(p["cau"], conv, dep)
            if chon == "xuat_ket_qua":
                return _xuat(p["cau"], conv, dep, dang_trong_cau(p["cau"]))
            if chon == TLC.MA_THE_KHO:
                return _chay_kho(p["cau"], conv, dep)
            return _chay(chon, p["cau"], conv, dep)
        # --- câu mới
        if not isinstance(cau, str):
            return 400, {"loai": "loi", "chu": CHU["tu_choi"]}
        r0 = C.bac0(cau)
        if r0 is not None:
            return 200, {"loai": "loi", "chu": CHU["tu_choi"]}
        ls = kho.lich_su(conv)
        ls_dt = kho.lich_su_dt(conv)   # [ghi chú kỹ thuật] bản gửi bộ định tuyến
        kho.them(conv, "nguoi_dung", cau)
        if " ".join(cau.lower().split()).strip(" .!?“”\"'") in CAU_XEM_HET:   # [ghi chú kỹ thuật] lối xem ĐỦ danh sách việc — bằng code, không gọi bộ định tuyến, 0 đồng
            chu = danh_sach_viec(dep, day_du=True); kho.them(conv, "tro_ly", chu)
            return 200, {"loai": "khong_co", "chu": chu, "danh_sach_viec": True}
        ghim = dep["ghim"]
        muc_luc_chay = lambda ma: _muc(TLC.MA_THE if ma == TLC.MA_THE_KHO else ma, dep)   # noqa: E731  [ghi chú kỹ thuật] thẻ kho chạy CHÍNH workflow hỏi tài liệu
        # [ghi chú kỹ thuật] thẻ có mã NV gắn cứng chỉ gửi định tuyến khi câu / lịch sử có đúng mã đó (vd "Tối ưu KPI cho NV02" với câu hỏi về NV05 ⇒ bỏ thẻ)
        # [ghi chú kỹ thuật] câu mới CÓ mã NV ⇒ chỉ xét câu mới (lịch sử NV02 rồi hỏi "còn NV05?" ⇒ không đưa thẻ NV02); câu mới không mã ⇒ xét cả lịch sử
        co_ma = re.search(r"(?<![A-Z0-9])NV\d{2}(?![A-Z0-9])", cau.upper())
        chu_ls = (cau if co_ma else cau + " " + " ".join(m.get("noi_dung", "") for m in ls)).upper()
        the_cau = [t for t in dep["the"] if not (ghim.get(t["ma"]) or {}).get("ma_nv_co_dinh")
                   or any(re.search(rf"(?<![A-Z0-9]){m}(?![A-Z0-9])", chu_ls) for m in ghim[t["ma"]]["ma_nv_co_dinh"])]
        if dep.get("xuat") and kho.phien_tra_loi_cuoi(conv):
            the_cau = the_cau + [THE_XUAT]   # chỉ khi đã có kết quả để xuất
        if _file_dang_mo(dep, conv):
            # [ghi chú kỹ thuật] đang mở file ⇒ KHÔNG gửi thẻ "Hỏi dữ liệu thử"
            # (thẻ không chạy được từ khung chat). Chỉ rút khi đang mở file, không rút hẳn: lời gửi bộ định tuyến (chon_jev) có nhắc tên thẻ ⇒ rút hẳn là đổi lời + phải đo lại trạng thái không file.
            the_cau = [t for t in the_cau if t.get("ma") != "hoi_du_lieu_thu"] + [TLC.THE_TAI_LIEU]   # [ghi chú kỹ thuật] chỉ khi cuộc trò chuyện ĐANG có file mở
            if _bang_dang_mo(dep, conv):
                the_cau = the_cau + [TLC.THE_TINH]   # [ghi chú kỹ thuật] file đang mở là bảng tính còn byte gốc ⇒ thêm việc TÍNH trên cả bảng
        elif _kho_cong_ty(dep):
            the_cau = the_cau + [TLC.THE_KHO]   # [ghi chú kỹ thuật] hỏi tự do trên kho nạp sẵn — chỉ khi tổ chức có kho bền và không có file đang mở
        try:
            r = J.chon(cau, the=the_cau, goi_jev=lambda b, n: dep["goi_jev"](b, n, CHO_JEV), muc=muc_luc_chay, lich_su=ls_dt, tu_chay=True)
            if J.LS_LAN_CUOI["cat_muc"] or J.LS_LAN_CUOI["bo_muc"]:   # [ghi chú kỹ thuật] lịch sử gửi bộ định tuyến bị cắt / bỏ ⇒ đếm + nhật ký
                dep["dem"]["ls_gui_cat_muc"] = dep["dem"].get("ls_gui_cat_muc", 0) + J.LS_LAN_CUOI["cat_muc"]
                dep["dem"]["ls_gui_bo_muc"] = dep["dem"].get("ls_gui_bo_muc", 0) + J.LS_LAN_CUOI["bo_muc"]
                dep["ghi"](f"TRO-LY lịch sử (lúc gửi định tuyến): cắt {J.LS_LAN_CUOI['cat_muc']} mục, bỏ {J.LS_LAN_CUOI['bo_muc']} mục")
                kho.ls_rut[conv] = True
        except TimeoutError:
            dep["dem"]["jev_qua_gio"] = dep["dem"].get("jev_qua_gio", 0) + 1
            return 200, {"loai": "loi", "chu": CHU["ban"]}
        except Exception as e:  # noqa: BLE001 — Jev / OpenRouter lỗi ⇒ KHÔNG tự chạy
            dep["dem"]["jev_loi"] = dep["dem"].get("jev_loi", 0) + 1; dep["ghi"](f"TRO-LY Jev lỗi {type(e).__name__}")
            return 200, {"loai": "loi", "chu": CHU["loi"]}
        if r["loai"] == "tu_choi":
            return 200, {"loai": "loi", "chu": CHU["tu_choi"]}
        if r["loai"] == "khong_co":
            kho.them(conv, "tro_ly", CHU["khong_co"]); return 200, {"loai": "khong_co", "chu": CHU["khong_co"]}
        if r["loai"] == "hoi_he_thong":   # [ghi chú kỹ thuật] "đang có những workflow nào", "bạn làm được gì" ⇒ trả lời bằng CODE
            chu = danh_sach_viec(dep); kho.them(conv, "tro_ly", chu)
            dep["ghi"]("TRO-LY câu hỏi về chính hệ thống ⇒ trả danh sách việc (không chạy lượt)")
            return 200, {"loai": "khong_co", "chu": chu, "danh_sach_viec": True}
        if r["loai"] == "dung_moi":
            return _dung_moi(cau, conv, dep)
        # thẻ xuất: định tuyến chắc ⇒ xuất (thiếu dạng ⇒ nút dạng); chưa chắc nhưng câu NÊU RÕ đúng 1 dạng file ⇒ xuất; còn lại ⇒ hỏi làm rõ như thường
        if r.get("ma") == "xuat_ket_qua" and (r["loai"] == "chay" or (r["loai"] == "hoi_lai" and dang_trong_cau(cau))):
            return _xuat(cau, conv, dep, dang_trong_cau(cau))
        if r["loai"] == "chay" and r.get("ma") == TLC.MA_THE_KHO:
            return _chay_kho(cau, conv, dep)
        if r["loai"] == "chay":
            return _chay(r["ma"], cau, conv, dep)
        # hoi_lai: tiền / gửi ngoài / làm rõ
        if r.get("kieu_hoi") in ("tien", "gui_ngoai") and r.get("ma") in ghim and not ghim[r["ma"]].get("mo"):   # [ghi chú kỹ thuật] thẻ ĐÓNG ⇒ không hỏi tiền
            kho.them(conv, "tro_ly", CHU["thieu_muc"]); return 200, {"loai": "loi", "chu": CHU["thieu_muc"]}
        if r.get("kieu_hoi") == "tien" and r["ma"] in ghim:
            m = muc_luc_chay(r["ma"])
            if m is None:   # [ghi chú kỹ thuật] không tính được mức ⇒ báo thẳng, không hỏi tiền
                kho.them(conv, "tro_ly", CHU["thieu_muc"]); return 200, {"loai": "loi", "chu": CHU["thieu_muc"]}
            ph = kho.cap_phieu(conv, "tien", ["co", "khong"], muc=m, cau=cau); kho.phieu[ph]["ma"] = r["ma"]
            chu = f"Việc này tốn tối đa khoảng {_usd(m)} USD. Bạn muốn chạy không?"
            kho.them(conv, "tro_ly", chu)
            return 200, {"loai": "hoi", "chu": chu, "phieu": ph, "lua_chon": [{"chon": "co", "ten": "Chạy"}, {"chon": "khong", "ten": "Không"}],
                         "workflow": {"ten": ghim[r["ma"]]["ten"]}}
        co_xuat = bool(dep.get("xuat") and kho.phien_tra_loi_cuoi(conv))
        _mt = J._mo_ta() if hasattr(J, "_mo_ta") else {}
        _thu = {t["ma"] for t in dep["the"] if str((_mt.get(t.get("ten_goc") or t.get("ten_tim")) or {}).get("lam") or "").startswith("Mẫu thử")}   # thẻ mẫu thử: không làm nút
        _co_kho = bool(_kho_cong_ty(dep)) and not _file_dang_mo(dep, conv)
        lc = [x for x in (r.get("ung_vien") or []) if x == TLC.MA_THE_KHO and _co_kho or x not in _thu and (x in ghim and ghim[x].get("mo")) or x == "dung_moi" or (x == "xuat_ket_qua" and co_xuat)][:3]   # [gói 6] bỏ thẻ đóng
        ten_nut = lambda x: (TEN_NUT_DANH_SACH if x == "danh_sach" else TLC.TEN_THE_KHO if x == TLC.MA_THE_KHO else "Tạo / sửa quy trình mới" if x == "dung_moi" else   # noqa: E731
                             ("Xuất kết quả vừa rồi ra file" if x == "xuat_ket_qua" else ghim[x]["ten"]))
        hoi_nv = r.get("kieu_hoi") == "lam_ro" and "mã nhân viên" in (r.get("ly_do") or "")
        conf = (r.get("jev") or {}).get("conf")
        # [ghi chú kỹ thuật] KHÔNG CHẮC THẤP: bộ định tuyến chọn "không thẻ nào" / "tạo mới" mà không chắc, hoặc độ tin < 0,5 (vd câu hỏi VỀ chính xưởng:
        # "đang có những workflow nào", "bạn làm được gì") ⇒ trả DANH SÁCH VIỆC bằng code; nút chọn nhanh chỉ khi có ≥ 2 ứng viên (không bao giờ đúng một nút)
        thap = not hoi_nv and (r.get("ma") in (None, "dung_moi", "hoi_ve_he_thong") or (isinstance(conf, (int, float)) and conf < NGUONG_KHONG_CHAC_THAP))
        mot_the = not hoi_nv and len(lc) == 1 and r.get("ma") not in (None, "dung_moi", "hoi_ve_he_thong")   # đúng MỘT ứng viên và bộ định tuyến có nghiêng về chính thẻ đó
        if mot_the:   # ⇒ hỏi thẳng + lối xem mọi việc (không đưa đúng MỘT nút dưới câu "việc nào dưới đây"); giữ được nút "Xuất kết quả vừa rồi" cho câu nối tiếp
            thap = False
            chu = f"Mình chưa chắc — có phải bạn muốn “{ten_nut(lc[0])}” không?"
            lc = lc + ["danh_sach"]
        elif thap:
            lc = lc if len(lc) >= 2 else []
            chu = danh_sach_viec(dep, "Mình chưa chắc bạn muốn làm việc nào. Mình làm được các việc dưới đây") + ("\n\nHoặc chọn nhanh một việc gần với câu của bạn:" if lc else "")
            dep["ghi"](f"TRO-LY hỏi lại: không chắc thấp (độ tin {conf}) ⇒ trả danh sách việc, {len(lc)} nút")
        elif hoi_nv:
            chu = r.get("ly_do")
        else:
            chu = "Bạn muốn mình làm việc nào dưới đây?" if lc else "Bạn nói rõ thêm giúp mình muốn xem gì nhé."
        ph = kho.cap_phieu(conv, "lam_ro", lc, cau=cau) if lc else None
        kho.them(conv, "tro_ly", chu)
        return 200, {"loai": "khong_co" if thap and not lc else "hoi", "chu": chu, "phieu": ph, "lua_chon": [{"chon": x, "ten": ten_nut(x)} for x in lc],
                     **({"danh_sach_viec": True} if thap else {})}
    except Exception as e:  # noqa: BLE001 — ĐÓNG KHI LỖI
        dep["ghi"](f"TRO-LY lỗi {type(e).__name__}")
        return 200, {"loai": "loi", "chu": CHU["loi"]}


def _muc(ma, dep):
    g = dep["ghim"].get(ma)
    if not g:
        return None
    v = dep["pb"](g["uuid"])
    if not v or sha_payload(v.get("payload")) != g["sha"]:
        return None   # khác sha ⇒ không tính mức ⇒ can_tu_chay hỏi lại (không chạy)
    muc, thieu = dep["toi_da"](v["payload"])
    return None if thieu else float(muc)


def conv_operator(conv: str) -> str:
    """[ghi chú kỹ thuật] chat_operator chỉ nhận conv 32 hex / UUID (_CONV) ⇒ đổi conv của khung chat sang 32 hex CỐ ĐỊNH theo conv (băm) — cùng cuộc chat
    luôn cùng mã Operator (giữ lịch sử dựng), mọi dạng conv hợp lệ của trang đều qua."""
    return hashlib.sha256(("tro_ly:" + conv).encode("utf-8")).hexdigest()[:32]


def ma_nv_cua(cau) -> list[str]:
    """[ghi chú kỹ thuật] mã NV khác nhau trong CÂU MỚI. KHÔNG lấy từ lịch sử ("phiếu của tôi", "chị Lan thì sao" sẽ âm thầm chạy cho người cũ) ⇒
    câu không có mã ⇒ hỏi lại."""
    return sorted(set(MA_NV_CAU.findall((cau or "").upper())))


def _nap_o(ma, cach, payload, cau, conv, dep):
    """(text_inputs, lời hỏi / từ chối | None). giu_nguyen ⇒ {} (may_chu dùng câu cài sẵn). thay_nv ⇒ thay đúng 1 mã trong mỗi câu cài sẵn."""
    if cach == "giu_nguyen":
        return {}, None
    ds = ma_nv_cua(cau)
    if len(ds) != 1:
        return None, CHU["hoi_ma_nv"]
    try:
        nv = dep["ds_nv"]()
    except Exception:  # noqa: BLE001
        nv = None
    if not nv:
        return None, CHU["khong_doc_nv"]
    if ds[0] not in set(nv):
        return None, CHU["khong_co_nv"].format(ma=ds[0])
    ti = {k: MA_NV.sub(ds[0], v, count=1) for k, v in o_nhap(payload).items()}
    dep["ghi"](f"TRO-LY {ma} thay_nv ⇒ {ds[0]} · sha câu " + ",".join(hashlib.sha256(v.encode()).hexdigest()[:8] for v in ti.values()))
    return ti, None


def _xuat(cau, conv, dep, dang):
    """Xuất kết quả lượt trả lời GẦN NHẤT của chính cuộc chat (máy chủ tự lấy phiên — không nhận từ trình duyệt). 0 AI."""
    kho = dep["kho"]
    ph = kho.phien_tra_loi_cuoi(conv)
    if ph and dep.get("org") and kho.org_cuoi.get(conv) not in (None, dep["org"]):   # [ghi chú kỹ thuật] lượt đó do tổ chức KHÁC tạo ⇒ coi như chưa có kết quả để xuất
        dep["ghi"]("TRO-LY xuất: cuộc trò chuyện này có kết quả của tổ chức khác — không xuất"); ph = None
    if not ph:
        kho.them(conv, "tro_ly", CHU["xuat_chua_co"]); return 200, {"loai": "khong_co", "chu": CHU["xuat_chua_co"]}
    if dang not in TEN_DANG:
        lc = list(TEN_DANG)
        m = kho.cap_phieu(conv, "xuat_dang", lc, cau=cau)
        kho.them(conv, "tro_ly", CHU["xuat_hoi_dang"])
        return 200, {"loai": "hoi", "chu": CHU["xuat_hoi_dang"], "phieu": m, "lua_chon": [{"chon": d, "ten": TEN_DANG[d]} for d in lc]}
    try:
        kq_x = dep["xuat"](ph, dang, kho.cau_cuoi.get(conv))   # [ghi chú kỹ thuật] kèm câu vừa hỏi; hàm trả (tên, byte[, thông tin])
        ten, raw, tt_x = (kq_x[0], kq_x[1], kq_x[2] if len(kq_x) > 2 and isinstance(kq_x[2], dict) else {})
    except Exception as e:  # noqa: BLE001 — LoiXuat nhóm kỹ thuật mang lời thường; lỗi khác ⇒ câu chung
        chu = str(e) if type(e).__name__ == "LoiXuat" and str(e) else CHU["loi"]
        dep["ghi"](f"TRO-LY xuất {dang} lỗi {type(e).__name__}"); kho.them(conv, "tro_ly", chu)
        return 200, {"loai": "loi", "chu": chu}
    m = kho.cat_tep(conv, ten, raw)
    import hashlib as _hl   # [ghi chú kỹ thuật] mỗi lần xuất THÀNH CÔNG ghi một dòng nhật ký (trước: chỉ ghi khi lỗi ⇒ không truy được ai đã xuất gì)
    dep["ghi"](f"TRO-LY xuất {dang} xong: phiên {str(ph)[:8]} · {len(raw)} byte · sha {_hl.sha256(raw).hexdigest()[:8]} · phần theo câu hỏi: "
               f"{'có' if tt_x.get('phan_cau_hoi') else 'không'}{' · ' + str(tt_x['ly_do'])[:120] if tt_x.get('ly_do') else ''}")
    if tt_x.get("phan_cau_hoi"):   # [ghi chú kỹ thuật] bộ dựng báo ĐÃ in phần theo câu hỏi (đã gọi lại công cụ và so khớp); in được MỘT PHẦN thì có thêm ly_do
        chu = (f"Đã xuất kết quả vừa rồi ra {TEN_DANG[dang]}. PHẦN ĐẦU file là đúng nội dung câu bạn vừa hỏi (số lấy từ phần tính thưởng, không phải chữ AI viết); "
               "PHẦN SAU là bảng thưởng cả đội của kỳ đó (mọi nhân viên, tên + lương). Trạng thái số liệu (tạm tính / đã duyệt) ghi ngay đầu file.")   # [ghi chú kỹ thuật] không ghi cứng trạng thái
        if tt_x.get("ly_do"):
            chu += " Lưu ý: " + str(tt_x["ly_do"])[:300].rstrip(".") + "."
    else:
        chu = (f"Đã xuất kết quả vừa rồi ra {TEN_DANG[dang]}. File gồm BẢNG THƯỞNG CẢ ĐỘI của kỳ đó (mọi nhân viên, tên + lương) — "
               "không chỉ người vừa hỏi; số lấy đúng bảng đã kiểm, không tính lại.")   # [ghi chú kỹ thuật] nói rõ phạm vi file
        if tt_x.get("ly_do"):
            chu += " File này CHƯA có phần riêng cho câu bạn vừa hỏi: " + str(tt_x["ly_do"])[:300].rstrip(".") + "."
    kho.them(conv, "tro_ly", chu)
    return 200, {"loai": "tep", "chu": chu, "tep": [{"id": m, "ten": ten}], "phien": ph}


NGUONG_KHONG_CHAC_THAP = 0.5   # [ghi chú kỹ thuật] dưới mức này bộ định tuyến gần như không biết câu thuộc việc nào ⇒ đưa DANH SÁCH VIỆC thay vì nút thẻ gần nhất
TEN_NUT_DANH_SACH = "Xem mọi việc mình làm được"


_GIU_HOA = {"KPI", "NV", "LPPC", "ASO", "PC", "BT", "ON", "PDF", "CSV", "JSON", "AI", "ALL", "USD"}   # chữ viết tắt GIỮ in hoa
CAU_XEM_HET = {"xem hết", "xem het", "xem hết việc", "xem het viec", "xem đủ", "xem du", "xem hết danh sách", "xem het danh sach"}
MOI_NHOM_HIEN = 6


def _ha_hoa(chu: str) -> str:
    """Chữ IN HOA để nhấn mạnh trong mô tả thẻ ("MỘT", "TỪNG NGƯỜI", "CƠ HỘI") ⇒ chữ thường; chữ viết tắt (KPI, NV02, PC_BT, LPPC…) giữ nguyên."""
    def doi(m):
        w = m.group(0)
        return w if w in _GIU_HOA else w.lower()
    return re.sub(r"(?<![\w])[^\W\d_]{2,}(?![\w])", lambda m: doi(m) if m.group(0).isupper() else m.group(0), chu)


def danh_sach_viec(dep, mo_dau="Mình làm được các việc dưới đây", day_du=False) -> str:
    """[ghi chú kỹ thuật] Danh sách việc khung chat ĐANG làm được — dựng bằng CODE từ thẻ đã ghim và đang MỞ (dep["ghim"]) +
    thuộc tính thẻ (dep["the"]); không gọi AI. Nhóm theo THUỘC TÍNH THẺ LÚC CHẠY (xuất file / có bước duyệt) — thẻ đổi mẫu thì nhóm tự đổi. day_du=False ⇒ mỗi nhóm
    MOI_NHOM_HIEN dòng đầu + "… và N việc khác". Thẻ đóng / không chạy được từ khung chat / thẻ mẫu thử không liệt kê."""
    ghim, tt = dep["ghim"], {t["ma"]: t for t in dep["the"]}
    try:
        mo_ta = J._mo_ta()   # mô tả lời thường của thẻ riêng (mo_ta_the.json, khoá theo tên mẫu) — cùng nguồn bộ định tuyến dùng
    except Exception:  # noqa: BLE001 — thiếu file mô tả ⇒ vẫn ra danh sách bằng tên
        mo_ta = {}
    nhom = {"nhanh": [], "duyet": [], "file": []}
    for ma, g in ghim.items():
        t = tt.get(ma) or {}
        if not g.get("mo") or t.get("chay_tu_chat") is False or ma == "hoi_tai_lieu":
            continue
        ten = re.sub(r"^KPI mẫu \d+(?: \(khung chat\))? — ", "", str(g.get("ten") or ma)).strip()
        lam = str(t.get("lam_hien") or (mo_ta.get(t.get("ten_goc") or t.get("ten_tim")) or {}).get("lam") or t.get("lam_gi") or "").strip()
        if lam.startswith("Mẫu thử"):   # thẻ dựng để thử bộ định tuyến — không phải việc cho người dùng
            continue
        lam = re.sub(r"\s*(Trả lời ngay\.|Lượt chạy DỪNG chờ người phụ trách duyệt rồi mới trả câu trả lời\.)\s*$", "", lam).strip()
        lam = re.sub(r"\s*\((?:số )?tạm tính[^)]*\)", "", lam).strip()   # đuôi "(số tạm tính, đã qua khối kiểm số bằng code)" — tiêu đề nhóm đã nói
        lam = _ha_hoa(lam)
        dong_viec = f"- {lam[:1].upper() + lam[1:220].rstrip('.')}. *(thẻ “{ten}”)*" if lam and lam != ten else f"- {ten}"
        nhom["file" if (g.get("xuat_file") or t.get("xuat_file")) else "duyet" if t.get("co_nguoi_duyet") else "nhanh"].append(dong_viec)
    so = sum(len(v) for v in nhom.values())
    an = 0

    def phan(ds):
        nonlocal an
        if day_du or len(ds) <= MOI_NHOM_HIEN + 1:
            return ds
        an += len(ds) - MOI_NHOM_HIEN
        return ds[:MOI_NHOM_HIEN] + [f"- … và {len(ds) - MOI_NHOM_HIEN} việc khác."]
    dong = [f"{mo_dau} ({so} việc về thưởng / KPI, cộng hỏi tài liệu và dựng quy trình mới). Bạn cứ hỏi bằng lời thường, mình tự chọn việc phù hợp:", ""]
    if nhom["nhanh"]:
        dong += ["**Hỏi là có câu trả lời ngay (số tạm tính, đã qua bước kiểm số):**"] + phan(nhom["nhanh"]) + [""]
    if nhom["duyet"]:
        dong += ["**Có bước chờ người phụ trách duyệt rồi mới trả lời:**"] + phan(nhom["duyet"]) + [""]
    if nhom["file"]:
        dong += ["**Ra bảng thưởng kèm file Excel:**"] + phan(nhom["file"]) + [""]
    so_kho = len(_kho_cong_ty(dep))
    dong += ["**Tài liệu:** " + (f"hỏi thẳng về {so_kho} tài liệu công ty đã nạp sẵn trong kho (ví dụ “báo cáo quý 3 nói gì về doanh thu?”), hoặc " if so_kho else "")
             + "kéo một file (PDF, Word, Excel / CSV, ảnh, trang web, JSON) vào khung này rồi hỏi về nội dung của nó.", "",
             "**Quy trình mới:** tả việc bạn cần (ví dụ “tạo quy trình tóm tắt văn bản”), mình dựng nháp để bạn xem.", "",
             "Ví dụ câu hỏi: “Tháng 10 NV02 được lương kinh doanh bao nhiêu?” · “Tổng thưởng cả đội tháng 10?” · “Xuất bảng thưởng cả đội ra Excel”."]
    if an:
        dong += ["", f"Gõ “xem hết” để xem đủ {so} việc."]
    return "\n".join(dong)


def _tu_xuat_file(out, g, phien, cau, conv, dep):
    """[ghi chú kỹ thuật] Thẻ có cờ xuất file (ghim) và lượt đã TRẢ LỜI (không phải chờ duyệt / lỗi) ⇒ tự xuất Excel
    NHÁP từ gói số đã qua kiểm của chính lượt (bộ dựng nhóm kỹ thuật qua dep["xuat"]; 0 đồng AI, không lượt mới). Thành công ⇒ thêm ô tep + một câu nói rõ file là bản nháp
    chưa có người duyệt. Không xuất được ⇒ GIỮ câu trả lời, thêm một dòng lý do thật. Không có bộ xuất / thẻ không xuất file / lượt chưa trả lời ⇒ trả nguyên."""
    if not (isinstance(out, dict) and out.get("loai") == "tra_loi" and g.get("xuat_file") and dep.get("xuat") and phien):
        return out
    kho = dep["kho"]
    try:
        kq_x = dep["xuat"](phien, "xlsx", cau)
        ten, raw = kq_x[0], kq_x[1]
    except Exception as e:  # noqa: BLE001 — LoiXuat mang lời thường; lỗi khác ⇒ câu chung; KHÔNG làm hỏng câu trả lời
        ly = str(e) if type(e).__name__ == "LoiXuat" and str(e) else "bước dựng file gặp lỗi"
        dep["ghi"](f"TRO-LY tự xuất xlsx sau lượt {str(phien)[:8]} lỗi {type(e).__name__}")
        return dict(out, chu=out["chu"].rstrip() + "\n\nChưa kèm được file Excel: " + ly.rstrip(".") + ". Bạn có thể gõ “xuất kết quả này ra excel” để thử lại.", tu_xuat_loi=True)
    import hashlib as _hl
    m = kho.cat_tep(conv, ten, raw)
    dep["ghi"](f"TRO-LY tự xuất xlsx xong: phiên {str(phien)[:8]} · {len(raw)} byte · sha {_hl.sha256(raw).hexdigest()[:8]}")
    # [ghi chú kỹ thuật] file ĐÃ dựng được ⇒ dòng của CODE đứng ĐẦU câu trả lời
    return dict(out, chu="**File Excel bảng thưởng cả đội đã có ở nút tải bên dưới** — bản NHÁP, số tạm tính, CHƯA có người duyệt.\n\n" + out["chu"].strip(), tep=[{"id": m, "ten": ten}])


def _dung_moi(cau, conv, dep):
    code, r = dep["operator"](cau, conv_operator(conv))
    chu = (r or {}).get("tra_loi") or (r or {}).get("assistant_reply") or (r or {}).get("loi") or CHU["loi"]
    dep["kho"].them(conv, "tro_ly", chu)
    return code, dict(r or {}, loai="dung_moi", chu=chu)


def _nhanh_cua(payload, ma_ra, cau_o) -> str | None:
    """Câu của ô nhập là gốc của khối Kết quả (lần ngược dây) — để mở đầu phần trả lời của nhánh. Không có / nhiều ô ⇒ None."""
    truoc = {}
    for e in payload.get("edges") or []:
        truoc.setdefault(e.get("target"), set()).add(e.get("source"))
    thay_, goc, da = [ma_ra], set(), set()
    while thay_:
        x = thay_.pop()
        if x in da:
            continue
        da.add(x)
        if x in cau_o:
            goc.add(x); continue
        thay_ += list(truoc.get(x) or ())
    return cau_o[next(iter(goc))] if len(goc) == 1 else None


def _qua_rao(payload, ma_ra, kq, tin_khoi=None) -> dict:
    """[ghi chú kỹ thuật] khối Kết quả KHÔNG có giá trị (bị bỏ qua vì lượt dừng ở nhánh khác) ⇒ lấy câu đã qua Rào của CHÍNH nhánh:
    đúng 1 khối Rào nối thẳng vào khối Kết quả, Rào chế độ code, dat is True VÀ ket_qua_dich_vu.dat is True, Rào nhận đúng 1 khối Trợ lý, giá trị
    Trợ lý là chữ ⇒ {"kieu": "chu_qua_rao", "v": chữ}. Đã đo lượt bản tin 98ca14a0: 3/3 nhánh cau_tra_loi của gói == chữ Trợ lý, bảng số trùng.
    Thiếu bất kỳ điều nào ⇒ {} (nhánh "chưa đọc được")."""
    loai = {n.get("id"): (((n or {}).get("data") or {}).get("struct") or {}).get("node_id") for n in payload.get("nodes") or []}
    truoc = {}
    for e in payload.get("edges") or []:
        truoc.setdefault(e.get("target"), []).append(e.get("source"))
    rao = [x for x in truoc.get(ma_ra) or [] if loai.get(x) == "guardrails_node"]
    if len(rao) != 1 or len(truoc.get(ma_ra) or []) != 1:
        return {}
    g = (kq.get(rao[0]) or {}).get("v")
    if not (isinstance(g, dict) and g.get("che_do") == "code" and g.get("dat") is True
            and isinstance(g.get("ket_qua_dich_vu"), dict) and g["ket_qua_dich_vu"].get("dat") is True):
        return {}
    ai = [x for x in truoc.get(rao[0]) or [] if loai.get(x) == "agent_node"]
    if len(ai) != 1:
        return {}
    # [ghi chú kỹ thuật] Rào chế độ code không ghi câu đã kiểm ⇒ chỉ tin "chữ Trợ lý = chữ Rào nhận" khi CẢ HAI khối có đúng 1 kết quả của CHÍNH lượt này
    # (bảng node_result: COMPLETED, không cached_from lượt khác). Không kiểm được ⇒ chưa đọc được.
    try:
        if not (tin_khoi and tin_khoi([rao[0], ai[0]]) is True):
            return {}
    except Exception:  # noqa: BLE001
        return {}
    o = kq.get(ai[0]) or {}
    return {"kieu": "chu_qua_rao", "v": o["v"]} if o.get("kieu") == "chu" and isinstance(o.get("v"), str) and o["v"].strip() else {}


def _doc_nhieu_nhanh(ra_ds, kq, v, phien, wf, cau_o, tin_khoi=None):
    """[ghi chú kỹ thuật] lượt NHIỀU khối Kết quả: ghép theo thứ tự payload. Gói KPI chưa kiem.dat ⇒ nhánh đó chỉ một câu "chưa đọc được"
    (KHÔNG chữ / bảng / số của nhánh). Không nhánh nào đọc được ⇒ khong_doc. Không dựng r.the (nhiều gói)."""
    phan, doc_duoc, ky_ds = [], 0, []   # [ghi chú kỹ thuật] kỳ của từng nhánh đọc được
    for n in ra_ds:
        o = kq.get(n.get("id")) or _qua_rao(v["payload"], n.get("id"), kq, tin_khoi)
        dau = _nhanh_cua(v["payload"], n.get("id"), cau_o)
        chu = None
        if o.get("kieu") == "kpi":
            goi = o.get("v") if isinstance(o.get("v"), dict) else {}
            if (goi.get("kiem") or {}).get("dat") is True and isinstance(goi.get("cau_tra_loi"), str) and goi["cau_tra_loi"].strip():
                chu = goi["cau_tra_loi"].strip(); ky_ds.append(ky_cua_goi(goi))
        elif o.get("kieu") == "chu_qua_rao":
            chu = o["v"].strip()   # câu Rào chế độ code đã kiểm (dat True) — cùng nghĩa với cau_tra_loi của gói khi lượt chạy hết
        elif o.get("kieu") == "chu" and isinstance(o.get("v"), str) and o["v"].strip():
            chu = o["v"].strip() + "\n\n" + CHU["chua_kiem_so"]   # [ghi chú kỹ thuật] số không qua Rào chế độ code
        doc_duoc += chu is not None
        phan.append(((f"**{dau.strip()[:300]}**\n\n") if dau else "") + (chu if chu else CHU["nhanh_chua_doc"]))
    if not doc_duoc:
        return {"loai": "loi", "chu": CHU["khong_doc"], "phien": phien, "workflow": wf}
    ky = ky_ds[0] if ky_ds and all(k == ky_ds[0] for k in ky_ds) and len(ky_ds) == doc_duoc else {"khong_ro": True}   # nhánh nào không qua gói số / kỳ lệch nhau ⇒ không khẳng định
    return {"loai": "tra_loi", "chu": "\n\n---\n\n".join(phan), "nhan": "tạm tính", "phien": phien, "workflow": wf, "ky": ky}


def _chu_luot_hong(phien, dep) -> str:
    """[ghi chú kỹ thuật] Lượt không xong: nếu trang ĐÃ làm rõ được lý do (loi_de_hieu: Rào chặn đúng luật, lớp chặn lỗi mềm, rào file) thì nói
    lý do đó ra khung chat; không làm rõ được ⇒ câu chung như cũ (KHÔNG đưa chữ lỗi thô của hệ thống ra chat)."""
    try:
        ly = dep["ly_do_hong"](phien) if dep.get("ly_do_hong") else None
    except Exception:  # noqa: BLE001
        ly = None
    # [ghi chú kỹ thuật] lý do của LỚP KIỂM SỐ ("Bị Rào CHẶN …") mang chính con số bị chặn ⇒ KHÔNG đưa ra khung chat (số chưa qua kiểm); chi tiết chỉ ở màn dấu vết
    if isinstance(ly, str) and ly.lstrip().startswith("Bị Rào CHẶN"):
        return CHU["rao_giu_lai"]   # [ghi chú kỹ thuật] nói thật là bị giữ lại ở bước kiểm số; không lặp lại lý do thô (có con số chưa kiểm)
    if isinstance(ly, str) and ly.strip() and not ly.lstrip().startswith("Bị Rào CHẶN"):
        return "Lượt chạy đã dừng giữa chừng nên chưa có câu trả lời. Lý do: " + ly.strip()[:600] + "\n\nBạn mở lượt chạy trên xưởng để xem chi tiết nhé."
    return CHU["khong_doc"]


MAT_DONG_SAU_GIAY, MAT_DONG_CACH_GIAY = 15, 10


def ky_cua_goi(goi) -> dict:
    """[ghi chú kỹ thuật] Kỳ dữ liệu của CHÍNH lượt, đọc từ gói số của khối Kiểm số (do lõi tính thưởng dựng, không phải chữ AI):
    {"period": "2026-10", "cutoff": "2026-10-31", "trang_thai": "tam_tinh", "data_version": … | None} — sai khuôn / thiếu ⇒ {"khong_ro": True} (web nói rõ, không bỏ trống).
    Gói hiện không có ô "đã chốt" ⇒ luôn "tam_tinh"."""
    b = goi.get("bang_thuong") if isinstance(goi, dict) else None
    per, cut = (str(b.get("period") or "").strip(), str(b.get("cutoff") or "").strip()) if isinstance(b, dict) else ("", "")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", per) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cut):
        return {"khong_ro": True}
    dv = goi.get("data_version")
    return {"period": per, "cutoff": cut, "trang_thai": "tam_tinh", "data_version": str(dv)[:80] if isinstance(dv, (str, int)) and str(dv).strip() else None}


def han_tu_duyet(payload) -> int | None:
    """[ghi chú kỹ thuật] Số giây chờ của khối Người duyệt NẾU khối bật tự duyệt khi quá hạn và hạn là số nguyên 30–3600 (cùng luật workflow_kpi Đ-17);
    không có khối duyệt / không bật / hạn lạ ⇒ None (lời cũ). Nhiều khối duyệt ⇒ lấy hạn DÀI nhất (nói hạn không bị thiếu)."""
    han = []
    for n in (payload or {}).get("nodes") or []:
        st = ((n or {}).get("data") or {}).get("struct") or {}
        if st.get("node_id") != "human_intervention_node":
            continue
        o = {q.get("field_id"): q.get("value") for q in st.get("params") or [] if isinstance(q, dict)}
        bat = o.get("auto_approve_on_timeout")
        bat = bat is True or (isinstance(bat, str) and bat.strip().lower() in ("true", "1", "yes", "on"))
        t = o.get("timeout")
        if not bat or isinstance(t, bool) or not isinstance(t, int) or not 30 <= t <= 3600:
            return None      # một khối duyệt không tự duyệt ⇒ lượt vẫn có thể đứng chờ người ⇒ KHÔNG hứa hạn
        han.append(t)
    return max(han) if han else None


def chu_han(giay: int) -> str:
    return f"{giay} giây" if giay < 120 else f"khoảng {round(giay / 60)} phút"


def _doc_ket_qua(phien, v, g, dep, het_giay=300, cau_o=None):  # noqa: C901
    """Chờ lượt xong (hoặc dừng chờ Người duyệt) rồi đọc chữ ở khối Kết quả. Trả dict cho trang (KHÔNG lưu)."""
    het, tt = dep["gio"]() + het_giay, None
    rong_tu, hoi_ke = None, 0.0   # [ghi chú kỹ thuật] chưa thấy dòng lượt từ lúc nào; lần hỏi Temporal kế
    while dep["gio"]() < het:
        tt = dep["trang_thai"](phien)
        if tt in ("SUCCESS", "FAILED", "CANCELLED", "STOPPED", "PAUSED", "paused", "awaiting_intervention"):
            break
        if tt is None and dep.get("mat_dong"):   # không có dòng lượt ≥ 15 giây ⇒ hỏi Temporal (mỗi 10 giây); chắc "mất dòng" ⇒ báo ngay, không chờ hết 300 giây
            bay = dep["gio"]()
            rong_tu = bay if rong_tu is None else rong_tu
            if bay - rong_tu >= MAT_DONG_SAU_GIAY and bay >= hoi_ke:
                hoi_ke = bay + MAT_DONG_CACH_GIAY
                loai = dep["mat_dong"](phien)   # None | "khong_khoi" (chắc chưa khối nào chạy ⇒ được nói "chưa tốn tiền") | "co_khoi"
                if loai:
                    dep["ghi"](f"TRO-LY: lượt {str(phien)[:8]} mất dòng (Temporal đã kết thúc, không có dòng lượt; {loai}) — báo người dùng gửi lại")
                    return {"loai": "loi", "chu": CHU["mat_dong"] if loai == "khong_khoi" else CHU["mat_dong_co_khoi"], "phien": phien, "workflow": {"ten": g["ten"], "id": g["uuid"]}}
        else:
            rong_tu = None
        dep["ngu"](1.5)
    wf = {"ten": g["ten"], "id": g["uuid"]}   # [đợt 2] id để trang mở lượt trên xưởng
    if tt in ("PAUSED", "paused", "awaiting_intervention"):
        han = han_tu_duyet(v.get("payload") if isinstance(v, dict) else None)   # [ghi chú kỹ thuật]
        return {"loai": "cho_duyet", "chu": CHU["cho_duyet_co_han"].format(han=chu_han(han)) if han else CHU["cho_duyet"], "phien": phien, "workflow": wf,
                **({"han_tu_duyet_giay": han} if han else {})}
    ra_ds = [n for n in v["payload"]["nodes"]
             if (((n or {}).get("data") or {}).get("struct") or {}).get("node_id") in ("text_output_node", "markdown_output_node")]
    # [ghi chú kỹ thuật] lượt NHIỀU nhánh bị chặn ở một nhánh (Capitol dừng cả lượt, khối Kết quả nhánh khác bị bỏ qua) ⇒ vẫn đọc nhánh có Rào ĐẠT
    if tt != "SUCCESS" and not (tt in ("FAILED", "STOPPED") and len(ra_ds) > 1):
        return {"loai": "loi", "chu": _chu_luot_hong(phien, dep), "phien": phien, "workflow": wf}
    kq = dep["ket_qua"](phien) or {}
    if len(ra_ds) > 1:
        tin = dep.get("tin_khoi")
        out = _doc_nhieu_nhanh(ra_ds, kq, v, phien, wf, cau_o, (lambda ids: tin(phien, ids)) if tin else None)
        if tt != "SUCCESS" and out["loai"] == "tra_loi":
            out = dict(out, chu=out["chu"] + "\n\n" + CHU["dung_giua"])
        return out
    for n in v["payload"]["nodes"]:
        if (((n or {}).get("data") or {}).get("struct") or {}).get("node_id") in ("text_output_node", "markdown_output_node"):
            o = kq.get(n.get("id")) or {}
            if o.get("kieu") == "kpi":
                goi = o.get("v") if isinstance(o.get("v"), dict) else {}
                chu = goi.get("cau_tra_loi")
                if (goi.get("kiem") or {}).get("dat") is not True:
                    # [ghi chú kỹ thuật] gói KPI CHƯA có dấu đạt của khối kiểm số ⇒ đóng: KHÔNG hiện số
                    return {"loai": "loi", "chu": CHU["rao_giu_lai"], "phien": phien, "workflow": wf}   # [ghi chú kỹ thuật] nói thật thay cho "chưa đọc được"
                if isinstance(chu, str) and chu.strip():
                    out = {"loai": "tra_loi", "chu": chu.strip(), "nhan": "tạm tính", "phien": phien, "workflow": wf, "ky": ky_cua_goi(goi)}   # [ghi chú kỹ thuật]
                    if dep.get("bang"):   # [ghi chú kỹ thuật] thẻ KPI đầy đủ (bảng / xếp hạng / ô một NV) — cùng khuôn /api/hoi-nhanh, dựng bằng hoi_nhanh._bang
                        k = goi.get("kiem") or {}
                        out["the"] = {"session": phien, "cau": "", "workflow": g["uuid"], "ten": g["ten"], "giay": 0, "status": "SUCCESS", "ket_thuc": True,
                                      "tra_loi": out["chu"], "nhan": dep.get("nhan_tam_tinh") or "tạm tính", "view": dep["bang"](goi),
                                      "kiem": {"dat": True, "ly_do": str(k.get("ly_do") or "")[:300], "phien_ban": str(k.get("phien_ban") or "")[:40]}, "loi": None}
                    return out
                continue
            chu = o.get("v") if o.get("kieu") == "chu" else None
            if isinstance(chu, str) and chu.strip():
                if g.get("ma") in CACH_NAP and CACH_NAP[g["ma"]] != "tai_lieu":   # [ghi chú kỹ thuật] vd wf_11: số do khối chạy code tính, không qua Rào
                    chu = chu.strip() + "\n\n" + CHU["chua_kiem_so"]
                    return {"loai": "tra_loi", "chu": chu.strip(), "nhan": "tạm tính", "phien": phien, "workflow": wf, "ky": {"khong_ro": True}}   # [ghi chú kỹ thuật] thẻ KPI không qua gói số
                return {"loai": "tra_loi", "chu": chu.strip(), "nhan": "tạm tính", "phien": phien, "workflow": wf}
    return {"loai": "loi", "chu": CHU["khong_doc"], "phien": phien, "workflow": wf}


def _ca_doi(out, cach, cau):
    """[ghi chú kỹ thuật] thẻ cả đội (giu_nguyen) mà câu có mã NV ⇒ nói rõ đây là số cả đội."""
    if cach == "giu_nguyen" and out.get("loai") == "tra_loi" and ma_nv_cua(cau):
        out = dict(out, chu=CHU["so_ca_doi"] + "\n\n" + out["chu"])
    return out


def _chay(ma, cau, conv, dep, muc_da_hoi=None):
    kho, ghim = dep["kho"], dep["ghim"]
    g = ghim.get(ma)
    if not g or not g["mo"]:
        return 200, {"loai": "loi", "chu": CHU["thieu_muc"]}
    if g.get("gui_thay_nguoi_dung"):
        return 200, {"loai": "loi", "chu": CHU["thieu_muc"]}   # ngoại lệ gửi thay người dùng: đợt 1 KHÔNG chạy (chưa có thẻ nào)
    f_tl = f_bang = None
    if CACH_NAP.get(ma) == "tinh_bang":   # [ghi chú kỹ thuật]
        f_bang = _bang_dang_mo(dep, conv)
        if not f_bang:
            chu_b = "Mình không còn giữ file bảng tính của cuộc trò chuyện này (hết hạn giữ hoặc máy chủ vừa bật lại). Bạn kéo lại file vào khung chat rồi hỏi nhé."
            kho.them(conv, "tro_ly", chu_b); return 200, {"loai": "khong_co", "chu": chu_b}
    if CACH_NAP.get(ma) == "tai_lieu":   # [ghi chú kỹ thuật]
        f_tl = _file_dang_mo(dep, conv)
        if not f_tl:
            kho.them(conv, "tro_ly", TLC.CHU["het_han"]); return 200, {"loai": "khong_co", "chu": TLC.CHU["het_han"]}
        if not f_tl.get("kieu") and f_tl.get("raw_vb") is not None and TLC.hoi_ve_hinh(cau):   # [ghi chú kỹ thuật] nạp kho tạm MUỘN, chỉ khi câu hỏi nhắc hình
            ma_h, r_h = dep["tai_lieu"].chuyen_sang_kho(conv)
            if ma_h == 200:
                dep["ghi"]("TRO-LY câu hỏi về hình trên file đọc thẳng ⇒ chuyển file sang kho tạm để đọc cả hình")
                kho.them(conv, "tro_ly", r_h["chu"]); return 200, {"loai": "khong_co", "chu": r_h["chu"], "dang_doc": True, "doc_ca_hinh": True}
            dep["ghi"](f"TRO-LY câu hỏi về hình: không chuyển được sang kho (mã {ma_h}) ⇒ trả lời theo phần chữ như cũ")
    cau_k = (TLC.khoa_cau(f_tl, cau) + ("[hình]" if f_tl.get("ly_do") == "co_hinh" else "")) if f_tl else (TLC.khoa_cau(f_bang, cau) + "[tính]") if f_bang else cau   # cùng câu trên file KHÁC ⇒ khoá khác (không trả lại câu trả lời của file cũ)
    khoa = (_chuan(cau_k), g["uuid"])
    cu = kho.da_chay.get(conv, {}).get(khoa)
    doc_lai = None   # [ghi chú kỹ thuật] mã lượt CHỜ DUYỆT đã kết thúc ⇒ đọc lại kết quả của chính lượt đó (không chạy lượt mới)
    if cu and cu.get("loai") == "cho_duyet" and cu.get("phien"):
        # [phiên kiểm H6, vòng 2] CHỈ đọc lại khi trạng thái thuộc tập ĐÃ KẾT THÚC; đang chờ / đang chạy / không đọc được / trạng thái lạ ⇒ coi như CHƯA xong:
        #   trả câu đã nhớ, KHÔNG quên, KHÔNG chạy lượt mới (cổng đóng khi không hiểu — trước: mọi thứ khác ba chữ "chờ" đều đi đọc lại ⇒ lỗi ⇒ quên câu ⇒ lượt thứ hai)
        if dep["trang_thai"](cu["phien"]) not in ("SUCCESS", "FAILED", "CANCELLED", "STOPPED"):
            return 200, kho.nho_cuoi(conv, dict(cu, lap_lai=True), cau, dep.get("org"))
        doc_lai = cu["phien"]
    elif cu:
        return 200, kho.nho_cuoi(conv, dict(cu, lap_lai=True), cau, dep.get("org"))   # không chạy lại lượt đã xong trong cùng cuộc chat
    v = dep["pb"](g["uuid"])
    if not v or sha_payload(v.get("payload")) != g["sha"]:
        dep["ghi"](f"TRO-LY {ma}: sha khác ghim — không chạy"); return 200, {"loai": "loi", "chu": CHU["doi"]}
    cach = CACH_NAP.get(ma)
    o_vao, ly = ra_ngoai_hop_le(v["payload"], cach)
    if ly or (cach is None and not o_vao) or (cach is not None and o_vao is None):
        dep["ghi"](f"TRO-LY {ma}: {'; '.join(ly)[:200]}"); return 200, {"loai": "loi", "chu": CHU["thieu_muc"]}
    hs_tim = None
    if cach == "tai_lieu" and f_tl.get("kieu") in ("kho", "kho_chung"):   # [ghi chú kỹ thuật] file bản chụp đã / đang nạp kho: chưa xong ⇒ nói rõ; xong ⇒ TÌM rồi mới chạy
        tt_kho, tin = dep["tai_lieu"].trang_thai_kho(f_tl)
        if tt_kho != "xong":
            kho.them(conv, "tro_ly", tin["chu"]); return 200, {"loai": "khong_co", "chu": tin["chu"], "dang_doc": tt_kho == "dang_nap"}
        truoc_nd = [m.get("noi_dung") for m in kho.lich_su(conv)[:-1] if isinstance(m, dict) and m.get("vai") == "nguoi_dung" and str(m.get("noi_dung") or "").strip()]
        kq_tim, hs_tim = dep["tai_lieu"].tim_trang(f_tl, cau, truoc_nd[-1] if truoc_nd else None)
        dep["ghi"](f"TRO-LY tài liệu (kho): {len(hs_tim['luot'])} lượt tìm, k={hs_tim['k']} ⇒ {hs_tim['so_trang_tra']} trang {hs_tim['trang'][:12]} · {hs_tim['ky_tu']} ký tự · "
                   f"mã {[x['http'] for x in hs_tim['luot']]}")   # không ghi câu tìm vào nhật ký (câu của người dùng) — câu tìm nằm trong ô `tim` của lượt
        if not kq_tim["phan"]:
            loi_tim = next((x["loi"] for x in hs_tim["luot"] if x.get("loi")), None)
            chu = TLC.CHU["tim_loi"].format(ly_do=str(loi_tim)[:160]) if loi_tim else TLC.CHU["khong_trang"].format(ten=f_tl["ten"])
            kho.them(conv, "tro_ly", chu); return 200, {"loai": "khong_co", "chu": chu, "tim": hs_tim}
        f_tl = dict(f_tl, kq=kq_tim)   # bản ghi của RIÊNG lượt này: "tài liệu" = các trang tìm được (kho file đang mở giữ nguyên)
    fi = None
    if cach == "tinh_bang":   # [ghi chú kỹ thuật] câu hỏi vào ô chữ; byte gốc của file vào kho file Capitol (một lần mỗi file đang mở) rồi vào khối nhận file của LƯỢT NÀY
        if not f_bang.get("phong_bi"):
            ma_f, env = dep["dua_file"](f_bang["ten"], f_bang["raw"])
            if ma_f != 200 or not isinstance(env, dict) or not env.get("file"):
                dep["ghi"](f"TRO-LY tính trên bảng: không đưa được file vào kho (mã {ma_f})")
                return 200, {"loai": "loi", "chu": "Mình chưa đưa được file của bạn vào nơi chạy code. Bạn thử lại sau ít phút; nếu vẫn lỗi, kéo lại file vào khung chat."}
            f_bang["phong_bi"] = env
        ti, fi = {o_vao[0]: cau.strip()}, {o_vao[1]: f_bang["phong_bi"]}
    elif cach == "tai_lieu":
        ti = {o_vao[0]: TLC.o_nhap(f_tl, cau, kho.lich_su(conv)[:-1])}   # toàn văn (hoặc các trang tìm được) + lượt trước + câu hỏi — KHÔNG cắt
    elif cach is None:
        ti = {o_vao: cau.strip()}   # [ghi chú kỹ thuật] không cắt 2.000 ký tự — câu người dùng vào workflow ĐỦ
    else:
        ti, hoi = _nap_o(ma, cach, v["payload"], cau, conv, dep)
        if hoi:
            kho.them(conv, "tro_ly", hoi); return 200, {"loai": "khong_co", "chu": hoi}
    # [ghi chú kỹ thuật] cùng cuộc chat đang có lượt CHỜ DUYỆT của cùng workflow ⇒ không chạy lượt mới (câu khác chữ vẫn bắt)
    for x in list(kho.da_chay.get(conv, {}).values()):
        if x.get("loai") == "cho_duyet" and (x.get("workflow") or {}).get("id") == g["uuid"] and x.get("phien")\
                and dep["trang_thai"](x["phien"]) in ("PAUSED", "paused", "awaiting_intervention"):
            kho.them(conv, "tro_ly", CHU["dang_cho_duyet"])
            return 200, {"loai": "cho_duyet", "chu": CHU["dang_cho_duyet"], "phien": x["phien"], "workflow": x.get("workflow"), "lap_lai": True}
    if doc_lai:   # [ghi chú kỹ thuật] lượt chờ duyệt của ĐÚNG câu này đã kết thúc ⇒ đọc lại, 0 đồng, không chạy lượt mới
        out = _sau_tai_lieu(_ca_doi(dict(_doc_ket_qua(doc_lai, v, g, dep, het_giay=5, cau_o=dict(o_nhap(v["payload"]), **(ti or {}))), doc_lai_luot_cho=True), cach, cau), f_tl, hs_tim)
        dep["ghi"](f"TRO-LY hỏi lại câu của lượt chờ duyệt {str(doc_lai)[:8]} đã kết thúc ⇒ đọc lại kết quả: {out.get('loai')}")
        if out["loai"] == "cho_duyet":   # vừa quay lại trạng thái chờ giữa hai lần hỏi trạng thái ⇒ coi như còn chờ
            return 200, kho.nho_cuoi(conv, dict(cu, lap_lai=True), cau, dep.get("org"))
        if out["loai"] == "tra_loi":
            kho.da_chay.setdefault(conv, {})[khoa] = out   # nhớ câu trả lời thay cho câu "chờ duyệt"
        else:
            kho.da_chay.get(conv, {}).pop(khoa, None)      # lượt hỏng / bị dừng ⇒ bỏ câu đã nhớ: lần hỏi sau chạy lượt mới
        kho.them(conv, "tro_ly", out["chu"], dt=TLC.DT_TRA_LOI if f_tl else None)
        return 200, kho.nho_cuoi(conv, out, cau, dep.get("org"))
    # [ghi chú kỹ thuật] cache câu trả lời DÙNG CHUNG (cache_tra_loi nhóm kỹ thuật): câu y hệt + cùng lịch sử + cùng workflow / phiên bản + vân tay hộp KPI ⇒ trả lại phiên cũ, KHÔNG chạy
    ca = dep.get("cache")
    if ca:
        ph_cu = ca["lay"](cau_k, g["uuid"], v.get("so"), kho.lich_su(conv)[:-1])
        if ph_cu:
            out = _sau_tai_lieu(_ca_doi(dict(_doc_ket_qua(ph_cu, v, g, dep, het_giay=5, cau_o=dict(o_nhap(v["payload"]), **(ti or {}))), tu_cache=True), cach, cau), f_tl, hs_tim)
            if out["loai"] in ("tra_loi", "cho_duyet"):
                kho.them(conv, "tro_ly", out["chu"], dt=TLC.DT_TRA_LOI if f_tl else None); kho.da_chay.setdefault(conv, {})[khoa] = out
                return 200, kho.nho_cuoi(conv, out, cau, dep.get("org"))
    muc, thieu = dep["toi_da"](v["payload"])
    if thieu or muc is None or not (muc == muc) or muc < 0:
        return 200, {"loai": "loi", "chu": CHU["thieu_muc"]}
    if muc_da_hoi is None and not muc < J.NGUONG_TU_CHAY:
        # chọn từ câu làm rõ trúng workflow đắt ⇒ hỏi tiền (phiếu mới gắn mức LÚC NÀY), không chạy
        ph = kho.cap_phieu(conv, "tien", ["co", "khong"], muc=muc, cau=cau); kho.phieu[ph]["ma"] = ma
        chu = f"Việc này tốn tối đa khoảng {_usd(muc)} USD. Bạn muốn chạy không?"
        kho.them(conv, "tro_ly", chu)
        return 200, {"loai": "hoi", "chu": chu, "phieu": ph, "lua_chon": [{"chon": "co", "ten": "Chạy"}, {"chon": "khong", "ten": "Không"}], "workflow": {"ten": g["ten"]}}
    if muc_da_hoi is not None and muc > float(muc_da_hoi) + 1e-9:
        return 200, {"loai": "loi", "chu": CHU["doi"]}   # mức đổi sau khi người dùng đồng ý ⇒ không chạy
    if muc > float(dep["con_tran"]()):
        return 200, {"loai": "loi", "chu": CHU["het_tran"]}
    code, r = dep["chay"](g["uuid"], ti, fi) if fi else dep["chay"](g["uuid"], ti)
    if code != 200 or not isinstance(r, dict) or not r.get("session"):
        if code == 422:   # [ghi chú kỹ thuật] may_chu.chay chặn lúc chạy (0 đồng) — [ghi chú kỹ thuật] nói đúng luật nào chặn
            chu_422 = CHU["khong_dat_luat"] if r.get("luat_kpi") else CHU["kho_chua_san"] if r.get("luat_kho") else CHU["thieu_muc"]
            dep["ghi"](f"TRO-LY {ma}: lượt bị chặn lúc chạy (422: {', '.join(k for k in r if k.startswith('luat_')) or 'không rõ luật'})")
            kho.them(conv, "tro_ly", chu_422); return 200, {"loai": "loi", "chu": chu_422}
        if code == 413 and isinstance(r, dict) and r.get("qua_co") and isinstance(r.get("loi"), str):   # [ghi chú kỹ thuật] lời của o_nhap.kiem_co (nêu cỡ thật + chốt)
            kho.them(conv, "tro_ly", r["loi"]); return 200, {"loai": "loi", "chu": r["loi"]}
        return 200, {"loai": "loi", "chu": CHU["het_tran"] if code == 402 else CHU["loi"]}
    if r.get("version") != v.get("so") or (dep["pb"](g["uuid"]) or {}).get("so") != v.get("so"):
        return 200, {"loai": "loi", "chu": CHU["doi"], "phien": r["session"]}   # [giống chat_chay Đ3] phiên bản đổi giữa lúc kiểm và lúc chạy
    phien = r["session"]
    if ca:
        ca["ghi"](cau_k, g["uuid"], v.get("so"), kho.lich_su(conv)[:-1], phien)
    out = _sau_tai_lieu(_ca_doi(_doc_ket_qua(phien, v, g, dep, cau_o=dict(o_nhap(v["payload"]), **(ti or {}))), cach, cau), f_tl, hs_tim)
    if f_bang and isinstance(out, dict) and out.get("loai") == "tra_loi":   # [ghi chú kỹ thuật] nói rõ số từ đâu ra; KHÔNG mang nhãn "tạm tính" (không phải số thưởng)
        out = dict(out, chu=out["chu"].rstrip() + f"\n\nSố do code tính trên toàn bộ file “{f_bang['ten']}” (chạy trên máy ảo). Muốn xem đoạn code đã chạy: bấm “Mở lượt này trên xưởng”; "
                   "file kết quả (nếu có): bấm “Xem file của lượt”.", tinh_tren_bang=True)
        out.pop("nhan", None)
    if (isinstance(out, dict) and out.get("loai") == "tra_loi" and not f_tl and not f_bang and dep.get("xuat") and phien and not g.get("xuat_file")
            and re.search(r"^\s*\|.+\|\s*$", out.get("chu") or "", re.M)):   # [ghi chú kỹ thuật] câu trả lời KPI có BẢNG ⇒ web hiện nút "Tải Excel"
        out = dict(out, co_xuat=True)
    out = _tu_xuat_file(out, g, phien, cau, conv, dep)   # [ghi chú kỹ thuật] thẻ xuất file + trả lời ngay ⇒ kèm file NHÁP trong cùng câu trả lời
    kho.them(conv, "tro_ly", out["chu"], dt=TLC.DT_TRA_LOI if f_tl else None)
    if out["loai"] in ("tra_loi", "cho_duyet"):
        kho.da_chay.setdefault(conv, {})[khoa] = out
    return 200, kho.nho_cuoi(conv, out, cau, dep.get("org"))
