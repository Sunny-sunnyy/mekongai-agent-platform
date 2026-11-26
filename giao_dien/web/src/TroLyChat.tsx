// [ghi chú] TRỢ LÝ: chat với trợ lý dựng workflow (Operator của Capitol) qua máy chủ trang (POST /api/chat).
// Trang CHỈ gửi câu hỏi + mã cuộc trò chuyện; hồ sơ trợ lý do máy chủ gắn cứng. Trợ lý liệt kê / xem / TẠO MỚI workflow; không sửa workflow có sẵn,
// không chạy, không duyệt — muốn chạy thì mở workflow trong xưởng rồi bấm Chạy (đi qua giữ chỗ tiền). Lịch sử chỉ giữ trong phiên trình duyệt này.
// [Lần gắn 4 — User: "tab trợ lý bên trái nhỏ và chật… chuyển sang góc dưới bên phải hoặc cửa sổ pop up riêng"] CỬA SỔ NỔI góc dưới phải:
// nút "Trợ lý" luôn thấy; mở ra khung rộng, có phóng to / thu nhỏ; nhớ mở / đóng + cỡ theo phiên trình duyệt. Câu trả lời hiển thị qua Markdown.tsx
// (dựng bằng phần tử React, không innerHTML).
import { useEffect, useRef, useState } from "react";
import { api, usd, type ChatTraLoi, type DauVet, type HoiNhanh, type TepLuot, type TroLyTraLoi, type View } from "./api";
import { Markdown } from "./Markdown";
import { xepHang, maTrongChu, oMotNhanVien, tongDoiTrongCau, type Dong } from "./xepHang";
import { TEN_CONG_CU } from "./tenCongCu";
import { banDuLieu } from "./banDuLieu";   // [ghi chú] bản dữ liệu đang dùng

type Tin = { ai: "toi" | "tro_ly" | "loi"; chu: string; kq?: ChatTraLoi; hn?: HoiNhanh; kpi?: boolean; tl?: TroLyTraLoi };   // tl: câu trả lời đường một khung chat (/api/tro-ly)
const KHO = "xuong_chat_v1";
// [ghi chú] gợi ý KHÔNG ghi cứng "tháng 10" / mã NV: kỳ lấy từ meta của hộp KPI đang bật (prop ky = period),
// mã NV chỉ lấy từ câu / bảng của chính lượt (xếp hạng) — đổi tháng là gợi ý đổi theo, không còn hỏi NV08 / Turnover của tháng 10.
const tenKy = (ky?: string | null) => (ky && /^\d{4}-\d{2}$/.test(ky) ? `tháng ${Number(ky.slice(5))}/${ky.slice(0, 4)}` : "kỳ đang chạy");
const goiYKpi = (ky?: string | null) => [`Tổng lương kinh doanh cả đội ${tenKy(ky)} là bao nhiêu?`, `Ai được lương kinh doanh nhiều nhất ${tenKy(ky)}?`,
  `Những ai chưa có tiền ở KPI nào ${tenKy(ky)}, vì sao?`];
// [ghi chú] bỏ chọn chế độ: máy chủ (/api/tro-ly) tự chọn quy trình
const GOI_Y = ["Trong tổ chức này đang có những workflow nào?", "Tạo workflow mới: một ô nhập chữ nối sang một ô ra chữ, tên “Thử chat”."];
// [E U9] mã cuộc chat = 32 hex (cùng khuôn chat_operator); máy chủ vẫn tự đổi khi chuyển sang trợ lý dựng
// [ghi chú] cùng chữ với chat_chay.NHAN_TAM_TINH (máy chủ) — nhãn dưới câu trả lời đường một khung chat
const NHAN_TAM_TINH = "Số tạm tính, chưa có người duyệt. File tải ngay trong chat là bản NHÁP; muốn bản chính thức (có người duyệt) thì bảo \"xuất bảng thưởng chính thức\".";
const moiConv = () => { const b = new Uint8Array(16); crypto.getRandomValues(b); return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join(""); };
const CONV_HOP_LE = /^[0-9a-f]{32}$/;

// Tên công cụ của trợ lý ⇒ lời thường; công cụ dựng workflow có tiền tố wb_. Chưa có trong bảng ⇒ hiện tên gốc.
const TEN_BUOC: Record<string, string> = {
  list_workflows: "liệt kê workflow", show_workflows: "hiện danh sách workflow", get_workflow_definition: "xem định nghĩa workflow",
  describe_workflow: "mô tả workflow", get_workflow_run_status: "xem trạng thái lượt chạy", update_workflow_prompt: "sửa lời nhắc trong workflow",
  export_workflow_prompt: "xuất lời nhắc", export_workflow_questionnaire: "xuất bảng câu hỏi", stop_workflow: "dừng lượt chạy", pause_workflow: "tạm dừng lượt chạy",
  ask_user_decision: "hỏi lại người dùng", report_input_issue: "báo đầu vào có vấn đề", list_run_tools: "xem công cụ của lượt chạy", show_output: "hiện kết quả",
  set_file_display_preference: "đặt cách hiện file",
  wb_list_workflow_nodes: "xem danh mục khối", wb_update_workflow_node: "sửa một khối", wb_assemble_workflow: "ráp và lưu workflow",
  wb_edit_workflow: "sửa sơ đồ workflow", wb_lint_workflow: "kiểm workflow", wb_publish_workflow: "đăng workflow", wb_list_workflow_runs: "xem các lượt chạy",
};
const tenBuoc = (t: string) => TEN_BUOC[t] || t;

const khoUser = (uid?: string) => `${KHO}_${uid || "mac_dinh"}`;
const doc = (uid?: string): { tin: Tin[]; conv?: string } => {
  try { return JSON.parse(sessionStorage.getItem(khoUser(uid)) || "") || { tin: [] }; } catch { return { tin: [] }; }
};
const docConv = (uid?: string): string | undefined => {
  try { const v = localStorage.getItem(`xuong_chat_conv_${uid || "mac_dinh"}`); if (v && CONV_HOP_LE.test(v)) return v; } catch { /* bỏ qua */ }
  return doc(uid).conv;
};

