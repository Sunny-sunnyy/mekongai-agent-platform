"""`kiem_so` / `kiem_excel` — Rào chắn kiểm bằng code. Không mock: kết quả công cụ = JSON THẬT backend trả (TestClient,
dữ liệu thật tháng 10, store tạm), Excel = file THẬT `/api/export/xlsx` dựng. Số mong đợi lấy từ chính kết quả công cụ, không gõ tay.

"Xong" (luật 09/11): 1 ca thật đúng (câu trả lời + Excel thật ⇒ đạt) + 2 ca xấu bị chặn (số bịa trong câu trả lời · AI tự gõ số vào Excel)."""
import base64
import io
import os
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from decimal import Decimal

from kpi_agent.agent import number_check as nc
from kpi_agent.agent.kiem_so import VERSION, kiem_excel, kiem_so

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")


def _vn(amount: int) -> str:
    return f"{amount:,}".replace(",", ".")


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """Kết quả công cụ cấp đội THẬT (bảng thưởng + tổng từng KPI — cách (b): máy chủ tự gọi) + file Excel thật + vài kết quả một người."""
    # Kho tạm RIÊNG rồi TRẢ LẠI giá trị cũ khi hết file (như conftest.py): fixture này bấm "Đã soát" ở dòng review dưới ⇒ trước 10/11 kho đã soát
    # rò sang test_backend chạy sau cùng tiến trình (thấy da_soat thay tam_tinh — khi chạy test_kiem_so trước test_backend)
    old = os.environ.get("KPI_STORE")
    os.environ["KPI_STORE"] = str(tmp_path_factory.mktemp("store") / "kpi_store.sqlite")
    from kpi_agent.backend import main
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        meta = client.get("/api/meta").json()
        team = client.get("/api/team-bonus").json()
        team_results = [team] + [client.get("/api/team-kpi", params={"kpi": kpi}).json() for kpi in meta["kpis"]]
        calls = {code: client.get(f"/api/employees/{code}").json() for code in ("NV05", "NV04", "NV02")}
        issues = client.get("/api/data-issues").json()   # cờ theo người — nguồn cho luật "cờ gán nhầm người"
        # Từ khối xuất (develop ed773c7, bỏ tải nháp): chưa soát ⇒ 409 không có file ⇒ soát bản đang dùng trước khi lấy file mẫu
        assert client.post(f"/api/runs/{meta['current_run_id']}/review", json={"reviewer": "test kiem_so"}).status_code == 200
        export = client.get("/api/export/xlsx")
        assert export.status_code == 200, export.text[:200]
        xlsx = export.content
    salary = {row["employee_id"]: row["business_salary"] for row in team["employees"]}
    yield {"team": team, "team_results": team_results, "employee": calls, "xlsx": xlsx, "salary": salary, "issues": issues}
    if old is None:
        os.environ.pop("KPI_STORE", None)
    else:
        os.environ["KPI_STORE"] = old


def _other_salary(salary: dict, of: str) -> tuple[str, int]:
    # Số của MỘT người khác, không trùng số của `of` — để ca "gán nhầm người"
    return next((code, amount) for code, amount in sorted(salary.items()) if code != of and amount != salary[of] and amount > 0)


# ---------- câu trả lời ----------
def test_real_answer_passes(real):
    nv05 = real["salary"]["NV05"]
    result = kiem_so({"answer": f"NV05 được lương kinh doanh {_vn(nv05)} đ.", "question": "NV05 được bao nhiêu?",
                      "tool_results": real["team_results"]})
    assert result == {"dat": True, "loi": [], "ly_do": result["ly_do"], "phien_ban": VERSION}


def test_invented_number_is_blocked(real):
    # Ca xấu (i): số bịa — không có ở đâu trong kết quả công cụ
    invented = 9_999_999
    assert invented not in real["salary"].values()
    result = kiem_so({"answer": f"NV05 được {_vn(invented)} đ.", "tool_results": real["team_results"]})
    assert result["dat"] is False and [p["loai"] for p in result["loi"]] == ["so_khong_nguon"] and _vn(invented) in result["loi"][0]["chi_tiet"]


def test_other_persons_number_is_blocked(real):
    # Ca xấu (i'): số CÓ trong đội nhưng của người khác (cách (b) lỏng — owner_check vẫn bắt khi câu có mã NV)
    code, amount = _other_salary(real["salary"], "NV05")
    result = kiem_so({"answer": f"NV05 được {_vn(amount)} đ.", "tool_results": real["team_results"]})
    assert result["dat"] is False and {p["loai"] for p in result["loi"]} == {"so_sai_nguoi"}, (code, result)


def test_rerun_calls_ignore_claimed_results(real):
    # Dạng (a): có tool_calls ⇒ nguồn = kết quả CHẠY LẠI; tool_results giả (có số bịa) bị bỏ ⇒ số bịa vẫn bị bắt
    fake = [{"employee_id": "NV05", "business_salary": 9_999_999}]
    rerun = lambda name, args: real["employee"][args["employee_id"]]
    result = kiem_so({"answer": "NV05 được 9.999.999 đ.", "tool_results": fake,
                      "tool_calls": [{"name": "get_employee_result", "args": {"employee_id": "NV05"}}]}, run_tool=rerun)
    assert result["dat"] is False and result["loi"][0]["loai"] == "so_khong_nguon"
    honest = kiem_so({"answer": f"NV05 được {_vn(real['salary']['NV05'])} đ.",
                      "tool_calls": [{"name": "get_employee_result", "args": {"employee_id": "NV05"}}]}, run_tool=rerun)
    assert honest["dat"] is True, honest


@pytest.mark.parametrize("payload, run_tool, kind", [
    (None, None, "dau_vao_loi"),
    ({"question": "x"}, None, "dau_vao_loi"),
    ({"answer": "x", "tool_results": "không phải danh sách"}, None, "dau_vao_loi"),
    ({"answer": "x", "tool_calls": [{"args": {}}]}, None, "dau_vao_loi"),
    ({"answer": "x", "tool_calls": [{"name": "get_team_bonus_table"}]}, None, "khong_chay_lai_duoc"),
    ({"answer": "x", "tool_calls": [{"name": "get_team_bonus_table"}]}, lambda name, args: 1 / 0, "khong_chay_lai_duoc"),
])
def test_bad_input_fails_closed(payload, run_tool, kind):
    # Đóng khi lỗi: vào sai hình / không chạy lại được ⇒ TRƯỢT, không cho qua
    result = kiem_so(payload, run_tool=run_tool)
    assert result["dat"] is False and result["loi"][0]["loai"] == kind


def test_answer_without_numbers_and_without_tools_passes():
    # Câu trả lời không có số (vd từ chối) ⇒ không có gì để kiểm ⇒ đạt
    assert kiem_so({"answer": "Mình chỉ trả lời về KPI của đội bạn.", "tool_results": []})["dat"] is True


# ---------- Excel ----------
def _b64(book: openpyxl.Workbook) -> str:
    buffer = io.BytesIO()
    book.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode()


def _detail_sheet(real) -> tuple[openpyxl.Workbook, object, int, int]:
    """Workbook thật + sheet tổng đội + (hàng, cột) ô tiền lương kinh doanh của NV05."""
    book = openpyxl.load_workbook(io.BytesIO(real["xlsx"]))
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            if row[0].value == "NV05" or any(cell.value == "NV05" for cell in row):
                for cell in row:
                    if cell.value == real["salary"]["NV05"]:
                        return book, sheet, cell.row, cell.column
    raise AssertionError("không thấy ô lương NV05 trong file thật")


