"""
Utility helpers for translating natural-language questions into Athena SQL via an LLM.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import boto3

from opentelemetry import context as otel_context

from config import settings
from dynamodb_client.collection_dataset_schemas import CollectionDatasetSchemasClient
from utils import get_logger
from utils.s3_utils import s3Client
from langfuse import observe, get_client
from utils.llm_client import ChatCompletionRequest, chat_completion

MAX_DETAILED_TABLES = 80
MAX_COLUMNS_PER_TABLE = 50
MAX_SAMPLE_VALUES = 3
# Ceiling for the schema context handed to the NL->SQL model (~100k tokens at
# ~4 chars/token). When rich profiles blow past it, the detail section is
# rebuilt without samples/profiles — column names are dropped last, since a
# name the model never saw is what produces COLUMN_NOT_FOUND queries.
MAX_CONTEXT_CHARS = 400_000

GLUE_FETCH_WORKERS = 8

# Temperature for the serial-retry SQL generation. The best-of-N candidates and
# the first serial attempt run at 0.0 for reproducibility; the error-feedback
# retry uses a small non-zero value so it can diverge from the failed statement.
SERIAL_RETRY_TEMPERATURE = 0.3

logger = get_logger("llm_athena")



# ==== [ghi chú] ====
# Kho bảng chạy TRÊN MÁY: thay AWS Athena + Glue bằng sqlite3 có sẵn (không cài gói). Mỗi truy vấn: xác định dataset đang hỏi (biến
# `dataset_id` của hàm gọi — cả 3 đường gọi run_athena_query :1104 / :1406 / routes/collections.py:3771 đều có) ⇒ nạp ĐÚNG các bảng của
# dataset đó từ Parquet trên MinIO vào SQLite :memory: ⇒ bộ cấp quyền (set_authorizer) chỉ cho SELECT / đọc ĐÚNG các bảng đó ⇒ trần
# giờ + trần dòng. Không thấy dataset ⇒ từ chối (đóng khi lỗi). SQL kiểu Athena/Presto phức tạp có thể lỗi trên SQLite (giới hạn đã chốt).
import inspect as _kb_inspect
import io as _kb_io
import sqlite3 as _kb_sqlite3
import uuid as _kb_uuid

_KB_TRAN_DONG_NAP = 200_000      # dòng tối đa nạp từ MỘT bảng
_KB_TRAN_BYTE = 50 * 1024 * 1024  # cỡ tối đa một file Parquet tải về
_KB_TRAN_DONG_RA = 1_000         # như trang đầu MaxResults của Athena gốc
_KB_TRAN_GIAY = 20.0             # gốc: vòng chờ `while True` không hết giờ
_KB_HAM_CAM = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "zipfile", "sqlar_compress", "sqlar_uncompress"}
_KB_TU_CHOI = "access denied (chay_thu): "   # chứa chữ khớp EXECUTION_FAILURE_SIGNATURES ⇒ không sinh SQL lại để lách


_KB_MO_HINH = "gpt-6-luna"   # [đã chốt 10/11] luật dự án: chỉ gpt-6-luna (PA mặc định gpt-4.1-mini — routes/collections.py:3898, 3904)
_KB_TRAN_UNG_VIEN = 1         # [đã chốt] lượt thử: 1 câu SQL ứng viên (gốc mặc định 5)
_KB_TRAN_VAO = 30_000         # [ghi chú] token VÀO tối đa / lần gọi (tiktoken o200k + lề 10 %) — vượt ⇒ TỪ CHỐI, không gọi
_KB_TRAN_RA = 4_000           # [ghi chú] max_completion_tokens / lần gọi (gồm cả token suy luận) ⇒ mức tối đa 1 lần = 30k × 0,1 + 4k × 0,5 / 1 triệu = 0,005 USD


# [ghi chú] Máy thử chạy SQL trên SQLite :memory: (không phải Athena) nhưng lời dặn gốc của Capitol
# (DEFAULT_SYSTEM_PROMPT quy tắc 3) BẮT viết TRY_CAST(... AS DOUBLE) + regexp_replace ⇒ SQLite báo near "AS". Lớp 1 (gốc): nối thêm ghi chú
# phương ngữ vào tin hệ thống của lần SINH SQL (nhận ra bằng chữ "Athena SQL" của lời dặn gốc; lời dặn chấm kết quả không có chữ này ⇒ không đổi).
_KB_GHI_CHU_SQLITE = (
    "\n\nIMPORTANT — execution engine override (test environment): the SQL runs on SQLite, NOT Athena/Trino. This overrides rule 3 and any "
    "Athena-specific syntax above. Use CAST(... AS REAL) (never TRY_CAST); types TEXT / REAL / INTEGER (not varchar / DOUBLE); "
    "regexp_replace(text, '[character class]', '') is available ONLY with a single character class such as '[^0-9.-]'; no other regexp_* / "
    "date_parse / try() / LISTAGG. Keep the database-qualified, double-quoted table names exactly as required above."
)


def _kb_them_ghi_chu_sqlite(req) -> None:
    for m in req.messages or []:
        if isinstance(m, dict) and m.get("role") == "system" and "Athena SQL" in str(m.get("content") or "") \
                and _KB_GHI_CHU_SQLITE not in str(m.get("content")):
            m["content"] = str(m["content"]) + _KB_GHI_CHU_SQLITE
            return


def _kb_chat(req: "ChatCompletionRequest") -> "ChatCompletionResult":
    """Bọc MỌI lần gọi AI của file này (sinh SQL :830, :1098, :1268): ép model = gpt-6-luna, bỏ temperature (llm_client chỉ coi gpt-5*
    là mô hình suy luận ⇒ vẫn gửi temperature cho luna ⇒ có thể bị từ chối); ghi 1 dòng log "[chay_thu_tien]" có số token để script
    chạy ca cộng vào sổ tiền ngày (lần gọi AI trong PA không nằm trong message_history của AB)."""
    req.model = _KB_MO_HINH
    req.temperature = None
    _kb_them_ghi_chu_sqlite(req)
    if isinstance(req.provider_options, dict):
        req.provider_options.pop("temperature", None)
    # [ghi chú] TRẦN: vào ước theo ký tự / 3 (tiếng Việt / JSON ⇒ cận trên thô); vượt ⇒ lỗi TRƯỚC khi gọi (0 đồng). Ra: gọi
    # thẳng OpenAI với max_completion_tokens (llm_client.chat_completion chỉ gửi max_tokens — mô hình suy luận có thể từ chối trường đó).
    # [E MAJOR 1] "ký tự / 3" KHÔNG phải cận trên (JSON tiếng Việt 2,14 ký tự / token) ⇒ đếm bằng tiktoken o200k_base (có trong image, chạy không
    # mạng) + lề 10 % + 4 token / tin nhắn; không nạp được ⇒ ký tự / 1,5 (cận trên thô hơn, chặt hơn).
    noi = [str(m.get("content") or "") for m in (req.messages or []) if isinstance(m, dict)]
    try:
        import tiktoken as _kb_tt
        _enc = _kb_tt.get_encoding("o200k_base")
        uoc = int((sum(len(_enc.encode(x, disallowed_special=())) for x in noi) + 4 * len(noi)) * 1.10) + 1
    except Exception:  # noqa: BLE001
        uoc = int(sum(len(x) for x in noi) / 1.5) + 1
    if uoc > _KB_TRAN_VAO:
        logger.warning(f"[chay_thu_tien] TỪ CHỐI trước khi gọi: ~{uoc} token vào > trần {_KB_TRAN_VAO} ds={_kb_dataset_dang_hoi()}")
        raise ValueError(f"chay_thu: lời gửi AI quá trần ({uoc} > {_KB_TRAN_VAO} token ước) — không gọi AI")
    from openai import OpenAI as _KbOpenAI
    from utils.llm_client import ChatCompletionResult as _KbKq
    from utils.openai_endpoint import openai_base_url as _kb_base_url
    payload = {"model": req.model, "messages": req.messages, "max_completion_tokens": _KB_TRAN_RA}
    if req.response_format is not None:
        payload["response_format"] = req.response_format
    resp = _KbOpenAI(api_key=req.api_key, base_url=_kb_base_url()).chat.completions.create(**payload)
    kq = _KbKq(text=(resp.choices[0].message.content or "") if getattr(resp, "choices", None) else "", raw_response=resp, provider="openai")
    u = getattr(getattr(kq, "raw_response", None), "usage", None)
    vao = getattr(u, "prompt_tokens", None)
    ra = getattr(u, "completion_tokens", None)
    dem = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", None) or 0
    logger.warning(f"[chay_thu_tien] model={req.model} in={vao} out={ra} cache={dem} ds={_kb_dataset_dang_hoi()}")
    return kq


def _kb_la_rao(exc: "AthenaQueryError") -> bool:
    """[ghi chú] lỗi do RÀO của chay_thu (không xác định dataset / chưa có bảng / SQL ngoài SELECT — _KB_TU_CHOI). Lỗi này
    tất định: chạy lại cũng bị chặn y vậy. Ném ValueError (route ⇒ HTTP 400) thay AthenaQueryError (⇒ 502) để agentic-backend KHÔNG thử lại
    (@async_retry thử lại mọi 5xx, AB services/platform/collections.py:497 ⇒ 4 lần × 2 lời gọi luna). Lỗi hạ tầng thật vẫn 502 ⇒ vẫn thử lại."""
    return getattr(exc, "error_type", None) == "CHAY_THU" and str(getattr(exc, "reason", "") or "").startswith(_KB_TU_CHOI)


def _kb_loi(sql: str, eid: str, ly_do: str) -> "AthenaQueryError":
    return AthenaQueryError(sql=sql, query_execution_id=eid, state="FAILED", reason=ly_do, error_type="CHAY_THU", error_message=ly_do)


def _kb_dataset_dang_hoi() -> Optional[str]:
    f = _kb_inspect.currentframe()
    try:
        f = f.f_back if f else None
        while f is not None:
            v = f.f_locals.get("dataset_id")
            if isinstance(v, str) and 1 <= len(v) <= 200:
                return v
            f = f.f_back
    finally:
        del f
    return None


_KB_TRY_CAST = re.compile(r"(?i)\bTRY_CAST(\s*\()")
_KB_LOP_KY_TU = re.compile(r"\[\^?(?:[^\[\]\\]|\\.){1,60}\][+*]?")   # đúng MỘT lớp ký tự, vd [^0-9.-] — không nhóm / lặp lồng ⇒ không ReDoS


def _kb_doi_try_cast(sql: str) -> str:
    """[ghi chú] TRY_CAST( ⇒ CAST( CHỈ ở phần SQL ngoài '…' (chuỗi) và "…" (tên trích) — chữ TRY_CAST trong dữ liệu / tên cột giữ nguyên.
    SQLite CAST không ném lỗi (chuỗi hỏng ⇒ 0 thay vì NULL như TRY_CAST) — chấp nhận cho máy thử, ghi giới hạn ở GHI_CHEP."""
    ra, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:   # '' hoặc "" thoát
                        j += 2; continue
                    break
                j += 1
            ra.append(sql[i:j + 1]); i = j + 1
            continue
        j = i
        while j < n and sql[j] not in ("'", '"'):
            j += 1
        ra.append(_KB_TRY_CAST.sub(r"CAST\1", sql[i:j])); i = j
    return "".join(ra)


def _kb_regexp_replace(chuoi, mau, thay=""):
    """[ghi chú] regexp_replace kiểu Athena cho SQLite, HẸP: mẫu chỉ được là MỘT lớp ký tự (≤ 60 ký tự, tuỳ + / *), chuỗi ≤ 2.000 ký tự,
    chuỗi thay không có nhóm. Khác ⇒ ném lỗi ⇒ SQL lỗi ⇒ Capitol sinh lại (như lỗi Athena thường). Không nhận mẫu tự do ⇒ không treo vì ReDoS."""
    if chuoi is None or mau is None:
        return None
    chuoi, mau, thay = str(chuoi), str(mau), "" if thay is None else str(thay)
    if len(chuoi) > 2000 or not _KB_LOP_KY_TU.fullmatch(mau) or "$" in thay or "\\" in thay or len(thay) > 20:
        raise ValueError("regexp_replace (chay_thu) chỉ nhận một lớp ký tự như '[^0-9.-]'")
    return re.sub(mau, thay.replace("\\", ""), chuoi)


def _kb_bang_cua(dataset_id: str) -> Dict[str, str]:
    """{tên bảng Glue ⇒ key Parquet} của ĐÚNG dataset (bảng ghi schema DynamoDB, do bước đồng bộ Parquet ghi)."""
    out: Dict[str, str] = {}
    for item in CollectionDatasetSchemasClient().list_dataset_schemas(dataset_id=dataset_id):
        md = item.get("metadata") or {}
        ten, key = md.get("glue_table_name"), md.get("parquet_object_key")
        if isinstance(ten, str) and ten and isinstance(key, str) and key:
            out[ten] = key
    return out


def _kb_doc_parquet(key: str, chi_schema: bool = False):
    import pyarrow.parquet as pq
    r = boto3.client("s3", region_name=settings.AWS_REGION).get_object(Bucket=settings.S3_UPLOADS_BUCKET, Key=key)
    if int(r.get("ContentLength") or 0) > _KB_TRAN_BYTE:
        raise ValueError(f"file Parquet quá {_KB_TRAN_BYTE // (1024 * 1024)} MB")
    b = r["Body"].read(_KB_TRAN_BYTE + 1)
    if len(b) > _KB_TRAN_BYTE:
        raise ValueError(f"file Parquet quá {_KB_TRAN_BYTE // (1024 * 1024)} MB")
    return pq.read_schema(_kb_io.BytesIO(b)) if chi_schema else pq.read_table(_kb_io.BytesIO(b))


def _kb_kieu(t) -> str:
    import pyarrow.types as pt
    return ("boolean" if pt.is_boolean(t) else "bigint" if pt.is_integer(t) else "double" if pt.is_floating(t) or pt.is_decimal(t)
            else "date" if pt.is_date(t) else "timestamp" if pt.is_timestamp(t) else "string")

class AthenaRoutingError(RuntimeError):
    """Raised when dataset-query routing is misconfigured (e.g. a federated catalog is
    set without a scratch location). A deployment error, not a query-execution failure."""


class AthenaQueryError(RuntimeError):
    """Custom exception raised when an Athena query fails."""

    def __init__(
        self,
        *,
        sql: str,
        query_execution_id: str,
        state: str,
        reason: Optional[str] = None,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        error_category: Optional[int] = None,
    ) -> None:
        message = f"Athena query failed with status {state}"
        detail = error_message or reason
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.sql = sql
        self.query_execution_id = query_execution_id
        self.state = state
        self.reason = reason
        self.error_type = error_type
        self.error_message = error_message
        self.error_category = error_category


# A denied connector invoke and a misspelt column arrive as the same FAILED status.
# ENG-3679 / Sentry 5559: a 403 on the PME connector Lambda surfaced as a 400 for a month.
EXECUTION_FAILURE_SIGNATURES = (
    "accessdenied",
    "access denied",
    "not authorized",
    "insufficient permissions",
    "unrecognizedclientexception",
    "invalidsignatureexception",
    "expiredtoken",
    "throttlingexception",
    "toomanyrequestsexception",
    "rate exceeded",
    "slowdown",
    "task timed out",
)

# Athena's AthenaError.ErrorCategory: 1 = SYSTEM, 2 = USER, 3 = OTHER.
_ATHENA_ERROR_CATEGORY_SYSTEM = 1


def is_execution_failure(exc: AthenaQueryError) -> bool:
    """True when the query failed for an infrastructure or permission reason rather than
    because the generated SQL was wrong.

    Errs towards False: an unrecognised failure keeps the existing SQL-generation
    treatment, so this only ever reclassifies a failure we can positively identify as
    infrastructure.
    """
    if exc.error_category == _ATHENA_ERROR_CATEGORY_SYSTEM:
        return True
    haystack = " ".join(p for p in (exc.error_message, exc.reason) if p).lower()
    return any(sig in haystack for sig in EXECUTION_FAILURE_SIGNATURES)


DEFAULT_SYSTEM_PROMPT = f"""
You are an analytics assistant that writes Amazon Athena SQL against the Glue database "{settings.GLUE_DATASET_DATABASE_NAME}".
Follow these rules:

1. Tables and columns are provided in the metadata. Use EXACT column names as listed — these are the only valid identifiers.
   Column metadata is formatted as: "column_name" (human-readable label). You MUST use "column_name" in SQL, NEVER the label.
   Example: if metadata says `Asian_and_NHPI (Asian and NHPI)` you MUST write `Asian_and_NHPI` in SQL, NOT `Asian and NHPI`.
2. ALWAYS qualify table names with the database name and wrap them in double quotes: {settings.GLUE_DATASET_DATABASE_NAME}."table_name"
   This is REQUIRED because table names contain hyphens and special characters.
   Example: FROM {settings.GLUE_DATASET_DATABASE_NAME}."fcc5ab70_abortion-postdobbs"
3. Check the glue_type field in the metadata to determine how to handle columns:
   - If glue_type is 'int', 'bigint', 'double', 'float', or any numeric type: Use the column directly, it's already typed correctly
   - If glue_type is 'string' AND the column has semantic tags 'integer' or 'numeric': Apply sanitization (note the CAST to varchar first):
     TRY_CAST(
       NULLIF(regexp_replace(CAST(column_name AS varchar), '[^0-9\\\\.-]', ''), '')
       AS DOUBLE
     )
     The outer coercion MUST be TRY_CAST, never CAST. regexp_replace keeps digits, dots and
     minus signs, so values like '18.5.9', '--02' or '2031-04-10' survive it and make CAST
     fail the whole query with INVALID_CAST_ARGUMENT; TRY_CAST yields NULL for those rows.
   - If glue_type is 'string' without numeric tags: Use as-is
4. CRITICAL: Do NOT invent, guess, or rephrase column names. If a column is not in the metadata, do not use it.
   When a table has only 1 column and it looks like merged data, query it as-is and note the limitation.
   The table profiles below are the ONLY authoritative source of column names; any column names mentioned
   in a dataset description are context, not a schema — never use a column that is not in the profiles.
5. Trino/Athena aggregate rule: when an aggregate uses DISTINCT together with a WITHIN GROUP (ORDER BY ...)
   clause (e.g. LISTAGG), the ORDER BY expression MUST be identical to the aggregated (DISTINCT)
   expression. Never order by a bare column when you aggregate an expression of that column.
   (Note: array_agg orders with array_agg(expr ORDER BY expr), not WITHIN GROUP.)
   WRONG: LISTAGG(DISTINCT CONCAT('"', l1_process, '"'), ',') WITHIN GROUP (ORDER BY l1_process)
   RIGHT: LISTAGG(DISTINCT CONCAT('"', l1_process, '"'), ',') WITHIN GROUP (ORDER BY CONCAT('"', l1_process, '"'))
   Alternatively drop DISTINCT, or SELECT DISTINCT in a subquery first, then aggregate.
6. Return only SQL terminated with a semicolon; do not include commentary or explanation.
"""


def _fetch_glue_columns(database: str, table_names: List[str]) -> Dict[str, List[Dict[str, str]]]:
    """[chay_thu] Không có Glue: đọc cột từ CHÍNH file Parquet của bảng (nguồn thật như Glue với Athena). Bảng không đọc được ⇒ bỏ
    (Capitol tự lùi về schema DynamoDB như bản gốc khi Glue lỗi)."""
    if not table_names:
        return {}
    ds = _kb_dataset_dang_hoi()
    bang = _kb_bang_cua(ds) if ds else {}
    out: Dict[str, List[Dict[str, str]]] = {}
    for ten in dict.fromkeys(table_names):
        key = bang.get(ten)
        if not key:
            continue
        try:
            sc = _kb_doc_parquet(key, chi_schema=True)
            out[ten] = [{"name": f.name, "type": _kb_kieu(f.type)} for f in sc]
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[chay_thu] không đọc được cột Parquet của {ten}: {exc!r}")
    return out


def collect_table_metadata(dataset_id: str) -> List[Dict[str, Any]]:
    schemas_client = CollectionDatasetSchemasClient()
    items = schemas_client.list_dataset_schemas(dataset_id=dataset_id)

    # Pre-collect table names so we can batch-fetch Glue schemas
    table_names = []
    for item in items:
        glue_name = (item.get("metadata") or {}).get("glue_table_name")
        if glue_name:
            table_names.append(glue_name)

    database = settings.GLUE_DATASET_DATABASE_NAME
    glue_schemas = _fetch_glue_columns(database, table_names) if table_names else {}

    # Encryption-aware client for fetching sidecars: decrypts transparently for
    # encrypted orgs (raw boto3 would return ciphertext), plaintext passthrough otherwise.
    s3_client = s3Client(bucket=settings.S3_UPLOADS_BUCKET)

    tables = []
    for item in items:
        metadata = item.get("metadata") or {}
        table_name = metadata.get("glue_table_name")
        if not table_name:
            continue

        schema_fields = metadata.get("schema_fields")
        schema_types = metadata.get("schema_types")
        column_profiles = metadata.get("column_profiles") or {}
        column_mapping = metadata.get("column_name_mapping") or {}
        original_headers: Sequence[str] = column_mapping.get("original") or []

        # Fetch quality data:
        # - If quality_summary exists (new format): use it directly from DynamoDB
        # - Else if metadata_object_key exists (old format with S3 sidecar): fetch from S3
        # - Else: use quality from DynamoDB
        quality = {}

        # Check for new format (quality_summary in DynamoDB)
        if "quality_summary" in metadata:
            quality = metadata.get("quality_summary") or {}
        else:
            # Old format: prefer S3 sidecar if available
            metadata_object_key = metadata.get("metadata_object_key")
            if metadata_object_key:
                try:
                    # Fetch full quality data from S3 sidecar (more up-to-date than old DynamoDB format)
                    response = s3_client.get_object(metadata_object_key)
                    s3_metadata = json.loads(response["Body"].read().decode("utf-8"))
                    quality = s3_metadata.get("quality") or {}
                except Exception as e:
                    logger.debug(f"Failed to fetch S3 metadata from {metadata_object_key}: {e}")
                    # Fall back to DynamoDB metadata
                    quality = metadata.get("quality") or {}
            else:
                # No S3 sidecar reference, use quality from DynamoDB
                quality = metadata.get("quality") or {}

        schema_data = item.get("schema", {})
        source_metadata = {
            "row_count": metadata.get("parquet_row_count") or schema_data.get("row_count"),
            "column_count": schema_data.get("column_count") or len(schema_data.get("columns", [])),
        }

        # Use Glue column names (source of truth for Athena) with fallback to DynamoDB schema
        glue_table_info = glue_schemas.get(table_name, [])
        if glue_table_info:
            glue_column_names = [c["name"] for c in glue_table_info]
            glue_column_types = {c["name"]: c["type"] for c in glue_table_info}
        else:
            glue_column_names = item.get("schema", {}).get("columns", [])
            glue_column_types = {}

        tables.append(
            {
                "table": table_name,
                "database": metadata.get("glue_database_name") or settings.GLUE_DATASET_DATABASE_NAME,
                "columns": glue_column_names,
                "glue_column_types": glue_column_types,
                "primary_key": item.get("schema", {}).get("primary_key") or [],
                "foreign_keys": item.get("schema", {}).get("foreign_keys") or [],
                "column_profiles": column_profiles,
                "quality": quality,
                "source_metadata": {
                    "row_count": source_metadata.get("row_count"),
                    "column_count": source_metadata.get("column_count"),
                    "original_headers": list(original_headers),
                },
                "schema_fields": schema_fields,
                "schema_types": schema_types,
                "column_name_mapping": column_mapping,
            }
        )
    return tables


# Backwards compatibility for legacy imports/tests
def _collect_table_metadata(dataset_id: str) -> List[Dict[str, Any]]:  # pragma: no cover - thin wrapper
    return collect_table_metadata(dataset_id)


def _clean_sql(response_text: str) -> str:
    sql = response_text.strip()
    if sql.startswith("```"):
        sql = sql.strip("`")
        parts = sql.split("\n", 1)
        if len(parts) == 2:
            sql = parts[1]
    return sql.strip().rstrip(";") + ";"


# Only LISTAGG uses the WITHIN GROUP (ORDER BY ...) form in Trino/Athena; array_agg
# orders via array_agg(expr ORDER BY expr) and never carries a WITHIN GROUP clause.
_AGG_OPEN_RE = re.compile(r"\blistagg\s*\(", re.IGNORECASE)
_WITHIN_GROUP_RE = re.compile(r"\bwithin\s+group\s*\(", re.IGNORECASE)
_CAST_OPEN_RE = re.compile(r"(?<![\w.])(try_cast|cast)\s*\(", re.IGNORECASE)
_NUMERIC_CAST_TARGETS = frozenset(
    {"double", "real", "float", "decimal", "numeric", "bigint", "integer", "int", "smallint", "tinyint"}
)


def _normalize_sql_expr(expr: str) -> str:
    """Normalize a SQL expression for equality comparison (strip whitespace, lowercase)."""
    return re.sub(r"\s+", "", expr).lower()


def _matching_paren(text: str, open_idx: int) -> int:
    """Find the index of the ``)`` matching the ``(`` at ``open_idx``.

    Args:
        text: The string to scan.
        open_idx: Index of an opening parenthesis in ``text``.

    Returns:
        The index of the matching closing parenthesis, or -1 if unbalanced.
    """
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _first_top_level_arg(args: str) -> str:
    """Return the substring before the first top-level (unparenthesized) comma.

    Args:
        args: A comma-separated argument list (without the enclosing parens).

    Returns:
        The first argument, with nested parenthesised commas ignored.
    """
    depth = 0
    for i, char in enumerate(args):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            return args[:i]
    return args


def detect_distinct_orderby_mismatch(sql: str) -> Optional[str]:
    """Detect the Trino "aggregate DISTINCT + non-matching ORDER BY" violation.

    Trino/Athena rejects an aggregate that uses ``DISTINCT`` together with a
    ``WITHIN GROUP (ORDER BY <expr>)`` clause whose ``<expr>`` is not identical
    to the aggregated (DISTINCT) expression, raising ``EXPRESSION_NOT_IN_DISTINCT``.

    Args:
        sql: The candidate SQL to inspect.

    Returns:
        A human-readable hint describing the fix if the pattern is present,
        otherwise None. The check is heuristic and read-only; it never modifies
        the SQL.
    """
    for match in _AGG_OPEN_RE.finditer(sql):
        open_idx = match.end() - 1
        close_idx = _matching_paren(sql, open_idx)
        if close_idx == -1:
            continue
        inner = sql[open_idx + 1 : close_idx]
        if not re.match(r"\s*distinct\s+", inner, re.IGNORECASE):
            continue
        distinct_arg = _first_top_level_arg(re.sub(r"^\s*distinct\s+", "", inner, count=1, flags=re.IGNORECASE))
        within = _WITHIN_GROUP_RE.search(sql, close_idx)
        # The WITHIN GROUP must belong to THIS aggregate: it has to immediately
        # follow the aggregate's closing paren (only whitespace in between).
        # Otherwise a later, unrelated aggregate's WITHIN GROUP would be
        # mis-associated with a DISTINCT aggregate that has no ORDER BY of its own.
        if not within or sql[close_idx + 1 : within.start()].strip():
            continue
        wg_close = _matching_paren(sql, within.end() - 1)
        if wg_close == -1:
            continue
        order_clause = sql[within.end() : wg_close]
        order_match = re.match(r"\s*order\s+by\s+(.+)", order_clause, re.IGNORECASE | re.DOTALL)
        if not order_match:
            continue
        order_expr = _first_top_level_arg(order_match.group(1))
        order_expr = re.sub(r"\s+(asc|desc)\s*$", "", order_expr, flags=re.IGNORECASE)
        if _normalize_sql_expr(distinct_arg) != _normalize_sql_expr(order_expr):
            return (
                "An aggregate uses DISTINCT with a WITHIN GROUP (ORDER BY ...) whose "
                "expression differs from the aggregated expression. In Trino/Athena the "
                "ORDER BY expression must be identical to the DISTINCT argument "
                f"(aggregated `{distinct_arg.strip()}` vs ordered-by `{order_expr.strip()}`)."
            )
    return None


def detect_unsafe_numeric_cast(sql: str) -> Optional[str]:
    """Detect a sanitized string->number coercion that uses CAST instead of TRY_CAST.

    The prompt's numeric-sanitization recipe strips characters outside ``[0-9.-]`` with
    ``regexp_replace``, which still leaves values Trino cannot parse ('18.5.9', '--02', a
    date). ``CAST`` then fails the entire query with ``INVALID_CAST_ARGUMENT``, whereas
    ``TRY_CAST`` yields NULL for those rows.

    Args:
        sql: The candidate SQL to inspect.

    Returns:
        A human-readable hint describing the fix if the pattern is present, otherwise
        None. The check is heuristic and read-only; it never modifies the SQL.
    """
    for match in _CAST_OPEN_RE.finditer(sql):
        if match.group(1).lower() == "try_cast":
            continue
        open_idx = match.end() - 1
        close_idx = _matching_paren(sql, open_idx)
        if close_idx == -1:
            continue
        inner = sql[open_idx + 1 : close_idx]
        if "regexp_replace" not in inner.lower():
            continue
        targets = re.findall(r"\bas\s+(\w+)", inner, re.IGNORECASE)
        if targets and targets[-1].lower() in _NUMERIC_CAST_TARGETS:
            return (
                f"A sanitized string-to-number coercion uses CAST(... AS {targets[-1].upper()}). regexp_replace "
                "can leave values Trino cannot parse (e.g. '18.5.9', '--02', '2031-04-10'), and CAST then fails "
                "the whole query with INVALID_CAST_ARGUMENT. Use TRY_CAST instead so unparseable values become NULL."
            )
    return None


def _build_sql_error_feedback(failures: List[QueryResult]) -> Optional[str]:
    """Build corrective feedback for the serial retry from failed candidates.

    Includes up to 3 distinct real error messages so the model can fix the
    specific problem, and appends targeted hints for known invalid-SQL patterns
    (e.g. DISTINCT + non-matching ORDER BY) instead of assuming every failure is
    a column-name issue.

    Args:
        failures: The QueryResults whose ``success`` is False.

    Returns:
        The feedback string, or None if there are no failures.
    """
    if not failures:
        return None
    seen_errors: set[str] = set()
    error_parts: List[str] = []
    hints: List[str] = []
    for qr in failures:
        err_key = (qr.error or "")[:100]
        if err_key not in seen_errors:
            seen_errors.add(err_key)
            error_parts.append(f"SQL: {qr.candidate.sql}\nError: {qr.error}")
        for detect in (detect_distinct_orderby_mismatch, detect_unsafe_numeric_cast):
            hint = detect(qr.candidate.sql)
            if hint and hint not in hints:
                hints.append(hint)
        if len(error_parts) >= 3:
            break
    feedback = (
        f"{len(failures)} previous SQL attempts all failed. Here are the distinct errors:\n\n"
        + "\n\n---\n\n".join(error_parts)
        + "\n\nAnalyze the EXACT error message(s) above and fix that specific problem. "
        "Use only column names present in the metadata, and respect Trino/Athena "
        "semantics (for example, an aggregate with DISTINCT must ORDER BY the same "
        "expression it aggregates, and a string column coerced to a number must use "
        "TRY_CAST, never CAST). "
        "Generate corrected SQL."
    )
    if hints:
        feedback += "\n\nDetected issues:\n- " + "\n- ".join(hints)
    return feedback


def _validate_sql_structure(sql: str, dataset_id: str) -> Dict[str, Any]:
    """
    Validate SQL structure before execution to catch common errors early.

    Returns:
        Dict with 'valid' (bool) and 'errors' (list of error messages)
    """
    errors = []
    sql_upper = sql.upper()

    # Basic SQL structure checks
    if not sql_upper.strip().startswith("SELECT"):
        errors.append("SQL must start with SELECT statement")

    if sql_upper.count("SELECT") != sql_upper.count("FROM"):
        errors.append("Mismatched SELECT/FROM count - possible nested query issue")

    # Check for dangerous operations (belt and suspenders)
    dangerous_keywords = ["DROP", "DELETE", "TRUNCATE", "ALTER", "CREATE", "INSERT", "UPDATE"]
    for keyword in dangerous_keywords:
        if keyword in sql_upper:
            errors.append(f"SQL contains prohibited keyword: {keyword}")

    # Check for required components
    if "FROM" not in sql_upper:
        errors.append("SQL missing FROM clause")

    # Check for semicolon termination
    if not sql.strip().endswith(";"):
        errors.append("SQL must end with semicolon")

    # Check for empty SQL
    if len(sql.strip()) < 10:
        errors.append("SQL is too short to be valid")

    # Warn about common mistakes
    if sql_upper.count("(") != sql_upper.count(")"):
        errors.append("Unbalanced parentheses in SQL")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


def _format_foreign_keys(foreign_keys: List[Dict[str, Any]]) -> Optional[str]:
    if not foreign_keys:
        return None
    formatted = []
    for fk in foreign_keys:
        columns = fk.get("columns") or []
        reference = fk.get("references") or {}
        ref_columns = reference.get("columns") or []
        ref_dataset = reference.get("dataset_id")
        ref_table = reference.get("table")
        formatted.append(
            {
                "columns": columns,
                "references": {
                    "dataset_id": ref_dataset,
                    "table": ref_table,
                    "columns": ref_columns,
                },
            }
        )
    return json.dumps(formatted, ensure_ascii=False)


def _format_numeric_summary(summary: Optional[Dict[str, Any]]) -> Optional[str]:
    if not summary:
        return None
    min_value = summary.get("min")
    max_value = summary.get("max")
    avg_value = summary.get("avg")
    count = summary.get("count")
    if min_value is None and max_value is None and avg_value is None:
        return None
    parts = []
    if min_value is not None:
        try:
            parts.append(f"min={float(min_value):.3f}")
        except (ValueError, TypeError):
            parts.append(f"min={min_value}")
    if max_value is not None:
        try:
            parts.append(f"max={float(max_value):.3f}")
        except (ValueError, TypeError):
            parts.append(f"max={max_value}")
    if avg_value is not None:
        try:
            parts.append(f"avg={float(avg_value):.3f}")
        except (ValueError, TypeError):
            parts.append(f"avg={avg_value}")
    if summary.get("stddev") is not None:
        try:
            parts.append(f"stddev={float(summary['stddev']):.3f}")
        except (ValueError, TypeError):
            parts.append(f"stddev={summary['stddev']}")
    if count is not None:
        parts.append(f"count={count}")
    return ", ".join(parts)


def _build_prompt_context(
    *,
    dataset_id: str,
    tables: List[Dict[str, Any]],
    dataset_details: Optional[Dict[str, Any]],
) -> str:
    context = _render_prompt_context(
        dataset_id=dataset_id,
        tables=tables,
        dataset_details=dataset_details,
        include_profiles=True,
    )
    if len(context) > MAX_CONTEXT_CHARS:
        logger.warning(
            f"Schema context for dataset {dataset_id} is {len(context)} chars "
            f"(> {MAX_CONTEXT_CHARS}); rebuilding without column profiles/samples"
        )
        context = _render_prompt_context(
            dataset_id=dataset_id,
            tables=tables,
            dataset_details=dataset_details,
            include_profiles=False,
        )
    if len(context) > MAX_CONTEXT_CHARS:
        # Even bare column names blew the budget (pathological table/column count).
        # Hard-truncate and say so — a marked-incomplete schema beats an unbounded
        # prompt. Best-effort: prefers a line boundary (falls back to a mid-line cut
        # when no newline exists in the window), and whatever fits the prefix is
        # kept in render order — an extreme case can lose tail overview lines too.
        logger.error(
            f"Lean schema context for dataset {dataset_id} still {len(context)} chars "
            f"(> {MAX_CONTEXT_CHARS}); hard-truncating column detail"
        )
        marker = "\n[schema context truncated to size budget — additional tables/columns omitted]"
        # Reserve room for the marker so MAX_CONTEXT_CHARS is a true ceiling.
        # max(0, ...) keeps the reservation sane for a budget smaller than the
        # marker; the final slice makes the ceiling unconditional even then
        # (a clipped marker beats a blown budget).
        limit = max(0, MAX_CONTEXT_CHARS - len(marker))
        cut = context.rfind("\n", 0, limit)
        context = (context[: cut if cut != -1 else limit] + marker)[:MAX_CONTEXT_CHARS]
    return context


def _render_prompt_context(
    *,
    dataset_id: str,
    tables: List[Dict[str, Any]],
    dataset_details: Optional[Dict[str, Any]],
    include_profiles: bool,
) -> str:
    details = dataset_details or {}
    dataset_info = details.get("dataset") or {}
    collection_info = details.get("collection") or {}
    dataset_name = dataset_info.get("name") or details.get("dataset_name") or dataset_id
    dataset_description = dataset_info.get("description") or details.get("dataset_description")
    dataset_tags = details.get("tags") or dataset_info.get("tags") or []
    playground_config = details.get("playground") or {}
    saved_prompts = playground_config.get("prompts") or []

    sorted_tables = sorted(
        tables,
        key=lambda table: (table.get("source_metadata", {}).get("row_count") or 0),
        reverse=True,
    )

    lines: List[str] = []
    lines.append(f"Dataset ID: {dataset_id}")
    lines.append(f"Dataset Name: {dataset_name}")
    if dataset_description:
        lines.append(f"Dataset Description: {dataset_description}")
    if dataset_tags:
        lines.append(f"Dataset Tags: {', '.join(sorted(dataset_tags))}")
    if collection_info:
        collection_name = collection_info.get("name")
        collection_description = collection_info.get("description")
        if collection_name:
            lines.append(f"Collection: {collection_name}")
        if collection_description:
            lines.append(f"Collection Description: {collection_description}")

    if saved_prompts:
        limited_prompts = saved_prompts[:5]
        lines.append("Saved Playground Prompts (for context):")
        for idx, prompt_text in enumerate(limited_prompts, start=1):
            lines.append(f"  {idx}. {prompt_text}")
        if len(saved_prompts) > len(limited_prompts):
            lines.append(f"  ... {len(saved_prompts) - len(limited_prompts)} additional prompts available.")

    lines.append("\nTable Overview (all tables):")
    for table in sorted_tables:
        source_metadata = table.get("source_metadata") or {}
        row_count = source_metadata.get("row_count")
        column_count = source_metadata.get("column_count") or len(table.get("columns", []))
        sheet_name = source_metadata.get("sheet_name")
        overview = f"- {table['table']} (rows≈{row_count if row_count is not None else '?'}; columns={column_count})"
        if sheet_name:
            overview += f"; sheet={sheet_name}"
        lines.append(overview)

    lines.append("\nDetailed Table Profiles (top tables):")
    for table in sorted_tables[:MAX_DETAILED_TABLES]:
        lines.append(f"Table: {table['table']}")
        pk = table.get("primary_key") or []
        if pk:
            lines.append(f"  Primary Key: {', '.join(pk)}")
        fk_formatted = _format_foreign_keys(table.get("foreign_keys") or [])
        if fk_formatted:
            lines.append(f"  Foreign Keys: {fk_formatted}")
        column_profiles = table.get("column_profiles") or {}
        glue_column_types = table.get("glue_column_types") or {}

        # Build mapping from Glue column names (snake_case) to DynamoDB column names (CamelCase)
        # so we can look up column_profiles which are keyed by DynamoDB names
        schema_fields_list = table.get("schema_fields") or []
        glue_to_dynamo: Dict[str, str] = {}
        if isinstance(schema_fields_list, list):
            glue_cols = table.get("columns", [])
            for glue_col, dynamo_col in zip(glue_cols, schema_fields_list):
                glue_to_dynamo[glue_col] = dynamo_col

        for column_name in table.get("columns", [])[:MAX_COLUMNS_PER_TABLE]:
            # Look up profile using DynamoDB column name (CamelCase)
            glue_type = glue_column_types.get(column_name) or "string"
            # Lean mode omits ALL profile-derived fields (label hint + inferred_type),
            # keeping only the column name and the Glue type Athena resolves against —
            # profiles aren't even looked up.
            details_line = f'  - "{column_name}"; glue_type={glue_type}'
            if include_profiles:
                dynamo_name = glue_to_dynamo.get(column_name, column_name)
                profile = column_profiles.get(dynamo_name) or column_profiles.get(column_name) or {}
                display_name = profile.get("display_name") or column_name
                inferred_type = profile.get("inferred_type") or "unknown"
                tags = profile.get("semantic_tags") or []
                non_null_ratio = profile.get("non_null_ratio")
                samples = profile.get("sample_values") or []
                numeric_summary = _format_numeric_summary(profile.get("numeric_summary"))
                if display_name != column_name:
                    details_line = f'  - "{column_name}" label="{display_name}"; glue_type={glue_type}'
                details_line += f"; inferred_type={inferred_type}"
                if tags:
                    details_line += f"; tags={', '.join(tags)}"
                if non_null_ratio is not None:
                    try:
                        ratio_float = float(non_null_ratio)
                        details_line += f"; non_null_ratio={ratio_float:.1%}"
                    except (ValueError, TypeError):
                        details_line += f"; non_null_ratio={non_null_ratio}"
                if samples:
                    sample_preview = ", ".join(samples[:MAX_SAMPLE_VALUES])
                    details_line += f"; samples={sample_preview}"
                if numeric_summary:
                    details_line += f"; {numeric_summary}"
            lines.append(details_line)

        if len(table.get("columns", [])) > MAX_COLUMNS_PER_TABLE:
            lines.append(
                f"  - [truncated {len(table['columns']) - MAX_COLUMNS_PER_TABLE} additional columns for brevity]"
            )

    if len(sorted_tables) > MAX_DETAILED_TABLES:
        lines.append(
            f"Additional tables not fully expanded: {len(sorted_tables) - MAX_DETAILED_TABLES}. Focus on overview list above."
        )

    return "\n".join(lines)


@observe()
def generate_athena_sql_from_question(
    *,
    dataset_id: str,
    question: str,
    model: str = "gpt-4.1-mini",
    system_prompt: Optional[str] = None,
    dataset_details: Optional[Dict[str, Any]] = None,
    error_feedback: Optional[str] = None,
    temperature: float = 0.0,
    api_key: Optional[str] = None,
) -> str:
    tables = _collect_table_metadata(dataset_id)
    if not tables:
        raise ValueError("No Glue tables found for this dataset. Run the schema/parquet sync commands first.")

    schema_description = _build_prompt_context(
        dataset_id=dataset_id,
        tables=tables,
        dataset_details=dataset_details,
    )
    prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    user_message_parts = [f"Dataset context:\n{schema_description}"]
    if error_feedback:
        user_message_parts.append(
            f"Previous attempt failed with the following error. Correct for it:\n{error_feedback}"
        )
    user_message_parts.append(f"Question:\n{question}")
    user_message = "\n\n".join(user_message_parts)

    completion = _kb_chat(
        ChatCompletionRequest(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_message},
            ],
            # Defaults to 0.0 (deterministic); the error-feedback retry raises this
            # so it can diverge from a statement that already failed.
            temperature=temperature,
            api_key=api_key,
        )
    )
    text = completion.text
    sql = _clean_sql(text)
    logger.info(f"Generated SQL via {_KB_MO_HINH} (chay_thu ép; tham số gọi: {model}): {sql}")
    return sql


def resolve_athena_routing(orgid: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Decide the Athena catalog + output location for a dataset query.

    Encryption off, or the federated catalog not configured, → native catalog +
    default results bucket (returns ``(None, None)``). v1 keys off the cluster
    ``ENABLE_APP_ENCRYPTION`` flag; ``orgid`` is accepted (unused today) so a per-org
    encryption check can replace the flag later without changing any caller.

    Once the federated catalog IS configured (encryption on + catalog set), a distinct
    scratch location is **mandatory**: without it Athena would write results to the
    durable results bucket, which the result-securing step then deletes. A missing
    scratch location therefore raises ``AthenaRoutingError`` so the misconfiguration
    surfaces loudly instead of corrupting results.

    NOTE (known limitation): the dataset writer produces PME parquet whenever encryption
    is on, but this returns the native path when encryption is on yet the catalog is
    unset — so an "encryption on, federation not yet deployed" window would route PME
    reads to the native catalog (which cannot read them). Deploy the federated catalog
    before enabling encryption; tightening this to fail loud is a separate decision.
    """
    if not (settings.ENABLE_APP_ENCRYPTION and settings.ATHENA_FEDERATED_CATALOG_NAME):
        return None, None
    scratch = settings.ATHENA_SCRATCH_OUTPUT_LOCATION
    if not scratch:
        raise AthenaRoutingError(
            f"ATHENA_FEDERATED_CATALOG_NAME={settings.ATHENA_FEDERATED_CATALOG_NAME!r} is set but "
            "ATHENA_SCRATCH_OUTPUT_LOCATION is empty. Encrypted dataset queries require a distinct "
            "scratch location; refusing to run without one."
        )
    return settings.ATHENA_FEDERATED_CATALOG_NAME, scratch


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://bucket/key`` into ``(bucket, key)``. Raises ``ValueError`` for a
    value that is not an ``s3://`` URI (empty/unset/malformed) or that has no bucket.

    The ``s3://`` scheme is enforced strictly — a non-S3 value (e.g. ``http://…``)
    must NOT be accepted, since this helper feeds scratch deletion and result
    re-encryption where a wrong bucket/key would act on unintended objects."""
    if not uri or not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// URI: {uri!r}")
    bucket, _, key = uri[len("s3://") :].partition("/")
    if not bucket:
        raise ValueError(f"s3:// URI has no bucket: {uri!r}")
    return bucket, key


def _delete_scratch_outputs(scratch_outputs: List[str], protect: Optional[tuple[str, str]] = None) -> None:
    """Best-effort delete of every scratch Athena output (the raw decrypted CSV +
    Athena's ``.metadata`` sidecar). ``protect`` is the exact ``(bucket, key)`` of the
    re-encrypted result, which is never deleted — so scratch is cleaned even when it
    shares the results bucket (only that one object is spared, not the whole bucket).
    The scratch bucket's aggressive TTL is the backstop for anything this misses."""
    s3 = boto3.client("s3", region_name=settings.AWS_REGION)
    for loc in scratch_outputs:
        if not loc:
            continue
        try:
            bucket, key = _parse_s3_uri(loc)
            if protect and (bucket, key) == protect:
                continue  # the re-encrypted result object itself — keep it
            s3.delete_object(Bucket=bucket, Key=key)
            s3.delete_object(Bucket=bucket, Key=key + ".metadata")
        except Exception as exc:  # noqa: BLE001 - cleanup is best-effort; TTL backstops
            logger.warning(f"Failed to delete scratch output {loc}: {exc}")


def secure_encrypted_result(
    *,
    winner_output_location: Optional[str],
    scratch_outputs: List[str],
    filename: Optional[str] = None,
) -> Optional[str]:
    """Secure an encrypted-org query's result.

    Re-encrypts the winning candidate's raw scratch output under the Azure key
    (streaming AEAD via ``s3Client.put_object_stream`` → the results bucket, reusing the
    SAME object key so the key-bound AEAD stays decryptable) and returns a decrypt-proxy
    URL to put in ``output_location``. Every scratch output (winner + losers) is then
    deleted — except the one re-encrypted result object, so cleanup is correct even if
    scratch and results share a bucket. **Fail-closed:** on any re-encryption failure the
    scratch is still deleted and ``None`` is returned — the caller must never surface a
    raw scratch path. (All S3-URI parsing happens inside the protected block so a bad
    ``ATHENA_OUTPUT_LOCATION`` still fails closed.)
    """
    proxy_url: Optional[str] = None
    protect: Optional[tuple[str, str]] = None  # (bucket, key) of the re-encrypted result
    try:
        if winner_output_location:
            scratch_bucket, key = _parse_s3_uri(winner_output_location)
            results_bucket, _ = _parse_s3_uri(settings.ATHENA_OUTPUT_LOCATION)
            # Stream the plaintext scratch object straight into the encrypting write —
            # no local temp file (no plaintext on the server's disk; bounded memory).
            source_body = boto3.client("s3", region_name=settings.AWS_REGION).get_object(
                Bucket=scratch_bucket, Key=key
            )["Body"]
            try:
                results_client = s3Client(bucket=results_bucket)  # encryption-on → AEAD streaming
                results_client.put_object_stream(
                    key,
                    source_body,
                    content_type="text/csv",
                    metadata={"x-capai-source": "athena-pme-result"},
                )
            finally:
                source_body.close()
            protect = (results_bucket, key)  # written successfully — never delete it as scratch
            proxy_url = results_client.create_presigned_url(key, download=True, filename=filename)
    except Exception as exc:  # noqa: BLE001 - fail-closed: never return a raw scratch path
        logger.error(f"Failed to secure encrypted query result (fail-closed): {exc}")
        proxy_url = None
    finally:
        _delete_scratch_outputs(scratch_outputs, protect=protect)
    return proxy_url


def run_athena_query(
    *,
    sql: str,
    database: Optional[str] = None,
    output_location: Optional[str] = None,
    catalog: Optional[str] = None,
    poll_interval: float = 1.0,
    dataset_id: Optional[str] = None,
) -> Dict[str, Any]:
    """[chay_thu] Chạy SQL trên SQLite :memory: chỉ chứa bảng của dataset đang hỏi. Hình trả như bản gốc:
    {execution_id, columns, rows: [{col: str|None}], output_location}. Lỗi ⇒ AthenaQueryError (như gốc)."""
    eid = f"chay-thu-{_kb_uuid.uuid4()}"
    db = database or settings.GLUE_DATASET_DATABASE_NAME
    # [ghi chú] Ứng viên SQL chạy trong ThreadPoolExecutor (execute_sql_candidates) ⇒ dò khung gọi
    # ở luồng phụ KHÔNG thấy người gọi ⇒ luôn None ⇒ mọi truy vấn bị từ chối. Nay người gọi trong file này TRUYỀN dataset_id; dò khung chỉ
    # còn là đường lùi cho người gọi cùng luồng không truyền (routes/collections.py:3771 — hàm route có biến dataset_id). Thiếu / sai dạng ⇒ đóng.
    ds = dataset_id if dataset_id is not None else _kb_dataset_dang_hoi()
    if not (isinstance(ds, str) and 1 <= len(ds) <= 200):
        raise _kb_loi(sql, eid, _KB_TU_CHOI + "không xác định được dataset đang hỏi — không chạy")
    bang = _kb_bang_cua(ds)
    if not bang:
        raise _kb_loi(sql, eid, _KB_TU_CHOI + f"dataset {ds} chưa có bảng nào (chưa đồng bộ Parquet)")
    con = _kb_sqlite3.connect(":memory:")
    try:
        con.execute("PRAGMA hard_heap_limit=268435456")   # [D minor] trần bộ nhớ 256 MB (nối chéo lớn / ORDER BY) — TRƯỚC authorizer
        con.execute("PRAGMA temp_store=MEMORY")
        for ten, key in bang.items():   # nạp TRƯỚC khi bật bộ cấp quyền
            t = _kb_doc_parquet(key)
            t = t.slice(0, _KB_TRAN_DONG_NAP)
            cot = [str(c) for c in t.column_names]
            q = lambda x: '"' + x.replace('"', '""') + '"'
            con.execute(f"CREATE TABLE {q(ten)} ({', '.join(q(c) for c in cot)})")
            hang = [tuple((v.isoformat() if hasattr(v, "isoformat") else float(v) if type(v).__name__ == "Decimal" else v) for v in r)
                    for r in zip(*[t.column(c).to_pylist() for c in cot])] if cot else []
            if hang:
                con.executemany(f"INSERT INTO {q(ten)} VALUES ({', '.join('?' * len(cot))})", hang)
        cho = {x.lower() for x in bang}
        con.create_function("regexp_replace", 2, _kb_regexp_replace, deterministic=True)   # [ghi chú] TRƯỚC bộ cấp quyền
        con.create_function("regexp_replace", 3, _kb_regexp_replace, deterministic=True)

        def cap_quyen(hanh_dong, a1, a2, ten_db, nguon):
            if hanh_dong == _kb_sqlite3.SQLITE_SELECT:
                return _kb_sqlite3.SQLITE_OK
            if hanh_dong == _kb_sqlite3.SQLITE_READ:
                return _kb_sqlite3.SQLITE_OK if (a1 or "").lower() in cho and ten_db in (None, "main") else _kb_sqlite3.SQLITE_DENY
            if hanh_dong == _kb_sqlite3.SQLITE_FUNCTION:
                return _kb_sqlite3.SQLITE_DENY if (a2 or "").lower() in _KB_HAM_CAM else _kb_sqlite3.SQLITE_OK
            if hanh_dong == getattr(_kb_sqlite3, "SQLITE_RECURSIVE", -1):
                return _kb_sqlite3.SQLITE_OK
            return _kb_sqlite3.SQLITE_DENY   # ATTACH / PRAGMA / ghi / tạo / xoá / bảng hệ thống …
        con.set_authorizer(cap_quyen)
        t0 = time.monotonic()
        con.set_progress_handler(lambda: 1 if time.monotonic() - t0 > _KB_TRAN_GIAY else 0, 10_000)
        sql_chay = sql.strip().rstrip(";").strip()
        if db:   # Athena viết db."bảng" / "db"."bảng" ⇒ SQLite chỉ có main
            sql_chay = re.sub(r'(?i)"?' + re.escape(db) + r'"?\s*\.\s*', "", sql_chay)
        sql_chay = _kb_doi_try_cast(sql_chay)   # [ghi chú] đỡ khi AI vẫn viết TRY_CAST dù lời dặn đã nói SQLite
        try:
            cur = con.execute(sql_chay)
            cot_ra = [d[0] for d in (cur.description or [])]
            dong = cur.fetchmany(_KB_TRAN_DONG_RA)
        except _kb_sqlite3.DatabaseError as e:
            m = str(e)
            if "not authorized" in m or "prohibited" in m:
                raise _kb_loi(sql, eid, _KB_TU_CHOI + "SQL đụng bảng / lệnh / hàm không được phép (chỉ SELECT trên bảng của dataset này)")
            if "interrupted" in m:
                raise _kb_loi(sql, eid, f"SQL chạy quá {_KB_TRAN_GIAY:.0f} giây — dừng")
            raise _kb_loi(sql, eid, f"SQLite: {m}")   # sai SQL ⇒ để Capitol sinh lại như lỗi Athena thường
        logger.info(f"[chay_thu] Kho bảng SQLite dataset={ds} bảng={sorted(bang)} dòng_ra={len(dong)}")
        return {"execution_id": eid, "columns": cot_ra,
                "rows": [{c: (None if v is None else str(v)) for c, v in zip(cot_ra, r)} for r in dong],
                "output_location": output_location or "chay_thu://sqlite"}
    except AthenaQueryError:
        raise
    except Exception as e:  # noqa: BLE001 — nạp / đọc lỗi ⇒ lỗi rõ, không trả rỗng
        raise _kb_loi(sql, eid, f"Kho bảng (chay_thu) lỗi nạp dữ liệu: {type(e).__name__}: {e}")
    finally:
        con.close()


PROMPT_VARIANTS = [
    "",  # default — no suffix
    "\nWrite a concise query using only the minimum columns needed to answer the question.",
    "\nWrite a comprehensive query that handles NULL values, edge cases, and returns all relevant columns.",
]

TEMPERATURES = [0.0, 0.3, 0.5, 0.7, 1.0]

EVALUATION_SYSTEM_PROMPT = """You are evaluating SQL query results. Given the user's question and multiple successful query results, pick the one that best answers the question.

Consider:
- Does the result actually answer the question asked?
- Does it return a reasonable number of rows?
- Are the column names relevant to the question?
- Does it look like the data is correct (not all nulls, not obviously wrong)?

Reply with ONLY the candidate number (1, 2, 3, etc.) of the best result. No explanation."""


@dataclass
class SQLCandidate:
    sql: str
    temperature: float
    prompt_variant: str
    validation: Dict[str, Any] = field(default_factory=dict)
    generation_time_ms: float = 0.0


@dataclass
class QueryResult:
    candidate: SQLCandidate
    success: bool
    rows: List[Dict[str, Any]] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    execution_id: str = ""
    output_location: Optional[str] = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    athena_error: Optional[AthenaQueryError] = None


def _generate_single_candidate(
    *,
    schema_description: str,
    question: str,
    model: str,
    system_prompt: str,
    temperature: float,
    prompt_suffix: str,
    dataset_id: str,
    api_key: Optional[str] = None,
) -> SQLCandidate:
    start = time.monotonic()
    effective_prompt = system_prompt + prompt_suffix if prompt_suffix else system_prompt
    user_message = f"Dataset context:\n{schema_description}\n\nQuestion:\n{question}"

    from utils.llm_client import _supports_custom_temperature

    effective_temp = temperature if _supports_custom_temperature(model) else None

    completion = _kb_chat(
        ChatCompletionRequest(
            model=model,
            messages=[
                {"role": "system", "content": effective_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=effective_temp,
            api_key=api_key,
        )
    )
    sql = _clean_sql(completion.text)
    elapsed_ms = (time.monotonic() - start) * 1000
    validation = _validate_sql_structure(sql, dataset_id)
    return SQLCandidate(
        sql=sql,
        temperature=temperature,
        prompt_variant=prompt_suffix[:60] if prompt_suffix else "default",
        validation=validation,
        generation_time_ms=elapsed_ms,
    )


def _execute_single_candidate(
    candidate: SQLCandidate,
    *,
    dataset_id: str,   # [ghi chú] bắt buộc — luồng phụ không dò khung được
    catalog: Optional[str] = None,
    output_location: Optional[str] = None,
) -> QueryResult:
    start = time.monotonic()
    try:
        result = run_athena_query(sql=candidate.sql, catalog=catalog, output_location=output_location, dataset_id=dataset_id)
        elapsed_ms = (time.monotonic() - start) * 1000
        return QueryResult(
            candidate=candidate,
            success=True,
            rows=result["rows"],
            columns=result["columns"],
            execution_id=result["execution_id"],
            output_location=result.get("output_location"),
            execution_time_ms=elapsed_ms,
        )
    except AthenaQueryError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        return QueryResult(
            candidate=candidate,
            success=False,
            execution_id=exc.query_execution_id,
            error=str(exc),
            execution_time_ms=elapsed_ms,
            athena_error=exc,
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        return QueryResult(
            candidate=candidate,
            success=False,
            error=str(exc),
            execution_time_ms=elapsed_ms,
        )


@observe(name="generate_sql_candidates")
async def generate_sql_candidates(
    *,
    schema_description: str,
    question: str,
    model: str,
    system_prompt: str,
    dataset_id: str,
    n_candidates: int = 5,
    api_key: Optional[str] = None,
) -> List[SQLCandidate]:
    configs = []
    for i in range(n_candidates):
        temp = TEMPERATURES[i % len(TEMPERATURES)]
        variant = PROMPT_VARIANTS[i % len(PROMPT_VARIANTS)]
        configs.append((temp, variant))

    loop = asyncio.get_running_loop()
    ctx = otel_context.get_current()

    def _run_with_context(temp, variant):
        token = otel_context.attach(ctx)
        try:
            return _generate_single_candidate(
                schema_description=schema_description,
                question=question,
                model=model,
                system_prompt=system_prompt,
                temperature=temp,
                prompt_suffix=variant,
                dataset_id=dataset_id,
                api_key=api_key,
            )
        finally:
            otel_context.detach(token)

    with ThreadPoolExecutor(max_workers=n_candidates) as executor:
        futures = [loop.run_in_executor(executor, _run_with_context, temp, variant) for temp, variant in configs]
        results = await asyncio.gather(*futures, return_exceptions=True)

    candidates = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"SQL candidate generation failed: {r}")
        else:
            candidates.append(r)
    return candidates


@observe(name="execute_sql_candidates")
async def execute_sql_candidates(
    candidates: List[SQLCandidate],
    *,
    dataset_id: str,   # [ghi chú] bắt buộc
    catalog: Optional[str] = None,
    output_location: Optional[str] = None,
) -> List[QueryResult]:
    valid_candidates = [c for c in candidates if c.validation.get("valid", False)]
    if not valid_candidates:
        return []

    loop = asyncio.get_running_loop()
    ctx = otel_context.get_current()

    def _run_with_context(candidate):
        token = otel_context.attach(ctx)
        try:
            return _execute_single_candidate(candidate, dataset_id=dataset_id, catalog=catalog, output_location=output_location)
        finally:
            otel_context.detach(token)

    with ThreadPoolExecutor(max_workers=len(valid_candidates)) as executor:
        futures = [loop.run_in_executor(executor, _run_with_context, c) for c in valid_candidates]
        results = await asyncio.gather(*futures, return_exceptions=True)

    query_results = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"Athena execution failed unexpectedly: {r}")
        else:
            query_results.append(r)
    return query_results


@observe(name="evaluate_and_select_best")
def evaluate_and_select_best(
    *,
    question: str,
    successes: List[QueryResult],
    model: str = "gpt-4.1-mini",
    api_key: Optional[str] = None,
) -> QueryResult:
    if len(successes) == 1:
        return successes[0]

    candidate_summaries = []
    for i, qr in enumerate(successes, 1):
        preview_rows = qr.rows[:5]
        summary = (
            f"Candidate {i}:\n"
            f"  SQL: {qr.candidate.sql}\n"
            f"  Columns: {qr.columns}\n"
            f"  Row count: {len(qr.rows)}\n"
            f"  Preview: {json.dumps(preview_rows, default=str)[:500]}"
        )
        candidate_summaries.append(summary)

    user_message = f"Question: {question}\n\n" + "\n\n".join(candidate_summaries)

    completion = _kb_chat(
        ChatCompletionRequest(
            model=model,
            messages=[
                {"role": "system", "content": EVALUATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
            api_key=api_key,
        )
    )

    choice_text = completion.text.strip()
    try:
        choice_idx = int("".join(c for c in choice_text if c.isdigit())) - 1
        if 0 <= choice_idx < len(successes):
            logger.info(f"Evaluation selected candidate {choice_idx + 1} of {len(successes)}")
            return successes[choice_idx]
    except (ValueError, IndexError):
        pass

    logger.warning(f"Evaluation response '{choice_text}' unparseable, selecting candidate with most rows")
    return max(successes, key=lambda qr: len(qr.rows))


@observe(name="best_of_n_query")
async def best_of_n_query(
    *,
    dataset_id: str,
    question: str,
    model: str = "gpt-4.1-mini",
    system_prompt: Optional[str] = None,
    dataset_details: Optional[Dict[str, Any]] = None,
    n_candidates: int = 5,
    evaluation_model: Optional[str] = None,
    catalog: Optional[str] = None,
    output_location: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    tables = collect_table_metadata(dataset_id)
    if not tables:
        raise ValueError("No Glue tables found for this dataset. Run the schema/parquet sync commands first.")

    schema_description = _build_prompt_context(
        dataset_id=dataset_id,
        tables=tables,
        dataset_details=dataset_details,
    )
    effective_system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    n_candidates = min(int(n_candidates or 1), _KB_TRAN_UNG_VIEN)   # [ghi chú] lượt thử: 1 ứng viên
    candidates = await generate_sql_candidates(
        schema_description=schema_description,
        question=question,
        model=model,
        system_prompt=effective_system_prompt,
        dataset_id=dataset_id,
        n_candidates=n_candidates,
        api_key=api_key,
    )

    logger.info(
        f"Generated {len(candidates)} SQL candidates "
        f"({sum(1 for c in candidates if c.validation.get('valid', False))} valid)"
    )

    query_results = await execute_sql_candidates(candidates, dataset_id=dataset_id, catalog=catalog, output_location=output_location)
    successes = [qr for qr in query_results if qr.success]

    logger.info(
        f"Executed {len(query_results)} candidates: "
        f"{len(successes)} succeeded, {len(query_results) - len(successes)} failed"
    )
    # Per-candidate failures are logged at DEBUG with a truncated SQL/error
    # payload to avoid spamming prod logs (and potentially leaking user-
    # derived literals) on every query. If the whole batch fails, escalate
    # to WARNING since that indicates a real problem worth paging on.
    _MAX_SQL_LOG_CHARS = 500
    _MAX_ERR_LOG_CHARS = 500
    for qr in query_results:
        if not qr.success:
            logger.debug(
                f"Candidate SQL failed: error={repr(qr.error)[:_MAX_ERR_LOG_CHARS]} "
                f"| sql={qr.candidate.sql[:_MAX_SQL_LOG_CHARS]}"
            )
    if query_results and not successes:
        logger.warning(f"All {len(query_results)} SQL candidates failed — enable DEBUG logging for details")

    if successes:
        eval_model = evaluation_model or model
        scratch_outputs = [qr.output_location for qr in query_results if qr.output_location] if catalog else []
        try:
            best = evaluate_and_select_best(
                question=question,
                successes=successes,
                model=eval_model,
                api_key=api_key,
            )
        except Exception:
            # Evaluator raised after candidates already wrote (decrypted) scratch output;
            # clean it up so plaintext results don't linger to TTL, then propagate.
            if scratch_outputs:
                _delete_scratch_outputs(scratch_outputs)
            raise
        result_location = best.output_location
        if catalog:
            # Encrypted: re-encrypt the winner under Azure → proxy URL; delete ALL
            # candidates' scratch outputs (fail-closed — never expose a raw path).
            result_location = secure_encrypted_result(
                winner_output_location=best.output_location,
                scratch_outputs=scratch_outputs,
            )
        return {
            "rows": best.rows,
            "columns": best.columns,
            "execution_id": best.execution_id,
            "output_location": result_location,
            "sql": best.candidate.sql,
            "candidates_generated": len(candidates),
            "candidates_succeeded": len(successes),
            "strategy": "best_of_n",
        }

    failures = [qr for qr in query_results if not qr.success]
    blocked: List[AthenaQueryError] = []
    sql_failures: List[QueryResult] = []
    for qr in failures:
        if qr.athena_error is not None and is_execution_failure(qr.athena_error):
            blocked.append(qr.athena_error)
        else:
            sql_failures.append(qr)
    if blocked and not sql_failures:
        # A serial retry would spend another LLM call and still hit the same wall.
        logger.error(
            f"All {len(failures)} candidates failed on execution/permission errors, not SQL: dataset={dataset_id} "
            f"state={blocked[0].state} reason={blocked[0].reason!r} message={blocked[0].error_message!r}"
        )
        if _kb_la_rao(blocked[0]):   # [ghi chú] rào chay_thu ⇒ 400, không để AB thử lại (vẫn KHÔNG sinh SQL lại — nhánh này đứng trước serial retry)
            raise ValueError(str(blocked[0].reason)[:300]) from blocked[0]
        raise blocked[0]
    if blocked:
        logger.error(
            f"{len(blocked)}/{len(failures)} candidate failures for dataset {dataset_id} are execution/permission "
            f"errors, not SQL — retrying, but this is an infrastructure fault: "
            f"{(blocked[0].error_message or blocked[0].reason)!r}"
        )

    # Fallback: serial retry with error feedback from the SQL failures only — a 403 is
    # not something the model can fix, and it would crowd out a real error in the
    # builder's 3-error budget.
    logger.warning(f"All {len(candidates)} candidates failed, falling back to serial retry")
    error_feedback = _build_sql_error_feedback(sql_failures)

    sql = generate_athena_sql_from_question(
        dataset_id=dataset_id,
        question=question,
        model=model,
        system_prompt=system_prompt,
        dataset_details=dataset_details,
        error_feedback=error_feedback,
        # A non-zero temperature stops the retry from deterministically
        # regenerating the identical statement that just failed at temp 0.
        temperature=SERIAL_RETRY_TEMPERATURE,
        api_key=api_key,
    )
    try:
        result = run_athena_query(sql=sql, catalog=catalog, output_location=output_location, dataset_id=dataset_id)
    except AthenaQueryError as exc:
        if is_execution_failure(exc):
            logger.error(
                f"Serial fallback failed on an execution/permission error for dataset {dataset_id}: "
                f"state={exc.state} reason={exc.reason!r} message={exc.error_message!r}"
            )
            if _kb_la_rao(exc):   # [ghi chú] như trên: rào ⇒ 400, không thử lại ở AB
                raise ValueError(str(exc.reason)[:300]) from exc
            raise
        logger.warning(
            f"Serial fallback also failed for dataset {dataset_id}: state={exc.state} reason={exc.reason!r} sql={sql}"
        )
        raise ValueError(
            "Unable to generate a valid query for this question. "
            "The dataset columns may not match what was requested. "
            "Please rephrase or check the dataset schema."
        ) from exc
    fallback_location = result.get("output_location")
    if catalog and fallback_location:
        # Re-encrypt the fallback result and also clean up any scratch the failed
        # candidates produced (best-effort — failed Athena queries usually write none,
        # so this is defense-in-depth alongside the scratch-bucket TTL).
        candidate_scratch = [qr.output_location for qr in query_results if qr.output_location]
        fallback_location = secure_encrypted_result(
            winner_output_location=fallback_location,
            scratch_outputs=[fallback_location, *candidate_scratch],
        )
    return {
        "rows": result["rows"],
        "columns": result["columns"],
        "execution_id": result["execution_id"],
        "output_location": fallback_location,
        "sql": sql,
        "candidates_generated": len(candidates),
        "candidates_succeeded": 0,
        "strategy": "serial_fallback",
    }
