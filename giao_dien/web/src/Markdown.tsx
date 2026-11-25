// [ghi chú kỹ thuật] Hiển thị chữ kiểu Markdown của trợ lý: đậm, nghiêng, tiêu đề nhỏ, danh sách,
// bảng, mã, liên kết workflow.
// AN TOÀN (chữ do AI viết ⇒ coi là dữ liệu chưa tin cậy):
//  - KHÔNG dùng innerHTML / dangerouslySetInnerHTML ở bất cứ đâu: mọi chữ đi vào trang dưới dạng nút chữ của React ⇒ thẻ HTML trong câu trả lời
//    (<script>, <img onerror=…>) hiện nguyên là chữ, không chạy.
//  - Liên kết: CHỈ nhận liên kết tới workflow của trang ([tên](/workflow/<mã UUID>), kể cả khi trợ lý tự ghi địa chỉ đầy đủ đằng trước) ⇒ thành NÚT mở
//    trong xưởng. Mọi liên kết khác (http ngoài, javascript:, data:) ⇒ chỉ hiện phần chữ, không tạo thẻ <a>, không có địa chỉ để bấm.
//  - Không tải ảnh: cú pháp ![chữ](địa chỉ) ⇒ chỉ hiện "chữ".
import type { ReactNode } from "react";

const WF = /^(?:https?:\/\/[^\s)\/]{1,80})?\/workflow\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/?$/;

