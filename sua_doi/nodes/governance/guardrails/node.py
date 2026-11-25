import json
import logging
import os
import re
from typing import Any

from sentry_sdk import capture_exception

from nodes.governance.guardrails.params import PARAMS
from nodes.governance.guardrails.ports import OUTPUT_PORTS
from services.custom_guardrail_check.custom_guardrail_check_service import (
    CustomGuardrailCheckService,
)
from workflow.node.base.node import BaseNode, NodeOutput
from workflow.node.base.param.custom import JsonDisplay
from workflow.node.registry import register

logger = logging.getLogger(__name__)


def _normalize_guardrail_entry(entry: Any) -> dict:
    """Coerce a bare-string guardrail ID to the canonical dict shape."""
    if isinstance(entry, dict):
        return entry
    guardrail_id = str(entry) if entry is not None else ""
    logger.warning(
        "Coercing bare-string guardrail entry %r to dict",
        guardrail_id,
    )
    return {"option_id": guardrail_id, "option_display_name": guardrail_id}


# [VÁ "RÀO CHẮN CHẶN THẬT" chay_thu 07/11/2026 — nhóm kỹ thuật, theo thiết kế D BAO_CAO_KET_QUA.md mục 32.3]
# Vì sao: bản gốc chấm xong LUÔN emit_success + đưa bảng điểm ra cổng (dòng gốc :155-170) và không chỗ nào trong AB đọc
# guardrail_passed (D 32.1) ⇒ "rào chắn" chỉ chấm, không chặn (đã chạy 06/11, KET_QUA_S5 R1/R2). Vá: cờ bật thì trượt ⇒ NodeOutput.error
# ⇒ _shared.py ném NodeReportedError ⇒ Temporal fail-fast dừng cả lượt (D 32.3 ý 2). Cờ tắt ⇒ đi đúng đường gốc từng dòng (để còn so).
_CO_CHAN = "CHAY_THU_RAO_CHAN_CHAN"
_CAU_BAO_AN_TOAN = "Guardrail blocked: nội dung không qua rào chắn (xem bảng điểm ở khối rào chắn)."
_MAX_CAU_BAO = 600


def _co_chan_bat() -> bool:
    """Đọc cờ MỖI LẦN chạy (không lúc nạp module) ⇒ đổi env + tạo lại hộp là đủ, và phép thử đặt / bỏ cờ được trong cùng tiến trình."""
    return os.environ.get(_CO_CHAN, "").strip().lower() in ("1", "true", "yes", "co")


# D m3 (mục 34): cờ mặc định tắt ⇒ quên đặt biến là rào TẮT lặng lẽ ⇒ in trạng thái một lần lúc nạp module (grep được trong log hộp).
logger.warning("rào chắn chặn: %s (%s=%r)", "BẬT" if _co_chan_bat() else "TẮT", _CO_CHAN, os.environ.get(_CO_CHAN))


def _tom_tat_log(guardrail_ids: list[str], results: Any) -> str:
    """D m1 (mục 34): một dòng log khi chặn — MÃ LUẬT + LOẠI trượt, KHÔNG có nội dung khách và KHÔNG có lý do AI viết
    (lý do AI viết có thể trích nguyên câu của khách; nó vẫn nằm ở bảng điểm / emit_error)."""
    items = list(results.values()) if isinstance(results, dict) else results
    if not guardrail_ids:
        return "khong_co_luat"
    if not isinstance(items, list) or len(items) < len(guardrail_ids):
        return f"ket_qua_la(so_luat={len(guardrail_ids)})"
    out = []
    for r in items:
        if not isinstance(r, dict):
            out.append("?:khuon_la")
        elif r.get("guardrail_check_status") == "success":
            out.append(f"{r.get('id')}:{'dat' if r.get('guardrail_passed') is True else 'truot'}")
        else:
            out.append(f"{r.get('id')}:loi_cham")
    return ",".join(out)


def _ket_qua_truot(guardrail_ids: list[str], results: Any) -> list[str]:
    """Trả danh sách lý do TRƯỢT (rỗng = đạt). ĐÓNG KHI LỖI (D 32.3 ý 1):
    - không có luật nào được chấm;
    - luật chấm xong mà guardrail_passed không phải True;
    - luật lỗi (lấy luật / AI chấm lỗi — bản gốc cho qua);
    - kết quả lạ (không phải danh sách dict, thiếu luật so với guardrail_ids).
    Bỏ qua `override` (luôn False — chủ đề 12 mục 2.2 ý 1)."""
    if not guardrail_ids:
        return ["không có luật nào được chọn"]
    # Service trả list[dict] (custom_guardrail_check_service.py:252-255); nhận cả dict {mã: kết quả} cho chắc (D 32.1 ghi dict)
    items = list(results.values()) if isinstance(results, dict) else results
    if not isinstance(items, list) or len(items) < len(guardrail_ids):
        return ["kết quả chấm không đủ / không đúng khuôn"]
    ly_do = []
    for r in items:
        if not isinstance(r, dict):
            ly_do.append("kết quả chấm không đúng khuôn")
            continue
        ten = str(r.get("name") or r.get("id") or "?")
        if r.get("guardrail_check_status") == "success":
            if r.get("guardrail_passed") is not True:
                ly_do.append(f"{ten}: {r.get('reason') or 'không đạt'}")
        else:
            ly_do.append(f"{ten}: không chấm được ({r.get('error_message') or r.get('guardrail_check_status') or 'lỗi'})")
    return ly_do


