"""E2B-backed skills service (chat + one-shot generate).

Runs an Agent Skill inside a self-hosted E2B sandbox instead of Anthropic's
native server-side Skills, exposing the surfaces the skill-output nodes already
use so it is a drop-in for the native services:

  * ``stream_chat`` -> ``ChatStreamEvent``   (multi-turn doc-gen chat nodes)
  * ``stream_generate`` / ``stream_analyze`` -> ``StreamEvent``  (one-shot nodes)
  * ``download_file`` / ``upload_to_s3`` / ``process_files`` / ``config``

A single ``_run`` engine (sandbox lifecycle + skill load + agent run + output
collection) yields the base ``StreamEvent``; ``stream_chat`` wraps those into
``ChatStreamEvent`` for the doc-chat nodes. One sandbox is created per service
instance (which a node holds for the life of one ``execute``) and reused across
turns, replacing Anthropic's server-side ``container_id``; it is torn down by
``cleanup()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import zipfile
from collections.abc import AsyncGenerator, Awaitable, Callable
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic_ai import Agent, NativeToolCallPart, NativeToolReturnPart
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import UsageLimits
from sentry_sdk import capture_exception, capture_message

from ai.agentic.cache_observability import log_cache_usage, resolve_executor_model
from ai.agentic.common.chat_safety import PLATFORM_SAFETY_GUARDRAILS
from ai.agentic.context import build_capability_for_model, progress_notifier
from ai.agentic.models.fallback import get_anthropic_model_with_bedrock_failover
from core.config.ai import AnthropicModels, get_context_budget, get_spec
from core.settings import settings

# [ghi chú] trần token cho MỘT lần chạy khối chat (PowerPoint / Word / Excel / skill), cộng dồn
# qua các lần nối lại trong vòng thử bên dưới (như requests_used). Chạm trần ⇒ UsageLimitExceeded ⇒ không nối lại (không phải lỗi tạm thời).
# [ghi chú] file này CHỈ phục vụ 7 khối tài liệu (PowerPoint / Word / Excel /
# phân tích dữ liệu / D3 / …) ⇒ trần RIÊNG 200k vào / 24k ra (trước 120k / 16k, BẰNG khối Agent — Agent GIỮ 120k ở workflow/node/base/agent/
# node.py). Vì sao: 3 / 14 lượt PowerPoint trên DB hỏng vì vượt 120k (133.538 · 136.876 · 143.984) dù đầu vào nhỏ — lịch sử cộng dồn qua 9–13
# lần gọi công cụ. Kèm trần SỐ LẦN GỌI riêng 14 (PA1, B_DE_XUAT_TRAN_KHOI_VONG_GOI.md): vòng viết lại mã kéo dài ⇒ dừng sớm. Mức tối đa ở
# giao_dien/muc_toi_da.py KHOI_TAI_LIEU (đổi số ở đây ⇒ đổi bên đó).
TRAN_TOKEN_VAO = 200_000
TRAN_TOKEN_RA = 24_000
TRAN_LAN_GOI = 14   # ≤ settings.AGENT_REQUEST_LIMIT (20 ở bộ thử) — lấy số nhỏ hơn


def _gioi_han_luot(requests_used: int, tokens_in_used: int, tokens_out_used: int, bo_tran_token: bool = False) -> "UsageLimits":
    """[va_tran_tai_lieu] Trần còn lại cho một lần nối lại (cộng dồn qua các lần). Tách hàm để thử được bằng code.
    [ghi chú] bo_tran_token (khối chạy Claude 5.5 qua Bedrock): CHỈ còn rào số lần gọi, không trần tổng token vào / ra."""
    from pydantic_ai.usage import UsageLimits as _UL
    if bo_tran_token:
        return _UL(request_limit=max(1, min(settings.AGENT_REQUEST_LIMIT, TRAN_LAN_GOI) - requests_used))
    return _UL(request_limit=max(1, min(settings.AGENT_REQUEST_LIMIT, TRAN_LAN_GOI) - requests_used),
               input_tokens_limit=max(1, TRAN_TOKEN_VAO - tokens_in_used),
               output_tokens_limit=max(1, TRAN_TOKEN_RA - tokens_out_used))
from nodes.utils import (
    TRANSIENT_TRANSPORT_ERRORS,
    describe_exception,
    is_anthropic_retryable,
    resolve_deprecated_model,
)
from services.aws.s3 import S3Service
from services.document_generation.events import ChatEventType, ChatStreamEvent
from services.document_generation.naming import coerce_friendly_filename
from services.provider_keys import Provider, resolve_api_key_for_provider
from services.pydantic_ai_skills.config import SkillServiceConfig
from services.pydantic_ai_skills.events import EventType, StreamEvent
from services.pydantic_ai_skills.registry import get_skill_entry
from services.sandbox import (
    SANDBOX_EXECUTION_GUIDANCE,
    SandboxManager,
    ToolResultOffloadCapability,
    ToolResultOffloader,
    make_sandbox_tools,
)
from services.sandbox.basemaps import build_basemaps_note
from services.sandbox.paths import INPUT_DIR, OUTPUT_DIR, SKILLS_BAKED_DIR, WORK_DIR
from services.sandbox.templates import build_capability_note, get_capabilities
from services.skill_zip import build_skill_zip
from workflow.node.base.file import NodeFile

logger = logging.getLogger(__name__)

SKILL_SANDBOX_DIR = "/home/user/skills"


# [ghi chú]
# Cờ CHAY_THU_HOP_PHIEN (mặc định BẬT; false / 0 / no / off ⇒ đúng Capitol gốc: E2B + Anthropic):
#   - hộp = HopPhienManager (V2 nhóm kỹ thuật, services/sandbox/hop_phien.py) thay SandboxManager E2B (có mạng mặc định — manager.py:375-381);
#   - mô hình = gpt-6-luna; mô hình khác ⇒ BÁO LỖI (gốc lặng lẽ đổi về Claude — _resolve_anthropic_model);
#   - có mcp_servers / khối công cụ ⇒ TỪ CHỐI (chạy code + đường ra ngoài trong cùng khối — R12 trong khối);
#   - skill giải nén TRÊN agentic-backend (zipfile trong bộ nhớ, kiểm từng tên) rồi upload_file từng file — không unzip trong hộp;
#   - bỏ lớp nén ngữ cảnh: CONTEXT_COMPACTION_MODEL mặc định "claude-sonnet-4-6" (core/settings.py:1075), bộ mình không đè ⇒ nén sẽ đòi Claude.
def _hop_phien_bat() -> bool:
    return os.environ.get("CHAY_THU_HOP_PHIEN", "true").strip().lower() not in ("false", "0", "no", "off")


# [ghi chú]
# Hộp phiên hỏi CÙNG file mô hình khối + CÙNG hàm đọc với khối kiểu Agent (workflow/node/base/agent/model_resolution.py — một nguồn):
#   có dòng cho loại khối này (hoặc mac_dinh) ⇒ đi nhánh Claude GỐC của Capitol (bộ dựng trỏ về cổng AI ⇒ cổng gọi Bedrock, sổ cổng ghi mô hình thật);
#   không có dòng ⇒ gpt-6-luna Y NHƯ CŨ (= cách lùi nhanh: dat_mo_hinh.sh tat).
# LOẠI khối: dịch vụ này không được truyền loại khối; mọi khối gọi nó đều truyền emit_progress = phương thức ĐÃ BUỘC của chính khối
# (nodes/output/*/node.py: E2BSkillsChatService(build_skill_config(), …, emit_progress=self.emit_progress)) ⇒ lấy khối từ __self__, loại từ LỚP của khối
# (bài học 18/11: node_id của ĐỐI TƯỢNG lúc chạy thật là mã riêng của nút, chữ loại khối chỉ còn ở lớp). Không lần ra loại ⇒ KHÔNG đổi (luna) và nói rõ ở dòng tiến độ.
# MỌI rào của hộp phiên giữ nguyên với Claude: từ chối mcp_servers, kiểm file vào theo tổ chức, khoá file ra theo tổ chức, không nhận .html, không nén ngữ cảnh,
# rào 14 lần gọi, mỗi trả lời ≤ 1 lời gọi công cụ. [người vận hành "bỏ trần luôn"] với Claude 5.5: KHÔNG trần tổng token vào / ra của lượt, KHÔNG kẹp max_tokens
# (cài đặt mô hình y như nhánh Claude gốc của Capitol; cổng AI gửi mức đầu ra tối đa của mô hình).


def _chay_thu_mo_hinh_hop_phien(emit_progress: Any) -> tuple[str | None, tuple[str, str | None] | None]:
    """(loại khối lần ra được | None, (tên mô hình Claude, mức suy luận | None) | None = giữ luna). File mô hình khối sai khuôn ⇒ NÉM (kể cả khi không lần ra loại)."""
    from workflow.node.base.agent.model_resolution import CHAY_THU_KHOI_HOP_PHIEN, _chay_thu_mo_hinh_ep

    khoi = getattr(emit_progress, "__self__", None)
    loai = getattr(type(khoi), "node_id", None) if khoi is not None else None
    loai = loai if isinstance(loai, str) else None
    ep = _chay_thu_mo_hinh_ep(loai, "")   # gọi cả khi loai None: file hỏng phải ném, không lặng lẽ chạy luna
    return loai, (ep if loai in CHAY_THU_KHOI_HOP_PHIEN else None)


# [ghi chú] File VÀO: chỉ file NGƯỜI DÙNG TẢI LÊN của chính tổ chức chạy lượt (khoá files/<orgid>/…), hỏi cỡ trước
# khi tải — dùng lại đúng hàm đã được đã soát ở khối Agent (sua_doi/nodes/agent/native_tools.py kiem_khoa_cua_to_chuc / _hoi_co_s3 / TRAN_FILE_TAI).
# File RA: khoá S3 có mã tổ chức của lượt ({s3_folder}/<orgid>/<uuid><đuôi>) — gốc Capitol không có (khoá chung mọi tổ chức); không nhận
# trang web (.html / .htm / .xhtml — mở ra là chạy script trong trình duyệt người xem).
_DUOI_RA_KHONG_NHAN = frozenset({".html", ".htm", ".xhtml"})


def _kiem_file_vao(node_file: Any, orgid: str | None, tran: int | None = None) -> None:
    """[ghi chú] `tran` (byte): trần cỡ RIÊNG do bên gọi đưa (khối chạy code trên E2B với file người dùng: 20.000.000 — đã chốt, bằng rào ô nhận file của trang);
    không đưa ⇒ TRAN_FILE_TAI như cũ (hộp phiên, khối Agent)."""
    from nodes.agent.native_tools import TRAN_FILE_TAI, _hoi_co_s3, kiem_khoa_cua_to_chuc

    TRAN_FILE_TAI = int(tran) if tran else TRAN_FILE_TAI

    if getattr(node_file, "generated", False):
        raise ValueError("hộp phiên (chay_thu): chỉ nhận file NGƯỜI DÙNG TẢI LÊN, không nhận file do khối khác sinh ra")
    kiem_khoa_cua_to_chuc(getattr(node_file, "s3_key", ""), orgid or "")
    co = _hoi_co_s3(node_file)
    if co > TRAN_FILE_TAI:
        raise ValueError(f"hộp phiên (chay_thu): file đầu vào {co} byte, quá trần {TRAN_FILE_TAI} byte")


# [ghi chú] File ra mở bằng Excel có thể tự gọi ra ngoài / dẫn người xem tới trang lạ.
# CSV: dùng lại _csv_an_toan (R10 — đã soát ở khối Agent): ô bắt đầu bằng = + - @ (không phải số) ⇒ thêm ' ; mọi ô trong ngoặc kép.
# XLSX: ô công thức gọi ra ngoài (HYPERLINK, WEBSERVICE, FILTERXML, RTD, DDE, CALL, REGISTER, EXEC, IMPORT*, tham chiếu sổ khác "[…]",
# "://", "\\\\") ⇒ thành CHỮ (thêm ' đứng trước); bỏ siêu liên kết của ô; bỏ liên kết sổ ngoài / kết nối dữ liệu / macro.
# Chỉ GHI LẠI file khi thấy thứ cần bỏ (openpyxl ghi lại làm mất biểu đồ / ảnh) — file sạch giữ nguyên từng byte.
import re as _re

_HAM_RA_NGOAI = _re.compile(r"(\b(HYPERLINK|WEBSERVICE|FILTERXML|RTD|DDE|CALL|REGISTER(\.ID)?|EXEC|IMPORT[A-Z]*)\s*\()|\[|://|\\\\",
                            _re.IGNORECASE)
_PHAN_NGUY_HIEM = ("xl/externalLinks/", "xl/connections.xml", "xl/vbaProject.bin", "xl/queryTables/")


# [ghi chú] SỐ TÍNH SẴN. openpyxl nạp + lưu lại KHÔNG giữ kết quả đệm của công thức ⇒ file ra sau
# _xlsx_an_toan (khi có chỗ phải bỏ) hoặc sau dấu "do AI tạo" của Capitol mất số (đã chạy: v3 X0 / X1b, sandbox/v3/KET_QUA_V3.md). Máy ab-* không có
# LibreOffice ⇒ tính lại TRONG hộp phiên còn sống (image v3 có soffice, --network none): _xlsx_tinh_lai (method của dịch vụ, dưới).
def _xlsx_dem_o(b: bytes) -> tuple[int, int]:
    """(số ô công thức, số ô công thức KHÔNG có giá trị tính sẵn). Đọc lỗi ⇒ (0, 0) (không tính lại)."""
    from openpyxl import load_workbook

    try:
        f, v = load_workbook(BytesIO(b)), load_workbook(BytesIO(b), data_only=True)
    except Exception:  # noqa: BLE001
        return 0, 0
    o = [(ws.title, c.coordinate) for ws in f.worksheets for hang in ws.iter_rows() for c in hang if c.data_type == "f"]
    return len(o), sum(1 for t, k in o if v[t][k].value is None)


def _xlsx_an_toan(b: bytes) -> tuple[bytes, int]:
    """Trả (nội dung, số chỗ đã bỏ). Không đọc được ⇒ ValueError (file không lên)."""
    from openpyxl import load_workbook
    from openpyxl.worksheet.formula import ArrayFormula

    try:
        with zipfile.ZipFile(BytesIO(b)) as z:
            ten = z.namelist()
        wb = load_workbook(BytesIO(b))   # giữ công thức (không data_only)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"hộp phiên (chay_thu): file xlsx ra không đọc được ({type(exc).__name__}) — không lưu") from exc
    bo = sum(1 for t in ten if t.startswith(_PHAN_NGUY_HIEM))
    for ws in wb.worksheets:
        for hang in ws.iter_rows():
            for o in hang:
                v = o.value
                ct = v.text if isinstance(v, ArrayFormula) else (v if o.data_type == "f" and isinstance(v, str) else None)
                if ct and _HAM_RA_NGOAI.search(ct):
                    o.value = "'" + (ct if ct.startswith("=") else "=" + ct)
                    bo += 1
                if o.hyperlink is not None:
                    o.hyperlink = None
                    bo += 1
    if not bo:
        return b, 0
    wb._external_links = []
    out = BytesIO()
    wb.save(out)   # openpyxl không ghi lại kết nối dữ liệu / macro khi nạp không keep_vba
    return out.getvalue(), bo


# [ghi chú] Trang web CHỈ cho 2 skill vẽ hình.
# Khối khác vẫn từ chối .html (C14). Kiểm trên máy, trước khi lên MinIO: một nguồn ngoài duy nhất (D3 từ d3js.org); cấm iframe / object /
# embed / base / form / meta refresh; mọi src / href / url( khác trỏ ra ngoài ⇒ từ chối; chèn CSP băm từng <script> nội tuyến + connect-src
# 'none' (khuôn sua_doi/nodes/output/_do_thi_khoi.py:131-132) làm phần tử ĐẦU của <head>; lưu Content-Disposition: attachment.
# Giới hạn (D): CSP không chặn script tự chuyển trang (location = …); nút gắn onclick= bị CSP chặn (không nới 'unsafe-inline').
_SKILL_HTML = frozenset({"d3-data-viz", "org-chart"})
_D3_CDN = "https://d3js.org/d3.v7.min.js"
_TRAN_HTML = 5 * 1024 * 1024
_THE_CAM = frozenset({"iframe", "frame", "frameset", "object", "embed", "base", "form", "portal", "applet"})
_O_URL = frozenset({"src", "href", "action", "formaction", "data", "poster", "srcset", "xlink:href", "background", "ping", "manifest",
                    "codebase", "cite", "longdesc", "srcdoc"})
_RA_NGOAI = _re.compile(r"^\s*(https?:|//|javascript:|vbscript:|file:|ftp:)", _re.IGNORECASE)
_CSS_NGOAI = _re.compile(r"url\(\s*['\"]?\s*(https?:|//)|@import", _re.IGNORECASE)


def _html_an_toan(b: bytes) -> bytes:
    """Trả HTML đã chèn CSP; vi phạm ⇒ ValueError (file KHÔNG lên)."""
    import base64
    import hashlib
    from html.parser import HTMLParser

    if len(b) > _TRAN_HTML:
        raise ValueError(f"hộp phiên (chay_thu): trang web ra {len(b)} byte > trần {_TRAN_HTML}")
    try:
        chu = b.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("hộp phiên (chay_thu): trang web ra không phải UTF-8") from exc
    chu = chu.replace("\r\n", "\n").replace("\r", "\n")   # [D vòng 1 (1)] trình duyệt đổi CRLF / CR ⇒ LF trước khi băm script

    class _Doc(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.loi, self.script, self._dang, self._buf, self.co_head = [], [], None, [], False
            self.vi_tri_head = None   # (dòng, cột, chữ thẻ) của <head> THẬT — lấy từ bộ phân tích, không tìm chuỗi thô

        def handle_starttag(self, tag, attrs):
            t = tag.lower()
            if t == "head" and not self.co_head:
                self.co_head = True
                self.vi_tri_head = (*self.getpos(), self.get_starttag_text())
            if not self.co_head and t in ("script", "style", "link", "noscript", "template", "svg", "img"):
                # [D vòng 1 MAJOR] mọi thứ chạy / nạp được đứng TRƯỚC <head> thì không nằm dưới CSP ⇒ từ chối
                self.loi.append(f"<{t}> đứng trước <head>")
            if t in _THE_CAM:
                self.loi.append(f"thẻ <{t}> bị cấm")
            a = {(k or "").lower(): (v or "") for k, v in attrs}
            if t == "meta" and a.get("http-equiv", "").strip().lower() == "refresh":
                self.loi.append("meta refresh bị cấm")
            for k, v in a.items():
                ung_vien = [x.strip().split(" ")[0] for x in v.split(",")] if k in ("srcset", "imagesrcset") else [v]   # [D (2)] srcset nhiều URL
                if (k in _O_URL or k == "imagesrcset") and any(_RA_NGOAI.match(x) for x in ung_vien) \
                        and not (t == "script" and k == "src" and v.strip() == _D3_CDN):
                    self.loi.append(f"<{t} {k}=…> trỏ ra ngoài / lược đồ cấm")
                if k == "style" and _CSS_NGOAI.search(v):
                    self.loi.append(f"<{t} style> nạp ra ngoài")
            if t == "script":
                self._dang, self._buf = ("ngoai" if "src" in a else "trong"), []
                if "src" in a and a["src"].strip() != _D3_CDN:
                    self.loi.append("<script src> khác d3js.org")
            elif t == "style":
                self._dang, self._buf = "style", []

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag.lower() in ("script", "style"):
                self.handle_endtag(tag)

        def handle_data(self, data):
            if self._dang:
                self._buf.append(data)

        def handle_entityref(self, name):
            if self._dang:
                self._buf.append(f"&{name};")

        def handle_charref(self, name):
            if self._dang:
                self._buf.append(f"&#{name};")

        def handle_endtag(self, tag):
            t = tag.lower()
            if t == "script" and self._dang in ("trong", "ngoai"):
                if self._dang == "trong":
                    self.script.append("".join(self._buf))
                elif "".join(self._buf).strip():
                    self.loi.append("<script src> có nội dung kèm")
                self._dang = None
            elif t == "style" and self._dang == "style":
                if _CSS_NGOAI.search("".join(self._buf)):
                    self.loi.append("<style> nạp ra ngoài")
                self._dang = None

    d = _Doc()
    d.feed(chu)
    d.close()
    if not d.co_head:
        d.loi.append("không có <head> để chèn CSP")
    if d.loi:
        raise ValueError("hộp phiên (chay_thu): trang web ra bị từ chối — " + "; ".join(sorted(set(d.loi)))[:400])
    bam = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(x.encode("utf-8")).digest()).decode() + "'" for x in d.script)
    csp = (f"default-src 'none'; script-src https://d3js.org {bam}".rstrip() + "; style-src 'unsafe-inline'; img-src data: blob:; "
           "connect-src 'none'; font-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'")
    dong, cot, the = d.vi_tri_head
    vt = sum(len(x) for x in chu.split("\n")[: dong - 1]) + (dong - 1) + cot   # getpos: dòng tính từ 1, cột từ 0 (sau chuẩn hoá LF)
    if chu[vt: vt + len(the)] != the:
        raise ValueError("hộp phiên (chay_thu): trang web ra bị từ chối — không định vị được <head>")
    vt += len(the)
    return (chu[:vt] + f'<meta http-equiv="Content-Security-Policy" content="{csp}">' + chu[vt:]).encode("utf-8")


# [ghi chú] file ra .pptx / ảnh: khối data_analysis dựng PowerPoint có biểu đồ PNG ngay trong hộp
# (bao_cao_ket_qua/09_POWERPOINT_TU_DU_LIEU.md mục 70.5). Chỉ KIỂM, không sửa: không đạt ⇒ ValueError ⇒ file không lên MinIO (nhánh except của
# process_files ghi lý do vào log). Vì sao từ chối thay vì làm sạch: file python-pptx dựng sạch không có các phần này; nạp + lưu lại không
# bảo đảm bỏ hết quan hệ ngoài. Điều cấm: quan hệ TargetMode="External" hoặc đích là URL / UNC (ảnh, video, mẫu tải từ ngoài khi mở),
# macro (vbaProject, kiểu macroEnabled, đuôi .pptm…), đối tượng nhúng / ActiveX (oleObject, package, control, mọi .bin), DTD / ENTITY trong XML.
_DUOI_MACRO = frozenset({".pptm", ".potm", ".ppsm", ".ppam", ".docm", ".dotm", ".xltm", ".xlam"})
_TRAN_PPTX, _TRAN_PPTX_GIAI, _TRAN_PPTX_MUC = 50 * 1024 * 1024, 300 * 1024 * 1024, 3000
_PPTX_THU_MUC_CAM = ("ppt/embeddings/", "ppt/activex/", "ppt/ctrlprops/")
_PPTX_MAY_IN, _TRAN_MAY_IN = _re.compile(r"^ppt/printersettings/printersettings\d{1,3}\.bin$"), 64 * 1024
_PPTX_KIEU_CAM = ("macroenabled", "vbaproject", "oleobject", "activex", "ms-office.package")
_REL_KIEU_CAM = ("/oleobject", "/package", "/control", "/activexcontrolbinary", "/vbaproject", "/attachedtemplate", "/externallink")
_REL_DICH_NGOAI = _re.compile(r"^\s*([a-z][a-z0-9+.-]*:|//|\\\\)", _re.IGNORECASE)   # http:, file:, mhtml:, //máy, \\máy
_TRAN_ANH, _TRAN_DIEM_ANH, _TRAN_CANH_ANH = 10 * 1024 * 1024, 40_000_000, 12_000
_MAGIC_ANH = {".png": (b"\x89PNG\r\n\x1a\n", "PNG"), ".jpg": (b"\xff\xd8\xff", "JPEG"), ".jpeg": (b"\xff\xd8\xff", "JPEG")}


def _lien_ket_bam(r) -> bool:
    """[va_docx_goi, D 10/11 — MỘT luật cho docx + pptx] Quan hệ ra ngoài DUY NHẤT được nhận: liên kết BẤM ĐƯỢC (Type kết thúc "/hyperlink") trỏ
    http:// / https:// / mailto: — chỉ mở khi người bấm, Word / PowerPoint không tự tải. Mọi kiểu ra ngoài khác (ảnh liên kết, template, oleObject…)
    và scheme khác (file:, \\\\máy, //máy, mhtml:, javascript:) vẫn từ chối."""
    dich = (r.get("Target") or "").strip().lower()
    return (r.get("Type") or "").lower().endswith("/hyperlink") and dich.startswith(("http://", "https://", "mailto:"))


def _pptx_an_toan(b: bytes) -> bytes:
    """Trả nguyên `b` nếu đạt; không đạt ⇒ ValueError có lý do (file không lên)."""
    def tu_choi(ly_do: str):
        raise ValueError(f"hộp phiên (chay_thu): file pptx ra bị từ chối — {ly_do}")

    if len(b) > _TRAN_PPTX:
        tu_choi(f"quá {_TRAN_PPTX} byte")
    try:
        z = zipfile.ZipFile(BytesIO(b))
        muc = z.infolist()
    except Exception as exc:  # noqa: BLE001
        tu_choi(f"không phải gói zip đọc được ({type(exc).__name__})")
    if len(muc) > _TRAN_PPTX_MUC or sum(i.file_size for i in muc) > _TRAN_PPTX_GIAI:
        tu_choi("quá nhiều mục / quá lớn khi giải nén")
    ten = [i.filename for i in muc]
    thap = [t.lower() for t in ten]
    goc_ten = dict(zip(thap, ten))
    if len(goc_ten) != len(ten):
        tu_choi("hai mục trùng tên (khác hoa / thường)")
    if "ppt/presentation.xml" not in thap or "[content_types].xml" not in thap:
        tu_choi("thiếu ppt/presentation.xml hoặc [Content_Types].xml")
    for t in thap:
        if t.startswith("/") or "\\" in t or ".." in t.split("/"):
            tu_choi(f"tên mục lạ {t!r}")
        if _PPTX_MAY_IN.match(t):   # khuôn mặc định của python-pptx luôn có (thử S1 lần 2, 10/11) — thông số máy in, không chạy được; vẫn có trần cỡ
            if z.getinfo(goc_ten[t]).file_size > _TRAN_MAY_IN:
                tu_choi(f"{t!r} quá {_TRAN_MAY_IN} byte")
            continue
        if t.endswith(".bin") or t.startswith(_PPTX_THU_MUC_CAM) or "vbaproject" in t:
            tu_choi(f"có phần nhúng / macro {t!r}")
    from defusedxml.ElementTree import fromstring   # có trong image agentic-backend (đã kiểm 10/11); chặn DTD / thực thể

    for goc, t in zip(ten, thap):
        if not (t.endswith(".rels") or t == "[content_types].xml"):
            continue
        x = z.read(goc)
        if b"<!DOCTYPE" in x.upper() or b"<!ENTITY" in x.upper():
            tu_choi(f"{goc}: có DTD / ENTITY")
        if t == "[content_types].xml":
            if any(k in x.decode("utf-8", "replace").lower() for k in _PPTX_KIEU_CAM):
                tu_choi("[Content_Types].xml khai kiểu macro / nhúng")
            continue
        try:
            cay = fromstring(x)
        except Exception as exc:  # noqa: BLE001
            tu_choi(f"{goc}: XML hỏng ({type(exc).__name__})")
        for r in cay.iter():
            if not r.tag.endswith("Relationship"):
                continue
            if ((r.get("TargetMode") or "").lower() == "external" or _REL_DICH_NGOAI.match(r.get("Target") or "")) and not _lien_ket_bam(r):
                tu_choi(f"{goc}: quan hệ trỏ ra ngoài ({(r.get('Target') or '')[:80]!r})")
            if (r.get("Type") or "").lower().endswith(_REL_KIEU_CAM):
                tu_choi(f"{goc}: quan hệ kiểu cấm ({(r.get('Type') or '').rsplit('/', 1)[-1]})")
    return b


# [ghi chú] _xlsx_an_toan (C14) chỉ xét công thức gọi ra
# ngoài + hyperlink + vài phần (_PHAN_NGUY_HIEM) — KHÔNG xét quan hệ TargetMode="External" khác (ảnh liên kết ở xl/drawings, ...), OLE (xl/embeddings),
# ActiveX (xl/activeX, xl/ctrlProps), kiểu macroEnabled ⇒ file AI tạo có các thứ đó đi nguyên lên kho. Kiểm GÓI sau khi làm sạch (hyperlink / externalLinks
# đã bị gỡ ở bước đó), cùng luật _pptx_an_toan; còn ⇒ ValueError ⇒ file KHÔNG lên. Chỉ kiểm, không sửa (file sạch giữ nguyên byte).
_XLSX_THU_MUC_CAM = ("xl/embeddings/", "xl/activex/", "xl/ctrlprops/")
_XLSX_MAY_IN = _re.compile(r"^xl/printersettings/printersettings\d{1,3}\.bin$")


def _xlsx_kiem_goi(b: bytes) -> bytes:
    def tu_choi(ly_do: str):
        raise ValueError(f"hộp phiên (chay_thu): file xlsx ra bị từ chối — {ly_do}")

    if len(b) > _TRAN_PPTX:
        tu_choi(f"quá {_TRAN_PPTX} byte")
    try:
        z = zipfile.ZipFile(BytesIO(b))
        muc = z.infolist()
    except Exception as exc:  # noqa: BLE001
        tu_choi(f"không phải gói zip đọc được ({type(exc).__name__})")
    if len(muc) > _TRAN_PPTX_MUC or sum(i.file_size for i in muc) > _TRAN_PPTX_GIAI:
        tu_choi("quá nhiều mục / quá lớn khi giải nén")
    ten = [i.filename for i in muc]
    thap = [t.lower() for t in ten]
    goc_ten = dict(zip(thap, ten))
    if len(goc_ten) != len(ten):
        tu_choi("hai mục trùng tên (khác hoa / thường)")
    if "xl/workbook.xml" not in thap or "[content_types].xml" not in thap:
        tu_choi("thiếu xl/workbook.xml hoặc [Content_Types].xml")
    for t in thap:
        if t.startswith("/") or "\\" in t or ".." in t.split("/"):
            tu_choi(f"tên mục lạ {t!r}")
        if _XLSX_MAY_IN.match(t):   # Excel thật có thể ghi thông số máy in — dữ liệu, không chạy được; có trần cỡ
            if z.getinfo(goc_ten[t]).file_size > _TRAN_MAY_IN:
                tu_choi(f"{t!r} quá {_TRAN_MAY_IN} byte")
            continue
        if t.endswith(".bin") or t.startswith(_XLSX_THU_MUC_CAM) or "vbaproject" in t:
            tu_choi(f"có phần nhúng / macro {t!r}")
    from defusedxml.ElementTree import fromstring   # chặn DTD / thực thể

    for goc, t in zip(ten, thap):
        if not (t.endswith(".rels") or t == "[content_types].xml"):
            continue
        x = z.read(goc)
        if b"<!DOCTYPE" in x.upper() or b"<!ENTITY" in x.upper():
            tu_choi(f"{goc}: có DTD / ENTITY")
        try:
            cay = fromstring(x)
        except Exception as exc:  # noqa: BLE001
            tu_choi(f"{goc}: XML hỏng ({type(exc).__name__})")
        if t == "[content_types].xml":   # D m2: đọc thuộc tính ContentType, không dò chuỗi thô
            if any(k in (e.get("ContentType") or "").lower() for e in cay.iter() for k in _PPTX_KIEU_CAM):
                tu_choi("[Content_Types].xml khai kiểu macro / nhúng")
            continue
        for r in cay.iter():
            if not r.tag.endswith("Relationship"):
                continue
            if (r.get("TargetMode") or "").lower() == "external" or _REL_DICH_NGOAI.match(r.get("Target") or ""):
                tu_choi(f"{goc}: quan hệ trỏ ra ngoài ({(r.get('Target') or '')[:80]!r})")
            if (r.get("Type") or "").lower().endswith(_REL_KIEU_CAM):
                tu_choi(f"{goc}: quan hệ kiểu cấm ({(r.get('Type') or '').rsplit('/', 1)[-1]})")
    return b


def _anh_an_toan(e: str, b: bytes) -> bytes:
    """PNG / JPEG thật: đúng chữ ký đầu file, Pillow đọc + giải mã hết, trần cỡ file / cạnh / số điểm ảnh. Không đạt ⇒ ValueError."""
    def tu_choi(ly_do: str):
        raise ValueError(f"hộp phiên (chay_thu): ảnh ra ({e}) bị từ chối — {ly_do}")

    magic, dang = _MAGIC_ANH[e]
    if len(b) > _TRAN_ANH:
        tu_choi(f"quá {_TRAN_ANH} byte")
    if not b.startswith(magic):
        tu_choi("chữ ký đầu file không khớp đuôi")
    from PIL import Image

    try:
        with Image.open(BytesIO(b)) as im:   # chỉ đọc phần đầu ⇒ kiểm cỡ TRƯỚC khi giải mã
            w, h = im.size
            if im.format != dang:
                tu_choi(f"Pillow nhận dạng {im.format}, không phải {dang}")
            if w > _TRAN_CANH_ANH or h > _TRAN_CANH_ANH or w * h > _TRAN_DIEM_ANH:
                tu_choi(f"kích thước {w}×{h} quá trần")
            im.verify()
        with Image.open(BytesIO(b)) as im:   # verify() làm hỏng đối tượng ⇒ mở lại để giải mã hết (bắt file cụt)
            im.load()
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, ValueError) and str(exc).startswith("hộp phiên"):
            raise
        tu_choi(f"Pillow không đọc được ({type(exc).__name__})")
    return b


