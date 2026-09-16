from __future__ import annotations

import hashlib
import json

from riftlab.catalog.domain import (
    CatalogItemSourceDTO,
    CatalogSourceResult,
    validate_recipe_graph,
)
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping, MappingEntry
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import SourceNotFoundError, SourceSchemaError
from riftlab.shared.resources import read_package_bytes, read_package_json

SUPPORTED_SCHEMA_VERSION = "v1"

_FIXTURES_PACKAGE = "riftlab.catalog.infrastructure.fixtures"
_MANIFEST_NAME = "manifest.json"

_REQUIRED_CATALOG_FIELDS = (
    "gamePatch",
    "dataDragonVersion",
    "mappingVersion",
    "curationRulesVersion",
    "items",
)
_REQUIRED_ITEM_FIELDS = ("itemId", "price")


class DemoCatalogSource:
    """CatalogSource cujo conteúdo vem inteiramente de fixtures sintéticas
    empacotadas com o backend, sem rede. Só serve game_patch presentes no
    manifesto fechado."""

    def __init__(self) -> None:
        manifest = read_package_json(_FIXTURES_PACKAGE, _MANIFEST_NAME)
        self._catalogs: dict[str, str] = {
            game_patch: entry["file"] for game_patch, entry in manifest["catalogs"].items()
        }

    async def load_catalog(self, game_patch: str) -> CatalogSourceResult:
        relative_path = self._catalogs.get(game_patch)
        if relative_path is None:
            raise SourceNotFoundError(
                code="CATALOG_SOURCE_NOT_FOUND",
                detail=f"game_patch demo desconhecido: {game_patch!r}",
            )
        raw_bytes = read_package_bytes(_FIXTURES_PACKAGE, relative_path)
        return parse_catalog_source_document(raw_bytes)


def demo_catalog_mapping() -> CatalogMapping:
    """CatalogMapping derivado diretamente das fixtures demo (nunca um
    literal solto duplicado por quem precisa resolver catálogo demo para
    uma partida — ex.: timeline.application.reconstruct_match): o mapping
    YAML real (configs/catalog-mappings.yaml) só cobre patches reais, nunca
    "DEMO-1". Catálogos deliberadamente inválidos do manifesto (ex.:
    DEMO-2-INVALID) são ignorados aqui, nunca entram no mapping."""
    manifest = read_package_json(_FIXTURES_PACKAGE, _MANIFEST_NAME)
    entries: dict[str, MappingEntry] = {}
    mapping_version = "v1"
    for game_patch, entry in manifest["catalogs"].items():
        raw_bytes = read_package_bytes(_FIXTURES_PACKAGE, entry["file"])
        try:
            result = parse_catalog_source_document(raw_bytes)
        except SourceSchemaError:
            continue
        entries[game_patch] = MappingEntry(data_dragon_version=result.data_dragon_version)
        mapping_version = result.mapping_version
    return CatalogMapping(schema_version="v1", mapping_version=mapping_version, entries=entries)


def parse_catalog_source_document(raw_bytes: bytes) -> CatalogSourceResult:
    """Pura: separa leitura/validação do JSON bruto da conversão para DTOs."""
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()

    try:
        document = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise SourceSchemaError(
            code="CATALOG_SOURCE_INVALID_JSON", detail="corpo da fonte não é JSON válido"
        ) from exc

    schema_version = document.get("schemaVersion")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise SourceSchemaError(
            code="SCHEMA_UNSUPPORTED",
            detail=f"schemaVersion não suportada pela fonte demo: {schema_version!r}",
        )

    missing = sorted(field for field in _REQUIRED_CATALOG_FIELDS if field not in document)
    if missing:
        raise SourceSchemaError(
            code="CATALOG_SOURCE_MISSING_FIELD",
            detail=f"campo estrutural obrigatório ausente: {', '.join(missing)}",
        )

    items = []
    for raw_item in document["items"]:
        item_missing = sorted(f for f in _REQUIRED_ITEM_FIELDS if f not in raw_item)
        if item_missing:
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item do catálogo sem campo obrigatório: {', '.join(item_missing)}",
            )
        items.append(
            CatalogItemSourceDTO(
                item_id=raw_item["itemId"],
                price=raw_item["price"],
                recipe=list(raw_item.get("recipe", [])),
                attributes=dict(raw_item.get("attributes", {})),
                flags=dict(raw_item.get("flags", {})),
            )
        )

    result = CatalogSourceResult(
        game_patch=document["gamePatch"],
        origin=SourceOrigin.CURATED_DEMO,
        schema_version=schema_version,
        raw_hash=raw_hash,
        data_dragon_version=document["dataDragonVersion"],
        mapping_version=document["mappingVersion"],
        curation_rules_version=document["curationRulesVersion"],
        items=tuple(items),
    )
    validate_recipe_graph(result.items)
    return result
