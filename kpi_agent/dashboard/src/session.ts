import { apiUrl } from "./base";
// Mã phiên phía TRANG (kpi_agent/session_key.py): backend + agent đòi header `X-Kpi-Session` ở MỌI lời gọi.
// Người bật trợ lý mở trang bằng đường script in ra (`…/?k=<mã>`). Ở đây: lấy mã khỏi địa chỉ ⇒ cất vào kho của trình duyệt ⇒ xoá `?k=` khỏi thanh
// địa chỉ ngay. Mã chỉ được GỬI ở header — không bao giờ ghép vào URL gọi backend / agent (URL nằm trong log truy cập), không gắn vào link sang
// trang khác. Mã không có trong file JS / HTML: máy chủ trang không biết mã.
//
// CẤT Ở ĐÂU (A + 12/11 — để tab mới / menu từ trang khác không phải dán lại đường có mã):
// - trang chạy ở gốc origin riêng (`base` = "/", vd 127.0.0.1:5183) ⇒ localStorage: mọi tab cùng origin dùng chung, còn sau khi đóng trình duyệt,
//   tới khi `chay_ban1.sh stop` (mã đổi mỗi lần bật ⇒ mã cũ tự vô hiệu, và bị xoá khỏi kho ở lần gọi 401 đầu tiên);
// - trang chạy dưới tiền tố (`KPI_BASE` khác "/", chung origin với trang khác sau cổng chung) ⇒ sessionStorage của riêng tab — không để script của
//   trang khác cùng origin đọc được mã.
// Cái giá của localStorage: MỌI script chạy trên origin này đọc được mã. Thứ che là: trang không dựng HTML từ chữ bên ngoài (chỉ text node) —
// giả định này có phép quét giữ: kpi_agent/tests/test_dashboard_static.py.
const STORE_KEY = "kpi_session_key";
const HEADER = "X-Kpi-Session";
export const UNAUTHORIZED_EVENT = "kpi-unauthorized";

const keyStore = (): Storage => (import.meta.env.BASE_URL === "/" ? window.localStorage : window.sessionStorage);

function takeKeyFromAddress() {
  const url = new URL(window.location.href);
  const key = url.searchParams.get("k");
  if (!key) return;
  keyStore().setItem(STORE_KEY, key);   // đường có mã MỚI ghi đè mã cũ
  url.searchParams.delete("k");          // chỉ bỏ đúng `k` — ?man=, ?nv=, ?kpi=, ?mau= giữ nguyên
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
}
takeKeyFromAddress();

// MỘT hàm gọi mạng cho cả ba trang (chính, workflow, kéo–thả): nhận đường "/api/…", tự ghép tiền tố trang (apiUrl) và gắn header mã.
// Máy chủ (backend, hoặc agent qua proxy) trả 401 ⇒ mã đã gửi không còn dùng được: XOÁ nó khỏi kho (chỉ khi kho vẫn đang giữ đúng mã đó — tab cũ
// không được xoá mã mới mà tab khác vừa nhận) rồi báo cho trang hiện màn "mở bằng đường có mã".
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const key = keyStore().getItem(STORE_KEY);
  if (key) headers.set(HEADER, key);
  const response = await fetch(apiUrl(path), { ...init, headers });   // apiUrl: tiền tố trang (KPI_BASE) — src/base.ts
  if (response.status === 401) {
    if (keyStore().getItem(STORE_KEY) === key) keyStore().removeItem(STORE_KEY);
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  return response;
}

export const NO_KEY_MESSAGE =
  "Trang chưa có mã phiên (hoặc mã đã cũ). Mở trang bằng đường có mã do script in ra lúc bật — in lại: bash kpi_agent/scripts/chay_ban1.sh link";
