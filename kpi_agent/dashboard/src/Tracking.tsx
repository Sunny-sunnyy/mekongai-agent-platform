import { useState } from "react";
import { type Tracking as TrackingData, getSpice, getTracking, isRefused } from "./api";
import { millions, pct, vnd } from "./format";
import { Nv } from "./Names";
import { MembershipView } from "./Membership";
import { OutletsView, TeaView } from "./Outlets";
import { SpiceCustomers } from "./SpiceCustomers";
import { KpiPageView } from "./TrackingKpi";
import { READ_ONLY } from "./base";
import { useLoad } from "./useLoad";

// Tab "Theo dõi" (bản 2): khung giống trang theo dõi
// của đội (Tổng quan · 8 KPI · Thưởng thêm, xanh / vàng / đỏ theo tiến độ) để quen mắt, code viết mới. Trang KHÔNG tính số: mọi số, hạng, ô lệch
// do backend `/api/tracking` trả; ở đây chỉ định dạng + vẽ. Mọi chữ hiện bằng text node (không dựng HTML từ chữ backend).
export function Tracking({ sessionUsd, initialTab }: { sessionUsd: number; initialTab?: string }) {
  const data = useLoad(getTracking);
  const [tab, setTab] = useState(initialTab ?? "8-kpi");
  if (data.error) return <section className="notice error">Không tải được tab Theo dõi ({data.error.message}).</section>;
  if (!data.data) return <section className="notice">Đang tải số theo dõi…</section>;
  if (isRefused(data.data)) return <section className="notice refused">Backend từ chối: {data.data.refused}</section>;
  const t = data.data;
  // Mục con xếp ĐÚNG thứ tự + tên 6 tab của trang theo dõi của đội: 8 KPI · Thưởng thêm · Hội viên · 1% gia vị · Chi tiết · Trà 365.
  // Bảng tiền từng người (mục "Tổng quan" cũ) bỏ khỏi đây — tiền ở màn Cả đội; tổng Thưởng thêm cả đội nằm ở mục Thưởng thêm.
  // Hội viên: mới dựng PHẦN DOANH SỐ (Membership.tsx) — hạng, nhiệm vụ, tiền chưa có dữ liệu, màn đó tự ghi rõ.
  const tabs = [["8-kpi", "8 KPI"], ["thuong-them", "Thưởng thêm"], ["hoi-vien", "Hội viên"], ["gia-vi", "1% gia vị"], ["chi-tiet", "Chi tiết"], ["tra-365", "Trà 365"]];
  return (
    <section className="tracking" aria-label="Theo dõi">
      <div className="tracking-bar">
        <nav className="tracking-tabs">
          {tabs.map(([key, label]) => (
            <button key={key} type="button" className={tab === key ? "on" : ""} onClick={() => setTab(key)}>{label}</button>
          ))}
        </nav>
        <span className="hint">Tiền gọi mô hình của phiên trang này: {sessionUsd.toFixed(5)} USD</span>
      </div>
      {!t.compared_with_page && (
        READ_ONLY
          ? <p className="hint no-page-compare">Không so với trang theo dõi của đội ở chế độ xem qua Capitol (hộp xem không mang file trang của đội) — bản này không có dấu ⚠ và số "trang: …".</p>
          : <p className="hint">Chưa so từng ô với trang theo dõi của đội{t.known_reasons._page_error ? ` (${t.known_reasons._page_error})` : " (backend bật không có KPI_TRACKING_PAGE)"}.</p>
      )}
      {tab === "8-kpi" && <KpiPageView t={t} />}
      {tab === "hoi-vien" && <MembershipView />}
      {tab === "thuong-them" && <ExtraTab t={t} />}
      {tab === "gia-vi" && <SpiceTab />}
      {tab === "tra-365" && <TeaView />}
      {tab === "chi-tiet" && <OutletsView />}
    </section>
  );
}

function ExtraTab({ t }: { t: TrackingData }) {
  // Cùng khuôn với mục 8 KPI (trang của đội dùng một khuôn cho cả hai): Tổng quan % 14 người × các khoản + từng khoản xếp hạng; kèm tiền + cách tính của mình
  return (
    <>
      <div className="cards">
        <div><span>Thưởng thêm cả đội</span><b>{vnd(t.extra.team_total)}</b></div>
      </div>
      <KpiPageView t={t} kind="extra" />
    </>
  );
}

