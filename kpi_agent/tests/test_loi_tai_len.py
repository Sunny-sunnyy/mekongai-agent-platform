"""Lời từ chối khi tải file RAW sai (18/11/2026) — người tải phải biết SAI GÌ, mà lời vẫn không lặp chữ trong file.

Lỗi thật: năm lời mới của bộ đọc (bẫy nạp 18/11: ô tiền là chữ, dòng không mã NV có doanh số, hai dòng Total, ô Total là chữ, lệch tổng) và lời "ô ngày
không phải ngày" đều tới người tải thành một câu chung "file raw sai khuôn dữ liệu — kiểm lại theo mẫu" (E đọc code + 18/11; Capitol gặp ca ô ngày
qua màn ghép cột). Chặn vẫn đúng — chỉ thiếu lời. Thêm: thân từ chối có `code` ổn định để màn Capitol khỏi dò chữ (`can_ghep_cot` …), chữ `refused` giữ.

File thử DỰNG GIẢ (ô xấu mang chữ đánh dấu để kiểm lời trả không lặp nó). Phần đọc-và-kiểm gọi thẳng `upload.read_and_check` (đúng hàm đường tải lên
gọi); 3 ca cuối đi qua HTTP thật (TestClient, dữ liệu thật tháng 10 làm bản gốc)."""
import io
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest
import yaml
from fastapi.testclient import TestClient

from kpi_agent.backend import upload
from kpi_agent.core.load_raw import NEEDED as RAW_COLUMNS

ROOT = Path(__file__).resolve().parents[2]
PERIOD, DAY, INVOICED = "2026-10", date(2026, 10, 5), datetime(2026, 10, 6, 9)
# bậc 3a: bộ đọc RAW cần luật kỳ (không có mặc định) — lời gọi truyền luật RAW tháng 10, kỳ vọng không đổi
ASSUMPTIONS = yaml.safe_load((ROOT / "kpi_agent/config/rules_2026_10_raw.yaml").read_text(encoding="utf-8"))["assumptions"]
MARK = "CHU_DANH_DAU_TRONG_FILE"     # chữ nằm trong ô xấu — không được xuất hiện trong lời trả
HEADER = [*RAW_COLUMNS, "Sales After Discount", "Discount value", "SHIP TO"]


def line(order, before=1000, after=900, discount=100, code="SM1", day=DAY):
    values = {"SM CODE": code, "ORDER NUM": order, "ORDER STATUS": "Invoiced", "ORDER DATE": day, "OUTLET CODE": "O1", "SUB DIV": "Beer",
              "DMS OBJ": "Bia White Lion", "SKU CODE": "X", "Sales Before Discount": before, "L1": "Kênh Off Premise", "MOQ_FLAG": "Y",
              "FREE ITEM FLAG": "N", "INVOICED DATE": INVOICED, "Sales After Discount": after, "Discount value": discount, "SHIP TO": "Cửa hàng"}
    return [values[column] for column in HEADER]


def total(before, after, discount):
    values = {"Sales Before Discount": before, "Sales After Discount": after, "Discount value": discount, "SHIP TO": "Total"}
    return [values.get(column) for column in HEADER]


def workbook(rows, header=HEADER) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export"
    sheet.append(list(header))
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def refusal_of(tmp_path, rows) -> upload.UploadRefused:
    path = tmp_path / "raw.xlsx"
    path.write_bytes(workbook(rows))
    with pytest.raises(upload.UploadRefused) as caught:
        upload.read_and_check("raw", path, PERIOD, {"SM1"}, date(2026, 10, 31), assumptions=ASSUMPTIONS)
    return caught.value


