"""Storage 适配器契约测试（libs/storage）。

验证 StorageClient 对 MinIO 的封装边界：
- ensure_bucket 只在 bucket 不存在时创建（并做内存缓存）；
- upload_file 使用正确 bucket/key/content_type；
- download_file 返回原始字节；delete_file 调用 remove_object；
- get_presigned_url 使用秒级过期。

使用 FakeMinio 替换底层 minio.Minio，记录调用参数，便于安全断言；
不发出任何真实网络请求。
"""

import pytest

from storage.minio_client import StorageClient


class FakeMinio:
    def __init__(self, endpoint, access_key, secret_key, secure=False):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure
        self.buckets: set[str] = set()
        self.objects: dict[str, dict[str, bytes]] = {}
        self.calls: list[dict] = []
        self.presigned_urls: dict[str, str] = {}

    def bucket_exists(self, bucket: str) -> bool:
        self.calls.append({"op": "bucket_exists", "bucket": bucket})
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.calls.append({"op": "make_bucket", "bucket": bucket})
        self.buckets.add(bucket)

    def put_object(self, bucket, key, data, length, content_type="application/octet-stream"):
        self.calls.append(
            {"op": "put_object", "bucket": bucket, "key": key, "length": length, "content_type": content_type}
        )
        self.objects.setdefault(bucket, {})[key] = data.read()

    def get_object(self, bucket, key):
        self.calls.append({"op": "get_object", "bucket": bucket, "key": key})
        data = self.objects[bucket][key]
        return _FakeResponse(data)

    def remove_object(self, bucket, key):
        self.calls.append({"op": "remove_object", "bucket": bucket, "key": key})
        self.objects[bucket].pop(key, None)

    def presigned_get_object(self, bucket, key, expires):
        self.calls.append({"op": "presigned_get_object", "bucket": bucket, "key": key, "expires": expires})
        return self.presigned_urls.get(f"{bucket}/{key}", f"https://{self.endpoint}/{bucket}/{key}")
class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


@pytest.fixture
def fake_minio(monkeypatch) -> tuple[StorageClient, FakeMinio]:
    fake = FakeMinio("minio.test:9000", "ak", "sk")
    monkeypatch.setattr("storage.minio_client.Minio", lambda *a, **kw: fake)
    return StorageClient("minio.test:9000", "ak", "sk"), fake


def test_ensure_bucket_creates_once_and_caches(fake_minio):
    client, fake = fake_minio
    client.ensure_bucket("documents")
    client.ensure_bucket("documents")
    # bucket_exists 只应被调用一次（第二次走 _verified_buckets 缓存）
    assert [c for c in fake.calls if c["op"] == "make_bucket"] == [{"op": "make_bucket", "bucket": "documents"}]
    assert fake.buckets == {"documents"}


def test_upload_file_records_arguments_and_bytes(fake_minio):
    client, fake = fake_minio
    client.upload_file("documents", "p1/doc.pdf", b"%PDF-test", "application/pdf")
    assert fake.objects["documents"]["p1/doc.pdf"] == b"%PDF-test"
    assert fake.calls[-1] == {
        "op": "put_object",
        "bucket": "documents",
        "key": "p1/doc.pdf",
        "length": len(b"%PDF-test"),
        "content_type": "application/pdf",
    }


def test_download_file_returns_original_bytes(fake_minio):
    client, fake = fake_minio
    client.upload_file("documents", "a.txt", b"hello")
    assert client.download_file("documents", "a.txt") == b"hello"


def test_delete_file_removes_object(fake_minio):
    client, fake = fake_minio
    client.upload_file("outputs", "k.json", b"{}")
    client.delete_file("outputs", "k.json")
    assert fake.calls[-1] == {"op": "remove_object", "bucket": "outputs", "key": "k.json"}
    assert "k.json" not in fake.objects["outputs"]


def test_presigned_url_uses_seconds_expiry(fake_minio):
    from datetime import timedelta

    client, fake = fake_minio
    client.upload_file("documents", "f.pdf", b"x")
    url = client.get_presigned_url("documents", "f.pdf", expires=120)
    assert url == "https://minio.test:9000/documents/f.pdf"
    call = fake.calls[-1]
    assert call["op"] == "presigned_get_object"
    assert call["bucket"] == "documents"
    assert call["key"] == "f.pdf"
    # get_presigned_url 将秒数转换为 timedelta 传给底层客户端
    assert call["expires"] == timedelta(seconds=120)
