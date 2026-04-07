from parsing.base import BaseParser, ParseResult  # noqa: F401
from parsing.pymupdf_parser import PymupdfParser  # noqa: F401
from parsing.mineru_parser import MineruParser  # noqa: F401
from parsing.paddleocr_parser import PaddleOCRParser  # noqa: F401


def get_parser(parser_name: str, options: dict | None = None) -> BaseParser:
    parsers = {
        "pymupdf4llm": PymupdfParser,
        "mineru": MineruParser,
        "paddleocr": PaddleOCRParser,
        # Backward compat
        "mock": PymupdfParser,
    }
    parser_cls = parsers.get(parser_name)
    if parser_cls is None:
        raise ValueError(f"未知的解析器: {parser_name}。可选: pymupdf4llm, mineru, paddleocr")
    return parser_cls(options=options)
