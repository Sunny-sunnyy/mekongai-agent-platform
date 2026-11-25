"""
Usage example with payload at : app/nodes/agent/tests/usage_example.py
"""

import asyncio
import json
import logging
import os
import time
from uuid import NAMESPACE_URL, uuid5

from sentry_sdk import capture_exception

from api.websockets.event_models import HumanInterventionData, NodeEvent
from nodes.logic.human_intervention.params import PARAMS
from nodes.logic.human_intervention.ports import OUTPUT_PORTS
from services.operator_relay.node_relay import NodeRelayMixin, activity_attempt
from services.operator_relay.relay import (
    OUTCOME_APPROVED,
    OUTCOME_AUTO_APPROVED,
    OUTCOME_RUN_STOPPED,
    OUTCOME_STOPPED,
    OUTCOME_TIMED_OUT,
    relay_intervention_requested,
    relay_intervention_resolved,
)
from workflow.node.base.node import BaseNode, NodeOutput
from workflow.node.base.param.custom import MarkdownDisplay
from workflow.node.base.toggle import toggle_enabled
from workflow.node.registry import register

# Set up logger
logger = logging.getLogger(__name__)


class InterventionSilenceUnverifiable(RuntimeError):
    """The review window ended but silence could NOT be confirmed.

    Raised when the deadline reconcile can't read the durable answer key,
    so "nobody answered" is an assumption rather than a finding. Auto-
    approval treats silence as consent, which is only sound when silence
    is established -- an unreachable Redis must fail the gate, not open
    it. Deliberately NOT an ``asyncio.TimeoutError`` subclass so it can
    never reach the auto-approve branch.
    """


class InterventionWaitExpired(asyncio.TimeoutError):
    """The human-review window elapsed with no answer.

    Raised at the ``wait_for`` boundary INSTEAD of a bare
    ``asyncio.TimeoutError`` so "nobody answered" is unambiguous: on
    Python 3.11+ ``asyncio.TimeoutError`` IS the builtin ``TimeoutError``,
    which infrastructure clients raise too (aiohttp's ``ServerTimeoutError``
    subclasses it), and the auto-approve branch in ``execute()`` must never
    read an I/O timeout from the subscribe / emit / persist setup as
    consent-by-silence. Subclasses ``asyncio.TimeoutError`` so every
    existing handler and the message-based classifier are unaffected.
    """


def _nguoi_duyet_bat() -> bool:
    import os

    return os.environ.get("CHAY_THU_NGUOI_DUYET", "true").strip().lower() not in ("false", "0", "no", "off")


# [ghi chú] CHẾ ĐỘ KPI của Người duyệt (đặc tả KPI bước 5: duyệt bảng thưởng TỪ LÕI, gắn băm).
# Đầu vào = gói 4 trường của Rào chắn chế độ code {cau_tra_loi, kiem, bang_thuong, sha256_bang}. Luật (đã chốt 10/11):
# (Đ-6) đọc bảng + băm từ gói, tính lại băm (lệch / thiếu / chưa qua kiểm ⇒ lỗi, dừng); (Đ-7) KHÔNG gọi tóm tắt AI — hiện nguyên văn bảng (backend đã che
# mã NV) + kết quả kiểm + băm; quyết định rỗng / lạ ≠ duyệt; hết giờ ⇒ dừng dù bật auto_approve; mở cổng: tính lại băm == băm đã hiện; data / cổng ra =
# gói + {"duyet": {sha256_bang, luc, nguoi}}; câu trả lời bền CŨ của (session, khối) bị xoá trước khi hỏi ⇒ lời duyệt không mang sang lượt khác,
# câu trả lời thứ 2 của checkpoint trước không mở được checkpoint sau (cái giá: lần thử lại trong cùng lượt phải hỏi lại).
_KPI_KHOA = ("cau_tra_loi", "kiem", "bang_thuong", "sha256_bang")
_KPI_MAX_VAO = 2 * 1024 * 1024


def _kpi_bat(params: dict) -> bool:
    return _nguoi_duyet_bat() and toggle_enabled((params or {}).get("che_do_kpi", False))


