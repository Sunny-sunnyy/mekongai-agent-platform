"""Tên nhân viên ở CỬA VÀO / CỬA RA cho mô hình (bậc "tên vào trợ lý", — 16:50 bỏ hẳn luật che tên; kế hoạch

Lõi tính (`backend/queries.py`, `core/` — trong dấu vân tay bản tính) GIỮ mã che NV01…; file này chỉ:
1. `resolve_employee`: chữ mô hình gửi ở tham số nhân viên (mã che, HỌ TÊN ĐỦ, 2 CHỮ CUỐI của tên — cách trang sếp gọi —, hoặc mã SM thật) ⇒ mã che;
   so không phân biệt dấu / hoa / khoảng trắng; chỉ nhận khi khớp ĐÚNG MỘT người; tên một chữ không nhận (dễ trùng) ⇒ từ chối, hỏi lại.
2. `with_names`: kết quả công cụ + `name` / `sm_code` cạnh mỗi `employee_id` mã che (chuỗi — không thêm số nào vào nguồn số của rào ngoài mã SM,
   mà mã SM cũng là của chính người đó).
3. `names_to_codes`: thay tên / mã SM trong câu trả lời bằng mã che của người đó TRƯỚC mọi luật rào ⇒ "<họ tên> được 1.500.000 đ" xét như
   "NV05 được 1.500.000 đ" (đảo luật áp nguyên). Bảng tên là bảng ĐỦ ĐỘI do bên gọi đưa vào (backend `app.state.name_entries`, chat `ctx.name_entries`).
Cờ `KPI_NAMES=on|off` (mặc định off — hành vi cũ y nguyên) đọc một chỗ: `names_enabled()`."""
import os
import re
import unicodedata

MASKED = re.compile(r"NV\d+", re.IGNORECASE)
WORD = re.compile(r"[^\W_]+(?:\.[^\W_]+)*", re.UNICODE)   # chữ / số; giữ dấu chấm giữa ("12AB.CD1234" là một từ)
REFUSED_UNCLEAR = "chưa rõ là nhân viên nào — gõ mã NV01…, họ tên đủ, 2 chữ cuối của tên hoặc mã SM (đúng một người)"


def names_enabled() -> bool:
    """Cờ `KPI_NAMES`: "on" / "off" (thiếu = off). Giá trị khác ⇒ dừng lúc bật (không đoán là bật hay tắt)."""
    value = os.getenv("KPI_NAMES", "off")   # đúng chữ `on` / `off` — "On", " on ", rỗng ⇒ dừng (như KPI_SYSTEM_ONLY: không đoán ý người đặt)
    if value not in ("on", "off"):
        raise ValueError("KPI_NAMES phải là on hoặc off")
    return value == "on"


def normalize(text: str) -> str:
    """Khoá so: bỏ dấu tiếng Việt (cả đ ⇒ d), chữ thường, gom khoảng trắng."""
    plain = unicodedata.normalize("NFD", str(text).replace("đ", "d").replace("Đ", "D"))
    plain = "".join(char for char in plain if unicodedata.category(char) != "Mn")
    return " ".join(plain.lower().split())


def _aliases(entry: dict) -> set[str]:
    words = normalize(entry["name"]).split()
    found = {" ".join(words)} | ({" ".join(words[-2:])} if len(words) >= 2 else set())
    return {alias for alias in found | {normalize(entry["sm_code"])} if alias}


def alias_table(entries: dict[str, dict]) -> dict[str, str | None]:
    """{khoá so: mã che} — khoá dùng chung cho ≥ 2 người ⇒ None (không rõ, từ chối). `entries` = {mã che: {"name", "sm_code"}}."""
    table: dict[str, str | None] = {}
    for masked, entry in entries.items():
        for alias in _aliases(entry):
            table[alias] = masked if table.get(alias, masked) == masked else None
    return table


def resolve_employee(value, entries: dict[str, dict]) -> tuple[str | None, str | None]:
    """(mã che, None) hoặc (None, lời từ chối). Mã che NVxx đi thẳng (lõi kiểm như cũ); lời từ chối không nhắc lại chuỗi đã gửi (có thể là tên thật)."""
    if not isinstance(value, str):
        return None, REFUSED_UNCLEAR
    if MASKED.fullmatch(value.strip()):
        return value.strip().upper(), None
    masked = alias_table(entries).get(normalize(value))
    return (masked, None) if masked else (None, REFUSED_UNCLEAR)


EMPLOYEE_ARGS = ("employee_id", "employee_a", "employee_b")   # mọi tham số nhân viên của 11 công cụ (tool_specs.py)


