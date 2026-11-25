"""Tải file kết quả của một lượt về máy qua trang 18400 — C 11/11/2026 (nhóm phát triển xác nhận, đề xuất E3 ở tài liệu thiết kế nội bộ).

Vì sao: link file kết quả Capitol tạo trỏ http://minio:9000/… (tên máy trong mạng nội bộ s3_noi_bo, MinIO không mở cổng ra máy) ⇒ người dùng bấm
không mở được. Đường này: trình duyệt hỏi máy chủ trang theo (mã lượt, mã file); MÁY CHỦ tự tìm bucket / khoá trong sự kiện của CHÍNH lượt đó
(không nhận bucket / khoá / URL từ người gọi), kiểm, rồi đọc byte qua hộp capitol-ab-api (đã có khoá MinIO trong env — máy chủ trang không giữ khoá).

Rào (vì sao):
- mã lượt + mã file = UUID (fullmatch) ⇒ không chèn được SQL;
- bucket phải là 1 trong 2 bucket của bộ chạy thử (kết quả / file); khoá chỉ gồm ký tự an toàn, không "..", không bắt đầu "/", và PHẢI chứa đoạn "/<org của lượt>/"
  (org lấy từ workflow của lượt trong DB) ⇒ không đọc được file của tổ chức khác dù sự kiện có ghi khoá lạ;
- docker exec bằng list đối số (không shell), bucket / khoá đi qua argv; trần 50 MB (đọc cỡ trước bằng head_object), hết 60 s;
- trả về luôn là FILE ĐÍNH KÈM: application/octet-stream + Content-Disposition attachment + nosniff + CSP sandbox ⇒ trình duyệt không hiển thị
  (kể cả .html / .svg) trên miền 127.0.0.1:18400 — script chạy trên miền này sẽ gọi được /api/run, /api/approve cùng nguồn.
"""
import re
import subprocess
import threading
from urllib.parse import quote

BUCKET_RA = "capitol-chay-thu-outputs"   # OUTPUTS_BUCKET_NAME của 3 hộp ab-* (compose.minio.yaml:42)
# [ghi chú kỹ thuật] + AWS_S3_BUCKET_NAME (compose.minio.yaml:41) — file người dùng tải lên / khối ghi vào kho chính; rà DB: 28 / 72 file nằm ở đây
BUCKET_CHO = frozenset({BUCKET_RA, "capitol-chay-thu-files"})
TRAN_BYTE = 50 * 1024 * 1024
_KHOA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/ ()+,=@%]{0,1000}$")
# [ghi chú kỹ thuật] NEO tiền tố + VỊ TRÍ của org (DB có 11 tổ chức): chỉ đòi "org nằm đâu đó" thì "khac-org/<org-của-lượt>/f" lọt. Khoá thật (nhóm phát triển xác nhận
# files_available + node_result.data.files): files/<org>/… (org ở đoạn 2); các thư mục dưới (org ở đoạn 3). Tiền tố lạ ⇒ từ chối.
TIEN_TO = (("files",), ("data_analysis", "outputs"), ("documents", "xlsx_chat"), ("documents", "pptx_chat"), ("documents", "docx_chat"),
           ("graphs", "network"), ("code_interpreter", "outputs"), ("d3_data_visualization", "outputs"))
# [ghi chú kỹ thuật] mỗi lần tải giữ cả file trong RAM (hộp + máy chủ trang) ⇒ tối đa 2 lượt tải cùng lúc, quá ⇒ 429
_CUNG_LUC = threading.BoundedSemaphore(2)
_DOC = r"""
import sys, boto3
b, k, tran = sys.argv[1], sys.argv[2], int(sys.argv[3])
c = boto3.client("s3")
if c.head_object(Bucket=b, Key=k)["ContentLength"] > tran:
    sys.exit(3)
d = c.get_object(Bucket=b, Key=k)["Body"].read(tran + 1)
if len(d) > tran:
    sys.exit(3)
sys.stdout.buffer.write(d)
"""


def _uuid(ma_re, x) -> str:
    if not isinstance(x, str) or not ma_re.fullmatch(x):
        raise ValueError("mã không hợp lệ")
    return x


