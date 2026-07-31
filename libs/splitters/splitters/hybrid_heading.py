import re

import tiktoken

from splitters.base import BaseChunker, ChunkConfig, ChunkData


class HybridHeadingRecursiveChunker(BaseChunker):
    def __init__(self):
        self._encoder = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(self, text: str) -> int:
        return len(self._encoder.encode(text))

    def chunk(self, markdown: str, heading_path: str, config: ChunkConfig) -> list[ChunkData]:
        # Step 1: Split by sub-headings (any ## or deeper)
        sub_sections = self._split_by_subheadings(markdown)

        # Step 2: For each sub-section, recursively split if too large
        raw_chunks = []
        for sub_heading, sub_content in sub_sections:
            full_path = re.sub(r"^[ >]+|[ >]+$", "", f"{heading_path} > {sub_heading}") if sub_heading else heading_path
            if self._count_tokens(sub_content) <= config.max_tokens:
                raw_chunks.append((full_path, sub_content))
            else:
                for piece in self._recursive_split(sub_content, config.max_tokens):
                    raw_chunks.append((full_path, piece))

        # Step 3: Apply overlap between consecutive chunks
        chunks = self._apply_overlap(raw_chunks, config)

        return chunks

    def _split_by_subheadings(self, markdown: str) -> list[tuple[str, str]]:
        """Split markdown by ## or deeper headings."""
        pattern = re.compile(r"^(#{2,6}) +(.+)$", re.MULTILINE)
        matches = list(pattern.finditer(markdown))

        if not matches:
            return [("", markdown.strip())]

        result = []
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

    def _recursive_split(self, text: str, max_tokens: int) -> list[str]:
        """Recursively split text by paragraphs, then sentences."""
        if self._count_tokens(text) <= max_tokens:
            return [text]

        # Try splitting by double newlines (paragraphs)
        paragraphs = re.split(r"\n\n+", text)
        if len(paragraphs) > 1:
            return self._merge_splits(paragraphs, max_tokens)

        # Fall back to splitting by single newlines
        lines = text.split("\n")
        if len(lines) > 1:
            return self._merge_splits(lines, max_tokens)

        # Last resort: hard split by tokens
        return self._hard_split(text, max_tokens)

    def _merge_splits(self, pieces: list[str], max_tokens: int) -> list[str]:
        """Merge small pieces into chunks respecting max_tokens."""
        result = []
        current = ""
        for piece in pieces:
            candidate = f"{current}\n\n{piece}".strip() if current else piece
            if self._count_tokens(candidate) <= max_tokens:
                current = candidate
            else:
                if current:
                    result.append(current)
                current = piece
        if current:
            result.append(current)
        return result

    def _hard_split(self, text: str, max_tokens: int) -> list[str]:
        """Hard split by token count as last resort."""
        tokens = self._encoder.encode(text)
        result = []
        for i in range(0, len(tokens), max_tokens):
            chunk_tokens = tokens[i : i + max_tokens]
            result.append(self._encoder.decode(chunk_tokens))
        return result

    def _apply_overlap(self, raw_chunks: list[tuple[str, str]], config: ChunkConfig) -> list[ChunkData]:
        """Create ChunkData objects with token overlap between consecutive chunks."""
        chunks = []
        for i, (path, content) in enumerate(raw_chunks):
            # Add overlap from previous chunk
            if i > 0 and config.overlap_tokens > 0:
                prev_content = raw_chunks[i - 1][1]
                prev_tokens = self._encoder.encode(prev_content)
                overlap_tokens = prev_tokens[-config.overlap_tokens :]
                overlap_text = self._encoder.decode(overlap_tokens)
                content = f"...{overlap_text}\n\n{content}"

            token_count = self._count_tokens(content)

            # Extract page numbers
            page_pattern = re.compile(r"(?:page|Page|PAGE)\s*(\d+)", re.IGNORECASE)
            pages = sorted(set(int(m.group(1)) for m in page_pattern.finditer(content)))

            chunks.append(ChunkData(
                ordinal=i,
                heading_path=path,
                content=content,
                source_pages=pages,
                token_count=token_count,
            ))

        return chunks