def test_real_excel_passes(real):
    result = kiem_excel(base64.b64encode(real["xlsx"]).decode(), real["team_results"])
    assert result["dat"] is True, result["loi"][:5]


def test_excel_typed_number_is_blocked(real):
    # Ca xấu (ii): AI tự gõ số vào Excel — sửa MỘT ô tiền (+1.000 đ) ⇒ chỉ đúng ô đó
    book, sheet, row, column = _detail_sheet(real)
    sheet.cell(row, column).value = real["salary"]["NV05"] + 1000
    result = kiem_excel(_b64(book), real["team_results"])
    where = f"{sheet.title}!{sheet.cell(row, column).coordinate}"
    assert result["dat"] is False and [p["loai"] for p in result["loi"]] == ["excel_so_khong_nguon"] and where in result["loi"][0]["chi_tiet"]


def test_excel_other_persons_number_in_row_is_blocked(real):
    book, sheet, row, column = _detail_sheet(real)
    _, amount = _other_salary(real["salary"], "NV05")
    sheet.cell(row, column).value = amount
    result = kiem_excel(_b64(book), real["team_results"])
    assert result["dat"] is False and {p["loai"] for p in result["loi"]} == {"excel_so_sai_nguoi"}


def test_excel_formula_is_blocked(real):
    book, sheet, row, column = _detail_sheet(real)
    sheet.cell(row, column).value = "=SUM(B2:B3)"
    result = kiem_excel(_b64(book), real["team_results"])
    assert result["dat"] is False and result["loi"][0]["loai"] == "excel_cong_thuc"


@pytest.mark.parametrize("raw", ["không phải base64!!", base64.b64encode(b"PK\x03\x04 hong").decode(), base64.b64encode(b"chu thuong").decode()])
def test_unreadable_excel_fails_closed(raw):
    result = kiem_excel(raw, [])
    assert result["dat"] is False and result["loi"][0]["loai"] == "dau_vao_loi"


def test_answer_with_excel_checks_both(real):
    book, sheet, row, column = _detail_sheet(real)
    sheet.cell(row, column).value = real["salary"]["NV05"] + 1000
    result = kiem_so({"answer": f"NV05 được {_vn(real['salary']['NV05'])} đ.", "tool_results": real["team_results"], "excel_b64": _b64(book)})
    assert result["dat"] is False and [p["loai"] for p in result["loi"]] == ["excel_so_khong_nguon"]


def test_money_typed_as_text_in_excel_is_blocked(real):
    # tiền gõ thành CHỮ trong ô ("9.999.999 đ") vẫn phải có nguồn
    book, sheet, row, column = _detail_sheet(real)
    sheet.cell(row, column).value = "9.999.999 đ"
    result = kiem_excel(_b64(book), real["team_results"])
    assert result["dat"] is False and [p["loai"] for p in result["loi"]] == ["excel_so_khong_nguon"] and "9.999.999" in result["loi"][0]["chi_tiet"]


def test_hidden_sheet_is_read(real):
    # sheet ẩn vẫn bị đọc — số bịa giấu trong sheet ẩn không lọt
    book = openpyxl.load_workbook(io.BytesIO(real["xlsx"]))
    hidden = book.create_sheet("an")
    hidden.sheet_state = "hidden"
    hidden["A1"] = 9_999_999
    result = kiem_excel(_b64(book), real["team_results"])
    assert result["dat"] is False and result["loi"][0]["chi_tiet"].startswith("an!A1")


def _mcp_flag(real) -> tuple[set, str]:
    # Cờ "không thuộc MCP" của MỘT người (dữ liệu thật tháng 10) + một NV khác không sở hữu cờ đó
    issue = next(i for i in real["issues"]["issues"] if i["employee_ids"] and "không thuộc MCP" in i["message"])
    other = next(code for code in sorted(real["salary"]) if code not in issue["employee_ids"])
    return set(issue["employee_ids"]), other


def test_flag_of_other_person_is_blocked(real):
    # Đặc tả bước 4 + (lỗi K3 09/11): câu về NV khác mà nêu cờ "ngoài MCP" của người sở hữu cờ ⇒ trượt
    owners, other = _mcp_flag(real)
    result = kiem_so({"question": f"Dữ liệu của {other} có vấn đề gì không?", "answer": f"{other}: có 1 dòng cửa hàng ngoài MCP.",
                      "tool_results": [*real["team_results"], real["issues"]]})
    assert result["dat"] is False and [p["loai"] for p in result["loi"]] == ["co_sai_nguoi"], result


def test_flag_of_the_asked_person_passes(real):
    owners, _ = _mcp_flag(real)
    owner = sorted(owners)[0]
    result = kiem_so({"question": f"Dữ liệu của {owner} có vấn đề gì không?", "answer": f"{owner}: có 1 dòng cửa hàng ngoài MCP.",
                      "tool_results": [*real["team_results"], real["issues"]]})
    assert result["dat"] is True, result



# bản 2: 3 phát hiện (KIEM_KIEM_SO_C.md, 10/11) ----------
def _vn_dec(value: float) -> str:
    return f"{value}".replace(".", ",")


def _ratio_row(real, code: str, kpi: str) -> dict:
    return next(row for row in real["employee"][code]["kpis"] if row["kpi"] == kpi)


def test_ratio_actual_must_be_exact_even_if_it_equals_a_tier(real):
    # C K2 (major, "tỷ lệ đúng y"): dòng bảng "LPPC_Meat · chỉ tiêu · thực hiện · % · tiền" — thực hiện 3,82 viết thành 3,8 (= chỉ tiêu / mốc) ⇒ trượt
    row = _ratio_row(real, "NV04", "LPPC_Meat")
    assert row["actual"] != row["target"]
    line = f"LPPC_Meat · {_vn_dec(row['target'])} · {{actual}} · {_vn_dec(row['pct'])} % · {_vn(row['amount'])} đ"
    sources = [real["employee"]["NV04"]]
    good = kiem_so({"answer": "NV04 — kỳ 2026-10\n" + line.format(actual=_vn_dec(row["actual"])), "tool_results": sources}, context=["2026-10"])
    bad = kiem_so({"answer": "NV04 — kỳ 2026-10\n" + line.format(actual=_vn_dec(row["target"])), "tool_results": sources}, context=["2026-10"])
    assert good["dat"] is True, good
    assert bad["dat"] is False and [p["loai"] for p in bad["loi"]] == ["so_khong_nguon"] and "tỷ lệ" in bad["loi"][0]["chi_tiet"]


def test_ratio_tier_sentence_is_not_flagged(real):
    # Câu nói MỐC (không phải số thực hiện) ⇒ không đòi actual
    row = _ratio_row(real, "NV04", "LPPC_Meat")
    result = kiem_so({"answer": f"NV04: LPPC_Meat mốc thấp nhất là {_vn_dec(row['target'])}.", "tool_results": [real["employee"]["NV04"]]})
    assert result["dat"] is True, result


def test_correct_flag_of_asked_person_with_count_passes(real):
    # C K3c (major — trên Capitol trượt = dừng lượt): câu ĐÚNG về cờ của chính người được hỏi, có số đếm trong chữ cờ ("1 dòng") ⇒ phải QUA
    owners, _ = _mcp_flag(real)
    owner = sorted(owners)[0]
    issue = next(i for i in real["issues"]["issues"] if owner in i["employee_ids"] and "không thuộc MCP" in i["message"])
    count = next(word for word in issue["message"].split() if word.isdigit())
    result = kiem_so({"question": f"Dữ liệu {owner} có vấn đề gì?", "answer": f"{owner} có {count} dòng ở cửa hàng ngoài MCP.",
                      "tool_results": [real["issues"]]})
    assert result["dat"] is True, result


