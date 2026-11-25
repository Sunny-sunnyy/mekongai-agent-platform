"""Dữ liệu cho trang CHỈ XEM workflow (bước 2a).

Vì sao file riêng trong `backend/`, không sửa `kpi_agent/workflow/*.py`: mọi file ở đó nằm trong dấu vân tay code của bản tính (`main.CODE_FILES`, S3)
⇒ thêm chữ nhãn vào `blocks.py` sẽ đẻ bản tính mới, mất "Đã soát", dù không con số nào đổi. File này chỉ ĐỌC danh bạ `BLOCKS` + kết quả lượt chạy.

Không đưa dữ liệu thô ra trang: cổng ra của khối đọc file / tính mang mã + tên NV thật ⇒ chỉ trả TÓM TẮT (đếm, tổng) — `port_summaries`."""
from collections import Counter

from kpi_agent.workflow import runner
from kpi_agent.workflow.blocks import BLOCKS, REQUIRED

# Nhãn lời thường cho người không viết code (mục 3: nhóm theo việc, mỗi khối một dòng mô tả)
BLOCK_LABELS = {
    "load_rules": ("Đọc bản luật", "Đọc dữ liệu", "Đọc file luật thưởng của kỳ (mốc, tiền, ngày lễ)."),
    "read_targets": ("Đọc file chỉ tiêu", "Đọc dữ liệu", "Đọc chỉ tiêu từng nhân viên theo 8 KPI."),
    "read_tracking": ("Đọc sổ Tracking", "Đọc dữ liệu", "Đọc số thực hiện đã chụp từ sổ theo dõi."),
    "read_raw": ("Đọc RAW DATA đơn hàng", "Đọc dữ liệu", "Đọc từng dòng đơn (ngày, cửa hàng, mã hàng) + danh sách điểm bán trong tuyến; lọc theo ngày chốt."),
    "check_data": ("Kiểm dữ liệu", "Kiểm", "Báo dữ liệu thiếu hay lệch; thiếu thứ bắt buộc thì dừng cả lượt."),
    "compute_kpi": ("Tính % KPI", "Tính", "Tính % thực hiện từng KPI của từng nhân viên."),
    "compute_bonus": ("Tính thưởng", "Tính", "Áp mốc thưởng, ra tiền từng KPI và lương kinh doanh."),
    "export_files": ("Xuất file", "Xuất", "Tải bảng thưởng Excel / Markdown / chữ thường của bản ĐÃ SOÁT — chưa soát thì không có file."),
    "review_gate": ("Người duyệt", "Duyệt", "Giám sát soát bảng thưởng; chưa bấm \"Đã soát\" thì các khối sau không chạy. Không tự duyệt."),
    "kpi_tool": ("Công cụ của trợ lý", "Trợ lý", "Cho trợ lý dùng MỘT công cụ tra số; nối vào cổng Công cụ của khối trợ lý."),
    "kpi_agent": ("Trợ lý KPI", "Trợ lý", "Trả lời câu hỏi bằng số của các khối tính, chỉ dùng công cụ được nối; code kiểm mọi con số."),
}
# Cổng: chữ tiếng Việt cho trang; tên cổng gốc (mã trong file) chỉ hiện khi rê chuột ("đừng vừa Anh vừa Việt")
PORT_LABELS = {"rules": "Bản luật", "targets": "Chỉ tiêu", "tracking": "Số thực hiện", "issues": "Kết quả kiểm",
               "results": "% KPI", "bonuses": "Tiền thưởng", "export": "File được xuất", "tool": "Công cụ", "tools": "Công cụ (nhiều dây)",
               "answer": "Câu trả lời"}
# Mã khối gợi ý khi thả khối mới trên trang 2b: mã = tên trong file gốc ⇒ ráp lại bằng tay ra ĐÚNG mã băm logic của file (mã khối nằm trong mã băm).
# Trùng (thả khối cùng loại lần 2) ⇒ trang thêm _2, _3.
DEFAULT_IDS = {"load_rules": "rules", "read_targets": "targets", "read_tracking": "tracking", "read_raw": "tracking", "check_data": "check", "compute_kpi": "kpi",
               "compute_bonus": "bonus", "review_gate": "duyet", "export_files": "xuat"}
