"""Bộ chạy workflow khối (bước 1 S2 — mục 2–3).

Ba việc, theo thứ tự:
1. **Kiểm file trước khi chạy** (`check_workflow`) — file sai thì TỪ CHỐI, 0 khối chạy. Học Capitol (chủ đề 02 mục 4 ý 5, chủ đề 17 mục 2.2b):
   họ có hàm kiểm dây nhưng bỏ qua kết quả, và KHÔNG có luật chiều dây bằng code ⇒ ở đây mọi lỗi đều chặn, kể cả dây ngược.
2. **Chạy lần lượt theo thứ tự file** — dây chỉ được đi từ khối đứng trước sang khối đứng sau, nên thứ tự file đã là thứ tự chạy (không cần xếp tầng:
   6 khối, vài giây, một máy — chủ đề 03 mục 5 "không học phần nặng").
3. **Gặp lỗi ⇒ dừng cả lượt** (03/11 "lỗi nặng ⇒ dừng hẳn"); khối sau ghi `not_run`, không ghi "xong với giá trị rỗng" như đường cũ của Capitol (chủ đề 03 mục 2.4).
   Lời lỗi đi qua `mask_codes` trước khi vào kết quả — lời `check_data` chứa mã NV thật.

Kết quả có **dấu vân tay đầu vào thật** (`input_fingerprint`): mã băm logic của workflow (bỏ `position` — `workflow_sha256`) + từng file mà ô `path` trỏ tới + code quyết định số + tham số lượt —
để bản ghi lượt nói được "đã tính với đúng cái gì", không chỉ "phiên bản số mấy" (chủ đề 17 mục 2.2b, hàng "sửa khi tạm dừng").

Chạy từ gốc repo:  .venv/bin/python -m kpi_agent.workflow.runner kpi_agent/workflows/kpi_thuong_nvbh.yaml --cutoff 2026-10-20 [--check-only] [--json]
Mã thoát: 0 = chạy xong (hoặc --check-only hợp lệ) · 1 = dừng giữa chừng · 2 = từ chối: file sai / không đọc được, thiếu --cutoff, tham số dòng lệnh sai · 3 = dừng ở cổng người duyệt (chưa soát)."""
import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from kpi_agent.workflow.blocks import BLOCKS, REPO_ROOT, BlockStop, GateWaiting, param_problems, run_block

WORKFLOW_KEYS = {"workflow", "nodes", "edges"}
NODE_KEYS = {"id", "type", "params", "position"}   # position {x, y}: chỗ vẽ khối cho giao diện bước 2 — không bắt buộc, không đổi kết quả
EDGE_KEYS = {"from_node", "from_port", "to_node", "to_port"}
# Code quyết định số của một lượt: lõi tính + khối + bộ chạy. Đổi file khác (agent, trang, test) không làm đổi dấu vân tay.
AGENT_BLOCK_FILE = REPO_ROOT / "kpi_agent/workflow/agent_block.py"
CODE_FILES = sorted((REPO_ROOT / "kpi_agent/core").glob("*.py")) + \
    sorted(path for path in (REPO_ROOT / "kpi_agent/workflow").glob("*.py") if path != AGENT_BLOCK_FILE)
# Khối agent: thêm code agent + hàm công cụ của backend (khối gọi thẳng các hàm đó) — chỉ vào dấu vân tay lượt có khối gọi mô hình
AI_CODE_FILES = [AGENT_BLOCK_FILE] + sorted((REPO_ROOT / "kpi_agent/agent").glob("*.py")) + sorted((REPO_ROOT / "kpi_agent/backend").glob("*.py"))
DEFAULT_MASK = REPO_ROOT / "kpi_agent/data/ma_che_ten.json"
DEFAULT_WORKFLOW = REPO_ROOT / "kpi_agent/workflows/kpi_thuong_nvbh.yaml"
# Dạng mã NV thật (đếm trên file chỉ tiêu tháng 10: 14 / 14 mã khớp dạng này). Lưới thứ hai: mã không có trong bảng che (vd mã lạ trong
# Rawdata — check_data báo đúng các mã đó) vẫn không được lộ.
REAL_CODE_PATTERN = re.compile(r"\b\d{2}[A-Z]{2}\.[A-Z]{2}\d{4,5}\b")


