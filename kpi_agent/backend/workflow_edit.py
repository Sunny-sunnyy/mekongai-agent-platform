"""Bước 2b — sửa / kiểm / lưu / chạy thử workflow từ trang kéo–thả ( bản 2;
09/11: chạy THỬ không thay số đang dùng · lưu SQLite `workflow_version` · chép code trang).

Vì sao file riêng trong `backend/`, không sửa `kpi_agent/workflow/*.py`: mọi file ở đó nằm trong dấu vân tay code của bản tính (`main.CODE_FILES`)
⇒ sửa bộ chạy sẽ đẻ bản tính mới, mất "Đã soát", dù không con số nào đổi (như `workflow_view.py`).

Ba việc:
1. **Đường file chỉ trong danh sách cho phép** (`path_problems`). Trước 2b, ô `path` chỉ đến từ YAML trong repo; từ nay đến từ trình duyệt (và bước 3: chat
   tạo workflow) ⇒ `blocks._path` nhận đường tuyệt đối / `..` sẽ đọc được mọi file máy đọc được (`../.env`). Luật: chuỗi `path` phải TRÙNG KHÍT một mục
   của `allowed_files()` — không giải mã, không ghép, không mở file nào để kiểm ⇒ `../`, `/etc/passwd`, `%2e%2e` đều trượt mà 0 lần đọc file.
2. **Chuẩn hoá trước khi kiểm** (`normalize`): người dùng tạo khối theo thứ tự bất kỳ, bộ chạy lại chạy theo thứ tự file và từ chối dây ngược
   (`runner.py:140`) ⇒ backend xếp khối theo dây (thứ tự tô-pô), dây theo khối đích + thứ tự cổng vào, ô cài đặt điền mặc định. Cùng sơ đồ ⇒ cùng
   mã băm logic dù tạo khối / nối dây theo thứ tự nào (phép thử T1: ráp lại bằng tay ra đúng `kpi_thuong_nvbh@aa67f7dc`).
   Xếp ở backend, không ở trang (kế hoạch ghi trang): một chỗ cho cả trang lẫn chat bước 3.
3. **Bọc lời kiểm thành `CheckItem`** cho trang (`check_itemsapi.ts` `CheckItem`)."""
import re
from pathlib import Path

from kpi_agent.workflow import runner
from kpi_agent.workflow.blocks import BLOCKS, REPO_ROOT, REQUIRED

# Thư mục được đọc từ trang (kế hoạch mục 3). Chỉ file nằm THẲNG trong thư mục (không đi thư mục con), đúng đuôi khối đọc file dùng.
ALLOWED_DIRS = ("kpi_agent/config", "sample_data/thang_10")
ALLOWED_SUFFIXES = (".yaml", ".xlsx")
# Lời của runner.check_workflow nêu khối theo 2 kiểu: "khối '<id>'" và "khối số N: id '<id>' trùng …" (11:00, minor 2 — kiểu sau từng ra node None)
NODE_IN_MESSAGE = re.compile(r"khối '([^']+)'|id '([^']+)' trùng")


def allowed_files(dirs: tuple[str, ...] = ALLOWED_DIRS, root: Path = REPO_ROOT) -> list[str]:
    """Đường tương đối (tính từ gốc repo, như trong file workflow) của mọi file được phép.

    Bỏ symlink trỏ ra ngoài thư mục: so thư mục cha SAU `resolve()` với thư mục cho phép đã `resolve()` (thư mục cho phép là symlink — vd worktree — vẫn được)."""
    files = []
    for folder in dirs:
        base = (root / folder).resolve()
        if not base.is_dir():
            continue
        for path in sorted((root / folder).iterdir()):
            if path.suffix.lower() in ALLOWED_SUFFIXES and path.is_file() and path.resolve().parent == base:
                files.append(f"{folder}/{path.name}")
    return files


def path_problems(workflow, allowed: list[str]) -> list[str]:
    """Ô `path` nằm ngoài danh sách ⇒ lỗi. Không mở / stat file nào (chỉ so chuỗi)."""
    if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list):
        return []   # hình sai — check_workflow tự báo
    problems = []
    for node in workflow["nodes"]:
        params = node.get("params") if isinstance(node, dict) else None
        if isinstance(params, dict) and "path" in params and params["path"] not in allowed:
            node_id = node.get("id") if isinstance(node.get("id"), str) else "?"
            problems.append(f"khối '{node_id}': ô path {params['path']!r} không nằm trong danh sách file được đọc "
                            f"(chỉ file trong {', '.join(ALLOWED_DIRS)})")
    return problems


