"""T06 切分器单元测试：Token 预算、overlap、Unicode 安全与确定性（验收标准 1-3、7）。

覆盖：
- 中文/英文/Markdown 标题/超长无换行文本/多字节字符：所有非空 Chunk 满足
  ``1..max_tokens``（对可拆分文本）。
- 开启 overlap 后仍无超限；``overlap_tokens=0``、``max_tokens=1``、接近边界。
- 同一输入/config/version 连跑两次，输出内容、ordinal、token_count 与 hash 完全一致。
- hard split 字节对齐：不产生 U+FFFD、不生成空 Chunk 或仅 overlap Chunk。
"""

from __future__ import annotations

import pytest
import tiktoken

from splitters.base import ChunkConfig
from splitters.canonical import (
    canonical_output_sha256,
    canonical_source_sha256,
    normalize_markdown,
    splitter_version,
    tokenizer_version,
)
from splitters.hybrid_heading import HybridHeadingRecursiveChunker
from splitters.token_util import clean_suffix, hard_split

CJK_TEXT = ("你好，" * 100) + ("世界" * 100)
ASCII_TEXT = "abcdefghij" * 100
HEADING_MD = ("# 标题\n\n## 子标题1\n\n正文一" * 30) + "\n"
NO_NEWLINE = "这是一个测试段落。" * 200
ENGLISH_PARAGRAPHS = ("\n\n".join(f"Paragraph {i}: " + "word " * 40 for i in range(10)))


@pytest.fixture(scope="module")
def chunker() -> HybridHeadingRecursiveChunker:
    return HybridHeadingRecursiveChunker()


def _records(chunks):
    return [(c.ordinal, c.heading_path, c.content, c.token_count) for c in chunks]


def _hash(chunks) -> str:
    return canonical_output_sha256(_records(chunks))


# ---------------------------------------------------------------------------
# 验收标准 1：所有非空 Chunk 满足 1..max_tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,cfg",
    [
        (CJK_TEXT, ChunkConfig(max_tokens=50, overlap_tokens=10)),
        (ASCII_TEXT, ChunkConfig(max_tokens=30, overlap_tokens=5)),
        (HEADING_MD, ChunkConfig(max_tokens=80, overlap_tokens=15)),
        (NO_NEWLINE, ChunkConfig(max_tokens=100, overlap_tokens=20)),
        (ENGLISH_PARAGRAPHS, ChunkConfig(max_tokens=120, overlap_tokens=25)),
    ],
)
def test_all_chunks_within_budget(chunker, text, cfg):
    chunks = chunker.chunk(text, "根路径", cfg)
    assert len(chunks) > 0
    for c in chunks:
        assert c.token_count >= 1, "不得生成空 Chunk"
        assert c.token_count <= cfg.max_tokens, f"token_count={c.token_count} > max_tokens={cfg.max_tokens}"
        assert c.content.strip(), "不得生成空内容 Chunk"
        assert "�" not in c.content, "不得生成 Unicode 损坏内容"
        assert c.ordinal == len([x for x in chunks if x.ordinal <= c.ordinal]) - 1  # 连续 ordinal
    assert [c.ordinal for c in chunks] == list(range(len(chunks))), "ordinal 必须从 0 连续"


# ---------------------------------------------------------------------------
# 验收标准 2：overlap 后仍无超限；overlap_tokens=0 / max_tokens=1 / 接近边界
# ---------------------------------------------------------------------------


def test_overlap_still_within_budget(chunker):
    for overlap in (0, 5, 20, 50):
        cfg = ChunkConfig(max_tokens=60, overlap_tokens=overlap)
        chunks = chunker.chunk(CJK_TEXT, "", cfg)
        for c in chunks:
            assert 1 <= c.token_count <= cfg.max_tokens
        # 只读第一块：不应含 overlap 标记；后续块可能含。
        if overlap > 0 and len(chunks) > 1:
            assert chunks[1].content.startswith("...")


