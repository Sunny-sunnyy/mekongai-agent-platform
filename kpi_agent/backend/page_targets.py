"""Chỉ tiêu riêng từng nhân viên của Thưởng thêm (Mì Kokomi 90; Meat 07, 7.1, 7.2, 7.3) rút từ TRANG theo dõi của đội (.html) người dùng tải lên — 2a-2 nấc 3.

Thay hai script tách tay `scripts/tach_chi_tieu_kokomi.py`, `scripts/tach_chi_tieu_meat.py`. Trang chỉ được ĐỌC NHƯ CHỮ: tìm hằng dữ liệu `const D={…}`, đọc
bằng bộ đọc JSON, không chạy mã nào của trang. Trang còn chứa số thực hiện / % / hạng (đáp án dùng để so) ⇒ hàm tách chỉ giữ tên `n` + chỉ tiêu `t`
của đúng 5 khoản; mọi thứ khác bỏ ngay trong hàm, không trả ra. Trang lệch khuôn ⇒ `PageProblem` mang MÃ + số đếm (không chữ của trang — trang có tên thật)."""
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

MAX_PAGE_BYTES = 5 * 1024 * 1024     # trang thật tháng 10: 0,84 MB (đo 20/11) — trần rộng gấp ~6
CONSTANT = re.compile(r'const D=(?=\{"upd")')
KOKOMI, MEAT = "kokomi", "meat"
# tên ngắn của khoản trên trang ⇒ (nhóm, khoá khối chỉ tiêu trong file lõi đọc); Kokomi là khối phẳng (khoản T06 của file luật không có `targets_key`)
PAGE_ITEMS = {"Mì Kokomi 90": (KOKOMI, None), "TPCB (điều kiện)": (MEAT, "07"), "Meat N1": (MEAT, "7.1"), "Meat N2": (MEAT, "7.2"), "Meat N3": (MEAT, "7.3")}
MILLION = Decimal(1_000_000)
MAX_TARGET_MILLIONS = Decimal(1_000_000)   # chỉ tiêu một người > 1.000 tỉ đồng ⇒ coi là số hỏng (chặn 1e999)


class PageProblem(Exception):
    """Trang không dùng được. `code` = mã cố định; `detail` = mã con + số đếm (không chữ của trang)."""

    def __init__(self, code: str, **detail):
        super().__init__(code)
        self.code, self.detail = code, detail


