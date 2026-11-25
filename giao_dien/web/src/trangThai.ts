// Từ sự kiện của một lượt chạy (bảng workflow_event) tới mốc t ⇒ trạng thái từng khối. Một nguồn sự thật cho xem trực tiếp lẫn phát lại.
// Bài học đã chạy thật (04/11, đã kiểm):
//  - giờ ghi có lúc đi LÙI 1–1,5 giây (đồng hồ WSL) ⇒ khối sau đã `begin` thì khối trước chắc chắn đã xong (Temporal giao theo tầng);
//  - khối duyệt: execution_successful tới TRƯỚC awaiting_intervention — nghĩa "đã viết bản tóm tắt", KHÔNG phải "đã duyệt";
//  - "bỏ qua" không có sự kiện riêng: khối không có `begin` mà lượt đã dừng.
import { ms, DANG_CHAY, type RunEvent, type WfEdge } from "./api";

export type TT = "cho" | "chay" | "duyet" | "xong" | "loi" | "bo" | "tc";
export type KhoiTT = {
  s: TT; bd: number | null; kt: number | null; choTu: number | null; choMs: number; song: string; nghi: string;
  ra: string | null; loi: string | null; xemDuyet: string | null; baoDauVao: string[]; lech: boolean;
  tuDuyet?: boolean;   // [ghi chú kỹ thuật] khối Người duyệt tự cho đi tiếp vì quá hạn (sự kiện lifecycle "auto_approved") — không ai bấm
};

// [ghi chú kỹ thuật] tên khối để HIỂN THỊ cho một lượt: lượt có khối tự duyệt mà tên khối nói "đã duyệt" (tên đặt sẵn trong mẫu) ⇒ nói đúng sự thật.
export const DUOI_TU_DUYET = "tự duyệt khi quá hạn, chưa có người duyệt";
export function tenKhoiHien(ten: string, luotTuDuyet: boolean): string {
  if (!luotTuDuyet || !/đã duyệt/i.test(ten)) return ten;
  return ten.replace(/\s*đã duyệt/i, "").trim() + " — " + DUOI_TU_DUYET;
}
export function luotCoTuDuyet(st: Record<string, KhoiTT> | null | undefined): boolean {
  return !!st && Object.values(st).some((x) => x.tuDuyet === true);
}
export const TT_VN: Record<TT, string> = {
  cho: "chờ", chay: "đang chạy", duyet: "chờ người duyệt", xong: "xong", loi: "lỗi", bo: "bỏ qua — lượt đã dừng", tc: "dừng ở bước duyệt",   // [ghi chú kỹ thuật] lifecycle "stopped" = từ chối HOẶC dừng từ ngoài — không phân biệt được (App.tsx:1288)
};

