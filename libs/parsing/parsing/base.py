from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass
class ParseResult:
    raw_markdown: str
    structured_json: dict = field(default_factory=dict)
    page_mapping: list[dict] = field(default_factory=list)


class BaseParser(ABC):
    @abstractmethod
    def parse(self, pdf_path: str) -> ParseResult:
        pass
