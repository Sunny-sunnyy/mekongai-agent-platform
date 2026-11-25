"""[ghi chú] Bậc chặn "lỗi mềm" trước / sau khi gọi mô hình ở khối Agent.

Vì sao: R2 cho thấy khối Agent nhận đúng chữ "None" / biến {…} chưa thay vẫn gọi mô hình (tốn token, ra câu vô nghĩa), và khối báo "xong"
dù mọi công cụ đều lỗi ⇒ khối sau chạy tiếp trên rác (sổ tra 12 dòng 18, C1, C73). Capitol từng bật một phép kiểm cứng chưa đo ⇒ hỏng 318 khối
⇒ ở đây mặc định CHỈ GHI NHÃN (chế độ "do"); chỉ chặn ("chan") sau phép phát lại trên lượt cũ đạt ngưỡng ghi trước.

Hàm thuần (không I/O) — dùng chung cho khối (đối tượng pydantic-ai) và script phát lại (dict JSON từ Postgres).
"""
from __future__ import annotations

import os
import re

CHE_DO_HOP_LE = ("do", "chan", "tat")


def che_do() -> tuple[str, str | None]:
    """Đọc cờ CHAY_THU_CHAN_LOI_MEM. Không đặt ⇒ "do". Giá trị lạ ⇒ "do" + lời cảnh báo (D S7: không bật lặng chế độ chặn;
    "do" chỉ ghi nhãn nên an toàn hơn đoán "chan")."""
    v = os.environ.get("CHAY_THU_CHAN_LOI_MEM")
    if v is None or v == "":
        return "do", None
    if v in CHE_DO_HOP_LE:
        return v, None
    return "do", f"cờ CHAY_THU_CHAN_LOI_MEM lạ ({v[:20]!r}) — chỉ nhận do / chan / tat; dùng 'do'"


# ---------- TRƯỚC: đầu vào ----------
_RONG_CHU = {"none", "null", "undefined", "nan", "[]", "{}"}
# biến chưa thay: {ten_bien} hoặc {{ten_bien}} — tên kiểu định danh; KHÔNG bắt JSON ({"a": 1}) hay {} rỗng
_BIEN = re.compile(r"\{\{?\s*[A-Za-z_][A-Za-z0-9_.]*\s*\}\}?")
_CHO_TRONG = re.compile(r"\[\s*(insert|chèn|điền)\b", re.IGNORECASE)
# [ghi chú] {x} MỘT ngoặc trong mã / chuỗi nháy là cú pháp lập trình (f'{x}', `{x}`), không phải biến khuôn ⇒ bỏ các vùng đó
# trước khi tìm {x}; {{x}} HAI ngoặc (cú pháp khuôn của Capitol) chỉ bỏ trong khối mã.
_MA = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_NHAY = re.compile(r"'[^'\n]{0,200}'|\"[^\"\n]{0,200}\"")
_BIEN_HAI = re.compile(r"\{\{\s*[A-Za-z_][A-Za-z0-9_.]*\s*\}\}")


def _con_bien(s: str) -> bool:
    ngoai_ma = _MA.sub(" ", s)
    return bool(_BIEN_HAI.search(ngoai_ma) or _BIEN.search(_BIEN_HAI.sub(" ", _NHAY.sub(" ", ngoai_ma))))