@pytest.mark.parametrize("template", [
    "Đúng, NV02 được {x} đồng.",                    # C K4b
    "NV02 được {x} đồng, đúng như anh nói.",        # C R3c (bản 2 lọt: lời đồng ý ở cuối câu)
    "Không sai, NV02 được {x} đồng.",               # C R3d (bản 2 lọt: "Không" mở đầu bị hiểu là bác bỏ)
    "NV02 được {x} đồng.",                          # không có chữ đồng ý nào — vẫn phải trượt
])
def test_invented_number_from_question_next_to_employee_is_blocked(real, template):
    # Bản 3: số đứng sau mã NV phải thuộc NV đó KỂ CẢ khi có trong câu hỏi — không đoán chữ mở đầu
    invented = 4_123_456
    assert invented not in real["salary"].values()
    result = kiem_so({"question": f"NV02 được {_vn(invented)} đồng phải không?", "answer": template.format(x=_vn(invented)),
                      "tool_results": [real["employee"]["NV02"]]})
    # số bịa không có ở đâu trong kết quả công cụ ⇒ "so_khong_nguon" (câu hỏi không còn là nguồn khi trước số có mã NV)
    assert result["dat"] is False and {p["loai"] for p in result["loi"]} == {"so_khong_nguon"}, result


def test_refutation_repeating_wrong_number_is_blocked_known_cost(real):
    # Cái giá A chấp nhận (bản 3): câu bác bỏ NHẮC LẠI số sai sau mã NV cũng bị chặn — ghi để không ai tưởng là lỗi mới
    real_amount, invented = real["salary"]["NV02"], 4_123_456
    result = kiem_so({"question": f"NV02 được {_vn(invented)} đồng phải không?",
                      "answer": f"Không, NV02 được {_vn(real_amount)} đồng, không phải {_vn(invented)} đồng.", "tool_results": [real["employee"]["NV02"]]})
    assert result["dat"] is False
    plain = kiem_so({"question": f"NV02 được {_vn(invented)} đồng phải không?", "answer": f"Không, NV02 được {_vn(real_amount)} đồng.",
                     "tool_results": [real["employee"]["NV02"]]})
    assert plain["dat"] is True, plain


def test_shared_flag_numbers_belong_to_each_persons_segment(real):
    # C R6b (bản 2 lọt): cờ chung "… NV06 PC_BT: chỉ tiêu 271 ≠ … 274; NV09 PC_BT: chỉ tiêu 287 ≠ … 289" — số của NV09 nói cho NV06 ⇒ trượt
    issue = next(i for i in real["issues"]["issues"] if len(i["employee_ids"]) > 1 and "lệch" in i["message"])
    first, second = issue["employee_ids"][:2]
    def numbers_of(code):
        part = issue["message"].split(code, 1)[1].split(";")[0]
        return [token for token in part.replace(":", " ").split() if token.isdigit()][:2]
    own, other = numbers_of(first), numbers_of(second)
    assert own != other
    ok = kiem_so({"question": f"Dữ liệu {first} có gì lệch?", "answer": f"{first} PC_BT: chỉ tiêu {own[0]} ≠ tính từ MCP {own[1]}.",
                  "tool_results": [real["issues"]]})
    bad = kiem_so({"question": f"Dữ liệu {first} có gì lệch?", "answer": f"{first} PC_BT: chỉ tiêu {other[0]} ≠ tính từ MCP {other[1]}.",
                   "tool_results": [real["issues"]]})
    assert ok["dat"] is True, ok
    assert bad["dat"] is False and {p["loai"] for p in bad["loi"]} == {"so_sai_nguoi"}, bad



def test_plain_number_from_question_next_to_employee_passes(real):
    # Bản 4 (bản 3 — hồi quy câu 9 lượt T5): số THƯỜNG của câu hỏi (tên sản phẩm "Trà 365") sau mã NV vẫn qua; tiền bịa cùng chỗ thì trượt
    question = "NV04 cần thêm bao nhiêu cửa hàng Trà 365 để đạt mốc?"
    sources = [real["employee"]["NV04"]]
    plain = kiem_so({"question": question, "answer": "NV04: sản phẩm Trà 365 — xem số cửa hàng còn thiếu ở bảng.", "tool_results": sources})
    money = kiem_so({"question": "NV04 được 4.123.456 đồng phải không?", "answer": "NV04 được 4.123.456 đồng.", "tool_results": sources})
    assert plain["dat"] is True, plain
    assert money["dat"] is False, money


# 10/11 (B, ý E): lời từ chối / lỗi KHÔNG là nguồn số — /api/system/kiem-so bắt major (cài số bịa qua tham số) ----------
def test_refusal_echoing_bogus_number_is_not_a_source():
    # Đúng hình lời từ chối của backend (`queries._refused` nhắc lại tham số) — AI điền kpi "5.041.000" rồi viết chính số đó
    refused = {"refused": "KPI '5.041.000' không có — chỉ có Turnover, PC_BT", "params_used": {"employee_id": "NV02", "kpi": "5.041.000"}}
    for payload in ({"answer": "NV02 được 5.041.000 đồng.", "tool_results": [refused]},
                    {"answer": "NV02 được 5.041.000 đồng.", "tool_calls": [{"name": "explain_kpi", "args": {"kpi": "5.041.000"}}]}):
        result = kiem_so(payload, run_tool=lambda name, args: refused)
        assert result["dat"] is False and "so_khong_nguon" in {item["loai"] for item in result["loi"]}, payload


def test_every_call_refused_means_empty_sources():
    # mọi lần gọi đều bị từ chối / lỗi ⇒ nguồn rỗng ⇒ câu có số KHÔNG được đạt
    answers = [{"refused": "mã nhân viên không hợp lệ"}, {"error": "backend không trả lời được (ConnectError)"}]
    result = kiem_so({"answer": "NV05 được 5.040.000 đồng.", "tool_results": answers})
    assert result["dat"] is False and {item["loai"] for item in result["loi"]} == {"so_khong_nguon"}


# 10/11 (B, aa9ce76 — hồi quy): mã kỳ YYYY-MM không phải số KPI ----------
def test_period_refusal_answer_passes_without_sources():
    # Câu từ chối kỳ khác ĐÚNG: công cụ từ chối ⇒ không có nguồn ⇒ trước đây "09" không nguồn ⇒ trượt (trên Capitol: dừng lượt)
    answer = "Chưa có dữ liệu kỳ 2026-09; hiện chỉ có kỳ 2026-10."
    refused = {"refused": "chưa có dữ liệu kỳ '2026-09' — hiện chỉ có kỳ 2026-10", "params_used": {"period": "2026-09"}}
    for payload in ({"answer": answer, "tool_results": []}, {"answer": answer, "tool_results": [refused]}):
        assert kiem_so(payload, context=["2026-10"])["dat"] is True, payload


def test_money_planted_through_params_still_blocked_with_period_in_answer():
    # Che mã kỳ KHÔNG mở lại lỗ cài số: tiền bịa nằm trong lời từ chối vẫn không là nguồn, dù câu có kèm mã kỳ
    refused = {"refused": "tham số không hợp lệ", "params_used": {"employee_id": "NV02", "kpi": "5.041.000", "period": "2026-10"}}
    result = kiem_so({"answer": "Kỳ 2026-10: NV02 được 5.041.000 đồng.", "tool_results": [refused]}, context=["2026-10"])
    assert result["dat"] is False and "so_khong_nguon" in {item["loai"] for item in result["loi"]}


