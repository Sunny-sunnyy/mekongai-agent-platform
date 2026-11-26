# [ghi chú] BẢN SAO có vá "khối chạy code qua HỘP PHIÊN" (KE_HOACH_DUNG_TIEP_D việc 4) — gốc: sandbox_runtime.py.goc
# (AB release-4.10.0, sha16 9686000951c0996e). Mọi chỗ sửa có nhãn [chay_thu CI]. Gắn ở V5 — CHƯA gắn.
"""E2B sandbox lifecycle shared by every agent node.

Consolidates the previously duplicated sandbox handling from the Agent
node (conditional sandbox when files are connected, parallel S3 download
+ sandbox boot, file probing, prompt directives, S3 persistence) and the
Code Interpreter (always-on sandbox, template routing, package
pre-install, executed-code capture, artifact-store bridge).

Standardised behaviour (ENG-4437): every node starts the sandbox
keepalive for the whole run. Previously only the code interpreter did;
the Agent node relied on the initial TTL plus the manager's respin —
which replays uploaded inputs but silently loses files the agent itself
created mid-run.

Per-class knobs are ClassVars documented on ``AgentBaseNode``.
"""

import asyncio
import logging
import os
from enum import StrEnum
from io import BytesIO
from typing import Any, ClassVar, final
from uuid import uuid4

from core.settings import settings
from services.artifact_store import register_artifact_bytes
from services.aws.s3 import S3Service
from services.document_generation.naming import coerce_friendly_filename
from services.output_file_mime import detect_content_type
from services.sandbox import (
    OFFLOAD_DIR,
    TASK_PROGRESS_SENTINEL,
    SandboxManager,
    make_sandbox_tools,
)
from services.sandbox.basemaps import build_basemaps_note
from services.sandbox.paths import OUTPUT_DIR as SANDBOX_OUTPUT_DIR
from services.sandbox.paths import SKILLS_BAKED_DIR
from services.sandbox.paths import WORK_DIR as SANDBOX_WORK_DIR
from services.sandbox.templates import build_capability_note
from workflow.node.base.file import NodeFile
from workflow.node.base.node import NodeOutput

logger = logging.getLogger(__name__)


# ======================================================================================================================================
# [ghi chú] Khối chạy code (`SandboxMode.ALWAYS`: flexible_code_interpreter_node,
# skill_code_interpreter_node) chạy qua HỘP PHIÊN (V2 `services/sandbox/hop_phien.py` ⇒ người chạy phiên trên WSL, hộp --network none)
# thay máy ảo E2B CÓ MẠNG (services/sandbox/manager.py:375-381). Chỉ khi cờ CHAY_THU_HOP_PHIEN bật (mặc định bật, như V1 nhóm kỹ thuật) VÀ khối là
# ALWAYS; họ Agent IF_FILES giữ nguyên (vá 10 vẫn cấm E2B cho họ đó). Tắt cờ ⇒ đúng Capitol gốc.
# Rào thêm khi đi hộp phiên (cùng luật V1 — dùng lại hàm của V1, không viết bản thứ hai):
#   - R12 TRONG khối: có mcp_servers / khối công cụ nối vào ⇒ từ chối (chạy code + đường ra ngoài trong cùng khối; R12 mức workflow
#     `sua_doi/workflow/r12_kiem.py` đã coi 2 loại này là gốc — lớp này là lớp thứ hai lúc chạy);
#   - C13 file VÀO: chỉ file người dùng tải lên của chính tổ chức (`_kiem_file_vao` của V1 ⇒ native_tools.kiem_khoa_cua_to_chuc);
#   - C14 file RA: khoá S3 có mã tổ chức `{prefix}/<orgid>/<uuid>/<tên>` (gốc: không có mã tổ chức), không nhận .html/.htm/.xhtml,
#     làm sạch xlsx / csv (`_lam_sach_file_ra` của V1).
# ======================================================================================================================================
def _ci_hop_phien_bat() -> bool:
    return os.environ.get("CHAY_THU_HOP_PHIEN", "true").strip().lower() not in ("false", "0", "no", "off")


# [ghi chú] Chỉ loại khối (node_sub_type) có trong
# CHAY_THU_E2B_KHOI (bậc 1: "code_interpreter" = flexible_code_interpreter_node) VÀ hộp có CHAY_THU_E2B_KHOA (biến riêng, xem _e2b_khoa) ⇒ chạy trên sandbox E2B thay hộp phiên.
# Khối khác giữ hộp phiên. Gỡ = bỏ biến (không xoá vá hộp phiên). Ép ở MỌI lần tạo (kể cả tạo lại): mạng ra TẮT (allow_internet_access=False),
# URL sandbox không công khai (network.allow_public_traffic=False — SDK e2b 2.46.4 sandbox_api.py:742), thời gian sống ≤ CHAY_THU_E2B_TTL
# (KHÔNG chạy keepalive ⇒ E2B tự tắt máy; manager tạo lại ≤ 3 lần ⇒ ≤ 4 × TTL giây máy), envs CHỈ NODE_PATH (không khoá nào vào sandbox).
# File vào: CHỈ file có sha256 trong danh sách giả lập (CHAY_THU_E2B_DS), so TRƯỚC khi tạo sandbox và so lại ngay trước khi tải lên.
def _e2b_khoi_bat(sub_type) -> bool:
    ds = {x.strip() for x in os.environ.get("CHAY_THU_E2B_KHOI", "").split(",") if x.strip()}
    return bool(sub_type and sub_type in ds and _e2b_khoa())


# [B 19/11/2026 va_noi_chay — E2B SONG SONG với hộp máy, bậc 0. User (nhóm kỹ thuật): "giữ song song chạy ở máy tôi, và vẫn triển khai lên e2b toàn bộ nhé"; đã chốt: nơi chạy
# luôn đúng như file cấu hình, E2B lỗi ⇒ báo lỗi rõ, KHÔNG tự chuyển về hộp máy.] FILE NƠI CHẠY: /srv/cau_hinh_mo_hinh/noi_chay_khoi.txt (cùng thư mục gắn với file mô hình
# khối; biến CHAY_THU_NOI_CHAY_FILE để bộ thử trỏ chỗ khác), đọc lại mỗi lần khối chạy. Mỗi dòng:  <mac_dinh | loại khối> = e2b | may   (dòng trống, dòng # bỏ qua).
# - KHÔNG có file ⇒ None ⇒ hành vi CŨ (biến CHAY_THU_E2B_KHOI quyết) — gắn gói này mà chưa đặt file thì không đổi gì.
# - Có file: khối trong CHAY_THU_KHOI_NOI_CHAY lấy dòng riêng, không có thì "mac_dinh", không có nữa thì NÉM (bản 3 — không tự chọn). Khối NGOÀI danh sách: file không áp (bậc sau thêm dần).
# - Dòng sai khuôn / giá trị lạ / loại khối chưa hỗ trợ ⇒ NÉM (khối dừng với lỗi rõ — như file mô hình khối), không đoán.
# [ghi chú] hai khối này đi dịch vụ làm file
# (services/pydantic_ai_skills/e2b_chat_service.py), không đi lớp chạy ở file này; dịch vụ hỏi e2b_cho_khoi_lam_file() bên dưới. Chưa có dòng riêng trong file nơi chạy ⇒ theo
# `mac_dinh` (đang là may) ⇒ gắn gói này KHÔNG đổi nơi chạy của khối nào cho tới khi thêm dòng "<loại khối> = e2b".
CHAY_THU_KHOI_NOI_CHAY = ("flexible_code_interpreter_node", "skill_code_interpreter_node", "agent_node", "xlsx_chat_node", "data_analysis_agent_node", "docx_chat_node", "d3_data_visualization_node", "pptx_chat_node")   # [ghi chú] + skill_code_interpreter_node + agent_node
CHAY_THU_KHOI_LAM_FILE_E2B = ("xlsx_chat_node", "data_analysis_agent_node", "docx_chat_node", "d3_data_visualization_node", "pptx_chat_node")   # [đợt 2] khối đồ thị quan hệ KHÔNG vào đây: bộ mình vẽ bằng code từ JSON của AI (cờ CHAY_THU_DO_THI_KHOI), không chạy code AI viết ⇒ không có gì để đưa lên máy ảo   # tập con của danh sách trên: các khối làm file ĐÃ có nhánh E2B   # loại khối ĐÃ chạy được ở CẢ hai nơi; bậc 1–3 thêm vào đây khi đã đo. giao_dien đọc hằng này bằng ast.
CHAY_THU_NOI = ("e2b", "may")


def _noi_chay_khoi(loai_khoi) -> str | None:
    duong = os.environ.get("CHAY_THU_NOI_CHAY_FILE") or "/srv/cau_hinh_mo_hinh/noi_chay_khoi.txt"
    try:
        with open(duong, encoding="utf-8") as f:
            dong = f.read().splitlines()
    except FileNotFoundError:
        return None
    bang: dict[str, str] = {}
    for so, d in enumerate(dong, 1):
        d = d.split("#", 1)[0].strip()
        if not d:
            continue
        khoa, dau, noi = d.partition("=")
        khoa, noi = khoa.strip(), noi.strip().lower()
        if not dau or not khoa or khoa in bang or noi not in CHAY_THU_NOI:
            raise ValueError(f"file nơi chạy khối dòng {so} sai khuôn hoặc lặp khoá ('{d[:60]}') — mỗi dòng: <mac_dinh | loại khối> = e2b | may")
        if khoa != "mac_dinh" and khoa not in CHAY_THU_KHOI_NOI_CHAY:
            raise ValueError(f"file nơi chạy khối dòng {so}: loại khối '{khoa[:60]}' chưa chạy được ở cả hai nơi (hiện chỉ: {', '.join(CHAY_THU_KHOI_NOI_CHAY)}) — sửa file {duong}")
        bang[khoa] = noi
    if loai_khoi not in CHAY_THU_KHOI_NOI_CHAY:
        return None
    noi = bang.get(loai_khoi) or bang.get("mac_dinh")
    if not noi:   # [bản 3 20/11 — E m1, đã chốt] file CÓ nhưng không dòng nào áp cho khối (file trống / chỉ chú thích / thiếu cả dòng riêng lẫn mac_dinh) ⇒ NÉM:
        # trước đây lặng lẽ coi là "may" ⇒ ai lỡ làm trống file là khối Chạy code tự chuyển từ E2B về hộp máy mà không ai chủ ý (luật: không thay thế lặng lẽ khi thiếu đầu vào).
        raise ValueError(f"file nơi chạy khối không có dòng nào cho loại khối '{loai_khoi}' và không có dòng mac_dinh — thêm dòng "
                         f"'{loai_khoi} = e2b' (hoặc may) hay 'mac_dinh = …' vào {duong}; hệ thống không tự chọn nơi chạy")
    return noi


