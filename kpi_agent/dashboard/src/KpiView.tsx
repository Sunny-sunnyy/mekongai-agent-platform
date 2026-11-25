import { type ReactNode, useCallback } from "react";
import { type Gap, type GapResult, type Refused, type TeamBonus, getExplain, getTeamKpi, isRefused } from "./api";
import { num, pct, vnd } from "./format";
import { MarkBar, STATUS } from "./MarkBar";
import { Nv } from "./Names";
import { useLoad } from "./useLoad";

// Màn "Theo chỉ tiêu" (User hỏi "tab theo dõi mỗi chỉ tiêu"): chọn MỘT KPI ⇒ cả đội xếp theo mức đạt, thanh tới mốc, ai có / chưa có tiền,
// còn thiếu bao nhiêu để lên mốc kế và khi đó tiền KPI là bao nhiêu, tổng tiền KPI của đội, cách đếm + mục công văn.
// TRANG KHÔNG TÍNH TIỀN: ô từng người từ /api/team-board; tổng + số người theo mốc từ /api/team-kpi; "nếu lên mốc kế" từ /api/gap; cách đếm từ /api/explain.
// Trang chỉ SẮP thứ tự theo số backend trả. Tiền ở màn này hiện đủ tới đồng.
// `nav` = thanh chọn chỉ tiêu + khoản thưởng thêm do màn bọc ngoài dựng (TargetsView.tsx) — dùng chung với màn một khoản thưởng thêm
export function KpiView({ team, gap, kpi, nav }: {
  team: TeamBonus; gap: GapResult | Refused | undefined; kpi: string; nav: ReactNode;
}) {
  const loadTotal = useCallback(() => getTeamKpi(kpi), [kpi]);
  const total = useLoad(loadTotal);
  // cách đếm + mục công văn là của KPI (giống nhau mọi người) ⇒ hỏi backend qua người đầu bảng; bảng mốc có thể khác theo vùng nên KHÔNG lấy từ đây
  const first = team.employees[0]?.employee_id;
  const loadRule = useCallback(() => (first ? getExplain(first, kpi) : Promise.resolve(undefined)), [first, kpi]);
  const rule = useLoad(loadRule);
  const gaps = new Map<string, Gap>();
  if (gap && !isRefused(gap)) for (const row of gap.employees) for (const item of row.gaps) if (item.kpi === kpi) gaps.set(row.employee_id, item);
  // xếp theo mức đạt backend trả: KPI theo % ⇒ %; KPI theo giá trị (LPPC…) ⇒ giá trị. Người chưa có số xuống cuối
  const measure = (id: string) => {
    const cell = team.employees.find((row) => row.employee_id === id)?.cells?.[kpi];
    return cell ? (cell.measure === "value" ? cell.value : cell.pct) ?? -1 : -1;
  };
  const rows = [...team.employees].sort((a, b) => measure(b.employee_id) - measure(a.employee_id));
  const totals = total.data && !isRefused(total.data) ? total.data : undefined;
  const ruleData = rule.data && !isRefused(rule.data) ? rule.data : undefined;
  return (
    <section className="kpi-view" aria-label="Theo chỉ tiêu">
      {nav}
      <div className="kpi-head">
        <div>
          <p className="hero-kicker">Chỉ tiêu {kpi}{totals ? ` · công văn mục ${totals.cong_van_section}` : ""}</p>
          <p className="kpi-total">{totals ? vnd(totals.team_amount_total) : "—"}</p>
          <p className="hero-sub">{totals ? <>đội đang nhận ở chỉ tiêu này · <b>{totals.paid_count} / {totals.employee_count}</b> người có tiền</> : total.error ? `Không tải được tổng (${total.error.message})` : "Đang tải tổng…"}</p>
        </div>
        {totals && (
          <div className="stats">
            {totals.by_tier.map((tier) => (
              <div className="stat" key={tier.reached_tier}>
                <div className="v">{tier.employee_count}<span className="u"> người</span></div>
                <div className="l">{tier.reached_tier} — {vnd(tier.amount_total)}</div>
              </div>
            ))}
          </div>
        )}
      </div>
      {total.data && isRefused(total.data) && <section className="notice refused">Backend từ chối: {total.data.refused}</section>}
      {ruleData && <p className="kpi-rule"><b>Cách đếm:</b> {ruleData.how_counted}</p>}
      <div className="board">
        <div className="scroll">
          <table className="compact-table kpi-table">
            <thead>
              <tr>
                <th scope="col" className="sticky left">Nhân viên</th>
                <th scope="col" className="left">Tới mốc</th>
                <th scope="col">Thực hiện<span className="sub">chỉ tiêu</span></th>
                <th scope="col">Mức đạt</th>
                <th scope="col" className="left">Mốc đã đạt</th>
                <th scope="col" className="left">Còn thiếu để lên mốc kế</th>
                <th scope="col">Tiền nếu lên mốc kế</th>
                <th scope="col">Tiền đang có</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const cell = row.cells?.[kpi];
                if (!cell) return <tr key={row.employee_id}><th scope="row" className="sticky left"><Nv id={row.employee_id} /></th><td colSpan={7} className="left">chưa có số</td></tr>;
                const next = gaps.get(row.employee_id);
                const status = STATUS[cell.status];
                return (
                  <tr key={row.employee_id}>
                    <th scope="row" className="sticky left"><Nv id={row.employee_id} /></th>
                    <td className="left bar"><MarkBar cell={cell} large /></td>
                    <td><span className="m">{num(cell.actual)}</span><span className="s">{num(cell.target)}</span></td>
                    <td>{cell.measure === "value" ? num(cell.value) : pct(cell.pct)}</td>
                    <td className="left">{cell.reached_tier ?? "chưa đạt mốc nào"}</td>
                    <td className="left wrap">{cell.next_tier === null ? "đã ở mốc cao nhất" : cell.gap_per_day_text ?? `${cell.gap_rounded_up ? "ít nhất " : ""}${num(cell.gap_to_next)} để lên “${cell.next_tier}”`}</td>
                    <td>{next ? <span className={cell.amount === 0 ? "up-chip" : ""}>{vnd(next.amount_if_reached)}</span> : "—"}</td>
                    <td className={cell.status}><span className="m"><span aria-hidden="true">{status.icon}</span><span className="sr-only"> {status.label}</span> {vnd(cell.amount)}</span></td>
                  </tr>
                );
              })}
            </tbody>
            {totals && (
              <tfoot>
                <tr className="total-row"><th scope="row" className="sticky left">Cả đội</th><td colSpan={6} className="left">{totals.paid_count} / {totals.employee_count} người có tiền</td><td className="sum"><span className="m">{vnd(totals.team_amount_total)}</span></td></tr>
              </tfoot>
            )}
          </table>
        </div>
      </div>
      <p className="foot">
        "Tiền nếu lên mốc kế" là TOÀN BỘ tiền của chỉ tiêu này khi lên mốc kế (không phải phần cộng thêm), do backend tính; nền vàng = hiện đang 0 đ.
        Các khoản ngoài 8 KPI (Thưởng thêm, Meat): chọn ở hàng "Thưởng thêm" phía trên.
      </p>
    </section>
  );
}
