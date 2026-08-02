"""tiktoken 字节对齐切分（T06 §7 防 Unicode 损坏）。

cl100k_base 对 CJK 等字符使用字节碎片 token（一个字符可能跨 2 个 token），
直接 ``encoder.decode(tokens[i:j])`` 在字符边界中间截断会产生 U+FFFD
（replacement character），违反“不生成 Unicode 损坏内容”的合同。

本模块用 ``decode_single_token_bytes`` 做字节级对齐：
- 只有完整 UTF-8 字节序列（字符边界）才允许作为切分点/重叠起点；
- 切分点预算内取最远干净边界；预算小于单个字符所需 token 时取完整字符
  （可能超出预算，由 handler 的最终 token 校验兜底，绝不以损坏文本发布）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tiktoken import Encoding


def _bytes_are_valid_utf8(data: bytes) -> bool:
    """字节序列是否为完整合法 UTF-8（不含截断/非法序列）。"""
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def token_bytes(encoder: Encoding, tokens: list[int]) -> list[bytes]:
    """每个 token 的原始字节片段（确定性）。"""
    return [encoder.decode_single_token_bytes(t) for t in tokens]


def clean_suffix(encoder: Encoding, tokens: list[int], max_count: int) -> tuple[str, int]:
    """从 tokens 尾部取至多 max_count 个 token，返回（干净文本, 实际 token 数）。

    从后往前寻找最长后缀（token 数 <= max_count）且以完整 UTF-8 字符边界开始；
    找不到时逐 token 移除直至干净或为空。返回文本无 U+FFFD。
    """
    if max_count <= 0 or not tokens:
        return "", 0
    end = len(tokens)
    best_start: int | None = None
    for n in range(min(max_count, end), 0, -1):
        s = end - n
        if _bytes_are_valid_utf8(b"".join(token_bytes(encoder, tokens[s:end]))):
            best_start = s
            break
    if best_start is None:
        # 极小概率：单个 token 也是不完整字节（孤立 continuation）。
        for s in range(end - 1, -1, -1):
            if _bytes_are_valid_utf8(b"".join(token_bytes(encoder, tokens[s:end]))):
                best_start = s
                break
    if best_start is None:
        return "", 0
    return encoder.decode(tokens[best_start:end]), end - best_start


def hard_split(encoder: Encoding, text: str, budget: int) -> list[str]:
    """按 token 预算硬切分文本，每个片段是完整合法 UTF-8。

    - 切分点取预算内最远干净字符边界，尽量接近预算；
    - 预算小于单个字符所需 token 数时取完整字符（允许超出预算，由上层
      最终校验决定集合成败，绝不产生损坏文本或空片段）。
    """
    if budget <= 0 or not text:
        return []
    pieces = token_bytes(encoder, encoder.encode(text))
    n = len(pieces)
    result: list[str] = []
    start = 0
    while start < n:
        # 预算内最远的干净边界。
        end = start
        for j in range(start, min(n, start + budget)):
            if _bytes_are_valid_utf8(b"".join(pieces[start : j + 1])):
                end = j + 1
            else:
                break
        if end == start:
            # 预算小于最小原子单元：取完整字符（可能超预算）。
            end = start + 1
            while end < n and not _bytes_are_valid_utf8(b"".join(pieces[start:end])):
                end += 1
        result.append(b"".join(pieces[start:end]).decode("utf-8"))
        start = end
    return result
