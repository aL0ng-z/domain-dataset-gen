#!/usr/bin/env python
"""存量 ParserProfile 受控迁移：旧网络字段 -> endpoint_ref（T03）。

用法（在仓库根目录）：
  python scripts/migrate_parser_profiles.py --dry-run   # 只输出映射计划，不写入
  python scripts/migrate_parser_profiles.py --check     # 检查是否已全部迁移（exit 0/1）
  python scripts/migrate_parser_profiles.py --migrate   # 单事务写入（可重复运行）

合同（对齐 T03 §4/§12）：
- 仅将与 registry base_url 精确匹配的旧 URL 映射为 endpoint_ref 并删除网络字段；
- 无法唯一映射的 profile 使迁移在写入前整体失败，输出 profile ID 与 URL hash
  （不输出完整 signed URL / secret）；
- 迁移单事务、可重复运行；--check 幂等；
- 迁移完成后旧网络字段清空，运行时对含旧网络字段的 profile 一律拒绝。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in ("apps/api", "libs/domain", "libs/storage", "libs/parsing", "libs/cleaning", "libs/splitters", "libs/llm"):
    _path = str(REPO_ROOT / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

import asyncio  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

# 禁止字段：与 parsing.snapshot.FORBIDDEN_OPTION_KEYS 保持一致。
FORBIDDEN_KEYS = {
    "base_url", "upload_url", "results_url", "vlm_base_url", "server_url",
    "proxy", "headers", "api_key", "access_token", "token", "secret",
    "credential", "authorization", "password", "auth_scheme", "sig", "signature", "x_api_key",
}

#: 规范化后（去掉连字符/下划线、小写）的禁止字段集合，与 parsing.snapshot 语义一致。
_FORBIDDEN_NORMALIZED = frozenset(k.replace("-", "").replace("_", "").lower() for k in FORBIDDEN_KEYS)

NETWORK_KEYS = {"base_url", "upload_url", "results_url", "vlm_base_url", "server_url"}


def _url_hash(url: str) -> str:
    """URL 的短 hash：迁移失败时输出 hash 而非完整 URL/secret。"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _find_forbidden(options: dict) -> list[str]:
    hits: list[str] = []
    for key, value in (options or {}).items():
        norm = key.replace("-", "").replace("_", "").lower()
        if norm in _FORBIDDEN_NORMALIZED:
            hits.append(key)
        elif isinstance(value, dict):
            hits.extend(_find_forbidden(value))
    return hits


def _strip_forbidden(options: dict | None) -> dict:
    """递归删除禁止字段，返回清理后的副本。"""
    if not options:
        return {}
    cleaned: dict = {}
    for key, value in options.items():
        norm = key.replace("-", "").replace("_", "").lower()
        if norm in _FORBIDDEN_NORMALIZED:
            continue
        if isinstance(value, dict):
            cleaned[key] = _strip_forbidden(value)
        else:
            cleaned[key] = value
    return cleaned


def _build_registry():
    from app.security.registry import build_registry

    return build_registry()


def _profile_base_url(parser_name: str, options: dict) -> str | None:
    """提取 profile 中表示服务地址的字段（与 registry base_url 比较）。"""
    return str(options.get("base_url", "")).strip().rstrip("/") or None


def _plan_migration(profiles: list, registry) -> tuple[list[dict], list[dict], list[dict]]:
    """返回 (可迁移, 无法唯一映射, 跳过/已迁移)。

    每个 profile 计划包含：id/name/parser_name/endpoint_ref/清理后的 options。
    """
    mapped: list[dict] = []
    unmapped: list[dict] = []
    skipped: list[dict] = []
    for profile in profiles:
        parser_name = profile.parser_name
        options = profile.parser_options or {}
        forbidden = _find_forbidden(options)
        if not forbidden:
            skipped.append({"id": str(profile.id), "name": profile.name, "reason": "no_forbidden_fields"})
            continue

        # 本地解析器：直接删除网络字段即可（不需要 endpoint_ref）。
        if parser_name in ("pymupdf4llm", "mock"):
            cleaned = _strip_forbidden(options)
            mapped.append(
                {
                    "id": str(profile.id),
                    "name": profile.name,
                    "parser_name": parser_name,
                    "endpoint_ref": None,
                    "cleaned_options": cleaned,
                    "removed": sorted(set(forbidden)),
                }
            )
            continue

        # 远端/本地服务解析器：必须精确匹配 registry base_url。
        base_url = _profile_base_url(parser_name, options)
        definition = registry.get_for_parser(parser_name) if base_url else None
        if definition is None or definition.base_url != base_url:
            unmapped.append(
                {
                    "id": str(profile.id),
                    "name": profile.name,
                    "parser_name": parser_name,
                    "url_hash": _url_hash(base_url or "<none>"),
                }
            )
            continue

        cleaned = _strip_forbidden(options)
        cleaned["endpoint_ref"] = definition.endpoint_ref
        mapped.append(
            {
                "id": str(profile.id),
                "name": profile.name,
                "parser_name": parser_name,
                "endpoint_ref": definition.endpoint_ref,
                "cleaned_options": cleaned,
                "removed": sorted(set(forbidden)),
            }
        )
    return mapped, unmapped, skipped


