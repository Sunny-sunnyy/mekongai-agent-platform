// Hình dữ liệu chung (bản thiết kế chung với mekongai-template, mục 8) + gọi máy chủ may_chu.py (cùng gốc 127.0.0.1:18400).
import { useEffect, useState } from "react";   // [ghi chú kỹ thuật] cho useMoHinhKhoi

export type ParamDef = {
  name: string; label: string; kind: string | null; required: boolean; default: unknown; info: string;
  advanced: boolean; bindable: boolean; placeholder: string;
  choices?: (string | { value: string; ok: boolean; why: string })[];
};
export type BlockDef = {
  type: string; label: string; description: string; group: string; group_key: string; icon: string | null;
  inputs: { name: string; label: string; required: boolean; accepts: string[]; many: boolean }[];
  outputs: { name: string; label: string; kind: string[] }[];
  params: ParamDef[]; runnable_here: boolean; tested_here: boolean; not_runnable_reason: string;
};
// Hình chung v1 (yêu cầu vận hành 09/11): khối đúng 4 trường, dây đúng 4 khoá + from_port bắt buộc; tên hiển thị khối là của trang ⇒ ui.labels.
export type WfNode = { id: string; type: string; params: Record<string, unknown>; position: { x: number; y: number } };
export type WfEdge = { from_node: string; from_port: string; to_node: string; to_port: string };
export type Workflow = { id?: string | null; name: string; description?: string; nodes: WfNode[]; edges: WfEdge[]; ui?: { labels?: Record<string, string> }; version?: number; saved_at?: string; unsupported_edges?: number };
export type CheckItem = { muc: "loi" | "canh_bao"; code: string; node: string | null; field: string | null; message: string; suggestion: string };
export type RunRow = { phien: string; tt: string; tao: string; bd: string | null; xong: string | null; loi: string; ten: string; wf: string; pb: string };
export type RunEvent = { id: string; c: string; n: string | null; t: string; ts: string; k: string | null; d: string | null };
export type RunDetail = { luot: { id: string; phien: string; tt: string; tao: string; bd: string | null; xong: string | null; loi: string; ten: string; pb: string }; su_kien: RunEvent[] };
export type Call = {
  node: string; model: string | null; at?: string; tokens_in?: number; tokens_out?: number; tokens_cached?: number; tokens_reasoning?: number;
  call_kind: "main" | "side" | "tool"; why: string; measurable: boolean; cost_usd: number | null; price_source?: string | null; price_read_at?: string; price_note?: string;
  mien_tran?: boolean;   // [ghi chú kỹ thuật] Claude qua Bedrock: giá tham khảo, không tính trần tiền API
};
// [ghi chú kỹ thuật] mô hình + mức suy luận ĐANG ĐẶT trong file mô hình khối (tên gửi trên dây, vd "claude-sonnet-5-5-medium"); mac_dinh "" = theo ô của khối
export type MoHinhKhoi = { mac_dinh: string; theo_khoi: Record<string, string>; khoi_doi_duoc: string[]; loi: string | null };
// [ghi chú kỹ thuật] kho nhớ CHUNG của file mô hình khối: mọi thẻ khối trên khung vẽ dùng chung MỘT lần gọi, đọc lại khi đã quá 15 giây.
let _mhk: MoHinhKhoi | null = null, _mhkLuc = 0;
const _mhkNghe = new Set<() => void>();
export function useMoHinhKhoi(): MoHinhKhoi | null {
  const [, ve] = useState(0);
  useEffect(() => {
    const f = () => ve((x) => x + 1);
    _mhkNghe.add(f);
    if (Date.now() - _mhkLuc > 15000) {
      _mhkLuc = Date.now();
      fetch("/api/mo-hinh-khoi").then((r) => (r.ok ? r.json() : null)).then((d) => { if (d) { _mhk = d as MoHinhKhoi; _mhkNghe.forEach((g) => g()); } }).catch(() => {});
    }
    return () => { _mhkNghe.delete(f); };
  }, []);
  return _mhk;
}
export const moHinhThat = (m: MoHinhKhoi | null, loai: string): string | null =>
  m && m.khoi_doi_duoc.includes(loai) ? (m.theo_khoi[loai] || m.mac_dinh || null) : null;