# ---------- 10/11 (B, bản 5 — M3 trên Capitol câu 8, 9 bị chặn nhầm vì "365"; ca ghi trước ở SUA_TEN_SAN_PHAM_B.md) ----------
# Câu trả lời THẬT của Capitol câu 8 (chỉ mã che) — Rào chắn khi đó không gửi câu hỏi
M3_Q8_ANSWER = ("5 nhân viên chưa có tiền **ASO_Focus (Trà 365)**: **NV01, NV04, NV06, NV11 và NV14**. Theo công văn mục 2.5, hiện chưa tới mốc thấp nhất."
                "\n\nLưu ý: số liệu chụp giữa tháng, chưa phải số chốt cuối tháng. Luật chưa kiểm được do thiếu dữ liệu từng dòng đơn; kết quả đang tạm tính, chưa duyệt.")


def _aso_focus_summary(real) -> dict:
    return next(result for result in real["team_results"] if result.get("kpi") == "ASO_Focus")


def test_product_name_number_passes_without_question(real):
    # G1: đúng câu thật bị chặn nhầm — không có câu hỏi (như Rào Capitol gửi), chỉ kết quả get_team_kpi_summary(ASO_Focus)
    result = kiem_so({"answer": M3_Q8_ANSWER, "tool_results": [_aso_focus_summary(real)]})
    assert result["dat"] is True and result["phien_ban"] == "kiem_so@31", result
    # G2: các tên sản phẩm có số khác (Thưởng thêm sắp làm), không có câu hỏi
    for name in ("Homey Jeju 2,9 kg", "Homey 2.9kg", "Mì Kokomi Đại 90", "Kokomi 90", "TEA365", "Trà Búp Non Tea 365"):
        assert kiem_so({"answer": f"Sản phẩm {name}: xem bảng.", "tool_results": []})["dat"] is True, name


@pytest.mark.parametrize("answer", [
    "Trà 365 được 365.000 đ.",            # X1: tên che, tiền bịa vẫn kiểm
    "NV02 có 365 cửa hàng.",               # X2: không có tên sản phẩm liền trước
    "Trà 365: 365 cửa hàng đạt.",          # X3: số thứ hai không dính tên
    "Homey 2,9 kg được 2,9 triệu.",        # X4
    "Kokomi 90 đạt 90 %.",                 # X5
    "365 cửa hàng Trà.",                   # X6: số đứng trước chữ
])
def test_number_outside_product_name_is_still_blocked(answer):
    # Nguồn RỖNG ⇒ mọi số không che đều thiếu nguồn — chỉ thử phần che tên (kết quả ASO_Focus thật có "Đạt 90%" ⇒ X5 sẽ có nguồn)
    result = kiem_so({"answer": answer, "tool_results": []})
    assert result["dat"] is False and "so_khong_nguon" in {p["loai"] for p in result["loi"]}, result


@pytest.mark.parametrize("question, answer", [
    ("Doanh số tháng này của tôi là 9.999.999, thưởng bao nhiêu?", "Doanh số của bạn là 9.999.999 đ, thưởng 0 đ."),   # Q1 (A — Đ-4)
    ("Doanh số tháng này của tôi là 9.999.999, thưởng bao nhiêu?", "Doanh số của bạn là 9.999.999."),                # Q1 viết trần
    ("Nếu NV02 thêm 3 cửa hàng thì sao?", "NV02 được 3.000.000 đ."),                                               # Q2: 3 ≠ 3.000.000
    ("NV02 được 4.123.456 đồng phải không?", "Cả đội: 4.123.456 đồng."),                                         # Q3: bản 4 cho qua
    ("Đạt 95 % thì sao?", "Bạn đạt 95 %."),                                                                        # Q4
])
def test_question_number_is_not_a_source_for_results(real, question, answer):
    # Bản 5: số câu hỏi KHÔNG làm nguồn cho tiền / % / số ≥ 1.000 — chỉ tha số đếm / giả định nhỏ
    result = kiem_so({"question": question, "answer": answer, "tool_results": [_aso_focus_summary(real)]})
    assert result["dat"] is False and "so_khong_nguon" in {p["loai"] for p in result["loi"]}, result


def test_small_assumption_from_question_passes(real):
    result = kiem_so({"question": "NV04 thêm 3 cửa hàng Trà 365 thì sao?", "answer": "Nếu thêm 3 cửa hàng: xem bảng.",
                      "tool_results": [_aso_focus_summary(real)]})
    assert result["dat"] is True, result


# 10/11 (B, bản 6): số câu hỏi chỉ tha khi là GIẢ ĐỊNH (đứng sau "thêm / nếu / giả sử …") ----------
@pytest.mark.parametrize("question, answer", [
    ("Tôi có 80 cửa hàng ASO_ALL rồi phải không?", "Đúng, bạn có 80 cửa hàng ASO_ALL."),     # bản 5 lọt (B thử trên 16b46a9)
    ("NV02 có 80 cửa hàng ASO_ALL rồi phải không?", "Đúng, NV02 có 80 cửa hàng ASO_ALL."),   # bản 5 lọt — có mã NV
])
def test_claimed_count_from_question_is_blocked(question, answer):
    result = kiem_so({"question": question, "answer": answer, "tool_results": []})
    assert result["dat"] is False and "so_khong_nguon" in {p["loai"] for p in result["loi"]}, result


@pytest.mark.parametrize("code", ["NV02", "NV05"])
def test_claimed_kpi_count_is_blocked_with_real_result(real, code):
    # Ca âm của (-4e): "tôi được 3 KPI đúng không?" khi thật khác 3 ⇒ "Đúng, bạn được 3 KPI" phải chặn. Số sai lấy từ dữ liệu thật:
    # số KPI có tiền thật của người đó + 1 (không gõ tay), và phải KHÔNG có ở đâu trong kết quả công cụ (số nhỏ có sẵn trong nguồn là giới hạn đã ghi)
    result_of = real["employee"][code]
    wrong = sum(1 for line in result_of["kpis"] if line["amount"] > 0) + 1
    assert Decimal(wrong) not in nc.source_numbers([result_of]), "số sai có sẵn trong nguồn — chọn số khác"
    for answer in (f"Đúng, bạn được {wrong} KPI có tiền.", f"Đúng, {code} được {wrong} KPI có tiền."):
        result = kiem_so({"question": f"Tôi được {wrong} KPI có tiền đúng không?", "answer": answer, "tool_results": [result_of]})
        assert result["dat"] is False, (answer, result)


def test_flag_reason_does_not_name_other_employees(real):
    # kiem_so@7 (an toàn): Capitol đưa NGUYÊN VĂN lý do vào lời nhắc "làm lại" của Agent ⇒ lý do không được mang mã NV khác người được hỏi
    owners, other = _mcp_flag(real)
    result = kiem_so({"question": f"Dữ liệu của {other} có vấn đề gì không?", "answer": f"{other}: có 1 dòng cửa hàng ngoài MCP.",
                      "tool_results": [*real["team_results"], real["issues"]]})
    text = result["ly_do"] + " ".join(p["chi_tiet"] for p in result["loi"])
    assert result["dat"] is False and not any(code in text for code in owners), text


