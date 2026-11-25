"""Khối hỏi kho bảng (collections_node) trên trang 18400 — chỉ cho chọn dataset thuộc ĐÚNG tổ chức của trang (đối chiếu kỹ thuật) của E;
ca ghi trước: tài liệu thiết kế nội bộ mục (15) D1–D4).

Vì sao cần lớp này dù khối gốc đã tra theo tổ chức của lượt (AB nodes/datasets/structured/node.py:64-90: list_datasets(orgid) rồi
datasets.get(id) — mã lạ ⇒ ValueError): lỗi đó nổ LÚC CHẠY, sau khi đã giữ chỗ tiền + mở phiên; và workflow do chat / script / API dựng
không qua form của trang. Kiểm ở /api/check + Lưu + chay() ⇒ chặn TRƯỚC giữ chỗ, báo rõ ô nào sai.

Danh sách dataset của tổ chức: GET /collections/{org}?data-type=structured của platform-api — chỉ ĐỌC DynamoDB (routes/collections.py:2292-2436
bản 1.82.0: list_collections(orgid); không token người dùng ⇒ không chạy nhánh tự tạo kho Sources), không gọi AI. Cổng máy 18301 không mở
đường này (Caddyfile, danh sách cho phép của B) ⇒ đi bằng `docker exec capitol-platform-api python` gọi 127.0.0.1:80 trong hộp (cùng kiểu
chat_operator gọi psql). Không đọc được ⇒ CHẶN (lệch phía an toàn), không đoán.
Giá trị ô datasets đúng khuôn khối gốc (node.py:116-118): [{"option_id": <mã dataset>, "option_display_name": <tên>}, …], 1–5 phần tử
(muc_toi_da KB_TRAN_DATASET = 5 ⇒ > 5 không có mức ⇒ trang chặn ở bước tiền; ở đây báo sớm hơn, cùng con số).
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time

HOP_PLATFORM = "capitol-platform-api"
LOAI = "collections_node"
TRAN_DATASET = 5          # = muc_toi_da.KB_TRAN_DATASET
GIAY_NHO = 30             # nhớ danh sách 30 s (Kiểm + Lưu + Chạy liền nhau chỉ hỏi một lần)
_MA = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_NHO: dict[str, tuple[float, dict]] = {}
_KHOA = threading.Lock()

_DOC = r"""
import json, sys, urllib.request
o = sys.argv[1]
r = urllib.request.urlopen("http://127.0.0.1:80/collections/" + o + "?data-type=structured", timeout=20)
ra = {}
for c in json.loads(r.read()):
    if not isinstance(c, dict) or c.get("data_type") != "structured":
        continue
    for d in (c.get("config") or {}).get("datasets") or []:
        if isinstance(d, dict) and d.get("id"):
            ra[str(d["id"])] = {"ten": str(d.get("name") or ""), "kho": str(c.get("name") or ""), "kho_id": str(c.get("id") or "")}
print(json.dumps(ra))
"""


def doc_platform(org: str) -> dict:
    """{mã dataset: {ten, kho, kho_id}} của tổ chức. Lỗi ⇒ NÉM (người gọi chặn)."""
    if not _MA.fullmatch(str(org)):
        raise ValueError("mã tổ chức không đúng khuôn")
    r = subprocess.run(["docker", "exec", "-i", HOP_PLATFORM, "python", "-I", "-", org], input=_DOC, capture_output=True, text=True, timeout=40)
    if r.returncode != 0:
        raise RuntimeError(f"đọc danh sách dataset lỗi (mã {r.returncode})")
    d = json.loads(r.stdout.strip().splitlines()[-1])
    if not isinstance(d, dict):
        raise RuntimeError("danh sách dataset sai dạng")
    return d


def ds_dataset(org: str, doc=doc_platform, bay_gio: float | None = None) -> dict:
    bay_gio = time.time() if bay_gio is None else bay_gio
    with _KHOA:
        x = _NHO.get(org)
        if x and bay_gio - x[0] < GIAY_NHO:
            return x[1]
    d = doc(org)
    with _KHOA:
        _NHO[org] = (bay_gio, d)
    return d


def _khoi(payload: dict):
    for n in payload.get("nodes") or []:
        st = (n.get("data") or {}).get("struct") or {}
        if st.get("node_id") == LOAI:
            ten = (n.get("data") or {}).get("label") or st.get("display_name") or n.get("id")
            v = next((p.get("value") for p in st.get("params") or [] if p.get("field_id") == "datasets"), None)
            yield n.get("id"), ten, v


def kiem(payload: dict, org: str, ds=ds_dataset) -> list[dict]:
    """Payload dạng Capitol (nodes[].data.struct) ⇒ [{node, message}] — rỗng = đạt. Không có khối collections_node ⇒ không hỏi platform."""
    khoi = list(_khoi(payload))
    if not khoi:
        return []
    try:
        cua_org = ds(org)
    except Exception as e:  # noqa: BLE001 — không biết dataset nào của tổ chức ⇒ chặn
        return [{"node": k[0], "message": f"“{k[1]}”: chưa đọc được danh sách kho bảng của tổ chức ({type(e).__name__}) — chưa kiểm được ô Dataset, "
                 "không chạy."} for k in khoi]
    loi = []
    for ma, ten, v in khoi:
        if not isinstance(v, list) or not v:
            loi.append({"node": ma, "message": f"“{ten}”: chưa chọn dataset nào (chọn 1–{TRAN_DATASET} dataset của tổ chức)."})
            continue
        if len(v) > TRAN_DATASET:
            loi.append({"node": ma, "message": f"“{ten}”: chọn {len(v)} dataset — tối đa {TRAN_DATASET} mỗi khối (mỗi dataset tốn tới 2 lần gọi AI)."})
            continue
        thay = set()
        for x in v:
            oid = x.get("option_id") if isinstance(x, dict) else None
            if not isinstance(oid, str) or not oid:
                loi.append({"node": ma, "message": f"“{ten}”: một mục trong ô Dataset sai khuôn (cần option_id)."})
            elif oid not in cua_org:
                loi.append({"node": ma, "message": f"“{ten}”: dataset “{oid[:40]}” không thuộc kho bảng của tổ chức này (hoặc đã bị xoá) — bỏ ra rồi chọn lại."})
            elif oid in thay:
                loi.append({"node": ma, "message": f"“{ten}”: dataset “{cua_org[oid]['ten']}” chọn hai lần."})
            if isinstance(oid, str):   # [ghi chú kỹ thuật] option_id là list / dict ⇒ không băm được ⇒ trước đây NÉM TypeError (trả 500); giờ chỉ báo sai khuôn
                thay.add(oid)
    return loi


def ds_cho_trang(org: str, ds=ds_dataset) -> list[dict]:
    """GET /api/kho-bang — để ô chọn trên trang (máy chủ vẫn kiểm lại lúc Lưu / Chạy)."""
    d = ds(org)
    return sorted(({"option_id": k, "option_display_name": v["ten"], "kho": v["kho"]} for k, v in d.items()),
                  key=lambda x: (x["kho"], x["option_display_name"]))
