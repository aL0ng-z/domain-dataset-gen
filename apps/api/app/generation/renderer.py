"""Prompt renderer：从冻结快照 + Chunk 重建确定性消息（T08 §4、§5.4）。

renderer 版本是稳定标识；同一 snapshot + Chunk 在 worker 与路由中必须重建出
相同的 ``input_prompt`` bytes/hash。renderer 只读 Batch 快照，绝不重新读取
PromptTemplate/ModelConfig 的可变参数。

渲染后 prompt hash 必须对其精确 UTF-8 bytes 计算（``rendered_prompt_sha256``）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: 稳定 renderer 版本标识。修改消息构建逻辑时必须递增并在迁移/快照中体现。
RENDERER_VERSION = "prompt-renderer:v1"


class RendererUnavailableError(Exception):
    """renderer 版本未知或无法确定性重建 prompt。"""


def render_messages(template_snapshot: dict[str, Any], chunk_content: str, chunk_heading_path: str) -> list[dict[str, str]]:
    """从冻结模板快照 + Chunk 内容构建规范化消息列表。

    占位符替换规则（确定性）：
    - ``{{content}}`` -> chunk.content
    - ``{{heading_path}}`` -> chunk.heading_path（无则空串）
    未替换的残留 ``{{...}}`` 视为渲染失败（避免把字面量发给 LLM）。
    """
    system_prompt = str(template_snapshot["system_prompt"])
    user_template = str(template_snapshot["user_prompt_template"])

    user_prompt = user_template.replace("{{content}}", chunk_content)
    user_prompt = user_prompt.replace("{{heading_path}}", chunk_heading_path)

    # 残留占位符：渲染失败（确定性合同，不把字面量当 prompt）。
    import re

    leftover = re.findall(r"\{\{.*?\}\}", user_prompt)
    if leftover:
        raise RendererUnavailableError(f"user_prompt_template 含未替换占位符: {leftover[0]}")

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def render_input_prompt(template_snapshot: dict[str, Any], chunk_content: str, chunk_heading_path: str) -> str:
    """构建 renderer 实际提交的规范化消息 JSON（input_prompt 字段内容）。"""
    messages = render_messages(template_snapshot, chunk_content, chunk_heading_path)
    return json.dumps(messages, ensure_ascii=False, separators=(",", ":"))


def rendered_prompt_sha256(input_prompt: str) -> str:
    """对 input_prompt 的精确 UTF-8 bytes 计算 SHA-256。"""
    return hashlib.sha256(input_prompt.encode("utf-8")).hexdigest()


def rebuild_input_prompt_and_hash(
    template_snapshot: dict[str, Any],
    chunk_content: str,
    chunk_heading_path: str,
) -> tuple[str, str]:
    """重建 input_prompt 与 hash（worker 核验 / retry 复制用）。"""
    input_prompt = render_input_prompt(template_snapshot, chunk_content, chunk_heading_path)
    return input_prompt, rendered_prompt_sha256(input_prompt)
