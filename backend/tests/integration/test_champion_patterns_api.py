import hashlib
import json
import uuid
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient
from pipeline_helpers import (
    FAKE_SUBJECT_HASH_SECRET,
    purge_open_preparations,
    real_match_docs,
    reset_match,
    write_prepare_configs,
)
from sqlalchemy import delete

from riftlab.api.app import create_app
from riftlab.catalog.domain import CatalogItemSourceDTO, CatalogSourceResult
from riftlab.catalog.infrastructure.curation import CURATION_RULES_VERSION
from riftlab.catalog.patches import CurrentPatchModel, record_current_patch
from riftlab.collection.importing import drain_local, enqueue_collected_imports
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.shared.db import unit_of_work
from riftlab.shared.domain import SourceOrigin


async def test_real_patterns_are_read_only_scoped_and_abstain_on_stale_versions(
    session_factory, tmp_path
):
    major = 700 + uuid.uuid4().int % 100
    patch = f"{major}.19"
    other_patch = f"{major}.18"
    settings = write_prepare_configs(
        tmp_path, scope_patch=patch, mapped_patches=(patch, other_patch)
    )
    scope_path = tmp_path / "scope.yaml"
    scope_path.write_text(scope_path.read_text().replace("role: UTILITY", "role: BOTTOM"))
    settings = settings.model_copy(update={"subject_hash_secret": FAKE_SUBJECT_HASH_SECRET})
    store = DurableRawMatchStore(tmp_path / "raw")
    ids = [f"BR1_{uuid.uuid4().int % 10**18}" for _ in range(6)]
    async with session_factory() as session:
        previous = await session.get(CurrentPatchModel, "current")
        previous_values = (
            {
                column.name: getattr(previous, column.name)
                for column in CurrentPatchModel.__table__.columns
            }
            if previous
            else None
        )

    class Source:
        async def load_catalog(self, game_patch):
            return CatalogSourceResult(
                game_patch=game_patch,
                origin=SourceOrigin.COMPLETED_MATCH,
                schema_version="test_v1",
                raw_hash=hashlib.sha256(game_patch.encode()).hexdigest(),
                data_dragon_version=f"{game_patch}.1",
                mapping_version="v1",
                curation_rules_version=CURATION_RULES_VERSION,
                items=(
                    CatalogItemSourceDTO(
                        item_id=1038,
                        price=1300,
                        attributes={"name": "Componente", "tags": [], "into": [3031]},
                        flags={"purchasable": True, "maps": [11]},
                    ),
                    CatalogItemSourceDTO(
                        item_id=3031,
                        price=3400,
                        recipe=[{"itemId": 1038, "quantity": 1}],
                        attributes={"name": "Item principal", "tags": [], "into": []},
                        flags={"purchasable": True, "maps": [11]},
                    ),
                ),
            )

    @asynccontextmanager
    async def source_factory(mapping):
        yield Source()

    try:
        await purge_open_preparations(session_factory)
        await record_current_patch(
            session_factory, json.dumps([f"{patch}.1", f"{other_patch}.1"]).encode()
        )
        for index, match_id in enumerate(ids):
            match, timeline = real_match_docs(
                match_id, patch if index < 5 else other_patch, ((120_000, 3031),)
            )
            match["info"]["participants"][0]["teamPosition"] = "BOTTOM"
            for kind, document in (("match", match), ("timeline", timeline)):
                raw = json.dumps(document).encode()
                store.store(match_id, kind, raw, hashlib.sha256(raw).hexdigest())
        await enqueue_collected_imports(session_factory, store, ids)
        await drain_local(session_factory, store, settings, catalog_source_factory=source_factory)
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client,
        ):
            url = f"/api/v1/champions/patterns?championId=89&patch={patch}&phase=first"
            response = await client.get(url)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "AVAILABLE"
            assert body["totalParticipations"] == body["patternDenominator"] == 5
            assert body["patterns"][0]["count"] == body["patterns"][0]["denominator"] == 5
            assert body["patterns"][0]["items"][0]["name"] == "Item principal"
            assert all(entry["matchId"] in ids[:5] for entry in body["situations"])
            assert "subjectHash" not in response.text and "puuid" not in response.text
            assert (await client.get(url.replace("championId=89", "championId=51"))).json()[
                "status"
            ] == "OUT_OF_SCOPE"
            assert (await client.get(url.replace(patch, other_patch))).json()[
                "status"
            ] == "OUT_OF_SCOPE"
            assert (await client.get(url.replace("phase=first", "phase=second"))).json()[
                "status"
            ] == "NO_ELIGIBLE"
            assert (await client.get(url + "&role=TOP")).status_code == 422
            scope_path.write_text(scope_path.read_text().replace("2026-09-19", "2026-09-20"))
            stale = (await client.get(url)).json()
            assert stale["status"] == "NO_ELIGIBLE"
            assert stale["patterns"] == [] and stale["eligibleDecisions"] == 0
    finally:
        for match_id in ids:
            await reset_match(session_factory, match_id)
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(CurrentPatchModel))
            if previous_values:
                session.add(CurrentPatchModel(**previous_values))
