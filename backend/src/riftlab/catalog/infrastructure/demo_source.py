from __future__ import annotations

import hashlib
import json

from riftlab.catalog.domain import CatalogItemSourceDTO, CatalogSourceResult
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import SourceNotFoundError, SourceSchemaError
from riftlab.shared.resources import read_package_bytes, read_package_json

SUPPORTED_SCHEMA_VERSION = "v1"

_FIXTURES_PACKAGE = "riftlab.catalog.infrastructure.fixtures"
_MANIFEST_NAME = "manifest.json"

_REQUIRED_CATALOG_FIELDS = ("gamePatch", "dataDragonVersion", "mappingVersion", "items")
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

    return CatalogSourceResult(
        game_patch=document["gamePatch"],
        origin=SourceOrigin.CURATED_DEMO,
        schema_version=schema_version,
        raw_hash=raw_hash,
        data_dragon_version=document["dataDragonVersion"],
        mapping_version=document["mappingVersion"],
        items=tuple(items),
    )
