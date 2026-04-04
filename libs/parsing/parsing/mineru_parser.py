from parsing.base import BaseParser, ParseResult


class MineruParser(BaseParser):
    def parse(self, pdf_path: str) -> ParseResult:
        raise NotImplementedError(
            "MinerU parser is not yet integrated. "
            "Please use parser_name='mock' or configure MinerU separately."
        )