const MUC_SL = ["low", "medium", "high", "xhigh", "max"];
export const tenMoHinh = (s: string) => {   // "claude-sonnet-5-5-medium" ⇒ "claude-sonnet-5-5 · mức suy luận medium"
  const i = s.lastIndexOf("-"), duoi = i > 0 ? s.slice(i + 1) : "";
  return MUC_SL.includes(duoi) ? `${s.slice(0, i)} · mức suy luận ${duoi}` : `${s} · mức suy luận mặc định của cổng AI`;
};
export type ToolCall = { node: string; tool: string; args: string; at: string; result: string | null; returned_at: string | null; retry: boolean; internal: boolean };
export type Check = { y: { kieu: string; gia_tri: string; nguon: "cong_cu" | "dau_vao" | "loi_dan" | null }[]; tong: number; khong_nguon: number;
  khuon: { truong: string; gia_tri: string; co_trong_ket_qua_cong_cu: boolean }[]; co_cong_cu: boolean };
export type Cost = { checks?: Record<string, Check>; inputs?: Record<string, string>; tool_calls?: ToolCall[]; calls: Call[]; total: { main: number; side: number; tool?: number; tokens_in: number; tokens_out: number; unmeasured_calls: number; unpriced_calls: number; bedrock?: number };
  // [ghi chú kỹ thuật] tiền theo SỔ của phiên (máy chủ đọc dòng sổ) — có cả khi Capitol không ghi token cho khối Chạy code / làm file
  so?: { usd_tinh_tran: number; usd_bedrock_tham_khao: number; token_vao: number; token_ra: number; so_dong: number; gio?: string } | null };
// [ghi chú kỹ thuật] khối KPI dựng sẵn (dữ liệu nhóm kỹ thuật qua GET /api/kpi/dung-san): ô nào người dùng được sửa, ô nào ẩn, ô nào chỉ xem
export type KpiKhoi = {
  ma: string; ten: string; mo_ta: string; khoi_goc: string; o_day: string[]; o_an: string[]; o_khoa: string[]; ghi_chu: Record<string, string>;
  sua_duoc: Record<string, { nhan: string; kieu: "chu" | "so_nguyen"; toi_da?: number; tu?: number; toi?: number }>;
};
export type KpiDungSan = {
  phien_ban: string; khoi: KpiKhoi[]; bo_san: { ten: string; mo_ta: string }; san_sang: boolean;
  mau: { ten: string; cau_hoi: string; ghi_chu?: string; id?: string | null }[];
  loi_dan: string | null; cong_cu: { ten: string; mo_ta: string }[]; ky: { period?: string; cutoff?: string; rule_version?: string };
};
// [ghi chú kỹ thuật] trả lời của POST /api/chat (chat_operator.gui) — lỗi 502 cũng mang usd / khoa
export type ChatChay = { da_chay: boolean; workflow: string; ten: string; phien: string | null; trang_thai: string | null;
  tra_loi: string | null; nhan: string | null; ly_do: string | null };   // [va_chat_chay C 12/11] trang tự chạy workflow Trợ lý đề nghị
export type ViewCot = { key: string; label?: string } | string;
export type View = { facts: { key: string; label?: string; value: string | number | null }[];
  tables: { key: string; label?: string; columns: ViewCot[]; rows: (string | number | null)[][]; total_rows: number }[]; notes: string[] };
