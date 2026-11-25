import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Bản dựng (dist/) do may_chu.py phục vụ ở 127.0.0.1:18400. Khi sửa giao diện: `npm run dev` (cổng 18410) chuyển /api sang máy chủ Python.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { proxy: { "/api": "http://127.0.0.1:18400" } },
  build: { outDir: "dist", sourcemap: false },
});
