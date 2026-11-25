import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./styles.css";
import App from "./App";

// [ghi chú kỹ thuật] Mã mở trang: người dùng mở đường dẫn có "#k=<mã>" MỘT LẦN ⇒ đổi lấy cookie (máy chủ đặt, HttpOnly) rồi xoá mã khỏi thanh địa chỉ.
// Phần sau dấu # không gửi lên máy chủ. Không có mã / mã sai ⇒ các đường /api trả 401 và trang hiện lời nhắc (App).
async function moBangMa() {
  const m = location.hash.match(/^#k=([A-Za-z0-9_-]{20,200})$/);
  if (!m) return;
  history.replaceState(null, "", location.pathname + location.search);
  try {
    await fetch("/api/mo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ k: m[1] }) });
  } catch { /* máy chủ không trả lời — App sẽ báo */ }
}

moBangMa().finally(() => createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ReactFlowProvider>
      <App />
    </ReactFlowProvider>
  </StrictMode>,
));
