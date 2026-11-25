"""Số cho tab "Theo dõi" (bản 2, /2026 — kế hoạch).

Một chỗ dựng dữ liệu theo dõi cho CẢ HAI đường: `GET /api/tracking` (giao diện trên máy) và lệnh sinh file .html (`scripts/tao_trang_tracking.py`).
Không tính số KPI / tiền nào: lấy nguyên `queries.team_board` + Thưởng thêm của backend. Chỉ thêm:
- hạng trong từng KPI / khoản (số đo lớn trước, bằng nhau cùng hạng) — backend chưa có;
- ô lệch trang theo dõi của đội (trang sếp gửi): so số THÔ hai bên, lý do từ `config/lech_trang_<kỳ>.yaml`.
Vì sao ngoài `main.CODE_FILES`: chỉ trình bày ⇒ sửa file này không được làm đổi dấu vân tay bản tính (mất "Đã soát")."""
import json
import math
import re
from pathlib import Path

import yaml

from kpi_agent.core.rules import KPI_ORDER

REPO_ROOT = Path(__file__).resolve().parents[2]
MILLION = 1_000_000
# Trang sếp lưu số thô 3 chữ số lẻ (triệu đ, đơn, mã / đơn): lệch quá nửa đơn vị chữ số lẻ thứ 3 mới là lệch. So số đã làm tròn hiển thị thì
# LPPC "6,03 ↔ 6,04" thành lệch giả (mình làm tròn xuống, trang làm tròn gần nhất — lượt đầu 11/11 ra 16 ô lệch giả)
PAGE_TOLERANCE = 0.0005 + 1e-9
UNKNOWN_REASON = "chưa rõ lý do — chưa ai kiểm"


def ranks(values: dict[str, float]) -> dict[str, int]:
    """Hạng theo số lớn trước; bằng nhau cùng hạng (1, 2, 2, 4)."""
    ordered = sorted(values.values(), reverse=True)
    return {code: ordered.index(value) + 1 for code, value in values.items()}


def known_reasons(period: str) -> dict[str, str]:
    """{khoá KPI / mã khoản: lý do chung, "<khoá>/<mã che>": lý do riêng một ô} của kỳ; chưa có file ⇒ {}."""
    path = REPO_ROOT / f"kpi_agent/config/lech_trang_{period.replace('-', '_')}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["reasons"] if path.exists() else {}


def outlet_sales_page_total(period: str) -> int | None:
    """Tổng doanh số các điểm bán thuộc tuyến mà TRANG đội ghi (tab "Chi tiết", đồng) — cùng file với `known_reasons`; chưa có ⇒ None."""
    path = REPO_ROOT / f"kpi_agent/config/lech_trang_{period.replace('-', '_')}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("outlet_sales_page_total") if path.exists() else None


def raw_actuals(ctx) -> dict[tuple[str, str], float]:
    """(KPI, mã che) ⇒ số thực hiện CHƯA làm tròn hiển thị (bảng đội làm tròn xuống LPPC / %) — chỉ để so với số thô của trang sếp."""
    masked_of = {real: masked for masked, real in ctx.to_real.items()}
    return {(kpi, masked_of[real]): result.actual for real, kpis in ctx.results.items() for kpi, result in kpis.items()}


def raw_measures(ctx) -> dict[tuple[str, str], float]:
    """(KPI, mã che) ⇒ số dùng để XẾP HẠNG: % thô (KPI tính theo % chỉ tiêu) hoặc số thực hiện thô — xếp theo số đúng, không theo số đã in
    (LPPC hai NV cùng in 5,39 mà số thật khác ⇒ xếp theo số in thì ra cùng hạng sai).
    """
    masked_of = {real: masked for masked, real in ctx.to_real.items()}
    return {(kpi, masked_of[real]): (result.pct if ctx.rules["kpis"][kpi]["measure"] == "pct_of_target" else result.actual)
            for real, kpis in ctx.results.items() for kpi, result in kpis.items()}


def page_data(html_path: str) -> dict:
    """(Dời từ `scripts/tach_mcp_trang_sep.py` 12/11 — backend không được import `scripts/`, thư mục đó không thuộc gói chạy.) Hằng dữ liệu `const D={…}` của trang (tính sẵn, dán vào trang). Người gọi chỉ lấy khoá mình cần — không đọc `kpi` (đáp án C giữ)."""
    text = Path(html_path).read_text(encoding="utf-8")
    match = re.search(r'const D=(?=\{"upd")', text)
    if match is None:
        raise ValueError("không thấy hằng dữ liệu `const D={\"upd\"…` trong trang")
    data, _ = json.JSONDecoder().raw_decode(text[match.end():])
    return data


