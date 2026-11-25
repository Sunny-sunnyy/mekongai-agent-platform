import type { WorkflowEdge, WorkflowNode } from "./workflowApi";

// Tự xếp khối khi file workflow CHƯA có `position` (tự tính, không thêm dagre — mục 2).
// Cột = độ sâu dài nhất tính từ khối nguồn; hàng = thứ tự trong file. Bộ chạy đã bắt mọi dây đi từ khối đứng TRƯỚC sang khối đứng SAU
// (runner.check_workflow) ⇒ duyệt một lượt theo thứ tự file là đủ, không có vòng. Khối đã có `position` giữ nguyên toạ độ (không ghi đè).
export const COLUMN_GAP = 280;
export const ROW_GAP = 120;

// Cột của mỗi khối = độ sâu dài nhất từ khối nguồn. Dùng cho bố cục và để biết dây nào "nhảy cột" (đi vòng phía trên, không khuất sau khối giữa).
export function columnsOf(nodes: WorkflowNode[], edges: WorkflowEdge[]): Record<string, number> {
  const depth: Record<string, number> = {};
  for (const node of nodes) {
    const parents = edges.filter((edge) => edge.to_node === node.id).map((edge) => depth[edge.from_node] ?? 0);
    depth[node.id] = parents.length ? Math.max(...parents) + 1 : 0;
  }
  return depth;
}

export function layoutNodes(nodes: WorkflowNode[], edges: WorkflowEdge[]): Record<string, { x: number; y: number }> {
  const depth = columnsOf(nodes, edges);
  const rowsUsed: Record<number, number> = {};
  const positions: Record<string, { x: number; y: number }> = {};
  for (const node of nodes) {
    if (node.position) {
      positions[node.id] = node.position;
      continue;
    }
    const column = depth[node.id];
    const row = rowsUsed[column] ?? 0;
    rowsUsed[column] = row + 1;
    positions[node.id] = { x: column * COLUMN_GAP, y: row * ROW_GAP };
  }
  return positions;
}
