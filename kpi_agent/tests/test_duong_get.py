"""Danh sách đường GET xuất cho cổng chuyển tiếp chỉ đọc bên Capitol (`scripts/xuat_duong_get.py`, 12/11) — không bật backend, 0 lần gọi AI.

Giữ sàn: (1) không đường có khoá / công cụ AI / xuất file / sửa workflow nào lọt vào danh sách (hộp bên kia từ chối bật, và đó là cửa không được mở);
(2) mỗi đoạn `{tên}` và mỗi tham số đều có regex khớp ĐỦ chuỗi, chặn được chuỗi kiểu đường dẫn; (3) backend thêm đường GET mới thì test này buộc
người thêm phải quyết: vào danh sách (có regex) hay vào tiền tố loại trừ.
"""
import inspect
import re

from fastapi.routing import APIRoute

from kpi_agent.backend import main
from kpi_agent.scripts import xuat_duong_get as export

ROUTES = export.get_routes()


def test_no_system_tool_export_or_workflow_route_is_listed():
    paths = [entry["path"] for entry in ROUTES]
    assert len(paths) >= 15 and len(set(paths)) == len(paths)
    assert not [path for path in paths if path.startswith(export.EXCLUDED_PREFIXES)]
    assert not [path for path in paths if re.match(r"^/api/\{", path)]            # đoạn đầu sau /api/ là tham số ⇒ hộp bên kia không bật
    assert "/api/meta" in paths and "/api/names" in paths and "/api/team-board" in paths


def test_every_get_route_of_the_app_is_listed_or_excluded_on_purpose():
    listed = {entry["path"] for entry in ROUTES}
    for route in main.app.routes:
        if isinstance(route, APIRoute) and "GET" in route.methods:
            shown = export.SEGMENT.sub(lambda found: "{" + found.group(1) + "}", route.path)
            assert shown in listed or route.path.startswith(export.EXCLUDED_PREFIXES), route.path
    post_only = [route.path for route in main.app.routes if isinstance(route, APIRoute) and "GET" not in route.methods]
    assert not listed & set(post_only)                                             # đường ghi (POST) không bao giờ nằm trong danh sách đọc


def test_every_segment_and_query_param_has_a_full_match_regex():
    for entry in ROUTES:
        segments = re.findall(r"\{(\w+)\}", entry["path"])
        assert sorted(entry.get("doan", {})) == sorted(segments), entry["path"]
        route = next(r for r in main.app.routes if isinstance(r, APIRoute) and export.SEGMENT.sub(lambda f: "{" + f.group(1) + "}", r.path) == entry["path"])
        accepted = [name for name in inspect.signature(route.endpoint).parameters if name != "request" and name not in segments]
        assert sorted(entry["tham_so"]) == sorted(accepted), entry["path"]         # đúng các tham số endpoint nhận — không thừa, không thiếu
        for pattern in [*entry.get("doan", {}).values(), *entry["tham_so"].values()]:
            assert pattern.startswith("^") and pattern.endswith("$") and re.compile(pattern)


def test_patterns_accept_real_values_and_refuse_path_like_strings():
    ok = {"employee_id": "NV05", "kpi": "ASO_Focus_2", "period": "2026-10", "run_id": "12", "extra": "200", "target_pct": "94.9"}
    bad = {"employee_id": ["NV05/../NV06", "NV5", "nv05", "NV05\n", "../customers", "NV005"], "kpi": ["../PC_BT", "PC_BT ", "Tong"],
           "period": ["2026-13", "2026-10-01", "../2026-10"], "run_id": ["0", "-1", "1e3", "1/2"], "extra": ["-5", "1e9", "5;drop"],
           "target_pct": ["1000", "94,9", ""]}
    for name, value in ok.items():
        assert re.fullmatch(export.PATTERNS[name], value), (name, value)
    for name, values in bad.items():
        assert not [value for value in values if re.fullmatch(export.PATTERNS[name], value)], name
    assert export.PATTERNS["kpi"].count("|") == len(export.queries.KPI_ORDER) - 1   # đúng các KPI của lõi, liệt kê tường minh


def test_patterns_are_strict_even_with_the_weaker_match_function():
    # Người kiểm độc lập 12/11: `\d` nhận chữ số Unicode; `$` trần + re.match cho "NV05\n" qua. Thử bằng CHÍNH hàm yếu (re.match) trên mọi regex của file
    ok = {"employee_id": "NV05", "employee_a": "NV05", "employee_b": "NV06", "kpi": "LPPC", "period": "2026-10", "run_id": "12", "version_id": "3",
          "extra": "200.5", "target_pct": "94.9"}
    fullwidth = str.maketrans("0123456789", "０１２３４５６７８９")
    used = {name: pattern for entry in ROUTES for name, pattern in {**entry.get("doan", {}), **entry["tham_so"]}.items()}
    assert set(used) <= set(ok)
    for name, pattern in used.items():
        assert "\\d" not in pattern and pattern.endswith(export.END)
        assert re.match(pattern, ok[name]) and re.fullmatch(pattern, ok[name]), name
        for bad in (ok[name] + "\n", ok[name] + "\r\n", ok[name] + " ", "\n" + ok[name], ok[name] + "\n/../x"):
            assert not re.match(pattern, bad) and not re.search(pattern, bad), (name, bad)
        if any(char.isdigit() for char in ok[name]):
            assert not re.match(pattern, ok[name].translate(fullwidth)), name          # chữ số Unicode toàn khổ


def test_config_file_in_repo_is_exactly_what_the_script_exports_now():
    # Một nguồn danh sách: máy chủ agent và cổng bên Capitol cùng đọc `config/duong_get_xem.json` (ghim sha). Backend đổi đường GET mà không xuất lại
    # ⇒ file cũ vẫn "đúng hình" nhưng thiếu / thừa đường ⇒ test này đỏ, nhắc chạy lại script.
    assert export.CONFIG_FILE.read_text(encoding="utf-8") == export.render()


def test_page_only_routes_with_customer_names_are_never_in_the_gate_list():
    # 13/11: đường trả tên + mã khách / đơn của cửa hàng chỉ để trang trên máy vẽ — không được có trong danh sách của cổng xem, kể cả khi ai chạy lại script
    page_only = ("/api/order-lines", "/api/membership", "/api/spice-customers")
    assert all(prefix in export.EXCLUDED_PREFIXES for prefix in page_only)
    assert not any(prefix.removeprefix("/api/") in export.render() for prefix in page_only)
    assert "/api/membership" in {route.path for route in main.app.routes if isinstance(route, APIRoute)}     # đường có thật trong app mà vẫn không được xuất
