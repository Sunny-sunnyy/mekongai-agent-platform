"""`GET /api/system/ky/{kỳ}` + `GET /api/system/ky` — kỳ này đã có / thiếu gì (trang Dữ liệu của bộ Capitol).

Ca thật: backend bật đường RAW tháng 10 thật ⇒ kỳ 2026-10 đủ 5 mục; kỳ 2026-11 (chưa có luật, chưa có file) ⇒ đúng danh sách thiếu; tải một file đơn
hàng tháng 10 khác ⇒ mục raw chuyển sang bản đã tải. Ca xấu: kỳ sai dạng ⇒ 422 và KHÔNG chạm phần đọc trạng thái; không khoá ⇒ 401.
Phản hồi không có số nào (không tiền), không mã / tên nhân viên, không đường dẫn tuyệt đối.
"""
import io
import json
import re
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from kpi_agent.backend import period_status

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
MASK = ROOT / "kpi_agent/data/ma_che_ten.json"
NEEDED = [RAW, ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", MASK, ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json",
          ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
KEYS = ["raw", "chi_tieu", "tuyen", "luat_chinh", "luat_phu"]


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    return tmp_path_factory.mktemp("ky_co_gi")


@pytest.fixture(scope="module")
def client(folder):
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        mp.setattr(main, "UPLOAD_DIR", folder / "uploads")     # không ghi file thử vào kpi_agent/data/uploads thật
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


def _strings_and_numbers(value) -> tuple[list[str], list]:
    """Mọi chuỗi (cả khoá) và mọi giá trị SỐ trong một JSON."""
    if isinstance(value, dict):
        parts = [_strings_and_numbers(item) for item in value.values()]
        return list(value) + [text for texts, _ in parts for text in texts], [number for _, numbers in parts for number in numbers]
    if isinstance(value, list):
        parts = [_strings_and_numbers(item) for item in value]
        return [text for texts, _ in parts for text in texts], [number for _, numbers in parts for number in numbers]
    if isinstance(value, str):
        return [value], []
    return [], [value] if isinstance(value, (int, float)) and not isinstance(value, bool) else []


MONEY_KEYS = ("tam_kpi", "thuong_them", "gia_vi")   # 20/11: đường này trả thêm BA TỔNG của bản cho trang Dữ liệu (quyết) — chỉ tổng, không theo người


def _assert_nothing_private(body: dict, folder: Path) -> None:
    for key in [key for key in MONEY_KEYS if key in body]:                        # ba ô tổng (chỉ có khi bản thuộc kỳ được hỏi): đúng hình, không dòng theo người
        assert set(body[key]) <= {"tong", "trang_thai", "ly_do"} and (body[key]["tong"] is None or isinstance(body[key]["tong"], int)), body[key]
    texts, numbers = _strings_and_numbers({key: value for key, value in body.items() if key not in MONEY_KEYS})
    assert numbers == []                                                          # NGOÀI ba ô tổng không ô nào là số ⇒ không tiền / % / số đếm nào khác
    joined = "\n".join(texts)
    assert not re.search(r"[0-9]{1,3}(\.[0-9]{3})+", joined)                       # không số viết kiểu tiền (1.234.567)
    assert not re.search(r"NV[0-9]{2}", joined)                                   # không mã che
    mask_texts, _ = _strings_and_numbers(json.loads(MASK.read_text(encoding="utf-8")))
    assert not [text for text in mask_texts if len(text) >= 4 and text in joined]  # không mã thật / tên thật nào của bảng che
    assert str(ROOT) not in joined and str(folder) not in joined and not [text for text in texts if text.startswith("/")]   # không đường dẫn tuyệt đối


def test_base_period_has_everything(client, folder):
    body = client.get("/api/system/ky/2026-10", headers=KEY).json()
    assert [item["key"] for item in body["items"]] == KEYS
    assert {item["key"]: item["state"] for item in body["items"]} == dict.fromkeys(KEYS, "co")
    assert body["ready"] is True and body["missing"] == [] and body["period"] == body["base_period"] == "2026-10" and body["data_source"] == "raw"
    assert body["uploads_survive_restart"] is True
    raw, targets, routes, rules, extra = body["items"]
    meta = client.get("/api/meta").json()
    assert raw["file"] == "sample_data/thang_10/RAW DATA.xlsx" and raw["from_upload"] is False and raw["checked"] is None
    assert raw["data_version"] == meta["data_version"] and raw["cutoff"] == "2026-10-31"
    assert raw["sha256_8"] == meta["files_sha256"]["RAW DATA.xlsx"][:8] and targets["sha256_8"] == meta["files_sha256"]["Chi tieu NVBH.xlsx"][:8]
    assert routes["file"] == "sample_data/thang_10/mcp_trang_sep_3110.json" and routes["sha256_8"] == meta["files_sha256"]["mcp_trang_sep_3110.json"][:8]
    assert rules["rule_version"] == meta["rule_version"] and rules["file"] == "kpi_agent/config/rules_2026_10_raw.yaml"
    assert extra["parts"] == {"thuong_them": "co", "gia_vi_1pct": "co", "hoi_vien": "co"}
    assert all(item["next_step"] is None and item["uploaded_by"] is None for item in body["items"])
    _assert_nothing_private(body, folder)


def test_period_without_rules_or_data_lists_exactly_what_is_missing(client, folder):
    body = client.get("/api/system/ky/2026-11", headers=KEY).json()
    assert {item["key"]: item["state"] for item in body["items"]} == {
        "raw": "thieu", "chi_tieu": "dung_cua_ky_khac", "tuyen": "dung_cua_ky_khac", "luat_chinh": "thieu", "luat_phu": "thieu"}
    assert body["ready"] is False and body["missing"] == KEYS and body["base_period"] == "2026-10"
    rules = body["items"][3]
    assert "rules_2026_11_raw.yaml" in rules["detail"] and rules["file"] is None and "rule_version" not in rules
    assert body["items"][4]["parts"] == {"thuong_them": "thieu", "gia_vi_1pct": "thieu", "hoi_vien": "thieu"}
    assert all(item["next_step"] for item in body["items"])                       # mục nào chưa "co" cũng có một câu việc kế
    _assert_nothing_private(body, folder)


# Ký tự xuống dòng không tới được hàm (đã thử 20/11): ở CUỐI thì bộ định tuyến bỏ nó ⇒ hàm nhận đúng "2026-10"; ở GIỮA thì bộ định tuyến trả 404
# (ca riêng bên dưới) — cả hai đều không đọc file nào.
@pytest.mark.parametrize("bad", ["2026-13", "2026-1", "2026-00", "abc", "..%2Fx", "2026-10%2F..%2F..%2Fx", "2026-10x", "２０２６-10"])
def test_bad_period_is_422_before_any_state_is_read(client, monkeypatch, bad):
    def must_not_run(*args, **kwargs):
        raise AssertionError("kỳ sai dạng mà vẫn đi tới phần đọc trạng thái / file")
    monkeypatch.setattr(period_status, "period_status", must_not_run)
    response = client.get(f"/api/system/ky/{bad}", headers=KEY)
    assert response.status_code == 422 and response.json() == {"refused": "kỳ phải có dạng YYYY-MM (vd 2026-10)"}


def test_newline_inside_period_never_reaches_the_reader(client, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("kỳ có ký tự xuống dòng mà vẫn đi tới phần đọc trạng thái / file")
    monkeypatch.setattr(period_status, "period_status", must_not_run)
    assert client.get("/api/system/ky/2026-10%0Ax", headers=KEY).status_code == 404


def test_no_key_is_refused_and_list_of_periods(client):
    assert client.get("/api/system/ky/2026-10").status_code == 401 and client.get("/api/system/ky").status_code == 401
    assert client.get("/api/system/ky", headers=KEY).json()["periods"] == [{"period": "2026-10", "ready": True}]


def test_uploaded_file_becomes_the_raw_of_its_period(client, folder):
    """Tải file đơn hàng tháng 10 chỉ tới 20/10 ⇒ mục raw của kỳ là BẢN ĐÃ TẢI (đã qua cửa tải lên); chỉ tiêu / tuyến / luật vẫn là file lúc bật."""
    source = openpyxl.load_workbook(RAW, read_only=True, data_only=True)
    rows = source["Export"].iter_rows(values_only=True)
    header = next(rows)
    at = header.index("ORDER DATE")
    target = openpyxl.Workbook(write_only=True)
    sheet = target.create_sheet("Export")
    sheet.append(header)
    for row in rows:
        if len(row) > at and isinstance(row[at], datetime) and row[at].date() <= date(2026, 10, 20):
            sheet.append(row)
    out = io.BytesIO()
    target.save(out)
    source.close()
    sent = client.post("/api/system/upload", params={"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-20"}, content=out.getvalue(),
                       headers={**KEY, "Content-Type": "application/octet-stream"})
    assert sent.status_code == 200, sent.text[:300]
    body = client.get("/api/system/ky/2026-10", headers=KEY).json()
    raw = body["items"][0]
    assert raw["from_upload"] is True and raw["checked"] is True and raw["data_version"] == sent.json()["data_version"] and raw["cutoff"] == "2026-10-20"
    assert re.fullmatch(r"[0-9a-f]{64}\.xlsx", raw["file"]) and raw["file"].startswith(raw["sha256_8"])
    assert datetime.fromisoformat(raw["uploaded_at"]).date() == datetime.now().date()
    assert body["ready"] is True and [item["from_upload"] for item in body["items"][1:]] == [False] * 4
    assert client.get("/api/system/ky", headers=KEY).json()["periods"] == [{"period": "2026-10", "ready": True}]
    _assert_nothing_private(body, folder)
