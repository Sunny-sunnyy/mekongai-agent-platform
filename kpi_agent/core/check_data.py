"""Khối 2 — kiểm dữ liệu trước khi tính: trả danh sách cảnh báo lời thường (không sửa dữ liệu, không dừng).

Mỗi cảnh báo là dict {"level": "error" | "warning" | "info", "message": ..., "employees": [mã NV liên quan]}. "error" = thiếu thứ bắt buộc
(vd NV không có đủ 8 chỉ tiêu) — bên gọi quyết có dừng không.
"employees" (09/11, cho công cụ lọc cờ theo người): mã THẬT như trong file (bên gọi tự che); [] = cờ cả đội. Câu chữ không đổi."""
import math
from datetime import date

from kpi_agent.core.rules import KPI_ORDER

ASO_KPIS = ("ASO_ALL", "ASO_Focus", "ASO_Focus_2", "PC_ON", "LPPC_Meat")   # khối có cột OUTLET CODE
# Mỗi dòng khối là gì — để cờ "ngoài MCP" nói đúng đơn vị đếm (khối khác: mỗi dòng = một đơn / PC)
OUTSIDE_WHAT = {"Turnover": "cửa hàng", "ASO_ALL": "cửa hàng", "ASO_Focus": "cửa hàng", "ASO_Focus_2": "cặp cửa hàng–mã hàng ở cửa hàng"}


def check_data(targets: dict, tracking: dict, cutoff: date) -> list[dict]:
    issues = []
    employees = targets["employees"]

    for code, by_kpi in targets["targets"].items():
        missing = [k for k in KPI_ORDER if k not in by_kpi]
        if missing:
            issues.append(_issue("error", f"{code}: thiếu chỉ tiêu {missing}", [code]))

    for kpi, block in tracking["blocks"].items():
        unknown = sorted({r["SM CODE"] for r in block["rows"]} - set(employees))
        if unknown:
            issues.append(_issue("warning", f"{kpi}: Rawdata có mã NV không có trong file chỉ tiêu: {unknown}", unknown))
    no_data = [c for c in employees if not any(r["SM CODE"] == c for r in tracking["blocks"]["Turnover"]["rows"])]
    if no_data:
        issues.append(_issue("warning", f"NV có chỉ tiêu nhưng không có doanh số trong Rawdata: {no_data}", no_data))

    # Công văn 6.2: outlet phải thuộc MCP của chính ĐDKD. Sổ không lọc điều này ⇒ báo số dòng bị ảnh hưởng.
    raw = tracking.get("source") == "raw"
    # QĐ-09 11/11: đường RAW lọc cả Turnover / PC_BT / LPPC (khối RAW có mã cửa hàng) ⇒ báo cả số dòng bị loại của 3 KPI đó
    with_outlet = tuple(kpi for kpi in ("Turnover", "PC_BT", "LPPC")
                        if raw and all("OUTLET CODE" in r for r in tracking["blocks"][kpi]["rows"]))
    for kpi in (*ASO_KPIS, *with_outlet):
        outside = [r for r in tracking["blocks"][kpi]["rows"]
                   if r["OUTLET CODE"] not in tracking["mcp"].get(r["SM CODE"], {})]
        if outside:
            # Đường RAW (10/11): luật RAW lọc cửa hàng ngoài tuyến ở KPI đếm cửa hàng ⇒ chỉ là thông tin, không phải cảnh báo
            note = "đã loại theo luật đường RAW" if raw else "cấu hình hiện vẫn tính như sổ"
            # đơn vị đếm theo hình khối (bản kế, minor: "Turnover: 4 dòng" thật là 4 cặp NV–cửa hàng = 13 dòng RAW)
            what = OUTSIDE_WHAT.get(kpi, "đơn ở cửa hàng")
            issues.append(_issue("info" if raw else "warning", f"{kpi}: {len(outside)} {what} không thuộc MCP của chính NV "
                                                               f"(công văn 6.2: không được tính; {note})", {r["SM CODE"] for r in outside}))

    refreshed = tracking.get("refreshed_at")
    if raw:
        pass   # RAW có ngày từng dòng ⇒ đã lọc đúng ngày chốt; không có "lần làm mới pivot"
    elif refreshed is None:
        issues.append(_issue("warning", "không đọc được ngày làm mới pivot — không biết dữ liệu chụp tới ngày nào"))
    elif cutoff >= refreshed.date():
        issues.append(_issue("warning", f"ngày chốt {cutoff:%d/%m/%Y} không trước lần làm mới pivot "
                                        f"{refreshed:%d/%m/%Y %H:%M} ⇒ số sau thời điểm làm mới chưa có trong sổ"))

    # Bậc 3a: giá trị luật kỳ nêu mà file không có dòng nào, đã được cho qua có ghi chú (`load_raw.rule_values_without_lines`) — KPI đó tính như
    # thường (có thể ra 0) nên phải để lại dấu cho người đọc số
    for item in tracking.get("rule_values_without_lines", []):
        issues.append(_issue("info", f"luật kỳ: {item['what']} «{item['value']}» nhưng file không có dòng nào — cho qua theo ghi chú trong file luật: "
                                     f"{item['note']}"))
    # Mục cho qua THỪA trong file luật (luật không nêu giá trị đó cho KPI đó, hoặc file có dòng): không đổi số, nhưng trước đây bị bỏ qua lặng lẽ
    for item in tracking.get("unused_rule_allowances", []):
        issues.append(_issue("info", f"file luật kỳ: mục cho qua `cho_qua_khong_co_don` {item['kpi']} «{item['value']}» không dùng tới — luật không nêu "
                                     "giá trị đó cho KPI đó, hoặc file có dòng mang giá trị đó; nên gỡ mục này khỏi file luật"))
    issues.extend(_check_targets_against_mcp(targets, tracking["mcp"]))
    return issues


