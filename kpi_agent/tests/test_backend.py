"""P1 backend chỉ đọc (SPEC mục 4) — gọi qua HTTP bằng TestClient trên dữ liệu thật tháng 10 (không mock lõi; máy không có dữ liệu thì bỏ qua).

Kiểm: số qua API = số lõi tính (tổng 55.700.000 đ như run_kpi), tham số lạ ⇒ 200 + "refused" và KHÔNG có số, không endpoint ghi.
Phép so T1 112/112 đầy đủ là việc bằng script riêng — test này chỉ giữ hợp đồng không bị vỡ khi sửa code."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")

NUMBER_KEYS = {"employees", "kpis", "business_salary", "team_total", "amount", "gaps"}


@pytest.fixture(scope="module")
def client():
    from kpi_agent.backend.main import app
    with TestClient(app, base_url="http://127.0.0.1:8110") as test_client:   # `with` để chạy lifespan; Host thật như agent / trang gọi
        yield test_client


def assert_refused(response):
    body = response.json()
    assert response.status_code == 200 and "refused" in body, body
    assert not NUMBER_KEYS & set(body), f"bị từ chối mà vẫn có số: {sorted(NUMBER_KEYS & set(body))}"
    assert "params_used" in body


def test_meta_has_provisional_label_and_team(client):
    body = client.get("/api/meta").json()
    assert body["period"] == "2026-10" and body["cutoff"] == "2026-10-20"
    assert "TẠM TÍNH" in body["label"] and body["snapshot_at"]
    assert body["employee_ids"] == [f"NV{i:02d}" for i in range(1, 15)]
    assert set(body["files_sha256"]) == {"rules_2026_10.yaml", "Chi tieu NVBH.xlsx", "Tracking.xlsx"}


def test_team_bonus_total_matches_core(client):
    body = client.get("/api/team-bonus").json()
    assert body["team_total"] == 55_700_000 and body["employee_count"] == 14
    assert body["params_used"] == {"period": "2026-10"}


def test_employee_and_explain_and_gap(client):
    one = client.get("/api/employees/nv04").json()
    assert one["employee_id"] == "NV04" and len(one["kpis"]) == 8 and one["params_used"]["employee_id"] == "NV04"
    explain = client.get("/api/explain/NV04/aso_focus").json()
    assert explain["kpi"] == "ASO_Focus" and explain["cong_van_section"] == "2.5"
    gap = client.get("/api/gap", params={"employee_id": "NV04"}).json()
    assert [e["employee_id"] for e in gap["employees"]] == ["NV04"]
    assert len(client.get("/api/gap").json()["employees"]) == 14
    # bậc "ít nhất N" bản sửa: cờ chỉ true khi số TỔNG đã làm tròn lên (trước: true mọi khoảng — ngược nghĩa chuỗi "trung bình")
    assert all(g["gap_rounded_up"] == (g["kpi"] == "Turnover" and "ít nhất" in g["gap_per_day_text"]) for e in gap["employees"] for g in e["gaps"])


@pytest.mark.parametrize("path", [
    # httpx (TestClient) tự rút gọn "/NV01/../NV02" thành "/NV02" trước khi gửi ⇒ ở đây gửi dạng %2E%2E (server giải mã thành "..");
    # chuỗi "/../" nguyên văn kiểm bằng curl --path-as-is trên server thật (KET_QUA mục P1)
    "/api/employees/NV01/%2E%2E/NV02",
    "/api/employees/NV01%2FNV02",
    "/api/employees/..%2Fdata%2Fma_che_ten.json",
    "/api/employees/NV15",
    "/api/employees/NV00",
    "/api/employees/NV1",
    "/api/explain/NV04/khong_co_kpi",
    "/api/explain/NV01%2F..%2FNV02/PC_BT",
    "/api/employees/NV04?period=2026-11",
    "/api/team-bonus?period=../2026-10",
    "/api/gap?employee_id=NV01/../NV02",
])
def test_bad_params_refused_without_numbers(client, path):
    assert_refused(client.get(path))


def test_refusal_does_not_echo_long_input(client):
    body = client.get("/api/employees/" + "A" * 3000).json()
    assert "refused" in body and len(body["refused"]) < 200 and len(str(body["params_used"])) < 200


def test_read_only(client):
    assert client.post("/api/team-bonus").status_code == 405
    assert client.delete("/api/employees/NV04").status_code == 405


def test_unknown_host_rejected_and_docs_off(client):
    # C rà bảo mật 03/11 (R2): Host lạ ⇒ 400 (chặn trang web lạ dùng DNS-rebinding gọi vào máy); /docs, /openapi.json tắt mặc định
    assert client.get("/api/meta", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/meta", headers={"host": "localhost:8110"}).status_code == 200
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


# /api/team-board: endpoint RIÊNG cho trang — agent không đọc ⇒ kết quả công cụ agent không đổi. Trang không tự tính số.
def test_team_board_matches_team_bonus_and_core(client):
    board = client.get("/api/team-board").json()
    bonus = client.get("/api/team-bonus").json()
    assert board["team_total"] == bonus["team_total"] == 55_700_000
    assert board["team_max_total"] == 14 * bonus["business_salary_at_100_pct"] == 203_560_000
    assert board["team_share_pct"] == 27.36                                   # 55.700.000 / 203.560.000 = 27,363…% làm tròn xuống
    assert "TẠM TÍNH" in board["label"] and board["kpis"][0] == "Turnover"
    by_id = {row["employee_id"]: row for row in bonus["employees"]}
    for row in board["employees"]:
        assert row["business_salary"] == by_id[row["employee_id"]]["business_salary"]
        for kpi, cell in row["cells"].items():
            assert cell["amount"] == by_id[row["employee_id"]]["kpi_amounts"][kpi]
            assert cell["status"] == ("chua_tien" if cell["amount"] == 0 else "du_moc" if cell["next_tier"] is None else "moc_thap")
            assert cell["marks"][-1] == 1.0 and cell["marks"] == sorted(cell["marks"])
            if cell["status"] == "du_moc":
                assert cell["progress"] >= 1.0
            if cell["status"] == "chua_tien":
                assert cell["progress"] < cell["marks"][0]
    paid = {k: sum(r["cells"][k]["amount"] > 0 for r in board["employees"]) for k in board["kpis"]}
    assert {k: v["paid_count"] for k, v in board["kpi_summary"].items()} == paid
    assert board["kpis_nobody_paid"] == [k for k in board["kpis"] if paid[k] == 0]


def test_team_board_refuses_other_period(client):
    # Trước 04/11 test này còn khoá "agent không có công cụ bảng đội" (quyết định bản đầu). 04/11 thêm get_team_board
    # ⇒ phần khoá đó bỏ; danh sách công cụ chính xác nay kiểm ở test_agent_new_tools.py
    body = client.get("/api/team-board?period=2026-11").json()
    assert "refused" in body and "employees" not in body
    # 04/11 G2-4: người vận hành get_team_board khỏi công cụ trợ lý (sau 2 vòng đo hồi quy) ⇒ khoá lại "endpoint chỉ cho trang"
    from kpi_agent.agent.backend_client import TOOL_PARAMS
    assert "get_team_board" not in TOOL_PARAMS


# Tính năng 4 (khuôn C KHUON_TRUONG_NGUON_C.md): bản luật + trạng thái do code điền
def test_meta_has_rule_version_and_status(client):
    import hashlib
    body = client.get("/api/meta").json()
    sha8 = hashlib.sha256((ROOT / "kpi_agent/config/rules_2026_10.yaml").read_bytes()).hexdigest()[:8]
    assert body["rule_version"] == f"rules_2026_10.yaml@{sha8}" and body["status"] == "tam_tinh"


# Tính năng 2 bản đầu (, (a) theo nhịp): endpoint cho trang; từ 04/11 agent đọc qua get_opportunities
def test_opportunities_endpoint(client):
    body = client.get("/api/opportunities").json()
    assert "chưa dự báo" in body["label"] and body["params_used"] == {"period": "2026-10"}
    assert body["opportunity_count"] == sum(item["is_opportunity"] is True for item in body["items"])
    assert "refused" in client.get("/api/opportunities?period=../2026-10").json()


def test_opportunity_why_uses_vietnamese_number_format(client):
    items = client.get("/api/opportunities").json()["items"]
    turnover = next(i for i in items if i["kpi"] == "Turnover")
    head = turnover["why"].split("/ngày")[0]
    assert head.startswith("cần trung bình ") and "." in head and head.count(",") <= 1 and "ít nhất" not in head   # 38.460.108,79 kiểu Việt, không 38,460,108.79


def test_team_board_label_without_region_code(client):
    assert client.get("/api/team-board").json()["team_label"] == "Nam Trung Bộ"        # bỏ mã "CD - "



# Bậc 4b (cuối file, ghi trước 11:45): CÙNG nhãn nguồn ở kết quả công cụ + dòng ngữ cảnh lời dặn
def test_tool_results_carry_rule_version_and_status_label(client):
    meta = client.get("/api/meta").json()
    assert meta["status_label"] == "tạm tính — chưa phải số chốt, chưa duyệt"
    for path in ("/api/team-bonus", "/api/employees/NV04", "/api/gap", "/api/explain/NV04/PC_BT"):
        body = client.get(path).json()
        assert (body["rule_version"], body["status"], body["status_label"]) == (meta["rule_version"], "tam_tinh", meta["status_label"]), path


# Bậc "ít nhất N / ngày" (L1 — bản sửa): nói theo TỔNG + trung bình, "ít nhất" chỉ khi tổng đã làm tròn lên
@pytest.mark.parametrize("args, expected", [
    (("ASO_ALL", 3, 3, 10), "cần thêm 3 trong 10 ngày làm việc còn lại (trung bình 0,3 mỗi ngày)"),        # không "ít nhất 1 mỗi ngày"
    (("PC_BT", 20, 20, 10), "cần thêm 20 trong 10 ngày làm việc còn lại (trung bình 2 mỗi ngày)"),
    (("Turnover", 812888711.36, 812888712, 10),
     "cần thêm ít nhất 812.888.712 đồng trong 10 ngày làm việc còn lại (trung bình 81.288.871,14 đồng mỗi ngày)"),
    (("ASO_ALL", 9, 9, 0), None),
    (("LPPC_Meat", 0.79, 0.79, 10), None),                         # tỷ lệ — không nói "mỗi ngày" (User câu 46)
])
def test_per_day_text(args, expected):
    from kpi_agent.backend.queries import per_day_text
    assert per_day_text(*args) == expected


def test_ratio_kpis_have_no_per_day_number(client):
    gaps = [g for e in client.get("/api/gap").json()["employees"] for g in e["gaps"]]
    ratio = [g for g in gaps if g["kpi"] in ("LPPC", "LPPC_Meat")]
    assert ratio and all(g["gap_per_working_day_left"] is None and g["gap_per_day_text"] is None for g in ratio)
    assert not any("ít nhất 1 mỗi ngày" in (g["gap_per_day_text"] or "") for g in gaps)


def test_gap_numbers_agree_with_text(client):
    # bản 2 (major): trường số mỗi ngày = trung bình (không làm tròn lên); gap_rounded_up chỉ khi TỔNG đã làm tròn
    gaps = [g for e in client.get("/api/gap").json()["employees"] for g in e["gaps"] if g["gap_per_day_text"]]
    for g in gaps:
        assert f"trung bình {str(g['gap_per_working_day_left']).replace('.', ',').removesuffix(',0')}" in g["gap_per_day_text"].replace(".", "") \
            or g["kpi"] == "Turnover", g
        assert g["gap_rounded_up"] == ("ít nhất" in g["gap_per_day_text"]), g


@pytest.mark.parametrize("value, expected", [(23260710.004, "23.260.710"), (23260710.1, "23.260.710,1"), (0.3, "0,3"),
                                             (81288871.136, "81.288.871,14"), (14540000, "14.540.000")])
def test_vn_number_has_no_dangling_comma(value, expected):
    from kpi_agent.backend.queries import _vn_number
    assert _vn_number(value) == expected


def test_employee_result_counts_paid_kpis_like_team_table(client):
    # 10/11 (B, chạy thử Capitol câu 2): "có thưởng ở 6 KPI" bị Rào chặn vì 6 là số mô hình tự đếm ⇒ công cụ một NV trả sẵn số KPI có tiền (code đếm),
    # BẰNG đúng ô `kpis_paid` của bảng đội cho mọi NV
    team = {row["employee_id"]: row["kpis_paid"] for row in client.get("/api/team-bonus").json()["employees"]}
    for code, paid in team.items():
        body = client.get(f"/api/employees/{code}").json()
        assert body["kpis_paid"] == paid == sum(1 for line in body["kpis"] if line["amount"] > 0), code
