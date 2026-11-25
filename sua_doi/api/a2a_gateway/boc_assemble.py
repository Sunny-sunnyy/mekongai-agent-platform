"""Móc BƯỚC KIỂM SAU KHI GHÉP vào bộ công cụ dựng workflow của Operator — C 16/11/2026 (đã giao; hướng (c): câu KPI ⇒ Operator CHỌN workflow
KPI có sẵn, KHÔNG tự dựng; bước kiểm áp cho mọi workflow ghép ra). BẢN THỬ, CHƯA GẮN.

Gắn (khi A cho): chép file này + kiem_sau_ghep.py vào /srv/src/api/a2a_gateway/ (bind :ro như các bản vá khác), rồi trong orchestrator.py ngay sau
  builder = make_workflow_builder_toolset(...)
thêm 2 dòng (chỉ khi hồ sơ chay_thu_khong_chay — chat trên trang):
  if builder is not None and agent.get("chay_thu_khong_chay") is True:
      builder.process_tool_call = boc(builder.process_tool_call, doc_payload=_doc_payload_chay_thu, r12=_r12_chay_thu)
Bộ công cụ được dựng MỚI cho mỗi lượt chat [suy luận — chưa đo] ⇒ bộ đếm vòng trong closure là theo lượt.

Đường đi: mô hình gọi wb_assemble_workflow ⇒ boc: (1) đã quá 3 lần gọi trong lượt ⇒ TỪ CHỐI bằng code, không gọi bộ dựng; (2) gọi bộ dựng thật;
(3) đọc mã workflow trong KẾT QUẢ (không tin đối số mô hình gửi); (4) đọc payload ĐÃ LƯU theo mã; (5) kiem_an_toan (lỗi ⇒ chặn); (6) nối chữ kiểm
vào kết quả trả mô hình. Không đọc được mã / payload ⇒ "chưa đạt". Workflow chưa đạt vẫn nằm trong danh sách (không xoá — A: vô hại).
"""
from __future__ import annotations

import json
import re

try:   # trong hộp: cùng thư mục với orchestrator; ngoài hộp (phép thử): cùng thư mục với file này
    from . import kiem_sau_ghep as KSG   # type: ignore
except ImportError:  # noqa: BLE001
    import kiem_sau_ghep as KSG   # type: ignore

_MA = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _ten_goc(name: str) -> str:
    return name[3:] if str(name).startswith("wb_") else str(name)


def _lay_ma(kq) -> str | None:
    """Mã workflow ở MỨC TRÊN CÙNG của kết quả bộ dựng (workflow_id / built_workflow_id). Chuỗi JSON / dict / đối tượng có .content."""
    x = kq
    for _ in range(3):
        if isinstance(x, dict):
            for k in ("workflow_id", "built_workflow_id", "id"):
                if isinstance(x.get(k), str) and _MA.fullmatch(x[k]):
                    return x[k]
            return None
        if isinstance(x, str):
            try:
                x = json.loads(x); continue
            except ValueError:
                m = re.search(r'"workflow_id"\s*:\s*"(' + _MA.pattern + ')"', x)
                return m.group(1) if m else None
        c = getattr(x, "structured_content", None) or getattr(x, "content", None)
        if c is None:
            return None
        x = c[0].text if isinstance(c, list) and c and hasattr(c[0], "text") else c
    return None


def _noi(kq, chu: str):
    if isinstance(kq, str):
        return kq + "\n\n" + chu
    if isinstance(kq, dict):
        return {**kq, "kiem_bo_chay_thu": chu}
    return {"ket_qua_bo_dung": str(kq)[:4000], "kiem_bo_chay_thu": chu}


def luat_kpi_chon_co_san(pl) -> list[str]:
    """Hướng (c) đã chốt 16/11: workflow tự ghép mà có Agent KPI ⇒ không dùng; chọn workflow KPI có sẵn."""
    return ["câu về KPI: KHÔNG tự dựng workflow — gọi list_workflows, chọn workflow KPI có sẵn phù hợp nhất rồi báo tên + mã cho người dùng"]


def boc(goc, *, doc_payload, r12=None):
    """goc: process_tool_call cũ (ctx, call_tool, name, args) ⇒ kết quả, hoặc None (gọi thẳng call_tool). doc_payload(ma) ⇒ payload đã lưu (async hoặc thường). r12(pl) ⇒ None | lỗi."""
    dem = {"vong": 0}

    async def _goi(ctx, call_tool, name, args):
        # [E blocker 16/11] make_workflow_builder_toolset KHÔNG truyền process_tool_call (workflow_builder_toolset.py:261-267) ⇒ goc = None
        # ⇒ gọi thẳng call_tool(name, args) như MCP toolset tự làm khi không có process_tool_call
        if goc is None:
            return await call_tool(name, args)
        return await goc(ctx, call_tool, name, args)

    async def _boc(ctx, call_tool, name, args):
        if _ten_goc(name) != "assemble_workflow":
            return await _goi(ctx, call_tool, name, args)
        dem["vong"] += 1
        if not KSG.cho_phep_assemble(dem["vong"]):
            return ("KIỂM CỦA BỘ CHẠY THỬ: đã sửa đủ 2 lần mà chưa đạt — KHÔNG ghép thêm trong lượt này. Báo người dùng workflow chưa chạy được "
                    "và các lỗi lần trước.")
        kq = await _goi(ctx, call_tool, name, args)
        ma = _lay_ma(kq)
        if not ma:
            return _noi(kq, "KIỂM CỦA BỘ CHẠY THỬ: không đọc được mã workflow vừa ghép ⇒ coi như CHƯA ĐẠT.")
        try:
            pl = doc_payload(ma)
            if hasattr(pl, "__await__"):
                pl = await pl
        except Exception as e:  # noqa: BLE001
            return _noi(kq, f"KIỂM CỦA BỘ CHẠY THỬ: không đọc được workflow {ma[:8]} ({type(e).__name__}) ⇒ coi như CHƯA ĐẠT.")
        loi = KSG.kiem_an_toan(pl, r12, luat_kpi_chon_co_san)
        return _noi(kq, KSG.tra_cho_mo_hinh(loi, dem["vong"]))

    return _boc
