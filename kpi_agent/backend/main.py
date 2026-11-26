"""Backend chỉ đọc của trợ lý KPI (bậc 1 — `kpi_agent/docs/SPEC.md` mục 3–4): chỗ giữ luật + kiểm mọi tham số.

Vì sao tách backend (SPEC mục 3, khoá AI FDE 2.2): lỗi 02/11 "NV01/../NV02" lọt vì rào nằm sau mô hình. Ở đây mọi tham số (mã NV, KPI,
kỳ) được kiểm trong `queries.py` cho MỌI bên gọi — agent, trang React, hay ai gọi thẳng HTTP. Số chỉ do lõi `core/` tính.
- Tham số sai ⇒ HTTP 200 kèm {"refused": lý do, "params_used": …} (mô hình đọc được để nói lại), không 500, không số nào.
- Số đang dùng tính MỘT lần lúc bật server (~1,6 s), ngày chốt cố định qua biến môi trường (không lấy giờ máy). Đường ghi (chỉ thêm, SQLite): "Đã soát" (2a);
  lưu phiên bản workflow + ghi lượt CHẠY THỬ từ trang kéo–thả (2b, cuối file) — chạy thử không đổi số đang dùng. Agent không có đường ghi (backend_client chỉ GET).
Chạy (từ gốc repo, chỉ máy này): .venv/bin/uvicorn kpi_agent.backend.main:app --host 127.0.0.1 --port 8110
"""
import copy
import functools
import hashlib
import logging
import re
import hmac
import json
import os
import tempfile
import threading
import unicodedata
from collections import OrderedDict, defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, Query, Request
from fastapi import Path as FastApiPath
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from kpi_agent.backend import opportunities as opportunity_rules
from kpi_agent.agent import tool_specs
from kpi_agent.agent.kiem_so import VERSION as KIEM_SO_VERSION, kiem_so
from kpi_agent.agent.owner_check import with_team_sentences
from kpi_agent.agent import names as agent_names
from kpi_agent.core.load_raw import RECONCILE_STOP   # bậc 1 nạp chung: lời cổng đối soát được chuyển nguyên khi tải file lên
from kpi_agent.agent.backend_client import (ALL_TOOL_PARAMS, META_FIELDS_FOR_MODEL, RUN_FIELDS_FOR_MODEL, AgentContext, _drop_zero_partner,
                                            check_tool_args, context_numbers)
from kpi_agent import session_key
from kpi_agent.backend import calc_tool, export_report, extra_bonus_tool, gap_status, membership, order_lines, outlets, period_status, queries, spice_loyalty, spice_tool, store, team_ranking, tracking_view, upload, what_if, workflow_edit, workflow_view
from kpi_agent.workflow import direct_tools, runner

REPO_ROOT = Path(__file__).resolve().parents[2]
# Bước 1 S3: số = một lượt chạy file workflow; đường file luật / dữ liệu nằm TRONG file workflow (một nguồn — không giữ bản sao ở đây).
# KPI_WORKFLOW đổi file workflow lúc bật (như KPI_STORE) ⇒ kiểm W3 trỏ được sang bản sao dữ liệu hỏng mà không sửa file thật.
WORKFLOW = Path(os.getenv("KPI_WORKFLOW", str(runner.DEFAULT_WORKFLOW)))
MASK = REPO_ROOT / "kpi_agent/data/ma_che_ten.json"
# Ngày chốt số = tham số ("không lấy giờ máy"); 20/10 = ngày đã đo đáp án 112/112 (KET_QUA mục 2)
CUTOFF = date.fromisoformat(os.getenv("KPI_CUTOFF", "2026-10-20"))
# Bậc 2a: bảng lưu bản tính (Git bỏ qua); đọc biến môi trường KPI_STORE lúc bật ⇒ test chạy trên file tạm
DEFAULT_STORE = REPO_ROOT / "kpi_agent/data/kpi_store.sqlite"
# Code quyết định số (A 13:55): đổi các file này ⇒ bản tính mới; đổi file khác (agent, trang, test) ⇒ không
CODE_FILES = sorted((REPO_ROOT / "kpi_agent/core").glob("*.py")) + [REPO_ROOT / "kpi_agent/backend/queries.py"] + \
    sorted((REPO_ROOT / "kpi_agent/workflow").glob("*.py"))     # S3: khối + bộ chạy cũng quyết định số
# Mã lượt / bản tính trong đường dẫn: 1 … 2^63−1 (giới hạn số nguyên SQLite). 2a GĐ1 (minor): số lớn hơn ⇒ OverflowError ⇒ 500 — chặn ở tham số ⇒ 422
SQLITE_MAX_INT = 2**63 - 1
RUN_ID = FastApiPath(ge=1, le=SQLITE_MAX_INT)
PROVISIONAL_LABEL = "TẠM TÍNH — số chụp giữa tháng, chưa phải số chốt (Admin miền chốt, giám sát + NPP ký)"


def _provisional_label(state) -> str:
    """Nhãn tạm tính theo ĐÚNG nguồn dữ liệu đang chạy: đường RAW tính từ file đơn hàng tới ngày chốt (vd 31/10 — không phải "giữa tháng");
    đường sổ Tracking mới là số chụp giữa tháng.
    """
    if runner.data_block_type(state.workflow) == "read_raw":
        return f"TẠM TÍNH — số tính từ file đơn hàng tới ngày chốt {state.ctx.cutoff:%d/%m/%Y}, chưa phải số chốt (Admin miền chốt, giám sát + NPP ký)"
    return PROVISIONAL_LABEL


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workflow_files(workflow: dict) -> tuple[Path, Path, Path]:
    """(file luật, file chỉ tiêu, sổ Tracking) mà file workflow trỏ tới."""
    return tuple(runner.repo_path(runner.node_param(workflow, block_type, "path"))
                 for block_type in ("load_rules", "read_targets", runner.data_block_type(workflow)))


def _files_sha256(workflow: dict) -> dict[str, str]:
    """{tên file: sha256 nội dung} của MỌI file đầu vào của workflow — luật, chỉ tiêu, dữ liệu (thứ tự vai, như trước) rồi các file còn lại mà
    khối khai là file (`runner.input_files`; đường RAW: file danh sách tuyến). Dùng cho /api/meta, `data_version` và bản tính.

    12/11: trước đó chỉ 3 file đầu ⇒ đổi file tuyến thì số đổi mà bản tính "đã soát" cũ vẫn được dùng lại, `data_version` đứng yên."""
    paths = list(_workflow_files(workflow))
    paths += [path for path in runner.input_files(workflow).values() if path not in paths]
    # File không đọc được LÚC ĐANG CHẠY (bị xoá / dời sau khi bật) ⇒ ghi "không có file" như `runner.input_fingerprint`, không ném lỗi: độc
    # lập 12/11 đổi tên file tuyến khi backend đang bật ⇒ /api/runs/{id}/verify trả HTTP 500. Chuỗi này khác mọi sha ⇒ bên so thấy "dữ liệu đã đổi".
    return {path.name: _sha256_or_missing(path) for path in paths}


def _sha256_or_missing(path: Path) -> str:
    try:
        return _sha256(path)
    except OSError:      # không có file, là thư mục, không có quyền đọc… — mọi kiểu "không đọc được" coi như nhau, không chép chuỗi lỗi hệ điều hành
        return "không có file"


def _stopped_reason(result) -> str:
    """Lời cho BÊN NGOÀI khi một lượt chạy lại dừng: chỉ nêu khối dừng. KHÔNG chép lời dừng của bộ chạy — lời đó có thể là chuỗi lỗi của hệ điều
    hành kèm đường dẫn tuyệt đối trên máy ("FileNotFoundError: [Errno 2] … '/tmp/…'", 12/11); chi tiết nằm ở bước của lượt trên máy.
    """
    stopped = [step["node"] for step in result.steps if step.get("status") == "stopped"]
    return "workflow không chạy xong" + (f" ở khối {', '.join(stopped)}" if stopped else "") + " (chi tiết lỗi chỉ xem trên máy chạy)"


def _data_files(files_sha256: dict[str, str], rule_file: Path) -> dict[str, str]:
    """Phần DỮ LIỆU của bản tính = mọi file đầu vào trừ file luật (file luật lưu riêng cả nội dung ở bảng `rule_version`)."""
    return {name: sha for name, sha in files_sha256.items() if name != rule_file.name}


def _code_fingerprint(workflow: dict) -> tuple[str, list[str]]:
    """Dấu vân tay code của bản tính = file code quyết định số + MÃ BĂM LOGIC của workflow (bỏ `position`).

    Vì sao băm logic, không băm chữ file: kéo khối trên giao diện (bước 2) không được đẻ bản tính mới / làm mất "Đã soát" khi không số nào đổi.
    Đổi một ô / dây ⇒ mã băm khác ⇒ bản tính mới thay bản cũ (W5).
    """
    files_sha, names = store.code_fingerprint(CODE_FILES, REPO_ROOT)
    logic = runner.workflow_sha256(workflow)
    return hashlib.sha256(f"{files_sha}\0{logic}".encode()).hexdigest(), names + [f"workflow:{workflow['workflow']}@{logic[:12]}"]


def _approved_gates(store_path: Path, workflow, cutoff: date) -> frozenset:
    """Cổng người duyệt nào được mở cho lượt (workflow, ngày chốt) này: bản tính cùng ĐÚNG dấu vân tay đầu vào đã soát, chưa bị thay (bước 3).

    Tra TRƯỚC khi chạy được vì dấu vân tay chỉ gồm đầu vào (workflow logic + file + code + ngày chốt), không gồm lời duyệt. File sai ⇒ không cổng nào
    (bộ chạy sẽ từ chối). Mặc định ĐÓNG."""
    if not isinstance(workflow, dict) or runner.check_workflow(workflow):
        return frozenset()
    gates = runner.gate_ids(workflow)
    if not gates:
        return frozenset()
    inputs_sha = runner.input_fingerprint(workflow, {"cutoff": cutoff})["sha256"]
    with store.connect(store_path) as conn:
        return gates if store.reviewed_for_inputs(conn, inputs_sha) else frozenset()


def _shown_path(path) -> str:
    """Đường file để ĐƯA VÀO LỜI TRẢ / lời lỗi: tương đối so với gốc repo; file ngoài repo ⇒ chỉ tên file. Không bao giờ in đường dẫn tuyệt đối của máy."""
    path = Path(os.path.abspath(path))   # abspath, KHÔNG resolve: thư mục dữ liệu có thể là liên kết (hộp gắn file, worktree) — vẫn phải ra đường tương đối
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return path.name


def _base_mask_map() -> dict[str, str]:
    return json.loads(MASK.read_text(encoding="utf-8"))["map"]


def _auto_mask_map(app) -> dict[str, str]:
    upload_store = getattr(app.state, "upload_store", None)
    if upload_store is None or not upload_store.exists():
        return {}
    with store.connect(upload_store) as conn:
        return store.auto_employee_masks(conn)


def _all_known_mask_map(app) -> dict[str, str]:
    return {**_base_mask_map(), **_auto_mask_map(app)}


def _mask_path_for_workflow(app, workflow: dict, extra_auto: dict[str, str] | None = None) -> str:
    """File bảng mã che (gốc + tự cấp cho đúng đội trong file chỉ tiêu của workflow) truyền vào `queries.build_context` để không sửa `queries.py`."""
    base_map = _base_mask_map()
    auto_map = {**_auto_mask_map(app), **(extra_auto or {})}
    if not auto_map:
        return str(MASK)
    from kpi_agent.core.load_data import load_targets
    targets_path = runner.repo_path(runner.node_param(workflow, "read_targets", "path"))
    target_codes = {str(code) for code in load_targets(targets_path)["employees"]}
    used_auto = {masked: real for masked, real in auto_map.items() if real in target_codes}
    if not used_auto:
        return str(MASK)
    merged = {**base_map, **used_auto}
    payload = json.dumps({"map": merged}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    mask_dir = app.state.upload_dir / "_ma_che"
    mask_dir.mkdir(parents=True, exist_ok=True)
    out = mask_dir / f"{hashlib.sha256(payload).hexdigest()[:16]}.json"
    if not out.exists():
        out.write_bytes(payload)
    return str(out)


def _attach_auto_employee_notes(ctx, base_map: dict[str, str]) -> None:
    auto_codes = sorted(masked for masked in ctx.to_real if masked not in base_map)
    if auto_codes:
        _attach_note(
            ctx,
            "warning",
            f"đội có {len(auto_codes)} nhân viên mới được tự cấp mã che ({', '.join(auto_codes)}) — kiểm lại họ tên trong file chỉ tiêu",
        )


def _start_period(app, workflow: dict, workflow_text: str, cutoff: date, uploads_only: bool = False) -> None:
    """Chạy workflow + ghi bản tính + đặt MỌI trạng thái của "bản đang dùng" lên `app.state`. Tách khỏi `lifespan` (20/11/2026) để hai nơi dùng
    ĐÚNG một đoạn: lúc bật thường (file trong workflow) và lúc một KỲ TRỐNG vừa nhận đủ ba file tải lên (`_start_from_pending`) — không có bản
    dựng thứ hai. `uploads_only`: bản dựng hoàn toàn từ file tải lên ⇒ các khoản phụ không đọc file gắn sẵn của backend. Lỗi ⇒ RuntimeError."""
    app.state.workflow = workflow
    app.state.uploads_only = uploads_only
    if not uploads_only:
        app.state.uploaded_at = None
        app.state.pending_uploaded_at = {}
        app.state.route_summary = None
        app.state.targets_override = None
    app.state.upload_store = app.state.store.with_name(app.state.store.stem + "_uploads.sqlite")
    # Thư mục file tải lên: KPI_UPLOAD_DIR (đọc lúc bật như KPI_STORE — hộp Capitol chỉ ghi được /srv/kpi_store); thiếu ⇒ như cũ
    app.state.upload_dir = Path(os.getenv("KPI_UPLOAD_DIR") or str(UPLOAD_DIR))
    for cache in ("extra_bonus", "tracking", "spice_customers", "spice", "outlets", "membership", "order_lines"):
        setattr(app.state, cache, None)
    logic_sha = runner.workflow_sha256(workflow) if not runner.check_workflow(workflow) else None
    base_map = _base_mask_map()
    try:
        ctx = queries.build_context(cutoff, workflow, mask_path=_mask_path_for_workflow(app, workflow),
                                    approved_gates=_approved_gates(app.state.store, workflow, cutoff))
    except queries.WorkflowStopped as stop:
        if stop.result.status == "stopped":
            # Lượt dừng vẫn ghi lại (bước nào dừng, lời đã che mã, dấu vân tay) — "rejected" (file sai, 0 khối chạy) thì không có gì để ghi
            with store.connect(app.state.store) as conn:
                version = store.record_workflow_version(conn, workflow["workflow"], workflow_text, logic_sha)
                store.record_workflow_run(conn, workflow_version_id=version, cutoff=cutoff.isoformat(), status="stopped",
                                          steps=stop.result.steps, errors=stop.result.errors, inputs=stop.result.fingerprint, bonus_run_id=None)
        # 03/11: dữ liệu lỗi nặng ⇒ DỪNG HẲN, không hiện số — backend không bật (lời đã che mã)
        raise RuntimeError(f"backend KHÔNG bật — {stop}") from None
    # File chỉ tiêu của workflow thiếu người có mã che gốc (20/11/2026): kiểm theo đội gốc để tải lại file 14 người sau khi cấp NV15 vẫn hợp lệ.
    missing = upload.missing_team_members(set(ctx.bonuses), {str(code) for code in base_map.values()})
    if missing:
        raise RuntimeError(f"backend KHÔNG bật — {missing}")
    app.state.ctx = ctx
    # Bậc "tên vào trợ lý": cờ KPI_NAMES — off (mặc định) ⇒ {} ⇒ mọi đường như cũ; on ⇒ bảng tên cho cửa vào / ra của công cụ.
    # Bảng tên thiếu / lệch đội khi cờ on ⇒ không bật (đóng khi lỗi — không chạy nửa tên nửa mã)
    app.state.name_entries = {}
    if agent_names.names_enabled():
        app.state.name_entries = _name_entries(app)
        if isinstance(app.state.name_entries, str):
            raise RuntimeError(f"backend KHÔNG bật — KPI_NAMES=on nhưng {app.state.name_entries}")
    rule_file = _workflow_files(workflow)[0]
    # sha để biết số trên trang tính từ đúng bản luật + dữ liệu nào (SPEC mục 4 — bậc 1 chưa có bản luật có phiên bản)
    app.state.files_sha256 = _files_sha256(workflow)
    # Bậc 2a: mỗi lần tính = một bản không sửa được; cùng (luật, dữ liệu, code + workflow) với bản mới nhất ⇒ dùng lại bản đó
    code_sha, code_files = _code_fingerprint(workflow)
    # Bản dữ liệu (upload 10/11): bản lúc bật = bản mặc định; file tải lên ⇒ bản khác, giữ trong bộ nhớ + file lưu RIÊNG (_for_version)
    app.state.extra_files_sha256 = _extra_files_sha256(workflow, ctx.period)
    app.state.data_version = _data_version(app.state.files_sha256, code_sha, ctx.cutoff, app.state.extra_files_sha256)
    app.state.versions = OrderedDict()
    app.state.restoring = None                                   # dòng `uploaded_version` đang được nạp lại (chỉ khác None trong lúc bật — 2j)
    app.state.restore_report = {"enabled": _restore_uploads_flag(os.getenv("KPI_RESTORE_UPLOADS")), "restored": 0, "changed": [], "skipped": []}
    # Bản dữ liệu của lần TẢI LÊN gần nhất (12/11): /api/meta chỉ nói về bản lúc bật ⇒ sau tải lên không trường nào của nó đổi ⇒ bên theo dõi
    # "vân tay" không biết có dữ liệu mới. Chưa tải lần nào ⇒ bằng bản lúc bật.
    app.state.latest_data_version = app.state.data_version
    with store.connect(app.state.store) as conn:
        app.state.run_id = store.record_run(
            conn, period=ctx.period, cutoff=ctx.cutoff.isoformat(), rule_file=rule_file,
            data_files=_data_files(app.state.files_sha256, rule_file),
            refreshed_at=ctx.snapshot_at.isoformat() if ctx.snapshot_at else None,
            code_sha=code_sha, code_files=code_files, lines=queries.bonus_lines(ctx))
        # S3: lượt workflow ghi SAU bản tính (bước sau chỉ khi bước trước báo thành công — chủ đề 17 mục 2.2b), mang số bản tính đó
        version = app.state.workflow_version_id = store.record_workflow_version(conn, workflow["workflow"], workflow_text, logic_sha)
        # Lượt dừng ở cổng người duyệt ("awaiting_review") vẫn ghi "ok": bản tính ĐÃ có (đúng CHECK ok ⇔ có bản tính). "Chờ duyệt" nằm trong `steps`
        # (bước cổng `awaiting_review`) — 10/11: không đổi CHECK / không dựng lại bảng trong file SQLite của User.
        app.state.workflow_run_id = store.record_workflow_run(
            conn, workflow_version_id=version, cutoff=ctx.cutoff.isoformat(), status="ok", steps=ctx.workflow_result.steps,
            errors=[], inputs=ctx.workflow_result.fingerprint, bonus_run_id=app.state.run_id)
        app.state.workflow_version = store.workflow_run_summary(conn, app.state.workflow_run_id)["workflow_version"]
        _use_status(ctx, store.run_summary(conn, app.state.run_id))
    _attach_auto_employee_notes(ctx, base_map)
    # Thưởng thêm tính SẴN lúc bật (bậc (b) bản 2,): tính ở lần gọi đầu mất ≈ 15 s (đọc lại RAW) > hạn 10 s mỗi công cụ của Capitol ⇒ AI nhận
    # lỗi hết giờ, người hỏi mất câu. Đường Tracking ⇒ lời từ chối ngay (không đọc file). Lỗi luật / dữ liệu thưởng thêm KHÔNG chặn bật (8 KPI vẫn chạy)
    _extra_bonus(app)
    # 1 % gia vị cũng tính SẴN lúc bật (13/11): công cụ 12 `get_spice_loyalty` gọi lần đầu mất ≈ 15 s (đọc lại RAW) > hạn chờ 10 s của trợ lý ⇒ câu gia
    # vị ĐẦU TIÊN sau mỗi lần bật trả "công cụ dữ liệu không phản hồi" (đo thật 13/11). Lỗi luật / dữ liệu gia vị ⇒ lời từ chối
    # được giữ, KHÔNG chặn bật. Cái giá: bật chậm thêm đúng một lượt đọc RAW (đo 13/11 trên máy đang bận: 37 s ⇒ 72 s) ⇒ CHỈ làm khi đặt
    # KPI_WARM_SPICE=on (script bật bộ của người dùng + hộp Capitol đặt); mặc định tắt để mọi test bật backend không chậm thêm ⇒ khi tắt, lần gọi đầu
    # vẫn tính tại chỗ như trước.
    if os.getenv("KPI_WARM_SPICE") == "on":
        _spice(app)
    app.state.period_ready = True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.system_only = _system_only_flag(os.getenv("KPI_SYSTEM_ONLY"))   # đọc MỘT chỗ, trước mọi việc nặng ⇒ giá trị lạ dừng ngay
    # Mã phiên: MẶC ĐỊNH ĐÓNG — không có mã thì dừng ngay ở đây, trước mọi việc nặng (kpi_agent/session_key.py)
    app.state.session_key = session_key.load_server_key(app.state.system_only)
    names_on = agent_names.names_enabled()   # KPI_NAMES cũng đọc TRƯỚC khi nạp dữ liệu: giá trị khác on / off ⇒ dừng ngay
    if not MASK.is_file():
        # 12/11: thiếu bảng mã che từng ra FileNotFoundError trần kèm đường dẫn tuyệt đối — nay lời rõ, chỉ tên file tương đối
        raise RuntimeError(f"backend KHÔNG bật: thiếu {_shown_path(MASK)}")
    # File workflow hỏng ⇒ WorkflowFileError ⇒ backend không bật (không có số nào)
    workflow, workflow_text = runner.load_workflow(WORKFLOW)
    app.state.workflow = workflow
    app.state.extra_bonus = None          # thưởng thêm tính ở lần gọi đầu (/api/system/extra-bonus) — bật lại thì tính lại
    app.state.tracking = None             # tab Theo dõi (bản 2) — cùng lý do: bật lại (đổi ngày chốt / workflow / file trang) thì tính lại
    app.state.spice_customers = None      # từng khách 1 % gia vị cho trang (13/11) — điền cùng lúc với app.state.spice
    app.state.spice = None                # 1 % gia vị (bậc 1, 11/11) — tính ở lần gọi đầu, bật lại thì tính lại
    app.state.outlets = None              # điểm bán thuộc tuyến + Trà 365 (12/11) — tính ở lần gọi đầu, bật lại thì tính lại
    app.state.membership = None           # khách hội viên phần doanh số (13/11) — đọc sổ + file đơn hàng ở lần gọi đầu, bật lại thì đọc lại
    app.state.order_lines = None          # dòng đơn hàng kèm lý do loại (12/11) — đọc ở lần gọi đầu, bật lại thì đọc lại
    app.state.route_summary = None
    app.state.targets_override = None
    app.state.run_calls = OrderedDict()   # nhật ký lần gọi công cụ theo (tổ chức, mã lượt) — CHỈ trong bộ nhớ (xem _record_call)
    app.state.store = Path(os.getenv("KPI_STORE", str(DEFAULT_STORE)))
    app.state.workflow_template, app.state.workflow_text = workflow, workflow_text
    app.state.period_ready = False
    empty_period = os.getenv("KPI_EMPTY_PERIOD")
    if empty_period:
        _start_empty(app, empty_period)          # kỳ trống: chưa có file dữ liệu nào — chờ người dùng tải lên (xem khối "KỲ TRỐNG")
        _start_from_pending(app, at_startup=True)
    else:
        _start_period(app, workflow, workflow_text, CUTOFF)
    if app.state.period_ready:
        _restore_uploaded_versions(app)
    yield


RESTORE_SHA = re.compile(r"[0-9a-f]{64}")   # tên file trong thư mục tải lên = sha256 nội dung, viết thường


def _restore_uploads_flag(value: str | None) -> bool:
    """KPI_RESTORE_UPLOADS: không đặt hoặc "1" = nạp lại bản đã tải lúc bật (mặc định); "0" = không nạp (hộp có hạn chờ bật ngắn — đo 20/11: mỗi bản
    nạp lại làm bật chậm thêm ≈ 15 giây). Giá trị khác ⇒ dừng lúc bật, như KPI_SYSTEM_ONLY: gõ nhầm mà lặng lẽ coi là bật / tắt thì không ai biết."""
    if value is None or value == "1":
        return True
    if value == "0":
        return False
    raise RuntimeError("backend KHÔNG bật — KPI_RESTORE_UPLOADS chỉ nhận 1 / không đặt biến (nạp lại bản đã tải) hoặc 0 (không nạp)")


def _restore_uploaded_versions(app) -> None:
    """Lúc bật: dựng lại các bản dữ liệu đã tải lên ở những lần chạy trước (2j, 20/11/2026) — trước đó backend bật lại là `?data_version=` cũ ra 404
    dù file và bản tính vẫn còn trên đĩa.

    Mỗi dòng `uploaded_version` (tối đa MAX_VERSIONS dòng mới nhất, theo thứ tự tải) ⇒ đọc file `<mã băm>.xlsx` trong thư mục tải lên, băm lại, rồi
    gọi ĐÚNG hàm của cửa tải lên (`_build_version`) — không có đường dựng thứ hai: mọi kiểm khuôn / luật kỳ / đối soát chạy lại, mã bản tính lại từ
    nội dung (code, luật hay file phụ đã đổi sau lúc tải ⇒ ra mã MỚI, mã cũ không còn hỏi được — đúng, vì số có thể đã khác).
    Dòng hỏng — KỂ CẢ lỗi lạ — ⇒ BỎ QUA dòng đó, ghi {file, code, reason} vào `restore_report`; không bao giờ chặn bật: bản lúc bật không phụ
    thuộc bản tải lên, và bảng là chỉ-thêm nên một dòng hỏng nằm đó mãi (vd bản tải bằng loại file của bản code MỚI rồi lùi về code cũ). Chỉ lỗi
    của chính kho / bảng (không mở / không đọc được) mới nổi lên. Mã: dong_sai_dang · file_mat · file_bi_doi · nen_khong_nap · bi_tu_choi ·
    loi_la (lời cố định, dấu vết đầy đủ ghi ra log máy chủ). Cái giá: bật chậm thêm một lượt đọc + tính cho mỗi bản."""
    report = app.state.restore_report
    if not report["enabled"] or not app.state.upload_store.exists():
        return                                                   # chưa từng tải lên ⇒ không tạo kho rỗng
    with store.connect(app.state.upload_store) as conn:
        rows = store.uploaded_versions(conn, MAX_VERSIONS)
    restored = {}                                                # mã bản LÚC TẢI ⇒ state vừa dựng lại (bản chồng tìm nền của nó ở đây)
    for row in rows:
        sha = str(row["file_sha256"])
        well_formed = RESTORE_SHA.fullmatch(sha) is not None
        # `file`: 8 ký tự đầu mã băm; dòng sai dạng thì KHÔNG lặp chữ của dòng (có thể là "../…") — ghi số thứ tự dòng trong bảng
        label = sha[:8] if well_formed else f"dòng {row['id']}"
        def skip(code: str, reason: str) -> None:
            report["skipped"].append({"file": label, "code": code, "reason": reason})
        # Kiểm dạng TRƯỚC khi ghép đường dẫn / đọc file: mã băm phải đúng 64 ký tự hex (dòng trỏ "../ngoai" từng khiến code đọc file ngoài thư mục
        # tải lên rồi mới so băm — 20/11), loại file phải là loại bản code NÀY biết, kỳ + ngày chốt đúng dạng.
        try:
            cutoff = date.fromisoformat(row["wanted_cutoff"]) if row["wanted_cutoff"] else None
        except (TypeError, ValueError):
            well_formed = False
        if not well_formed or row["kind"] not in upload.KINDS or not period_status.valid_period(str(row["period"])):
            skip("dong_sai_dang", "dòng ghi bản đã tải không đúng dạng bản code này đọc được (loại file, kỳ, ngày chốt hoặc mã băm) — tải lại file")
            continue
        # bản có chỉ tiêu từ trang: nguồn nạp lại là file chỉ tiêu đã tách <mã băm>.json (trang không được lưu); các loại khác: file .xlsx đã nhận
        source = (app.state.upload_dir / PAGE_TARGETS_DIR / f"{sha}.json" if row["kind"] == upload.PAGE_TARGETS
                  else app.state.upload_dir / f"{sha}.xlsx")
        try:
            body = source.read_bytes()
        except OSError:
            skip("file_mat", "file đã tải không còn trong thư mục tải lên — tải lại file")
            continue
        if hashlib.sha256(body).hexdigest() != sha:
            skip("file_bi_doi", "file trong thư mục tải lên đã bị đổi sau khi nhận (mã băm không khớp) — tải lại file gốc")
            continue
        base = app.state if row["base_data_version"] is None else restored.get(row["base_data_version"])
        if base is None:
            skip("nen_khong_nap", "bản làm nền của file này không nạp lại được — tải lại bản nền trước")
            continue
        app.state.restoring = row
        try:
            summary = _build_version(app, base, row["kind"], row["period"], body, cutoff)
        except upload.UploadRefused as refusal:
            skip("bi_tu_choi", "kiểm lại lúc bật không qua: " + refusal.reason)
            continue
        except Exception:   # noqa: BLE001 — cố ý bắt MỌI lỗi của MỘT dòng: hệ thống không bật được nặng hơn mất một bản (+ 20/11)
            logging.getLogger("kpi_agent.backend").exception("nạp lại bản đã tải: dòng %s của uploaded_version gặp lỗi lạ — bỏ qua dòng này", row["id"])
            skip("loi_la", "lỗi chưa biết khi dựng lại bản này — cần người phát triển xem log máy chủ; tải lại file để dùng tiếp")
            continue
        finally:
            app.state.restoring = None
        version = summary["data_version"]
        restored[row["data_version"]] = app.state if version == app.state.data_version else app.state.versions[version]
        report["restored"] += 1
        if version != row["data_version"]:
            report["changed"].append({"file": row["file_sha256"][:8], "reason": "bản nạp lại mang mã MỚI — code, luật hoặc file phụ đã đổi sau "
                                      "lúc tải; mã bản cũ không còn dùng được"})

def _use_status(ctx: queries.KpiContext, summary: dict) -> None:
    # Nhãn của bản đang dùng vào MỌI kết quả công cụ (bản không tên — queries._envelope đọc ctx) + /api/meta (cả hai bản)
    ctx.status, ctx.status_label, ctx.status_label_full = summary["status"], summary["status_label_for_model"], summary["status_label"]


_docs = os.getenv("KPI_API_DOCS") == "1"   # /docs, /openapi.json tắt mặc định (C rà bảo mật 03/11) — bật khi cần xem tay
app = FastAPI(title="Trợ lý KPI — backend chỉ đọc", lifespan=lifespan,
              docs_url="/docs" if _docs else None, redoc_url=None, openapi_url="/openapi.json" if _docs else None)
# Cờ KPI_SYSTEM_ONLY — hộp chạy trong bộ Capitol chỉ mở đường cho HỆ THỐNG, đóng mọi đường của trang trên máy ----------
# Vì sao: trong hộp Capitol không ai dùng trang trên máy, nhưng các đường của trang (bảng đội, "Đã soát" /review, tên NV /api/names …) không có khoá
# ⇒ hộp nào cùng mạng cũng gọi được. Bật cờ ⇒ chỉ còn các đường dưới; đường khác trả 404 y như không tồn tại. Tắt (mặc định) ⇒ không đổi gì.
SYSTEM_ONLY_PREFIXES = ("/api/system/", "/api/tool/", "/api/export/")   # /api/export/* khi cờ bật luôn đòi khoá (xem export_bonus_table)
SYSTEM_ONLY_PATHS = {"/api/tools", "/api/meta"}


def _system_only_flag(value: str | None) -> bool:
    """"1" = bật; không đặt biến hoặc "0" = tắt. Giá trị khác (kể cả để rỗng, "true", "on") ⇒ dừng lúc bật: gõ nhầm / file compose điền thiếu mà
    lặng lẽ coi là tắt thì hộp mở hết đường mà không ai biết."""
    if value is None or value == "0":
        return False
    if value == "1":
        return True
    raise RuntimeError("backend KHÔNG bật — KPI_SYSTEM_ONLY chỉ nhận 1 (bật) hoặc 0 / không đặt biến (tắt)")

@app.middleware("http")
async def system_only_gate(request: Request, call_next):
    # scope["path"] = đúng chuỗi bộ định tuyến dùng để khớp đường ⇒ không có cách viết đường nào qua cổng này mà tới được đường bị đóng
    path = request.scope["path"]
    if request.app.state.system_only and path not in SYSTEM_ONLY_PATHS and not path.startswith(SYSTEM_ONLY_PREFIXES):
        return JSONResponse({"detail": "Not Found"}, status_code=404)   # cùng thân với 404 của bộ định tuyến — không lộ đường nào có thật
    return await call_next(request)
# ---------- KỲ TRỐNG (20/11/2026): backend bật khi CHƯA có file dữ liệu nào của kỳ; người dùng tải đơn hàng + chỉ tiêu + sổ theo dõi ⇒ bản đầu ----------
# Vì sao: trước đó backend chỉ bật được khi đã gắn sẵn bộ file của kỳ trong workflow ⇒ tải đúng các file đó lên chỉ nhận "đã có sẵn" — không
# thể nói kỳ được dựng "chỉ bằng tải lên" (phép thử cuối tháng 10 lượt 1 của). Bật với KPI_EMPTY_PERIOD=<kỳ>: workflow chỉ còn là KHUÔN
# (khối, dây, file luật của kỳ); không đặt biến ⇒ mọi thứ như cũ.
EMPTY_OK_PATHS = {"/api/system/upload", "/api/system/ky", "/api/meta", "/api/tools",
                  # màn ghép cột: file lệch mẫu phải ghép được NGAY ở kỳ trống — không thì kẹt từ bước đầu (cách ghép lưu ở kho chính, không cần bản)
                  "/api/system/bo-cuc", "/api/system/ghep-thu", "/api/system/ghep-xac-nhan"}   # meta + tools: thân tối thiểu, không số (xem hai hàm đó)
EMPTY_OK_PREFIXES = ("/api/system/ky/", "/api/system/khuon/")
FIRST_SET = ("raw", "chi_tieu", upload.ROUTE_BOOK)          # ba file bắt buộc của bản ĐẦU: thiếu tuyến thì lõi dừng (luật lọc theo tuyến)
KIND_LABEL = {"raw": "file đơn hàng", "chi_tieu": "file chỉ tiêu", upload.ROUTE_BOOK: "sổ theo dõi (để lấy tuyến)"}
EMPTY_PERIOD_CODE = "ky_chua_co_du_lieu"


def _empty_period_message(app) -> str:
    missing = _pending_state(app)["con_thieu"]
    return (f"Kỳ {app.state.empty_period} chưa có dữ liệu — hãy tải " + ", ".join(KIND_LABEL[kind] for kind in missing)
            + " ở trang Dữ liệu; đủ ba file thì hệ thống tự tính")


@app.middleware("http")
async def empty_period_gate(request: Request, call_next):
    """Kỳ chưa có bản nào ⇒ mọi đường cần số trả 409 có lời + mã — không 500, không số. Chỉ cửa tải lên và hai đường "kỳ này có gì" đi tiếp."""
    path = request.scope["path"]
    if not getattr(request.app.state, "period_ready", True) and path.startswith("/api/") and path not in EMPTY_OK_PATHS \
            and not path.startswith(EMPTY_OK_PREFIXES):
        return _refused_json(_empty_period_message(request.app), 409, EMPTY_PERIOD_CODE)
    return await call_next(request)


def _mask_for_text(app) -> dict:
    """Bảng CHE chữ (mã thật ⇒ mã che) cho lời lỗi của màn ghép: có bản ⇒ bảng của bản đang dùng; kỳ trống ⇒ bảng che gốc + tự cấp."""
    to_real = app.state.ctx.to_real if app.state.ctx is not None else _all_known_mask_map(app)
    return {real: masked for masked, real in to_real.items()}


def _team_for_route_trial(app) -> set[str]:
    """"Đội" để chạy thử công thức đọc bảng tuyến: có bản ⇒ đội của bản đang dùng; kỳ trống ⇒ mã của file chỉ tiêu đang nằm trong bộ chờ."""
    if app.state.ctx is not None:
        return set(app.state.ctx.to_real.values())
    from kpi_agent.core.load_data import load_targets
    targets = _pending_state(app)["have"].get("chi_tieu")
    if targets is None:
        raise upload.UploadRefused(f"Kỳ {app.state.empty_period} chưa có file chỉ tiêu — hãy tải file chỉ tiêu trước rồi mới ghép thử sổ theo dõi "
                                   "(cần biết đội gồm những ai để đọc bảng tuyến)", 409, "chua_co_chi_tieu")
    return {str(code) for code in load_targets(targets["path"])["employees"]}


def _state_for_route_trial(app):
    """Thứ phép đối chiếu đơn hàng ⇄ tuyến cần (`_orders_vs_routes`: workflow có đường file đơn hàng + kỳ, ngày chốt, luật). Có bản ⇒ chính
    `app.state`. Kỳ trống ⇒ dựng từ file đơn hàng đang ở bộ chờ; chưa có ⇒ 409 — KHÔNG bỏ phép đối chiếu: không có nó thì công thức đọc tuyến
    ghép nhầm cột vẫn được xác nhận rồi lưu dùng lại (lỗ đã bắt ở nhánh tải sổ).
    """
    if app.state.ctx is not None:
        return app.state
    from types import SimpleNamespace
    from kpi_agent.core.rules import load_rules
    raw = _pending_state(app)["have"].get("raw")
    if raw is None:
        raise upload.UploadRefused(f"Kỳ {app.state.empty_period} chưa có file đơn hàng — hãy tải file đơn hàng trước rồi mới ghép thử sổ theo dõi "
                                   "(bảng tuyến đọc ra phải được đối chiếu với đơn hàng)", 409, "chua_co_don_hang_cua_ky")
    workflow, period = copy.deepcopy(app.state.workflow_template), app.state.empty_period
    node = next(node for node in workflow["nodes"] if node["type"] == "read_raw").setdefault("params", {})
    node["path"] = str(raw["path"])
    raw_mapping, _sha = _saved_mapping_for(app, "raw", raw["path"])
    if raw_mapping is not None:
        node["ghep"] = raw_mapping
    rules = load_rules(runner.repo_path(_rules_file_for_period(workflow, period)))
    cutoff = date.fromisoformat(raw["wanted_cutoff"]) if raw["wanted_cutoff"] else upload.last_day(period)
    return SimpleNamespace(workflow=workflow, ctx=SimpleNamespace(cutoff=cutoff, period=period, rules=rules))


def _start_empty(app, period: str) -> None:
    if not period_status.valid_period(period):
        raise RuntimeError("backend KHÔNG bật — KPI_EMPTY_PERIOD phải có dạng YYYY-MM (vd 2026-10)")
    try:
        _rules_file_for_period(app.state.workflow_template, period)
    except upload.UploadRefused as refusal:
        raise RuntimeError(f"backend KHÔNG bật — KPI_EMPTY_PERIOD={period}: {refusal.reason}") from None
    app.state.empty_period, app.state.ctx, app.state.workflow = period, None, app.state.workflow_template
    app.state.files_sha256, app.state.data_version, app.state.latest_data_version, app.state.name_entries = {}, None, None, {}
    app.state.versions, app.state.restoring = OrderedDict(), None
    app.state.uploaded_at, app.state.pending_uploaded_at = None, {}
    app.state.restore_report = {"enabled": _restore_uploads_flag(os.getenv("KPI_RESTORE_UPLOADS")), "restored": 0, "changed": [], "skipped": []}
    app.state.upload_store = app.state.store.with_name(app.state.store.stem + "_uploads.sqlite")
    app.state.upload_dir = Path(os.getenv("KPI_UPLOAD_DIR") or str(UPLOAD_DIR))
    app.state.pending_problem = None


def _pending_state(app) -> dict:
    """Bộ chờ của kỳ trống: mỗi loại file lấy lần tải MỚI NHẤT còn nguyên trong thư mục tải lên (mất / bị đổi ⇒ coi như chưa có)."""
    rows = {}
    if app.state.upload_store.exists():
        with store.connect(app.state.upload_store) as conn:
            rows = store.pending_uploads(conn, app.state.empty_period)
    have = {}
    for kind, row in rows.items():
        path = app.state.upload_dir / f"{row['file_sha256']}.xlsx"
        if RESTORE_SHA.fullmatch(str(row["file_sha256"])) and path.is_file() and _sha256(path) == row["file_sha256"]:
            have[kind] = {**row, "path": path}
    return {"have": have, "da_co": [kind for kind in FIRST_SET if kind in have], "con_thieu": [kind for kind in FIRST_SET if kind not in have]}


def _empty_status(app, period: str | None = None) -> dict:
    state = _pending_state(app)
    period, have = app.state.empty_period, state["have"]

    def item(key: str, kind: str, what: str, how: str) -> dict:
        row = have.get(kind)
        if row is None:
            return {**period_status._item(key, "thieu", f"chưa có {what} của kỳ {period}"), "next_step": how}
        return period_status._item(key, "co", f"{what} đã tải lên, đang chờ đủ bộ để tính", checked=True, sha256_8=row["file_sha256"][:8],
                                   from_upload=True, uploaded_at=row["created_at"], **({"cutoff": row["wanted_cutoff"]} if kind == "raw" else {}))
    # CÙNG hình `items` với kỳ đã có bản (`period_status.period_status`) — bảng "tháng này đã có gì" của trang đọc danh sách này
    items = [item("raw", "raw", "file đơn hàng", "tải file đơn hàng của kỳ này lên (loại raw)"),
             item("chi_tieu", "chi_tieu", "file chỉ tiêu", "tải file chỉ tiêu của kỳ này lên (loại chi_tieu)"),
             item("tuyen", upload.ROUTE_BOOK, "sổ theo dõi (để lấy tuyến)", "tải sổ theo dõi của kỳ này lên (loại so_tracking)"),
             period_status._main_rules_item(app.state, None, period, _shown_path, _rules_file_for_period, _sha256_or_missing),
             period_status._extra_rules_item(app.state, period, _extra_files_sha256)]
    body = {"period": period, "base_period": period, "data_source": "raw", "ready": False, "uploads_survive_restart": app.state.restore_report["enabled"],
            "missing": [entry["key"] for entry in items if entry["state"] != "co"], "items": items, "latest_data_version": None,
            "trang_thai": "cho_du_file", "da_co": state["da_co"], "con_thieu": state["con_thieu"],
            "loi": f"đã nhận {', '.join(KIND_LABEL[kind] for kind in state['da_co']) or 'chưa file nào'}; còn thiếu "
                   f"{', '.join(KIND_LABEL[kind] for kind in state['con_thieu'])} để tính"}
    if app.state.pending_problem:
        body["ly_do_chua_tinh"] = app.state.pending_problem
    return body


def _receive_for_empty_period(app, kind: str, period: str, body: bytes, wanted_cutoff: date | None) -> dict:
    """Cửa tải lên khi kỳ CHƯA có bản: kiểm file như mọi lần tải (gói, khuôn, kỳ, mã che) ⇒ cất vào bộ chờ ⇒ đủ ba file thì dựng bản đầu."""
    if period != app.state.empty_period:
        raise upload.UploadRefused(f"backend đang mở kỳ {app.state.empty_period} và kỳ đó chưa có dữ liệu — chưa nhận file của kỳ khác", 409, "khac_ky_dang_mo")
    if kind not in FIRST_SET:
        raise upload.UploadRefused(_empty_period_message(app), 409, EMPTY_PERIOD_CODE)
    upload.check_package(body)
    sha, folder = hashlib.sha256(body).hexdigest(), app.state.upload_dir
    try:
        temp = upload.write_temp(body, folder)
    except OSError:
        raise upload.UploadRefused("thư mục lưu file tải lên không ghi được (kiểm KPI_UPLOAD_DIR) — chưa nhận file", 503) from None
    new_employees: dict[str, str] = {}
    base_map = _base_mask_map()
    try:
        if kind != upload.ROUTE_BOOK:     # sổ được đọc + đối chiếu với đơn hàng lúc đủ ba file (cần đội của file chỉ tiêu và file đơn hàng)
            rules_path = runner.repo_path(_rules_file_for_period(app.state.workflow_template, period))
            assumptions = (yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}).get("assumptions") or {}
            base_known = {str(real) for real in base_map.values()}
            known = base_known | {str(real) for real in _auto_mask_map(app).values()}
            # file đơn hàng lệch mẫu: dùng cách ghép người đã xác nhận cho đúng bố cục này (cùng hàm với đường tải thường); chưa ghép ⇒ chỉ đường ghép
            mapping, mapping_sha = _saved_mapping_for(app, kind, temp)
            try:
                upload.read_and_check(
                    kind, temp, period, known, upload.last_day(period), wanted_cutoff, mapping, assumptions,
                    base_known_codes=base_known, new_employees_out=new_employees if kind == "chi_tieu" else None,
                )
            except upload.UploadRefused as refusal:
                if mapping_sha == "new_layout" and refusal.reason.startswith("file raw thiếu"):
                    raise upload.UploadRefused(refusal.reason + " — file bố cục khác mẫu: ghép cột trước (POST /api/system/bo-cuc ⇒ ghep-thu ⇒ "
                                               "ghep-xac-nhan) rồi tải lại", refusal.status, "can_ghep_cot") from None
                raise
        upload.check_room(sha, folder)
        upload.finalize(temp, folder, sha)
    finally:
        temp.unlink(missing_ok=True)
    with store.connect(app.state.upload_store) as conn:
        assigned = store.record_auto_employee_masks(conn, base_map, new_employees) if new_employees else []
        store.record_pending_upload(conn, kind=kind, period=period, wanted_cutoff=wanted_cutoff.isoformat() if wanted_cutoff else None, file_sha256=sha)
    started = _start_from_pending(app)
    reply = started if started is not None else {**_empty_status(app), "da_nhan": kind}
    if assigned:
        reply["nhan_vien_moi"] = [{"ma_che": entry["masked_code"], "ho_ten": entry["full_name"]} for entry in assigned]
    return reply


