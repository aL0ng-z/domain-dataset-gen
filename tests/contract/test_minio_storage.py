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
        # T11：versioned bucket 状态 + 每 key 多版本对象存储。
        self.versioned_buckets: set[str] = set()
        self.versions: dict[str, dict[str, list[tuple[str, bytes, dict]]]] = {}
        self._version_counter = 0
        self.versioning_fail = False

    def bucket_exists(self, bucket: str) -> bool:
        self.calls.append({"op": "bucket_exists", "bucket": bucket})
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.calls.append({"op": "make_bucket", "bucket": bucket})
        self.buckets.add(bucket)

    def set_bucket_versioning(self, bucket, config) -> None:
        self.calls.append({"op": "set_bucket_versioning", "bucket": bucket})
        if self.versioning_fail:
            return  # 模拟对象存储拒绝启用 versioning
        self.versioned_buckets.add(bucket)

    def get_bucket_versioning(self, bucket):
        self.calls.append({"op": "get_bucket_versioning", "bucket": bucket})
        if bucket in self.versioned_buckets:
            class _Cfg:
                status = "Enabled"
            return _Cfg()
        return None

    def _next_version_id(self) -> str:
        self._version_counter += 1
        return f"v{self._version_counter}"

    def put_object(self, bucket, key, data, length, content_type="application/octet-stream", metadata=None):
        call = {"op": "put_object", "bucket": bucket, "key": key, "length": length, "content_type": content_type}
        if metadata is not None:
            call["metadata"] = metadata
        self.calls.append(call)
        self.objects.setdefault(bucket, {})[key] = data.read()
        vid = self._next_version_id()
        meta = dict(metadata or {})
        meta["content-type"] = content_type
        self.versions.setdefault(bucket, {}).setdefault(key, []).append((vid, self.objects[bucket][key], meta))
        return _PutResult(vid)

    def stat_object(self, bucket, key, version_id=None, **kw):
        self.calls.append({"op": "stat_object", "bucket": bucket, "key": key, "version_id": version_id})
        if version_id is not None:
            for vid, data, meta in self.versions.get(bucket, {}).get(key, []):
                if vid == version_id:
                    return _StatResult(len(data), meta, vid)
            raise FileNotFoundError(f"no version {version_id}")
        data = self.objects[bucket][key]
        # 无 version_id 时返回最新版本（含其 metadata），供复用判断读取 hash。
        latest = self.versions.get(bucket, {}).get(key, [("", data, {})])[-1]
        return _StatResult(len(data), latest[2], latest[0])

    def get_object(self, bucket, key, version_id=None, **kw):
        self.calls.append({"op": "get_object", "bucket": bucket, "key": key, "version_id": version_id})
        if version_id is not None:
            for vid, data, _ in self.versions.get(bucket, {}).get(key, []):
                if vid == version_id:
                    return _FakeResponse(data)
            raise FileNotFoundError(f"no version {version_id}")
        data = self.objects[bucket][key]
        return _FakeResponse(data)

    def remove_object(self, bucket, key, version_id=None, **kw):
        self.calls.append({"op": "remove_object", "bucket": bucket, "key": key})
        self.objects[bucket].pop(key, None)

    def presigned_get_object(self, bucket, key, expires, version_id=None):
        self.calls.append({"op": "presigned_get_object", "bucket": bucket, "key": key, "expires": expires, "version_id": version_id})
        base = self.presigned_urls.get(f"{bucket}/{key}", f"https://{self.endpoint}/{bucket}/{key}")
        if version_id:
            return f"{base}?versionId={version_id}"
        return base


class _PutResult:
    def __init__(self, version_id: str):
        self.version_id = version_id


class _StatResult:
    def __init__(self, size: int, metadata: dict, version_id: str):
        self.size = size
        self.metadata = metadata
        self.version_id = version_id


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


# ---------------------------------------------------------------------------
# T11：versioned put/head/download/presign
# ---------------------------------------------------------------------------


def test_ensure_bucket_enables_versioning(fake_minio):
    client, fake = fake_minio
    client.ensure_bucket("outputs", versioned=True)
    assert fake.versioned_buckets == {"outputs"}
    # 再次调用走缓存，不重复启用。
    client.ensure_bucket("outputs", versioned=True)
    assert len([c for c in fake.calls if c["op"] == "set_bucket_versioning"]) == 1


def test_ensure_bucket_versioning_failure_raises(fake_minio):
    client, fake = fake_minio
    fake.versioning_fail = True
    # 模拟对象存储拒绝启用 versioning（启用后仍非 Enabled）。
    with pytest.raises(RuntimeError):
        client.ensure_bucket("outputs", versioned=True)


def test_put_object_versioned_returns_version_id(fake_minio):
    client, fake = fake_minio
    vid = client.put_object_versioned("outputs", "p/k.json", b"hello", "application/json")
    assert vid == "v1"
    assert fake.versioned_buckets == {"outputs"}
    assert fake.objects["outputs"]["p/k.json"] == b"hello"


def test_put_object_versioned_reuses_same_hash(fake_minio):
    client, fake = fake_minio
    v1 = client.put_object_versioned("outputs", "p/k.json", b"hello", "application/json", sha256="a" * 64)
    v2 = client.put_object_versioned("outputs", "p/k.json", b"hello", "application/json", sha256="a" * 64)
    # 相同 bytes/hash 复用，不产生新版本。
    assert v1 == v2
    assert len(fake.versions["outputs"]["p/k.json"]) == 1


def test_put_object_versioned_new_bytes_creates_new_version(fake_minio):
    client, fake = fake_minio
    v1 = client.put_object_versioned("outputs", "p/k.json", b"hello", "application/json")
    v2 = client.put_object_versioned("outputs", "p/k.json", b"hello2", "application/json")
    assert v1 != v2
    assert len(fake.versions["outputs"]["p/k.json"]) == 2


def test_download_object_version_returns_original_bytes(fake_minio):
    client, fake = fake_minio
    v1 = client.put_object_versioned("outputs", "p/k.json", b"old-version")
    client.put_object_versioned("outputs", "p/k.json", b"new-version")
    # 按保存的 v1 下载仍返回原字节（不被同 key 新版本覆盖）。
    assert client.download_object_version("outputs", "p/k.json", v1) == b"old-version"


def test_stat_object_version_returns_metadata(fake_minio):
    client, fake = fake_minio
    vid = client.put_object_versioned("outputs", "p/k.json", b"data", "application/json")
    stat = client.stat_object_version("outputs", "p/k.json", vid)
    assert stat["size"] == 4
    # put_object_versioned 写入真实 SHA-256 metadata，stat 应原样返回。
    assert len(stat["sha256"]) == 64
    assert all(c in "0123456789abcdef" for c in stat["sha256"])
    assert stat["version_id"] == vid


def test_presign_object_version_pins_version(fake_minio):
    from datetime import timedelta

    client, fake = fake_minio
    vid = client.put_object_versioned("outputs", "p/k.json", b"x")
    url = client.presign_object_version("outputs", "p/k.json", vid, expires=300)
    assert f"versionId={vid}" in url
    call = fake.calls[-1]
    assert call["op"] == "presigned_get_object"
    assert call["expires"] == timedelta(seconds=300)
    assert call["version_id"] == vid