GOOD = [line("D1"), line("D2", 500, 500, 0)]
# (tên ca, các dòng, những chữ PHẢI có trong lời, mã)
CASES = [
    ("o_tien_sau_ck_la_chu", [line("D1", after=f"384,00 {MARK}"), GOOD[1]], ["Sales After Discount", "không phải số", "dòng Excel 2"], "o_khong_phai_so"),
    ("o_chiet_khau_la_chu", [GOOD[0], line("D2", 500, 500, MARK)], ["Discount value", "không phải số", "dòng Excel 3"], "o_khong_phai_so"),
    ("o_tien_truoc_ck_la_chu", [line("D1", before=MARK), GOOD[1]], ["Sales Before Discount", "không phải số", "dòng Excel 2"], "o_khong_phai_so"),
    ("dong_khong_ma_nv_co_doanh_so", [GOOD[0], line("D2", 500, 500, 0, code=None)], ["không có mã nhân viên", "dòng Excel 3"], "dong_khong_ma_nv"),
    ("hai_dong_total", [GOOD[0], total(1000, 900, 100), total(1000, 900, 100)], ["dòng 'Total'", "chỉ được một", "dòng Excel 3, 4"], "dong_total"),
    ("total_o_sau_ck_la_chu", [GOOD[0], total(1000, MARK, 100)], ["dòng 'Total'", "Sales After Discount", "dòng Excel 3"], "dong_total"),
    ("total_lech_tong_sau_ck", [*GOOD, total(1500, 1300, 100)], ["cổng đối soát RAW chặn", "1.400", "1.300"], "lech_tong"),
    ("o_ngay_la_chu", [line("D1", day=f"05/10 {MARK}"), GOOD[1]], ["ORDER DATE", "ngày"], "ngay_sai"),
    ("o_ngay_la_so_tran", [line("D1", day=46300), GOOD[1]], ["ORDER DATE", "ngày"], "ngay_sai"),
    ("ngay_ngoai_ky", [line("D1", day=date(2026, 11, 2)), GOOD[1]], ["ngoài kỳ 2026-10"], "ngay_sai"),
]


@pytest.mark.parametrize("rows, must_say, code", [case[1:] for case in CASES], ids=[case[0] for case in CASES])
def test_refusal_says_what_is_wrong_without_file_text(tmp_path, rows, must_say, code):
    refusal = refusal_of(tmp_path, rows)
    assert refusal.status == 422
    for words in must_say:
        assert words in refusal.reason, refusal.reason
    assert "sai khuôn dữ liệu" not in refusal.reason, refusal.reason          # không còn câu chung
    assert MARK not in refusal.reason and "46300" not in refusal.reason       # không lặp giá trị ô
    assert refusal.code == code


def test_good_file_still_passes(tmp_path):
    path = tmp_path / "raw.xlsx"
    path.write_bytes(workbook([*GOOD, total(1500, 1400, 100)]))
    assert upload.read_and_check("raw", path, PERIOD, {"SM1"}, date(2026, 10, 31), assumptions=ASSUMPTIONS) == DAY


def test_unknown_loader_error_stays_generic(tmp_path):
    # Lời lạ của bộ đọc (ở đây: trạng thái đơn lạ — lời gốc in NGUYÊN giá trị ô) ⇒ vẫn câu chung, không lặp chữ file, mã chung
    row = line("D1")
    row[HEADER.index("ORDER STATUS")] = MARK
    refusal = refusal_of(tmp_path, [row, GOOD[1]])
    assert "sai khuôn dữ liệu" in refusal.reason and MARK not in refusal.reason
    assert refusal.code == "sai_khuon"


def test_reconcile_messages_only_the_two_total_ones_come_back_verbatim():
    # Người kiểm 18/11: bản đầu trả nguyên MỌI lời bắt đầu bằng tiền tố cổng đối soát. Lời "bị loại … %" của loader có thể mang mã NV + doanh số riêng
    # ⇒ phải về câu chung; chỉ hai lời lệch tổng (toàn chữ số) được trả nguyên.
    tail = " — file thiếu / thừa dòng hoặc ô tiền đọc sai; kiểm lại file"
    after = "cổng đối soát RAW chặn: tổng doanh số SAU chiết khấu các dòng đọc được 1.400 đ ≠ dòng 'Total' của file 1.300 đ" + tail
    before = "cổng đối soát RAW chặn: tổng doanh số các dòng đọc được 152.360.000 đ ≠ dòng 'Total' của file 152.000.000 đ" + tail
    for text in (after, before):
        assert upload.safe_reason("raw", ValueError(text)) == text and upload.reason_code(ValueError(text)) == "lech_tong"
    for text in (f"cổng đối soát RAW chặn: bị loại 3,2 % doanh số tới ngày chốt của đội; {MARK} SM0123 mất 12.000.000 đ",
                 after + f" {MARK}", before.replace("152.360.000", MARK)):
        reason = upload.safe_reason("raw", ValueError(text))
        assert "sai khuôn dữ liệu" in reason and MARK not in reason and "SM0123" not in reason
        assert upload.reason_code(ValueError(text)) == "sai_khuon"


