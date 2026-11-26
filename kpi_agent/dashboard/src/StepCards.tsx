import { Fragment, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { ChatReply, CheckStep, ToolView, Trace } from "./api";
import { dateTime, exact, monthLabel, vnd } from "./format";
import { NamesContext } from "./Names";

// Câu trả lời của trợ lý + "thẻ bước" ( — User: "khi trợ lý gọi công cụ thì hãy mô tả thêm về trợ lý gọi công cụ nào, tham số gì, kết quả
// trả về của công cụ"). Bốn loại thẻ: Hiểu câu hỏi · Gọi công cụ (tham số + kết quả dạng bảng) · Rào kiểm số · Mô hình và chi phí.
// TRANG KHÔNG TÍNH GÌ: bảng là đúng kết quả công cụ do máy chủ trợ lý sắp sẵn (`agent/result_view.py`); ở đây chỉ đặt nhãn tiếng Việt + định dạng.
// Mọi chữ từ trợ lý / backend vẽ bằng text node — không dựng HTML (giữ giả định của phép quét `test_dashboard_static.py`).
// Thành phần này KHÔNG phụ thuộc bố cục (khung phải hẹp hay màn "Trợ lý" riêng): bề ngang do khung chứa quyết định, bảng tự cuộn trong vùng riêng.

const FIRST_ROWS = 10;   // mỗi bảng hiện 10 dòng đầu; bấm để hiện hết phần máy chủ gửi (tối đa 50 dòng / bảng)

// Nhãn nghiệp vụ. Khoá chưa có trong bảng ⇒ hiện nguyên khoá (không đoán nghĩa).
const PARAM_LABELS: Record<string, string> = {
  employee_id: "Nhân viên", employee_a: "Nhân viên thứ nhất", employee_b: "Nhân viên thứ hai", kpi: "Chỉ tiêu", period: "Kỳ", name: "Tên", sm_code: "Mã SM",
  extra: "Nếu thêm", target_pct: "Nếu đạt (%)", item: "Khoản",
};
const TABLE_LABELS: Record<string, string> = {
  employees: "Theo nhân viên", kpis: "Theo chỉ tiêu", lines: "Từng khoản của từng người", items: "Theo khoản", issues: "Vấn đề dữ liệu",
  tiers: "Bảng mốc thưởng", by_tier: "Theo mốc đã đạt", sides: "Hai người, từng chỉ tiêu", kpi_amount_differences: "Chênh tiền từng chỉ tiêu",
  business_salary_difference: "Chênh tổng tiền", now: "Hiện tại", scenario: "Nếu số đổi như giả định", bonus_run: "Bản tính đang dùng",
  level_counts: "Số vấn đề theo mức",
};
// N3 (13/11, đã chọn bản nhỏ): khoá luôn mang nghĩa "tổng 8 KPI" dùng CÙNG chữ với "Hỏi nhanh" Capitol bản 3 ("Lương kinh doanh (8 KPI)",
// "Tổng lương kinh doanh cả đội"). GIỮ "team_total" = "Tổng tiền cả đội": công cụ thưởng thêm dùng khoá này cho tổng của MỘT khoản (extra_bonus_tool.py)
// ⇒ đổi thành "lương kinh doanh" sẽ sai nghĩa; GIỮ "snapshot_at": đường RAW = ngày đơn mới nhất, đường sổ Tracking = lúc làm mới sổ — khác nghĩa theo nguồn.
const COLUMN_LABELS: Record<string, string> = {
  employee_id: "Nhân viên", name: "Tên", sm_code: "Mã SM", kpi: "Chỉ tiêu", target: "Chỉ tiêu giao", actual: "Thực hiện", pct: "Mức đạt", reached_tier: "Mốc đã đạt", amount: "Tiền",
  reason: "Lý do", business_salary: "Lương kinh doanh (8 KPI)", kpis_paid: "Số chỉ tiêu có tiền", team_total: "Tổng tiền cả đội", employee_count: "Số nhân viên",
  kpi_count: "Số chỉ tiêu", over_target_bonus: "Thưởng vượt chỉ tiêu", over_target_reason: "Lý do thưởng vượt", next_tier: "Mốc kế",
  gap_to_next: "Còn thiếu tới mốc kế", amount_if_reached: "Tiền nếu lên mốc kế", item: "Khoản", title: "Tên khoản", reached: "Đạt",
  how: "Cách tính", extra_bonus_total: "Thưởng thêm", business_salary_8_kpi: "Lương kinh doanh (8 KPI)", total_8_kpi_and_extra: "8 KPI + thưởng thêm",
  paid_count: "Số người có tiền", unpaid_count: "Số người chưa có tiền", paid_employee_ids: "Người có tiền", unpaid_employee_ids: "Người chưa có tiền",
  employee_ids: "Nhân viên", team_amount_total: "Tổng tiền chỉ tiêu này", amount_total: "Tổng tiền", label: "Mốc", min: "Ngưỡng",
  level: "Mức (mã)", level_label: "Mức", message: "Nội dung", scope: "Phạm vi", a_minus_b: "Thứ nhất trừ thứ hai", higher: "Người cao hơn", gap: "Chênh",
  cong_van_section: "Mục công văn", how_counted: "Cách đếm", measure: "Cách đo", amount_change: "Tiền thay đổi", extra: "Nếu thêm", extra_unit: "Đơn vị",
  working_days_total: "Ngày công cả tháng", working_days_left: "Ngày công còn lại", working_days_done: "Ngày công đã qua",
  working_days_left_after_cutoff: "Ngày công còn lại sau ngày chốt", opportunity_count: "Số cơ hội", issue_count: "Số vấn đề", note: "Ghi chú",
  extra_bonus_team_total: "Thưởng thêm cả đội", team_total_8_kpi: "Tổng lương kinh doanh cả đội", team_total_8_kpi_and_extra: "8 KPI + thưởng thêm cả đội",
  business_salary_at_100_pct: "Lương kinh doanh (8 KPI) nếu đạt đủ 100 %", snapshot_at: "Số liệu tới", workflow_version: "Bản quy trình", current_run_id: "Bản tính số",
  run_id: "Bản tính số", created_at: "Tạo lúc", mode: "Kiểu giả định", shown: "Đang hiện", team_sentences: "Câu cả đội viết sẵn",
};
const PREFIX_LABELS: Record<string, string> = { kpi_amounts: "Tiền", kpi_pct: "Mức đạt", kpi_reached_tier: "Mốc", now: "Hiện tại", scenario: "Giả định", data_snapshot: "Dữ liệu" };
const MONEY_KEY = /(amount|salary|bonus|a_minus_b)|^(gap|team_total.*|total_8_kpi_and_extra|kpi_amounts)$/;
const EMPLOYEE_CODE = /^NV\d{2}$/;

function columnLabel(column: string): string {
  const [head, ...rest] = column.split(".");
  if (!rest.length) return COLUMN_LABELS[column] ?? column;
  return `${PREFIX_LABELS[head] ?? head} · ${COLUMN_LABELS[rest.join(".")] ?? rest.join(".")}`;
}
const isMoney = (column: string) => column.split(".").some((part) => MONEY_KEY.test(part));
const isPercent = (column: string) => column.split(".").some((part) => part === "pct" || part === "kpi_pct" || part === "target_pct");

// Nhân viên trong bảng / tham số: "Tên · NVxx" khi trang đang bật tên (cùng nguồn tên `/api/names` với các màn khác), không thì NVxx
// Khi trang đang hiện tên (ô nhân viên đã là "Tên · NVxx"), ô / cột / nhãn `name` và `sm_code` mà công cụ trả kèm là LẶP ⇒ ẩn
function useShowsNames() {
  const { mode, names } = useContext(NamesContext);
  return mode !== "ma" && names !== null;
}
const isNameKey = (key: string) => key === "name" || key === "sm_code" || key.endsWith(".name") || key.endsWith(".sm_code");

function useEmployeeText() {
  const { mode, names } = useContext(NamesContext);
  return (code: string) => {
    const entry = names?.[code];
    if (!entry || mode === "ma") return code;
    return mode === "ten" ? `${entry.name} · ${code}` : `${entry.name} · ${entry.sm_code} · ${code}`;
  };
}

type Cell = string | number | boolean | null;
function cellText(value: Cell, column: string, employee: (code: string) => string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "có" : "không";
  if (typeof value === "number") return isMoney(column) ? vnd(value) : isPercent(column) ? `${exact(value)} %` : exact(value);
  if (column === "period" && /^\d{4}-\d{2}$/.test(value)) return monthLabel(value);
  if (/_at$/.test(column)) return dateTime(value);
  return value.split(", ").map((part) => (EMPLOYEE_CODE.test(part) ? employee(part) : part)).join(", ");
}

// Chữ một con số trong câu trả lời ("1.000.000 đ", "89,74 %", "5,6 triệu") ⇒ các giá trị có thể — để tô ô CÙNG GIÁ TRỊ trong bảng kết quả.
const UNITS: [RegExp, number][] = [[/tỷ|tỉ/i, 1e9], [/triệu|\btr\b/i, 1e6], [/nghìn|ngàn|\bk\b/i, 1e3]];
function numberValues(text: string): number[] {
  const found = text.match(/-?\d[\d.]*(?:,\d+)?/);
  if (!found) return [];
  const value = Number(found[0].replace(/\./g, "").replace(",", "."));
  if (!Number.isFinite(value)) return [];
  const unit = UNITS.find(([pattern]) => pattern.test(text.slice(found.index! + found[0].length)));
  return unit ? [value * unit[1]] : [value];
}
// Phần chữ số của một con số ("2.200.000 đ" ⇒ "2.200.000") — để nhận ra cùng một con số trong lời của rào và trong câu trả lời
const numberCore = (text: string) => text.match(/-?\d[\d.]*(?:,\d+)?/)?.[0] ?? null;
// Hai cảnh báo máy chủ trợ lý vừa gửi riêng (`warnings`) vừa gắn vào cuối câu trả lời ("⚠ Lưu ý: …") khi rào kiểm số chặn (agent/server.py `_response`)
const REPEATED_WARNINGS = ["có số chưa đối chiếu được với kết quả tính", "có chỗ chưa khớp dữ liệu"];
const sameValue = (cell: Cell, wanted: number[]) =>
  typeof cell === "number" && wanted.some((value) => Math.abs(cell - value) <= 1e-9 * Math.max(1, Math.abs(value)));

// Chữ đứng NGAY TRƯỚC một con số cho biết số đó là tên mốc: "Đạt ", "đạt mốc ", "Mức ", "Mức 2: ", "mốc "
const TIER_NAME_BEFORE = /(?:đạt(?:\s+mốc)?|mức|mốc)\s*(?:\d+\s*:\s*)?$/i;
export type Focus = { text: string; sources: string[] } | null;

// Câu trả lời: chữ thường + **đậm** + mỗi con số máy chủ đã dò nguồn thành một nút (bấm / Tab + Enter ⇒ tô ô nguồn). Chỉ text node.
function AnswerText({ text, numbers, focus, onPick, linkable, flagged }: {
  text: string; numbers: { number: string; sources: string[] }[]; focus: Focus; onPick: (focus: Focus) => void; linkable: (item: { number: string; sources: string[] }) => boolean;
  flagged: (item: { number: string; sources: string[] }) => boolean;
}) {
  const parts = text.split(/\*\*([^*\n]+?)\*\*/g);
  let next = 0;   // số kế tiếp cần tìm (theo đúng thứ tự máy chủ trả)
  const withNumbers = (part: string, keyBase: string): ReactNode[] => {
    const out: ReactNode[] = [];
    let at = 0;
    while (next < numbers.length) {
      const item = numbers[next];
      const found = part.indexOf(item.number, at);
      if (found < 0) break;
      if (found > at) out.push(<Fragment key={`${keyBase}t${at}`}>{part.slice(at, found)}</Fragment>);
      const on = focus?.text === item.number;
      // Số có nguồn nhưng KHÔNG có ô nào trong bảng mang đúng giá trị (vd "2026" của kỳ, số thứ tự) ⇒ để chữ thường, không làm nút bấm vào mà không tô được gì.
      // Số nằm trong TÊN MỐC của công văn ("Đạt 100 %", "đạt mốc 95 %", "Mức 2: 4,7") cũng để chữ thường: tô theo giá trị sẽ sáng nhầm ô
      // "mức đạt 100 %" của một chỉ tiêu khác — tên mốc là chữ, không phải số đo. Chỉ áp cho số ĐÃ có nguồn; số chưa truy được vẫn là nút đỏ.
      const bad = flagged(item);   // chưa truy được nguồn, hoặc có nguồn nhưng rào "chủ của số" báo không phải số của người được nêu
      const inTierName = !bad && TIER_NAME_BEFORE.test(part.slice(0, found));
      out.push((!linkable(item) && !bad) || inTierName ? <Fragment key={`${keyBase}x${next}`}>{item.number}</Fragment> :
        <button key={`${keyBase}n${next}`} type="button" className={`num-link${bad ? " unsourced" : ""}`} aria-pressed={on}
          title={item.sources.length === 0 ? "Số này chưa truy được về kết quả công cụ" : bad ? "Rào kiểm số báo: số này không khớp dữ liệu của người được nêu" : "Bấm để xem số này lấy từ đâu"}
          onClick={() => onPick(on ? null : { text: item.number, sources: item.sources })}>{item.number}</button>,
      );
      at = found + item.number.length;
      next += 1;
    }
    if (at < part.length) out.push(<Fragment key={`${keyBase}t${at}`}>{part.slice(at)}</Fragment>);
    return out;
  };
  return <>{parts.map((part, index) => (index % 2 ? <strong key={index}>{withNumbers(part, `b${index}`)}</strong> : <Fragment key={index}>{withNumbers(part, `p${index}`)}</Fragment>))}</>;
}

function Chips({ params }: { params: Record<string, unknown> | null }) {
  const employee = useEmployeeText();
  const showsNames = useShowsNames();
  const entries = Object.entries(params ?? {}).filter(([key, value]) => value !== null && value !== undefined && value !== "" && !(showsNames && isNameKey(key)));
  if (!entries.length) return <span className="hint">không có tham số</span>;
  return (
    <span className="chips">
      {entries.map(([key, value]) => (
        <span className="chip" key={key}><span className="k">{PARAM_LABELS[key] ?? key}:</span> {cellText(typeof value === "object" ? JSON.stringify(value) : (value as Cell), key, employee)}</span>
      ))}
    </span>
  );
}

function ResultTable({ table, wanted }: { table: ToolView["tables"][number]; wanted: number[] }) {
  const employee = useEmployeeText();
  const [all, setAll] = useState(false);
  const showsNames = useShowsNames();
  // cột tên / mã SM lặp với ô nhân viên ⇒ không vẽ (chỉ bỏ cách hiện; dữ liệu máy chủ gửi không đổi)
  const shown = table.columns.map((column, at) => at).filter((at) => !(showsNames && isNameKey(table.columns[at]) && table.columns.some((other) => /employee_id$|^employee_[ab]$|^higher$/.test(other))));
  const hasHit = wanted.length > 0 && table.rows.some((row) => row.some((cell) => sameValue(cell, wanted)));
  const rows = all || hasHit ? table.rows : table.rows.slice(0, FIRST_ROWS);   // có ô đang được tô ⇒ hiện hết để ô đó không bị giấu
  return (
    <div className="result-table">
      <div className="result-title">{TABLE_LABELS[table.key] ?? table.key} <span className="hint">· {rows.length} / {table.total_rows} dòng{table.hidden_columns ? ` · còn ${table.hidden_columns} cột chưa hiện` : ""}</span></div>
      <div className="result-scroll" tabIndex={0} role="region" aria-label={`Bảng ${TABLE_LABELS[table.key] ?? table.key}`}>
        <table>
          <thead><tr>{shown.map((at) => <th key={table.columns[at]} scope="col">{columnLabel(table.columns[at])}</th>)}</tr></thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {shown.map((at) => <td key={at} className={`${typeof row[at] === "number" ? "n" : ""}${sameValue(row[at], wanted) ? " cell-hit" : ""}`}>{cellText(row[at], table.columns[at], employee)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!all && !hasHit && table.rows.length > FIRST_ROWS && <button type="button" className="ghost small" onClick={() => setAll(true)}>Hiện {table.rows.length} dòng</button>}
      {table.total_rows > table.rows.length && <p className="hint">Bảng có {table.total_rows} dòng; trang chỉ nhận {table.rows.length} dòng đầu.</p>}
    </div>
  );
}

// Phần bị trần của máy chủ cắt bớt: nói rõ "hiện N / tổng M" (máy chủ bản cũ không gửi tổng ⇒ không ghi gì)
function cutNote(view: ToolView): string {
  const parts = [
    (view.total_facts ?? 0) > view.facts.length ? `${view.facts.length} / ${view.total_facts} ô đơn` : "",
    (view.total_tables ?? 0) > view.tables.length ? `${view.tables.length} / ${view.total_tables} bảng` : "",
    (view.total_notes ?? 0) > view.notes.length ? `${view.notes.length} / ${view.total_notes} ghi chú` : "",
  ].filter(Boolean);
  return parts.length ? `Kết quả dài: chỉ hiện ${parts.join(", ")}.` : "";
}

function ToolCard({ call, index, open, onToggle, wanted }: { call: Trace["tool_calls"][number]; index: number; open: boolean; onToggle: () => void; wanted: number[] }) {
  const employee = useEmployeeText();
  const showsNames = useShowsNames();
  const view = call.view;
  const facts = view ? view.facts.filter((fact) => !(showsNames && isNameKey(fact.key) && view.facts.some((other) => other.key === "employee_id"))) : [];
  const rowCount = view ? view.tables.reduce((sum, table) => sum + table.total_rows, 0) : 0;
  return (
    <li className={`step tool${call.refused ? " refused" : ""}`}>
      <button type="button" className="step-head" aria-expanded={open} onClick={onToggle}>
        <span className="step-no">{index + 1}</span>
        <span className="step-title">Gọi công cụ: <b>{call.label}</b></span>
        <span className="step-sum">{call.refused ? "bị từ chối" : `${rowCount} dòng`}</span>
      </button>
      {open && (
        <div className="step-body">
          <div className="step-row"><span className="step-key">Tham số</span><Chips params={call.params_used} /></div>
          {call.refused && <p className="warning">Công cụ từ chối: {call.refused}</p>}
          {facts.length > 0 && (
            <dl className="facts">
              {facts.map((fact) => (
                <div key={fact.key} className={sameValue(fact.value, wanted) ? "cell-hit" : ""}><dt>{COLUMN_LABELS[fact.key] ?? fact.key}</dt><dd>{cellText(fact.value, fact.key, employee)}</dd></div>
              ))}
            </dl>
          )}
          {view?.tables.map((table) => <ResultTable key={table.key} table={table} wanted={wanted} />)}
          {view && view.notes.length > 0 && <ul className="step-notes">{view.notes.map((note, at) => <li key={at}>{note}</li>)}</ul>}
          {view && cutNote(view) && <p className="hint">{cutNote(view)}</p>}
          {view && view.nested_left.length > 0 && <p className="hint">Còn dữ liệu lồng chưa trải thành bảng — xem chi tiết kỹ thuật.</p>}
          <details className="tech">
            <summary>Chi tiết kỹ thuật</summary>
            <p>Tên công cụ: <code>{call.name}</code></p>
            <p>Tham số gốc: <code>{JSON.stringify(call.params_used)}</code></p>
            {call.summary.length > 0 && <p>Ô số chính: <code>{call.summary.join(" · ")}</code></p>}
            {view && view.nested_left.length > 0 && <p>Khoá còn dữ liệu lồng: <code>{view.nested_left.join(", ")}</code></p>}
          </details>
        </div>
      )}
    </li>
  );
}

const checkOk = (step: CheckStep | null | undefined) => !step || step.ok;
const problemText = (step: CheckStep | null | undefined) => (step && !step.ok ? step.problems.map((item) => (typeof item === "string" ? item : JSON.stringify(item))) : []);

// Trạng thái rào kiểm số của một lượt: "dat" · "dat-sau-lam-lai" · "chan" (còn số chưa truy được sau khi đã cho làm lại) · "khong-chay" (không có câu trả lời của mô hình)
function guardState(trace: Trace) {
  if (trace.input_guard || trace.stop !== "answered") return "khong-chay" as const;
  if (!checkOk(trace.number_check_final) || !checkOk(trace.owner_check_final)) return "chan" as const;
  return trace.retried_for_numbers ? ("dat-sau-lam-lai" as const) : ("dat" as const);
}
const GUARD_TEXT = { dat: "đạt", "dat-sau-lam-lai": "đạt sau một lần làm lại", chan: "CHẶN", "khong-chay": "không chạy" } as const;
const GUARD_ICON = { dat: "✓", "dat-sau-lam-lai": "✓", chan: "✕", "khong-chay": "–" } as const;

function GuardCard({ reply, trace, toolLabels, onRetry, focus, onPick }: {
  reply: ChatReply; trace: Trace; toolLabels: Record<string, string>; onRetry?: () => void; focus: Focus; onPick: (focus: Focus) => void;
}) {
  const state = guardState(trace);
  const problems = [...problemText(trace.number_check_final), ...problemText(trace.owner_check_final)];
  const first = [...problemText(trace.number_check_first), ...problemText(trace.owner_check_first)];
  // Bảng "số ⇒ nguồn" chỉ liệt kê số CHƯA truy được và số có ô cùng giá trị trong kết quả công cụ. Mảnh ngày / kỳ / số thứ tự ("2026", "10", "30") có
  // nguồn nhưng không phải số đo ⇒ không liệt kê — chỉ đổi cách HIỆN, rào kiểm số vẫn xét mọi số như cũ.
  const cells = trace.tool_calls.flatMap((call) => (call.view ? [...call.view.facts.map((fact) => fact.value), ...call.view.tables.flatMap((table) => table.rows.flat())] : []));
  const listed = reply.number_sources.filter((source) => source.sources.length === 0 || cells.some((cell) => sameValue(cell, numberValues(source.number))));
  const skipped = reply.number_sources.length - listed.length;
  return (
    <li className={`step guard ${state}`}>
      <div className="step-head static">
        <span className="step-no" aria-hidden="true">{GUARD_ICON[state]}</span>
        <span className="step-title">Rào kiểm số: <b>{GUARD_TEXT[state]}</b></span>
      </div>
      <div className="step-body">
        {state === "dat" && <p>Mọi số trong câu trả lời đều truy được về kết quả công cụ.</p>}
        {state === "dat-sau-lam-lai" && <p>Bản nháp đầu có chỗ chưa truy được ({first.join("; ") || "không ghi chi tiết"}) nên trợ lý phải viết lại một lần. Bản đang hiện: mọi số đều truy được về kết quả công cụ.</p>}
        {state === "chan" && (
          <>
            <p className="warning">Còn chỗ CHƯA truy được về kết quả công cụ sau khi đã cho viết lại một lần — đừng dùng các số này:</p>
            <ul className="step-notes">{problems.map((item, at) => <li key={at}>{item}</li>)}</ul>
            {onRetry && <button type="button" className="ghost" onClick={onRetry}>Hỏi lại câu này</button>}
          </>
        )}
        {state === "khong-chay" && <p>{trace.input_guard ? `Câu hỏi bị chặn trước khi gọi mô hình: ${trace.input_guard}` : `Lượt này không có câu trả lời của mô hình để kiểm (${trace.stop}).`}</p>}
        {listed.length > 0 && (
          <table className="sources">
            <thead><tr><th scope="col">Số trong câu trả lời</th><th scope="col">Có trong kết quả của</th></tr></thead>
            <tbody>
              {listed.map((source, at) => (
                <tr key={at} className={source.sources.length ? "" : "unsourced"}>
                  <td><button type="button" className="num-link" aria-pressed={focus?.text === source.number} onClick={() => onPick(focus?.text === source.number ? null : { text: source.number, sources: source.sources })}>{source.number}</button></td>
                  <td>{source.sources.length ? source.sources.map((name) => toolLabels[name] ?? name).join(", ") : "chưa truy được"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {skipped > 0 && <p className="hint">Không liệt kê {skipped} số khác trong câu trả lời (ngày, kỳ, số thứ tự…): có nguồn nhưng không nằm ở ô nào của bảng kết quả.</p>}
        <p className="hint">Rào chỉ kiểm con số CÓ trong kết quả công cụ và thuộc đúng người; ô được tô là ô có cùng giá trị — không tự chứng minh câu chữ xung quanh đúng.</p>
      </div>
    </li>
  );
}

// Trạng thái + chữ tóm một câu của một lượt: "Đã tra … · N dòng · rào kiểm số: đạt"
export function turnSummary(reply: ChatReply) {
  const trace = reply.trace;
  const calls = trace?.tool_calls ?? [];
  const state = trace ? guardState(trace) : null;
  const rowCount = calls.reduce((sum, call) => sum + (call.view ? call.view.tables.reduce((inner, table) => inner + table.total_rows, 0) : 0), 0);
  const labels = [...new Set(calls.map((call) => call.label))];
  const text = `${calls.length ? `Đã tra ${labels.join(", ")} · ${rowCount} dòng` : "Không gọi công cụ nào"} · rào kiểm số: ${state ? GUARD_TEXT[state] : "—"}`;
  return { state, text, icon: state ? GUARD_ICON[state] : "" };
}

// Câu trả lời của một lượt: chữ + con số bấm được + cảnh báo. Không có thẻ bước — nơi dùng tự đặt thẻ bước (cột phải / ngay dưới) hoặc nút "Xem chi tiết".
export function Answer({ reply, focus, onPick }: { reply: ChatReply; focus: Focus; onPick: (focus: Focus) => void }) {
  const calls = reply.trace?.tool_calls ?? [];
  // Một con số thành nút khi: chưa truy được nguồn (để người đọc thấy ngay), hoặc có ít nhất một ô / ô đơn trong kết quả công cụ mang đúng giá trị đó
  const allCells = calls.flatMap((call) => (call.view ? [...call.view.facts.map((fact) => fact.value), ...call.view.tables.flatMap((table) => table.rows.flat())] : []));
  const linkable = (item: { number: string; sources: string[] }) => item.sources.length === 0 || allCells.some((cell) => sameValue(cell, numberValues(item.number)));
  // Rào "chủ của số" ghi lời dạng "số 1.000.000 đ không phải tiền của NV05 …" ⇒ con số đó trong câu trả lời cũng tô đỏ như số chưa truy được nguồn
  const ownerProblems = reply.trace?.owner_check_final && !reply.trace.owner_check_final.ok ? reply.trace.owner_check_final.problems.filter((item) => typeof item === "string") : [];
  const flagged = (item: { number: string; sources: string[] }) => {
    if (item.sources.length === 0) return true;
    const core = numberCore(item.number);
    return core !== null && ownerProblems.some((problem) => problem.startsWith("số ") && numberCore(problem.slice(3)) === core);
  };
  // Khi rào chặn, máy chủ trợ lý ĐÃ gắn "⚠ Lưu ý: …" vào cuối câu trả lời ⇒ không hiện lại hai cảnh báo cùng nội dung ở dưới ( 12/11: lặp hai
  // lần). Cảnh báo loại khác (không trả lời trọn, bỏ link, không lấy được nguồn số) vẫn hiện.
  const inAnswer = reply.answer.includes("⚠ Lưu ý:");
  const warnings = reply.warnings.filter((warning) => !(inAnswer && typeof warning === "string" && REPEATED_WARNINGS.some((start) => warning.startsWith(start))));
  return (
    <>
      <div className="answer"><AnswerText text={reply.answer} numbers={reply.number_sources} focus={focus} onPick={onPick} linkable={linkable} flagged={flagged} /></div>
      {warnings.map((warning, index) => <div key={index} className="warning">⚠ {typeof warning === "string" ? warning : JSON.stringify(warning)}</div>)}
    </>
  );
}

// Các thẻ bước của MỘT lượt, mở sẵn: Hiểu câu hỏi · từng lần gọi công cụ · Rào kiểm số · Mô hình và chi phí · nguồn số.
// `focus` = con số đang chọn trong câu trả lời ⇒ tô các ô cùng giá trị, mở thẻ công cụ là nguồn của số đó và cuộn tới ô đầu tiên.
export function Steps({ reply, focus, onPick, onRetry }: { reply: ChatReply; focus: Focus; onPick: (focus: Focus) => void; onRetry?: () => void }) {
  const trace = reply.trace;
  const [closed, setClosed] = useState<Set<number>>(new Set());
  const box = useRef<HTMLOListElement>(null);
  const calls = trace?.tool_calls ?? [];
  const toolLabels = Object.fromEntries(calls.map((call) => [call.name, call.label]));
  const wanted = focus ? numberValues(focus.text) : [];
  useEffect(() => {
    if (!focus) return;
    setClosed((old) => new Set([...old].filter((index) => !focus.sources.includes(calls[index]?.name))));
  }, [focus]);   // eslint-disable-line react-hooks/exhaustive-deps — chỉ chạy khi đổi con số đang chọn
  useEffect(() => {
    if (focus) box.current?.querySelector(".cell-hit")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [focus, closed]);
  if (!trace) return <p className="hint">Lượt này không có dấu vết các bước (máy chủ trợ lý bản cũ).</p>;
  const toggle = (index: number) => setClosed((old) => {
    const next = new Set(old);
    if (!next.delete(index)) next.add(index);
    return next;
  });
  const understood = Object.assign({}, ...calls.map((call) => call.params_used ?? {})) as Record<string, unknown>;
  const provenance = reply.provenance;
  return (
    <>
      <ol className="steps" ref={box}>
        <li className="step understood">
          <div className="step-head static"><span className="step-no" aria-hidden="true">?</span><span className="step-title">Hiểu câu hỏi</span></div>
          <div className="step-body">
            {trace.input_guard ? <p className="warning">Câu hỏi bị chặn trước khi gọi mô hình: {trace.input_guard}</p>
              : calls.length ? <div className="step-row"><span className="step-key">Trợ lý hiểu là</span><Chips params={understood} /></div>
                : <p>Trợ lý trả lời mà không tra công cụ nào.</p>}
            {trace.requested_not_run.length > 0 && <p className="warning">Trợ lý xin tra thêm nhưng đã quá số bước cho phép: {trace.requested_not_run.map((call) => toolLabels[call.name] ?? call.name).join(", ")}</p>}
          </div>
        </li>
        {calls.map((call, index) => <ToolCard key={index} call={call} index={index} open={!closed.has(index)} onToggle={() => toggle(index)} wanted={wanted} />)}
        <GuardCard reply={reply} trace={trace} toolLabels={toolLabels} onRetry={onRetry} focus={focus} onPick={onPick} />
        <li className="step cost">
          <div className="step-head static"><span className="step-no" aria-hidden="true">≡</span><span className="step-title">Mô hình và chi phí</span></div>
          <div className="step-body">
            <dl className="facts">
              <div><dt>Mô hình</dt><dd>{trace.model ?? "—"}</dd></div>
              <div><dt>Mức suy nghĩ</dt><dd>{trace.reasoning ?? "—"}</dd></div>
              <div><dt>Số lần gọi mô hình</dt><dd>{exact(trace.model_calls)}</dd></div>
              <div><dt>Số lần gọi công cụ</dt><dd>{exact(calls.length)}</dd></div>
              <div><dt>Chữ vào ⇒ ra (token)</dt><dd>{exact(trace.input_tokens)} ⇒ {exact(trace.output_tokens)}</dd></div>
              <div><dt>Thời gian</dt><dd>{trace.seconds === null ? "—" : `${exact(trace.seconds)} giây`}</dd></div>
              <div><dt>Tiền gọi mô hình</dt><dd>{trace.usd === null ? "—" : `${trace.usd.toFixed(5)} USD`}</dd></div>
            </dl>
          </div>
        </li>
      </ol>
      {provenance && (
        <div className="meta-line">
          Nguồn: {monthLabel(provenance.period)}, số liệu tới {dateTime(provenance.snapshot_at)}, bản luật {provenance.rule_version}, {provenance.status_label_for_model ?? provenance.status_label ?? (provenance.status === "da_soat" ? "đã soát" : "tạm tính")}.
        </div>
      )}
      {focus && <div className="meta-line" role="status">Đang tô các ô có giá trị {focus.text}{focus.sources.length ? ` (${focus.sources.map((name) => (name === "câu hỏi" ? "có trong câu hỏi" : toolLabels[name] ?? name)).join(", ")})` : " — chưa truy được về kết quả công cụ nào"}.</div>}
    </>
  );
}
