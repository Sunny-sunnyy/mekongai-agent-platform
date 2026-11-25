import re
import shutil

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import UploadFile
from config import settings
import logging
import os
import magic
import arrow
from typing import List, Optional
import unicodedata as _ud
import urllib.parse as _up

# [ghi chú] Nhãn S3 (tag) lấy cả tên kho do người dùng đặt (routes/uploads.py:453-458).
# Bản gốc ghép "Key=Value&…" KHÔNG mã hoá (dưới) ⇒ MinIO trả InvalidTag ⇒ route gói thành 500. Đo trên MinIO của bộ (17/11): mã hoá URL vẫn
# InvalidTag với mọi chữ ngoài ASCII (kể cả "é") và với , & ( ) — cả đường header lẫn put_object_tagging. Chỉ qua: chữ / số ASCII, khoảng trắng,
# _ . : / = + - @ (đúng bộ ký tự tag AWS ghi). ⇒ làm sạch GIÁ TRỊ nhãn ở mọi chỗ ghi nhãn trong file này: bỏ dấu (NFKD), đ→d, ký tự khác → "_",
# ≤ 256 ký tự; rồi mã hoá URL cho header. Sửa TẠI CHỖ dict nhãn (route dùng lại cùng danh sách ở put_object_tagging, uploads.py:618).
# Chỉ đổi nhãn (để lọc / định tuyến); tên kho thật vẫn ở DynamoDB. Khoá nhãn (Key) do code đặt, đã ASCII — không đổi.
_TAG_OK = re.compile(r"[^A-Za-z0-9 _.:/=+\-@]")


def _lam_sach_gia_tri_tag(v) -> str:
    t = str(v if v is not None else "").replace("đ", "d").replace("Đ", "D")
    t = "".join(c for c in _ud.normalize("NFKD", t) if not _ud.combining(c))
    return _TAG_OK.sub("_", t)[:256]


def _lam_sach_tags(tags):
    for tag in tags or []:
        if isinstance(tag, dict) and "Value" in tag:
            tag["Value"] = _lam_sach_gia_tri_tag(tag["Value"])
    return tags


def _chuoi_tag(tags) -> str:
    return _up.urlencode([(tag["Key"], tag["Value"]) for tag in _lam_sach_tags(tags)])

logger = logging.getLogger(__name__)


