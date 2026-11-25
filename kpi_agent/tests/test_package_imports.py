"""Gói chạy của backend (`kpi_agent/{core,backend,agent,workflow}`) không được import `kpi_agent.scripts` (12/11).

Vì sao: hộp backend bên Capitol dựng từ ảnh chụp commit không có thư mục `scripts/` trong gói chạy ⇒ `/api/system/spice-loyalty` 500
ModuleNotFoundError (`backend/main.py` và `backend/tracking_view.py` mượn hàm từ `scripts/tach_mcp_trang_sep.py`). Ca thật của mình luôn chạy từ
working tree (có đủ thư mục) nên không bắt được ⇒ quét import bằng code. Chiều đúng: `scripts/` gọi vào gói chạy.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ("core", "backend", "agent", "workflow")


def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):   # ast.walk đi cả import đặt TRONG hàm (đúng kiểu lỗi đã gặp)
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_runtime_package_never_imports_scripts():
    files = [path for folder in RUNTIME for path in sorted((ROOT / "kpi_agent" / folder).glob("*.py"))]
    assert len(files) > 30                                                    # quét đúng chỗ (không phải thư mục rỗng)
    bad = {str(path.relative_to(ROOT)): sorted(name for name in _imports(path) if name.startswith("kpi_agent.scripts"))
           for path in files}
    assert {path: names for path, names in bad.items() if names} == {}


def test_scanner_sees_an_import_inside_a_function(tmp_path):
    # Ca xấu của chính phép quét: import đặt trong thân hàm (như `main._compute_spice` trước khi sửa) phải bị thấy
    sample = tmp_path / "x.py"
    sample.write_text("def f():\n    from kpi_agent.scripts.tach_mcp_trang_sep import raw_employee_names\n    return raw_employee_names\n", encoding="utf-8")
    assert "kpi_agent.scripts.tach_mcp_trang_sep" in _imports(sample)


def test_missing_targets_file_is_a_named_data_error_not_a_bare_file_error(tmp_path):
    # Hộp Capitol thiếu `chi_tieu_meat_3110.json` (Git bỏ qua) từng chết lúc bật vì FileNotFoundError trần; nay ValueError nêu TÊN FILE
    # ⇒ `main._extra_bonus` giữ làm lời từ chối của Thưởng thêm, 8 KPI vẫn chạy
    from kpi_agent.core.compute_extra_bonus import TARGET_MEASURES, load_item_targets
    rules = {"items": {"T99": {"measure": sorted(TARGET_MEASURES)[0], "targets_file": "kpi_agent/data/khong_co_file_nay.json"}}}
    with pytest.raises(ValueError, match=r"T99: không đọc được file chỉ tiêu kpi_agent/data/khong_co_file_nay\.json"):
        load_item_targets(rules, resolve=lambda name: tmp_path / name)
    broken = tmp_path / "hong.json"
    broken.write_text("{không phải json", encoding="utf-8")
    with pytest.raises(ValueError, match="không đọc được file chỉ tiêu hong.json"):
        load_item_targets({"items": {"T99": {"measure": sorted(TARGET_MEASURES)[0], "targets_file": "hong.json"}}}, resolve=lambda name: tmp_path / name)