def _sha_bang(bang: dict) -> str:
    """Cùng quy ước backend KPI + Rào chắn: json.dumps(table, ensure_ascii=False, sort_keys=True, separators=(",", ":")), UTF-8."""
    import hashlib

    return hashlib.sha256(json.dumps(bang, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _doc_goi_kpi(v) -> dict:
    """Gói KPI từ preview_context. Không đúng ⇒ ValueError (khối lỗi, lượt dừng)."""
    if isinstance(v, list) and len(v) == 1:
        v = v[0]
    if isinstance(v, str):   # [D m2] chỉ json.loads (chuỗi JSON) — str(dict) kiểu Python ⇒ DỪNG (reducer đã giữ dict; không đoán)
        if len(v) > _KPI_MAX_VAO:
            raise ValueError("gói KPI quá lớn")
        try:
            v = json.loads(v.strip())
        except ValueError:
            raise ValueError("chế độ KPI: đầu vào là chữ không phải JSON — gói KPI phải đi nguyên object từ Rào chắn") from None
    if not isinstance(v, dict) or not set(_KPI_KHOA) <= set(v):
        raise ValueError("chế độ KPI: đầu vào không phải gói của Rào chắn chế độ code (cần cau_tra_loi, kiem, bang_thuong, sha256_bang)")
    if not isinstance(v.get("kiem"), dict) or v["kiem"].get("dat") is not True:
        raise ValueError("chế độ KPI: gói chưa qua kiểm bằng code (kiem.dat không phải true)")
    if not isinstance(v["bang_thuong"], dict) or not isinstance(v["sha256_bang"], str) or _sha_bang(v["bang_thuong"]) != v["sha256_bang"]:
        raise ValueError("chế độ KPI: sha256 bảng thưởng tính lại KHÔNG khớp sha của gói")
    return v


# ---- [va_xuat_excel C 10/11/2026 — đã chốt X1, D mục 13 a82bda23] XUẤT EXCEL sau khi DUYỆT (chế độ KPI). AI không chạm số.
# Chiều dữ liệu: Người duyệt (sha bảng người duyệt ĐÃ THẤY, tính lại ở dòng mở cổng) ⇒ POST backend "đã duyệt" ⇒ GET file xlsx ⇒ KIỂM bằng code
# (gói zip an toàn + mọi ô tiền 2 sheet + tổng == gói đã duyệt, không ô công thức) ⇒ tải lên kho Capitol theo tổ chức của lượt ⇒ NodeFile.
# Bất kỳ bước nào lỗi ⇒ NÉM ⇒ khối lỗi ⇒ lượt dừng, KHÔNG có file. Địa chỉ + khoá đọc từ biến môi trường hộp (không từ payload — SSRF / lộ khoá):
# CHAY_THU_KPI_URL (đã có ở compose.rao_code.yaml), khoá = cặp CHAY_THU_KIEM_CODE_KHOA_TEN / _KHOA của Rào chắn (cùng khoá hệ thống mekongai).
# Khoá hệ thống chỉ ở nhánh này + Rào chắn; công cụ Agent KHÔNG gửi (D điều kiện 4).
# Hợp đồng (nhóm kỹ thuật -29 xác nhận 10/11): POST /api/system/duyet ĐÚNG 4 trường {run_id = bang_thuong.run_id (mã BẢN TÍNH, không phải mã lượt
# Capitol), sha256_bang (64 hex thường), org_id (1–100), nguoi_duyet (1–60, tự khai)} ⇒ 200 {"duyet": true, run_id, sha256_bang, …}
# (422 / 409 / 415 / 401 / 503 ⇒ dừng); rồi GET /api/export/xlsx KHÔNG tham số ⇒ 200 + byte xlsx (409 chưa duyệt / bảng đổi / file có mã thật
# — backend đếm sau giải nén). Đổi hợp đồng ⇒ chỉ sửa _kpi_goi_backend.
_XUAT_MAX = 5 * 1024 * 1024
_XUAT_GIAY = 20.0


async def _kpi_goi_backend(bang: dict, sha: str, org_id: str, nguoi: str, dv: str = "") -> tuple[bytes, int, dict | None]:
    import httpx

    goc = os.environ.get("CHAY_THU_KPI_URL", "").strip().rstrip("/")
    if not goc.startswith(("http://", "https://")):
        raise ValueError("xuất Excel: chưa cấu hình CHAY_THU_KPI_URL")
    ten, khoa = os.environ.get("CHAY_THU_KIEM_CODE_KHOA_TEN", "").strip(), os.environ.get("CHAY_THU_KIEM_CODE_KHOA", "")
    if not ten or not khoa:
        raise ValueError("xuất Excel: thiếu khoá hệ thống")
    h = {ten: khoa}
    q = {"data_version": dv} if dv else None   # [va_data_version] cùng bản dữ liệu Rào chắn đã kiểm (từ gói)
    nguoi = (str(nguoi or "").strip() or "nguoi_chay_luot_khong_ro")[:60]   # E: 1–60 ký tự, tự khai (Đ-9)

    async def goi() -> tuple[bytes, int, dict | None]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_XUAT_GIAY), follow_redirects=False, trust_env=False) as c:
            # [bản 2, ca thật B e4e2c146] bảng trong gói = `table` (KHÔNG có run_id); mã bản tính chỉ ở lớp ngoài GET /api/system/bonus-table
            # ⇒ lấy từ đó, CHỈ khi sha bảng backend == sha người duyệt đã thấy (D đ.k. 2: sha gửi đi vẫn là sha của mình)
            r0 = await c.get(f"{goc}/api/system/bonus-table", headers=h, params=q)
            if r0.status_code != 200:
                raise ValueError(f"xuất Excel: không đọc được bảng thưởng hiện tại (HTTP {r0.status_code})")
            try:
                j0 = r0.json()
            except ValueError:
                j0 = None
            ban = j0.get("run_id") if isinstance(j0, dict) else None
            if (not isinstance(j0, dict) or j0.get("sha256") != sha or not isinstance(j0.get("table"), dict) or _sha_bang(j0["table"]) != sha
                    or isinstance(ban, bool) or not isinstance(ban, int) or ban < 1):
                raise ValueError("xuất Excel: bảng thưởng hiện tại KHÁC bảng người duyệt đã thấy (sha lệch) hoặc không có mã bản tính")
            r = await c.post(f"{goc}/api/system/duyet", headers=h, params=q,
                             json={"run_id": ban, "sha256_bang": sha, "org_id": org_id, "nguoi_duyet": nguoi})
            if r.status_code != 200:
                raise ValueError(f"xuất Excel: backend không nhận lời duyệt (HTTP {r.status_code})")
            try:
                tl = r.json()
            except ValueError:
                tl = None
            if not isinstance(tl, dict) or tl.get("duyet") is not True or tl.get("sha256_bang") != sha or tl.get("run_id") != ban:
                raise ValueError("xuất Excel: backend trả lời duyệt không khớp (duyet / sha / run_id)")
            than = bytearray()
            # E 10/11: đường hệ thống KHÔNG nhận run_id (422) — trả file của bảng HIỆN TẠI chỉ khi có lần duyệt khớp sha bảng hiện tại;
            # bảng đổi sau duyệt ⇒ 409. File khác bảng mình đã duyệt ⇒ _kpi_kiem_o chặn.
            async with c.stream("GET", f"{goc}/api/export/xlsx", headers=h, params=q) as r2:
                if r2.status_code != 200:
                    raise ValueError(f"xuất Excel: backend không trả file (HTTP {r2.status_code})")
                async for k in r2.aiter_bytes():
                    than += k
                    if len(than) > _XUAT_MAX:
                        raise ValueError("xuất Excel: file quá lớn")
            # [ghi chú] KPI_NAMES=on: bảng tên đi CẠNH `table` (ngoài băm — mekongai main.py:977-980) ⇒ _kpi_kiem_o so ô Tên / Mã SM; off ⇒ None
            ten_ht = j0.get("names") if isinstance(j0.get("names"), dict) else None
            return bytes(than), ban, ten_ht

    try:
        return await asyncio.wait_for(goi(), timeout=_XUAT_GIAY * 2)
    except asyncio.TimeoutError:
        raise ValueError("xuất Excel: backend quá giờ") from None