// [ghi chú kỹ thuật] thân của /api/hoi-nhanh/<phiên>: lượt xong mới có tra_loi (đã qua khối kiểm số) hoặc loi (câu báo lời thường, KHÔNG có số)
// [ghi chú kỹ thuật] POST /api/tro-ly — máy chủ (gan/tro_ly.py) chọn workflow bằng Jev, tự chạy / hỏi lại; trang KHÔNG tự quyết chạy gì
export type TroLyTraLoi = { loai: "tra_loi" | "cho_duyet" | "hoi" | "khong_co" | "dung_moi" | "loi" | "tep"; chu: string; phieu?: string | null;
  lua_chon?: { chon: string; ten: string }[]; workflow?: { ten: string; id?: string }; phien?: string; nhan?: string; lap_lai?: boolean;
  co_xuat?: boolean;                       // [ghi chú kỹ thuật] câu trả lời KPI có bảng ⇒ hiện nút "Tải Excel"
  workflow_doi?: ChatTraLoi["workflow_doi"];
  the?: HoiNhanh;                          // [ghi chú kỹ thuật] thẻ KPI đầy đủ máy chủ dựng (chỉ khi gói đã qua kiểm số)
  // [ghi chú kỹ thuật] kỳ dữ liệu của CHÍNH lượt (máy chủ đọc từ gói số của khối Kiểm số) — web in dòng cố định dưới câu trả lời KPI
  ky?: { period: string; cutoff: string; trang_thai: "tam_tinh" | "da_chot"; data_version?: string | null } | { khong_ro: true };
  tep?: { id: string; ten: string }[];     // [ghi chú kỹ thuật] file vừa xuất — tải ở GET /api/tro-ly/tep?conv=&id=
  // [ghi chú kỹ thuật] câu trả lời trên FILE người dùng kéo vào khung chat: kết quả kiểm bằng code (chỗ dẫn có tồn tại, câu trích có trong tài liệu)
  tai_lieu?: { ten: string; don_vi: string; co_dan: boolean; loi: string[] } };
export type TaiLieuMo = { ten: string; chu: string; so_phan: number; don_vi: string; ky_tu: number; token_uoc: number; thay_file_cu?: string | null };
export type HoiNhanh = { session: string; cau: string; workflow: string; ten: string; giay: number; status?: string | null; ket_thuc: boolean;
  tra_loi?: string | null; nhan?: string | null; view?: View | null; kiem?: { dat: boolean; ly_do: string; phien_ban: string } | null; loi?: { loai: string; chu: string } | null };
export type ChatTraLoi = {
  chay?: ChatChay | null;
  tra_loi?: string; conv?: string; so_cong_cu?: number; trang_thai?: string; ma_luot: string; usd: number; usd_la: "thật" | "mức tối đa" | "không gọi";
  usage: { requests: number; input: number; output: number; cache_read: number; cham_tran: number } | null; giay: number;
  buoc?: string[] | null;   // TÊN công cụ trợ lý đã gọi, theo thứ tự (không tham số)
  xac_nhan_cong_cu: boolean; workflow_doi: { id: string; moi: boolean; phien_ban: number; loi_kiem: number | null }[]; khoa: string | null; loi?: string;
};
export type WfRow = { id: string; ten: string; tao: string; so_pb: number; so_luot: number };
// [ghi chú kỹ thuật] "kpi" = gói của Rào chắn chế độ code {cau_tra_loi, data_version, kiem, bang_thuong, sha256_bang}
export type KetQuaKhoi = { kieu: "kpi" | "dict" | "list" | "chu"; v: unknown };
export type TepLuot = { id: string; ten: string; mime: string; node: string };
export type DauVetKhoi = {
  id: string; ten: string; loai: string; loai_goc?: string; trang_thai: string; giay: number | null; so_cong_cu?: number;
  so_cong_cu_loi?: number; tu_bao_khong_lam_duoc?: boolean;   // [ghi chú kỹ thuật] hỏng mềm
  ai?: { so_lan_goi: number; mo_hinh_ban_ghi: string[]; mo_hinh_cau_hinh: string | null; mo_hinh_file?: string | null; co_do: boolean; ly_do_co: string; token_vao: number; token_ra: number;
         token_suy_nghi: number; tien_usd: number; so_lan_khong_do_duoc: number; muc_suy_nghi: string; ghi_chu: string[] };
  cong_cu?: { ten: string; tham_so: string; ket_qua: string; thu_lai: boolean; giay: number | null; loi?: boolean }[];
  rao?: { so_lan: number; dau: { luc: string; dat: boolean; ly_do: string } | null; cuoi: { luc: string; dat: boolean; ly_do: string } | null };
  duyet?: { cho_tu: string | null; han_giay: string | null; quyet_luc: string | null; ket_qua: string | null; cho_giay: number | null; qua: string; ai: string };
};
export type DauVet = { luot: { phien: string; tt: string; ten: string }; khoi: DauVetKhoi[]; tong: { tien_usd: number; so_lan_goi: number; co_do: string[]; khong_do_duoc: number; hong_mem?: string[] } };
export type TruyVan = { nhan: string; con_nua: boolean; lan: { khoi: string; luc: string | null; sql: string;
  ket_qua: { co: boolean; cot?: string[]; dong?: (string | number | boolean | null)[][]; so_dong?: number; da_cat?: boolean; ghi_chu?: string; loi?: string; chu?: string } }[] };
