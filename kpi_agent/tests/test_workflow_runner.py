"""Bước 1 S2 (mục 3–4): bộ chạy workflow — kiểm file trước, chạy lần lượt, dừng sớm, che mã, dấu vân tay đầu vào.

Không mock: chạy trên file workflow thật + dữ liệu thật tháng 10. Bản sai của file workflow sinh trong bộ nhớ; bản sao chỉ tiêu thiếu KPI ghi vào thư mục tạm."""
import copy
import dataclasses
import hashlib
import json
from datetime import date
from pathlib import Path

import openpyxl
import yaml
import pytest

from kpi_agent.core.check_data import check_data
from kpi_agent.core.compute_bonus import employee_bonus
from kpi_agent.core.compute_kpi import compute_actuals
from kpi_agent.core.load_data import load_targets, load_tracking
from kpi_agent.core.rules import load_rules
from kpi_agent.workflow import runner
from kpi_agent.workflow.blocks import run_block

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "kpi_agent/workflows/kpi_thuong_nvbh.yaml"
TARGETS = ROOT / "sample_data/thang_10/Chi tieu NVBH.xlsx"
TRACKING = ROOT / "sample_data/thang_10/Tracking.xlsx"
CUTOFF = date(2026, 10, 20)
needs_data = pytest.mark.skipif(not (TARGETS.exists() and TRACKING.exists()), reason="không có dữ liệu thật trên máy này")


@pytest.fixture(scope="module")
def workflow():
    return runner.load_workflow(WORKFLOW)


@pytest.fixture(scope="module")
def run_20(workflow):
    # 10/11 (bước 3): workflow chính thêm duyet (người duyệt) + xuat (xuất file) sau compute_bonus ⇒ mở cổng để chạy hết chuỗi (cổng không đổi số — test_workflow_review_gate.py)
    return runner.run_workflow(workflow[0], {"cutoff": CUTOFF}, approved_gates=runner.gate_ids(workflow[0]))


def _digest(value) -> str:
    # So dict theo mã NV THẬT bằng mã băm: assert hỏng thì pytest in hai mã băm, không in mã NV thật (lần assert đầu đã in ra)
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _edge(wf, to_node, to_port):
    return next(edge for edge in wf["edges"] if (edge["to_node"], edge["to_port"]) == (to_node, to_port))


# kiểm file trước khi chạy (W4 + các ca) ----------
def test_real_workflow_file_is_valid(workflow):
    assert runner.check_workflow(workflow[0]) == []


def _bad_unknown_type(wf):
    wf["nodes"].append({"id": "la", "type": "khoi_khong_co"})


def _bad_duplicate_id(wf):
    wf["nodes"].append(copy.deepcopy(wf["nodes"][0]))


def _bad_from_port(wf):
    wf["edges"][0]["from_port"] = "cong_khong_co"


def _bad_to_port(wf):
    wf["edges"][0]["to_port"] = "cong_khong_co"


def _bad_reverse_edge(wf):
    # dây đi từ khối đứng SAU về khối đứng TRƯỚC: kpi ⇒ check (check đứng trước kpi trong file)
    wf["edges"].append({"from_node": "kpi", "from_port": "results", "to_node": "check", "to_port": "targets"})
    wf["edges"].remove(_edge(wf, "check", "targets"))


def _bad_self_edge(wf):
    _edge(wf, "bonus", "results").update(from_node="bonus", from_port="bonuses")


def _bad_missing_wire(wf):
    wf["edges"].remove(_edge(wf, "bonus", "rules"))


def _bad_two_wires_one_port(wf):
    wf["edges"].append(copy.deepcopy(_edge(wf, "kpi", "rules")))


def _bad_edge_to_missing_node(wf):
    wf["edges"][0]["to_node"] = "khong_co"


def _bad_unknown_param(wf):
    wf["nodes"][-1]["params"] = {"npp_turnover": 130}      # sai tên ô


def _bad_param_type(wf):
    wf["nodes"][-1]["params"] = {"npp_turnover_pct": "130"}   # chữ, không phải số


def _bad_param_bool(wf):
    wf["nodes"][-1]["params"] = {"npp_turnover_pct": True}


def _bad_missing_required_param(wf):
    wf["nodes"][0]["params"] = {}


def _bad_extra_edge_field(wf):
    wf["edges"][0]["to_param"] = "targets"


