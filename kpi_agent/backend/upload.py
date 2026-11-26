"""Kiểm + lưu file Excel người dùng tải lên cho bộ Capitol.

Vì sao một file riêng: đây là KÊNH ĐẦU VÀO MỚI từ ngoài (trang "Hỏi KPI" bên Capitol) ⇒ mọi rào nằm một chỗ, kiểm TRƯỚC khi lõi đọc số:
trần kích thước, giải nén có trần (chống bom nén — cách repo RAG), không macro, cột + kỳ (loader sẵn có), NV phải có mã che.
Sai ⇒ `UploadRefused(lý do, mã HTTP)` — lý do KHÔNG chứa nội dung ô / mã / tên thật (chỉ số lượng, số dòng Excel, tên cột).
File lưu theo sha256 (không dùng tên người gửi) trong `kpi_agent/data/uploads/` (Git bỏ qua)."""
import hashlib
import io
import re
import uuid
import zipfile
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path

from kpi_agent.core.load_data import load_targets, load_tracking
from openpyxl import load_workbook

from kpi_agent.core.load_raw import NEEDED as RAW_COLUMNS, period_rules, read_raw_lines

MAX_FILE_BYTES = 20 * 1024 * 1024           # 10/11 (RAW tháng 10 thật 1,7 MB)
MAX_UNZIPPED_BYTES = 200 * 1024 * 1024      # như repo RAG
MAX_PARTS = 2000
MAX_RATIO = 100                             # tổng giải nén ÷ cỡ file
# loại file ⇒ khối đọc trong workflow. `so_tracking` (2a-1, 20/11): sổ theo dõi làm ĐẦU VÀO của đường raw — chỉ thay nguồn bảng tuyến của khối read_raw
# (ô mcp_path / mcp_ghep / mcp_doi), không thay file đơn hàng; khác loại `tracking` (sổ làm nguồn TÍNH của đường cũ).
KINDS = {"raw": "read_raw", "tracking": "read_tracking", "chi_tieu": "read_targets", "so_tracking": "read_raw", "trang_chi_tieu": "read_raw"}
ROUTE_BOOK = "so_tracking"
PAGE_TARGETS = "trang_chi_tieu"             # trang theo dõi .html ⇒ chỉ tiêu riêng Kokomi / Meat (2a-2 nấc 3) — thân là chữ, không phải gói .xlsx
MAX_UPLOAD_FILES = 50                       # trần số file trong uploads/ (C rà bảo mật U9) — đầy ⇒ 409, dọn tay
# Sheet / cột BẮT BUỘC — lời lỗi chỉ nêu tên trong các danh sách này (C rà bảo mật U1: không lặp chữ người tải đặt: cột thừa, tên sheet lạ, giá trị ô)
REQUIRED_SHEETS = {"raw": ("Export",), "chi_tieu": ("Export",), "tracking": ("Rawdata", "MCP")}
TARGET_COLUMNS = ("SM code", "SM name", "SM pos", "Region", "Month", "Distributor", "KPI type", "Target")
# Lời của loader được trả NGUYÊN chỉ khi đúng khuôn đã biết là an toàn (lời do code viết: chỉ tên cột CỐ ĐỊNH, số đếm, số dòng Excel, tổng tiền, ngày
# dd/mm/yyyy do code in); còn lại ⇒ lời chung + số dòng. Mỗi khuôn kèm MÃ ổn định cho bên gọi (màn Capitol) khỏi phải dò chữ.
# 18/11: thêm 5 lời của bẫy nạp (ô tiền là chữ ở 3 cột tiền, dòng không mã NV có doanh số, dòng Total) + dòng bán trống nhóm hàng — trước đó người tải
# chỉ thấy "file raw sai khuôn dữ liệu" (chặn đúng nhưng không biết sửa gì — E đọc code, thật 18/11).
_ROWS = r"[\d, …]+"
_MONEY_COLUMN = r"'(?:Sales Before Discount|Sales After Discount|Discount value)'"
RULE_VALUE_WITHOUT_LINES = "luat_neu_khong_co_dong"
XLSB_NEEDS_XLSX = "xlsb_can_luu_xlsx"     # file .xlsb: người tải tự lưu lại thành .xlsx (hệ thống không đọc Excel nhị phân)
TEAM_MEMBER_MISSING = "doi_thieu_nguoi"   # file chỉ tiêu thiếu người của đội (người có mã che) — xem `missing_team_members`
SAFE_LOADER_MESSAGES = (
    (re.compile(rf"RAW có \d+ dòng ô {_MONEY_COLUMN} trống hoặc không phải số \(dòng Excel {_ROWS}\) — "
                r"(?:ô tiền phải là SỐ, không phải chữ kiểu \"1\.234,00\"; )?sửa file rồi chạy lại"), "o_khong_phai_so"),
    (re.compile(rf"RAW có \d+ dòng không có mã nhân viên \(SM CODE\) mà có doanh số \(dòng Excel {_ROWS}\) — không biết của ai; sửa file rồi chạy lại"),
     "dong_khong_ma_nv"),
    (re.compile(rf"RAW có \d+ dòng 'Total' \(dòng Excel {_ROWS}\) — chỉ được một; sửa file"), "dong_total"),
    (re.compile(r"dòng 'Total' \(dòng Excel \d+\) có ô '(?:Sales Before Discount|Sales After Discount)' trống hoặc không phải số — "
                r"không đối chiếu được tổng; sửa file"), "dong_total"),
    (re.compile(rf"RAW có \d+ dòng bán \(không phải hàng tặng\) trống tên nhóm hàng 'DMS OBJ' \(dòng Excel {_ROWS}\) — "
                r"không biết xếp vào nhóm nào; sửa file rồi chạy lại"), "thieu_nhom_hang"),
    (re.compile(r"RAW có \d+ dòng ngày đơn ngoài kỳ \d{4}-\d{2} hoặc không phải ngày \(vd \d{2}/\d{2}/\d{4}\)"), "ngay_sai"),
    # Bậc 3a P2 (`load_raw.rule_values_without_lines`): lời chỉ có tên KPI + giá trị ghi trong FILE LUẬT (không có chữ nào của file tải lên) + cách cho qua
    (re.compile(r"luật kỳ nêu [^\n]+ nhưng file không có dòng nào mang giá trị đó — [^\n]+kèm ghi_chu"), RULE_VALUE_WITHOUT_LINES),
)
# Ô ngày KHÔNG phải ngày (số trần, chữ): lời gốc của loader in nguyên giá trị ô ở phần "(vd …)" ⇒ không trả nguyên; viết lại chỉ bằng số đếm + kỳ
# + tên cột cố định (Capitol gặp ca này ở màn ghép cột 18/11). Loader chưa đưa số dòng cho lỗi này ⇒ lời chưa có "dòng Excel".
BAD_DATE_MESSAGE = re.compile(r"RAW có (\d+) dòng ngày đơn ngoài kỳ (\d{4}-\d{2}) hoặc không phải ngày \(vd .*\)", re.DOTALL)
# Lời cổng đối soát: CHỈ hai lời "lệch tổng" (chỉ có chữ số tiền) được trả nguyên, khớp cả câu. Lời cùng tiền tố khác của loader ("bị loại … % doanh số …")
# có thể mang mã NV + doanh số riêng ⇒ không trả nguyên ở đường này (18/11: bản đầu nhận theo tiền tố ⇒ sau này lời đó đi qua là lộ + sai mã).
_AMOUNT = r"-?[\d.]+"
RECONCILE_TOTAL_MESSAGES = (
    re.compile(rf"cổng đối soát RAW chặn: tổng doanh số SAU chiết khấu các dòng đọc được {_AMOUNT} đ ≠ dòng 'Total' của file {_AMOUNT} đ — "
               r"file thiếu / thừa dòng hoặc ô tiền đọc sai; kiểm lại file"),
    re.compile(rf"cổng đối soát RAW chặn: tổng doanh số các dòng đọc được {_AMOUNT} đ ≠ dòng 'Total' của file {_AMOUNT} đ — "
               r"file thiếu / thừa dòng hoặc ô tiền đọc sai; kiểm lại file"),
)

