import { useContext, useState, type ReactNode } from "react";
import { type OutletRow, type TeaRow, getOutlets, getTea365, isRefused } from "./api";
import { dong, exact } from "./format";
import { NamesContext, Nv, employeeLabel } from "./Names";
import { useLoad } from "./useLoad";

// Hai màn "Chi tiết điểm bán" + "Trà 365" (hai tab còn thiếu so với trang theo dõi của đội).
// CHỈ ĐẾM: không có tiền thưởng nào ở đây. Mọi số trong 3 ô tổng + bảng theo nhân viên do backend trả (/api/outlets, /api/tea-365);
// trang chỉ LỌC và ĐẾM số dòng đang hiện (như trang của đội), không cộng tiền, không tự xét đạt / chưa đạt.
// Tên + địa chỉ cửa hàng chỉ vẽ thành chữ (text node), không đưa vào câu hỏi gửi trợ lý.
const PAGE = 60;                       // hiện 60 thẻ mỗi lần như trang của đội, bấm "Xem thêm" để hiện tiếp
type Basic = Pick<OutletRow, "outlet_code" | "outlet_name" | "employee_id" | "visit_days">;

// Bộ lọc chung của hai màn: ô tìm (tên, mã, địa chỉ), nhân viên, ngày viếng thăm. Trả về các ô chọn + hàm lọc.
function useBasicFilters(rows: Basic[]) {
  const { mode, names } = useContext(NamesContext);
  const [text, setText] = useState("");
  const [employee, setEmployee] = useState("");
  const [day, setDay] = useState("");
  const employees = [...new Set(rows.map((row) => row.employee_id))].sort();
  const days = [...new Set(rows.flatMap((row) => row.visit_days))].sort();
  const needle = text.trim().toLowerCase();
  const keep = (row: Basic & { address?: string | null }) =>
    (employee === "" || row.employee_id === employee) && (day === "" || row.visit_days.includes(day)) &&
    (needle === "" || [row.outlet_code, row.outlet_name ?? "", row.address ?? ""].some((value) => value.toLowerCase().includes(needle)));
  const controls = (
    <>
      <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder="Tìm tên, mã, địa chỉ điểm bán" aria-label="Tìm điểm bán" />
      <select value={employee} onChange={(event) => setEmployee(event.target.value)} aria-label="Lọc theo nhân viên">
        <option value="">Mọi nhân viên</option>
        {employees.map((id) => <option key={id} value={id}>{employeeLabel(id, mode, names)}</option>)}
      </select>
      <select value={day} onChange={(event) => setDay(event.target.value)} aria-label="Lọc theo ngày viếng thăm">
        <option value="">Mọi ngày viếng thăm</option>
        {days.map((name) => <option key={name} value={name}>{name}</option>)}
      </select>
    </>
  );
  return { controls, keep, employee, setEmployee };
}

// Danh sách thẻ: hiện từng đợt PAGE thẻ. `resetKey` đổi (đổi bộ lọc) ⇒ về đợt đầu.
function CardList<T extends Basic>({ rows, resetKey, card }: { rows: T[]; resetKey: string; card: (row: T) => ReactNode }) {
  const [state, setState] = useState({ key: resetKey, shown: PAGE });
  const shown = state.key === resetKey ? state.shown : PAGE;
  return (
    <>
      <div className="outlet-cards">{rows.slice(0, shown).map((row) => <article className="outlet-card" key={`${row.employee_id}/${row.outlet_code}`}>{card(row)}</article>)}</div>
      {rows.length > shown && (
        <button type="button" className="ghost outlet-more" onClick={() => setState({ key: resetKey, shown: shown + PAGE })}>
          Xem thêm ({rows.length - shown} điểm bán nữa)
        </button>
      )}
    </>
  );
}

const outletTitle = (row: Basic) => row.outlet_name ?? `Điểm bán ${row.outlet_code}`;
const visitText = (row: Basic) => (row.visit_days.length ? row.visit_days.join(", ") : "chưa có ngày viếng thăm");

function Loading({ error, what }: { error?: Error; what: string }) {
  if (error) return <section className="notice error">Không tải được {what} ({error.message}).</section>;
  return <p className="hint">Đang đếm {what} (lần đầu ≈ 15 giây)…</p>;
}

type Extra = "" | "on" | "off" | "tea" | "no-tea" | "homey" | "no-homey" | "fish" | "no-fish";
const EXTRA: [Extra, string, (row: OutletRow) => boolean][] = [
  ["on", "Kênh On", (row) => row.channel === "On"], ["off", "Kênh Off", (row) => row.channel === "Off"],
  ["no-tea", "Chưa có Trà 365", (row) => row.tea_cases === 0], ["tea", "Đã có Trà 365", (row) => row.tea_cases > 0],
  ["no-homey", "Chưa có Homey", (row) => !row.homey_reached], ["homey", "Đã có Homey", (row) => row.homey_reached],
  ["no-fish", "Chưa có nước mắm cao cấp", (row) => row.premium_fish_sauce_sales === 0], ["fish", "Đã có nước mắm cao cấp", (row) => row.premium_fish_sauce_sales > 0],
];

