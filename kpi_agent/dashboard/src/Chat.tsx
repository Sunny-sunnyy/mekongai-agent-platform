import { type FormEvent, useState } from "react";
import { Answer, type Focus, turnSummary } from "./StepCards";
import { type ChatState, SUGGESTIONS } from "./useChat";

// Khung HỎI NHANH ở cột phải các màn (SPEC mục 6 — phương án 1): chỉ câu hỏi, câu trả lời, một dòng tóm và nút
// "Xem chi tiết lượt này" ⇒ mở màn "Trợ lý" đúng lượt đó (các bước: công cụ nào, tham số gì, kết quả, rào kiểm số — StepCards.tsx).
// Hội thoại dùng chung với màn "Trợ lý" (useChat.ts). Mọi chữ từ trợ lý / backend vẽ bằng text node — KHÔNG dựng HTML / ảnh / link
// (T2a 03/11: lời từ chối chép nguyên chuỗi người gõ ⇒ dựng HTML là mở đường chèn script).
export function ChatForm({ chat }: { chat: ChatState }) {
  const [draft, setDraft] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    chat.ask(draft.trim());
    setDraft("");
  };
  return (
    <form onSubmit={submit}>
      <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Hỏi về KPI / thưởng của đội…" aria-label="Câu hỏi" maxLength={500} />
      <button type="submit" disabled={chat.busy || !draft.trim()}>Gửi</button>
    </form>
  );
}

export function Suggestions({ chat }: { chat: ChatState }) {
  return (
    <div className="suggestions">
      <p className="hint">Bấm một câu để hỏi thử (mỗi câu gọi mô hình một lần):</p>
      {SUGGESTIONS.map((question) => <button key={question} type="button" className="suggestion" disabled={chat.busy} onClick={() => chat.ask(question)}>{question}</button>)}
    </div>
  );
}

export function Chat({ chat, onOpenTurn }: { chat: ChatState; onOpenTurn: (index: number, focus?: Focus) => void }) {
  return (
    <section className="chat" aria-labelledby="chat-title">
      <div className="chat-head"><h2 id="chat-title">Hỏi nhanh trợ lý</h2><span>Trả lời bằng số của công cụ</span></div>
      <p className="hint chat-note">Số lấy từ backend; trợ lý không tự tính. Xem từng bước ở màn Trợ lý.</p>
      <div className="messages" aria-live="polite">
        {chat.messages.length === 0 && <Suggestions chat={chat} />}
        {chat.messages.map((message, index) => {
          if (message.role === "user") return <div key={index} className="msg user">{message.text}</div>;
          if (message.role === "error") return <div key={index} className="msg error">{message.text}</div>;
          const summary = turnSummary(message.reply);
          return (
            <div key={index} className={`msg assistant${summary.state === "chan" ? " blocked" : ""}`}>
              {/* bấm một con số ⇒ sang màn Trợ lý, đúng lượt này, tô sẵn ô nguồn của số đó */}
              <Answer reply={message.reply} focus={null} onPick={(focus) => onOpenTurn(index, focus)} />
              <div className={`turn-line ${summary.state ?? ""}`}><span aria-hidden="true">{summary.icon}</span> <span>{summary.text}</span></div>
              <button type="button" className="ghost small" onClick={() => onOpenTurn(index)}>Xem chi tiết lượt này</button>
            </div>
          );
        })}
        {chat.busy && <div className="msg assistant hint">Đang trả lời…</div>}
      </div>
      <ChatForm chat={chat} />
    </section>
  );
}
