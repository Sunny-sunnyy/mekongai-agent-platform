// [ghi chú kỹ thuật] Tab "Tài liệu": nạp file vào kho (app RAG mekongai qua hộp cổng) rồi hỏi.
// Mọi luật ở MÁY CHỦ (va_kho_tai_lieu/kho_tai_lieu.py): tổ chức do máy chủ gắn, tên / đuôi / cỡ / số trang, giữ chỗ tiền, một việc nạp mỗi lúc.
// Trang chỉ HIỆN: tên file là dữ liệu người dùng ⇒ chỉ hiện dạng chữ (React tự thoát), không ghép vào đường dẫn — mở nguồn bằng sha.
import { useEffect, useState } from "react";

export type Kho = { sha: string; ten: string; kho: string; trang: number | null; luc: number; tam: boolean; an: boolean; ghi_chu: string };
type DsKho = { noi_app: boolean; kho: Kho[]; trang_toi_da: number; duoi: string[] };
type Viec = { tt: "dang_nap" | "xong" | "loi"; ten?: string; trang?: number | null; muc?: number; kho?: string; loi?: string };

let _nho: DsKho | null | "khong_co" = null;           // nhớ trong trang (một lần tải); nạp xong thì làm mới
const _nghe = new Set<(d: DsKho | null | "khong_co") => void>();

export async function lamMoiKho(): Promise<void> {
  try {
    const r = await fetch("/api/tai-lieu/kho");
    _nho = r.status === 404 ? "khong_co" : r.ok ? ((await r.json()) as DsKho) : null;   // 404 = máy chủ trang bản cũ (chưa gắn kho tài liệu)
  } catch {
    _nho = null;
  }
  _nghe.forEach((f) => f(_nho));
}

export function useKho(): DsKho | null | "khong_co" {
  const [d, setD] = useState(_nho);
  useEffect(() => {
    _nghe.add(setD);
    if (_nho === null) lamMoiKho();
    return () => { _nghe.delete(setD); };
  }, []);
  return d;
}

export const moNguon = (sha: string, trang?: number | null) => `/api/tai-lieu/mo?sha=${encodeURIComponent(sha)}${trang ? `#page=${trang}` : ""}`;

export function TaiLieu() {
  const ds = useKho();
  const [tep, setTep] = useState<File | null>(null);
  const [tam, setTam] = useState(false);
  const [baoLoi, setBaoLoi] = useState("");
  const [viec, setViec] = useState<{ ma: string; muc: number; v: Viec | null } | null>(null);
  const [hienAn, setHienAn] = useState(false);

  useEffect(() => {   // hỏi trạng thái việc nạp vài giây một lần
    if (!viec || viec.v?.tt === "xong" || viec.v?.tt === "loi") return;
    const t = setTimeout(async () => {
      try {
        const r = await fetch("/api/tai-lieu/viec?ma=" + encodeURIComponent(viec.ma));
        const v = (await r.json()) as Viec;
        setViec({ ...viec, v });
        if (v.tt === "xong") lamMoiKho();
      } catch { /* mạng chập chờn ⇒ lần sau hỏi lại */ setViec({ ...viec }); }
    }, 3000);
    return () => clearTimeout(t);
  }, [viec]);

  if (ds === "khong_co") return <div className="phu">Máy chủ trang đang chạy bản chưa có kho tài liệu.</div>;
  if (ds === null) return <div className="phu">Đang đọc danh sách kho…</div>;

  const nap = async () => {
    setBaoLoi("");
    if (!tep) return setBaoLoi("Chưa chọn file.");
    const duoi = "." + (tep.name.split(".").pop() || "").toLowerCase();
    if (!ds.duoi.includes(duoi)) return setBaoLoi("Chỉ nhận: " + ds.duoi.join(", ") + ".");
    if (tep.size > 20 * 1024 * 1024) return setBaoLoi("File lớn hơn 20 MB.");
    try {
      const r = await fetch(`/api/tai-lieu/nap?ten=${encodeURIComponent(tep.name)}${tam ? "&tam=1" : ""}`, {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: await tep.arrayBuffer(),
      });
      const d = await r.json();
      if (r.status !== 202) return setBaoLoi(d.loi || `Chưa nạp được (mã ${r.status}).`);
      setViec({ ma: d.ma, muc: d.muc_toi_da, v: { tt: "dang_nap", trang: d.trang } });
    } catch {
      setBaoLoi("Không gửi được file tới máy chủ trang.");
    }
  };

  const hien = ds.kho.filter((k) => hienAn || !k.an);
  const dangNap = viec && viec.v?.tt === "dang_nap";
  return (
    <div>
      <div className="phu" style={{ marginBottom: 8 }}>
        Nạp file vào kho của tổ chức này rồi hỏi bằng khối Agent bật “tìm trong tài liệu” (chọn kho ở ô “Tài liệu được tìm”). Mỗi lần nạp một file,
        tối đa {ds.trang_toi_da} trang; nạp có tốn tiền AI (giữ chỗ mức tối đa trong sổ trước khi nạp).
      </div>
      {!ds.noi_app && <div className="canh-bao">Chưa nối app tìm tài liệu (người vận hành chưa bật hộp cổng) — xem được kho cũ, chưa nạp được.</div>}
      <input type="file" accept={ds.duoi.join(",")} onChange={(e) => setTep(e.target.files?.[0] || null)} style={{ margin: "6px 0" }} />
      <label className="phu" style={{ display: "block" }}>
        <input type="checkbox" checked={tam} onChange={(e) => setTam(e.target.checked)} /> Kho tạm (hỏi ngay, tự ẩn sau 24 giờ — ẩn chứ chưa xoá)
      </label>
      <button className="nut chinh" onClick={nap} disabled={!ds.noi_app || !!dangNap}>{dangNap ? "Đang nạp…" : "Nạp vào kho"}</button>
      {baoLoi && <div className="phu" style={{ color: "var(--loi)", marginTop: 8 }}>{baoLoi}</div>}
      {viec && (
        <div className={viec.v?.tt === "loi" ? "hop loi" : "hop"} style={{ marginTop: 8 }}>
          {viec.v?.tt === "dang_nap" && <>Đang nạp{viec.v.trang ? ` (${viec.v.trang} trang)` : ""} — đã giữ chỗ tối đa {viec.muc.toFixed(4)} USD. File lớn có thể mất vài phút.</>}
          {viec.v?.tt === "xong" && <>Đã nạp xong “{viec.v.ten}” — chọn kho này ở khối Agent để hỏi.</>}
          {viec.v?.tt === "loi" && <>Chưa nạp được: {viec.v.loi}</>}
        </div>
      )}
      <h4 style={{ marginTop: 12 }}>Kho của tổ chức · {ds.kho.filter((k) => !k.an).length}</h4>
      {ds.kho.some((k) => k.an) && (
        <label className="phu"><input type="checkbox" checked={hienAn} onChange={(e) => setHienAn(e.target.checked)} /> Hiện cả kho tạm đã ẩn</label>
      )}
      {hien.length ? hien.map((k) => (
        <div key={k.sha} className="the" style={k.an ? { opacity: 0.6 } : undefined}>
          <div className="h"><b title={k.ten}>{k.ten}</b></div>
          <div className="phu">
            {k.trang ? `${k.trang} trang · ` : ""}{new Date(k.luc * 1000).toLocaleString("vi-VN")}{k.tam ? " · kho tạm" : ""}{k.ghi_chu ? ` · ${k.ghi_chu}` : ""}
          </div>
          <a className="nut nho" href={moNguon(k.sha)} target="_blank" rel="noopener noreferrer">Mở file</a>
        </div>
      )) : <div className="phu">Chưa có kho nào.</div>}
    </div>
  );
}

