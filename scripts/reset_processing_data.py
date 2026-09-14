"""清空开发环境的派生处理记录，保留原始 PDF、账号、项目和配置。

默认仅输出目标统计。实际执行必须显式确认当前数据库、输出 bucket 与服务已停止，
避免误把该开发重置工具用于测试或其他环境。数据库处理记录在一个事务内删除；
事务提交后才按数据库中已登记的精确 key 清理派生对象。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select, text

REPO_ROOT = Path(__file__).resolve().parents[1]
# Settings 的 env_file 相对当前目录读取。开发重置必须使用 API 的同一份 .env。
os.chdir(REPO_ROOT / "apps" / "api")
for _path in (REPO_ROOT / "apps" / "api", REPO_ROOT / "libs" / "storage"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.config import settings  # noqa: E402
from app.database import async_session_factory  # noqa: E402
from app.models.chunk_set import ChunkSet  # noqa: E402
from app.models.cleaned_document_version import CleanedDocumentVersion  # noqa: E402
from app.models.export import Export, ExportArtifactSeal  # noqa: E402
from app.models.parse import ParseJob  # noqa: E402
from app.models.section import Section  # noqa: E402
from storage import get_storage_client  # noqa: E402

# 这些是明确的派生表；用户/项目/成员/配置/提示词版本/Documents/alembic_version
# 不在列表中。顺序满足当前 FK 依赖，数据库 trigger 仅在本次受控重置事务内禁用。
DELETE_ORDER = (
    "dataset_items",
    "benchmark_cases",
    "export_artifact_seals",
    "snapshot_manifests",
    "exports",
    "datasets",
    "benchmarks",
    "evidence_links",
    "review_records",
    "curated_revisions",
    "curated_items",
    "candidate_comments",
    "candidates",
    "llm_usage_logs",
    "generation_runs",
    "generation_batches",
    "chunks",
    "chunk_sets",
    "cleaned_document_versions",
    "section_comments",
    "section_leases",
    "section_revisions",
    "sections",
    "cleaning_jobs",
    "parse_jobs",
    "task_attempts",
    "tasks",
)

# 只关闭不可变性和组合一致性等业务 trigger；外键 constraint trigger 继续生效，
# 因而删除顺序仍被数据库实际验证。
TRIGGER_TABLES = (
    "dataset_items",
    "benchmark_cases",
    "datasets",
    "benchmarks",
    "curated_items",
    "curated_revisions",
    "review_records",
    "snapshot_manifests",
    "exports",
    "export_artifact_seals",
)


@dataclass
class ResetTargets:
    counts: dict[str, int]
    output_keys: set[tuple[str, str]] = field(default_factory=set)
    section_ids: set[str] = field(default_factory=set)


def _quote_table(table: str) -> str:
    """DELETE_ORDER/TRIGGER_TABLES 是本文件内固定白名单，仍显式引用 public schema。"""
    if not table.replace("_", "").isalnum():
        raise ValueError(f"非法表名: {table}")
    return f'public."{table}"'


def _add_key(targets: ResetTargets, bucket: str | None, key: str | None) -> None:
    if bucket and key:
        targets.output_keys.add((bucket, key))


async def _load_targets() -> ResetTargets:
    async with async_session_factory() as db:
        counts: dict[str, int] = {}
        for table in DELETE_ORDER:
            counts[table] = int((await db.execute(text(f"SELECT count(*) FROM {_quote_table(table)}"))).scalar_one())

        targets = ResetTargets(counts=counts)
        parse_rows = (
            await db.execute(select(ParseJob.raw_markdown_key, ParseJob.structured_json_key))
        ).all()
        for raw_key, structured_key in parse_rows:
            _add_key(targets, settings.minio_bucket_outputs, raw_key)
            _add_key(targets, settings.minio_bucket_outputs, structured_key)

        for (artifact_key,) in (await db.execute(select(CleanedDocumentVersion.artifact_key))).all():
            _add_key(targets, settings.minio_bucket_outputs, artifact_key)
        for (artifact_key,) in (await db.execute(select(ChunkSet.artifact_key))).all():
            _add_key(targets, settings.minio_bucket_outputs, artifact_key)

        export_rows = (
            await db.execute(
                select(
                    Export.bucket_name,
                    Export.minio_key,
                    Export.manifest_key,
                )
            )
        ).all()
        for bucket, output_key, manifest_key in export_rows:
            _add_key(targets, bucket, output_key)
            _add_key(targets, bucket, manifest_key)

        seal_rows = (
            await db.execute(
                select(
                    ExportArtifactSeal.output_bucket,
                    ExportArtifactSeal.output_key,
                    ExportArtifactSeal.manifest_bucket,
                    ExportArtifactSeal.manifest_key,
                )
            )
        ).all()
        for output_bucket, output_key, manifest_bucket, manifest_key in seal_rows:
            _add_key(targets, output_bucket, output_key)
            _add_key(targets, manifest_bucket, manifest_key)

        targets.section_ids = {str(section_id) for (section_id,) in (await db.execute(select(Section.id))).all()}
        return targets


async def _reset_database() -> None:
    async with async_session_factory() as db, db.begin():
            # 同一数据库只能有一个受控重置事务。
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('datasetgen:reset-processing-data'))"))

            # 已完成的 Export、快照、精修 revision 等由业务 trigger 保护。它们只在
            # 本次显式确认的开发重置事务内临时停用；外键 constraint trigger 保持启用。
            for table in TRIGGER_TABLES:
                await db.execute(text(f"ALTER TABLE {_quote_table(table)} DISABLE TRIGGER USER"))

            # 先解除 Documents 对派生行的可选指针，Document 和其原始 PDF 均保留。
            await db.execute(
                text(
                    """
                    UPDATE documents
                       SET active_clean_version_id = NULL,
                           active_chunk_set_id = NULL,
                           status = 'uploaded',
                           clean_status = 'not_started'
                    """
                )
            )
            # 删除 Export/CuratedItem 的反向可选引用，满足受限 FK 删除顺序。终态改为
            # 非完成状态，避免 deferred consistency trigger 在事务提交时审计已删除的关联行。
            await db.execute(
                text(
                    """
                    UPDATE exports
                       SET artifact_seal_id = NULL,
                           snapshot_manifest_id = NULL,
                           is_legacy = true,
                           status = 'failed',
                           error_code = 'RESET',
                           error_message = '开发处理记录重置'
                    """
                )
            )
            await db.execute(
                text(
                    """
                    UPDATE curated_items
                       SET status = 'draft',
                           approved_revision_id = NULL,
                           approval_record_id = NULL,
                           approved_by = NULL,
                           approved_at = NULL
                    """
                )
            )
            await db.execute(text("UPDATE generation_batches SET retry_of_generation_batch_id = NULL"))
            await db.execute(text("UPDATE tasks SET parent_task_id = NULL, retry_of_task_id = NULL"))

            for table in DELETE_ORDER:
                await db.execute(text(f"DELETE FROM {_quote_table(table)}"))
            for table in reversed(TRIGGER_TABLES):
                await db.execute(text(f"ALTER TABLE {_quote_table(table)} ENABLE TRIGGER USER"))


async def _delete_exact_object_versions(targets: ResetTargets) -> list[str]:
    """在 DB 提交之后删除已登记的派生对象；不按宽泛前缀删除，也不触碰 documents bucket。"""
    storage = get_storage_client(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_secure,
    )
    failures: list[str] = []
    for bucket, key in sorted(targets.output_keys):
        if bucket != settings.minio_bucket_outputs:
            failures.append(f"跳过非输出桶对象: bucket={bucket!r} key={key!r}")
            continue
        try:
            exists = await asyncio.to_thread(storage.client.bucket_exists, bucket)
            if not exists:
                continue
            versions = await asyncio.to_thread(storage.list_object_versions, bucket, key)
            matching = [item for item in versions if item["key"] == key]
            if not matching:
                continue
            for item in matching:
                version_id = item["version_id"]
                if version_id:
                    await asyncio.to_thread(storage.delete_object_version, bucket, key, version_id)
                else:
                    await asyncio.to_thread(storage.delete_file, bucket, key)
        except Exception as exc:  # noqa: BLE001 - DB 已提交，保留具体对象坐标供人工收尾
            failures.append(f"对象清理失败: bucket={bucket!r} key={key!r} error={type(exc).__name__}")
    return failures


async def _purge_output_bucket_versions() -> list[str]:
    """删除输出 bucket 的全部对象版本，仅供完整开发重置使用。"""
    storage = get_storage_client(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_secure,
    )
    bucket = settings.minio_bucket_outputs
    failures: list[str] = []
    try:
        exists = await asyncio.to_thread(storage.client.bucket_exists, bucket)
        if not exists:
            return failures
        for item in await asyncio.to_thread(storage.list_object_versions, bucket, ""):
            try:
                if item["version_id"]:
                    await asyncio.to_thread(
                        storage.delete_object_version, bucket, item["key"], item["version_id"]
                    )
                else:
                    await asyncio.to_thread(storage.delete_file, bucket, item["key"])
            except Exception as exc:  # noqa: BLE001 - 继续清理其他版本并报告精确坐标
                failures.append(
                    f"对象清理失败: bucket={bucket!r} key={item['key']!r} error={type(exc).__name__}"
                )
    except Exception as exc:  # noqa: BLE001 - 输出 bucket 不可访问时不伪称已清理
        failures.append(f"输出桶枚举失败: bucket={bucket!r} error={type(exc).__name__}")
    return failures


async def _clear_section_lease_cache(section_ids: Iterable[str]) -> list[str]:
    if not section_ids:
        return []
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await client.delete(*(f"section_lease:{section_id}" for section_id in section_ids))
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001 - 缓存不是真实来源，失败不回滚已提交数据库
        return [f"租约缓存清理失败: {type(exc).__name__}"]
    return []


def _print_targets(targets: ResetTargets) -> None:
    print(f"数据库: {settings.postgres_db}")
    print("待清空的派生记录:")
    for table, count in targets.counts.items():
        if count:
            print(f"  {table}: {count}")
    print(f"待清理的已登记输出对象 key: {len(targets.output_keys)}")
    print(f"待清理的 Section 租约缓存: {len(targets.section_ids)}")
    print("保留: users、projects、project_members、各类配置、提示词版本、documents 与 documents bucket 原始 PDF。")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清空开发环境派生处理记录（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="执行数据库和对象清理")
    parser.add_argument("--confirm-database", help="必须精确等于当前 POSTGRES_DB")
    parser.add_argument("--confirm-outputs-bucket", help="必须精确等于当前 MINIO_BUCKET_OUTPUTS")
    parser.add_argument(
        "--purge-output-bucket",
        action="store_true",
        help="清空输出 bucket 的全部对象版本；仅用于完整开发重置",
    )
    parser.add_argument("--acknowledge-services-stopped", action="store_true", help="确认 API/worker 已停止")
    return parser.parse_args()


async def _main(args: argparse.Namespace) -> int:
    if settings.postgres_db.endswith("_test"):
        print("拒绝执行：该工具仅用于开发数据，不能指向测试数据库", file=sys.stderr)
        return 2
    targets = await _load_targets()
    _print_targets(targets)
    if not args.apply:
        print("dry-run 完成；如确认目标无误，使用 --apply 与三个确认参数执行。")
        return 0
    if args.confirm_database != settings.postgres_db:
        print("拒绝执行：--confirm-database 必须精确匹配 POSTGRES_DB", file=sys.stderr)
        return 2
    if args.confirm_outputs_bucket != settings.minio_bucket_outputs:
        print("拒绝执行：--confirm-outputs-bucket 必须精确匹配 MINIO_BUCKET_OUTPUTS", file=sys.stderr)
        return 2
    if not args.acknowledge_services_stopped:
        print("拒绝执行：请先停止 API 和 worker，再传入 --acknowledge-services-stopped", file=sys.stderr)
        return 2

    await _reset_database()
    failures = await _delete_exact_object_versions(targets)
    if args.purge_output_bucket:
        failures.extend(await _purge_output_bucket_versions())
    cache_warnings = await _clear_section_lease_cache(targets.section_ids)
    print("数据库派生记录已清空，Documents 已重置为 uploaded/not_started。")
    for warning in cache_warnings:
        print(f"警告：{warning}", file=sys.stderr)
    if failures:
        print("以下派生对象未自动清理：", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("已登记派生对象已清理；原始 PDF 未触碰。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parse_args())))
