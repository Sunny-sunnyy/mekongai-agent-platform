"""Phép quét tĩnh mã nguồn trang (`kpi_agent/dashboard/`) — giữ HAI giả định an toàn của mã phiên (A +):

1. Trang KHÔNG dựng HTML / chạy code từ chữ bên ngoài (câu trả lời của AI, chữ backend, tham số đường dẫn) — chỉ text node. Vì mã phiên nằm trong
   localStorage (script nào chạy trên origin của trang cũng đọc được) và CSP của trang còn cho script nội tuyến (`'unsafe-inline'`), đây là thứ che thật.
2. Mọi lời gọi mạng đi qua MỘT hàm `apiFetch` (src/session.ts) — không có `fetch(` nào khác (lời gọi khác sẽ thiếu mã phiên, hoặc tệ hơn: ai đó
   tự gắn mã vào URL); không file nào khác đụng tới kho của trình duyệt.

GIỚI HẠN — nói thẳng: phép quét này bắt lỗi VÔ Ý (ai đó thêm `innerHTML` cho tiện, gọi `fetch` thẳng). Nó KHÔNG bắt được người cố ý lách (ghép chuỗi
`el["inner" + "HTML"]`, `globalThis["local" + "Storage"]`, nạp code qua thư viện…) — đọc diff khi vẫn là lớp chính. đã
thử: các kiểu vô ý bị bắt, các kiểu ghép chuỗi thì không. Chỉ đọc file, không cần dữ liệu thật."""
import re
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"
CODE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
# loại file ĐƯỢC có trong src/: code TypeScript, kiểu, phông + giấy phép, ghi chú. File .js / .jsx… không được có (code phải qua tsc)
ALLOWED_SUFFIXES = {".ts", ".tsx", ".css", ".ttf", ".txt", ".md"}
HTML_SINKS = re.compile(
    r"innerHTML|outerHTML|insertAdjacentHTML|dangerouslySetInnerHTML|document\.write|\beval\s*\(|new\s+Function\s*\(|srcdoc|"
    r"setTimeout\s*\(\s*[\"'`]|setInterval\s*\(\s*[\"'`]|createElement\s*\(\s*[\"'`]script[\"'`]|createContextualFragment|DOMParser")
NETWORK = re.compile(r"\bfetch\s*\(|XMLHttpRequest|new\s+WebSocket|EventSource|sendBeacon|importScripts")
BROWSER_STORE = re.compile(r"localStorage|sessionStorage|document\.cookie|indexedDB")


def strip_comments(text: str) -> str:
    """Bỏ chú thích khối `/* … */` (kể cả nằm đầu một dòng có code phía sau) và chú thích `// …` (không cắt nhầm `https://`); giữ số dòng."""
    text = re.sub(r"/\*.*?\*/", lambda found: "\n" * found.group(0).count("\n"), text, flags=re.DOTALL)
    return re.sub(r"(?<![:\"'`])//.*", "", text)


def scan(src: Path) -> dict[str, list[str]]:
    """{loại lỗi: [file:dòng …]} cho một thư mục mã nguồn trang."""
    found = {"html": [], "network": [], "store": [], "file_type": []}
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        name = str(path.relative_to(src))
        if path.suffix not in ALLOWED_SUFFIXES:
            found["file_type"].append(name)
        if path.suffix not in CODE_SUFFIXES:
            continue
        own_file = path.name == "session.ts" and path.parent == src   # nơi DUY NHẤT được gọi mạng và đụng kho trình duyệt
        for number, line in enumerate(strip_comments(path.read_text(encoding="utf-8")).splitlines(), start=1):
            if HTML_SINKS.search(line):
                found["html"].append(f"{name}:{number}")
            if NETWORK.search(line) and not own_file:
                found["network"].append(f"{name}:{number}")
            if BROWSER_STORE.search(line) and not own_file:
                found["store"].append(f"{name}:{number}")
    return found


