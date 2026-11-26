"""Bảng TUYẾN ở cửa tải lên (giai đoạn 2a-1): tìm công thức đọc cho một sổ đã gửi, chạy thử công thức ⇒ câu kiểm / lỗi có mã. Không tính KPI, không lưu.

Tách khỏi `backend/main.py` để file đó chỉ còn vài dòng nối. Lõi đọc ở `core/load_tuyen.py`; khuôn + hình công thức ở `core/khuon.py`.
"""
from pathlib import Path

from kpi_agent.core import khuon, load_tuyen

NEED_FORMULA = "can_cong_thuc_doc"   # mã ổn định: sổ có bố cục chưa có công thức đọc ⇒ bên gọi (người / khối agent) gửi công thức theo khuôn


def formula_for(path: Path, confirmed: list[dict]) -> tuple[dict | None, str | None]:
    """(công thức đọc, nguồn) cho sổ này: công thức LƯU SẴN nếu vân tay bố cục khớp sổ đã biết, không thì cách ghép người đã xác nhận mới nhất có
    vân tay khớp (`confirmed` = `store.mappings_of(conn, "tuyen")`). Không khớp cái nào ⇒ (None, None) — không áp công thức vị trí cột cho bố cục khác."""
    candidates = [("luu_san", khuon.default_mapping("tuyen"), khuon.KNOWN_LAYOUTS["tuyen"])]
    candidates += [("da_xac_nhan", item["mapping"], item["fingerprint"]) for item in confirmed]
    for source, mapping, fingerprint in candidates:
        try:
            header = load_tuyen.header_of(path, khuon.normalize_mapping({**mapping, "table": "tuyen"}))
        except (load_tuyen.RouteProblems, ValueError):
            continue
        if khuon.layout_fingerprint(str(mapping["sheet"]), mapping["header_row"], header) == fingerprint:
            return mapping, source
    return None, None


def need_formula_reply() -> dict:
    return {"refused": "sổ này có bố cục chưa có công thức đọc cho bảng tuyến — gửi công thức theo khuôn (ghep) qua /api/system/ghep-thu",
            "code": NEED_FORMULA, "bang": "tuyen", "khuon": khuon.schema("tuyen"),
            "hinh_cong_thuc": {"sheet": "tên sheet | số thứ tự", "header_row": "số dòng tiêu đề", "columns": "{trường: tên cột | vị trí cột}",
                               "values": "{channel: {chữ trong file: chữ chuẩn}}", "rows": "{bo_dong_trong: [trường]}"}}


def order_coverage(lines: list[dict], mcp: dict, team: set[str]) -> dict:
    """Bất biến bằng máy cho bảng tuyến: câu kiểm chỉ ĐẾM không bắt được đổi chéo On ↔ Off hay lệch khối cột. Với các điểm bán CÓ ĐƠN
    của người trong đội ở file đơn hàng của bản: bao nhiêu nằm trong tuyến của chính người đó, và trong số đó bao nhiêu trùng kênh — theo kênh của ĐƠN.
    Chỉ là số để người / agent nhìn; chưa đặt ngưỡng dừng.
    """
    seen = {(str(line["SM CODE"]).strip(), str(line["OUTLET CODE"]).strip()): line["L1"] for line in lines if str(line["SM CODE"]).strip() in team}
    out = {}
    for (sm, outlet), channel in seen.items():
        cell = out.setdefault(channel, {"outlets_with_orders": 0, "in_routes": 0, "same_channel": 0})
        cell["outlets_with_orders"] += 1
        route = mcp.get(sm, {}).get(outlet)
        cell["in_routes"] += route is not None
        cell["same_channel"] += route is not None and route["channel"] == channel
    return out

# Ngưỡng DỪNG của phép đối chiếu đơn hàng ⇄ tuyến ( chốt 20/11 sau ca: mã nhân viên hợp lệ nhưng sai người ⇒ bản mới 114.800.000 đ
# thay vì 152.360.000 đ, không cờ). TẠM — mới có số của MỘT tháng, xem lại khi có tháng thứ hai: tháng 10 thật 99,58 % (On) / 99,88 % (Off) nằm trong
# tuyến, 0 % lệch kênh; hai ca sai toàn bộ 6,7 % / 6,9 % và 100 % lệch kênh. Xét theo TỪNG kênh của đơn.
# Siết lần 2 (20/11, vòng 2): ngưỡng 90 % để lọt tuyến sai MỘT PHẦN — gán sai người ~8 % số dòng rải đều (đối chiếu ≈ 91,5 %, tổng đội
# 137.440.000 thay 152.360.000) và nửa số điểm của một người sang người khác (Off 95,5 %, lệch 3.240.000 đ) ⇒ 98 %.
MIN_ORDER_OUTLETS_IN_ROUTES_PCT = 98      # dưới mức này ⇒ dừng `tuyen_lech_don_hang`
WARN_ORDER_OUTLETS_IN_ROUTES_PCT = 99.5   # từ ngưỡng dừng tới dưới mức này ⇒ qua, kèm cảnh báo có số
MAX_CHANNEL_MISMATCH_PCT = 2              # trong các điểm nằm trong tuyến, lệch kênh trên mức này ⇒ dừng `tuyen_lech_kenh`
# Theo TỪNG NGƯỜI, gộp hai kênh (chọn 20/11): ca "nửa số điểm của MỘT người gán sang người khác" chỉ lộ ở tỉ lệ riêng của người đó. Dừng khi
# CẢ HAI: tỉ lệ dưới mức VÀ đủ nhiều điểm ngoài tuyến (người ít điểm không bị dừng oan vì 1–2 điểm). Tháng 10 thật: nhỏ nhất 98,43 % (2 / 127), nhiều nhất
# 2 điểm ngoài tuyến của một người.
MIN_PERSON_OUTLETS_IN_ROUTES_PCT = 95
MIN_PERSON_OUTLETS_OUTSIDE = 5
# CẢNH BÁO theo người ( 20/11, sau 15 ca của: 5 điểm Off có đơn của một người gán sang người khác ⇒ lệch 600.000 đ ở một người mà
# không ngưỡng nào chạm — Off 99,57 %, theo người chưa tới mức dừng): một người có từ ngần này điểm có đơn ngoài tuyến ⇒ qua, kèm cảnh báo. TẠM — một
# tháng dữ liệu: sổ thật tháng 10 nhiều nhất 2 điểm / người ⇒ không cảnh báo.
WARN_PERSON_OUTLETS_OUTSIDE = 3