export type DongThuong = { employee_id: string; kpi: string; pct: number | null; reached_tier: string | null; amount: number; reason: string };
export type GoiKpi = {
  cau_tra_loi: string; data_version?: string; sha256_bang?: string;
  kiem?: { dat?: boolean; ly_do?: string; loi?: unknown[]; phien_ban?: string; nguon?: string };   // [ghi chú kỹ thuật] nguon = "ai" khi AI chấm số là lớp quyết định (gói AI chặn số)
  bang_thuong?: { period?: string; cutoff?: string; lines?: DongThuong[]; team_total?: number; employees?: string[]; kpis?: string[];
    salary?: Record<string, number>; kpi_total?: Record<string, number> };
  duyet?: { luc?: string; sha256_bang?: string; kieu?: string; cho_giay?: number };   // [ghi chú kỹ thuật] kieu = "tu_duyet_qua_han" | "nguoi"; vắng = bản cũ = người duyệt
  xuat_excel?: { ten?: string; nhap?: boolean; kieu?: string };   // [ghi chú kỹ thuật] khối Người duyệt ghi khi đã xuất file (nhap = true khi tự duyệt)
};

async function doc<T>(r: Response): Promise<T> {
  const j = await r.json().catch(() => ({ loi: `HTTP ${r.status}` }));
  if (r.status === 401) window.dispatchEvent(new Event("xuong-can-ma"));   // [ghi chú kỹ thuật] chưa có / sai mã mở trang ⇒ App hiện lời nhắc
  if (!r.ok) throw Object.assign(new Error(j.loi || `HTTP ${r.status}`), { body: j, status: r.status });
  return j as T;
}
// [người dùng 12/11] máy chủ trang tắt / mất mạng ⇒ fetch ném TypeError "Failed to fetch" (chữ thô tiếng Anh) ⇒ đổi thành câu tiếng Việt cho MỌI lời gọi
const MAT_KET_NOI = "Không kết nối được máy chủ trang — thử lại sau ít phút.";
const goi = (u: string, o?: RequestInit) => fetch(u, o).catch(() => { throw Object.assign(new Error(MAT_KET_NOI), { mat_ket_noi: true }); });
const lay = <T,>(u: string) => goi(u).then((r) => doc<T>(r));
const gui = <T,>(u: string, body: unknown) =>
  goi(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => doc<T>(r));

// [10/11, A] bỏ giới hạn cứng: lọc theo ngày (giờ VN) + số dòng; /api/dem = tổng theo ngày (người dùng: "xem thêm" + lọc theo ngày)
export type Dem = { wf: number; luot: number; ngay: { ngay: string; wf: number; luot: number }[] };
const loc = (ngay?: string, so?: number) => {
  const q = new URLSearchParams();
  if (ngay) q.set("ngay", ngay);
  if (so) q.set("so", String(so));
  const s = q.toString();
  return s ? "?" + s : "";
};

