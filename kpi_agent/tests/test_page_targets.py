"""2a-2 nấc 3 — tách chỉ tiêu Kokomi / Meat từ trang theo dõi (.html) tải lên: `backend/page_targets.py`.

Trang thật tháng 10 (file trong thư mục `…_files`) + trang nhỏ tự dựng cho các ca lệch khuôn. Trang thật chứa cả số thực hiện / % / hạng — test chỉ so phần
chỉ tiêu với hai file JSON đang dùng, và chứng minh sửa phần còn lại không làm đổi kết quả.
"""
import copy
import json
from pathlib import Path

import pytest

from kpi_agent.backend import page_targets as pt

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "sample_data/thang_10/saved_resource.html"
OUTER_PAGE = ROOT / "sample_data/thang_10/Tracking KPI Lam Phương.html"
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
KOKOMI_FILE, MEAT_FILE = ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json", ROOT / "kpi_agent/data/chi_tieu_meat_3110.json"
needs_data = pytest.mark.skipif(not all(path.exists() for path in (PAGE, OUTER_PAGE, RAW, TARGETS, KOKOMI_FILE, MEAT_FILE)),
                                reason="không có dữ liệu thật trên máy này")
PERIOD = "2026-10"


@pytest.fixture(scope="module")
def real():
    """(byte trang thật, hằng dữ liệu đã đọc, vị trí hằng trong chữ, {mã: họ tên}, đội)."""
    from kpi_agent.core.load_data import load_targets
    from kpi_agent.core.load_raw import raw_employee_names
    body = PAGE.read_bytes()
    text = body.decode("utf-8")
    start = pt.CONSTANT.search(text).end()
    data, length = json.JSONDecoder().raw_decode(text[start:])
    team = {str(code).strip() for code in load_targets(TARGETS)["employees"]}
    return {"body": body, "text": text, "data": data, "span": (start, start + length), "names": raw_employee_names(str(RAW)), "team": team}


def rebuilt(real, change) -> bytes:
    """Trang thật với hằng dữ liệu đã sửa bằng `change(data)` — phần còn lại của trang giữ nguyên."""
    data = copy.deepcopy(real["data"])
    change(data)
    start, end = real["span"]
    return (real["text"][:start] + json.dumps(data, ensure_ascii=False) + real["text"][end:]).encode("utf-8")


def run(real, body: bytes) -> dict:
    return pt.extract(body, PERIOD, real["names"], real["team"])


@needs_data
def test_real_page_gives_exactly_the_two_files_in_use(real):
    out = run(real, real["body"])
    assert out[pt.KOKOMI] == json.loads(KOKOMI_FILE.read_text(encoding="utf-8"))["targets"]           # từng người, tới từng đồng
    assert out[pt.MEAT] == json.loads(MEAT_FILE.read_text(encoding="utf-8"))["targets"]               # 4 khối 07, 7.1, 7.2, 7.3
    assert set(out) == {pt.KOKOMI, pt.MEAT} and all(len(block) == 14 for block in [out[pt.KOKOMI], *out[pt.MEAT].values()])
    assert all(isinstance(value, int) for block in [out[pt.KOKOMI], *out[pt.MEAT].values()] for value in block.values())


@needs_data
def test_answer_cells_and_everything_else_on_the_page_do_not_matter(real):
    base = pt.target_files(run(real, real["body"]))

    def scramble(data):
        for item in data["bon"]:
            item["title"], item["tot"], item["dec"] = "đổi", {"a": 1}, "x"
            for row in item["rows"]:
                for key in ("a", "p", "k", "d", "m"):          # thực hiện, %, hạng… = đáp án so mù
                    row[key] = 123456.789
        data["upd"] = "01/10/2026"                              # ngày khác TRONG cùng tháng: không vào phép tính
        data["bon"] = [item for item in data["bon"] if item["short"] in pt.PAGE_ITEMS]      # bỏ 5 khoản không dùng
        for key in [key for key in data if key not in ("upd", "bon")]:
            del data[key]                                       # bỏ hẳn mọi khối khác của hằng
    assert pt.target_files(run(real, rebuilt(real, scramble))) == base


@needs_data
def test_changing_one_target_cell_changes_exactly_that_person_and_item(real):
    base = run(real, real["body"])

    def bump(data):
        row = next(item for item in data["bon"] if item["short"] == "Meat N2")["rows"][3]
        row["t"] = row["t"] + 1.5                               # thêm 1,5 triệu đồng
    changed = run(real, rebuilt(real, bump))
    assert changed[pt.KOKOMI] == base[pt.KOKOMI] and {key for key in base[pt.MEAT] if changed[pt.MEAT][key] != base[pt.MEAT][key]} == {"7.2"}
    moved = {code: changed[pt.MEAT]["7.2"][code] - base[pt.MEAT]["7.2"][code] for code in base[pt.MEAT]["7.2"] if changed[pt.MEAT]["7.2"][code] != base[pt.MEAT]["7.2"][code]}
    assert list(moved.values()) == [1_500_000]


