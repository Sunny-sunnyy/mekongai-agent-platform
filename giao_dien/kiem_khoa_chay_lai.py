"""[ghi chú kỹ thuật] KIỂM KHOÁ trước khi "chạy lại từ khối" (phần Capitol để ngỏ: _seed_cached_results
không gọi compute_node_cache_key — E xác nhận). Chạy TRONG hộp capitol-ab-api, dùng CHÍNH hàm của Capitol (không chép lại):
transform_advanced_to_backend + model_dump(mode="json") (như prepare_workflow.py:508) · build_upstream_dependencies (temporal/utils/downstream.py:103)
· compute_node_cache_key (temporal/utils/cache.py:126) · build_downstream_set. Chỉ ĐỌC Postgres; không gọi AI, không khởi lượt.

Hai việc:
  * trung_thanh(phien): tính lại khoá MỌI khối trên ĐÚNG phiên bản workflow lượt đó đã chạy ⇒ so node_result.cache_key ⇒ phải trùng hết
    (chứng minh cách tính của mình = cách Capitol tính lúc chạy).
  * tap_chay_moi(phien_nguon, payload_moi, khoi_bat_dau): tập chạy mới = khối chọn + phía sau (như Capitol) ∪ mọi khối có khoá TÍNH TRÊN PAYLOAD
    MỚI ≠ khoá đã lưu ở phiên nguồn (+ phía sau của chúng) ⇒ trả {tap, lech: {khối: lý do}}. Khối không có kết quả COMPLETED ở nguồn ⇒ chạy mới.
Dùng (trong hộp): python -I kiem_khoa.py trung_thanh <mã phiên 8+ ký tự> …
"""
from __future__ import annotations

import json
import logging
import os
import sys

logging.disable(logging.CRITICAL)
sys.path[:0] = ["/srv/src", "/srv"]
from temporal.utils.cache import compute_node_cache_key  # noqa: E402
from temporal.utils.downstream import build_downstream_set, build_upstream_dependencies  # noqa: E402
from workflow.runtime.payload_transformer import transform_advanced_to_backend  # noqa: E402

from temporal.utils.node_exclusion import is_excluded_from_execution  # noqa: E402 — handle (tool / memory) + ghi chú + evals khi tắt


def khoa_ca_luong(payload_nang_cao: dict) -> tuple[dict[str, str], dict]:
    """{react_flow_generated_id: cache_key} theo thứ tự phụ thuộc, đúng cách agentic_workflow._compute_cache_key (:1737-1753)."""
    wp = transform_advanced_to_backend(payload_nang_cao).model_dump(mode="json")
    nodes, edges = wp.get("nodes", []), wp.get("edges", [])
    handle = {n["react_flow_generated_id"] for n in nodes if is_excluded_from_execution(n, run_evals=True)}   # như prepare_workflow (run_evals=True ở bộ mình)
    up = build_upstream_dependencies(nodes=nodes, edges=edges, handle_node_ids=handle)
    nm = {n["react_flow_generated_id"]: n for n in nodes if n["react_flow_generated_id"] not in handle}
    khoa, con = {}, dict(nm)
    while con:
        san = [k for k in con if all(u in khoa or u not in nm for u in up.get(k, set()))]
        if not san:   # vòng ⇒ không tính được (Capitol cũng không chạy vòng) — để trống
            break
        for k in sorted(san):
            khoa[k] = compute_node_cache_key(node_dict=con.pop(k), upstream_cache_keys={u: khoa.get(u) for u in up.get(k, set())})
    return khoa, {"nodes": nodes, "edges": edges, "handle": handle}


def trung_thanh(payload: dict, luu: dict[str, str]) -> dict:
    tinh, _ = khoa_ca_luong(payload)
    chung = [k for k in luu if k in tinh]
    lech = [k for k in chung if tinh[k] != luu[k]]
    return {"khoi_co_khoa_luu": len(luu), "so_duoc": len(chung), "trung": len(chung) - len(lech), "lech": lech[:10]}


def tap_chay_moi(payload_moi: dict, luu_nguon: dict[str, str], khoi_bat_dau: list[str], single_step: bool = False) -> dict:
    """[ghi chú kỹ thuật] single_step (Capitol agentic_workflow.py:935-945): CHỈ khối chọn chạy mới, mọi khối phía sau CỐ Ý giữ cache ⇒ khoá của chúng
    luôn lệch khi khối chọn đổi — KHÔNG ép chạy lại (đúng ý chế độ này) nhưng liệt kê ở `sau_dung_lai_cu` để trang báo "khối sau dùng kết quả
    cũ". Khối KHÔNG ở phía sau khối chọn mà lệch khoá ⇒ vẫn ép chạy mới (như chế độ thường)."""
    tinh, g = khoa_ca_luong(payload_moi)
    sau = set(build_downstream_set(starting_node_ids=khoi_bat_dau, nodes=g["nodes"], edges=g["edges"],
                                   handle_node_ids=g["handle"], include_self=True))
    tap = set(khoi_bat_dau) if single_step else set(sau)
    lech, sau_cu = {}, []
    for k, v in tinh.items():
        if k in tap:
            continue
        if single_step and k in sau:
            sau_cu.append(k)
            continue
        if k not in luu_nguon:
            lech[k] = "không có kết quả xong ở lượt nguồn"
        elif luu_nguon[k] != v:
            lech[k] = "cấu hình khối (hoặc khối phía trước) đã đổi so với lượt nguồn"
    if lech:
        tap |= set(build_downstream_set(starting_node_ids=sorted(lech), nodes=g["nodes"], edges=g["edges"],
                                        handle_node_ids=g["handle"], include_self=True))
    return {"tap_chay_moi": sorted(tap), "lech": lech, "dung_lai": sorted(set(tinh) - tap), "sau_dung_lai_cu": sorted(sau_cu)}


if __name__ == "__main__":
    # Dữ liệu đưa vào qua file JSON (đối chiếu kỹ thuật): {"payload": …, "luu": {inst: cache_key}, …}
    viec, f = sys.argv[1], sys.argv[2]
    d = json.load(open(f))
    if viec == "trung_thanh":
        print(json.dumps(trung_thanh(d["payload"], d["luu"]), ensure_ascii=False))
    elif viec == "tap":
        print(json.dumps(tap_chay_moi(d["payload"], d["luu"], d["bat_dau"], bool(d.get("single_step"))), ensure_ascii=False))
