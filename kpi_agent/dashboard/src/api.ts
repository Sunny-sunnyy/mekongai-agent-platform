import { apiFetch } from "./session";
// Gọi backend chỉ đọc (E, `kpi_agent/backend/main.py`, :8110) và agent service (`POST /api/chat`, :8120) qua proxy của Vite.
// Kiểu dữ liệu theo `kpi_agent/backend/queries.py` (bản P1 03/11) + `docs/SPEC.md` mục 4, 5. Giao diện KHÔNG tính số: mọi số do backend trả.
// Tham số sai ⇒ backend trả HTTP 200 kèm {refused, params_used} (SPEC mục 4) ⇒ hiện lời từ chối, không có số.

export interface Refused {
  refused: string;
  params_used: Record<string, string | null>;
}
export const isRefused = (value: unknown): value is Refused =>
  typeof value === "object" && value !== null && typeof (value as Refused).refused === "string";

export interface Meta {
  period: string;
  cutoff: string;
  snapshot_at: string | null;
  data_source?: "raw" | "tracking";   // nguồn dữ liệu bản đang chạy: file đơn hàng / sổ Tracking chụp giữa tháng
  label: string;           // nhãn "TẠM TÍNH …" — code backend viết, trang vẽ nguyên văn (không nhờ mô hình nói)
  data_flags: string[];
  employee_ids: string[];
  kpis: string[];
  files_sha256: Record<string, string>;
  rule_version?: string;
  team_label?: string;
  working_days_left_after_cutoff?: number;
  // bậc 2a (E, mục 2) — thiếu (backend bậc 1) ⇒ trang giữ cách hiện "tạm tính" cũ
  current_run_id?: number;
  status?: RunStatus;
  status_label?: string;   // backend viết sẵn ("tạm tính — …" / "đã soát bởi … lúc … — vẫn chưa phải số chốt …"), trang vẽ nguyên văn
  // bước 3 (10/11, người vận hành tải nháp): tải được định dạng nào — chỉ khi khối xuất của lượt đang dùng đã chạy (cổng duyệt mở); lời từ chối do backend viết
  export?: { allowed: boolean; formats: string[]; reason: string | null };
}

// Bậc 2a: mỗi lần tính là một "bản tính" không sửa được; trạng thái suy từ sự kiện "Đã soát" (backend giữ, trang không suy)
export type RunStatus = "tam_tinh" | "da_soat";
export interface RunSummary {
  run_id: number;
  created_at: string;
  rule_version: string;
  data_snapshot: { cutoff: string; files_sha256: Record<string, string>; refreshed_at: string | null };
  code_sha: string;
  team_total: number;
  status: RunStatus;
  status_label: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  replaces: number | null;
  replaced_by: number | null;
}
export interface RunList {
  period: string;
  current_run_id: number;
  runs: RunSummary[];
}
export interface RunEvent {
  event_id: number | string;
  action: "reviewed";
  actor: string;
  note: string | null;
  at: string;
}
export interface RunDetail extends RunSummary {
  lines: { employee_id: string; kpi: string; pct: number; reached_tier: string | null; amount: number; reason: string }[];
  events: RunEvent[];
}
// File dữ liệu đổi so với lúc tính ⇒ backend trả {refused, inputs_match}, không có số ô
export interface RunVerify {
  inputs_match: Record<string, boolean>;
  refused?: string;
  cells_total?: number;
  cells_equal?: number;
  team_total_stored?: number;
  team_total_recomputed?: number;
}
// Bấm soát bị từ chối (đã soát / bị thay / không có / thiếu tên) ⇒ backend trả {refused} kèm HTTP 404 / 409 / 422 — không ném lỗi, hiện lời từ chối
export interface ReviewRefused {
  refused: string;
}

interface Envelope {
  period: string;
  cutoff: string;
  params_used: Record<string, string>;
  data_flags: string[];
}

