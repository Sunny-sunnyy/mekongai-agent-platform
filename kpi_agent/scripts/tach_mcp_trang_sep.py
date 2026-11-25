"""Tách DANH SÁCH ĐIỂM BÁN TRONG TUYẾN (MCP) từ trang "Tracking KPI Lam Phương" sếp gửi ⇒ JSON cho `core/load_raw.load_mcp_json`.

Vì sao (10/11, theo sếp): KPI 4 bỏ điểm bán ngoài tuyến (công văn 6.2) mà RAW DATA không có MCP; trang sếp có tab "Chi tiết" =
2.501 điểm bán thuộc MCP (hằng `D.ct`). CHỈ đọc khoá `ct` — không đọc `kpi` (số KPI của trang là đáp án C giữ để so mù).
Trang ghi NV theo TÊN ⇒ ghép sang SM CODE bằng cột SM_NAME của RAW (tên trang = các chữ cuối của SM_NAME). Không ghép được / ghép 2 người ⇒ dừng.

Chạy từ gốc repo:
  .venv/bin/python -m kpi_agent.scripts.tach_mcp_trang_sep "<saved_resource.html>" "sample_data/thang_10/RAW DATA.xlsx" sample_data/thang_10/mcp_trang_sep_3110.json"""
import hashlib
import json
import re
import sys
from pathlib import Path

# 12/11: ba hàm dưới đây backend cũng cần lúc chạy ⇒ đã dời vào gói chạy (core / backend); file này gọi lại (chiều ngược lại làm hộp Capitol thiếu module)
from kpi_agent.backend.tracking_view import page_data   # noqa: F401 — các script tach_chi_tieu_* lấy qua file này
from kpi_agent.core.load_raw import name_to_code, raw_employee_names   # noqa: F401

CT_COLUMNS = ("outlet_code", "outlet_name", "employee_name", "address", "visit_day", "channel")   # 6 cột đầu của một phần tử `ct` (trang tự mô tả)


def page_outlets(html_path: str) -> list[list]:
    return page_data(html_path)["ct"]


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(html_path: str, raw_path: str, out_path: str) -> None:
    outlets, names = page_outlets(html_path), raw_employee_names(raw_path)
    codes = {name: name_to_code(name, names) for name in {row[2] for row in outlets}}
    mcp: dict[str, dict] = {}
    for row in outlets:
        item = dict(zip(CT_COLUMNS, row))
        mcp.setdefault(codes[item["employee_name"]], {})[str(item["outlet_code"])] = {"channel": item["channel"], "visit_day": item["visit_day"]}
    out = {"source": {"page": Path(html_path).name, "page_sha256": _sha256(html_path), "raw": Path(raw_path).name, "raw_sha256": _sha256(raw_path),
                      "note": "tab Chi tiết (D.ct) của trang sếp — điểm bán thuộc MCP, số chốt 31/10/2026"},
           "mcp": mcp}
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{sum(len(v) for v in mcp.values())} điểm bán / {len(mcp)} NV ⇒ {out_path}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
