"""Mức tiền TỐI ĐA (USD, sổ tiền ngày chung) của một lượt workflow — giữ chỗ TRƯỚC khi chạy (luật D34, nhóm phát triển xác nhận 11/11/2026).

Vì sao: 10/11 một lượt 8 khối giọng tiêu 0,0895 USD trong khi trang chỉ giữ chỗ 0,01 cố định ⇒ sổ vượt trần ngày.
Luật: giữ chỗ = cộng mức tối đa từng khối; khối CHƯA có mức tối đa (giá trị None hoặc không có trong bảng) ⇒ KHÔNG chạy.

Nguồn số (C, 11/11 — chỉ đọc code + 1 câu SELECT chỉ đếm trên PostgreSQL bộ thử):
- Khối Agent và khối chat PowerPoint / Word / Excel: trần token TRAN_TOKEN_VAO / TRAN_TOKEN_RA trong bản vá
  sua_doi/workflow/node/base/agent/node.py.ci_hop_phien + sua_doi/services/pydantic_ai_skills/e2b_chat_service.py.
  pydantic-ai kiểm trần token theo tổng đã dùng ⇒ cộng "lần gọi cuối" (VUOT_1_LAN_* dưới; số đo 694 lượt agent_node:
  1 lần gọi vào 26.271, ra 4.000). × 2 cho bước "làm lại 1 lần" (sua_doi/nodes/agent/node.py).
- Giọng đọc: một khối ≤ 1.000 ký tự × 15 USD / 1 triệu ký tự (sua_doi/services/eleven_labs/audio_gen.py:26-28).
RỦI RO CÒN LẠI (nhóm phát triển xác nhận, ghi rõ): lỗi mạng / 429 SAU khi đã tiêu ⇒ khối Agent tự thử lại tới 5 lần (streaming.py:94)
và Temporal chạy lại tới 3 lần (temporal/config.py:202-208), mỗi lần đủ trần mới ⇒ tiền thật có thể vượt mức giữ chỗ.
Sổ tiền sau lượt vẫn cộng số thật (theo_doi_ghi_tien) ⇒ lượt sau bị chặn đúng.
Exa (tìm web) là công cụ của khối Agent, tính ở sổ Exa riêng (hộp capitol-mcp-exa tự giữ chỗ) ⇒ ở bảng này = 0.
"""
import json
from pathlib import Path

_GIA_CU = json.loads((Path(__file__).resolve().parent / "bang_gia.json").read_text(encoding="utf-8"))["gpt-6-luna"]
# [ghi chú kỹ thuật] giá đọc CÙNG bảng định tuyến của cổng (chay_thu/cong_ai/dinh_tuyen.yaml) — một nguồn. Mọi hằng dưới tính theo
# giá của mục mà tên "gpt-6-luna" trỏ tới; lấy max(bảng, bang_gia.json) từng chiều (thà dư). Bảng hỏng / luna ngoài bảng ⇒ _BANG_LOI ⇒ MỌI khối gọi AI
# không có mức ⇒ trang CHẶN (khối không AI vẫn 0). Khối có ô "model" ⇒ tên phải có trong bảng VÀ giá mục đích ≤ giá đã dùng tính mức, khác ⇒ chặn.
BANG_DINH_TUYEN = Path(__file__).resolve().parents[1] / "cong_ai" / "dinh_tuyen.yaml"


# [ghi chú kỹ thuật] TIỀN BEDROCK KHÔNG TÍNH TRẦN.
# Mức của một khối tách HAI phần: "mức phải báo" (E2B, tra tài liệu, OpenRouter, luna… — vào giữ chỗ + trần ngày + ngưỡng tự chạy 0,20) và "mức Bedrock ước"
# (chỉ để xem / đối soát; sổ cổng ghi tiền thật từng lời gọi). Khối nào dùng Bedrock: mô hình của khối (sau khi ÉP mô hình mặc định — file
# compose.mo_hinh_mac_dinh.yaml của gói va_mo_hinh_mac_dinh, cùng nguồn với 3 hộp ab-*) trỏ tới mục có nhà cung cấp "bedrock" trong bảng định tuyến.
# CHAY_THU_TIEN_BEDROCK=tat ⇒ mọi hàm trả Y NHƯ bản trước (mọi tiền tính trần; mô hình đắt hơn luna ⇒ chặn) — cách lùi không cần chép file.
import os as _os

TIEN_BEDROCK = _os.environ.get("CHAY_THU_TIEN_BEDROCK", "bat") != "tat"
NCC_BEDROCK = "bedrock"
_KIEU_NCC = ("responses", "chat", "bedrock_messages")   # PHẢI bằng danh sách kiểu của cong_ai/cong.py doc_bang — thêm kiểu ở cổng mà quên ở đây ⇒ trang chặn hết
NCC_BANG: dict[str, str] = {}   # tên Capitol gửi (và id mục) ⇒ tên nhà cung cấp; _doc_bang điền
# loại khối chọn mô hình ở điểm chung model_resolution.py (kế thừa AgentBaseNode) ⇒ bị ÉP mô hình mặc định. Khối khác (Word / Excel / PPT, Rào AI, Che,
# Người duyệt, đồ thị, kho bảng…) tự dựng mô hình ⇒ CHƯA đổi ⇒ vẫn tính như luna tới khi nhóm của chúng được chuyển.
FILE_BAN_VA_MO_HINH = Path(__file__).resolve().parents[1] / "va_mo_hinh_mac_dinh" / "model_resolution.py"   # bản vá ĐANG GẮN vào 3 hộp ab-*


def _doc_khoi_ep(p: Path | None = None, hang: str = "CHAY_THU_KHOI_DOI_MO_HINH") -> tuple[str, ...]:
    """[ghi chú kỹ thuật] danh sách loại khối được đổi mô hình = hằng CHAY_THU_KHOI_DOI_MO_HINH trong CHÍNH bản vá gắn vào hộp (đọc bằng ast, không chép tay).
    Không có file bản vá (chưa tới bước 2) ⇒ 4 loại gốc (vô hại: chưa có file mô hình khối thì không khối nào đổi). File có mà không đọc ra hằng ⇒ NÉM.
    [ghi chú kỹ thuật] hang="CHAY_THU_KHOI_HOP_PHIEN" ⇒ danh sách loại khối đi hộp phiên (Word / Excel / PPT…); bản vá CHƯA có hằng này (bản 69ef2b97
    trở về trước) ⇒ () = chưa loại nào trong họ này đổi được mô hình (đúng với bản vá đó)."""
    p = p or FILE_BAN_VA_MO_HINH
    hop_phien = hang == "CHAY_THU_KHOI_HOP_PHIEN"
    if not p.is_file():
        return () if hop_phien else ("agent_node", "flexible_code_interpreter_node", "mcp_agent_node", "vercel_deploy_agent_node")
    import ast
    for n in ast.parse(p.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == hang for t in n.targets):
            v = ast.literal_eval(n.value)
            assert isinstance(v, tuple) and v and all(isinstance(x, str) for x in v), f"{hang} sai dạng"
            return v
    if hop_phien or hang != "CHAY_THU_KHOI_DOI_MO_HINH":   # [va_nguoi_gac_claude] hằng thêm sau (hộp phiên, người gác): bản vá chưa có ⇒ () = chưa loại nào trong họ đó đổi được
        return ()
    raise ValueError("bản vá model_resolution.py không có hằng CHAY_THU_KHOI_DOI_MO_HINH")