def _start_from_pending(app, at_startup: bool = False) -> dict | None:
    """Đủ ba file trong bộ chờ ⇒ dựng bản ĐẦU của kỳ bằng đúng đoạn code lúc bật (`_start_period`) trên khuôn workflow đã thay ba đường file.
    Chưa đủ ⇒ None. Bộ file không tính được ⇒ kỳ vẫn trống; lúc tải thì từ chối có lời, lúc bật thì giữ lời ở `pending_problem` (vẫn bật)."""
    from kpi_agent.backend import route_upload
    from kpi_agent.core.load_data import load_targets
    state = _pending_state(app)
    if state["con_thieu"]:
        return None
    have = state["have"]
    try:
        workflow = copy.deepcopy(app.state.workflow_template)
        period = app.state.empty_period
        params = lambda block: next(node for node in workflow["nodes"] if node["type"] == block).setdefault("params", {})
        params("load_rules")["path"] = _rules_file_for_period(workflow, period)
        params("read_targets")["path"] = str(have["chi_tieu"]["path"])
        team = sorted(str(code) for code in load_targets(have["chi_tieu"]["path"])["employees"])
        book = have[upload.ROUTE_BOOK]["path"]
        with store.connect(app.state.store) as conn:
            mapping, _source = route_upload.formula_for(book, store.mappings_of(conn, "tuyen"))
        if mapping is None:
            raise upload.UploadRefused(route_upload.need_formula_reply()["refused"], 409, route_upload.NEED_FORMULA)
        outcome = route_upload.try_formula(book, mapping, set(team))
        if not outcome["ok"]:
            raise upload.UploadRefused(f"sổ theo dõi không đọc được bảng tuyến theo công thức: {route_upload.problems_text(outcome['loi'])}", 422,
                                       "so_lech_khuon_tuyen")
        params("read_raw").update(path=str(have["raw"]["path"]), mcp_path=str(book), mcp_ghep=mapping, mcp_doi=team)
        raw_mapping, _raw_mapping_sha = _saved_mapping_for(app, "raw", have["raw"]["path"])
        if raw_mapping is not None:      # file đơn hàng đã nhận bằng một cách ghép cột ⇒ bản đầu đọc file bằng đúng cách ghép đó
            params("read_raw")["ghep"] = raw_mapping
        _refuse_team_mismatch(workflow)     # bất biến của bản lấy tuyến từ sổ: đội của khối đọc tuyến = mã của file chỉ tiêu của chính bản
        cutoff = date.fromisoformat(have["raw"]["wanted_cutoff"]) if have["raw"]["wanted_cutoff"] else upload.last_day(period)
        app.state.uploaded_at = have["raw"]["created_at"]
        app.state.pending_uploaded_at = {
            "raw": have["raw"]["created_at"],
            "chi_tieu": have["chi_tieu"]["created_at"],
            "tuyen": have[upload.ROUTE_BOOK]["created_at"],
        }
        try:
            _start_period(app, workflow, yaml.safe_dump(workflow, allow_unicode=True), cutoff, uploads_only=True)
        except RuntimeError as stop:
            raise upload.UploadRefused("bộ ba file chưa tính được — " + str(stop).replace("backend KHÔNG bật — ", ""), 422, "bo_file_khong_tinh_duoc") from None
        # tuyến đọc được nhưng không khớp đơn hàng (nhầm cột, đổi kênh) ⇒ không giữ bản — cùng ngưỡng dừng với lần tải sổ lên một bản đã có
        summary = {**outcome["tom_tat"], **_coverage_keys(_orders_vs_routes(app.state, outcome["mcp"], set(team)))}
        stops, warnings = route_upload.route_stops(summary)
        if stops:
            raise upload.UploadRefused(f"bảng tuyến đọc từ sổ không khớp file đơn hàng — không dựng bản dữ liệu: {route_upload.problems_text(stops)}",
                                       422, stops[0]["ma"])
        # như lần tải sổ lên một bản đã có: lưu ý về tuyến đi theo bản + ô tóm tắt để bản chồng lên sau đối chiếu lại được
        summary["canh_bao"] = warnings
        _attach_route_notes(app.state.ctx, summary)
        app.state.route_summary = summary
        _spice(app)      # tính sẵn 1 % gia vị (≈ thêm một lượt đọc file đơn hàng lúc file thứ ba tới) — lỗi của khoản này được giữ thành lời, không chặn bản
    except upload.UploadRefused as refusal:
        app.state.ctx, app.state.period_ready, app.state.workflow = None, False, app.state.workflow_template
        app.state.files_sha256, app.state.data_version, app.state.latest_data_version = {}, None, None
        app.state.uploaded_at, app.state.pending_uploaded_at = None, {}
        app.state.route_summary = None
        app.state.pending_problem = refusal.reason
        if at_startup:
            return None
        raise
    app.state.pending_problem = None
    return {**_version_summary(app.state.data_version, app.state, reused=False), "ban_dau_cua_ky": True}


# Host lạ ⇒ 400: chặn trang web lạ trong trình duyệt gọi vào cổng máy này qua DNS-rebinding (C rà bảo mật 03/11, R2)
# 10/11: chạy trong hộp của bộ Capitol (cùng mạng compose, không mở cổng ra máy) ⇒ thêm tên hộp qua KPI_ALLOWED_HOSTS (cách nhau dấu phẩy)
EXTRA_HOSTS = [host.strip() for host in os.getenv("KPI_ALLOWED_HOSTS", "").split(",") if host.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", *EXTRA_HOSTS])


# Rào mã phiên — đăng ký SAU CÙNG ⇒ chạy NGOÀI CÙNG: trước rào Host, trước cổng KPI_SYSTEM_ONLY, trước bộ định tuyến và mọi hàm xử lý.
# Thiếu / sai mã ⇒ 401 cùng một thân cho MỌI đường và MỌI phương thức (kể cả đường không tồn tại, thân hỏng) — không đọc dữ liệu, không ghi SQLite.
@app.middleware("http")
async def session_gate(request: Request, call_next):
    return session_key.unauthorized(request.headers, request.app.state.session_key) or await call_next(request)


def _versioned(handler):
    """Đường GET của trang / công cụ chỉ đọc `request.app.state` ⇒ đi qua ĐÚNG bản được hỏi bằng ?data_version= (thiếu / = bản lúc bật ⇒ như cũ; bản lạ
    ⇒ 404). Trước 2a-2 nấc 1 các đường này nhận tham số mà không đọc ⇒ hỏi bản tải lên vẫn nhận số bản lúc bật với mã 200 ( bắt ở
    /api/team-bonus 20/11).
    """
    @functools.wraps(handler)
    def wrapper(request: Request, *args, **kwargs):
        request = _for_version(request)
        return request if isinstance(request, JSONResponse) else handler(request, *args, **kwargs)
    return wrapper


BOOT_VERSION_ONLY = "màn này chỉ có cho bản dữ liệu lúc bật — chưa đọc theo bản tải lên"


def _boot_version_only(handler):
    """Màn giữ kết quả tính sẵn theo bản LÚC BẬT (điểm bán, danh sách đơn, tab theo dõi, tên, danh sách bản tính) chưa đọc theo bản tải lên ⇒ ?data_version=
    của bản khác bị TỪ CHỐI có lời (thân 200 như các lời từ chối của trang), không lặng lẽ trả số bản lúc bật."""
    @functools.wraps(handler)
    def wrapper(request: Request, *args, **kwargs):
        if request.query_params.get("data_version") not in (None, "", request.app.state.data_version):
            return queries._refused(BOOT_VERSION_ONLY)
        return handler(request, *args, **kwargs)
    return wrapper

@app.get("/api/meta")
def meta(request: Request):
    if not request.app.state.period_ready:
        # Kỳ trống: 200 với thân TỐI THIỂU, không ngày chốt, không số — bên dựng hộp dựa vào đường này để biết backend đã bật (vòng chờ, vân tay)
        return {"period": request.app.state.empty_period, "trang_thai": "cho_du_file", "data_version": None, "latest_data_version": None,
                "employee_ids": [], "kpis": list(queries.KPI_ORDER)}
    # 2.6: ?data_version= ⇒ meta của ĐÚNG bản đó (kỳ, ngày chốt, luật, cờ…) — thiếu ⇒ bản lúc bật như cũ; bản lạ ⇒ 404
    request = _for_version(request)
    if isinstance(request, JSONResponse):
        return request
    ctx = request.app.state.ctx
    return {"period": ctx.period, "cutoff": ctx.cutoff.isoformat(),
            "snapshot_at": ctx.snapshot_at.isoformat() if ctx.snapshot_at else None,
            "label": _provisional_label(request.app.state), "data_flags": ctx.data_flags, "employee_ids": sorted(ctx.to_real),
            # nguồn dữ liệu của bản đang chạy — trang đổi chữ "số chụp …" (sổ Tracking) / "đơn tới ngày …" (file đơn hàng)
            "data_source": "raw" if runner.data_block_type(request.app.state.workflow) == "read_raw" else "tracking",
            # mức tra cứu vòng 3: data_flags = đủ cờ cho trang; data_flags_for_model = bỏ cờ của riêng một người (get_data_provenance đọc khoá này)
            "data_flags_for_model": ctx.data_flags_for_model,
            "kpis": queries.KPI_ORDER, "files_sha256": request.app.state.files_sha256,
            # 13/11: file quyết định Thưởng thêm + 1 % gia vị (ngoài bảng 8 KPI) — nằm trong `data_version`, KHÔNG nằm trong dấu vân tay bản tính
            "extra_files_sha256": request.app.state.extra_files_sha256,
            # Tính năng 4 (câu 43 — khuôn C KHUON_TRUONG_NGUON_C.md): bản luật = tên file luật đang nạp @ sha 8 ký tự;
            # bậc 1 LUÔN "tam_tinh" (chưa có duyệt / chốt — bậc 2 mới có da_duyet, da_chot_admin)
            # status_label = bản có tên người soát cho trang; status_label_for_model = bản agent đưa vào lời dặn (không tên — C S3)
            "rule_version": ctx.rule_version, "status": ctx.status, "status_label": ctx.status_label_full,
            "status_label_for_model": ctx.status_label,
            "current_run_id": request.app.state.run_id,
            # 12/11 (bước "một nguồn số" với hộp Capitol): `data_version` = bản dữ liệu LÚC BẬT (trang User luôn hiện bản này — không đổi hành vi);
            # `latest_data_version` = bản của lần tải lên gần nhất qua /api/system/upload (chưa tải ⇒ bằng `data_version`). Duyệt không đổi cả hai.
            "data_version": request.app.state.data_version, "latest_data_version": request.app.state.latest_data_version,
            # S3: phiên bản workflow + lượt chạy workflow đã cho ra số đang hiện
            "workflow_version": request.app.state.workflow_version, "workflow_run_id": request.app.state.workflow_run_id,
            # Bước 3: nút tải trên trang đọc khoá này (bỏ tải nháp — chưa soát thì không có file)
            "export": _export_state(request.app.state.ctx.workflow_result, reviewed=ctx.status == "da_soat")}


def _export_state(result, reviewed: bool) -> dict:
    """Lượt đang dùng được tải định dạng nào: chỉ khi bản tính ĐÃ SOÁT và khối xuất `ok`. Không có khối xuất ⇒ không file (không còn đường nháp).
    Cùng luật với đường tải (`store.exported_formats`) — đường tải tự chặn, khoá này chỉ để trang hiện lý do."""
    steps = [step for step in result.steps if step["type"] == "export_files"]
    if not steps:
        return {"allowed": False, "formats": [], "reason": "workflow chưa có khối xuất file"}
    if not reviewed or steps[0]["status"] != "ok":
        return {"allowed": False, "formats": [], "reason": "chưa duyệt — bảng thưởng chưa được soát, chưa có file"}
    formats = next(outputs["export"]["formats"] for outputs in result.outputs.values() if "export" in outputs)
    return {"allowed": True, "formats": formats, "reason": None}


@app.get("/api/team-bonus")
@_versioned
def team_bonus(request: Request, period: str | None = None) -> dict:
    return team_ranking.add_ranking(queries.get_team_bonus_table(request.app.state.ctx, period=period))


@app.get("/api/team-board")
@_versioned
def team_board(request: Request, period: str | None = None) -> dict:
    """Bảng cho trang — KHÔNG phải công cụ của agent; nhãn tạm tính do code điền."""
    body = queries.team_board(request.app.state.ctx, period=period)
    return body if "refused" in body else {**body, "label": _provisional_label(request.app.state)}


@app.get("/api/opportunities")
@_versioned
def opportunities(request: Request, period: str | None = None) -> dict:
    """Tính năng 2 bản đầu "Có cơ hội lên bậc" ( (a) theo nhịp) — cho trang; KHÔNG phải công cụ của agent."""
    return opportunity_rules.opportunities(request.app.state.ctx, period=period)


# `:path` — nhận cả chuỗi có "/" (vd "NV01/../NV02", "%2F" đã giải mã) để KIỂM rồi từ chối bằng lời, thay vì 404 của bộ định tuyến
@app.get("/api/employees/{employee_id:path}")
@_versioned
def employee(request: Request, employee_id: str, period: str | None = None) -> dict:
    return queries.get_employee_result(request.app.state.ctx, employee_id=employee_id, period=period)


@app.get("/api/gap")
@_versioned
def gap(request: Request, employee_id: str | None = None, period: str | None = None) -> dict:
    ctx = request.app.state.ctx
    return gap_status.add_gap_status(ctx, queries.get_mid_month_gap(ctx, employee_id=employee_id, period=period))


@app.get("/api/explain/{employee_id:path}/{kpi}")
@_versioned
def explain(request: Request, employee_id: str, kpi: str, period: str | None = None) -> dict:
    return queries.explain_kpi(request.app.state.ctx, employee_id=employee_id, kpi=kpi, period=period)


# Nhóm A bậc mức tra cứu (09/11): 3 công cụ đọc mới — tham số đi qua query (không qua đường dẫn) ⇒ "../" không làm bộ định tuyến tách nhầm; queries.py kiểm + từ chối
@app.get("/api/team-kpi")
@_versioned
def team_kpi(request: Request, kpi: str | None = None, period: str | None = None) -> dict:
    return queries.get_team_kpi_summary(request.app.state.ctx, kpi=kpi, period=period)


@app.get("/api/compare")
@_versioned
def compare(request: Request, employee_a: str | None = None, employee_b: str | None = None, kpi: str | None = None,
            period: str | None = None) -> dict:
    return queries.compare_employees(request.app.state.ctx, employee_a=employee_a, employee_b=employee_b, kpi=kpi, period=period)


@app.get("/api/data-issues")
@_versioned
def data_issues(request: Request, employee_id: str | None = None, period: str | None = None) -> dict:
    return queries.get_data_issues(request.app.state.ctx, employee_id=employee_id, period=period)

@app.get("/api/what-if/{employee_id:path}/{kpi}")
@_versioned
def what_if_scenario(request: Request, employee_id: str, kpi: str, extra: str | None = None, period: str | None = None,
                     target_pct: str | None = None) -> dict:
    # Công cụ 4: kịch bản giả định một KPI — mọi tham số (kể cả `extra` / `target_pct` dạng chữ từ truy vấn) kiểm ở what_if.py.
    # A-ii-1 (09/11): thêm `target_pct` ("nếu đạt X % chỉ tiêu") — đúng một trong hai, what_if.py từ chối nếu gửi cả hai / không gửi
    return what_if.simulate_kpi_change(request.app.state.ctx, employee_id=employee_id, kpi=kpi, extra=extra, period=period, target_pct=target_pct)

# bậc 2a: bản tính đã lưu + một hành động ghi "Đã soát" (docs/ mục 2) ----------

def _one_line(text: str) -> str:
    # Tên người soát đi vào nhãn ⇒ vào lời dặn mô hình (B minor 3): bỏ ký tự điều khiển, gom xuống dòng / khoảng trắng thành một dấu cách
    return " ".join("".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in text).split())


