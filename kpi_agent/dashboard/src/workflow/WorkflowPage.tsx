import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BaseEdge, Background, BackgroundVariant, Controls, Handle, Position, ReactFlow, type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useLoad } from "../useLoad";
import { columnsOf, layoutNodes } from "./layout";
import {
  getBlocks, getWorkflow, getWorkflowRun, getWorkflowRuns,
  type BlockInfo, type PortSummary, type RunStep, type WorkflowEdge, type WorkflowNode, type WorkflowRunDetail,
} from "./workflowApi";
import "./workflow.css";

// Trang CHỈ XEM workflow (bước 2a — thiết kế mục 2–5).
// Vẽ đúng PHIÊN BẢN của lượt đang xem (lấy nodes / edges từ /api/workflow-runs/{id}, không từ file hiện tại) ⇒ đổi lượt không trộn cài đặt lượt khác.
// Không kéo, không nối, không xoá: mọi thao tác sửa để bước 2b. Trang không tự tính số — số + trạng thái do backend trả.

type StepStatus = RunStep["status"] | "waiting";
// Trạng thái luôn có hình + chữ, không chỉ màu (thiết kế mục 2). "not_run" sau khối dừng = "bỏ qua — lượt đã dừng" (mục 4 ý 7)
const STATUS_TEXT: Record<StepStatus, { icon: string; text: string }> = {
  ok: { icon: "✓", text: "xong" },
  stopped: { icon: "×", text: "lỗi" },
  not_run: { icon: "–", text: "bỏ qua — lượt đã dừng" },
  awaiting_review: { icon: "⏸", text: "chờ duyệt — bấm \"Đã soát\"" },
  waiting: { icon: "○", text: "chưa có lượt chạy" },
};

interface BlockNodeData extends Record<string, unknown> {
  label: string;
  typeName: string;
  inputs: { name: string; label: string; required: boolean }[];
  outputs: { name: string; label: string }[];
  status: StepStatus;
  seconds: number | null;
}

function BlockNode({ data, selected }: NodeProps<Node<BlockNodeData>>) {
  const status = STATUS_TEXT[data.status];
  // Cổng xếp dưới tên khối, tên cổng nằm TRONG khối (ảnh 1440 lần 1: tên đặt ngoài khối đè lên dây + khối bên cạnh)
  const rows = Math.max(data.inputs.length, data.outputs.length);
  return (
    <div className={`wf-block status-${data.status}${selected ? " is-selected" : ""}`} title={data.typeName}
      style={{ minHeight: PORT_TOP + rows * PORT_GAP + 30 }}>
      <div className="wf-block-title">{data.label}</div>
      <div className="wf-block-status" style={{ marginTop: rows * PORT_GAP + 8 }}>
        <span aria-hidden="true" className="wf-icon">{status.icon}</span> {status.text}
        {data.seconds !== null && <span className="wf-seconds">{data.seconds.toFixed(2).replace(".", ",")} s</span>}
      </div>
      {/* Cổng vào bên trái, ra bên phải (thiết kế mục 3); bắt buộc = chấm đặc. Tên cổng hiện cạnh chấm khi khối được chọn */}
      {data.inputs.map((port, i) => (
        <Handle key={`in-${port.name}`} id={port.name} type="target" position={Position.Left} isConnectable={false}
          className={port.required ? "wf-port required" : "wf-port"} style={{ top: PORT_TOP + i * PORT_GAP }} title={`đầu vào: ${port.label} (${port.name})`}>
          {selected && <span className="wf-port-name left">{port.label}</span>}
        </Handle>
      ))}
      {data.outputs.map((port, i) => (
        <Handle key={`out-${port.name}`} id={port.name} type="source" position={Position.Right} isConnectable={false}
          className="wf-port required" style={{ top: PORT_TOP + i * PORT_GAP }} title={`đầu ra: ${port.label} (${port.name})`}>
          {selected && <span className="wf-port-name right">{port.label}</span>}
        </Handle>
      ))}
    </div>
  );
}

