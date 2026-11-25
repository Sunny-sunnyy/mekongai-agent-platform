"""Mọi FILE đầu vào của workflow phải nằm trong dấu vân tay (`Block.file_params`, `runner.input_files`, `main._files_sha256`).

Lỗ đã chạy ra thật trước khi vá: dấu vân tay chỉ băm ô `path` ⇒ bỏ một nửa điểm bán trong file danh sách tuyến (`mcp_path` của khối read_raw) làm
số trong bộ nhớ đổi 126.660.000 ⇒ 34.720.000 mà run_id, `data_version`, `files_sha256` và trạng thái "đã soát" đứng yên.
Ba ca đầu không cần dữ liệu thật; ca cuối bật backend RAW 31/10 thật hai lần (kho tạm, không mở cổng)."""
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kpi_agent.workflow import runner
from kpi_agent.workflow.blocks import BLOCKS

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "sample_data/thang_10/mcp_trang_sep_3110.json"
NEEDED = [ROOT / "sample_data/thang_10/RAW DATA.xlsx", ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json", ROUTES]
SYS_KEY = "khoa-thu-trong-test-0123456789"


def test_every_path_like_param_is_declared_as_input_file():
    # Người thêm khối / ô đọc file mà quên khai `file_params` ⇒ file đó lại nằm ngoài dấu vân tay (đúng lỗi 12/11). Tên ô chỉ dùng Ở TEST này để nhắc;
    # code không dò theo tên.
    for name, block in BLOCKS.items():
        path_like = {key for key in block.params if key == "path" or key.endswith("_path")}
        assert path_like <= set(block.file_params), f"khối {name}: ô {sorted(path_like - set(block.file_params))} trông như đường file mà chưa khai file_params"
        assert set(block.file_params) <= set(block.params), f"khối {name}: file_params có ô không tồn tại"


def test_shipped_workflows_list_their_input_files():
    raw = runner.input_files(runner.load_workflow(ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml")[0])
    assert [Path(value).name for value in raw] == ["rules_2026_10_raw.yaml", "Chi tieu NVBH.xlsx", "RAW DATA.xlsx", "mcp_trang_sep_3110.json"]
    old = runner.input_files(runner.load_workflow(ROOT / "kpi_agent/workflows/kpi_thuong_nvbh.yaml")[0])
    assert [Path(value).name for value in old] == ["rules_2026_10.yaml", "Chi tieu NVBH.xlsx", "Tracking.xlsx"]   # đường Tracking vẫn 3 file


def test_run_fingerprint_follows_the_route_file_content(tmp_path):
    files = {name: tmp_path / name for name in ("rules.yaml", "targets.xlsx", "raw.xlsx", "routes.json")}
    for path in files.values():
        path.write_text("nội dung đầu", encoding="utf-8")
    workflow = {"workflow": "thu", "edges": [], "nodes": [
        {"id": "rules", "type": "load_rules", "params": {"path": str(files["rules.yaml"])}},
        {"id": "targets", "type": "read_targets", "params": {"path": str(files["targets.xlsx"])}},
        {"id": "tracking", "type": "read_raw", "params": {"path": str(files["raw.xlsx"]), "mcp_path": str(files["routes.json"])}}]}
    run = {"cutoff": date(2026, 10, 31)}
    first = runner.input_fingerprint(workflow, run)
    assert set(first["files_sha256"]) == {str(path) for path in files.values()}
    assert runner.input_fingerprint(workflow, run)["sha256"] == first["sha256"]          # file không đổi ⇒ cùng dấu vân tay
    files["routes.json"].write_text("nội dung đầu, đổi một dòng", encoding="utf-8")
    changed = runner.input_fingerprint(workflow, run)
    assert changed["sha256"] != first["sha256"]
    assert {value for value in first["files_sha256"] if first["files_sha256"][value] != changed["files_sha256"][value]} == {str(files["routes.json"])}
    files["routes.json"].unlink()
    assert runner.input_fingerprint(workflow, run)["files_sha256"][str(files["routes.json"])] == "không có file"


def test_unreadable_input_file_is_marked_not_raised(tmp_path):
    from kpi_agent.backend import main
    files = {name: tmp_path / name for name in ("rules.yaml", "targets.xlsx", "raw.xlsx", "routes.json")}
    for path in files.values():
        path.write_text("x", encoding="utf-8")
    workflow = {"workflow": "thu", "edges": [], "nodes": [
        {"id": "rules", "type": "load_rules", "params": {"path": str(files["rules.yaml"])}},
        {"id": "targets", "type": "read_targets", "params": {"path": str(files["targets.xlsx"])}},
        {"id": "tracking", "type": "read_raw", "params": {"path": str(files["raw.xlsx"]), "mcp_path": str(files["routes.json"])}}]}
    assert list(main._files_sha256(workflow)) == ["rules.yaml", "targets.xlsx", "raw.xlsx", "routes.json"]   # thứ tự vai rồi file khác
    files["routes.json"].unlink()
    assert main._files_sha256(workflow)["routes.json"] == "không có file"
    assert main._data_files(main._files_sha256(workflow), files["rules.yaml"]).keys() == {"targets.xlsx", "raw.xlsx", "routes.json"}


@pytest.mark.skipif(not all(path.exists() for path in NEEDED), reason="không có dữ liệu thật trên máy này")
def test_changed_route_file_makes_a_new_unreviewed_run(tmp_path):
    from kpi_agent.backend import main
    route_copy = tmp_path / "mcp_ban_chep.json"
    shutil.copy(ROUTES, route_copy)
    text = (ROOT / "kpi_agent/workflows/kpi_thuong_nvbh_raw.yaml").read_text(encoding="utf-8")
    assert "sample_data/thang_10/mcp_trang_sep_3110.json" in text
    workflow_copy = tmp_path / "kpi_thuong_nvbh_raw.yaml"
    # File đơn hàng thật KHÔNG bị đụng: workflow trỏ tới một ĐƯỜNG LINK tạm cùng tên — ca "file đơn hàng biến mất" chỉ đổi tên đường link
    raw_link = tmp_path / "RAW DATA.xlsx"
    raw_link.symlink_to(NEEDED[0])
    assert "sample_data/thang_10/RAW DATA.xlsx" in text
    workflow_copy.write_text(text.replace("sample_data/thang_10/mcp_trang_sep_3110.json", str(route_copy))
                             .replace("sample_data/thang_10/RAW DATA.xlsx", str(raw_link)), encoding="utf-8")

    def start(review: bool) -> dict:
        with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
            meta = client.get("/api/meta").json()
            if review:
                assert client.post(f"/api/runs/{meta['current_run_id']}/review", json={"reviewer": "Giám sát thử"}).status_code == 200
                meta = client.get("/api/meta").json()
                # File tuyến biến mất KHI ĐANG CHẠY (người kiểm độc lập 12/11: bản vá đầu trả HTTP 500) ⇒ lời từ chối rõ, không đường dẫn máy
                for gone in (route_copy, raw_link):                                      # file tuyến, rồi file đơn hàng (đường link tạm)
                    hidden = gone.with_name("da_doi_ten")
                    gone.rename(hidden)
                    try:
                        lost = client.get(f"/api/runs/{meta['current_run_id']}/verify")
                        lost_meta = client.get("/api/meta")
                    finally:
                        hidden.rename(gone)
                    assert lost.status_code == 200 and lost.json()["inputs_match"]["data"] is False
                    assert lost.json()["refused"].startswith(f"không tính lại được — không đọc được file {gone.name};")   # chỉ TÊN file
                    assert str(tmp_path) not in lost.text and "Errno" not in lost.text and "/home/" not in lost.text
                    assert lost_meta.status_code == 200                                  # /api/meta dùng số băm lúc bật — không đọc lại file
                assert client.get(f"/api/runs/{meta['current_run_id']}/verify").json()["inputs_match"]["data"] is True   # trả file về ⇒ lại khớp
            table = client.get("/api/system/bonus-table", headers={"X-Kpi-Sys-Key": SYS_KEY}).json()
            in_memory = sum(bonus["business_salary"] for bonus in client.app.state.ctx.bonuses.values())
            return {**{key: meta[key] for key in ("current_run_id", "status", "data_version", "files_sha256")},
                    "table_total": table["table"]["team_total"], "in_memory_total": in_memory}

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("KPI_STORE", str(tmp_path / "kpi_store.sqlite"))
        mp.setenv("KPI_SYS_KEY", SYS_KEY)
        mp.setattr(main, "WORKFLOW", workflow_copy)
        mp.setattr(main, "CUTOFF", date(2026, 10, 31))
        reviewed = start(review=True)
        routes = json.loads(route_copy.read_text(encoding="utf-8"))
        routes["mcp"] = {sm: dict(list(outlets.items())[: len(outlets) // 2]) for sm, outlets in routes["mcp"].items()}
        route_copy.write_text(json.dumps(routes, ensure_ascii=False), encoding="utf-8")
        after = start(review=False)

    assert reviewed["status"] == "da_soat" and reviewed["in_memory_total"] == reviewed["table_total"] == 152_360_000
    assert sorted(reviewed["files_sha256"]) == ["Chi tieu NVBH.xlsx", "RAW DATA.xlsx", "mcp_ban_chep.json", "rules_2026_10_raw.yaml"]
    assert after["in_memory_total"] != reviewed["in_memory_total"]                       # bỏ nửa tuyến ⇒ số phải đổi (không thì ca này không thử được gì)
    assert after["current_run_id"] != reviewed["current_run_id"] and after["status"] != "da_soat"   # bản tính MỚI, chưa soát
    assert after["data_version"] != reviewed["data_version"]
    assert after["files_sha256"]["mcp_ban_chep.json"] != reviewed["files_sha256"]["mcp_ban_chep.json"]
    assert after["table_total"] == after["in_memory_total"]                              # bảng theo run_id và số trong bộ nhớ lại là MỘT


def test_stopped_rerun_reason_names_the_block_not_the_machine_path(tmp_path):
    # Lượt chạy THẬT bị dừng vì file luật không có ⇒ lời dừng của bộ chạy chứa đường dẫn tuyệt đối; lời cho bên ngoài (verify) chỉ được nêu khối dừng
    from kpi_agent.backend import main
    missing = tmp_path / "khong_co" / "rules.yaml"
    workflow = {"workflow": "thu", "edges": [], "nodes": [{"id": "rules", "type": "load_rules", "params": {"path": str(missing)}}]}
    result = runner.run_workflow(workflow, {}, {})
    assert result.status == "stopped" and str(tmp_path) in " ".join(result.errors)        # lời gốc CÓ đường dẫn — đúng thứ không được ra ngoài
    reason = main._stopped_reason(result)
    assert reason == "workflow không chạy xong ở khối rules (chi tiết lỗi chỉ xem trên máy chạy)"
    assert str(tmp_path) not in reason and "Errno" not in reason and "/" not in reason


def test_unreadable_but_existing_input_file_is_marked_not_raised(tmp_path):
    from kpi_agent.backend import main
    folder_as_file = tmp_path / "la_thu_muc.json"      # có tên đó nhưng là THƯ MỤC ⇒ đọc ném IsADirectoryError (một kiểu OSError khác "không có file")
    folder_as_file.mkdir()
    assert main._sha256_or_missing(folder_as_file) == "không có file"
