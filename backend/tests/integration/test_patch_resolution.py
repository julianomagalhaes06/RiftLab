import asyncio
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update

from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping, MappingEntry
from riftlab.catalog.patches import (
    PatchResolutionModel,
    current_patch_window,
    load_persisted_mapping,
    record_current_patch,
    resolve_patch,
)
from riftlab.collection.filters import FilterReason, MatchFilterConfig, evaluate_match_document
from riftlab.decisions.scope import load_real_scope
from riftlab.shared.db import read_only_unit_of_work, unit_of_work


@pytest.mark.asyncio
async def test_resolution_is_idempotent_under_concurrency_and_first_write_wins(
    session_factory,
) -> None:
    patch = f"{uuid.uuid4().int % 80 + 20}.91"
    overrides = CatalogMapping("v1", "v1", {"16.16": MappingEntry("16.16.1")})
    body = f'["{patch}.2","{patch}.1","16.16.1"]'.encode()
    first, second = await asyncio.gather(
        resolve_patch(session_factory, overrides, patch, body),
        resolve_patch(session_factory, overrides, patch, body),
    )
    assert first.resolve(patch).data_dragon_version == f"{patch}.1"
    assert second.resolve(patch).data_dragon_version == f"{patch}.1"
    changed = f'["{patch}.3","{patch}.2"]'.encode()
    again = await resolve_patch(session_factory, overrides, patch, changed)
    assert again.resolve(patch).data_dragon_version == f"{patch}.1"
    assert again.resolve("16.16").data_dragon_version == "16.16.1"
    async with read_only_unit_of_work(session_factory) as session:
        rows = (
            await session.scalars(
                select(PatchResolutionModel).where(PatchResolutionModel.game_patch == patch)
            )
        ).all()
        assert len(rows) == 1 and rows[0].rule_version == "first-published-v1"
        assert len(rows[0].versions_sha256) == 64
    with pytest.raises(Exception, match="immutable"):
        async with unit_of_work(session_factory) as session:
            await session.execute(
                update(PatchResolutionModel)
                .where(PatchResolutionModel.game_patch == patch)
                .values(data_dragon_version=f"{patch}.3")
            )
    async with unit_of_work(session_factory) as session:
        await session.execute(
            delete(PatchResolutionModel).where(PatchResolutionModel.game_patch == patch)
        )


@pytest.mark.asyncio
async def test_detected_patch_is_recorded_without_any_match_collection(session_factory) -> None:
    # O teste precisa começar sem uma resolução criada por outra prova na mesma DB.
    # Detectar a versão vigente não deve criar essa linha de catálogo.
    async with unit_of_work(session_factory) as session:
        await session.execute(
            delete(PatchResolutionModel).where(PatchResolutionModel.game_patch == "16.19")
        )
    assert (
        await record_current_patch(session_factory, b'["16.19.1","16.18.1","16.17.1"]') == "16.19"
    )
    mapping = await load_persisted_mapping(session_factory, CatalogMapping("v1", "v1", {}))
    assert mapping.resolve("16.19") is None  # detecção não sincroniza catálogo
    from riftlab.catalog.patches import CurrentPatchModel

    async with read_only_unit_of_work(session_factory) as session:
        window = await current_patch_window(session)
    root = Path(__file__).resolve().parents[2]
    scope_file = root / "configs/scope.yaml"
    if not scope_file.is_file():
        scope_file = root.parent / "configs/scope.yaml"
    scope = load_real_scope(scope_file, window)
    assert scope.patches == {"16.19", "16.18"}
    assert "16.19,16.18" in scope.version
    filters = MatchFilterConfig(scope.queues, scope.map_id, scope.patches, scope.version)

    def match_body(patch: str) -> bytes:
        return json.dumps(
            {
                "info": {
                    "queueId": 420,
                    "mapId": 11,
                    "endOfGameResult": "GameComplete",
                    "gameVersion": f"{patch}.123.456",
                },
                "metadata": {"participants": ["synthetic-seed"]},
            }
        ).encode()

    assert evaluate_match_document(match_body("16.19"), filters, "synthetic-seed").accepted
    assert (
        evaluate_match_document(match_body("16.17"), filters, "synthetic-seed").reason
        == FilterReason.PATCH_OUT_OF_SCOPE
    )

    async with unit_of_work(session_factory) as session:
        await session.execute(
            delete(CurrentPatchModel).where(CurrentPatchModel.singleton == "current")
        )
