import { useCallback, useRef, useState } from "react";
import { type ChatReply, sendQuestion } from "./api";

// MỘT hội thoại dùng chung cho khung hỏi nhanh (cột phải các màn) và màn "Trợ lý" (phương án 1): hỏi ở đâu cũng thấy ở kia.
// Trình duyệt chỉ giữ chữ để vẽ; lịch sử gửi mô hình vẫn do máy chủ trợ lý giữ theo mã hội thoại (SPEC mục 3).
export type Message = { role: "user"; text: string } | { role: "assistant"; reply: ChatReply } | { role: "error"; text: string };
export interface ChatState { messages: Message[]; busy: boolean; ask: (question: string) => void }

export function useChat(onSpend?: (usd: number) => void): ChatState {
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | null>(null);
  const busyNow = useRef(false);   // chặn gửi hai câu chồng nhau từ hai khung trong cùng một nhịp vẽ
  const ask = useCallback((question: string) => {
    if (!question || busyNow.current) return;
    busyNow.current = true;
    setBusy(true);
    setMessages((old) => [...old, { role: "user", text: question }]);
    sendQuestion(conversationId.current, question).then(
      (reply) => {
        conversationId.current = reply.conversation_id;
        setMessages((old) => [...old, { role: "assistant", reply }]);
        if (onSpend && typeof reply.usd === "number") onSpend(reply.usd);
      },
      // agent tắt / lỗi ⇒ báo lỗi thật, không câu trả lời giả
      (error: Error) => setMessages((old) => [...old, { role: "error", text: `Không gọi được trợ lý (agent :8120): ${error.message}` }]),
    ).finally(() => { busyNow.current = false; setBusy(false); });
  }, [onSpend]);
  return { messages, busy, ask };
}

// Câu người dùng đứng ngay trước một câu trả lời — nút "Hỏi lại câu này" gửi lại đúng câu đó; màn Trợ lý ghi câu hỏi của lượt đang chọn
export function questionBefore(messages: Message[], index: number): string {
  for (let at = index - 1; at >= 0; at -= 1) {
    const message = messages[at];
    if (message.role === "user") return message.text;
  }
  return "";
}

// 3 câu gợi ý (thiết kế mục 3) — đúng ba việc giám sát hay hỏi: còn thiếu, vì sao 0 đ, ai 0 đ
export const SUGGESTIONS = ["NV07 còn thiếu bao nhiêu PC_BT?", "Vì sao NV01 không có tiền Turnover?", "Ai đang 0 đ ASO_ALL?"];
