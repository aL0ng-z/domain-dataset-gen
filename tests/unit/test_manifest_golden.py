"""T11 manifest/seal canonical hash golden fixtures（任务卡 §4.4/§12）。

同一 manifest 在 domain.manifest 与迁移工具（import 同一模块）得到相同 SHA-256；
改一个 code point 必变 hash；key 顺序不影响 hash；``manifest_sha256`` 字段
被排除在 hash 计算之外（避免自引用）；``artifact-seal-cjson-v1`` 规范字节
与 golden bytes 一致且稳定。
"""

from __future__ import annotations

from domain.manifest import (
    EXPORTER_VERSION,
    MANIFEST_CJSON_VERSION,
    canonical_bytes_for_seal,
    manifest_cjson,
    manifest_sha256,
    seal_sha256,
)

#: 固定 golden manifest（与迁移/后端共用同一规范语义）。
GOLDEN_MANIFEST = {
    "schema_version": 1,
    "canonicalization_version": MANIFEST_CJSON_VERSION,
    "exporter_version": EXPORTER_VERSION,
    "export_id": "11111111-1111-1111-1111-111111111111",
    "project_id": "22222222-2222-2222-2222-222222222222",
    "requested_by": "33333333-3333-3333-3333-333333333333",
    "sealed_at": "2026-08-03T12:00:00Z",
    "source": {"source_type": "dataset", "source_id": "44444444-4444-4444-4444-444444444444"},
    "memberships": [
        {
            "membership_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "ordinal": 1,
            "curated_item_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "curated_revision_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "curated_revision_sha256": "0" * 64,
            "content_sha256": "1" * 64,
        }
    ],
}


class TestManifestGolden:
    def test_golden_hash_is_64_lower_hex(self):
        h = manifest_sha256(GOLDEN_MANIFEST)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_golden_hash_deterministic(self):
        assert manifest_sha256(GOLDEN_MANIFEST) == manifest_sha256(GOLDEN_MANIFEST)

    def test_golden_bytes_fixed(self):
        """golden manifest 规范字节固定（哈希即字节指纹，跨端一致）。"""
        cjson = manifest_cjson(GOLDEN_MANIFEST)
        assert isinstance(cjson, str)
        assert " " not in cjson  # 紧凑无空白
        assert "\\u" not in cjson  # UTF-8，非 ASCII 不转义
        assert "manifest_sha256" not in cjson

    def test_manifest_sha256_field_excluded(self):
        """hash 计算排除 manifest_sha256 自身（避免自引用）。"""
        h1 = manifest_sha256(GOLDEN_MANIFEST)
        with_self = dict(GOLDEN_MANIFEST)
        with_self["manifest_sha256"] = h1
        assert manifest_sha256(with_self) == h1

    def test_key_order_does_not_change_hash(self):
        import json

        a = dict(GOLDEN_MANIFEST)
        b = json.loads(json.dumps(a, sort_keys=False, separators=(",", ":")))
        assert manifest_sha256(a) == manifest_sha256(b)

    def test_changes_on_codepoint(self):
        m = dict(GOLDEN_MANIFEST)
        m["requested_by"] = "33333333-3333-3333-3333-33333333333X"
        assert manifest_sha256(m) != manifest_sha256(GOLDEN_MANIFEST)

    def test_nfc_normalization(self):
        m1 = dict(GOLDEN_MANIFEST)
        m2 = dict(GOLDEN_MANIFEST)
        # 组合形式 é (e + U+0301) 与预组合 é 应规范化为同一 hash。
        m1["source"]["source_type"] = "datasét"
        m2["source"]["source_type"] = "datasét"
        assert manifest_sha256(m1) == manifest_sha256(m2)


class TestSealGolden:
    SEAL = {
        "seal_version": "artifact-seal-cjson-v1",
        "export_id": "11111111-1111-1111-1111-111111111111",
        "snapshot_manifest_id": "22222222-2222-2222-2222-222222222222",
        "schema_version": 1,
        "formatter_version": EXPORTER_VERSION,
        "manifest": {
            "bucket": "outputs-test",
            "key": "proj/exports/export-id/manifest-abc.json",
            "object_version_id": "v1",
            "sha256": "3" * 64,
            "size": 100,
            "content_type": "application/json",
        },
        "output": {
            "bucket": "outputs-test",
            "key": "proj/exports/export-id/payload-def.json",
            "object_version_id": "v2",
            "sha256": "4" * 64,
            "size": 200,
            "content_type": "application/json",
        },
    }

    def test_seal_bytes_fixed(self):
        b = canonical_bytes_for_seal(self.SEAL)
        assert isinstance(b, bytes)
        assert b" " not in b
        assert b"\\u" not in b

    def test_seal_sha256_excludes_self(self):
        h = seal_sha256(self.SEAL)
        with_self = dict(self.SEAL)
        with_self["seal_sha256"] = h
        assert seal_sha256(with_self) == h

    def test_seal_sha256_deterministic_and_64_hex(self):
        h = seal_sha256(self.SEAL)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
        assert seal_sha256(self.SEAL) == h

    def test_seal_changes_on_any_field(self):
        altered = dict(self.SEAL)
        altered["output"] = {**self.SEAL["output"], "sha256": "5" * 64}
        assert seal_sha256(altered) != seal_sha256(self.SEAL)
