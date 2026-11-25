import { type KpiCell } from "./api";
import { num, pct } from "./format";

// Thanh mốc — thứ duy nhất được "nổi" trên trang (thiết kế mục 3): rãnh xám, phần đã làm tô theo trạng thái, vạch mảnh tại từng mốc.
// Vị trí phần đã làm + vạch mốc do backend trả (progress, marks — tỉ lệ so mốc cao nhất); trang chỉ đổi ra % độ rộng để vẽ.
export const STATUS = {
  du_moc: { icon: "✓", label: "đủ" },
  moc_thap: { icon: "◐", label: "mốc thấp" },
  chua_tien: { icon: "○", label: "0 đ" },
} as const;

export function MarkBar({ cell, large = false }: { cell: KpiCell; large?: boolean }) {
  const width = Math.min(cell.progress, 1) * 100;   // vượt mốc cao nhất ⇒ đầy thanh
  const status = STATUS[cell.status];
  return (
    <div className={`markbar ${cell.status}${large ? " large" : ""}`}>
      <div className="track" aria-hidden="true">
        <div className="fill" style={{ width: `${width}%` }} />
        {/* hết thanh = mốc cao nhất (marks cuối = 1) ⇒ chỉ vẽ vạch cho các mốc thấp hơn */}
        {cell.marks.filter((mark) => mark < 1).map((mark) => <span key={mark} className="mark" style={{ left: `${mark * 100}%` }} />)}
      </div>
      <div className="caption">
        {/* bảng: biểu tượng + số (chữ trạng thái cho trình đọc màn hình + chú giải); thẻ NV (large): biểu tượng + chữ đầy đủ */}
        <span className="state"><span aria-hidden="true">{status.icon}</span>{large ? ` ${status.label}` : <span className="sr-only"> {status.label}</span>}</span>
        <span className="value">{cell.measure === "value" ? num(cell.value) : pct(cell.pct)}</span>
      </div>
    </div>
  );
}
