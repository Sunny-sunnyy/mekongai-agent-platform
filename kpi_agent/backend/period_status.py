"""Kỳ này đã có / thiếu gì — dữ liệu cho `GET /api/system/ky/{kỳ}` và `GET /api/system/ky` (trang Dữ liệu của bộ Capitol hiện, không tự suy).

CHỈ ĐỌC trạng thái backend đang giữ (bản lúc bật + các bản đã tải lên trong bộ nhớ) và TÊN / mã băm file; không mở file dữ liệu, không tính lại.
Phản hồi không có số tiền, không có mã / tên nhân viên, không có đường dẫn tuyệt đối (đường file đi qua `shown_path` của `main`).
Vì sao là file riêng mà nhận hàm của `main` qua tham số: `main` nhập file này, và phép tìm luật theo kỳ / file luật phụ phải là ĐÚNG phép đường
tải lên đang dùng — chép lại thì hai nơi lệch nhau.
"""
import re
from pathlib import Path

from kpi_agent.backend import upload
from kpi_agent.workflow import runner

PERIOD = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")     # chỉ [0-9]: `\d` nhận cả chữ số Unicode
REQUIRED = ("raw", "chi_tieu", "tuyen", "luat_chinh")  # bốn mục quyết `ready`; luật phụ thiếu chỉ liệt kê (khoản đó tự báo lỗi ở đường của nó)
MISSING_FILE = "không có file"                         # chuỗi `main._sha256_or_missing` ghi thay mã băm
# Bản tải lên chỉ nằm trong bộ nhớ: backend bật lại là mất khỏi `versions` (file + bản tính vẫn còn trên đĩa) — trang phải nói thật với người dùng.
UPLOADS_SURVIVE_RESTART = False
EXTRA_PARTS = (("thuong_them", "extra_bonus_"), ("gia_vi_1pct", "gia_vi_1pct_"), ("hoi_vien", "hoi_vien_"))
NEXT_STEP = {
    ("raw", "thieu"): "tải file đơn hàng của kỳ này lên (loại raw)",
    ("chi_tieu", "thieu"): "tải file chỉ tiêu của kỳ này lên (loại chi_tieu)",
    ("chi_tieu", "dung_cua_ky_khac"): "tải file chỉ tiêu của kỳ này lên (loại chi_tieu) — hiện chỉ có file của kỳ khác",
    ("tuyen", "thieu"): "cần danh sách điểm bán trong tuyến của kỳ này",
    ("tuyen", "dung_cua_ky_khac"): "cần danh sách điểm bán trong tuyến của kỳ này — hiện chỉ có file của kỳ khác",
    ("luat_chinh", "thieu"): "cần file luật của kỳ này (người phát triển soạn từ công văn rồi thêm vào hệ thống)",
    ("luat_phu", "thieu"): "cần file luật phụ của kỳ này cho các khoản còn thiếu (thưởng thêm, 1 % gia vị, hội viên)",
}


def valid_period(period: str) -> bool:
    return PERIOD.fullmatch(period) is not None


def _latest_state(boot, period: str):
    """Bản dữ liệu mới nhất của kỳ: bản tải lên sau cùng (dict giữ thứ tự thêm), không có thì bản lúc bật nếu đúng kỳ; không có bản nào ⇒ None."""
    uploaded = [state for state in boot.versions.values() if state.ctx.period == period]
    if uploaded:
        return uploaded[-1]
    return boot if boot.ctx.period == period else None


def _item(key: str, state: str, detail: str, **more) -> dict:
    base = {"key": key, "state": state, "checked": None, "file": None, "sha256_8": None, "from_upload": False, "uploaded_at": None,
            "uploaded_by": None,   # cửa tải lên không nhận tên người tải ⇒ luôn None tới khi cửa có tham số đó
            "detail": detail, "next_step": NEXT_STEP.get((key, state))}
    return {**base, **more}


def _file_of(state, block_type: str, param: str, shown_path) -> tuple[str, str | None]:
    """(đường file để hiện, 8 ký tự đầu mã băm) của một ô file trong workflow của bản `state`; mã băm lấy từ `files_sha256` đã tính, không băm lại."""
    path = runner.repo_path(runner.node_param(state.workflow, block_type, param))
    sha = state.files_sha256.get(path.name)
    return shown_path(path), (sha[:8] if sha and sha != MISSING_FILE else None)


def _data_item(boot, state, period: str, shown_path) -> dict:
    block = runner.data_block_type(boot.workflow)
    kind = "file đơn hàng" if block == "read_raw" else "sổ Tracking"
    if state is None:
        return _item("raw", "thieu", f"chưa có {kind} của kỳ {period}")
    shown, sha = _file_of(state, block, "path", shown_path)
    uploaded = state is not boot
    detail = f"{kind} đã tải lên và qua kiểm" if uploaded else f"{kind} backend nạp lúc bật"
    # checked: True = đã đi qua cửa tải lên của kỳ này · None = bản lúc bật (qua kiểm của bộ chạy khi bật, không qua cửa tải lên)
    return _item("raw", "co", detail, checked=True if uploaded else None, file=shown, sha256_8=sha, from_upload=uploaded,
                 uploaded_at=getattr(state, "uploaded_at", None) if uploaded else None,
                 cutoff=state.ctx.cutoff.isoformat(), data_version=state.data_version)