# 10/11 (B, kiem_so@8 — @7, 2 minor) ----------
@pytest.mark.parametrize("answer", ["Kokomi 90 cửa hàng.", "NV04 có Mì Kokomi Đại 90 đơn.", "Trà 365 đồng."])
def test_count_right_after_product_name_is_not_masked(answer):
    # Số ngay sau tên mà theo sau là đơn vị đếm / tiền ⇒ là SỐ ĐẾM / tiền, không phải tên sản phẩm ⇒ nguồn rỗng ⇒ chặn
    result = kiem_so({"answer": answer, "tool_results": []})
    assert result["dat"] is False and "so_khong_nguon" in {p["loai"] for p in result["loi"]}, result


# ---------- 11/11 (B, cùng bậc câu cả đội — A phân định (f)): ô Excel chỉ lấy số câu hỏi khi là GIẢ ĐỊNH < 1.000, như phần chữ ----------
def _one_row_book(value) -> str:
    book = openpyxl.Workbook()
    book.active.append(["Mục", "Giá trị"])
    book.active.append(["Tổng", value])   # hàng KHÔNG có mã NV ⇒ chỉ luật nguồn số xét
    return _b64(book)


@pytest.mark.parametrize("question, value, ok", [
    ("Doanh số của tôi là 9.999.999 đồng, thưởng bao nhiêu?", 9999999, False),   # C E1 (hở ở ec5527ee)
    ("Tôi có 80 cửa hàng ASO_ALL rồi phải không?", 80, False),                    # C E2: không phải số giả định
    ("Nếu NV04 thêm 3 cửa hàng thì sao?", 3, True),                                # C E3: giả định < 1.000
    ("Nếu NV04 tăng 2.000.000 đồng doanh số?", 2000000, False),                   # C E4: giả định nhưng ≥ 1.000
])
def test_excel_question_numbers_only_small_assumptions(question, value, ok):
    result = kiem_so({"answer": "Xem file.", "question": question, "tool_results": [{"team_total": 50000000}], "excel_b64": _one_row_book(value)})
    assert result["dat"] is ok, result["loi"]


# ---------- kiem_so@18 (13/11): câu hỏi KHÔNG có mã NV ⇒ cờ theo người xét TỪNG DÒNG của câu trả lời ----------
# Hai câu trả lời THẬT bị @17 chặn oan trên Capitol (mốc 4, câu "Dữ liệu tháng này có gì cần để ý không?", 2 / 2 lượt — bản ghi
# 095004_mau_m4_c8.json): chữ "ngoài MCP" (cờ của các NV có cửa hàng ngoài
# tuyến) nằm ở MỘT gạch, mã NV06 / NV09 (lệch chỉ tiêu ASO_ALL) nằm ở gạch KHÁC ⇒ bản cũ gom mọi mã trong câu trả lời thành "người được hỏi".
TEAM_WIDE_ANSWERS = [
    """Có vài điểm cần lưu ý:

- Dữ liệu ngoài MCP đã bị loại theo công văn mục 6.2, ảnh hưởng đến các KPI ASO_ALL, Turnover, PC_BT, PC_ON và LPPC.
- Chỉ tiêu ASO_ALL lệch ở NV06 và NV09 khi đối chiếu với danh sách tuyến. PC_BT / PC_ON chưa đối chiếu được do thiếu số lần viếng thăm trong danh sách tuyến.
- Kết quả tháng này đang **tạm tính — chưa phải số chốt, chưa duyệt**.""",
    """Có thông tin cần lưu ý; không có lỗi hay cảnh báo.

- Theo công văn 6.2, số cửa hàng/đơn ngoài MCP của nhân viên đã bị loại khi tính các KPI: ASO_ALL, Turnover, PC_BT, PC_ON và LPPC.
- Chỉ tiêu ASO_ALL được tính lại từ danh sách tuyến có chênh lệch tại NV06 và NV09; các nhân viên khác khớp.

Số liệu đang **tạm tính, chưa phải số chốt và chưa duyệt**."""]


@pytest.mark.parametrize("answer", TEAM_WIDE_ANSWERS)
def test_team_wide_answer_naming_people_on_another_line_passes(real, answer):
    owners, _ = _mcp_flag(real)
    assert not owners & {"NV06", "NV09"}   # điều kiện của ca: NV06 / NV09 KHÔNG sở hữu cờ "ngoài MCP" trên dữ liệu thật tháng 10
    for question in ("Dữ liệu tháng này có gì cần để ý không?", ""):
        result = kiem_so({"question": question, "answer": answer, "tool_results": [*real["team_results"], real["issues"]]})
        assert "co_sai_nguoi" not in [p["loai"] for p in result["loi"]], result


def test_flag_of_other_person_on_the_same_line_is_still_blocked_without_asked_code(real):
    _, other = _mcp_flag(real)
    for answer in (f"{other} có đơn ở cửa hàng ngoài MCP.",                                     # cùng dòng
                   f"Có vài điểm:\n- {other} có đơn ở cửa hàng ngoài MCP.\n- Số liệu tạm tính."):  # cùng một gạch giữa các gạch khác
        result = kiem_so({"question": "Dữ liệu tháng này có gì cần để ý không?", "answer": answer,
                          "tool_results": [*real["team_results"], real["issues"]]})
        assert "co_sai_nguoi" in [p["loai"] for p in result["loi"]], (answer, result)


def test_flag_under_a_person_heading_belongs_to_that_person(real):
    # Mô hình hay viết tiêu đề từng người rồi gạch đầu dòng: cờ ở gạch KHÔNG có mã vẫn là của người ở tiêu đề gần nhất phía trên
    owners, other = _mcp_flag(real)
    owner = sorted(owners)[0]
    question, sources = "Dữ liệu tháng này có gì cần để ý không?", [*real["team_results"], real["issues"]]
    for heading in (f"{other}:", f"**{other}**", f"### {other}:"):
        blocked = kiem_so({"question": question, "answer": f"{heading}\n- có cửa hàng ngoài MCP đã bị loại.", "tool_results": sources})
        assert "co_sai_nguoi" in [p["loai"] for p in blocked["loi"]], (heading, blocked)
    passed = kiem_so({"question": question, "answer": f"{owner}:\n- có cửa hàng ngoài MCP đã bị loại.", "tool_results": sources})
    assert "co_sai_nguoi" not in [p["loai"] for p in passed["loi"]], passed
    # Dòng trống ngắt kế thừa; dòng THƯỜNG có mã không truyền mã xuống dòng sau
    for answer in (f"{other}:\n- chỉ tiêu ASO_ALL lệch.\n\nCả đội: cửa hàng ngoài MCP đã bị loại.",
                   f"- Chỉ tiêu ASO_ALL lệch ở {other}.\n- Cửa hàng ngoài MCP đã bị loại theo công văn."):
        result = kiem_so({"question": question, "answer": answer, "tool_results": sources})
        assert "co_sai_nguoi" not in [p["loai"] for p in result["loi"]], (answer, result)


def test_question_with_a_code_keeps_the_whole_answer_meaning(real):
    # Câu hỏi CÓ mã ⇒ nghĩa cũ: cả câu trả lời là về người được hỏi, kể cả khi chữ cờ nằm ở dòng khác, không có mã (ca K3 09/11)
    _, other = _mcp_flag(real)
    result = kiem_so({"question": f"Dữ liệu của {other} có vấn đề gì không?", "answer": "Có một điểm:\n- có 1 dòng cửa hàng ngoài MCP.",
                      "tool_results": [*real["team_results"], real["issues"]]})
    assert "co_sai_nguoi" in [p["loai"] for p in result["loi"]], result


