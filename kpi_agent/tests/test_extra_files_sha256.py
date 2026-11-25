"""File phụ (Thưởng thêm, 1 % gia vị, Hội viên) vào mã bản dữ liệu (việc Z).

Ca chạy thật trước vá : đổi file chỉ tiêu Kokomi ⇒ tổng Thưởng thêm đổi mà
`data_version` đứng yên. Test ở đây giữ hai điều không cần dữ liệu thật: file phụ thiếu không làm hỏng lúc bật, và đường sổ Tracking không có file phụ."""
from datetime import date

from kpi_agent.backend import main
from kpi_agent.workflow import runner

RAW_WORKFLOW = {"nodes": [{"id": "doc", "type": "read_raw"}]}
TRACKING_WORKFLOW = {"nodes": [{"id": "doc", "type": "read_tracking"}]}


def test_missing_extra_files_are_marked_not_raised(monkeypatch, tmp_path):
    # Kỳ không có file luật nào ⇒ ba file nguồn đều "không có file", không ném lỗi, không đoán file con
    monkeypatch.setattr(runner, "repo_path", lambda value: tmp_path / value)
    files = main._extra_files_sha256(RAW_WORKFLOW, "2099-01")
    assert files == {"kpi_agent/config/extra_bonus_2099_01.yaml": "không có file",
                     "kpi_agent/config/gia_vi_1pct_2099_01.yaml": "không có file",
                     "kpi_agent/config/hoi_vien_2099_01.yaml": "không có file"}


def test_child_file_change_changes_data_version(monkeypatch, tmp_path):
    # Sổ do file nguồn Hội viên trỏ tới đổi nội dung ⇒ data_version đổi; bản tính 8 KPI (files_sha256) không liên quan
    config = tmp_path / "kpi_agent/config"
    config.mkdir(parents=True)
    (config / "hoi_vien_2099_01.yaml").write_text('workbook: "so.xlsx"\n', encoding="utf-8")
    book = tmp_path / "so.xlsx"
    book.write_bytes(b"ban 1")
    monkeypatch.setattr(runner, "repo_path", lambda value: tmp_path / value)
    first = main._extra_files_sha256(RAW_WORKFLOW, "2099-01")
    book.write_bytes(b"ban 2")
    second = main._extra_files_sha256(RAW_WORKFLOW, "2099-01")
    assert "so.xlsx" in first and first["so.xlsx"] != second["so.xlsx"]
    cutoff = date(2099, 1, 31)
    assert main._data_version({"a": "x"}, "code", cutoff, first) != main._data_version({"a": "x"}, "code", cutoff, second)


def test_tracking_workflow_has_no_extra_files():
    assert main._extra_files_sha256(TRACKING_WORKFLOW, "2026-10") == {}