def route_stops(summary: dict) -> tuple[list[dict], list[str]]:
    """(lỗi làm DỪNG, cảnh báo) của bảng tuyến vừa đọc, từ câu kiểm đã có số đối chiếu `orders_vs_routes`. Lỗi mang mã cố định + số, không chữ của file."""
    problems, warnings = [], []
    missing = summary.get("team_codes_without_routes")
    if missing:   # người trong đội (file chỉ tiêu) không có điểm bán nào trong sổ: ghép nhầm cột mã / sổ ghi sai mã một người ⇒ người đó mất mọi KPI theo tuyến
        problems.append({"ma": "nguoi_trong_doi_khong_co_tuyen", "truong": "sm_code", "so_dong": 0, "so_ma_sm": missing})
    coverage = summary.get("orders_vs_routes")
    if coverage is None:
        problems.append({"ma": "khong_doi_chieu_duoc_voi_don_hang", "truong": None, "so_dong": 0})
        return problems, warnings
    for channel, cell in sorted(coverage.items()):
        with_orders, inside, same = cell["outlets_with_orders"], cell["in_routes"], cell["same_channel"]
        if not with_orders:
            continue
        pct = inside * 100 / with_orders
        if pct < MIN_ORDER_OUTLETS_IN_ROUTES_PCT:
            problems.append({"ma": "tuyen_lech_don_hang", "truong": "sm_code", "so_dong": with_orders - inside, "kenh": channel,
                             "diem_co_don": with_orders, "nam_trong_tuyen": inside, "nguong_pct": MIN_ORDER_OUTLETS_IN_ROUTES_PCT})
        elif pct < WARN_ORDER_OUTLETS_IN_ROUTES_PCT:
            warnings.append(f"{channel}: {inside} / {with_orders} điểm bán có đơn nằm trong tuyến của đúng người (dưới {WARN_ORDER_OUTLETS_IN_ROUTES_PCT} %) — "
                            "xem lại sổ có thiếu tuyến không")
        if inside and (inside - same) * 100 / inside > MAX_CHANNEL_MISMATCH_PCT:
            problems.append({"ma": "tuyen_lech_kenh", "truong": "channel", "so_dong": inside - same, "kenh": channel,
                             "nam_trong_tuyen": inside, "trung_kenh": same, "nguong_pct": MAX_CHANNEL_MISMATCH_PCT})
    # theo từng người (gộp hai kênh): chỉ nêu SỐ người vi phạm, không nêu mã
    people = [person for person in summary.get("orders_vs_routes_by_person") or []
              if person["outlets_with_orders"] - person["in_routes"] >= MIN_PERSON_OUTLETS_OUTSIDE
              and person["in_routes"] * 100 / person["outlets_with_orders"] < MIN_PERSON_OUTLETS_IN_ROUTES_PCT]
    if people:
        problems.append({"ma": "tuyen_lech_don_hang_theo_nguoi", "truong": "sm_code", "so_ma_sm": len(people),
                         "so_dong": sum(person["outlets_with_orders"] - person["in_routes"] for person in people),
                         "nguong_pct": MIN_PERSON_OUTLETS_IN_ROUTES_PCT, "nguong_diem": MIN_PERSON_OUTLETS_OUTSIDE})
    # theo người mà CHƯA tới mức dừng: từ `WARN_PERSON_OUTLETS_OUTSIDE` điểm có đơn ngoài tuyến, hoặc tỉ lệ đã dưới mức dừng với ít điểm hơn ⇒ qua,
    # kèm MỘT cảnh báo chỉ nêu số người (mã `PERSON_WARNING`)
    stopped = {id(person) for person in people}
    near = [person for person in summary.get("orders_vs_routes_by_person") or [] if id(person) not in stopped and _person_needs_a_look(person)]
    if near:
        warnings.append(f"{len(near)} nhân viên có điểm bán có đơn nằm ngoài tuyến của mình ở mức cần xem lại (từ {WARN_PERSON_OUTLETS_OUTSIDE} điểm, "
                        f"hoặc dưới {MIN_PERSON_OUTLETS_IN_ROUTES_PCT} % số điểm có đơn) — xem lại sổ có thiếu / gán nhầm tuyến không")
    return problems, warnings


