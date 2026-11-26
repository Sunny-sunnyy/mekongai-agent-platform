import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import { giay, usd, useMoHinhKhoi, moHinhThat, type BlockDef } from "./api";
import { TT_VN, tomTatRao, lyDoRaoChan, loiThuong, type KhoiTT } from "./trangThai";

export type KhoiData = {
  label: string; loai: string; params: Record<string, unknown>; block?: BlockDef;
  vao?: Record<string, { ten: string; mau: string }[]>;   // ô nhận ⇒ các khối nguồn đang nối vào (màu dây)
  stt?: number;   // số thứ tự bước theo dây (App.sttKhoi) — chỉ để hiển thị
  soCongCu?: number;   // [ghi chú] số ô tick công cụ đang bật — chỉ để vẽ cổng “Công cụ” ở đáy khối
  tt?: KhoiTT; dungLai?: string | null; raoChan?: boolean; dur?: number | null; doiChieu?: { tong: number; khong: number }; tien?: number | null; tienNgam?: boolean; soLoi?: number; canhBao?: string;
};
export type KhoiRF = Node<KhoiData, "khoi">;
// [ghi chú] THẺ CÔNG CỤ — khối ẢO, chỉ để nhìn: sinh từ ô tick công cụ đang bật của khối chủ, KHÔNG có trong dữ liệu workflow (không lưu, không chạy).
export type CongCuRF = Node<{ ten: string; khoi: string; o: string; goc: string; ds?: string }, "congCu">;
export const CONG_CONG_CU = "__cong_cu";   // mã cổng ở đáy khối chủ; không có trong danh mục cổng ⇒ không nối tay được
const BAT_O = (v: unknown) => v === true || (typeof v === "string" && ["true", "1", "yes", "on"].includes(v.trim().toLowerCase()));
/** Các ô tick công cụ ĐANG BẬT của một khối: ô kiểu bật / tắt có tên tool_* (mọi loại khối, không riêng Agent). */
export const oCongCuBat = (b: BlockDef | undefined, params: Record<string, unknown>) =>
  (b?.params || []).filter((p) => p.kind === "BinaryToggle" && p.name.startsWith("tool_") && BAT_O(params[p.name]));
export const coTraWebExa = (params: Record<string, unknown>): boolean => {
  const m = params?.mcp_servers;
  if (!m) return false;
  const s = typeof m === "string" ? m : JSON.stringify(m);
  return /mcp-exa|exa_web_search/i.test(s);
};
/** Nhãn ô ⇒ tên ngắn lời thường: bỏ tiền tố “Công cụ:” và phần trong ngoặc. */
export const tenCongCuNgan = (nhan: string) => {
  const t = nhan.replace(/^\s*Công cụ\s*:\s*/i, "").replace(/\s*\([^)]*\)\s*/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : nhan;
};
export function CongCuNode({ data }: NodeProps<CongCuRF>) {
  return (
    <div className="the-cong-cu" title={`Công cụ khối AI này được phép gọi: ${data.ten}.${data.ds ? "\nGồm:\n" + data.ds : ""}\nBấm để mở ô bật / tắt công cụ. (Thẻ chỉ để nhìn — không phải một khối của workflow.)`}>
      <Handle type="source" position={Position.Top} id="ra" isConnectable={false} />
      <span className="ten-cc"><span className="tien-to-cc">Công cụ: </span>{data.ten}</span>
    </div>
  );
}

export const MAU_NHOM: Record<string, string> = {
  input: "var(--g-input)", agent: "var(--g-agent)", governance: "var(--g-governance)", output: "var(--g-output)", tool: "var(--g-tool)",
};
export const CHU_NHOM: Record<string, string> = { input: "Vào", agent: "AI", governance: "KS", output: "Ra", tool: "Cụ" }; // KS = kiểm soát (duyệt, rào chắn, chấm, che)
export const kyHieu = (b?: BlockDef) => (b?.type === "human_intervention_node" ? "✋" : CHU_NHOM[b?.group_key || ""] || "•");