def _cau_bao_chan(ly_do: list[str]) -> str:
    """Câu báo cho NodeOutput.error. Phải được Temporal xếp KHÔNG THỬ LẠI — thử lại = gọi AI chấm lần nữa (tốn tiền, có thể ra kết quả khác).
    Lý do do AI viết / câu lỗi mạng có thể chứa chữ bộ xếp lỗi coi là lỗi tạm ("connection error", "timed out", "HTTP 503"…;
    temporal/errors/classification.py:232-300, 437-470) ⇒ hỏi CHÍNH bộ xếp lỗi đó; nó bảo "thử lại được" (hoặc không hỏi được)
    ⇒ dùng câu cố định không có lý do. Lý do vẫn nằm ở bảng điểm (display_model) + emit_error."""
    cau = ("Guardrail blocked: " + "; ".join(ly_do))[:_MAX_CAU_BAO]
    try:
        from temporal.errors.classification import classify_exception
        from temporal.errors.node_errors import NodeReportedError

        if classify_exception(NodeReportedError(cau)).non_retryable:
            return cau
    except Exception:  # noqa: BLE001 — không kiểm được thì chọn câu an toàn
        logger.warning("guardrails: không kiểm được câu báo bằng bộ xếp lỗi ⇒ dùng câu cố định", exc_info=True)
    return _CAU_BAO_AN_TOAN


# [ghi chú] CHẾ ĐỘ "code": khối gọi một dịch vụ kiểm bằng code (backend KPI mekongai — phép kiểm
# kiem_so: số trong câu trả lời phải có nguồn, đúng người), KHÔNG dùng AI. Vì sao ở khối Rào chắn: Capitol không có khối nào "gọi công cụ mà không
# qua AI"; Rào chắn đã đứng đúng chỗ (sau Agent, trước Người duyệt) và đã có đường DỪNG lượt (NodeOutput.error ⇒ fail-fast, vá 07/11 của B).
# Luật: chế độ code LUÔN chặn khi trượt (không phụ thuộc cờ CHAY_THU_RAO_CHAN_CHAN — quên cờ không được mở cổng); ĐÓNG KHI LỖI: chưa cấu hình,
# không gọi được, hết giờ, HTTP ≠ 200, không phải JSON, quá cỡ, thiếu trường ⇒ trượt. Chỉ `dat is True` là đạt.
# Địa chỉ, kiểu gọi, khoá: CHỈ từ biến môi trường của hộp (không từ payload — payload sửa được ⇒ SSRF / gửi khoá đi nơi khác).
# Khuôn [chốt với mekongai 10/11, E + B]: vào {"answer": chữ, "question": chữ tuỳ chọn} ⇒ ra {"dat": bool, "loi": [{"loai", "chi_tiet"}],
# "ly_do": chữ, "phien_ban": …, [bản 4/5: vào thêm "run_id" = self._run_id (WorkflowRun.id)], "bang_thuong": {table backend}, "sha256_bang": hex64} (TÍNH gộp vào KIỂM — A 10/11). Khối chỉ có 1 cổng vào kiểu chữ ⇒ v1 chỉ gửi "answer" (không có câu hỏi, không có lịch sử gọi công cụ).
_KIEM_CODE_URL = "CHAY_THU_KIEM_CODE_URL"          # vd http://kpi-api:8770/kiem_so (tên hộp nội bộ; không cho người dùng đặt)
_KIEM_CODE_KIEU = "CHAY_THU_KIEM_CODE_KIEU"        # "http" (mặc định). Đổi đường gọi (vd MCP) ⇒ thêm nhánh trong _goi_kiem_code, giữ khuôn ra
_KIEM_CODE_GIAY = "CHAY_THU_KIEM_CODE_GIAY"        # trần chờ, giây (mặc định 10, tối đa 60)
_KIEM_CODE_KHOA_TEN = "CHAY_THU_KIEM_CODE_KHOA_TEN"  # tên header khoá (vd X-Kpi-Sys-Key); giá trị ở biến dưới — không bao giờ ghi log
_KIEM_CODE_KHOA = "CHAY_THU_KIEM_CODE_KHOA"
_KIEM_CODE_MAX_RA = 1024 * 1024
_KIEM_CODE_MAX_VAO = 200_000
_MA_HE_THONG = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")   # [bản 6] dạng mã lượt / tổ chức backend nhận (a5: 1–128 ký tự chữ / số / _ . : -)


def _che_do(validated_params: dict) -> str:
    """Thiếu / rỗng ⇒ "ai" (workflow cũ giữ đúng hành vi). Giá trị lạ ⇒ "la" (bị chặn — không đoán)."""
    v = validated_params.get("che_do_kiem")
    v = "" if v is None else str(v).strip().lower()
    return "ai" if v in ("", "ai") else (v if v in ("code", "sql") else "la")   # [va_kiem_so_sql C 12/11] + "sql"