const KHO_CUA = "xuong_chat_cua_v1";
const KHO_GAN = "xuong_chat_gan_v1";   // nhớ "gắn mép phải" trong trình duyệt (localStorage) — không mất khi mở lại trang
type Cua = { mo: boolean; to: boolean; w?: number; h?: number };
const soDuong = (x: unknown) => (typeof x === "number" && x > 0 && x < 10000 ? Math.round(x) : undefined);
const docCua = (): Cua => {
  try { const d = JSON.parse(sessionStorage.getItem(KHO_CUA) || ""); return { mo: !!d.mo, to: !!d.to, w: soDuong(d.w), h: soDuong(d.h) }; } catch { return { mo: false, to: false }; }
};
const kep = (x: number, a: number, b: number) => Math.max(a, Math.min(Math.max(a, b), x));

const so = (x: unknown) => (typeof x === "number" ? x.toLocaleString("vi-VN") : String(x ?? "—"));
const soTrong = (chu: string) => { const r = new Set<number>(); (chu.match(/\d{1,3}(?:[.,]\d{3})+|\d+/g) || []).forEach((m) => { const n = Number(m.replace(/[.,]/g, "")); if (n >= 1000) r.add(n); }); return r; };
// ô số của bảng: nhãn theo KHOÁ (ghi đè nhãn máy chủ khi cần) + ngày yyyy-mm-dd ⇒ dd/mm/yyyy
const NHAN_O: Record<string, string> = { cutoff: "Ngày chốt kỳ", period: "Kỳ", captured_at: "Đơn hàng mới nhất trong số liệu" };   // [ghi chú] hộp KPI chạy đường RAW: giá trị = ngày đơn mới nhất được tính (SAI nghĩa nếu hộp chạy chế độ Tracking)
const CHU_THICH_O: Record<string, string> = { captured_at: "Đơn phát sinh sau ngày này chưa nằm trong con số" };
const giaTriO = (v: unknown) => { const m = typeof v === "string" ? v.match(/^(\d{4})-(\d{2})-(\d{2})$/) : null; const k = typeof v === "string" ? v.match(/^(\d{4})-(\d{2})$/) : null;
  return m ? `${m[3]}/${m[2]}/${m[1]}` : k ? `${k[2]}/${k[1]}` : so(v); };
const MAU_KHOI: Record<string, string> = { text_input_node: "var(--g-input)", agent_node: "var(--nhan)", guardrails_node: "var(--duyet)", text_output_node: "var(--dong)" };

