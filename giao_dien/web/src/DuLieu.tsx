// [C 20–26/11/2026] Trang "Dữ liệu" — hỗ trợ đủ 4 loại tải KPI kỳ trống (raw, chi_tieu, so_tracking, trang_chi_tieu .html),
// thẻ "đã nhận, còn thiếu…", khối 3 con số từ GET /ky/<kỳ>?data_version=, gửi tuần tự 1 file/lúc, và nút "Xuất bảng thưởng ra Excel".
import { useCallback, useEffect, useRef, useState } from "react";
import { banDuLieu, useBanDuLieu } from "./banDuLieu";
import { GhepCot } from "./GhepCot";

type Nhan = { loai: string; ten_loai: string; ho_tro: boolean; ky: string | null; ngay_chot: string | null; vi_sao: string; ghi_chu: string | null };
type KqTai = {
  ma?: string | null;
  data_version?: string;
  period?: string;
  cutoff?: string;
  team_total?: number;
  da_co_san?: boolean;
  refused?: string;
  loi?: string;
  viec_nen_lam?: string;
  trang_thai?: string;
  da_co?: string[];
  con_thieu?: string[];
  da_nhan?: string;
  ly_do_chua_tinh?: string;
  ban_dau_cua_ky?: boolean;
  thuong_them?: string;
  thuong_them_ly_do?: string | null;
  gia_vi?: string;
  gia_vi_ly_do?: string | null;
  chi_tieu_rieng?: string;
  tu_trang?: { so_khoan?: number; so_nguoi?: number; trang_sha256?: string } | null;
  tuyen_tu_so?: { nguon_cong_thuc?: string; tom_tat?: { canh_bao?: string[] } } | null;
  nhan_vien_moi?: { ma_che: string; ho_ten?: string }[];
  data_flags?: string[];
};
type TT = "doc" | "ai_doc" | "hieu" | "tai" | "cho_du_file" | "xong" | "ghep" | "loi" | "chua_ho_tro";
type The = { id: number; tep: File; tt: TT; nhan?: Nhan; loai: string; ky: string; ngayChot: string; sua: boolean; kq?: KqTai; loi?: string; viec?: string; moGoc?: boolean; moGhep?: boolean };
type DongThang = { khoa: string; ten: string; trang_thai: string; luc: number | null; chi_tiet: string };
type KhoanTong = { tong: number | null; trang_thai?: string | null; ly_do?: string | null } | null;
type BaTong = { tam_kpi?: { tong: number | null } | null; thuong_them?: KhoanTong; gia_vi?: KhoanTong } | null;
type Kpi = {
  nguon?: string;
  san_sang?: boolean;
  giu_qua_bat_lai?: boolean;
  ly_do_khong_hoi_duoc?: string;
  ky: string | null;
  ky_ds: string[];
  thang: DongThang[];
  viec_ke: string;
  latest_data_version?: string | null;
  data_version?: string | null;
  ba_tong?: BaTong;
  trang_thai_ky?: string | null;
  da_co?: string[];
  con_thieu?: string[];
  lich_su: { luc: number; loai: string; ky: string | null; ngay_chot: string | null; ket_qua: string; ly_do: string; tong_doi: number | null }[];
};
type TaiLieu = { sha: string; ten: string; trang: number | null; luc: number };
type Viec = { ma: string; ten: string; tt: "dang_nap" | "xong" | "loi"; loi?: string };

const TRAN = 20 * 1024 * 1024;
const TRAN_TRANG = 5 * 1024 * 1024;
const TEN_LOAI: Record<string, string> = {
  raw: "File đơn hàng",
  chi_tieu: "File chỉ tiêu nhân viên",
  so_tracking: "Sổ theo dõi (Tracking)",
  trang_chi_tieu: "Trang theo dõi chỉ tiêu (.html)",
  tracking: "Sổ theo dõi (Tracking)",
};
const TEN_KQ: Record<string, string> = {
  da_nhan: "đã nhận",
  da_co_san: "đã có sẵn",
  cho_du_file: "đã nhận, còn thiếu file",
  can_ghep_cot: "cần ghép cột",
  thieu_luat: "thiếu luật của tháng",
  lech_ky: "lệch tháng / ngày chốt",
  day_cho: "hệ thống đầy chỗ giữ file",
  tu_choi: "không nhận",
};
const NHAN_TT: Record<string, [string, string]> = {
  da_co: ["đã có", "SUCCESS"],
  thieu: ["còn thiếu", "cho"],
  chua_duoc: ["chưa được nhận", "FAILED"],
  chua_ho_tro: ["chưa hỗ trợ", "cho"],
  chua_ro: ["chưa rõ", "cho"],
  ky_khac: ["đang dùng của tháng khác", "cho"],
};
const nhanTT = (s: string): [string, string] => NHAN_TT[s] || NHAN_TT.chua_ro;
const thang = (ky?: string | null) => (ky && /^\d{4}-\d{2}$/.test(ky) ? `tháng ${Number(ky.slice(5))}/${ky.slice(0, 4)}` : "chưa rõ tháng");
const ngay = (d?: string | null) => (d && /^\d{4}-\d{2}-\d{2}/.test(d) ? `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}` : "chưa rõ ngày");
const gio = (s: number) => new Date(s * 1000).toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit", year: "numeric" });
const dong = (n?: number | null) => (typeof n === "number" ? n.toLocaleString("vi-VN") + " đồng" : "—");
const cuoiKy = (ky: string): string => {
  if (!/^\d{4}-\d{2}$/.test(ky)) return "";
  const [y, m] = ky.split("-").map(Number);
  const d = new Date(y, m, 0).getDate();
  return `${ky}-${String(d).padStart(2, "0")}`;
};