async def _goi_kiem_code(noi_dung: str, run_id: str, org_id: str, cau_hoi: str = "", data_version: str = "") -> dict:
    """Lớp gọi ra ngoài DUY NHẤT của chế độ code. Trả JSON dịch vụ (dict) hoặc ném lỗi (người gọi coi mọi lỗi là trượt)."""
    url = os.environ.get(_KIEM_CODE_URL, "").strip()
    kieu = os.environ.get(_KIEM_CODE_KIEU, "http").strip().lower() or "http"
    if not url:
        raise RuntimeError(f"chưa cấu hình dịch vụ kiểm ({_KIEM_CODE_URL} trống)")
    if not url.startswith(("http://", "https://")):
        raise RuntimeError("địa chỉ dịch vụ kiểm không phải http(s)")
    try:
        giay = min(60.0, max(1.0, float(os.environ.get(_KIEM_CODE_GIAY, "10"))))
    except ValueError:
        giay = 10.0
    if len(noi_dung) + len(cau_hoi or "") > _KIEM_CODE_MAX_VAO:   # [lam_lai] trần tính CẢ câu hỏi
        raise RuntimeError(f"nội dung cần kiểm quá {_KIEM_CODE_MAX_VAO} ký tự")
    than_gui = {"answer": noi_dung, "run_id": run_id, "org_id": org_id}
    if cau_hoi:   # [lam_lai C 10/11] trường `question` (mekongai main.py:719 nhận sẵn, không bắt buộc); rỗng ⇒ KHÔNG gửi (thân y bản 6)
        than_gui["question"] = cau_hoi
    if kieu != "http":
        raise RuntimeError(f"kiểu gọi {kieu!r} chưa hỗ trợ")
    import httpx

    headers = {"Content-Type": "application/json"}
    ten, khoa = os.environ.get(_KIEM_CODE_KHOA_TEN, "").strip(), os.environ.get(_KIEM_CODE_KHOA, "")
    if ten and khoa:
        headers[ten] = khoa
    async def goi() -> tuple[int, bytes]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(giay), follow_redirects=False, trust_env=False) as c:
            async with c.stream("POST", url, json=than_gui, headers=headers,
                                params={"data_version": data_version} if data_version else None) as r:   # [va_data_version]
                if r.status_code != 200:
                    return r.status_code, b""
                than = bytearray()
                async for khuc in r.aiter_bytes():   # [D m3] trần TRONG LÚC đọc, không đọc hết rồi mới so
                    than += khuc
                    if len(than) > _KIEM_CODE_MAX_RA:
                        raise RuntimeError("kết quả dịch vụ kiểm quá lớn")
                return 200, bytes(than)

    import asyncio

    try:
        ma, than = await asyncio.wait_for(goi(), timeout=giay)   # [D m2] trần TỔNG cả lần gọi (httpx.Timeout là trần từng bước)
    except asyncio.TimeoutError:
        raise RuntimeError(f"dịch vụ kiểm quá {giay:g} giây") from None
    if ma != 200:
        raise RuntimeError(f"dịch vụ kiểm trả HTTP {ma}")
    kq = json.loads(than)
    if not isinstance(kq, dict):
        raise RuntimeError("kết quả dịch vụ kiểm không phải object JSON")
    return kq


def _cau_hoi_tu_params(vp: dict) -> str:
    """[lam_lai C 10/11] Ô `cau_hoi`: chữ ⇒ giữ; danh sách ⇒ nối "\n"; kiểu khác / thiếu ⇒ "" (không đoán)."""
    v = (vp or {}).get("cau_hoi")
    if isinstance(v, list):
        v = "\n".join(str(x) for x in v)
    return v.strip() if isinstance(v, str) else ""


async def tu_kiem_kpi(cau_tra_loi: str, cau_hoi: str, run_id: str, org_id: str, data_version: str = "") -> list[str]:
    """[lam_lai C 10/11 — đã chốt P1] Cho khối Agent TỰ KIỂM trước khi đưa câu sang Rào chắn — CHỈ để gợi ý làm lại, KHÔNG phải cổng
    (Rào chắn vẫn kiểm lại từ đầu, luật không đổi). Cùng lớp gọi + cùng cách đọc kết quả với Rào chắn (không viết lại lần 2).
    Trả lý do trượt ĐÚNG như dịch vụ trả (A điều kiện c — không thêm dữ liệu khác); rỗng = đạt. Lỗi / hết giờ ⇒ NÉM (người gọi: không làm lại)."""
    if not _MA_HE_THONG.match(run_id or "") or not _MA_HE_THONG.match(org_id or ""):
        raise RuntimeError("không có mã lượt / tổ chức hợp lệ")
    return _ly_do_kiem_code(await _goi_kiem_code(cau_tra_loi, run_id, org_id, cau_hoi, data_version))


