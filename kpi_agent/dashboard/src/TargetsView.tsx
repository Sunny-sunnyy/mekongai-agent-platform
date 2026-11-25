import { type ExtraBonus, type GapResult, type Refused, type TeamBonus, getExtraBonus, getTracking, isRefused } from "./api";
import { dong, exact, vnd } from "./format";
import { KpiView } from "./KpiView";
import { Nv } from "./Names";
import { useLoad } from "./useLoad";

// Kiểu xem "Theo chỉ tiêu" = 8 KPI + các khoản THƯỞNG THÊM ngoài 8 KPI (việc 6 "giao diện bậc sau": trang của đội có tab Thưởng thêm).
// Hàng chọn thứ nhất: 8 KPI (màn KpiView như cũ). Hàng thứ hai: từng khoản thưởng thêm, hiện bằng TÊN khoản (mã T01… để nhỏ; ?kpi=T03 mở thẳng).
// TRANG KHÔNG TÍNH TIỀN: mọi số của khoản thưởng thêm lấy từ /api/extra-bonus (backend tính sẵn lúc bật); trang chỉ sắp thứ tự dòng.
// "Thưởng 01: Bia Sư Tử Trắng" ⇒ "Bia Sư Tử Trắng"; "Thưởng 7.1: Doanh số Xúc xích …" ⇒ "Xúc xích …" (ba khoản xúc xích tên dài, cắt đuôi thì giống hệt nhau
// ⇒ bỏ chữ "Doanh số" ở đầu và đặt mã khoản nhỏ TRƯỚC tên để vẫn phân biệt được)
const shortTitle = (title: string) => title.replace(/^Thưởng\s+[\d.]+\s*:\s*/, "").replace(/^Doanh số\s+/, "");

export function TargetsView({ team, kpis, gap, focus, onPick }: {
  team: TeamBonus; kpis: string[]; gap: GapResult | Refused | undefined; focus: string | null; onPick: (name: string) => void;
}) {
  const load = useLoad(getExtraBonus);
  const extra = load.data && !isRefused(load.data) ? load.data : undefined;
  const titles = new Map((extra?.lines ?? []).map((line) => [line.item, line.title]));
  const items = extra?.items ?? [];
  // ?kpi= không phân biệt hoa thường; khớp KPI trước, rồi tới mã khoản thưởng thêm; tên lạ ⇒ KPI đầu
  const kpi = kpis.find((name) => name.toLowerCase() === focus?.toLowerCase());
  const item = kpi ? undefined : items.find((entry) => entry.item.toLowerCase() === focus?.toLowerCase());
  // đường dẫn trỏ một khoản thưởng thêm mà số chưa tải xong ⇒ chờ, không nhảy tạm sang KPI đầu
  const waiting = !kpi && focus !== null && load.data === undefined && !load.error;
  const nav = (
    <div className="target-nav">
      <div className="row">
        <span className="hint">8 KPI (lương kinh doanh):</span>
        <nav className="tracking-tabs" aria-label="Chọn chỉ tiêu">
          {kpis.map((name) => {
            const on = !item && !waiting && name === (kpi ?? kpis[0]);
            return <button key={name} type="button" className={on ? "on" : ""} aria-pressed={on} onClick={() => onPick(name)}>{name}</button>;
          })}
        </nav>
      </div>
      <div className="row">
        <span className="hint">Thưởng thêm (ngoài 8 KPI):</span>
        {extra ? (
          <nav className="tracking-tabs" aria-label="Chọn khoản thưởng thêm">
            {items.map((entry) => (
              <button key={entry.item} type="button" className={item?.item === entry.item ? "on" : ""} aria-pressed={item?.item === entry.item}
                title={titles.get(entry.item) ?? entry.item} onClick={() => onPick(entry.item)}>
                <small>{entry.item}</small>{shortTitle(titles.get(entry.item) ?? entry.item)}
              </button>
            ))}
          </nav>
        ) : <span className="hint">{load.data && isRefused(load.data) ? `chưa có — ${load.data.refused}` : load.error ? `không tải được (${load.error.message})` : "đang tải…"}</span>}
      </div>
    </div>
  );
  if (waiting) return <section className="kpi-view" aria-label="Theo chỉ tiêu">{nav}<p className="hint">Đang tải khoản thưởng thêm…</p></section>;
  if (item && extra) return <ExtraItemView extra={extra} itemId={item.item} nav={nav} employeeCount={team.employees.length} />;
  return <KpiView team={team} gap={gap} kpi={kpi ?? kpis[0]} nav={nav} />;
}

// Đơn vị của cột "Thực hiện" / "Ngưỡng" theo CÁCH ĐO backend ghi cho từng khoản (`measure` trong file luật thưởng thêm, trả qua /api/tracking).
// Cách đo chưa có trong bảng ⇒ không ghi đơn vị (không đoán) — người xem đọc ở dòng luật + cột "Cách tính".
const MEASURE_UNIT: Record<string, { unit: string; money: boolean }> = {
  cases: { unit: "thùng", money: false }, aso: { unit: "điểm bán", money: false },
  sales_pct_of_target: { unit: "đ", money: true }, sales_rate_of_target: { unit: "đ", money: true },
};

