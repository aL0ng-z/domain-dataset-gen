from splitters.base import BaseChunker, ChunkConfig, ChunkData  # noqa: F401
from splitters.hybrid_heading import HybridHeadingRecursiveChunker  # noqa: F401


def get_chunker(strategy: str) -> BaseChunker:
    chunkers = {
        "hybrid_heading_recursive": HybridHeadingRecursiveChunker,
    }
    chunker_cls = chunkers.get(strategy)
    if chunker_cls is None:
        raise ValueError(f"Unknown chunking strategy: {strategy}. Available: {list(chunkers.keys())}")
    return chunker_cls()