def ds_tep(xem, phien: str) -> tuple[str | None, list[dict]]:
    """(org của lượt, danh sách file) — từ files_available + data.files của node_result trong lượt. Mỗi mục: id, ten, mime, node, bucket, khoa."""
    phien = _uuid(xem.MA, phien)
    r = xem.sql(f"select row_to_json(x) from (select r.id, w.org_id::text as org from workflow_run r join workflow w on w.id=r.workflow_id "
                f"where r.session_id='{phien}' order by r.created_at desc limit 1) x")
    if not r or not xem.MA.fullmatch(str(r.get("id") or "")):
        return None, []
    rid = r["id"]
    ds = xem.sql(f"""select coalesce(json_agg(x.f), '[]') from (
            select f || jsonb_build_object('_node', coalesce(f->>'node_id', '')) as f from workflow_event e, jsonb_array_elements(e.event_data->'files') f
              where e.run_id='{rid}' and e.event_type='files_available' and jsonb_typeof(e.event_data->'files')='array'
            union all
            select f || jsonb_build_object('_node', coalesce(e.node_instance_id::text, e.event_data->>'node_id', '')) from workflow_event e,
                   jsonb_array_elements(e.event_data->'data'->'files') f
              where e.run_id='{rid}' and e.event_type='node_result' and jsonb_typeof(e.event_data->'data'->'files')='array') x""") or []
    out, thay = [], set()
    for f in ds:
        if not isinstance(f, dict):
            continue
        ma, khoa, bucket = f.get("file_id") or f.get("id"), f.get("s3_key"), f.get("s3_bucket")
        if not (isinstance(ma, str) and xem.MA.fullmatch(ma) and isinstance(khoa, str) and khoa) or khoa in thay:
            continue
        thay.add(khoa)
        out.append({"id": ma, "ten": str(f.get("filename") or f.get("name") or khoa.rsplit("/", 1)[-1])[:200],
                    "mime": str(f.get("mime_type") or "")[:100], "node": str(f.get("_node") or "")[:64], "bucket": bucket, "khoa": khoa})
    org = r.get("org") if isinstance(r.get("org"), str) and xem.MA.fullmatch(r.get("org")) else None
    return org, out


def ds_cong_khai(xem, phien: str) -> list[dict]:
    """Danh sách cho trang: KHÔNG có bucket / khoá / URL — chỉ id, tên, kiểu, khối."""
    _, ds = ds_tep(xem, phien)
    return [{k: f[k] for k in ("id", "ten", "mime", "node")} for f in ds]


def kiem_khoa(bucket, khoa, org) -> str | None:
    """Lý do từ chối, hoặc None nếu được đọc."""
    if bucket not in BUCKET_CHO:
        return "bucket lạ"
    if not isinstance(khoa, str) or not _KHOA.fullmatch(khoa) or ".." in khoa or "//" in khoa:
        return "khoá lạ"
    phan = khoa.split("/")
    for tt in TIEN_TO:
        n = len(tt)
        if tuple(phan[:n]) == tt:
            if org and len(phan) > n + 1 and phan[n] == org:
                return None
            return "khoá không thuộc tổ chức của lượt"
    return "thư mục kho lạ"


def doc_tep(xem, phien: str, ma_tep: str) -> tuple[int, dict | None, bytes]:
    """(mã HTTP, thông tin {ten, mime}, byte). Lỗi ⇒ (mã, {"loi": …}, b"")."""
    ma_tep = _uuid(xem.MA, ma_tep)
    org, ds = ds_tep(xem, phien)
    f = next((x for x in ds if x["id"] == ma_tep), None)
    if f is None:
        return 404, {"loi": "lượt này không có file đó"}, b""
    ly_do = kiem_khoa(f["bucket"], f["khoa"], org)
    if ly_do:
        return 403, {"loi": f"không đọc: {ly_do}"}, b""
    if not _CUNG_LUC.acquire(blocking=False):
        return 429, {"loi": "đang có 2 lượt tải — thử lại sau ít giây"}, b""
    try:
        p = subprocess.run(["docker", "exec", "capitol-ab-api", "python", "-I", "-c", _DOC, f["bucket"], f["khoa"], str(TRAN_BYTE)],
                           capture_output=True, timeout=60)
    finally:
        _CUNG_LUC.release()
    if p.returncode == 3:
        return 413, {"loi": "file lớn hơn 50 MB — tải bằng cách khác"}, b""
    if p.returncode != 0:
        return 502, {"loi": "không đọc được file từ kho (MinIO)"}, b""
    return 200, {"ten": f["ten"], "mime": f["mime"]}, p.stdout


def dau_tai_ve(ten: str, n: int) -> list[tuple[str, str]]:
    """Header trả file: luôn đính kèm, không cho trình duyệt đoán kiểu / chạy script."""
    an = re.sub(r"[^A-Za-z0-9._-]+", "_", ten).strip("._") or "tep"
    return [("Content-Type", "application/octet-stream"),
            ("Content-Disposition", f"attachment; filename=\"{an[:150]}\"; filename*=UTF-8''{quote(ten[:200], safe='')}"),
            ("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", "sandbox; default-src 'none'"),
            ("Cache-Control", "no-store"), ("Content-Length", str(n))]
