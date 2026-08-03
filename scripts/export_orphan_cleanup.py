"""审计并清理 versioned outputs bucket 中未被导出 seal 引用的旧版本。

默认只读 dry-run。真实删除必须同时提供 ``--apply`` 与精确匹配的
``--confirm-bucket``；只处理形如 ``.../{project_id}/exports/{export_id}/...`` 的
对象版本，且永不删除 seal 已引用或仍属 queued/processing Export 的版本。
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT / "apps" / "api", REPO_ROOT / "libs" / "storage"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.config import settings  # noqa: E402
from app.database import async_session_factory  # noqa: E402
from app.models.export import Export, ExportArtifactSeal  # noqa: E402
from storage import get_storage_client  # noqa: E402

_EXPORT_OBJECT_NAME = re.compile(
    r"^(?:payload-[0-9a-f]{64}\.[A-Za-z0-9]+|manifest-[0-9a-f]{64}\.json)$"
)


def _parse_export_coordinates(key: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    """仅接受 ``.../{project_uuid}/exports/{export_uuid}/{content-addressed-name}``。"""
    parts = key.split("/")
    try:
        marker = len(parts) - 3
        if marker < 1 or parts[marker] != "exports":
            return None
        if not _EXPORT_OBJECT_NAME.fullmatch(parts[-1]):
            return None
        return uuid.UUID(parts[marker - 1]), uuid.UUID(parts[marker + 1])
    except (ValueError, IndexError):
        return None


async def _load_db_state() -> tuple[
    set[tuple[str, str]],
    dict[uuid.UUID, tuple[uuid.UUID, str, datetime]],
]:
    referenced: set[tuple[str, str]] = set()
    exports: dict[uuid.UUID, tuple[uuid.UUID, str, datetime]] = {}
    async with async_session_factory() as db:
        seals = (await db.execute(select(ExportArtifactSeal))).scalars().all()
        for seal in seals:
            referenced.add((seal.output_key, seal.output_object_version_id))
            referenced.add((seal.manifest_key, seal.manifest_object_version_id))
        rows = (
            await db.execute(select(Export.id, Export.project_id, Export.status, Export.updated_at))
        ).all()
        exports = {
            row.id: (row.project_id, row.status, row.updated_at)
            for row in rows
        }
    return referenced, exports


async def _run(args: argparse.Namespace) -> int:
    bucket = args.bucket or settings.minio_bucket_outputs
    if args.apply and args.confirm_bucket != bucket:
        print("拒绝删除：--confirm-bucket 必须与目标 bucket 精确一致", file=sys.stderr)
        return 2
    if args.older_than_hours < 1:
        print("拒绝执行：保留期必须至少为 1 小时", file=sys.stderr)
        return 2

    referenced, exports = await _load_db_state()
    storage = get_storage_client(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_secure,
    )
    prefix = (args.prefix if args.prefix is not None else settings.minio_key_prefix).strip("/")
    if prefix:
        prefix += "/"
    cutoff = datetime.now(UTC) - timedelta(hours=args.older_than_hours)
    candidates: list[dict] = []

    for item in storage.list_object_versions(bucket, prefix):
        coordinate = (item["key"], item["version_id"])
        if coordinate in referenced:
            continue
        coordinates = _parse_export_coordinates(item["key"])
        if coordinates is None:
            continue
        project_id, export_id = coordinates
        export_state = exports.get(export_id)
        if export_state is not None and export_state[0] != project_id:
            continue
        if export_state is not None and export_state[1] in {"queued", "processing"}:
            continue
        modified = item["last_modified"]
        if modified is None or modified > cutoff:
            continue
        item["export_id"] = str(export_id)
        item["reason"] = (
            "export_missing"
            if export_state is None
            else f"export_{export_state[1]}_unreferenced"
        )
        candidates.append(item)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] bucket={bucket} prefix={prefix!r} candidates={len(candidates)}")
    for item in candidates:
        print(
            f"  {item['key']} version={item['version_id']} size={item['size']} "
            f"delete_marker={item['is_delete_marker']} reason={item['reason']}"
        )
        if args.apply:
            storage.delete_object_version(bucket, item["key"], item["version_id"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="删除列出的精确对象版本")
    parser.add_argument("--confirm-bucket", help="apply 时必须精确重复目标 bucket 名")
    parser.add_argument("--bucket", help="目标 outputs bucket；默认读取应用配置")
    parser.add_argument("--prefix", help="限制对象 key 前缀；默认读取 MINIO_KEY_PREFIX")
    parser.add_argument("--older-than-hours", type=int, default=24, help="最小保留期，默认 24")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