// Tab "1 % gia vị": tải riêng khi mở (backend đọc lại file đơn hàng ≈ 15 s lần đầu). Tiền trả cho khách — hiện riêng, không cộng vào lương NV.
function SpiceTab() {
  const data = useLoad(getSpice);
  const [employee, setEmployee] = useState("");   // bấm một dòng nhân viên ⇒ danh sách khách bên dưới lọc theo người đó (như trang của đội)
  if (data.error) return <section className="notice error">Không tải được 1 % gia vị ({data.error.message}).</section>;
  if (!data.data) return <p className="hint">Đang tính 1 % gia vị (lần đầu ≈ 15 giây)…</p>;
  if (isRefused(data.data)) return <section className="notice refused">Backend từ chối: {data.data.refused}</section>;
  const s = data.data;
  const totals = s.page_totals;
  return (
    <>
      <h3>Chương trình gắn kết 1 % gia vị</h3>
      <p className="hint">{s.rule_text}. Tiền trả cho {s.paid_to}.</p>
      <div className="cards">
        {totals && (
          <>
            <div><span>Chỉ tiêu (tổng nền, triệu đ)</span><b title={vnd(totals.team_baseline)}>{millions(totals.team_baseline, "tr", 1)}</b>{totals.page && <small className="dev">trang: {millions(totals.page.baseline, "tr", 1)}</small>}</div>
            <div><span>Thực hiện (doanh số gia vị)</span><b title={vnd(totals.team_sales)}>{millions(totals.team_sales, "tr", 1)}</b>{totals.page && <small className="dev">trang: {millions(totals.page.sales, "tr", 1)}</small>}</div>
            <div><span>% đạt</span><b>{pct(totals.team_pct)}</b>{totals.page && <small className="dev">trang: {pct(totals.page.pct)}</small>}</div>
          </>
        )}
        <div><span>Khách tham gia</span><b>{s.team_customers}</b></div>
        <div><span>Khách đạt ≥ 110 % nền</span><b>{s.team_customers_reached}</b></div>
        <div><span>Thưởng 1 % dự kiến cả đội</span><b>{vnd(s.team_reward)}</b></div>
        {totals && <div><span>Tiềm năng thêm · {totals.potential_customers} khách chưa đạt</span><b>{vnd(totals.potential_reward)}</b></div>}
      </div>
      {totals && (
        <p className="hint">
          "Tiềm năng thêm" = tiền thưởng sẽ có thêm nếu mỗi khách chưa đạt bán vừa đủ ngưỡng (1 % của ngưỡng, tối đa trần mỗi khách).
          {totals.page ? " Số của trang theo dõi của đội ghi nhỏ bên dưới: trang làm tròn nền và doanh số của TỪNG KHÁCH tới 10.000 đ rồi mới cộng (nền làm tròn gần nhất, doanh số làm tròn lên), ở đây cộng đủ đồng — nên hai tổng lệch nhẹ; không khách nào khác số thật, số khách đạt và tiền thưởng trùng." : ""}
        </p>
      )}
      <table className="track">
        <thead><tr><th>Nhân viên phụ trách</th><th>Khách tham gia</th><th>Khách đạt</th><th>Doanh số gia vị</th><th>Thưởng 1 % (trả khách)</th></tr></thead>
        <tbody>
          {s.employees.map((row) => (
            <tr key={row.employee_id} className={row.reward > 0 ? "du_moc" : "chua_tien"}>
              <td><button type="button" className="link" onClick={() => setEmployee(employee === row.employee_id ? "" : row.employee_id)} title="Lọc danh sách khách bên dưới theo nhân viên này"><Nv id={row.employee_id} /></button></td><td className="r">{row.customers}</td><td className="r">{row.customers_reached}</td>
              <td className="r">{vnd(row.sales)}</td><td className="r">{vnd(row.reward)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <SpiceCustomers employee={employee} onEmployee={setEmployee} />
    </>
  );
}