def test_page_source_is_clean():
    src = DASHBOARD / "src"
    assert len([p for p in src.rglob("*") if p.suffix in CODE_SUFFIXES]) >= 15, "không thấy mã nguồn trang — đường dẫn sai?"
    assert scan(src) == {"html": [], "network": [], "store": [], "file_type": []}


def test_session_file_is_the_single_network_and_store_point():
    code = strip_comments((DASHBOARD / "src" / "session.ts").read_text(encoding="utf-8"))
    assert len(re.findall(r"\bfetch\s*\(", code)) == 1                                     # đúng một lời gọi mạng thật
    # mã chỉ đi ở header; kho là localStorage CHỈ khi trang ở gốc origin riêng, còn lại sessionStorage; 401 thì xoá khỏi kho
    assert "headers.set(HEADER, key)" in code and 'import.meta.env.BASE_URL === "/" ? window.localStorage : window.sessionStorage' in code
    assert "removeItem(STORE_KEY)" in code
    assert not re.search(r"[?&]k=|searchParams\.(?:set|append)", code)                     # không chỗ nào GHÉP mã vào đường dẫn


def test_html_entry_files_have_no_inline_script():
    for name in ("index.html", "keo_tha.html"):
        html = (DASHBOARD / name).read_text(encoding="utf-8")
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.DOTALL | re.IGNORECASE)
        assert scripts, name
        for attributes, body in scripts:
            assert 'type="module"' in attributes and re.search(r'\bsrc="/src/[A-Za-z0-9_/.-]+"', attributes) and not body.strip(), (name, attributes)
        assert not re.search(r"\son[a-z]+\s*=", html, flags=re.IGNORECASE), name            # không thuộc tính onclick= / onload=… trong file gốc


def test_scan_catches_the_careless_cases(tmp_path):
    # Đối chứng: chính phép quét phải ĐỎ với từng kiểu lỗi vô ý (C thử tay 12/11 thấy bản đầu sót 4 kiểu dưới — nay phải bắt)
    (tmp_path / "session.ts").write_text('const r = await fetch("/api/x"); localStorage.getItem("k");\n', encoding="utf-8")
    cases = {
        "a.tsx": ("html", 'el.innerHTML = answer;'),
        "b.ts": ("network", 'window.fetch("/api/meta");'),                                  # bản đầu sót: `.fetch(` sau dấu chấm
        "c.js": ("file_type", 'export const x = 1;'),                                        # bản đầu sót: file .js không được quét
        "d.ts": ("html", 'const s = document.createElement("script");'),                    # bản đầu sót
        "e.ts": ("html", '/* ghi chú */ el.insertAdjacentHTML("beforeend", text);'),        # bản đầu sót: code sau chú thích khối cùng dòng
        "f.ts": ("store", 'const k = sessionStorage.getItem("kpi_session_key");'),
        "g.tsx": ("network", 'new EventSource("/api/stream");'),
        "h.ts": ("html", 'setTimeout("doIt()", 10);'),
    }
    for name, (_kind, line) in cases.items():
        (tmp_path / name).write_text(line + "\n", encoding="utf-8")
    found = scan(tmp_path)
    for name, (kind, _line) in cases.items():
        assert any(hit.startswith(name) for hit in found[kind]), (name, kind, found)
    assert not any(hit.startswith("session.ts") for hits in found.values() for hit in hits)   # file phiên được phép gọi mạng + đụng kho
    # chú thích nhắc tên hàm cấm thì KHÔNG tính; đường dẫn https:// trong chuỗi không bị coi là chú thích
    clean = tmp_path / "sach"
    clean.mkdir()
    (clean / "ok.ts").write_text('// không dùng innerHTML ở đây\n/* fetch( localStorage */\nconst u = "https://x/y"; // eval(\n', encoding="utf-8")
    assert scan(clean) == {"html": [], "network": [], "store": [], "file_type": []}
