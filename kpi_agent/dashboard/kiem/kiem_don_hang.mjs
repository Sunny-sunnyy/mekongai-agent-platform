// Kiểm trên trình duyệt: "Xem đơn được tính" ở màn Từng nhân viên. Không gọi mô hình (0 USD).
// Chạy từ kpi_agent/dashboard/ khi bộ thử đang bật KHÔNG mã phiên (backend RAW 31/10 + trang):
//   node kiem/kiem_don_hang.mjs http://127.0.0.1:<cổng trang thử> http://127.0.0.1:<cổng backend thử> <thư mục ảnh NGOÀI repo>
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const [base, backend, out] = process.argv.slice(2);
const exe = process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell";
const browser = await chromium.launch({ executablePath: exe });
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "ĐẠT  " : "TRƯỢT"} ${name}${detail ? " — " + detail : ""}`); };
const errors = [];
let dialogs = 0;
const flat = (text) => text.replace(/\s+/g, " ").trim();
const vi = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 });
const api = async (employee, kpi) => (await fetch(`${backend}/api/order-lines?employee_id=${employee}&kpi=${kpi}`)).json();
const board = await (await fetch(`${backend}/api/team-board`)).json();

const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("favicon")) errors.push(m.text()); });
page.on("dialog", (d) => { dialogs += 1; d.dismiss(); });
await page.goto(`${base}/?man=nhan-vien&nv=NV05`);
await page.waitForSelector(".emp-card");
const card = (kpi) => page.locator(".emp-card", { has: page.locator(`h3:text-is("${kpi}")`) });
check("mỗi thẻ chỉ tiêu có nút 'Xem đơn được tính'; chưa bấm thì chưa có danh sách", (await page.locator('.emp-card button:has-text("Xem đơn được tính")').count()) === 8 && (await page.locator(".orders").count()) === 0);

const pcbt = await api("NV05", "PC_BT");
await card("PC_BT").locator('button:has-text("Xem đơn được tính")').click();
await page.waitForSelector(".orders .stat", { timeout: 90000 });
const tiles = await page.$$eval(".orders .stat .v", (els) => els.map((el) => el.textContent.trim()));
check("PC_BT của NV05: 3 ô = số backend (được tính · không tính · số khớp bảng thưởng)", tiles.join("|") === [pcbt.summary.counted, pcbt.summary.not_counted, pcbt.summary.counted_value].map((n) => vi.format(n)).join("|")
  && pcbt.summary.counted_value === board.employees.find((e) => e.employee_id === "NV05").cells.PC_BT.actual, tiles.join(" | "));
check("ô thứ ba ghi 'khớp số thực hiện trong bảng thưởng'", flat(await page.textContent(".orders .stats")).includes("khớp số thực hiện trong bảng thưởng"));
const sel = (label) => page.locator(`.orders select[aria-label="${label}"]`);
const count = async () => flat(await page.textContent(".orders .outlet-count"));
await sel("Lọc được tính hay không").selectOption("co");
check("lọc 'Được tính' = số đơn được tính; mọi dòng đang hiện ghi ✓", (await count()).includes(`/ ${vi.format(pcbt.summary.counted)} dòng`) && await page.$$eval(".orders tbody tr", (trs) => trs.length > 0 && trs.every((tr) => tr.textContent.includes("✓ được tính"))), await count());
await sel("Lọc được tính hay không").selectOption("khong");
check("lọc 'Không được tính' = số đơn không tính; dòng nào cũng có lý do", (await count()).includes(`/ ${vi.format(pcbt.summary.not_counted)} dòng`) && await page.$$eval(".orders tbody tr", (trs) => trs.every((tr) => tr.lastElementChild.textContent.trim() !== "—")), await count());
await sel("Lọc được tính hay không").selectOption("");
const reasons = Object.entries(pcbt.summary.by_reason);
check("ô lọc lý do liệt kê đúng các lý do backend trả kèm số dòng", (await sel("Lọc theo lý do").locator("option").count()) === reasons.length + 1, reasons.map(([name, n]) => `${n}× ${name.slice(0, 40)}`).join(" · "));
if (reasons.length) {
  await sel("Lọc theo lý do").selectOption(reasons[0][0]);
  check("lọc theo một lý do ⇒ đúng số dòng của lý do đó", (await count()).includes(`/ ${vi.format(reasons[0][1])} dòng`), await count());
  await sel("Lọc theo lý do").selectOption("");
}
const firstOrder = pcbt.rows[0].order_num;
await page.fill(".orders input[type=search]", firstOrder);
check("tìm theo số đơn ra dòng của đơn đó", (await page.locator(".orders tbody tr").count()) >= 1 && (await page.textContent(".orders tbody tr")).includes(firstOrder));
for (const bad of ["<script>alert(1)</script>", "%", "'"]) await page.fill(".orders input[type=search]", bad);
check("ô tìm chữ lạ: không chạy mã", dialogs === 0);
await page.fill(".orders input[type=search]", "");
check("có dòng nói rõ 'màn này không nói một đơn đáng bao nhiêu tiền'", (await page.textContent(".orders .outlet-notes")).includes("không nói một đơn đáng bao nhiêu tiền"));
await page.screenshot({ path: `${out}/don_duoc_tinh_pc_bt_1440.png` });

const aso = await api("NV05", "ASO_ALL");
await card("ASO_ALL").locator('button:has-text("Xem đơn được tính")').click();
await page.waitForFunction(() => document.querySelector(".orders-head h3")?.textContent.includes("ASO_ALL") && document.querySelector(".orders .stat"), null, { timeout: 60000 });
check("ASO_ALL: mỗi dòng một CỬA HÀNG, số được tính = số backend", flat(await page.textContent(".orders thead")).startsWith("Cửa hàng") && (await page.$$eval(".orders .stat .v", (els) => els[0].textContent.trim())) === vi.format(aso.summary.counted), flat(await page.textContent(".orders .stats")).slice(0, 120));
await card("LPPC").locator('button:has-text("Xem đơn được tính")').click();
// bậc 2 (13/11): LPPC đã có ⇒ nút trên thẻ mở được danh sách (trước: lời "chưa có danh sách đơn"); 8 / 8 chỉ tiêu có danh sách
await page.waitForFunction(() => document.querySelector(".orders-head h3")?.textContent.includes("LPPC") && document.querySelector(".orders .stat"), null, { timeout: 60000 });
check("LPPC (bậc 2): nút trên thẻ mở danh sách, không còn lời 'chưa có danh sách đơn'", (await page.locator(".orders table").count()) === 1 && (await page.locator(".orders .notice.refused").count()) === 0);
await page.selectOption(".emp-head select", "NV04");
check("đổi nhân viên ⇒ danh sách đóng lại (không hiện đơn của người trước)", (await page.locator(".orders").count()) === 0);

const turnover = await api("NV04", "Turnover");
await page.goto(`${base}/?man=nhan-vien&nv=NV04&kpi=turnover`);
await page.waitForSelector(".orders .stat", { timeout: 90000 });
const third = await page.$$eval(".orders .stat .v", (els) => els[2].textContent.trim());
check("đường ?man=nhan-vien&nv=NV04&kpi=turnover mở thẳng danh sách Turnover; tổng được tính = bảng thưởng (tới đồng)", (await page.textContent(".orders-head h3")).includes("Turnover")
  && third === `${vi.format(turnover.summary.counted_value)} đ` && Math.abs(turnover.summary.counted_value - board.employees.find((e) => e.employee_id === "NV04").cells.Turnover.actual) < 0.5, third);
check("Turnover NV04: lý do 'không thuộc tuyến' có trong ô lọc (người này có đơn ngoài tuyến)", Object.keys(turnover.summary.by_reason).some((name) => name.includes("không thuộc tuyến")), Object.entries(turnover.summary.by_reason).map(([n, c]) => `${c}× ${n.slice(0, 30)}`).join(" · "));
check("1440 không tràn ngang", (await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)) === 0);
// ---- bậc 2 (13/11): LPPC (tỷ lệ mã ÷ đơn) + ASO_Focus_2 (cửa hàng) — số so với BẢNG ĐỘI (/api/team-board, đường khác /api/order-lines), đọc chữ TRÊN MÀN
const nv04 = board.employees.find((e) => e.employee_id === "NV04").cells;
const lppc = await api("NV04", "LPPC");
await page.goto(`${base}/?man=nhan-vien&nv=NV04&kpi=LPPC`);
await page.waitForSelector(".orders .stat", { timeout: 90000 });
const lppcStat = flat(await page.locator(".orders .stat").nth(2).textContent());
const floor2 = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(Math.floor(nv04.LPPC.actual * 100 + 1e-9) / 100);
check("LPPC NV04: ô thứ 3 = tỷ lệ bảng đội (2 số lẻ làm tròn xuống) + 'x mã ÷ y đơn'; số dòng ✓ = số đơn; x ÷ y = số bảng đội",
  lppcStat.startsWith(floor2) && lppcStat.includes(`${vi.format(lppc.summary.lines)} mã ÷ ${vi.format(lppc.summary.orders)} đơn`) && lppc.summary.counted === lppc.summary.orders
  && Math.abs(Math.floor(lppc.summary.lines / lppc.summary.orders * 100 + 1e-9) / 100 - nv04.LPPC.actual) < 1e-9   /* bảng đội trả LPPC đã làm tròn xuống 2 số lẻ */, lppcStat);
await page.selectOption('.orders select[aria-label="Lọc được tính hay không"]', "co");
const lppcShown = flat(await page.textContent(".orders .outlet-count"));
check("LPPC NV04: lọc 'Được tính' ⇒ đúng số đơn của mẫu số", lppcShown.includes(`/ ${vi.format(lppc.summary.orders)} dòng theo bộ lọc`), lppcShown);
check("LPPC NV04: dòng ✓ ghi 'n mã'; ghi chú cuối nói rõ quy ước đơn ✓ cùng ngày", flat(await page.locator(".orders tbody tr:not(.not-counted)").first().textContent()).includes(" mã")
  && flat(await page.textContent(".orders .outlet-notes")).includes("là quy ước của màn này"));
await page.locator(".orders").screenshot({ path: `${out}/don_duoc_tinh_lppc_1440.png` });
const homey = await api("NV04", "ASO_Focus_2");
await page.goto(`${base}/?man=nhan-vien&nv=NV04&kpi=ASO_Focus_2`);
await page.waitForSelector(".orders .stat", { timeout: 90000 });
const homeyHead = await page.$$eval(".orders thead th", (els) => els.map((el) => el.textContent.trim()));
check("ASO_Focus_2 NV04: mỗi dòng một cửa hàng; số cửa hàng được tính = bảng đội", homeyHead[0] === "Cửa hàng" && homey.unit === "outlet" && homey.summary.counted === nv04.ASO_Focus_2.actual
  && flat(await page.locator(".orders .stat").first().textContent()).startsWith(`${homey.summary.counted}`), `${homey.summary.counted} / bảng ${nv04.ASO_Focus_2.actual}`);
check("ASO_Focus_2 NV04: lý do cửa hàng không được tính nêu đúng nhóm hàng (Homey túi 2,9 kg)", Object.keys(homey.summary.by_reason).some((name) => name.includes("Homey túi 2,9 kg")),
  Object.keys(homey.summary.by_reason).join(" · ").slice(0, 200));
await page.goto(`${base}/?man=nhan-vien&nv=NV04&kpi=<script>alert(1)</script>`);
await page.waitForSelector(".emp-card");
check("tên chỉ tiêu lạ trên đường dẫn ⇒ không mở gì, không chạy mã", (await page.locator(".orders").count()) === 0 && dialogs === 0);
await page.setViewportSize({ width: 390, height: 900 });
await page.goto(`${base}/?man=nhan-vien&nv=NV05&kpi=PC_ON`);
await page.waitForSelector(".orders .stat", { timeout: 90000 });
check("390 px không tràn ngang (bảng cuộn trong khung)", (await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)) === 0);
await page.locator(".orders").screenshot({ path: `${out}/don_duoc_tinh_390.png` });
check("0 lỗi JS / console", errors.length === 0, errors.slice(0, 3).join(" ¦ "));
await browser.close();
console.log(`${results.filter(Boolean).length} / ${results.length} đạt`);
process.exit(results.every(Boolean) ? 0 : 1);
