import { useCallback, useEffect, useState } from "react";

// Tải dữ liệu một lần, hoặc hỏi lại sau mỗi `refreshMs` (hỏi định kỳ như dashboard UnderCover).
// Chờ câu trả lời rồi mới hỏi tiếp ⇒ backend chậm không bị dồn yêu cầu. `enabled = false` ⇒ không hỏi
// (vd chưa mở hội thoại). `load` phải giữ nguyên giữa các lần vẽ (useCallback) — đổi `load` = tải lại từ đầu.
export function useLoad<T>(load: () => Promise<T>, refreshMs?: number, enabled = true) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<Error | undefined>(undefined);
  const [version, setVersion] = useState(0);

  // Đổi nguồn (load) hoặc tắt ⇒ xoá dữ liệu cũ. reload() thì GIỮ dữ liệu đang hiện tới khi có bản mới — trước đây reload cũng xoá
  // ⇒ danh sách nháy trống và các mục đang mở (màn Kho) đóng lại sau mỗi lần lưu (C bấm thử 27/10 chiều)
  useEffect(() => {
    setData(undefined);
    setError(undefined);
  }, [load, enabled]);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const askAgain = () => {
      if (refreshMs !== undefined) timer = setTimeout(fetchNow, refreshMs);
    };
    const fetchNow = () => {
      load().then(
        (value) => {
          if (!active) return;
          setData(value);
          setError(undefined);
          askAgain();
        },
        (reason: unknown) => {
          if (!active) return;
          setError(reason instanceof Error ? reason : new Error(String(reason)));
          askAgain();
        },
      );
    };
    fetchNow();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [load, refreshMs, enabled, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error, reload };
}
