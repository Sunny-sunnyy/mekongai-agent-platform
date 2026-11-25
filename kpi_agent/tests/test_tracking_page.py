"""Trang theo dõi .html bản 1 (`backend/tracking_page.py`, `scripts/tao_trang_tracking.py`) — bảng giả nhỏ + ca xấu + 1 ca thật RAW 31/10."""
import json
import re
from pathlib import Path

import pytest

from kpi_agent.backend.tracking_page import TrackingPageError, render
from kpi_agent.backend.tracking_view import ranks
from kpi_agent.core.rules import KPI_ORDER

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "sample_data/thang_10/saved_resource.html"
RAW = ROOT / "sample_data/thang_10/RAW DATA.xlsx"
MASK = ROOT / "kpi_agent/data/ma_che_ten.json"
needs_data = pytest.mark.skipif(not all(p.exists() for p in (PAGE, RAW, MASK)), reason="không có dữ liệu thật trên máy này")


# Ô lệch trang theo dõi của đội trên dữ liệu thật 31/10. Mốc 7 (17/11 — chuẩn mới 16/11: trang sếp là đúng, lõi đổi theo trang): 0 ô lệch.
# Trước mốc 7 là 21 ô + ô "07" NV05 (KPI 2 cả 14 người · doanh số NV04 / NV05 / NV08 / NV14 · LPPC NV04 · PC_ON NV08 · Kokomi NV05) — lịch sử: git log -p.
DEVIATING_CELLS: set = set()


def _cell(value, amount, status="moc_thap"):
    return {"measure": "value", "pct": None, "value": value, "actual": value, "target": 10, "amount": amount, "status": status,
            "reached_tier": "Mức 1" if amount else None, "next_tier": None, "gap_to_next": 2, "gap_per_day_text": "1 mỗi ngày"}


def _board(codes=("NV01", "NV02")):
    employees = [{"employee_id": code, "business_salary": 1_000_000 * (i + 1), "cells": {kpi: _cell(5 + i, 100_000 * i) for kpi in KPI_ORDER}}
                 for i, code in enumerate(codes)]
    return {"employees": employees, "team_total": sum(e["business_salary"] for e in employees), "team_label": "Nam Trung Bộ",
            "working_days_total": 27, "working_days_left_after_cutoff": 0,
            "kpi_summary": {kpi: {"paid_count": 1, "full_count": 0} for kpi in KPI_ORDER}}


def _extra(codes=("NV01", "NV02")):
    items = [{"id": "T01", "title": "Thưởng 01: Bia", "label": "Đạt ≥20 thùng: 6.000đ/thùng"}]
    lines = [{"employee_id": code, "item": "T01", "actual": 20 + i, "amount": 6000 * (20 + i), "how": "x"} for i, code in enumerate(codes)]
    return {"items": items, "lines": lines, "employee_total": {l["employee_id"]: l["amount"] for l in lines}, "team_total": sum(l["amount"] for l in lines)}


META = {"period": "2026-10", "cutoff": "2026-10-31", "status_label": "TẠM TÍNH", "files_sha256": {"RAW DATA.xlsx": "a" * 64},
        "data_version": "b" * 64, "generated_at": "14:30 11/11/2026"}


def test_page_is_self_contained_escaped_and_marks_deviation():
    page = render(_board(), _extra(), META, deviations={("PC_BT", "NV02"): "trang đếm <khác>"}, names={"NV01": "<script>x</script>"})
    assert not re.search(r"https?://|src=|fetch\(|@import", page)           # 0 đường mạng: mở được không có mạng, không lộ gì ra ngoài
    assert "<script>x</script>" not in page and "&lt;script&gt;x&lt;/script&gt;" in page   # chữ người đặt (tên) luôn qua escape
    assert page.count("⚠ trang sếp tính khác: trang đếm &lt;khác&gt;") == 1   # đúng một ô lệch, đúng chỗ, có lý do
    assert "3.000.000" in page and "126.000" in page                        # tổng 8 KPI 1.000.000 + 2.000.000; thưởng thêm 120.000 + 126.000 (đúng tới đồng)


