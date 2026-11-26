// [ghi chú] MÀN GHÉP CỘT của tab "Dữ liệu KPI": file RAW có bố cục khác mẫu đã biết ⇒ người chỉ cho máy "trường chuẩn nào nằm ở cột nào"
// MỘT lần; lõi KPI (mekongai) ghép thử, kiểm nghĩa, hỏi một câu kiểm rồi mới lưu cách ghép. Sau đó file cùng bố cục tải lên như thường.
// Đường đi: trang ⇒ /api/kpi/ghep/{khuon,bo-cuc,thu,xac-nhan} (máy chủ trang, ghep_kpi.py) ⇒ hộp 18402 ⇒ kpi-backend.
// Luật của màn: (1) KHÔNG có AI — 0 đồng; (2) trang KHÔNG đọc file, KHÔNG có đáp án câu kiểm — mọi số / lời chặn / cảnh báo là chữ do code lõi viết,
// hiện NGUYÊN VĂN, không nuốt; (3) tên cột lạ đã bị lõi che (chỉ còn vị trí + độ dài) ⇒ người mở file gốc để đối chiếu; (4) đổi một ô chọn sau khi
// ghép thử ⇒ kết quả thử + câu kiểm cũ bỏ (mã câu kiểm chỉ đúng cho cách ghép đã thử); (5) cho qua giá trị mới ⇒ bắt buộc ghi lý do.
// Giới hạn: chỉ bảng đơn hàng (RAW); chưa ghép GIÁ TRỊ (kênh / cờ ghi khác chữ chuẩn ⇒ lõi chặn, sửa file rồi tải lại).
import { useEffect, useState } from "react";

type Truong = { field: string; meaning: string; kind: string; required: boolean; default_column: string; used_for: string };
type Cot = { position: number; name?: string; masked?: boolean; length?: number };
type BoCuc = { sha_file: string; sheet: string | null; sheet_masked?: boolean; sheet_number: number; header_row: number; columns: Cot[]; known?: boolean; missing_required?: string[] };
type TomTat = { vi_tri: number; kieu: string; ty_le_trong: number; so_gia_tri_khac: number | string; mau: string[] };
type KetQuaThu = {
  ok?: boolean; chan?: string; doi_soat?: unknown; canh_bao?: string[]; tom_tat_cot?: TomTat[];
  gia_tri_moi?: { truong: string; ma: string; so_dong: number }[]; cau_kiem?: { ma: string; cau: string } | null;
};

const KIEU: Record<string, string> = { so: "số", ngay: "ngày", chu: "chữ", trong: "trống" };
const chuCot = (n: number) => { let s = ""; for (let k = n; k > 0; k = Math.floor((k - 1) / 26)) s = String.fromCharCode(65 + ((k - 1) % 26)) + s; return s; };
const tenCot = (c: Cot) => `${chuCot(c.position)} (cột ${c.position}) · ${c.name ?? `tên lạ, ${c.length ?? "?"} ký tự`}`;

async function goi(duong: string, init?: RequestInit): Promise<{ ma: number; d: Record<string, unknown> }> {
  try {
    const r = await fetch(duong, init);
    let d: Record<string, unknown> = {};
    try { d = await r.json(); } catch { /* thân không phải JSON */ }
    return { ma: r.status, d };
  } catch {
    return { ma: 0, d: { refused: "Không gửi được tới máy chủ trang." } };
  }
}
const loiCua = (x: { ma: number; d: Record<string, unknown> }) =>
  x.ma === 200 ? "" : String(x.d.refused || x.d.loi || `Không thành công (mã ${x.ma}).`);