async def _load_profiles(db: AsyncSession) -> list:
    from app.models.config import ParserProfile

    result = await db.execute(select(ParserProfile).order_by(ParserProfile.created_at))
    return list(result.scalars().all())


async def _apply_migration(db: AsyncSession, mapped: list[dict]) -> None:
    from app.models.config import ParserProfile

    for plan in mapped:
        result = await db.execute(select(ParserProfile).where(ParserProfile.id == plan["id"]))
        profile = result.scalar_one_or_none()
        if profile is None:
            continue
        profile.parser_options = plan["cleaned_options"]
        profile.version = profile.version + 1
    await db.commit()


async def main(mode: str, db_url: str) -> int:
    engine = create_async_engine(db_url)
    async with engine.begin():
        pass  # 连接校验
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            registry = _build_registry()
            profiles = await _load_profiles(db)
            mapped, unmapped, skipped = _plan_migration(profiles, registry)

            print(f"Profile 总数: {len(profiles)}")
            print(f"  可迁移: {len(mapped)}  无法唯一映射: {len(unmapped)}  无需迁移: {len(skipped)}")

            if unmapped:
                print("\n[FAIL] 存在无法唯一映射的存量 profile；迁移在写入前整体失败（fail closed）：")
                for item in unmapped:
                    print(f"  - id={item['id']} name={item['name']!r} parser={item['parser_name']} url_hash={item['url_hash']}")
                return 1

            if mode == "check":
                if mapped:
                    print(f"\n[FAIL] 尚有 {len(mapped)} 个 profile 未迁移。")
                    return 1
                print("\n[OK] 所有存量 profile 均已迁移。")
                return 0

            if mode == "dry-run":
                for plan in mapped:
                    removed = ", ".join(plan["removed"]) or "-"
                    print(
                        f"  映射 {plan['name']} ({plan['parser_name']}) -> "
                        f"endpoint_ref={plan['endpoint_ref'] or 'local'} 删除字段=[{removed}]"
                    )
                print("\n[dry-run] 未写入数据库。")
                return 0

            # mode == "migrate"
            await _apply_migration(db, mapped)
            print(f"\n[OK] 已迁移 {len(mapped)} 个 profile（单事务提交）。")
            return 0
    finally:
        await engine.dispose()


def _default_db_url() -> str:
    """从环境变量构造 DB URL（与 app.config.settings 一致）。"""
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "datasetgen")
    user = os.environ.get("POSTGRES_USER", "datasetgen")
    password = os.environ.get("POSTGRES_PASSWORD", "datasetgen_dev_password")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def main_entry() -> int:
    parser = argparse.ArgumentParser(description="存量 ParserProfile 受控迁移")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="只输出映射计划，不写入")
    group.add_argument("--check", action="store_true", help="检查是否已全部迁移")
    group.add_argument("--migrate", action="store_true", help="单事务写入迁移")
    parser.add_argument("--db-url", default=None, help="数据库 URL（默认从环境变量构造）")
    args = parser.parse_args()

    mode = "migrate" if args.migrate else ("check" if args.check else "dry-run")
    db_url = args.db_url or _default_db_url()
    return asyncio.run(main(mode, db_url))


if __name__ == "__main__":
    raise SystemExit(main_entry())
