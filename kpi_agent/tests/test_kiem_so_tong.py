"""Rào kiểm số với con số TỔNG 8 KPI + THƯỞNG THÊM của một nhân viên (18/11/2026).

Lỗi thật (Capitol, phiếu NV03, hai lượt 18/11): câu ĐÚNG "Tổng thưởng thêm: E. Tổng 8 KPI cộng thưởng thêm: T" bị chặn với lời "số T không phải
tiền thưởng thêm của NV03" — lớp "đúng khoản" coi T là tiền của khoản thưởng thêm vì chữ số "8" trong "8 KPI" cắt mất chữ "tổng", và rào không biết
ô `total_8_kpi_and_extra` công cụ `get_extra_bonus` trả. Luật mới: nhãn đứng trước số nhắc ĐỦ hai phía (8 KPI / lương kinh doanh · thưởng thêm) và
không có khoản thứ ba ⇒ số phải BẰNG ô tổng của đúng người (so thật, không nới thành "không xác định").

Backend chạy thật trong bộ nhớ (TestClient, đường RAW, ngày chốt 31/10, dữ liệu thật trên máy, kho dấu tạm), 0 lần gọi AI. Số trong câu lấy từ kết
quả công cụ, không gõ tay — trừ hai câu NGUYÊN VĂN Capitol (số cố định, test tự kiểm chúng còn khớp công cụ).
"""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json",
          ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"   # khoá giả chỉ sống trong test
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
NV, OTHER = "NV03", "NV05"
QUESTION = "NV03 được những khoản thưởng thêm nào, mỗi khoản bao nhiêu, tổng 8 KPI cộng thưởng thêm là bao nhiêu?"

# NGUYÊN VĂN hai câu trả lời bị chặn (C chép từ bản ghi lượt 7300c715 và 139852fb, 18/11); câu 2 bỏ phần tên thật trong ngoặc.
CAPITOL_1 = """NV03 có các khoản thưởng thêm:

- **Thưởng 03: ASO nước mắm cao cấp:** 180.000 đ. Lưu ý: nhóm hàng được xác định theo trang theo dõi của đội; công văn và sổ không liệt kê nhóm này.
- **Thưởng 04: ASO xốt Chinsu:** 50.000 đ.
- **Thưởng 05: ASO Homey Jeju 2.9kg:** 260.000 đ.
- **Thưởng 06: Doanh số Mì Kokomi 90:** 500.000 đ.

**Tổng thưởng thêm:** 990.000 đ. **Tổng 8 KPI cộng thưởng thêm:** 9.930.000 đ. Số liệu tạm tính — chưa phải số chốt, chưa duyệt."""
CAPITOL_2 = """NV03 đạt các khoản thưởng thêm sau:

- Thưởng 03: ASO nước mắm cao cấp — 180.000 đ.
- Thưởng 04: ASO xốt Chinsu — 50.000 đ.
- Thưởng 05: ASO Homey Jeju 2.9kg — 260.000 đ.
- Thưởng 06: Doanh số Mì Kokomi 90 — 500.000 đ.

Tổng thưởng thêm là 990.000 đ; tổng 8 KPI cộng thưởng thêm là 9.930.000 đ. Số liệu **tạm tính — chưa phải số chốt, chưa duyệt**.

Lưu ý: nhóm hàng nước mắm cao cấp của Thưởng 03 lấy theo trang theo dõi của đội; công văn và sổ không liệt kê nhóm này."""