function ExtraItemView({ extra, itemId, nav, employeeCount }: { extra: ExtraBonus; itemId: string; nav: React.ReactNode; employeeCount: number }) {
  const lines = extra.lines.filter((line) => line.item === itemId);
  const item = extra.items.find((entry) => entry.item === itemId);
  const title = lines[0]?.title ?? itemId;
  // tổng của khoản: backend trả `team_total` ở dòng khoản; khoản chỉ MỘT người có tiền thì backend đặt số đó cạnh mã người ấy (cùng một trường)
  const rows = [...lines].sort((a, b) => b.amount - a.amount || b.actual - a.actual);
  // Dòng luật + cách đo của khoản: backend chỉ trả ở /api/tracking (tab Theo dõi dùng chung; lần đầu ≈ 10 giây) ⇒ tải riêng, chưa có thì chưa ghi đơn vị
  const tracking = useLoad(getTracking);
  const rule = tracking.data && !isRefused(tracking.data) ? tracking.data.extra.items.find((entry) => entry.id === itemId) : undefined;
  const measure = rule?.measure ? MEASURE_UNIT[rule.measure] : undefined;
  // doanh số trong file đơn hàng có phần lẻ xu ⇒ hiện tới đồng như các màn khác (số backend trả không đổi; tiền thưởng vẫn hiện nguyên)
  const amountOf = (value: number) => (measure?.money ? dong(value) : exact(value));
  return (
    <section className="kpi-view" aria-label="Theo chỉ tiêu">
      {nav}
      <div className="kpi-head">
        <div>
          <p className="hero-kicker">Thưởng thêm · {itemId}</p>
          <p className="kpi-total">{vnd(item?.team_total)}</p>
          <p className="hero-sub">{title} — đội đang nhận ở khoản này · <b>{item ? item.paid_count : "—"} / {employeeCount}</b> người có tiền</p>
          {rule && <p className="hero-sub">Luật của khoản: {rule.label}{measure ? ` · thực hiện và ngưỡng tính bằng ${measure.unit === "đ" ? "đồng" : measure.unit}` : ""}</p>}
        </div>
        <div className="stats">
          <div className="stat"><div className="v">{vnd(extra.extra_bonus_team_total)}</div><div className="l">tổng mọi khoản thưởng thêm của đội</div></div>
          <div className="stat"><div className="v">{vnd(extra.team_total_8_kpi)}</div><div className="l">lương kinh doanh 8 KPI của đội (KHÔNG gồm thưởng thêm)</div></div>
        </div>
      </div>
      <p className="outside-note">Khoản ngoài 8 KPI — không nằm trong lương kinh doanh {vnd(extra.team_total_8_kpi)} của đội; đừng cộng hai lần.</p>
      {lines[0]?.gioi_han && <p className="kpi-rule"><b>Giới hạn:</b> {lines[0].gioi_han}</p>}
      <div className="board">
        <div className="scroll">
          <table className="compact-table kpi-table">
            <thead>
              <tr>
                <th scope="col" className="sticky left">Nhân viên</th>
                <th scope="col">Thực hiện{measure && !measure.money && <span className="sub">{measure.unit}</span>}</th>
                <th scope="col">Ngưỡng / chỉ tiêu{measure && !measure.money && <span className="sub">{measure.unit}</span>}</th>
                <th scope="col" className="left">Đạt</th>
                <th scope="col">Tiền</th>
                <th scope="col" className="left">Cách tính</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((line) => (
                <tr key={line.employee_id}>
                  <th scope="row" className="sticky left"><Nv id={line.employee_id} /></th>
                  <td>{amountOf(line.actual)}</td>
                  <td>{amountOf(line.target)}</td>
                  <td className="left">{line.reached ? "✓ đạt" : "chưa đạt"}</td>
                  <td className={line.amount > 0 ? "du_moc" : "chua_tien"}><span className="m">{vnd(line.amount)}</span></td>
                  <td className="how">{line.how}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="total-row"><th scope="row" className="sticky left">Cả đội</th><td colSpan={3} className="left">{item ? item.paid_count : "—"} / {employeeCount} người có tiền</td><td className="sum"><span className="m">{vnd(item?.team_total)}</span></td><td /></tr>
            </tfoot>
          </table>
        </div>
      </div>
      <p className="foot">
        Số do backend tính (cùng nguồn với tab Theo dõi › Thưởng thêm + Meat và công cụ "Thưởng thêm" của trợ lý); trang chỉ sắp thứ tự.
        Khoản thưởng thêm chưa có "còn thiếu để lên mốc kế / tiền nếu lên mốc kế" — backend chưa trả.
      </p>
    </section>
  );
}