def _e2b_ghi_so(dong: dict) -> None:
    """[ghi chú] Một dòng JSON vào sổ máy E2B (thư mục gắn từ máy chủ ./ket_qua/e2b_so, biến
    CHAY_THU_E2B_SO) — bền qua tạo lại hộp (log thì mất). sandbox/chay_s5.tien_luot đọc theo phiên. Thiếu biến / ghi lỗi ⇒ NÉM: dòng "tao" ghi
    TRƯỚC khi gọi E2B ⇒ không ghi được sổ thì KHÔNG tạo máy (không có máy nào ngoài sổ)."""
    import json as _json
    import time as _time
    d = (os.environ.get("CHAY_THU_E2B_SO") or "").strip()
    if not d:
        raise RuntimeError("E2B (chay_thu): thiếu CHAY_THU_E2B_SO (sổ máy E2B) — không tạo sandbox")
    dong = {"ts": _time.strftime("%Y-%m-%dT%H:%M:%S%z"), **dong}
    fd = os.open(os.path.join(d, "e2b_so.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (_json.dumps(dong, ensure_ascii=False) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _noi_chay_ghi_may(session, workflow, node) -> None:
    """[ghi chú] BẰNG CHỨNG lúc chạy: khối Chạy code được FILE NƠI CHẠY đặt ở hộp máy ⇒ MỘT dòng vào sổ riêng
    noi_chay_so.jsonl (cùng thư mục gắn với sổ máy E2B, biến CHAY_THU_E2B_SO). sandbox/chay_s5.py đọc: khối có dòng này thì KHÔNG bị coi là "thiếu dòng sổ máy"
    (trước: khối Chạy code không có dòng sổ máy ⇒ sổ cộng mức E2B tối đa ~0,05 USD — đúng khi sổ máy mất, OAN khi khối vốn chạy ở hộp máy).
    Vì sao file RIÊNG chứ không ghi vào e2b_so.jsonl: bản sổ cũ kiểm MỌI dòng của e2b_so.jsonl và ném "dòng hỏng" khi gặp loại lạ ⇒ lùi gói sẽ làm hỏng sổ.
    Vì sao dùng bằng chứng lúc chạy chứ không đọc file nơi chạy lúc ghi sổ: file có thể đã đổi sau lượt.
    Không ghi được ⇒ CHỈ cảnh báo, khối vẫn chạy ở hộp máy (không có tiền E2B nào để mất); sổ khi đó cộng mức tối đa như cũ (thà dư)."""
    import json as _json
    import time as _time
    try:
        d = (os.environ.get("CHAY_THU_E2B_SO") or "").strip()
        if not d or not session:
            raise RuntimeError("thiếu CHAY_THU_E2B_SO hoặc mã phiên")
        dong = {"ts": _time.strftime("%Y-%m-%dT%H:%M:%S%z"), "loai": "may", "session": str(session), "workflow": str(workflow), "node": str(node)}
        fd = os.open(os.path.join(d, "noi_chay_so.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (_json.dumps(dong, ensure_ascii=False) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception as e:  # noqa: BLE001
        logger.warning("[NOI_CHAY] không ghi được dòng 'may' vào sổ nơi chạy (%s) — sổ tiền sẽ cộng mức E2B tối đa cho khối này", type(e).__name__)


def _e2b_template() -> str:
    """Template E2B dùng cho khối (biến riêng; mặc định template công khai của SDK). Một nguồn cho cả tham số tạo máy lẫn lời dặn."""
    return (os.environ.get("CHAY_THU_E2B_TEMPLATE") or "code-interpreter-v1").strip()


def _e2b_gia() -> float:
    """[ghi chú] USD / giây của template đang dùng, đọc từ bảng MỘT nguồn va_e2b/gia_e2b.json (gắn ro, biến CHAY_THU_E2B_GIA).
    Đọc lỗi / template không có trong bảng ⇒ NÉM: không tạo máy khi sổ tiền không biết giá (template mới phải ĐO rồi mới thêm vào bảng)."""
    import json as _json
    duong = (os.environ.get("CHAY_THU_E2B_GIA") or "").strip()
    try:
        bang = _json.load(open(duong, encoding="utf-8"))["gia_giay"] if duong else {}
    except Exception:
        bang = {}
    t = _e2b_template()
    if t not in bang:
        raise RuntimeError(f"E2B (chay_thu): template {t!r} không có trong bảng giá E2B (CHAY_THU_E2B_GIA) — không tạo sandbox")
    return float(bang[t])


def _e2b_gia_an_toan() -> float:
    try:
        return _e2b_gia()
    except Exception:
        return 0.000046   # chỉ cho dòng log [E2B_USAGE]; sổ tiền tính theo sổ máy


def _e2b_khoa() -> str:
    """[ghi chú] Khoá E2B ở biến RIÊNG CHAY_THU_E2B_KHOA (env_file $CAPITOL_STATE_DIR, 600) — KHÔNG ở E2B_API_KEY chung ⇒ mọi đường E2B
    khác của Capitol (artifact_tools, run_tool_tools, offload chat A2A, SandboxManager gốc, skill) thấy khoá rỗng ⇒ tắt như cũ. Rào bằng máy cho
    "chỉ khối trong CHAY_THU_E2B_KHOI đi E2B" (E X3), không dựa vào việc lần đủ mọi đường. Không in, không ghi log giá trị."""
    return (os.environ.get("CHAY_THU_E2B_KHOA") or "").strip()


def _e2b_ttl() -> int:
    try:
        v = int(os.environ.get("CHAY_THU_E2B_TTL", "180"))
    except ValueError:
        v = 180
    return max(30, min(v, 180))   # [đã chốt 17/11] trần CỨNG 180 s = E2B_TTL_GIAY ở muc_toi_da ⇒ biến đặt cao hơn không vượt được mức giữ chỗ


def _e2b_ds_cho_phep() -> set[str]:
    """sha256 (64 hex, chữ thường) các file GIẢ LẬP được phép gửi lên E2B. Thiếu / đọc lỗi ⇒ tập rỗng ⇒ mọi file bị chặn (đóng khi lỗi)."""
    import re as _re
    duong = os.environ.get("CHAY_THU_E2B_DS", "")
    try:
        dong = open(duong, encoding="utf-8").read().splitlines() if duong else []
    except OSError:
        return set()
    return {x.split()[0].lower() for x in dong if x.strip() and not x.lstrip().startswith("#") and _re.fullmatch(r"[0-9a-fA-F]{64}", x.split()[0])}


# [VÁ chay_thu — B 19/11/2026 va_e2b_file_nguoi_dung; người vận hành: "các file này phục vụ cho vấn đề hỏi đáp, và rag … có thể gửi lên E2B và gửi cho claude qua
# bedrock nhé; được gửi hết nhé; vì đây là vấn đề tôi được phép và được quyết định" — AGENTS 3.2] File NGƯỜI DÙNG tải lên (kể cả file thật) được lên E2B.
# Công tắc CHAY_THU_E2B_FILE ở hộp worker: "gia_lap" (mặc định, thiếu biến = y như trước: chỉ file có sha trong danh sách giả lập) · "nguoi_dung" (file người dùng
# tải qua trang, đã qua rào C13 ở _ci_ran_truoc_khi_mo: đúng vùng file tải lên của CHÍNH tổ chức chạy lượt + không phải file khối khác sinh + cỡ ≤ TRAN_FILE_TAI).
# Chế độ "nguoi_dung" thêm 3 rào ở đây (so TRƯỚC khi tạo máy ảo và so lại ngay trước khi tải lên):
#   (1) LOẠI file: chỉ đuôi trong _E2B_DUOI_NGUOI_DUNG (tài liệu + bảng tính — đúng thứ khuôn máy ảo bậc 2 đọc được); (2) TÊN file không giống file khoá / cấu
#   hình bí mật (.env, *.pem, *.key, credentials…, id_rsa…); (3) NỘI DUNG không chứa mẫu khoá phổ biến (khối khoá riêng PEM, khoá AWS, khoá dạng sk-… / ghp_… /
#   e2b_…) — soát cả BÊN TRONG file nén Office (xlsx / docx / pptx). Giới hạn (3): khoá nằm trong PDF (dòng chữ bị nén) hay trong ảnh thì không thấy.
#   (4) [bản 2] mạng ra của máy ảo GIỮ theo quyết định người vận hành (rủi ro đã nêu); file người dùng lên máy ảo có mạng ra bật ⇒ ghi một dòng log E2B_FILE_NGUOI_DUNG.
# Giá trị lạ của công tắc ⇒ coi như "gia_lap" bị hỏng ⇒ CHẶN mọi file ngoài danh sách (đóng khi lỗi). Không đổi: khoá E2B không vào máy ảo, máy tự tắt ≤ TTL.
# [ghi chú] thêm ĐÚNG hai
# đuôi .html + .xlsb (KHÔNG thêm .htm / .xhtml / .xlsm / .mhtml). Danh sách này CHỈ dùng cho đường lên máy ảo E2B; cửa tải file của trang (may_chu._DUOI_NHAN_FILE),
# danh sách tải lên của ab-api (services/upload_allowlist.py), khung chat (kho_tai_lieu.DUOI_CHAT) là danh sách KHÁC, không đổi. Giữ nguyên: rào tên giống file khoá,
# trần 20.000.000 byte, khoá E2B không vào máy ảo, máy tự tắt. .xlsb là file NÉN như .xlsx ⇒ soát từng phần bên trong, không mở được ⇒ từ chối.
# DÒ KHOÁ LƯỢT PHỤ (phần siết, áp cho MỌI đuôi — lỗ có sẵn: .txt / .csv / phần trong .xlsx viết UTF-16 có khoá lọt; và sổ .xlsb lưu mọi chuỗi dạng UTF-16):
#   khi nội dung có byte 0, dò thêm trên BA cách nhìn — (1) bỏ mọi byte 0 (bắt cả UTF-32); (2)(3) giải mã UTF-16LE ở hai độ lệch chẵn / lẻ (lẻ = UTF-16BE), ký tự
#   ngoài ASCII thành dấu "?". Lượt phụ dùng luật ngữ cảnh nới hơn mẫu chính: CHO PHÉP MỘT ký tự rác dính ngay trước / ngay sau khoá — vì định dạng
#   nhị phân đặt số đo độ dài sát trước chuỗi (sổ .xlsb: 4 byte độ dài; chuỗi trong công thức: 2 byte) và số đó có thể là chữ / số (độ dài 48–57, 65–90, 95,
#   97–122) ⇒ mẫu chính đòi "ranh giới từ" sẽ trượt [phiên kiểm bắt ở vòng 1 — ca N7, phiếu tài liệu nội bộ]. Hai ký tự chữ / số dính trước
#   (vd "task-…", "risk-…") vẫn KHÔNG tính là khoá ⇒ không chặn oan chữ thường có gạch nối.
# Giới hạn ghi rõ: khoá viết trong .html bằng mã thực thể (&#115;…), base64, hay bị thẻ cắt giữa chừng; khoá trong PDF / ảnh / gói nén LỒNG trong file nén: KHÔNG thấy.
_E2B_DUOI_NGUOI_DUNG = (".csv", ".xlsx", ".pdf", ".docx", ".pptx", ".txt", ".md", ".html", ".xlsb")
_E2B_DUOI_NEN = (".xlsx", ".docx", ".pptx", ".xlsb")
_E2B_DO_DOAN, _E2B_DO_CHONG = 8 * 1024 * 1024, 4096   # lượt phụ đi từng đoạn 8 MB, chồng 4.096 byte (khoá nằm vắt hai đoạn vẫn thấy) ⇒ bộ nhớ thêm ≤ ~40 MB dù phần 150 MB
# [đã chốt 19/11] cỡ MỖI file người dùng lên E2B ≤ 20.000.000 byte = đúng rào của ô nhận file / tab Tài liệu của trang (một con số cho cả bộ; vượt ⇒ từ chối có báo).
# Chỉ áp khi khối chạy trên E2B VÀ công tắc = "nguoi_dung"; mọi đường khác (hộp phiên, khối Agent, chế độ giả lập) giữ 3.000.000 byte.
_E2B_TRAN_FILE_NGUOI_DUNG = 20_000_000
_E2B_NEN_SO_PHAN, _E2B_NEN_TONG = 5_000, 150_000_000   # [bản 2 — E H2] trần khi soát bên trong file nén Office: số phần · tổng byte sau giải nén (hộp worker trần RAM 1,6 GB ⇒ 150 MB, E gợi ý)


def _e2b_che_do_file() -> str:
    return (os.environ.get("CHAY_THU_E2B_FILE") or "gia_lap").strip().lower()


def _e2b_kiem_file_nguoi_dung(ten: str, noi_dung: bytes) -> None:
    """Ném ValueError lời thường nếu file người dùng KHÔNG được lên E2B (loại file / tên giống file khoá / nội dung có mẫu khoá)."""
    import re as _re

    goc = os.path.basename(str(ten or "")).strip()
    duoi = os.path.splitext(goc)[1].lower()
    # [bản 2 — H1: User quyết 19/11, nguyên văn "khi có file thật, máy ảo E2B có được ra Internet: có nhé, cho ra nhé" — rủi ro câu cài lệnh trong file xúi code gửi
    # file đi nơi khác ĐÃ nêu với User (A trình, tài liệu nội bộ H1)] ⇒ KHÔNG từ chối khi mạng ra bật; chỉ GHI NHẬN một dòng log để đối soát cuối ngày đếm được.
    if os.environ.get("CHAY_THU_E2B_MANG", "") == "bat":
        import logging as _lg
        _lg.getLogger("chay_thu.e2b").warning("E2B_FILE_NGUOI_DUNG mang_ra=bat file=%s co=%s", goc[:60], len(noi_dung or b""))
    if duoi not in _E2B_DUOI_NGUOI_DUNG:
        raise ValueError(f"E2B (chay_thu): file '{goc[:80]}' thuộc loại không gửi lên E2B (chỉ nhận: {', '.join(_E2B_DUOI_NGUOI_DUNG)})")
    ten_cam = _re.compile(r"(^|[^a-z0-9])(env|secret|secrets|credential|credentials|passwd|password|token|apikey|api[_-]?key|id_rsa|id_ed25519|private[_-]?key)([^a-z0-9]|$)", _re.IGNORECASE)
    if goc.startswith(".") or ten_cam.search(os.path.splitext(goc)[0]):
        raise ValueError(f"E2B (chay_thu): tên file '{goc[:80]}' giống file khoá / cấu hình bí mật ⇒ không gửi lên E2B")
    mau_khoa = _re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bASIA[0-9A-Z]{16}\b|aws_secret_access_key|\bsk-[A-Za-z0-9_-]{20,}|\bghp_[A-Za-z0-9]{30,}|\be2b_[0-9a-f]{20,}", _re.IGNORECASE)
    # [vòng 2] LƯỢT PHỤ. mau_loi = các mẫu khoá KHÔNG kèm điều kiện ngữ cảnh; ngữ cảnh xét bằng code ở _phu_khop: với khoá nhận theo đầu (AKIA / ASIA / sk- /
    # ghp_ / e2b_) chỉ BỎ QUA khi có từ HAI ký tự chữ / số dính ngay trước (chữ thường kiểu "task-…"); MỘT ký tự dính trước vẫn tính là khoá (số đo độ dài của định
    # dạng nhị phân). Lọc trước bằng phép tìm chuỗi con trên bản chữ thường (rất nhanh) ⇒ dữ liệu nhị phân không có dấu hiệu nào thì không chạy biểu thức chính quy.
    mau_loi = _re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|aws_secret_access_key|A[KS]IA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|e2b_[0-9a-f]{20,}", _re.IGNORECASE)
    dau_hieu = (b"-----begin", b"aws_secret_access_key", b"akia", b"asia", b"sk-", b"ghp_", b"e2b_")
    chu_so = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")

    def _phu_khop(v: bytes) -> bool:
        thuong = v.lower()
        if not any(d in thuong for d in dau_hieu):
            return False
        vt = 0
        while True:
            m = mau_loi.search(v, vt)
            if m is None:
                return False
            a, z = m.start(), m.end()
            dau = thuong[a:a + 4]
            if dau.startswith((b"----", b"aws_")):
                return True
            truoc_hai = a >= 2 and v[a - 1] in chu_so and v[a - 2] in chu_so
            sau_hai = dau in (b"akia", b"asia") and z + 1 < len(v) and v[z] in chu_so and v[z + 1] in chu_so
            if not truoc_hai and not sau_hai:
                return True
            vt = a + 1          # không nhảy qua cả đoạn vừa khớp: khoá thật có thể nằm ngay sau một dấu gạch nối bên trong đoạn đó

    def co_khoa(b: bytes) -> bool:
        if mau_khoa.search(b):
            return True
        if b"\x00" not in b:          # chữ thường / UTF-8 không có byte 0 ⇒ không có lượt phụ (hành vi + tốc độ như trước)
            return False
        mv = memoryview(b)
        for i in range(0, len(b), _E2B_DO_DOAN):
            d = bytes(mv[max(0, i - _E2B_DO_CHONG):i + _E2B_DO_DOAN])
            if _phu_khop(d.replace(b"\x00", b"")):
                return True
            for lech in (0, 1):
                x = d[lech:]
                if _phu_khop(x[:len(x) & ~1].decode("utf-16-le", "replace").encode("ascii", "replace")):
                    return True
        return False

    if co_khoa(noi_dung or b""):
        raise ValueError(f"E2B (chay_thu): nội dung file '{goc[:80]}' có đoạn giống KHOÁ bí mật ⇒ không gửi lên E2B (gỡ khoá khỏi file rồi tải lại)")
    # [bản 2 — E H2] .xlsx / .docx / .pptx là file NÉN: soát mẫu khoá trong TỪNG phần bên trong. Trần: ≤ _E2B_NEN_SO_PHAN phần, tổng giải nén ≤ _E2B_NEN_TONG byte;
    # không mở được như file nén, vượt trần, hay có phần mã hoá ⇒ không soát hết được ⇒ TỪ CHỐI (đóng khi lỗi). PDF và ảnh: KHÔNG soát bên trong (giới hạn ghi rõ).
    if duoi in _E2B_DUOI_NEN:
        import io as _io
        import zipfile as _zf
        try:
            with _zf.ZipFile(_io.BytesIO(noi_dung or b"")) as z:
                ds = z.infolist()
                if len(ds) > _E2B_NEN_SO_PHAN or sum(i.file_size for i in ds) > _E2B_NEN_TONG or any(i.flag_bits & 0x1 for i in ds):
                    raise ValueError("quá nhiều phần / quá lớn khi giải nén / có phần đặt mật khẩu")
                for i in ds:
                    with z.open(i) as f:
                        phan = f.read(_E2B_NEN_TONG + 1)
                    if len(phan) > _E2B_NEN_TONG:
                        raise ValueError("một phần lớn hơn cỡ ghi trong mục lục")
                    if co_khoa(phan):
                        raise ValueError("co_khoa")
        except ValueError as e:
            if str(e) == "co_khoa":
                raise ValueError(f"E2B (chay_thu): bên trong file '{goc[:80]}' có đoạn giống KHOÁ bí mật ⇒ không gửi lên E2B (gỡ khoá khỏi file rồi tải lại)") from None
            raise ValueError(f"E2B (chay_thu): không soát hết được bên trong file '{goc[:80]}' ({str(e)[:80]}) ⇒ không gửi lên E2B") from None
        except Exception as e:  # noqa: BLE001 — BadZipFile, lỗi giải nén…: không soát được ⇒ từ chối
            raise ValueError(f"E2B (chay_thu): file '{goc[:80]}' không mở được như file {duoi} ({type(e).__name__}) ⇒ không gửi lên E2B") from None


def _e2b_kiem_noi_dung(ten: str, noi_dung: bytes) -> None:
    import hashlib as _hl
    h = _hl.sha256(noi_dung or b"").hexdigest()
    if h in _e2b_ds_cho_phep():
        return
    if _e2b_che_do_file() == "nguoi_dung":
        _e2b_kiem_file_nguoi_dung(ten, noi_dung)
        return
    raise ValueError(f"E2B (chay_thu bậc 1): file '{ten}' không có trong danh sách file GIẢ LẬP được phép gửi lên E2B — chặn trước khi tải lên")


def e2b_cho_khoi_lam_file(loai_khoi) -> bool:
    """[bậc 2] Dịch vụ làm file hỏi: khối loại này có chạy trên máy ảo E2B không. False ⇒ hộp phiên trên máy như cũ. File nơi chạy ghi e2b mà thiếu khoá E2B ⇒ NÉM
    (báo lỗi rõ, không tự về hộp máy — luật điều phối chốt 19/11). File sai khuôn ⇒ _noi_chay_khoi tự ném."""
    if loai_khoi not in CHAY_THU_KHOI_LAM_FILE_E2B:
        return False
    if _noi_chay_khoi(loai_khoi) != "e2b":
        return False
    if not _e2b_khoa():
        raise RuntimeError("Khối này được đặt chạy trên máy ảo E2B (file nơi chạy khối) nhưng hộp chưa có khoá E2B — báo người quản trị; hệ thống không tự chuyển về hộp trên máy.")
    return True


class _E2bChayThu(SandboxManager):
    """[ghi chú] SandboxManager gốc + ép tham số mạng / thời gian / envs ở MỌI lần tạo (create và _respin cùng dùng hàm này, manager.py:441-469)."""

    @property
    def template_name(self) -> str:
        """[ghi chú] tên template E2B THẬT ⇒ lời dặn "gói có sẵn" (_build_dependency_note)
        theo đúng máy đang chạy. code-interpreter-v1 KHÔNG có trong bảng mẫu ⇒ lời dặn chung của Capitol — mục mẫu thật chỉ thêm khi đã ĐO."""
        return _e2b_template()

    async def create(self, packages=None):
        """= SandboxManager.create (manager.py:483-503) nhưng khoá lấy từ _e2b_khoa(), không từ settings.E2B_API_KEY (rỗng ở bộ thử)."""
        import time as _time
        from services.sandbox import manager as _mg
        if not _e2b_khoa():
            raise RuntimeError("E2B (chay_thu): thiếu CHAY_THU_E2B_KHOA — không tạo sandbox")
        logger.info(f"E2B (chay_thu) tạo sandbox (template={self.template_name}, timeout ép ≤ {_e2b_ttl()}s)")
        self._sandbox = await _mg.AsyncSandbox.create(**self._e2b_create_kwargs(""))
        self._sandbox_created_at = _time.monotonic()
        self._last_alive_at = self._sandbox_created_at
        logger.info(f"E2B (chay_thu) sandbox: {self._sandbox.sandbox_id}")
        self._set_sentry_tags()
        if packages:
            await self.install_packages(packages)
        return self

    async def cleanup(self) -> None:
        """Gốc (kill + cộng giây tính tiền, manager.py:1302-1327) rồi ghi MỘT dòng log [E2B_USAGE] cho sổ tiền: giây máy thật × giá cận trên
        2 vCPU + 4 GiB = 0,000046 USD/s (BAO_CAO_E2B.md mục 2; cấu hình thực đo ở ca thật đầu). Không có khoá / id bí mật trong dòng."""
        await super().cleanup()
        giay = float(getattr(self, "_accumulated_billable_seconds", 0.0) or 0.0)
        if getattr(self, "_ct_ma", None) and not getattr(self, "_ct_xong", False):
            self._ct_xong = True
            try:   # "xong": giây tính tiền (gồm mọi lần tạo lại) — lỗi ghi thì sổ vẫn có "tao" ⇒ chay_s5 tính TTL đầy (thà dư)
                _e2b_ghi_so({"loai": "xong", **self._e2b_so_chung(), "giay": round(giay, 1), "so_may": self._respin_count + 1})
            except Exception as e:
                logger.warning("[E2B_USAGE] không ghi được dòng xong vào sổ máy E2B: %s", type(e).__name__)
        logger.warning("[E2B_USAGE] workflow=%s node=%s template=%s giay=%.1f so_lan_tao_lai=%s usd_can_tren=%.6f",
                       self._workflow_id, self._node_id, self.template_name, giay, self._respin_count, giay * _e2b_gia_an_toan())

    async def _respin_impl(self) -> None:
        """[đã chốt 17/11] chỉ cho TẠO LẠI máy 1 lần (gốc 3, manager.py:700) ⇒ ≤ 2 máy / lần thử khối = E2B_MAY_TOI_DA ở muc_toi_da."""
        from services.sandbox import manager as _mg
        if self._respin_count >= 1:
            raise _mg.SandboxException(f"E2B (chay_thu): đã tạo lại {self._respin_count} lần (trần 1) — dừng")
        await super()._respin_impl()

    def _e2b_so_chung(self) -> dict:
        if not getattr(self, "_ct_ma", None):
            self._ct_ma = uuid4().hex
        return {"session": getattr(self, "_ct_session", None), "workflow": self._workflow_id, "node": self._node_id, "ma": self._ct_ma}

    def _e2b_create_kwargs(self, api_key: str) -> dict:
        # GỌI NGAY TRƯỚC AsyncSandbox.create ở cả create (dưới) lẫn _respin (manager.py:731) ⇒ mỗi máy E2B có đúng 1 dòng "tao" (đếm cả lần lỗi)
        if not getattr(self, "_ct_session", None):
            raise RuntimeError("E2B (chay_thu): thiếu mã phiên — sổ tiền không gán được máy cho lượt ⇒ không tạo sandbox")
        _gia = _e2b_gia()   # [ghi chú] TRƯỚC dòng sổ: template ngoài bảng ⇒ ném, không có dòng "tao" nào, không có máy
        _e2b_ghi_so({"loai": "tao", **self._e2b_so_chung(), "ttl": _e2b_ttl(), "template": _e2b_template(), "gia_giay": _gia})
        kw = super()._e2b_create_kwargs(api_key)
        kw["api_key"] = _e2b_khoa()   # _respin (manager.py:731) truyền settings.E2B_API_KEY (rỗng) ⇒ thay ở đây
        # Template E2B lấy từ biến RIÊNG (E2B_*TEMPLATE của bộ = "chay-thu-sandbox-py-v3:v3" là tên hộp phiên, không có trên E2B; đổi chúng thì lời dặn các
        #   khối hộp phiên sai). Bậc 1: template công khai mặc định của SDK; template tự viết = bậc sau. Giới hạn: lời dặn "gói có sẵn" của khối này
        #   vẫn theo E2B_CODE_INTERPRETER_TEMPLATE (v3) ⇒ đo ở ca thật đầu (import thử) rồi mới sửa.
        kw["template"] = _e2b_template()
        kw["timeout"] = min(int(kw.get("timeout") or _e2b_ttl()), _e2b_ttl())
        # [ghi chú] mạng RA bật được trong giai đoạn thử bằng CỜ (đúng chữ "bat"); mặc định / mọi giá trị khác ⇒ TẮT.
        #   Phải tắt lại trước khi có người dùng thật. Truy cập VÀO hộp từ Internet (allow_public_traffic) luôn tắt cứng.
        kw["allow_internet_access"] = os.environ.get("CHAY_THU_E2B_MANG", "") == "bat"
        net = dict(kw.get("network") or {})
        net["allow_public_traffic"] = False
        kw["network"] = net
        kw["envs"] = {k: v for k, v in (kw.get("envs") or {}).items() if k == "NODE_PATH"}
        return kw


def _ci_v1():
    """Hàm rào của V1 — gắn ở services/pydantic_ai_skills/e2b_chat_service.py (V5). Thiếu ⇒ lỗi rõ, KHÔNG chạy tiếp không rào."""
    try:
        from services.pydantic_ai_skills import e2b_chat_service as v1
        return v1._kiem_file_vao, v1._lam_sach_file_ra, v1._DUOI_RA_KHONG_NHAN
    except (ImportError, AttributeError) as exc:
        raise RuntimeError("hộp phiên (chay_thu): thiếu hàm rào của V1 (e2b_chat_service bản chay_thu) — không chạy khối chạy code") from exc


class _E2bLamFile(_E2bChayThu):
    """[bậc 2] Máy ảo E2B cho DỊCH VỤ LÀM FILE: mọi thứ của _E2bChayThu (khoá riêng, mạng theo cờ, không cho Internet vào, tự tắt ≤ CHAY_THU_E2B_TTL, tạo lại ≤ 1 lần, dòng
    "tao" / "xong" vào sổ máy, không biến môi trường nào của hộp vào máy ảo) + hai khác biệt: (1) ba biến thư mục làm việc mang GIÁ TRỊ CỐ ĐỊNH (dịch vụ + kỹ năng cần chúng;
    không phải bí mật, không lấy từ môi trường của hộp); (2) start_keepalive KHÔNG làm gì — dịch vụ gốc gọi nó để gia hạn máy vô hạn, ở đây trần thời gian = trần tiền."""
    _THU_MUC = {"INPUT_DIR": "/home/user/input", "OUTPUT_DIR": "/home/user/output", "WORK_DIR": "/home/user/work"}

    def _e2b_create_kwargs(self, api_key: str) -> dict:
        kw = super()._e2b_create_kwargs(api_key)
        kw["envs"] = {**(kw.get("envs") or {}), **self._THU_MUC}
        return kw

    async def start_keepalive(self, interval=None) -> None:
        return None


class SandboxMode(StrEnum):
    """When a node's execute() creates an E2B sandbox."""

    # No sandbox, ever — for agent nodes whose run strategy has no use
    # for code execution (Vercel Deploy: its agent stages text files and
    # the deploy call is hardcoded, never an LLM tool).
    NEVER = "never"
    # Sandbox is created only when input files are bound to the node
    # (the Agent-node semantic; also the default for every agent node
    # per the ENG-4437 decision that all agent nodes carry e2b tools).
    IF_FILES = "if_files"
    # Sandbox is created on every run (the code-interpreter semantic —
    # those nodes exist to execute code, files or not).
    ALWAYS = "always"


# Named entries that exist in the E2B /home/user/ directory by default and
# should not be treated as agent-generated output.
SANDBOX_SYSTEM_FILES: frozenset[str] = frozenset(
    {
        ".bashrc",
        ".bash_logout",
        ".profile",
        ".cache",
        ".local",
        ".config",
        ".ipython",
        ".jupyter",
        "output",
        "work",
    }
)

PARSER_SYSTEM_SNIPPET = """
IMPORTANT — Working with input files:\n
• Before processing any input file, preview its content (display the first few rows, pages, or records as appropriate).\n
• When extracting or parsing data from a file, always write the parsed output to a new text file (e.g., '/parsed_text.txt') 
• After parsing, read the parsed file incrementally (in small parts) rather than loading the entire file into memory at once.\n\n
"""

# Maps the run_code language to a file extension + MIME type so the persisted
# executed-source artifact is openable as the right file type.
_CODE_LANG_EXT = {
    "python": (".py", "text/x-python"),
    "javascript": (".js", "text/javascript"),
    "shell": (".sh", "text/x-shellscript"),
}


def offload_template_candidates() -> list[str | None]:
    """Template preference order for the on-demand offload sandbox.

    Configured templates first (light code-interpreter image, then the
    deployment default), the raw E2B SDK image strictly last — an empty
    template setting must never promote the SDK image ahead of a
    configured one. Deduplicated, ``None`` (SDK default) always present
    as the final fallback.
    """
    candidates: list[str | None] = []
    for template in (settings.E2B_CODE_INTERPRETER_TEMPLATE, settings.E2B_TEMPLATE):
        if template and template not in candidates:
            candidates.append(template)
    candidates.append(None)
    return candidates


class SandboxLifecycleMixin:
    """Internal building block of ``AgentBaseNode`` — not for standalone use."""

    _sandbox_mode: ClassVar[SandboxMode] = SandboxMode.IF_FILES
    # True reads the sandbox TTL from the node's ``sandbox_timeout`` param
    # (code-interpreter family); False uses ``_sandbox_default_timeout``.
    _sandbox_uses_timeout_param: ClassVar[bool] = False
    _sandbox_default_timeout: ClassVar[int] = 3600
    # True passes the node's ``language`` param as the sandbox tools'
    # default run_code language (code-interpreter family).
    _sandbox_language_param: ClassVar[bool] = False
    # True pre-installs the node's ``packages`` param at sandbox creation.
    _sandbox_packages_param: ClassVar[bool] = False
    # True records every run_code invocation and persists the combined
    # source as run provenance (code-interpreter family; additionally gated
    # by ``settings.CODE_INTERPRETER_PERSIST_EXECUTED_SOURCE``).
    _capture_executed_code: ClassVar[bool] = False
    # True bridges persisted output bytes into the content-addressed
    # artifact store (code-interpreter family).
    _persist_via_artifact_bridge: ClassVar[bool] = False
    _artifact_source_node_type: ClassVar[str] = "code_interpreter"
    # S3 layout for persisted outputs: ``None`` uses the org-scoped
    # ``upload_file_object`` layout (Agent family); a string prefix uses the
    # ``{prefix}/{uuid}/{filename}`` layout (code-interpreter family).
    _persist_s3_prefix: ClassVar[str | None] = None
    _executed_code_s3_prefix: ClassVar[str] = "code_interpreter/source"
    # True scans EVERY bound param for upstream files (Agent family — keeps
    # legacy ``File Input -> Task`` wires working); False reads only the
    # dedicated ``files`` port (code-interpreter family).
    _input_files_from_all_bound_params: ClassVar[bool] = True
    # True runs the Agent node's in-sandbox file probe and prepends the
    # parse-files-first user-prompt directive; False leaves prompt
    # surfacing to the node's ``_build_sandbox_manifest_prompt`` alone.
    _sandbox_prompt_probing: ClassVar[bool] = True

    # ------------------------------------------------------------------
    # Input-file extraction
    # ------------------------------------------------------------------

    @final
    async def _raw_bound_outputs(self, field_id: str) -> list[NodeOutput]:
        """Return the raw NodeOutput(s) bound to *field_id*, bypassing the
        runtime's param-value resolution.

        ``runtime.get_param_value`` unwraps non-file ``NodeOutput`` to
        ``.data``, losing ``NodeOutput.files``. We reach into the backing
        store directly to preserve file metadata (so legacy
        ``File Input -> Agent.Task`` wires still route into the sandbox).

        Supports both runtimes:
        - In-process ``NodeRuntime``: live lookup via
          ``connections.get_param_binding`` + ``data_store.retrieve``.
        - Temporal ``ActivityNodeRuntime``: values pre-resolved at dispatch
          and stored raw on ``_resolved_params`` (live ``NodeOutput`` via the
          envelope reconstruction in ``temporal/activities/_shared.py``).
        """
        runtime = getattr(self, "runtime", None)
        if runtime is None:
            return []

        raw_values: list[NodeOutput | list[NodeOutput]] = []

        # Temporal activity runtime — pre-resolved raw NodeOutputs.
        resolved_params = getattr(runtime, "_resolved_params", None)
        if resolved_params is not None:
            val = resolved_params.get(field_id)
            if val is None:
                return []
            raw_values = val if isinstance(val, list) else [val]
        else:
            # In-process runtime — walk bindings and hit the data store.
            connections = getattr(runtime, "connections", None)
            data_store = getattr(runtime, "data_store", None)
            if connections is None or data_store is None:
                return []
            try:
                bindings = connections.get_param_binding(self.node_id, field_id)
            except Exception as exc:
                logger.warning(f"Failed to get param bindings for '{field_id}': {exc}")
                return []
            for binding in bindings or []:
                data_ref = getattr(binding, "data_reference", None)
                if not data_ref:
                    continue
                try:
                    raw_values.append(await data_store.retrieve(data_ref))
                except Exception as exc:
                    logger.warning(
                        f"Failed to retrieve bound value for '{field_id}': {exc}"
                    )
                    continue

        outputs: list[NodeOutput] = []
        for v in raw_values:
            if isinstance(v, list):
                outputs.extend(x for x in v if isinstance(x, NodeOutput))
            elif isinstance(v, NodeOutput):
                outputs.append(v)
        return outputs

    @final
    async def _extract_input_files(self) -> list[NodeFile]:
        """Collect NodeFile objects from upstream nodes.

        Two strategies, selected by ``_input_files_from_all_bound_params``:

        * Agent family — scan every ``node_config`` field and pull raw
          ``NodeOutput``s via ``_raw_bound_outputs``, so legacy workflows
          wiring a File Input into the Task (``user_prompt``) field still get
          the file uploaded to the sandbox. Deduplicated across fields by
          (s3_key, name).
        * Code-interpreter family — read only the dedicated ``files`` port.
        """
        if not self._input_files_from_all_bound_params:
            files: list[NodeFile] = []
            inbound_outputs = await self._get_validated_inbound_node_output()
            for node_output in inbound_outputs.get("files", []):
                if isinstance(node_output, NodeOutput) and node_output.files:
                    for node_file in node_output.files:
                        if isinstance(node_file, NodeFile):
                            files.append(node_file)
                        elif isinstance(node_file, dict):
                            try:
                                files.append(NodeFile(**node_file))
                            except Exception as exc:
                                logger.warning(f"Failed to parse file dict: {exc}")
            return files

        files = []
        seen: set[tuple[str | None, str]] = set()
        seen_fields: set[str] = set()

        for param_config in self.node_config:
            field_id = param_config.get("field_id")
            if not field_id or field_id in seen_fields:
                continue
            seen_fields.add(field_id)

            for node_output in await self._raw_bound_outputs(field_id):
                if not node_output.files:
                    continue
                for node_file in node_output.files:
                    if isinstance(node_file, dict):
                        try:
                            node_file = NodeFile(**node_file)
                        except Exception as exc:
                            logger.warning(
                                f"Failed to parse file dict on field '{field_id}': {exc}"
                            )
                            continue
                    if not isinstance(node_file, NodeFile):
                        continue
                    key = (getattr(node_file, "s3_key", None), node_file.name)
                    if key in seen:
                        continue
                    seen.add(key)
                    files.append(node_file)
                    if field_id != "files":
                        logger.info(
                            f"Picked up NodeFile '{node_file.name}' from bound field "
                            f"'{field_id}' (not the dedicated files port); routing to sandbox."
                        )
        return files

    # ------------------------------------------------------------------
    # Sandbox creation / teardown
    # ------------------------------------------------------------------

    # [chay_thu CI] ------------------------------------------------------------------------------------------------------------
    def _ci_dung_hop_phien(self) -> bool:
        """Khối ALWAYS (họ chạy code) + cờ CHAY_THU_HOP_PHIEN bật ⇒ hộp phiên thay E2B. Họ Agent IF_FILES: KHÔNG (vá 10 vẫn cấm E2B).
        [ghi chú] loại khối được bật E2B (_e2b_dung) ⇒ KHÔNG dùng hộp phiên."""
        return self._sandbox_mode == SandboxMode.ALWAYS and _ci_hop_phien_bat() and not self._e2b_dung()

    def _e2b_dung(self) -> bool:
        """[ghi chú] Khối ALWAYS hoặc khối IF_FILES có dòng trong file nơi chạy (agent_node…) ⇒ sandbox E2B (đã ép mạng / thời gian / file)."""
        if self._sandbox_mode not in (SandboxMode.ALWAYS, SandboxMode.IF_FILES):
            return False
        if not hasattr(self, "_ct_e2b_dung"):   # [va_noi_chay] quyết MỘT lần cho mỗi lần chạy khối (file đổi giữa chừng không làm khối nửa nơi này nửa nơi kia)
            loai = getattr(type(self), "node_id", None)
            cau_dao_cu = _e2b_khoi_bat(getattr(self, "node_sub_type", None))   # cầu dao cũ (chưa có file / khối ngoài danh sách)
            cau_dao_chung = bool(os.environ.get("CHAY_THU_E2B_KHOI", "").strip()) and bool(_e2b_khoa())
            noi = _noi_chay_khoi(loai)      # loại khối đọc trên LỚP (node_id của ĐỐI TƯỢNG là mã nút — bài học 18/11)
            self._ct_noi_file = noi                                          # [va_noi_chay_b2] None | "e2b" | "may" — "may" ⇒ _setup_sandbox ghi dòng bằng chứng cho sổ
            if noi is None:
                self._ct_e2b_dung = (self._sandbox_mode == SandboxMode.ALWAYS) and cau_dao_cu
            elif noi == "e2b":
                if not cau_dao_chung:
                    raise RuntimeError("Khối này được đặt chạy trên máy ảo E2B (file nơi chạy khối) nhưng cầu dao E2B đang tắt (biến CHAY_THU_E2B_KHOI hoặc khoá E2B) — "
                                       "hệ thống KHÔNG tự chuyển về hộp trên máy. Báo người quản trị.")
                self._ct_e2b_dung = True
            else:
                self._ct_e2b_dung = False                                    # "may" ⇒ hộp phiên trên máy, kể cả khi cầu dao bật
        return self._ct_e2b_dung

    def _ci_ran_truoc_khi_mo(self, input_files: list[NodeFile]) -> None:
        """[chay_thu CI] Rào TRƯỚC khi mở hộp phiên: R12 trong khối (không MCP / khối công cụ), không cài gói (hộp không mạng), C13 file vào."""
        p = self._validated_params or {}
        mcp = p.get("mcp_servers") or {}
        ds_mcp = mcp if isinstance(mcp, list) else (mcp.get("mcp_servers", []) if isinstance(mcp, dict) else [mcp])
        cong_cu = [n for n in (getattr(self, "handle_nodes", None) or []) if n is not None]
        if [s for s in ds_mcp if s] or cong_cu:
            raise ValueError("hộp phiên (chay_thu): khối chạy code có máy chủ MCP / khối công cụ nối vào ⇒ từ chối (chạy code + đường ra "
                             "ngoài trong cùng khối — R12). Đặt khối tra cứu TRƯỚC khối chạy code.")
        if self._sandbox_preinstall_packages():
            raise ValueError("hộp phiên (chay_thu): hộp không có mạng — không cài gói (bỏ ô Packages; image có pandas / openpyxl / matplotlib)")
        kiem_file_vao, _, _ = _ci_v1()
        tran_e2b = _E2B_TRAN_FILE_NGUOI_DUNG if (self._e2b_dung() and _e2b_che_do_file() == "nguoi_dung") else None   # [ghi chú] file người dùng lên E2B: trần cỡ riêng
        for nf in input_files:
            if tran_e2b:
                kiem_file_vao(nf, getattr(self, "_orgid", None), tran_e2b)
            else:
                kiem_file_vao(nf, getattr(self, "_orgid", None))

    def _ci_khoa_va_lam_sach(self, filename: str, content: bytes) -> tuple[str, bytes]:
        """[chay_thu CI — C14] Khoá S3 file ra có mã tổ chức + không nhận trang web + làm sạch xlsx / csv (hàm của V1)."""
        from nodes.agent.native_tools import _MA_TO_CHUC

        _, lam_sach, duoi_cam = _ci_v1()
        ext = os.path.splitext(filename)[1].lower()
        if ext in duoi_cam:
            raise ValueError(f"hộp phiên (chay_thu): không nhận file ra dạng trang web ({ext})")
        org = getattr(self, "_orgid", None) or ""
        if not _MA_TO_CHUC.match(org):
            raise ValueError("hộp phiên (chay_thu): không rõ tổ chức của lượt ⇒ không lưu file ra")
        return f"{self._persist_s3_prefix}/{org}/{uuid4()}/{filename}", lam_sach(ext, content)

    @final
    def _resolve_sandbox_timeout(self) -> int:
        if not self._sandbox_uses_timeout_param:
            return self._sandbox_default_timeout
        raw = self._validated_params.get(
            "sandbox_timeout", self._sandbox_default_timeout
        )
        return int(raw) if raw not in ("", None) else self._sandbox_default_timeout

    @final
    def _sandbox_manager_kwargs(self) -> dict[str, Any]:
        """Shared ``SandboxManager`` constructor kwargs (identity + timeout).

        Single home for the kwargs both creation paths (``_setup_sandbox``
        and ``_ensure_offload_sandbox``) assemble, so a new constructor
        argument only has to be remembered in one place. ``template`` is
        deliberately NOT included: ``_setup_sandbox`` must preserve the
        manager's ``_UNSET`` semantics by omitting the kwarg entirely when
        routing selected no template.
        """
        return {
            "timeout": self._resolve_sandbox_timeout(),
            "node_type": self.node_type,
            "node_sub_type": self.node_sub_type,
            "node_id": self.node_id,
            "workflow_id": self._workflowid,
        }

    @final
    def _resolve_sandbox_template(self) -> str | None:
        # All nodes now share capai-base-image (the unified floor), so no file
        # extension or keyword routing is needed. Return the deployment default
        # so each workspace pins its own version via Terraform.
        return settings.E2B_TEMPLATE or None

    @final
    def _sandbox_preinstall_packages(self) -> list[str]:
        if not self._sandbox_packages_param:
            return []
        packages_raw = self._validated_params.get("packages", "")
        return [p.strip() for p in (packages_raw or "").split("\n") if p.strip()]

    @final
    async def _setup_sandbox(self, input_files: list[NodeFile]) -> set[str]:
        """Create the sandbox, upload input files, build sandbox tools.

        S3 downloads run concurrently with the sandbox boot — both are
        network-bound and independent, so parallelising saves the full
        file-download wall time (typically 5-30 s). Duplicate filenames are
        disambiguated with an index prefix instead of silently overwriting.

        Returns the set of (deduplicated) input file names uploaded to
        ``/home/user/``. The keepalive is started before returning and runs
        until ``_teardown_sandbox``.
        """
        template = self._resolve_sandbox_template()
        packages = self._sandbox_preinstall_packages()

        if input_files:
            await self.emit_progress(
                f"Creating sandbox for {len(input_files)} input file(s)..."
            )
        else:
            await self.emit_progress("Creating code execution sandbox...")

        manager_kwargs: dict[str, Any] = self._sandbox_manager_kwargs()
        # ``SandboxManager`` distinguishes template OMITTED (falls back to
        # ``settings.E2B_TEMPLATE``) from template=None (forces the raw E2B
        # base image) via an ``_UNSET`` sentinel — only pass the kwarg when
        # routing actually selected a template.
        if template is not None:
            manager_kwargs["template"] = template
        if self._ci_dung_hop_phien():            # [chay_thu CI] hộp phiên (không mạng) thay E2B — rào trước khi mở
            self._ci_ran_truoc_khi_mo(input_files)
            if getattr(self, "_ct_noi_file", None) == "may":   # [va_noi_chay_b2] CHỈ khi FILE đặt "may" (cầu dao tắt / chưa có file ⇒ không ghi ⇒ sổ như cũ)
                _noi_chay_ghi_may(getattr(self, "_session_id", None), manager_kwargs.get("workflow_id"), manager_kwargs.get("node_id"))
            from services.sandbox.hop_phien import HopPhienManager

            self._sandbox = HopPhienManager(**manager_kwargs)
        elif self._e2b_dung():                   # [ghi chú] rào như hộp phiên (R12, không cài gói, C13) + sha file giả lập TRƯỚC khi tạo
            self._ci_ran_truoc_khi_mo(input_files)
            for nf in input_files:
                _e2b_kiem_noi_dung(os.path.basename(nf.name), await asyncio.to_thread(nf.download_file_content))
            _ma_tinh = getattr(self, "_ct_ma_file_e2b", None)
            if _ma_tinh:
                _e2b_kiem_noi_dung(_ma_tinh[0], _ma_tinh[1])
            manager_kwargs["timeout"] = min(int(manager_kwargs.get("timeout") or _e2b_ttl()), _e2b_ttl())
            self._sandbox = _E2bChayThu(**manager_kwargs)
            self._sandbox._ct_session = getattr(self, "_session_id", None)   # sổ máy E2B theo phiên (chay_s5.tien_luot)
        else:
            self._sandbox = SandboxManager(**manager_kwargs)
        # A full-lifecycle sandbox, not an offload-only one: output
        # persistence keeps its complete scan behaviour (ENG-4646).
        self._sandbox_offload_only = False
        self._sandbox_file_manifest = []
        input_file_names: set[str] = set()

        sandbox_task: asyncio.Task | None = None
        download_tasks: list[asyncio.Task[bytes]] = []
        try:
            if input_files:
                await self.emit_progress(
                    f"Creating sandbox and downloading {len(input_files)} file(s)..."
                )
            sandbox_task = asyncio.create_task(
                self._sandbox.create(packages=packages if packages else None)
            )
            download_tasks = [
                asyncio.create_task(asyncio.to_thread(nf.download_file_content))
                for nf in input_files
            ]
            try:
                await asyncio.gather(sandbox_task, *download_tasks)
            except Exception:
                for task in [sandbox_task, *download_tasks]:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    sandbox_task,
                    *download_tasks,
                    return_exceptions=True,
                )
                raise
            file_contents = [t.result() for t in download_tasks]
            if self._e2b_dung():                 # [ghi chú] so lại sha ngay trước khi tải lên (nội dung có thể đổi giữa hai lần tải)
                for nf, content in zip(input_files, file_contents):
                    _e2b_kiem_noi_dung(os.path.basename(nf.name), content)

            if template:
                await self.emit_progress(f"Sandbox ready (template: {template})")

            # Pre-compute safe names (sequential — duplicate detection
            # is stateful) then upload all files concurrently alongside
            # mkdir. For N files this collapses N serial uploads into a
            # single parallel fan-out.
            upload_plan: list[tuple[str, bytes, str | None]] = []
            for i, (nf, content) in enumerate(zip(input_files, file_contents)):
                base_name = os.path.basename(nf.name)
                safe_name = (
                    base_name
                    if base_name not in input_file_names
                    else f"{i}_{base_name}"
                )
                input_file_names.add(safe_name)
                sandbox_path = f"/home/user/{safe_name}"
                self._sandbox_file_manifest.append(
                    (sandbox_path, getattr(nf, "mime_type", None))
                )
                upload_plan.append(
                    (sandbox_path, content, getattr(nf, "mime_type", None))
                )
            _ma_tinh = getattr(self, "_ct_ma_file_e2b", None)
            if self._e2b_dung() and _ma_tinh and _ma_tinh[0] not in input_file_names:
                _safe_tinh = _ma_tinh[0]
                input_file_names.add(_safe_tinh)
                _sandbox_path = f"/home/user/{_safe_tinh}"
                self._sandbox_file_manifest.append((_sandbox_path, None))
                upload_plan.append((_sandbox_path, _ma_tinh[1], None))

            if upload_plan:
                await self.emit_progress(
                    f"Uploading {len(upload_plan)} file(s) to sandbox..."
                )
            upload_results = await asyncio.gather(
                self._sandbox.run_shell(
                    f"mkdir -p {SANDBOX_OUTPUT_DIR} {SANDBOX_WORK_DIR}"
                ),
                *[
                    self._sandbox.upload_file(path, content)
                    for path, content, _ in upload_plan
                ],
                return_exceptions=True,
            )
            upload_errors = [r for r in upload_results if isinstance(r, BaseException)]
            if upload_errors:
                raise upload_errors[0]
        except BaseException:
            # A failed boot/download/upload must not leak pending tasks.
            pending = [
                t
                for t in [sandbox_task, *download_tasks]
                if t is not None and not t.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise

        # Sandbox is live from here (create + uploads succeeded) — record it
        # for the audit-honest ``sandbox_used`` output metadata (ENG-4646).
        self._sandbox_was_used = True

        tool_kwargs: dict[str, Any] = {"emit_progress": self.emit_progress}
        if self._sandbox_language_param:
            tool_kwargs["default_language"] = self._validated_params.get(
                "language", "python"
            )
        if self._capture_executed_code:
            self._executed_code = []
            tool_kwargs["code_sink"] = lambda lang, code: self._executed_code.append(
                (lang, code)
            )
        self._sandbox_tools = make_sandbox_tools(self._sandbox, **tool_kwargs)

        if input_file_names:
            await self.emit_progress(
                "Files ready in sandbox: " + ", ".join(input_file_names)
            )

        # Standardised keepalive (ENG-4437): periodically extend the sandbox
        # TTL for the whole run — including persist steps and the
        # output-feedback review pause — so a long tool-silent model turn
        # can never let the sandbox expire and silently lose the agent's
        # work-in-progress files. Stopped by ``_teardown_sandbox``.
        if not self._e2b_dung():                 # [ghi chú] KHÔNG keepalive ⇒ E2B tự tắt máy ở TTL (trần thời gian = trần tiền)
            await self._sandbox.start_keepalive()

        # Pre-flight probe: classify each file and detect scanned
        # PDFs so the user-prompt directive can give the agent
        # concrete, file-specific instructions (and a content
        # preview the model can't ignore).
        if self._sandbox_prompt_probing and input_file_names:
            await self._probe_sandbox_files()

        return input_file_names

    @final
    async def _ensure_offload_sandbox(self) -> SandboxManager:
        """Return the node's sandbox, creating a lightweight one on demand.

        The on-demand path exists for the tool-result offloader
        (ENG-4646): nodes that ran without input files have no sandbox,
        but an oversized tool result needs somewhere to land. The
        sandbox is created lazily on the FIRST oversized result — runs
        that never produce one never pay the boot latency or the E2B
        cost — and reuses the node's existing sandbox when one is
        already live (file-processing runs, code-interpreter family).

        Prefers the light code-interpreter template (pandas/numpy
        pre-installed, no OCR stack) for fast boots, then falls back to
        the deployment default template and finally the E2B SDK default
        image (``offload_template_candidates``) — not every deployment's
        E2B account carries both custom templates, and a missing template
        must degrade to a working sandbox rather than a dead offload
        path. Torn down by the standard ``_teardown_sandbox`` in
        ``execute()``'s ``finally``.

        Serialised behind a lock so parallel oversized tool calls in the
        same run coalesce onto a single sandbox boot (the presence check
        deliberately sits INSIDE the lock: an unlocked fast-path could
        observe a mid-boot manager and hand out an uncreated sandbox).

        Cancellation-safe like ``_setup_sandbox``: ``self._sandbox`` is
        assigned BEFORE ``create()`` so a Temporal cancel delivered
        mid-boot is cleaned up — and its billable seconds recorded — by
        ``_teardown_sandbox`` instead of orphaning a sandbox until its
        TTL.
        """
        async with self._offload_sandbox_lock:
            if self._sandbox is not None:
                return self._sandbox
            if _ci_hop_phien_bat():
                # [ghi chú] khối KHÔNG có hộp (vd agent_node có tra web) + kết quả công cụ quá lớn ⇒ gốc
                # dựng E2B CÓ MẠNG ở đây (vá 10 + R12 không thấy đường này). Máy thử: KHÔNG dựng — ném ⇒ bộ offload "fail-open" theo thiết kế
                # (services/sandbox/tool_result_offload.py:28-31, :382-390) cắt giữa kết quả thay vì giữ file. Tắt cờ ⇒ như gốc.
                raise RuntimeError("máy thử (chay_thu): không dựng hộp E2B để giữ kết quả công cụ quá lớn — kết quả sẽ bị cắt giữa")
            await self.emit_progress(
                "Creating code sandbox to hold a large tool result..."
            )
            candidates = offload_template_candidates()
            last_error: str | None = None
            for template in candidates:
                manager = SandboxManager(
                    **self._sandbox_manager_kwargs(), template=template
                )
                # Assigned pre-create for cancellation safety (see docstring).
                self._sandbox = manager
                try:
                    await manager.create()
                    # Probe the code kernel: run_code is the agent's primary
                    # way to explore the offloaded file, and a template can
                    # boot fine while its Jupyter port never opens (observed
                    # with stale custom-template builds). Catching it here
                    # costs one ~1s round-trip on a rare path and lets the
                    # ladder advance to a working image.
                    probe = await manager.run_code("print('ok')", timeout=60)
                    if probe.error or "ok" not in (probe.stdout or ""):
                        raise RuntimeError(
                            f"kernel probe failed: {probe.error or probe.stderr}"
                        )
                except asyncio.CancelledError:
                    # ``self._sandbox`` stays assigned: execute()'s finally
                    # tears it down and records cost.
                    raise
                except Exception as exc:
                    last_error = str(exc)
                    self._sandbox = None
                    logger.warning(
                        "Offload sandbox unusable with template=%r (%s); "
                        "trying next candidate",
                        template or "default",
                        exc,
                    )
                    try:
                        await manager.cleanup()
                    except Exception:  # pragma: no cover - best-effort kill
                        pass
                    continue
                await manager.start_keepalive()
                # Offload-only: created to hold tool results, so output
                # persistence must not sweep the agent's exploration
                # scratch out of /home/user as user-facing files.
                self._sandbox_offload_only = True
                self._sandbox_was_used = True
                return manager
            raise RuntimeError(
                f"Could not create a usable offload sandbox with any template "
                f"({[c or 'default' for c in candidates]}); last error: "
                f"{last_error}"
            )

    @final
    async def _teardown_sandbox(self) -> None:
        if self._sandbox is None:
            return
        try:
            await self._sandbox.stop_keepalive()
        finally:
            await self._sandbox.cleanup()
            self._sandbox = None

    # ------------------------------------------------------------------
    # In-sandbox file probing + prompt directives (Agent-family style)
    # ------------------------------------------------------------------

    def _build_dependency_note(self, *, system_prompt: bool = False) -> str:
        """Tell the agent what's available in the sandbox.

        Reads the capability manifest from the template registry so the note
        is always in sync with the actual image. When the active template is
        not in the registry (or no sandbox has been created yet), falls back
        to generic install guidance so the agent can still do its job.
        """
        tpl = self._sandbox.template_name if self._sandbox else ""
        registry_note = build_capability_note(tpl, allow_extra_installs=True)
        if registry_note:
            return registry_note

        # Fallback: template unknown or sandbox not yet created.
        if system_prompt:
            return (
                "### Required installs in this sandbox\n\n"
                "pandas / numpy / matplotlib / scipy are pre-installed, "
                "but the PDF/OCR stack is NOT. Install per task before "
                "use:\n\n"
                "- For any PDF work: "
                "`install_packages(['pymupdf', 'pdfplumber'])`\n"
                "- For OCR (scanned PDF or image): first the system "
                "package — "
                "`install_packages(['tesseract-ocr', 'tesseract-ocr-eng', "
                "'poppler-utils'], manager='apt')` — then the Python "
                "side — `install_packages(['pytesseract', 'pillow'])`. "
                "The apt step takes ~60–180 s; that is normal.\n"
                "- For Excel: "
                "`install_packages(['openpyxl', 'xlrd'])`\n"
                "- For Word/PowerPoint: "
                "`install_packages(['python-docx', 'python-pptx'])`\n\n"
                "Run the install once at the start of your code session "
                "and reuse the imports across subsequent `run_code` "
                "calls. Do not refuse the task because of a missing "
                "dependency — install it."
            )
        return (
            "Required installs in this sandbox (one-time, at the start "
            "of your code session): "
            "`install_packages(['pymupdf', 'pdfplumber'])` for any PDF "
            "work; "
            "`install_packages(['tesseract-ocr', 'tesseract-ocr-eng', "
            "'poppler-utils'], manager='apt')` then "
            "`install_packages(['pytesseract', 'pillow'])` for OCR / "
            "scanned PDFs / images; "
            "`install_packages(['openpyxl', 'xlrd'])` for Excel; "
            "`install_packages(['python-docx', 'python-pptx'])` for "
            "Word/PowerPoint. The apt step may take ~60–180 s; that is "
            "normal."
        )

    @final
    async def _probe_sandbox_files(self) -> None:
        """Run a one-shot probe inside the sandbox to classify uploaded files.

        For each file in ``self._sandbox_file_manifest`` records ``kind``,
        ``size``, ``preview``, and (PDF only) ``has_text_layer`` (False →
        scanned, needs OCR), ``has_tables`` (use ``extract_tables`` over naive
        text), and ``looks_financial`` (keyword match — EYP's main workload is
        financial docs, so this biases toward table-extraction recipes).

        Best-effort — on failure we log and move on; the prompt directives
        still cover the agent.
        """
        if not self._sandbox or not self._sandbox_file_manifest:
            return

        paths = [p for p, _ in self._sandbox_file_manifest]
        await self.emit_progress(f"Probing {len(paths)} input file(s)...")

        if not self._sandbox.uses_custom_template:
            # Default E2B image lacks pymupdf/pdfplumber; install once so
            # the probe can report has_text_layer / looks_financial.
            try:
                await self._sandbox.install_packages(["pymupdf", "pdfplumber"])
            except Exception as exc:
                logger.warning(f"Pre-flight PDF library install failed: {exc}")

        probe_code = (
            "import json, os, mimetypes, re\n"
            f"PATHS = {paths!r}\n"
            "TEXT_EXTS = {'.txt','.md','.csv','.tsv','.json','.html','.htm',"
            "'.xml','.py','.js','.ts','.yaml','.yml','.log'}\n"
            "IMAGE_EXTS = {'.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp','.gif'}\n"
            # Heuristic keywords for financial reports. Hits on UK / US "
            # filings, audit reports, board packs, fund reports, etc.\n"
            "FIN_KEYWORDS = re.compile(r'\\b(balance sheet|income statement|"
            "profit and loss|cash flow|statement of (financial position|"
            "comprehensive income|changes in equity)|consolidated|"
            "auditor|net assets|shareholders.? funds?|ebitda|revenue|"
            "directors.? report|notes to the (financial )?statements|"
            "fiscal year|fy20|trial balance|general ledger)\\b', re.I)\n"
            "out = {}\n"
            "for p in PATHS:\n"
            "    info = {'path': p}\n"
            "    try:\n"
            "        info['size'] = os.path.getsize(p)\n"
            "    except Exception as e:\n"
            "        info['error'] = f'stat failed: {e}'\n"
            "        out[p] = info\n"
            "        continue\n"
            "    ext = os.path.splitext(p)[1].lower()\n"
            "    info['ext'] = ext\n"
            "    if ext == '.pdf':\n"
            "        info['kind'] = 'pdf'\n"
            "        try:\n"
            "            import fitz\n"
            "            doc = fitz.open(p)\n"
            "            info['pages'] = doc.page_count\n"
            "            # Sample up to 3 pages — cover/title pages often lack\n"
            "            # text even when the body has a text layer.\n"
            "            sample = ''\n"
            "            for i in range(min(3, doc.page_count)):\n"
            "                sample += doc[i].get_text() or ''\n"
            "                if len(sample) > 4000: break\n"
            "            info['has_text_layer'] = bool(sample.strip())\n"
            "            info['preview'] = sample[:2000]\n"
            "            info['looks_financial'] = bool("
            "FIN_KEYWORDS.search(sample))\n"
            "            doc.close()\n"
            "        except Exception as e:\n"
            "            info['error'] = f'pymupdf failed: {e}'\n"
            "        if info.get('has_text_layer'):\n"
            "            try:\n"
            "                import pdfplumber\n"
            "                with pdfplumber.open(p) as pl:\n"
            "                    found = 0\n"
            "                    for pg in pl.pages[:3]:\n"
            "                        tables = pg.extract_tables() or []\n"
            "                        found += sum(1 for t in tables "
            "if t and len(t) > 1 and len(t[0]) > 1)\n"
            "                    info['has_tables'] = bool(found)\n"
            "                    info['table_count_sample'] = found\n"
            "            except Exception as e:\n"
            "                info['table_probe_error'] = str(e)\n"
            "    elif ext in IMAGE_EXTS:\n"
            "        info['kind'] = 'image'\n"
            "        info['needs_ocr'] = True\n"
            "    elif ext in TEXT_EXTS:\n"
            "        info['kind'] = 'text'\n"
            "        try:\n"
            "            with open(p, 'rb') as fh:\n"
            "                raw = fh.read(4096)\n"
            "            info['preview'] = raw.decode('utf-8', errors='replace')\n"
            "        except Exception as e:\n"
            "            info['error'] = f'read failed: {e}'\n"
            "    elif ext in {'.xlsx', '.xls'}:\n"
            "        info['kind'] = 'spreadsheet'\n"
            "    elif ext == '.docx':\n"
            "        info['kind'] = 'docx'\n"
            "    elif ext == '.parquet':\n"
            "        info['kind'] = 'parquet'\n"
            "    else:\n"
            "        info['kind'] = mimetypes.guess_type(p)[0] or 'binary'\n"
            "    out[p] = info\n"
            "print(json.dumps(out))\n"
        )

        try:
            result = await self._sandbox.run_code(probe_code, timeout=120)
            if result.error:
                logger.warning(
                    f"Sandbox file probe returned error: {result.error[:300]}"
                )
                return
            import json

            self._sandbox_file_probes = json.loads(result.stdout.strip() or "{}")
            scanned = [
                p
                for p, info in self._sandbox_file_probes.items()
                if info.get("kind") == "pdf" and info.get("has_text_layer") is False
            ]
            if scanned:
                names = ", ".join(os.path.basename(p) for p in scanned)
                await self.emit_progress(
                    f"Scanned PDF detected (no text layer): {names} — OCR will be required"
                )
            tabular = [
                p
                for p, info in self._sandbox_file_probes.items()
                if info.get("has_tables") is True
            ]
            if tabular:
                names = ", ".join(os.path.basename(p) for p in tabular)
                await self.emit_progress(
                    f"Tables detected in: {names} — pdfplumber.extract_tables() recommended"
                )
        except Exception as exc:
            logger.warning(f"Sandbox file probe failed: {exc}", exc_info=True)

    @final
    def _build_user_prompt_files_directive(self) -> str:
        """Build a forced directive prepended to the user prompt when files
        are connected.

        Models attend to user-message content more strongly than system
        prompts, so this is the hardest enforcement layer. Keeps it tight:
        file list with probe findings, a recipe table, and operational
        rules. No install instructions — the template has everything.
        """
        if not self._sandbox_prompt_probing or not self._sandbox_file_manifest:
            return ""

        lines: list[str] = [
            "[ATTACHED FILES — REQUIRED FIRST STEP]",
            "",
            "Files are in your sandbox at `/home/user/`. "
            "Parse them with `run_code` / `read_file` before answering. "
            "Do not skip a file.",
            "",
        ]

        for path, mime in self._sandbox_file_manifest:
            info = self._sandbox_file_probes.get(path) or {}
            kind = info.get("kind") or (mime or "unknown")
            size = info.get("size")
            size_str = f", {size} bytes" if isinstance(size, int) else ""
            entry = f"- `{path}` ({kind}{size_str}"

            if kind == "pdf":
                pages = info.get("pages")
                if pages is not None:
                    entry += f", {pages} pages"
                if info.get("has_text_layer") is True:
                    entry += ", text layer: YES"
                elif info.get("has_text_layer") is False:
                    entry += ", text layer: NO (scanned)"
                if info.get("has_tables") is True:
                    entry += ", tables: YES"
                if info.get("looks_financial") is True:
                    entry += ", financial doc: likely"
            entry += ")"

            preview = info.get("preview")
            if preview:
                snippet = preview.strip().replace("\n", " ")
                if len(snippet) > 300:
                    snippet = snippet[:300] + "..."
                entry += f"\n    Preview: {snippet!r}"

            lines.append(entry)

        lines.extend(
            [
                "",
                "### Recipe by file shape",
                "",
                "| File shape | Recipe |",
                "|---|---|",
                "| PDF, text + tables | `pdfplumber.extract_tables()` → "
                "`pd.DataFrame` |",
                "| PDF, text only | `pdftotext -layout` for large files; "
                "`pdfplumber.extract_text()` otherwise |",
                "| PDF, scanned (no text layer) | `fitz` → "
                "`get_pixmap(dpi=300)` → `pytesseract.image_to_string`; "
                "for tabular scans use `image_to_data` + reconstruct from "
                "`top`/`left` |",
                "| Image | `pytesseract.image_to_string` |",
                "| `.xlsx` / `.xls` | `pandas.read_excel(path, sheet_name=None)` |",
                "| `.csv` / `.tsv` | `pandas.read_csv` |",
                "| `.parquet` | `pandas.read_parquet` |",
                "| `.docx` | `docx.Document(path)` |",
                "| `.pptx` | `pptx.Presentation(path)` |",
                "| text / json / html | `read_file` directly |",
                "",
                self._build_dependency_note(),
                "",
                "For long-running ops, report progress by printing lines "
                f"starting with `{TASK_PROGRESS_SENTINEL}` (with "
                "`flush=True`) — these are shown to the user live, so keep "
                "each one to a short status line and never put document "
                "content in them; unmarked output is not streamed and is "
                "returned in the tool result. "
                f"Save output files to `{SANDBOX_OUTPUT_DIR}/` with short, "
                "descriptive filenames derived from their content "
                "(e.g. `Q3-Revenue-Summary.xlsx`), not generic names like "
                "`output.xlsx`. Put anything you produce along the way -- "
                "page renders, format conversions, scripts, intermediate "
                f"drafts -- in `{SANDBOX_WORK_DIR}/` instead; only "
                f"`{SANDBOX_OUTPUT_DIR}/` is returned.",
                "",
                "[USER TASK]",
            ]
        )
        return "\n".join(lines)

    def _build_sandbox_manifest_prompt(self) -> str:
        """System-prompt snippet listing files uploaded to the sandbox.

        OVERRIDABLE HOOK (the code interpreter substitutes its
        template-aware library note). Keeps the model aware that files
        exist even when the user's Task doesn't name them explicitly.
        Tool-selection recipes live in the user-prompt directive
        (``_build_user_prompt_files_directive``), so this only needs the
        file list and behavioral rules.
        """
        if not self._sandbox_file_manifest:
            return ""

        lines = [
            "## Sandbox files",
            "",
            "These files are in your E2B sandbox. When the user refers "
            'to "the document" or "the spreadsheet", match by name/type.',
            "",
        ]
        for path, mime in self._sandbox_file_manifest:
            suffix = f" ({mime})" if mime else ""
            lines.append(f"- `{path}`{suffix}")
        lines.extend(
            [
                "",
                "### You MUST extract and use the contents of these files",
                "",
                "These files were attached because the user expects you to "
                "use them. Your first action should be to read or parse the "
                "relevant file(s) using `run_code` / `read_file`. Do NOT "
                "answer from prior knowledge, do NOT ask the user to paste "
                "contents, and do NOT call web search before you have "
                "actually read the file.",
                "",
                self._build_dependency_note(system_prompt=True),
                "",
                "Verify a file exists before reporting it missing. "
                f"Save output files to `{SANDBOX_OUTPUT_DIR}/` with short, "
                "descriptive filenames derived from their content "
                "(e.g. `Q3-Revenue-Summary.xlsx`), not generic names like "
                "`output.xlsx`. Put anything you produce along the way -- "
                "page renders, format conversions, scripts, intermediate "
                f"drafts -- in `{SANDBOX_WORK_DIR}/` instead; only "
                f"`{SANDBOX_OUTPUT_DIR}/` is returned.",
            ]
        )
        return "\n".join(lines)

    @final
    def _build_sandbox_basemaps_prompt(self) -> str:
        """System-prompt section pointing the model at the CARTO basemaps key.

        Only when this run has a sandbox: the key is an env var inside it,
        so a node that never created one has nothing to point at. Empty
        when no key is configured (see ``services.sandbox.basemaps``).
        """
        if self._sandbox is None:
            return ""
        return build_basemaps_note()

    # ------------------------------------------------------------------
    # Output persistence
    # ------------------------------------------------------------------

    @final
    async def _persist_sandbox_files(
        self,
        input_file_names: set[str] | None = None,
    ) -> list[NodeFile]:
        """Collect output files from the sandbox and upload to S3.

        Strategy:
        1. Recursively list the output dir (the canonical one).
        2. If nothing found there, scan /home/user/ for any non-system files
           the agent may have created outside the output dir (excluding the
           original input files, at their staged /home/user/<name> paths, to
           avoid re-uploading them).

        The S3 layout is per-node (``_persist_s3_prefix``): the Agent family
        uses the org-scoped ``upload_file_object`` layout; the
        code-interpreter family keeps its historical
        ``{prefix}/{uuid}/{filename}`` layout. Both stamp ``content_hash`` /
        ``size_bytes`` on the resulting ``NodeFile`` and optionally bridge
        the real bytes into the content-addressed artifact store.
        """
        if not self._sandbox:
            logger.warning("_persist_sandbox_files called with no sandbox")
            return []

        file_paths: list[str] = []

        await self.emit_progress("Scanning sandbox for output files...")

        try:
            file_paths = await self._sandbox.list_files_recursive(SANDBOX_OUTPUT_DIR)
            logger.info(
                f"Found {len(file_paths)} file(s) in {SANDBOX_OUTPUT_DIR}/: {file_paths}"
            )
            if file_paths:
                await self.emit_progress(
                    f"Found {len(file_paths)} file(s) in {SANDBOX_OUTPUT_DIR}/: "
                    + ", ".join(os.path.basename(p) for p in file_paths)
                )
        except Exception as exc:
            logger.warning(f"Failed to list {SANDBOX_OUTPUT_DIR}: {exc}", exc_info=True)
            await self.emit_progress(f"Could not list {SANDBOX_OUTPUT_DIR}: {exc}")

        if not file_paths and getattr(self, "_sandbox_offload_only", False):
            # Offload-only sandbox (ENG-4646): it exists to hold oversized
            # tool results and the agent's exploration scratch. The
            # canonical output-dir scan above still honours files
            # the agent DELIBERATELY saved as outputs, but the fallback
            # whole-home sweep would turn pandas intermediates into
            # user-facing download cards on a node that never produced
            # files before — skip it.
            await self.emit_progress("No output files found in sandbox")
            logger.info(
                "_persist_sandbox_files: offload-only sandbox, skipping "
                "the /home/user fallback scan"
            )
            return []

        if not file_paths:
            await self.emit_progress(
                "Output dir empty, scanning /home/user/ for files..."
            )
            try:
                all_home = await self._sandbox.list_files_recursive("/home/user")
                logger.info(f"All files in /home/user: {all_home}")
                # Inputs are staged flat in /home/user (``_setup_sandbox``),
                # so only that exact path is an original upload. Matching on
                # the basename anywhere in the tree would also drop the
                # model's own copy of an input — which in the edit-in-place
                # case IS the deliverable it left behind (ENG-4780).
                _input_paths = {
                    f"/home/user/{name}" for name in (input_file_names or set())
                }
                file_paths = [
                    p
                    for p in all_home
                    if os.path.basename(p) not in SANDBOX_SYSTEM_FILES
                    and not p.startswith("/home/user/.")
                    and p not in _input_paths
                    # Offloaded tool results (ENG-4646) are working data
                    # staged for the agent, never node output — sweeping
                    # them up would upload multi-MB raw tool dumps to S3
                    # as user-facing files.
                    and not p.startswith(f"{OFFLOAD_DIR}/")
                    # Baked skill toolchains (ENG-3970) ship inside
                    # capai-base-image, not something the agent produced --
                    # sweeping them up would upload every baked skill file as
                    # a user-facing download card.
                    and not p.startswith(f"{SKILLS_BAKED_DIR}/")
                    # SANDBOX_WORK_DIR is deliberately NOT excluded: unlike
                    # offloaded tool results, a file there may be the
                    # deliverable the model forgot to copy out (ENG-4780).
                    # See services/sandbox/paths.py for the rule.
                ]
                logger.info(f"After filtering: {file_paths}")
                if file_paths:
                    await self.emit_progress(
                        f"Found {len(file_paths)} file(s): "
                        + ", ".join(os.path.basename(p) for p in file_paths)
                    )
            except Exception as exc:
                logger.warning(f"Failed to list /home/user: {exc}", exc_info=True)
                await self.emit_progress(f"Could not list /home/user: {exc}")

        if not file_paths:
            await self.emit_progress("No output files found in sandbox")
            logger.info("_persist_sandbox_files: no files to persist")
            return []

        await self.emit_progress(f"Uploading {len(file_paths)} file(s) to storage...")

        s3_service = S3Service()
        bucket = settings.resolve_s3_bucket(generated=True)
        node_files: list[NodeFile] = []

        for abs_path in file_paths:
            filename = os.path.basename(abs_path)
            try:
                content = await self._sandbox.download_file(abs_path)
                ci_khoa = None
                if self._persist_s3_prefix is not None and (self._ci_dung_hop_phien() or self._e2b_dung()):   # [chay_thu CI — C14] trước khi tính hash / tải lên · [ghi chú] cả E2B
                    ci_khoa, content = self._ci_khoa_va_lam_sach(filename, content)
                content_type = detect_content_type(filename)
                content_hash = S3Service.compute_content_hash(content)

                if self._persist_s3_prefix is not None:
                    # Historical code-interpreter layout.
                    s3_key = ci_khoa or f"{self._persist_s3_prefix}/{uuid4()}/{filename}"   # [chay_thu CI] có mã tổ chức khi hộp phiên
                    s3_service.upload_file(
                        file=BytesIO(content),
                        bucket_name=bucket,
                        key=s3_key,
                        extras={"ContentType": content_type},
                    )
                    s3_region = settings.AWS_S3_REGION or settings.AWS_REGION
                else:
                    # Org-scoped layout (Agent family).
                    s3_service.bucket_name = bucket
                    upload_result = await asyncio.to_thread(
                        s3_service.upload_file_object,
                        file_obj=BytesIO(content),
                        filename=filename,
                        content_type=content_type,
                        orgid=self._orgid or "unknown",
                        workflow_id=self._workflowid or None,
                        node_id=self.node_id or None,
                        metadata={"generated_by": f"{self.node_id}_sandbox"},
                    )
                    s3_key = upload_result["s3_key"]
                    s3_region = upload_result["s3_region"]

                # Friendly download name: keep the model's sandbox filename when
                # meaningful, otherwise fall back to a content-reflective name
                # (node name → task topic → node type) so a random/hash name
                # never reaches the user. Extension is preserved from the sandbox
                # filename. Computed before the presign so it can be bound into
                # the URL's Content-Disposition (friendly name, not UUID key).
                display_name = coerce_friendly_filename(
                    filename,
                    fallback_label=self._output_fallback_label(),
                    default_extension="",
                )

                presigned_url = s3_service.generate_presigned_url(
                    s3_key=s3_key,
                    bucket_name=bucket,
                    filename=display_name,
                )
                node_file = NodeFile(
                    name=display_name,
                    presigned_url=presigned_url,
                    mime_type=content_type,
                    s3_bucket=bucket,
                    s3_key=s3_key,
                    s3_region=s3_region,
                    generated=True,
                    content_hash=content_hash,
                    size_bytes=len(content),
                )
                node_files.append(node_file)
                logger.info(
                    f"Persisted {abs_path} -> s3://{bucket}/{s3_key} ({content_type})"
                )
                await self.emit_progress(f"Uploaded {filename} ({content_type})")

                if self._persist_via_artifact_bridge:
                    # Bridge the REAL output bytes into the content-addressed
                    # artifact store so they can be retrieved/reused later. This
                    # is the reliable path: relying on the LLM to inline base64
                    # into the save_artifact MCP tool stored placeholder strings
                    # (e.g. "fib_b64"), not images. Best-effort — a store hiccup
                    # must not fail the node.
                    try:
                        receipt = await register_artifact_bytes(
                            orgid=self._orgid,
                            data=content,
                            filename=filename,
                            mime_type=content_type,
                            workflow_id=self._workflowid or None,
                            run_id=self._session_id or None,
                            node_instance_id=self.node_id or None,
                            user_id=self._user_id or None,
                            source_node_type=self._artifact_source_node_type,
                        )
                        if receipt and receipt.get("artifact_id"):
                            # Stamp the id on the NodeFile so downstream steps
                            # and rerun reuse can reference the registered blob.
                            node_file.blob_store_artifact_id = receipt["artifact_id"]
                            logger.info(
                                f"Registered artifact {receipt['artifact_id']} "
                                f"({filename}, {len(content)} bytes) in artifact store"
                            )
                    except Exception as exc:
                        logger.warning(
                            f"Artifact-store bridge failed for {filename}: {exc}",
                            exc_info=True,
                        )
            except Exception as e:
                logger.warning(f"Failed to persist file {abs_path}: {e}", exc_info=True)
                await self.emit_progress(f"Failed to upload {filename}: {e}")

        logger.info(f"_persist_sandbox_files returning {len(node_files)} NodeFile(s)")
        return node_files

    @final
    async def _persist_executed_code(self) -> NodeFile | None:
        """Persist the agent's executed source as a content-addressed ``code``
        artifact before the sandbox is torn down.

        Historically the executed script was lost the moment the run ended
        (the sandbox filesystem is killed on cleanup). We capture every
        ``run_code`` invocation and store the combined source in the generated
        bucket with a sha256 content hash.

        This is run provenance, not a node output: the caller surfaces the
        returned ``NodeFile`` through ``NodeOutput.metadata`` and must never
        append it to ``NodeOutput.files``. Off unless the node opts in via
        ``_capture_executed_code`` AND
        ``CODE_INTERPRETER_PERSIST_EXECUTED_SOURCE`` is enabled.
        """
        if not self._capture_executed_code:
            return None
        if not settings.CODE_INTERPRETER_PERSIST_EXECUTED_SOURCE:
            return None
        snippets: list[tuple[str, str]] = getattr(self, "_executed_code", []) or []
        if not snippets:
            return None

        languages = {lang for lang, _ in snippets}
        if len(languages) == 1:
            ext, mime_type = _CODE_LANG_EXT.get(
                next(iter(languages)), (".txt", "text/plain")
            )
        else:
            ext, mime_type = (".txt", "text/plain")

        if len(snippets) == 1:
            source = snippets[0][1]
        else:
            blocks = []
            for idx, (lang, code) in enumerate(snippets, start=1):
                blocks.append(f"# --- run_code #{idx} ({lang}) ---\n{code}")
            source = "\n\n".join(blocks)

        content = source.encode("utf-8")
        content_hash = S3Service.compute_content_hash(content)
        filename = f"executed_code{ext}"
        s3_key = f"{self._executed_code_s3_prefix}/{uuid4()}/{filename}"

        try:
            s3 = S3Service()
            bucket = settings.resolve_s3_bucket(generated=True)
            s3.upload_file(
                file=BytesIO(content),
                bucket_name=bucket,
                key=s3_key,
                extras={"ContentType": mime_type},
            )
            presigned_url = s3.generate_presigned_url(s3_key=s3_key, bucket_name=bucket)
            logger.info(
                f"Persisted executed source ({len(snippets)} snippet(s)) -> "
                f"s3://{bucket}/{s3_key}"
            )
            await self.emit_progress(
                f"Saved executed source ({len(snippets)} run_code call(s))"
            )
            return NodeFile(
                name=filename,
                presigned_url=presigned_url,
                mime_type=mime_type,
                s3_bucket=bucket,
                s3_key=s3_key,
                s3_region=settings.AWS_S3_REGION or settings.AWS_REGION,
                generated=True,
                content_hash=content_hash,
                size_bytes=len(content),
            )
        except Exception as e:
            logger.warning(f"Failed to persist executed code: {e}", exc_info=True)
            await self.emit_progress(f"Failed to save executed source: {e}")
            return None
