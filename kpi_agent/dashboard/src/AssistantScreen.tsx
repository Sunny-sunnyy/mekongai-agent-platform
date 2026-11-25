import { useEffect, useState } from "react";
import { ChatForm, Suggestions } from "./Chat";
import { Answer, type Focus, Steps, turnSummary } from "./StepCards";
import { type ChatState, questionBefore } from "./useChat";

// Màn "Trợ lý" (phương án 1: "có tab riêng để hỏi đáp"): TRÁI = hội thoại, PHẢI = các bước của lượt đang chọn, mở sẵn.
// Bấm một câu trả lời bên trái (hoặc nút "Lượt 1 · Lượt 2 …") ⇒ cột phải đổi theo. Màn hẹp (≤ 900 px): không có cột phải, các bước xếp ngay
// dưới câu trả lời đang chọn. Lượt đang chọn + con số đang tô do App giữ (khung hỏi nhanh ở màn khác mở thẳng tới đúng lượt).
function useNarrow() {
  const query = "(max-width: 900px)";
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const onChange = () => setNarrow(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return narrow;
}

export function AssistantScreen({ chat, selected, onSelect, focus, onFocus }: {
  chat: ChatState; selected: number | null; onSelect: (index: number) => void; focus: Focus; onFocus: (focus: Focus) => void;
}) {
  const narrow = useNarrow();
  const turns = chat.messages.flatMap((message, index) => (message.role === "assistant" ? [index] : []));
  // chưa chọn (hoặc lượt đã chọn không còn) ⇒ lượt mới nhất
  const current = selected !== null && turns.includes(selected) ? selected : turns.length ? turns[turns.length - 1] : null;
  const message = current !== null ? chat.messages[current] : null;
  const reply = message && message.role === "assistant" ? message.reply : null;
  const question = current !== null ? questionBefore(chat.messages, current) : "";
  const steps = reply && current !== null ? (
    <Steps key={current} reply={reply} focus={focus} onPick={onFocus} onRetry={chat.busy ? undefined : () => chat.ask(question)} />
  ) : null;
  const pick = (index: number, next: Focus) => {
    if (index !== current) onSelect(index);
    onFocus(next);
  };
  return (
    <section className="assistant-screen" aria-label="Trợ lý">
      <div className="assistant-chat">
        <div className="assistant-head">
          <h2>Hỏi trợ lý</h2>
          <p className="hint">Trợ lý đọc số từ công cụ tính, không tự tính. Bấm một câu trả lời để xem các bước của lượt đó; bấm một con số để xem nó lấy từ ô nào.</p>
        </div>
        <div className="messages" aria-live="polite">
          {chat.messages.length === 0 && <Suggestions chat={chat} />}
          {chat.messages.map((item, index) =>
            item.role === "user" ? (
              <div key={index} className="msg user">{item.text}</div>
            ) : item.role === "error" ? (
              <div key={index} className="msg error">{item.text}</div>
            ) : (
              <div key={index} className={`msg assistant turn${index === current ? " selected" : ""}${turnSummary(item.reply).state === "chan" ? " blocked" : ""}`}>
                <Answer reply={item.reply} focus={index === current ? focus : null} onPick={(next) => pick(index, next)} />
                <button type="button" className={`turn-line as-button ${turnSummary(item.reply).state ?? ""}`} aria-pressed={index === current}
                  onClick={() => { onSelect(index); onFocus(null); }}>
                  <span aria-hidden="true">{turnSummary(item.reply).icon}</span> <span>Lượt {turns.indexOf(index) + 1} · {turnSummary(item.reply).text}</span>
                  <span className="more">{index === current ? "đang xem các bước" : "xem các bước"}</span>
                </button>
                {narrow && index === current && <div className="steps-inline">{steps}</div>}
              </div>
            ),
          )}
          {chat.busy && <div className="msg assistant hint">Đang trả lời…</div>}
        </div>
        <ChatForm chat={chat} />
      </div>
      {!narrow && (
        <div className="assistant-steps" aria-labelledby="steps-title">
          <div className="assistant-head">
            <div>
              <h2 id="steps-title">Các bước của lượt đang chọn</h2>
              {question && <p className="hint steps-question">“{question}”</p>}
            </div>
            {turns.length > 1 && (
              <nav className="view-switch turn-nav" aria-label="Chọn lượt">
                {turns.map((index, at) => (
                  <button key={index} type="button" className={index === current ? "on" : ""} aria-pressed={index === current} onClick={() => { onSelect(index); onFocus(null); }}>Lượt {at + 1}</button>
                ))}
              </nav>
            )}
          </div>
          {steps ?? <p className="hint">Chưa có lượt hỏi nào. Hỏi một câu ở bên trái — các bước (công cụ nào, tham số gì, kết quả, rào kiểm số, chi phí) sẽ hiện ở đây.</p>}
        </div>
      )}
    </section>
  );
}