def _ly_do_kiem_code(kq: dict) -> list[str]:
    """Rỗng = đạt. Chỉ `dat is True` (đúng kiểu bool) là đạt."""
    if kq.get("dat") is True:
        return []
    ly_do = [str(kq.get("ly_do") or "dịch vụ kiểm không trả đạt")[:300]]
    for x in (kq.get("loi") if isinstance(kq.get("loi"), list) else [])[:10]:
        if isinstance(x, dict):
            ly_do.append(f"{str(x.get('loai') or '?')[:40]}: {str(x.get('chi_tiet') or '')[:200]}")
    return ly_do


def _sha_bang(bang: dict) -> str:
    """Băm bảng thưởng ĐÚNG quy ước backend KPI (a5 10/11): json.dumps(table, ensure_ascii=False, sort_keys=True, separators=(",", ":")), UTF-8.
    Chỉ trên `table` (trạng thái duyệt để ngoài). Số thực `pct` đi qua json.loads / dumps của Python vẫn in lại y hệt ⇒ tính lại được."""
    import hashlib

    return hashlib.sha256(json.dumps(bang, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _kiem_cho_goi(kq: dict, ai_qua: dict | None) -> dict:
    """[ghi chú] Ô `kiem` của GÓI khối Rào chuyển cho khối sau (khung chat, Người duyệt, xuất file, hỏi nhanh — đều đòi kiem.dat is True).
    - ai_qua None (công tắc code / không có file công tắc): Y NHƯ TRƯỚC — đúng 4 khoá của dịch vụ kiểm số.
    - ai_qua có (công tắc ai, AI chấm đã cho qua): dấu theo QUYẾT ĐỊNH CUỐI ⇒ dat True, nguon "ai". Ý kiến của lớp code để ở ô RIÊNG `code` và CHỈ gồm đạt / loại lỗi / phiên bản:
      KHÔNG chép `ly_do` và `loi[].chi_tiet` của code (có con số; khối sau hiện ô ly_do / loi cho người dùng, người duyệt)."""
    if not ai_qua:
        return {k: kq.get(k) for k in ("dat", "loi", "ly_do", "phien_ban")}
    code_dat = not _ly_do_kiem_code(kq)
    loai = [str(x.get("loai"))[:40] for x in (kq.get("loi") or []) if isinstance(x, dict)][:10] if isinstance(kq.get("loi"), list) else []
    return {"dat": True, "loi": [], "ly_do": "AI chấm số cho qua; lớp kiểm bằng luật chỉ ghi ý kiến: " + ("đạt" if code_dat else "không đạt"),
            "phien_ban": f"ai_cham_so@{ai_qua.get('vong')}:{ai_qua.get('sha_loi_dan')}"[:40], "nguon": "ai", "mo_hinh": ai_qua.get("mo_hinh"),
            "code": {"dat": code_dat, "loai": loai, "phien_ban": str(kq.get("phien_ban"))[:80]}}


def _ly_do_bang(kq: dict) -> list[str]:
    """[ghi chú] Kiểm đạt rồi vẫn phải có bảng thưởng TỪ LÕI + băm khớp; không tin băm suông. Rỗng = đạt."""
    bang, sha = kq.get("bang_thuong"), kq.get("sha256_bang")
    if not isinstance(bang, dict) or not bang:
        return ["dịch vụ không trả bảng thưởng (bang_thuong)"]
    if not isinstance(bang.get("lines"), list) or isinstance(bang.get("team_total"), bool) or not isinstance(bang.get("team_total"), int):
        return ["bảng thưởng sai khuôn (thiếu lines / team_total số nguyên)"]
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        return ["dịch vụ không trả sha256_bang hợp lệ"]
    if _sha_bang(bang) != sha:
        return ["sha256 bảng thưởng tính lại KHÔNG khớp sha dịch vụ gửi"]
    return []


@register
class GuardrailsNode(BaseNode):
    version = "0.1.0"

    display_name = "Guardrails"
    description = "Run guardrail checks on the input"
    icon = "ShieldCheck"

    show = True
    beta = False

    node_id = "guardrails_node"
    node_type = "governance"
    node_sub_type = "guardrails"

    input_ports = []
    output_ports = OUTPUT_PORTS
    params = PARAMS

    def __init__(
        self,
        node_config: list[dict],
        node_inputs=None,
        validate_required=True,
        validate_type=False,
    ) -> None:
        """Initialize the guardrails node with configuration and inputs.

        Parameters
        ----------
        node_config : list[dict]
            Configuration for the node, containing parameters and settings.
        node_inputs : dict, optional
            Input data for the node, by default None.
        validate_required : bool, optional
            Whether to validate required parameters, by default True.
        validate_type : bool, optional
            Whether to validate parameter types, by default False.

        Returns
        -------
        None
            This method doesn't return anything.

        Notes
        -----
        This method sets up the guardrails service and retrieves organization ID
        and user token from context variables.
        """
        super().__init__(
            node_config=node_config,
            node_inputs=node_inputs,
            validate_required=validate_required,
            validate_type=validate_type,
        )
        self._validated_params = {}
        self._custom_guardrail_check_service = CustomGuardrailCheckService()

    async def _chay_kiem_sql(self, query: str) -> NodeOutput:
        """[va_kiem_so_sql C 12/11/2026 — đã giao] Chế độ "sql" (workflow "Hỏi dữ liệu bằng lời"): mọi số trong câu trả lời phải có trong kết quả
        công cụ sql_query của CHÍNH lượt này. Kết quả lấy từ sổ do CODE của công cụ ghi (nodes/agent/nho_sql_luot.py, khoá theo self._orgid +
        self._run_id — thuộc tính hệ thống, không từ ô cài đặt / payload / chữ AI); phép so là hàm của B (kiem_so_sql.kiem_so — một nguồn).
        KHÔNG dùng AI, KHÔNG gọi dịch vụ ngoài. Trượt / không đọc được sổ / câu rỗng / thiếu mã ⇒ DỪNG lượt (câu sai không đi tiếp).
        Đạt ⇒ data = câu trả lời GỐC (chữ) ⇒ khối Xuất chữ phía sau hiện như thường.
        GIỚI HẠN: chỉ chứng minh AI chép đúng kết quả công cụ; KHÔNG chứng minh câu SQL trả lời đúng câu hỏi (trang hiện câu SQL + bảng cạnh đó)."""
        kq: dict = {}
        run_id, org = str(getattr(self, "_run_id", "") or "").strip(), str(getattr(self, "_orgid", "") or "").strip()
        if not (query or "").strip():
            ly_do = ["câu trả lời rỗng — không có gì để kiểm"]
        elif not (_MA_HE_THONG.match(run_id) and _MA_HE_THONG.match(org)):
            ly_do = ["không có mã lượt / mã tổ chức hợp lệ — không tìm được kết quả SQL của lượt"]
        elif len(query) > _KIEM_CODE_MAX_VAO:
            ly_do = ["câu trả lời quá dài để kiểm"]
        else:
            await self.emit_progress("Kiểm số theo kết quả truy vấn (không dùng AI)")
            try:
                from nodes.agent import nho_sql_luot

                ban_ghi = await nho_sql_luot.doc(org, run_id)
                kq = nho_sql_luot.kiem(query, ban_ghi, _cau_hoi_tu_params(self._validated_params))
                ly_do = nho_sql_luot.ly_do(kq)
                # luật 0 dòng ở khối Rào: theo công tắc nho_sql_luot.LUAT_0_DONG_O_RAO ("ghi" mặc định | "nhan" | "chan") — đã chốt sau khi D đo
                l0 = nho_sql_luot.ly_do_0_dong(kq)
                if (kq.get("luat_0_dong") or {}).get("dat") is None and nho_sql_luot.LUAT_0_DONG_O_RAO != "ghi" and not ly_do:
                    ly_do = ["không chạy được luật 0 dòng — chưa cho câu đi tiếp"]   # công tắc "nhan" / "chan": luật không chạy được ⇒ ĐÓNG (B: công cụ bản 1 ⇒ ném)
                if l0 and not ly_do and nho_sql_luot.LUAT_0_DONG_O_RAO == "chan":
                    ly_do = l0
                elif l0 and not ly_do and nho_sql_luot.LUAT_0_DONG_O_RAO == "nhan":
                    query = "[" + str((kq.get("luat_0_dong") or {}).get("nhan") or "chưa chắc")[:300] + "]\n\n" + query   # nhãn HẰNG của B, code gắn
            except Exception as e:  # noqa: BLE001 — ĐÓNG KHI LỖI
                ly_do = [f"không kiểm được ({type(e).__name__})"]   # [D minor] chỉ tên loại lỗi — chữ lỗi Redis có thể mang địa chỉ nội bộ
                logger.warning("guardrails KIEM_SQL loi=%s", type(e).__name__)
        bang = JsonDisplay(display_name="Kết quả kiểm số theo truy vấn", field_id="guardrail_check_results", required=True,
                           value=json.dumps({"che_do": "sql", "dat": not ly_do, "ket_qua_dich_vu": dict(kq, loi=[{"loai": "so_khong_co_trong_ket_qua_sql"}] if ly_do else [],
                                                                                                   ly_do="; ".join(ly_do) if ly_do else "đạt — mọi số có trong kết quả truy vấn của lượt",
                                                                                                   phien_ban="kiem_so_sql")}, ensure_ascii=False, default=str)[:200_000],
                           info="Kết quả phép so số với kết quả SQL của lượt", show=True)
        logger.warning("guardrails KIEM_SQL dat=%s so_kiem=%s so_la=%d so_lan_sql=%s so_lan_ok=%s luat_0_dong=%s", not ly_do, kq.get("so_kiem"),
                       len(kq.get("so_la") or []), kq.get("so_lan_sql"), kq.get("so_lan_ok"), (kq.get("luat_0_dong") or {}).get("dat"))   # chỉ số đếm
        if ly_do:
            cau = _cau_bao_chan(ly_do)
            await self.emit_error({"error": cau, "details": ly_do})
            await self.emit_end({"error": cau})
            return NodeOutput(data=None, node=self, error=cau, display_model=bang)
        output = NodeOutput(data=query, node=self, display_model=bang)
        await self.emit_node_result(output)
        await self.emit_success(query)
        await self.emit_end()
        return output

    async def _chay_kiem_code(self, query: str, che_do: str) -> NodeOutput:
        """[ghi chú] Chế độ code (xem khối chú thích trên _KIEM_CODE_URL). Trượt / lỗi / bảng thiếu / băm lệch ⇒ DỪNG lượt;
        đạt ⇒ {cau_tra_loi (câu GỐC), kiem, bang_thuong, sha256_bang} đi tiếp."""
        kq: dict = {}
        self._kiem_so_ai_qua = None   # [va_ai_chan_so_b2] chỉ _quyet_kiem_so đặt, và chỉ khi AI chấm là bên cho qua
        if che_do != "code":
            ly_do = [f"chế độ kiểm lạ {str(self._validated_params.get('che_do_kiem'))[:40]!r}"]
        elif not (query or "").strip():   # [D Đ-1] không có gì để kiểm ⇒ TRƯỢT, không gọi dịch vụ
            ly_do = ["câu trả lời rỗng — không có gì để kiểm"]
        elif not _MA_HE_THONG.match(str(getattr(self, "_run_id", "") or "").strip()):   # [bản 4/5] không có mã lượt ⇒ TRƯỢT
            ly_do = ["không có mã lượt (run_id) — không đối chiếu được lần gọi công cụ"]
        elif not _MA_HE_THONG.match(str(getattr(self, "_orgid", "") or "").strip()):   # [bản 6] tổ chức rỗng / sai dạng ⇒ TRƯỢT
            ly_do = ["không có mã tổ chức hợp lệ — không đối chiếu được lần gọi công cụ"]
        else:
            await self.emit_progress("Kiểm bằng code (không dùng AI)")
            try:
                # [bản 5, D Đ-8 + đã chốt 10/11] run_id = self._run_id = WorkflowRun.id — RIÊNG từng lượt, hệ thống điền cho MỌI khối của lượt
                # (AB temporal/utils/node_factory.py:118 ← ctx.run_id, temporal/activities/_shared.py:425); KHÔNG dùng _session_id (mã định tuyến bên gọi
                # truyền vào — có thể giữ qua chạy lại / tạm dừng ⇒ backend tính cả lần gọi của lượt cũ). Không lấy từ ô cài đặt / payload / chữ AI.
                # Backend KPI dùng ĐÚNG các lần /api/tool nó phục vụ trong lượt đó (native tool của khối Agent gửi X-Kpi-Run-Id = cùng _run_id).
                # [bản 6, a5 10/11] + org_id = self._orgid (cùng giá trị header X-Kpi-Org-Id native tool KPI gửi) — backend khoá lần gọi theo mã lượt + tổ chức
                # [lam_lai C 10/11] + câu hỏi từ ô `cau_hoi` (chỉ được nối từ Ô nhập — workflow_kpi.kiem_payload); không nối ⇒ "" ⇒ không gửi
                from nodes.agent.native_tools import kpi_data_version

                dv = kpi_data_version(self._validated_params)   # [va_data_version] sai dạng ⇒ ngoại lệ ⇒ TRƯỢT (đóng khi lỗi)
                kq = await _goi_kiem_code(query, str(self._run_id).strip(), str(self._orgid).strip(),
                                          _cau_hoi_tu_params(self._validated_params), dv)
                ly_do = await self._quyet_kiem_so(query, kq)   # [va_ai_chan_so 20/11] công tắc code: y như trước (_ly_do_kiem_code(kq) or _ly_do_bang(kq)); công tắc ai: theo PHIẾU của AI chấm
            except Exception as e:  # noqa: BLE001 — ĐÓNG KHI LỖI
                ly_do = [f"không kiểm được ({type(e).__name__}: {str(e)[:200]})"]
        bang = JsonDisplay(display_name="Kết quả kiểm bằng code", field_id="guardrail_check_results", required=True,
                           value=json.dumps({"che_do": che_do, "dat": not ly_do, "ket_qua_dich_vu": kq}, ensure_ascii=False, default=str)[:200_000],
                           info="Kết quả dịch vụ kiểm bằng code", show=True)
        # một dòng log: KHÔNG nội dung khách, chỉ trạng thái + loại lỗi
        logger.warning("guardrails KIEM_CODE dat=%s so_ly_do=%d loai=%s phien_ban=%s", not ly_do, len(ly_do),
                       [str(x.get("loai"))[:40] for x in (kq.get("loi") or []) if isinstance(x, dict)][:10] if isinstance(kq.get("loi"), list) else [],
                       str(kq.get("phien_ban"))[:80])
        if ly_do:
            cau = _cau_bao_chan(ly_do)
            await self.emit_error({"error": cau, "details": ly_do})
            await self.emit_end({"error": cau})
            return NodeOutput(data=None, node=self, error=cau, display_model=bang)
        # ĐẠT ⇒ data = object 4 trường (A + b9 chốt 10/11): Người duyệt / Xuất Excel đọc THẲNG bảng + băm từ lõi, không từ lời AI.
        # Khối không khai cổng riêng ⇒ Capitol chuyển nguyên data sang khối sau (temporal/utils/input_resolver.py:90-103).
        # [va_data_version] dv vào gói ⇒ Người duyệt lấy bảng / duyệt / xuất trên ĐÚNG bản dữ liệu Rào đã kiểm (không ô riêng ở Người duyệt)
        from nodes.agent.native_tools import kpi_data_version as _dv

        ra = {"cau_tra_loi": query, "data_version": _dv(self._validated_params),
              "kiem": _kiem_cho_goi(kq, getattr(self, "_kiem_so_ai_qua", None)),   # [va_ai_chan_so_b2 — E M1] dấu theo QUYẾT ĐỊNH CUỐI của khối này
              "bang_thuong": kq["bang_thuong"], "sha256_bang": kq["sha256_bang"]}
        output = NodeOutput(data=ra, node=self, display_model=bang)
        await self.emit_node_result(output)
        await self.emit_success(ra)
        await self.emit_end()
        return output

    async def _quyet_kiem_so(self, query: str, kq: dict) -> list[str]:
        """[ghi chú] Lý do chặn (rỗng = cho qua) của khối Rào chế độ code.
        Khối Rào VẪN là cổng và VẪN gọi dịch vụ kiểm số (ở trên) — để lấy bảng thưởng + mã băm cho khối sau và lấy ý kiến của lớp code.
          - công tắc `code` (hoặc không có file công tắc): quyết theo lớp code như trước; THÊM: có phiếu AI chấm KHÔNG ĐẠT của đúng câu này ⇒ chặn (công tắc có thể đã đổi
            giữa khối Trợ lý và khối này).
          - công tắc `ai`: ý kiến của code CHỈ GHI; cho qua khi và chỉ khi có PHIẾU hợp lệ ĐẠT của đúng (tổ chức, phiên, câu trả lời đang cầm). Không phiếu / phiếu của bản
            lời dặn khác / phiếu không đạt / không chấm được ⇒ chặn kèm câu cho người dùng. Bảng thưởng từ lõi vẫn phải có + khớp mã băm (_ly_do_bang) ở cả hai chế độ.
        File công tắc sai khuôn ⇒ ném ⇒ người gọi coi là trượt (đóng)."""
        from nodes.agent import kiem_so_ai as KS

        cd = KS.che_do()
        org, phien = str(getattr(self, "_orgid", "") or "").strip(), str(getattr(self, "_session_id", "") or "").strip()
        ly_do_code = _ly_do_kiem_code(kq)
        phieu = await KS.doc_phieu(org, phien, query) if (org and phien) else None
        qua, nhan, cau = KS.quyet_theo_phieu(cd, phieu, query, kq.get("loi") if isinstance(kq.get("loi"), list) else [])
        if cd["quyet_dinh"] == "ai":
            ly_do = ([] if qua else [cau]) or _ly_do_bang(kq)
        else:
            ly_do = ([cau] if not qua else []) or ly_do_code or _ly_do_bang(kq)
        dong = {"noi": "rao", "phien": phien, "run_id": str(getattr(self, "_run_id", "") or ""), "ma_nut": str(getattr(self, "node_id", "") or ""), "sha_cau": KS.bam_cau(query),
                "cong_tac": cd["quyet_dinh"], "luat_code_chan": list(cd.get("luat_code_chan") or ()), "quyet_dinh": nhan, "cho_qua": not ly_do,
                "ai": None if not phieu else {"dat": phieu.get("dat"), "khong_cham_duoc": phieu.get("khong_cham_duoc"), "hop_le": KS.phieu_hop_le(phieu), "lan": phieu.get("lan"),
                                              "ma_nut": phieu.get("ma_nut"), "mo_hinh": phieu.get("mo_hinh"), "ly_do": [x.get("loai") for x in (phieu.get("ly_do") or [])]},
                "code": {"dat": not ly_do_code, "loai": [str(x.get("loai"))[:40] for x in (kq.get("loi") or []) if isinstance(x, dict)][:10] if isinstance(kq.get("loi"), list) else [],
                         "phien_ban": str(kq.get("phien_ban"))[:80]}}
        code_dat, ai_dat = not ly_do_code, bool(phieu and phieu.get("dat"))
        if cd["quyet_dinh"] == "ai" and code_dat != ai_dat:   # hai ý kiến KHÁC nhau ở chế độ ai ⇒ lưu câu để người xem lại (chỉ trên máy)
            dong["cau_tra_loi"] = query[:20000]
        KS.ghi_nhat_ky(dong)
        # [va_ai_chan_so_b2 — E M1] AI là bên quyết và ĐÃ cho qua ⇒ ghi lại để gói đầu ra mang dấu theo quyết định cuối (xem _kiem_cho_goi)
        self._kiem_so_ai_qua = ({"vong": KS.VONG, "sha_loi_dan": KS.SHA_LOI_DAN_GHIM[:8], "mo_hinh": phieu.get("mo_hinh")}
                                if (cd["quyet_dinh"] == "ai" and not ly_do and nhan == "ai_cho_qua" and phieu) else None)
        return ly_do

    async def execute(self) -> NodeOutput:
        """Execute guardrail checks on the provided input.

        Parameters
        ----------
        None
            This method doesn't take any parameters beyond those initialized with the class.

        Returns
        -------
        output : NodeOutput
            Node output containing the results of guardrail checks.
            If successful, contains a list of guardrail check results.
            If failed, contains error information.

        Notes
        -----
        This method performs the following steps:
        1. Retrieves validated parameters including guardrail query and selected guardrails
        2. Runs guardrail checks using the guardrails service
        3. Processes and formats the results
        4. Emits appropriate events for node execution tracking

        Examples
        --------
        The method is typically called as part of the node execution flow:
        ```
        node = GuardrailsNode(node_config)
        output = await node.execute()
        ```
        """
        try:
            await self.emit_begin()

            validated_params = await self._get_validated_params()
            self._validated_params = validated_params or {}   # [ghi chú] để nhánh except / chế độ code đọc được chế độ

            query = validated_params.get("guardrail_query")
            selected_guardrails: list[Any] = (
                validated_params.get("guardrails_list") or []
            )
            if not isinstance(selected_guardrails, list):
                selected_guardrails = [selected_guardrails]

            chan = _co_chan_bat()
            if isinstance(query, list):
                # bản gốc nối bằng "/n" (gõ nhầm \n) — sửa chỉ khi cờ bật (hoặc chế độ code — D m1) để chế độ ai + cờ tắt giữ đúng hành vi gốc
                query = ("\n" if chan or _che_do(validated_params or {}) != "ai" else "/n").join(str(element) for element in query)
            elif not isinstance(query, str):
                raise ValueError("guardrail query is not a string")

            che_do = _che_do(validated_params)
            if che_do == "sql":   # [va_kiem_so_sql C 12/11]
                return await self._chay_kiem_sql(query)
            if che_do != "ai":
                return await self._chay_kiem_code(query, che_do)

            await self.emit_progress(
                f"Running {len(selected_guardrails)} guardrail checks"
            )

            guardrail_ids: list[str] = []
            for guardrail in selected_guardrails:
                option_id = _normalize_guardrail_entry(guardrail).get("option_id")
                if option_id:
                    guardrail_ids.append(option_id)
            guardrail_check_results = (
                await self._custom_guardrail_check_service.run_custom_guardrail_checks(
                    orgid=self._orgid,
                    user_token=self._user_token,
                    guardrail_ids=guardrail_ids,
                    content=query,
                )
            )

            bang_diem = JsonDisplay(
                display_name="Guardrail Check Results",
                field_id="guardrail_check_results",
                required=True,
                value=json.dumps(guardrail_check_results),
                info="Results of the guardrail checks",
                show=True,
            )
            if chan:
                ly_do = _ket_qua_truot(guardrail_ids, guardrail_check_results)
                if ly_do:
                    # TRƯỢT ⇒ lỗi tất định ⇒ fail-fast dừng cả lượt; KHÔNG emit_success, KHÔNG đưa dữ liệu ra cổng
                    cau = _cau_bao_chan(ly_do)
                    # m1: một dòng — cờ, mã luật + loại trượt, câu báo có phải câu cố định không (DB có thể không lưu lý do)
                    logger.warning("guardrails BLOCKED co=%s luat=[%s] cau_co_dinh=%s", _co_chan_bat(),
                                   _tom_tat_log(guardrail_ids, guardrail_check_results), cau == _CAU_BAO_AN_TOAN)
                    await self.emit_error({"error": cau, "details": ly_do, "guardrail_check_results": guardrail_check_results})
                    await self.emit_end({"error": cau})
                    return NodeOutput(data=None, node=self, error=cau, display_model=bang_diem)
                # ĐẠT ⇒ NỘI DUNG GỐC đi tiếp (bản gốc đưa bảng điểm ⇒ khối sau mất câu trả lời — 06/11 R1/R2); bảng điểm vẫn để xem
                output = NodeOutput(data=query, node=self, display_model=bang_diem)
                await self.emit_node_result(output)
                await self.emit_success(query)
                await self.emit_end()
                return output

            output = NodeOutput(
                data=guardrail_check_results,
                node=self,
                display_model=bang_diem,
            )

            await self.emit_node_result(output)

            await self.emit_success(guardrail_check_results)
            await self.emit_end()

            return output

        except Exception as e:
            error_msg = f"Guardrails node failed: {str(e)}"
            if _che_do(getattr(self, "_validated_params", None) or {}) in ("code", "sql"):   # [va_kiem_so_sql] + "sql"
                error_msg = _cau_bao_chan([f"lỗi trong khối kiểm: {type(e).__name__}"])   # không để lỗi bị xếp "thử lại"
            logger.error(error_msg, exc_info=True)
            capture_exception(e)
            await self.emit_error({"error": error_msg, "details": str(e)})
            await self.emit_end({"error": error_msg})
            return NodeOutput(data=None, node=self, error=error_msg)