# ---------- qua HTTP thật: mã ổn định trong thân từ chối, chữ `refused` giữ nguyên ----------
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
NEEDED = [RAW, ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json",
          ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json", ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json"]
real_data = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"   # khoá giả chỉ sống trong test
BIN = {"X-Kpi-Sys-Key": SYS_KEY, "Content-Type": "application/octet-stream"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from kpi_agent.backend import main
    folder = tmp_path_factory.mktemp("loi_tai_len")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        mp.setattr(main, "UPLOAD_DIR", folder / "uploads")     # không ghi file thử vào thư mục tải lên thật
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


def post(client, body: bytes):
    return client.post("/api/system/upload", params={"loai": "raw", "ky": PERIOD, "ngay_chot": "2026-10-31"}, content=body, headers=BIN)


@real_data
def test_http_text_money_cell_refusal_has_reason_and_code(client):
    refused = post(client, workbook([line("D1", after=f"384,00 {MARK}"), GOOD[1]]))
    body = refused.json()
    assert refused.status_code == 422, body
    assert "Sales After Discount" in body["refused"] and "dòng Excel 2" in body["refused"] and MARK not in refused.text
    assert body["code"] == "o_khong_phai_so"


@real_data
def test_http_missing_column_refusal_keeps_text_and_adds_can_ghep_cot(client):
    # Màn ghép cột bên Capitol đang nhận biết "file cần ghép cột" bằng cách dò chữ "ghép cột trước" ⇒ chữ GIỮ, thêm mã ổn định.
    header = [column for column in HEADER if column != "MOQ_FLAG"]
    rows = [[value for column, value in zip(HEADER, row) if column != "MOQ_FLAG"] for row in GOOD]
    refused = post(client, workbook(rows, header))
    body = refused.json()
    assert refused.status_code == 422, body
    assert "MOQ_FLAG" in body["refused"] and "ghép cột trước" in body["refused"]
    assert body["code"] == "can_ghep_cot"


@real_data
def test_http_other_refusals_do_not_carry_can_ghep_cot(client):
    not_excel = post(client, b"not an excel file")
    assert not_excel.status_code == 422 and not_excel.json().get("code") != "can_ghep_cot" and "refused" in not_excel.json()
    money = post(client, workbook([GOOD[0], line("D2", 500, 500, 0, code=None)]))
    assert money.status_code == 422 and money.json()["code"] == "dong_khong_ma_nv"


@real_data
def test_http_rule_value_without_lines_tells_which_value_and_how_to_pass(client):
    # Bậc 3a P2: RAW tháng 10 thật bỏ hết dòng mã Homey 2,9 kg (như một tháng chưa bán mã đó) ⇒ luật kỳ nêu mã mà file không có dòng nào ⇒ dừng.
    # Trước khi sửa đường tải lên: người tải chỉ nhận "bộ chạy dừng", không biết thiếu mã nào / cho qua bằng khoá nào.
    source = openpyxl.load_workbook(ROOT / "sample_data/thang_10/RAW DATA.xlsx", read_only=True, data_only=True)
    rows = source["Export"].iter_rows(values_only=True)
    header = next(rows)
    day, sku = header.index("ORDER DATE"), header.index("SKU CODE")
    target = openpyxl.Workbook(write_only=True)
    sheet = target.create_sheet("Export")
    sheet.append(header)
    for row in rows:
        if len(row) > day and isinstance(row[day], datetime) and row[sku] != "04HP00639":
            sheet.append(row)
    out = io.BytesIO()
    target.save(out)
    source.close()
    refused = post(client, out.getvalue())
    body = refused.json()
    assert refused.status_code == 422, body
    assert "luật kỳ nêu" in body["refused"] and "04HP00639" in body["refused"] and "cho_qua_khong_co_don" in body["refused"], body
    assert body["code"] == "luat_neu_khong_co_dong"
