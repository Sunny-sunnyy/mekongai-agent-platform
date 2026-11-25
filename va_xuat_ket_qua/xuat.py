"""Xuất "kết quả vừa trả lời" ra Excel / Word / PDF / PowerPoint — phần chạy trên MÁY CHỦ TRANG (python3 hệ thống). B 18/11/2026, nhóm phát triển xác nhận.
C gắn đường /api/… + nút tải (đối chiếu kỹ thuật). Hàm thuần, không giữ khoá, không gọi AI:

  from va_xuat_ket_qua import xuat
  goi = xuat.lay_goi(ket_qua_luot(phien))          # gói Rào chắn chế độ code của CHÍNH lượt — None ⇒ lượt không có bảng số
  ten, raw = xuat.xuat(goi, "xlsx")                 # lỗi ⇒ xuat.LoiXuat(lời thường) — KHÔNG có file dở

Bên gọi PHẢI chỉ đưa mã phiên mà máy chủ biết là của cuộc trò chuyện đang hỏi (đối chiếu kỹ thuật) — hàm này không tự biết điều đó.
Kiểm 2 lần: ở đây (gói đủ hình, kiem.dat, sha) và trong hộp (dung_file.kiem_goi + đọc lại file so TỪNG số). Hộp dựng: capitol-ab-worker (có sẵn
openpyxl / python-docx / pptx / reportlab / PyMuPDF; python3 của máy chủ trang không có) — gửi script qua stdin, không gắn file, không tạo lại hộp.
"""
import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path

DANG = {"xlsx", "docx", "pdf", "pptx"}
HOP = "capitol-ab-worker"
GIAY = 120                   # một lần xuất (đối chiếu kỹ thuật)
MAX_GOI = 2 * 1024 * 1024    # gói gửi vào hộp
MAX_RA = 10 * 1024 * 1024    # file trả về (sau giải base64)
SCRIPT = Path(__file__).with_name("dung_file.py")
TEN = re.compile(r"[A-Za-z0-9_.\-]{1,120}\.(xlsx|docx|pdf|pptx)")


class LoiXuat(Exception):
    """Lỗi xuất — chữ là lời thường cho người dùng."""


