import { createContext, useContext } from "react";
import type { EmployeeNames } from "./api";

// Kiểu hiện nhân viên trên TRANG (người vận hành luật che tên 11/11 — 12/11 User: "thay tên nhân viên liền"): MẶC ĐỊNH là Tên · "Tên · mã SM · NVxx" · NVxx.
// Tên + mã SM lấy từ backend `/api/names`; backend không trả (404 / 409) ⇒ trang chỉ hiện NVxx và ẩn nút chọn.
// Chỉ đổi chữ hiện trên trang — câu hỏi gửi trợ lý chat và mọi lời gọi backend vẫn dùng mã NVxx.
export type NameMode = "ma" | "ten" | "day-du";
export const DEFAULT_NAME_MODE: NameMode = "ten";
export const NAME_MODES: [NameMode, string][] = [["ten", "Tên"], ["day-du", "Tên · mã SM · NVxx"], ["ma", "NVxx"]];

export const NamesContext = createContext<{ mode: NameMode; names: EmployeeNames | null }>({ mode: DEFAULT_NAME_MODE, names: null });

export function employeeLabel(id: string, mode: NameMode, names: EmployeeNames | null): string {
  const entry = names?.[id];
  if (!entry || mode === "ma") return id;
  return mode === "ten" ? entry.name : `${entry.name} · ${entry.sm_code} · ${id}`;
}

// Một nhân viên theo kiểu đang chọn; luôn kèm mã NVxx ở chú thích rê chuột để đối chiếu với file xuất / câu trả lời của trợ lý
export function Nv({ id }: { id: string }) {
  const { mode, names } = useContext(NamesContext);
  return <span title={id}>{employeeLabel(id, mode, names)}</span>;
}

export function NameModeSwitch({ mode, onChange }: { mode: NameMode; onChange: (mode: NameMode) => void }) {
  return (
    <nav className="view-switch" aria-label="Kiểu hiện nhân viên">
      <span className="hint">Hiện nhân viên theo:</span>
      {NAME_MODES.map(([key, label]) => (
        <button key={key} type="button" className={mode === key ? "on" : ""} aria-pressed={mode === key} onClick={() => onChange(key)}>{label}</button>
      ))}
    </nav>
  );
}
