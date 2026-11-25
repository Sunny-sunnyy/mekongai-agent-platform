"""[ghi chú kỹ thuật] Phía TRANG (máy chủ trang, script đo): một khối "sẽ chạy ở đâu" theo file nơi chạy — để trang HIỆN cạnh nhãn mô hình
(người vẽ workflow CHƯA được chọn; nơi chạy do người quản trị đặt trong file). Chỉ đọc. Gắn ở giao_dien/noi_chay.py.

    noi_chay("flexible_code_interpreter_node")  ⇒  {"noi": "e2b" | "may" | None, "nguon": "dong_rieng" | "mac_dinh" | "khong_co_dong" | "khong_co_file" | "khoi_ngoai_danh_sach",
                                                    "ten": "Máy ảo E2B (dịch vụ ngoài)" | "Hộp kín trên máy" | None, "loi": None | "<lời>"}
    bang_noi_chay()  ⇒  {"khoi_dat_duoc": [...], "mac_dinh": "may" | "e2b" | None, "theo_khoi": {loại khối: nơi}, "loi": None | "<lời>"}

"noi" = None nghĩa là file KHÔNG quyết cho khối này (chưa có file, hoặc khối chưa nằm trong danh sách chạy được ở cả hai nơi) ⇒ khối chạy như trước giờ.
Cùng luật đọc với hộp (sua_doi/workflow/node/base/agent/sandbox_runtime.py _noi_chay_khoi); danh sách khối đặt được đọc từ CHÍNH file đó bằng ast (một nguồn).
Giới hạn: trang không thấy được CẦU DAO trong hộp (biến CHAY_THU_E2B_KHOI, khoá E2B) — file ghi e2b mà cầu dao tắt thì khối sẽ báo lỗi lúc chạy; trang chỉ hiện điều file ghi."""
import ast
from pathlib import Path

GOC = Path(__file__).resolve().parent.parent
FILE_NOI_CHAY = GOC / "cau_hinh_mo_hinh" / "noi_chay_khoi.txt"
FILE_LOP_CHAY = GOC / "sua_doi" / "workflow" / "node" / "base" / "agent" / "sandbox_runtime.py"
TEN = {"e2b": "Máy ảo E2B (dịch vụ ngoài)", "may": "Hộp kín trên máy"}


def khoi_dat_duoc(file_lop_chay: Path = FILE_LOP_CHAY) -> tuple[str, ...]:
    for n in ast.parse(file_lop_chay.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "CHAY_THU_KHOI_NOI_CHAY" for t in n.targets):
            return tuple(ast.literal_eval(n.value))
    return ()   # lớp chạy đang gắn chưa có bậc 0 ⇒ chưa khối nào đặt được


def bang_noi_chay(file_noi_chay: Path = FILE_NOI_CHAY, file_lop_chay: Path = FILE_LOP_CHAY) -> dict:
    ds = khoi_dat_duoc(file_lop_chay); ra = {"khoi_dat_duoc": list(ds), "mac_dinh": None, "theo_khoi": {}, "co_file": file_noi_chay.is_file(), "loi": None}
    if not ra["co_file"]:
        return ra
    bang = {}
    for so, d in enumerate(file_noi_chay.read_text(encoding="utf-8").splitlines(), 1):
        d = d.split("#", 1)[0].strip()
        if not d:
            continue
        khoa, dau, noi = d.partition("="); khoa, noi = khoa.strip(), noi.strip().lower()
        if not dau or not khoa or khoa in bang or noi not in TEN or (khoa != "mac_dinh" and khoa not in ds):
            ra["loi"] = f"File nơi chạy khối dòng {so} sai — khối chạy code bị chặn tới khi sửa file."; return ra
        bang[khoa] = noi
    ra["mac_dinh"] = bang.pop("mac_dinh", None); ra["theo_khoi"] = bang
    return ra


def noi_chay(loai_khoi: str, file_noi_chay: Path = FILE_NOI_CHAY, file_lop_chay: Path = FILE_LOP_CHAY) -> dict:
    b = bang_noi_chay(file_noi_chay, file_lop_chay)
    def tra(noi, nguon): return {"noi": noi, "nguon": nguon, "ten": TEN.get(noi), "loi": b["loi"]}
    if b["loi"]: return tra(None, "file_sai")
    if loai_khoi not in b["khoi_dat_duoc"]: return tra(None, "khoi_ngoai_danh_sach")
    if not b["co_file"]: return tra(None, "khong_co_file")
    if loai_khoi in b["theo_khoi"]: return tra(b["theo_khoi"][loai_khoi], "dong_rieng")
    if b["mac_dinh"]: return tra(b["mac_dinh"], "mac_dinh")
    # [bản 3 20/11] không có dòng riêng lẫn mac_dinh ⇒ hộp sẽ DỪNG khối với lỗi rõ (không tự chọn nơi chạy) ⇒ trang báo đúng như vậy
    r = tra(None, "khong_co_dong"); r["loi"] = "File nơi chạy khối không có dòng nào cho loại khối này và không có dòng mac_dinh — khối chạy code bị chặn tới khi sửa file."
    return r
