import { useCallback, useEffect, useState } from "react";
import { NO_KEY_MESSAGE, UNAUTHORIZED_EVENT } from "./session";
import { getMeta, getNames, getRuns, getTeamBoard, getTeamBonus, getTeamGap, getXemStatus, isRefused } from "./api";
import { AssistantScreen } from "./AssistantScreen";
import { Chat } from "./Chat";
import { dateOnly, dateTime } from "./format";
import { EmployeeView } from "./EmployeeView";
import { Evaluations } from "./Evaluations";
import { TargetsView } from "./TargetsView";
import { ExportButtons } from "./ExportButtons";
import { DEFAULT_NAME_MODE, NameModeSwitch, NamesContext, type NameMode } from "./Names";
import { RunPanel } from "./RunPanel";
import { TeamOverview } from "./TeamOverview";
import { TeamTable } from "./TeamTable";
import { Tracking } from "./Tracking";
import type { Focus } from "./StepCards";
import { useChat } from "./useChat";
import { useLoad } from "./useLoad";

// Trang giám sát bán hàng, bản 3 "Đường đua thưởng" (kiểu A + nút bảng gọn — kế hoạch 09:20 12/11).
// Thanh trên: các màn + nhãn tạm tính / đã soát + nút sáng / tối. Màn "Cả đội": khối tổng tiền, dải 8 KPI, bảng đường đua / bảng gọn.
// Không đăng nhập, không đổi kỳ — một đội một kỳ. TRANG KHÔNG TỰ TÍNH TIỀN: mọi số do backend trả.
type Screen = "doi" | "nhan-vien" | "chi-tieu" | "theo-doi" | "danh-gia" | "tro-ly";
// Thanh trên cùng 4 tab (thêm màn "Trợ lý" thì 6 tab không vừa thanh): "Từng nhân viên" + "Theo chỉ tiêu" thành 3 kiểu xem TRONG tab
// "Cả đội" (TEAM_VIEWS). Mã màn cũ giữ nguyên ⇒ ?man=nhan-vien / chi-tieu / ?nv= / ?kpi= (menu chéo bên Capitol) vẫn mở đúng chỗ.
const TABS: [Screen, string][] = [["doi", "Cả đội"], ["theo-doi", "Theo dõi"], ["danh-gia", "Đánh giá"], ["tro-ly", "Trợ lý"]];
const TEAM_VIEWS: [Screen, string][] = [["doi", "Cả đội"], ["nhan-vien", "Từng nhân viên"], ["chi-tieu", "Theo chỉ tiêu"]];
const SCREENS = [...TEAM_VIEWS, ...TABS.slice(1)];
const isTeamView = (screen: Screen) => TEAM_VIEWS.some(([key]) => key === screen);
// Menu trỏ chéo trong "một hệ thống": địa chỉ Trang chủ + Xưởng workflow (trang có người duyệt) là biến lúc dựng trang; chưa đặt ⇒ ẩn mục đó
const TRACKING_ALIASES: Record<string, string> = { "chi-tiet": "chi-tiet", "tra-365": "tra-365" };
// Chế độ "một nguồn số": trang đọc số qua hộp KPI trong Capitol ⇒ CHỈ XEM. Soát + tải file làm ở xưởng (WORKSHOP_URL).
const READ_ONLY = import.meta.env.VITE_KPI_READ_ONLY === "1";
const STATUS_REFRESH_MS = 30_000;
const HOME_URL: string | undefined = import.meta.env.VITE_KPI_HOME_URL;
const WORKSHOP_URL: string | undefined = import.meta.env.VITE_KPI_WORKSHOP_URL;

