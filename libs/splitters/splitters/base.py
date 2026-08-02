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


class BaseChunker(ABC):
    @abstractmethod
    def chunk(self, markdown: str, heading_path: str, config: ChunkConfig) -> list[ChunkData]:
        pass
