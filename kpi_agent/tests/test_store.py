"""Bậc 2a S1 : bảng chỉ thêm — sửa / xoá bị SQLite từ chối; bật lại không đẻ bản trùng; đổi luật ⇒ bản mới thay bản cũ.

Chạy trên file SQLite thật trong thư mục tạm (không mock); số liệu nhỏ viết tay — phép so 112 ô trên dữ liệu thật ở test_backend_store.py."""
import sqlite3

import pytest

from kpi_agent.backend import store

LINES = [{"employee_id": "NV01", "kpi": "PC_BT", "pct": 45.07, "reached_tier": None, "amount": 0, "reason": "chưa tới mốc"},
         {"employee_id": "NV01", "kpi": "ASO_ALL", "pct": 100.0, "reached_tier": "Đạt 100%", "amount": 400000, "reason": "đạt"}]


@pytest.fixture
def paths(tmp_path):
    rule = tmp_path / "rules_2026_10.yaml"
    rule.write_text("period: 2026-10\n", encoding="utf-8")
    return tmp_path / "kpi_store.sqlite", rule


def record(conn, rule, code_sha="c1", files=None, lines=LINES):
    return store.record_run(conn, period="2026-10", cutoff="2026-10-20", rule_file=rule,
                            data_files=files or {"Tracking.xlsx": "aa", "Chi tieu NVBH.xlsx": "bb"}, refreshed_at="2026-10-21T14:16:38",
                            code_sha=code_sha, code_files=["kpi_agent/core/x.py"], lines=lines)


def test_update_and_delete_are_refused_on_every_table(paths):
    db, rule = paths
    with store.connect(db) as conn:
        run_id = record(conn, rule)
        store.mark_reviewed(conn, run_id, "Giám sát A")
        # bảng rỗng thì trigger theo dòng không chạy ⇒ phải có dòng ở 2 bảng workflow (S2) thì phép thử mới chứng minh được gì
        version = store.record_workflow_version(conn, "wf", "workflow: wf\n", "sha-a")
        store.record_workflow_run(conn, workflow_version_id=version, cutoff="2026-10-20", status="ok", steps=[], errors=[],
                                  inputs={"sha256": "x"}, bonus_run_id=run_id)
        store.record_workflow_trial_run(conn, workflow_version_id=version, cutoff="2026-10-20", status="ok", steps=[], errors=[],
                                        inputs={"sha256": "x"}, summary={})   # bảng 2b — cũng phải có dòng
        store.record_capitol_approval(conn, run_id, "a" * 64, "org-thu", "người duyệt thử")   # bảng Người duyệt Capitol (10/11) — cũng phải có dòng
        store.record_mapping(conn, "don_hang", "f" * 64, {"sheet": "S"}, "b" * 64, "người ghép thử", "")   # bậc 2: cách ghép đã xác nhận — cũng phải có dòng
        # 2j: bảng ghi các bản đã tải lên (để nạp lại lúc bật) — cũng phải có dòng
        store.record_uploaded_version(conn, data_version="d" * 64, kind="raw", period="2026-10", wanted_cutoff=None, base_data_version=None,
                                      file_sha256="e" * 64, mapping_sha=None)
        store.record_pending_upload(conn, kind="raw", period="2026-10", wanted_cutoff=None, file_sha256="e" * 64)   # bộ chờ của kỳ trống
        store.record_auto_employee_masks(conn, {"NV01": "SM01"}, {"SM99": "Nhân viên mới"})                        # K1: bảng mã che tự cấp
    conn = sqlite3.connect(db)
    for table in store.TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0, f"bảng {table} rỗng ⇒ phép thử không chứng minh được gì"
        with pytest.raises(sqlite3.IntegrityError, match="chỉ thêm"):
            conn.execute(f"UPDATE {table} SET id = id")
        with pytest.raises(sqlite3.IntegrityError, match="chỉ thêm"):
            conn.execute(f"DELETE FROM {table}")
    assert conn.execute("SELECT COUNT(*) FROM bonus_line").fetchone()[0] == 2
    conn.close()


