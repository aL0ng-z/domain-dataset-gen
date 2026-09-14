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

from sqlalchemy import select, update
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


def _load_source_intervals(value: object) -> list[dict]:
    """读取清洗版本冻结的、可验证的正文来源区间。"""
    if not isinstance(value, list):
        return []
    intervals: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            section_id = uuid.UUID(str(item["section_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        start, end = item.get("start_char"), item.get("end_char")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end <= start
        ):
            continue
        pages = item.get("source_pages")
        trusted_pages = sorted({
            page for page in pages
            if isinstance(page, int) and not isinstance(page, bool) and page > 0
        }) if isinstance(pages, list) else []
        intervals.append({
            "section_id": section_id,
            "start_char": start,
            "end_char": end,
            "source_pages": trusted_pages,
            "trusted": item.get("page_mapping_status") == "trusted" and bool(trusted_pages),
        })
    return sorted(intervals, key=lambda item: (item["start_char"], item["end_char"]))


def _locate_chunk_source_ranges(markdown: str, chunk_data_list) -> list[tuple[list[tuple[int, int]], list[tuple[int, int]]]]:
    """定位每个 Chunk 正文及其 overlap 的 merged_markdown 字符区间。

    splitter 保留未加 overlap 的 ``source_content``。正文无法在冻结内容中精确定位时，
    不猜测相同文本的其他出现位置，后续将把该 Chunk 标为页码未知。
    """
    cursor = 0
    previous_body_ranges: list[tuple[int, int]] = []
    result: list[tuple[list[tuple[int, int]], list[tuple[int, int]]]] = []
    for cdata in chunk_data_list:
        source_content = cdata.source_content
        if not isinstance(source_content, str):
            source_content = cdata.content
        body_ranges: list[tuple[int, int]] = []
        if source_content:
            start = markdown.find(source_content, cursor)
            if start >= 0:
                end = start + len(source_content)
                body_ranges = [(start, end)]
                cursor = end
        # overlap 的内容来自紧邻的前一段正文，因此携带该正文的来源区间；避免从
        # 生成的 "..." 前缀或正文中的页码字样反推来源。
        ranges = [*previous_body_ranges, *body_ranges] if cdata.content != source_content else body_ranges
        result.append((body_ranges, ranges))
        previous_body_ranges = body_ranges
    return result


def _resolve_source_provenance(
    ranges: list[tuple[int, int]], source_intervals: list[dict]
) -> tuple[uuid.UUID | None, list[int]]:
    """按相交的冻结区间选首个 Section，并合并可信物理页码。"""
    if not ranges:
        return None, []
    selected = [
        interval
        for interval in source_intervals
        if any(start < interval["end_char"] and end > interval["start_char"] for start, end in ranges)
    ]
    if not selected:
        return None, []
    section_id = selected[0]["section_id"]
    # Chunk 只要有一部分来源页码无法确认，就不能把部分集合伪装成完整页码来源。
    if any(not interval["trusted"] for interval in selected):
        return section_id, []
    return section_id, sorted({page for interval in selected for page in interval["source_pages"]})


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

    # 仅接受清洗版本冻结的来源区间。正常新链路中每个 Chunk 均会映射到其首个实际
    # Section；旧/异常快照无法确认来源时保留空页码，并使用同一清洗任务的首个
    # Section 满足既有非空外键约束。
    section_query = select(Section.id).where(Section.document_id == document_id)
    if clean_version.source_cleaning_job_id is not None:
        section_query = section_query.where(Section.cleaning_job_id == clean_version.source_cleaning_job_id)
    available_sections = list((await db.execute(section_query.order_by(Section.ordinal))).scalars().all())
    fallback_section = available_sections[0] if available_sections else None
    if fallback_section is None:
        raise ProjectChainError("文档没有可关联的 Section，无法切分")
    valid_section_ids = set(available_sections)
    source_intervals = [
        interval for interval in _load_source_intervals(clean_version.source_intervals)
        if interval["section_id"] in valid_section_ids
    ]
    chunk_source_ranges = _locate_chunk_source_ranges(markdown, chunk_data_list)

    staging: list[Chunk] = []
    total_tokens = 0
    for cdata, (body_ranges, all_ranges) in zip(chunk_data_list, chunk_source_ranges, strict=True):
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
        # overlap 位于输出开头时也属于 Chunk 的真实来源，因此关联整个输出范围中的首个
        # Section，而不是统一关联文档的第一条 Section。
        section_id, _ = _resolve_source_provenance(all_ranges, source_intervals)
        if not body_ranges:
            # 当前正文不能精确落到冻结文本中时，overlap 的已知来源不能替代它。
            source_pages = []
        else:
            _, source_pages = _resolve_source_provenance(all_ranges, source_intervals)
        chunk = Chunk(
            section_id=section_id or fallback_section,
            document_id=document_id,
            chunk_set_id=chunk_set.id,
            ordinal=cdata.ordinal,
            heading_path=cdata.heading_path,
            content=cdata.content,
            source_pages=source_pages,
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
    # 标量读取不会复用 identity map 中切分开始时的旧 Document。
    active_clean_version_id = (
        await db.execute(
            select(Document.active_clean_version_id)
            .where(Document.id == document_id)
            .with_for_update()
        )
    ).scalar_one()
    if active_clean_version_id != clean_version.id:
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
    published = await db.execute(
        update(Document)
        .where(Document.id == document_id, Document.active_clean_version_id == clean_version.id)
        .values(active_chunk_set_id=chunk_set.id, status="chunked")
    )
    if published.rowcount != 1:
        raise CleanVersionStaleError("来源清洗版本已不是 active，本次切分作废")
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