# Lời `load_raw._read_rows` khi gặp giá trị chưa biết: "RAW có giá trị chưa biết — <trường>: N giá trị / M dòng[; …]. Ghép giá trị …"
UNKNOWN_VALUES_MESSAGE = re.compile(r"RAW có giá trị chưa biết — (?:(?:channel|moq_flag|is_free_item|sub_div): \d+ giá trị / \d+ dòng(?:; |(?=\.)))+"
                                    r"\. Ghép giá trị \(kênh, cờ\) hoặc cho qua ngành mới ở màn ghép cột; không tính tiếp")


class UploadRefused(Exception):
    """`code`: mã ổn định của loại từ chối (None = chưa đặt mã) — bên gọi rẽ nhánh theo mã, chữ `reason` chỉ để người đọc."""

    def __init__(self, reason: str, status: int = 422, code: str | None = None):
        super().__init__(reason)
        self.reason, self.status, self.code = reason, status, code


def check_package(body: bytes) -> None:
    """Gói .xlsx hợp lệ, không macro, giải nén có trần — chỉ đọc MỤC LỤC gói (không giải nén) trừ [Content_Types].xml (nhỏ)."""
    if len(body) > MAX_FILE_BYTES:
        raise UploadRefused(f"file lớn hơn {MAX_FILE_BYTES // (1024 * 1024)} MB", 413)
    if not body.startswith(b"PK\x03\x04") or not zipfile.is_zipfile(io.BytesIO(body)):
        raise UploadRefused("không phải file .xlsx (không phải gói zip của Excel)")
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            parts = archive.infolist()
            if len(parts) > MAX_PARTS:
                raise UploadRefused(f"gói có hơn {MAX_PARTS} phần", 413)
            unzipped = sum(part.file_size for part in parts)
            if unzipped > MAX_UNZIPPED_BYTES or unzipped > MAX_RATIO * max(len(body), 1):
                raise UploadRefused(f"giải nén vượt trần ({MAX_UNZIPPED_BYTES // (1024 * 1024)} MB hoặc gấp {MAX_RATIO} lần cỡ file) — không đọc", 413)
            names = {part.filename for part in parts}
            if "xl/workbook.bin" in names:
                # Excel nhị phân (.xlsb) — sổ gốc đội hay gửi. Nhận biết bằng nội dung gói (đổi đuôi tên không qua được); nói CÁCH SỬA, không nói
                # tên phần trong gói (trước 20/11: "gói thiếu xl/workbook.xml" — người tải không biết phải làm gì)
                raise UploadRefused("File này là .xlsb (Excel nhị phân) — hãy mở bằng Excel, chọn Lưu thành (Save As) dạng .xlsx rồi tải lại",
                                    code=XLSB_NEEDS_XLSX)
            if "xl/workbook.xml" not in names or "[Content_Types].xml" not in names:
                raise UploadRefused("gói thiếu xl/workbook.xml — không phải bảng tính .xlsx")
            types = archive.read("[Content_Types].xml").decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        raise UploadRefused("gói zip hỏng") from None
    if any("vbaproject" in name.lower() for name in names) or "macroEnabled" in types or "vbaProject" in types:
        raise UploadRefused("file có macro (vbaProject / macroEnabled) — chỉ nhận .xlsx không macro")


