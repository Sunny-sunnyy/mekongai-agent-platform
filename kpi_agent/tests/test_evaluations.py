"""Tab "Đánh giá" (`backend/evaluations.py`, `scripts/dung_so_danh_gia.py`, `GET /api/evaluations`) — thư mục kết quả tự dựng nhỏ + ca xấu + 1 ca thật."""
import json
from pathlib import Path

import pytest
import yaml

from kpi_agent.backend import evaluations
from kpi_agent.backend.evaluations import KEEP, ROLL_BACK, UNDECIDED

ROOT = Path(__file__).resolve().parents[2]
NEEDED = [ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx", ROOT / "sample_data/thang_10/Tracking.xlsx", ROOT / "kpi_agent/data/ma_che_ten.json"]
needs_data = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="không có dữ liệu thật trên máy này")
EIGHT = r"^\*\*Kết quả: (\d+) / 8 đúng nội dung, (\d+) số tiền sai, người hỏi nhận (\d+) / 8"


def _t4(folder: Path, tool_pass=20, verdict_ok=True):
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"git_head": "abc12345", "model": "mo-hinh-thu", "reasoning": "low", "cutoff": "2026-10-20"}), encoding="utf-8")
    (folder / "grades.json").write_text(json.dumps({"summary": {
        "tool_ok": {"pass": tool_pass, "of": 20}, "args_ok": {"pass": 13, "of": 13}, "unsourced_final": 0, "false_block": 0, "ran": 20, "total": 20,
        "verdict": {"tool_ok ≥ 19/20": verdict_ok, "0 số không nguồn": True}}}), encoding="utf-8")
    (folder / "answers.jsonl").write_text("\n".join(json.dumps({"usd": 0.001, "seconds": 4.0}) for _ in range(20)), encoding="utf-8")


def _m3(path: Path, passed):
    path.write_text(json.dumps({"tong": {"dat": passed, "n": 20, "nguong": 19, "truot": [8, 9][: 20 - passed], "tien": 0.0125, "ket_luan": "x"}}), encoding="utf-8")


def _ledger(tmp_path: Path, versions: list[dict]) -> Path:
    path = tmp_path / "so.yaml"
    path.write_text(yaml.safe_dump({"versions": versions}, allow_unicode=True), encoding="utf-8")
    return path


def test_numbers_come_from_files_with_sources_and_three_labels(tmp_path):
    _t4(tmp_path / "luot_a")
    _t4(tmp_path / "luot_b", tool_pass=17, verdict_ok=False)
    _m3(tmp_path / "m3_a.json", 20)
    _m3(tmp_path / "m3_b.json", 18)
    (tmp_path / "cham.md").write_text("# chấm\n\n**Kết quả: 8 / 8 đúng nội dung, 0 số tiền sai, người hỏi nhận 8 / 8** x\n"
                                      "**Kết quả: 7 / 8 đúng nội dung, 1 số tiền sai, người hỏi nhận 8 / 8** y\n", encoding="utf-8")
    eight = lambda n: {"file": str(tmp_path / "cham.md"), "pattern": EIGHT, "occurrence": n}
    ledger = _ledger(tmp_path, [
        {"id": "giữ", "commit": "HEAD", "t4_run": str(tmp_path / "luot_a"), "m3_file": str(tmp_path / "m3_a.json"), "capitol_8": eight(1)},
        {"id": "quay lại", "commit": "HEAD", "t4_run": str(tmp_path / "luot_a"), "m3_file": str(tmp_path / "m3_a.json"), "capitol_8": eight(2)},
        {"id": "chưa kết luận", "commit": "HEAD", "t4_run": str(tmp_path / "luot_b"), "m3_file": str(tmp_path / "m3_b.json"), "capitol_8": eight(1)},
    ])
    keep, back, undecided = evaluations.build(ROOT, ledger)["versions"]
    assert keep["label"] == KEEP and back["label"] == ROLL_BACK and undecided["label"] == UNDECIDED
    assert "1 câu tiền sai" in back["label_reason"]                              # tiền sai xét TRƯỚC: mọi chỉ số khác đạt vẫn QUAY LẠI
    assert keep["capitol_8"]["money_wrong"] == {"value": 0, "source": "cham.md:3"} and back["capitol_8"]["money_wrong"]["source"] == "cham.md:4"
    assert keep["m3"]["passed"]["value"] == "20 / 20" and undecided["m3"]["passed"]["value"] == "18 / 20" and undecided["m3"]["failed_questions"] == [8, 9]
    t4 = keep["t4"]
    assert t4["tool_ok"]["value"] == "20 / 20" and t4["model"]["value"] == "mo-hinh-thu" and t4["usd"]["value"] == 0.02 and t4["seconds_per_question"]["value"] == 4.0
    assert undecided["t4"]["verdict_all"]["value"] is False and undecided["t4"]["verdict_failed"] == ["tool_ok ≥ 19/20"]
    assert keep["changes"] is None and back["changes"]["commit_count"] == 0     # cùng commit ⇒ không đổi gì


