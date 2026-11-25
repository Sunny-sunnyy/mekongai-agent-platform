// [ghi chú kỹ thuật] Hàm THUẦN cho thẻ trả lời "Hỏi KPI": mọi con số ở đây lấy từ BẢNG do phần tính thưởng dựng (view của khối kiểm số),
// không lấy từ chữ AI viết. Mục đích: người đọc nhìn dải xếp hạng là tự thấy câu "ai nhiều nhất / ít nhất" của AI đúng hay sai
// (lượt thật 45beecd2 ngày 13/11: AI viết "NV13 ít nhất 5.540.000" trong khi bảng: NV08 5.240.000 — khối kiểm số không bắt được loại lỗi này).
export type Dong = { ma: string; tien: number };
type BangVao = { key: string; rows: (string | number | null)[][]; total_rows?: number };
type ViewVao = { facts: { key: string; value: string | number | null }[]; tables: BangVao[] } | null | undefined;

const MA_NV = /(?<![A-Za-z0-9])NV\d{2}(?![A-Za-z0-9])/g;

/** Các dòng [mã nhân viên, tiền] của bảng "Thưởng theo nhân viên"; sai hình ⇒ []. */
export function dongNhanVien(v: ViewVao): Dong[] {
  const b = v?.tables.find((t) => t.key === "salary_by_employee");
  if (!b || (typeof b.total_rows === "number" && b.total_rows > b.rows.length)) return [];   // bảng bị cắt bớt dòng ⇒ không đủ để xếp hạng / tra ⇒ coi như không có
  const ra: Dong[] = [];
  for (const r of b.rows) if (typeof r[0] === "string" && typeof r[1] === "number" && isFinite(r[1])) ra.push({ ma: r[0], tien: r[1] });
  return ra;
}

/** Xếp hạng do CODE sắp: n người cao nhất + n người thấp nhất; người ĐỒNG HẠNG ở ranh giới được lấy hết (không cắt giữa nhóm cùng tiền).
 *  Ít hơn 2 dòng ⇒ null. Tổng số dòng ≤ 2n ⇒ trả một danh sách đủ (cao = tất cả, thap = []). */
export function xepHang(v: ViewVao, n = 3): { cao: Dong[]; thap: Dong[]; tong: number } | null {
  const ds = dongNhanVien(v);
  if (ds.length < 2) return null;
  const giam = [...ds].sort((a, b) => b.tien - a.tien || a.ma.localeCompare(b.ma));
  if (ds.length <= 2 * n) return { cao: giam, thap: [], tong: ds.length };
  const lay = (xs: Dong[]) => { const moc = xs[n - 1].tien; return xs.filter((x, i) => i < n || x.tien === moc); };
  const cao = lay(giam);
  const tang = [...ds].sort((a, b) => a.tien - b.tien || a.ma.localeCompare(b.ma));
  const thap = lay(tang).filter((x) => !cao.some((c) => c.ma === x.ma));
  return { cao, thap, tong: ds.length };
}

/** Mã nhân viên xuất hiện trong một đoạn chữ, không trùng, theo thứ tự gặp.
 *  [ghi chú kỹ thuật] yêu cầu vận hành tên + mã NV thật được dùng ⇒ KHÔNG chỉ dựa mẫu "NV + 2 số": có bảng của lượt (v) thì lấy MỌI mã / tên trong
 *  cột đầu của bảng "Thưởng theo nhân viên" mà xuất hiện trong chữ (so nguyên cụm, không phân biệt hoa thường); mẫu NVdd vẫn giữ làm dự phòng. */
export function maTrongChu(chu?: string | null, v?: ViewVao): string[] {
  const c = chu || "";
  const gap: { ma: string; i: number }[] = [];
  const thap = c.toLocaleLowerCase("vi");
  for (const d of dongNhanVien(v)) {
    const k = d.ma.toLocaleLowerCase("vi");
    if (!k) continue;
    let i = thap.indexOf(k);
    while (i >= 0) {
      const truoc = i === 0 ? "" : thap[i - 1], sau = thap[i + k.length] ?? "";
      if (!/[\p{L}\p{N}]/u.test(truoc) && !/[\p{L}\p{N}]/u.test(sau)) { gap.push({ ma: d.ma, i }); break; }
      i = thap.indexOf(k, i + 1);
    }
  }
  for (const m of c.matchAll(MA_NV)) gap.push({ ma: m[0], i: m.index ?? 0 });
  return [...new Set(gap.sort((a, b) => a.i - b.i).map((x) => x.ma))];
}

/** Ô nổi "Lương kinh doanh 8 KPI của <mã>" [ghi chú kỹ thuật]: bảng này là LƯƠNG KINH DOANH = tổng 8 KPI (không gồm thưởng thêm / thưởng vượt).
 *  Chọn mã: câu HỎI hiện tại nêu đúng MỘT mã ⇒ dùng mã đó (kể cả khi câu trả lời nhắc thêm người khác), TRỪ KHI câu trả lời có nêu mã mà không
 *  nêu mã được hỏi (AI trả lời về người khác) ⇒ không ô; câu hỏi không có mã ⇒ lấy mã DUY NHẤT của câu trả lời; còn lại ⇒ không ô.
 *  Tiền luôn tra từ bảng theo mã, không lấy từ chữ AI. */
export function oMotNhanVien(v: ViewVao, cauHoi?: string | null, traLoi?: string | null): Dong | null {
  const h = maTrongChu(cauHoi, v), t = maTrongChu(traLoi, v);
  const ma = h.length === 1 ? (t.length > 0 && !t.includes(h[0]) ? null : h[0]) : h.length === 0 && t.length === 1 ? t[0] : null;
  return ma ? dongNhanVien(v).find((d) => d.ma === ma) ?? null : null;
}

/** Ô "Tổng thưởng cả đội" chỉ làm NỔI khi câu trả lời có đúng con số tổng (tránh đọc nhầm tổng đội là số của một người). */
export function tongDoiTrongCau(v: ViewVao, soTrongCau: Set<number>): boolean {
  const t = v?.facts.find((f) => f.key === "team_total")?.value;
  return typeof t === "number" && soTrongCau.has(t);
}
