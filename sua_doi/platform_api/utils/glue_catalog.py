"""Utilities for registering Parquet-backed datasets in AWS Glue."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from config import settings
from dynamodb_client.collection_dataset_schemas import CollectionDatasetSchemasClient
from utils import get_logger, slugify

logger = get_logger("glue_catalog")


def _slug_table_name(dataset_id: str, csv_object_key: str) -> str:
    base = os.path.splitext(os.path.basename(csv_object_key))[0]
    return f"{slugify(dataset_id)[:8]}_{slugify(base)}"[:255]


class _GlueGia:
    """[ghi chú] Thay client AWS Glue: Kho bảng chạy SQLite trên máy (utils/llm_athena.py bản chay_thu) và đọc cột thẳng từ
    file Parquet ⇒ Glue chỉ cần NHẬN lệnh đăng ký / xoá (không lưu gì). Tên bảng + key Parquet vẫn được Capitol ghi vào bảng schema
    DynamoDB như gốc (register_parquet_table ghi glue_table_name) — đó là nguồn SQLite dùng."""

    class exceptions:  # noqa: N801 — cùng hình boto3: self.glue.exceptions.EntityNotFoundException
        class EntityNotFoundException(Exception):
            pass

    def get_table(self, **kw):
        raise self.exceptions.EntityNotFoundException(kw.get("Name"))

    def create_table(self, **kw):
        logger.info(f"[chay_thu] Glue giả: create_table {((kw.get('TableInput') or {}).get('Name'))}")
        return {}

    def update_table(self, **kw):
        return {}

    def delete_table(self, **kw):
        raise self.exceptions.EntityNotFoundException(kw.get("Name"))


class GlueCatalogManager:
    """Manage Glue catalog tables for dataset Parquet files."""

    def __init__(
        self,
        *,
        glue_client=None,
        schemas_client: Optional[CollectionDatasetSchemasClient] = None,
        database_name: Optional[str] = None,
    ):
        self.glue = glue_client or _GlueGia()   # [ghi chú] không có AWS Glue trên máy — xem _GlueGia
        self.schemas_client = schemas_client or CollectionDatasetSchemasClient()
        self.database_name = database_name or settings.GLUE_DATASET_DATABASE_NAME

    def register_parquet_table(self, *, dataset_id: str, csv_object_key: str) -> Dict[str, Any]:
        """Create or update a Glue table based on the stored schema metadata."""

        schema_record = self.schemas_client.get_schema(dataset_id=dataset_id, csv_object_key=csv_object_key)
        if not schema_record:
            raise ValueError(
                f"Schema not found for dataset_id={dataset_id}, csv_object_key={csv_object_key}. Run schema sync first."
            )

        metadata = schema_record.get("metadata") or {}
        parquet_key = metadata.get("parquet_object_key")
        if not parquet_key:
            raise ValueError(
                "Parquet object key missing from schema metadata. Run Parquet sync before registering Glue tables."
            )

        table_name = metadata.get("glue_table_name") or _slug_table_name(dataset_id, csv_object_key)
        location = f"s3://{settings.S3_UPLOADS_BUCKET}/{os.path.dirname(parquet_key)}"
        if not location.endswith("/"):
            location = f"{location}/"

        columns = _build_columns(schema_record)
        parameters = _build_parameters(schema_record)

        table_input = {
            "Name": table_name,
            "TableType": "EXTERNAL_TABLE",
            "StorageDescriptor": {
                "Columns": columns,
                "Location": location,
                "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                "SerdeInfo": {
                    "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
                    "Parameters": {"serialization.format": "1"},
                },
            },
            "Parameters": parameters,
        }

        try:
            self.glue.get_table(DatabaseName=self.database_name, Name=table_name)
            self.glue.update_table(DatabaseName=self.database_name, TableInput=table_input)
            action = "updated"
        except self.glue.exceptions.EntityNotFoundException:
            self.glue.create_table(DatabaseName=self.database_name, TableInput=table_input)
            action = "created"
        except ClientError as exc:  # pragma: no cover - unexpected AWS errors
            logger.exception("Glue table lookup failed", exc_info=exc)
            raise

        updated_metadata = {
            **metadata,
            "glue_table_name": table_name,
            "glue_database_name": self.database_name,
        }

        self.schemas_client.put_schema(
            dataset_id=dataset_id,
            csv_object_key=csv_object_key,
            schema=schema_record["schema"],
            metadata=updated_metadata,
        )

        logger.info(
            "Glue table %s for dataset %s (%s)",
            action,
            dataset_id,
            table_name,
            extra={"csv_object_key": csv_object_key},
        )

        return {
            "action": action,
            "table_name": table_name,
            "database_name": self.database_name,
        }

    def drop_table(
        self,
        *,
        table_name: str,
        database_name: Optional[str] = None,
    ) -> None:
        if not table_name:
            return

        db_name = database_name or self.database_name
        try:
            self.glue.delete_table(DatabaseName=db_name, Name=table_name)
            logger.info("Dropped Glue table %s from %s", table_name, db_name)
        except self.glue.exceptions.EntityNotFoundException:
            logger.info("Glue table %s already absent", table_name)
        except ClientError as exc:  # pragma: no cover - unexpected AWS errors
            logger.exception("Failed to drop Glue table %s", table_name, exc_info=exc)
            raise


def _arrow_type_to_glue(arrow_type: Optional[str]) -> str:
    """Map a PyArrow-repr type string (e.g. 'double', 'int64', 'timestamp[ns]')
    to a Hive/Glue type. Unknown → 'string' so Athena falls back to varchar.

    Conservative: we only widen a column out of string when the Parquet footer
    tells us the physical type is numeric/boolean/timestamp. Mis-typing a
    string column as numeric would cause HIVE_BAD_DATA; the reverse (numeric
    written as string) silently works but loses SUM/AVG semantics — which is
    the bug we're fixing here.
    """
    if not arrow_type:
        return "string"
    t = str(arrow_type).lower().strip()

    if t in {"double", "float64"}:
        return "double"
    if t in {"float", "float32", "halffloat", "float16"}:
        return "float"
    if t in {"int64", "uint64"}:
        return "bigint"
    if t in {"int32", "uint32"}:
        return "int"
    if t in {"int16", "uint16"}:
        return "smallint"
    if t in {"int8", "uint8"}:
        return "tinyint"
    if t in {"bool", "boolean"}:
        return "boolean"
    if t.startswith("decimal"):
        # PyArrow repr looks like 'decimal128(10, 2)' — Hive wants 'decimal(10,2)'.
        inner = t[t.find("(") : t.find(")") + 1] if "(" in t and ")" in t else "(38,10)"
        return f"decimal{inner.replace(' ', '')}"
    if t.startswith("timestamp"):
        return "timestamp"
    if t == "date32[day]" or t.startswith("date"):
        return "date"
    if t in {"string", "large_string", "utf8", "large_utf8"}:
        return "string"
    if t in {"binary", "large_binary"}:
        return "binary"
    # Nested/list/struct/map types: keep as string. Athena can't reliably
    # read complex Parquet types via the default SerDe without extra schema work.
    return "string"


def _build_columns(schema_record: Dict[str, Any]) -> List[Dict[str, str]]:
    metadata = schema_record.get("metadata") or {}
    candidate_columns = metadata.get("schema_fields") or schema_record.get("schema", {}).get("columns", [])
    if not candidate_columns:
        return [{"Name": "_raw", "Type": "string"}]

    # schema_types is populated by both the direct-Parquet multipart pipeline
    # and the CSV→Parquet converter (utils/dataset_parquet_sync.py). Some
    # legacy/partial records may still be missing it — in that case we fall
    # back to 'string' for every column.
    raw_types = metadata.get("schema_types") or []

    columns: List[Dict[str, str]] = []
    seen: Dict[str, int] = {}
    for idx, raw_name in enumerate(candidate_columns, start=1):
        sanitized = _sanitize_glue_column_name(raw_name, index=idx)
        key = sanitized.lower()
        if key in seen:
            seen[key] += 1
            sanitized = f"{sanitized}_{seen[key]}"
        else:
            seen[key] = 1
        arrow_type = raw_types[idx - 1] if idx - 1 < len(raw_types) else None
        columns.append({"Name": sanitized, "Type": _arrow_type_to_glue(arrow_type)})
    return columns


def _sanitize_glue_column_name(value: Any, *, index: int) -> str:
    fallback = f"column_{index}"
    if value is None:
        return fallback

    name = str(value).strip()
    if not name:
        return fallback

    sanitized = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")
    sanitized = sanitized.strip("_")
    if not sanitized:
        sanitized = fallback
    if sanitized[0].isdigit():
        sanitized = f"col_{sanitized}"
    return sanitized


def _build_parameters(schema_record: Dict[str, Any]) -> Dict[str, str]:
    params = {
        "classification": "parquet",
        "cap:dataset_id": schema_record.get("dataset_id", ""),
    }
    schema = schema_record.get("schema", {})
    metadata = schema_record.get("metadata", {})

    if schema.get("primary_key"):
        params["cap:primary_key"] = json.dumps(schema["primary_key"])
    if schema.get("foreign_keys"):
        params["cap:foreign_keys"] = json.dumps(schema["foreign_keys"])
    if metadata.get("parquet_object_key"):
        params["cap:parquet_object_key"] = metadata["parquet_object_key"]
    return params
