from parsing.base import BaseParser, ParseResult  # noqa: F401
from parsing.mock_parser import MockParser  # noqa: F401
from parsing.mineru_parser import MineruParser  # noqa: F401


def get_parser(parser_name: str) -> BaseParser:
    parsers = {
        "mock": MockParser,
        "mineru": MineruParser,
    }
    parser_cls = parsers.get(parser_name)
    if parser_cls is None:
        raise ValueError(f"Unknown parser: {parser_name}. Available: {list(parsers.keys())}")
    return parser_cls()