// [ghi chú] chuỗi kết quả dạng dict có cau_tra_loi (repr Python hoặc JSON,
// có thể bị cắt 4.000 ký tự) ⇒ lấy riêng câu trả lời, bỏ ** và xuống dòng, 1–2 dòng ĐẦU. Dict khác ⇒ 1 câu chỉ đường sang màn kết quả.
function tomTatKetQua(ra: string): { chu: string; dau: boolean } | null {
  const rao = tomTatRao(ra);   // [ghi chú] khối Kiểm số bằng code: một dòng ĐẠT / CHẶN thay vì JSON
  if (rao) return { chu: rao, dau: true };
  // link ký sẵn kho nội bộ (minio:9000 — có mã khoá truy cập + chữ ký) ⇒ không in trên thẻ (Playwright C 11/11 bắt: đuôi 150 ký tự chứa X-Amz-Signature)
  if (/^\s*https?:\/\/minio:9000\//.test(ra)) return { chu: "Đã tạo file — tải ở “Kết quả lượt chạy” ⇒ File tải về.", dau: true };
  const m = ra.match(/^\s*\{\s*(['"])cau_tra_loi\1\s*:\s*(['"])((?:\\.|(?!\2)[^\\])*)\2/);
  if (m) {
    const c = m[3].replace(/\\n/g, " ").replace(/\\t/g, " ").replace(/\\(['"\\])/g, "$1").replace(/\*\*/g, "").replace(/\s+/g, " ").trim();
    return { chu: c, dau: true };
  }
  if (/^\s*[{[]\s*['"]/.test(ra)) return { chu: "Kết quả dạng bảng — xem đầy đủ ở “Kết quả lượt chạy”.", dau: true };
  return null;
}

export function KhoiNode({ data, selected }: NodeProps<KhoiRF>) {
  const mhk = useMoHinhKhoi();   // [ghi chú] mô hình thật theo file mô hình khối
  const b = data.block, x = data.tt;
  const s = x?.s;
  let chu = "", nghi = false, layDau = false;
  if (x) {
    if (s === "loi") chu = "⚠ " + (lyDoRaoChan(x.loi) ? "Bị CHẶN ở bước kiểm số — lượt dừng tại đây. Bấm khối để xem lý do." : loiThuong(x.loi));
    else if (s === "duyet") chu = "Đang chờ anh duyệt — bấm khối để xem và Duyệt / Từ chối.";
    else if (s === "tc") chu = "Lượt dừng ở bước duyệt — người duyệt bấm Từ chối hoặc lượt bị dừng từ ngoài.";
    else if (s === "xong" && x.ra && data.raoChan && data.loai === "agent_node") { chu = "Câu trả lời CHƯA QUA KIỂM SỐ — bấm khối để xem."; layDau = true; }   // [ghi chú] lượt bị Rào chặn: thẻ nhỏ không in câu / số của AI
    else if (s === "xong" && x.ra) { const t = tomTatKetQua(x.ra); if (t) { chu = t.chu; layDau = t.dau; } else chu = x.ra; }
    else if (s === "chay" && data.params.output_schema && !x.song) chu = "Đang tạo đầu ra theo khuôn… (khối có khuôn không hiện chữ chạy dần — kết quả hiện khi xong)";
    else if (x.song) chu = x.song;
    else if (x.nghi) { chu = x.nghi; nghi = true; }
    else if (s === "bo") chu = "Không chạy: lượt đã dừng trước khi tới khối này — cả nhánh không dính lỗi cũng dừng.";
  }
  if (chu.length > 150) chu = layDau ? chu.slice(0, 150) + "…" : "…" + chu.slice(-150);   // tóm câu trả lời: lấy ĐẦU; chữ chạy dần: lấy CUỐI
  const lop = ["khoi", selected ? "chon" : "", s ? "s-" + s : "", data.soLoi ? "c-loi" : ""].join(" ");
  return (
    <div className={lop}>
      {!!data.soLoi && <span className="loi-kiem" title="Số lỗi kiểm của khối này">{data.soLoi} lỗi</span>}
      {data.dungLai && <span className="canh" style={{ right: data.soLoi ? 56 : undefined }} title={"Khối này KHÔNG chạy lại — dùng lại kết quả của lượt " + data.dungLai.slice(0, 8)}>dùng lại</span>}
      {!data.soLoi && data.canhBao && <span className="canh" title={data.canhBao}>chưa thử</span>}
      {data.stt != null && <span className="stt serif" aria-label={`Bước ${data.stt}`} title={`Bước ${data.stt} theo thứ tự chạy`}>{data.stt}</span>}
      <div className="dau">
        <div className="ic" style={{ background: MAU_NHOM[b?.group_key || ""] || "var(--g-tool)" }}>{kyHieu(b)}</div>
        <div style={{ minWidth: 0 }}>
          <div className="ten">{data.label}</div>
          <div className="loai">{b?.label || data.loai}{(() => { const that = moHinhThat(mhk, data.loai); const m = that || data.params.model; return m ? ` · ${String(m)}${that || !mhk ? "" : " (ô của khối — loại khối này chưa theo file mô hình)"}` : ""; })()}</div>
        </div>
      </div>
      {(b?.inputs || []).map((i) => (
        <div className="cong" key={i.name}>
          <Handle type="target" position={Position.Left} id={i.name} className={i.required ? "bat-buoc" : ""} title={`${i.label} — nhận ${i.accepts.join(", ")}`} />
          {i.label}{i.required && <span className="bb">*</span>}
          {(data.vao?.[i.name] || []).length > 0 && (
            <span className="nguon" title={"Nhận từ: " + data.vao![i.name].map((v) => v.ten).join(" + ") + " (nối liền, xuống dòng giữa các đoạn)"}>
              {data.vao![i.name].map((v, k) => <i key={k} style={{ background: v.mau }} />)}
              {data.vao![i.name].length > 1 && <b>{data.vao![i.name].length} dây</b>}
            </span>
          )}
        </div>
      ))}
      <div className="than">
        <div className="dong">
          {x ? <span className={"chip " + s}>{TT_VN[s!]}</span> : <span className="chip cho">chưa chạy</span>}
          <span className="phu so" title={x?.lech ? "Giờ ghi lệch (đồng hồ máy bị chỉnh lùi) — thời lượng lấy theo giờ ghi nhận của máy chủ" : ""}>
            {data.dur != null ? giay(Math.max(0, data.dur)) + (x?.lech ? " ≈" : "") : ""}
          </span>
        </div>
        {coTraWebExa(data.params) && <div style={{ marginTop: 4 }}><span className="huy ok" title="Đang nối máy chủ Exa (http://mcp-exa:8766/mcp) để tra web">🌐 Tra web Exa</span></div>}
        {chu && <div className={"song" + (nghi ? " nghi" : "")}>{chu}</div>}
        {data.doiChieu && data.doiChieu.tong > 0 && (
          <div style={{ marginTop: 4, fontSize: 12, fontWeight: 600, color: data.doiChieu.khong ? "var(--loi)" : "var(--xong)" }}
               title="Đối chiếu bằng code: số / ngày / link / mã trong đầu ra phải có trong kết quả công cụ, đầu vào hoặc lời dặn">
            {data.doiChieu.khong ? `⚠ đối chiếu: ${data.doiChieu.khong} / ${data.doiChieu.tong} ý KHÔNG rõ nguồn` : `✓ đối chiếu: ${data.doiChieu.tong} / ${data.doiChieu.tong} ý có nguồn`}
          </div>
        )}
        {data.tien !== undefined && (
          <div className="phu so" style={{ marginTop: 4, fontSize: 12 }}>
            {data.tien === null ? "tiền: không đo được" : "tiền: " + usd(data.tien)}{data.tienNgam ? " · có gọi ngầm" : ""}
          </div>
        )}
      </div>
      {!!data.soCongCu && <Handle type="target" position={Position.Bottom} id={CONG_CONG_CU} isConnectable={false} className="cong-cong-cu" title={`Công cụ đang bật: ${data.soCongCu}`} />}
      {(b?.outputs || []).map((o) => (
        <div className="cong ra" key={o.name}>
          {o.label}
          <Handle type="source" position={Position.Right} id={o.name} title={`${o.label} — ra ${o.kind.join(", ")}`} />
        </div>
      ))}
    </div>
  );
}