PARAM_LABELS = {
    "path": "Đường file, tính từ gốc repo",
    "mcp_path": "File danh sách điểm bán trong tuyến (MCP), tính từ gốc repo",
    "formats": "Định dạng file được tải (xlsx, md, txt)",
    "npp_turnover_pct": "% doanh số cả nhà phân phối — để trống khi chưa có (thưởng vượt sẽ ghi \"thiếu dữ kiện\")",
}


def block_catalog() -> list[dict]:
    """Danh bạ khối cho trang — hình dữ liệu nháp mục 8.1 (phần repo này có)."""
    catalog = []
    for block_type, block in BLOCKS.items():
        label, group, description = BLOCK_LABELS.get(block_type, (block_type, "Khác", ""))
        params = [{"name": name, "label": PARAM_LABELS.get(name, name), "required": default is REQUIRED,
                   "default": None if default is REQUIRED else default,
                   # kiểu lấy đúng chữ của bộ kiểm (blocks.param_types) — một nguồn, khoảng 0–300 không chép lại ở đây
                   "kind": block.param_types[name][0] if name in block.param_types else None}
                  for name, default in block.params.items()]
        catalog.append({"type": block_type, "label": label, "group": group, "description": description,
                        "default_id": DEFAULT_IDS.get(block_type, block_type),
                        "inputs": [{"name": port, "label": PORT_LABELS.get(port, port), "required": True}   # bước 1: mọi cổng vào bắt buộc
                                   for port in block.inputs],
                        "outputs": [{"name": port, "label": PORT_LABELS.get(port, port)} for port in block.outputs],
                        # cổng công cụ (khối agent): nhận nhiều dây, chỉ từ khối "Công cụ của trợ lý" — trang chưa vẽ (mốc sau)
                        "tool_inputs": [{"name": port, "label": PORT_LABELS.get(port, port), "required": True, "many": True}
                                        for port in block.tool_inputs],
                        "params": params, "run_params": list(block.run_params)})
    return catalog


def _summary(port: str, value, to_mask: dict) -> dict:
    """Tóm tắt MỘT cổng ra — chỉ đếm / tổng, không mã, không tên (mã thật nằm trong khoá dict của các cổng này)."""
    if port == "rules":
        return {"text": f"bản luật kỳ {value['period']}", "period": value["period"]}
    if port == "targets":
        return {"text": f"{len(value['employees'])} nhân viên", "employees": len(value["employees"])}
    if port == "tracking":
        at = value.get("refreshed_at")
        return {"text": f"{len(value['blocks'])} khối số liệu" + (f", làm mới {at:%d/%m/%Y %H:%M}" if at else ""),
                "kpi_blocks": len(value["blocks"]), "refreshed_at": at.isoformat() if at else None}
    if port == "issues":
        levels = Counter(issue["level"] for issue in value)
        return {"text": f"{len(value)} mục (lỗi {levels['error']}, cảnh báo {levels['warning']}, thông tin {levels['info']})",
                "levels": dict(levels), "messages": [{"level": issue["level"], "message": runner.mask_codes(issue["message"], to_mask)}
                                                     for issue in value]}
    if port == "results":
        kpis = {kpi for by_kpi in value.values() for kpi in by_kpi}
        return {"text": f"% của {len(value)} nhân viên × {len(kpis)} KPI", "employees": len(value), "kpis": len(kpis)}
    if port == "bonuses":
        salary = sum(bonus["business_salary"] for bonus in value.values())
        over = [bonus["over_target_bonus"] for bonus in value.values()]
        return {"text": f"lương kinh doanh cả đội {salary:,} đ".replace(",", "."), "employees": len(value), "business_salary_total": salary,
                "over_target_total": sum(amount for amount in over if amount is not None), "over_target_missing_data": sum(amount is None for amount in over)}
    if port == "export":
        return {"text": "được tải: " + ", ".join(value["formats"]), "formats": value["formats"]}
    return {"text": "(không có tóm tắt cho cổng này)"}


def port_summaries(result: runner.RunResult, to_mask: dict) -> dict:
    """node id ⇒ {cổng ra: tóm tắt} cho lượt backend đang giữ trong bộ nhớ. Cổng vào của một khối = cổng ra phía trước (trang tự ghép theo dây)."""
    return {node_id: {port: _summary(port, value, to_mask) for port, value in outputs.items()} for node_id, outputs in result.outputs.items()}