def kpi_ranks(measures: dict[tuple[str, str], float]) -> dict[str, dict[str, int]]:
    """{KPI: {mã che: hạng}} từ `raw_measures`."""
    out = {}
    for (kpi, code), value in measures.items():
        out.setdefault(kpi, {})[code] = value
    return {kpi: ranks(values) for kpi, values in out.items()}


def page_deviations(page_path: str | Path, raw_path: str | Path, raw_actual: dict, extra: dict, to_real: dict, reasons: dict) -> dict:
    """{(khoá KPI / mã khoản, mã che): chú thích} cho ô có số thực hiện khác trang sếp. Tên trang không ghép được mã ⇒ ValueError (dừng)."""
    from kpi_agent.core.load_raw import name_to_code, raw_employee_names
    data, names = page_data(str(page_path)), raw_employee_names(str(raw_path))
    to_masked = {real: masked for masked, real in to_real.items()}
    ours = dict(raw_actual)
    for line in extra["lines"]:
        ours[(line["item"], line["employee_id"])] = line["actual"]
    gate = extra.get("gate_progress")
    for line in (gate or {}).get("lines", []):
        ours[(gate["id"], line["employee_id"])] = line["actual"]      # khoản điều kiện "07" — cũng so với trang (13/11: chỗ khác trang phải hiện, không im)
    sections = list(zip(KPI_ORDER, data["kpi"]))
    item_ids = {item["id"] for item in extra["items"]}
    for item in data["bon"]:
        found = re.match(r"Thưởng 0(\d)", item["title"])
        if found and f"T0{found.group(1)}" in item_ids:
            sections.append((f"T0{found.group(1)}", item))
        elif gate and re.match(rf"Thưởng {re.escape(gate['id'])}\b", item["title"]):
            sections.append((gate["id"], item))
    out = {}
    for key, item in sections:
        scale = MILLION if item["unit"] == "tr" else 1
        for row in item["rows"]:
            code = to_masked[name_to_code(row["n"], names)]
            if abs(float(ours[(key, code)]) / scale - float(row["a"])) > PAGE_TOLERANCE:
                shown = f"{row['a']:.{item['dec']}f}".replace(".", ",")
                # Lý do riêng của một ô ("<khoá>/<mã che>") thắng lý do chung của khoá: cùng một KPI có thể lệch vì nguyên nhân khác nhau ở từng người
                reason = reasons.get(f"{key}/{code}", reasons.get(key, UNKNOWN_REASON))
                out[(key, code)] = f"{reason} (trang: {shown} {item['unit']})"
    return out


def tracking_data(board: dict, extra: dict, measures: dict, deviations: dict, reasons: dict, compared_with_page: bool,
                  page_view: dict | None = None) -> dict:
    """JSON cho tab "Theo dõi": bảng đội nguyên văn + hạng (theo `raw_measures`) + Thưởng thêm + ô lệch. `deviations` key (khoá, mã che)."""
    if board.get("refused"):
        return board
    by_item = {}
    for line in extra["lines"]:
        by_item.setdefault(line["item"], {})[line["employee_id"]] = line["actual"]
    extra_ranks = {item: ranks(values) for item, values in by_item.items()}
    return {**board, "ranks": kpi_ranks(measures),
            "extra": {"items": extra["items"], "lines": extra["lines"], "employee_total": extra["employee_total"], "team_total": extra["team_total"],
                      "ranks": extra_ranks},
            "deviations": [{"key": key, "employee_id": code, "note": note} for (key, code), note in sorted(deviations.items())],
            **({"page_view": page_view} if page_view is not None else {}),
            "known_reasons": reasons, "compared_with_page": compared_with_page,
            "rank_note": "hạng xếp theo số thực hiện / % chưa làm tròn (lớn trước, bằng nhau cùng hạng) — backend không có hạng",
            "gap_note": "còn thiếu = tới MỐC THƯỞNG KẾ TIẾP (trang theo dõi của đội ghi còn thiếu tới 100 % chỉ tiêu — hai cột khác nghĩa)"}