def refused(real, body: bytes, period: str = PERIOD) -> pt.PageProblem:
    with pytest.raises(pt.PageProblem) as caught:
        pt.extract(body, period, real["names"], real["team"])
    return caught.value


@needs_data
def test_page_of_another_month_or_without_a_readable_date_is_refused(real):
    assert refused(real, real["body"], "2026-11").code == "trang_khac_ky"
    assert refused(real, rebuilt(real, lambda data: data.update(upd="30/11/2026"))).code == "trang_khac_ky"
    for bad in ("2026-10-31", "31-10-2026", "", "31/13/2026", None, 20261031):
        assert refused(real, rebuilt(real, lambda data, bad=bad: data.update(upd=bad))).code == "trang_khong_ro_ky", bad


@needs_data
def test_outer_page_and_non_pages_get_their_own_codes(real):
    outer = refused(real, OUTER_PAGE.read_bytes())
    assert outer.code == "trang_ngoai_khong_co_du_lieu" and "_files" in pt.refusal_text(outer)     # lời thường chỉ đúng file cần tải
    assert refused(real, RAW.read_bytes()[:200_000]).code == "trang_lech_khuon"                      # byte .xlsx ⇒ không phải chữ
    assert refused(real, b"<html><body>khong co gi</body></html>").code == "trang_lech_khuon"
    assert refused(real, b" " * (pt.MAX_PAGE_BYTES + 1)).code == "trang_qua_lon"


def text_with(real, constant_text: str) -> bytes:
    start, end = real["span"]
    return (real["text"][:start] + constant_text + real["text"][end:]).encode("utf-8")