function OTha({ nhieu, nhan, onTep, chu }: { nhieu?: boolean; nhan: string; onTep: (f: File[]) => void; chu: string }) {
  const [tren, setTren] = useState(false);
  const o = useRef<HTMLInputElement>(null);
  return (
    <div className={"dl-tha" + (tren ? " tren" : "")} role="button" tabIndex={0} aria-label={chu}
      onClick={() => o.current?.click()} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); o.current?.click(); } }}
      onDragOver={(e) => { e.preventDefault(); setTren(true); }} onDragLeave={() => setTren(false)}
      onDrop={(e) => { e.preventDefault(); setTren(false); const f = Array.from(e.dataTransfer?.files || []); if (f.length) onTep(nhieu ? f : f.slice(0, 1)); }}>
      <b>{chu}</b>
      <span className="phu">hoặc bấm vào đây để chọn file từ máy</span>
      <input ref={o} type="file" hidden multiple={!!nhieu} accept={nhan} onChange={(e) => { const f = Array.from(e.target.files || []); if (f.length) onTep(f); e.target.value = ""; }} />
    </div>
  );
}

export function DuLieu({ kyDangChay, nguoiMacDinh, onHoi, onHoiTaiLieu, onVe }: { kyDangChay?: string; nguoiMacDinh?: string; onHoi: (cau: string) => void; onHoiTaiLieu: (sha: string, ten: string) => void; onVe: () => void }) {
  // ---------------- phần 1: số liệu KPI ----------------
  const [the, setThe] = useState<The[]>([]);
  const dem = useRef(0);
  const [kpi, setKpi] = useState<Kpi | null>(null);
  const kpiRef = useRef<Kpi | null>(null);
  kpiRef.current = kpi;
  const [kyXem, setKyXem] = useState<string>("");
  const [kpiLoi, setKpiLoi] = useState(false);
  const dangDung = useBanDuLieu();
  const sua = (id: number, d: Partial<The>) => setThe((ds) => ds.map((x) => (x.id === id ? { ...x, ...d } : x)));
  const napKpi = useCallback(async (ky?: string, dv?: string | null) => {
    const q = new URLSearchParams();
    if (ky) q.set("ky", ky);
    if (dv) q.set("data_version", dv);
    const qs = q.toString();
    try {
      const r = await fetch("/api/du-lieu/kpi" + (qs ? "?" + qs : ""));
      if (r.ok) { setKpi(await r.json()); setKpiLoi(false); } else setKpiLoi(true);
    } catch { setKpiLoi(true); }
  }, []);
  useEffect(() => {
    const dv = dangDung && (!kyXem || dangDung.period === kyXem) ? dangDung.dv : undefined;
    void napKpi(kyXem || undefined, dv);
  }, [kyXem, dangDung?.dv, dangDung?.period, napKpi]);

  const nhanTep = (fs: File[]) => fs.forEach(async (tep) => {
    const id = ++dem.current;
    const goc: The = { id, tep, tt: "doc", loai: "raw", ky: "", ngayChot: "", sua: false };
    setThe((ds) => [goc, ...ds]);
    const laHtml = /\.(html|htm)$/i.test(tep.name);
    if (tep.size > (laHtml ? TRAN_TRANG : TRAN)) {
      return sua(id, {
        tt: "loi",
        loi: laHtml ? "Trang theo dõi (.html) lớn hơn 5 MB." : "File lớn hơn 20 MB.",
        viec: laHtml ? "Kiểm tra lại đúng file saved_resource.html trong thư mục <tên trang>_files rồi kéo lại." : "Bạn tách file theo tháng (mỗi file một tháng) rồi kéo lại.",
      });
    }
    try {
      const r = await fetch("/api/du-lieu/nhan-loai?ten=" + encodeURIComponent(tep.name), { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: await tep.arrayBuffer() });
      const d = await r.json();
      if (!r.ok) return sua(id, { tt: "loi", loi: d.loi || `Chưa đọc được file (mã ${r.status}).`, viec: d.viec_nen_lam });
      const n = d as Nhan;
      const loai = n.ho_tro ? n.loai : "raw";
      const ky = n.ky || kyDangChay || kpiRef.current?.ky || "";
      const ngayChot = loai === "raw" ? (n.ngay_chot || cuoiKy(ky)) : "";
      sua(id, { tt: n.ho_tro || n.loai === "khong_ro" ? "hieu" : "chua_ho_tro", nhan: n, loai, ky, ngayChot, sua: n.loai === "khong_ro" });
    } catch { sua(id, { tt: "loi", loi: "Không gửi được file tới máy chủ của trang.", viec: "Kiểm tra trang còn mở được không rồi kéo lại file." }); }
  });

  // [ghi chú] gửi tuần tự 1 file/lúc (tránh kpi-backend 409 dang_ban + đọc lại GET /ky/<kỳ> sau mỗi lần tải)
  const dangTaiRef = useRef(false);
  const hangDoiRef = useRef<The[]>([]);
  const guiMotFile = async (t: The) => {
    const q = new URLSearchParams({ loai: t.loai, ky: t.ky });
    if (t.loai === "raw" && t.ngayChot) q.set("ngay_chot", t.ngayChot);
    const dvChong = (dangDung?.period === t.ky ? dangDung.dv : null) || (kpiRef.current?.ky === t.ky ? kpiRef.current?.latest_data_version : null);
    if (dvChong) q.set("data_version", dvChong);
    try {
      const r = await fetch("/api/kpi/tai-len?" + q.toString(), { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: await t.tep.arrayBuffer() });
      const d = (await r.json()) as KqTai;
      if (r.ok && d.trang_thai === "cho_du_file") sua(t.id, { tt: "cho_du_file", kq: d });
      else if (r.status === 422 && t.loai === "raw" && (d.ma === "can_ghep_cot" || d.ma === "gia_tri_chua_biet" || /ghép cột/i.test(d.refused || ""))) sua(t.id, { tt: "ghep", kq: d, viec: d.viec_nen_lam });
      else if (!r.ok || d.refused || d.loi) sua(t.id, { tt: "loi", loi: d.refused || d.loi || `Tải lên không thành công (mã ${r.status}).`, viec: d.viec_nen_lam, kq: d });
      else {
        sua(t.id, { tt: "xong", kq: d });
        if (d.data_version && dangDung?.period === t.ky) banDuLieu.dat({ dv: d.data_version, period: d.period || t.ky, cutoff: d.cutoff });
      }
    } catch { sua(t.id, { tt: "loi", loi: "Không gửi được file tới máy chủ của trang." }); }
    setKyXem(t.ky);
    await napKpi(t.ky);
  };
  const chayHangDoi = async () => {
    if (dangTaiRef.current) return;
    dangTaiRef.current = true;
    try {
      while (hangDoiRef.current.length > 0) {
        const tiep = hangDoiRef.current.shift()!;
        await guiMotFile(tiep);
      }
    } finally { dangTaiRef.current = false; }
  };
  const taiLen = (t: The) => {
    if (!/^\d{4}-\d{2}$/.test(t.ky)) return sua(t.id, { sua: true, loi: "Chưa có tháng của file — chọn tháng rồi bấm lại." });
    if (t.loai === "raw" && !t.ngayChot) return sua(t.id, { sua: true, loi: "File đơn hàng cần ngày chốt số liệu — chọn ngày rồi bấm lại." });
    sua(t.id, { tt: "tai", loi: undefined, viec: undefined });
    hangDoiRef.current.push(t);
    void chayHangDoi();
  };

  // ---------------- phần 2: tài liệu hỏi đáp ----------------
  const [tl, setTl] = useState<TaiLieu[] | null>(null);
  const [tim, setTim] = useState("");
  const [viec, setViec] = useState<Viec[]>([]);
  const [loiTl, setLoiTl] = useState("");
  const napTl = useCallback(async () => { try { const r = await fetch("/api/du-lieu/tai-lieu"); if (r.ok) setTl((await r.json()).tai_lieu); } catch { /* giữ */ } }, []);
  useEffect(() => { void napTl(); }, [napTl]);
  useEffect(() => {
    if (!viec.some((v) => v.tt === "dang_nap")) return;
    const h = setInterval(async () => {
      for (const v of viec.filter((x) => x.tt === "dang_nap")) {
        try {
          const r = await fetch("/api/tai-lieu/viec?ma=" + encodeURIComponent(v.ma)); const d = await r.json();
          if (d.tt && d.tt !== "dang_nap") { setViec((ds) => ds.map((x) => (x.ma === v.ma ? { ...x, tt: d.tt, loi: d.loi } : x))); if (d.tt === "xong") void napTl(); }
        } catch { /* thử lại nhịp sau */ }
      }
    }, 4000);
    return () => clearInterval(h);
  }, [viec, napTl]);
  const napTaiLieu = async (fs: File[]) => {
    setLoiTl("");
    const tep = fs[0];
    if (tep.size > TRAN) return setLoiTl("File lớn hơn 20 MB — hãy tách nhỏ rồi đưa vào lại.");
    try {
      const r = await fetch("/api/tai-lieu/nap?ten=" + encodeURIComponent(tep.name), { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: await tep.arrayBuffer() });
      const d = await r.json();
      if (!r.ok || !d.ma) return setLoiTl(d.loi || `Chưa nhận được tài liệu (mã ${r.status}).`);
      setViec((ds) => [{ ma: d.ma, ten: tep.name, tt: "dang_nap" }, ...ds]);
    } catch { setLoiTl("Không gửi được file tới máy chủ của trang."); }
  };
  const an = async (sha: string) => { try { await fetch("/api/du-lieu/tai-lieu/an", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sha }) }); } catch { /* bỏ qua */ } void napTl(); };
  const loc = (tl || []).filter((x) => !tim.trim() || (x.ten || "").toLowerCase().includes(tim.trim().toLowerCase()));
  const ba = kpi?.ba_tong;
  const nvMoiHop = (ds?: { ma_che: string; ho_ten?: string }[]) =>
    ds && ds.length > 0 ? (
      <div className="canh-bao" style={{ marginTop: 6 }}>
        Đã tự cấp mã cho nhân viên mới: <b>{ds.map((nv) => (nv.ho_ten ? `${nv.ma_che} (${nv.ho_ten})` : nv.ma_che)).join(", ")}</b>
      </div>
    ) : null;

  return (
    <div className="dl-trang">
      <div className="dl-dau">
        <h1 className="serif">Dữ liệu</h1>
        <span className="phu">Nơi đưa số liệu và tài liệu vào hệ thống. Không cần biết kỹ thuật: kéo file vào, xem hệ thống hiểu thế nào, rồi hỏi trợ lý.</span>
        <span className="cach" style={{ flex: 1 }} />
        <button className="nut" onClick={onVe}>← Về xưởng workflow</button>
      </div>

      {/* ============ PHẦN 1 ============ */}
      <section className="dl-phan">
        <h2 className="serif">1 · Số liệu để tính thưởng KPI theo tháng</h2>
        <p className="dl-mo">Tải các file số liệu của tháng lên đây để hệ thống tính thưởng cho từng nhân viên.</p>
        <ol className="dl-buoc">
          <li><b>Kéo file vào ô bên dưới</b>: file đơn hàng (<code>.xlsx</code>), chỉ tiêu nhân viên (<code>.xlsx</code>), sổ theo dõi (<code>.xlsx</code>), rồi trang theo dõi chỉ tiêu (<code>saved_resource.html</code> trong thư mục <code>&lt;tên trang&gt;_files</code>).</li>
          <li><b>Xem hệ thống hiểu file là gì</b> rồi bấm “Xác nhận và tải lên” (hệ thống tự gửi tuần tự từng file một).</li>
          <li><b>Xem ngay 3 tổng</b> của tháng và <b>hỏi trợ lý</b>, ví dụ: <button className="lk" onClick={() => onHoi("Tháng này cả đội được bao nhiêu tiền thưởng?")}>“Tháng này cả đội được bao nhiêu tiền thưởng?”</button></li>
        </ol>
        <OTha nhieu nhan=".xlsx,.html,.htm,.pdf" onTep={nhanTep} chu="Kéo file số liệu của tháng (.xlsx, .html) vào đây" />
        <div className="phu" style={{ marginTop: 6 }}>
          Nhận 4 loại: file đơn hàng (<code>.xlsx</code>), file chỉ tiêu nhân viên (<code>.xlsx</code>), sổ theo dõi (Tracking, <code>.xlsx</code> — nếu đang là <code>.xlsb</code> hãy mở bằng Excel rồi “Lưu thành” <code>.xlsx</code>), và trang theo dõi chỉ tiêu (<code>.html</code> ≤ 5 MB — chọn file <code>saved_resource.html</code> trong thư mục <code>&lt;tên trang&gt;_files</code>).
        </div>
        {!kpi?.giu_qua_bat_lai && <div className="phu" style={{ marginTop: 4 }}><b>Lưu ý:</b> file tải lên ở đây chưa được giữ qua lần hệ thống bật lại — sau khi hệ thống được bật lại, bạn cần tải lại file của tháng.</div>}

        {the.map((t) => (
          <div key={t.id} className="dl-the">
            <div className="dl-the-dau"><b>{t.tep.name}</b>
              <span className={"chip " + (t.tt === "xong" ? "SUCCESS" : t.tt === "loi" ? "FAILED" : t.tt === "doc" || t.tt === "tai" || t.tt === "ai_doc" ? "chay" : "cho")}>
                {{ doc: "đang đọc file", ai_doc: "đang nhờ AI đọc file", hieu: "chờ bạn xác nhận", tai: "đang tải lên và tính thử", cho_du_file: "đã nhận, còn thiếu…", xong: "đã nhận", ghep: "cần ghép cột", loi: "chưa được nhận", chua_ho_tro: "chưa hỗ trợ" }[t.tt]}</span>
              <span className="cach" style={{ flex: 1 }} /><button className="nut nho" onClick={() => setThe((ds) => ds.filter((x) => x.id !== t.id))} title="Bỏ thẻ này khỏi màn hình (không xoá gì trên hệ thống)">Bỏ khỏi danh sách</button>
            </div>
            {t.tt === "doc" && <div className="phu">Hệ thống đang mở file để xem đây là loại file gì…</div>}
            {t.tt === "chua_ho_tro" && <div className="canh-bao">{t.nhan?.ghi_chu} <span className="phu">(Vì sao nhận ra: {t.nhan?.vi_sao}.)</span></div>}
            {(t.tt === "hieu" || t.tt === "tai") && <>
              <div className="dl-hieu">{t.nhan?.loai === "khong_ro" && !t.sua
                ? <>Hệ thống <b>chưa nhận ra</b> đây là loại file gì — bấm “Sửa” để chọn giúp.</>
                : <>Hệ thống hiểu đây là: <b>{TEN_LOAI[t.loai] || t.nhan?.ten_loai}</b> · <b>{thang(t.ky)}</b>{t.loai === "raw" && <> · số liệu tới ngày <b>{ngay(t.ngayChot)}</b></>}</>}
                {" "}<button className="lk" onClick={() => sua(t.id, { sua: !t.sua })} disabled={t.tt === "tai"}>{t.sua ? "Xong sửa" : "Sửa"}</button></div>
              {t.nhan?.vi_sao && <div className="phu">Vì sao hệ thống hiểu vậy: {t.nhan.vi_sao}.</div>}
              {t.sua && <div className="dl-sua">
                <label>Loại file <select value={t.loai} onChange={(e) => {
                  const nl = e.target.value;
                  sua(t.id, { loai: nl, ngayChot: nl === "raw" ? (t.ngayChot || cuoiKy(t.ky)) : "" });
                }}>
                  <option value="raw">File đơn hàng (.xlsx)</option>
                  <option value="chi_tieu">File chỉ tiêu nhân viên (.xlsx)</option>
                  <option value="so_tracking">Sổ theo dõi (.xlsx)</option>
                  <option value="trang_chi_tieu">Trang theo dõi chỉ tiêu (.html)</option>
                </select></label>
                <label>Tháng <input type="month" value={t.ky} onChange={(e) => {
                  const nk = e.target.value;
                  sua(t.id, { ky: nk, ngayChot: t.loai === "raw" ? cuoiKy(nk) : t.ngayChot });
                }} /></label>
                {t.loai === "raw" && <label>Số liệu tới ngày <input type="date" value={t.ngayChot} onChange={(e) => sua(t.id, { ngayChot: e.target.value })} /></label>}
              </div>}
              {t.loi && <div className="phu" style={{ color: "var(--loi)" }}>{t.loi}</div>}
              <div style={{ marginTop: 8 }}><button className="nut chinh" disabled={t.tt === "tai"} onClick={() => taiLen(t)}>{t.tt === "tai" ? "Đang tải lên…" : "Xác nhận và tải lên"}</button>
                <span className="phu" style={{ marginLeft: 8 }}>Chưa bấm thì chưa có gì được gửi đi tính thưởng.</span></div>
            </>}
            {t.tt === "cho_du_file" && t.kq && <>
              <div className="dl-hieu">{t.kq.loi || `Đã nhận ${TEN_LOAI[t.kq.da_nhan || t.loai] || t.loai}, đang chờ đủ file để tính.`}</div>
              <div className="phu">
                {t.kq.da_co?.length ? <>Đã có: <b>{t.kq.da_co.map((k) => TEN_LOAI[k] || k).join(", ")}</b></> : null}
                {t.kq.con_thieu?.length ? <>{t.kq.da_co?.length ? " · " : ""}Còn thiếu: <b>{t.kq.con_thieu.map((k) => TEN_LOAI[k] || k).join(", ")}</b></> : null}
              </div>
              {t.kq.ly_do_chua_tinh && <div className="canh-bao" style={{ marginTop: 6 }}>{t.kq.ly_do_chua_tinh}</div>}
              {nvMoiHop(t.kq.nhan_vien_moi)}
            </>}
            {t.tt === "xong" && t.kq && <>
              <div className="dl-hieu">{t.kq.da_co_san ? "Hệ thống đã có sẵn đúng file này." : t.kq.ban_dau_cua_ky ? "Đã nhận đủ 3 file và tính xong bản đầu của tháng." : "Đã nhận."} {thang(t.kq.period)} · số liệu tới ngày {ngay(t.kq.cutoff)}.</div>
              <div>Tổng lương kinh doanh cả đội (8 KPI): <b>{dong(t.kq.team_total)}</b> <span className="nhan-tam">tạm tính, chưa duyệt</span>{" "}
                <span className="phu">
                  {t.kq.gia_vi === "co" ? "— đã tính sẵn 1 % gia vị." : t.kq.gia_vi_ly_do ? `— ${t.kq.gia_vi_ly_do}` : ""}
                  {t.kq.thuong_them === "co" ? " Đã có thưởng thêm từ trang theo dõi." : t.kq.thuong_them_ly_do ? ` ${t.kq.thuong_them_ly_do}` : ""}
                </span>
              </div>
              {t.kq.tu_trang && <div className="phu">Trang theo dõi: đã đọc {t.kq.tu_trang.so_khoan ?? "?"} khoản · {t.kq.tu_trang.so_nguoi ?? "?"} nhân viên.</div>}
              {nvMoiHop(t.kq.nhan_vien_moi)}
              {t.kq.data_flags && t.kq.data_flags.length > 0 && <ul className="phu" style={{ margin: "6px 0 0 18px" }}>{t.kq.data_flags.map((fl, idx) => <li key={idx}>{fl}</li>)}</ul>}
              {t.kq.data_version && (dangDung?.dv === t.kq.data_version
                ? <div className="canh-bao" style={{ marginTop: 8 }}>Trợ lý đang trả lời trên số liệu này. <button className="nut nho" onClick={() => banDuLieu.dat(null)}>Thôi dùng</button>{" "}
                    <button className="nut chinh" onClick={() => onHoi(`Tổng lương kinh doanh cả đội ${thang(t.kq!.period)} là bao nhiêu?`)}>Hỏi trợ lý về tháng này</button></div>
                : <div style={{ marginTop: 8 }}><button className="nut chinh" onClick={() => banDuLieu.dat({ dv: t.kq!.data_version!, period: t.kq!.period, cutoff: t.kq!.cutoff })}>Dùng số liệu này để hỏi</button>
                    <span className="phu" style={{ marginLeft: 8 }}>Chưa bấm thì trợ lý vẫn trả lời trên số liệu đang chạy.</span></div>)}
            </>}
            {t.tt === "ghep" && <>
              <div className="canh-bao">{t.viec || "Tên cột trong file khác mẫu hệ thống đã biết. Ghép cột một lần là dùng được."}{" "}
                {!t.moGhep && <button className="nut chinh" onClick={() => sua(t.id, { moGhep: true })}>Ghép cột</button>}</div>
              {t.moGhep && <GhepCot tep={t.tep} ky={t.ky} ngayChot={t.ngayChot}
                dataVersion={(dangDung?.period === t.ky ? dangDung.dv : null) || (kpiRef.current?.ky === t.ky ? kpiRef.current?.latest_data_version : null)}
                kyTrong={kpi?.trang_thai_ky === "cho_du_file"}
                nguoiMacDinh={nguoiMacDinh} onDong={() => sua(t.id, { moGhep: false })}
                onLuu={(kq) => {
                  sua(t.id, { moGhep: false });
                  if (kq && (kq.trang_thai === "cho_du_file" || kq.data_version)) {
                    const kqTai = kq as KqTai;
                    sua(t.id, { tt: kqTai.trang_thai === "cho_du_file" ? "cho_du_file" : "xong", kq: kqTai });
                    if (kqTai.data_version && dangDung?.period === t.ky) banDuLieu.dat({ dv: kqTai.data_version, period: kqTai.period || t.ky, cutoff: kqTai.cutoff });
                    setKyXem(t.ky);
                    void napKpi(t.ky);
                  } else {
                    void taiLen(t);
                  }
                }} />}
            </>}
            {t.tt === "loi" && <>
              <div className="canh-bao"><b>Việc nên làm:</b> {t.viec || "Xem lý do bên dưới rồi thử lại."}</div>
              <button className="lk" onClick={() => sua(t.id, { moGoc: !t.moGoc })}>{t.moGoc ? "Ẩn" : "Xem"} nguyên văn lý do của hệ thống</button>
              {t.moGoc && <div className="hop loi" style={{ marginTop: 4 }}>{t.loi}</div>}
              {t.nhan && t.kq?.ma !== "luat_neu_khong_co_dong" && !/đầy chỗ giữ file/.test(t.viec || "") && <div style={{ marginTop: 8 }}><button className="nut" onClick={() => sua(t.id, { tt: "hieu", sua: true, loi: undefined })}>Sửa tháng / ngày rồi tải lại</button></div>}
            </>}
          </div>
        ))}

        <div className="dl-bang">
          <div className="dl-bang-dau"><b className="serif">{kpi?.ky ? `${thang(kpi.ky).replace(/^t/, "T")} đã có gì` : "Tháng này đã có gì"}</b>
            {kpi && kpi.ky_ds.length > 0 && <label className="phu">Xem tháng khác <select value={kpi.ky || ""} onChange={(e) => setKyXem(e.target.value)}>{kpi.ky_ds.map((k) => <option key={k} value={k}>{thang(k)}</option>)}</select></label>}
          </div>
          {ba && (
            <div className="dl-ba-tong">
              <div><span>Lương kinh doanh cả đội (8 KPI)</span><b className="so">{ba.tam_kpi?.tong != null ? dong(ba.tam_kpi.tong) : "Chưa có"}</b></div>
              <div><span>Thưởng thêm (Kokomi / Meat)</span><b className="so">{ba.thuong_them?.tong != null ? dong(ba.thuong_them.tong) : "Chưa có"}</b>
                {ba.thuong_them?.tong == null && ba.thuong_them?.ly_do && <small className="phu">{ba.thuong_them.ly_do}</small>}</div>
              <div><span>1 % Gia vị</span><b className="so">{ba.gia_vi?.tong != null ? dong(ba.gia_vi.tong) : "Chưa có"}</b>
                {ba.gia_vi?.tong == null && ba.gia_vi?.ly_do && <small className="phu">{ba.gia_vi.ly_do}</small>}</div>
            </div>
          )}
          {kpi ? <table className="bang"><tbody>{kpi.thang.map((d) => (
            <tr key={d.khoa}><th style={{ width: 220 }}>{d.ten}</th><td style={{ width: 150 }}><span className={"chip " + nhanTT(d.trang_thai)[1]}>{nhanTT(d.trang_thai)[0]}</span></td>
              <td>{d.luc ? <span className="phu">tải lúc {gio(d.luc)} · </span> : null}{d.chi_tiet}</td></tr>))}</tbody></table>
            : kpiLoi ? <div className="canh-bao">Chưa hỏi được máy chủ của trang. <button className="lk" onClick={() => void napKpi(kyXem || undefined)}>Thử lại</button></div>
            : <div className="phu">Đang hỏi máy chủ…</div>}
          {kpi && <div className="dl-viec"><b>Việc tiếp theo:</b> {kpi.viec_ke}{" "}
            {(kpi.san_sang ?? kpi.thang[0]?.trang_thai === "da_co") && <>
              <button className="nut chinh" onClick={() => onHoi(`Tổng lương kinh doanh cả đội ${thang(kpi.ky)} là bao nhiêu?`)}>Hỏi trợ lý về tháng này</button>{" "}
              <button className="nut" onClick={() => onHoi(`Xuất bảng thưởng cả đội ${thang(kpi.ky)} ra Excel`)}>Xuất bảng thưởng ra Excel</button>
            </>}
          </div>}
          <div className="phu" style={{ fontSize: 12, marginTop: 6 }}>{kpi?.nguon === "loi"
            ? "Bảng này do hệ thống tính thưởng trả lời trực tiếp (gồm cả file được nạp sẵn lúc hệ thống bật)."
            : "Chưa hỏi được hệ thống tính thưởng" + (kpi?.ly_do_khong_hoi_duoc ? ` (${kpi.ly_do_khong_hoi_duoc})` : "") + " — bảng này tạm lấy từ các lần tải lên mà trang ghi lại; file được nạp bằng cách khác sẽ không hiện."}</div>
        </div>
        {kpi && kpi.lich_su.length > 0 && <details className="dl-ls"><summary>Các lần tải gần đây ({kpi.lich_su.length})</summary>
          <table className="bang"><tbody>{kpi.lich_su.map((x, i) => <tr key={i}><td style={{ width: 150 }}>{gio(x.luc)}</td><td style={{ width: 190 }}>{TEN_LOAI[x.loai] || x.loai}</td><td style={{ width: 120 }}>{thang(x.ky)}</td>
            <td><span className={"chip " + (x.ket_qua.startsWith("da_") || x.ket_qua === "cho_du_file" ? "SUCCESS" : "FAILED")}>{TEN_KQ[x.ket_qua] || x.ket_qua}</span>{x.tong_doi != null && <span className="phu"> · cả đội {dong(x.tong_doi)} (tạm tính)</span>}</td></tr>)}</tbody></table></details>}
      </section>

      {/* ============ PHẦN 2 ============ */}
      <section className="dl-phan">
        <h2 className="serif">2 · Tài liệu để hỏi đáp</h2>
        <p className="dl-mo">Đưa tài liệu (PDF, Word, văn bản) vào đây để hỏi trợ lý về nội dung của nó — quy định, hợp đồng, báo cáo…</p>
        <ol className="dl-buoc">
          <li><b>Kéo tài liệu vào ô bên dưới</b> (mỗi lần một file).</li>
          <li><b>Chờ hệ thống đọc xong</b> — tài liệu dài hoặc bản chụp có thể mất vài phút.</li>
          <li><b>Hỏi trợ lý</b> về tài liệu đó, ví dụ: <button className="lk" onClick={() => onHoi("Tài liệu này quy định thời hạn thanh toán thế nào?")}>“Tài liệu này quy định thời hạn thanh toán thế nào?”</button></li>
        </ol>
        <OTha nhan=".pdf,.docx,.txt,.md" onTep={napTaiLieu} chu="Kéo tài liệu vào đây" />
        <div className="phu" style={{ marginTop: 6 }}>Đọc tài liệu có tốn một khoản nhỏ tiền AI; hệ thống tự giới hạn theo ngày. Muốn hỏi nhanh một file mà không lưu lại: kéo thẳng file vào khung Trợ lý ở góc dưới bên phải.</div>
        {loiTl && <div className="canh-bao" style={{ marginTop: 8 }}>{loiTl}</div>}
        {viec.map((v) => <div key={v.ma} className="dl-the"><div className="dl-the-dau"><b>{v.ten}</b>
          <span className={"chip " + (v.tt === "xong" ? "SUCCESS" : v.tt === "loi" ? "FAILED" : "chay")}>{v.tt === "xong" ? "đã đọc xong" : v.tt === "loi" ? "chưa đọc được" : "đang đọc…"}</span></div>
          {v.tt === "loi" && <div className="phu" style={{ color: "var(--loi)" }}>{v.loi || "Không rõ lý do."}</div>}</div>)}

        <div className="dl-bang">
          <div className="dl-bang-dau"><b className="serif">Tài liệu đã đưa vào ({tl ? tl.length : "…"})</b>
            <input className="tim" style={{ maxWidth: 280, margin: 0 }} value={tim} onChange={(e) => setTim(e.target.value)} placeholder="Tìm theo tên tài liệu…" aria-label="Tìm tài liệu" /></div>
          {tl === null ? <div className="phu">Đang hỏi máy chủ…</div>
            : loc.length === 0 ? <div className="phu">{tl.length ? "Không có tài liệu nào khớp ô tìm." : "Chưa có tài liệu nào. Kéo một file PDF hoặc Word vào ô phía trên để bắt đầu."}</div>
            : <table className="bang"><thead><tr><th>Tên</th><th style={{ width: 90 }}>Số trang</th><th style={{ width: 150 }}>Ngày đưa vào</th><th style={{ width: 120 }}>Trạng thái</th><th style={{ width: 250 }} /></tr></thead>
                <tbody>{loc.map((x) => <tr key={x.sha}><td>{x.ten}</td><td>{x.trang ?? "—"}</td><td>{gio(x.luc)}</td><td><span className="chip SUCCESS">đã đọc xong</span></td>
                  <td><button className="nut nho chinh" onClick={() => onHoiTaiLieu(x.sha, x.ten)} title="Mở khung trợ lý, gắn sẵn tài liệu này để hỏi — không phải đọc lại tài liệu">Hỏi trên tài liệu này</button>{" "}
                    <button className="nut nho" onClick={() => an(x.sha)} title="Ẩn khỏi danh sách này (không xoá tài liệu)">Ẩn</button></td></tr>)}</tbody></table>}
        </div>
      </section>
    </div>
  );
}