def _refused_json(reason: str, status: int, code: str | None = None) -> JSONResponse:
    # `code` (18/11): mã ổn định của loại từ chối cho bên gọi rẽ nhánh (màn ghép cột Capitol từng phải dò chữ trong `refused`); không có mã ⇒ không có khoá
    return JSONResponse({"refused": reason, **({"code": code} if code else {})}, status_code=status)


@app.get("/api/runs")
@_boot_version_only
def runs(request: Request, period: str | None = None) -> dict:
    ctx = request.app.state.ctx
    if period is not None and period != ctx.period:
        return queries._refused(f"chỉ có kỳ {ctx.period}", period=period)
    with store.connect(request.app.state.store) as conn:
        return {"period": ctx.period, "current_run_id": request.app.state.run_id, "runs": store.list_runs(conn, ctx.period)}


@app.get("/api/runs/{run_id}")
def run_detail(request: Request, run_id: int = RUN_ID):
    with store.connect(request.app.state.store) as conn:
        summary = store.run_summary(conn, run_id)
        if summary is None:
            return _refused_json(f"không có bản tính số {run_id}", 404)
        return {**summary, "lines": store.run_lines(conn, run_id), "events": store.run_events(conn, run_id)}


@app.post("/api/runs/{run_id}/review")
async def review(request: Request, run_id: int = RUN_ID):
    """Đường ghi DUY NHẤT của sản phẩm (2a). Không công cụ nào của agent gọi tới đây (agent chỉ có GET — backend_client).

    Chỉ nhận `application/json`: trang web lạ không gửi được JSON tới 127.0.0.1 khi backend không bật CORS (trình duyệt đòi hỏi trước);
    cộng rào Host đã có. Không đăng nhập ⇒ tên người soát là tên tự khai (giới hạn ghi ở kế hoạch mục 5).
    `async` mà gọi SQLite đồng bộ là CỐ Ý (B minor 2): bước "kiểm chưa soát ⇒ ghi" chạy liền một mạch trên vòng xử lý, hai lần bấm cùng lúc
    không cùng lọt qua bước kiểm. Đổi sang chạy song song (thread) thì phải thêm khoá / ràng buộc UNIQUE trước.
    """
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return _refused_json("chỉ nhận application/json", 422)
    try:
        body = await request.json()
    except ValueError:
        return _refused_json("thân yêu cầu không phải JSON", 422)
    if not isinstance(body, dict) or not isinstance(body.get("reviewer"), str) or not isinstance(body.get("note") or "", str):
        # C hộp đen F1: {"reviewer": 123} từng được nhận thành "123" ⇒ chỉ nhận chuỗi; note thiếu / null = không ghi chú (B minor bản 3)
        return _refused_json("reviewer (và note nếu có) phải là chuỗi chữ", 422)
    reviewer, note = _one_line(body["reviewer"]), (body.get("note") or "").strip()
    if not 1 <= len(reviewer) <= 60:
        return _refused_json("cần tên người soát (1–60 ký tự)", 422)
    if len(note) > 500:
        return _refused_json("ghi chú tối đa 500 ký tự", 422)
    with store.connect(request.app.state.store) as conn:
        try:
            result = store.mark_reviewed(conn, run_id, reviewer, note)
        except store.Refused as refusal:
            return _refused_json(refusal.reason, refusal.status)
    if run_id == request.app.state.run_id:
        _use_status(request.app.state.ctx, result["run"])
        _reopen_gates(request.app)
    return result


def _reopen_gates(app) -> None:
    """Soát xong bản đang dùng mà lượt đang dừng ở cổng ⇒ chạy lại với cổng mở, ghi THÊM một lượt (chỉ thêm) trỏ cùng bản tính (bước 3).

    "Chạy lại = chạy tiếp" vì mọi khối trước cổng tất định (khối AI bị cấm dây đi ra — nhóm kỹ thuật chạy lại cả tầng ⇒ AI trả tiền 2 lần).
    Chạy ngay trên vòng xử lý (≈ 1,6 s, như lúc bật): cố ý giữ "soát ⇒ mở" liền một mạch với bước kiểm chưa soát ở trên.
    Dấu vân tay lệch lượt cũ (không nên xảy ra) ⇒ không ghi, giữ lượt cũ — không để một lời soát mở cổng cho số khác.
    """
    ctx = app.state.ctx
    if ctx.workflow_result.status != "awaiting_review":
        return
    approved = _approved_gates(app.state.store, app.state.workflow, ctx.cutoff)
    if not approved:
        return
    result = runner.run_workflow(app.state.workflow, {"cutoff": ctx.cutoff}, {real: masked for masked, real in ctx.to_real.items()}, approved)
    if result.status != "ok" or result.fingerprint != ctx.workflow_result.fingerprint:
        return
    with store.connect(app.state.store) as conn:
        app.state.workflow_run_id = store.record_workflow_run(
            conn, workflow_version_id=app.state.workflow_version_id, cutoff=ctx.cutoff.isoformat(), status="ok", steps=result.steps,
            errors=[], inputs=result.fingerprint, bonus_run_id=app.state.run_id)
    ctx.workflow_result = result


@app.get("/api/runs/{run_id}/verify")
def verify(request: Request, run_id: int = RUN_ID):
    """Tính lại bản đã lưu từ NỘI DUNG luật đã lưu + file dữ liệu (kiểm sha trước) ⇒ so từng ô NV × KPI (tiêu chí 2a: 112 / 112)."""
    with store.connect(request.app.state.store) as conn:
        summary = store.run_summary(conn, run_id)
        if summary is None:
            return _refused_json(f"không có bản tính số {run_id}", 404)
        stored, content = store.run_lines(conn, run_id), store.rule_content(conn, run_id)
    workflow = request.app.state.workflow
    data_now = _data_files(_files_sha256(workflow), _workflow_files(workflow)[0])
    code_now, _ = _code_fingerprint(workflow)
    inputs_match = {"rule": hashlib.sha256(content.encode("utf-8")).hexdigest() == summary["rule_sha256"],
                    "data": data_now == summary["data_snapshot"]["files_sha256"], "code": code_now == summary["code_sha"]}
    if not inputs_match["data"]:
        # Chỉ nêu TÊN file (không đường dẫn máy, không chuỗi lỗi hệ điều hành) — đường này nằm trong danh sách đường GET bên ngoài gọi được
        missing = sorted(name for name, sha in data_now.items() if sha == "không có file")
        why = f"không đọc được file {', '.join(missing)}; file dữ liệu đã đổi so với lúc tính" if missing else "file dữ liệu đã đổi so với lúc tính"
        return {"run_id": run_id, "inputs_match": inputs_match, "refused": f"không tính lại được — {why}"}
    with tempfile.TemporaryDirectory() as folder:
        rule_file = Path(folder) / "rules_saved.yaml"
        rule_file.write_text(content, encoding="utf-8")
        # Chạy lại CHÍNH workflow đang dùng, chỉ đổi file luật sang nội dung luật đã lưu cùng bản tính
        saved = copy.deepcopy(workflow)
        next(node for node in saved["nodes"] if node["type"] == "load_rules")["params"]["path"] = str(rule_file)
        try:
            again = queries.build_context(date.fromisoformat(summary["data_snapshot"]["cutoff"]), saved,
                                          mask_path=_mask_path_for_workflow(request.app, saved))
        except queries.WorkflowStopped as stop:
            return {"run_id": run_id, "inputs_match": inputs_match, "refused": f"không tính lại được — {_stopped_reason(stop.result)}"}
    fields = ("pct", "reached_tier", "amount")
    now = {(line["employee_id"], line["kpi"]): line for line in queries.bonus_lines(again)}
    differs = [{"employee_id": line["employee_id"], "kpi": line["kpi"], "stored": {f: line[f] for f in fields},
                "recomputed": {f: now[key][f] for f in fields} if (key := (line["employee_id"], line["kpi"])) in now else None}
               for line in stored if (line["employee_id"], line["kpi"]) not in now
               or any(line[f] != now[(line["employee_id"], line["kpi"])][f] for f in fields)]
    return {"run_id": run_id, "inputs_match": inputs_match, "cells_total": len(stored), "cells_equal": len(stored) - len(differs),
            "cells_differ": differs, "team_total_stored": summary["team_total"],
            "team_total_recomputed": sum(line["amount"] for line in now.values())}


# bước 2a: trang CHỈ XEM workflow (mục 1) — chỉ GET ----------

@app.get("/api/blocks")
def block_catalog() -> dict:
    return {"blocks": workflow_view.block_catalog()}


@app.get("/api/workflow")
def current_workflow(request: Request) -> dict:
    """File workflow đang dùng (khối, dây, `position` nếu có) + phiên bản — trang vẽ từ đây."""
    return {"workflow_version": request.app.state.workflow_version, **request.app.state.workflow}


@app.get("/api/workflow-runs")
def workflow_runs(request: Request) -> dict:
    with store.connect(request.app.state.store) as conn:
        # Danh sách không mang `inputs` (đường file dữ liệu + sha — 2a GĐ1: trang không cần); xem trong /{run_id}
        runs = [{key: value for key, value in run.items() if key != "inputs"} for run in store.list_workflow_runs(conn)]
        return {"current_workflow_run_id": request.app.state.workflow_run_id, "runs": runs}


@app.get("/api/workflow-runs/{run_id}")
def workflow_run_detail(request: Request, run_id: int = RUN_ID):
    """Một lượt: bước (trạng thái, giây, lời lỗi đã che) + khối / dây của ĐÚNG phiên bản lượt đó. Tóm tắt cổng ra chỉ có cho lượt đang hiện số
    (kết quả nằm trong bộ nhớ lúc bật; lượt cũ không lưu kết quả khối)."""
    with store.connect(request.app.state.store) as conn:
        summary = store.workflow_run_summary(conn, run_id)
        if summary is None:
            return _refused_json(f"không có lượt chạy workflow số {run_id}", 404)
        version = yaml.safe_load(store.workflow_version_content(conn, summary["workflow_version_id"]))
    is_current = run_id == request.app.state.workflow_run_id
    to_mask = {real: masked for masked, real in request.app.state.ctx.to_real.items()}
    return {**summary, "nodes": version["nodes"], "edges": version["edges"], "is_current": is_current,
            "port_summaries": workflow_view.port_summaries(request.app.state.ctx.workflow_result, to_mask) if is_current else None}


# bước 2b: trang kéo–thả — kiểm / lưu / chạy THỬ (workflow_edit.py — bản 2 mục 3) ----------
# POST chỉ nhận application/json (như /review): không bật CORS ⇒ trang lạ trong trình duyệt không gửi được JSON tới 127.0.0.1. Rào Host đã có.
# Chạy thử KHÔNG đụng app.state.ctx ⇒ số trang chính + trợ lý không đổi, "Đã soát" không mất.
TRIAL_LOCK = threading.Lock()   # một lượt chạy thử một lúc (mỗi lượt đọc lại sổ Tracking 7 MB)


async def _json_body(request: Request) -> dict | JSONResponse:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        # 415 = sai kiểu nội dung (kế hoạch 2b T7 — 11:00 minor 1 — bản đầu trả 422). Thân JSON hỏng / không phải bảng vẫn 422
        return _refused_json("chỉ nhận application/json", 415)
    try:
        body = await request.json()
    except ValueError:
        return _refused_json("thân yêu cầu không phải JSON", 422)
    return body if isinstance(body, dict) else _refused_json("thân yêu cầu phải là một bảng JSON", 422)


def _to_mask(request: Request) -> dict:
    return {real: masked for masked, real in request.app.state.ctx.to_real.items()}


@app.get("/api/workflow/files")
def workflow_files() -> dict:
    """File ô `path` được chọn trên trang — cũng là danh sách duy nhất check / save / run chấp nhận."""
    return {"files": workflow_edit.allowed_files()}


@app.post("/api/workflow/check")
async def workflow_check(request: Request):
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    normalized, items = workflow_edit.check_items(body.get("workflow"), workflow_edit.allowed_files(), _to_mask(request))
    return {"kiem": items, "workflow_sha256": None if items else runner.workflow_sha256(normalized)}


@app.post("/api/workflow/save")
async def workflow_save(request: Request):
    """Kiểm lại ở backend (không tin trang) ⇒ một dòng `workflow_version` nếu mã băm logic mới; cùng mã băm ⇒ dùng lại dòng cũ (kéo khối không đẻ bản)."""
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    normalized, items = workflow_edit.check_items(body.get("workflow"), workflow_edit.allowed_files(), _to_mask(request))
    if items:
        return JSONResponse({"refused": f"chưa lưu — {len(items)} lỗi kiểm", "kiem": items}, status_code=422)
    sha = runner.workflow_sha256(normalized)
    text = yaml.safe_dump(normalized, allow_unicode=True, sort_keys=False)
    with store.connect(request.app.state.store) as conn:
        existed = conn.execute("SELECT id FROM workflow_version WHERE sha256 = ?", (sha,)).fetchone()
        version_id = store.record_workflow_version(conn, normalized["workflow"], text, sha)
    return {"workflow_version_id": version_id, "workflow_version": f"{normalized['workflow']}@{sha[:8]}", "is_new": existed is None,
            "workflow": normalized, "kiem": []}


@app.get("/api/workflow-versions")
def workflow_versions(request: Request) -> dict:
    with store.connect(request.app.state.store) as conn:
        return {"current": request.app.state.workflow_version, "versions": store.list_workflow_versions(conn)}


@app.get("/api/workflow-versions/{version_id}")
def workflow_version_detail(request: Request, version_id: int = FastApiPath(ge=1, le=SQLITE_MAX_INT)):   # RUN_ID gắn tên run_id — không dùng chung được
    with store.connect(request.app.state.store) as conn:
        row = conn.execute("SELECT id, name, sha256, content, created_at FROM workflow_version WHERE id = ?", (version_id,)).fetchone()
    if row is None:
        return _refused_json(f"không có phiên bản workflow số {version_id}", 404)
    content = yaml.safe_load(row["content"])
    return {"workflow_version_id": row["id"], "workflow_version": f"{row['name']}@{row['sha256'][:8]}", "created_at": row["created_at"], **content}


@app.post("/api/workflow/run")
async def workflow_trial_run(request: Request):
    """Chạy THỬ một phiên bản đã lưu với ngày chốt người dùng gõ ⇒ bước + tóm tắt cổng ra (đã che mã) + 1 dòng `workflow_trial_run`."""
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    version_id, cutoff_text = body.get("workflow_version_id"), body.get("cutoff")
    if not isinstance(version_id, int) or isinstance(version_id, bool) or not 1 <= version_id <= SQLITE_MAX_INT:
        return _refused_json("workflow_version_id phải là số phiên bản đã lưu", 422)
    try:
        cutoff = date.fromisoformat(cutoff_text) if isinstance(cutoff_text, str) and len(cutoff_text) == 10 else None
    except ValueError:
        cutoff = None
    if cutoff is None:
        return _refused_json("cần ngày chốt dạng YYYY-MM-DD (ngày chốt là tham số mỗi lượt, không có mặc định)", 422)
    with store.connect(request.app.state.store) as conn:
        row = conn.execute("SELECT content FROM workflow_version WHERE id = ?", (version_id,)).fetchone()
    if row is None:
        return _refused_json(f"không có phiên bản workflow số {version_id}", 404)
    to_mask = _to_mask(request)
    # Kiểm lại cả bản đã lưu: bảng có thể chứa phiên bản do file lúc bật ghi (KPI_WORKFLOW trỏ ngoài danh sách) — đường file vẫn phải được phép
    workflow, items = workflow_edit.check_items(yaml.safe_load(row["content"]), workflow_edit.allowed_files(), to_mask)
    if items:
        return JSONResponse({"refused": f"không chạy — {len(items)} lỗi kiểm", "kiem": items}, status_code=422)
    if not TRIAL_LOCK.acquire(blocking=False):
        return _refused_json("đang có một lượt chạy thử — chờ lượt đó xong", 409)
    try:
        # Cổng người duyệt chỉ mở khi đúng đầu vào này đã có bản tính đã soát (bản đang dùng) — lượt thử khác số ⇒ dừng ở cổng
        approved = _approved_gates(request.app.state.store, workflow, cutoff)
        result = await run_in_threadpool(runner.run_workflow, workflow, {"cutoff": cutoff}, to_mask, approved)
    finally:
        TRIAL_LOCK.release()
    if result.status == "rejected":
        return JSONResponse({"refused": "bộ chạy từ chối — " + "; ".join(result.errors), "errors": result.errors}, status_code=422)
    summary = {"ports": workflow_view.port_summaries(result, to_mask), "team_total": runner.team_total(result)}
    with store.connect(request.app.state.store) as conn:
        # "awaiting_review" (dừng ở cổng) ghi "ok" như lượt bật — bảng chỉ nhận ok / stopped (10/11 không đổi CHECK); bước cổng mang "awaiting_review"
        trial_id = store.record_workflow_trial_run(conn, workflow_version_id=version_id, cutoff=cutoff.isoformat(),
                                                   status="stopped" if result.status == "stopped" else "ok",
                                                   steps=result.steps, errors=result.errors, inputs=result.fingerprint, summary=summary)
    return {"trial_run_id": trial_id, "workflow_version_id": version_id, "cutoff": cutoff.isoformat(), "status": result.status,
            "steps": result.steps, "errors": result.errors, **summary}


@app.get("/api/workflow/trial-runs")
def workflow_trial_runs(request: Request) -> dict:
    with store.connect(request.app.state.store) as conn:
        return {"runs": store.list_workflow_trial_runs(conn)}


# xuất file bảng thưởng — chỉ GET, chỉ đọc bản tính đã lưu ----------
EXPORT_KINDS = {"xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", export_report.to_xlsx),
                "md": ("text/markdown; charset=utf-8", export_report.to_markdown),
                "txt": ("text/plain; charset=utf-8", export_report.to_text)}


@app.get("/api/export/{kind}")
def export_bonus_table(request: Request, kind: str, run_id: int | None = Query(default=None, ge=1, le=SQLITE_MAX_INT)):
    """Tải bảng thưởng của bản tính đang dùng (hoặc `run_id`) — Excel 3 sheet / Markdown / chữ thường; tên file do code đặt.
    Có header X-Kpi-Sys-Key (khối Xuất của bộ Capitol) ⇒ đường hệ thống `_system_export` (duyệt = bản ghi của Người duyệt Capitol); không có ⇒ như cũ."""
    if kind not in EXPORT_KINDS:
        return _refused_json(f"chỉ xuất được: {', '.join(EXPORT_KINDS)}", 404)
    if SYS_KEY_HEADER in request.headers or request.app.state.system_only:
        return _system_export(request, kind, run_id)   # cờ KPI_SYSTEM_ONLY: không còn đường tải không khoá (thiếu khoá ⇒ 401)
    wanted = run_id or request.app.state.run_id
    with store.connect(request.app.state.store) as conn:
        try:
            data = export_report.report_data(conn, wanted)
        except ValueError as problem:
            # bản tính thiếu ô / tổng lệch ⇒ KHÔNG xuất, trả lời rõ (13:23, minor 1 — bản đầu ra 500 trống)
            return _refused_json(f"không xuất được — {problem}", 409)
    if data is None:
        return _refused_json(f"không có bản tính số {wanted}", 404)
    with store.connect(request.app.state.store) as conn:
        formats, summary = store.exported_formats(conn, wanted), store.run_summary(conn, wanted)
    if kind not in formats:
        # Chỉ có file khi bản tính ĐÃ SOÁT, chưa bị thay, và một lượt workflow của đúng bản đó đã đi qua khối xuất —: bỏ tải nháp
        if summary["replaced_by"] is not None:
            reason = f"bản này đã bị thay bởi bản tính {summary['replaced_by']} — soát bản mới"
        elif summary["status"] != "da_soat" or not formats:
            reason = "chưa duyệt — bảng thưởng chưa được soát, chưa có file"
        else:
            reason = f"workflow chỉ khai báo xuất: {', '.join(formats)}"
        return _refused_json(f"không xuất được {kind} — {reason}", 409)
    return _file_response(kind, _with_export_names(request, data))


def _with_export_names(request: Request, data: dict) -> dict:
    """KPI_NAMES=on ⇒ dữ liệu file thêm bảng tên (file có 2 cột Tên + Mã SM cạnh cột mã che); off ⇒ nguyên, file y như cũ.
    Thêm ở bước TRÌNH BÀY file, không vào `report_data` ⇒ mã băm bảng thưởng (`sha256_bang`) không đổi theo cờ.
    """
    entries = request.app.state.name_entries
    return {**data, "names": entries} if entries else data