def test_ranks_ties_share_rank():
    assert ranks({"A": 5, "B": 7, "C": 7, "D": 1}) == {"B": 1, "C": 1, "A": 3, "D": 4}


def test_ranks_use_unrounded_values():
    # LPPC hai NV cùng IN 5,39 mà số thật khác ⇒ hạng theo số thật, không cùng hạng
    from kpi_agent.backend.tracking_view import kpi_ranks
    assert kpi_ranks({("LPPC", "NV05"): 5.3949, ("LPPC", "NV10"): 5.3912})["LPPC"] == {"NV05": 1, "NV10": 2}


@pytest.mark.parametrize("board, extra, match", [
    ({"refused": True, "reason": "kỳ khác"}, _extra(), "bảng đội không có số"),
    (_board(()), _extra(()), "0 nhân viên"),
    (_board(), "thưởng thêm không tính được", "thưởng thêm không có số"),
    (_board(), _extra(("NV01",)), "NV02 thiếu khoản thưởng thêm T01"),
])
def test_bad_inputs_stop_without_page(board, extra, match):
    # Ca xấu: dữ liệu thiếu / bị từ chối ⇒ dừng với lời rõ, không sinh trang rỗng
    with pytest.raises(TrackingPageError, match=match):
        render(board, extra, META)


def test_script_stops_and_writes_nothing_when_page_file_missing(tmp_path, monkeypatch):
    from kpi_agent.scripts import tao_trang_tracking
    monkeypatch.setattr(tao_trang_tracking, "backend_numbers", lambda cutoff: (_board(), _extra(), {**META, "raw_path": RAW, "raw_actual": {}, "to_real": {}}))
    out = tmp_path / "trang.html"
    assert tao_trang_tracking.main(["--ngay-chot", "2026-10-31", "--trang", str(tmp_path / "khong_co.html"), "--ra", str(out)]) == 1
    assert not out.exists()


@needs_data
def test_real_page_3110(tmp_path):
    from kpi_agent.scripts import tao_trang_tracking
    out = tmp_path / "trang.html"
    assert tao_trang_tracking.main(["--ngay-chot", "2026-10-31", "--trang", str(PAGE), "--ra", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert "152.360.000" in page and "19.026.300" in page                   # 8 KPI + Thưởng thêm = số backend (luật đơn hàng 11/11 + Meat 7.x 1.242.300)
    assert all(f">NV{n:02d}<" in page for n in range(1, 15))
    real_codes = json.loads(MASK.read_text(encoding="utf-8"))["map"].values()
    assert not any(code in page for code in real_codes)                     # mặc định 0 mã NV thật
    # Mốc 7: 0 ô lệch trang (xem DEVIATING_CELLS) — ô ⚠ + lý do vẫn được kiểm bằng ca tự dựng ở test_render_* phía trên
    assert page.count("⚠ trang sếp tính khác:") == len(DEVIATING_CELLS) == 0 and "chưa rõ lý do" not in page
    assert not re.search(r"https?://|src=", page)


@pytest.fixture
def raw_backend(tmp_path, monkeypatch):
    """Backend bật đường RAW ngày chốt 31/10 (như chay_ban1.sh), kho SQLite tạm."""
    from datetime import date
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 31))
    monkeypatch.setattr(main, "WORKFLOW", ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))

    def start(page):
        monkeypatch.setattr(main, "TRACKING_PAGE", str(page))
        return TestClient(main.app, base_url="http://127.0.0.1:8110")
    return start