def _doc_nguoi_gac_theo(p: Path | None = None) -> dict:
    """[ghi chú kỹ thuật] hằng CHAY_THU_NGUOI_GAC_THEO của bản vá ({loại khối: loại khối mà nó dùng chung dòng cấu hình}) — đọc bằng ast; chưa có ⇒ {}."""
    p = p or FILE_BAN_VA_MO_HINH
    if not p.is_file():
        return {}
    import ast
    for n in ast.parse(p.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "CHAY_THU_NGUOI_GAC_THEO" for t in n.targets):
            v = ast.literal_eval(n.value)
            assert isinstance(v, dict) and all(isinstance(a, str) and isinstance(b, str) for a, b in v.items()), "CHAY_THU_NGUOI_GAC_THEO sai dạng"
            return v
    return {}


KHOI_EP_MO_HINH: tuple[str, ...] = ()   # điền trong khối try lúc nạp (cùng chỗ đọc bảng) — hỏng ⇒ _BANG_LOI ⇒ chặn mọi khối gọi AI
KHOI_HOP_PHIEN: tuple[str, ...] = ()    # [va_hop_phien_claude] như trên, cho họ khối đi hộp phiên
KHOI_NGUOI_GAC: tuple[str, ...] = ()    # [va_nguoi_gac_claude] như trên, cho "người gác" (Rào chế độ AI, Che thông tin, bản tóm tắt cho Người duyệt)
NGUOI_GAC_THEO: dict = {}               # [va_nguoi_gac_claude] loại khối dùng chung dòng cấu hình với loại khác (che PDF ⇒ theo che chữ)
MUC_SUY_LUAN = ("low", "medium", "high", "xhigh", "max")
FILE_EP_MO_HINH = Path(__file__).resolve().parents[1] / "cau_hinh_mo_hinh" / "mo_hinh_khoi.txt"   # [bản 2, nhóm phát triển xác nhận 18/11] CÙNG file mà bản vá model_resolution.py trong hộp ab-* đọc


def _doc_ep_mo_hinh(p: Path | None = None) -> tuple[str, dict]:
    """(mô hình mặc định, {loại khối: mô hình}) từ file mô hình khối — CÙNG luật đọc với va_mo_hinh_mac_dinh/model_resolution.py: mỗi dòng "<mac_dinh | loại khối> =
    <tên>", bỏ dòng trống / dòng #. Không có file / không có dòng nào ⇒ ("", {}) = không ép. Dòng sai khuôn / lặp khoá ⇒ NÉM ValueError (người gọi chặn khối)."""
    p = p or FILE_EP_MO_HINH   # đọc biến module lúc GỌI (bộ thử đổi đường dẫn)
    try:
        dong = p.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return "", {}
    bang: dict[str, str] = {}
    for so, d in enumerate(dong, 1):
        d = d.strip()
        if not d or d.startswith("#"):
            continue
        khoa, dau, gia_tri = d.partition("=")
        ten, co_muc, muc = gia_tri.partition("/")
        khoa, ten, muc = khoa.strip(), ten.strip(), muc.strip()
        if not dau or not khoa or not ten or khoa in bang or (co_muc and muc not in MUC_SUY_LUAN):
            raise ValueError(f"file mô hình khối dòng {so} sai khuôn, lặp khoá hoặc mức suy luận lạ")
        if khoa != "mac_dinh" and khoa not in KHOI_EP_MO_HINH + KHOI_HOP_PHIEN + KHOI_NGUOI_GAC:
            raise ValueError(f"file mô hình khối dòng {so}: loại khối chưa hỗ trợ đổi mô hình")
        bang[khoa] = f"{ten}-{muc}" if muc else ten   # có mức ⇒ tên GỬI TRÊN DÂY là tuyến "<tên>-<mức>" của bảng cổng (bản vá trong hộp đổi tên y như vậy)
    return bang.pop("mac_dinh", ""), bang


def _doc_bang(p: Path = BANG_DINH_TUYEN) -> dict:
    """Cùng luật kiểm với cong_ai/cong.py doc_bang (sai ⇒ NÉM). Trả {tên Capitol gửi: (giá vào lớn nhất, giá ra lớn nhất)} qua MỌI đường."""
    import yaml
    b = yaml.safe_load(p.read_text(encoding="utf-8"))
    ncc, mh, dt = b["nha_cung_cap"], b["mo_hinh"], b["dinh_tuyen"]
    for ten, n in ncc.items():
        assert n["kieu"] in (_KIEU_NCC if TIEN_BEDROCK else ("responses", "chat")) and n["goc"].startswith("https://") and n["khoa"], f"nhà cung cấp {ten} sai"
        assert not n.get("mien_tran") or n["kieu"] == "bedrock_messages", f"chỉ bedrock_messages mới được đặt mien_tran ({ten})"   # như cong.py của F
    for ten, m in mh.items():
        assert m["nha_cung_cap"] in ncc and float(m["gia_vao"]) > 0 and float(m["gia_ra"]) > 0 and int(m["tran_ra"]) > 0 and m.get("nguon"), f"mục {ten} sai"
    gia: dict[str, tuple[float, float]] = {}
    for d in dt:
        assert d["duong"] in ("messages", "responses", "chat", "*") and d["ten"] and d["toi"] in mh, f"dòng định tuyến sai: {d}"
        m = mh[d["toi"]]; cu = gia.get(d["ten"], (0.0, 0.0))
        gia[d["ten"]] = (max(cu[0], float(m["gia_vao"])), max(cu[1], float(m["gia_ra"])))
        # một tên mà các đường trỏ tới nhà cung cấp KHÁC nhau ⇒ coi là KHÔNG phải bedrock (tính trần — lệch an toàn)
        # [ghi chú kỹ thuật] "là Bedrock, miễn trần" = NHÀ CUNG CẤP của mục có `mien_tran: true` trong bảng (tên nhà cung cấp tuỳ ý, vd bedrock_046)
        loai = NCC_BEDROCK if ncc[m["nha_cung_cap"]].get("mien_tran") is True else str(m["nha_cung_cap"])
        NCC_BANG[d["ten"]] = loai if NCC_BANG.get(d["ten"], loai) == loai else "lan"
    return gia


def _sha_bang() -> str | None:
    import hashlib
    try:
        return hashlib.sha256(BANG_DINH_TUYEN.read_bytes()).hexdigest()
    except OSError:
        return None


_BANG_SHA_NAP = _sha_bang()   # [ghi chú kỹ thuật] cổng đọc bảng MỖI lời gọi, trang chỉ đọc lúc nạp ⇒ bảng đổi sau khi nạp ⇒ chặn tới khi bật lại máy chủ trang