// đối soát của lõi là một cây số liệu nhỏ ⇒ hiện từng dòng "khoá: giá trị", không tự tính / diễn giải
// nhãn lời thường cho các khoá đối soát đã thấy ở lõi 1e7202d4 (đo 18/11 trên RAW T10); khoá chưa có trong bảng ⇒ hiện NGUYÊN tên khoá, không giấu
const NHAN_DOI_SOAT: Record<string, string> = {
  input_lines: "Số dòng đơn đọc được từ file", input_sales: "Tổng doanh số trước chiết khấu của các dòng đó (đồng)",
  counted_lines: "Số dòng được đưa vào tính", counted_sales: "Tổng doanh số của các dòng được tính (đồng)",
  excluded_pct: "Phần doanh số bị loại (%)", max_excluded_sales_pct: "Mức loại tối đa cho phép (%)",
  file_total_sales: "Tổng doanh số ghi ở dòng Tổng của file (đồng)", file_total_matches: "Khớp với dòng Tổng của file", ok: "Đối soát đạt",
};
function dongDoiSoat(v: unknown, dau = ""): string[] {
  if (v === null || v === undefined) return [];
  if (typeof v !== "object") return [`${dau}: ${typeof v === "number" ? v.toLocaleString("vi-VN") : typeof v === "boolean" ? (v ? "có" : "KHÔNG") : String(v)}`];
  if (Array.isArray(v)) return v.flatMap((x, i) => dongDoiSoat(x, `${dau}[${i + 1}]`));
  return Object.entries(v as Record<string, unknown>).flatMap(([k, x]) => dongDoiSoat(x, dau ? `${dau} › ${NHAN_DOI_SOAT[k] ?? k}` : (NHAN_DOI_SOAT[k] ?? k)));
}

