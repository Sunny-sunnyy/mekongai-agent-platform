import { Fragment } from "react";

// Chỉ một tập con markdown AN TOÀN: "**đậm**" ⇒ <strong>, còn lại là chữ thường (C rà bảo mật R6 03/11: mô hình có thể viết ảnh / link markdown
// chứa số tiền trong URL ⇒ KHÔNG dựng ảnh / link / HTML). Tách chuỗi rồi dựng phần tử React — không dangerouslySetInnerHTML.
// Xuống dòng giữ nhờ CSS white-space: pre-wrap của khung chứa. "**" lẻ (không đóng) giữ nguyên chữ.
export function BoldText({ text }: { text: string }) {
  const parts = text.split(/\*\*([^*\n]+?)\*\*/g);   // phần lẻ (1, 3, …) = chữ nằm giữa hai cặp **
  return <>{parts.map((part, i) => (i % 2 ? <strong key={i}>{part}</strong> : <Fragment key={i}>{part}</Fragment>))}</>;
}