def _loi_bang() -> str | None:
    if _BANG_LOI:
        return _BANG_LOI
    if _sha_bang() != _BANG_SHA_NAP:
        return "bảng định tuyến đã đổi sau khi máy chủ trang nạp — bật lại máy chủ trang (mức giữ chỗ phải theo giá mới)"
    return None


MO_HINH_MAC_DINH, MO_HINH_THEO_KHOI = "", {}   # mặc định khi bảng lỗi: không ép (mọi khối AI đằng nào cũng bị chặn)
try:
    GIA_BANG = _doc_bang()
    KHOI_EP_MO_HINH = _doc_khoi_ep()
    KHOI_HOP_PHIEN = _doc_khoi_ep(hang="CHAY_THU_KHOI_HOP_PHIEN")
    assert not set(KHOI_EP_MO_HINH) & set(KHOI_HOP_PHIEN), "một loại khối nằm ở cả hai danh sách đổi mô hình"
    KHOI_NGUOI_GAC = _doc_khoi_ep(hang="CHAY_THU_KHOI_NGUOI_GAC")
    NGUOI_GAC_THEO = _doc_nguoi_gac_theo()
    assert not set(KHOI_NGUOI_GAC) & set(KHOI_EP_MO_HINH + KHOI_HOP_PHIEN), "một loại người gác nằm ở danh sách đổi mô hình khác"
    assert all(b in KHOI_NGUOI_GAC and a not in KHOI_NGUOI_GAC + KHOI_EP_MO_HINH + KHOI_HOP_PHIEN for a, b in NGUOI_GAC_THEO.items()), "CHAY_THU_NGUOI_GAC_THEO trỏ sai"
    _l = GIA_BANG["gpt-6-luna"]
    _GIA = {"input": max(_l[0], float(_GIA_CU["input"])), "output": max(_l[1], float(_GIA_CU["output"]))}
    try:   # chỉ để IN lúc nạp (gan.sh, bộ thử) — mức của từng lượt đọc lại file MỖI LẦN ở mo_hinh_cua_khoi
        MO_HINH_MAC_DINH, MO_HINH_THEO_KHOI = _doc_ep_mo_hinh() if TIEN_BEDROCK else ("", {})
    except (ValueError, OSError):
        MO_HINH_MAC_DINH, MO_HINH_THEO_KHOI = "?file mô hình khối hỏng", {}
    _BANG_LOI = None
except Exception as _e:   # không ném lúc nạp: trang vẫn mở được, nhưng mọi khối gọi AI bị chặn (toi_da báo lý do)
    GIA_BANG, _GIA, _BANG_LOI = {}, dict(_GIA_CU), f"bảng định tuyến lỗi: {type(_e).__name__}: {str(_e)[:120]}"
TRAN_TOKEN_VAO, TRAN_TOKEN_RA = 120_000, 16_000      # BẰNG số trong 2 file vá ở trên — đổi bên đó thì đổi ở đây
TRAN_RA_MOT_LAN = 8_000                               # max_tokens mỗi lần trả lời (base node _model_settings; e2b_chat_service :1493)
# Lần gọi CUỐI (đối chiếu kỹ thuật): vào được kiểm TRƯỚC lần gọi theo tổng đã dùng (≤ 120k) nhưng mang cả ngữ cảnh ⇒ thêm tới ~120k;
# ra được kiểm SAU ⇒ thêm tới 1 lần trả lời (8k). Ngữ cảnh lần cuối còn cộng kết quả công cụ mới — không có trần ⇒ rủi ro còn lại.
VUOT_1_LAN_VAO, VUOT_1_LAN_RA = TRAN_TOKEN_VAO, TRAN_RA_MOT_LAN
_MOT_LUOT_AI = ((TRAN_TOKEN_VAO + VUOT_1_LAN_VAO) * _GIA["input"] + (TRAN_TOKEN_RA + VUOT_1_LAN_RA) * _GIA["output"]) / 1e6
KHOI_AGENT = max(0.075, round(2 * _MOT_LUOT_AI, 4))   # công thức ≈ 0,072 (2 = lượt chính + làm lại 1 lần); nhóm phát triển xác nhận giữ 0,075
# [ghi chú kỹ thuật] khối tài liệu (đi e2b_chat_service): trần RIÊNG 200k vào / 24k ra / 14 lần gọi (BẰNG số trong
# va_tran_tai_lieu/e2b_chat_service.py). Vào kiểm TRƯỚC mỗi lần gọi theo tổng ⇒ lần cuối vượt ≤ 1 ngữ cảnh — lấy BẰNG trần (như KHOI_AGENT, cận
# trên); ra kiểm SAU ⇒ + max_tokens 8.000 / lần (e2b_chat_service.py model_settings). × 1: khối tài liệu KHÔNG có vòng làm lại KPI.
TRAN_TAI_LIEU_VAO, TRAN_TAI_LIEU_RA, TAI_LIEU_RA_MOT_LAN = 200_000, 24_000, 8_000
_MOT_LUOT_TAI_LIEU = ((2 * TRAN_TAI_LIEU_VAO) * _GIA["input"] + (TRAN_TAI_LIEU_RA + TAI_LIEU_RA_MOT_LAN) * _GIA["output"]) / 1e6
KHOI_TAI_LIEU = max(0.08, round(_MOT_LUOT_TAI_LIEU, 4))   # công thức = 0,056; nhóm phát triển xác nhận giữ 0,08 (16/11)
KHOI_GIONG = round(1_000 * 15 / 1_000_000, 4)         # 0,015 USD
# Người duyệt — bản tóm tắt (vá sua_doi/nodes/logic/human_intervention/digest.py, 11/11): trần cả lượt vào 70k / ra 4k, max_tokens 4k,
# ≤ 3 lần gọi, 1 lần thử (HITL_REVIEW_DIGEST_RETRIES 0). Lần gọi cuối thêm tới 1 ngữ cảnh (≤ 60.000 ký tự + lời dặn ≈ 66k) và 1 lần trả lời.
KHOI_DUYET = max(0.02, round(((70_000 + 66_000) * _GIA["input"] + (4_000 + 4_000) * _GIA["output"]) / 1e6, 4))   # ≈ 0,0176 ⇒ 0,02

