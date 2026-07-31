from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChunkConfig:
    max_tokens: int = 512
    overlap_tokens: int = 50


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
