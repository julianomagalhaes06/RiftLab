"""CatalogSource real (CC-202): resolve gamePatch -> versão do Data Dragon
por mapping explícito (nunca "latest"), busca item.json real e devolve o
mesmo DTO (CatalogSourceResult) que a fonte demo — consumidor não distingue
qual adapter recebeu (mesma interface, mesmo padrão de MatchSource/CC-104)."""

from __future__ import annotations

import json

from riftlab.catalog.domain import CatalogSourceResult, validate_recipe_graph
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.catalog.infrastructure.curation import CURATION_RULES_VERSION, curate_data_dragon_items
from riftlab.catalog.infrastructure.data_dragon_transport import DataDragonTransport
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import SourceNotFoundError, SourceSchemaError


class DataDragonCatalogSource:
    """game_patch aqui é major.minor real (ex.: "16.17", mesmo formato de
    matches.patch) — nunca uma versão completa do Data Dragon diretamente;
    a resolução para a versão exata vem só do mapping explícito."""

    def __init__(self, transport: DataDragonTransport, mapping: CatalogMapping) -> None:
        self._transport = transport
        self._mapping = mapping

    async def load_catalog(self, game_patch: str) -> CatalogSourceResult:
        entry = self._mapping.resolve(game_patch)
        if entry is None:
            raise SourceNotFoundError(
                code="MISSING_CATALOG",
                detail=f"game_patch sem entrada no mapping explícito: {game_patch!r}",
            )

        response = await self._transport.fetch_item_catalog(entry.data_dragon_version)
        try:
            document = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise SourceSchemaError(
                code="CATALOG_SOURCE_INVALID_JSON", detail="corpo do Data Dragon não é JSON válido"
            ) from exc
        if not isinstance(document, dict):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD", detail="documento raiz não é objeto JSON"
            )

        items, excluded = curate_data_dragon_items(document.get("data"))

        result = CatalogSourceResult(
            game_patch=game_patch,
            origin=SourceOrigin.COMPLETED_MATCH,
            schema_version="data_dragon_item_json_v1",
            raw_hash=response.sha256,
            data_dragon_version=entry.data_dragon_version,
            mapping_version=self._mapping.mapping_version,
            curation_rules_version=CURATION_RULES_VERSION,
            items=items,
            excluded_items=excluded,
        )
        validate_recipe_graph(result.items)
        return result