def test_missing_sources_give_none_never_guess_and_never_keep(tmp_path):
    # Ca xấu: lượt đo đã mất, file chấm mất, mẫu không khớp dòng nào ⇒ từng số None + lý do "chưa có …", không sập, nhãn không bao giờ GIỮ
    (tmp_path / "cham.md").write_text("Kết quả: tám trên tám\n", encoding="utf-8")
    ledger = _ledger(tmp_path, [{"id": "mất nguồn", "commit": "HEAD", "t4_run": str(tmp_path / "khong_co"), "m3_file": str(tmp_path / "khong_co.json"),
                                 "capitol_8_dir": str(tmp_path / "khong_co_8"), "capitol_8": {"file": str(tmp_path / "cham.md"), "pattern": EIGHT}},
                                {"id": "không ghi nguồn", "commit": "khong-co-commit-nay"}])
    lost, bare = evaluations.build(ROOT, ledger)["versions"]
    for row in (lost, bare):
        assert row["label"] == UNDECIDED and row["label_reason"].startswith("chưa có số")
        assert all(cell["value"] is None and cell["source"].startswith("chưa có") for cell in row["capitol_8"].values())
        assert row["m3"]["passed"]["value"] is None and row["m3_reading"]["value"] is None and row["sealed_set_leak"]["value"] is None
    assert lost["t4"]["tool_ok"]["value"] is None and lost["t4"]["usd"]["value"] is None and bare["t4"] is None
    assert "mẫu tìm không khớp" in lost["capitol_8"]["money_wrong"]["source"]
    assert bare["changes"]["commit_count"] is None and bare["changes"]["diffstat"].startswith("chưa có")   # commit lạ ⇒ không đọc được git, không sập


@pytest.mark.parametrize("text", ["", "versions: []", "versions:\n  - id: thiếu commit\n", "- không phải bảng"])
def test_broken_ledger_stops_and_script_writes_nothing(tmp_path, text):
    from kpi_agent.scripts import dung_so_danh_gia
    ledger, out = tmp_path / "so.yaml", tmp_path / "ra.json"
    ledger.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        evaluations.build(ROOT, ledger)
    assert dung_so_danh_gia.main(["--so", str(ledger), "--ra", str(out)]) == 1 and not out.exists()


def test_repo_ledger_names_every_run_folder_explicitly():
    # Bẫy 04/11: thư mục lượt đo có cả lượt kiểm cuối ⇒ sổ nguồn phải ghi rõ tên từng thư mục, không mẫu ngày / dấu sao, không trỏ vào bộ câu kiểm cuối
    ledger = yaml.safe_load((ROOT / "kpi_agent/config/so_danh_gia.yaml").read_text(encoding="utf-8"))
    for entry in ledger["versions"]:
        for key in ("t4_run", "m3_file", "capitol_8_dir"):
            value = entry.get(key, "")
            assert "*" not in value and "?" not in value and "giu_kin" not in value.lower() and "held" not in value.lower(), (entry["id"], key)


@needs_data
def test_api_reads_built_file_and_refuses_when_missing_or_broken(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from kpi_agent.backend import main
    monkeypatch.setenv("KPI_STORE", str(tmp_path / "store.sqlite"))
    built = tmp_path / "danh_gia.json"
    monkeypatch.setattr(main, "EVALUATIONS_FILE", built)
    with TestClient(main.app, base_url="http://127.0.0.1:8110") as client:
        assert client.get("/api/evaluations").json()["refused"] == "chưa có file số đánh giá dựng sẵn"   # không nêu lệnh chạy script (hộp không có scripts/)      # ca xấu: chưa dựng ⇒ lời từ chối, không sập
        built.write_text("{hỏng", encoding="utf-8")
        assert "hỏng" in client.get("/api/evaluations").json()["refused"]
        _t4(tmp_path / "luot_a")
        ledger = _ledger(tmp_path, [{"id": "một bản", "commit": "HEAD", "t4_run": str(tmp_path / "luot_a")}])
        from kpi_agent.scripts import dung_so_danh_gia
        assert dung_so_danh_gia.main(["--so", str(ledger), "--ra", str(built)]) == 0
        body = client.get("/api/evaluations").json()
        assert body["versions"][0]["t4"]["tool_ok"]["value"] == "20 / 20" and body["built_at"] and "chưa đo nhiễu" in body["noise_note"]
        # nhãn tạm tính theo nguồn dữ liệu: đường sổ Tracking (mặc định của test) vẫn là "số chụp giữa tháng"
        meta = client.get("/api/meta").json()
        assert meta["data_source"] == "tracking" and "số chụp giữa tháng" in meta["label"]