# Kiểu xem "8 KPI" theo bố cục trang theo dõi của đội (User: "dựng đầy đủ các trường" như trang của đội) ----------
# Tên nút đúng chữ trang của đội (thứ tự = KPI_ORDER); tên bên mình hiện nhỏ bên cạnh.
PAGE_KPI_TITLES = {"Turnover": "KPI 1: Sell out", "PC_BT": "KPI 2: Số đơn đạt MOQ", "LPPC": "KPI 3: LPPC", "ASO_ALL": "KPI 4: Bao phủ ASO",
                   "ASO_Focus": "KPI 5: Trận Xanh", "ASO_Focus_2": "KPI 6: Trận vàng", "PC_ON": "KPI 7: Bao phủ kênh On", "LPPC_Meat": "KPI 8: LPPC Meat"}
RATIO_TOTAL_NOTE = "dòng Tổng của chỉ tiêu dạng tỷ lệ cộng tỷ lệ của từng người rồi chia tổng chỉ tiêu (cách trang theo dõi của đội cộng) — không phải tỷ lệ chung của cả đội"
PER_DAY_CLOSED_NOTE = "Kỳ đã hết ngày làm việc — 'cần mỗi ngày' hiện bằng đúng phần còn thiếu (như trang theo dõi của đội)"


def _pct_pair(fraction: float) -> dict:
    """% so với chỉ tiêu: 2 số lẻ làm tròn XUỐNG (cùng hàm với công cụ — luật "% đúng y") + số nguyên làm tròn gần nhất như trang của đội."""
    from kpi_agent.core.compute_bonus import floor_pct
    return {"pct_to_target": float(floor_pct(fraction)), "pct_rounded": math.floor(fraction * 100 + 0.5)}


def _ranked_rows(entries: list[dict], time_fraction: float, days_left: int, shown) -> tuple[list[dict], dict]:
    """Dòng xếp hạng + ô tổng của MỘT chỉ tiêu / khoản. `entries` = [{employee_id, target, actual, …trường kèm theo}] (số chưa làm tròn);
    `shown(value, need_at_least)` = cách làm tròn để HIỆN (thực hiện làm tròn xuống, phần còn thiếu làm tròn lên)."""
    fractions = {entry["employee_id"]: (entry["actual"] / entry["target"] if entry["target"] else 0.0) for entry in entries}
    rank = ranks(fractions)
    rows = []
    for entry in sorted(entries, key=lambda item: (rank[item["employee_id"]], item["employee_id"])):
        code, shortfall_raw = entry["employee_id"], max(0.0, entry["target"] - entry["actual"])
        shortfall = shown(shortfall_raw, True)
        rows.append({**entry, "rank": rank[code], "actual": shown(entry["actual"], False), **_pct_pair(fractions[code]),
                     "on_track": fractions[code] >= time_fraction, "shortfall": shortfall,
                     "per_day": shown(shortfall_raw / days_left, True) if days_left else shortfall})
    target_sum, actual_sum = sum(entry["target"] for entry in entries), sum(entry["actual"] for entry in entries)
    team_fraction = actual_sum / target_sum if target_sum else 0.0
    team = {"target": shown(target_sum, False), "actual": shown(actual_sum, False), **_pct_pair(team_fraction), "on_track": team_fraction >= time_fraction}
    return rows, team


def _extra_items(extra: dict, time_fraction: float, days_left: int) -> dict:
    """9 khoản Thưởng thêm theo cùng khuôn (trang của đội dùng một khuôn cho 8 KPI và Thưởng thêm). "Chỉ tiêu" của khoản đếm thùng / điểm bán là
    NGƯỠNG của từng người (trang cũng lấy ngưỡng: 14 người × 20 thùng = 280). Dòng kèm tiền + cách tính của mình (trang của đội không có)."""
    def shown(money: bool):
        def rounded(value: float, need_at_least: bool):
            if money:
                return math.ceil(value) if need_at_least else round(value)
            scaled = round(value * 100, 6)     # thùng có thể lẻ: 2 số lẻ, phần còn thiếu làm tròn LÊN
            return (math.ceil(scaled) if need_at_least else math.floor(scaled)) / 100
        return rounded
    out = {}
    gate = extra.get("gate_progress")
    for item in extra["items"]:
        if gate and item["id"] == gate["before"]:
            # "07": điều kiện chung của các khoản 7.x — trang của đội hiện thành một khoản riêng, chỉ có % (không tiền ⇒ dòng không mang amount / how)
            rows, team = _ranked_rows([dict(line) for line in gate["lines"]], time_fraction, days_left, shown(True))
            out[gate["id"]] = {"page_title": f"Thưởng {gate['id']}: Ngành thịt chế biến (điều kiện)", "our_name": gate["id"],
                               "rule_text": f"Doanh số cả ngành {gate['sub_div']} so với chỉ tiêu {gate['id']} của từng người — điều kiện để xét các khoản 7.x, không có tiền riêng",
                               "tiers": [], "is_ratio": False, "money": True, "unit": "đ", "team": team, "rows": rows}
        money = item.get("measure") in ("sales_pct_of_target", "sales_rate_of_target")
        entries = [{"employee_id": line["employee_id"], "target": line["target"], "actual": line["actual"], "amount": line["amount"], "how": line["how"]}
                   for line in extra["lines"] if line["item"] == item["id"]]
        rows, team = _ranked_rows(entries, time_fraction, days_left, shown(money))
        out[item["id"]] = {"page_title": item["title"], "our_name": item["id"], "rule_text": item["label"], "tiers": [], "is_ratio": False, "money": money,
                           "unit": "đ" if money else ("thùng" if item.get("measure") == "cases" else "điểm bán"), "team": team, "rows": rows}
    return out