// Ô "Tài liệu được tìm" (rag_collections) của khối Agent: CHỌN từ kho của tổ chức thay cho gõ tên (máy chủ vẫn kiểm lại lúc chạy).
export function ChonKho({ v, dat }: { v: string; dat: (s: string) => void }) {
  const ds = useKho();
  const da = new Set(String(v || "").split("\n").map((x) => x.trim()).filter(Boolean));
  if (ds === null || ds === "khong_co") {
    return <textarea value={v} onChange={(e) => dat(e.target.value)} placeholder="mỗi dòng một tên kho" />;
  }
  const dung = ds.kho.filter((k) => !k.an);
  const la = [...da].filter((x) => !dung.some((k) => k.kho === x));
  const doi = (kho: string, bat: boolean) => { const s = new Set(da); if (bat) s.add(kho); else s.delete(kho); dat([...s].join("\n")); };
  return (
    <div>
      {dung.length ? dung.map((k) => (
        <label key={k.kho} className="phu" style={{ display: "block" }}>
          <input type="checkbox" checked={da.has(k.kho)} onChange={(e) => doi(k.kho, e.target.checked)} /> {k.ten}{k.tam ? " (tạm)" : ""}
        </label>
      )) : <div className="phu">Chưa có kho — nạp file ở tab “Tài liệu”.</div>}
      {la.length > 0 && (
        <div className="canh-bao" style={{ marginTop: 4 }}>
          {la.length} kho không thuộc tổ chức này / đã ẩn — lượt chạy sẽ bị chặn.{" "}
          <button className="nut nho" onClick={() => dat([...da].filter((x) => !la.includes(x)).join("\n"))}>Bỏ các kho này</button>
        </div>
      )}
    </div>
  );
}

// Dưới câu trả lời: "tên file · trang N" (rag_tool ghi nguồn theo khuôn này) ⇒ nút mở đúng trang, CHỈ khi tên trùng một kho của tổ chức.
export function NguonTaiLieu({ chu }: { chu: string | null }) {
  const ds = useKho();
  if (!chu || ds === null || ds === "khong_co" || !ds.kho.length) return null;
  const thay: { k: Kho; trang: number }[] = [];
  const re = /([^\n·|"“”]{1,140}?)\s*·\s*trang\s*(\d{1,4})/g;
  for (const m of chu.matchAll(re)) {
    const ten = m[1].trim().replace(/^[-*•(\[]+\s*/, "");
    const k = ds.kho.find((x) => x.ten === ten || ten.endsWith(x.ten));
    const tr = Number(m[2]);
    if (k && !thay.some((t) => t.k.sha === k.sha && t.trang === tr)) thay.push({ k, trang: tr });
    if (thay.length >= 12) break;
  }
  if (!thay.length) return null;
  return (
    <div className="phu" style={{ marginTop: 6 }}>
      Nguồn:{" "}
      {thay.map((t) => (
        <a key={t.k.sha + t.trang} className="nut nho" href={moNguon(t.k.sha, t.trang)} target="_blank" rel="noopener noreferrer" title={t.k.ten}>
          {t.k.ten.length > 30 ? t.k.ten.slice(0, 28) + "…" : t.k.ten} · tr. {t.trang}
        </a>
      ))}
    </div>
  );
}