def resolve_args(args: dict, entries: dict[str, dict]) -> tuple[dict, dict | None]:
    """Tham số công cụ với tên / 2 chữ cuối / mã SM đã đổi sang mã che: (tham số mới, None) hoặc (tham số gốc, lời từ chối kiểu công cụ).
    Một chỗ cho cả hai cửa vào: backend `/api/tool/*` (Capitol) và đường chat (`backend_client.call_tool`)."""
    resolved = dict(args)
    for key in EMPLOYEE_ARGS:
        if resolved.get(key) not in (None, ""):
            resolved[key], unclear = resolve_employee(resolved[key], entries)
            if unclear:
                return args, {"refused": unclear, "params_used": {key: "(chữ không khớp đúng một nhân viên)"}}
    return resolved, None


def with_names(result, entries: dict[str, dict]):
    """Bản sao kết quả + `name` / `sm_code` cạnh mỗi `employee_id` là mã che có trong bảng (không ghi đè khoá sẵn có)."""
    if isinstance(result, dict):
        out = {key: with_names(value, entries) for key, value in result.items()}
        code = result.get("employee_id")
        if isinstance(code, str) and code.upper() in entries:
            out.setdefault("name", entries[code.upper()]["name"])
            out.setdefault("sm_code", entries[code.upper()]["sm_code"])
        return out
    if isinstance(result, list):
        return [with_names(item, entries) for item in result]
    return result


def _answer_key(text: str) -> str:
    """Khoá so GIỮ dấu tiếng Việt (NFC), không phân biệt hoa thường, gom khoảng trắng."""
    return " ".join(unicodedata.normalize("NFC", str(text)).casefold().split())


def _has_no_accents(text: str) -> bool:
    """Cụm không mang dấu tiếng Việt nào (kể cả đ) — người gõ không dấu."""
    return _answer_key(text) == normalize(text)


def names_to_codes(answer: str, entries: dict[str, dict]) -> str:
    """Thay tên / mã SM trong CÂU TRẢ LỜI bằng mã che của người đó, cụm dài trước. `entries` = bảng tên ĐỦ ĐỘI (không suy từ kết quả công cụ của
    lượt — 12/11: lượt chỉ gọi công cụ cho A thì tên B không có trong kết quả ⇒ "<tên B> được <tiền A>" không được đổi ra mã ⇒ lọt).

    Luật so (nhóm kỹ thuật 12/11, hai ca thật kéo hai phía): khớp không dấu như cửa vào, NHƯNG cụm 1–2 chữ (2 chữ cuối của tên) chỉ được đổi khi
    (a) dấu trong câu đúng bằng dấu của tên ("bá mẫn", "Bá Mẫn"), hoặc (b) cụm trong câu không có dấu nào ("ba man" — người hỏi gõ không dấu, mô hình
    lặp lại). Cụm CÓ dấu mà KHÁC dấu của tên thì không phải tên: "… thì thêm 2.800.000 đ" không thành người tên "Thị Thêm" (từng chặn oan).
    Họ tên đủ (≥ 3 chữ) và mã SM: so không dấu, không phân biệt hoa thường. Cụm khớp ≥ 2 người giữ nguyên chữ ⇒ số sau đó theo mã gần nhất phía trước.
    """
    if not entries:
        return answer
    table = {alias: code for alias, code in alias_table(entries).items() if code}
    exact = {code: {_answer_key(" ".join(entry["name"].split()[-2:])), _answer_key(entry["name"])} for code, entry in entries.items()}
    sm_keys = {normalize(entry["sm_code"]) for entry in entries.values()}
    longest = max(len(alias.split()) for alias in table)
    words = list(WORD.finditer(answer))
    out, position, index = [], 0, 0
    while index < len(words):
        for size in range(min(longest, len(words) - index), 0, -1):
            span = words[index:index + size]
            # các từ của cụm phải liền nhau (chỉ khoảng trắng chen giữa) — "Hà, Nội" không thành "Hà Nội"
            if any(answer[a.end():b.start()].strip() for a, b in zip(span, span[1:])):
                continue
            text = " ".join(word.group(0) for word in span)
            code = table.get(normalize(text))
            if code and (size >= 3 or normalize(text) in sm_keys or _answer_key(text) in exact[code] or _has_no_accents(text)):
                out += [answer[position:span[0].start()], code]
                position, index = span[-1].end(), index + size
                break
        else:
            index += 1
    return "".join(out) + answer[position:]