@dataclass
class RunResult:
    status: str                                   # "ok" · "stopped" (dừng giữa chừng) · "awaiting_review" (dừng ở cổng chờ duyệt — không lỗi) · "rejected" (file sai, 0 khối chạy)
    errors: list[str] = field(default_factory=list)   # đã che mã
    steps: list[dict] = field(default_factory=list)   # mỗi khối: node · type · status (ok / stopped / awaiting_review / not_run) · seconds · error
    outputs: dict = field(default_factory=dict)       # node id ⇒ {cổng ra: giá trị} — MÃ THẬT, chỉ backend / test dùng, không in ra
    fingerprint: dict = field(default_factory=dict)


class _NoDuplicateKeysLoader(yaml.SafeLoader):
    """Bộ đọc YAML từ chối khoá ghi 2 lần trong cùng một bảng.

    Vì sao: `safe_load` mặc định lấy giá trị SAU, im lặng — `{npp_turnover_pct: 130, npp_turnover_pct: null}` chạy với trống
    mà bước kiểm vẫn báo hợp lệ. Cùng lớp lỗi "ô sai tên bị bỏ qua" của S1; chat soạn file (bước 3) rất dễ ghi trùng.
    
    """
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(None, None, f"khoá '{key}' ghi 2 lần trong cùng một bảng", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_workflow(path: str | Path) -> tuple[dict, str]:
    """Đọc file workflow ⇒ (nội dung đã đọc, chữ gốc). Chữ gốc giữ lại vì phiên bản + dấu vân tay băm đúng chữ đó.

    Đọc BYTE rồi giải mã (không `read_text`): `read_text` đổi CRLF ⇒ LF nên hai file khác byte ra cùng mã băm.
    Lỗi đọc (không có file, không phải UTF-8, YAML hỏng, khoá trùng) ⇒ ném `WorkflowFileError`.
    
    """
    try:
        text = Path(path).read_bytes().decode("utf-8")
        return yaml.load(text, Loader=_NoDuplicateKeysLoader), text
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise WorkflowFileError(f"không đọc được file workflow: {error}") from error
    except TypeError as error:
        # S2-2 (minor): khoá YAML là danh sách (`? [a, b]: 1`) không băm được ⇒ TypeError ở bộ đọc ⇒ từ chối gọn, không traceback
        raise WorkflowFileError(f"không đọc được file workflow: khoá không phải chữ / số ({error})") from error


class WorkflowFileError(Exception):
    """File workflow không đọc được — bên gọi coi như bị từ chối (dòng lệnh: mã thoát 2)."""


def check_workflow(workflow) -> list[str]:
    """Mọi lỗi của file workflow (rỗng = hợp lệ). Không chạy khối nào, không đọc file dữ liệu."""
    if not isinstance(workflow, dict):
        return ["file workflow phải là một bảng có workflow / nodes / edges"]
    problems = [f"file có trường lạ: {key}" for key in sorted(set(workflow) - WORKFLOW_KEYS)]
    problems += [f"file thiếu trường: {key}" for key in sorted(WORKFLOW_KEYS - set(workflow))]
    nodes, edges = workflow.get("nodes"), workflow.get("edges")
    if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
        return problems + ["nodes phải là danh sách khác rỗng, edges phải là danh sách"]

    position, types = {}, {}   # node id ⇒ vị trí trong file (= thứ tự chạy) · node id ⇒ loại khối (chỉ khối đầu tiên của mỗi id)
    for index, node in enumerate(nodes):
        where = f"khối số {index + 1}"
        if not isinstance(node, dict) or set(node) - NODE_KEYS or not {"id", "type"} <= set(node):
            problems.append(f"{where}: chỉ nhận các trường id, type, params, position (bắt buộc id + type)")
            continue
        node_id, block_type = node["id"], node["type"]
        if not isinstance(node_id, str) or not node_id or not isinstance(block_type, str):
            # S2 (minor 2): `type: [compute_kpi]` từng ném TypeError (danh sách không tra được trong bảng) ⇒ traceback, mã thoát sai
            problems.append(f"{where}: id và type phải là chữ khác rỗng")
            continue
        if node_id in position:
            problems.append(f"{where}: id '{node_id}' trùng với khối số {position[node_id] + 1}")
            continue
        position[node_id], types[node_id] = index, block_type
        if "position" in node and not _is_point(node["position"]):
            problems.append(f"khối '{node_id}': position phải là {{x: số, y: số}}, nhận {node['position']!r}")
        if block_type not in BLOCKS:
            problems.append(f"khối '{node_id}': loại '{block_type}' không có trong danh bạ (có: {', '.join(BLOCKS)})")
            continue
        params = node.get("params") or {}
        if not isinstance(params, dict):
            problems.append(f"khối '{node_id}': params phải là bảng ô ⇒ giá trị")
            continue
        problems += [f"khối '{node_id}': {problem}" for problem in param_problems(block_type, params)]

    wired = {}      # (khối đích, cổng vào) ⇒ số dây
    for index, edge in enumerate(edges):
        where = f"dây số {index + 1}"
        if not isinstance(edge, dict) or set(edge) != EDGE_KEYS or not all(isinstance(value, str) for value in edge.values()):
            problems.append(f"{where}: phải có đúng 4 trường {', '.join(sorted(EDGE_KEYS))}, mỗi trường là chữ")
            continue
        source, target = edge["from_node"], edge["to_node"]
        if source not in position or target not in position:
            missing = [name for name in (source, target) if name not in position]
            problems.append(f"{where}: trỏ khối không có: {', '.join(map(str, missing))}")
            continue
        if types[source] in BLOCKS and edge["from_port"] not in BLOCKS[types[source]].outputs:
            problems.append(f"{where}: khối '{source}' không có cổng ra '{edge['from_port']}' (có: {', '.join(BLOCKS[types[source]].outputs)})")
        source_block, target_block = BLOCKS.get(types[source]), BLOCKS.get(types[target])
        to_tools = target_block is not None and edge["to_port"] in target_block.tool_inputs
        if target_block is not None and edge["to_port"] not in target_block.inputs + target_block.tool_inputs:
            problems.append(f"{where}: khối '{target}' không có cổng vào '{edge['to_port']}' "
                            f"(có: {', '.join(target_block.inputs + target_block.tool_inputs) or 'không cổng nào'})")
        # Dây công cụ (09/11): loại dây suy từ cổng đích — cổng công cụ chỉ nhận khối kpi_tool, khối kpi_tool chỉ cắm vào cổng công cụ
        if to_tools and types[source] != "kpi_tool":
            problems.append(f"{where}: cổng công cụ '{edge['to_port']}' của khối '{target}' chỉ nhận dây từ khối kpi_tool, nhận từ '{source}' ({types[source]})")
        if types[source] == "kpi_tool" and target_block is not None and edge["to_port"] in target_block.inputs:
            problems.append(f"{where}: khối '{source}' là khối công cụ — chỉ cắm vào cổng công cụ, không vào cổng dữ liệu '{edge['to_port']}'")
        if source_block is not None and source_block.uses_ai:
            # #4: chữ / số do AI viết không được đi vào khối tính tiền — mốc này cấm mọi dây ra từ khối gọi mô hình
            problems.append(f"{where}: khối '{source}' gọi mô hình — chưa được nối ra khối khác")
        if position[source] >= position[target]:
            # Luật chiều dây (Capitol không có bằng code — chủ đề 17 mục 2.2b): nguồn phải đứng TRƯỚC đích ⇒ không vòng, thứ tự file = thứ tự chạy
            problems.append(f"{where}: dây ngược — '{source}' (khối số {position[source] + 1}) phải đứng trước '{target}' (khối số {position[target] + 1})")
        key = (target, edge["to_port"])
        wired[key] = wired.get(key, 0) + 1

    for (target, port), count in wired.items():
        tool_ports = BLOCKS[types[target]].tool_inputs if types[target] in BLOCKS else ()
        if count > 1 and port not in tool_ports:   # cổng công cụ nhận nhiều dây (mỗi dây một công cụ)
            problems.append(f"khối '{target}' cổng vào '{port}' có {count} dây — mỗi cổng vào đúng một dây")
    for node_id, block_type in types.items():
        if block_type in BLOCKS:
            for port in BLOCKS[block_type].inputs + BLOCKS[block_type].tool_inputs:
                if (node_id, port) not in wired:
                    problems.append(f"khối '{node_id}' cổng vào bắt buộc '{port}' chưa có dây")
    return problems

# tham số lượt ⇒ kiểu (ngày chốt là `date`, không phải chữ; câu hỏi của khối agent là chữ — như Capitol: câu hỏi không nằm trong file)
RUN_PARAM_TYPES = {"cutoff": date, "question": str}


def check_run_params(workflow: dict, run: dict) -> list[str]:
    """Tham số lượt các khối cần (vd ngày chốt) phải có + đúng kiểu — kiểm TRƯỚC khi chạy khối nào.

    Vì sao: thiếu ngày chốt từng chạy xong 3 khối đọc file rồi mới dừng ở `check`; kiểm file hợp lệ chưa đủ để chạy.
    
    """
    needed = {key for node in workflow["nodes"] for key in BLOCKS[node["type"]].run_params}
    problems = [f"thiếu tham số lượt: {key}" for key in sorted(needed - set(run))]
    problems += [f"tham số lượt {key} phải là {RUN_PARAM_TYPES[key].__name__}, nhận {run[key]!r}"
                 for key in sorted(needed & set(run)) if key in RUN_PARAM_TYPES and not isinstance(run[key], RUN_PARAM_TYPES[key])]
    if "question" in needed and isinstance(run.get("question"), str) and not run["question"].strip():
        problems.append("tham số lượt question là chữ rỗng")
    return problems

def _is_point(value) -> bool:
    # Bộ chạy chỉ kiểm kiểu rồi bỏ qua position (giao diện bước 2a vẽ sơ đồ từ đây)
    return isinstance(value, dict) and set(value) == {"x", "y"} and \
        all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value.values())