def _bad_extra_top_field(wf):
    wf["entry_node_id"] = "rules"


def _bad_type_is_list(wf):
    wf["nodes"][4]["type"] = ["compute_kpi"]                 # S2 minor 2: từng ném TypeError


def _bad_edge_value_is_list(wf):
    wf["edges"][0]["to_node"] = ["check"]


def _bad_position_type(wf):
    wf["nodes"][0]["position"] = {"x": 1, "y": "2"}


def _bad_position_keys(wf):
    wf["nodes"][0]["position"] = {"x": 1, "y": 2, "z": 3}


def _bad_npp_out_of_range(wf):
    wf["nodes"][-1]["params"] = {"npp_turnover_pct": 1000}    # 04/11


BAD = {name[5:]: fn for name, fn in globals().items() if name.startswith("_bad_")}


@pytest.mark.parametrize("name", sorted(BAD))
def test_bad_file_rejected_before_any_block_runs(workflow, name):
    wf = copy.deepcopy(workflow[0])
    BAD[name](wf)
    assert runner.check_workflow(wf), name
    result = runner.run_workflow(wf, {"cutoff": CUTOFF}, to_mask={})
    assert (result.status, result.steps, result.outputs) == ("rejected", [], {})


def test_reverse_edge_message_names_the_order(workflow):
    wf = copy.deepcopy(workflow[0])
    _bad_reverse_edge(wf)
    assert any("dây ngược" in problem and "'kpi'" in problem for problem in runner.check_workflow(wf))


# ---------- chạy thật ----------
@needs_data
def test_run_equals_direct_core_chain(run_20):
    # W2 qua bộ chạy: cùng chuỗi build_context đang gọi hôm nay (backend/queries.py)
    rules, targets, tracking = load_rules(ROOT / "kpi_agent/config/rules_2026_10.yaml"), load_targets(TARGETS), load_tracking(TRACKING)
    results = compute_actuals(targets, tracking, rules)
    regions = {code: info["region"] for code, info in targets["employees"].items()}
    assert run_20.status == "ok" and [step["status"] for step in run_20.steps] == ["ok"] * 8
    assert _digest(run_20.outputs["kpi"]["results"]) == _digest(results)
    assert _digest(run_20.outputs["bonus"]["bonuses"]) == _digest({code: employee_bonus(by_kpi, rules, regions[code]) for code, by_kpi in results.items()})
    assert _digest(run_20.outputs["check"]["issues"]) == _digest(check_data(targets, tracking, CUTOFF))
    assert runner.team_total(run_20) == 55_700_000


@needs_data
def test_cutoff_is_used_issues_change_money_does_not(workflow, run_20):
    # 04/11: ngày chốt chỉ vào check_data (blocks.py) ⇒ (a) issues đổi · (b) ngày chốt vào dấu vân tay · (c) tiền + % giống hệt
    run_31 = runner.run_workflow(workflow[0], {"cutoff": date(2026, 10, 31)})
    issues_20, issues_31 = run_20.outputs["check"]["issues"], run_31.outputs["check"]["issues"]
    assert (len(issues_20), len(issues_31)) == (3, 4)
    added = [issue for issue in issues_31 if issue not in issues_20]
    assert len(added) == 1 and "không trước lần làm mới pivot" in added[0]["message"]
    assert _digest(run_31.outputs["kpi"]["results"]) == _digest(run_20.outputs["kpi"]["results"])
    assert _digest(run_31.outputs["bonus"]["bonuses"]) == _digest(run_20.outputs["bonus"]["bonuses"])
    assert (run_20.fingerprint["run"], run_31.fingerprint["run"]) == ({"cutoff": "2026-10-20"}, {"cutoff": "2026-10-31"})
    assert run_20.fingerprint["sha256"] != run_31.fingerprint["sha256"]


@needs_data
def test_npp_param_no_effect_on_october_data(workflow, run_20):
    # Dữ liệu giữa tháng 10: không NV nào > 103 % doanh số ⇒ đổi ô NPP KHÔNG đổi gì. Đây là quan sát, không chứng minh ô được dùng
    # phép chứng minh ở test dưới (đầu vào dựng tay).
    wf = copy.deepcopy(workflow[0])
    next(node for node in wf["nodes"] if node["type"] == "compute_bonus")["params"] = {"npp_turnover_pct": 130}   # 10/11 (bước 3): workflow chính thêm duyet (người duyệt) + xuat (xuất file) sau compute_bonus
    changed = runner.run_workflow(wf, {"cutoff": CUTOFF})
    assert _digest(changed.outputs["bonus"]["bonuses"]) == _digest(run_20.outputs["bonus"]["bonuses"])
    assert {bonus["over_target_bonus"] for bonus in run_20.outputs["bonus"]["bonuses"].values()} == {0}