@needs_data
def test_reader_is_closed_on_four_sides(real):
    start, end = real["span"]
    constant = real["text"][start:end]
    twice = (real["text"] + "\n<!-- const D=" + constant + " -->").encode("utf-8")                 # bản thứ hai nằm trong chú thích HTML
    assert (refused(real, twice).code, refused(real, twice).detail["ly_do"]) == ("trang_lech_khuon", "hang_du_lieu_lap")
    duplicate_key = text_with(real, constant.replace('"done":', '"bon":[],"done":', 1))             # khoá "bon" có hai lần trong hằng
    assert refused(real, duplicate_key).detail["ly_do"] == "khoa_lap"
    for token in ("NaN", "Infinity", "-Infinity"):
        assert refused(real, text_with(real, constant.replace('"done":', f'"x":{token},"done":', 1))).detail["ly_do"] == "so_khong_huu_han", token
    deep = text_with(real, constant.replace('"done":', '"x":' + "[" * 100_000 + "]" * 100_000 + ',"done":', 1))
    assert refused(real, deep).code == "trang_lech_khuon"                                            # lồng quá sâu ⇒ từ chối có mã, không lỗi trần
    assert refused(real, text_with(real, constant[: len(constant) // 2])).detail["ly_do"] == "hang_du_lieu_khong_doc_duoc"

    def huge(data):
        next(item for item in data["bon"] if item["short"] == "Mì Kokomi 90")["rows"][0]["t"] = 1e300
    assert refused(real, rebuilt(real, huge)).detail["ly_do"] == "chi_tieu_ngoai_khoang"
    # 1e999 ở MỘT ô chỉ tiêu được dùng (khoản Kokomi) ⇒ dừng; ở ô không dùng thì không vào kết quả (ca "mọi thứ khác không quan trọng")
    import re
    at = constant.index('"Mì Kokomi 90"')
    with_huge = constant[:at] + re.sub(r'"t":\s*[-0-9.eE+]+', '"t":1e999', constant[at:], count=1)
    assert with_huge != constant and refused(real, text_with(real, with_huge)).detail["ly_do"] == "chi_tieu_ngoai_khoang"


@needs_data
@pytest.mark.parametrize("change, code, reason", [
    (lambda data: data.pop("bon"), "trang_lech_khuon", "thieu_khoi_thuong_them"),
    (lambda data: data["bon"].append(copy.deepcopy(next(item for item in data["bon"] if item["short"] == "Meat N1"))), "trang_lech_khuon", "so_khoan_khac_mot"),
    (lambda data: data["bon"].remove(next(item for item in data["bon"] if item["short"] == "Mì Kokomi 90")), "trang_lech_khuon", "so_khoan_khac_mot"),
    (lambda data: next(item for item in data["bon"] if item["short"] == "Meat N3").update(unit="thùng"), "trang_lech_khuon", "khoan_sai_don_vi_hoac_thieu_dong"),
    (lambda data: next(item for item in data["bon"] if item["short"] == "Meat N3")["rows"][0].pop("t"), "trang_lech_khuon", "dong_thieu_ten_hoac_chi_tieu"),
    (lambda data: next(item for item in data["bon"] if item["short"] == "Meat N3")["rows"][0].update(t="12.5"), "trang_lech_khuon", "chi_tieu_khong_phai_so"),
    (lambda data: next(item for item in data["bon"] if item["short"] == "Meat N3")["rows"][0].update(t=0), "trang_lech_khuon", "chi_tieu_ngoai_khoang"),
    (lambda data: next(item for item in data["bon"] if item["short"] == "Meat N3")["rows"][0].update(t=-3.5), "trang_lech_khuon", "chi_tieu_ngoai_khoang"),
    (lambda data: next(item for item in data["bon"] if item["short"] == "Meat N3")["rows"][0].update(t=1.0000001), "trang_lech_khuon", "chi_tieu_ngoai_khoang"),
    (lambda data: next(item for item in data["bon"] if item["short"] == "Meat N3")["rows"].pop(), "trang_lech_doi", "danh_sach_nguoi_khac_doi"),
    (lambda data: next(item for item in data["bon"] if item["short"] == "Meat N3")["rows"][0].update(
        n=next(item for item in data["bon"] if item["short"] == "Meat N3")["rows"][1]["n"]), "trang_lech_doi", "ten_khong_ghep_duoc_dung_mot_nguoi"),
])
def test_page_off_the_mould_stops_with_a_code(real, change, code, reason):
    problem = refused(real, rebuilt(real, change))
    assert (problem.code, problem.detail["ly_do"]) == (code, reason)


@needs_data
def test_refusals_never_repeat_text_from_the_page(real):
    planted = '<script>alert(1)</script>\x07Nguyễn Văn Lạ'

    def plant(data):
        next(item for item in data["bon"] if item["short"] == "Mì Kokomi 90")["rows"][0]["n"] = planted
    problem = refused(real, rebuilt(real, plant))
    text = pt.refusal_text(problem)
    assert problem.code == "trang_lech_doi" and not any(piece in text + repr(problem.detail) for piece in ("script", "alert", "\x07", "Lạ"))
    real_names = {row["n"] for item in real["data"]["bon"] for row in item["rows"]}
    every_text = [pt.refusal_text(pt.PageProblem(code, so_nguoi_trang=13, ly_do="x")) for code in pt.REFUSALS]
    assert not any(name in text for name in real_names for text in every_text)


@needs_data
def test_names_must_match_whole_trailing_words_of_exactly_one_person(real):
    names = {"A1": "Nguyễn Thị Lan Anh", "A2": "Trần Văn Anh", "A3": "Lê Lan"}
    assert pt._code_of("lan  ANH", names) == "A1"                # chuẩn hoá hoa thường + dấu cách
    assert pt._code_of("Anh", names) is None                     # khớp hai người ⇒ không ghép
    assert pt._code_of("Lan An", names) is None and pt._code_of("an Anh", names) is None      # không ghép gần đúng / nửa chữ
    assert pt._code_of("", names) is None and pt._code_of("Lan", names) == "A3"


@needs_data
def test_target_files_have_the_shapes_the_core_reads_and_stable_bytes(real):
    from kpi_agent.core.compute_extra_bonus import load_extra_rules, load_item_targets
    files = pt.target_files(run(real, real["body"]))
    assert files == pt.target_files(run(real, real["body"]))                                         # cùng chỉ tiêu ⇒ cùng byte ⇒ cùng sha
    rules = load_extra_rules(ROOT / "kpi_agent/config/extra_bonus_2026_10.yaml")
    fixed = load_item_targets(rules, resolve=lambda name: ROOT / name)
    folder_files = {"kpi_agent/data/chi_tieu_kokomi_3110.json": files[pt.KOKOMI], "kpi_agent/data/chi_tieu_meat_3110.json": files[pt.MEAT]}
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        paths = {}
        for name, content in folder_files.items():
            paths[name] = Path(folder) / Path(name).name
            paths[name].write_bytes(content)
        assert load_item_targets(rules, resolve=lambda name: paths[name]) == fixed                   # lõi đọc file tách ra đúng như đọc file đang dùng


@needs_data
def test_two_team_members_sharing_the_trailing_words_stop_the_page(real):
    # Trang chỉ ghi 2 chữ cuối của họ tên: hai người trong đội trùng 2 chữ cuối ⇒ tên trang khớp HAI người ⇒ dừng, không chọn bừa một người
    first_name = real["data"]["bon"][5]["rows"][0]["n"]
    owner = pt._code_of(first_name, real["names"])
    other = next(code for code in sorted(real["team"]) if code != owner)
    names = {**real["names"], other: "Họ Khác " + first_name}
    with pytest.raises(pt.PageProblem) as caught:
        pt.extract(real["body"], PERIOD, names, real["team"])
    assert (caught.value.code, caught.value.detail["ly_do"]) == ("trang_lech_doi", "ten_khong_ghep_duoc_dung_mot_nguoi")
    assert first_name not in pt.refusal_text(caught.value)
