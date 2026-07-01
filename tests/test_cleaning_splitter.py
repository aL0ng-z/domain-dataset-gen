from cleaning import split_into_sections


def test_split_prefers_structured_pages():
    sections = split_into_sections(
        "# Heading that should not split\n\nPage one text\n\n# Another heading",
        structured_json={
            "pages": [
                {"page_number": 1, "markdown": "# Heading that should not split\n\nPage one text"},
                {"page_number": 2, "markdown": "# Another heading\n\nPage two text"},
            ]
        },
    )

    assert [section.heading_path for section in sections] == ["第 1 页", "第 2 页"]
    assert sections[0].source_pages == [1]
    assert sections[1].raw_markdown.endswith("Page two text")


def test_split_uses_markdown_ranges_when_structured_pages_are_absent():
    markdown = "page one\n\npage two"

    sections = split_into_sections(
        markdown,
        page_mapping=[
            {"page_number": 1, "markdown_start": 0, "markdown_end": 8},
            {"page_number": 2, "markdown_start": 10, "markdown_end": len(markdown)},
        ],
    )

    assert [section.raw_markdown for section in sections] == ["page one", "page two"]
    assert [section.source_pages for section in sections] == [[1], [2]]


def test_split_supports_explicit_page_markers():
    markdown = "<!-- Page 1 -->\n\nfirst\n\n<!-- Page 2 -->\n\nsecond"

    sections = split_into_sections(markdown)

    assert [section.heading_path for section in sections] == ["第 1 页", "第 2 页"]
    assert [section.raw_markdown for section in sections] == ["first", "second"]


def test_split_falls_back_to_headings_for_legacy_markdown():
    sections = split_into_sections("# A\n\none\n\n# B\n\ntwo")

    assert [section.heading_path for section in sections] == ["A", "B"]
