"""Template-to-capability registry for E2B sandbox images.

Single source of truth for what is pre-installed in each named template.
Consumed by:
  - Prompt builders (AgentNode._build_dependency_note,
    FlexibleCodeInterpreterNode._build_sandbox_manifest_prompt) so the model
    always sees an accurate manifest regardless of which image is active.
  - Build scripts (e2b-template/build.py) to keep the manifest in sync with
    the actual package installs.
  - E2BSkillsChatService to inject a capability note into the skill prompt.

Template names must match the E2B tag used in settings (without the version
suffix, e.g. "capai-base-image" not "capai-base-image:v1.0.0"). The lookup
strips the ":vX.Y.Z" suffix automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TemplateCapabilities:
    """What is pre-installed in an E2B template.

    ``pip_packages``, ``npm_globals``, and ``apt_packages`` list the relevant
    user-facing packages — not a complete apt/pip manifest, just the ones worth
    advertising to the model so it doesn't try to install what's already there.
    """

    pip_packages: tuple[str, ...] = field(default_factory=tuple)
    npm_globals: tuple[str, ...] = field(default_factory=tuple)
    apt_packages: tuple[str, ...] = field(default_factory=tuple)

    # Human-readable prose description of the template's purpose.
    description: str = ""

    # Whether this image has LibreOffice (soffice) available.
    has_libreoffice: bool = False

    # Whether this image has Node.js 20 available.
    has_node: bool = False

    # Whether this image has the document-parsing stack (PyMuPDF / tesseract).
    has_document_stack: bool = False

    # Shared, general-purpose image (base/legacy code-interpreter/file-processor)
    # serving arbitrary tasks, as opposed to a dedicated, bounded per-skill
    # image. Callers use this to decide whether the model should be told it
    # may install packages beyond this list (open) or not (closed) — see
    # ``build_capability_note``'s ``allow_extra_installs``.
    general_purpose: bool = False


# ---------------------------------------------------------------------------
# Base image — shared floor for all nodes
# ---------------------------------------------------------------------------

_BASE_CAPABILITIES = TemplateCapabilities(
    description=(
        "Full document + data-science + code-execution stack. "
        "Serves Agent, Code Interpreter, D3, and Org Chart nodes directly."
    ),
    has_document_stack=True,
    has_node=True,
    general_purpose=True,
    pip_packages=(
        # PDF
        "pymupdf",
        "pdfplumber",
        "reportlab",
        # OCR
        "pytesseract",
        "pillow",
        # Excel
        "openpyxl",
        "xlrd",
        # Office
        "python-docx",
        "python-pptx",
        # Data science
        "pandas",
        "numpy",
        "scipy",
        "matplotlib",
        "seaborn",
        "plotly",
        "scikit-learn",
        "pyarrow",
        # Web / XML
        "requests",
        "beautifulsoup4",
        "lxml",
        # Utilities
        "sympy",
        "tabulate",
        "python-magic",
        "chardet",
        "markitdown",
        "ipykernel",
    ),
    apt_packages=(
        "tesseract-ocr",
        "poppler-utils",
        "ffmpeg",
    ),
)

# ---------------------------------------------------------------------------
# Office layer — inherits base, adds LibreOffice / fonts / gcc / pandoc
# ---------------------------------------------------------------------------

_OFFICE_CAPABILITIES = TemplateCapabilities(
    description=(
        "Intermediate layer (not used directly). "
        "Adds LibreOffice, OOXML fonts, gcc, and pandoc on top of capai-base-image."
    ),
    has_document_stack=True,
    has_libreoffice=True,
    has_node=True,
    pip_packages=_BASE_CAPABILITIES.pip_packages,
    apt_packages=(
        *_BASE_CAPABILITIES.apt_packages,
        "libreoffice",
        "pandoc",
        "gcc",
    ),
)

# ---------------------------------------------------------------------------
# Per-skill images
# ---------------------------------------------------------------------------

_PPTX_CAPABILITIES = TemplateCapabilities(
    description="PowerPoint skill. Extends office layer with pptxgenjs and sharp.",
    has_document_stack=True,
    has_libreoffice=True,
    has_node=True,
    pip_packages=(
        *_OFFICE_CAPABILITIES.pip_packages,
        "defusedxml",
    ),
    npm_globals=("pptxgenjs", "sharp"),
    apt_packages=_OFFICE_CAPABILITIES.apt_packages,
)

_DOCX_CAPABILITIES = TemplateCapabilities(
    description="Word skill. Extends office layer with npm docx and defusedxml.",
    has_document_stack=True,
    has_libreoffice=True,
    has_node=True,
    pip_packages=(
        *_OFFICE_CAPABILITIES.pip_packages,
        "defusedxml",
    ),
    npm_globals=("docx",),
    apt_packages=_OFFICE_CAPABILITIES.apt_packages,
)

_XLSX_CAPABILITIES = TemplateCapabilities(
    description="Spreadsheet skill. Office layer; no additional packages.",
    has_document_stack=True,
    has_libreoffice=True,
    has_node=True,
    pip_packages=_OFFICE_CAPABILITIES.pip_packages,
    apt_packages=_OFFICE_CAPABILITIES.apt_packages,
)

_EY_PPTX_CAPABILITIES = TemplateCapabilities(
    description=(
        "EY PowerPoint skill. Extends capai-base-image with Node only; "
        "npm dependencies are bundled into vendor.bundle.js."
    ),
    has_document_stack=True,
    has_node=True,
    pip_packages=_BASE_CAPABILITIES.pip_packages,
    apt_packages=_BASE_CAPABILITIES.apt_packages,
)

# Legacy image (e2b-template/template_code_interpreter.py) — pre-dates the
# document stack. Do NOT alias to _BASE_CAPABILITIES: it has no pymupdf,
# pytesseract, python-docx, or python-pptx, so that would advertise packages
# the model doesn't have. Keep accurate until this image is retired
# alongside capai-file-processor after the measurement step completes.
_CODE_INTERPRETER_CAPABILITIES = TemplateCapabilities(
    description=(
        "Legacy code-interpreter image (no document stack). "
        "Supports Python, JavaScript (Deno), and shell execution."
    ),
    has_node=True,
    general_purpose=True,
    pip_packages=(
        "ipykernel",
        "pandas",
        "numpy",
        "scipy",
        "matplotlib",
        "seaborn",
        "plotly",
        "scikit-learn",
        "pillow",
        "requests",
        "beautifulsoup4",
        "lxml",
        "sympy",
        "pyarrow",
        "openpyxl",
        "xlrd",
        "tabulate",
        "chardet",
    ),
)

# Legacy image (e2b-template/template_file_processing.py) — pre-dates
# template_base.py's Node.js 20 / Deno / ipykernel additions, which were
# promoted from this image onto capai-base-image. Do NOT alias to
# _BASE_CAPABILITIES: this image has neither Node/Deno nor markitdown/
# ipykernel, so that would advertise runtimes the model doesn't have. Keep
# accurate until this image is retired after the measurement step completes.
_FILE_PROCESSOR_CAPABILITIES = TemplateCapabilities(
    description=(
        "Legacy file-processor image (no Node.js/Deno/ipykernel). "
        "Full document + data-science stack for file-processing nodes."
    ),
    has_document_stack=True,
    general_purpose=True,
    pip_packages=(
        "pymupdf",
        "pdfplumber",
        "reportlab",
        "pytesseract",
        "pillow",
        "openpyxl",
        "xlrd",
        "python-docx",
        "python-pptx",
        "pandas",
        "numpy",
        "scipy",
        "matplotlib",
        "seaborn",
        "plotly",
        "scikit-learn",
        "pyarrow",
        "requests",
        "beautifulsoup4",
        "lxml",
        "sympy",
        "tabulate",
        "python-magic",
        "chardet",
    ),
    apt_packages=(
        "tesseract-ocr",
        "poppler-utils",
        "ffmpeg",
    ),
)

# [ghi chú] HỘP PHIÊN của bộ chạy thử (sandbox-py:v3, người chạy
# sandbox/nguoi_chay_phien.py) — danh sách ĐO trong image (docker run --network none, 17/11): Python dists, /usr/lib/node_modules, command -v.
# Vì sao: các mẫu capai-* (mặc định settings) hứa pymupdf / pdfplumber / pytesseract / markitdown / tesseract / ffmpeg / unzip / Deno và dặn
# "đừng cài" ⇒ hộp mình KHÔNG có ⇒ AI viết mã hỏng sau khi đã tốn tiền. Cờ has_node / has_document_stack / has_libreoffice để False vì câu
# mẫu của chúng nói sai với hộp này ("Deno", "fitz / pytesseract", "profile pre-warmed"); sự thật ghi ở description. general_purpose False:
# hộp không mạng, không pip ⇒ câu "đừng cài" là đúng cho mọi khối.
_CHAY_THU_V3_CAPABILITIES = TemplateCapabilities(
    description=(
        "Offline sandbox: NO network and no root, so nothing can be installed (no pip / npm; apt-get cannot install). Python 3 with the packages below; "
        "Node.js (no Deno) with the global npm packages below; LibreOffice `soffice --headless` (first run may take ~10 s); "
        "`pandoc`; poppler `pdftotext` / `pdfinfo` / `pdftoppm`. NOT available: pymupdf (fitz), pdfplumber, pytesseract / tesseract OCR, "
        "markitdown, ffmpeg, unzip / zip, git. Read PDFs with `pdftotext -layout file.pdf -`; scanned PDFs / images cannot be OCR'd here — "
        "say so instead of guessing. DOCX: python-docx; PPTX: python-pptx; XLSX: openpyxl / xlsxwriter / pandas. "
        "Input files are in /home/user/input/, write outputs to /home/user/output/."
    ),
    pip_packages=("defusedxml", "lxml", "matplotlib", "numpy", "openpyxl", "pandas", "pillow", "python-docx", "python-pptx", "xlsxwriter"),
    npm_globals=("docx", "pptxgenjs", "sharp"),
    apt_packages=("libreoffice (soffice)", "pandoc", "poppler-utils (pdftotext, pdfinfo, pdftoppm)", "nodejs"),
    has_libreoffice=False,
    has_node=False,
    has_document_stack=False,
    general_purpose=False,
)


# [ghi chú] Template E2B bậc 2 `chay-thu-e2b-doc:v1` (build b3b0eeb3). Danh sách = ĐO trong máy thật
# (va_e2b/bac2/thu_sau_dung.py 11:23 18/11: nproc 1, MemTotal ≈ 1 GiB, tesseract eng / osd / vie, pdftotext / pdftoppm, import đủ các gói dưới,
# thu_doc_tai_lieu 9/9). [E minor 18/11] mạng theo cờ CHAY_THU_E2B_MANG (sandbox_runtime.py:222; chú thích :68 "TẮT" là ghi cũ) ⇒ câu cài gói có điều kiện.
# has_document_stack False: câu mẫu của cờ đó hứa python-pptx — CHƯA đo trên template này ⇒ sự thật ghi ở description.
_CHAY_THU_E2B_DOC_CAPABILITIES = TemplateCapabilities(
    description=(
        "E2B sandbox (1 vCPU, 1 GiB RAM, Python 3.13). Document reading is pre-installed: for PDF / Word files call "
        "`from doc_tai_lieu import doc_pdf, doc_docx` — doc_pdf(path) returns [{'trang', 'chu', ...}] per page (text layer, OCR for scanned pages "
        "with Tesseract vie+eng), doc_docx(path) returns sections with paragraphs and tables. Also available: PyMuPDF (fitz), pymupdf-layout, "
        "pytesseract + tesseract (eng, vie), pdf2image, opencv-python-headless, onnxruntime, python-docx, lxml, Pillow, openpyxl, pandas, numpy; "
        "poppler `pdftotext` / `pdftoppm`. Input files are in /home/user/input/, write outputs to /home/user/output/. "
        "Installing extra packages only works when this run has network access; if `install_packages` fails, say so and use what is installed."
    ),
    pip_packages=("pymupdf", "pymupdf-layout", "pytesseract", "pdf2image", "opencv-python-headless", "onnxruntime", "python-docx", "lxml",
                  "pillow", "openpyxl", "pandas", "numpy"),
    apt_packages=("tesseract-ocr (eng, vie)", "poppler-utils (pdftotext, pdftoppm)"),
    has_libreoffice=False,
    has_node=False,
    has_document_stack=False,
    general_purpose=False,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Maps template name (without version tag) → capabilities.
# Lookup is performed with _strip_version().
TEMPLATE_REGISTRY: dict[str, TemplateCapabilities] = {
    "capai-base-image": _BASE_CAPABILITIES,
    # Legacy name — narrower capabilities than the base image (see above).
    "capai-file-processor": _FILE_PROCESSOR_CAPABILITIES,
    # Legacy name — narrower capabilities than the base image (see above).
    "capai-code-interpreter": _CODE_INTERPRETER_CAPABILITIES,
    # Dev-workspace variants (distinct deployed names, not version tags —
    # see e2b-template/build_dev.py / build_code_interpreter_dev.py).
    "capai-file-processor-dev": _FILE_PROCESSOR_CAPABILITIES,
    "capai-code-interpreter-dev": _CODE_INTERPRETER_CAPABILITIES,
    "capai-skill-office": _OFFICE_CAPABILITIES,
    "capai-skill-pptx": _PPTX_CAPABILITIES,
    "capai-skill-docx": _DOCX_CAPABILITIES,
    "capai-skill-xlsx": _XLSX_CAPABILITIES,
    "capai-skill-ey-pptx": _EY_PPTX_CAPABILITIES,
    "chay-thu-sandbox-py-v3": _CHAY_THU_V3_CAPABILITIES,   # [ghi chú] hộp phiên sandbox-py:v3 (đo)
    "chay-thu-e2b-doc": _CHAY_THU_E2B_DOC_CAPABILITIES,   # [ghi chú] E2B bậc 2 chay-thu-e2b-doc:v1 (đo 18/11)
}


def _strip_version(template_name: str) -> str:
    """Remove a semver tag suffix, e.g. "capai-skill-pptx:v1.2.0" → "capai-skill-pptx"."""
    return template_name.split(":")[0] if template_name else ""


def get_capabilities(template_name: str) -> TemplateCapabilities | None:
    """Return capabilities for *template_name*, or None if not in the registry.

    Strips the version suffix before lookup so callers can pass the full
    "capai-skill-pptx:v1.2.0" tag without special-casing.
    """
    return TEMPLATE_REGISTRY.get(_strip_version(template_name))


# ---------------------------------------------------------------------------
# Prompt helpers — called by the prompt builders in node.py
# ---------------------------------------------------------------------------

_FILE_INPUT_GUIDANCE = (
    "File input handling:\n"
    "- The following file types may arrive in /home/user/input/:\n"
    "  PDF (text or scanned), DOCX, PPTX, XLSX, images (PNG/JPEG), CSV, TXT.\n"
    "- For text PDFs: `import fitz; doc = fitz.open(path)` — returns text directly.\n"
    "- For scanned PDFs or images: rasterize with `pdftoppm` then call "
    "`pytesseract.image_to_string()` for OCR.\n"
    "- For DOCX: `import docx; d = docx.Document(path)`.\n"
    "- For PPTX: `from pptx import Presentation; prs = Presentation(path)`.\n"
    "- For XLSX: `import openpyxl; wb = openpyxl.load_workbook(path)`.\n"
    "Do NOT install any of these packages — they are pre-installed."
)


def build_capability_note(
    template_name: str, *, allow_extra_installs: bool = False
) -> str:
    """Return a system-prompt paragraph describing what is pre-installed.

    Returns an empty string if the template is not in the registry, which
    degrades gracefully — the model will see only the skill's SKILL.md.

    ``allow_extra_installs`` controls the closing line: skill nodes (the
    default) run a closed, self-contained image where nothing beyond the
    list is expected to be needed, so the note says not to install anything
    else. Agent and Code Interpreter run arbitrary user tasks and still
    expose the ``install_packages`` tool, so telling them the same thing
    would make them refuse tasks that need a package outside the list —
    pass ``allow_extra_installs=True`` there instead.
    """
    cap = get_capabilities(template_name)
    if cap is None:
        return ""

    lines = [f"Sandbox: {cap.description}", ""]

    if cap.pip_packages:
        pip_list = ", ".join(f"`{p}`" for p in sorted(cap.pip_packages))
        lines.append(f"Pre-installed Python packages: {pip_list}")

    if cap.has_libreoffice:
        lines.append(
            "LibreOffice (`soffice --headless`) is pre-installed and its "
            "first-run profile is pre-warmed — no cold-start delay."
        )

    if cap.npm_globals:
        npm_list = ", ".join(f"`{p}`" for p in cap.npm_globals)
        lines.append(
            f"Global npm packages: {npm_list} — `NODE_PATH` is set, so "
            "`require()` resolves them from any working directory."
        )

    if cap.apt_packages:
        apt_list = ", ".join(f"`{p}`" for p in sorted(cap.apt_packages))
        lines.append(f"System tools: {apt_list}")

    if cap.has_node:
        lines.append("Node.js 20 and Deno are available.")

    lines.append("")
    if allow_extra_installs:
        lines.append(
            "The packages above are already installed — no need to install "
            "them again. Need something else? Use `install_packages` first."
        )
    else:
        lines.append("Do NOT run `pip install`, `npm install`, or `apt-get install` — ")
        lines.append("all listed packages are already present.")

    if cap.has_document_stack:
        lines += ["", _FILE_INPUT_GUIDANCE]

    return "\n".join(lines)
