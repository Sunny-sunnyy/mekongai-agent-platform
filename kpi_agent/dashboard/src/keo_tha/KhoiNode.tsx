// Chép từ e9db26a (NGUON.md) — bỏ phần AI / người duyệt / tiền / đối chiếu (khối của mình chỉ TÍNH).
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import { giay, type BlockDef } from "./api";
import { TT_VN, type KhoiTT } from "./trangThai";

export type KhoiData = {
  label: string; loai: string; params: Record<string, unknown>; block?: BlockDef;
  vao?: Record<string, { ten: string; mau: string }[]>;   // ô nhận ⇒ khối nguồn đang nối vào (màu dây)
  tt?: KhoiTT; soLoi?: number;
};
export type KhoiRF = Node<KhoiData, "khoi">;

// Màu theo nhóm của danh bạ mình (backend/workflow_view.py BLOCK_LABELS) — thay 5 nhóm của Capitol
export const MAU_NHOM: Record<string, string> = { "Đọc dữ liệu": "var(--g-doc)", "Kiểm": "var(--g-kiem)", "Tính": "var(--g-tinh)", "Duyệt": "var(--g-duyet)",
  "Xuất": "var(--g-xuat)", "Trợ lý": "var(--g-troly)" };
export const kyHieu = (b?: BlockDef) => (b?.group || "•").slice(0, 1).toUpperCase();

export function KhoiNode({ id, data, selected }: NodeProps<KhoiRF>) {
  const b = data.block, x = data.tt;
  const s = x?.s;
  let chu = "";
  if (x) {
    if (s === "loi") chu = "⚠ " + (x.loi || "lỗi");
    else if (s === "xong" && x.ra) chu = x.ra;
    else if (s === "bo") chu = "Không chạy: lượt đã dừng trước khi tới khối này.";
    else if (s === "duyet") chu = "Chờ giám sát bấm \"Đã soát\" bảng thưởng — chưa soát thì các khối sau không chạy.";
  }
  if (chu.length > 150) chu = chu.slice(0, 150) + "…";
  const lop = ["khoi", selected ? "chon" : "", s ? "s-" + s : "", data.soLoi ? "c-loi" : ""].join(" ");
  return (
    <div className={lop}>
      {!!data.soLoi && <span className="loi-kiem" title="Số lỗi kiểm của khối này">{data.soLoi} lỗi</span>}
      <div className="dau">
        <div className="ic" style={{ background: MAU_NHOM[b?.group || ""] || "var(--g-tinh)" }}>{kyHieu(b)}</div>
        <div style={{ minWidth: 0 }}>
          <div className="ten">{data.label}</div>
          <div className="loai">{b?.group} · mã <code>{id}</code></div>
        </div>
      </div>
      {(b?.inputs || []).map((i) => (
        <div className="cong" key={i.name}>
          <Handle type="target" position={Position.Left} id={i.name} className={i.required ? "bat-buoc" : ""} title={`${i.label} (${i.name}) — đúng một dây`} />
          {i.label}{i.required && <span className="bb">*</span>}
          {(data.vao?.[i.name] || []).length > 0 && (
            <span className="nguon" title={"Nhận từ: " + data.vao![i.name].map((v) => v.ten).join(" + ")}>
              {data.vao![i.name].map((v, k) => <i key={k} style={{ background: v.mau }} />)}
              {data.vao![i.name].length > 1 && <b>{data.vao![i.name].length} dây</b>}
            </span>
          )}
        </div>
      ))}
      {(b?.tool_inputs || []).map((i) => (
        // Cổng công cụ: nhiều dây, mỗi dây một công cụ trợ lý được dùng (loại dây suy từ cổng — hình chung điểm 8); bộ kiểm của máy chủ chặn dây sai
        <div className="cong cong-cu" key={i.name}>
          <Handle type="target" position={Position.Left} id={i.name} className={"o-cong-cu" + (i.required ? " bat-buoc" : "")}
            title={`${i.label} (${i.name}) — nhiều dây, mỗi dây một khối Công cụ của trợ lý`} />
          {i.label}{i.required && <span className="bb">*</span>}
          <span className="nguon" title={"Công cụ đang nối: " + (data.vao?.[i.name] || []).map((v) => v.ten).join(", ")}>
            <b>{(data.vao?.[i.name] || []).length} công cụ</b>
          </span>
        </div>
      ))}
      <div className="than">
        <div className="dong">
          {x ? <span className={"chip " + s}>{TT_VN[s!]}</span> : <span className="chip cho">chưa chạy</span>}
          <span className="phu so">{x?.giay != null ? giay(x.giay) : ""}</span>
        </div>
        {chu && <div className="song">{chu}</div>}
      </div>
      {(b?.outputs || []).map((o) => (
        <div className="cong ra" key={o.name}>
          {o.label}
          <Handle type="source" position={Position.Right} id={o.name} title={`${o.label} (${o.name})`} />
        </div>
      ))}
    </div>
  );
}
