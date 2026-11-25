// Số hiện theo kiểu Việt Nam (dấu chấm ngăn nghìn, dấu phẩy thập phân). Chỉ ĐỊNH DẠNG — số do backend tính, giao diện không tính lại.
const vi = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 });

export const num = (value: number | null | undefined) => (value === null || value === undefined ? "—" : vi.format(value));
export const vnd = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${vi.format(value)} đ`);
// Số thùng: hiện ĐÚNG số backend trả (1.180,375 — không làm tròn về 2 chữ số lẻ như `num`)
const viExact = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 6 });
export const exact = (value: number | null | undefined) => (value === null || value === undefined ? "—" : viExact.format(value));
// Tiền làm tròn tới đồng (doanh số từng điểm bán có phần lẻ xu trong file đơn hàng — hiện tới đồng như các màn khác; số gốc backend trả không đổi)
const viDong = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 });
export const dong = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${viDong.format(value)} đ`);
export const pct = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${vi.format(value)} %`);

// "2026-10-21T14:16:38" ⇒ "21/10/2026 14:16"
export function dateTime(iso: string | null | undefined) {
  if (!iso) return "—";
  const [day, time = ""] = iso.split("T");
  const [y, m, d] = day.split("-");
  return `${d}/${m}/${y}${time ? " " + time.slice(0, 5) : ""}`;
}

// "2026-10-30T00:00:00" ⇒ "30/10/2026" — đường file đơn hàng chỉ có NGÀY đơn mới nhất, giờ "00:00" vô nghĩa (góp ý kiểm 12/11)
export const dateOnly = (iso: string | null | undefined) => dateTime(iso ? iso.split("T")[0] : iso);

// Triệu đồng một chữ số lẻ cho ô bảng (thiết kế mục 3) — chỉ định dạng; số đủ đồng vẫn hiện trong chú thích + thẻ
// Cột Tổng: 2 chữ số lẻ (đã chọn 03/11 sau kiểm — 1 lẻ làm 3.840.000 và 3.800.000 cùng hiện "3,8 tr"); câu đầu trang: 1 lẻ
const viMillion = (digits: number) => new Intl.NumberFormat("vi-VN", { maximumFractionDigits: digits });
export const millions = (value: number | null | undefined, unit = "tr", digits = 2) =>
  value === null || value === undefined ? "—" : `${viMillion(digits).format(value / 1e6)} ${unit}`;

// "2026-10" ⇒ "tháng 10/2026"
export const monthLabel = (period: string) => {
  const [y, m] = period.split("-");
  return `tháng ${Number(m)}/${y}`;
};
