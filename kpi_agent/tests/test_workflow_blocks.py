"""Bước 1 S1 (mục 4, W2): mỗi khối ra ĐÚNG đầu ra của hàm gốc gọi thẳng, trên dữ liệu thật tháng 10; dừng sớm có test chứng minh chặn.

Không mock. Máy không có dữ liệu thật thì bỏ qua (như test_backend.py)."""
import copy
from datetime import date
from pathlib import Path

import pytest

from kpi_agent.core.check_data import check_data
from kpi_agent.core.compute_bonus import employee_bonus
from kpi_agent.core.compute_kpi import compute_actuals
from kpi_agent.core.load_data import load_targets, load_tracking
from kpi_agent.core.rules import load_rules
from kpi_agent.workflow.blocks import BLOCKS, BlockStop, run_block

ROOT = Path(__file__).resolve().parents[2]
RULES = "kpi_agent/config/rules_2026_10.yaml"
TARGETS = "sample_data/thang_10/Chi tieu NVBH.xlsx"
TRACKING = "sample_data/thang_10/Tracking.xlsx"
CUTOFF = date(2026, 10, 20)
pytestmark = pytest.mark.skipif(not all((ROOT / p).exists() for p in (TARGETS, TRACKING)), reason="không có dữ liệu thật trên máy này")


@pytest.fixture(scope="module")
def direct():
    # Gọi thẳng hàm gốc — đúng chuỗi backend/queries.py build_context đang làm hôm nay
    rules, targets, tracking = load_rules(ROOT / RULES), load_targets(ROOT / TARGETS), load_tracking(ROOT / TRACKING)
    results = compute_actuals(targets, tracking, rules)
    regions = {code: info["region"] for code, info in targets["employees"].items()}
    bonuses = {code: employee_bonus(by_kpi, rules, regions[code]) for code, by_kpi in results.items()}
    return {"rules": rules, "targets": targets, "tracking": tracking, "issues": check_data(targets, tracking, CUTOFF),
            "results": results, "bonuses": bonuses}


@pytest.fixture(scope="module")
def via_blocks():
    out = {}
    out |= run_block("load_rules", {}, {"path": RULES})
    out |= run_block("read_targets", {}, {"path": TARGETS})
    out |= run_block("read_tracking", {}, {"path": TRACKING})
    out |= run_block("check_data", out, run={"cutoff": CUTOFF})
    out |= run_block("compute_kpi", out)
    out |= run_block("compute_bonus", out)
    return out


@pytest.mark.parametrize("port", ["rules", "targets", "tracking", "issues", "results", "bonuses"])
def test_each_block_equals_direct_call(direct, via_blocks, port):
    assert via_blocks[port] == direct[port]


def test_team_total_is_unchanged(via_blocks):
    # W1 (phần khối): tổng lương kinh doanh tháng 10 = số đã đo đáp án 112 / 112 (KET_QUA mục 2)
    assert sum(b["business_salary"] for b in via_blocks["bonuses"].values()) == 55_700_000
    assert sum(len(b["lines"]) for b in via_blocks["bonuses"].values()) == 112


def test_check_data_stops_on_error(via_blocks):
    # W3: file chỉ tiêu thiếu một KPI (bản sao trong bộ nhớ) ⇒ khối DỪNG, không trả gì
    broken = copy.deepcopy(via_blocks["targets"])
    first = next(iter(broken["targets"]))
    broken["targets"][first].pop("PC_BT")
    with pytest.raises(BlockStop, match="Dữ liệu thiếu thứ bắt buộc"):
        run_block("check_data", {"targets": broken, "tracking": via_blocks["tracking"]}, run={"cutoff": CUTOFF})


def test_load_rules_stops_on_bad_rule_file(tmp_path):
    bad = tmp_path / "rules_hong.yaml"
    bad.write_text("period: 2026-10\nkpis: {}\n", encoding="utf-8")
    with pytest.raises(BlockStop, match="File luật sai khuôn"):
        run_block("load_rules", {}, {"path": str(bad)})


def test_missing_port_param_or_run_param_is_refused():
    with pytest.raises(ValueError, match="ô path"):
        run_block("read_targets", {})
    with pytest.raises(ValueError, match="cổng tracking"):
        run_block("check_data", {"targets": {}}, run={"cutoff": CUTOFF})
    with pytest.raises(ValueError, match="tham số lượt cutoff"):
        run_block("check_data", {"targets": {}, "tracking": {}})


def test_catalog_ports_connect():
    # Mọi cổng vào của một khối là cổng ra của một khối khác — bộ kiểm file (S2) dựa vào danh bạ này
    outputs = {port for block in BLOCKS.values() for port in block.outputs}
    assert all(port in outputs for block in BLOCKS.values() for port in block.inputs)
    assert list(BLOCKS) == ["load_rules", "read_targets", "read_tracking", "read_raw", "check_data", "compute_kpi", "compute_bonus",
                           "review_gate", "export_files", "kpi_tool", "kpi_agent"]   # 09/11 khối agent + 10/11 người duyệt, xuất file
    assert BLOCKS["kpi_agent"].tool_inputs == ("tools",) and BLOCKS["kpi_tool"].outputs == ("tool",)   # cổng công cụ nhận cổng ra "tool"


def test_unknown_param_is_refused():
    # S1 (major): ô sai tên phải bị từ chối, không chạy với giá trị mặc định
    with pytest.raises(ValueError, match="không có ô: npp_turnover"):
        run_block("compute_bonus", {"results": {}, "rules": {}, "targets": {"employees": {}}}, {"npp_turnover": 130})
    with pytest.raises(ValueError, match="không có ô: cutoff"):
        run_block("check_data", {"targets": {}, "tracking": {}}, {"cutoff": "2026-10-20"}, run={"cutoff": CUTOFF})
