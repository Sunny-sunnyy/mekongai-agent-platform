"""Ảnh tải lên trực tiếp (.png, .jpg, .jpeg) ⇒ PDF một trang ⇒ đọc bằng đúng đường PDF (dòng 154 khung năng lực, C 28/10).

Vì sao qua PDF thay vì viết Loader riêng: Loader PDF đã có đủ đường cho trang chỉ là ảnh (bản scan) — máy nhận dạng chữ, trang khó gửi
mô hình đọc ảnh, ảnh chụp được giữ để hiện lại dưới câu trả lời. Ảnh đi lại đúng đường đó nên đọc như một trang scan.
"""
import fitz

from controllers.documents.pdf.process_pdf import OCR_DPI

IMAGE_SUFFIXES = ("png", "jpg", "jpeg")


def image_to_pdf(image_path, pdf_path):
    """Ghi file PDF một trang chứa ảnh. Cỡ trang = số điểm ảnh × 72 / OCR_DPI ⇒ Loader dựng ảnh trang ở OCR_DPI ra đúng cỡ ảnh gốc
    (không phóng, không thu ảnh trước khi nhận dạng chữ)."""
    pixmap = fitz.Pixmap(image_path)
    scale = 72 / OCR_DPI
    with fitz.open() as pdf:
        page = pdf.new_page(width=pixmap.width * scale, height=pixmap.height * scale)
        page.insert_image(page.rect, filename=image_path)
        pdf.save(pdf_path)
    return pdf_path