def test_same_inputs_reuse_run_and_unrelated_change_does_not_create_run(paths):
    db, rule = paths
    with store.connect(db) as conn:
        first = record(conn, rule)
    with store.connect(db) as conn:          # "bật lại server": mở kết nối mới, cùng đầu vào
        assert record(conn, rule) == first
        assert [r["run_id"] for r in store.list_runs(conn, "2026-10")] == [first]


def test_changed_rule_creates_new_run_old_one_still_readable(paths):
    db, rule = paths
    with store.connect(db) as conn:
        old = record(conn, rule)
        rule.write_text("period: 2026-10\n# sửa luật\n", encoding="utf-8")
        new = record(conn, rule, lines=[{**LINES[0], "amount": 100000}])
        assert new != old
        old_summary, new_summary = store.run_summary(conn, old), store.run_summary(conn, new)
        assert old_summary["replaced_by"] == new and new_summary["replaces"] == old
        assert old_summary["team_total"] == 400000 and new_summary["team_total"] == 100000
        assert len(store.run_lines(conn, old)) == 2                       # bản cũ vẫn đọc được nguyên
        assert store.rule_content(conn, old) == "period: 2026-10\n"       # và nội dung luật cũ còn để tính lại
    # đổi code / dữ liệu cũng ra bản mới
    with store.connect(db) as conn:
        assert record(conn, rule, code_sha="c2") not in (old, new)


def test_review_label_and_refusals(paths):
    db, rule = paths
    with store.connect(db) as conn:
        run_id = record(conn, rule)
        assert store.run_summary(conn, run_id)["status_label"] == "tạm tính — chưa phải số chốt, chưa duyệt"   # đúng chữ bậc 1
        result = store.mark_reviewed(conn, run_id, "Giám sát A", "đã xem NV01")
        summary = result["run"]
        assert summary["status"] == "da_soat" and summary["reviewed_by"] == "Giám sát A"
        assert summary["status_label"].startswith("đã soát bởi Giám sát A lúc ") and "vẫn chưa phải số chốt" in summary["status_label"]
        assert result["event"]["note"] == "đã xem NV01"
        with pytest.raises(store.Refused) as again:
            store.mark_reviewed(conn, run_id, "Giám sát B")
        assert again.value.status == 409
        with pytest.raises(store.Refused) as missing:
            store.mark_reviewed(conn, 999, "Giám sát A")
        assert missing.value.status == 404
        rule.write_text("period: 2026-10\n# bản 2\n", encoding="utf-8")
        newer = record(conn, rule)
        assert store.run_summary(conn, newer)["status"] == "tam_tinh"      # bản mới phải soát lại
        newest = record(conn, rule, code_sha="c3")
        with pytest.raises(store.Refused) as replaced:
            store.mark_reviewed(conn, newer, "Giám sát A")                 # newer đã bị thay bởi newest
        assert replaced.value.status == 409 and str(newest) in replaced.value.reason