@needs_data
def test_npp_param_reaches_core_and_changes_over_target_bonus(run_20):
    # Ô cài đặt nhận được phải có phép thử chứng minh nó đổi kết quả (chủ đề 17 mục 2.2b — lịch chạy Capitol nhận input_overrides mà không dùng).
    # Đầu vào DỰNG TAY (không phải số thật): một NV, doanh số = 110 % chỉ tiêu; còn lại là kết quả thật của lượt 20/10.
    code, by_kpi = next(iter(run_20.outputs["kpi"]["results"].items()))
    turnover = by_kpi["Turnover"]
    above = {code: {**by_kpi, "Turnover": dataclasses.replace(turnover, actual=turnover.target * 1.10)}}
    inputs = {"results": above, "rules": run_20.outputs["rules"]["rules"], "targets": run_20.outputs["targets"]["targets"]}
    def over(value):
        return run_block("compute_bonus", inputs, {"npp_turnover_pct": value})["bonuses"][code]["over_target_bonus"]
    assert over(None) is None                     # để trống = hôm nay: "thiếu dữ kiện"
    assert over(90) == 0                          # NPP < 100 % ⇒ không có
    assert over(130) > 0                          # NPP đủ ⇒ có tiền


@needs_data
def test_fingerprint_has_real_inputs(workflow, run_20):
    fp = run_20.fingerprint
    assert set(fp["files_sha256"]) == {"kpi_agent/config/rules_2026_10.yaml", "sample_data/thang_10/Chi tieu NVBH.xlsx",
                                       "sample_data/thang_10/Tracking.xlsx"}
    assert "không có file" not in fp["files_sha256"].values()
    assert runner.run_workflow(workflow[0], {"cutoff": CUTOFF}).fingerprint == fp       # cùng đầu vào ⇒ cùng dấu vân tay


def test_workflow_sha_ignores_position_and_comments_not_params(workflow):
    # 04/11: position là bố cục ⇒ kéo khối không đổi mã băm (S3: không tạo bản tính mới, không mất "Đã soát"); đổi một ô thì đổi
    base = runner.workflow_sha256(workflow[0])
    moved = copy.deepcopy(workflow[0])
    for index, node in enumerate(moved["nodes"]):
        node["position"] = {"x": 100 * index, "y": 40.5}
    assert runner.check_workflow(moved) == [] and runner.workflow_sha256(moved) == base
    commented, _ = runner.load_workflow(WORKFLOW)            # chú thích trong chữ file không vào mã băm logic
    assert runner.workflow_sha256(commented) == base
    edited = copy.deepcopy(workflow[0])
    edited["nodes"][-1]["params"] = {"npp_turnover_pct": 130}
    assert runner.workflow_sha256(edited) != base


# ---------- dừng sớm + che mã (W3 mức bộ chạy) ----------
@needs_data
def test_missing_kpi_stops_at_check_and_hides_real_codes(workflow, tmp_path):
    copy_path = tmp_path / "chi_tieu_thieu_1_kpi.xlsx"
    book = openpyxl.load_workbook(TARGETS)
    book["Export"].delete_rows(2)                  # bỏ một dòng (một KPI của một NV) ⇒ NV đó thiếu chỉ tiêu
    book.save(copy_path)
    wf = copy.deepcopy(workflow[0])
    next(node for node in wf["nodes"] if node["id"] == "targets")["params"]["path"] = str(copy_path)
    result = runner.run_workflow(wf, {"cutoff": CUTOFF})
    assert result.status == "stopped"
    assert {step["node"]: step["status"] for step in result.steps} == {
        "rules": "ok", "targets": "ok", "tracking": "ok", "check": "stopped", "kpi": "not_run", "bonus": "not_run",
        "duyet": "not_run", "xuat": "not_run"}
    assert set(result.outputs) == {"rules", "targets", "tracking"}       # không có kết quả tính nào sau chỗ dừng
    text = json.dumps([result.errors, result.steps], ensure_ascii=False)
    assert "thiếu chỉ tiêu" in text and "NV" in text
    real_codes = set(runner.load_mask())
    assert not any(code in text for code in real_codes) and not runner.REAL_CODE_PATTERN.search(text)


