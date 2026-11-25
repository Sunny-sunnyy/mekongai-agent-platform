"""calc_period_change chọn bản dữ liệu của kỳ KHÁC kỳ của lượt (`_calc_source`, main.py) — gồm cả bản lúc bật.

Vì sao (tích hợp bậc 2 + công cụ tính, 18/11/2026): bản gộp chỉ tìm kỳ khác trong `app.state.versions` — mà `versions` chỉ chứa
bản TẢI LÊN; bản lúc bật nằm ở chính `app.state` ⇒ bật T10, tải T11, hỏi trên bản T11 "so với tháng 10" ⇒ từ chối "chưa có kỳ 10" dù có.
Test thử ĐƯỜNG CHỌN BẢN (trạng thái app dựng tay, công cụ đọc thay bằng hàm trả kỳ của bản được chọn) — không phải số KPI thật."""
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from kpi_agent.backend import main

BOOT = "a" * 64      # data_version giả của bản lúc bật (kỳ 10)
UP11 = "b" * 64      # bản tải lên kỳ 11
UP10 = "c" * 64      # bản tải lên kỳ 10 (sau lúc bật)


@pytest.fixture
def app_state(monkeypatch):
    # công cụ đọc ⇒ trả kỳ + bản của request nó nhận (để biết _calc_source chọn bản nào)
    monkeypatch.setattr(main, "_tool_for_model", lambda target, tool, args: {"period": target.app.state.ctx.period,
                                                                             "version": target.app.state.data_version})
    root = SimpleNamespace(ctx=SimpleNamespace(period="2026-10"), data_version=BOOT, versions=OrderedDict())
    root.versions[UP11] = main._VersionState(root, ctx=SimpleNamespace(period="2026-11"), data_version=UP11)
    return root


def request_on(root, version=None):
    base = SimpleNamespace(app=SimpleNamespace(state=root))
    return base if version is None else main._VersionRequest(base, root.versions[version])


def test_uploaded_turn_finds_boot_version_of_other_period(app_state):
    # lượt trên bản T11 (tải lên) hỏi kỳ 10 = bản lúc bật
    out = main._calc_source(request_on(app_state, UP11), "get_team_bonus_table", {}, "2026-10")
    assert out.get("period") == "2026-10" and out["version"] == BOOT, out
    assert out["ban_du_lieu"] == f"data@{BOOT[:8]}"


def test_boot_turn_finds_uploaded_period(app_state):
    out = main._calc_source(request_on(app_state), "get_team_bonus_table", {}, "2026-11")
    assert out["period"] == "2026-11" and out["version"] == UP11


def test_newer_upload_of_same_period_wins_over_boot(app_state):
    app_state.versions[UP10] = main._VersionState(app_state, ctx=SimpleNamespace(period="2026-10"), data_version=UP10)
    out = main._calc_source(request_on(app_state, UP11), "get_team_bonus_table", {}, "2026-10")
    assert out["version"] == UP10                  # bản tải sau lúc bật = bản mới nhất của kỳ


def test_missing_period_still_refused(app_state):
    out = main._calc_source(request_on(app_state, UP11), "get_team_bonus_table", {}, "2026-09")
    assert "chưa có bản dữ liệu kỳ" in out["refused"]
