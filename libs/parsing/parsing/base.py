from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass
class ParseResult:
    raw_markdown: str
    structured_json: dict = field(default_factory=dict)
    page_mapping: list[dict] = field(default_factory=list)


class BaseParser(ABC):
    def __init__(self, options: dict | None = None):
        self.options = options or {}

    @abstractmethod
    def parse(self, pdf_data: bytes) -> ParseResult:
        """Parse PDF bytes and return structured result."""
        pass