@needs_data
def test_missing_data_file_stops_at_read(workflow):
    wf = copy.deepcopy(workflow[0])
    next(node for node in wf["nodes"] if node["id"] == "tracking")["params"]["path"] = "kpi_agent/data/khong_co_file_nay.xlsx"
    result = runner.run_workflow(wf, {"cutoff": CUTOFF}, to_mask={})
    assert result.status == "stopped" and [step["status"] for step in result.steps] == ["ok", "ok", "stopped"] + ["not_run"] * 5
    assert result.fingerprint["files_sha256"]["kpi_agent/data/khong_co_file_nay.xlsx"] == "không có file"


def test_mask_codes_covers_codes_outside_the_mask_table():
    # mã lạ (vd trong Rawdata mà không có trong file chỉ tiêu) không có trong bảng che ⇒ lưới theo dạng mã
    assert runner.mask_codes("NV 12AB.CD3456 và 99XY.ZT12345 thiếu", {"12AB.CD3456": "NV03"}) == "NV NV03 và NV?? thiếu"


# ---------- dòng lệnh (lệnh C dùng để kiểm hộp đen) ----------
def test_cli_check_only_exit_codes(tmp_path, capsys):
    assert runner.main([str(WORKFLOW), "--check-only"]) == 0
    bad = tmp_path / "sai.yaml"
    bad.write_text(WORKFLOW.read_text(encoding="utf-8").replace("type: compute_kpi", "type: khoi_la"), encoding="utf-8")
    capsys.readouterr()
    assert runner.main([str(bad), "--check-only"]) == 2
    assert runner.main([str(bad)]) == 2
    out = capsys.readouterr().out
    assert "TỪ CHỐI" in out and "0 khối đã chạy" in out


# ---------- S2-2: khoá trùng · ngày chốt bắt buộc · file sai dạng · --json ----------
def test_duplicate_key_rejected(tmp_path):
    # S2 (major): safe_load lấy giá trị SAU, im lặng ⇒ bộ đọc riêng từ chối
    text = WORKFLOW.read_text(encoding="utf-8").replace("params: {npp_turnover_pct: null}", "params: {npp_turnover_pct: 130, npp_turnover_pct: null}")
    assert "npp_turnover_pct: 130, npp_turnover_pct: null" in text
    bad = tmp_path / "trung.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(runner.WorkflowFileError, match="ghi 2 lần"):
        runner.load_workflow(bad)
    assert runner.main([str(bad), "--check-only"]) == 2


def test_duplicate_node_key_inside_node_rejected(tmp_path):
    bad = tmp_path / "trung_type.yaml"
    bad.write_text(WORKFLOW.read_text(encoding="utf-8").replace("{id: kpi,      type: compute_kpi}", "{id: kpi, type: compute_kpi, type: check_data}"),
                   encoding="utf-8")
    with pytest.raises(runner.WorkflowFileError, match="'type' ghi 2 lần"):
        runner.load_workflow(bad)


def test_missing_cutoff_rejected_before_any_block(workflow, capsys):
    result = runner.run_workflow(workflow[0], {}, to_mask={})
    assert (result.status, result.steps, result.errors) == ("rejected", [], ["thiếu tham số lượt: cutoff"])
    assert runner.run_workflow(workflow[0], {"cutoff": "2026-10-20"}, to_mask={}).status == "rejected"   # chữ, không phải ngày
    capsys.readouterr()
    assert runner.main([str(WORKFLOW)]) == 2                                   # dòng lệnh: không còn mặc định 20/10
    assert "thiếu --cutoff" in capsys.readouterr().out


def test_not_utf8_and_missing_file_exit_2(tmp_path, capsys):
    latin = tmp_path / "latin.yaml"
    latin.write_bytes(b"workflow: kpi\n# ng\xe0y ch\xf4t\n")          # chữ Latin-1, không phải UTF-8
    assert runner.main([str(latin), "--check-only"]) == 2
    assert runner.main([str(tmp_path / "khong_co.yaml"), "--check-only"]) == 2
    out = capsys.readouterr().out
    assert "Traceback" not in out and out.count("TỪ CHỐI") == 2


