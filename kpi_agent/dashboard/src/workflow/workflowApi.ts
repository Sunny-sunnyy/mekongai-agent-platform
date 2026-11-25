import { apiFetch } from "../session";
// Bước 2a — 4 đường GET của backend (`kpi_agent/backend/main.py` cuối file, `workflow_view.py`). Trang CHỈ XEM: không có lệnh ghi nào ở đây.
// Hình dữ liệu theo mục 8.1–8.2 (phần repo này có).

export interface PortInfo { name: string; label: string; required?: boolean }   // label = chữ tiếng Việt; name = mã trong file
export interface ParamInfo { name: string; label: string; required: boolean; default: unknown; kind: string | null }
export interface BlockInfo {
  type: string;
  label: string;
  group: string;
  description: string;
  inputs: PortInfo[];
  outputs: PortInfo[];
  params: ParamInfo[];
  run_params: string[];
}
export interface WorkflowNode { id: string; type: string; params?: Record<string, unknown>; position?: { x: number; y: number } }
export interface WorkflowEdge { from_node: string; from_port: string; to_node: string; to_port: string }
export interface Workflow { workflow: string; workflow_version: string; nodes: WorkflowNode[]; edges: WorkflowEdge[] }

// status của bước do bộ chạy ghi: ok · stopped (khối dừng lượt) · awaiting_review (khối người duyệt chưa được soát — bước 3) · not_run (khối sau khối dừng / cổng)
export interface RunStep { node: string; type: string; status: "ok" | "stopped" | "awaiting_review" | "not_run"; seconds: number | null; error: string | null }
export interface PortSummary { text: string; messages?: { level: string; message: string }[] }
export interface WorkflowRunSummary {
  workflow_run_id: number;
  workflow_version_id: number;   // số thứ tự phiên bản trong bảng — trang hiện "phiên bản 2"; mã băm chỉ ở title
  workflow_version: string;
  cutoff: string;
  status: "ok" | "stopped";
  steps: RunStep[];
  errors: string[];
  bonus_run_id: number | null;
  created_at: string;
}
export interface WorkflowRunDetail extends WorkflowRunSummary {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  is_current: boolean;
  port_summaries: Record<string, Record<string, PortSummary>> | null;   // chỉ lượt đang hiện số (kết quả trong bộ nhớ backend)
}

async function getJson<T>(path: string): Promise<T> {
  const response = await apiFetch(path);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return (await response.json()) as T;
}

export const getBlocks = () => getJson<{ blocks: BlockInfo[] }>("/api/blocks");
export const getWorkflow = () => getJson<Workflow>("/api/workflow");
export const getWorkflowRuns = () => getJson<{ current_workflow_run_id: number; runs: WorkflowRunSummary[] }>("/api/workflow-runs");
export const getWorkflowRun = (runId: number) => getJson<WorkflowRunDetail>(`/api/workflow-runs/${encodeURIComponent(String(runId))}`);
