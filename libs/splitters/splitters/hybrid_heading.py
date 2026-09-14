"""hybrid_heading_recursive 切分器（T06 §7 Token 预算合同）。

预算规则（与 canonical/config 快照一致）：
- ``ChunkConfig`` 携带可选 ``options``（含 tokenizer 名/版本冻结），splitter 内部
  用与最终校验一致的 tokenizer 复算，保证拆分、overlap 与最终校验使用同一计数器。
- overlap 是总预算的一部分：先确定实际 overlap token 数，再以
  ``max_tokens - overlap_tokens - separator_tokens`` 作为当前正文预算。
- ``_merge_splits`` 遇到单个 piece 超限时继续递归（可能 hard split），绝不直接
  追加超限 piece。
- hard split 后仍须经过 overlap-aware 最终校验；不生成空 Chunk、仅 overlap Chunk
  或 Unicode 损坏内容（tiktoken decode 的完整 token 序列不会产生半个字符）。
"""

from __future__ import annotations

import re
from typing import Any

import tiktoken

from splitters.base import BaseChunker, ChunkConfig, ChunkData
from splitters.canonical import OVERLAP_SEPARATOR, TOKENIZER_NAME, normalize_heading_path
from splitters.token_util import clean_suffix, hard_split


class HybridHeadingRecursiveChunker(BaseChunker):
    def __init__(self, options: dict[str, Any] | None = None):
        """options 可覆盖 tokenizer 名（默认 cl100k_base）。"""
        options = options or {}
        tokenizer_name = options.get("tokenizer_name", TOKENIZER_NAME)
        self._encoder = tiktoken.get_encoding(tokenizer_name)
        self._tokenizer_name = tokenizer_name

    def _count_tokens(self, text: str) -> int:
        return len(self._encoder.encode(text))

    # ------------------------------------------------------------------
    # 公共入口：固定顺序，输出唯一 ordinal。
    # ------------------------------------------------------------------

    def chunk(self, markdown: str, heading_path: str, config: ChunkConfig) -> list[ChunkData]:
        # Step 1: Split by sub-headings (any ## or deeper)
        sub_sections = self._split_by_subheadings(markdown)

        # Step 2: For each sub-section, recursively split if too large.
        # 初始判断也用正文预算（max_tokens - overlap - separator），保证加回
        # overlap 后的最终块不超过 max_tokens。
        body_budget = self._body_budget(config)
        raw_chunks: list[tuple[str, str]] = []
        for sub_heading, sub_content in sub_sections:
            full_path = normalize_heading_path(
                f"{heading_path} > {sub_heading}" if sub_heading else heading_path
            )
            if self._count_tokens(sub_content) <= body_budget:
                raw_chunks.append((full_path, sub_content))
            else:
                for piece in self._recursive_split(sub_content, config):
                    raw_chunks.append((full_path, piece))

        # Step 3: Apply overlap between consecutive chunks（含最终 token 校验）。
        return self._apply_overlap(raw_chunks, config)

    # ------------------------------------------------------------------
    # 分段
    # ------------------------------------------------------------------

    def _split_by_subheadings(self, markdown: str) -> list[tuple[str, str]]:
        """Split markdown by ## or deeper headings."""
        pattern = re.compile(r"^(#{2,6}) +(.+)$", re.MULTILINE)
        matches = list(pattern.finditer(markdown))

        if not matches:
            return [("", markdown.strip())]

        result: list[tuple[str, str]] = []
        # Content before first subheading
        preamble = markdown[: matches[0].start()].strip()
        if preamble:
            result.append(("", preamble))

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
            heading_text = match.group(2).strip()
            content = markdown[start:end].strip()
            result.append((heading_text, content))

        return result

    # ------------------------------------------------------------------
    # 递归拆分（overlap-aware 预算）
    # ------------------------------------------------------------------

    def _recursive_split(self, text: str, config: ChunkConfig) -> list[str]:
        """Recursively split text by paragraphs, then sentences, then hard split."""
        # 预留 overlap + separator 的预算：正文块不得超过 body_budget。
        body_budget = self._body_budget(config)
        if self._count_tokens(text) <= body_budget:
            return [text]

        # Try splitting by double newlines (paragraphs)
        paragraphs = re.split(r"\n\n+", text)
        if len(paragraphs) > 1:
            return self._merge_splits(paragraphs, config)

        # Fall back to splitting by single newlines
        lines = text.split("\n")
        if len(lines) > 1:
            return self._merge_splits(lines, config)

        # Last resort: hard split by tokens（每块不超过 body_budget）。
        return self._hard_split(text, body_budget)

    def _body_budget(self, config: ChunkConfig) -> int:
        """正文预算 = max_tokens - overlap - separator - joiner；至少保留 1。

        - ``separator``：overlap 前缀标记（``...\n\n``）。
        - ``joiner``：overlap 文本与正文之间的空行（``\n\n``）。

        保证最坏情况（完整 overlap + separator + joiner）下最终 Chunk 仍
        ``<= max_tokens``。
        """
        separator_tokens = self._count_tokens(OVERLAP_SEPARATOR)
        joiner_tokens = self._count_tokens("\n\n")
        budget = config.max_tokens - config.overlap_tokens - separator_tokens - joiner_tokens
        return max(1, budget)

    def _merge_splits(self, pieces: list[str], config: ChunkConfig) -> list[str]:
        """Merge small pieces into chunks respecting body budget.

        单个 piece 自身超限时继续递归（可能 hard split），绝不直接追加超限 piece。
        """
        body_budget = self._body_budget(config)
        result: list[str] = []
        current = ""
        for piece in pieces:
            if self._count_tokens(piece) > body_budget:
                # 先把当前已累积的块收尾，再递归拆分超限 piece。
                if current:
                    result.append(current)
                    current = ""
                result.extend(self._recursive_split(piece, config))
                continue
            candidate = f"{current}\n\n{piece}".strip() if current else piece
            if self._count_tokens(candidate) <= body_budget:
                current = candidate
            else:
                if current:
                    result.append(current)
                current = piece
        if current:
            result.append(current)
        return result

    def _hard_split(self, text: str, body_budget: int) -> list[str]:
        """Hard split by token count as last resort.

        使用字节对齐切分（token_util.hard_split），绝不产生半个 Unicode 字符；
        切分点尽量贴近 body_budget，预算不足一个字符时取完整字符。
        """
        return hard_split(self._encoder, text, body_budget)

    # ------------------------------------------------------------------
    # overlap 应用与最终校验
    # ------------------------------------------------------------------

    def _apply_overlap(self, raw_chunks: list[tuple[str, str]], config: ChunkConfig) -> list[ChunkData]:
        """Create ChunkData objects with token overlap between consecutive chunks.

        overlap 是总预算的一部分：``max_tokens - overlap - separator`` 已在上层
        ``_recursive_split`` 预留，因此加回 overlap/separator 后的最终块不会超限。
        仍执行最终校验兜底：任一 ``token_count > max_tokens`` 抛错（由 handler 使
        整个集合失败），不静默截断发布（任务卡 §4.2）。
        """
        chunks: list[ChunkData] = []
        for i, (path, source_content) in enumerate(raw_chunks):
            content = source_content
            if i > 0 and config.overlap_tokens > 0:
                prev_content = raw_chunks[i - 1][1]
                prev_tokens = self._encoder.encode(prev_content)
                # 实际 overlap 用字节对齐后缀（不产生 U+FFFD）；0 则不加 overlap。
                overlap_text, _ = clean_suffix(self._encoder, prev_tokens, config.overlap_tokens)
                if overlap_text:
                    content = f"{OVERLAP_SEPARATOR}{overlap_text}\n\n{content}"

            token_count = self._count_tokens(content)

            # 最终校验（兜底）：overlap/separator/joiner 已在正文预算中预留，
            # 任何 token_count > max_tokens 都使整个集合失败，不静默截断（任务卡 §4.2）。
            if token_count > config.max_tokens:
                raise ValueError(
                    f"chunk 超过 max_tokens 预算: token_count={token_count} "
                    f"max_tokens={config.max_tokens} overlap_tokens={config.overlap_tokens}"
                )

            chunks.append(ChunkData(
                ordinal=i,
                heading_path=path,
                content=content,
                token_count=token_count,
                source_content=source_content,
            ))

        return chunks