// [ghi chú kỹ thuật] kết quả của khối Rào chế độ "code" (JSON bị cắt 4.000 ký tự ⇒ đọc bằng mẫu chữ ở đầu chuỗi) ⇒ một dòng lời thường; không phải ⇒ null.
// Lý do chi tiết chỉ hiện khi ĐẠT: lý do khi chặn chứa chính con số bị chặn.
// [ghi chú kỹ thuật] kết quả khối Xuất chữ của bộ KPI là chuỗi kiểu Python / JSON {'cau_tra_loi': '…', …} (máy chủ trang cắt 4.000 ký tự) ⇒ lấy riêng câu
// trả lời, trả lại xuống dòng thật; chuỗi bị cắt giữa chừng ⇒ lấy tới hết + "…". Không đúng hình ⇒ null (người gọi hiện như cũ).
export function tachCauTraLoi(ra?: string | null): string | null {
  if (!ra) return null;
  const m = /^\s*\{\s*(['"])cau_tra_loi\1\s*:\s*(['"])/.exec(ra);
  if (!m) return null;
  const q = m[2];
  let out = "", i = m[0].length, dong = false;
  for (; i < ra.length; i++) {
    const c = ra[i];
    if (c === "\\") {
      const d = ra[++i];
      if (d === undefined) break;
      out += d === "n" ? "\n" : d === "t" ? " " : d === "r" ? "" : d;
    } else if (c === q) { dong = true; break; } else out += c;
  }
  out = out.trim();
  return out ? out + (dong ? "" : " …") : null;
}

// [ghi chú kỹ thuật] khối Rào CHẶN không phát node_result: nó phát execution_failed với chữ "Guardrail blocked: <lý do>" ⇒ tách lý do
// (lý do có nêu con số bị chặn — chỉ hiện trong XƯỞNG cho người vận hành, không đưa ra thẻ trả lời Hỏi KPI). Không phải chặn của Rào ⇒ null.
export function lyDoRaoChan(loi?: string | null): string | null {
  const m = /^\s*Guardrail blocked:\s*([\s\S]+)$/.exec(loi || "");
  return m ? m[1].trim() : null;
}

// [ghi chú kỹ thuật] lỗi thô của khối ⇒ câu lời thường cho người vận hành (lỗi thô vẫn hiện trong phần gập). Không nhận ra ⇒ trả nguyên văn.
export function loiThuong(loi?: string | null): string {
  const s = (loi || "").trim();
  if (!s) return "lỗi";
  if (lyDoRaoChan(s)) return "Bị CHẶN ở bước kiểm — lượt dừng tại đây, khối sau không nhận gì.";
  let m = /Human intervention timed out after (\d+) seconds/i.exec(s) || /No response was submitted within (\d+) seconds/i.exec(s);   // [ghi chú kỹ thuật] câu Capitol ghi ở tóm tắt lượt
  if (m) return `Hết giờ, chưa ai duyệt (đã chờ ${m[1]} giây) — lượt dừng tại đây, không có gì được gửi đi.`;
  m = /Exceeded the input_tokens_limit of (\d+) \(input_tokens=(\d+)\)/.exec(s);
  if (m) return `Vượt trần token đầu vào của bộ chạy thử (${Number(m[2]).toLocaleString("vi-VN")} > ${Number(m[1]).toLocaleString("vi-VN")}) — khối dừng để giữ tiền; thường do vòng gọi công cụ lặp lại cả lịch sử.`;
  if (/An internal error occurred while processing this step|This step encountered an error/i.test(s)) return "Khối lỗi (Capitol chỉ báo câu chung) — xem lỗi gốc của từng khối bên dưới.";
  return s;
}

// [ghi chú kỹ thuật] MỘT dòng ngắn cho DANH SÁCH lượt (cột trái): chữ Capitol ghi ở tóm tắt lượt là tiếng Anh chung chung ⇒ lời thường + "bấm để xem".
// Đã là tiếng Việt (máy chủ đổi lời) ⇒ giữ, cắt 110 ký tự như cũ.
export function loiDanhSach(loi?: string | null): string {
  const s = (loi || "").trim();
  if (!s) return "";
  let m = /No response was submitted within (\d+) seconds|Human intervention timed out after (\d+) seconds/i.exec(s);
  if (m) return `Hết giờ, chưa ai duyệt (đã chờ ${m[1] || m[2]} giây) — lượt dừng.`;
  if (/^Guardrails?\b[^:]{0,40}:\s*An internal error occurred/i.test(s)) return "Bước kiểm số dừng lượt — bấm để xem lý do.";
  m = /^(.{1,60}?):\s*An internal error occurred while processing this step/i.exec(s);
  if (m) return `Khối “${m[1]}” lỗi — bấm để xem lỗi gốc.`;
  if (/stopped by user/i.test(s)) return "Người bấm dừng lượt.";
  return s.slice(0, 110);
}

// [ghi chú kỹ thuật] lượt có khối Rào CHẶN ⇒ một luật cho cả lượt trong xưởng: câu AI đã viết + lý do chặn mang nhãn này và mặc định gập.
export const NHAN_CHUA_KIEM = "CHƯA QUA KIỂM SỐ — không dùng số này";
export function luotBiRaoChan(st: Record<string, KhoiTT> | null | undefined): boolean {
  return !!st && Object.values(st).some((y) => !!lyDoRaoChan(y.loi));
}

export function tomTatRao(ra?: string | null): string | null {
  if (!ra || !/^\s*\{\s*"che_do":\s*"code"/.test(ra)) return null;
  const dat = /"dat":\s*(true|false)/.exec(ra)?.[1];
  if (!dat) return null;
  const pb = /"phien_ban":\s*"([^"]{1,40})"/.exec(ra)?.[1];
  const dsLoi = /"loi":\s*\[([\s\S]*?)\],\s*"ly_do"/.exec(ra)?.[1];
  const soLoi = dsLoi ? (dsLoi.match(/"loai"/g) || []).length : 0;
  const lyDo = dat === "true" ? /"ly_do":\s*"((?:\\.|[^"\\]){0,300})"/.exec(ra)?.[1]?.replace(/^đạt\s*—\s*/, "") : null;
  return (dat === "true" ? "ĐẠT" : "CHẶN — lượt dừng tại đây") + (pb ? ` · bản kiểm ${pb}` : "") + ` · ${soLoi} lỗi` + (lyDo ? ` · ${lyDo}` : "")
    + (dat === "true" ? "" : " · chi tiết ở bảng “Kết quả kiểm bằng code”");
}

