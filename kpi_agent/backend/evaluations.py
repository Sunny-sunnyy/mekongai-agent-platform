"""Số cho tab "Đánh giá" (kế hoạch).

Chỉ ĐỌC kết quả đã đo — không đo thêm, không gọi AI. Sổ nguồn `config/so_danh_gia.yaml` chỉ ghi CHỖ LẤY SỐ của từng phiên bản (tên thư mục lượt đo,
file chấm, file kiểm + mẫu tìm) — không gõ số tay. Mỗi số trả về kèm nguồn; không tìm thấy ⇒ `None` + lý do "chưa có …" (không đoán).
Vì sao lượt đo phải ghi rõ TÊN thư mục (không quét theo mẫu ngày): thư mục lượt đo có cả lượt kiểm cuối / lượt đang chấm mù (bẫy 04/11).
Ngoài `main.CODE_FILES`: chỉ trình bày ⇒ sửa file này không đổi dấu vân tay bản tính."""
import json
import re
import subprocess
from pathlib import Path

import yaml

NOISE_NOTE = "mọi lượt đo chạy 1 lần — chưa đo nhiễu"
KEEP, ROLL_BACK, UNDECIDED = "GIỮ", "QUAY LẠI", "CHƯA KẾT LUẬN"
M3_PASS_MIN = 19   # luật nhãn ghi TRƯỚC khi dựng bảng (kế hoạch mục 3): M3 ≥ 19 / 20 theo máy chấm, hoặc theo người đọc nghĩa khi máy chấm còn lỗi đã ghi


def _number(value, source: str) -> dict:
    return {"value": value, "source": source}


def _missing(why: str) -> dict:
    return {"value": None, "source": f"chưa có — {why}"}


