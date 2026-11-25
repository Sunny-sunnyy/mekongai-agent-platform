"""Đọc PDF / Word trong máy E2B bậc 2 (template chay-thu-e2b-doc) bằng code RAG mức 1 của mekongai (9 file chép đúng commit e2b4dd97,
sha ở MEKONG_DOC_SHA256.txt). [ghi chú]

AI trong khối chạy code gọi:  from doc_tai_lieu import doc_pdf, doc_docx
Vì sao có module bọc: code mekongai trả kiểu dữ liệu riêng của app (PageText, DataFrame, dict trạng thái trang) ⇒ đưa về list dict đơn giản,
và KHOÁ hai cờ gọi mạng ngay lúc import.
"""
import os

# [E minor 2] GHI ĐÈ (không setdefault): thiếu biến thì page_vision.vlm_enabled() mặc định "true" (page_vision.py:103-105) ⇒ đọc ảnh Word gọi
# luna của app; AI có thể đã xoá / đổi env trước khi import ⇒ module tự ép tắt. Docling (mô hình 164 MB) cũng tắt (docling_order.py:23).
os.environ["PDF_VLM_ENABLED"] = "false"
os.environ["PDF_DOCLING_ORDER"] = "false"

TRAN_KY_TU = 200_000   # = SANDBOX_TOOL_OUTPUT_MAX_CHARS của khối chạy code ⇒ một file không làm tràn đầu ra
CHU_KHONG_DOC_DUOC = "[không đọc được chữ ở trang này — trang quét / ảnh, OCR không ra chữ]"


def _cat(tong, phan):
    """Thêm `phan` nếu còn chỗ trong trần; trả (chuỗi thêm được, đã cắt?)."""
    con = TRAN_KY_TU - tong
    if len(phan) <= con:
        return phan, False
    return phan[:max(con, 0)], True


def doc_pdf(duong, ngon_ngu="vie+eng"):
    """PDF ⇒ [{"trang": n, "chu": str, "bang": [[ô, ...], ...] list, "cach_doc": "native" | "ocr" | …}, ...] (trang đếm từ 1).
    Chữ cả file ≤ 200.000 ký tự; vượt ⇒ trang cuối có "da_cat": True và các trang sau không đọc vào kết quả."""
    from controllers.documents.pdf.process_pdf import PageText, load_pdf_pages
    trang_ra, tong = [], 0
    for so, phan_tu in enumerate(load_pdf_pages(duong, language=ngon_ngu, vlm_client=None), start=1):   # vlm_client=None ⇒ không bao giờ gọi luna
        chu = "\n".join(e.text for e in phan_tu if isinstance(e, PageText)).strip()
        bang = []
        for e in phan_tu:
            if isinstance(e, dict) and e.get("type") == "table" and hasattr(e.get("data"), "values"):   # process_pdf.py:499 — DataFrame ở khoá "data"
                df = e["data"]
                bang.append([[str(c) for c in df.columns]] + [[str(x) for x in hang] for hang in df.values.tolist()])
        trang_thai = next((e for e in phan_tu if isinstance(e, dict) and e.get("type") == "page_status"), {})
        if not chu and not bang:
            chu = CHU_KHONG_DOC_DUOC
        chu, da_cat = _cat(tong, chu)
        tong += len(chu)
        trang_ra.append({"trang": so, "chu": chu, "bang": bang, "cach_doc": trang_thai.get("method", "?")} | ({"da_cat": True} if da_cat else {}))
        if da_cat:
            break
    return trang_ra


def doc_docx(duong):
    """.docx ⇒ [{"muc": "Chương II > Điều 3 …", "doan": [str, ...], "bang": [[[ô, ...], ...], ...]}, ...] theo thứ tự trong file.
    Ảnh trong Word KHÔNG đọc (cờ VLM tắt). Chữ cả file ≤ 200.000 ký tự; vượt ⇒ mục cuối có "da_cat": True."""
    from controllers.documents.docx.process_docx import process_docx
    ra, tong = [], 0
    for muc in process_docx(duong):
        doan, bang, da_cat = [], [], False
        for b in muc.get("blocks", []):
            if isinstance(b, str):
                phan, da_cat = _cat(tong, b)
                tong += len(phan)
                doan.append(phan)
            elif isinstance(b, dict) and "rows" in b:
                hang = [[str(o) for o in r] for r in b["rows"]]
                tong += sum(len(o) for r in hang for o in r)
                bang.append(hang)
            if da_cat or tong >= TRAN_KY_TU:
                da_cat = True
                break
        ra.append({"muc": muc.get("title", ""), "doan": doan, "bang": bang} | ({"da_cat": True} if da_cat else {}))
        if da_cat:
            break
    return ra
