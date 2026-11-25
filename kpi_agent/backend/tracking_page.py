"""Trang theo dõi KPI dạng MỘT file.html tĩnh tự chứa (bản 1, /2026 — kế hoạch

Vì sao: User nghĩ sếp muốn một trang theo dõi kiểu trang sếp gửi ("Tracking KPI …"). Trang này KHÔNG tính số nào: mọi số lấy nguyên từ
`queries.team_board` (8 KPI, cùng luật làm tròn với công cụ) và Thưởng thêm của backend (`main._extra_bonus` ⇒ lõi `core/compute_extra_bonus`).
Riêng "hạng" xếp ở đây (theo số đo của từng KPI, bằng nhau cùng hạng) vì backend không có — ghi rõ ở chân trang.

Vì sao file nằm ở `backend/` mà KHÔNG trong `main.CODE_FILES`: trang chỉ trình bày ⇒ đổi trang không được làm đổi dấu vân tay bản tính (mất "Đã soát").
An toàn: mọi chữ qua `html.escape`; 0 đường mạng (không CDN, không fetch, không `src=` ra ngoài) — mở được khi không có mạng, gửi file không lộ gì ra ngoài."""
from html import escape

from kpi_agent.backend.tracking_view import ranks
from kpi_agent.core.rules import KPI_ORDER

KPI_TITLES = {"Turnover": "KPI 1 — Doanh số (sell out)", "PC_BT": "KPI 2 — Đơn thoả MOQ kênh Off", "LPPC": "KPI 3 — LPPC (trừ Meat & Bia)",
              "ASO_ALL": "KPI 4 — ASO ALL", "ASO_Focus": "KPI 5 — ASO Focus (Trà 365)", "ASO_Focus_2": "KPI 6 — ASO Focus 2 (Homey 2,9 kg)",
              "PC_ON": "KPI 7 — Đơn thoả MOQ kênh On", "LPPC_Meat": "KPI 8 — LPPC Meat"}
STATUS_TEXT = {"du_moc": "đủ mốc", "moc_thap": "có tiền, còn mốc", "chua_tien": "chưa có tiền"}


class TrackingPageError(ValueError):
    """Dữ liệu vào thiếu / bị từ chối ⇒ không dựng trang (không sinh trang rỗng)."""


def money(value: int | float) -> str:
    """12345678 ⇒ "12.345.678" (đúng tới đồng, không làm tròn — luật tiền đúng y)."""
    return f"{int(value):,}".replace(",", ".")


def number(value) -> str:
    """Số đo như công cụ đã làm tròn: 1234.5 ⇒ "1.234,5"; None ⇒ "—"."""
    if value is None:
        return "—"
    whole, _, frac = f"{float(value):,.4f}".rstrip("0").rstrip(".").partition(".")
    return whole.replace(",", ".") + ("," + frac if frac else "")


def check_inputs(board: dict, extra: dict) -> None:
    """Ca xấu ⇒ dừng với lời rõ: công cụ từ chối, thiếu NV / KPI / khoản, hai nguồn khác danh sách NV."""
    if board.get("refused") or "employees" not in board:
        raise TrackingPageError(f"bảng đội không có số: {board.get('reason') or 'thiếu employees'}")
    if not isinstance(extra, dict) or "lines" not in extra:
        raise TrackingPageError(f"thưởng thêm không có số: {extra if isinstance(extra, str) else 'thiếu lines'}")
    codes = [row["employee_id"] for row in board["employees"]]
    if not codes:
        raise TrackingPageError("bảng đội 0 nhân viên — không dựng trang rỗng")
    for row in board["employees"]:
        missing = [kpi for kpi in KPI_ORDER if kpi not in row["cells"]]
        if missing:
            raise TrackingPageError(f"{row['employee_id']} thiếu KPI {', '.join(missing)}")
    item_ids = [item["id"] for item in extra["items"]]
    for code in codes:
        have = {line["item"] for line in extra["lines"] if line["employee_id"] == code}
        if have != set(item_ids):
            raise TrackingPageError(f"{code} thiếu khoản thưởng thêm {', '.join(sorted(set(item_ids) - have))}")


