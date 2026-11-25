import { useContext, useState } from "react";
import { type SpiceCustomer, getSpiceCustomers, isRefused } from "./api";
import { dong, exact, pct } from "./format";
import { NamesContext, Nv, employeeLabel } from "./Names";
import { READ_ONLY } from "./base";
import { useLoad } from "./useLoad";

// Danh sách khách của chương trình 1 % gia vị (trang theo dõi của đội có 1.351 thẻ khách + bộ lọc). Mọi số từng khách do backend trả
// (/api/spice-customers, dùng lại đúng các dòng đã tính của /api/spice-loyalty); trang chỉ LỌC và ĐẾM số thẻ đang hiện.
// Tên + mã khách chỉ vẽ thành chữ, không gửi trợ lý. Tiền ở đây trả cho KHÁCH, không phải lương nhân viên.
const PAGE = 60;
const GROUP_ORDER = ["co_thuong", "du_nen", "gan_nen", "duoi_70"] as const;
const GROUP_SHORT: Record<string, string> = { co_thuong: "Có thưởng", du_nen: "Đủ nền, chưa tới 110 %", gan_nen: "70 % – dưới 100 %", duoi_70: "Dưới 70 %" };

export function SpiceCustomers({ employee, onEmployee }: { employee: string; onEmployee: (id: string) => void }) {
  const load = useLoad(getSpiceCustomers);
  const { mode, names } = useContext(NamesContext);
  const [text, setText] = useState("");
  const [day, setDay] = useState("");
  const [group, setGroup] = useState("");
  const [state, setState] = useState({ key: "", shown: PAGE });
  // Xem qua Capitol: đường /api/spice-customers (tên + mã khách) bị cổng xem cấm cứng (404) ⇒ nói rõ, không báo lỗi tải — cùng cách màn Hội viên
  if (load.error && READ_ONLY) return <section className="notice refused not-in-view">Danh sách từng khách 1 % gia vị: chưa có ở bản xem qua Capitol — chỉ xem được ở trang chạy trên máy của đội (ô tổng ở trên vẫn là số qua Capitol).</section>;
  if (load.error) return <section className="notice error">Không tải được danh sách khách 1 % gia vị ({load.error.message}).</section>;
  if (!load.data) return <p className="hint">Đang đọc danh sách khách…</p>;
  if (isRefused(load.data)) return <section className="notice refused">Danh sách khách: {load.data.refused}</section>;
  const data = load.data;
  const employees = [...new Set(data.rows.map((row) => row.employee_id))].sort();
  const days = [...new Set(data.rows.flatMap((row) => row.visit_days))].sort();
  const needle = text.trim().toLowerCase();
  const rows = data.rows.filter((row) => (employee === "" || row.employee_id === employee) && (day === "" || row.visit_days.includes(day)) &&
    (group === "" || row.group === group) && (needle === "" || [row.customer_code, row.customer_name ?? ""].some((value) => value.toLowerCase().includes(needle))));
  const key = `${employee}|${day}|${group}|${needle}`;          // đổi bộ lọc ⇒ về 60 thẻ đầu
  const shown = state.key === key ? state.shown : PAGE;
  return (
    <section className="spice-customers" aria-label="Khách của chương trình 1 % gia vị">
      <h3>Từng khách</h3>
      {data.notes.map((note) => <p className="hint" key={note}>{note}</p>)}
      <div className="outlet-filters">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder="Tìm tên, mã khách" aria-label="Tìm khách gia vị" />
        <select value={employee} onChange={(event) => onEmployee(event.target.value)} aria-label="Lọc khách theo nhân viên">
          <option value="">Mọi nhân viên</option>
          {employees.map((id) => <option key={id} value={id}>{employeeLabel(id, mode, names)}</option>)}
        </select>
        <select value={day} onChange={(event) => setDay(event.target.value)} aria-label="Lọc khách theo ngày viếng thăm">
          <option value="">Mọi ngày viếng thăm</option>
          {days.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
        <select value={group} onChange={(event) => setGroup(event.target.value)} aria-label="Lọc khách theo nhóm">
          <option value="">Mọi nhóm</option>
          {GROUP_ORDER.map((item) => <option key={item} value={item}>{data.groups[item] ?? GROUP_SHORT[item]}</option>)}
        </select>
      </div>
      <p className="hint count">{exact(rows.length)} khách</p>
      <div className="outlet-cards">{rows.slice(0, shown).map((row) => <CustomerCard key={row.customer_code} row={row} />)}</div>
      {rows.length > shown && <button type="button" className="ghost outlet-more" onClick={() => setState({ key, shown: shown + PAGE })}>Xem thêm ({rows.length - shown} khách nữa)</button>}
    </section>
  );
}

function CustomerCard({ row }: { row: SpiceCustomer }) {
  const paid = row.group === "co_thuong";
  return (
    <article className="outlet-card">
      <h3>{row.customer_name ?? `Khách ${row.customer_code}`}</h3>
      <div className="line"><span className={`chip ${paid ? "on-track" : row.group === "duoi_70" ? "off-track" : ""}`}>{GROUP_SHORT[row.group]}</span> · mã {row.customer_code}</div>
      <div className="line"><Nv id={row.employee_id} /> · {row.visit_days.length ? row.visit_days.join(", ") : "chưa có ngày viếng thăm"}</div>
      <div className="big">{row.pct_of_baseline === null ? "—" : pct(row.pct_of_baseline)}</div>
      <span className={`meter ${paid ? "on-track" : "off-track"}`} aria-hidden="true"><span className="fill" style={{ width: `${Math.min(row.pct_of_baseline ?? 0, 100)}%` }} /></span>
      <div className="line">Doanh số gia vị <b>{dong(row.sales)}</b> / nền <b>{dong(row.baseline)}</b></div>
      <div className="line">
        {paid ? <>Thưởng 1 % tạm tính <b>{dong(row.reward)}</b> (trả khách)</>
          : row.need_more === null ? "Khách chưa có doanh số nền — không xét thưởng"
            : <>Cần thêm <b>{dong(row.need_more)}</b> để đạt 110 % nền ⇒ thưởng ≈ <b>{dong(row.reward_at_threshold)}</b></>}
      </div>
    </article>
  );
}
