// [ghi chú kỹ thuật] "Đang dùng bản dữ liệu nào" — một chỗ cho cả trang (tab Dữ liệu KPI bật, nút Chạy + ô hỏi trợ lý gửi kèm, thẻ kết quả so).
// Cố ý CHỈ trong bộ nhớ: tải lại trang ⇒ về "bản lúc bật" (ca V10 nhóm kỹ thuật — tránh chạy nhầm bản cũ mà không biết).
import { useSyncExternalStore } from "react";

export type BanDuLieu = { dv: string; period?: string; cutoff?: string } | null;

let dangDung: BanDuLieu = null;
const daGui: Record<string, string | null> = {};   // mã phiên ⇒ bản đã gửi khi bấm chạy (null = bản lúc bật)
const nghe = new Set<() => void>();
const bao = () => nghe.forEach((f) => f());

export const banDuLieu = {
  lay: (): BanDuLieu => dangDung,
  dat: (b: BanDuLieu) => { dangDung = b; bao(); },
  ghiLuot: (phien: string, dv: string | null) => { daGui[phien] = dv; bao(); },
  daGui: (phien: string): string | null | undefined => daGui[phien],   // undefined = lượt không bấm từ trang này (hoặc trước khi tải lại)
};

const dangKy = (f: () => void) => { nghe.add(f); return () => { nghe.delete(f); }; };
export const useBanDuLieu = () => useSyncExternalStore(dangKy, () => dangDung);
let phienBan = 0;
const dangKyLuot = (f: () => void) => { const g = () => { phienBan++; f(); }; nghe.add(g); return () => { nghe.delete(g); }; };
export const useDaGui = (phien: string) => { useSyncExternalStore(dangKyLuot, () => phienBan); return daGui[phien]; };
