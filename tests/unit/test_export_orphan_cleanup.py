from scripts.export_orphan_cleanup import _parse_export_coordinates

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
EXPORT_ID = "22222222-2222-4222-8222-222222222222"
HASH = "a" * 64


def test_parse_export_coordinates_accepts_only_content_addressed_export_objects():
    assert _parse_export_coordinates(
        f"tests/run-1/{PROJECT_ID}/exports/{EXPORT_ID}/payload-{HASH}.json"
    ) is not None
    assert _parse_export_coordinates(
        f"{PROJECT_ID}/exports/{EXPORT_ID}/manifest-{HASH}.json"
    ) is not None


def test_parse_export_coordinates_rejects_ambiguous_or_non_export_keys():
    assert _parse_export_coordinates(
        f"tests/run-1/exports/{EXPORT_ID}/payload-{HASH}.json"
    ) is None
    assert _parse_export_coordinates(
        f"tests/run-1/{PROJECT_ID}/exports/{EXPORT_ID}/payload-latest.json"
    ) is None
    assert _parse_export_coordinates(
        f"tests/run-1/{PROJECT_ID}/exports/{EXPORT_ID}/nested/payload-{HASH}.json"
    ) is None
    assert _parse_export_coordinates(
        f"tests/run-1/{PROJECT_ID}/exports/not-a-uuid/manifest-{HASH}.json"
    ) is None