// [ghi chú kỹ thuật] "chạy lại từ khối" — hợp đồng API nhóm kỹ thuật (va_chay_lai/gan/HOP_DONG_API.md 14:22)
export type ChayLaiXem = { workflow_id: string; phien_nguon: string; khoi_chon: string[]; single_step: boolean; chay_moi: string[]; dung_lai: string[];
  lech: Record<string, string>; sau_dung_lai_cu: string[]; muc_toi_da: number; phien_ban: number };
export type ChayLaiThan = { workflow_id: string; phien_nguon: string; khoi: string[]; single_step?: boolean };
export const api = {
  blocks: () => lay<BlockDef[]>("/api/blocks"),
  kpiDungSan: () => lay<KpiDungSan>("/api/kpi/dung-san"),
  kpiBoSan: (cau?: string) => gui<Workflow>("/api/kpi/bo-san", cau ? { cau } : {}),
  kpiKhoi: (ma: string) => gui<{ type: string; params: Record<string, unknown>; label: string }>("/api/kpi/bo-san", { ma }),
  // [ghi chú kỹ thuật] dv = bản dữ liệu đang dùng (banDuLieu.ts); không có ⇒ không gửi ⇒ máy chủ dùng bản lúc bật
  hoiNhanh: (cau: string, truoc: string[] = [], dv?: string | null) =>
    gui<{ session: string; workflow: string; ten: string; tu_cache?: boolean; luc?: string }>("/api/hoi-nhanh", { cau, ...(truoc.length ? { truoc } : {}), ...(dv ? { data_version: dv } : {}) }),
  hoiNhanhTt: (s: string) => lay<HoiNhanh>(`/api/hoi-nhanh/${s}`),
  chayLaiXemTruoc: (b: ChayLaiThan) => gui<ChayLaiXem>("/api/chay-lai/xem-truoc", b),
  chayLai: (b: ChayLaiThan) => gui<ChayLaiXem & { session: string; version: number; chay_lai_tu: string }>("/api/chay-lai", b),
  chayLaiDungLai: (s: string) => lay<{ session: string; dung_lai: { khoi: string; tu_phien: string }[] }>(`/api/chay-lai/${s}`),
  chat: (cau: string, conv?: string) => gui<ChatTraLoi>("/api/chat", conv ? { cau, conv } : { cau }),
  // [ghi chú kỹ thuật] câu mới: {cau, conv} · trả lời câu hỏi lại / hỏi tiền: {conv, phieu, chon} — phiếu do máy chủ cấp, dùng MỘT lần
  troLy: (b: { cau: string; conv: string } | { conv: string; phieu: string; chon: string } | { conv: string; xuat: "xlsx" }) => gui<TroLyTraLoi>("/api/tro-ly", b),
  // [ghi chú kỹ thuật] file kéo vào khung chat (thân nhị phân; máy chủ kiểm tên / đuôi / cỡ, tách chữ, giữ 24 giờ theo cuộc trò chuyện)
  troLyTaiLieu: (conv: string, f: File) => goi(`/api/tro-ly/tai-lieu?conv=${encodeURIComponent(conv)}&ten=${encodeURIComponent(f.name)}`,
    { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: f }).then((r) => doc<TaiLieuMo>(r)),
  // [ghi chú kỹ thuật] nút "Hỏi trên tài liệu này" của trang Dữ liệu: gắn tài liệu ĐÃ nạp kho (theo sha) vào cuộc trò chuyện — không nạp lại
  troLyMoKho: (conv: string, sha: string) => gui<{ ten: string; chu: string; thay_file_cu?: string | null }>("/api/tro-ly/tai-lieu/mo-kho", { conv, sha }),
  troLyTaiLieuDong: (conv: string) => gui<{ da_dong: boolean }>("/api/tro-ly/tai-lieu/dong", { conv }),
  troLyTaiLieuDangMo: (conv: string) => lay<{ ten: string | null }>(`/api/tro-ly/tai-lieu?conv=${encodeURIComponent(conv)}`),
  dem: () => lay<Dem>("/api/dem"),
  workflows: (ngay?: string, so?: number) => lay<WfRow[]>("/api/workflows" + loc(ngay, so)),
  workflow: (id: string) => lay<Workflow>(`/api/workflows/${id}`),
  runs: (ngay?: string, so?: number) => lay<RunRow[]>("/api/runs" + loc(ngay, so)),
  run: (s: string, sau?: string) => lay<RunDetail>(`/api/runs/${s}` + (sau ? `?sau=${encodeURIComponent(sau)}` : "")),
  cost: (s: string) => lay<Cost>(`/api/runs/${s}/cost`),
  // [ghi chú kỹ thuật] kết quả cuối từng khối, máy chủ đọc đủ + chuyển repr / JSON thành dữ liệu (may_chu.py ket_qua_luot)
  ketQua: (s: string) => lay<Record<string, KetQuaKhoi>>(`/api/runs/${s}/ket-qua`),
  // [ghi chú kỹ thuật] file kết quả của lượt (không có khoá / URL kho) + link tải qua máy chủ trang (link minio:9000 không mở được từ máy)
  tepLuot: (s: string) => lay<TepLuot[]>(`/api/runs/${s}/tep`),
  truyVan: (s: string) => lay<TruyVan>(`/api/runs/${s}/truy-van`),   // [ghi chú kỹ thuật] câu SQL lượt đã gọi (chỉ đọc bản ghi)
  moHinhKhoi: () => lay<MoHinhKhoi>("/api/mo-hinh-khoi"),   // [ghi chú kỹ thuật]
  dauVet: (s: string) => lay<DauVet>(`/api/runs/${s}/dau-vet`),   // [ghi chú kỹ thuật] dấu vết lượt chạy (dau_vet.py)
  linkTai: (s: string, tep: string) => `/api/tep/tai-ve?phien=${encodeURIComponent(s)}&tep=${encodeURIComponent(tep)}`,
  check: (workflow: Workflow) => gui<{ kiem: CheckItem[] }>("/api/check", { workflow }),
  save: (workflow: Workflow) => gui<{ workflow_id: string; version: number; kiem: CheckItem[]; loi_dan_dien_lai?: number }>("/api/save", { workflow }),
  start: (workflow_id: string, dv?: string | null) => gui<{ session: string; version: number }>("/api/run", dv ? { workflow_id, data_version: dv } : { workflow_id }),
  refreshBlocks: () => gui<{ blocks: number }>("/api/blocks/refresh", {}),
  approve: (session: string, node: string, decision: "dong_y" | "tu_choi") => gui<{ http: number; phan_hoi: unknown }>("/api/approve", { session, node, decision }),
};