def _file_response(kind: str, data: dict) -> Response:
    media, build = EXPORT_KINDS[kind]
    body = build(data)
    return Response(content=body if isinstance(body, bytes) else body.encode("utf-8"), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{export_report.file_name(data, kind)}"'})


def _system_export(request: Request, kind: str, run_id: int | None):
    """Khối Xuất của Capitol (10/11 theo -4e): chỉ có file khi khối Người duyệt Capitol ĐÃ GHI duyệt (`POST /api/system/duyet`) đúng bản tính
    đang dùng + đúng mã băm bảng HIỆN TẠI. Không nhận mã băm từ bên gọi — bên có khoá tự tính băm rồi lấy file thì bỏ qua được Người duyệt (-4e chỉ ra).
    Bảng đổi sau khi duyệt ⇒ băm khác ⇒ 409 "duyệt lại". Không đụng "Đã soát" của trang.
    """
    problem = _system_key_problem(request)
    if problem:
        return problem
    request = _for_version(request)
    if isinstance(request, JSONResponse):
        return request
    if run_id is not None:
        return _refused_json("đường hệ thống chỉ xuất bản tính đang dùng — không nhận run_id", 422)
    table = _bonus_table_body(request)
    if isinstance(table, JSONResponse):
        return table
    current = request.app.state.run_id
    with store.connect(request.app.state.store) as conn:
        approval = store.capitol_approval_for(conn, current, table["sha256"])
        if approval is None:
            return _refused_json(f"không xuất được {kind} — chưa duyệt: Người duyệt chưa duyệt bảng thưởng hiện tại "
                                 f"(bản tính {current}, mã băm {table['sha256'][:16]}…) — duyệt (lại) rồi xuất", 409)
        try:
            data = export_report.report_data(conn, current)
        except ValueError as problem:
            return _refused_json(f"không xuất được — {problem}", 409)
    at = datetime.fromisoformat(approval["created_at"])
    # Tiêu đề / trạng thái theo lần duyệt Capitol (không chép tên người duyệt — file dễ bị chuyển tiếp, như bản trang User)
    status = f"đã duyệt ở khối Người duyệt lúc {at:%d/%m/%Y %H:%M} (mã băm bảng {table['sha256'][:16]}) — vẫn chưa phải số chốt (Admin miền chốt)"
    source = [(label, status) if label == "Trạng thái" else (label, value) for label, value in data["source"]]
    response = _file_response(kind, _with_export_names(request, {**data, "title": export_report.TITLE_REVIEWED, "status": status, "source": source}))
    # Hộp Capitol không giữ bảng mã / tên thật ⇒ chỉ backend đếm được (theo -4e 10/11): file ra ngoài có chuỗi thật ⇒ không trả.
    # Không in chuỗi thật vào lời trả / log — chỉ số lượng.
    if request.app.state.name_entries:
        # KPI_NAMES=on (16:50: bỏ hẳn luật che tên) ⇒ file CÓ cột Tên + Mã SM, không chặn tên / mã thật trong file
        return response
    try:
        hits = _real_identity_hits(request.app, response.body, kind)
    except ValueError as problem:
        return _refused_json(f"không xuất được {kind} — không kiểm được tên thật ({problem})", 409)
    if hits:
        return _refused_json(f"không xuất được {kind} — file có {hits} chỗ chứa mã / tên nhân viên thật (chỉ được mã che NV01–NV14)", 409)
    return response


def _real_identity_tokens(app) -> list[str]:
    """Mã thật (bảng che `ma_che_ten.json`) + họ tên đầy đủ và 2 chữ cuối của tên (file chỉ tiêu — trang sếp gọi NV bằng 2 chữ cuối). NFC, chữ thường,
    gom khoảng trắng. Đọc lại mỗi lần xuất (không đệm). ĐÓNG KHI LỖI: danh sách rỗng ⇒ đếm ra 0 ⇒ file lọt;
    nên bảng che đọc lỗi / rỗng / thiếu một NV của đội, hoặc NV không có tên ⇒ ValueError (người gọi trả 409, không trả file).
    """
    from kpi_agent.core.load_data import load_targets
    try:
        codes = {str(code) for code in _all_known_mask_map(app).values()}
        _rules_file, targets_file, _data_file = _workflow_files(app.state.workflow)
        team = load_targets(targets_file)["employees"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("không đọc được bảng mã che / file chỉ tiêu") from None
    names = [str(info.get("name") or "").strip() for info in team.values()]
    if not team or {str(code) for code in team} - codes or not all(names):
        raise ValueError("bảng mã che / tên thiếu nhân viên của đội")
    raw = list(codes) + names + [" ".join(name.split()[-2:]) for name in names if len(name.split()) >= 2]
    return sorted({" ".join(unicodedata.normalize("NFC", t).lower().split()) for t in raw if t and t.strip()})


RICH_TEXT_CELL = re.compile(r"<(si|is)\b[^>]*>(.*?)</\1>", re.DOTALL)
TEXT_RUN = re.compile(r"<t\b[^>]*>(.*?)</t>", re.DOTALL)


def _real_identity_hits(app, body: bytes, kind: str) -> int:
    """Số lần chuỗi thật xuất hiện trong NỘI DUNG file: xlsx = MỌI phần XML sau giải nén (sharedStrings, sheet, tên sheet trong workbook.xml,
    docProps / thuộc tính), bỏ mã thực thể XML (&#…; / &amp;) trước khi đếm; md / txt = chữ của file."""
    import html
    import io
    import zipfile
    if kind == "xlsx":
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            text = "\n".join(archive.read(name).decode("utf-8", errors="replace") for name in archive.namelist())
        # Một ô chữ (<si> sharedStrings, <is> inlineStr) có thể chia nhiều đoạn <r><t>…</t></r> ⇒ thay cả ô bằng chữ đã nối của mọi <t> rồi mới đếm
        # (tên thật chia 2 đoạn không lọt). docProps / workbook.xml giữ nguyên chữ thô.
        text = RICH_TEXT_CELL.sub(lambda cell: "".join(TEXT_RUN.findall(cell.group(2))), text)
        text = html.unescape(text)
    else:
        text = body.decode("utf-8", errors="replace")
    # Gom mọi khoảng trắng (cả xuống dòng / dấu cách đôi) thành một dấu cách — 10/11 (minor). Tên tách nhiều đoạn <r><t> chưa bắt (to_xlsx viết chuỗi trơn)
    text = " ".join(unicodedata.normalize("NFC", text).lower().split())
    return sum(text.count(token) for token in _real_identity_tokens(app))

# API cho bộ Capitol (KPI agent chạy trên Capitol, công cụ gắn thẳng gọi HTTP) ----------
# Công cụ: ĐÚNG đường khối agent (`direct_tools`, T2 chứng minh = đường HTTP agent mình đang dùng) — Capitol không gọi thẳng 10 GET cũ vì đường
# agent còn lớp kiểm tham số (`check_tool_args`), gọt kết quả get_opportunities, ghép get_data_provenance. Kỳ do HỆ THỐNG điền (kỳ đang nạp):
# mô tả gửi Capitol không có ô `period`, gửi kèm `period` ⇒ từ chối.
SYS_KEY_HEADER = "x-kpi-sys-key"


def _agent_context(request: Request) -> AgentContext:
    # Như agent đọc từ /api/meta lúc bật (backend_client.load_context), nhưng cờ = bản cho mô hình (mức tra cứu vòng 3)
    ctx = request.app.state.ctx
    return AgentContext("backend — gọi thẳng hàm", ctx.period, ctx.cutoff, sorted(ctx.to_real), list(queries.KPI_ORDER),
                        ctx.data_flags_for_model, ctx.rule_version, ctx.status_label)


def _provenance_for_model(request: Request) -> dict:
    """get_data_provenance trong cùng tiến trình — đúng các trường `backend_client._data_provenance` ghép từ /api/meta + /api/runs/{id}."""
    meta_now = meta(request)
    with store.connect(request.app.state.store) as conn:
        run = store.run_summary(conn, request.app.state.run_id)
    if run is None:
        return {"error": "không đọc được bản tính đang dùng"}
    return {**{key: meta_now.get(key) for key in META_FIELDS_FOR_MODEL}, "data_flags": meta_now.get("data_flags_for_model"),
            "bonus_run": {key: run.get(key) for key in RUN_FIELDS_FOR_MODEL}}


@app.get("/api/tools")
def tool_list(request: Request):
    """Mô tả các công cụ (một nguồn: `agent/tool_specs.py`) BỎ ô `period` — để bên Capitol dựng công cụ gắn thẳng, không chép tay mô tả.
    Kỳ trống: danh sách công cụ không phụ thuộc dữ liệu ⇒ vẫn trả (kỳ = kỳ đang mở; mô tả theo mã che — bảng tên chỉ có khi kỳ đã có bản)."""
    empty = not request.app.state.period_ready
    if not empty:
        request = _for_version(request)
        if isinstance(request, JSONResponse):
            return request
    tools = []
    for schema in tool_specs.tool_schemas(list(queries.KPI_ORDER), names=bool(request.app.state.name_entries)):
        function = copy.deepcopy(schema["function"])
        function["parameters"]["properties"].pop("period", None)
        function["parameters"]["required"] = [name for name in function["parameters"].get("required", []) if name != "period"]
        tools.append(function)
    return {"period": request.app.state.empty_period if empty else request.app.state.ctx.period, "tools": tools}


@app.post("/api/tool/{name}")
async def tool_call(request: Request, name: str):
    """Một lần gọi công cụ của AI Capitol: thân {"args": {...}} ⇒ đúng kết quả mô hình của agent mình nhận (lời từ chối cũng trả 200 trong thân
    — như agent mình, để mô hình đọc được lý do). Thân sai hình ⇒ 415 / 422."""
    request = _for_version(request)
    if isinstance(request, JSONResponse):
        return request
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    args = body.get("args", {})
    if set(body) - {"args"} or not isinstance(args, dict):
        return _refused_json('thân phải là {"args": {...}}', 422)
    run_key = _run_key(request.headers.get(RUN_ID_HEADER), request.headers.get(ORG_ID_HEADER))
    if run_key is False:
        return _refused_json("mã lượt / tổ chức sai dạng (1–128 ký tự chữ, số, _ . : -)", 422)
    result = _tool_for_model(request, name, args)
    if run_key:
        _record_call(request.app.state.run_calls, run_key, name, args, result)
    return result


# Cách 2 (27 đề xuất, A hai bên chốt 10/11): công cụ gắn thẳng của Capitol gửi header mã lượt + tổ chức do HỆ THỐNG điền
# (không phải tham số AI) ⇒ backend tự ghi các lần đã phục vụ ⇒ Rào chắn gửi `run_id` vào kiem-so ⇒ nguồn số = đúng các lần đó (không tin bên gửi
# lẫn AI — kết quả là của chính backend). Chỉ trong bộ nhớ, có trần: hộp bật lại / mã lạ ⇒ không có nhật ký ⇒ kiem-so trượt (đóng khi lỗi).
RUN_ID_HEADER, ORG_ID_HEADER = "x-kpi-run-id", "x-kpi-org-id"
RUN_KEY_PATTERN = re.compile(r"[A-Za-z0-9_.:\-]{1,128}")
MAX_LOGGED_RUNS, MAX_CALLS_PER_RUN = 200, 20


def _run_key(run_id, org_id) -> tuple[str, str] | None | bool:
    """(tổ chức, mã lượt); None = không có mã lượt (không ghi); False = sai dạng."""
    if run_id in (None, ""):
        return None
    org_id = org_id or ""
    if not isinstance(run_id, str) or not RUN_KEY_PATTERN.fullmatch(run_id) or not (org_id == "" or RUN_KEY_PATTERN.fullmatch(org_id)):
        return False
    return org_id, run_id


def _record_call(log: OrderedDict, key: tuple[str, str], name: str, args: dict, result: dict) -> None:
    """Ghi một lần gọi KHÔNG bị từ chối (lời từ chối không phải nguồn số). Quá MAX_CALLS_PER_RUN ⇒ đánh dấu tràn (None) ⇒
    kiem-so trượt (không tự cắt bớt nguồn). Quá MAX_LOGGED_RUNS lượt ⇒ bỏ lượt cũ nhất.
    """
    calls = log.get(key, [])
    log[key] = calls
    log.move_to_end(key)
    while len(log) > MAX_LOGGED_RUNS:
        log.popitem(last=False)
    if calls is None or "refused" in result or "error" in result:
        return
    if len(calls) >= MAX_CALLS_PER_RUN:
        log[key] = None
        return
    calls.append({"name": name, "args": dict(args), "result": result})


def _tool_for_model(request: Request, name: str, args: dict) -> dict:
    """Một lần gọi công cụ đúng đường agent — dùng cho `/api/tool/{name}` và để Rào chắn chạy lại lần gọi (`kiem-so`)."""
    if name not in ALL_TOOL_PARAMS:
        return {"refused": f"không có công cụ {queries._short(name)}"}
    if "period" in args:
        return {"refused": "không nhận period — kỳ do hệ thống điền (kỳ đang có: " + request.app.state.ctx.period + ")"}
    if name == "simulate_kpi_change":
        args = _drop_zero_partner(args)
    entries = request.app.state.name_entries
    if entries:
        # KPI_NAMES=on: tên / 2 chữ cuối / mã SM ở tham số nhân viên ⇒ mã che TRƯỚC mọi luật (lõi chỉ biết mã che); không rõ ⇒ từ chối, hỏi lại
        args, unclear = agent_names.resolve_args(args, entries)
        if unclear:
            return unclear
    refusal = check_tool_args(_agent_context(request), name, args)
    if refusal:
        return refusal
    return agent_names.with_names(_tool_result(request, name, args), entries) if entries else _tool_result(request, name, args)


def _tool_result(request: Request, name: str, args: dict) -> dict:
    """Kết quả lõi (mã che) của một lần gọi đã qua luật tham số."""
    if name in calc_tool.CALC_TOOLS:
        return calc_tool.run(name, args, lambda tool, tool_args, period: _calc_source(request, tool, tool_args, period),
                             request.app.state.ctx.period)
    if name == "get_data_provenance":
        return _provenance_for_model(request)
    if name == "get_spice_loyalty":
        return json.loads(json.dumps(jsonable_encoder(spice_tool.get_spice_loyalty(
            request.app.state.ctx, spice(request), employee_id=args.get("employee_id") or None))))
    if name == "get_extra_bonus":
        # cần app (workflow, file RAW) — không qua direct_tools (khối agent trong dây chuyền tự viết không có công cụ này)
        return json.loads(json.dumps(jsonable_encoder(extra_bonus_tool.get_extra_bonus(
            request.app.state.ctx, _extra_bonus(request.app), employee_id=args.get("employee_id") or None))))
    result = json.loads(json.dumps(jsonable_encoder(direct_tools._run_tool(request.app.state.ctx, name, args))))
    if name == "get_mid_month_gap":
        # chỉ tiêu đã ở mốc cao nhất + kỳ hết ngày — gắn ở cửa ra như `team_sentences` (queries.py nằm trong dấu vân tay bản tính)
        result = gap_status.add_gap_status(request.app.state.ctx, result)
    if name == "get_team_bonus_table":
        # xếp hạng lương kinh doanh do CODE xếp (mô hình tự so 14 dòng từng đọc sai người thấp nhất) — gắn ở cửa ra, cùng lý do như trên
        result = team_ranking.add_ranking(result)
    # Bậc (a) 11/11: công cụ cả đội + câu chuẩn code viết (`team_sentences`) để mô hình chép nguyên — gắn ở cửa ra, không ở queries.py (dấu vân tay bản tính)
    return with_team_sentences(name, result)
def _calc_source(request: Request, tool: str, args: dict, period: str | None) -> dict:
    """Công cụ tính đọc một số: chạy lại công cụ đọc qua ĐÚNG đường agent (luật tham số, tên ⇒ mã che) trên bản dữ liệu của lượt; `period` khác kỳ
    của lượt (chỉ calc_period_change) ⇒ bản dữ liệu MỚI NHẤT của kỳ đó trong bộ nhớ — hệ thống chọn bản, mô hình không điền data_version.
    Chưa có bản của kỳ đó ⇒ lời từ chối, không đoán."""
    if period in (None, "", request.app.state.ctx.period):
        # kỳ HIỆN TẠI: luôn đúng bản của lượt (không thay bằng bản "mới nhất" của kỳ)
        state, target = request.app.state, request
    else:
        # Bản lúc bật nằm ở app.state GỐC, không có trong `versions` (chỉ chứa bản tải lên) ⇒ lượt trên bản tải lên phải xét cả nó
        # (tích hợp 18/11: bật T10, tải T11, hỏi trên T11 "so với tháng 10" ⇒ từ chối nhầm). Lượt trên bản tải lên: state là
        # _VersionState, bản gốc ở `_base`.
        boot = getattr(request.app.state, "_base", request.app.state)
        same_period = [state for state in boot.versions.values() if state.ctx.period == period]
        if same_period:
            state = same_period[-1]                   # dict giữ thứ tự thêm ⇒ phần tử cuối = bản tải sau cùng của kỳ đó (mới hơn bản lúc bật)
        elif boot.ctx.period == period:
            state = boot
        else:
            return {"refused": f"chưa có bản dữ liệu kỳ {queries._short(period)} (backend chỉ có kỳ {request.app.state.ctx.period})"}
        target = _VersionRequest(request, state)
    result = _tool_for_model(target, tool, args)
    if isinstance(result, dict) and "refused" not in result and "error" not in result:
        # bản đã dùng — dạng "data@<8 hex>" (mẫu mã bản luật number_check BỎ QUA: chuỗi hex không thành "số có nguồn" giả cho Rào)
        result = {**result, "ban_du_lieu": f"data@{state.data_version[:8]}"}
    return result


@app.get("/api/calc/{name}")
def calc_call(request: Request, name: str, args: str = "{}"):
    """Đường chat của mình gọi công cụ tính (GET như các công cụ đọc): `args` = bảng tham số dạng JSON. Capitol đi `/api/tool/{name}` như mọi công cụ."""
    request = _for_version(request)
    if isinstance(request, JSONResponse):
        return request
    if name not in calc_tool.CALC_TOOLS:
        return _refused_json(f"không có công cụ tính {queries._short(name)}", 404)
    try:
        parsed = json.loads(args)
    except ValueError:
        return {"refused": "args phải là JSON"}
    if not isinstance(parsed, dict):
        return {"refused": "args phải là bảng JSON {…}"}
    return _tool_for_model(request, name, parsed)

def _team_sources(request: Request) -> list[dict]:
    """Nguồn số cho Rào chắn khi KHÔNG có danh sách lần gọi công cụ (Capitol: Rào chắn chỉ nhận chữ — 27 xác nhận 10/11): kết quả các công cụ
    cấp đội + từng NV × KPI, gọi đúng đường agent. Giới hạn: lỏng hơn "chỉ số của lượt" (số có ở bất kỳ đâu trong dữ liệu của đội đều có nguồn);
    KHÔNG gồm simulate_kpi_change / compare_employees (tổ hợp tham số vô hạn) ⇒ câu "nếu thêm / đạt X" hay so hai người cần `tool_calls`."""
    employees, kpis = sorted(request.app.state.ctx.to_real), list(queries.KPI_ORDER)
    calls = [("get_team_bonus_table", {}), ("get_mid_month_gap", {}), ("get_opportunities", {}), ("get_data_issues", {}), ("get_data_provenance", {})]
    calls += [("get_team_kpi_summary", {"kpi": kpi}) for kpi in kpis]
    # Bậc (b) 11/11: + Thưởng thêm — bản cả đội VÀ từng NV; thiếu ⇒ mọi câu thưởng thêm trên Capitol bị chặn nhầm. Đường Tracking ⇒ lời từ chối
    # (không phải nguồn số — `kiem_so._not_refusal`)
    calls.append(("get_extra_bonus", {}))
    # Công cụ 12 (13/11): bản cả đội đã mang đủ dòng từng nhân viên phụ trách + câu chuẩn ⇒ không gọi lẻ từng NV. Thiếu ⇒ mọi câu 1 % gia vị trên
    # Capitol bị chặn "số không nguồn". Đường Tracking ⇒ lời từ chối (không phải nguồn số)
    calls.append(("get_spice_loyalty", {}))
    calls += [(name, {"employee_id": nv}) for nv in employees
              for name in ("get_employee_result", "get_mid_month_gap", "get_data_issues", "get_extra_bonus")]
    calls += [("explain_kpi", {"employee_id": nv, "kpi": kpi}) for nv in employees for kpi in kpis]
    return [_tool_for_model(request, name, args) for name, args in calls]


def _system_key_problem(request: Request) -> JSONResponse | None:
    """Đường HỆ THỐNG (Người duyệt / Xuất / Rào chắn gọi, AI không được gọi): header X-Kpi-Sys-Key = KPI_SYS_KEY (so hằng thời gian).
    Chưa đặt khoá (≥ 16 ký tự) ⇒ 503: không chạy đường hệ thống không khoá. Không ghi giá trị khoá vào log / lời trả."""
    key = os.getenv("KPI_SYS_KEY", "")
    if len(key) < 16:
        return _refused_json("đường hệ thống chưa bật — thiếu KPI_SYS_KEY (≥ 16 ký tự)", 503)
    sent = request.headers.get(SYS_KEY_HEADER, "")
    if not hmac.compare_digest(sent.encode("utf-8"), key.encode("utf-8")):
        return _refused_json("thiếu hoặc sai khoá X-Kpi-Sys-Key", 401)
    return None


@app.get("/api/system/bonus-table")
def system_bonus_table(request: Request):
    """Bảng thưởng đầy đủ của bản tính đang dùng = ĐÚNG dữ liệu khối xuất file dùng (`export_report.report_data`) + sha256 của JSON chuẩn
    (khoá xếp, không khoảng trắng) ⇒ Người duyệt gắn mã băm khi duyệt, Xuất kiểm lại cùng mã ⇒ không duyệt bản này mà xuất bản khác."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    request = _for_version(request)
    if isinstance(request, JSONResponse):
        return request
    body = _bonus_table_body(request)
    if isinstance(body, dict) and request.app.state.name_entries:
        # KPI_NAMES=on (A + chốt 12/11): thêm `names` {mã che: {name, sm_code}} CẠNH `table` — hộp bật KPI_SYSTEM_ONLY không có
        # /api/names, mà khối kiểm ô bên Capitol cần nguồn của hệ thống để so từng ô Tên / Mã SM trong file xuất. Cùng bảng với /api/names và file
        # xuất (`name_entries` của đúng bản tính). NGOÀI `sha256` (mã băm bảng không đổi theo cờ); cờ off ⇒ không có khoá này, thân trả y cũ.
        body = {**body, "names": request.app.state.name_entries}
    return body

UNHASHED_SOURCE_ROWS = {"Bản tính số", "Tính lúc", "Trạng thái"}   # nhãn ở export_report.report_data — giữ một chỗ
def _bonus_table_body(request: Request) -> dict | JSONResponse:
    with store.connect(request.app.state.store) as conn:
        try:
            data = export_report.report_data(conn, request.app.state.run_id)
        except ValueError as error:
            return _refused_json(f"bảng thưởng không dùng được — {error}", 409)
    # Băm CHỈ SỐ + NGUỒN SỐ (dữ liệu, luật, code) ⇒ cùng số thì cùng mã qua MỌI lần bật (: không ràng buộc "duyệt + xuất cùng một lần bật"
    # máy hay khởi động lại, hộp hay dựng lại). 2 lần bật (store mới mỗi lần): chỉ dòng "Tính lúc" khác ⇒ bỏ. Bỏ thêm: số thứ tự bản tính
    # ("Bản tính số", run_id — bộ đếm của store, không phải số / nguồn), "Xuất file lúc" (giờ gọi), "Trạng thái" (đổi khi "Đã soát").
    # Giữ "Lúc chụp số liệu" (lấy từ file dữ liệu = nguồn). `cells` khoá (NV, KPI) ⇒ danh sách theo thứ tự NV × KPI. run_id / status trả NGOÀI mã băm.
    table = {"period": data["period"], "cutoff": data["cutoff"], "employees": data["employees"], "kpis": data["kpis"],
             "lines": [data["cells"][(nv, kpi)] for nv in data["employees"] for kpi in data["kpis"]],
             "salary": data["salary"], "kpi_total": data["kpi_total"], "team_total": data["team_total"],
             "source": [list(row) for row in data["source"] if str(row[0]) not in UNHASHED_SOURCE_ROWS and not str(row[0]).startswith("Xuất file lúc")]}
    table = json.loads(json.dumps(jsonable_encoder(table)))
    canonical = json.dumps(table, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"run_id": data["run_id"], "period": data["period"], "status": data["status"], "table": table,
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


KIEM_SO_FIELDS = {"answer", "question", "excel_b64", "excel_name", "tool_calls", "run_id", "org_id"}


@app.post("/api/system/kiem-so")
async def system_kiem_so(request: Request):
    """Rào chắn KIỂM BẰNG CODE (khối Rào chắn Capitol vá chế độ code gọi, sau khối Agent; trượt ⇒ dừng lượt) — lõi `agent/kiem_so.py`.
    Thân: answer (bắt buộc) · question · excel_b64 / excel_name · tool_calls [{name, args}] (nếu bên gọi biết — máy chủ CHẠY LẠI đúng đường agent).
    Không có tool_calls ⇒ nguồn số = `_team_sources` (máy chủ tự lấy). `tool_results` từ bên gọi KHÔNG nhận: nguồn số phải do hệ thống ghi.
    Ra: {dat, loi, ly_do, phien_ban} của kiem_so + bang_thuong + sha256_bang (như /api/system/bonus-table — 27 xin một lần gọi). Chỉ dat true là qua."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    request = _for_version(request)
    if isinstance(request, JSONResponse):
        return request
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    unknown = set(body) - KIEM_SO_FIELDS
    if unknown:
        return _refused_json(f"trường không nhận: {', '.join(sorted(unknown))} — nguồn số (tool_results) do hệ thống lấy, không nhận từ bên gọi", 422)
    if "run_id" in body and "tool_calls" in body:
        return _refused_json("gửi run_id HOẶC tool_calls, không cả hai", 422)
    payload = {key: body[key] for key in KIEM_SO_FIELDS - {"run_id", "org_id"} if key in body}
    if "run_id" in body:
        key = _run_key(body["run_id"], body.get("org_id"))
        if not key:
            return _kiem_so_fail("khong_co_lan_goi", "mã lượt / tổ chức sai dạng", request)
        if key not in request.app.state.run_calls and any(run == key[1] for _org, run in request.app.state.run_calls):
            return _kiem_so_fail("khong_co_lan_goi", "mã lượt này thuộc tổ chức khác", request)   # tổ chức khác ⇒ trượt sẵn
        calls = request.app.state.run_calls.get(key, [])
        if calls is None:
            return _kiem_so_fail("khong_co_lan_goi", f"lượt vượt {MAX_CALLS_PER_RUN} lần gọi công cụ — không kiểm được đủ nguồn", request)
        # Không có lần gọi (mã lạ, lượt chỉ có lời từ chối, backend đã bật lại) ⇒ nguồn RỖNG, KHÔNG trượt ngay: câu từ chối
        # đúng không gọi công cụ ("chưa có dữ liệu …", từ chối mã / kỳ) phải qua; câu có số vẫn trượt so_khong_nguon vì không có nguồn.
        payload["tool_results"] = [call["result"] for call in calls]
    elif "tool_calls" not in payload:
        payload["tool_results"] = _team_sources(request)
    result = kiem_so(payload, run_tool=lambda name, args: _tool_for_model(request, name, dict(args)),
                     context=context_numbers(_agent_context(request)),
                     # KPI_NAMES=on: bảng tên ĐỦ ĐỘI của đúng bản tính — rào đổi tên / mã SM trong câu trả lời sang mã che dù lượt không gọi công cụ cho người đó
                     name_entries=request.app.state.name_entries)
    table = _bonus_table_body(request)
    if isinstance(table, JSONResponse):
        return table   # bảng thưởng hỏng ⇒ 409, không trả "đạt" kèm bảng không dùng được
    return {**result, "bang_thuong": table["table"], "sha256_bang": table["sha256"]}


def _kiem_so_fail(kind: str, detail: str, request: Request):
    """Trượt trước khi tới lõi (cùng khuôn ra kiem_so + bảng thưởng) — không có nguồn số thì KHÔNG cho qua dù câu trả lời không có số."""
    table = _bonus_table_body(request)
    if isinstance(table, JSONResponse):
        return table
    return {"dat": False, "loi": [{"loai": kind, "chi_tiet": detail}], "ly_do": f"trượt — {detail}", "phien_ban": KIEM_SO_VERSION,
            "bang_thuong": table["table"], "sha256_bang": table["sha256"]}



# Người duyệt của bộ Capitol (10/11 theo -4e) — đường ghi CÓ KHOÁ HỆ THỐNG, bảng lưu riêng ----------
DUYET_FIELDS = {"run_id", "sha256_bang", "org_id", "nguoi_duyet"}
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


@app.post("/api/system/duyet")
async def system_duyet(request: Request):
    """Khối Người duyệt Capitol ghi "đã duyệt" bảng thưởng: thân {run_id, sha256_bang, org_id, nguoi_duyet}. Backend TỰ so sha256_bang với bảng
    của bản tính đang dùng (cùng mã băm /api/system/bonus-table) — lệch ⇒ 409, không ghi. Khớp ⇒ thêm một dòng `capitol_approval` (chỉ thêm).
    Không đổi "Đã soát" của trang User. `nguoi_duyet` là tên tự khai (Capitol tắt đăng nhập — giới hạn). `async` + SQLite đồng bộ: như /review."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    request = _for_version(request)
    if isinstance(request, JSONResponse):
        return request
    body = await _json_body(request)
    if isinstance(body, JSONResponse):
        return body
    if set(body) != DUYET_FIELDS:
        return _refused_json(f"cần đúng các trường: {', '.join(sorted(DUYET_FIELDS))}", 422)
    run_id, sha, org_id, reviewer = body["run_id"], body["sha256_bang"], body["org_id"], body["nguoi_duyet"]
    if isinstance(run_id, bool) or not isinstance(run_id, int) or not isinstance(sha, str) or not SHA256_HEX.fullmatch(sha) \
            or not isinstance(org_id, str) or not isinstance(reviewer, str):
        return _refused_json("run_id phải là số nguyên; sha256_bang 64 ký tự hex thường; org_id, nguoi_duyet là chuỗi chữ", 422)
    org_id, reviewer = _one_line(org_id), _one_line(reviewer)
    if not 1 <= len(reviewer) <= 60 or not 1 <= len(org_id) <= 100:
        return _refused_json("cần tên người duyệt (1–60 ký tự) và org_id (1–100 ký tự)", 422)
    if run_id != request.app.state.run_id:
        return _refused_json(f"bản tính {run_id} không phải bản đang dùng ({request.app.state.run_id}) — lấy lại bảng thưởng rồi duyệt", 409)
    table = _bonus_table_body(request)
    if isinstance(table, JSONResponse):
        return table
    if not hmac.compare_digest(sha, table["sha256"]):
        return _refused_json("mã băm không khớp bảng thưởng hiện tại — bảng đã đổi sau khi lấy, lấy lại bảng rồi duyệt", 409)
    with store.connect(request.app.state.store) as conn:
        row = store.record_capitol_approval(conn, run_id, sha, org_id, reviewer)
    return {"duyet": True, "run_id": run_id, "sha256_bang": sha, "org_id": org_id, "nguoi_duyet": reviewer, "luc": row["created_at"]}


# Thưởng thêm 01–06 + Meat 7.1–7.3 (thêm 11/11) cho Capitol — đường RIÊNG, KHÔNG thuộc bảng thưởng 8 KPI / mã băm bảng ----------
_extra_lock = threading.Lock()
# Bản dựng HOÀN TOÀN từ file tải lên (kỳ trống): khoản phụ không đọc file gắn sẵn của backend (đó là file của một tháng cụ thể) — nói rõ thiếu gì.
# Gia vị + hội viên: bản có sổ tải lên đọc nền từ CHÍNH sổ đó (`_own_book`) ⇒ tính được; thưởng thêm: cần trang chỉ tiêu tải lên (`targets_override`)
NO_UPLOADED_SOURCE = "{what} chưa tính được — kỳ này dựng từ file tải lên, còn thiếu {missing}"


@app.get("/api/system/extra-bonus")
def system_extra_bonus(request: Request):
    """Thưởng thêm mọi khoản trong file luật (01–06 + Meat 7.1–7.3 từ 11/11) × 14 NV (mã che) + tổng + nguồn luật — lõi `core/compute_extra_bonus` (luật `config/extra_bonus_<kỳ>.yaml`).
    Chỉ có ở workflow đường RAW (cần dòng đơn từng mã hàng). Tính sẵn lúc bật / lúc nạp file tải lên (`_extra_bonus`), giữ trong bộ nhớ đến khi bật lại."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    request = _for_version(request)
    if isinstance(request, JSONResponse):
        return request
    extra = _extra_bonus(request.app)
    if isinstance(extra, str):
        return _refused_json(f"thưởng thêm không tính được — {extra}", 409)
    # `gate_progress` (khoản điều kiện "07" cho kiểu xem của trang, 13/11) KHÔNG ra đường hệ thống: hình đường này bên Capitol đã duyệt — không đổi lặng lẽ
    return {key: value for key, value in extra.items() if key != "gate_progress"}
@app.get("/api/extra-bonus")
@_versioned
def extra_bonus(request: Request, employee_id: str | None = None, period: str | None = None) -> dict:
    """Công cụ 11 `get_extra_bonus` cho trợ lý chat (bậc (b) 11/11) — mã che, KHÔNG khoá (như /api/team-bonus); bản đủ cho Capitol vẫn là
    /api/system/extra-bonus (có khoá + nguồn luật). Lời từ chối trả 200 trong thân như các công cụ khác."""
    return extra_bonus_tool.get_extra_bonus(request.app.state.ctx, _extra_bonus(request.app), employee_id=employee_id, period=period)


# Tên + mã SM thật cạnh mã che — CHỈ cho giao diện trên máy:5183 ("Tên · mã SM thật · NV05", mẫu trang sếp) ----------
# Đường duy nhất trả TÊN THẬT: không đi qua agent / công cụ / kiem_so (kết quả công cụ vẫn 0 tên thật — test cũ giữ); không ghi tên vào log
# (lời lỗi chỉ nói thiếu gì, không nêu tên / mã thật); `Cache-Control: no-store` để trình duyệt không giữ bản tên.
@app.get("/api/names")
@_boot_version_only
def employee_names(request: Request):
    """{"names": {mã che NVxx: {"name": họ tên, "sm_code": mã SM thật}}} — mã che ⇒ mã thật từ bảng che của bản tính (`ctx.to_real`), tên từ file chỉ
    tiêu của workflow (như `_real_identity_tokens`).
    Thiếu tên / bảng che không khớp đội ⇒ 409, không trả nửa danh sách."""
    names = _name_entries(request.app)
    if isinstance(names, str):
        return _refused_json(names, 409)
    return JSONResponse({"names": names}, headers={"Cache-Control": "no-store"})


def _name_entries(app) -> dict[str, dict] | str:
    """{mã che: {"name": họ tên, "sm_code": mã SM thật}} của bản tính, hoặc lời lỗi (chuỗi — không chứa tên / mã thật). Một chỗ cho `/api/names`
    và bảng tên của công cụ khi `KPI_NAMES=on`."""
    from kpi_agent.core.load_data import load_targets
    try:
        team = load_targets(_workflow_files(app.state.workflow)[1])["employees"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return "không đọc được file chỉ tiêu (tên nhân viên)"
    names = {masked: {"name": str((team.get(real) or {}).get("name") or "").strip(), "sm_code": str(real)} for masked, real in app.state.ctx.to_real.items()}
    if not names or not all(entry["name"] and entry["sm_code"] for entry in names.values()):
        return "bảng mã che / tên thiếu nhân viên của đội"
    return dict(sorted(names.items()))


# Tab "Theo dõi" của giao diện trên máy (bản 2) — chỉ đọc, KHÔNG tính số KPI / tiền nào (backend/tracking_view.py) ----------
# File trang theo dõi của đội (trang sếp gửi) để đánh dấu ô lệch: chỉ trên máy, Git bỏ qua; không đặt ⇒ chỉ ghi lệch đã biết theo KPI / khoản
TRACKING_PAGE = os.getenv("KPI_TRACKING_PAGE", "")
_tracking_lock = threading.Lock()


@app.get("/api/tracking")
@_boot_version_only
def tracking(request: Request) -> dict:
    """Bảng đội + hạng + Thưởng thêm + ô lệch trang sếp (mã che) cho bản tính lúc bật. Tính một lần rồi giữ (so trang đọc lại RAW ≈ 10 s).
    Giới hạn: bản dữ liệu tải lên (`/api/system/upload`) chưa có tab này — tab hiện bản lúc bật."""
    app = request.app
    with _tracking_lock:
        cached = getattr(app.state, "tracking", None)
        if cached is None:
            ctx, extra = app.state.ctx, _extra_bonus(app)
            board = queries.team_board(ctx)
            if isinstance(extra, str):
                return queries._refused(f"thưởng thêm không tính được — {extra}")
            reasons = tracking_view.known_reasons(ctx.period)
            deviations, compared = {}, False
            if TRACKING_PAGE and Path(TRACKING_PAGE).exists():
                raw_path = _workflow_files(app.state.workflow)[2]
                try:
                    deviations = tracking_view.page_deviations(TRACKING_PAGE, raw_path, tracking_view.raw_actuals(ctx), extra, ctx.to_real, reasons)
                    compared = True
                except (ValueError, KeyError, OSError) as error:
                    # Trang không khớp bản này (tên không ghép được, khác đội) ⇒ vẫn hiện số, nói rõ không so được — không đoán ô lệch
                    reasons = {**reasons, "_page_error": f"không so được với trang theo dõi: {type(error).__name__}"}
            cached = tracking_view.tracking_data(board, extra, tracking_view.raw_measures(ctx), deviations, reasons, compared,
                                                 page_view=tracking_view.page_view(ctx, extra))
            app.state.tracking = cached
    return cached
# "1 % gia vị" (bậc 1 — backend/spice_loyalty.py, luật config/gia_vi_1pct_<kỳ>.yaml) — chỉ đọc, ngoài bảng thưởng 8 KPI ----------
_spice_lock = threading.Lock()
@app.get("/api/spice-loyalty")
def spice(request: Request) -> dict:
    """Thưởng 1 % gia vị gom theo NV phụ trách (mã che) + tổng đội — tiền trả cho KHÁCH, không cộng lương NV. 0 mã / tên khách.
    Chỉ đường RAW (cần dòng đơn từng nhóm hàng). Tính SẴN lúc bật (`_spice` trong lifespan) rồi giữ tới khi bật lại; lỗi dữ liệu ⇒ lời từ chối."""
    # Đường trang (/api/spice-loyalty, /api/spice-tool, /api/spice-customers — cả ba gọi hàm này) không qua `_for_version` ⇒ ?data_version= của bản
    # khác từng bị bỏ qua và trả số / khách của bản lúc bật ⇒ từ chối như đường hệ thống. Lời gọi đã qua `_for_version` (công cụ) mang
    # data_version của chính bản đó ⇒ không vướng dòng này; bản tải lên tự từ chối ở ô `spice` của nó.
    # 2a-2 nấc 1 (20/11): bản tải lên TỰ tính 1 % gia vị từ file của chính nó ⇒ gỡ rào "chỉ bản lúc bật"; điều kiện giữ cho rào cũ vẫn đúng: MỌI đường
    # gia vị đi qua đúng bản được hỏi (`_own_version`), ô giữ kết quả nằm trên state của TỪNG bản.
    request = _own_version(request)
    if isinstance(request, dict):
        return request
    return _spice(request.app)


UNKNOWN_VERSION = "không có bản dữ liệu này — tải file lại"


def _own_version(request: Request):
    """Request của ĐÚNG bản được hỏi cho các đường của trang / công cụ (trả lời từ chối trong thân, mã 200): ?data_version= thiếu / = bản lúc bật ⇒ request
    gốc; bản tải lên ⇒ request của bản đó; lạ ⇒ lời từ chối (dict). Request đã là của một bản (cửa ra công cụ) ⇒ giữ nguyên."""
    if isinstance(request, _VersionRequest):
        return request
    versioned = _for_version(request)
    return queries._refused(UNKNOWN_VERSION) if isinstance(versioned, JSONResponse) else versioned


def _spice(app) -> dict:
    """1 % gia vị của bản dữ liệu — tính một lần rồi giữ (kể cả lời từ chối), như `_extra_bonus`."""
    with _spice_lock:
        cached = getattr(app.state, "spice", None)
        if cached is None:
            cached = (queries._refused(NO_UPLOADED_SOURCE.format(what="1 % gia vị", missing="nền gia vị (chưa có đường tải)"))
                      if getattr(app.state, "uploads_only", False) and _own_book(app.state.workflow) is None else _compute_spice(app))
            app.state.spice = cached
    return cached


@app.get("/api/spice-tool")
def spice_for_chat(request: Request, employee_id: str | None = None, period: str | None = None) -> dict:
    """Công cụ 12 `get_spice_loyalty` cho trợ lý chat — hình RIÊNG cho mô hình (không có khoá `employee_id` ở dòng nào: tiền này trả cho
    KHÁCH — xem `backend/spice_tool.py`); đường của trang `/api/spice-loyalty` giữ nguyên hình. Lời từ chối trả 200 trong thân như các công cụ khác.
    """
    request = _own_version(request)
    if isinstance(request, dict):
        return request
    return spice_tool.get_spice_loyalty(request.app.state.ctx, spice(request), employee_id=employee_id, period=period)


@app.get("/api/spice-customers")
def spice_customers(request: Request, employee_id: str | None = None) -> dict:
    """Từng khách của chương trình 1 % gia vị (cả đội hoặc một NV): nền, doanh số, % nền, thưởng / còn cần bao nhiêu để tới ngưỡng. CÓ tên + mã khách ⇒
    đường chỉ cho trang (không vào công cụ AI, không vào danh sách đường của cổng xem; KPI_SYSTEM_ONLY ⇒ 404). Dùng lại đúng các dòng `/api/spice-loyalty` đã tính."""
    request = _own_version(request)
    if isinstance(request, dict):
        return request
    ctx = request.app.state.ctx
    if employee_id is not None:
        _real, refusal = queries._check_employee(ctx, employee_id)
        if refusal:
            return queries._refused(refusal)
        employee_id = employee_id.strip().upper()
    summary = spice(request)                       # tính (hoặc lấy bản đã giữ) — cũng điền app.state.spice_customers
    if "refused" in summary:
        return summary
    rows = [row for row in request.app.state.spice_customers["rows"] if employee_id in (None, row["employee_id"])]
    return {"period": ctx.period, "cutoff": ctx.cutoff.isoformat(), "employee_id": employee_id, "rule_text": summary["rule_text"],
            "customers": len(rows), "groups": spice_loyalty.CUSTOMER_GROUPS, "rows": rows, "notes": request.app.state.spice_customers["notes"]}


# Tab "Chi tiết" (điểm bán thuộc tuyến) + "Trà 365" (backend/outlets.py) — chỉ đọc, CHỈ ĐẾM, ngoài bảng thưởng ----------
# Hai đường trả TÊN + ĐỊA CHỈ cửa hàng (dữ liệu khách) cho trang xem ⇒ KHÔNG thêm vào công cụ của AI / backend_client, không có bản /api/system/…
# (cờ KPI_SYSTEM_ONLY bật ⇒ 404 như mọi đường của trang). Tính ở lần gọi đầu (đọc lại RAW ≈ 15 s) rồi giữ tới khi bật lại, như tab Theo dõi.
_outlets_lock = threading.Lock()


def _outlet_rows(app) -> dict:
    with _outlets_lock:
        cached = getattr(app.state, "outlets", None)
        if cached is None:
            cached = _compute_outlets(app)
            app.state.outlets = cached
    return cached


def _outlets_answer(request: Request, employee_id: str | None, view, notes: list[str], page_notes: bool = False) -> dict:
    ctx = request.app.state.ctx
    if employee_id is not None:
        _real, refusal = queries._check_employee(ctx, employee_id)
        if refusal:
            return queries._refused(refusal)
        employee_id = employee_id.strip().upper()
    data = _outlet_rows(request.app)
    if "refused" in data:
        return data
    team = sorted(ctx.to_real)
    page_total = tracking_view.outlet_sales_page_total(ctx.period) if page_notes else None
    if page_total is not None:
        # So TỔNG cả đội với số của trang (file lệch đã biết): cộng các số ĐÃ LÀM TRÒN TỚI ĐỒNG như trên thẻ (trang cũng cộng số nguyên từng điểm) ⇒ lệch còn lại ghi đúng số đồng
        mine = sum(round(row["page_sales"]) for row in data["rows"])
        kpi_way = round(outlets.outlets_view(data["rows"], team)["summary"]["sales"])
        gap, dot = mine - page_total, lambda n: f"{n:,}".replace(",", ".")
        notes = notes + [f"Tổng doanh số 'như trang' của các điểm bán thuộc tuyến cả đội: ở đây {dot(mine)} đ, trang theo dõi của đội ghi {dot(page_total)} đ"
                         + (" — khớp từng đồng." if gap == 0 else f" — lệch {dot(abs(gap))} đ do làm tròn từng điểm bán.")
                         + f" Cùng các điểm đó, doanh số tính KPI 1 (trước chiết khấu) là {dot(kpi_way)} đ."]
    return {"period": ctx.period, "cutoff": ctx.cutoff.isoformat(), "employee_id": employee_id, **view(data["rows"], team, employee_id),
            "notes": notes + data["notes"], "source": data["source"]}


@app.get("/api/outlets")
@_boot_version_only
def outlet_list(request: Request, employee_id: str | None = None) -> dict:
    """Điểm bán thuộc tuyến của cả đội (hoặc một NV): doanh số tháng, đạt / chưa đạt MOQ, Trà 365, Homey, kênh On, nước mắm cao cấp. Chỉ đường RAW."""
    return _outlets_answer(request, employee_id, outlets.outlets_view, [
        "Đạt MOQ = điểm bán có ít nhất một mặt hàng đủ số lượng mua tối thiểu trong tháng (cùng cách đếm KPI 4).",
        "Tiền trên thẻ 'như trang theo dõi của đội' = doanh số SAU chiết khấu, CỘNG CẢ hoá đơn xuất sau 22:00 ngày 02 tháng sau (công văn 6.1 loại; KPI 1 cộng như trang); "
        "Trà 365 chỉ cộng dòng đủ số lượng mua tối thiểu; chỉ tính đơn của đúng nhân viên phụ trách tuyến.",
        "Dòng 'Doanh số tính KPI 1' trên thẻ = trước chiết khấu (gồm hoá đơn xuất trễ đã có ngày xuất, như KPI 1) — số dùng cho thưởng, khác số của trang. Tiền trên thẻ làm tròn tới đồng."], page_notes=True)


@app.get("/api/tea-365")
@_boot_version_only
def tea_365(request: Request, employee_id: str | None = None) -> dict:
    """Điểm bán thuộc tuyến mua từ 1 thùng Trà 365 cộng dồn trong tháng. CHỈ ĐẾM — tiền theo công văn 419 chưa dựng."""
    return _outlets_answer(request, employee_id, outlets.tea_view, [
        "Chỉ đếm — chưa tính tiền Trà 365 theo công văn 419.",
        "Số thùng = cộng mọi dòng đơn Trà 365 của điểm bán trong tháng, kể cả dòng chưa đủ số lượng mua tối thiểu.",
        "Tổng thùng chỉ cộng các điểm bán mua từ 1 thùng."])


def _raw_read_args(workflow: dict, ctx) -> dict:
    """Bậc 2 bước 2.5 (17/11): luật kỳ (trạng thái nào cần ngày xuất HĐ) + bảng ghép cột của bản đang chạy — MỌI đường đọc RAW ngoài lõi (Thưởng thêm,
    thẻ Chi tiết, 1 % gia vị, xem đơn) truyền cùng hai thứ này như khối read_raw ⇒ cùng tập dòng với bản tính (trước đây bỏ cả hai: file đã ghép cột
    đọc sai cột, kỳ có Shipped chưa xuất HĐ ra tập dòng khác lõi)."""
    from kpi_agent.core.load_raw import period_rules
    from kpi_agent.workflow.blocks import raw_options
    # + luật kỳ của bộ đọc (bậc 3a: trạng thái được tính, hạn hoá đơn — `period_rules`); bản tính đã chạy qua cùng luật này nên không lỗi ở đây
    return {"invoice_required_status": raw_options(ctx.rules)["invoice_required_status"],
            "mapping": runner.node_param(workflow, "read_raw", "ghep"), "period_rules": period_rules(ctx.rules["assumptions"])}


def _routes_of(workflow: dict) -> dict:
    """Bảng tuyến của một bản dữ liệu — đúng hàm khối read_raw dùng (`blocks.routes_of`): file JSON cố định, hoặc sổ theo dõi đã tải + công thức đọc (2a-1).
    Mọi màn cần tuyến đi qua đây; đọc riêng `load_mcp_json(mcp_path)` hỏng với bản lấy tuyến từ sổ. Sổ không đọc được ⇒ ValueError (lời chỉ có mã lỗi)."""
    from kpi_agent.workflow.blocks import BlockStop, routes_of
    # ô lấy qua `runner.node_param` như mọi chỗ khác đọc đường file của khối (một lối đọc ô — file tuyến thiếu ⇒ cùng một lời "thiếu file")
    params = {key: runner.node_param(workflow, "read_raw", key) for key in ("mcp_path", "mcp_ghep", "mcp_doi")}
    try:
        return routes_of(params)
    except BlockStop as stop:
        raise ValueError(str(stop)) from None


def _compute_outlets(app) -> dict:
    from kpi_agent.core.compute_extra_bonus import load_extra_rules
    from kpi_agent.core.load_raw import read_raw_lines
    workflow, ctx = app.state.workflow, app.state.ctx
    if runner.data_block_type(workflow) != "read_raw":
        return queries._refused("danh sách điểm bán chỉ có ở workflow đường RAW DATA (cần dòng đơn từng cửa hàng)")
    # Nhóm "nước mắm cao cấp" lấy đúng danh sách của khoản Thưởng 03 (một chỗ ghi nhóm hàng — không chép lại)
    rules_path = REPO_ROOT / f"kpi_agent/config/extra_bonus_{ctx.period.replace('-', '_')}.yaml"
    if not rules_path.exists():
        return queries._refused(f"chưa có file luật thưởng thêm cho kỳ {ctx.period} (danh sách nhóm nước mắm cao cấp nằm ở đó)")
    raw_path = mcp_path = None
    try:
        fish_sauce = load_extra_rules(rules_path)["items"]["T03"]["dms_obj"]
        raw_path = runner.repo_path(runner.node_param(workflow, "read_raw", "path"))
        mcp_path = runner.repo_path(runner.node_param(workflow, "read_raw", "mcp_path"))
        mcp = _routes_of(workflow)
        read_args = _raw_read_args(workflow, ctx)
        lines, _late = read_raw_lines(raw_path, ctx.cutoff, ctx.period, extra_columns=outlets.EXTRA_COLUMNS, **read_args)
        # Dòng hoá đơn trễ: tiền "như trang" trên thẻ cộng vào (dòng 13 bài 15b); KPI 1 cộng dòng có ngày xuất (mốc 7 N2) ⇒ đọc riêng, chỉ khi có dòng trễ
        late_lines = outlets.read_late_lines(raw_path, ctx.cutoff, ctx.period, **read_args) if _late else []
        late_in_kpi1 = "Turnover" in ctx.rules["assumptions"].get("late_invoice_kpis", ())   # mốc 7 N2: KPI 1 cộng dòng trễ có ngày xuất
        rows = outlets.compute(lines, mcp, {real: masked for masked, real in ctx.to_real.items()}, read_args["period_rules"]["focus"], fish_sauce, late_lines,
                               late_in_kpi1)
    except OSError:
        # Không chép chuỗi lỗi của hệ điều hành (có đường dẫn tuyệt đối) — chỉ nêu tên file tương đối đang thiếu
        missing = [_shown_path(path) for path in (raw_path, mcp_path) if path is not None and not Path(path).is_file()]
        return queries._refused("danh sách điểm bán không tính được — " + (f"thiếu file {', '.join(missing)}" if missing else "không đọc được file dữ liệu"))
    except KeyError:
        return queries._refused("danh sách điểm bán không tính được — file luật / file tuyến thiếu mục cần đọc")
    except ValueError as error:
        # Lời lỗi của module chỉ có số dòng, tên cột, tên nhóm hàng, số đếm — không tên / mã cửa hàng, không đường dẫn
        return queries._refused(f"danh sách điểm bán không tính được — {error}")
    # Điểm thuộc tuyến chưa có đơn ⇒ tên + địa chỉ lấy từ sheet "MCP" của sổ Tracking của kỳ (đường file sổ: chỗ duy nhất đang ghi là luật 1 % gia vị).
    # Không đọc được sổ ⇒ giữ "chưa có tên" như trước (tên chỉ để hiện, không đổi số đếm nào).
    filled = 0
    spice_rules = REPO_ROOT / f"kpi_agent/config/gia_vi_1pct_{ctx.period.replace('-', '_')}.yaml"
    if spice_rules.exists():
        try:
            filled = outlets.fill_names(rows, outlets.read_route_names(REPO_ROOT / spice_loyalty.load_rules(spice_rules)["baseline"]["file"]))
        except (OSError, ValueError, KeyError):
            filled = 0
    without_name = sum(row["outlet_name"] is None for row in rows)
    notes = [f"{without_name} điểm bán thuộc tuyến chưa có đơn nào trong tháng nên chưa có tên và địa chỉ (tên lấy từ file đơn hàng)."] if without_name else []
    if filled:
        notes.append(f"{filled} điểm bán thuộc tuyến chưa có đơn nào trong tháng: tên và địa chỉ lấy từ danh sách tuyến trong sổ theo dõi của đội (không phải từ file đơn hàng).")
    return {"rows": rows, "notes": notes,
            "source": {"raw_sha256": app.state.files_sha256.get(raw_path.name), "route_file": mcp_path.name, "route_file_sha256": _sha256(mcp_path)}}


# Tab "Hội viên" — PHẦN DOANH SỐ (backend/membership.py): khách hội viên, nền, doanh số, nhóm theo doanh số ----------
# Trả TÊN + MÃ khách ⇒ đường chỉ cho trang: không vào công cụ AI, không vào danh sách đường của cổng xem; KPI_SYSTEM_ONLY ⇒ 404.
# CHƯA có hạng / nhiệm vụ / tiền (thiếu dữ liệu + công văn 408). Đọc sổ + file đơn hàng ở lần gọi đầu rồi giữ tới khi bật lại.
_membership_lock = threading.Lock()


def _has_own_column_mapping(workflow: dict) -> bool:
    """File đơn hàng của bản có sheet / dòng tiêu đề / cột KHÁC mẫu không. Bảng ghép chỉ thêm ngành được cho qua trên bố cục mẫu ⇒ không tính là khác."""
    from kpi_agent.core import khuon
    mapping = runner.node_param(workflow, "read_raw", "ghep")
    if mapping is None:
        return False
    own, default = khuon.normalize_mapping({**mapping, "table": "don_hang"}), khuon.normalize_mapping({**khuon.default_mapping("don_hang"), "table": "don_hang"})
    return any(own.get(key) != default.get(key) for key in ("sheet", "header_row", "columns"))


def _compute_membership(app) -> dict:
    from kpi_agent.core.load_raw import raw_employee_names
    workflow, ctx = app.state.workflow, app.state.ctx
    if getattr(app.state, "uploads_only", False) and _own_book(workflow) is None:
        return queries._refused(NO_UPLOADED_SOURCE.format(what="danh sách hội viên", missing="nền hội viên (chưa có đường tải)"))
    if runner.data_block_type(workflow) != "read_raw":
        return queries._refused("danh sách hội viên chỉ có ở workflow đường RAW DATA (cần dòng đơn từng cửa hàng)")
    source_path = REPO_ROOT / f"kpi_agent/config/hoi_vien_{ctx.period.replace('-', '_')}.yaml"
    if not source_path.exists():
        return queries._refused(f"chưa có file nguồn hội viên cho kỳ {ctx.period}")
    raw_path = book_path = None
    try:
        spec = membership.load_source(source_path)
        if spec["period"] != ctx.period:
            # file nguồn ghi cứng sổ nền của MỘT kỳ ⇒ bản của kỳ khác không được lặng lẽ dùng nền đó (như 1 % gia vị)
            return queries._refused(f"file nguồn hội viên ghi kỳ {queries._short(spec['period'])}, không phải kỳ {ctx.period} của bản dữ liệu")
        if _has_own_column_mapping(workflow):
            # phần hội viên đọc file đơn hàng theo TÊN CỘT của mẫu, chưa qua bảng ghép cột ⇒ bản tải lên có cách ghép riêng: từ chối, không đọc nhầm cột
            return queries._refused("danh sách hội viên chưa đọc được file đơn hàng có cách ghép cột riêng — chỉ có cho file đúng mẫu")
        raw_path, book_path = _workflow_files(workflow)[2], REPO_ROOT / _on_own_book(spec, "workbook", workflow)["workbook"]
        members = membership.read_members(book_path, spec)
        codes = {full: code for code, full in raw_employee_names(str(raw_path)).items()}
        from kpi_agent.core.load_raw import period_rules
        counted_status = period_rules(ctx.rules["assumptions"])["counted_status"]      # bậc 3a: trạng thái được tính theo luật kỳ
        rows, skipped = membership.compute(members, membership.sales_after_discount(raw_path, ctx.cutoff, counted_status=counted_status), codes,
                                           {real: masked for masked, real in ctx.to_real.items()})
    except OSError:
        # Không chép chuỗi lỗi của hệ điều hành (có đường dẫn tuyệt đối) — chỉ nêu tên file tương đối đang thiếu
        missing = [_shown_path(path) for path in (raw_path, book_path) if path is not None and not Path(path).is_file()]
        return queries._refused("danh sách hội viên không tính được — " + (f"thiếu file {', '.join(missing)}" if missing else "không đọc được file dữ liệu"))
    except (ValueError, KeyError) as error:
        # Lời lỗi của module chỉ có tên sheet, tên cột, số dòng — không tên / mã khách, không đường dẫn
        return queries._refused(f"danh sách hội viên không tính được — {error}")
    notes = ["Doanh số = sau chiết khấu, mọi đơn của khách tới ngày chốt, tính cả hoá đơn xuất trễ (cách của trang theo dõi của đội — khác KPI 1).",
             "Đủ doanh số ≥ nền KHÔNG có nghĩa là đã đạt chương trình: trang của đội còn xét nhiệm vụ Duy trì / Mở mới theo hạng khách."]
    if skipped:
        notes.append(f"{skipped} khách trong sổ có nhân viên phụ trách không ghép được vào đội — không tính.")
    return {"rows": rows, "notes": notes}


@app.get("/api/membership")
def membership_list(request: Request, employee_id: str | None = None) -> dict:
    """Khách hội viên của cả đội (hoặc một NV): nền, doanh số sau chiết khấu, % nền, còn lại, nhóm theo doanh số. Chỉ đường RAW. Chưa có hạng / tiền."""
    # m6: đường này không qua `_for_version` ⇒ ?data_version= của bản khác từng trả số hội viên của bản LÚC BẬT ⇒ từ chối như gia vị
    request = _own_version(request)     # 2a-2 nấc 1: bản tải lên có danh sách hội viên của CHÍNH nó (ô giữ `membership` nằm trên state của từng bản)
    if isinstance(request, dict):
        return request
    ctx = request.app.state.ctx
    if employee_id is not None:
        _real, refusal = queries._check_employee(ctx, employee_id)
        if refusal:
            return queries._refused(refusal)
        employee_id = employee_id.strip().upper()
    with _membership_lock:
        data = getattr(request.app.state, "membership", None)
        if data is None:
            data = _compute_membership(request.app)
            request.app.state.membership = data
    if "refused" in data:
        return data
    return {"period": ctx.period, "cutoff": ctx.cutoff.isoformat(), "employee_id": employee_id,
            **membership.view(data["rows"], sorted(ctx.to_real), employee_id), "notes": data["notes"]}


# "Đơn hàng của một nhân viên" (backend/order_lines.py): đơn / cửa hàng nào được tính cho một chỉ tiêu, vì sao không ----------
# Bản luật THỨ HAI đi song song lõi ⇒ RÀO: số "được tính" phải bằng số thực hiện của lõi, lệch thì từ chối (không hiện danh sách sai).
# Trả tên cửa hàng cho trang xem ⇒ không vào công cụ AI; KPI_SYSTEM_ONLY ⇒ 404; chưa vào danh sách đường của cổng xem (scripts/xuat_duong_get.py).
_order_lines_lock = threading.Lock()


def _order_lines_data(app) -> dict:
    with _order_lines_lock:
        cached = getattr(app.state, "order_lines", None)
        if cached is None:
            cached = _load_order_lines(app)
            app.state.order_lines = cached
    return cached


def _load_order_lines(app) -> dict:
    from kpi_agent.core.load_raw import read_raw_lines
    workflow, ctx = app.state.workflow, app.state.ctx
    if runner.data_block_type(workflow) != "read_raw":
        return queries._refused("danh sách đơn chỉ có ở workflow đường RAW DATA (cần dòng đơn từng cửa hàng)")
    raw_path = mcp_path = None
    try:
        raw_path = runner.repo_path(runner.node_param(workflow, "read_raw", "path"))
        mcp_path = runner.repo_path(runner.node_param(workflow, "read_raw", "mcp_path"))
        read_args = _raw_read_args(workflow, ctx)
        lines = order_lines.read_all_lines(raw_path, ctx.cutoff, ctx.period, **read_args)
        kept_by_core = len(read_raw_lines(raw_path, ctx.cutoff, ctx.period, **read_args)[0])
        mcp = _routes_of(workflow)
    except OSError:
        missing = [_shown_path(path) for path in (raw_path, mcp_path) if path is not None and not Path(path).is_file()]
        return queries._refused("danh sách đơn không đọc được — " + (f"thiếu file {', '.join(missing)}" if missing else "không đọc được file dữ liệu"))
    except (ValueError, KeyError) as error:
        return queries._refused(f"danh sách đơn không đọc được — {error if isinstance(error, ValueError) else 'file dữ liệu thiếu mục cần đọc'}")
    if sum(line["_drop"] is None for line in lines) != kept_by_core:
        # bộ lọc dòng ở đây đã lệch bộ lọc của lõi (ai đó đổi core/load_raw.py mà chưa sửa order_lines.py) ⇒ không hiện gì
        return queries._refused("danh sách đơn chưa đối chiếu được với bảng thưởng (bộ lọc dòng lệch bản tính) — báo người phụ trách")
    by_employee = defaultdict(list)
    for line in lines:
        by_employee[line["SM CODE"]].append(line)
    return {"by_employee": by_employee, "mcp": mcp}


@app.get("/api/order-lines")
@_boot_version_only
def employee_order_lines(request: Request, employee_id: str | None = None, kpi: str | None = None) -> dict:
    """Đơn (hoặc cửa hàng) của một nhân viên cho một chỉ tiêu, kèm được tính / không và lý do. Chỉ đường RAW; chỉ các chỉ tiêu đã làm (bậc 1: 4 chỉ tiêu)."""
    ctx = request.app.state.ctx
    real, refusal = queries._check_employee(ctx, employee_id)
    if refusal:
        return queries._refused(refusal)
    name, refusal = queries._check_kpi(kpi)
    if refusal:
        return queries._refused(refusal)
    if name not in order_lines.SUPPORTED:
        return queries._refused(f"chưa có danh sách đơn cho chỉ tiêu {name} — hiện có: {', '.join(order_lines.SUPPORTED)}")
    data = _order_lines_data(request.app)
    if "refused" in data:
        return data
    try:
        result = order_lines.build(name, data["by_employee"].get(real, []), data["mcp"].get(real, {}), ctx.rules["assumptions"])
    except ValueError as error:   # luật đang dùng khác luật bản này viết (vd LPPC tính cả dòng tặng) ⇒ từ chối, lời không có dữ liệu khách
        return queries._refused(f"danh sách đơn của chỉ tiêu {name} chưa dựng cho luật đang dùng — {error}")
    core = ctx.results[real][name]
    core_actual = core.actual
    if name in order_lines.RATIO_KPIS and (result["summary"]["lines"], result["summary"]["orders"]) != (core.numerator, core.denominator):
        # LPPC: so cả tử (số mã) lẫn mẫu (số đơn) với lõi — tỷ lệ bằng nhau mà tử / mẫu khác vẫn là đếm sai
        return queries._refused(f"danh sách đơn của chỉ tiêu {name} chưa đối chiếu được với bảng thưởng — chưa hiện để tránh sai; báo người phụ trách")
    if not order_lines.matches_core(name, result["counted_value"], core_actual):
        return queries._refused(f"danh sách đơn của chỉ tiêu {name} chưa đối chiếu được với bảng thưởng — chưa hiện để tránh sai; báo người phụ trách")
    return {"period": ctx.period, "cutoff": ctx.cutoff.isoformat(), "employee_id": employee_id.strip().upper(), "kpi": name, "unit": result["unit"],
            "summary": {**result["summary"], "counted_value": result["counted_value"], "core_actual": core_actual, "matches_core": True},
            "rows": result["rows"], "notes": [order_lines.NOTE, order_lines.SAME_DAY_NOTE]}

# Tab "Đánh giá" (backend/evaluations.py) — chỉ đọc file đã dựng sẵn, không đo, không gọi AI ----------
EVALUATIONS_FILE = REPO_ROOT / "kpi_agent/data/danh_gia.json"   # do scripts/dung_so_danh_gia.py ghi (Git bỏ qua)


@app.get("/api/evaluations")
def evaluations() -> dict:
    """Bảng đánh giá từng phiên bản (số đã đo + nguồn từng số + nhãn giữ / quay lại). Chưa dựng / file hỏng ⇒ lời từ chối rõ, không sập."""
    try:
        return json.loads(EVALUATIONS_FILE.read_text(encoding="utf-8"))
    except OSError:
        # lời trung tính, KHÔNG nêu lệnh chạy script: gói chạy trong hộp không có thư mục scripts/ — cách dựng ghi ở config/so_danh_gia.yaml
        return queries._refused("chưa có file số đánh giá dựng sẵn")
    except ValueError:
        return queries._refused("file số đánh giá dựng sẵn bị hỏng — cần dựng lại")


# Hai đường hệ thống thêm cho hộp Capitol: khi KPI_SYSTEM_ONLY bật, /api/team-board và /api/spice-loyalty bị đóng ----------
@app.get("/api/system/team-board")
def system_team_board(request: Request):
    """Bảng đội (như /api/team-board của trang) — có khoá hệ thống, theo đúng bản dữ liệu (`data_version`)."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    request = _for_version(request)
    if isinstance(request, JSONResponse):
        return request
    return team_board(request)


@app.get("/api/system/spice-loyalty")
def system_spice_loyalty(request: Request):
    """1 % gia vị (như /api/spice-loyalty) — có khoá hệ thống. ?data_version= ⇒ số của ĐÚNG bản đó (bản tải lên tự tính từ file của nó); bản lạ ⇒ 404;
    bản không tính được ⇒ 409."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    request = _for_version(request)     # bản lạ ⇒ 404 như mọi đường hệ thống; bản tải lên ⇒ số của CHÍNH bản đó (2a-2 nấc 1)
    if isinstance(request, JSONResponse):
        return request
    result = spice(request)
    # Đường hệ thống: không tính được ⇒ 409 (như /api/system/extra-bonus) để bên gọi bằng máy không nhận 200 mà không có số. Đường của trang
    # (/api/spice-loyalty) GIỮ 200 + "refused": trang chỉ hiện lời từ chối khi mã trả là 200 (`dashboard/src/api.ts` readJson ném lỗi chung nếu khác 200)
    return _refused_json(result["refused"], 409) if "refused" in result else result


def _spice_page_totals() -> dict | None:
    """Dòng "Tổng" của tab 1 % gia vị trên TRANG của đội (nền, doanh số, tỷ lệ) — chỉ để hiện cạnh số của mình; không có trang / trang khác hình ⇒ None."""
    if not (TRACKING_PAGE and Path(TRACKING_PAGE).exists()):
        return None
    try:
        total = tracking_view.page_data(TRACKING_PAGE)["gvt"]
        return {"baseline": total[1], "sales": total[2], "pct": float(queries.floor_pct(total[3]))}
    except (ValueError, KeyError, IndexError, TypeError, OSError):
        return None


def _own_book(workflow: dict) -> Path | None:
    """Sổ theo dõi người dùng ĐÃ TẢI LÊN cho bản này (bản lấy tuyến từ sổ — 2a-1); bản dùng file tuyến cố định ⇒ None."""
    if runner.data_block_type(workflow) != "read_raw" or runner.node_param(workflow, "read_raw", "mcp_ghep") is None:
        return None
    return runner.repo_path(runner.node_param(workflow, "read_raw", "mcp_path"))


def _on_own_book(spec: dict, key: str, workflow: dict) -> dict:
    """2a-2 nấc 2: bản có sổ tải lên ⇒ nền 1 % gia vị / danh sách hội viên đọc từ CHÍNH sổ đó (cùng sheet, dòng tiêu đề, cột ghi trong file luật của kỳ),
    không đọc sổ ghi cứng trong file luật — tháng sau người dùng tải sổ tháng sau là đủ. Bản không có sổ tải lên ⇒ giữ đường ghi trong file luật."""
    book = _own_book(workflow)
    return spec if book is None else {**spec, key: str(book)}


def _compute_spice(app) -> dict:
    from kpi_agent.core.load_raw import raw_employee_names, read_raw_lines
    workflow, ctx = app.state.workflow, app.state.ctx
    if runner.data_block_type(workflow) != "read_raw":
        return queries._refused("1 % gia vị chỉ có ở workflow đường RAW DATA (cần dòng đơn từng nhóm hàng)")
    rules_path = REPO_ROOT / f"kpi_agent/config/gia_vi_1pct_{ctx.period.replace('-', '_')}.yaml"
    if not rules_path.exists():
        return queries._refused(f"chưa có file luật 1 % gia vị cho kỳ {ctx.period}")
    rules = raw_path = None
    try:
        rules = spice_loyalty.load_rules(rules_path)
        if rules["period"] != ctx.period:
            # file luật ghi cứng sổ nền của MỘT kỳ ⇒ bản của kỳ khác không được lặng lẽ dùng nền đó
            return queries._refused(f"file luật 1 % gia vị ghi kỳ {queries._short(rules['period'])}, không phải kỳ {ctx.period} của bản dữ liệu")
        raw_path = _workflow_files(workflow)[2]
        read_args = _raw_read_args(workflow, ctx)
        lines, _late = read_raw_lines(raw_path, ctx.cutoff, rules["period"], extra_columns=(rules["sales_column"],), **read_args)
        codes = {full: code for code, full in raw_employee_names(str(raw_path), read_args["mapping"]).items()}
        baseline = _on_own_book(rules["baseline"], "file", workflow)
        rows = spice_loyalty.compute(lines, rules, spice_loyalty.read_baseline(REPO_ROOT, baseline), codes)
        summary = spice_loyalty.by_employee(rows, {real: masked for masked, real in ctx.to_real.items()})
        # Danh sách từng khách cho TRANG (13/11) — cùng các dòng vừa tính; tên + ngày viếng thăm đọc thêm từ sổ, không đọc được thì danh sách vẫn có, thiếu tên
        to_masked = {real: masked for masked, real in ctx.to_real.items()}
        try:
            info, info_note = spice_loyalty.read_customer_info(REPO_ROOT, baseline), []
        except ValueError as error:
            info, info_note = {}, [f"Chưa có tên khách và ngày viếng thăm — {error}."]
        app.state.spice_customers = {"rows": spice_loyalty.customer_rows(rows, rules, info, to_masked), "notes": info_note + [
            "Số ở đây cộng đủ đồng; trang theo dõi của đội hiện nền và doanh số của từng khách đã làm tròn tới 10.000 đ.",
            "Có thưởng khi doanh số gia vị đạt từ 110 % nền; \"cần thêm\" = doanh số còn thiếu để tới ngưỡng đó."]}
    except OSError:
        # 12/11: thiếu sổ nền từng trả nguyên chuỗi lỗi hệ thống — đường dẫn TUYỆT ĐỐI trên máy + "[Errno 2]" — ra lời trả.
        # Nay chỉ nêu tên file tương đối của các file khoản này đọc; không chép chuỗi lỗi của hệ điều hành.
        files = [raw_path] + ([REPO_ROOT / _on_own_book(rules["baseline"], "file", workflow)["file"]] if isinstance(rules, dict) and "baseline" in rules else [])
        missing = [_shown_path(path) for path in files if path is not None and not Path(path).is_file()]
        return queries._refused("1 % gia vị không tính được — " + (f"thiếu file {', '.join(missing)}" if missing else "không đọc được file dữ liệu"))
    except (ValueError, KeyError) as error:
        # Lời lỗi của module không chứa tên / mã thật, không chứa đường dẫn (chỉ số dòng, tên cột, tên sheet, số đếm)
        column = rules.get("sales_column") if isinstance(rules, dict) else None
        if column and str(error).startswith("RAW thiếu cột") and column in str(error):
            # file đơn hàng được nhận (cột này không thuộc cột bắt buộc của 8 KPI) nhưng khoản gia vị cần nó — lời thường, chỉ việc phải làm
            return queries._refused(f"Chưa tính được 1 % gia vị: file đơn hàng thiếu cột doanh số sau chiết khấu (tên cột mẫu: {column}) — "
                                    "đổi tên cột trong file cho đúng rồi tải lại")
        return queries._refused(f"1 % gia vị không tính được — {error}")
    return {"period": ctx.period, "cutoff": ctx.cutoff.isoformat(), "rule_text": rules.get("rule_text"), **summary,
            # 4 ô tổng như trang theo dõi của đội (chỉ thêm khoá). Ô "page" = số TRANG CỦA SẾP hiện cạnh (đáp án để so bằng mắt, không vào phép
            # tính): chỉ gắn cho bản lúc bật — trang là số chốt của đúng bản đó; bản tải lên ⇒ None
            "page_totals": {**spice_loyalty.page_totals(rows, rules), "page": None if isinstance(app.state, _VersionState) or getattr(app.state, "uploads_only", False) else _spice_page_totals()},
            # bản chưa hết kỳ (ngày chốt trước ngày cuối kỳ): doanh số mới tới ngày chốt mà nền là của cả tháng ⇒ nói rõ là tạm tính. Kỳ đã hết ⇒ không có khoá
            **({"provisional_note": f"tạm tính tới ngày chốt {ctx.cutoff:%d/%m/%Y} — chưa phải thưởng cuối kỳ"} if ctx.cutoff < upload.last_day(ctx.period) else {}),
            "paid_to": "khách (điểm bán) — không cộng vào lương 8 KPI / Thưởng thêm của NV",
            "source": {"rules_file": rules_path.name, "rules_sha256": _sha256(rules_path), "baseline_sheet": rules["baseline"]["sheet"],
                       "baseline_from": "so_tai_len" if _own_book(workflow) is not None else "file_co_dinh"}}


def _extra_bonus(app) -> dict | str:
    """Thưởng thêm của bản dữ liệu (tính SẴN lúc bật / lúc nạp file tải lên — đọc lại RAW ≈ 15 s — rồi giữ đến khi backend bật lại), hoặc lời lỗi (chuỗi).
    Một chỗ cho đường hệ thống, công cụ 11 và nguồn số của Rào chắn ⇒ ba đường cùng một bộ số."""
    with _extra_lock:
        cached = getattr(app.state, "extra_bonus", None)
        if cached is None and getattr(app.state, "uploads_only", False) and getattr(app.state, "targets_override", None) is None:
            cached = "Chưa có chỉ tiêu Kokomi / Meat — hãy tải trang theo dõi (.html) của kỳ này"   # lời thường: chỉ việc phải làm
            app.state.extra_bonus = cached
        if cached is None:
            try:
                cached = _compute_extra_bonus(app)
            except Exception as error:   # noqa: BLE001 — mọi lỗi thành LỜI (không 500), nhưng chỉ lời do code viết mới ra cửa (`_extra_bonus_reason`)
                # Luật / dữ liệu sai ⇒ không có số (lỗi nặng ⇒ dừng); lời lỗi của lõi không chứa mã NV thật / tên.
                # GIỮ cả lời lỗi (bản 2): không giữ thì mỗi lần gọi (cả nguồn số của Rào chắn) lại đọc RAW ≈ 15 s rồi hết giờ; sửa file ⇒ bật lại / tải lại
                cached = _extra_bonus_reason(error)
            app.state.extra_bonus = cached
    return cached


EXTRA_BONUS_UNREADABLE = "không đọc được dữ liệu để tính thưởng thêm (mã lỗi: thuong_them_loi_doc)"


def _extra_bonus_reason(error: Exception) -> str:
    """Lời lỗi của đường thưởng thêm đi ra cửa (409 hệ thống, lời từ chối của công cụ, phản hồi tải lên): chỉ giữ lời do CODE của dự án viết (ValueError
    của lõi / của bộ đọc tuyến). Lỗi của Python / hệ điều hành (giải mã chữ, JSON hỏng, thiếu file, thiếu khoá…) mang chữ thô, đường dẫn tuyệt đối hay
    nội dung file ⇒ lời chung + mã cố định. Ca thật 20/11: file tuyến là .xlsx bị đọc như JSON ⇒ "'utf-8' codec can't decode byte…" từng ra cửa."""
    if isinstance(error, ValueError) and not isinstance(error, (UnicodeError, json.JSONDecodeError)):
        return str(error)
    return EXTRA_BONUS_UNREADABLE


def _compute_extra_bonus(app) -> dict:
    from kpi_agent.core.compute_extra_bonus import compute_extra_bonus, load_extra_rules, load_item_targets
    from kpi_agent.core.load_raw import read_raw_lines
    workflow, ctx = app.state.workflow, app.state.ctx
    if runner.data_block_type(workflow) != "read_raw":
        raise ValueError("chỉ có ở workflow đường RAW DATA (workflow đang chạy đọc sổ Tracking — không có dòng đơn từng mã hàng)")
    rules_path = REPO_ROOT / f"kpi_agent/config/extra_bonus_{ctx.period.replace('-', '_')}.yaml"
    if not rules_path.exists():
        raise ValueError(f"chưa có file luật thưởng thêm cho kỳ {ctx.period}")
    rules = load_extra_rules(rules_path)
    raw_path = runner.repo_path(runner.node_param(workflow, "read_raw", "path"))
    mcp = _routes_of(workflow)
    # keep_late (mốc 7 N2): dòng hoá đơn trễ có ngày xuất mang cờ LATE — lõi chỉ cộng ở T06 + cổng 07 (`include_late_invoice` trong file luật)
    lines, _late = read_raw_lines(raw_path, ctx.cutoff, rules["period"], extra_columns=("TOTAL CASES", "TOTAL PIECES"), keep_late=True,
                                  **_raw_read_args(workflow, ctx))
    # chỉ tiêu riêng từng NV: T06 Kokomi + Meat 7.1–7.3 (cổng TPCB). Bản có trang theo dõi tải lên (2a-2 nấc 3) đọc file đã tách của CHÍNH bản đó
    own_targets = getattr(app.state, "targets_override", None) or {}
    item_targets = load_item_targets(rules, resolve=lambda name: Path(own_targets[name]) if name in own_targets else runner.repo_path(name))
    to_masked = {real: masked for masked, real in ctx.to_real.items()}
    result = compute_extra_bonus(lines, rules, sorted(to_masked), mcp, item_targets)
    items = list(rules["items"])
    out_lines = [{"employee_id": to_masked[code], "item": line.item, "actual": line.actual, "target": line.target, "reached": line.reached,
                  "amount": line.amount, "how": line.how} for code in sorted(result, key=to_masked.get) for line in result[code]]
    employee_total = {nv: sum(l["amount"] for l in out_lines if l["employee_id"] == nv) for nv in sorted(to_masked.values())}
    # "07 Ngành TPCB" (13/11 — trang theo dõi của đội có khoản này, chỉ %, không tiền): doanh số cả ngành ÷ chỉ tiêu 07 của từng người. Lõi dùng đúng số
    # này làm ĐIỀU KIỆN cho 7.1–7.3 nhưng không trả ra thành dòng (lõi nằm trong dấu vân tay ⇒ không sửa) ⇒ cộng lại ở đây bằng CÙNG hàm cộng doanh số
    # + cùng bảng chỉ tiêu + cùng cách bỏ hàng tặng của lõi. Không có tiền, không đổi tổng nào.
    gate_progress = None
    gated = next((item for item in items if rules["items"][item].get("gate")), None)
    if gated is not None:
        from kpi_agent.core.compute_extra_bonus import gate_sales_by_employee
        gate = rules["items"][gated]["gate"]
        # cùng hàm với lõi (gồm cả luật cộng dòng hoá đơn trễ của cổng) ⇒ ô "07" không thể lệch điều kiện lõi dùng, không cộng trễ hai lần
        gate_sales = gate_sales_by_employee([line for line in lines if line["FREE ITEM FLAG"] != "Y"], gate)
        gate_progress = {"id": gate["targets_key"], "before": gated, "sub_div": gate["sub_div"],
                         "lines": [{"employee_id": to_masked[code], "actual": round(gate_sales.get(code, 0), 4), "target": item_targets[gated]["gate"][code]}
                                   for code in sorted(to_masked, key=to_masked.get)]}
    item_total = {item: sum(l["amount"] for l in out_lines if l["item"] == item) for item in items}
    return {"period": ctx.period, "cutoff": ctx.cutoff.isoformat(),
            "items": [{"id": item, "title": rules["items"][item]["title"], "label": rules["items"][item]["label"],
                       "measure": rules["items"][item]["measure"], "aso_rule": rules["items"][item].get("aso_rule"),
                       "limit_note": rules["items"][item].get("limit_note")} for item in items],
            "lines": out_lines, "employee_total": employee_total, "item_total": item_total, "team_total": sum(item_total.values()),
            "gate_progress": gate_progress,
            "source": {"rules_file": rules_path.name, "rules_sha256": _sha256(rules_path), "raw_sha256": app.state.files_sha256.get(raw_path.name),
                       "kokomi_targets_sha256": _sha256(runner.repo_path(rules["items"]["T06"]["targets_file"])),
                       "targets_sha256": {Path(file).name: _sha256(runner.repo_path(file)) for file in
                                          sorted({item["targets_file"] for item in rules["items"].values() if item.get("targets_file")})}},
            "note": "Thưởng thêm TÁCH khỏi bảng thưởng 8 KPI (không cộng vào team_total / sha256 của /api/system/bonus-table); "
                    "luật lấy từ chữ in trên sổ theo dõi của đội; công văn số 21 không có các khoản này — xem file luật."}



# Bản dữ liệu từ file Excel tải lên ----------
# Mỗi lần tải = một BẢN DỮ LIỆU (data_version) mới; bản cũ giữ. Bản tính của file tải lên ghi vào file SQLite RIÊNG (`<store>_uploads.sqlite`):
# ghi chung file với bản lúc bật thì `store.record_run` coi là "bản mới thay bản cũ" ⇒ bản đang dùng của trang User bị đánh "đã bị thay"
# (mất "Đã soát") và lần bật sau không dùng lại được bản cũ. Các đường hệ thống + công cụ nhận ?data_version= (thiếu ⇒ bản lúc bật).
MAX_VERSIONS = 10
UPLOAD_DIR = REPO_ROOT / "kpi_agent/data/uploads"     # mặc định khi không đặt KPI_UPLOAD_DIR (lifespan đọc)


def _extra_files_sha256(workflow: dict, period: str, own_targets: dict | None = None) -> dict[str, str]:
    """{đường file tính từ gốc repo: sha256} của các file quyết định số NGOÀI bảng 8 KPI: luật + chỉ tiêu riêng của Thưởng thêm, luật + sổ nền của
    1 % gia vị, nguồn + sổ của tab Hội viên (`hoi_vien_<kỳ>.yaml` + sổ Tracking nó trỏ — số doanh số hội viên trên trang). Chỉ đường RAW có các
    khoản này; đường khác ⇒ {}.

    13/11: các file này không nằm trong `files_sha256` ⇒ đổi file chỉ tiêu Kokomi thì tổng Thưởng thêm đổi (19.026.300 ⇒ 12.526.300, ca chạy thật)
    mà `data_version` đứng yên — bên theo dõi "vân tay" không biết số đã đổi. Cố ý KHÔNG gộp vào `files_sha256`: danh sách đó là dấu vân tay của
    bản tính 8 KPI, thêm vào là mất dấu "Đã soát" dù 8 KPI không đổi đồng nào. File thiếu / hỏng ⇒ ghi "không có file", không ném lỗi (khoản đó tự
    báo lỗi ở đường của nó)."""
    if runner.data_block_type(workflow) != "read_raw":
        return {}
    suffix = period.replace("-", "_")
    extra_rules, spice_rules, members_source = (f"kpi_agent/config/extra_bonus_{suffix}.yaml", f"kpi_agent/config/gia_vi_1pct_{suffix}.yaml",
                                                f"kpi_agent/config/hoi_vien_{suffix}.yaml")
    files, from_book = [extra_rules, spice_rules, members_source], set()
    for rules_file, pick in ((extra_rules, lambda rules: [item.get("targets_file") for item in rules.get("items", {}).values()]),
                             (spice_rules, lambda rules: [rules.get("baseline", {}).get("file")]),
                             (members_source, lambda rules: [rules.get("workbook")])):
        try:
            rules = yaml.safe_load(runner.repo_path(rules_file).read_text(encoding="utf-8"))
            found = [name for name in pick(rules) if isinstance(name, str)]
            files += [name for name in found if name not in files]
            if rules_file != extra_rules:
                from_book.update(found)      # sổ nền 1 % gia vị + sổ hội viên: bản có sổ tải lên đọc từ chính sổ đó (nấc 2)
        except (OSError, yaml.YAMLError, AttributeError):
            continue   # file luật thiếu / không đọc được ⇒ sha của chính nó đã là "không có file" hoặc đổi; không biết file con thì thôi
    # `own_targets` (2a-2 nấc 3): bản có trang tải lên ⇒ băm file chỉ tiêu đã tách của bản dưới ĐÚNG tên file cố định nó thay ⇒ đổi trang thì mã bản đổi
    own_targets, book = own_targets or {}, _own_book(workflow)
    path_of = lambda name: Path(own_targets[name]) if name in own_targets else book if book is not None and name in from_book else runner.repo_path(name)
    return {name: _sha256_or_missing(path_of(name)) for name in files}


def _data_version(files_sha256: dict, code_sha: str, cutoff: date, extra_files_sha256: dict | None = None, mapping_sha: str | None = None) -> str:
    """sha256(sha NỘI DUNG mọi file đầu vào — luật, chỉ tiêu, dữ liệu theo thứ tự vai rồi file khác như danh sách tuyến (`_files_sha256`) —, dấu vân tay code, ngày chốt) — không dùng tên file (file tải lên lưu theo sha) ⇒
    tải lại đúng file đang chạy lúc bật ra đúng bản lúc bật. + sha các file phụ (`_extra_files_sha256`, 13/11) khi có — không có ⇒ công thức cũ."""
    parts = {"files": list(files_sha256.values()), "code": code_sha, "cutoff": cutoff.isoformat()}
    if extra_files_sha256:
        parts["extra_files"] = list(extra_files_sha256.values())
    if mapping_sha:
        # bậc 2: cùng file mà ghép cột khác ⇒ số khác ⇒ bản khác ("Đã soát" / duyệt Capitol không mang sang). Không ghép riêng ⇒ công thức cũ
        parts["mapping"] = mapping_sha
    text = json.dumps(parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _VersionState:
    """`app.state` của MỘT bản dữ liệu: thuộc tính riêng của bản (ctx, run_id, workflow, file lưu…) đè lên state chung (khoá, nhật ký lượt…)."""
    def __init__(self, base, **own):
        object.__setattr__(self, "_base", base)
        self.__dict__.update(own)

    def __getattr__(self, name):
        return getattr(self._base, name)


class _VersionApp:
    def __init__(self, state):
        self.state = state


class _VersionRequest:
    """Request đi qua đúng bản dữ liệu: mọi hàm đọc `request.app.state.ctx / run_id / store …` dùng bản đó mà không phải sửa từng hàm."""
    def __init__(self, request: Request, state: _VersionState):
        self._request, self.app = request, _VersionApp(state)

    def __getattr__(self, name):
        return getattr(self._request, name)


def _for_version(request: Request):
    """?data_version= ⇒ request của bản đó; thiếu / = bản lúc bật ⇒ request gốc; lạ ⇒ 404 (không liệt kê bản nào đang có)."""
    wanted = request.query_params.get("data_version")
    if wanted in (None, "") and getattr(request.app.state, "uploads_only", False):
        # Kỳ dựng HOÀN TOÀN từ file tải lên: không gửi mã bản ⇒ bản MỚI NHẤT của kỳ. Bản đầu (đơn hàng + chỉ tiêu + sổ) chưa có thưởng thêm; tải
        # trang chỉ tiêu ra bản mới — trang / khung chat hỏi không kèm mã bản mà vẫn rơi về bản đầu thì không bao giờ thấy khoản đó.
        latest = request.app.state.versions.get(request.app.state.latest_data_version)
        if latest is not None:
            return _VersionRequest(request, latest)
    if wanted in (None, "", request.app.state.data_version):
        return request
    state = request.app.state.versions.get(wanted)
    if state is None:
        return _refused_json("không có bản dữ liệu này — tải file lại", 404)
    return _VersionRequest(request, state)


UPLOAD_QUERY = {"loai", "ky", "ngay_chot", "data_version"}   # bỏ ten_file (C rà bảo mật U8: query vào log truy cập)
@app.post("/api/system/upload")
async def system_upload(request: Request):
    """Thân = byte file .xlsx (application/octet-stream); query loai = raw | tracking | chi_tieu · ky (vd 2026-10) · ngay_chot (YYYY-MM-DD, hệ thống điền; BẮT BUỘC với tracking và raw — xem dưới) · data_version (bản làm nền — thiếu ⇒ bản lúc bật). Kiểm (upload.py) ⇒ lưu theo sha ⇒ tính bằng lõi cố định
    ⇒ {data_version, run_id, cutoff, files_sha256, data_flags, team_total, gioi_han}. Kokomi + tuyến (MCP) vẫn là file cố định của workflow."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    params = request.query_params
    # ngay_chot bắt buộc với file có ngày (tracking, raw) — không đoán. Tracking thiếu ⇒ ngày làm mới pivot của sổ ra bản bất ngờ. RAW thiếu từng
    # lấy ngày đơn mới nhất (30/10, thứ Sáu) ≠ ngày chốt lúc bật 31/10 ⇒ cùng file mà công cụ báo "còn 1 ngày làm việc", 23 dòng "cần thêm …
    # mỗi ngày", mất "kỳ đã hết" (16/11, tiền không đổi). Không lấy ngày chốt lúc bật làm mặc định: file tải giữa tháng sẽ nhận nhầm ngày.
    if set(params) - UPLOAD_QUERY or params.get("loai") not in upload.KINDS or not params.get("ky") \
            or (params.get("loai") in ("tracking", "raw") and not params.get("ngay_chot")):
        return await _drained(request, _refused_json(
            "query: loai = raw | tracking | chi_tieu | so_tracking | trang_chi_tieu, ky (vd 2026-10), ngay_chot YYYY-MM-DD (bắt buộc với raw và tracking — hệ thống điền "
            "ngày chốt, không đoán), data_version (không bắt buộc)", 422))
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
        return await _drained(request, _refused_json("chỉ nhận thân application/octet-stream (byte file .xlsx)", 415))
    # Một lượt tải một lúc (C rà bảo mật U9 — máy 15 GB từng OOM): đọc thân + kiểm + tính đều trong khoá; lượt thứ hai ⇒ 409 ngay, không xếp hàng
    if not _upload_busy.acquire(blocking=False):
        return await _drained(request, _refused_json("đang xử lý một file khác — thử lại sau", 409, "dang_ban"))
    try:
        return await _upload_locked(request, params)
    finally:
        _upload_busy.release()


_upload_busy = threading.Lock()


async def _drained(request: Request, refusal: JSONResponse) -> JSONResponse:
    """Đọc BỎ thân (≤ 20 MB, không giữ trong bộ nhớ) trước khi trả lời từ chối — M2: trả lời khi client còn đang gửi ⇒ urllib báo
    "Connection reset" thay vì đọc được 422. Thân > 20 MB (theo Content-Length) hoặc không khai ⇒ trả ngay như cũ (không đọc thân lớn).
    """
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) <= upload.MAX_FILE_BYTES:
        read = 0
        async for chunk in request.stream():
            read += len(chunk)
            if read > upload.MAX_FILE_BYTES:
                break
    return refusal


async def _read_body(request: Request) -> bytes | JSONResponse:
    """Thân file ≤ 20 MB, kiểm Content-Length TRƯỚC khi đọc — dùng chung cho /api/system/upload và /api/system/bo-cuc (bậc 2, 17/11)."""
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        return _refused_json("cần Content-Length", 411)
    if int(length) > upload.MAX_FILE_BYTES:
        return _refused_json(f"file lớn hơn {upload.MAX_FILE_BYTES // (1024 * 1024)} MB", 413)   # chặn TRƯỚC khi đọc thân
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > upload.MAX_FILE_BYTES:
            return _refused_json(f"file lớn hơn {upload.MAX_FILE_BYTES // (1024 * 1024)} MB", 413)
    return bytes(body)


async def _upload_locked(request: Request, params):
    body = await _read_body(request)
    if isinstance(body, JSONResponse):
        return body
    try:   # thân đã đọc hết ở trên ⇒ trả lời từ đây không cắt kết nối
        wanted_cutoff = date.fromisoformat(params["ngay_chot"]) if params.get("ngay_chot") else None
    except ValueError:
        return _refused_json("ngay_chot phải dạng YYYY-MM-DD", 422)
    if not request.app.state.period_ready:           # kỳ trống: file vào bộ chờ; đủ ba file ⇒ bản đầu của kỳ
        try:
            return await run_in_threadpool(_receive_for_empty_period, request.app, params["loai"], params["ky"], body, wanted_cutoff)
        except upload.UploadRefused as refusal:
            return _refused_json(refusal.reason, refusal.status, refusal.code)
    base = _for_version(request)
    if isinstance(base, JSONResponse):
        return base
    try:
        return await run_in_threadpool(_build_version, request.app, base.app.state, params["loai"], params["ky"], body, wanted_cutoff)
    except upload.UploadRefused as refusal:
        return _refused_json(refusal.reason, refusal.status, refusal.code)


# ---------- Kỳ này đã có / thiếu gì (trang Dữ liệu của bộ Capitol) — CHỈ ĐỌC trạng thái đang giữ + tên file, không mở file dữ liệu, không tính ----------
def _period_status(request: Request, period: str) -> dict:
    # Hai đường /api/system/ky… không nhận ?data_version= ⇒ request.app.state luôn là state GỐC (bản lúc bật + `versions`) — một nguồn cho cả hai
    return period_status.period_status(request.app.state, period, rules_file_for_period=_rules_file_for_period, extra_files_sha256=_extra_files_sha256,
                                       sha256_or_missing=_sha256_or_missing, shown_path=_shown_path)

@app.get("/api/system/ky")
def system_periods(request: Request):
    """Các kỳ backend biết (có bản dữ liệu hoặc có file luật chính), mỗi kỳ kèm `ready`."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    if not request.app.state.period_ready:
        return {"periods": [{"period": request.app.state.empty_period, "ready": False}], "restored_uploads": request.app.state.restore_report,
                "cho_du_file": _empty_status(request.app)}
    periods = period_status.known_periods(request.app.state, PERIOD_RULES_DIR)
    return {"periods": [{"period": period, "ready": _period_status(request, period)["ready"]} for period in periods],
            # 2j: lần bật này có nạp lại bản tải lên không (`enabled`), nạp được bao nhiêu, bản nào đổi mã, bản nào bị bỏ qua và vì sao (file ghi
            # 8 ký tự đầu mã băm)
            "restored_uploads": request.app.state.restore_report}


# `{period:path}`: nhận cả chuỗi có "/" để TỰ trả 422 cho mọi giá trị sai dạng (vd "../x") — không để bộ định tuyến trả 404 khó hiểu.
@app.get("/api/system/ky/{period:path}")
def system_period_status(request: Request, period: str):
    """Kỳ `period` (YYYY-MM) đã có / thiếu gì: file đơn hàng, chỉ tiêu, tuyến, luật chính, luật phụ. Không mã / tên nhân viên, không số theo người;
    số tiền duy nhất là BA TỔNG của một bản (`_money_summary`, 20/11 — cho trang Dữ liệu của nền tảng)."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    if not period_status.valid_period(period):
        return _refused_json("kỳ phải có dạng YYYY-MM (vd 2026-10)", 422)
    if not request.app.state.period_ready:
        if period != request.app.state.empty_period:
            return _refused_json(f"backend đang mở kỳ {request.app.state.empty_period} — chưa có gì về kỳ {period}", 404)
        return _empty_status(request.app)
    # trang Dữ liệu của nền tảng không tới được /api/meta ⇒ mã bản mới nhất nằm luôn ở đây
    body = {**_period_status(request, period), "latest_data_version": request.app.state.latest_data_version}
    # ?data_version= (thiếu ⇒ bản mặc định như mọi đường): BA TỔNG của bản đó cho trang Dữ liệu — trang không cầm khoá của ba đường tiền và không nên
    # nhận bảng theo người. Chỉ khi bản thuộc đúng kỳ được hỏi.
    versioned = _for_version(request)
    if isinstance(versioned, JSONResponse):
        return versioned
    state = versioned.app.state
    return {**body, **(_money_summary(state) if state.ctx.period == period else {})}


# Bậc 2 nạp dữ liệu chung — bước 2.1: lược đồ trường chuẩn + đọc bố cục — CHỈ ĐỌC, không tính ----------
@app.get("/api/system/khuon/{table}")
def standard_schema(request: Request, table: str) -> dict:
    """Trường chuẩn của một bảng (tên, nghĩa, kiểu, bắt buộc, cột mặc định) — trang màn ghép vẽ cột trái từ đây, không chép tay (M1)."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    from kpi_agent.core import khuon
    try:
        return {"table": table, "fields": khuon.schema(table)}
    except ValueError:
        return _refused_json(f"chưa có khuôn cho bảng này — có: {', '.join(khuon.SCHEMAS)}", 404)


@app.post("/api/system/bo-cuc")
async def read_upload_layout(request: Request):
    """Thân = byte .xlsx (octet-stream, cùng rào /api/system/upload: khoá hệ thống, 20 MB, gói zip / macro, một lượt một lúc); query `bang` = don_hang.
    ⇒ {sha_file, sheet, header_row, columns (tên lạ bị che — U1), fingerprint, known, missing_required}. File lưu theo sha ở `<thư mục tải lên>/cho_ghep/`
    để bước ghép thử (2.3) gọi theo sha, không gửi lại file. KHÔNG tính KPI, không đổi bản dữ liệu nào."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    from kpi_agent.core import khuon
    params = request.query_params
    manual = (params.get("sheet_so"), params.get("dong_tieu_de"))
    if set(params) - {"bang", "sheet_so", "dong_tieu_de"} or params.get("bang") not in khuon.SCHEMAS \
            or (any(manual) and not all(value and value.isdigit() for value in manual)):
        return await _drained(request, _refused_json(f"query: bang = {' | '.join(khuon.SCHEMAS)}; sheet_so + dong_tieu_de (số, đi cùng nhau — khi máy "
                                                     "không đoán được dòng tiêu đề)", 422))
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
        return await _drained(request, _refused_json("chỉ nhận thân application/octet-stream (byte file .xlsx)", 415))
    if not _upload_busy.acquire(blocking=False):
        return await _drained(request, _refused_json("đang xử lý một file khác — thử lại sau", 409, "dang_ban"))
    try:
        body = await _read_body(request)
        if isinstance(body, JSONResponse):
            return body
        return await run_in_threadpool(_read_layout_of, request.app, params["bang"], body,
                                       *((int(manual[0]), int(manual[1])) if all(manual) else (None, None)))
    except upload.UploadRefused as refusal:
        return _refused_json(refusal.reason, refusal.status)
    finally:
        _upload_busy.release()


def _read_layout_of(app, table: str, body: bytes, sheet_number: int | None = None, header_row: int | None = None) -> dict:
    from kpi_agent.core import khuon
    upload.check_package(body)
    sha = hashlib.sha256(body).hexdigest()
    folder = app.state.upload_dir / "cho_ghep"
    try:
        temp = upload.write_temp(body, folder)
    except OSError:
        raise upload.UploadRefused("thư mục lưu file tải lên không ghi được (kiểm KPI_UPLOAD_DIR) — chưa nhận file", 503) from None
    try:
        try:
            layout = khuon.read_layout(temp, table, sheet_number, header_row)
        except ValueError as error:
            # lời của read_layout chỉ có chữ do code viết (không tên cột / sheet của file) ⇒ trả nguyên; lỗi đọc gói khác ⇒ lời chung (U1)
            text = str(error)
            raise upload.UploadRefused(text + (" — gửi lại kèm sheet_so + dong_tieu_de" if "không nhận ra dòng tiêu đề" in text else "")
                                       if "không nhận ra dòng tiêu đề" in text or "không có sheet / dòng tiêu đề" in text
                                       else "file không đọc được bảng tính") from None
        except Exception:   # noqa: BLE001 — gói hỏng / XML lạ ⇒ 422, không 500 (như upload — C rà U3)
            raise upload.UploadRefused("file không đọc được bảng tính") from None
        upload.check_room(sha, folder)
        upload.finalize(temp, folder, sha)
        return {"sha_file": sha, **layout}
    finally:
        temp.unlink(missing_ok=True)


class _RouteBook(Exception):
    """Dấu rẽ nhánh nội bộ của `_build_from_file` cho loại `so_tracking` (không phải lỗi)."""


MAPPING_CHECK_TTL_SECONDS = 1800   # câu kiểm của một lượt ghép thử sống 30 phút, trả lời được MỘT lần


@app.post("/api/system/ghep-thu")
async def try_mapping(request: Request):
    """Bậc 2 bước 2.3 — ghép thử, KHÔNG tính KPI, KHÔNG lưu: JSON {sha_file (từ /bo-cuc), bang, ghep, ky, ngay_chot}. Đọc file đã lưu theo sha
    QUA bảng ghép bằng đúng bộ nạp của lõi + luật kỳ ⇒ {ok, tom_tat_cot, doi_soat | chan, cau_kiem {ma, cau}}. Câu kiểm hỏi VỊ TRÍ cột doanh số trước
    chiết khấu trong file — người phải mở file gốc để trả lời; đáp án backend giữ (M3: đáp án ở trang thì bấm quán tính vẫn qua).
    `tom_tat_cot`: kiểu / % trống / số giá trị / ≤ 3 mẫu đã che của từng cột — `_column_summary`.
    """
    problem = _system_key_problem(request)
    if problem:
        return problem
    try:
        body = await request.json()
    except ValueError:
        return _refused_json("thân phải là JSON {sha_file, bang, ghep, ky, ngay_chot}", 422)
    if not isinstance(body, dict) or set(body) - {"sha_file", "bang", "ghep", "ky", "ngay_chot", "khong_co_cot_sau_ck"} \
            or not isinstance(body.get("khong_co_cot_sau_ck", False), bool):
        return _refused_json("thân phải là JSON {sha_file, bang, ghep, ky, ngay_chot, khong_co_cot_sau_ck (true / false, tuỳ chọn)}", 422)
    if not _upload_busy.acquire(blocking=False):
        return _refused_json("đang xử lý một file khác — thử lại sau", 409, "dang_ban")
    try:
        return await run_in_threadpool(_try_mapping, request.app, body)
    except upload.UploadRefused as refusal:
        return _refused_json(refusal.reason, refusal.status)
    finally:
        _upload_busy.release()


def _try_mapping(app, body: dict) -> dict:
    import secrets
    import time
    from kpi_agent.core import khuon
    from kpi_agent.core.load_raw import load_raw, period_rules
    from kpi_agent.core.rules import load_rules
    from kpi_agent.workflow.blocks import raw_options
    sha = body.get("sha_file")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise upload.UploadRefused("sha_file phải là sha256 (64 ký tự hex) do /api/system/bo-cuc trả")
    path = app.state.upload_dir / "cho_ghep" / f"{sha}.xlsx"
    if not path.is_file():
        raise upload.UploadRefused("chưa có file này — gửi file qua /api/system/bo-cuc trước", 404)
    if body.get("bang") not in khuon.SCHEMAS:
        raise upload.UploadRefused(f"bang = {' | '.join(khuon.SCHEMAS)}")
    if body["bang"] == "tuyen":
        return _try_route_formula(app, body, sha, path)
    try:
        mapping = khuon.normalize_mapping({**(body.get("ghep") or {}), "table": body["bang"]})
        cutoff = date.fromisoformat(str(body.get("ngay_chot")))
    except ValueError as error:
        raise upload.UploadRefused(str(error) if str(error).startswith("bảng ghép sai khuôn") else "ngay_chot phải dạng YYYY-MM-DD") from None
    if not isinstance(body.get("ky"), str) or not re.fullmatch(r"\d{4}-\d{2}", body["ky"]) or f"{cutoff:%Y-%m}" != body["ky"]:
        raise upload.UploadRefused("ky phải dạng YYYY-MM và ngay_chot nằm trong kỳ đó")
    rules = load_rules(runner.repo_path(_rules_file_for_period(app.state.workflow, body["ky"])))   # 2.6: luật của kỳ file, thiếu ⇒ 409
    fingerprint = khuon.layout_fingerprint(mapping["sheet"], mapping["header_row"], _header_of(path, mapping))
    summary = _column_summary(path, mapping)
    from kpi_agent.core.load_raw import read_raw_lines
    to_mask = _mask_for_text(app)
    # Bước 2.4: giá trị chưa biết ⇒ trả MÃ CHE (moi_1…) + số dòng, không trả chữ (U1); ngành mới người cho qua ở ghep-xac-nhan
    new_values = []
    try:
        result = load_raw(path, cutoff, rules["period"], **raw_options(rules), mapping=mapping, period_rules=period_rules(rules["assumptions"]))
    except ValueError as error:
        text = str(error)
        if "giá trị chưa biết" in text:
            new_values = _new_values_of(path, rules["period"], mapping)
        # lời do code viết (cổng đối soát, giá trị chưa biết, sai khuôn ghép) ⇒ trả nguyên (đã che mã NV); lời loader khác có thể lặp chữ file ⇒ lọc (U1)
        # + kiểm nghĩa (bước 2.4): lời chỉ có tên trường + số dòng, do code viết
        # + luật kỳ thiếu / sai khoá (bậc 3a): lời chỉ có tên khoá + giá trị của file luật, không có chữ của file tải lên
        safe = RECONCILE_STOP in text or "giá trị chưa biết" in text or text.startswith(("bảng ghép", "RAW sai nghĩa cột", "file luật kỳ", "luật kỳ nêu"))
        reply = {"ok": False, "van_tay_bo_cuc": fingerprint, "tom_tat_cot": summary, "chan": runner.mask_codes(text, to_mask) if safe else upload.safe_reason("raw", error),
                 "gia_tri_moi": [{"truong": item["field"], "ma": item["code"], "so_dong": item["lines"]} for item in new_values], "cau_kiem": None}
        # chỉ còn NGÀNH mới chặn ⇒ vẫn đặt câu kiểm để người CHO QUA (đọc file với các ngành đó coi như đã biết)
        if not new_values or any(item["field"] != "sub_div" for item in new_values):
            return reply
        mapping = {**mapping, "accepted_values": {"sub_div": sorted(item["value"] for item in new_values)}}
        try:
            result = load_raw(path, cutoff, rules["period"], **raw_options(rules), mapping=mapping, period_rules=period_rules(rules["assumptions"]))
        except ValueError as again:
            reply["chan"] = runner.mask_codes(str(again), to_mask) if RECONCILE_STOP in str(again) else upload.safe_reason("raw", again)
            return reply
    lines, _late = read_raw_lines(path, cutoff, rules["period"], invoice_required_status=(), mapping=mapping,
                                  period_rules=period_rules(rules["assumptions"]))
    # Câu kiểm hỏi dữ kiện NGOÀI cách ghép (C M1 vòng 1: hỏi giá trị ô của CHÍNH cột đã ghép ⇒ ghép nhầm vẫn qua): cột doanh số TRƯỚC chiết khấu là
    # cột thứ mấy trong file (A = 1). Đáp án = vị trí cột đã ghép — người đã ghép nhầm sẽ thấy lệch khi nhìn lại dòng tiêu đề của file.
    header = _header_of(path, mapping)
    column = mapping["columns"]["sales_before_discount"]
    answer = column if isinstance(column, int) else [str(name).strip() if name is not None else None for name in header].index(column) + 1
    # M1-r (MAJOR 17/11): không ghép cột sau CK ⇒ không kiểm được nghĩa cột tiền. File còn cột nghi là cột tiền bị bỏ sót ⇒ DỪNG, trừ khi
    # người khai rõ `khong_co_cot_sau_ck` (rồi ghi lý do ở ghi_chu lúc xác nhận). Hai lời cảnh báo KHÁC nhau: file một cột tiền ↔ người bỏ trống.
    warnings, needs_note = [], False
    if not (lines and "Sales After Discount" in lines[0]):
        suspects = _unmapped_money_suspects(path, mapping)
        where = ", ".join(f"cột thứ {n}" for n in suspects)
        if suspects and not body.get("khong_co_cot_sau_ck"):
            return {"ok": False, "van_tay_bo_cuc": fingerprint, "tom_tat_cot": summary, "gia_tri_moi": [], "cau_kiem": None,
                    "chan": f"bảng ghép chưa có cột doanh số sau chiết khấu mà file còn cột chưa ghép giống cột tiền ({where}) — ghép đủ sales_after_discount "
                            "(+ discount_value nếu file có) để máy kiểm được hai cột tiền; nếu file thật không có cột sau chiết khấu, gửi lại kèm "
                            "khong_co_cot_sau_ck: true rồi ghi lý do ở ghi_chu khi xác nhận"}
        if suspects:
            needs_note = True
            warnings.append(f"bạn khai file không có cột doanh số sau chiết khấu, dù còn cột chưa ghép giống cột tiền ({where}) — xác nhận phải ghi lý do "
                            "ở ghi_chu; ghép nhầm cột tiền sẽ làm sai tiền mà máy không bắt được")
        else:
            warnings.append("file chỉ có một cột tiền (không thấy cột doanh số sau chiết khấu) — máy chưa kiểm được nghĩa cột tiền, "
                            "đối chiếu tổng doanh số với báo cáo của bạn")
    code = secrets.token_hex(16)
    checks = app.state.__dict__.setdefault("mapping_checks", {})
    for key in [key for key, item in checks.items() if item["expires"] < time.time()]:
        checks.pop(key)
    checks[code] = {"sha_file": sha, "table": body["bang"], "fingerprint": fingerprint, "mapping": mapping, "answer": answer,
                    "expires": time.time() + MAPPING_CHECK_TTL_SECONDS, "new_codes": sorted(item["code"] for item in new_values), "needs_note": needs_note}
    return {"ok": not new_values, "van_tay_bo_cuc": fingerprint, "tom_tat_cot": summary, "doi_soat": result["reconciliation"], "canh_bao": warnings,
            "gia_tri_moi": [{"truong": item["field"], "ma": item["code"], "so_dong": item["lines"]} for item in new_values],
            "cau_kiem": {"ma": code, "cau": "Mở file gốc, nhìn dòng tiêu đề: cột doanh số TRƯỚC chiết khấu là cột thứ mấy (cột A = 1)?"
                         + (" Có ngành mới chưa biết — xem trong file rồi gửi kèm cho_qua = danh sách mã moi_… nếu đúng là ngành thật." if new_values else "")}}


def _try_route_formula(app, body: dict, sha: str, path: Path):
    """Giai đoạn 2a-1 — thử CÔNG THỨC ĐỌC bảng tuyến trên sổ đã gửi qua /bo-cuc: không tính KPI, không lưu. Không gửi `ghep` ⇒ dùng công thức lưu sẵn /
    đã xác nhận khớp vân tay bố cục; không có ⇒ 409 mã `can_cong_thuc_doc` kèm khuôn. Đọc được ⇒ câu kiểm (số nhân viên, điểm bán, theo kênh, dòng của mã
    ngoài đội bị bỏ, dòng mẫu); lệch khuôn ⇒ `loi` có mã. Đội = các mã của file chỉ tiêu bản đang dùng."""
    import secrets
    import time
    from kpi_agent.backend import route_upload
    from kpi_agent.core import khuon
    source = "gui_kem"
    if body.get("ghep") is None:
        with store.connect(app.state.store) as conn:
            mapping, source = route_upload.formula_for(path, store.mappings_of(conn, "tuyen"))
        if mapping is None:
            return JSONResponse(route_upload.need_formula_reply(), status_code=409)
    else:
        try:
            mapping = khuon.normalize_mapping({**body["ghep"], "table": "tuyen"}) if isinstance(body["ghep"], dict) else None
        except ValueError as error:
            raise upload.UploadRefused(str(error) if str(error).startswith("bảng ghép sai khuôn") else "ghep sai khuôn") from None
        if mapping is None:
            raise upload.UploadRefused("ghep phải là bảng JSON theo khuôn công thức đọc")
    team = _team_for_route_trial(app)                # ngoài khối try: lời "tải file chỉ tiêu trước" của kỳ trống phải tới người dùng nguyên văn
    orders_state = _state_for_route_trial(app)       # như trên: "tải file đơn hàng trước"
    try:
        outcome = route_upload.try_formula(path, mapping, team)
    except Exception:   # noqa: BLE001 — gói hỏng / XML lạ ⇒ 422, không 500 (như /bo-cuc)
        raise upload.UploadRefused("file không đọc được bảng tính") from None
    if not outcome["ok"]:
        return {"ok": False, "bang": "tuyen", "nguon_cong_thuc": source, "loi": outcome["loi"], "cau_kiem": None}
    # cùng phép đối chiếu với đơn hàng + cùng ngưỡng dừng như lúc tải lên — công thức sai không được xác nhận rồi lưu dùng lại
    summary = {**outcome["tom_tat"], **_coverage_keys(_orders_vs_routes(orders_state, outcome["mcp"], team))}
    stops, warnings = route_upload.route_stops(summary)
    if stops:
        return {"ok": False, "bang": "tuyen", "nguon_cong_thuc": source, "loi": stops, "tom_tat": summary, "cau_kiem": None}
    summary["canh_bao"] = warnings
    outcome = {**outcome, "tom_tat": summary}
    code = secrets.token_hex(16)
    checks = app.state.__dict__.setdefault("mapping_checks", {})
    for key in [key for key, item in checks.items() if item["expires"] < time.time()]:
        checks.pop(key)
    checks[code] = {"sha_file": sha, "table": "tuyen", "fingerprint": outcome["van_tay_bo_cuc"], "mapping": mapping, "answer": outcome["cot_ma_diem_ban"],
                    "expires": time.time() + MAPPING_CHECK_TTL_SECONDS, "new_codes": [], "needs_note": False}
    return {"ok": True, "bang": "tuyen", "nguon_cong_thuc": source, "van_tay_bo_cuc": outcome["van_tay_bo_cuc"], "cong_thuc": mapping,
            "tom_tat": outcome["tom_tat"],
            "cau_kiem": {"ma": code, "cau": "Mở sổ gốc, sheet danh sách tuyến, nhìn dòng tiêu đề: cột MÃ ĐIỂM BÁN là cột thứ mấy (cột A = 1)?"}}


def _new_values_of(path: Path, period: str, mapping: dict) -> list[dict]:
    """Giá trị chưa biết của file dưới bảng ghép, mỗi giá trị một MÃ CHE (moi_1, moi_2… theo thứ tự số dòng giảm dần) — chữ thật chỉ backend giữ."""
    from kpi_agent.core import khuon
    from kpi_agent.core.load_raw import _read_rows, unknown_values
    everything = khuon.normalize_mapping({key: value for key, value in mapping.items() if key != "accepted_values"})
    lines, _total = _read_rows(path, period, mapping=everything, check_values=False)   # chỉ để ĐẾM giá trị mới, không tính gì từ đây
    found = unknown_values(lines, everything)
    items = sorted(((field, value, n) for field, counts in found.items() for value, n in counts.items()), key=lambda item: (-item[2], item[0], str(item[1])))
    return [{"field": field, "value": value, "lines": n, "code": f"moi_{i}"} for i, (field, value, n) in enumerate(items, start=1)]


def _column_summary(path: Path, mapping: dict) -> list[dict]:
    """Tóm tắt từng cột dưới dòng tiêu đề của bảng ghép (17/11, nhóm kỹ thuật phác màn ghép: người ghép khỏi chọn mù khi tên cột vô nghĩa):
    kiểu ô phần lớn (so / ngay / chu / trong), % ô trống, số giá trị khác nhau, ≤ 3 mẫu. Mẫu theo rào U1 (không lặp chữ người dùng gõ): chỉ trả
    NGUYÊN giá trị thuộc tập đã biết (`khuon.KNOWN_VALUES` + `CANONICAL_VALUES` — kênh, cờ, trạng thái, ngành); chữ khác ⇒ "‹chữ N ký tự›"; số / ngày
    không trả mẫu (doanh số, mã khách). Dòng không mã NV (dòng tổng, dòng trống) vẫn tính — đây chỉ là gợi ý, không phải kiểm.
    """
    from kpi_agent.core import khuon
    safe = set().union(*khuon.KNOWN_VALUES.values(), *khuon.CANONICAL_VALUES.values())
    rows = iter(_mapped_sheet_rows(path, mapping)[mapping["header_row"] - 1:])
    width = len(next(rows, ()))
    kinds = [defaultdict(int) for _ in range(width)]
    values = [set() for _ in range(width)]
    total = 0
    for row in rows:
        total += 1
        for i in range(width):
            cell = row[i] if i < len(row) else None
            kind = "trong" if cell is None or (isinstance(cell, str) and not cell.strip()) else \
                "ngay" if isinstance(cell, (datetime, date)) else "so" if isinstance(cell, (int, float)) and not isinstance(cell, bool) else "chu"
            kinds[i][kind] += 1
            if kind != "trong" and len(values[i]) < 1000:
                values[i].add(cell)
    out = []
    for i in range(width):
        filled = {kind: n for kind, n in kinds[i].items() if kind != "trong"}
        texts = sorted((str(value) for value in values[i] if isinstance(value, str)), key=lambda text: (text not in safe, text))   # mẫu đã biết trước
        samples = [text if text in safe else f"‹chữ {len(text)} ký tự›" for text in texts[:3]]
        out.append({"vi_tri": i + 1, "kieu": max(filled, key=filled.get) if filled else "trong",
                    "ty_le_trong": round(100 * kinds[i]["trong"] / total, 1) if total else 100.0,
                    "so_gia_tri_khac": len(values[i]) if len(values[i]) < 1000 else "≥ 1000", "mau": samples})
    return out


def _unmapped_money_suspects(path: Path, mapping: dict) -> list[int]:
    """Vị trí (1-based) các cột CHƯA ghép mà có thể là cột tiền bị bỏ sót khi bảng ghép không có cột sau chiết khấu (, M1-r — MAJOR
    17/11: ghép trước CK ⇒ cột sau CK rồi bỏ trống sau CK + chiết khấu ⇒ 145.640.000 lặng lẽ). Nghi khi: (1) cột mang tên chuẩn "Sales After Discount" /
    "Discount value"; hoặc (2) cột số mà MỌI dòng đơn ≥ cột trước CK đã ghép và lớn hơn ở ≥ 1 dòng (= dáng cột trước CK thật). Không dùng "mọi cột số
    có ô Total": RAW T10 có 6 cột số ngoài lược đồ (đơn giá, quy cách, số lượng, khuyến mãi) có ô Total ⇒ file thật một cột tiền sẽ bị chặn oan.
    """
    from kpi_agent.core import khuon
    header = [str(name).strip() if name is not None else None for name in _header_of(path, mapping)]
    position = lambda column: (column - 1 if column <= len(header) else None) if isinstance(column, int) else \
        (header.index(column) if column in header else None)
    mapped = {position(column) for column in mapping["columns"].values()} - {None}
    unmapped = [i for i in range(len(header)) if i not in mapped]
    named = [i for i in unmapped if header[i] in ("Sales After Discount", "Discount value")]
    before, code = position(mapping["columns"]["sales_before_discount"]), position(mapping["columns"]["sm_code"])
    number = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
    alive = {i: [0, 0] for i in unmapped if i not in named}   # vị trí ⇒ [mọi dòng ≥ trước CK vẫn đúng (0 = đúng), số dòng lớn hơn]
    for row in _mapped_sheet_rows(path, mapping)[mapping["header_row"]:]:   # các dòng SAU dòng tiêu đề
        if code is None or before is None or code >= len(row) or not row[code] or before >= len(row) or not number(row[before]):
            continue                                    # chỉ dòng đơn có mã NV + ô trước CK là số
        for i in list(alive):
            value = row[i] if i < len(row) else None
            if not number(value) or value < row[before] - khuon.MONEY_TOLERANCE:
                alive.pop(i)
            elif value > row[before] + khuon.MONEY_TOLERANCE:
                alive[i][1] += 1
    return sorted(i + 1 for i in named + [i for i, (_, bigger) in alive.items() if bigger])


def _mapped_sheet_rows(path: Path, mapping: dict) -> tuple:
    """Mọi dòng (từ dòng 1) của sheet mà bảng ghép chỉ tới — lấy từ lớp đọc-một-lần (`core/raw_sheet.py`, 19/11): một lần ghép thử trước đây mở
    cùng file 5 lần (đo 19/11), nay các bước dùng chung một lần đọc. File không có sheet đó ⇒ lời từ chối như trước."""
    from kpi_agent.core import raw_sheet
    _sheet_names, rows = raw_sheet.sheet_rows(path, mapping["sheet"])
    if rows is None:
        raise upload.UploadRefused("file không có sheet của bảng ghép")
    return rows


def _header_of(path: Path, mapping: dict) -> list:
    """Tên cột ở dòng tiêu đề của bảng ghép — để băm vân tay bố cục đúng như /bo-cuc (`khuon.read_layout`)."""
    rows = _mapped_sheet_rows(path, mapping)
    return list(rows[mapping["header_row"] - 1]) if mapping["header_row"] <= len(rows) else []


@app.post("/api/system/ghep-xac-nhan")
async def confirm_mapping(request: Request):
    """JSON {ma, tra_loi, nguoi, ghi_chu?}: đúng câu kiểm (lệch < 1 đồng) ⇒ lưu cách ghép (kho `column_mapping`, chỉ thêm) cho vân tay bố cục của file
    ⇒ tải lên file cùng bố cục dùng lại. Sai / hết hạn ⇒ 409, câu kiểm bỏ (mỗi câu MỘT lần trả lời), không lưu gì."""
    problem = _system_key_problem(request)
    if problem:
        return problem
    import time
    from kpi_agent.core import khuon
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or set(body) - {"ma", "tra_loi", "nguoi", "ghi_chu", "cho_qua"} or not isinstance(body.get("nguoi"), str) \
            or not body["nguoi"].strip() or len(body["nguoi"]) > 80 or len(str(body.get("ghi_chu") or "")) > 500:
        return _refused_json("thân phải là JSON {ma, tra_loi, nguoi (≤ 80 ký tự), ghi_chu (≤ 500)}", 422)
    checks = request.app.state.__dict__.get("mapping_checks", {})
    no_note = not str(body.get("ghi_chu") or "").strip()
    if body.get("cho_qua") and no_note:
        # 17/11 (nhóm kỹ thuật phác màn): cho qua ngành mới phải có LÝ DO — chặn TRƯỚC khi tiêu câu kiểm ⇒ người ghi lý do rồi gửi lại cùng mã
        return _refused_json("có cho_qua thì phải ghi lý do ở ghi_chu (vì sao ngành mới là ngành thật) — cách ghép chưa lưu, gửi lại cùng mã", 422)
    if (checks.get(str(body.get("ma"))) or {}).get("needs_note") and no_note:
        # M1-r: người đã khai khong_co_cot_sau_ck dù file còn cột giống cột tiền ⇒ phải ghi lý do (chưa tiêu câu kiểm)
        return _refused_json("đã khai file không có cột sau chiết khấu ⇒ phải ghi lý do ở ghi_chu — cách ghép chưa lưu, gửi lại cùng mã", 422)
    check = checks.pop(str(body.get("ma")), None)
    if check is None or check["expires"] < time.time():
        return _refused_json("câu kiểm không có hoặc đã hết hạn — ghép thử lại", 409)
    answer = body.get("tra_loi")   # số thứ tự cột (số nguyên JSON)
    if isinstance(answer, bool) or not isinstance(answer, int) or answer != check["answer"]:
        column = "mã điểm bán" if check["table"] == "tuyen" else "doanh số trước chiết khấu"
        return _refused_json(f"trả lời câu kiểm chưa đúng — cách ghép KHÔNG được lưu; xem lại cột {column} rồi ghép thử lại", 409)
    if sorted(body.get("cho_qua") or []) != check["new_codes"]:
        # bước 2.4: có ngành mới ⇒ người phải cho qua ĐỦ từng mã (xem file gốc) — thiếu / thừa ⇒ không lưu (mỗi câu kiểm một lần)
        return _refused_json(f"cần cho_qua đúng các mã giá trị mới {check['new_codes']} — cách ghép KHÔNG được lưu; ghép thử lại", 409)
    with store.connect(request.app.state.store) as conn:
        mapping_id = store.record_mapping(conn, check["table"], check["fingerprint"], check["mapping"], khuon.mapping_sha(check["mapping"]),
                                          body["nguoi"].strip(), str(body.get("ghi_chu") or ""))
    return {"mapping_id": mapping_id, "mapping_sha": khuon.mapping_sha(check["mapping"]), "van_tay_bo_cuc": check["fingerprint"]}


# Bậc 2 bước 2.6 (A thêm 17/11): luật chọn theo KỲ của file tải lên. None = cùng thư mục với file luật của workflow (kpi_agent/config/); test đặt
# thư mục riêng để thử luật kỳ khác mà không ghi vào config thật.
PERIOD_RULES_DIR: Path | None = None


def _rules_file_for_period(workflow: dict, period: str) -> str:
    """Đường file luật của kỳ `period` (YYYY-MM) theo tên file luật của workflow: rules_2026_10_raw.yaml ⇒ rules_2026_11_raw.yaml. Kỳ của workflow ⇒
    đúng file đó (bản dữ liệu không đổi). Thiếu file / file ghi kỳ khác ⇒ 409 nêu đúng file thiếu — không đoán, không mượn luật kỳ khác."""
    base = runner.node_param(workflow, "load_rules", "path")
    base_period = yaml.safe_load(runner.repo_path(base).read_text(encoding="utf-8"))["period"]
    if period == base_period:
        return base
    name = Path(base).name
    token = base_period.replace("-", "_")
    if name.count(token) != 1:
        raise upload.UploadRefused(f"chưa có luật cho kỳ {queries._short(period)} — tên file luật của workflow không có kỳ để đổi (luật đang có: kỳ "
                                   f"{base_period})", 409)
    folder = PERIOD_RULES_DIR or runner.repo_path(base).parent
    wanted = folder / name.replace(token, period.replace("-", "_"))
    shown = _shown_path(wanted)
    if not wanted.is_file():
        raise upload.UploadRefused(f"chưa có luật cho kỳ {queries._short(period)}: thiếu file {shown} — thêm file luật của kỳ này rồi tải lại "
                                   "(không đoán luật)", 409)
    try:
        found = yaml.safe_load(wanted.read_text(encoding="utf-8")).get("period")
    except (yaml.YAMLError, AttributeError, OSError):
        raise upload.UploadRefused(f"file luật {shown} không đọc được", 409) from None
    if found != period:
        raise upload.UploadRefused(f"file luật {shown} ghi kỳ {found}, không phải {period} — sửa file luật", 409)
    return str(wanted) if PERIOD_RULES_DIR else str(wanted.relative_to(REPO_ROOT))


def _refuse_fixed_files_of_other_period(workflow: dict, period: str, base_period: str, kind: str) -> None:
    """17/11 (sai lặng lẽ tệ hơn dừng): chỉ tiêu + danh sách tuyến (MCP) vẫn là file CỐ ĐỊNH của workflow (kỳ của workflow) — file tải lên kỳ
    khác mà tính tiếp sẽ dùng chỉ tiêu / tuyến của kỳ cũ ⇒ 409 nêu rõ file nào thiếu cho kỳ đó. Chỉ tiêu + tuyến theo kỳ làm thật ở bậc 3.
    """
    if period == base_period:
        return
    fixed = [] if kind == "chi_tieu" else [("chỉ tiêu", runner.node_param(workflow, "read_targets", "path"))]
    if runner.data_block_type(workflow) == "read_raw":
        fixed.append(("danh sách tuyến", runner.node_param(workflow, "read_raw", "mcp_path")))
    if fixed:
        listed = "; ".join(f"{what} đang có là file kỳ {base_period} ({_shown_path(runner.repo_path(path))})" for what, path in fixed)
        raise upload.UploadRefused(f"chưa có chỉ tiêu / danh sách tuyến cho kỳ {queries._short(period)} — {listed}; backend chưa nhận file này theo kỳ "
                                   "(bậc 3) ⇒ không tính bằng file của kỳ khác", 409)


def _build_version(app, base_state, kind: str, period: str, body: bytes, wanted_cutoff: date | None = None) -> dict:
    if kind == upload.PAGE_TARGETS:
        return _build_page_version(app, base_state, period, body, wanted_cutoff)
    upload.check_package(body)
    workflow = copy.deepcopy(base_state.workflow)
    block = upload.KINDS[kind]
    if block in ("read_raw", "read_tracking") and runner.data_block_type(workflow) != block:
        raise upload.UploadRefused(f"backend đang chạy đường {runner.data_block_type(workflow)} — không nhận file loại {kind}")
    # 2.6: luật của KỲ file tải lên (thay một file luật gắn cứng); kỳ của workflow ⇒ workflow không đổi ⇒ bản dữ liệu như cũ
    base_period = yaml.safe_load(runner.repo_path(runner.node_param(workflow, "load_rules", "path")).read_text(encoding="utf-8"))["period"]
    if kind == upload.ROUTE_BOOK and period != base_ctx_period(base_state):
        # 2a-1 (chốt 20/11, cách nhỏ nhất): sổ chỉ thay bảng tuyến của một bản ĐÃ có file đơn hàng của kỳ đó; chưa có ⇒ từ chối, không lưu chờ
        raise upload.UploadRefused(f"chưa có file đơn hàng của kỳ {queries._short(period)} — tải file đơn hàng của kỳ đó trước, rồi mới tải sổ theo dõi",
                                   409, "chua_co_don_hang_cua_ky")
    next(node for node in workflow["nodes"] if node["type"] == "load_rules").setdefault("params", {})["path"] = _rules_file_for_period(workflow, period)
    if kind != upload.ROUTE_BOOK:
        _refuse_fixed_files_of_other_period(workflow, period, base_period, kind)
    sha = hashlib.sha256(body).hexdigest()
    folder = app.state.upload_dir
    try:
        temp = upload.write_temp(body, folder)
    except OSError:
        # Không ghi được ⇒ báo rõ, KHÔNG ghi lặng sang chỗ khác. Không in đường dẫn / lỗi hệ điều hành ra ngoài
        raise upload.UploadRefused("thư mục lưu file tải lên không ghi được (kiểm KPI_UPLOAD_DIR) — chưa nhận file", 503) from None
    try:
        if kind == upload.ROUTE_BOOK:
            route = _route_inputs(app, base_state, temp)
            summary = _build_from_file(app, base_state, workflow, kind, block, period, temp, sha, wanted_cutoff, route)
            # câu kiểm của bảng tuyến đi kèm MỖI lần tải sổ (kể cả khi dùng công thức đã lưu) — người tải nhìn số nhân viên / điểm bán / mã ngoài đội
            return {**summary, "tuyen_tu_so": {"nguon_cong_thuc": route["source"], "tom_tat": route["summary"]},
                    "gioi_han": "danh sách tuyến lấy từ sổ theo dõi vừa tải; chỉ tiêu Kokomi vẫn là file cố định của backend; trang User không thấy bản này"}
        return _build_from_file(app, base_state, workflow, kind, block, period, temp, sha, wanted_cutoff)
    except BaseException:
        # File bị từ chối / lỗi: bỏ sheet của nó khỏi lớp đọc-một-lần (khoá nhớ = sha256 nội dung = `sha`) — không ai dùng nữa mà vẫn chiếm chỗ,
        # hai lần từ chối liền nhau có thể đẩy sheet của bản đang dùng ra (+ chủ file 19/11).
        from kpi_agent.core import raw_sheet
        raw_sheet.forget(sha)
        raise
    finally:
        temp.unlink(missing_ok=True)       # bị từ chối / lỗi ⇒ không còn file nào; qua kiểm ⇒ đã đổi tên (finalize) nên không còn ở đây


def base_ctx_period(state) -> str:
    return state.ctx.period


PAGE_TARGETS_DIR = "chi_tieu_rieng"     # thư mục con của thư mục tải lên: file chỉ tiêu đã tách từ trang (trang không lưu); nạp lại lúc bật đọc ở đây
TARGETS_FROM_PAGE_NOTE = "chỉ tiêu riêng của Thưởng thêm (Mì Kokomi 90, Meat) lấy từ trang theo dõi tải lên"


def _fixed_target_files(period: str) -> dict[str, str]:
    """{"kokomi": tên file, "meat": tên file} — hai file chỉ tiêu riêng mà file luật thưởng thêm của kỳ ghi cứng; bản có trang tải lên thay đúng hai file
    này. File luật thiếu / cần khối chỉ tiêu mà trang không có ⇒ từ chối (không đoán khoản nào ứng với khối nào)."""
    from kpi_agent.backend import page_targets
    rules_path = REPO_ROOT / f"kpi_agent/config/extra_bonus_{period.replace('-', '_')}.yaml"
    try:
        items = (yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}).get("items") or {}
    except (OSError, yaml.YAMLError):
        raise upload.UploadRefused(f"chưa có file luật thưởng thêm cho kỳ {queries._short(period)} — chưa dùng được chỉ tiêu từ trang", 409,
                                   "chua_co_luat_thuong_them") from None
    keys_of = {}
    for item in items.values():
        if isinstance(item, dict) and item.get("targets_file"):
            keys_of.setdefault(item["targets_file"], set()).update({item.get("targets_key"), (item.get("gate") or {}).get("targets_key", item.get("targets_key"))})
    meat_keys = {key for group, key in page_targets.PAGE_ITEMS.values() if group == page_targets.MEAT}
    flat, blocks = [name for name, keys in keys_of.items() if keys == {None}], [name for name, keys in keys_of.items() if keys and keys <= meat_keys]
    if len(flat) != 1 or len(blocks) != 1 or len(keys_of) != 2:
        raise upload.UploadRefused("file luật thưởng thêm của kỳ cần chỉ tiêu riêng khác với 5 khoản trang có — chưa dùng được chỉ tiêu từ trang", 409,
                                   "luat_khac_khoan_cua_trang")
    return {page_targets.KOKOMI: flat[0], page_targets.MEAT: blocks[0]}

def _build_page_version(app, base_state, period: str, body: bytes, wanted_cutoff: date | None) -> dict:
    """Loại tải lên `trang_chi_tieu` (2a-2 nấc 3): trang theo dõi .html ⇒ chỉ tiêu riêng Kokomi / Meat của bản. Trang KHÔNG được lưu — chỉ lưu hai file
    đã tách (tên = sha256 nội dung) trong `<thư mục tải lên>/chi_tieu_rieng/`; bản mới = bản đang dùng + hai file đó thay hai file cố định."""
    from kpi_agent.backend import page_targets
    from kpi_agent.core.load_raw import raw_employee_names
    workflow = copy.deepcopy(base_state.workflow)
    if runner.data_block_type(workflow) != "read_raw":
        raise upload.UploadRefused("chỉ tiêu từ trang chỉ dùng ở đường file đơn hàng — backend đang chạy đường khác")
    if period != base_ctx_period(base_state):
        raise upload.UploadRefused(f"chưa có file đơn hàng của kỳ {queries._short(period)} — tải file đơn hàng của kỳ đó trước, rồi mới tải trang theo dõi",
                                   409, "chua_co_don_hang_cua_ky")
    fixed = _fixed_target_files(period)
    team = set(base_state.ctx.to_real.values())
    try:
        if getattr(app.state, "restoring", None) is not None:
            # nạp lại lúc bật: `body` là file chỉ tiêu ĐÃ TÁCH của lượt tải (trang không được lưu) — kiểm lại hình + đội rồi dùng
            extracted = page_targets.from_saved_form(body, team)
        else:
            names = raw_employee_names(str(runner.repo_path(runner.node_param(workflow, "read_raw", "path"))), runner.node_param(workflow, "read_raw", "ghep"))
            extracted = page_targets.extract(body, period, names, team)
    except page_targets.PageProblem as problem:
        raise upload.UploadRefused(page_targets.refusal_text(problem), 413 if problem.code == "trang_qua_lon" else 422, problem.code) from None
    except (OSError, ValueError):
        raise upload.UploadRefused("không đọc được tên nhân viên từ file đơn hàng của bản — chưa ghép được trang với đội") from None
    folder = app.state.upload_dir / PAGE_TARGETS_DIR
    own_targets, created = {}, []
    saved = page_targets.saved_form(extracted)
    saved_sha = hashlib.sha256(saved).hexdigest()       # mã băm ghi ở dòng nạp lại: file <mã>.json này đủ để dựng lại bản lúc bật
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for name, content in [(fixed[group], content) for group, content in page_targets.target_files(extracted).items()] + [(None, saved)]:
            path = folder / f"{hashlib.sha256(content).hexdigest()}.json"
            if not path.exists():
                path.write_bytes(content)
                created.append(path)
            if name is not None:
                own_targets[name] = str(path)
    except OSError:
        raise upload.UploadRefused("thư mục lưu file tải lên không ghi được (kiểm KPI_UPLOAD_DIR) — chưa nhận file", 503) from None
    try:
        summary = _build_from_file(app, base_state, workflow, upload.PAGE_TARGETS, "read_raw", period, None, saved_sha, wanted_cutoff,
                                   own_targets=own_targets)
    except BaseException:
        for path in created:               # bị từ chối / lỗi ⇒ không để lại file chỉ tiêu nào của lượt này
            path.unlink(missing_ok=True)
        raise
    blocks = [extracted[page_targets.KOKOMI], *extracted[page_targets.MEAT].values()]
    # `trang_sha256` chỉ có ở lượt tải thật (lúc nạp lại không còn trang để băm)
    return {**summary, "tu_trang": {"so_khoan": len(blocks), "so_nguoi": len(blocks[0]),
                                    "trang_sha256": None if getattr(app.state, "restoring", None) is not None else hashlib.sha256(body).hexdigest()},
            "gioi_han": "chỉ tiêu riêng Kokomi / Meat lấy từ trang vừa tải (trang không được lưu); trang User không thấy bản này"}


def _route_inputs(app, base_state, path: Path) -> dict:
    """Sổ theo dõi tải lên làm ĐẦU VÀO (2a-1): công thức đọc bảng tuyến khớp vân tay bố cục + chạy thử trên chính file ⇒ {mapping, mapping_sha, team,
    summary, source}. Không có công thức ⇒ 409 `can_cong_thuc_doc` (gửi công thức qua /bo-cuc ⇒ /ghep-thu ⇒ /ghep-xac-nhan); lệch khuôn ⇒ 422 nêu mã lỗi."""
    from kpi_agent.backend import route_upload
    from kpi_agent.core import khuon
    try:
        with store.connect(app.state.store) as conn:
            mapping, source = route_upload.formula_for(path, store.mappings_of(conn, "tuyen"))
        if mapping is None:
            raise upload.UploadRefused(route_upload.need_formula_reply()["refused"], 409, route_upload.NEED_FORMULA)
        team = sorted(set(base_state.ctx.to_real.values()))   # đội = mã của file chỉ tiêu của CHÍNH bản đang dùng (lõi tự rút, không nhận từ người gọi)
        outcome = route_upload.try_formula(path, mapping, set(team))
    except upload.UploadRefused:
        raise
    except Exception:   # noqa: BLE001 — gói hỏng / XML lạ ⇒ 422, không 500
        raise upload.UploadRefused("file không đọc được bảng tính") from None
    if not outcome["ok"]:
        raise upload.UploadRefused(f"sổ theo dõi không đọc được bảng tuyến theo công thức: {route_upload.problems_text(outcome['loi'])}", 422,
                                   "so_lech_khuon_tuyen")
    summary = {**outcome["tom_tat"], **_coverage_keys(_orders_vs_routes(base_state, outcome["mcp"], set(team)))}
    # Vòng 2 (M1): tuyến đọc được nhưng SAI (nhầm cột mã, đổi chéo kênh) từng ra bản mới với tiền sai ⇒ đối chiếu với đơn hàng thành DỪNG
    stops, warnings = route_upload.route_stops(summary)
    if stops:
        raise upload.UploadRefused(f"bảng tuyến đọc từ sổ không khớp file đơn hàng của bản đang dùng — không dựng bản dữ liệu: "
                                   f"{route_upload.problems_text(stops)}", 422, stops[0]["ma"])
    summary["canh_bao"] = warnings
    return {"mapping": mapping, "mapping_sha": "tuyen:" + khuon.mapping_sha(mapping), "team": team, "summary": summary, "source": source}


def _orders_vs_routes(base_state, mcp: dict, team: set[str]) -> dict | None:
    """Điểm bán CÓ ĐƠN ở file đơn hàng của bản đang dùng so với bảng tuyến vừa đọc, theo kênh (`route_upload.order_coverage`). Không đọc được ⇒ None."""
    from kpi_agent.backend import route_upload
    from kpi_agent.core.load_raw import period_rules, read_raw_lines
    workflow, ctx = base_state.workflow, base_state.ctx
    try:
        lines, _late = read_raw_lines(runner.repo_path(runner.node_param(workflow, "read_raw", "path")), ctx.cutoff, ctx.period,
                                      invoice_required_status=(), mapping=runner.node_param(workflow, "read_raw", "ghep"),
                                      period_rules=period_rules(ctx.rules["assumptions"]))
    except Exception:   # noqa: BLE001 — số đối chiếu phụ; file đơn hàng của bản đã qua kiểm lúc dựng bản
        return None
    return {"by_channel": route_upload.order_coverage(lines, mcp, team), "by_person": route_upload.order_coverage_by_person(lines, mcp, team)}


def _attach_route_notes(ctx, summary: dict) -> None:
    """Cảnh báo / thứ bị bỏ ở bước nhận sổ đi theo BẢN: trước đây chỉ nằm ở phản hồi tải lên ⇒ người hỏi số của bản không thấy.
    Lưu ý cấp đội (không gắn người) ⇒ vào cả cờ cho trang, cờ kèm mọi kết quả công cụ và danh sách `get_data_issues`.
    """
    from kpi_agent.backend import route_upload
    for note in route_upload.version_notes(summary):
        _attach_note(ctx, note["level"], note["message"])


def _attach_note(ctx, level: str, message: str) -> None:
    """Một lưu ý cấp đội đi theo bản: cờ cho trang, cờ kèm mọi kết quả công cụ, danh sách `get_data_issues`."""
    ctx.data_issues.append({"level": level, "message": message, "employee_ids": []})
    ctx.data_flags.append(message)
    ctx.data_flags_for_model.append(message)


def _recheck_inherited_routes(base_state, workflow: dict, ctx) -> dict | None:
    """Bản dựng TRÊN một bản lấy tuyến từ sổ (tải file đơn hàng / chỉ tiêu mới lên bản đó) vẫn dùng bảng tuyến ấy, nhưng phép đối chiếu đơn hàng ⇄ tuyến
    lúc nhận sổ là với file đơn hàng CŨ ⇒ chạy LẠI với file của bản mới, cùng ngưỡng dừng (không thì đơn hàng mới lệch tuyến vẫn ra tiền, không dừng).
    Trả câu kiểm của bản mới (lưu ý đi theo bản); bản không lấy tuyến từ sổ ⇒ None. Lệch ⇒ 422 mang mã của ngưỡng bị chạm."""
    from types import SimpleNamespace
    from kpi_agent.backend import route_upload
    if next((n.get("params", {}) for n in workflow["nodes"] if n["type"] == "read_raw"), {}).get("mcp_ghep") is None:
        return None
    team, routes = set(ctx.to_real.values()), _routes_of(workflow)
    summary = {**(getattr(base_state, "route_summary", None) or {}), "team_codes_without_routes": len(team - set(routes)),
               **_coverage_keys(_orders_vs_routes(SimpleNamespace(workflow=workflow, ctx=ctx), routes, team))}
    stops, warnings = route_upload.route_stops(summary)
    if stops:
        raise upload.UploadRefused("bảng tuyến của bản này (đọc từ sổ theo dõi đã tải) không khớp file vừa tải — không dựng bản dữ liệu; tải lại sổ theo dõi "
                                   f"của kỳ sau khi đổi file đơn hàng: {route_upload.problems_text(stops)}", 422, stops[0]["ma"])
    summary["canh_bao"] = warnings
    return summary


def _coverage_keys(coverage: dict | None) -> dict:
    """Hai ô của câu kiểm từ phép đối chiếu đơn hàng ⇄ tuyến: theo kênh (`orders_vs_routes`) + theo người gộp hai kênh; không đối chiếu được ⇒ None."""
    return {"orders_vs_routes": coverage["by_channel"] if coverage else None, "orders_vs_routes_by_person": coverage["by_person"] if coverage else None}


def _refuse_team_mismatch(workflow: dict) -> None:
    """Bất biến lúc dựng bản lấy tuyến từ sổ: `mcp_doi` của khối đọc tuyến == tập mã của file chỉ tiêu của chính bản đó; lệch ⇒ dừng có mã."""
    from kpi_agent.backend import route_upload
    from kpi_agent.core.load_data import load_targets
    params = next((node.get("params", {}) for node in workflow["nodes"] if node["type"] == "read_raw"), {})
    if params.get("mcp_ghep") is None:
        return
    targets = load_targets(runner.repo_path(runner.node_param(workflow, "read_targets", "path")))["employees"]
    if route_upload.team_mismatch(params.get("mcp_doi"), targets):
        raise upload.UploadRefused("danh sách đội của bảng tuyến lệch với file chỉ tiêu của bản — không dựng bản dữ liệu", 409, "doi_lech_file_chi_tieu")


def _saved_mapping_for(app, kind: str, path: Path) -> tuple[dict | None, str | None]:
    """Bậc 2 bước 2.3: file RAW bố cục đã biết (T10 / T11: sheet Export, tiêu đề dòng 1, đúng 38 cột) ⇒ (None, None) = đường cũ y hệt. Bố cục khác ⇒
    cách ghép người đã xác nhận mới nhất có vân tay khớp file (tính theo sheet + dòng tiêu đề CỦA cách ghép đó — file đổi tên hết cột vẫn nhận ra);
    không khớp ghép nào ⇒ (None, "new_layout"): kiểm theo mẫu T10 như cũ, lời từ chối thêm gợi ý ghép cột. Loại file khác ⇒ chưa ghép (bậc 3).
    """
    if kind != "raw":
        return None, None
    from kpi_agent.core import khuon
    default = khuon.default_mapping("don_hang")
    try:
        if khuon.layout_fingerprint(default["sheet"], 1, _header_of(path, default)) == khuon.KNOWN_LAYOUTS["don_hang"]:
            return _accepted_values_for_known_layout(app, path, default)
    except upload.UploadRefused:
        pass                                  # không có sheet Export ⇒ xem các cách ghép đã lưu
    except Exception:   # noqa: BLE001 — gói hỏng ⇒ 422 như _check_layout (C U3)
        raise upload.UploadRefused("file raw hỏng — không đọc được gói .xlsx") from None
    with store.connect(app.state.store) as conn:
        saved = store.mappings_of(conn, "don_hang")
    for item in saved:
        try:
            if khuon.layout_fingerprint(item["mapping"]["sheet"], item["mapping"]["header_row"], _header_of(path, item["mapping"])) == item["fingerprint"]:
                return item["mapping"], item["mapping_sha"]
        except upload.UploadRefused:
            continue
    return None, "new_layout"      # không khớp ghép nào ⇒ kiểm như cũ (lời 422 cũ cho file hỏng); lỗi bố cục được thêm gợi ý ghép ở người gọi


def _accepted_values_for_known_layout(app, path: Path, default: dict) -> tuple[dict | None, str | None]:
    """(MAJOR 17/11 — đường "mọi tháng": tháng sau cùng bố cục T10 + ngành mới): bố cục đã biết vẫn dùng CỘT mặc định, nhưng áp ngành
    người đã cho qua (accepted_values của cách ghép đã lưu cho bố cục này). Chỉ trả bảng ghép khi file THẬT có ngành đã cho qua ⇒ file T10 cũ tải lại
    vẫn (None, None) = data_version cũ.
    """
    from kpi_agent.core import khuon
    with store.connect(app.state.store) as conn:
        saved = store.latest_mapping(conn, "don_hang", khuon.KNOWN_LAYOUTS["don_hang"])
    accepted = set(((saved or {}).get("mapping") or {}).get("accepted_values", {}).get("sub_div", ()))
    if not accepted:
        return None, None
    rows = iter(_mapped_sheet_rows(path, default))   # bố cục đã biết ⇒ sheet mặc định có sẵn (đã qua kiểm khuôn); dùng chung lần đọc
    at = [str(name).strip() if name is not None else None for name in next(rows, ())].index(default["columns"]["sub_div"])
    present = sorted({row[at] for row in rows if at < len(row) and row[at] in accepted})
    if not present:
        return None, None
    mapping = khuon.normalize_mapping({**default, "accepted_values": {"sub_div": present}})
    return mapping, khuon.mapping_sha(mapping)

def _build_from_file(app, base_state, workflow, kind, block, period, temp, sha, wanted_cutoff, route: dict | None = None,
                     own_targets: dict | None = None) -> dict:
    # chỉ tiêu riêng của bản (2a-2 nấc 3): của trang vừa tải, hoặc kế thừa khi dựng TRÊN một bản đã có trang (tải chỉ tiêu / đơn hàng lên bản đó)
    from_page = own_targets is not None
    own_targets = own_targets if from_page else getattr(base_state, "targets_override", None)
    base_ctx = base_state.ctx
    mapping, mapping_sha = _saved_mapping_for(app, kind, temp)
    new_layout = mapping_sha == "new_layout"
    mapping_sha = None if new_layout else mapping_sha
    file_param = "path"   # ô của khối nhận đường file vừa tải
    base_map = _base_mask_map()
    base_known = {str(real) for real in base_map.values()}
    auto_map = _auto_mask_map(app)
    known_codes = base_known | {str(real) for real in auto_map.values()}
    new_employees: dict[str, str] = {}
    try:
        if route is not None or from_page:
            raise _RouteBook()   # sổ / trang làm đầu vào: đã đọc + kiểm ở `_route_inputs` / `_build_page_version`; không qua bộ kiểm theo loại của file dữ liệu
        # luật kỳ của file (đã chọn theo kỳ ở `_build_version`) — bộ đọc RAW cần trạng thái được tính + hạn hoá đơn của kỳ (bậc 3a)
        rules_path = runner.repo_path(runner.node_param(workflow, "load_rules", "path"))
        assumptions = (yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}).get("assumptions") or {}
        cutoff = upload.read_and_check(
            kind, temp, period, known_codes, base_ctx.cutoff, wanted_cutoff, mapping, assumptions,
            base_known_codes=base_known, new_employees_out=new_employees if kind == "chi_tieu" else None,
        )
    except upload.UploadRefused as refusal:
        if new_layout and refusal.reason.startswith("file raw thiếu"):
            raise upload.UploadRefused(refusal.reason + " — file bố cục khác mẫu: ghép cột trước (POST /api/system/bo-cuc ⇒ ghep-thu ⇒ ghep-xac-nhan)"
                                       " rồi tải lại", refusal.status, "can_ghep_cot") from None
        raise
    except _RouteBook:
        if wanted_cutoff is not None and f"{wanted_cutoff:%Y-%m}" != period:
            raise upload.UploadRefused(f"ngày chốt {wanted_cutoff:%d/%m/%Y} ngoài kỳ {period}") from None
        cutoff = wanted_cutoff or base_ctx.cutoff
        # trang: không có file nào của workflow đổi (file_param None) — chỉ hai file chỉ tiêu riêng của bản đổi
        mapping_sha, file_param = (route["mapping_sha"], "mcp_path") if route is not None else (None, None)
    node = next(node for node in workflow["nodes"] if node["type"] == block).setdefault("params", {})
    if file_param is not None:
        node[file_param] = str(temp)
    if route is not None:
        node["mcp_ghep"], node["mcp_doi"] = route["mapping"], route["team"]
    if mapping is not None:
        node["ghep"] = mapping
    if kind == "chi_tieu":
        # bản đang lấy tuyến từ sổ: "đội" của công thức đọc đi theo file chỉ tiêu ⇒ tải chỉ tiêu mới thì ghi lại danh sách mã của file mới
        routes_node = next((n.get("params", {}) for n in workflow["nodes"] if n["type"] == "read_raw"), {})
        if routes_node.get("mcp_ghep") is not None:
            from kpi_agent.core.load_data import load_targets
            routes_node["mcp_doi"] = sorted(str(code).strip() for code in load_targets(temp)["employees"])
    _refuse_team_mismatch(workflow)
    # Phần "code" của bản dữ liệu = workflow GỐC (bản chép chỉ khác đường file — nội dung file đã nằm trong files_sha256)
    # File phụ (chỉ tiêu Thưởng thêm, sổ nền gia vị) là file cố định của workflow, không tải lên được ⇒ đọc lại sha lúc này, cùng công thức lúc bật
    version = _data_version(_files_sha256(workflow), _code_fingerprint(base_state.workflow)[0], cutoff,
                            _extra_files_sha256(workflow, period, own_targets), mapping_sha)
    planned_auto = store.plan_auto_employee_masks(base_map, auto_map, new_employees) if new_employees else []
    extra_auto = {entry["masked_code"]: entry["real_code"] for entry in planned_auto}
    with _upload_lock:
        if version == app.state.data_version or version in app.state.versions:
            state = app.state if version == app.state.data_version else app.state.versions[version]
            app.state.latest_data_version = version   # tải lại file đã có cũng là lần tải gần nhất
            return _version_summary(version, state, reused=True)
        try:
            ctx = queries.build_context(cutoff, workflow, mask_path=_mask_path_for_workflow(app, workflow, extra_auto=extra_auto))
        except queries.WorkflowStopped as stop:
            # Không trả lời của bộ chạy: có thể nhắc chữ trong file (C rà bảo mật U1) — chỉ báo dừng, chi tiết xem bằng workflow cố định.
            # TRỪ lời của cổng đối soát RAW (bậc 1, 17/11): lời do code viết, chỉ có lý do cố định + số dòng + số tiền ⇒ chuyển nguyên để người tải biết vì sao
            gate = next((error.split(RECONCILE_STOP, 1)[1] for error in stop.result.errors if RECONCILE_STOP in error), None)
            if gate is not None:
                raise upload.UploadRefused(RECONCILE_STOP + gate) from None
            # + lời "luật kỳ nêu mã / nhóm / ngành mà file không có dòng nào" (bậc 3a P2): lời do code viết, chỉ có giá trị của file luật + cách cho qua
            # ⇒ chuyển nguyên (qua khuôn an toàn của `upload`) để người tải biết thiếu giá trị nào; không có thì họ chỉ thấy "bộ chạy dừng"
            marker = "luật kỳ nêu "
            for error in stop.result.errors:
                text = marker + error.split(marker, 1)[1] if marker in error else ""
                if upload.reason_code(ValueError(text)) == upload.RULE_VALUE_WITHOUT_LINES:
                    raise upload.UploadRefused(text, code=upload.RULE_VALUE_WITHOUT_LINES) from None
            raise upload.UploadRefused("dữ liệu không tính được — bộ chạy dừng (lỗi dữ liệu nặng); kiểm lại file theo mẫu") from None
        route_summary = route["summary"] if route is not None else _recheck_inherited_routes(base_state, workflow, ctx)
        if route_summary:
            _attach_route_notes(ctx, route_summary)
        # QUA mọi kiểm + tính được ⇒ mới đổi tên thành <sha>.xlsx; workflow của bản trỏ file đã đổi tên (khối sau như thưởng thêm đọc lại file này)
        # Trần số file kiểm NGAY TRƯỚC khi lưu: file bị từ chối không bao giờ tới đây; bản trùng (dùng lại) đã trả ở trên ⇒ không bị chặn oan
        if file_param is not None:
            upload.check_room(sha, app.state.upload_dir)
            node[file_param] = str(upload.finalize(temp, app.state.upload_dir, sha))
        if own_targets:
            _attach_note(ctx, "info", TARGETS_FROM_PAGE_NOTE)
        rule_file = _workflow_files(workflow)[0]
        files_sha256 = _files_sha256(workflow)
        code_sha, code_files = _code_fingerprint(workflow)
        # Ô giữ của các khoản tính theo bản (`spice`, `spice_customers`, `membership`, `extra_bonus`) phải CÓ trên state của bản (None = tính ở lần gọi
        # đầu): thiếu ô thì `_VersionState.__getattr__` rơi về ô của bản LÚC BẬT ⇒ trả số bản cũ như số bản mới (lỗi 16/11).
        state = _VersionState(app.state, ctx=ctx, workflow=workflow, files_sha256=files_sha256, store=app.state.upload_store,
                              extra_bonus=None, spice=None, spice_customers=None, membership=None, route_summary=route_summary,
                              targets_override=own_targets,
                              extra_files_sha256=_extra_files_sha256(workflow, period, own_targets),   # 2.6: /api/meta?data_version= nêu file phụ của ĐÚNG bản
                              workflow_version=app.state.workflow_version, workflow_run_id=None, data_version=version, name_entries={})
        if app.state.name_entries:
            # KPI_NAMES=on: bảng tên của CHÍNH bản này (file chỉ tiêu tải lên có thể khác đội lúc bật); thiếu / lệch ⇒ bản này chỉ mã che
            # (không dùng nhầm bảng tên lúc bật cho đội khác) — giới hạn ghi ở kế hoạch
            entries = _name_entries(_VersionApp(state))
            state.name_entries = entries if isinstance(entries, dict) else {}
        with store.connect(state.store) as conn:
            state.run_id = store.record_run(
                conn, period=ctx.period, cutoff=ctx.cutoff.isoformat(), rule_file=rule_file,
                # bậc 2: cách ghép là một phần DỮ LIỆU của bản tính (ghép khác ⇒ ảnh chụp dữ liệu khác ⇒ bản tính khác)
                data_files={**_data_files(files_sha256, rule_file), **({"ghep_cot": mapping_sha} if mapping_sha else {})},
                refreshed_at=ctx.snapshot_at.isoformat() if ctx.snapshot_at else None,
                code_sha=code_sha, code_files=code_files, lines=queries.bonus_lines(ctx),
                # nạp lại lúc bật: dùng lại ĐÚNG lượt tính cũ của bản này nếu còn trùng (giữ dấu "Đã soát" / lời duyệt, kho không phình mỗi lần bật)
                reuse_any=app.state.restoring is not None)
            _use_status(ctx, store.run_summary(conn, state.run_id))
            _attach_auto_employee_notes(ctx, base_map)
            assigned = store.record_auto_employee_masks(conn, base_map, new_employees) if new_employees else []
            # 2j: một dòng cho mỗi bản MỚI nhận — đủ để lần bật sau dựng lại đúng bản này (`_restore_uploaded_versions`). Đang nạp lại lúc bật ⇒
            # không ghi thêm dòng, giữ giờ tải đã ghi. Giờ tải chỉ để trang Dữ liệu hiện (/api/system/ky); không vào phép tính, không vào mã bản.
            restoring = app.state.restoring
            state.uploaded_at = restoring["created_at"] if restoring else store.record_uploaded_version(
                conn, data_version=version, kind=kind, period=period, wanted_cutoff=wanted_cutoff.isoformat() if wanted_cutoff else None,
                base_data_version=None if base_state is app.state else base_state.data_version, file_sha256=sha, mapping_sha=mapping_sha)
        app.state.versions[version] = state
        app.state.latest_data_version = version
        _extra_bonus(_VersionApp(state))   # như lúc bật: bản dữ liệu mới có thưởng thêm sẵn — lần gọi công cụ đầu không hết giờ
        # kỳ dựng từ file tải lên (trang Dữ liệu đọc ba tổng ngay sau lần tải): tính sẵn như bản đầu của kỳ — người tải chờ thêm một lượt đọc file đơn hàng
        if os.getenv("KPI_WARM_SPICE") == "on" or getattr(app.state, "uploads_only", False):
            _spice(_VersionApp(state))     # 2a-2 nấc 1: 1 % gia vị của chính bản tính sẵn — cùng công tắc với lúc bật (lifespan); tắt ⇒ tính ở lần gọi đầu
        while len(app.state.versions) > MAX_VERSIONS:
            app.state.versions.popitem(last=False)     # bản cũ nhất ra khỏi bộ nhớ (bản tính vẫn trong file lưu riêng)
        reply = _version_summary(version, state, reused=False)
        if assigned:
            reply["nhan_vien_moi"] = [{"ma_che": entry["masked_code"], "ho_ten": entry["full_name"]} for entry in assigned]
        return reply
_upload_lock = threading.Lock()

def _money_summary(state) -> dict:
    """Ba tổng tiền của MỘT bản dữ liệu + trạng thái từng khoản, cho `GET /api/system/ky/<kỳ>`: lấy từ kết quả ĐANG GIỮ của bản (cùng ô giữ mà ba đường
    tiền đọc) — không dòng theo người. 1 % gia vị chưa được tính (ô giữ còn trống) ⇒ "chua_tinh": không tính ở đây (một lượt đọc lại file đơn hàng)."""
    extra, spice = _extra_bonus(_VersionApp(state)), getattr(state, "spice", None)
    spice_state = "chua_tinh" if spice is None else "khong_tinh_duoc" if "refused" in spice else "co"
    return {"data_version": state.data_version,
            "tam_kpi": {"tong": sum(line["amount"] for line in queries.bonus_lines(state.ctx))},
            "thuong_them": {"tong": None if isinstance(extra, str) else extra["team_total"],
                            "trang_thai": "khong_tinh_duoc" if isinstance(extra, str) else "co", "ly_do": extra if isinstance(extra, str) else None},
            "gia_vi": {"tong": spice["team_reward"] if spice_state == "co" else None, "trang_thai": spice_state, "ly_do": (spice or {}).get("refused")}}


def _version_summary(version: str, state, reused: bool) -> dict:
    ctx = state.ctx
    # Thưởng thêm của CHÍNH bản này có tính được không (20/11: bản lấy tuyến từ sổ từng mất thưởng thêm mà phản hồi tải lên vẫn 200, không nhắc gì).
    # Không dừng lần tải: kỳ chưa có file luật thưởng thêm là ca hợp lệ. Ô THÊM — các ô cũ giữ nguyên.
    extra = _extra_bonus(_VersionApp(state))
    # 1 % gia vị: tính sẵn chỉ khi bật KPI_WARM_SPICE (một lượt đọc lại file đơn hàng) — chưa tính thì nói "chua_tinh" (tính ở lần gọi đầu), không đoán
    warm = os.getenv("KPI_WARM_SPICE") == "on" or getattr(state, "uploads_only", False)
    spice_result = _spice(_VersionApp(state)) if warm else getattr(state, "spice", None)
    spice_state = "chua_tinh" if spice_result is None else "khong_tinh_duoc" if "refused" in spice_result else "co"
    return {"thuong_them": "khong_tinh_duoc" if isinstance(extra, str) else "co", "thuong_them_ly_do": extra if isinstance(extra, str) else None,
            "gia_vi": spice_state, "gia_vi_ly_do": (spice_result or {}).get("refused"),
            "chi_tieu_rieng": "tu_trang" if getattr(state, "targets_override", None) else "file_co_dinh",
            # bản lấy tuyến từ sổ: câu kiểm tuyến (số người, điểm bán, tỉ lệ đối chiếu với file đơn hàng CỦA BẢN NÀY, cảnh báo) đi kèm MỌI lần tải ra
            # bản đó — trước đây chỉ lần tải sổ có, tải đơn hàng / chỉ tiêu lên bản có sổ thì người tải không thấy tỉ lệ mới.
            # Lần tải sổ ghi đè ô này kèm nguồn công thức (`_build_version`).
            **({"tuyen_tu_so": {"nguon_cong_thuc": None, "tom_tat": state.route_summary}} if getattr(state, "route_summary", None) else {}),
            "data_version": version, "run_id": state.run_id, "period": ctx.period, "cutoff": ctx.cutoff.isoformat(),
            "files_sha256": state.files_sha256, "data_flags": ctx.data_flags_for_model,
            "team_total": sum(line["amount"] for line in queries.bonus_lines(ctx)), "da_co_san": reused,
            # bậc 1 nạp chung (17/11): cổng đối soát của bản (dòng vào / tính / loại theo lý do + tiền) — chỉ số, không chữ trong file; sổ Tracking ⇒ None
            "doi_soat": ctx.raw_reconciliation,
            "gioi_han": ("tuyến lấy từ sổ theo dõi đã tải; thưởng thêm cần chỉ tiêu Kokomi / Meat từ trang theo dõi (tải trang để có)"
                         if _own_book(state.workflow) is not None and getattr(state, "uploads_only", False) else
                         "chỉ tiêu Kokomi + danh sách tuyến (MCP) vẫn là file cố định của backend; trang User không thấy bản này")}
