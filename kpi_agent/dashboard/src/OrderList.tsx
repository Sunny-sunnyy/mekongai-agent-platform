import { useCallback, useState } from "react";
import { type OrderLineRow, getOrderLines, isRefused } from "./api";
import { dateOnly, dong, exact, vnd } from "./format";
import { Nv } from "./Names";
import { useLoad } from "./useLoad";

// "Đơn được tính" của MỘT nhân viên cho MỘT chỉ tiêu (câu khiếu nại "đơn nào của tôi được tính"): mỗi đơn (hoặc cửa hàng) một dòng,
// được tính / không và vì sao. TRANG KHÔNG XÉT LUẬT: được tính hay không + lý do do backend trả (/api/order-lines — backend đã tự đối chiếu số
// "được tính" với bảng thưởng; lệch thì backend từ chối chứ không trả danh sách). Trang chỉ lọc, tìm và đếm số dòng đang hiện.
// Tên cửa hàng vẽ bằng text node; không gửi vào câu hỏi cho trợ lý.
const PAGE = 100;

export function OrderList({ employeeId, kpi, onClose }: { employeeId: string; kpi: string; onClose: () => void }) {
  const load = useLoad(useCallback(() => getOrderLines(employeeId, kpi), [employeeId, kpi]));
  const [status, setStatus] = useState<"" | "co" | "khong">("");
  const [reason, setReason] = useState("");
  const [text, setText] = useState("");
  const [shown, setShown] = useState(PAGE);
  const head = (
    <div className="orders-head">
      <h3>Đơn được tính — <Nv id={employeeId} /> · {kpi}</h3>
      <button type="button" className="ghost small" onClick={onClose}>Đóng</button>
    </div>
  );
  if (load.error) return <section className="orders">{head}<div className="notice error">Không tải được danh sách đơn ({load.error.message}).</div></section>;
  if (!load.data) return <section className="orders">{head}<p className="hint">Đang đọc đơn hàng (lần đầu ≈ 20 giây)…</p></section>;
  if (isRefused(load.data)) return <section className="orders">{head}<div className="notice refused">{load.data.refused}</div></section>;
  const data = load.data;
  const byOutlet = data.unit === "outlet";
  const needle = text.trim().toLowerCase();
  const rows = data.rows.filter((row: OrderLineRow) =>
    (status === "" || row.counted === (status === "co")) && (reason === "" || row.reasons.includes(reason)) &&
    (needle === "" || [row.order_num ?? "", row.outlet_code, row.outlet_name ?? ""].some((value) => value.toLowerCase().includes(needle))));
  const money = kpi === "Turnover";
  // LPPC / LPPC Meat (bậc 2, 13/11): số của bảng thưởng là TỶ LỆ mã ÷ đơn ⇒ hiện cả tử, mẫu (backend đã so cả hai với lõi)
  const ratio = data.summary.lines !== undefined && data.summary.orders !== undefined;
  const valueText = (value: number) => money ? dong(value) : ratio ? ratioText(value) : exact(value);
  return (
    <section className="orders" aria-label="Đơn được tính">
      {head}
      <div className="stats orders-stats">
        <div className="stat"><div className="v">{exact(data.summary.counted)}</div><div className="l">{byOutlet ? "cửa hàng" : "đơn"} được tính</div></div>
        <div className="stat"><div className="v">{exact(data.summary.not_counted)}</div><div className="l">{byOutlet ? "cửa hàng" : "đơn"} không được tính</div></div>
        <div className="stat">
          <div className="v">{valueText(data.summary.counted_value)}</div>
          <div className="l">{ratio && <>{exact(data.summary.lines!)} mã ÷ {exact(data.summary.orders!)} đơn · </>}✓ khớp số thực hiện trong bảng thưởng ({valueText(data.summary.core_actual)})</div>
        </div>
      </div>
      <div className="outlet-filters">
        <input type="search" value={text} onChange={(event) => { setText(event.target.value); setShown(PAGE); }} placeholder={byOutlet ? "Tìm tên, mã cửa hàng" : "Tìm số đơn, tên, mã cửa hàng"} aria-label="Tìm trong danh sách" />
        <select value={status} onChange={(event) => { setStatus(event.target.value as "" | "co" | "khong"); setShown(PAGE); }} aria-label="Lọc được tính hay không">
          <option value="">Được tính và không được tính</option><option value="co">Được tính</option><option value="khong">Không được tính</option>
        </select>
        <select value={reason} onChange={(event) => { setReason(event.target.value); setShown(PAGE); }} aria-label="Lọc theo lý do">
          <option value="">Mọi lý do</option>
          {Object.entries(data.summary.by_reason).map(([name, count]) => <option key={name} value={name}>{name} ({count})</option>)}
        </select>
      </div>
      <p className="outlet-count" role="status">Đang hiện {exact(Math.min(shown, rows.length))} / {exact(rows.length)} dòng theo bộ lọc (tất cả {exact(data.rows.length)}).</p>
      <div className="board">
        <div className="scroll">
          <table className="compact-table orders-table">
            <thead>
              {byOutlet ? (
                <tr><th scope="col" className="left">Cửa hàng</th><th scope="col">Số đơn</th><th scope="col">Doanh số các mã đủ số lượng</th><th scope="col" className="left">Được tính</th><th scope="col" className="left">Vì sao không</th></tr>
              ) : (
                <tr><th scope="col" className="left">Ngày</th><th scope="col" className="left">Số đơn</th><th scope="col" className="left">Cửa hàng</th><th scope="col" className="left">Kênh</th>
                  {!money && <th scope="col">Số mã đủ số lượng</th>}<th scope="col">Doanh số đơn</th><th scope="col" className="left">Được tính</th><th scope="col" className="left">Vì sao không</th></tr>
              )}
            </thead>
            <tbody>
              {rows.slice(0, shown).map((row, index) => (
                <tr key={`${row.order_num ?? ""}/${row.outlet_code}/${row.day ?? ""}/${index}`} className={row.counted ? "" : "not-counted"}>
                  {!byOutlet && <td className="left">{dateOnly(row.day)}</td>}
                  {!byOutlet && <td className="left">{row.order_num}</td>}
                  <td className="left wrap">{row.outlet_name ?? "—"} <span className="hint">{row.outlet_code}</span></td>
                  {byOutlet ? <td>{exact(row.order_count)}</td> : <td className="left">{row.channel}</td>}
                  {!byOutlet && !money && <td>{exact(row.moq_skus)}</td>}
                  <td>{dong(byOutlet ? row.moq_sales : row.sales)}</td>
                  <td className={`left ${row.counted ? "du_moc" : "chua_tien"}`}>{row.counted ? "✓ được tính" : "✕ không"}{money && row.counted ? ` · ${vnd(row.counted_sales)}` : ""}{ratio && row.counted ? ` · ${exact(row.counted_lines)} mã` : ""}</td>
                  <td className="left wrap">{row.reasons.join("; ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {rows.length > shown && <button type="button" className="ghost outlet-more" onClick={() => setShown(shown + PAGE)}>Xem thêm ({rows.length - shown} dòng nữa)</button>}
      <ul className="outlet-notes">{data.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>
    </section>
  );
}

// Tỷ lệ LPPC: 2 số lẻ LÀM TRÒN XUỐNG kiểu Việt (6,59) — đúng cách bảng thưởng hiện LPPC (`queries._shown`); tử ÷ mẫu hiện cạnh bên là số chính xác
function ratioText(value: number) {
  return new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(Math.floor(value * 100 + 1e-9) / 100);
}