def _mcp_owner_sets(real) -> tuple[set, list[set]]:
    """(HỢP chủ của mọi cờ "không thuộc MCP", danh sách chủ từng cờ) trên dữ liệu thật — tháng 10 có nhiều cờ cùng dấu này, chủ khác nhau theo chỉ tiêu."""
    each = [set(issue["employee_ids"]) for issue in real["issues"]["issues"] if issue["employee_ids"] and "không thuộc MCP" in issue["message"]]
    return set().union(*each), each


def test_true_statement_about_one_owner_passes_even_when_another_flag_has_other_owners():
    # 13/11 (ca 17, 18): xét từng cờ riêng thì câu ĐÚNG về một chủ cờ bị chặn vì "không giao" với cờ cùng dấu của người khác.
    # Cờ tự dựng theo đúng hình `get_data_issues` trên RAW 31/10 (dữ liệu của file test này chỉ có MỘT cờ "không thuộc MCP").
    sources = [{"issues": [{"level": "info", "message": "ASO_ALL: cửa hàng không thuộc MCP của chính NV (đã loại)", "employee_ids": ["NV04", "NV08", "NV14"]},
                           {"level": "info", "message": "PC_ON: đơn ở cửa hàng không thuộc MCP của chính NV (đã loại)", "employee_ids": ["NV08"]},
                           {"level": "info", "message": "PC_BT: đơn ở cửa hàng không thuộc MCP của chính NV (đã loại)", "employee_ids": ["NV04", "NV14"]}]}]
    blocked = kiem_so({"question": "", "answer": "- NV06 có cửa hàng ngoài MCP đã bị loại.", "tool_results": sources})
    assert "co_sai_nguoi" in [p["loai"] for p in blocked["loi"]], blocked
    for owner in ("NV04", "NV08", "NV14"):
        for question, answer in (("Dữ liệu tháng này có gì cần để ý không?", f"- {owner} có cửa hàng ngoài MCP đã bị loại."),
                                 (f"Dữ liệu của {owner} có vấn đề gì không?", f"{owner} có cửa hàng không thuộc MCP đã bị loại.")):
            result = kiem_so({"question": question, "answer": answer, "tool_results": sources})
            assert "co_sai_nguoi" not in [p["loai"] for p in result["loi"]], (owner, question, result)


def test_item_is_the_unit_and_every_person_in_it_must_own_the_flag(real):
    union, _ = _mcp_owner_sets(real)
    owner, other = sorted(union)[0], next(code for code in sorted(real["salary"]) if code not in union)
    question, sources = "Dữ liệu tháng này có gì cần để ý không?", [*real["team_results"], real["issues"]]
    blocked = [f"- Lưu ý về {other}\ncó đơn ngoài MCP đã bị loại.",                       # xuống dòng giữa câu: dòng nối tiếp thuộc mục trên
               f"- {owner}, {other} có cửa hàng ngoài MCP đã bị loại.",                   # lẫn người đúng + người sai trong một mục
               f"| Nhân viên | Ghi chú |\n|---|---|\n| {other} | có đơn ngoài MCP |",      # hàng bảng
               f"1. {other} có đơn ở cửa hàng không thuộc MCP."]                           # mục đánh số
    for answer in blocked:
        result = kiem_so({"question": question, "answer": answer, "tool_results": sources})
        assert "co_sai_nguoi" in [p["loai"] for p in result["loi"]], (answer, result)
    passed = [f"| Nhân viên | Ghi chú |\n|---|---|\n| {owner} | có đơn ngoài MCP |\n| {other} | không có lưu ý |",   # mỗi hàng bảng một mục
              f"- {', '.join(sorted(union))} có cửa hàng ngoài MCP đã bị loại.\n- {other}: không có lưu ý riêng."]
    for answer in passed:
        result = kiem_so({"question": question, "answer": answer, "tool_results": sources})
        assert "co_sai_nguoi" not in [p["loai"] for p in result["loi"]], (answer, result)


def test_question_code_applies_to_items_that_name_nobody_but_not_to_items_naming_owners(real):
    union, _ = _mcp_owner_sets(real)
    other = next(code for code in sorted(real["salary"]) if code not in union)
    sources = [*real["team_results"], real["issues"]]
    # Hỏi về người KHÔNG có cờ, mục không nêu ai ⇒ vẫn là về người được hỏi ⇒ CHẶN (không mở lại ca K3)
    nobody = kiem_so({"question": f"Dữ liệu của {other} có vấn đề gì không?", "answer": "Có cửa hàng không thuộc MCP đã bị loại.", "tool_results": sources})
    assert "co_sai_nguoi" in [p["loai"] for p in nobody["loi"]], nobody
    # Hỏi về người đó, câu trả lời nói thêm ĐÚNG về các chủ cờ ở mục riêng ⇒ QUA (trước: chặn oan vì cả câu trả lời bị coi là về người được hỏi)
    extra = kiem_so({"question": f"Dữ liệu của {other} có vấn đề gì không?",
                     "answer": f"- {other}: không có lưu ý riêng.\n- Ngoài ra {', '.join(sorted(union))} có cửa hàng ngoài MCP đã bị loại.", "tool_results": sources})
    assert "co_sai_nguoi" not in [p["loai"] for p in extra["loi"]], extra


# kiem_so@18 vòng 3 (13/11): luật CHA–CON — viết 17 ca lách sau khi đọc bản vòng 2, 5 ca LỌT mà @17 đang chặn ----------
# Cờ tự dựng theo đúng hình `get_data_issues` trên RAW 31/10: "ngoài MCP" của NV04 / NV08 / NV14; "lệch … MCP" của NV06 / NV09.
PARENT_CHILD_SOURCES = [{"issues": [
    {"level": "info", "message": "ASO_ALL: cửa hàng không thuộc MCP của chính NV (đã loại)", "employee_ids": ["NV04", "NV08", "NV14"]},
    {"level": "info", "message": "PC_ON: đơn ở cửa hàng không thuộc MCP của chính NV (đã loại)", "employee_ids": ["NV08"]},
    {"level": "info", "message": "lệch (MCP có thể đổi sau ngày chốt mẫu số): chỉ tiêu ASO_ALL khác số tính lại", "employee_ids": ["NV06", "NV09"]}]}]