# CHAT OPERATOR trên trang (đối chiếu kỹ thuật): trần cả lượt trong bản vá va_operator_chat/orchestrator.py (CHAY_THU_CHAT_*) — BẰNG số ở đây.
# ≤ 20 lần gọi; vào ≤ 150k (kiểm TRƯỚC mỗi lần theo tổng đã dùng ⇒ lần cuối thêm tới 1 ngữ cảnh ≤ 150k); ra ≤ 12k (kiểm SAU ⇒ thêm 1 lần ≤ 8k).
CHAT_LAN, CHAT_VAO, CHAT_RA, CHAT_RA_MOT_LAN = 20, 400_000, 12_000, 8_000   # vào 150k ⇒ 400k (nhóm phát triển xác nhận 12/11: đo lượt thật, 150k chỉ đủ ~4 lần gọi)
# [ghi chú kỹ thuật] trần vào là trần TỔNG ĐÃ DÙNG ⇒ lần gọi cuối KHÔNG bị nó chặn. Bản vá chặn cỡ từng lần gọi: tin nhắn ≤ CHAT_BYTE_1_LAN byte
# (token ≤ byte) + phần cố định (lời dặn + mô tả công cụ) ≤ CHAT_CO_DINH token [ĐÃ ĐO 12/11: lượt 1 lần gọi = 35.155 token vào ⇒ 50.000 còn dư].
CHAT_BYTE_1_LAN, CHAT_CO_DINH = 220_000, 50_000   # [ghi chú kỹ thuật] 100_000 ⇒ 220_000 (251 workflow: danh sách + danh mục khối = 106.757 byte); + CHAT_CO_DINH phải < 272.000 (bậc giá luna)
CHAT_TOI_DA = round(((CHAT_VAO + CHAT_BYTE_1_LAN + CHAT_CO_DINH) * _GIA["input"] + (CHAT_RA + CHAT_RA_MOT_LAN) * _GIA["output"]) / 1e6, 4)   # 0,065 USD
CHAT_LUOT_MOT_CONV = 8   # [ghi chú kỹ thuật] quá số lượt này trong một cuộc trò chuyện ⇒ trang tự mở cuộc mới (bước tóm tắt lịch sử bằng AI kích khi > 10 lượt)


def tien_chat(vao: int, ra: int) -> float:
    """Tiền THẬT một lượt chat từ dòng log [OPERATOR_USAGE] (token ra đã gồm phần suy nghĩ)."""
    return (vao * _GIA["input"] + ra * _GIA["output"]) / 1e6


# [ghi chú kỹ thuật] flexible_code_interpreter_node có thể chạy trên sandbox E2B (va_e2b, cờ CHAY_THU_E2B_KHOI ở
# ab-worker-temporal) ⇒ + tiền máy E2B cận trên: TTL ép 180 s (CHAY_THU_E2B_TTL, kẹp 30–180; E2B tự tắt máy ở TTL, không keepalive)
# × giá cấu hình mặc định lớn nhất E2B ghi 2 vCPU + 4 GiB = 0,000046 USD/s (BAO_CAO_E2B.md mục 2)
# × số máy tối đa một khối = 3 lần thử Temporal (_AGENT_RETRY, temporal/config.py; categories.py:86) × (1 + 1 lần tạo lại — sandbox_runtime _respin_impl) = 6.
# = 0,0497 USD. Tính cả khi cờ tắt (trang không thấy cờ của hộp worker) ⇒ thà dư. Template riêng 1 vCPU / 1 GiB (bậc sau) ⇒ 0,0000185 USD/s ⇒ 0,0200.
# [ghi chú kỹ thuật] giá / giây theo TEMPLATE ở MỘT file va_e2b/gia_e2b.json (sandbox_runtime + chay_s5 cùng đọc). Mức khối dùng giá CAO NHẤT
# trong bảng. Đọc lỗi ⇒ giá cận trên cứng 0,000046 (2 vCPU + 4 GiB) — trang không bao giờ giữ chỗ THẤP hơn vì lỗi đọc.
E2B_GIA_FILE = Path(__file__).resolve().parent.parent / "va_e2b" / "gia_e2b.json"
try:
    E2B_GIA_THEO_TEMPLATE = {str(k): float(v) for k, v in json.loads(E2B_GIA_FILE.read_text(encoding="utf-8"))["gia_giay"].items()}
except Exception:
    E2B_GIA_THEO_TEMPLATE = {}
E2B_TTL_GIAY, E2B_GIA_GIAY, E2B_MAY_TOI_DA = 180, max([0.000046, *E2B_GIA_THEO_TEMPLATE.values()]), 3 * (1 + 1)   # [ghi chú kỹ thuật] TTL 180 + tạo lại ≤ 1
E2B_TOI_DA = round(E2B_TTL_GIAY * E2B_GIA_GIAY * E2B_MAY_TOI_DA, 4)   # 0,04968 ⇒ 0,0497

# None = gọi AI nhưng CHƯA có trần trong code ⇒ chặn. 0 = không gọi AI (đã đọc code).
MUC_TOI_DA: dict[str, float | None] = {
    # không gọi AI
    "text_input_node": 0, "text_output_node": 0, "markdown_output_node": 0, "file_input_node": 0, "file_browser_output_node": 0,
    # đi qua trần token đã vá (lớp gốc AgentBaseNode hoặc e2b_chat_service)
    "agent_node": KHOI_AGENT, "flexible_code_interpreter_node": round(KHOI_AGENT + E2B_TOI_DA, 4), "skill_code_interpreter_node": KHOI_AGENT,   # [ghi chú kỹ thuật] + E2B
    "pptx_chat_node": KHOI_TAI_LIEU, "docx_chat_node": KHOI_TAI_LIEU, "xlsx_chat_node": KHOI_TAI_LIEU,   # [va_tran_tai_lieu] trước KHOI_AGENT
    # trần cứng theo ký tự
    "text_to_speech_output_node": KHOI_GIONG,
    # công cụ Exa gắn vào khối Agent: tiền ở sổ Exa riêng, hộp capitol-mcp-exa tự giữ chỗ + từ chối khi vượt (va_exa/may_chu.py:9, :60)
    "EXA_SEARCH": 0, "EXA_GET_CONTENTS_ACTION": 0,
    "human_intervention_node": KHOI_DUYET,   # chỉ bản tóm tắt gọi AI; xuất Excel sau duyệt gọi /api/export/xlsx, không AI (node.py:164)
    # gọi AI qua dịch vụ riêng, chưa có trần token ⇒ chặn (nhóm phát triển xác nhận 11/11: để nguyên, không vá khi người dùng chưa cần)
    # [ghi chú kỹ thuật] 2 khối đi CÙNG dịch vụ E2BSkillsChatService với pptx / docx / xlsx (agentic-backend 4.10.0
    # nodes/output/data_analysis/node.py:32,78,365 · d3_data_visualization/node.py:15,61,173 — mỗi khối gọi
    # stream_* đúng 1 lần) ⇒ cùng trần: sua_doi/services/pydantic_ai_skills/e2b_chat_service.py:60-61 (120k vào / 16k ra), :1013-1019
    # (UsageLimits mang phần đã dùng qua các lần nối lại :1095-1097), :1493 (max_tokens ≤ 8.000 / lần), :922, :1485-1490 (chỉ gpt-6-luna khi hộp phiên bật).
    "data_analysis_agent_node": KHOI_TAI_LIEU, "d3_data_visualization_node": KHOI_TAI_LIEU,   # [va_tran_tai_lieu] cùng e2b_chat_service
    # [ghi chú kỹ thuật] network_graph KHÔNG đi đường E2B ở bản đang gắn: sua_doi/nodes/output/org_chart/node.py + CHAY_THU_DO_THI_KHOI
    # (bật mặc định) gọi _do_thi_khoi._soan (:231-241) — Agent(retries=2), không UsageLimits, đầu vào không trần ⇒ giữ chặn tới khi vá _soan.
    # [ghi chú kỹ thuật] ĐÃ VÁ _soan (va_muc_toi_da_r2/_do_thi_khoi.py, gắn ⇒ sua_doi/nodes/output/_do_thi_khoi.py): UsageLimits
    # request 3 / vào 120k / ra 12k + max_tokens 4k + cắt yêu cầu ≤ 40.000 byte, dặn thêm ≤ 8.000 byte ⇒ mức = DO_THI_TOI_DA (dưới). Gắn mức
    # này mà chưa gắn file vá ⇒ SAI (như vercel).
    "network_graph_output_node": None,   # đặt lại ngay sau bảng = DO_THI_TOI_DA
    # Đăng trang (đối chiếu kỹ thuật): nối lại streaming tới 3 lần KHÔNG mang token đã dùng (streaming.py: agent.iter mới mỗi lần) —
    # cùng rủi ro còn lại đã chấp nhận cho khối Agent (đầu file) ⇒ không ×3 riêng. Đăng trang: bản vá gốc chỉ có request_limit (sua_doi/nodes/agent/vercel_deploy/node.py:370) ⇒ KHÔNG trần token. Mức dưới CHỈ đúng khi gắn
    # cùng lúc va_muc_toi_da/vercel_deploy_node.py (thêm input/output_tokens_limit như khối Agent). Gắn muc_toi_da mà chưa gắn file đó ⇒ SAI.
    "vercel_deploy_agent_node": KHOI_AGENT,
    # chưa có trần cứng trong code ⇒ giữ chặn (đề xuất vá: va_muc_toi_da/DE_XUAT.md mục 3)
    "text_redaction_node": None, "pdf_redaction_node": None, "evals_node": None,
    "agentic_deep_research_node": None,   # đặt lại dưới = DR_TOI_DA (đối chiếu kỹ thuật)
    # guardrails_node: xem _muc_khoi — chế độ code 0, chế độ AI chưa có trần
}