def _sha_bang(b: dict) -> str:
    return hashlib.sha256(json.dumps(b, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def lay_goi(ket_qua: dict | None) -> dict | None:
    """Từ ket_qua_luot(phien) của máy chủ trang ({mã khối: {"kieu", "v"}}) lấy gói Rào chế độ code (dict có bang_thuong + sha256_bang). Nhiều gói
    (lượt có > 1 khối Rào) ⇒ None (không đoán gói nào là câu trả lời)."""
    ds = [x["v"] for x in (ket_qua or {}).values()
          if isinstance(x, dict) and isinstance(x.get("v"), dict) and {"bang_thuong", "sha256_bang", "kiem"} <= set(x["v"])]
    return ds[0] if len(ds) == 1 else None


def xuat(goi: dict | None, dang: str, *, script: Path = SCRIPT, xf: str | None = None, hop: str = HOP, cau_hoi: dict | None = None) -> tuple[str, bytes]:
    """(tên file, byte) — như trước. [ghi chú kỹ thuật] thêm cau_hoi (mặc định None ⇒ file như trước): xem xuat_chi_tiet."""
    ten, raw, _ = xuat_chi_tiet(goi, dang, cau_hoi, script=script, xf=xf, hop=hop)
    return ten, raw


def xuat_chi_tiet(goi: dict | None, dang: str, cau_hoi: dict | None = None, *, script: Path = SCRIPT, xf: str | None = None, hop: str = HOP) -> tuple[str, bytes, dict]:
    """[ghi chú kỹ thuật] cau_hoi = {"cau": chữ câu người dùng, "cong_cu": [{"ten", "tham_so": dict, "ket_qua": dict | list}]} —
    kết quả công cụ của CHÍNH lượt (trang đọc từ lịch sử lời gọi). Bộ dựng trong hộp tự gọi lại lõi KPI để so; ở đây chỉ kiểm hình + cỡ.
    Trả (tên file, byte, {"phan_cau_hoi": file CÓ phần đầu theo câu hỏi hay không, "ly_do": chữ | None (vì sao thiếu / thiếu một phần), "cong_cu_da_in": [tên công cụ]})."""
    if dang not in DANG:
        raise LoiXuat("Chỉ xuất được Excel, Word, PDF hoặc PowerPoint.")
    if not isinstance(goi, dict) or not isinstance(goi.get("bang_thuong"), dict):
        raise LoiXuat("Câu trả lời này không có bảng số để xuất (chỉ câu trả lời KPI đã qua bước kiểm số mới xuất được).")
    if (goi.get("kiem") or {}).get("dat") is not True or _sha_bang(goi["bang_thuong"]) != goi.get("sha256_bang"):
        raise LoiXuat("Câu trả lời này chưa qua bước kiểm số (hoặc bảng số không khớp mã kiểm) — không xuất file.")
    du = {"goi": {k: goi[k] for k in ("bang_thuong", "sha256_bang", "kiem")}, "dang": dang, **({"xf": xf} if xf else {})}
    # [ghi chú kỹ thuật] gói của lượt TỰ DUYỆT khi quá hạn ⇒ chuyển ĐÚNG hai ô (kiểu, số giây chờ) cho bộ dựng:
    # file xuất từ khung chat (cả 4 dạng) mang nhãn + tên file có nhãn. Gói khác (người duyệt / không qua khối duyệt) ⇒ không chuyển gì, file như cũ.
    dy = goi.get("duyet")
    if isinstance(dy, dict) and dy.get("kieu") == "tu_duyet_qua_han":
        du["goi"]["duyet"] = {"kieu": "tu_duyet_qua_han", "cho_giay": dy.get("cho_giay")}
    if cau_hoi is not None:
        cc = cau_hoi.get("cong_cu") if isinstance(cau_hoi, dict) else None
        if not isinstance(cc, list) or not all(isinstance(x, dict) and isinstance(x.get("ten"), str) and isinstance(x.get("tham_so"), dict)
                                               and isinstance(x.get("ket_qua"), (dict, list)) for x in cc):
            raise LoiXuat("Phần nội dung theo câu hỏi của lượt này sai khuôn — không xuất file.")
        du["cau_hoi"] = {"cau": str(cau_hoi.get("cau") or "")[:500], "cong_cu": [{k: x[k] for k in ("ten", "tham_so", "ket_qua")} for x in cc[:20]]}
    than = json.dumps(du, ensure_ascii=False)
    if len(than.encode()) > MAX_GOI:
        raise LoiXuat("Bảng số quá lớn để xuất.")
    nguon = f"import json\nDU_LIEU = json.loads({json.dumps(than, ensure_ascii=False)})\n" + script.read_text(encoding="utf-8")
    try:
        p = subprocess.run(["docker", "exec", "-i", hop, "python", "-I", "-"], input=nguon.encode("utf-8"), capture_output=True, timeout=GIAY)
    except subprocess.TimeoutExpired:
        raise LoiXuat("Xuất file quá lâu — thử lại sau.") from None
    dong = [x for x in p.stdout.decode("utf-8", "replace").splitlines() if x.startswith("{")]
    try:
        kq = json.loads(dong[-1])
    except (IndexError, ValueError):
        raise LoiXuat("Hộp dựng file không trả kết quả — báo người vận hành.") from None
    if not kq.get("ok"):
        raise LoiXuat(str(kq.get("loi") or "Không dựng được file.")[:300])
    # [bản 2] tên file phải KHỚP loại gói đã gửi: gói tự duyệt ⇒ bắt buộc "TU_DUYET_QUA_HAN_NHAP_…" (bộ dựng quên nhãn ở tên ⇒ không trả file); gói khác ⇒ "NHAP_…" như cũ
    dau_ten = "TU_DUYET_QUA_HAN_NHAP_" if "duyet" in du["goi"] else "NHAP_"
    if not TEN.fullmatch(str(kq.get("ten") or "")) or not str(kq["ten"]).startswith(dau_ten):
        raise LoiXuat("Tên file trả về lạ — không trả file.")
    raw = base64.b64decode(kq.get("b64") or "", validate=True)
    if not raw or len(raw) > MAX_RA or hashlib.sha256(raw).hexdigest()[:16] != kq.get("sha16"):
        raise LoiXuat("File trả về hỏng hoặc quá lớn — không trả file.")
    da_in, chu = [str(x)[:64] for x in (kq.get("cong_cu_da_in") or [])][:20], [str(x)[:300] for x in (kq.get("chu_cau") or [])][:20]
    if cau_hoi is None or not du.get("cau_hoi", {}).get("cong_cu"):
        ly_do = "lượt này không có lần tra số nào để in — file chỉ có bảng thưởng cả đội"
    else:
        ly_do = " ".join(chu) or None
    return kq["ten"], raw, {"phan_cau_hoi": bool(da_in), "ly_do": ly_do, "cong_cu_da_in": da_in,
                            "so_da_so": kq.get("so_da_so"), "so_cau_da_so": kq.get("so_cau_da_so")}