def _fixed_item(key: str, what: str, block_type: str, param: str, boot, state, period: str, shown_path) -> dict:
    """Chỉ tiêu / danh sách tuyến: file của workflow. Kỳ hỏi khác kỳ gốc mà bản của kỳ đó không mang file riêng ⇒ `dung_cua_ky_khac`
    (cùng luật với `main._refuse_fixed_files_of_other_period`: không tính kỳ này bằng file của kỳ khác)."""
    boot_path = runner.node_param(boot.workflow, block_type, param)
    own = state is not None and state is not boot and runner.node_param(state.workflow, block_type, param) != boot_path
    if own:
        shown, sha = _file_of(state, block_type, param, shown_path)
        return _item(key, "co", f"{what} đã tải lên và qua kiểm", checked=True, file=shown, sha256_8=sha, from_upload=True,
                     uploaded_at=getattr(state, "uploaded_at", None))
    shown, sha = _file_of(boot, block_type, param, shown_path)
    if period == boot.ctx.period:
        if sha is None:
            return _item(key, "thieu", f"{what} của kỳ {period} không đọc được", file=shown)
        return _item(key, "co", f"{what} backend nạp lúc bật", file=shown, sha256_8=sha)
    return _item(key, "dung_cua_ky_khac", f"chưa có {what} của kỳ {period} — backend chỉ có file của kỳ {boot.ctx.period}", file=shown, sha256_8=sha)


def _route_item(boot, state, period: str, shown_path, data: dict) -> dict:
    """Danh sách điểm bán trong tuyến. CHỖ NỐI DUY NHẤT khi nguồn tuyến đổi (kế hoạch 2a-1: rút từ sổ Tracking tải lên) — sửa hàm này, không sửa nơi gọi."""
    if runner.data_block_type(boot.workflow) != "read_raw":
        # đường sổ Tracking: tuyến nằm ngay trong sổ ⇒ có / thiếu theo đúng mục dữ liệu
        detail = "danh sách tuyến nằm trong sổ Tracking" if data["state"] == "co" else f"chưa có sổ Tracking của kỳ {period}"
        return _item("tuyen", data["state"], detail, checked=data["checked"], file=data["file"], sha256_8=data["sha256_8"],
                     from_upload=data["from_upload"], uploaded_at=data["uploaded_at"])
    return _fixed_item("tuyen", "danh sách điểm bán trong tuyến", "read_raw", "mcp_path", boot, state, period, shown_path)


def _main_rules_item(boot, state, period: str, shown_path, rules_file_for_period, sha256_or_missing) -> dict:
    try:
        path = runner.repo_path(rules_file_for_period(boot.workflow, period))
    except upload.UploadRefused as refusal:
        return _item("luat_chinh", "thieu", refusal.reason)     # lời do code viết: kỳ + đường file tương đối, không có chữ của người dùng
    sha = sha256_or_missing(path)
    if sha == MISSING_FILE:
        return _item("luat_chinh", "thieu", f"file luật của kỳ {period} không đọc được", file=shown_path(path))
    # bản đang chạy của kỳ ⇒ đúng bản luật nó đã nạp; chưa có bản nào ⇒ tên file @ mã băm của file đang nằm trên đĩa
    version = state.ctx.rule_version if state is not None else f"{path.name}@{sha[:8]}"
    return _item("luat_chinh", "co", "có file luật của kỳ này", checked=None if state is not None else False, file=shown_path(path),
                 sha256_8=sha[:8], rule_version=version)


def _extra_rules_item(boot, period: str, extra_files_sha256) -> dict:
    files = extra_files_sha256(boot.workflow, period)
    parts = {}
    for part, prefix in EXTRA_PARTS:
        shas = [sha for name, sha in files.items() if Path(name).name.startswith(prefix)]
        parts[part] = "co" if shas and shas[0] != MISSING_FILE else "thieu"
    missing = [part for part, state in parts.items() if state != "co"]
    if not missing:
        return _item("luat_phu", "co", "đủ file luật phụ của kỳ (thưởng thêm, 1 % gia vị, hội viên)", parts=parts)
    return _item("luat_phu", "thieu", "thiếu file luật phụ của kỳ cho: " + ", ".join(missing), parts=parts)


def period_status(boot, period: str, *, rules_file_for_period, extra_files_sha256, sha256_or_missing, shown_path) -> dict:
    """`boot` = `app.state` gốc (bản lúc bật + `versions`). `period` đã qua `valid_period`."""
    state = _latest_state(boot, period)
    data = _data_item(boot, state, period, shown_path)
    items = [data,
             _fixed_item("chi_tieu", "file chỉ tiêu", "read_targets", "path", boot, state, period, shown_path),
             _route_item(boot, state, period, shown_path, data),
             _main_rules_item(boot, state, period, shown_path, rules_file_for_period, sha256_or_missing),
             _extra_rules_item(boot, period, extra_files_sha256)]
    states = {item["key"]: item["state"] for item in items}
    return {"period": period, "base_period": boot.ctx.period,
            "data_source": "raw" if runner.data_block_type(boot.workflow) == "read_raw" else "tracking",
            "ready": all(states[key] == "co" for key in REQUIRED), "uploads_survive_restart": UPLOADS_SURVIVE_RESTART,
            "missing": [key for key, value in states.items() if value != "co"], "items": items}


def known_periods(boot, rules_dir: Path | None) -> list[str]:
    """Các kỳ backend biết: kỳ của bản lúc bật, của các bản đã tải lên, và kỳ có file luật chính cùng khuôn tên với luật của workflow
    (rules_2026_10_raw.yaml ⇒ rules_<YYYY>_<MM>_raw.yaml) trong thư mục luật."""
    periods = {boot.ctx.period, *(state.ctx.period for state in boot.versions.values())}
    base = runner.repo_path(runner.node_param(boot.workflow, "load_rules", "path"))
    token = boot.ctx.period.replace("-", "_")
    if base.name.count(token) == 1:
        before, after = base.name.split(token)
        for path in (rules_dir or base.parent).glob(f"{before}*{after}"):
            found = path.name[len(before):len(path.name) - len(after)].replace("_", "-")
            if valid_period(found):
                periods.add(found)
    return sorted(periods)