def load_mask(path: Path = DEFAULT_MASK) -> dict:
    """Bảng che {mã thật: NVxx} từ `kpi_agent/data/ma_che_ten.json` (Git bỏ qua). Không có file ⇒ chỉ còn lưới theo dạng mã."""
    if not path.exists():
        return {}
    return {real: masked for masked, real in json.loads(path.read_text(encoding="utf-8"))["map"].items()}


def mask_codes(text: str, to_mask: dict) -> str:
    for real, masked in to_mask.items():
        text = text.replace(real, masked)
    return REAL_CODE_PATTERN.sub("NV??", text)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def workflow_sha256(workflow: dict) -> str:
    """Mã băm LOGIC của workflow: bỏ mọi `position`, dump sắp khoá ⇒ chỉ khối / ô / dây quyết định mã băm.

    Vì sao: `position` là bố cục. Băm cả nó thì kéo một khối trên giao diện (bước 2) ⇒ phiên bản mới ⇒ (S3, file workflow
    vào dấu vân tay bản tính) bản tính mới thay bản cũ, mất "Đã soát" — dù không một con số nào đổi. Gọi trên file đã qua `check_workflow`.
    
    """
    logic = {**workflow, "nodes": [{key: value for key, value in node.items() if key != "position"} for node in workflow["nodes"]]}
    return _sha256(json.dumps(logic, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))


