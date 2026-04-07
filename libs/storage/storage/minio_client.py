from io import BytesIO

from minio import Minio


class StorageClient:
    _verified_buckets: set[str] = set()

    def __init__(self, endpoint: str, access_key: str, secret_key: str, secure: bool = False):
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    def ensure_bucket(self, bucket: str) -> None:
        if bucket in self._verified_buckets:
            return
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)
        self._verified_buckets.add(bucket)

    def upload_file(self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.ensure_bucket(bucket)
        self.client.put_object(bucket, key, BytesIO(data), length=len(data), content_type=content_type)
        return key

    def download_file(self, bucket: str, key: str) -> bytes:
        response = self.client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def get_presigned_url(self, bucket: str, key: str, expires: int = 3600) -> str:
        from datetime import timedelta
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