def kiem_dau_vao(user_prompt: str, co_day_vao: bool = True, khuon: str | None = None) -> list[str]:
    """Lý do (mã ngắn) nếu đầu vào của khối Agent là rác; [] nếu ổn.
    co_day_vao=False (ô user_prompt không nối từ khối trước — vd lượt theo lịch chỉ có lời dặn) ⇒ không xét "rỗng" (D minor 1 / S5).
    khuon = chữ gõ sẵn của ô (node_config). [vòng 2, C đo 21/21 — C_BIEN_CHUA_THAY.md] Capitol KHÔNG thay {{x}}: ô có dây gửi
    "chữ gõ sẵn + \\n + kết quả khối trước" (workflow/runtime/implementation.py:325-338) ⇒ {{x}} trong khuôn là chuyện bình thường.
    Chỉ hỏng khi tin gửi CHỈ còn khuôn (kết quả khối trước không tới) ⇒ "còn biến" / "chỗ trống" chỉ xét khi chữ gửi == khuôn.
    Không biết khuôn (None) ⇒ không xét hai luật đó (an toàn: thà bỏ sót hơn chặn oan)."""
    s = user_prompt if isinstance(user_prompt, str) else str(user_prompt)
    t = s.strip()
    ly_do = []
    if co_day_vao and not t:
        ly_do.append("dau_vao_rong")
    elif t.lower() in _RONG_CHU:
        ly_do.append("dau_vao_none")
    chi_khuon = isinstance(khuon, str) and t == khuon.strip() and t != ""
    if chi_khuon and _con_bien(s):   # [vòng 3] trước: _BIEN.search(s) — bắt cả {x} trong câu hỏi lập trình
        ly_do.append("con_bien_chua_thay")
    if chi_khuon and _CHO_TRONG.search(s):
        ly_do.append("con_cho_trong_mau")
    return ly_do


def o_user_prompt(node_config) -> tuple[str | None, bool]:
    """(chữ gõ sẵn, có dây) của ô user_prompt từ cấu hình khối (danh sách dict field_id / value / bindable_config — dạng
    workflow/node/base/node.py:327-331 đọc; cùng dạng payload.nodes[].data.struct.params của workflow_version). Không đọc được ⇒ (None, True)."""
    for p in node_config or []:
        if isinstance(p, dict) and p.get("field_id") == "user_prompt":
            v = p.get("value")
            bc = p.get("bindable_config") or {}
            return (v if isinstance(v, str) else None), bool(bc.get("is_bound", True)) if isinstance(bc, dict) else True
    return None, True


# ---------- SAU: kết quả công cụ + câu trả lời ----------
# Lỗi HỆ THỐNG trong {"ok": false, "error": …} của công cụ (native_tools.py) — khác lời từ chối NGHIỆP VỤ ("không có đơn này",
# "đã ghi file", KPI "refused") vốn là hành vi đúng (D minor 2 / S4).
_LOI_HE_THONG = re.compile(r"lỗi hệ thống|không gọi được|không gửi được|không trả kết quả|không đọc được|chưa có khoá|"
                           r"\bHTTP \d{3}\b|traceback|exception|error code", re.IGNORECASE)
_RUN_PYTHON_LOI = {"khong_gui_duoc", "khong_co_ket_qua", "ket_qua_hong", "ket_qua_qua_lon"}   # native_tools.py:358–374 (hộp không chạy được)
_XIN_LOI = re.compile(r"^\s*(xin lỗi|rất tiếc|tôi không thể|mình không thể|i'?m sorry|sorry|i cannot|i can'?t|unfortunately)",
                      re.IGNORECASE)


def _thuoc(p, ten, mac_dinh=None):
    return p.get(ten, mac_dinh) if isinstance(p, dict) else getattr(p, ten, mac_dinh)


def _cac_phan(lich_su) -> list:
    out = []
    for m in lich_su or []:
        out.extend(_thuoc(m, "parts", None) or [])
    return out


def _cong_cu_loi(phan) -> bool | None:
    """True = lần gọi công cụ lỗi thật; False = có kết quả (kể cả lời từ chối nghiệp vụ); None = không phải phần kết quả công cụ."""
    kind = _thuoc(phan, "part_kind")
    if kind == "retry-prompt":
        return True if _thuoc(phan, "tool_name") else None   # retry-prompt không tên công cụ = mô hình trả sai khuôn đầu ra, không phải công cụ
    if kind != "tool-return":
        return None
    if _thuoc(phan, "outcome") not in (None, "success"):
        return True
    c = _thuoc(phan, "content")
    if isinstance(c, dict):
        if c.get("ok") is False:
            if _thuoc(phan, "tool_name") == "run_python":
                return c.get("ly_do") in _RUN_PYTHON_LOI
            return bool(_LOI_HE_THONG.search(str(c.get("error", ""))))
        st = c.get("status")
        return isinstance(st, int) and st >= 500
    if isinstance(c, str):
        return bool(re.match(r"\s*(error|lỗi|traceback)\b", c, re.IGNORECASE))
    return False


