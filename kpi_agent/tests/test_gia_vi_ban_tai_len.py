"""2a-2 nấc 1 — bản dựng qua cửa tải lên TỰ tính 1 % gia vị + hội viên; không đường nào trả số của bản khác.

Hai bản tải lên thật trên backend đường file đơn hàng (luật tháng 10 thật, ngày chốt lúc bật 31/10):
- bản SỔ: tải sổ theo dõi (tuyến từ sổ, dữ liệu như lúc bật) ⇒ mọi khoản tiền phải GIỐNG bản lúc bật (152.360.000 · 19.026.300 · 11.868.854);
- bản 20/10: file đơn hàng cắt tới 20/10, ngày chốt 20/10 ⇒ số gia vị KHÁC bản lúc bật — dùng để bắt đường nào trả nhầm số bản lúc bật (lỗi 16/11).
SÁU đường trả 1 % gia vị / hội viên (đề của test, bổ sung đường thứ 6): /api/spice-loyalty · /api/spice-tool · /api/spice-customers
/api/system/spice-loyalty · POST /api/tool/get_spice_loyalty · /api/membership."""
import io
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
BOOK = ROOT / "sample_data/thang_10/Tracking.xlsx"
needs_data = pytest.mark.skipif(not (RAW.exists() and BOOK.exists()), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"
KEY = {"X-Kpi-Sys-Key": SYS_KEY}
BIN = {**KEY, "Content-Type": "application/octet-stream"}
TEAM_SPICE = 11_868_854


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    folder = tmp_path_factory.mktemp("gia_vi")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        mp.setattr(main, "UPLOAD_DIR", folder / "uploads")
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as test_client:
            yield test_client


@pytest.fixture(scope="module")
def versions(client) -> dict:
    """{"book": mã bản lấy tuyến từ sổ, "d20": mã bản file đơn hàng tới 20/10} — dựng TRƯỚC khi gọi bất kỳ đường gia vị nào của bản lúc bật."""
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
    d20 = client.post("/api/system/upload", params={"loai": "raw", "ky": "2026-10", "ngay_chot": "2026-10-20"}, content=out.getvalue(), headers=BIN)
    book = client.post("/api/system/upload", params={"loai": "so_tracking", "ky": "2026-10"}, content=BOOK.read_bytes(), headers=BIN)
    assert d20.status_code == 200 and book.status_code == 200, (d20.text[:200], book.text[:200])
    return {"d20": d20.json(), "book": book.json()}


def _spice_by_every_route(client, version: str | None) -> dict:
    """Tổng thưởng 1 % gia vị cả đội theo TỪNG đường (+ số khách của đường từng khách) cho một bản (None = không gửi mã bản)."""
    q = {"data_version": version} if version else {}
    page = client.get("/api/spice-loyalty", params=q).json()
    tool_page = client.get("/api/spice-tool", params=q).json()
    customers = client.get("/api/spice-customers", params=q).json()
    system = client.get("/api/system/spice-loyalty", params=q, headers=KEY)
    tool = client.post("/api/tool/get_spice_loyalty", json={"args": {}}, params=q).json()
    for name, body in (("spice-loyalty", page), ("spice-tool", tool_page), ("spice-customers", customers), ("tool", tool)):
        assert "refused" not in body, (name, version, str(body)[:200])
    assert system.status_code == 200, system.text[:200]
    return {"page": page["team_reward"], "tool_page": tool_page["team"]["reward_paid_to_customers"], "system": system.json()["team_reward"],
            "tool": tool["team"]["reward_paid_to_customers"], "customer_rows_reward": sum(row["reward"] for row in customers["rows"]),
            "cutoff": {page["cutoff"], tool_page["cutoff"], customers["cutoff"], system.json()["cutoff"]}}


@needs_data
def test_every_spice_route_answers_for_the_version_asked_in_any_order(client, versions):
    d20, book = versions["d20"]["data_version"], versions["book"]["data_version"]
    # thứ tự 1: bản tải lên TRƯỚC bản lúc bật (ô giữ của bản lúc bật còn trống) — rồi xen kẽ lại; ô giữ phải khoá theo mã bản
    first_d20 = _spice_by_every_route(client, d20)
    base = _spice_by_every_route(client, None)
    from_book = _spice_by_every_route(client, book)
    again_d20 = _spice_by_every_route(client, d20)
    again_base = _spice_by_every_route(client, client.app.state.data_version)
    money = lambda result: {key: value for key, value in result.items() if key != "cutoff"}
    assert set(money(base).values()) == {TEAM_SPICE} and base["cutoff"] == {"2026-10-31"}                   # bản lúc bật: 6 con số của 5 đường đều 11.868.854
    assert money(from_book) == money(base) and from_book["cutoff"] == {"2026-10-31"}                         # bản lấy tuyến từ sổ: giống bản lúc bật
    assert len(set(money(first_d20).values())) == 1 and first_d20["cutoff"] == {"2026-10-20"}                # bản 20/10: mọi đường CÙNG một số…
    assert money(first_d20)["page"] not in (TEAM_SPICE, 0)                                                   # …của CHÍNH nó, không phải số bản lúc bật
    assert again_d20 == first_d20 and again_base == base                                                     # gọi xen kẽ không đổi số của bản nào


@needs_data
def test_book_version_spice_matches_startup_cell_by_cell(client, versions):
    q = {"data_version": versions["book"]["data_version"]}
    base, own = client.get("/api/system/spice-loyalty", headers=KEY).json(), client.get("/api/system/spice-loyalty", headers=KEY, params=q).json()
    assert own["employees"] == base["employees"] and len(base["employees"]) == 14                             # 14 người × các ô của từng người
    assert own["team_reward"] == base["team_reward"] == TEAM_SPICE
    rows = lambda body: sorted((row["customer_code"], row["baseline"], row["sales"], row["reward"]) for row in body["rows"])
    assert rows(client.get("/api/spice-customers", params=q).json()) == rows(client.get("/api/spice-customers").json())   # từng khách


@needs_data
def test_page_numbers_only_for_startup_and_cut_version_is_marked_provisional(client, versions):
    base = client.get("/api/spice-loyalty").json()
    d20 = client.get("/api/spice-loyalty", params={"data_version": versions["d20"]["data_version"]}).json()
    book = client.get("/api/spice-loyalty", params={"data_version": versions["book"]["data_version"]}).json()
    # số TRANG CỦA SẾP hiện cạnh là đáp án của đúng bản lúc bật ⇒ bản tải lên không mang
    assert d20["page_totals"]["page"] is None and book["page_totals"]["page"] is None
    # bản chưa hết kỳ (chốt 20/10) nói rõ tạm tính; bản đã hết kỳ không có khoá này (hình phản hồi của bản lúc bật không đổi)
    assert "20/10/2026" in d20["provisional_note"] and "provisional_note" not in base and "provisional_note" not in book


@needs_data
def test_membership_answers_for_the_version_asked(client, versions):
    d20, book = versions["d20"]["data_version"], versions["book"]["data_version"]
    first = client.get("/api/membership", params={"data_version": d20}).json()
    base = client.get("/api/membership").json()
    own = client.get("/api/membership", params={"data_version": book}).json()
    again = client.get("/api/membership", params={"data_version": d20}).json()
    for body in (first, base, own):
        assert "refused" not in body, str(body)[:200]
    sales = lambda body: sum(row["sales"] for row in body["rows"])
    assert own["rows"] == base["rows"] and (base["cutoff"], first["cutoff"]) == ("2026-10-31", "2026-10-20")
    assert sales(first) < sales(base) and again == first                                                    # doanh số tới 20/10 nhỏ hơn cả tháng; xen kẽ không đổi


@needs_data
def test_unknown_version_gets_no_numbers_on_any_route(client, versions):
    q = {"data_version": "0" * 40}
    for path in ("/api/spice-loyalty", "/api/spice-tool", "/api/spice-customers", "/api/membership"):
        body = client.get(path, params=q).json()
        assert "refused" in body and not ({"rows", "team_reward", "team", "employees"} & set(body)), (path, str(body)[:200])
    assert client.get("/api/system/spice-loyalty", params=q, headers=KEY).status_code == 404
    assert client.post("/api/tool/get_spice_loyalty", json={"args": {}}, params=q).status_code == 404


@needs_data
def test_upload_reply_reports_spice_state(versions):
    # KPI_WARM_SPICE tắt trong test (như lúc bật) ⇒ 1 % gia vị tính ở lần gọi đầu ⇒ phản hồi tải lên nói đúng "chua_tinh", không đoán "co"
    assert versions["book"]["gia_vi"] == "chua_tinh" and versions["book"]["gia_vi_ly_do"] is None and versions["book"]["thuong_them"] == "co"


# ---------- các đường GET của trang / công cụ nhận ?data_version= — đi qua ĐÚNG bản, hoặc từ chối có lời; không lặng lẽ trả bản lúc bật ----------
@needs_data
def test_page_routes_answer_for_the_version_asked(client, versions):
    d20 = versions["d20"]
    q = {"data_version": d20["data_version"]}
    base, cut = client.get("/api/team-bonus").json(), client.get("/api/team-bonus", params=q).json()
    # lỗi có sẵn: /api/team-bonus?data_version=<bản khác> trả bảng của bản LÚC BẬT với mã 200
    assert (base["cutoff"], cut["cutoff"]) == ("2026-10-31", "2026-10-20")
    assert base["team_total"] == 152_360_000 and cut["team_total"] == d20["team_total"] != 152_360_000
    assert client.get("/api/team-bonus", params={"data_version": client.app.state.data_version}).json() == base      # mã bản lúc bật ⇒ như không gửi
    for path in ("/api/team-board", "/api/opportunities", "/api/employees/NV01", "/api/gap", "/api/explain/NV01/KPI1", "/api/team-kpi", "/api/compare",
                 "/api/data-issues", "/api/what-if/NV01/KPI1", "/api/extra-bonus"):
        body = client.get(path, params=q).json()
        assert body.get("cutoff", "2026-10-20") == "2026-10-20" and "2026-10-31" not in str(body.get("cutoff")), (path, str(body)[:200])
        assert client.get(path, params={"data_version": "0" * 40}).status_code == 404, path                           # bản lạ ⇒ 404, không có số
    assert client.get("/api/employees/NV01", params=q).json()["cutoff"] == "2026-10-20"


@needs_data
def test_screens_kept_for_the_startup_version_refuse_another_version(client, versions):
    from kpi_agent.backend import main
    q = {"data_version": versions["d20"]["data_version"]}
    for path in ("/api/names", "/api/tracking", "/api/outlets", "/api/tea-365", "/api/order-lines", "/api/runs"):
        assert client.get(path, params=q).json()["refused"] == main.BOOT_VERSION_ONLY, path
        own = client.get(path, params={"data_version": client.app.state.data_version})
        assert main.BOOT_VERSION_ONLY not in own.text, path


@needs_data
def test_version_of_another_period_does_not_borrow_the_october_spice_baseline(client):
    # File luật 1 % gia vị ghi cứng sổ nền của MỘT kỳ ⇒ bản của kỳ khác bị từ chối có lời, không lặng lẽ tính trên nền tháng 10. Chưa có dữ liệu thật
    # của kỳ khác qua cửa (luật tháng 11 gác) ⇒ gọi thẳng hàm tính với ngữ cảnh của bản lúc bật ĐỔI kỳ; file luật giả của kỳ đó trỏ lại kỳ 2026-10.
    import dataclasses
    from types import SimpleNamespace
    from kpi_agent.backend import main
    state = client.app.state
    other = SimpleNamespace(state=SimpleNamespace(workflow=state.workflow, ctx=dataclasses.replace(state.ctx, period="2026-09")))
    assert "chưa có file luật 1 % gia vị cho kỳ 2026-09" in main._compute_spice(other)["refused"]
    rules = main.REPO_ROOT / "kpi_agent/config/gia_vi_1pct_2026_10.yaml"
    borrowed = main.REPO_ROOT / "kpi_agent/config/gia_vi_1pct_2026_09.yaml"
    assert not borrowed.exists()
    borrowed.write_bytes(rules.read_bytes())          # file tên kỳ 09 nhưng nội dung ghi kỳ 10 (chép nhầm) ⇒ phải từ chối
    try:
        refused = main._compute_spice(other)
    finally:
        borrowed.unlink()
    assert "refused" in refused and "không phải kỳ 2026-09" in refused["refused"], str(refused)[:200]


@needs_data
def test_membership_refuses_another_period_and_a_custom_column_mapping(client):
    # Như 1 % gia vị: nền hội viên ghi cứng sổ của MỘT kỳ. Và phần hội viên đọc file đơn hàng theo tên cột của mẫu (chưa qua bảng ghép cột) ⇒ bản có
    # cách ghép riêng bị từ chối có lời, không đọc nhầm cột. Gọi thẳng hàm tính với ngữ cảnh / workflow của bản lúc bật đổi đúng một thứ.
    import copy
    import dataclasses
    from types import SimpleNamespace
    from kpi_agent.backend import main
    from kpi_agent.core import khuon
    state = client.app.state
    source = main.REPO_ROOT / "kpi_agent/config/hoi_vien_2026_10.yaml"
    borrowed = main.REPO_ROOT / "kpi_agent/config/hoi_vien_2026_09.yaml"
    assert not borrowed.exists()
    other = SimpleNamespace(state=SimpleNamespace(workflow=state.workflow, ctx=dataclasses.replace(state.ctx, period="2026-09")))
    borrowed.write_bytes(source.read_bytes())         # file tên kỳ 09 nhưng nội dung ghi kỳ 10 (chép nhầm) ⇒ phải từ chối
    try:
        refused = main._compute_membership(other)
    finally:
        borrowed.unlink()
    assert "không phải kỳ 2026-09" in refused["refused"], str(refused)[:200]
    def with_mapping(mapping):
        workflow = copy.deepcopy(state.workflow)
        next(node for node in workflow["nodes"] if node["type"] == "read_raw").setdefault("params", {})["ghep"] = mapping
        return main._compute_membership(SimpleNamespace(state=SimpleNamespace(workflow=workflow, ctx=state.ctx)))
    default = khuon.default_mapping("don_hang")
    columns = dict(default["columns"])
    columns["sales_after_discount"], columns["sales_before_discount"] = columns["sales_before_discount"], columns["sales_after_discount"]
    refused = with_mapping({**default, "columns": columns})                       # ghép riêng: đổi chéo hai cột tiền
    assert "cách ghép cột riêng" in refused["refused"] and "rows" not in refused
    assert "rows" in with_mapping({**default, "accepted_values": {"sub_div": ["NGANH MOI"]}})   # bố cục mẫu + ngành được cho qua ⇒ vẫn tính (không chặn oan)
    assert "rows" in main._compute_membership(SimpleNamespace(state=SimpleNamespace(workflow=state.workflow, ctx=state.ctx)))    # bản đúng mẫu vẫn tính
