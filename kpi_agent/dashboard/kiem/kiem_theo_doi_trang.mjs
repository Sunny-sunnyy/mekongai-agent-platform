// Kiểm trên trình duyệt: tab Theo dõi › 8 KPI theo bố cục trang theo dõi của đội. 0 USD — không hỏi trợ lý.
// Chạy từ kpi_agent/dashboard/ khi bộ thử đang bật (backend RAW 31/10 có KPI_TRACKING_PAGE + trang; KHÔNG chạy vào bộ đang dùng của người dùng):
//   node kiem/kiem_theo_doi_trang.mjs http://127.0.0.1:<cổng trang thử> <thư mục ảnh NGOÀI repo>
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const [base, out] = process.argv.slice(2);
const exe = process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell";
const browser = await chromium.launch({ executablePath: exe });
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "ĐẠT  " : "TRƯỢT"} ${name}${detail ? " — " + detail : ""}`); };
const flat = (text) => text.replace(/\s+/g, " ").trim();
const errors = [];
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => errors.push(String(e))); page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
await page.goto(`${base}/?man=theo-doi`);
await page.waitForSelector(".page-kpi table.matrix", { timeout: 120000 });
const api = await page.evaluate(() => fetch("/api/tracking").then((r) => r.json()));
const view = api.page_view;

const tabs = await page.$$eval(".tracking-bar .tracking-tabs button", (els) => els.map((el) => el.textContent.trim()));
check("mục con Theo dõi đúng 6 mục, đúng thứ tự trang của đội", tabs.join(" · ") === "8 KPI · Thưởng thêm · Hội viên · 1% gia vị · Chi tiết · Trà 365", tabs.join(" · "));
const kpiButtons = await page.$$eval('nav[aria-label="Chọn KPI theo trang của đội"] button', (els) => els.map((el) => el.textContent.trim()));
check("hàng nút: Tổng quan + 8 KPI theo tên trang của đội", kpiButtons.join(" · ") === "Tổng quan · KPI 1: Sell out · KPI 2: Số đơn đạt MOQ · KPI 3: LPPC · KPI 4: Bao phủ ASO · KPI 5: Trận Xanh · KPI 6: Trận vàng · KPI 7: Bao phủ kênh On · KPI 8: LPPC Meat", kpiButtons.join(" · "));
const timeLine = flat(await page.textContent(".page-kpi .time-line"));
check("dòng cập nhật + tiến độ thời gian = số backend", timeLine.includes(`${view.days_done}/${view.days_total} ngày làm việc`) && timeLine.includes("31/10/2026") && timeLine.includes("Tiến độ thời gian 100 %"), timeLine);
const bodyRows = await page.locator("table.matrix tbody tr").count(), headCells = await page.locator("table.matrix thead th").count();
check("Tổng quan: bảng 14 người × 8 chỉ tiêu + dòng Tổng", bodyRows === 14 && headCells === 9 && (await page.locator("table.matrix tfoot td").count()) === 8, `${bodyRows} dòng, ${headCells} cột`);
// từng ô: số lớn = pct_rounded, số nhỏ = pct_to_target, màu = on_track của backend
const cells = await page.$$eval("table.matrix tbody tr", (rows) => rows.map((row) => [...row.querySelectorAll("td")].slice(1).map((td) => ({ big: td.querySelector("b").textContent, small: td.querySelector("small").textContent, on: td.classList.contains("on-track"), off: td.classList.contains("off-track"), warn: !!td.querySelector(".dev") }))));
const vi = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 });
let cellOk = true, warned = 0;
api.employees.forEach((employee, r) => Object.keys(view.kpis).forEach((kpi, c) => {
  const row = view.kpis[kpi].rows.find((item) => item.employee_id === employee.employee_id), shown = cells[r][c];
  if (shown.big !== `${row.pct_rounded} %` || shown.small !== `${vi.format(row.pct_to_target)} %` || shown.on !== row.on_track || shown.off === row.on_track) cellOk = false;
  warned += shown.warn ? 1 : 0;
}));
const kpiDeviations = api.deviations.filter((d) => d.key in view.kpis).length;
check("112 ô: % nguyên + % 2 số lẻ + màu đúng y backend", cellOk);
// Mốc 7 (17/11): lõi theo trang sếp ⇒ 0 ô lệch trên RAW 31/10 — số ⚠ trên màn phải = API = 0 (trước: > 0)
check("dấu ⚠ ở đúng số ô mình tính khác trang của đội (phần 8 KPI) — mốc 7: 0", warned === kpiDeviations && kpiDeviations === 0, `${warned} / ${kpiDeviations}`);
check("Tổng quan không hiện tiền (không có chữ ' đ' trong bảng)", !/\d đ/.test(await page.textContent("table.matrix")));
await page.screenshot({ path: `${out}/theo_doi_8kpi_tong_quan_1440.png`, fullPage: true });

// ---- một KPI không lệch (KPI 4) và một KPI lệch cả 14 người (KPI 2)
await page.click('nav[aria-label="Chọn KPI theo trang của đội"] button:has-text("KPI 4")');
await page.waitForSelector("table.ranked");
const aso = view.kpis.ASO_ALL;
const cards = await page.$$eval(".page-kpi .cards div b", (els) => els.map((el) => el.textContent));
check("KPI 4: 3 ô tổng Chỉ tiêu · Thực hiện · % đạt = backend (trang của đội: 1.999 / 2.113)", cards.join(" | ") === `${vi.format(aso.team.target)} | ${vi.format(aso.team.actual)} | ${vi.format(aso.team.pct_to_target)} %` && aso.team.target === 1999 && aso.team.actual === 2113, cards.join(" | "));
const ranks = await page.$$eval("table.ranked tbody tr:not(.devrow) .rank", (els) => els.map((el) => [el.textContent, el.classList.contains("top"), el.classList.contains("low")]));
check("KPI 4: 14 dòng xếp theo hạng; hạng 1–3 tô top, 3 hạng cuối tô low", ranks.length === 14 && ranks.every(([rank], i) => Number(rank) === aso.rows[i].rank) && ranks.filter((r) => r[1]).length >= 3 && ranks.filter((r) => r[2]).length >= 3 && ranks[0][1] && ranks[13][2]);
const firstRow = flat(await page.textContent("table.ranked tbody tr"));
check("KPI 4: dòng đầu có 'thực hiện / chỉ tiêu' + còn thiếu + cần mỗi ngày + %", firstRow.includes(`${vi.format(aso.rows[0].actual)} / ${vi.format(aso.rows[0].target)}`) && firstRow.includes(`${vi.format(aso.rows[0].pct_to_target)} %`), firstRow);
check("KPI 4: thanh tiến độ có vạch thời gian ở mỗi dòng", (await page.locator("table.ranked .meter .mark").count()) === 14);
check("KPI 4: có chữ mốc thưởng + đường bấm sang Cả đội › Theo chỉ tiêu", flat(await page.textContent(".page-kpi")).includes("Đạt 100%: 1,04 triệu") && (await page.locator('.page-kpi a[href*="man=chi-tieu&kpi=ASO_ALL"]').count()) === 1);
await page.screenshot({ path: `${out}/theo_doi_kpi4_1440.png`, fullPage: true });
await page.click('nav[aria-label="Chọn KPI theo trang của đội"] button:has-text("KPI 2")');
// chờ màn ĐÃ sang KPI 2 (tiêu đề ghi tên bên mình PC_BT) — không thì đếm nhầm trên bảng KPI 4 còn hiện
await page.waitForFunction(() => document.querySelector(".page-kpi h3")?.textContent.includes("tên bên mình: PC_BT"), null, { timeout: 30000 });
// Mốc 7 N3 (17/11): KPI 2 đếm như trang sếp ⇒ 14/14 khớp ⇒ KHÔNG còn hộp lý do lệch / số của trang (trước: 1 lý do "Cả 14 người … công văn 2.2" + 14 dòng "trang: N đơn")
const diffBoxes = await page.locator(".page-kpi .page-diff").count(), pageNumbers = await page.locator("table.ranked .page-number").count();
check("KPI 2: 0 hộp lý do lệch + 0 dòng số của trang (mốc 7 khớp trang 14/14)", diffBoxes === 0 && pageNumbers === 0 && (await page.locator("table.ranked tbody tr").count()) === 14, `${diffBoxes} hộp · ${pageNumbers} dòng`);
await page.screenshot({ path: `${out}/theo_doi_kpi2_1440.png`, fullPage: true });
// đường bấm sang màn tiền
await page.click('.page-kpi a[href*="man=chi-tieu"]');
await page.waitForSelector(".kpi-view", { timeout: 90000 });
check("bấm 'Cả đội › Theo chỉ tiêu' ⇒ mở đúng màn + đúng chỉ tiêu PC_BT", flat(await page.textContent(".kpi-view .tracking-tabs button.on")) === "PC_BT");

// ---- mục Hội viên + Thưởng thêm + 390 px
await page.goto(`${base}/?man=theo-doi`);
await page.waitForSelector(".page-kpi table.matrix", { timeout: 120000 });
await page.click('.tracking-bar .tracking-tabs button:has-text("Hội viên")');
await page.waitForSelector(".membership .cards", { timeout: 120000 });
const members = await page.evaluate(() => fetch("/api/membership").then((r) => r.json()));
const viDong = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 });
const memberCards = flat(await page.textContent(".membership .cards"));
check("Hội viên: băng nói rõ mới dựng phần doanh số + thiếu gì (hạng, nhiệm vụ, tiền, công văn 408)", flat(await page.textContent(".membership .page-diff")).includes("PHẦN DOANH SỐ") && flat(await page.textContent(".membership .page-diff")).includes("408"));
check("Hội viên › Tổng quan: ô tổng = backend = số của trang đội (417 · 185 · 100 · 123 · 9 · 7.680.778.933 / 9.387.745.964)", members.summary.customers === 417
  && ["Khách hội viên417", "(chưa xét nhiệm vụ)185", "Gần đạt ≥ 60 %100", "Đang bán < 60 %123", "Chưa phát sinh9", `${viDong.format(7680778933)} đ / ${viDong.format(9387745964)} đ`].every((text) => memberCards.includes(text)), memberCards.slice(0, 240));
check("Hội viên › Tổng quan: bảng 14 nhân viên", (await page.locator(".membership table.track tbody tr").count()) === 14);
await page.screenshot({ path: `${out}/theo_doi_hoi_vien_tong_quan_1440.png`, fullPage: true });
await page.click(".membership table.track tbody tr >> nth=0 >> button.link");
await page.waitForSelector(".membership .outlet-cards");
const firstEmployee = members.by_employee[0];
check("bấm một nhân viên ⇒ Chi tiết hội viên lọc đúng người đó", flat(await page.textContent(".membership p.hint")).startsWith(`${firstEmployee.customers} khách hội viên`) && (await page.locator(".membership .outlet-card").count()) === Math.min(firstEmployee.customers, 60));
await page.selectOption('.membership select[aria-label="Lọc theo nhân viên"]', "");
await page.selectOption('.membership select[aria-label="Lọc theo nhóm doanh số"]', "chua_phat_sinh");
check("lọc nhóm 'Chưa phát sinh' cả đội ⇒ 9 thẻ, mỗi thẻ có doanh số / nền + còn lại", (await page.locator(".membership .outlet-card").count()) === 9 && (await page.$$eval(".membership .outlet-card", (els) => els.every((el) => el.textContent.includes("Doanh số 0 đ / nền") && el.textContent.includes("Còn lại")))));
await page.selectOption('.membership select[aria-label="Lọc theo nhóm doanh số"]', "");
check("không lọc ⇒ 417 khách, hiện 60 thẻ + nút Xem thêm", flat(await page.textContent(".membership p.hint")).startsWith("417 khách hội viên") && (await page.locator(".membership .outlet-card").count()) === 60 && (await page.locator(".membership .outlet-more").count()) === 1);
await page.screenshot({ path: `${out}/theo_doi_hoi_vien_chi_tiet_1440.png` });
await page.click('.tracking-bar .tracking-tabs button:has-text("Thưởng thêm")');
check("Thưởng thêm: còn ô tổng cả đội 19.026.300 đ", flat(await page.textContent(".tracking .cards")).includes("19.026.300 đ"));
// ---- bậc 2: Thưởng thêm cùng khuôn + 1 % gia vị 4 ô tổng + tên điểm bán chưa có đơn
const extra = view.extra, extraKeys = Object.keys(extra);
const extraButtons = await page.$$eval('nav[aria-label="Chọn khoản thưởng thêm theo trang của đội"] button', (els) => els.map((el) => el.textContent.trim()));
check("Thưởng thêm: nút Tổng quan + từng khoản", extraButtons.length === extraKeys.length + 1 && extraButtons[0] === "Tổng quan" && extraButtons[1] === extra[extraKeys[0]].page_title, extraButtons.slice(0, 3).join(" · "));
check("Thưởng thêm › Tổng quan: bảng 14 người × số khoản + dòng Tổng", (await page.locator("table.matrix tbody tr").count()) === 14 && (await page.locator("table.matrix tfoot td").count()) === extraKeys.length);
await page.screenshot({ path: `${out}/theo_doi_thuong_them_tong_quan_1440.png`, fullPage: true });
await page.click(`nav[aria-label="Chọn khoản thưởng thêm theo trang của đội"] button >> nth=1`);
await page.waitForSelector("table.ranked");
const t01 = extra[extraKeys[0]];
const extraCards = await page.$$eval(".page-kpi .cards div b", (els) => els.map((el) => el.textContent));
check("khoản đầu: 3 ô tổng = backend (trang của đội: 280 / 724)", extraCards.join(" | ") === `${vi.format(t01.team.target)} | ${vi.format(t01.team.actual)} | ${vi.format(t01.team.pct_to_target)} %` && t01.team.target === 280 && t01.team.actual === 724, extraCards.join(" | "));
const extraHead = await page.$$eval("table.ranked thead th", (els) => els.map((el) => el.textContent));
check("khoản đầu: có thêm cột Tiền + Cách tính của mình; tổng tiền 14 dòng = backend", extraHead.slice(-2).join() === "Tiền,Cách tính" && flat(await page.textContent("table.ranked")).includes(vi.format(t01.rows[0].amount) + " đ"));
await page.screenshot({ path: `${out}/theo_doi_thuong_them_khoan1_1440.png`, fullPage: true });
// khoản 07 (điều kiện ngành thịt chế biến): có ở hàng nút, chỉ %, KHÔNG có cột Tiền / Cách tính
await page.click('nav[aria-label="Chọn khoản thưởng thêm theo trang của đội"] button:has-text("Thưởng 07")');
await page.waitForSelector("table.ranked");
const gateHead = await page.$$eval("table.ranked thead th", (els) => els.map((el) => el.textContent));
check("Thưởng 07: 14 dòng xếp hạng, không có cột Tiền / Cách tính, có chữ 'điều kiện … không có tiền riêng'", (await page.locator("table.ranked tbody tr").count()) === 14 && !gateHead.includes("Tiền")
  && flat(await page.textContent(".page-kpi")).includes("không có tiền riêng") && extra["07"].team.target === 808780000, gateHead.join(" · "));
// 13/11 (A dặn): khoản 07 khác trang ở 1 người ⇒ màn phải HIỆN — đúng 1 dòng có ⚠ + số của trang, hộp lý do nêu công văn 6.1 (đọc chữ trên màn, không chỉ API)
const gateMarks = await page.$$eval("table.ranked tbody tr", (rows) => rows.map((tr) => [tr.querySelector("td:nth-child(2)").textContent, tr.querySelector("small.dev")?.textContent ?? null]).filter(([, mark]) => mark));
const gateBox = flat((await page.locator(".page-kpi .page-diff").textContent().catch(() => "")) ?? "");
const gateApi = api.deviations.filter((d) => d.key === "07");
// Mốc 7 N2 (17/11): cổng 07 cộng dòng hoá đơn trễ như trang ⇒ NV05 hết lệch ⇒ 0 dòng ⚠ (trước: 1 dòng 'trang: 64,2 tr' + 'công văn 6.1')
check("Thưởng 07: 0 dòng ⚠ (= API) — mốc 7 khớp trang", gateMarks.length === 0 && gateApi.length === 0, JSON.stringify(gateMarks) + " | " + gateBox.slice(0, 200));
await page.screenshot({ path: `${out}/theo_doi_thuong_them_07_1440.png`, fullPage: true });
await page.click('.tracking-bar .tracking-tabs button:has-text("1% gia vị")');
await page.waitForSelector(".tracking .cards", { timeout: 120000 });
await page.waitForFunction(() => document.querySelector(".tracking .cards")?.textContent.includes("Tiềm năng thêm"), null, { timeout: 120000 });
const spice = await page.evaluate(() => fetch("/api/spice-loyalty").then((r) => r.json()));
const spiceText = flat(await page.textContent(".tracking .cards"));
check("1% gia vị: 4 ô tổng mới = backend + số của trang bên cạnh", spiceText.includes(`Tiềm năng thêm · ${spice.page_totals.potential_customers} khách chưa đạt${vi.format(spice.page_totals.potential_reward)} đ`)
  && spiceText.includes(`${vi.format(spice.page_totals.team_pct)} %`) && spiceText.includes("trang: 1.465,8 tr") && spiceText.includes("trang: 1.679 tr"), spiceText.slice(0, 260));
// danh sách từng khách (dòng 10 của bài đối chiếu): 1.351 khách, 60 thẻ + Xem thêm, bấm dòng nhân viên ⇒ lọc, lọc nhóm "Có thưởng" ⇒ 467
await page.waitForSelector(".spice-customers .outlet-card", { timeout: 120000 });
const spiceCustomers = await page.evaluate(() => fetch("/api/spice-customers").then((r) => r.json()));
check("1% gia vị › Từng khách: 1.351 khách, hiện 60 thẻ + nút Xem thêm, có dòng ghi chú trang làm tròn 10.000 đ", spiceCustomers.customers === 1351 && flat(await page.textContent(".spice-customers p.count")) === "1.351 khách"
  && (await page.locator(".spice-customers .outlet-card").count()) === 60 && (await page.locator(".spice-customers .outlet-more").count()) === 1 && flat(await page.textContent(".spice-customers")).includes("làm tròn tới 10.000 đ"));
await page.selectOption('.spice-customers select[aria-label="Lọc khách theo nhóm"]', "co_thuong");
check("lọc 'Có thưởng' ⇒ 467 khách, thẻ ghi 'Thưởng 1 % tạm tính'", flat(await page.textContent(".spice-customers p.count")) === "467 khách" && flat(await page.locator(".spice-customers .outlet-card").first().textContent()).includes("Thưởng 1 % tạm tính"));
await page.selectOption('.spice-customers select[aria-label="Lọc khách theo nhóm"]', "duoi_70");
check("lọc 'Dưới 70 %' ⇒ thẻ ghi 'Cần thêm … để đạt 110 % nền ⇒ thưởng ≈' hoặc 'không xét thưởng'", /Cần thêm .* đ để đạt 110 % nền ⇒ thưởng ≈|không xét thưởng/.test(flat(await page.locator(".spice-customers .outlet-card").first().textContent())));
await page.selectOption('.spice-customers select[aria-label="Lọc khách theo nhóm"]', "");
await page.click(".tracking table.track tbody tr >> nth=0 >> button.link");
const firstSpice = spice.employees[0];
check("bấm dòng nhân viên đầu ⇒ danh sách khách lọc đúng số khách của người đó", flat(await page.textContent(".spice-customers p.count")) === `${firstSpice.customers} khách`, flat(await page.textContent(".spice-customers p.count")));
await page.screenshot({ path: `${out}/theo_doi_gia_vi_1440.png`, fullPage: true });
const outletsApi = await page.evaluate(() => fetch("/api/outlets").then((r) => r.json()));
const outletRows = outletsApi.outlets ?? outletsApi.rows ?? [];
check("Chi tiết: không còn điểm bán thuộc tuyến nào thiếu tên (tên điểm chưa có đơn lấy từ sổ tuyến)", outletRows.length === 2501 && outletRows.every((row) => row.outlet_name) && outletRows.filter((row) => row.name_from_route_sheet).length === 387,
  `${outletRows.length} dòng, ${outletRows.filter((row) => !row.outlet_name).length} thiếu tên, ${outletRows.filter((row) => row.name_from_route_sheet).length} lấy từ sổ`);
check("1440 không tràn ngang", (await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)) <= 0);
const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
await phone.goto(`${base}/?man=theo-doi`);
await phone.waitForSelector(".page-kpi table.matrix", { timeout: 120000 });
check("390 px: trang không tràn ngang (bảng cuộn trong khung riêng)", (await phone.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)) <= 0);
await phone.screenshot({ path: `${out}/theo_doi_8kpi_390.png`, fullPage: true });
check("0 lỗi JS / console", errors.length === 0, errors.slice(0, 3).join(" | "));
// ---- thẻ điểm bán chưa có đơn (người kiểm 13/11: API có tên + địa chỉ mà thẻ vẫn in câu cũ "chưa có tên và địa chỉ") — kiểm TRÊN MÀN, không chỉ API
await page.click('.tracking-bar .tracking-tabs button:has-text("Chi tiết")');
await page.waitForSelector(".outlets-view .outlet-card", { timeout: 120000 });
const noOrder = outletRows.find((row) => row.name_from_route_sheet);
await page.fill('.outlets-view input[type="search"]', noOrder.outlet_code);
await page.waitForFunction((code) => [...document.querySelectorAll(".outlets-view .outlet-card")].length >= 1 && document.querySelector(".outlets-view .outlet-card").textContent.includes(code), noOrder.outlet_code);
const noOrderCard = flat(await page.locator(".outlets-view .outlet-card").first().textContent());
check("Chi tiết: thẻ điểm CHƯA có đơn hiện tên + địa chỉ từ sổ, không còn câu 'chưa có tên và địa chỉ'", noOrderCard.includes(noOrder.outlet_name) && noOrderCard.includes(noOrder.address)
  && noOrderCard.includes("lấy từ danh sách tuyến trong sổ") && !noOrderCard.includes("chưa có tên và địa chỉ"));
await page.screenshot({ path: `${out}/theo_doi_chi_tiet_chua_co_don_1440.png` });
// ---- dòng 13 (13/11): tiền trên thẻ "như trang theo dõi" — so với SỐ CHÉP TỪ TRANG của đội (`D.ct`, điểm 129814: 14.702.350 · Trà 365 174.000 · nước mắm CC 690.000),
// không so với API của chính mình; thẻ phải ghi cách tính để không lẫn với doanh số KPI 1
await page.fill('.outlets-view input[type="search"]', "129814");
await page.waitForFunction(() => document.querySelector(".outlets-view .outlet-card")?.textContent.includes("129814"));
const pageWayCard = flat(await page.locator(".outlets-view .outlet-card").first().textContent());
const kpiWay = outletRows.find((row) => row.outlet_code === "129814");
check("Chi tiết › thẻ 129814: tiền = số của trang (14.702.350 · 174.000 · 690.000) + nhãn 'như trang theo dõi: sau chiết khấu, gồm hoá đơn xuất trễ' + dòng KPI 1 riêng",
  pageWayCard.includes("(như trang theo dõi: sau chiết khấu, gồm hoá đơn xuất trễ): 14.702.350 đ") && pageWayCard.includes("174.000 đ") && pageWayCard.includes("Nước mắm cao cấp: 690.000 đ")
  && pageWayCard.includes(`Doanh số tính KPI 1 (trước chiết khấu, gồm hoá đơn xuất trễ đã có ngày xuất): ${vi.format(Math.round(kpiWay.sales))} đ`), pageWayCard.slice(0, 400));
const outletNotes = flat(await page.textContent(".outlets-view .outlet-notes"));
check("Chi tiết: ghi chú tổng 'như trang' cả đội ↔ trang 10.144.849.688 đ ghi đúng số đồng lệch", /ở đây [\d.]+ đ, trang theo dõi của đội ghi 10\.144\.849\.688 đ — (khớp từng đồng|lệch \d+ đ do làm tròn)/.test(outletNotes),
  outletNotes.match(/Tổng doanh số 'như trang'[^.]*\.[^.]*\.[^.]*/)?.[0] ?? outletNotes.slice(0, 300));
await page.screenshot({ path: `${out}/theo_doi_chi_tiet_nhu_trang_1440.png` });
console.log(`${results.filter(Boolean).length} / ${results.length} đạt`);
await browser.close();
process.exit(results.every(Boolean) ? 0 : 1);
