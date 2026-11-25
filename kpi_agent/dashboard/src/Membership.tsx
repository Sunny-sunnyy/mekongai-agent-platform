import { useContext, useState } from "react";
import { type MemberRow, getMembership, isRefused } from "./api";
import { dong, exact, pct } from "./format";
import { NamesContext, Nv, employeeLabel } from "./Names";
import { READ_ONLY } from "./base";
import { useLoad } from "./useLoad";

// Mục "Hội viên" của tab Theo dõi — PHẦN DOANH SỐ (trang theo dõi của đội có tab này: Tổng quan + Chi tiết hội viên).
// Dựng được: khách hội viên, nền, doanh số sau chiết khấu, % nền, còn lại, nhóm theo doanh số (backend /api/membership — số trùng trang của đội).
// CHƯA dựng (thiếu dữ liệu): hạng khách, nhiệm vụ Duy trì / Mở mới, tiền thưởng ⇒ băng cảnh báo luôn hiện; "đủ doanh số" KHÔNG phải "đã đạt chương trình".
// Mọi số ở ô tổng + bảng theo người do backend trả; trang chỉ LỌC và ĐẾM số thẻ đang hiện. Tên + mã khách chỉ vẽ thành chữ, không gửi trợ lý.
const PAGE = 60;                                  // 60 thẻ mỗi lần như trang của đội
const GROUP_ORDER = ["du_doanh_so", "gan_dat", "dang_ban", "chua_phat_sinh"] as const;
const GROUP_SHORT: Record<string, string> = { du_doanh_so: "Đủ doanh số ≥ nền", gan_dat: "Gần đạt ≥ 60 %", dang_ban: "Đang bán < 60 %", chua_phat_sinh: "Chưa phát sinh" };

