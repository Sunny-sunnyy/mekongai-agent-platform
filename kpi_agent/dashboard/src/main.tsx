import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";
import { WorkflowPage } from "./workflow/WorkflowPage";

// Lớp màu của trang: MẶC ĐỊNH "Sổ tài chính" (12/11 sau khi so ảnh hai kiểu); kiểu coban "Đường đua" giữ làm lớp phụ,
// bật bằng ?mau=duong_dua trên đường dẫn hoặc biến dựng VITE_KPI_SKIN=duong_dua. Chỉ nhận tên có trong danh sách (không gắn chữ lạ từ đường dẫn vào trang).
const SKINS = ["so_tai_chinh", "duong_dua"];
const asked = new URLSearchParams(window.location.search).get("mau") ?? import.meta.env.VITE_KPI_SKIN;
const skin = asked && SKINS.includes(asked) ? asked : "so_tai_chinh";
if (skin === "duong_dua") delete document.documentElement.dataset.skin;   // token gốc trong styles.css là kiểu coban
else document.documentElement.dataset.skin = skin;

// ?trang=workflow ⇒ trang chỉ xem workflow (bước 2a); không thêm bộ định tuyến cho 2 trang
const isWorkflow = new URLSearchParams(window.location.search).get("trang") === "workflow";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {isWorkflow ? <WorkflowPage /> : <App />}
  </StrictMode>,
);
