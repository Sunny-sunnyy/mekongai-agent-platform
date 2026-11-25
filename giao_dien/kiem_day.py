"""Hai cảnh báo trước khi Lưu (đối chiếu kỹ thuật). CHỈ CẢNH BÁO (muc "canh_bao"), không chặn Lưu. Hàm thuần, không mạng.

1. noi_cheo_ten — dây từ khối Nhận file F vào khối X, tên X KHÔNG có từ nào chung với tên F, nhưng CÓ từ chung với tên một khối Nhận file
   khác F2 ⇒ "có thể nối chéo". (FCA v6: file "… Wind Down Plan 2024" vào "ILAAP Extraction Agent", trong khi có file "… ILAAP 2024".)
2. khoa_lech — khối X nhắc một khoá dạng a_b_c (vd section_6_adverse_findings) mà KHÔNG khối nào nối thẳng vào X có khoá đó, trong khi
   có khối nối vào X dùng khoá GẦN TRÙNG — cùng số phần, khác đúng 1 phần (vd section_7_adverse_findings) ⇒ "khoá có thể đã đổi tên ở khối trước".
   (FCA v6: Add Information Request Agent đọc section_6_adverse_findings, Synthesis đã dời sang section_7.)

Đầu vào theo hình CHUNG của trang: nodes [{id, type, name, params{}}], edges [{from_node, to_node, to_port}].
"""
import re
import unicodedata

_TU = re.compile(r"[0-9a-zA-ZÀ-ỹ]+")
_KHOA = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}\b")
# từ chung chung — bỏ khi so tên (Anh + Việt, không dấu)
_BO = {"file", "files", "input", "upload", "uploads", "data", "document", "documents", "doc", "agent", "node", "the", "and", "for", "of",
       "nhan", "tai", "len", "du", "lieu", "khoi", "doc", "boc", "tach", "tep", "tu", "vao", "cho", "cua", "va", "extraction", "extract",
       "analysis", "analyzer", "report", "output", "text", "csv", "xlsx", "pdf", "docx"}
_O_CHU = ("system_prompt", "user_prompt", "task", "request", "preview_context", "custom_instructions", "deep_research_query",
          "output_spec_description", "text_input")


def _khong_dau(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D")


def tu_cua(ten: str) -> set:
    """Từ có nghĩa trong tên: ≥ 3 ký tự hoặc có chữ số, không dấu, chữ thường, bỏ từ chung chung và năm (4 chữ số)."""
    out = set()
    for t in _TU.findall(_khong_dau(ten or "").lower()):
        if t in _BO or re.fullmatch(r"(19|20)\d\d", t) or (len(t) < 3 and not any(ch.isdigit() for ch in t)):
            continue
        out.add(t)
    return out


def _chu(n: dict) -> str:
    pr = n.get("params") or {}
    return "\n".join(str(pr.get(f)) for f in _O_CHU if isinstance(pr.get(f), str))


def canh_bao(nodes: list, edges: list) -> list[dict]:
    theo = {n.get("id"): n for n in nodes if n.get("id")}
    ten = lambda n: (n.get("label") or n.get("name") or n.get("type") or "?").strip()   # noqa: E731  (trang: "label")
    ra = []
    files = [n for n in nodes if n.get("type") == "file_input_node"]
    # 1. nối chéo theo tên
    for e in edges:
        f, x = theo.get(e.get("from_node")), theo.get(e.get("to_node"))
        if not f or not x or f.get("type") != "file_input_node":
            continue
        tf, tx = tu_cua(ten(f)), tu_cua(ten(x))
        if not tx or tf & tx:
            continue
        khac = [g for g in files if g is not f and tu_cua(ten(g)) & tx]
        if khac:
            ra.append({"muc": "canh_bao", "code": "noi_cheo_ten", "node": x.get("id"), "field": e.get("to_port"),
                       "message": f"File “{ten(f)}” nối vào “{ten(x)}”, nhưng tên “{ten(x)}” giống file “{ten(khac[0])}” hơn — có thể nối chéo.",
                       "suggestion": "Kiểm lại dây file; nếu đúng ý thì bỏ qua cảnh báo này."})
    # 2. khoá lệch với khối nối thẳng vào
    vao = {}
    for e in edges:
        if e.get("to_node") in theo and e.get("from_node") in theo:
            vao.setdefault(e["to_node"], []).append(theo[e["from_node"]])
    for xid, nguon in vao.items():
        x = theo[xid]
        kx = set(_KHOA.findall(_chu(x)))
        kn = set().union(*[set(_KHOA.findall(_chu(s))) for s in nguon]) if nguon else set()
        if not kn:
            continue
        for k in sorted(kx - kn):
            # chỉ báo khi khối trước có khoá GẦN TRÙNG: cùng số phần, khác đúng 1 phần (section_6_x ↔ section_7_x; …_gbp ↔ …_gbpm).
            # Khoá khối tự đặt cho đầu ra của mình thường không có bản gần trùng ở khối trước ⇒ không báo (thử trên R2: hết báo ồn).
            pk = k.split("_")
            gan = sorted(c for c in kn if len(c.split("_")) == len(pk) and sum(a != b for a, b in zip(c.split("_"), pk)) == 1)
            if gan:
                ra.append({"muc": "canh_bao", "code": "khoa_lech", "node": xid, "field": None,
                           "message": f"“{ten(x)}” nhắc khoá “{k}” nhưng khối nối vào nó không có khoá này (có: {', '.join(gan[:4])}).",
                           "suggestion": "Khoá có thể đã đổi tên ở khối trước — sửa lời dặn cho khớp."})
    return ra
