"""Lưu mỗi lần tính thưởng thành một bản KHÔNG sửa được + sự kiện "Đã soát" (bậc 2a — câu 48).

Vì sao (nháp bậc 2 mục 2, công văn mục 9–10): số thưởng qua nhiều tay (Admin miền, giám sát, NPP) ⇒ phải trả lời được "số này tính theo
bản luật nào, dữ liệu chụp lúc nào, ai đã soát". Sửa sai = thêm bản mới (`replaces` trỏ bản cũ), không sửa bản cũ.
- "Chỉ thêm" do chính SQLite giữ: trigger chặn UPDATE / DELETE trên cả 11 bảng (5 bảng 2a + `workflow_version`, `workflow_run` của bước 1 S2 + `workflow_trial_run` của 2b + `capitol_approval` 10/11 + `run_refresh`, `column_mapping` 17/11) ⇒ không có cột phải sửa: "bị thay bởi" tra từ bản mới,
  trạng thái suy từ `approval_event`. Giới hạn (A 13:55): không chặn DROP TABLE / xoá file / mở file bằng `sqlite3` tay.
- Mã NV trong file này là mã che NV01–NV14 (như mọi thứ ngoài `core/`); file nằm trong `kpi_agent/data/` (Git bỏ qua)."""
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

TABLES = ("rule_version", "data_snapshot", "bonus_run", "bonus_line", "approval_event", "workflow_version", "workflow_run", "workflow_trial_run",
          "capitol_approval", "run_refresh", "column_mapping", "uploaded_version", "pending_upload", "auto_employee_mask")
