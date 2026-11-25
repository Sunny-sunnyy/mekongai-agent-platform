import { Fragment, useCallback, useState, type KeyboardEvent } from "react";
import { type Gap, type GapResult, type KpiCell, type Meta, type Refused, type TeamBonus, type TeamKpi, getTeamKpi, isRefused } from "./api";
import { EmployeeCard } from "./EmployeeCard";
import { millions, monthLabel, num, pct, vnd } from "./format";
import { STATUS } from "./MarkBar";
import { Nv } from "./Names";
import { useLoad } from "./useLoad";

// Màn "Cả đội" kiểu "Đường đua thưởng" (kiểu A + nút bảng gọn).
// TRANG KHÔNG TÍNH TIỀN: mọi số tiền, %, số người do backend trả (/api/team-board, /api/team-kpi, /api/gap). Trang chỉ (1) định dạng, (2) SẮP thứ tự
// theo số backend trả, (3) đổi tỉ lệ ra độ dài thanh để vẽ. Tiền đủ tới đồng ở khối đầu trang + cột Tổng; ô bảng viết gọn "5,6 tr", rê chuột thấy đủ đồng.

export function TeamOverview({ team, bonus, meta, gap, selected, onSelect, onOpenKpi }: {
  team: TeamBonus;
  // /api/team-bonus: mức tối đa mỗi người + số KPI có tiền của từng người (bảng /api/team-board không có hai số này); thiếu ⇒ ẩn phần dùng nó
  bonus: TeamBonus | undefined;
  meta: Meta;
  gap: GapResult | Refused | undefined;
  selected: string | null;
  onSelect: (employeeId: string | null) => void;
  onOpenKpi: (kpi: string) => void;
}) {
  const kpis = meta.kpis;
  // tiền từng KPI của cả đội: một lần gọi /api/team-kpi cho mỗi KPI (backend cộng — trang không cộng ô)
  const loadKpis = useCallback(() => Promise.all(kpis.map((kpi) => getTeamKpi(kpi))), [kpis]);
  const byKpi = useLoad(loadKpis);
  const kpiTotals = new Map<string, TeamKpi>();
  for (const item of byKpi.data ?? []) if (!isRefused(item)) kpiTotals.set(item.kpi, item);
  const [compact, setCompact] = useState(false);
  return (
    <>
      <Hero team={team} perPersonMax={bonus?.business_salary_at_100_pct} meta={meta} kpis={kpis} kpiTotals={kpiTotals} />
      <KpiStrip team={team} kpis={kpis} kpiTotals={kpiTotals} onOpenKpi={onOpenKpi} />
      {byKpi.error && <p className="hint">Không tải được tiền từng KPI ({byKpi.error.message}) — thanh màu và dòng "đội nhận" tạm ẩn.</p>}
      <section className="board" aria-labelledby="board-title">
        <div className="board-head">
          <h2 id="board-title">{compact ? "Bảng gọn" : `Đường đua của ${team.employee_count} người`}</h2>
          <p>{compact ? "Ô ghi gọn theo triệu, rê chuột để xem đủ đồng. Bấm một dòng để mở nhân viên." : "Xếp theo tổng tiền, cao xuống thấp. Bấm một dòng để mở nhân viên."}</p>
          <div className="seg-switch" role="group" aria-label="Kiểu bảng">
            <button type="button" aria-pressed={!compact} onClick={() => setCompact(false)}>Đường đua</button>
            <button type="button" aria-pressed={compact} onClick={() => setCompact(true)}>Bảng gọn</button>
          </div>
        </div>
        {compact
          ? <CompactTable team={team} bonus={bonus} kpis={kpis} kpiTotals={kpiTotals} selected={selected} onSelect={onSelect} />
          : <RaceTable team={team} bonus={bonus} kpis={kpis} gap={gap} selected={selected} onSelect={onSelect} />}
      </section>
      <p className="foot">
        Số {meta.data_source === "raw" ? "tính từ file đơn hàng" : "chụp từ sổ theo dõi"} tới ngày chốt {meta.cutoff.split("-").reverse().join("/")} · bản luật {meta.rule_version ?? "—"} ·
        trang chỉ định dạng và sắp thứ tự, mọi số tiền do backend tính.
      </p>
    </>
  );
}

