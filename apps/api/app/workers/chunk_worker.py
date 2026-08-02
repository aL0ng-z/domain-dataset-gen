"""versioned chunk handler（T06 §5、§8；T07 §5）。

``chunk_document`` handler 按冻结输入执行切分，在 checkpoint 后校验统计/hash，
再以 run token 原子发布并切换 ``Document.active_chunk_set_id``。

payload（payload_version=2）: {"document_id": UUID, "chunk_set_id": UUID}

关键语义（任务卡 §8.5、§8.6、§8.8）：
- 只消费 T05 已 accepted 且仍为 Document active 的清洗版本（CleanedDocumentVersion
  status=accepted 且 id == doc.active_clean_version_id），不再从多批 Section 猜测来源。
- 发布事务前重新锁定 Document 并复核 ``active_clean_version_id`` 仍等于该 set 的来源；
  T05 终审已推进时本次切分以 CLEAN_VERSION_STALE 失败（不覆盖新 active 链路）。
- worker 写入前后均用冻结的 tokenizer 复算 token_count，任何 token_count > max_tokens
  都使整个集合 failed，不能截断后静默发布（任务卡 §4.2）。
- 成功：业务写入（staging Chunk + completed set + active pointer）由 runner 完成
  事务原子提交；失败/取消：runner 回滚全部 staging Chunk，旧 active pointer 与默认
  列表不变。
- 通过 ctx.set_terminal_hook 注册业务终态钩子：runner 失败/取消回滚后以新会话把
  ChunkSet 收敛为 failed/cancelled（CAS，不覆盖已完成产物）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.chunk_set import ChunkSet
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.document import Document
from app.models.section import Section
from app.workers.execution import ExecutionContext
from splitters import (
    ChunkConfig,
    canonical_output_sha256,
    get_chunker,
)


class CleanVersionStaleError(Exception):
    """发布时发现来源清洗版本已不是 active（T05 终审推进）。"""


async def run_chunk_handler(ctx: ExecutionContext) -> None:
    """chunk_document:v2 handler：按冻结 ChunkSet 输入切分并原子发布。"""
    payload = ctx.payload
    document_id = uuid.UUID(str(payload["document_id"]))
    chunk_set_id = uuid.UUID(str(payload["chunk_set_id"]))
    db = ctx.db

    # 项目链复核：以 task.project_id 为锚点。
    from app.authz import ProjectChainError, verify_project_chain

    await verify_project_chain(
        db,
        ctx.project_id,
        [(Document, document_id), (ChunkSet, chunk_set_id)],
        detail="切分任务项目链不一致",
    )

    # 加载冻结的 ChunkSet（只读输入；绝不从当前 Section/Profile 猜测）。
    chunk_set = (
        await db.execute(
            select(ChunkSet).where(ChunkSet.id == chunk_set_id).with_for_update()
        )
    ).scalar_one_or_none()
    if chunk_set is None or chunk_set.document_id != document_id:
        raise ProjectChainError("切分集合不存在")
    if chunk_set.is_legacy:
        raise ProjectChainError("legacy 集合不可重新切分")
    await ctx.checkpoint()

    # 幂等保护：completed/rejected 不可重放。
    if chunk_set.status in ("completed", "rejected"):
        raise ProjectChainError("切分集合已终态，不可重复执行")

    # 注册业务终态钩子：失败/取消时把本 ChunkSet 收敛为 failed/cancelled。
    ctx.set_terminal_hook(_make_terminal_hook(chunk_set.id))

    # 只消费已 accepted 且仍为 active 的清洗版本。
    clean_version = (
        await db.execute(
            select(CleanedDocumentVersion).where(
                CleanedDocumentVersion.id == chunk_set.cleaned_document_version_id,
                CleanedDocumentVersion.document_id == document_id,
            )
        )
    ).scalar_one_or_none()
    if clean_version is None or clean_version.status != "accepted":
        raise ProjectChainError("来源清洗版本不可用")

    doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one()
    if doc.active_clean_version_id != clean_version.id:
        raise CleanVersionStaleError("来源清洗版本已不是 active，本次切分作废")
    await ctx.checkpoint()

    # 从冻结 config 构建 splitter（同一 tokenizer）。
    config_json = chunk_set.config_json or {}
    strategy = chunk_set.strategy or config_json.get("strategy", "hybrid_heading_recursive")
    chunker = get_chunker(strategy)
    cfg = ChunkConfig(
        max_tokens=int(config_json.get("max_tokens", 512)),
        overlap_tokens=int(config_json.get("overlap_tokens", 0)),
        options=config_json.get("options"),
    )

    # 标记处理中（pending -> processing）。failed 的 retry 由 T07 后继 Task 的
    # 有效 run token 进入 handler，这里把 failed CAS 回 pending 再处理。
    if chunk_set.status == "pending":
        chunk_set.status = "processing"
    elif chunk_set.status == "failed":
        chunk_set.status = "pending"
        chunk_set.error_message = None
    await db.flush()
    await ctx.checkpoint()

    # 以 active clean version 的 merged_markdown 作为唯一来源切分。
    markdown = clean_version.merged_markdown
    chunk_data_list = chunker.chunk(markdown, "", cfg)
    await ctx.checkpoint()

    # fallback section（保持下游 FK 结构，不伪造批次来源）。若文档无任何 Section
    # （异常态），整个集合失败而不是写入 NULL section_id。
    fallback_section = (
        await db.execute(
            select(Section.id).where(Section.document_id == document_id).order_by(Section.ordinal).limit(1)
        )
    ).scalar_one_or_none()
    if fallback_section is None:
        raise ProjectChainError("文档没有可关联的 Section，无法切分")

    staging: list[Chunk] = []
    total_tokens = 0
    for cdata in chunk_data_list:
        # 批次循环 checkpoint：支持协作取消与 run token 校验。
        if len(staging) % 5 == 0:
            await ctx.checkpoint()
            await ctx.heartbeat()
        # 校验冻结 tokenizer 复算 token_count（不得信任 splitter 输出）。
        token_count = _count_tokens(cdata.content, cfg)
        if token_count > cfg.max_tokens:
            raise ValueError(
                f"chunk 超过 max_tokens 预算: token_count={token_count} "
                f"max_tokens={cfg.max_tokens}"
            )
        chunk = Chunk(
            section_id=fallback_section,
            document_id=document_id,
            chunk_set_id=chunk_set.id,
            ordinal=cdata.ordinal,
            heading_path=cdata.heading_path,
            content=cdata.content,
            source_pages=cdata.source_pages,
            token_count=token_count,
            status="ready",
        )
        db.add(chunk)
        staging.append(chunk)
        total_tokens += token_count

    if not staging:
        raise ValueError("切分结果为空，未产生任何 Chunk")

    # 发布前门禁：校验 run token + cancel；然后原子发布。
    await ctx.checkpoint_before_publish()

    # 发布事务：再次锁定 Document，复核 active clean version 仍为来源。
    doc = (
        await db.execute(
            select(Document).where(Document.id == document_id).with_for_update()
        )
    ).scalar_one()
    if doc.active_clean_version_id != clean_version.id:
        raise CleanVersionStaleError("来源清洗版本已不是 active，本次切分作废")

    # 计算 canonical 输出 hash（按 ordinal 串联）。
    output_hash = canonical_output_sha256(
        [(c.ordinal, c.heading_path, c.content, c.token_count) for c in staging]
    )

    chunk_set.status = "completed"
    chunk_set.completed_at = datetime.now(UTC)
    chunk_set.total_chunks = len(staging)
    chunk_set.total_tokens = total_tokens
    chunk_set.output_sha256 = output_hash
    chunk_set.source_sha256 = clean_version.content_sha256 or chunk_set.source_sha256
    chunk_set.error_message = None

    # 切 active pointer（文档状态：chunked）。返回后由 runner 在完成事务中原子提交。
    doc.active_chunk_set_id = chunk_set.id
    doc.status = "chunked"
    await db.flush()


def _count_tokens(content: str, cfg: ChunkConfig) -> int:
    """用冻结 tokenizer 复算 token_count（worker 写入前校验）。"""
    import tiktoken

    from splitters import TOKENIZER_NAME

    name = (cfg.options or {}).get("tokenizer_name", TOKENIZER_NAME)
    encoder = tiktoken.get_encoding(name)
    return len(encoder.encode(content))


def _make_terminal_hook(chunk_set_id: uuid.UUID):
    """构造绑定具体 ChunkSet 的终态钩子（闭包）。

    runner 在失败/取消回滚后以新会话调用：``hook(db, status, error_message)``。
    用 ``WHERE status IN ('pending','processing','review_pending')`` CAS 收敛，
    绝不覆盖已完成的集合或已发布的 active pointer。
    """

    async def hook(db: AsyncSession, status: str, error_message: str | None) -> None:
        from sqlalchemy import update

        terminal = "failed" if status == "failed" else "cancelled"
        await db.execute(
            update(ChunkSet)
            .where(
                ChunkSet.id == chunk_set_id,
                ChunkSet.status.in_(("pending", "processing", "review_pending")),
            )
            .values(status=terminal, error_message=error_message, completed_at=datetime.now(UTC))
        )

    return hook
