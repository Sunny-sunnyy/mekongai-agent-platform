// Kiểm trên trình duyệt: kiểu xem "Theo chỉ tiêu" có thêm các khoản THƯỞNG THÊM ngoài 8 KPI. Không gọi mô hình (0 USD).
// Chạy từ kpi_agent/dashboard/ khi bộ thử đang bật KHÔNG mã phiên (backend RAW 31/10 + trang):
//   node kiem/kiem_khoan_ngoai_kpi.mjs http://127.0.0.1:<cổng trang thử> http://127.0.0.1:<cổng backend thử> <thư mục ảnh NGOÀI repo>
// Số mong đợi (RAW 31/10, đã kiểm độc lập 11/11): tổng 9 khoản 19.026.300 đ; 8 KPI 152.360.000 đ (mốc 7 theo trang sếp 16/11; trước 126.660.000).
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const [base, backend, out] = process.argv.slice(2);
const exe = process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell";
const browser = await chromium.launch({ executablePath: exe });
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "ĐẠT  " : "TRƯỢT"} ${name}${detail ? " — " + detail : ""}`); };
const errors = [];
const flat = (text) => text.replace(/\s+/g, " ").trim();
const dong = (text) => Number(text.replace(/[^\d-]/g, ""));
const api = await (await fetch(`${backend}/api/extra-bonus`)).json();

const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
await page.goto(`${base}/?man=chi-tieu`);
await page.waitForSelector('nav[aria-label="Chọn khoản thưởng thêm"] button');
await page.click('.view-switch button:text-is("NVxx")');   // hiện mã che để so từng người với backend
const itemButtons = page.locator('nav[aria-label="Chọn khoản thưởng thêm"] button');
const labels = (await itemButtons.allTextContents()).map(flat);
check("hàng 'Thưởng thêm' có đủ số khoản backend trả, hiện bằng TÊN khoản (không bắt đầu bằng 'Thưởng NN:')", labels.length === api.items.length && labels.length === 9 && labels.every((label) => !/^Thưởng\s/.test(label)), labels.join(" | ").slice(0, 300));
check("mở ?man=chi-tieu không tham số ⇒ vẫn là KPI đầu như cũ", flat(await page.textContent('nav[aria-label="Chọn chỉ tiêu"] button.on')) === "Turnover" && (await page.locator(".outside-note").count()) === 0);

let sum = 0, rowsOk = true, paidOk = true;
for (let at = 0; at < labels.length; at += 1) {
  const item = api.items[at];
  await itemButtons.nth(at).click();
  await page.waitForFunction((code) => document.querySelector(".kpi-view .hero-kicker")?.textContent.includes(code), item.item);
  const total = dong(await page.textContent(".kpi-view .kpi-total"));
  sum += total;
  const rows = await page.$$eval(".kpi-table tbody tr", (trs) => trs.map((tr) => [tr.querySelector("th").textContent.trim(), tr.querySelectorAll("td")[3].textContent]));
  const want = Object.fromEntries(api.lines.filter((line) => line.item === item.item).map((line) => [line.employee_id, line.amount]));
  if (total !== item.team_total || rows.length !== Object.keys(want).length || !rows.every(([id, money]) => dong(money) === want[id])) { rowsOk = false; console.log("  lệch ở khoản", item.item, total, item.team_total, rows.length); }
  if (!flat(await page.textContent(".kpi-view .hero-sub")).includes(`${item.paid_count} / 14`)) paidOk = false;
}
check("9 khoản: tổng từng khoản + tiền từng người trên màn = đúng số /api/extra-bonus", rowsOk);
check("9 khoản: 'N / 14 người có tiền' = paid_count của backend", paidOk);
check("cộng 9 khoản trên màn = 19.026.300 đ = tổng backend", sum === 19026300 && api.extra_bonus_team_total === 19026300, String(sum));
const note = flat(await page.textContent(".outside-note"));
check("có dòng 'khoản ngoài 8 KPI — không nằm trong lương kinh doanh 152.360.000 đ'", note.includes("ngoài 8 KPI") && note.includes("152.360.000"), note);
check("dòng chân nói rõ chưa có 'còn thiếu / mốc kế' cho khoản thưởng thêm", (await page.textContent(".kpi-view .foot")).includes("backend chưa trả"));
// đơn vị theo cách đo backend trả (/api/tracking, lần đầu ≈ 10 giây): khoản đếm thùng ghi "thùng" ở tiêu đề cột; khoản doanh số hiện "đ" trong ô
await itemButtons.nth(0).click();
await page.waitForFunction(() => document.querySelector(".kpi-table thead .sub")?.textContent === "thùng", null, { timeout: 60000 }).catch(() => {});
check("khoản đếm thùng: tiêu đề cột Thực hiện / Ngưỡng ghi đơn vị 'thùng' + có dòng 'Luật của khoản'", (await page.$$eval(".kpi-table thead .sub", (els) => els.map((el) => el.textContent))).join() === "thùng,thùng"
  && flat(await page.textContent(".kpi-head")).includes("Luật của khoản:"), flat(await page.textContent(".kpi-head")).slice(0, 220));
await itemButtons.nth(5).click();
await page.waitForFunction(() => /đ$/.test(document.querySelector(".kpi-table tbody tr td")?.textContent ?? ""), null, { timeout: 60000 }).catch(() => {});
check("khoản doanh số: ô Thực hiện / Ngưỡng có 'đ', tiêu đề cột không ghi đơn vị khác", /đ$/.test(await page.textContent(".kpi-table tbody tr td")) && (await page.locator(".kpi-table thead .sub").count()) === 0,
  await page.textContent(".kpi-table tbody tr td"));
await itemButtons.nth(8).click();
await page.screenshot({ path: `${out}/theo_chi_tieu_thuong_them_1440.png` });
await page.click('nav[aria-label="Chọn chỉ tiêu"] button:has-text("PC_BT")');
check("bấm lại một KPI ⇒ về màn 8 KPI như cũ (không còn dòng 'ngoài 8 KPI')", (await page.locator(".outside-note").count()) === 0 && flat(await page.textContent('nav[aria-label="Chọn chỉ tiêu"] button.on')) === "PC_BT"
  && (await page.locator('nav[aria-label="Chọn khoản thưởng thêm"] button.on').count()) === 0);
check("1440 không tràn ngang", (await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)) === 0);

const third = api.items[2].item;
for (const [query, wantKicker, wantKpi] of [[`?man=chi-tieu&kpi=${third.toLowerCase()}`, third, null], ["?man=chi-tieu&kpi=T99", null, "Turnover"], ["?man=chi-tieu&kpi=PC_ON", null, "PC_ON"], ["?kpi=<script>alert(1)</script>&man=chi-tieu", null, "Turnover"]]) {
  await page.goto(`${base}/${query}`);
  await page.waitForSelector('nav[aria-label="Chọn khoản thưởng thêm"] button');
  await page.waitForSelector(".kpi-view .kpi-total");
  const kicker = flat(await page.textContent(".kpi-view .hero-kicker"));
  const onKpi = await page.locator('nav[aria-label="Chọn chỉ tiêu"] button.on').count() ? flat(await page.textContent('nav[aria-label="Chọn chỉ tiêu"] button.on')) : null;
  check(`đường ${query} ⇒ ${wantKicker ? "khoản " + wantKicker : "KPI " + wantKpi}`, wantKicker ? kicker.includes(wantKicker) && onKpi === null : onKpi === wantKpi, `${kicker} · ${onKpi}`);
}
await page.setViewportSize({ width: 390, height: 900 });
await page.goto(`${base}/?man=chi-tieu&kpi=${third}`);
await page.waitForSelector(".outside-note");
check("390 px: tên 3 khoản xúc xích hiện ĐỦ trong nút (xuống dòng, không cắt '…')", await page.$$eval('nav[aria-label="Chọn khoản thưởng thêm"] button', (els) => els.every((el) => el.scrollWidth <= el.clientWidth + 1 && getComputedStyle(el).textOverflow !== "ellipsis")));
check("390 px không tràn ngang", (await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)) === 0);
await page.screenshot({ path: `${out}/theo_chi_tieu_thuong_them_390.png` });
check("0 lỗi JS / console", errors.length === 0, errors.slice(0, 3).join(" ¦ "));
await browser.close();
console.log(`${results.filter(Boolean).length} / ${results.length} đạt`);
process.exit(results.every(Boolean) ? 0 : 1);