VN_TZ = timezone(timedelta(hours=7))
SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_version (
    id INTEGER PRIMARY KEY, file_name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS data_snapshot (
    id INTEGER PRIMARY KEY, period TEXT NOT NULL, cutoff TEXT NOT NULL, files TEXT NOT NULL, refreshed_at TEXT, created_at TEXT NOT NULL,
    UNIQUE (period, cutoff, files));
CREATE TABLE IF NOT EXISTS bonus_run (
    id INTEGER PRIMARY KEY, period TEXT NOT NULL, rule_version_id INTEGER NOT NULL REFERENCES rule_version(id),
    data_snapshot_id INTEGER NOT NULL REFERENCES data_snapshot(id), code_sha256 TEXT NOT NULL, code_files TEXT NOT NULL,
    team_total INTEGER NOT NULL, replaces INTEGER REFERENCES bonus_run(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bonus_line (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES bonus_run(id), employee_id TEXT NOT NULL, kpi TEXT NOT NULL,
    pct REAL NOT NULL, reached_tier TEXT, amount INTEGER NOT NULL, reason TEXT NOT NULL, UNIQUE (run_id, employee_id, kpi));
CREATE TABLE IF NOT EXISTS approval_event (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES bonus_run(id), action TEXT NOT NULL CHECK (action = 'reviewed'),
    actor TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_version (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflow_run (
    id INTEGER PRIMARY KEY, workflow_version_id INTEGER NOT NULL REFERENCES workflow_version(id), cutoff TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'stopped')), steps TEXT NOT NULL, errors TEXT NOT NULL,
    inputs TEXT NOT NULL, inputs_sha256 TEXT NOT NULL, bonus_run_id INTEGER REFERENCES bonus_run(id), created_at TEXT NOT NULL,
    CHECK ((status = 'ok') = (bonus_run_id IS NOT NULL)));
-- Bước 2b (09/11 "chạy thử, không thay số đang dùng"): lượt bấm Chạy trên trang KHÔNG đẻ bản tính (bonus_run) ⇒ không ghi được vào
-- workflow_run (CHECK trên: ok ⇔ có bản tính). Bảng riêng, cùng luật chỉ thêm; `summary` = tóm tắt cổng ra đã che mã (không số theo NV).
CREATE TABLE IF NOT EXISTS workflow_trial_run (
    id INTEGER PRIMARY KEY, workflow_version_id INTEGER NOT NULL REFERENCES workflow_version(id), cutoff TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'stopped')), steps TEXT NOT NULL, errors TEXT NOT NULL,
    inputs TEXT NOT NULL, inputs_sha256 TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL);
-- 10/11: khối Người duyệt của bộ Capitol duyệt bảng thưởng = một dòng ở bảng RIÊNG, gắn mã băm bảng đã duyệt.
-- KHÔNG ghi vào approval_event ⇒ không đổi "Đã soát" của trang User. Xuất file có khoá hệ thống chỉ khi có dòng khớp băm bảng HIỆN TẠI.
-- Thêm bảng mới (CREATE … IF NOT EXISTS) — không đổi bảng cũ trong file SQLite của User. Người duyệt là tên tự khai (Capitol tắt đăng nhập).
CREATE TABLE IF NOT EXISTS capitol_approval (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES bonus_run(id), table_sha256 TEXT NOT NULL, org_id TEXT NOT NULL,
    reviewer TEXT NOT NULL, created_at TEXT NOT NULL);
-- Mốc dữ liệu: "lúc chụp số liệu" của TỪNG bản tính. data_snapshot có UNIQUE (period, cutoff, files)
-- nên cùng file + ngày chốt chỉ có một dòng ⇒ code mới đọc ra ngày khác vẫn dùng lại nhãn cũ. Không đổi bảng cũ (tiền lệ 10/11) ⇒ bảng riêng,
-- chỉ thêm; bản tính cũ không có dòng ở đây ⇒ đọc nhãn từ data_snapshot như trước.
-- 17/11 (bậc 2 bước 2.3): cách ghép cột NGƯỜI đã xác nhận (qua câu kiểm), theo (bảng chuẩn, vân tay bố cục) — tải lên file cùng bố cục dùng lại bản
-- mới nhất. Chỉ thêm: ghép lại = dòng mới. `mapping` = JSON đã chuẩn hoá (khuon.normalize_mapping), `mapping_sha` vào data_version.
CREATE TABLE IF NOT EXISTS column_mapping (
    id INTEGER PRIMARY KEY, table_name TEXT NOT NULL, layout_fingerprint TEXT NOT NULL, mapping TEXT NOT NULL, mapping_sha TEXT NOT NULL,
    confirmed_by TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_refresh (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL UNIQUE REFERENCES bonus_run(id), refreshed_at TEXT, created_at TEXT NOT NULL);
-- 20/11 (2j): mỗi bản dữ liệu MỚI nhận qua cửa tải lên = một dòng, đủ để backend bật lại dựng lại đúng bản đó bằng chính đường tải lên:
-- loại file, kỳ, ngày chốt người tải gửi (có thể trống), bản làm nền (trống = bản lúc bật), mã băm file (= tên file trong thư mục tải lên).
-- `data_version` = mã bản LÚC TẢI (để đối chiếu; lúc nạp lại mã được tính lại từ nội dung). `mapping_sha` = cách ghép cột của bản (có thể trống).
-- Chỉ dùng trong kho bản tải lên (<kho>_uploads.sqlite); bảng mới, không đổi bảng cũ.
CREATE TABLE IF NOT EXISTS uploaded_version (
    id INTEGER PRIMARY KEY, data_version TEXT NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL, wanted_cutoff TEXT, base_data_version TEXT,
    file_sha256 TEXT NOT NULL, mapping_sha TEXT, created_at TEXT NOT NULL);
-- 20/11 (kỳ trống): file đã nhận qua cửa tải lên khi kỳ CHƯA có bản nào — "bộ chờ". Mỗi lần nhận một dòng; mỗi loại lấy dòng mới nhất.
-- Đủ ba loại (đơn hàng, chỉ tiêu, sổ theo dõi) thì backend dựng bản đầu của kỳ; bật lại đọc lại bảng này. Chỉ dùng trong kho bản tải lên.
CREATE TABLE IF NOT EXISTS pending_upload (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, period TEXT NOT NULL, wanted_cutoff TEXT, file_sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
-- 26/11 (K1): mã che tự cấp nối tiếp (NV15…) cho nhân viên mới từ file chỉ tiêu — chỉ thêm, bền qua restart, không sửa ma_che_ten.json.
CREATE TABLE IF NOT EXISTS auto_employee_mask (
    id INTEGER PRIMARY KEY, masked_code TEXT NOT NULL UNIQUE, real_code TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL, created_at TEXT NOT NULL);
""" + "".join(
    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{verb.lower()} BEFORE {verb} ON {table} "
    f"BEGIN SELECT RAISE(ABORT, 'chỉ thêm: không sửa / xoá {table}'); END;\n"
    for table in TABLES for verb in ("UPDATE", "DELETE"))

# Nhãn trạng thái — `tam_tinh` giữ ĐÚNG chữ bậc 1 (A 13:55: chuỗi kỳ vọng tính năng 4 không sửa)
UNREVIEWED = ("tam_tinh", "tạm tính — chưa phải số chốt, chưa duyệt")
REVIEWED_LABEL = "đã soát bởi {actor} lúc {at:%d/%m/%Y %H:%M} — vẫn chưa phải số chốt (Admin miền chốt)"
# Bản GỬI MÔ HÌNH không chép tên: tên là chữ người gõ — không để chữ đó vào lời dặn / kết quả công cụ
REVIEWED_LABEL_FOR_MODEL = "đã soát lúc {at:%d/%m/%Y %H:%M} — vẫn chưa phải số chốt (Admin miền chốt)"


class Refused(Exception):
    """Lời từ chối ghi (bản không có / bị thay / đã soát) — `status` là mã HTTP backend trả."""
    def __init__(self, reason: str, status: int):
        super().__init__(reason)
        self.reason, self.status = reason, status


@contextmanager
def connect(path: Path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 2a (major): không bật thì `INSERT OR REPLACE` xoá ngầm dòng cũ mà trigger DELETE không chạy ⇒ đè được tiền
    conn.execute("PRAGMA recursive_triggers = ON")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    # Giờ ghi sự kiện = giờ Việt Nam UTC+7 (khác ngày chốt số — ngày chốt vẫn là tham số)
    return datetime.now(VN_TZ).isoformat(timespec="seconds")


def code_fingerprint(files: list[Path], root: Path) -> tuple[str, list[str]]:
    """sha256 gộp (đường dẫn + nội dung) của đúng các file code quyết định số — đổi file khác không đẻ bản mới."""
    digest, names = hashlib.sha256(), []
    for path in sorted(files):
        name = path.relative_to(root).as_posix()
        digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
        names.append(name)
    return digest.hexdigest(), names


def _insert_or_get(conn, table: str, unique: dict, extra: dict) -> int:
    where = " AND ".join(f"{key} = ?" for key in unique)
    row = conn.execute(f"SELECT id FROM {table} WHERE {where}", tuple(unique.values())).fetchone()
    if row:
        return row["id"]
    values = {**unique, **extra, "created_at": _now()}
    return conn.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                        tuple(values.values())).lastrowid


def record_run(conn, *, period: str, cutoff: str, rule_file: Path, data_files: dict[str, str], refreshed_at: str | None,
               code_sha: str, code_files: list[str], lines: list[dict], reuse_any: bool = False) -> int:
    """Ghi một lần tính; cùng (bản luật, dữ liệu, code) với bản mới nhất của kỳ ⇒ dùng lại bản đó (bật lại server không đẻ bản trùng).

    `reuse_any` (2j vòng 2, 20/11/2026 — chỉ lúc backend NẠP LẠI bản đã tải): tìm bản trùng trong MỌI bản tính của kỳ, không chỉ bản mới nhất.
    Kho bản tải lên giữ nhiều bản cùng kỳ ⇒ khi nạp lại, bản không phải mới nhất từng bị ghi thành lượt tính MỚI ở mỗi lần bật ( đo:
    224 ⇒ 448 ⇒ 672 dòng) và lượt mới không mang dấu "Đã soát" / lời duyệt gắn với lượt cũ.

    `lines`: [{employee_id (mã che), kpi, pct, reached_tier, amount, reason}] — đầu ra lõi `core/`, ghi nguyên.
    
    """
    content = rule_file.read_text(encoding="utf-8")
    rule_id = _insert_or_get(conn, "rule_version", {"sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()},
                             {"file_name": rule_file.name, "content": content})
    snapshot_id = _insert_or_get(conn, "data_snapshot",
                                 {"period": period, "cutoff": cutoff, "files": json.dumps(data_files, sort_keys=True, ensure_ascii=False)},
                                 {"refreshed_at": refreshed_at})
    runs = conn.execute("SELECT * FROM bonus_run WHERE period = ? ORDER BY id DESC" + ("" if reuse_any else " LIMIT 1"), (period,)).fetchall()
    latest = runs[0] if runs else None
    # "lúc chụp" là một phần của bản tính (nhãn nguồn trên bảng / file Excel): khác ⇒ bản mới thay bản cũ (đã chọn 17/11)
    for run in runs:
        if (run["rule_version_id"], run["data_snapshot_id"], run["code_sha256"]) == (rule_id, snapshot_id, code_sha) \
                and _run_refreshed_at(conn, run) == refreshed_at:
            return run["id"]
    run_id = conn.execute(
        "INSERT INTO bonus_run (period, rule_version_id, data_snapshot_id, code_sha256, code_files, team_total, replaces, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (period, rule_id, snapshot_id, code_sha, json.dumps(code_files), sum(line["amount"] for line in lines),
         latest["id"] if latest else None, _now())).lastrowid
    conn.executemany("INSERT INTO bonus_line (run_id, employee_id, kpi, pct, reached_tier, amount, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     [(run_id, l["employee_id"], l["kpi"], l["pct"], l["reached_tier"], l["amount"], l["reason"]) for l in lines])
    conn.execute("INSERT INTO run_refresh (run_id, refreshed_at, created_at) VALUES (?, ?, ?)", (run_id, refreshed_at, _now()))
    return run_id


def _run_refreshed_at(conn, run) -> str | None:
    """"Lúc chụp số liệu" của một bản tính: dòng run_refresh (từ 17/11); bản cũ chưa có ⇒ nhãn của data_snapshot như trước."""
    row = conn.execute("SELECT refreshed_at FROM run_refresh WHERE run_id = ?", (run["id"],)).fetchone()
    if row:
        return row["refreshed_at"]
    return conn.execute("SELECT refreshed_at FROM data_snapshot WHERE id = ?", (run["data_snapshot_id"],)).fetchone()["refreshed_at"]


def record_workflow_version(conn, name: str, content: str, sha256: str) -> int:
    """Một phiên bản = một mã băm LOGIC (`runner.workflow_sha256`: bỏ `position`, 04/11). Sửa một ô / dây ⇒ dòng mới, dòng cũ giữ nguyên.

    `content` = chữ gốc file lúc phiên bản này được ghi LẦN ĐẦU. Hệ quả: chỉ kéo khối (đổi position) ⇒ cùng mã băm ⇒ dùng lại dòng cũ, bố cục mới
    KHÔNG vào bảng — bố cục sống trong chính file workflow (giao diện bước 2 đọc file). Store không tự băm để khỏi phụ thuộc bộ chạy.
    
    """
    return _insert_or_get(conn, "workflow_version", {"sha256": sha256}, {"name": name, "content": content})


def record_workflow_run(conn, *, workflow_version_id: int, cutoff: str, status: str, steps: list[dict], errors: list[str],
                        inputs: dict, bonus_run_id: int | None) -> int:
    """Ghi một lượt chạy workflow (bước 1 S2 — thiết kế bản 2 mục 2–3). Chỉ thêm, như 5 bảng 2a.

    Thứ tự (B minor 2, chủ đề 17 mục 2.2b "bước sau chỉ chạy khi bước trước báo thành công"): lượt `ok` ghi SAU `record_run`, mang `bonus_run_id`
    của bản tính đó; lượt `stopped` ghi với `bonus_run_id` rỗng. SQLite giữ luật này bằng CHECK + khoá ngoại — `ok` mà không có bản tính,
    hay `stopped` mà trỏ bản tính, hay "rejected" (file sai, 0 khối chạy) đều không ghi được.
    `steps` / `errors` đã che mã (runner.mask_codes). `inputs` = dấu vân tay đầu vào thật (runner.input_fingerprint), không chỉ số phiên bản.
    """
    return conn.execute(
        "INSERT INTO workflow_run (workflow_version_id, cutoff, status, steps, errors, inputs, inputs_sha256, bonus_run_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (workflow_version_id, cutoff, status, json.dumps(steps, ensure_ascii=False), json.dumps(errors, ensure_ascii=False),
         json.dumps(inputs, sort_keys=True, ensure_ascii=False), inputs["sha256"], bonus_run_id, _now())).lastrowid


def workflow_run_summary(conn, run_id: int) -> dict | None:
    row = conn.execute("SELECT r.*, v.name, v.sha256 AS version_sha FROM workflow_run r JOIN workflow_version v ON v.id = r.workflow_version_id"
                       " WHERE r.id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return {"workflow_run_id": row["id"], "workflow": row["name"], "workflow_version_id": row["workflow_version_id"],
            "workflow_version": f"{row['name']}@{row['version_sha'][:8]}", "cutoff": row["cutoff"], "status": row["status"],
            "steps": json.loads(row["steps"]), "errors": json.loads(row["errors"]), "inputs": json.loads(row["inputs"]),
            "bonus_run_id": row["bonus_run_id"], "created_at": row["created_at"]}


def list_workflow_runs(conn) -> list[dict]:
    """Mọi lượt chạy workflow, mới nhất trước — kể cả lượt `stopped` của lần bật trước (backend không bật khi dừng ⇒ trang xem lượt đó ở lần bật sau)."""
    return [workflow_run_summary(conn, row["id"]) for row in conn.execute("SELECT id FROM workflow_run ORDER BY id DESC")]


def list_workflow_versions(conn) -> list[dict]:
    """Mọi phiên bản workflow, mới nhất trước (bước 2b: tab "Workflow" của trang) — không mang chữ file."""
    return [{"workflow_version_id": row["id"], "workflow": row["name"], "workflow_version": f"{row['name']}@{row['sha256'][:8]}",
             "created_at": row["created_at"]}
            for row in conn.execute("SELECT id, name, sha256, created_at FROM workflow_version ORDER BY id DESC")]


def record_workflow_trial_run(conn, *, workflow_version_id: int, cutoff: str, status: str, steps: list[dict], errors: list[str],
                              inputs: dict, summary: dict) -> int:
    """Ghi một lượt chạy THỬ từ trang (bước 2b) — không bản tính, không đổi số trang chính / trợ lý dùng. Đầu vào đã che mã."""
    return conn.execute(
        "INSERT INTO workflow_trial_run (workflow_version_id, cutoff, status, steps, errors, inputs, inputs_sha256, summary, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (workflow_version_id, cutoff, status, json.dumps(steps, ensure_ascii=False), json.dumps(errors, ensure_ascii=False),
         json.dumps(inputs, sort_keys=True, ensure_ascii=False), inputs["sha256"], json.dumps(summary, ensure_ascii=False), _now())).lastrowid


def list_workflow_trial_runs(conn) -> list[dict]:
    rows = conn.execute("SELECT r.*, v.name, v.sha256 AS version_sha FROM workflow_trial_run r JOIN workflow_version v ON v.id = r.workflow_version_id"
                        " ORDER BY r.id DESC")
    return [{"trial_run_id": row["id"], "workflow_version_id": row["workflow_version_id"], "workflow_version": f"{row['name']}@{row['version_sha'][:8]}",
             "cutoff": row["cutoff"], "status": row["status"], "steps": json.loads(row["steps"]), "errors": json.loads(row["errors"]),
             "summary": json.loads(row["summary"]), "created_at": row["created_at"]} for row in rows]


def workflow_version_content(conn, version_id: int) -> str:
    """Chữ gốc file workflow của một phiên bản (bước 2a: trang vẽ đúng phiên bản của lượt đang xem, không vẽ file hiện tại)."""
    return conn.execute("SELECT content FROM workflow_version WHERE id = ?", (version_id,)).fetchone()["content"]


def run_summary(conn, run_id: int) -> dict | None:
    run = conn.execute("SELECT r.*, v.file_name, v.sha256 AS rule_sha FROM bonus_run r JOIN rule_version v ON v.id = r.rule_version_id"
                       " WHERE r.id = ?", (run_id,)).fetchone()
    if run is None:
        return None
    snapshot = conn.execute("SELECT * FROM data_snapshot WHERE id = ?", (run["data_snapshot_id"],)).fetchone()
    review = conn.execute("SELECT * FROM approval_event WHERE run_id = ? AND action = 'reviewed' ORDER BY id LIMIT 1", (run_id,)).fetchone()
    newer = conn.execute("SELECT id FROM bonus_run WHERE replaces = ? ORDER BY id LIMIT 1", (run_id,)).fetchone()
    if review:
        at = datetime.fromisoformat(review["created_at"])
        status, label, model_label = "da_soat", REVIEWED_LABEL.format(actor=review["actor"], at=at), REVIEWED_LABEL_FOR_MODEL.format(at=at)
    else:
        (status, label), model_label = UNREVIEWED, UNREVIEWED[1]
    return {"run_id": run["id"], "period": run["period"], "created_at": run["created_at"],
            "rule_version": f"{run['file_name']}@{run['rule_sha'][:8]}", "rule_sha256": run["rule_sha"],
            "data_snapshot": {"cutoff": snapshot["cutoff"], "files_sha256": json.loads(snapshot["files"]), "refreshed_at": _run_refreshed_at(conn, run)},
            "code_sha": run["code_sha256"], "code_files": json.loads(run["code_files"]), "team_total": run["team_total"],
            "status": status, "status_label": label, "status_label_for_model": model_label,
            "reviewed_by": review["actor"] if review else None, "reviewed_at": review["created_at"] if review else None,
            "replaces": run["replaces"], "replaced_by": newer["id"] if newer else None}


def list_runs(conn, period: str) -> list[dict]:
    ids = [row["id"] for row in conn.execute("SELECT id FROM bonus_run WHERE period = ? ORDER BY id DESC", (period,))]
    return [run_summary(conn, run_id) for run_id in ids]


def run_lines(conn, run_id: int) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT employee_id, kpi, pct, reached_tier, amount, reason FROM bonus_line WHERE run_id = ? ORDER BY employee_id, id", (run_id,))]


def run_events(conn, run_id: int) -> list[dict]:
    return [{"event_id": row["id"], "action": row["action"], "actor": row["actor"], "note": row["note"], "at": row["created_at"]}
            for row in conn.execute("SELECT * FROM approval_event WHERE run_id = ? ORDER BY id", (run_id,))]


def rule_content(conn, run_id: int) -> str:
    return conn.execute("SELECT v.content FROM bonus_run r JOIN rule_version v ON v.id = r.rule_version_id WHERE r.id = ?",
                        (run_id,)).fetchone()["content"]


def reviewed_for_inputs(conn, inputs_sha256: str) -> bool:
    """Lượt có ĐÚNG dấu vân tay đầu vào này đã ra một bản tính ĐÃ SOÁT và chưa bị thay? — cổng người duyệt (bước 3) chỉ mở khi có.

    Duyệt gắn BẢN TÍNH (luật + dữ liệu + code + workflow), không gắn tên khối: — Capitol khoá lời duyệt theo (phiên, khối)
    nên duyệt cũ mở được cho nội dung mới. Bản bị thay (đổi luật / dữ liệu / code) ⇒ False, kể cả khi bản cũ đã soát. Chỉ đọc.
    """
    for row in conn.execute("SELECT DISTINCT bonus_run_id FROM workflow_run WHERE inputs_sha256 = ? AND bonus_run_id IS NOT NULL", (inputs_sha256,)):
        summary = run_summary(conn, row["bonus_run_id"])
        if summary["status"] == "da_soat" and summary["replaced_by"] is None:
            return True
    return False


def exported_formats(conn, run_id: int) -> list[str]:
    """Định dạng được tải của một bản tính: từ lượt workflow MỚI NHẤT của bản đó có khối xuất `ok` (bước 3 phần 2). Không có ⇒ [] (không file). Chỉ đọc.

    Đọc `steps` (bước `export_files` = ok) + ô `formats` trong ĐÚNG phiên bản workflow của lượt đó — không đọc file workflow hiện tại.
    ĐIỀU KIỆN ĐẦU: bản tính phải ĐÃ SOÁT — 10/11: workflow bỏ cổng (dây bonus ⇒ xuat) qua bộ kiểm file hợp lệ ⇒ bước xuất `ok` mà chưa ai
    soát ⇒ tải được. Sàn "chưa soát ⇒ không file" không được phụ thuộc file workflow có cổng hay không.
    """
    import yaml   # nạp muộn: store không cần yaml ở đường khác
    summary = run_summary(conn, run_id)
    # Bản bị thay cũng KHÔNG xuất (10/11: tiêu đề "TẠM TÍNH — NHÁP" trái lựa chọn bỏ nháp của User) — soát bản mới
    if summary is None or summary["status"] != "da_soat" or summary["replaced_by"] is not None:
        return []
    rows = conn.execute("SELECT r.steps, v.content FROM workflow_run r JOIN workflow_version v ON v.id = r.workflow_version_id"
                        " WHERE r.bonus_run_id = ? ORDER BY r.id DESC", (run_id,))
    for row in rows:
        done = {step["node"] for step in json.loads(row["steps"]) if step["type"] == "export_files" and step["status"] == "ok"}
        for node in yaml.safe_load(row["content"])["nodes"]:
            if node["id"] in done:
                return list((node.get("params") or {}).get("formats", ["xlsx", "md", "txt"]))
    return []


def mark_reviewed(conn, run_id: int, actor: str, note: str = "") -> dict:
    """Hành động ghi DUY NHẤT của 2a. Chỉ bản mới nhất, chưa soát, mới được soát (một lần)."""
    summary = run_summary(conn, run_id)
    if summary is None:
        raise Refused(f"không có bản tính số {run_id}", 404)
    if summary["replaced_by"] is not None:
        raise Refused(f"bản tính {run_id} đã bị thay bởi bản {summary['replaced_by']} — soát bản mới", 409)
    if summary["status"] == "da_soat":
        at = datetime.fromisoformat(summary["reviewed_at"])   # cùng khuôn giờ với nhãn (B minor 1)
        raise Refused(f"bản tính {run_id} đã soát bởi {summary['reviewed_by']} lúc {at:%d/%m/%Y %H:%M}", 409)
    event_id = conn.execute("INSERT INTO approval_event (run_id, action, actor, note, created_at) VALUES (?, 'reviewed', ?, ?, ?)",
                            (run_id, actor, note, _now())).lastrowid
    return {"event": next(e for e in run_events(conn, run_id) if e["event_id"] == event_id), "run": run_summary(conn, run_id)}


def record_capitol_approval(conn, run_id: int, table_sha256: str, org_id: str, reviewer: str) -> dict:
    """Ghi một lần duyệt của khối Người duyệt Capitol (người gọi đã so băm với bảng hiện tại). Chỉ thêm — duyệt lại = thêm dòng."""
    row_id = conn.execute("INSERT INTO capitol_approval (run_id, table_sha256, org_id, reviewer, created_at) VALUES (?, ?, ?, ?, ?)",
                          (run_id, table_sha256, org_id, reviewer, _now())).lastrowid
    return dict(conn.execute("SELECT * FROM capitol_approval WHERE id = ?", (row_id,)).fetchone())


def capitol_approval_for(conn, run_id: int, table_sha256: str) -> dict | None:
    """Lần duyệt Capitol mới nhất của ĐÚNG bản tính + ĐÚNG băm bảng — không có ⇒ None (bảng đổi sau khi duyệt ⇒ băm khác ⇒ None)."""
    row = conn.execute("SELECT * FROM capitol_approval WHERE run_id = ? AND table_sha256 = ? ORDER BY id DESC LIMIT 1",
                       (run_id, table_sha256)).fetchone()
    return dict(row) if row else None


def record_mapping(conn, table: str, fingerprint: str, mapping: dict, mapping_sha: str, actor: str, note: str) -> int:
    """Ghi một cách ghép đã xác nhận (bậc 2 bước 2.3). Không dùng lại dòng cũ: mỗi lần xác nhận là một sự kiện (ai, lúc nào)."""
    return conn.execute("INSERT INTO column_mapping (table_name, layout_fingerprint, mapping, mapping_sha, confirmed_by, note, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (table, fingerprint, json.dumps(mapping, ensure_ascii=False, sort_keys=True), mapping_sha, actor, note, _now())).lastrowid


def mappings_of(conn, table: str) -> list[dict]:
    """Cách ghép MỚI NHẤT của mỗi vân tay bố cục (mới trước) — đường tải lên dò file thuộc bố cục nào."""
    rows = conn.execute("SELECT * FROM column_mapping WHERE table_name = ? AND id IN (SELECT MAX(id) FROM column_mapping WHERE table_name = ?"
                        " GROUP BY layout_fingerprint) ORDER BY id DESC", (table, table)).fetchall()
    return [{"id": row["id"], "fingerprint": row["layout_fingerprint"], "mapping": json.loads(row["mapping"]), "mapping_sha": row["mapping_sha"]}
            for row in rows]


def latest_mapping(conn, table: str, fingerprint: str) -> dict | None:
    """Cách ghép người xác nhận MỚI NHẤT cho bố cục này; chưa có ⇒ None."""
    row = conn.execute("SELECT * FROM column_mapping WHERE table_name = ? AND layout_fingerprint = ? ORDER BY id DESC LIMIT 1",
                       (table, fingerprint)).fetchone()
    return None if row is None else {"id": row["id"], "mapping": json.loads(row["mapping"]), "mapping_sha": row["mapping_sha"],
                                     "confirmed_by": row["confirmed_by"], "created_at": row["created_at"]}


def record_uploaded_version(conn, *, data_version: str, kind: str, period: str, wanted_cutoff: str | None, base_data_version: str | None,
                            file_sha256: str, mapping_sha: str | None) -> str:
    """Ghi một bản dữ liệu vừa nhận qua cửa tải lên (2j) ⇒ trả giờ ghi (= giờ tải, trang Dữ liệu hiện)."""
    at = _now()
    conn.execute("INSERT INTO uploaded_version (data_version, kind, period, wanted_cutoff, base_data_version, file_sha256, mapping_sha, created_at)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (data_version, kind, period, wanted_cutoff, base_data_version, file_sha256, mapping_sha, at))
    return at


def record_pending_upload(conn, *, kind: str, period: str, wanted_cutoff: str | None, file_sha256: str) -> None:
    """Ghi một file vừa nhận vào bộ chờ của kỳ trống."""
    conn.execute("INSERT INTO pending_upload (kind, period, wanted_cutoff, file_sha256, created_at) VALUES (?, ?, ?, ?, ?)",
                 (kind, period, wanted_cutoff, file_sha256, _now()))


def pending_uploads(conn, period: str) -> dict[str, dict]:
    """{loại file: dòng MỚI NHẤT của loại đó} trong bộ chờ của `period`."""
    return {row["kind"]: dict(row) for row in conn.execute("SELECT * FROM pending_upload WHERE period = ? ORDER BY id", (period,))}


def uploaded_versions(conn, limit: int) -> list[dict]:
    """`limit` bản tải lên MỚI NHẤT, theo thứ tự tải (cũ trước) — thứ tự backend nạp lại lúc bật (bản nền luôn được tải trước bản chồng lên nó)."""
    rows = conn.execute("SELECT * FROM uploaded_version ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in reversed(rows)]


def auto_employee_masks(conn) -> dict[str, str]:
    """{NVxx: mã thật} đã tự cấp trong `auto_employee_mask` (theo thứ tự thêm)."""
    rows = conn.execute("SELECT masked_code, real_code FROM auto_employee_mask ORDER BY id").fetchall()
    return {row["masked_code"]: row["real_code"] for row in rows}


def plan_auto_employee_masks(base_map: dict[str, str], existing_auto_map: dict[str, str],
                               new_employees: dict[str, str]) -> list[dict[str, str]]:
    """Tính mã che kế tiếp (NV15…) theo `sorted(new_employees)` mà chưa ghi DB — dùng chung cho xem trước và ghi thật."""
    known_real = {str(v) for v in base_map.values()} | {str(v) for v in existing_auto_map.values()}
    used_masked = set(base_map) | set(existing_auto_map)
    next_num = max((int(code[2:]) for code in used_masked if re.fullmatch(r"NV\d+", code)), default=0) + 1
    planned = []
    for real_code in sorted(new_employees):
        if str(real_code) in known_real:
            continue
        while f"NV{next_num:02d}" in used_masked:
            next_num += 1
        masked = f"NV{next_num:02d}"
        used_masked.add(masked)
        known_real.add(str(real_code))
        planned.append({"masked_code": masked, "real_code": str(real_code), "full_name": str(new_employees[real_code] or "").strip()})
        next_num += 1
    return planned


def record_auto_employee_masks(conn, base_map: dict[str, str], new_employees: dict[str, str]) -> list[dict[str, str]]:
    """Cấp và lưu nối tiếp mã che mới (NV15…) vào `auto_employee_mask`; trả danh sách chỉ gồm mã che + họ tên (không mã thật)."""
    planned = plan_auto_employee_masks(base_map, auto_employee_masks(conn), new_employees)
    now = _now()
    for entry in planned:
        conn.execute(
            "INSERT INTO auto_employee_mask (masked_code, real_code, full_name, created_at) VALUES (?, ?, ?, ?)",
            (entry["masked_code"], entry["real_code"], entry["full_name"], now),
        )
    return [{"masked_code": entry["masked_code"], "full_name": entry["full_name"]} for entry in planned]
