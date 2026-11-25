import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// Trang gọi "/api/..." trên chính cổng Vite; Vite chuyển tiếp ⇒ backend / agent không cần CORS.
// "/api/chat" đi agent service (:8120, SPEC mục 3), còn lại đi backend chỉ đọc (:8110). Khoá cụ thể đứng trước "/api".
// Chỉ nghe 127.0.0.1 (SPEC mục 2 — chạy trên máy, không mở mạng ngoài).
// KPI_BASE: tiền tố khi trang được phục vụ sau cổng chung (vd KPI_BASE=/kpi/ npm run build). Mặc định "/" ⇒ chạy:5183 như cũ, không đổi gì.
// Trang gọi API qua `src/base.ts` apiUrl() ⇒ "<tiền tố>api/…"; khi có tiền tố, dev / preview bỏ tiền tố rồi chuyển tiếp như cũ (cổng chung thật tự làm việc này).
const base = process.env.KPI_BASE ?? "/";
const backend = process.env.KPI_API ?? "http://127.0.0.1:8110";
const agent = process.env.KPI_AGENT ?? "http://127.0.0.1:8120";
const stripBase = (path: string) => path.slice(base.length - 1);
const proxy = {
  "/api/chat": agent,
  "/api": backend,
  ...(base === "/" ? {} : {
    [`${base}api/chat`]: { target: agent, rewrite: stripBase },
    [`${base}api`]: { target: backend, rewrite: stripBase },
  }),
};

export default defineConfig({
  base,
  plugins: [react()],
  // Bước 2b: trang kéo–thả là trang HTML riêng (keo_tha.html — CSS riêng, không lẫn trang chính); dev mở thẳng /keo_tha.html
  build: {
    rollupOptions: {
      input: { main: fileURLToPath(new URL("index.html", import.meta.url)), keo_tha: fileURLToPath(new URL("keo_tha.html", import.meta.url)) },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5183,
    strictPort: true,
    proxy,
  },
  preview: { host: "127.0.0.1", proxy },
});
