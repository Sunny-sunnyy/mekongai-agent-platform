"""Đối chiếu chỉ tiêu với danh sách tuyến khi file tuyến KHÔNG có số lần viếng thăm (đường RAW) + lý do "luật chưa kiểm được" theo nguồn.

Trước 12/11: thiếu số lần viếng thăm ⇒ bỏ luôn cả phép đối chiếu ASO_ALL (chỉ cần số cửa hàng); và lời "thiếu dữ liệu từng dòng đơn" dùng cho cả
đường RAW — nơi có từng dòng đơn. Số tự đặt, không cần dữ liệu thật."""
from kpi_agent.backend import queries
from kpi_agent.core.check_data import _check_targets_against_mcp

OFF, ON = "Kênh Off Premise", "Kênh On Premise"


def _routes(count: int, visits: int | None = None) -> dict:
    outlet = {"channel": OFF} if visits is None else {"channel": OFF, "visits_per_month": visits}
    return {f"cua_hang_{index}": dict(outlet) for index in range(count)}


def test_without_visit_counts_aso_all_is_still_checked_and_pc_is_named_as_not_checked():
    targets = {"targets": {"SM01": {"ASO_ALL": 8, "PC_BT": 99, "PC_ON": 99}, "SM02": {"ASO_ALL": 7, "PC_BT": 99, "PC_ON": 99}}}
    issues = _check_targets_against_mcp(targets, {"SM01": _routes(10), "SM02": _routes(10)})   # 80 % của 10 = 8
    assert [issue["level"] for issue in issues] == ["info", "info"]                           # thông tin, không phải cảnh báo
    # Dòng tổng (đi kèm mọi kết quả công cụ) NÊU người lệch, không có số đếm "1/2" — bản đầu ghi "khớp 12/14" làm trợ lý trả lời "khớp" cho chính người lệch
    assert issues[0]["message"] == ("chỉ tiêu ASO_ALL tính lại từ danh sách tuyến (80 % số cửa hàng trong tuyến): lệch ở SM02 — xem vấn đề dữ liệu của "
                                    "các nhân viên này; các nhân viên khác khớp. PC_BT / PC_ON chưa đối chiếu (danh sách tuyến không có số lần "
                                    "viếng thăm mỗi tháng)")
    assert issues[0]["employees"] == []                                                       # vẫn là cờ CẢ ĐỘI ⇒ vào mọi kết quả công cụ
    assert issues[1]["message"].startswith("lệch") and "SM02 ASO_ALL: chỉ tiêu 7 ≠ tính từ MCP 8 (80 % của 10 cửa hàng tuyến)" in issues[1]["message"]
    assert "PC_BT" not in issues[1]["message"] and issues[1]["employees"] == ["SM02"]         # PC không bị báo lệch khi không tính lại được


def test_without_visit_counts_and_no_mismatch_says_everyone_matches():
    issues = _check_targets_against_mcp({"targets": {"SM01": {"ASO_ALL": 8}}}, {"SM01": _routes(10)})
    assert len(issues) == 1 and "mọi nhân viên khớp" in issues[0]["message"] and "lệch" not in issues[0]["message"]


def test_summary_flag_names_mismatched_people_in_masked_form_for_the_model():
    targets = {"targets": {"SM01": {"ASO_ALL": 8}, "SM02": {"ASO_ALL": 7}}}
    issues = _check_targets_against_mcp(targets, {"SM01": _routes(10), "SM02": _routes(10)})
    to_real = {"NV01": "SM01", "NV02": "SM02"}
    team_issues = [issue for issue in issues if not queries._names_team_member(issue, to_real)]   # đúng phép lọc của cờ gửi mô hình
    flags = queries._data_flags(team_issues, {"source": "raw", "refreshed_at": None}, {}, to_real)
    summary = [flag for flag in flags if flag.startswith("chỉ tiêu ASO_ALL")]
    assert len(summary) == 1 and "lệch ở NV02" in summary[0] and "SM02" not in " ".join(flags)   # mã che, không mã thật
    assert not any(flag.startswith("lệch") for flag in flags)                                    # dòng chi tiết từng người không vào cờ chung


def test_with_visit_counts_all_three_are_checked_with_the_old_wording():
    targets = {"targets": {"SM01": {"ASO_ALL": 8, "PC_BT": 13, "PC_ON": 0}}}
    issues = _check_targets_against_mcp(targets, {"SM01": _routes(10, visits=2)})             # 65 % của 20 lượt = 13
    assert [issue["message"] for issue in issues] == ["chỉ tiêu tính lại từ MCP khớp 3/3"]


def test_unchecked_rule_reason_follows_the_data_source():
    rules = {}
    raw_flags = queries._data_flags([], {"source": "raw", "refreshed_at": None}, rules, {})
    old_flags = queries._data_flags([], {"source": "tracking", "refreshed_at": None}, rules, {})
    raw_unchecked = [flag for flag in raw_flags if flag.startswith("luật chưa kiểm được")]
    assert len(raw_unchecked) == 1 and "chưa có bảng số lượng tối thiểu theo từng mã hàng" in raw_unchecked[0]
    assert "thiếu dữ liệu từng dòng đơn" not in " ".join(raw_flags) and "MOQ_FLAG" not in raw_unchecked[0].split(":")[0]
    old_unchecked = [flag for flag in old_flags if flag.startswith("luật chưa kiểm được")]
    assert len(old_unchecked) == 3 and all("thiếu dữ liệu từng dòng đơn" in flag for flag in old_unchecked)   # đường Tracking giữ chữ cũ
