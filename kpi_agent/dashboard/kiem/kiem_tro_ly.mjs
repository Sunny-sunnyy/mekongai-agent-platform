// Kiểm trên trình duyệt: màn "Trợ lý" (hai cột + thẻ bước) · thanh 4 tab + đường dẫn ?man= / ?nv= / ?kpi= cũ · khung hỏi nhanh.
// Chạy từ kpi_agent/dashboard/ khi bộ thử đang bật (backend RAW 31/10 + agent + trang; KHÔNG chạy vào bộ đang dùng của người dùng):
//   node kiem/kiem_tro_ly.mjs http://127.0.0.1:<cổng trang thử> <thư mục ảnh NGOÀI repo> [that]
// Trình duyệt: chromium của Playwright đã có trên máy (~/.cache/ms-playwright/chromium_headless_shell-1208) — không tải thêm.
// Ca thật (chỉ khi có "that"): MỘT câu hỏi qua agent thử (tốn tiền mô hình ≈ 0,0003–0,0006 USD). Ca xấu thật: câu bị rào ĐẦU VÀO chặn (0 USD).
// Lượt bị RÀO SỐ chặn: PHẢN HỒI SOẠN TAY theo đúng hình máy chủ trả (page.route) — KHÔNG phải lượt thật, chỉ nằm trong script kiểm này.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const [base, out, real] = process.argv.slice(2);
// Bộ thử bật CÓ mã phiên ⇒ đặt KPI_KIEM_KEY_FILE=<file mã>; script mở trang bằng ?k=<mã> như người dùng (mã không in ra)
const sessionKey = process.env.KPI_KIEM_KEY_FILE ? readFileSync(process.env.KPI_KIEM_KEY_FILE, "utf-8").trim() : "";
const address = (query) => `${base}/${query}${sessionKey ? `&k=${encodeURIComponent(sessionKey)}` : ""}`;
const exe = process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell";
const browser = await chromium.launch({ executablePath: exe });
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "ĐẠT  " : "TRƯỢT"} ${name}${detail ? " — " + detail : ""}`); };
const errors = [];
const watch = (page) => { page.on("pageerror", (e) => errors.push(String(e))); page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); }); };
const overflow = (page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
const flat = (text) => text.replace(/\s+/g, " ").trim();

// ---- A. Đường dẫn cũ vẫn mở đúng chỗ + thanh trên cùng 4 tab, không tab nào bị che (0 USD)
const routes = [
  ["?man=doi", "Cả đội", "Cả đội", ".hero, .team-section, .board"], ["?man=nhan-vien", "Cả đội", "Từng nhân viên", ".emp-view"],
  ["?nv=NV07", "Cả đội", "Từng nhân viên", ".emp-view"], ["?man=chi-tieu&kpi=PC_ON", "Cả đội", "Theo chỉ tiêu", ".kpi-view"],
  ["?man=theo-doi", "Theo dõi", null, ".tracking"], ["?man=chi-tiet", "Theo dõi", null, ".outlets-view"], ["?man=tra-365", "Theo dõi", null, ".only-count"],
  ["?man=danh-gia", "Đánh giá", null, "main"], ["?man=tro-ly", "Trợ lý", null, ".assistant-screen"], ["?man=khong-co", "Cả đội", "Cả đội", ".view-switch.team-views"],
];
for (const [query, tab, view, selector] of routes) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  watch(page);
  await page.goto(address(query));
  await page.waitForSelector(selector, { timeout: 90000 });
  const selectedTab = flat(await page.textContent('.tabs .tab[aria-selected="true"]'));
  const selectedView = view === null ? null : flat(await page.textContent('.team-views button[aria-pressed="true"]'));
  let extra = true, note = "";
  if (query.includes("kpi=PC_ON")) { note = flat(await page.textContent(".kpi-view .tracking-tabs button.on")); extra = note === "PC_ON"; }
  if (query.includes("nv=NV07")) { note = await page.inputValue(".emp-head select").catch(() => ""); extra = note === "NV07"; }
  if (view === null) extra = extra && (await page.locator(".team-views").count()) === 0;
  check(`đường ${query} ⇒ tab "${tab}"${view ? ` · kiểu xem "${view}"` : ""}`, selectedTab === tab && selectedView === view && extra, `${selectedTab}${selectedView ? " · " + selectedView : ""}${note ? " · " + note : ""}`);
  await page.close();
}
for (const width of [1440, 1280, 1024, 800]) {
  const page = await browser.newPage({ viewport: { width, height: 800 } });
  await page.goto(address("?man=doi"));
  await page.waitForSelector(".chip-status");   // nhãn trạng thái tải xong mới đo (nó chiếm chỗ của thanh)
  const m = await page.evaluate(() => {
    const nav = document.querySelector(".tabs").getBoundingClientRect();
    const tabs = [...document.querySelectorAll(".tabs .tab")].map((el) => el.getBoundingClientRect());
    const right = document.querySelector(".top-right").getBoundingClientRect();
    return { n: tabs.length, clipped: tabs.filter((r) => r.right > nav.right + 1 || (r.right > right.left + 1 && r.bottom > right.top + 1 && r.top < right.bottom - 1)).length };
  });
  check(`${width} px: thanh trên cùng 4 tab, không tab nào bị che`, m.n === 4 && m.clipped === 0, JSON.stringify(m));
  await page.close();
}

// ---- B. Màn Trợ lý
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
watch(page);
let dialogs = 0;
page.on("dialog", (d) => { dialogs += 1; d.dismiss(); });
await page.goto(address("?man=tro-ly"));
await page.waitForSelector(".assistant-screen form input");
const turnsSel = ".assistant-chat .msg.assistant.turn, .assistant-chat .msg.error";
const ask = async (question) => {
  const before = await page.locator(turnsSel).count();
  await page.fill(".assistant-chat form input", question);
  await page.keyboard.press("Enter");
  await page.waitForFunction(([sel, n]) => document.querySelectorAll(sel).length > n, [turnsSel, before], { timeout: 120000 });
  return page.locator(turnsSel).last();
};
if (sessionKey) check("có mã phiên: trang xoá ?k= khỏi thanh địa chỉ, giữ ?man=", !page.url().includes("k=") && page.url().includes("man=tro-ly"));
check("màn Trợ lý: hai cột, chưa hỏi thì cột phải có lời hướng dẫn, vùng tin nhắn aria-live", (await page.locator(".assistant-steps .steps").count()) === 0
  && (await page.textContent(".assistant-steps")).includes("Chưa có lượt hỏi nào") && (await page.getAttribute(".assistant-chat .messages", "aria-live")) === "polite");
const steps = page.locator(".assistant-steps");

if (real === "that") {
  const msg = await ask("NV05 được bao nhiêu tiền ở từng chỉ tiêu trong 8 KPI?");
  check("ca thật: có câu trả lời, lượt mới tự được chọn", (await msg.getAttribute("class")).includes("selected"), flat(await msg.textContent()).slice(0, 150));
  check("ca thật: cột phải MỞ SẴN các bước + bảng kết quả, ghi lại câu hỏi", (await steps.locator(".step").count()) >= 4 && (await steps.locator(".result-scroll tbody tr").count()) >= 8
    && (await steps.locator(".steps-question").textContent()).includes("NV05"), `${await steps.locator(".step").count()} thẻ · ${await steps.locator(".result-scroll tbody tr").count()} dòng bảng`);
  const tool = steps.locator(".step.tool .step-head").first();
  check("thẻ công cụ: nhãn tiếng Việt + tham số dạng nhãn; tên kỹ thuật chỉ trong 'Chi tiết kỹ thuật' gập sẵn",
    !flat(await tool.textContent()).includes("get_") && (await steps.locator(".step.tool .chip").count()) >= 1 && (await steps.locator("details.tech[open]").count()) === 0
    && (await steps.locator("details.tech code").first().textContent()).startsWith("get_"), `${flat(await tool.textContent())} · ${(await steps.locator(".step.tool .chip").allTextContents()).map(flat).join(" | ")}`);
  const tierButtons = await msg.locator(".answer .num-link").evaluateAll((els) => els.filter((el) => /(?:đạt(?:\s+mốc)?|mức|mốc)\s*(?:\d+\s*:\s*)?$/i.test(el.previousSibling?.textContent ?? "")).map((el) => el.textContent));
  check("số nằm trong tên mốc (Đạt 100 %, Mức 2: 4,7) KHÔNG là nút tô ô", tierButtons.length === 0, tierButtons.join(" | "));
  const money = msg.locator(".answer .num-link:not(.unsourced)").first();
  await money.focus();
  await page.keyboard.press("Enter");
  check("Tab + Enter vào một con số bên trái ⇒ tô ô nguồn ở cột phải", (await steps.locator(".cell-hit").count()) >= 1 && (await money.getAttribute("aria-pressed")) === "true",
    `số ${flat(await money.textContent())} ⇒ ${await steps.locator(".cell-hit").count()} ô tô`);
  await tool.focus();
  await page.keyboard.press("Enter");
  check("Enter trên đầu thẻ công cụ ⇒ gập / mở (aria-expanded)", (await tool.getAttribute("aria-expanded")) === "false");
  await page.keyboard.press("Enter");
  check("thẻ rào kiểm số + thẻ mô hình và chi phí có chữ rõ", /đạt|CHẶN|không chạy/.test(await steps.locator(".step.guard .step-title").textContent())
    && (await steps.locator(".step.cost").textContent()).includes("USD"), flat(await steps.locator(".step.cost").textContent()).slice(0, 220));
  check("không phần nào tràn khỏi hai cột", await page.evaluate(() => [...document.querySelectorAll(".assistant-chat .msg, .assistant-steps .step-body > *")]
    .every((el) => el.getBoundingClientRect().right <= el.closest(".assistant-chat, .assistant-steps").getBoundingClientRect().right + 1)));
  await page.screenshot({ path: `${out}/tro_ly_ca_that_sang_1440.png` });
  await page.click(".theme-btn");
  await page.screenshot({ path: `${out}/tro_ly_ca_that_toi_1440.png` });
  await page.click(".theme-btn");
}

const refused = await ask("NV01/../NV02 được bao nhiêu tiền?");
check("ca xấu thật (rào đầu vào): cột phải nói câu hỏi bị chặn trước khi gọi mô hình, 0 thẻ công cụ", (await steps.locator(".step.guard").textContent()).includes("bị chặn trước khi gọi mô hình")
  && (await steps.locator(".step.tool").count()) === 0, flat(await refused.locator(".turn-line").textContent()));

const made = {
  conversation_id: "soan-tay", stop: "answered", usd: 0.00031, seconds: 6.2, warnings: ["có số chưa đối chiếu được với kết quả tính: 9.999.999 đ"],
  answer: "NV05 có tiền PC_BT là **2.200.000 đ**, tổng 8 KPI là 9.999.999 đ.\n\n⚠ Lưu ý: câu trả lời có số chưa đối chiếu được với kết quả tính (9.999.999 đ) — đừng dùng các số này.",
  tool_calls: [{ name: "get_employee_result", params_used: { employee_id: "NV05", period: "2026-10" }, refused: false }],
  number_sources: [{ number: "2.200.000 đ", sources: ["get_employee_result"] }, { number: "9.999.999 đ", sources: [] }],
  provenance: { period: "2026-10", snapshot_at: "2026-10-30T00:00:00", cutoff: "2026-10-31", rule_version: "soạn tay", status: "tam_tinh" },
  trace: {
    tool_calls: [{ name: "get_employee_result", label: "Kết quả một nhân viên", params_used: { employee_id: "NV05", period: "2026-10" }, refused: null, summary: ["business_salary = 9240000"],
      view: { facts: [{ key: "employee_id", value: "NV05" }, { key: "business_salary", value: 9240000 }], notes: [], nested_left: [],
        tables: [{ key: "kpis", columns: ["kpi", "target", "actual", "pct", "reached_tier", "amount"], hidden_columns: 0, total_rows: 2,
          rows: [["PC_BT", 290, 301, 103.79, "Đạt 100%", 2200000], ["LPPC", 4.7, 4.52, 96.17, "Mức 4.5", 1800000]] }] } }],
    requested_not_run: [], number_check_first: { ok: false, problems: ["9.999.999 đ"] }, number_check_final: { ok: false, problems: ["9.999.999 đ"] },
    owner_check_first: { ok: true, problems: [] }, owner_check_final: { ok: false, problems: ["số 2.200.000 đ không phải tiền của NV05 trong công cụ"] }, retried_for_numbers: true, input_guard: null,
    model_calls: 3, input_tokens: 4100, output_tokens: 260, usd: 0.00031, seconds: 6.2, stop: "answered", model: "gpt-6-luna", reasoning: "medium",
  },
};
const asked = [];
await page.route("**/api/chat", async (route) => { asked.push(route.request().postDataJSON().question); await route.fulfill({ contentType: "application/json", body: JSON.stringify(made) }); });
const blocked = await ask("NV05 được bao nhiêu tiền PC_BT và tổng 8 KPI?");
check("[soạn tay] lượt bị rào số chặn: câu trả lời VẪN hiện + viền cảnh báo + dòng tóm ghi CHẶN", (await blocked.getAttribute("class")).includes("blocked")
  && (await blocked.locator(".answer").textContent()).includes("2.200.000") && (await blocked.locator(".turn-line").textContent()).includes("CHẶN"), flat(await blocked.locator(".turn-line").textContent()));
check("[soạn tay] thẻ rào ở cột phải nêu số chưa truy được + nút hỏi lại; số không nguồn đánh dấu riêng trong câu trả lời", (await steps.locator(".step.guard").textContent()).includes("9.999.999 đ")
  && (await steps.locator(".step.guard").textContent()).includes("CHƯA truy được") && (await steps.locator(".step.guard button.ghost").count()) === 1 && (await blocked.locator(".answer .num-link.unsourced").count()) >= 1);
check("[soạn tay] cảnh báo KHÔNG lặp: câu trả lời đã có '⚠ Lưu ý' ⇒ không hiện lại dòng cảnh báo cùng nội dung", (await blocked.locator(".warning").count()) === 0 && (await blocked.locator(".answer").textContent()).includes("⚠ Lưu ý"));
check("[soạn tay] số bị rào 'chủ của số' báo (2.200.000 đ) cũng tô đỏ trong câu trả lời", (await blocked.locator(".answer .num-link.unsourced").count()) === 2);
await blocked.locator(".answer .num-link").first().click();
check("[soạn tay] bấm 2.200.000 đ ⇒ tô đúng 1 ô tiền PC_BT ở cột phải", (await steps.locator(".result-scroll td.cell-hit").count()) === 1 && (await steps.locator(".result-scroll td.cell-hit").textContent()).includes("2.200.000"));
await page.screenshot({ path: `${out}/tro_ly_bi_chan_soan_tay_1440.png` });
// N3 (13/11): nhãn khoá business_salary = CÙNG chữ với "Hỏi nhanh" Capitol bản 3 ("Lương kinh doanh (8 KPI)"), không còn chữ cũ "Tiền 8 KPI" — đọc trên MÀN
const stepsText = flat(await steps.textContent());
check("[soạn tay] nhãn tiền 8 KPI ở cột phải = 'Lương kinh doanh (8 KPI)' như Capitol, không còn 'Tiền 8 KPI'", stepsText.includes("Lương kinh doanh (8 KPI)") && !stepsText.includes("Tiền 8 KPI"),
  stepsText.match(/.{0,40}Lương kinh doanh.{0,40}/)?.[0] ?? stepsText.slice(0, 200));
const navCount = await steps.locator(".turn-nav button").count();
await steps.locator(".turn-nav button").first().click();
check("nút chọn lượt: bấm 'Lượt 1' ⇒ cột phải + câu trả lời đang chọn đổi theo", navCount >= 2 && (await page.locator(turnsSel).first().getAttribute("class")).includes("selected")
  && !(await blocked.getAttribute("class")).includes("selected"), `${navCount} nút lượt`);
await blocked.locator(".turn-line.as-button").click();
await steps.locator(".step.guard button.ghost").click();
await page.waitForFunction(() => document.querySelectorAll(".assistant-chat .msg.assistant.blocked").length === 2, null, { timeout: 20000 });
check("[soạn tay] nút 'Hỏi lại câu này' gửi lại ĐÚNG câu hỏi cũ", asked.length === 2 && asked[0] === asked[1], asked.join(" ¦ "));

// ---- C. Khung hỏi nhanh ở màn khác: cùng một hội thoại, chỉ câu trả lời + dòng tóm + nút xem chi tiết
const total = await page.locator(".assistant-chat .msg.assistant.turn").count();
await page.click('.tabs .tab:has-text("Cả đội")');
await page.waitForSelector(".chat .msg.assistant");
check("khung hỏi nhanh thấy CÙNG hội thoại, không có thẻ bước, có nút xem chi tiết", (await page.locator(".chat .msg.assistant:not(.hint)").count()) === total && (await page.locator(".chat .steps").count()) === 0
  && (await page.locator('.chat button:has-text("Xem chi tiết lượt này")').count()) === total, `${total} lượt`);
await page.locator('.chat button:has-text("Xem chi tiết lượt này")').first().click();
check("'Xem chi tiết lượt này' ⇒ sang màn Trợ lý, chọn đúng lượt đó", flat(await page.textContent('.tabs .tab[aria-selected="true"]')) === "Trợ lý"
  && (await page.locator(turnsSel).first().getAttribute("class")).includes("selected"));
check("ô hỏi: chữ lạ không chạy mã", dialogs === 0);
check("1440 không tràn ngang", (await overflow(page)) === 0);

// ---- D. Màn hẹp: các bước xếp ngay dưới câu trả lời đang chọn
await page.setViewportSize({ width: 390, height: 900 });
await page.waitForSelector(".steps-inline .steps");
check("390 px: không còn cột phải, các bước nằm ngay dưới câu trả lời đang chọn, không tràn ngang", (await page.locator(".assistant-steps").count()) === 0
  && (await page.locator(".msg.assistant.turn.selected .steps-inline .step").count()) >= 3 && (await overflow(page)) === 0, String(await overflow(page)));
await page.screenshot({ path: `${out}/tro_ly_390.png`, fullPage: true });
check("0 lỗi JS / console", errors.length === 0, errors.slice(0, 3).join(" ¦ "));
await browser.close();
console.log(`${results.filter(Boolean).length} / ${results.length} đạt`);
process.exit(results.every(Boolean) ? 0 : 1);
