"""[ghi chú kỹ thuật] Hai phép kiểm THUẦN cho máy chủ trang (may_chu gọi; khi gắn file này nằm ở giao_dien/kiem_luc_chay.py):
  loi_hinh_luc_chay(payload, DM, text_inputs) ⇒ danh sách lỗi mức "loi" của phép kiểm HÌNH (capitol_chuyen_doi.kiem) trên bản sẽ chạy.
      VÌ SAO: Lưu của trang chặn khi còn lỗi kiểm hình, nhưng Chạy thì không kiểm ⇒ workflow vào bằng đường khác (trợ lý dựng, nhập R2, bản lưu trước khi luật
      đổi) còn ô bắt buộc trống vẫn chạy được rồi hỏng giữa chừng. Ô nhập chữ được ĐIỀN LÚC CHẠY (text_inputs — khung chat / ô sửa đầu vào) không tính là trống.
      Không chuyển được workflow sang hình của trang ⇒ trả None (người gọi KHÔNG chặn vì lý do này — không chặn oan workflow lạ; ghi nhật ký).
  cau_bao_hinh(loi) ⇒ câu cho người dùng: khối nào, ô nào, tối đa 5 lỗi + số còn lại.
  canh_bao_noi_file(payload, KSG) ⇒ các CẢNH BÁO luật nối file của bộ "kiểm sau ghép" (Agent chạy code phải nhận đúng 1 file .csv / .xlsx từ khối Nhận file; khối
      làm file không nhận file do khối khác sinh…) để HIỆN lúc Lưu — KHÔNG chặn (A: cảnh báo trước). Chỉ lấy đúng nhóm luật nối file, không lấy luật mô hình / loại
      khối / tầng hộp (đã có chỗ kiểm riêng)."""
from capitol_chuyen_doi import kiem, tu_capitol

MAU_NOI_FILE = ("Agent chạy code", "Agent bật chạy code", "Agent không bật chạy code", "không nhận file do khối khác sinh")


def loi_hinh_luc_chay(payload, DM, text_inputs=None):
    try:
        loi = [x for x in kiem(tu_capitol(payload, DM), DM) if x.get("muc") == "loi"]
    except Exception:  # noqa: BLE001
        return None
    dien = set(k for k, v in (text_inputs or {}).items() if isinstance(v, str) and v.strip())
    return [x for x in loi if not (x.get("code") == "o_bat_buoc_trong" and x.get("field") == "text_input" and x.get("node") in dien)]


def cau_bao_hinh(loi: list) -> str:
    dau = [str(x.get("message") or x.get("code") or "lỗi kiểm")[:200] for x in loi[:5]]
    return ("Workflow còn lỗi kiểm nên chưa chạy (chưa tốn tiền): " + " · ".join(dau) + (f" · và {len(loi) - 5} lỗi nữa" if len(loi) > 5 else "")
            + " Mở workflow trên xưởng, điền / nối các ô đó rồi Lưu, sau đó chạy lại.")


def canh_bao_noi_file(payload, KSG) -> list[str]:
    try:
        return [x for x in KSG.kiem(payload, None, lambda p: []) if any(m in x for m in MAU_NOI_FILE)]
    except Exception:  # noqa: BLE001
        return []
