"""MinIO 适配器（T11 §5：versioned put/head/download）。

新增能力（任务卡 §9 预期修改面）：
- ``ensure_bucket(versioned=False)``：bucket 不存在时创建；``versioned=True`` 时
  启用并验证 bucket versioning（或等价 WORM/write-once）。
- ``put_object_versioned``：返回每次 PUT 的对象 ``version_id``；若同 key 已有
  相同 bytes/hash 的对象则复用（仅 bytes/hash 完全一致时复用，任务卡 §5.4）。
- ``stat_object_version``：按保存的 version_id 做 HEAD，校验 size/hash metadata。
- ``download_object_version``：按 version_id 下载原始字节。
- ``presign_object_version``：按 version_id 签发短时 GET URL（下载固定到版本）。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from io import BytesIO

from minio import Minio
from minio.api import VersioningConfig

logger = logging.getLogger(__name__)


class StorageClient:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, secure: bool = False):
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        #: bucket -> 已校验的 versioning 状态（True=已启用 versioning，False=未启用）。
        self._verified_buckets: dict[str, bool] = {}

    def ensure_bucket(self, bucket: str, *, versioned: bool = False) -> None:
        """确保 bucket 存在；``versioned=True`` 时启用并验证 versioning。

        启用 versioning 是幂等操作；若对象存储不支持 versioning（无 WORM/write-once
        等价能力），启用失败即抛错——不得静默降级（任务卡 §10 风险）。
        """
        cached = self._verified_buckets.get(bucket)
        if cached is versioned:
            return
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)
        if versioned:
            self.client.set_bucket_versioning(
                bucket, VersioningConfig(status="Enabled")
            )
            config = self.client.get_bucket_versioning(bucket)
            if not config or config.status != "Enabled":
                raise RuntimeError(
                    f"bucket {bucket} 无法启用 versioning（无 WORM/write-once 等价能力）"
                )
        self._verified_buckets[bucket] = versioned

    def upload_file(self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.ensure_bucket(bucket)
        self.client.put_object(bucket, key, BytesIO(data), length=len(data), content_type=content_type)
        return key

    def put_object_versioned(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        *,
        sha256: str | None = None,
    ) -> str:
        """versioned PUT，返回对象 ``version_id``。

        - 先 HEAD 检查同 key 已存在对象：若 bytes/hash metadata 与当前数据完全一致
          则复用（不产生新版本，任务卡 §5.4：仅在 bytes/hash 完全一致时复用）。
        - 否则 PUT 并返回服务端分配的 version_id（对象版本必须能按 id 下载）。
        """
        self.ensure_bucket(bucket, versioned=True)
        digest = sha256 or hashlib.sha256(data).hexdigest()
        try:
            existing = self.client.stat_object(bucket, key)
            if existing.size == len(data) and existing.metadata.get("x-amz-meta-sha256") == digest:
                return existing.version_id or ""
        except Exception:  # noqa: BLE001 - 对象不存在/不可读时正常 PUT
            pass
        # MinIO 用户自定义 metadata 必须以 x-amz-meta- 前缀，否则签名计算失败。
        response = self.client.put_object(
            bucket,
            key,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
            metadata={"x-amz-meta-sha256": digest},
        )
        version_id = getattr(response, "version_id", None) or ""
        return str(version_id)

    def stat_object_version(self, bucket: str, key: str, version_id: str) -> dict:
        """按 version_id 做 HEAD，返回 {size, sha256, content_type, version_id}。"""
        obj = self.client.stat_object(bucket, key, version_id=version_id)
        metadata = obj.metadata or {}
        return {
            "size": int(obj.size or 0),
            "sha256": metadata.get("x-amz-meta-sha256") or metadata.get("sha256") or "",
            "content_type": metadata.get("content-type") or "",
            "version_id": getattr(obj, "version_id", None) or version_id,
        }

    def download_object_version(self, bucket: str, key: str, version_id: str) -> bytes:
        """按 version_id 下载原始字节（历史版本不被同 key 新版本覆盖）。"""
        response = self.client.get_object(bucket, key, version_id=version_id)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def presign_object_version(self, bucket: str, key: str, version_id: str, expires: int = 3600) -> str:
        """按 version_id 签发短时 GET URL（下载固定到记录的对象版本）。"""
        return self.client.presigned_get_object(
            bucket, key, expires=timedelta(seconds=expires), version_id=version_id,
        )

    def download_file(self, bucket: str, key: str) -> bytes:
        response = self.client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def get_presigned_url(self, bucket: str, key: str, expires: int = 3600) -> str:
        return self.client.presigned_get_object(bucket, key, expires=timedelta(seconds=expires))

    def delete_file(self, bucket: str, key: str) -> None:
        self.client.remove_object(bucket, key)


_storage_instance: StorageClient | None = None


def get_storage_client(endpoint: str, access_key: str, secret_key: str, secure: bool = False) -> StorageClient:
    """Return a singleton StorageClient to avoid recreating Minio connections."""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = StorageClient(endpoint, access_key, secret_key, secure)
    return _storage_instance


def reset_storage_client() -> None:
    """Reset the singleton（测试隔离：避免跨测试复用错误凭据的客户端）。"""
    global _storage_instance
    _storage_instance = None