def _no_duplicate_keys(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise PageProblem("trang_lech_khuon", ly_do="khoa_lap")   # khoá lặp: bộ đọc JSON lặng lẽ lấy cái sau — hai người đọc trang ra hai số
    return dict(pairs)


def _no_constants(name: str):
    raise PageProblem("trang_lech_khuon", ly_do="so_khong_huu_han")   # NaN / Infinity / -Infinity


def read_constant(body: bytes) -> dict:
    """Hằng dữ liệu của trang. Quá trần / không phải chữ utf-8 / hằng xuất hiện khác MỘT lần / JSON hỏng, khoá lặp, số vô hạn, lồng quá sâu ⇒ `PageProblem`."""
    if len(body) > MAX_PAGE_BYTES:
        raise PageProblem("trang_qua_lon", tran_mb=MAX_PAGE_BYTES // (1024 * 1024))
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise PageProblem("trang_lech_khuon", ly_do="khong_phai_chu_utf8") from None
    found = CONSTANT.findall(text)
    if not found:
        # trang NGOÀI (file người dùng dễ tải nhầm nhất): chỉ là khung chứa, hằng dữ liệu nằm ở file trong thư mục `_files` đi kèm
        raise PageProblem("trang_ngoai_khong_co_du_lieu" if "<iframe" in text.lower() else "trang_lech_khuon", ly_do="khong_thay_hang_du_lieu")
    if len(found) != 1:
        raise PageProblem("trang_lech_khuon", ly_do="hang_du_lieu_lap", so_lan=len(found))   # kể cả bản nằm trong chú thích HTML — không đoán bản nào thật
    decoder = json.JSONDecoder(object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constants, parse_float=Decimal)
    try:
        data, _end = decoder.raw_decode(text[CONSTANT.search(text).end():])
    except (ValueError, RecursionError, InvalidOperation):
        raise PageProblem("trang_lech_khuon", ly_do="hang_du_lieu_khong_doc_duoc") from None
    if not isinstance(data, dict):
        raise PageProblem("trang_lech_khuon", ly_do="hang_du_lieu_khong_doc_duoc")
    return data


def _page_month(data: dict) -> str:
    """Kỳ (YYYY-MM) của ô ngày `upd` (dạng dd/mm/yyyy). Chỉ dùng để TỪ CHỐI trang của tháng khác — không vào phép tính, không làm ngày chốt."""
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", data.get("upd")) if isinstance(data.get("upd"), str) else None
    if match is None or not (1 <= int(match[1]) <= 31 and 1 <= int(match[2]) <= 12):
        raise PageProblem("trang_khong_ro_ky", ly_do="o_ngay_khong_doc_duoc")
    return f"{match[3]}-{match[2]}"


def _dong(value) -> int:
    """Ô chỉ tiêu (triệu đồng) ⇒ đồng, bằng số thập phân chính xác (không qua số thực). Không phải số > 0, quá lớn, lẻ dưới một đồng ⇒ dừng."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise PageProblem("trang_lech_khuon", ly_do="chi_tieu_khong_phai_so")
    amount = Decimal(value) * MILLION
    if not amount.is_finite() or amount <= 0 or Decimal(value) > MAX_TARGET_MILLIONS or amount != amount.to_integral_value():
        raise PageProblem("trang_lech_khuon", ly_do="chi_tieu_ngoai_khoang")
    return int(amount)


def _clean(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", name).casefold().split())


def _code_of(page_name: str, names: dict[str, str]) -> str | None:
    """Tên trên trang (trang chỉ ghi các chữ CUỐI của họ tên) ⇒ mã nhân viên: các chữ đó phải bằng NGUYÊN từng chữ cuối của họ tên (đã chuẩn hoá dấu cách /
    hoa thường), và khớp đúng MỘT người. Không ghép gần đúng. Giới hạn: hai tên hợp lệ đổi chỗ cho nhau trên trang thì không rào nào ở đây bắt được."""
    words = _clean(page_name).split()
    hits = [code for code, full in names.items() if words and _clean(full).split()[-len(words):] == words]
    return hits[0] if len(hits) == 1 else None


def extract(body: bytes, period: str, names: dict[str, str], team: set[str]) -> dict:
    """{"kokomi": {mã: đồng}, "meat": {khoá: {mã: đồng}}} của kỳ `period`. `names` = {mã nhân viên: họ tên đủ} (cột tên của file đơn hàng của bản);
    `team` = mã của đội (file chỉ tiêu của bản). Mọi khoá khác của trang (thực hiện, %, hạng, tuyến, hội viên…) không ra khỏi hàm này."""
    data = read_constant(body)
    if _page_month(data) != period:
        raise PageProblem("trang_khac_ky")
    items = data.get("bon")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise PageProblem("trang_lech_khuon", ly_do="thieu_khoi_thuong_them")
    out = {KOKOMI: None, MEAT: {}}
    for short, (group, key) in PAGE_ITEMS.items():
        found = [item for item in items if item.get("short") == short]
        if len(found) != 1:
            raise PageProblem("trang_lech_khuon", ly_do="so_khoan_khac_mot", so_khoan=len(found))
        rows = found[0].get("rows")
        if found[0].get("unit") != "tr" or not isinstance(rows, list) or not rows:
            raise PageProblem("trang_lech_khuon", ly_do="khoan_sai_don_vi_hoac_thieu_dong")
        block = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("n"), str) or "t" not in row:
                raise PageProblem("trang_lech_khuon", ly_do="dong_thieu_ten_hoac_chi_tieu")
            code = _code_of(row["n"], names)
            if code is None or code in block:
                raise PageProblem("trang_lech_doi", ly_do="ten_khong_ghep_duoc_dung_mot_nguoi")
            block[code] = _dong(row["t"])
        if set(block) != set(team):
            raise PageProblem("trang_lech_doi", ly_do="danh_sach_nguoi_khac_doi", so_nguoi_trang=len(block), so_nguoi_doi=len(team))
        if group == KOKOMI:
            out[KOKOMI] = dict(sorted(block.items()))
        else:
            out[MEAT][key] = dict(sorted(block.items()))
    return out


REFUSALS = {
    "trang_qua_lon": "trang lớn hơn mức cho phép",
    "trang_ngoai_khong_co_du_lieu": "đây là trang ngoài (khung chứa), không có dữ liệu — hãy tải file \"saved_resource.html\" nằm TRONG thư mục "
                                    "\"<tên trang>_files\" đi kèm trang đã lưu (file lớn nhất trong thư mục đó)",
    "trang_lech_khuon": "trang không đúng khuôn trang theo dõi của đội — không đọc chỉ tiêu, không đoán",
    "trang_khong_ro_ky": "không đọc được ngày cập nhật của trang nên không biết trang thuộc tháng nào — không dùng",
    "trang_khac_ky": "trang thuộc tháng khác với kỳ đang tải — tải đúng trang của kỳ",
    "trang_lech_doi": "danh sách nhân viên trên trang không khớp đội của bản dữ liệu (file chỉ tiêu + file đơn hàng)",
}


def refusal_text(problem: PageProblem) -> str:
    """Lời từ chối do code viết: câu cố định theo mã + mã con + số đếm."""
    detail = ", ".join(f"{key} = {value}" for key, value in sorted(problem.detail.items()))
    return REFUSALS[problem.code] + (f" ({detail})" if detail else "")


def target_files(extracted: dict) -> dict[str, bytes]:
    """Hai file lõi đọc (`compute_extra_bonus.load_item_targets`), dạng byte ổn định (cùng chỉ tiêu ⇒ cùng sha256): Kokomi khối phẳng, Meat nhiều khối."""
    dump = lambda targets: json.dumps({"unit": "đồng", "targets": targets}, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    return {KOKOMI: dump(extracted[KOKOMI]), MEAT: dump(extracted[MEAT])}


def saved_form(extracted: dict) -> bytes:
    """MỘT file chỉ tiêu đã tách của lượt tải trang (byte ổn định) — nguồn để backend dựng lại bản lúc bật lại mà không cần trang (trang không được lưu)."""
    return json.dumps({KOKOMI: extracted[KOKOMI], MEAT: extracted[MEAT]}, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")


def from_saved_form(body: bytes, team: set[str]) -> dict:
    """Đọc lại file `saved_form`. Sai hình / danh sách người khác đội của bản (file chỉ tiêu đã đổi sau lúc tải) ⇒ `PageProblem` — không dùng nửa vời."""
    try:
        data = json.loads(body.decode("utf-8"))
        blocks = [data[KOKOMI], *(data[MEAT][key] for group, key in PAGE_ITEMS.values() if group == MEAT)]
    except (ValueError, KeyError, TypeError):
        raise PageProblem("trang_lech_khuon", ly_do="file_chi_tieu_da_tach_khong_doc_duoc") from None
    if set(data) != {KOKOMI, MEAT} or not all(isinstance(block, dict) and all(isinstance(value, int) and not isinstance(value, bool) and value > 0
                                                                              for value in block.values()) for block in blocks):
        raise PageProblem("trang_lech_khuon", ly_do="file_chi_tieu_da_tach_khong_doc_duoc")
    if any(set(block) != set(team) for block in blocks):
        raise PageProblem("trang_lech_doi", ly_do="danh_sach_nguoi_khac_doi", so_nguoi_doi=len(team))
    return {KOKOMI: data[KOKOMI], MEAT: {key: data[MEAT][key] for group, key in PAGE_ITEMS.values() if group == MEAT}}
