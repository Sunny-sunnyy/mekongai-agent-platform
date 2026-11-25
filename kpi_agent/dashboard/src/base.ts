// Tiền tố đường dẫn của trang (hướng "một hệ thống": cổng chung phục vụ trang này dưới `/kpi/`).
// `import.meta.env.BASE_URL` = `base` của Vite lúc dựng (biến môi trường KPI_BASE, mặc định "/") ⇒ chạy :5183 như cũ thì apiUrl("/api/meta") = "/api/meta";
// dựng với KPI_BASE=/kpi/ thì = "/kpi/api/meta". MỌI lời gọi backend / agent đi qua `apiFetch()` (src/session.ts — gọi hàm này rồi gắn mã phiên); không có fetch trần nào khác.
export const apiUrl = (path: string) => import.meta.env.BASE_URL.replace(/\/$/, "") + path;

// Trang chỉ đọc, xem số qua cổng xem của hộp KPI trong Capitol ("một nguồn số" — VITE_KPI_READ_ONLY=1). Hộp xem không có file trang theo dõi của đội và
// không mở các đường chỉ-cho-trang có tên khách (Hội viên, từng khách gia vị) ⇒ các màn đó ghi rõ "chưa có ở bản xem qua Capitol" thay vì báo lỗi.
export const READ_ONLY = import.meta.env.VITE_KPI_READ_ONLY === "1";
