// THAY trangThai.ts của (bản đó dựng trạng thái từ sự kiện Temporal theo thời gian — NGUON.md).
// Bộ chạy của mình chạy xong trong MỘT lần gọi và trả danh sách bước (runner.run_workflow) ⇒ chỉ cần đổi bước ⇒ trạng thái khối.
import type { PortSummary, Step } from "./api";

export type TT = "cho" | "xong" | "loi" | "bo" | "duyet";
export type KhoiTT = { s: TT; giay: number | null; loi: string | null; ra: string | null; canhBao: string[] };
export const TT_VN: Record<TT, string> = { cho: "chưa chạy", xong: "xong", loi: "lỗi", bo: "bỏ qua — lượt đã dừng", duyet: "chờ duyệt — chưa soát" };
const TU_BUOC: Record<Step["status"], TT> = { ok: "xong", stopped: "loi", not_run: "bo", awaiting_review: "duyet" };

export function tuBuoc(steps: Step[], ports: Record<string, Record<string, PortSummary>> | undefined): Record<string, KhoiTT> {
  const st: Record<string, KhoiTT> = {};
  for (const b of steps) {
    const ra = Object.values(ports?.[b.node] || {}).map((p) => p.text).join(" · ") || null;
    const canhBao = (b.issues || Object.values(ports?.[b.node] || {}).flatMap((p) => p.messages || []))
      .filter((m) => m.level !== "info").map((m) => m.message);
    st[b.node] = { s: TU_BUOC[b.status], giay: b.seconds, loi: b.error, ra, canhBao };
  }
  return st;
}
