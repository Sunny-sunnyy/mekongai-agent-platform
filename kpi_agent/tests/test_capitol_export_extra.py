"""Đường hệ thống cho khối Người duyệt / Xuất của bộ Capitol + Thưởng thêm.

- POST /api/system/duyet: backend tự so mã băm với bảng hiện tại ⇒ ghi bảng riêng `capitol_approval`; KHÔNG đổi "Đã soát" của trang User.
- GET /api/export/{kind} có khoá: chỉ có file khi có lần duyệt Capitol khớp băm bảng HIỆN TẠI; file có mã / tên thật ⇒ 409. Không khoá ⇒ như cũ.
- GET /api/system/extra-bonus: 6 khoản × 14 NV mã che, tách khỏi bảng 8 KPI (mã băm bảng không đổi).
Backend chạy thật (TestClient, workflow đường RAW, ngày chốt 31/10, KPI_STORE tạm). Ca xấu ghi trước khi code."""
import io
import json
import zipfile
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


@pytest.fixture(scope="module")
def raw_client(tmp_path_factory):
    from kpi_agent.backend import main
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(tmp_path_factory.mktemp("store") / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
            yield client


def _table(client) -> dict:
    return client.get("/api/system/bonus-table", headers=KEY).json()


def _duyet(client, **override):
    table = _table(client)
    body = {"run_id": table["run_id"], "sha256_bang": table["sha256"], "org_id": "org-thu", "nguoi_duyet": "giám sát thử", **override}
    return client.post("/api/system/duyet", json=body, headers=KEY)


def test_1_export_with_key_before_approval_is_409_and_page_path_unchanged(raw_client):
    # Ca xấu ghi trước: export có khoá khi chưa duyệt ⇒ 409; không khoá ⇒ như cũ (chưa soát ⇒ 409)
    refused = raw_client.get("/api/export/xlsx", headers=KEY)
    assert refused.status_code == 409 and "chưa duyệt" in refused.json()["refused"]
    assert raw_client.get("/api/export/xlsx").status_code == 409
    assert raw_client.get("/api/export/xlsx", headers=KEY, params={"run_id": 1}).status_code == 422   # đường hệ thống chỉ bản đang dùng


def test_2_keys_and_bad_bodies(raw_client, monkeypatch):
    assert raw_client.post("/api/system/duyet", json={}, headers={"X-Kpi-Sys-Key": "sai-" + "x" * 20}).status_code == 401
    assert raw_client.get("/api/export/md", headers={"X-Kpi-Sys-Key": ""}).status_code == 401      # có header mà rỗng ⇒ vẫn là đường hệ thống
    assert raw_client.get("/api/system/extra-bonus").status_code == 401
    table = _table(raw_client)
    assert _duyet(raw_client, sha256_bang="0" * 64).status_code == 409                               # sha cũ / sai ⇒ 409, không ghi
    assert _duyet(raw_client, run_id=table["run_id"] + 1).status_code == 409                          # không phải bản đang dùng
    for bad in ({"sha256_bang": table["sha256"].upper()}, {"run_id": "1"}, {"nguoi_duyet": ""}, {"nguoi_duyet": 5}):
        assert _duyet(raw_client, **bad).status_code == 422, bad
    extra_field = {"run_id": table["run_id"], "sha256_bang": table["sha256"], "org_id": "o", "nguoi_duyet": "n", "status": "da_soat"}
    assert raw_client.post("/api/system/duyet", json=extra_field, headers=KEY).status_code == 422
    assert raw_client.get("/api/export/xlsx", headers=KEY).status_code == 409                        # các lần trên không ghi gì
    monkeypatch.setenv("KPI_SYS_KEY", "ngan")   # máy chưa đặt khoá đủ dài ⇒ 503 (trước cả khi đọc thân)
    assert raw_client.post("/api/system/duyet", json=extra_field, headers=KEY).status_code == 503
    assert raw_client.get("/api/export/xlsx", headers=KEY).status_code == 503


def test_3_approval_then_export_files_without_real_identity(raw_client):
    meta_before = raw_client.get("/api/meta").json()
    approved = _duyet(raw_client)
    assert approved.status_code == 200 and approved.json()["duyet"] is True
    for kind in ("xlsx", "md", "txt"):
        response = raw_client.get(f"/api/export/{kind}", headers=KEY)
        assert response.status_code == 200, (kind, response.text[:200])
    md = raw_client.get("/api/export/md", headers=KEY).text
    assert "ĐÃ SOÁT" in md and "đã duyệt ở khối Người duyệt" in md and "giám sát thử" not in md   # không chép tên người duyệt vào file
    # "Đã soát" của trang User KHÔNG đổi: vẫn tạm tính, trang vẫn chưa tải được file
    meta_after = raw_client.get("/api/meta").json()
    assert meta_after["export"] == meta_before["export"] and meta_after["export"]["allowed"] is False
    assert raw_client.get("/api/export/xlsx").status_code == 409


def test_4_table_changed_after_approval_needs_new_approval(raw_client):
    # Ca xấu ghi trước: duyệt xong rồi bảng đổi ⇒ export 409. Bảng đổi ⇒ mã băm đổi; ở đây giữ lần duyệt của băm cũ, đổi băm bảng hiện tại
    from kpi_agent.backend import main
    original = main._bonus_table_body
    def changed(request):
        body = original(request)
        return {**body, "sha256": "f" * 64}
    main._bonus_table_body = changed
    try:
        refused = raw_client.get("/api/export/xlsx", headers=KEY)
        assert refused.status_code == 409 and "duyệt (lại)" in refused.json()["refused"]
    finally:
        main._bonus_table_body = original
    assert raw_client.get("/api/export/xlsx", headers=KEY).status_code == 200


def test_5_real_identity_in_docprops_or_text_blocks_file(raw_client, monkeypatch):
    # Ca xấu ghi trước: cài 1 mã thật vào docProps của xlsx / 1 tên thật vào md ⇒ 409, lời trả không chứa chuỗi thật
    from kpi_agent.backend import main
    tokens = main._real_identity_tokens(raw_client.app)
    code, name = tokens[0], max(tokens, key=len)
    xlsx_build, md_build = main.EXPORT_KINDS["xlsx"][1], main.EXPORT_KINDS["md"][1]

    def xlsx_with_real_code(data):
        source, out = zipfile.ZipFile(io.BytesIO(xlsx_build(data))), io.BytesIO()
        with zipfile.ZipFile(out, "w") as target:
            for item in source.namelist():
                content = source.read(item)
                if item == "docProps/core.xml":
                    content = content.replace(b"</cp:coreProperties>", f"<dc:creator>{code.upper()}</dc:creator></cp:coreProperties>".encode())
                target.writestr(item, content)
        return out.getvalue()

    monkeypatch.setitem(main.EXPORT_KINDS, "xlsx", (main.EXPORT_KINDS["xlsx"][0], xlsx_with_real_code))
    monkeypatch.setitem(main.EXPORT_KINDS, "md", (main.EXPORT_KINDS["md"][0], lambda data: md_build(data) + f"\nngười lập: {name.title()}\n"))
    for kind in ("xlsx", "md"):
        refused = raw_client.get(f"/api/export/{kind}", headers=KEY)
        assert refused.status_code == 409 and "mã / tên nhân viên thật" in refused.json()["refused"]
        assert code not in refused.text.lower() and name not in refused.text.lower()


def test_6_extra_bonus_route_is_separate_from_bonus_table(raw_client):
    sha_before = _table(raw_client)["sha256"]
    body = raw_client.get("/api/system/extra-bonus", headers=KEY).json()
    assert body["team_total"] == 17_784_000 + 1_242_300 and len(body["lines"]) == 9 * 14   # 11/11 + Meat 7.x; luật chữ sổ T03–T05 (trước 17.644.000, 88a3b6f)
    assert body["item_total"] == {"T01": 4_176_000, "T02": 318_000, "T03": 2_350_000, "T04": 510_000, "T05": 3_930_000, "T06": 6_500_000,
                                 "T071": 596_175, "T072": 646_125, "T073": 0}
    assert sorted(body["employee_total"]) == [f"NV{i:02d}" for i in range(1, 15)]
    from kpi_agent.backend import main
    text = json.dumps(body, ensure_ascii=False).lower()
    assert not any(token in text for token in main._real_identity_tokens(raw_client.app))   # 0 mã / tên thật
    assert _table(raw_client)["sha256"] == sha_before and _table(raw_client)["table"]["team_total"] == 152_360_000


@pytest.mark.parametrize("mask", ["", '{"map": {}}', "drop_one"])
def test_6b_export_closed_when_identity_list_cannot_be_checked(raw_client, monkeypatch, tmp_path, mask):
    # Ca xấu ghi trước ("đóng khi lỗi"): bảng mã che rỗng / hỏng / thiếu 1 NV ⇒ 409, không trả file
    from kpi_agent.backend import main
    path = tmp_path / "ma_che_ten.json"
    if mask == "drop_one":
        data = json.loads(main.MASK.read_text(encoding="utf-8"))
        data["map"].pop(sorted(data["map"])[0])
        mask = json.dumps(data)
    path.write_text(mask, encoding="utf-8")
    monkeypatch.setattr(main, "MASK", path)
    refused = raw_client.get("/api/export/xlsx", headers=KEY)
    assert refused.status_code == 409 and "không kiểm được tên thật" in refused.json()["refused"]


def test_6c_real_name_split_into_runs_is_still_found(raw_client, monkeypatch):
    # Ca xấu ghi trước: tên thật chia 2 đoạn <r><t> trong MỘT ô chữ (inlineStr) ⇒ vẫn 409 (tên lấy từ bảng lúc chạy, không gõ tên thật)
    from kpi_agent.backend import main
    name = max((t for t in main._real_identity_tokens(raw_client.app) if " " in t), key=len)
    head, tail = name.split(" ", 1)
    xlsx_build = main.EXPORT_KINDS["xlsx"][1]

    def xlsx_with_split_name(data):
        source, out = zipfile.ZipFile(io.BytesIO(xlsx_build(data))), io.BytesIO()
        # openpyxl ghi chữ INLINE trong ô (không có sharedStrings.xml) ⇒ chèn một ô inlineStr 2 đoạn vào sheet đầu
        cell = f'<c r="Z1" t="inlineStr"><is><r><t>{head.title()}</t></r><r><t xml:space="preserve"> {tail.title()}</t></r></is></c>'
        sheet = sorted(n for n in source.namelist() if n.startswith("xl/worksheets/sheet"))[0]
        with zipfile.ZipFile(out, "w") as target:
            for item in source.namelist():
                content = source.read(item)
                if item == sheet:
                    assert b"</row>" in content
                    content = content.replace(b"</row>", cell.encode() + b"</row>", 1)
                target.writestr(item, content)
        return out.getvalue()

    monkeypatch.setitem(main.EXPORT_KINDS, "xlsx", (main.EXPORT_KINDS["xlsx"][0], xlsx_with_split_name))
    refused = raw_client.get("/api/export/xlsx", headers=KEY)
    assert refused.status_code == 409 and "mã / tên nhân viên thật" in refused.json()["refused"] and head not in refused.text.lower()

def test_7_extra_bonus_refused_on_tracking_workflow(tmp_path, monkeypatch):
    from kpi_agent.backend import main
    from kpi_agent.workflow import runner
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))
    monkeypatch.setenv("KPI_SYS_KEY", SYS_KEY)
    monkeypatch.setattr(main, "WORKFLOW", runner.DEFAULT_WORKFLOW)   # fixture module còn đặt RAW — đặt rõ đường Tracking
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 20))
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        refused = client.get("/api/system/extra-bonus", headers=KEY)
        assert refused.status_code == 409 and "đường RAW" in refused.json()["refused"]