// Một ô KPI của bảng đội — backend tính sẵn (trang chỉ vẽ): trạng thái, vị trí thanh (progress = thực hiện / ngưỡng mốc cao nhất) và vạch mốc.
export interface KpiCell {
  measure: "pct" | "value";       // KPI theo % chỉ tiêu, hay theo giá trị (LPPC, LPPC_Meat)
  pct: number | null;
  value: number | null;
  amount: number;
  status: "du_moc" | "moc_thap" | "chua_tien";   // tên: đủ mốc cao nhất · có tiền nhưng còn mốc kế · 0 đ
  progress: number;
  marks: number[];
  tier_labels?: string[];
  reached_tier?: string | null;
  next_tier: string | null;
  gap_to_next: number | null;
  gap_per_working_day_left: number | null;
  gap_rounded_up?: boolean;        // từ bản 3 "ít nhất N" (E): SỐ TỔNG gap_to_next đã làm tròn lên ⇒ mới ghi "ít nhất"
  gap_per_day_text?: string | null; // câu backend viết (cùng hàm /api/gap); null khi đủ mốc / KPI tỷ lệ
  target: number;
  actual: number | null;
}

export interface TeamBonus extends Envelope {
  employees: { employee_id: string; kpi_amounts?: Record<string, number>; kpis_paid?: number; business_salary: number;
    cells?: Record<string, KpiCell> }[];
  employee_count: number;
  kpi_count: number;
  team_total: number;
  business_salary_at_100_pct: number;
  // trang v2 (E thêm 03/11) — thiếu thì trang ẩn phần dùng nó, không tự tính
  team_max_total?: number;
  team_share_pct?: number;
  kpi_summary?: Record<string, { paid_count: number; full_count: number }>;
  kpis_nobody_paid?: string[];
  kpis?: string[];
  team_label?: string;
  working_days_left_after_cutoff?: number;
}

export interface KpiLine {
  kpi: string;
  target: number;
  actual: number | null;
  pct: number;
  reached_tier: string | null;
  amount: number;
  reason: string;
}
export interface EmployeeResult extends Envelope {
  employee_id: string;
  kpis: KpiLine[];
  business_salary: number;
  over_target_bonus: number;
  over_target_reason: string;
}

export interface Gap {
  kpi: string;
  next_tier: string;
  gap_to_next: number;
  gap_per_working_day_left: number | null;   // backend làm tròn LÊN ⇒ trang ghi "khoảng" (03/11, minor 2)
  amount_if_reached: number;
  amount_now: number;
  gap_rounded_up?: boolean;   // từ bản 3 (E): SỐ TỔNG đã làm tròn lên; gap_per_working_day_left = trung bình không làm tròn — trang không dựng câu từ hai trường này
  gap_per_day_text?: string | null; // câu backend viết sẵn (bậc "ít nhất N" bản 2): "cần thêm X trong N ngày … (trung bình Y mỗi ngày)"
}
export interface GapResult extends Envelope {
  working_days_total: number;
  working_days_left_after_cutoff: number;
  employees: { employee_id: string; gaps: Gap[] }[];
}

export interface Explain extends Envelope {
  employee_id: string;
  kpi: string;
  cong_van_section: string;
  measure: string;
  how_counted: string;
  target: number;
  actual: number | null;
  pct: number;
  tiers: { label: string; min: number; amount: number }[];
  amount: number;
  reason: string;
}

// Agent service (SPEC mục 5). `number_sources`: mỗi con số trong câu trả lời ⇒ tên các công cụ có số đó ("câu hỏi" = số người hỏi tự nêu).
export interface ChatReply {
  conversation_id: string;
  answer: string;
  stop: string;
  tool_calls: { name: string; params_used: Record<string, unknown> | null; refused: boolean }[];
  number_sources: { number: string; sources: string[] }[];
  warnings: unknown[];
  usd: number;
  seconds: number;
  // tính năng 4 (E): nguồn của câu trả lời do CODE điền — null khi backend không trả lời
  provenance?: { period: string; snapshot_at: string | null; cutoff: string; rule_version: string; status: string; status_label?: string; status_label_for_model?: string } | null;
  // bản 2 tab Theo dõi: mọi bước của lượt hỏi — agent điền từ bản ghi, trang chỉ vẽ
  trace?: Trace;
}