def render(board: dict, extra: dict, meta: dict, deviations: dict | None = None, names: dict[str, str] | None = None,
           kpi_ranks: dict[str, dict[str, int]] | None = None) -> str:
    """board = `queries.team_board`; extra = Thưởng thêm của backend; meta = ngày chốt / nguồn / mã bản tính;
    deviations = {(khoá KPI hoặc mã khoản, mã che NV): chú thích} — ô lệch trang sếp; names = {mã che: tên hiện} (None ⇒ chỉ mã che)."""
    check_inputs(board, extra)
    deviations = deviations or {}
    shown = lambda code: escape(names[code] if names and code in names else code)
    codes = [row["employee_id"] for row in board["employees"]]
    tabs = [("tong-quan", "Tổng quan")] + [(kpi, KPI_TITLES.get(kpi, kpi).split(" — ")[0]) for kpi in KPI_ORDER] + [("thuong-them", "Thưởng thêm")]
    sections = [_overview(board, extra, codes, shown)] + [_kpi_section(board, kpi, codes, shown, deviations, (kpi_ranks or {}).get(kpi)) for kpi in KPI_ORDER] + \
               [_extra_section(extra, codes, shown, deviations)]
    buttons = "".join(f'<button data-tab="{escape(key)}"{" class=on" if i == 0 else ""}>{escape(label)}</button>' for i, (key, label) in enumerate(tabs))
    return PAGE.format(title=escape(f"Theo dõi KPI {meta['period']} — chốt {meta['cutoff']}"), status=escape(meta.get("status_label") or ""),
                       buttons=buttons, sections="".join(sections), footer=_footer(meta, deviations))


def _overview(board: dict, extra: dict, codes: list[str], shown) -> str:
    extra_total = extra["employee_total"]
    rows = "".join(
        f"<tr><td>{shown(row['employee_id'])}</td><td class=r>{money(row['business_salary'])}</td>"
        f"<td class=r>{money(extra_total[row['employee_id']])}</td><td class=r><b>{money(row['business_salary'] + extra_total[row['employee_id']])}</b></td></tr>"
        for row in board["employees"])
    team = board["team_total"] + extra["team_total"]
    return (f'<section id="tong-quan" class=on><h2>Tổng quan — {escape(board.get("team_label") or "")}</h2>'
            f"<p>Ngày làm việc: {board['working_days_total']} · còn sau ngày chốt: {board['working_days_left_after_cutoff']}</p>"
            f"<div class=cards><div><span>Tiền 8 KPI cả đội</span><b>{money(board['team_total'])} đ</b></div>"
            f"<div><span>Thưởng thêm cả đội</span><b>{money(extra['team_total'])} đ</b></div>"
            f"<div><span>Cộng</span><b>{money(team)} đ</b></div></div>"
            f"<table><tr><th>Nhân viên</th><th>8 KPI (đ)</th><th>Thưởng thêm (đ)</th><th>Cộng (đ)</th></tr>{rows}"
            f"<tr class=tot><td>Cả đội</td><td class=r>{money(board['team_total'])}</td><td class=r>{money(extra['team_total'])}</td>"
            f"<td class=r>{money(team)}</td></tr></table></section>")


def _kpi_section(board: dict, kpi: str, codes: list[str], shown, deviations: dict, rank: dict | None) -> str:
    cells = {row["employee_id"]: row["cells"][kpi] for row in board["employees"]}
    # Hạng theo số THÔ (script đưa `tracking_view.kpi_ranks`); không có ⇒ theo số đã in (bảng giả trong test)
    rank = rank or ranks({code: (cell["pct"] if cell["measure"] == "pct" else cell["value"]) or 0 for code, cell in cells.items()})
    rows = []
    for code in sorted(codes, key=lambda c: (rank[c], c)):
        cell = cells[code]
        note = deviations.get((kpi, code))
        mark = f' <span class=dev title="{escape(note)}">⚠</span>' if note else ""
        pct = f"{number(cell['pct'])} %" if cell["pct"] is not None else "—"
        rows.append(
            f"<tr class={cell['status']}><td>{rank[code]}</td><td>{shown(code)}</td><td class=r>{number(cell['actual'])}{mark}</td>"
            f"<td class=r>{number(cell['target'])}</td><td class=r>{pct}</td><td>{escape(cell['reached_tier'] or '—')}</td>"
            f"<td class=r>{number(cell['gap_to_next'])}</td><td>{escape(cell['gap_per_day_text'] or '—')}</td>"
            f"<td class=r>{money(cell['amount'])}</td><td>{STATUS_TEXT[cell['status']]}</td></tr>"
            + (f'<tr class=devrow><td></td><td colspan=9>⚠ trang sếp tính khác: {escape(note)}</td></tr>' if note else ""))
    paid = board["kpi_summary"][kpi]
    return (f'<section id="{escape(kpi)}"><h2>{escape(KPI_TITLES.get(kpi, kpi))}</h2>'
            f"<p>Có tiền: {paid['paid_count']} / {len(codes)} người · đủ mốc cao nhất: {paid['full_count']}</p>"
            "<table><tr><th>Hạng</th><th>Nhân viên</th><th>Thực hiện</th><th>Chỉ tiêu</th><th>% đạt</th><th>Mốc đã đạt</th>"
            "<th>Còn thiếu tới mốc thưởng kế (trang sếp: tới 100 %)</th><th>Cần mỗi ngày</th><th>Tiền (đ)</th><th>Trạng thái</th></tr>" + "".join(rows) + "</table></section>")


