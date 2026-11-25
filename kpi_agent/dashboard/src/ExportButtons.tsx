import { useState } from "react";
import { downloadExport, type ExportKind, type Meta } from "./api";

// Nút tải bảng thưởng: Excel / MD / TXT của ĐÚNG bản tính đang hiện (runId từ /api/meta) — GET /api/export/{kind}.
// Tải bằng fetch (không phải link thẳng): lỗi 409 (bản tính thiếu ô) ⇒ hiện lời máy chủ, không mở ra trang JSON. Tên file do máy chủ đặt.
const KINDS: { kind: ExportKind; label: string }[] = [
  { kind: "xlsx", label: "Excel" },
  { kind: "md", label: "Markdown" },
  { kind: "txt", label: "Chữ thường (.txt)" },
];

// Bước 3 (BỎ tải nháp): nút chỉ bật khi backend báo khối xuất đã chạy (bản tính ĐÃ SOÁT); chưa soát ⇒ nút tắt + lời backend.
// Backend vẫn tự chặn (409) — trang tắt nút chỉ để người dùng thấy lý do, không phải rào chính.
export function ExportButtons({ runId, exportState }: { runId: number | undefined; exportState: Meta["export"] }) {
  const [busy, setBusy] = useState<ExportKind | null>(null);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const start = async (kind: ExportKind) => {
    setBusy(kind);
    setMessage(null);
    try {
      const fileName = await downloadExport(kind, runId);
      setMessage({ text: `Đã tải ${fileName}`, error: false });
    } catch (problem) {
      setMessage({ text: (problem as Error).message, error: true });
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="export" aria-label="Tải bảng thưởng">
      <p className="export-label"><b>Tải bảng thưởng ĐÃ SOÁT — chưa phải số chốt (Admin miền chốt)</b>{runId ? ` (bản tính số ${runId})` : ""}:</p>
      {!exportState?.allowed && <p className="export-message" role="status">⏸ {exportState?.reason || "chưa có file"} — bấm "Đã soát" ở phần bản tính, rồi tải.</p>}
      <div className="export-buttons">
        {KINDS.map(({ kind, label }) => (
          <button key={kind} type="button" disabled={busy !== null || !exportState?.allowed || !exportState.formats.includes(kind)} onClick={() => start(kind)}>
            {busy === kind ? "Đang tải…" : label}
          </button>
        ))}
      </div>
      {message && <p className={message.error ? "export-message error" : "export-message"} role="status">{message.text}</p>}
    </section>
  );
}