# Đồ thị: trần vào là TỔNG đã dùng, kiểm TRƯỚC mỗi lần ⇒ lần cuối thêm tới 1 ngữ cảnh. Ngữ cảnh 1 lần ≤ lời dặn cố định 675 byte + đoạn grounding
# + dặn thêm 8.000 + yêu cầu 40.000 + khuôn DoThi + lịch sử ≤ 2 lần trả lời (2 × 4.000) ⇒ lấy cận trên 64.000 token (token ≤ byte). Ra kiểm SAU ⇒ +1 lần 4.000.
# [đã đo 16/11, D minor 2] grounding.py cả file 4.136 B (chữ cố định) · khuôn DoThi.model_json_schema() 1.415 B (đo trong hộp worker) ⇒ tổng ≈ 62,2k ≤ 64k.
DO_THI_TRAN_VAO, DO_THI_NGU_CANH, DO_THI_TRAN_RA, DO_THI_RA_MOT_LAN = 120_000, 64_000, 12_000, 4_000   # BẰNG số trong _do_thi_khoi.py vá
DO_THI_TOI_DA = round(((DO_THI_TRAN_VAO + DO_THI_NGU_CANH) * _GIA["input"] + (DO_THI_TRAN_RA + DO_THI_RA_MOT_LAN) * _GIA["output"]) / 1e6 + 5e-5, 4)
MUC_TOI_DA["network_graph_output_node"] = DO_THI_TOI_DA
# [ghi chú kỹ thuật] chú thích D 13/11 ở trên KHÔNG còn đúng: dòng gắn sua_doi/nodes/output/org_chart/node.py bị
# comment ở compose.yaml:118, :204, compose.temporal.yaml:65 từ 09/11 (Q1) ⇒ trong hộp org_chart/node.py = gốc 9441e024 ⇒ Network Graph đi
# E2BSkillsChatService (org_chart/node.py:60) với trần khối tài liệu ⇒ mức = KHOI_TAI_LIEU (DO_THI_TOI_DA 0,0265 thiếu).
MUC_TOI_DA["network_graph_output_node"] = KHOI_TAI_LIEU
# [ghi chú kỹ thuật] Che thông tin (text_redaction_node — nodes/redaction/node.py gọi redact_text HOẶC redact_json đúng 1 lần) — ĐÃ VÁ
# va_muc_toi_da_r2/redaction_service.py (gắn ⇒ sua_doi/services/redaction/): vượt trần ⇒ TỪ CHỐI (không cắt — phần cắt sẽ lộ).
# Đường chữ: ≤ CHE_DOAN_TOI_DA lần gọi; mỗi lần vào ≤ lời dặn tách từ 299 + khuôn 72 + cấu hình 4.000 + đoạn 6.000 byte; ra ≤ max_tokens 2.000.
# Đường JSON: 1 lần; vào ≤ lời dặn 1.074 + thêm 226 + khuôn 72 + cấu hình 4.000 + JSON 12.000 byte; ra ≤ 16.000. Mức = đường lớn hơn.
CHE_DOAN_TOI_DA, CHE_DOAN_BYTE, CHE_CAU_HINH_BYTE, CHE_RA_MOT_LAN, CHE_JSON_BYTE, CHE_JSON_RA = 40, 6_000, 4_000, 2_000, 12_000, 16_000   # BẰNG file vá
_CHE_CHU = CHE_DOAN_TOI_DA * ((299 + 72 + CHE_CAU_HINH_BYTE + CHE_DOAN_BYTE) * _GIA["input"] + CHE_RA_MOT_LAN * _GIA["output"]) / 1e6
_CHE_JSON = ((1_074 + 226 + 72 + CHE_CAU_HINH_BYTE + CHE_JSON_BYTE) * _GIA["input"] + CHE_JSON_RA * _GIA["output"]) / 1e6
CHE_TOI_DA = round(max(_CHE_CHU, _CHE_JSON) + 5e-5, 4)
MUC_TOI_DA["text_redaction_node"] = CHE_TOI_DA
# [ghi chú kỹ thuật] Che PDF chế độ AI: cùng lời dặn tách từ + khuôn + cấu hình + đoạn như đường chữ, TỔNG ≤ CHE_DOAN_TOI_DA đoạn / khối
# (va_che_pdf/redaction_service.py kiểm trước lần gọi đầu) ⇒ mức = _CHE_CHU. Chế độ regex không gọi AI (≤ mức).
MUC_TOI_DA["pdf_redaction_node"] = round(_CHE_CHU + 5e-5, 4)   # gắn mức này mà chưa gắn va_che_pdf/redaction_service.py ⇒ SAI
# [ghi chú kỹ thuật] Đánh giá (evals_node) CHỈ pass_fail / score — eval-service vá va_muc_toi_da_r2/evals/eval_runner.py
# (EVAL_CHI_PASS_FAIL=1: ≤ 5 bài, đúng 1 đầu vào chữ, không file / URL, dữ liệu chấm đã mã hoá như lời dặn ≤ 60.000 byte, cấu hình bài ≤ 4.000 byte;
# pipeline từ chối) + gpt5_service.py (ra ≤ 8.000 / lần) + agentic-backend services/platform/evals.py (v2 exemplar / dimensional từ chối).
# Mỗi bài × đầu vào = đúng 1 aquery (pass_fail_eval.py:298, score_eval.py:255; max_retries=0) ⇒ ≤ 5 lần. Phần cố định của lời dặn ≤ tổng byte MỌI chuỗi
# trong pass_fail_eval.py 5.607 + base.py 455 + _rubric.py 7.285 + _attachment_helpers.py 5.116 = 18.463 (đo 16/11, cận trên) ⇒ lấy 20.000.
EVAL_BAI, EVAL_CO_DINH, EVAL_TIEU_CHI, EVAL_DU_LIEU, EVAL_RA_MOT_LAN = 5, 20_000, 4_000, 60_000, 8_000   # BẰNG các file vá
EVAL_TOI_DA = round(EVAL_BAI * ((EVAL_CO_DINH + EVAL_TIEU_CHI + EVAL_DU_LIEU) * _GIA["input"] + EVAL_RA_MOT_LAN * _GIA["output"]) / 1e6 + 5e-5, 4)
MUC_TOI_DA["evals_node"] = EVAL_TOI_DA
# [ghi chú kỹ thuật] Rào chắn chế độ AI — ĐÃ VÁ va_muc_toi_da_r2/rao_ai/custom_guardrail_check_service.py (gắn ⇒ sua_doi/services/
# custom_guardrail_check/): ≤ 5 luật, mỗi luật 1 agent.run (song song) với UsageLimits(request 2 = 1 + 1 lần sửa khuôn đầu ra, ra ≤ 2 × 4.000) +
# max_tokens 4.000; tin gửi mỗi luật ≤ 64.000 byte (đo chính chuỗi format) — vượt ⇒ TỪ CHỐI cả khối. Lần 2 mang thêm lần trả lời 1 (≤ 4.000).
RAO_AI_LUAT, RAO_AI_LOI_DAN, RAO_AI_TIN, RAO_AI_RA_MOT_LAN, RAO_AI_LAN = 5, 1_172, 64_000, 4_000, 2   # BẰNG file vá (lời dặn hệ thống đo 16/11)
RAO_AI_TOI_DA = round(RAO_AI_LUAT * (RAO_AI_LAN * (RAO_AI_LOI_DAN + RAO_AI_TIN + RAO_AI_RA_MOT_LAN) * _GIA["input"]
                                     + RAO_AI_LAN * RAO_AI_RA_MOT_LAN * _GIA["output"]) / 1e6 + 5e-5, 4)
