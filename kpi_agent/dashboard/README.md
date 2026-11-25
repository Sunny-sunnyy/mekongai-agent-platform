# Giao diện trợ lý KPI (`kpi_agent/dashboard`)

## Chạy cục bộ

```bash
npm ci
npx vite   # http://127.0.0.1:5183 (chuyển tiếp /api/chat -> :8120, /api -> :8110)
npm run build
```

Có thể đổi địa chỉ backend / agent bằng biến môi trường `KPI_API` và `KPI_AGENT`, hoặc tiền tố đường dẫn bằng `KPI_BASE`.