def input_files(workflow: dict) -> dict[str, Path]:
    """{giá trị ô: đường file} của MỌI ô cài đặt là file đầu vào (`Block.file_params`), theo thứ tự khối trong file workflow.

    MỘT nguồn cho hai dấu vân tay (12/11): dấu vân tay lượt (`input_fingerprint` — cổng người duyệt tra theo nó) và danh sách file của bản tính ở
    backend (`files_sha256`, `data_version`). Trước đó mỗi bên tự kể file và cả hai đều sót file danh sách tuyến."""
    return {value: repo_path(value) for node in workflow["nodes"] for key in BLOCKS[node["type"]].file_params
            if (value := (node.get("params") or {}).get(key))}


def input_fingerprint(workflow: dict, run: dict) -> dict:
    """Đầu vào THẬT của một lượt: workflow (mã băm logic, không tính position) + NỘI DUNG từng file đầu vào (`input_files`) + code + tham số lượt.
    Gộp thành `sha256`."""
    digest = hashlib.sha256()
    # Có khối gọi mô hình ⇒ code agent (lời dặn, mô tả công cụ, tên mô hình, kiểm số) cũng quyết kết quả (#3). Chỉ thêm khi có khối đó:
    # thêm cho mọi workflow thì sửa agent làm đổi dấu vân tay bản tính thưởng ⇒ bản tính mới, mất "Đã soát" dù không số nào đổi.
    has_ai = any(BLOCKS[node["type"]].uses_ai for node in workflow["nodes"])
    for path in CODE_FILES + (AI_CODE_FILES if has_ai else []):
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    files = {value: _sha256(path.read_bytes()) if path.exists() else "không có file" for value, path in input_files(workflow).items()}
    parts = {"workflow_sha256": workflow_sha256(workflow), "files_sha256": files, "code_sha256": digest.hexdigest(),
             "run": {key: str(value) for key, value in sorted(run.items())}}
    return {**parts, "sha256": _sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8"))}


def run_workflow(workflow: dict, run: dict, to_mask: dict | None = None, approved_gates: frozenset = frozenset()) -> RunResult:
    """Kiểm file ⇒ chạy lần lượt ⇒ dừng ở lỗi đầu tiên. `run` = tham số lượt (vd {"cutoff": date}). `to_mask` mặc định đọc bảng che.

    `approved_gates` = id các khối người duyệt đã được duyệt (backend tra bản tính đã soát). Để NGOÀI `run`: duyệt là sự kiện về bản tính,
    không phải đầu vào của nó ⇒ không vào dấu vân tay (cùng số ⇒ cùng dấu ⇒ tra đúng bản đã soát). Cổng chưa duyệt ⇒ lượt "awaiting_review".
    Chữ gốc của file (từ `load_workflow`) không cần ở đây: dấu vân tay dùng mã băm logic; chữ gốc chỉ để lưu vào `workflow_version` (S3)."""
    if not isinstance(approved_gates, (set, frozenset)):
        # (10/11): chuỗi "xduyetx" thì `id in chuỗi` = so chuỗi con ⇒ mở nhầm cổng. Chỉ nhận tập id
        raise TypeError(f"approved_gates phải là set / frozenset id khối, nhận {type(approved_gates).__name__}")
    to_mask = load_mask() if to_mask is None else to_mask
    problems = check_workflow(workflow) or check_run_params(workflow, run)
    if problems:
        return RunResult("rejected", errors=[mask_codes(problem, to_mask) for problem in problems])

    result = RunResult("ok", fingerprint=input_fingerprint(workflow, run))
    for node in workflow["nodes"]:
        step = {"node": node["id"], "type": node["type"], "status": "not_run", "seconds": None, "error": None}
        result.steps.append(step)
        if result.status != "ok":
            continue
        tool_ports = BLOCKS[node["type"]].tool_inputs
        inputs = {port: [] for port in tool_ports}   # cổng công cụ: gom MỌI dây thành danh sách tên công cụ (theo thứ tự dây trong file)
        for edge in workflow["edges"]:
            if edge["to_node"] == node["id"]:
                value = result.outputs[edge["from_node"]][edge["from_port"]]
                if edge["to_port"] in tool_ports:
                    inputs[edge["to_port"]].append(value)
                else:
                    inputs[edge["to_port"]] = value
        started = time.perf_counter()
        try:
            result.outputs[node["id"]] = run_block(node["type"], inputs, node.get("params") or {}, {**run, "approved": node["id"] in approved_gates})
            step["status"] = "ok"
        except GateWaiting as waiting:
            # Dừng ở cổng: KHÔNG phải lỗi ⇒ không vào `errors`; khối sau `not_run`; số tới cổng vẫn đủ trong `outputs`
            step["status"], step["error"] = "awaiting_review", str(waiting)
            result.status = "awaiting_review"
        except Exception as error:   # BlockStop = dữ liệu / luật hỏng; lỗi khác (file không có, lỗi code) cũng dừng — không chạy tiếp trên kết quả không tin được
            message = str(error) if isinstance(error, BlockStop) else f"{type(error).__name__}: {error}"
            step["status"], step["error"] = "stopped", mask_codes(message, to_mask)
            if isinstance(error, BlockStop) and "issues" in error.partial:
                # S2-2 (minor): khối dừng thì không trả cổng ra ⇒ các mục warning / info mất. Giữ lại trong bước (đã che) — vào --json + workflow_run
                step["issues"] = masked_issues(error.partial["issues"], to_mask)
            result.status = "stopped"
            result.errors.append(f"khối '{node['id']}': {step['error']}")
        step["seconds"] = round(time.perf_counter() - started, 3)
    return result


def over_target_summary(result: RunResult) -> dict | None:
    """Thưởng vượt (NPP chi — trường riêng, KHÔNG nằm trong team_total): tổng tiền + số NV "thiếu dữ kiện". C xin 04/11 để hộp đen thấy ô NPP."""
    for outputs in result.outputs.values():
        if "bonuses" in outputs:
            amounts = [bonus["over_target_bonus"] for bonus in outputs["bonuses"].values()]
            return {"total": sum(a for a in amounts if a is not None), "missing_data": sum(a is None for a in amounts)}
    return None


def masked_issues(issues: list[dict], to_mask: dict) -> list[dict]:
    return [{"level": issue["level"], "message": mask_codes(issue["message"], to_mask)} for issue in issues]

def repo_path(value: str) -> Path:
    # Đường trong file workflow tính từ gốc repo — cùng luật với blocks._path (khối đọc file)
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def node_param(workflow: dict, block_type: str, key: str):
    """Ô cài đặt của khối DUY NHẤT thuộc loại `block_type` (vd đường file luật) — backend đọc từ file workflow, không giữ bản sao đường dẫn."""
    nodes = [node for node in workflow["nodes"] if node["type"] == block_type]
    if len(nodes) != 1:
        raise ValueError(f"workflow cần đúng một khối {block_type}, có {len(nodes)}")
    return (nodes[0].get("params") or {}).get(key, BLOCKS[block_type].params.get(key))


def output_of(workflow: dict, result: RunResult, block_type: str, port: str):
    """Cổng ra `port` của khối duy nhất thuộc loại `block_type` trong lượt đã chạy xong."""
    node_id = next(node["id"] for node in workflow["nodes"] if node["type"] == block_type)
    return result.outputs[node_id][port]


def data_block_type(workflow: dict) -> str:
    """Loại khối đọc số thực hiện của workflow: `read_raw` (RAW DATA, 10/11) nếu có, không thì `read_tracking` (sổ Tracking) — cùng cổng ra `tracking`.
    Ca thật 10/11 (E): backend tra cổng ra theo loại `read_tracking` cứng ⇒ workflow RAW không bật được (StopIteration) — test chỉ chạy workflow Tracking."""
    return "read_raw" if any(node["type"] == "read_raw" for node in workflow["nodes"]) else "read_tracking"


def gate_ids(workflow: dict) -> frozenset:
    """Id mọi khối người duyệt trong file — backend mở cả các cổng này khi bản tính của lượt đã được soát (một lần soát = một bản tính)."""
    return frozenset(node["id"] for node in workflow["nodes"] if node["type"] == "review_gate")


def team_total(result: RunResult) -> int | None:
    """Tổng lương kinh doanh cả đội của lượt `ok` (để in tóm tắt — không lộ mã NV)."""
    for node_id, outputs in result.outputs.items():
        if "bonuses" in outputs:
            return sum(bonus["business_salary"] for bonus in outputs["bonuses"].values())
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kiểm + chạy một file workflow khối KPI")
    parser.add_argument("workflow", help="đường file .yaml (tính từ thư mục đang đứng)")
    # Không có mặc định: ngày chốt là tham số MỖI lượt — mặc định ngầm 20/10 từng làm lượt quên ghi ngày vẫn chạy
    parser.add_argument("--cutoff", type=date.fromisoformat, help="ngày chốt YYYY-MM-DD — bắt buộc khi chạy (tham số lượt)")
    parser.add_argument("--check-only", action="store_true", help="chỉ kiểm file, không chạy khối nào (không cần --cutoff)")
    parser.add_argument("--json", action="store_true", help="in tóm tắt JSON (không có số theo NV)")
    parser.add_argument("--question", help="câu hỏi cho khối agent (tham số lượt) — chỉ cần khi workflow có khối kpi_agent")
    args = parser.parse_args(argv)

    try:
        workflow, text = load_workflow(args.workflow)
    except WorkflowFileError as error:
        # S2-2 (minor): với --json mọi ca từ chối đều in JSON — kể cả file không đọc được
        message = mask_codes(str(error), load_mask())
        print(json.dumps({"status": "rejected", "errors": [message]}, ensure_ascii=False, indent=2) if args.json else f"TỪ CHỐI — {message}")
        return 2
    to_mask = load_mask()
    if args.check_only:
        problems = [mask_codes(problem, to_mask) for problem in check_workflow(workflow)]
        result = RunResult("rejected" if problems else "checked", errors=problems)
    elif args.cutoff is None:
        # dừng trước khi đọc file dữ liệu nào
        result = RunResult("rejected", errors=["thiếu --cutoff (ngày chốt YYYY-MM-DD) — ngày chốt là tham số mỗi lượt, không có mặc định"])
    else:
        run = {"cutoff": args.cutoff, **({"question": args.question} if args.question is not None else {})}
        result = run_workflow(workflow, run, to_mask)

    issues = next((masked_issues(outputs["issues"], to_mask) for outputs in result.outputs.values() if "issues" in outputs),
                  next((step["issues"] for step in result.steps if "issues" in step), None))
    summary = {"status": result.status, "errors": result.errors, "cutoff": args.cutoff.isoformat() if args.cutoff else None,
               "steps": result.steps, "team_total": team_total(result), "over_target": over_target_summary(result), "fingerprint": result.fingerprint,
               # 04/11: hộp đen phải thấy được ngày chốt có tác dụng ⇒ in mục kiểm dữ liệu (đã che mã)
               "issues": issues,
               # khối agent: chữ trả lời (người đọc) + các lần gọi công cụ + tiền — kết quả công cụ đã che mã, không in ở đây cho gọn
               "answers": {node_id: {key: outputs["answer"][key] for key in ("answer", "tool_calls", "stop", "usd")}
                           for node_id, outputs in result.outputs.items() if "answer" in outputs}}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif result.status == "rejected":
        print("TỪ CHỐI — 0 khối đã chạy:")
        print("\n".join(f"  - {error}" for error in result.errors))
    elif result.status == "checked":
        print("KIỂM FILE: hợp lệ (chưa chạy khối nào — --check-only)")
    else:
        for step in result.steps:
            print(f"  {step['node']:<9} {step['type']:<14} {step['status']:<8} {step['seconds'] if step['seconds'] is not None else '-'} s")
        if summary["issues"] is not None:
            print(f"  kiểm dữ liệu: {len(summary['issues'])} mục (" + ", ".join(f"{issue['level']}" for issue in summary["issues"]) + ")")
        if result.status == "stopped":
            print("DỪNG — " + "\n".join(result.errors))
        elif result.status == "awaiting_review":
            # Dòng lệnh không có cách duyệt (duyệt = nút "Đã soát" trên trang) ⇒ lượt có cổng luôn dừng ở đây
            node = next(step["node"] for step in result.steps if step["status"] == "awaiting_review")
            print(f"CHỜ DUYỆT — khối '{node}': bảng thưởng chưa được soát; các khối sau chưa chạy")
        else:
            total = "-" if summary["team_total"] is None else f"{summary['team_total']:,} đ"
            print(f"XONG — ngày chốt {args.cutoff:%d/%m/%Y}, tổng lương kinh doanh cả đội {total}, "
                  f"dấu vân tay {result.fingerprint['sha256'][:12]}")
            for node_id, answer in summary["answers"].items():
                calls = ", ".join(call["name"] for call in answer["tool_calls"]) or "không gọi công cụ"
                print(f"  [{node_id}] ({calls}; {answer['usd']:.5f} USD)\n{answer['answer']}")
    return {"ok": 0, "checked": 0, "stopped": 1, "awaiting_review": 3}.get(result.status, 2)


if __name__ == "__main__":
    sys.exit(main())