def test_overlap_zero_produces_no_marker(chunker):
    chunks = chunker.chunk(ENGLISH_PARAGRAPHS, "", ChunkConfig(max_tokens=60, overlap_tokens=0))
    for c in chunks:
        assert "..." not in c.content


def test_max_tokens_one_english(chunker):
    chunks = chunker.chunk(ASCII_TEXT, "", ChunkConfig(max_tokens=1, overlap_tokens=0))
    for c in chunks:
        assert 1 <= c.token_count <= 1
        assert c.content.strip()


def test_max_tokens_one_cjk_raises_rather_than_corrupt(chunker):
    """max_tokens=1 时单个 CJK 字符占 2 个 token，物理上无法在不损坏文本的前提下拆分：
    必须抛错（由 handler 使集合 failed），绝不发布 U+FFFD 内容。"""
    with pytest.raises(ValueError):
        chunker.chunk(CJK_TEXT, "", ChunkConfig(max_tokens=1, overlap_tokens=0))


def test_near_boundary_no_overshoot(chunker):
    word = "token"
    enc = tiktoken.get_encoding("cl100k_base")
    while len(enc.encode(word)) < 100:
        word += "token"
    chunks = chunker.chunk(word, "h", ChunkConfig(max_tokens=100, overlap_tokens=10))
    for c in chunks:
        assert 1 <= c.token_count <= 100
        assert "�" not in c.content


# ---------------------------------------------------------------------------
# 验收标准 3：确定性 - 同输入/config/version 连跑两次 hash 一致
# ---------------------------------------------------------------------------


def test_deterministic_output(chunker):
    cfg = ChunkConfig(max_tokens=80, overlap_tokens=15)
    c1 = chunker.chunk(HEADING_MD, "根路径", cfg)
    c2 = chunker.chunk(HEADING_MD, "根路径", cfg)
    assert _records(c1) == _records(c2)
    assert _hash(c1) == _hash(c2)


def test_splitter_version_is_frozen():
    """splitter_version 必须含算法版本与 tokenizer 名/版本，不得只写策略名。"""
    ver = splitter_version()
    assert ver.startswith("3.0.0@")
    assert "cl100k_base" in ver
    assert tokenizer_version() in ver


def test_canonical_source_hash_normalizes():
    a = canonical_source_sha256("第 1 行   \n\n第 2 行\n")
    b = canonical_source_sha256("第 1 行\n\n第 2 行")
    assert a == b
    assert normalize_markdown("x\r\n\r\ny  \n").endswith("y\n")


# ---------------------------------------------------------------------------
# token_util 字节对齐：不产生 U+FFFD、不产生空片段
# ---------------------------------------------------------------------------


def test_hard_split_no_corruption():
    enc = tiktoken.get_encoding("cl100k_base")
    for budget in (1, 2, 3, 5, 10, 50):
        pieces = hard_split(enc, CJK_TEXT, budget)
        assert pieces, "不得产生空结果"
        joined = "".join(pieces)
        assert "�" not in joined, f"budget={budget} 产生损坏"
        # 内容在去除 overlap 标记后应能重组（chunk 间无字符丢失/重复）。
        assert joined == CJK_TEXT


def test_clean_suffix_boundary():
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(CJK_TEXT)
    text, n = clean_suffix(enc, tokens, 10)
    assert "�" not in text
    assert 0 < n <= 10
    # 全量后缀干净。
    full, n_full = clean_suffix(enc, tokens, len(tokens))
    assert n_full == len(tokens)
    assert "�" not in full


def test_hard_split_ascii_never_exceeds_budget():
    enc = tiktoken.get_encoding("cl100k_base")
    for budget in (1, 2, 5, 10, 30):
        pieces = hard_split(enc, ASCII_TEXT, budget)
        for p in pieces:
            assert 1 <= len(enc.encode(p)) <= budget