class s3Client:
    def __init__(self, *args, encrypt=True, **kwargs):
        self.bucket = kwargs.pop("bucket")
        # Fall back to settings.AWS_REGION before boto3's default chain:
        # without it, an unset env var resolves to the developer's AWS
        # profile region (e.g. us-east-2), and presigned URLs signed for
        # the wrong region are hard-rejected by S3 with
        # AuthorizationQueryParametersError -- real API calls survive via
        # botocore's region redirector, so only presigned downloads break.
        region_name = (
            kwargs.pop("region_name", None)
            or os.environ.get("AWS_REGION")
            or settings.AWS_REGION
        )
        self.total = 0
        self.uploaded = 0
        self.s3 = boto3.client("s3", region_name=region_name, config=Config(signature_version="s3v4"))

        # App-level encryption: wrap the S3 client when enabled.
        # Callers can pass encrypt=False for buckets that serve content
        # directly to browsers (e.g. org logos) where no decrypt proxy exists.
        self._encryption_enabled = settings.ENABLE_APP_ENCRYPTION and encrypt
        self._encrypted_s3 = None
        if self._encryption_enabled:
            self._encrypted_s3 = self._init_encrypted_client()

    @property
    def encryption_enabled(self) -> bool:
        """Whether app-level encryption is active for this client instance."""
        return self._encryption_enabled and self._encrypted_s3 is not None

    def verify_download_token(self, token: str) -> dict:
        """Verify a JWT download token and return the payload (bucket, key).

        Raises HTTPException-friendly errors if encryption is not enabled.
        """
        if not self.encryption_enabled:
            raise RuntimeError("Encryption is not enabled on this s3Client instance.")
        return self._encrypted_s3.verify_download_token(token)

    def get_decrypted_object(self, bucket: str, key: str) -> dict:
        """Fetch and decrypt an S3 object. Returns the same dict as boto3 get_object
        but with Body replaced by the SDK's decrypting stream (a file-like that may be
        non-seekable for v2 objects; callers should ``.read()`` it, not seek).

        Raises RuntimeError if encryption is not enabled.
        """
        if not self.encryption_enabled:
            raise RuntimeError("Encryption is not enabled on this s3Client instance.")
        # get_object_stream (capai-crypto-sdk >=0.3.0) handles v1 (whole-object
        # in-memory fallback), v2 (bounded-memory streaming decrypt), and
        # plaintext passthrough. Caller only .read()s the Body, so a
        # non-seekable v2 stream is fine. Do NOT pass Range (encrypted objects
        # must decrypt from the start).
        return self._encrypted_s3.get_object_stream(Bucket=bucket, Key=key)

    def get_object(self, key: str) -> dict:
        """Fetch an S3 object, transparently decrypting when encryption is enabled.

        Returns the same dict shape as boto3 ``get_object``.
        """
        if self.encryption_enabled:
            # get_object_stream (capai-crypto-sdk >=0.3.0) transparently handles
            # v1 (whole-object fallback), v2 (streaming decrypt), and plaintext.
            # No Range arg — encrypted objects must decrypt from the start.
            return self._encrypted_s3.get_object_stream(Bucket=self.bucket, Key=key)
        return self.s3.get_object(Bucket=self.bucket, Key=key)

    def _init_encrypted_client(self):
        """Lazily import and initialize EncryptedS3Client from capai-crypto-sdk."""
        from capai_crypto import EncryptedS3Client
        from azure.identity import ClientSecretCredential

        credential = ClientSecretCredential(
            tenant_id=settings.CAPAI_CRYPTO_TENANT_ID,
            client_id=settings.CAPAI_CRYPTO_CLIENT_ID,
            client_secret=settings.CAPAI_CRYPTO_CLIENT_SECRET,
        )
        return EncryptedS3Client(
            s3_client=self.s3,
            vault_url=settings.CAPAI_CRYPTO_VAULT_URL,
            kek_name=settings.CAPAI_CRYPTO_KEK_NAME,
            credential=credential,
            download_token_secret=settings.CAPAI_CRYPTO_DOWNLOAD_TOKEN_SECRET,
        )

    def exists_in_s3(self, key):
        try:
            s3obj = self.s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            else:
                raise
        return s3obj

    def dir_exists(self, prefix):
        """
        Check if a directory (prefix) exists in the S3 bucket

        In S3, directories are virtual concepts represented by prefixes in object keys.
        This method checks if any objects exist with the given prefix.

        Args:
            prefix (str): S3 prefix to check (e.g., "orgid/collection/")

        Returns:
            bool: True if the directory exists, False otherwise
        """
        try:
            # Ensure the prefix ends with a slash to avoid partial matches
            if not prefix.endswith("/"):
                prefix = prefix + "/"

            # List objects with the given prefix with a limit of 1
            response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=1)

            # If any objects are returned, the directory exists
            return "Contents" in response and len(response["Contents"]) > 0

        except ClientError as e:
            logger.error(f"Error checking if directory exists: {e}")
            raise

    def empty_bucket(self):
        """
        Caution!! Empties all objects from the bucket.
        """
        # Create an S3 resource and use it to delete all objects in the bucket
        s3_resource = boto3.resource("s3")
        bucket = s3_resource.Bucket(self.bucket)
        bucket.objects.all().delete()

    def upload_callback(self, size):
        if self.total == 0:
            return
        self.uploaded += size
        # logger.debug("{} %".format(int(self.uploaded / self.total * 100)))

    def upload(self, key, file, content_type=None, metadata=None, tags=None):
        """
        Upload a file to S3 with optional content type, metadata, and tags

        Args:
            key (str): S3 object key
            file (str): Path to the file to upload
            content_type (str, optional): Content type of the file. If None, auto-detected.
            metadata (dict, optional): Metadata to attach to the S3 object
            tags (list, optional): List of dictionaries with 'Key' and 'Value' keys for S3 object tags
                                  Example: [{'Key': 'project', 'Value': 'demo'}, {'Key': 'env', 'Value': 'dev'}]

        Returns:
            bool: True if successful
        """
        self.total = os.stat(file).st_size
        if content_type is None:
            mime = magic.Magic(mime=True)
            content_type = mime.from_file(file)

        extra_args = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = metadata

        # Add tags if provided
        if tags:
            # Convert tags list to the format required by S3 (URL-encoded string)
            tag_string = _chuoi_tag(tags)   # [chay_thu] gốc: "&".join(f"{Key}={Value}") không làm sạch / mã hoá
            extra_args["Tagging"] = tag_string

        if self._encryption_enabled and self._encrypted_s3 is not None:
            # Streaming AEAD (capai-crypto-sdk >=0.3.0): put_object_stream
            # consumes the file-like Body incrementally and segment-encrypts it,
            # writing v2 (Tink AES256-GCM-HKDF) objects. The SDK spools its own
            # ciphertext temp >64 MiB, so the full plaintext is never buffered.
            with open(file, "rb") as data:
                self._encrypted_s3.put_object_stream(
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    **extra_args,
                )
        else:
            with open(file, "rb") as data:
                self.s3.upload_fileobj(
                    data,
                    self.bucket,
                    key,
                    Callback=self.upload_callback,
                    ExtraArgs=extra_args,
                )
        return True

    def put_object_stream(self, key, body, content_type=None, metadata=None):
        """Stream a file-like ``body`` to S3, segment-encrypting (AEAD) when encryption
        is enabled. Unlike ``upload`` (which takes a path), this consumes a stream — e.g.
        another S3 object's ``get_object`` Body — so no local temp file is needed and
        memory stays bounded (the SDK spools its own ciphertext temp >64 MiB).
        """
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        if metadata:
            extra_args["Metadata"] = metadata

        if self._encryption_enabled and self._encrypted_s3 is not None:
            self._encrypted_s3.put_object_stream(Bucket=self.bucket, Key=key, Body=body, **extra_args)
        else:
            self.s3.upload_fileobj(body, self.bucket, key, ExtraArgs=extra_args)
        return True

    @staticmethod
    def pme_sidecar_key(parquet_key: str) -> str:
        """Derive the external key-material sidecar key for a PME parquet key.

        ``…/<dir>/<name>.parquet`` -> ``…/<dir>/_KEY_MATERIAL_FOR_<name>.parquet.json``
        (the convention parquet-mr/PyArrow use; co-located with the parquet).
        """
        directory = os.path.dirname(parquet_key)
        sidecar = f"_KEY_MATERIAL_FOR_{os.path.basename(parquet_key)}.json"
        return f"{directory}/{sidecar}" if directory else sidecar

    def put_pme_object(self, key, source, *, master_key_id=None, content_type="application/octet-stream",
                       metadata=None, tags=None):
        """Write a pyarrow ``Table``/``RecordBatchReader`` as a PME Parquet
        (+ its key-material sidecar) to S3 — encryption-path only.

        Delegates to ``EncryptedS3Client.put_pme_object`` (streamed; ciphertext
        only spills to disk; stamps ``x-capai-object-class`` markers). Returns the
        SDK summary dict (``parquet_key``, ``key_material_key``, ``masterKeyID``).
        """
        if not self.encryption_enabled:
            raise RuntimeError("put_pme_object requires app-level encryption to be enabled.")
        extra_args = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = metadata
        if tags:
            extra_args["Tagging"] = _chuoi_tag(tags)   # [chay_thu] như upload()
        return self._encrypted_s3.put_pme_object(
            Bucket=self.bucket, Key=key, source=source, master_key_id=master_key_id, **extra_args
        )

    def get_pme_object(self, key):
        """Download a PME Parquet (+ sidecar) and decrypt it to a pyarrow Table."""
        if not self.encryption_enabled:
            raise RuntimeError("get_pme_object requires app-level encryption to be enabled.")
        return self._encrypted_s3.get_pme_object(Bucket=self.bucket, Key=key)

    def delete_pme_pair(self, parquet_key: str) -> None:
        """Delete a PME parquet and its key-material sidecar together.

        The two objects are a unit, so they are removed as a pair. Sidecar
        deletion is best-effort (it may be absent for a not-yet-PME object, e.g.
        an org that just enabled encryption).
        """
        self.s3.delete_object(Bucket=self.bucket, Key=parquet_key)
        sidecar_key = self.pme_sidecar_key(parquet_key)
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=sidecar_key)
        except Exception as exc:  # noqa: BLE001 — best-effort; sidecar may not exist
            logger.warning("Failed to delete PME sidecar %s: %s", sidecar_key, exc)

    def delete_parquet(self, parquet_key: str) -> None:
        """Delete a dataset parquet — plus its PME key-material sidecar when encryption
        is on. The single entry point for parquet deletion so callers don't repeat the
        encrypted-vs-plaintext branch (and always delete from this client's bucket)."""
        if self.encryption_enabled:
            self.delete_pme_pair(parquet_key)
        else:
            self.s3.delete_object(Bucket=self.bucket, Key=parquet_key)

    def download(self, key: str, download_path: str = "/tmp"):
        """
        Download a file from S3 to a local path

        Args:
            key (str): S3 object key
            download_path (str): Local path to download the file to

        Returns:
            str: Path to the downloaded file
        """
        exists = self.exists_in_s3(key)
        if not exists:
            raise FileNotFoundError(f"S3 object with key '{key}' not found in bucket '{self.bucket}'")

        # Extract filename from key
        filename = key.split("/")[-1] if "/" in key else key

        # Create the full local file path
        local_file_path = os.path.join(download_path, filename)

        try:
            # Ensure the download directory exists
            os.makedirs(os.path.dirname(local_file_path), exist_ok=True)

            # Download the file
            logger.info(f"Downloading S3 object '{key}' to '{local_file_path}'")

            if self._encryption_enabled and self._encrypted_s3 is not None:
                # get_object_stream (capai-crypto-sdk >=0.3.0) returns a Body that
                # decrypts on the fly for v2 objects (bounded memory), falls back
                # to whole-object in-memory decrypt for legacy v1 objects, and
                # passes plaintext through. shutil.copyfileobj reads the Body
                # strictly forward, so a non-seekable v2 stream is fine. No Range
                # arg — encrypted objects must decrypt from the start.
                response = self._encrypted_s3.get_object_stream(Bucket=self.bucket, Key=key)
                body = response["Body"]
                try:
                    with open(local_file_path, "wb") as f:
                        shutil.copyfileobj(body, f)
                finally:
                    close = getattr(body, "close", None)
                    if close is not None:
                        close()
            else:
                self.s3.download_file(Bucket=self.bucket, Key=key, Filename=local_file_path)

            logger.info(f"Successfully downloaded S3 object '{key}' to '{local_file_path}'")
            return local_file_path

        except ClientError as e:
            logger.error(f"Error downloading S3 object '{key}': {e}")
            raise

    def create_presigned_url(self, object_key, expiration=3600, download: bool = False, filename: str | None = None):
        """Generate a presigned URL to share an S3 object
        https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-presigned-urls.html

        When app-level encryption is enabled, returns a JWT download token URL
        (via /api/v1/files/download?token=...) instead of a native S3 presigned URL
        because encrypted objects cannot be served directly by S3.

        :param object_key: string
        :param expiration: Time in seconds for the presigned URL to remain valid
        :param download: If True, sets the Content-Disposition header to attachment for file download
        :param filename: Optional download filename for Content-Disposition. Applied to both
            direct S3 presigned URLs and encrypted proxy URLs.
        :return: Presigned URL as string. If error, returns None.
        """
        if self._encryption_enabled and self._encrypted_s3 is not None:
            try:
                token = self._encrypted_s3.generate_download_url(
                    Bucket=self.bucket,
                    Key=object_key,
                    expires_in=expiration,
                )
                base_url = settings.PLATFORM_API_BASE_URL.rstrip("/")
                url = f"{base_url}/api/v1/files/download?token={token}"
                if download:
                    url += "&download=1"
                if filename:
                    from urllib.parse import quote
                    url += f"&filename={quote(filename)}"
                return url
            except Exception as e:
                logger.error(f"Error generating encrypted download token: {e}")
                return None

        # Standard S3 presigned URL path (encryption disabled)
        params = {
            "Bucket": self.bucket,
            "Key": object_key,
        }
        if download:
            if filename:
                safe_name = re.sub(r'[\x00-\x1f\x7f\\"]', "_", filename)
                params["ResponseContentDisposition"] = f'attachment; filename="{safe_name}"'
            else:
                params["ResponseContentDisposition"] = "attachment"
        try:
            response = self.s3.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expiration,
            )
        except ClientError as e:
            logger.error(f"Error generating presigned URL: {e}")
            return None

        # The response contains the presigned URL
        return response

    def list_objects(
        self,
        prefix: str = "",
        include_metadata: bool = True,
        include_tags: bool = True,
        exclude_substrings: Optional[List[str]] = None,
    ):
        """
        List objects in the S3 bucket with optional prefix filtering

        Args:
            prefix (str): Prefix to filter objects (e.g., "orgid/collection/")

        Returns:
            list: List of object metadata dictionaries
        """
        try:
            response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)

            # Collect base object info first (single API call already done)
            base_objects = []
            if "Contents" in response:
                for obj in response["Contents"]:
                    key_name = obj["Key"]
                    if exclude_substrings and any(excluded in key_name for excluded in exclude_substrings):
                        continue
                    if not include_metadata and key_name.endswith("metadata.json"):
                        continue
                    if not include_metadata and key_name.endswith(".parquet"):
                        continue
                    filename = key_name.split("/")[-1] if "/" in key_name else key_name
                    if filename == ".collection_info":
                        continue
                    base_objects.append({
                        "key": key_name,
                        "filename": filename,
                        "size": obj["Size"],
                        "size_mb": round(obj["Size"] / 1024 / 1024, 2),
                        "last_modified": obj["LastModified"].isoformat(),
                        "s3_url": f"s3://{self.bucket}/{key_name}",
                    })

            if not base_objects:
                return []

            # Fetch per-object metadata and tags concurrently
            need_enrichment = include_metadata or include_tags
            if need_enrichment:
                enrichment = self._enrich_objects_concurrent(base_objects, include_metadata, include_tags)
            else:
                enrichment = {obj["key"]: {"content_type": "unknown", "metadata": {}, "tags": {}} for obj in base_objects}

            objects = []
            for obj in base_objects:
                extra = enrichment.get(obj["key"], {})
                custom_metadata = extra.get("metadata", {})
                obj["content_type"] = extra.get("content_type", "unknown")
                obj["metadata"] = custom_metadata
                obj["upload_timestamp"] = custom_metadata.get("upload-timestamp")
                obj["tags"] = extra.get("tags", {})
                objects.append(obj)

            return objects

        except ClientError as e:
            logger.error(f"Error listing objects: {e}")
            raise

    def _enrich_objects_concurrent(
        self,
        base_objects: list,
        include_metadata: bool,
        include_tags: bool,
        max_workers: int = 20,
    ) -> dict:
        """Fetch head_object and get_object_tagging concurrently for a list of objects."""
        results = {}

        def _enrich_single(key: str) -> tuple:
            content_type = "unknown"
            custom_metadata = {}
            tags = {}

            if include_metadata:
                try:
                    head_response = self.s3.head_object(Bucket=self.bucket, Key=key)
                    content_type = head_response.get("ContentType", "unknown")
                    custom_metadata = head_response.get("Metadata", {})
                except ClientError:
                    pass

            if include_tags:
                try:
                    tag_response = self.s3.get_object_tagging(Bucket=self.bucket, Key=key)
                    if "TagSet" in tag_response:
                        tags = {tag["Key"]: tag["Value"] for tag in tag_response["TagSet"]}
                except ClientError as e:
                    logger.warning(f"Error getting tags for object {key}: {e}")

            return key, {"content_type": content_type, "metadata": custom_metadata, "tags": tags}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_enrich_single, obj["key"]): obj["key"] for obj in base_objects}
            for future in as_completed(futures):
                try:
                    key, data = future.result()
                    results[key] = data
                except Exception as e:
                    obj_key = futures[future]
                    logger.warning(f"Error enriching object {obj_key}: {e}")
                    results[obj_key] = {"content_type": "unknown", "metadata": {}, "tags": {}}

        return results

    def delete_object(self, key):
        """
        Delete an object from the S3 bucket

        Args:
            key (str): S3 object key to delete

        Returns:
            bool: True if successful
        """
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
            logger.info(f"Successfully deleted object: {key}")
            return True
        except ClientError as e:
            logger.error(f"Error deleting object {key}: {e}")
            raise

    def create_collection(self, orgid, collection) -> str:
        """
        Create a collection (directory) by uploading a placeholder file

        Args:
            orgid (str): Organization ID
            collection (str): Collection name

        Returns:
            bool: True if successful
        """
        try:
            # Create a placeholder file to establish the directory structure
            collection_prefix = f"{orgid}/{collection.lower()}"
            placeholder_key = f"{collection_prefix}/.collection_info"
            placeholder_content = (
                f"Collection: {collection}\nCreated: {arrow.utcnow().isoformat()}\nOrganization: {orgid}"
            )

            # Upload placeholder content
            self.s3.put_object(
                Bucket=self.bucket,
                Key=placeholder_key,
                Body=placeholder_content.encode("utf-8"),
                ContentType="text/plain",
                Metadata={
                    "collection-name": collection,
                    "organization-id": orgid,
                    "created-timestamp": arrow.utcnow().isoformat(),
                    "type": "collection-info",
                },
            )

            logger.info(f"Successfully created collection: {orgid}/{collection}")
            return collection_prefix
        except ClientError as e:
            logger.error(f"Error creating collection {orgid}/{collection}: {e}")
            raise

    def delete_collection(self, s3_prefix: str) -> dict:
        """
        Delete an entire collection (directory) and all its contents

        Args:
            s3_prefix (str): S3 prefix for the collection

        Returns:
            dict: Summary of deletion operation
        """
        try:
            # List all objects in the collection
            response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=s3_prefix)

            deleted_objects = []
            if "Contents" in response:
                # Delete all objects in the collection
                objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]

                if objects_to_delete:
                    delete_response = self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": objects_to_delete})

                    deleted_objects = [obj["Key"] for obj in delete_response.get("Deleted", [])]

            logger.info(f"Successfully deleted collection: {s3_prefix} ({len(deleted_objects)} objects)")
            return {"s3_prefix": s3_prefix, "deleted_objects": deleted_objects, "total_deleted": len(deleted_objects)}

        except ClientError as e:
            logger.error(f"Error deleting collection {s3_prefix}: {e}")
            raise

    def list_collections(self, orgid):
        """
        List all collections for an organization

        Args:
            orgid (str): Organization ID

        Returns:
            list: List of collection names
        """
        try:
            prefix = f"{orgid}/"
            response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix, Delimiter="/")

            collections = []
            if "CommonPrefixes" in response:
                for prefix_info in response["CommonPrefixes"]:
                    # Extract collection name from prefix
                    collection_path = prefix_info["Prefix"]
                    collection_name = collection_path.rstrip("/").split("/")[-1]
                    collections.append(collection_name)

            return collections

        except ClientError as e:
            logger.error(f"Error listing collections for {orgid}: {e}")
            raise

    def get_tags(self, key):
        """
        Get tags for an S3 object

        Args:
            key (str): S3 object key

        Returns:
            dict: Dictionary of tags with 'Key' and 'Value'
        """
        try:
            response = self.s3.get_object_tagging(Bucket=self.bucket, Key=key)
            return {tag["Key"]: tag["Value"] for tag in response.get("TagSet", [])}
        except ClientError as e:
            logger.error(f"Error getting tags for object {key}: {e}")
            raise

    def get_metadata(self, key: str) -> dict:
        """Return the user-defined metadata map for an S3 object.

        Object metadata (what S3 returns under the ``Metadata`` key of
        HeadObject) is distinct from tags: metadata is set at write time on
        the object itself, while tags are a separate mutable keyed store.
        Upload flow writes upload-timestamp, original-filename, and
        file-size-bytes into object metadata, so reindex must read them
        from here — not from tags.
        """
        try:
            response = self.s3.head_object(Bucket=self.bucket, Key=key)
            return response.get("Metadata", {}) or {}
        except ClientError as e:
            logger.error(f"Error getting metadata for object {key}: {e}")
            raise

    def put_tags(self, key, tags):
        """
        Set tags on an S3 object, replacing any existing tags

        Args:
            key (str): S3 object key
            tags (list): List of dictionaries with 'Key' and 'Value' keys for S3 object tags
                        Example: [{'Key': 'project', 'Value': 'demo'}, {'Key': 'env', 'Value': 'dev'}]

        Returns:
            bool: True if successful
        """
        try:
            self.s3.put_object_tagging(Bucket=self.bucket, Key=key, Tagging={"TagSet": tags})
            logger.info(f"Successfully set tags on object: {key}")
            return True
        except ClientError as e:
            logger.error(f"Error setting tags on object {key}: {e}")
            raise

    def append_tags(self, key, new_tags, exclude_keys=None):
        """
        Add new tags to an S3 object while preserving existing tags

        When a tag with the same key already exists in the S3 object, this method will:
        1. Remove the existing tag with that key
        2. Add the new tag with the same key but updated value
        This ensures tag keys remain unique in the final set and new values take precedence.

        Args:
            key (str): S3 object key
            new_tags (list): List of dictionaries with 'Key' and 'Value' keys for new S3 object tags
            exclude_keys (list, optional): List of tag keys to exclude from existing tags before merging

        Returns:
            bool: True if successful

        Raises:
            ValueError: If the total number of tags would exceed the AWS limit of 10 tags per object
        """
        _lam_sach_tags(new_tags)   # [chay_thu] như upload()
        try:
            # Get existing tags
            try:
                existing_tag_response = self.s3.get_object_tagging(Bucket=self.bucket, Key=key)
                existing_tags = existing_tag_response.get("TagSet", [])

                # Filter out excluded keys if provided
                if exclude_keys:
                    existing_tags = [tag for tag in existing_tags if tag["Key"] not in exclude_keys]

                # Filter out keys that will be updated by new_tags
                new_tag_keys = [tag["Key"] for tag in new_tags]
                existing_tags = [tag for tag in existing_tags if tag["Key"] not in new_tag_keys]

                # Combine existing and new tags
                updated_tags = existing_tags + new_tags

                # Check if the total number of tags exceeds the AWS limit of 10
                if len(updated_tags) > 10:
                    error_msg = f"Total number of tags ({len(updated_tags)}) would exceed AWS limit of 10 tags per object for {key}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            except ClientError as tag_error:
                logger.warning(f"Error getting existing tags for {key}: {str(tag_error)}. Using only new tags.")
                updated_tags = new_tags

                # Still check the limit even if we're only using new tags
                if len(updated_tags) > 10:
                    error_msg = f"Total number of tags ({len(updated_tags)}) would exceed AWS limit of 10 tags per object for {key}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            # Update object with combined tags
            self.s3.put_object_tagging(Bucket=self.bucket, Key=key, Tagging={"TagSet": updated_tags})

            logger.info(f"Successfully appended tags to object: {key}")
            return True

        except ClientError as e:
            logger.error(f"Error appending tags to object {key}: {e}")
            raise
