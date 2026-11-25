// [ghi chú kỹ thuật] Ô "Dataset" của khối hỏi kho bảng (collections_node): CHỌN từ kho bảng của tổ chức (GET /api/kho-bang).
// Giá trị đúng khuôn khối gốc: [{option_id, option_display_name}] (AB nodes/datasets/structured/node.py:116-118). Mọi luật ở MÁY CHỦ
// (va_kho_bang_trang/kho_bang.py: thuộc tổ chức, 1–5 dataset, không trùng) — trang chỉ hiện; mục lạ (dán từ workflow khác) hiện cảnh báo + nút bỏ.
import { useEffect, useState } from "react";

type Ds = { option_id: string; option_display_name: string; kho: string };
type Muc = { option_id: string; option_display_name?: string };
const TRAN = 5;

let _nho: Ds[] | string | null = null;   // mảng = đã đọc; chuỗi = lỗi; null = chưa đọc
const _nghe = new Set<(d: Ds[] | string | null) => void>();

export async function lamMoiKhoBang(): Promise<void> {
  try {
    const r = await fetch("/api/kho-bang");
    const d = r.status === 404 ? { loi: "Máy chủ trang đang chạy bản chưa có kho bảng." } : await r.json();
    _nho = r.ok && Array.isArray(d.datasets) ? (d.datasets as Ds[]) : String(d.loi || `Chưa đọc được kho bảng (mã ${r.status}).`);
  } catch {
    _nho = "Không nối được máy chủ trang.";
  }
  _nghe.forEach((f) => f(_nho));
}

export function ChonDataset({ v, dat }: { v: unknown; dat: (x: Muc[] | "") => void }) {
  const [ds, setDs] = useState(_nho);
  useEffect(() => {
    _nghe.add(setDs);
    if (_nho === null) lamMoiKhoBang();
    return () => { _nghe.delete(setDs); };
  }, []);
  const da: Muc[] = Array.isArray(v) ? (v as Muc[]).filter((x) => x && typeof x.option_id === "string") : [];
  const coId = new Set(da.map((x) => x.option_id));
  if (ds === null) return <div className="phu">Đang đọc kho bảng của tổ chức…</div>;
  if (typeof ds === "string") return <div className="canh-bao">{ds} <button className="nut nho" onClick={lamMoiKhoBang}>Đọc lại</button></div>;
  const la = da.filter((x) => !ds.some((d) => d.option_id === x.option_id));
  const doi = (d: Ds, bat: boolean) => {
    const moi = bat ? [...da, { option_id: d.option_id, option_display_name: d.option_display_name }] : da.filter((x) => x.option_id !== d.option_id);
    dat(moi.length ? moi : "");
  };
  return (
    <div>
      {ds.length ? ds.map((d) => (
        <label key={d.option_id} className="phu" style={{ display: "block" }}>
          <input type="checkbox" checked={coId.has(d.option_id)} disabled={!coId.has(d.option_id) && da.length >= TRAN}
            onChange={(e) => doi(d, e.target.checked)} /> {d.option_display_name || d.option_id} <span style={{ opacity: 0.7 }}>· kho {d.kho}</span>
        </label>
      )) : <div className="phu">Tổ chức này chưa có dataset nào trong kho bảng.</div>}
      <div className="phu">Chọn 1–{TRAN} dataset; mỗi dataset tốn tới 2 lần gọi AI (viết SQL + thử lại).</div>
      {la.length > 0 && (
        <div className="canh-bao" style={{ marginTop: 4 }}>
          {la.length} dataset không thuộc tổ chức này / đã xoá — Lưu và Chạy sẽ bị chặn.{" "}
          <button className="nut nho" onClick={() => { const c = da.filter((x) => !la.includes(x)); dat(c.length ? c : ""); }}>Bỏ các dataset này</button>
        </div>
      )}
    </div>
  );
}