def _check_targets_against_mcp(targets: dict, mcp: dict) -> list[dict]:
    """Đối chiếu chỉ tiêu với chữ công văn: PC_BT = 65 % số call OFF, PC_ON = 50 % số call ON, ASO_ALL = 80 % số cửa hàng.

    MCP trong sổ là danh sách hiện tại; công văn chốt mẫu số ngày 28 tháng trước (6.3) ⇒ lệch là "thông tin", không phải lỗi.
    Cách làm tròn (PC làm tròn xuống, ASO_ALL làm tròn gần nhất) chọn sau khi xem số tháng 10 — công văn không ghi.
    """
    issues, mismatched, mismatched_codes = [], [], set()
    # MCP tách từ trang sếp (đường RAW) không có số lần viếng thăm ⇒ không tính lại được chỉ tiêu PC — nói rõ, không đoán. ASO_ALL chỉ cần SỐ CỬA
    # HÀNG nên vẫn đối chiếu (12/11: trước đó trả sớm, bỏ luôn phép ASO_ALL làm được).
    has_visits = all("visits_per_month" in outlet for outlets in mcp.values() for outlet in outlets.values())
    for code, by_kpi in targets["targets"].items():
        outlets = mcp.get(code, {}).values()
        n_outlets = sum(1 for o in outlets if o["channel"] in ("Kênh Off Premise", "Kênh On Premise"))
        expected = {"ASO_ALL": round(0.80 * n_outlets)}
        if has_visits:
            calls_off = sum(o["visits_per_month"] for o in outlets if o["channel"] == "Kênh Off Premise")
            calls_on = sum(o["visits_per_month"] for o in outlets if o["channel"] == "Kênh On Premise")
            expected = {"PC_BT": math.floor(0.65 * calls_off), "PC_ON": math.floor(0.50 * calls_on), **expected}
        for kpi, value in expected.items():
            if by_kpi.get(kpi) != value:
                # ASO_ALL kèm số cửa hàng tuyến để người đọc tự thấy phép tính — không phải đoán "80 % của bao nhiêu"
                detail = f" (80 % của {n_outlets} cửa hàng tuyến)" if kpi == "ASO_ALL" else ""
                mismatched.append(f"{code} {kpi}: chỉ tiêu {by_kpi.get(kpi)} ≠ tính từ MCP {value}{detail}")
                mismatched_codes.add(code)
    checked = (3 if has_visits else 1) * len(targets["targets"])
    if has_visits:
        issues.append(_issue("info", f"chỉ tiêu tính lại từ MCP khớp {checked - len(mismatched)}/{checked}"))
    else:
        # Dòng tổng đi kèm MỌI kết quả công cụ (cờ cả đội) ⇒ phải NÊU NGƯỜI LỆCH, không chỉ số đếm: bản đầu ghi "khớp 12/14" — hỏi về chính người
        # lệch thì mô hình đọc dòng đó và trả lời "khớp" (12/11, câu thật). Không mang số đếm (số đứng sau mã NV dễ bị rào chủ-số chặn).
        # Mã thật ở đây được che khi thành cờ; chi tiết từng người nằm ở dòng "lệch …" gắn `employees` ngay dưới.
        who = list(dict.fromkeys(line.split(" ", 1)[0] for line in mismatched))
        verdict = (f"lệch ở {', '.join(who)} — xem vấn đề dữ liệu của các nhân viên này; các nhân viên khác khớp" if who else "mọi nhân viên khớp")
        issues.append(_issue("info", f"chỉ tiêu ASO_ALL tính lại từ danh sách tuyến (80 % số cửa hàng trong tuyến): {verdict}. "
                                     "PC_BT / PC_ON chưa đối chiếu (danh sách tuyến không có số lần viếng thăm mỗi tháng)"))
    if mismatched:
        issues.append(_issue("info", "lệch (MCP có thể đổi sau ngày chốt mẫu số 28/T-1): " + "; ".join(mismatched), mismatched_codes))
    return issues


def _issue(level: str, message: str, employees=()) -> dict:
    return {"level": level, "message": message, "employees": sorted(employees)}
