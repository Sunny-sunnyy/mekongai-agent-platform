"""[VÁ nạp khối theo mã, 09/11/2026 — nhóm kỹ thuật, đã giao (mục 65, người vận hành hướng "vá phòng thủ")]
Gắn vào hộp ở /srv/src/workflow/nap_khoi.py (chưa gắn).

Vì sao: kiểm quyền theo tổ chức (AB api/handlers/nodes/validate_node_access.py:26-73) và R12 của mình
(sua_doi/workflow/r12_kiem.py:102, 130-132, 182) xét loại khối theo `node_id`. Nhưng bộ chạy lại chọn class theo hai chuỗi
`module` + `class_name` đi kèm yêu cầu (AB workflow/runtime/workflow_executor.py:195-196, 521-522; temporal/utils/node_factory.py:83-87)
[code, agentic-backend release-4.10.0]. Hai bên nhìn hai thứ khác nhau ⇒ phải để cả ba (kiểm quyền, R12, bộ chạy) cùng dựa vào MỘT
sự thật: `node_id`.

Cách làm: dựng bảng `node_id ⇒ class` từ sổ đăng ký của chính code (class có @register, AB workflow/node/registry/registry.py:69-82),
rồi luôn lấy class từ bảng. `module` / `class_name` của yêu cầu chỉ dùng để ghi cảnh báo khi lệch, không bao giờ để import.
Mã không có trong sổ ⇒ từ chối (ValueError) — trước đây import được cả class chưa đăng ký; các loại đó vốn đã bị kiểm quyền chặn
(không nằm trong danh sách cho phép — D mục 64 bảng (2)).
Không đụng khối công cụ: đường đó dựng class từ `node_id`, không dùng module / class_name (node_factory.py:256-293,
workflow_executor.py:286-300).
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_khoa = threading.Lock()
_bang: dict[str, type] | None = None


def _dung_bang() -> dict[str, type]:
    """Bảng node_id ⇒ class, dựng một lần mỗi tiến trình (dò thư mục nodes/ như API danh mục làm — AB api/handlers/nodes/list.py:118)."""
    global _bang
    if _bang is not None:
        return _bang
    with _khoa:
        if _bang is None:
            from workflow.node.registry import get_node_registry

            reg = get_node_registry()
            reg.discover_and_register_nodes(package_names=["nodes"])
            bang: dict[str, type] = {}
            trung: set[str] = set()
            for cls in reg.get_all_nodes().values():
                ma = getattr(cls, "node_id", None)
                if not isinstance(ma, str) or not ma:
                    continue
                if ma in bang and bang[ma] is not cls:
                    trung.add(ma)
                bang[ma] = cls
            # Hai class cùng mã ⇒ không biết chọn cái nào ⇒ bỏ mã đó (từ chối khi chạy) thay vì đoán.
            for ma in trung:
                logger.error("[NAP_KHOI] mã %s có nhiều class trong sổ đăng ký — bỏ, khối loại này sẽ bị từ chối", ma)
                bang.pop(ma, None)
            _bang = bang
            logger.info("[NAP_KHOI] bảng node_id ⇒ class: %d mã", len(bang))
    return _bang


def lay_class(node_id: str | None, module: str | None = None, class_name: str | None = None) -> type:
    """Class của khối theo `node_id` (từ sổ đăng ký). `module` / `class_name` chỉ để so và ghi cảnh báo."""
    cls = _dung_bang().get(node_id or "")
    if cls is None:
        raise ValueError(f"Loại khối '{node_id}' không có trong sổ đăng ký khối — từ chối chạy")
    if (module, class_name) != (cls.__module__, cls.__name__):
        logger.warning("[NAP_KHOI] khối %s: yêu cầu ghi %s.%s, dùng class trong sổ %s.%s",
                       node_id, module, class_name, cls.__module__, cls.__name__)
    return cls