# [ghi chú] .docx do code AI tạo trong hộp đi NGUYÊN ra kho (không như pptx / ảnh / xlsx). Cùng luật
# _pptx_an_toan (không sửa hàm đó) + riêng Word: altChunk (aFChunk — nhúng tài liệu khác), subDocument, frame; mã trường gọi ra ngoài trong XML
# nội dung (DDE / DDEAUTO / INCLUDETEXT / INCLUDEPICTURE / LINK / IMPORT — chạy / tải khi mở, không cần quan hệ). Chỉ kiểm, không sửa.
_DOCX_THU_MUC_CAM = ("word/embeddings/", "word/activex/")
_DOCX_REL_CAM = _REL_KIEU_CAM + ("/afchunk", "/subdocument", "/frame")
_DOCX_TRUONG_CAM = _re.compile(rb"\b(DDEAUTO|DDE|INCLUDETEXT|INCLUDEPICTURE|LINK|IMPORT)\b", _re.IGNORECASE)


def _docx_an_toan(b: bytes) -> bytes:
    """Trả nguyên `b` nếu đạt; không đạt ⇒ ValueError có lý do (file không lên)."""
    def tu_choi(ly_do: str):
        raise ValueError(f"hộp phiên (chay_thu): file docx ra bị từ chối — {ly_do}")

    if len(b) > _TRAN_PPTX:
        tu_choi(f"quá {_TRAN_PPTX} byte")
    try:
        z = zipfile.ZipFile(BytesIO(b))
        muc = z.infolist()
    except Exception as exc:  # noqa: BLE001
        tu_choi(f"không phải gói zip đọc được ({type(exc).__name__})")
    if len(muc) > _TRAN_PPTX_MUC or sum(i.file_size for i in muc) > _TRAN_PPTX_GIAI:
        tu_choi("quá nhiều mục / quá lớn khi giải nén")
    ten = [i.filename for i in muc]
    thap = [t.lower() for t in ten]
    if len(set(thap)) != len(ten):
        tu_choi("hai mục trùng tên (khác hoa / thường)")
    if "word/document.xml" not in thap or "[content_types].xml" not in thap:
        tu_choi("thiếu word/document.xml hoặc [Content_Types].xml")
    for t in thap:
        if t.startswith("/") or "\\" in t or ".." in t.split("/"):
            tu_choi(f"tên mục lạ {t!r}")
        if t.endswith(".bin") or t.startswith(_DOCX_THU_MUC_CAM) or "vbaproject" in t:
            tu_choi(f"có phần nhúng / macro {t!r}")
    from defusedxml.ElementTree import fromstring   # chặn DTD / thực thể

    for goc, t in zip(ten, thap):
        if not t.endswith((".rels", ".xml")):
            continue
        x = z.read(goc)
        if b"<!DOCTYPE" in x.upper() or b"<!ENTITY" in x.upper():
            tu_choi(f"{goc}: có DTD / ENTITY")
        if t == "[content_types].xml":
            if any(k in x.decode("utf-8", "replace").lower() for k in _PPTX_KIEU_CAM):
                tu_choi("[Content_Types].xml khai kiểu macro / nhúng")
            continue
        if t.startswith("word/") and not t.endswith(".rels"):
            try:
                cay = fromstring(x)
            except Exception as exc:  # noqa: BLE001
                tu_choi(f"{goc}: XML hỏng ({type(exc).__name__})")
            for e in cay.iter():   # mã trường: <w:instrText>…</w:instrText> và <w:fldSimple w:instr="…">
                chu = (e.text or "") if e.tag.endswith("}instrText") else next((v for k, v in e.attrib.items() if k.endswith("}instr")), "")
                if chu and _DOCX_TRUONG_CAM.search(chu.encode("utf-8")):
                    tu_choi(f"{goc}: mã trường gọi ra ngoài ({chu.strip()[:40]!r})")
            continue
        if not t.endswith(".rels"):
            continue
        try:
            cay = fromstring(x)
        except Exception as exc:  # noqa: BLE001
            tu_choi(f"{goc}: XML hỏng ({type(exc).__name__})")
        for r in cay.iter():
            if not r.tag.endswith("Relationship"):
                continue
            if ((r.get("TargetMode") or "").lower() == "external" or _REL_DICH_NGOAI.match(r.get("Target") or "")) and not _lien_ket_bam(r):
                tu_choi(f"{goc}: quan hệ trỏ ra ngoài ({(r.get('Target') or '')[:80]!r})")
            if (r.get("Type") or "").lower().endswith(_DOCX_REL_CAM):
                tu_choi(f"{goc}: quan hệ kiểu cấm ({(r.get('Type') or '').rsplit('/', 1)[-1]})")
    return b