// Bảng số do phần tính thưởng dựng (hình "view" chung với trang :5183) — ô có số trùng số trong câu trả lời được tô
function BangView({ v, trongCau }: { v: View; trongCau: Set<number> }) {
  return (<>
    {v.tables.map((b) => (
      <div key={b.key} className="hn-bang">
        <table>
          <caption>{b.label || b.key}</caption>
          <thead><tr>{b.columns.map((c, i) => <th key={i} className={i > 0 ? "so" : ""}>{typeof c === "string" ? c : c.label || c.key}</th>)}</tr></thead>
          <tbody>{b.rows.map((r, i) => <tr key={i}>{r.map((o, j) => (
            <td key={j} className={(typeof o === "number" ? "so" : "") + (typeof o === "number" && trongCau.has(o) ? " sang" : "")}
              title={typeof o === "number" && trongCau.has(o) ? "Con số này có trong câu trả lời" : undefined}>{o == null ? "—" : so(o)}</td>))}</tr>)}</tbody>
        </table>
        {b.total_rows > b.rows.length && <div className="phu">Hiện {b.rows.length} / {b.total_rows} dòng.</div>}
      </div>))}
  </>);
}
function KhoiMa({ chu }: { chu: string }) {
  const [du, setDu] = useState(false);
  return (<div><pre className={"hn-ma" + (!du && chu.length > 260 ? " cat" : "")}>{chu}</pre>
    {chu.length > 260 && <button type="button" className="lk" onClick={() => setDu(!du)}>{du ? "Thu gọn" : "Xem đầy đủ"}</button>}</div>);
}
// Sổ nhật ký các bước của MỘT lượt (dấu vết sẵn có): mỗi bước một dòng; bấm ⇒ mở phiếu tham số + kết quả, đồng thời chọn khối trên khung vẽ
function SoNhatKy({ phien, giay, onChonKhoi }: { phien: string; giay: number; onChonKhoi: (id: string) => void }) {
  const [d, setD] = useState<DauVet | null | undefined>(undefined);
  const [mo, setMo] = useState(false);
  const [moBuoc, setMoBuoc] = useState<number | null>(null);
  useEffect(() => { api.dauVet(phien).then(setD).catch(() => setD(null)); }, [phien]);
  const buoc: { khoi: string; mau: string; ten: string; tom: string; dai: string; muc: [string, string][] }[] = [];
  for (const k of d?.khoi || []) {
    const mau = MAU_KHOI[k.loai] || "var(--muc2)", dai = k.giay != null ? so(k.giay) + " s" : "";
    if (k.loai === "text_input_node") buoc.push({ khoi: k.id, mau, ten: "Nhận câu hỏi", tom: "câu bạn gõ được đưa nguyên văn vào workflow", dai, muc: [] });
    else if (k.loai === "agent_node") {
      for (const c of k.cong_cu || []) buoc.push({ khoi: k.id, mau, ten: "Gọi công cụ " + c.ten, tom: c.thu_lai ? "lần gọi phải thử lại" : "lấy số từ phần tính thưởng",
        dai: c.giay != null ? so(c.giay) + " s" : "", muc: [["Tham số", c.tham_so || "{}"], ["Kết quả công cụ trả về", c.ket_qua || ""]] });
      buoc.push({ khoi: k.id, mau, ten: "Trợ lý KPI viết câu trả lời", tom: k.ai ? `${k.ai.so_lan_goi} lần gọi AI, ${so(k.ai.token_vao)} token vào, ${so(k.ai.token_ra)} ra` : "", dai,
        muc: k.ai ? [["Mô hình và tiền", `${k.ai.mo_hinh_cau_hinh ?? ""} · ${usd(k.ai.tien_usd)}`]] : [] });
    } else if (k.loai === "guardrails_node") {
      const r = k.rao?.cuoi;
      buoc.push({ khoi: k.id, mau, ten: "Kiểm số", tom: r ? (r.dat ? "đạt" : "KHÔNG đạt") : "", dai, muc: r?.ly_do ? [["Kết quả kiểm", r.ly_do]] : [] });
    } else if (k.loai === "text_output_node") buoc.push({ khoi: k.id, mau, ten: "Trả kết quả", tom: "câu trả lời đã qua kiểm được đưa về cuộc trò chuyện", dai, muc: [] });
  }
  // [ghi chú] thấy NGAY Trợ lý đã gọi công cụ gì, tham số gì (chi tiết + kết quả vẫn ở mục gập bên dưới)
  const daGoi = (d?.khoi || []).flatMap((k) => (k.loai === "agent_node" ? k.cong_cu || [] : [])).map((c) => {
    let ts = "";
    try { const j = JSON.parse(c.tham_so || "{}"); ts = Object.values(j && typeof j === "object" ? j : {}).filter((v) => typeof v === "string" || typeof v === "number").map(String).join(", ").slice(0, 60); } catch { /* tham số không phải JSON — bỏ */ }
    return { nhan: TEN_CONG_CU[c.ten] || c.ten, ten: c.ten, ts };
  });
  return (
    <div className={"hn-gap" + (mo ? " mo" : "")}>
      {daGoi.length > 0 && <div className="hn-goi"><span className="phu">Trợ lý đã gọi công cụ:</span>{daGoi.map((g, i) => <span key={i} className="xh-o" title={"Tên kỹ thuật: " + g.ten}>{g.nhan}{g.ts ? <> · <b>{g.ts}</b></> : null}</span>)}</div>}
      <button type="button" aria-expanded={mo} onClick={() => setMo(!mo)}><span className="mui" aria-hidden="true">▸</span>
        {d === undefined ? "Đang đọc các bước của lượt…" : d === null ? "Chưa đọc được các bước của lượt này" : <>Đã làm <b>{buoc.length}</b> bước, {so(giay)} giây{d.tong ? `, ${usd(d.tong.tien_usd)}` : ""}</>}</button>
      {mo && <ol className="hn-buoc">{buoc.map((b, i) => (
        <li key={i} className={moBuoc === i ? "mo" : ""} style={{ ["--mau" as string]: b.mau }}>
          <span className="truc"><i /></span>
          <button type="button" className="dong" aria-expanded={moBuoc === i} onClick={() => { setMoBuoc(moBuoc === i ? null : i); onChonKhoi(b.khoi); }}
            title="Bấm để xem chi tiết và chọn khối này trên khung vẽ"><b>{b.ten}</b><span className="tom">{b.tom}</span><span className="dai so">{b.dai}</span></button>
          {moBuoc === i && b.muc.length > 0 && <div className="phieu">{b.muc.map(([nh, chu], j) => <div key={j}><h5>{nh}</h5><KhoiMa chu={chu} /></div>)}</div>}
        </li>))}</ol>}
    </div>
  );
}
// [ghi chú] Dải xếp hạng do CODE sắp từ bảng của phần tính thưởng — luôn hiện dưới câu trả lời để người đọc tự đối chiếu câu
// "ai nhiều nhất / ít nhất" của AI (khối kiểm số không bắt được lỗi xếp hạng). Tô đậm nhân viên mà câu trả lời nhắc tới.
function DaiXepHang({ cao, thap, tong, nhac }: { cao: Dong[]; thap: Dong[]; tong: number; nhac: Set<string> }) {
  const o = (d: Dong) => <span key={d.ma} className={"xh-o" + (nhac.has(d.ma) ? " nhac" : "")} title={nhac.has(d.ma) ? "Nhân viên này được nhắc trong câu trả lời" : undefined}><b>{d.ma}</b> <span className="so">{so(d.tien)}</span></span>;
  return (
    <div className="hn-xh">
      <div className="xh-dau">Xếp hạng LƯƠNG KINH DOANH (8 KPI) theo nhân viên — <b>do phần tính dựng và sắp bằng code, không phải chữ AI</b> ({tong} người; cùng tiền là đồng hạng; không gồm thưởng thêm)</div>
      <div className="xh-hang"><span className="xh-nhan">{thap.length ? "Cao nhất" : "Từ cao xuống thấp"}</span>{cao.map(o)}</div>
      {thap.length > 0 && <div className="xh-hang"><span className="xh-nhan">Thấp nhất</span>{thap.map(o)}</div>}
    </div>
  );
}
// Câu trả lời của một lượt "Hỏi KPI": chỉ hiện SAU KHI workflow chạy xong và khối kiểm số cho qua
// [ghi chú] gợi ý hỏi tiếp CỐ ĐỊNH do code chọn (không dùng AI sinh). KHÔNG gồm loại câu đang biết lỗi trên mốc 4: xếp hạng "ai ít nhất",
// "dữ liệu có gì cần để ý". Đúng một mã nhân viên ⇒ gợi ý về người đó; còn lại ⇒ gợi ý chung.
function goiYHoiTiep(x: HoiNhanh): string[] {
  const ma = [...new Set([...maTrongChu(x.cau, x.view), ...maTrongChu(x.tra_loi, x.view)])];
  const cao = (xepHang(x.view)?.cao || []).map((d) => d.ma);   // [ghi chú] người so lấy từ bảng của lượt, không ghi cứng NV04 / NV09
  if (ma.length === 1) {
    const so = cao.find((m) => m !== ma[0]);
    return [`${ma[0]} được tiền ở những KPI nào, mỗi KPI bao nhiêu?`, ...(so ? [`So ${ma[0]} với ${so}: ai được nhiều hơn, hơn ở KPI nào?`] : [])];
  }
  return ["Ai được lương kinh doanh nhiều nhất?", ...(cao[0] ? [`${cao[0]} được bao nhiêu, ở những KPI nào?`] : [])];
}
// [ghi chú] DÒNG CỐ ĐỊNH do code in dưới mọi câu trả lời KPI: kỳ + ngày chốt + trạng thái của ĐÚNG lượt đó (không phải chữ AI). Lớp kiểm số không
// bắt được câu ghi nhầm kỳ ⇒ người đọc đối chiếu kỳ ở dòng này. Không đọc được ⇒ nói rõ, không bỏ trống.
const ngayVN = (s: string) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s); return m ? `${m[3]}/${m[2]}/${m[1]}` : s; };
const thangVN = (s: string) => { const m = /^(\d{4})-(\d{2})$/.exec(s); return m ? `tháng ${Number(m[2])}/${m[1]}` : s; };
export function DongKy({ ky }: { ky: NonNullable<TroLyTraLoi["ky"]> }) {
  if ("khong_ro" in ky) return <div className="dong-ky khong-ro" role="note">Không xác định được kỳ dữ liệu của câu trả lời này — hãy kiểm lại kỳ trước khi dùng con số.</div>;
  return <div className="dong-ky" role="note" title="Dòng này do hệ thống in từ bảng số của chính lượt chạy, không phải chữ AI viết. Nếu câu trả lời ở trên ghi kỳ khác thì tin dòng này.">
    Kỳ dữ liệu: <b>{thangVN(ky.period)}</b> · số liệu tới ngày <b>{ngayVN(ky.cutoff)}</b> · trạng thái: <b>{ky.trang_thai === "da_chot" ? "đã chốt" : "tạm tính, chưa có người duyệt"}</b></div>;
}
function KetQuaHoiKpi({ x, onMo, onMoLuot, onChonKhoi, onHoi }: { x: HoiNhanh; onMo: (id: string) => void; onMoLuot: (phien: string, wf: string) => void; onChonKhoi: (id: string) => void; onHoi?: (cau: string) => void }) {
  const [moBang, setMoBang] = useState(false);
  const trongCau = soTrong(x.tra_loi || ""), soBang = new Set<number>();
  x.view?.facts.forEach((f) => { if (typeof f.value === "number") soBang.add(f.value); });
  x.view?.tables.forEach((b) => b.rows.forEach((r) => r.forEach((o) => { if (typeof o === "number") soBang.add(o); })));
  const v = x.view && [...trongCau].some((n) => soBang.has(n)) ? x.view : null;   // câu không dùng số nào của bảng ⇒ không hiện bảng
  // [Đ2] dải xếp hạng: có bảng là hiện, kể cả khi câu không chứa số của bảng — [ghi chú] trừ khi câu trả lời KHÔNG dính KPI:
  // không dùng số nào của bảng, không có mã NV, câu hỏi không hỏi xếp hạng ⇒ ẩn (ca "Viết giúp email xin nghỉ phép" từng gắn bảng lương vào email)
  const dinhKpi = !!v || maTrongChu(x.tra_loi, x.view).length > 0 || maTrongChu(x.cau, x.view).length > 0 || /nhiều nhất|ít nhất|cao nhất|thấp nhất|xếp hạng|ai được/i.test(x.cau || "");
  const xh = x.tra_loi && dinhKpi ? xepHang(x.view) : null;
  const motNV = x.tra_loi ? oMotNhanVien(x.view, x.cau, x.tra_loi) : null;           // [Đ5] đúng MỘT mã nhân viên ⇒ ô nổi lấy số từ bảng
  const tongNoi = tongDoiTrongCau(x.view, trongCau);                                // [Đ5] ô tổng cả đội chỉ nổi khi câu trả lời có đúng số tổng
  return (
    <div className={"hn-ket" + (x.tra_loi ? "" : " hong")}>
      <div className="hn-nguon">Workflow <b>{x.ten}</b>{x.tra_loi ? " — câu trả lời đã qua bước kiểm số" : ""}</div>
      {x.tra_loi ? <div className="hn-cau"><Markdown chu={x.tra_loi} onMo={onMo} /></div> : <div className="chu">{x.loi?.chu || "Trang không trả được câu trả lời cho câu này."}</div>}
      {v && <div className="hn-so">
        {motNV && <div className="noi" title="Số tra từ bảng của phần tính thưởng theo mã nhân viên, không lấy từ chữ AI. Không gồm thưởng thêm. Nếu câu hỏi là về MỘT khoản (một KPI, thưởng thêm, giả định) thì đây KHÔNG phải đáp số của câu hỏi."><span>Lương kinh doanh 8 KPI của {motNV.ma} — số hiện tại, theo bảng (không gồm thưởng thêm)</span><b className="so">{so(motNV.tien)}</b></div>}
        {v.facts.map((f) => <div key={f.key} className={f.key === "team_total" ? (tongNoi ? "noi" : "mo") : undefined}
          title={f.key === "team_total" && !tongNoi ? "Tổng của CẢ ĐỘI — không phải số của một nhân viên" : CHU_THICH_O[f.key]}><span>{NHAN_O[f.key] || f.label || f.key}</span><b className="so">{giaTriO(f.value)}</b></div>)}
      </div>}
      {xh && <DaiXepHang cao={xh.cao} thap={xh.thap} tong={xh.tong} nhac={new Set(maTrongChu(x.tra_loi, x.view))} />}
      {v && v.tables.length > 0 && (
        <div className={"hn-gap" + (moBang ? " mo" : "")}>
          <button type="button" aria-expanded={moBang} onClick={() => setMoBang(!moBang)}><span className="mui" aria-hidden="true">▸</span>Bảng số do phần tính thưởng dựng (không phải chữ AI viết)</button>
          {moBang && <><BangView v={v} trongCau={trongCau} /><div className="phu">Ô tô màu là con số có trong câu trả lời.</div></>}
        </div>)}
      {x.tra_loi && <div className="hn-tam">{x.nhan}</div>}
      <SoNhatKy phien={x.session} giay={x.giay} onChonKhoi={onChonKhoi} />
      {onHoi && x.tra_loi && <div className="hn-tiep"><span className="phu">Hỏi tiếp — bấm là GỬI câu hỏi (tốn như một câu thường):</span>
        {goiYHoiTiep(x).map((g) => <button key={g} type="button" className="nut nho" onClick={() => onHoi(g)}>➤ {g}</button>)}</div>}
      <div className="hn-chan"><span className="phu">Trợ lý dùng các lượt trước của cuộc trò chuyện này (giữ 2 giờ) để hiểu câu hỏi nối tiếp; con số luôn lấy lại từ công cụ, không lấy từ trí nhớ. Bấm “Cuộc mới” để xoá.</span><span className="cach" />
        <button type="button" className="nut nho" onClick={() => onMoLuot(x.session, x.workflow)}>Xem màn kết quả đầy đủ</button></div>
    </div>
  );
}