@needs_data
def test_api_tracking_real_3110_and_bad_page(raw_backend, tmp_path):
    with raw_backend(PAGE) as client:
        body = client.get("/api/tracking").json()
    assert body["team_total"] == 152_360_000 and body["extra"]["team_total"] == 17_784_000 + 1_242_300 and body["compared_with_page"]
    assert body["deviations"] == []   # mốc 7: mọi ô khớp trang (trước: 21 ô + ô "07" NV05, có lý do theo từng ô — lịch sử git log -p)
    assert set(body["ranks"]) == set(KPI_ORDER) and set(body["extra"]["ranks"]) == {item["id"] for item in body["extra"]["items"]}
    assert {"T01", "T02", "T03", "T04", "T05", "T06"} <= set(body["extra"]["ranks"])   # từ 11/11 chiều có thêm các khoản Meat 07 / 7.x ⇒ không còn đúng 6 khoản
    real_codes = json.loads(MASK.read_text(encoding="utf-8"))["map"].values()
    assert not any(code in json.dumps(body, ensure_ascii=False) for code in real_codes)   # chỉ mã che
    # Ca xấu: file trang không phải trang của đội (ghép tên hỏng) ⇒ vẫn có số, nói rõ không so được, 0 ô lệch đoán bừa
    bad = tmp_path / "trang_khac.html"
    bad.write_text("<html>const D = {\"kpi\": [], \"bon\": [{\"title\": \"Thưởng 01\", \"unit\": \"thùng\", \"dec\": 0, \"rows\": [{\"n\": \"Không Ai\", \"a\": 1}]}]}</html>",
                   encoding="utf-8")
    with raw_backend(bad) as client:
        body = client.get("/api/tracking").json()
    assert body["team_total"] == 152_360_000 and not body["compared_with_page"] and body["deviations"] == []
    assert "_page_error" in body["known_reasons"]


# Kiểu xem "8 KPI" theo bố cục trang của đội (`tracking_view.page_view`) ----------
# Giữ sàn: người dùng so màn này với trang của đội — hạng / còn thiếu / % phải đúng nghĩa của trang (tới 100 % CHỈ TIÊU), không lẫn với "tới mốc thưởng kế".

def test_page_view_small_team_rank_shortfall_and_time_progress():
    from datetime import date
    from types import SimpleNamespace
    from kpi_agent.backend.tracking_view import page_view
    from kpi_agent.core.compute_kpi import KpiResult
    tiers = [{"min": 100, "label": "Đạt 100%", "printed_amount": 1_000_000}]
    rules = {"kpis": {kpi: {"measure": "pct_of_target", "tiers": tiers} for kpi in KPI_ORDER}}
    result = lambda kpi, target, actual: KpiResult(kpi, target, actual, actual, 1, "")
    # SM1 đạt 50 %, SM2 đạt 120 % ở mọi chỉ tiêu; đã qua 6 / 10 ngày ⇒ tiến độ thời gian 60 %
    ctx = SimpleNamespace(to_real={"NV01": "SM1", "NV02": "SM2"}, regions={"SM1": "CD - Miền thử", "SM2": "CD - Miền thử"}, rules=rules,
                          days_total=10, days_left=4, cutoff=date(2026, 10, 7),
                          results={"SM1": {kpi: result(kpi, 200, 100) for kpi in KPI_ORDER}, "SM2": {kpi: result(kpi, 100, 120) for kpi in KPI_ORDER}})
    view = page_view(ctx)
    assert (view["days_done"], view["days_total"], view["time_progress_pct"], view["per_day_note"]) == (6, 10, 60.0, None)
    item = view["kpis"]["PC_BT"]
    assert item["page_title"] == "KPI 2: Số đơn đạt MOQ" and item["tiers"] == [{"region": None, "label": "Đạt 100%", "amount": 1_000_000}]
    first, second = item["rows"]
    assert (first["employee_id"], first["rank"], first["pct_to_target"], first["shortfall"], first["per_day"], first["on_track"]) == ("NV02", 1, 120.0, 0, 0, True)
    assert (second["employee_id"], second["rank"], second["pct_to_target"], second["shortfall"], second["per_day"], second["on_track"]) == ("NV01", 2, 50.0, 100, 25, False)
    assert item["team"] == {"target": 300, "actual": 220, "pct_to_target": 73.33, "pct_rounded": 73, "on_track": True}   # 220 / 300 = 73,33 % (làm tròn XUỐNG)
    assert "amount" not in first and "business_salary" not in view      # kiểu xem này không có tiền thưởng


