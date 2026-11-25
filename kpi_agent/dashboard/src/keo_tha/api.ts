import { apiFetch } from "../session";
// Chép từ e9db26a (NGUON.md) — sửa cho backend kpi_agent (bước 2b, backend/main.py cuối file).
// Hình chung v1 (hai A bàn 04/11, CHƯA): dây lưu đúng 4 khoá, from_port bắt buộc; `label` của khối, `id` của dây chỉ sống ở trang.

export type ParamDef = { name: string; label: string; kind: string | null; required: boolean; default: unknown };
export type BlockDef = {
  type: string; label: string; description: string; group: string; default_id: string;
  inputs: { name: string; label: string; required: boolean }[];
  outputs: { name: string; label: string }[];
  // Cổng công cụ (khối Trợ lý KPI, 10/11): nhận NHIỀU dây, mỗi dây một khối "Công cụ của trợ lý" — backend/workflow_view.py block_catalog
  tool_inputs?: { name: string; label: string; required: boolean; many: boolean }[];
  params: ParamDef[]; run_params: string[];
};
export type WfNode = { id: string; type: string; params?: Record<string, unknown>; position?: { x: number; y: number } };
export type WfEdge = { from_node: string; from_port: string; to_node: string; to_port: string };
export type Workflow = { workflow: string; nodes: WfNode[]; edges: WfEdge[] };
export type CheckItem = { muc: "loi" | "canh_bao"; code: string; node: string | null; field: string | null; message: string; suggestion: string };
export type Step = { node: string; type: string; status: "ok" | "stopped" | "awaiting_review" | "not_run"; seconds: number | null; error: string | null;
  issues?: { level: string; message: string }[] };
export type PortSummary = { text: string; messages?: { level: string; message: string }[] };
export type TrialRun = {
  trial_run_id: number; workflow_version_id: number; workflow_version?: string; cutoff: string; status: "ok" | "stopped" | "awaiting_review";
  steps: Step[]; errors: string[]; ports: Record<string, Record<string, PortSummary>>; team_total: number | null; created_at?: string;
};
export type VersionRow = { workflow_version_id: number; workflow: string; workflow_version: string; created_at: string };
export type SavedVersion = { workflow_version_id: number; workflow_version: string; is_new: boolean; workflow: Workflow; kiem: CheckItem[] };

async function doc<T>(r: Response): Promise<T> {
  const j = await r.json().catch(() => ({ refused: `HTTP ${r.status}` }));
  if (!r.ok) throw Object.assign(new Error(j.refused || `HTTP ${r.status}`), { body: j, status: r.status });
  return j as T;
}
const lay = <T,>(u: string) => apiFetch(u).then((r) => doc<T>(r));
const gui = <T,>(u: string, body: unknown) =>
  apiFetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => doc<T>(r));

export const api = {
  blocks: () => lay<{ blocks: BlockDef[] }>("/api/blocks").then((r) => r.blocks),
  current: () => lay<Workflow & { workflow_version: string }>("/api/workflow"),
  files: () => lay<{ files: string[] }>("/api/workflow/files").then((r) => r.files),
  versions: () => lay<{ current: string; versions: VersionRow[] }>("/api/workflow-versions"),
  version: (id: number) => lay<Workflow & { workflow_version_id: number; workflow_version: string; created_at: string }>(`/api/workflow-versions/${id}`),
  trialRuns: () => lay<{ runs: (TrialRun & { summary: { ports: TrialRun["ports"]; team_total: number | null } })[] }>("/api/workflow/trial-runs")
    .then((r) => r.runs.map((x) => ({ ...x, ...x.summary }) as TrialRun)),
  check: (workflow: Workflow) => gui<{ kiem: CheckItem[]; workflow_sha256: string | null }>("/api/workflow/check", { workflow }),
  save: (workflow: Workflow) => gui<SavedVersion>("/api/workflow/save", { workflow }),
  run: (workflow_version_id: number, cutoff: string) => gui<TrialRun>("/api/workflow/run", { workflow_version_id, cutoff }),
};

export const giay = (x: number | null | undefined) =>
  x == null || !isFinite(x) ? "—" : x.toLocaleString("vi-VN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " s";
export const dong = (x: number | null | undefined) => (x == null ? "—" : x.toLocaleString("vi-VN") + " đ");
export const ngayGio = (ts?: string | null) => {
  const t = ts ? Date.parse(ts) : NaN;
  return isNaN(t) ? "—" : new Date(t).toLocaleString("vi-VN", { hour12: false, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
};