// trong MỘT dòng: `mã`, **đậm**, *nghiêng* / _nghiêng_, [chữ](địa chỉ), ![chữ](địa chỉ)
function trongDong(s: string, onMo: (id: string) => void, k = "d"): ReactNode[] {
  const ra: ReactNode[] = [];
  const mau = /(`[^`\n]+`)|(!?\[[^\]\n]{1,300}\]\([^)\s]{1,400}\))|(\*\*[^*\n]+?\*\*)|(\*[^*\s][^*\n]*?\*)|(\b_[^_\s][^_\n]*?_\b)/g;
  let cuoi = 0, i = 0;
  for (let m = mau.exec(s); m; m = mau.exec(s)) {
    if (m.index > cuoi) ra.push(s.slice(cuoi, m.index));
    const t = m[0], khoa = `${k}-${i++}`;
    if (m[1]) ra.push(<code key={khoa}>{t.slice(1, -1)}</code>);
    else if (m[2]) {
      const lk = t.match(/^(!?)\[([^\]]+)\]\(([^)]+)\)$/)!;
      const wf = lk[1] ? null : lk[3].match(WF);
      // nhãn của liên kết cũng có thể mang **đậm** / `mã` (đo lượt thật 11:27: "[**KPI mẫu 1 — …**](/workflow/…)") ⇒ dựng nhãn như chữ trong dòng
      const nhan = trongDong(lk[2], onMo, khoa);
      ra.push(wf ? <button key={khoa} className="lk" onClick={() => onMo(wf[1])} title="Mở workflow này trong xưởng">{nhan}</button> : <span key={khoa}>{nhan}</span>);
    } else if (m[3]) ra.push(<strong key={khoa}>{trongDong(t.slice(2, -2), onMo, khoa)}</strong>);
    else ra.push(<em key={khoa}>{trongDong(t.slice(1, -1), onMo, khoa)}</em>);
    cuoi = m.index + t.length;
  }
  if (cuoi < s.length) ra.push(s.slice(cuoi));
  return ra;
}

// [ghi chú kỹ thuật] dòng quá dài (chuỗi khoảng trắng / ký tự lặp do AI hoặc dữ liệu bẩn) làm vài biểu thức chậm theo bình phương ⇒ dòng dài hơn
// DAI ký tự hiện NGUYÊN là chữ, không đem khớp bất kỳ biểu thức nào.
const DAI = 2000;
const dai = (x: string | undefined) => (x ?? "").length > DAI;
const oBang = (dong: string) => dong.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((x) => x.trim());
const laKeBang = (dong: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(dong);

export function Markdown({ chu, onMo }: { chu: string; onMo: (id: string) => void }) {
  const dong = chu.replace(/\r\n?/g, "\n").split("\n");
  const ra: ReactNode[] = [];
  let i = 0, k = 0;
  const td = (s: string, khoa: string) => trongDong(s, onMo, khoa);
  while (i < dong.length) {
    const d = dong[i];
    if (dai(d)) { ra.push(<p key={k++} className="md-tho">{d}</p>); i++; continue; }
    if (!d.trim()) { i++; continue; }
    if (/^\s*```/.test(d)) {                                         // khối mã
      const than: string[] = [];
      i++;
      while (i < dong.length && (dai(dong[i]) || !/^\s*```/.test(dong[i]))) than.push(dong[i++]);
      i++;
      ra.push(<pre key={k++}><code>{than.join("\n")}</code></pre>);
      continue;
    }
    const tieuDe = d.match(/^\s{0,3}(#{1,6})\s+(.*)$/);
    if (tieuDe) { ra.push(<h4 key={k++} className={"md-h md-h" + Math.min(tieuDe[1].length, 3)}>{td(tieuDe[2].replace(/\s+#+\s*$/, ""), "h" + k)}</h4>); i++; continue; }
    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(d)) { ra.push(<hr key={k++} />); i++; continue; }
    if (d.includes("|") && i + 1 < dong.length && !dai(dong[i + 1]) && laKeBang(dong[i + 1])) {   // bảng: dòng tiêu đề + dòng kẻ
      const dau = oBang(d);
      const than: string[][] = [];
      i += 2;
      while (i < dong.length && !dai(dong[i]) && dong[i].includes("|") && dong[i].trim()) than.push(oBang(dong[i++]));
      ra.push(
        <div key={k++} className="md-bang"><table className="bang">
          <thead><tr>{dau.map((o, j) => <th key={j}>{td(o, `th${k}-${j}`)}</th>)}</tr></thead>
          <tbody>{than.map((h, r) => <tr key={r}>{dau.map((_, j) => {
            const o = h[j] ?? "";
            return <td key={j} className={/^[\d.,\s%đ₫+-]+$/.test(o) && /\d/.test(o) ? "so" : undefined}>{td(o, `td${k}-${r}-${j}`)}</td>;
          })}</tr>)}</tbody>
        </table></div>,
      );
      continue;
    }
    const ds = d.match(/^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/);
    if (ds) {                                                        // danh sách (một cấp; dòng thụt vào nối tiếp mục trước)
      const coSo = /\d/.test(ds[2]);
      const muc: string[] = [];
      while (i < dong.length && !dai(dong[i])) {
        const m = dong[i].match(/^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/);
        if (m && /\d/.test(m[2]) === coSo) { muc.push(m[3]); i++; }
        else if (dong[i].trim() && /^\s{2,}/.test(dong[i]) && muc.length) { muc[muc.length - 1] += " " + dong[i].trim(); i++; }
        else break;
      }
      const li = muc.map((x, j) => <li key={j}>{td(x, `li${k}-${j}`)}</li>);
      ra.push(coSo ? <ol key={k++}>{li}</ol> : <ul key={k++}>{li}</ul>);
      continue;
    }
    const doan: string[] = [];                                       // đoạn thường: gom tới dòng trống / khối khác
    while (i < dong.length && !dai(dong[i]) && dong[i].trim() && !/^\s*```/.test(dong[i]) && !/^\s{0,3}#{1,6}\s/.test(dong[i]) && !/^\s*([-*+]|\d{1,3}[.)])\s+/.test(dong[i])
      && !(dong[i].includes("|") && i + 1 < dong.length && !dai(dong[i + 1]) && laKeBang(dong[i + 1]))) doan.push(dong[i++]);
    ra.push(<p key={k++}>{doan.flatMap((x, j) => (j ? [<br key={`br${j}`} />, ...td(x, `p${k}-${j}`)] : td(x, `p${k}-${j}`)))}</p>);
  }
  return <div className="md">{ra}</div>;
}