export const DANG_CHAY = new Set(["RUNNING", "QUEUED", "PENDING", "PAUSED"]);
export const LUOT_VN: Record<string, string> = {
  RUNNING: "đang chạy", QUEUED: "đang xếp hàng", PENDING: "đang chờ", PAUSED: "tạm dừng", SUCCESS: "thành công",
  COMPLETED: "thành công", FAILED: "thất bại", CANCELLED: "đã huỷ", STOPPED: "đã dừng",
};
export const ms = (ts?: string | null) => (ts ? Date.parse(String(ts).replace(" ", "T").replace(/(\.\d{3})\d+/, "$1")) : NaN);
export const giay = (x: number | null | undefined) =>
  x == null || !isFinite(x) ? "—" : (x / 1000).toLocaleString("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " s";
export const usd = (x: number | null | undefined) =>
  x == null ? "chưa có giá" : x === 0 ? "0 USD" : x < 0.01 ? x.toLocaleString("vi-VN", { maximumSignificantDigits: 2 }) + " USD" : x.toLocaleString("vi-VN", { maximumFractionDigits: 4 }) + " USD";
export const ngayGio = (ts?: string | null) => {
  const t = ms(ts);
  return isNaN(t) ? "—" : new Date(t).toLocaleString("vi-VN", { hour12: false, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
};
