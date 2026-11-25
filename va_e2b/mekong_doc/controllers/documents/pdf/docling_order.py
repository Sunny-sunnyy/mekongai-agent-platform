"""Thứ tự khối đọc của Docling cho trang có chữ sẵn NHIỀU CỘT (người vận hành: lấy cái tốt của Docling, giữ cái tốt của Loader).

Chỉ lấy KHỐI + THỨ TỰ KHỐI (mô hình bố cục Heron chạy ONNX + luật xếp thứ tự của Docling); chữ, bảng, xếp dòng trong khối vẫn là của Loader
(PyMuPDF, process_pdf.native_elements). Đo trên 24 trang có đáp án thứ tự: 0,918 ⇒ 0,970; 14 trang chưa thấy 0,956 ⇒ 0,987; 0 từ mất.
Docling tự đọc chữ / bảng / scan thì kém Loader ⇒ không dùng các phần đó (OCR tắt, bảng tắt) (CHANGELOG: CH-20261018-A40).
Tắt: PDF_DOCLING_ORDER=false."""
import logging
import os

logger = logging.getLogger(__name__)

# Trang có ≥ 2 cặp khối chữ đặt cạnh nhau (theo khối pymupdf-layout) mới gọi Docling: trang 1 cột = 0 cặp ⇒ không tốn ~1 s / trang.
# Đo: chọn 10 / 14 trang mới mà điểm bằng chạy mọi trang; 2 trang lợi nhất có đúng 2 cặp
MIN_SIDE_PAIRS = 2
TEXT_BLOCK_KINDS = {"text", "list-item", "section-header", "title", "caption", "footnote", "formula"}
# Mô hình Heron ONNX tải lúc build image (Dockerfile), không tải lúc chạy
MODELS_DIR = os.getenv("DOCLING_ARTIFACTS_PATH", "/app/models/docling")
PAGE_TIMEOUT_SECONDS = 120
DOCLING_THREADS = 6   # lượt này chạy riêng sau lượt 1, trước OCR ⇒ không tranh CPU với 6 tiến trình con


def docling_order_enabled():
    return os.getenv("PDF_DOCLING_ORDER", "true").strip().lower() not in ("0", "false", "no", "off")


def _side_by_side(a, b):
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    return overlap >= 0.5 * min(a.height, b.height) and (a.x1 <= b.x0 or b.x1 <= a.x0)


def side_by_side_pairs(blocks):
    """Số cặp khối chữ đặt cạnh nhau (chồng dọc ≥ 50 % khối thấp hơn, không chồng ngang) — dấu hiệu trang nhiều cột. blocks: [(Rect, loại)]."""
    rects = [rect for rect, kind in blocks if kind in TEXT_BLOCK_KINDS]
    return sum(_side_by_side(a, b) for i, a in enumerate(rects) for b in rects[i + 1:])


def page_blocks(file_path, page_numbers):
    """Khối bố cục Docling theo thứ tự đọc cho từng trang (1-based): {trang: [(x0, y0, x1, y1, loại)]}, toạ độ gốc trên-trái như PyMuPDF.
    Chạy trong tiến trình con (process_pdf.apply_docling_order): mô hình nạp một lần cho cả file; sập không kéo theo app.
    Gọi từng trang một (page_range nhiều trang làm lệch thứ tự — Docling issue #1758). Trang lỗi bị bỏ qua (giữ thứ tự pymupdf-layout)."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.object_detection_engine_options import OnnxRuntimeObjectDetectionEngineOptions
    from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import ContentLayer

    options = PdfPipelineOptions(artifacts_path=MODELS_DIR, do_ocr=False, do_table_structure=False, document_timeout=PAGE_TIMEOUT_SECONDS,
                                 accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=DOCLING_THREADS))
    options.layout_options = LayoutObjectDetectionOptions.from_preset("layout_heron_default", engine_options=OnnxRuntimeObjectDetectionEngineOptions())
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    result = {}
    for number in page_numbers:
        try:
            document = converter.convert(file_path, page_range=(number, number)).document
            height = document.pages[number].size.height
            rows = []
            # Đầu / chân trang cũng là khối (pymupdf-layout cũng có khối page-header / footer)
            for item, _ in document.iterate_items(included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}):
                for prov in getattr(item, "prov", []):
                    if prov.page_no == number:
                        box = prov.bbox.to_top_left_origin(page_height=height)
                        rows.append((box.l, box.t, box.r, box.b, str(item.label)))
            result[number] = rows
        except Exception as e:
            logger.warning(f"Docling layout failed on page {number}: {type(e).__name__}")
    return result