def _kpi_kiem_o(b: bytes, bang: dict, names: dict | None = None) -> int:
    """Kiểm VÙNG DỮ LIỆU 2 sheet bằng DANH SÁCH CHO PHÉP (đã chốt (a); D: lớp PHỤ — đếm tên thật là việc của kpi-backend (b) + máy chủ (c)).
    "Tổng đội" (từ dòng tiêu đề "Mã NV"): tiêu đề đúng [Mã NV, <KPI của bảng>, Lương kinh doanh]; cột 1 chỉ mã của bảng (mỗi mã 1 lần; từ 18/11 mã / tên thật, không bắt dạng NVdd) +
    "Cả đội"; mọi ô khác là SỐ NGUYÊN == bảng đã duyệt (NV × KPI, lương từng NV, tổng KPI, tổng đội == team_total).
    "Chi tiết" (từ dòng tiêu đề): tiêu đề đúng 6 cột; mỗi dòng (mã, KPI) của bảng đúng 1 lần, tiền == bảng, mốc / lý do == ĐÚNG chữ của bảng.
    Ô ngoài các cột trên phải trống. Công thức / chữ ở ô tiền ⇒ lệch. Ngoài phạm vi (backend đếm): 3 dòng đầu mỗi sheet, sheet "Nguồn".
    Trả số ô đã so. Lệch ⇒ ValueError. Bố cục theo mekongai export_report.to_xlsx.
    [ghi chú] `names` = {mã che: {name, sm_code}} lấy từ ĐƯỜNG HỆ THỐNG (GET /api/system/bonus-table, cùng lần đọc sha) ⇒ file phải có ĐÚNG
    3 cột người [Mã NV, Tên, Mã SM] (export_report._person_headers) và mỗi ô Tên / Mã SM == chữ của names (chuẩn hoá như bên ghi: NFC, bỏ ký tự điều
    khiển trừ xuống dòng), kiểu ô CHỮ (không công thức); mã không có trong names ⇒ ô trống. names None ⇒ đúng 1 cột Mã NV như cũ."""
    import re
    from io import BytesIO

    import unicodedata

    from openpyxl import load_workbook

    if names is not None and (not isinstance(names, dict) or not all(
            isinstance(k, str) and isinstance(v, dict) and all(isinstance(v.get(f, ""), str) for f in ("name", "sm_code")) for k, v in names.items())):
        raise ValueError("xuất Excel: bảng tên (names) của hệ thống sai khuôn")
    nguoi = ["Mã NV", "Tên", "Mã SM"] if names is not None else ["Mã NV"]   # export_report.PERSON_HEADERS
    p = len(nguoi)

    def chuan(x: str) -> str:   # = export_report.clean_text
        x = unicodedata.normalize("NFC", x)
        return "".join(ch for ch in x if ch == "\n" or not unicodedata.category(ch).startswith("C"))

    def o_nguoi(ws, r, ma):
        if names is None:
            return
        e = names.get(ma) or {}
        for c, dung in ((2, chuan(str(e.get("name") or ""))), (3, chuan(str(e.get("sm_code") or "")))):
            o = ws.cell(r, c)
            if (o.value or "") != dung or (o.value not in (None, "") and o.data_type != "s"):
                raise ValueError(f"xuất Excel: ô Tên / Mã SM của {ma} ({ws.title}) khác bảng tên hệ thống")

    dong = {}
    for d in bang.get("lines") or []:
        k = (d.get("employee_id"), d.get("kpi"))
        if k in dong or isinstance(d.get("amount"), bool) or not isinstance(d.get("amount"), int):
            raise ValueError("xuất Excel: bảng đã duyệt có dòng trùng / tiền không phải số nguyên")
        dong[k] = d
    nv = sorted({k[0] for k in dong})
    kpi = sorted({k[1] for k in dong})
    if not dong or len(dong) != len(nv) * len(kpi):
        raise ValueError("xuất Excel: bảng đã duyệt thiếu ô NV × KPI")
    # [ghi chú] BỎ kiểm DẠNG mã đã che NVdd (mã / tên thật nay hợp lệ). Giữ kiểm DANH SÁCH
    # (mỗi dòng file phải là mã của bảng đã duyệt — bên dưới) + thêm chống CÔNG THỨC / ký tự lạ (E (a)): chuỗi 1–64 ký tự, không ký tự điều khiển,
    # không mở đầu = + - @ (Excel hiểu là công thức), không trùng nhãn dòng tổng "Cả đội".
    def _ma_an_toan(x) -> bool:
        return (isinstance(x, str) and 0 < len(x) <= 64 and x == x.strip() and x[0] not in "=+-@"
                and not any(ord(c) < 32 or 127 <= ord(c) < 160 for c in x) and x != "Cả đội")

    if not all(_ma_an_toan(x) for x in nv):
        raise ValueError("xuất Excel: bảng có mã nhân viên rỗng / quá dài / ký tự lạ / mở đầu = + - @ / trùng \"Cả đội\"")
    tien = {k: d["amount"] for k, d in dong.items()}
    wb = load_workbook(BytesIO(b), data_only=False)   # data_only=False: ô công thức hiện là chữ "=…" ⇒ lệch, không đọc giá trị đệm

    def so(v, dung, cho):
        if isinstance(v, bool) or not isinstance(v, int) or v != dung:
            raise ValueError(f"xuất Excel: ô {cho} trong file khác bảng đã duyệt")

    def tieu_de(ws, dau_cot):
        r = next((r for r in range(1, 30) if ws.cell(r, 1).value == "Mã NV"), None)
        if r is None or [ws.cell(r, c).value for c in range(1, len(dau_cot) + 1)] != dau_cot:
            raise ValueError(f"xuất Excel: tiêu đề sheet {ws.title} khác mẫu")
        return r

    def trong_ngoai(ws, r, so_cot):
        if any(ws.cell(r, c).value is not None for c in range(so_cot + 1, ws.max_column + 1)):
            raise ValueError(f"xuất Excel: sheet {ws.title} có ô ngoài vùng cho phép (dòng {r})")

    if wb.sheetnames != ["Tổng đội", "Chi tiết", "Nguồn"]:
        raise ValueError("xuất Excel: danh sách sheet khác mẫu")
    t = wb["Tổng đội"]
    r0 = next((r for r in range(1, 30) if t.cell(r, 1).value == "Mã NV"), None)
    cot_kpi = [t.cell(r0, c).value for c in range(p + 1, len(kpi) + p + 1)] if r0 else []   # đúng số KPI của bảng; cột sau đó phải là "Lương kinh doanh"
    if not all(isinstance(x, str) for x in cot_kpi) or sorted(cot_kpi) != kpi or len(cot_kpi) != len(kpi):   # [ghi chú] ô rỗng ⇒ ValueError rõ, không TypeError
        raise ValueError("xuất Excel: cột KPI trong file khác bảng đã duyệt")
    dau = tieu_de(t, [*nguoi, *cot_kpi, "Lương kinh doanh"])
    n_cot, dem, gap, het = len(cot_kpi) + p + 1, 0, [], False
    for r in range(dau + 1, t.max_row + 1):
        ma = t.cell(r, 1).value
        trong_ngoai(t, r, n_cot)
        if het:
            if any(t.cell(r, c).value is not None for c in range(1, n_cot + 1)):
                raise ValueError("xuất Excel: Tổng đội có dòng sau dòng Cả đội")
            continue
        if ma == "Cả đội":
            if any(t.cell(r, c).value not in (None, "") for c in range(2, p + 1)):
                raise ValueError("xuất Excel: dòng Cả đội có chữ ở cột Tên / Mã SM")
            for j, k in enumerate(cot_kpi, p + 1):
                so(t.cell(r, j).value, sum(tien[(n, k)] for n in nv), f"Tổng đội!Cả đội/{k}")
            so(t.cell(r, n_cot).value, sum(tien.values()), "Tổng đội!Cả đội/tổng")
            so(sum(tien.values()), bang.get("team_total"), "team_total")
            dem, het = dem + len(cot_kpi) + 1, True
            continue
        if ma not in nv or ma in gap:
            raise ValueError("xuất Excel: Tổng đội có dòng mã NV lạ / lặp")
        gap.append(ma)
        o_nguoi(t, r, ma)
        for j, k in enumerate(cot_kpi, p + 1):
            so(t.cell(r, j).value, tien[(ma, k)], f"Tổng đội!{ma}/{k}")
        so(t.cell(r, n_cot).value, sum(tien[(ma, k)] for k in kpi), f"Tổng đội!{ma}/lương")
        dem += len(cot_kpi) + 1
    if sorted(gap) != nv or not het:
        raise ValueError("xuất Excel: Tổng đội thiếu NV hoặc thiếu dòng Cả đội")
    ct = wb["Chi tiết"]
    dau2 = tieu_de(ct, [*nguoi, "KPI", "% thực hiện", "Mốc đạt", "Tiền (đồng)", "Lý do"])
    thay = set()
    for r in range(dau2 + 1, ct.max_row + 1):
        trong_ngoai(ct, r, p + 5)
        o = [ct.cell(r, c).value for c in range(1, p + 6)]
        if all(x is None for x in o):
            continue
        k = (o[0], o[p])
        if k in dong:
            o_nguoi(ct, r, k[0])
        o = [o[0], *o[p:]]   # bỏ cột Tên / Mã SM (đã so) ⇒ chỉ số cột phía dưới giữ như bản 1 cột
        if k not in dong or k in thay:
            raise ValueError("xuất Excel: Chi tiết có dòng lạ / lặp")
        thay.add(k)
        d = dong[k]
        so(o[4], d["amount"], f"Chi tiết!{k[0]}/{k[1]}/tiền")
        if o[3] != (d.get("reached_tier") or "chưa đạt mốc") or o[5] != d.get("reason"):
            raise ValueError(f"xuất Excel: Chi tiết!{k[0]}/{k[1]} mốc / lý do khác bảng đã duyệt")
        if isinstance(o[2], bool) or not isinstance(o[2], (int, float)):
            raise ValueError(f"xuất Excel: Chi tiết!{k[0]}/{k[1]} % không phải số")
        dem += 3
    if thay != set(dong):
        raise ValueError("xuất Excel: Chi tiết thiếu dòng NV × KPI")
    return dem


async def _kpi_xuat_excel(node, bang: dict, sha: str, nguoi: str):
    """Trả (NodeFile, tóm tắt). Mọi lỗi ⇒ ValueError (người gọi để khối lỗi ⇒ dừng, không file)."""
    import hashlib
    from io import BytesIO

    from services.aws.s3 import S3Service
    from services.pydantic_ai_skills.e2b_chat_service import _xlsx_kiem_goi   # bước chặn gói xlsx ĐÃ GẮN (va_xlsx_goi, D ĐẠT)
    from workflow.node.base.file import NodeFile

    org = str(getattr(node, "_orgid", "") or "").strip()
    if not org:
        raise ValueError("xuất Excel: không có mã tổ chức của lượt")
    if _sha_bang(bang) != sha:   # sha người duyệt ĐÃ THẤY (D điều kiện 2) — không lấy lại từ backend
        raise ValueError("xuất Excel: bảng KHÁC bảng người duyệt đã thấy (sha lệch)")
    import re

    dv = getattr(node, "_kpi_dv", "") or ""
    if dv and not re.fullmatch(r"[0-9a-f]{1,64}", dv):
        raise ValueError("xuất Excel: bản dữ liệu (data_version) trong gói sai dạng")
    b, ban, ten_ht = await _kpi_goi_backend(bang, sha, org, nguoi, dv)
    _xlsx_kiem_goi(b)                       # .rels ngoài / OLE / ActiveX / macro / DTD / quá cỡ ⇒ ValueError
    so_o = _kpi_kiem_o(b, bang, ten_ht)
    ten = f"bang_thuong_{str(bang.get('period') or 'ky')[:7]}_ban{int(ban)}.xlsx"   # tên do CODE đặt
    if not all(c.isalnum() or c in "_-." for c in ten):
        raise ValueError("xuất Excel: tên file lạ")
    s3 = S3Service()
    kq = await asyncio.to_thread(
        s3.upload_file_object, file_obj=BytesIO(b), filename=ten,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", orgid=org,
        workflow_id=getattr(node, "_workflowid", None) or None, node_id=node.node_id or None,
        metadata={"generated_by": "human_intervention_kpi_xuat_excel", "sha256_bang": sha})
    url = s3.generate_presigned_url(s3_key=kq["s3_key"], bucket_name=kq["s3_bucket"], expires_in=3600)   # link hạn ngắn (D điều kiện 6)
    nf = NodeFile(name=ten, presigned_url=url, mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  s3_bucket=kq["s3_bucket"], s3_key=kq["s3_key"], s3_region=kq["s3_region"], generated=True)
    return nf, {"ten": ten, "sha256_file": hashlib.sha256(b).hexdigest(), "so_o_da_so": so_o, "s3_key": kq["s3_key"]}