def _lam_sach_file_ra(ext: str, content: bytes, skill: str | None = None) -> bytes:
    e = (ext or "").lower()
    if e in _DUOI_MACRO:   # [ghi chú] đuôi có macro ⇒ không lên
        raise ValueError(f"hộp phiên (chay_thu): không nhận file ra có macro ({ext})")
    if e == ".pptx":   # [ghi chú]
        return _pptx_an_toan(content)
    if e == ".docx":   # [ghi chú]
        return _docx_an_toan(content)
    if e in _MAGIC_ANH:   # [ghi chú]
        return _anh_an_toan(e, content)
    if e in _DUOI_RA_KHONG_NHAN:
        if skill not in _SKILL_HTML:
            raise ValueError(f"hộp phiên (chay_thu): không nhận file ra dạng trang web ({ext})")
        return _html_an_toan(content)
    if e == ".csv":
        from nodes.agent.native_tools import _csv_an_toan

        return _csv_an_toan(content)
    if e in (".xlsx", ".xlsm"):
        moi, bo = _xlsx_an_toan(content)
        if bo:
            logger.warning("[chay_thu V1] file xlsx ra: đã bỏ %d chỗ gọi ra ngoài / liên kết", bo)
        return _xlsx_kiem_goi(moi)   # [ghi chú] còn quan hệ ngoài / OLE / ActiveX / macro sau làm sạch ⇒ từ chối
    return content


_GOP_SKILL_TU = 20   # skill > 20 file ⇒ 1 gói zip + 1 phiếu giải (D3_NETWORK_GRAPH_CACH_DUNG_D.md mục 2)
_GIAI_SKILL = r"""import os, zipfile
p, d = @GOI@, @DICH@
with zipfile.ZipFile(p) as z:
    for i in z.infolist():
        n = i.filename
        if n.startswith("/") or chr(92) in n or any(x in ("", ".", "..") for x in n.split("/")):
            raise SystemExit("ten la: " + repr(n))
    z.extractall(d)
    so = len(z.infolist())
os.remove(p)
print("GIAI_XONG", so)
"""
_TRAN_SKILL_FILE, _TRAN_SKILL_TONG, _TRAN_SKILL_SO = 2_000_000, 10_000_000, 200   # skill trong repo: data-analysis 48 KB, xlsx 2 file
TEMPLATE_SANDBOX_DIR = "/home/user/templates"
_TURN_MARKER = "/tmp/.capitol_turn_start"
_SANDBOX_TIMEOUT_SECONDS = 900  # E2B TTL for a turn; kept alive across the session

# The registry spec owns each model's thinking shape (``thinking_mode`` /
# ``thinking_display``). Opus 4.6 (+fast) is legacy budget-thinking there but
# runs adaptive here, with the 128K max-output ceiling.
_ADAPTIVE_CARVE_IN = frozenset(
    {
        AnthropicModels.CLAUDE_OPUS_4_6.value,
        AnthropicModels.CLAUDE_OPUS_4_6_FAST.value,
    }
)

# Ceiling on an honoured ``Retry-After``. A provider may name a wait longer
# than is worth holding the activity open for; past this we stop resuming and
# let Temporal's retry -- which applies its own, longer back-off -- take over.
_MAX_RETRY_AFTER_SECONDS = 60.0


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Seconds the provider asked us to wait, from a ``Retry-After`` header.

    ``None`` when there is no header or it cannot be parsed. Only the
    delta-seconds form is handled; Anthropic and Bedrock do not use the
    HTTP-date form on throttling responses.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        return max(0.0, float(headers.get("retry-after")))
    except (TypeError, ValueError):
        return None


def _nonempty_lines(stdout: str | None) -> list[str]:
    """Split ``find`` output into paths, dropping blank lines."""
    return [line for line in (stdout or "").splitlines() if line.strip()]


