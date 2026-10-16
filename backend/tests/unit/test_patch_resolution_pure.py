from pathlib import Path

import pytest

from riftlab.catalog.infrastructure.catalog_mapping import load_catalog_mapping
from riftlab.catalog.patches import choose_version, parse_versions
from riftlab.decisions.scope import parse_real_scope_document
from riftlab.shared.errors import SourceSchemaError


def test_first_published_version_never_crosses_patch() -> None:
    versions = parse_versions(b'["16.19.2","16.19.1","16.18.1","lolpatch_7.20"]')
    assert choose_version("16.19", versions) == "16.19.1"
    assert choose_version("16.18", versions) == "16.18.1"
    assert choose_version("16.17", versions) is None
    with pytest.raises(SourceSchemaError):
        choose_version("16.19/../16.18", versions)


def test_historical_overrides_remain_exact() -> None:
    root = Path(__file__).resolve().parents[2]
    mapping_file = root / "configs/catalog-mappings.yaml"
    if not mapping_file.is_file():
        mapping_file = root.parent / "configs/catalog-mappings.yaml"
    mapping = load_catalog_mapping(mapping_file)
    assert mapping.resolve("16.16").data_dragon_version == "16.16.1"
    assert mapping.resolve("16.17").data_dragon_version == "16.17.1"


@pytest.mark.parametrize("body", [b"{}", b"[]", b'["16.19.1",3]', b'["16.19.1","16.19.1"]'])
def test_invalid_versions_fail_closed(body: bytes) -> None:
    with pytest.raises(SourceSchemaError):
        parse_versions(body)


def test_rolling_scope_uses_detected_window_and_legacy_stays_valid() -> None:
    body = b"""
schemaVersion: v1
frozenAt: "2026-09-14"
queue: 420
map: 11
role: BOTTOM
championIds: [145]
patchWindow: {mode: rolling, size: 2}
patches: [{gamePatch: "16.16"}, {gamePatch: "16.17"}]
"""
    from riftlab.decisions.domain import DecisionConfigError

    with pytest.raises(DecisionConfigError, match="cc patch detect"):
        parse_real_scope_document(body)
    legacy = parse_real_scope_document(
        body.replace(b"patchWindow: {mode: rolling, size: 2}\n", b"")
    )
    current = parse_real_scope_document(body, ("16.19", "16.18", "16.17"))
    assert legacy.patches == {"16.16", "16.17"}
    assert current.patches == {"16.19", "16.18"}
    assert current.version != legacy.version


_SCOPE_BODY = b"""
schemaVersion: v3
frozenAt: "2026-10-16"
queues: [420, 440]
map: 11
role: BOTTOM
championIds: [145]
patches: [{gamePatch: "16.19"}]
"""


def test_scope_accepts_a_list_of_queues_and_the_legacy_single_queue() -> None:
    both = parse_real_scope_document(_SCOPE_BODY)
    legacy = parse_real_scope_document(_SCOPE_BODY.replace(b"queues: [420, 440]", b"queue: 420"))

    assert both.queues == {420, 440}
    assert legacy.queues == {420}


@pytest.mark.parametrize(
    "queues_line",
    [
        b"queues: []",
        b"queues: [420, 420]",
        b"queues: [420, true]",
        b"queues: 420",
        b"queues: [420]\nqueue: 420",
    ],
)
def test_invalid_queue_lists_fail_closed(queues_line: bytes) -> None:
    from riftlab.decisions.domain import DecisionConfigError

    with pytest.raises(DecisionConfigError):
        parse_real_scope_document(_SCOPE_BODY.replace(b"queues: [420, 440]", queues_line))