NO_CODE_QUESTION = "Dữ liệu tháng này có gì cần để ý không?"
MUST_BLOCK = {
    "L4 tiêu đề + dòng trống + gạch": "NV06:\n\n- có đơn ngoài MCP đã bị loại.",
    "L12 chữ cờ ở tiêu đề, mã ở gạch": "Các nhân viên có đơn ngoài MCP đã bị loại:\n- NV06\n- NV04",
    "L1 tiêu đề chung chen giữa": "NV06:\nLưu ý:\n- có đơn ngoài MCP đã bị loại.",
    "L2 dòng dẫn không có dấu hai chấm": "Về NV06 thì có mấy điểm sau\n- có đơn ngoài MCP đã bị loại.",
    "L6 chữ cờ ở tiêu đề cột của bảng": "Nhân viên | Có đơn ngoài MCP\n---|---\nNV06 | có\nNV04 | có",
    "tiêu đề in đậm": "**NV06**\n- có đơn ngoài MCP đã bị loại.",
    "mục đánh số tách dòng": "1. NV06\n   có đơn ngoài MCP đã bị loại.",
    "tiêu đề hai người": "NV04, NV06:\n- có cửa hàng ngoài MCP đã bị loại.",
    "người này ở mục kế": "- NV06\n- Người này có đơn ngoài MCP đã bị loại.",
    "tiêu đề rồi dòng văn ngay dưới": "NV06:\nCó đơn ngoài MCP đã bị loại.",
    "H3 tiêu đề chưa có gạch con + dòng trống + đoạn văn": "NV06:\n\nCó đơn ngoài MCP đã bị loại.",
    "xuống dòng giữa đoạn văn": "Có một lưu ý về NV06\ncó đơn ngoài MCP đã bị loại.",
    "một hàng bảng": "NV06 | có đơn ngoài MCP",
    "cờ lệch gán sai người": "- NV04 có chỉ tiêu ASO_ALL lệch so với MCP.",
}
MUST_PASS = {
    "dòng dẫn mang hai dấu cờ, gạch con tự mang dấu của mình":
        "Có cửa hàng ngoài MCP đã bị loại và chỉ tiêu ASO_ALL lệch so với MCP ở một số người:\n- NV04, NV08, NV14: cửa hàng ngoài MCP\n"
        "- NV06, NV09: chỉ tiêu lệch so với MCP",
    "đoạn lưu ý không gạch đứng sau các gạch, cách một dòng trống":
        "NV06:\n- chỉ tiêu ASO_ALL lệch so với MCP.\n\nLưu ý chung: cửa hàng ngoài MCP đã bị loại theo công văn.",
    "danh sách đúng chủ dưới tiêu đề mang chữ cờ": "Các nhân viên có cửa hàng ngoài MCP đã bị loại:\n- NV04\n- NV08\n- NV14",
    "tiêu đề từng người, gạch của đúng người": "- NV04:\n  - có cửa hàng ngoài MCP đã bị loại\n- NV06:\n  - chỉ tiêu ASO_ALL lệch so với MCP",
    "bảng đúng": "Nhân viên | Có đơn ngoài MCP\n---|---\nNV04 | có\nNV08 | có",
    "hai gạch cùng cấp như lượt thật": "- Cửa hàng ngoài MCP đã bị loại theo công văn 6.2.\n- Chỉ tiêu ASO_ALL lệch ở NV06 và NV09.",
}


@pytest.mark.parametrize("answer", MUST_BLOCK.values(), ids=MUST_BLOCK.keys())
def test_parent_child_wrong_person_is_blocked(answer):
    result = kiem_so({"question": NO_CODE_QUESTION, "answer": answer, "tool_results": PARENT_CHILD_SOURCES})
    assert "co_sai_nguoi" in [p["loai"] for p in result["loi"]], result


@pytest.mark.parametrize("answer", MUST_PASS.values(), ids=MUST_PASS.keys())
def test_parent_child_true_answer_passes(answer):
    result = kiem_so({"question": NO_CODE_QUESTION, "answer": answer, "tool_results": PARENT_CHILD_SOURCES})
    assert "co_sai_nguoi" not in [p["loai"] for p in result["loi"]], result


def test_parent_child_child_code_wins_over_lead_code_when_question_names_someone():
    # Hỏi NV06: dòng dẫn mang mã NV06, gạch con mang mã KHÁC nói đúng về chủ cờ ⇒ QUA; gạch con không mã mang cờ của người khác ⇒ CHẶN
    passed = kiem_so({"question": "Dữ liệu của NV06 có vấn đề gì không?", "tool_results": PARENT_CHILD_SOURCES,
                      "answer": "NV06 có 2 điểm:\n- chỉ tiêu ASO_ALL lệch so với MCP\n- lưu ý chung: NV04, NV08, NV14 có cửa hàng ngoài MCP"})
    assert "co_sai_nguoi" not in [p["loai"] for p in passed["loi"]], passed
    blocked = kiem_so({"question": "Dữ liệu của NV06 có vấn đề gì không?", "tool_results": PARENT_CHILD_SOURCES,
                       "answer": "NV06 có 2 điểm:\n- chỉ tiêu ASO_ALL lệch so với MCP\n- có cửa hàng ngoài MCP đã bị loại"})
    assert "co_sai_nguoi" in [p["loai"] for p in blocked["loi"]], blocked


# ---------- kiem_so@18 hoàn tất vòng 3 (13/11): cha–con theo THỤT LỀ + dấu cờ lệch nhận chữ "danh sách tuyến" ----------
# Hình câu NHÁP THẬT của mô hình sáng 13/11 (ca T1–T7): gạch cấp 1 kết thúc ":" nói "ngoài MCP", các gạch con thụt lề đúng chủ,
# rồi một gạch CÙNG CẤP nói NV06, NV09 lệch chỉ tiêu "theo danh sách tuyến" ⇒ bản trước coi gạch cùng cấp là con của gạch kết thúc ":" ⇒ chặn oan.
INDENT_LEAD = "- Cửa hàng không thuộc MCP của nhân viên đã bị loại khỏi tính thưởng:\n  - ASO_ALL và Turnover: NV04, NV08, NV14.\n  - PC_ON: NV08.\n"
SIBLING = "- Chỉ tiêu ASO_ALL tính lại theo danh sách tuyến bị lệch ở NV06 và NV09; các nhân viên khác khớp."
INDENT_PASS = {
    "T1 gạch cùng cấp sau khối thụt lề": INDENT_LEAD + SIBLING,
    "T5 thêm dòng trống trước gạch cùng cấp": INDENT_LEAD + "\n" + SIBLING,
    "T7 dòng dẫn là đoạn văn, gạch con, rồi đoạn văn khép khối":
        "Cửa hàng không thuộc MCP của nhân viên đã bị loại khỏi tính thưởng:\n- ASO_ALL và Turnover: NV04, NV08, NV14.\n"
        "Chỉ tiêu ASO_ALL tính lại theo danh sách tuyến bị lệch ở NV06 và NV09.",
    "T2 gạch cùng cấp tự viết chữ MCP": INDENT_LEAD + "- Chỉ tiêu ASO_ALL lệch so với MCP ở NV06 và NV09.",
    "nguyên chữ dòng lưu ý cả đội của hệ thống": "- chỉ tiêu ASO_ALL tính lại từ danh sách tuyến (80 % số cửa hàng trong tuyến): lệch ở NV06, NV09",
}
INDENT_BLOCK = {
    "T4 gạch con thụt lề gán sai người": "- Cửa hàng không thuộc MCP của nhân viên đã bị loại khỏi tính thưởng:\n  - ASO_ALL và Turnover: NV06.",
    "cờ lệch theo danh sách tuyến gán sai người": "- Chỉ tiêu ASO_ALL tính lại theo danh sách tuyến bị lệch ở NV04.",
    "gạch dẫn có mã, gạch con thụt lề mang cờ người khác": "- NV06:\n  - có đơn ngoài MCP đã bị loại.",
    "xuống dòng giữa câu của một gạch (ca 6)": "- Lưu ý về NV06\ncó đơn ngoài MCP đã bị loại.",
}


@pytest.mark.parametrize("answer", INDENT_PASS.values(), ids=INDENT_PASS.keys())
def test_indent_siblings_are_not_children(answer):
    result = kiem_so({"question": NO_CODE_QUESTION, "answer": answer, "tool_results": PARENT_CHILD_SOURCES})
    assert "co_sai_nguoi" not in [p["loai"] for p in result["loi"]], result


@pytest.mark.parametrize("answer", INDENT_BLOCK.values(), ids=INDENT_BLOCK.keys())
def test_indent_children_still_inherit(answer):
    result = kiem_so({"question": NO_CODE_QUESTION, "answer": answer, "tool_results": PARENT_CHILD_SOURCES})
    assert "co_sai_nguoi" in [p["loai"] for p in result["loi"]], result


# ---------- kiem_so@20 (13/11): 2 chặn oan có từ @17 tìm bằng phát lại 2.038 chữ thật (kiem_b/phat_lai_hop_nhat_rao_b.py) ----------
def _ratio_flagged(result: dict) -> bool:
    return any("tỷ lệ" in problem["chi_tiet"] for problem in result["loi"])


