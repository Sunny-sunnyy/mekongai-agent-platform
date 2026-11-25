import { useCallback, useState } from "react";
import { getRunVerify, reviewRun, type RunList, type RunSummary, type RunVerify } from "./api";
import { dateTime, vnd, dateOnly } from "./format";

// Bậc 2a (mục 6, mục 2): khung "bản tính đang dùng" — số này theo bản luật nào, dữ liệu chụp lúc nào,
// ai đã soát. Hành động ghi DUY NHẤT của trang là "Đã soát"; trang không sửa số, không khoá, không xoá. Nhãn trạng thái là chữ backend viết.
export function RunPanel({ runs, onReviewed, dataSource }: { runs: RunList; onReviewed: () => void; dataSource?: "raw" | "tracking" }) {
  const current = runs.runs.find((run) => run.run_id === runs.current_run_id);
  if (!current) return <section className="notice refused">Backend báo bản tính đang dùng là {runs.current_run_id} nhưng không có trong danh sách bản tính.</section>;
  const older = runs.runs.filter((run) => run.run_id !== current.run_id);
  return (
    <section className={`run-panel ${current.status}`} aria-label="Bản tính đang dùng">
      <div className="run-head">
        <h2>Bản tính đang dùng</h2>
        <p className="run-status">{current.status_label}</p>
      </div>
      <RunFacts run={current} dataSource={dataSource} />
      <div className="run-actions">
        <VerifyButton runId={current.run_id} />
        {current.status === "tam_tinh" && !current.replaced_by && <ReviewForm runId={current.run_id} onReviewed={onReviewed} />}
      </div>
      {older.length > 0 && (
        <details className="run-history">
          <summary>Các bản tính trước ({older.length})</summary>
          <ul>
            {older.map((run) => (
              <li key={run.run_id}>
                <b>{run.run_id}</b>, tính lúc {dateTime(run.created_at)}, tổng {vnd(run.team_total)}. {run.status_label}
                {run.replaced_by && <> · bị thay bởi bản {run.replaced_by}</>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function RunFacts({ run, dataSource }: { run: RunSummary; dataSource?: "raw" | "tracking" }) {
  return (
    <dl className="facts run-facts">
      <div><dt>Mã bản</dt><dd>{run.run_id}</dd></div>
      <div><dt>Bản luật</dt><dd>{run.rule_version}</dd></div>
      {/* đường file đơn hàng: "đơn mới nhất" (chỉ ngày); đường sổ Tracking: "số chụp" (ngày giờ làm mới sổ) — cùng cách gọi với nhãn đầu trang */}
      {dataSource === "raw"
        ? <div><dt>Đơn mới nhất</dt><dd>{dateOnly(run.data_snapshot.refreshed_at)}</dd></div>
        : <div><dt>Số chụp</dt><dd>{dateTime(run.data_snapshot.refreshed_at)}</dd></div>}
      <div><dt>Ngày chốt số</dt><dd>{dateTime(run.data_snapshot.cutoff)}</dd></div>
      <div><dt>Tính lúc</dt><dd>{dateTime(run.created_at)}</dd></div>
      <div><dt>Tổng cả đội</dt><dd>{vnd(run.team_total)}</dd></div>
      <div><dt>Mã code</dt><dd title={run.code_sha}>{run.code_sha.slice(0, 8)}</dd></div>
      {run.replaces && <div><dt>Thay cho bản</dt><dd>{run.replaces}</dd></div>}
    </dl>
  );
}

// "Kiểm lại số": backend tính lại từ bản luật + dữ liệu ĐÃ LƯU (kiểm sha trước) — người dùng tự thấy số trên trang tra lại được
function VerifyButton({ runId }: { runId: number }) {
  const [state, setState] = useState<{ busy: boolean; result?: RunVerify; error?: string }>({ busy: false });
  const verify = useCallback(() => {
    setState({ busy: true });
    getRunVerify(runId).then(
      (result) => setState({ busy: false, result }),
      (reason: unknown) => setState({ busy: false, error: reason instanceof Error ? reason.message : String(reason) }),
    );
  }, [runId]);
  const r = state.result;
  return (
    <div className="verify">
      <button type="button" onClick={verify} disabled={state.busy}>{state.busy ? "Đang tính lại…" : "Kiểm lại số"}</button>
      {r?.refused && <p className="verify-bad">Không tính lại được: {r.refused}</p>}
      {r && !r.refused && (
        <p className={r.cells_equal === r.cells_total && r.team_total_recomputed === r.team_total_stored ? "verify-ok" : "verify-bad"}>
          Tính lại từ bản luật và dữ liệu đã lưu: {r.cells_equal} / {r.cells_total} ô khớp; tổng lưu {vnd(r.team_total_stored)}, tính lại {vnd(r.team_total_recomputed)}.
          {Object.entries(r.inputs_match).filter(([, ok]) => !ok).map(([name]) => ` ${name} đã đổi so với lúc tính.`).join("")}
        </p>
      )}
      {state.error && <p className="verify-bad">Không kiểm lại được ({state.error}).</p>}
    </div>
  );
}

// Bấm hai bước (nhập tên ⇒ "Đã soát" ⇒ "Xác nhận") để giảm bấm nhầm — tiêu chí theo dõi của bậc 2a. Tên là tự khai (chưa đăng nhập, mục 5).
function ReviewForm({ runId, onReviewed }: { runId: number; onReviewed: () => void }) {
  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "refused" | "error"; text: string } | null>(null);
  const name = reviewer.trim();

  const submit = () => {
    setBusy(true);
    setMessage(null);
    reviewRun(runId, name, note.trim()).then(
      (result) => {
        setBusy(false);
        setConfirming(false);
        if ("refused" in result) setMessage({ kind: "refused", text: result.refused });   // chữ backend — hiện dạng chữ thường, không HTML
        else onReviewed();
      },
      (reason: unknown) => {
        setBusy(false);
        setConfirming(false);
        setMessage({ kind: "error", text: reason instanceof Error ? reason.message : String(reason) });
      },
    );
  };

  return (
    <form className="review" onSubmit={(event) => { event.preventDefault(); if (name) setConfirming(true); }}>
      <label>
        Người soát
        <input value={reviewer} maxLength={60} onChange={(event) => { setReviewer(event.target.value); setConfirming(false); }} placeholder="Họ tên giám sát" />
      </label>
      <label>
        Ghi chú (tuỳ chọn)
        <textarea value={note} maxLength={500} rows={2} onChange={(event) => setNote(event.target.value)} />
      </label>
      {!confirming
        ? <button type="submit" disabled={!name || busy}>Đã soát</button>
        : (
          <div className="confirm">
            <span>Xác nhận <b>{name}</b> đã soát bản {runId}? Sau khi bấm không sửa được; sai thì tính bản mới.</span>
            <button type="button" onClick={submit} disabled={busy}>{busy ? "Đang ghi…" : "Xác nhận"}</button>
            <button type="button" className="secondary" onClick={() => setConfirming(false)} disabled={busy}>Huỷ</button>
          </div>
        )}
      {message && <p className={message.kind === "refused" ? "review-refused" : "verify-bad"}>{message.kind === "refused" ? "Backend từ chối: " : "Lỗi: "}{message.text}</p>}
    </form>
  );
}