export function App() {
  const meta = useLoad(getMeta);
  const board = useLoad(getTeamBoard);
  const bonus = useLoad(getTeamBonus);
  // có /api/team-board (thanh mốc, tổng tối đa…) thì dùng; chưa có ⇒ bảng tiền /api/team-bonus (trang ẩn phần thiếu, không tự tính)
  const team = board.data ? board : bonus;
  const gap = useLoad(getTeamGap);
  // bậc 2a: bản tính đang dùng + "Đã soát"; backend bậc 1 trả 404 ⇒ null ⇒ không hiện khung
  const runs = useLoad(getRuns);
  const afterReview = () => { meta.reload(); runs.reload(); };
  // ?nv=NV07 trên đường dẫn ⇒ mở sẵn thẻ NV đó (gửi link / chụp ảnh); mã do backend kiểm như mọi tham số khác
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(window.location.search).get("nv"));
  const [showFlags, setShowFlags] = useState(false);
  // Mở thẳng một màn bằng đường dẫn (menu chéo từ trang khác): ?man=doi|nhan-vien|chi-tieu|chi-tiet|tra-365|theo-doi|danh-gia|tro-ly (giá trị lạ ⇒ Cả đội);
  // không có ?man= mà có ?nv= ⇒ màn Từng nhân viên như cũ. Các tham số này còn nguyên sau khi session.ts xoá ?k= (chỉ bỏ đúng `k`).
  const [screen, setScreen] = useState<Screen>(() => {
    const query = new URLSearchParams(window.location.search);
    const asked = query.get("man");
    if (asked !== null && asked in TRACKING_ALIASES) return "theo-doi";
    if (asked !== null) return SCREENS.some(([key]) => key === asked) ? (asked as Screen) : "doi";
    return query.get("nv") ? "nhan-vien" : "doi";
  });
  // ?man=chi-tiet / ?man=tra-365 ⇒ màn Theo dõi mở thẳng mục đó ( — hai mục điểm bán nằm TRONG Theo dõi như trang của đội; 7 tab trên cùng thì
  // thanh tab che mất 2 tab cuối ở màn 1440 px)
  const [trackingTab] = useState<string | undefined>(() => TRACKING_ALIASES[new URLSearchParams(window.location.search).get("man") ?? ""]);
  const openEmployee = (id: string | null) => { if (id) { setSelected(id); setScreen("nhan-vien"); } };
  const [sessionUsd, setSessionUsd] = useState(0);
  // Một hội thoại cho cả khung hỏi nhanh và màn Trợ lý; lượt đang xem các bước + con số đang tô giữ ở đây để khung hỏi nhanh mở thẳng tới đúng lượt
  const addSpend = useCallback((usd: number) => setSessionUsd((old) => old + usd), []);
  const chat = useChat(addSpend);
  const [turn, setTurn] = useState<number | null>(null);
  const [focus, setFocus] = useState<Focus>(null);
  const openTurn = (index: number, next: Focus = null) => { setTurn(index); setFocus(next); setScreen("tro-ly"); };
  // có câu trả lời mới ⇒ màn Trợ lý xem lượt mới nhất
  useEffect(() => { setTurn(null); setFocus(null); }, [chat.messages.length]);
  // màn "Theo chỉ tiêu": KPI đang xem (bấm một ô ở dải 8 KPI của màn Cả đội ⇒ mở màn này đúng KPI đó)
  // ?kpi=<tên KPI> ⇒ màn Theo chỉ tiêu mở đúng KPI đó; tên lạ ⇒ KPI đầu (kiểm với danh sách backend trả ở chỗ vẽ, không gắn chữ lạ vào trang)
  const [kpiFocus, setKpiFocus] = useState<string | null>(() => new URLSearchParams(window.location.search).get("kpi"));
  const openKpi = (kpi: string) => { setKpiFocus(kpi); setScreen("chi-tieu"); };
  // Kiểu hiện nhân viên trên trang: mặc định TÊN; không lưu lại giữa các lần mở trang. Lỗi tải tên ⇒ coi như không có tên (chỉ NVxx)
  const names = useLoad(getNames);
  const [nameMode, setNameMode] = useState<NameMode>(DEFAULT_NAME_MODE);
  // Sáng / tối: mặc định theo máy; bấm ◑ ⇒ ép kiểu ngược lại cho tới khi tải lại trang (không lưu)
  const toggleTheme = () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
  };

  // Mã phiên: máy chủ trả 401 ở bất kỳ lời gọi nào ⇒ dừng vẽ trang, hiện một màn hướng dẫn (không trắng trang, không gọi lặp)
  const [unauthorized, setUnauthorized] = useState(false);
  useEffect(() => {
    const onUnauthorized = () => setUnauthorized(true);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);
  if (unauthorized) return <main><h1>Trợ lý KPI thưởng</h1><section className="notice refused" role="alert">{NO_KEY_MESSAGE}</section></main>;

  // Bản xem: hỏi lại 30 giây một lần xem hộp xem còn khớp hộp chính không. Không khớp / không hỏi được ⇒ MỘT khung lý do, không vẽ số nào (kể cả số cũ)
  const xem = useLoad(getXemStatus, STATUS_REFRESH_MS, READ_ONLY);
  const sourceClosed = READ_ONLY && (xem.error !== undefined || (xem.data !== undefined && !xem.data.khop));
  const loadError = meta.error ?? team.error;
  const boardData = board.data && !isRefused(board.data) ? board.data : undefined;
  return (
    <NamesContext.Provider value={{ mode: nameMode, names: names.data ?? null }}>
      <header className="top">
        <div className="top-in">
          <h1 className="brand"><span className="brand-mark" aria-hidden="true"><i /></span><span className="t">Trợ lý KPI thưởng</span></h1>
          <nav className="tabs" role="tablist" aria-label="Màn">
            {TABS.map(([key, label]) => (
              <button key={key} type="button" role="tab" className="tab" aria-selected={key === "doi" ? isTeamView(screen) : screen === key} onClick={() => setScreen(key)}>{label}</button>
            ))}
          </nav>
          <div className="top-right">
            {HOME_URL && <a className="official-link" href={HOME_URL}>Trang chủ</a>}
            {WORKSHOP_URL && <a className="official-link" href={WORKSHOP_URL}>Xưởng workflow (có người duyệt)</a>}
            {/* Xem qua Capitol (READ_ONLY): `status` của hộp đọc "Đã soát" của trang trên máy (store.run_summary ⇒ approval_event); hộp bật KPI_SYSTEM_ONLY
                nên không ai bấm được ⇒ luôn "tam_tinh", kể cả sau khi khối Người duyệt Capitol duyệt (ghi bảng khác, capitol_approval). Vì vậy ở chế độ
                này KHÔNG bao giờ hiện "Đã soát" — ghi rõ "chưa có trường duyệt" (13/11). */}
            {meta.data && (
              <button type="button" className={`chip-status ${READ_ONLY ? "tam_tinh" : meta.data.status ?? "tam_tinh"}`} aria-expanded={showFlags} onClick={() => setShowFlags((v) => !v)}>
                <span aria-hidden="true">{!READ_ONLY && meta.data.status === "da_soat" ? "✓" : "◐"}</span> <b>{READ_ONLY ? "Tạm tính — xem qua Capitol (chưa có trường duyệt)" : meta.data.status === "da_soat" ? "Đã soát" : "Tạm tính"}</b>
                {meta.data.data_source === "raw"
                  ? `đơn mới nhất ${dateOnly(meta.data.snapshot_at)} · chốt ${dateOnly(meta.data.cutoff)}`
                  : `số chụp ${dateTime(meta.data.snapshot_at)}`}
              </button>
            )}
            <button type="button" className="theme-btn" aria-label="Đổi sáng / tối" title="Đổi sáng / tối" onClick={toggleTheme}>◑</button>
          </div>
        </div>
      </header>
      {sourceClosed ? (
        <main>
          <section className="notice refused source-closed" role="status">
            <b>Chưa hiện số.</b> {xem.data?.loi ?? "Chưa hỏi được trạng thái nguồn số — xem bản chính thức ở Xưởng workflow"}.
            {WORKSHOP_URL && <> <a href={WORKSHOP_URL}>Mở Xưởng workflow</a></>}
          </section>
        </main>
      ) : (
      <main>
        {showFlags && meta.data && (
          <div className="flags">
            <p>{meta.data.status_label ?? meta.data.label}</p>
            <ul>{meta.data.data_flags.map((flag, i) => <li key={i}>{flag}</li>)}</ul>
          </div>
        )}
        {loadError && <section className="notice error">Không tải được dữ liệu từ backend :8110 ({loadError.message}). Bật backend rồi tải lại trang.</section>}
        {board.error && (
          <section className="notice refused" role="alert">
            Không tải được bảng đội đầy đủ ({board.error.message.replace(/^https?:\/\/\S+: /, "")}) — đang hiện bảng tiền dự phòng, thiếu khối tổng, thanh mốc và các màn Từng nhân viên / Theo chỉ tiêu.
          </section>
        )}
        {team.data && isRefused(team.data) && <section className="notice refused">Backend từ chối: {team.data.refused}</section>}
        {names.data && <NameModeSwitch mode={nameMode} onChange={setNameMode} />}
        {isTeamView(screen) && (
          <nav className="view-switch team-views" aria-label="Kiểu xem của màn Cả đội">
            <span className="hint">Xem theo:</span>
            {TEAM_VIEWS.map(([key, label]) => <button key={key} type="button" className={screen === key ? "on" : ""} aria-pressed={screen === key} onClick={() => setScreen(key)}>{label}</button>)}
          </nav>
        )}
        {screen === "tro-ly" && <AssistantScreen chat={chat} selected={turn} onSelect={setTurn} focus={focus} onFocus={setFocus} />}

        {screen === "doi" && boardData && meta.data && (
          <TeamOverview team={boardData} bonus={bonus.data && !isRefused(bonus.data) ? bonus.data : undefined} meta={meta.data} gap={gap.data} selected={null} onSelect={openEmployee} onOpenKpi={openKpi} />
        )}
        {screen === "chi-tieu" && boardData && meta.data && (
          <TargetsView team={boardData} kpis={meta.data.kpis} gap={gap.data} focus={kpiFocus} onPick={setKpiFocus} />
        )}
        {screen !== "tro-ly" && <div className="layout">
          {screen === "doi" && (
            <div>
              {/* backend bản cũ không có /api/team-board ⇒ chỉ có bảng tiền gọn (không tự dựng khối đầu trang) */}
              {!boardData && team.data && !isRefused(team.data) && meta.data && <TeamTable team={team.data} kpis={meta.data.kpis} selected={selected} onSelect={setSelected} />}
              {READ_ONLY ? (
                <p className="panel read-only-note">Bản xem: duyệt ("Đã soát") và tải bảng thưởng làm ở trang chính thức{WORKSHOP_URL ? <> — <a href={WORKSHOP_URL}>mở Xưởng workflow</a></> : ""}.</p>
              ) : (
              <details className="panel">
                <summary>Bản tính đang dùng và tải bảng thưởng</summary>
                {runs.data && <RunPanel runs={runs.data} onReviewed={afterReview} dataSource={meta.data?.data_source} />}
                {meta.data && <ExportButtons runId={meta.data.current_run_id} exportState={meta.data.export} />}
                {runs.error && <section className="notice error">Không tải được danh sách bản tính ({runs.error.message}).</section>}
              </details>
              )}
            </div>
          )}
          {screen === "nhan-vien" && boardData && (
            <EmployeeView team={boardData} bonus={bonus.data && !isRefused(bonus.data) ? bonus.data : undefined}
              employeeId={selected ?? boardData.employees[0].employee_id}
              onPick={setSelected} onBack={() => setScreen("doi")} />
          )}
          {screen === "theo-doi" && <Tracking sessionUsd={sessionUsd} initialTab={trackingTab} />}
          {screen === "danh-gia" && <Evaluations />}
          <Chat chat={chat} onOpenTurn={openTurn} />
        </div>}
      </main>
      )}
    </NamesContext.Provider>
  );
}