class E2BSkillsChatService:
    """Skills service (chat + one-shot) executing in a self-hosted E2B sandbox.

    Instantiated with the ``SkillServiceConfig`` built by each skill's
    ``config.build_skill_config()`` factory.
    """

    def __init__(
        self,
        config: SkillServiceConfig,
        build_input_issue_tools: Callable[[], list[Any]] | None = None,
        emit_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        # The driving node's input-issue tool builder. Taken as a callable
        # rather than the node itself so the service never reaches into node
        # internals, and so the tool is built per run rather than held: it
        # captures the node's session and run ids, which do not exist yet when
        # the node constructs this service. Optional for callers with no node.
        self._build_input_issue_tools = build_input_issue_tools
        # The driving node's ``emit_progress``; safe to bind early because it
        # reads the node's ids at call time.
        self._emit_progress = emit_progress
        self._sandbox: SandboxManager | None = None
        self._skill_prompt_section: str = ""
        self._http_client: httpx.AsyncClient | None = None
        # Respin count observed at the start of the current turn. The sandbox is
        # reused for the whole session and its counter is cumulative, so the
        # loss check below has to compare against this baseline rather than the
        # raw total (see ``_warn_if_outputs_may_be_lost``).
        self._respins_at_turn_start: int = 0
        # Tool-result offloader (ENG-4646 / ENG-5197): one per sandbox
        # lifetime, shared by every turn and every resume of the session, so
        # offload file names never collide across turns and an envelope in an
        # earlier turn's history keeps pointing at a file that still exists.
        # Built lazily by ``_build_tool_offload_capability``; reset with the
        # sandbox in ``cleanup()``.
        self._tool_offloader: ToolResultOffloader | None = None
        self._mo_hinh_ep: tuple[str, str | None] | None = None   # [va_hop_phien_claude] (tên Claude, mức) của lượt đang chạy; None = luna

    @property
    def config(self) -> SkillServiceConfig:
        return self._config

    @property
    def tool_result_offloads(self) -> list[dict[str, Any]]:
        """Offload events recorded this session, for ``NodeOutput.metadata``.

        Mirrors ``AgentBaseNode``'s ``tool_result_offloads`` metadata so a
        consumer node can surface the same observability. Empty when nothing
        was offloaded (or offloading is disabled).
        """
        if self._tool_offloader is None:
            return []
        return [record.to_metadata() for record in self._tool_offloader.records]

    # ── Session lifecycle ────────────────────────────────────────────────────

    async def _ensure_sandbox(self) -> SandboxManager:
        if self._sandbox is not None:
            return self._sandbox

        # Use the skill-specific template if configured; fall back to the
        # deployment default so local development (empty e2b_template) still works.
        template = self._config.e2b_template or settings.E2B_TEMPLATE or None

        _Mgr = SandboxManager
        if _hop_phien_bat():   # [chay_thu V1] hộp phiên của mình (V2 — C)
            from services.sandbox.hop_phien import HopPhienManager as _Mgr
        if _hop_phien_bat() and getattr(self, "_ct_e2b", False):
            # [ghi chú] khối được file nơi chạy đặt `e2b` (đã quyết + đã soát file ở _run) ⇒ máy ảo E2B của bộ mình (lớp _E2bLamFile:
            # khoá riêng, tự tắt, sổ máy, không biến môi trường của hộp) thay hộp phiên. Mọi rào khác của hộp phiên (mô hình, mcp_servers, file vào theo tổ chức, làm sạch +
            # khoá file ra) vẫn chạy vì cờ hộp phiên vẫn bật. Mã phiên / workflow / nút lấy từ chính khối (để sổ tiền gán máy cho lượt).
            from workflow.node.base.agent import sandbox_runtime as _sr
            khoi = getattr(self._emit_progress, "__self__", None)
            sandbox = _sr._E2bLamFile(
                timeout=_sr._e2b_ttl(), template=_sr._e2b_template(), node_type="skills", node_sub_type="e2b_skills",
                node_id=str(getattr(khoi, "node_id", "") or "") or None, workflow_id=getattr(khoi, "_workflowid", None) or None,
                envs={"INPUT_DIR": INPUT_DIR, "OUTPUT_DIR": OUTPUT_DIR, "WORK_DIR": WORK_DIR},
            )
            sandbox._ct_session = getattr(khoi, "_session_id", None)
        else:
            sandbox = _Mgr(
                timeout=_SANDBOX_TIMEOUT_SECONDS,
                template=template,
                node_type="skills",
                node_sub_type="e2b_skills",
                envs={
                    "INPUT_DIR": INPUT_DIR,
                    "OUTPUT_DIR": OUTPUT_DIR,
                    "WORK_DIR": WORK_DIR,
                },
            )
        await sandbox.create()
        try:
            self._skill_prompt_section = await self._load_skill(sandbox)
            # Replayed on respin for OUTPUT_DIR's sake. The node-side
            # collectors tolerate a missing output dir because they fall back
            # to scanning /home/user recursively; this service has no such net
            # -- ``_collect_output_files`` only ever looks inside OUTPUT_DIR.
            # So if a respin removes it and the agent does not happen to
            # ``makedirs`` before writing, the turn reports no files and says
            # nothing. The bundled skills do call ``makedirs``, but docx/xlsx/
            # pptx are told where to write in prose with no prescribed code, so
            # that is a guess about model behaviour rather than a guarantee.
            # One command removes the guess. INPUT_DIR rides along; the
            # manager's parent-directory pass would recreate it anyway.
            await sandbox.run_shell(
                f"mkdir -p {INPUT_DIR} {OUTPUT_DIR} {WORK_DIR}",
                timeout=10,
                replay_on_respin=True,
            )
            await sandbox.start_keepalive()
        except Exception:
            await sandbox.cleanup()
            raise
        self._sandbox = sandbox
        return sandbox

    async def cleanup(self) -> None:
        """Tear down the session sandbox and HTTP client. Safe to call twice."""
        if self._sandbox is not None:
            await self._sandbox.cleanup()
            self._sandbox = None
        # The offloader's files and sequence live in the sandbox that was just
        # destroyed; a node instance that executes again gets a fresh one.
        self._tool_offloader = None
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def _build_tool_offload_capability(
        self, resolved_model_id: str
    ) -> ToolResultOffloadCapability | None:
        """Build the tool-result offload capability for one agent run.

        The skill nodes run outside the ENG-4437 ``AgentBaseNode`` hierarchy,
        so they never received the ENG-4646 size guard that every other agent
        node gets: an MCP tool returning a multi-MB payload (EY US, Data
        Analysis Agent, ``query_structured_dataset`` returning 14.5 MB of rows)
        went straight into the model request and failed the node with
        ``prompt is too long`` -- a failure compaction structurally cannot
        recover from, because the fresh tool return sits in the protected
        recent-turns tail. This is the same capability, wired to this
        service's own sandbox (ENG-5197).

        Differences from the ``AgentBaseNode`` wiring, each deliberate:

        * ``ensure_sandbox`` is ``_ensure_sandbox``: the session sandbox
          already exists by the time any tool runs, so no on-demand boot
          ladder is needed; ``upload_file`` records the offloaded content in
          the manager's respin replay cache, so a mid-turn respin restores the
          files.
        * No gated exploration toolset and an empty tool-name prefix: every
          run here registers the ungated standard sandbox tools
          (``make_sandbox_tools(sandbox)``), so the envelope's access guidance
          must name ``run_code`` / ``read_file`` -- the same branch the base
          takes for files-connected nodes. A second, prefixed registration
          would only add duplicate tools.
        * No citation harvest: this path never minted citations from tool
          results, so there is nothing for the offloader to preserve.

        Returns ``None`` only when the feature is disabled via settings, in
        which case tool results flow exactly as before.
        """
        if not settings.TOOL_RESULT_OFFLOAD_ENABLED:
            return None
        if self._tool_offloader is None:
            budget = get_context_budget(resolved_model_id)
            self._tool_offloader = ToolResultOffloader(
                ensure_sandbox=self._ensure_sandbox,
                emit_progress=self._emit_progress,
                safe_input_window=budget.safe_input_window if budget else None,
                exploration_tool_prefix="",
            )
        return ToolResultOffloadCapability(
            offloader=self._tool_offloader,
            exploration_toolset=None,
        )

    # ── Public surfaces (mirror the native services) ─────────────────────────

    async def stream_generate(
        self,
        prompt: str,
        system_prompt: str,
        files: list[NodeFile] | None = None,
        model: str | None = None,
        mcp_servers: list[Any] | None = None,
        orgid: str | None = None,
        user_token: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """One-shot skill execution, yielding base StreamEvent objects."""
        async for event in self._run(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model or self._config.default_model,
            files=files,
            orgid=orgid,
            message_history=None,
            mcp_servers=mcp_servers,
        ):
            yield event

    async def stream_analyze(
        self,
        prompt: str,
        system_prompt: str,
        files: list[NodeFile] | None = None,
        model: str | None = None,
        mcp_servers: list[Any] | None = None,
        orgid: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Alias for stream_generate (the data-analysis node's entry point)."""
        async for event in self.stream_generate(
            prompt=prompt,
            system_prompt=system_prompt,
            files=files,
            model=model,
            mcp_servers=mcp_servers,
            orgid=orgid,
        ):
            yield event

    async def stream_chat(
        self,
        prompt: str,
        system_prompt: str,
        model: str | None = None,
        container_id: str | None = None,
        files: list[NodeFile] | None = None,
        message_history: list[Any] | None = None,
        template_file_id: str | None = None,
        orgid: str | None = None,
        user_token: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatStreamEvent, None]:
        """Multi-turn chat, wrapping the engine's StreamEvents as ChatStreamEvents.

        ``container_id`` is accepted for interface parity but not needed to locate
        the session — the service instance owns the sandbox. ``message_history``
        seeds the conversation so edits build on prior turns.
        """
        user_message = prompt
        if template_file_id:
            user_message = (
                f"{prompt}\n\n## Template\n"
                f"A style-reference template is available at {template_file_id} in "
                "the sandbox. Copy it before use, and match only its styling and "
                "theming (fonts, colours, layout) — not its content. Never truncate "
                "text with ellipses (...); summarise in natural language instead."
            )
        async for event in self._run(
            prompt=user_message,
            system_prompt=system_prompt,
            model=model or self._config.default_model,
            files=files,
            orgid=orgid,
            message_history=message_history,
            mcp_servers=None,
        ):
            yield ChatStreamEvent(
                type=ChatEventType(event.type.value),
                content=event.content,
                tool_name=event.tool_name,
                file_ids=event.file_ids,
                usage=event.usage,
                stop_reason=event.stop_reason,
                container_id=event.container_id,
                assistant_content=event.assistant_content,
                file_name=event.file_name,
            )

    def _input_issue_tools(self) -> list[Any]:
        """Return the driving node's ``report_input_issue`` tool, if any.

        Called per run rather than at construction: the tool captures the
        node's session and run ids, and a node builds this service before
        either is populated. Building early would bind the tool to an empty
        session and silently drop every finding it reported.

        Fails open on ``ImportError`` only. The builder defers its import, so
        a module-resolution problem can appear at run time without failing at
        start-up, and losing the warning must not cost the user the
        deliverable the run was for. Every other exception is a programming
        error in the builder, which CI catches: swallowing it would leave the
        model holding the doctrine with no tool, which is the exact defect
        this reporting path exists to fix.

        Returns:
            The node's input-issue tools, or an empty list when this service
            was built without a tool builder or the builder could not import.

        Raises:
            Exception: Anything the builder raises other than ``ImportError``.
        """
        if self._build_input_issue_tools is None:
            return []
        try:
            return list(self._build_input_issue_tools())
        except ImportError as exc:
            logger.warning("could not import the input-issue tool: %s", exc)
            capture_exception(exc)
            return []

    # ── Engine: one skill turn in the sandbox -> base StreamEvent ─────────────

    async def _run(
        self,
        prompt: str,
        system_prompt: str,
        model: str,
        files: list[NodeFile] | None,
        orgid: str | None,
        message_history: list[Any] | None,
        mcp_servers: list[Any] | None,
    ) -> AsyncGenerator[StreamEvent, None]:
        if _hop_phien_bat():   # [chay_thu V1] kiểm TRƯỚC khi mở hộp / gọi AI
            if mcp_servers:
                raise ValueError("hộp phiên (chay_thu): khối có mcp_servers / khối công cụ đi chung với chạy code — từ chối (R12 trong khối)")
            # [va_hop_phien_claude] file mô hình khối quyết: có dòng ⇒ Claude qua cổng AI (bỏ qua ô model của nút, như khối kiểu Agent); không ⇒ luna như cũ
            loai_khoi, ep = _chay_thu_mo_hinh_hop_phien(self._emit_progress)
            nguon_mo_hinh = "file mô hình khối"
            if ep is None:   # file không có dòng cho khối này ⇒ theo Ô MODEL của khối: tên Claude 5.5 (đúng danh sách bỏ trần token) ở khối thuộc 6 loại ⇒ chạy Claude
                from workflow.node.base.agent.model_resolution import CHAY_THU_KHOI_HOP_PHIEN as _HP, CHAY_THU_MO_HINH_BO_TRAN_TOKEN as _CLAUDE
                if model in _CLAUDE and loai_khoi in _HP:
                    ep, nguon_mo_hinh = (model, None), "ô model của khối"
            if ep is None and model != "gpt-6-luna":
                raise ValueError(f"hộp phiên (chay_thu) chỉ chạy gpt-6-luna; khối đặt mô hình {model!r} — đặt ô model = gpt-6-luna")
            self._mo_hinh_ep = ep
            if self._emit_progress is not None:   # [CHỐNG LẶNG] mọi lượt có dòng tiến độ ghi mô hình THẬT + nguồn ⇒ đối chiếu được với sổ cổng
                if ep:
                    await self._emit_progress(f"Mô hình của khối: {ep[0]}{(' — mức suy luận ' + ep[1]) if ep[1] else ''} (nguồn: {nguon_mo_hinh}; ô model của khối đang ghi '{str(model)[:60]}'; loại khối {loai_khoi}; hộp phiên).")
                else:
                    await self._emit_progress(f"Mô hình của khối: {str(model)[:60]} (nguồn: ô model của khối; loại khối {loai_khoi or 'không lần ra'}; hộp phiên).")
            for node_file in files or []:   # [C13] kiểm cả lô TRƯỚC khi mở hộp — một file lệch ⇒ không file nào vào hộp
                await asyncio.to_thread(_kiem_file_vao, node_file, orgid)
            self._orgid_luot = orgid   # [C14] để khoá file ra mang mã tổ chức của lượt
            # [ghi chú] nơi chạy code của khối theo FILE nơi chạy. e2b ⇒ soát TỪNG file vào bằng đúng rào "file người dùng lên E2B"
            # của khối Chạy code (loại file, tên giống file khoá, dò khoá) TRƯỚC khi tạo máy ảo; một file không qua ⇒ dừng, không máy nào được tạo.
            # [ghi chú] khối PowerPoint gửi FILE MẪU (upload_template) TRƯỚC _run ⇒ máy đã có sẵn khi tới đây; nơi chạy khi đó đã quyết ở
            # upload_template (cùng hàm, cùng file nơi chạy). Soát file vào + dòng "Nơi chạy code" vẫn phải chạy ĐÚNG MỘT lần cho máy đó ⇒ cờ _ct_da_soat (gỡ khi máy đổi).
            if self._sandbox is None or not getattr(self, "_ct_da_soat", False):
                from workflow.node.base.agent import sandbox_runtime as _sr
                if self._sandbox is None:
                    self._ct_e2b = _sr.e2b_cho_khoi_lam_file(loai_khoi)
                self._ct_e2b = bool(getattr(self, "_ct_e2b", False))
                self._ct_da_soat = True
                if self._ct_e2b:
                    for node_file in files or []:
                        _sr._e2b_kiem_noi_dung(os.path.basename(node_file.name), await asyncio.to_thread(node_file.download_file_content))
                if self._emit_progress is not None:
                    await self._emit_progress("Nơi chạy code của khối: " + ("máy ảo E2B (dịch vụ ngoài)." if self._ct_e2b else "hộp kín trên máy."))
        sandbox = await self._ensure_sandbox()
        # ``replay_on_respin`` re-stamps the marker on a replacement sandbox.
        # Without it a mid-turn respin left ``_collect_output_files``' `find
        # -newer` pointing at a file that no longer existed, so the turn
        # reported zero outputs while its files sat in OUTPUT_DIR (ENG-3922).
        # Re-stamping at respin time is also semantically right: everything
        # that survives the respin is written after it.
        await sandbox.run_shell(
            f"touch {_TURN_MARKER}", timeout=10, replay_on_respin=True
        )

        if not message_history:
            for node_file in files or []:
                yield StreamEvent(
                    type=EventType.FILE_UPLOAD_START, file_name=node_file.name
                )
                safe_name = os.path.basename(node_file.name)
                # Off the event loop: the read is a blocking S3 call that may
                # now wait out a key-store proxy blip for tens of seconds.
                content = await asyncio.to_thread(node_file.download_file_content)
                await sandbox.upload_file(f"{INPUT_DIR}/{safe_name}", content)
                yield StreamEvent(
                    type=EventType.FILE_UPLOAD_COMPLETE, file_name=node_file.name
                )

        # Baseline for the end-of-turn loss check. Only a respin from here on
        # can have destroyed output this turn produced: input uploads above are
        # replayed onto a replacement sandbox, so a respin before this point
        # costs nothing.
        self._respins_at_turn_start = sandbox.respin_count

        ep = self._mo_hinh_ep if _hop_phien_bat() else None   # [va_hop_phien_claude] (tên Claude, mức) lấy ở đầu hàm; None = luna / gốc
        from workflow.node.base.agent.model_resolution import CHAY_THU_MO_HINH_BO_TRAN_TOKEN as _BO_TRAN
        bo_tran_token = bool(ep) and ep[0] in _BO_TRAN   # Claude 5.5 qua Bedrock ⇒ không trần tổng token của lượt (giữ rào số lần gọi)
        resolved_model_id, _ = resolve_deprecated_model(ep[0] if ep else model)   # cận ngữ cảnh của bộ cất kết quả công cụ theo mô hình THẬT
        compaction = None if _hop_phien_bat() else build_capability_for_model(   # [chay_thu V1] nén đòi Claude — bỏ
            resolved_model_id,
            on_compaction=progress_notifier(self._emit_progress),
            orgid=orgid or "",
        )
        capabilities: list[Any] = [compaction] if compaction is not None else []
        # Tool-result offloading rides alongside compaction, in the same
        # order the ``AgentBaseNode`` pipeline uses (ENG-4646 / ENG-5197).
        offload = self._build_tool_offload_capability(resolved_model_id)
        if offload is not None:
            capabilities.append(offload)
        # Basemap key note (services.sandbox.basemaps): the d3-data-viz skill
        # offers tile-map chart types and this sandbox carries the key like
        # every other. Placed ahead of the execution guidance so that stays
        # the closing section of the prompt.
        basemaps_note = build_basemaps_note()
        basemaps_section = f"\n\n{basemaps_note}\n" if basemaps_note else ""
        agent = Agent(
            model=await self._build_model(model, orgid, ep),
            system_prompt=(
                f"{PLATFORM_SAFETY_GUARDRAILS}\n\n"
                f"{system_prompt}{self._skill_prompt_section}"
                f"{basemaps_section}{SANDBOX_EXECUTION_GUIDANCE}"
            ),
            tools=make_sandbox_tools(sandbox, include_view_image=True)
            + self._input_issue_tools(),
            toolsets=list(mcp_servers) if mcp_servers else None,
            retries=3,
            capabilities=capabilities,
        )

        result_all_messages: list[Any] | None = None
        stop_reason: str | None = None
        captured_exc: Exception | None = None

        # Resume state for the retry loop below. ``pending_prompt`` is sent only
        # on the first attempt: on a resume the prompt is already the first
        # message in ``history``, and replaying it would duplicate the user turn.
        pending_prompt: str | None = prompt
        history: list[Any] | None = list(message_history) if message_history else None
        max_attempts = max(1, settings.E2B_SKILLS_STREAM_MAX_ATTEMPTS)
        # Each resume starts a fresh pydantic-ai run with its own request
        # counter, so the runaway-loop guard has to be carried across attempts
        # by hand -- otherwise a stalling turn would get the full budget again
        # every time.
        requests_used = 0
        _dem_cc: dict[str, int] = {}   # [ghi chú] cộng qua mọi lần nối lại
        tokens_in_used = 0    # [ghi chú] như requests_used: mang trần token qua các lần nối lại
        tokens_out_used = 0

        for attempt in range(max_attempts):
            run = None
            try:
                async with agent.iter(
                    pending_prompt,
                    message_history=history,
                    usage_limits=_gioi_han_luot(requests_used, tokens_in_used, tokens_out_used, bo_tran_token=bo_tran_token),   # [va_tran_tai_lieu]
                ) as run:
                    async for node in run:
                        if Agent.is_model_request_node(node):
                            async for event in self._stream_model_request(node, run):
                                yield event
                        elif Agent.is_call_tools_node(node):
                            async for event in self._stream_tool_calls(node, run):
                                if event.type == EventType.TOOL_USE:   # [ghi chú] đếm read_file / run_code của lượt
                                    _dem_cc[event.tool_name] = _dem_cc.get(event.tool_name, 0) + 1
                                yield event
                    result = run.result
                    if result is not None:
                        result_all_messages = result.all_messages()
                        for msg in reversed(result_all_messages):
                            if isinstance(msg, ModelResponse):
                                stop_reason = (msg.provider_details or {}).get(
                                    "finish_reason"
                                )
                                break

                        # ``provider`` must be the model that served the run:
                        # the configured id carries no Bedrock prefix, so it
                        # mislabels that transport as Anthropic.
                        log_cache_usage(
                            result.usage,
                            node_type=f"skills:{self.config.skill_name}",
                            provider=resolve_executor_model(result) or (ep[0] if ep else model),
                        )
                captured_exc = None
                break
            except Exception as e:  # noqa: BLE001 - surfaced as ERROR then re-raised
                captured_exc = e
                attempts_left = max_attempts - attempt - 1
                # Resume instead of restarting the node, but only when the
                # salvaged history ends with the ModelRequest that was in
                # flight: re-issuing a model call is idempotent, and pydantic-ai
                # pops that trailing request and re-sends it, so every tool call
                # already completed this turn is preserved.
                #
                # A stall inside ``_stream_tool_calls`` instead leaves a
                # trailing ModelResponse carrying tool calls, because results
                # are only appended once every tool in the response finishes.
                # Resuming from that re-enters CallToolsNode and re-runs the
                # whole batch -- including tools that already succeeded, which
                # would duplicate their sandbox side effects. Not resumable.
                #
                # An empty list means ``agent.iter`` never started, so there is
                # nothing to resume from either.
                salvaged = list(run.all_messages()) if run is not None else []
                resumable = bool(salvaged) and isinstance(salvaged[-1], ModelRequest)
                # A 429 names its own wait. Honouring it is the whole point, but
                # a hint longer than the ceiling is not worth holding the
                # activity open for -- and resuming sooner than asked would only
                # deepen the throttle -- so give up and let Temporal's retry,
                # with its own longer back-off, own the wait.
                retry_after = _retry_after_seconds(e)
                wait_exceeds_ceiling = (
                    retry_after is not None and retry_after > _MAX_RETRY_AFTER_SECONDS
                )
                if (
                    attempts_left <= 0
                    or not resumable
                    or wait_exceeds_ceiling
                    or not is_anthropic_retryable(e, TRANSIENT_TRANSPORT_ERRORS)
                ):
                    logger.error(
                        f"E2B skills agent run failed: {describe_exception(e)}",
                        exc_info=True,
                    )
                    stop_reason = "stream_error"
                    yield StreamEvent(
                        type=EventType.ERROR, content=describe_exception(e)
                    )
                    break

                history = salvaged
                pending_prompt = None
                requests_used += run.usage.requests
                tokens_in_used += run.usage.input_tokens
                tokens_out_used += run.usage.output_tokens
                delay = settings.E2B_SKILLS_STREAM_RETRY_BASE_DELAY_SECONDS * (
                    2**attempt
                )
                if retry_after is not None:
                    # Never resume before the server said to. No clamp needed:
                    # anything above the ceiling already took the branch above.
                    delay = max(delay, retry_after)
                logger.warning(
                    "E2B skills stream stalled (%s); resuming from %d message(s), "
                    "attempt %d/%d in %.1fs",
                    describe_exception(e),
                    len(history),
                    attempt + 2,
                    max_attempts,
                    delay,
                )
                yield StreamEvent(
                    type=EventType.PROGRESS,
                    content=(
                        "Connection to the model stalled — resuming "
                        f"({attempts_left} attempt(s) remaining)..."
                    ),
                )
                await asyncio.sleep(delay)

        output_file_ids: list[str] = []
        if captured_exc is None:
            output_file_ids = await self._collect_output_files()
            # [ghi chú] khối nhận file
            # mà 0 lần read_file (+ 0 run_code, hoặc mọi file là chữ) ⇒ log cảnh báo + self._chan_loi_mem_doc_file. Lỗi khi xét ⇒ bỏ qua.
            self._chan_loi_mem_doc_file = []
            try:
                from nodes.agent import kiem_loi_mem as _KL
                if files and _KL.che_do()[0] != "tat":
                    self._chan_loi_mem_doc_file = _KL.kiem_doc_file_tai_lieu(
                        _dem_cc.get("read_file", 0), _dem_cc.get("run_code", 0), [getattr(f, "name", "") or "" for f in files])
                    if self._chan_loi_mem_doc_file:
                        logger.warning("skills:%s CHAN_LOI_MEM doc_file=%s read_file=%s run_code=%s", self._config.skill_name,
                                       ",".join(self._chan_loi_mem_doc_file), _dem_cc.get("read_file", 0), _dem_cc.get("run_code", 0))
            except Exception:  # noqa: BLE001
                self._chan_loi_mem_doc_file = []

        yield StreamEvent(
            type=EventType.MESSAGE_COMPLETE,
            file_ids=output_file_ids,
            stop_reason=stop_reason or "end_turn",
            container_id=self._sandbox.sandbox.sandbox_id if self._sandbox else None,
            assistant_content=result_all_messages,
        )

        if captured_exc is not None:
            # Re-raise so consumer nodes mark themselves failed, but carry a
            # message built from the exception TYPE. httpx transport errors
            # stringify to "", and every node interpolates `str(exc)` into
            # "<node> failed: {}" -- which produced a blank error that the
            # Temporal classifier then read as a non-retryable internal bug
            # ("re-running won't fix it") for what is the textbook transient
            # failure. Naming the type restores the operator-facing message and
            # lets the classifier's existing transport markers match.
            raise RuntimeError(describe_exception(captured_exc)) from captured_exc

    async def _collect_output_files(self) -> list[str]:
        """Paths of files produced in OUTPUT_DIR this turn, or recovered scratch.

        Files modified after the turn marker, so a reused sandbox's earlier
        outputs aren't re-reported. When OUTPUT_DIR yields nothing at all,
        WORK_DIR is consulted as a last resort -- see
        ``_recover_from_work_dir``. ``services.sandbox.paths`` is the canonical
        statement of that rule.

        Returns:
            Absolute sandbox paths of this turn's files, or an empty list.
        """
        if self._sandbox is None:
            return []
        try:
            paths = await self._find_since_marker(OUTPUT_DIR)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to list {OUTPUT_DIR}: {exc}")
            return []

        if not paths:
            paths = await self._recover_from_work_dir()

        try:
            self._warn_if_outputs_may_be_lost(paths)
        except Exception:  # noqa: BLE001
            # Purely observational; it must never be the reason a turn's
            # collected files fail to reach the caller.
            logger.debug("Output-loss check failed", exc_info=True)
        return paths

    async def _find_since_marker(self, directory: str) -> list[str]:
        """Files under ``directory`` modified after this turn's marker.

        ``find`` exits non-zero when ``-newer``'s reference file is missing,
        and prints nothing on stdout. That is exactly what a mid-turn sandbox
        respin looks like, and reading only stdout turned it into a silent
        "this turn produced nothing" while the files sat in OUTPUT_DIR
        (ENG-3922). The marker is now replayed onto a respun sandbox, but this
        also stops trusting an errored ``find``: on a non-zero exit the
        directory is listed unfiltered. Re-reporting an earlier turn's file is
        recoverable -- consumers pick the newest match -- whereas dropping this
        turn's output is not.

        Shared by both probes so the respin handling cannot drift between them.
        A missing marker is precisely when a deliverable is most likely to be
        stranded, so the recovery probe needs this fallback at least as much as
        the output one does (ENG-4780).

        Args:
            directory: Absolute sandbox path to search.

        Returns:
            Absolute paths of matching files, or an empty list.
        """
        if self._sandbox is None:
            return []
        found = await self._sandbox.run_shell(
            f"find {directory} -type f -newer {_TURN_MARKER}",
            timeout=15,
            raise_on_error=False,
        )
        if found.exit_code == 0:
            return _nonempty_lines(found.stdout)

        logger.warning(
            "Turn-marker file listing failed (exit %s: %s); falling back to "
            "every file in %s",
            found.exit_code,
            (found.stderr or "").strip()[:200],
            directory,
        )
        every = await self._sandbox.run_shell(
            f"find {directory} -type f",
            timeout=15,
            raise_on_error=False,
        )
        return _nonempty_lines(every.stdout) if every.exit_code == 0 else []

    async def _recover_from_work_dir(self) -> list[str]:
        """Deliverables this turn left in WORK_DIR, when OUTPUT_DIR is empty.

        The prompt tells the model to work in WORK_DIR and copy the finished
        file out (ENG-4688). When it does the work but forgets the copy, the
        turn would otherwise report that it produced nothing at all while the
        deliverable sits in a sandbox about to be destroyed (ENG-4780).

        Filters here rather than relying on the caller, because most callers do
        not filter: of this service's seven consumers only the doc-chat family
        does -- three via ``ChatBaseNode._select_result_files`` and
        ``powerpoint_chat`` via its own ``.pptx`` check. ``data_analysis``,
        ``d3_data_visualization`` and ``org_chart`` are plain ``BaseNode``s that
        hand these paths straight to ``process_files``, so without a filter here
        an empty OUTPUT_DIR would publish the whole of WORK_DIR -- page renders,
        build scripts, intermediate drafts -- to them as download cards.

        The allow-list is the skill's own ``s3_content_types``, not
        ``file_extension`` alone: that field is the *default* extension, while
        the map is the skill's declaration of every type it can legitimately
        produce. ``data_analysis`` defaults to ``.xlsx`` but a CSV or PNG is a
        first-class output for it, and dropping a stranded one would be the very
        bug this method exists to fix. Matching is case-insensitive, as in
        ``_is_working_file``.

        The map is the whole boundary -- the default extension gets no priority
        within it. Narrowing to ``file_extension`` when anything matches would
        protect only the nodes that are already protected (the pptx family
        filters again downstream, and the docx/xlsx maps exclude images
        outright) while risking a real loss on the three that are not: a
        ``data_analysis`` turn stranding ``results.xlsx`` *and* ``chart.png``
        would silently drop the chart.

        Two consequences of trusting each skill's own map, worth naming because
        neither is obvious from here:

        * ``ppt_chat`` declares ``.png`` / ``.jpg`` / ``.jpeg``, so page renders
          *are* declared types for the PowerPoint family and this filter passes
          them. The outcome is still right -- the ``.pptx`` filter downstream
          drops them -- but for those nodes this method does less work than it
          appears to.
        * ``docx_chat`` declares ``.pdf`` and ``.txt``. A stranded
          ``build.pdf`` is recovered here, dropped by
          ``_select_result_files(".docx")``, then republished by *that* method's
          fail-open, so the user receives a PDF rather than nothing. Defensible
          -- a PDF of the document beats an empty result -- but it is an
          interaction across three filters, so do not read this one in
          isolation.

        Fails *closed*, the opposite of ``_select_result_files``. That one reads
        OUTPUT_DIR, where the model was told to put deliverables, so discarding
        everything would be wrong. This reads scratch by definition: if nothing
        carries a type the skill can produce, nothing here was the deliverable.

        Never raises. It sits between the OUTPUT_DIR listing and the ENG-3922
        respin alert, and a last-resort net must not be the reason that alert
        is skipped.

        Returns:
            Absolute sandbox paths of recovered deliverables, or an empty list.
        """
        if self._sandbox is None:
            return []
        # Debug, not warning: on a chat service most turns just answer a
        # question and produce no file, so this is the normal path. The alert
        # that should be loud is _warn_if_outputs_may_be_lost.
        logger.debug(
            "No files in %s this turn; checking %s before reporting none",
            OUTPUT_DIR,
            WORK_DIR,
        )
        try:
            spilled = await self._find_since_marker(WORK_DIR)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to list {WORK_DIR}: {exc}")
            return []

        recovered = self._select_recoverable(spilled)
        if recovered:
            logger.info(
                "Recovered %d file(s) the model left in %s: %s",
                len(recovered),
                WORK_DIR,
                ", ".join(os.path.basename(p) for p in recovered),
            )
        return recovered

    def _select_recoverable(self, paths: list[str]) -> list[str]:
        """Keep only paths carrying a type this skill can produce.

        Args:
            paths: Absolute sandbox paths found in WORK_DIR.

        Returns:
            Every path whose extension the skill declares it can produce, by
            ``s3_content_types`` plus ``file_extension``. Empty when none match,
            including for a path carrying no extension at all -- an unnamed type
            is not a recognised deliverable.
        """
        cfg = self._config
        allowed = {ext.lower() for ext in (cfg.s3_content_types or {})}
        if cfg.file_extension:
            allowed.add(cfg.file_extension.lower())
        return [p for p in paths if os.path.splitext(p)[1].lower() in allowed]

    def _warn_if_outputs_may_be_lost(self, paths: list[str]) -> None:
        """Flag the combination that used to fail silently.

        Collecting nothing is unremarkable on its own -- plenty of turns just
        answer a question. Collecting nothing *after the sandbox was replaced
        during this turn* is the ENG-3922 signature and worth an alert, because
        the consuming node will still report success with an empty file list.

        The comparison is against ``_respins_at_turn_start``, not the manager's
        raw counter: one sandbox is reused for every turn in the session and the
        counter only ever grows, so a single respin on turn 1 would otherwise
        make every later question-only turn alert.
        """
        if paths or self._sandbox is None:
            return
        respins = self._sandbox.respin_count - self._respins_at_turn_start
        if respins <= 0:
            return
        logger.error(
            "No output files collected from %s after %d sandbox respin(s) "
            "during this turn; files generated before the respin are not "
            "recoverable",
            OUTPUT_DIR,
            respins,
        )
        capture_message(
            "E2B skills turn collected no output files after a sandbox respin",
            level="warning",
        )

    @staticmethod
    async def _stream_model_request(node, run) -> AsyncGenerator[StreamEvent, None]:
        """Translate one model request's stream into ``StreamEvent``s.

        Text deltas are buffered until the request completes instead of being
        yielded as they arrive. Consumer nodes accumulate ``TEXT_DELTA`` into
        the node's ``text`` output but only render on ``TEXT_STOP``, so
        withholding them costs nothing visually and means a request that dies
        mid-stream contributes no partial text -- which is what makes the
        retry in ``_run`` safe to replay without duplicating output.

        Thinking is still emitted live: it is ephemeral progress rather than
        node output, so a stalled attempt repeating some reasoning is
        harmless, and buffering it would freeze the UI for long generations.
        An open thinking block is closed before a failure propagates, so the
        resumed attempt does not leave the abandoned one spinning.
        """
        text_open = False
        thinking_open = False
        text_buf = ""
        buffered_text: list[StreamEvent] = []
        try:
            async with node.stream(run.ctx) as request_stream:
                async for event in request_stream:
                    if isinstance(event, PartStartEvent):
                        part = getattr(event, "part", None)
                        if isinstance(part, TextPart):
                            text_open = True
                            text_buf = part.content or ""
                            if part.content:
                                buffered_text.append(
                                    StreamEvent(
                                        type=EventType.TEXT_DELTA, content=part.content
                                    )
                                )
                        elif isinstance(part, ThinkingPart):
                            thinking_open = True
                            yield StreamEvent(type=EventType.THINKING_START)
                            if part.content:
                                yield StreamEvent(
                                    type=EventType.THINKING_DELTA, content=part.content
                                )
                    elif isinstance(event, PartDeltaEvent):
                        delta = getattr(event, "delta", None)
                        if isinstance(delta, TextPartDelta) and delta.content_delta:
                            text_buf += delta.content_delta
                            buffered_text.append(
                                StreamEvent(
                                    type=EventType.TEXT_DELTA,
                                    content=delta.content_delta,
                                )
                            )
                        elif (
                            isinstance(delta, ThinkingPartDelta) and delta.content_delta
                        ):
                            yield StreamEvent(
                                type=EventType.THINKING_DELTA,
                                content=delta.content_delta,
                            )
        except Exception:
            # A stalled stream skips the clean-up below, so close the thinking
            # block on the way out. Consumers key the block by id and replace it
            # on the next THINKING_START, so without this the resumed attempt
            # strands the abandoned block at "Agent is thinking..." forever.
            # ``Exception`` deliberately, not ``BaseException``: yielding while
            # a GeneratorExit is unwinding raises RuntimeError.
            if thinking_open:
                yield StreamEvent(type=EventType.THINKING_STOP)
            raise
        # Reached only when the stream drained cleanly; a transport failure
        # propagates out of the `async with` above and discards `buffered_text`.
        for text_event in buffered_text:
            yield text_event
        if thinking_open:
            yield StreamEvent(type=EventType.THINKING_STOP)
        if text_open:
            yield StreamEvent(type=EventType.TEXT_STOP, content=text_buf)

    @staticmethod
    async def _stream_tool_calls(node, run) -> AsyncGenerator[StreamEvent, None]:
        async with node.stream(run.ctx) as request_stream:
            async for event in request_stream:
                if isinstance(event, FunctionToolCallEvent):
                    part = getattr(event, "part", None)
                    if isinstance(part, (ToolCallPart, NativeToolCallPart)):
                        yield StreamEvent(
                            type=EventType.TOOL_USE,
                            tool_name=part.tool_name,
                            content=part.args_as_json_str(),
                        )
                elif isinstance(event, FunctionToolResultEvent):
                    res = getattr(event, "result", None)
                    if isinstance(res, (ToolReturnPart, NativeToolReturnPart)):
                        yield StreamEvent(
                            type=EventType.TOOL_RESULT,
                            tool_name=res.tool_name,
                            content=res.model_response_str(),
                        )

    # ── Model + skill load ────────────────────────────────────────────────────

    def _get_streaming_http_client(self) -> httpx.AsyncClient:
        """Client for multi-minute doc-gen streams.

        The read timeout is the gap allowed between two chunks of an in-flight
        response. Unlike the native skills service this path has no
        server-side code execution, so the model is only ever emitting tokens
        and a long silence always means the stream is dead -- see
        ``E2B_SKILLS_STREAM_READ_TIMEOUT_SECONDS``. ``_run`` re-issues the
        stalled request rather than failing the node.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=settings.E2B_SKILLS_STREAM_READ_TIMEOUT_SECONDS,
                    write=60.0,
                    pool=5.0,
                )
            )
        return self._http_client

    async def _build_model(self, model: str, orgid: str | None, ep: tuple[str, str | None] | None = None):
        """Resolve the model to an Anthropic model with the
        per-org key. Residency routing composes on top of this."""
        if _hop_phien_bat() and ep:
            # [va_hop_phien_claude] file mô hình khối bảo Claude ⇒ bộ dựng Claude GỐC (ai/agentic/models/anthropic.py get_anthropic_model: địa chỉ gốc =
            # resolve_anthropic_base_url() = cổng AI). KHÔNG qua _resolve_anthropic_model (gốc lặng lẽ lùi về claude-opus-5 — tên không có trong bảng cổng).
            ten, muc = ep
            cai_dat = self._model_settings(ten)   # y như gốc: Claude 5.5 suy luận tự điều chỉnh (adaptive), max_tokens gốc — không kẹp
            cai_dat["parallel_tool_calls"] = False   # giữ rào "mỗi trả lời ≤ 1 lời gọi công cụ" như nhánh luna (nới hay không: quyết cùng E sau khi có số đo)
            mh = get_anthropic_model_with_bedrock_failover(
                AnthropicModels(ten),   # tên lạ ⇒ ValueError (hàm đọc file đã kiểm trước)
                cai_dat,
                api_key=await resolve_api_key_for_provider(Provider.ANTHROPIC, orgid),
                http_client=self._get_streaming_http_client(),
            )
            # phải là mô hình Anthropic TRỰC TIẾP mang đúng tên gửi (đi cổng AI). Lớp bọc lùi-sang-Bedrock của Capitol (hộp tự gọi Bedrock, đi vòng cổng + sổ) ⇒ NÉM.
            if getattr(mh, "_model_name", None) != ten:
                raise ValueError(f"hộp phiên (chay_thu): mô hình dựng ra ({type(mh).__name__}) không mang tên gửi '{ten}' — không chạy (tránh đi vòng cổng AI)")
            if muc:   # mức suy luận ⇒ tên gửi trên dây là tuyến "<tên>-<mức>" của bảng cổng (như bản vá model_resolution.py)
                mh._model_name = f"{ten}-{muc}"
            return mh
        if _hop_phien_bat():   # [chay_thu V1] gpt-6-luna (như sua_doi/ai/agentic/models/openai.py); khác ⇒ lỗi, không đổi lặng lẽ
            from ai.agentic.models.openai import get_openai_model_for_agent_node
            from core.config.ai.openai import OpenAIModels

            if model != OpenAIModels.GPT_6_LUNA.value:
                raise ValueError(f"hộp phiên (chay_thu) chỉ chạy gpt-6-luna, không {model!r}")
            return get_openai_model_for_agent_node(
                OpenAIModels.GPT_6_LUNA,
                # [va_tran_tai_lieu vòng 3 — E] parallel_tool_calls False: mỗi trả lời ≤ 1 lời gọi công cụ ⇒ cận "1 lần gọi ≤ lần trước + 8k + MỘT kết
                # quả công cụ (≤ 30k ký tự)" < 272k mới đúng (mặc định OpenAI cho gọi song song: 3 read_file × ~58k ⇒ > 272k). Như deep_research.
                model_settings={"max_tokens": min(int(self._config.max_tokens or 8000), 8000), "parallel_tool_calls": False},   # gốc 64.000 — giữ tiền
                api_key=await resolve_api_key_for_provider(Provider.OPENAI, orgid),
            )
        anthropic_model = self._resolve_anthropic_model(model, orgid)
        model = anthropic_model.value
        spec = get_spec(model)
        base_value = spec.base_model_id if spec and spec.base_model_id else model
        base_model = next(
            (m for m in AnthropicModels if m.value == base_value), anthropic_model
        )
        api_key = await resolve_api_key_for_provider(Provider.ANTHROPIC, orgid)
        return get_anthropic_model_with_bedrock_failover(
            base_model,
            self._model_settings(model),
            api_key=api_key,
            http_client=self._get_streaming_http_client(),
        )

    def _resolve_anthropic_model(
        self, model: str, orgid: str | None
    ) -> AnthropicModels:
        """Swap a model id from another provider for this node's configured
        default — these skills only run on Anthropic models. Substituting
        loudly beats silently running a model the skill isn't tuned for.
        """
        try:
            return AnthropicModels(model)
        except ValueError:
            default = self._config.default_model
            logger.warning(
                f"Non-Anthropic model {model!r} configured on an Anthropic-only "
                f"skill node (orgid={orgid}); using {default!r} instead"
            )
            return AnthropicModels(default)

    def _model_settings(self, model: str) -> dict[str, Any]:
        """Model-behavior settings. Compaction is applied separately as a
        provider-agnostic capability, not via server-side context management."""
        cfg = self._config
        spec = get_spec(model)
        s: dict[str, Any] = {}
        if model in _ADAPTIVE_CARVE_IN or (spec and spec.thinking_mode == "adaptive"):
            s["max_tokens"] = cfg.opus_max_tokens
            s["anthropic_thinking"] = {"type": "adaptive"}
            if spec and spec.thinking_display:
                s["anthropic_thinking"]["display"] = spec.thinking_display
        else:
            s["max_tokens"] = cfg.max_tokens
            s["temperature"] = 1
            s["anthropic_thinking"] = {
                "type": "enabled",
                "budget_tokens": cfg.thinking_budget,
            }
        if spec and spec.fast_mode:
            s["anthropic_speed"] = "fast"
        return s

    async def _load_skill(self, sandbox: SandboxManager) -> str:
        """Load the skill into SKILL_SANDBOX_DIR and return its SKILL.md prompt section.

        Resolution order:
          1. Baked: if /home/user/skills-baked/<name>/.skill-version exists, use
             it — with a pin configured (settings.E2B_SKILL_<NAME>_VERSION) it
             must match the marker; with no pin configured, whatever is baked is
             trusted as-is. Either way this is a local copy, zero bytes
             transferred over the network.
          2. S3: if a pin is configured but the baked version does not match (or
             no bake marker exists), fetch skills/<name>/v<X.Y.Z>.zip from the
             skills bucket, verify the sha256 from object metadata, upload and
             extract.
          3. Local zip: no pin and no usable baked copy — fall back to zipping
             the local source directory and uploading, which is today's
             behaviour and keeps local skill editing working.
        """
        if _hop_phien_bat():   # [chay_thu V1] giải nén phía máy, không unzip trong hộp (sandbox-py:v1 không có unzip)
            return await self._load_skill_hop_phien(sandbox)
        skill_name = self._config.skill_name
        baked_dir = f"{SKILLS_BAKED_DIR}/{skill_name}"
        marker_path = f"{baked_dir}/.skill-version"

        # Determine the pinned version (if any)
        pin_setting = f"E2B_SKILL_{skill_name.upper().replace('-', '_')}_VERSION"
        pinned_version: str = getattr(settings, pin_setting, "")

        await sandbox.run_shell(f"mkdir -p {SKILL_SANDBOX_DIR}", timeout=10)

        check = await sandbox.run_shell(
            f"cat {marker_path} 2>/dev/null || echo __missing__",
            timeout=10,
            raise_on_error=False,
        )
        baked_marker = (check.stdout or "").strip()
        baked_present = baked_marker not in ("", "__missing__")

        # Marker format: "version:sha256"
        baked_version = (
            baked_marker.split(":")[0] if ":" in baked_marker else baked_marker
        )

        if baked_present and (not pinned_version or baked_version == pinned_version):
            # Fast path: no pin configured (trust whatever is baked) or the
            # baked copy matches the pin — copy locally, zero network transfer.
            logger.info(
                f"Skill {skill_name}: using baked copy"
                + (f" @ {baked_version}" if baked_version else "")
                + " (local copy, zero transfer)"
            )
            result = await sandbox.run_shell(
                f"cp -r {baked_dir}/. {SKILL_SANDBOX_DIR}/",
                timeout=30,
                raise_on_error=False,
                replay_on_respin=True,
            )
            if result.exit_code != 0:
                logger.warning(
                    f"Skill {skill_name}: baked copy failed "
                    f"(exit {result.exit_code}), falling back"
                )
            else:
                skill_md = await sandbox.read_file(f"{SKILL_SANDBOX_DIR}/SKILL.md")
                has_files = await self._has_supporting_files(sandbox)
                return self._build_skill_prompt(skill_md, skill_name, has_files)

        if pinned_version:
            if baked_present and baked_version != pinned_version:
                logger.info(
                    f"Skill {skill_name}: baked={baked_version!r} != "
                    f"pin={pinned_version!r}, fetching from S3"
                )

            # S3 fallback: download pinned artifact
            bucket = settings.E2B_SKILLS_BUCKET
            if bucket:
                await self._load_skill_from_s3(
                    sandbox, skill_name, pinned_version, bucket
                )
                skill_md = await sandbox.read_file(f"{SKILL_SANDBOX_DIR}/SKILL.md")
                has_files = await self._has_supporting_files(sandbox)
                return self._build_skill_prompt(skill_md, skill_name, has_files)
            else:
                logger.warning(
                    f"Skill {skill_name}: S3 bucket not configured "
                    f"(E2B_SKILLS_BUCKET), falling back to local zip"
                )

        # Local zip fallback (original behaviour — keeps local editing working)
        source_dir = Path(get_skill_entry(skill_name)["source_dir"])
        if not source_dir.is_dir():
            raise RuntimeError(f"Built-in skill directory not found: {source_dir}")

        archive_bytes, has_supporting_files = self._zip_skill_dir(source_dir)
        await sandbox.upload_file(f"{SKILL_SANDBOX_DIR}/skill.zip", archive_bytes)
        result = await sandbox.run_shell(
            f"cd {SKILL_SANDBOX_DIR} && unzip -o skill.zip && rm skill.zip",
            timeout=60,
            raise_on_error=False,
            replay_on_respin=True,
        )
        if result.exit_code != 0:
            err = (result.stderr or result.stdout or "unknown error").strip()[:300]
            raise RuntimeError(
                f"Skill {skill_name} unzip failed (exit {result.exit_code}): {err}"
            )

        skill_md = await sandbox.read_file(f"{SKILL_SANDBOX_DIR}/SKILL.md")
        return self._build_skill_prompt(skill_md, skill_name, has_supporting_files)

    async def _load_skill_from_s3(
        self,
        sandbox: SandboxManager,
        skill_name: str,
        version: str,
        bucket: str,
    ) -> None:
        """Download a pinned skill artifact from S3, verify its sha256, and extract it."""
        import hashlib

        key = f"skills/{skill_name}/{version}.zip"
        s3 = S3Service()

        try:
            metadata = await asyncio.to_thread(
                s3.get_file_metadata, key, bucket_name=bucket
            )
        except Exception as exc:
            raise RuntimeError(
                f"Skill artifact not found in S3: s3://{bucket}/{key} — {exc}"
            ) from exc
        expected_sha = metadata.get("metadata", {}).get("sha256", "")
        if not expected_sha:
            raise RuntimeError(
                f"Skill {skill_name} S3 artifact missing sha256 metadata: "
                f"s3://{bucket}/{key}"
            )

        zip_bytes = await asyncio.to_thread(
            s3.download_file_content, key, bucket_name=bucket
        )

        actual_sha = hashlib.sha256(zip_bytes).hexdigest()
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"Skill {skill_name} sha256 mismatch: "
                f"expected {expected_sha[:12]}… got {actual_sha[:12]}…"
            )

        await sandbox.upload_file(f"{SKILL_SANDBOX_DIR}/skill.zip", zip_bytes)
        result = await sandbox.run_shell(
            f"cd {SKILL_SANDBOX_DIR} && unzip -o skill.zip && rm skill.zip",
            timeout=120,
            raise_on_error=False,
            replay_on_respin=True,
        )
        if result.exit_code != 0:
            err = (result.stderr or result.stdout or "unknown").strip()[:300]
            raise RuntimeError(
                f"Skill {skill_name} S3 unzip failed (exit {result.exit_code}): {err}"
            )
        logger.info(f"Skill {skill_name}: loaded from S3 @ {version}")

    async def _has_supporting_files(self, sandbox: SandboxManager) -> bool:
        """Return True if SKILL_SANDBOX_DIR contains files other than SKILL.md."""
        result = await sandbox.run_shell(
            f"find {SKILL_SANDBOX_DIR} -type f ! -name SKILL.md | head -1",
            timeout=10,
            raise_on_error=False,
        )
        return bool((result.stdout or "").strip())

    async def _load_skill_hop_phien(self, sandbox) -> str:
        """[chay_thu V1] Skill từ thư mục nguồn trong image (đúng nguồn "local zip" của gốc) ⇒ build_skill_zip (gốc) ⇒ giải TRONG BỘ NHỚ,
        kiểm từng mục (không tuyệt đối, không \\, không '.' / '..', không symlink, trần cỡ / số / tổng) ⇒ upload_file từng file vào
        SKILL_SANDBOX_DIR (V2 tự tạo thư mục cha — GIAO_DIEN_V2 6.5). Không đường baked / S3 (cần E2B / bucket skill của Capitol)."""
        skill_name = self._config.skill_name
        archive = build_skill_zip(Path(get_skill_entry(skill_name)["source_dir"]))
        skill_md, khac, tong, sach = None, False, 0, []
        with zipfile.ZipFile(BytesIO(archive)) as zf:
            muc = [i for i in zf.infolist() if not i.is_dir()]
            if len(muc) > _TRAN_SKILL_SO:
                raise ValueError(f"skill {skill_name}: {len(muc)} file > trần {_TRAN_SKILL_SO}")
            for info in muc:
                ten = info.filename
                if ten.startswith("/") or "\\" in ten or any(p in ("", ".", "..") for p in ten.split("/")):
                    raise ValueError(f"skill {skill_name}: tên trong gói không hợp lệ {ten!r}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"skill {skill_name}: {ten!r} là symlink — từ chối")
                if info.file_size > _TRAN_SKILL_FILE:
                    raise ValueError(f"skill {skill_name}: {ten!r} {info.file_size} byte > trần {_TRAN_SKILL_FILE}")
                data = zf.read(info)
                tong += len(data)
                if len(data) != info.file_size or tong > _TRAN_SKILL_TONG:
                    raise ValueError(f"skill {skill_name}: cỡ giải nén lệch / tổng > trần {_TRAN_SKILL_TONG}")
                sach.append((ten, data))
                if ten == "SKILL.md":
                    skill_md = data.decode("utf-8", "replace")
                else:
                    khac = True
        if skill_md is None:
            raise ValueError(f"skill {skill_name}: gói không có SKILL.md")
        if len(sach) <= _GOP_SKILL_TU:
            for ten, data in sach:
                await sandbox.upload_file(f"{SKILL_SANDBOX_DIR}/{ten}", data)
        else:
            # [ghi chú] skill nhiều file (d3-data-viz 153) ⇒ mỗi file một phiếu sẽ vượt trần 30 phiếu / phiên của người chạy ⇒
            # gói lại CHỈ các mục đã kiểm (ZipInfo mới, không mang thuộc tính gốc) ⇒ 1 dat_file + 1 phiếu python giải trong hộp (kiểm tên lần 2).
            buf = BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for ten, data in sach:
                    z.writestr(zipfile.ZipInfo(ten, date_time=(2026, 1, 1, 0, 0, 0)), data)
            goi = f"/home/user/work/.skill_{uuid4().hex}.zip"
            await sandbox.upload_file(goi, buf.getvalue())
            r = await sandbox.run_code(_GIAI_SKILL.replace("@GOI@", repr(goi)).replace("@DICH@", repr(SKILL_SANDBOX_DIR)),
                                       language="python", timeout=60)
            if getattr(r, "exit_code", 1) != 0 or getattr(r, "error", None) or f"GIAI_XONG {len(sach)}" not in (getattr(r, "stdout", "") or ""):
                raise ValueError(f"skill {skill_name}: giải gói trong hộp lỗi — {(getattr(r, 'error', None) or getattr(r, 'stderr', ''))!s:.200}")
        return self._build_skill_prompt(skill_md, skill_name, khac)

    @staticmethod
    def _zip_skill_dir(source_dir: Path) -> tuple[bytes, bool]:
        """Zip a skill directory via the shared deterministic zip builder.

        Returns ``(archive_bytes, has_files_beyond_skill_md)``.
        """
        archive = build_skill_zip(source_dir)
        with zipfile.ZipFile(BytesIO(archive)) as zf:
            names = zf.namelist()
        has_supporting_files = any(name != "SKILL.md" for name in names)
        return archive, has_supporting_files

    def _build_skill_prompt(
        self,
        skill_md: str,
        skill_name: str,
        has_supporting_files: bool,
    ) -> str:
        """Bounded skill section for the system prompt, mirroring
        ``SkillCodeInterpreterNode._build_skill_prompt`` so the model treats the
        content as authoritative instructions and knows where the files live.

        Injects a capability note from the template registry so the agent
        never tries to install packages that are already pre-installed.
        """
        parts = ["", "=" * 60, f"ACTIVE SKILL: {skill_name}", "=" * 60, ""]

        # Capability note — injected here so the agent sees it immediately
        # alongside the skill header, before SKILL.md's dependency section.
        # Skills pinned to a dedicated, bounded image (pptx/docx/xlsx/...) get
        # the closed-image wording; skills like Data Analysis that fall back
        # to the shared, general-purpose base image run arbitrary tasks and
        # still expose install_packages, so they need the open wording too.
        tpl = self._config.e2b_template or settings.E2B_TEMPLATE or ""
        cap = get_capabilities(tpl)
        capability_note = build_capability_note(
            tpl, allow_extra_installs=bool(cap and cap.general_purpose)
        )
        if capability_note:
            parts += [
                "SANDBOX ENVIRONMENT",
                "-" * 40,
                capability_note,
                "",
            ]

        parts.append(
            "The following skill instructions define your domain expertise for "
            "this session. Follow them closely — they take priority over generic "
            "approaches when they conflict with default behavior."
        )
        parts.append("")
        if has_supporting_files:
            parts.append(
                "SKILL FILES: Supporting files from this skill have been "
                f"extracted to {SKILL_SANDBOX_DIR}/. Use them as reference data, "
                "templates, or scripts as described in the instructions below."
            )
            parts.append("")
        parts.append(
            f"Write the files you were asked for to {OUTPUT_DIR}/, and put "
            f"anything you produce along the way -- page renders, format "
            f"conversions, build scripts, intermediate drafts -- in "
            f"{WORK_DIR}/ instead. Only {OUTPUT_DIR}/ is returned. A copy "
            f"of these instructions is also saved at "
            f"{SKILL_SANDBOX_DIR}/SKILL.md."
        )
        parts += [
            "",
            "--- BEGIN SKILL INSTRUCTIONS ---",
            "",
            skill_md.strip(),
            "",
            "--- END SKILL INSTRUCTIONS ---",
            "",
        ]
        return "\n".join(parts)

    async def upload_template(
        self, file_content: bytes, filename: str, orgid: str | None = None
    ) -> str | None:
        """Write a style-reference template into the sandbox and return its path."""
        # [ghi chú] file mẫu là đường file THỨ HAI vào máy (không đi qua `files` của _run). Chưa có máy ⇒ quyết nơi chạy NGAY ĐÂY bằng đúng
        # hàm của _run (thiếu khoá E2B ⇒ ném, không tự về hộp trên máy); khối chạy trên máy ảo E2B ⇒ file mẫu qua ĐÚNG rào "file người dùng lên E2B" (loại file, tên giống
        # file khoá, dò khoá kể cả trong gói nén) TRƯỚC khi tạo máy. Không qua rào ⇒ KHÔNG gửi file mẫu, báo một dòng tiến độ, khối chạy tiếp không có mẫu (như khi tải mẫu lỗi).
        if _hop_phien_bat():
            from workflow.node.base.agent import sandbox_runtime as _sr
            if self._sandbox is None:
                self._ct_e2b = _sr.e2b_cho_khoi_lam_file(_chay_thu_mo_hinh_hop_phien(self._emit_progress)[0])
                self._ct_da_soat = False
            if getattr(self, "_ct_e2b", False):
                try:
                    _sr._e2b_kiem_noi_dung(os.path.basename(filename), bytes(file_content))
                except ValueError as exc:
                    logger.warning("[chay_thu e2b] file mẫu không qua rào file lên E2B ⇒ không gửi: %s", str(exc)[:200])
                    if self._emit_progress is not None:
                        await self._emit_progress("File mẫu không được gửi lên máy ảo E2B (khối chạy tiếp, không dùng mẫu): " + str(exc)[:300])
                    return None
                file_content = bytes(file_content)
        try:
            sandbox = await self._ensure_sandbox()
            path = f"{TEMPLATE_SANDBOX_DIR}/{os.path.basename(filename)}"
            await sandbox.run_shell(f"mkdir -p {TEMPLATE_SANDBOX_DIR}", timeout=10)
            await sandbox.upload_file(path, file_content)
            return path
        except Exception as exc:  # noqa: BLE001 - template is best-effort
            logger.error(f"Failed to upload template into sandbox: {exc}")
            capture_exception(exc)
            return None

    # ── Output file I/O (sandbox <-> S3) ──────────────────────────────────────

    async def download_file(self, file_id: str) -> tuple[bytes, str | None]:
        """Read a produced file from the (still-alive) session sandbox.

        ``file_id`` is a sandbox path returned in MESSAGE_COMPLETE.file_ids.
        """
        if self._sandbox is None:
            raise RuntimeError("No active sandbox; download_file called out of turn.")
        content = await self._sandbox.download_file(file_id)
        return content, os.path.basename(file_id)

    async def _xlsx_tinh_lai(self, b: bytes) -> bytes:
        """[ghi chú] File xlsx (ĐÃ làm sạch) còn ô công thức thiếu giá trị ⇒ soffice tính lại trong hộp phiên ⇒ KIỂM bản nhận về:
        chạy lại _xlsx_an_toan phải 0 chỗ bỏ (soffice không được thêm liên kết / gọi ngoài), cùng số ô công thức, ít ô thiếu giá trị hơn.
        Không đạt / lỗi / không có hộp ⇒ trả NGUYÊN b (bản đã làm sạch: an toàn, chỉ thiếu số) + log. Không bao giờ trả file chưa qua kiểm."""
        tong, thieu = _xlsx_dem_o(b)
        if not thieu:
            return b
        if self._sandbox is None or not _hop_phien_bat():
            logger.warning("[chay_thu tinh_lai] %d / %d ô công thức thiếu giá trị, không có hộp ⇒ giữ nguyên", thieu, tong)
            return b
        ten = uuid4().hex
        vao, ra = f"/home/user/work/_tl_{ten}.xlsx", f"/home/user/work/_tl_{ten}"
        try:
            await self._sandbox.run_shell(f"mkdir -p {ra}", timeout=15)
            await self._sandbox.upload_file(vao, bytes(b))   # [ghi chú] file đọc về từ máy ảo E2B là bytearray; hàm gửi file của thư viện E2B chỉ nhận bytes / str ⇒ InvalidArgumentException TRƯỚC khi gửi ⇒ ô công thức không bao giờ có số (lượt 7e36e977). Hộp phiên nhận cả hai kiểu nên không lộ.
            r = await self._sandbox.run_shell(f"timeout 90 soffice --headless --norestore --convert-to xlsx --outdir {ra} {vao}", timeout=110,
                                              raise_on_error=False)
            moi = await self._sandbox.download_file(f"{ra}/_tl_{ten}.xlsx")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[chay_thu tinh_lai] không tính lại được (%s) ⇒ giữ bản đã làm sạch", type(exc).__name__)
            return b
        finally:
            try:
                await self._sandbox.run_shell(f"rm -rf {vao} {ra}", timeout=15, raise_on_error=False)
            except Exception:  # noqa: BLE001
                pass
        try:
            _, bo = _xlsx_an_toan(moi)
            _xlsx_kiem_goi(moi)   # [ghi chú] bản tính lại cũng phải qua kiểm gói
        except ValueError:
            bo = -1
        tong2, thieu2 = _xlsx_dem_o(moi)
        if bo != 0 or tong2 != tong or thieu2 >= thieu:
            logger.warning("[chay_thu tinh_lai] bản tính lại KHÔNG đạt kiểm (bo=%s, o=%d/%d, thieu=%d/%d, ma=%s) ⇒ giữ bản đã làm sạch",
                           bo, tong2, tong, thieu2, thieu, getattr(r, "exit_code", "?"))
            return b
        logger.info("[chay_thu tinh_lai] tính lại %d / %d ô thiếu giá trị ⇒ còn %d", thieu, tong, thieu2)
        return moi

    def _khoa_file_ra(self, ext: str) -> str:
        """[chay_thu C14] Khoá S3 của file ra. Tắt hộp phiên ⇒ giữ nguyên khuôn gốc Capitol."""
        cfg = self._config
        if not _hop_phien_bat():
            return f"{cfg.s3_folder}/{uuid4()}{ext}"
        from nodes.agent.native_tools import _MA_TO_CHUC

        org = getattr(self, "_orgid_luot", None) or ""
        if not _MA_TO_CHUC.match(org):
            raise ValueError("hộp phiên (chay_thu): không rõ tổ chức của lượt ⇒ không lưu file ra")
        if (ext or "").lower() in _DUOI_RA_KHONG_NHAN and cfg.skill_name not in _SKILL_HTML:   # html chỉ cho 2 skill vẽ hình
            raise ValueError(f"hộp phiên (chay_thu): không nhận file ra dạng trang web ({ext})")
        return f"{cfg.s3_folder}/{org}/{uuid4()}{ext}"

    def _them_tai_ve(self, ext: str, extras: dict) -> dict:
        """[chay_thu d3 / đồ thị] trang web ⇒ tải về (attachment), không hiện thẳng trên miền MinIO (như _do_thi_khoi.py:251-253)."""
        if _hop_phien_bat() and (ext or "").lower() in _DUOI_RA_KHONG_NHAN:
            extras = dict(extras, ContentDisposition="attachment")
        return extras

    def _resolve_content_type(self, filename: str) -> tuple[str, str]:
        _, ext = os.path.splitext(filename.lower())
        ext = ext or self._config.file_extension
        return ext, self._config.s3_content_types.get(
            ext, self._config.default_content_type
        )

    async def upload_to_s3(
        self,
        file_content: bytes,
        filename: str | None = None,
        *,
        download_filename: str | None = None,
    ) -> dict:
        """Upload a produced file to S3 via proxy URLs (Office-viewer compatible).

        The shared ``ChatBaseNode._finalize_output_file`` path depends on this
        signature and return shape.
        """
        s3 = S3Service()
        cfg = self._config
        ext, content_type = self._resolve_content_type(filename or "")
        s3_key = self._khoa_file_ra(ext)   # [C14]
        if _hop_phien_bat():
            file_content = _lam_sach_file_ra(ext, file_content, cfg.skill_name)   # [C14 làm sạch; html chỉ 2 skill]
            if (ext or "").lower() == ".xlsx":
                file_content = await self._xlsx_tinh_lai(file_content)   # [ghi chú] số tính sẵn
        buf = BytesIO(file_content)
        buf.seek(0)

        s3.upload_file(
            file=buf,
            bucket_name=cfg.s3_bucket,
            key=s3_key,
            extras=self._them_tai_ve(ext, {"ContentType": content_type}),
        )
        s3_url = s3.generate_proxy_url(
            s3_key=s3_key,
            bucket_name=cfg.s3_bucket,
            content_type=content_type,
            filename=download_filename,
            **({"download": True} if _hop_phien_bat() and ext in _DUOI_RA_KHONG_NHAN else {}),   # [D (5)] html luôn tải về
        )
        download_url = s3.generate_proxy_url(
            s3_key=s3_key,
            bucket_name=cfg.s3_bucket,
            content_type=content_type,
            filename=download_filename,
            download=True,
        )
        return {
            "s3_url": s3_url,
            "download_url": download_url,
            "s3_bucket": cfg.s3_bucket,
            "s3_key": s3_key,
            "s3_region": s3.region or "us-east-1",
            "content_type": content_type,
        }

    async def process_files(
        self,
        file_ids: list[str],
        *,
        fallback_label: str | None = None,
        default_extension: str | None = None,
    ) -> list[dict]:
        """Download each produced file from the sandbox and upload it to S3 with a
        presigned URL (used by the one-shot generate nodes).

        Filename stems the skill's config marks as generic (e.g.
        ``visualization`` for the D3 skill, via
        ``self._config.generic_filename_stems``) still get a content-derived
        download name rather than every artifact sharing one title.
        """
        if self._sandbox is None:
            return []
        default_extension = default_extension or self._config.file_extension
        multiple = len(file_ids) > 1
        s3 = S3Service()
        cfg = self._config
        results: list[dict] = []
        for i, path in enumerate(file_ids):
            try:
                content, raw_name = await self.download_file(path)
                friendly = coerce_friendly_filename(
                    raw_name,
                    fallback_label=fallback_label,
                    default_extension=default_extension,
                    index=i if multiple else None,
                    extra_placeholders=cfg.generic_filename_stems,
                )
                ext, content_type = self._resolve_content_type(raw_name or "")
                s3_key = self._khoa_file_ra(ext)   # [C14] (lỗi ⇒ bỏ file này, nhánh except dưới ghi log)
                if _hop_phien_bat():
                    content = _lam_sach_file_ra(ext, content, cfg.skill_name)   # [C14 làm sạch; html chỉ 2 skill]
                    if (ext or "").lower() == ".xlsx":
                        content = await self._xlsx_tinh_lai(content)   # [ghi chú] số tính sẵn
                s3.upload_file(
                    file=BytesIO(content),
                    bucket_name=cfg.s3_bucket,
                    key=s3_key,
                    extras=self._them_tai_ve(ext, {"ContentType": content_type}),
                )
                results.append(
                    {
                        "file_id": path,
                        "filename": friendly,
                        "s3_url": s3.generate_presigned_url(
                            s3_key=s3_key, bucket_name=cfg.s3_bucket, filename=friendly,
                            **({"download": True} if _hop_phien_bat() and ext in _DUOI_RA_KHONG_NHAN else {}),   # [D (5)]
                        ),
                        "s3_bucket": cfg.s3_bucket,
                        "s3_key": s3_key,
                        "s3_region": s3.region or "us-east-1",
                        "content_type": content_type,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed to process output file {path}: {exc}")
                capture_exception(exc)
        return results