def read_t4_run(run_dir: Path, shown: str) -> dict:
    """Lượt đo bộ câu của trợ lý chat (`meta.json`, `grades.json`, `answers.jsonl`). Thiếu file / khoá ⇒ từng số "chưa có"."""
    out = {"run": shown}
    try:
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        for key in ("git_head", "model", "reasoning", "cutoff"):
            out[key] = _number(meta[key], f"{shown}/meta.json#{key}") if key in meta else _missing(f"meta.json không có {key}")
    except (OSError, ValueError):
        for key in ("git_head", "model", "reasoning", "cutoff"):
            out[key] = _missing("không đọc được meta.json")
    try:
        summary = json.loads((run_dir / "grades.json").read_text(encoding="utf-8"))["summary"]
        src = f"{shown}/grades.json#summary"
        out["tool_ok"] = _number(f"{summary['tool_ok']['pass']} / {summary['tool_ok']['of']}", src + ".tool_ok")
        out["args_ok"] = _number(f"{summary['args_ok']['pass']} / {summary['args_ok']['of']}", src + ".args_ok")
        out["unsourced_final"] = _number(summary["unsourced_final"], src + ".unsourced_final")
        out["false_block"] = _number(summary["false_block"], src + ".false_block")
        out["ran"] = _number(f"{summary['ran']} / {summary['total']}", src + ".ran")
        failed = [name for name, ok in summary["verdict"].items() if not ok]
        out["verdict_all"] = _number(not failed, src + ".verdict")
        out["verdict_failed"] = failed
    except (OSError, ValueError, KeyError, TypeError):
        for key in ("tool_ok", "args_ok", "unsourced_final", "false_block", "ran", "verdict_all"):
            out[key] = _missing("không đọc được grades.json")
        out["verdict_failed"] = []
    try:
        answers = [json.loads(line) for line in (run_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        usd = [a["usd"] for a in answers if a.get("usd") is not None]
        seconds = [a["seconds"] for a in answers if a.get("seconds") is not None]
        out["usd"] = _number(round(sum(usd), 6), f"{shown}/answers.jsonl (cộng {len(usd)} câu)") if usd else _missing("answers.jsonl không có tiền")
        out["seconds_per_question"] = (_number(round(sum(seconds) / len(seconds), 1), f"{shown}/answers.jsonl (trung bình {len(seconds)} câu)")
                                       if seconds else _missing("answers.jsonl không có giây"))
    except (OSError, ValueError):
        out["usd"] = out["seconds_per_question"] = _missing("không đọc được answers.jsonl")
    return out


def read_m3(path: Path, shown: str) -> dict:
    """File máy chấm 20 câu trên bộ Capitol (`M3_cham_*.json`, khoá `tong`)."""
    try:
        total = json.loads(path.read_text(encoding="utf-8"))["tong"]
        src = f"{shown}#tong"
        return {"passed": _number(f"{total['dat']} / {total['n']}", src + ".dat"), "passed_count": total["dat"], "threshold": _number(total["nguong"], src + ".nguong"),
                "failed_questions": total.get("truot", []), "usd": _number(total["tien"], src + ".tien"),
                "machine_verdict": _number(total.get("ket_luan"), src + ".ket_luan")}
    except (OSError, ValueError, KeyError, TypeError):
        return _no_m3(f"không đọc được {shown}")


def _no_m3(why: str) -> dict:
    return {"passed": _missing(why), "passed_count": None, "threshold": _missing("—"), "failed_questions": [], "usd": _missing("—"), "machine_verdict": _missing("—")}


def read_capitol_8_dir(folder: Path, shown: str) -> dict:
    """Thư mục 8 câu trên bộ Capitol (mỗi câu một file `NN.json`): bao nhiêu câu qua Rào chắn tới người hỏi + tiền. Chỉ đọc cờ và tiền, không đọc chữ trả lời."""
    files = sorted(folder.glob("[0-9][0-9].json")) if folder.is_dir() else []
    try:
        answers = [json.loads(path.read_text(encoding="utf-8")) for path in files]
        if not answers:
            raise ValueError("thư mục không có file câu nào")
        return {"gate_passed": _number(f"{sum(1 for a in answers if a['rao_qua'] is True)} / {len(answers)}", f"{shown}/NN.json#rao_qua"),
                "usd": _number(round(sum(a["tien"] for a in answers), 6), f"{shown}/NN.json#tien (cộng {len(answers)} câu)")}
    except (OSError, ValueError, KeyError, TypeError):
        return {"gate_passed": _missing(f"không đọc được {shown}"), "usd": _missing("—")}


def read_pattern(path: Path, shown: str, pattern: str, occurrence: int = 1) -> list[dict]:
    """Số chỉ có trong chữ của file kiểm: lần khớp thứ `occurrence` của mẫu ⇒ mỗi nhóm bắt một số (kèm `file:dòng`). Không khớp ⇒ [] (người gọi ghi
    "chưa có") — không lấy lần khớp khác thay."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    seen = 0
    for number, line in enumerate(lines, start=1):
        found = re.search(pattern, line)
        if found:
            seen += 1
            if seen == occurrence:
                return [_number(int(group) if group.isdigit() else group, f"{shown}:{number}") for group in found.groups()]
    return []


def label(row: dict, previous: dict | None) -> tuple[str, str]:
    """Nhãn GIỮ / QUAY LẠI / CHƯA KẾT LUẬN — tiền sai xét TRƯỚC (kế hoạch mục 3, ghi trước khi dựng bảng). Thiếu số ⇒ không bao giờ ra GIỮ."""
    money_wrong = row["capitol_8"]["money_wrong"]["value"]
    leak, leak_before = row["sealed_set_leak"]["value"], previous["sealed_set_leak"]["value"] if previous else None
    if money_wrong is not None and money_wrong > 0:
        return ROLL_BACK, f"{money_wrong} câu tiền sai trong 8 câu trên bộ Capitol"
    if leak is not None and leak_before is not None and leak > leak_before:
        return ROLL_BACK, f"câu sai lọt rào ở bộ giữ kín tăng {leak_before} ⇒ {leak}"
    m3_machine, m3_reading = row["m3"]["passed_count"], row["m3_reading"]["value"]
    m3_ok = (m3_machine is not None and m3_machine >= M3_PASS_MIN) or (m3_reading is not None and m3_reading >= M3_PASS_MIN)
    t4_ok = row["t4"]["verdict_all"]["value"] is True if row["t4"] else False
    if money_wrong == 0 and m3_ok and t4_ok:
        return KEEP, "0 câu tiền sai; 20 câu trên Capitol đạt ngưỡng; bộ câu trợ lý chat đạt hết tiêu chí"
    missing = [name for name, absent in (("8 câu Capitol", money_wrong is None), ("20 câu Capitol", m3_machine is None and m3_reading is None),
                                         ("bộ câu trợ lý chat", not row["t4"] or row["t4"]["verdict_all"]["value"] is None)) if absent]
    if missing:
        return UNDECIDED, "chưa có số: " + ", ".join(missing)
    return UNDECIDED, "có tiêu chí chưa đạt: " + ", ".join(
        name for name, bad in (("20 câu Capitol dưới ngưỡng", not m3_ok), ("bộ câu trợ lý chat", not t4_ok)) if bad)


def changes_between(repo: Path, older: str, newer: str) -> dict:
    """"Đổi gì so bản trước": chủ đề các commit + dòng tổng `git diff --stat` thư mục `kpi_agent/`. Commit không có trong kho ⇒ "chưa có"."""
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    try:
        subjects = git("log", "--no-merges", "--format=%h %s", f"{older}..{newer}", "--", "kpi_agent").splitlines()
        stat = git("diff", "--stat", older, newer, "--", "kpi_agent").splitlines()
        return {"commits": subjects[:30], "commit_count": len(subjects), "diffstat": stat[-1].strip() if stat else "không đổi file nào trong kpi_agent/"}
    except (subprocess.SubprocessError, OSError):
        return {"commits": [], "commit_count": None, "diffstat": f"chưa có — không đọc được git {older}..{newer}"}


def _resolve(repo: Path, text: str) -> Path:
    path = Path(text)
    return path if path.is_absolute() else repo / path


def build(repo: Path, ledger_path: Path) -> dict:
    """Dựng dữ liệu tab từ sổ nguồn. Sổ sai hình (thiếu `versions`, thiếu `id` / `commit`) ⇒ ValueError — không dựng bảng nửa vời."""
    ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8"))
    if not isinstance(ledger, dict) or not isinstance(ledger.get("versions"), list) or not ledger["versions"]:
        raise ValueError("sổ nguồn phải có danh sách `versions`")
    rows, previous = [], None
    for entry in ledger["versions"]:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("commit"):
            raise ValueError("mỗi phiên bản trong sổ nguồn cần `id` và `commit`")
        row = {"id": entry["id"], "commit": entry["commit"], "note": entry.get("note")}
        row["t4"] = read_t4_run(_resolve(repo, entry["t4_run"]), Path(entry["t4_run"]).name) if entry.get("t4_run") else None
        row["m3"] = read_m3(_resolve(repo, entry["m3_file"]), Path(entry["m3_file"]).name) if entry.get("m3_file") else _no_m3("sổ nguồn chưa ghi file chấm 20 câu")
        found = {}
        for key in ("capitol_8", "m3_reading", "sealed_set_leak"):
            spec = entry.get(key)
            found[key] = read_pattern(_resolve(repo, spec["file"]), Path(spec["file"]).name, spec["pattern"], spec.get("occurrence", 1)) if spec else None
        eight = found["capitol_8"]
        why = "sổ nguồn chưa ghi file chấm 8 câu" if eight is None else "mẫu tìm không khớp dòng nào trong file chấm"
        row["capitol_8"] = dict(zip(("correct", "money_wrong", "received"), eight)) if eight and len(eight) == 3 else {
            key: _missing(why) for key in ("correct", "money_wrong", "received")}
        row["capitol_8"].update(read_capitol_8_dir(_resolve(repo, entry["capitol_8_dir"]), Path(entry["capitol_8_dir"]).name) if entry.get("capitol_8_dir")
                                else {"gate_passed": _missing("sổ nguồn chưa ghi thư mục 8 câu"), "usd": _missing("—")})
        for key, name in (("m3_reading", "người đọc nghĩa 20 câu"), ("sealed_set_leak", "số câu sai lọt rào ở bộ giữ kín")):
            row[key] = found[key][0] if found[key] else _missing(f"sổ nguồn chưa ghi {name}" if found[key] is None else "mẫu tìm không khớp dòng nào")
        row["label"], row["label_reason"] = label(row, previous)
        row["changes"] = changes_between(repo, previous["commit"], entry["commit"]) if previous else None
        rows.append(row)
        previous = row
    return {"versions": rows, "noise_note": NOISE_NOTE,
            "label_rule": f"{ROLL_BACK}: có câu tiền sai (8 câu Capitol) hoặc câu sai lọt rào ở bộ giữ kín tăng · {KEEP}: 0 tiền sai, 20 câu Capitol ≥ {M3_PASS_MIN} / 20 "
                          f"(máy chấm hoặc người đọc nghĩa), bộ câu trợ lý chat đạt hết · còn lại: {UNDECIDED}"}
