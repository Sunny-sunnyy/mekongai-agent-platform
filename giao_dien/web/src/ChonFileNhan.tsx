// [ghi chú kỹ thuật] Nút "Chọn file" trong Cài đặt của khối Nhận file: tải 1 file lên kho file của Capitol (agentic-backend
// POST /files/{org}/upload — qua máy chủ trang /api/tep/tai-len) rồi điền phong bì file trả về vào ô file_data của khối.
import { useState } from "react";

const DUOI = [".csv", ".doc", ".docx", ".gif", ".jpeg", ".jpg", ".json", ".jsonl", ".markdown", ".md", ".pdf", ".png", ".ppt", ".pptx",
  ".tsv", ".txt", ".webp", ".xls", ".xlsx"];   // tập con danh sách cho phép của Capitol (services/upload_allowlist.py)
const TRAN = 20 * 1024 * 1024;

export function ChonFileNhan({ onXong, daCo }: { onXong: (phongBi: unknown) => void; daCo: boolean }) {
  const [dang, setDang] = useState(false);
  const [bao, setBao] = useState("");
  const chon = async (f: File | null) => {
    setBao("");
    if (!f) return;
    const duoi = "." + (f.name.split(".").pop() || "").toLowerCase();
    if (!DUOI.includes(duoi)) return setBao(`Không nhận đuôi ${duoi}. Được: ${DUOI.join(" ")}`);
    if (f.size > TRAN) return setBao("File lớn hơn 20 MB — không tải được.");
    setDang(true);
    try {
      const r = await fetch("/api/tep/tai-len?ten=" + encodeURIComponent(f.name), {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: await f.arrayBuffer(),
      });
      const d = await r.json();
      if (!r.ok || !d || !d.file) setBao(d?.loi || d?.refused || `Tải lên không thành công (mã ${r.status}).`);
      else { onXong(d); setBao(`Đã tải "${d.file.original_filename || f.name}" — bấm “Lưu rồi chạy”.`); }
    } catch {
      setBao("Không gửi được file tới máy chủ trang.");
    } finally {
      setDang(false);
    }
  };
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="phu" style={{ marginBottom: 4 }}>
        File đi vào <b>kho file của Capitol</b> (dùng cho khối nhận file của workflow). File dữ liệu KPI tải ở tab “Dữ liệu KPI” — kho này không nạp vào phần tính thưởng.
      </div>
      <input type="file" accept={DUOI.join(",")} disabled={dang} onChange={(e) => chon(e.target.files?.[0] || null)} />
      {dang && <div className="phu">Đang tải…</div>}
      {!bao && daCo && <div className="phu">Khối đã có một file.</div>}
      {bao && <div className="phu" style={{ marginTop: 4 }}>{bao}</div>}
    </div>
  );
}