def _chay_code_loi(phan) -> bool | None:
    """Chỉ phần kết quả của run_python: True = đoạn code chạy lỗi (ok false, kể cả lỗi trong code), False = chạy được."""
    if _thuoc(phan, "part_kind") != "tool-return" or _thuoc(phan, "tool_name") != "run_python":
        return None
    c = _thuoc(phan, "content")
    return not (isinstance(c, dict) and c.get("ok") is True)


def kiem_dau_ra(cau_tra_loi, lich_su, co_kpi: bool = False) -> list[str]:
    """Lý do nếu khối "xong" mà thật ra hỏng; [] nếu ổn.
    - moi_cong_cu_loi: có ≥ 1 lần gọi công cụ (trừ final_result) và TẤT CẢ đều lỗi thật.
    - moi_lan_chay_code_loi: có ≥ 1 lần run_python và tất cả đều lỗi.
    - chi_xin_loi: câu ngắn mở đầu bằng xin lỗi / không thể, không có chữ số, VÀ không có công cụ nào trả kết quả được.
      KHÔNG áp cho khối KPI (D major 2: lời dặn 5b bắt nói "không làm được" ở câu đầu cho yêu cầu sửa / cộng — đúng hành vi)."""
    phan = [p for p in _cac_phan(lich_su) if _thuoc(p, "tool_name") != "final_result"]
    cc = [x for x in (_cong_cu_loi(p) for p in phan) if x is not None]
    code = [x for x in (_chay_code_loi(p) for p in phan) if x is not None]
    ly_do = []
    if cc and all(cc):
        ly_do.append("moi_cong_cu_loi")
    if code and all(code):
        ly_do.append("moi_lan_chay_code_loi")
    s = cau_tra_loi if isinstance(cau_tra_loi, str) else ""
    if (not co_kpi and (not cc or all(cc)) and len(s.strip()) <= 400 and _XIN_LOI.match(s)
            and not re.search(r"\d", s)):
        ly_do.append("chi_xin_loi")
    return ly_do


# Chữ lỗi khi chặn: CỐ ĐỊNH, không chép chữ công cụ / mô hình vào (giữ dữ liệu khỏi log + tránh mẫu "HTTP nnn", "timed out",
# "overloaded"… làm Temporal xếp vào nhóm THỬ LẠI — classification.py:232, :264, :430, nhánh 13 :1037–1108 ⇒ CLASS_INTERNAL không thử lại).
# Bậc TRƯỚC dùng đúng mảnh "no text content provided" ⇒ nhánh 0b MISSING_INPUT (không thử lại, chữ cho người dùng rõ) — :620–931.
def chu_loi(ly_do: list[str], truoc: bool) -> str:
    ma = ", ".join(ly_do)
    if truoc:
        return f"Khối Agent không gọi AI: no text content provided — đầu vào từ khối trước trống hoặc còn chỗ chưa điền ({ma}). Sửa khối trước rồi chạy lại."
    return f"Khối Agent đã chạy nhưng kết quả không dùng được ({ma}) — dừng để khối sau không chạy trên kết quả hỏng. Xem công cụ / khối trước."


# ---------- KHÔNG ĐỌC FILE ĐÍNH KÈM (B 16/11 vòng 3 — C đề xuất, đã giao; chế độ chỉ ghi nhãn) ----------
# Khối nhận file mà mô hình không mở file lần nào ⇒ kết quả không dựa trên file (127fb31c lượt 7808cdb4). Kế hoạch + tiêu chí:
# tài liệu kỹ thuật
LOAI_TAI_LIEU = {"pptx_chat_node", "docx_chat_node", "xlsx_chat_node", "data_analysis_agent_node", "d3_data_visualization_node",
                 "network_graph_output_node", "ey_pptx_chat_node"}