def test_version_sha_is_bytes_not_newline_normalised(tmp_path):
    # S2 minor 3: read_text đổi CRLF ⇒ LF; nay đọc byte ⇒ chữ gốc giữ nguyên CRLF (để lưu đúng chữ vào workflow_version)
    crlf = tmp_path / "crlf.yaml"
    crlf.write_bytes(WORKFLOW.read_bytes().replace(b"\n", b"\r\n"))
    _, text = runner.load_workflow(crlf)
    assert "\r\n" in text


@needs_data
def test_cli_json_has_masked_issues_and_over_target(capsys):
    # 10/11 (bước 3): workflow chính thêm duyet (người duyệt) + xuat (xuất file) sau compute_bonus: dòng lệnh không có cách duyệt ⇒ dừng ở cổng, mã thoát 3; số tới cổng vẫn đủ
    assert runner.main([str(WORKFLOW), "--cutoff", "2026-10-31", "--json"]) == 3
    summary = json.loads(capsys.readouterr().out)
    assert summary["team_total"] == 55_700_000 and summary["over_target"] == {"total": 0, "missing_data": 0}
    assert [issue["level"] for issue in summary["issues"]] == ["warning", "warning", "info", "info"]
    text = json.dumps(summary, ensure_ascii=False)
    assert not any(code in text for code in runner.load_mask()) and not runner.REAL_CODE_PATTERN.search(text)


# ---------- S3: 3 minor của S2-2 (B, C) ----------
def test_list_as_yaml_key_rejected_cleanly(tmp_path, capsys):
    # S2-2: `? [a, b]: 1` từng ném TypeError ở bộ đọc khoá trùng ⇒ traceback
    bad = tmp_path / "khoa_danh_sach.yaml"
    bad.write_text(WORKFLOW.read_text(encoding="utf-8") + "? [a, b]\n: 1\n", encoding="utf-8")
    with pytest.raises(runner.WorkflowFileError):
        runner.load_workflow(bad)
    capsys.readouterr()
    assert runner.main([str(bad), "--check-only"]) == 2
    assert "Traceback" not in capsys.readouterr().out


def test_json_output_even_when_file_unreadable(tmp_path, capsys):
    # S2-2: với --json mọi ca từ chối đều in JSON
    dup = tmp_path / "trung.yaml"
    dup.write_text(WORKFLOW.read_text(encoding="utf-8").replace("params: {npp_turnover_pct: null}", "params: {npp_turnover_pct: 1, npp_turnover_pct: 2}"),
                   encoding="utf-8")
    latin = tmp_path / "latin.yaml"
    latin.write_bytes(b"workflow: kpi\n# ng\xe0y\n")
    for path in (dup, latin):
        capsys.readouterr()
        assert runner.main([str(path), "--cutoff", "2026-10-20", "--json"]) == 2
        summary = json.loads(capsys.readouterr().out)
        assert summary["status"] == "rejected" and len(summary["errors"]) == 1


@needs_data
def test_stopped_run_keeps_all_issues_masked(workflow, tmp_path, capsys):
    # S2-2: lượt dừng ở check từng mất mọi mục warning / info (khối dừng không trả cổng ra)
    copy_path = tmp_path / "chi_tieu_thieu_1_kpi.xlsx"
    book = openpyxl.load_workbook(TARGETS)
    book["Export"].delete_rows(2)
    book.save(copy_path)
    wf = copy.deepcopy(workflow[0])
    next(node for node in wf["nodes"] if node["id"] == "targets")["params"]["path"] = str(copy_path)
    result = runner.run_workflow(wf, {"cutoff": CUTOFF})
    check = next(step for step in result.steps if step["node"] == "check")
    assert check["status"] == "stopped" and "check" not in result.outputs           # khối sau vẫn không nhận gì
    levels = [issue["level"] for issue in check["issues"]]
    assert levels.count("error") == 1 and "warning" in levels and "info" in levels
    text = json.dumps(check, ensure_ascii=False)
    assert not any(code in text for code in runner.load_mask()) and not runner.REAL_CODE_PATTERN.search(text)
    path = tmp_path / "w.yaml"
    path.write_text(yaml.safe_dump(wf, allow_unicode=True, sort_keys=False), encoding="utf-8")
    capsys.readouterr()
    assert runner.main([str(path), "--cutoff", "2026-10-20", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"] == check["issues"]