export function tinh(suKien: RunEvent[], ids: string[], edges: WfEdge[], t: number, ttLuot: string) {
  const st: Record<string, KhoiTT> = {};
  for (const id of ids) st[id] = { s: "cho", bd: null, kt: null, choTu: null, choMs: 0, song: "", nghi: "", ra: null, loi: null, xemDuyet: null, baoDauVao: [], lech: false };
  const nguon: Record<string, string[]> = {};
  for (const e of edges) (nguon[e.to_node] ??= []).push(e.from_node);
  let ketThuc: number | null = null, batDau: number | null = null, loiLuot: string | null = null;
  for (const e of suKien) {
    const te = ms(e.ts);
    if (te > t) break;
    if (e.t === "run_started" && batDau === null) batDau = te;
    if (e.t === "termination" || e.t === "execution_result") ketThuc ??= te;
    const x = e.n ? st[e.n] : undefined;
    if (!x) continue;
    switch (e.t) {
      case "begin":
        // [ghi chú kỹ thuật] "end" của khối là CHỐT:
        // begin mang giờ SAU end ⇒ không mở lại khối; giờ begin thật không biết ⇒ thanh dòng thời gian dài 0 tại end, đánh dấu lệch giờ
        if (x.s === "xong" && x.kt !== null && te > x.kt) { x.bd ??= x.kt; x.lech = true; break; }
        x.s = "chay"; x.bd = te;
        for (const u of nguon[e.n!] || []) { const y = st[u]; if (y && (y.s === "chay" || y.s === "cho")) { y.s = "xong"; y.kt = te; y.lech = true; } }
        break;
      case "processing": if (e.k === "thinking") x.nghi += e.d || ""; else if (e.d !== "{}") x.song += e.d || ""; break;   // [ghi chú kỹ thuật] khối Rào phát "{}" — không phải chữ AI viết
      case "awaiting_intervention": x.s = "duyet"; x.xemDuyet = e.d; x.choTu = te; break;
      case "execution_successful": if (x.s !== "duyet" && e.d) x.ra = e.d; break;   // [ghi chú kỹ thuật] khối Rào: sự kiện này đến SAU node_result và rỗng (data là đối tượng) ⇒ không ghi đè
      case "node_result": if (x.s !== "duyet") x.ra = e.d ?? x.ra; else x.xemDuyet = e.d ?? x.xemDuyet; break;
      case "execution_failed": x.s = "loi"; x.loi = e.d; x.kt = te; loiLuot ??= e.d; break;
      case "input_issue": x.baoDauVao.push(e.d || ""); break;
      // [đã chạy 04/11] Từ chối ⇒ KHÔNG có `end` cho khối duyệt; chỉ có lifecycle {node_id, resolution: "stopped"} (máy chủ đưa node_id vào e.n)
      case "lifecycle":
        if (e.d === "stopped") { if (x.choTu !== null) { x.choMs += te - x.choTu; x.choTu = null; } x.s = "tc"; x.kt = te; }
        if (e.d === "auto_approved") x.tuDuyet = true;   // [ghi chú kỹ thuật] đã đo trên lượt dfc8e105
        break;
      case "end":
        if (x.choTu !== null) { x.choMs += te - x.choTu; x.choTu = null; }
        if (x.s !== "loi" && x.s !== "tc") x.s = "xong";
        x.kt = te; break;
    }
  }
  // [ghi chú kỹ thuật] lượt đã kết thúc (theo trạng thái lượt) và đã duyệt HẾT danh sách (không phải đang tua giữa chừng) ⇒ coi như xong,
  // kể cả khi chưa nhận được sự kiện termination / execution_result (ghi sau lúc trạng thái lượt đổi)
  const daHet = !suKien.some((e) => ms(e.ts) > t);
  const xongLuot = ketThuc !== null || (!!ttLuot && !DANG_CHAY.has(ttLuot) && daHet);
  if (xongLuot) for (const x of Object.values(st)) {
    if (x.s === "cho") x.s = "bo";
    else if (x.s === "chay" || x.s === "duyet") x.s = ttLuot === "SUCCESS" ? "xong" : "loi";
  }
  for (const x of Object.values(st)) if (x.choTu !== null) x.choMs += t - x.choTu;
  return { st, batDau, ketThuc, loiLuot };
}