function Hero({ team, perPersonMax, meta, kpis, kpiTotals }: { team: TeamBonus; perPersonMax: number | undefined; meta: Meta; kpis: string[]; kpiTotals: Map<string, TeamKpi> }) {
  const max = team.team_max_total;
  // KPI ít người có tiền nhất: trang chỉ SẮP / CHỌN theo `paid_count` backend trả — không cộng trừ tiền. Hoà thì hiện đủ các KPI hoà.
  const counts = kpis.map((kpi) => ({ kpi, paid: team.kpi_summary?.[kpi]?.paid_count })).filter((item): item is { kpi: string; paid: number } => item.paid !== undefined);
  const fewest = counts.length ? Math.min(...counts.map((item) => item.paid)) : undefined;
  const fewestKpis = counts.filter((item) => item.paid === fewest).map((item) => item.kpi);
  const nobody = team.kpis_nobody_paid ?? [];
  return (
    <section className="hero" aria-label="Tình trạng cả đội">
      <div className="hero-grid">
        <div>
          <p className="hero-kicker">{team.team_label ? `Đội ${team.team_label}` : "Đội của bạn"}, {monthLabel(meta.period)}, đang giữ</p>
          <p className="hero-total">{vnd(team.team_total).replace(" đ", "")}<small>đ</small></p>
          {max !== undefined && (
            <p className="hero-sub">trên mức tối đa <b>{vnd(max)}</b> nếu cả {team.employee_count} người đủ {kpis.length} KPI.</p>
          )}
        </div>
        <div className="stats">
          {team.team_share_pct !== undefined && (
            <div className="stat"><div className="v">{pct(team.team_share_pct).replace(" %", "")}<span className="u"> %</span></div><div className="l">của mức tối đa đã về tay đội</div></div>
          )}
          <div className="stat">
            <div className="v">{team.employee_count}</div>
            <div className="l">nhân viên{perPersonMax !== undefined ? <>, tối đa <span title={vnd(perPersonMax)}>{millions(perPersonMax)}</span> mỗi người</> : ""}</div>
          </div>
          {fewest !== undefined && (
            <div className="stat">
              <div className="v">{fewest}<span className="u">/{team.employee_count}</span></div>
              <div className="l">người có tiền {fewestKpis.join(", ")} — KPI ít người có tiền nhất{nobody.length > 0 ? ` · chưa ai có tiền: ${nobody.join(", ")}` : ""}</div>
            </div>
          )}
        </div>
      </div>
      {max !== undefined && kpiTotals.size === kpis.length && (
        <div className="race">
          <div className="race-bar" role="img" aria-label={`Tiền đội đang giữ theo từng KPI, trên mức tối đa ${vnd(max)}`}>
            {kpis.map((kpi, index) => {
              const amount = kpiTotals.get(kpi)!.team_amount_total;
              // độ dài đoạn = tiền KPI (backend) so mức tối đa (backend) — chỉ để vẽ
              return <div key={kpi} className="seg grow" title={`${kpi}: ${vnd(amount)}`} style={{ width: `${(amount / max) * 100}%`, background: `var(--seg${index + 1})` }} />;
            })}
          </div>
          <div className="race-ends"><span>0 đ</span><span>{vnd(max)}</span></div>
          <div className="hero-legend">
            {kpis.map((kpi, index) => (
              <span key={kpi} title={vnd(kpiTotals.get(kpi)!.team_amount_total)}><i style={{ background: `var(--seg${index + 1})` }} />{kpi} <b>{millions(kpiTotals.get(kpi)!.team_amount_total)}</b></span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function KpiStrip({ team, kpis, kpiTotals, onOpenKpi }: { team: TeamBonus; kpis: string[]; kpiTotals: Map<string, TeamKpi>; onOpenKpi: (kpi: string) => void }) {
  return (
    <div className="kpi-strip">
      {kpis.map((kpi) => {
        const total = kpiTotals.get(kpi);
        const paid = total?.paid_count ?? team.kpi_summary?.[kpi]?.paid_count;
        return (
          <button type="button" className="kpi-tile" key={kpi} onClick={() => onOpenKpi(kpi)} title={`Mở màn Theo chỉ tiêu: ${kpi}`}>
            <h3>{kpi}</h3>
            <div className="cnt">{paid === undefined ? "—" : `${paid} / ${team.employee_count} người có tiền`}</div>
            {/* mỗi chấm một người, theo thứ tự bảng; chấm xanh = ô đó có tiền (trạng thái backend trả) */}
            <div className="dots" aria-hidden="true">
              {team.employees.map((row) => <i key={row.employee_id} className={row.cells?.[kpi] && row.cells[kpi].status !== "chua_tien" ? "on" : ""} />)}
            </div>
            <div className="amt" title={total ? vnd(total.team_amount_total) : undefined}>đội nhận <b>{total ? millions(total.team_amount_total) : "—"}</b></div>
          </button>
        );
      })}
    </div>
  );
}

function RaceTable({ team, bonus, kpis, gap, selected, onSelect }: {
  team: TeamBonus; bonus: TeamBonus | undefined; kpis: string[]; gap: GapResult | Refused | undefined; selected: string | null; onSelect: (employeeId: string | null) => void;
}) {
  const gaps = new Map<string, Gap>();
  if (gap && !isRefused(gap)) for (const row of gap.employees) for (const item of row.gaps) gaps.set(`${row.employee_id}/${item.kpi}`, item);
  const rows = [...team.employees].sort((a, b) => b.business_salary - a.business_salary);   // sắp theo tổng tiền backend trả
  const toggle = (id: string) => onSelect(id === selected ? null : id);
  const onKey = (event: KeyboardEvent, id: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle(id);
    }
  };
  const top = bonus?.business_salary_at_100_pct;
  const paidKpis = new Map((bonus?.employees ?? []).map((row) => [row.employee_id, row.kpis_paid]));
  return (
    <>
      <div className="legend-cells">
        <span>✓ đủ mốc cao nhất</span><span>◐ có tiền, còn mốc kế</span><span>○ chưa có tiền</span>
        <span><span className="up-chip">+tiền</span> sẽ có NẾU lên mốc kế (không phải tiền đã có)</span>
      </div>
      <div className="scroll">
        <table className="race-table">
          <thead>
            <tr>
              <th scope="col" className="sticky">Nhân viên</th>
              <th scope="col">Tổng tiền{top !== undefined && <span className="sub">so với tối đa {millions(top)} mỗi người</span>}</th>
              {kpis.map((kpi) => {
                const paid = team.kpi_summary?.[kpi]?.paid_count;
                return <th scope="col" key={kpi}>{kpi}{paid !== undefined && <span className="sub">{paid}/{team.employee_count} có tiền</span>}</th>;
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const open = row.employee_id === selected;
              const share = top ? Math.min(row.business_salary / top, 1) * 100 : undefined;   // chỉ để vẽ độ dài làn
              const paid = paidKpis.get(row.employee_id);
              return (
                <Fragment key={row.employee_id}>
                  <tr className={open ? "lane-row open" : "lane-row"} tabIndex={0} aria-expanded={open}
                      onClick={() => toggle(row.employee_id)} onKeyDown={(event) => onKey(event, row.employee_id)}>
                    <th scope="row" className="sticky">
                      <div className="who">
                        <span className="avatar" aria-hidden="true">{row.employee_id.replace(/\D/g, "")}</span>
                        <span><b><Nv id={row.employee_id} /></b>{paid !== undefined && <span className="sub">{paid}/{kpis.length} KPI có tiền</span>}</span>
                      </div>
                    </th>
                    <td>
                      <div className="lane">
                        <div className="lane-top"><b>{vnd(row.business_salary)}</b></div>
                        {share !== undefined && (
                          <div className="lane-track" aria-hidden="true">
                            <div className="lane-fill grow" style={{ width: `${share}%` }} />
                            <span className="coin" style={{ left: `${share}%` }} />
                          </div>
                        )}
                      </div>
                    </td>
                    {kpis.map((kpi) => {
                      const cell = row.cells?.[kpi];
                      return <td key={kpi}>{cell ? <RaceCell kpi={kpi} cell={cell} gap={gaps.get(`${row.employee_id}/${kpi}`)} /> : <span className="num">{millions(row.kpi_amounts?.[kpi])}</span>}</td>;
                    })}
                  </tr>
                  {open && (
                    <tr className="card-row"><td colSpan={kpis.length + 2}><EmployeeCard employeeId={row.employee_id} cells={row.cells} /></td></tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="total-row">
              <th scope="row" className="sticky">Cả đội</th>
              <td>{vnd(team.team_total)}</td>
              <td colSpan={kpis.length}>{team.employee_count} người</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  );
}

// "Bảng gọn" (kiểu sổ — bản xem thử kiểu B): mỗi ô tiền gọn + % / giá trị, cột nhân viên dính, dòng tổng đội theo từng KPI (số backend cộng ở /api/team-kpi).
function CompactTable({ team, bonus, kpis, kpiTotals, selected, onSelect }: {
  team: TeamBonus; bonus: TeamBonus | undefined; kpis: string[]; kpiTotals: Map<string, TeamKpi>; selected: string | null; onSelect: (employeeId: string | null) => void;
}) {
  const paidKpis = new Map((bonus?.employees ?? []).map((row) => [row.employee_id, row.kpis_paid]));
  const toggle = (id: string) => onSelect(id === selected ? null : id);
  const onKey = (event: KeyboardEvent, id: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle(id);
    }
  };
  return (
    <>
      <div className="legend-cells"><span>✓ đủ mốc cao nhất</span><span>◐ có tiền, còn mốc kế</span><span>○ chưa có tiền</span><span>Ô ghi gọn theo triệu; rê chuột để xem đủ đồng</span></div>
      <div className="scroll">
        <table className="compact-table">
          <thead>
            <tr>
              <th scope="col" className="sticky left">Nhân viên</th>
              {kpis.map((kpi) => {
                const paid = team.kpi_summary?.[kpi]?.paid_count;
                return <th scope="col" key={kpi}>{kpi}{paid !== undefined && <span className="sub">{paid}/{team.employee_count} có tiền</span>}</th>;
              })}
              <th scope="col">Tổng<span className="sub">KPI có tiền</span></th>
            </tr>
          </thead>
          <tbody>
            {team.employees.map((row) => {
              const open = row.employee_id === selected;
              const paid = paidKpis.get(row.employee_id);
              return (
                <Fragment key={row.employee_id}>
                  <tr className={open ? "lane-row open" : "lane-row"} tabIndex={0} aria-expanded={open}
                      onClick={() => toggle(row.employee_id)} onKeyDown={(event) => onKey(event, row.employee_id)}>
                    <th scope="row" className="sticky left"><Nv id={row.employee_id} /></th>
                    {kpis.map((kpi) => {
                      const cell = row.cells?.[kpi];
                      if (!cell) return <td key={kpi}>—</td>;
                      const status = STATUS[cell.status];
                      return (
                        <td key={kpi} className={cell.status} title={`${kpi}: ${status.label} — ${vnd(cell.amount)}\nChỉ tiêu ${num(cell.target)}, thực hiện ${num(cell.actual)}`}>
                          <span className="m"><span aria-hidden="true">{status.icon}</span><span className="sr-only"> {status.label}</span> {cell.amount > 0 ? millions(cell.amount) : "0 đ"}</span>
                          <span className="s">{cell.measure === "value" ? num(cell.value) : pct(cell.pct)}</span>
                        </td>
                      );
                    })}
                    <td className="sum"><span className="m">{vnd(row.business_salary)}</span><span className="s">{paid !== undefined ? `${paid}/${kpis.length}` : ""}</span></td>
                  </tr>
                  {open && (
                    <tr className="card-row"><td colSpan={kpis.length + 2}><EmployeeCard employeeId={row.employee_id} cells={row.cells} /></td></tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="total-row">
              <th scope="row" className="sticky left">Cả đội</th>
              {kpis.map((kpi) => {
                const total = kpiTotals.get(kpi);
                return <td key={kpi} title={total ? vnd(total.team_amount_total) : undefined}><span className="m">{total ? millions(total.team_amount_total) : "—"}</span></td>;
              })}
              <td className="sum"><span className="m">{vnd(team.team_total)}</span></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  );
}

function RaceCell({ kpi, cell, gap }: { kpi: string; cell: KpiCell; gap: Gap | undefined }) {
  const status = STATUS[cell.status];
  const measure = cell.measure === "value" ? num(cell.value) : pct(cell.pct);
  const note = [`${kpi}: ${status.label} — ${vnd(cell.amount)}`, `Chỉ tiêu ${num(cell.target)}, thực hiện ${num(cell.actual)}${cell.measure === "pct" ? ` (${pct(cell.pct)})` : ""}`];
  if (gap) note.push(`Nếu lên “${gap.next_tier}” (còn thiếu ${gap.gap_rounded_up ? "ít nhất " : ""}${num(gap.gap_to_next)}): tiền KPI này là ${vnd(gap.amount_if_reached)}`);
  return (
    <div className={`cell ${cell.status}`} title={note.join("\n")}>
      <div className="row">
        <span className="m"><span aria-hidden="true">{status.icon}</span><span className="sr-only"> {status.label}</span> {cell.amount > 0 ? millions(cell.amount) : "0 đ"}</span>
        {/* thẻ "+tiền": chỉ khi ô đang 0 đ ⇒ số trên thẻ chính là `amount_if_reached` backend trả (trang không trừ). Ô đã có tiền: số mốc kế nằm ở chú thích */}
        {gap && cell.amount === 0 && <span className="up-chip">+{millions(gap.amount_if_reached)}</span>}
      </div>
      <span className="s">{measure}</span>
    </div>
  );
}