@needs_data
def test_api_tracking_page_view_real_3110(raw_backend):
    with raw_backend(PAGE) as client:
        body = client.get("/api/tracking").json()
        board = client.get("/api/team-board").json()
        tool = client.get("/api/extra-bonus").json()
    view = body["page_view"]
    assert (view["cutoff"], view["days_done"], view["days_total"], view["time_progress_pct"]) == ("2026-10-31", 27, 27, 100.0) and view["per_day_note"]
    assert list(view["kpis"]) == list(KPI_ORDER) and all(len(item["rows"]) == 14 for item in view["kpis"].values())
    # số tổng của trang của đội ở 3 chỉ tiêu KHÔNG có ô lệch (đọc từ `tot` của trang 13/11): KPI 4 1.999 / 2.113, KPI 5 1.008 / 1.089, KPI 6 364 / 393
    assert [(view["kpis"][kpi]["team"]["target"], view["kpis"][kpi]["team"]["actual"]) for kpi in ("ASO_ALL", "ASO_Focus", "ASO_Focus_2")] == [(1999, 2113), (1008, 1089), (364, 393)]
    cells = {row["employee_id"]: row["cells"] for row in board["employees"]}
    for kpi, item in view["kpis"].items():
        assert [row["rank"] for row in item["rows"]] == sorted(row["rank"] for row in item["rows"])            # dòng đã xếp theo hạng
        for row in item["rows"]:
            cell = cells[row["employee_id"]][kpi]
            assert row["actual"] == cell["actual"] and row["target"] == cell["target"]                          # cùng số với bảng đội
            if cell["pct"] is not None:
                assert row["pct_to_target"] == cell["pct"]                                                     # % đúng y số công cụ / bảng đội
            assert row["per_day"] == row["shortfall"] and (row["shortfall"] == 0) == (row["actual"] >= row["target"])   # hết ngày ⇒ cần / ngày = còn thiếu
            assert row["on_track"] == (row["pct_to_target"] >= 100)                                             # tiến độ thời gian 100 %
    assert body["team_total"] == 152_360_000                                                                    # thêm khoá, số cũ không đổi
    # Thưởng thêm cùng khuôn: tổng của trang của đội ở 5 khoản đếm (đọc `tot` của trang 13/11) + mỗi dòng mang tiền / cách tính của mình
    extra = view["extra"]
    # + khoản "07" (điều kiện ngành thịt chế biến — trang của đội có, chỉ %, không tiền) đứng ngay trước khoản 7.x đầu tiên
    assert set(extra) == {item["id"] for item in body["extra"]["items"]} | {"07"}
    order = list(extra)
    assert order[order.index("07") + 1] == "T071" and order[order.index("07") - 1] == "T06" and extra["07"]["team"]["target"] == 808_780_000   # chỉ tiêu 07 = trang (808,78 tr)
    assert all("amount" not in row and "how" not in row for row in extra["07"]["rows"]) and len(extra["07"]["rows"]) == 14
    # khoản 07: trước mốc 7 khác trang ở NV05 (hoá đơn trễ); mốc 7 N2 cổng 07 cộng dòng trễ như trang ⇒ 0 ô lệch
    assert [d for d in body["deviations"] if d["key"] == "07"] == []
    # khoá gate_progress chỉ phục vụ trang: không có trong kết quả công cụ AI (đường chat) — số cấp ngoài dòng nhân viên không tới mô hình
    # (so theo mã khoản của từng mục — so chuỗi "07" trên cả JSON trúng nhầm mã NV07)
    assert "gate_progress" not in json.dumps(tool, ensure_ascii=False) and "07" not in {item["item"] for item in tool["items"]}
    assert [(extra[key]["team"]["target"], extra[key]["team"]["actual"]) for key in ("T01", "T02", "T03", "T04", "T05")] == [(280, 724), (280, 74), (210, 249), (70, 59), (140, 393)]
    assert sum(row.get("amount", 0) for item in extra.values() for row in item["rows"]) == body["extra"]["team_total"] == 19_026_300
    assert all(row["how"] for key, item in extra.items() if key != "07" for row in item["rows"]) and extra["T06"]["money"] and not extra["T01"]["money"]