_DUNG_NHAP = "/srv/src/nodes/logic/human_intervention/dung_file_nhap.py"   # = chay_thu/va_xuat_ket_qua/dung_file.py, gắn qua compose (bộ dựng file nháp của khung chat)


async def _kpi_nhap_tu_duyet(node, goi: dict, sha: str, cho_giay: int):
    """[ghi chú] File NHÁP cho lượt KPI tự duyệt khi quá hạn. Trả (NodeFile, tóm tắt) cùng hình với _kpi_xuat_excel + "nhap": True.
    KHÔNG gọi /api/system/duyet và KHÔNG gọi /api/export (file chính thức) — chỉ đọc bảng tên qua đường hệ thống như file nháp của khung chat. Mọi lỗi ⇒ ValueError."""
    import hashlib
    import importlib.util
    from io import BytesIO

    from services.aws.s3 import S3Service
    from services.pydantic_ai_skills.e2b_chat_service import _xlsx_kiem_goi
    from workflow.node.base.file import NodeFile

    org = str(getattr(node, "_orgid", "") or "").strip()
    if not org:
        raise ValueError("tự duyệt khi quá hạn: không có mã tổ chức của lượt")
    if _sha_bang(goi["bang_thuong"]) != sha:
        raise ValueError("tự duyệt khi quá hạn: bảng KHÁC bảng đã hiện (sha lệch)")
    try:
        sp = importlib.util.spec_from_file_location("chay_thu_dung_file_nhap", _DUNG_NHAP)
        m = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(m)
    except (OSError, AttributeError) as e:
        raise ValueError(f"tự duyệt khi quá hạn: hộp chưa gắn bộ dựng file nháp ({type(e).__name__})") from None
    ten, b, so_o = await asyncio.to_thread(m.dung_nhap_tu_duyet, goi, int(cho_giay))   # đọc lại file + so TỪNG ô bằng _kpi_kiem_o ở trong hàm
    _xlsx_kiem_goi(b)
    if not ten.startswith(m.TU_DUYET_TEP + "_") or not all(c.isalnum() or c in "_-." for c in ten):
        raise ValueError("tự duyệt khi quá hạn: tên file lạ")
    s3 = S3Service()
    kq = await asyncio.to_thread(
        s3.upload_file_object, file_obj=BytesIO(b), filename=ten,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", orgid=org,
        workflow_id=getattr(node, "_workflowid", None) or None, node_id=node.node_id or None,
        metadata={"generated_by": "human_intervention_kpi_tu_duyet_qua_han", "sha256_bang": sha})
    url = s3.generate_presigned_url(s3_key=kq["s3_key"], bucket_name=kq["s3_bucket"], expires_in=3600)
    nf = NodeFile(name=ten, presigned_url=url, mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  s3_bucket=kq["s3_bucket"], s3_key=kq["s3_key"], s3_region=kq["s3_region"], generated=True)
    return nf, {"ten": ten, "sha256_file": hashlib.sha256(b).hexdigest(), "so_o_da_so": so_o, "s3_key": kq["s3_key"], "nhap": True, "kieu": "tu_duyet_qua_han"}


def _o_md(x) -> str:
    return str("" if x is None else x).replace("|", "\\|").replace("\n", " ")[:200]


def _hien_kpi(goi: dict, ghi_chu) -> str:
    """Chữ hiển thị cho người duyệt — dựng bằng code từ gói, KHÔNG qua AI."""
    b, k = goi["bang_thuong"], goi["kiem"]
    phan = []
    if isinstance(ghi_chu, str) and ghi_chu.strip():
        phan.append(f"### Ghi chú cho người duyệt (không gửi đi)\n{ghi_chu.strip()}")
    phan.append("### Bảng thưởng từ lõi KPI (code tính — không qua AI)\n"
                f"Kỳ: {_o_md(b.get('period'))} · Bản tính: {_o_md(b.get('run_id'))} · **Tổng đội: {_o_md(b.get('team_total'))} đồng**")
    dong = ["| Mã NV | KPI | % đạt | Bậc | Tiền (đồng) | Lý do |", "|---|---|---|---|---|---|"]
    for r in (b.get("lines") or [])[:500]:
        if isinstance(r, dict):
            dong.append("| " + " | ".join(_o_md(r.get(c)) for c in ("employee_id", "kpi", "pct", "reached_tier", "amount", "reason")) + " |")
    phan.append("\n".join(dong))
    loi = "; ".join(f"{_o_md(x.get('loai'))}: {_o_md(x.get('chi_tiet'))}" for x in (k.get("loi") or []) if isinstance(x, dict)) or "không"
    # [ghi chú] gói do AI chấm số quyết (kiem.nguon = "ai") ⇒ tiêu đề nói đúng ai quyết; ý kiến của lớp code (chỉ ghi) in riêng, chỉ đạt / loại lỗi
    if k.get("nguon") == "ai":
        c = k.get("code") if isinstance(k.get("code"), dict) else {}
        loai = ", ".join(_o_md(x) for x in (c.get("loai") or [])[:10]) or "không"
        phan.append(f"### Kiểm số — AI chấm quyết định\nĐạt: **{k.get('dat') is True}** · Phiên bản: {_o_md(k.get('phien_ban'))}\n"
                    f"Lớp kiểm bằng luật (chỉ ghi, không chặn): đạt **{c.get('dat') is True}** · loại lỗi: {loai} · phiên bản: {_o_md(c.get('phien_ban'))}")
    else:
        phan.append(f"### Kiểm bằng code\nĐạt: **{k.get('dat') is True}** · Lý do: {_o_md(k.get('ly_do'))} · Lỗi: {loi} · Phiên bản: {_o_md(k.get('phien_ban'))}")
    phan.append(f"### Mã băm bảng (sha256) — đối chiếu khi duyệt\n`{goi['sha256_bang']}`")
    phan.append("### Câu trả lời của trợ lý (chỉ để tham khảo — số dùng là bảng trên)\n" + _o_md(goi.get("cau_tra_loi"))[:200])
    return "\n\n".join(phan)


def _hien_cho_nguoi_duyet(ghi_chu, ban_capitol: str, nguyen_van: str) -> str:
    """[ghi chú] Chữ HIỂN THỊ cho người duyệt. Nguyên văn đặt trong khối mã có rào dài hơn mọi chuỗi ` trong nội dung
    (để markdown / HTML trong nội dung hiện đúng chữ, không bị giao diện dựng thành định dạng)."""
    import re as _re

    rao = "`" * max(3, 1 + max((len(m) for m in _re.findall(r"`+", nguyen_van)), default=0))
    phan = []
    if isinstance(ghi_chu, str) and ghi_chu.strip():
        phan.append(f"### Ghi chú cho người duyệt (không gửi đi)\n{ghi_chu.strip()}")
    phan.append(ban_capitol)
    phan.append(f"### Sẽ gửi NGUYÊN VĂN cho khối sau ({len(nguyen_van)} ký tự của chữ gửi đi)\n{rao}text\n{nguyen_van}\n{rao}")   # D m3
    return "\n\n".join(phan)