# [ghi chú kỹ thuật] Nghiên cứu sâu agentic — ĐÃ VÁ va_deep_research/node.py (gắn ⇒
# /srv/src/nodes/agentic_deep_research/node.py): MỘT RunUsage chung mọi vòng + UsageLimits(request 30, vào 240k [ghi chú kỹ thuật], ra 48k, tool_calls 10) +
# max_tokens 8.000; vòng soát ≤ 2. Vào kiểm TRƯỚC lần gọi theo tổng ⇒ lần cuối thêm ≤ 1 ngữ cảnh (lấy 120k như KHOI_AGENT); ra kiểm SAU ⇒ +8k.
# Công cụ tra web = MCP Exa của mình: ≤ DR_CONG_CU lần × DR_EXA_MOT_LAN (= mức giữ chỗ / lần của chính hộp Exa, va_exa/may_chu.py:31 —
# ƯỚC của mình, không phải trần cứng phía Exa) — nhóm phát triển xác nhận: tiền Exa tính VÀO trần ngày chung (sổ Exa riêng vẫn chặn thêm).
# [ghi chú kỹ thuật] vào 240k + lần cuối vượt ≤ 265k (đối chiếu kỹ thuật) ⇒ mọi lần gọi < 272k (giá cao hơn trên mốc này).
DR_TRAN_VAO, DR_NGU_CANH, DR_TRAN_RA, DR_RA_MOT_LAN, DR_CONG_CU, DR_EXA_MOT_LAN = 240_000, 265_000, 48_000, 8_000, 10, 0.02   # BẰNG file vá
DR_TOI_DA = round(((DR_TRAN_VAO + DR_NGU_CANH) * _GIA["input"] + (DR_TRAN_RA + DR_RA_MOT_LAN) * _GIA["output"]) / 1e6
                  + DR_CONG_CU * DR_EXA_MOT_LAN + 5e-5, 4)
MUC_TOI_DA["agentic_deep_research_node"] = DR_TOI_DA   # gắn mức này mà chưa gắn file vá ⇒ SAI   # gắn mức này mà chưa gắn 3 file vá + cờ EVAL_CHI_PASS_FAIL=1 ở eval-service ⇒ SAI   # pdf_redaction_node vẫn None (đường PDF render trang ⇒ số đoạn theo số trang, chưa kẹp)


# [ghi chú kỹ thuật] Khối hỏi kho bảng (collections_node) — AI sinh SQL chạy TRONG platform-api (bản vá
# sua_doi/platform_api/utils/llm_athena.py: _KB_TRAN_VAO 30k vào, _KB_TRAN_RA 4k ra / lần, 1 ứng viên ⇒ ≤ 2 lần gọi / dataset: ứng viên + thử lại
# :1437-1447). Khối hỏi TỪNG dataset trong ô "datasets" (AB nodes/datasets/structured/node.py:116) ⇒ mức = số dataset × KB_LAN_MOI_DS lần.
# > KB_TRAN_DATASET dataset ⇒ None ⇒ trang CHẶN (không đoán). 5 dataset ⇒ 0,2001 USD (trước 17/11 chiều: 0,05 — thiếu thử lại của AB).
# [ghi chú kỹ thuật] agentic-backend bọc query_structured_collection bằng
#   @async_retry(retry_on=_is_transient_platform_error, max_retries=3) (AB services/platform/collections.py:497; thử lại MỌI lỗi 5xx — platform-api đổi
#   AthenaQueryError thành 502, P routes/collections.py ~3949-3957) ⇒ ca xấu = (1 + 3) lần thử × 2 lời gọi = 8 / dataset. Số 3 KHÔNG tin tay:
#   dung_lai_sau_bat.sh mục 5c đọc đúng dòng decorator trong hộp ab-api — khác ⇒ HỎNG (phải sửa số ở đây).
KB_LAN_MOI_LAN_THU, KB_THU_LAI_AB = 2, 3   # 2 = ứng viên + thử lại trong llm_athena :1437-1447 · 3 = max_retries của AB (dung_lai kiểm)
KB_TRAN_VAO, KB_TRAN_RA, KB_LAN_MOI_DS, KB_TRAN_DATASET = 30_000, 4_000, KB_LAN_MOI_LAN_THU * (1 + KB_THU_LAI_AB), 5   # BẰNG file vá llm_athena
KB_MOT_LAN = (KB_TRAN_VAO * _GIA["input"] + KB_TRAN_RA * _GIA["output"]) / 1e6

