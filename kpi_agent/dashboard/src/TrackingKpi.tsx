import { useState } from "react";
import { type PageKpi, type Tracking as TrackingData } from "./api";
import { dateOnly, millions, num, pct, vnd } from "./format";
import { Nv } from "./Names";

// Mục "8 KPI" của tab Theo dõi, vẽ theo BỐ CỤC trang theo dõi của đội (người dùng: "dựng đầy đủ các trường" như trang của đội):
// nút Tổng quan + KPI 1…8 (tên đúng chữ trang), dòng cập nhật + tiến độ thời gian, bảng % 14 người × 8 chỉ tiêu tô xanh / đỏ theo tiến độ, dòng Tổng;
// mỗi chỉ tiêu: chữ mốc, 3 ô tổng, 14 dòng xếp hạng với "thực hiện / chỉ tiêu", còn thiếu tới 100 % chỉ tiêu, cần mỗi ngày, thanh có vạch thời gian.
// KHÔNG hiện tiền (trang của đội không có) — tiền + mốc thưởng ở Cả đội › Theo chỉ tiêu. Trang không tính số: mọi số, hạng, màu do backend trả
// (`page_view` của /api/tracking); ô mình tính KHÁC trang của đội giữ số của mình + dấu ⚠ kèm lý do và số của trang.
// Khoản tính bằng tiền (doanh số) hiện theo triệu đồng 3 số lẻ như trang của đội, rê chuột ra số đủ đồng; khoản đếm hiện nguyên số
const amount = (item: PageKpi, value: number) => (item.money ? millions(value, "tr", 3) : num(value));
const exactTitle = (item: PageKpi, value: number) => (item.money ? vnd(value) : undefined);
// Chú thích ô lệch của backend có dạng "<lý do> (trang: <số của trang>)" ⇒ tách để hiện lý do một lần + số của trang ở từng dòng (chỉ cắt chữ, không tính)
const splitNote = (why: string) => {
  const found = why.match(/^(.*) \((trang: [^()]*)\)$/s);
  return found ? { reason: found[1], page: found[2] } : { reason: why, page: null };
};
// 8 KPI ⇒ mở đúng chỉ tiêu ở Cả đội › Theo chỉ tiêu; khoản Thưởng thêm ⇒ mở màn đó (khoản thưởng thêm chọn tiếp ở hàng nút dưới)
const detailLink = (kind: Kind, key: string) => `${import.meta.env.BASE_URL}?man=chi-tieu${kind === "kpis" ? `&kpi=${encodeURIComponent(key)}` : ""}`;
type Kind = "kpis" | "extra";

export function KpiPageView({ t, kind = "kpis" }: { t: TrackingData; kind?: Kind }) {
  const [picked, setPicked] = useState<string>("tong-quan");
  const view = t.page_view;
  const items = view?.[kind];
  if (!view || !items) return <section className="notice refused">Backend bản này chưa trả số cho kiểu xem theo trang của đội.</section>;
  const kpis = Object.keys(items);
  const note = (kpi: string, code: string) => t.deviations.find((d) => d.key === kpi && d.employee_id === code)?.note;
  return (
    <div className="page-kpi">
      <div className="time-line">
        <span>Cập nhật <b>{dateOnly(view.cutoff)}</b> · <b>{view.days_done}/{view.days_total}</b> ngày làm việc</span>
        <span className="time-bar" role="img" aria-label={`Tiến độ thời gian ${pct(view.time_progress_pct)}`}>
          <span className="fill" style={{ width: `${Math.min(view.time_progress_pct, 100)}%` }} />
        </span>
        <span>Tiến độ thời gian <b>{pct(view.time_progress_pct)}</b></span>
      </div>
      <nav className="tracking-tabs" aria-label={kind === "kpis" ? "Chọn KPI theo trang của đội" : "Chọn khoản thưởng thêm theo trang của đội"}>
        <button type="button" className={picked === "tong-quan" ? "on" : ""} onClick={() => setPicked("tong-quan")}>Tổng quan</button>
        {kpis.map((kpi) => (
          <button key={kpi} type="button" className={picked === kpi ? "on" : ""} onClick={() => setPicked(kpi)} title={`Tên bên mình: ${kpi}`}>{items[kpi].page_title}</button>
        ))}
      </nav>
      {picked === "tong-quan" || !items[picked]
        ? <Matrix t={t} kind={kind} items={items} note={note} onPick={setPicked} />
        : <OneKpi kpi={picked} kind={kind} item={items[picked]} t={t} note={note} />}
    </div>
  );
}