def check_room(sha: str, folder: Path) -> None:
    """Trần số file ĐÃ NHẬN (chỉ file <sha>.xlsx ở cấp trên cùng — file đang kiểm nằm thư mục con, không tính). File đã có ⇒ luôn qua (dùng lại)."""
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / f"{sha}.xlsx").exists() and len(list(folder.glob("*.xlsx"))) >= MAX_UPLOAD_FILES:
        raise UploadRefused(f"thư mục file tải lên đã đủ {MAX_UPLOAD_FILES} file — cần dọn trước khi tải thêm", 409)


def write_temp(body: bytes, folder: Path) -> Path:
    """File đang kiểm: `uploads/.dang_kiem/<ngẫu nhiên>.xlsx` (M1: file bị từ chối từng nằm lại trong uploads/ ⇒ giữ dữ liệu thật + lấp trần).
    Người gọi XOÁ trong finally; chỉ `finalize` khi file QUA mọi kiểm + tính được.
    """
    temp_dir = folder / ".dang_kiem"
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / f"{uuid.uuid4().hex}.xlsx"
    path.write_bytes(body)
    return path


def finalize(temp: Path, folder: Path, sha: str) -> Path:
    """Đổi tên file đã qua kiểm thành <sha>.xlsx (đã có ⇒ giữ file cũ, file tạm bị xoá ở finally của người gọi)."""
    path = folder / f"{sha}.xlsx"
    if not path.exists():
        temp.replace(path)
    return path