// Dây "nhảy cột" (vd Đọc bản luật ⇒ Tính thưởng): đường cong thường đi ngang qua sau các khối cột giữa, đoạn lộ ra ở khe trông như hai khối giữa nối
// nhau (GĐ2-trang). Đi vòng phía TRÊN mọi khối: lên ⇒ ngang ⇒ xuống. `lane` tách các dây vòng khỏi nhau.
// (lớp CSS wf-edge / ran do thư viện gắn lên thẻ <g> bọc dây — `.wf-edge path` vẫn áp)
function DetourEdge({ id, sourceX, sourceY, targetX, targetY, data }: EdgeProps<Edge<{ top: number }>>) {
  const top = data?.top ?? Math.min(sourceY, targetY) - 40;
  const path = `M ${sourceX} ${sourceY} C ${sourceX + 40} ${sourceY} ${sourceX + 40} ${top} ${sourceX + 80} ${top} `
    + `L ${targetX - 80} ${top} C ${targetX - 40} ${top} ${targetX - 40} ${targetY} ${targetX} ${targetY}`;
  return <BaseEdge id={id} path={path} />;
}

const NODE_TYPES = { block: BlockNode };
const EDGE_TYPES = { detour: DetourEdge };
const DETOUR_LANE = 14;   // px giữa hai dây vòng
const PORT_TOP = 40;   // px từ đỉnh khối tới cổng đầu tiên (dưới dòng tên khối)
const PORT_GAP = 18;
// Dưới 600 px: KHÔNG thu nhỏ sơ đồ — hiện danh sách thẻ khối theo thứ tự chạy (04/11 sau khi chữ 5,6 px ở 390 px)
const LIST_QUERY = "(max-width: 600px)";

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.matchMedia(LIST_QUERY).matches);
  useEffect(() => {
    const query = window.matchMedia(LIST_QUERY);
    const update = () => setNarrow(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return narrow;
}

function toGraph(nodes: WorkflowNode[], edges: WorkflowEdge[], catalog: Record<string, BlockInfo>, steps: RunStep[] | undefined) {
  const positions = layoutNodes(nodes, edges);
  const column = columnsOf(nodes, edges);
  const topOfAll = Math.min(...Object.values(positions).map((point) => point.y)) - 30;
  let lane = 0;
  const stepOf = Object.fromEntries((steps ?? []).map((step) => [step.node, step]));
  const flowNodes: Node<BlockNodeData>[] = nodes.map((node) => {
    const block = catalog[node.type];
    const step = stepOf[node.id];
    return {
      id: node.id, type: "block", position: positions[node.id], draggable: false, connectable: false, deletable: false,
      data: {
        label: block?.label ?? node.type, typeName: `${node.id} · ${node.type}`,   // mã kỹ thuật chỉ hiện khi rê chuột
        inputs: (block?.inputs ?? []).map((port) => ({ name: port.name, label: port.label, required: port.required ?? true })),
        outputs: (block?.outputs ?? []).map((port) => ({ name: port.name, label: port.label })),
        status: step ? step.status : "waiting", seconds: step?.seconds ?? null,
      },
    };
  });
  const flowEdges: Edge[] = edges.map((edge) => {
    const ran = stepOf[edge.from_node]?.status === "ok";
    const skips = column[edge.to_node] - column[edge.from_node] > 1;
    return {
      id: `${edge.from_node}.${edge.from_port}->${edge.to_node}.${edge.to_port}`, source: edge.from_node, sourceHandle: edge.from_port,
      target: edge.to_node, targetHandle: edge.to_port, deletable: false, className: ran ? "wf-edge ran" : "wf-edge",
      ...(skips ? { type: "detour", data: { top: topOfAll - DETOUR_LANE * lane++ } } : {}),
    };
  });
  return { flowNodes, flowEdges };
}

export function WorkflowPage() {
  const blocks = useLoad(getBlocks);
  const workflow = useLoad(getWorkflow);
  const runs = useLoad(getWorkflowRuns);
  const [chosenRun, setChosenRun] = useState<number | null>(null);
  const runId = chosenRun ?? runs.data?.current_workflow_run_id ?? null;
  const loadRun = useCallback(() => (runId === null ? Promise.resolve(null) : getWorkflowRun(runId)), [runId]);
  const run = useLoad(loadRun);
  const [selected, setSelected] = useState<string | null>(null);

  const catalog = useMemo(() => Object.fromEntries((blocks.data?.blocks ?? []).map((block) => [block.type, block])), [blocks.data]);
  const detail = run.data ?? undefined;
  // Có lượt ⇒ vẽ phiên bản của lượt đó; chưa có ⇒ file đang dùng
  const source = detail ?? workflow.data;
  const graph = useMemo(() => (source ? toGraph(source.nodes, source.edges, catalog, detail?.steps) : null), [source, catalog, detail]);
  const loadError = blocks.error ?? workflow.error ?? runs.error ?? run.error;
  const narrow = useNarrow();

  return (
    <main className="wf-page">
      <header className="wf-head">
        <div>
          <h1 title={workflow.data?.workflow}>{WORKFLOW_TITLES[workflow.data?.workflow ?? ""] ?? "Quy trình"}</h1>
          <p className="wf-sub">
            {detail ? <>Đang xem lượt số {detail.workflow_run_id} — <span title={versionCode(detail.workflow_version)}>phiên bản {detail.workflow_version_id}</span>, ngày chốt {dayText(detail.cutoff)}</> : "Chưa có lượt chạy"}
            {" · "}chỉ xem, không sửa
          </p>
        </div>
        <a className="wf-link" href="./">Về trang thưởng</a>
      </header>
      {loadError && <section className="wf-notice">Không tải được dữ liệu quy trình ({loadError.message}). Bật backend :8110 rồi tải lại trang.</section>}
      <div className="wf-body">
        <aside className="wf-catalog" aria-label="Danh bạ khối">
          <h2>Danh bạ khối</h2>
          {groupBlocks(blocks.data?.blocks ?? []).map(([group, items]) => (
            <section key={group}>
              <h3>{group}</h3>
              <ul>{items.map((block) => <li key={block.type}><b>{block.label}</b><span>{block.description}</span></li>)}</ul>
            </section>
          ))}
        </aside>
        {narrow && graph && source ? (
          <BlockList nodes={graph.flowNodes} edges={source.edges} selected={selected} onSelect={setSelected} />
        ) : (
        <section className="wf-canvas" aria-label="Khung vẽ quy trình">
          {graph && (
            // Giữ dòng ghi công "React Flow" ở góc (thư viện xin chỉ ẩn khi mua bản Pro — B thấy cảnh báo console 04/11)
            // minZoom 0,2: mặc định 0,5 làm sơ đồ 3 cột tràn khung hẹp; dưới 600 px trang không vẽ sơ đồ (danh sách thẻ ở trên)
            <ReactFlow nodes={graph.flowNodes} edges={graph.flowEdges} nodeTypes={NODE_TYPES} edgeTypes={EDGE_TYPES} fitView minZoom={0.2}
              nodesDraggable={false} nodesConnectable={false} edgesFocusable={false} deleteKeyCode={null}
              onNodeClick={(_, node) => setSelected(node.id)} onPaneClick={() => setSelected(null)}
              ariaLabelConfig={ARIA_VI}>
              <Background variant={BackgroundVariant.Dots} gap={16} color="#E7EAF0" />
              <Controls showInteractive={false} />
            </ReactFlow>
          )}
        </section>
        )}
        <aside className="wf-panel" aria-label="Chi tiết khối">
          {selected && source ? <BlockPanel nodeId={selected} nodes={source.nodes} edges={source.edges} catalog={catalog} detail={detail} />
            : <p className="wf-hint">Bấm vào một khối để xem cổng vào / ra, số giây chạy và lời lỗi của lượt đang xem.</p>}
        </aside>
      </div>
      <footer className="wf-runs" aria-label="Các lượt chạy">
        <h2>Lượt chạy</h2>
        <ul>
          {(runs.data?.runs ?? []).map((item) => (
            <li key={item.workflow_run_id}>
              <button type="button" aria-pressed={item.workflow_run_id === runId} onClick={() => setChosenRun(item.workflow_run_id)}>
                <span aria-hidden="true">{item.status === "ok" ? "✓" : "×"}</span> Lượt {item.workflow_run_id} — {item.status === "ok" ? "xong" : "dừng"} ·{" "}
                <span title={versionCode(item.workflow_version)}>phiên bản {item.workflow_version_id}</span> · ngày chốt {dayText(item.cutoff)}{item.workflow_run_id === runs.data?.current_workflow_run_id ? " · đang hiện số" : ""}
              </button>
            </li>
          ))}
        </ul>
      </footer>
    </main>
  );
}

// Màn hẹp: mỗi khối một thẻ theo thứ tự chạy (= thứ tự file) — tên, trạng thái hình + chữ, giây, "nhận từ"; bấm thẻ ⇒ chi tiết ở bảng dưới
function BlockList({ nodes, edges, selected, onSelect }: {
  nodes: Node<BlockNodeData>[]; edges: WorkflowEdge[]; selected: string | null; onSelect: (id: string) => void;
}) {
  const labelOf = Object.fromEntries(nodes.map((node) => [node.id, node.data.label]));
  return (
    <ol className="wf-list" aria-label="Các khối theo thứ tự chạy">
      {nodes.map((node) => {
        const status = STATUS_TEXT[node.data.status];
        const from = [...new Set(edges.filter((edge) => edge.to_node === node.id).map((edge) => labelOf[edge.from_node] ?? edge.from_node))];
        return (
          <li key={node.id} data-id={node.id}>
            <button type="button" className={`wf-card status-${node.data.status}`} aria-pressed={selected === node.id} title={node.data.typeName}
              onClick={() => onSelect(node.id)}>
              <span className="wf-block-title">{node.data.label}</span>
              <span className="wf-card-status">
                <span aria-hidden="true" className="wf-icon">{status.icon}</span> {status.text}
                {node.data.seconds !== null && <span className="wf-seconds">{node.data.seconds.toFixed(2).replace(".", ",")} s</span>}
              </span>
              <span className="wf-card-from">{from.length ? `nhận từ: ${from.join(", ")}` : "tự đọc file — không nhận từ khối nào"}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function groupBlocks(blocks: BlockInfo[]): [string, BlockInfo[]][] {
  const groups = new Map<string, BlockInfo[]>();
  for (const block of blocks) groups.set(block.group, [...(groups.get(block.group) ?? []), block]);
  return [...groups.entries()];
}

function BlockPanel({ nodeId, nodes, edges, catalog, detail }: {
  nodeId: string; nodes: WorkflowNode[]; edges: WorkflowEdge[]; catalog: Record<string, BlockInfo>; detail: WorkflowRunDetail | undefined;
}) {
  const node = nodes.find((item) => item.id === nodeId);
  if (!node) return null;
  const block = catalog[node.type];
  const step = detail?.steps.find((item) => item.node === nodeId);
  const status = STATUS_TEXT[step ? step.status : "waiting"];
  const summaries = detail?.port_summaries;
  const incoming = edges.filter((edge) => edge.to_node === nodeId);
  return (
    <div>
      <h2 title={`${node.id} · ${node.type}`}>{block?.label ?? node.type}</h2>
      <p className="wf-hint">{block?.description}</p>
      <p className={`wf-status status-${step ? step.status : "waiting"}`}>
        <span aria-hidden="true">{status.icon}</span> {status.text}
        {step?.seconds !== null && step?.seconds !== undefined && <> · {step.seconds.toFixed(3).replace(".", ",")} giây</>}
      </p>
      {step?.error && <p className="wf-error">{step.error}</p>}
      <h3>Cài đặt</h3>
      {(block?.params ?? []).length === 0 ? <p className="wf-hint">Khối này không có ô cài đặt.</p> : (
        <dl>{block!.params.map((param) => (
          <div key={param.name}><dt>{param.label}</dt><dd>{formatValue(node.params?.[param.name] ?? param.default)}</dd></div>
        ))}</dl>
      )}
      <h3>Đầu vào</h3>
      {incoming.length === 0 ? <p className="wf-hint">Khối này không nhận đầu vào — tự đọc file.</p> : (
        <ul>{incoming.map((edge) => (
          <li key={edge.to_port} title={`${edge.from_node}.${edge.from_port} ⇒ ${edge.to_port}`}>
            <b>{portLabel(block, edge.to_port)}</b> từ khối "{blockLabel(nodes, catalog, edge.from_node)}": {summaryText(summaries?.[edge.from_node]?.[edge.from_port])}
          </li>
        ))}</ul>
      )}
      <h3>Đầu ra</h3>
      <ul>{(block?.outputs ?? []).map((port) => (
        <li key={port.name} title={port.name}><b>{port.label}</b>: {summaryText(summaries?.[nodeId]?.[port.name])}
          {summaries?.[nodeId]?.[port.name]?.messages && (
            <ul>{summaries[nodeId][port.name].messages!.map((item, i) => <li key={i}>{LEVEL_TEXT[item.level] ?? item.level}: {item.message}</li>)}</ul>
          )}
        </li>
      ))}</ul>
      {detail && !detail.is_current && <p className="wf-hint">Lượt cũ: backend chỉ giữ kết quả khối của lượt đang hiện số — ở đây chỉ có trạng thái, giây và lời lỗi.</p>}
    </div>
  );
}

function summaryText(summary: PortSummary | undefined): string {
  return summary ? summary.text : "—";
}

function formatValue(value: unknown): string {
  return value === null || value === undefined ? "để trống" : String(value);
}


// Chữ trên trang tiếng Việt; mã kỹ thuật (type, tên cổng, tên file workflow) chỉ hiện khi rê chuột (mục 5)
const WORKFLOW_TITLES: Record<string, string> = { kpi_thuong_nvbh: "Quy trình tính thưởng nhân viên bán hàng" };
const LEVEL_TEXT: Record<string, string> = { error: "lỗi", warning: "cảnh báo", info: "thông tin" };
const ARIA_VI = {
  "controls.ariaLabel": "Điều khiển khung vẽ", "controls.zoomIn.ariaLabel": "Phóng to", "controls.zoomOut.ariaLabel": "Thu nhỏ",
  "controls.fitView.ariaLabel": "Vừa khung", "controls.interactive.ariaLabel": "Khoá / mở khung vẽ", "handle.ariaLabel": "Cổng",
  "node.a11yDescription.default": "Bấm để xem chi tiết khối.", "node.a11yDescription.keyboardDisabled": "Bấm để xem chi tiết khối.",
  "edge.a11yDescription.default": "Dây nối hai khối.",
};

function versionCode(version: string): string {
  return version.includes("@") ? version.split("@")[1] : version;   // "kpi_thuong_nvbh@aa67f7dc" ⇒ "aa67f7dc"
}

function dayText(iso: string): string {
  const [y, m, d] = iso.split("-");
  return d && m && y ? `${d}/${m}/${y}` : iso;
}

function portLabel(block: BlockInfo | undefined, port: string): string {
  return [...(block?.inputs ?? []), ...(block?.outputs ?? [])].find((item) => item.name === port)?.label ?? port;
}

function blockLabel(nodes: WorkflowNode[], catalog: Record<string, BlockInfo>, nodeId: string): string {
  const node = nodes.find((item) => item.id === nodeId);
  return (node && catalog[node.type]?.label) ?? nodeId;
}