def _vn(amount) -> str:
    return f"{int(amount):,}".replace(",", ".")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(tmp_path_factory.mktemp("store") / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


@pytest.fixture(scope="module")
def money(client) -> dict:
    """Chữ số tiền của NV03 (K = 8 KPI, E = thưởng thêm, T = tổng), tổng của một người khác (T2) và tổng cả đội (S) — đều từ công cụ."""
    def extra(args):
        return client.post("/api/tool/get_extra_bonus", json={"args": args}).json()
    one, other, team = extra({"employee_id": NV}), extra({"employee_id": OTHER}), extra({})
    values = {"K": one["business_salary_8_kpi"], "E": one["extra_bonus_total"], "T": one["total_8_kpi_and_extra"],
              "K2": other["business_salary_8_kpi"], "E2": other["extra_bonus_total"], "T2": other["total_8_kpi_and_extra"],
              "S": team["team_total_8_kpi_and_extra"]}
    assert values["T"] == values["K"] + values["E"] and values["E"] > 0 and values["T2"] != values["T"]
    assert len({values["K"], values["E"], values["T"], values["T2"], values["S"]}) == 5   # các số phải khác nhau thì ca "gán nhầm" mới có nghĩa
    values["T_SAI"] = values["T"] + 1_000
    return {key: _vn(value) for key, value in values.items()}


def _kiem(client, answer: str, calls: list | None = None, question: str = QUESTION) -> dict:
    """calls=None ⇒ nguồn số = bảng cả đội máy chủ tự lấy (cách Rào chắn Capitol gọi khi chỉ có chữ); có calls ⇒ máy chủ chạy lại đúng các lần gọi đó."""
    body = {"answer": answer, "question": question, **({"tool_calls": calls} if calls is not None else {})}
    return client.post("/api/system/kiem-so", json=body, headers=KEY).json()


EXTRA_CALL = [{"name": "get_extra_bonus", "args": {"employee_id": NV}}]
BOTH_CALLS = [{"name": "get_employee_result", "args": {"employee_id": NV}}, *EXTRA_CALL]
SOURCES = [pytest.param(None, id="nguon_ca_doi"), pytest.param(BOTH_CALLS, id="nguon_hai_lan_goi")]

# Câu ĐÚNG về số tổng ⇒ phải QUA. Năm câu đầu + hai câu Capitol là các câu bị chặn oan ở kiem_so@23.
GOOD = [
    "NV03: tổng 8 KPI và thưởng thêm là {T} đ.",
    "NV03: lương kinh doanh 8 KPI {K} đ + thưởng thêm {E} đ = {T} đ.",
    "- Lương kinh doanh 8 KPI của NV03: {K} đ\n- Thưởng thêm của NV03: {E} đ\n- Tổng 8 KPI + thưởng thêm của NV03: {T} đ",
    "Phiếu NV03 — thưởng thêm: {E} đ; tổng cộng (8 KPI và thưởng thêm): {T} đ.",
    "| Khoản | Tiền |\n|---|---|\n| Thưởng thêm của NV03 | {E} đ |\n| Tổng 8 KPI và thưởng thêm của NV03 | {T} đ |",
    # đã qua từ trước — giữ
    "NV03 được thưởng thêm {E} đồng, ngoài 8 KPI. Tính cả 8 KPI thì tổng là {T} đồng.",
    "NV03 tổng {T} đ.",
    "**Tổng thu nhập của NV03 (lương kinh doanh + thưởng thêm): {T} đ**",
    # H1 (C): tổng và thành phần trên cùng một dòng — quyết theo TỪNG số, K và E không bị coi là số tổng
    "NV03 nhận tổng cộng {T} đ, gồm {K} đ lương kinh doanh và {E} đ thưởng thêm.",
    "NV03: tổng 8 KPI là {K} đ, thưởng thêm {E} đ.",
    "NV03: {T} đ = {K} đ lương kinh doanh + {E} đ thưởng thêm.",
    "NV03: 8 KPI {K} đ, thưởng thêm {E} đ, tổng {T} đ.",
    "NV03: tổng thưởng thêm {E} đ; tổng 8 KPI cộng thưởng thêm {T} đ.",
    # H2 (C): bảng nhiều người, mỗi dòng tổng của đúng người đó
    "| Nhân viên | Tổng 8 KPI và thưởng thêm |\n|---|---|\n| NV03 | {T} đ |\n| NV05 | {T2} đ |",
]

# Câu SAI ⇒ phải CHẶN (sàn không được mất khi sửa).
BAD = [
    "NV03 được thưởng thêm {T} đ.",                                  # số tổng gọi là thưởng thêm
    "Thưởng thêm của NV03 là {K} đ.",                                # số 8 KPI gọi là thưởng thêm
    "Lương kinh doanh của NV03 là {T} đ.",                           # số tổng gọi là lương kinh doanh
    "Tổng thưởng thêm của NV03 là {T} đ.",                           # chỉ một phía + chữ "tổng"
    "NV03: tổng 8 KPI và thưởng thêm là {T_SAI} đ.",                 # tổng bịa (lệch 1.000)
    # H3 (C): thành phần đội lốt tổng
    "Tổng 8 KPI và thưởng thêm của NV03 là {K} đ.",
    "Tổng 8 KPI và thưởng thêm của NV03 là {E} đ.",
    "NV03: lương kinh doanh 8 KPI {K} đ + thưởng thêm {E} đ = {K} đ.",
    # H2 (C): tổng của người khác
    "Tổng 8 KPI và thưởng thêm của NV03 là {T2} đ.",
    "| Nhân viên | Tổng 8 KPI và thưởng thêm |\n|---|---|\n| NV03 | {T2} đ |\n| NV05 | {T} đ |",
]


@pytest.mark.parametrize("calls", SOURCES)
@pytest.mark.parametrize("template", GOOD)
def test_true_total_sentences_pass(client, money, template, calls):
    if "NV05" in template and calls is not None:
        calls = [*calls, {"name": "get_extra_bonus", "args": {"employee_id": OTHER}}]
    result = _kiem(client, template.format(**money), calls)
    assert result["dat"] is True, result["loi"]


@pytest.mark.parametrize("calls", SOURCES)
@pytest.mark.parametrize("template", BAD)
def test_wrong_total_sentences_blocked(client, money, template, calls):
    if "NV05" in template and calls is not None:
        calls = [*calls, {"name": "get_extra_bonus", "args": {"employee_id": OTHER}}]
    result = _kiem(client, template.format(**money), calls)
    assert result["dat"] is False, template


@pytest.mark.parametrize("calls", [pytest.param(None, id="nguon_ca_doi"), pytest.param(EXTRA_CALL, id="nguon_mot_lan_goi_thuong_them")])
@pytest.mark.parametrize("answer", [pytest.param(CAPITOL_1, id="luot_7300c715"), pytest.param(CAPITOL_2, id="luot_139852fb")])
def test_capitol_verbatim_answers_pass(client, money, answer, calls):
    assert money["E"] == "990.000" and money["T"] == "9.930.000"   # số cố định trong câu nguyên văn còn khớp công cụ
    result = _kiem(client, answer, calls)
    assert result["dat"] is True, result["loi"]


@pytest.mark.parametrize("answer", [CAPITOL_1, CAPITOL_2])
def test_capitol_verbatim_answers_with_wrong_total_blocked(client, answer):
    for wrong in ("9.931.000", "8.940.000", "990.000"):   # tổng bịa · số 8 KPI đội lốt tổng · số thưởng thêm đội lốt tổng
        changed = answer.replace("9.930.000", wrong)
        assert changed != answer
        assert _kiem(client, changed, EXTRA_CALL)["dat"] is False, wrong


def test_total_needs_a_tool_result_carrying_it(client, money):
    # H7 (C): ô tổng lấy từ kết quả công cụ do HỆ THỐNG ghi. Lượt chỉ gọi get_employee_result (không có ô tổng) mà câu trả lời nêu số tổng ⇒ số đó
    # không có nguồn trong lượt ⇒ chặn (trợ lý phải gọi công cụ thưởng thêm, không tự cộng); gọi đủ công cụ ⇒ qua.
    answer = "NV03: tổng 8 KPI và thưởng thêm là {T} đ.".format(**money)
    only_result = [{"name": "get_employee_result", "args": {"employee_id": NV}}]
    assert _kiem(client, answer, only_result)["dat"] is False
    assert _kiem(client, answer, BOTH_CALLS)["dat"] is True


# ---------- kiem_so@25: ba lỗ CÓ TỪ TRƯỚC người kiểm tìm khi dò @24 (câu SAI mà @23 lẫn @24 đều cho qua) ----------

@pytest.fixture(scope="module")
def part(client, money) -> dict:
    """Thêm: P = tiền MỘT KPI của NV03 (khác K, E, T), tên KPI đó, L = tiền một dòng thưởng thêm (khác E), S / T2 đã có ở `money`."""
    result = client.post("/api/tool/get_employee_result", json={"args": {"employee_id": NV}}).json()
    extra = client.post("/api/tool/get_extra_bonus", json={"args": {"employee_id": NV}}).json()
    taken = {money["K"], money["E"], money["T"]}
    kpi = next(row for row in result["kpis"] if row["amount"] > 0 and _vn(row["amount"]) not in taken)
    bonus_line = next(row for row in extra["lines"] if row["amount"] > 0 and _vn(row["amount"]) not in taken | {_vn(kpi["amount"])})
    return {**money, "P": _vn(kpi["amount"]), "KPI": kpi["kpi"], "L": _vn(bonus_line["amount"])}


TEAM_CALL = [{"name": "get_extra_bonus", "args": {}}]
HEAD = "| NV | 8 KPI | Thưởng thêm | Tổng 8 KPI + thưởng thêm |\n|---|---|---|---|\n"

# (a) BẢNG: tên khoản nằm ở TIÊU ĐỀ CỘT, không ở dòng của số
TABLE_BAD = [
    HEAD + "| NV03 | {K} đ | {T} đ | {T} đ |",                       # ô "Thưởng thêm" ghi số tổng (ca R2a của người kiểm)
    HEAD + "| NV03 | {E} đ | {E} đ | {T} đ |",                       # ô "8 KPI" ghi số thưởng thêm
    HEAD + "| NV03 | {K} đ | {E} đ | {K} đ |",                       # ô tổng ghi số 8 KPI
    "| Nhân viên | Lương kinh doanh | Thưởng thêm |\n|---|---|---|\n| NV03 | {E} đ | {K} đ |",        # đảo hai cột
    "| Nhân viên | {KPI} | Thưởng thêm |\n|---|---|---|\n| NV03 | {E} đ | {P} đ |",                  # tiền một KPI ↔ thưởng thêm đảo cột
]
TABLE_GOOD = [
    HEAD + "| NV03 | {K} đ | {E} đ | {T} đ |",
    HEAD + "| NV03 | {K} đ | {E} đ | {T} đ |\n| NV05 | {K2} đ | {E2} đ | {T2} đ |",
    "| Nhân viên | Lương kinh doanh | Thưởng thêm |\n|---|---|---|\n| NV03 | {K} đ | {E} đ |",
    "| Nhân viên | {KPI} | Thưởng thêm |\n|---|---|---|\n| NV03 | {P} đ | {E} đ |",
    "| Khoản | Tiền |\n|---|---|\n| Thưởng thêm của NV03 | {E} đ |\n| Tổng 8 KPI và thưởng thêm của NV03 | {T} đ |",   # nhãn ở DÒNG, tiêu đề cột trung tính
]
# (b) CẤP ĐỘI: "tổng 8 KPI và thưởng thêm của cả đội" phải bằng ô tổng đội
TEAM_BAD = [
    "Tổng 8 KPI và thưởng thêm của cả đội là {T} đ.",                # tổng của MỘT người gọi là của cả đội
    "Cả đội: tổng 8 KPI + thưởng thêm {T2} đ.",
    "Toàn đội có tổng lương kinh doanh và thưởng thêm là {K} đ.",
]
TEAM_GOOD = ["Tổng 8 KPI và thưởng thêm của cả đội là {S} đ.", "Cả đội: tổng 8 KPI + thưởng thêm {S} đ."]
# (c) "TỔNG" TRẦN: tiền một khoản lẻ của người đó không được gọi là "tổng"
BARE_BAD = ["NV03 tổng {P} đ.", "Tổng của NV03 là {P} đ.", "NV03 nhận tổng cộng {L} đ."]
BARE_GOOD = ["NV03 tổng {T} đ.", "NV03 tổng {K} đ.", "NV03 có tổng {KPI} {P} đ.", "NV03: {KPI} {P} đ."]


def _with_other(template: str, calls):
    return [*calls, {"name": "get_extra_bonus", "args": {"employee_id": OTHER}}] if "NV05" in template and calls is not None else calls


@pytest.mark.parametrize("calls", SOURCES)
@pytest.mark.parametrize("template", TABLE_BAD + BARE_BAD)
def test_holes_wrong_sentences_blocked(client, part, template, calls):
    assert _kiem(client, template.format(**part), _with_other(template, calls), question="")["dat"] is False, template


@pytest.mark.parametrize("calls", SOURCES)
@pytest.mark.parametrize("template", TABLE_GOOD + BARE_GOOD)
def test_holes_true_sentences_pass(client, part, template, calls):
    result = _kiem(client, template.format(**part), _with_other(template, calls), question="")
    assert result["dat"] is True, result["loi"]


@pytest.mark.parametrize("calls", [pytest.param(None, id="nguon_ca_doi"), pytest.param(TEAM_CALL, id="nguon_goi_cong_cu_doi")])
@pytest.mark.parametrize("template, ok", [(t, False) for t in TEAM_BAD] + [(t, True) for t in TEAM_GOOD])
def test_team_total_of_both_must_be_team_cell(client, part, template, ok, calls):
    result = _kiem(client, template.format(**part), calls, question="")
    assert result["dat"] is ok, (template, result["loi"])


def test_team_total_without_team_tool_result_is_blocked(client, part):
    # Lượt chỉ có kết quả của một người mà câu nói "của cả đội" ⇒ không có ô tổng đội để so ⇒ chặn (không tha vì số trùng số của người đó)
    assert _kiem(client, "Tổng 8 KPI và thưởng thêm của cả đội là {T} đ.".format(**part), BOTH_CALLS, question="")["dat"] is False


# ---------- kiem_so@29 (19/11): dòng TỔNG GỘP không nêu tên khoản, đứng sau hai phần 8 KPI + thưởng thêm ----------
# Ca dự án bên cạnh báo + người kiểm độc lập phát lại trên @26 / @27 / @28: 6 / 6 cách viết sai đều QUA. Hai câu đầu của GRAND_BAD là NGUYÊN VĂN người
# kiểm gửi (S1, S6 — đỏ trước); số cố định trong câu được test tự kiểm còn khớp công cụ. Các câu khác do người sửa thêm, kỳ vọng ghi trước khi chạy.
NV7 = "NV07"
NV7_QUESTION = "NV07 được những khoản thưởng thêm nào, mỗi khoản bao nhiêu, tổng 8 KPI cộng thưởng thêm là bao nhiêu?"
NV7_CALLS = [{"name": "get_employee_result", "args": {"employee_id": NV7}}, {"name": "get_extra_bonus", "args": {"employee_id": NV7}}]
LIST_SHAPE = "NV07:\n- Lương kinh doanh 8 KPI: 14.540.000 đ\n- Thưởng thêm: 950.000 đ\n- Tổng cộng: {total} đ"
TABLE_SHAPE = "| Khoản | Số tiền |\n|---|---|\n| Lương kinh doanh 8 KPI | 14.540.000 đ |\n| Thưởng thêm | 950.000 đ |\n| Tổng cộng | {total} đ |"
GRAND_BAD = [
    LIST_SHAPE.format(total="14.540.000"),                                                      # S1 — tổng 8 KPI đặt ở dòng tổng gộp
    TABLE_SHAPE.format(total="14.540.000"),                                                     # S6 — cùng lỗi, dạng bảng
    LIST_SHAPE.format(total="950.000"),                                                         # thưởng thêm đặt ở dòng tổng gộp
    "NV07: 8 KPI 14.540.000 đ, thưởng thêm 950.000 đ, tổng cộng 14.540.000 đ.",
    "NV07:\n- Lương kinh doanh 8 KPI: 14.540.000 đ\n- Thưởng thêm: 950.000 đ\nCộng hai phần: 14.540.000 đ.",
    "NV07:\n- Lương kinh doanh 8 KPI: 14.540.000 đ\n- Thưởng thêm: 950.000 đ\n- Tổng thực nhận: 14.540.000 đ",
]
GRAND_GOOD = [
    LIST_SHAPE.format(total="15.490.000"),
    TABLE_SHAPE.format(total="15.490.000"),
    "NV07 — tổng tiền 8 KPI: 14.540.000 đ.",                                                    # chỉ nêu 8 KPI, không có thưởng thêm
    "NV07:\n- Lương kinh doanh 8 KPI: 14.540.000 đ\n- Thưởng thêm: 950.000 đ\nCộng hai phần: 15.490.000 đ.",
    "NV07: 8 KPI 14.540.000 đ, thưởng thêm 950.000 đ, tổng cộng 15.490.000 đ.",
    "NV07 có thưởng thêm, tổng cộng 950.000 đ.",                                                 # không nêu tiền 8 KPI ⇒ "tổng cộng" là tổng thưởng thêm
    # hai nhóm dòng cách nhau một dòng trống: "Tổng cộng" của nhóm thưởng thêm không bị đòi bằng tổng gộp
    "NV07 — lương kinh doanh 8 KPI: 14.540.000 đ.\n\nThưởng thêm của NV07, tổng cộng: 950.000 đ.",
]


def test_nv07_numbers_still_match_the_tools(client):
    extra = client.post("/api/tool/get_extra_bonus", json={"args": {"employee_id": NV7}}).json()
    assert (extra["business_salary_8_kpi"], extra["extra_bonus_total"], extra["total_8_kpi_and_extra"]) == (14_540_000, 950_000, 15_490_000)


@pytest.mark.parametrize("answer", GRAND_BAD)
def test_grand_total_line_with_a_part_instead_of_the_total_is_blocked(client, answer):
    assert _kiem(client, answer, NV7_CALLS, NV7_QUESTION)["dat"] is False, answer


@pytest.mark.parametrize("answer", GRAND_GOOD)
def test_grand_total_line_with_the_true_total_passes(client, answer):
    result = _kiem(client, answer, NV7_CALLS, NV7_QUESTION)
    assert result["dat"] is True, result["loi"]


# ---------- Vòng 2 của @29 (người kiểm độc lập 19/11): test theo LOẠI — mọi cách gọi một con số là "tổng của cả hai phần" ----------
# Loại lỗ: câu trả lời đã nêu TIỀN của cả 8 KPI lẫn thưởng thêm của một người, rồi một NHÃN KIỂU TỔNG không nêu tên khoản mang số của MỘT phần.
# Nhãn × cách trình bày (dòng riêng, cùng dòng, câu riêng sau dấu chấm, ô bảng, in đậm, cách một dòng trống) × phần bị lấy nhầm × hai nhân viên.
# Hai nhãn "Tổng cả hai khoản" và "Tổng gộp" + cách trình bày "câu riêng" là nguyên văn người kiểm (lọt trên bản đầu @29).
TOTAL_LABELS = ["Tổng cộng", "Tổng cả hai khoản", "Tổng gộp", "Cộng hai phần", "Cộng lại", "Gộp cả hai", "Tổng thực nhận", "Tất cả", "Tổng số tiền",
                "Tổng thu nhập", "Tổng"]                                     # "Tổng" đứng một mình: siết ở commit riêng
LAYOUTS = {
    "dong_rieng": "{nv}:\n- Lương kinh doanh 8 KPI: {K} đ\n- Thưởng thêm: {E} đ\n- {label}: {x} đ",
    "in_dam": "{nv}:\n- Lương kinh doanh 8 KPI: {K} đ\n- Thưởng thêm: {E} đ\n**{label}: {x} đ**",
    "cach_dong_trong": "{nv}:\n- Lương kinh doanh 8 KPI: {K} đ\n- Thưởng thêm: {E} đ\n\n**{label}:** {x} đ",
    "cung_dong": "{nv}: 8 KPI {K} đ, thưởng thêm {E} đ, {label} {x} đ.",
    "cau_rieng": "Lương kinh doanh 8 KPI của {nv} là {K} đ; thưởng thêm là {E} đ. {label}: {x} đ.",
    "bang": "| Khoản của {nv} | Số tiền |\n|---|---|\n| Lương kinh doanh 8 KPI | {K} đ |\n| Thưởng thêm | {E} đ |\n| {label} | {x} đ |",
}


@pytest.fixture(scope="module")
def two_people(client) -> dict:
    people = {}
    for code in ("NV03", "NV07"):
        extra = client.post("/api/tool/get_extra_bonus", json={"args": {"employee_id": code}}).json()
        people[code] = {"K": _vn(extra["business_salary_8_kpi"]), "E": _vn(extra["extra_bonus_total"]), "T": _vn(extra["total_8_kpi_and_extra"])}
        assert len(set(people[code].values())) == 3 and extra["extra_bonus_total"] > 0
    return people


def _total_answer(two_people, code: str, layout: str, label: str, part: str):
    numbers = two_people[code]
    calls = [{"name": "get_employee_result", "args": {"employee_id": code}}, {"name": "get_extra_bonus", "args": {"employee_id": code}}]
    question = f"{code} nhận tổng cộng bao nhiêu, gồm 8 KPI và thưởng thêm?"
    return LAYOUTS[layout].format(nv=code, K=numbers["K"], E=numbers["E"], x=numbers[part], label=label), calls, question


@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("part", ["K", "E"])
@pytest.mark.parametrize("layout", list(LAYOUTS))
@pytest.mark.parametrize("label", TOTAL_LABELS)
def test_any_unnamed_total_label_with_a_part_is_blocked(client, two_people, label, layout, part, code):
    answer, calls, question = _total_answer(two_people, code, layout, label, part)
    assert _kiem(client, answer, calls, question)["dat"] is False, answer


@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("layout", list(LAYOUTS))
@pytest.mark.parametrize("label", TOTAL_LABELS)
def test_any_unnamed_total_label_with_the_true_total_passes(client, two_people, label, layout, code):
    answer, calls, question = _total_answer(two_people, code, layout, label, "T")
    result = _kiem(client, answer, calls, question)
    assert result["dat"] is True, (answer, result["loi"])


# Nhãn NÊU RÕ khoản thì số của khoản đó là đúng — không bị đòi bằng tổng gộp
@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("template", [
    "{nv}:\n- Lương kinh doanh 8 KPI: {K} đ\n- Thưởng thêm: {E} đ\n- Tổng 8 KPI: {K} đ",
    "{nv}:\n- Lương kinh doanh 8 KPI: {K} đ\n- Thưởng thêm: {E} đ\n- Tổng thưởng thêm: {E} đ\n- Tổng cộng: {T} đ",
    "{nv}: 8 KPI {K} đ, thưởng thêm {E} đ; tổng lương kinh doanh vẫn là {K} đ.",
    "{nv} — tổng tiền 8 KPI: {K} đ.",
])
def test_named_total_labels_keep_their_own_numbers(client, two_people, template, code):
    numbers = two_people[code]
    calls = [{"name": "get_employee_result", "args": {"employee_id": code}}, {"name": "get_extra_bonus", "args": {"employee_id": code}}]
    result = _kiem(client, template.format(nv=code, **numbers), calls, f"{code} nhận tổng cộng bao nhiêu?")
    assert result["dat"] is True, result["loi"]


# Chữ "tổng" đứng MỘT MÌNH: tổng RIÊNG của nhóm thưởng thêm chỉ qua khi nhãn nêu tên khoản; nhãn trần sau khi đã có tiền cả hai phía ⇒ đòi tổng gộp
@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("template, passes", [
    ("{nv}: tổng lương kinh doanh {K} đ, tổng thưởng thêm {E} đ.", True),
    ("{nv} — lương kinh doanh 8 KPI: {K} đ.\n\nThưởng thêm của {nv} — tổng: {E} đ.", True),      # nhãn nêu "thưởng thêm"
    ("{nv}: 8 KPI {K} đ, thưởng thêm {E} đ, tổng {T} đ.", True),
    ("{nv} — lương kinh doanh 8 KPI: {K} đ.\n\nThưởng thêm: {E} đ.\n\nTổng: {E} đ.", False),      # giới hạn ghi rõ: nhãn trần ⇒ đòi tổng gộp
])
def test_bare_total_word(client, two_people, template, passes, code):
    numbers = two_people[code]
    calls = [{"name": "get_employee_result", "args": {"employee_id": code}}, {"name": "get_extra_bonus", "args": {"employee_id": code}}]
    assert _kiem(client, template.format(nv=code, **numbers), calls, f"{code} nhận tổng cộng bao nhiêu?")["dat"] is passes


# ---------- kiem_so@30 (19/11): MỘT KHOẢN LẺ nhắc lại dưới nhãn KHÔNG nêu tên khoản (người kiểm độc lập: "thôi dò theo chữ của nhãn") ----------
# Loại lỗ: câu trả lời đã nêu tiền của CẢ HAI phía (8 KPI và thưởng thêm) của một người, rồi một số tiền đứng sau BẰNG đúng số 8 KPI (K) hoặc đúng số
# thưởng thêm của người đó mà nhãn / vế của nó KHÔNG nêu tên khoản đó ⇒ người đọc hiểu như một khoản khác / số tổng. Nhãn ở đây cố ý KHÔNG có chữ
# tổng / cộng / gộp (lưới @29 đã phủ nhãn có chữ tổng); thêm "Tổng thu nhập tháng" vì số đứng ngay sau chữ "tháng" từng bị che như "tháng 10".
# Số lấy từ công cụ lúc chạy (fixture `two_people`).
PART_LABELS = ["Thành tiền:", "Cả thảy {nv} nhận", "⇒ Cả thảy {nv} nhận", "Số tiền nhận cuối cùng:", "Thu nhập tháng", "Tổng thu nhập tháng",
               "Được nhận:", "Chốt lại:", "Con số cuối:", "⇒"]
PART_BASE = "{nv}:\n- Lương kinh doanh 8 KPI: {K} đ\n- Thưởng thêm: {E} đ"
PART_LAYOUTS = {
    "dong_rieng": PART_BASE + "\n- {label} {x} đ",
    "cung_dong": "{nv}: 8 KPI {K} đ, thưởng thêm {E} đ, {label} {x} đ.",
    "cau_rieng": "Lương kinh doanh 8 KPI của {nv} là {K} đ; thưởng thêm là {E} đ. {label} {x} đ.",
    "doan_rieng": PART_BASE + "\n\n{label} {x} đ.",
    "bang": "| Khoản của {nv} | Số tiền |\n|---|---|\n| Lương kinh doanh 8 KPI | {K} đ |\n| Thưởng thêm | {E} đ |\n| {cell} | {x} đ |",
}


def _calls_of(code: str) -> list:
    return [{"name": "get_employee_result", "args": {"employee_id": code}}, {"name": "get_extra_bonus", "args": {"employee_id": code}}]


def _part_answer(two_people, code: str, layout: str, label: str, part: str) -> str:
    numbers = two_people[code]
    label = label.format(nv=code)
    return PART_LAYOUTS[layout].format(nv=code, K=numbers["K"], E=numbers["E"], x=numbers[part], label=label, cell=label.rstrip(":"))


@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("part", ["K", "E"])
@pytest.mark.parametrize("layout", list(PART_LAYOUTS))
@pytest.mark.parametrize("label", PART_LABELS)
def test_part_repeated_under_unnamed_label_is_blocked(client, two_people, label, layout, part, code):
    answer = _part_answer(two_people, code, layout, label, part)
    assert _kiem(client, answer, _calls_of(code), f"{code} nhận tổng cộng bao nhiêu?")["dat"] is False, answer


@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("layout", list(PART_LAYOUTS))
@pytest.mark.parametrize("label", PART_LABELS)
def test_true_total_under_unnamed_label_passes(client, two_people, label, layout, code):
    answer = _part_answer(two_people, code, layout, label, "T")
    result = _kiem(client, answer, _calls_of(code), f"{code} nhận tổng cộng bao nhiêu?")
    assert result["dat"] is True, (answer, result["loi"])


# Nhắc lại K / E mà nhãn / vế NÊU ĐÚNG tên khoản ⇒ qua; bốn câu cuối là câu đúng bị chặn oan trước @30 vì "8 KPI" trần không được nhận là tên khoản
@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("template", [
    PART_BASE + "\n\nLương kinh doanh vẫn là {K} đ.",
    PART_BASE + "\n\nRiêng thưởng thêm {E} đ.",
    PART_BASE + "\n\nNhắc lại: 8 KPI {K} đ và thưởng thêm {E} đ.",
    # (câu "{K} đ là lương kinh doanh 8 KPI, {E} đ là thưởng thêm." KHÔNG ở đây: bị chặn oan có sẵn từ trước @30 — lớp khoản gán tên đứng sau số trước
    # cho số kế; đo trên @29: chặn. Chữa ở lớp khoản làm đỏ test_kiem_so.py::test_number_from_question_is_exempt_only_in_item_layer ⇒ nhóm việc rào sau)
    PART_BASE + "\n- Tổng 8 KPI: {K} đ\n- Tổng thưởng thêm: {E} đ\n- Thành tiền: {T} đ",
    "{nv}: 8 KPI {K} đ, thưởng thêm {E} đ; lương kinh doanh vẫn là {K} đ, riêng thưởng thêm {E} đ.",
    "{nv}: 8 KPI {K} đ và thưởng thêm {E} đ, tổng cộng {T} đ.",
    "{nv}: 8 KPI {K} đ và thưởng thêm {E} đ.",
    "{nv}: 8 KPI {K} + thưởng thêm {E} = {T}.",
    "Tổng cộng 8 KPI của {nv} là {K} (chưa cộng thưởng thêm {E}).",
])
def test_part_repeated_with_its_own_name_passes(client, two_people, template, code):
    result = _kiem(client, template.format(nv=code, **two_people[code]), _calls_of(code), f"{code} nhận tổng cộng bao nhiêu?")
    assert result["dat"] is True, result["loi"]


# Số KHÔNG có mã NV (người được hỏi chỉ có trong câu hỏi): người = ai có cả hai phía đã được nêu tiền ở phía trước
@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("part, ok", [("K", False), ("E", False), ("T", True)])
def test_part_repeated_without_employee_code(client, two_people, part, ok, code):
    numbers = two_people[code]
    answer = f"- Lương kinh doanh 8 KPI: {numbers['K']} đ\n- Thưởng thêm: {numbers['E']} đ\n\nThành tiền: {numbers[part]} đ."
    result = _kiem(client, answer, _calls_of(code), f"Tôi là {code}, tôi nhận bao nhiêu?")
    assert result["dat"] is ok, (answer, result["loi"])


def test_money_after_month_word_must_have_a_source(client, two_people):
    # "tháng 15.490.000" từng bị che như "tháng 10" ở CẢ hai lớp ⇒ số bịa đứng sau "tháng" không lớp nào xét
    numbers = two_people["NV07"]
    answer = f"NV07: thu nhập tháng {numbers['T']} đ."
    assert _kiem(client, answer, _calls_of("NV07"), "NV07 nhận bao nhiêu?")["dat"] is True
    assert _kiem(client, answer.replace(numbers["T"], "15.491.000"), _calls_of("NV07"), "NV07 nhận bao nhiêu?")["dat"] is False
    assert _kiem(client, "NV07: lương tháng 10 là " + numbers["T"] + " đ.", _calls_of("NV07"), "NV07 nhận bao nhiêu?")["dat"] is True


# ---------- kiem_so@30 vòng 2 (20/11): nhãn NÊU CẢ HAI phía (8 KPI / lương kinh doanh VÀ thưởng thêm) mà số chỉ là MỘT khoản lẻ ----------
# Hồi quy người kiểm độc lập tìm ở vòng 1: "- 8 KPI và thưởng thêm: K đ" — bản 29 chặn (nhờ chữ số của "8 KPI" cắt nhãn), bản 30 cho qua vì nhãn có
# tên khoản của K. Luật theo LOẠI: nhãn / vế nêu cả hai phía ⇒ người đọc hiểu là tổng hai khoản ⇒ số phải là ô tổng gộp; bằng đúng một khoản lẻ ⇒ chặn,
# KỂ CẢ khi chưa nêu riêng hai phía ở phía trước. Không dò theo chữ nối ("và", ",", "+", "cùng", "kèm") — chỉ xét nhãn có tên của cả hai phía.
BOTH_LABELS = ["8 KPI và thưởng thêm:", "Lương kinh doanh 8 KPI, thưởng thêm:", "Lương kinh doanh 8 KPI + thưởng thêm:", "8 KPI cùng thưởng thêm là",
               "Thưởng thêm và 8 KPI:", "Lương kinh doanh 8 KPI kèm thưởng thêm", "Lương kinh doanh và thưởng thêm:"]
BOTH_LAYOUTS = {
    "sau_hai_phan": PART_BASE + "\n- {label} {x} đ",
    "khong_neu_truoc": "{nv}:\n- {label} {x} đ",
    "cau": "{nv} — {label} {x} đ.",
    "bang": "| Khoản của {nv} | Số tiền |\n|---|---|\n| {cell} | {x} đ |",
}


def _both_answer(two_people, code: str, layout: str, label: str, part: str) -> str:
    numbers = two_people[code]
    return BOTH_LAYOUTS[layout].format(nv=code, K=numbers["K"], E=numbers["E"], x=numbers[part], label=label, cell=label.rstrip(":"))


@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("part", ["K", "E"])
@pytest.mark.parametrize("layout", list(BOTH_LAYOUTS))
@pytest.mark.parametrize("label", BOTH_LABELS)
def test_single_part_under_label_naming_both_sides_is_blocked(client, two_people, label, layout, part, code):
    answer = _both_answer(two_people, code, layout, label, part)
    assert _kiem(client, answer, _calls_of(code), f"{code} nhận tổng cộng bao nhiêu?")["dat"] is False, answer


@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("layout", list(BOTH_LAYOUTS))
@pytest.mark.parametrize("label", BOTH_LABELS)
def test_true_total_under_label_naming_both_sides_passes(client, two_people, label, layout, code):
    answer = _both_answer(two_people, code, layout, label, "T")
    result = _kiem(client, answer, _calls_of(code), f"{code} nhận tổng cộng bao nhiêu?")
    assert result["dat"] is True, (answer, result["loi"])


# Câu ĐÚNG có tên của cả hai phía quanh một khoản lẻ ⇒ phải qua: tên kia đứng sau lời LOẠI TRỪ ("không tính / chưa gồm thưởng thêm" = đang nói về 8 KPI),
# hai số kể "lần lượt", tên khoản đứng sau số qua chữ "từ". Hai câu đầu là chặn oan MỚI của bản 30 người kiểm ghi ở vòng 1.
@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("template", [
    PART_BASE + "\n- Nhận về: {T} đ (trong đó {K} đ từ 8 KPI)",
    PART_BASE + "\n\nNếu không tính thưởng thêm, {nv} nhận {K} đ.",
    "{nv}: lương kinh doanh 8 KPI (chưa gồm thưởng thêm): {K} đ; thưởng thêm: {E} đ.",
    PART_BASE + "\n\nChưa kể thưởng thêm thì {nv} được {K} đ.",
    "{nv}: 8 KPI và thưởng thêm lần lượt là {K} đ và {E} đ.",
])
def test_single_part_with_other_side_excluded_passes(client, two_people, template, code):
    result = _kiem(client, template.format(nv=code, **two_people[code]), _calls_of(code), f"{code} nhận tổng cộng bao nhiêu?")
    assert result["dat"] is True, result["loi"]


# Lời loại trừ không được thành đường lách: "không tính thưởng thêm" mà số lại là chính thưởng thêm / "không tính 8 KPI" mà số là 8 KPI ⇒ chặn
@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("template", [
    PART_BASE + "\n\nNếu không tính thưởng thêm, {nv} nhận {E} đ.",
    PART_BASE + "\n\nChưa kể lương kinh doanh 8 KPI thì {nv} được {K} đ.",
])
def test_excluded_side_number_is_blocked(client, two_people, template, code):
    answer = template.format(nv=code, **two_people[code])
    assert _kiem(client, answer, _calls_of(code), f"{code} nhận tổng cộng bao nhiêu?")["dat"] is False, answer


# ---------- kiem_so@30 vòng 3 (20/11, M2 người kiểm): lời loại trừ BỊ PHỦ ĐỊNH không phải lời loại trừ ----------
# "không bỏ / chưa trừ / chẳng bớt thưởng thêm" nghĩa là CÓ gồm khoản đó — vòng 2 đọc thành "loại trừ thưởng thêm" rồi bỏ lớp kiểm khoản ⇒ số một khoản
# lẻ dưới nhãn như vậy lọt. Theo LOẠI: chữ phủ định × động từ loại trừ × khoản bị nhắc × cách trình bày.
NEGATIONS = ["không", "chưa", "chẳng", "không hề", "chả"]   # không dùng "không cần": chữ "cần" làm số thành số CÓ ĐIỀU KIỆN (luật mốc) — loại khác
REMOVAL_VERBS = ["bỏ", "trừ", "bỏ khoản", "trừ đi", "ngoài", "loại"]
NEGATED_REMOVAL_LAYOUTS = [
    ("- Tổng cộng ({neg} {verb} thưởng thêm): {x} đ", "K"),
    ("- Nhận về (đã cộng, {neg} {verb} thưởng thêm): {x} đ", "K"),
    ("⇒ {nv} nhận, {neg} {verb} thưởng thêm, là {x} đ.", "K"),
    ("- Cả hai khoản, {neg} {verb} lương kinh doanh 8 KPI: {x} đ", "E"),
    ("- Nhận về ({neg} {verb} lương kinh doanh): {x} đ", "E"),
]


@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("layout, part", NEGATED_REMOVAL_LAYOUTS)
@pytest.mark.parametrize("verb", REMOVAL_VERBS)
@pytest.mark.parametrize("neg", NEGATIONS)
def test_negated_removal_is_not_an_exclusion(client, two_people, neg, verb, layout, part, code):
    numbers = two_people[code]
    answer = PART_BASE.format(nv=code, **numbers) + "\n" + layout.format(nv=code, neg=neg, verb=verb, x=numbers[part])
    assert _kiem(client, answer, _calls_of(code), f"{code} nhận tổng cộng bao nhiêu?")["dat"] is False, answer


@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("line", [
    "- Nhận về (không phải là chưa tính thưởng thêm): {K} đ",       # phủ định của "chưa tính" = đã tính ⇒ K là sai
    "- Nhận về (đâu phải không gồm thưởng thêm): {K} đ",
    "- Riêng 8 KPI, không tính thưởng thêm: {E} đ",                  # nói về 8 KPI mà số là thưởng thêm
])
def test_exclusion_cannot_launder_the_other_part(client, two_people, line, code):
    numbers = two_people[code]
    answer = PART_BASE.format(nv=code, **numbers) + "\n" + line.format(**numbers)
    assert _kiem(client, answer, _calls_of(code), f"{code} nhận tổng cộng bao nhiêu?")["dat"] is False, answer


@pytest.mark.parametrize("code", ["NV03", "NV07"])
def test_excluded_part_under_two_names_is_blocked_without_prior_parts(client, two_people, code):
    answer = f"{code}:\n- Riêng 8 KPI, không tính thưởng thêm: {two_people[code]['E']} đ"
    assert _kiem(client, answer, _calls_of(code), f"{code} nhận tổng cộng bao nhiêu?")["dat"] is False, answer


# Hai câu ĐÚNG người kiểm thấy bị chặn oan ở vòng 2
@pytest.mark.parametrize("code", ["NV03", "NV07"])
@pytest.mark.parametrize("template", [
    "{K} đ là lương kinh doanh 8 KPI, {E} đ là thưởng thêm.",       # không có mã đứng trước — người được hỏi ở câu hỏi
    PART_BASE + "\n- Riêng 8 KPI, không tính thưởng thêm: {K} đ",
    "{nv}:\n- Riêng 8 KPI, không tính thưởng thêm: {K} đ\n- Thưởng thêm: {E} đ",
])
def test_true_single_part_sentences_from_round_two_pass(client, two_people, template, code):
    result = _kiem(client, template.format(nv=code, **two_people[code]), _calls_of(code), f"Tôi là {code}, tôi nhận bao nhiêu?")
    assert result["dat"] is True, result["loi"]