def last_day(period: str) -> date:
    year, month = map(int, period.split("-"))
    return date(year, month, monthrange(year, month)[1])


def read_and_check(kind: str, path: Path, period: str, known_codes: set[str], base_cutoff: date, wanted_cutoff: date | None = None,
                   mapping: dict | None = None, assumptions: dict | None = None, base_known_codes: set[str] | None = None,
                   new_employees_out: dict[str, str] | None = None) -> date:
    """Đọc bằng ĐÚNG loader của lõi (cột, kỳ, ô sai kiểu ⇒ ValueError ⇒ 422) + mọi mã NV phải có mã che. Trả ngày chốt của bản:
    `wanted_cutoff` (hệ thống điền — "ngày chốt là tham số") nếu có; không có: RAW = ngày đơn mới nhất được tính (không lấy giờ máy);
    Tracking = ngày chụp sổ (không có ⇒ ngày chốt bản gốc); chỉ tiêu = ngày chốt bản gốc.
    `assumptions` (bậc 3a, bắt buộc với RAW): khối assumptions của luật kỳ — bộ đọc RAW lấy trạng thái được tính + hạn hoá đơn từ đó.
    K1 (26/11): với `chi_tieu`, nếu truyền `new_employees_out` thì kiểm rào `file_sai_doi` + `missing_team_members` theo `base_known_codes`
    và thu các mã mới `{mã_thật: họ_tên}` vào `new_employees_out` thay vì từ chối; `raw` / `tracking` không bao giờ tự cấp mã.
    """
    if wanted_cutoff is not None and f"{wanted_cutoff:%Y-%m}" != period:
        raise UploadRefused(f"ngày chốt {wanted_cutoff:%d/%m/%Y} ngoài kỳ {period}")
    if not (kind == "raw" and mapping is not None):   # file RAW có bảng ghép người đã xác nhận (bậc 2): cột / sheet do bảng ghép nói, không theo mẫu T10
        _check_layout(kind, path)
    if kind == "raw":
        # SAU kiểm khuôn file: file hỏng vẫn nhận lời "file hỏng" như trước; luật kỳ thiếu khoá ⇒ 409 nêu tên khoá (lời do code viết)
        try:
            rules = period_rules(assumptions)
        except ValueError as error:
            raise UploadRefused(f"luật kỳ {period} không dùng được: {error}", 409) from None
    employees = {}
    try:
        if kind == "raw":
            # Ngày chốt / mã NV lấy từ MỌI dòng trạng thái được tính (kể cả Shipped chưa xuất hoá đơn — bậc 1 17/11): đọc như trước thì ngày chốt tự lùi
            # về ngày cuối CÓ hoá đơn (RAW tháng 11: 05/11 ⇒ 03/11) và cổng đối soát sau đó không thấy dòng bị loại. Luật hoá đơn của kỳ áp ở bộ chạy.
            lines, _late = read_raw_lines(path, wanted_cutoff or last_day(period), period, invoice_required_status=(), mapping=mapping,
                                          period_rules=rules)
            codes = {str(line["SM CODE"]) for line in lines}
            days = [line["ORDER DATE"] for line in lines]
            cutoff = max((d.date() if isinstance(d, datetime) else d for d in days), default=None)
            if cutoff is None:
                raise UploadRefused(f"RAW không có dòng đơn nào được tính trong kỳ {period}")
        elif kind == "tracking":
            data = load_tracking(path)
            codes = {str(row["SM CODE"]) for block in data["blocks"].values() for row in block["rows"] if row.get("SM CODE")}
            cutoff = data["refreshed_at"].date() if data.get("refreshed_at") else base_cutoff
        else:
            employees = load_targets(path)["employees"]
            # A rà ghi cứng 17/11: cột Month được đọc mà không so ⇒ file chỉ tiêu tháng khác lọt lặng lẽ. Khuôn file thật: "MM-YYYY" (T10: "10-2026").
            # Không khớp / khuôn khác ⇒ dừng, chỉ nêu SỐ dòng (không lặp ô — U1), không đoán khuôn tháng.
            wrong = sum(info["month"] != f"{period[5:]}-{period[:4]}" for info in employees.values())
            if wrong:
                raise UploadRefused(f"file chỉ tiêu: {wrong} nhân viên có cột Month không phải kỳ {period} (khuôn MM-YYYY, vd {period[5:]}-{period[:4]})")
            codes = {str(code) for code in employees}
            base = base_known_codes if base_known_codes is not None else known_codes
            unknown_in_targets = codes - known_codes
            if new_employees_out is not None and unknown_in_targets and (len(codes & base) == 0 or len(unknown_in_targets) > max(5, len(base) // 2)):
                raise UploadRefused(
                    f"file chỉ tiêu không khớp đội hiện tại ({len(unknown_in_targets)} mã lạ, trùng {len(codes & base)}/{len(base)} người của đội)"
                    " — kiểm lại đúng file chỉ tiêu của đội",
                    422,
                    "file_sai_doi",
                )
            cutoff = base_cutoff
            # Chiều NGƯỢC của kiểm mã che ở cuối hàm (20/11/2026): backend coi "đội" là MỌI người có mã che, còn số chỉ được tính cho người có
            # trong file chỉ tiêu ⇒ file bớt một người từng qua mọi kiểm rồi sập HTTP 500 lúc lập bảng (KeyError). Tới khi "đội của một bản = người
            # trong file chỉ tiêu của bản đó" được làm ở lõi: dừng có lời + mã, chỉ nêu SỐ người (không lặp mã / tên — U1).
            missing = missing_team_members(codes, base)
            if missing:
                raise UploadRefused(missing, code=TEAM_MEMBER_MISSING)
    except UploadRefused:
        raise
    except Exception as error:   # noqa: BLE001 — mọi lỗi đọc (gói hỏng, CRC, XML, ô sai kiểu) ⇒ 422, không 500 (C rà bảo mật U3)
        raise UploadRefused(safe_reason(kind, error), code=reason_code(error)) from None
    unknown = codes - known_codes
    if unknown:
        if kind == "chi_tieu" and new_employees_out is not None:
            for code in sorted(unknown):
                new_employees_out[code] = str(employees[code].get("name") or "").strip()
        else:
            raise UploadRefused(f"{len(unknown)} mã NV mới chưa có mã che — thêm vào bảng mã che trước (không tự đặt mã)")
    return wanted_cutoff or cutoff


def missing_team_members(target_codes: set[str], known_codes: set[str]) -> str | None:
    """Lời dừng khi file chỉ tiêu không có đủ mọi người của đội (`known_codes` = người có mã che); đủ ⇒ None. Dùng chung cho cửa tải lên và lúc bật."""
    count = len({str(code) for code in known_codes} - {str(code) for code in target_codes})
    if not count:
        return None
    return (f"file chỉ tiêu thiếu {count} người của đội (đội đang có {len(known_codes)} người có mã che) — bổ sung chỉ tiêu cho đủ đội rồi tải lại; "
            "đội thật sự bớt người thì hệ thống CHƯA nhận file như vậy (báo người phát triển)")


def _check_layout(kind: str, path: Path) -> None:
    """Sheet + cột bắt buộc, kiểm TRƯỚC loader để lời lỗi chỉ có tên của mình. Gói hỏng ⇒ 422 "file hỏng"."""
    try:
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            missing_sheets = [name for name in REQUIRED_SHEETS[kind] if name not in book.sheetnames]
            if missing_sheets:
                raise UploadRefused(f"file {kind} thiếu sheet bắt buộc: {', '.join(missing_sheets)}")
            if kind in ("raw", "chi_tieu"):
                header = next(book["Export"].iter_rows(max_row=1, values_only=True), ()) or ()
                required = RAW_COLUMNS if kind == "raw" else TARGET_COLUMNS
                missing = [name for name in required if name not in header]
                if missing:
                    raise UploadRefused(f"file {kind} thiếu cột bắt buộc: {', '.join(missing)}")
        finally:
            book.close()
    except UploadRefused:
        raise
    except Exception:   # noqa: BLE001 — gói zip / XML hỏng, CRC sai (C U3)
        raise UploadRefused(f"file {kind} hỏng — không đọc được gói .xlsx") from None


def _known_loader_message(text: str) -> tuple[str, str] | None:
    """(lời an toàn, mã) nếu lời của loader thuộc một khuôn đã biết; không thì None."""
    for pattern, code in SAFE_LOADER_MESSAGES:
        if pattern.fullmatch(text):
            return text, code
    if any(pattern.fullmatch(text) for pattern in RECONCILE_TOTAL_MESSAGES):
        return text, "lech_tong"
    if UNKNOWN_VALUES_MESSAGE.fullmatch(text):
        # m5: lời do code viết — chỉ TÊN TRƯỜNG cố định + số giá trị / dòng (không giá trị ô — U1) ⇒ trả nguyên + chỉ đường ghép
        return (text.split(". Ghép giá trị", 1)[0] + " — vào ghép thử (POST /api/system/bo-cuc ⇒ ghep-thu) để xem mã giá trị mới và cho qua / ghép giá trị",
                "gia_tri_chua_biet")
    bad_date = BAD_DATE_MESSAGE.fullmatch(text)
    if bad_date:
        count, period = bad_date.groups()
        return (f"RAW có {count} dòng ô 'ORDER DATE' không phải ngày hoặc ngoài kỳ {period} — cột ORDER DATE phải là ô kiểu ngày "
                f"(không phải số trần, không phải chữ) và nằm trong kỳ {period}; sửa file rồi chạy lại", "ngay_sai")
    return None


def safe_reason(kind: str, error: Exception) -> str:
    """Lời trả cho bên gọi: lời loader nguyên văn CHỈ khi khớp khuôn an toàn; không thì lời chung + số dòng Excel (chỉ chữ số) — không lặp chữ trong file."""
    text = str(error)
    known = _known_loader_message(text)
    if known:
        return known[0]
    rows = re.findall(r"dòng Excel ([\d, …]+)", text)
    return f"file {kind} sai khuôn dữ liệu — kiểm lại theo mẫu" + (f" (dòng Excel {rows[0].strip(', ')})" if rows else "")


def reason_code(error: Exception) -> str:
    """Mã ổn định đi kèm `safe_reason` — "sai_khuon" khi lời của loader không thuộc khuôn nào đã biết."""
    known = _known_loader_message(str(error))
    return known[1] if known else "sai_khuon"
