"""Catálogo em uso na interface (CC-1202): patch e Data Dragon mais recentes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from riftlab.catalog.patches import latest_catalog
from riftlab.shared.api.common import CamelModel
from riftlab.shared.db import read_only_unit_of_work

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


class CatalogCurrent(CamelModel):
    game_patch: str | None = None
    data_dragon_version: str | None = None


@router.get(
    "/current",
    response_model=CatalogCurrent,
    summary="Patch e versão do Data Dragon mais recentes do catálogo",
    description="Somente banco. Sem catálogo importado, os dois campos vêm nulos.",
)
async def get_catalog_current(request: Request) -> CatalogCurrent:
    async with read_only_unit_of_work(request.app.state.session_factory) as session:
        latest = await latest_catalog(session)
    if latest is None:
        return CatalogCurrent()
    return CatalogCurrent(game_patch=latest[0], data_dragon_version=latest[1])