def test_kpi_count_and_date_are_not_ratio_actual(real):
    # Câu 206 thật "NV09 đang có tiền ở 5 KPI: LPPC 2.000.000 đ, …" và câu 1 "… tính đến ngày 20/10. Trong đó, LPPC được …": số 5 / 20/10 không phải
    # số thực hiện LPPC
    lppc = _ratio_row(real, "NV04", "LPPC")
    sources = [real["employee"]["NV04"]]
    for answer in (f"NV04 đang có tiền ở 5 KPI: LPPC {_vn(lppc['amount'])} đ, ASO_ALL 0 đ.",
                   f"NV04 đang có tiền ở **2 KPI**: LPPC — {_vn(lppc['amount'])} đ",
                   f"NV04 tính đến ngày 20/10. Trong đó, LPPC được {_vn(lppc['amount'])} đ."):
        assert not _ratio_flagged(kiem_so({"answer": answer, "tool_results": sources})), answer


@pytest.mark.parametrize("template", ["NV04 đạt {value} ở LPPC_Meat.", "NV04 có tỷ lệ {value} dòng/đơn của LPPC_Meat."])
def test_ratio_number_before_kpi_name_still_checked(real, template):
    # A lo 13/11: thu hẹp vế không được mở khe "số đứng TRƯỚC tên KPI" — số sai (= chỉ tiêu, không phải thực hiện) ⇒ chặn; số đúng ⇒ không bắt
    row = _ratio_row(real, "NV04", "LPPC_Meat")
    sources = [real["employee"]["NV04"]]
    assert _ratio_flagged(kiem_so({"answer": template.format(value=_vn_dec(row["target"])), "tool_results": sources}))
    assert not _ratio_flagged(kiem_so({"answer": template.format(value=_vn_dec(row["actual"])), "tool_results": sources}))


def test_team_total_before_not_belonging_to_employee_passes(real):
    # Câu 802 thật: tổng cả đội đứng trước "không phải tiền của NV08" ⇒ không gán cho NV08; "NV08 được <tổng đội>" vẫn chặn
    total = _vn(sum(real["salary"].values()))
    good = kiem_so({"answer": f"{total} đ là tổng lương kinh doanh cả đội, không phải tiền của NV08.", "tool_results": real["team_results"]})
    bad = kiem_so({"answer": f"NV08 được {total} đ.", "tool_results": real["team_results"]})
    assert "so_sai_nguoi" not in [p["loai"] for p in good["loi"]], good
    assert "so_sai_nguoi" in [p["loai"] for p in bad["loi"]], bad


# kiem_so@22: số tiền cạnh tên một khoản phải là ô của ĐÚNG khoản đó ----------
def _nv04_items(real) -> dict:
    # Hai KPI có tiền KHÁC 0 và khác nhau (bản Tracking 20/10 của fixture: một số KPI của NV04 là 0) — tên + số lấy từ dữ liệu, không gõ cứng
    row = real["employee"]["NV04"]
    paid = sorted({kpi["amount"]: kpi["kpi"] for kpi in row["kpis"] if kpi["amount"] and kpi["amount"] != row["business_salary"]}.items())
    (amount_a, name_a), (amount_b, name_b) = paid[-1], paid[-2]
    return {"A": name_a, "a": _vn(amount_a), "B": name_b, "b": _vn(amount_b), "salary": _vn(row["business_salary"]),
            "over": _vn(row["over_target_bonus"])}


@pytest.mark.parametrize("template, ok", [
    ("NV04 được thưởng vượt {a} đ.", False),                       # lỗ thật: tiền một KPI gọi là thưởng vượt (thưởng vượt NV04 = 0)
    ("NV04 có tổng lương kinh doanh {a} đ.", False),
    ("NV04: {A} {b} đ.", False),                                   # tiền của KPI B gán cho A
    ("{a} đ là tiền {B} của NV04.", False),                        # tên khoản đứng sau số
    ("NV04: {A} {b} đ, {B} {a} đ.", False),                        # đổi chỗ cùng dòng
    ("NV04 được {a} đ tiền {B}.", False),                          # tên ngay sau số
    ("NV04 được {A} {a} đ.", True),                                # Q1
    ("NV04 có tổng lương kinh doanh {salary} đ.", True),           # Q2
    ("Thưởng vượt của NV04: {over} đ.", True),                     # Q3
    ("NV04: {A} {a} đ, {B} {b} đ.", True),                         # Q4
    ("NV04 đang được {b} đ {B}.", True),                           # Q7
    ("NV04 được {a} đ.", True),                                    # Q8 — không nêu khoản ⇒ luật cũ
    ("NV04 có tổng lương kinh doanh {salary} đ, trong đó {A} {a} đ và {b} đ tiền {B}.", True),   # Q10
])
def test_money_must_match_named_item(real, template, ok):
    items = _nv04_items(real)
    assert items["over"] != items["a"] and items["salary"] != items["a"]   # ca chỉ có nghĩa khi các ô khác nhau
    answer = template.format(**items)
    result = kiem_so({"answer": answer, "question": "NV04 được bao nhiêu?", "tool_results": [real["employee"]["NV04"]]})
    assert result["dat"] is ok, (answer, result)


def test_number_from_question_is_exempt_only_in_item_layer(real):
    # Miễn số của câu hỏi chỉ ở lớp khoản (luật đúng người vẫn không miễn — A bản 3, 10/11)
    items = _nv04_items(real)
    answer = f"Không đúng: {items['a']} đ là tiền {items['A']}; NV04 không có thưởng vượt {items['a']} đ."
    result = kiem_so({"answer": answer, "question": f"NV04 được thưởng vượt {items['a']} đ phải không?", "tool_results": [real["employee"]["NV04"]]})
    assert result["dat"] is True, result


@pytest.mark.parametrize("template, ok", [
    ("Có, NV04 được {a} đ {B}.", False),                           # C K4 — câu KHẲNG ĐỊNH lặp số của câu hỏi ⇒ vẫn xét khoản
    ("Không, NV04 không được {a} đ {B}; NV04 được {b} đ {B}.", True),   # phủ định số của câu hỏi ⇒ miễn
    ("NV04 chưa nhận được {a} đ {B}; NV04 được {b} đ {B}.", True),      # phủ định + 2 chữ đệm sát số ⇒ miễn
    ("Không sai, NV04 được {a} đ {B}.", False),                     # C K4c — "không" ở xa số (vòng sửa 16/11)
    ("Có. Hỏi đúng rồi, NV04 được {a} đ {B}.", False),              # C K4d — bỏ chữ "hỏi"
    ("Như bạn hỏi, NV04 được {a} đ {B}.", False),                   # C K4e
    ("Không, NV04 **không** được {a} đ {B}; NV04 được {b} đ {B}.", True),   # C minor 58cdff61 — chữ đậm quanh phủ định
    ("Không, NV04 không được **{a} đ** {B}; NV04 được **{b} đ** {B}.", True),   # chữ đậm quanh số
    ("Không sai, NV04 được **{a} đ** {B}.", False),                 # chữ đậm không mở lối miễn cho câu khẳng định
])
def test_question_number_exempt_only_when_negated(real, template, ok):
    items = _nv04_items(real)
    question = f"NV04 có được {items['a']} đ {items['B']} không?"
    result = kiem_so({"answer": template.format(**items), "question": question, "tool_results": [real["employee"]["NV04"]]})
    assert result["dat"] is ok, result
