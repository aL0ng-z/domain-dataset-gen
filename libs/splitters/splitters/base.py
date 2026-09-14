from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChunkConfig:
    max_tokens: int = 512
    overlap_tokens: int = 50
    # 冻结快照：tokenizer 名/版本等，保证拆分与最终校验使用同一计数器。
    options: dict[str, Any] | None = field(default=None)


@dataclass
class ChunkData:
    ordinal: int
    heading_path: str
    content: str
    source_pages: list[int] = field(default_factory=list)
    token_count: int = 0
    # 未加 overlap 前的正文。worker 用它在冻结的 merged_markdown 中定位真实来源区间；
    # 不写入数据库，也不影响输出内容或 canonical hash。
    source_content: str | None = None


class BaseChunker(ABC):
    @abstractmethod
    def chunk(self, markdown: str, heading_path: str, config: ChunkConfig) -> list[ChunkData]:
        pass