export function OutletsView() {
  const load = useLoad(getOutlets);
  const data = load.data && !isRefused(load.data) ? load.data : undefined;
  const filters = useBasicFilters(data?.rows ?? []);
  const [status, setStatus] = useState<"" | "dat" | "chua">("");
  const [extra, setExtra] = useState<Extra>("");
  if (load.data && isRefused(load.data)) return <section className="notice refused">Backend từ chối: {load.data.refused}</section>;
  if (!data) return <Loading error={load.error} what="điểm bán thuộc tuyến" />;
  const extraTest = EXTRA.find(([key]) => key === extra)?.[2];
  const rows = data.rows.filter((row) => filters.keep(row) && (status === "" || row.moq_reached === (status === "dat")) && (!extraTest || extraTest(row)));
  return (
    <section className="outlets-view" aria-label="Chi tiết điểm bán">
      <div className="kpi-head">
        <div>
          <p className="hero-kicker">Điểm bán thuộc tuyến của cả đội</p>
          <div className="stats">
            <div className="stat"><div className="v">{exact(data.summary.outlets)}</div><div className="l">điểm bán thuộc tuyến</div></div>
            <div className="stat"><div className="v">{exact(data.summary.moq_reached)}</div><div className="l">đạt MOQ (có mặt hàng đủ số lượng mua tối thiểu)</div></div>
            <div className="stat"><div className="v">{exact(data.summary.moq_not_reached)}</div><div className="l">chưa đạt MOQ — trong đó {exact(data.summary.without_orders)} điểm chưa có đơn nào trong tháng</div></div>
          </div>
        </div>
      </div>
      <div className="board outlet-by-nv">
        <div className="scroll">
          <table className="compact-table">
            <thead><tr><th scope="col" className="sticky left">Nhân viên (bấm để lọc)</th><th scope="col">Điểm bán thuộc tuyến</th><th scope="col">Đạt MOQ</th><th scope="col">Chưa đạt MOQ</th></tr></thead>
            <tbody>
              {data.by_employee.map((item) => (
                <tr key={item.employee_id} className={filters.employee === item.employee_id ? "on" : ""}>
                  <th scope="row" className="sticky left">
                    <button type="button" aria-pressed={filters.employee === item.employee_id}
                      onClick={() => filters.setEmployee(filters.employee === item.employee_id ? "" : item.employee_id)}><Nv id={item.employee_id} /></button>
                  </th>
                  <td>{exact(item.outlets)}</td><td>{exact(item.moq_reached)}</td><td>{exact(item.moq_not_reached)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="outlet-filters">
        {filters.controls}
        <select value={status} onChange={(event) => setStatus(event.target.value as "" | "dat" | "chua")} aria-label="Lọc theo trạng thái MOQ">
          <option value="">Đạt và chưa đạt MOQ</option><option value="dat">Đạt MOQ</option><option value="chua">Chưa đạt MOQ</option>
        </select>
        <select value={extra} onChange={(event) => setExtra(event.target.value as Extra)} aria-label="Lọc thêm">
          <option value="">Lọc thêm: không</option>
          {EXTRA.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </div>
      <p className="outlet-count" role="status">Đang hiện {exact(rows.length)} / {exact(data.rows.length)} điểm bán theo bộ lọc.</p>
      <CardList rows={rows} resetKey={`${filters.employee}|${status}|${extra}|${rows.length}`} card={(row) => (
        <>
          <div className="card-top">
            <h3>{outletTitle(row)}</h3>
            <span className={`pill ${row.moq_reached ? "du_moc" : "chua_tien"}`}>{row.moq_reached ? "Đạt MOQ" : "Chưa đạt MOQ"}</span>
          </div>
          <div className="line">Mã {row.outlet_code} · <Nv id={row.employee_id} /> · {visitText(row)} · kênh {row.channel}</div>
          {row.has_orders ? (
            <>
              <div className="line">{row.address ?? "chưa có địa chỉ"}</div>
              {/* Dòng 13 bài 15b (13/11): tiền "như trang theo dõi của đội" = sau chiết khấu, gồm hoá đơn xuất trễ — ghi rõ để không lẫn với doanh số KPI 1 */}
              <div className="line">Doanh số tháng <small className="hint">(như trang theo dõi: sau chiết khấu, gồm hoá đơn xuất trễ)</small>: <b>{dong(row.page_sales)}</b></div>
              <div className="line hint">Doanh số tính KPI 1 (trước chiết khấu, gồm hoá đơn xuất trễ đã có ngày xuất): {dong(row.sales)}</div>
              <div className="line">Trà 365: {row.tea_cases > 0 ? <><b>{exact(row.tea_cases)} thùng · {dong(row.page_tea_sales)}</b> <small className="hint">(sau chiết khấu, chỉ dòng đủ số lượng tối thiểu)</small></> : "chưa có"}</div>
              <div className="line">Homey túi 2,9 kg: {row.homey_reached ? <b>Đạt</b> : "–"} · Kênh On: <b>{dong(row.on_sales)}</b> <small className="hint">(trước chiết khấu)</small></div>
              <div className="line">Nước mắm cao cấp: <b>{dong(row.page_premium_fish_sauce_sales)}</b> <small className="hint">(sau chiết khấu)</small></div>
            </>
          ) : (
            <>
              {/* Điểm chưa có đơn: tên + địa chỉ lấy từ danh sách tuyến trong sổ của đội (13/11); sổ không đọc được ⇒ vẫn chưa có tên */}
              {row.address && <div className="line">{row.address}</div>}
              <div className="line">
                Chưa có đơn nào trong tháng{row.name_from_route_sheet ? " — tên và địa chỉ lấy từ danh sách tuyến trong sổ theo dõi của đội." : row.outlet_name ? "." : " — chưa có tên và địa chỉ."}
              </div>
              {/* Điểm CHỈ có đơn xuất hoá đơn trễ: trang theo dõi của đội cộng tiền; mốc 7 N2 (16/11): KPI 1 và KPI 2 (PC_BT) cộng phần đã có ngày xuất (như trang),
                  các KPI còn lại không (công văn 6.1) — m3 */}
              {row.page_sales > 0 && <div className="line">Chỉ có đơn xuất hoá đơn trễ — trang theo dõi tính: <b>{dong(row.page_sales)}</b> <small className="hint">(KPI 1 tính {dong(row.sales)}; KPI 2 cũng tính đơn đã có ngày xuất; các KPI còn lại không tính)</small></div>}
            </>
          )}
        </>
      )} />
      <Notes notes={data.notes} />
    </section>
  );
}

export function TeaView() {
  const load = useLoad(getTea365);
  const data = load.data && !isRefused(load.data) ? load.data : undefined;
  const filters = useBasicFilters(data?.rows ?? []);
  if (load.data && isRefused(load.data)) return <section className="notice refused">Backend từ chối: {load.data.refused}</section>;
  if (!data) return <Loading error={load.error} what="điểm bán mua Trà 365" />;
  const rows = data.rows.filter(filters.keep);
  return (
    <section className="outlets-view" aria-label="Trà 365">
      <p className="only-count">Chỉ đếm — chưa tính tiền Trà 365 theo công văn 419.</p>
      <div className="kpi-head">
        <div>
          <p className="hero-kicker">Điểm bán thuộc tuyến mua từ 1 thùng Trà 365 cộng dồn trong tháng</p>
          <div className="stats">
            <div className="stat"><div className="v">{exact(data.summary.customers)}</div><div className="l">khách mua từ 1 thùng</div></div>
            <div className="stat"><div className="v">{exact(data.summary.cases)}<span className="u"> thùng</span></div><div className="l">tổng số thùng của các khách đó</div></div>
            <div className="stat"><div className="v">{exact(data.summary.customers_from_5)}</div><div className="l">khách mua từ 5 thùng</div></div>
          </div>
        </div>
      </div>
      <div className="board outlet-by-nv">
        <div className="scroll">
          <table className="compact-table">
            <thead><tr><th scope="col" className="sticky left">Nhân viên (bấm để lọc)</th><th scope="col">Khách từ 1 thùng</th><th scope="col">Số thùng</th><th scope="col">Khách từ 5 thùng</th></tr></thead>
            <tbody>
              {data.by_employee.map((item) => (
                <tr key={item.employee_id} className={filters.employee === item.employee_id ? "on" : ""}>
                  <th scope="row" className="sticky left">
                    <button type="button" aria-pressed={filters.employee === item.employee_id}
                      onClick={() => filters.setEmployee(filters.employee === item.employee_id ? "" : item.employee_id)}><Nv id={item.employee_id} /></button>
                  </th>
                  <td>{exact(item.customers)}</td><td>{exact(item.cases)}</td><td>{exact(item.customers_from_5)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="outlet-filters">{filters.controls}</div>
      <p className="outlet-count" role="status">Đang hiện {exact(rows.length)} / {exact(data.rows.length)} khách theo bộ lọc.</p>
      <CardList<TeaRow> rows={rows} resetKey={`${filters.employee}|${rows.length}`} card={(row) => (
        <>
          <h3>{outletTitle(row)}</h3>
          <div className="big">{exact(row.tea_cases)} thùng</div>
          <div className="line">Mã {row.outlet_code} · <Nv id={row.employee_id} /> · {visitText(row)}</div>
        </>
      )} />
      <Notes notes={data.notes} />
    </section>
  );
}

function Notes({ notes }: { notes: string[] }) {
  return <ul className="outlet-notes">{notes.map((note, index) => <li key={index}>{note}</li>)}</ul>;
}