def _extra_section(extra: dict, codes: list[str], shown, deviations: dict) -> str:
    by = {(line["employee_id"], line["item"]): line for line in extra["lines"]}
    blocks = []
    for item in extra["items"]:
        rows = []
        for code in codes:
            line = by[(code, item["id"])]
            note = deviations.get((item["id"], code))
            mark = f' <span class=dev title="{escape(note)}">⚠</span>' if note else ""
            css = "du_moc" if line["amount"] > 0 else "chua_tien"
            rows.append(f"<tr class={css}><td>{shown(code)}</td><td class=r>{number(line['actual'])}{mark}</td><td>{escape(line['how'])}</td>"
                        f"<td class=r>{money(line['amount'])}</td></tr>"
                        + (f'<tr class=devrow><td></td><td colspan=3>⚠ trang sếp tính khác: {escape(note)}</td></tr>' if note else ""))
        total = sum(by[(code, item["id"])]["amount"] for code in codes)
        blocks.append(f"<h3>{escape(item['title'])} — {escape(item['label'])}</h3>"
                      "<table><tr><th>Nhân viên</th><th>Thực hiện</th><th>Cách tính</th><th>Tiền (đ)</th></tr>" + "".join(rows) +
                      f"<tr class=tot><td>Cả đội</td><td></td><td></td><td class=r>{money(total)}</td></tr></table>")
    return f'<section id="thuong-them"><h2>Thưởng thêm (ngoài 8 KPI) — cả đội {money(extra["team_total"])} đ</h2>' + "".join(blocks) + "</section>"


def _footer(meta: dict, deviations: dict) -> str:
    files = "".join(f"<li>{escape(name)} · sha256 {escape(sha[:16])}…</li>" for name, sha in sorted(meta["files_sha256"].items()))
    return (f"<p>Kỳ {escape(meta['period'])} · ngày chốt {escape(meta['cutoff'])} · sinh lúc {escape(meta['generated_at'])}</p>"
            f"<p>Mã bản tính (dữ liệu + code + workflow): {escape(meta['data_version'][:16])}…</p><ul>{files}</ul>"
            f"<p>Số lấy nguyên từ backend KPI (bảng đội + Thưởng thêm); trang chỉ xếp <b>hạng</b> (theo số chưa làm tròn, lớn trước, bằng nhau cùng hạng). "
            f"Ô ⚠ = khác trang theo dõi của đội ({len(deviations)} ô) — lý do ghi ngay dưới dòng.</p>")


PAGE = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#fff;color:#1f2933}}
header{{padding:12px 16px;border-bottom:1px solid #d9dee3}} header h1{{font-size:18px;margin:0}} header p{{margin:4px 0 0;color:#8a4b00}}
nav{{display:flex;flex-wrap:wrap;gap:4px;padding:8px 16px;border-bottom:1px solid #d9dee3}}
nav button{{border:1px solid #c5ccd3;background:#f5f7f9;border-radius:4px;padding:4px 8px;cursor:pointer}} nav button.on{{background:#1f6feb;color:#fff;border-color:#1f6feb}}
main{{padding:8px 16px}} section{{display:none}} section.on{{display:block}}
table{{border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:13px}} th,td{{border:1px solid #e1e5e9;padding:4px 6px;text-align:left}}
th{{background:#f5f7f9}} td.r{{text-align:right;white-space:nowrap}} tr.tot td{{font-weight:600;background:#f5f7f9}}
tr.du_moc td{{background:#e9f7ef}} tr.moc_thap td{{background:#fff8e1}} tr.chua_tien td{{background:#fdecea}}
tr.devrow td{{background:#fff;color:#8a4b00;font-size:12px}} .dev{{color:#b54708;cursor:help}}
.cards{{display:flex;flex-wrap:wrap;gap:12px}} .cards div{{border:1px solid #d9dee3;border-radius:6px;padding:8px 12px}} .cards span{{display:block;color:#52606d;font-size:12px}}
footer{{padding:8px 16px;border-top:1px solid #d9dee3;color:#52606d;font-size:12px}}
</style></head>
<body><header><h1>{title}</h1><p>{status}</p></header>
<nav>{buttons}</nav><main>{sections}</main><footer>{footer}</footer>
<script>
document.querySelectorAll("nav button").forEach(function (b) {{
  b.onclick = function () {{
    document.querySelectorAll("nav button, section").forEach(function (e) {{ e.classList.remove("on"); }});
    b.classList.add("on"); document.getElementById(b.dataset.tab).classList.add("on");
  }};
}});
</script></body></html>
"""