@register
class HumanIntervention(NodeRelayMixin, BaseNode):
    """Node for pausing workflow execution to request human input or intervention."""

    # Bumped for the timeout cap (300 -> 3600): the editor offers a node update
    # only when the catalog version outranks the stored one, and the cap is
    # copied into each saved node's struct, so without the bump existing
    # workflows keep clamping the field to 300 in the editor.
    version = "0.3.0"

    display_name = "Human Intervention"
    description = "Pauses workflow for manual review or input"
    icon = "OctagonPause"

    show = True  # Show in node catalog
    beta = False

    node_id = "human_intervention_node"
    node_type = "governance"
    node_sub_type = "human_intervention"

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
        """node_config will be validated against the params, node_inputs are generated in run time and passed from other nodes"""
        super().__init__(
            node_config=node_config,
            node_inputs=node_inputs,
            validate_required=validate_required,
            validate_type=validate_type,
        )
        self._validated_params = {}

    def _intervention_request_id(self, session_id: str) -> str:
        """Deterministic request id for this (session, node) intervention.

        The one place the NODE mints the id. The same ``uuid5`` recipe is
        re-derived independently by the A2A layers
        (``workflow_tools._deterministic_intervention_request_id`` and the
        ``event_adapter`` resolver), and the response publisher is handed
        the id by its caller -- change the recipe in all of them or not
        at all.

        Args:
            session_id: The run's session id.

        Returns:
            The stable request id string.
        """
        return str(uuid5(NAMESPACE_URL, f"intervention:{session_id}:{self.node_id}"))

    def _relay_checkpoint_opened(
        self,
        *,
        session_id: str,
        request_id: str,
        timeout: int,
        preview_context: str | None,
    ) -> None:
        attempt = activity_attempt()
        self._operator_checkpoint = {"attempt": attempt, "timeout": timeout}
        self._spawn_relay(
            relay_intervention_requested(
                session_id=session_id,
                node_id=self.node_id,
                display_name=self._operator_node_title(),
                request_id=request_id,
                brief=preview_context,
                timeout_secs=timeout,
                deadline_epoch_ms=int((time.time() + timeout) * 1000),
                auto_approve_on_timeout=toggle_enabled(
                    self._validated_params.get("auto_approve_on_timeout", False)
                ),
                attempt=attempt,
            )
        )

    async def _relay_checkpoint_closed(
        self, outcome: str, *, wait: bool = False
    ) -> None:
        """Close the checkpoint the operator chat was told about, once.

        ``wait`` bounds the write instead of spawning it on the exits where
        the task is about to go away (a declined gate, a stopped run, the
        expiry that fails the node).
        """
        checkpoint = getattr(self, "_operator_checkpoint", None)
        if not checkpoint:
            return
        self._operator_checkpoint = None
        coro = relay_intervention_resolved(
            session_id=self._session_id or "",
            node_id=self.node_id,
            outcome=outcome,
            attempt=checkpoint["attempt"],
            timeout_secs=checkpoint["timeout"],
        )
        if wait:
            await self._await_relay(coro)
        else:
            self._spawn_relay(coro)

    async def await_user_intervention(
        self,
        timeout: int = 300,
        preview_context: str | None = None,
    ) -> str:
        """
        Pause execution and wait for user input via chat.

        Args:
            timeout: Timeout in seconds (default 5 minutes)
            preview_context: The content the human is being asked to review;
                carried on the intervention-request event so chat clients can
                render what's under review, not just a bare prompt (ENG-3243)

        Returns:
            User's input as string

        Raises:
            asyncio.TimeoutError: If no input received within timeout
            RuntimeError: If chat request fails
        """
        # Get session ID from context
        session_id = self._session_id
        if not session_id:
            raise RuntimeError("No session ID found in context")

        # ENG-2823: STABLE, deterministic request_id per (session, node).
        # The INTERACTIVE activity retries (maximum_attempts=4); a uuid4()
        # here regenerated the id on every retry, re-emitting
        # ``node.input_required`` with a fresh id (#1->#4 in the OneEdge
        # report) and orphaning any reply sent to an earlier id. A stable id
        # makes re-emits idempotent (clients dedup by request_id) and means a
        # reply always targets the same logical request.
        request_id = self._intervention_request_id(session_id)

        # Create response channel name
        response_channel = (
            f"intervention:response:{session_id}:{self.node_id}:{request_id}"
        )

        # Create an event to wait on
        response_event = asyncio.Event()

        # Initialize active requests dict if not exists
        if not hasattr(self, "_active_requests"):
            self._active_requests = {}
        self._active_requests[request_id] = response_event

        # Storage for the response
        response_data = {"input": None}

        # Initialize Redis client if not exists
        if not hasattr(self, "_redis_client"):
            from services.redis.client import RedisServiceSingleton

            self._redis_client = RedisServiceSingleton()

        # ENG-2823: durable recovery. A reply published during a previous
        # activity attempt (or while the worker was briefly down) is stored
        # at a (session, node) key by ``publish_intervention_response``.
        # Check it BEFORE blocking so a retry consumes an already-delivered
        # answer instead of re-waiting and eventually exhausting retries.
        try:
            durable = await self._redis_client.get_intervention_answer(
                session_id, self.node_id
            )
        except Exception as durable_exc:  # noqa: BLE001 - never fail on cache miss
            durable = None
            logger.warning(f"Durable intervention-answer check failed: {durable_exc}")
        if durable is not None:
            logger.info(
                f"Recovered durable intervention answer for node {self.node_id} "
                f"(request {request_id}) before waiting"
            )
            try:
                await self._redis_client.clear_intervention_answer(
                    session_id, self.node_id
                )
                await self._redis_client.clear_intervention_awaiting(session_id)
            except Exception:
                pass
            if request_id in self._active_requests:
                del self._active_requests[request_id]
            return durable

        try:
            # Subscribe to response channel
            pubsub = await self._redis_client.get_subscriber(response_channel)

            # Emit intervention request event
            event = NodeEvent.request_intervention(
                node_id=self.node_id,
                node_display_name=self.display_name,
                request_id=request_id,
                timeout=timeout,
                preview_context=preview_context,
            )
            await self._emit_node_event(event)

            # Decoupled: fire the owner notification concurrently so its DB
            # lookups + notification-service HTTP don't sit on the critical path
            # before the wait below and eat into the response window.
            notify_task = asyncio.create_task(
                self._notify_human_intervention(session_id, request_id)
            )

            # Scale display contract (ENG-3897): the run only moves ahead
            # after this human review, so the interim output under review
            # must be visible and clickable NOW -- emit the run's interim
            # files with ``display: true`` regardless of the conversation's
            # show-intermediate preference. Same decoupling rationale as the
            # notify task (DB walk + presigning off the critical path);
            # deterministic seed so activity retries re-emit idempotently.
            async def _surface_files_for_review() -> None:
                try:
                    from services.node_results.files_available import (
                        emit_human_review_files_available,
                    )

                    await emit_human_review_files_available(
                        session_id=session_id,
                        org_id=self._orgid,
                        node_instance_id=str(self.node_id) if self.node_id else None,
                        event_seed=f"human_review:{session_id}:{self.node_id}",
                    )
                except Exception as exc:  # noqa: BLE001 - never fail the wait
                    logger.warning(
                        f"Human-review files_available emit failed for "
                        f"session {session_id}: {exc}"
                    )

            review_files_task = asyncio.create_task(_surface_files_for_review())

            # ENG-2650: mark the run as awaiting human input so the A2A task
            # store can project TASK_STATE_INPUT_REQUIRED while we block on
            # the Redis wait below (the run row itself stays RUNNING). Best
            # effort -- never let a marker failure break the intervention.
            try:
                await self._redis_client.set_intervention_awaiting(
                    session_id, self.node_id, ttl=timeout
                )
            except Exception as marker_exc:
                logger.warning(
                    f"Failed to set intervention awaiting marker: {marker_exc}"
                )

            self._relay_checkpoint_opened(
                session_id=session_id,
                request_id=request_id,
                timeout=timeout,
                preview_context=preview_context,
            )

            # Persist alongside the live emit so a client that (re)opens the
            # run later can rehydrate the pending intervention from the
            # replayable event log — the Redis emit above only reaches
            # sockets that are connected right now. Ordered AFTER the awaiting
            # marker: a fast responder can answer the live emit immediately,
            # and delivery confirmation depends on that marker, so a slow
            # OpenSearch write must not delay it. ``preview_context`` is
            # truncated the same way CoT persists are so an oversized
            # content-under-review can't balloon the event log. The payload
            # is rebuilt from HumanInterventionData rather than read off
            # ``event.data`` — NodeEvent.data is typed ``str | dict | list``,
            # so pydantic coerces the model into a list of key/value pairs
            # at construction.
            from core.settings import settings

            preview_raw = (preview_context or "").encode("utf-8")
            preview_stored = (
                preview_raw[: settings.COT_MAX_BYTES].decode("utf-8", errors="ignore")
                if len(preview_raw) > settings.COT_MAX_BYTES
                else preview_context
            )
            persisted_data = HumanInterventionData(
                request_id=request_id,
                timeout=timeout,
                preview_context=preview_stored,
            )
            await self._persist_event(
                "awaiting_intervention",
                {
                    **persisted_data.model_dump(),
                    "display_type": "INTERVENTION_REQUEST",
                },
            )

            logger.info(
                f"Intervention request {request_id} emitted for node {self.node_id}, "
                f"waiting for response on channel {response_channel}"
            )

            # Start listening for response in background
            async def listen_for_response():
                try:
                    while True:
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True
                        )
                        if message and message["type"] == "message":
                            try:
                                data = json.loads(message["data"])
                                if data.get("request_id") == request_id:
                                    response_data["input"] = data.get("user_input", "")
                                    response_event.set()
                                    break
                            except json.JSONDecodeError as exc:
                                capture_exception(exc)
                                logger.error(
                                    f"Failed to parse message data: {message['data']}"
                                )
                        # ENG-2823: also poll the durable answer key so a reply
                        # that missed the pub/sub window (published with no live
                        # subscriber, e.g. a mid-retry gap) is still consumed.
                        try:
                            durable_answer = (
                                await self._redis_client.get_intervention_answer(
                                    session_id, self.node_id
                                )
                            )
                            if durable_answer is not None:
                                response_data["input"] = durable_answer
                                response_event.set()
                                break
                        except Exception:
                            pass
                        await asyncio.sleep(0.1)  # Small delay to prevent busy waiting
                except Exception as e:
                    capture_exception(e)
                    logger.error(f"Error in response listener: {e}")
                finally:
                    await pubsub.aclose()

            # Start listener task
            listener_task = asyncio.create_task(listen_for_response())

            try:
                # Wait for response with timeout
                await asyncio.wait_for(response_event.wait(), timeout=timeout)

                # Get the user input
                user_input = response_data["input"]

                logger.info(
                    f"Received user input for request {request_id}: {user_input}..."
                )

                # ENG-2823: consumed -> clear the durable answer so a later
                # intervention at this node doesn't pick up a stale reply.
                try:
                    await self._redis_client.clear_intervention_answer(
                        session_id, self.node_id
                    )
                except Exception:
                    pass

                return user_input

            except asyncio.TimeoutError:
                # Deadline reconcile: ``publish_intervention_response`` writes
                # the durable answer key BEFORE the pub/sub emit, so a reply
                # committed moments before the deadline can lose the listener
                # race with ``wait_for`` while the decision itself already
                # stands (and the publisher has already emitted
                # ``resolution: "answered"``). Consume it in preference to
                # expiring -- a decision inside the window always wins, and
                # expiring here would double-resolve the checkpoint.
                # Read STRICTLY: a swallowed Redis error would return None,
                # which is indistinguishable from "nobody answered" -- and
                # auto-approval would then treat an unverified state as
                # consent. Fail the gate instead; the toggle's contract is
                # silence means consent, not "assume silence".
                try:
                    late_answer = await self._redis_client.get_intervention_answer(
                        session_id, self.node_id, raise_on_error=True
                    )
                except Exception as read_exc:
                    raise InterventionSilenceUnverifiable(
                        "Human intervention window ended but the response "
                        "store was unreachable, so no-response could not be "
                        "confirmed"
                    ) from read_exc
                if late_answer is not None:
                    logger.info(
                        f"Consumed answer for request {request_id} that landed "
                        f"at the deadline; treating as answered, not expired"
                    )
                    try:
                        await self._redis_client.clear_intervention_answer(
                            session_id, self.node_id
                        )
                    except Exception:
                        pass
                    return late_answer
                # Not captured to Sentry here: the caller decides whether the
                # expiry is an error (fail path captures it in ``execute()``)
                # or expected (auto-approve). Capturing at both layers also
                # double-reported every fail-path timeout.
                logger.warning(
                    f"Chat request {request_id} timed out after {timeout} seconds"
                )
                # The decision is now the timeout's. Drop any answer written
                # between the reconcile read and here so a later re-entry
                # can't consume it as a fresh human decision and resolve the
                # same checkpoint twice. (An answer arriving after this point
                # still lands in the key; closing that fully needs an atomic
                # resolved-claim, which is deliberately out of scope here.)
                try:
                    await self._redis_client.clear_intervention_answer(
                        session_id, self.node_id
                    )
                except Exception:
                    pass
                raise InterventionWaitExpired(
                    f"No user input received within {timeout} seconds"
                )
            finally:
                # Cancel listener task if still running
                if not listener_task.done():
                    listener_task.cancel()
                    try:
                        await listener_task
                    except asyncio.CancelledError:
                        pass
                # The owner notification is best-effort and decoupled from the
                # wait; cancel it if it's still in flight once the wait ends
                # (the user has already responded or it has timed out).
                if not notify_task.done():
                    notify_task.cancel()
                    try:
                        await notify_task
                    except asyncio.CancelledError:
                        pass
                # Same for the review-files surfacing: once the wait ends the
                # checkpoint moment has passed, so an in-flight emit is moot.
                if not review_files_task.done():
                    review_files_task.cancel()
                    try:
                        await review_files_task
                    except asyncio.CancelledError:
                        pass

        finally:
            # Clean up
            if request_id in self._active_requests:
                del self._active_requests[request_id]
            # ENG-2650: clear the awaiting marker so the task store stops
            # reporting INPUT_REQUIRED once the wait ends (response, timeout,
            # or error).
            try:
                await self._redis_client.clear_intervention_awaiting(session_id)
            except Exception as marker_exc:
                logger.warning(
                    f"Failed to clear intervention awaiting marker: {marker_exc}"
                )

    async def _notify_human_intervention(
        self, session_id: str, request_id: str
    ) -> None:
        """Notify the run owner that the workflow is waiting on them.

        Resolves the owner via a fresh DB session. Best-effort: any
        failure is captured to Sentry and swallowed so the wait is never
        disrupted by a notification problem.
        """
        from db.session import async_session_factory
        from services.notifications import Recipient, notify
        from services.users import User as UserService
        from services.workflows import Workflow as WorkflowService

        try:
            factory = async_session_factory(use_fresh_engine=True)
            async with factory() as db_session:
                wf_service = WorkflowService(db_session)
                run = await wf_service.get_run_by_session(session_id)
                if run is None:
                    return
                owner = await UserService(db_session).get_by_id(run.run_by_id)
                workflow = await wf_service.get_by_id(run.workflow_id)
                org_id = workflow.org_id if workflow else None
                workflow_id = str(run.workflow_id)
                workflow_name = workflow.name if workflow else None
                run_id = str(run.id)
                owner_id = run.run_by_id
                owner_email = owner.email if owner else None

            await notify(
                event_type="human_intervention",
                event_key=f"intervention:{session_id}:{self.node_id}",
                title=f"{self.display_name} needs your review to continue",
                recipients=[
                    Recipient(user_id=owner_id, email=owner_email, org_id=org_id)
                ],
                link=f"/workflow/{workflow_id}?runId={run_id}",
                metadata={
                    "run_id": run_id,
                    "session_id": session_id,
                    "workflow_id": workflow_id,
                    "workflow_name": workflow_name,
                    "node_id": self.node_id,
                    "request_id": request_id,
                },
            )
        except Exception as exc:  # noqa: BLE001 — best-effort emit
            capture_exception(exc)
            logger.warning(
                f"Human intervention notification failed for session "
                f"{session_id} node {self.node_id}: {exc}"
            )

    async def _resolve_review_display(self, preview_context: str) -> str:
        """Return the HUMAN-facing text for this checkpoint (ENG-4536).

        ``preview_context`` arrives as the author's static review
        instructions with the ENTIRE upstream output appended (text-modal
        param merge), which buried the decision under process narration.
        This distills it into a short brief and re-attaches the author's
        instructions verbatim.

        The author's instructions are read from this node's own
        ``node_config`` — the same value the param merge prepended — so
        they can be restored byte-identical instead of being paraphrased
        by the model or recovered by parsing the merged string.

        Fail-open: on any failure (model unreachable, anything unexpected)
        returns ``preview_context`` unchanged — exactly today's behavior.
        The caller must use the result ONLY for display surfaces;
        ``NodeOutput.data`` and the port emission keep the raw text so
        downstream nodes are unaffected.
        """
        try:
            from nodes.logic.human_intervention.digest import (
                build_review_digest,
                compose_review_display,
            )

            static_value = next(
                (
                    cfg.get("value")
                    for cfg in self.node_config
                    if cfg.get("field_id") == "preview_context"
                ),
                None,
            )
            instructions = (
                str(static_value) if static_value not in (None, "", []) else ""
            )

            digest_body = await build_review_digest(
                session_id=self._session_id or "",
                node_id=str(self.node_id),
                review_instructions=instructions,
                checkpoint_text=preview_context,
            )
            if digest_body is None:
                return preview_context
            return compose_review_display(instructions, digest_body)
        except Exception as exc:  # noqa: BLE001 - display polish must never fail the node
            capture_exception(exc)
            logger.warning(
                f"HITL review digest failed for node {self.node_id}; "
                f"falling back to raw preview context: {exc}"
            )
            return preview_context

    async def _emit_auto_approved_resolution(self, timeout: int) -> None:
        """Resolve the checkpoint on the event stream after an auto-approval.

        A human answer flows through ``publish_intervention_response``, which
        emits the ``INPUT_RESOLVED`` lifecycle event SSE/A2A consumers use to
        drop their outstanding-checkpoint state (projected onto the frozen
        ``node.input_resolved`` wire type). Auto-approve never touches that
        endpoint, so emit the same event here — otherwise the stream holds a
        checkpoint that only resolves by inference from a later
        ``node_completed``. Best-effort: a failed emit must never fail the
        (already approved) run.

        Args:
            timeout: The wait window that elapsed, for the human-facing text.
        """
        try:
            from api.websockets.emitters import emit_lifecycle_event
            from api.websockets.event_models import LifecyclePhase

            session_id = self._session_id or ""
            # Payload spelled out with a LITERAL ``input_kind``, matching
            # ``publish_intervention_response``'s answered emit: the
            # resolve-side guard in the telemetry-export tests statically
            # resolves this value per call site and refuses one it cannot
            # evaluate, so this must not move behind a shared wrapper.
            await emit_lifecycle_event(
                session_id,
                LifecyclePhase.INPUT_RESOLVED,
                message=(
                    f"No response within {timeout}s — auto-approved, workflow resuming"
                ),
                data={
                    "node_id": str(self.node_id),
                    "request_id": self._intervention_request_id(session_id),
                    "input_kind": "intervention",
                    "resolution": "auto_approved",
                },
            )
        except Exception as exc:  # noqa: BLE001 - best-effort emit
            capture_exception(exc)
            logger.warning(
                f"Failed to emit auto-approve input_resolved event for node "
                f"{self.node_id}: {exc}"
            )

    async def _kpi_xoa_tra_loi_cu(self) -> None:
        """[ghi chú] Xoá câu trả lời bền (intervention_answer:{session}:{node}, TTL 2 giờ — services/redis/client.py:277) TRƯỚC khi hỏi: node_id giữ nguyên
        qua các lượt cùng workflow ⇒ câu cũ / câu thứ 2 của checkpoint trước sẽ tự mở cổng lượt này (await_user_intervention đọc nó trước khi chờ)."""
        if not hasattr(self, "_redis_client"):
            from services.redis.client import RedisServiceSingleton

            self._redis_client = RedisServiceSingleton()
        session_id = self._session_id or ""
        cu = await self._redis_client.get_intervention_answer(session_id, self.node_id)
        if cu is not None:
            logger.warning("[chay_thu KPI] bỏ câu trả lời CŨ của checkpoint (session, khối %s) — lời duyệt không mang sang lượt / checkpoint khác",
                           self.node_id)
            await self._redis_client.clear_intervention_answer(session_id, self.node_id)

    async def execute(self):
        try:
            await self.emit_begin()
            self._validated_params = await self._get_validated_params()
            preview_context = self._validated_params.get("preview_context", None)
            if not preview_context:
                error_msg = "Preview context should not be empty"
                await self.emit_error({"error": error_msg, "details": error_msg})
                raise Exception(error_msg)

            timeout = self._validated_params.get("timeout", 300)
            timeout = int(timeout)
            if not timeout:
                error_msg = "Timeout seconds should not be empty"
                await self.emit_error({"error": error_msg, "details": error_msg})
                raise Exception(error_msg)

            kpi = _kpi_bat(self._validated_params)   # [ghi chú] chế độ KPI
            # [ghi chú] Workflow bật ô "chế độ KPI" mà cờ máy CHAY_THU_NGUOI_DUYET đang TẮT ⇒ _kpi_bat() sai ⇒ khối sẽ chạy như chế độ
            # THƯỜNG: gói KPI thành chữ, và (từ khi mẫu KPI bật ô tự duyệt) hết hạn là tự duyệt kiểu Capitol gốc, KHÔNG nhãn, không file nháp. Không cho rơi lặng lẽ: LỖI RÕ.
            if not kpi and toggle_enabled((self._validated_params or {}).get("che_do_kpi", False)):
                raise ValueError("chế độ KPI đang bật ở workflow nhưng cờ máy CHAY_THU_NGUOI_DUYET đang tắt — khối không chạy bảng thưởng ở chế độ thường; bật lại cờ rồi chạy lại")
            goi = _doc_goi_kpi(preview_context) if kpi else None
            if not kpi and not isinstance(preview_context, str):   # [D m3] reducer giữ dict gói KPI ⇒ chế độ thường vẫn chữ như Capitol gốc
                preview_context = str(preview_context)
            sha_hien = goi["sha256_bang"] if kpi else None
            # ENG-4536: what the HUMAN sees. The digest distills the upstream
            # content into a decision brief; the raw ``preview_context`` stays
            # on ``NodeOutput.data`` + the output port untouched, so
            # downstream consumers are byte-identical to before.
            # [ghi chú] chế độ KPI: KHÔNG gọi tóm tắt AI — dựng bằng code
            display_context = _hien_kpi(goi, self._validated_params.get("reviewer_note")) if kpi else \
                await self._resolve_review_display(str(preview_context))
            # [ghi chú] cờ CHAY_THU_NGUOI_DUYET bật (mặc định) ⇒ người duyệt thấy: ghi chú riêng + bản Capitol (tóm tắt / lời
            # dặn) + NGUYÊN VĂN đúng chữ sẽ gửi xuống khối sau. data / cổng ra KHÔNG đổi (vẫn preview_context thô) — chỉ đổi phần hiển thị.
            if _nguoi_duyet_bat() and not kpi:
                display_context = _hien_cho_nguoi_duyet(
                    self._validated_params.get("reviewer_note"), display_context, str(preview_context)
                )

            output = NodeOutput(
                data=goi if kpi else str(preview_context),  # Convert to string for consistency  [ghi chú]
                display_model=MarkdownDisplay(
                    display_name="Agent Result",
                    field_id="agent_result",
                    required=False,
                    value=display_context,
                    info="Agents Result",
                    show=True,
                ),
                node=self,
            )
            await self.emit_success("Human intervention received!")
            await self.emit_node_result(output)

            # Display the review brief to the user
            await self.emit_progress(
                f"Waiting for user intervention: {display_context}",
                {"preview_context": display_context, "timeout": timeout},
            )

            # Wait for user decision (continue/stop)
            logger.info(f"Requesting user intervention with timeout {timeout}s")
            auto_approve = toggle_enabled(
                self._validated_params.get("auto_approve_on_timeout", False)
            )   # [ghi chú] bỏ "and not kpi" (C 10/11: KPI hết giờ ⇒ dừng dù bật): nay chế độ
            #     KPI + ô bật + hết hạn ⇒ đi tiếp theo nhánh TỰ DUYỆT bên dưới (không ghi "đã duyệt" vào lõi, file là bản NHÁP có nhãn). Ô tắt ⇒ hết hạn vẫn DỪNG như cũ.
            if kpi and auto_approve and not 30 <= timeout <= 3600:   # hạn chờ lạ ở chế độ KPI ⇒ không tự duyệt (luật kiểm workflow nhóm kỹ thuật cũng chặn; đây là lớp trong khối)
                raise ValueError("chế độ KPI: bật tự duyệt khi quá hạn thì hạn chờ phải từ 30 tới 3600 giây")
            if kpi:
                await self._kpi_xoa_tra_loi_cu()
            timed_out = False
            try:
                user_decision = await self.await_user_intervention(
                    timeout=timeout, preview_context=display_context
                )
            except InterventionWaitExpired:
                # Opt-in auto-approval: silence past the deadline counts as
                # consent, so fall through to the continue branch below and
                # reuse everything it does (port emission, emit_end, success
                # output). When the toggle is off, re-raise so the outer
                # handler fails the node exactly as before. Only the typed
                # wait-boundary expiry lands here -- an infrastructure
                # ``TimeoutError`` from the wait's setup (subscribe / emit /
                # persist) is NOT silence and takes the failure path.
                if not auto_approve:
                    await self._relay_checkpoint_closed(OUTCOME_TIMED_OUT, wait=True)
                    raise
                timed_out = True
                user_decision = "continue"

            # Log the decision
            logger.info(f"User decision received: {user_decision}")

            # Guard against a None/empty decision (durable path or a bare
            # reply): default to continue rather than AttributeError on
            # ``.lower()``. The submit surfaces treat empty as "continue".
            # [ghi chú] MỌI chế độ dùng CÙNG điều kiện chặt (trước: chỉ chế độ KPI): chỉ đúng chữ
            # "continue" mới mở cổng. Quyết định None / rỗng (đường đọc lại khi hết hạn / chạy lại, câu trả lời trống) KHÔNG còn bị coi là đồng ý ở chế độ thường —
            # rơi xuống nhánh "không duyệt" bên dưới như mọi chữ khác "continue". Tự duyệt khi hết giờ (ô Auto-approve on timeout) là nhánh khác, không đổi.
            if str(user_decision or "").strip().lower() == "continue":   # [ghi chú] KPI: rỗng / lạ ≠ duyệt · [ghi chú] áp cho cả chế độ thường
                goi_ra = None
                if kpi:   # mở cổng: bảng sắp chuyển đi phải đúng bảng đã hiện
                    if _sha_bang(goi["bang_thuong"]) != sha_hien:
                        raise ValueError("chế độ KPI: bảng lúc mở cổng KHÁC bảng đã hiện cho người duyệt (sha lệch)")
                    # [D Đ-9, đã chọn (b)] self._user_id là NGƯỜI CHẠY lượt (temporal/utils/node_factory.py:121), câu trả lời qua Redis chỉ mang chữ
                    # quyết định (không mã người bấm) ⇒ KHÔNG gọi là người duyệt; chưa tách người duyệt ≠ người chạy (AUTH tắt, 1 người dùng máy local).
                    self._kpi_dv = goi.get("data_version") or ""   # [va_data_version] từ gói Rào chắn
                    if timed_out:
                        # [ghi chú] TỰ DUYỆT KHI QUÁ HẠN: KHÔNG gọi /api/system/duyet (lõi KHÔNG ghi "đã duyệt" — chữ đó chỉ dành cho lúc có
                        # người bấm), KHÔNG lấy file chính thức. Gói ra mang duyet.kieu = "tu_duyet_qua_han" (không có ô người); file kèm là bản NHÁP do code dựng,
                        # dòng đầu mỗi sheet + tên file mang nhãn tự duyệt. Lỗi khi dựng ⇒ NÉM ⇒ khối lỗi, lượt dừng, không file.
                        goi_ra = {**goi, "duyet": {"kieu": "tu_duyet_qua_han", "cho_giay": timeout, "sha256_bang": sha_hien, "luc": time.strftime("%Y-%m-%dT%H:%M:%S%z")}}
                        nf, tom = await _kpi_nhap_tu_duyet(self, goi, sha_hien, timeout)
                        logger.warning("human_intervention KPI_TU_DUYET_QUA_HAN cho_giay=%d so_o=%d", timeout, tom["so_o_da_so"])   # không số tiền, không mã NV
                    else:
                        goi_ra = {**goi, "duyet": {"kieu": "nguoi", "sha256_bang": sha_hien, "luc": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                                    "nguoi_chay_luot": self._user_id or self._user_email or "",
                                                    "tach_nguoi_duyet": "chưa — AUTH tắt, 1 người dùng máy local"}}
                        # [va_xuat_excel] DUYỆT ⇒ xuất Excel + kiểm ô bằng code TRƯỚC khi mở cổng; lỗi ⇒ NÉM ⇒ khối lỗi, lượt dừng, không file
                        nf, tom = await _kpi_xuat_excel(self, goi["bang_thuong"], sha_hien, goi_ra["duyet"]["nguoi_chay_luot"])
                        logger.warning("human_intervention KPI_XUAT_EXCEL so_o=%d", tom["so_o_da_so"])   # không số tiền, không mã NV
                    goi_ra["xuat_excel"] = tom
                    output.files = [nf]
                    output.data = goi_ra
                await self._relay_checkpoint_closed(
                    OUTCOME_AUTO_APPROVED if timed_out else OUTCOME_APPROVED
                )
                if timed_out:
                    # No human answered, so nothing came through the response
                    # endpoint that normally resolves the checkpoint on the
                    # stream — resolve it from here, and mark the decision as
                    # the machine's, not a person's, in the run record.
                    await self._emit_auto_approved_resolution(timeout)
                    await self.emit_progress(
                        f"No response within {timeout}s; auto-approved and "
                        "continuing workflow execution",
                        {"decision": "auto_approved", "timed_out": True},
                    )
                else:
                    # User decided to continue (or any other input defaults
                    # to continue)
                    await self.emit_progress(
                        "User decided to continue workflow execution",
                        {"decision": "continue"},
                    )

                # Emit to output port if runtime is available
                if hasattr(self, "runtime") and self.runtime:
                    # AgentNode has a text output port
                    for port in self.output_ports:
                        await self.runtime.emit_output(port.id, goi_ra if kpi else preview_context)

                # Emit end event to mark completion
                await self.emit_end()
                return output

            else:
                await self._relay_checkpoint_closed(OUTCOME_STOPPED, wait=True)
                raise asyncio.CancelledError()

        except asyncio.CancelledError:
            # A declined gate has already closed its checkpoint above; only a
            # run stopped from outside reaches the relay here.
            await self._relay_checkpoint_closed(OUTCOME_RUN_STOPPED, wait=True)
            raise
        except asyncio.TimeoutError as exc:
            capture_exception(exc)
            error_msg = f"Human intervention timed out after {timeout} seconds"
            logger.warning(error_msg)
            await self.emit_error({"error": error_msg, "timeout": timeout})
            await self.emit_end({"error": error_msg, "timed_out": True})
            return NodeOutput(
                data={"decision": "timeout", "timed_out": True},
                node=self,
                error=error_msg,
            )
        except Exception as e:
            capture_exception(e)
            error_msg = f"Human Intervention node failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            await self.emit_error({"error": error_msg, "details": str(e)})
            await self.emit_end({"error": error_msg})
            return NodeOutput(data=None, node=self, error=error_msg)
