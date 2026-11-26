"""Đọc trọn MỘT sheet của file đơn hàng (RAW) đúng một lần cho mỗi nội dung file, rồi dùng chung cho mọi phép tính.

Vì sao (đo 19/11/2026, bản sau bậc 3a): một bản dữ liệu bị mở 10 lần — 2 lúc bật backend + 2 ở lần mở đầu của mỗi tab (Điểm bán, Khách hội viên,
Đơn được tính, 1 % gia vị); nạp một file tải lên 5 lần; một lần ghép thử 5 lần. Mỗi lần đọc trọn ~24 nghìn dòng mất hàng chục giây; ngay cả lần
"chỉ đọc dòng tiêu đề" cũng mất 6–8 giây ở riêng lệnh mở. Các lần đọc đó cùng file, cùng sheet, chỉ khác cột + phép lọc.

Ở đây chỉ có việc ĐỌC FILE. Chọn cột, bảng ghép, kiểm ô / kỳ, lời dừng vẫn nằm ở từng nơi gọi (`load_raw._read_rows`, …) và chạy lại mỗi lần gọi.
- Khoá nhớ = sha256 NỘI DUNG file + tên sheet ⇒ file bị thay tại chỗ (cùng tên) luôn được đọc lại; file tạm lúc tải lên và file đã lưu cùng nội dung
  dùng chung một lần đọc.
- Giữ dòng THÔ đúng như thư viện trả, từ dòng 1: cả dòng trống, dòng tổng cuối file, dòng ngắn — cổng đối soát và số "dòng Excel N" trong lời dừng
  sống nhờ các dòng đó. Dòng là tuple ⇒ nơi gọi không sửa được dữ liệu dùng chung.
- Mở `read_only=True, data_only=True` như mọi chỗ đọc RAW trước đây (đổi chế độ mở là đổi số ở ô công thức).
- Số file giữ: biến môi trường KPI_RAW_SHEETS_KEPT (mặc định 2 = bản đang dùng + bản vừa tải lên; 0 = không giữ, mỗi lần gọi đọc lại như trước —
  đường lùi khi hộp chạy thiếu RAM). Đo 19/11: một sheet 24.129 dòng × 38 cột giữ ~56 MB.
- Trần cỡ một sheet được giữ: KPI_RAW_SHEET_MAX_CELLS (mặc định 5 triệu ô); vượt ⇒ vẫn đọc và trả kết quả, chỉ không giữ.
- Một khoá luồng cho cả lớp nhớ: tab mở lần đầu và phần tính sẵn chạy ở luồng khác nhau; không khoá thì hai luồng cùng đọc một file, RAM gấp đôi.
"""
import hashlib
import io
import os
import threading
from collections import OrderedDict
from pathlib import Path

from openpyxl import load_workbook

KEPT_ENV = "KPI_RAW_SHEETS_KEPT"
DEFAULT_KEPT = 2
MAX_CELLS_ENV = "KPI_RAW_SHEET_MAX_CELLS"
DEFAULT_MAX_CELLS = 5_000_000
_lock = threading.Lock()
_files: OrderedDict[str, dict] = OrderedDict()   # sha256 file ⇒ {"sheet_names": [...], "rows": {tên sheet: tuple các dòng}}; cuối = dùng gần nhất


def kept_files() -> int:
    """Số file giữ trong bộ nhớ. Giá trị không phải số nguyên ≥ 0 ⇒ dừng (lần gọi đầu là lúc bật backend ⇒ backend không bật với cấu hình sai)."""
    raw = os.environ.get(KEPT_ENV)
    if raw is None:
        return DEFAULT_KEPT
    if not raw.isdigit():
        raise ValueError(f"{KEPT_ENV} phải là số nguyên ≥ 0 (số file đơn hàng giữ trong bộ nhớ; 0 = không giữ) — đang là '{raw}'")
    return int(raw)


def max_kept_cells() -> int:
    """Trần số Ô (tổng số ô của mọi dòng) của một sheet được GIỮ. Vì sao: file tải lên được tới 20 MB nén / 200 MB giải nén, trong khi file thật
    1,7 MB (24.129 dòng × 38 cột ≈ 0,92 triệu ô) đã giữ ~56 MB; hộp chạy backend có trần RAM. Mặc định 5 triệu ô ≈ 300 MB (suy theo tỉ lệ đo đó)."""
    raw = os.environ.get(MAX_CELLS_ENV)
    if raw is None:
        return DEFAULT_MAX_CELLS
    if not raw.isdigit():
        raise ValueError(f"{MAX_CELLS_ENV} phải là số nguyên ≥ 0 (số ô tối đa của một sheet được giữ trong bộ nhớ) — đang là '{raw}'")
    return int(raw)


def forget(digest: str) -> None:
    """Bỏ khỏi lớp nhớ mọi sheet của file có sha256 nội dung này. Đường tải lên gọi khi file BỊ TỪ CHỐI: sheet của nó không ai dùng nữa mà vẫn chiếm
    một trong các chỗ giữ, có thể đẩy sheet của bản đang dùng ra."""
    with _lock:
        _files.pop(digest, None)


def sheet_rows(path: str | Path, sheet: str | int) -> tuple[list[str], tuple[tuple, ...] | None]:
    """(tên các sheet của file, mọi dòng của `sheet` từ dòng 1). `sheet`: tên, hoặc số thứ tự 1-based như bảng ghép.
    File không có sheet đó ⇒ (tên các sheet, None) — không ném lỗi, không tạo mục nhớ: mỗi nơi gọi tự sinh lời dừng của mình."""
    keep = kept_files()
    # Băm và ĐỌC cùng một khối byte: băm xong mới mở lại theo đường dẫn thì file bị thay giữa hai bước sẽ cho dòng MỚI nằm dưới mã băm CŨ
    # ⇒ lần sau hỏi bằng nội dung cũ nhận nhầm dòng mới. Giá: giữ thêm các byte của file trong lúc đọc (trần tải lên 20 MB).
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    stream = io.BytesIO(data)
    stream.name = str(path)   # chỉ để công cụ đếm / lời lỗi của thư viện biết tên file; nội dung đọc là `data`
    with _lock:
        entry = _files.get(digest) if keep else None
        if entry is not None:
            _files.move_to_end(digest)
            name = _sheet_name(entry["sheet_names"], sheet)
            if name is None:
                return list(entry["sheet_names"]), None
            if name in entry["rows"]:
                return list(entry["sheet_names"]), entry["rows"][name]
        book = load_workbook(stream, read_only=True, data_only=True)
        try:
            names = list(book.sheetnames)
            name = _sheet_name(names, sheet)
            if name is None:
                return names, None
            rows = tuple(book[name].iter_rows(values_only=True))
        finally:
            book.close()
        # Trần cỡ: sheet quá lớn vẫn được đọc và trả về, chỉ KHÔNG giữ lại (lần gọi sau đọc lại như trước khi có lớp nhớ).
        if keep and sum(len(row) for row in rows) <= max_kept_cells():
            _files.setdefault(digest, {"sheet_names": names, "rows": {}})["rows"][name] = rows
            _files.move_to_end(digest)
            while len(_files) > keep:
                _files.popitem(last=False)
        return names, rows


def _sheet_name(names: list[str], sheet: str | int) -> str | None:
    if isinstance(sheet, int):
        return names[sheet - 1] if 1 <= sheet <= len(names) else None
    return sheet if sheet in names else None


def forget_all() -> None:
    """Bỏ mọi sheet đang giữ (test dùng để các ca không ảnh hưởng nhau)."""
    with _lock:
        _files.clear()