export function GhepCot({ tep, ky, ngayChot, dataVersion, kyTrong, nguoiMacDinh, onLuu, onDong }: {
  tep: File; ky: string; ngayChot: string; dataVersion?: string | null; kyTrong?: boolean; nguoiMacDinh?: string;
  onLuu: (kq?: Record<string, unknown>) => void; onDong: () => void;
}) {
  const [truong, setTruong] = useState<Truong[]>([]);
  const [bc, setBc] = useState<BoCuc | null>(null);
  const [chon, setChon] = useState<Record<string, number>>({});
  const [sheetSo, setSheetSo] = useState(""); const [dongTd, setDongTd] = useState("");
  const [canChiDong, setCanChiDong] = useState(false);
  const [khongSauCk, setKhongSauCk] = useState(false);
  const [thu, setThu] = useState<KetQuaThu | null>(null);
  const [traLoi, setTraLoi] = useState(""); const [nguoi, setNguoi] = useState(() => nguoiMacDinh || ""); const [ghiChu, setGhiChu] = useState("");
  const [choQua, setChoQua] = useState<Record<string, boolean>>({});
  const [loi, setLoi] = useState(""); const [dang, setDang] = useState("");

  const docBoCuc = async (tay: boolean) => {
    setLoi(""); setThu(null); setDang("Đang đọc bố cục file…");
    const q = tay ? `?sheet_so=${encodeURIComponent(sheetSo)}&dong_tieu_de=${encodeURIComponent(dongTd)}` : "";
    const x = await goi("/api/kpi/ghep/bo-cuc" + q, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: await tep.arrayBuffer() });
    setDang("");
    if (x.ma !== 200) { setLoi(loiCua(x)); setCanChiDong(true); return null; }
    const b = x.d as unknown as BoCuc;
    setBc(b); setCanChiDong(false);
    return b;
  };

  useEffect(() => {
    let con = true;
    (async () => {
      const k = await goi("/api/kpi/ghep/khuon");
      if (!con) return;
      if (k.ma !== 200) return setLoi(loiCua(k));
      const ds = ((k.d.fields as Truong[]) || []);
      setTruong(ds);
      const b = await docBoCuc(false);
      if (!con || !b) return;
      // điền sẵn: cột có TÊN đúng bằng tên mặc định của trường (lõi chỉ trả tên với cột đã biết) — người vẫn phải nhìn lại
      const san: Record<string, number> = {};
      for (const t of ds) { const c = b.columns.find((y) => y.name && y.name === t.default_column); if (c) san[t.field] = c.position; }
      setChon(san);
    })();
    return () => { con = false; };
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const datCot = (f: string, v: number) => { setChon((c) => { const m = { ...c }; if (v) m[f] = v; else delete m[f]; return m; }); setThu(null); };
  const thieu = truong.filter((t) => t.required && !chon[t.field]);
  const trung = Object.values(chon).length !== new Set(Object.values(chon)).size;
  const tomTat = (viTri: number) => thu?.tom_tat_cot?.find((t) => t.vi_tri === viTri);

  const ghepThu = async () => {
    if (!bc) return;
    setLoi(""); setThu(null); setTraLoi(""); setChoQua({}); setDang("Đang ghép thử (chưa tính KPI, chưa lưu)…");
    const x = await goi("/api/kpi/ghep/thu", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sha_file: bc.sha_file, ghep: { sheet: bc.sheet_number, header_row: bc.header_row, columns: chon }, ky, ngay_chot: ngayChot,
                             ...(dataVersion ? { data_version: dataVersion } : {}),
                             ...(khongSauCk ? { khong_co_cot_sau_ck: true } : {}) }),
    });
    setDang("");
    if (x.ma !== 200) return setLoi(loiCua(x));
    setThu(x.d as KetQuaThu);
  };

  const maChoQua = (thu?.gia_tri_moi || []).map((g) => g.ma);
  const daTick = maChoQua.filter((m) => choQua[m]);
  const xacNhanDuoc = !!thu?.cau_kiem && /^\d+$/.test(traLoi) && nguoi.trim() !== "" && daTick.length === maChoQua.length
    && !((maChoQua.length > 0 || khongSauCk) && !ghiChu.trim());

  const xacNhan = async () => {
    if (!thu?.cau_kiem) return;
    setLoi(""); setDang("Đang gửi câu trả lời…");
    const x = await goi("/api/kpi/ghep/xac-nhan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ma: thu.cau_kiem.ma, tra_loi: Number(traLoi), nguoi: nguoi.trim(), ...(ghiChu.trim() ? { ghi_chu: ghiChu } : {}),
                             ...(daTick.length ? { cho_qua: daTick } : {}) }),
    });
    setDang("");
    if (x.ma === 200) return onLuu(x.d);
    setLoi(loiCua(x));
    if (x.ma === 409) setThu(null);   // lõi đã bỏ câu kiểm (mỗi câu trả lời MỘT lần) ⇒ phải ghép thử lại
  };

  return (
    <div className="the ghep-cot" style={{ marginTop: 8, cursor: "default" }} aria-label="Màn ghép cột">
      <div className="h"><b>Ghép cột cho file này</b>{" "}<button className="nut" onClick={onDong}>Đóng</button></div>
      <div className="phu">File có bố cục khác mẫu đã biết. Chỉ cho máy mỗi thông tin nằm ở cột nào — làm một lần, lần sau file cùng bố cục tải lên như thường.
        Máy chỉ ghép thử, chưa tính thưởng và chưa lưu gì cho tới khi bạn trả lời đúng câu kiểm. Không có AI ở bước này.</div>
      {kyTrong && <div className="canh-bao" style={{ marginTop: 4 }}>Kỳ {ky} đang chờ đủ file: sau khi xác nhận ghép cột, hệ thống sẽ giữ file đơn hàng và báo tiếp các file còn thiếu (nếu có).</div>}
      {bc && <div className="phu so">Sheet {bc.sheet ?? `thứ ${bc.sheet_number} (tên lạ — đã che)`} · dòng tiêu đề {bc.header_row} · {bc.columns.length} cột · kỳ {ky} · ngày chốt {ngayChot}</div>}
      {canChiDong && (
        <div className="canh-bao">
          Máy chưa tìm được dòng tiêu đề. Mở file, xem tên cột nằm ở sheet thứ mấy, dòng thứ mấy rồi điền:
          <div><input className="tim" style={{ width: 70 }} inputMode="numeric" placeholder="sheet" aria-label="Sheet thứ mấy" value={sheetSo} onChange={(e) => setSheetSo(e.target.value.trim())} />{" "}
            <input className="tim" style={{ width: 70 }} inputMode="numeric" placeholder="dòng" aria-label="Dòng tiêu đề thứ mấy" value={dongTd} onChange={(e) => setDongTd(e.target.value.trim())} />{" "}
            <button className="nut" disabled={!/^\d{1,3}$/.test(sheetSo) || !/^\d{1,3}$/.test(dongTd) || !!dang} onClick={() => docBoCuc(true)}>Đọc lại</button></div>
        </div>
      )}
      {bc && truong.map((t) => {
        const tt = chon[t.field] ? tomTat(chon[t.field]) : undefined;
        return (
          <div key={t.field} style={{ marginTop: 6 }}>
            <label className="phu" htmlFor={`ghep-${t.field}`}><b>{t.meaning}</b>{t.required ? " · bắt buộc" : ` · không bắt buộc${t.used_for ? ` (dùng cho: ${t.used_for})` : ""}`}</label>
            <select id={`ghep-${t.field}`} value={chon[t.field] || 0} onChange={(e) => datCot(t.field, Number(e.target.value))}
                    style={{ width: "100%", borderColor: t.required && !chon[t.field] ? "var(--loi)" : undefined }}>
              <option value={0}>— chưa chọn —</option>
              {bc.columns.map((c) => <option key={c.position} value={c.position}>{tenCot(c)}</option>)}
            </select>
            {tt && <div className="phu so">Cột này: phần lớn là {KIEU[tt.kieu] ?? tt.kieu} · {tt.ty_le_trong}% ô trống · {String(tt.so_gia_tri_khac)} giá trị khác nhau{tt.mau.length ? ` · ví dụ: ${tt.mau.join(", ")}` : ""}</div>}
          </div>
        );
      })}
      {bc && (
        <div style={{ marginTop: 8 }}>
          {!chon.sales_after_discount && (
            <label className="phu" style={{ display: "block" }}>
              <input type="checkbox" checked={khongSauCk} onChange={(e) => { setKhongSauCk(e.target.checked); setThu(null); }} />{" "}
              File thật sự không có cột doanh số sau chiết khấu (phải ghi lý do khi xác nhận)
            </label>
          )}
          {thieu.length > 0 && <div className="phu" style={{ color: "var(--loi)" }}>Còn {thieu.length} thông tin bắt buộc chưa chọn cột.</div>}
          {trung && <div className="phu" style={{ color: "var(--loi)" }}>Có hai thông tin đang chọn cùng một cột.</div>}
          <button className="nut chinh" disabled={thieu.length > 0 || trung || !!dang} onClick={ghepThu}>Ghép thử</button>
        </div>
      )}
      {dang && <div className="phu" style={{ marginTop: 6 }}>{dang}</div>}
      {loi && <div className="phu" role="alert" style={{ color: "var(--loi)", marginTop: 6 }}>{loi}</div>}
      {thu && (
        <div style={{ marginTop: 8 }}>
          <div className="h"><b>Ghép thế này thì…</b> <span className="phu">(code tính, chưa tính thưởng, chưa lưu)</span></div>
          {thu.chan && <div className="canh-bao" role="alert"><b>Chưa ghép được:</b> {thu.chan}</div>}
          {dongDoiSoat(thu.doi_soat).length > 0 && (
            <ul className="phu so" style={{ paddingLeft: 16, margin: "4px 0" }}>{dongDoiSoat(thu.doi_soat).map((d, i) => <li key={i}>{d}</li>)}</ul>
          )}
          {(thu.canh_bao || []).map((c, i) => <div key={i} className="canh-bao">Lưu ý: {c}</div>)}
          {maChoQua.length > 0 && (
            <div className="phu">Giá trị mới chưa biết (máy che chữ, chỉ báo mã + số dòng) — mở file xem, đúng là giá trị thật thì tick cho qua:
              {(thu.gia_tri_moi || []).map((g) => (
                <label key={g.ma} style={{ display: "block" }}>
                  <input type="checkbox" disabled={!thu.cau_kiem} checked={!!choQua[g.ma]} onChange={(e) => setChoQua((c) => ({ ...c, [g.ma]: e.target.checked }))} />{" "}
                  {g.ma} · trường {truong.find((t) => t.field === g.truong)?.meaning ?? g.truong} · {g.so_dong.toLocaleString("vi-VN")} dòng
                </label>
              ))}
            </div>
          )}
          {thu.cau_kiem ? (
            <div style={{ marginTop: 6 }}>
              <div className="h"><b>Câu kiểm</b> <span className="phu">(thay cho nút "Đồng ý" — trả lời một lần; sai thì phải ghép thử lại)</span></div>
              <div className="phu">{thu.cau_kiem.cau}</div>
              <input className="tim" inputMode="numeric" aria-label="Trả lời câu kiểm" placeholder="số thứ tự cột" value={traLoi} onChange={(e) => setTraLoi(e.target.value.trim())} />
              <input className="tim" aria-label="Người xác nhận" maxLength={80} placeholder="Người xác nhận (tự khai — trang chưa bật đăng nhập)" value={nguoi} onChange={(e) => setNguoi(e.target.value)} />
              <input className="tim" aria-label="Ghi chú" maxLength={500} placeholder={maChoQua.length || khongSauCk ? "Ghi chú — bắt buộc: lý do" : "Ghi chú (không bắt buộc)"} value={ghiChu} onChange={(e) => setGhiChu(e.target.value)} />
              <button className="nut chinh" disabled={!xacNhanDuoc || !!dang} onClick={xacNhan}>Xác nhận và tải file lên</button>
            </div>
          ) : <div className="phu">Sửa ô chọn cột ở trên (hoặc sửa file) rồi bấm Ghép thử lại.</div>}
        </div>
      )}
    </div>
  );
}
