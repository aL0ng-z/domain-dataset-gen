from parsing.base import BaseParser, ParseResult  # noqa: F401
from parsing.mineru_local_parser import MineruLocalParser  # noqa: F401
from parsing.mineru_local_service_parser import MineruLocalServiceParser  # noqa: F401
from parsing.mineru_parser import MineruParser  # noqa: F401
from parsing.paddleocr_local_service_parser import PaddleOCRLocalServiceParser  # noqa: F401
from parsing.paddleocr_parser import PaddleOCRParser  # noqa: F401
from parsing.pymupdf_parser import PymupdfParser  # noqa: F401

#: 可用解析器注册表（schema/worker 白名单依据）。
PARSERS: dict[str, type[BaseParser]] = {
    "pymupdf4llm": PymupdfParser,
    "mineru": MineruParser,
    "mineru_local": MineruLocalParser,
    "mineru_local_service": MineruLocalServiceParser,
    "paddleocr": PaddleOCRParser,
    "paddleocr_local_service": PaddleOCRLocalServiceParser,
    # Backward compat
    "mock": PymupdfParser,
}

#: 当前可用的解析器名（schema/worker 白名单依据）。
AVAILABLE_PARSERS = frozenset(PARSERS.keys())


def get_parser(parser_name: str, options: dict | None = None) -> BaseParser:
    parser_cls = PARSERS.get(parser_name)
    if parser_cls is None:
        raise ValueError(
            "未知的解析器: "
            f"{parser_name}。可选: pymupdf4llm, mineru, mineru_local, mineru_local_service, paddleocr, "
            "paddleocr_local_service"
        )
    return parser_cls(options=options)