export interface CheckStep { ok: boolean; problems: string[] }
// Kết quả một lần gọi công cụ, máy chủ trợ lý sắp sẵn thành ô đơn + bảng (agent/result_view.py — có trần 50 dòng, 12 cột mỗi bảng)
export interface ToolView {
  facts: { key: string; value: string | number | boolean | null }[];
  tables: { key: string; columns: string[]; hidden_columns: number; total_rows: number; rows: (string | number | boolean | null)[][] }[];
  notes: string[];
  nested_left: string[];
  total_facts?: number; total_tables?: number; total_notes?: number;   // tổng trước khi trần cắt (máy chủ bản cũ không gửi)
}
export interface Trace {
  tool_calls: { name: string; label: string; params_used: Record<string, unknown> | null; refused: string | null; summary: string[]; view?: ToolView | null }[];
  requested_not_run: { name: string; args: Record<string, unknown> }[];
  number_check_first: CheckStep | null;
  number_check_final: CheckStep | null;
  owner_check_first: CheckStep | null;
  owner_check_final: CheckStep | null;
  retried_for_numbers: boolean;
  input_guard: string | null;
  model_calls: number;
  input_tokens: number;
  output_tokens: number;
  usd: number | null;
  seconds: number | null;
  stop: string;
  model?: string;       // tên mô hình + mức suy nghĩ của agent; agent bản cũ không gửi ⇒ trang hiện "—"
  reasoning?: string;
}

// Tab "Theo dõi" (bản 2 — backend/tracking_view.py): bảng đội nguyên văn + hạng + Thưởng thêm + ô lệch trang theo dõi của đội
export interface ExtraLine { employee_id: string; item: string; actual: number; target: number; reached: boolean; amount: number; how: string }
export interface Tracking extends TeamBonus {
  ranks: Record<string, Record<string, number>>;
  extra: { items: { id: string; title: string; label: string; measure?: string }[]; lines: ExtraLine[]; employee_total: Record<string, number>; team_total: number;
    ranks: Record<string, Record<string, number>> };
  deviations: { key: string; employee_id: string; note: string }[];
  known_reasons: Record<string, string>;
  compared_with_page: boolean;
  rank_note: string;
  gap_note: string;
  page_view?: PageView;
}
// Kiểu xem "8 KPI" theo bố cục trang theo dõi của đội (backend/tracking_view.py `page_view`) — % so với chỉ tiêu, không có tiền
export interface PageKpiRow { employee_id: string; rank: number; target: number; actual: number; pct_to_target: number; pct_rounded: number; on_track: boolean;
  shortfall: number; per_day: number; amount?: number; how?: string }
export interface PageKpi { page_title: string; our_name: string; rule_text: string | null; money: boolean; unit: string | null; tiers: { region: string | null; label: string; amount: number }[]; is_ratio: boolean;
  team: { target: number; actual: number; pct_to_target: number; pct_rounded: number; on_track: boolean }; rows: PageKpiRow[] }
export interface PageView { cutoff: string; days_done: number; days_total: number; days_left: number; time_progress_pct: number; kpis: Record<string, PageKpi>; extra?: Record<string, PageKpi>;
  ratio_total_note: string; per_day_note: string | null; shortfall_note: string }