export function TroLyChat({ onMo, onMoLuot, onDoi, onMoDong, onTheoLuot, onChonKhoi, luotDangXem, khoiDangChay, buocLuot, ky, userId }: {
  ky?: string | null;   // [ghi chú] kỳ của hộp KPI đang bật (/api/kpi/dung-san ky.period) — chỉ để viết gợi ý
  userId?: string;      // [ghi chú] tách lịch sử chat bền vững theo tài khoản người dùng
  onMo: (id: string) => void; onMoLuot: (phien: string, wf: string) => void; onDoi: () => void; onMoDong?: (mo: boolean, to: boolean, gan: boolean) => void;
  onTheoLuot: (phien: string, wf: string) => void | Promise<void>; onChonKhoi: (id: string) => void; luotDangXem: string | null; khoiDangChay: string[]; buocLuot: string | null }) {
  // [ghi chú] KHÔNG còn chọn chế độ: mọi câu đi /api/tro-ly; máy chủ chọn quy trình (pplx-decider), tự chạy / hỏi lại / hỏi tiền
  const [cua, setCua] = useState<Cua>(docCua);
  const [gan, setGan] = useState<boolean>(() => { try { return localStorage.getItem(KHO_GAN) === "1"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem(KHO_GAN, gan ? "1" : "0"); } catch { /* bỏ qua */ } }, [gan]);
  // [ghi chú] kéo góc TRÊN-TRÁI để đổi cỡ (cửa sổ neo góc dưới phải ⇒ kéo sang trái / lên trên là to ra); nhớ cỡ theo phiên
  const khung = useRef<HTMLElement>(null);
  const keo = (e: React.PointerEvent<HTMLDivElement>) => {
    const k = khung.current;
    if (!k || cua.to) return;
    e.preventDefault();
    const x0 = e.clientX, y0 = e.clientY, w0 = k.offsetWidth, h0 = k.offsetHeight;
    const di = (ev: PointerEvent) => setCua((c) => ({ ...c,
      w: kep(w0 + x0 - ev.clientX, 340, window.innerWidth - 36), h: kep(h0 + y0 - ev.clientY, 300, window.innerHeight - 100) }));
    const tha = () => { window.removeEventListener("pointermove", di); window.removeEventListener("pointerup", tha); window.removeEventListener("pointercancel", tha); };
    window.addEventListener("pointermove", di); window.addEventListener("pointerup", tha); window.addEventListener("pointercancel", tha);
  };
  useEffect(() => { try { sessionStorage.setItem(KHO_CUA, JSON.stringify(cua)); } catch { /* bỏ qua */ } onMoDong?.(cua.mo, cua.to, gan); }, [cua, gan]);   // eslint-disable-line react-hooks/exhaustive-deps
  const oNhap = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (cua.mo) oNhap.current?.focus(); }, [cua.mo]);
  const [tin, setTin] = useState<Tin[]>(() => doc(userId).tin);
  const [conv, setConv] = useState<string>(() => { const c = docConv(userId); return c && CONV_HOP_LE.test(c) ? c : moiConv(); });
  const prevUser = useRef<string | undefined>(userId);
  useEffect(() => {
    if (prevUser.current !== userId) {
      prevUser.current = userId;
      const d = doc(userId);
      const c = docConv(userId);
      setTin(d.tin);
      setConv(c && CONV_HOP_LE.test(c) ? c : moiConv());
      setTep({});
      setTl(null);
    }
  }, [userId]);
  useEffect(() => {
    let con = true;
    api.troLyLichSu(conv).then((r) => {
      if (!con) return;
      if (r.tin?.length) {
        setTin((hien) => (hien.length >= r.tin.length ? hien : r.tin.map((m) => ({ ai: m.vai === "nguoi_dung" ? "toi" : "tro_ly", chu: m.noi_dung }))));
      } else if (r.conv && r.conv !== conv && CONV_HOP_LE.test(r.conv)) {
        // Nếu người dùng đăng nhập trên máy mới và chưa có lịch sử cục bộ cho conv mới, khôi phục cuộc chat gần nhất
        setTin((hien) => {
          if (hien.length === 0) setConv(r.conv!);
          return hien;
        });
      }
    }).catch(() => {});
    return () => { con = false; };
  }, [conv, userId]);
  const [tep, setTep] = useState<Record<string, TepLuot[] | "dang" | "loi">>({});   // file của lượt (bấm "Xem file")
  const [cau, setCau] = useState("");
  // [ghi chú] trang Dữ liệu bấm một câu ví dụ ⇒ MỞ khung trợ lý và ĐIỀN SẴN câu đó vào ô nhập (KHÔNG tự gửi — người dùng đọc lại rồi bấm gửi)
  useEffect(() => {
    const h = (e: Event) => { const c = (e as CustomEvent).detail; if (typeof c === "string" && c.trim()) { setCua((x) => ({ ...x, mo: true })); setCau(c.slice(0, 2000)); } };
    window.addEventListener("tro-ly-dien-cau", h);
    return () => window.removeEventListener("tro-ly-dien-cau", h);
  }, []);
  // [ghi chú] trang Dữ liệu bấm "Hỏi trên tài liệu này" ⇒ mở khung trợ lý + gắn tài liệu đã nạp kho vào cuộc trò chuyện (hàm thật gán ở dưới, sau taiLen)
  const moKhoRef = useRef<(sha: string, ten: string) => void>(() => {});
  useEffect(() => {
    const h = (e: Event) => { const d = (e as CustomEvent).detail as { sha?: unknown; ten?: unknown } | null; if (d && typeof d.sha === "string") moKhoRef.current(d.sha, typeof d.ten === "string" ? d.ten : "tài liệu"); };
    window.addEventListener("tro-ly-mo-tai-lieu", h);
    return () => window.removeEventListener("tro-ly-mo-tai-lieu", h);
  }, []);
  const [dang, setDang] = useState(false);
  const [giay, setGiay] = useState(0);
  // [ghi chú] file đang mở của cuộc trò chuyện (máy chủ giữ 24 giờ) — trang chỉ nhớ TÊN để hiện; tải lại trang thì hỏi lại máy chủ
  const [tl, setTl] = useState<string | null>(null);
  const oTep = useRef<HTMLInputElement>(null);
  useEffect(() => { let con = true; api.troLyTaiLieuDangMo(conv).then((r) => { if (con) setTl(r.ten); }).catch(() => { if (con) setTl(null); }); return () => { con = false; }; }, [conv]);
  const cuoi = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try { sessionStorage.setItem(khoUser(userId), JSON.stringify({ tin: tin.slice(-60), conv })); } catch { /* đầy bộ nhớ phiên — bỏ qua */ }
    try { localStorage.setItem(`xuong_chat_conv_${userId || "mac_dinh"}`, conv); } catch { /* bỏ qua */ }
  }, [tin, conv, userId]);
  useEffect(() => { cuoi.current?.scrollIntoView({ block: "end" }); }, [tin, dang]);
  useEffect(() => {
    if (!dang) return;
    const t0 = Date.now();
    const h = setInterval(() => setGiay(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(h);
  }, [dang]);

  // [ghi chú] thẻ KPI đầy đủ (bảng, xếp hạng, kiểm số) do MÁY CHỦ dựng trong chính câu trả lời /api/tro-ly (r.the) — không gọi thêm đường nào
  const theKpi = (r: TroLyTraLoi): HoiNhanh | null => (r.the && r.the.ket_thuc && r.the.view ? r.the : null);
  const nhan = async (r: TroLyTraLoi) => {
    if (r.loai === "dung_moi") {   // máy chủ đã chuyển sang trợ lý dựng workflow (Operator) — hiện như chat dựng cũ
      const kq = r as unknown as ChatTraLoi;
      setTin((t) => [...t, { ai: "tro_ly", chu: r.chu || kq.tra_loi || "", kq, tl: r }]);
      if (kq.workflow_doi?.length) onDoi();
      return;
    }
    const hn = r.loai === "tra_loi" ? theKpi(r) : null;
    setTin((t) => [...t, { ai: r.loai === "loi" ? "loi" : "tro_ly", chu: r.chu, tl: r, hn: hn ?? undefined }]);
  };
  const goi = async (than: Parameters<typeof api.troLy>[0], chuToi: string) => {
    setCau(""); setGiay(0); setDang(true);
    setTin((t) => [...t, { ai: "toi", chu: chuToi }]);
    try { await nhan(await api.troLy(than)); }
    catch (e) { setTin((t) => [...t, { ai: "loi", chu: (e as { body?: { chu?: string } }).body?.chu || (e as Error).message }]); }
    finally { setDang(false); }
  };
  const gui = async (chu: string) => { const c = chu.trim(); if (!c || dang) return; await goi({ cau: c, conv }, c); };   // [ghi chú] không cắt ở trang; máy chủ từ chối có báo khi > 20.000 ký tự
  const chon = async (phieu: string, ma: string, ten: string) => { if (!dang) await goi({ conv, phieu, chon: ma }, `Chọn: ${ten}`); };
  const taiLen = async (f: File | undefined | null) => {
    if (!f || dang) return;
    setGiay(0); setDang(true);
    setTin((t) => [...t, { ai: "toi", chu: `Gửi tài liệu: ${f.name}` }]);
    try {
      if (f.size < 1 || f.size > 20 * 1024 * 1024) throw new Error("File phải từ 1 byte tới 20 MB.");
      const r = await api.troLyTaiLieu(conv, f);
      setTl(r.ten);
      setTin((t) => [...t, { ai: "tro_ly", chu: r.chu + (r.thay_file_cu ? ` (Thay cho tài liệu trước: “${r.thay_file_cu}”.)` : "") }]);
    } catch (e) { setTin((t) => [...t, { ai: "loi", chu: (e as { body?: { loi?: string } }).body?.loi || (e as Error).message }]); }
    finally { setDang(false); if (oTep.current) oTep.current.value = ""; }
  };
  const moKho = async (sha: string, ten: string) => {
    setCua((x) => ({ ...x, mo: true }));
    if (dang) { setTin((t) => [...t, { ai: "loi", chu: "Trợ lý đang trả lời câu trước — chờ xong rồi bấm lại “Hỏi trên tài liệu này” nhé." }]); return; }
    setGiay(0); setDang(true);
    setTin((t) => [...t, { ai: "toi", chu: `Hỏi trên tài liệu đã đưa vào: ${ten}` }]);
    try {
      const r = await api.troLyMoKho(conv, sha);
      setTl(r.ten);
      setTin((t) => [...t, { ai: "tro_ly", chu: r.chu + (r.thay_file_cu ? ` (Thay cho tài liệu trước: “${r.thay_file_cu}”.)` : "") }]);
    } catch (e) { setTin((t) => [...t, { ai: "loi", chu: (e as { body?: { loi?: string } }).body?.loi || (e as Error).message }]); }
    finally { setDang(false); }
  };
  moKhoRef.current = moKho;
  const dongTep = async () => { try { await api.troLyTaiLieuDong(conv); } catch { /* máy chủ không đóng được ⇒ file tự hết hạn sau 24 giờ */ } setTl(null); };
  const xemTep = async (phien: string) => {
    setTep((x) => ({ ...x, [phien]: "dang" }));
    try { const ds = await api.tepLuot(phien); setTep((x) => ({ ...x, [phien]: ds })); } catch { setTep((x) => ({ ...x, [phien]: "loi" })); }
  };

  if (!cua.mo) {
    return (
      <button className="chat-nut" onClick={() => setCua((c) => ({ ...c, mo: true }))} title="Mở trợ lý dựng workflow" aria-label="Mở trợ lý">
        <span aria-hidden="true" className="dau" />Trợ lý{dang ? ` · đang làm ${giay} giây` : tin.length ? ` · ${tin.filter((m) => m.ai !== "toi").length} trả lời` : ""}
      </button>
    );
  }
  return (
    <section ref={khung} className={"chat chat-cua" + (gan ? " gan" : cua.to ? " to" : "")} role="dialog" aria-label="Trợ lý"
      style={!gan && !cua.to && cua.w && cua.h ? { width: `min(${cua.w}px, calc(100vw - 36px))`, height: `min(${cua.h}px, calc(100vh - 100px))` } : undefined}
      onDragOver={(e) => { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault(); }}
      onDrop={(e) => { const f = e.dataTransfer?.files?.[0]; if (f) { e.preventDefault(); taiLen(f); } }}
      onKeyDown={(e) => { if (e.key === "Escape") setCua((c) => ({ ...c, mo: false })); }}>
      {!cua.to && !gan && <div className="chat-keo" onPointerDown={keo} onDoubleClick={() => setCua((c) => ({ mo: c.mo, to: c.to }))}
        title="Kéo để đổi cỡ cửa sổ; bấm đúp để về cỡ ban đầu" aria-hidden="true" />}
      <header className="chat-thanh">
        <b>Trợ lý</b>
        <span className="cach" />
        {tin.length > 0 && <button className="nut nho" onClick={() => { setTin([]); setConv(moiConv()); setTep({}); }} disabled={dang} title="Xoá lịch sử trên màn hình và bắt đầu cuộc trò chuyện mới">Cuộc mới</button>}
        <button className="nut nho" onClick={() => setGan(!gan)} title={gan ? "Thả cửa sổ ra thành cửa sổ nổi" : "Gắn cửa sổ vào mép phải — khung vẽ co lại, không bị che khi workflow chạy"}>{gan ? "Thả nổi" : "Gắn phải"}</button>
        {!gan && <button className="nut nho vuong" onClick={() => setCua((c) => ({ ...c, to: !c.to }))} title={cua.to ? "Thu nhỏ về góc" : "Phóng to"} aria-label={cua.to ? "Thu nhỏ cửa sổ trợ lý" : "Phóng to cửa sổ trợ lý"}>{cua.to ? "▫" : "□"}</button>}
        <button className="nut nho vuong" onClick={() => setCua((c) => ({ ...c, mo: false }))} title="Đóng (lịch sử vẫn giữ trong phiên này)" aria-label="Đóng cửa sổ trợ lý">×</button>
      </header>
      <div className="chat-dau phu">
        Hỏi bất cứ gì về thưởng KPI, tài liệu, hoặc nhờ dựng quy trình mới. Kéo file (PDF, Word, Excel / CSV, ảnh, trang web, JSON) vào đây (hoặc bấm nút kẹp) để hỏi trên chính tài liệu đó. Trợ lý tự chọn quy trình phù hợp và <b>chạy luôn</b> nếu tốn dưới {usd(0.2)};
        đắt hơn thì hỏi bạn trước. Số là <b>tạm tính</b> (đã qua khối kiểm số); bảng chính thức chờ người phụ trách duyệt.
      </div>
      <div className="chat-tin">
        {!tin.length && <div className="phu">Câu mẫu — bấm là GỬI câu hỏi:</div>}
        {!tin.length && [...goiYKpi(ky), GOI_Y[0]].map((g) => <button key={g} className="the goi-y" onClick={() => gui(g)}>➤ {g}</button>)}
        {tin.map((m, i) => (
          <div key={i} className={"bong " + m.ai}>
            {m.tl?.workflow && m.tl.loai !== "hoi" && <div className="phu">Đã chọn: <b>{m.tl.workflow.ten}</b>{m.tl.loai === "tra_loi" || m.tl.loai === "cho_duyet" ? (m.tl.lap_lai ? " · trả lại kết quả trước" : (m.tl as { tu_cache?: boolean }).tu_cache ? " · trả lại kết quả cũ, không chạy lại" : " · đã tự chạy") : ""}</div>}
            {m.hn ? <KetQuaHoiKpi x={m.hn} onMo={onMo} onMoLuot={onMoLuot} onChonKhoi={onChonKhoi} onHoi={!dang && i === tin.length - 1 ? gui : undefined} />
              : m.ai === "tro_ly" ? <Markdown chu={m.chu} onMo={onMo} /> : <div className="chu">{m.chu}</div>}
            {m.tl?.loai === "tra_loi" && m.tl.ky && <DongKy ky={m.tl.ky} />}
            {m.tl?.loai === "tra_loi" && !m.hn && m.tl.nhan && <div className="canh-bao">{NHAN_TAM_TINH}</div>}
            {m.tl?.tai_lieu && (m.tl.tai_lieu.loi.length > 0   // [ghi chú] kết quả kiểm bằng code của câu trả lời trên tài liệu
              ? <div className="canh-bao">Kiểm tự động thấy vấn đề ở chỗ dẫn / câu trích — hãy đối chiếu lại với tài liệu “{m.tl.tai_lieu.ten}”:
                  <ul>{m.tl.tai_lieu.loi.map((l, j) => <li key={j}>{l}</li>)}</ul></div>
              : <div className="phu">Trả lời theo tài liệu “{m.tl.tai_lieu.ten}”. {m.tl.tai_lieu.co_dan
                  ? `Các chỗ dẫn [${m.tl.tai_lieu.don_vi} …] đều có trong tài liệu và câu trích khớp nguyên văn (kiểm bằng code; không kiểm được câu trả lời có đúng ý hay không).`
                  : "Câu trả lời này không kèm chỗ dẫn."}</div>)}
            {(m.tl as { lich_su_rut_gon?: boolean } | undefined)?.lich_su_rut_gon && <div className="phu">Cuộc trò chuyện đã dài: một phần lịch sử cũ không còn được dùng để hiểu câu này. Nếu câu trả lời lạc ý, bấm “Cuộc mới” rồi hỏi lại đủ ý.</div>}
            {(() => {
              const dsAnh = (m.tl?.anh || []).filter(
                (a) => /^image\/(png|jpeg|jpg|webp|gif)$/i.test(a.image_mime || "") && /^[A-Za-z0-9+/=\s]{10,2500000}$/.test(a.image_base64 || "")
              ).slice(0, 4);
              if (!dsAnh.length) return null;
              return (
                <div className="tl-anh" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8, marginTop: 8 }}>
                  {dsAnh.map((a, idx) => (
                    <figure key={idx} style={{ margin: 0, border: "1px solid var(--vien2)", borderRadius: 6, padding: 4, background: "var(--giay2, #f8f8f6)" }}>
                      <img src={`data:${a.image_mime};base64,${a.image_base64.replace(/\s+/g, "")}`} alt={a.chu_thich || `Biểu đồ ${idx + 1}`} style={{ width: "100%", height: "auto", display: "block", borderRadius: 4 }} />
                      {a.chu_thich && <figcaption className="phu" style={{ fontSize: 11, marginTop: 4 }}>{a.chu_thich}</figcaption>}
                    </figure>
                  ))}
                </div>
              );
            })()}
            {m.tl?.loai === "hoi" && m.tl.phieu && m.tl.lua_chon?.length ? (
              <div className="wf-doi">{m.tl.lua_chon.map((x) => (
                <button key={x.chon} className="nut nho" disabled={dang || i !== tin.length - 1} onClick={() => chon(m.tl!.phieu!, x.chon, x.ten)}>{x.ten}</button>))}
                {i !== tin.length - 1 && <span className="phu"> (đã qua — hỏi lại nếu cần)</span>}</div>) : null}
            {m.tl?.phien && (m.tl.loai === "tra_loi" || m.tl.loai === "cho_duyet") && (
              <div className="wf-doi">
                {m.tl.workflow?.id && <button className="nut nho" onClick={() => onMoLuot(m.tl!.phien!, m.tl!.workflow!.id!)}>Mở lượt này trên xưởng</button>}
                <button className="nut nho" onClick={() => xemTep(m.tl!.phien!)} disabled={tep[m.tl.phien] === "dang"}>{m.tl.loai === "cho_duyet" ? "Đã duyệt? Xem file" : "Xem file của lượt"}</button>
                {(() => { const d = tep[m.tl!.phien!]; if (!d) return null; if (d === "dang") return <span className="phu"> đang tìm…</span>;
                  if (d === "loi") return <span className="canh-bao"> không đọc được danh sách file</span>;
                  if (!d.length) return <span className="phu"> {m.tl!.loai === "cho_duyet" ? "chưa có file — bảng chính thức còn chờ duyệt" : "lượt này không có file"}</span>;
                  return <span>{d.map((f) => <a key={f.id} className="nut nho" href={`/api/tep/tai-ve?phien=${encodeURIComponent(m.tl!.phien!)}&tep=${encodeURIComponent(f.id)}`}>Tải {f.ten}</a>)}</span>; })()}
              </div>)}
            {m.tl?.loai === "tra_loi" && m.tl.co_xuat && !m.tl.tep?.length && i === tin.length - 1 && (   // [ghi chú] chỉ ở câu trả lời MỚI NHẤT (máy chủ xuất lượt gần nhất)
              <div className="wf-doi"><button className="nut nho" disabled={dang} onClick={() => { if (!dang) void goi({ conv, xuat: "xlsx" }, "Tải Excel"); }}>Tải Excel</button>
                <span className="phu"> bảng thưởng cả đội + phần của câu này, bản nháp (số tạm tính)</span></div>)}
            {(m.tl?.loai === "tep" || m.tl?.loai === "tra_loi") && m.tl.tep?.length ? (   // [ghi chú] + câu trả lời kèm file nháp (thẻ xuất file, không chờ duyệt)
                 // [ghi chú] file xuất từ kết quả vừa trả lời — tải qua máy chủ, chỉ đúng cuộc chat này
              <div className="wf-doi">{m.tl.tep.map((f) => (
                <a key={f.id} className="nut nho" href={`/api/tro-ly/tep?conv=${encodeURIComponent(conv)}&id=${encodeURIComponent(f.id)}`}>Tải {f.ten}</a>))}
                <span className="phu"> (giữ 30 phút)</span></div>) : null}
            {m.kq?.buoc && m.kq.buoc.length > 0 && (
              <div className="buoc"><b>Đã làm:</b> {m.kq.buoc.map((b, j) => <span key={j}>{j > 0 && <i aria-hidden="true"> ⇒ </i>}{tenBuoc(b)}</span>)}</div>
            )}
            {m.kq && (
              <div className="chan so">
                {typeof m.kq.so_cong_cu === "number" && !m.kq.buoc?.length && <>đã gọi {m.kq.so_cong_cu} công cụ · </>}
                {m.kq.giay != null && <>{m.kq.giay} giây · </>}
                tiền {usd(m.kq.usd)}{m.kq.usd_la === "mức tối đa" ? " (ghi mức tối đa — không đọc được số thật)" : ""}
                {m.kq.usage && <> · {m.kq.usage.requests} lần gọi AI, {m.kq.usage.input.toLocaleString("vi-VN")} token vào / {m.kq.usage.output.toLocaleString("vi-VN")} ra</>}
                {m.kq.usage?.cham_tran ? <b className="canh-bao"> · chạm trần lượt chat — câu trả lời có thể dở dang</b> : null}
                {m.kq.khoa && <div className="canh-bao">Đường chat đã khoá: {m.kq.khoa}</div>}
              </div>
            )}
            {m.kq?.chay && (
              <div className="wf-doi">
                {m.kq.chay.tra_loi != null ? (<>
                  <div className="phu"><b>Kết quả từ workflow{m.kq.chay.ten ? ` “${m.kq.chay.ten}”` : ""}</b> (đã qua khối kiểm số tự động — tạm tính):</div>
                  <div className="chu" style={{ whiteSpace: "pre-wrap" }}>{m.kq.chay.tra_loi}</div>
                  <div className="canh-bao">{m.kq.chay.nhan}</div>
                </>) : (
                  <div className="canh-bao">Trang không trả được kết quả{m.kq.chay.ten ? ` của workflow “${m.kq.chay.ten}”` : ""}: {m.kq.chay.ly_do}</div>
                )}
                {m.kq.chay.phien
                  ? <button className="nut nho" onClick={() => onMoLuot(m.kq!.chay!.phien!, m.kq!.chay!.workflow)}>Mở lượt này trên xưởng</button>
                  : <button className="nut nho" onClick={() => onMo(m.kq!.chay!.workflow)}>Mở workflow trong xưởng</button>}
              </div>
            )}
            {m.kq?.workflow_doi?.map((w) => (
              <div key={w.id} className="wf-doi">
                Workflow {w.moi ? "mới tạo" : "vừa sửa"} (phiên bản {w.phien_ban})
                {w.loi_kiem ? <b className="canh-bao"> · {w.loi_kiem} lỗi kiểm — mở ra sửa trước khi chạy</b> : w.loi_kiem === 0 ? " · kiểm sạch" : " · chưa kiểm được"}
                <button className="nut nho" onClick={() => onMo(w.id)}>Mở trong xưởng</button>
              </div>
            ))}
          </div>
        ))}
        {dang && <div className="bong tro_ly cho hn-cho"><div><span className="dot" aria-hidden="true" />Đang chọn quy trình và chạy… {giay} giây{giay > 30 ? " (bảng chính thức / dựng quy trình có thể lâu hơn)" : ""}</div>
          {luotDangXem && khoiDangChay.length ? <div className="phu">Đang chạy: {khoiDangChay.join(", ")}</div> : buocLuot ? <div className="phu">{buocLuot}</div> : null}</div>}
        <div ref={cuoi} />
      </div>
      {tl && <div className="chat-tep">Đang mở: <b>{tl}</b> <span className="phu">— hỏi về tài liệu này ngay bên dưới (giữ 24 giờ)</span><span className="cach" />
        <button type="button" className="lk" onClick={dongTep} disabled={dang} title="Bỏ tài liệu này khỏi cuộc trò chuyện">Đóng tài liệu</button></div>}
      <form className="chat-nhap" onSubmit={(e) => { e.preventDefault(); gui(cau); }}>
        <input ref={oTep} type="file" accept=".pdf,.docx,.txt,.md,.xlsx,.xlsm,.xls,.csv,.png,.jpg,.jpeg,.html,.htm,.json" hidden onChange={(e) => taiLen(e.target.files?.[0])} />
        <button type="button" className="nut nho vuong" onClick={() => oTep.current?.click()} disabled={dang} aria-label="Đính kèm tài liệu để hỏi"
          title="Đính kèm tài liệu (PDF, Word .docx, .txt, .md; tối đa 20 MB) rồi hỏi về nó">📎</button>
        <textarea ref={oNhap} value={cau} maxLength={20000} rows={3} placeholder="Hỏi về thưởng KPI, tài liệu (kéo file vào đây), hoặc nhờ dựng quy trình…" aria-label="Câu gửi trợ lý"
          onChange={(e) => setCau(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); gui(cau); } }} />
        <button className="nut chinh" disabled={dang || !cau.trim()}>{dang ? "Đang chờ…" : "Gửi"}</button>
      </form>
    </section>
  );
}
