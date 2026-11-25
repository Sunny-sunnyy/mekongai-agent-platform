// Kiểm trên trình duyệt: trang ở chế độ "một nguồn số" (bản xem, chỉ đọc) —. Không gọi mô hình (0 USD).
// Cần bộ thử: backend thử + HAI CỔNG GIẢ (kpi_agent/tests/cong_gia_xem.py — KHÔNG phải hộp Capitol thật) + agent bật KPI_XEM_URL + trang dựng
// với VITE_KPI_READ_ONLY=1, VITE_KPI_WORKSHOP_URL=<địa chỉ xưởng>, KPI_API trỏ vào agent.
//   node kiem/kiem_ban_xem.mjs <địa chỉ trang> <địa chỉ backend thử> <file công tắc lệch của cổng giả> <thư mục ảnh NGOÀI repo>
import { rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const [base, backend, switchFile, out] = process.argv.slice(2);
const exe = process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell";
const browser = await chromium.launch({ executablePath: exe });
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "ĐẠT  " : "TRƯỢT"} ${name}${detail ? " — " + detail : ""}`); };
const flat = (text) => text.replace(/\s+/g, " ").trim();
const dong = (text) => Number(text.replace(/[^\d-]/g, ""));
const WAIT_MS = 16000;   // máy chủ trợ lý giữ kết quả so vân tay tối đa 15 giây ⇒ đổi trạng thái cổng giả xong chờ hết hạn rồi mới nhìn trang
if (switchFile !== "hop-that") rmSync(switchFile, { force: true });
const direct = await (await fetch(`${backend}/api/team-board`)).json();

const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [], posts = [], seen = new Set();
page.on("pageerror", (e) => errors.push(String(e)));
page.on("request", (request) => { const url = new URL(request.url()); if (url.pathname.startsWith("/api/")) { seen.add(url.port); if (request.method() !== "GET") posts.push(url.pathname); } });
await page.goto(`${base}/?man=doi`);
await page.waitForSelector(".hero, .kpi-total, .team-section", { timeout: 90000 });
await page.waitForSelector(".chip-status");
const hero = dong(await page.textContent(".hero .hero-total, .hero-total, .hero b, .hero"));
check("bản xem: tổng tiền cả đội trên trang = số backend sau cổng (từng đồng)", flat(await page.textContent("main")).includes(new Intl.NumberFormat("vi-VN").format(direct.team_total)), `${direct.team_total} · ô đầu ${hero}`);
check("nhãn đầu trang ghi 'Tạm tính — xem qua Capitol (chưa có trường duyệt)', không có chữ 'Đã soát' / 'đã duyệt'", flat(await page.textContent(".chip-status")).includes("Tạm tính — xem qua Capitol (chưa có trường duyệt)")
  && (await page.getAttribute(".chip-status", "class")).includes("tam_tinh") && !/Đã soát|đã duyệt/i.test(flat(await page.textContent("header, .masthead, .topbar, body")).split("Xưởng workflow")[0]), flat(await page.textContent(".chip-status")));
check("không có khung 'Đã soát' / nút tải; có dòng trỏ sang trang chính thức + link xưởng", (await page.locator("details.panel").count()) === 0 && (await page.locator(".read-only-note a").count()) === 1
  && (await page.locator('button:has-text("Đã soát"), button:has-text("Tải")').count()) === 0, flat(await page.textContent(".read-only-note")));
check("trang chỉ gọi /api/* trên chính cổng của trang (không gọi thẳng cổng nào khác)", seen.size === 1, [...seen].join(","));
for (const [query, selector] of [["?man=theo-doi", ".tracking"], ["?man=chi-tiet", ".outlets-view"], ["?man=chi-tieu&kpi=T03", ".outside-note"], ["?man=nhan-vien", ".emp-view"], ["?man=tro-ly", ".assistant-screen"]]) {
  await page.goto(`${base}/${query}`);
  const ok = await page.waitForSelector(selector, { timeout: 90000 }).then(() => true, () => false);
  check(`bản xem: màn ${query} có số qua cổng`, ok);
}
// 13/11: hộp xem không mang file trang của đội + không mở đường có tên khách ⇒ màn ghi rõ, không vỡ, không báo "không tải được"
await page.goto(`${base}/?man=theo-doi`);
await page.waitForSelector(".page-kpi table.matrix", { timeout: 90000 });
check("bản xem › Theo dõi: mục 8 KPI có bảng % + dòng 'Không so với trang theo dõi của đội ở chế độ xem qua Capitol', 0 dấu ⚠", flat(await page.textContent(".tracking .no-page-compare")).includes("xem qua Capitol")
  && (await page.locator("table.matrix tbody tr").count()) === 14 && (await page.locator("table.matrix .dev").count()) === 0);
await page.click('.tracking-bar .tracking-tabs button:has-text("Hội viên")');
await page.waitForSelector(".not-in-view", { timeout: 60000 });
check("bản xem › Hội viên: ghi 'chưa có ở bản xem qua Capitol', không có chữ 'Không tải được'", flat(await page.textContent(".tracking")).includes("chưa có ở bản xem qua Capitol") && !flat(await page.textContent(".tracking")).includes("Không tải được"));
await page.click('.tracking-bar .tracking-tabs button:has-text("1% gia vị")');
await page.waitForSelector(".tracking .cards", { timeout: 120000 });
check("bản xem › 1% gia vị: có ô tổng, không có chữ 'trang:' (hộp xem không so với trang của đội)", flat(await page.textContent(".tracking .cards")).includes("Tiềm năng thêm") && !flat(await page.textContent(".tracking .cards")).includes("trang:"));
// 13/11: danh sách từng khách (/api/spice-customers — tên + mã khách) bị cổng xem cấm ⇒ màn phải ghi rõ, không "Không tải được", không thẻ khách nào
await page.waitForSelector(".tracking .not-in-view", { timeout: 120000 }).catch(() => {});
const spiceText = flat(await page.textContent(".tracking"));
check("bản xem › 1% gia vị › Từng khách: ghi 'chưa có ở bản xem qua Capitol', không 'Không tải được', 0 thẻ khách", spiceText.includes("Danh sách từng khách 1 % gia vị: chưa có ở bản xem qua Capitol") && !spiceText.includes("Không tải được") && !spiceText.includes("Xem thêm"), spiceText.slice(-220));
await page.screenshot({ path: `${out}/ban_xem_theo_doi_1440.png`, fullPage: true });
check("cả lượt xem không có lời gọi nào khác GET", posts.length === 0, posts.join(","));

// Hộp Capitol THẬT (tham số thứ ba = "hop-that"): không có công tắc làm lệch ⇒ bỏ 3 phép "lệch / dữ liệu mới / khớp lại" (đã có ở cổng giả + test_xem_proxy)
if (switchFile !== "hop-that") {
// đóng khi nghi ngờ: vân tay lệch ⇒ MỘT khung, không số nào (kể cả số cũ)
writeFileSync(switchFile, "lech");
await page.waitForTimeout(WAIT_MS);
await page.goto(`${base}/?man=doi`);
await page.waitForSelector(".source-closed", { timeout: 60000 });
const closed = flat(await page.textContent("main"));
check("vân tay LỆCH ⇒ một khung 'Chưa hiện số … nguồn số đang cập nhật' + link xưởng, không còn số nào", closed.includes("nguồn số đang cập nhật") && (await page.locator(".source-closed").count()) === 1
  && (await page.locator(".source-closed a").count()) === 1 && !/\d{1,3}(\.\d{3})+ đ/.test(closed) && (await page.locator(".chat, .hero, table").count()) === 0, closed.slice(0, 160));
await page.screenshot({ path: `${out}/ban_xem_lech_1440.png` });
writeFileSync(switchFile, "moi");
await page.waitForTimeout(WAIT_MS);
await page.goto(`${base}/?man=doi`);
await page.waitForSelector(".source-closed", { timeout: 60000 });
check("hộp chính có dữ liệu tải lên mới ⇒ khung ghi 'đã có dữ liệu mới'", flat(await page.textContent(".source-closed")).includes("đã có dữ liệu mới"), flat(await page.textContent(".source-closed")).slice(0, 140));
rmSync(switchFile, { force: true });
await page.waitForTimeout(WAIT_MS);
await page.goto(`${base}/?man=doi`);
const back = await page.waitForSelector(".chip-status", { timeout: 90000 }).then(() => true, () => false);
check("khớp lại ⇒ trang hiện số trở lại", back && (await page.locator(".source-closed").count()) === 0 && flat(await page.textContent("main")).includes(new Intl.NumberFormat("vi-VN").format(direct.team_total)));
}
await page.screenshot({ path: `${out}/ban_xem_khop_1440.png` });
check("1440 không tràn ngang", (await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)) === 0);
check("0 lỗi JS (lỗi tải 409 của trình duyệt lúc lệch không tính)", errors.length === 0, errors.slice(0, 3).join(" ¦ "));
await browser.close();
console.log(`${results.filter(Boolean).length} / ${results.length} đạt`);
process.exit(results.every(Boolean) ? 0 : 1);