def version_notes(summary: dict) -> list[dict]:
    """Lưu ý đi theo BẢN dữ liệu lấy tuyến từ sổ ⇒ [{level, message}] cho `data_flags` + `get_data_issues` của bản (người hỏi số thấy luôn thứ đã bị
    bỏ / sát ngưỡng, không chỉ người tải thấy ở phản hồi tải lên). Lời do code viết: chỉ số đếm, không mã nhân viên, không chữ của file."""
    notes = []
    dropped = summary.get("dropped_outside_team") or []
    blank = summary.get("rows_blank_skipped") or 0
    if dropped or blank:
        parts = ([f"{sum(item['rows'] for item in dropped)} dòng của {len(dropped)} mã nhân viên không có trong file chỉ tiêu (ngoài đội)"] if dropped else [])
        parts += [f"{blank} dòng trống mã nhân viên"] if blank else []
        notes.append({"level": "info", "message": "bảng tuyến lấy từ sổ theo dõi tải lên — đã bỏ " + " và ".join(parts)})
    notes += [{"level": "warning", "message": "bảng tuyến lấy từ sổ theo dõi tải lên — " + text} for text in summary.get("canh_bao") or []]
    return notes


PERSON_WARNING = "tuyen_nguoi_co_diem_ngoai_tuyen"   # mã của cảnh báo theo người (bảng tên chung với đường máy)


def _person_needs_a_look(person: dict) -> bool:
    outside = person["outlets_with_orders"] - person["in_routes"]
    return outside >= WARN_PERSON_OUTLETS_OUTSIDE or (outside > 0 and person["in_routes"] * 100 / person["outlets_with_orders"] < MIN_PERSON_OUTLETS_IN_ROUTES_PCT)


def order_coverage_by_person(lines: list[dict], mcp: dict, team: set[str]) -> list[dict]:
    """Như `order_coverage` nhưng theo TỪNG NGƯỜI trong đội, gộp hai kênh: [{outlets_with_orders, in_routes}] — KHÔNG mang mã nhân viên (chỉ để tính
    ngưỡng + cho người xem thấy phân bố), xếp theo tỉ lệ tăng dần."""
    seen = {(str(line["SM CODE"]).strip(), str(line["OUTLET CODE"]).strip()) for line in lines if str(line["SM CODE"]).strip() in team}
    people = {}
    for sm, outlet in seen:
        cell = people.setdefault(sm, {"outlets_with_orders": 0, "in_routes": 0})
        cell["outlets_with_orders"] += 1
        cell["in_routes"] += outlet in mcp.get(sm, {})
    return sorted(people.values(), key=lambda cell: cell["in_routes"] / cell["outlets_with_orders"])


def problems_text(problems: list[dict]) -> str:
    """Một dòng chữ cho lời từ chối: mã lỗi + trường + số (không chữ của file)."""
    return "; ".join(p["ma"] + (f" ({p['truong']})" if p.get("truong") else "") + (f" — {p['so_dong']} dòng" if p.get("so_dong") else "")
                     + (f" — {p['so_ma_sm']} mã" if p.get("so_ma_sm") else "") + (f" — {p['kenh']}" if p.get("kenh") else "") for p in problems)


def team_mismatch(route_team, target_codes) -> bool:
    """Danh sách "đội" ghi ở khối đọc tuyến phải ĐÚNG bằng tập mã của file chỉ tiêu của chính bản đó — không thành nguồn thứ hai."""
    return {str(code).strip() for code in (route_team or ())} != {str(code).strip() for code in target_codes}


def try_formula(path: Path, mapping: dict, team_codes: set[str]) -> dict:
    """Chạy công thức trên sổ ⇒ {"ok": True, tom_tat, van_tay_bo_cuc, cot_ma_diem_ban} hoặc {"ok": False, "loi": [{ma, truong, so_dong}]}.
    `cot_ma_diem_ban` = vị trí cột mã điểm bán trong file (A = 1) — đáp án của câu kiểm, bên gọi giữ, không trả ra."""
    try:
        result = load_tuyen.read_routes(path, mapping, team_codes)
    except load_tuyen.RouteProblems as error:
        return {"ok": False, "loi": error.problems}
    normalized = khuon.normalize_mapping({**mapping, "table": "tuyen"})
    column = normalized["columns"]["outlet_code"]
    if not isinstance(column, int):
        header = [khuon._clean(name) for name in load_tuyen.header_of(path, normalized)]
        column = header.index(column) + 1
    return {"ok": True, "tom_tat": result["summary"], "van_tay_bo_cuc": result["fingerprint"], "cot_ma_diem_ban": column, "mcp": result["mcp"]}
