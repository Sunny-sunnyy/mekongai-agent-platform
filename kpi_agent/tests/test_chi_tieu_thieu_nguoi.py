"""File chỉ tiêu tải lên THIẾU người của đội (20/11/2026): trước đó ra HTTP 500 — backend lấy "đội" từ toàn bộ bảng mã che còn số chỉ được tính cho
người có trong file chỉ tiêu, và cửa tải lên chỉ kiểm một chiều (mã trong file phải có mã che). Nay: từ chối 422 có lời + mã `doi_thieu_nguoi`.

Ca thật: file chỉ tiêu tháng 10 bỏ mọi dòng của một / hai người ⇒ 422, lời nêu SỐ người, không nêu mã / tên; file đủ 14 người vẫn qua.
Ca lúc BẬT: file chỉ tiêu của workflow thiếu một người ⇒ backend không bật, bằng lời do code viết (trước: KeyError thô kèm mã thật).
"""
import io
import json
from datetime import date
from pathlib import Path

import openpyxl
import pytest
import yaml
from fastapi.testclient import TestClient

from kpi_agent.core.load_data import load_targets

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
MASK = ROOT / "kpi_agent/data/ma_che_ten.json"
NEEDED = [TARGETS, MASK, ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json",
          ROOT / "kpi_agent/data/chi_tieu_kokomi_3110.json"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
SYS_KEY = "khoa-thu-trong-test-0123456789"
BIN = {"X-Kpi-Sys-Key": SYS_KEY, "Content-Type": "application/octet-stream"}
RAW_WORKFLOW = ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml"


def targets_without(count: int) -> bytes:
    """File chỉ tiêu thật, bỏ mọi dòng của `count` người đầu tiên (theo thứ tự mã)."""
    dropped = {str(code) for code in sorted(load_targets(TARGETS)["employees"])[:count]}
    source = openpyxl.load_workbook(TARGETS, read_only=True, data_only=True)
    rows = list(source["Export"].iter_rows(values_only=True))
    source.close()
    at = rows[0].index("SM code")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Export"
    for row in [rows[0], *(row for row in rows[1:] if str(row[at]) not in dropped)]:
        sheet.append(list(row))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from kpi_agent.backend import main
    folder = tmp_path_factory.mktemp("thieu_nguoi")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(folder / "kpi_store.sqlite"))
        mp.setenv("KPI_UPLOAD_DIR", str(folder / "uploads"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", RAW_WORKFLOW)
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        with TestClient(main.app, base_url="http://127.0.0.1:8110", raise_server_exceptions=False) as test_client:
            yield test_client


def post(client, body: bytes):
    return client.post("/api/system/upload", params={"loai": "chi_tieu", "ky": "2026-10"}, content=body, headers=BIN)


@pytest.mark.parametrize("count", [1, 2])
def test_targets_file_missing_team_members_is_refused_with_code_not_500(client, count):
    before = client.get("/api/meta").json()["latest_data_version"]
    response = post(client, targets_without(count))
    assert response.status_code == 422, response.text[:200]
    body = response.json()
    assert body["code"] == "doi_thieu_nguoi" and f"thiếu {count} người" in body["refused"]
    # rào U1: lời không lặp mã che, mã thật hay tên của bất kỳ ai trong đội
    mask = json.loads(MASK.read_text(encoding="utf-8"))["map"]
    names = [str(info["name"]) for info in load_targets(TARGETS)["employees"].values()]
    private = [*mask, *map(str, mask.values()), *names]
    assert not [text for text in private if text and text in body["refused"]] and "NV0" not in body["refused"]
    assert client.get("/api/meta").json()["latest_data_version"] == before          # không sinh bản dữ liệu nào


def test_full_team_targets_file_still_accepted(client):
    response = post(client, TARGETS.read_bytes())
    assert response.status_code == 200, response.text[:200]
    assert "refused" not in response.json()


def test_startup_with_targets_file_missing_a_member_stops_with_written_reason(tmp_path, monkeypatch):
    """Lúc BẬT: file chỉ tiêu của workflow thiếu một người có mã che ⇒ backend không bật, lời do code viết, chỉ nêu SỐ người.
    Khi việc "đội của một bản = người trong file chỉ tiêu của bản đó" được làm (đổi `queries.py`), ca này đổi kỳ vọng thành bật được với đội 13 người."""
    from kpi_agent.backend import main
    short = tmp_path / "chi_tieu_thieu.xlsx"
    short.write_bytes(targets_without(1))
    workflow = yaml.safe_load(RAW_WORKFLOW.read_text(encoding="utf-8"))
    next(node for node in workflow["nodes"] if node["type"] == "read_targets")["params"]["path"] = str(short)
    file = tmp_path / "wf_thieu.yaml"
    file.write_text(yaml.safe_dump(workflow, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))
    monkeypatch.setenv("KPI_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(main, "WORKFLOW", file)
    monkeypatch.setattr(main, "CUTOFF", date(2026, 10, 31))
    with pytest.raises(RuntimeError, match="backend KHÔNG bật — file chỉ tiêu thiếu 1 người của đội") as caught:
        with TestClient(main.app, base_url="http://127.0.0.1:8110"):
            pass
    dropped = str(sorted(load_targets(TARGETS)["employees"])[0])
    assert dropped not in str(caught.value)                                           # không lặp mã của người bị thiếu