def test_code_fingerprint_only_covers_given_files(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text("x = 1\n")
    (tmp_path / "other.py").write_text("y = 1\n")
    sha, names = store.code_fingerprint([tmp_path / "core" / "a.py"], tmp_path)
    (tmp_path / "other.py").write_text("y = 2\n")                         # file không liên quan đổi
    assert store.code_fingerprint([tmp_path / "core" / "a.py"], tmp_path) == (sha, names) and names == ["core/a.py"]
    (tmp_path / "core" / "a.py").write_text("x = 2\n")
    assert store.code_fingerprint([tmp_path / "core" / "a.py"], tmp_path)[0] != sha


def test_insert_or_replace_cannot_overwrite(paths):
    # 2a (major): REPLACE xoá ngầm dòng cũ — phải bị trigger DELETE chặn (cần PRAGMA recursive_triggers)
    db, rule = paths
    with store.connect(db) as conn:
        run_id = record(conn, rule)
        line_id = conn.execute("SELECT id FROM bonus_line WHERE run_id = ? AND kpi = 'ASO_ALL'", (run_id,)).fetchone()["id"]
        with pytest.raises(sqlite3.IntegrityError, match="chỉ thêm"):
            conn.execute("INSERT OR REPLACE INTO bonus_line (id, run_id, employee_id, kpi, pct, reached_tier, amount, reason)"
                         " VALUES (?, ?, 'NV01', 'ASO_ALL', 100.0, 'Đạt 100%', 999999, 'đè')", (line_id, run_id))
        assert conn.execute("SELECT amount FROM bonus_line WHERE id = ?", (line_id,)).fetchone()["amount"] == 400000


# ---------- bước 1 S2: workflow_version + workflow_run ----------
def test_workflow_version_same_logic_sha_reused_changed_sha_new_old_kept(paths):
    db, _ = paths
    with store.connect(db) as conn:
        first = store.record_workflow_version(conn, "wf", "workflow: wf\n", "sha-a")
        assert store.record_workflow_version(conn, "wf", "workflow: wf\n", "sha-a") == first
        # chỉ đổi bố cục (cùng mã băm logic) ⇒ dùng lại dòng cũ, chữ gốc lần đầu giữ nguyên
        assert store.record_workflow_version(conn, "wf", "workflow: wf\n# kéo khối\n", "sha-a") == first
        second = store.record_workflow_version(conn, "wf", "workflow: wf\n# đổi một ô\n", "sha-b")
        assert second != first
        assert [row["content"] for row in conn.execute("SELECT content FROM workflow_version ORDER BY id")] == \
               ["workflow: wf\n", "workflow: wf\n# đổi một ô\n"]


@pytest.mark.parametrize("status, use_bonus_run", [("ok", False), ("stopped", True), ("rejected", False)])
def test_workflow_run_rules_enforced_by_sqlite(paths, status, use_bonus_run):
    # ok ⇔ có bản tính: "ok" thiếu bản tính, "stopped" trỏ bản tính, "rejected" (0 khối chạy) đều không ghi được
    db, rule = paths
    with store.connect(db) as conn:
        bonus_run = record(conn, rule)
        version = store.record_workflow_version(conn, "wf", "workflow: wf\n", "sha-a")
        with pytest.raises(sqlite3.IntegrityError):
            store.record_workflow_run(conn, workflow_version_id=version, cutoff="2026-10-20", status=status, steps=[], errors=[],
                                      inputs={"sha256": "x"}, bonus_run_id=bonus_run if use_bonus_run else None)


def test_workflow_run_ok_must_point_to_existing_bonus_run(paths):
    db, _ = paths
    with store.connect(db) as conn:
        version = store.record_workflow_version(conn, "wf", "workflow: wf\n", "sha-a")
        with pytest.raises(sqlite3.IntegrityError):
            store.record_workflow_run(conn, workflow_version_id=version, cutoff="2026-10-20", status="ok", steps=[], errors=[],
                                      inputs={"sha256": "x"}, bonus_run_id=999)


def test_workflow_run_summary_keeps_cutoff_inputs_and_version(paths):
    db, rule = paths
    with store.connect(db) as conn:
        bonus_run = record(conn, rule)
        version = store.record_workflow_version(conn, "wf", "workflow: wf\n", "sha-a")
        stopped = store.record_workflow_run(conn, workflow_version_id=version, cutoff="2026-10-31", status="stopped",
                                            steps=[{"node": "check", "status": "stopped"}], errors=["khối 'check': NV01: thiếu"],
                                            inputs={"sha256": "s1", "run": {"cutoff": "2026-10-31"}}, bonus_run_id=None)
        ok = store.record_workflow_run(conn, workflow_version_id=version, cutoff="2026-10-20", status="ok", steps=[], errors=[],
                                       inputs={"sha256": "s2", "run": {"cutoff": "2026-10-20"}}, bonus_run_id=bonus_run)
        first, second = store.workflow_run_summary(conn, stopped), store.workflow_run_summary(conn, ok)
    assert (first["status"], first["cutoff"], first["bonus_run_id"], first["errors"]) == ("stopped", "2026-10-31", None, ["khối 'check': NV01: thiếu"])
    assert (second["status"], second["cutoff"], second["bonus_run_id"], second["inputs"]["sha256"]) == ("ok", "2026-10-20", bonus_run, "s2")
    assert first["workflow_version"].startswith("wf@")


# ---------- Lỗi mốc 7 (nhóm kỹ thuật 17/11): cùng file + ngày chốt mà code đọc ra "lúc chụp" khác ⇒ bản tính dùng lại nhãn cũ của data_snapshot ----------
def _record_at(conn, rule, refreshed_at, code_sha="c1"):
    return store.record_run(conn, period="2026-10", cutoff="2026-10-31", rule_file=rule, data_files={"RAW DATA.xlsx": "aa"},
                            refreshed_at=refreshed_at, code_sha=code_sha, code_files=["kpi_agent/core/x.py"], lines=LINES)


def test_changed_refreshed_at_creates_new_run_with_its_own_label(paths):
    db, rule = paths
    with store.connect(db) as conn:
        old = _record_at(conn, rule, "2026-10-30T00:00:00")
        new = _record_at(conn, rule, "2026-10-31T00:00:00", code_sha="c2")      # mốc 7: code mới đọc RAW ra ngày đơn mới nhất 31/10
        assert new != old and store.run_summary(conn, new)["replaces"] == old
        assert store.run_summary(conn, new)["data_snapshot"]["refreshed_at"] == "2026-10-31T00:00:00"
        assert store.run_summary(conn, old)["data_snapshot"]["refreshed_at"] == "2026-10-30T00:00:00"   # bản cũ giữ nhãn lúc ghi
        assert _record_at(conn, rule, "2026-10-31T00:00:00", code_sha="c2") == new   # giống hết ⇒ KHÔNG đẻ thêm bản (bật lại server)
        # cùng code, chỉ "lúc chụp" khác ⇒ vẫn bản mới (nhãn nguồn là một phần của bản tính)
        third = _record_at(conn, rule, "2026-11-01T00:00:00", code_sha="c2")
        assert third != new and store.run_summary(conn, third)["replaces"] == new
        assert conn.execute("SELECT COUNT(*) FROM data_snapshot").fetchone()[0] == 1   # bảng cũ không đổi khuôn, vẫn một dòng cho cùng file + ngày chốt


def test_old_store_without_run_refresh_still_opens_and_reads(paths):
    # Kho tạo trước bản sửa không có bảng run_refresh ⇒ mở được (bảng thêm bằng CREATE IF NOT EXISTS), bản cũ đọc nhãn từ data_snapshot như trước
    db, rule = paths
    with store.connect(db) as conn:
        old = _record_at(conn, rule, "2026-10-30T00:00:00")
    raw = sqlite3.connect(db)
    raw.execute("DROP TABLE run_refresh")          # giả lập file kho cũ (DROP không bị trigger chặn — giới hạn đã ghi ở đầu store.py)
    raw.commit(); raw.close()
    with store.connect(db) as conn:
        assert store.run_summary(conn, old)["data_snapshot"]["refreshed_at"] == "2026-10-30T00:00:00"
        assert _record_at(conn, rule, "2026-10-30T00:00:00") == old           # cùng nhãn ⇒ dùng lại bản cũ, không đẻ bản trùng
        assert _record_at(conn, rule, "2026-10-31T00:00:00") != old