// Tab "Đánh giá" (backend/evaluations.py): số đã đo của từng phiên bản, mỗi số kèm nguồn; value null = "chưa có" (lý do nằm ở source)
export interface Sourced<T = string | number | boolean> { value: T | null; source: string }
export interface EvalVersion {
  id: string; commit: string; note: string | null; label: string; label_reason: string;
  capitol_8: { correct: Sourced<number>; money_wrong: Sourced<number>; received: Sourced<number>; gate_passed: Sourced<string>; usd: Sourced<number> };
  m3: { passed: Sourced<string>; threshold: Sourced<number>; failed_questions: number[]; usd: Sourced<number>; machine_verdict: Sourced<string> };
  m3_reading: Sourced<number>;
  sealed_set_leak: Sourced<number>;
  t4: null | { run: string; git_head: Sourced<string>; model: Sourced<string>; reasoning: Sourced<string>; cutoff: Sourced<string>; tool_ok: Sourced<string>;
    args_ok: Sourced<string>; unsourced_final: Sourced<number>; false_block: Sourced<number>; ran: Sourced<string>; verdict_all: Sourced<boolean>;
    verdict_failed: string[]; usd: Sourced<number>; seconds_per_question: Sourced<number> };
  changes: null | { commits: string[]; commit_count: number | null; diffstat: string };
}
export interface Evaluations { versions: EvalVersion[]; noise_note: string; label_rule: string; built_at?: string }

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`;
    throw new Error(`${response.url}: ${detail}`);
  }
  return (await response.json()) as T;
}

const getJson = <T>(path: string) => apiFetch(path).then((r) => readJson<T>(r));
// encodeURIComponent: mã người dùng chọn đi nguyên vẹn tới backend (backend tự kiểm, kể cả chuỗi có "/")
const enc = encodeURIComponent;

export const getMeta = () => getJson<Meta>("/api/meta");
export const getTeamBonus = () => getJson<TeamBonus | Refused>("/api/team-bonus");
// Bảng cho trang (E, endpoint riêng — agent không đọc nên thêm trường không phải đo lại agent). Chưa có (404) ⇒ null ⇒ trang dùng /api/team-bonus.
export const getTeamBoard = () =>
  apiFetch("/api/team-board").then((r) => (r.status === 404 ? null : readJson<TeamBonus | Refused>(r)));
// Một KPI cho cả đội (backend cộng / đếm sẵn — trang không cộng ô): tổng tiền, số người có / chưa có tiền, mục công văn
export interface TeamKpi extends Envelope {
  kpi: string; cong_van_section: string; employee_count: number; team_amount_total: number; paid_count: number; unpaid_count: number;
  paid_employee_ids: string[]; unpaid_employee_ids: string[];
  by_tier: { reached_tier: string; employee_count: number; employee_ids: string[]; amount_total: number }[];
}
export const getTeamKpi = (kpi: string) => getJson<TeamKpi | Refused>(`/api/team-kpi?kpi=${enc(kpi)}`);
export const getTeamGap = () => getJson<GapResult | Refused>("/api/gap");
export const getTracking = () => getJson<Tracking | Refused>("/api/tracking");
export const getSpice = () => getJson<Spice | Refused>("/api/spice-loyalty");
// Từng khách 1 % gia vị (backend/spice_loyalty.py `customer_rows`). Có tên + mã khách ⇒ chỉ để vẽ, không gửi trợ lý.
export interface SpiceCustomer { customer_code: string; customer_name: string | null; employee_id: string; visit_days: string[]; baseline: number; sales: number;
  pct_of_baseline: number | null; group: "co_thuong" | "du_nen" | "gan_nen" | "duoi_70"; reward: number; need_more: number | null; reward_at_threshold: number | null }
export interface SpiceCustomers { period: string; cutoff: string; employee_id: string | null; rule_text: string; customers: number; groups: Record<string, string>;
  rows: SpiceCustomer[]; notes: string[] }
export const getSpiceCustomers = () => getJson<SpiceCustomers | Refused>("/api/spice-customers");
export const getEmployee = (id: string) => getJson<EmployeeResult | Refused>(`/api/employees/${enc(id)}`);
export const getEmployeeGap = (id: string) => getJson<GapResult | Refused>(`/api/gap?employee_id=${enc(id)}`);
export const getExplain = (id: string, kpi: string) => getJson<Explain | Refused>(`/api/explain/${enc(id)}/${enc(kpi)}`);

// Bậc 2a. Backend bậc 1 chưa có /api/runs (404) ⇒ null ⇒ trang ẩn khung bản tính.
export const getRuns = () => apiFetch("/api/runs").then((r) => (r.status === 404 ? null : readJson<RunList>(r)));
export const getRun = (runId: number) => getJson<RunDetail>(`/api/runs/${enc(runId)}`);
export const getRunVerify = (runId: number) => getJson<RunVerify>(`/api/runs/${enc(runId)}/verify`);
// Hành động ghi DUY NHẤT của trang. Gửi JSON (backend chỉ nhận application/json — rào trang lạ, mục 5).
export async function reviewRun(runId: number, reviewer: string, note: string): Promise<{ event: RunEvent; run: RunSummary } | ReviewRefused> {
  const response = await apiFetch(`/api/runs/${enc(runId)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(note ? { reviewer, note } : { reviewer }),
  });
  const body = (await response.json().catch(() => null)) as { refused?: unknown; detail?: unknown } | null;
  if (body && typeof body.refused === "string") return { refused: body.refused };
  if (!response.ok) throw new Error(`${response.url}: ${typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`}`);
  return body as unknown as { event: RunEvent; run: RunSummary };
}

// Tải bảng thưởng (khối xuất file — backend/export_report.py): GET, chỉ đọc bản tính đã lưu. Lỗi (404 / 409 / 422) ⇒ ném lời từ chối của máy chủ.
// Tên file lấy từ Content-Disposition (máy chủ đặt, chỉ chữ ASCII + số) — trang không tự đặt tên.
export type ExportKind = "xlsx" | "md" | "txt";
export async function downloadExport(kind: ExportKind, runId: number | undefined): Promise<string> {
  const response = await apiFetch(`/api/export/${kind}` + (runId ? `?run_id=${enc(runId)}` : ""));
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { refused?: unknown } | null;
    throw new Error(typeof body?.refused === "string" ? body.refused : `Tải không được (HTTP ${response.status})`);
  }
  const fileName = /filename="([A-Za-z0-9._-]+)"/.exec(response.headers.get("content-disposition") ?? "")?.[1] ?? `bang_thuong.${kind}`;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return fileName;
}

// Trình duyệt chỉ gửi mã hội thoại + câu mới; lịch sử giữ ở agent service (SPEC mục 3 — chặn lịch sử giả)
export const sendQuestion = (conversationId: string | null, question: string) =>
  apiFetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(conversationId ? { conversation_id: conversationId, question } : { question }),
  }).then((r) => readJson<ChatReply>(r));

// "1 % gia vị" (bậc 1 — backend/spice_loyalty.py): gom theo NV phụ trách, tiền trả cho KHÁCH (không cộng lương NV); 0 mã / tên khách
export interface Spice {
  period: string;
  rule_text: string;
  employees: { employee_id: string; customers: number; customers_reached: number; sales: number; reward: number }[];
  team_customers: number;
  team_customers_reached: number;
  team_reward: number;
  // 4 ô tổng như trang theo dõi của đội; `page` = số của chính trang đó khi backend bật có KPI_TRACKING_PAGE
  page_totals?: { team_baseline: number; team_sales: number; team_pct: number | null; potential_customers: number; potential_reward: number;
    page: { baseline: number; sales: number; pct: number } | null };
  paid_to: string;
}
// Tên + mã SM của nhân viên cho TRANG (backend `/api/names`). Backend bản cũ / cờ chỉ-hệ-thống (404) hoặc bảng tên thiếu (409) ⇒ null ⇒ trang chỉ hiện NVxx.
export type EmployeeNames = Record<string, { name: string; sm_code: string }>;
export const getNames = () =>
  apiFetch("/api/names").then((r) => (r.ok ? (r.json() as Promise<{ names: EmployeeNames }>).then((body) => body.names) : null));
export const getEvaluations = () => getJson<Evaluations | Refused>("/api/evaluations");

// Tab "Chi tiết điểm bán" + "Trà 365" (backend/outlets.py): CHỈ ĐẾM, không có tiền thưởng. Dòng có tên + địa chỉ cửa hàng ⇒ chỉ trang xem,
// không gửi vào câu hỏi cho trợ lý. Mỗi đường tải MỘT lần cả đội; lọc / tìm làm ở trình duyệt (chỉ lọc và đếm dòng, không cộng tiền).
export interface OutletRow {
  outlet_code: string; outlet_name: string | null; address: string | null; employee_id: string; visit_days: string[]; channel: string;
  name_from_route_sheet?: boolean;   // true = tên + địa chỉ lấy từ sheet tuyến của sổ (điểm chưa có đơn trong tháng)
  has_orders: boolean; sales: number; moq_reached: boolean; tea_cases: number; tea_sales: number; homey_reached: boolean;
  on_sales: number; premium_fish_sauce_sales: number;
  // tiền theo cách trang theo dõi của đội (13/11): sau chiết khấu, gồm hoá đơn xuất trễ; Trà 365 chỉ dòng đủ số lượng tối thiểu
  page_sales: number; page_tea_sales: number; page_premium_fish_sauce_sales: number;
}
export interface Outlets {
  period: string; cutoff: string; employee_id: string | null; notes: string[];
  summary: { outlets: number; moq_reached: number; moq_not_reached: number; sales: number; page_sales: number; without_orders: number };
  by_employee: { employee_id: string; outlets: number; moq_reached: number; moq_not_reached: number }[];
  rows: OutletRow[];
}
export type TeaRow = Pick<OutletRow, "outlet_code" | "outlet_name" | "employee_id" | "visit_days" | "channel" | "tea_cases">;
export interface Tea365 {
  period: string; cutoff: string; employee_id: string | null; notes: string[];
  summary: { customers: number; cases: number; customers_from_5: number };
  by_employee: { employee_id: string; customers: number; cases: number; customers_from_5: number }[];
  rows: TeaRow[];
}
// Thưởng thêm (ngoài 8 KPI) cho kiểu xem "Theo chỉ tiêu": đường sẵn có của công cụ 11 — backend tính sẵn lúc bật, mã che, trang không tính lại
export interface ExtraBonus {
  lines: (ExtraLine & { title: string; gioi_han?: string })[];
  items: { item: string; paid_count: number; team_total?: number; employee_id?: string }[];
  extra_bonus_team_total: number; team_total_8_kpi: number; team_total_8_kpi_and_extra: number; note?: string;
}
export const getExtraBonus = () => getJson<ExtraBonus | Refused>("/api/extra-bonus");
// Chế độ "một nguồn số" (trang chỉ đọc, VITE_KPI_READ_ONLY=1): máy chủ trợ lý nói hộp xem có khớp hộp chính không (không có số nào).
// Không khớp ⇒ mọi đường số trả 409 ⇒ trang KHÔNG hiện số, chỉ hiện một khung lý do (`loi` do máy chủ viết sẵn).
export interface XemStatus { khop: boolean; ly_do: string; loi: string | null; status: string | null; luc_so: string }
export const getXemStatus = () => getJson<XemStatus>("/api/xem/trang-thai");

// "Đơn được tính" của một nhân viên cho một chỉ tiêu (backend/order_lines.py). `unit`: "order" = mỗi dòng một đơn, "outlet" = mỗi dòng một
// cửa hàng. Được tính / lý do do backend xét; backend chỉ trả danh sách khi số "được tính" khớp số thực hiện của bảng thưởng (`matches_core`).
export interface OrderLineRow {
  outlet_code: string; outlet_name: string | null; counted: boolean; reasons: string[];
  order_num?: string; day?: string; channel?: string; line_count?: number; sales?: number; moq_skus?: number; counted_sales?: number; counted_lines?: number;   // unit = order
  address?: string | null; order_count?: number; moq_sales?: number;                                                                    // unit = outlet
}
export interface OrderLines {
  period: string; cutoff: string; employee_id: string; kpi: string; unit: "order" | "outlet"; notes: string[];
  summary: { rows: number; counted: number; not_counted: number; by_reason: Record<string, number>; counted_value: number; core_actual: number; matches_core: boolean;
    lines?: number; orders?: number };   // lines / orders: chỉ LPPC, LPPC_Meat (tử / mẫu của tỷ lệ)
  rows: OrderLineRow[];
}
export const getOrderLines = (employeeId: string, kpi: string) => getJson<OrderLines | Refused>(`/api/order-lines?employee_id=${enc(employeeId)}&kpi=${enc(kpi)}`);
// Tab Theo dõi › Hội viên — PHẦN DOANH SỐ (backend/membership.py). Có tên + mã khách ⇒ chỉ để vẽ, không gửi trợ lý.
export interface MemberRow { customer_code: string; customer_name: string | null; employee_id: string; visit_days: string[]; baseline: number; sales: number;
  pct_of_baseline: number | null; remaining: number; group: "du_doanh_so" | "gan_dat" | "dang_ban" | "chua_phat_sinh" }
export interface MemberCounts { customers: number; du_doanh_so: number; gan_dat: number; dang_ban: number; chua_phat_sinh: number; sales: number; baseline: number;
  pct_of_baseline: number | null }
export interface Membership { period: string; cutoff: string; employee_id: string | null; summary: MemberCounts; by_employee: (MemberCounts & { employee_id: string })[];
  rows: MemberRow[]; groups: Record<string, string>; not_built: string; notes: string[] }
export const getMembership = () => getJson<Membership | Refused>("/api/membership");
export const getOutlets = () => getJson<Outlets | Refused>("/api/outlets");
export const getTea365 = () => getJson<Tea365 | Refused>("/api/tea-365");
