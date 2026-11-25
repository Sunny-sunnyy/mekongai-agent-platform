"""Xuất DANH SÁCH ĐƯỜNG GET chỉ đọc của backend KPI ra JSON — cho cổng chuyển tiếp chỉ GET của hộp "xem" bên Capitol (bước "một nguồn số", 12/11).

Vì sao bằng máy: cổng bên kia chỉ cho qua đúng các đường + tham số trong file này (đường lạ ⇒ 404, tham số lạ / sai dạng ⇒ 422) và ghim sha256 của
file ⇒ gõ tay sót một đường là trang mất số, thừa một đường là mở cửa không ai soát. Danh sách lấy từ `app.routes` của đúng commit đang chạy.
Hình (bên Capitol chốt): {"duong": [{"path": "/api/employees/{employee_id}", "doan": {"employee_id": "^NV[0-9]{2}…$"}, "tham_so": {"period": "…"}}]}
- mỗi đoạn `{tên}` trong path BẮT BUỘC có regex ở "doan"; mỗi tham số truy vấn một regex khớp ĐỦ chuỗi;
- KHÔNG có đường bắt đầu bằng các tiền tố ở `EXCLUDED_PREFIXES` (đường hệ thống có khoá, công cụ của AI, xuất file, sửa workflow) — có thì hộp bên kia không bật;
- tham số / đoạn chưa có regex ở bảng `PATTERNS` ⇒ script DỪNG (đường mới thêm vào backend buộc phải có người quyết regex, không tự cho qua).

Chạy từ gốc repo (không bật backend, không đọc dữ liệu):  .venv/bin/python -m kpi_agent.scripts.xuat_duong_get <file ra .json>
Bản trong repo: `kpi_agent/config/duong_get_xem.json` (xuất lại bằng: … xuat_duong_get kpi_agent/config/duong_get_xem.json) — test `test_duong_get.py` bắt
file đó lệch với `app.routes` hiện tại (thêm / đổi đường GET mà quên xuất lại ⇒ đỏ).
In ra số đường + sha256 của file.
"""
import hashlib
import inspect
import json
import re
import sys
from pathlib import Path

from fastapi.routing import APIRoute

from kpi_agent.backend import main, queries

# "/api/order-lines": danh sách đơn của một nhân viên — CHƯA cho qua cổng xem ở bậc này (không đổi sha file đường trước mốc 3; bậc sau tính)
# "/api/membership", "/api/spice-customers" (13/11): danh sách từng KHÁCH (tên + mã) của Hội viên / 1 % gia vị — đường CHỈ CHO TRANG trên máy, không cho
# qua cổng xem của Capitol (người kiểm 13/11: thiếu dòng này thì chạy lại script sẽ đưa đường có tên khách vào danh sách cổng xem).
# "/api/calc" (17/11, E — công cụ tính cho ĐƯỜNG CHAT trên máy): tham số `args` là JSON lồng nhau tự do — không viết được regex chặt; Capitol gọi công
# cụ tính qua `/api/tool/{name}` (đã loại ở trên) ⇒ không cho qua cổng xem; sha `duong_get_xem.json` giữ nguyên. Giới hạn: chat chế độ "một nguồn số"
# (qua cổng xem) chưa có công cụ tính.
EXCLUDED_PREFIXES = ("/api/system", "/api/tool", "/api/export", "/api/workflow", "/api/blocks", "/api/order-lines", "/api/membership", "/api/spice-customers",
                     "/api/calc")
# Regex viết để an toàn với MỌI cách so của bên dùng (người kiểm độc lập 12/11): chỉ `[0-9]` (không `\d` — `\d` nhận cả chữ số Unicode như "５");
# kết thúc bằng `END` = `(?![\s\S])$` — "không còn ký tự nào sau đó" — vì `$` trần khớp TRƯỚC dấu xuống dòng cuối khi so bằng re.match ("NV05\n" lọt).
# Bên dùng vẫn nên so bằng fullmatch.
END = r"(?![\s\S])$"
POSITIVE_INT = r"^[1-9][0-9]{0,17}" + END          # mã bản tính / lượt: số nguyên dương (SQLite tối đa 19 chữ số)
MASKED_CODE = r"^NV[0-9]{2}" + END                 # mã che nhân viên — backend kiểm lại theo đội thật (`queries._check_employee`)
# Tên tham số (đoạn đường hoặc truy vấn) ⇒ regex khớp đủ chuỗi. Tên KPI lấy đúng danh sách của lõi, liệt kê tường minh.
PATTERNS = {
    "employee_id": MASKED_CODE, "employee_a": MASKED_CODE, "employee_b": MASKED_CODE,
    "kpi": "^(?:" + "|".join(re.escape(kpi) for kpi in queries.KPI_ORDER) + ")" + END,
    "period": r"^[0-9]{4}-(?:0[1-9]|1[0-2])" + END,                 # kỳ YYYY-MM — backend từ chối kỳ khác kỳ đang nạp
    "run_id": POSITIVE_INT, "version_id": POSITIVE_INT,
    "extra": r"^[0-9]{1,12}(?:\.[0-9]{1,4})?" + END,                # "nếu thêm N": số ≥ 0 (đơn / cửa hàng / đồng)
    "target_pct": r"^[0-9]{1,3}(?:\.[0-9]{1,2})?" + END,            # "nếu đạt X %": backend nhận 0 < X ≤ 300
}
SEGMENT = re.compile(r"\{(\w+)(?::\w+)?\}")                  # `{employee_id:path}` ⇒ tên "employee_id" (kiểu path của Starlette bỏ đi, regex giữ chặt)


def get_routes() -> list[dict]:
    routes, unknown = [], []
    for route in main.app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods or route.path.startswith(EXCLUDED_PREFIXES):
            continue
        segments = SEGMENT.findall(route.path)
        query = [name for name in inspect.signature(route.endpoint).parameters if name != "request" and name not in segments]
        unknown += [f"{route.path}: {name}" for name in segments + query if name not in PATTERNS]
        entry = {"path": SEGMENT.sub(lambda found: "{" + found.group(1) + "}", route.path)}
        if segments:
            entry["doan"] = {name: PATTERNS.get(name) for name in segments}
        entry["tham_so"] = {name: PATTERNS.get(name) for name in query}
        routes.append(entry)
    if unknown:
        raise SystemExit("DỪNG — tham số chưa có regex trong PATTERNS (thêm regex chặt, hoặc đưa đường vào EXCLUDED_PREFIXES): " + "; ".join(unknown))
    return sorted(routes, key=lambda entry: entry["path"])


CONFIG_FILE = Path(__file__).resolve().parents[1] / "config/duong_get_xem.json"   # bản ghi trong repo — máy chủ agent (chế độ xem qua hộp Capitol) đọc file này


def render() -> str:
    """Đúng chữ của file JSON (khoá xếp theo chữ cái, thụt 1, kết thúc bằng xuống dòng) — sha256 tính trên đúng các byte này."""
    return json.dumps({"duong": get_routes()}, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main_cli(out_path: str) -> None:
    text = render()
    Path(out_path).write_text(text, encoding="utf-8")
    print(f"{len(json.loads(text)['duong'])} đường GET ⇒ {out_path} · sha256 {hashlib.sha256(text.encode('utf-8')).hexdigest()}")


if __name__ == "__main__":
    main_cli(sys.argv[1])