export function MembershipView() {
  const load = useLoad(getMembership);
  const { mode, names } = useContext(NamesContext);
  const [screen, setScreen] = useState<"tong-quan" | "chi-tiet">("tong-quan");
  const [text, setText] = useState("");
  const [employee, setEmployee] = useState("");
  const [day, setDay] = useState("");
  const [group, setGroup] = useState("");
  const [shown, setShown] = useState(PAGE);
  // Xem qua Capitol: đường /api/membership (có tên + mã khách) không mở ở cổng xem ⇒ nói rõ, không báo lỗi tải
  if (load.error && READ_ONLY) return <section className="notice refused not-in-view">Hội viên: chưa có ở bản xem qua Capitol — danh sách khách chỉ xem được ở trang chạy trên máy của đội.</section>;
  if (load.error) return <section className="notice error">Không tải được danh sách hội viên ({load.error.message}).</section>;
  if (!load.data) return <p className="hint">Đang đọc danh sách hội viên (lần đầu ≈ 15 giây)…</p>;
  if (isRefused(load.data)) return <section className="notice refused">Hội viên: chưa dựng được ở bản này — {load.data.refused}</section>;
  const data = load.data;
  const reset = <T,>(set: (value: T) => void) => (value: T) => { set(value); setShown(PAGE); };
  const days = [...new Set(data.rows.flatMap((row) => row.visit_days))].sort();
  const needle = text.trim().toLowerCase();
  const rows = data.rows.filter((row) => (employee === "" || row.employee_id === employee) && (day === "" || row.visit_days.includes(day)) &&
    (group === "" || row.group === group) && (needle === "" || [row.customer_code, row.customer_name ?? ""].some((value) => value.toLowerCase().includes(needle))));
  const openEmployee = (id: string) => { setEmployee(id); setGroup(""); setShown(PAGE); setScreen("chi-tiet"); };
  return (
    <section className="membership" aria-label="Hội viên">
      <div className="page-diff"><b>Mới dựng PHẦN DOANH SỐ.</b> {data.not_built}</div>
      <nav className="tracking-tabs" aria-label="Chọn màn hội viên">
        <button type="button" className={screen === "tong-quan" ? "on" : ""} onClick={() => setScreen("tong-quan")}>Tổng quan</button>
        <button type="button" className={screen === "chi-tiet" ? "on" : ""} onClick={() => setScreen("chi-tiet")}>Chi tiết hội viên</button>
      </nav>
      {screen === "tong-quan" ? (
        <>
          <div className="cards">
            <div><span>Khách hội viên</span><b>{exact(data.summary.customers)}</b></div>
            {GROUP_ORDER.map((key) => <div key={key}><span>{GROUP_SHORT[key]}{key === "du_doanh_so" ? " (chưa xét nhiệm vụ)" : ""}</span><b>{exact(data.summary[key])}</b></div>)}
            <div><span>DS / nền tảng</span><b>{dong(data.summary.sales)} / {dong(data.summary.baseline)}</b><small>{pct(data.summary.pct_of_baseline)}</small></div>
          </div>
          <div className="table-scroll">
          <table className="track">
            <thead><tr><th>Nhân viên phụ trách</th><th>KH</th><th>Đủ doanh số ≥ nền</th><th>Gần đạt</th><th>Đang bán</th><th>Chưa phát sinh</th><th>DS thực hiện / nền tảng</th><th>% nền</th></tr></thead>
            <tbody>
              {data.by_employee.map((row) => (
                <tr key={row.employee_id}>
                  <td><button type="button" className="link" onClick={() => openEmployee(row.employee_id)} title="Xem khách hội viên của nhân viên này"><Nv id={row.employee_id} /></button></td>
                  <td className="r">{row.customers}</td><td className="r">{row.du_doanh_so}</td><td className="r">{row.gan_dat}</td><td className="r">{row.dang_ban}</td>
                  <td className="r">{row.chua_phat_sinh}</td><td className="r">{dong(row.sales)} / {dong(row.baseline)}</td><td className="r">{pct(row.pct_of_baseline)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </>
      ) : (
        <>
          <div className="outlet-filters">
            <input type="search" value={text} onChange={(event) => reset(setText)(event.target.value)} placeholder="Tìm tên, mã khách" aria-label="Tìm khách hội viên" />
            <select value={employee} onChange={(event) => reset(setEmployee)(event.target.value)} aria-label="Lọc theo nhân viên">
              <option value="">Mọi nhân viên</option>
              {data.by_employee.map((row) => <option key={row.employee_id} value={row.employee_id}>{employeeLabel(row.employee_id, mode, names)}</option>)}
            </select>
            <select value={day} onChange={(event) => reset(setDay)(event.target.value)} aria-label="Lọc theo ngày viếng thăm">
              <option value="">Mọi ngày viếng thăm</option>
              {days.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
            <select value={group} onChange={(event) => reset(setGroup)(event.target.value)} aria-label="Lọc theo nhóm doanh số">
              <option value="">Mọi nhóm</option>
              {GROUP_ORDER.map((key) => <option key={key} value={key}>{data.groups[key] ?? GROUP_SHORT[key]}</option>)}
            </select>
          </div>
          <p className="hint">{exact(rows.length)} khách hội viên</p>
          <div className="outlet-cards">{rows.slice(0, shown).map((row) => <MemberCard key={row.customer_code} row={row} />)}</div>
          {rows.length > shown && <button type="button" className="ghost outlet-more" onClick={() => setShown(shown + PAGE)}>Xem thêm ({rows.length - shown} khách nữa)</button>}
        </>
      )}
      {data.notes.map((note) => <p className="hint" key={note}>{note}</p>)}
    </section>
  );
}

function MemberCard({ row }: { row: MemberRow }) {
  const reached = row.group === "du_doanh_so";
  return (
    <article className="outlet-card">
      <h3>{row.customer_name ?? `Khách ${row.customer_code}`}</h3>
      <div className="line"><span className={`chip ${reached ? "on-track" : row.group === "chua_phat_sinh" ? "off-track" : ""}`}>{GROUP_SHORT[row.group]}</span> · mã {row.customer_code}</div>
      <div className="line"><Nv id={row.employee_id} /> · {row.visit_days.length ? row.visit_days.join(", ") : "chưa có ngày viếng thăm"}</div>
      <div className="big">{row.pct_of_baseline === null ? "—" : pct(row.pct_of_baseline)}</div>
      <span className={`meter ${reached ? "on-track" : "off-track"}`} aria-hidden="true"><span className="fill" style={{ width: `${Math.min(row.pct_of_baseline ?? 0, 100)}%` }} /></span>
      <div className="line">Doanh số <b>{dong(row.sales)}</b> / nền <b>{dong(row.baseline)}</b></div>
      <div className="line">{reached ? "Đã đủ doanh số nền — chưa xét nhiệm vụ" : <>Còn lại <b>{dong(row.remaining)}</b> để đủ nền</>}</div>
    </article>
  );
}
