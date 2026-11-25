import { Nv } from "./Names";
import { Fragment, type KeyboardEvent } from "react";
import { type KpiCell, type TeamBonus } from "./api";
import { EmployeeCard } from "./EmployeeCard";
import { millions, num, pct, vnd } from "./format";
import { MarkBar, STATUS } from "./MarkBar";

// Bảng đội (thiết kế mục 3): mỗi ô KPI là một thanh mốc; bấm / Enter vào một hàng ⇒ thẻ nhân viên mở NGAY DƯỚI hàng đó.
// Mọi số do backend trả — trang chỉ vẽ và định dạng.

function cellNote(kpi: string, cell: KpiCell) {
  // chú thích khi rê chuột / focus: đủ số (đồng) để giám sát không phải mở thẻ
  const lines = [`${kpi}: ${STATUS[cell.status].label}`, `Chỉ tiêu ${num(cell.target)}, thực hiện ${num(cell.actual)}${cell.measure === "pct" ? ` (${pct(cell.pct)})` : ""}`,
    `Tiền: ${vnd(cell.amount)}`];
  if (cell.next_tier && cell.gap_to_next !== null) {
    // Câu do backend viết (gap_per_day_text — theo TỔNG + trung bình); không có ⇒ chỉ số tổng. "ít nhất" chỉ khi gap_rounded_up:
    // từ bậc "ít nhất N" bản 3 (03/11 chiều) cờ này nghĩa là SỐ TỔNG đã làm tròn lên (trước đó nói về số mỗi ngày — trang hiện sai "ít nhất 114")
    lines.push(cell.gap_per_day_text
      ? `Để lên “${cell.next_tier}”: ${cell.gap_per_day_text}`
      : `Còn thiếu ${cell.gap_rounded_up ? "ít nhất " : ""}${num(cell.gap_to_next)} để lên “${cell.next_tier}”`);
  }
  return lines.join("\n");
}

export function TeamTable({ team, kpis, selected, onSelect }: {
  team: TeamBonus;
  kpis: string[];
  selected: string | null;
  onSelect: (employeeId: string | null) => void;
}) {
  const toggle = (id: string) => onSelect(id === selected ? null : id);
  const onKey = (event: KeyboardEvent, id: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle(id);
    }
  };
  return (
    <section className="team-section" aria-labelledby="team-title">
      <h2 id="team-title">Bảng đội <span className="hint">bấm một người để xem từng KPI</span></h2>
      <div className="table-wrap">
        <table className="team">
          <thead>
            <tr>
              <th scope="col" className="left">Nhân viên</th>
              {kpis.map((kpi) => <th scope="col" key={kpi}>{kpi}</th>)}
              <th scope="col" className="num">Tổng</th>
            </tr>
          </thead>
          <tbody>
            {team.employees.map((row) => {
              const open = row.employee_id === selected;
              return (
                <Fragment key={row.employee_id}>
                  <tr className={open ? "row open" : "row"} tabIndex={0} aria-expanded={open}
                      onClick={() => toggle(row.employee_id)} onKeyDown={(e) => onKey(e, row.employee_id)}>
                    <th scope="row" className="left code"><Nv id={row.employee_id} /></th>
                    {kpis.map((kpi) => {
                      const cell = row.cells?.[kpi];
                      return (
                        <td key={kpi} title={cell ? cellNote(kpi, cell) : vnd(row.kpi_amounts?.[kpi])}>
                          {cell ? <MarkBar cell={cell} /> : <span className="num">{millions(row.kpi_amounts?.[kpi])}</span>}
                        </td>
                      );
                    })}
                    <td className="num total" title={vnd(row.business_salary)}>{millions(row.business_salary)}</td>
                  </tr>
                  {open && (
                    <tr className="card-row">
                      <td colSpan={kpis.length + 2}><EmployeeCard employeeId={row.employee_id} cells={row.cells} /></td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row" className="left" colSpan={kpis.length + 1}>Cả đội, {team.employee_count} người</th>
              <td className="num total" title={vnd(team.team_total)}>{millions(team.team_total)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="legend">
        {(["du_moc", "moc_thap", "chua_tien"] as const).map((key) => (
          <span key={key} className={`legend-item ${key}`}><span aria-hidden="true">{STATUS[key].icon}</span> {STATUS[key].label}</span>
        ))}
        <span className="legend-note">Vạch mảnh là mốc thấp (vd 95 %); hết thanh là mốc cao nhất. Rê chuột vào ô để xem chỉ tiêu, còn thiếu, tiền.</span>
      </p>
    </section>
  );
}
