import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { type Explain, type Refused, type TeamBonus, getEmployee, getEmployeeGap, getExplain, isRefused } from "./api";
import { ExplainBox } from "./EmployeeCard";
import { num, pct, vnd } from "./format";
import { MarkBar, STATUS } from "./MarkBar";
import { NamesContext, Nv, employeeLabel } from "./Names";
import { OrderList } from "./OrderList";
import { useLoad } from "./useLoad";

// Màn "Từng nhân viên" (bậc 2 giao diện bản 3): chọn một người ⇒ khối tổng tiền của người đó + mỗi KPI một thẻ (tiền đủ đồng, thực hiện /
// chỉ tiêu, thanh mốc, còn thiếu để lên mốc kế + tiền khi đó, nút Giải thích). Thay cho thẻ mở dưới hàng của bảng đội.
// TRANG KHÔNG TÍNH TIỀN: số từng KPI từ /api/employees/<mã>, "còn thiếu / tiền nếu lên mốc kế" từ /api/gap, thanh mốc từ ô của /api/team-board.
// Mã gửi backend luôn là mã che NVxx; tên chỉ là chữ hiện trên trang.
const STATUS_PILL = { du_moc: "Đủ mốc", moc_thap: "Có tiền, còn mốc kế", chua_tien: "Chưa có tiền" } as const;

