import { Nv } from "./Names";
import { useCallback, useState } from "react";
import { getEmployee, getEmployeeGap, getExplain, isRefused, type Explain, type KpiCell, type Refused } from "./api";
import { num, vnd } from "./format";
import { MarkBar } from "./MarkBar";
import { useLoad } from "./useLoad";

// Thẻ một nhân viên, mở ngay dưới hàng được bấm: mỗi KPI một hàng — thanh mốc to, chỉ tiêu / thực hiện, còn thiếu (nói theo người dùng), tiền, nút giải thích.
// Gọi thẳng backend, không qua mô hình (SPEC mục 6). Lời từ chối hiện bằng chữ thường (text node).
export function EmployeeCard({ employeeId, cells }: { employeeId: string; cells?: Record<string, KpiCell> }) {
  const loadEmployee = useCallback(() => getEmployee(employeeId), [employeeId]);
  const loadGap = useCallback(() => getEmployeeGap(employeeId), [employeeId]);
  const employee = useLoad(loadEmployee);
  const gap = useLoad(loadGap);
  const [explained, setExplained] = useState<{ kpi: string; data?: Explain | Refused; error?: string } | null>(null);

  const explain = (kpi: string) => {
    if (explained?.kpi === kpi) return setExplained(null);
    setExplained({ kpi });
    getExplain(employeeId, kpi).then(
      (data) => setExplained({ kpi, data }),
      (error: Error) => setExplained({ kpi, error: error.message }),
    );
  };

  if (employee.error) return <div className="card error">Không tải được thẻ {employeeId}: {employee.error.message}. Kiểm tra backend :8110 đang chạy.</div>;
  if (!employee.data) return <div className="card muted">Đang tải thẻ {employeeId}…</div>;
  if (isRefused(employee.data)) return <div className="card refused">Backend từ chối mã này: {employee.data.refused}</div>;

  const data = employee.data;
  const gaps = gap.data && !isRefused(gap.data) ? gap.data.employees[0]?.gaps ?? [] : [];
  const daysLeft = gap.data && !isRefused(gap.data) ? gap.data.working_days_left_after_cutoff : null;
  return (
    <div className="card">
      <div className="card-head">
        <h3><Nv id={data.employee_id} />: lương kinh doanh tạm tính {vnd(data.business_salary)}</h3>
        <p className="hint">
          Thưởng vượt (NPP chi) {vnd(data.over_target_bonus)}: {data.over_target_reason}.
          {daysLeft !== null && ` Còn ${daysLeft} ngày làm việc sau ngày chốt số.`}
        </p>
      </div>
      <ul className="kpi-list">
        {data.kpis.map((line) => {
          const next = gaps.find((g) => g.kpi === line.kpi);
          const cell = cells?.[line.kpi];
          const open = explained?.kpi === line.kpi;
          return (
            <li key={line.kpi} className="kpi-item">
              <div className="kpi-name">{line.kpi}</div>
              <div className="kpi-bar">{cell && <MarkBar cell={cell} large />}</div>
              <div className="kpi-numbers">
                <span>Chỉ tiêu <b>{num(line.target)}</b></span>
                <span>Thực hiện <b>{num(line.actual)}</b></span>
              </div>
              <div className="kpi-gap">
                {next ? (
                  // Câu "còn thiếu bao nhiêu trong mấy ngày" do backend viết (gap_per_day_text — theo TỔNG + trung bình, không làm tròn từng ngày).
                  // Trang KHÔNG tự dựng câu từ số mỗi ngày / cờ làm tròn: bản P3 hiện "khoảng 1 mỗi ngày" cho thiếu 3 / 10 ngày (= nghĩa đen 10) — 03/11 chiều.
                  next.gap_per_day_text ? (
                    <>Để lên “{next.next_tier}” (khi đó được {vnd(next.amount_if_reached)}): {next.gap_per_day_text}</>
                  ) : (
                    <>Còn thiếu {next.gap_rounded_up ? "ít nhất " : ""}<b>{num(next.gap_to_next)}</b> để lên “{next.next_tier}”, khi đó được {vnd(next.amount_if_reached)}</>
                  )
                ) : gap.error ? (
                  // tải phần "còn thiếu" lỗi ⇒ nói là lỗi; KHÔNG nói "đã ở mốc cao nhất" (B thấy 03/11 chiều: backend lỗi 500 mà thẻ vẫn khẳng định đủ mốc)
                  <span className="hint">Không tải được phần còn thiếu ({gap.error.message})</span>
                ) : !gap.data ? (
                  <span className="hint">Đang tải…</span>
                ) : isRefused(gap.data) ? (
                  <span className="hint">Chưa lấy được mốc kế (backend từ chối: {gap.data.refused})</span>
                ) : cell && cell.next_tier !== null ? (
                  <span className="hint">Chưa lấy được mốc kế</span>
                ) : (
                  <span className="hint">Đã ở mốc cao nhất</span>
                )}
              </div>
              <div className="kpi-money num">{vnd(line.amount)}</div>
              <button type="button" className="ghost" aria-expanded={open} onClick={() => explain(line.kpi)}>
                {open ? "Ẩn giải thích" : "Giải thích"}
              </button>
              {open && <ExplainBox employeeId={employeeId} {...explained!} />}
            </li>
          );
        })}
      </ul>
      <p className="hint">Turnover tính bằng đồng. Số chi tiết từ backend; trang không tự tính.</p>
    </div>
  );
}

export function ExplainBox({ employeeId, kpi, data, error }: { employeeId: string; kpi: string; data?: Explain | Refused; error?: string }) {
  if (error) return <div className="explain error">Không tải được giải thích {kpi}: {error}</div>;
  if (!data) return <div className="explain hint">Đang tải giải thích {kpi}…</div>;
  if (isRefused(data)) return <div className="explain refused">Backend từ chối: {data.refused}</div>;
  return (
    <div className="explain">
      <p><b><Nv id={employeeId} />, {data.kpi}, công văn mục {data.cong_van_section}.</b> {data.reason}</p>
      <p>Cách đếm: {data.how_counted}</p>
      <p>Các mốc: {data.tiers.map((t) => `${t.label} được ${vnd(t.amount)}`).join("; ")}.</p>
    </div>
  );
}