def _sane_shape(workflow) -> bool:
    # Chỉ chuẩn hoá khi hình đủ đúng để xếp; hình sai thì để nguyên cho check_workflow báo lỗi bằng lời của nó
    if not isinstance(workflow, dict) or not isinstance(workflow.get("nodes"), list) or not isinstance(workflow.get("edges"), list):
        return False
    nodes_ok = all(isinstance(n, dict) and isinstance(n.get("id"), str) and n.get("type") in BLOCKS for n in workflow["nodes"])
    ids = [n["id"] for n in workflow["nodes"]] if nodes_ok else []
    edges_ok = all(isinstance(e, dict) and e.get("from_node") in ids and e.get("to_node") in ids for e in workflow["edges"])
    return nodes_ok and edges_ok and len(ids) == len(set(ids))


def normalize(workflow) -> tuple[object, list[str]]:
    """(workflow đã chuẩn hoá, lỗi vòng). Hình sai ⇒ trả nguyên, không lỗi (check_workflow báo).

    Thứ tự khối: tô-pô theo dây; giữa các khối cùng sẵn sàng chọn theo thứ tự danh bạ `BLOCKS` rồi theo id ⇒ ra đúng thứ tự file `kpi_thuong_nvbh.yaml`.
    Thứ tự dây: theo khối đích rồi theo thứ tự cổng vào khai báo. Ô cài đặt: khối có ô ⇒ đủ mọi ô (thiếu thì điền mặc định, ô REQUIRED thiếu thì để
    thiếu cho check báo); khối không có ô ⇒ bỏ khoá `params` (như file gốc). Ô lạ giữ nguyên để check báo."""
    if not _sane_shape(workflow):
        return workflow, []
    nodes, edges = workflow["nodes"], workflow["edges"]
    catalog_order = {block_type: index for index, block_type in enumerate(BLOCKS)}
    parents = {node["id"]: {edge["from_node"] for edge in edges if edge["to_node"] == node["id"]} for node in nodes}
    by_id = {node["id"]: node for node in nodes}
    ordered, done = [], set()
    while len(ordered) < len(nodes):
        ready = [node for node in nodes if node["id"] not in done and parents[node["id"]] <= done]
        if not ready:
            stuck = sorted(node["id"] for node in nodes if node["id"] not in done)
            return workflow, [f"dây tạo vòng giữa các khối: {', '.join(stuck)} — workflow phải chạy một chiều"]
        first = min(ready, key=lambda node: (catalog_order[node["type"]], node["id"]))
        ordered.append(first)
        done.add(first["id"])

    position = {node["id"]: index for index, node in enumerate(ordered)}
    port_order = {node["id"]: {port: i for i, port in enumerate(BLOCKS[node["type"]].inputs)} for node in nodes}
    sorted_edges = sorted(edges, key=lambda e: (position[e["to_node"]], port_order[e["to_node"]].get(e.get("to_port"), 99), position[e["from_node"]]))

    out_nodes = []
    for node in ordered:
        block = BLOCKS[node["type"]]
        clean = {"id": node["id"], "type": node["type"]}
        params = node.get("params") or {}
        if block.params or params:
            filled = {name: params.get(name, default) for name, default in block.params.items() if name in params or default is not REQUIRED}
            filled.update({name: value for name, value in params.items() if name not in block.params})   # ô lạ ⇒ check báo
            clean["params"] = filled
        if "position" in node:
            clean["position"] = node["position"]
        for key in set(node) - {"id", "type", "params", "position"}:
            clean[key] = node[key]   # trường lạ (vd `label`) giữ ⇒ check_workflow từ chối, không bỏ lặng lẽ
        out_nodes.append(clean)
    return {**workflow, "nodes": out_nodes, "edges": sorted_edges}, []


def check_items(workflow, allowed: list[str], to_mask: dict) -> tuple[object, list[dict]]:
    """(workflow đã chuẩn hoá, danh sách lỗi theo khuôn CheckItem của trang). Rỗng = lưu / chạy được."""
    normalized, cycle = normalize(workflow)
    problems = cycle or runner.check_workflow(normalized)
    name = normalized.get("workflow") if isinstance(normalized, dict) else None
    if "workflow" in (normalized if isinstance(normalized, dict) else {}) and not (isinstance(name, str) and 1 <= len(name.strip()) <= 80):
        problems.append("tên workflow phải là chữ 1–80 ký tự")   # bộ chạy không kiểm tên; tên vào bảng phiên bản + nhãn trang
    problems += path_problems(normalized, allowed)
    items = []
    for problem in problems:
        message = runner.mask_codes(problem, to_mask)
        match = NODE_IN_MESSAGE.search(message)
        node = (match.group(1) or match.group(2)) if match else None
        field = "path" if "ô path" in message else None
        items.append({"muc": "loi", "code": "path_not_allowed" if field else "workflow_invalid", "node": node,
                      "field": field, "message": message,
                      "suggestion": "chọn file trong danh sách của ô" if field else ""})
    return normalized, items