def page_view(ctx, extra: dict | None = None) -> dict:
    """Số cho mục "8 KPI" (+ "Thưởng thêm" khi có `extra`) vẽ theo bố cục trang của đội: tiến độ thời gian, mỗi chỉ tiêu có chữ mốc, 3 ô tổng, 14 dòng
    xếp hạng theo % chỉ tiêu, còn thiếu tới 100 % CHỈ TIÊU (khác "còn thiếu tới mốc thưởng kế" của bảng đội), cần mỗi ngày. Phần 8 KPI KHÔNG có tiền
    thưởng (trang của đội không hiện).

    Chỉ ĐỌC số lõi đã tính (`ctx.results`, kết quả Thưởng thêm) rồi chia / trừ / xếp hạng để trình bày — không tính lại KPI, không đụng tiền. Trang không tự tính số nào.
    """
    from kpi_agent.backend.queries import _shown
    from kpi_agent.core.compute_bonus import kpi_tiers
    masked_of = {real: masked for masked, real in ctx.to_real.items()}
    days_done = ctx.days_total - ctx.days_left
    time_fraction = days_done / ctx.days_total if ctx.days_total else 0.0
    kpis = {}
    for kpi in KPI_ORDER:
        entries = [{"employee_id": masked, "target": ctx.results[real][kpi].target, "actual": ctx.results[real][kpi].actual} for real, masked in masked_of.items()]
        rows, team = _ranked_rows(entries, time_fraction, ctx.days_left, lambda value, need_at_least, kpi=kpi: _shown(kpi, value, need_at_least=need_at_least))
        by_region = {}   # mốc + tiền chép từ tờ luật; LPPC khác nhau theo miền ⇒ ghi theo miền có trong đội
        for real in masked_of:
            by_region.setdefault(ctx.regions[real].split(" - ", 1)[-1], kpi_tiers(ctx.rules, kpi, ctx.regions[real]))
        tier_sets = list(by_region.items())
        same = all(tiers == tier_sets[0][1] for _, tiers in tier_sets)
        kpis[kpi] = {"page_title": PAGE_KPI_TITLES[kpi], "our_name": kpi, "rule_text": None,
                     "tiers": [{"region": None if same else region, "label": tier["label"], "amount": tier["printed_amount"]}
                               for region, tiers in (tier_sets[:1] if same else tier_sets) for tier in tiers],
                     "is_ratio": ctx.rules["kpis"][kpi]["measure"] != "pct_of_target", "money": kpi == "Turnover",
                     "unit": "đ" if kpi == "Turnover" else None, "team": team, "rows": rows}
    return {"cutoff": ctx.cutoff.isoformat(), "days_done": days_done, "days_total": ctx.days_total, "days_left": ctx.days_left,
            "time_progress_pct": _pct_pair(time_fraction)["pct_to_target"], "kpis": kpis,
            **({"extra": _extra_items(extra, time_fraction, ctx.days_left)} if extra is not None else {}),
            "ratio_total_note": RATIO_TOTAL_NOTE,
            "per_day_note": PER_DAY_CLOSED_NOTE if ctx.days_left == 0 else None,
            "shortfall_note": "Còn thiếu ở đây = tới 100 % CHỈ TIÊU (nghĩa của trang theo dõi của đội); còn thiếu tới mốc THƯỞNG kế tiếp xem ở Cả đội › Theo chỉ tiêu"}
