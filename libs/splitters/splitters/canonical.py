"""切分溯源：splitter/tokenizer 版本标识与 canonical hash（T06 §1、§7）。

- ``SPLITTER_VERSION``：切分算法/代码版本（不是策略名），改动拆分逻辑必须递增。
- ``TOKENIZER_NAME`` / ``tokenizer_version()``：tiktoken 编码名与库版本，冻结进
  ``config_json``，防止 tokenizer 升级改变计数后历史 hash 无法复算。
- ``canonical_source_sha256``：对规范化后的 merged_markdown 计算；normalize 规则
  （去除每行行尾空白、统一行尾、去除首尾空行）必须是稳定幂等的。
- ``canonical_output_sha256``：按 ordinal 串联 canonical Chunk 记录后计算，
  保证同一输入/配置/版本产生完全相同的输出 hash。

所有函数同步执行、不依赖数据库，便于 handler 与测试复用。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import re

# 算法版本：修改拆分/overlap 逻辑时必须递增（任务卡 §7：同一输入/config/version
# 必须产生相同顺序、内容、token_count 与 output_sha256）。
SPLITTER_VERSION = "2.0.0"

# 默认 tokenizer：tiktoken cl100k_base。生产与测试必须使用同一编码名与库版本，
# 否则计数漂移会使迁移预检失败（任务卡 §12 停止条件）。
TOKENIZER_NAME = "cl100k_base"

# overlap 前缀标记：当前实现用 "..." 引导被裁剪的上一块尾部，再接空行与正文。
OVERLAP_SEPARATOR = "...\n\n"


def tokenizer_version() -> str:
    """tiktoken 库版本（冻结进 config_json，tokenizer 升级会改变计数）。"""
    try:
        return importlib.metadata.version("tiktoken")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def splitter_version() -> str:
    """完整 splitter 版本标识：算法版本 + tokenizer 名/版本，禁止只写策略名。"""
    return f"{SPLITTER_VERSION}@{TOKENIZER_NAME}:{tokenizer_version()}"


def normalize_markdown(content: str) -> str:
    """规范化 merged_markdown：统一行尾、去除每行行尾空白、去除首尾空行。

    幂等且确定性；splitter 与 source_sha256 必须消费同一规范化文本。
    """
    lines = []
    for line in content.splitlines():
        stripped = line.rstrip()
        if stripped:
            lines.append(stripped)
    return "\n".join(lines) + "\n"


def canonical_source_sha256(content: str) -> str:
    """对规范化后的 merged_markdown 计算 source hash。"""
    return hashlib.sha256(normalize_markdown(content).encode("utf-8")).hexdigest()


def _canonical_chunk_line(ordinal: int, heading_path: str, content: str, token_count: int) -> str:
    """单个 Chunk 的 canonical 记录（确定性串联的原子行）。"""
    # content 本身可能含换行；用长度前缀 + 内容字段避免歧义。
    return f"{ordinal}\n{token_count}\n{heading_path}\n{len(content)}\n{content}"


def canonical_output_sha256(
    chunks: list[tuple[int, str, str, int]],
) -> str:
    """按 ordinal 串联 canonical Chunk 记录计算输出 hash。

    ``chunks``：``[(ordinal, heading_path, content, token_count), ...]``，
    必须已按 ordinal 升序传入（handler 负责保证唯一连续 ordinal）。
    """
    digest = hashlib.sha256()
    for ordinal, heading_path, content, token_count in chunks:
        digest.update(_canonical_chunk_line(ordinal, heading_path, content, token_count).encode("utf-8"))
    return digest.hexdigest()


def normalize_heading_path(path: str) -> str:
    """规范化 heading 路径：去除首尾分隔符与空白（与 splitter 内部一致）。"""
    return re.sub(r"^[ >]+|[ >]+$", "", path).strip()