SERPER_LAN_TOI_DA, SERPER_MOT_LAN = 40, 0.001   # [ghi chú kỹ thuật] BẰNG sandbox/chay_s5.py SERPER_MOT_LAN; 40 = AGENT_REQUEST_LIMIT 20 × 2 lượt
RAG_LAN_TOI_DA, RAG_MOT_LAN = 5, 0.0001   # BẰNG sua_doi/nodes/agent/rag_tool.py LAN_TOI_DA và sandbox/chay_s5.py RAG_MOT_LAN


def mo_hinh_cua_khoi(st: dict) -> str | None:
    """Tên mô hình khối sẽ GỬI lên cổng: khối kiểu Agent ⇒ mô hình ghi trong file mô hình khối (dòng theo loại khối, rồi mac_dinh) nếu có; còn lại ⇒ ô model của nút."""
    loai = st.get("node_id")
    loai = NGUOI_GAC_THEO.get(loai, loai)   # [va_nguoi_gac_claude] che PDF đọc dòng của che chữ (cùng dịch vụ che)
    if TIEN_BEDROCK and loai in KHOI_EP_MO_HINH + KHOI_HOP_PHIEN + KHOI_NGUOI_GAC:
        # [bản 2] đọc file MỖI LẦN (đổi mô hình không cần bật lại may_chu). File hỏng / lỗi đọc ⇒ trả tên chắc chắn NGOÀI bảng ⇒ muc_chi_tiet chặn khối (None).
        try:
            mac_dinh, theo_khoi = _doc_ep_mo_hinh()
        except (ValueError, OSError):
            return "?file mô hình khối hỏng"
        ep = theo_khoi.get(loai) or mac_dinh
        if ep:
            return ep
    v = next((p.get("value") for p in st.get("params", []) if p.get("field_id") == "model"), None)
    return v.strip() if isinstance(v, str) and v.strip() else None


def _phan_ngoai_mo_hinh(st: dict, m: float) -> float:
    """Phần của mức KHÔNG phải tiền mô hình của khối kiểu Agent: máy E2B của khối chạy code, tra tài liệu (RAG) của khối Agent. Suy từ chính công thức mức:
    mức = tiền mô hình (KHOI_AGENT) + phần ngoài ⇒ phần ngoài = mức − KHOI_AGENT (không âm)."""
    ngoai = max(0.0, m - KHOI_AGENT)
    # [ghi chú kỹ thuật] tra web (Serper, 0,001 USD / lần — tiền PHẢI BÁO): trước đây nằm lọt trong 0,075 của mô hình; nay tiền mô hình = 0 ⇒ phải giữ chỗ RIÊNG, làm như RAG.
    # Cận trên số lần: mỗi lần gọi mô hình ≥ 1 công cụ; khối ≤ AGENT_REQUEST_LIMIT (20, đọc từ 3 hộp ab-* 18/11) lần gọi × 2 (làm lại 1 lần) = 40 lần.
    # Giới hạn: mô hình gọi NHIỀU công cụ trong một lần thì vượt con số này — sổ lượt vẫn ghi số lần thật (chay_s5 đếm tool-call web_search).
    if st.get("node_id") == "agent_node":
        v = next((p.get("value") for p in st.get("params", []) if p.get("field_id") == "tool_web_search"), None)
        if v is True or str(v).strip().lower() in ("true", "1", "yes", "on"):
            ngoai += SERPER_LAN_TOI_DA * SERPER_MOT_LAN
    return round(ngoai, 6)


def _bedrock_uoc_dr(g: tuple[float, float]) -> float:
    """[ghi chú kỹ thuật] Mức Bedrock ƯỚC (tham khảo, không tính trần, không phải cận trên) của khối tra cứu sâu chạy Claude: cùng số token với công thức
    DR_TOI_DA của luna (240k + 265k vào, 48k + 8k ra) nhân giá Claude. Tiền tra web (Exa) KHÔNG nằm ở đây — vẫn là phần phải báo."""
    return round(((DR_TRAN_VAO + DR_NGU_CANH) * g[0] + (DR_TRAN_RA + DR_RA_MOT_LAN) * g[1]) / 1e6, 4)


def _bedrock_uoc_nguoi_gac(m: float, g: tuple[float, float]) -> float:
    """[ghi chú kỹ thuật] Mức Bedrock ƯỚC (THAM KHẢO, không tính trần, không phải cận trên) của một người gác chạy Claude: mức luna của chính khối đó
    (m — toàn là tiền mô hình) nhân tỉ lệ giá Claude / luna lớn hơn trong hai chiều vào / ra. Chạy Claude không còn trần đầu ra ⇒ lượt thật có thể vượt; tiền thật xem sổ cổng."""
    return round(m * max(g[0] / _GIA["input"], g[1] / _GIA["output"]), 4)


def _bedrock_uoc(g: tuple[float, float]) -> float:
    """Mức Bedrock ƯỚC của một khối kiểu Agent theo giá bảng g = (vào, ra) USD / 1 triệu token — cùng công thức KHOI_AGENT (trần token × 2 lượt), KHÔNG tính trần.
    [ghi chú kỹ thuật] THAM KHẢO, không phải cận trên: khối chạy Claude 5.5 không còn trần tổng token (người dùng "bỏ trần luôn") ⇒ lượt thật có thể vượt; tiền thật xem sổ cổng."""
    return round(2 * ((TRAN_TOKEN_VAO + VUOT_1_LAN_VAO) * g[0] + (TRAN_TOKEN_RA + VUOT_1_LAN_RA) * g[1]) / 1e6, 4)


def _bedrock_uoc_tai_lieu(g: tuple[float, float]) -> float:
    """[ghi chú kỹ thuật] Mức Bedrock ƯỚC (THAM KHẢO, không tính trần, KHÔNG phải cận trên) của một khối đi hộp phiên chạy Claude: cùng số token với
    công thức KHOI_TAI_LIEU của luna (2 × 200k vào + 24k + 8k ra) nhân giá Claude. Từ 19/11 khối chạy Claude KHÔNG còn trần tổng token (người dùng "bỏ trần luôn") ⇒ lượt
    thật có thể vượt số này; tiền thật xem sổ cổng."""
    return round(((2 * TRAN_TAI_LIEU_VAO) * g[0] + (TRAN_TAI_LIEU_RA + TAI_LIEU_RA_MOT_LAN) * g[1]) / 1e6, 4)


