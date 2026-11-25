"""[ghi chú] BẢN THAY cho platform-api 1.82.0 eval_manager/eval_manager_opensearch.py: lưu / đọc kết quả chấm vào KHO FILE JSON
thay OpenSearch. Vì sao: bộ chạy thử không có OpenSearch (JVM ≥ 1 GB RAM, máy thiếu RAM) ⇒ bản gốc ghi hỏng (ConnectionError opensearch-node:9200)
⇒ route /evaluations/test/status (routes/eval_manager.py:329-343) chỉ trả "completed" khi đọc được bản ghi ⇒ khối Evals của agentic-backend chờ đủ
300 s rồi FAILED (lượt b6d8c482, 17/11: eval-service chấm xong 5,4 s mà khối vẫn hết giờ).

Giữ nguyên MẶT NGOÀI: cùng tên lớp, cùng 7 hàm công khai, cùng kiểu trả, cùng lỗi khi không thấy (get_eval_result ⇒ IndexError như
response["hits"]["hits"][0] của bản gốc ⇒ route status bắt và trả "incomplete"). Người gọi (3 file, rg 17/11): routes/eval_manager.py,
routes/eval_v2.py, eval_manager/background_tasks.py.

Kho: <EVAL_KHO_DIR>/<orgid>/<id>.json (một file một bản ghi; id = mã yêu cầu chấm = run_id của route status).
- Tổ chức: mọi hàm đọc / xoá chỉ mở thư mục của ĐÚNG orgid ⇒ mã của tổ chức khác không đọc được điểm.
- orgid / id đi vào đường dẫn ⇒ chỉ nhận khuôn uuid (chữ thường hex + gạch); lạ ⇒ ValueError (không tạo / đọc file nào).
- Ghi: file tạm cùng thư mục + os.replace (nguyên tử). Đọc: bỏ qua symlink và file không phải file thường.
- So khớp CHÍNH XÁC (bản gốc dùng match / term của OpenSearch trên trường keyword ⇒ cũng khớp chính xác).
"""
import json
import os
import re
import tempfile
from pathlib import Path

from eval_manager.eval_runner_schemas import EvalsResultOpensearchLogModel
from utils import get_logger

logger = get_logger("eval_manager_opensearch")

_MA = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")   # dùng fullmatch (E minor 1: "$" của match nhận "uuid\n")


def _kho() -> Path:
    d = os.environ.get("EVAL_KHO_DIR", "")
    if not d or not os.path.isabs(d):
        raise RuntimeError("EVAL_KHO_DIR chưa đặt (đường tuyệt đối) — kho kết quả chấm của bộ chạy thử")
    return Path(d)


def _ma(x, ten: str) -> str:
    s = str(x or "")
    if not _MA.fullmatch(s):
        raise ValueError(f"{ten} không phải mã uuid: {s[:40]!r}")
    return s


def _thu_muc(org_id: str, tao: bool = False) -> Path:
    p = _kho() / _ma(org_id, "orgid")
    if tao:
        p.mkdir(mode=0o700, parents=False, exist_ok=True)
    return p


def _doc_thu_muc(org_id: str) -> list[dict]:
    p = _thu_muc(org_id)
    if not p.is_dir() or p.is_symlink():
        return []
    out = []
    for f in sorted(p.iterdir()):
        if f.suffix != ".json" or f.is_symlink() or not f.is_file():
            continue
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001 — một file hỏng không làm hỏng cả danh sách
            logger.warning(f"Kho evals: bỏ qua file hỏng {f.name}: {type(e).__name__}")
    return out


class EvalManagerOpenSearchClient:

    @classmethod
    def save_eval_result(cls, eval_result: EvalsResultOpensearchLogModel):
        d = _thu_muc(eval_result.orgid, tao=True)
        ma = _ma(eval_result.id, "id")
        body = eval_result.model_dump_json(exclude_none=True)
        fd, tam = tempfile.mkstemp(prefix=".tam_", suffix=".json", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tam, d / f"{ma}.json")
        except Exception:
            try:
                os.unlink(tam)
            except OSError:
                pass
            raise
        logger.info(f"Kho evals: đã lưu orgid={eval_result.orgid} test_id={eval_result.testid} id={ma}")

    @classmethod
    def get_eval_result(cls, org_id: str, test_id: str, run_id: str) -> EvalsResultOpensearchLogModel:
        hits = [x for x in _doc_thu_muc(org_id) if x.get("testid") == test_id and x.get("id") == run_id]
        return EvalsResultOpensearchLogModel(**hits[0])   # không thấy ⇒ IndexError như bản gốc

    @classmethod
    def get_eval_result_by_run_id(cls, org_id: str, run_id: str) -> EvalsResultOpensearchLogModel | None:
        hits = [x for x in _doc_thu_muc(org_id) if x.get("id") == run_id]
        return EvalsResultOpensearchLogModel(**hits[0]) if hits else None

    @classmethod
    def get_eval_results(cls, org_id: str, test_id: str):
        return [EvalsResultOpensearchLogModel(**x) for x in _doc_thu_muc(org_id) if x.get("testid") == test_id][:500]

    @classmethod
    def _xoa(cls, org_id: str, dk) -> int:
        p = _thu_muc(org_id)
        if not p.is_dir() or p.is_symlink():
            return 0
        n = 0
        for f in p.iterdir():
            if f.suffix != ".json" or f.is_symlink() or not f.is_file():
                continue
            try:
                x = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if dk(x):
                f.unlink(); n += 1
        return n

    @classmethod
    def delete_eval_results_for_node(cls, org_id: str, workflow_run_id: str, node_id: str) -> int:
        n = cls._xoa(org_id, lambda x: x.get("workflow_run_id") == workflow_run_id and x.get("node_id") == node_id)
        if n:
            logger.info(f"Cleared {n} prior eval result(s) for node={node_id} workflow_run={workflow_run_id}")
        return n

    @classmethod
    def delete_dimensional_eval_results(cls, orgid: str, workflow_run_id: str, node_id: str, eval_id: str) -> int:
        n = cls._xoa(orgid, lambda x: x.get("workflow_run_id") == workflow_run_id and x.get("node_id") == node_id
                     and x.get("testid") == eval_id)
        if n:
            logger.info(f"Cleared {n} prior dimensional eval result(s) for eval={eval_id} node={node_id} workflow_run={workflow_run_id}")
        return n

    @classmethod
    def get_eval_results_by_workflow_run(cls, org_id: str, workflow_run_id: str):
        return [EvalsResultOpensearchLogModel(**x) for x in _doc_thu_muc(org_id) if x.get("workflow_run_id") == workflow_run_id][:500]
