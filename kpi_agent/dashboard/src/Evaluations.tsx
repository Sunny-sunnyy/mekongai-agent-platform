import { Fragment, useState } from "react";
import { type EvalVersion, type Sourced, getEvaluations, isRefused } from "./api";
import { useLoad } from "./useLoad";

// Tab "Đánh giá" (backend/evaluations.py): mỗi phiên bản một dòng, CỘT TIỀN SAI ĐỨNG ĐẦU (tiền sai xét trước mọi chỉ số khác).
// Trang KHÔNG tính gì: số, nhãn giữ / quay lại, lý do, nguồn từng số đều do backend trả; số chưa có hiện "chưa có" (không để trống, không đoán).
// Khung dùng lại kiểu bảng của tab Theo dõi — phần thẩm mỹ làm sau khi chọn hướng giao diện.
const shown = (cell: Sourced<string | number | boolean>) => (cell.value === null ? "chưa có" : cell.value === true ? "đạt" : cell.value === false ? "không đạt" : String(cell.value));

export function Evaluations() {
  const data = useLoad(getEvaluations);
  const [open, setOpen] = useState<string | null>(null);
  if (data.error) return <section className="notice error">Không tải được tab Đánh giá ({data.error.message}).</section>;
  if (!data.data) return <section className="notice">Đang tải số đánh giá…</section>;
  // bản xem (chỉ đọc, VITE_KPI_READ_ONLY=1): hộp xem không mang file số đánh giá ⇒ nói thẳng màn này chưa có, không hiện lời kỹ thuật
  if (isRefused(data.data)) return <section className="notice refused">{import.meta.env.VITE_KPI_READ_ONLY === "1" ? "Màn Đánh giá chưa có ở bản xem." : `Chưa có số đánh giá: ${data.data.refused}.`}</section>;
  const view = data.data;
  return (
    <section className="tracking" aria-label="Đánh giá">
      <h3>Đánh giá từng phiên bản trợ lý</h3>
      <p className="hint">Chỉ đọc kết quả đã đo{view.built_at ? `, dựng lúc ${view.built_at}` : ""}. Lưu ý: {view.noise_note}. Bấm một dòng để xem đổi gì so bản trước và nguồn từng số.</p>
      <table className="track">
        <thead><tr><th>Phiên bản</th><th>Tiền sai (8 câu Capitol)</th><th>8 câu: đúng / tới người hỏi</th><th>20 câu Capitol (máy chấm)</th>
          <th>Bộ câu trợ lý chat: chọn công cụ</th><th>Số không nguồn</th><th>Nhãn</th></tr></thead>
        <tbody>
          {view.versions.map((row) => (
            <Fragment key={row.commit}>
              <tr onClick={() => setOpen(open === row.commit ? null : row.commit)} aria-expanded={open === row.commit} style={{ cursor: "pointer" }}>
                <td>{row.id} <span className="hint">({row.commit})</span></td>
                <td className="r">{shown(row.capitol_8.money_wrong)}</td>
                <td className="r">{shown(row.capitol_8.correct)} / {shown(row.capitol_8.received)}</td>
                <td className="r">{shown(row.m3.passed)}</td>
                <td className="r">{row.t4 ? shown(row.t4.tool_ok) : "chưa có"}</td>
                <td className="r">{row.t4 ? shown(row.t4.unsourced_final) : "chưa có"}</td>
                <td><b>{row.label}</b> <span className="hint">({row.label_reason})</span></td>
              </tr>
              {open === row.commit && <tr className="devrow"><td colSpan={7}><Detail row={row} /></td></tr>}
            </Fragment>
          ))}
        </tbody>
      </table>
      <p className="hint">Luật nhãn (ghi trước khi dựng bảng): {view.label_rule}.</p>
    </section>
  );
}

function Detail({ row }: { row: EvalVersion }) {
  const numbers: [string, Sourced<string | number | boolean>][] = [
    ["8 câu Capitol: tiền sai", row.capitol_8.money_wrong], ["8 câu Capitol: đúng nội dung", row.capitol_8.correct],
    ["8 câu Capitol: tới người hỏi", row.capitol_8.received], ["8 câu Capitol: qua Rào chắn", row.capitol_8.gate_passed], ["8 câu Capitol: tiền gọi mô hình (USD)", row.capitol_8.usd],
    ["20 câu Capitol: máy chấm đạt", row.m3.passed], ["20 câu Capitol: ngưỡng", row.m3.threshold], ["20 câu Capitol: kết luận của máy chấm", row.m3.machine_verdict],
    ["20 câu Capitol: tiền gọi mô hình (USD)", row.m3.usd], ["20 câu Capitol: người đọc nghĩa đạt", row.m3_reading], ["Bộ câu kiểm cuối: câu sai lọt rào", row.sealed_set_leak],
    ...(row.t4 ? ([
      ["Trợ lý chat: đo trên commit", row.t4.git_head], ["Trợ lý chat: mô hình", row.t4.model], ["Trợ lý chat: mức suy nghĩ", row.t4.reasoning],
      ["Trợ lý chat: số câu đã chạy", row.t4.ran], ["Trợ lý chat: chọn đúng công cụ", row.t4.tool_ok], ["Trợ lý chat: đúng tham số", row.t4.args_ok],
      ["Trợ lý chat: số không nguồn", row.t4.unsourced_final], ["Trợ lý chat: chặn nhầm", row.t4.false_block], ["Trợ lý chat: đạt mọi tiêu chí", row.t4.verdict_all],
      ["Trợ lý chat: tiền gọi mô hình (USD)", row.t4.usd], ["Trợ lý chat: giây mỗi câu", row.t4.seconds_per_question],
    ] as [string, Sourced<string | number | boolean>][]) : []),
  ];
  return (
    <div>
      {row.note && <p className="hint">Ghi chú: {row.note}</p>}
      {row.m3.failed_questions.length > 0 && <p>Máy chấm 20 câu Capitol đánh trượt câu: {row.m3.failed_questions.join(", ")}.</p>}
      {row.t4 && row.t4.verdict_failed.length > 0 && <p>Tiêu chí bộ câu trợ lý chat chưa đạt: {row.t4.verdict_failed.join("; ")}.</p>}
      {!row.t4 && <p className="hint">Bộ câu trợ lý chat: chưa có — sổ nguồn chưa ghi lượt đo cho phiên bản này.</p>}
      <p><b>Đổi gì so bản trước:</b> {row.changes ? `${row.changes.diffstat}${row.changes.commit_count === null ? "" : ` · ${row.changes.commit_count} commit`}` : "đây là bản đầu trong bảng"}</p>
      {row.changes && row.changes.commits.length > 0 && <ul>{row.changes.commits.map((line) => <li key={line}>{line}</li>)}</ul>}
      <table className="track">
        <thead><tr><th>Số</th><th>Giá trị</th><th>Lấy từ đâu</th></tr></thead>
        <tbody>{numbers.map(([name, cell]) => <tr key={name}><td>{name}</td><td className="r">{shown(cell)}</td><td>{cell.source}</td></tr>)}</tbody>
      </table>
    </div>
  );
}
