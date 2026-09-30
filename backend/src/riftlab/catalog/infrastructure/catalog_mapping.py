"""Overrides explícitos para patch → Data Dragon (CC-202/314).

Resoluções automáticas são persistidas em patch_resolutions e combinadas com
estes overrides; nunca há fallback para a versão mais recente de outro patch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from riftlab.shared.errors import SourceSchemaError

# backend/src/riftlab/catalog/infrastructure/catalog_mapping.py -> parents[5]
# é a raiz do repositório (infrastructure -> catalog -> riftlab -> src ->
# backend -> raiz) — mesma técnica de cli/env_file.py. Só funciona fora do
# container (uv run a partir de backend/); dentro do runtime/test o caminho
# de verdade é Settings.catalog_mappings_path (absoluto, copiado pelo
# Dockerfile), pelo mesmo motivo documentado em Settings.raw_storage_dir
# (COPY backend/src ./src remove o nível "backend/" do checkout do host,
# mudando a profundidade relativa dentro da imagem).
DEFAULT_CATALOG_MAPPINGS_PATH = (
    Path(__file__).resolve().parents[5] / "configs" / "catalog-mappings.yaml"
)

_SUPPORTED_SCHEMA_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class MappingEntry:
    data_dragon_version: str


@dataclass(frozen=True, slots=True)
class CatalogMapping:
    """Mapping carregado e validado. mapping_version identifica esta
    configuração inteira — muda só quando a própria regra de mapeamento é
    alterada deliberadamente (não a cada patch novo adicionado à lista, a
    menos que a curadoria decida bump); entra na identidade de conteúdo do
    catálogo persistido (CatalogVersion.sha256)."""

    schema_version: str
    mapping_version: str
    entries: dict[str, MappingEntry]

    def resolve(self, game_patch: str) -> MappingEntry | None:
        """None = patch sem override/resolução no snapshot recebido."""
        return self.entries.get(game_patch)


def parse_catalog_mapping_document(raw_bytes: bytes) -> CatalogMapping:
    """Pura: separa leitura de bytes da validação/conversão para o DTO
    tipado — mesmo padrão de parse_catalog_source_document (CC-104)."""
    try:
        document: Any = yaml.safe_load(raw_bytes)
    except yaml.YAMLError as exc:
        raise SourceSchemaError(
            code="CATALOG_MAPPING_INVALID_YAML", detail="mapping de catálogo não é YAML válido"
        ) from exc

    if not isinstance(document, dict):
        raise SourceSchemaError(
            code="CATALOG_MAPPING_MISSING_FIELD", detail="documento raiz do mapping não é objeto"
        )

    schema_version = document.get("schemaVersion")
    if schema_version != _SUPPORTED_SCHEMA_VERSION:
        raise SourceSchemaError(
            code="SCHEMA_UNSUPPORTED",
            detail=f"schemaVersion não suportada pelo mapping: {schema_version!r}",
        )

    mapping_version = document.get("mappingVersion")
    if not isinstance(mapping_version, str) or not mapping_version:
        raise SourceSchemaError(
            code="CATALOG_MAPPING_MISSING_FIELD", detail="mappingVersion ausente ou vazio"
        )

    raw_mappings = document.get("mappings")
    if not isinstance(raw_mappings, dict) or not raw_mappings:
        raise SourceSchemaError(
            code="CATALOG_MAPPING_MISSING_FIELD", detail="mappings ausente ou vazio"
        )

    entries: dict[str, MappingEntry] = {}
    for game_patch, raw_entry in raw_mappings.items():
        if not isinstance(game_patch, str) or not game_patch:
            raise SourceSchemaError(
                code="CATALOG_MAPPING_MISSING_FIELD", detail="chave de mapping não é string válida"
            )
        if not isinstance(raw_entry, dict):
            raise SourceSchemaError(
                code="CATALOG_MAPPING_MISSING_FIELD",
                detail=f"entrada de mapping para {game_patch!r} não é objeto",
            )
        data_dragon_version = raw_entry.get("dataDragonVersion")
        if not isinstance(data_dragon_version, str) or not data_dragon_version:
            raise SourceSchemaError(
                code="CATALOG_MAPPING_MISSING_FIELD",
                detail=f"dataDragonVersion ausente para {game_patch!r}",
            )
        entries[game_patch] = MappingEntry(data_dragon_version=data_dragon_version)

    return CatalogMapping(
        schema_version=schema_version, mapping_version=mapping_version, entries=entries
    )


def load_catalog_mapping(path: Path) -> CatalogMapping:
    return parse_catalog_mapping_document(path.read_bytes())