def muc_chi_tiet(st: dict) -> tuple[float | None, float]:
    """(mức PHẢI BÁO — None = chặn, mức Bedrock ước). Cờ tắt / khối không dùng Bedrock ⇒ (mức như bản trước, 0)."""
    m = _muc_khoi_luna(st)
    if not m:
        return m, 0.0   # None (chưa có mức) hoặc 0 (không gọi AI) — giữ nguyên: bỏ chặn tiền Bedrock KHÔNG mở khối chưa có mức
    if _loi_bang():
        return None, 0.0
    v = mo_hinh_cua_khoi(st)
    if v:
        g = GIA_BANG.get(v) or GIA_BANG.get(v.rsplit(":", 1)[-1].strip())   # [ghi chú kỹ thuật] ô có tiền tố nhà cung cấp ("openai-responses:gpt-6-luna")
        if g is None:
            return None, 0.0   # tên ngoài bảng (cổng sẽ 400) ⇒ chặn
        ncc = NCC_BANG.get(v) or NCC_BANG.get(v.rsplit(":", 1)[-1].strip())
        if TIEN_BEDROCK and ncc == NCC_BEDROCK and st.get("node_id") == "agentic_deep_research_node" and "agentic_deep_research_node" in KHOI_EP_MO_HINH:
            # [va_skill_dr_claude] tra cứu sâu chạy Claude: phần PHẢI BÁO = tiền tra web trả phí (≤ DR_CONG_CU lần × DR_EXA_MOT_LAN — tính trần ngày);
            # tiền mô hình là mức ước tham khảo. KHÔNG dùng công thức "mức − KHOI_AGENT" của khối Agent (mức luna của tra cứu sâu khác hẳn).
            return round(DR_CONG_CU * DR_EXA_MOT_LAN, 6), _bedrock_uoc_dr(g)
        if TIEN_BEDROCK and ncc == NCC_BEDROCK and NGUOI_GAC_THEO.get(st.get("node_id"), st.get("node_id")) in KHOI_NGUOI_GAC:
            # [va_nguoi_gac_claude] Rào chế độ AI / Che thông tin / bản tóm tắt cho Người duyệt chạy Claude: mức luna của các khối này TOÀN là tiền mô hình
            # (không E2B, không tra web, không công cụ trả phí) ⇒ phần phải báo = 0; tiền Claude là mức ước tham khảo, không tính trần.
            return 0.0, _bedrock_uoc_nguoi_gac(m, g)
        if TIEN_BEDROCK and ncc == NCC_BEDROCK and st.get("node_id") in KHOI_EP_MO_HINH:
            return _phan_ngoai_mo_hinh(st, m), _bedrock_uoc(g)   # tiền mô hình Bedrock không tính trần; phần E2B / RAG của khối vẫn tính
        if TIEN_BEDROCK and ncc == NCC_BEDROCK and st.get("node_id") in KHOI_HOP_PHIEN:
            # [va_hop_phien_claude] khối đi hộp phiên: mức luna (KHOI_TAI_LIEU) TOÀN là tiền mô hình — hộp phiên là hộp Docker trên máy mình (không E2B),
            # khối từ chối mcp_servers, không tra web ⇒ phần phải báo = 0; tiền Claude là mức ước, không tính trần.
            # [ghi chú kỹ thuật] khối làm file được FILE NƠI CHẠY đặt `e2b` ⇒ phần phải báo (tính trần ngày) = mức máy ảo E2B tối đa, như khối
            # Chạy code; đặt `may` / chưa có dòng ⇒ 0 như cũ. Đọc file mỗi lần gọi ⇒ đổi dòng là mức đổi theo, không cần bật lại trang. Đọc lỗi ⇒ tính CÓ E2B (thà dư).
            try:
                import noi_chay as _NC
                _noi = _NC.noi_chay(st.get("node_id")); _e2b = _noi["noi"] == "e2b" or bool(_noi["loi"])
            except Exception:  # noqa: BLE001
                _e2b = True
            return (E2B_TOI_DA if _e2b else 0.0), _bedrock_uoc_tai_lieu(g)
        if g[0] > _GIA["input"] or g[1] > _GIA["output"]:
            return None, 0.0   # mô hình KHÔNG phải bedrock mà đắt hơn giá đã tính mức ⇒ không giữ chỗ đúng được ⇒ chặn (như bản trước)
    return m, 0.0


def _muc_khoi(st: dict) -> float | None:
    """Mức PHẢI BÁO của khối (giữ chỗ, trần, ngưỡng tự chạy). Giữ tên + kiểu trả cũ cho mọi người gọi (toi_da, chay_s5…)."""
    return muc_chi_tiet(st)[0]


def _muc_khoi_luna(st: dict) -> float | None:
    loai = st.get("node_id")
    if loai == "guardrails_node":
        # cùng quy tắc với sua_doi/nodes/governance/guardrails/node.py:132-136 (_che_do): thiếu / rỗng / "ai" ⇒ AI
        v = next((p.get("value") for p in st.get("params", []) if p.get("field_id") == "che_do_kiem"), None)
        return 0 if str(v or "").strip().lower() in ("code", "sql") else RAO_AI_TOI_DA   # [ghi chú kỹ thuật] "sql" không gọi AI ⇒ 0 · [ghi chú kỹ thuật] chế độ AI ⇒ RAO_AI_TOI_DA
    if loai == "collections_node":
        v = next((p.get("value") for p in st.get("params", []) if p.get("field_id") == "datasets"), None)
        n = len(v) if isinstance(v, list) else (1 if v else 0)
        if n < 1 or n > KB_TRAN_DATASET:
            return None   # 0 dataset (khối tự báo lỗi) hoặc quá trần ⇒ không có mức ⇒ trang chặn
        return round(n * KB_LAN_MOI_DS * KB_MOT_LAN + 5e-5, 4)
    m = MUC_TOI_DA.get(loai)
    if loai == "agent_node" and m is not None:
        # [ghi chú kỹ thuật] công cụ rag_search (va_rag): tạo vector câu hỏi phía app RAG — NGOÀI tiền luna ⇒ + ≤ 5 lần × RAG_MOT_LAN
        # (rag_tool.py LAN_TOI_DA = 5; sổ đếm tool-call × 0,0001: sandbox/chay_s5.py RAG_MOT_LAN). Đọc ô giống native_tools._on (True / "true"…).
        v = next((p.get("value") for p in st.get("params", []) if p.get("field_id") == "tool_rag_search"), None)
        if v is True or str(v).strip().lower() in ("true", "1", "yes", "on"):
            m = round(m + RAG_LAN_TOI_DA * RAG_MOT_LAN, 6)
    return m


def toi_da(payload: dict) -> tuple[float, list[str]]:
    """Trả (tổng USD tối đa, danh sách khối chưa có mức tối đa). Danh sách khác rỗng ⇒ người gọi KHÔNG được chạy."""
    tong, thieu = 0.0, []
    for n in payload.get("nodes", []):
        st = (n.get("data") or {}).get("struct") or {}
        m = _muc_khoi(st)
        if m is None:
            thieu.append(f"{st.get('name') or n.get('id')} ({st.get('node_id')})" + (f" — {_loi_bang()}" if _loi_bang() and _muc_khoi_luna(st) else ""))
        else:
            tong += m
    return round(tong, 4), thieu


def toi_da_chi_tiet(payload: dict) -> dict:
    """{"phai_bao": tổng mức phải báo, "bedrock_uoc": tổng mức Bedrock ước (không tính trần), "thieu": [khối chưa có mức]} — cho trang hiển thị + sổ."""
    pb, br, thieu = 0.0, 0.0, []
    for n in payload.get("nodes", []):
        st = (n.get("data") or {}).get("struct") or {}
        m, b = muc_chi_tiet(st)
        if m is None:
            thieu.append(f"{st.get('name') or n.get('id')} ({st.get('node_id')})")
        else:
            pb += m; br += b
    return {"phai_bao": round(pb, 4), "bedrock_uoc": round(br, 4), "thieu": thieu}