_MO_FILE = re.compile(r"/dau_vao|\bopen\(|read_csv|read_excel|read_json|read_parquet|load_workbook|ExcelFile|\bglob\b|listdir|scandir|"
                      r"\bPath\(|pdfplumber|fitz\.open|Document\(|Presentation\(", re.IGNORECASE)


def _ma_run_python(lich_su) -> list[str]:
    """Mã của mọi lần gọi run_python (phần tool-call) trong lịch sử pydantic-ai (dict JSON hoặc đối tượng)."""
    out = []
    for p in _cac_phan(lich_su):
        if _thuoc(p, "part_kind") == "tool-call" and _thuoc(p, "tool_name") == "run_python":
            a = _thuoc(p, "args")
            if isinstance(a, str):
                try:
                    import json
                    a = json.loads(a)
                except ValueError:
                    a = {"code": a}
            out.append(str((a or {}).get("code", "")) if isinstance(a, dict) else str(a))
    return out


# [vòng 2 luật, B 16/11 — sau phát lại 26 khối] read_file của hộp đọc file dạng CHỮ (services/sandbox/tools.py:447-451 trong hộp) ⇒ file nhị
# phân (xlsx, docx, pdf…) chỉ đọc được bằng run_code; phát lại: 14 / 15 ca "nghi" là .xlsx có run_code ⇒ gần như chắc đã đọc ⇒ không gắn.
DUOI_CHU = {"md", "markdown", "txt", "json", "html", "htm", "xml", "yaml", "yml"}


def kiem_doc_file(loai: str, co_file_vao: bool, lich_su=None, so_read_file: int | None = None, so_run_code: int | None = None,
                  duoi_file=None, trang_thai: str | None = None) -> list[str]:
    """Nhãn [] / ["khong_doc_file"] / ["co_the_khong_doc_file"]. Chỉ xét khi co_file_vao (có dây vào ô files từ khối có file) VÀ khối
    COMPLETED (trang_thai None = gọi lúc chạy, coi như xong; FAILED / khác ⇒ không xét — khối đã báo hỏng, nhãn thừa).
    - Agent (agent_node): có run_python với mã mở file ⇒ đã đọc; không ⇒ khong_doc_file.
    - Khối tài liệu (LOAI_TAI_LIEU): đếm từ sự kiện ("Calling read_file" / "Calling run_code"). read_file ≥ 1 ⇒ đã đọc; 0 và run_code 0 ⇒
      khong_doc_file; 0 và run_code > 0 ⇒ co_the_khong_doc_file CHỈ KHI mọi file đều là chữ (DUOI_CHU — đọc được bằng read_file mà không
      dùng); có file nhị phân / không biết đuôi ⇒ [] (run_code là cách đọc, mã không được lưu).
    - Loại khác / thiếu dấu vết (None) ⇒ [] (không đủ căn cứ — thà bỏ sót hơn gắn oan)."""
    if not co_file_vao or (trang_thai is not None and trang_thai != "COMPLETED"):
        return []
    if loai == "agent_node":
        if lich_su is None:
            return []
        return [] if any(_MO_FILE.search(m) for m in _ma_run_python(lich_su)) else ["khong_doc_file"]
    if loai in LOAI_TAI_LIEU:
        return kiem_doc_file_tai_lieu(so_read_file, so_run_code, duoi_file)
    return []


def kiem_doc_file_tai_lieu(so_read_file: int | None, so_run_code: int | None, duoi_file=None) -> list[str]:
    """Phần khối tài liệu của kiem_doc_file (gọi thẳng từ dịch vụ chung e2b_chat_service — dịch vụ không biết loại khối, đã biết có file)."""
    if so_read_file is None or so_run_code is None:
        return []
    if so_read_file > 0:
        return []
    if so_run_code == 0:
        return ["khong_doc_file"]
    duoi = {str(d).lower().rsplit(".", 1)[-1] for d in (duoi_file or [])}
    return ["co_the_khong_doc_file"] if duoi and duoi <= DUOI_CHU else []