function Matrix({ t, kind, items, note, onPick }: { t: TrackingData; kind: Kind; items: Record<string, PageKpi>; note: (kpi: string, code: string) => string | undefined; onPick: (kpi: string) => void }) {
  const view = t.page_view!;
  const kpis = Object.keys(items);
  const cell = (kpi: string, code: string) => items[kpi].rows.find((row) => row.employee_id === code)!;
  return (
    <>
      <p className="hint legend">
        <span className="chip on-track">xanh</span> % đạt ≥ tiến độ thời gian · <span className="chip off-track">đỏ</span> thấp hơn tiến độ · số lớn = % làm tròn như trang của
        đội, số nhỏ = % đúng 2 số lẻ · <span className="dev">⚠</span> = ô mình tính khác trang của đội (rê chuột / bấm chỉ tiêu để xem lý do).
        Tiền và mốc thưởng: <a href={detailLink(kind, kpis[0])}>Cả đội › Theo chỉ tiêu</a>.
      </p>
      <div className="matrix-scroll">
        <table className="track matrix">
          <thead>
            <tr><th>Nhân viên</th>{kpis.map((kpi, index) => (
              <th key={kpi}><button type="button" className="link" onClick={() => onPick(kpi)} title={items[kpi].page_title}>{kind === "kpis" ? `KPI ${index + 1}` : kpi}<small>{kind === "kpis" ? kpi : items[kpi].page_title.replace(/^Thưởng [\d.]+: /, "")}</small></button></th>
            ))}</tr>
          </thead>
          <tbody>
            {t.employees.map((row) => (
              <tr key={row.employee_id}>
                <td><Nv id={row.employee_id} /></td>
                {kpis.map((kpi) => {
                  const item = cell(kpi, row.employee_id), why = note(kpi, row.employee_id);
                  return (
                    <td key={kpi} className={`r ${item.on_track ? "on-track" : "off-track"}`} title={why ? `Trang theo dõi của đội tính khác: ${why}` : undefined}>
                      <b>{item.pct_rounded} %</b>{why && <span className="dev"> ⚠</span>}<small>{pct(item.pct_to_target)}</small>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr><th>Tổng</th>{kpis.map((kpi) => {
              const team = items[kpi].team;
              return <td key={kpi} className={`r ${team.on_track ? "on-track" : "off-track"}`}><b>{team.pct_rounded} %</b>{items[kpi].is_ratio && <span title={view.ratio_total_note}> *</span>}<small>{pct(team.pct_to_target)}</small></td>;
            })}</tr>
          </tfoot>
        </table>
      </div>
      {kind === "kpis" && <p className="hint">* {view.ratio_total_note}.</p>}
      <p className="hint">
        Lệch nhỏ do cách làm tròn (không phải khác luật): % số nguyên ở đây làm tròn trên số đủ lẻ, trang của đội làm tròn trên số đã cắt 3 số lẻ nên vài ô có thể hơn
        kém 1 đơn vị; tỷ lệ và % ở đây làm tròn XUỐNG 2 số lẻ nên có thể thấp hơn trang 0,01.
      </p>
    </>
  );
}

function OneKpi({ kpi, kind, item, t, note }: { kpi: string; kind: Kind; item: PageKpi; t: TrackingData; note: (kpi: string, code: string) => string | undefined }) {
  const view = t.page_view!;
  const byRegion = item.tiers.some((tier) => tier.region !== null);
  const total = item.rows.length;
  const hasMoney = item.rows.some((row) => row.amount !== undefined);   // khoản Thưởng thêm: kèm tiền + cách tính của mình (trang của đội không có)
  // Lý do giống nhau ở nhiều người chỉ nêu MỘT lần (nhìn ảnh 13/11: KPI 2 lặp cùng một đoạn 14 lần); số của trang vẫn ghi ở từng dòng
  const byReason = new Map<string, string[]>();
  item.rows.forEach((row) => {
    const why = note(kpi, row.employee_id);
    if (why) byReason.set(splitNote(why).reason, [...(byReason.get(splitNote(why).reason) ?? []), row.employee_id]);
  });
  const reasons = [...byReason.entries()];
  return (
    <>
      <h3>{item.page_title} <small className="hint">· tên bên mình: {kpi}</small></h3>
      <p className="hint">
        {item.rule_text ? `Luật của khoản: ${item.rule_text}.` : `Mốc thưởng theo công văn: ${item.tiers.map((tier) => `${byRegion ? tier.region + " — " : ""}${tier.label}: ${millions(tier.amount, "triệu")}`).join(" · ")}.`}{" "}
        <a href={detailLink(kind, kpi)}>{kind === "kpis" ? "Xem tiền và mốc thưởng từng người" : "Xem ngưỡng và tiền từng khoản"}: Cả đội › Theo chỉ tiêu</a>
      </p>
      {reasons.length > 0 && (
        <div className="page-diff">
          <b>⚠ Chỗ bên mình tính khác trang theo dõi của đội</b> (số của mình là số chính; số của trang ghi cạnh từng dòng):
          <ul>{reasons.map(([reason, codes]) => (
            <li key={reason}>{codes.length === total ? `Cả ${total} người` : codes.map((code, index) => <span key={code}>{index > 0 && ", "}<Nv id={code} /></span>)}: {reason}.</li>
          ))}</ul>
        </div>
      )}
      <div className="cards">
        <div><span>Chỉ tiêu{item.money ? " (triệu đ)" : item.unit ? ` (${item.unit})` : ""}</span><b title={exactTitle(item, item.team.target)}>{amount(item, item.team.target)}</b></div>
        <div><span>Thực hiện</span><b title={exactTitle(item, item.team.actual)}>{amount(item, item.team.actual)}</b></div>
        <div className={item.team.on_track ? "on-track" : "off-track"}><span>% đạt{item.is_ratio ? " *" : ""}</span><b>{pct(item.team.pct_to_target)}</b></div>
      </div>
      <table className="track ranked">
        <thead><tr><th>Hạng</th><th>Nhân viên</th><th>Thực hiện / chỉ tiêu</th><th>Còn thiếu tới 100 % chỉ tiêu</th><th>Cần mỗi ngày</th><th>% đạt</th><th>Tiến độ</th>{hasMoney && <><th>Tiền</th><th>Cách tính</th></>}</tr></thead>
        <tbody>
          {item.rows.map((row) => {
            const why = note(kpi, row.employee_id);
            const place = row.rank <= 3 ? "top" : row.rank > total - 3 ? "low" : "";
            return (
              <tr key={row.employee_id}>
                <td><span className={`rank ${place}`}>{row.rank}</span></td><td><Nv id={row.employee_id} /></td>
                <td className="r">
                  <span title={exactTitle(item, row.actual)}>{amount(item, row.actual)}</span> / <span title={exactTitle(item, row.target)}>{amount(item, row.target)}</span>
                  {why && <small className="dev page-number" title={why}>⚠ {splitNote(why).page ?? "khác trang của đội"}</small>}
                </td>
                <td className="r" title={exactTitle(item, row.shortfall)}>{amount(item, row.shortfall)}</td>
                <td className="r" title={exactTitle(item, row.per_day)}>{amount(item, row.per_day)}</td>
                <td className={`r ${row.on_track ? "on-track" : "off-track"}`}><b>{pct(row.pct_to_target)}</b></td>
                <td className="meter-cell">
                  <span className={`meter ${row.on_track ? "on-track" : "off-track"}`} aria-hidden="true">
                    <span className="fill" style={{ width: `${Math.min(row.pct_to_target, 100)}%` }} />
                    <span className="mark" style={{ left: `${Math.min(view.time_progress_pct, 100)}%` }} />
                  </span>
                </td>
                {hasMoney && <><td className="r">{vnd(row.amount)}</td><td className="how">{row.how}</td></>}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="hint">
        Vạch đen trên thanh = tiến độ thời gian ({pct(view.time_progress_pct)}). {view.shortfall_note}.{view.per_day_note ? ` ${view.per_day_note}.` : ""}
        {item.is_ratio ? ` * ${view.ratio_total_note}.` : ""}
      </p>
    </>
  );
}
