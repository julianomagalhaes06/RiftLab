"""Mapping explícito gameVersion/gamePatch -> Data Dragon (CC-202): nunca
"latest" implícito, patch fora do mapping é MISSING_CATALOG sem rede."""

from __future__ import annotations

from pathlib import Path

import pytest

from riftlab.catalog.infrastructure.catalog_mapping import (
    load_catalog_mapping,
    parse_catalog_mapping_document,
)
from riftlab.config import Settings
from riftlab.shared.errors import SourceSchemaError

_VALID_DOCUMENT = b"""
schemaVersion: v1
mappingVersion: v1
mappings:
  "16.17":
    dataDragonVersion: "16.17.1"
"""


def test_parse_catalog_mapping_document_resolves_known_patch() -> None:
    mapping = parse_catalog_mapping_document(_VALID_DOCUMENT)

    assert mapping.mapping_version == "v1"
    entry = mapping.resolve("16.17")
    assert entry is not None
    assert entry.data_dragon_version == "16.17.1"


def test_parse_catalog_mapping_document_returns_none_for_unmapped_patch() -> None:
    mapping = parse_catalog_mapping_document(_VALID_DOCUMENT)

    assert mapping.resolve("13.1") is None  # MISSING_CATALOG, nunca "latest"


def test_parse_catalog_mapping_document_rejects_invalid_yaml() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        parse_catalog_mapping_document(b"not: valid: yaml: [")
    assert excinfo.value.code == "CATALOG_MAPPING_INVALID_YAML"


def test_parse_catalog_mapping_document_rejects_unknown_schema_version() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        parse_catalog_mapping_document(b"schemaVersion: v99\nmappingVersion: v1\nmappings: {}")
    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"


def test_parse_catalog_mapping_document_rejects_missing_mapping_version() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        parse_catalog_mapping_document(
            b'schemaVersion: v1\nmappings: {"16.17": {"dataDragonVersion": "16.17.1"}}'
        )
    assert excinfo.value.code == "CATALOG_MAPPING_MISSING_FIELD"


def test_parse_catalog_mapping_document_rejects_empty_mappings() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        parse_catalog_mapping_document(b"schemaVersion: v1\nmappingVersion: v1\nmappings: {}")
    assert excinfo.value.code == "CATALOG_MAPPING_MISSING_FIELD"


def test_parse_catalog_mapping_document_rejects_entry_missing_data_dragon_version() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        parse_catalog_mapping_document(
            b'schemaVersion: v1\nmappingVersion: v1\nmappings: {"16.17": {}}'
        )
    assert excinfo.value.code == "CATALOG_MAPPING_MISSING_FIELD"


def test_parse_catalog_mapping_document_rejects_non_object_root() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        parse_catalog_mapping_document(b"- just\n- a\n- list\n")
    assert excinfo.value.code == "CATALOG_MAPPING_MISSING_FIELD"


def _real_repo_mapping_path() -> Path:
    """Mesmo arquivo real que Settings.catalog_mappings_path aponta dentro
    do container (Dockerfile copia configs/catalog-mappings.yaml para lá);
    em host (uv run fora de container) o arquivo vive na raiz do repositório
    real, dois níveis acima de backend/. Testa o arquivo de verdade, não uma
    cópia inline, em qualquer um dos dois ambientes reais e validados deste
    projeto (host e Docker)."""
    container_path = Path(Settings().catalog_mappings_path)
    if container_path.exists():
        return container_path
    host_path = Path(__file__).resolve().parents[3] / "configs" / "catalog-mappings.yaml"
    if host_path.exists():
        return host_path
    pytest.skip("configs/catalog-mappings.yaml não encontrado neste ambiente")


def test_real_repo_catalog_mappings_yaml_covers_the_frozen_scope_patches() -> None:
    """configs/scope.yaml (CC-105) congelou 16.16 e 16.17 — o mapping real
    tem que cobrir exatamente esses dois patches com as versões reais do
    Data Dragon conferidas nesta sessão (16.16.1/16.17.1)."""
    mapping = load_catalog_mapping(_real_repo_mapping_path())

    entry_1616 = mapping.resolve("16.16")
    entry_1617 = mapping.resolve("16.17")
    assert entry_1616 is not None and entry_1616.data_dragon_version == "16.16.1"
    assert entry_1617 is not None and entry_1617.data_dragon_version == "16.17.1"