export function EmployeeView({ team, bonus, employeeId, onPick, onBack }: {
  team: TeamBonus; bonus: TeamBonus | undefined; employeeId: string; onPick: (employeeId: string) => void; onBack: () => void;
}) {
  const { mode, names } = useContext(NamesContext);
  const loadEmployee = useCallback(() => getEmployee(employeeId), [employeeId]);
  const loadGap = useCallback(() => getEmployeeGap(employeeId), [employeeId]);
  const inTeam = team.employees.some((row) => row.employee_id === employeeId);
  const employee = useLoad(loadEmployee, undefined, inTeam);
  const gap = useLoad(loadGap, undefined, inTeam);
  const [explained, setExplained] = useState<{ kpi: string; data?: Explain | Refused; error?: string } | null>(null);
  const explain = (kpi: string) => {
    if (explained?.kpi === kpi) return setExplained(null);
    setExplained({ kpi });
    getExplain(employeeId, kpi).then((data) => setExplained({ kpi, data }), (error: Error) => setExplained({ kpi, error: error.message }));
  };
  // "Xem đơn được tính": danh sách đơn của đúng người + đúng chỉ tiêu, mở ngay dưới các thẻ. Đường dẫn ?man=nhan-vien&nv=NV05&kpi=PC_BT mở thẳng.
  const [ordersKpi, setOrdersKpi] = useState<string | null>(() => {
    const query = new URLSearchParams(window.location.search);
    return query.get("man") === "nhan-vien" && query.get("nv") === employeeId ? query.get("kpi") : null;
  });
  const ordersBox = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ordersKpi) ordersBox.current?.scrollIntoView({ block: "nearest" }); }, [ordersKpi]);
  const known = team.employees.some((row) => row.employee_id === employeeId);
  const cells = team.employees.find((row) => row.employee_id === employeeId)?.cells;
  const perPersonMax = bonus?.business_salary_at_100_pct;
  const paid = bonus?.employees.find((row) => row.employee_id === employeeId)?.kpis_paid;
  const picker = (
    <div className="emp-head">
      <button type="button" className="back" onClick={onBack}>← Cả đội</button>
      <label>Nhân viên{" "}
        <select value={known ? employeeId : ""} onChange={(event) => { setExplained(null); setOrdersKpi(null); onPick(event.target.value); }}>
          {!known && <option value="" disabled>— chọn nhân viên —</option>}
          {team.employees.map((row) => <option key={row.employee_id} value={row.employee_id}>{employeeLabel(row.employee_id, mode, names)}</option>)}
        </select>
      </label>
    </div>
  );
  // mã không có trong đội (vd ?nv= gõ sai trên đường dẫn) ⇒ nói rõ, KHÔNG lặng lẽ hiện người khác; không gọi backend cho mã đó
  if (!known) return <section className="emp-view">{picker}<div className="notice refused" role="alert">Không có nhân viên “{employeeId}” trong đội — chọn một người ở ô bên trên.</div></section>;
  if (employee.error) return <section className="emp-view">{picker}<div className="notice error">Không tải được nhân viên này: {employee.error.message}.</div></section>;
  if (!employee.data) return <section className="emp-view">{picker}<p className="hint">Đang tải…</p></section>;
  if (isRefused(employee.data)) return <section className="emp-view">{picker}<div className="notice refused">Backend từ chối mã này: {employee.data.refused}</div></section>;
  const data = employee.data;
  const gaps = gap.data && !isRefused(gap.data) ? gap.data.employees[0]?.gaps ?? [] : [];
  const daysLeft = gap.data && !isRefused(gap.data) ? gap.data.working_days_left_after_cutoff : null;
  const share = perPersonMax ? Math.min(data.business_salary / perPersonMax, 1) * 100 : undefined;   // chỉ để vẽ độ dài thanh
  return (
    <section className="emp-view" aria-label="Từng nhân viên">
      {picker}
      <div className="emp-hero">
        <div className="emp-id"><Nv id={data.employee_id} /></div>
        <div>
          <div className="emp-money">{vnd(data.business_salary)}
            <small>{perPersonMax !== undefined ? ` trên tối đa ${vnd(perPersonMax)}` : ""}{paid !== undefined ? `, ${paid}/${data.kpis.length} KPI có tiền` : ""}</small>
          </div>
          {share !== undefined && <div className="emp-track" aria-hidden="true"><div className="lane-fill grow" style={{ width: `${share}%` }} /></div>}
        </div>
        <div className="emp-note">
          Thưởng vượt (nhà phân phối chi) {vnd(data.over_target_bonus)}: {data.over_target_reason}.
          {daysLeft !== null && ` Còn ${daysLeft} ngày làm việc sau ngày chốt số.`}
        </div>
      </div>
      <div className="emp-cards">
        {data.kpis.map((line) => {
          const cell = cells?.[line.kpi];
          const next = gaps.find((item) => item.kpi === line.kpi);
          const open = explained?.kpi === line.kpi;
          return (
            <article className="emp-card" key={line.kpi}>
              <div className="card-top">
                <h3>{line.kpi}</h3>
                {cell && <span className={`pill ${cell.status}`}><span aria-hidden="true">{STATUS[cell.status].icon}</span> {STATUS_PILL[cell.status]}</span>}
              </div>
              <div className="big">{vnd(line.amount)}</div>
              <div className="line">Thực hiện <b>{num(line.actual)}</b> / chỉ tiêu {num(line.target)}{cell?.measure === "pct" ? <>, tức <b>{pct(cell.pct)}</b></> : ""}</div>
              {cell && <MarkBar cell={cell} large />}
              <div className="gapline">
                {next ? (
                  // câu "còn thiếu" do backend viết (gap_per_day_text); trang không tự dựng câu từ số mỗi ngày
                  <>{next.gap_per_day_text ?? <>Còn thiếu {next.gap_rounded_up ? "ít nhất " : ""}<b>{num(next.gap_to_next)}</b></>} để lên <b>“{next.next_tier}”</b> ⇒ tiền KPI này là{" "}
                    <span className={line.amount === 0 ? "plus" : ""}>{vnd(next.amount_if_reached)}</span></>
                ) : gap.error ? `Không tải được phần còn thiếu (${gap.error.message})`
                  : !gap.data ? "Đang tải…"
                  // backend TỪ CHỐI phần "còn thiếu", hoặc bảng đội nói còn mốc kế mà phần này không có dòng ⇒ nói rõ là chưa lấy được,
                  // KHÔNG khẳng định "đã ở mốc cao nhất" (góp ý kiểm 12/11: thẻ 0 đ từng ghi sai như vậy)
                  : isRefused(gap.data) ? `Chưa lấy được mốc kế (backend từ chối: ${gap.data.refused})`
                  : cell && cell.next_tier !== null ? "Chưa lấy được mốc kế"
                  : <>Đã ở mốc cao nhất{line.reached_tier ? <> — <b>{line.reached_tier}</b></> : ""}</>}
              </div>
              <div className="card-actions">
                <button type="button" className="ghost" aria-expanded={open} onClick={() => explain(line.kpi)}>{open ? "Ẩn giải thích" : "Giải thích"}</button>
                <button type="button" className="ghost" aria-expanded={ordersKpi === line.kpi} onClick={() => setOrdersKpi(ordersKpi === line.kpi ? null : line.kpi)}>
                  {ordersKpi === line.kpi ? "Ẩn đơn được tính" : "Xem đơn được tính"}
                </button>
              </div>
              {open && <ExplainBox employeeId={employeeId} {...explained!} />}
            </article>
          );
        })}
      </div>
      <div ref={ordersBox}>
        {/* tên chỉ tiêu trên đường dẫn so không phân biệt hoa thường với các thẻ đang có; tên lạ ⇒ không mở gì */}
        {ordersKpi && data.kpis.some((line) => line.kpi.toLowerCase() === ordersKpi.toLowerCase()) && (
          <OrderList employeeId={employeeId} kpi={data.kpis.find((line) => line.kpi.toLowerCase() === ordersKpi.toLowerCase())!.kpi} onClose={() => setOrdersKpi(null)} />
        )}
      </div>
      <p className="foot">Số từng KPI do backend trả; trang không tự tính. "Tiền KPI này là …" = toàn bộ tiền của KPI khi lên mốc kế (nền vàng: hiện đang 0 đ).</p>
    </section>
  );
}
